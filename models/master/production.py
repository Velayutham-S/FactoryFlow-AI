"""Master models M5-M7: what is produced, where it can be produced, and how fast."""

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
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.master import (
    CapabilityType,
    CriticalityLevel,
    LineType,
    QualityCriticality,
    UnitOfMeasure,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk, Money, Percent, Rate, Seconds2

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.inventory import BillOfMaterials
    from models.master.people import NotificationRecipient, Worker
    from models.master.plant import Department, PlantArea
    from models.master.thresholds import BusinessRule


class Product(SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin, Base):
    """Table ``product``, master group: what the plant makes, with the standard costs
    every business-impact figure is computed from."""

    __tablename__ = "product"
    __table_args__ = (
        UniqueConstraint("product_code", name="uq_product_code"),
        CheckConstraint(
            "product_code GLOB 'PRD-[A-Z][A-Z]*-[0-9][0-9][0-9]' "
            "AND length(product_code) BETWEEN 10 AND 12",
            name=conv("ck_product_code_format")),
        # The narrowed set: BOX is permitted on inventory_item and excluded here.
        # One vocabulary, two permitted sets (§40.3).
        CheckConstraint(
            "unit_of_measure IN ('EA', 'KG', 'L', 'M', 'SET')",
            name=conv("ck_product_unit_of_measure_allowed")),
        CheckConstraint("standard_selling_price > 0",
                        name=conv("ck_product_selling_price_positive")),
        CheckConstraint("standard_material_cost > 0",
                        name=conv("ck_product_material_cost_positive")),
        CheckConstraint("standard_material_cost < standard_selling_price",
                        name=conv("ck_product_margin_positive")),
        CheckConstraint(
            "target_scrap_rate_pct IS NULL "
            "OR target_scrap_rate_pct BETWEEN 0 AND 100",
            name=conv("ck_product_scrap_rate_range")),
        CheckConstraint("shelf_life_days IS NULL OR shelf_life_days > 0",
                        name=conv("ck_product_shelf_life_positive")),
        CheckConstraint("length(trim(product_name)) > 0",
                        name=conv("ck_product_name_not_blank")),
        CheckConstraint(
            "quality_criticality IN ('safety_critical', 'high', 'standard')",
            name=conv("ck_product_quality_criticality_allowed")),
        CheckConstraint("length(product_code) <= 20",
                        name=conv("ck_product_product_code_length")),
        CheckConstraint("length(product_name) <= 150",
                        name=conv("ck_product_product_name_length")),
        CheckConstraint("length(product_family) <= 80",
                        name=conv("ck_product_product_family_length")),
        CheckConstraint(
            "drawing_revision IS NULL OR length(drawing_revision) <= 12",
            name=conv("ck_product_drawing_revision_length")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_product_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    product_id: Mapped[MasterPk]
    product_code: Mapped[str] = mapped_column(String(20))
    product_name: Mapped[str] = mapped_column(String(150))
    product_family: Mapped[str] = mapped_column(String(80))
    unit_of_measure: Mapped[UnitOfMeasure] = mapped_column(
        Enum(UnitOfMeasure, name="unit_of_measure", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    standard_selling_price: Mapped[Money]
    # Scrap cost is computed at read time as quantity x this column; scrap_record
    # deliberately stores no captured cost (§O9).
    standard_material_cost: Mapped[Money]
    quality_criticality: Mapped[QualityCriticality] = mapped_column(
        Enum(QualityCriticality, name="quality_criticality", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    target_scrap_rate_pct: Mapped[Optional[Percent]]
    shelf_life_days: Mapped[Optional[int]] = mapped_column(Integer)
    drawing_revision: Mapped[Optional[str]] = mapped_column(String(12))
    introduced_date: Mapped[date] = mapped_column(Date)

    line_capabilities: Mapped[list["ProductLineCapability"]] = relationship(
        back_populates="product", lazy="select"
    )
    bom_lines: Mapped[list["BillOfMaterials"]] = relationship(
        back_populates="product", lazy="select"
    )

    # production_run is deliberately unmapped: ~2,000 rows a year against 3
    # product rows, rule L1 (§19.1).

    @validates("product_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()

    @validates("drawing_revision")
    def _normalise_revision(self, key: str, value: Optional[str]) -> Optional[str]:
        return value if value is None else value.strip().upper()


class ProductionLine(SoftDeleteMixin, TimestampCreatedMixin,
                     TimestampUpdatedMixin, Base):
    """Table ``production_line``, master group: a sequence of machines producing one
    product at a time, and the scope at which impact and reroute are reasoned."""

    __tablename__ = "production_line"
    __table_args__ = (
        UniqueConstraint("production_line_code", name="uq_production_line_code"),
        CheckConstraint("production_line_code GLOB 'LN-[0-9][0-9]'",
                        name=conv("ck_production_line_code_format")),
        CheckConstraint("design_capacity_units_per_hour > 0",
                        name=conv("ck_production_line_capacity_positive")),
        CheckConstraint("station_count > 0",
                        name=conv("ck_production_line_station_count_positive")),
        CheckConstraint(
            "target_oee_percent IS NULL OR target_oee_percent BETWEEN 0 AND 100",
            name=conv("ck_production_line_target_oee_range")),
        CheckConstraint(
            "changeover_time_minutes IS NULL OR changeover_time_minutes >= 0",
            name=conv("ck_production_line_changeover_non_negative")),
        CheckConstraint("length(trim(line_name)) > 0",
                        name=conv("ck_production_line_name_not_blank")),
        CheckConstraint(
            "line_type IN ('machining', 'assembly', 'packaging', 'finishing', "
            "'inspection')",
            name=conv("ck_production_line_line_type_allowed")),
        CheckConstraint(
            "criticality IN ('critical', 'high', 'standard', 'low')",
            name=conv("ck_production_line_criticality_allowed")),
        CheckConstraint("length(production_line_code) <= 10",
                        name=conv("ck_production_line_production_line_code_length")),
        CheckConstraint("length(line_name) <= 120",
                        name=conv("ck_production_line_line_name_length")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_production_line_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    production_line_id: Mapped[MasterPk]
    production_line_code: Mapped[str] = mapped_column(String(10))
    plant_area_id: Mapped[int] = mapped_column(
        ForeignKey("plant_area.plant_area_id",
                   name="fk_production_line_plant_area",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("department.department_id",
                   name="fk_production_line_department",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    line_name: Mapped[str] = mapped_column(String(120))
    line_type: Mapped[LineType] = mapped_column(
        Enum(LineType, name="line_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Shared vocabulary, also on Machine, so prioritisation compares line and
    # machine criticality as a direct value comparison (§40.2).
    criticality: Mapped[CriticalityLevel] = mapped_column(
        Enum(CriticalityLevel, name="criticality_level", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    design_capacity_units_per_hour: Mapped[Rate]
    station_count: Mapped[int] = mapped_column(Integer)
    target_oee_percent: Mapped[Optional[Percent]]
    changeover_time_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    commissioned_date: Mapped[date] = mapped_column(Date)

    plant_area: Mapped["PlantArea"] = relationship(
        back_populates="production_lines", lazy="select"
    )
    department: Mapped["Department"] = relationship(
        back_populates="production_lines", lazy="select"
    )
    machines: Mapped[list["Machine"]] = relationship(
        back_populates="production_line", lazy="select"
    )
    line_capabilities: Mapped[list["ProductLineCapability"]] = relationship(
        back_populates="production_line", lazy="select"
    )
    workers: Mapped[list["Worker"]] = relationship(
        back_populates="production_line", lazy="select"
    )
    business_rules: Mapped[list["BusinessRule"]] = relationship(
        back_populates="production_line", lazy="select"
    )
    notification_recipients: Mapped[list["NotificationRecipient"]] = relationship(
        back_populates="scope_production_line", lazy="select"
    )

    # Six operational reverse collections are deliberately unmapped -- run,
    # event, alert, supervisor context, recommendation, dashboard snapshot. All
    # grow without bound relative to 4 line rows (§19.1).

    @validates("production_line_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class ProductLineCapability(SoftDeleteMixin, TimestampCreatedMixin,
                            TimestampUpdatedMixin, Base):
    """Table ``product_line_capability``, master group: which products a line can make
    and at what rate, and the sole home of the authoritative cycle time."""

    __tablename__ = "product_line_capability"
    __table_args__ = (
        UniqueConstraint("product_id", "production_line_id",
                         name="uq_product_line_capability_pair"),
        CheckConstraint("cycle_time_seconds > 0",
                        name=conv("ck_plc_cycle_time_positive")),
        CheckConstraint("max_hourly_output_units > 0",
                        name=conv("ck_plc_max_output_positive")),
        CheckConstraint("changeover_minutes >= 0",
                        name=conv("ck_plc_changeover_non_negative")),
        CheckConstraint(
            "capability_type <> 'finishing_stage' OR is_primary_line = 0",
            name=conv("ck_plc_finishing_stage_not_primary")),
        CheckConstraint(
            "qualification_expiry_date IS NULL OR is_qualified = 1",
            name=conv("ck_plc_qualification_expiry_requires_qualified")),
        CheckConstraint(
            "capability_type IN ('production_route', 'finishing_stage')",
            name=conv("ck_plc_capability_type_allowed")),
        CheckConstraint("is_primary_line IN (0, 1)",
                        name=conv("ck_plc_is_primary_line_bool")),
        CheckConstraint("is_qualified IN (0, 1)",
                        name=conv("ck_plc_is_qualified_bool")),
        CheckConstraint("tooling_available IN (0, 1)",
                        name=conv("ck_plc_tooling_available_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_plc_is_active_bool")),
        # Exactly one primary production route per product: a conditional
        # uniqueness rule across a subset of rows grouped by product, which no
        # table-level unique constraint can express (§37.9).
        Index(
            "uq_plc_primary_route_per_product",
            "product_id",
            unique=True,
            sqlite_where=text(
                "capability_type = 'production_route' "
                "AND is_primary_line = 1 AND is_active = 1"
            ),
        ),
        {"sqlite_autoincrement": True},
    )

    product_line_capability_id: Mapped[MasterPk]
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product.product_id", name="fk_plc_product",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[int] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_plc_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    capability_type: Mapped[CapabilityType] = mapped_column(
        Enum(CapabilityType, name="capability_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    is_primary_line: Mapped[bool] = mapped_column(server_default=text("0"))
    # The authoritative rate figure, and the standard cycle_history measures
    # deviation against. Superseded capabilities are soft-retired via is_active
    # and never edited, which is what lets deviation be computed against a
    # pinned capability without copying the standard (§M7).
    cycle_time_seconds: Mapped[Seconds2]
    max_hourly_output_units: Mapped[Rate]
    changeover_minutes: Mapped[int] = mapped_column(Integer)
    is_qualified: Mapped[bool] = mapped_column(server_default=text("0"))
    qualification_expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    tooling_available: Mapped[bool] = mapped_column(server_default=text("1"))
    effective_from_date: Mapped[date] = mapped_column(Date)

    product: Mapped["Product"] = relationship(
        back_populates="line_capabilities", lazy="select"
    )
    production_line: Mapped["ProductionLine"] = relationship(
        back_populates="line_capabilities", lazy="select"
    )

    # production_run is deliberately unmapped: unbounded relative to 7
    # capability rows (§19.1).
