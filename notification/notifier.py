"""The Notification Service. Transition T5: a recommendation becomes messages.

Owns two tables (§6.2 #20, #21): ``notification`` and ``notification_delivery``. It writes
nothing else. §16.2 states its one responsibility as "deliver messages" and what it must
never do as "decide content, priority, or recipients' actions" -- so this module composes
from a recommendation that already exists, routes by master data that already exists, and
invokes no agent.

**Its input is the Supervisor's Final Response.** Nothing is re-monitored, re-predicted or
re-reasoned. The response is read for the recommendation codes it carries, and the rows are
loaded through the ORM. When the workflow stopped early -- no live alert, no prediction,
nothing escalated -- there is no recommendation and nothing is sent.

**Three transaction boundaries, exactly as §46.6 draws them.**

``T-NOT-1`` compose
    "Insert one ``notification`` per qualifying recipient, including suppressed ones, for a
    single triggering recommendation." One transaction for the whole recipient evaluation,
    because "the suppression audit is only meaningful if the full recipient evaluation is
    recorded together. A partial commit could show two recipients notified and omit the
    record that a third was deliberately suppressed."

``T-NOT-2`` attempt
    "Insert one ``notification_delivery`` per channel attempt." Separate, "because delivery
    is an external call with unpredictable latency. Holding a transaction open across a
    provider round-trip would be the same defect as T-DEC-1 avoids." So the provider is
    called with no transaction open, and the row is written afterwards.

``T-NOT-3`` confirm
    "Update ``notification_delivery.delivery_status`` from ``sent`` to ``delivered``." The
    only ``UPDATE`` in the service, and the single documented mutation in Group G.

**Duplicate prevention reuses the identifiers already in the model.** §E20 rule 1 is the
mechanism: one notification per qualifying recipient per triggering event. A recipient who
already has a row for this recommendation is skipped, keyed on
``(ai_recommendation_id, notification_recipient_id)``. No new deduplication concept is
introduced and no frozen table is altered.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from models.operational import AiRecommendation, Notification, NotificationDelivery
from models.session import initialize_database, session_scope, shutdown_database

from notification.compose import Message, compose
from notification.context import NOTIFICATION_COMPONENT, NotificationContext
from notification.errors import MasterDataUnavailableError, NotificationError
from notification.recipients import Routing, already_composed, resolve
from notification.whatsapp import DeliveryOutcome, WhatsAppSender, confirmed_now

if TYPE_CHECKING:  # pragma: no cover - annotation only, no runtime coupling
    from supervisor import FinalResponse


@dataclass
class MessageOutcome:
    """What happened to one composed message."""

    notification_code: str
    recipient: str
    suppressed: bool
    suppression_reason: str | None = None
    channel: str | None = None
    delivery_status: str | None = None
    failure_reason: str | None = None
    attempts: int = 0
    provider_reference: str | None = None
    latency_ms: int | None = None


@dataclass
class DispatchReport:
    """What one dispatch run produced."""

    triggered: bool = False
    skip_reason: str = ""
    recommendations_considered: int = 0
    recommendations_dispatched: int = 0
    already_notified: int = 0
    composed: int = 0
    suppressed: int = 0
    transmitted: int = 0
    failed: int = 0
    attempts: int = 0
    by_suppression: dict[str, int] = field(default_factory=dict)
    by_failure: dict[str, int] = field(default_factory=dict)
    coverage_gaps: list[str] = field(default_factory=list)
    outcomes: list[MessageOutcome] = field(default_factory=list)
    recipient_masked: str = ""
    retry_policy: str = ""


class NotificationService:
    """Composes, routes and transmits. Nothing else."""

    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[Session],
        *,
        sender: Any | None = None,
        env_path: str | Path | None = None,
        quiet: bool = False,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.quiet = quiet
        # Resolved eagerly: a missing recipient number or credential is a deployment
        # fault, and it must surface before a single notification row is written.
        self.sender = sender if sender is not None else WhatsAppSender(env_path=env_path)
        with session_scope(session_factory) as session:
            self.context = NotificationContext(session)

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    # ------------------------------------------------------------------- dispatch

    def deliver(self, final_response: "FinalResponse") -> DispatchReport:
        """Deliver notifications for one Supervisor Final Response.

        The trigger conditions are read off the response rather than re-derived: a
        workflow that stopped at monitoring, prediction or escalation produced no
        recommendation, and a run with nothing to convey sends nothing.
        """
        report = DispatchReport(
            recipient_masked=getattr(self.sender, "masked_recipient", ""),
            retry_policy=self.context.policy.retry_note,
        )

        stopped_at = getattr(final_response, "stopped_at", "complete")
        codes = [
            entry.get("recommendation")
            for entry in getattr(final_response, "recommendations", []) or []
            if isinstance(entry, dict) and entry.get("recommendation")
        ]
        if stopped_at != "complete":
            report.skip_reason = (
                "the workflow stopped at %s, so no recommendation was produced and "
                "there is nothing to deliver" % stopped_at)
            self.say("  nothing to deliver: %s" % report.skip_reason)
            return report
        if not codes:
            report.skip_reason = (
                "the Final Response carries no recommendation, so there is nothing to "
                "deliver")
            self.say("  nothing to deliver: %s" % report.skip_reason)
            return report

        report.triggered = True
        report.recommendations_considered = len(codes)
        for code in codes:
            self._dispatch_one(code, report)
        return report

    def _dispatch_one(self, code: str, report: DispatchReport) -> None:
        """Compose for one recommendation, then transmit what was not suppressed."""
        before = report.composed
        composed = self._compose_for(code, report)
        # Counted when anything was composed for it, transmitted or suppressed: a
        # recommendation every recipient was suppressed for was still handled, and
        # reporting it as untouched would hide the suppression audit behind a zero.
        if report.composed > before:
            report.recommendations_dispatched += 1
        for notification_id, notification_code, message in composed:
            self._transmit(notification_id, notification_code, message, report)

    # -------------------------------------------------------------------- T-NOT-1

    def _compose_for(
        self,
        code: str,
        report: DispatchReport,
    ) -> list[tuple[int, str, Message]]:
        """One transaction covering the entire recipient evaluation.

        Returns the notifications that still need transmitting -- suppressed rows are
        written and deliberately not returned, because §E21 rule 1 allows delivery rows
        only "for notifications where ``is_suppressed = 0``. A suppressed message was
        never attempted."
        """
        master = self.context.master
        pending: list[tuple[int, str, Message]] = []

        with session_scope(self.session_factory) as session:
            recommendation = session.scalars(
                select(AiRecommendation).where(
                    AiRecommendation.ai_recommendation_code == code)
            ).first()
            if recommendation is None:
                raise MasterDataUnavailableError(
                    "the Final Response names recommendation %s, which is not in the "
                    "database" % code)

            machine = master.machines.get(recommendation.machine_id)
            severity = master.severities.get(recommendation.priority_severity_level_id)
            if machine is None or severity is None:
                raise MasterDataUnavailableError(
                    "recommendation %s references a machine or severity absent from "
                    "master data" % code)

            prediction = recommendation.prediction_result
            composed_at = recommendation.generated_at
            message = compose(
                recommendation,
                machine=machine,
                line=master.lines.get(machine.production_line_id),
                severity=severity,
                root_cause=recommendation.root_cause_failure_category,
                failure_probability=(
                    None if prediction is None
                    else float(prediction.failure_probability)),
                prediction_code=(
                    None if prediction is None else prediction.prediction_result_code),
                prediction_horizon_hours=(
                    None if prediction is None
                    else prediction.prediction_horizon_hours),
                timezone=master.timezone,
                composed_at=composed_at,
            )

            routings = resolve(
                session, master, self.context.policy,
                machine=machine,
                severity=severity,
                composed_at=composed_at,
                ai_recommendation_id=recommendation.ai_recommendation_id,
            )

            transmitted_here = 0
            for routing in routings:
                if already_composed(
                    session, recommendation.ai_recommendation_id,
                    routing.recipient.recipient_id,
                ):
                    report.already_notified += 1
                    continue

                row = self._notification_row(
                    recommendation, routing, message, severity, composed_at)
                session.add(row)
                session.flush()
                report.composed += 1

                outcome = MessageOutcome(
                    notification_code=row.notification_code,
                    recipient=routing.recipient.worker_code,
                    suppressed=routing.suppressed,
                    suppression_reason=routing.reason,
                )
                report.outcomes.append(outcome)

                if routing.suppressed:
                    report.suppressed += 1
                    reason = routing.reason or "unknown"
                    report.by_suppression[reason] = (
                        report.by_suppression.get(reason, 0) + 1)
                else:
                    transmitted_here += 1
                    pending.append(
                        (row.notification_id, row.notification_code, message))

            # §E20 rule 7. Recorded rather than raised: the rule asks for the gap to be
            # visible, and raising here would roll back the very suppression rows that
            # evidence it. The gap travels on the report, where the caller can act on it
            # without the audit trail being lost to an exception.
            if severity.requires_immediate_escalation and transmitted_here == 0:
                fallback = master.escalation_email_for(machine)
                report.coverage_gaps.append(
                    "%s (%s, %s): every eligible recipient was suppressed; the "
                    "departmental fallback is %s"
                    % (code, machine.machine_code,
                       severity.failure_severity_level_code,
                       fallback or "not configured"))

        return pending

    def _notification_row(
        self,
        recommendation: AiRecommendation,
        routing: Routing,
        message: Message,
        severity: Any,
        composed_at: datetime,
    ) -> Notification:
        """Build one ``notification`` row.

        ``requires_acknowledgement`` derives from the severity (§E20 rule 6) **and** is
        false on a suppressed row. Two frozen CHECK constraints force that pairing:
        ``ck_nt_ack_deadline_required`` demands a deadline whenever acknowledgement is
        required, and ``ck_nt_ack_deadline_after_composed`` demands it be later than
        composition. A clock cannot run against a message that was never transmitted, so
        a suppressed row carries neither.
        """
        acknowledge = bool(
            severity.requires_manager_acknowledgement
            and not routing.suppressed
            and severity.max_acknowledgement_minutes
        )
        deadline = (
            composed_at + timedelta(minutes=int(severity.max_acknowledgement_minutes))
            if acknowledge else None
        )
        return Notification(
            notification_code=self.context.notification_code(composed_at),
            notification_recipient_id=routing.recipient.recipient_id,
            notification_type="recommendation",
            ai_recommendation_id=recommendation.ai_recommendation_id,
            operational_alert_id=None,
            severity_level_id=severity.failure_severity_level_id,
            composed_at=composed_at,
            subject=message.subject,
            body_text=message.body,
            is_suppressed=routing.suppressed,
            suppression_reason=routing.reason,
            requires_acknowledgement=acknowledge,
            acknowledgement_deadline_at=deadline,
            escalation_order_applied=routing.recipient.escalation_order,
            shift_id=self.context.master.shift_at(composed_at).shift_id,
            created_by_component=NOTIFICATION_COMPONENT,
        )

    # -------------------------------------------------------------------- T-NOT-2

    def _transmit(
        self,
        notification_id: int,
        notification_code: str,
        message: Message,
        report: DispatchReport,
    ) -> None:
        """Call the provider with no transaction open, then record the attempt.

        Retries follow §E21 rule 4 and stop at the cap §E21 rule 5 puts in
        ``business_rule``. With no such rule present the cap is one attempt, so a
        transient failure is recorded once and not retried -- the absence of a policy
        disables retrying rather than inventing a limit.
        """
        limit = max(self.context.policy.retry_limit, 1)
        channel = getattr(self.sender, "channel", "whatsapp")
        outcome: DeliveryOutcome | None = None
        attempt = 0

        while attempt < limit:
            attempt += 1
            outcome = self.sender.send(message.subject, message.body)
            report.attempts += 1
            self._record_attempt(notification_id, channel, attempt, outcome)
            if outcome.succeeded or not outcome.retryable:
                break

        if outcome is None:  # pragma: no cover - the loop always runs once
            return

        for entry in report.outcomes:
            if entry.notification_code == notification_code:
                entry.channel = channel
                entry.delivery_status = outcome.status
                entry.failure_reason = outcome.failure_reason
                entry.attempts = attempt
                entry.provider_reference = outcome.provider_reference
                entry.latency_ms = outcome.latency_ms
                break

        if outcome.succeeded:
            report.transmitted += 1
        else:
            report.failed += 1
            reason = outcome.failure_reason or "unknown"
            report.by_failure[reason] = report.by_failure.get(reason, 0) + 1

    def _record_attempt(
        self,
        notification_id: int,
        channel: str,
        attempt: int,
        outcome: DeliveryOutcome,
    ) -> None:
        """One ``notification_delivery`` row. Its own transaction."""
        with session_scope(self.session_factory) as session:
            notification = session.get(Notification, notification_id)
            # Anchored to the notification's own instant, not wall-clock time, so the
            # delivery log stays on the same timeline as the rest of the platform.
            # ck_nd_delivered_at_not_before_attempt needs attempted_at >= composed_at;
            # a retry is offset by a second per attempt so the sequence stays ordered.
            attempted_at = (
                confirmed_now() if notification is None
                else notification.composed_at + timedelta(seconds=attempt - 1))
            session.add(NotificationDelivery(
                notification_id=notification_id,
                channel=channel,
                attempt_number=attempt,
                attempted_at=attempted_at,
                delivery_status=outcome.status,
                delivered_at=(
                    attempted_at if outcome.status == "delivered" else None),
                provider_reference=outcome.provider_reference,
                failure_reason=outcome.failure_reason,
                failure_detail=outcome.failure_detail,
                latency_ms=max(outcome.latency_ms, 0),
                created_by_component=NOTIFICATION_COMPONENT,
            ))

    # -------------------------------------------------------------------- T-NOT-3

    def confirm_delivery(
        self,
        provider_reference: str,
        *,
        confirmed_at: datetime | None = None,
    ) -> bool:
        """Advance one attempt from ``sent`` to ``delivered`` on provider confirmation.

        The service's only ``UPDATE``, and the single documented mutation in Group G. It
        matches on ``provider_reference`` because that is the identifier the provider
        quotes back, and §E21 rule 9 is why the reference is captured in the first place.

        Returns whether a row was advanced. ``latency_ms`` is recomputed from the
        confirmation instant, which is what §E21 means by "time from attempt to
        confirmation".
        """
        moment = confirmed_at or confirmed_now()
        with session_scope(self.session_factory) as session:
            row = session.scalars(
                select(NotificationDelivery).where(
                    NotificationDelivery.provider_reference == provider_reference,
                    NotificationDelivery.delivery_status == "sent",
                ).limit(1)
            ).first()
            if row is None:
                return False
            if moment < row.attempted_at:
                moment = row.attempted_at
            row.delivery_status = "delivered"
            row.delivered_at = moment
            row.latency_ms = int((moment - row.attempted_at).total_seconds() * 1000)
            return True

    # ------------------------------------------------------------------ reporting

    def summary(self) -> dict[str, int]:
        with session_scope(self.session_factory) as session:
            return {
                "notification": int(session.execute(
                    select(func.count()).select_from(Notification)).scalar_one()),
                "notification_suppressed": int(session.execute(
                    select(func.count()).select_from(Notification).where(
                        Notification.is_suppressed.is_(True))).scalar_one()),
                "notification_delivery": int(session.execute(
                    select(func.count()).select_from(NotificationDelivery)
                ).scalar_one()),
            }


def notify(
    database_path: str | Path,
    final_response: "FinalResponse",
    *,
    sender: Any | None = None,
    env_path: str | Path | None = None,
    quiet: bool = False,
) -> DispatchReport:
    """Deliver one Final Response against a database."""
    engine, session_factory = initialize_database(database_path)
    try:
        service = NotificationService(
            engine, session_factory, sender=sender, env_path=env_path, quiet=quiet)
        service.say("Notification Service — whatsapp to %s"
                    % service.sender.masked_recipient)
        service.say("  %s" % service.context.policy.retry_note)
        report = service.deliver(final_response)

        if report.triggered:
            service.say("")
            service.say(
                "Composed %d notification(s) for %d recommendation(s): "
                "%d transmitted, %d suppressed, %d failed, %d already notified"
                % (report.composed, report.recommendations_dispatched,
                   report.transmitted, report.suppressed, report.failed,
                   report.already_notified))
            for reason, count in sorted(report.by_suppression.items()):
                service.say("  suppressed  %-30s %d" % (reason, count))
            for reason, count in sorted(report.by_failure.items()):
                service.say("  failed      %-30s %d" % (reason, count))
            for gap in report.coverage_gaps:
                service.say("  COVERAGE GAP: %s" % gap)

        totals = service.summary()
        service.say("")
        service.say("Notification output:")
        for name in ("notification", "notification_suppressed",
                     "notification_delivery"):
            service.say("  %-32s %d" % (name, totals[name]))
        service.say("")
        service.say("Notification Complete.")
        return report
    finally:
        shutdown_database(engine)


def main(argv: list[str] | None = None) -> int:
    """``python -m notification <database-path> [cycles]``.

    Runs the Supervisor Orchestrator to obtain a Final Response, then delivers it. The
    import is local so the Notification Service carries no runtime dependency on the
    Supervisor: the service itself only ever receives a Final Response, and wiring the
    two phases together is an entry point's job rather than the service's.
    """
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        print("usage: python -m notification <database-path> [cycles]",
              file=sys.stderr)
        return 2
    path = Path(args[0]).resolve()
    cycles = int(args[1]) if len(args) > 1 else 1
    try:
        from supervisor import supervise

        responses = supervise(path, cycles=cycles)
        for response in responses:
            notify(path, response)
    except NotificationError as exc:
        print("", file=sys.stderr)
        print("Notification failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        print("", file=sys.stderr)
        print("Notification failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0
