"""The Machines page: the asset list, one machine's detail, and its sensor trends."""

from __future__ import annotations

from typing import Any

import streamlit as st

from dashboard.components import (
    empty,
    kpi_row,
    masthead,
    section,
    severity_badge,
    status_badge,
    trend_chart,
)
from dashboard.components.layout import card, field_grid
from dashboard.services import (
    load_latest_readings,
    load_machine_detail,
    load_machines,
    load_parameter_catalogue,
    load_trend,
)
from dashboard.styles.theme import severity_colour, tone_colour
from dashboard.views import (
    apply_filters,
    close_detail,
    navigate,
    open_detail,
    search,
    seed_widget,
    selected,
    selection,
)


def render(db_path: str, filters: dict[str, Any], database: dict[str, Any]) -> None:
    masthead("Machine Monitoring",
             "Asset state, live risk and sensor history from recorded telemetry")

    machines = load_machines(db_path)
    if not machines:
        empty("No machines in master data.",
              "Seed master data before using the dashboard.")
        return

    chosen = selection("selected_machine")
    if chosen and any(m["code"] == chosen for m in machines):
        _detail(db_path, chosen)
        return

    _list(db_path, machines, filters)


# ------------------------------------------------------------------------ list


def _list(db_path: str, machines: list[dict[str, Any]],
          filters: dict[str, Any]) -> None:
    rows = apply_filters(machines, filters, {"machine": "code", "line": "line_code"})
    wanted_severity = selected(filters, "severity")
    if wanted_severity:
        rows = [m for m in rows
                if m["alert"] and m["alert"]["severity"]["code"] == wanted_severity]

    term = st.text_input("Search machines", placeholder="code, name, type or line",
                         key="machine_search", label_visibility="collapsed")
    rows = search(rows, term, ["code", "name", "type_code", "type_name",
                               "line_code", "line_name"])

    with_alert = [m for m in rows if m["alert"]]
    with_telemetry = [m for m in rows if m["has_telemetry"]]
    kpi_row([
        {"label": "Shown", "value": len(rows), "note": "of %d machines" % len(machines),
         "tone": "info"},
        {"label": "With Live Alert", "value": len(with_alert),
         "tone": "warning" if with_alert else "healthy"},
        {"label": "Recording Telemetry", "value": len(with_telemetry),
         "note": "%d declare parameters but record none"
                 % (len(rows) - len(with_telemetry)), "tone": "neutral"},
    ])

    section("Machines")
    if not rows:
        empty("No machines match the current filters.",
              "Widen a filter in the sidebar or clear the search box.")
        return

    for machine in rows:
        accent = None
        if machine["alert"]:
            accent = severity_colour(machine["alert"]["severity"].get("colour"),
                                     machine["alert"]["severity"].get("rank"))
        badges = [status_badge(machine["state"], "machine")]
        if machine["alert"]:
            badges.append(severity_badge(machine["alert"]["severity"]))
        if machine["is_bottleneck"]:
            badges.append(
                '<span class="ff-badge" style="--ff-tone:%s">'
                '<span class="ff-dot"></span>bottleneck</span>' % tone_colour("warning"))
        if not machine["is_monitored"]:
            badges.append(
                '<span class="ff-badge" style="--ff-tone:%s">'
                '<span class="ff-dot"></span>not monitored</span>'
                % tone_colour("neutral"))

        prediction = machine["prediction"]
        risk = "no prediction" if not prediction else (
            "%s · %s · %s" % (prediction["probability_display"],
                              prediction["horizon_display"],
                              prediction["category"] or "no category attributed"))

        body, action = st.columns([6, 1])
        with body:
            card(
                '<div style="display:flex;align-items:center;gap:0.6rem;'
                'flex-wrap:wrap"><span class="ff-code" style="font-weight:660;'
                'font-size:0.98rem">%s</span><span style="font-weight:560">%s</span>'
                '%s</div>'
                '<div class="ff-muted" style="margin-top:0.34rem;font-size:0.83rem">'
                '%s &middot; %s &middot; %s &middot; criticality %s</div>'
                '<div style="margin-top:0.3rem;font-size:0.86rem">%s</div>'
                % (machine["code"], machine["name"], "".join(badges),
                   machine["type_code"], machine["line_code"], machine["line_name"],
                   machine["criticality"], risk),
                accent=accent)
        with action:
            st.write("")
            if st.button("Details", key="mc_%s" % machine["code"], width="stretch"):
                open_detail("selected_machine", machine["code"])
        st.write("")


# ---------------------------------------------------------------------- detail


def _detail(db_path: str, machine_code: str) -> None:
    detail = load_machine_detail(db_path, machine_code)
    if detail is None:
        empty("Machine %s is not in master data." % machine_code)
        return

    back, _ = st.columns([1, 5])
    with back:
        if st.button("Back to machines", width="stretch", key="mc_back",
                     icon=":material/arrow_back:"):
            close_detail("selected_machine")

    badges = [status_badge(detail["state"], "machine")]
    if detail["alert"]:
        badges.append(severity_badge(detail["alert"]["severity"]))
    card(
        '<div style="display:flex;align-items:center;gap:0.65rem;flex-wrap:wrap">'
        '<span class="ff-code" style="font-weight:680;font-size:1.15rem">%s</span>'
        '<span style="font-size:1.1rem;font-weight:600">%s</span>%s</div>'
        % (detail["code"], detail["name"], "".join(badges)),
        accent=severity_colour(
            (detail["alert"] or {}).get("severity", {}).get("colour"),
            (detail["alert"] or {}).get("severity", {}).get("rank"))
        if detail["alert"] else None)

    # Asset facts beside live condition: the first is reference data that rarely changes,
    # the second is why the operator opened the page. Side by side, both are above the fold.
    facts, health = st.columns(2, gap="medium")
    alert = detail["alert"]
    prediction = detail["prediction"]

    with facts:
        section("Machine Information")
        field_grid([
            {"label": "Machine ID", "value": detail["code"]},
            {"label": "Machine Name", "value": detail["name"]},
            {"label": "Machine Type", "value": "%s — %s" % (detail["type_code"],
                                                            detail["type_name"])},
            {"label": "Production Line", "value": "%s — %s" % (detail["line_code"],
                                                               detail["line_name"])},
            {"label": "Criticality", "value": detail["criticality"]},
            {"label": "Lifecycle", "value": detail["lifecycle"].replace("_", " ")},
            {"label": "Bottleneck", "value": "yes" if detail["is_bottleneck"] else "no"},
        ])

    with health:
        section("Current Health")
        field_grid([
            {"label": "State", "html": status_badge(detail["state"], "machine")},
            {"label": "State Since",
             "value": (detail["state_since"] or {}).get("display")},
            {"label": "Live Alerts", "value": detail["live_alert_count"]},
            {"label": "Severity",
             "html": severity_badge(alert["severity"]) if alert else None,
             "value": None if alert else "no live alert"},
            {"label": "Failure Category",
             "value": (prediction or {}).get("category") or "not attributed"},
            {"label": "Failure Probability",
             "value": (prediction or {}).get("probability_display") or "not predicted",
             "raw": (prediction or {}).get("probability")},
            {"label": "Prediction Horizon",
             "value": (prediction or {}).get("horizon_display") or "not stated"},
        ])

    assigned = detail.get("assigned")
    if assigned:
        section("Assigned Response")
        field_grid([
            {"label": "Failure", "value": assigned["failure"]},
            {"label": "Estimated Downtime", "value": assigned["downtime"]},
            {"label": "Deadline", "value": assigned["deadline"]},
            {"label": "Engineer", "value": assigned["engineer"] or "unassigned"},
            {"label": "Team", "value": assigned["team"] or "unassigned"},
            {"label": "Recommendation", "value": assigned["recommendation_code"]},
        ])
        if st.button("Open recommendation", key="mc_rec",
                     icon=":material/lightbulb:"):
            navigate("AI Recommendations",
                     selected_recommendation=assigned["recommendation_code"])
    else:
        section("Assigned Response")
        empty("No recommendation is assigned to this machine.",
              "A recommendation exists only once a prediction has been escalated.")

    _sensors(db_path, detail)


# --------------------------------------------------------------------- sensors


def _sensors(db_path: str, detail: dict[str, Any]) -> None:
    section("Sensor Readings")
    catalogue = load_parameter_catalogue(db_path, detail["code"])
    if not catalogue:
        empty("This machine's type declares no measured parameters.",
              "Parameters are declared per machine type in machine_type_parameter.")
        return

    latest = load_latest_readings(db_path, detail["code"])
    if not latest:
        empty("No sensor readings recorded for %s." % detail["code"],
              "It declares %d parameter(s) but has written no telemetry."
              % len(catalogue))
        return

    cards = []
    for parameter in catalogue:
        reading = latest.get(parameter["code"])
        if reading is None:
            continue
        over = (parameter["normal_max"] is not None
                and reading["value"] is not None
                and reading["value"] > parameter["normal_max"])
        under = (parameter["normal_min"] is not None
                 and reading["value"] is not None
                 and reading["value"] < parameter["normal_min"])
        cards.append({
            "label": parameter["name"],
            "value": reading["display"],
            "note": "healthy max %s" % parameter["normal_max_display"],
            "tone": "critical" if (over or under) else "healthy",
        })
    if cards:
        kpi_row(cards, columns=3)
        st.write("")

    measured = [p for p in catalogue if p["code"] in latest]
    labels = ["%s (%s)" % (p["name"], p["unit"]) for p in measured]
    # Each machine type declares its own parameters, so the option list changes as the user
    # moves between machines. Seeding drops a remembered label that this machine does not
    # measure, which would otherwise leave the widget pointing at an option it cannot show.
    seed_widget("mc_param", None, labels)
    choice = st.selectbox("Parameter trend", labels, key="mc_param")
    parameter = measured[labels.index(choice)]

    only_valid = st.checkbox(
        "Exclude readings flagged as instrument faults", value=True,
        key="mc_valid",
        help="Readings whose quality_flag is not 'valid' are sensor faults rather than "
             "machine condition. They reach the parameter's physical maximum and would "
             "compress the real signal into a flat line.")

    trend = load_trend(db_path, detail["code"], parameter["code"],
                       only_valid=only_valid)
    if not trend["points"]:
        empty("No readings available for %s." % parameter["name"])
        return

    reading = latest[parameter["code"]]
    field_grid([
        {"label": "Latest", "value": reading["display"], "raw": reading["value"]},
        {"label": "Healthy Range",
         "value": "%s – %s" % (parameter["normal_min_display"],
                               parameter["normal_max_display"])},
        {"label": "Recorded", "value": reading["recorded_at"]["display"]},
        {"label": "Quality", "value": reading["quality"]},
        {"label": "ML Feature", "value": "yes" if parameter["is_ml_feature"] else "no"},
    ])
    st.altair_chart(
        trend_chart(trend, parameter_name=parameter["name"], unit=parameter["unit"],
                    normal_min=parameter["normal_min"],
                    normal_max=parameter["normal_max"]),
        width="stretch")
    st.caption(
        "%d point(s) thinned from the most recent %s reading(s) for this parameter%s. "
        "Values are displayed at engineering precision; the stored readings keep their "
        "full recorded precision."
        % (len(trend["points"]), "{:,}".format(trend["window_rows"]),
           ", %d excluded as instrument faults" % trend["excluded"]
           if trend["excluded"] else ""))
