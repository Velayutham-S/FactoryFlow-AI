"""The context package: seven blocks, resolved once, preserved exactly.

This is the only place in the platform where consolidation is the goal rather than a
smell. §E17: "the Decision Agent receives one coherent package rather than issuing fifteen
queries, and the package is preserved exactly as the LLM saw it."

**Why preservation matters more than convenience.** "If the context were reassembled at
audit time, it would reflect current data rather than what was known at the decision
moment, and *'why did it recommend that?'* would become unanswerable. **The context
document is the input side of the explainability contract.**"

**No business rule values are copied.** The rule that governed the escalation is
referenced from the row's own ``applied_escalation_rule_id``. What the ``business`` block
carries are the resolved *rates* the Decision Agent needs to quantify impact, each beside
the code it came from, so the recommendation can cite them.

All seven blocks are mandatory when escalating (E17 rule 10): "A missing block means the
Decision Agent cannot satisfy the corresponding element of the §16.5 contract -- most
often business impact." Two further keys, ``readings`` and ``corroboration``, are supplied
alongside them because §E18's ``supporting_evidence`` requires both and this is the only
stage that can see the healthy envelope and the independent measurement paths at once.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.master import Machine
from models.operational import (
    CycleHistory,
    InventoryMovement,
    MachineOperationalStatus,
    MachineSensorReading,
    MaintenanceWorkRecord,
    OperationalAlert,
    OperationalEvent,
    PredictionResult,
    ProductionProgress,
    ProductionRun,
    QualityInspectionResult,
    ScrapRecord,
)

from supervisor.context import (
    LIVE_ALERT_STATUSES,
    OPEN_WORK_STATUSES,
    SupervisorContext,
)
from supervisor.errors import ContextAssemblyError

# How far back the corroboration and evidence lookbacks reach. Matches the 24-hour window
# §E15's machine-level block already uses for its two count features, so the Supervisor
# and the Prediction Agent describe the same recent past.
LOOKBACK = timedelta(hours=24)


def build_context_document(
    session: Session,
    context: SupervisorContext,
    alert: OperationalAlert,
    prediction: PredictionResult,
    assembled_at: datetime,
) -> dict[str, Any]:
    """Assemble the package for one escalated situation.

    Raises :class:`ContextAssemblyError` rather than returning a partial package: E17
    rule 2 forbids escalating without a document, and a document missing a block would
    escalate a situation the Decision Agent cannot fully reason about.
    """
    master = context.master
    if alert.machine_id is None:
        raise ContextAssemblyError(
            "alert %s has no machine, so no machine-scoped package can be assembled"
            % alert.operational_alert_code)
    machine = master.machines.get(alert.machine_id)
    if machine is None:
        raise ContextAssemblyError(
            "alert %s references machine %d, which is not in master data"
            % (alert.operational_alert_code, alert.machine_id))

    mode = _predicted_mode(context, machine, prediction)
    run, progress = _current_run(session, machine)

    document: dict[str, Any] = {
        "machine": _machine_block(session, context, machine, assembled_at),
        "production": _production_block(context, machine, run, progress),
        "cascade": _cascade_block(context, machine, progress),
        "inventory": _inventory_block(session, context, mode),
        "maintenance": _maintenance_block(session, context, machine, assembled_at),
        "business": _business_block(context, machine, run),
        "evidence": _evidence_block(session, context, alert),
        "readings": _readings_block(session, context, machine, assembled_at),
        "corroboration": _corroboration_block(
            session, context, machine, assembled_at),
        "traceability": {
            "triggering_alert": alert.operational_alert_code,
            "prediction_result": prediction.prediction_result_code,
            "assembled_at": assembled_at.isoformat(),
            "assembled_by": "supervisor_agent",
        },
    }

    missing = [
        name for name in
        ("machine", "production", "cascade", "inventory", "maintenance", "business",
         "evidence")
        if not document.get(name)
    ]
    if missing:
        raise ContextAssemblyError(
            "package for alert %s is missing the %s block(s); E17 rule 10 requires all "
            "seven" % (alert.operational_alert_code, ", ".join(missing)))
    return document


# ----------------------------------------------------------------- the seven blocks


def _machine_block(
    session: Session,
    context: SupervisorContext,
    machine: Machine,
    assembled_at: datetime,
) -> dict[str, Any]:
    """Current state, wear position, age, and the open cases against it."""
    master = context.master
    status = session.scalars(
        select(MachineOperationalStatus).where(
            MachineOperationalStatus.machine_id == machine.machine_id)
    ).first()

    hours = 0.0
    since_maintenance = 0.0
    state = None
    state_since = None
    if status is not None:
        hours = float(status.accumulated_operating_hours)
        at_last = (
            0.0 if status.operating_hours_at_last_maintenance is None
            else float(status.operating_hours_at_last_maintenance))
        since_maintenance = max(hours - at_last, 0.0)
        state = status.current_state.value
        state_since = status.state_since.isoformat()

    open_codes = sorted(session.scalars(
        select(OperationalAlert.operational_alert_code).where(
            OperationalAlert.machine_id == machine.machine_id,
            OperationalAlert.alert_status.in_(LIVE_ALERT_STATUSES),
        )
    ))
    age_days = None
    if machine.commissioned_date is not None:
        age_days = (assembled_at.astimezone(master.timezone).date()
                    - machine.commissioned_date).days

    machine_type = master.machine_types.get(machine.machine_type_id)
    return {
        "machine_code": machine.machine_code,
        "machine_name": machine.machine_name,
        "machine_type": None if machine_type is None else machine_type.machine_type_code,
        "criticality": machine.criticality.value,
        "current_state": state,
        "state_since": state_since,
        "accumulated_operating_hours": round(hours, 2),
        "hours_since_last_maintenance": round(since_maintenance, 2),
        "age_days": age_days,
        "open_alert_codes": open_codes,
    }


def _production_block(
    context: SupervisorContext,
    machine: Machine,
    run: ProductionRun | None,
    progress: ProductionProgress | None,
) -> dict[str, Any]:
    """The order at risk, its customer, and how far behind it is."""
    master = context.master
    line = master.lines.get(machine.production_line_id)
    block: dict[str, Any] = {
        "line": None if line is None else line.production_line_code,
        "run_code": None,
        "product": None,
        "customer": None,
        "customer_tier": None,
        "quantity_remaining": None,
        "current_rate": None,
        "schedule_variance_minutes": None,
        "is_behind_schedule": None,
        "due_date": None,
        "run_priority": None,
    }
    if run is None:
        return block

    product = master.products.get(run.product_id)
    customer = master.customers.get(run.customer_id)
    block.update({
        "run_code": run.production_run_code,
        "product": None if product is None else product.product_code,
        "customer": None if customer is None else customer.customer_code,
        "customer_tier": None if customer is None else customer.priority_tier.value,
        "due_date": run.due_date.isoformat(),
        "run_priority": run.priority.value,
    })
    if progress is not None:
        remaining = int(run.planned_quantity_units) - int(
            progress.quantity_good_cumulative)
        block.update({
            "quantity_remaining": max(remaining, 0),
            "current_rate": float(progress.current_rate_units_per_hour),
            "schedule_variance_minutes": int(progress.schedule_variance_minutes),
            "is_behind_schedule": bool(progress.is_behind_schedule),
            "percent_complete": float(progress.percent_complete),
            "scrap_rate_pct": float(progress.scrap_rate_pct),
        })
    return block


def _cascade_block(
    context: SupervisorContext,
    machine: Machine,
    progress: ProductionProgress | None,
) -> dict[str, Any]:
    """Where the machine sits on the line, and how long the buffer lasts.

    The grace period is the buffer divided by the achieved rate. §E18 rule 9 makes it
    load-bearing: "A deadline later than the line will starve is not a usable deadline."
    """
    master = context.master
    downstream = master.downstream_of(machine)
    on_line = master.machines_on_line.get(machine.production_line_id, [])

    rate = None
    if progress is not None and progress.current_rate_units_per_hour:
        rate = float(progress.current_rate_units_per_hour)
    elif machine.rated_capacity_units_per_hour is not None:
        rate = float(machine.rated_capacity_units_per_hour)

    buffer_units = (
        None if machine.downstream_buffer_units is None
        else int(machine.downstream_buffer_units))
    grace = None
    if buffer_units is not None and rate:
        grace = round((buffer_units / rate) * 60.0, 1)

    return {
        "line_position": int(machine.line_position),
        "line_machine_count": len(on_line),
        "is_bottleneck": bool(machine.is_bottleneck),
        "downstream_buffer_units": buffer_units,
        "grace_period_minutes": grace,
        "downstream": [other.machine_code for other in downstream],
    }


def _inventory_block(
    session: Session,
    context: SupervisorContext,
    mode: Any | None,
) -> dict[str, Any]:
    """The spare the predicted failure needs, and whether it is on the shelf.

    The part is the failure mode's ``required_inventory_item_id`` -- §E18 rule 7 makes the
    Decision Agent derive it from the failure rather than guess, so it is resolved here
    from the same source. On-hand quantity comes from the newest movement's
    ``resulting_quantity_on_hand``, which is the running balance the simulator maintains.
    """
    master = context.master
    block: dict[str, Any] = {
        "required_item": None,
        "quantity_on_hand": None,
        "reorder_point": None,
        "safety_stock_qty": None,
        "lead_time_days": None,
        "retrieval_time_minutes": None,
        "is_critical_spare": None,
        "location": None,
    }
    if mode is None or mode.required_inventory_item_id is None:
        block["note"] = "The predicted failure mode declares no required spare part."
        return block

    item = master.items.get(mode.required_inventory_item_id)
    if item is None:
        block["note"] = "The declared spare part is not present in master data."
        return block

    location = master.locations.get(item.default_inventory_location_id)
    on_hand = session.scalars(
        select(InventoryMovement.resulting_quantity_on_hand)
        .where(InventoryMovement.inventory_item_id == item.inventory_item_id)
        .order_by(InventoryMovement.movement_at.desc(),
                  InventoryMovement.inventory_movement_id.desc())
        .limit(1)
    ).first()

    block.update({
        "required_item": item.inventory_item_code,
        "item_name": item.item_name,
        "quantity_on_hand": None if on_hand is None else float(on_hand),
        "reorder_point": float(item.reorder_point),
        "safety_stock_qty": float(item.safety_stock_qty),
        "lead_time_days": int(item.lead_time_days),
        "retrieval_time_minutes": (
            None if location is None
            else int(location.average_retrieval_time_minutes)),
        "is_critical_spare": bool(item.is_critical_spare),
        "location": None if location is None else location.inventory_location_code,
    })
    return block


def _maintenance_block(
    session: Session,
    context: SupervisorContext,
    machine: Machine,
    assembled_at: datetime,
) -> dict[str, Any]:
    """Due schedules with their deferability, open jobs, and who could attend.

    The due computation is why master data refused to cache a next-due date (§M26): due
    status is derived from operational counters, and §E18 calls the resulting ability to
    combine two jobs into one stoppage "the most valuable thing in the whole document".
    """
    master = context.master
    status = session.scalars(
        select(MachineOperationalStatus).where(
            MachineOperationalStatus.machine_id == machine.machine_id)
    ).first()
    hours = 0.0 if status is None else float(status.accumulated_operating_hours)
    cycles = 0 if status is None else int(status.accumulated_cycle_count)
    at_hours = (
        0.0 if status is None or status.operating_hours_at_last_maintenance is None
        else float(status.operating_hours_at_last_maintenance))
    at_cycles = (
        0 if status is None or status.cycle_count_at_last_maintenance is None
        else int(status.cycle_count_at_last_maintenance))

    due: list[dict[str, Any]] = []
    for schedule in master.schedules_by_machine.get(machine.machine_id, []):
        basis = schedule.interval_basis.value
        interval = float(schedule.interval_value)
        remaining: float | None = None
        if basis == "operating_hours" and interval > 0:
            remaining = round(interval - (hours - at_hours), 2)
        elif basis == "cycle_count" and interval > 0:
            remaining = float(int(interval) - (cycles - at_cycles))
        due.append({
            "schedule": schedule.machine_maintenance_schedule_code,
            "maintenance_type": schedule.maintenance_type.value,
            "interval_basis": basis,
            "due_in_operating_hours": remaining if basis == "operating_hours" else None,
            "due_in_cycles": remaining if basis == "cycle_count" else None,
            "estimated_duration_minutes": int(schedule.estimated_duration_minutes),
            "requires_line_stop": bool(schedule.requires_line_stop),
            "can_be_deferred": bool(schedule.can_be_deferred),
            "max_deferral_days": (
                None if schedule.max_deferral_days is None
                else int(schedule.max_deferral_days)),
        })
    due.sort(key=lambda entry: (
        entry["due_in_operating_hours"] is None,
        entry["due_in_operating_hours"] if entry["due_in_operating_hours"] is not None
        else 0.0,
    ))

    open_jobs = [
        {
            "work_record": job.maintenance_work_record_code,
            "work_type": job.work_type.value,
            "work_status": job.work_status.value,
            "opened_at": job.opened_at.isoformat(),
        }
        for job in session.scalars(
            select(MaintenanceWorkRecord).where(
                MaintenanceWorkRecord.machine_id == machine.machine_id,
                MaintenanceWorkRecord.work_status.in_(OPEN_WORK_STATUSES),
            ).order_by(MaintenanceWorkRecord.opened_at)
        )
    ]

    shift = master.shift_at(assembled_at)
    today = assembled_at.astimezone(master.timezone).date()
    teams: list[dict[str, Any]] = []
    for team in master.teams.values():
        pool = []
        for engineer in master.engineers.values():
            if engineer.maintenance_team_id != team.maintenance_team_id:
                continue
            worker = master.workers.get(engineer.worker_id)
            certified = (
                engineer.certification_expiry_date is None
                or engineer.certification_expiry_date >= today)
            on_shift = worker is not None and worker.shift_id == shift.shift_id
            pool.append({
                "engineer": engineer.maintenance_engineer_code,
                "primary_specialization": engineer.primary_specialization.value,
                "secondary_specialization": (
                    None if engineer.secondary_specialization is None
                    else engineer.secondary_specialization.value),
                "certification_valid": certified,
                "certification_expiry": (
                    None if engineer.certification_expiry_date is None
                    else engineer.certification_expiry_date.isoformat()),
                "on_shift": on_shift,
                "is_on_call": bool(engineer.is_on_call),
                "available": certified and (on_shift or bool(engineer.is_on_call)),
                "years_experience": int(engineer.years_experience),
            })
        pool.sort(key=lambda entry: entry["engineer"])
        teams.append({
            "team": team.maintenance_team_code,
            "specialization": team.specialization.value,
            "is_emergency_response": bool(team.is_emergency_response),
            "target_response_time_minutes": int(team.target_response_time_minutes),
            "on_shift": team.shift_id == shift.shift_id,
            "engineers": pool,
        })
    teams.sort(key=lambda entry: entry["team"])

    return {
        "current_shift": shift.shift_code,
        "due_schedules": due,
        "open_work_records": open_jobs,
        "teams": teams,
    }


def _business_block(
    context: SupervisorContext,
    machine: Machine,
    run: ProductionRun | None,
) -> dict[str, Any]:
    """The policy rates the Decision Agent needs, each beside the code it came from."""
    master = context.master
    policy = context.policy
    line_id = machine.production_line_id
    cost = policy.downtime_cost(line_id)
    threshold = policy.probability_threshold(line_id)
    customer = None if run is None else master.customers.get(run.customer_id)
    product = None if run is None else master.products.get(run.product_id)

    return {
        "downtime_cost_per_hour": None if cost is None else cost.numeric,
        "downtime_cost_rule": None if cost is None else cost.code,
        "customer_penalty_per_day": (
            None if customer is None or customer.late_delivery_penalty_per_day is None
            else float(customer.late_delivery_penalty_per_day)),
        "customer_otd_target_pct": (
            None if customer is None or customer.contractual_otd_target_pct is None
            else float(customer.contractual_otd_target_pct)),
        "contribution_margin_per_unit": (
            None if product is None
            else float(master.contribution_margin(product))),
        "priority_weights": [
            {"rule": rule.code, "name": rule.name, "value": rule.numeric,
             "unit": rule.unit}
            for rule in policy.priority_weights
        ],
        "escalation_threshold": None if threshold is None else threshold.numeric,
        "escalation_rule": None if threshold is None else threshold.code,
        "severity_floor": (
            None if policy.severity_floor is None else policy.severity_floor.text),
        "severity_floor_rule": (
            None if policy.severity_floor is None else policy.severity_floor.code),
    }


def _evidence_block(
    session: Session,
    context: SupervisorContext,
    alert: OperationalAlert,
) -> list[dict[str, Any]]:
    """The observations that raised the case, with their limits as they were.

    Thresholds are read from the event rows rather than recomputed from current master
    data: §E13 stores the limit that actually fired so a retuned profile cannot rewrite
    history.
    """
    master = context.master
    entries: list[dict[str, Any]] = []
    for event in session.scalars(
        select(OperationalEvent)
        .where(OperationalEvent.operational_alert_id == alert.operational_alert_id)
        .order_by(OperationalEvent.detected_at)
    ):
        parameter = (
            None if event.machine_parameter_id is None
            else master.parameters.get(event.machine_parameter_id))
        entries.append({
            "code": event.operational_event_code,
            "event_type": event.event_type.value,
            "parameter": None if parameter is None else parameter.machine_parameter_code,
            "observed": (
                None if event.observed_value is None else float(event.observed_value)),
            "threshold": (
                None if event.threshold_value_breached is None
                else float(event.threshold_value_breached)),
            "direction": (
                None if event.threshold_direction is None
                else event.threshold_direction.value),
            "unit": None if parameter is None else parameter.unit_of_measure,
            "detected_at": event.detected_at.isoformat(),
            "sustained_duration_seconds": event.sustained_duration_seconds,
        })
    return entries


# ------------------------------------------- two keys §E18's evidence contract needs


def _readings_block(
    session: Session,
    context: SupervisorContext,
    machine: Machine,
    assembled_at: datetime,
) -> list[dict[str, Any]]:
    """Latest value per parameter against its healthy maximum.

    ``healthy_max`` is ``machine_type_parameter.normal_max`` -- the envelope for a machine
    of this type running correctly -- which is a different number from the warning limit
    an event breached, and §E18's readings array wants the former.
    """
    master = context.master
    since = assembled_at - LOOKBACK
    rows = session.execute(
        select(
            MachineSensorReading.machine_parameter_id,
            func.max(MachineSensorReading.recorded_at),
        )
        .where(
            MachineSensorReading.machine_id == machine.machine_id,
            MachineSensorReading.recorded_at > since,
            MachineSensorReading.recorded_at <= assembled_at,
            MachineSensorReading.quality_flag == "valid",
        )
        .group_by(MachineSensorReading.machine_parameter_id)
    ).all()

    readings: list[dict[str, Any]] = []
    for parameter_id, newest in rows:
        value = session.scalars(
            select(MachineSensorReading.reading_value).where(
                MachineSensorReading.machine_id == machine.machine_id,
                MachineSensorReading.machine_parameter_id == parameter_id,
                MachineSensorReading.recorded_at == newest,
            ).limit(1)
        ).first()
        if value is None:
            continue
        parameter = master.parameters.get(parameter_id)
        declaration = master.declarations.get(
            (machine.machine_type_id, parameter_id))
        latest = float(value)
        healthy_max = (
            None if declaration is None else float(declaration.normal_max))
        pct_above = None
        if healthy_max and latest > healthy_max:
            pct_above = round(((latest - healthy_max) / healthy_max) * 100.0, 2)
        readings.append({
            "parameter": None if parameter is None else parameter.machine_parameter_code,
            "latest": latest,
            "healthy_max": healthy_max,
            "pct_above": pct_above,
            "unit": None if parameter is None else parameter.unit_of_measure,
            "recorded_at": newest.isoformat(),
        })
    readings.sort(key=lambda entry: str(entry["parameter"]))
    return readings


def _corroboration_block(
    session: Session,
    context: SupervisorContext,
    machine: Machine,
    assembled_at: datetime,
) -> list[dict[str, Any]]:
    """Findings from measurement paths independent of the sensors.

    §E18 rule 4 keys ``root_cause_confidence = 'high'`` to having at least two of these,
    and its worked example draws them from three separate instruments -- accelerometer,
    cycle timer, coordinate measuring machine. Each entry names its **source entity** so
    the Decision Agent can count independence rather than assume it.
    """
    since = assembled_at - LOOKBACK
    found: list[dict[str, Any]] = []

    deviation = session.execute(
        select(func.avg(CycleHistory.deviation_from_standard_pct),
               func.count())
        .where(
            CycleHistory.machine_id == machine.machine_id,
            CycleHistory.interrupted.is_(False),
            CycleHistory.deviation_from_standard_pct.is_not(None),
            CycleHistory.cycle_ended_at > since,
            CycleHistory.cycle_ended_at <= assembled_at,
        )
    ).one()
    if deviation[1] and deviation[0] is not None:
        found.append({
            "source": "cycle_history",
            "finding": (
                "mean cycle deviation %.2f %% over %d completed cycles, measured by the "
                "machine's own timer rather than any sensor"
                % (float(deviation[0]), int(deviation[1]))),
        })

    inspections = list(session.scalars(
        select(QualityInspectionResult).where(
            QualityInspectionResult.attributed_machine_id == machine.machine_id,
            QualityInspectionResult.fail_count > 0,
            QualityInspectionResult.inspected_at > since,
            QualityInspectionResult.inspected_at <= assembled_at,
        ).order_by(QualityInspectionResult.inspected_at.desc()).limit(3)
    ))
    if inspections:
        newest = inspections[0]
        found.append({
            "source": "quality_inspection_result",
            "finding": (
                "%s recorded %d failed unit(s) of %d inspected, dispositioned %s and "
                "attributed to this machine"
                % (newest.quality_inspection_result_code, int(newest.fail_count),
                   int(newest.sample_size), newest.disposition.value)),
        })

    scrap = session.execute(
        select(func.count(), func.sum(ScrapRecord.quantity_units))
        .where(
            ScrapRecord.attributed_machine_id == machine.machine_id,
            ScrapRecord.recorded_at > since,
            ScrapRecord.recorded_at <= assembled_at,
        )
    ).one()
    if scrap[0]:
        found.append({
            "source": "scrap_record",
            "finding": (
                "%d scrap record(s) totalling %s unit(s) attributed to this machine in "
                "the last 24 hours"
                % (int(scrap[0]), "0" if scrap[1] is None else str(float(scrap[1])))),
        })

    return found


# ------------------------------------------------------------------ small helpers


def _predicted_mode(
    context: SupervisorContext,
    machine: Machine,
    prediction: PredictionResult,
) -> Any | None:
    """The failure mode the prediction attributed, if any."""
    modes = context.master.modes_by_type.get(machine.machine_type_id, [])
    if prediction.machine_type_failure_mode_id is not None:
        for mode in modes:
            if (mode.machine_type_failure_mode_id
                    == prediction.machine_type_failure_mode_id):
                return mode
    if prediction.predicted_failure_category_id is not None:
        for mode in modes:
            if mode.failure_category_id == prediction.predicted_failure_category_id:
                return mode
    return None


def _current_run(
    session: Session,
    machine: Machine,
) -> tuple[ProductionRun | None, ProductionProgress | None]:
    """The run the machine is engaged on, and its newest progress snapshot."""
    status = session.scalars(
        select(MachineOperationalStatus).where(
            MachineOperationalStatus.machine_id == machine.machine_id)
    ).first()

    run: ProductionRun | None = None
    if status is not None and status.current_production_run_id is not None:
        run = session.get(ProductionRun, status.current_production_run_id)
    if run is None:
        run = session.scalars(
            select(ProductionRun)
            .where(
                ProductionRun.production_line_id == machine.production_line_id,
                ProductionRun.run_status.in_(["setup", "running", "paused"]),
            )
            .order_by(ProductionRun.planned_start_at.desc())
            .limit(1)
        ).first()
    if run is None:
        return None, None

    progress = session.scalars(
        select(ProductionProgress)
        .where(ProductionProgress.production_run_id == run.production_run_id)
        .order_by(ProductionProgress.snapshot_at.desc())
        .limit(1)
    ).first()
    return run, progress
