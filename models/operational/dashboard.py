"""Operational model O22: the precomputed presentation aggregate.

Sits in the operational group because its content is factory state, not platform
state (§9.3 of the schema document).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import SnapshotScope
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    TimestampUpdatedMixin,
    require_timezone_aware,
)
from models.types import JsonDoc, OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.production import ProductionLine


class DashboardSnapshot(TimestampCreatedMixin, ComponentProvenanceMixin,
                        TimestampUpdatedMixin, Base):
    """Table ``dashboard_snapshot``, operational group: one precomputed aggregate for
    one scope at one instant. Rebuildable idempotently."""

    __tablename__ = "dashboard_snapshot"
    __table_args__ = (
        CheckConstraint(
            "snapshot_scope <> 'production_line' "
            "OR production_line_id IS NOT NULL",
            name=conv("ck_ds_line_scope_subject")),
        CheckConstraint(
            "snapshot_scope <> 'machine' OR machine_id IS NOT NULL",
            name=conv("ck_ds_machine_scope_subject")),
        CheckConstraint(
            "snapshot_scope <> 'plant' "
            "OR (production_line_id IS NULL AND machine_id IS NULL)",
            name=conv("ck_ds_plant_scope_no_subject")),
        CheckConstraint("computed_from_window_seconds > 0",
                        name=conv("ck_ds_window_positive")),
        CheckConstraint("generation_duration_ms >= 0",
                        name=conv("ck_ds_generation_duration_non_negative")),
        CheckConstraint(
            "json_valid(snapshot_document) "
            "AND json_type(snapshot_document) = 'object'",
            name=conv("ck_ds_snapshot_document_is_object")),
        CheckConstraint(
            "snapshot_scope IN ('plant', 'production_line', 'machine')",
            name=conv("ck_ds_snapshot_scope_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_ds_created_by_component_allowed")),
        # An EXPRESSION index rather than a plain one, because SQLite treats every
        # NULL in a unique index as distinct. A plain index over the four columns
        # would constrain nothing at all for plant-scoped rows -- those carry NULL
        # in both subject columns, so no two of them would ever conflict, and
        # duplicate snapshots at the same instant would be accepted silently.
        #
        # -1 is safe as the substitute because AUTOINCREMENT issues only positive
        # integers, so no real identity value can collide with it.
        #
        # CONSEQUENCE FOR CALLERS: an expression index is used only when a query's
        # predicate matches the expression. A lookup by scope and subject must be
        # written with the same COALESCE form, or it gets a table scan with no
        # error raised. This is the one place in the layer where a query has to be
        # written a particular way for a constraint's index to be usable (§37.9).
        Index(
            "uq_ds_scope_subject_time",
            text("snapshot_scope"),
            text("COALESCE(production_line_id, -1)"),
            text("COALESCE(machine_id, -1)"),
            text("snapshot_at"),
            unique=True,
        ),
        {"sqlite_autoincrement": True},
    )

    dashboard_snapshot_id: Mapped[OperationalPk]
    snapshot_at: Mapped[TimestampTz]
    snapshot_scope: Mapped[SnapshotScope] = mapped_column(
        Enum(SnapshotScope, name="snapshot_scope", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    production_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_ds_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_ds_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # The whole aggregate, replaced wholesale on rebuild and never edited in
    # place. MutableDict is deliberately not applied, so the whole-replacement
    # convention is load-bearing rather than stylistic (§39.6).
    snapshot_document: Mapped[JsonDoc]
    computed_from_window_seconds: Mapped[int] = mapped_column(Integer)
    generation_duration_ms: Mapped[int] = mapped_column(Integer)

    # Both relationships are unidirectional (§O22).
    production_line: Mapped[Optional["ProductionLine"]] = relationship(
        lazy="select"
    )
    machine: Mapped[Optional["Machine"]] = relationship(lazy="select")

    @validates("snapshot_at", "snapshot_scope", "production_line_id",
               "machine_id", "production_line", "machine")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Identity columns immutable; ``snapshot_at`` timezone-aware (§41.3, §41.4).

        Mutable by idempotent rebuild only: the document and the timing columns may
        be recomputed, but the scope and subject identify the row.
        """
        if inspect(self).persistent:
            raise ValueError(
                "dashboard_snapshot.%s identifies the row and cannot be "
                "reassigned; rebuild recomputes the document only" % key
            )
        if key == "snapshot_at":
            return require_timezone_aware(key, value)
        return value
