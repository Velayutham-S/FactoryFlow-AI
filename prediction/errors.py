"""Exceptions for the Prediction Agent.

Nothing here is logged-and-swallowed. A prediction that silently did not happen is
indistinguishable from a healthy machine, and a model that silently retrained during
inference would destroy reproducibility -- both must reach the caller.
"""

from __future__ import annotations


class PredictionError(Exception):
    """Root of every error the Prediction Agent raises."""


class MasterDataUnavailableError(PredictionError):
    """Master data the agent needs is missing.

    Includes the ``business_rule`` rows that E15 rule 6 and E16 rule 6 require: the
    completeness threshold and the probability-to-severity cut-offs are policy, and
    both documents state they come from ``business_rule`` rather than from a constant.
    Defaulting silently in code would defeat the purpose of that entity.
    """


class OperationalDataUnavailableError(PredictionError):
    """The operational tables the agent reads are absent or empty."""


class ModelNotTrainedError(PredictionError):
    """No persisted model exists for the requested scope.

    Raised rather than triggering a train: the caller asked for an inference, and
    training inside a prediction request would make latency unpredictable and the
    model version non-deterministic. Training is an explicit separate operation.
    """


class ModelLoadError(PredictionError):
    """A persisted model exists but could not be loaded or is incompatible.

    A model whose feature list does not match the current feature set version cannot
    be used: the vector would be silently misaligned and every probability wrong.
    """


class TrainingDataInsufficientError(PredictionError):
    """Not enough labelled history to train a model.

    Supervised learning needs both classes. A model trained on one class returns a
    constant, and a constant dressed as a probability is worse than no model.
    """


class FeatureExtractionError(PredictionError):
    """A feature vector could not be built for a machine."""
