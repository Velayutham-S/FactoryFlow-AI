"""Operational models O13-O14: the detected condition, and the correlated case.

The split between immutable observation and mutable managed case. A degrading
bearing produces dozens of events over hours; correlating them into one alert is
what separates monitoring from noise (§O13, §O14).

``OperationalEvent.threshold_value_breached`` is the only place in the entire
database where a master value is copied. An event is quoted into LLM prompts and
notification bodies and must read as self-contained evidence: the rule is referenced
for lineage, the value is captured for evidence (§O13).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import (
    AlertResolutionType,
    AlertStatus,
    AlertSuppressionReason,
    EventCategory,
    EventType,
    ThresholdDirection,
)
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    TimestampUpdatedMixin,
    require_timezone_aware,
)
from models.types import Measurement, OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.failure import FailureSeverityLevel
    from models.master.inventory import InventoryItem
    from models.master.parameters import MachineParameter
    from models.master.people import Worker
    from models.master.plant import Shift
    from models.master.production import ProductionLine
    from models.master.thresholds import AlertThresholdRule
    from models.operational.production import ProductionRun
    from models.operational.telemetry import MachineSensorReading


class OperationalAlert(TimestampCreatedMixin, ComponentProvenanceMixin,
                       TimestampUpdatedMixin, Base):
    """Table ``operational_alert``, operational group: the correlated, managed case
    grouping many events for one condition, with its own lifecycle."""

    __tablename__ = "operational_alert"
    __table_args__ = (
        UniqueConstraint("operational_alert_code", name="uq_oa_code"),
        CheckConstraint(
            "operational_alert_code GLOB "
            "'ALR-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9]'",
            name=conv("ck_oa_code_format")),
        CheckConstraint("event_count >= 1",
                        name=conv("ck_oa_event_count_positive")),
        CheckConstraint("opened_at = first_event_at",
                        name=conv("ck_oa_opened_equals_first_event")),
        CheckConstraint("last_event_at >= first_event_at",
                        name=conv("ck_oa_last_event_not_before_first")),
        CheckConstraint(
            "(acknowledged_at IS NULL OR acknowledged_at >= opened_at) "
            "AND (escalated_at IS NULL OR escalated_at >= opened_at) "
            "AND (resolved_at IS NULL OR resolved_at >= opened_at) "
            "AND (closed_at IS NULL OR closed_at >= opened_at) "
            "AND (closed_at IS NULL OR resolved_at IS NULL "
            "OR closed_at >= resolved_at)",
            name=conv("ck_oa_timestamp_sequence")),
        CheckConstraint(
            "(acknowledged_at IS NULL) = (acknowledged_by_worker_id IS NULL)",
            name=conv("ck_oa_acknowledged_paired")),
        CheckConstraint(
            "alert_status NOT IN ('resolved', 'closed') "
            "OR resolution_type IS NOT NULL",
            name=conv("ck_oa_resolution_type_required")),
        CheckConstraint(
            "alert_status <> 'closed' OR resolution_note IS NOT NULL",
            name=conv("ck_oa_closed_requires_note")),
        CheckConstraint(
            "resolution_type IS NULL OR resolution_type <> 'false_positive' "
            "OR resolution_note IS NOT NULL",
            name=conv("ck_oa_false_positive_requires_note")),
        CheckConstraint(
            "alert_status <> 'suppressed' OR suppression_reason IS NOT NULL",
            name=conv("ck_oa_suppression_reason_required")),
        CheckConstraint("length(trim(correlation_key)) > 0",
                        name=conv("ck_oa_correlation_key_not_blank")),
        CheckConstraint(
            "alert_category IN ('machine_condition', 'machine_output', "
            "'quality', 'inventory', 'data_quality')",
            name=conv("ck_oa_alert_category_allowed")),
        CheckConstraint(
            "alert_status IN ('open', 'acknowledged', 'escalated', 'resolved', "
            "'closed', 'suppressed')",
            name=conv("ck_oa_alert_status_allowed")),
        CheckConstraint(
            "resolution_type IS NULL OR resolution_type IN ('auto_recovered', "
            "'maintenance_performed', 'false_positive', 'superseded', "
            "'manual_close')",
            name=conv("ck_oa_resolution_type_allowed")),
        CheckConstraint(
            "suppression_reason IS NULL OR suppression_reason IN "
            "('maintenance_in_progress', 'machine_offline', "
            "'planned_downtime', 'duplicate_condition', 'rate_limited')",
            name=conv("ck_oa_suppression_reason_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_oa_created_by_component_allowed")),
        CheckConstraint("length(operational_alert_code) <= 20",
                        name=conv("ck_oa_operational_alert_code_length")),
        CheckConstraint("length(correlation_key) <= 120",
                        name=conv("ck_oa_correlation_key_length")),
        # THE ALERT-STORM PREVENTION MECHANISM, and a correctness constraint
        # rather than a performance one: without it two events arriving
        # milliseconds apart could each find no open alert and each create one,
        # and correlation would silently degrade under load. Closed alerts may
        # legitimately share a key with a new open one, which is why the
        # predicate is required (§37.9).
        Index(
            "uq_oa_open_correlation_key",
            "correlation_key",
            unique=True,
            sqlite_where=text(
                "alert_status IN ('open', 'acknowledged', 'escalated')"
            ),
        ),
        {"sqlite_autoincrement": True},
    )

    operational_alert_id: Mapped[OperationalPk]
    operational_alert_code: Mapped[str] = mapped_column(String(20))
    correlation_key: Mapped[str] = mapped_column(String(120))
    # Shared vocabulary with OperationalEvent.event_category: correlation depends
    # on an alert's category matching its events' exactly, which one shared class
    # guarantees and two could not (§40.2).
    alert_category: Mapped[EventCategory] = mapped_column(
        Enum(EventCategory, name="event_category", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    machine_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_oa_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_oa_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    inventory_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_oa_inventory_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    initial_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_oa_initial_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # May only become more severe over an alert's life. That is a transition rule
    # comparing a row to its previous version, so it is application-enforced;
    # allowing de-escalation would hide deterioration (§O14).
    current_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_oa_current_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    alert_status: Mapped[AlertStatus] = mapped_column(
        Enum(AlertStatus, name="alert_status", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        server_default=text("'open'"),
    )
    # A maintained counter, reconcilable against the events collection. The ORM
    # derives nothing from the collection (§41.3).
    event_count: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    opened_at: Mapped[TimestampTz]
    first_event_at: Mapped[TimestampTz]
    # Advances as events correlate in.
    last_event_at: Mapped[TimestampTz]
    acknowledged_at: Mapped[Optional[TimestampTz]]
    acknowledged_by_worker_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_oa_acknowledged_by",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    escalated_at: Mapped[Optional[TimestampTz]]
    resolved_at: Mapped[Optional[TimestampTz]]
    resolution_type: Mapped[Optional[AlertResolutionType]] = mapped_column(
        Enum(AlertResolutionType, name="alert_resolution_type",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    closed_at: Mapped[Optional[TimestampTz]]
    suppression_reason: Mapped[Optional[AlertSuppressionReason]] = mapped_column(
        Enum(AlertSuppressionReason, name="alert_suppression_reason",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    resolution_note: Mapped[Optional[str]] = mapped_column(Text)

    # The six many-to-one relationships are unidirectional (§O14).
    machine: Mapped[Optional["Machine"]] = relationship(lazy="select")
    production_line: Mapped[Optional["ProductionLine"]] = relationship(
        lazy="select"
    )
    inventory_item: Mapped[Optional["InventoryItem"]] = relationship(
        lazy="select"
    )
    # Two keys to FailureSeverityLevel in two roles: initial and current.
    initial_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        lazy="select", foreign_keys=[initial_severity_level_id]
    )
    current_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        lazy="select", foreign_keys=[current_severity_level_id]
    )
    acknowledged_by: Mapped[Optional["Worker"]] = relationship(lazy="select")
    events: Mapped[list["OperationalEvent"]] = relationship(
        back_populates="operational_alert", lazy="select"
    )

    @validates("operational_alert_code", "correlation_key", "alert_category",
               "initial_severity_level_id", "initial_severity_level",
               "opened_at", "first_event_at", "last_event_at",
               "acknowledged_at", "escalated_at", "resolved_at", "closed_at")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Partial immutability plus timezone-awareness (§41.3, §41.4).

        The mutable set is ``current_severity_level_id``, ``alert_status``,
        ``event_count``, ``last_event_at`` and the lifecycle timestamps.
        """
        if key in ("operational_alert_code", "correlation_key",
                   "alert_category", "initial_severity_level_id",
                   "initial_severity_level", "opened_at",
                   "first_event_at") and inspect(self).persistent:
            raise ValueError(
                "operational_alert.%s is immutable after insert; it is the "
                "alert's identity and origin" % key
            )
        if key in ("opened_at", "first_event_at", "last_event_at",
                   "acknowledged_at", "escalated_at", "resolved_at",
                   "closed_at"):
            return require_timezone_aware(key, value)
        if key == "operational_alert_code":
            return value.strip().upper()
        return value


class OperationalEvent(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``operational_event``, operational group: one detected condition as an
    immutable fact, with the evidence that produced it.

    Four nullable *typed* subject keys rather than a polymorphic
    scope_type/scope_ref pair: the extra columns buy full referential integrity,
    which a generic reference cannot have (§O13).
    """

    __tablename__ = "operational_event"
    __table_args__ = (
        UniqueConstraint("operational_event_code", name="uq_oe_code"),
        CheckConstraint(
            "operational_event_code GLOB "
            "'EVT-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9]'",
            name=conv("ck_oe_code_format")),
        CheckConstraint(
            "event_category NOT IN ('machine_condition', 'machine_output', "
            "'data_quality') OR machine_id IS NOT NULL",
            name=conv("ck_oe_machine_subject_required")),
        CheckConstraint(
            "event_category <> 'inventory' OR inventory_item_id IS NOT NULL",
            name=conv("ck_oe_inventory_subject_required")),
        CheckConstraint(
            "event_category <> 'quality' "
            "OR (machine_id IS NOT NULL AND production_run_id IS NOT NULL)",
            name=conv("ck_oe_quality_subject_required")),
        # An event claiming a breach without stating what was breached is not
        # evidence. The most valuable constraint on this table.
        CheckConstraint(
            "event_type NOT IN ('threshold_warning', 'threshold_critical', "
            "'rate_of_change_exceeded') "
            "OR (machine_parameter_id IS NOT NULL "
            "AND alert_threshold_rule_id IS NOT NULL "
            "AND observed_value IS NOT NULL "
            "AND threshold_value_breached IS NOT NULL "
            "AND threshold_direction IS NOT NULL)",
            name=conv("ck_oe_threshold_event_complete")),
        CheckConstraint(
            "sustained_duration_seconds IS NULL "
            "OR sustained_duration_seconds >= 0",
            name=conv("ck_oe_sustained_duration_non_negative")),
        CheckConstraint(
            "event_category IN ('machine_condition', 'machine_output', "
            "'quality', 'inventory', 'data_quality')",
            name=conv("ck_oe_event_category_allowed")),
        CheckConstraint(
            "event_type IN ('threshold_warning', 'threshold_critical', "
            "'rate_of_change_exceeded', 'sustained_deviation', "
            "'output_shortfall', 'cycle_deviation', 'scrap_rate_exceeded', "
            "'quality_failure_rate', 'reorder_point_reached', "
            "'safety_stock_breached', 'sensor_out_of_range', "
            "'telemetry_stale')",
            name=conv("ck_oe_event_type_allowed")),
        CheckConstraint(
            "threshold_direction IS NULL OR threshold_direction IN "
            "('above_high', 'below_low', 'rate_exceeded')",
            name=conv("ck_oe_threshold_direction_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_oe_created_by_component_allowed")),
        CheckConstraint("length(operational_event_code) <= 20",
                        name=conv("ck_oe_operational_event_code_length")),
        {"sqlite_autoincrement": True},
    )

    operational_event_id: Mapped[OperationalPk]
    operational_event_code: Mapped[str] = mapped_column(String(20))
    operational_alert_id: Mapped[int] = mapped_column(
        ForeignKey("operational_alert.operational_alert_id",
                   name="fk_oe_alert",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    event_category: Mapped[EventCategory] = mapped_column(
        Enum(EventCategory, name="event_category", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    event_type: Mapped[EventType] = mapped_column(
        Enum(EventType, name="event_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    detected_at: Mapped[TimestampTz]
    severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_oe_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_oe_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_oe_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_oe_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    inventory_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_oe_inventory_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_parameter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine_parameter.machine_parameter_id",
                   name="fk_oe_machine_parameter",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Referenced for lineage, not copied.
    alert_threshold_rule_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("alert_threshold_rule.alert_threshold_rule_id",
                   name="fk_oe_threshold_rule",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    observed_value: Mapped[Optional[Measurement]]
    # The one place in the database where a master value is copied, because an
    # event must read as self-contained evidence (§O13).
    threshold_value_breached: Mapped[Optional[Measurement]]
    threshold_direction: Mapped[Optional[ThresholdDirection]] = mapped_column(
        Enum(ThresholdDirection, name="threshold_direction",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    sustained_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer)
    # THE SINGLE FOREIGN KEY IN THIS DATABASE THAT IS NOT ON DELETE RESTRICT.
    # Telemetry is purged at 90 days; events are retained a year or more.
    # RESTRICT would either pin individual readings in otherwise purgeable
    # ranges or fail the purge outright. SET NULL is safe ONLY because
    # observed_value, threshold_value_breached and detected_at are captured onto
    # the event itself: when the reading is purged the lineage pointer becomes
    # NULL and the evidence survives intact (§32.3, §48.4 of the schema doc).
    triggering_reading_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine_sensor_reading.machine_sensor_reading_id",
                   name="fk_oe_triggering_reading",
                   ondelete="SET NULL", onupdate="RESTRICT")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_oe_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    detection_note: Mapped[Optional[str]] = mapped_column(Text)

    operational_alert: Mapped["OperationalAlert"] = relationship(
        back_populates="events", lazy="select"
    )
    # The other nine are unidirectional (§O13).
    severity_level: Mapped["FailureSeverityLevel"] = relationship(lazy="select")
    machine: Mapped[Optional["Machine"]] = relationship(lazy="select")
    production_line: Mapped[Optional["ProductionLine"]] = relationship(
        lazy="select"
    )
    production_run: Mapped[Optional["ProductionRun"]] = relationship(
        lazy="select"
    )
    inventory_item: Mapped[Optional["InventoryItem"]] = relationship(
        lazy="select"
    )
    machine_parameter: Mapped[Optional["MachineParameter"]] = relationship(
        lazy="select"
    )
    alert_threshold_rule: Mapped[Optional["AlertThresholdRule"]] = relationship(
        lazy="select"
    )
    triggering_reading: Mapped[Optional["MachineSensorReading"]] = relationship(
        lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")

    @validates("operational_event_code", "operational_alert_id",
               "event_category", "event_type", "detected_at",
               "severity_level_id", "machine_id", "production_line_id",
               "production_run_id", "inventory_item_id",
               "machine_parameter_id", "alert_threshold_rule_id",
               "observed_value", "threshold_value_breached",
               "threshold_direction", "sustained_duration_seconds",
               "triggering_reading_id", "shift_id", "detection_note",
               "operational_alert", "severity_level", "machine",
               "production_line", "production_run", "inventory_item",
               "machine_parameter", "alert_threshold_rule",
               "triggering_reading", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only with no exception, and ``detected_at`` timezone-aware.

        The strictest immutability in the ORM layer: an event is evidence, and
        evidence that can be edited is not evidence. The schema reinforces it by
        omitting ``updated_at`` entirely (§41.4).
        """
        if inspect(self).persistent:
            raise ValueError(
                "operational_event is append-only; %s cannot be reassigned "
                "once the row is persistent" % key
            )
        if key == "detected_at":
            return require_timezone_aware(key, value)
        if key == "operational_event_code":
            return value.strip().upper()
        return value
