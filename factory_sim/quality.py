"""Quality Engine — inspections, findings, and scrap.

Owns ``quality_inspection_result`` and ``scrap_record``, and T-SIM-6: an inspection, any
scrap arising from it, and the ``inventory_movement`` recording the material written off,
all in one unit of work. Atomic because an inspection with ``disposition = 'scrap'`` must
have a corresponding scrap record, and scrap consuming material must have a corresponding
movement — a partial application would leave stock overstating what is physically present.

**A finding is not a disposition.** The inspection records what was found; the scrap record
records that material was written off as a result. Not every failed inspection becomes
scrap: a part may be reworked and recovered, accepted under concession, or quarantined.
Conflating the two would erase the recovered material and systematically overstate loss.

**Attribution is claimed only when it is known.** ``machine_id`` is where the inspection
happened; ``attributed_machine_id`` is the machine that caused the defect. The two are
different fields because on a line the inspection station is not the station that made
the part. Attribution is populated only when the machine is actually incubating a failure
whose category is known, and ``attributed_failure_category_id`` moves with it — the schema
pairs them, and attributing a defect to a machine without naming a mechanism is half an
inference. Material and handling causes are never attributed, because blaming a machine
for a supplier's casting flaw would corrupt the preventable-loss figure the platform is
ultimately judged on.

**Outcomes are not random.** Failure probability rises with the attributed machine's
degradation, so a developing fault shows up in dimensional results before it shows up as a
breakdown. That is the third independent measurement path — sensor, cycle time, geometry —
and having three is what makes a root-cause claim defensible later.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from models.operational import QualityInspectionResult, ScrapRecord

from factory_sim.context import SIMULATOR_COMPONENT, SimulationContext
from factory_sim.inventory import InventoryEngine
from factory_sim.production import CycleScrap

# Cycles between in-process inspections on a station.
INSPECTION_CYCLE_INTERVAL = 25


class QualityEngine:
    """Inspection activity and material write-off."""

    def __init__(self, context: SimulationContext, inventory: InventoryEngine) -> None:
        self.context = context
        self.inventory = inventory

    # -------------------------------------------------------------------- T-SIM-6

    def inspect(self, session: Session, degradation: dict[int, float]) -> tuple[int, int]:
        """Run due inspections. Returns ``(inspections, scrap_records)``.

        The inspecting station is the line's last position, which on a machining line is
        where a coordinate measuring machine sits. Defects found there are attributed
        back to the station that made the part.
        """
        context = self.context
        inspections = 0
        scraps = 0

        for line_id, line_runtime in context.lines.items():
            if line_runtime.run_id is None or line_runtime.run_status != "running":
                continue
            machines = [
                m for m in context.master.machines_by_line.get(line_id, [])
                if m.lifecycle_status.value == "in_service"
            ]
            if not machines:
                continue
            station = machines[-1]
            upstream = machines[:-1] or machines

            station_runtime = context.machines[station.machine_id]
            if station_runtime.cycles_since_inspection < INSPECTION_CYCLE_INTERVAL:
                continue
            station_runtime.cycles_since_inspection = 0

            # Attribute to the most degraded upstream machine, if any is degrading.
            worst_id = None
            worst = 0.0
            for machine in upstream:
                progress = degradation.get(machine.machine_id, 0.0)
                if progress > worst:
                    worst = progress
                    worst_id = machine.machine_id

            sample = context.rng.randint(3, 5)
            fail_chance = 0.02 + 0.55 * worst
            fails = sum(
                1 for _ in range(sample) if context.rng.random() < fail_chance)
            passes = sample - fails

            attributed_machine_id = None
            attributed_category_id = None
            if fails > 0 and worst_id is not None:
                mode = context.machines[worst_id].incubating_mode
                if mode is not None:
                    attributed_machine_id = worst_id
                    attributed_category_id = mode.failure_category_id

            if fails == 0:
                disposition = "accept"
            elif attributed_machine_id is not None and context.rng.random() < 0.55:
                disposition = "scrap"
            else:
                disposition = "rework" if context.rng.random() < 0.7 else "quarantine"

            note = None
            if fails > 0:
                category = (
                    context.master.failure_categories.get(attributed_category_id)
                    if attributed_category_id else None
                )
                note = (
                    "%d of %d units outside specification%s"
                    % (
                        fails, sample,
                        "; consistent with %s" % category.category_name
                        if category is not None else "",
                    )
                )

            inspector = context.pick(context.master.inspectors)
            moment = context.now
            inspection = QualityInspectionResult(
                quality_inspection_result_code=context.inspection_code(moment),
                production_run_id=line_runtime.run_id,
                machine_id=station.machine_id,
                attributed_machine_id=attributed_machine_id,
                attributed_failure_category_id=attributed_category_id,
                inspected_at=moment,
                inspection_type="in_process",
                sample_size=sample,
                pass_count=passes,
                fail_count=fails,
                inspector_worker_id=inspector.worker_id,
                disposition=disposition,
                primary_defect_note=note,
                shift_id=context.shift_at(moment).shift_id,
                created_by_component=SIMULATOR_COMPONENT,
            )
            session.add(inspection)
            session.flush()
            inspections += 1

            # E8 rule 5: disposition 'scrap' requires a scrap record.
            if disposition == "scrap":
                self._write_scrap(
                    session,
                    run_id=line_runtime.run_id,
                    product_id=line_runtime.product_id,
                    machine_id=station.machine_id,
                    attributed_machine_id=attributed_machine_id,
                    attributed_category_id=attributed_category_id,
                    quantity=Decimal(fails).quantize(Decimal("0.01")),
                    reason="dimensional_deviation",
                    at=moment,
                    worker_id=inspector.worker_id,
                    inspection_id=inspection.quality_inspection_result_id,
                )
                line_runtime.quantity_scrapped += Decimal(fails)
                scraps += 1

        return inspections, scraps

    def record_cycle_scrap(
        self,
        session: Session,
        pending: list[CycleScrap],
    ) -> int:
        """Write the scrap record for each cycle whose outcome was scrap.

        §9.4 requires a scrap cycle outcome to have a corresponding scrap record. These
        have no originating inspection — an operator discarding a bad part does not raise
        one — which is exactly why ``quality_inspection_result_id`` is nullable.
        """
        context = self.context
        written = 0
        for item in pending:
            line_runtime = None
            for runtime in context.lines.values():
                if runtime.run_id == item.run_id:
                    line_runtime = runtime
                    break
            product_id = line_runtime.product_id if line_runtime else None
            recorder = context.pick(context.master.operators or context.master.inspectors)
            self._write_scrap(
                session,
                run_id=item.run_id,
                product_id=product_id,
                machine_id=item.machine_id,
                attributed_machine_id=item.attributed_machine_id,
                attributed_category_id=item.attributed_failure_category_id,
                quantity=item.quantity,
                reason=item.reason,
                at=item.at,
                worker_id=recorder.worker_id,
                inspection_id=None,
            )
            written += 1
        return written

    def _write_scrap(
        self,
        session: Session,
        *,
        run_id: int,
        product_id: int | None,
        machine_id: int,
        attributed_machine_id: int | None,
        attributed_category_id: int | None,
        quantity: Decimal,
        reason: str,
        at: datetime,
        worker_id: int,
        inspection_id: int | None,
    ) -> None:
        """One scrap record, plus the material it consumed."""
        context = self.context

        # ck_sr_non_machine_reasons_unattributed: neither of these is machine-caused.
        if reason in {"material_defect", "handling_damage"}:
            attributed_machine_id = None
            attributed_category_id = None
        # ck_sr_attribution_paired: both, or neither.
        if attributed_machine_id is None or attributed_category_id is None:
            attributed_machine_id = None
            attributed_category_id = None
        # ck_sr_machine_fault_requires_attribution.
        if reason == "machine_fault" and attributed_machine_id is None:
            reason = "process_deviation"

        record = ScrapRecord(
            production_run_id=run_id,
            machine_id=machine_id,
            attributed_machine_id=attributed_machine_id,
            attributed_failure_category_id=attributed_category_id,
            recorded_at=at,
            quantity_units=quantity,
            scrap_reason=reason,
            quality_inspection_result_id=inspection_id,
            recorded_by_worker_id=worker_id,
            shift_id=context.shift_at(at).shift_id,
            created_by_component=SIMULATOR_COMPONENT,
        )
        session.add(record)
        session.flush()

        if product_id is not None:
            self.inventory.consume_for_scrap(
                session,
                scrap_record_id=record.scrap_record_id,
                product_id=product_id,
                quantity_units=quantity,
                at=at,
                worker_id=worker_id,
            )

    def first_article(self, session: Session, line_id: int) -> int:
        """A first-article inspection after a changeover.

        ``first_article`` is what checks a changeover, so it is written when a run starts
        rather than on the in-process cadence.
        """
        context = self.context
        line_runtime = context.lines[line_id]
        if line_runtime.run_id is None:
            return 0
        machines = [
            m for m in context.master.machines_by_line.get(line_id, [])
            if m.lifecycle_status.value == "in_service"
        ]
        if not machines:
            return 0

        inspector = context.pick(context.master.inspectors)
        moment = context.now
        sample = 3
        session.add(QualityInspectionResult(
            quality_inspection_result_code=context.inspection_code(moment),
            production_run_id=line_runtime.run_id,
            machine_id=machines[-1].machine_id,
            attributed_machine_id=None,
            attributed_failure_category_id=None,
            inspected_at=moment,
            inspection_type="first_article",
            sample_size=sample,
            pass_count=sample,
            fail_count=0,
            inspector_worker_id=inspector.worker_id,
            disposition="accept",
            primary_defect_note=None,
            shift_id=context.shift_at(moment).shift_id,
            created_by_component=SIMULATOR_COMPONENT,
        ))
        return 1
