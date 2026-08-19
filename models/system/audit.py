"""System model O23: the audit trail.

Sits in the system group because its subject is the platform rather than the
factory. That is a documentation and packaging decision only -- all 53 tables share
one database file (§9.3 of the schema document, §13).

``entity_id`` is a deliberate soft reference: the one non-foreign-key row citation
in the database, because an audit entry must be able to name a row in any of the 53
tables and no single foreign key can do that. It depends on AUTOINCREMENT never
recycling an identifier, which is why AUTOINCREMENT is required rather than merely
chosen (§39.3).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.system import AuditActionType, AuditOutcome, PlatformComponent
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    require_timezone_aware,
)
from models.types import JsonDoc, OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.people import Worker


class AuditLog(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``audit_log``, system group: what each component did, when, to what, and
    whether it worked. Append-only.

    The ORM hook is the whole of the immutability guarantee here, and that is stated
    plainly rather than left to be discovered. SQLite has no roles and no GRANT, so
    there is no way to make the table physically insert-only for one writer: any
    process holding the file can update or delete any row. The hook makes accidental
    mutation impossible and deliberate mutation easy, and for a single-operator
    deployment where the adversary is a bug rather than a person, that is
    sufficient. The real access boundary is filesystem permissions on the file
    (§41.4).
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "outcome <> 'failure' OR error_message IS NOT NULL",
            name=conv("ck_al_failure_requires_message")),
        CheckConstraint("length(trim(correlation_id)) > 0",
                        name=conv("ck_al_correlation_id_not_blank")),
        # A bare identifier with no table name is unresolvable, so the two are
        # required together.
        CheckConstraint(
            "entity_id IS NULL OR entity_name IS NOT NULL",
            name=conv("ck_al_entity_reference_paired")),
        CheckConstraint(
            "action_detail IS NULL OR (json_valid(action_detail) "
            "AND json_type(action_detail) = 'object')",
            name=conv("ck_al_action_detail_is_object")),
        CheckConstraint(
            "component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_al_component_allowed")),
        CheckConstraint(
            "action_type IN ('entity_created', 'entity_updated', "
            "'state_transition', 'decision_made', 'human_action', "
            "'configuration_changed', 'component_error', 'retention_purge', "
            "'reconciliation_run')",
            name=conv("ck_al_action_type_allowed")),
        CheckConstraint("outcome IN ('success', 'failure', 'denied')",
                        name=conv("ck_al_outcome_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_al_created_by_component_allowed")),
        CheckConstraint(
            "entity_name IS NULL OR length(entity_name) <= 60",
            name=conv("ck_al_entity_name_length")),
        CheckConstraint(
            "entity_code IS NULL OR length(entity_code) <= 32",
            name=conv("ck_al_entity_code_length")),
        CheckConstraint("length(correlation_id) <= 40",
                        name=conv("ck_al_correlation_id_length")),
        {"sqlite_autoincrement": True},
    )

    audit_log_id: Mapped[OperationalPk]
    occurred_at: Mapped[TimestampTz]
    # Which component acted. A second binding of PlatformComponent alongside the
    # mixin's created_by_component, and a different fact: the actor versus the
    # writer of the row.
    component: Mapped[PlatformComponent] = mapped_column(
        Enum(PlatformComponent, name="platform_component", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    action_type: Mapped[AuditActionType] = mapped_column(
        Enum(AuditActionType, name="audit_action_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    entity_name: Mapped[Optional[str]] = mapped_column(String(60))
    # A soft reference, not a foreign key: no single key can point at any of 53
    # tables. Its correctness depends on identifiers never being recycled.
    entity_id: Mapped[Optional[int]] = mapped_column(Integer)
    entity_code: Mapped[Optional[str]] = mapped_column(String(32))
    actor_worker_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_al_actor_worker",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # str because the schema types it as text; the ORM does not reinterpret it as
    # a UUID (§38.1).
    correlation_id: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[AuditOutcome] = mapped_column(
        Enum(AuditOutcome, name="audit_outcome", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Before and after values for a configuration change.
    action_detail: Mapped[Optional[JsonDoc]]
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    # Named for the role, not the target (§45.1). Unidirectional:
    # Worker.audit_log_entries would be ~730,000 rows a year against ~13 worker
    # rows, so it is not mapped (§19.1).
    #
    # raise_on_sql under rule L4: audit_log is one of the four highest-volume
    # models, read in bulk inside loops, and an N+1 here should be a loud failure
    # in development rather than a slow afternoon in production (§19.4).
    actor: Mapped[Optional["Worker"]] = relationship(lazy="raise_on_sql")

    @validates("occurred_at", "component", "action_type", "entity_name",
               "entity_id", "entity_code", "actor_worker_id", "correlation_id",
               "outcome", "action_detail", "error_message", "actor")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``occurred_at`` timezone-aware (§41.3, §41.4).

        This is the one table whose value depends entirely on not having been
        edited.
        """
        if inspect(self).persistent:
            raise ValueError(
                "audit_log is append-only; %s cannot be reassigned once the row "
                "is persistent" % key
            )
        if key == "occurred_at":
            return require_timezone_aware(key, value)
        return value
