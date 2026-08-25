"""Contract element 5's concrete parts: who attends, with what part, by when.

Four documented rules drive this module, and each is arithmetic or a filter, so none of
it involves the LLM:

* **Rule 7 — the part is derived, not guessed.** ``required_inventory_item_id`` "must come
  from the predicted failure mode's ``required_inventory_item_id``".
* **Rule 8 — downtime is a sum of two master values.** ``estimated_downtime_minutes``
  "should equal the failure mode's ``estimated_repair_duration_minutes`` plus the part
  location's ``average_retrieval_time_minutes``. Both are master data; the sum is the
  honest estimate."
* **Rule 6 — the engineer must actually be able to attend.** They "must belong to the
  suggested team, hold valid certification, and be on shift or ``is_on_call``. The
  platform must never recommend an engineer who cannot legally or practically attend."
* **Rule 9 — the deadline must beat the starvation point.** ``recommended_action_by``
  "must account for the grace period from ``machine.downstream_buffer_units`` and the
  current rate. A deadline later than the line will starve is not a usable deadline."

Rejected engineer candidates are kept with their reason. That is not diagnostics: §16.5's
practical test is that "why did it say that?" be answerable from stored data, and *why
not the other engineer* is part of the same question.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from models.master import (
    InventoryItem,
    Machine,
    MaintenanceEngineer,
    MaintenanceTeam,
)

from decision.context import DecisionContext, MasterSnapshot
from decision.evidence import Candidate, PredictionFacts

# Minimum gap the deadline must leave after generation, so
# ck_ar_action_deadline_after_generation can never be violated by rounding.
MINIMUM_DEADLINE_MINUTES = 1


@dataclass
class Assignment:
    """The maintenance, inventory and timing decisions, with their justification."""

    team: MaintenanceTeam | None = None
    engineer: MaintenanceEngineer | None = None
    engineer_rejections: list[dict[str, str]] = field(default_factory=list)
    item: InventoryItem | None = None
    quantity_on_hand: float | None = None
    part_in_stock: bool | None = None
    retrieval_minutes: int | None = None
    repair_minutes: int | None = None
    estimated_downtime_minutes: int | None = None
    action_by: datetime | None = None
    deadline_basis: str = ""
    combinable_schedule: dict[str, Any] | None = None
    lead_time_days: int | None = None

    @property
    def team_id(self) -> int | None:
        return None if self.team is None else self.team.maintenance_team_id

    @property
    def engineer_id(self) -> int | None:
        return None if self.engineer is None else self.engineer.maintenance_engineer_id

    @property
    def item_id(self) -> int | None:
        return None if self.item is None else self.item.inventory_item_id


def resolve(
    context: DecisionContext,
    context_document: dict[str, Any],
    machine: Machine,
    facts: PredictionFacts,
    root_cause: Candidate | None,
    *,
    generated_at: datetime,
    grace_period_minutes: float | None,
) -> Assignment:
    """Decide the part, the downtime, the team, the engineer and the deadline."""
    master = context.master
    assignment = Assignment()

    inventory_block = _block(context_document, "inventory")
    maintenance_block = _block(context_document, "maintenance")

    _resolve_part_and_downtime(
        master, assignment, root_cause, inventory_block)
    _resolve_team(master, assignment, root_cause, generated_at)
    _resolve_engineer(master, assignment, root_cause, generated_at)
    _resolve_deadline(
        context, assignment, facts, root_cause,
        generated_at=generated_at, grace_period_minutes=grace_period_minutes)
    assignment.combinable_schedule = _combinable_schedule(
        context, maintenance_block)
    return assignment


# ------------------------------------------------------------- part and downtime


def _resolve_part_and_downtime(
    master: MasterSnapshot,
    assignment: Assignment,
    root_cause: Candidate | None,
    inventory_block: dict[str, Any],
) -> None:
    """Rules 7 and 8. The part comes from the mode; the downtime is a documented sum."""
    if root_cause is None:
        return

    mode = root_cause.mode
    assignment.repair_minutes = int(mode.estimated_repair_duration_minutes)

    if mode.required_inventory_item_id is not None:
        assignment.item = master.items.get(mode.required_inventory_item_id)

    if assignment.item is not None:
        location = master.locations.get(assignment.item.default_inventory_location_id)
        if location is not None:
            assignment.retrieval_minutes = int(location.average_retrieval_time_minutes)
        assignment.lead_time_days = int(assignment.item.lead_time_days)

        # Stock position comes from the context document: it is operational state, and
        # the Supervisor Agent resolved it at the decision moment.
        on_hand = _number(inventory_block.get("quantity_on_hand"))
        if on_hand is None:
            on_hand = _number(inventory_block.get("on_hand"))
        if on_hand is not None:
            assignment.quantity_on_hand = on_hand
            assignment.part_in_stock = on_hand > 0

    total = (assignment.repair_minutes or 0) + (assignment.retrieval_minutes or 0)
    assignment.estimated_downtime_minutes = total if total > 0 else None


# ----------------------------------------------------------------- team and person


def _resolve_team(
    master: MasterSnapshot,
    assignment: Assignment,
    root_cause: Candidate | None,
    generated_at: datetime,
) -> None:
    """Match the failure's required specialisation, then shift availability.

    §15's consumer note for the Decision Agent: "Matches specialization to the failure,
    checks shift availability and emergency eligibility, and states the expected response
    time."
    """
    if root_cause is None or not master.teams:
        return

    specialization = root_cause.required_specialization
    shift = master.shift_at(generated_at)

    def rank(team: MaintenanceTeam) -> tuple[int, int, int, str]:
        return (
            0 if team.specialization.value == specialization else 1,
            0 if team.shift_id == shift.shift_id else 1,
            0 if team.is_emergency_response else 1,
            team.maintenance_team_code,
        )

    matching = [
        team for team in master.teams.values()
        if team.specialization.value in (specialization, "general")
    ]
    pool = matching or list(master.teams.values())
    assignment.team = sorted(pool, key=rank)[0]


def _resolve_engineer(
    master: MasterSnapshot,
    assignment: Assignment,
    root_cause: Candidate | None,
    generated_at: datetime,
) -> None:
    """Rule 6. Every exclusion is recorded with its reason.

    §16's consumer note: "Verifies certification, matches discipline, checks on-call
    eligibility, and falls back to a cross-trained engineer when the primary specialist
    is unavailable."
    """
    if assignment.team is None:
        return

    today = generated_at.astimezone(master.timezone).date()
    shift = master.shift_at(generated_at)
    specialization = None if root_cause is None else root_cause.required_specialization
    eligible: list[MaintenanceEngineer] = []

    for engineer in master.engineers_by_team.get(assignment.team.maintenance_team_id, []):
        reason = _ineligible_reason(master, engineer, today, shift.shift_id)
        if reason is not None:
            assignment.engineer_rejections.append({
                "engineer": engineer.maintenance_engineer_code,
                "reason": reason,
            })
            continue
        eligible.append(engineer)

    if not eligible:
        return

    def rank(engineer: MaintenanceEngineer) -> tuple[int, int, int, str]:
        primary = engineer.primary_specialization.value
        secondary = (
            None if engineer.secondary_specialization is None
            else engineer.secondary_specialization.value
        )
        if specialization is None:
            discipline = 1
        elif primary == specialization:
            discipline = 0
        elif secondary == specialization:
            discipline = 1
        else:
            discipline = 2
        return (
            discipline,
            0 if engineer.is_team_lead else 1,
            -int(engineer.years_experience),
            engineer.maintenance_engineer_code,
        )

    assignment.engineer = sorted(eligible, key=rank)[0]


def _ineligible_reason(
    master: MasterSnapshot,
    engineer: MaintenanceEngineer,
    today: date,
    shift_id: int,
) -> str | None:
    """Why this engineer cannot be recommended, or ``None`` when they can."""
    if (
        engineer.certification_expiry_date is not None
        and engineer.certification_expiry_date < today
    ):
        return "certification expired %s" % engineer.certification_expiry_date

    worker = master.workers.get(engineer.worker_id)
    if worker is None:
        return "no worker record"
    if not worker.is_active:
        return "worker is not active"
    if worker.shift_id != shift_id and not engineer.is_on_call:
        return "off shift and not on call"
    return None


# ----------------------------------------------------------------------- deadline


def _resolve_deadline(
    context: DecisionContext,
    assignment: Assignment,
    facts: PredictionFacts,
    root_cause: Candidate | None,
    *,
    generated_at: datetime,
    grace_period_minutes: float | None,
) -> None:
    """Rule 9, built from three documented quantities and the shift pattern.

    The window opens at the prediction's own horizon -- the period the probability
    applies to -- and is then pulled in twice: by the grace period, so that a failure
    occurring at the deadline is still covered by the downstream buffer, and by the
    severity level's ``target_response_time_minutes``, which §23 describes as what
    "converts a severity label into a deadline the Decision Agent can state".

    Finally the deadline is pulled back to the latest crew change inside the window when
    one exists. §E18's worked recommendation does exactly that -- "at the 06:00 shift
    change ... Do not stop the line mid-batch" -- and a planned stop at a shift boundary
    costs the line less than one mid-batch.
    """
    master = context.master
    horizon_hours = facts.prediction_horizon_hours
    if root_cause is not None and root_cause.mode.typical_warning_period_hours:
        horizon_hours = min(
            horizon_hours, int(root_cause.mode.typical_warning_period_hours))

    limit = generated_at + timedelta(hours=max(horizon_hours, 0))
    basis = ["prediction horizon %dh" % horizon_hours]

    if grace_period_minutes:
        limit -= timedelta(minutes=float(grace_period_minutes))
        basis.append("less %.0f min grace period" % float(grace_period_minutes))

    severity = master.severities.get(facts.risk_severity_level_id)
    if severity is not None and severity.target_response_time_minutes:
        response_limit = generated_at + timedelta(
            minutes=int(severity.target_response_time_minutes))
        if response_limit < limit:
            limit = response_limit
            basis.append(
                "capped by %s response target %d min"
                % (severity.failure_severity_level_code,
                   int(severity.target_response_time_minutes)))

    floor = generated_at + timedelta(minutes=MINIMUM_DEADLINE_MINUTES)
    if limit <= floor:
        assignment.action_by = floor
        assignment.deadline_basis = (
            "immediate: %s leaves no usable window" % "; ".join(basis))
        return

    crew_change = _latest_shift_start(master, generated_at, limit)
    if crew_change is not None:
        assignment.action_by = crew_change
        basis.append("aligned to the %s crew change"
                     % crew_change.astimezone(master.timezone).strftime("%H:%M"))
    else:
        assignment.action_by = limit

    assignment.deadline_basis = "; ".join(basis)


def _latest_shift_start(
    master: MasterSnapshot,
    after: datetime,
    before: datetime,
) -> datetime | None:
    """The last production shift start strictly inside ``(after, before]``."""
    best: datetime | None = None
    local_after = after.astimezone(master.timezone)
    for day_offset in range(0, (before - after).days + 2):
        day = (local_after + timedelta(days=day_offset)).date()
        for shift in master.shifts:
            start = datetime.combine(day, shift.start_time, tzinfo=master.timezone)
            if after < start <= before and (best is None or start > best):
                best = start
    return best


# ------------------------------------------------------------- schedule combining


def _combinable_schedule(
    context: DecisionContext,
    maintenance_block: dict[str, Any],
) -> dict[str, Any] | None:
    """A due preventive job that could share the same stoppage.

    §E18 calls this "the most valuable thing in the whole document": combining the repair
    with a service that is already close costs one stoppage instead of two. The candidate
    comes from the context document's maintenance block, because how close a schedule is
    depends on operational counters.
    """
    due = maintenance_block.get("due_schedules")
    if not isinstance(due, list):
        return None
    for entry in due:
        if not isinstance(entry, dict):
            continue
        if not entry.get("requires_line_stop"):
            continue
        candidate = {
            "schedule": entry.get("schedule") or entry.get("code"),
            "due_in_operating_hours": entry.get("due_in_operating_hours"),
            "requires_line_stop": True,
            "can_be_deferred": entry.get("can_be_deferred"),
        }
        if context.defer_max_days is not None:
            candidate["max_deferral_rule"] = context.defer_max_days.cite()
        return candidate
    return None


# ------------------------------------------------------------------ small helpers


def _block(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
