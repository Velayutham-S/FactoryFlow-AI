"""Typed failures for the Notification Service.

**Two kinds of failure, handled two different ways, and the distinction is the whole
design of Group G.**

*Configuration* failures raise. A missing recipient number or provider credential is a
deployment fault: nothing can be attempted, and attempting anyway would record a delivery
that never happened.

*Transport* failures do **not** raise. They are returned and written as a
``notification_delivery`` row carrying one of the six documented ``failure_reason``
values. §E21 exists precisely so that a failure is a recorded fact rather than an
exception -- "an email address bounces, a WhatsApp provider rate-limits, a network times
out. Each is a delivery failure that leaves the notification perfectly composed and
completely useless." Raising instead would also break §E21 rule 7: "A failed delivery must
not silently end the chain."
"""

from __future__ import annotations


class NotificationError(Exception):
    """Base class for every failure raised by the Notification Service."""


class NotificationConfigurationError(NotificationError):
    """The service is not configured well enough to attempt a delivery.

    Raised for an absent ``WHATSAPP_PHONE_NUMBER``, absent provider credentials, or a
    missing transport package. Never continues: a recommendation whose delivery was
    recorded without a provider having been called would make the delivery log fiction.
    """


class MasterDataUnavailableError(NotificationError):
    """Master data the service depends on is missing.

    Recipients, severity levels and shifts all come from master data. §17 makes
    ``notification_recipient`` "the configuration that connects a finished recommendation
    to a specific person", so with none configured there is no routing to perform and the
    service says so rather than reporting a quiet delivery run.
    """


class NotificationPolicyMissingError(NotificationError):
    """A ``business_rule`` the service depends on is absent.

    §29 rule 4: a consumer with no policy "defaulting silently in code would defeat the
    purpose of this entity".
    """


class NotificationTransportError(NotificationError):
    """A transmission attempt failed, raised only when a caller asks for raising.

    The service itself does **not** raise this: :meth:`WhatsAppSender.send` returns a
    structured outcome so the failure becomes a ``notification_delivery`` row, which is
    what §E21 is for and what §E21 rule 7 requires -- "a failed delivery must not silently
    end the chain". This type exists for callers outside the pipeline that would rather
    have an exception than an outcome, and it carries the same mapped vocabulary so
    nothing is lost in the translation.
    """

    def __init__(
        self,
        message: str,
        *,
        status: str | None = None,
        failure_reason: str | None = None,
        provider_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.failure_reason = failure_reason
        self.provider_code = provider_code


class RecipientCoverageError(NotificationError):
    """A critical recommendation would reach nobody.

    §E20 rule 7: "At least one non-suppressed notification must exist for any severity
    whose ``requires_immediate_escalation`` is 1... a critical recommendation must never
    reach nobody." Raised only after the notification rows are committed, so the
    suppression audit survives the failure and the gap is visible rather than inferred.
    """
