"""Master models M1-M4: the site and its calendar and organisational structure."""

from datetime import date, time
from typing import TYPE_CHECKING, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.master import (
    AccessRestriction,
    AreaType,
    DepartmentFunction,
    ShiftType,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk, Percent, Rate

if TYPE_CHECKING:
    from models.master.inventory import InventoryLocation
    from models.master.people import MaintenanceTeam, Worker
    from models.master.production import ProductionLine


class Plant(SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin, Base):
    """Table ``plant``, master group: the single manufacturing site and the anchor
    for site-wide timezone and currency."""

    __tablename__ = "plant"
    __table_args__ = (
        UniqueConstraint("plant_code", name="uq_plant_code"),
        CheckConstraint("plant_code GLOB 'PLT-[0-9][0-9]'",
                        name=conv("ck_plant_code_format")),
        CheckConstraint("country_code GLOB '[A-Z][A-Z]'",
                        name=conv("ck_plant_country_code_format")),
        CheckConstraint("currency_code GLOB '[A-Z][A-Z][A-Z]'",
                        name=conv("ck_plant_currency_code_format")),
        CheckConstraint("operating_days_per_week BETWEEN 1 AND 7",
                        name=conv("ck_plant_operating_days_range")),
        CheckConstraint("shifts_per_day BETWEEN 1 AND 4",
                        name=conv("ck_plant_shifts_per_day_range")),
        CheckConstraint(
            "annual_production_capacity_units IS NULL "
            "OR annual_production_capacity_units > 0",
            name=conv("ck_plant_capacity_positive")),
        CheckConstraint("length(trim(plant_name)) > 0",
                        name=conv("ck_plant_name_not_blank")),
        CheckConstraint("length(plant_code) <= 10",
                        name=conv("ck_plant_plant_code_length")),
        CheckConstraint("length(plant_name) <= 120",
                        name=conv("ck_plant_plant_name_length")),
        CheckConstraint("length(address_line) <= 200",
                        name=conv("ck_plant_address_line_length")),
        CheckConstraint("length(city) <= 80",
                        name=conv("ck_plant_city_length")),
        CheckConstraint("length(state_region) <= 80",
                        name=conv("ck_plant_state_region_length")),
        CheckConstraint("length(country_code) = 2",
                        name=conv("ck_plant_country_code_length")),
        CheckConstraint("length(timezone) <= 50",
                        name=conv("ck_plant_timezone_length")),
        CheckConstraint("length(currency_code) = 3",
                        name=conv("ck_plant_currency_code_length")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_plant_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    plant_id: Mapped[MasterPk]
    plant_code: Mapped[str] = mapped_column(String(10))
    plant_name: Mapped[str] = mapped_column(String(120))
    address_line: Mapped[str] = mapped_column(String(200))
    city: Mapped[str] = mapped_column(String(80))
    state_region: Mapped[str] = mapped_column(String(80))
    country_code: Mapped[str] = mapped_column(CHAR(2))
    # Not check-constrained against a catalogue: SQLite carries none, and a CHECK
    # expression must be deterministic. ORM validation below is mandatory (§41.3).
    timezone: Mapped[str] = mapped_column(String(50))
    currency_code: Mapped[str] = mapped_column(CHAR(3))
    operating_days_per_week: Mapped[int] = mapped_column(Integer)
    shifts_per_day: Mapped[int] = mapped_column(Integer)
    commissioned_date: Mapped[date] = mapped_column(Date)
    annual_production_capacity_units: Mapped[Optional[int]] = mapped_column(Integer)

    plant_areas: Mapped[list["PlantArea"]] = relationship(
        back_populates="plant", lazy="select"
    )
    departments: Mapped[list["Department"]] = relationship(
        back_populates="plant", lazy="select"
    )
    shifts: Mapped[list["Shift"]] = relationship(
        back_populates="plant", lazy="select"
    )

    @validates("plant_code", "country_code", "currency_code")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()

    @validates("timezone")
    def _validate_timezone(self, key: str, value: str) -> str:
        """The single highest-value validation hook in the ORM layer (§41.3).

        It guards one column on a one-row table, and a wrong value there is wrong
        everywhere: an invalid IANA name silently corrupts every shift-window
        calculation in the platform.
        """
        candidate = value.strip()
        try:
            ZoneInfo(candidate)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(
                "timezone must be a valid IANA time zone name; %r is not (%s)"
                % (candidate, exc)
            ) from exc
        return candidate


class PlantArea(SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin, Base):
    """Table ``plant_area``, master group: a physical zone within the plant, and the
    thermal and access context a machine inherits from its location."""

    __tablename__ = "plant_area"
    __table_args__ = (
        UniqueConstraint("plant_area_code", name="uq_plant_area_code"),
        CheckConstraint("plant_area_code GLOB 'AREA-[A-Z][A-Z][A-Z]'",
                        name=conv("ck_plant_area_code_format")),
        CheckConstraint("floor_level IS NULL OR floor_level BETWEEN -2 AND 10",
                        name=conv("ck_plant_area_floor_level_range")),
        CheckConstraint("floor_space_sqm IS NULL OR floor_space_sqm > 0",
                        name=conv("ck_plant_area_floor_space_positive")),
        CheckConstraint(
            "nominal_ambient_temp_c IS NULL "
            "OR nominal_ambient_temp_c BETWEEN -20 AND 60",
            name=conv("ck_plant_area_ambient_range")),
        CheckConstraint("length(trim(area_name)) > 0",
                        name=conv("ck_plant_area_name_not_blank")),
        CheckConstraint(
            "area_type IN ('production', 'assembly', 'warehouse', "
            "'spare_parts_store', 'maintenance_workshop', 'quality_lab', "
            "'dispatch', 'utility')",
            name=conv("ck_plant_area_area_type_allowed")),
        CheckConstraint(
            "access_restriction IN ('general', 'authorized_only', 'restricted')",
            name=conv("ck_plant_area_access_restriction_allowed")),
        CheckConstraint("length(plant_area_code) <= 12",
                        name=conv("ck_plant_area_plant_area_code_length")),
        CheckConstraint("length(area_name) <= 100",
                        name=conv("ck_plant_area_area_name_length")),
        CheckConstraint("is_climate_controlled IN (0, 1)",
                        name=conv("ck_plant_area_is_climate_controlled_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_plant_area_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    plant_area_id: Mapped[MasterPk]
    plant_area_code: Mapped[str] = mapped_column(String(12))
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plant.plant_id", name="fk_plant_area_plant",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    area_name: Mapped[str] = mapped_column(String(100))
    area_type: Mapped[AreaType] = mapped_column(
        Enum(AreaType, name="area_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    floor_level: Mapped[Optional[int]] = mapped_column(Integer)
    floor_space_sqm: Mapped[Optional[Rate]]
    nominal_ambient_temp_c: Mapped[Optional[Percent]]
    is_climate_controlled: Mapped[bool] = mapped_column(server_default=text("0"))
    access_restriction: Mapped[AccessRestriction] = mapped_column(
        Enum(AccessRestriction, name="access_restriction", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        server_default=text("'general'"),
    )

    plant: Mapped["Plant"] = relationship(
        back_populates="plant_areas", lazy="select"
    )
    production_lines: Mapped[list["ProductionLine"]] = relationship(
        back_populates="plant_area", lazy="select"
    )
    inventory_locations: Mapped[list["InventoryLocation"]] = relationship(
        back_populates="plant_area", lazy="select"
    )
    based_maintenance_teams: Mapped[list["MaintenanceTeam"]] = relationship(
        back_populates="base_plant_area", lazy="select"
    )

    @validates("plant_area_code")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()


class Department(SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin, Base):
    """Table ``department``, master group: the organisational owner of lines, workers
    and maintenance teams, and the escalation address of last resort."""

    __tablename__ = "department"
    __table_args__ = (
        UniqueConstraint("department_code", name="uq_department_code"),
        UniqueConstraint("cost_center_code",
                         name="uq_department_cost_center_code"),
        CheckConstraint("department_code GLOB 'DEP-[A-Z][A-Z][A-Z]'",
                        name=conv("ck_department_code_format")),
        CheckConstraint(
            "headcount_budget IS NULL OR headcount_budget >= 0",
            name=conv("ck_department_headcount_non_negative")),
        CheckConstraint(
            "escalation_email IS NULL OR (escalation_email GLOB '?*@?*.?*' "
            "AND escalation_email NOT GLOB '*@*@*')",
            name=conv("ck_department_escalation_email_format")),
        CheckConstraint("length(trim(department_name)) > 0",
                        name=conv("ck_department_name_not_blank")),
        CheckConstraint(
            "department_function IN ('production', 'maintenance', 'quality', "
            "'warehouse', 'planning', 'engineering')",
            name=conv("ck_department_department_function_allowed")),
        CheckConstraint("length(department_code) <= 12",
                        name=conv("ck_department_department_code_length")),
        CheckConstraint("length(department_name) <= 100",
                        name=conv("ck_department_department_name_length")),
        CheckConstraint("length(cost_center_code) <= 20",
                        name=conv("ck_department_cost_center_code_length")),
        CheckConstraint(
            "escalation_email IS NULL OR length(escalation_email) <= 150",
            name=conv("ck_department_escalation_email_length")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_department_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    department_id: Mapped[MasterPk]
    department_code: Mapped[str] = mapped_column(String(12))
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plant.plant_id", name="fk_department_plant",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    department_name: Mapped[str] = mapped_column(String(100))
    department_function: Mapped[DepartmentFunction] = mapped_column(
        Enum(DepartmentFunction, name="department_function", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    cost_center_code: Mapped[str] = mapped_column(String(20))
    escalation_email: Mapped[Optional[str]] = mapped_column(String(150))
    headcount_budget: Mapped[Optional[int]] = mapped_column(Integer)

    plant: Mapped["Plant"] = relationship(
        back_populates="departments", lazy="select"
    )
    production_lines: Mapped[list["ProductionLine"]] = relationship(
        back_populates="department", lazy="select"
    )
    workers: Mapped[list["Worker"]] = relationship(
        back_populates="department", lazy="select"
    )
    maintenance_teams: Mapped[list["MaintenanceTeam"]] = relationship(
        back_populates="department", lazy="select"
    )

    @validates("department_code", "cost_center_code")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()

    @validates("escalation_email")
    def _normalise_email(self, key: str, value: Optional[str]) -> Optional[str]:
        return value if value is None else value.strip().lower()


class Shift(SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin, Base):
    """Table ``shift``, master group: the recurring work period every operational row
    is attributed to, and the axis all shift reporting aggregates on."""

    __tablename__ = "shift"
    __table_args__ = (
        UniqueConstraint("shift_code", name="uq_shift_code"),
        CheckConstraint(
            "shift_code GLOB 'SH-[A-Z]*' "
            "AND length(shift_code) BETWEEN 4 AND 7 "
            "AND substr(shift_code, 4) NOT GLOB '*[^A-Z]*'",
            name=conv("ck_shift_code_format")),
        CheckConstraint("start_time <> end_time",
                        name=conv("ck_shift_times_differ")),
        CheckConstraint(
            "crosses_midnight = "
            "(CASE WHEN end_time <= start_time THEN 1 ELSE 0 END)",
            name=conv("ck_shift_crosses_midnight_consistent")),
        CheckConstraint("sequence_order > 0",
                        name=conv("ck_shift_sequence_order_positive")),
        CheckConstraint(
            "break_duration_minutes IS NULL "
            "OR break_duration_minutes BETWEEN 0 AND 120",
            name=conv("ck_shift_break_duration_range")),
        CheckConstraint("length(trim(shift_name)) > 0",
                        name=conv("ck_shift_name_not_blank")),
        CheckConstraint(
            "shift_type IN ('production', 'general', 'maintenance_only')",
            name=conv("ck_shift_shift_type_allowed")),
        CheckConstraint("length(shift_code) <= 8",
                        name=conv("ck_shift_shift_code_length")),
        CheckConstraint("length(shift_name) <= 60",
                        name=conv("ck_shift_shift_name_length")),
        CheckConstraint("crosses_midnight IN (0, 1)",
                        name=conv("ck_shift_crosses_midnight_bool")),
        CheckConstraint("is_production_shift IN (0, 1)",
                        name=conv("ck_shift_is_production_shift_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_shift_is_active_bool")),
        # Rotation order is unique among production shifts only; the general
        # shift is excluded from rotation ordering, which a plain unique
        # constraint could not express (§37.9).
        Index(
            "uq_shift_sequence_order_production",
            "plant_id",
            "sequence_order",
            unique=True,
            sqlite_where=text("shift_type = 'production'"),
        ),
        {"sqlite_autoincrement": True},
    )

    shift_id: Mapped[MasterPk]
    shift_code: Mapped[str] = mapped_column(String(8))
    plant_id: Mapped[int] = mapped_column(
        ForeignKey("plant.plant_id", name="fk_shift_plant",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    shift_name: Mapped[str] = mapped_column(String(60))
    start_time: Mapped[time] = mapped_column(Time)
    end_time: Mapped[time] = mapped_column(Time)
    # Explicit rather than derived, because end_time < start_time is legitimate
    # for a night shift. The ORM stores both times as given and derives nothing.
    crosses_midnight: Mapped[bool] = mapped_column(server_default=text("0"))
    shift_type: Mapped[ShiftType] = mapped_column(
        Enum(ShiftType, name="shift_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    sequence_order: Mapped[int] = mapped_column(Integer)
    is_production_shift: Mapped[bool] = mapped_column(server_default=text("1"))
    break_duration_minutes: Mapped[Optional[int]] = mapped_column(Integer)

    plant: Mapped["Plant"] = relationship(
        back_populates="shifts", lazy="select"
    )
    workers: Mapped[list["Worker"]] = relationship(
        back_populates="shift", lazy="select"
    )
    maintenance_teams: Mapped[list["MaintenanceTeam"]] = relationship(
        back_populates="shift", lazy="select"
    )

    # The 17 operational reverse collections are deliberately unmapped under
    # rule L1 (§19.1): Shift has 4 rows, and Shift.sensor_readings would be an
    # eight-million-row attribute on one of them. Queries by shift filter the
    # child model, and every operational `shift` relationship is unidirectional.

    @validates("shift_code")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()
