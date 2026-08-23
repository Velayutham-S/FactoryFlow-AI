"""Correlation and the alert lifecycle. The Monitoring Agent's only writes.

Owns the two tables the ownership model assigns this component
(FACTORY_OPERATIONAL_DATA_DESIGN.md §6.2): ``operational_event`` insert-only, and
``operational_alert`` insert plus update. It also maintains
``machine_operational_status.open_alert_count``, which §46.2 assigns to this agent's
T-MON-1 and T-MON-3 boundaries even though the simulator owns the rest of that row --
the count is a maintained total over the alert table, so its only possible source is
here, and §41.6 reconciles it hourly against exactly that.

**Correlation is the whole point of the split.** A degrading bearing produced 34 events
over eleven hours as vibration flapped above and below its limit. Every one is a
legitimate observation and none should have been a separate notification. The
``correlation_key`` -- subject plus category -- groups them into one case a person is
asked to deal with once. Without it the platform would add to cognitive load rather than
reduce it, and the recipient would have muted the channel by the fifth message.

Three boundaries, from §46.2:

* **T-MON-1, detection.** Find or create the correlating alert, insert the event with
  that alert identifier, update the alert's ``event_count``, ``last_event_at`` and
  possibly ``current_severity_level_id``, and increment ``open_alert_count`` when the
  alert is new. Atomic because ``operational_event.operational_alert_id`` is NOT NULL
  and set at insert -- which is what makes the event immutable. The find-or-create and
  the insert must be one unit, or an event could reference an alert a concurrent
  rollback removed.
* **T-MON-2, acknowledgement.** Submitted by the Dashboard, written here, so the table
  keeps a single writer.
* **T-MON-3, resolution and closure.** Status, resolution, timestamps, and the
  ``open_alert_count`` decrement.

``uq_oa_open_correlation_key`` is the guard: a partial unique index over
``correlation_key`` where the status is open, acknowledged or escalated. If two
evaluations race to open an alert for the same condition, the second fails on the unique
violation and retries the find branch. §42.4 calls it the single most valuable index in
the operational group, and a correctness constraint rather than a performance one.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from models.operational import (
    MachineOperationalStatus,
    MaintenanceWorkRecord,
    OperationalAlert,
    OperationalEvent,
)

from monitoring.context import (
    ACTIVE_WORK_STATUSES,
    MONITORING_COMPONENT,
    Detection,
    MonitoringContext,
)
from monitoring.errors import CorrelationError

# Statuses in which an alert is still live and its correlation key is claimed. Matches
# the predicate of uq_oa_open_correlation_key exactly.
LIVE_STATUSES = ("open", "acknowledged", "escalated")


@dataclass
class AlertOutcome:
    """What one cycle's correlation produced."""

    events_written: int = 0
    alerts_opened: int = 0
    alerts_updated: int = 0
    severities_escalated: int = 0
    escalated_for_no_ack: int = 0
    suppressed: int = 0
    resolved: int = 0
    closed: int = 0
    codes: list[str] = field(default_factory=list)


class AlertWriter:
    """Turns detections into events attached to correlated alerts."""

    def __init__(self, context: MonitoringContext) -> None:
        self.context = context

    # -------------------------------------------------------------------- T-MON-1

    def record(
        self,
        session: Session,
        detections: list[Detection],
        outcome: AlertOutcome,
    ) -> None:
        """Write every detection as an event on its correlating alert.

        Detections are processed in event-time order so an alert's ``first_event_at``
        and ``last_event_at`` are correct on the first write rather than needing repair,
        and so a severity escalation lands in the order the conditions actually
        occurred.
        """
        for detection in sorted(detections, key=lambda d: d.detected_at):
            alert, is_new = self._find_or_create(session, detection)
            if alert is None:
                raise CorrelationError(
                    "could not correlate a %s/%s detection on %r; an event cannot be "
                    "written without an alert"
                    % (detection.category, detection.event_type,
                       detection.correlation_subject)
                )

            self._insert_event(session, detection, alert)
            outcome.events_written += 1

            if is_new:
                outcome.alerts_opened += 1
                outcome.codes.append(alert.operational_alert_code)
                self._increment_open_count(session, detection.machine_id)
            else:
                outcome.alerts_updated += 1
                self._absorb(detection, alert, outcome)

    def _find_or_create(
        self,
        session: Session,
        detection: Detection,
    ) -> tuple[OperationalAlert | None, bool]:
        """Find the live alert for this correlation key, or open one.

        The unique index makes the race safe rather than impossible: if a concurrent
        writer opened the alert first, the flush raises and this retries the find
        branch, which is the documented behaviour in §46.2.
        """
        existing = self._find_live(session, detection.correlation_key)
        if existing is not None:
            return existing, False

        alert = OperationalAlert(
            operational_alert_code=self.context.alert_code(detection.detected_at),
            correlation_key=detection.correlation_key,
            alert_category=detection.category,
            machine_id=detection.machine_id,
            production_line_id=detection.production_line_id,
            inventory_item_id=detection.inventory_item_id,
            initial_severity_level_id=detection.severity_level_id,
            current_severity_level_id=detection.severity_level_id,
            alert_status="open",
            event_count=1,
            opened_at=detection.detected_at,
            first_event_at=detection.detected_at,
            last_event_at=detection.detected_at,
            created_by_component=MONITORING_COMPONENT,
        )
        # The insert goes in a savepoint. If uq_oa_open_correlation_key fires because
        # another evaluation opened this alert between the find and the insert, only the
        # insert is discarded and the find branch is retried. A plain rollback here
        # would abort the whole boundary and discard events already written in it --
        # §41.5 is explicit that one IntegrityError aborts the entire transaction, which
        # is exactly why the savepoint is needed to continue within it.
        savepoint = session.begin_nested()
        try:
            session.add(alert)
            session.flush()
            savepoint.commit()
        except IntegrityError:
            savepoint.rollback()
            existing = self._find_live(session, detection.correlation_key)
            if existing is None:
                return None, False
            return existing, False
        return alert, True

    @staticmethod
    def _find_live(session: Session, correlation_key: str) -> OperationalAlert | None:
        return session.scalars(
            select(OperationalAlert).where(
                OperationalAlert.correlation_key == correlation_key,
                OperationalAlert.alert_status.in_(LIVE_STATUSES),
            )
        ).first()

    def _insert_event(
        self,
        session: Session,
        detection: Detection,
        alert: OperationalAlert,
    ) -> None:
        """One immutable observation, with the alert already known.

        The alert identifier is set at insert rather than attached afterwards, which is
        what allows the event to be genuinely immutable: had it been added later, every
        event would need updating and the immutability guarantee would be fiction
        (§E13).
        """
        session.add(OperationalEvent(
            operational_event_code=self.context.event_code(detection.detected_at),
            operational_alert_id=alert.operational_alert_id,
            event_category=detection.category,
            event_type=detection.event_type,
            detected_at=detection.detected_at,
            severity_level_id=detection.severity_level_id,
            machine_id=detection.machine_id,
            production_line_id=detection.production_line_id,
            production_run_id=detection.production_run_id,
            inventory_item_id=detection.inventory_item_id,
            machine_parameter_id=detection.machine_parameter_id,
            alert_threshold_rule_id=detection.alert_threshold_rule_id,
            observed_value=detection.observed_value,
            threshold_value_breached=detection.threshold_value_breached,
            threshold_direction=detection.threshold_direction,
            sustained_duration_seconds=detection.sustained_duration_seconds,
            triggering_reading_id=detection.triggering_reading_id,
            shift_id=self.context.shift_at(detection.detected_at).shift_id,
            detection_note=detection.detection_note,
            created_by_component=MONITORING_COMPONENT,
        ))

    def _absorb(
        self,
        detection: Detection,
        alert: OperationalAlert,
        outcome: AlertOutcome,
    ) -> None:
        """Fold one more event into an existing case."""
        alert.event_count = alert.event_count + 1
        if detection.detected_at > alert.last_event_at:
            alert.last_event_at = detection.detected_at

        # E14 rule 4: severity may only become more severe. A worsening case escalates;
        # it never quietly de-escalates, because de-escalation would hide deterioration.
        master = self.context.master
        if master.rank_of(detection.severity_level_id) < master.rank_of(
            alert.current_severity_level_id
        ):
            alert.current_severity_level_id = detection.severity_level_id
            outcome.severities_escalated += 1

    def _increment_open_count(self, session: Session, machine_id: int | None) -> None:
        if machine_id is None:
            return
        status = session.scalars(
            select(MachineOperationalStatus).where(
                MachineOperationalStatus.machine_id == machine_id)
        ).first()
        if status is not None:
            status.open_alert_count = status.open_alert_count + 1

    def _decrement_open_count(self, session: Session, machine_id: int | None) -> None:
        if machine_id is None:
            return
        status = session.scalars(
            select(MachineOperationalStatus).where(
                MachineOperationalStatus.machine_id == machine_id)
        ).first()
        if status is not None and status.open_alert_count > 0:
            status.open_alert_count = status.open_alert_count - 1

    # -------------------------------------------------------------------- T-MON-2

    def acknowledge(
        self,
        session: Session,
        alert_id: int,
        worker_id: int,
        at: datetime,
    ) -> None:
        """Record a human acknowledgement.

        Submitted through the Dashboard and written here so ``operational_alert`` keeps a
        single writer (§25.3). The Dashboard does not exist yet, so nothing in this phase
        calls it; it is present because the boundary belongs to this agent and splitting
        it across phases would leave the table with two writers.

        E14 rule 6 requires the acknowledging worker to hold the authority the severity
        implies. That check needs ``worker_role``, and it is the caller's to make -- the
        Dashboard knows who is acting.
        """
        alert = session.get(OperationalAlert, alert_id)
        if alert is None:
            raise CorrelationError("no alert with id %d to acknowledge" % alert_id)
        if alert.acknowledged_at is not None:
            return
        alert.acknowledged_at = at
        alert.acknowledged_by_worker_id = worker_id
        if alert.alert_status.value in ("open", "escalated"):
            alert.alert_status = "acknowledged"

    # -------------------------------------------------------------------- T-MON-3

    def escalate_unacknowledged(
        self,
        session: Session,
        now: datetime,
        outcome: AlertOutcome,
    ) -> None:
        """Escalate alerts whose acknowledgement window has elapsed.

        E14 rule 7: escalation fires when ``acknowledged_at`` is still NULL after
        ``failure_severity_level.max_acknowledgement_minutes``. **The window comes from
        master data, never from a constant** -- a severity with no window configured is
        one that does not demand acknowledgement, and is left alone.
        """
        master = self.context.master
        for alert in session.scalars(
            select(OperationalAlert).where(
                OperationalAlert.alert_status == "open",
                OperationalAlert.acknowledged_at.is_(None),
            )
        ):
            severity = master.severities.get(alert.current_severity_level_id)
            if severity is None or severity.max_acknowledgement_minutes is None:
                continue
            window = int(severity.max_acknowledgement_minutes)
            elapsed = (now - alert.opened_at).total_seconds() / 60.0
            if elapsed < window:
                continue
            alert.alert_status = "escalated"
            alert.escalated_at = now
            outcome.escalated_for_no_ack += 1

    def suppress_during_maintenance(
        self,
        session: Session,
        outcome: AlertOutcome,
    ) -> None:
        """Suppress live alerts on machines whose repair is now under way.

        §E14: an alert opened while a work order is already in progress is suppressed
        rather than notified, and the reason is recorded, because silent suppression
        would be indistinguishable from a delivery failure. Rule 10 is explicit that
        suppression stops notification and never stops recording -- a suppressed alert
        stays fully visible.
        """
        working = {
            machine_id for machine_id in session.scalars(
                select(MaintenanceWorkRecord.machine_id).where(
                    MaintenanceWorkRecord.work_status.in_(ACTIVE_WORK_STATUSES))
            )
        }
        if not working:
            return
        for alert in session.scalars(
            select(OperationalAlert).where(
                OperationalAlert.alert_status.in_(LIVE_STATUSES),
                OperationalAlert.machine_id.in_(working),
            )
        ):
            alert.alert_status = "suppressed"
            alert.suppression_reason = "maintenance_in_progress"
            outcome.suppressed += 1
            self._decrement_open_count(session, alert.machine_id)

    def resolve_after_maintenance(
        self,
        session: Session,
        now: datetime,
        outcome: AlertOutcome,
    ) -> None:
        """Resolve and close alerts a completed repair has answered.

        E14 rule 8: ``resolution_type = 'maintenance_performed'`` requires a **closed**
        ``maintenance_work_record`` referencing this alert through its
        ``triggering_alert_id``. The resolving job is derived by that query rather than
        stored as a back-reference, because a mutual reference between the two entities
        would be the model's only circular dependency.

        Rule 12: alerts are never deleted. A case that ended is closed, and the record
        of it stays.
        """
        closed_jobs = session.scalars(
            select(MaintenanceWorkRecord).where(
                MaintenanceWorkRecord.work_status == "closed",
                MaintenanceWorkRecord.triggering_alert_id.is_not(None),
            )
        ).all()
        if not closed_jobs:
            return

        by_alert: dict[int, MaintenanceWorkRecord] = {}
        for job in closed_jobs:
            current = by_alert.get(job.triggering_alert_id)
            if current is None or (job.closed_at or now) > (current.closed_at or now):
                by_alert[job.triggering_alert_id] = job

        for alert_id, job in by_alert.items():
            alert = session.get(OperationalAlert, alert_id)
            if alert is None:
                continue
            if alert.alert_status.value in ("resolved", "closed"):
                continue

            resolved_at = job.closed_at or now
            if resolved_at < alert.opened_at:
                resolved_at = alert.opened_at
            alert.resolved_at = resolved_at
            alert.resolution_type = "maintenance_performed"
            alert.resolution_note = (
                "Resolved by %s. %s"
                % (job.maintenance_work_record_code, job.resolution_note or "")
            ).strip()
            alert.closed_at = max(resolved_at, now)
            was_live = alert.alert_status.value in LIVE_STATUSES
            alert.alert_status = "closed"
            outcome.resolved += 1
            outcome.closed += 1
            if was_live:
                self._decrement_open_count(session, alert.machine_id)
