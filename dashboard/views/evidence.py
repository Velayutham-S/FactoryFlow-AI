"""The Evidence page: what the platform based a decision on, and the chain it sits in.

Evidence is read from ``ai_recommendation.supporting_evidence`` exactly as the Decision
Agent persisted it. Nothing is recomputed, and nothing is regenerated -- the point of the
stored document is that it can be checked against the operational database rather than
believed.

**Organised in three tiers, because two audiences read this page.** The information is
identical to before; what changed is that it is no longer a flat run of nine equally-weighted
sections.

``Identification``
    What this decision is about, and the reference codes that locate it. A manager reads
    this and stops.
``Evidence``
    The measurements and findings the conclusion rests on: counts first, then the detail
    behind expanders. An engineer opens these.
``Reasoning``
    The Decision Agent's own words, in full.
``Traceability``
    The chain, and the routes out of it.

Nothing is hidden that was previously visible: the long tables and the raw document sit in
expanders, which is where a several-hundred-row table belongs on a page that also has to be
scannable.
"""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import (
    contribution_chart,
    empty,
    kpi_row,
    masthead,
    meta_row,
    section,
    severity_badge,
    traceability_chain,
)
from dashboard.components.layout import card, field_grid, prose
from dashboard.services import (
    load_evidence,
    load_recommendation_detail,
    load_recommendations,
    load_traceability,
)
from dashboard.views import navigate, seed_widget, selection


def render(db_path: str, filters: dict[str, Any], database: dict[str, Any]) -> None:
    masthead("Evidence & Traceability",
             "The persisted basis for each decision, and the chain from reading to message")

    recommendations = load_recommendations(db_path)
    if not recommendations:
        empty("No evidence available.",
              "Evidence is attached to a recommendation, and none has been produced yet.")
        _chains(load_traceability(db_path))
        return

    codes = [r["code"] for r in recommendations]
    # A keyed selectbox remembers its own value and ignores `index` on later runs, so
    # arriving here from a recommendation had to be honoured before the widget is built.
    seed_widget("ev_pick", selection("selected_recommendation"), codes)
    code = st.selectbox("Recommendation", codes, key="ev_pick",
                        help="Every recommendation the platform has produced. Evidence is "
                             "attached to the recommendation, not to the alert.")

    evidence = load_evidence(db_path, code)
    detail = load_recommendation_detail(db_path, code)
    if evidence is None or detail is None:
        empty("Evidence for %s could not be read." % code)
        return
    chain = next((c for c in load_traceability(db_path)
                  if c["recommendation"] == code), None)

    _identification(evidence, detail, chain)
    _evidence(evidence, detail)
    _reasoning(detail)
    _traceability(code, chain)


# -------------------------------------------------------------- identification


def _identification(
    evidence: dict[str, Any],
    detail: dict[str, Any],
    chain: dict[str, Any] | None,
) -> None:
    """What this decision is about, beside the codes that locate it.

    Two columns rather than one: the subject and its identifiers answer different questions
    and reading them side by side is faster than scrolling past one to reach the other.
    """
    section("Identification")
    fields = detail["fields"]
    left, right = st.columns([3, 2], gap="medium")

    with left:
        field_grid([
            {"label": "Machine", "value": fields["machine"]["display"]},
            {"label": "Failure", "value": fields["failure_type"]["display"]},
            {"label": "Severity", "html": severity_badge(detail["severity"])},
            {"label": fields["failure_probability"]["label"],
             "value": fields["failure_probability"]["display"],
             "raw": fields["failure_probability"]["value"]},
        ])

    with right:
        notifications = (chain or {}).get("notifications") or []
        if len(notifications) > 1:
            notification = "%s +%d" % (notifications[0], len(notifications) - 1)
        else:
            notification = notifications[0] if notifications else None
        meta_row([
            ("Prediction", detail.get("prediction_code")),
            ("Alert", detail.get("alert_code")),
            ("Context", detail.get("context_code")),
            ("Recommendation", detail["code"]),
            ("Notification", notification),
        ], framed=True)
        st.caption("Reference codes, as stored. A dash means the platform holds no such "
                   "link for this decision.")


# -------------------------------------------------------------------- evidence


def _evidence(evidence: dict[str, Any], detail: dict[str, Any]) -> None:
    """The measurements and findings, counts first and detail behind expanders."""
    section("Evidence")
    kpi_row([
        {"label": "Total Events", "value": "{:,}".format(evidence["event_count"]),
         "note": "correlated into this decision", "tone": "info"},
        {"label": "Parameters", "value": evidence["parameter_count"],
         "note": ", ".join(evidence["parameters"][:3]) or "none named",
         "tone": "neutral"},
        {"label": "Corroborating Sources", "value": evidence["corroboration_count"],
         "note": "independent measurement paths",
         "tone": "healthy" if evidence["corroboration_count"] >= 2 else "warning"},
        {"label": "Root Cause Confidence", "value": detail["confidence"],
         "note": "capped by the corroboration count", "tone": "neutral"},
    ])
    st.write("")

    left, right = st.columns(2, gap="medium")

    with left:
        st.markdown("**Sensor readings**")
        trend = detail["fields"].get("prediction_trend") or {}
        if trend.get("display") and trend["display"] != "no trend recorded":
            prose(trend["display"])
            st.caption("Latest value of each parameter against the healthy envelope "
                       "declared for this machine's type, at display precision.")
        else:
            st.caption("No trend was recorded for this decision.")

        readings = evidence["readings"]
        if readings:
            with st.expander("Reading detail (%d parameter(s))" % len(readings)):
                st.dataframe(pd.DataFrame(readings), width="stretch",
                             hide_index=True)
                st.caption("Stored at full recorded precision. These are the raw values "
                           "behind the formatted trend above.")

    with right:
        st.markdown("**Corroborating findings**")
        entries = evidence["corroboration"]
        if not entries:
            st.caption("None recorded. With no independent measurement path the "
                       "root-cause confidence is capped at 'low'.")
        else:
            for entry in entries:
                card('<div style="font-weight:600;font-size:0.9rem">%s</div>'
                     '<div class="ff-muted" style="margin-top:0.22rem;'
                     'font-size:0.85rem">%s</div>'
                     % (str(entry.get("source", "unknown")),
                        str(entry.get("finding", "no finding recorded"))))
                st.write("")
            st.caption("Two findings from mechanically independent measurements are what "
                       "allow a root cause to be presented as corroborated rather than "
                       "speculative.")

    contributions = evidence["feature_contributions"]
    if contributions:
        st.write("")
        st.markdown("**Model feature contributions**")
        st.altair_chart(contribution_chart(contributions), width="stretch")

    sample = evidence["sample_events"]
    if sample:
        with st.expander("Event evidence — first %d of %s"
                         % (len(sample), "{:,}".format(evidence["event_count"]))):
            st.dataframe(pd.DataFrame(sample), width="stretch", hide_index=True)
            st.caption(
                "All %s events are persisted verbatim on the recommendation and remain "
                "auditable. A bounded sample is shown because a recurring condition "
                "correlates thousands of near-identical readings and the four hundredth "
                "adds nothing the first did not."
                % "{:,}".format(evidence["event_count"]))
    else:
        st.caption("No individual events are recorded in the evidence document.")

    with st.expander("Raw evidence document"):
        st.caption("The document exactly as persisted, for anyone reconciling it against "
                   "the operational tables.")
        st.json(evidence["document"], expanded=False)


# ------------------------------------------------------------------- reasoning


def _reasoning(detail: dict[str, Any]) -> None:
    """The Decision Agent's own words. Complete, never summarised here."""
    section("Reasoning")
    recommendation = detail["fields"]["recommendation"]

    st.markdown("**Recommended action**")
    prose(recommendation["display"])

    narrative = (recommendation.get("reasoning_narrative_display")
                 or recommendation.get("reasoning_narrative"))
    if narrative:
        st.write("")
        st.markdown("**Why the platform concluded this**")
        prose(narrative)

    recovery = (recommendation.get("recovery_plan_display")
                or recommendation.get("recovery_plan"))
    if recovery:
        with st.expander("Recovery plan"):
            prose(recovery)

    st.caption(
        "Shown in full, exactly as generated by %s. Measurements are displayed at "
        "engineering precision; the stored text keeps its full recorded precision."
        % detail["llm_model"])


# ---------------------------------------------------------------- traceability


def _traceability(code: str, chain: dict[str, Any] | None) -> None:
    section("Traceability")
    if chain is None:
        empty("No chain could be resolved for this recommendation.")
        return

    traceability_chain(chain)
    st.caption(
        "Prediction, alert, context, recommendation and notification are joined by foreign "
        "keys on the rows themselves. Nothing here is inferred from matching codes or "
        "timestamps, so a gap in the chain is a real gap.")

    # One button per link that actually resolves. Nothing on this row is clickable unless it
    # leads somewhere.
    left, middle, right, _ = st.columns([1, 1, 1, 2])
    with left:
        if st.button("Open recommendation", width="stretch", key="ev_rec",
                     icon=":material/lightbulb:"):
            navigate("AI Recommendations", selected_recommendation=code)
    with middle:
        if chain.get("prediction") and st.button(
                "Open prediction", width="stretch", key="ev_pred",
                icon=":material/insights:"):
            navigate("Predictions", selected_prediction=chain["prediction"])
    with right:
        if chain.get("alert") and st.button(
                "Open alert", width="stretch", key="ev_alert",
                icon=":material/notification_important:"):
            navigate("Alerts", selected_alert=chain["alert"])

    if chain.get("notifications"):
        if st.button("Open notifications", key="ev_ntf", icon=":material/send:"):
            navigate("Notifications",
                     selected_notification=chain["notifications"][0])


def _chains(chains: list[dict[str, Any]]) -> None:
    section("Traceability")
    if not chains:
        empty("No chains to trace yet.")
        return
    for chain in chains:
        traceability_chain(chain)
        st.write("")
