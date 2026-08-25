"""The Notifications page: what was composed, what was suppressed, what reached Meta.

**No secret and no phone number appears here, and none is read.** The WhatsApp destination
and the provider credentials live in the environment, which this package never opens.
``notification_recipient`` holds a worker reference and channel switches, so a recipient is
identified by worker code and name -- which is what an operator needs and carries nothing
sensitive.
"""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.components import (
    delivery_status_chart,
    empty,
    kpi_row,
    masthead,
    section,
    severity_badge,
    status_badge,
)
from dashboard.components.layout import card, field_grid
from dashboard.services import load_notifications
from dashboard.styles.theme import severity_colour, tone_colour
from dashboard.views import apply_filters, navigate, search, selected, selection

PROVIDER = "Meta WhatsApp Cloud API"


def render(db_path: str, filters: dict[str, Any], database: dict[str, Any]) -> None:
    masthead("Notifications",
             "Composed messages, suppression decisions and delivery outcomes")

    notifications = load_notifications(db_path)
    if not notifications:
        empty("No notification history available.",
              "The Notification Service writes a row per qualifying recipient, including "
              "suppressed ones, once a recommendation exists.")
        return

    _list(db_path, notifications, filters)


def _list(db_path: str, notifications: list[dict[str, Any]],
          filters: dict[str, Any]) -> None:
    rows = apply_filters(notifications, filters, {
        "machine": "machine_code", "severity": "severity"})
    wanted = selected(filters, "delivery_status")
    if wanted:
        rows = [n for n in rows if n["status"] == wanted]

    term = st.text_input("Search notifications",
                         placeholder="notification code, recommendation, recipient",
                         key="ntf_search", label_visibility="collapsed")
    rows = search(rows, term, ["code", "recommendation_code", "recipient_code",
                              "recipient_name", "machine_code", "subject"])

    transmitted = [n for n in rows
                   if n["status"] in ("sent", "delivered")]
    suppressed = [n for n in rows if n["is_suppressed"]]
    failed = [n for n in rows if n["status"] in ("failed", "rejected", "bounced")]
    kpi_row([
        {"label": "Shown", "value": len(rows),
         "note": "of %d notifications" % len(notifications), "tone": "info"},
        {"label": "Accepted by Provider", "value": len(transmitted),
         "tone": "healthy" if transmitted else "neutral"},
        {"label": "Suppressed", "value": len(suppressed),
         "note": "recorded with a reason", "tone": "neutral"},
        {"label": "Failed", "value": len(failed),
         "tone": "critical" if failed else "healthy"},
    ])

    section("Outcomes")
    st.altair_chart(delivery_status_chart(rows), width="stretch")

    if any(n["status"] == "sent" for n in rows):
        st.info(
            "Delivery confirmation unavailable. `sent` means the message was accepted by "
            "the provider and a message id was returned. Advancing to `delivered` "
            "requires a webhook callback, and no receiver is deployed, so the platform "
            "does not claim delivery it cannot verify.",
            icon=":material/info:")

    section("Notification List")
    if not rows:
        empty("No notifications match the current filters.")
        return

    # Arriving from a recommendation's traceability chain names one notification. Opening
    # its details is what makes that navigation mean something rather than landing the user
    # on an unchanged list.
    focus = selection("selected_notification")
    if focus and not any(n["code"] == focus for n in rows):
        st.info(
            "Notification %s is filtered out of this view. Clear the filters to see it."
            % focus, icon=":material/filter_alt:")
    for notification in rows:
        _card(db_path, notification, expanded=notification["code"] == focus)


def _card(db_path: str, notification: dict[str, Any],
          expanded: bool = False) -> None:
    badges = [severity_badge(notification["severity"], False)]
    if notification["is_suppressed"]:
        badges.append(
            '<span class="ff-badge" style="--ff-tone:%s">'
            '<span class="ff-dot"></span>suppressed</span>' % tone_colour("neutral"))
    else:
        badges.append(status_badge(notification["status"], "delivery"))
    if notification["channel"]:
        badges.append(
            '<span class="ff-badge" style="--ff-tone:%s">'
            '<span class="ff-dot"></span>%s</span>'
            % (tone_colour("info"), notification["channel"]))

    card(
        '<div style="display:flex;align-items:center;gap:0.55rem;flex-wrap:wrap">'
        '<span class="ff-code" style="font-weight:660">%s</span>%s</div>'
        '<div style="margin-top:0.34rem;font-size:0.9rem">%s &rarr; %s</div>'
        '<div class="ff-muted" style="margin-top:0.26rem;font-size:0.82rem">'
        '%s &middot; composed %s%s</div>'
        % (notification["code"], "".join(badges),
           notification["recommendation_code"] or "no recommendation",
           notification["recipient_name"] or notification["recipient_code"]
           or "recipient not resolved",
           notification["machine_code"] or "plant-wide",
           notification["composed_at"]["display"],
           " &middot; %s ms" % notification["latency_ms"]
           if notification["latency_ms"] is not None else ""),
        accent=severity_colour(notification["severity"].get("colour"),
                               notification["severity"].get("rank")))

    with st.expander("Details — %s" % notification["code"], expanded=expanded):
        # Metadata on the left, delivery evidence on the right. The message body then runs
        # full width beneath, because it is the one field that needs the room.
        meta, delivery = st.columns([3, 2], gap="medium")
        with meta:
            field_grid([
                {"label": "Notification Reference", "value": notification["code"]},
                {"label": "Channel",
                 "value": (notification["channel"] or "not attempted").upper()
                 if notification["channel"] else "not attempted"},
                {"label": "Recipient",
                 "value": "%s (%s)" % (notification["recipient_name"] or "unknown",
                                       notification["recipient_code"] or "no code")},
                {"label": "Recommendation Reference",
                 "value": notification["recommendation_code"]},
                {"label": "Machine", "value": notification["machine_code"]},
                {"label": "Escalation Order",
                 "value": notification["escalation_order"]},
                {"label": "Type", "value": notification["type"]},
                {"label": "Created At", "value": notification["composed_at"]["display"],
                 "raw": notification["composed_at"]["iso"]},
                {"label": "Requires Acknowledgement",
                 "value": "yes" if notification["requires_ack"] else "no"},
            ], framed=False)
        with delivery:
            if notification["provider_reference"]:
                field_grid([
                    {"label": "Provider", "value": PROVIDER},
                    {"label": "Status",
                     "html": status_badge(notification["status"], "delivery")},
                    {"label": "Latency",
                     "value": "%s ms" % notification["latency_ms"]
                     if notification["latency_ms"] is not None else "not recorded"},
                ], framed=False)

        if notification["is_suppressed"]:
            st.warning(
                "Suppressed: %s. A row is still written so the audit trail can "
                "distinguish a deliberate suppression from a message that was never "
                "composed." % (notification["suppression_reason"] or "reason not recorded"),
                icon=":material/block:")

        if notification["attempts"]:
            st.markdown("**Delivery attempts**")
            for attempt in notification["attempts"]:
                st.markdown(
                    "- Attempt %d on `%s` — %s · %s · %s ms"
                    % (attempt["attempt"], attempt["channel"],
                       attempt["status"], attempt["attempted_at"]["display"],
                       attempt["latency_ms"]))
                if attempt["provider_reference"]:
                    # The provider is named once, in the summary column above. Here it is
                    # the message id that matters: the identifier Meta quotes back, and the
                    # only handle on this transmission that exists outside the platform.
                    st.code(attempt["provider_reference"], language=None)
                if attempt["failure_reason"]:
                    st.error("Failure: %s — %s" % (attempt["failure_reason"],
                                                   attempt["failure_detail"] or ""))
        elif not notification["is_suppressed"]:
            st.caption("No delivery attempt was recorded for this notification.")

        if notification["subject"]:
            st.markdown("**Subject**")
            st.markdown("`%s`" % notification["subject"])
        if notification["body"]:
            st.markdown("**Message as transmitted**")
            st.text_area("Body", notification["body"], height=380, disabled=True,
                         key="ntf_body_%s" % notification["code"],
                         label_visibility="collapsed", width="stretch")
            st.caption("Stored verbatim at composition. What the recipient saw is part of "
                       "the audit trail, so it is never regenerated from the current "
                       "recommendation.")

        if notification["recommendation_code"] and st.button(
                "Open recommendation", key="ntf_rec_%s" % notification["code"],
                icon=":material/lightbulb:"):
            navigate("AI Recommendations",
                     selected_recommendation=notification["recommendation_code"])
    st.write("")
