"""Maintenance Engine — work orders and their timelines.

Owns ``maintenance_work_record`` and ``machine_maintenance_activity``, and two
boundaries:

* **T-SIM-7, job progression.** Status and timestamps on the work record, one activity
  row, and — on ``part_collected`` — the ``issue_maintenance`` movement. Atomic because
  the timeline must not record a part collection without the corresponding stock issue.
* **T-SIM-8, closure.** The record reaches ``closed`` with its confirmed failure category
  and resolution note, the closing activity is written, and the machine's
  ``operating_hours_at_last_maintenance`` and ``cycle_count_at_last_maintenance`` advance.
  §M26 makes this **the sole mechanism by which maintenance due status advances**, which
  is why a partial application here would leave a closed job whose schedule still reads
  as overdue.

**Policies are master data.** Intervals come from ``machine_maintenance_schedule``, repair
durations from ``machine_type_failure_mode.estimated_repair_duration_minutes``, response
targets from ``maintenance_team.target_response_time_minutes``, and part retrieval from
``inventory_location.average_retrieval_time_minutes``. The engine invents no policy of its
own; it schedules against the ones already recorded.

**The simulator cannot originate predictive work.** ``ck_mwr_predictive_requires_recommendation``
requires ``triggering_recommendation_id``, and §6.5 forbids the simulator from reading
``ai_recommendation``. Scheduled work is therefore preventive, calibration or inspection,
and unplanned work is corrective or emergency. Predictive work appears once the Decision
Agent exists and a human acts on its advice.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.operational import (
    MachineMaintenanceActivity,
    MachineOperationalStatus,
    MaintenanceWorkRecord,
)

from factory_sim.context import (
    SIMULATOR_COMPONENT,
    WORK_TYPE_FOR_MAINTENANCE_TYPE,
    SimulationContext,
)
from factory_sim.failure import Breakdown
from factory_sim.inventory import InventoryEngine
from factory_sim.machine_state import MachineStateEngine

# Activity sequences. A corrective job diagnoses before repairing; routine work does
# not. §E12 rule 5 states the expected corrective order and permits deviation.
CORRECTIVE_STEPS = (
    "dispatched", "arrived", "diagnosis_started", "diagnosis_complete",
    "part_requested", "part_collected", "repair_started", "repair_complete",
    "test_run", "handover",
)
ROUTINE_STEPS = (
    "dispatched", "arrived", "repair_started", "repair_complete", "handover",
)


@dataclass
class _Plan:
    """The remaining timeline for one open job."""

    work_record_id: int
    machine_id: int
    steps: list[str]
    is_corrective: bool
    repair_minutes: int
    required_item_id: int | None
    failure_category_id: int | None
    engineer_worker_id: int | None
    last_activity_at: datetime
    schedule_id: int | None = None
    stops_line: bool = False
    started_at: datetime | None = None
    notes: dict[str, str] = field(default_factory=dict)


class MaintenanceEngine:
    """Scheduled and reactive maintenance execution."""

    def __init__(
        self,
        context: SimulationContext,
        state: MachineStateEngine,
        inventory: InventoryEngine,
    ) -> None:
        self.context = context
        self.state = state
        self.inventory = inventory
        self._plans: dict[int, _Plan] = {}

    # ----------------------------------------------------------------- raising

    def raise_scheduled_work(self, session: Session) -> int:
        """Open work orders for schedules that have come due.

        Due status is computed from the machine's counters against the schedule's own
        interval, which is exactly what master data refused to cache: §M26 excluded
        ``next_due_date`` so that due status is derived from operational history instead.
        """
        context = self.context
        raised = 0

        for machine_id, schedules in context.master.schedules_by_machine.items():
            runtime = context.machines.get(machine_id)
            if runtime is None or runtime.open_work_record_id is not None:
                continue
            machine = context.master.machines[machine_id]
            if machine.lifecycle_status.value != "in_service":
                continue
            if runtime.state in {"down_unplanned", "down_planned", "offline"}:
                continue

            for schedule in schedules:
                if not self._is_due(machine_id, schedule):
                    continue
                self._open(
                    session,
                    machine_id=machine_id,
                    work_type=WORK_TYPE_FOR_MAINTENANCE_TYPE.get(
                        schedule.maintenance_type.value, "preventive"),
                    schedule=schedule,
                    failure_category_id=None,
                    severity_level_id=self._routine_severity_id(),
                    repair_minutes=int(schedule.estimated_duration_minutes),
                    required_item_id=schedule.required_inventory_item_id,
                    team_id=schedule.assigned_maintenance_team_id,
                    stops_line=bool(schedule.requires_line_stop),
                    is_corrective=False,
                )
                raised += 1
                break  # one job per machine at a time

        return raised

    def raise_corrective_work(
        self,
        session: Session,
        breakdowns: list[Breakdown],
    ) -> dict[int, int]:
        """Open a corrective or emergency job for each machine that has just failed.

        Returns machine id to work record id. The caller writes the machine's
        transition to ``down_unplanned`` **after** this, carrying the returned
        identifier as ``triggering_work_record_id``: E11 rule 8 requires an
        in-progress job to have a down-state transition that references it, and a
        transition is immutable, so the reference cannot be added afterwards.
        """
        context = self.context
        raised: dict[int, int] = {}

        for breakdown in breakdowns:
            runtime = context.machines[breakdown.machine_id]
            if runtime.open_work_record_id is not None:
                continue
            mode = breakdown.mode
            category = context.master.failure_categories.get(mode.failure_category_id)
            severity = context.master.severities.get(mode.typical_severity_level_id)
            emergency = severity is not None and severity.requires_line_stop

            specialization = (
                category.required_specialization.value if category is not None
                else "general"
            )
            team = context.master.team_for_specialization(specialization)

            self._open(
                session,
                machine_id=breakdown.machine_id,
                work_type="emergency" if emergency else "corrective",
                schedule=None,
                failure_category_id=mode.failure_category_id,
                severity_level_id=mode.typical_severity_level_id,
                repair_minutes=int(mode.estimated_repair_duration_minutes),
                required_item_id=mode.required_inventory_item_id,
                team_id=team.maintenance_team_id if team is not None else None,
                stops_line=True,
                is_corrective=True,
            )
            raised[breakdown.machine_id] = runtime.open_work_record_id

        return raised

    def _is_due(self, machine_id: int, schedule: Any) -> bool:
        context = self.context
        runtime = context.machines[machine_id]
        basis = schedule.interval_basis.value
        interval = float(schedule.interval_value)
        if interval <= 0:
            return False

        if basis == "operating_hours":
            return float(runtime.hours_since_maintenance) >= interval
        if basis == "cycle_count":
            return runtime.cycles_since_maintenance >= interval
        if basis == "calendar_days":
            anchor = schedule.baseline_start_date
            elapsed_days = (context.local(context.now).date() - anchor).days
            if elapsed_days < 0:
                return False
            # Calendar schedules recur; a job is due when a whole interval has passed
            # since the last completion, or since the baseline if never serviced.
            if runtime.hours_at_last_maintenance is None:
                return elapsed_days >= interval
            return float(runtime.hours_since_maintenance) >= interval * 8.0
        return False

    def _routine_severity_id(self) -> int:
        """The least severe active level, for work that is not a failure."""
        severities = list(self.context.master.severities.values())
        return max(severities, key=lambda s: s.severity_rank).failure_severity_level_id

    def _open(
        self,
        session: Session,
        *,
        machine_id: int,
        work_type: str,
        schedule: Any | None,
        failure_category_id: int | None,
        severity_level_id: int,
        repair_minutes: int,
        required_item_id: int | None,
        team_id: int | None,
        stops_line: bool,
        is_corrective: bool,
    ) -> None:
        context = self.context
        runtime = context.machines[machine_id]

        record = MaintenanceWorkRecord(
            maintenance_work_record_code=context.work_order_code(context.now),
            machine_id=machine_id,
            work_type=work_type,
            machine_maintenance_schedule_id=(
                schedule.machine_maintenance_schedule_id if schedule else None),
            reported_failure_category_id=failure_category_id,
            priority_severity_level_id=severity_level_id,
            work_status="open",
            opened_at=context.now,
            planned_duration_minutes=max(repair_minutes, 1),
            did_stop_line=stops_line,
            shift_id_opened=context.shift_at(context.now).shift_id,
            created_by_component=SIMULATOR_COMPONENT,
        )
        session.add(record)
        session.flush()

        steps = list(CORRECTIVE_STEPS if is_corrective else ROUTINE_STEPS)
        if required_item_id is None:
            steps = [s for s in steps if s not in {"part_requested", "part_collected"}]

        engineer_worker_id = None
        if team_id is not None:
            engineer = self._pick_engineer(team_id)
            if engineer is not None:
                engineer_worker_id = engineer.worker_id

        self._plans[record.maintenance_work_record_id] = _Plan(
            work_record_id=record.maintenance_work_record_id,
            machine_id=machine_id,
            steps=steps,
            is_corrective=is_corrective,
            repair_minutes=max(repair_minutes, 1),
            required_item_id=required_item_id,
            failure_category_id=failure_category_id,
            engineer_worker_id=engineer_worker_id,
            last_activity_at=context.now,
            schedule_id=(
                schedule.machine_maintenance_schedule_id if schedule else None),
            stops_line=stops_line,
        )
        runtime.open_work_record_id = record.maintenance_work_record_id
        runtime.work_stage = "open"

        team = context.master.teams.get(team_id) if team_id else None
        response = int(team.target_response_time_minutes) if team is not None else 30
        runtime.work_next_step_at = context.now + timedelta(minutes=response)

    def _pick_engineer(self, team_id: int) -> Any | None:
        """An engineer on the team, preferring one whose certification is current."""
        context = self.context
        candidates = context.master.engineers_for_team(team_id)
        if not candidates:
            return None
        today = context.local(context.now).date()
        certified = [
            e for e in candidates
            if e.certification_expiry_date is None
            or e.certification_expiry_date >= today
        ]
        return context.pick(certified or candidates)

    # ------------------------------------------------------- T-SIM-7 and T-SIM-8

    def advance_jobs(self, session: Session) -> tuple[int, int]:
        """Take the next timeline step on every job whose step time has arrived.

        Returns ``(activities_written, jobs_closed)``.
        """
        context = self.context
        activities = 0
        closed = 0

        for machine_id, runtime in context.machines.items():
            record_id = runtime.open_work_record_id
            if record_id is None or runtime.work_next_step_at is None:
                continue
            if context.now < runtime.work_next_step_at:
                continue
            plan = self._plans.get(record_id)
            if plan is None:
                runtime.open_work_record_id = None
                runtime.work_next_step_at = None
                continue
            if not plan.steps:
                closed += self._close(session, plan)
                continue

            step = plan.steps.pop(0)
            record = session.get(MaintenanceWorkRecord, record_id)
            activities += self._perform(session, plan, record, step)
            runtime.work_next_step_at = context.now + timedelta(
                minutes=self._minutes_for_next(plan, step))

        return activities, closed

    def _minutes_for_next(self, plan: _Plan, step: str) -> int:
        """How long the step just taken occupies before the next one begins."""
        context = self.context
        if step == "dispatched":
            return max(int(context.jitter(10, 0.3)), 1)
        if step == "arrived":
            return max(int(context.jitter(5, 0.3)), 1)
        if step == "diagnosis_started":
            return max(int(context.jitter(15, 0.3)), 1)
        if step == "diagnosis_complete":
            return max(int(context.jitter(2, 0.5)), 1)
        if step == "part_requested":
            # Retrieval time is a property of the location the part is stored in,
            # which is why inventory_location carries average_retrieval_time_minutes.
            minutes = 15
            item = (
                context.master.items.get(plan.required_item_id)
                if plan.required_item_id else None
            )
            if item is not None:
                location = context.master.locations.get(
                    item.default_inventory_location_id)
                if location is not None:
                    minutes = int(location.average_retrieval_time_minutes)
            return max(int(context.jitter(minutes, 0.2)), 1)
        if step == "part_collected":
            return max(int(context.jitter(7, 0.3)), 1)
        if step == "repair_started":
            return max(int(context.jitter(plan.repair_minutes, 0.15)), 1)
        if step == "repair_complete":
            return max(int(context.jitter(12, 0.3)), 1)
        if step == "test_run":
            return max(int(context.jitter(12, 0.3)), 1)
        return 1

    def _perform(
        self,
        session: Session,
        plan: _Plan,
        record: MaintenanceWorkRecord,
        step: str,
    ) -> int:
        context = self.context
        runtime = context.machines[plan.machine_id]
        moment = context.now
        gap = int((moment - plan.last_activity_at).total_seconds())

        note = None
        worker_id = plan.engineer_worker_id

        if step == "dispatched":
            record.work_status = "assigned"
            record.assigned_at = moment
            if record.assigned_maintenance_team_id is None:
                team = self._team_for(plan)
                record.assigned_maintenance_team_id = team
            engineer = self._engineer_record(record.assigned_maintenance_team_id)
            if engineer is not None:
                record.assigned_engineer_id = engineer.maintenance_engineer_id
                plan.engineer_worker_id = engineer.worker_id
            worker_id = None  # a dispatch is system-recorded

        elif step == "arrived":
            pass

        elif step == "repair_started":
            record.work_status = "in_progress"
            record.started_at = moment
            plan.started_at = moment
            # E11 rule 8: an in-progress job must have a transition to a down state
            # carrying the work record reference.
            if runtime.state not in {"down_unplanned", "down_planned"}:
                to_state = (
                    "down_unplanned" if plan.is_corrective else "down_planned")
                reason = "breakdown" if plan.is_corrective else "planned_maintenance"
                self.state.transition(
                    session, plan.machine_id, to_state, reason,
                    work_record_id=plan.work_record_id,
                    notes="Maintenance in progress",
                )

        elif step == "part_collected":
            item_id = plan.required_item_id
            if item_id is not None:
                movement = self.inventory.issue_for_maintenance(
                    session, plan.work_record_id, item_id, moment,
                    note="Issued against %s" % record.maintenance_work_record_code,
                )
                if movement is None:
                    # Stock is not available; the job waits rather than pretending.
                    record.work_status = "awaiting_parts"
                    plan.steps.insert(0, "part_collected")
                    note = "Awaiting parts: stock unavailable"
                else:
                    note = "Part issued from store"
                    record.work_status = "in_progress"

        elif step == "repair_complete":
            record.work_status = "completed"
            record.completed_at = moment
            if plan.started_at is not None:
                minutes = max(int((moment - plan.started_at).total_seconds() // 60), 1)
                record.actual_duration_minutes = minutes

        elif step == "test_run":
            note = "Verification run completed"

        elif step == "handover":
            note = "Released to production"

        session.add(MachineMaintenanceActivity(
            maintenance_work_record_id=plan.work_record_id,
            activity_at=moment,
            activity_type=step,
            performed_by_worker_id=worker_id,
            duration_from_previous_seconds=(
                gap if plan.last_activity_at != moment else 0),
            notes=note,
            shift_id=context.shift_at(moment).shift_id,
            created_by_component=SIMULATOR_COMPONENT,
        ))
        plan.last_activity_at = moment
        return 1

    def _team_for(self, plan: _Plan) -> int | None:
        context = self.context
        if plan.failure_category_id is not None:
            category = context.master.failure_categories.get(plan.failure_category_id)
            if category is not None:
                team = context.master.team_for_specialization(
                    category.required_specialization.value)
                return team.maintenance_team_id if team is not None else None
        team = next(iter(context.master.teams.values()), None)
        return team.maintenance_team_id if team is not None else None

    def _engineer_record(self, team_id: int | None) -> Any | None:
        if team_id is None:
            return None
        return self._pick_engineer(team_id)

    def _close(self, session: Session, plan: _Plan) -> int:
        """T-SIM-8. Close the job and advance the machine's maintenance counters."""
        context = self.context
        runtime = context.machines[plan.machine_id]
        record = session.get(MaintenanceWorkRecord, plan.work_record_id)
        moment = context.now

        record.work_status = "closed"
        record.closed_at = moment
        if record.completed_at is None:
            record.completed_at = moment
        if record.started_at is None:
            record.started_at = record.assigned_at or record.opened_at
        if record.actual_duration_minutes is None:
            record.actual_duration_minutes = max(
                int((record.completed_at - record.started_at).total_seconds() // 60), 1)
        record.machine_downtime_minutes = max(
            record.actual_duration_minutes,
            int((moment - record.started_at).total_seconds() // 60),
            1,
        )

        # §41.3: closure of corrective, predictive or emergency work requires a
        # confirmed failure category. Routine work confirms nothing because nothing
        # failed, which is why the requirement is scoped to those work types.
        if record.work_type.value in {"corrective", "emergency", "predictive"}:
            record.confirmed_failure_category_id = plan.failure_category_id
            category = context.master.failure_categories.get(plan.failure_category_id)
            record.resolution_note = (
                "%s addressed and verified. Machine returned to service."
                % (category.category_name if category is not None else "Fault")
            )
        else:
            record.resolution_note = (
                "Scheduled work completed to specification. No fault found."
            )

        # The counters advance here and nowhere else (E2 rule 8).
        self.state.persist_counters(session, plan.machine_id)
        status_hours = runtime.accumulated_operating_hours
        runtime.hours_at_last_maintenance = status_hours
        runtime.cycles_at_last_maintenance = runtime.accumulated_cycle_count
        status = session.get(MachineOperationalStatus, runtime.status_id)
        status.operating_hours_at_last_maintenance = status_hours
        status.cycle_count_at_last_maintenance = runtime.accumulated_cycle_count

        runtime.open_work_record_id = None
        runtime.work_stage = None
        runtime.work_next_step_at = None
        self._plans.pop(plan.work_record_id, None)
        return 1

    def restore_after_close(self, session: Session) -> int:
        """Bring machines that are down with no open job back to idle.

        A separate step from closure because the machine returns to service after the
        job is signed off, and the transition carries ``reason_code = 'restored'``.
        """
        context = self.context
        restored = 0
        for machine_id, runtime in context.machines.items():
            if runtime.open_work_record_id is not None:
                continue
            if runtime.state not in {"down_unplanned", "down_planned"}:
                continue
            self.state.transition(
                session, machine_id, "idle", "restored", run_id=None,
                notes="Returned to service after maintenance",
            )
            restored += 1
        return restored

    def open_job_count(self, session: Session) -> int:
        return len(list(session.scalars(
            select(MaintenanceWorkRecord.maintenance_work_record_id).where(
                MaintenanceWorkRecord.work_status.in_(
                    ["open", "assigned", "in_progress", "awaiting_parts", "completed"])
            )
        )))
