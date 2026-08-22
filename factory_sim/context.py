"""Shared simulation state: the master data snapshot, the clock, the RNG, and the
per-machine runtime position.

The six engines and the simulator all need the same three things — what the factory
is (master data), what time it is, and where each machine currently stands. Passing
those as separate arguments to every engine call would mean threading eight
parameters through the whole layer, so they are collected here. This module holds
**no simulation behaviour**; every rule lives in the engine that owns it.

**The master snapshot is read-only and loaded once.** FACTORY_OPERATIONAL_DATA_DESIGN.md
§7.4 states that the simulator has no free parameters of its own and that every
characteristic it generates is governed by frozen master data. Loading it once, up
front, makes that literal: the engines read this snapshot and never query master
tables mid-cycle.

The snapshot holds **detached ORM instances**. ``models.session`` builds its session
factory with ``expire_on_commit=False``, so a loaded instance keeps its column values
after its session closes. Only mapped **column** attributes are read from these
objects — never a relationship, which on a detached instance would either emit a
query or raise. That restriction is the price of not copying 29 tables into parallel
dataclasses, and it is enforced by convention here rather than by a wrapper.

**Randomness is seeded and confined.** One ``random.Random`` instance lives here and
every engine draws from it. There is no module-level ``random`` use anywhere in the
layer, which is what makes a run reproducible from its seed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.master import (
    BillOfMaterials,
    Customer,
    FailureCategory,
    FailureSeverityLevel,
    InventoryItem,
    InventoryLocation,
    Machine,
    MachineCategory,
    MachineMaintenanceSchedule,
    MachineParameter,
    MachineType,
    MachineTypeFailureMode,
    MachineTypeParameter,
    MaintenanceEngineer,
    MaintenanceTeam,
    Plant,
    Product,
    ProductionLine,
    ProductLineCapability,
    Shift,
    Worker,
    WorkerRole,
)
from models.operational import (
    InventoryMovement,
    MaintenanceWorkRecord,
    ProductionRun,
    QualityInspectionResult,
)

from factory_sim.errors import MasterDataIncompleteError

# The component identity written to created_by_component on all 24 operational
# tables. NOT NULL with no default, so every insert must state it (§27).
SIMULATOR_COMPONENT = "simulator"

# Master maintenance_type -> operational work_type.
#
# `predictive` is deliberately absent from the values this maps onto:
# ck_mwr_predictive_requires_recommendation requires triggering_recommendation_id,
# and §6.5 forbids the simulator from reading ai_recommendation. The simulator
# therefore cannot originate predictive work, and a schedule declaring that type is
# executed as preventive.
WORK_TYPE_FOR_MAINTENANCE_TYPE = {
    "preventive": "preventive",
    "lubrication": "preventive",
    "predictive": "preventive",
    "calibration": "calibration",
    "inspection": "inspection",
}

# Relative weights for choosing which declared failure mode fires, from
# machine_type_failure_mode.relative_frequency.
FREQUENCY_WEIGHT = {"common": 6.0, "occasional": 3.0, "rare": 1.0}


@dataclass
class MachineRuntime:
    """Where one machine currently stands. Mirrors machine_operational_status."""

    machine_id: int
    state: str
    state_since: datetime
    run_id: int | None = None
    accumulated_operating_hours: Decimal = Decimal("0.00")
    accumulated_cycle_count: int = 0
    hours_at_last_maintenance: Decimal | None = None
    cycles_at_last_maintenance: int | None = None
    last_reading_at: datetime | None = None
    last_transition_id: int | None = None
    status_id: int | None = None

    reading_sequence: int = 0
    cycle_sequence: int = 0
    cycle_number_in_run: int = 0

    # Per-parameter next sampling instant, keyed by machine_parameter_id.
    next_reading_at: dict[int, datetime] = field(default_factory=dict)
    next_cycle_end_at: datetime | None = None

    # Failure incubation. When a mode is incubating, the Failure Engine drifts the
    # mode's primary parameter toward its limit and the machine breaks down at
    # ``failure_due_at``. Expressed as telemetry, never as an event (§7.5).
    incubating_mode: Any | None = None
    incubation_started_at: datetime | None = None
    failure_due_at: datetime | None = None

    open_work_record_id: int | None = None
    work_stage: str | None = None
    work_next_step_at: datetime | None = None

    cycles_since_inspection: int = 0

    @property
    def hours_since_maintenance(self) -> Decimal:
        base = self.hours_at_last_maintenance or Decimal("0.00")
        return self.accumulated_operating_hours - base

    @property
    def cycles_since_maintenance(self) -> int:
        return self.accumulated_cycle_count - (self.cycles_at_last_maintenance or 0)


@dataclass
class LineRuntime:
    """Where one production line currently stands."""

    line_id: int
    run_id: int | None = None
    run_status: str | None = None
    setup_until: datetime | None = None
    quantity_good: Decimal = Decimal("0.00")
    quantity_scrapped: Decimal = Decimal("0.00")
    quantity_rework: Decimal = Decimal("0.00")
    planned_quantity: Decimal = Decimal("0.00")
    downtime_seconds: int = 0
    elapsed_production_seconds: int = 0
    capability_id: int | None = None
    product_id: int | None = None
    actual_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    last_progress_at: datetime | None = None


class MasterSnapshot:
    """Every master row the simulator reads, loaded once and indexed.

    Raises :class:`MasterDataIncompleteError` when the factory cannot be simulated.
    The checks are the subset of §41.3's completeness rules that this layer actually
    depends on; each one, if violated, would make some generated row describe a
    factory that does not exist.
    """

    def __init__(self, session: Session) -> None:
        self.plant: Plant = session.scalars(select(Plant)).first()
        if self.plant is None:
            raise MasterDataIncompleteError("no plant row; master data is not seeded")
        self.timezone = ZoneInfo(self.plant.timezone)

        self.shifts: list[Shift] = list(session.scalars(select(Shift)))
        self.production_shifts = [
            s for s in self.shifts
            if s.shift_type.value == "production" and s.is_active
        ]
        if not self.production_shifts:
            raise MasterDataIncompleteError(
                "no active production shift; every operational row must reference "
                "the shift containing its event time"
            )

        self.machines: dict[int, Machine] = {
            m.machine_id: m for m in session.scalars(select(Machine))
        }
        if not self.machines:
            raise MasterDataIncompleteError("no machine rows; nothing to simulate")

        self.machine_types: dict[int, MachineType] = {
            t.machine_type_id: t for t in session.scalars(select(MachineType))
        }
        self.machine_categories: dict[int, MachineCategory] = {
            c.machine_category_id: c
            for c in session.scalars(select(MachineCategory))
        }
        self.lines: dict[int, ProductionLine] = {
            line.production_line_id: line
            for line in session.scalars(select(ProductionLine))
            if line.is_active
        }
        if not self.lines:
            raise MasterDataIncompleteError("no active production line")

        self.products: dict[int, Product] = {
            p.product_id: p for p in session.scalars(select(Product)) if p.is_active
        }
        self.customers: list[Customer] = [
            c for c in session.scalars(select(Customer)) if c.is_active
        ]
        if not self.customers:
            raise MasterDataIncompleteError(
                "no active customer; production_run.customer_id is mandatory and "
                "make-to-stock production is not modelled"
            )

        self.capabilities: list[ProductLineCapability] = [
            c for c in session.scalars(select(ProductLineCapability)) if c.is_active
        ]
        self.routes_by_line: dict[int, list[ProductLineCapability]] = {}
        for cap in self.capabilities:
            if cap.capability_type.value == "production_route":
                self.routes_by_line.setdefault(cap.production_line_id, []).append(cap)
        if not self.routes_by_line:
            raise MasterDataIncompleteError(
                "no active production_route capability; a run may not be scheduled "
                "onto a finishing stage"
            )

        self.parameters: dict[int, MachineParameter] = {
            p.machine_parameter_id: p for p in session.scalars(select(MachineParameter))
        }
        self.declarations_by_type: dict[int, list[MachineTypeParameter]] = {}
        for decl in session.scalars(select(MachineTypeParameter)):
            if decl.is_active:
                self.declarations_by_type.setdefault(
                    decl.machine_type_id, []).append(decl)

        self.failure_modes_by_type: dict[int, list[MachineTypeFailureMode]] = {}
        for mode in session.scalars(select(MachineTypeFailureMode)):
            if mode.is_active:
                self.failure_modes_by_type.setdefault(
                    mode.machine_type_id, []).append(mode)

        self.failure_categories: dict[int, FailureCategory] = {
            c.failure_category_id: c for c in session.scalars(select(FailureCategory))
        }
        self.severities: dict[int, FailureSeverityLevel] = {
            s.failure_severity_level_id: s
            for s in session.scalars(select(FailureSeverityLevel))
        }

        self.items: dict[int, InventoryItem] = {
            i.inventory_item_id: i
            for i in session.scalars(select(InventoryItem)) if i.is_active
        }
        self.locations: dict[int, InventoryLocation] = {
            loc.inventory_location_id: loc
            for loc in session.scalars(select(InventoryLocation)) if loc.is_active
        }
        self.bom_by_product: dict[int, list[BillOfMaterials]] = {}
        for line_item in session.scalars(select(BillOfMaterials)):
            if line_item.is_active:
                self.bom_by_product.setdefault(
                    line_item.product_id, []).append(line_item)

        self.schedules_by_machine: dict[int, list[MachineMaintenanceSchedule]] = {}
        for sched in session.scalars(select(MachineMaintenanceSchedule)):
            if sched.is_active:
                self.schedules_by_machine.setdefault(
                    sched.machine_id, []).append(sched)

        self.teams: dict[int, MaintenanceTeam] = {
            t.maintenance_team_id: t
            for t in session.scalars(select(MaintenanceTeam)) if t.is_active
        }
        self.engineers: list[MaintenanceEngineer] = [
            e for e in session.scalars(select(MaintenanceEngineer)) if e.is_active
        ]

        self.workers: dict[int, Worker] = {
            w.worker_id: w for w in session.scalars(select(Worker)) if w.is_active
        }
        roles = {r.worker_role_id: r for r in session.scalars(select(WorkerRole))}
        self.inspectors = [
            w for w in self.workers.values()
            if roles[w.worker_role_id].role_category.value in {"inspector", "manager"}
            or roles[w.worker_role_id].is_managerial
        ]
        self.storekeepers = [
            w for w in self.workers.values()
            if roles[w.worker_role_id].role_category.value == "storekeeper"
        ]
        self.operators = [
            w for w in self.workers.values()
            if roles[w.worker_role_id].role_category.value == "operator"
        ]
        if not self.inspectors:
            raise MasterDataIncompleteError(
                "no worker whose role permits inspection; "
                "quality_inspection_result.inspector_worker_id would be unfillable"
            )
        if not self.storekeepers:
            raise MasterDataIncompleteError(
                "no storekeeper; inventory_movement.recorded_by_worker_id would be "
                "unfillable"
            )

        self.machines_by_line: dict[int, list[Machine]] = {}
        for machine in self.machines.values():
            self.machines_by_line.setdefault(
                machine.production_line_id, []).append(machine)
        for group in self.machines_by_line.values():
            group.sort(key=lambda m: m.line_position)

    def declarations_for(self, machine: Machine) -> list[MachineTypeParameter]:
        """Parameters this machine's type declares.

        Empty when the type declares none, and the simulator then emits no telemetry
        for the machine — §7.5 forbids generating a parameter a type does not
        declare.
        """
        return self.declarations_by_type.get(machine.machine_type_id, [])

    def failure_modes_for(self, machine: Machine) -> list[MachineTypeFailureMode]:
        return self.failure_modes_by_type.get(machine.machine_type_id, [])

    def engineers_for_team(self, team_id: int) -> list[MaintenanceEngineer]:
        return [e for e in self.engineers if e.maintenance_team_id == team_id]

    def team_for_specialization(self, specialization: str) -> MaintenanceTeam | None:
        for team in self.teams.values():
            if team.specialization.value == specialization:
                return team
        return next(iter(self.teams.values()), None)


class SimulationContext:
    """The clock, the RNG, the master snapshot, and the runtime position."""

    def __init__(
        self,
        session: Session,
        *,
        start_at: datetime,
        seed: int,
        tick_seconds: int = 60,
    ) -> None:
        if start_at.tzinfo is None:
            raise ValueError(
                "start_at must be timezone-aware; the ORM stores UTC and rejects "
                "naive datetimes"
            )
        self.master = MasterSnapshot(session)
        self.rng = random.Random(seed)
        self.seed = seed
        self.tick_seconds = tick_seconds
        self.now = start_at.astimezone(timezone.utc)
        self.machines: dict[int, MachineRuntime] = {}
        self.lines: dict[int, LineRuntime] = {}
        self.balances: dict[int, Decimal] = {}
        self._code_counters: dict[str, int] = {}
        self._load_code_counters(session)

    # ------------------------------------------------------------------ clock

    def advance(self) -> None:
        self.now = self.now + timedelta(seconds=self.tick_seconds)

    @property
    def tick_end(self) -> datetime:
        return self.now + timedelta(seconds=self.tick_seconds)

    def local(self, moment: datetime) -> datetime:
        """The plant's wall-clock time for a UTC instant.

        Shift windows are local wall-clock; every stored timestamp is UTC. Resolving
        one against the other requires this conversion, and ``plant.timezone`` is the
        authority for it.
        """
        return moment.astimezone(self.master.timezone)

    def shift_at(self, moment: datetime) -> Shift:
        """The production shift containing ``moment``.

        Every operational table records the shift its event occurred in, so this is
        called for nearly every row. Only production shifts are considered: the
        general shift overlaps them, and picking it would make two shifts contain the
        same instant.
        """
        clock = self.local(moment).time()
        for shift in self.master.production_shifts:
            if shift.crosses_midnight:
                if clock >= shift.start_time or clock < shift.end_time:
                    return shift
            elif shift.start_time <= clock < shift.end_time:
                return shift
        # A gap in shift coverage is possible master data; attribute the event to
        # the shift whose start is nearest behind it rather than failing the write.
        return min(
            self.master.production_shifts,
            key=lambda s: (
                (clock.hour * 3600 + clock.minute * 60)
                - (s.start_time.hour * 3600 + s.start_time.minute * 60)
            ) % 86400,
        )

    def is_working_time(self, moment: datetime) -> bool:
        """Whether the plant is scheduled to run at ``moment``.

        ``plant.operating_days_per_week`` drives this: a six-day week means Sunday is
        not a working day, and generating production on a non-working day would
        contradict master data.
        """
        weekday = self.local(moment).weekday()  # Monday is 0
        return weekday < self.master.plant.operating_days_per_week

    # ------------------------------------------------------------------- codes

    def _load_code_counters(self, session: Session) -> None:
        """Continue each code series from the highest value already stored.

        Without this a second run against a populated database would reissue codes
        that are unique-constrained, and the first insert would fail.
        """
        for key, model, column in (
            ("RUN", ProductionRun, ProductionRun.production_run_code),
            ("WO", MaintenanceWorkRecord,
             MaintenanceWorkRecord.maintenance_work_record_code),
            ("QIR", QualityInspectionResult,
             QualityInspectionResult.quality_inspection_result_code),
            ("MOV", InventoryMovement, InventoryMovement.inventory_movement_code),
        ):
            for existing in session.scalars(select(column)):
                scope, _, suffix = str(existing).rpartition("-")
                try:
                    value = int(suffix)
                except ValueError:
                    continue
                self._code_counters[scope] = max(
                    self._code_counters.get(scope, 0), value
                )

    def next_code(self, prefix: str, scope: str, width: int) -> str:
        """``RUN-2026-0001``, ``QIR-20260729-0001``, ``MOV-20260729-00001``."""
        key = "%s-%s" % (prefix, scope)
        nxt = self._code_counters.get(key, 0) + 1
        self._code_counters[key] = nxt
        return "%s-%0*d" % (key, width, nxt)

    def run_code(self, moment: datetime) -> str:
        return self.next_code("RUN", "%04d" % self.local(moment).year, 4)

    def work_order_code(self, moment: datetime) -> str:
        return self.next_code("WO", "%04d" % self.local(moment).year, 4)

    def inspection_code(self, moment: datetime) -> str:
        return self.next_code("QIR", self.local(moment).strftime("%Y%m%d"), 4)

    def movement_code(self, moment: datetime) -> str:
        return self.next_code("MOV", self.local(moment).strftime("%Y%m%d"), 5)

    # ----------------------------------------------------------------- helpers

    def jitter(self, value: float, spread: float) -> float:
        """``value`` perturbed by up to ±``spread`` proportionally, from the seeded RNG."""
        return value * (1.0 + self.rng.uniform(-spread, spread))

    def pick(self, population: list[Any]) -> Any:
        return population[self.rng.randrange(len(population))]
