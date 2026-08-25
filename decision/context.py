"""Shared state: the master snapshot, the policy rules, and the ``REC-`` sequence.

**What this agent reads, and the one documentation tension worth naming.**

§11.5 (Transition T4) says the Decision Agent's input is "one escalated
``supervisor_context`` -- its ``context_document`` and nothing else", and that "the
Decision Agent issues no queries". §31 of the master document says the opposite-sounding
thing: "Decision Agent | 29 | **Everything.** It is the only component that touches every
entity."

Both hold once you separate *reasoning input* from *foreign key resolution*:

* **No operational query is issued.** Every operational value the agent reasons over --
  machine state, run, customer, buffer, stock, schedules, evidence -- arrives already
  resolved inside ``context_document``, which is exactly why §E17 calls that payload "the
  input side of the explainability contract".
* **Master data is read**, because ``ai_recommendation`` stores eleven integer foreign
  keys and the context document holds business *codes* (``MC-0101``, ``FC-BRG``,
  ``MTM-MECH``). Turning a code into a key is not reasoning over data; it is writing a
  row. Master data is also where the root-cause vocabulary lives, and §E18 rule 3
  requires the classification to be constrained to the modes declared for the machine's
  type -- a constraint that cannot be enforced without reading
  ``machine_type_failure_mode``.

**Every policy value comes from ``business_rule``.** §29's consumer table gives this agent
two dependencies -- "reads downtime cost rates to quantify impact, and prioritization
weights to rank competing risks" -- and adds that it "**cites rule codes in reasoning**".
So each resolved rule is carried as an :class:`AppliedRule` with its code, and those codes
travel into the stored evidence and the narrative. Nothing is defaulted in code: §29 rule
4 states that a consumer with no policy "defaulting silently in code would defeat the
purpose of this entity".

Downtime cost resolution is the documented two-step of §29 rule 3 -- a rule scoped to the
affected line, falling back to the global rule -- expressed as a query on
``rule_category`` and ``production_line_id`` rather than by assembling a code string, so a
renamed rule still resolves.
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
    MachineType,
    MachineTypeFailureMode,
    MaintenanceEngineer,
    MaintenanceTeam,
    Plant,
    Product,
    ProductionLine,
    ProductLineCapability,
    Shift,
    Worker,
)
from models.operational import AiRecommendation

from decision.errors import MasterDataUnavailableError

DECISION_COMPONENT = "decision_agent"

# Prioritisation weights, by code. Both codes are transcribed from §29's Example records
# and both exist in the seeded master set; neither value is written here.
RULE_PRIORITY_GOLD = "BR-PRIOR-GOLD"
RULE_PRIORITY_SAFETY = "BR-PRIOR-SAFETY"

# Maintenance policy consulted when the recovery plan proposes combining the repair with
# a due preventive service.
RULE_DEFER_MAX = "BR-MAINT-DEFER-MAX"

# The probability-to-severity band floors already in ``business_rule``. Reused rather
# than duplicated -- see ``impact.priority_for`` for why.
RULE_RISK_PREFIX = "BR-PRED-RISK-"

CATEGORY_COSTING = "costing"
CATEGORY_PRIORITIZATION = "prioritization"

# The seven blocks §E17 requires in an escalated context document (rule 10).
CONTEXT_BLOCKS = (
    "machine", "production", "cascade", "inventory", "maintenance", "business",
    "evidence",
)


@dataclass(frozen=True)
class AppliedRule:
    """One resolved ``business_rule`` row, carried so its code can be cited."""

    business_rule_id: int
    code: str
    name: str
    value: float
    unit: str | None
    line_scoped: bool

    def cite(self) -> str:
        """``42000.0 INR/hour (BR-COST-DOWN-LN01)`` -- the form used in narrative."""
        amount = ("%.4f" % self.value).rstrip("0").rstrip(".")
        return "%s%s (%s)" % (
            amount, "" if not self.unit else " " + self.unit, self.code)


@dataclass(frozen=True)
class RiskBand:
    """One probability cut-off mapping onto a severity level."""

    severity_level_id: int
    severity_code: str
    severity_rank: int
    minimum_probability: float
    business_rule_id: int
    code: str


class MasterSnapshot:
    """Every master row the agent resolves against, loaded once and indexed.

    Indexed by code as well as by id, because the context document names things the way
    a human does and the row stores integer keys.
    """

    def __init__(self, session: Session) -> None:
        plant = session.scalars(select(Plant)).first()
        if plant is None:
            raise MasterDataUnavailableError("no plant row; master data is not seeded")
        self.plant = plant
        self.timezone = ZoneInfo(plant.timezone)

        self.machines: dict[int, Machine] = {}
        self.machines_by_code: dict[str, Machine] = {}
        for machine in session.scalars(select(Machine)):
            self.machines[machine.machine_id] = machine
            self.machines_by_code[machine.machine_code] = machine

        self.machine_types: dict[int, MachineType] = {
            t.machine_type_id: t for t in session.scalars(select(MachineType))
        }

        self.lines: dict[int, ProductionLine] = {}
        self.lines_by_code: dict[str, ProductionLine] = {}
        for line in session.scalars(select(ProductionLine)):
            self.lines[line.production_line_id] = line
            self.lines_by_code[line.production_line_code] = line

        self.severities: dict[int, FailureSeverityLevel] = {}
        self.severities_by_code: dict[str, FailureSeverityLevel] = {}
        for level in session.scalars(select(FailureSeverityLevel)):
            self.severities[level.failure_severity_level_id] = level
            self.severities_by_code[level.failure_severity_level_code] = level
        if not self.severities:
            raise MasterDataUnavailableError("no failure_severity_level rows")

        self.failure_categories: dict[int, FailureCategory] = {}
        self.failure_categories_by_code: dict[str, FailureCategory] = {}
        for category in session.scalars(select(FailureCategory)):
            if not category.is_active:
                continue
            self.failure_categories[category.failure_category_id] = category
            self.failure_categories_by_code[category.failure_category_code] = category
        if not self.failure_categories:
            raise MasterDataUnavailableError(
                "no active failure_category rows; the root-cause vocabulary is empty "
                "and §E18 rule 3 requires the classification to come from it")

        # Declared failure modes per machine type. This is the root-cause candidate set
        # (§E18 rule 3) and the source of the part, repair duration and warning period.
        self.modes_by_type: dict[int, list[MachineTypeFailureMode]] = {}
        for mode in session.scalars(select(MachineTypeFailureMode)):
            if mode.is_active:
                self.modes_by_type.setdefault(mode.machine_type_id, []).append(mode)
        for modes in self.modes_by_type.values():
            modes.sort(key=lambda m: m.machine_type_failure_mode_id)

        self.teams: dict[int, MaintenanceTeam] = {}
        self.teams_by_code: dict[str, MaintenanceTeam] = {}
        for team in session.scalars(select(MaintenanceTeam)):
            if not team.is_active:
                continue
            self.teams[team.maintenance_team_id] = team
            self.teams_by_code[team.maintenance_team_code] = team

        self.engineers: dict[int, MaintenanceEngineer] = {}
        self.engineers_by_code: dict[str, MaintenanceEngineer] = {}
        self.engineers_by_team: dict[int, list[MaintenanceEngineer]] = {}
        for engineer in session.scalars(select(MaintenanceEngineer)):
            if not engineer.is_active:
                continue
            self.engineers[engineer.maintenance_engineer_id] = engineer
            self.engineers_by_code[engineer.maintenance_engineer_code] = engineer
            self.engineers_by_team.setdefault(
                engineer.maintenance_team_id, []).append(engineer)
        for pool in self.engineers_by_team.values():
            pool.sort(key=lambda e: e.maintenance_engineer_code)

        self.workers: dict[int, Worker] = {
            w.worker_id: w for w in session.scalars(select(Worker))
        }

        self.items: dict[int, InventoryItem] = {}
        self.items_by_code: dict[str, InventoryItem] = {}
        for item in session.scalars(select(InventoryItem)):
            self.items[item.inventory_item_id] = item
            self.items_by_code[item.inventory_item_code] = item

        self.locations: dict[int, InventoryLocation] = {
            loc.inventory_location_id: loc
            for loc in session.scalars(select(InventoryLocation))
        }

        self.customers: dict[int, Customer] = {}
        self.customers_by_code: dict[str, Customer] = {}
        for customer in session.scalars(select(Customer)):
            self.customers[customer.customer_id] = customer
            self.customers_by_code[customer.customer_code] = customer

        self.products: dict[int, Product] = {}
        self.products_by_code: dict[str, Product] = {}
        for product in session.scalars(select(Product)):
            self.products[product.product_id] = product
            self.products_by_code[product.product_code] = product

        # Reroute options: §E18's business impact block reports alternative production
        # routes, and the honest answer is often none.
        self.capabilities_by_product: dict[int, list[ProductLineCapability]] = {}
        for capability in session.scalars(select(ProductLineCapability)):
            if capability.is_active:
                self.capabilities_by_product.setdefault(
                    capability.product_id, []).append(capability)

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

    # ------------------------------------------------------------------ helpers

    def shift_at(self, moment: datetime) -> Shift:
        """The production shift containing an instant, in plant local time."""
        clock = moment.astimezone(self.timezone).time()
        for shift in self.shifts:
            if shift.crosses_midnight:
                if clock >= shift.start_time or clock < shift.end_time:
                    return shift
            elif shift.start_time <= clock < shift.end_time:
                return shift
        return self.shifts[0]

    def modes_for_machine(self, machine: Machine) -> list[MachineTypeFailureMode]:
        """The failure modes declared for this machine's type.

        §E18 rule 3: the root cause "should be one declared for this machine's type in
        ``machine_type_failure_mode``. A root cause implausible for the equipment is a
        reasoning failure worth detecting."
        """
        return self.modes_by_type.get(machine.machine_type_id, [])

    def contribution_margin(self, product: Product) -> Decimal:
        """Selling price less material cost.

        The margin the §E18 worked example puts at 2,710 per unit is not a stored
        column; it is this subtraction, and both operands are documented master data.
        """
        return Decimal(product.standard_selling_price) - Decimal(
            product.standard_material_cost)


class DecisionContext:
    """Master data, the resolved policy rules, and the recommendation sequence."""

    def __init__(self, session: Session) -> None:
        self.master = MasterSnapshot(session)
        self._codes: dict[str, int] = {}
        self._load_code_counters(session)

        self._costing_global: AppliedRule | None = None
        self._costing_by_line: dict[int, AppliedRule] = {}
        self._load_costing(session)

        self.gold_weight = self._optional_rule(session, RULE_PRIORITY_GOLD)
        self.safety_weight = self._optional_rule(session, RULE_PRIORITY_SAFETY)
        self.defer_max_days = self._optional_rule(session, RULE_DEFER_MAX)
        self._require_prioritization_policy(session)

        self.risk_bands = self._load_risk_bands(session)

    # ------------------------------------------------------------------- policy

    def _load_costing(self, session: Session) -> None:
        """Downtime cost rates, indexed for the §29 rule 3 two-step resolution."""
        for rule in session.scalars(
            select(BusinessRule).where(
                BusinessRule.rule_category == CATEGORY_COSTING,
                BusinessRule.is_active.is_(True),
            ).order_by(BusinessRule.business_rule_code)
        ):
            if rule.value_numeric is None:
                continue
            applied = AppliedRule(
                business_rule_id=rule.business_rule_id,
                code=rule.business_rule_code,
                name=rule.rule_name,
                value=float(rule.value_numeric),
                unit=rule.unit,
                line_scoped=rule.production_line_id is not None,
            )
            if rule.production_line_id is None:
                if self._costing_global is None:
                    self._costing_global = applied
            else:
                self._costing_by_line.setdefault(rule.production_line_id, applied)

        if self._costing_global is None and not self._costing_by_line:
            raise MasterDataUnavailableError(
                "no active numeric business_rule with rule_category = 'costing'. §29 "
                "rule 4 requires every category a consumer depends on to have at least "
                "one active global rule, and §29's consumer table makes downtime cost "
                "this agent's dependency for quantifying impact. The agent will not "
                "substitute a rate."
            )

    def downtime_cost_for(self, production_line_id: int) -> AppliedRule | None:
        """The line's downtime cost rate, falling back to the global rate.

        §29 rule 3: "look for a rule scoped to the relevant line; if none exists, use
        the global rule. Exactly two levels, no deeper hierarchy."
        """
        return self._costing_by_line.get(production_line_id, self._costing_global)

    def _optional_rule(self, session: Session, code: str) -> AppliedRule | None:
        rule = session.scalars(
            select(BusinessRule).where(
                BusinessRule.business_rule_code == code,
                BusinessRule.is_active.is_(True),
            )
        ).first()
        if rule is None or rule.value_numeric is None:
            return None
        return AppliedRule(
            business_rule_id=rule.business_rule_id,
            code=rule.business_rule_code,
            name=rule.rule_name,
            value=float(rule.value_numeric),
            unit=rule.unit,
            line_scoped=rule.production_line_id is not None,
        )

    def _require_prioritization_policy(self, session: Session) -> None:
        """§29 rule 4, applied to the second category this agent depends on."""
        present = session.scalars(
            select(BusinessRule).where(
                BusinessRule.rule_category == CATEGORY_PRIORITIZATION,
                BusinessRule.is_active.is_(True),
            )
        ).first()
        if present is None:
            raise MasterDataUnavailableError(
                "no active business_rule with rule_category = 'prioritization'. §29's "
                "consumer table makes prioritisation weights this agent's second "
                "dependency, and §29 rule 4 forbids defaulting silently in code."
            )

    def _load_risk_bands(self, session: Session) -> list[RiskBand]:
        """The probability-to-severity floors, most severe first."""
        bands: list[RiskBand] = []
        for rule in session.scalars(
            select(BusinessRule).where(
                BusinessRule.business_rule_code.like(RULE_RISK_PREFIX + "%"),
                BusinessRule.is_active.is_(True),
            )
        ):
            if rule.value_numeric is None:
                continue
            suffix = rule.business_rule_code[len(RULE_RISK_PREFIX):].upper()
            level = next(
                (s for s in self.master.severities.values()
                 if s.failure_severity_level_code.upper() == suffix
                 or s.failure_severity_level_code.upper().endswith(suffix)),
                None,
            )
            if level is None:
                continue
            bands.append(RiskBand(
                severity_level_id=level.failure_severity_level_id,
                severity_code=level.failure_severity_level_code,
                severity_rank=level.severity_rank,
                minimum_probability=float(rule.value_numeric),
                business_rule_id=rule.business_rule_id,
                code=rule.business_rule_code,
            ))
        if not bands:
            raise MasterDataUnavailableError(
                "no active business_rule rows matching %r supplying the "
                "probability-to-severity floors. Priority is derived from them, and a "
                "hardcoded mapping is forbidden, so the agent will not substitute one."
                % (RULE_RISK_PREFIX + "*")
            )
        bands.sort(key=lambda b: b.minimum_probability, reverse=True)
        return bands

    def band_for(self, score: float) -> RiskBand:
        """The band a weighted risk score falls into. First match wins."""
        for band in self.risk_bands:
            if score >= band.minimum_probability:
                return band
        return self.risk_bands[-1]

    # -------------------------------------------------------------------- codes

    def _load_code_counters(self, session: Session) -> None:
        for existing in session.scalars(
            select(AiRecommendation.ai_recommendation_code)
        ):
            scope, _, suffix = str(existing).rpartition("-")
            try:
                value = int(suffix)
            except ValueError:
                continue
            self._codes[scope] = max(self._codes.get(scope, 0), value)

    def recommendation_code(self, moment: datetime) -> str:
        """``REC-<yyyymmdd>-<nnnn>``, per §3.1."""
        key = "REC-%s" % moment.astimezone(self.master.timezone).strftime("%Y%m%d")
        nxt = self._codes.get(key, 0) + 1
        self._codes[key] = nxt
        return "%s-%04d" % (key, nxt)
