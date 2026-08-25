"""The Alerts page: the detected-condition list, one alert's detail and its evidence."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import (
    alerts_by_machine_chart,
    empty,
    kpi_row,
    masthead,
    section,
    severity_badge,
    severity_distribution_chart,
    status_badge,
)
from dashboard.components.layout import card, field_grid
from dashboard.services import load_alert_detail, load_alerts
from dashboard.styles.theme import severity_colour
from dashboard.views import (
    apply_filters,
    close_detail,
    navigate,
    open_detail,
    search,
    selected,
    selection,
)

LIVE = ("open", "acknowledged", "escalated")


def render(db_path: str, filters: dict[str, Any], database: dict[str, Any]) -> None:
    masthead("Alerts", "Correlated conditions, their severity and the gate's verdict")

    alerts = load_alerts(db_path)
    if not alerts:
        empty("No alerts recorded.",
              "Run the Monitoring Agent to correlate detected events into alerts.")
        return

    chosen = selection("selected_alert")
    if chosen and any(a["code"] == chosen for a in alerts):
        _detail(db_path, chosen)
        return

    _list(db_path, alerts, filters)


# ------------------------------------------------------------------------ list


def _list(db_path: str, alerts: list[dict[str, Any]],
          filters: dict[str, Any]) -> None:
    rows = apply_filters(alerts, filters, {
        "machine": "machine_code",
        "severity": "severity",
        "alert_status": "status",
    })
    decision = selected(filters, "escalation_decision")
    if decision:
        rows = [a for a in rows
                if (a["verdict"] or {}).get("decision") == decision]

    term = st.text_input("Search alerts", placeholder="alert code, machine or category",
                         key="alert_search", label_visibility="collapsed")
    rows = search(rows, term, ["code", "machine_code", "machine_name", "category",
                               "correlation_key"])

    live = [a for a in rows if a["status"] in LIVE]
    escalated = [a for a in rows if a["status"] == "escalated"]
    kpi_row([
        {"label": "Shown", "value": len(rows), "note": "of %d alerts" % len(alerts),
         "tone": "info"},
        {"label": "Live", "value": len(live),
         "tone": "warning" if live else "healthy"},
        {"label": "Escalated", "value": len(escalated),
         "tone": "critical" if escalated else "healthy"},
        {"label": "Events Correlated",
         "value": "{:,}".format(sum(a["event_count"] for a in rows)),
         "tone": "neutral"},
    ])

    left, right = st.columns(2, gap="medium")
    with left:
        section("By Severity")
        st.altair_chart(severity_distribution_chart(rows), width="stretch")
    with right:
        section("By Machine")
        st.altair_chart(alerts_by_machine_chart(rows), width="stretch")

    section("Alert List")
    if not rows:
        empty("No alerts match the current filters.")
        return

    for alert in rows:
        verdict = alert["verdict"] or {}
        badges = [severity_badge(alert["severity"]),
                  status_badge(alert["status"], "alert")]
        if verdict.get("decision"):
            badges.append(status_badge(verdict["decision"], "escalation"))

        body, action = st.columns([6, 1])
        with body:
            card(
                '<div style="display:flex;align-items:center;gap:0.55rem;'
                'flex-wrap:wrap"><span class="ff-code" style="font-weight:660">%s</span>'
                '%s</div>'
                '<div style="margin-top:0.34rem;font-size:0.9rem">%s &middot; %s</div>'
                '<div class="ff-muted" style="margin-top:0.26rem;font-size:0.82rem">'
                '%s &middot; %s event(s) &middot; last activity %s &middot; %s</div>'
                % (alert["code"], "".join(badges),
                   alert["machine_code"] or "plant-wide",
                   alert["category"].replace("_", " "),
                   alert["probability_display"],
                   "{:,}".format(alert["event_count"]),
                   alert["last_event_at"]["display"],
                   alert["prediction_code"] or "no prediction attributed"),
                accent=severity_colour(alert["severity"].get("colour"),
                                       alert["severity"].get("rank")))
        with action:
            st.write("")
            if st.button("Details", key="al_%s" % alert["code"], width="stretch"):
                open_detail("selected_alert", alert["code"])
        st.write("")


# ---------------------------------------------------------------------- detail


def _detail(db_path: str, alert_code: str) -> None:
    detail = load_alert_detail(db_path, alert_code)
    if detail is None or detail["header"] is None:
        empty("Alert %s was not found." % alert_code)
        return
    header = detail["header"]

    back, _ = st.columns([1, 5])
    with back:
        if st.button("Back to alerts", width="stretch", key="al_back",
                     icon=":material/arrow_back:"):
            close_detail("selected_alert")

    card(
        '<div style="display:flex;align-items:center;gap:0.6rem;flex-wrap:wrap">'
        '<span class="ff-code" style="font-weight:680;font-size:1.1rem">%s</span>'
        '%s%s</div>'
        '<div class="ff-muted" style="margin-top:0.34rem;font-size:0.86rem">%s</div>'
        % (header["code"], severity_badge(header["severity"]),
           status_badge(header["status"], "alert"),
           header["machine_name"] or "plant-wide"),
        accent=severity_colour(header["severity"].get("colour"),
                               header["severity"].get("rank")))

    # The condition beside the platform's verdict on it. Reading "what fired" and "what we
    # decided about it" together is the whole point of opening an alert.
    facts, verdict_column = st.columns(2, gap="medium")
    verdict = header["verdict"] or {}

    with facts:
        section("Alert Information")
        field_grid([
            {"label": "Alert ID", "value": header["code"]},
            {"label": "Machine", "value": header["machine_code"] or "plant-wide"},
            {"label": "Category", "value": header["category"].replace("_", " ")},
            {"label": "Severity", "html": severity_badge(header["severity"])},
            {"label": "Status", "html": status_badge(header["status"], "alert")},
            {"label": "Opened", "value": header["opened_at"]["display"],
             "raw": header["opened_at"]["iso"]},
            {"label": "Last Event", "value": header["last_event_at"]["display"]},
            {"label": "Events", "value": "{:,}".format(header["event_count"])},
            {"label": "Target Response",
             "value": "%s min" % header["severity"]["target_response_minutes"]
             if header["severity"].get("target_response_minutes") else "not set"},
        ])

    with verdict_column:
        section("Prediction and Escalation Verdict")
        field_grid([
            {"label": "Prediction",
             "value": header["prediction_code"] or "none attributed"},
            {"label": "Failure Probability", "value": header["probability_display"],
             "raw": header["probability"]},
            {"label": "Context", "value": verdict.get("code") or "not evaluated"},
            {"label": "Verdict",
             "html": status_badge(verdict.get("decision"), "escalation")
             if verdict.get("decision") else None,
             "value": None if verdict.get("decision") else "not evaluated"},
            {"label": "Assembled",
             "value": (verdict.get("assembled_at") or {}).get("display")},
        ])

    st.caption("Correlation key `%s` — the identity the Monitoring Agent groups events "
               "under, and the reason a recurring condition is one alert rather than "
               "thousands." % header["correlation_key"])

    if verdict.get("rationale"):
        st.markdown("**Recorded rationale**")
        st.info(verdict["rationale"])
    else:
        st.caption("This alert has not been through the escalation gate, so no verdict "
                   "was recorded for it.")

    if header["prediction_code"] and st.button("Open prediction", key="al_pred",
                                              icon=":material/insights:"):
        navigate("Predictions", selected_prediction=header["prediction_code"])

    _evidence(detail)


def _evidence(detail: dict[str, Any]) -> None:
    section("Supporting Evidence")
    kpi_row([
        {"label": "Events Correlated", "value": "{:,}".format(detail["event_total"]),
         "tone": "info"},
        {"label": "Event Types", "value": len(detail["event_types"]),
         "tone": "neutral"},
        {"label": "Shown Below", "value": detail["events_shown"],
         "note": "newest first", "tone": "neutral"},
    ])

    if detail["event_types"]:
        st.write("")
        st.markdown(" ".join(
            '`%s × %d`' % (name.replace("_", " "), count)
            for name, count in sorted(detail["event_types"].items(),
                                      key=lambda kv: -kv[1])))

    events = detail["events"]
    if not events:
        empty("No events are attached to this alert.")
        return

    with st.expander("Event evidence (%d of %s shown)"
                     % (detail["events_shown"],
                        "{:,}".format(detail["event_total"]))):
        frame = pd.DataFrame([
            {
                "Event": event["code"],
                "Detected": event["detected_at"]["display"],
                "Type": event["type"].replace("_", " "),
                "Parameter": event["parameter"] or "—",
                "Observed": event["observed_display"],
                "Threshold": event["threshold_display"],
                "Direction": (event["direction"] or "—").replace("_", " "),
                "Sustained (s)": event["sustained_seconds"],
            }
            for event in events
        ])
        st.dataframe(frame, width="stretch", hide_index=True)
        st.caption(
            "Observed values and breached thresholds are captured onto the event itself, "
            "which is why this evidence survives the ninety-day telemetry purge. "
            "Measurements are shown at engineering precision; the stored values are "
            "unchanged.")
