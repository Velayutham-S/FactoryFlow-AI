"""Operational models O4-O7: the run, and the three grains of its output.

Four deliberately different grains, none derivable from a coarser one: the run is
the commitment, ``production_progress`` is the periodic roll-up, ``production_count``
is the per-machine interval tally, and ``cycle_history`` is the individual cycle
where sub-second variance is the degradation signal (§O4-§O7).
"""

from datetime import date
from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
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
    CycleOutcome,
    RunPauseReason,
    RunPriority,
    RunStatus,
)
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    TimestampUpdatedMixin,
    require_timezone_aware,
)
from models.types import (
    OperationalPk,
    Percent,
    Quantity,
    Rate,
    Seconds2,
    SignedPercent,
    TimestampTz,
)

if TYPE_CHECKING:
    from models.master.commercial import Customer
    from models.master.equipment import Machine
    from models.master.plant import Shift
    from models.master.production import Product, ProductionLine, ProductLineCapability
    from models.operational.quality import QualityInspectionResult, ScrapRecord


class ProductionRun(TimestampCreatedMixin, ComponentProvenanceMixin,
                    TimestampUpdatedMixin, Base):
    """Table ``production_run``, operational group: one batch of one product on one
    line, and the commitment every downstream operational row is attributed to."""

    __tablename__ = "production_run"
    __table_args__ = (
        UniqueConstraint("production_run_code", name="uq_production_run_code"),
        CheckConstraint(
            "production_run_code GLOB "
            "'RUN-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'",
            name=conv("ck_pr_code_format")),
        CheckConstraint("planned_quantity_units > 0",
                        name=conv("ck_pr_planned_quantity_positive")),
        CheckConstraint("planned_end_at > planned_start_at",
                        name=conv("ck_pr_planned_window_ordered")),
        CheckConstraint(
            "actual_end_at IS NULL OR actual_start_at IS NULL "
            "OR actual_end_at > actual_start_at",
            name=conv("ck_pr_actual_window_ordered")),
        CheckConstraint(
            "(pause_reason IS NOT NULL) = (run_status = 'paused')",
            name=conv("ck_pr_pause_reason_consistency")),
        CheckConstraint(
            "run_status <> 'cancelled' OR cancellation_reason IS NOT NULL",
            name=conv("ck_pr_cancellation_reason_required")),
        CheckConstraint(
            "run_status IN ('planned', 'setup', 'cancelled') "
            "OR actual_start_at IS NOT NULL",
            name=conv("ck_pr_started_when_beyond_setup")),
        CheckConstraint(
            "run_status <> 'completed' OR actual_end_at IS NOT NULL",
            name=conv("ck_pr_ended_when_terminal")),
        CheckConstraint("priority IN ('normal', 'high', 'urgent')",
                        name=conv("ck_pr_priority_allowed")),
        CheckConstraint(
            "run_status IN ('planned', 'setup', 'running', 'paused', "
            "'completed', 'cancelled')",
            name=conv("ck_pr_run_status_allowed")),
        CheckConstraint(
            "pause_reason IS NULL OR pause_reason IN ('machine_down', "
            "'material_shortage', 'quality_hold', 'operator_unavailable', "
            "'shift_end', 'higher_priority_run')",
            name=conv("ck_pr_pause_reason_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_pr_created_by_component_allowed")),
        CheckConstraint("length(production_run_code) <= 16",
                        name=conv("ck_pr_production_run_code_length")),
        # At most one active run per line -- the primary concurrency guard for
        # the Simulator transaction, and the database-level guarantee that two
        # concurrent writes cannot both schedule a line (§37.9).
        Index(
            "uq_production_run_active_per_line",
            "production_line_id",
            unique=True,
            sqlite_where=text("run_status IN ('setup', 'running', 'paused')"),
        ),
        {"sqlite_autoincrement": True},
    )

    production_run_id: Mapped[OperationalPk]
    production_run_code: Mapped[str] = mapped_column(String(16))
    product_id: Mapped[int] = mapped_column(
        ForeignKey("product.product_id", name="fk_pr_product",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[int] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_pr_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Pins the cycle-time standard this run is measured against, which is why a
    # retired capability row must still resolve (§M7).
    product_line_capability_id: Mapped[int] = mapped_column(
        ForeignKey("product_line_capability.product_line_capability_id",
                   name="fk_pr_capability",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.customer_id", name="fk_pr_customer",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    planned_quantity_units: Mapped[Quantity]
    planned_start_at: Mapped[TimestampTz]
    planned_end_at: Mapped[TimestampTz]
    actual_start_at: Mapped[Optional[TimestampTz]]
    actual_end_at: Mapped[Optional[TimestampTz]]
    due_date: Mapped[date] = mapped_column(Date)
    priority: Mapped[RunPriority] = mapped_column(
        Enum(RunPriority, name="run_priority", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        server_default=text("'normal'"),
    )
    run_status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus, name="run_status", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        server_default=text("'planned'"),
    )
    pause_reason: Mapped[Optional[RunPauseReason]] = mapped_column(
        Enum(RunPauseReason, name="run_pause_reason", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    cancellation_reason: Mapped[Optional[str]] = mapped_column(Text)

    # The four many-to-one relationships are unidirectional: no master model maps
    # a production_runs collection (§19.1).
    product: Mapped["Product"] = relationship(lazy="select")
    production_line: Mapped["ProductionLine"] = relationship(lazy="select")
    product_line_capability: Mapped["ProductLineCapability"] = relationship(
        lazy="select"
    )
    customer: Mapped["Customer"] = relationship(lazy="select")
    progress_snapshots: Mapped[list["ProductionProgress"]] = relationship(
        back_populates="production_run", lazy="select"
    )
    quality_inspections: Mapped[list["QualityInspectionResult"]] = relationship(
        back_populates="production_run", lazy="select"
    )
    scrap_records: Mapped[list["ScrapRecord"]] = relationship(
        back_populates="production_run", lazy="select"
    )

    # cycle_history (~650 per run) and production_count (~290 per run) both
    # exceed the L2 bound of roughly 100 per parent, so both are excluded even
    # though they are conceptually children (§19.2).

    @validates("production_run_code", "product_id", "production_line_id",
               "product_line_capability_id", "customer_id", "product",
               "production_line", "product_line_capability", "customer",
               "planned_start_at", "planned_end_at", "actual_start_at",
               "actual_end_at")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Partial immutability plus timezone-awareness (§41.3, §41.4).

        Changing a run's product, line, capability or customer mid-flight would
        invalidate every child record already attributed to it. Status, priority,
        actual timestamps and reasons remain mutable, and the legal status
        transitions are the Simulator's state machine rather than a constraint.
        """
        if key in ("production_run_code", "product_id", "production_line_id",
                   "product_line_capability_id", "customer_id", "product",
                   "production_line", "product_line_capability", "customer"):
            if inspect(self).persistent:
                raise ValueError(
                    "production_run.%s is immutable after insert; every child "
                    "record is already attributed to it" % key
                )
            if key == "production_run_code":
                return value.strip().upper()
            return value
        return require_timezone_aware(key, value)


class ProductionProgress(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``production_progress``, operational group: a periodic roll-up of a run's
    cumulative output, rate and schedule variance. Append-only."""

    __tablename__ = "production_progress"
    __table_args__ = (
        UniqueConstraint("production_run_id", "snapshot_at",
                         name="uq_pp_run_snapshot"),
        CheckConstraint(
            "quantity_good_cumulative >= 0 "
            "AND quantity_scrapped_cumulative >= 0 "
            "AND quantity_rework_cumulative >= 0",
            name=conv("ck_pp_quantities_non_negative")),
        CheckConstraint("percent_complete >= 0",
                        name=conv("ck_pp_percent_complete_non_negative")),
        CheckConstraint("current_rate_units_per_hour >= 0",
                        name=conv("ck_pp_rate_non_negative")),
        CheckConstraint("elapsed_production_seconds >= 0",
                        name=conv("ck_pp_elapsed_non_negative")),
        CheckConstraint("downtime_seconds_cumulative >= 0",
                        name=conv("ck_pp_downtime_non_negative")),
        CheckConstraint("scrap_rate_pct BETWEEN 0 AND 100",
                        name=conv("ck_pp_scrap_rate_range")),
        CheckConstraint(
            "projected_completion_at IS NULL "
            "OR projected_completion_at > snapshot_at",
            name=conv("ck_pp_projection_after_snapshot")),
        CheckConstraint("is_behind_schedule IN (0, 1)",
                        name=conv("ck_pp_is_behind_schedule_bool")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_pp_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    production_progress_id: Mapped[OperationalPk]
    production_run_id: Mapped[int] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_pp_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    snapshot_at: Mapped[TimestampTz]
    quantity_good_cumulative: Mapped[Quantity]
    quantity_scrapped_cumulative: Mapped[Quantity]
    quantity_rework_cumulative: Mapped[Quantity]
    percent_complete: Mapped[Percent]
    current_rate_units_per_hour: Mapped[Rate]
    elapsed_production_seconds: Mapped[int] = mapped_column(Integer)
    downtime_seconds_cumulative: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    # NULL when the current rate is zero: there is nothing to project from.
    projected_completion_at: Mapped[Optional[TimestampTz]]
    # Signed: negative is ahead of schedule.
    schedule_variance_minutes: Mapped[int] = mapped_column(Integer)
    # No default deliberately: it is the writer's judgement, and re-deriving it
    # in the ORM would make the stored value redundant (§29, §O5).
    is_behind_schedule: Mapped[bool]
    scrap_rate_pct: Mapped[Percent]
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_pp_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    production_run: Mapped["ProductionRun"] = relationship(
        back_populates="progress_snapshots", lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")

    @validates("production_run_id", "snapshot_at", "quantity_good_cumulative",
               "quantity_scrapped_cumulative", "quantity_rework_cumulative",
               "percent_complete", "current_rate_units_per_hour",
               "elapsed_production_seconds", "downtime_seconds_cumulative",
               "projected_completion_at", "schedule_variance_minutes",
               "is_behind_schedule", "scrap_rate_pct", "shift_id",
               "production_run", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and both timestamps must be timezone-aware (§41.3, §41.4)."""
        if inspect(self).persistent:
            raise ValueError(
                "production_progress is append-only; %s cannot be reassigned "
                "once the row is persistent" % key
            )
        if key in ("snapshot_at", "projected_completion_at"):
            return require_timezone_aware(key, value)
        return value


class ProductionCount(TimestampCreatedMixin, ComponentProvenanceMixin,
                      TimestampUpdatedMixin, Base):
    """Table ``production_count``, operational group: per-machine output tallied over
    a fixed interval. Declared derived, and rebuildable idempotently."""

    __tablename__ = "production_count"
    __table_args__ = (
        UniqueConstraint("machine_id", "interval_from",
                         name="uq_pc_machine_interval"),
        CheckConstraint("interval_to > interval_from",
                        name=conv("ck_pc_interval_ordered")),
        CheckConstraint(
            "good_count >= 0 AND scrap_count >= 0 AND rework_count >= 0",
            name=conv("ck_pc_counts_non_negative")),
        CheckConstraint(
            "cycles_completed = good_count + scrap_count + rework_count",
            name=conv("ck_pc_cycles_equal_outcomes")),
        CheckConstraint("total_cycle_time_seconds >= 0",
                        name=conv("ck_pc_total_cycle_time_non_negative")),
        CheckConstraint("running_seconds >= 0",
                        name=conv("ck_pc_running_seconds_non_negative")),
        # strftime rather than a date function: deterministic, and valid inside a
        # CHECK expression on SQLite.
        CheckConstraint(
            "running_seconds <= (strftime('%s', interval_to) "
            "- strftime('%s', interval_from))",
            name=conv("ck_pc_running_within_interval")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_pc_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    production_count_id: Mapped[OperationalPk]
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_pc_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_pc_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    interval_from: Mapped[TimestampTz]
    interval_to: Mapped[TimestampTz]
    good_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    scrap_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    rework_count: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    cycles_completed: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    total_cycle_time_seconds: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    running_seconds: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_pc_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # All three unidirectional, all three raise rather than emit SQL under rule
    # L4: ProductionRun declines the reverse because ~290 counts per run exceeds
    # the L2 bound (§19.2, §19.4).
    machine: Mapped["Machine"] = relationship(lazy="raise_on_sql")
    production_run: Mapped[Optional["ProductionRun"]] = relationship(
        lazy="raise_on_sql"
    )
    shift: Mapped["Shift"] = relationship(lazy="raise_on_sql")

    @validates("machine_id", "machine", "interval_from", "interval_to")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Identity columns immutable; interval bounds timezone-aware (§41.3, §41.4).

        Mutable only by idempotent rebuild: the count columns may be rewritten,
        but ``machine_id``, ``interval_from`` and ``interval_to`` are the row's
        natural identity and the unique constraint's columns.
        """
        if inspect(self).persistent:
            raise ValueError(
                "production_count.%s is the row's identity and cannot be "
                "reassigned; rebuild rewrites the count columns only" % key
            )
        if key in ("interval_from", "interval_to"):
            return require_timezone_aware(key, value)
        return value


class CycleHistory(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``cycle_history``, operational group: one machine cycle, where
    sub-second variance against the standard is the degradation signal.
    Append-only."""

    __tablename__ = "cycle_history"
    __table_args__ = (
        UniqueConstraint("machine_id", "production_run_id",
                         "cycle_number_in_run",
                         name="uq_ch_machine_run_cycle"),
        UniqueConstraint("machine_id", "sequence_number",
                         name="uq_ch_machine_sequence"),
        CheckConstraint("cycle_ended_at > cycle_started_at",
                        name=conv("ck_ch_cycle_window_ordered")),
        CheckConstraint("cycle_time_seconds > 0",
                        name=conv("ck_ch_cycle_time_positive")),
        CheckConstraint("cycle_number_in_run > 0",
                        name=conv("ck_ch_cycle_number_positive")),
        CheckConstraint("sequence_number > 0",
                        name=conv("ck_ch_sequence_number_positive")),
        CheckConstraint(
            "interrupted = 0 OR deviation_from_standard_pct IS NULL",
            name=conv("ck_ch_interrupted_has_no_deviation")),
        CheckConstraint("outcome IN ('good', 'scrap', 'rework')",
                        name=conv("ck_ch_outcome_allowed")),
        CheckConstraint("interrupted IN (0, 1)",
                        name=conv("ck_ch_interrupted_bool")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_ch_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    cycle_history_id: Mapped[OperationalPk]
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_ch_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_run_id: Mapped[int] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_ch_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    cycle_number_in_run: Mapped[int] = mapped_column(Integer)
    cycle_started_at: Mapped[TimestampTz]
    cycle_ended_at: Mapped[TimestampTz]
    # Two decimals: sub-second variance is the signal, not noise.
    cycle_time_seconds: Mapped[Seconds2]
    # Signed and wider than Percent because it can exceed 100 in both
    # directions. Stored rather than derived, because the capability row it was
    # computed against may later be retired (§O7).
    deviation_from_standard_pct: Mapped[Optional[SignedPercent]]
    outcome: Mapped[CycleOutcome] = mapped_column(
        Enum(CycleOutcome, name="cycle_outcome", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    interrupted: Mapped[bool] = mapped_column(server_default=text("0"))
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_ch_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    sequence_number: Mapped[int] = mapped_column(Integer)

    # All three unidirectional and all three raise under rule L4: ~650 cycles
    # per run puts the reverse collection well past the L2 bound (§19.2, §19.4).
    machine: Mapped["Machine"] = relationship(lazy="raise_on_sql")
    production_run: Mapped["ProductionRun"] = relationship(lazy="raise_on_sql")
    shift: Mapped["Shift"] = relationship(lazy="raise_on_sql")

    @validates("machine_id", "production_run_id", "cycle_number_in_run",
               "cycle_started_at", "cycle_ended_at", "cycle_time_seconds",
               "deviation_from_standard_pct", "outcome", "interrupted",
               "shift_id", "sequence_number", "machine", "production_run",
               "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and both cycle timestamps timezone-aware (§41.3, §41.4)."""
        if inspect(self).persistent:
            raise ValueError(
                "cycle_history is append-only; %s cannot be reassigned once "
                "the row is persistent" % key
            )
        if key in ("cycle_started_at", "cycle_ended_at"):
            return require_timezone_aware(key, value)
        return value
