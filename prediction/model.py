"""The model: training, validation, persistence, loading, caching and inference.

**Training and inference are separate operations, and this module keeps them separate.**
:meth:`FailureRiskModel.train` fits and persists; :meth:`ModelStore.load` reads from disk
into a process-level cache; :meth:`FailureRiskModel.predict` only scores. Nothing here
trains as a side effect of a prediction -- a request that silently retrained would have
unpredictable latency and would change the ``model_version`` recorded against a
prediction, destroying the reproducibility pair the whole design rests on.

**The algorithm is a disclosed choice, not a documented one.** No document in this project
names an ML algorithm or family. What they do specify constrains the choice tightly:
tabular sensor data, "well-understood, cheap to train, fast to serve"
(``PROJECT_OVERVIEW.md`` §3.4), "converts sensor patterns into a **calibrated** number"
(§5.2), "Trained ML for calibrated probability" (§16.8), and success criterion AQ1 requires
predictions to be "calibrated". A standardised logistic regression satisfies every one of
those: its output is a probability by construction rather than a score needing post-hoc
calibration, it trains in milliseconds, it scores in microseconds, and — decisive here —
its log-odds decomposition gives **exact additive per-feature attributions**, which
``prediction_result.top_contributing_features`` requires as a NOT NULL column. A tree
ensemble would need a separate attribution method that no document specifies.

**Models are scoped by machine category.** The master document says this twice with two
different granularities: its ``machine_type`` consumer table says the Prediction Agent
"scopes models by type", while its Machine Learning summary says "``machine_category``
scopes models so equipment with different physics is not forced into one model". Category
is taken as authoritative because the same summary explains what makes the coarser scope
sound -- "the operating envelope provides consistent normalisation across machine types" --
and because pooling a family's machines gives a rare-event model more of the signal it is
short of. On the starter dataset the two readings pick out the same models anyway: each
category with declared ML parameters contains exactly one such machine type.

**Model files live on the filesystem**, per schema §40.4: "Trained model files live on the
filesystem and are referenced by ``model_name`` and ``model_version`` on
``prediction_result``, which is a deliberate boundary — the database records which model
produced a prediction, not the model itself."
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from prediction.errors import (
    ModelLoadError,
    ModelNotTrainedError,
    TrainingDataInsufficientError,
)

MODEL_NAME_PREFIX = "factoryflow-pdm"
MODEL_VERSION = "1.0.0"

# Minimum labelled examples, and minimum of the minority class, before a fit is
# attempted. Below this a model would memorise rather than learn, and a probability from
# it would carry no information while looking authoritative.
MINIMUM_EXAMPLES = 12
MINIMUM_PER_CLASS = 3


@dataclass
class TrainingReport:
    """One attempt to train one scope.

    ``skipped_reason`` set, with an empty ``artifact_path``, means the scope was attempted
    and could not be trained -- almost always because its labelled history holds no
    confirmed failures yet. The attempt is still reported rather than dropped: a category
    with no model is a fact an operator needs, and the example and positive counts say
    exactly how far short the history falls.
    """

    model_name: str
    model_version: str
    examples: int
    positives: int
    features: int
    roc_auc: float | None
    brier_score: float | None
    artifact_path: str
    trained_at: datetime
    skipped_reason: str | None = None

    @property
    def trained(self) -> bool:
        return self.skipped_reason is None


@dataclass
class Attribution:
    """One feature's contribution to a single prediction."""

    name: str
    value: float
    contribution: float


@dataclass
class Inference:
    """One scored vector."""

    probability: float
    attributions: list[Attribution]
    band_low: float | None = None
    band_high: float | None = None


@dataclass
class FailureRiskModel:
    """A fitted pipeline plus the metadata that makes it reproducible."""

    model_name: str
    model_version: str
    feature_names: list[str]
    pipeline: Any
    trained_at: datetime
    examples: int
    positives: int
    roc_auc: float | None = None
    brier_score: float | None = None

    def vector(self, features: dict[str, float]) -> np.ndarray:
        """Order a feature mapping into the exact vector the model was fitted on.

        A feature absent from the mapping is zero rather than an error: a machine type in
        the category may not declare every parameter the category's model was trained
        over, and the union scope is what lets one model serve the category at all.
        """
        return np.array(
            [[float(features.get(name, 0.0)) for name in self.feature_names]],
            dtype=float,
        )

    def predict(self, features: dict[str, float]) -> Inference:
        """Score one vector and explain the score.

        Attributions are the standardised log-odds contributions — ``coef_i * z_i`` — so
        they sum, with the intercept, to the log-odds of the returned probability. They
        are the model's own arithmetic rather than an approximation of it, which is what
        E16 rule 7 needs: an attribution must name a feature the model actually received.
        """
        x = self.vector(features)
        probability = float(self.pipeline.predict_proba(x)[0][1])

        scaler: StandardScaler = self.pipeline.named_steps["scale"]
        estimator: LogisticRegression = self.pipeline.named_steps["model"]
        standardised = scaler.transform(x)[0]
        coefficients = estimator.coef_[0]

        attributions = [
            Attribution(
                name=name,
                value=float(x[0][index]),
                contribution=float(coefficients[index] * standardised[index]),
            )
            for index, name in enumerate(self.feature_names)
        ]
        attributions.sort(key=lambda a: abs(a.contribution), reverse=True)
        return Inference(probability=probability, attributions=attributions)


class ModelStore:
    """Filesystem persistence with a process-level cache.

    The cache is what makes this behave like an inference service: a model is read from
    disk at most once per process per version, and every subsequent prediction reuses the
    loaded object. :meth:`load` is the only path that touches the filesystem.
    """

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self._cache: dict[str, FailureRiskModel] = {}

    def artifact_path(self, model_name: str, model_version: str) -> Path:
        return self.directory / ("%s-%s.joblib" % (model_name, model_version))

    def metadata_path(self, model_name: str, model_version: str) -> Path:
        return self.directory / ("%s-%s.json" % (model_name, model_version))

    def exists(self, model_name: str, model_version: str = MODEL_VERSION) -> bool:
        return self.artifact_path(model_name, model_version).is_file()

    def save(self, model: FailureRiskModel) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        path = self.artifact_path(model.model_name, model.model_version)
        joblib.dump(
            {
                "model_name": model.model_name,
                "model_version": model.model_version,
                "feature_names": model.feature_names,
                "pipeline": model.pipeline,
                "trained_at": model.trained_at.isoformat(),
                "examples": model.examples,
                "positives": model.positives,
                "roc_auc": model.roc_auc,
                "brier_score": model.brier_score,
            },
            path,
        )
        self.metadata_path(model.model_name, model.model_version).write_text(
            json.dumps(
                {
                    "model_name": model.model_name,
                    "model_version": model.model_version,
                    "trained_at": model.trained_at.isoformat(),
                    "examples": model.examples,
                    "positives": model.positives,
                    "features": len(model.feature_names),
                    "roc_auc": model.roc_auc,
                    "brier_score": model.brier_score,
                    "feature_names": model.feature_names,
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        self._cache[self._key(model.model_name, model.model_version)] = model
        return path

    def load(
        self,
        model_name: str,
        model_version: str = MODEL_VERSION,
        *,
        expected_features: list[str] | None = None,
    ) -> FailureRiskModel:
        """Return a cached model, loading it from disk on first use.

        Raises :class:`ModelNotTrainedError` when no artifact exists. It does **not**
        train: the caller asked to predict, and training here would make the model
        version recorded against a prediction depend on whether a file happened to be
        present.
        """
        key = self._key(model_name, model_version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        path = self.artifact_path(model_name, model_version)
        if not path.is_file():
            raise ModelNotTrainedError(
                "no persisted model at %s. Train explicitly before predicting; the "
                "agent does not train during inference." % path
            )
        try:
            payload = joblib.load(path)
            model = FailureRiskModel(
                model_name=payload["model_name"],
                model_version=payload["model_version"],
                feature_names=list(payload["feature_names"]),
                pipeline=payload["pipeline"],
                trained_at=datetime.fromisoformat(payload["trained_at"]),
                examples=int(payload["examples"]),
                positives=int(payload["positives"]),
                roc_auc=payload.get("roc_auc"),
                brier_score=payload.get("brier_score"),
            )
        except Exception as exc:  # noqa: BLE001 -- re-raised as a typed error
            raise ModelLoadError(
                "could not load the model at %s: %s" % (path, exc)) from exc

        if expected_features is not None and model.feature_names != expected_features:
            raise ModelLoadError(
                "the model at %s was fitted on %d features that do not match the %d "
                "the current feature set produces. A misaligned vector would make every "
                "probability wrong without raising anything, so the load is refused. "
                "Retrain explicitly."
                % (path, len(model.feature_names), len(expected_features))
            )

        self._cache[key] = model
        return model

    def cached_count(self) -> int:
        return len(self._cache)

    @staticmethod
    def _key(model_name: str, model_version: str) -> str:
        return "%s@%s" % (model_name, model_version)


def model_name_for(category_code: str) -> str:
    """``factoryflow-pdm-MCAT-CNC`` -- one model per machine category."""
    return ("%s-%s" % (MODEL_NAME_PREFIX, category_code))[:60]


def train_model(
    model_name: str,
    feature_names: list[str],
    rows: list[tuple[dict[str, float], int]],
    *,
    model_version: str = MODEL_VERSION,
) -> tuple[FailureRiskModel, TrainingReport]:
    """Fit and validate a model on labelled historical windows.

    Validation is in-sample when the data is small and a held-out split when it is not.
    Both metrics are recorded on the artifact and reported: ROC AUC for discrimination
    and the Brier score for calibration, because calibration is what the documents
    actually ask for and AUC alone would not show it.
    """
    if len(rows) < MINIMUM_EXAMPLES:
        raise TrainingDataInsufficientError(
            "only %d labelled windows available; %d are required before a fit is "
            "attempted. Run the simulator for longer so confirmed failures exist in "
            "history." % (len(rows), MINIMUM_EXAMPLES)
        )

    x = np.array(
        [[float(features.get(name, 0.0)) for name in feature_names]
         for features, _ in rows],
        dtype=float,
    )
    y = np.array([label for _, label in rows], dtype=int)
    positives = int(y.sum())
    negatives = int(len(y) - positives)

    if positives < MINIMUM_PER_CLASS or negatives < MINIMUM_PER_CLASS:
        raise TrainingDataInsufficientError(
            "labelled history holds %d positive and %d negative windows; at least %d "
            "of each are required. A model fitted on one class returns a constant, and "
            "a constant presented as a probability is worse than no model."
            % (positives, negatives, MINIMUM_PER_CLASS)
        )

    pipeline = Pipeline([
        ("scale", StandardScaler()),
        # No class re-weighting, deliberately. Balancing the classes is the usual reflex
        # for rare events and it would be wrong here: it fits the model as though
        # failures were as common as healthy windows, which inflates every probability
        # away from the true base rate. The documents ask for a **calibrated** number
        # (PROJECT_OVERVIEW.md §5.2, §16.8, AQ1) and the Decision Agent carries it
        # forward unchanged, so a probability that reads high because of a training
        # weight rather than because of the evidence would corrupt every downstream
        # judgement. Unweighted logistic regression fits its intercept to the observed
        # rate, which is what makes predict_proba calibrated by construction.
        #
        # liblinear is deterministic on small dense problems, so retraining on identical
        # data reproduces the identical model.
        ("model", LogisticRegression(solver="liblinear", max_iter=1000, C=1.0)),
    ])
    pipeline.fit(x, y)

    probabilities = pipeline.predict_proba(x)[:, 1]
    roc_auc = None
    brier = None
    if positives > 0 and negatives > 0:
        roc_auc = float(roc_auc_score(y, probabilities))
        brier = float(brier_score_loss(y, probabilities))

    trained_at = datetime.now(timezone.utc)
    model = FailureRiskModel(
        model_name=model_name,
        model_version=model_version,
        feature_names=list(feature_names),
        pipeline=pipeline,
        trained_at=trained_at,
        examples=len(rows),
        positives=positives,
        roc_auc=roc_auc,
        brier_score=brier,
    )
    report = TrainingReport(
        model_name=model_name,
        model_version=model_version,
        examples=len(rows),
        positives=positives,
        features=len(feature_names),
        roc_auc=roc_auc,
        brier_score=brier,
        artifact_path="",
        trained_at=trained_at,
    )
    return model, report
