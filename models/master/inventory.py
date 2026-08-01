"""Master models M18-M21: where stock lives, what it is, what it goes into, and who
supplies it.

``InventoryItem`` holds stocking *policy* and no quantity. Quantity lives in
``inventory_movement`` as a ledger, so there is exactly one source of truth for how
much of anything is on hand (§O10).
"""

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
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
from models.enums.master import (
    InventoryItemType,
    InventoryLocationType,
    SupplierType,
    UnitOfMeasure,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk, Measurement, Money, Percent, Quantity, Ratio1

if TYPE_CHECKING:
    from models.master.failure import MachineTypeFailureMode
    from models.master.maintenance import MachineMaintenanceSchedule
    from models.master.plant import PlantArea
    from models.master.production import Product


class Supplier(SoftDeleteMixin, TimestampCreatedMixin,
               TimestampUpdatedMixin, Base):
    """Table ``supplier``, master group: the external source of an item, and the lead
    time a replenishment recommendation is built from."""

    __tablename__ = "supplier"
    __table_args__ = (
        UniqueConstraint("supplier_code", name="uq_supplier_code"),
        CheckConstraint("supplier_code GLOB 'SUP-[0-9][0-9][0-9]'",
                        name=conv("ck_supplier_code_format")),
        CheckConstraint("country_code GLOB '[A-Z][A-Z]'",
                        name=conv("ck_supplier_country_code_format")),
        CheckConstraint("standard_lead_time_days >= 0",
                        name=conv("ck_supplier_lead_time_non_negative")),
        CheckConstraint(
            "expedited_lead_time_days IS NULL "
            "OR expedited_lead_time_days < standard_lead_time_days",
            name=conv("ck_supplier_expedited_faster")),
        CheckConstraint("reliability_rating BETWEEN 0.0 AND 5.0",
                        name=conv("ck_supplier_reliability_range")),
        CheckConstraint(
            "on_time_delivery_pct IS NULL "
            "OR on_time_delivery_pct BETWEEN 0 AND 100",
            name=conv("ck_supplier_otd_range")),
        CheckConstraint(
            "contact_email IS NULL OR (contact_email GLOB '?*@?*.?*' "
            "AND contact_email NOT GLOB '*@*@*')",
            name=conv("ck_supplier_email_format")),
        CheckConstraint("length(trim(supplier_name)) > 0",
                        name=conv("ck_supplier_name_not_blank")),
        CheckConstraint(
            "supplier_type IN ('raw_material', 'component', 'spare_part', "
            "'consumable', 'service')",
            name=conv("ck_supplier_supplier_type_allowed")),
        CheckConstraint("length(supplier_code) <= 10",
                        name=conv("ck_supplier_supplier_code_length")),
        CheckConstraint("length(supplier_name) <= 150",
                        name=conv("ck_supplier_supplier_name_length")),
        CheckConstraint(
            "contact_person IS NULL OR length(contact_person) <= 100",
            name=conv("ck_supplier_contact_person_length")),
        CheckConstraint(
            "contact_email IS NULL OR length(contact_email) <= 150",
            name=conv("ck_supplier_contact_email_length")),
        CheckConstraint(
            "contact_phone IS NULL OR length(contact_phone) <= 20",
            name=conv("ck_supplier_contact_phone_length")),
        CheckConstraint("length(city) <= 80",
                        name=conv("ck_supplier_city_length")),
        CheckConstraint("length(country_code) = 2",
                        name=conv("ck_supplier_country_code_length")),
        CheckConstraint("is_approved_vendor IN (0, 1)",
                        name=conv("ck_supplier_is_approved_vendor_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_supplier_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    supplier_id: Mapped[MasterPk]
    supplier_code: Mapped[str] = mapped_column(String(10))
    supplier_name: Mapped[str] = mapped_column(String(150))
    supplier_type: Mapped[SupplierType] = mapped_column(
        Enum(SupplierType, name="supplier_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    contact_person: Mapped[Optional[str]] = mapped_column(String(100))
    contact_email: Mapped[Optional[str]] = mapped_column(String(150))
    contact_phone: Mapped[Optional[str]] = mapped_column(String(20))
    city: Mapped[str] = mapped_column(String(80))
    country_code: Mapped[str] = mapped_column(CHAR(2))
    standard_lead_time_days: Mapped[int] = mapped_column(Integer)
    expedited_lead_time_days: Mapped[Optional[int]] = mapped_column(Integer)
    reliability_rating: Mapped[Ratio1]
    on_time_delivery_pct: Mapped[Optional[Percent]]
    is_approved_vendor: Mapped[bool] = mapped_column(server_default=text("1"))
    contract_expiry_date: Mapped[Optional[date]] = mapped_column(Date)

    inventory_items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="primary_supplier", lazy="select"
    )

    # A supplier cannot be soft-retired while an active item names it as primary
    # source: an item left with no source cannot be replenished at all. That is a
    # state pre-condition no constraint can express (§41.3).

    @validates("supplier_code", "country_code")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()

    @validates("contact_email")
    def _normalise_email(self, key: str, value: Optional[str]) -> Optional[str]:
        return value if value is None else value.strip().lower()


class InventoryLocation(SoftDeleteMixin, TimestampCreatedMixin,
                        TimestampUpdatedMixin, Base):
    """Table ``inventory_location``, master group: a stocking point, carrying the
    retrieval time that feeds every repair duration estimate."""

    __tablename__ = "inventory_location"
    __table_args__ = (
        UniqueConstraint("inventory_location_code",
                         name="uq_inventory_location_code"),
        CheckConstraint(
            "inventory_location_code GLOB 'LOC-[A-Z][A-Z]-[A-Z0-9]*' "
            "AND length(inventory_location_code) BETWEEN 8 AND 11 "
            "AND substr(inventory_location_code, 8) NOT GLOB '*[^A-Z0-9]*'",
            name=conv("ck_inventory_location_code_format")),
        CheckConstraint("capacity_slots IS NULL OR capacity_slots > 0",
                        name=conv("ck_inventory_location_capacity_positive")),
        CheckConstraint("average_retrieval_time_minutes BETWEEN 1 AND 240",
                        name=conv("ck_inventory_location_retrieval_range")),
        CheckConstraint(
            "stock_count_frequency_days IS NULL "
            "OR stock_count_frequency_days BETWEEN 1 AND 365",
            name=conv("ck_inventory_location_count_frequency_range")),
        CheckConstraint("length(trim(location_name)) > 0",
                        name=conv("ck_inventory_location_name_not_blank")),
        CheckConstraint(
            "location_type IN ('raw_material_store', 'spare_parts_store', "
            "'tooling_crib', 'wip_buffer', 'finished_goods_store', "
            "'quarantine')",
            name=conv("ck_inventory_location_location_type_allowed")),
        CheckConstraint(
            "length(inventory_location_code) <= 16",
            name=conv("ck_inventory_location_inventory_location_code_length")),
        CheckConstraint("length(location_name) <= 100",
                        name=conv("ck_inventory_location_location_name_length")),
        CheckConstraint(
            "is_temperature_controlled IN (0, 1)",
            name=conv("ck_inventory_location_is_temperature_controlled_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_inventory_location_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    inventory_location_id: Mapped[MasterPk]
    inventory_location_code: Mapped[str] = mapped_column(String(16))
    location_name: Mapped[str] = mapped_column(String(100))
    plant_area_id: Mapped[int] = mapped_column(
        ForeignKey("plant_area.plant_area_id",
                   name="fk_inventory_location_plant_area",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    location_type: Mapped[InventoryLocationType] = mapped_column(
        Enum(InventoryLocationType, name="inventory_location_type",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    capacity_slots: Mapped[Optional[int]] = mapped_column(Integer)
    is_temperature_controlled: Mapped[bool] = mapped_column(
        server_default=text("0")
    )
    # The commitment machine_maintenance_activity's part-retrieval interval is
    # measured against (§O12).
    average_retrieval_time_minutes: Mapped[int] = mapped_column(Integer)
    stock_count_frequency_days: Mapped[Optional[int]] = mapped_column(Integer)

    plant_area: Mapped["PlantArea"] = relationship(
        back_populates="inventory_locations", lazy="select"
    )
    inventory_items: Mapped[list["InventoryItem"]] = relationship(
        back_populates="default_inventory_location", lazy="select"
    )

    @validates("inventory_location_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class InventoryItem(SoftDeleteMixin, TimestampCreatedMixin,
                    TimestampUpdatedMixin, Base):
    """Table ``inventory_item``, master group: stocking policy for a part or material.

    Holds no quantity: the balance lives in ``inventory_movement`` as a running
    ledger. A second current-state table was considered and rejected, because it
    would be a second source of truth for the same number (§O10).
    """

    __tablename__ = "inventory_item"
    __table_args__ = (
        UniqueConstraint("inventory_item_code", name="uq_inventory_item_code"),
        CheckConstraint(
            "inventory_item_code GLOB 'INV-[A-Z][A-Z]-[A-Z0-9-][A-Z0-9-]*' "
            "AND length(inventory_item_code) BETWEEN 9 AND 21 "
            "AND substr(inventory_item_code, 8) NOT GLOB '*[^A-Z0-9-]*'",
            name=conv("ck_inventory_item_code_format")),
        # Enforced by exactly the same mechanism as the 100 enum bindings, but
        # deliberately given no enum class: three single-character values that
        # are conventionally letters (§40.6).
        CheckConstraint("abc_class IN ('A', 'B', 'C')",
                        name=conv("ck_inventory_item_abc_class_allowed")),
        CheckConstraint("unit_cost > 0",
                        name=conv("ck_inventory_item_unit_cost_positive")),
        CheckConstraint(
            "safety_stock_qty <= reorder_point "
            "AND reorder_point < max_stock_qty",
            name=conv("ck_inventory_item_stock_thresholds_ordered")),
        CheckConstraint("safety_stock_qty >= 0",
                        name=conv("ck_inventory_item_safety_stock_non_negative")),
        CheckConstraint("lead_time_days >= 0",
                        name=conv("ck_inventory_item_lead_time_non_negative")),
        CheckConstraint(
            "is_critical_spare = 0 OR safety_stock_qty > 0",
            name=conv("ck_inventory_item_critical_spare_has_buffer")),
        CheckConstraint("shelf_life_days IS NULL OR shelf_life_days > 0",
                        name=conv("ck_inventory_item_shelf_life_positive")),
        CheckConstraint("length(trim(item_name)) > 0",
                        name=conv("ck_inventory_item_name_not_blank")),
        CheckConstraint(
            "item_type IN ('raw_material', 'component', 'consumable', "
            "'spare_part', 'tooling', 'finished_good')",
            name=conv("ck_inventory_item_item_type_allowed")),
        # All six values permitted here, unlike product (§40.3).
        CheckConstraint(
            "unit_of_measure IN ('EA', 'KG', 'L', 'M', 'SET', 'BOX')",
            name=conv("ck_inventory_item_unit_of_measure_allowed")),
        CheckConstraint("length(inventory_item_code) <= 24",
                        name=conv("ck_inventory_item_inventory_item_code_length")),
        CheckConstraint("length(item_name) <= 150",
                        name=conv("ck_inventory_item_item_name_length")),
        CheckConstraint("length(abc_class) = 1",
                        name=conv("ck_inventory_item_abc_class_length")),
        CheckConstraint("is_critical_spare IN (0, 1)",
                        name=conv("ck_inventory_item_is_critical_spare_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_inventory_item_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    inventory_item_id: Mapped[MasterPk]
    inventory_item_code: Mapped[str] = mapped_column(String(24))
    item_name: Mapped[str] = mapped_column(String(150))
    item_type: Mapped[InventoryItemType] = mapped_column(
        Enum(InventoryItemType, name="inventory_item_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    unit_of_measure: Mapped[UnitOfMeasure] = mapped_column(
        Enum(UnitOfMeasure, name="unit_of_measure", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    unit_cost: Mapped[Money]
    reorder_point: Mapped[Quantity]
    safety_stock_qty: Mapped[Quantity]
    max_stock_qty: Mapped[Quantity]
    lead_time_days: Mapped[int] = mapped_column(Integer)
    primary_supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier.supplier_id", name="fk_inventory_item_supplier",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    default_inventory_location_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_location.inventory_location_id",
                   name="fk_inventory_item_location",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    is_critical_spare: Mapped[bool] = mapped_column(server_default=text("0"))
    # str rather than an enum class: the check constraint is the whole of its
    # enforcement, and the line is drawn on whether the set is worth naming in
    # Python, not on what the database does (§40.6).
    abc_class: Mapped[str] = mapped_column(CHAR(1))
    shelf_life_days: Mapped[Optional[int]] = mapped_column(Integer)
    specification: Mapped[Optional[str]] = mapped_column(Text)

    primary_supplier: Mapped[Optional["Supplier"]] = relationship(
        back_populates="inventory_items", lazy="select"
    )
    default_inventory_location: Mapped["InventoryLocation"] = relationship(
        back_populates="inventory_items", lazy="select"
    )
    bom_lines: Mapped[list["BillOfMaterials"]] = relationship(
        back_populates="inventory_item", lazy="select",
        foreign_keys="BillOfMaterials.inventory_item_id"
    )
    bom_lines_as_substitute: Mapped[list["BillOfMaterials"]] = relationship(
        back_populates="substitute_inventory_item", lazy="select",
        foreign_keys="BillOfMaterials.substitute_inventory_item_id"
    )
    required_by_failure_modes: Mapped[list["MachineTypeFailureMode"]] = relationship(
        back_populates="required_inventory_item", lazy="select"
    )
    required_by_maintenance_schedules: Mapped[
        list["MachineMaintenanceSchedule"]
    ] = relationship(back_populates="required_inventory_item", lazy="select")

    @validates("inventory_item_code", "abc_class")
    def _normalise_upper(self, key: str, value: str) -> str:
        return value.strip().upper()


class BillOfMaterials(SoftDeleteMixin, TimestampCreatedMixin,
                      TimestampUpdatedMixin, Base):
    """Table ``bill_of_materials``, master group: what a product consumes per unit.

    An association object rather than a ``secondary=`` table, because it carries
    its own columns -- quantity, scrap allowance, substitute, effective date --
    and a plain many-to-many could hold none of them (§37.5).
    """

    __tablename__ = "bill_of_materials"
    __table_args__ = (
        UniqueConstraint("product_id", "inventory_item_id",
                         name="uq_bill_of_materials_pair"),
        CheckConstraint("quantity_per_unit > 0",
                        name=conv("ck_bom_quantity_positive")),
        CheckConstraint("scrap_allowance_pct BETWEEN 0 AND 50",
                        name=conv("ck_bom_scrap_allowance_range")),
        CheckConstraint(
            "substitute_inventory_item_id IS NULL "
            "OR substitute_inventory_item_id <> inventory_item_id",
            name=conv("ck_bom_substitute_differs")),
        CheckConstraint("is_critical_component IN (0, 1)",
                        name=conv("ck_bom_is_critical_component_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_bom_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    bill_of_materials_id: Mapped[MasterPk]
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product.product_id", name="fk_bom_product",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_bom_inventory_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Four decimals because fractional consumption per unit is real.
    quantity_per_unit: Mapped[Measurement]
    scrap_allowance_pct: Mapped[Percent] = mapped_column(server_default=text("0"))
    is_critical_component: Mapped[bool] = mapped_column(server_default=text("0"))
    substitute_inventory_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_bom_substitute_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    effective_from_date: Mapped[date] = mapped_column(Date)

    product: Mapped["Product"] = relationship(
        back_populates="bom_lines", lazy="select"
    )
    # Two keys to one target, so both relationships state foreign_keys
    # explicitly. Role-qualified names, not target names (§37.6, §45.5).
    inventory_item: Mapped["InventoryItem"] = relationship(
        back_populates="bom_lines", lazy="select",
        foreign_keys=[inventory_item_id]
    )
    substitute_inventory_item: Mapped[Optional["InventoryItem"]] = relationship(
        back_populates="bom_lines_as_substitute", lazy="select",
        foreign_keys=[substitute_inventory_item_id]
    )
