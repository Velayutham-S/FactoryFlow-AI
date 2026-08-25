"""The Overview page: system state in one screen, and the pipeline made obvious."""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.components import (
    alerts_by_machine_chart,
    empty,
    kpi_row,
    masthead,
    pipeline_flow,
    section,
    severity_badge,
    severity_distribution_chart,
    status_badge,
    traceability_chain,
)
from dashboard.components.layout import card, field_grid, prose
from dashboard.services import (
    load_alerts,
    load_component_activity,
    load_machines,
    load_overview,
    load_recommendation_detail,
    load_recommendations,
    load_traceability,
)
from dashboard.styles.theme import severity_colour, tone_colour
from dashboard.views import navigate

TITLE = "FactoryFlow AI"
SUBTITLE = "AI-Powered Predictive Maintenance & Decision Support"

LIVE = ("open", "acknowledged", "escalated")


def render(db_path: str, filters: dict[str, Any], database: dict[str, Any]) -> None:
    masthead(TITLE, SUBTITLE, meta="%s · %s" % (
        database.get("plant_name") or "plant", database.get("name")))

    counts = load_overview(db_path)
    alerts = load_alerts(db_path)
    machines = load_machines(db_path)
    recommendations = load_recommendations(db_path)

    _kpis(counts)
    section("Pipeline")
    _pipeline(counts)
    _priority_alert(db_path, alerts, recommendations)
    _system_health(db_path)
    _charts(alerts)
    _recent(db_path, alerts, recommendations, machines)


# ------------------------------------------------------------------------ kpis


def _kpis(counts: dict[str, Any]) -> None:
    critical = counts["critical_machines"]
    kpi_row([
        {"label": "Machines", "value": counts["machines_total"],
         "note": "%d monitored · %d in service" % (counts["machines_monitored"],
                                                   counts["machines_in_service"]),
         "tone": "info"},
        {"label": "Live Alerts", "value": counts["alerts_live"],
         "note": "%d of %d total escalated" % (counts["alerts_escalated"],
                                              counts["alerts_total"]),
         "tone": "warning" if counts["alerts_live"] else "healthy"},
        {"label": "Predictions", "value": counts["predictions"],
         "note": "%d feature snapshot(s)" % counts["feature_snapshots"],
         "tone": "info"},
        {"label": "AI Recommendations", "value": counts["recommendations"],
         "note": "from %d escalation(s)" % counts["contexts_escalated"],
         "tone": "info"},
    ])
    st.write("")
    kpi_row([
        {"label": "Critical Machines", "value": critical,
         "note": "live alert requiring immediate escalation",
         "tone": "critical" if critical else "healthy"},
        {"label": "Escalated Alerts", "value": counts["alerts_escalated"],
         "note": "%d suppressed by the gate" % counts["contexts_suppressed"],
         "tone": "critical" if counts["alerts_escalated"] else "healthy"},
        {"label": "Notifications Sent", "value": counts["deliveries_transmitted"],
         "note": "%d composed · %d suppressed" % (counts["notifications"],
                                                  counts["notifications_suppressed"]),
         "tone": "healthy" if counts["deliveries_transmitted"] else "neutral"},
        {"label": "Sensor Readings", "value": "{:,}".format(counts["readings"]),
         "note": "%s event(s) detected" % "{:,}".format(counts["events"]),
         "tone": "neutral"},
    ])


def _pipeline(counts: dict[str, Any]) -> None:
    pipeline_flow([
        {"label": "Telemetry", "value": "{:,}".format(counts["readings"]),
         "tone": "neutral"},
        {"label": "Monitoring", "value": "{:,} events".format(counts["events"]),
         "tone": "info"},
        {"label": "Detection", "value": "%d alerts" % counts["alerts_total"],
         "tone": "warning"},
        {"label": "Prediction", "value": "%d scored" % counts["predictions"],
         "tone": "info"},
        {"label": "Escalation",
         "value": "%d of %d" % (counts["contexts_escalated"], counts["contexts"]),
         "tone": "critical"},
        {"label": "AI Reasoning",
         "value": "%d recommendation(s)" % counts["recommendations"],
         "tone": "info"},
        {"label": "WhatsApp",
         "value": "%d sent" % counts["deliveries_transmitted"], "tone": "healthy"},
    ])
    st.caption(
        "Every figure is a row count from the live database. The escalation stage is the "
        "cost gate: situations below the business-rule threshold are recorded as "
        "suppressed rather than reasoned about, which is why far fewer recommendations "
        "exist than alerts."
    )


# -------------------------------------------------------------- priority alert


def _worst(alerts: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The live alert that most deserves the top of the screen.

    Most severe first by ``severity_rank``, then most recently active. Both are stored
    columns, so the choice is reproducible rather than a heuristic.
    """
    live = [a for a in alerts if a["status"] in LIVE]
    if not live:
        return None
    # Two stable passes rather than one composite key: ISO timestamps sort
    # lexicographically but cannot be negated, so the tie-break is applied first and the
    # severity ordering second. Python's sort is stable, so the result is
    # "most severe, and among those the most recently active".
    ordered = sorted(live, key=lambda a: a["last_event_at"]["iso"] or "", reverse=True)
    ordered.sort(key=lambda a: a["severity"].get("rank") or 99)
    return ordered[0]


def _priority_alert(
    db_path: str,
    alerts: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
) -> None:
    worst = _worst(alerts)
    if worst is None:
        section("Top Priority")
        empty("No live alerts. Every detected condition has been resolved or closed.")
        return

    severity = worst["severity"]
    urgent = bool(severity.get("requires_immediate_escalation")) \
        or worst["status"] == "escalated"
    section("Top Priority Alert")

    accent = severity_colour(severity.get("colour"), severity.get("rank"))
    heading = "%s %s" % ("\U0001f6a8" if urgent else "\u26a0",
                         worst["machine_name"] or worst["machine_code"] or "Plant-wide")
    card(
        '<div style="display:flex;align-items:center;gap:0.7rem;flex-wrap:wrap">'
        '<span style="font-size:1.2rem;font-weight:650">%s</span>%s%s</div>'
        '<div class="ff-muted" style="margin-top:0.35rem;font-size:0.86rem">'
        '%s &middot; <span class="ff-code">%s</span> &middot; %s event(s) correlated'
        '</div>'
        % (heading, severity_badge(severity), status_badge(worst["status"], "alert"),
           worst["machine_code"] or "unassigned", worst["code"],
           "{:,}".format(worst["event_count"])),
        accent=accent,
    )

    match = next((r for r in recommendations
                  if r["machine_code"] == worst["machine_code"]), None)
    fields: list[dict[str, Any]] = [
        {"label": "Alert Category", "value": worst["category"].replace("_", " ")},
        {"label": "Failure Probability", "value": worst["probability_display"]},
        {"label": "Last Event", "value": worst["last_event_at"]["display"]},
    ]
    if match is not None:
        fields = [
            {"label": "Failure", "value": match["category"]},
            {"label": "Failure Probability", "value": match["probability_display"],
             "raw": match["probability"]},
            {"label": "Prediction Horizon", "value": match["horizon_display"]},
            {"label": "Estimated Downtime", "value": match["downtime_display"],
             "raw": match["downtime_minutes"]},
            {"label": "Deadline", "value": match["deadline"]["display"],
             "raw": match["deadline"]["iso"]},
        ]
    field_grid(fields)

    if match is None:
        st.caption(
            "This alert has not produced a recommendation. Either it was suppressed by "
            "the escalation gate or no prediction was attributed to it -- the Alerts page "
            "shows the recorded verdict."
        )
    else:
        detail = load_recommendation_detail(db_path, match["code"])
        if detail is not None:
            st.markdown("**Recommended Action**")
            prose(detail["fields"]["recommendation"]["display"])

    left, right, _ = st.columns([1, 1, 3])
    with left:
        if st.button("Open alert", width="stretch", key="ov_alert",
                     icon=":material/notification_important:"):
            navigate("Alerts", selected_alert=worst["code"])
    with right:
        if worst["machine_code"] and st.button(
                "Open machine", width="stretch", key="ov_machine",
                icon=":material/precision_manufacturing:"):
            navigate("Machines", selected_machine=worst["machine_code"])


# -------------------------------------------------------------- system health


def _system_health(db_path: str) -> None:
    section("System Health")
    activity = load_component_activity(db_path)
    heartbeats = [row for row in activity if row["heartbeat_status"]]

    for row in activity:
        columns = st.columns([2.4, 1.5, 2.2, 2.4])
        with columns[0]:
            st.markdown("**%s**" % row["component"])
        with columns[1]:
            if row["heartbeat_status"]:
                st.markdown(status_badge(row["heartbeat_status"], "alert"),
                            unsafe_allow_html=True)
            elif row["has_output"]:
                st.markdown(
                    '<span class="ff-badge" style="--ff-tone:%s">'
                    '<span class="ff-dot"></span>output recorded</span>'
                    % tone_colour("healthy"), unsafe_allow_html=True)
            else:
                st.markdown(
                    '<span class="ff-badge" style="--ff-tone:%s">'
                    '<span class="ff-dot"></span>status unavailable</span>'
                    % tone_colour("neutral"), unsafe_allow_html=True)
        with columns[2]:
            st.markdown('<span class="ff-muted">%s %s</span>'
                        % ("{:,}".format(row["output_count"]), row["output_noun"]
                           + ("s" if row["output_count"] != 1 else "")),
                        unsafe_allow_html=True)
        with columns[3]:
            st.markdown('<span class="ff-muted">last %s</span>'
                        % (row["last_output"]["display"] if row["has_output"]
                           else "never"), unsafe_allow_html=True)

    if not heartbeats:
        st.caption(
            "No component writes a heartbeat to `system_health_status` in this database, "
            "so none can be reported as operational. What is shown instead is evidence "
            "of work: the number of rows each component has written and when it last "
            "wrote one. That proves the stage ran; it does not prove it is running now."
        )


# ---------------------------------------------------------------------- charts


def _charts(alerts: list[dict[str, Any]]) -> None:
    live = [a for a in alerts if a["status"] in LIVE]
    left, right = st.columns(2, gap="medium")
    with left:
        section("Alert Load by Severity")
        if not alerts:
            empty("No alerts recorded.")
        else:
            st.altair_chart(severity_distribution_chart(alerts), width="stretch")
    with right:
        section("Live Alerts by Machine")
        if not live:
            empty("No live alerts.")
        else:
            st.altair_chart(alerts_by_machine_chart(live), width="stretch")


# ---------------------------------------------------------------------- recent


def _recent(
    db_path: str,
    alerts: list[dict[str, Any]],
    recommendations: list[dict[str, Any]],
    machines: list[dict[str, Any]],
) -> None:
    section("Traceability")
    chains = load_traceability(db_path)
    if not chains:
        empty("No recommendation has been produced yet, so there is no chain to trace.",
              "The chain appears once a prediction is escalated and reasoned about.")
    else:
        for chain in chains[:4]:
            st.markdown('<div class="ff-muted" style="font-size:0.84rem;'
                        'margin-bottom:0.3rem">%s &middot; %s</div>'
                        % (chain["machine_code"] or "plant",
                           chain["generated_at"]["display"]),
                        unsafe_allow_html=True)
            traceability_chain(chain)
            st.write("")

    left, right = st.columns(2, gap="medium")
    with left:
        section("Machine Health")
        attention = sorted(
            machines,
            key=lambda m: ((m["alert"]["severity"]["rank"] if m["alert"] else 99),
                           -(m["prediction"]["probability"] or 0.0)
                           if m["prediction"] else 0.0),
        )[:5]
        if not attention:
            empty("No machines in master data.")
        for machine in attention:
            bits = [status_badge(machine["state"], "machine")]
            if machine["alert"]:
                bits.append(severity_badge(machine["alert"]["severity"], False))
            note = "no live alert" if not machine["alert"] else (
                "%s · %s" % (machine["alert"]["code"],
                             machine["alert"]["category"].replace("_", " ")))
            card(
                '<div style="display:flex;align-items:center;gap:0.55rem;'
                'flex-wrap:wrap"><span class="ff-code" style="font-weight:650">%s</span>'
                '<span>%s</span>%s</div>'
                '<div class="ff-muted" style="margin-top:0.3rem;font-size:0.82rem">'
                '%s</div>'
                % (machine["code"], machine["name"], "".join(bits), note))
            st.write("")

    with right:
        section("Latest Recommendations")
        if not recommendations:
            empty("No recommendations available.")
        for recommendation in recommendations[:3]:
            card(
                '<div style="display:flex;align-items:center;gap:0.55rem;'
                'flex-wrap:wrap"><span class="ff-code" style="font-weight:650">%s</span>'
                '%s</div>'
                '<div style="margin-top:0.35rem;font-weight:560">%s &middot; %s</div>'
                '<div class="ff-muted" style="margin-top:0.25rem;font-size:0.82rem">'
                '%s failure probability &middot; act by %s</div>'
                % (recommendation["code"],
                   severity_badge(recommendation["severity"]),
                   recommendation["machine_code"], recommendation["category"],
                   recommendation["probability_display"],
                   recommendation["deadline"]["display"]))
            st.write("")
        if recommendations and st.button("Open AI Recommendations", width="stretch",
                                        key="ov_recs", icon=":material/lightbulb:"):
            navigate("AI Recommendations",
                     selected_recommendation=recommendations[0]["code"])
