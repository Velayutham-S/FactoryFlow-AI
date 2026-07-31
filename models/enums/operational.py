"""The 31 operational-group controlled vocabularies (§40.1).

Values are transcribed from FACTORY_SQLITE_DATABASE_SCHEMA.md §37.6 character
for character. Members are ordered as that catalogue lists them, not
alphabetically (§40.7). Classes are ordered alphabetically within the module.

This package imports nothing but the standard library (§44.4).
"""

from enum import Enum


class AlertResolutionType(str, Enum):
    """Vocabulary ``alert_resolution_type``; bound by ck_oa_resolution_type_allowed."""

    AUTO_RECOVERED = "auto_recovered"
    MAINTENANCE_PERFORMED = "maintenance_performed"
    FALSE_POSITIVE = "false_positive"
    SUPERSEDED = "superseded"
    MANUAL_CLOSE = "manual_close"


class AlertStatus(str, Enum):
    """Vocabulary ``alert_status``; bound by ck_oa_alert_status_allowed.

    The first three members are the predicate of uq_oa_open_correlation_key, the
    alert-storm prevention index (§37.9).
    """

    OPEN = "open"
    ACKNOWLEDGED = "acknowledged"
    ESCALATED = "escalated"
    RESOLVED = "resolved"
    CLOSED = "closed"
    SUPPRESSED = "suppressed"


class AlertSuppressionReason(str, Enum):
    """Vocabulary ``alert_suppression_reason``;
    bound by ck_oa_suppression_reason_allowed.

    Deliberately separate from NotificationSuppressionReason: an alert
    suppressed because maintenance is in progress is not the same fact as a
    notification suppressed for quiet hours (§37.6).
    """

    MAINTENANCE_IN_PROGRESS = "maintenance_in_progress"
    MACHINE_OFFLINE = "machine_offline"
    PLANNED_DOWNTIME = "planned_downtime"
    DUPLICATE_CONDITION = "duplicate_condition"
    RATE_LIMITED = "rate_limited"


class CycleOutcome(str, Enum):
    """Vocabulary ``cycle_outcome``; bound by ck_ch_cycle_outcome_allowed."""

    GOOD = "good"
    SCRAP = "scrap"
    REWORK = "rework"


class DeliveryChannel(str, Enum):
    """Vocabulary ``delivery_channel``; bound by ck_nd_channel_allowed."""

    EMAIL = "email"
    WHATSAPP = "whatsapp"


class DeliveryFailureReason(str, Enum):
    """Vocabulary ``delivery_failure_reason``; bound by ck_nd_failure_reason_allowed."""

    INVALID_ADDRESS = "invalid_address"
    PROVIDER_ERROR = "provider_error"
    TIMEOUT = "timeout"
    RATE_LIMITED_BY_PROVIDER = "rate_limited_by_provider"
    RECIPIENT_BLOCKED = "recipient_blocked"
    MESSAGE_TOO_LARGE = "message_too_large"


class DeliveryStatus(str, Enum):
    """Vocabulary ``delivery_status``; bound by ck_nd_delivery_status_allowed.

    SENT and DELIVERED are deliberately distinct: the gap between handing a
    message to a provider and the provider confirming receipt is where silent
    failures live (§O21).
    """

    QUEUED = "queued"
    SENT = "sent"
    DELIVERED = "delivered"
    FAILED = "failed"
    BOUNCED = "bounced"
    REJECTED = "rejected"


class EscalationDecision(str, Enum):
    """Vocabulary ``escalation_decision``; bound by ck_sc_escalation_decision_allowed.

    Suppression reasons are enumerated so the platform's silence is always
    explicable (§O17).
    """

    ESCALATED = "escalated"
    SUPPRESSED_BELOW_THRESHOLD = "suppressed_below_threshold"
    SUPPRESSED_DUPLICATE = "suppressed_duplicate"
    SUPPRESSED_MAINTENANCE_IN_PROGRESS = "suppressed_maintenance_in_progress"
    SUPPRESSED_RATE_LIMITED = "suppressed_rate_limited"
    SUPPRESSED_INSUFFICIENT_DATA = "suppressed_insufficient_data"


class EventCategory(str, Enum):
    """Vocabulary ``event_category``;
    shared by OperationalEvent and OperationalAlert (§40.2).

    Correlation depends on an alert's category matching its events' exactly,
    which is why one class binds both columns.
    """

    MACHINE_CONDITION = "machine_condition"
    MACHINE_OUTPUT = "machine_output"
    QUALITY = "quality"
    INVENTORY = "inventory"
    DATA_QUALITY = "data_quality"


class EventType(str, Enum):
    """Vocabulary ``event_type``; bound by ck_oe_event_type_allowed."""

    THRESHOLD_WARNING = "threshold_warning"
    THRESHOLD_CRITICAL = "threshold_critical"
    RATE_OF_CHANGE_EXCEEDED = "rate_of_change_exceeded"
    SUSTAINED_DEVIATION = "sustained_deviation"
    OUTPUT_SHORTFALL = "output_shortfall"
    CYCLE_DEVIATION = "cycle_deviation"
    SCRAP_RATE_EXCEEDED = "scrap_rate_exceeded"
    QUALITY_FAILURE_RATE = "quality_failure_rate"
    REORDER_POINT_REACHED = "reorder_point_reached"
    SAFETY_STOCK_BREACHED = "safety_stock_breached"
    SENSOR_OUT_OF_RANGE = "sensor_out_of_range"
    TELEMETRY_STALE = "telemetry_stale"


class InspectionDisposition(str, Enum):
    """Vocabulary ``inspection_disposition``; bound by ck_qir_disposition_allowed.

    Separate from the finding, because a failed inspection does not
    automatically mean scrap (§O8).
    """

    ACCEPT = "accept"
    REWORK = "rework"
    SCRAP = "scrap"
    QUARANTINE = "quarantine"


class InspectionType(str, Enum):
    """Vocabulary ``inspection_type``; bound by ck_qir_inspection_type_allowed."""

    FIRST_ARTICLE = "first_article"
    IN_PROCESS = "in_process"
    FINAL = "final"
    AUDIT = "audit"


class InventoryMovementType(str, Enum):
    """Vocabulary ``inventory_movement_type``; bound by ck_im_movement_type_allowed.

    ck_im_delta_sign_matches_type divides these into inbound, outbound and
    ADJUSTMENT, which may go either way (§O10).
    """

    RECEIPT = "receipt"
    ISSUE_PRODUCTION = "issue_production"
    ISSUE_MAINTENANCE = "issue_maintenance"
    RETURN = "return"
    ADJUSTMENT = "adjustment"
    SCRAP_CONSUMPTION = "scrap_consumption"
    TRANSFER_OUT = "transfer_out"
    TRANSFER_IN = "transfer_in"


class MachineOperationalState(str, Enum):
    """Vocabulary ``machine_operational_state``; bound on O2 and twice on O3.

    STARVED and BLOCKED are distinguished because they mean opposite things
    about where the constraint is (§O2).
    """

    RUNNING = "running"
    IDLE = "idle"
    SETUP = "setup"
    STARVED = "starved"
    BLOCKED = "blocked"
    DOWN_UNPLANNED = "down_unplanned"
    DOWN_PLANNED = "down_planned"
    OFFLINE = "offline"


class MaintenanceActivityType(str, Enum):
    """Vocabulary ``maintenance_activity_type``; bound by ck_mma_activity_type_allowed.

    This vocabulary is what makes interval measurement possible (§O12).
    """

    DISPATCHED = "dispatched"
    ARRIVED = "arrived"
    DIAGNOSIS_STARTED = "diagnosis_started"
    DIAGNOSIS_COMPLETE = "diagnosis_complete"
    PART_REQUESTED = "part_requested"
    PART_COLLECTED = "part_collected"
    REPAIR_STARTED = "repair_started"
    REPAIR_COMPLETE = "repair_complete"
    TEST_RUN = "test_run"
    HANDOVER = "handover"
    ESCALATED = "escalated"
    ON_HOLD = "on_hold"
    RESUMED = "resumed"


class MaintenanceWorkStatus(str, Enum):
    """Vocabulary ``maintenance_work_status``; bound by ck_mwr_work_status_allowed."""

    OPEN = "open"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    AWAITING_PARTS = "awaiting_parts"
    COMPLETED = "completed"
    CLOSED = "closed"
    CANCELLED = "cancelled"


class MaintenanceWorkType(str, Enum):
    """Vocabulary ``maintenance_work_type``; bound by ck_mwr_work_type_allowed.

    PREDICTIVE means the job exists because FactoryFlow AI recommended it, which
    is the platform's value metric (§O11).
    """

    PREVENTIVE = "preventive"
    PREDICTIVE = "predictive"
    CORRECTIVE = "corrective"
    EMERGENCY = "emergency"
    CALIBRATION = "calibration"
    INSPECTION = "inspection"


class NotificationSuppressionReason(str, Enum):
    """Vocabulary ``notification_suppression_reason``;
    bound by ck_nt_suppression_reason_allowed."""

    QUIET_HOURS = "quiet_hours"
    RATE_LIMITED = "rate_limited"
    BELOW_MIN_SEVERITY = "below_min_severity"
    RECIPIENT_INACTIVE = "recipient_inactive"
    CHANNEL_UNAVAILABLE = "channel_unavailable"
    ALREADY_ACKNOWLEDGED = "already_acknowledged"


class NotificationType(str, Enum):
    """Vocabulary ``notification_type``; bound by ck_nt_notification_type_allowed."""

    RECOMMENDATION = "recommendation"
    ALERT_ESCALATION = "alert_escalation"
    ACKNOWLEDGEMENT_REMINDER = "acknowledgement_reminder"
    INVENTORY_WARNING = "inventory_warning"
    SYSTEM_HEALTH = "system_health"


class ReadingQualityFlag(str, Enum):
    """Vocabulary ``reading_quality_flag``; bound by ck_msr_quality_flag_allowed."""

    VALID = "valid"
    OUT_OF_PHYSICAL_RANGE = "out_of_physical_range"
    SENSOR_OFFLINE = "sensor_offline"
    INTERPOLATED = "interpolated"
    STALE = "stale"


class RecommendationActionType(str, Enum):
    """Vocabulary ``recommendation_action_type``; bound by ck_ra_action_taken_allowed.

    ACCEPTED_WITH_MODIFICATION is the most informative value and the commonest
    real outcome; NO_ACTION_TAKEN records the platform's worst (§O19).
    """

    ACCEPTED = "accepted"
    ACCEPTED_WITH_MODIFICATION = "accepted_with_modification"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    SUPERSEDED = "superseded"
    NO_ACTION_TAKEN = "no_action_taken"


class RejectionReason(str, Enum):
    """Vocabulary ``rejection_reason``; bound by ck_ra_rejection_reason_allowed.

    Enumerated so rejections aggregate into an improvement signal (§O19).
    """

    DISAGREE_WITH_DIAGNOSIS = "disagree_with_diagnosis"
    IMPRACTICAL_TIMING = "impractical_timing"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    ALREADY_ADDRESSED = "already_addressed"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    BUSINESS_PRIORITY_CONFLICT = "business_priority_conflict"


class RootCauseConfidence(str, Enum):
    """Vocabulary ``root_cause_confidence``;
    bound by ck_ar_root_cause_confidence_allowed."""

    HIGH = "high"
    MODERATE = "moderate"
    LOW = "low"


class RunPauseReason(str, Enum):
    """Vocabulary ``run_pause_reason``; bound by ck_pr_pause_reason_allowed."""

    MACHINE_DOWN = "machine_down"
    MATERIAL_SHORTAGE = "material_shortage"
    QUALITY_HOLD = "quality_hold"
    OPERATOR_UNAVAILABLE = "operator_unavailable"
    SHIFT_END = "shift_end"
    HIGHER_PRIORITY_RUN = "higher_priority_run"


class RunPriority(str, Enum):
    """Vocabulary ``run_priority``; bound by ck_pr_priority_allowed."""

    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class RunStatus(str, Enum):
    """Vocabulary ``run_status``; bound by ck_pr_run_status_allowed.

    SETUP, RUNNING and PAUSED are the predicate of
    uq_production_run_active_per_line (§37.9).
    """

    PLANNED = "planned"
    SETUP = "setup"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class ScrapReason(str, Enum):
    """Vocabulary ``scrap_reason``; bound by ck_sr_scrap_reason_allowed.

    MACHINE_FAULT requires attribution and MATERIAL_DEFECT / HANDLING_DAMAGE
    forbid it, which together keep the preventable-loss metric honest (§O9).
    """

    DIMENSIONAL_DEVIATION = "dimensional_deviation"
    SURFACE_DEFECT = "surface_defect"
    TOOL_MARK = "tool_mark"
    MATERIAL_DEFECT = "material_defect"
    SETUP_REJECT = "setup_reject"
    MACHINE_FAULT = "machine_fault"
    HANDLING_DAMAGE = "handling_damage"
    PROCESS_DEVIATION = "process_deviation"


class SnapshotInsufficiencyReason(str, Enum):
    """Vocabulary ``snapshot_insufficiency_reason``;
    bound by ck_pfs_insufficiency_reason_allowed."""

    COMPLETENESS_BELOW_THRESHOLD = "completeness_below_threshold"
    SENSOR_FAULT = "sensor_fault"
    MACHINE_NOT_RUNNING = "machine_not_running"
    WINDOW_SPANS_MAINTENANCE = "window_spans_maintenance"
    INSUFFICIENT_HISTORY = "insufficient_history"


class SnapshotScope(str, Enum):
    """Vocabulary ``snapshot_scope``; bound by ck_ds_snapshot_scope_allowed."""

    PLANT = "plant"
    PRODUCTION_LINE = "production_line"
    MACHINE = "machine"


class StateTransitionReason(str, Enum):
    """Vocabulary ``state_transition_reason``; bound by ck_mst_reason_code_allowed.

    This is what turns a state log into a diagnostic record (§O3).
    """

    RUN_START = "run_start"
    RUN_COMPLETE = "run_complete"
    CHANGEOVER = "changeover"
    TOOL_CHANGE = "tool_change"
    UPSTREAM_STARVATION = "upstream_starvation"
    DOWNSTREAM_BLOCKAGE = "downstream_blockage"
    BREAKDOWN = "breakdown"
    PLANNED_MAINTENANCE = "planned_maintenance"
    QUALITY_HOLD = "quality_hold"
    OPERATOR_UNAVAILABLE = "operator_unavailable"
    SHIFT_END = "shift_end"
    RESTORED = "restored"
    ASSET_STATUS_CHANGE = "asset_status_change"


class ThresholdDirection(str, Enum):
    """Vocabulary ``threshold_direction``;
    bound by ck_oe_threshold_direction_allowed."""

    ABOVE_HIGH = "above_high"
    BELOW_LOW = "below_low"
    RATE_EXCEEDED = "rate_exceeded"
