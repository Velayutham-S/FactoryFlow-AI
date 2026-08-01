"""Operational model O10: the stock ledger.

Quantity lives here as a *ledger*, not a balance. Each movement stores both the
signed change and the balance that resulted, which buys three things: current stock
is one indexed lookup rather than a SUM over the item's whole history; the ledger is
self-auditing, since every row's balance must equal the previous row's plus this
row's delta, making a break locatable rather than merely detectable; and stock at
any past moment is recoverable, so "was the bearing in stock when we made the
recommendation?" is answerable without replay.

A separate current-state balance table was considered and rejected: it would be a
second source of truth for the same number, and the two would eventually disagree,
at which point neither could be trusted (§O10).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import InventoryMovementType
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    require_timezone_aware,
)
from models.types import Measurement, OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.inventory import InventoryItem, InventoryLocation, Supplier
    from models.master.people import Worker
    from models.master.plant import Shift
    from models.operational.maintenance import MaintenanceWorkRecord
    from models.operational.production import ProductionRun
    from models.operational.quality import ScrapRecord


class InventoryMovement(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``inventory_movement``, operational group: every receipt, issue, return,
    adjustment and consumption, with the resulting balance. Append-only."""

    __tablename__ = "inventory_movement"
    __table_args__ = (
        UniqueConstraint("inventory_movement_code", name="uq_im_code"),
        CheckConstraint(
            "inventory_movement_code GLOB "
            "'MOV-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9][0-9]'",
            name=conv("ck_im_code_format")),
        CheckConstraint("quantity_delta <> 0",
                        name=conv("ck_im_quantity_delta_non_zero")),
        # Issuing more than is on hand is physically impossible.
        CheckConstraint("resulting_quantity_on_hand >= 0",
                        name=conv("ck_im_balance_non_negative")),
        CheckConstraint(
            "(movement_type IN ('receipt', 'return', 'transfer_in') "
            "AND quantity_delta > 0) "
            "OR (movement_type IN ('issue_production', 'issue_maintenance', "
            "'scrap_consumption', 'transfer_out') AND quantity_delta < 0) "
            "OR movement_type = 'adjustment'",
            name=conv("ck_im_delta_sign_matches_type")),
        # The four type-specific reference constraints together make an entire
        # class of untraceable material movement impossible: unreferenced
        # consumption is material that left the store with no explanation, and a
        # ledger containing it cannot be reconciled against a physical count.
        CheckConstraint(
            "movement_type <> 'issue_production' "
            "OR production_run_id IS NOT NULL",
            name=conv("ck_im_production_reference_required")),
        CheckConstraint(
            "movement_type <> 'issue_maintenance' "
            "OR maintenance_work_record_id IS NOT NULL",
            name=conv("ck_im_maintenance_reference_required")),
        CheckConstraint(
            "movement_type <> 'scrap_consumption' "
            "OR scrap_record_id IS NOT NULL",
            name=conv("ck_im_scrap_reference_required")),
        CheckConstraint(
            "movement_type <> 'receipt' OR supplier_id IS NOT NULL",
            name=conv("ck_im_receipt_supplier_required")),
        # An unexplained stock correction destroys the ledger's credibility.
        CheckConstraint(
            "movement_type <> 'adjustment' OR reference_note IS NOT NULL",
            name=conv("ck_im_adjustment_note_required")),
        CheckConstraint(
            "movement_type IN ('receipt', 'issue_production', "
            "'issue_maintenance', 'return', 'adjustment', "
            "'scrap_consumption', 'transfer_out', 'transfer_in')",
            name=conv("ck_im_movement_type_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_im_created_by_component_allowed")),
        CheckConstraint("length(inventory_movement_code) <= 22",
                        name=conv("ck_im_inventory_movement_code_length")),
        {"sqlite_autoincrement": True},
    )

    inventory_movement_id: Mapped[OperationalPk]
    inventory_movement_code: Mapped[str] = mapped_column(String(22))
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_im_inventory_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Where the transaction physically happened. Retrieval time differs by
    # location and feeds the repair estimate.
    inventory_location_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_location.inventory_location_id",
                   name="fk_im_inventory_location",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    movement_at: Mapped[TimestampTz]
    movement_type: Mapped[InventoryMovementType] = mapped_column(
        Enum(InventoryMovementType, name="inventory_movement_type",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Signed: negative for issues and consumption.
    quantity_delta: Mapped[Measurement]
    # The running balance. The balance chain is the audit, and it cannot be a
    # check constraint: each row's balance depends on the previous row for the
    # same item, which no per-row predicate can see (§41.6 of the schema doc).
    resulting_quantity_on_hand: Mapped[Measurement]
    production_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_im_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    maintenance_work_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_work_record.maintenance_work_record_id",
                   name="fk_im_work_record",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    scrap_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("scrap_record.scrap_record_id", name="fk_im_scrap_record",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    supplier_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("supplier.supplier_id", name="fk_im_supplier",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    recorded_by_worker_id: Mapped[int] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_im_recorded_by",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # A part needed on a shift with no storekeeper is a real constraint.
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_im_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    reference_note: Mapped[Optional[str]] = mapped_column(Text)

    # Six of the eight are unidirectional (§O10).
    inventory_item: Mapped["InventoryItem"] = relationship(lazy="select")
    inventory_location: Mapped["InventoryLocation"] = relationship(lazy="select")
    production_run: Mapped[Optional["ProductionRun"]] = relationship(
        lazy="select"
    )
    maintenance_work_record: Mapped[Optional["MaintenanceWorkRecord"]] = (
        relationship(back_populates="inventory_movements", lazy="select")
    )
    scrap_record: Mapped[Optional["ScrapRecord"]] = relationship(
        back_populates="inventory_movements", lazy="select"
    )
    supplier: Mapped[Optional["Supplier"]] = relationship(lazy="select")
    recorded_by: Mapped["Worker"] = relationship(lazy="select")
    shift: Mapped["Shift"] = relationship(lazy="select")

    @validates("inventory_movement_code", "inventory_item_id",
               "inventory_location_id", "movement_at", "movement_type",
               "quantity_delta", "resulting_quantity_on_hand",
               "production_run_id", "maintenance_work_record_id",
               "scrap_record_id", "supplier_id", "recorded_by_worker_id",
               "shift_id", "reference_note", "inventory_item",
               "inventory_location", "production_run",
               "maintenance_work_record", "scrap_record", "supplier",
               "recorded_by", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``movement_at`` must be timezone-aware (§41.3, §41.4).

        Errors are corrected by an ``adjustment`` movement, never by editing.
        """
        if inspect(self).persistent:
            raise ValueError(
                "inventory_movement is append-only; %s cannot be reassigned. "
                "Correct an error with an adjustment movement" % key
            )
        if key == "movement_at":
            return require_timezone_aware(key, value)
        if key == "inventory_movement_code":
            return value.strip().upper()
        return value
