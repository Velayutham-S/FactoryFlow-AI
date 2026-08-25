"""Contract element 1, the root-cause vocabulary, and the confidence ceiling.

Everything here is deterministic and derived from stored rows. §16.5 is explicit that
"**supporting evidence originates from persisted telemetry and events**, so it is
verifiable against the operational database rather than being generated narrative" -- so
the LLM contributes nothing to this module's output. It writes prose about the evidence;
it never supplies the evidence.

**One documentation gap, named rather than papered over.** §E18 requires
``supporting_evidence`` to carry ``feature_contributions``, and §16.5 requires the ML
confidence element to present "the failure probability ... as a number the manager can
weigh". Neither value appears in the ``context_document`` structure §E17 specifies. The
agent therefore reads the ``prediction_result`` row its own ``prediction_result_id``
already points at -- its mandatory parent, not a discretionary query -- and carries the
probability forward **verbatim**. That is what keeps AQ8 true: "ML confidence reported in
a recommendation matches the Prediction Agent's output exactly and is never re-estimated
by the LLM."

**Confidence is capped by arithmetic, not by the model's own opinion.** §E18 rule 4:
``root_cause_confidence = 'high'`` "requires the ``supporting_evidence`` corroboration
list to contain findings from **at least two independent measurement paths**. One signal
is a hypothesis; two agreeing is a corroborated one." Counting distinct source entities is
a deterministic operation, so it is done here and imposed on the LLM's proposal rather
than trusted to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from models.master import Machine, MachineTypeFailureMode
from models.operational import PredictionResult

from decision.context import MasterSnapshot

# Confidence vocabulary, in descending strength. Bound by
# ck_ar_root_cause_confidence_allowed.
CONFIDENCE_ORDER = ("high", "moderate", "low")

# Feature names whose presence in the prediction's attributions evidences a measurement
# path independent of the sensors. Both are named in §E15's machine-level block, and
# §E18's worked corroboration list cites `cycle_history` precisely because it is
# "independent of sensors, measured by the machine's own timer".
CYCLE_FEATURES = ("cycle_deviation_slope", "mean_cycle_deviation_pct")
SCRAP_FEATURES = ("attributed_scrap_count_24h",)


@dataclass(frozen=True)
class PredictionFacts:
    """The ML-confidence element, read from ``prediction_result`` and never restated."""

    prediction_result_id: int
    code: str
    failure_probability: float
    risk_severity_level_id: int
    risk_severity_code: str
    predicted_failure_category_id: int | None
    machine_type_failure_mode_id: int | None
    prediction_horizon_hours: int
    top_contributing_features: list[dict[str, Any]]
    predicted_at: datetime


@dataclass(frozen=True)
class Candidate:
    """One root-cause option declared for the machine's type."""

    failure_category_id: int
    failure_category_code: str
    category_name: str
    mode: MachineTypeFailureMode
    frequency: str
    leading_indicator: str
    has_safety_implication: bool
    required_specialization: str


@dataclass
class Evidence:
    """Contract element 1, plus the two facts derived from it."""

    document: dict[str, Any]
    measurement_paths: list[str] = field(default_factory=list)
    event_codes: list[str] = field(default_factory=list)

    @property
    def confidence_ceiling(self) -> str:
        """The strongest confidence §E18 rule 4 permits for this evidence."""
        if len(self.measurement_paths) >= 2:
            return "high"
        if len(self.measurement_paths) == 1:
            return "moderate"
        return "low"

    @property
    def is_substantive(self) -> bool:
        """Whether element 1 was actually produced, for ``contract_complete``."""
        return bool(self.document.get("events") or self.document.get("readings"))


def prediction_facts(prediction: PredictionResult) -> PredictionFacts:
    """Lift the prediction row into the values the recommendation cites.

    ``failure_probability`` is converted once, for formatting, and never recomputed.
    There is no column on ``ai_recommendation`` that could hold it, which is the
    structural half of the same guarantee (§O18).
    """
    raw = prediction.top_contributing_features
    contributions = list(raw) if isinstance(raw, list) else []
    return PredictionFacts(
        prediction_result_id=prediction.prediction_result_id,
        code=prediction.prediction_result_code,
        failure_probability=float(prediction.failure_probability),
        risk_severity_level_id=prediction.risk_severity_level_id,
        risk_severity_code="",
        predicted_failure_category_id=prediction.predicted_failure_category_id,
        machine_type_failure_mode_id=prediction.machine_type_failure_mode_id,
        prediction_horizon_hours=prediction.prediction_horizon_hours,
        top_contributing_features=contributions,
        predicted_at=prediction.predicted_at,
    )


def build_evidence(
    context_document: dict[str, Any],
    facts: PredictionFacts,
) -> Evidence:
    """Assemble ``supporting_evidence`` in the four-part shape §E18 specifies.

    Events and readings are taken from the context document verbatim -- they were
    resolved by the Supervisor Agent from ``operational_event`` and
    ``machine_sensor_reading``, and restating them here would be the generated narrative
    §16.5 forbids. Feature contributions come from the prediction.

    The corroboration list is the load-bearing part, because rule 4 keys the confidence
    to it. When the context document supplies one it is used as given, since the
    assembling agent had the full picture. Otherwise it is derived from what is present,
    and each entry names the **source entity** that produced the finding so that
    "independent measurement path" is countable rather than asserted.
    """
    evidence_block = _as_list(context_document.get("evidence"))
    events = [_event_entry(entry) for entry in evidence_block]
    readings = [_reading_entry(entry)
                for entry in _as_list(context_document.get("readings"))]

    supplied = _as_list(context_document.get("corroboration"))
    if supplied:
        corroboration = [
            {"source": str(entry.get("source", "")).strip(),
             "finding": str(entry.get("finding", "")).strip()}
            for entry in supplied
            if isinstance(entry, dict) and entry.get("source")
        ]
    else:
        corroboration = _derive_corroboration(events, facts)

    paths: list[str] = []
    for entry in corroboration:
        source = entry.get("source", "")
        if source and source not in paths:
            paths.append(source)

    document = {
        "events": events,
        "readings": readings,
        "corroboration": corroboration,
        "feature_contributions": [
            {
                "feature": str(item.get("feature", "")),
                "value": item.get("value"),
                "contribution": item.get("contribution"),
            }
            for item in facts.top_contributing_features
            if isinstance(item, dict) and item.get("feature")
        ],
        "ml_confidence": {
            "prediction_result_code": facts.code,
            "failure_probability": facts.failure_probability,
            "prediction_horizon_hours": facts.prediction_horizon_hours,
            "note": "Quoted from prediction_result. Never re-estimated (§16.5, AQ8).",
        },
    }
    return Evidence(
        document=document,
        measurement_paths=paths,
        event_codes=[e["code"] for e in events if e.get("code")],
    )


def summarise(evidence: Evidence) -> dict[str, Any]:
    """A compact projection of ``supporting_evidence``, for the prompt and nothing else.

    :attr:`Evidence.document` is the audit record: it is persisted verbatim to
    ``ai_recommendation.supporting_evidence`` and must keep every event, because §16.5
    requires the evidence to be "verifiable against the operational database". But a
    recurring condition correlates thousands of samples into one alert, and sending one
    JSON object per sample to the model is both ruinous and pointless -- the four hundredth
    vibration breach tells it nothing the first did not.

    So the events are aggregated per parameter: how many fired, the worst reading against
    the limit that fired, the exceedance, and the window they span, with the first and
    last event codes kept as anchors so a human can find the raw rows. Readings,
    corroboration, feature contributions and ML confidence pass through untouched -- they
    are already one entry per parameter or per feature, and each is load-bearing for the
    §16.5 contract.

    **The aggregation is arithmetic, so it belongs here and not in the model.** §16.8
    reserves calculation for deterministic logic; the model receives settled figures and
    reasons about them.

    Event ordering is relied upon rather than re-sorted: :func:`build_evidence` takes the
    events from the context document, which the Supervisor assembled ``ORDER BY
    detected_at``, so the first entry seen for a parameter is its earliest.
    """
    document = evidence.document
    events = [entry for entry in document.get("events", []) if isinstance(entry, dict)]

    groups: dict[str, dict[str, Any]] = {}
    for event in events:
        key = str(event.get("parameter") or "unattributed")
        group = groups.get(key)
        if group is None:
            group = {
                "parameter": event.get("parameter"),
                "unit": event.get("unit"),
                "event_count": 0,
                "worst_observed": None,
                "threshold_breached": None,
                "peak_exceedance_pct": None,
                "first_detected_at": event.get("detected_at"),
                "first_event_code": event.get("code") or None,
                "last_detected_at": None,
                "last_event_code": None,
            }
            groups[key] = group

        group["event_count"] += 1
        group["last_detected_at"] = event.get("detected_at")
        group["last_event_code"] = event.get("code") or None

        observed = _as_float(event.get("observed"))
        if observed is None:
            continue
        worst = group["worst_observed"]
        if worst is None or observed > worst:
            threshold = _as_float(event.get("threshold"))
            group["worst_observed"] = observed
            group["threshold_breached"] = threshold
            group["peak_exceedance_pct"] = (
                None if not threshold
                else round(((observed - threshold) / threshold) * 100.0, 2))

    return {
        "event_total": len(events),
        "event_window": {
            "first_detected_at": events[0].get("detected_at") if events else None,
            "last_detected_at": events[-1].get("detected_at") if events else None,
        },
        "parameters_breached": [groups[key] for key in sorted(groups)],
        "readings": document.get("readings", []),
        "corroboration": document.get("corroboration", []),
        "feature_contributions": document.get("feature_contributions", []),
        "ml_confidence": document.get("ml_confidence", {}),
        "note": (
            "Events are aggregated per parameter for brevity. All %d individual events "
            "are persisted verbatim in ai_recommendation.supporting_evidence and remain "
            "auditable; first_event_code and last_event_code locate the raw rows."
            % len(events)
        ),
    }


def _derive_corroboration(
    events: list[dict[str, Any]],
    facts: PredictionFacts,
) -> list[dict[str, str]]:
    """Independent findings, from the evidence and the model's own attributions.

    Three paths are distinguishable from stored data without inventing anything: the
    sensors that raised the events, the machine's own cycle timer, and scrap attributed
    to the machine. §E18's worked example lists exactly this kind of set and explains
    why it matters -- "the top two contributions come from mechanically independent
    measurements, which is precisely why the Decision Agent can present the root cause
    as corroborated rather than speculative".
    """
    found: list[dict[str, str]] = []

    parameters = sorted({str(e.get("parameter")) for e in events if e.get("parameter")})
    if parameters:
        found.append({
            "source": "machine_sensor_reading",
            "finding": "threshold conditions observed on %s" % ", ".join(parameters),
        })

    for item in facts.top_contributing_features:
        if not isinstance(item, dict):
            continue
        name = str(item.get("feature", ""))
        value = item.get("value")
        if not _is_nonzero(value):
            continue
        if name in CYCLE_FEATURES and not _has_source(found, "cycle_history"):
            found.append({
                "source": "cycle_history",
                "finding": "%s at %s, measured by the machine's own cycle timer rather "
                           "than any sensor" % (name, value),
            })
        elif name in SCRAP_FEATURES and not _has_source(found, "scrap_record"):
            found.append({
                "source": "scrap_record",
                "finding": "%s scrap record(s) attributed to this machine in the last "
                           "24 hours" % value,
            })
    return found


def root_cause_candidates(
    master: MasterSnapshot,
    machine: Machine,
) -> list[Candidate]:
    """The root-cause options declared for this machine's type.

    §E18 rule 3 constrains the classification to the modes declared in
    ``machine_type_failure_mode`` for the machine's own type. Presenting the LLM with
    this list rather than the whole twelve-value taxonomy is what makes "the LLM
    classifies, never invents" enforceable: a category outside the list is rejected, and
    a category implausible for the equipment cannot be chosen in the first place.

    Ordered most frequent first so the prompt leads with the likeliest option.
    """
    order = {"common": 0, "occasional": 1, "rare": 2}
    candidates: list[Candidate] = []
    for mode in master.modes_for_machine(machine):
        category = master.failure_categories.get(mode.failure_category_id)
        if category is None:
            continue
        candidates.append(Candidate(
            failure_category_id=category.failure_category_id,
            failure_category_code=category.failure_category_code,
            category_name=category.category_name,
            mode=mode,
            frequency=mode.relative_frequency.value,
            leading_indicator=mode.leading_indicator_description,
            has_safety_implication=bool(category.has_safety_implication),
            required_specialization=category.required_specialization.value,
        ))
    candidates.sort(key=lambda c: (order.get(c.frequency, 3), c.failure_category_code))
    return candidates


def cap_confidence(proposed: str, ceiling: str) -> str:
    """Hold the model's proposal to the ceiling the evidence supports."""
    candidate = (proposed or "").strip().lower()
    if candidate not in CONFIDENCE_ORDER:
        return ceiling
    if CONFIDENCE_ORDER.index(candidate) < CONFIDENCE_ORDER.index(ceiling):
        return ceiling
    return candidate


# ------------------------------------------------------------------ small helpers


def _as_list(value: Any) -> list[Any]:
    return [item for item in value if isinstance(item, dict)] if isinstance(
        value, list) else []


def _event_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One event, in the six fields §E18 names."""
    return {
        "code": str(entry.get("code", "")).strip(),
        "parameter": entry.get("parameter"),
        "observed": entry.get("observed"),
        "threshold": entry.get("threshold"),
        "unit": entry.get("unit"),
        "detected_at": entry.get("detected_at"),
    }


def _reading_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """One reading, in the five fields §E18 names."""
    return {
        "parameter": entry.get("parameter"),
        "latest": entry.get("latest"),
        "healthy_max": entry.get("healthy_max"),
        "pct_above": entry.get("pct_above"),
        "unit": entry.get("unit"),
    }


def _has_source(found: list[dict[str, str]], source: str) -> bool:
    return any(item.get("source") == source for item in found)


def _is_nonzero(value: Any) -> bool:
    try:
        return abs(float(value)) > 0.0
    except (TypeError, ValueError):
        return False


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
