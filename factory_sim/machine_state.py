"""Machine State Engine — machine condition and behaviour.

Owns three tables and two transaction boundaries:

* **T-SIM-2, state change.** One ``machine_state_transition`` insert paired with the
  ``machine_operational_status`` update, in one unit of work. §46.1 calls this "the
  most important boundary in the Simulator": the status row is a materialised summary
  of the transition history, and a partial application would leave the current state
  disagreeing with its own history. The transition is flushed mid-transaction so the
  status row's ``last_state_transition_id`` can point at the row inserted beside it.
* **T-SIM-1, telemetry batch.** A batch of ``machine_sensor_reading`` rows for the
  interval, plus ``machine_operational_status.last_reading_at``. Atomic because a
  partially applied batch would leave ``last_reading_at`` disagreeing with the newest
  reading, and staleness detection would then report an outage that did not occur.

**The eight states are the schema's, not the phase brief's.** The brief names
Running, Idle, Setup, Maintenance, Failure and Stopped; the schema's vocabulary is
``running``, ``idle``, ``setup``, ``starved``, ``blocked``, ``down_unplanned``,
``down_planned``, ``offline``. Maintenance is ``down_planned``, failure is
``down_unplanned``, and stopped is ``idle`` or ``offline`` depending on whether the
asset is in service. ``starved`` and ``blocked`` are kept distinct because they say
opposite things about where the constraint is, which is what lets cascade direction be
reasoned about at all.

**Transitions are not arbitrary.** ``reason_code`` must be consistent with the state
pair (E3 rule 5), so the reason determines the destination through
:data:`STATE_FOR_REASON` rather than the two being chosen independently.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from models.operational import (
    MachineOperationalStatus,
    MachineSensorReading,
    MachineStateTransition,
)

from factory_sim.context import SIMULATOR_COMPONENT, MachineRuntime, SimulationContext
from factory_sim.errors import SimulationStateError

# States in which a machine is engaged with a run. ck_mos_run_only_when_engaged
# permits current_production_run_id only in these, and ck_mos_running_requires_run
# makes it mandatory in `running`.
ENGAGED_STATES = frozenset({"running", "setup", "starved", "blocked"})
DOWN_STATES = frozenset({"down_unplanned", "down_planned"})

# reason_code -> the only destination state that reason can produce.
# Reasons absent from this map may lead to more than one state and are checked
# against PERMITTED_STATES_FOR_REASON instead.
STATE_FOR_REASON = {
    "run_start": "running",
    "changeover": "setup",
    "tool_change": "setup",
    "upstream_starvation": "starved",
    "downstream_blockage": "blocked",
    "breakdown": "down_unplanned",
    "planned_maintenance": "down_planned",
    "asset_status_change": "offline",
}

PERMITTED_STATES_FOR_REASON = {
    "run_complete": {"idle"},
    "shift_end": {"idle"},
    "operator_unavailable": {"idle"},
    "quality_hold": {"idle"},
    "restored": {"idle", "running", "setup"},
}


class MachineStateEngine:
    """Machine state, state history, and telemetry."""

    def __init__(self, context: SimulationContext) -> None:
        self.context = context
        self.transitions_written = 0
        """Running total, so the orchestrator does not need a COUNT(*) per interval."""

    # ------------------------------------------------------------ initialisation

    def initialise(self, session: Session) -> None:
        """Create one ``machine_operational_status`` row per machine, once.

        Every machine gets a row, including unmonitored and decommissioned ones: an
        unmonitored machine still occupies a station and its state still affects the
        line. Machines whose ``lifecycle_status`` is not ``in_service`` start
        ``offline``, because the master row is authoritative about the asset and this
        row only reflects it.
        """
        context = self.context
        existing = {
            row.machine_id: row
            for row in session.scalars(select(MachineOperationalStatus))
        }
        shift = context.shift_at(context.now)

        for machine_id, machine in context.master.machines.items():
            in_service = machine.lifecycle_status.value == "in_service"
            state = "idle" if in_service else "offline"

            row = existing.get(machine_id)
            if row is None:
                row = MachineOperationalStatus(
                    machine_id=machine_id,
                    current_state=state,
                    state_since=context.now,
                    current_production_run_id=None,
                    current_shift_id=shift.shift_id,
                    accumulated_operating_hours=Decimal("0.00"),
                    accumulated_cycle_count=0,
                    open_alert_count=0,
                    created_by_component=SIMULATOR_COMPONENT,
                )
                session.add(row)
                session.flush()

            context.machines[machine_id] = MachineRuntime(
                machine_id=machine_id,
                state=row.current_state.value if hasattr(
                    row.current_state, "value") else row.current_state,
                state_since=row.state_since,
                run_id=row.current_production_run_id,
                accumulated_operating_hours=row.accumulated_operating_hours,
                accumulated_cycle_count=row.accumulated_cycle_count,
                hours_at_last_maintenance=row.operating_hours_at_last_maintenance,
                cycles_at_last_maintenance=row.cycle_count_at_last_maintenance,
                last_reading_at=row.last_reading_at,
                last_transition_id=row.last_state_transition_id,
                status_id=row.machine_operational_status_id,
            )

    # -------------------------------------------------------------------- T-SIM-2

    def transition(
        self,
        session: Session,
        machine_id: int,
        to_state: str,
        reason_code: str,
        *,
        at: datetime | None = None,
        run_id: int | None = None,
        work_record_id: int | None = None,
        notes: str | None = None,
    ) -> None:
        """Move one machine to a new state, writing history and current position.

        Insert-then-flush-then-update, in that order, because the status row's
        ``last_state_transition_id`` references the transition inserted in the same
        transaction and the generated key is only available after the flush.
        """
        context = self.context
        runtime = context.machines[machine_id]
        moment = at or context.now

        self._check_reason(to_state, reason_code)
        if runtime.state == to_state:
            return  # ck_mst_states_differ: a transition to the same state is none

        # Operating hours accrue over the interval being closed, not the one opening.
        elapsed = int((moment - runtime.state_since).total_seconds())
        if elapsed < 0:
            raise SimulationStateError(
                "machine %d transition at %s precedes its current state_since %s; "
                "transition timestamps are strictly increasing per machine"
                % (machine_id, moment.isoformat(), runtime.state_since.isoformat())
            )
        if runtime.state == "running":
            runtime.accumulated_operating_hours += (
                Decimal(elapsed) / Decimal(3600)
            ).quantize(Decimal("0.01"))

        engaged_run = run_id if to_state in ENGAGED_STATES else None
        if to_state == "running" and engaged_run is None:
            raise SimulationStateError(
                "machine %d cannot enter 'running' without a production run; "
                "ck_mos_running_requires_run forbids it" % machine_id
            )

        shift = context.shift_at(moment)
        transition = MachineStateTransition(
            machine_id=machine_id,
            from_state=runtime.state,
            to_state=to_state,
            transition_at=moment,
            duration_in_previous_state_seconds=elapsed,
            reason_code=reason_code,
            shift_id=shift.shift_id,
            production_run_id=run_id,
            triggering_event_id=None,
            triggering_work_record_id=work_record_id,
            notes=notes,
            created_by_component=SIMULATOR_COMPONENT,
        )
        session.add(transition)
        session.flush()

        status = session.get(MachineOperationalStatus, runtime.status_id)
        status.current_state = to_state
        status.state_since = moment
        status.current_production_run_id = engaged_run
        status.current_shift_id = shift.shift_id
        status.accumulated_operating_hours = runtime.accumulated_operating_hours
        status.accumulated_cycle_count = runtime.accumulated_cycle_count
        status.last_state_transition_id = transition.machine_state_transition_id

        runtime.state = to_state
        runtime.state_since = moment
        runtime.run_id = engaged_run
        runtime.last_transition_id = transition.machine_state_transition_id
        self.transitions_written += 1

    @staticmethod
    def _check_reason(to_state: str, reason_code: str) -> None:
        pinned = STATE_FOR_REASON.get(reason_code)
        if pinned is not None and pinned != to_state:
            raise SimulationStateError(
                "reason_code %r implies to_state %r, not %r (E3 rule 5)"
                % (reason_code, pinned, to_state)
            )
        permitted = PERMITTED_STATES_FOR_REASON.get(reason_code)
        if permitted is not None and to_state not in permitted:
            raise SimulationStateError(
                "reason_code %r cannot produce to_state %r; permitted: %s"
                % (reason_code, to_state, ", ".join(sorted(permitted)))
            )

    def sync_run(self, session: Session, machine_id: int, run_id: int | None) -> None:
        """Repoint a machine's current run without changing its state.

        Used when a run's lifecycle advances under machines that are already in an
        engaged state, which is T-SIM-5's second half.
        """
        runtime = self.context.machines[machine_id]
        if runtime.state not in ENGAGED_STATES:
            run_id = None
        if runtime.run_id == run_id:
            return
        status = session.get(MachineOperationalStatus, runtime.status_id)
        status.current_production_run_id = run_id
        runtime.run_id = run_id

    def persist_counters(self, session: Session, machine_id: int) -> None:
        """Push the runtime's accumulated counters onto the status row.

        The counters only ever increase (E2 rule 5), so this never lowers a stored
        value; it carries forward increments made by the cycle and telemetry paths.
        """
        runtime = self.context.machines[machine_id]
        status = session.get(MachineOperationalStatus, runtime.status_id)
        status.accumulated_operating_hours = runtime.accumulated_operating_hours
        status.accumulated_cycle_count = runtime.accumulated_cycle_count

    # -------------------------------------------------------------------- T-SIM-1

    def emit_telemetry(self, session: Session, degradation: dict[int, float]) -> int:
        """Insert this interval's readings for every monitored machine.

        ``degradation`` maps machine id to a 0..1 incubation progress supplied by the
        Failure Engine. The progress drifts the incubating mode's primary parameter
        toward its limit, which is how a developing failure reaches the database:
        **as telemetry, never as an event** (§7.5).

        Executed as one multi-row insert against the mapped class rather than through
        the unit of work. §26 of the model specification calls for exactly this on
        this table — nothing references a reading at insert time, so the generated
        keys are not needed, and the alternative is one round trip per row on the
        highest-volume table in the database.
        """
        context = self.context
        rows: list[dict[str, Any]] = []
        touched: dict[int, datetime] = {}

        for machine_id, machine in context.master.machines.items():
            if not machine.is_monitored:
                continue
            if machine.lifecycle_status.value != "in_service":
                continue
            runtime = context.machines[machine_id]
            if runtime.state == "offline":
                continue
            declarations = context.master.declarations_for(machine)
            if not declarations:
                continue  # the type declares no parameters; §7.5 forbids inventing one

            progress = degradation.get(machine_id, 0.0)
            incubating = runtime.incubating_mode
            drifting_param = (
                incubating.primary_machine_parameter_id if incubating else None
            )

            for decl in declarations:
                interval = int(decl.sampling_interval_seconds)
                due = runtime.next_reading_at.get(decl.machine_parameter_id)
                if due is None:
                    due = context.now
                while due < context.tick_end:
                    runtime.reading_sequence += 1
                    value, flag = self._reading_value(
                        decl,
                        progress if decl.machine_parameter_id == drifting_param else 0.0,
                        runtime.state,
                    )
                    rows.append({
                        "machine_id": machine_id,
                        "machine_parameter_id": decl.machine_parameter_id,
                        "recorded_at": due,
                        "reading_value": value,
                        "quality_flag": flag,
                        "machine_state_at_reading": runtime.state,
                        "shift_id": context.shift_at(due).shift_id,
                        "production_run_id": runtime.run_id,
                        "sequence_number": runtime.reading_sequence,
                        "created_by_component": SIMULATOR_COMPONENT,
                    })
                    touched[machine_id] = due
                    due = due + timedelta(seconds=interval)
                runtime.next_reading_at[decl.machine_parameter_id] = due

        if not rows:
            return 0

        session.execute(insert(MachineSensorReading), rows)
        for machine_id, moment in touched.items():
            runtime = context.machines[machine_id]
            runtime.last_reading_at = moment
            status = session.get(MachineOperationalStatus, runtime.status_id)
            status.last_reading_at = moment
        return len(rows)

    def _reading_value(
        self,
        declaration: Any,
        progress: float,
        state: str,
    ) -> tuple[Decimal, str]:
        """One sensor value, and its quality flag.

        The healthy band is the declaration's own ``normal_min``/``normal_max`` around
        ``nominal_value``. Drift moves the value toward the physical limit in the
        declaration's ``expected_drift_direction``. A machine that is not running
        reads near the bottom of its band, because a stopped spindle is not hot.
        """
        context = self.context
        param = context.master.parameters[declaration.machine_parameter_id]
        nominal = float(declaration.nominal_value)
        low = float(declaration.normal_min)
        high = float(declaration.normal_max)
        phys_low = float(param.physical_min)
        phys_high = float(param.physical_max)
        half_band = max((high - low) / 2.0, 1e-9)

        if state == "running":
            value = nominal + context.rng.gauss(0.0, half_band * 0.18)
        else:
            value = low + (nominal - low) * 0.15 + context.rng.gauss(
                0.0, half_band * 0.05)

        direction = declaration.expected_drift_direction.value
        if progress > 0.0:
            if direction == "increasing":
                value += (phys_high - nominal) * 0.55 * progress
            elif direction == "decreasing":
                value -= (nominal - phys_low) * 0.55 * progress
            else:
                value += (phys_high - nominal) * 0.30 * progress

        # A rare instrument fault. §7.5 permits a value outside the physical range
        # only when a sensor fault is being simulated deliberately, and forbids
        # leaving it unflagged.
        if context.rng.random() < 0.0004:
            return (
                Decimal("%.4f" % (phys_high + abs(phys_high - phys_low) * 0.1)),
                "out_of_physical_range",
            )

        value = min(max(value, phys_low), phys_high)
        if param.data_type.value == "numeric_integer":
            value = float(round(value))
        return Decimal("%.4f" % value), "valid"
