"""Typed failures for the Supervisor Orchestrator.

Each names the stage that failed, because the Supervisor's whole value is knowing where
in the pipeline something went wrong. §16.10 requires stage failures to be recorded
rather than swallowed, and §16.11 requires the platform's own behaviour to be
inspectable: "what was detected, what was predicted, what was escalated, what was
recommended, what was delivered, and what failed".

A stage failure aborts the cycle. The Supervisor never half-executes a workflow: a
prediction that ran against a failed monitoring pass would be scoring a factory nobody
had looked at.
"""

from __future__ import annotations


class SupervisorError(Exception):
    """Base class for every failure raised by the Supervisor Orchestrator."""


class MasterDataUnavailableError(SupervisorError):
    """Master data the orchestrator depends on is missing."""


class OperationalDataUnavailableError(SupervisorError):
    """There is no operational history to orchestrate over.

    An empty telemetry table means the Factory Simulation Engine has not run. The
    Supervisor refuses rather than reporting a quiet factory, because "nothing detected"
    and "nothing examined" must never look the same.
    """


class EscalationPolicyMissingError(SupervisorError):
    """No ``business_rule`` supplies the escalation thresholds.

    §29 rule 4: a consumer whose category has no active global rule "would default
    silently in code", which "would defeat the purpose of this entity". The Supervisor is
    the primary consumer of the escalation category, so its absence is fatal rather than
    forgiving. E17 rule 4 also requires the governing rule to be *named* on the row, and
    a rule that does not exist cannot be named.
    """


class MonitoringStageError(SupervisorError):
    """The Monitoring Agent failed. The cycle stops here."""


class PredictionStageError(SupervisorError):
    """The Prediction Agent failed. No escalation decision is taken."""


class DecisionStageError(SupervisorError):
    """The Decision Agent failed after contexts were escalated.

    The escalated contexts remain committed and are picked up on the next cycle: §E17
    makes the context row the durable record of the escalation, so a reasoning failure
    loses the recommendation but never the decision to seek one.
    """


class ContextAssemblyError(SupervisorError):
    """A context package could not be assembled for an escalated situation.

    E17 rule 10 requires all seven blocks when escalating, and rule 2 requires a non-NULL
    document. Escalating without a complete package would give the Decision Agent an
    input it cannot satisfy the §16.5 contract from, so the situation is recorded as
    suppressed instead of escalated with a hole in it.
    """
