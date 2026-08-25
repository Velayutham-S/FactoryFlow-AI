"""Contract element 4, and the priority the recommendation carries.

Both are arithmetic, so both are deterministic and neither goes near the LLM. §16.8 is
blunt about it: "Using an LLM for arithmetic risk estimation or a threshold rule for
business impact analysis are both architectural errors." Money and units are computed
here from master data and ``business_rule``; the model is later asked to explain the
figures, never to produce them.

**Priority is a disclosed construction.** §E18 requires
``priority_severity_level_id`` to reflect "technical severity **and** business impact",
and AQ5 repeats it. What the documents supply is two prioritisation multipliers --
``BR-PRIOR-GOLD`` at 1.4 for gold-tier customers and ``BR-PRIOR-SAFETY`` at 1.6 for
safety-implicated failures, both described as "applied to prioritisation scores". What
they do **not** define is the score those multipliers apply to, or how a score becomes a
severity level. This module states its choice rather than hiding it:

* **The base score is the prediction's ``failure_probability``.** It is the only
  calibrated quantity in the pipeline and already the thing the escalation gate tests, so
  using it keeps priority on the same scale as every other risk judgement.
* **The weighted score is mapped through the ``BR-PRED-RISK-SEV-*`` floors already in
  ``business_rule``.** Reusing the existing probability-to-severity mapping avoids
  inventing a second one, and keeps the mapping in the database where §E16 rule 6 put it.
* **Priority never falls below the prediction's own risk severity.** Business context can
  make a situation more urgent; it cannot make a machine less likely to fail. Without
  this floor a bronze-tier customer could pull a genuine ``SEV-2`` risk down the scale,
  which would be the opposite of what §16.5 asks prioritisation to do.

Every number involved is read from the database. Nothing is hardcoded.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from models.master import Machine, ProductionLine

from decision.context import AppliedRule, DecisionContext, MasterSnapshot
from decision.evidence import Candidate, PredictionFacts


@dataclass
class Impact:
    """The assembled business impact, and the priority derived alongside it."""

    document: dict[str, Any]
    applied_rules: list[AppliedRule] = field(default_factory=list)
    priority_severity_level_id: int = 0
    priority_severity_code: str = ""
    priority_severity_name: str = ""
    weighted_score: float = 0.0
    downtime_minutes: int = 0
    grace_period_minutes: float | None = None
    days_of_float: int | None = None

    @property
    def is_substantive(self) -> bool:
        """Whether element 4 was actually produced, for ``contract_complete``.

        A run reference alone is not an impact statement; AQ3 requires impact to
        "reference the actual affected production context, not boilerplate". The test is
        that at least one quantity was computable.
        """
        return any(
            self.document.get(key) is not None
            for key in ("units_at_risk", "margin_at_risk", "downtime_cost")
        )


def assess(
    context: DecisionContext,
    context_document: dict[str, Any],
    machine: Machine,
    line: ProductionLine,
    facts: PredictionFacts,
    root_cause: Candidate | None,
    *,
    downtime_minutes: int,
    generated_at: datetime,
) -> Impact:
    """Quantify the consequence of doing nothing, and set the priority.

    Every figure traces to a source: rates and buffers from the context document the
    Supervisor Agent assembled, margin from ``product``, cost rate from
    ``business_rule``, penalty from ``customer``.
    """
    master = context.master
    production = _block(context_document, "production")
    cascade = _block(context_document, "cascade")
    business = _block(context_document, "business")

    cited: list[AppliedRule] = []
    downtime_hours = downtime_minutes / 60.0 if downtime_minutes > 0 else 0.0

    product = master.products_by_code.get(str(production.get("product") or ""))
    customer = master.customers_by_code.get(str(production.get("customer") or ""))
    tier = (
        customer.priority_tier.value if customer is not None
        else str(production.get("customer_tier") or "").strip().lower() or None
    )

    # Units at risk: the rate the line is actually achieving, over the repair window.
    rate = _number(production.get("current_rate"))
    if rate is None:
        rate = _number(machine.rated_capacity_units_per_hour)
    if rate is None:
        rate = _number(line.design_capacity_units_per_hour)
    units_at_risk = (
        int(round(rate * downtime_hours))
        if rate is not None and downtime_hours > 0 else None
    )

    # Margin at risk: contribution per unit is selling price less material cost.
    margin_per_unit = (
        float(master.contribution_margin(product)) if product is not None else None
    )
    margin_at_risk = (
        round(units_at_risk * margin_per_unit, 2)
        if units_at_risk is not None and margin_per_unit is not None else None
    )

    # Downtime cost: line-scoped rate falling back to global (§29 rule 3).
    cost_rule = context.downtime_cost_for(line.production_line_id)
    downtime_cost = None
    if cost_rule is not None:
        cited.append(cost_rule)
        if downtime_hours > 0:
            downtime_cost = round(cost_rule.value * downtime_hours, 2)

    # Penalty exposure: the rate is the customer's, the exposure depends on float.
    penalty_per_day = (
        float(customer.late_delivery_penalty_per_day)
        if customer is not None
        and customer.late_delivery_penalty_per_day is not None else None
    )
    days_of_float = _days_of_float(production.get("due_date"), generated_at, master)
    variance_minutes = _number(production.get("schedule_variance_minutes"))
    projected_delay_minutes = (variance_minutes or 0.0) + downtime_minutes
    at_risk_of_penalty = (
        days_of_float is not None
        and projected_delay_minutes > days_of_float * 24 * 60
    )
    penalty_exposure = (
        penalty_per_day if at_risk_of_penalty and penalty_per_day is not None else 0.0
    ) if penalty_per_day is not None else None

    # Grace period: the Supervisor Agent resolves it, but it is recomputable from the
    # buffer and the rate, which is what §E18 rule 9 requires the deadline to respect.
    grace = _number(cascade.get("grace_period_minutes"))
    if grace is None:
        buffer_units = _number(cascade.get("downstream_buffer_units"))
        if buffer_units is None:
            buffer_units = _number(machine.downstream_buffer_units)
        if buffer_units is not None and rate:
            grace = round((buffer_units / rate) * 60.0, 1)

    document: dict[str, Any] = {
        "affected_run": production.get("run_code") or production.get("run"),
        "product": production.get("product"),
        "customer": production.get("customer"),
        "customer_tier": tier,
        "units_at_risk": units_at_risk,
        "margin_at_risk": margin_at_risk,
        "margin_per_unit": margin_per_unit,
        "downtime_cost": downtime_cost,
        "downtime_cost_rule": None if cost_rule is None else cost_rule.code,
        "downtime_hours": round(downtime_hours, 2) if downtime_hours else None,
        "penalty_per_day": penalty_per_day,
        "penalty_exposure": penalty_exposure,
        "days_of_float": days_of_float,
        "grace_period_minutes": grace,
        "schedule_variance_minutes": variance_minutes,
        "is_bottleneck": bool(
            cascade.get("is_bottleneck", machine.is_bottleneck)),
        "line": line.production_line_code,
        "line_criticality": line.criticality.value,
        "reroute_options": _reroute_options(master, product, line),
        "applied_escalation_threshold": business.get("escalation_threshold"),
    }

    impact = Impact(
        document=document,
        applied_rules=cited,
        downtime_minutes=downtime_minutes,
        grace_period_minutes=grace,
        days_of_float=days_of_float,
    )
    _set_priority(context, impact, facts, tier, root_cause)
    return impact


def _set_priority(
    context: DecisionContext,
    impact: Impact,
    facts: PredictionFacts,
    tier: str | None,
    root_cause: Candidate | None,
) -> None:
    """Weight the calibrated probability by business context, then map to severity."""
    score = facts.failure_probability
    weights: list[str] = []

    if tier == "gold" and context.gold_weight is not None:
        score *= context.gold_weight.value
        impact.applied_rules.append(context.gold_weight)
        weights.append(context.gold_weight.cite())

    if (
        root_cause is not None
        and root_cause.has_safety_implication
        and context.safety_weight is not None
    ):
        score *= context.safety_weight.value
        impact.applied_rules.append(context.safety_weight)
        weights.append(context.safety_weight.cite())

    band = context.band_for(score)

    # The floor: business context may raise urgency, never lower it.
    predicted = context.master.severities.get(facts.risk_severity_level_id)
    if predicted is not None and predicted.severity_rank < band.severity_rank:
        level = predicted
    else:
        level = context.master.severities[band.severity_level_id]

    impact.weighted_score = round(score, 4)
    impact.priority_severity_level_id = level.failure_severity_level_id
    impact.priority_severity_code = level.failure_severity_level_code
    impact.priority_severity_name = level.severity_name
    impact.document["priority"] = {
        "severity": level.failure_severity_level_code,
        "base_failure_probability": facts.failure_probability,
        "weighted_score": impact.weighted_score,
        "weights_applied": weights,
        "band_rule": band.code,
        "floored_at_prediction_severity": (
            predicted is not None and predicted.severity_rank < band.severity_rank
        ),
    }


def _reroute_options(
    master: MasterSnapshot,
    product: Any,
    line: ProductionLine,
) -> list[dict[str, Any]]:
    """Alternative production routes for the product, excluding the affected line.

    An empty list is a real answer and the documented one for ``PRD-GH-100``: §E18's
    example reports "none -- PRD-GH-100 has no qualified alternative production route",
    and notes approvingly that "the Decision Agent checked and reported the absence
    rather than inventing an option".
    """
    if product is None:
        return []
    options: list[dict[str, Any]] = []
    for capability in master.capabilities_by_product.get(product.product_id, []):
        if capability.capability_type.value != "production_route":
            continue
        if capability.production_line_id == line.production_line_id:
            continue
        alternate = master.lines.get(capability.production_line_id)
        if alternate is None:
            continue
        options.append({
            "line": alternate.production_line_code,
            "rate": float(capability.max_hourly_output_units),
            "changeover_minutes": int(capability.changeover_minutes),
            "qualified": bool(capability.is_qualified and capability.tooling_available),
        })
    options.sort(key=lambda option: option["line"])
    return options


# ------------------------------------------------------------------ small helpers


def _block(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    return value if isinstance(value, dict) else {}


def _number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _days_of_float(
    due: Any,
    generated_at: datetime,
    master: MasterSnapshot,
) -> int | None:
    """Whole days between now and the run's due date, in plant local time."""
    if due is None:
        return None
    if isinstance(due, datetime):
        due_date = due.astimezone(master.timezone).date()
    elif isinstance(due, date):
        due_date = due
    else:
        try:
            due_date = date.fromisoformat(str(due)[:10])
        except ValueError:
            return None
    return (due_date - generated_at.astimezone(master.timezone).date()).days
