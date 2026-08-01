"""Operational models O8-O9: the quality finding, and the material write-off.

Two tables rather than one, because a finding is not a disposition: a failed
inspection may be reworked, accepted under concession, or quarantined, and
conflating them would systematically overstate material loss (§O9).

Both carry ``machine_id`` and ``attributed_machine_id`` as different columns. The
inspection happens at a station; the defect was caused somewhere else. Without the
separation, quality data would blame the inspection station for every defect it
discovered, and the link between machine degradation and product quality -- one of
the platform's most valuable inferences -- would be unavailable (§O8).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import (
    InspectionDisposition,
    InspectionType,
    ScrapReason,
)
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    require_timezone_aware,
)
from models.types import OperationalPk, Quantity, TimestampTz

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.failure import FailureCategory
    from models.master.people import Worker
    from models.master.plant import Shift
    from models.operational.events import OperationalEvent
    from models.operational.inventory import InventoryMovement
    from models.operational.production import ProductionRun


class QualityInspectionResult(TimestampCreatedMixin, ComponentProvenanceMixin,
                              Base):
    """Table ``quality_inspection_result``, operational group: the outcome of an
    inspection against sampled output, with the machine the defect is attributed to.
    Append-only."""

    __tablename__ = "quality_inspection_result"
    __table_args__ = (
        UniqueConstraint("quality_inspection_result_code", name="uq_qir_code"),
        CheckConstraint(
            "quality_inspection_result_code GLOB "
            "'QIR-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9]'",
            name=conv("ck_qir_code_format")),
        CheckConstraint("sample_size > 0",
                        name=conv("ck_qir_sample_size_positive")),
        CheckConstraint("pass_count >= 0 AND fail_count >= 0",
                        name=conv("ck_qir_counts_non_negative")),
        # Self-checking: the parts add up or the row is rejected.
        CheckConstraint("pass_count + fail_count = sample_size",
                        name=conv("ck_qir_counts_sum_to_sample")),
        CheckConstraint(
            "fail_count = 0 OR primary_defect_note IS NOT NULL",
            name=conv("ck_qir_defect_note_required")),
        # Attributing a defect to a machine without naming a mechanism is half an
        # inference: attribution is either fully usable or explicitly absent.
        CheckConstraint(
            "(attributed_machine_id IS NULL "
            "AND attributed_failure_category_id IS NULL) "
            "OR (attributed_machine_id IS NOT NULL "
            "AND attributed_failure_category_id IS NOT NULL)",
            name=conv("ck_qir_attribution_paired")),
        CheckConstraint(
            "inspection_type IN ('first_article', 'in_process', 'final', "
            "'audit')",
            name=conv("ck_qir_inspection_type_allowed")),
        CheckConstraint(
            "disposition IN ('accept', 'rework', 'scrap', 'quarantine')",
            name=conv("ck_qir_disposition_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_qir_created_by_component_allowed")),
        CheckConstraint("length(quality_inspection_result_code) <= 20",
                        name=conv("ck_qir_quality_inspection_result_code_length")),
        {"sqlite_autoincrement": True},
    )

    quality_inspection_result_id: Mapped[OperationalPk]
    quality_inspection_result_code: Mapped[str] = mapped_column(String(20))
    production_run_id: Mapped[int] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_qir_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Where inspected. NULL for a manual inspection.
    machine_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_qir_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Which machine caused it. Deliberately distinct from machine_id.
    attributed_machine_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_qir_attributed_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    attributed_failure_category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("failure_category.failure_category_id",
                   name="fk_qir_attributed_failure_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    inspected_at: Mapped[TimestampTz]
    inspection_type: Mapped[InspectionType] = mapped_column(
        Enum(InspectionType, name="inspection_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    sample_size: Mapped[int] = mapped_column(Integer)
    pass_count: Mapped[int] = mapped_column(Integer)
    fail_count: Mapped[int] = mapped_column(Integer)
    inspector_worker_id: Mapped[int] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_qir_inspector",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Deliberately separate from the finding: a failed inspection does not
    # automatically mean scrap.
    disposition: Mapped[InspectionDisposition] = mapped_column(
        Enum(InspectionDisposition, name="inspection_disposition",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Read by the Decision Agent as corroborating evidence, which is why a
    # recorded failure with no description is rejected.
    primary_defect_note: Mapped[Optional[str]] = mapped_column(Text)
    related_operational_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("operational_event.operational_event_id",
                   name="fk_qir_related_event",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_qir_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    production_run: Mapped["ProductionRun"] = relationship(
        back_populates="quality_inspections", lazy="select"
    )
    # Two keys to Machine in two distinct roles. No cycle: machine is master data
    # and references nothing operational (§37.6).
    machine: Mapped[Optional["Machine"]] = relationship(
        lazy="select", foreign_keys=[machine_id]
    )
    attributed_machine: Mapped[Optional["Machine"]] = relationship(
        lazy="select", foreign_keys=[attributed_machine_id]
    )
    attributed_failure_category: Mapped[Optional["FailureCategory"]] = relationship(
        lazy="select"
    )
    inspector: Mapped["Worker"] = relationship(lazy="select")
    related_operational_event: Mapped[Optional["OperationalEvent"]] = relationship(
        lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")
    scrap_records: Mapped[list["ScrapRecord"]] = relationship(
        back_populates="quality_inspection_result", lazy="select"
    )

    @validates("quality_inspection_result_code", "production_run_id",
               "machine_id", "attributed_machine_id",
               "attributed_failure_category_id", "inspected_at",
               "inspection_type", "sample_size", "pass_count", "fail_count",
               "inspector_worker_id", "disposition", "primary_defect_note",
               "related_operational_event_id", "shift_id", "production_run",
               "machine", "attributed_machine", "attributed_failure_category",
               "inspector", "related_operational_event", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``inspected_at`` must be timezone-aware (§41.3, §41.4)."""
        if inspect(self).persistent:
            raise ValueError(
                "quality_inspection_result is append-only; %s cannot be "
                "reassigned once the row is persistent" % key
            )
        if key == "inspected_at":
            return require_timezone_aware(key, value)
        if key == "quality_inspection_result_code":
            return value.strip().upper()
        return value


class ScrapRecord(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``scrap_record``, operational group: units written off, with quantity,
    reason and attribution. Append-only.

    No captured cost column: scrap cost is computed at read time as quantity times
    ``product.standard_material_cost``. Storing a captured cost would extend the
    single permitted master-value-copy exception to a second place with a much
    weaker justification than the threshold capture in O13 (§O9).
    """

    __tablename__ = "scrap_record"
    __table_args__ = (
        CheckConstraint("quantity_units > 0",
                        name=conv("ck_sr_quantity_positive")),
        CheckConstraint(
            "(attributed_machine_id IS NULL "
            "AND attributed_failure_category_id IS NULL) "
            "OR (attributed_machine_id IS NOT NULL "
            "AND attributed_failure_category_id IS NOT NULL)",
            name=conv("ck_sr_attribution_paired")),
        # The next two work in opposite directions and are the most valuable
        # constraints on this table: the first prevents under-attribution, which
        # would understate preventable loss; the second prevents
        # over-attribution, which would inflate a machine's figure with a
        # supplier's casting flaw. Together they make the preventable-loss metric
        # trustworthy by construction (§O9).
        CheckConstraint(
            "scrap_reason <> 'machine_fault' "
            "OR (attributed_machine_id IS NOT NULL "
            "AND attributed_failure_category_id IS NOT NULL)",
            name=conv("ck_sr_machine_fault_requires_attribution")),
        CheckConstraint(
            "scrap_reason NOT IN ('material_defect', 'handling_damage') "
            "OR (attributed_machine_id IS NULL "
            "AND attributed_failure_category_id IS NULL)",
            name=conv("ck_sr_non_machine_reasons_unattributed")),
        CheckConstraint(
            "scrap_reason IN ('dimensional_deviation', 'surface_defect', "
            "'tool_mark', 'material_defect', 'setup_reject', 'machine_fault', "
            "'handling_damage', 'process_deviation')",
            name=conv("ck_sr_scrap_reason_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_sr_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    scrap_record_id: Mapped[OperationalPk]
    production_run_id: Mapped[int] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_sr_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Where recorded.
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_sr_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Machine responsible. NULL for material or handling defects.
    attributed_machine_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_sr_attributed_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    attributed_failure_category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("failure_category.failure_category_id",
                   name="fk_sr_attributed_failure_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    recorded_at: Mapped[TimestampTz]
    quantity_units: Mapped[Quantity]
    scrap_reason: Mapped[ScrapReason] = mapped_column(
        Enum(ScrapReason, name="scrap_reason", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # NULL for scrap not arising from a formal inspection -- a setup reject
    # discarded by an operator, a part damaged in handling.
    quality_inspection_result_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("quality_inspection_result.quality_inspection_result_id",
                   name="fk_sr_inspection_result",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # The link that makes preventable loss measurable.
    related_operational_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("operational_event.operational_event_id",
                   name="fk_sr_related_event",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    recorded_by_worker_id: Mapped[int] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_sr_recorded_by",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_sr_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)

    production_run: Mapped["ProductionRun"] = relationship(
        back_populates="scrap_records", lazy="select"
    )
    machine: Mapped["Machine"] = relationship(
        lazy="select", foreign_keys=[machine_id]
    )
    attributed_machine: Mapped[Optional["Machine"]] = relationship(
        lazy="select", foreign_keys=[attributed_machine_id]
    )
    attributed_failure_category: Mapped[Optional["FailureCategory"]] = relationship(
        lazy="select"
    )
    quality_inspection_result: Mapped[Optional["QualityInspectionResult"]] = (
        relationship(back_populates="scrap_records", lazy="select")
    )
    related_operational_event: Mapped[Optional["OperationalEvent"]] = relationship(
        lazy="select"
    )
    recorded_by: Mapped["Worker"] = relationship(lazy="select")
    shift: Mapped["Shift"] = relationship(lazy="select")
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(
        back_populates="scrap_record", lazy="select"
    )

    @validates("production_run_id", "machine_id", "attributed_machine_id",
               "attributed_failure_category_id", "recorded_at",
               "quantity_units", "scrap_reason", "quality_inspection_result_id",
               "related_operational_event_id", "recorded_by_worker_id",
               "shift_id", "notes", "production_run", "machine",
               "attributed_machine", "attributed_failure_category",
               "quality_inspection_result", "related_operational_event",
               "recorded_by", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``recorded_at`` must be timezone-aware (§41.3, §41.4).

        A reversal is a compensating record, never an edit: a negative scrap must
        be its own row with its own reason.
        """
        if inspect(self).persistent:
            raise ValueError(
                "scrap_record is append-only; %s cannot be reassigned once the "
                "row is persistent. A reversal is a compensating record" % key
            )
        if key == "recorded_at":
            return require_timezone_aware(key, value)
        return value
