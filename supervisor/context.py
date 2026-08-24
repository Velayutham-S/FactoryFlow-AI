"""Shared state: the master snapshot, the escalation policy, and the ``CTX-`` sequence.

**The escalation policy is entirely ``business_rule`` data.** §11.4 gives the resolution
order: "a ``business_rule`` scoped to the affected line, falling back to global", and §29
rule 3 confirms it is "exactly two levels, no deeper hierarchy". Nothing about a
particular line is known to this code -- §29's own commentary makes that the point:
"a 0.60 probability on Line 03 is recorded and monitored but does not reach the Decision
Agent. The same 0.60 probability on Line 01 escalates... **Nothing in the code knows about
Line 01.**"

Rules are resolved by ``rule_category`` and scope rather than by assembling a code string,
so renaming ``BR-ESC-PROB-LN01`` does not break the lookup. The resolved row is carried
whole so its identifier can be written to
``supervisor_context.applied_escalation_rule_id`` -- E17 is explicit that no value is
copied: "``applied_escalation_rule_id`` references the ``business_rule`` row that governed
the decision, and nothing more", which is sufficient because master data preserves
superseded values as new rows.

This snapshot exists rather than being borrowed from a neighbour on purpose. §16.3:
"Components interact through defined inputs and outputs, not through shared internals."
Importing the Decision Agent's snapshot would also point a dependency backwards along the
pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.master import (
    BusinessRule,
    Customer,
    FailureCategory,
    FailureSeverityLevel,
    InventoryItem,
    InventoryLocation,
    Machine,
    MachineMaintenanceSchedule,
    MachineParameter,
    MachineType,
    MachineTypeFailureMode,
    MachineTypeParameter,
    MaintenanceEngineer,
    MaintenanceTeam,
    NotificationRecipient,
    Plant,
    Product,
    ProductionLine,
    Shift,
    Worker,
)
from models.operational import SupervisorContext as SupervisorContextRow

from supervisor.errors import (
    EscalationPolicyMissingError,
    MasterDataUnavailableError,
)

SUPERVISOR_COMPONENT = "supervisor_agent"

# The escalation category. §29's consumer table names the Supervisor Agent its "primary
# consumer ... This is where cost and noise are controlled".
CATEGORY_ESCALATION = "escalation"
CATEGORY_COSTING = "costing"
CATEGORY_PRIORITIZATION = "prioritization"

# Alert statuses that represent a live case worth evaluating.
LIVE_ALERT_STATUSES = ("open", "acknowledged", "escalated")

# Work statuses that mean a repair is under way on the machine (E17 rule 6).
OPEN_WORK_STATUSES = ("open", "assigned", "in_progress", "awaiting_parts", "completed")


@dataclass(frozen=True)
class PolicyRule:
    """One resolved ``business_rule`` row, carried so it can be referenced and cited."""

    business_rule_id: int
    code: str
    name: str
    numeric: float | None
    text: str | None
    unit: str | None
    line_scoped: bool

    def cite(self) -> str:
        if self.numeric is not None:
            amount = ("%.4f" % self.numeric).rstrip("0").rstrip(".")
            return "%s%s (%s)" % (
                amount, "" if not self.unit else " " + self.unit, self.code)
        return "%s (%s)" % (self.text, self.code)


class MasterSnapshot:
    """The master rows the orchestrator resolves against, loaded once and indexed."""

    def __init__(self, session: Session) -> None:
        plant = session.scalars(select(Plant)).first()
        if plant is None:
            raise MasterDataUnavailableError("no plant row; master data is not seeded")
        self.plant = plant
        self.timezone = ZoneInfo(plant.timezone)

        self.machines: dict[int, Machine] = {
            m.machine_id: m for m in session.scalars(select(Machine))
        }
        self.machine_types: dict[int, MachineType] = {
            t.machine_type_id: t for t in session.scalars(select(MachineType))
        }
        self.lines: dict[int, ProductionLine] = {
            line.production_line_id: line
            for line in session.scalars(select(ProductionLine))
        }
        self.lines_by_code: dict[str, ProductionLine] = {
            line.production_line_code: line for line in self.lines.values()
        }

        self.severities: dict[int, FailureSeverityLevel] = {}
        self.severities_by_code: dict[str, FailureSeverityLevel] = {}
        for level in session.scalars(select(FailureSeverityLevel)):
            self.severities[level.failure_severity_level_id] = level
            self.severities_by_code[level.failure_severity_level_code] = level
        if not self.severities:
            raise MasterDataUnavailableError("no failure_severity_level rows")

        self.failure_categories: dict[int, FailureCategory] = {
            c.failure_category_id: c for c in session.scalars(select(FailureCategory))
        }
        self.parameters: dict[int, MachineParameter] = {
            p.machine_parameter_id: p for p in session.scalars(select(MachineParameter))
        }

        # Healthy envelope per machine type, for the readings block's healthy_max.
        self.declarations: dict[tuple[int, int], MachineTypeParameter] = {}
        for declaration in session.scalars(select(MachineTypeParameter)):
            if declaration.is_active:
                self.declarations[
                    (declaration.machine_type_id, declaration.machine_parameter_id)
                ] = declaration

        self.modes_by_type: dict[int, list[MachineTypeFailureMode]] = {}
        for mode in session.scalars(select(MachineTypeFailureMode)):
            if mode.is_active:
                self.modes_by_type.setdefault(mode.machine_type_id, []).append(mode)

        self.teams: dict[int, MaintenanceTeam] = {
            t.maintenance_team_id: t for t in session.scalars(select(MaintenanceTeam))
            if t.is_active
        }
        self.engineers: dict[int, MaintenanceEngineer] = {
            e.maintenance_engineer_id: e
            for e in session.scalars(select(MaintenanceEngineer)) if e.is_active
        }
        self.workers: dict[int, Worker] = {
            w.worker_id: w for w in session.scalars(select(Worker))
        }
        self.recipients: list[NotificationRecipient] = [
            r for r in session.scalars(select(NotificationRecipient)) if r.is_active
        ]

        self.items: dict[int, InventoryItem] = {
            i.inventory_item_id: i for i in session.scalars(select(InventoryItem))
        }
        self.locations: dict[int, InventoryLocation] = {
            loc.inventory_location_id: loc
            for loc in session.scalars(select(InventoryLocation))
        }
        self.customers: dict[int, Customer] = {
            c.customer_id: c for c in session.scalars(select(Customer))
        }
        self.products: dict[int, Product] = {
            p.product_id: p for p in session.scalars(select(Product))
        }

        self.schedules_by_machine: dict[int, list[MachineMaintenanceSchedule]] = {}
        for schedule in session.scalars(select(MachineMaintenanceSchedule)):
            if schedule.is_active:
                self.schedules_by_machine.setdefault(
                    schedule.machine_id, []).append(schedule)

        self.shifts = [
            s for s in session.scalars(select(Shift))
            if s.shift_type.value == "production" and s.is_active
        ]
        if not self.shifts:
            raise MasterDataUnavailableError("no active production shift")
        self.shifts.sort(key=lambda s: s.sequence_order)

        self.machines_on_line: dict[int, list[Machine]] = {}
        for machine in self.machines.values():
            self.machines_on_line.setdefault(
                machine.production_line_id, []).append(machine)
        for group in self.machines_on_line.values():
            group.sort(key=lambda m: m.line_position)

    # ------------------------------------------------------------------ helpers

    def shift_at(self, moment: datetime) -> Shift:
        clock = moment.astimezone(self.timezone).time()
        for shift in self.shifts:
            if shift.crosses_midnight:
                if clock >= shift.start_time or clock < shift.end_time:
                    return shift
            elif shift.start_time <= clock < shift.end_time:
                return shift
        return self.shifts[0]

    def downstream_of(self, machine: Machine) -> list[Machine]:
        """Machines after this one on the same line, in position order."""
        return [
            other for other in self.machines_on_line.get(machine.production_line_id, [])
            if other.line_position > machine.line_position
        ]

    def contribution_margin(self, product: Product) -> Decimal:
        return Decimal(product.standard_selling_price) - Decimal(
            product.standard_material_cost)


class SupervisorPolicy:
    """The escalation thresholds, resolved from ``business_rule``."""

    def __init__(self, session: Session, master: MasterSnapshot) -> None:
        self.master = master
        self._probability_global: PolicyRule | None = None
        self._probability_by_line: dict[int, PolicyRule] = {}
        self.severity_floor: PolicyRule | None = None
        self.severity_floor_rank: int | None = None
        self._costing_global: PolicyRule | None = None
        self._costing_by_line: dict[int, PolicyRule] = {}
        self.priority_weights: list[PolicyRule] = []

        for rule in session.scalars(
            select(BusinessRule).where(BusinessRule.is_active.is_(True))
            .order_by(BusinessRule.business_rule_code)
        ):
            resolved = PolicyRule(
                business_rule_id=rule.business_rule_id,
                code=rule.business_rule_code,
                name=rule.rule_name,
                numeric=None if rule.value_numeric is None else float(
                    rule.value_numeric),
                text=rule.value_text,
                unit=rule.unit,
                line_scoped=rule.production_line_id is not None,
            )
            category = rule.rule_category.value

            if category == CATEGORY_ESCALATION:
                if resolved.numeric is not None:
                    if rule.production_line_id is None:
                        self._probability_global = (
                            self._probability_global or resolved)
                    else:
                        self._probability_by_line.setdefault(
                            rule.production_line_id, resolved)
                elif resolved.text and self.severity_floor is None:
                    level = master.severities_by_code.get(resolved.text.strip().upper())
                    if level is not None:
                        self.severity_floor = resolved
                        self.severity_floor_rank = level.severity_rank
            elif category == CATEGORY_COSTING and resolved.numeric is not None:
                if rule.production_line_id is None:
                    self._costing_global = self._costing_global or resolved
                else:
                    self._costing_by_line.setdefault(
                        rule.production_line_id, resolved)
            elif category == CATEGORY_PRIORITIZATION and resolved.numeric is not None:
                self.priority_weights.append(resolved)

        if self._probability_global is None and not self._probability_by_line:
            raise EscalationPolicyMissingError(
                "no active numeric business_rule with rule_category = 'escalation'. "
                "§11.4 makes the probability threshold the first step of the escalation "
                "test and E17 rule 4 requires the governing rule to be named on the "
                "row, so the Supervisor will not substitute a threshold."
            )

    def probability_threshold(
        self,
        production_line_id: int | None,
    ) -> PolicyRule | None:
        """The two-step resolution of §11.4: line-scoped, then global."""
        if production_line_id is not None:
            scoped = self._probability_by_line.get(production_line_id)
            if scoped is not None:
                return scoped
        return self._probability_global

    def downtime_cost(self, production_line_id: int | None) -> PolicyRule | None:
        if production_line_id is not None:
            scoped = self._costing_by_line.get(production_line_id)
            if scoped is not None:
                return scoped
        return self._costing_global


class SupervisorContext:
    """Master data, the escalation policy, and the context code sequence."""

    def __init__(self, session: Session) -> None:
        self.master = MasterSnapshot(session)
        self.policy = SupervisorPolicy(session, self.master)
        self._codes: dict[str, int] = {}
        for existing in session.scalars(
            select(SupervisorContextRow.supervisor_context_code)
        ):
            scope, _, suffix = str(existing).rpartition("-")
            try:
                value = int(suffix)
            except ValueError:
                continue
            self._codes[scope] = max(self._codes.get(scope, 0), value)

    def context_code(self, moment: datetime) -> str:
        """``CTX-<yyyymmdd>-<nnnn>``, per §3.1."""
        key = "CTX-%s" % moment.astimezone(self.master.timezone).strftime("%Y%m%d")
        nxt = self._codes.get(key, 0) + 1
        self._codes[key] = nxt
        return "%s-%04d" % (key, nxt)
