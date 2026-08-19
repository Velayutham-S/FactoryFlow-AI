"""Operational models O20-O21: the composed message, and the transmission attempt.

Suppression is recorded as a row, not as an absence. Without that, "was the
supervisor told?" would be answered by the absence of a row, and absence is
ambiguous between deliberately suppressed, never composed, and lost to a bug.
Suppression stops transmission, never recording (§O20).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import (
    DeliveryChannel,
    DeliveryFailureReason,
    DeliveryStatus,
    NotificationSuppressionReason,
    NotificationType,
)
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    TimestampUpdatedMixin,
    require_timezone_aware,
)
from models.types import OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.failure import FailureSeverityLevel
    from models.master.people import NotificationRecipient
    from models.master.plant import Shift
    from models.operational.decision import AiRecommendation
    from models.operational.events import OperationalAlert


class Notification(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``notification``, operational group: one message composed for one
    recipient, with the decision of whether to send it. Append-only.

    The message body is stored, not regenerated: what the recipient actually saw is
    part of the audit trail, and regenerating it later from the recommendation would
    produce current wording against a past decision (§O20).
    """

    __tablename__ = "notification"
    __table_args__ = (
        UniqueConstraint("notification_code", name="uq_nt_code"),
        CheckConstraint(
            "notification_code GLOB "
            "'NTF-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9][0-9]'",
            name=conv("ck_nt_code_format")),
        CheckConstraint(
            "notification_type <> 'recommendation' "
            "OR ai_recommendation_id IS NOT NULL",
            name=conv("ck_nt_recommendation_required")),
        # The next two make suppression_reason present exactly when suppressed.
        CheckConstraint(
            "is_suppressed = 0 OR suppression_reason IS NOT NULL",
            name=conv("ck_nt_suppression_reason_required")),
        CheckConstraint(
            "is_suppressed = 1 OR suppression_reason IS NULL",
            name=conv("ck_nt_suppression_reason_absent")),
        CheckConstraint(
            "requires_acknowledgement = 0 "
            "OR acknowledgement_deadline_at IS NOT NULL",
            name=conv("ck_nt_ack_deadline_required")),
        CheckConstraint(
            "acknowledgement_deadline_at IS NULL "
            "OR acknowledgement_deadline_at > composed_at",
            name=conv("ck_nt_ack_deadline_after_composed")),
        CheckConstraint("escalation_order_applied > 0",
                        name=conv("ck_nt_escalation_order_positive")),
        CheckConstraint("length(trim(subject)) > 0",
                        name=conv("ck_nt_subject_not_blank")),
        CheckConstraint("length(trim(body_text)) > 0",
                        name=conv("ck_nt_body_not_blank")),
        CheckConstraint(
            "notification_type IN ('recommendation', 'alert_escalation', "
            "'acknowledgement_reminder', 'inventory_warning', "
            "'system_health')",
            name=conv("ck_nt_notification_type_allowed")),
        CheckConstraint(
            "suppression_reason IS NULL OR suppression_reason IN "
            "('quiet_hours', 'rate_limited', 'below_min_severity', "
            "'recipient_inactive', 'channel_unavailable', "
            "'already_acknowledged')",
            name=conv("ck_nt_suppression_reason_allowed")),
        CheckConstraint("is_suppressed IN (0, 1)",
                        name=conv("ck_nt_is_suppressed_bool")),
        CheckConstraint("requires_acknowledgement IN (0, 1)",
                        name=conv("ck_nt_requires_acknowledgement_bool")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_nt_created_by_component_allowed")),
        CheckConstraint("length(notification_code) <= 22",
                        name=conv("ck_nt_notification_code_length")),
        CheckConstraint("length(subject) <= 200",
                        name=conv("ck_nt_subject_length")),
        {"sqlite_autoincrement": True},
    )

    notification_id: Mapped[OperationalPk]
    notification_code: Mapped[str] = mapped_column(String(22))
    # Stores no contact details: endpoints resolve through the recipient to
    # worker.
    notification_recipient_id: Mapped[int] = mapped_column(
        ForeignKey("notification_recipient.notification_recipient_id",
                   name="fk_nt_recipient",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    notification_type: Mapped[NotificationType] = mapped_column(
        Enum(NotificationType, name="notification_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    ai_recommendation_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("ai_recommendation.ai_recommendation_id",
                   name="fk_nt_recommendation",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    operational_alert_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("operational_alert.operational_alert_id",
                   name="fk_nt_alert",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_nt_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    composed_at: Mapped[TimestampTz]
    # Carries severity, machine and deadline: many recipients decide whether to
    # open from this line alone.
    subject: Mapped[str] = mapped_column(String(200))
    # The message as sent. Stored, not regenerated.
    body_text: Mapped[str] = mapped_column(Text)
    # Suppressed rows are still recorded and still displayed on the dashboard.
    is_suppressed: Mapped[bool] = mapped_column(server_default=text("0"))
    suppression_reason: Mapped[Optional[NotificationSuppressionReason]] = (
        mapped_column(
            Enum(NotificationSuppressionReason,
                 name="notification_suppression_reason", native_enum=False,
                 create_constraint=False,
                 values_callable=lambda enum_cls: [m.value for m in enum_cls])
        )
    )
    requires_acknowledgement: Mapped[bool]
    # The escalation clock. Resolved and stored at composition from the severity's
    # max_acknowledgement_minutes, because a clock whose deadline is recomputed on
    # every check is a clock that can drift (§O20).
    acknowledgement_deadline_at: Mapped[Optional[TimestampTz]]
    escalation_order_applied: Mapped[int] = mapped_column(Integer)
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_nt_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # Four of the five many-to-one relationships are unidirectional (§O20).
    notification_recipient: Mapped["NotificationRecipient"] = relationship(
        lazy="select"
    )
    ai_recommendation: Mapped[Optional["AiRecommendation"]] = relationship(
        back_populates="notifications", lazy="select"
    )
    operational_alert: Mapped[Optional["OperationalAlert"]] = relationship(
        lazy="select"
    )
    severity_level: Mapped["FailureSeverityLevel"] = relationship(lazy="select")
    shift: Mapped["Shift"] = relationship(lazy="select")
    deliveries: Mapped[list["NotificationDelivery"]] = relationship(
        back_populates="notification", lazy="select"
    )

    @validates("notification_code", "notification_recipient_id",
               "notification_type", "ai_recommendation_id",
               "operational_alert_id", "severity_level_id", "composed_at",
               "subject", "body_text", "is_suppressed", "suppression_reason",
               "requires_acknowledgement", "acknowledgement_deadline_at",
               "escalation_order_applied", "shift_id",
               "notification_recipient", "ai_recommendation",
               "operational_alert", "severity_level", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and both timestamps timezone-aware (§41.3, §41.4).

        Delivery state lives on the child, which is why this model needs no
        ``updated_at``.
        """
        if inspect(self).persistent:
            raise ValueError(
                "notification is append-only; %s cannot be reassigned once the "
                "row is persistent" % key
            )
        if key in ("composed_at", "acknowledgement_deadline_at"):
            return require_timezone_aware(key, value)
        if key == "notification_code":
            return value.strip().upper()
        return value


class NotificationDelivery(TimestampCreatedMixin, ComponentProvenanceMixin,
                           TimestampUpdatedMixin, Base):
    """Table ``notification_delivery``, operational group: one transmission attempt on
    one channel, with its outcome. Answers what the notification cannot: did the
    message actually arrive?

    ``delivery_status`` distinguishes ``sent`` -- the platform handed the message to
    a provider -- from ``delivered`` -- the provider confirmed receipt. The gap
    between them is where silent failures live, and a platform recording only
    ``sent`` will believe every message arrived.

    The only model in the 53 with no master data reference at all: its subject is
    entirely a transport concern and the recipient is reached through the
    notification (§O21).
    """

    __tablename__ = "notification_delivery"
    __table_args__ = (
        # Retries are distinguishable per channel, which makes attempt recording
        # idempotent.
        UniqueConstraint("notification_id", "channel", "attempt_number",
                         name="uq_nd_notification_channel_attempt"),
        CheckConstraint("attempt_number > 0",
                        name=conv("ck_nd_attempt_number_positive")),
        CheckConstraint(
            "delivery_status <> 'delivered' OR delivered_at IS NOT NULL",
            name=conv("ck_nd_delivered_requires_timestamp")),
        CheckConstraint(
            "delivered_at IS NULL OR delivered_at >= attempted_at",
            name=conv("ck_nd_delivered_at_not_before_attempt")),
        CheckConstraint(
            "delivery_status NOT IN ('failed', 'bounced', 'rejected') "
            "OR failure_reason IS NOT NULL",
            name=conv("ck_nd_failure_reason_required")),
        CheckConstraint(
            "delivery_status NOT IN ('delivered', 'sent', 'queued') "
            "OR failure_reason IS NULL",
            name=conv("ck_nd_failure_reason_absent_on_success")),
        CheckConstraint("latency_ms IS NULL OR latency_ms >= 0",
                        name=conv("ck_nd_latency_non_negative")),
        CheckConstraint("channel IN ('email', 'whatsapp')",
                        name=conv("ck_nd_channel_allowed")),
        CheckConstraint(
            "delivery_status IN ('queued', 'sent', 'delivered', 'failed', "
            "'bounced', 'rejected')",
            name=conv("ck_nd_delivery_status_allowed")),
        CheckConstraint(
            "failure_reason IS NULL OR failure_reason IN ('invalid_address', "
            "'provider_error', 'timeout', 'rate_limited_by_provider', "
            "'recipient_blocked', 'message_too_large')",
            name=conv("ck_nd_failure_reason_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_nd_created_by_component_allowed")),
        CheckConstraint(
            "provider_reference IS NULL OR length(provider_reference) <= 120",
            name=conv("ck_nd_provider_reference_length")),
        {"sqlite_autoincrement": True},
    )

    notification_delivery_id: Mapped[OperationalPk]
    # The only foreign key on this model.
    notification_id: Mapped[int] = mapped_column(
        ForeignKey("notification.notification_id", name="fk_nd_notification",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    channel: Mapped[DeliveryChannel] = mapped_column(
        Enum(DeliveryChannel, name="delivery_channel", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    attempt_number: Mapped[int] = mapped_column(
        Integer, server_default=text("1")
    )
    attempted_at: Mapped[TimestampTz]
    delivery_status: Mapped[DeliveryStatus] = mapped_column(
        Enum(DeliveryStatus, name="delivery_status", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    delivered_at: Mapped[Optional[TimestampTz]]
    # Needed to investigate a disputed delivery.
    provider_reference: Mapped[Optional[str]] = mapped_column(String(120))
    # Enumerated so failures aggregate: invalid_address points at stale master
    # data, provider_error at infrastructure, rate_limited_by_provider at volume.
    # A recurring invalid_address is a master data quality problem, not a delivery
    # problem -- the one place an operational signal points back at a master
    # defect (§O21).
    failure_reason: Mapped[Optional[DeliveryFailureReason]] = mapped_column(
        Enum(DeliveryFailureReason, name="delivery_failure_reason",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    failure_detail: Mapped[Optional[str]] = mapped_column(Text)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)

    notification: Mapped["Notification"] = relationship(
        back_populates="deliveries", lazy="select"
    )

    @validates("notification_id", "channel", "attempt_number", "attempted_at",
               "provider_reference", "failure_reason", "failure_detail",
               "notification", "delivered_at")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Narrow mutability plus timezone-awareness (§41.3, §41.4).

        Everything except ``delivery_status``, ``delivered_at`` and ``latency_ms``
        is immutable after insert. The permitted mutation is exactly one
        transition -- ``sent`` to ``delivered`` when the provider confirms
        asynchronously -- and it is the single documented mutation in the
        notification group, which is why this model carries ``updated_at`` while
        ``Notification`` does not.

        The retry policy is a business rule the Notification Service owns:
        ``failed`` and ``timeout`` are transient and retryable, while ``bounced``,
        ``rejected`` and ``invalid_address`` are permanent and must not be
        retried, because retrying a bounced address only generates more bounces.
        """
        if key != "delivered_at" and inspect(self).persistent:
            raise ValueError(
                "notification_delivery.%s is immutable after insert; only "
                "delivery_status, delivered_at and latency_ms may change" % key
            )
        if key in ("attempted_at", "delivered_at"):
            return require_timezone_aware(key, value)
        return value
