"""The Decision Agent (Phase 6.1).

Transition T4 of the platform's pipeline: an escalated ``supervisor_context`` becomes an
``ai_recommendation``. That is the whole responsibility. §16.2 states it as "reason and
recommend", and what it "must never do" as "detect, predict, actuate, or deliver" -- so
this package writes no event, no prediction, no notification, and nothing resembling a
command to a machine. §E18 rule 10 makes the last point structural: no column on the row
it owns could hold a setpoint even if the code tried.

**It owns exactly one table** (§6.2 #18): ``ai_recommendation``, insert-only and
immutable. Not ``supervisor_context``, which belongs to the Supervisor Agent, and not
``recommendation_action``, which §6.4 gives to the Dashboard because "ownership follows
the actor" -- the component that produces advice must not record the verdict on it.

**The five §16.5 contract elements, and where each comes from:**

============================  ============================================
Supporting evidence           persisted events and readings, plus the
                              prediction's own feature attributions
ML confidence                 ``prediction_result_id`` -- by reference, never
                              restated, because no probability column exists
Root cause                    classified by the LLM **within** the failure
                              modes declared for the machine's type
Business impact               computed from master data and ``business_rule``
Recommended action            LLM prose over settled figures, plus recovery
                              plan, team, engineer, part and deadline
============================  ============================================

``contract_complete`` records whether all five were produced. §E18 rule 5: a row with
``0`` "must not be delivered as final and must be flagged for review".

**Deterministic where it can be, generative only where it must be.** Every number --
priority, units at risk, margin, downtime cost, penalty exposure, repair duration,
deadline -- is arithmetic over database values, computed before the model is called and
passed to it as settled fact. The model supplies language and one classification from a
supplied list. §16.8 is the reason: "Using an LLM for arithmetic risk estimation or a
threshold rule for business impact analysis are both architectural errors."

Usage::

    python -m decision factoryflow.db

``GROQ_API_KEY`` is read from the environment or the project ``.env`` and appears in no
source file. Its absence raises :class:`LlmConfigurationError`.
"""

from decision.agent import CycleReport, DecisionAgent, Situation, decide, main
from decision.assignment import Assignment
from decision.context import (
    CONTEXT_BLOCKS,
    DECISION_COMPONENT,
    RULE_DEFER_MAX,
    RULE_PRIORITY_GOLD,
    RULE_PRIORITY_SAFETY,
    RULE_RISK_PREFIX,
    AppliedRule,
    DecisionContext,
    MasterSnapshot,
    RiskBand,
)
from decision.errors import (
    ContextDocumentInvalidError,
    DecisionError,
    LlmConfigurationError,
    LlmReasoningError,
    MasterDataUnavailableError,
    RecommendationRejectedError,
)
from decision.evidence import (
    Candidate,
    Evidence,
    PredictionFacts,
    build_evidence,
    cap_confidence,
    prediction_facts,
    root_cause_candidates,
    summarise,
)
from decision.impact import Impact, assess
from decision.reasoning import (
    LLM_MODEL_NAME,
    LLM_MODEL_VERSION,
    GroqReasoner,
    ReasoningBatch,
    ReasoningRequest,
    ReasoningResult,
    load_api_key,
)

__all__ = [
    # entry points
    "decide",
    "main",
    "DecisionAgent",
    # results
    "CycleReport",
    "Situation",
    "Assignment",
    "Impact",
    "Evidence",
    "Candidate",
    "PredictionFacts",
    # deterministic logic
    "assess",
    "build_evidence",
    "summarise",
    "prediction_facts",
    "root_cause_candidates",
    "cap_confidence",
    # context and policy
    "DecisionContext",
    "MasterSnapshot",
    "AppliedRule",
    "RiskBand",
    "CONTEXT_BLOCKS",
    "DECISION_COMPONENT",
    "RULE_PRIORITY_GOLD",
    "RULE_PRIORITY_SAFETY",
    "RULE_DEFER_MAX",
    "RULE_RISK_PREFIX",
    # reasoning
    "GroqReasoner",
    "ReasoningRequest",
    "ReasoningResult",
    "ReasoningBatch",
    "load_api_key",
    "LLM_MODEL_NAME",
    "LLM_MODEL_VERSION",
    # errors
    "DecisionError",
    "MasterDataUnavailableError",
    "ContextDocumentInvalidError",
    "LlmConfigurationError",
    "LlmReasoningError",
    "RecommendationRejectedError",
]
