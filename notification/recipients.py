"""Who qualifies, who is suppressed, and why. Deterministic and master-data-driven.

**Scope excludes; everything else suppresses.** §E20 rule 2 says recipients "qualify by
``min_severity_level_id`` and ``scope_production_line_id``", and rule 1 writes one row
"per qualifying recipient". The suppression vocabulary -- ``quiet_hours``,
``rate_limited``, ``below_min_severity``, ``recipient_inactive``, ``channel_unavailable``,
``already_acknowledged`` -- contains **no value for a wrong-line recipient**, and that
absence is the answer: a Line 02 supervisor is not in Line 01's chain at all, so no row is
written for them. A severity floor is different: it has its own reason value, and §E20's
worked example writes a suppressed row for the plant manager whose ``SEV-1`` floor was not
met. Both readings follow the vocabulary rather than working around it.

**Quiet hours has two conditions, not one.** §E20 rule 4: it "applies only when
``notification_recipient.notify_outside_shift_hours = 0`` **and** the severity does not
have ``requires_immediate_escalation``. A critical condition overrides quiet hours --
master data's severity policy takes precedence over recipient preference."

**The order of the checks is the order of the reasons.** A recipient can fail several at
once, and the one recorded is the most specific: never configured for this channel, then
already acknowledged, then below the floor, then quiet hours, then rate limited. Recording
``rate_limited`` for someone whose severity floor was never met would misdirect whoever
later tunes the rate limits.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.master import FailureSeverityLevel, Machine
from models.operational import Notification, RecommendationAction

from notification.context import MasterSnapshot, NotificationPolicy, Recipient

RATE_LIMIT_WINDOW = timedelta(hours=1)

# The channels this service can transmit on. WhatsApp first; §E21's vocabulary already
# carries `email`, so adding it later means adding a sender, not touching this module.
DELIVERABLE_CHANNELS = ("whatsapp",)


@dataclass(frozen=True)
class Routing:
    """One recipient's outcome: notify, or suppress with a recorded reason."""

    recipient: Recipient
    suppressed: bool
    reason: str | None
    channels: tuple[str, ...]

    @property
    def will_send(self) -> bool:
        return not self.suppressed and bool(self.channels)


def resolve(
    session: Session,
    master: MasterSnapshot,
    policy: NotificationPolicy,
    *,
    machine: Machine,
    severity: FailureSeverityLevel,
    composed_at: datetime,
    ai_recommendation_id: int,
) -> list[Routing]:
    """Every qualifying recipient for one recommendation, in escalation order."""
    acknowledged = _already_acknowledged(session, ai_recommendation_id)
    routings: list[Routing] = []

    for recipient in master.recipients:
        # Scope is qualification, not suppression. Out-of-scope recipients get no row.
        if (
            recipient.scope_production_line_id is not None
            and recipient.scope_production_line_id != machine.production_line_id
        ):
            continue

        channels = _channels_for(recipient)
        reason = _suppression_reason(
            session, master, policy, recipient, severity,
            composed_at=composed_at,
            channels=channels,
            acknowledged=acknowledged,
        )
        routings.append(Routing(
            recipient=recipient,
            suppressed=reason is not None,
            reason=reason,
            channels=channels,
        ))
    return routings


def _channels_for(recipient: Recipient) -> tuple[str, ...]:
    """The channels enabled for this recipient that the service can actually use.

    §E21 rule 2 is the gate: the channel "must be enabled for the recipient in master
    data" -- ``whatsapp_enabled`` on ``notification_recipient``. A recipient with the
    channel switched off has no usable channel and is suppressed as
    ``channel_unavailable``.

    **``worker.phone_number`` is deliberately not part of this gate**, and the reason is
    worth stating. §17 rule 3 expects an enabled channel to have its matching endpoint
    populated, but this implementation dials the project-level ``WHATSAPP_PHONE_NUMBER``
    from the environment rather than a per-worker number, so a stored phone number is
    never the destination. Gating on a field the transport does not read would block
    delivery for a reason that has no bearing on whether the message can be sent.
    :attr:`Recipient.has_phone` is still resolved and reported, because a roster with
    channels enabled and no endpoints recorded is a master-data observation worth
    surfacing rather than hiding behind a suppression.
    """
    channels: list[str] = []
    if "whatsapp" in DELIVERABLE_CHANNELS and recipient.whatsapp_enabled:
        channels.append("whatsapp")
    return tuple(channels)


def _suppression_reason(
    session: Session,
    master: MasterSnapshot,
    policy: NotificationPolicy,
    recipient: Recipient,
    severity: FailureSeverityLevel,
    *,
    composed_at: datetime,
    channels: tuple[str, ...],
    acknowledged: bool,
) -> str | None:
    """The most specific reason this recipient is not transmitted to, or ``None``."""
    if not channels:
        return "channel_unavailable"

    # §E20 rule 8: once a human has responded, further escalation is noise.
    if acknowledged:
        return "already_acknowledged"

    # A lower rank is more severe, so the alert must be at or above their floor.
    if severity.severity_rank > recipient.min_severity_rank:
        return "below_min_severity"

    # §E20 rule 4, both halves.
    if (
        policy.quiet_hours_enabled
        and not recipient.notify_outside_shift_hours
        and not severity.requires_immediate_escalation
        and not master.is_on_shift(recipient, composed_at)
    ):
        return "quiet_hours"

    # §E20 rule 5.
    if recipient.max_notifications_per_hour is not None:
        sent = _sent_within_hour(session, recipient.recipient_id, composed_at)
        if sent >= recipient.max_notifications_per_hour:
            return "rate_limited"

    return None


def _already_acknowledged(session: Session, ai_recommendation_id: int) -> bool:
    """Whether a human has already recorded a decision on this recommendation."""
    return session.scalars(
        select(RecommendationAction.recommendation_action_id).where(
            RecommendationAction.ai_recommendation_id == ai_recommendation_id
        ).limit(1)
    ).first() is not None


def _sent_within_hour(
    session: Session,
    recipient_id: int,
    composed_at: datetime,
) -> int:
    """Transmitted notifications for this recipient in the trailing hour.

    Suppressed rows do not count: they were never transmitted, and counting them would
    let a rate limit exhaust itself on messages nobody received.
    """
    return int(session.execute(
        select(func.count()).select_from(Notification).where(
            Notification.notification_recipient_id == recipient_id,
            Notification.composed_at > composed_at - RATE_LIMIT_WINDOW,
            Notification.composed_at <= composed_at,
            Notification.is_suppressed.is_(False),
        )
    ).scalar_one())


def already_composed(
    session: Session,
    ai_recommendation_id: int,
    recipient_id: int,
) -> bool:
    """Whether this recipient already has a notification for this recommendation.

    §E20 rule 1 -- "One notification per qualifying recipient per triggering event" -- is
    the deduplication mechanism the documentation already provides. It is a query rather
    than a unique index because the schema declares none, and inventing one would mean
    altering a frozen table. The existing identifiers are enough:
    ``ai_recommendation_id`` and ``notification_recipient_id`` together identify the
    message that must not be composed twice.
    """
    return session.scalars(
        select(Notification.notification_id).where(
            Notification.ai_recommendation_id == ai_recommendation_id,
            Notification.notification_recipient_id == recipient_id,
        ).limit(1)
    ).first() is not None
