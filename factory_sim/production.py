"""Production Engine — runs, cycles, interval counts and progress snapshots.

Owns four tables and three transaction boundaries:

* **T-SIM-5, run lifecycle change.** ``production_run`` status and actual timestamps,
  plus ``machine_operational_status.current_production_run_id`` for the machines on the
  line. Atomic because ``ck_mos_running_requires_run`` and
  ``ck_mos_run_only_when_engaged`` span the two tables.
* **T-SIM-3, cycle completion.** ``cycle_history`` rows and the machine's
  ``accumulated_cycle_count``. The counter is a maintained total over exactly this
  table and §41.6 reconciles the two daily.
* **T-SIM-4, interval close.** ``production_count`` for the closing interval and
  ``production_progress`` for each active run. Both derive from the same cycle and
  state data over the same window, and both are idempotent through their composite
  unique constraints.

**Rates come from master data, never from the simulator.** ``cycle_time_seconds`` and
``max_hourly_output_units`` are read from the ``product_line_capability`` row the run
pins, which is why no cycle time is copied into operational data anywhere. A run may
only be scheduled onto a capability whose ``capability_type`` is ``production_route``:
a finishing stage is not somewhere production starts.

**At most one active run per line.** ``uq_production_run_active_per_line`` is a partial
unique index over ``production_line_id`` where the status is setup, running or paused.
The engine never attempts a second one, so the index guards against a defect here
rather than being the mechanism that prevents it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.operational import (
    CycleHistory,
    ProductionCount,
    ProductionProgress,
    ProductionRun,
)

from factory_sim.context import SIMULATOR_COMPONENT, LineRuntime, SimulationContext
from factory_sim.machine_state import MachineStateEngine

COUNT_INTERVAL_SECONDS = 1800   # production_count closes every 30 minutes
PROGRESS_INTERVAL_SECONDS = 900  # production_progress snapshots every 15 minutes


@dataclass
class CycleScrap:
    """A cycle whose outcome was scrap, awaiting its material consequence.

    §9.4 requires that a scrap cycle outcome has a corresponding ``scrap_record``, so
    the Production Engine reports them and the Quality Engine writes the record inside
    the same unit of work.
    """

    machine_id: int
    run_id: int
    at: datetime
    quantity: Decimal
    reason: str
    attributed_machine_id: int | None
    attributed_failure_category_id: int | None


@dataclass
class _IntervalTally:
    good: int = 0
    scrap: int = 0
    rework: int = 0
    cycle_time_seconds: int = 0
    running_seconds: int = 0
    run_id: int | None = None


class ProductionEngine:
    """Run scheduling and execution."""

    def __init__(self, context: SimulationContext, state: MachineStateEngine) -> None:
        self.context = context
        self.state = state
        self._tallies: dict[int, _IntervalTally] = {}
        self._interval_from = context.now
        self._next_count_close = context.now + timedelta(
            seconds=COUNT_INTERVAL_SECONDS)
        self._next_progress_at = context.now + timedelta(
            seconds=PROGRESS_INTERVAL_SECONDS)

    # ------------------------------------------------------------ initialisation

    def initialise(self, session: Session) -> None:
        """Adopt any run already active on a line, so a restart does not double-book."""
        context = self.context
        for line_id in context.master.lines:
            context.lines[line_id] = LineRuntime(line_id=line_id)

        active = session.scalars(
            select(ProductionRun).where(
                ProductionRun.run_status.in_(["setup", "running", "paused"])
            )
        )
        for run in active:
            runtime = context.lines.get(run.production_line_id)
            if runtime is None:
                continue
            runtime.run_id = run.production_run_id
            runtime.run_status = run.run_status.value
            runtime.capability_id = run.product_line_capability_id
            runtime.product_id = run.product_id
            runtime.planned_quantity = run.planned_quantity_units
            runtime.actual_start_at = run.actual_start_at
            runtime.planned_end_at = run.planned_end_at

    # -------------------------------------------------------------------- T-SIM-5

    def advance_runs(self, session: Session) -> list[tuple[int, int, Decimal]]:
        """Schedule, start and complete runs for every line.

        Returns ``(run_id, product_id, planned_quantity)`` for runs that started during
        this interval, so the caller can issue their bill of materials inside the same
        boundary.
        """
        context = self.context
        started: list[tuple[int, int, Decimal]] = []
        for line_id in context.master.lines:
            runtime = context.lines[line_id]
            if runtime.run_id is None:
                if context.is_working_time(context.now):
                    self._schedule_run(session, line_id)
                continue
            if runtime.run_status == "setup" and runtime.setup_until is not None:
                if context.now >= runtime.setup_until:
                    self._start_run(session, line_id)
                    started.append(
                        (runtime.run_id, runtime.product_id, runtime.planned_quantity))
            elif runtime.run_status == "running":
                if runtime.quantity_good >= runtime.planned_quantity:
                    self._complete_run(session, line_id)
        return started

    def propagate_line_stalls(self, session: Session) -> int:
        """Starve downstream stations and block upstream ones around a stopped machine.

        ``starved`` and ``blocked`` are distinct states because they say opposite things
        about where the constraint is: starvation points upstream, blockage points
        downstream. Collapsing them into one waiting state would destroy the cascade
        direction the platform reasons about.
        """
        context = self.context
        changed = 0

        for line_id, runtime in context.lines.items():
            if runtime.run_id is None or runtime.run_status != "running":
                continue
            machines = [
                m for m in context.master.machines_by_line.get(line_id, [])
                if m.lifecycle_status.value == "in_service"
            ]
            stopped = [
                index for index, m in enumerate(machines)
                if context.machines[m.machine_id].state in {
                    "down_unplanned", "down_planned"}
            ]

            for index, machine in enumerate(machines):
                machine_runtime = context.machines[machine.machine_id]
                if machine_runtime.state in {"down_unplanned", "down_planned", "offline"}:
                    continue
                if not stopped:
                    if machine_runtime.state in {"starved", "blocked"}:
                        self.state.transition(
                            session, machine.machine_id, "running", "restored",
                            run_id=runtime.run_id,
                        )
                        changed += 1
                    continue
                if any(position < index for position in stopped):
                    target, reason = "starved", "upstream_starvation"
                else:
                    target, reason = "blocked", "downstream_blockage"
                if machine_runtime.state != target:
                    self.state.transition(
                        session, machine.machine_id, target, reason,
                        run_id=runtime.run_id,
                    )
                    changed += 1
        return changed

    def _schedule_run(self, session: Session, line_id: int) -> None:
        context = self.context
        routes = context.master.routes_by_line.get(line_id)
        if not routes:
            return  # no production route on this line; nothing may be scheduled here

        capability = context.pick(routes)
        product = context.master.products.get(capability.product_id)
        if product is None:
            return
        customer = context.pick(context.master.customers)
        line = context.master.lines[line_id]

        rate = float(capability.max_hourly_output_units)
        hours = context.rng.uniform(3.0, 6.0)
        quantity = Decimal(int(max(round(rate * hours), 1))).quantize(Decimal("0.01"))
        changeover = int(capability.changeover_minutes or line.changeover_time_minutes or 0)

        planned_start = context.now
        planned_end = planned_start + timedelta(
            minutes=changeover + (float(quantity) / max(rate, 0.01)) * 60.0
        )
        due = (planned_end + timedelta(days=context.rng.randint(2, 6))).date()

        run = ProductionRun(
            production_run_code=context.run_code(planned_start),
            product_id=product.product_id,
            production_line_id=line_id,
            product_line_capability_id=capability.product_line_capability_id,
            customer_id=customer.customer_id,
            planned_quantity_units=quantity,
            planned_start_at=planned_start,
            planned_end_at=planned_end,
            due_date=due,
            priority=self._priority_for(customer),
            run_status="setup",
            created_by_component=SIMULATOR_COMPONENT,
        )
        session.add(run)
        session.flush()

        runtime = context.lines[line_id]
        runtime.run_id = run.production_run_id
        runtime.run_status = "setup"
        runtime.capability_id = capability.product_line_capability_id
        runtime.product_id = product.product_id
        runtime.planned_quantity = quantity
        runtime.planned_end_at = planned_end
        runtime.quantity_good = Decimal("0.00")
        runtime.quantity_scrapped = Decimal("0.00")
        runtime.quantity_rework = Decimal("0.00")
        runtime.elapsed_production_seconds = 0
        runtime.downtime_seconds = 0
        runtime.actual_start_at = None
        runtime.setup_until = context.now + timedelta(minutes=max(changeover, 1))

        for machine in context.master.machines_by_line.get(line_id, []):
            if machine.lifecycle_status.value != "in_service":
                continue
            runtime_machine = context.machines[machine.machine_id]
            if runtime_machine.state in {"down_unplanned", "down_planned"}:
                continue
            self.state.transition(
                session, machine.machine_id, "setup", "changeover",
                run_id=run.production_run_id,
            )

    @staticmethod
    def _priority_for(customer: Any) -> str:
        tier = customer.priority_tier.value
        if tier == "gold":
            return "high"
        if tier == "silver":
            return "normal"
        return "normal"

    def _start_run(self, session: Session, line_id: int) -> None:
        context = self.context
        runtime = context.lines[line_id]
        run = session.get(ProductionRun, runtime.run_id)
        run.run_status = "running"
        run.actual_start_at = context.now
        runtime.run_status = "running"
        runtime.actual_start_at = context.now

        capability = self._capability(runtime)
        cycle_seconds = float(capability.cycle_time_seconds)
        for machine in context.master.machines_by_line.get(line_id, []):
            if machine.lifecycle_status.value != "in_service":
                continue
            machine_runtime = context.machines[machine.machine_id]
            if machine_runtime.state in {"down_unplanned", "down_planned"}:
                continue
            machine_runtime.cycle_number_in_run = 0
            machine_runtime.next_cycle_end_at = context.now + timedelta(
                seconds=cycle_seconds)
            self.state.transition(
                session, machine.machine_id, "running", "run_start",
                run_id=runtime.run_id,
            )

    def _complete_run(self, session: Session, line_id: int) -> None:
        context = self.context
        runtime = context.lines[line_id]
        run = session.get(ProductionRun, runtime.run_id)
        run.run_status = "completed"
        run.actual_end_at = context.now

        for machine in context.master.machines_by_line.get(line_id, []):
            machine_runtime = context.machines[machine.machine_id]
            machine_runtime.next_cycle_end_at = None
            if machine_runtime.state in {"running", "setup", "starved", "blocked"}:
                self.state.transition(
                    session, machine.machine_id, "idle", "run_complete",
                    run_id=None,
                )
        runtime.run_id = None
        runtime.run_status = None
        runtime.setup_until = None

    def pause_run(self, session: Session, line_id: int, reason: str) -> None:
        """Pause the line's run. ``pause_reason`` is set if and only if paused."""
        runtime = self.context.lines[line_id]
        if runtime.run_id is None or runtime.run_status != "running":
            return
        run = session.get(ProductionRun, runtime.run_id)
        run.run_status = "paused"
        run.pause_reason = reason
        runtime.run_status = "paused"

    def resume_run(self, session: Session, line_id: int) -> None:
        runtime = self.context.lines[line_id]
        if runtime.run_id is None or runtime.run_status != "paused":
            return
        run = session.get(ProductionRun, runtime.run_id)
        run.run_status = "running"
        run.pause_reason = None
        runtime.run_status = "running"

    def _capability(self, runtime: LineRuntime) -> Any:
        for cap in self.context.master.capabilities:
            if cap.product_line_capability_id == runtime.capability_id:
                return cap
        raise KeyError("capability %r missing from snapshot" % runtime.capability_id)

    # -------------------------------------------------------------------- T-SIM-3

    def run_cycles(
        self,
        session: Session,
        degradation: dict[int, float],
    ) -> tuple[int, list[CycleScrap]]:
        """Complete every cycle whose end falls inside this interval.

        Each station on a running line cycles independently, so ``cycle_number_in_run``
        is per machine. Line output is measured at the **last** station, which is where
        a finished unit actually leaves the line — counting it at every station would
        multiply output by the station count.
        """
        context = self.context
        written = 0
        scraps: list[CycleScrap] = []

        for line_id, runtime in context.lines.items():
            if runtime.run_id is None or runtime.run_status != "running":
                continue
            capability = self._capability(runtime)
            standard = float(capability.cycle_time_seconds)
            machines = [
                m for m in context.master.machines_by_line.get(line_id, [])
                if m.lifecycle_status.value == "in_service"
            ]
            if not machines:
                continue
            output_station = machines[-1].machine_id

            for machine in machines:
                machine_runtime = context.machines[machine.machine_id]
                if machine_runtime.state != "running":
                    continue
                due = machine_runtime.next_cycle_end_at
                if due is None:
                    due = context.now + timedelta(seconds=standard)
                while due < context.tick_end:
                    progress = degradation.get(machine.machine_id, 0.0)
                    # A degrading machine takes longer per part. This is the second
                    # independent way a developing failure reaches the database.
                    actual = context.jitter(standard * (1.0 + 0.45 * progress), 0.04)
                    started = due - timedelta(seconds=actual)
                    deviation = Decimal(
                        "%.2f" % (((actual - standard) / standard) * 100.0))

                    outcome, scrap_reason = self._cycle_outcome(
                        runtime.product_id, progress)
                    machine_runtime.cycle_number_in_run += 1
                    machine_runtime.cycle_sequence += 1
                    machine_runtime.accumulated_cycle_count += 1

                    session.add(CycleHistory(
                        machine_id=machine.machine_id,
                        production_run_id=runtime.run_id,
                        cycle_number_in_run=machine_runtime.cycle_number_in_run,
                        cycle_started_at=started,
                        cycle_ended_at=due,
                        cycle_time_seconds=Decimal("%.2f" % actual),
                        deviation_from_standard_pct=deviation,
                        outcome=outcome,
                        interrupted=False,
                        shift_id=context.shift_at(due).shift_id,
                        sequence_number=machine_runtime.cycle_sequence,
                        created_by_component=SIMULATOR_COMPONENT,
                    ))
                    written += 1

                    tally = self._tallies.setdefault(
                        machine.machine_id, _IntervalTally())
                    tally.run_id = runtime.run_id
                    tally.cycle_time_seconds += int(actual)
                    if outcome == "good":
                        tally.good += 1
                    elif outcome == "scrap":
                        tally.scrap += 1
                    else:
                        tally.rework += 1

                    if machine.machine_id == output_station:
                        if outcome == "good":
                            runtime.quantity_good += Decimal("1.00")
                        elif outcome == "scrap":
                            runtime.quantity_scrapped += Decimal("1.00")
                        else:
                            runtime.quantity_rework += Decimal("1.00")

                    if outcome == "scrap":
                        attributed, category = self._attribute_scrap(
                            machine.machine_id, scrap_reason)
                        scraps.append(CycleScrap(
                            machine_id=machine.machine_id,
                            run_id=runtime.run_id,
                            at=due,
                            quantity=Decimal("1.00"),
                            reason=scrap_reason,
                            attributed_machine_id=attributed,
                            attributed_failure_category_id=category,
                        ))

                    machine_runtime.cycles_since_inspection += 1
                    due = due + timedelta(seconds=actual)
                machine_runtime.next_cycle_end_at = due
                self.state.persist_counters(session, machine.machine_id)

        return written, scraps

    def _cycle_outcome(self, product_id: int | None, progress: float) -> tuple[str, str]:
        """Cycle outcome, weighted by the product's own scrap target and machine wear."""
        context = self.context
        target = 1.5
        product = context.master.products.get(product_id) if product_id else None
        if product is not None and product.target_scrap_rate_pct is not None:
            target = float(product.target_scrap_rate_pct)
        scrap_chance = (target / 100.0) * (1.0 + 4.0 * progress)
        rework_chance = scrap_chance * 0.6

        draw = context.rng.random()
        if draw < scrap_chance:
            reason = "dimensional_deviation" if progress > 0.25 else "process_deviation"
            return "scrap", reason
        if draw < scrap_chance + rework_chance:
            return "rework", ""
        return "good", ""

    def _attribute_scrap(
        self,
        machine_id: int,
        reason: str,
    ) -> tuple[int | None, int | None]:
        """Attribute scrap to a machine and mechanism, or to neither.

        ``ck_sr_attribution_paired`` requires both or neither, and
        ``ck_sr_non_machine_reasons_unattributed`` forbids attribution for material and
        handling causes. Attribution is only claimed when the machine is actually
        incubating a failure whose category is known — guessing would inflate the
        preventable-loss figure the platform is ultimately judged on.
        """
        if reason in {"material_defect", "handling_damage", "setup_reject"}:
            return None, None
        runtime = self.context.machines[machine_id]
        mode = runtime.incubating_mode
        if mode is None:
            return None, None
        return machine_id, mode.failure_category_id

    # -------------------------------------------------------------------- T-SIM-4

    def accrue_running_time(self) -> None:
        """Add this interval's running seconds to each machine's interval tally."""
        for machine_id, runtime in self.context.machines.items():
            if runtime.state == "running":
                tally = self._tallies.setdefault(machine_id, _IntervalTally())
                tally.running_seconds += self.context.tick_seconds

    def due_for_interval_close(self) -> bool:
        return self.context.tick_end >= self._next_count_close

    def close_interval(self, session: Session) -> int:
        """Write ``production_count`` for the closing window and reset the tallies."""
        context = self.context
        interval_from = self._interval_from
        interval_to = self._next_count_close
        span = int((interval_to - interval_from).total_seconds())
        written = 0

        for machine_id, tally in self._tallies.items():
            cycles = tally.good + tally.scrap + tally.rework
            if cycles == 0 and tally.running_seconds == 0:
                continue
            existing = session.scalars(
                select(ProductionCount).where(
                    ProductionCount.machine_id == machine_id,
                    ProductionCount.interval_from == interval_from,
                )
            ).first()
            running = min(tally.running_seconds, span)
            if existing is None:
                session.add(ProductionCount(
                    machine_id=machine_id,
                    production_run_id=tally.run_id,
                    interval_from=interval_from,
                    interval_to=interval_to,
                    good_count=tally.good,
                    scrap_count=tally.scrap,
                    rework_count=tally.rework,
                    cycles_completed=cycles,
                    total_cycle_time_seconds=tally.cycle_time_seconds,
                    running_seconds=running,
                    shift_id=context.shift_at(interval_from).shift_id,
                    created_by_component=SIMULATOR_COMPONENT,
                ))
            else:
                existing.good_count = tally.good
                existing.scrap_count = tally.scrap
                existing.rework_count = tally.rework
                existing.cycles_completed = cycles
                existing.total_cycle_time_seconds = tally.cycle_time_seconds
                existing.running_seconds = running
            written += 1

        self._tallies.clear()
        self._interval_from = interval_to
        self._next_count_close = interval_to + timedelta(
            seconds=COUNT_INTERVAL_SECONDS)
        return written

    def due_for_progress(self) -> bool:
        return self.context.tick_end >= self._next_progress_at

    def snapshot_progress(self, session: Session) -> int:
        """Write one ``production_progress`` row per active run."""
        context = self.context
        moment = self._next_progress_at
        written = 0

        for line_id, runtime in context.lines.items():
            if runtime.run_id is None or runtime.actual_start_at is None:
                continue
            elapsed = int((moment - runtime.actual_start_at).total_seconds())
            produced = (
                runtime.quantity_good + runtime.quantity_scrapped
                + runtime.quantity_rework
            )
            hours = max(elapsed / 3600.0, 1e-6)
            rate = float(runtime.quantity_good) / hours
            percent = (
                float(runtime.quantity_good) / float(runtime.planned_quantity) * 100.0
                if runtime.planned_quantity else 0.0
            )
            remaining = float(runtime.planned_quantity - runtime.quantity_good)

            projected = None
            if rate > 0.01 and remaining > 0:
                projected = moment + timedelta(hours=remaining / rate)
            reference = projected or moment
            variance = int(
                (reference - runtime.planned_end_at).total_seconds() // 60
            ) if runtime.planned_end_at else 0
            scrap_rate = (
                float(runtime.quantity_scrapped) / float(produced) * 100.0
                if produced else 0.0
            )

            existing = session.scalars(
                select(ProductionProgress).where(
                    ProductionProgress.production_run_id == runtime.run_id,
                    ProductionProgress.snapshot_at == moment,
                )
            ).first()
            if existing is None:
                session.add(ProductionProgress(
                    production_run_id=runtime.run_id,
                    snapshot_at=moment,
                    quantity_good_cumulative=runtime.quantity_good,
                    quantity_scrapped_cumulative=runtime.quantity_scrapped,
                    quantity_rework_cumulative=runtime.quantity_rework,
                    percent_complete=Decimal("%.2f" % min(percent, 999.99)),
                    current_rate_units_per_hour=Decimal("%.2f" % max(rate, 0.0)),
                    elapsed_production_seconds=max(elapsed, 0),
                    downtime_seconds_cumulative=runtime.downtime_seconds,
                    projected_completion_at=projected,
                    schedule_variance_minutes=variance,
                    is_behind_schedule=variance > 0,
                    scrap_rate_pct=Decimal("%.2f" % min(scrap_rate, 100.0)),
                    shift_id=context.shift_at(moment).shift_id,
                    created_by_component=SIMULATOR_COMPONENT,
                ))
                written += 1

        self._next_progress_at = moment + timedelta(
            seconds=PROGRESS_INTERVAL_SECONDS)
        return written

    def accrue_downtime(self) -> None:
        """Charge this interval's non-producing time to the run on each line."""
        context = self.context
        for line_id, runtime in context.lines.items():
            if runtime.run_id is None:
                continue
            machines = context.master.machines_by_line.get(line_id, [])
            if any(
                context.machines[m.machine_id].state in {
                    "down_unplanned", "down_planned"}
                for m in machines
            ):
                runtime.downtime_seconds += context.tick_seconds
            elif any(
                context.machines[m.machine_id].state == "running" for m in machines
            ):
                runtime.elapsed_production_seconds += context.tick_seconds
