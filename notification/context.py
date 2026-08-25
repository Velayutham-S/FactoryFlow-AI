"""Shared state: the recipient roster, the notification policy, and the ``NTF-`` sequence.

Recipients are master data, not configuration in code. §17: "Without it, notification
routing would be hardcoded, and every change to who gets alerted would require a code
change. More importantly, the platform could not explain *why* a particular person was
contacted -- and the explainability contract requires that every step be traceable."

**Two policy values come from ``business_rule``**, and only one of them exists in the
seeded set:

``BR-NOTIF-QUIET``
    A boolean switch for quiet-hours suppression. Present.

retry cap
    §E21 rule 5: "Retry count is capped by a ``business_rule`` value, not a constant."
    The seeded ``notification`` category contains no such rule, so
    :attr:`NotificationPolicy.retry_limit` resolves to **one attempt and no retries** and
    :attr:`NotificationPolicy.retry_policy_missing` records why. That is the conservative
    reading rather than a default: retrying without a documented cap is the behaviour the
    rule exists to prevent, so its absence disables retries instead of inventing a number.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.master import (
    BusinessRule,
    Department,
    FailureSeverityLevel,
    Machine,
    NotificationRecipient,
    Plant,
    ProductionLine,
    Shift,
    Worker,
)
from models.operational import Notification

from notification.errors import MasterDataUnavailableError

NOTIFICATION_COMPONENT = "notification_service"

# Quiet-hours switch. Transcribed from §29's Example records; present in the seeded set.
RULE_QUIET_HOURS = "BR-NOTIF-QUIET"

# The retry cap §E21 rule 5 requires. No rule with this code exists in the seeded master
# data; its absence disables retries and is reported.
RULE_RETRY_MAX = "BR-NOTIF-RETRY-MAX"

CATEGORY_NOTIFICATION = "notification"


@dataclass(frozen=True)
class Recipient:
    """One configured recipient, with the worker endpoints resolved beside it.

    ``notification`` stores no contact detail -- §E20: "Contact endpoints resolve through
    the recipient to ``worker`` -- no contact detail is copied here." This object is the
    in-memory resolution, not a stored one.
    """

    recipient_id: int
    worker_code: str
    full_name: str
    min_severity_rank: int
    min_severity_code: str
    email_enabled: bool
    whatsapp_enabled: bool
    scope_production_line_id: int | None
    notify_outside_shift_hours: bool
    escalation_order: int
    max_notifications_per_hour: int | None
    shift_id: int
    has_email: bool
    has_phone: bool


class MasterSnapshot:
    """Every master row the service routes against, loaded once and indexed."""

    def __init__(self, session: Session) -> None:
        plant = session.scalars(select(Plant)).first()
        if plant is None:
            raise MasterDataUnavailableError("no plant row; master data is not seeded")
        self.plant = plant
        self.timezone = ZoneInfo(plant.timezone)

        self.severities: dict[int, FailureSeverityLevel] = {
            level.failure_severity_level_id: level
            for level in session.scalars(select(FailureSeverityLevel))
        }
        if not self.severities:
            raise MasterDataUnavailableError("no failure_severity_level rows")

        self.shifts: dict[int, Shift] = {
            shift.shift_id: shift for shift in session.scalars(select(Shift))
        }
        self.production_shifts = sorted(
            (s for s in self.shifts.values()
             if s.shift_type.value == "production" and s.is_active),
            key=lambda s: s.sequence_order,
        )
        if not self.production_shifts:
            raise MasterDataUnavailableError("no active production shift")

        self.machines: dict[int, Machine] = {
            m.machine_id: m for m in session.scalars(select(Machine))
        }
        self.lines: dict[int, ProductionLine] = {
            line.production_line_id: line
            for line in session.scalars(select(ProductionLine))
        }
        self.departments: dict[int, Department] = {
            d.department_id: d for d in session.scalars(select(Department))
        }

        workers = {w.worker_id: w for w in session.scalars(select(Worker))}
        self.workers = workers

        self.recipients: list[Recipient] = []
        for row in session.scalars(select(NotificationRecipient)):
            if not row.is_active:
                continue
            worker = workers.get(row.worker_id)
            if worker is None:
                continue
            severity = self.severities.get(row.min_severity_level_id)
            if severity is None:
                continue
            self.recipients.append(Recipient(
                recipient_id=row.notification_recipient_id,
                worker_code=worker.worker_code,
                full_name="%s %s" % (worker.first_name, worker.last_name),
                min_severity_rank=severity.severity_rank,
                min_severity_code=severity.failure_severity_level_code,
                email_enabled=bool(row.email_enabled),
                whatsapp_enabled=bool(row.whatsapp_enabled),
                scope_production_line_id=row.scope_production_line_id,
                notify_outside_shift_hours=bool(row.notify_outside_shift_hours),
                escalation_order=int(row.escalation_order),
                max_notifications_per_hour=(
                    None if row.max_notifications_per_hour is None
                    else int(row.max_notifications_per_hour)),
                shift_id=worker.shift_id,
                has_email=bool(worker.email),
                has_phone=bool(worker.phone_number),
            ))
        if not self.recipients:
            raise MasterDataUnavailableError(
                "no active notification_recipient rows. §17 makes this table the "
                "configuration that connects a recommendation to a person, so with none "
                "present there is no routing to perform")
        self.recipients.sort(key=lambda r: (r.escalation_order, r.worker_code))

    # ------------------------------------------------------------------ helpers

    def shift_at(self, moment: datetime) -> Shift:
        clock = moment.astimezone(self.timezone).time()
        for shift in self.production_shifts:
            if shift.crosses_midnight:
                if clock >= shift.start_time or clock < shift.end_time:
                    return shift
            elif shift.start_time <= clock < shift.end_time:
                return shift
        return self.production_shifts[0]

    def is_on_shift(self, recipient: Recipient, moment: datetime) -> bool:
        """Whether the recipient's own shift covers this instant."""
        shift = self.shifts.get(recipient.shift_id)
        if shift is None:
            return False
        clock = moment.astimezone(self.timezone).time()
        if shift.crosses_midnight:
            return clock >= shift.start_time or clock < shift.end_time
        return shift.start_time <= clock < shift.end_time

    def escalation_email_for(self, machine: Machine) -> str | None:
        """The departmental fallback address §E20 rule 7 names."""
        line = self.lines.get(machine.production_line_id)
        if line is None:
            return None
        department = self.departments.get(line.department_id)
        return None if department is None else department.escalation_email


class NotificationPolicy:
    """The two ``business_rule`` values the service applies."""

    def __init__(self, session: Session) -> None:
        self.quiet_hours_enabled = False
        self.quiet_hours_rule: str | None = None
        self.retry_limit = 1
        self.retry_limit_rule: str | None = None
        self.retry_policy_missing = True

        for rule in session.scalars(
            select(BusinessRule).where(
                BusinessRule.rule_category == CATEGORY_NOTIFICATION,
                BusinessRule.is_active.is_(True),
            ).order_by(BusinessRule.business_rule_code)
        ):
            if rule.business_rule_code == RULE_QUIET_HOURS:
                self.quiet_hours_enabled = bool(rule.value_boolean)
                self.quiet_hours_rule = rule.business_rule_code
            elif rule.business_rule_code == RULE_RETRY_MAX:
                if rule.value_numeric is not None:
                    self.retry_limit = max(int(rule.value_numeric), 1)
                    self.retry_limit_rule = rule.business_rule_code
                    self.retry_policy_missing = False

    @property
    def retry_note(self) -> str:
        """What the report says about retries, either way."""
        if self.retry_policy_missing:
            return (
                "retries disabled: §E21 rule 5 requires the cap to come from "
                "business_rule and no active rule %r exists, so one attempt is made and "
                "no number is invented" % RULE_RETRY_MAX
            )
        return "retry cap %d attempt(s) from %s" % (
            self.retry_limit, self.retry_limit_rule)


class NotificationContext:
    """Master data, the notification policy, and the message code sequence."""

    def __init__(self, session: Session) -> None:
        self.master = MasterSnapshot(session)
        self.policy = NotificationPolicy(session)
        self._codes: dict[str, int] = {}
        for existing in session.scalars(select(Notification.notification_code)):
            scope, _, suffix = str(existing).rpartition("-")
            try:
                value = int(suffix)
            except ValueError:
                continue
            self._codes[scope] = max(self._codes.get(scope, 0), value)

    def notification_code(self, moment: datetime) -> str:
        """``NTF-<yyyymmdd>-<nnnnn>``, per §3.1."""
        key = "NTF-%s" % moment.astimezone(self.master.timezone).strftime("%Y%m%d")
        nxt = self._codes.get(key, 0) + 1
        self._codes[key] = nxt
        return "%s-%05d" % (key, nxt)
