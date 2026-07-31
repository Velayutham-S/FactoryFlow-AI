"""The four mixins, each carrying exactly one concern (§34).

No mixin knows about another, and four compositions cover all 53 models with
zero exceptions:

===========================  ==================================  =====
Composition                  Mixins                              Count
===========================  ==================================  =====
Master standard              SoftDelete + Created + Updated         28
Master machine               Created + Updated                       1
Operational append-only      Created + Provenance                   16
Operational mutable          Created + Updated + Provenance          8
===========================  ==================================  =====

Four narrow mixins beat one wide one because the variance becomes part of the
declaration instead of an exception to it: ``Machine``'s difference is not
composing SoftDeleteMixin, and an append-only model's difference is not
composing TimestampUpdatedMixin. A reader can tell whether a model is
append-only from its class line.

``sort_order`` places each mixin column after the model's own columns and in the
frozen schema's trailing order -- ``is_active``, ``created_at``,
``created_by_component``, ``updated_at``. Without it SQLAlchemy emits mixin
columns first, because they are created at mixin-definition time and column
order follows creation order.

The named boolean and vocabulary check constraints for the columns supplied here
are declared in each model's ``__table_args__``, not by these mixins. The frozen
schema names them from a table abbreviation in many cases --
ck_atp_is_active_bool on alert_threshold_profile, ck_bom_is_active_bool on
bill_of_materials, ck_msr_created_by_component_allowed on
machine_sensor_reading -- so no mixin-generated name could reproduce them
(§32, §45.9 check 2).
"""

from datetime import datetime

from sqlalchemy import Enum, func, text
from sqlalchemy.orm import Mapped, mapped_column, validates

from models.enums.system import PlatformComponent
from models.types import TimestampTz


def require_timezone_aware(key: str, value: datetime | None) -> datetime | None:
    """Reject a naive datetime (§41.3, category 2).

    This is the one validation the ORM performs that the database structurally
    cannot. SQLite has no date/time type and no concept of a zone: the column
    holds a string, so a naive value is accepted, written, and read back looking
    exactly as legitimate as a correct one. There is no offset in the stored
    value to disagree with and nothing for a check constraint to test.

    Guessing instead of rejecting is what makes the failure dangerous. Assuming
    a naive ``recorded_at`` is UTC when it is local time shifts every reading by
    the offset, moves readings across shift boundaries, and surfaces weeks later
    as a shift-report anomaly. Rejecting at assignment makes the defect immediate
    and local.

    Used by the timestamp mixins for their own columns and by each model for its
    own event-time columns.
    """
    if value is not None and (
        value.tzinfo is None or value.tzinfo.utcoffset(value) is None
    ):
        raise ValueError(
            "%s requires a timezone-aware datetime in UTC; got a naive value"
            % key
        )
    return value


class SoftDeleteMixin:
    """Supplies ``is_active``; composed by the 28 master models except Machine (§30).

    Nothing in this database is ever hard-deleted by application code. Master
    rows are retired by setting this flag to 0, because operational history
    references them and the 162 RESTRICT foreign keys make deletion fail.

    There is deliberately no global query filter hiding inactive rows and no
    ``delete()`` helper: a 2024 maintenance record must still resolve its
    engineer even if that engineer has left, so filtering is the caller's
    explicit responsibility (§30).
    """

    is_active: Mapped[bool] = mapped_column(server_default=text("1"),
                                            sort_order=90)


class TimestampCreatedMixin:
    """Supplies ``created_at``; composed by all 53 models (§28).

    Record time, never event time. ``created_at`` is when the row was written;
    ``recorded_at``, ``detected_at``, ``opened_at`` and their siblings are when
    the thing happened, and the ORM derives neither from the other.
    """

    created_at: Mapped[TimestampTz] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"), sort_order=91
    )

    @validates("created_at")
    def _reject_naive_created_at(self, key: str, value: datetime) -> datetime:
        return require_timezone_aware(key, value)


class TimestampUpdatedMixin:
    """Supplies ``updated_at``; composed by 29 master + 8 operational models (§28).

    The eight operational models carrying it are exactly the eight that receive
    UPDATE. The other 16 are append-only, and the absence of this column is the
    schema's statement of immutability, reinforced by the field-level hooks of
    §41.4 rather than by adding a column the database does not have.

    ``server_default`` covers the insert; the ORM-level ``onupdate`` emits
    CURRENT_TIMESTAMP in the SET clause of every UPDATE (§45.4).
    """

    updated_at: Mapped[TimestampTz] = mapped_column(
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
        sort_order=93,
    )

    @validates("updated_at")
    def _reject_naive_updated_at(self, key: str, value: datetime) -> datetime:
        return require_timezone_aware(key, value)


class ComponentProvenanceMixin:
    """Supplies ``created_by_component``; composed by all 24 operational models (§27).

    Provenance, not decoration. NOT NULL with no default, so the writing
    component must state its identity explicitly on every insert. That friction
    carries more weight in SQLite than it would elsewhere: the engine has no
    privilege system to restrict who writes which table, so this column is the
    only thing that makes a write attributable after the fact.

    ``PlatformComponent`` is the only enum any mixin references, and the sole
    reason mixins may import from the enums package (§16 R2).
    """

    created_by_component: Mapped[PlatformComponent] = mapped_column(
        Enum(
            PlatformComponent,
            name="platform_component",
            native_enum=False,
            create_constraint=False,
            values_callable=lambda enum_cls: [m.value for m in enum_cls],
        ),
        sort_order=92,
    )
