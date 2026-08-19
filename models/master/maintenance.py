"""Master model M26: the preventive maintenance plan.

The table deliberately carries no ``next_due_date``. Due status is computed from
operational history instead -- ``machine_operational_status``'s accumulated
counters and closed ``maintenance_work_record`` rows -- so there is no cached date
to drift out of agreement with what actually happened (§O2, §O11).
"""

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.master import IntervalBasis, MaintenanceType
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.inventory import InventoryItem
    from models.master.people import MaintenanceTeam


class MachineMaintenanceSchedule(SoftDeleteMixin, TimestampCreatedMixin,
                                 TimestampUpdatedMixin, Base):
    """Table ``machine_maintenance_schedule``, master group: the recurring maintenance
    obligation per machine, expressed as an interval rather than a due date."""

    __tablename__ = "machine_maintenance_schedule"
    __table_args__ = (
        UniqueConstraint("machine_maintenance_schedule_code",
                         name="uq_machine_maintenance_schedule_code"),
        CheckConstraint(
            "machine_maintenance_schedule_code GLOB 'SCH-[0-9][0-9][0-9][0-9]'",
            name=conv("ck_mms_code_format")),
        CheckConstraint("interval_value > 0",
                        name=conv("ck_mms_interval_value_positive")),
        CheckConstraint("estimated_duration_minutes > 0",
                        name=conv("ck_mms_duration_positive")),
        CheckConstraint("can_be_deferred = 1 OR max_deferral_days IS NULL",
                        name=conv("ck_mms_deferral_consistency")),
        CheckConstraint(
            "max_deferral_days IS NULL OR max_deferral_days > 0",
            name=conv("ck_mms_max_deferral_positive")),
        CheckConstraint(
            "maintenance_type IN ('preventive', 'predictive', 'calibration', "
            "'inspection', 'lubrication')",
            name=conv("ck_mms_maintenance_type_allowed")),
        CheckConstraint(
            "interval_basis IN ('calendar_days', 'operating_hours', "
            "'cycle_count')",
            name=conv("ck_mms_interval_basis_allowed")),
        CheckConstraint(
            "length(machine_maintenance_schedule_code) <= 12",
            name=conv("ck_mms_machine_maintenance_schedule_code_length")),
        CheckConstraint("requires_line_stop IN (0, 1)",
                        name=conv("ck_mms_requires_line_stop_bool")),
        CheckConstraint("can_be_deferred IN (0, 1)",
                        name=conv("ck_mms_can_be_deferred_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_mms_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    machine_maintenance_schedule_id: Mapped[MasterPk]
    machine_maintenance_schedule_code: Mapped[str] = mapped_column(String(12))
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_mms_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    maintenance_type: Mapped[MaintenanceType] = mapped_column(
        Enum(MaintenanceType, name="maintenance_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Selects which operational counter the due calculation reads:
    # calendar_days against baseline_start_date and closed work records,
    # operating_hours and cycle_count against machine_operational_status's
    # accumulated totals minus the value at last maintenance (§O2).
    interval_basis: Mapped[IntervalBasis] = mapped_column(
        Enum(IntervalBasis, name="interval_basis", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    interval_value: Mapped[int] = mapped_column(Integer)
    estimated_duration_minutes: Mapped[int] = mapped_column(Integer)
    requires_line_stop: Mapped[bool] = mapped_column(server_default=text("0"))
    assigned_maintenance_team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_team.maintenance_team_id", name="fk_mms_team",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    required_inventory_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_mms_inventory_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    baseline_start_date: Mapped[date] = mapped_column(Date)
    can_be_deferred: Mapped[bool] = mapped_column(server_default=text("1"))
    max_deferral_days: Mapped[Optional[int]] = mapped_column(Integer)
    task_summary: Mapped[Optional[str]] = mapped_column(Text)

    machine: Mapped["Machine"] = relationship(
        back_populates="maintenance_schedules", lazy="select"
    )
    assigned_maintenance_team: Mapped[Optional["MaintenanceTeam"]] = relationship(
        back_populates="maintenance_schedules", lazy="select"
    )
    required_inventory_item: Mapped[Optional["InventoryItem"]] = relationship(
        back_populates="required_by_maintenance_schedules", lazy="select"
    )

    # A closed maintenance_work_record referencing this schedule is what
    # establishes when it was last satisfied. Due-date computation from the
    # interval basis and the live counters belongs to the Simulator (§41.3).

    @validates("machine_maintenance_schedule_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()
