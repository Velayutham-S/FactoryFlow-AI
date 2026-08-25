"""Subject and body. What the recipient actually saw, stored rather than regenerated.

§E20: "A notification is a communication that happened, and what the recipient actually
saw is part of the audit trail. Regenerating it later from the recommendation would
produce current wording against a past decision." So this module runs once, at
composition, and its output is persisted verbatim.

**Rule 9 sets the bar for the body:** it "must contain the machine, the severity, the
recommended action, and the deadline. A message requiring the recipient to open the
dashboard to learn what to do has failed at its one job." All four are present, and the
recommendation and prediction codes are appended so the message is traceable back through
the chain from the recipient's phone.

**The layout is fixed**, because a message read under time pressure should not change
shape between incidents. Each field is a bare label with its value beneath it, separated
by one blank line::

    🚨 FactoryFlow AI Alert

    Machine
    MC-0101

    Severity
    SEV-3

    Failure
    Bearing Degradation

    Failure Probability
    55.24%

    Recommended Action
    <the opening of the recommended action>

    Deadline
    24 Jul 2026, 11:29 AM

    Estimated Downtime
    4 h 15 min (255 min)

    Reference
    REC-20260724-0001

    Prediction Horizon
    8 hours

    Prediction Reference
    PDN-20260724-0001

``Failure`` and ``Failure Probability`` are omitted when the recommendation carries no
root-cause category or no prediction result, rather than being printed empty, and the same
holds for ``Estimated Downtime``, ``Prediction Horizon`` and ``Prediction Reference``.

**The horizon and the reference are separate fields**, because they answer different
questions. ``8 hours`` is how far ahead the model looked; ``PDN-20260724-0001`` is the row
to open. Printing them as ``PDN-20260724-0001 (8 h horizon)`` made the operator parse a
parenthetical to find a number they act on, and left the horizon unlabelled.

**Two surfaces, one formatting authority.** :func:`compose` renders for WhatsApp, where
the recommendation is excerpted on a sentence or word boundary so it reads on a phone.
:func:`dashboard_view` renders the same recommendation for a screen, whole and untruncated,
and returns the exact stored figure beside every formatted string so the display stays
readable and the record stays auditable. Both call the same formatters --
:func:`format_duration`, :func:`format_timestamp`, :func:`format_horizon`,
:func:`format_measurement`, :func:`format_measurements` -- and both take their labels from
the same constants, which is what stops the two surfaces drifting apart. The Supervisor's
Final Response carries the raw values these formatters consume, so it feeds the same
authority rather than holding a third copy of the rules.

**Formatting never touches stored data.** ``estimated_downtime_minutes`` remains the
integer 255 in the database and is displayed as ``4 h 15 min (255 min)``;
``recommended_action_by`` remains a timezone-aware timestamp and is displayed as
``24 Jul 2026, 11:29 AM``, with :func:`iso_timestamp` available for audit output;
``recommended_action`` remains the Decision Agent's text verbatim on ``ai_recommendation``
and is displayed with its measurements at engineering precision by
:func:`format_measurements`. Nothing here writes, and nothing here is written back.

``line`` and ``composed_at`` remain in the signature although the body no longer prints
them: the parameters are part of this module's published interface, and the subject line
still needs the machine, severity, root cause and deadline that sit beside them.

**The probability is quoted, never recomputed.** It is read from ``prediction_result``,
which is the only place it exists -- ``ai_recommendation`` has no probability column, by
design (§O18). AQ8 requires the number reported to a human to match the Prediction Agent's
output exactly.

**The subject carries severity, machine and deadline** because §E20 notes that "many
recipients decide whether to open it from this line alone". It is capped at the column's
200 characters.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from models.master import FailureCategory, FailureSeverityLevel, Machine, ProductionLine
from models.operational import AiRecommendation

SUBJECT_LIMIT = 200

# The heading the message opens with. A siren emoji reads as urgent on a phone at a
# glance, which is the one thing the first line has to achieve. No other emoji is used:
# a maintenance instruction is not decorated.
ALERT_HEADING = "🚨 FactoryFlow AI Alert"

# Deadline rendering. Local to the plant timezone the caller passes in; the zone name is
# not appended because every recipient of this channel is on the plant's own clock.
# This is the shape format_timestamp() produces, retained as a published constant.
DEADLINE_FORMAT = "%d %b %Y, %I:%M %p"

# How much of the recommendation travels to a phone. The full text is persisted on
# ai_recommendation and shown whole on the dashboard; this is a reading aid, not an edit.
WHATSAPP_ACTION_LIMIT = 320

# The label for the failure probability, used identically on every surface.
#
# "Failure Probability" rather than "Prediction Confidence" deliberately: the number is
# the modelled likelihood that the machine fails, not the model's confidence in its own
# output. Presenting 55.24% as confidence would tell an operator the model is barely sure
# of itself, which is a different and materially misleading claim. Model self-assessment
# already has its own field, ai_recommendation.root_cause_confidence, on the high /
# moderate / low vocabulary.
PROBABILITY_LABEL = "Failure Probability"

# The prediction's two fields, named once and used by both surfaces so the wording cannot
# drift between the phone and the screen.
HORIZON_LABEL = "Prediction Horizon"
PREDICTION_REFERENCE_LABEL = "Prediction Reference"

# Display precision per unit of measure, in decimal places.
#
# The keys are the whole ``unit_of_measure`` vocabulary of ``machine_parameter``; there are
# seven parameters in the plant and every one of them is here, so a measurement is either
# formatted by a rule stated in this table or left exactly as it was found. The precision
# for each is read off the same master row: ``rpm`` is declared
# ``numeric_integer`` so it takes none, and the rest are scaled to their declared physical
# range -- two places where the working values are single digits (``mm/s`` spans 0-50,
# ``bar`` spans 0-12), one where they run to three (``°C`` to 150, ``Nm`` to 200,
# ``kW`` to 60, ``%`` to 100).
#
# This is display precision only. The stored reading keeps all four decimals it was
# recorded with, and every projection in this module returns it alongside the formatted
# string.
MEASUREMENT_PRECISION: dict[str, int] = {
    "mm/s": 2,
    "bar": 2,
    "°C": 1,
    "Nm": 1,
    "kW": 1,
    "%": 1,
    "rpm": 0,
}

# The units :func:`format_measurements` may rewrite when it finds them inside prose.
#
# ``%`` is deliberately absent. Inside a recommendation a percent sign is far more likely
# to be the failure probability or an exceedance figure than a Tool Wear reading, and
# rewriting ``55.24%`` to ``55.2%`` would contradict the ``Failure Probability`` field
# printed directly above it and break AQ8, which requires the number a human reads to
# match the Prediction Agent's output exactly. ``%`` keeps its entry in
# :data:`MEASUREMENT_PRECISION` because :func:`format_measurement` is called with an
# explicit unit, where there is no such ambiguity.
_REWRITABLE_UNITS = ("mm/s", "bar", "°C", "Nm", "kW", "rpm")

# A number immediately followed by one of those units, and nothing else.
#
# The unit is the anchor, which is what makes rewriting prose safe: a bare number is never
# touched, so machine codes, reference codes, dates, counts, currency and percentages are
# all invisible to this pattern. The lookbehind excludes a preceding word character, dot or
# comma so that the ``0001`` of ``REC-20260724-0001`` and the ``000`` of ``15,000`` cannot
# be matched as numbers in their own right, and the lookahead excludes a following word
# character so ``bar`` does not match inside ``barrier``. Longest unit first, so an
# alternation never settles for a prefix of the unit actually present.
_MEASUREMENT_IN_TEXT = re.compile(
    r"(?<![\w.,])(-?\d+(?:\.\d+)?)[ \t]*(%s)(?![\w/])"
    % "|".join(re.escape(unit)
               for unit in sorted(_REWRITABLE_UNITS, key=len, reverse=True))
)


def format_measurement(value: Any, unit: str | None) -> str:
    """One reading at engineering display precision. ``2.2333`` becomes ``2.23 mm/s``.

    The precision comes from :data:`MEASUREMENT_PRECISION`, so the result is deterministic
    for a given unit and never depends on how the value happens to have been recorded:
    ``2.5`` renders as ``2.50 mm/s`` and ``72`` as ``72.0 °C``, because a fixed number of
    places is what makes a column of readings comparable at a glance.

    A unit absent from the table is a unit this module has no stated rule for, so the value
    is passed through unchanged rather than formatted by a guess.

    The stored value is untouched. This returns a string for a human; the caller keeps the
    original number for the audit trail, and every projection in this module returns both.
    """
    if value is None:
        return "not measured"
    key = (unit or "").strip()
    places = MEASUREMENT_PRECISION.get(key)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "%s %s" % (value, key) if key else str(value)
    if places is None:
        # No rule for this unit. Reproduce the value as given, and say nothing more.
        return "%s %s" % (value, key) if key else str(value)
    shown = _at_precision(number, places)
    if key == "%":
        # The one unit written closed up against its number, by universal convention.
        return shown + key
    return "%s %s" % (shown, key) if key else shown


def format_measurements(text: str | None) -> str:
    """The same precision rule applied to the measurements inside a passage of prose.

    The Decision Agent is given readings at full recorded precision and quotes them back,
    so its text carries figures like ``2.2333 mm/s`` and ``56.2667°C``. Those are the right
    values and the wrong presentation: four decimal places on a vibration reading is noise
    to the operator deciding whether to stop the machine.

    **This changes digits, and nothing else.** Every rewrite is anchored on a unit from
    :data:`_REWRITABLE_UNITS`, so the only substrings that can be altered are a number and
    its adjacent unit. No word is touched, no sentence is re-ordered, nothing is removed
    and nothing is added -- the meaning of the recommendation is the Decision Agent's and
    survives this function intact. Where a rewrite would be unsafe the original text is
    kept, because correctness outranks readability.

    It is idempotent: formatted text formats to itself, so a passage may pass through more
    than one surface without degrading.

    The full-precision text stays on ``ai_recommendation.recommended_action``. This runs at
    read time and its result is never written back.
    """
    if not text:
        return text or ""
    return _MEASUREMENT_IN_TEXT.sub(_rewrite_measurement, text)


def format_horizon(minutes: int | float | None) -> str:
    """How far ahead the model looked, in words. ``480`` becomes ``8 hours``.

    Spelt out rather than abbreviated, because this reads as a statement about the
    prediction rather than as a quantity to reconcile -- an operator wants to know the
    forecast covers the next eight hours. :func:`format_duration` keeps the abbreviated
    form with its exact parenthetical for the downtime figure, which *is* reconciled
    against the prediction engine.

    Whole hours collapse to hours, anything under an hour stays in minutes, and a mixed
    value states both: ``90`` gives ``1 hour 30 minutes``. Singular and plural are both
    handled, so ``60`` gives ``1 hour`` rather than ``1 hours``.

    ``prediction_result.prediction_horizon_hours`` is an integer count of hours and the
    schema requires it to be positive, so only whole hours reach this from the database.
    The parameter is minutes because minutes are the finer unit and the conversion belongs
    with the caller that knows what it holds. Zero is rendered literally as ``0 minutes``
    rather than special-cased: nothing is invented for a value the schema already forbids.
    """
    if minutes is None:
        return "not stated"
    total = int(minutes)
    if total < 0:
        return "not stated"
    hours, remainder = divmod(total, 60)
    if hours == 0:
        return _count(remainder, "minute")
    if remainder == 0:
        return _count(hours, "hour")
    return "%s %s" % (_count(hours, "hour"), _count(remainder, "minute"))


def format_duration(minutes: int | float | None) -> str:
    """A duration an operator can read, with the exact figure kept alongside it.

    ``255`` becomes ``4 h 15 min (255 min)``. The parenthetical exists because the
    dashboard and the notification are read by two audiences: a supervisor wants the
    human form, and an engineer reconciling against the prediction engine wants the
    number the engine actually produced. Nothing is rounded and nothing is lost.

    The parenthetical is omitted under an hour, where it would merely repeat itself.
    """
    if minutes is None:
        return "not estimated"
    total = int(minutes)
    if total < 0:
        return "not estimated"
    hours, remainder = divmod(total, 60)
    if hours == 0:
        return "%d min" % remainder
    human = "%d h" % hours if remainder == 0 else "%d h %d min" % (hours, remainder)
    return "%s (%d min)" % (human, total)


def format_timestamp(moment: datetime | None) -> str:
    """A timestamp an operator can read. ``24 Jul 2026, 11:29 AM``.

    Presentation only. The stored value keeps its full timezone-aware precision, and
    :func:`iso_timestamp` renders the auditable form from the same instant.

    Built field by field rather than through one ``strftime`` pattern because ``%d`` and
    ``%I`` zero-pad on every platform and ``%-d`` is not portable. An operator reading a
    deadline wants "4 Jul 2026, 9:05 AM", not "04 Jul 2026, 09:05 AM".
    """
    if moment is None:
        return "no deadline set"
    hour = moment.hour % 12 or 12
    return "%d %s %d, %d:%02d %s" % (
        moment.day, moment.strftime("%b"), moment.year,
        hour, moment.minute, moment.strftime("%p"))


def iso_timestamp(moment: datetime | None) -> str | None:
    """The same instant in ISO 8601, for audit trails and machine consumers."""
    return None if moment is None else moment.isoformat()


@dataclass(frozen=True)
class Message:
    """One composed message, ready to store and to transmit."""

    subject: str
    body: str


def compose(
    recommendation: AiRecommendation,
    *,
    machine: Machine,
    line: ProductionLine | None,
    severity: FailureSeverityLevel,
    root_cause: FailureCategory | None,
    failure_probability: float | None,
    prediction_code: str | None,
    prediction_horizon_hours: int | None,
    timezone: ZoneInfo,
    composed_at: datetime,
) -> Message:
    """Build the WhatsApp message for one recommendation.

    Deliberately short. WhatsApp is read on a phone, often on a factory floor, and §5.2
    describes the channel as "suited to urgency -- short, immediate, read away from a
    desk". The full reasoning stays in the recommendation row and on the dashboard; what
    travels is what the recipient needs in order to act.
    """
    deadline = _local(recommendation.recommended_action_by, timezone)
    subject = _subject(machine, severity, root_cause, deadline)

    # Field blocks: a bare label, its value beneath it, one blank line between blocks.
    # Labels carry no colon -- on a narrow phone screen the colon reads as punctuation
    # dangling at the end of a line rather than as a separator. Every value is read from
    # the recommendation, the severity, the machine or the prediction result; nothing
    # here is derived, recomputed or inferred.
    blocks: list[tuple[str, str]] = [
        ("Machine", machine.machine_code),
        ("Severity", severity.failure_severity_level_code),
    ]

    if root_cause is not None:
        blocks.append(("Failure", root_cause.category_name))

    if failure_probability is not None:
        # Two decimals, quoted from prediction_result. AQ8 requires the number a human
        # reads to match the Prediction Agent's output, and rounding to whole percent
        # would make 0.5524 and 0.5549 indistinguishable either side of a 0.55 threshold.
        blocks.append((PROBABILITY_LABEL,
                       "%.2f%%" % (failure_probability * 100.0)))

    # The reasoning is reused exactly as the Decision Agent produced it. What travels to
    # a phone is its opening, cut on a sentence or word boundary; the whole text stays on
    # ai_recommendation and the dashboard shows it in full.
    #
    # Measurements are brought to display precision before the excerpt is taken, not after:
    # formatting shortens the text, and cutting first would fix the boundary against a
    # length the reader never sees.
    blocks.append(("Recommended Action",
                   _readable_excerpt(
                       format_measurements(recommendation.recommended_action),
                       WHATSAPP_ACTION_LIMIT)))

    # Rule 9's fourth mandatory element. Stated as an absence when there is none, rather
    # than omitted, so the recipient is never left guessing whether one was set.
    blocks.append(("Deadline", format_timestamp(deadline)))

    if recommendation.estimated_downtime_minutes is not None:
        blocks.append(("Estimated Downtime",
                       format_duration(recommendation.estimated_downtime_minutes)))

    blocks.append(("Reference", recommendation.ai_recommendation_code))

    # Two fields rather than one. Each is labelled with what it is, and each stands on its
    # own when the other is absent -- both are read from the same prediction row, but a
    # message should not have to withhold the horizon because a code is missing.
    if prediction_horizon_hours is not None:
        blocks.append((HORIZON_LABEL,
                       format_horizon(prediction_horizon_hours * 60)))

    if prediction_code is not None:
        blocks.append((PREDICTION_REFERENCE_LABEL, prediction_code))

    lines = [ALERT_HEADING]
    for label, value in blocks:
        lines += ["", label, value]

    return Message(subject=subject, body="\n".join(lines))


def dashboard_view(
    recommendation: AiRecommendation,
    *,
    machine: Machine,
    line: ProductionLine | None,
    severity: FailureSeverityLevel,
    root_cause: FailureCategory | None,
    failure_probability: float | None,
    prediction_code: str | None,
    prediction_horizon_hours: int | None,
    timezone: ZoneInfo,
    engineer: str | None = None,
    team: str | None = None,
    notification_status: str | None = None,
    prediction_trend: str | None = None,
) -> dict[str, dict[str, Any]]:
    """The same recommendation, formatted for a screen instead of a phone.

    One field per entry, each carrying the operator-facing ``display`` string and, where a
    figure underlies it, the exact ``value`` the pipeline stored. That pairing is what
    keeps the display readable and the record auditable from a single source: the
    dashboard never has to reformat, and an engineer reconciling against the prediction
    engine never has to reverse a formatting decision.

    **The recommendation is returned whole.** No truncation, no summary, no rewrite. Only
    the WhatsApp surface abbreviates, and only for reading on a phone. Its ``display``
    carries the measurements at engineering precision and its ``value`` the Decision
    Agent's text exactly as stored, and ``recovery_plan`` and ``reasoning_narrative`` are
    paired the same way.

    This is a projection, not a renderer. No dashboard application exists in this
    repository yet, so this function exists to give one a single formatting authority
    shared with the notification, which is what stops the two surfaces drifting apart.
    """
    deadline = _local(recommendation.recommended_action_by, timezone)
    downtime = recommendation.estimated_downtime_minutes
    evidence = recommendation.supporting_evidence \
        if isinstance(recommendation.supporting_evidence, dict) else {}

    # ai_recommendation.supporting_evidence is the document build_evidence() persists:
    # events, readings, corroboration, feature_contributions and ml_confidence. The
    # per-parameter aggregate the reasoning payload uses is a separate projection and is
    # not stored, so both shapes are accepted here and the stored one is preferred.
    events = evidence.get("events") if isinstance(evidence.get("events"), list) else []
    readings = evidence.get("readings") \
        if isinstance(evidence.get("readings"), list) else []
    corroboration = evidence.get("corroboration") \
        if isinstance(evidence.get("corroboration"), list) else []
    breached = evidence.get("parameters_breached") \
        if isinstance(evidence.get("parameters_breached"), list) else []

    trend = prediction_trend
    if trend is None:
        parts: list[str] = []
        # Readings are the trend signal in the stored document: the latest value for each
        # parameter against the healthy envelope for a machine of this type.
        for reading in readings:
            if not isinstance(reading, dict):
                continue
            parameter = reading.get("parameter")
            latest = reading.get("latest")
            healthy = reading.get("healthy_max")
            over = reading.get("pct_above")
            unit = reading.get("unit") or ""
            if parameter is None or latest is None:
                continue
            # Readings carry their own unit, so the precision rule applies directly here
            # rather than through the prose rewriter. The raw figures travel untouched in
            # this field's "value".
            fragment = "%s at %s" % (parameter, format_measurement(latest, unit))
            if healthy is not None:
                fragment += " against a healthy maximum of %s" % format_measurement(
                    healthy, unit)
            if over is not None:
                fragment += " (%s%% over)" % over
            parts.append(fragment)
        for group in breached:
            if not isinstance(group, dict):
                continue
            parameter = group.get("parameter")
            worst = group.get("worst_observed")
            if parameter is None or worst is None:
                continue
            parts.append("%s peaked at %s" % (
                parameter, format_measurement(worst, group.get("unit"))))
        trend = "; ".join(parts) if parts else None

    if events or readings or corroboration:
        evidence_display = "%d event(s), %d parameter reading(s), %d " \
            "corroborating source(s)" % (len(events), len(readings),
                                         len(corroboration))
    elif breached:
        evidence_display = "%d event(s) across %d parameter(s)" % (
            evidence.get("event_total", 0), len(breached))
    else:
        evidence_display = "no evidence recorded"

    return {
        "machine": {
            "label": "Machine",
            "display": "%s — %s" % (machine.machine_code, machine.machine_name),
            "value": machine.machine_code,
            "production_line": None if line is None else line.production_line_code,
        },
        "severity": {
            "label": "Severity",
            "display": "%s %s" % (severity.failure_severity_level_code,
                                  severity.severity_name),
            "value": severity.failure_severity_level_code,
        },
        "failure_type": {
            "label": "Failure Type",
            "display": "not classified" if root_cause is None
            else root_cause.category_name,
            "value": None if root_cause is None
            else root_cause.failure_category_code,
        },
        "failure_probability": {
            "label": PROBABILITY_LABEL,
            "display": "not predicted" if failure_probability is None
            else "%.2f%%" % (failure_probability * 100.0),
            "value": failure_probability,
        },
        "prediction_horizon": {
            "label": HORIZON_LABEL,
            "display": format_horizon(
                None if prediction_horizon_hours is None
                else prediction_horizon_hours * 60),
            # value is the stored column, in the unit the column is declared in.
            # value_minutes is the same quantity in minutes, for consumers that work in
            # the finer unit; neither is derived from the display string.
            "value": prediction_horizon_hours,
            "unit": "hours",
            "value_minutes": None if prediction_horizon_hours is None
            else prediction_horizon_hours * 60,
        },
        "estimated_downtime": {
            "label": "Estimated Downtime",
            "display": format_duration(downtime),
            "value": downtime,
            "unit": "minutes",
        },
        "deadline": {
            "label": "Deadline",
            "display": format_timestamp(deadline),
            "value": iso_timestamp(recommendation.recommended_action_by),
        },
        "recommendation": {
            "label": "Recommended Action",
            # Whole and untruncated, with its measurements at display precision. "value"
            # is the Decision Agent's text exactly as stored, so the two together let a
            # screen read well and an auditor read the original.
            "display": format_measurements(recommendation.recommended_action),
            "value": recommendation.recommended_action,
            "recovery_plan": recommendation.recovery_plan,
            "recovery_plan_display": format_measurements(recommendation.recovery_plan),
            "reasoning_narrative": recommendation.reasoning_narrative,
            "reasoning_narrative_display": format_measurements(
                recommendation.reasoning_narrative),
            "root_cause_confidence": recommendation.root_cause_confidence.value,
            "contract_complete": bool(recommendation.contract_complete),
        },
        "supporting_evidence": {
            "label": "Supporting Evidence",
            "display": evidence_display,
            "value": evidence,
            "event_count": len(events),
            "reading_count": len(readings),
            "corroborating_sources": [
                entry.get("source") for entry in corroboration
                if isinstance(entry, dict) and entry.get("source")
            ],
        },
        "prediction_trend": {
            "label": "Prediction Trend",
            "display": trend or "no trend recorded",
            "value": readings or breached or None,
            "feature_contributions": evidence.get("feature_contributions"),
        },
        "notification_status": {
            "label": "Notification Status",
            "display": notification_status or "not dispatched",
            "value": notification_status,
        },
        "engineer": {
            "label": "Engineer",
            "display": engineer or "unassigned",
            "value": engineer,
        },
        "team": {
            "label": "Team",
            "display": team or "unassigned",
            "value": team,
        },
        "reference": {
            "label": "Reference",
            "display": recommendation.ai_recommendation_code,
            "value": recommendation.ai_recommendation_code,
            "prediction": prediction_code,
        },
        # The prediction code as a field of its own, matching the notification, where the
        # horizon and the reference are now labelled separately. The "prediction" key on
        # "reference" above is left in place: it is part of this function's published
        # shape and something may already read it.
        "prediction_reference": {
            "label": PREDICTION_REFERENCE_LABEL,
            "display": prediction_code or "not predicted",
            "value": prediction_code,
        },
    }


def _subject(
    machine: Machine,
    severity: FailureSeverityLevel,
    root_cause: FailureCategory | None,
    deadline: datetime | None,
) -> str:
    text = "%s: %s %s" % (
        severity.failure_severity_level_code,
        machine.machine_code,
        "condition detected" if root_cause is None
        else root_cause.category_name.lower() + " predicted",
    )
    if deadline is not None:
        text += " — action needed by %s" % deadline.strftime("%H:%M")
    return _trim(text, SUBJECT_LIMIT)


def _trim(text: str, limit: int) -> str:
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 1].rstrip() + "…"


def _readable_excerpt(text: str, limit: int) -> str:
    """The opening of a long passage, cut where a reader would not notice the seam.

    Preference order: the whole text if it fits, then the last sentence that ends inside
    the limit, then the last word boundary. Cutting mid-word produces "and measur…",
    which reads like a fault rather than a continuation.

    A sentence break is only accepted past two thirds of the limit, otherwise a short
    opening sentence would throw most of the message away.
    """
    cleaned = " ".join((text or "").split())
    if len(cleaned) <= limit:
        return cleaned

    window = cleaned[:limit]
    floor = int(limit * 0.66)

    sentence_end = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence_end >= floor:
        return window[: sentence_end + 1] + " …"

    space = window.rfind(" ")
    if space >= floor:
        return window[:space].rstrip(" ,;:") + " …"

    return window.rstrip(" ,;:") + "…"


def _at_precision(number: float, places: int) -> str:
    return "%.*f" % (places, number)


def _rewrite_measurement(match: re.Match[str]) -> str:
    """One matched ``<number><unit>`` pair, re-rendered at its unit's display precision.

    Returns the matched text unchanged in every case where rewriting could mislead: a unit
    with no stated precision, a number that will not parse, and -- the one that matters --
    a non-zero reading that would round to zero. ``0.0004 mm/s`` shown as ``0.00 mm/s``
    would read as *nothing detected*, which is a different claim from a small measurement,
    so the original figure is kept instead.

    Spacing is normalised to a single space, which is why ``56.2667°C`` comes out as
    ``56.3 °C``: the source text is inconsistent about it, and the field it lands in is not.
    """
    raw, unit = match.group(1), match.group(2)
    places = MEASUREMENT_PRECISION.get(unit)
    if places is None:
        return match.group(0)
    try:
        number = float(raw)
    except ValueError:  # pragma: no cover - the pattern only matches parseable numbers
        return match.group(0)
    shown = _at_precision(number, places)
    if float(shown) == 0.0 and number != 0.0:
        return match.group(0)
    return "%s %s" % (shown, unit)


def _count(quantity: int, noun: str) -> str:
    return "%d %s" % (quantity, noun if quantity == 1 else noun + "s")


def _local(moment: datetime | None, timezone: ZoneInfo) -> datetime | None:
    return None if moment is None else moment.astimezone(timezone)
