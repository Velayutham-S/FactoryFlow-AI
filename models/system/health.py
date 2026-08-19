"""System model O24: component liveness and lag.

One row per component, overwritten in place. Sits in the system group because its
subject is the platform rather than the factory (§9.3 of the schema document).

The only model of the 53 that holds no foreign key at all, master or operational.
"""

from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    Integer,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.system import ComponentHealthStatus, PlatformComponent
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    TimestampUpdatedMixin,
    require_timezone_aware,
)
from models.types import JsonDoc, OperationalPk, TimestampTz


class SystemHealthStatus(TimestampCreatedMixin, ComponentProvenanceMixin,
                         TimestampUpdatedMixin, Base):
    """Table ``system_health_status``, system group: the current liveness, lag and
    error state of each platform component, in exactly one row per component."""

    __tablename__ = "system_health_status"
    __table_args__ = (
        # One row per component for life.
        UniqueConstraint("component", name="uq_shs_component"),
        CheckConstraint("consecutive_failure_count >= 0",
                        name=conv("ck_shs_failure_count_non_negative")),
        CheckConstraint(
            "processing_lag_seconds IS NULL OR processing_lag_seconds >= 0",
            name=conv("ck_shs_lag_non_negative")),
        CheckConstraint(
            "pending_backlog_count IS NULL OR pending_backlog_count >= 0",
            name=conv("ck_shs_backlog_non_negative")),
        # An error timestamp with no message, or a message with no timestamp, is
        # half a record.
        CheckConstraint(
            "(last_error_at IS NULL) = (last_error_message IS NULL)",
            name=conv("ck_shs_error_fields_paired")),
        CheckConstraint(
            "last_successful_run_at IS NULL "
            "OR last_successful_run_at <= last_heartbeat_at",
            name=conv("ck_shs_successful_run_not_after_heartbeat")),
        CheckConstraint(
            "last_error_at IS NULL OR last_error_at <= last_heartbeat_at",
            name=conv("ck_shs_error_not_after_heartbeat")),
        CheckConstraint(
            "metrics_document IS NULL OR (json_valid(metrics_document) "
            "AND json_type(metrics_document) = 'object')",
            name=conv("ck_shs_metrics_document_is_object")),
        CheckConstraint(
            "component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_shs_component_allowed")),
        CheckConstraint(
            "status IN ('healthy', 'degraded', 'failed', 'stopped')",
            name=conv("ck_shs_status_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_shs_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    system_health_status_id: Mapped[OperationalPk]
    component: Mapped[PlatformComponent] = mapped_column(
        Enum(PlatformComponent, name="platform_component", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    status: Mapped[ComponentHealthStatus] = mapped_column(
        Enum(ComponentHealthStatus, name="component_health_status",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Staleness is interpreted by the reader, not stored as a verdict: how long is
    # too long depends on which component it is (§41.1).
    last_heartbeat_at: Mapped[TimestampTz]
    last_successful_run_at: Mapped[Optional[TimestampTz]]
    consecutive_failure_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    processing_lag_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    pending_backlog_count: Mapped[Optional[int]] = mapped_column(Integer)
    last_error_at: Mapped[Optional[TimestampTz]]
    last_error_message: Mapped[Optional[str]] = mapped_column(Text)
    # Component-specific metrics, replaced wholesale on each heartbeat rather than
    # edited in place (§39.6).
    metrics_document: Mapped[Optional[JsonDoc]]

    # No relationships: this model holds no foreign key.

    @validates("component", "last_heartbeat_at", "last_successful_run_at",
               "last_error_at")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """``component`` immutable; the timestamps must be timezone-aware.

        A mutable model: status, heartbeat, counters, error fields and metrics are
        all overwritten. ``component`` is the exception, because the row *is* that
        component's status (§41.3, §41.4).
        """
        if key == "component" and inspect(self).persistent:
            raise ValueError(
                "system_health_status.component is set once at insert and "
                "cannot be reassigned; the row is that component's status"
            )
        if key in ("last_heartbeat_at", "last_successful_run_at",
                   "last_error_at"):
            return require_timezone_aware(key, value)
        return value
