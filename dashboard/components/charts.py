"""Charts, built with Altair because Streamlit already ships and themes it.

**Six charts, each answering one operational question.** Nothing here is decorative: a
chart that does not change a decision is not drawn.

* :func:`trend_chart` -- is this parameter drifting out of its declared healthy envelope?
* :func:`severity_distribution_chart` -- how much of the alert load is genuinely severe?
* :func:`alerts_by_machine_chart` -- where is the noise coming from?
* :func:`probability_chart` -- which predictions are near the escalation threshold?
* :func:`contribution_chart` -- why did the model say what it said?
* :func:`delivery_status_chart` -- did the messages actually leave?

Colour is left to Streamlit's own chart theme except where it carries meaning, in which
case it comes from the shared palette or from ``display_color_hex``. Axis titles carry the
real unit of measure so a number is never shown without knowing what it is.
"""

from __future__ import annotations

from typing import Any, Sequence

import altair as alt
import pandas as pd

from dashboard.styles.theme import severity_colour, tone_colour


def _empty_frame(columns: Sequence[str]) -> pd.DataFrame:
    return pd.DataFrame({name: [] for name in columns})


def trend_chart(
    trend: dict[str, Any],
    *,
    parameter_name: str,
    unit: str | None,
    normal_min: float | None = None,
    normal_max: float | None = None,
    height: int = 260,
) -> alt.LayerChart | alt.Chart:
    """One parameter's history with its declared healthy limits marked.

    The limits are drawn as dashed rules rather than a shaded band: a rule reads as a
    threshold that matters, and it survives Streamlit's light and dark chart themes
    identically. ``normal_min`` and ``normal_max`` come from ``machine_type_parameter``, so
    the envelope is the one master data declares for this machine's type rather than a
    convenient round number.
    """
    points = trend.get("points") or []
    frame = pd.DataFrame([
        {"time": p["recorded_at"], "value": p["value"], "state": p.get("state") or ""}
        for p in points
    ]) if points else _empty_frame(["time", "value", "state"])

    axis_title = parameter_name if not unit else "%s (%s)" % (parameter_name, unit)
    line = (
        alt.Chart(frame)
        .mark_line(strokeWidth=1.6, color=tone_colour("info"))
        .encode(
            x=alt.X("time:T", title=None),
            y=alt.Y("value:Q", title=axis_title,
                    scale=alt.Scale(zero=False, nice=True)),
            tooltip=[
                alt.Tooltip("time:T", title="Recorded"),
                alt.Tooltip("value:Q", title=axis_title, format=".4f"),
                alt.Tooltip("state:N", title="Machine state"),
            ],
        )
    )

    layers: list[Any] = [line]
    for limit, label, tone in ((normal_max, "healthy maximum", "warning"),
                               (normal_min, "healthy minimum", "neutral")):
        if limit is None:
            continue
        rule_frame = pd.DataFrame({"limit": [limit], "label": [label]})
        layers.append(
            alt.Chart(rule_frame)
            .mark_rule(strokeDash=[5, 4], strokeWidth=1.2, color=tone_colour(tone))
            .encode(y=alt.Y("limit:Q"),
                    tooltip=[alt.Tooltip("label:N", title="Limit"),
                             alt.Tooltip("limit:Q", title="Value", format=".4f")])
        )

    return alt.layer(*layers).properties(height=height).interactive(bind_y=False)


def severity_distribution_chart(
    alerts: Sequence[dict[str, Any]],
    height: int = 240,
) -> alt.Chart:
    """Alert count per severity band, coloured by the band's own master-data colour."""
    rows: dict[str, dict[str, Any]] = {}
    for alert in alerts:
        severity = alert.get("severity") or {}
        code = severity.get("code") or "unknown"
        entry = rows.setdefault(code, {
            "severity": code,
            "label": "%s %s" % (code, severity.get("name") or ""),
            "rank": severity.get("rank") or 99,
            "colour": severity_colour(severity.get("colour"), severity.get("rank")),
            "count": 0,
        })
        entry["count"] += 1
    frame = pd.DataFrame(sorted(rows.values(), key=lambda r: r["rank"])) \
        if rows else _empty_frame(["severity", "label", "rank", "colour", "count"])

    domain = list(frame["severity"]) if not frame.empty else []
    palette = list(frame["colour"]) if not frame.empty else []
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3, size=26)
        .encode(
            x=alt.X("count:Q", title="Alerts"),
            y=alt.Y("severity:N", title=None, sort=domain),
            color=alt.Color("severity:N", legend=None,
                            scale=alt.Scale(domain=domain, range=palette)),
            tooltip=[alt.Tooltip("label:N", title="Severity"),
                     alt.Tooltip("count:Q", title="Alerts")],
        )
        .properties(height=height)
    )


def alerts_by_machine_chart(
    alerts: Sequence[dict[str, Any]],
    height: int = 260,
) -> alt.Chart:
    """Live alert count per machine, so the noisiest asset is obvious at a glance."""
    counts: dict[str, int] = {}
    for alert in alerts:
        code = alert.get("machine_code") or "unassigned"
        counts[code] = counts.get(code, 0) + 1
    frame = pd.DataFrame(
        [{"machine": k, "count": v} for k, v in sorted(counts.items())]
    ) if counts else _empty_frame(["machine", "count"])
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3, size=22, color=tone_colour("info"))
        .encode(
            x=alt.X("count:Q", title="Alerts"),
            y=alt.Y("machine:N", title=None, sort="-x"),
            tooltip=[alt.Tooltip("machine:N", title="Machine"),
                     alt.Tooltip("count:Q", title="Alerts")],
        )
        .properties(height=height)
    )


def probability_chart(
    predictions: Sequence[dict[str, Any]],
    height: int = 240,
) -> alt.Chart:
    """Failure probability per prediction, coloured by the risk band the platform assigned.

    Quoted, never recomputed: the value plotted is ``prediction_result.failure_probability``
    as stored.
    """
    rows = [
        {
            "code": p["code"],
            "machine": p.get("machine_code") or "",
            "probability": (p.get("probability") or 0.0) * 100.0,
            "colour": severity_colour((p.get("risk") or {}).get("colour"),
                                      (p.get("risk") or {}).get("rank")),
            "risk": (p.get("risk") or {}).get("code") or "",
            "display": p.get("probability_display") or "",
        }
        for p in predictions if p.get("probability") is not None
    ]
    frame = pd.DataFrame(rows) if rows else _empty_frame(
        ["code", "machine", "probability", "colour", "risk", "display"])
    domain = list(frame["code"]) if not frame.empty else []
    palette = list(frame["colour"]) if not frame.empty else []
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3, size=22)
        .encode(
            x=alt.X("probability:Q", title="Failure probability (%)",
                    scale=alt.Scale(domain=[0, 100])),
            y=alt.Y("code:N", title=None, sort="-x"),
            color=alt.Color("code:N", legend=None,
                            scale=alt.Scale(domain=domain, range=palette)),
            tooltip=[alt.Tooltip("code:N", title="Prediction"),
                     alt.Tooltip("machine:N", title="Machine"),
                     alt.Tooltip("display:N", title="Failure probability"),
                     alt.Tooltip("risk:N", title="Risk band")],
        )
        .properties(height=height)
    )


def contribution_chart(
    contributions: Sequence[dict[str, Any]],
    height: int = 260,
) -> alt.Chart:
    """Signed feature contributions for one prediction.

    Diverging colour because the sign is the whole point: a feature can push the
    probability down as well as up, and a single-hue bar chart would hide that.
    """
    rows = [
        {
            "feature": entry.get("feature") or "",
            "contribution": float(entry.get("contribution") or 0.0),
            "value": entry.get("value"),
            "direction": "increases risk"
            if float(entry.get("contribution") or 0.0) >= 0 else "reduces risk",
        }
        for entry in contributions if entry.get("feature")
    ]
    frame = pd.DataFrame(rows) if rows else _empty_frame(
        ["feature", "contribution", "value", "direction"])
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3, size=18)
        .encode(
            x=alt.X("contribution:Q", title="Contribution to log-odds"),
            y=alt.Y("feature:N", title=None,
                    sort=alt.EncodingSortField(field="contribution", op="max",
                                               order="descending")),
            color=alt.Color(
                "direction:N", title=None,
                scale=alt.Scale(domain=["increases risk", "reduces risk"],
                                range=[tone_colour("critical"),
                                       tone_colour("info")])),
            tooltip=[alt.Tooltip("feature:N", title="Feature"),
                     alt.Tooltip("value:Q", title="Feature value", format=".4f"),
                     alt.Tooltip("contribution:Q", title="Contribution",
                                 format="+.4f"),
                     alt.Tooltip("direction:N", title="Direction")],
        )
        .properties(height=height)
    )


def delivery_status_chart(
    notifications: Sequence[dict[str, Any]],
    height: int = 220,
) -> alt.Chart:
    """Notification outcomes, including suppression, which is an outcome and not a gap."""
    counts: dict[str, int] = {}
    for notification in notifications:
        if notification.get("is_suppressed"):
            key = "suppressed"
        else:
            key = notification.get("status") or "not attempted"
        counts[key] = counts.get(key, 0) + 1

    tones = {
        "delivered": "healthy",
        "sent": "info",
        "queued": "neutral",
        "suppressed": "neutral",
        "not attempted": "neutral",
        "failed": "critical",
        "rejected": "critical",
        "bounced": "critical",
    }
    rows = [
        {"status": key, "count": value, "colour": tone_colour(tones.get(key, "neutral"))}
        for key, value in sorted(counts.items())
    ]
    frame = pd.DataFrame(rows) if rows else _empty_frame(["status", "count", "colour"])
    domain = list(frame["status"]) if not frame.empty else []
    palette = list(frame["colour"]) if not frame.empty else []
    return (
        alt.Chart(frame)
        .mark_bar(cornerRadiusEnd=3, size=24)
        .encode(
            x=alt.X("count:Q", title="Notifications"),
            y=alt.Y("status:N", title=None, sort="-x"),
            color=alt.Color("status:N", legend=None,
                            scale=alt.Scale(domain=domain, range=palette)),
            tooltip=[alt.Tooltip("status:N", title="Outcome"),
                     alt.Tooltip("count:Q", title="Notifications")],
        )
        .properties(height=height)
    )
