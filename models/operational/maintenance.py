"""Operational models O11-O12: the maintenance job, and the timeline inside it.

``MaintenanceWorkRecord`` is the platform's accuracy scorecard: comparing
``reported_failure_category_id`` -- what was suspected at open, normally from the
prediction -- against ``confirmed_failure_category_id`` -- what the engineer
actually found -- is the only honest measure of whether the predictions are
correct. A model with 0.90 average confidence and a 40% confirmation rate is not a
good model, and no internal validation metric would reveal that (§O11).
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
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import (
    MaintenanceActivityType,
    MaintenanceWorkStatus,
    MaintenanceWorkType,
)
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    TimestampUpdatedMixin,
    require_timezone_aware,
)
from models.types import OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.failure import FailureCategory, FailureSeverityLevel
    from models.master.maintenance import MachineMaintenanceSchedule
    from models.master.people import MaintenanceEngineer, MaintenanceTeam, Worker
    from models.master.plant import Shift
    from models.operational.decision import AiRecommendation
    from models.operational.events import OperationalAlert
    from models.operational.inventory import InventoryMovement


class MaintenanceWorkRecord(TimestampCreatedMixin, ComponentProvenanceMixin,
                            TimestampUpdatedMixin, Base):
    """Table ``maintenance_work_record``, operational group: one maintenance job from
    request to closure, and the maintenance history the master model deliberately
    did not cache."""

    __tablename__ = "maintenance_work_record"
    __table_args__ = (
        UniqueConstraint("maintenance_work_record_code", name="uq_mwr_code"),
        CheckConstraint(
            "maintenance_work_record_code GLOB "
            "'WO-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'",
            name=conv("ck_mwr_code_format")),
        # This is the definition of predictive work, and it makes the platform's
        # own value metric self-enforcing (§O11).
        CheckConstraint(
            "work_type <> 'predictive' "
            "OR triggering_recommendation_id IS NOT NULL",
            name=conv("ck_mwr_predictive_requires_recommendation")),
        # The lifecycle chain, enforced for every pair where both values are
        # present. opened_at is NOT NULL so it needs no null guard.
        CheckConstraint(
            "(assigned_at IS NULL OR opened_at <= assigned_at) "
            "AND (started_at IS NULL OR opened_at <= started_at) "
            "AND (completed_at IS NULL OR opened_at <= completed_at) "
            "AND (closed_at IS NULL OR opened_at <= closed_at) "
            "AND (assigned_at IS NULL OR started_at IS NULL "
            "OR assigned_at <= started_at) "
            "AND (assigned_at IS NULL OR completed_at IS NULL "
            "OR assigned_at <= completed_at) "
            "AND (assigned_at IS NULL OR closed_at IS NULL "
            "OR assigned_at <= closed_at) "
            "AND (started_at IS NULL OR completed_at IS NULL "
            "OR started_at <= completed_at) "
            "AND (started_at IS NULL OR closed_at IS NULL "
            "OR started_at <= closed_at) "
            "AND (completed_at IS NULL OR closed_at IS NULL "
            "OR completed_at <= closed_at)",
            name=conv("ck_mwr_timestamp_sequence")),
        CheckConstraint(
            "work_status <> 'closed' "
            "OR (closed_at IS NOT NULL AND resolution_note IS NOT NULL)",
            name=conv("ck_mwr_closed_requires_resolution")),
        CheckConstraint(
            "(planned_duration_minutes IS NULL "
            "OR planned_duration_minutes > 0) "
            "AND (actual_duration_minutes IS NULL "
            "OR actual_duration_minutes > 0) "
            "AND (machine_downtime_minutes IS NULL "
            "OR machine_downtime_minutes > 0)",
            name=conv("ck_mwr_durations_positive")),
        # Downtime includes handover and restart that working time excludes.
        CheckConstraint(
            "machine_downtime_minutes IS NULL "
            "OR actual_duration_minutes IS NULL "
            "OR machine_downtime_minutes >= actual_duration_minutes",
            name=conv("ck_mwr_downtime_at_least_duration")),
        CheckConstraint(
            "work_status IN ('open', 'cancelled') "
            "OR assigned_maintenance_team_id IS NOT NULL",
            name=conv("ck_mwr_assigned_requires_team")),
        CheckConstraint(
            "work_type IN ('preventive', 'predictive', 'corrective', "
            "'emergency', 'calibration', 'inspection')",
            name=conv("ck_mwr_work_type_allowed")),
        CheckConstraint(
            "work_status IN ('open', 'assigned', 'in_progress', "
            "'awaiting_parts', 'completed', 'closed', 'cancelled')",
            name=conv("ck_mwr_work_status_allowed")),
        CheckConstraint("did_stop_line IN (0, 1)",
                        name=conv("ck_mwr_did_stop_line_bool")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_mwr_created_by_component_allowed")),
        CheckConstraint("length(maintenance_work_record_code) <= 14",
                        name=conv("ck_mwr_maintenance_work_record_code_length")),
        {"sqlite_autoincrement": True},
    )

    maintenance_work_record_id: Mapped[OperationalPk]
    maintenance_work_record_code: Mapped[str] = mapped_column(String(14))
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_mwr_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # 'predictive' means the job exists because FactoryFlow AI recommended it.
    # Counting predictive jobs that displaced corrective ones is the business
    # case expressed in data.
    work_type: Mapped[MaintenanceWorkType] = mapped_column(
        Enum(MaintenanceWorkType, name="maintenance_work_type",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Populating this is what marks the schedule as performed (§M26).
    machine_maintenance_schedule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine_maintenance_schedule.machine_maintenance_schedule_id",
                   name="fk_mwr_schedule",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    triggering_alert_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("operational_alert.operational_alert_id",
                   name="fk_mwr_triggering_alert",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    triggering_recommendation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ai_recommendation.ai_recommendation_id",
                   name="fk_mwr_triggering_recommendation",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Suspected at open, normally from the prediction.
    reported_failure_category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("failure_category.failure_category_id",
                   name="fk_mwr_reported_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # The ground truth every prediction is scored against.
    confirmed_failure_category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("failure_category.failure_category_id",
                   name="fk_mwr_confirmed_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    priority_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_mwr_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    assigned_maintenance_team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_team.maintenance_team_id", name="fk_mwr_team",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    assigned_engineer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_engineer.maintenance_engineer_id",
                   name="fk_mwr_engineer",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    work_status: Mapped[MaintenanceWorkStatus] = mapped_column(
        Enum(MaintenanceWorkStatus, name="maintenance_work_status",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        server_default=text("'open'"),
    )
    opened_at: Mapped[TimestampTz]
    assigned_at: Mapped[Optional[TimestampTz]]
    started_at: Mapped[Optional[TimestampTz]]
    completed_at: Mapped[Optional[TimestampTz]]
    # Only on closure do the machine's maintenance counters update.
    closed_at: Mapped[Optional[TimestampTz]]
    planned_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    # Compared against planned to validate the failure mode's estimates.
    actual_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    # Exceeds working time by handover and restart, which is why it is a separate
    # column rather than a derivation.
    machine_downtime_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    # The difference between a nuisance and a production loss.
    did_stop_line: Mapped[bool] = mapped_column(server_default=text("0"))
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)
    # Attribute name is the frozen schema's, not reordered to shift_opened_id
    # (§45.1).
    shift_id_opened: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_mwr_shift_opened",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # The ten many-to-one relationships are unidirectional (§O11).
    machine: Mapped["Machine"] = relationship(lazy="select")
    machine_maintenance_schedule: Mapped[Optional["MachineMaintenanceSchedule"]] = (
        relationship(lazy="select")
    )
    triggering_alert: Mapped[Optional["OperationalAlert"]] = relationship(
        lazy="select"
    )
    triggering_recommendation: Mapped[Optional["AiRecommendation"]] = relationship(
        lazy="select"
    )
    # Two keys to FailureCategory in two roles: reported and confirmed.
    reported_failure_category: Mapped[Optional["FailureCategory"]] = relationship(
        lazy="select", foreign_keys=[reported_failure_category_id]
    )
    confirmed_failure_category: Mapped[Optional["FailureCategory"]] = relationship(
        lazy="select", foreign_keys=[confirmed_failure_category_id]
    )
    priority_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        lazy="select"
    )
    assigned_maintenance_team: Mapped[Optional["MaintenanceTeam"]] = relationship(
        lazy="select"
    )
    assigned_engineer: Mapped[Optional["MaintenanceEngineer"]] = relationship(
        lazy="select"
    )
    shift_opened: Mapped["Shift"] = relationship(lazy="select")
    activities: Mapped[list["MachineMaintenanceActivity"]] = relationship(
        back_populates="maintenance_work_record", lazy="select"
    )
    inventory_movements: Mapped[list["InventoryMovement"]] = relationship(
        back_populates="maintenance_work_record", lazy="select"
    )

    @validates("maintenance_work_record_code", "machine_id", "work_type",
               "machine", "opened_at", "assigned_at", "started_at",
               "completed_at", "closed_at")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Partial immutability plus timezone-awareness (§41.3, §41.4).

        Status, assignment, lifecycle timestamps, durations and resolution are
        mutable; the code, machine, work type and ``opened_at`` are not. Legal
        status transitions are the writing component's state machine.
        """
        if key in ("maintenance_work_record_code", "machine_id", "work_type",
                   "machine", "opened_at") and inspect(self).persistent:
            raise ValueError(
                "maintenance_work_record.%s is immutable after insert" % key
            )
        if key in ("opened_at", "assigned_at", "started_at", "completed_at",
                   "closed_at"):
            return require_timezone_aware(key, value)
        if key == "maintenance_work_record_code":
            return value.strip().upper()
        return value


class MachineMaintenanceActivity(TimestampCreatedMixin,
                                 ComponentProvenanceMixin, Base):
    """Table ``machine_maintenance_activity``, operational group: the append-only
    timeline of steps within a maintenance job.

    A work record says a job took 255 minutes; this says where those minutes went.
    Two four-hour jobs are entirely different problems if one spent three hours
    waiting for an engineer and the other three hours on the repair. Master data
    states the commitments -- team response time, location retrieval time, failure
    mode repair estimate -- and this table is where they are held to account (§O12).
    """

    __tablename__ = "machine_maintenance_activity"
    __table_args__ = (
        # Activities within a job are strictly time-ordered, and two identical
        # steps at the same instant indicate a defect. Also makes insertion
        # idempotent.
        UniqueConstraint("maintenance_work_record_id", "activity_at",
                         "activity_type",
                         name="uq_mma_work_record_activity"),
        CheckConstraint(
            "duration_from_previous_seconds IS NULL "
            "OR duration_from_previous_seconds >= 0",
            name=conv("ck_mma_duration_non_negative")),
        CheckConstraint(
            "activity_type IN ('dispatched', 'arrived', 'diagnosis_started', "
            "'diagnosis_complete', 'part_requested', 'part_collected', "
            "'repair_started', 'repair_complete', 'test_run', 'handover', "
            "'escalated', 'on_hold', 'resumed')",
            name=conv("ck_mma_activity_type_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_mma_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    machine_maintenance_activity_id: Mapped[OperationalPk]
    # ON DELETE CASCADE was considered here and rejected: a cascade could silently
    # remove activity history for a job whose duration is under dispute or whose
    # intervals feed a service-level report (§32.2 of the schema document).
    maintenance_work_record_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_work_record.maintenance_work_record_id",
                   name="fk_mma_work_record",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    activity_at: Mapped[TimestampTz]
    # This vocabulary is what makes interval measurement possible.
    activity_type: Mapped[MaintenanceActivityType] = mapped_column(
        Enum(MaintenanceActivityType, name="maintenance_activity_type",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    performed_by_worker_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_mma_performed_by",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # NULL on the first activity. Makes interval analysis a read rather than a
    # window function.
    duration_from_previous_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    # A job spanning a shift change shows the handover here.
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_mma_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    maintenance_work_record: Mapped["MaintenanceWorkRecord"] = relationship(
        back_populates="activities", lazy="select"
    )
    performed_by: Mapped[Optional["Worker"]] = relationship(lazy="select")
    shift: Mapped["Shift"] = relationship(lazy="select")

    @validates("maintenance_work_record_id", "activity_at", "activity_type",
               "performed_by_worker_id", "duration_from_previous_seconds",
               "notes", "shift_id", "maintenance_work_record", "performed_by",
               "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``activity_at`` must be timezone-aware (§41.3, §41.4)."""
        if inspect(self).persistent:
            raise ValueError(
                "machine_maintenance_activity is append-only; %s cannot be "
                "reassigned once the row is persistent" % key
            )
        if key == "activity_at":
            return require_timezone_aware(key, value)
        return value
