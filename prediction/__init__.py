"""The Prediction Agent (Phase 5).

Transition T1 of the platform's pipeline: readings become features, features become a
probability. Nothing further. The agent produces no recommendation, sends no
notification, and takes no decision -- those are later phases, and the separation is
structural rather than conventional: ``ai_recommendation`` has no column that could hold
a probability, so the ML confidence figure can only ever originate here (§O18).

**It owns exactly two tables**, both insert-only:

``prediction_feature_snapshot``
    The exact vector fed to the model, plus an honest statement of its own adequacy. A
    snapshot is written even when the data is inadequate, carrying
    ``is_sufficient_for_inference = 0`` and a reason -- that row is what makes the
    platform's silence explicable rather than mysterious.

``prediction_result``
    One inference: probability, risk severity, attributed failure mode, horizon, and the
    features that drove it, tied to the snapshot it scored and the model that scored it.

**Training and inference are separate operations.**

.. code-block:: text

    train(db)             fits one model per machine category and writes it to disk
    predict(db)           loads persisted models and scores; never fits
    train(db, force=True) the explicit retraining path

``predict`` raises :class:`ModelNotTrainedError` when no artifact exists rather than
training one. A prediction whose ``model_version`` depended on whether a file happened to
be present would not be reproducible, and reproducibility is the entire point of storing
the snapshot alongside the result.

Usage::

    python -m prediction factoryflow.db train
    python -m prediction factoryflow.db predict
    python -m prediction factoryflow.db train --force
"""

from prediction.agent import (
    DEFAULT_LOOKBACK_SECONDS,
    CycleReport,
    PredictionAgent,
    default_model_directory,
    main,
    predict,
    train,
)
from prediction.context import (
    FEATURE_SET_VERSION,
    MACHINE_FEATURES,
    PARAMETER_FEATURES,
    PREDICTION_COMPONENT,
    RULE_COMPLETENESS_MIN,
    RULE_RISK_PREFIX,
    MasterSnapshot,
    PredictionContext,
    RiskBand,
)
from prediction.errors import (
    FeatureExtractionError,
    MasterDataUnavailableError,
    ModelLoadError,
    ModelNotTrainedError,
    OperationalDataUnavailableError,
    PredictionError,
    TrainingDataInsufficientError,
)
from prediction.features import ExtractedWindow, FeatureExtractor
from prediction.model import (
    MODEL_VERSION,
    Attribution,
    FailureRiskModel,
    Inference,
    ModelStore,
    TrainingReport,
    model_name_for,
    train_model,
)

__all__ = [
    # entry points
    "train",
    "predict",
    "main",
    "PredictionAgent",
    "default_model_directory",
    # results
    "CycleReport",
    "TrainingReport",
    "Inference",
    "Attribution",
    "ExtractedWindow",
    # model lifecycle
    "FailureRiskModel",
    "ModelStore",
    "train_model",
    "model_name_for",
    "MODEL_VERSION",
    # features and context
    "FeatureExtractor",
    "PredictionContext",
    "MasterSnapshot",
    "RiskBand",
    "FEATURE_SET_VERSION",
    "PARAMETER_FEATURES",
    "MACHINE_FEATURES",
    "PREDICTION_COMPONENT",
    "RULE_COMPLETENESS_MIN",
    "RULE_RISK_PREFIX",
    "DEFAULT_LOOKBACK_SECONDS",
    # errors
    "PredictionError",
    "MasterDataUnavailableError",
    "OperationalDataUnavailableError",
    "ModelNotTrainedError",
    "ModelLoadError",
    "TrainingDataInsufficientError",
    "FeatureExtractionError",
]
