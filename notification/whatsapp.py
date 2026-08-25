"""The WhatsApp transport. The only part of the service that talks to the outside world.

**Configuration comes from the environment, never from source.** The same approach the
Decision Agent uses for ``GROQ_API_KEY``: process environment first, then the project
``.env`` through ``python-dotenv``, then a clear configuration error. The loader is written
here rather than imported because the Decision Agent's binds a single hardcoded key name;
the shape is deliberately identical so there is one configuration convention in the
project, not two.

================================= ========= ===============================================
``WHATSAPP_RECIPIENT_NUMBER``     required  destination, E.164 digits; falls back to
                                            ``WHATSAPP_PHONE_NUMBER``
``WHATSAPP_ACCESS_TOKEN``         required  provider credential
``WHATSAPP_PHONE_NUMBER_ID``      required  the sending number's provider identifier
``WHATSAPP_API_BASE``             optional  defaults to the Meta Cloud API endpoint
``WHATSAPP_DEFAULT_COUNTRY``      optional  dialling code prepended to a local number
``WHATSAPP_TIMEOUT_SECONDS``      optional  defaults to 30
================================= ========= ===============================================

No value is ever logged, echoed, or stored. The token appears only in an ``Authorization``
header and is never included in ``failure_detail``. ``provider_reference`` holds Meta's
message id, which is not a secret and which §E21 rule 9 requires "whenever the provider
supplies one, because a disputed delivery cannot be investigated without it".

**A send returns an outcome; it does not raise.** Every transport result maps onto the
documented ``delivery_status`` and ``failure_reason`` vocabularies so that failures
aggregate as §E21 intends -- "``invalid_address`` points at stale master data,
``provider_error`` at infrastructure, ``rate_limited_by_provider`` at message volume. Each
has a different fix, and free-text failures would be unaggregatable." Only configuration
faults raise, because nothing can be attempted and recording an attempt would be fiction.
:class:`NotificationTransportError` exists for callers that want raising semantics and is
produced by :meth:`WhatsAppSender.send_or_raise`.

**Meta's own error codes are mapped, not just the HTTP status.** The Cloud API returns 400
for several unrelated conditions and distinguishes them in the body's ``error.code``: 131026
is an undeliverable recipient, 130429 is a throughput limit, 190 is an expired token. Those
map to three different documented reasons and three different fixes, so the body is read
before the status is used as a fallback.

**A successful call yields ``sent``, never ``delivered``.** The Cloud API acknowledges
acceptance, not receipt. §E21 is emphatic: "``sent`` means the platform handed the message
to a provider. ``delivered`` means the provider confirmed receipt. The gap between them is
where silent failures live, and a platform that only records ``sent`` will believe every
message arrived." Confirmation arrives later, by webhook, and is applied through
:meth:`NotificationService.confirm_delivery`.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from notification.errors import (
    NotificationConfigurationError,
    NotificationTransportError,
)

CHANNEL = "whatsapp"

# The brief's name first, the name already in the project's .env second. Both are
# accepted so an existing deployment keeps working and a new one can use either.
ENV_RECIPIENT = "WHATSAPP_RECIPIENT_NUMBER"
ENV_RECIPIENT_FALLBACK = "WHATSAPP_PHONE_NUMBER"
ENV_TOKEN = "WHATSAPP_ACCESS_TOKEN"
ENV_SENDER_ID = "WHATSAPP_PHONE_NUMBER_ID"
ENV_API_BASE = "WHATSAPP_API_BASE"
ENV_COUNTRY = "WHATSAPP_DEFAULT_COUNTRY"
ENV_TIMEOUT = "WHATSAPP_TIMEOUT_SECONDS"

DEFAULT_API_BASE = "https://graph.facebook.com/v21.0"
DEFAULT_TIMEOUT_SECONDS = 30

# WhatsApp rejects a text body above 4096 characters. Checked before the call so the
# documented `message_too_large` reason is recorded rather than a generic provider error.
MAX_BODY_CHARACTERS = 4096

# Meta Cloud API error codes worth distinguishing, mapped onto the two frozen
# vocabularies. Anything absent falls through to the HTTP status mapping.
META_ERROR_MAP: dict[int, tuple[str, str]] = {
    190: ("rejected", "provider_error"),            # access token expired or invalid
    102: ("rejected", "provider_error"),            # session invalidated
    131026: ("bounced", "invalid_address"),         # message undeliverable
    131030: ("bounced", "invalid_address"),         # recipient not in allowed list
    133010: ("rejected", "provider_error"),         # sending number not registered
    131031: ("rejected", "provider_error"),         # account locked
    131047: ("rejected", "provider_error"),         # re-engagement window closed
    131051: ("rejected", "message_too_large"),      # unsupported message type
    130429: ("failed", "rate_limited_by_provider"),  # throughput limit
    131048: ("failed", "rate_limited_by_provider"),  # spam rate limit
    80007: ("failed", "rate_limited_by_provider"),   # business rate limit
    131053: ("rejected", "provider_error"),         # media upload error
    368: ("rejected", "recipient_blocked"),         # temporarily blocked for policy
}


@dataclass(frozen=True)
class DeliveryOutcome:
    """One transmission attempt's result, in the vocabulary §E21 defines."""

    status: str
    provider_reference: str | None = None
    failure_reason: str | None = None
    failure_detail: str | None = None
    latency_ms: int = 0
    delivered_at: datetime | None = None
    provider_code: int | None = None

    @property
    def succeeded(self) -> bool:
        return self.status in ("sent", "delivered")

    @property
    def retryable(self) -> bool:
        """§E21 rule 4: only ``failed`` and ``timeout`` may be retried.

        "``bounced``, ``rejected``, and ``invalid_address`` are **permanent** and must not
        be retried -- retrying a bounced address only generates more bounces."
        """
        if self.status in ("bounced", "rejected"):
            return False
        if self.failure_reason in ("invalid_address", "recipient_blocked",
                                   "message_too_large"):
            return False
        return self.status == "failed"


def read_setting(
    name: str,
    *,
    env_path: str | Path | None = None,
    required: bool = True,
) -> str | None:
    """One configuration value, from the environment or the project ``.env``.

    Whitespace around both the name and the value is tolerated, because ``KEY = value``
    is a natural thing to write in a ``.env`` file and silently failing to find a key
    that is plainly present would be the worst kind of configuration bug.
    """
    value = os.environ.get(name, "").strip()
    if value:
        return value

    candidate = Path(env_path) if env_path is not None else Path.cwd() / ".env"
    if candidate.is_file():
        try:
            from dotenv import dotenv_values

            values = dict(dotenv_values(candidate))
        except ImportError:  # pragma: no cover - dotenv ships with the project
            values = _parse_env(candidate)
        # dotenv strips a padded key, but normalise anyway so both readers agree.
        normalised = {
            str(key).strip(): ("" if item is None else str(item).strip())
            for key, item in values.items()
        }
        value = normalised.get(name, "")
        if value:
            return value

    if not required:
        return None
    raise NotificationConfigurationError(
        "%s is not set. The Notification Service reads it from the process environment "
        "or from %s, and never from source. Add a line reading '%s=<value>' to that "
        "file, or export it, then run again." % (name, candidate, name)
    )


def _parse_env(path: Path) -> dict[str, str]:
    """Minimal ``.env`` reader, used only if python-dotenv is unavailable."""
    found: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        found[key.strip()] = value.strip().strip('"').strip("'")
    return found


def read_recipient(env_path: str | Path | None = None) -> str:
    """The destination, under either accepted variable name."""
    for name in (ENV_RECIPIENT, ENV_RECIPIENT_FALLBACK):
        value = read_setting(name, env_path=env_path, required=False)
        if value:
            return value
    raise NotificationConfigurationError(
        "neither %s nor %s is set, so there is no destination to send to. The "
        "Notification Service reads them from the process environment or from the "
        "project .env, and never from source." % (ENV_RECIPIENT, ENV_RECIPIENT_FALLBACK)
    )


def normalise_number(raw: str, default_country: str | None) -> str:
    """A destination in the digits-only E.164 form the Cloud API expects.

    A number configured without a dialling code is completed from
    ``WHATSAPP_DEFAULT_COUNTRY`` when one is set. Left as given otherwise, so a wrong
    number surfaces as the provider's own ``invalid_address`` rather than being silently
    rewritten into a different valid number.
    """
    digits = "".join(character for character in raw if character.isdigit())
    if not digits:
        raise NotificationConfigurationError(
            "%s contains no digits, so there is no destination to send to."
            % ENV_RECIPIENT
        )
    if default_country:
        code = "".join(c for c in default_country if c.isdigit())
        if code and not digits.startswith(code) and len(digits) <= 10:
            digits = code + digits
    return digits


class WhatsAppSender:
    """Sends one message through the Meta WhatsApp Cloud API.

    Configuration is resolved eagerly so a deployment fault surfaces before any
    notification row is written, and the destination is resolved once and reused. One
    HTTP client is constructed per send and closed with it: the service sends a handful
    of messages per day, so a pooled long-lived client would hold a socket open for hours
    to save nothing measurable.
    """

    channel = CHANNEL

    def __init__(
        self,
        *,
        env_path: str | Path | None = None,
        timeout_seconds: int | None = None,
    ) -> None:
        self.recipient_number = normalise_number(
            read_recipient(env_path),
            read_setting(ENV_COUNTRY, env_path=env_path, required=False),
        )
        self._token = read_setting(ENV_TOKEN, env_path=env_path)
        self._sender_id = read_setting(ENV_SENDER_ID, env_path=env_path)
        base = read_setting(
            ENV_API_BASE, env_path=env_path, required=False) or DEFAULT_API_BASE
        self.endpoint = "%s/%s/messages" % (base.rstrip("/"), self._sender_id)
        configured = read_setting(ENV_TIMEOUT, env_path=env_path, required=False)
        self.timeout_seconds = (
            timeout_seconds if timeout_seconds is not None
            else int(configured) if configured and configured.isdigit()
            else DEFAULT_TIMEOUT_SECONDS
        )

    @property
    def masked_recipient(self) -> str:
        """The destination with all but the last four digits hidden, for reporting."""
        digits = self.recipient_number
        return "*" * max(len(digits) - 4, 0) + digits[-4:]

    def payload_for(self, subject: str, body: str) -> dict[str, Any]:
        """The Cloud API request body. Separated so it can be inspected and asserted."""
        return {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": self.recipient_number,
            "type": "text",
            "text": {"preview_url": False, "body": self.render(subject, body)},
        }

    @staticmethod
    def render(subject: str, body: str) -> str:
        """Subject and body as one WhatsApp text, the subject emphasised."""
        return body if not subject else "*%s*\n\n%s" % (subject, body)

    def send(self, subject: str, body: str) -> DeliveryOutcome:
        """Attempt one transmission and describe what happened.

        Never raises for a transport condition: the outcome becomes a
        ``notification_delivery`` row, which is the whole point of §E21. A configuration
        fault has already been raised in ``__init__``, before any row existed.
        """
        text = self.render(subject, body)
        if len(text) > MAX_BODY_CHARACTERS:
            return DeliveryOutcome(
                status="rejected",
                failure_reason="message_too_large",
                failure_detail="message is %d characters; the channel accepts %d"
                               % (len(text), MAX_BODY_CHARACTERS),
            )

        try:
            import httpx
        except ImportError as exc:  # pragma: no cover - httpx ships with the project
            raise NotificationConfigurationError(
                "the 'httpx' package is not installed, so the Notification Service "
                "cannot reach the WhatsApp Cloud API. Install it and run again."
            ) from exc

        payload = self.payload_for(subject, body)
        headers = {
            "Authorization": "Bearer %s" % self._token,
            "Content-Type": "application/json",
        }

        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(self.endpoint, json=payload, headers=headers)
        except httpx.TimeoutException:
            return DeliveryOutcome(
                status="failed",
                failure_reason="timeout",
                failure_detail="no response within %d seconds" % self.timeout_seconds,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        except httpx.RequestError as error:
            return DeliveryOutcome(
                status="failed",
                failure_reason="provider_error",
                failure_detail="network failure: %s" % type(error).__name__,
                latency_ms=int((time.perf_counter() - started) * 1000),
            )
        elapsed = int((time.perf_counter() - started) * 1000)

        if response.is_success:
            return DeliveryOutcome(
                status="sent",
                provider_reference=_message_id(response.text),
                latency_ms=elapsed,
            )

        code, message = _meta_error(response.text)
        status, reason = _map_failure(response.status_code, code, message)
        return DeliveryOutcome(
            status=status,
            failure_reason=reason,
            failure_detail=_detail(response.status_code, code, message),
            latency_ms=elapsed,
            provider_code=code,
        )

    def send_or_raise(self, subject: str, body: str) -> DeliveryOutcome:
        """As :meth:`send`, but raising :class:`NotificationTransportError` on failure.

        Not used by the pipeline -- the pipeline records failures rather than raising, so
        that §E21 rule 7's escalation continues. Provided for callers outside it.
        """
        outcome = self.send(subject, body)
        if outcome.succeeded:
            return outcome
        raise NotificationTransportError(
            outcome.failure_detail or "the WhatsApp transport failed",
            status=outcome.status,
            failure_reason=outcome.failure_reason,
            provider_code=outcome.provider_code,
        )


def _map_failure(
    http_status: int,
    provider_code: int | None,
    message: str,
) -> tuple[str, str]:
    """Map a failed call onto the two documented vocabularies.

    Meta's own ``error.code`` is preferred because the Cloud API returns HTTP 400 for
    conditions with entirely different fixes. The HTTP status is the fallback.

    Authentication failure becomes ``provider_error`` rather than a reason of its own: the
    six ``failure_reason`` values are fixed by ``ck_nd_failure_reason_allowed`` and none
    names authentication, so the reason stays inside the vocabulary and the specifics go
    to ``failure_detail`` where they are readable but not aggregated wrongly.
    """
    if provider_code is not None and provider_code in META_ERROR_MAP:
        return META_ERROR_MAP[provider_code]
    return _map_http_status(http_status, message)


def _map_http_status(code: int, detail: str) -> tuple[str, str]:
    """Fallback mapping, used when Meta supplies no recognised error code."""
    lowered = (detail or "").lower()
    if code == 429:
        return "failed", "rate_limited_by_provider"
    if code in (401, 403):
        return "rejected", "provider_error"
    if code == 413:
        return "rejected", "message_too_large"
    if code >= 500:
        return "failed", "provider_error"
    if code >= 400:
        if any(hint in lowered for hint in
               ("recipient", "phone", "number", "not a whatsapp")):
            return "bounced", "invalid_address"
        if "block" in lowered:
            return "rejected", "recipient_blocked"
        return "rejected", "provider_error"
    return "failed", "provider_error"


def _meta_error(raw: str) -> tuple[int | None, str]:
    """Meta's ``error.code`` and ``error.message`` from a failure body."""
    import json

    try:
        body: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None, (raw or "")[:400]
    error = body.get("error") if isinstance(body, dict) else None
    if not isinstance(error, dict):
        return None, (raw or "")[:400]
    code = error.get("code")
    return (
        int(code) if isinstance(code, (int, float)) else None,
        str(error.get("message") or "")[:400],
    )


def _detail(http_status: int, provider_code: int | None, message: str) -> str:
    """A failure line carrying everything needed to diagnose, and no credential."""
    parts = ["HTTP %d" % http_status]
    if provider_code is not None:
        parts.append("Meta code %d" % provider_code)
    if message:
        parts.append(message)
    return ": ".join(parts)[:900]


def _message_id(raw: str) -> str | None:
    """The provider's message id, if the response carried one."""
    import json

    try:
        body: Any = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    messages = body.get("messages") if isinstance(body, dict) else None
    if isinstance(messages, list) and messages and isinstance(messages[0], dict):
        identifier = messages[0].get("id")
        if identifier:
            return str(identifier)[:120]
    return None


def confirmed_now() -> datetime:
    """The instant a provider confirmation is applied, for T-NOT-3."""
    return datetime.now(timezone.utc)
