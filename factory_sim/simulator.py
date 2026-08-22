"""Factory Simulator — the orchestrator.

Advances simulated time in fixed intervals and, for each one, drives the six engines in
the order the phase requires:

    production → quality → inventory → maintenance

That order is a data dependency, not a preference. A cycle must exist before it can be
inspected, an inspection must exist before the scrap it causes can be recorded, and scrap
must exist before the material it consumed can be written off. Running the engines in any
other order would produce rows whose prerequisites had not happened yet.

**One transaction per boundary, not one per tick.** §46 draws eight boundaries for the
Simulator and each becomes one ``models.session.session_scope``: it commits when the
boundary's work is complete and rolls the whole boundary back on any failure. Grouping a
whole tick into one transaction would mean a defect in the last engine discarding correct
work from the first five; committing per row would leave a half-applied state that the
model declares impossible.

**Time advances in intervals, not per second.** A tick is a window, and each engine emits
every event whose moment falls inside it — readings at their own sampling intervals, cycles
at their own cycle times. That keeps timestamps exact without iterating simulated seconds.

**The simulator reads master data and its own tables, and nothing else.** §6.5 forbids it
from reading any agent-produced table, because a simulator that could see a prediction
could be influenced by it and every accuracy measurement downstream would be circular.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from models.operational import (
    CycleHistory,
    InventoryMovement,
    MachineMaintenanceActivity,
    MachineOperationalStatus,
    MachineSensorReading,
    MachineStateTransition,
    MaintenanceWorkRecord,
    ProductionCount,
    ProductionProgress,
    ProductionRun,
    QualityInspectionResult,
    ScrapRecord,
)
from models.session import (
    initialize_database,
    session_scope,
    shutdown_database,
)

from factory_sim.context import SimulationContext
from factory_sim.failure import FailureEngine
from factory_sim.inventory import InventoryEngine
from factory_sim.machine_state import MachineStateEngine
from factory_sim.maintenance import MaintenanceEngine
from factory_sim.production import ProductionEngine
from factory_sim.quality import QualityEngine

# The twelve tables the ownership model assigns to the Factory Simulator (§6.2, §7.1).
SIMULATOR_TABLES = (
    ("production_run", ProductionRun),
    ("machine_sensor_reading", MachineSensorReading),
    ("machine_state_transition", MachineStateTransition),
    ("machine_operational_status", MachineOperationalStatus),
    ("cycle_history", CycleHistory),
    ("production_count", ProductionCount),
    ("production_progress", ProductionProgress),
    ("quality_inspection_result", QualityInspectionResult),
    ("scrap_record", ScrapRecord),
    ("inventory_movement", InventoryMovement),
    ("maintenance_work_record", MaintenanceWorkRecord),
    ("machine_maintenance_activity", MachineMaintenanceActivity),
)


@dataclass
class SimulationReport:
    """What one simulation run produced."""

    ticks: int = 0
    simulated_seconds: int = 0
    readings: int = 0
    cycles: int = 0
    transitions: int = 0
    runs_started: int = 0
    inspections: int = 0
    scrap_records: int = 0
    movements: int = 0
    work_orders: int = 0
    activities: int = 0
    jobs_closed: int = 0
    counts: int = 0
    progress: int = 0
    row_counts: dict[str, int] = field(default_factory=dict)

    @property
    def simulated_hours(self) -> float:
        return self.simulated_seconds / 3600.0


class FactorySimulator:
    """Drives the six engines across simulated time."""

    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[Session],
        *,
        start_at: datetime,
        seed: int,
        tick_seconds: int = 60,
        quiet: bool = False,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.quiet = quiet
        with session_scope(session_factory) as session:
            self.context = SimulationContext(
                session, start_at=start_at, seed=seed, tick_seconds=tick_seconds)

        self.state = MachineStateEngine(self.context)
        self.inventory = InventoryEngine(self.context)
        self.production = ProductionEngine(self.context, self.state)
        self.failure = FailureEngine(self.context)
        self.quality = QualityEngine(self.context, self.inventory)
        self.maintenance = MaintenanceEngine(
            self.context, self.state, self.inventory)

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    # ------------------------------------------------------------ initialisation

    def initialise(self) -> None:
        """Establish the starting position: machine status, open runs, stock balances."""
        context = self.context
        self.say(
            "Master data: %d machines, %d lines, %d products, %d items"
            % (
                len(context.master.machines),
                len(context.master.lines),
                len(context.master.products),
                len(context.master.items),
            )
        )
        with session_scope(self.session_factory) as session:
            self.state.initialise(session)
        with session_scope(self.session_factory) as session:
            self.production.initialise(session)
        with session_scope(self.session_factory) as session:
            self.inventory.initialise(session)

        monitored = [
            m for m in context.master.machines.values()
            if m.is_monitored
            and m.lifecycle_status.value == "in_service"
            and context.master.declarations_for(m)
        ]
        self.say(
            "Telemetry: %d of %d machines have declared parameters"
            % (len(monitored), len(context.master.machines))
        )

    # -------------------------------------------------------------------- the loop

    def run(self, duration_seconds: int) -> SimulationReport:
        """Simulate ``duration_seconds`` of factory time."""
        report = SimulationReport()
        context = self.context
        ticks = max(duration_seconds // context.tick_seconds, 1)
        announce_every = max(ticks // 8, 1)

        self.say("")
        self.say(
            "Simulating %.1f hours from %s (seed %d, tick %ds)"
            % (
                duration_seconds / 3600.0,
                context.now.isoformat(timespec="seconds"),
                context.seed,
                context.tick_seconds,
            )
        )

        for index in range(ticks):
            self._tick(report)
            report.ticks += 1
            report.simulated_seconds += context.tick_seconds
            context.advance()
            if (index + 1) % announce_every == 0 or index == ticks - 1:
                self.say(
                    "  %s  readings %-7d cycles %-6d states %-5d insp %-4d "
                    "mov %-5d wo %d"
                    % (
                        context.now.strftime("%Y-%m-%d %H:%M"),
                        report.readings, report.cycles, report.transitions,
                        report.inspections, report.movements, report.work_orders,
                    )
                )

        report.row_counts = self.table_counts()
        return report

    def _tick(self, report: SimulationReport) -> None:
        """One interval of factory time, boundary by boundary."""
        context = self.context

        # The Failure Engine writes nothing; it decides what is degrading and what has
        # just broken, and the owning engines write the consequences.
        degradation = self.failure.degradation()
        breakdowns = self.failure.evaluate()

        # T-SIM-5 — run lifecycle, plus the bill of materials for runs that start.
        with session_scope(self.session_factory) as session:
            started = self.production.advance_runs(session)
            for run_id, product_id, quantity in started:
                if product_id is not None:
                    report.movements += self.inventory.issue_for_run(
                        session, run_id, product_id, quantity)
                report.inspections += self.quality.first_article(
                    session, self._line_of_run(run_id))
            report.runs_started += len(started)

        # Breakdown: the corrective job is raised first, then the machine's transition
        # to down_unplanned is written carrying that job's identifier. E11 rule 8
        # requires the reference, and a transition is immutable once written, so the
        # order is forced. Both land in one boundary so neither can exist alone.
        if breakdowns:
            with session_scope(self.session_factory) as session:
                raised = self.maintenance.raise_corrective_work(session, breakdowns)
                report.work_orders += len(raised)
                for breakdown in breakdowns:
                    runtime = context.machines[breakdown.machine_id]
                    if runtime.state in {"down_unplanned", "down_planned", "offline"}:
                        continue
                    self.state.transition(
                        session, breakdown.machine_id, "down_unplanned", "breakdown",
                        run_id=None,
                        work_record_id=raised.get(breakdown.machine_id),
                        notes="Unplanned stoppage",
                    )

        with session_scope(self.session_factory) as session:
            self.production.propagate_line_stalls(session)

        # T-SIM-3 — cycles, and the scrap records their outcomes require (§9.4).
        with session_scope(self.session_factory) as session:
            cycles, pending_scrap = self.production.run_cycles(session, degradation)
            report.cycles += cycles
            if pending_scrap:
                report.scrap_records += self.quality.record_cycle_scrap(
                    session, pending_scrap)

        # T-SIM-1 — telemetry batch.
        with session_scope(self.session_factory) as session:
            report.readings += self.state.emit_telemetry(session, degradation)

        # T-SIM-6 — inspection, its scrap, and the material written off.
        with session_scope(self.session_factory) as session:
            inspections, scraps = self.quality.inspect(session, degradation)
            report.inspections += inspections
            report.scrap_records += scraps

        # T-SIM-7 and T-SIM-8 — maintenance progression and closure.
        with session_scope(self.session_factory) as session:
            report.work_orders += self.maintenance.raise_scheduled_work(session)
        with session_scope(self.session_factory) as session:
            activities, closed = self.maintenance.advance_jobs(session)
            report.activities += activities
            report.jobs_closed += closed
        with session_scope(self.session_factory) as session:
            self.maintenance.restore_after_close(session)
        report.transitions = self.state.transitions_written

        # Replenishment against master reorder points and supplier lead times.
        with session_scope(self.session_factory) as session:
            report.movements += self.inventory.replenish(session)

        self.production.accrue_running_time()
        self.production.accrue_downtime()

        # T-SIM-4 — interval close and progress snapshots.
        if self.production.due_for_interval_close():
            with session_scope(self.session_factory) as session:
                report.counts += self.production.close_interval(session)
        if self.production.due_for_progress():
            with session_scope(self.session_factory) as session:
                report.progress += self.production.snapshot_progress(session)

    def _line_of_run(self, run_id: int) -> int:
        for line_id, runtime in self.context.lines.items():
            if runtime.run_id == run_id:
                return line_id
        return next(iter(self.context.master.lines))

    # ------------------------------------------------------------------ reporting

    def table_counts(self) -> dict[str, int]:
        """Row count for each of the twelve tables the simulator owns."""
        counts: dict[str, int] = {}
        with session_scope(self.session_factory) as session:
            for name, model in SIMULATOR_TABLES:
                counts[name] = int(session.execute(
                    select(func.count()).select_from(model)
                ).scalar_one())
        return counts


def simulate(
    database_path: str | Path,
    *,
    hours: float = 8.0,
    seed: int = 20260731,
    start_at: datetime | None = None,
    tick_seconds: int = 60,
    quiet: bool = False,
) -> SimulationReport:
    """Run the simulator against a database that already holds master data.

    The database is opened through ``models.session.initialize_database`` and closed
    through ``shutdown_database`` on every path, including failure.
    """
    moment = start_at or datetime(2026, 7, 31, 6, 0, tzinfo=timezone.utc)
    engine, session_factory = initialize_database(database_path)
    try:
        simulator = FactorySimulator(
            engine, session_factory,
            start_at=moment, seed=seed, tick_seconds=tick_seconds, quiet=quiet,
        )
        simulator.say("Factory Simulation Engine")
        simulator.initialise()
        report = simulator.run(int(hours * 3600))

        simulator.say("")
        simulator.say("Operational rows written:")
        for name, _ in SIMULATOR_TABLES:
            simulator.say("  %-32s %d" % (name, report.row_counts[name]))
        simulator.say("")
        simulator.say(
            "Simulation Complete. %.1f simulated hours, %d ticks."
            % (report.simulated_hours, report.ticks)
        )
        return report
    finally:
        shutdown_database(engine)


def main(argv: list[str] | None = None) -> int:
    """``python -m factory_sim <database-path> [hours] [seed]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 3:
        print(
            "usage: python -m factory_sim <database-path> [hours] [seed]",
            file=sys.stderr,
        )
        return 2
    path = Path(args[0]).resolve()
    hours = float(args[1]) if len(args) > 1 else 8.0
    seed = int(args[2]) if len(args) > 2 else 20260731

    try:
        simulate(path, hours=hours, seed=seed)
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        print("", file=sys.stderr)
        print("Simulation failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0
