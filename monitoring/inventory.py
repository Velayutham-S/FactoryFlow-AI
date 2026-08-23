"""Inventory detection, from the stock ledger against master stocking policy.

Two of the twelve documented event types, and the two most directly specified in the
frozen documents:

============================  =============================================================
``reorder_point_reached``     balance at or below ``inventory_item.reorder_point``
``safety_stock_breached``     critical spare at or below ``inventory_item.safety_stock_qty``
============================  =============================================================

§E10 rule 6: "A movement leaving stock at or below ``inventory_item.reorder_point`` is a
replenishment condition the Monitoring Agent raises as an event."

§E10 rule 7: "A movement leaving a ``is_critical_spare`` item at or below
``safety_stock_qty`` is a **high-severity** condition. The master model's rule that
critical spares must carry non-zero safety stock exists precisely so this check has a
floor to compare against."

**Detection is per movement, not per balance query.** Each movement carries the balance it
produced, so the condition is read straight off the ledger row: §E10's consumer note calls
for "a single indexed read per movement, not an aggregate". That also means the event's
``detected_at`` is the moment stock actually crossed the floor rather than the moment the
agent happened to look.

**Inventory events carry no machine.** ``ck_oe_inventory_subject_required`` requires
``inventory_item_id`` and the category has no machine subject, so these alerts are the one
kind with a NULL ``machine_id`` -- which is why ``open_alert_count`` is untouched by them.
The correlation subject is the item code, giving keys like
``INV-CP-BRG-6205|inventory``.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.operational import InventoryMovement

from monitoring.context import Detection, MonitoringContext


class InventoryMonitor:
    """Stock balances against master reorder points and safety floors."""

    def __init__(self, context: MonitoringContext) -> None:
        self.context = context

    def evaluate(self, session: Session) -> list[Detection]:
        """Examine movements recorded since the last cycle."""
        context = self.context
        master = context.master
        cursor = context.cursors.movement_at

        query = select(InventoryMovement).order_by(InventoryMovement.movement_at)
        if cursor is not None:
            query = query.where(InventoryMovement.movement_at > cursor)

        detections: list[Detection] = []
        newest = cursor
        # One event per item per cycle, at its lowest balance: a run that issues the same
        # item repeatedly crosses the floor once, and reporting every subsequent movement
        # would describe the same condition many times.
        lowest: dict[int, InventoryMovement] = {}

        for movement in session.scalars(query):
            newest = (
                movement.movement_at if newest is None
                else max(newest, movement.movement_at)
            )
            item = master.items.get(movement.inventory_item_id)
            if item is None:
                continue
            current = lowest.get(movement.inventory_item_id)
            if (
                current is None
                or movement.resulting_quantity_on_hand
                < current.resulting_quantity_on_hand
            ):
                lowest[movement.inventory_item_id] = movement

        for item_id, movement in lowest.items():
            item = master.items[item_id]
            balance = movement.resulting_quantity_on_hand

            # Safety stock is tested first: a critical spare below its floor is the more
            # severe condition and reporting both would describe one shortage twice.
            if (
                item.is_critical_spare
                and item.safety_stock_qty is not None
                and balance <= item.safety_stock_qty
            ):
                detections.append(self._detection(
                    item, movement, balance, item.safety_stock_qty,
                    "safety_stock_breached",
                    self._critical_severity(),
                    "Critical spare %s at %s %s, at or below its safety stock floor of "
                    "%s. Lead time %s days from the primary supplier."
                    % (item.inventory_item_code, balance, item.unit_of_measure,
                       item.safety_stock_qty, item.lead_time_days),
                ))
                continue

            if item.reorder_point is not None and balance <= item.reorder_point:
                detections.append(self._detection(
                    item, movement, balance, item.reorder_point,
                    "reorder_point_reached",
                    self.context.master.least_severe_id(),
                    "%s at %s %s, at or below its reorder point of %s. Replenishment "
                    "required; lead time %s days."
                    % (item.inventory_item_code, balance, item.unit_of_measure,
                       item.reorder_point, item.lead_time_days),
                ))

        context.cursors.movement_at = newest
        return detections

    def _detection(
        self,
        item,
        movement: InventoryMovement,
        balance,
        floor,
        event_type: str,
        severity_level_id: int,
        note: str,
    ) -> Detection:
        return Detection(
            category="inventory",
            event_type=event_type,
            detected_at=movement.movement_at,
            severity_level_id=severity_level_id,
            correlation_subject=item.inventory_item_code,
            inventory_item_id=item.inventory_item_id,
            observed_value=balance,
            threshold_value_breached=floor,
            threshold_direction="below_low",
            detection_note=note,
        )

    def _critical_severity(self) -> int:
        """Severity for a critical spare below its floor.

        §E10 rule 7 calls this a **high-severity** condition. "High" is a named level in
        the severity scale, so the level whose rank sits second -- one below the most
        severe -- is selected from master data rather than hard-coding an identifier.
        """
        master = self.context.master
        ranked = sorted(master.severities.values(), key=lambda s: s.severity_rank)
        if len(ranked) >= 2:
            return ranked[1].failure_severity_level_id
        return master.most_severe_id()
