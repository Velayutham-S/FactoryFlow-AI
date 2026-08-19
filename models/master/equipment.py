"""Master models M8-M10: the three-level equipment classification and the assets."""

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.master import (
    CriticalityLevel,
    EquipmentClass,
    MachineLifecycleStatus,
    MaintenanceSpecialization,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk, Rate, Seconds2

if TYPE_CHECKING:
    from models.master.maintenance import MachineMaintenanceSchedule
    from models.master.failure import MachineTypeFailureMode
    from models.master.parameters import MachineTypeParameter
    from models.master.production import ProductionLine
    from models.master.thresholds import AlertThresholdProfile
    from models.operational.telemetry import MachineOperationalStatus


class MachineCategory(SoftDeleteMixin, TimestampCreatedMixin,
                      TimestampUpdatedMixin, Base):
    """Table ``machine_category``, master group: the broadest equipment
    classification, carrying the maintenance discipline a failure routes to."""

    __tablename__ = "machine_category"
    __table_args__ = (
        UniqueConstraint("machine_category_code", name="uq_machine_category_code"),
        CheckConstraint("machine_category_code GLOB 'MCAT-[A-Z][A-Z][A-Z]'",
                        name=conv("ck_machine_category_code_format")),
        CheckConstraint(
            "typical_service_life_years IS NULL "
            "OR typical_service_life_years BETWEEN 1 AND 50",
            name=conv("ck_machine_category_service_life_range")),
        CheckConstraint("length(trim(category_name)) > 0",
                        name=conv("ck_machine_category_name_not_blank")),
        CheckConstraint(
            "equipment_class IN ('rotating', 'robotic', 'conveying', 'static', "
            "'metrology')",
            name=conv("ck_machine_category_equipment_class_allowed")),
        CheckConstraint(
            "primary_maintenance_specialization IN ('mechanical', 'electrical', "
            "'automation', 'general')",
            name=conv(
                "ck_machine_category_primary_maintenance_specialization_allowed")),
        CheckConstraint("length(machine_category_code) <= 12",
                        name=conv("ck_machine_category_machine_category_code_length")),
        CheckConstraint("length(category_name) <= 80",
                        name=conv("ck_machine_category_category_name_length")),
        CheckConstraint("is_rotating_equipment IN (0, 1)",
                        name=conv("ck_machine_category_is_rotating_equipment_bool")),
        CheckConstraint(
            "requires_condition_monitoring IN (0, 1)",
            name=conv("ck_machine_category_requires_condition_monitoring_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_machine_category_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    machine_category_id: Mapped[MasterPk]
    machine_category_code: Mapped[str] = mapped_column(String(12))
    category_name: Mapped[str] = mapped_column(String(80))
    description: Mapped[Optional[str]] = mapped_column(Text)
    equipment_class: Mapped[EquipmentClass] = mapped_column(
        Enum(EquipmentClass, name="equipment_class", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # The most consequential shared vocabulary in the master group: 5 columns
    # across 4 tables, and team matching is a direct value comparison between
    # them. One Python class holds the vocabulary once; the database holds five
    # identical check constraints (§40.2).
    primary_maintenance_specialization: Mapped[MaintenanceSpecialization] = (
        mapped_column(
            Enum(MaintenanceSpecialization, name="maintenance_specialization",
                 native_enum=False, create_constraint=False,
                 values_callable=lambda enum_cls: [m.value for m in enum_cls])
        )
    )
    is_rotating_equipment: Mapped[bool] = mapped_column(server_default=text("0"))
    requires_condition_monitoring: Mapped[bool] = mapped_column(
        server_default=text("1")
    )
    typical_service_life_years: Mapped[Optional[int]] = mapped_column(Integer)

    machine_types: Mapped[list["MachineType"]] = relationship(
        back_populates="machine_category", lazy="select"
    )

    @validates("machine_category_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class MachineType(SoftDeleteMixin, TimestampCreatedMixin,
                  TimestampUpdatedMixin, Base):
    """Table ``machine_type``, master group: a specific make and model, carrying the
    reliability figures every prediction horizon and repair estimate derives from."""

    __tablename__ = "machine_type"
    __table_args__ = (
        UniqueConstraint("machine_type_code", name="uq_machine_type_code"),
        CheckConstraint(
            "machine_type_code GLOB 'MTY-[A-Z0-9-][A-Z0-9-][A-Z0-9-]*' "
            "AND length(machine_type_code) BETWEEN 7 AND 24 "
            "AND substr(machine_type_code, 5) NOT GLOB '*[^A-Z0-9-]*'",
            name=conv("ck_machine_type_code_format")),
        CheckConstraint("rated_power_kw > 0",
                        name=conv("ck_machine_type_rated_power_positive")),
        CheckConstraint("design_life_hours > 0",
                        name=conv("ck_machine_type_design_life_positive")),
        CheckConstraint("mtbf_hours > 0",
                        name=conv("ck_machine_type_mtbf_positive")),
        CheckConstraint("mtbf_hours < design_life_hours",
                        name=conv("ck_machine_type_mtbf_below_design_life")),
        CheckConstraint("mttr_minutes > 0",
                        name=conv("ck_machine_type_mttr_positive")),
        CheckConstraint("min_operators_required BETWEEN 0 AND 5",
                        name=conv("ck_machine_type_operators_range")),
        CheckConstraint("length(trim(type_name)) > 0",
                        name=conv("ck_machine_type_name_not_blank")),
        CheckConstraint("length(machine_type_code) <= 24",
                        name=conv("ck_machine_type_machine_type_code_length")),
        CheckConstraint("length(type_name) <= 120",
                        name=conv("ck_machine_type_type_name_length")),
        CheckConstraint("length(manufacturer) <= 100",
                        name=conv("ck_machine_type_manufacturer_length")),
        CheckConstraint("length(model_number) <= 60",
                        name=conv("ck_machine_type_model_number_length")),
        CheckConstraint(
            "control_system IS NULL OR length(control_system) <= 80",
            name=conv("ck_machine_type_control_system_length")),
        CheckConstraint("requires_tooling IN (0, 1)",
                        name=conv("ck_machine_type_requires_tooling_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_machine_type_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    machine_type_id: Mapped[MasterPk]
    machine_type_code: Mapped[str] = mapped_column(String(24))
    machine_category_id: Mapped[int] = mapped_column(
        ForeignKey("machine_category.machine_category_id",
                   name="fk_machine_type_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    type_name: Mapped[str] = mapped_column(String(120))
    manufacturer: Mapped[str] = mapped_column(String(100))
    model_number: Mapped[str] = mapped_column(String(60))
    # Resolves through Seconds2 because both are NUMERIC(8,2). A PowerKw alias
    # with identical DDL would be a synonym, which §45 rejects; the attribute
    # name carries the meaning (§38.4).
    rated_power_kw: Mapped[Seconds2]
    design_life_hours: Mapped[int] = mapped_column(Integer)
    mtbf_hours: Mapped[int] = mapped_column(Integer)
    mttr_minutes: Mapped[int] = mapped_column(Integer)
    requires_tooling: Mapped[bool] = mapped_column(server_default=text("0"))
    control_system: Mapped[Optional[str]] = mapped_column(String(80))
    min_operators_required: Mapped[int] = mapped_column(Integer)

    machine_category: Mapped["MachineCategory"] = relationship(
        back_populates="machine_types", lazy="select"
    )
    machines: Mapped[list["Machine"]] = relationship(
        back_populates="machine_type", lazy="select"
    )
    type_parameters: Mapped[list["MachineTypeParameter"]] = relationship(
        back_populates="machine_type", lazy="select"
    )
    failure_modes: Mapped[list["MachineTypeFailureMode"]] = relationship(
        back_populates="machine_type", lazy="select"
    )
    alert_threshold_profiles: Mapped[list["AlertThresholdProfile"]] = relationship(
        back_populates="machine_type", lazy="select"
    )

    @validates("machine_type_code", "model_number")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()


class Machine(TimestampCreatedMixin, TimestampUpdatedMixin, Base):
    """Table ``machine``, master group: the physical asset every reading, event,
    prediction and recommendation is anchored to.

    The one master model that does not compose SoftDeleteMixin: it carries
    ``lifecycle_status`` instead of ``is_active``, because in-service, standby,
    under overhaul and decommissioned are states a two-valued flag cannot
    express and the Monitoring Agent needs (§27, §34).
    """

    __tablename__ = "machine"
    __table_args__ = (
        UniqueConstraint("machine_code", name="uq_machine_code"),
        UniqueConstraint("serial_number", name="uq_machine_serial_number"),
        # Unique when present: SQLite treats each NULL as distinct, so untagged
        # machines do not conflict.
        UniqueConstraint("asset_tag", name="uq_machine_asset_tag"),
        UniqueConstraint("production_line_id", "line_position",
                         name="uq_machine_line_position"),
        CheckConstraint("machine_code GLOB 'MC-[0-9][0-9][0-9][0-9]'",
                        name=conv("ck_machine_code_format")),
        CheckConstraint("line_position > 0",
                        name=conv("ck_machine_line_position_positive")),
        CheckConstraint(
            "downstream_buffer_units IS NULL OR downstream_buffer_units >= 0",
            name=conv("ck_machine_buffer_non_negative")),
        CheckConstraint(
            "rated_capacity_units_per_hour IS NULL "
            "OR rated_capacity_units_per_hour > 0",
            name=conv("ck_machine_rated_capacity_positive")),
        CheckConstraint("commissioned_date >= installation_date",
                        name=conv("ck_machine_commissioned_after_installation")),
        CheckConstraint(
            "warranty_expiry_date IS NULL "
            "OR warranty_expiry_date >= commissioned_date",
            name=conv("ck_machine_warranty_after_commissioned")),
        CheckConstraint(
            "is_monitored = 0 OR alert_threshold_profile_id IS NOT NULL",
            name=conv("ck_machine_monitored_requires_profile")),
        CheckConstraint("length(trim(machine_name)) > 0",
                        name=conv("ck_machine_name_not_blank")),
        CheckConstraint(
            "criticality IN ('critical', 'high', 'standard', 'low')",
            name=conv("ck_machine_criticality_allowed")),
        CheckConstraint(
            "lifecycle_status IN ('in_service', 'standby', 'under_overhaul', "
            "'decommissioned')",
            name=conv("ck_machine_lifecycle_status_allowed")),
        CheckConstraint("length(machine_code) <= 12",
                        name=conv("ck_machine_machine_code_length")),
        CheckConstraint("length(machine_name) <= 120",
                        name=conv("ck_machine_machine_name_length")),
        CheckConstraint("length(serial_number) <= 60",
                        name=conv("ck_machine_serial_number_length")),
        CheckConstraint("asset_tag IS NULL OR length(asset_tag) <= 30",
                        name=conv("ck_machine_asset_tag_length")),
        CheckConstraint("is_bottleneck IN (0, 1)",
                        name=conv("ck_machine_is_bottleneck_bool")),
        CheckConstraint("is_monitored IN (0, 1)",
                        name=conv("ck_machine_is_monitored_bool")),
        # At most one bottleneck per line: a line has one constraint by
        # definition, and two would make impact arithmetic contradictory. No
        # table-level unique constraint can express "at most one TRUE per
        # group" (§37.9).
        Index(
            "uq_machine_bottleneck_per_line",
            "production_line_id",
            unique=True,
            sqlite_where=text("is_bottleneck = 1"),
        ),
        {"sqlite_autoincrement": True},
    )

    machine_id: Mapped[MasterPk]
    machine_code: Mapped[str] = mapped_column(String(12))
    machine_type_id: Mapped[int] = mapped_column(
        ForeignKey("machine_type.machine_type_id", name="fk_machine_type",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[int] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_machine_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    line_position: Mapped[int] = mapped_column(Integer)
    # NULL means fall back to the machine type's default profile -- one of the
    # nullability patterns a caller must know about (§38.5).
    alert_threshold_profile_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("alert_threshold_profile.alert_threshold_profile_id",
                   name="fk_machine_alert_threshold_profile",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_name: Mapped[str] = mapped_column(String(120))
    serial_number: Mapped[str] = mapped_column(String(60))
    asset_tag: Mapped[Optional[str]] = mapped_column(String(30))
    installation_date: Mapped[date] = mapped_column(Date)
    commissioned_date: Mapped[date] = mapped_column(Date)
    warranty_expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    criticality: Mapped[CriticalityLevel] = mapped_column(
        Enum(CriticalityLevel, name="criticality_level", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    is_bottleneck: Mapped[bool] = mapped_column(server_default=text("0"))
    downstream_buffer_units: Mapped[Optional[int]] = mapped_column(Integer)
    rated_capacity_units_per_hour: Mapped[Optional[Rate]]
    # Replaces is_active. Retirement is 'decommissioned', never a delete; the 13
    # inbound RESTRICT foreign keys make a delete fail regardless, which is the
    # stronger guarantee (§30).
    lifecycle_status: Mapped[MachineLifecycleStatus] = mapped_column(
        Enum(MachineLifecycleStatus, name="machine_lifecycle_status",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        server_default=text("'in_service'"),
    )
    is_monitored: Mapped[bool] = mapped_column(server_default=text("1"))
    installed_position_notes: Mapped[Optional[str]] = mapped_column(Text)

    machine_type: Mapped["MachineType"] = relationship(
        back_populates="machines", lazy="select"
    )
    production_line: Mapped["ProductionLine"] = relationship(
        back_populates="machines", lazy="select"
    )
    alert_threshold_profile: Mapped[Optional["AlertThresholdProfile"]] = relationship(
        back_populates="machines", lazy="select"
    )
    maintenance_schedules: Mapped[list["MachineMaintenanceSchedule"]] = relationship(
        back_populates="machine", lazy="select"
    )
    operational_status: Mapped[Optional["MachineOperationalStatus"]] = relationship(
        back_populates="machine", lazy="select", uselist=False
    )

    # Twelve operational reverse collections are deliberately unmapped, led by
    # machine_sensor_reading at ~4 million rows per machine per year. Rule L1
    # (§19.1).

    @validates("machine_code", "serial_number")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()

    @validates("asset_tag")
    def _normalise_asset_tag(self, key: str, value: Optional[str]) -> Optional[str]:
        return value if value is None else value.strip().upper()
