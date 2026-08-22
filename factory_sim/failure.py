"""Failure Engine — when machines degrade, and when they break.

This engine writes **no table of its own**. It decides which failure is developing on
which machine and how far along it is, and the consequences are written by the engines
that own the affected tables: drifting values by the Machine State Engine, lengthening
cycles and dimensional scrap by the Production Engine, defects by the Quality Engine,
and the repair by the Maintenance Engine.

**That indirection is the point, and §7.5 is explicit about it.** The simulator must
never create an ``operational_event`` to represent a failure. When a bearing is failing,
what reaches the database is rising vibration, rising temperature, lengthening cycle
times and out-of-tolerance parts. Detection is the Monitoring Agent's job in a later
phase, and if the simulator short-circuited it by writing events directly, the
platform's core capability would never be exercised by its own test data.

**Which failures are possible is master data.** Only modes declared in
``machine_type_failure_mode`` for the machine's own type can occur, weighted by their
``relative_frequency``. Timing comes from ``machine_type.mtbf_hours`` scaled by wear
against ``design_life_hours`` and by how overdue the machine is for service, so a
reviewer asking why a machine failed when it did gets a data answer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from factory_sim.context import FREQUENCY_WEIGHT, SimulationContext


@dataclass
class Breakdown:
    """A machine that has just failed, and the mode it failed in."""

    machine_id: int
    mode: Any

    @property
    def failure_category_id(self) -> int:
        return self.mode.failure_category_id

    @property
    def severity_level_id(self) -> int:
        return self.mode.typical_severity_level_id


class FailureEngine:
    """Degradation onset, progression, and the moment of failure."""

    def __init__(self, context: SimulationContext) -> None:
        self.context = context

    def degradation(self) -> dict[int, float]:
        """How far each machine's incubating failure has progressed, 0.0 to 1.0.

        Read by the Machine State Engine to drift the affected parameter and by the
        Production Engine to lengthen cycle times and raise scrap probability. A machine
        with nothing incubating is absent from the mapping.
        """
        progress: dict[int, float] = {}
        now = self.context.now
        for machine_id, runtime in self.context.machines.items():
            if runtime.incubating_mode is None:
                continue
            start = runtime.incubation_started_at
            due = runtime.failure_due_at
            if start is None or due is None or due <= start:
                progress[machine_id] = 1.0
                continue
            span = (due - start).total_seconds()
            elapsed = (now - start).total_seconds()
            progress[machine_id] = min(max(elapsed / span, 0.0), 1.0)
        return progress

    def evaluate(self) -> list[Breakdown]:
        """Advance every machine's failure position by one interval.

        Returns the machines that failed during it. Two things can happen per machine:
        a new mode begins incubating, or an incubating mode reaches its due moment and
        the machine goes down.
        """
        context = self.context
        breakdowns: list[Breakdown] = []

        for machine_id, machine in context.master.machines.items():
            runtime = context.machines[machine_id]
            if machine.lifecycle_status.value != "in_service":
                continue

            if runtime.incubating_mode is not None:
                if runtime.failure_due_at is not None and (
                    context.now >= runtime.failure_due_at
                ):
                    if runtime.state in {"down_unplanned", "down_planned"}:
                        continue  # already stopped; the repair will clear the mode
                    breakdowns.append(
                        Breakdown(machine_id=machine_id, mode=runtime.incubating_mode))
                continue

            # Degradation only accrues while the machine is actually working.
            if runtime.state != "running":
                continue
            if runtime.open_work_record_id is not None:
                continue

            mode = self._maybe_begin(machine_id, machine)
            if mode is not None:
                warning_hours = mode.typical_warning_period_hours
                runtime.incubating_mode = mode
                runtime.incubation_started_at = context.now
                if warning_hours is None or float(warning_hours) <= 0:
                    # No leading indicator: the fault appears without warning.
                    runtime.failure_due_at = context.now
                else:
                    runtime.failure_due_at = context.now + timedelta(
                        hours=float(warning_hours))

        return breakdowns

    def _maybe_begin(self, machine_id: int, machine: Any) -> Any | None:
        """Draw against the machine's hazard rate for this interval."""
        context = self.context
        modes = context.master.failure_modes_for(machine)
        if not modes:
            return None  # the type declares no failure modes; none is possible

        machine_type = context.master.machine_types[machine.machine_type_id]
        mtbf = float(machine_type.mtbf_hours or 0)
        if mtbf <= 0:
            return None

        runtime = context.machines[machine_id]
        design_life = float(machine_type.design_life_hours or 0)
        wear = (
            float(runtime.accumulated_operating_hours) / design_life
            if design_life > 0 else 0.0
        )
        overdue = self._overdue_factor(machine_id)
        tick_hours = context.tick_seconds / 3600.0

        hazard = (tick_hours / mtbf) * (1.0 + 2.0 * wear) * overdue
        if context.rng.random() >= hazard:
            return None
        return self._choose_mode(modes)

    def _overdue_factor(self, machine_id: int) -> float:
        """How much overdue maintenance raises the hazard.

        Maintenance history is one of the inputs the phase requires failures to be
        generated from. A machine past its operating-hour interval is more likely to
        fail, which is the entire justification for preventive maintenance existing.
        """
        context = self.context
        runtime = context.machines[machine_id]
        worst = 1.0
        for schedule in context.master.schedules_by_machine.get(machine_id, []):
            basis = schedule.interval_basis.value
            interval = float(schedule.interval_value)
            if interval <= 0:
                continue
            if basis == "operating_hours":
                ratio = float(runtime.hours_since_maintenance) / interval
            elif basis == "cycle_count":
                ratio = runtime.cycles_since_maintenance / interval
            else:
                continue
            if ratio > 1.0:
                worst = max(worst, min(ratio, 3.0))
        return worst

    def _choose_mode(self, modes: list[Any]) -> Any:
        """Pick a declared mode, weighted by ``relative_frequency``."""
        context = self.context
        weights = [
            FREQUENCY_WEIGHT.get(m.relative_frequency.value, 1.0) for m in modes
        ]
        total = sum(weights)
        draw = context.rng.random() * total
        cumulative = 0.0
        for mode, weight in zip(modes, weights):
            cumulative += weight
            if draw <= cumulative:
                return mode
        return modes[-1]

    def clear(self, machine_id: int) -> None:
        """Forget the incubating mode after a repair has addressed it."""
        runtime = self.context.machines[machine_id]
        runtime.incubating_mode = None
        runtime.incubation_started_at = None
        runtime.failure_due_at = None
