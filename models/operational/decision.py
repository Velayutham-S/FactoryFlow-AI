"""Operational models O17-O19: the escalation gate, the recommendation, the response.

``AiRecommendation`` has no failure probability column, and that absence is the
point. The overview requires ML confidence in every recommendation and requires
that it originate from the Prediction Agent and never be restated by the LLM. This
layer enforces it structurally: ``prediction_result_id`` is NOT NULL and there is
nowhere to write a probability. A convention would have been circumvented
eventually; a missing column cannot be. DO NOT ADD A PROBABILITY COLUMN (§O18).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import (
    EscalationDecision,
    RecommendationActionType,
    RejectionReason,
    RootCauseConfidence,
)
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    require_timezone_aware,
)
from models.types import JsonDoc, OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.failure import FailureCategory, FailureSeverityLevel
    from models.master.inventory import InventoryItem
    from models.master.people import MaintenanceEngineer, MaintenanceTeam, Worker
    from models.master.plant import Shift
    from models.master.production import ProductionLine
    from models.master.thresholds import BusinessRule
    from models.operational.events import OperationalAlert
    from models.operational.maintenance import MaintenanceWorkRecord
    from models.operational.notification import Notification
    from models.operational.prediction import PredictionResult
    from models.operational.production import ProductionRun


class SupervisorContext(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``supervisor_context``, operational group: the escalation decision and
    the context package assembled for it. The audit trail of the cost and noise gate.

    A row is written either way -- escalated or suppressed. With suppressions
    recorded, "the machine was showing symptoms, why didn't the system tell me?"
    has an answer that points at the threshold, not at the platform (§O17).
    """

    __tablename__ = "supervisor_context"
    __table_args__ = (
        UniqueConstraint("supervisor_context_code", name="uq_sc_code"),
        CheckConstraint(
            "supervisor_context_code GLOB "
            "'CTX-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9]'",
            name=conv("ck_sc_code_format")),
        # Read the next two together: they make context_document present exactly
        # when escalated and absent otherwise, which is both the correctness rule
        # and the cost-control rule (§O17).
        CheckConstraint(
            "escalation_decision <> 'escalated' "
            "OR context_document IS NOT NULL",
            name=conv("ck_sc_escalated_requires_context")),
        CheckConstraint(
            "escalation_decision = 'escalated' OR context_document IS NULL",
            name=conv("ck_sc_suppressed_has_no_context")),
        # Both verdicts turn on a threshold and both must name it.
        CheckConstraint(
            "escalation_decision NOT IN ('escalated', "
            "'suppressed_below_threshold') "
            "OR applied_escalation_rule_id IS NOT NULL",
            name=conv("ck_sc_threshold_decisions_name_rule")),
        CheckConstraint("length(trim(escalation_rationale)) > 0",
                        name=conv("ck_sc_rationale_not_blank")),
        CheckConstraint("context_assembly_duration_ms >= 0",
                        name=conv("ck_sc_assembly_duration_non_negative")),
        CheckConstraint(
            "context_document IS NULL OR (json_valid(context_document) "
            "AND json_type(context_document) = 'object')",
            name=conv("ck_sc_context_document_is_object")),
        CheckConstraint(
            "related_alert_codes IS NULL OR (json_valid(related_alert_codes) "
            "AND json_type(related_alert_codes) = 'array')",
            name=conv("ck_sc_related_alerts_is_array")),
        CheckConstraint(
            "escalation_decision IN ('escalated', "
            "'suppressed_below_threshold', 'suppressed_duplicate', "
            "'suppressed_maintenance_in_progress', 'suppressed_rate_limited', "
            "'suppressed_insufficient_data')",
            name=conv("ck_sc_escalation_decision_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_sc_created_by_component_allowed")),
        CheckConstraint("length(supervisor_context_code) <= 20",
                        name=conv("ck_sc_supervisor_context_code_length")),
        {"sqlite_autoincrement": True},
    )

    supervisor_context_id: Mapped[OperationalPk]
    supervisor_context_code: Mapped[str] = mapped_column(String(20))
    machine_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_sc_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_sc_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    assembled_at: Mapped[TimestampTz]
    triggering_alert_id: Mapped[int] = mapped_column(
        ForeignKey("operational_alert.operational_alert_id",
                   name="fk_sc_triggering_alert",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # NULL when an alert had no prediction, which is itself a reason not to
    # escalate.
    triggering_prediction_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("prediction_result.prediction_result_id",
                   name="fk_sc_triggering_prediction",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Holds alert *codes*, deliberately not foreign keys, so a correlated alert
    # can be cited without pinning it against purge. The ORM resolves nothing.
    related_alert_codes: Mapped[Optional[list[Any]]]
    escalation_decision: Mapped[EscalationDecision] = mapped_column(
        Enum(EscalationDecision, name="escalation_decision",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Referenced, never copied: master data records superseded rule values as new
    # rows rather than editing in place, so a reference is sufficient (§O17).
    applied_escalation_rule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("business_rule.business_rule_id",
                   name="fk_sc_escalation_rule",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    escalation_rationale: Mapped[str] = mapped_column(Text)
    # Preserved exactly as the Decision Agent received it. Reassembling it at
    # audit time would reflect current data rather than what was known at the
    # decision moment. This is the input side of the explainability contract.
    context_document: Mapped[Optional[JsonDoc]]
    context_assembly_duration_ms: Mapped[int] = mapped_column(Integer)
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_sc_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # The six many-to-one relationships are unidirectional (§O17).
    machine: Mapped[Optional["Machine"]] = relationship(lazy="select")
    production_line: Mapped[Optional["ProductionLine"]] = relationship(
        lazy="select"
    )
    triggering_alert: Mapped["OperationalAlert"] = relationship(lazy="select")
    triggering_prediction: Mapped[Optional["PredictionResult"]] = relationship(
        lazy="select"
    )
    applied_escalation_rule: Mapped[Optional["BusinessRule"]] = relationship(
        lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")
    ai_recommendation: Mapped[Optional["AiRecommendation"]] = relationship(
        back_populates="supervisor_context", lazy="select", uselist=False
    )

    @validates("supervisor_context_code", "machine_id", "production_line_id",
               "assembled_at", "triggering_alert_id",
               "triggering_prediction_id", "related_alert_codes",
               "escalation_decision", "applied_escalation_rule_id",
               "escalation_rationale", "context_document",
               "context_assembly_duration_ms", "shift_id", "machine",
               "production_line", "triggering_alert", "triggering_prediction",
               "applied_escalation_rule", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``assembled_at`` timezone-aware (§41.3, §41.4)."""
        if inspect(self).persistent:
            raise ValueError(
                "supervisor_context is append-only; %s cannot be reassigned "
                "once the row is persistent" % key
            )
        if key == "assembled_at":
            return require_timezone_aware(key, value)
        if key == "supervisor_context_code":
            return value.strip().upper()
        return value


class AiRecommendation(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``ai_recommendation``, operational group: the Decision Agent's output and
    the platform's actual product. Append-only and immutable.

    ``root_cause_failure_category_id`` is NOT NULL and references the controlled
    failure taxonomy: the LLM classifies within a validated set rather than
    generating free-form causes, which is what makes the root cause checkable by an
    engineer, matchable to a maintenance specialisation, and linkable to a spare
    part (§O18).
    """

    __tablename__ = "ai_recommendation"
    __table_args__ = (
        UniqueConstraint("ai_recommendation_code", name="uq_ar_code"),
        # One recommendation per escalated context. Enforces the one-to-one and
        # prevents duplicate reasoning on the same package -- belt and braces on
        # the platform's most expensive operation (§O18).
        UniqueConstraint("supervisor_context_id",
                         name="uq_ar_supervisor_context"),
        CheckConstraint(
            "ai_recommendation_code GLOB "
            "'REC-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9]'",
            name=conv("ck_ar_code_format")),
        CheckConstraint("length(trim(recommended_action)) > 0",
                        name=conv("ck_ar_recommended_action_not_blank")),
        CheckConstraint("length(trim(recovery_plan)) > 0",
                        name=conv("ck_ar_recovery_plan_not_blank")),
        CheckConstraint("length(trim(reasoning_narrative)) > 0",
                        name=conv("ck_ar_reasoning_narrative_not_blank")),
        CheckConstraint(
            "json_valid(supporting_evidence) "
            "AND json_type(supporting_evidence) = 'object'",
            name=conv("ck_ar_supporting_evidence_is_object")),
        CheckConstraint(
            "json_valid(business_impact) "
            "AND json_type(business_impact) = 'object'",
            name=conv("ck_ar_business_impact_is_object")),
        CheckConstraint(
            "recommended_action_by IS NULL "
            "OR recommended_action_by > generated_at",
            name=conv("ck_ar_action_deadline_after_generation")),
        CheckConstraint(
            "estimated_downtime_minutes IS NULL "
            "OR estimated_downtime_minutes > 0",
            name=conv("ck_ar_estimated_downtime_positive")),
        CheckConstraint("generation_duration_ms >= 0",
                        name=conv("ck_ar_generation_duration_non_negative")),
        CheckConstraint(
            "(prompt_token_count IS NULL OR prompt_token_count >= 0) "
            "AND (completion_token_count IS NULL "
            "OR completion_token_count >= 0)",
            name=conv("ck_ar_token_counts_non_negative")),
        CheckConstraint(
            "root_cause_confidence IN ('high', 'moderate', 'low')",
            name=conv("ck_ar_root_cause_confidence_allowed")),
        CheckConstraint("contract_complete IN (0, 1)",
                        name=conv("ck_ar_contract_complete_bool")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_ar_created_by_component_allowed")),
        CheckConstraint("length(ai_recommendation_code) <= 20",
                        name=conv("ck_ar_ai_recommendation_code_length")),
        CheckConstraint("length(llm_model_name) <= 60",
                        name=conv("ck_ar_llm_model_name_length")),
        CheckConstraint("length(llm_model_version) <= 40",
                        name=conv("ck_ar_llm_model_version_length")),
        {"sqlite_autoincrement": True},
    )

    ai_recommendation_id: Mapped[OperationalPk]
    ai_recommendation_code: Mapped[str] = mapped_column(String(20))
    supervisor_context_id: Mapped[int] = mapped_column(
        ForeignKey("supervisor_context.supervisor_context_id",
                   name="fk_ar_supervisor_context",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # The ML confidence element, BY REFERENCE. NOT NULL is what makes the
    # probability unforgeable, and the absence of a probability column here is
    # what makes it unrestatable.
    prediction_result_id: Mapped[int] = mapped_column(
        ForeignKey("prediction_result.prediction_result_id",
                   name="fk_ar_prediction_result",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_ar_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[int] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_ar_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_ar_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    generated_at: Mapped[TimestampTz]
    llm_model_name: Mapped[str] = mapped_column(String(60))
    # Quality attribution: a change in recommendation quality must be traceable
    # to a model change.
    llm_model_version: Mapped[str] = mapped_column(String(40))
    priority_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_ar_priority_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    root_cause_failure_category_id: Mapped[int] = mapped_column(
        ForeignKey("failure_category.failure_category_id",
                   name="fk_ar_root_cause_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Forces a stated hypothesis strength rather than an implied one.
    root_cause_confidence: Mapped[RootCauseConfidence] = mapped_column(
        Enum(RootCauseConfidence, name="root_cause_confidence",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Contract element 1. Shape is the Decision Agent's contract, not the ORM's.
    supporting_evidence: Mapped[JsonDoc]
    # Contract element 4.
    business_impact: Mapped[JsonDoc]
    # Contract element 5.
    recommended_action: Mapped[str] = mapped_column(Text)
    recovery_plan: Mapped[str] = mapped_column(Text)
    suggested_maintenance_team_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_team.maintenance_team_id",
                   name="fk_ar_suggested_team",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    suggested_engineer_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_engineer.maintenance_engineer_id",
                   name="fk_ar_suggested_engineer",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    required_inventory_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_ar_required_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    estimated_downtime_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    recommended_action_by: Mapped[Optional[TimestampTz]]
    # What a manager reads to decide whether to trust it.
    reasoning_narrative: Mapped[str] = mapped_column(Text)
    # All five §16.5 elements produced. A recommendation with 0 here must not be
    # delivered as final. No default: the Decision Agent must assert it.
    contract_complete: Mapped[bool]
    generation_duration_ms: Mapped[int] = mapped_column(Integer)
    # Cost monitoring -- the metric that proves the escalation gate pays for
    # itself.
    prompt_token_count: Mapped[Optional[int]] = mapped_column(Integer)
    completion_token_count: Mapped[Optional[int]] = mapped_column(Integer)
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_ar_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    supervisor_context: Mapped["SupervisorContext"] = relationship(
        back_populates="ai_recommendation", lazy="select"
    )
    # The ten remaining many-to-one relationships are unidirectional (§O18).
    prediction_result: Mapped["PredictionResult"] = relationship(lazy="select")
    machine: Mapped["Machine"] = relationship(lazy="select")
    production_line: Mapped["ProductionLine"] = relationship(lazy="select")
    production_run: Mapped[Optional["ProductionRun"]] = relationship(
        lazy="select"
    )
    priority_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        lazy="select"
    )
    root_cause_failure_category: Mapped["FailureCategory"] = relationship(
        lazy="select"
    )
    suggested_maintenance_team: Mapped[Optional["MaintenanceTeam"]] = relationship(
        lazy="select"
    )
    suggested_engineer: Mapped[Optional["MaintenanceEngineer"]] = relationship(
        lazy="select"
    )
    required_inventory_item: Mapped[Optional["InventoryItem"]] = relationship(
        lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")
    actions: Mapped[list["RecommendationAction"]] = relationship(
        back_populates="ai_recommendation", lazy="select"
    )
    notifications: Mapped[list["Notification"]] = relationship(
        back_populates="ai_recommendation", lazy="select"
    )

    @validates("ai_recommendation_code", "supervisor_context_id",
               "prediction_result_id", "machine_id", "production_line_id",
               "production_run_id", "generated_at", "llm_model_name",
               "llm_model_version", "priority_severity_level_id",
               "root_cause_failure_category_id", "root_cause_confidence",
               "supporting_evidence", "business_impact", "recommended_action",
               "recovery_plan", "suggested_maintenance_team_id",
               "suggested_engineer_id", "required_inventory_item_id",
               "estimated_downtime_minutes", "recommended_action_by",
               "reasoning_narrative", "contract_complete",
               "generation_duration_ms", "prompt_token_count",
               "completion_token_count", "shift_id", "supervisor_context",
               "prediction_result", "machine", "production_line",
               "production_run", "priority_severity_level",
               "root_cause_failure_category", "suggested_maintenance_team",
               "suggested_engineer", "required_inventory_item", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and both timestamps timezone-aware (§41.3, §41.4).

        The human response lives in ``recommendation_action`` precisely so the
        recommendation stays untouched. An editable recommendation could be
        quietly improved after the fact, destroying the audit trail entirely.
        """
        if inspect(self).persistent:
            raise ValueError(
                "ai_recommendation is immutable; %s cannot be reassigned. The "
                "human response is a separate row" % key
            )
        if key in ("generated_at", "recommended_action_by"):
            return require_timezone_aware(key, value)
        if key == "ai_recommendation_code":
            return value.strip().upper()
        return value


class RecommendationAction(TimestampCreatedMixin, ComponentProvenanceMixin,
                           Base):
    """Table ``recommendation_action``, operational group: what the human actually
    decided. Append-only.

    The least regenerable table in the database: it records human judgement that
    exists nowhere else and cannot be reconstructed from any other data. There is
    deliberately no unique constraint on ``ai_recommendation_id`` -- a deferral
    followed by an acceptance is two decisions and both are recorded, with the
    latest by ``actioned_at`` operative (§O19).
    """

    __tablename__ = "recommendation_action"
    __table_args__ = (
        CheckConstraint("response_time_minutes >= 0",
                        name=conv("ck_ra_response_time_non_negative")),
        # An unexplained modification is lost feedback.
        CheckConstraint(
            "action_taken <> 'accepted_with_modification' "
            "OR modification_note IS NOT NULL",
            name=conv("ck_ra_modification_note_required")),
        # Rejections are the platform's most valuable improvement signal and an
        # unexplained one teaches nothing.
        CheckConstraint(
            "action_taken <> 'rejected' "
            "OR (rejection_reason IS NOT NULL AND rejection_note IS NOT NULL)",
            name=conv("ck_ra_rejection_fields_required")),
        # The less obvious half: prevents an accepted recommendation carrying a
        # stale rejection reason, which would corrupt the aggregate that drives
        # threshold and prompt tuning.
        CheckConstraint(
            "action_taken = 'rejected' "
            "OR (rejection_reason IS NULL AND rejection_note IS NULL)",
            name=conv("ck_ra_rejection_fields_absent")),
        CheckConstraint(
            "action_taken <> 'deferred' OR deferred_until IS NOT NULL",
            name=conv("ck_ra_deferred_until_required")),
        CheckConstraint(
            "deferred_until IS NULL OR deferred_until > actioned_at",
            name=conv("ck_ra_deferred_until_future")),
        CheckConstraint(
            "action_taken IN ('accepted', 'accepted_with_modification', "
            "'rejected', 'deferred', 'superseded', 'no_action_taken')",
            name=conv("ck_ra_action_taken_allowed")),
        CheckConstraint(
            "rejection_reason IS NULL OR rejection_reason IN "
            "('disagree_with_diagnosis', 'impractical_timing', "
            "'resource_unavailable', 'already_addressed', "
            "'insufficient_evidence', 'business_priority_conflict')",
            name=conv("ck_ra_rejection_reason_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_ra_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    recommendation_action_id: Mapped[OperationalPk]
    ai_recommendation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_recommendation.ai_recommendation_id",
                   name="fk_ra_recommendation",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    action_taken: Mapped[RecommendationActionType] = mapped_column(
        Enum(RecommendationActionType, name="recommendation_action_type",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    actioned_at: Mapped[TimestampTz]
    # Accountability. Must hold a role with the relevant authority flag, which
    # reads worker_role and is therefore the Dashboard's check (§41.3).
    actioned_by_worker_id: Mapped[int] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_ra_actioned_by",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # The platform's real effectiveness measure.
    response_time_minutes: Mapped[int] = mapped_column(Integer)
    # The richest feedback the platform receives.
    modification_note: Mapped[Optional[str]] = mapped_column(Text)
    rejection_reason: Mapped[Optional[RejectionReason]] = mapped_column(
        Enum(RejectionReason, name="rejection_reason", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    rejection_note: Mapped[Optional[str]] = mapped_column(Text)
    deferred_until: Mapped[Optional[TimestampTz]]
    # Proves a recommendation produced action.
    resulting_work_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_work_record.maintenance_work_record_id",
                   name="fk_ra_resulting_work_record",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_ra_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    ai_recommendation: Mapped["AiRecommendation"] = relationship(
        back_populates="actions", lazy="select"
    )
    actioned_by: Mapped["Worker"] = relationship(lazy="select")
    resulting_work_record: Mapped[Optional["MaintenanceWorkRecord"]] = relationship(
        lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")

    @validates("ai_recommendation_id", "action_taken", "actioned_at",
               "actioned_by_worker_id", "response_time_minutes",
               "modification_note", "rejection_reason", "rejection_note",
               "deferred_until", "resulting_work_record_id", "shift_id",
               "ai_recommendation", "actioned_by", "resulting_work_record",
               "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and both timestamps timezone-aware (§41.3, §41.4).

        A change of mind is a NEW action row, so the decision sequence stays
        visible.
        """
        if inspect(self).persistent:
            raise ValueError(
                "recommendation_action is append-only; %s cannot be "
                "reassigned. A change of mind is a new action row" % key
            )
        if key in ("actioned_at", "deferred_until"):
            return require_timezone_aware(key, value)
        return value
