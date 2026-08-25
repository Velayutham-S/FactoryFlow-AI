"""The Predictions page: model output as recorded, never recomputed here."""

from __future__ import annotations

from typing import Any

import pandas as pd
import streamlit as st

from dashboard.components import (
    contribution_chart,
    empty,
    kpi_row,
    masthead,
    probability_chart,
    section,
    severity_badge,
)
from dashboard.components.layout import card, field_grid
from dashboard.services import load_prediction_detail, load_predictions
from dashboard.styles.theme import severity_colour
from dashboard.views import (
    apply_filters,
    close_detail,
    navigate,
    open_detail,
    search,
    selection,
)


def render(db_path: str, filters: dict[str, Any], database: dict[str, Any]) -> None:
    masthead("Predictions",
             "Failure probability and horizon, quoted from the Prediction Agent")

    predictions = load_predictions(db_path)
    if not predictions:
        empty("No predictions available.",
              "The Prediction Agent writes a row per scored feature snapshot. Train a "
              "model and run an inference cycle to populate this page.")
        return

    chosen = selection("selected_prediction")
    if chosen and any(p["code"] == chosen for p in predictions):
        _detail(db_path, chosen)
        return

    _list(predictions, filters)


# ------------------------------------------------------------------------ list


def _list(predictions: list[dict[str, Any]], filters: dict[str, Any]) -> None:
    rows = apply_filters(predictions, filters, {"machine": "machine_code"})
    wanted = filters.get("category")
    if wanted and wanted != "All":
        rows = [p for p in rows if p["category"] == wanted]

    term = st.text_input("Search predictions",
                         placeholder="prediction code, machine or failure category",
                         key="pred_search", label_visibility="collapsed")
    rows = search(rows, term, ["code", "machine_code", "machine_name", "category",
                              "model_name"])

    attributed = [p for p in rows if p["category"]]
    highest = max((p["probability"] or 0.0) for p in rows) if rows else 0.0
    kpi_row([
        {"label": "Shown", "value": len(rows),
         "note": "of %d predictions" % len(predictions), "tone": "info"},
        {"label": "Highest Probability", "value": "%.2f%%" % (highest * 100.0),
         "tone": "critical" if highest >= 0.7 else "info"},
        {"label": "Failure Mode Attributed", "value": len(attributed),
         "note": "%d reported without a category" % (len(rows) - len(attributed)),
         "tone": "neutral"},
        {"label": "Escalated to Reasoning",
         "value": len([p for p in rows if p["recommendation_code"]]),
         "tone": "info"},
    ])

    section("Failure Probability")
    if rows:
        st.altair_chart(probability_chart(rows), width="stretch")
        st.caption(
            "A prediction with no attributed failure category is the honest output when "
            "no parameter has left its declared healthy envelope: the probability is "
            "reported on its own rather than a mechanism being invented for it.")

    section("Prediction List")
    if not rows:
        empty("No predictions match the current filters.")
        return

    for prediction in rows:
        body, action = st.columns([6, 1])
        with body:
            card(
                '<div style="display:flex;align-items:center;gap:0.55rem;'
                'flex-wrap:wrap"><span class="ff-code" style="font-weight:660">%s</span>'
                '%s</div>'
                '<div style="margin-top:0.34rem;font-size:0.9rem">%s &middot; %s</div>'
                '<div class="ff-muted" style="margin-top:0.26rem;font-size:0.82rem">'
                '%s failure probability &middot; %s horizon &middot; %s &middot; %s</div>'
                % (prediction["code"], severity_badge(prediction["risk"]),
                   prediction["machine_code"],
                   prediction["category"] or "no failure mode attributed",
                   prediction["probability_display"], prediction["horizon_display"],
                   prediction["predicted_at"]["display"],
                   "%s %s" % (prediction["model_name"], prediction["model_version"])),
                accent=severity_colour(prediction["risk"].get("colour"),
                                       prediction["risk"].get("rank")))
        with action:
            st.write("")
            if st.button("Details", key="pd_%s" % prediction["code"], width="stretch"):
                open_detail("selected_prediction", prediction["code"])
        st.write("")


# ---------------------------------------------------------------------- detail


def _detail(db_path: str, code: str) -> None:
    detail = load_prediction_detail(db_path, code)
    if detail is None or detail["header"] is None:
        empty("Prediction %s was not found." % code)
        return
    header = detail["header"]

    back, _ = st.columns([1, 5])
    with back:
        if st.button("Back to predictions", width="stretch", key="pd_back",
                     icon=":material/arrow_back:"):
            close_detail("selected_prediction")

    card(
        '<div style="display:flex;align-items:center;gap:0.6rem;flex-wrap:wrap">'
        '<span class="ff-code" style="font-weight:680;font-size:1.1rem">%s</span>%s</div>'
        '<div class="ff-muted" style="margin-top:0.34rem;font-size:0.86rem">'
        '%s &middot; %s</div>'
        % (header["code"], severity_badge(header["risk"]),
           header["machine_code"], header["machine_name"]),
        accent=severity_colour(header["risk"].get("colour"),
                               header["risk"].get("rank")))

    section("Prediction")
    field_grid([
        {"label": "Prediction Reference", "value": header["code"]},
        {"label": "Machine", "value": header["machine_code"]},
        {"label": "Failure", "value": header["category"] or "not attributed"},
        {"label": "Failure Probability", "value": header["probability_display"],
         "raw": header["probability"]},
        {"label": "Prediction Horizon", "value": header["horizon_display"],
         "raw": "%s hours" % header["horizon_hours"]},
        {"label": "Risk Band", "html": severity_badge(header["risk"])},
        {"label": "Predicted At", "value": header["predicted_at"]["display"],
         "raw": header["predicted_at"]["iso"]},
    ])

    section("Model")
    field_grid([
        {"label": "Model", "value": header["model_name"]},
        {"label": "Version", "value": header["model_version"]},
        {"label": "Inference Time", "value": "%s ms" % header["inference_ms"]},
        {"label": "Confidence Band",
         "value": "not recorded" if header["band_low"] is None
         else "%.4f – %.4f" % (header["band_low"], header["band_high"])},
    ])

    section("Feature Contributions")
    if not detail["contributions"]:
        empty("No feature attributions were recorded for this prediction.")
    else:
        st.altair_chart(contribution_chart(detail["contributions"]), width="stretch")
        st.caption(
            "Standardised log-odds contributions from the model's own coefficients, so "
            "each bar names a feature the model actually received rather than an "
            "approximation of it.")

    _snapshot(detail["snapshot"])

    left, right, _ = st.columns([1, 1, 3])
    with left:
        if header["alert_code"] and st.button(
                "Open alert", width="stretch", key="pd_alert",
                icon=":material/notification_important:"):
            navigate("Alerts", selected_alert=header["alert_code"])
    with right:
        if header["recommendation_code"] and st.button(
                "Open recommendation", width="stretch", key="pd_rec",
                icon=":material/lightbulb:"):
            navigate("AI Recommendations",
                     selected_recommendation=header["recommendation_code"])


def _snapshot(snapshot: dict[str, Any] | None) -> None:
    section("Feature Snapshot")
    if snapshot is None:
        empty("The feature snapshot behind this prediction is not available.")
        return

    field_grid([
        {"label": "Snapshot", "value": snapshot["code"]},
        {"label": "Window",
         "value": "%s → %s" % (snapshot["window_from"]["display"],
                               snapshot["window_to"]["display"])},
        {"label": "Lookback", "value": snapshot["lookback_display"],
         "raw": "%s s" % snapshot["lookback_seconds"]},
        {"label": "Feature Set", "value": snapshot["feature_set_version"]},
        {"label": "Features", "value": snapshot["feature_count"]},
        {"label": "Source Readings",
         "value": "{:,}".format(snapshot["source_readings"])},
        {"label": "Excluded", "value": snapshot["excluded_readings"]},
        {"label": "Completeness",
         "value": "not recorded" if snapshot["completeness"] is None
         else "%.2f%%" % snapshot["completeness"]},
        {"label": "Sufficient",
         "value": "yes" if snapshot["is_sufficient"]
         else "no — %s" % (snapshot["insufficiency_reason"] or "reason not recorded")},
    ])

    values = snapshot["feature_values"]
    if values:
        with st.expander("Feature vector (%d value(s))" % len(values)):
            frame = pd.DataFrame(
                [{"Feature": k, "Value": v} for k, v in sorted(values.items())])
            st.dataframe(frame, width="stretch", hide_index=True)
            st.caption("The exact vector fed to the model, stored so the prediction is "
                       "reproducible against its own model version.")
