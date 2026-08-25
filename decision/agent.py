"""The Decision Agent. Transition T4: an escalated context becomes a recommendation.

Owns exactly one table (§6.2 #18): ``ai_recommendation``. It writes nothing else -- no
context, no notification, no work order, and deliberately not
``recommendation_action``, which §6.4 assigns to the Dashboard because "ownership follows
the actor" and a component must not record the verdict on its own advice.

**The cycle, in the order §46.5 requires:**

.. code-block:: text

    read escalated contexts        one query, then the read is closed
    compute every settled figure   deterministic, database-driven, no LLM
    ONE model call                 outside any transaction
    per recommendation: T-DEC-1    one row, one transaction, commit or roll back

"The LLM call happens outside the transaction, and this matters: an LLM call taking four
seconds inside an open transaction would hold a snapshot for four seconds on every
reasoning cycle." Batching the whole cycle into one call makes that window shorter still.

**Root cause is resolved twice, on purpose.** Before the call, an anchor candidate is
taken from the prediction's own attributed failure mode so the prompt carries real
figures. After the call, if the model classified differently -- within the candidate set
-- the part, downtime, team, engineer and deadline are recomputed against the mode it
chose, because §E18 rules 7 and 8 tie those values to the *root cause*, not to the
prediction. The recomputation touches no database.

**A rejected classification does not become a silent substitution.** If the model returns
a category outside the declared set, the anchor is kept, confidence drops to ``low``, and
``contract_complete`` is set to 0 -- which §E18 rule 5 defines as "must not be delivered
as final and must be flagged for review". That is the documented mechanism for exactly
this case, so it is used rather than an exception that would discard four other valid
recommendations in the same cycle.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from models.master import Machine, ProductionLine
from models.operational import (
    AiRecommendation,
    PredictionResult,
    ProductionRun,
    SupervisorContext,
)
from models.session import initialize_database, session_scope, shutdown_database

from decision.assignment import Assignment, resolve as resolve_assignment
from decision.context import (
    CONTEXT_BLOCKS,
    DECISION_COMPONENT,
    DecisionContext,
)
from decision.errors import ContextDocumentInvalidError, DecisionError
from decision.evidence import (
    Candidate,
    Evidence,
    PredictionFacts,
    build_evidence,
    cap_confidence,
    prediction_facts,
    root_cause_candidates,
    summarise,
)
from decision.impact import Impact, assess
from decision.reasoning import (
    GroqReasoner,
    ReasoningRequest,
    ReasoningResult,
)


@dataclass
class Situation:
    """One escalated context, with every deterministic figure already settled."""

    context_id: int
    context_code: str
    assembled_at: datetime
    document: dict[str, Any]
    machine: Machine
    line: ProductionLine
    production_run_id: int | None
    facts: PredictionFacts
    evidence: Evidence
    candidates: list[Candidate]
    anchor: Candidate | None
    assignment: Assignment
    impact: Impact
    generated_at: datetime


@dataclass
class CycleReport:
    """What one decision cycle produced."""

    contexts_considered: int = 0
    already_recommended: int = 0
    recommendations: int = 0
    contract_complete: int = 0
    flagged_for_review: int = 0
    llm_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    codes: list[str] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=dict)

    def skip(self, reason: str) -> None:
        self.skipped[reason] = self.skipped.get(reason, 0) + 1


class DecisionAgent:
    """Turns escalated contexts into explainable recommendations."""

    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[Session],
        *,
        reasoner: Any | None = None,
        env_path: str | Path | None = None,
        quiet: bool = False,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.quiet = quiet
        # Constructed eagerly so a missing key fails before any work is done, and
        # injectable so the deterministic half can be exercised on its own.
        self.reasoner = reasoner if reasoner is not None else GroqReasoner(
            env_path=env_path)
        with session_scope(session_factory) as session:
            self.context = DecisionContext(session)

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    # ----------------------------------------------------------------- the cycle

    def run_cycle(self, *, limit: int | None = None) -> CycleReport:
        """Reason over every escalated context that has no recommendation yet."""
        report = CycleReport()

        # Phase 1 - read. Closed before the model is called (§46.5).
        situations = self._prepare(report, limit=limit)
        if not situations:
            return report

        # Phase 2 - one model call for the whole cycle, outside any transaction.
        batch = self.reasoner.reason([
            ReasoningRequest(
                reference=situation.context_code,
                facts=self._settled_facts(situation),
                candidates=[
                    {
                        "code": candidate.failure_category_code,
                        "name": candidate.category_name,
                        "relative_frequency": candidate.frequency,
                        "leading_indicator": candidate.leading_indicator,
                    }
                    for candidate in situation.candidates
                ],
            )
            for situation in situations
        ])
        report.llm_calls = 1
        report.prompt_tokens = batch.prompt_tokens or 0
        report.completion_tokens = batch.completion_tokens or 0

        # Phase 3 - one T-DEC-1 transaction per recommendation. One call served every
        # situation, so its cost is apportioned evenly across them: recording the batch
        # total on each row would overstate the price of each recommendation, and
        # prompt_token_count exists precisely so the escalation gate's economics can be
        # checked rather than asserted (§E18).
        share = max(len(situations), 1)
        prompt_share = None if batch.prompt_tokens is None else (
            batch.prompt_tokens // share)
        completion_share = None if batch.completion_tokens is None else (
            batch.completion_tokens // share)

        for situation in situations:
            result = batch.results.get(situation.context_code)
            if result is None:
                report.skip("no_model_result")
                continue
            with session_scope(self.session_factory) as session:
                row = self._assemble(
                    situation, result, batch.duration_ms,
                    prompt_tokens=prompt_share,
                    completion_tokens=completion_share)
                session.add(row)
                session.flush()
                report.recommendations += 1
                report.codes.append(row.ai_recommendation_code)
                if row.contract_complete:
                    report.contract_complete += 1
                else:
                    report.flagged_for_review += 1
        return report

    # -------------------------------------------------------------------- phase 1

    def _prepare(
        self,
        report: CycleReport,
        *,
        limit: int | None,
    ) -> list[Situation]:
        """Load escalated contexts and settle every figure that is not language."""
        situations: list[Situation] = []
        with session_scope(self.session_factory) as session:
            recommended = set(session.scalars(
                select(AiRecommendation.supervisor_context_id)))

            query = (
                select(SupervisorContext)
                .where(SupervisorContext.escalation_decision == "escalated")
                .order_by(SupervisorContext.assembled_at,
                          SupervisorContext.supervisor_context_id)
            )
            for row in session.scalars(query):
                report.contexts_considered += 1

                # uq_ar_supervisor_context enforces this too; checking first keeps a
                # re-run from raising on rows that are simply already done.
                if row.supervisor_context_id in recommended:
                    report.already_recommended += 1
                    continue
                if limit is not None and len(situations) >= limit:
                    break

                try:
                    situations.append(self._build_situation(session, row))
                except DecisionError as exc:
                    report.skip(type(exc).__name__)
                    self.say("  %s skipped: %s" % (row.supervisor_context_code, exc))
        return situations

    def _build_situation(
        self,
        session: Session,
        row: SupervisorContext,
    ) -> Situation:
        master = self.context.master
        document = row.context_document
        if not isinstance(document, dict) or not document:
            raise ContextDocumentInvalidError(
                "%s escalated with no context_document; §E17 rule 2 requires one"
                % row.supervisor_context_code)
        missing = [name for name in CONTEXT_BLOCKS if name not in document]
        if missing:
            raise ContextDocumentInvalidError(
                "%s is missing the %s block(s); §E17 rule 10 requires all seven, and a "
                "missing block means the corresponding §16.5 element cannot be produced"
                % (row.supervisor_context_code, ", ".join(missing)))

        if row.triggering_prediction_id is None:
            raise ContextDocumentInvalidError(
                "%s escalated with no triggering prediction. "
                "ai_recommendation.prediction_result_id is NOT NULL because ML "
                "confidence is referenced and never restated, so no recommendation can "
                "be formed" % row.supervisor_context_code)
        prediction = session.get(PredictionResult, row.triggering_prediction_id)
        if prediction is None:
            raise ContextDocumentInvalidError(
                "%s references prediction %d, which does not exist"
                % (row.supervisor_context_code, row.triggering_prediction_id))
        facts = prediction_facts(prediction)

        machine = self._resolve_machine(row, document)
        line = self._resolve_line(row, document, machine)

        evidence = build_evidence(document, facts)
        candidates = root_cause_candidates(master, machine)
        anchor = self._anchor_candidate(candidates, facts)

        # generated_at is the context's own instant. The CHECK requires
        # generated_at >= assembled_at, and anchoring to the data rather than to
        # wall-clock latency keeps a cycle reproducible; the real latency is recorded
        # separately in generation_duration_ms, which is where it belongs.
        generated_at = row.assembled_at

        assignment = resolve_assignment(
            self.context, document, machine, facts, anchor,
            generated_at=generated_at, grace_period_minutes=None)
        impact = assess(
            self.context, document, machine, line, facts, anchor,
            downtime_minutes=assignment.estimated_downtime_minutes or 0,
            generated_at=generated_at)
        # The deadline needs the grace period, which the impact assessment resolves.
        assignment = resolve_assignment(
            self.context, document, machine, facts, anchor,
            generated_at=generated_at,
            grace_period_minutes=impact.grace_period_minutes)

        return Situation(
            context_id=row.supervisor_context_id,
            context_code=row.supervisor_context_code,
            assembled_at=row.assembled_at,
            document=document,
            machine=machine,
            line=line,
            production_run_id=self._resolve_run(session, document),
            facts=facts,
            evidence=evidence,
            candidates=candidates,
            anchor=anchor,
            assignment=assignment,
            impact=impact,
            generated_at=generated_at,
        )

    def _resolve_machine(
        self,
        row: SupervisorContext,
        document: dict[str, Any],
    ) -> Machine:
        """``ai_recommendation.machine_id`` is NOT NULL, so a subject is required."""
        master = self.context.master
        if row.machine_id is not None:
            machine = master.machines.get(row.machine_id)
            if machine is not None:
                return machine
        code = str(_block(document, "machine").get("machine_code") or "").strip()
        machine = master.machines_by_code.get(code)
        if machine is None:
            raise ContextDocumentInvalidError(
                "%s names no resolvable machine; ai_recommendation.machine_id is NOT "
                "NULL" % row.supervisor_context_code)
        return machine

    def _resolve_line(
        self,
        row: SupervisorContext,
        document: dict[str, Any],
        machine: Machine,
    ) -> ProductionLine:
        """``production_line_id`` is NOT NULL; the machine's own line is the fallback."""
        master = self.context.master
        if row.production_line_id is not None:
            line = master.lines.get(row.production_line_id)
            if line is not None:
                return line
        code = str(_block(document, "production").get("line") or "").strip()
        line = master.lines_by_code.get(code) or master.lines.get(
            machine.production_line_id)
        if line is None:
            raise ContextDocumentInvalidError(
                "%s names no resolvable production line"
                % row.supervisor_context_code)
        return line

    @staticmethod
    def _resolve_run(
        session: Session,
        document: dict[str, Any],
    ) -> int | None:
        """Resolve the run code the context names into its key. Optional column."""
        code = str(
            _block(document, "production").get("run_code")
            or _block(document, "production").get("run")
            or ""
        ).strip()
        if not code:
            return None
        return session.scalars(
            select(ProductionRun.production_run_id).where(
                ProductionRun.production_run_code == code)
        ).first()

    @staticmethod
    def _anchor_candidate(
        candidates: list[Candidate],
        facts: PredictionFacts,
    ) -> Candidate | None:
        """The deterministic starting point for root cause.

        Preference order: the mode the Prediction Agent itself attributed, then its
        predicted category, then the most frequent declared mode. All three come from
        stored rows, so the prompt always carries a real part and a real repair estimate
        even before the model classifies.
        """
        if not candidates:
            return None
        if facts.machine_type_failure_mode_id is not None:
            for candidate in candidates:
                if (candidate.mode.machine_type_failure_mode_id
                        == facts.machine_type_failure_mode_id):
                    return candidate
        if facts.predicted_failure_category_id is not None:
            for candidate in candidates:
                if candidate.failure_category_id == facts.predicted_failure_category_id:
                    return candidate
        return candidates[0]

    def _settled_facts(self, situation: Situation) -> dict[str, Any]:
        """Everything the model may quote and must not change.

        ``supporting_evidence`` carries the aggregated projection
        :func:`~decision.evidence.summarise` produces, not the full evidence document.
        The full document is what gets persisted on the row; what the model needs is which
        parameters breached, how far past their limits, over what window, and what
        corroborates them. Sending one object per correlated sample instead put the prompt
        past the provider's per-minute token ceiling for no gain in reasoning quality.
        """
        assignment = situation.assignment
        worker = (
            None if assignment.engineer is None
            else self.context.master.workers.get(assignment.engineer.worker_id)
        )
        return {
            "machine": situation.machine.machine_code,
            "machine_name": situation.machine.machine_name,
            "is_bottleneck": bool(situation.machine.is_bottleneck),
            "production_line": situation.line.production_line_code,
            "failure_probability": situation.facts.failure_probability,
            "prediction_result": situation.facts.code,
            "prediction_horizon_hours": situation.facts.prediction_horizon_hours,
            "priority_severity": situation.impact.priority_severity_code,
            "priority_severity_name": situation.impact.priority_severity_name,
            "supporting_evidence": summarise(situation.evidence),
            "independent_measurement_paths": situation.evidence.measurement_paths,
            "maximum_confidence_supported": situation.evidence.confidence_ceiling,
            "business_impact": situation.impact.document,
            "suggested_team": (
                None if assignment.team is None
                else assignment.team.maintenance_team_code),
            "team_response_target_minutes": (
                None if assignment.team is None
                else int(assignment.team.target_response_time_minutes)),
            "suggested_engineer": (
                None if assignment.engineer is None
                else assignment.engineer.maintenance_engineer_code),
            "suggested_engineer_name": (
                None if worker is None
                else "%s %s" % (worker.first_name, worker.last_name)),
            "engineers_excluded": assignment.engineer_rejections,
            "required_part": (
                None if assignment.item is None
                else assignment.item.inventory_item_code),
            "part_quantity_on_hand": assignment.quantity_on_hand,
            "part_lead_time_days": assignment.lead_time_days,
            "part_retrieval_minutes": assignment.retrieval_minutes,
            "repair_minutes": assignment.repair_minutes,
            "estimated_downtime_minutes": assignment.estimated_downtime_minutes,
            "act_by": (
                None if assignment.action_by is None
                else assignment.action_by.astimezone(
                    self.context.master.timezone).isoformat(timespec="minutes")),
            "deadline_basis": assignment.deadline_basis,
            "combinable_schedule": assignment.combinable_schedule,
            "business_rules_applied": sorted({
                rule.code for rule in situation.impact.applied_rules}),
        }

    # -------------------------------------------------------------------- phase 3

    def _assemble(
        self,
        situation: Situation,
        result: ReasoningResult,
        duration_ms: int,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
    ) -> AiRecommendation:
        """Build the row, recomputing anything the model's classification changed."""
        master = self.context.master
        by_code = {c.failure_category_code: c for c in situation.candidates}
        chosen = by_code.get(result.root_cause_code)
        classification_valid = chosen is not None
        if chosen is None:
            chosen = situation.anchor

        assignment = situation.assignment
        impact = situation.impact
        if (
            classification_valid
            and situation.anchor is not None
            and chosen is not None
            and chosen.failure_category_id != situation.anchor.failure_category_id
        ):
            assignment = resolve_assignment(
                self.context, situation.document, situation.machine, situation.facts,
                chosen, generated_at=situation.generated_at,
                grace_period_minutes=impact.grace_period_minutes)
            impact = assess(
                self.context, situation.document, situation.machine, situation.line,
                situation.facts, chosen,
                downtime_minutes=assignment.estimated_downtime_minutes or 0,
                generated_at=situation.generated_at)

        if chosen is None:
            raise ContextDocumentInvalidError(
                "%s: no failure mode is declared for machine type %d, so §E18 rule 3 "
                "leaves no permissible root cause and root_cause_failure_category_id "
                "is NOT NULL" % (situation.context_code,
                                 situation.machine.machine_type_id))

        confidence = (
            cap_confidence(result.root_cause_confidence,
                           situation.evidence.confidence_ceiling)
            if classification_valid else "low"
        )

        action = result.recommended_action.strip()
        recovery = result.recovery_plan.strip()
        narrative = result.reasoning_narrative.strip()
        complete = bool(
            situation.evidence.is_substantive
            and impact.is_substantive
            and classification_valid
            and action and recovery and narrative
            and assignment.team is not None
        )

        evidence_document = dict(situation.evidence.document)
        evidence_document["root_cause_classification"] = {
            "chosen": chosen.failure_category_code,
            "within_declared_modes": classification_valid,
            "candidates_offered": [
                c.failure_category_code for c in situation.candidates],
            "model_returned": result.root_cause_code,
        }

        return AiRecommendation(
            ai_recommendation_code=self.context.recommendation_code(
                situation.generated_at),
            supervisor_context_id=situation.context_id,
            prediction_result_id=situation.facts.prediction_result_id,
            machine_id=situation.machine.machine_id,
            production_line_id=situation.line.production_line_id,
            production_run_id=situation.production_run_id,
            generated_at=situation.generated_at,
            llm_model_name=self.reasoner.model_name,
            llm_model_version=self.reasoner.model_version,
            priority_severity_level_id=impact.priority_severity_level_id,
            root_cause_failure_category_id=chosen.failure_category_id,
            root_cause_confidence=confidence,
            supporting_evidence=evidence_document,
            business_impact=impact.document,
            recommended_action=action or "Review required: no action was produced.",
            recovery_plan=recovery or "Review required: no recovery plan was produced.",
            suggested_maintenance_team_id=assignment.team_id,
            suggested_engineer_id=assignment.engineer_id,
            required_inventory_item_id=assignment.item_id,
            estimated_downtime_minutes=assignment.estimated_downtime_minutes,
            recommended_action_by=assignment.action_by,
            reasoning_narrative=(
                narrative or "Review required: no narrative was produced."),
            contract_complete=complete,
            generation_duration_ms=max(duration_ms, 0),
            prompt_token_count=(
                None if prompt_tokens is None else max(prompt_tokens, 0)),
            completion_token_count=(
                None if completion_tokens is None else max(completion_tokens, 0)),
            shift_id=master.shift_at(situation.generated_at).shift_id,
            created_by_component=DECISION_COMPONENT,
        )

    # ------------------------------------------------------------------ reporting

    def summary(self) -> dict[str, int]:
        with session_scope(self.session_factory) as session:
            return {
                "ai_recommendation": int(session.execute(
                    select(func.count()).select_from(AiRecommendation)).scalar_one()),
                "escalated_contexts": int(session.execute(
                    select(func.count()).select_from(SupervisorContext).where(
                        SupervisorContext.escalation_decision == "escalated")
                ).scalar_one()),
            }


def _block(document: dict[str, Any], name: str) -> dict[str, Any]:
    value = document.get(name)
    return value if isinstance(value, dict) else {}


def decide(
    database_path: str | Path,
    *,
    limit: int | None = None,
    reasoner: Any | None = None,
    env_path: str | Path | None = None,
    quiet: bool = False,
) -> CycleReport:
    """Run one decision cycle against a database."""
    engine, session_factory = initialize_database(database_path)
    try:
        agent = DecisionAgent(
            engine, session_factory, reasoner=reasoner, env_path=env_path, quiet=quiet)
        agent.say("Decision Agent — %s" % agent.reasoner.model_version)
        report = agent.run_cycle(limit=limit)

        agent.say("")
        agent.say(
            "Contexts: %d escalated, %d already recommended, %d reasoned"
            % (report.contexts_considered, report.already_recommended,
               report.recommendations))
        for reason, count in sorted(report.skipped.items()):
            agent.say("  %-34s %d" % (reason, count))
        if report.recommendations:
            agent.say(
                "Recommendations: %d complete, %d flagged for review"
                % (report.contract_complete, report.flagged_for_review))
            agent.say(
                "LLM: %d call(s), %d prompt tokens, %d completion tokens"
                % (report.llm_calls, report.prompt_tokens, report.completion_tokens))
            for code in report.codes:
                agent.say("  %s" % code)

        totals = agent.summary()
        agent.say("")
        agent.say("Decision output:")
        agent.say("  %-34s %d" % ("ai_recommendation", totals["ai_recommendation"]))
        agent.say("")
        agent.say("Decision Complete.")
        return report
    finally:
        shutdown_database(engine)


def main(argv: list[str] | None = None) -> int:
    """``python -m decision <database-path> [max-recommendations]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        print(
            "usage: python -m decision <database-path> [max-recommendations]",
            file=sys.stderr,
        )
        return 2
    path = Path(args[0]).resolve()
    limit = int(args[1]) if len(args) > 1 else None
    try:
        decide(path, limit=limit)
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        print("", file=sys.stderr)
        print("Decision failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0
