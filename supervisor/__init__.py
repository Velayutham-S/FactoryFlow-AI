"""The Supervisor Orchestrator (Phase 6.2).

The coordinator. §5.2: "It decides whether a detected and scored event warrants
escalation, and if so, it assembles the complete decision context... It does not predict
and it does not generate recommendations -- it decides *what deserves attention* and
gathers *everything needed to reason about it*. This is also where cost and noise are
controlled, because it gates access to the LLM stage."

**It owns exactly one table** (§6.2 #17): ``supervisor_context``, insert-only. A row is
written for every situation it evaluates, escalated or suppressed, which is what lets the
platform answer *"why wasn't I told?"* with a row rather than a shrug.

**What it orchestrates:**

.. code-block:: text

    Monitoring Agent   ->  live alert?      no  -> cycle ends
    Prediction Agent   ->  prediction?      no  -> cycle ends
    escalation gate    ->  escalated?       no  -> cycle ends
    Decision Agent     ->  recommendation
    final response         (input to the Notification Service, next phase)

Each finished component is constructed once and driven through its own public entry point.
None of their internals is touched, no detector or model or prompt is duplicated, and each
keeps its own transaction boundaries -- the Supervisor's own boundary is T-SUP-1, a single
row.

Usage::

    python -m supervisor factoryflow.db
    python -m supervisor factoryflow.db 3

The Decision Agent's existing Groq integration is reused exactly as it stands, including
the ``llama-3.3-70b-versatile`` model and the ``GROQ_API_KEY`` it reads from ``.env``. The
Supervisor constructs no client of its own.
"""

from supervisor.assembly import LOOKBACK, build_context_document
from supervisor.context import (
    CATEGORY_ESCALATION,
    LIVE_ALERT_STATUSES,
    OPEN_WORK_STATUSES,
    SUPERVISOR_COMPONENT,
    MasterSnapshot,
    PolicyRule,
    SupervisorContext,
    SupervisorPolicy,
)
from supervisor.errors import (
    ContextAssemblyError,
    DecisionStageError,
    EscalationPolicyMissingError,
    MasterDataUnavailableError,
    MonitoringStageError,
    OperationalDataUnavailableError,
    PredictionStageError,
    SupervisorError,
)
from supervisor.gate import GateVerdict, evaluate
from supervisor.orchestrator import (
    DecisionResult,
    EscalationResult,
    FinalResponse,
    MonitoringResult,
    NotificationResult,
    PredictionOutcome,
    StageOutcome,
    SupervisorOrchestrator,
    main,
    supervise,
)

__all__ = [
    # entry points
    "supervise",
    "main",
    "SupervisorOrchestrator",
    # results — one per stage, combined by build_response()
    "FinalResponse",
    "StageOutcome",
    "MonitoringResult",
    "PredictionOutcome",
    "EscalationResult",
    "DecisionResult",
    "NotificationResult",
    "GateVerdict",
    # the gate and the package
    "evaluate",
    "build_context_document",
    "LOOKBACK",
    # context and policy
    "SupervisorContext",
    "SupervisorPolicy",
    "MasterSnapshot",
    "PolicyRule",
    "SUPERVISOR_COMPONENT",
    "CATEGORY_ESCALATION",
    "LIVE_ALERT_STATUSES",
    "OPEN_WORK_STATUSES",
    # errors
    "SupervisorError",
    "MasterDataUnavailableError",
    "OperationalDataUnavailableError",
    "EscalationPolicyMissingError",
    "MonitoringStageError",
    "PredictionStageError",
    "DecisionStageError",
    "ContextAssemblyError",
]
