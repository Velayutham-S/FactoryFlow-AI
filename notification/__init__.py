"""The Notification Service (Phase 7).

Transition T5, and the last stage of the pipeline: an ``ai_recommendation`` becomes
messages, and the messages become delivery attempts. §16.2 states its one responsibility
as "deliver messages" and what it must never do as "decide content, priority, or
recipients' actions".

**It owns two tables** (§6.2 #20, #21):

``notification``
    One message composed for one recipient, with the decision of whether to send it.
    Append-only. A suppressed recipient still gets a row -- §E20: "without it, 'was Priya
    told?' would be answered by the absence of a row, and absence is ambiguous between
    *deliberately suppressed*, *never composed*, and *lost to a bug*."

``notification_delivery``
    One transmission attempt on one channel, with its outcome. It answers what the
    notification cannot: did the message actually arrive? ``sent`` and ``delivered`` are
    deliberately distinct, "and the gap between them is where silent failures live".

**Its input is the Supervisor's Final Response and nothing else.** No agent is called, no
prediction is made, no decision is taken. A workflow that stopped early produced no
recommendation, and nothing is sent.

.. code-block:: text

    Final Response  ->  qualifying recipients   (master data, not code)
                    ->  T-NOT-1  one notification per recipient, suppressed or not
                    ->  T-NOT-2  one delivery attempt per channel, outside the transaction
                    ->  T-NOT-3  sent -> delivered, on provider confirmation

**WhatsApp first, and the vocabulary already allows more.** ``delivery_channel`` carries
``email`` and ``whatsapp``, so a second channel means adding a sender and enabling it in
``notification_recipient`` -- not touching the composition, routing or recording paths.

Configuration is read from the environment or the project ``.env`` and appears in no source
file: ``WHATSAPP_PHONE_NUMBER`` for the destination, plus the provider credentials
:mod:`notification.whatsapp` documents. A missing value raises
:class:`NotificationConfigurationError` rather than recording a delivery that never
happened.

Usage::

    python -m notification factoryflow.db

or, receiving a Final Response directly::

    from notification import notify
    report = notify("factoryflow.db", final_response)
"""

from notification.compose import (
    ALERT_HEADING,
    HORIZON_LABEL,
    MEASUREMENT_PRECISION,
    PREDICTION_REFERENCE_LABEL,
    PROBABILITY_LABEL,
    WHATSAPP_ACTION_LIMIT,
    Message,
    compose,
    dashboard_view,
    format_duration,
    format_horizon,
    format_measurement,
    format_measurements,
    format_timestamp,
    iso_timestamp,
)
from notification.context import (
    NOTIFICATION_COMPONENT,
    RULE_QUIET_HOURS,
    RULE_RETRY_MAX,
    MasterSnapshot,
    NotificationContext,
    NotificationPolicy,
    Recipient,
)
from notification.errors import (
    MasterDataUnavailableError,
    NotificationConfigurationError,
    NotificationError,
    NotificationPolicyMissingError,
    NotificationTransportError,
    RecipientCoverageError,
)
from notification.notifier import (
    DispatchReport,
    MessageOutcome,
    NotificationService,
    main,
    notify,
)
from notification.recipients import Routing, already_composed, resolve
from notification.whatsapp import (
    CHANNEL,
    ENV_RECIPIENT,
    ENV_RECIPIENT_FALLBACK,
    DeliveryOutcome,
    WhatsAppSender,
    normalise_number,
    read_recipient,
    read_setting,
)

__all__ = [
    # entry points
    "notify",
    "main",
    "NotificationService",
    # results
    "DispatchReport",
    "MessageOutcome",
    "DeliveryOutcome",
    "Message",
    "Routing",
    # routing and composition
    "resolve",
    "already_composed",
    "compose",
    # presentation
    "dashboard_view",
    "format_duration",
    "format_horizon",
    "format_measurement",
    "format_measurements",
    "format_timestamp",
    "iso_timestamp",
    "ALERT_HEADING",
    "HORIZON_LABEL",
    "MEASUREMENT_PRECISION",
    "PREDICTION_REFERENCE_LABEL",
    "PROBABILITY_LABEL",
    "WHATSAPP_ACTION_LIMIT",
    # transport
    "WhatsAppSender",
    "read_setting",
    "read_recipient",
    "normalise_number",
    "CHANNEL",
    "ENV_RECIPIENT",
    "ENV_RECIPIENT_FALLBACK",
    # context and policy
    "NotificationContext",
    "NotificationPolicy",
    "MasterSnapshot",
    "Recipient",
    "NOTIFICATION_COMPONENT",
    "RULE_QUIET_HOURS",
    "RULE_RETRY_MAX",
    # errors
    "NotificationError",
    "NotificationConfigurationError",
    "MasterDataUnavailableError",
    "NotificationPolicyMissingError",
    "NotificationTransportError",
    "RecipientCoverageError",
]
