"""Typed failures for the Decision Agent.

Every one carries enough detail to act on. The agent raises rather than degrading:
§16.10 requires stage failures to be *recorded* rather than swallowed, and a
recommendation is the platform's accountability record -- a half-formed one is worse
than none, because it would be delivered as though it were complete.
"""

from __future__ import annotations


class DecisionError(Exception):
    """Base class for every failure raised by the Decision Agent."""


class MasterDataUnavailableError(DecisionError):
    """Master data the agent depends on is missing.

    Raised rather than defaulted. Master data §29 rule 4 is explicit that a consumer
    with no policy "defaulting silently in code would defeat the purpose of this
    entity", so an absent ``business_rule`` is an error and never a constant.
    """


class ContextDocumentInvalidError(DecisionError):
    """An escalated context carries a payload the agent cannot reason over.

    §E17 rule 2 requires a non-NULL ``context_document`` for every escalation and
    rule 10 requires all seven blocks. A context that escalated without them is an
    upstream defect, and reasoning over a partial package would produce a
    confident-sounding recommendation with holes in its evidence.
    """


class LlmConfigurationError(DecisionError):
    """The LLM is not configured.

    Raised when ``GROQ_API_KEY`` is absent from the environment. The agent does not
    continue without it: a recommendation records which model reasoned, and inventing
    that provenance would corrupt the quality-attribution trail
    ``llm_model_version`` exists to keep.
    """


class LlmReasoningError(DecisionError):
    """The model call failed, or returned something unusable.

    Covers transport failure, a non-JSON body, and a response missing entries for
    contexts that were sent. Never swallowed: the alternative is a recommendation
    whose narrative was written by the fallback path and presented as the model's.
    """


class RecommendationRejectedError(DecisionError):
    """The assembled recommendation would violate a documented rule.

    Raised before the insert is attempted, so the transaction never opens on a row
    the model declares impossible.
    """
