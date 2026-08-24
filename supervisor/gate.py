"""The escalation gate: does this situation warrant the Decision Agent?

§11.4 calls this "the pipeline's cost and noise gate, and the most consequential
transition in the model". It is entirely deterministic and entirely
``business_rule``-driven. No LLM is involved, because deciding what deserves attention is
a threshold comparison and §16.8 reserves those for deterministic logic.

**A row is written either way.** E17 rule 1: "A row is written for every evaluated
situation, escalated or suppressed. There is no silent path through the gate." That is what
lets the platform answer the question a manager asks after an incident that surprised them
-- *"the machine was showing symptoms, why didn't the system tell me?"* -- with a row
rather than a shrug.

**The order of the tests is fixed by the documented examples**, not chosen freely. §E17's
five example rows pin it down:

======================================  ===========================================
``CTX-20260729-0046`` no prediction     ``suppressed_insufficient_data``, rule NULL
``CTX-20260730-0009`` repair under way  ``suppressed_maintenance_in_progress``, NULL
``CTX-20260729-0045`` 0.11 vs 0.70      ``suppressed_below_threshold``, rule named
``CTX-20260729-0061`` 0.74, but already
                      recommended       ``suppressed_duplicate``, rule named
``CTX-20260729-0044`` 0.68 vs 0.55      ``escalated``, rule named
======================================  ===========================================

The two verdicts that record no rule must therefore be evaluated *before* the threshold is
resolved, and the duplicate check *after* it -- row 0061 passed the threshold at 0.74 and
still names the rule that it passed. Following that order is what makes this
implementation reproduce all five documented outcomes.

**One prediction, one escalation.** ``suppressed_duplicate`` is decided twice, on the two
ways a situation can already be covered: a recommendation existing for this alert, and an
escalated context existing for this machine and prediction. The second matters because
:func:`_latest_prediction` resolves the machine's newest prediction for *every* live alert
on it, so a machine holding four live alerts would otherwise escalate one degrading bearing
four times and send four identical messages. Both checks sit after the threshold, so both
name the rule they passed. The converged alert keeps its own row, cites the context that
covers it, and already appears in that context's ``related_alert_codes`` -- nothing is
lost, the recommendation is simply unique.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.operational import (
    AiRecommendation,
    MaintenanceWorkRecord,
    Notification,
    OperationalAlert,
    PredictionFeatureSnapshot,
    PredictionResult,
    SupervisorContext as SupervisorContextRow,
)

from supervisor.context import (
    LIVE_ALERT_STATUSES,
    OPEN_WORK_STATUSES,
    PolicyRule,
    SupervisorContext,
)

RATE_LIMIT_WINDOW = timedelta(hours=1)


@dataclass
class GateVerdict:
    """The gate's decision about one alert, with everything the row needs."""

    decision: str
    rationale: str
    applied_rule: PolicyRule | None = None
    prediction: PredictionResult | None = None
    related_alert_codes: list[str] = field(default_factory=list)
    threshold: float | None = None
    probability: float | None = None

    @property
    def escalated(self) -> bool:
        return self.decision == "escalated"


def evaluate(
    session: Session,
    context: SupervisorContext,
    alert: OperationalAlert,
    reference_instant: datetime,
) -> GateVerdict:
    """Decide one alert-and-prediction pair, in the documented order."""
    master = context.master
    policy = context.policy
    machine = (
        None if alert.machine_id is None else master.machines.get(alert.machine_id))
    line_id = alert.production_line_id or (
        None if machine is None else machine.production_line_id)
    severity = master.severities.get(alert.current_severity_level_id)
    related = _related_alert_codes(session, alert)

    # ---- 1. Data quality. E17 rule 8: "Reasoning over a weak prediction produces a
    # confident-sounding recommendation with no basis." No rule is named because no
    # threshold was reached.
    prediction = _latest_prediction(session, alert)
    if prediction is None:
        return GateVerdict(
            decision="suppressed_insufficient_data",
            rationale=(
                "No prediction was available for %s, so failure risk could not be "
                "evaluated. The most likely cause is a feature snapshot that did not "
                "meet the completeness requirement. Recorded so the absence of an "
                "assessment is explicable."
                % (alert.operational_alert_code if machine is None
                   else machine.machine_code)),
            related_alert_codes=related,
        )

    snapshot = session.get(
        PredictionFeatureSnapshot, prediction.prediction_feature_snapshot_id)
    if snapshot is not None and not snapshot.is_sufficient_for_inference:
        reason = (
            "unknown" if snapshot.insufficiency_reason is None
            else snapshot.insufficiency_reason.value)
        return GateVerdict(
            decision="suppressed_insufficient_data",
            rationale=(
                "Prediction %s rests on snapshot %s, which was marked insufficient "
                "(%s) at %.2f %% completeness. Reasoning over it would produce a "
                "confident-sounding recommendation with no basis."
                % (prediction.prediction_result_code,
                   snapshot.prediction_feature_snapshot_code, reason,
                   float(snapshot.data_completeness_pct))),
            prediction=prediction,
            related_alert_codes=related,
        )

    # ---- 2. A repair is already under way. E17 rule 6: "Escalating a problem already
    # being fixed adds nothing."
    if machine is not None:
        open_job = _open_work_record(session, machine.machine_id)
        if open_job is not None:
            return GateVerdict(
                decision="suppressed_maintenance_in_progress",
                rationale=(
                    "Work order %s is %s on %s, so the condition is already being "
                    "addressed. Escalating a problem under repair would add noise "
                    "without adding information."
                    % (open_job.maintenance_work_record_code,
                       open_job.work_status.value.replace("_", " "),
                       machine.machine_code)),
                prediction=prediction,
                related_alert_codes=related,
            )

    # ---- 3. The probability threshold, resolved line-scoped then global (§11.4).
    rule = policy.probability_threshold(line_id)
    if rule is None or rule.numeric is None:
        return GateVerdict(
            decision="suppressed_insufficient_data",
            rationale=(
                "No escalation threshold is configured for this scope, so the "
                "prediction could not be evaluated against policy."),
            prediction=prediction,
            related_alert_codes=related,
        )
    probability = float(prediction.failure_probability)
    line = None if line_id is None else master.lines.get(line_id)
    scope = "the plant default" if not rule.line_scoped else (
        "the %s threshold" % (line.production_line_code if line else "line"))

    if probability < rule.numeric:
        return GateVerdict(
            decision="suppressed_below_threshold",
            rationale=(
                "Failure probability %.4f did not reach %s of %.4f (%s), so the "
                "situation was recorded and monitored without invoking reasoning."
                % (probability, scope, rule.numeric, rule.code)),
            applied_rule=rule,
            prediction=prediction,
            related_alert_codes=related,
            threshold=rule.numeric,
            probability=probability,
        )

    # ---- 4. The severity floor.
    if (
        policy.severity_floor is not None
        and policy.severity_floor_rank is not None
        and severity is not None
        and severity.severity_rank > policy.severity_floor_rank
    ):
        return GateVerdict(
            decision="suppressed_below_threshold",
            rationale=(
                "Failure probability %.4f met %s of %.4f (%s), but alert severity %s "
                "sits below the %s floor (%s), so reasoning was not invoked."
                % (probability, scope, rule.numeric, rule.code,
                   severity.failure_severity_level_code,
                   policy.severity_floor.text, policy.severity_floor.code)),
            applied_rule=policy.severity_floor,
            prediction=prediction,
            related_alert_codes=related,
            threshold=rule.numeric,
            probability=probability,
        )

    # ---- 5. Already reasoned about. §E18's consumer table gives the Supervisor exactly
    # this read: "Reads only to check whether a recommendation already exists for an
    # alert, which is how suppressed_duplicate is decided."
    existing = _existing_recommendation(session, alert)
    if existing is not None:
        return GateVerdict(
            decision="suppressed_duplicate",
            rationale=(
                "Recommendation %s already covers alert %s. Re-reasoning on an "
                "unresolved condition would bury the original under near-identical "
                "repeats." % (existing, alert.operational_alert_code)),
            applied_rule=rule,
            prediction=prediction,
            related_alert_codes=related,
            threshold=rule.numeric,
            probability=probability,
        )

    # ---- 5b. Already escalated for this same prediction. Several live alerts on one
    # machine resolve to the same newest prediction -- a vibration case and a data-quality
    # case both observing one degrading bearing -- and each would otherwise escalate
    # separately and produce its own near-identical recommendation and its own WhatsApp
    # message for a single physical failure. The unit a recommendation is unique by is the
    # machine and the prediction it cites, so that is the pair converged on here. The
    # contributing alert is not lost: it keeps its own row, names the context that covers
    # it, and already appears in that context's related_alert_codes.
    converged = _existing_escalation(session, alert, prediction)
    if converged is not None:
        subject = (
            alert.operational_alert_code if machine is None else machine.machine_code)
        return GateVerdict(
            decision="suppressed_duplicate",
            rationale=(
                "Context %s has already escalated prediction %s for %s, so alert %s is "
                "recorded as a further observation of that same predicted failure rather "
                "than escalated again. Several alerts can witness one failure; one "
                "prediction yields one recommendation."
                % (converged, prediction.prediction_result_code, subject,
                   alert.operational_alert_code)),
            applied_rule=rule,
            prediction=prediction,
            related_alert_codes=related,
            threshold=rule.numeric,
            probability=probability,
        )

    # ---- 6. Rate limiting. E17 rule 7 is careful that this "suppresses delivery, never
    # recording" -- the row is still written and stays visible on the dashboard.
    limited = _rate_limited(session, context, alert, reference_instant)
    if limited is not None:
        return GateVerdict(
            decision="suppressed_rate_limited",
            rationale=limited,
            applied_rule=rule,
            prediction=prediction,
            related_alert_codes=related,
            threshold=rule.numeric,
            probability=probability,
        )

    # ---- 7. Escalate.
    detail = [
        "Failure probability %.4f met %s of %.4f (%s)"
        % (probability, scope, rule.numeric, rule.code)
    ]
    if severity is not None and policy.severity_floor is not None:
        detail.append(
            "severity %s met the %s floor (%s)"
            % (severity.failure_severity_level_code, policy.severity_floor.text,
               policy.severity_floor.code))
    if line is not None:
        detail.append("Line criticality: %s" % line.criticality.value)
    if machine is not None and machine.is_bottleneck:
        detail.append("Bottleneck machine")

    return GateVerdict(
        decision="escalated",
        rationale="; ".join(detail) + ".",
        applied_rule=rule,
        prediction=prediction,
        related_alert_codes=related,
        threshold=rule.numeric,
        probability=probability,
    )


# ------------------------------------------------------------------ the four reads


def _latest_prediction(
    session: Session,
    alert: OperationalAlert,
) -> PredictionResult | None:
    """The prediction to evaluate for this alert.

    Preference goes to one raised against this alert -- ``triggering_alert_id`` records
    an off-schedule inference -- then to the machine's newest scheduled prediction.
    """
    if alert.machine_id is None:
        return None
    attributed = session.scalars(
        select(PredictionResult)
        .where(PredictionResult.triggering_alert_id == alert.operational_alert_id)
        .order_by(PredictionResult.predicted_at.desc())
        .limit(1)
    ).first()
    if attributed is not None:
        return attributed
    return session.scalars(
        select(PredictionResult)
        .where(PredictionResult.machine_id == alert.machine_id)
        .order_by(PredictionResult.predicted_at.desc())
        .limit(1)
    ).first()


def _open_work_record(
    session: Session,
    machine_id: int,
) -> MaintenanceWorkRecord | None:
    return session.scalars(
        select(MaintenanceWorkRecord)
        .where(
            MaintenanceWorkRecord.machine_id == machine_id,
            MaintenanceWorkRecord.work_status.in_(OPEN_WORK_STATUSES),
        )
        .order_by(MaintenanceWorkRecord.opened_at.desc())
        .limit(1)
    ).first()


def _existing_recommendation(
    session: Session,
    alert: OperationalAlert,
) -> str | None:
    """The code of a recommendation already produced for this alert, if any."""
    return session.scalars(
        select(AiRecommendation.ai_recommendation_code)
        .join(
            SupervisorContextRow,
            AiRecommendation.supervisor_context_id
            == SupervisorContextRow.supervisor_context_id,
        )
        .where(SupervisorContextRow.triggering_alert_id == alert.operational_alert_id)
        .order_by(AiRecommendation.generated_at.desc())
        .limit(1)
    ).first()


def _existing_escalation(
    session: Session,
    alert: OperationalAlert,
    prediction: PredictionResult,
) -> str | None:
    """The code of an escalated context already covering this machine and prediction.

    Keyed on the pair a recommendation is unique by rather than on the alert, because
    ``_latest_prediction`` resolves the machine's newest prediction for every live alert
    on it: without this, a machine holding four live alerts escalates one predicted
    failure four times.

    Only ``escalated`` rows count. A suppressed row for the same pair recorded a decision
    not to reason, and must not stand in the way of a later alert that qualifies.
    """
    if alert.machine_id is None:
        return None
    return session.scalars(
        select(SupervisorContextRow.supervisor_context_code)
        .where(
            SupervisorContextRow.machine_id == alert.machine_id,
            SupervisorContextRow.triggering_prediction_id
            == prediction.prediction_result_id,
            SupervisorContextRow.escalation_decision == "escalated",
        )
        .order_by(SupervisorContextRow.assembled_at,
                  SupervisorContextRow.supervisor_context_id)
        .limit(1)
    ).first()


def _related_alert_codes(session: Session, alert: OperationalAlert) -> list[str]:
    """Other live alerts on the same subject, considered but not merged.

    §E17: "Correlated cases reasoned about together without merging them." Codes rather
    than foreign keys, deliberately, so citing one does not pin it against purge.
    """
    if alert.machine_id is None:
        return []
    return sorted(session.scalars(
        select(OperationalAlert.operational_alert_code).where(
            OperationalAlert.machine_id == alert.machine_id,
            OperationalAlert.operational_alert_id != alert.operational_alert_id,
            OperationalAlert.alert_status.in_(LIVE_ALERT_STATUSES),
        )
    ))


def _rate_limited(
    session: Session,
    context: SupervisorContext,
    alert: OperationalAlert,
    reference_instant: datetime,
) -> str | None:
    """Whether every eligible recipient has exhausted their hourly allowance.

    Eligibility is the same filter the Notification Service applies: active, the alert at
    or above their ``min_severity_level_id``, and in scope for the line.

    **Zero eligible recipients is not rate limiting** and does not suppress here. It is a
    configuration gap, and reporting it as a rate limit would blame traffic for a missing
    row. The Notification Service records its own suppression reasons for delivery.

    With no Notification Service yet built, ``notification`` is empty, so nobody is ever
    over their limit and this check never fires. That is the correct behaviour rather than
    a stub: the query is real and will start returning rows the moment Phase 7 writes any.
    """
    master = context.master
    severity = master.severities.get(alert.current_severity_level_id)
    if severity is None:
        return None
    line_id = alert.production_line_id
    if line_id is None and alert.machine_id is not None:
        machine = master.machines.get(alert.machine_id)
        line_id = None if machine is None else machine.production_line_id

    eligible = []
    for recipient in master.recipients:
        minimum = master.severities.get(recipient.min_severity_level_id)
        if minimum is None or severity.severity_rank > minimum.severity_rank:
            continue
        if (
            recipient.scope_production_line_id is not None
            and recipient.scope_production_line_id != line_id
        ):
            continue
        eligible.append(recipient)

    if not eligible:
        return None

    window_start = reference_instant - RATE_LIMIT_WINDOW
    exhausted = 0
    limited_any = False
    for recipient in eligible:
        if recipient.max_notifications_per_hour is None:
            return None  # unlimited recipient available
        limited_any = True
        sent = int(session.execute(
            select(func.count()).select_from(Notification).where(
                Notification.notification_recipient_id
                == recipient.notification_recipient_id,
                Notification.composed_at > window_start,
                Notification.composed_at <= reference_instant,
                Notification.is_suppressed.is_(False),
            )
        ).scalar_one())
        if sent >= int(recipient.max_notifications_per_hour):
            exhausted += 1

    if limited_any and exhausted == len(eligible):
        return (
            "All %d eligible recipients have reached their hourly notification limit, "
            "so reasoning was deferred. The situation remains recorded and visible; "
            "rate limiting suppresses delivery, never recording." % len(eligible)
        )
    return None
