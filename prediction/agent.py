"""The Prediction Agent. Two separate operations: train, and predict.

Owns two tables (§6.2): ``prediction_feature_snapshot`` and ``prediction_result``, both
insert-only and immutable. It writes nothing else -- no events, no context, no
recommendations, no notifications.

**T-PRED-1** is the only write boundary: insert one snapshot and, if it is sufficient,
one prediction, in one transaction. Atomic because
``prediction_result.prediction_feature_snapshot_id`` is NOT NULL and, more importantly,
because "the reproducibility contract requires the pair to be inseparable — a result whose
snapshot was rolled back could never be reproduced or audited" (§46.3). **An insufficient
snapshot commits alone**, deliberately: the row records why no prediction was produced,
which is what makes the platform's silence explicable.

**Training never happens during a prediction.** :meth:`PredictionAgent.train` is an
explicit call. :meth:`PredictionAgent.run_cycle` loads a persisted model and raises if
none exists rather than fitting one, because a prediction whose model version depended on
whether a file happened to be present would not be reproducible.

**The training label is a disclosed construction, not a documented one.** §E11 names
``confirmed_failure_category_id`` and ``closed_at`` as where supervised learning gets its
ground truth, and §E16 rule 10 scores accuracy by comparing the predicted category against
the confirmed one — but no document defines how a labelled example is formed. The
construction used here: a window is **positive** when a ``maintenance_work_record`` for the
same machine, with ``work_type`` in (``corrective``, ``emergency``), ``work_status =
'closed'`` and a non-NULL ``confirmed_failure_category_id``, was **opened** within the
horizon following the window. ``closed_at`` and ``confirmed_failure_category_id`` gate that
the failure is confirmed rather than merely suspected; ``opened_at`` locates it in time,
because that is when the machine actually failed. Both columns §E11 names are therefore
used, for the two different things they each establish.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from models.master import Machine
from models.operational import (
    MachineSensorReading,
    MaintenanceWorkRecord,
    OperationalAlert,
    PredictionFeatureSnapshot,
    PredictionResult,
)
from models.session import initialize_database, session_scope, shutdown_database

from prediction.context import (
    FEATURE_SET_VERSION,
    PREDICTION_COMPONENT,
    PredictionContext,
    RiskBand,
)
from prediction.errors import (
    ModelNotTrainedError,
    OperationalDataUnavailableError,
    TrainingDataInsufficientError,
)
from prediction.features import ExtractedWindow, FeatureExtractor
from prediction.model import (
    MODEL_VERSION,
    FailureRiskModel,
    ModelStore,
    TrainingReport,
    model_name_for,
    train_model,
)

# Default lookback. The document's worked example uses a four-hour window and stores the
# length explicitly on every snapshot so a vector stays interpretable.
DEFAULT_LOOKBACK_SECONDS = 14400

# Spacing between training windows when walking history. Closer spacing would produce
# near-duplicate vectors that inflate the example count without adding information.
TRAINING_STRIDE_SECONDS = 1800

WORK_TYPES_CONFIRMING_FAILURE = ("corrective", "emergency")


@dataclass
class CycleReport:
    """What one prediction cycle produced."""

    snapshots: int = 0
    sufficient: int = 0
    insufficient: int = 0
    predictions: int = 0
    by_insufficiency: dict[str, int] = field(default_factory=dict)
    codes: list[str] = field(default_factory=list)
    models_loaded: int = 0
    skipped_no_model: int = 0
    untrained_categories: list[str] = field(default_factory=list)
    """Categories with no persisted model. Their machines are not snapshotted at all:
    an insufficiency reason describes inadequate *data*, and using one to mean "the
    platform has no model for this equipment family" would misreport a configuration
    gap as a sensor problem. The gap is surfaced here instead."""


class PredictionAgent:
    """Featurises monitored machines, scores them, and records both."""

    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[Session],
        *,
        model_directory: str | Path,
        completeness_minimum: float | None = None,
        risk_bands: list[RiskBand] | None = None,
        lookback_seconds: int = DEFAULT_LOOKBACK_SECONDS,
        quiet: bool = False,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.lookback_seconds = lookback_seconds
        self.quiet = quiet
        self.store = ModelStore(model_directory)
        self._grouped: dict[str, list[Machine]] | None = None

        with session_scope(session_factory) as session:
            self._require_operational_data(session)
            self.context = PredictionContext(
                session,
                completeness_minimum=completeness_minimum,
                risk_bands=risk_bands,
            )
        self.extractor = FeatureExtractor(self.context)

    @staticmethod
    def _require_operational_data(session: Session) -> None:
        readings = session.execute(
            select(func.count()).select_from(MachineSensorReading)).scalar_one()
        if readings == 0:
            raise OperationalDataUnavailableError(
                "machine_sensor_reading is empty; there is no history to featurise or "
                "train on. Run the Factory Simulation Engine first."
            )

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    # ------------------------------------------------------------------ training

    def train(self, *, force: bool = False) -> list[TrainingReport]:
        """Fit and persist one model per machine category. An explicit operation.

        With ``force`` unset, a category that already has a persisted artifact is left
        alone -- so calling this at startup trains only what is missing. ``force`` is the
        explicit retraining path.
        """
        reports: list[TrainingReport] = []
        by_category = self._machines_by_category()
        if not by_category:
            raise TrainingDataInsufficientError(
                "no machine is eligible for scoring, so no model can be trained. "
                "Eligibility requires is_monitored, in_service, a category with "
                "requires_condition_monitoring, and at least one is_ml_feature "
                "parameter declared for the machine's type."
            )

        already = 0
        for category_code, machines in sorted(by_category.items()):
            name = model_name_for(category_code)
            if not force and self.store.exists(name, MODEL_VERSION):
                already += 1
                self.say("  %-34s already trained; left alone" % name)
                continue

            type_ids = sorted({m.machine_type_id for m in machines})
            feature_names = FeatureExtractor.feature_names(self.context, type_ids)
            rows = self._labelled_windows(machines)
            positives = sum(label for _, label in rows)
            try:
                model, report = train_model(
                    name, feature_names, rows, model_version=MODEL_VERSION)
            except TrainingDataInsufficientError as exc:
                # One untrainable scope must not cost the scopes that can be trained.
                # A reliable machine family with no confirmed failure in history simply
                # has no supervised signal yet, and that is a fact to report, not an
                # abort: refusing to train the bottleneck machining centre because a
                # conveyor has never broken would be the wrong trade.
                reports.append(TrainingReport(
                    model_name=name, model_version=MODEL_VERSION, examples=len(rows),
                    positives=positives, features=len(feature_names), roc_auc=None,
                    brier_score=None, artifact_path="",
                    trained_at=datetime.now(timezone.utc), skipped_reason=str(exc),
                ))
                self.say("  %-34s not trained — %s" % (name, exc))
                continue

            path = self.store.save(model)
            report.artifact_path = str(path)
            reports.append(report)
            self.say(
                "  %-34s %d windows (%d positive), %d features, auc %s, brier %s"
                % (name, report.examples, report.positives, report.features,
                   "n/a" if report.roc_auc is None else "%.3f" % report.roc_auc,
                   "n/a" if report.brier_score is None else "%.4f" % report.brier_score)
            )

        if already == 0 and not any(report.trained for report in reports):
            raise TrainingDataInsufficientError(
                "no model could be trained for any of the %d eligible machine "
                "categories. Every scope lacked labelled history: a window is positive "
                "only when a closed corrective or emergency maintenance_work_record "
                "with a confirmed_failure_category_id follows it inside the horizon, and "
                "no scope had enough of both classes. Simulate a longer period so "
                "confirmed failures exist in history." % len(by_category)
            )
        return reports

    def _machines_by_category(self) -> dict[str, list[Machine]]:
        """Eligible machines grouped by the category whose model serves them.

        Computed once: the master snapshot is immutable for the agent's lifetime, and
        both training and every cycle need the same grouping.
        """
        if self._grouped is None:
            grouped: dict[str, list[Machine]] = {}
            for machine in self.context.master.scored_machines():
                category = self.context.master.category_of(machine)
                if category is None:
                    continue
                grouped.setdefault(category.machine_category_code, []).append(machine)
            self._grouped = grouped
        return self._grouped

    def _labelled_windows(
        self,
        machines: list[Machine],
    ) -> list[tuple[dict[str, float], int]]:
        """Walk each machine's telemetry history, building labelled feature windows.

        Only windows the sufficiency gates accept become training examples: a vector the
        agent would refuse to score at inference time must not be one it learned from,
        or training and serving would see different distributions.
        """
        rows: list[tuple[dict[str, float], int]] = []
        with session_scope(self.session_factory) as session:
            failures = self._confirmed_failures(session)
            for machine in machines:
                horizon = self._horizon_for(machine)
                observed = failures.get(machine.machine_id, [])
                for window in self.extractor.walk(
                    session, machine,
                    lookback_seconds=self.lookback_seconds,
                    stride_seconds=TRAINING_STRIDE_SECONDS,
                ):
                    if not window.is_sufficient:
                        continue
                    rows.append((
                        window.ordered_features,
                        self._label_for(observed, window.generated_at, horizon),
                    ))
        return rows

    @staticmethod
    def _confirmed_failures(session: Session) -> dict[int, list[datetime]]:
        """When each machine actually failed, as confirmed by a closed repair.

        The ground truth §E11 describes: a closed corrective or emergency job whose
        ``confirmed_failure_category_id`` records what the engineer actually found.
        """
        found: dict[int, list[datetime]] = {}
        for machine_id, opened_at in session.execute(
            select(MaintenanceWorkRecord.machine_id, MaintenanceWorkRecord.opened_at)
            .where(
                MaintenanceWorkRecord.work_status == "closed",
                MaintenanceWorkRecord.work_type.in_(WORK_TYPES_CONFIRMING_FAILURE),
                MaintenanceWorkRecord.confirmed_failure_category_id.is_not(None),
            )
        ).all():
            found.setdefault(machine_id, []).append(opened_at)
        return found

    @staticmethod
    def _label_for(
        failure_times: list[datetime],
        window_to: datetime,
        horizon_hours: int,
    ) -> int:
        limit = window_to + timedelta(hours=horizon_hours)
        return int(any(window_to < t <= limit for t in failure_times))

    def _horizon_for(self, machine: Machine) -> int:
        """The machine's default horizon, used when no failure mode is attributed.

        E16 rule 5 binds the horizon to the referenced mode's
        ``typical_warning_period_hours``. With no mode referenced there is no such period,
        so the **shortest** among the machine's declared predictable modes is used: it is
        the only horizon that stays inside rule 5 whichever mode turns out to be
        developing. It is also the horizon the training label is built against, so a
        positive window means "a confirmed failure followed within the shortest warning
        period this machine's physics allows" -- one definition per machine, applied
        identically at training and at serving time.
        """
        modes = self.context.master.predictable_by_type.get(
            machine.machine_type_id, [])
        periods = [
            int(m.typical_warning_period_hours) for m in modes
            if m.typical_warning_period_hours is not None
        ]
        return min(periods) if periods else 24

    # ---------------------------------------------------------------- inference

    def run_cycle(self, *, generated_at: datetime | None = None) -> CycleReport:
        """Featurise and score every eligible machine. One T-PRED-1 per machine.

        Loads each category's model once; subsequent machines in the category reuse the
        cached object, so a cycle over eight machines reads at most one file per category.
        """
        report = CycleReport()
        moment = generated_at or self._reference_instant()
        by_category = self._machines_by_category()

        # Every model the cycle needs is loaded before anything is written. Two reasons:
        # a missing artifact must fail the cycle before a snapshot is committed, and the
        # store reads each file at most once per process, so a cycle over any number of
        # machines costs one load per category on the first cycle and none after.
        models: dict[str, FailureRiskModel] = {}
        for code in sorted(by_category):
            if self.store.exists(model_name_for(code), MODEL_VERSION):
                models[code] = self._load_model(code)
            else:
                report.untrained_categories.append(code)
                report.skipped_no_model += len(by_category[code])
        if not models:
            raise ModelNotTrainedError(
                "no persisted model exists for any of the %d eligible machine "
                "categories (%s), so nothing can be scored. Train explicitly first: "
                "the agent does not train during inference."
                % (len(by_category), ", ".join(sorted(by_category)))
            )

        for category_code, model in sorted(models.items()):
            for machine in by_category[category_code]:
                with session_scope(self.session_factory) as session:
                    window = self.extractor.extract(
                        session, machine,
                        generated_at=moment,
                        lookback_seconds=self.lookback_seconds,
                    )
                    alert_id = self._open_alert_for(session, machine.machine_id)
                    snapshot = self._write_snapshot(session, window, alert_id)
                    report.snapshots += 1
                    report.codes.append(snapshot.prediction_feature_snapshot_code)

                    if not window.is_sufficient:
                        report.insufficient += 1
                        reason = window.insufficiency_reason or "unknown"
                        report.by_insufficiency[reason] = (
                            report.by_insufficiency.get(reason, 0) + 1)
                        continue

                    report.sufficient += 1
                    self._write_prediction(
                        session, machine, snapshot, window, model, alert_id)
                    report.predictions += 1

        report.models_loaded = self.store.cached_count()
        return report

    def _load_model(self, category_code: str) -> FailureRiskModel:
        """The persisted model for a category, from the store's cache after first use.

        ``expected_features`` is passed so a model fitted on a different feature set is
        refused rather than silently fed a misaligned vector.
        """
        machines = self._machines_by_category().get(category_code, [])
        type_ids = sorted({m.machine_type_id for m in machines})
        expected = FeatureExtractor.feature_names(self.context, type_ids)
        return self.store.load(
            model_name_for(category_code), MODEL_VERSION, expected_features=expected)

    def _write_snapshot(
        self,
        session: Session,
        window: ExtractedWindow,
        alert_id: int | None,
    ) -> PredictionFeatureSnapshot:
        snapshot = PredictionFeatureSnapshot(
            prediction_feature_snapshot_code=self.context.snapshot_code(
                window.generated_at),
            machine_id=window.machine_id,
            generated_at=window.generated_at,
            window_from=window.window_from,
            window_to=window.generated_at,
            lookback_window_seconds=window.lookback_seconds,
            feature_set_version=FEATURE_SET_VERSION,
            feature_values=window.feature_values,
            source_reading_count=window.source_reading_count,
            excluded_reading_count=window.excluded_reading_count,
            data_completeness_pct=window.data_completeness_pct,
            is_sufficient_for_inference=window.is_sufficient,
            insufficiency_reason=window.insufficiency_reason,
            triggering_alert_id=alert_id,
            shift_id=self.context.master.shift_at(window.generated_at).shift_id,
            created_by_component=PREDICTION_COMPONENT,
        )
        session.add(snapshot)
        session.flush()
        return snapshot

    def _write_prediction(
        self,
        session: Session,
        machine: Machine,
        snapshot: PredictionFeatureSnapshot,
        window: ExtractedWindow,
        model: FailureRiskModel,
        alert_id: int | None,
    ) -> PredictionResult:
        started = time.perf_counter()
        inference = model.predict(window.ordered_features)
        elapsed_ms = int((time.perf_counter() - started) * 1000)

        band = self.context.severity_for(inference.probability)
        mode = self._attribute_mode(machine, window)

        # E16 rule 5 binds the horizon to the **referenced** mode: "a prediction horizon
        # should fall inside" its typical_warning_period_hours. When a mode is named, that
        # mode's own period is the horizon -- a bearing failure with a week of warning is
        # a week-scale forecast, and clamping it to the shortest mode the type declares
        # would understate a real forecast. With no mode named there is no referenced
        # period, so the shortest declared period is used: it is the only horizon that
        # stays inside rule 5 whichever mode turns out to be developing.
        horizon = self._horizon_for(machine)
        if mode is not None and mode.typical_warning_period_hours is not None:
            horizon = int(mode.typical_warning_period_hours)

        # The snapshot's instant, not wall-clock time: the agent scores recorded history,
        # and a predicted_at in the real present against a window in simulated history
        # would resolve to an unrelated shift and break the timeline the platform reads.
        predicted_at = snapshot.generated_at
        top = [
            {
                "feature": attribution.name,
                "value": round(attribution.value, 4),
                "contribution": round(attribution.contribution, 4),
            }
            for attribution in inference.attributions[:6]
        ]

        result = PredictionResult(
            prediction_result_code=self.context.prediction_code(predicted_at),
            prediction_feature_snapshot_id=snapshot.prediction_feature_snapshot_id,
            machine_id=machine.machine_id,
            predicted_at=predicted_at,
            model_name=model.model_name,
            model_version=model.model_version,
            failure_probability=Decimal("%.4f" % inference.probability),
            risk_severity_level_id=band.severity_level_id,
            predicted_failure_category_id=(
                mode.failure_category_id if mode is not None else None),
            machine_type_failure_mode_id=(
                mode.machine_type_failure_mode_id if mode is not None else None),
            prediction_horizon_hours=max(horizon, 1),
            confidence_band_low=None,
            confidence_band_high=None,
            top_contributing_features=top,
            triggering_alert_id=alert_id,
            inference_duration_ms=max(elapsed_ms, 0),
            shift_id=self.context.master.shift_at(predicted_at).shift_id,
            created_by_component=PREDICTION_COMPONENT,
        )
        session.add(result)
        return result

    def _attribute_mode(
        self,
        machine: Machine,
        window: ExtractedWindow,
    ) -> object | None:
        """Which declared-predictable mode the risk is attributed to, if any.

        Only modes declared for this machine's type with ``is_model_predictable = 1`` are
        eligible (E16 rule 4) -- the model may not predict a failure nothing precedes.
        Among those, the mode chosen is the one whose ``primary_machine_parameter_id``
        shows the strongest **signature**, and a signature means the parameter has
        actually left the envelope master data declares healthy for it: it is above
        ``machine_type_parameter.normal_max``, or it has spent time above its
        ``alert_threshold_rule`` warning limit. Both are already in the snapshot.

        **When nothing has left its envelope the mode and category are NULL**, whatever
        the probability. §E16's own example does exactly this at 0.11: "the model reports
        low risk without attributing a mode, which is the honest output when no signature
        is present. Forcing a category at low probability would manufacture false
        specificity." A parameter drifting upward inside its healthy range is not yet a
        signature of anything, so it does not earn a named mechanism -- the elevated
        probability is reported on its own, which is the whole point of the probability
        and the category being separate columns.
        """
        modes = self.context.master.predictable_by_type.get(
            machine.machine_type_id, [])
        if not modes:
            return None

        best = None
        best_score = 0.0
        for mode in modes:
            parameter_id = mode.primary_machine_parameter_id
            if parameter_id is None:
                continue
            parameter = self.context.master.parameters.get(parameter_id)
            if parameter is None:
                continue
            code = parameter.machine_parameter_code
            # Both terms are zero on a healthy parameter, so a zero score means no
            # signature and the loop leaves `best` as None.
            score = max(
                window.ordered_features.get("%s.pct_above_normal_max" % code, 0.0),
                window.ordered_features.get(
                    "%s.seconds_above_warning_limit" % code, 0.0) / 60.0,
            )
            if score > best_score:
                best_score = score
                best = mode
        return best

    @staticmethod
    def _open_alert_for(session: Session, machine_id: int) -> int | None:
        """A live alert on this machine, if one prompted this scoring.

        Recorded on both rows so an off-schedule inference is traceable to the condition
        that prompted it. NULL for routine scheduled generation.
        """
        return session.execute(
            select(OperationalAlert.operational_alert_id).where(
                OperationalAlert.machine_id == machine_id,
                OperationalAlert.alert_status.in_(
                    ["open", "acknowledged", "escalated"]),
            ).order_by(OperationalAlert.opened_at.desc()).limit(1)
        ).scalar_one_or_none()

    def _reference_instant(self) -> datetime:
        """The newest operational timestamp, not wall-clock time.

        The agent analyses recorded history. Featurising a window ending now would score
        every machine on an empty window the moment the simulator stops.
        """
        with session_scope(self.session_factory) as session:
            newest = session.execute(
                select(func.max(MachineSensorReading.recorded_at))).scalar_one_or_none()
        if newest is None:
            raise OperationalDataUnavailableError(
                "no telemetry timestamp available to anchor the prediction cycle")
        return newest

    def summary(self) -> dict[str, int]:
        with session_scope(self.session_factory) as session:
            return {
                "prediction_feature_snapshot": int(session.execute(
                    select(func.count()).select_from(PredictionFeatureSnapshot)
                ).scalar_one()),
                "prediction_result": int(session.execute(
                    select(func.count()).select_from(PredictionResult)
                ).scalar_one()),
            }


def default_model_directory(database_path: str | Path) -> Path:
    """Where model artifacts live: beside the database file.

    Schema §40.4 puts trained model files on the filesystem, referenced from
    ``prediction_result`` by name and version. Keeping them next to the database keeps a
    deployment's data and its models together.
    """
    return Path(database_path).resolve().parent / "prediction_models"


def train(
    database_path: str | Path,
    *,
    force: bool = False,
    model_directory: str | Path | None = None,
    completeness_minimum: float | None = None,
    risk_bands: list[RiskBand] | None = None,
    quiet: bool = False,
) -> list[TrainingReport]:
    """Explicit training entry point."""
    engine, session_factory = initialize_database(database_path)
    try:
        agent = PredictionAgent(
            engine, session_factory,
            model_directory=model_directory or default_model_directory(database_path),
            completeness_minimum=completeness_minimum,
            risk_bands=risk_bands,
            quiet=quiet,
        )
        agent.say("Prediction Agent — training")
        reports = agent.train(force=force)
        trained = [report for report in reports if report.trained]
        skipped = [report for report in reports if not report.trained]
        agent.say("")
        agent.say("Trained %d model(s); %d scope(s) had no trainable history."
                  % (len(trained), len(skipped)))
        for report in trained:
            agent.say("  %s" % report.artifact_path)
        agent.say("")
        agent.say("Training Complete.")
        return reports
    finally:
        shutdown_database(engine)


def predict(
    database_path: str | Path,
    *,
    cycles: int = 1,
    model_directory: str | Path | None = None,
    completeness_minimum: float | None = None,
    risk_bands: list[RiskBand] | None = None,
    quiet: bool = False,
) -> list[CycleReport]:
    """Inference entry point. Never trains."""
    engine, session_factory = initialize_database(database_path)
    try:
        agent = PredictionAgent(
            engine, session_factory,
            model_directory=model_directory or default_model_directory(database_path),
            completeness_minimum=completeness_minimum,
            risk_bands=risk_bands,
            quiet=quiet,
        )
        agent.say("Prediction Agent — inference")
        reports: list[CycleReport] = []
        for index in range(max(cycles, 1)):
            report = agent.run_cycle()
            reports.append(report)
            agent.say(
                "Cycle %d: %d snapshots (%d sufficient, %d insufficient), "
                "%d predictions, %d model(s) cached"
                % (index + 1, report.snapshots, report.sufficient,
                   report.insufficient, report.predictions, report.models_loaded)
            )
            for reason, count in sorted(report.by_insufficiency.items()):
                agent.say("  %-32s %d" % (reason, count))
            if report.untrained_categories:
                agent.say(
                    "  %-32s %d machine(s) in %s"
                    % ("not scored, no model", report.skipped_no_model,
                       ", ".join(report.untrained_categories))
                )

        totals = agent.summary()
        agent.say("")
        agent.say("Prediction output:")
        for name in ("prediction_feature_snapshot", "prediction_result"):
            agent.say("  %-32s %d" % (name, totals[name]))
        agent.say("")
        agent.say("Prediction Complete.")
        return reports
    finally:
        shutdown_database(engine)


def main(argv: list[str] | None = None) -> int:
    """``python -m prediction <database-path> train|predict [--force|cycles]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) < 2:
        print(
            "usage: python -m prediction <database-path> train [--force]\n"
            "       python -m prediction <database-path> predict [cycles]",
            file=sys.stderr,
        )
        return 2
    path = Path(args[0]).resolve()
    command = args[1].lower()
    try:
        if command == "train":
            train(path, force="--force" in args[2:])
        elif command == "predict":
            count = int(args[2]) if len(args) > 2 else 1
            predict(path, cycles=count)
        else:
            print("unknown command %r; expected train or predict" % command,
                  file=sys.stderr)
            return 2
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        print("", file=sys.stderr)
        print("Prediction failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0
