"""Operational models O1-O3: raw telemetry, current machine state, and state history.

``MachineSensorReading`` is the highest-volume table in the database at roughly 32
million rows a year, and its four relationships are the reason rule L4 exists: all
of them use ``raise_on_sql``, so an accidental lazy load during a bulk read fails
immediately instead of emitting one query per row (§19.4).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import (
    MachineOperationalState,
    ReadingQualityFlag,
    StateTransitionReason,
)
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    TimestampUpdatedMixin,
    require_timezone_aware,
)
from models.types import Hours, Measurement, OperationalPk, TimestampTz

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.parameters import MachineParameter
    from models.master.plant import Shift
    from models.operational.events import OperationalEvent
    from models.operational.maintenance import MaintenanceWorkRecord
    from models.operational.production import ProductionRun


class MachineSensorReading(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``machine_sensor_reading``, operational group: one parameter value from
    one machine at one instant. Append-only, and the evidentiary floor of the
    platform."""

    __tablename__ = "machine_sensor_reading"
    __table_args__ = (
        UniqueConstraint("machine_id", "sequence_number",
                         name="uq_msr_machine_sequence"),
        CheckConstraint("sequence_number > 0",
                        name=conv("ck_msr_sequence_number_positive")),
        CheckConstraint(
            "quality_flag IN ('valid', 'out_of_physical_range', "
            "'sensor_offline', 'interpolated', 'stale')",
            name=conv("ck_msr_quality_flag_allowed")),
        CheckConstraint(
            "machine_state_at_reading IN ('running', 'idle', 'setup', "
            "'starved', 'blocked', 'down_unplanned', 'down_planned', "
            "'offline')",
            name=conv("ck_msr_machine_state_at_reading_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_msr_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    machine_sensor_reading_id: Mapped[OperationalPk]
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_msr_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_parameter_id: Mapped[int] = mapped_column(
        ForeignKey("machine_parameter.machine_parameter_id",
                   name="fk_msr_parameter",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Event time: when the reading was taken, never when the row was written.
    recorded_at: Mapped[TimestampTz]
    # Decimal, never float. A float threshold comparison across 32 million rows
    # produces a stream of alerts nobody can reproduce (§38.2).
    reading_value: Mapped[Measurement]
    # Sensor trust, not process judgement. The ORM makes no inference between
    # this and reading_value.
    quality_flag: Mapped[ReadingQualityFlag] = mapped_column(
        Enum(ReadingQualityFlag, name="reading_quality_flag",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls]),
        server_default=text("'valid'"),
    )
    # Denormalised deliberately: a reading has to be interpretable without
    # joining state history, because "running at 95 C" and "idle at 95 C" mean
    # different things, and reconstructing it would be a range scan per row.
    machine_state_at_reading: Mapped[MachineOperationalState] = mapped_column(
        Enum(MachineOperationalState, name="machine_operational_state",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_msr_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_msr_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Monotonic per machine: deterministic ordering when timestamps tie.
    sequence_number: Mapped[int] = mapped_column(Integer)

    # All four are unidirectional -- Machine, MachineParameter, Shift and
    # ProductionRun each decline a reverse collection under rule L1 -- and all
    # four raise rather than emit SQL, under rule L4 (§19.1, §19.4).
    machine: Mapped["Machine"] = relationship(lazy="raise_on_sql")
    machine_parameter: Mapped["MachineParameter"] = relationship(
        lazy="raise_on_sql"
    )
    shift: Mapped["Shift"] = relationship(lazy="raise_on_sql")
    production_run: Mapped[Optional["ProductionRun"]] = relationship(
        lazy="raise_on_sql"
    )

    @validates("machine_id", "machine_parameter_id", "recorded_at",
               "reading_value", "quality_flag", "machine_state_at_reading",
               "shift_id", "production_run_id", "sequence_number",
               "machine", "machine_parameter", "shift", "production_run")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``recorded_at`` must be timezone-aware.

        Both rules live in one hook because SQLAlchemy permits a single validator
        per attribute (§41.3, §41.4). ``recorded_at`` is the highest-volume
        timestamp in the database, and a naive value here would corrupt every
        window computation downstream.
        """
        if inspect(self).persistent:
            raise ValueError(
                "machine_sensor_reading is append-only; %s cannot be "
                "reassigned once the row is persistent" % key
            )
        if key == "recorded_at":
            return require_timezone_aware(key, value)
        return value


class MachineStateTransition(TimestampCreatedMixin, ComponentProvenanceMixin,
                             Base):
    """Table ``machine_state_transition``, operational group: every change of machine
    state as an immutable fact, with the duration of the state being left.

    Storing the duration of the state being *left* at the moment of leaving it is
    what turns availability analysis into a sum rather than a window function over
    the whole history (§O3).
    """

    __tablename__ = "machine_state_transition"
    __table_args__ = (
        CheckConstraint("from_state IS NULL OR from_state <> to_state",
                        name=conv("ck_mst_states_differ")),
        CheckConstraint(
            "(from_state IS NULL "
            "AND duration_in_previous_state_seconds IS NULL) "
            "OR (from_state IS NOT NULL "
            "AND duration_in_previous_state_seconds IS NOT NULL)",
            name=conv("ck_mst_duration_consistency")),
        CheckConstraint(
            "duration_in_previous_state_seconds IS NULL "
            "OR duration_in_previous_state_seconds >= 0",
            name=conv("ck_mst_duration_non_negative")),
        CheckConstraint(
            "from_state IS NULL OR from_state IN ('running', 'idle', 'setup', "
            "'starved', 'blocked', 'down_unplanned', 'down_planned', "
            "'offline')",
            name=conv("ck_mst_from_state_allowed")),
        CheckConstraint(
            "to_state IN ('running', 'idle', 'setup', 'starved', 'blocked', "
            "'down_unplanned', 'down_planned', 'offline')",
            name=conv("ck_mst_to_state_allowed")),
        CheckConstraint(
            "reason_code IN ('run_start', 'run_complete', 'changeover', "
            "'tool_change', 'upstream_starvation', 'downstream_blockage', "
            "'breakdown', 'planned_maintenance', 'quality_hold', "
            "'operator_unavailable', 'shift_end', 'restored', "
            "'asset_status_change')",
            name=conv("ck_mst_reason_code_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_mst_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    machine_state_transition_id: Mapped[OperationalPk]
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_mst_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # NULL only on the first transition of a machine's life. Two columns bind the
    # same enum class, one nullable and one not, which in SQLite means two
    # independent check constraints carrying the same value list (§40.2).
    from_state: Mapped[Optional[MachineOperationalState]] = mapped_column(
        Enum(MachineOperationalState, name="machine_operational_state",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    to_state: Mapped[MachineOperationalState] = mapped_column(
        Enum(MachineOperationalState, name="machine_operational_state",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    transition_at: Mapped[TimestampTz]
    # NULL only when from_state is NULL: the first transition has no predecessor
    # and therefore no duration.
    duration_in_previous_state_seconds: Mapped[Optional[int]] = mapped_column(
        Integer
    )
    # What turns a state log into a diagnostic record.
    reason_code: Mapped[StateTransitionReason] = mapped_column(
        Enum(StateTransitionReason, name="state_transition_reason",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_mst_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_mst_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # The triggering references close the causal loop from a detected condition,
    # through the maintenance job, to the downtime it produced.
    triggering_event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("operational_event.operational_event_id",
                   name="fk_mst_triggering_event",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    triggering_work_record_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("maintenance_work_record.maintenance_work_record_id",
                   name="fk_mst_triggering_work_record",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # All five are unidirectional. machine_operational_status's reverse would be
    # a one-to-one for the latest transition only, which is a filtered
    # relationship rather than a structural one (§O3).
    machine: Mapped["Machine"] = relationship(lazy="select")
    shift: Mapped["Shift"] = relationship(lazy="select")
    production_run: Mapped[Optional["ProductionRun"]] = relationship(
        lazy="select"
    )
    triggering_event: Mapped[Optional["OperationalEvent"]] = relationship(
        lazy="select"
    )
    triggering_work_record: Mapped[Optional["MaintenanceWorkRecord"]] = (
        relationship(lazy="select")
    )

    @validates("machine_id", "from_state", "to_state", "transition_at",
               "duration_in_previous_state_seconds", "reason_code", "shift_id",
               "production_run_id", "triggering_event_id",
               "triggering_work_record_id", "notes", "machine", "shift",
               "production_run", "triggering_event", "triggering_work_record")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``transition_at`` must be timezone-aware (§41.3, §41.4)."""
        if inspect(self).persistent:
            raise ValueError(
                "machine_state_transition is append-only; %s cannot be "
                "reassigned once the row is persistent" % key
            )
        if key == "transition_at":
            return require_timezone_aware(key, value)
        return value


class MachineOperationalStatus(TimestampCreatedMixin, ComponentProvenanceMixin,
                               TimestampUpdatedMixin, Base):
    """Table ``machine_operational_status``, operational group: the current state of
    each machine in exactly one row, with the counters maintenance scheduling reads.

    The only table in the database whose row count never grows, and the most
    derived: it is fully regenerable by replaying state transitions and cycle
    history, which is what makes its mutability safe (§O2).
    """

    __tablename__ = "machine_operational_status"
    __table_args__ = (
        UniqueConstraint("machine_id", name="uq_mos_machine"),
        CheckConstraint("accumulated_operating_hours >= 0",
                        name=conv("ck_mos_operating_hours_non_negative")),
        CheckConstraint("accumulated_cycle_count >= 0",
                        name=conv("ck_mos_cycle_count_non_negative")),
        CheckConstraint("open_alert_count >= 0",
                        name=conv("ck_mos_open_alert_count_non_negative")),
        CheckConstraint(
            "operating_hours_at_last_maintenance IS NULL "
            "OR operating_hours_at_last_maintenance "
            "<= accumulated_operating_hours",
            name=conv("ck_mos_maint_hours_not_ahead")),
        CheckConstraint(
            "cycle_count_at_last_maintenance IS NULL "
            "OR cycle_count_at_last_maintenance <= accumulated_cycle_count",
            name=conv("ck_mos_maint_cycles_not_ahead")),
        # These two work as a pair: together they make the state-to-run
        # relationship a closed set of legal combinations (§O2).
        CheckConstraint(
            "current_state <> 'running' "
            "OR current_production_run_id IS NOT NULL",
            name=conv("ck_mos_running_requires_run")),
        CheckConstraint(
            "current_production_run_id IS NULL "
            "OR current_state IN ('running', 'setup', 'starved', 'blocked')",
            name=conv("ck_mos_run_only_when_engaged")),
        CheckConstraint(
            "current_state IN ('running', 'idle', 'setup', 'starved', "
            "'blocked', 'down_unplanned', 'down_planned', 'offline')",
            name=conv("ck_mos_current_state_allowed")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_mos_created_by_component_allowed")),
        {"sqlite_autoincrement": True},
    )

    machine_operational_status_id: Mapped[OperationalPk]
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_mos_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # 'starved' and 'blocked' are distinguished because they mean opposite things
    # about where the constraint is.
    current_state: Mapped[MachineOperationalState] = mapped_column(
        Enum(MachineOperationalState, name="machine_operational_state",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    state_since: Mapped[TimestampTz]
    current_production_run_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_run.production_run_id",
                   name="fk_mos_production_run",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    current_shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_mos_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Machine hour meter. Storing the absolute value plus the value at last
    # maintenance -- rather than a resetting "since" counter -- means two
    # readings and a subtraction, which cannot drift (§O2).
    accumulated_operating_hours: Mapped[Hours] = mapped_column(
        server_default=text("0")
    )
    accumulated_cycle_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    operating_hours_at_last_maintenance: Mapped[Optional[Hours]]
    cycle_count_at_last_maintenance: Mapped[Optional[int]] = mapped_column(
        Integer
    )
    # Staleness detection. NULL for unmonitored machines.
    last_reading_at: Mapped[Optional[TimestampTz]]
    last_state_transition_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine_state_transition.machine_state_transition_id",
                   name="fk_mos_last_transition",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Maintained for dashboard performance and reconcilable against
    # operational_alert. The ORM does not derive it (§41.3).
    open_alert_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )

    machine: Mapped["Machine"] = relationship(
        back_populates="operational_status", lazy="select"
    )
    # The other three are unidirectional.
    current_shift: Mapped["Shift"] = relationship(lazy="select")
    current_production_run: Mapped[Optional["ProductionRun"]] = relationship(
        lazy="select"
    )
    last_state_transition: Mapped[Optional["MachineStateTransition"]] = (
        relationship(lazy="select")
    )

    @validates("machine_id", "machine", "state_since", "last_reading_at")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Partial immutability plus timezone-awareness (§41.3, §41.4).

        A mutable model: state, counters and timestamps are updated continuously.
        ``machine_id`` is the exception -- it is set once at insert, because the
        row *is* that machine's status. Counter monotonicity across updates is a
        temporal rule the writing component owns.
        """
        if key in ("machine_id", "machine") and inspect(self).persistent:
            raise ValueError(
                "machine_operational_status.%s is set once at insert and "
                "cannot be reassigned; the row is that machine's status" % key
            )
        if key in ("state_since", "last_reading_at"):
            return require_timezone_aware(key, value)
        return value
