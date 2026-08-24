"""The Supervisor Orchestrator. It coordinates; it does not compute.

§16.2 states its one responsibility as "orchestrate and assemble context", and what it
must never do as "predict risk or generate recommendations". So this module contains no
detector, no model, no prompt and no threshold of its own. What it contains is the order
in which the finished components run, the gate that decides how far a situation travels,
and the package it hands forward.

**The five stages, and where each one stops.**

.. code-block:: text

    run_monitoring()   Monitoring Agent    no live alert    -> cycle ends
    run_prediction()   Prediction Agent    no prediction    -> cycle ends
    run_escalation()   escalation gate     none escalated   -> cycle ends
    run_decision()     Decision Agent      no recommendation -> cycle ends
    run_notification() Notification Service
    build_response()   the single structured response

Each stage method has one responsibility, decides for itself whether it is required, and
returns its own result. Nothing is accumulated in a shared mutable object as the workflow
runs: the five results flow forward and :meth:`SupervisorOrchestrator.build_response`
combines them at the end. A stage that was never reached returns an empty result and
contributes no :class:`StageOutcome`, which is what makes the response say precisely how
far the cycle travelled.

**Notification is the one stage whose failure does not end the cycle.** The other four
raise a stage error, because a failure there means the workflow cannot proceed and the
next cycle should retry it. Delivery is different: by the time it runs, the context row
and the recommendation are already committed, and losing them to an undelivered WhatsApp
message would destroy the analysis to punish the postman. So a delivery fault is recorded
on the stage and in ``notification_delivery``, and the Final Response is still returned.

**The Supervisor is the only caller of the Notification Service, and it passes only the
Decision Result.** The service invokes no agent, reads no prediction and takes no
decision; it receives the recommendations that were just produced and delivers them.

Each stage is skipped only by the documented gate that precedes it. §16.4: "Detection
triggers prediction; prediction informs escalation; escalation triggers reasoning."
§12.4's volume table is the point of the whole arrangement -- "170 -> 25 evaluated -> ~2
escalated" -- and §TS5 states the requirement it exists to satisfy: "LLM reasoning is
invoked only on escalated, context-enriched situations".

**Every component is constructed once and reused.** One engine, one session factory,
shared by all three agents. That is what keeps the Prediction Agent's model cache warm
across cycles -- it loads each artifact once per process and never retrains -- and gives
the Decision Agent a single Groq client. Constructing them per cycle would silently
reintroduce the loading and retraining the previous phases were built to avoid.

**The Supervisor opens no transaction of its own except its own row.** T-SUP-1 (§46.4):
"Insert one ``supervisor_context``, escalated or suppressed. Single-table, single-row, and
the simplest boundary in the platform. That simplicity is a direct consequence of the
Supervisor Agent owning exactly one table." The three agents keep their own boundaries;
the Supervisor never wraps them.

**Hand-off is through the database, not through Python.** §16.7: "Agents read from it and
write to it rather than passing hidden state between themselves. This is the precondition
for auditability, replay, and reliable debugging." So the Supervisor writes the context
row and the Decision Agent reads it. Passing the package in memory would be faster and
would destroy the audit trail.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, null, select
from sqlalchemy.orm import Session, sessionmaker

from models.operational import (
    AiRecommendation,
    MachineSensorReading,
    Notification,
    NotificationDelivery,
    OperationalAlert,
    SupervisorContext as SupervisorContextRow,
)
from models.session import initialize_database, session_scope, shutdown_database

from decision import DecisionAgent
from monitoring import MonitoringAgent
from notification import NotificationService
from prediction import PredictionAgent, default_model_directory

from supervisor.assembly import build_context_document
from supervisor.context import (
    LIVE_ALERT_STATUSES,
    SUPERVISOR_COMPONENT,
    SupervisorContext,
)
from supervisor.errors import (
    ContextAssemblyError,
    DecisionStageError,
    MonitoringStageError,
    OperationalDataUnavailableError,
    PredictionStageError,
)
from supervisor.gate import evaluate


@dataclass
class StageOutcome:
    """What one stage did, and whether the workflow continued past it."""

    name: str
    ran: bool = False
    stopped_here: bool = False
    reason: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class MonitoringResult:
    """What the Monitoring Agent reported, and whether anything is live.

    ``live_alerts`` is the "no issue" gate: with nothing open there is no condition for
    the Prediction Agent to be triggered by.
    """

    outcome: StageOutcome
    summary: dict[str, Any] = field(default_factory=dict)
    live_alerts: int = 0

    @property
    def should_continue(self) -> bool:
        return self.live_alerts > 0


@dataclass
class PredictionOutcome:
    """What the Prediction Agent reported, or an empty result when not required.

    Named ``PredictionOutcome`` rather than ``PredictionResult`` because the latter is an
    ORM table class, and one of the two would have to be aliased at every import site.
    """

    outcome: StageOutcome | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    predictions: int = 0

    @property
    def ran(self) -> bool:
        return self.outcome is not None and self.outcome.ran

    @property
    def should_continue(self) -> bool:
        return self.ran and self.predictions > 0


@dataclass
class EscalationResult:
    """The gate's verdicts for this cycle, escalated and suppressed alike."""

    outcome: StageOutcome | None = None
    escalations: list[dict[str, Any]] = field(default_factory=list)
    suppressions: dict[str, int] = field(default_factory=dict)

    @property
    def should_continue(self) -> bool:
        return self.outcome is not None and bool(self.escalations)


@dataclass
class DecisionResult:
    """What the Decision Agent produced, read back for the response."""

    outcome: StageOutcome | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    traceability: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class NotificationResult:
    """What the Notification Service delivered, or an empty result when not required.

    ``outcome`` stays ``None`` when there was no recommendation to deliver, so a cycle
    that stopped early contributes no ``notification`` stage -- the same convention the
    prediction, escalation and decision stages follow.

    ``report`` carries the service's own :class:`~notification.DispatchReport` when one
    was produced, so the caller can read the per-recipient outcomes without the
    orchestrator having to restate them.
    """

    outcome: StageOutcome | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    report: Any | None = None

    @property
    def ran(self) -> bool:
        return self.outcome is not None and self.outcome.ran


@dataclass
class FinalResponse:
    """The single structured response, delivered by the Notification Service.

    §16.6 terminates the pipeline at a person, and ``notification_summary`` is the record
    of that last step: which messages were composed, which were suppressed and why, and
    which reached the provider.
    """

    started_at: datetime
    reference_instant: datetime | None = None
    stages: list[StageOutcome] = field(default_factory=list)
    monitoring_summary: dict[str, Any] = field(default_factory=dict)
    prediction_summary: dict[str, Any] = field(default_factory=dict)
    decision_summary: dict[str, Any] = field(default_factory=dict)
    escalations: list[dict[str, Any]] = field(default_factory=list)
    suppressions: dict[str, int] = field(default_factory=dict)
    recommendations: list[dict[str, Any]] = field(default_factory=list)
    traceability: list[dict[str, Any]] = field(default_factory=list)
    notification_summary: dict[str, Any] = field(default_factory=dict)
    execution: dict[str, Any] = field(default_factory=dict)

    @property
    def stopped_at(self) -> str:
        for stage in self.stages:
            if stage.stopped_here:
                return stage.name
        return "complete"

    def as_dict(self) -> dict[str, Any]:
        """The response as a plain document, ready to hand to a later phase."""
        return {
            "monitoring_summary": self.monitoring_summary,
            "prediction_summary": self.prediction_summary,
            "decision_summary": self.decision_summary,
            "escalations": self.escalations,
            "suppressions": self.suppressions,
            "recommendations": self.recommendations,
            "traceability": self.traceability,
            "notification_summary": self.notification_summary,
            "execution": dict(self.execution, stopped_at=self.stopped_at,
                              stages=[
                                  {"stage": s.name, "ran": s.ran,
                                   "stopped_here": s.stopped_here,
                                   "reason": s.reason, "detail": s.detail}
                                  for s in self.stages
                              ]),
        }


class SupervisorOrchestrator:
    """Drives the finished components in the documented order."""

    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[Session],
        *,
        model_directory: str | Path | None = None,
        database_path: str | Path | None = None,
        reasoner: Any | None = None,
        env_path: str | Path | None = None,
        notification_sender: Any | None = None,
        quiet: bool = False,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.quiet = quiet
        self._env_path = env_path
        self._notification_sender = notification_sender
        # Built on first use rather than here. Constructing it eagerly would make the
        # whole Supervisor refuse to start when a WhatsApp credential is absent, and a
        # missing delivery credential must not stop monitoring, prediction and reasoning
        # from running. A configuration fault still surfaces -- it is raised by the
        # service on the first delivery and recorded on the notification stage.
        self._notification: NotificationService | None = None

        with session_scope(session_factory) as session:
            self._require_operational_data(session)
            self.context = SupervisorContext(session)

        # One instance of each finished component, sharing this connection layer. Their
        # internals are never touched -- only run_cycle() is called.
        self.monitoring = MonitoringAgent(engine, session_factory, quiet=True)
        self.prediction = PredictionAgent(
            engine, session_factory,
            model_directory=(
                model_directory if model_directory is not None
                else default_model_directory(database_path or ":memory:")),
            quiet=True,
        )
        self.decision = DecisionAgent(
            engine, session_factory, reasoner=reasoner, env_path=env_path, quiet=True)

    @staticmethod
    def _require_operational_data(session: Session) -> None:
        readings = session.execute(
            select(func.count()).select_from(MachineSensorReading)).scalar_one()
        if readings == 0:
            raise OperationalDataUnavailableError(
                "machine_sensor_reading is empty; the Factory Simulation Engine has not "
                "run against this database, so there is no operational change to "
                "orchestrate over")

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    # ---------------------------------------------------------------- one workflow

    def run_cycle(self) -> FinalResponse:
        """One pass of the documented workflow, stopping at the first closed gate.

        The whole workflow, readable in five lines. Each stage decides for itself whether
        it is required, so the order here is the order in the architecture and nothing
        else.
        """
        started_at = datetime.now()
        reference_instant = self._reference_instant()

        monitoring = self.run_monitoring()
        prediction = self.run_prediction(monitoring)
        escalation = self.run_escalation(prediction, reference_instant)
        decision = self.run_decision(escalation)
        notification = self.run_notification(decision)

        return self.build_response(
            started_at, reference_instant, monitoring, prediction, escalation, decision,
            notification)

    # ------------------------------------------------------------------- stage 1

    def run_monitoring(self) -> MonitoringResult:
        """Invoke the Monitoring Agent and report what it found.

        Detection only. Whether anything downstream is required is read off the live
        alert count and left for the next stage to act on.
        """
        stage = StageOutcome(name="monitoring")
        try:
            report = self.monitoring.run_cycle()
        except Exception as exc:  # noqa: BLE001 -- re-raised as a stage failure
            raise MonitoringStageError(
                "the Monitoring Agent failed, so the cycle stops before prediction: "
                "%s: %s" % (type(exc).__name__, exc)) from exc
        stage.ran = True

        live = self._live_alerts_count()
        stage.detail = {"detections": report.detections, "live_alerts": live}

        if live == 0:
            stage.stopped_here = True
            stage.reason = (
                "No live alert exists, so there is no condition to quantify. "
                "Prediction and reasoning are not required.")
            self.say("  monitoring: no live alert — cycle ends")
        else:
            self.say("  monitoring: %d detection(s), %d live alert(s)"
                     % (report.detections, live))

        return MonitoringResult(
            outcome=stage,
            summary={
                "detections": report.detections,
                "events_written": report.events_written,
                "alerts_opened": report.alerts_opened,
                "alerts_updated": report.alerts_updated,
                "alerts_suppressed": report.suppressed,
                "alerts_resolved": report.resolved,
                "alerts_closed": report.closed,
                "by_category": dict(report.by_category),
                "by_type": dict(report.by_type),
                "alert_codes": list(report.alert_codes),
                "live_alerts": live,
            },
            live_alerts=live,
        )

    # ------------------------------------------------------------------- stage 2

    def run_prediction(self, monitoring: MonitoringResult) -> PredictionOutcome:
        """Invoke the Prediction Agent when monitoring found something to quantify.

        Inference only -- it never trains here. Returns an empty result without invoking
        anything when no alert is live.
        """
        if not monitoring.should_continue:
            return PredictionOutcome()

        stage = StageOutcome(name="prediction")
        try:
            report = self.prediction.run_cycle()
        except Exception as exc:  # noqa: BLE001 -- re-raised as a stage failure
            raise PredictionStageError(
                "the Prediction Agent failed, so no escalation decision is taken: "
                "%s: %s" % (type(exc).__name__, exc)) from exc
        stage.ran = True
        stage.detail = {
            "predictions": report.predictions,
            "models_in_memory": report.models_loaded,
        }

        if report.predictions == 0:
            stage.stopped_here = True
            stage.reason = (
                "No prediction was produced this cycle, so there is no quantified risk "
                "to escalate. The feature snapshots record why.")
            self.say("  prediction: no prediction produced — cycle ends")
        else:
            self.say("  prediction: %d prediction(s), %d model(s) in memory"
                     % (report.predictions, report.models_loaded))

        return PredictionOutcome(
            outcome=stage,
            summary={
                "snapshots": report.snapshots,
                "sufficient": report.sufficient,
                "insufficient": report.insufficient,
                "predictions": report.predictions,
                "by_insufficiency": dict(report.by_insufficiency),
                "models_in_memory": report.models_loaded,
                "machines_without_model": report.skipped_no_model,
                "untrained_categories": list(report.untrained_categories),
            },
            predictions=report.predictions,
        )

    # ------------------------------------------------------------------- stage 3

    def run_escalation(
        self,
        prediction: PredictionOutcome,
        reference_instant: datetime | None,
    ) -> EscalationResult:
        """Run the escalation gate over every live alert that has changed.

        One ``supervisor_context`` row per evaluated alert, escalated or suppressed. The
        gate itself lives in :mod:`supervisor.gate`; this method only drives it and
        collects the verdicts.
        """
        if not prediction.should_continue:
            return EscalationResult()

        stage = StageOutcome(name="escalation")
        stage.ran = True
        result = EscalationResult(outcome=stage)

        pending = self._alerts_awaiting_evaluation()
        for alert_id in pending:
            verdict = self._decide_one(alert_id, reference_instant)
            if verdict is None:
                continue
            if verdict["decision"] == "escalated":
                result.escalations.append(verdict)
            else:
                result.suppressions[verdict["decision"]] = (
                    result.suppressions.get(verdict["decision"], 0) + 1)

        evaluated = len(pending)
        stage.detail = {
            "alerts_evaluated": evaluated,
            "escalated": len(result.escalations),
            "suppressed": sum(result.suppressions.values()),
        }
        self.say("  escalation: %d evaluated, %d escalated, %d suppressed"
                 % (evaluated, len(result.escalations),
                    sum(result.suppressions.values())))

        if not result.escalations:
            stage.stopped_here = True
            stage.reason = (
                "Every evaluated situation was suppressed with a recorded reason, so "
                "reasoning was not warranted. The rows explain the silence.")
        return result

    def _alerts_awaiting_evaluation(self) -> list[int]:
        """Live alerts whose newest event has not yet been through the gate."""
        with session_scope(self.session_factory) as session:
            alerts = list(session.scalars(
                select(OperationalAlert)
                .where(OperationalAlert.alert_status.in_(LIVE_ALERT_STATUSES))
                .order_by(OperationalAlert.opened_at,
                          OperationalAlert.operational_alert_id)
            ))
            return [
                alert.operational_alert_id for alert in alerts
                if not self._already_evaluated(session, alert)
            ]

    def _decide_one(
        self,
        alert_id: int,
        reference_instant: datetime | None,
    ) -> dict[str, Any] | None:
        """Evaluate one alert and write its row. T-SUP-1: one row, one transaction."""
        started = datetime.now()
        with session_scope(self.session_factory) as session:
            alert = session.get(OperationalAlert, alert_id)
            if alert is None:
                return None
            verdict = evaluate(
                session, self.context, alert,
                reference_instant or alert.last_event_at)

            assembled_at = alert.last_event_at
            document = None
            if verdict.escalated and verdict.prediction is not None:
                # generated_at on the recommendation must be >= this instant, and the
                # prediction must not post-date the package it is cited in.
                assembled_at = max(assembled_at, verdict.prediction.predicted_at)
                try:
                    document = build_context_document(
                        session, self.context, alert, verdict.prediction, assembled_at)
                except ContextAssemblyError as exc:
                    verdict.decision = "suppressed_insufficient_data"
                    verdict.rationale = (
                        "A complete context package could not be assembled, so the "
                        "situation was recorded rather than escalated with an "
                        "incomplete input. %s" % exc)
                    document = None

            machine = (
                None if alert.machine_id is None
                else self.context.master.machines.get(alert.machine_id))
            line_id = alert.production_line_id or (
                None if machine is None else machine.production_line_id)

            row = SupervisorContextRow(
                supervisor_context_code=self.context.context_code(assembled_at),
                machine_id=alert.machine_id,
                production_line_id=line_id,
                assembled_at=assembled_at,
                triggering_alert_id=alert.operational_alert_id,
                triggering_prediction_id=(
                    None if verdict.prediction is None
                    else verdict.prediction.prediction_result_id),
                related_alert_codes=(
                    verdict.related_alert_codes if verdict.related_alert_codes
                    else null()),
                escalation_decision=verdict.decision,
                applied_escalation_rule_id=(
                    None if verdict.applied_rule is None
                    else verdict.applied_rule.business_rule_id),
                escalation_rationale=verdict.rationale,
                # An explicit SQL NULL, not Python None. The column is JSON, and the
                # frozen type serialises None to the JSON literal 'null' -- which is a
                # value, not an absence, and would fail ck_sc_suppressed_has_no_context.
                # E17 rule 3 requires suppressions to carry no document at all.
                context_document=document if document is not None else null(),
                context_assembly_duration_ms=max(
                    int((datetime.now() - started).total_seconds() * 1000), 0),
                shift_id=self.context.master.shift_at(assembled_at).shift_id,
                created_by_component=SUPERVISOR_COMPONENT,
            )
            session.add(row)
            session.flush()

            return {
                "context_code": row.supervisor_context_code,
                "decision": verdict.decision,
                "machine": None if machine is None else machine.machine_code,
                "alert": alert.operational_alert_code,
                "prediction": (
                    None if verdict.prediction is None
                    else verdict.prediction.prediction_result_code),
                "probability": verdict.probability,
                "threshold": verdict.threshold,
                "applied_rule": (
                    None if verdict.applied_rule is None
                    else verdict.applied_rule.code),
                "rationale": verdict.rationale,
                "related_alerts": verdict.related_alert_codes,
                "blocks": sorted(document) if document else [],
            }

    @staticmethod
    def _already_evaluated(session: Session, alert: OperationalAlert) -> bool:
        """Whether this alert already has a context row at its current event count.

        The gate is re-entrant: a live alert that has not produced a new event since it
        was last evaluated does not need re-evaluating, and re-recording the same verdict
        every cycle would bury the interesting rows.
        """
        newest = session.scalars(
            select(SupervisorContextRow.assembled_at)
            .where(SupervisorContextRow.triggering_alert_id
                   == alert.operational_alert_id)
            .order_by(SupervisorContextRow.assembled_at.desc())
            .limit(1)
        ).first()
        return newest is not None and newest >= alert.last_event_at

    # ------------------------------------------------------------------- stage 4

    def run_decision(self, escalation: EscalationResult) -> DecisionResult:
        """Invoke the Decision Agent when something escalated.

        It reads the context rows the gate has just committed -- the hand-off is the
        database, not this method's arguments -- and the recommendations it produces are
        read back for the response.
        """
        if not escalation.should_continue:
            return DecisionResult()

        stage = StageOutcome(name="decision")
        try:
            report = self.decision.run_cycle()
        except Exception as exc:  # noqa: BLE001 -- re-raised as a stage failure
            raise DecisionStageError(
                "the Decision Agent failed after %d context(s) were escalated. The "
                "contexts remain committed and will be reasoned over on the next "
                "cycle: %s: %s"
                % (len(escalation.escalations), type(exc).__name__, exc)) from exc
        stage.ran = True
        stage.detail = {
            "recommendations": report.recommendations,
            "llm_calls": report.llm_calls,
        }
        self.say("  decision: %d recommendation(s), %d LLM call(s)"
                 % (report.recommendations, report.llm_calls))

        recommendations, traceability = self._collect_recommendations(report.codes)
        return DecisionResult(
            outcome=stage,
            summary={
                "contexts_considered": report.contexts_considered,
                "already_recommended": report.already_recommended,
                "recommendations": report.recommendations,
                "contract_complete": report.contract_complete,
                "flagged_for_review": report.flagged_for_review,
                "llm_calls": report.llm_calls,
                "prompt_tokens": report.prompt_tokens,
                "completion_tokens": report.completion_tokens,
                "skipped": dict(report.skipped),
            },
            recommendations=recommendations,
            traceability=traceability,
        )

    def _collect_recommendations(
        self,
        codes: list[str],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Read back what the Decision Agent produced, and its traceability chain."""
        recommendations: list[dict[str, Any]] = []
        traceability: list[dict[str, Any]] = []
        if not codes:
            return recommendations, traceability

        master = self.context.master
        with session_scope(self.session_factory) as session:
            for row in session.scalars(
                select(AiRecommendation).where(
                    AiRecommendation.ai_recommendation_code.in_(codes))
                .order_by(AiRecommendation.ai_recommendation_code)
            ):
                context_row = session.get(
                    SupervisorContextRow, row.supervisor_context_id)
                severity = master.severities.get(row.priority_severity_level_id)
                category = master.failure_categories.get(
                    row.root_cause_failure_category_id)
                machine = master.machines.get(row.machine_id)

                recommendations.append({
                    "recommendation": row.ai_recommendation_code,
                    "machine": None if machine is None else machine.machine_code,
                    "priority": (
                        None if severity is None
                        else severity.failure_severity_level_code),
                    "priority_name": None if severity is None else severity.severity_name,
                    "root_cause": (
                        None if category is None else category.failure_category_code),
                    "root_cause_confidence": row.root_cause_confidence.value,
                    "recommended_action": row.recommended_action,
                    "recovery_plan": row.recovery_plan,
                    "reasoning_narrative": row.reasoning_narrative,
                    "business_impact": row.business_impact,
                    "estimated_downtime_minutes": row.estimated_downtime_minutes,
                    "recommended_action_by": (
                        None if row.recommended_action_by is None
                        else row.recommended_action_by.isoformat()),
                    "contract_complete": bool(row.contract_complete),
                    "llm_model": "%s %s" % (row.llm_model_name, row.llm_model_version),
                })
                traceability.append({
                    "recommendation": row.ai_recommendation_code,
                    "supervisor_context": (
                        None if context_row is None
                        else context_row.supervisor_context_code),
                    "triggering_alert": (
                        None if context_row is None else session.get(
                            OperationalAlert, context_row.triggering_alert_id
                        ).operational_alert_code),
                    "prediction_result": row.prediction_result.prediction_result_code,
                    "feature_snapshot": (
                        row.prediction_result.prediction_feature_snapshot
                        .prediction_feature_snapshot_code),
                    "evidence_events": [
                        entry.get("code") for entry in
                        row.supporting_evidence.get("events", [])
                    ],
                })
        return recommendations, traceability

    # ------------------------------------------------------------------- stage 5

    def run_notification(self, decision: DecisionResult) -> NotificationResult:
        """Invoke the Notification Service when a recommendation was produced.

        The last stage. §16.6 ends the pipeline at a person, and what that person is told
        is exactly the decision that was just taken, so the service receives the Decision
        Result and nothing else: no monitoring summary, no escalation verdict, no
        prediction. It invokes no agent and takes no decision of its own.

        The Decision Result is handed over inside the :class:`FinalResponse` the service's
        published interface accepts, carrying only ``recommendations``. Nothing else is
        populated, because nothing else is the service's business.

        A failure here is recorded, not raised. The context rows and recommendations are
        already committed by the time this runs, and discarding a completed analysis
        because a message could not be handed to a provider would be the wrong trade. The
        attempt is recorded in ``notification_delivery`` and on this stage, and the cycle
        completes.
        """
        if not decision.recommendations:
            return NotificationResult()

        stage = StageOutcome(name="notification")
        try:
            service = self._notification_service()
            report = service.deliver(FinalResponse(
                started_at=datetime.now(),
                recommendations=decision.recommendations,
            ))
        except Exception as exc:  # noqa: BLE001 -- recorded, deliberately not raised
            stage.ran = True
            stage.reason = (
                "delivery failed for %d recommendation(s). They remain committed and "
                "unaffected: %s: %s"
                % (len(decision.recommendations), type(exc).__name__, exc))
            self.say("  notification: FAILED, %s: %s" % (type(exc).__name__, exc))
            return NotificationResult(
                outcome=stage,
                summary={
                    "delivered": False,
                    "error": type(exc).__name__,
                    "error_detail": str(exc),
                    "recommendations_considered": len(decision.recommendations),
                },
            )

        stage.ran = True
        stage.detail = {
            "composed": report.composed,
            "transmitted": report.transmitted,
            "suppressed": report.suppressed,
            "failed": report.failed,
        }
        self.say("  notification: %d composed, %d transmitted, %d suppressed, %d failed"
                 % (report.composed, report.transmitted, report.suppressed,
                    report.failed))
        for gap in report.coverage_gaps:
            self.say("    COVERAGE GAP: %s" % gap)

        return NotificationResult(
            outcome=stage,
            summary={
                "delivered": True,
                "triggered": report.triggered,
                "skip_reason": report.skip_reason,
                "recommendations_considered": report.recommendations_considered,
                "recommendations_dispatched": report.recommendations_dispatched,
                "already_notified": report.already_notified,
                "composed": report.composed,
                "suppressed": report.suppressed,
                "transmitted": report.transmitted,
                "failed": report.failed,
                "attempts": report.attempts,
                "by_suppression": dict(report.by_suppression),
                "by_failure": dict(report.by_failure),
                "coverage_gaps": list(report.coverage_gaps),
                "recipient": report.recipient_masked,
                "retry_policy": report.retry_policy,
                "messages": [
                    {
                        "notification": entry.notification_code,
                        "recipient": entry.recipient,
                        "suppressed": entry.suppressed,
                        "suppression_reason": entry.suppression_reason,
                        "channel": entry.channel,
                        "delivery_status": entry.delivery_status,
                        "failure_reason": entry.failure_reason,
                        "attempts": entry.attempts,
                        "provider_reference": entry.provider_reference,
                        "latency_ms": entry.latency_ms,
                    }
                    for entry in report.outcomes
                ],
            },
            report=report,
        )

    def _notification_service(self) -> NotificationService:
        """The Notification Service, constructed once and reused across cycles.

        One instance for the process, like the three agents: it reads the recipient roster
        and the notification policy from master data at construction, and rebuilding it
        per cycle would re-read both for no gain.
        """
        if self._notification is None:
            self._notification = NotificationService(
                self.engine,
                self.session_factory,
                sender=self._notification_sender,
                env_path=self._env_path,
                quiet=True,
            )
        return self._notification

    # ------------------------------------------------------------------ the response

    def build_response(
        self,
        started_at: datetime,
        reference_instant: datetime | None,
        monitoring: MonitoringResult,
        prediction: PredictionOutcome,
        escalation: EscalationResult,
        decision: DecisionResult,
        notification: NotificationResult | None = None,
    ) -> FinalResponse:
        """Combine the five stage results into the one structured response.

        Assembly only: no agent is invoked, no model is loaded, no row is written. Stages
        that were never reached carry no outcome and so appear nowhere in ``stages``,
        which is what lets :attr:`FinalResponse.stopped_at` name the gate that closed.

        ``notification`` is optional so that a caller driving the four analysis stages
        directly still gets a response, unchanged in every other field.
        """
        delivery = notification if notification is not None else NotificationResult()
        response = FinalResponse(
            started_at=started_at,
            reference_instant=reference_instant,
            stages=[
                outcome for outcome in (
                    monitoring.outcome, prediction.outcome,
                    escalation.outcome, decision.outcome, delivery.outcome)
                if outcome is not None
            ],
            monitoring_summary=monitoring.summary,
            prediction_summary=prediction.summary,
            decision_summary=decision.summary,
            escalations=escalation.escalations,
            suppressions=escalation.suppressions,
            recommendations=decision.recommendations,
            traceability=decision.traceability,
            notification_summary=delivery.summary,
        )
        response.execution = self._execution_metadata(response)
        return response

    def _execution_metadata(self, response: FinalResponse) -> dict[str, Any]:
        """Row counts and which components ran, for the response's metadata block."""
        with session_scope(self.session_factory) as session:
            contexts = int(session.execute(
                select(func.count()).select_from(SupervisorContextRow)).scalar_one())
            escalated = int(session.execute(
                select(func.count()).select_from(SupervisorContextRow).where(
                    SupervisorContextRow.escalation_decision == "escalated")
            ).scalar_one())
            recommendations = int(session.execute(
                select(func.count()).select_from(AiRecommendation)).scalar_one())
            notifications = int(session.execute(
                select(func.count()).select_from(Notification)).scalar_one())
            deliveries = int(session.execute(
                select(func.count()).select_from(NotificationDelivery)).scalar_one())
        return {
            "reference_instant": (
                None if response.reference_instant is None
                else response.reference_instant.isoformat()),
            "duration_ms": int(
                (datetime.now() - response.started_at).total_seconds() * 1000),
            "supervisor_context_rows": contexts,
            "escalated_rows": escalated,
            "ai_recommendation_rows": recommendations,
            "notification_rows": notifications,
            "notification_delivery_rows": deliveries,
            "components": {
                "monitoring_agent": "invoked",
                "prediction_agent": "invoked" if any(
                    s.name == "prediction" and s.ran for s in response.stages)
                else "not required",
                "decision_agent": "invoked" if any(
                    s.name == "decision" and s.ran for s in response.stages)
                else "not required",
                "notification_service": "invoked" if any(
                    s.name == "notification" and s.ran for s in response.stages)
                else "not required",
            },
            "llm_model": self.decision.reasoner.model_version,
        }

    # ------------------------------------------------------------------ small reads

    def _live_alerts_count(self) -> int:
        """How many cases are open, acknowledged or escalated right now.

        This is the "no issue" gate. A factory with no live alert has nothing for the
        Prediction Agent to be triggered by, and §16.4 makes detection the trigger:
        "Detection triggers prediction."
        """
        with session_scope(self.session_factory) as session:
            return int(session.execute(
                select(func.count()).select_from(OperationalAlert).where(
                    OperationalAlert.alert_status.in_(LIVE_ALERT_STATUSES))
            ).scalar_one())

    def _reference_instant(self) -> datetime | None:
        """The newest operational timestamp -- the moment the platform treats as now."""
        with session_scope(self.session_factory) as session:
            newest = session.execute(
                select(func.max(MachineSensorReading.recorded_at))
            ).scalar_one_or_none()
            if newest is None:
                newest = session.execute(
                    select(func.max(OperationalAlert.last_event_at))
                ).scalar_one_or_none()
        return newest


def supervise(
    database_path: str | Path,
    *,
    cycles: int = 1,
    model_directory: str | Path | None = None,
    reasoner: Any | None = None,
    env_path: str | Path | None = None,
    notification_sender: Any | None = None,
    quiet: bool = False,
) -> list[FinalResponse]:
    """Run the workflow against a database. The orchestration entry point."""
    engine, session_factory = initialize_database(database_path)
    try:
        orchestrator = SupervisorOrchestrator(
            engine, session_factory,
            model_directory=model_directory,
            database_path=database_path,
            reasoner=reasoner,
            env_path=env_path,
            notification_sender=notification_sender,
            quiet=quiet,
        )
        orchestrator.say("Supervisor Orchestrator")
        responses: list[FinalResponse] = []
        for index in range(max(cycles, 1)):
            orchestrator.say("")
            orchestrator.say("Cycle %d" % (index + 1))
            response = orchestrator.run_cycle()
            responses.append(response)
            orchestrator.say("  stopped at: %s" % response.stopped_at)

        last = responses[-1]
        orchestrator.say("")
        orchestrator.say("Workflow output:")
        orchestrator.say("  %-34s %d" % (
            "supervisor_context", last.execution["supervisor_context_rows"]))
        orchestrator.say("  %-34s %d" % (
            "  of which escalated", last.execution["escalated_rows"]))
        orchestrator.say("  %-34s %d" % (
            "ai_recommendation", last.execution["ai_recommendation_rows"]))
        orchestrator.say("  %-34s %d" % (
            "notification", last.execution["notification_rows"]))
        orchestrator.say("  %-34s %d" % (
            "notification_delivery", last.execution["notification_delivery_rows"]))
        for entry in last.recommendations:
            orchestrator.say("  %s  %s  %s  %s"
                             % (entry["recommendation"], entry["machine"],
                                entry["priority"], entry["root_cause"]))
        if last.notification_summary:
            orchestrator.say("")
            orchestrator.say("Notification: %s" % (
                "%d composed, %d transmitted, %d suppressed, %d failed to %s"
                % (last.notification_summary.get("composed", 0),
                   last.notification_summary.get("transmitted", 0),
                   last.notification_summary.get("suppressed", 0),
                   last.notification_summary.get("failed", 0),
                   last.notification_summary.get("recipient", "unknown"))
                if last.notification_summary.get("delivered")
                else "not delivered, %s"
                     % last.notification_summary.get("error_detail", "unknown")))
        orchestrator.say("")
        orchestrator.say("Supervision Complete.")
        return responses
    finally:
        shutdown_database(engine)


def main(argv: list[str] | None = None) -> int:
    """``python -m supervisor <database-path> [cycles]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        print("usage: python -m supervisor <database-path> [cycles]", file=sys.stderr)
        return 2
    path = Path(args[0]).resolve()
    cycles = int(args[1]) if len(args) > 1 else 1
    try:
        supervise(path, cycles=cycles)
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        print("", file=sys.stderr)
        print("Supervision failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0
