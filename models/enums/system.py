"""The 4 system-group controlled vocabularies (§40.1).

Values are transcribed from FACTORY_SQLITE_DATABASE_SCHEMA.md §37.6 character
for character. Members are ordered as that catalogue lists them (§40.7).

This package imports nothing but the standard library (§44.4).
"""

from enum import Enum


class AuditActionType(str, Enum):
    """Vocabulary ``audit_action_type``; bound by ck_al_action_type_allowed."""

    ENTITY_CREATED = "entity_created"
    ENTITY_UPDATED = "entity_updated"
    STATE_TRANSITION = "state_transition"
    DECISION_MADE = "decision_made"
    HUMAN_ACTION = "human_action"
    CONFIGURATION_CHANGED = "configuration_changed"
    COMPONENT_ERROR = "component_error"
    RETENTION_PURGE = "retention_purge"
    RECONCILIATION_RUN = "reconciliation_run"


class AuditOutcome(str, Enum):
    """Vocabulary ``audit_outcome``; bound by ck_al_outcome_allowed."""

    SUCCESS = "success"
    FAILURE = "failure"
    DENIED = "denied"


class ComponentHealthStatus(str, Enum):
    """Vocabulary ``component_health_status``; bound by ck_shs_health_status_allowed."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    FAILED = "failed"
    STOPPED = "stopped"


class PlatformComponent(str, Enum):
    """Vocabulary ``platform_component``; the most widely used in the schema (§40.2).

    26 columns bind this one class: AuditLog.component,
    SystemHealthStatus.component, and ``created_by_component`` on all 24
    operational models, which ComponentProvenanceMixin supplies. It is the only
    enum a mixin may reference (§16 R2, §34).
    """

    SIMULATOR = "simulator"
    MONITORING_AGENT = "monitoring_agent"
    PREDICTION_AGENT = "prediction_agent"
    SUPERVISOR_AGENT = "supervisor_agent"
    DECISION_AGENT = "decision_agent"
    NOTIFICATION_SERVICE = "notification_service"
    DASHBOARD = "dashboard"
    PLATFORM = "platform"
