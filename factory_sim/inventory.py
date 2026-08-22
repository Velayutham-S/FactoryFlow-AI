"""Inventory Engine — the stock ledger.

Owns ``inventory_movement``, which is a ledger rather than a balance table: every row
carries both the signed change and the balance that resulted. Three properties follow,
and the engine has to preserve all three.

* **Current stock is the latest movement's ``resulting_quantity_on_hand``.** It is
  stored nowhere else, so the running balance this engine maintains in memory must
  match what it writes.
* **The chain is the audit.** Each balance must equal the previous balance for that item
  plus this movement's delta, so the balance is read from the ledger at start-up and
  advanced in lock step from then on.
* **Stock may never go negative.** ``ck_im_balance_non_negative`` enforces it, and an
  issue larger than the balance on hand is refused rather than clamped — silently
  issuing less than was asked for would make the material record disagree with what
  physically moved.

Sign and reference rules are declared by the schema and mirrored here so a violation is
caught before the write: receipts, returns and transfers-in are positive; issues,
scrap consumption and transfers-out are negative; ``issue_production`` needs a run,
``issue_maintenance`` needs a work record, ``scrap_consumption`` needs a scrap record,
``receipt`` needs a supplier, and ``adjustment`` needs a note.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from models.operational import InventoryMovement

from factory_sim.context import SIMULATOR_COMPONENT, SimulationContext
from factory_sim.errors import SimulationStateError

POSITIVE_TYPES = frozenset({"receipt", "return", "transfer_in"})
NEGATIVE_TYPES = frozenset(
    {"issue_production", "issue_maintenance", "scrap_consumption", "transfer_out"})


class InventoryEngine:
    """Material consumption, replenishment and the running balance."""

    def __init__(self, context: SimulationContext) -> None:
        self.context = context
        self._replenishing: dict[int, datetime] = {}

    # ------------------------------------------------------------ initialisation

    def initialise(self, session: Session) -> None:
        """Load each item's balance from the ledger, opening stock where there is none.

        Master data holds stocking policy and deliberately holds no quantity, so a
        freshly seeded database has no balance at all. The first movement for an item is
        therefore a receipt from its primary supplier bringing it to its maximum stock
        level — which is what an opening physical count would record.
        """
        context = self.context
        for item_id in context.master.items:
            latest = session.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.inventory_item_id == item_id)
                .order_by(desc(InventoryMovement.movement_at),
                          desc(InventoryMovement.inventory_movement_id))
                .limit(1)
            ).first()
            if latest is not None:
                context.balances[item_id] = latest.resulting_quantity_on_hand

        storekeeper = context.pick(context.master.storekeepers)
        for item_id, item in context.master.items.items():
            if item_id in context.balances:
                continue
            if item.primary_supplier_id is None:
                context.balances[item_id] = Decimal("0.0000")
                continue
            self.record(
                session,
                item_id=item_id,
                movement_type="receipt",
                quantity=Decimal(item.max_stock_qty).quantize(Decimal("0.0001")),
                at=context.now,
                worker_id=storekeeper.worker_id,
                supplier_id=item.primary_supplier_id,
                reference_note="Opening stock",
            )

    # ------------------------------------------------------------------ recording

    def record(
        self,
        session: Session,
        *,
        item_id: int,
        movement_type: str,
        quantity: Decimal,
        at: datetime,
        worker_id: int,
        run_id: int | None = None,
        work_record_id: int | None = None,
        scrap_record_id: int | None = None,
        supplier_id: int | None = None,
        reference_note: str | None = None,
    ) -> InventoryMovement | None:
        """Append one movement, advancing the running balance.

        ``quantity`` is always given as a positive magnitude; the sign is applied from
        ``movement_type`` so a caller cannot accidentally invert it. Returns ``None``
        when an issue cannot be satisfied from stock, which the caller must treat as a
        material shortage rather than as success.
        """
        context = self.context
        item = context.master.items[item_id]
        magnitude = Decimal(quantity).quantize(Decimal("0.0001"))
        if magnitude <= 0:
            return None

        if movement_type in POSITIVE_TYPES:
            delta = magnitude
        elif movement_type in NEGATIVE_TYPES:
            delta = -magnitude
        else:
            raise SimulationStateError(
                "movement_type %r is not one this engine issues" % movement_type)

        balance = context.balances.get(item_id, Decimal("0.0000"))
        resulting = balance + delta
        if resulting < 0:
            return None  # ck_im_balance_non_negative; the caller sees a shortage

        self._check_references(
            movement_type, run_id, work_record_id, scrap_record_id, supplier_id,
            reference_note,
        )

        movement = InventoryMovement(
            inventory_movement_code=context.movement_code(at),
            inventory_item_id=item_id,
            inventory_location_id=item.default_inventory_location_id,
            movement_at=at,
            movement_type=movement_type,
            quantity_delta=delta,
            resulting_quantity_on_hand=resulting,
            production_run_id=run_id,
            maintenance_work_record_id=work_record_id,
            scrap_record_id=scrap_record_id,
            supplier_id=supplier_id,
            recorded_by_worker_id=worker_id,
            shift_id=context.shift_at(at).shift_id,
            reference_note=reference_note,
            created_by_component=SIMULATOR_COMPONENT,
        )
        session.add(movement)
        context.balances[item_id] = resulting
        return movement

    @staticmethod
    def _check_references(
        movement_type: str,
        run_id: int | None,
        work_record_id: int | None,
        scrap_record_id: int | None,
        supplier_id: int | None,
        reference_note: str | None,
    ) -> None:
        required = {
            "issue_production": ("production_run_id", run_id),
            "issue_maintenance": ("maintenance_work_record_id", work_record_id),
            "scrap_consumption": ("scrap_record_id", scrap_record_id),
            "receipt": ("supplier_id", supplier_id),
        }.get(movement_type)
        if required is not None and required[1] is None:
            raise SimulationStateError(
                "movement_type %r requires %s; unreferenced consumption is "
                "untraceable material" % (movement_type, required[0])
            )
        if movement_type == "adjustment" and not reference_note:
            raise SimulationStateError(
                "an adjustment requires a reference_note; an unexplained stock "
                "correction destroys the ledger's credibility"
            )

    # -------------------------------------------------------------- consumption

    def issue_for_run(
        self,
        session: Session,
        run_id: int,
        product_id: int,
        quantity_units: Decimal,
    ) -> int:
        """Issue the bill of materials for a run, including its scrap allowance.

        Quantity per unit and scrap allowance both come from ``bill_of_materials``, so
        the issue reconciles against the BOM as §41.6 expects.
        """
        context = self.context
        storekeeper = context.pick(context.master.storekeepers)
        issued = 0
        for line in context.master.bom_by_product.get(product_id, []):
            allowance = Decimal("1.0000") + (
                Decimal(line.scrap_allowance_pct or 0) / Decimal("100")
            )
            needed = (
                Decimal(line.quantity_per_unit) * Decimal(quantity_units) * allowance
            ).quantize(Decimal("0.0001"))
            movement = self.record(
                session,
                item_id=line.inventory_item_id,
                movement_type="issue_production",
                quantity=needed,
                at=context.now,
                worker_id=storekeeper.worker_id,
                run_id=run_id,
                reference_note=None,
            )
            if movement is not None:
                issued += 1
        return issued

    def consume_for_scrap(
        self,
        session: Session,
        scrap_record_id: int,
        product_id: int,
        quantity_units: Decimal,
        at: datetime,
        worker_id: int,
    ) -> int:
        """Write off the material the scrapped units contained.

        §E9 rule 7 expects a ``scrap_consumption`` movement for scrapped material.
        Only the product's critical components are consumed — a scrapped part does not
        return its coolant.
        """
        context = self.context
        consumed = 0
        for line in context.master.bom_by_product.get(product_id, []):
            if not line.is_critical_component:
                continue
            needed = (
                Decimal(line.quantity_per_unit) * Decimal(quantity_units)
            ).quantize(Decimal("0.0001"))
            movement = self.record(
                session,
                item_id=line.inventory_item_id,
                movement_type="scrap_consumption",
                quantity=needed,
                at=at,
                worker_id=worker_id,
                scrap_record_id=scrap_record_id,
            )
            if movement is not None:
                consumed += 1
        return consumed

    def issue_for_maintenance(
        self,
        session: Session,
        work_record_id: int,
        item_id: int,
        at: datetime,
        note: str | None = None,
    ) -> InventoryMovement | None:
        """Issue one spare against a work order, at ``part_collected`` time.

        §E12 rule 7 requires this movement to accompany the ``part_collected`` activity,
        which is why both are written inside T-SIM-7.
        """
        context = self.context
        storekeeper = context.pick(context.master.storekeepers)
        return self.record(
            session,
            item_id=item_id,
            movement_type="issue_maintenance",
            quantity=Decimal("1.0000"),
            at=at,
            worker_id=storekeeper.worker_id,
            work_record_id=work_record_id,
            reference_note=note,
        )

    # ------------------------------------------------------------- replenishment

    def replenish(self, session: Session) -> int:
        """Receive stock for items at or below their reorder point.

        The order is placed when the balance reaches the reorder point and arrives after
        the supplier's ``standard_lead_time_days`` — the lead time is master data, so
        replenishment timing is not the simulator's invention. An item already awaiting
        a delivery is not reordered.
        """
        context = self.context
        storekeeper = context.pick(context.master.storekeepers)
        received = 0

        for item_id, item in context.master.items.items():
            balance = context.balances.get(item_id)
            if balance is None or item.primary_supplier_id is None:
                continue

            arriving = self._replenishing.get(item_id)
            if arriving is not None:
                if context.now >= arriving:
                    quantity = (
                        Decimal(item.max_stock_qty) - balance
                    ).quantize(Decimal("0.0001"))
                    if quantity > 0:
                        movement = self.record(
                            session,
                            item_id=item_id,
                            movement_type="receipt",
                            quantity=quantity,
                            at=context.now,
                            worker_id=storekeeper.worker_id,
                            supplier_id=item.primary_supplier_id,
                            reference_note="Replenishment to maximum stock level",
                        )
                        if movement is not None:
                            received += 1
                    del self._replenishing[item_id]
                continue

            if balance <= Decimal(item.reorder_point):
                lead_days = int(item.lead_time_days or 0)
                self._replenishing[item_id] = context.now + timedelta(days=lead_days)

        return received
