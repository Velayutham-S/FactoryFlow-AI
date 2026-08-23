"""Shared monitoring state: the master snapshot, the code sequences, the detection
cursors, and the suppression gate.

The four detector modules and the alert layer all need the same things — the thresholds
and floors master data declares, where the last cycle stopped reading, and whether a
machine's events should be suppressed at all. Collecting them here keeps each detector
to the one question it answers.

**Every threshold the agent applies comes from master data.** That is not a style
choice: FACTORY_OPERATIONAL_DATA_DESIGN.md §E14 rule 7 says the acknowledgement window
"comes from master data, never from a constant", and the same principle governs every
limit the agent compares against. The snapshot below is the complete set:

===============================  ====================================================
``alert_threshold_rule``         warning and critical limits, rate limits, sustained
                                 duration, and the severity each maps to
``machine_parameter``            physical range and unit of measure
``machine_type_parameter``       sampling interval, healthy envelope
``product_line_capability``      cycle time and maximum hourly output
``product``                      target scrap rate
``inventory_item``               reorder point, safety stock, critical-spare flag
``failure_severity_level``       severity ranks and acknowledgement windows
===============================  ====================================================

**The snapshot holds detached ORM instances.** ``models.session`` builds its session
factory with ``expire_on_commit=False``, so a loaded instance keeps its column values
after its session closes. Only mapped **column** attributes are read from these objects,
never a relationship. Same convention the simulator's snapshot uses.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.master import (
    AlertThresholdRule,
    FailureCategory,
    FailureSeverityLevel,
    InventoryItem,
    Machine,
    MachineParameter,
    MachineType,
    MachineTypeParameter,
    Plant,
    Product,
    ProductionLine,
    ProductLineCapability,
    Shift,
)
from models.operational import (
    MachineOperationalStatus,
    MaintenanceWorkRecord,
    OperationalAlert,
    OperationalEvent,
)

from monitoring.errors import MasterDataUnavailableError

# The component identity written to created_by_component on every row this agent
# creates. NOT NULL with no default, so it must be stated explicitly (§27).
MONITORING_COMPONENT = "monitoring_agent"

# Machine states in which events are not raised. E13 rule 7: abnormal behaviour during
# a repair or a changeover is expected, not newsworthy, and alerting on it is the
# largest single source of false positives in condition monitoring.
SUPPRESSED_STATES = {
    "setup": "planned_downtime",
    "down_planned": "planned_downtime",
    "offline": "machine_offline",
}

# Work statuses that mean a machine is actively being worked on. E11's consumer note:
# the Monitoring Agent suppresses events for machines with an in-progress job.
ACTIVE_WORK_STATUSES = ("assigned", "in_progress", "awaiting_parts")

# E2 rule 9: telemetry is stale once it is older than three sampling intervals for a
# monitored, non-offline machine. The multiplier is documented; the interval itself
# comes from machine_type_parameter.
STALE_SAMPLING_INTERVALS = 3


@dataclass(frozen=True)
class Detection:
    """One condition a detector found, before it is correlated and written.

    Detectors produce these; :mod:`monitoring.alerts` turns them into an
    ``operational_event`` attached to an ``operational_alert``. Keeping the two apart
    means a detector never has to know how correlation works, and correlation never has
    to know what a vibration limit is.
    """

    category: str
    event_type: str
    detected_at: datetime
    severity_level_id: int
    correlation_subject: str
    """The subject half of ``correlation_key`` -- a machine code or an item code."""

    machine_id: int | None = None
    production_line_id: int | None = None
    production_run_id: int | None = None
    inventory_item_id: int | None = None
    machine_parameter_id: int | None = None
    alert_threshold_rule_id: int | None = None
    observed_value: Decimal | None = None
    threshold_value_breached: Decimal | None = None
    threshold_direction: str | None = None
    sustained_duration_seconds: int | None = None
    triggering_reading_id: int | None = None
    detection_note: str | None = None

    @property
    def correlation_key(self) -> str:
        """``MC-0101|machine_condition``.

        E14 rule 2: composed deterministically from subject and category, never
        heuristically, so the deduplication behaviour is reproducible and explainable.
        """
        return "%s|%s" % (self.correlation_subject, self.category)


@dataclass
class BreachEpisode:
    """An in-flight threshold breach for one machine, parameter and direction.

    Detection is per **episode**, not per reading. §E14 records that one alert absorbed
    34 events over eleven hours as vibration flapped above and below its limit -- which
    is one event per excursion, not one per sample. An episode opens on the first
    breaching reading, is confirmed once it has persisted for the rule's
    ``sustained_duration_seconds``, and closes when the value returns inside the limit.
    """

    started_at: datetime
    reported: bool = False
    last_value: Decimal | None = None
    last_reading_id: int | None = None


@dataclass
class Cursors:
    """Where the previous cycle stopped reading.

    **No table is introduced for agent state.** The cursors are recovered from the
    agent's own output: the newest event of each category tells the agent what it has
    already accounted for. Telemetry uses a row identifier because many readings share a
    timestamp; the aggregate paths use timestamps because their rows are naturally
    ordered by event time.

    The recovery is deliberately conservative -- it may re-examine rows already seen
    rather than risk skipping unseen ones. A re-examined breach lands on the same
    correlation key and is absorbed by the alert that already exists.
    """

    reading_id: int = 0
    progress_at: datetime | None = None
    inspection_at: datetime | None = None
    scrap_at: datetime | None = None
    movement_at: datetime | None = None
    cycle_at: datetime | None = None


class MasterSnapshot:
    """Every master row the agent compares against, loaded once and indexed."""

    def __init__(self, session: Session) -> None:
        self.plant: Plant | None = session.scalars(select(Plant)).first()
        if self.plant is None:
            raise MasterDataUnavailableError(
                "no plant row; shift windows are local wall-clock and cannot be "
                "resolved without plant.timezone"
            )
        self.timezone = ZoneInfo(self.plant.timezone)

        self.severities: dict[int, FailureSeverityLevel] = {
            s.failure_severity_level_id: s
            for s in session.scalars(select(FailureSeverityLevel))
        }
        if not self.severities:
            raise MasterDataUnavailableError(
                "no failure_severity_level rows; every event and alert must reference "
                "a severity and none exists"
            )

        self.machines: dict[int, Machine] = {
            m.machine_id: m for m in session.scalars(select(Machine))
        }
        if not self.machines:
            raise MasterDataUnavailableError(
                "no machine rows; there is nothing to monitor")

        self.machine_types: dict[int, MachineType] = {
            t.machine_type_id: t for t in session.scalars(select(MachineType))
        }
        self.lines: dict[int, ProductionLine] = {
            line.production_line_id: line
            for line in session.scalars(select(ProductionLine))
        }
        self.parameters: dict[int, MachineParameter] = {
            p.machine_parameter_id: p
            for p in session.scalars(select(MachineParameter))
        }
        self.products: dict[int, Product] = {
            p.product_id: p for p in session.scalars(select(Product))
        }
        self.capabilities: dict[int, ProductLineCapability] = {
            c.product_line_capability_id: c
            for c in session.scalars(select(ProductLineCapability))
        }
        self.items: dict[int, InventoryItem] = {
            i.inventory_item_id: i for i in session.scalars(select(InventoryItem))
        }
        self.failure_categories: dict[int, FailureCategory] = {
            c.failure_category_id: c for c in session.scalars(select(FailureCategory))
        }
        self.shifts: list[Shift] = list(session.scalars(select(Shift)))
        self.production_shifts = [
            s for s in self.shifts
            if s.shift_type.value == "production" and s.is_active
        ]
        if not self.production_shifts:
            raise MasterDataUnavailableError(
                "no active production shift; every event records the shift it "
                "occurred in"
            )

        # Threshold rules indexed by the profile a machine points at, then by
        # parameter. A machine with no profile is not threshold-monitored, which is a
        # master data statement rather than a gap.
        self.rules_by_profile: dict[int, dict[int, AlertThresholdRule]] = {}
        for rule in session.scalars(select(AlertThresholdRule)):
            if not (rule.is_active and rule.is_enabled):
                continue
            self.rules_by_profile.setdefault(
                rule.alert_threshold_profile_id, {}
            )[rule.machine_parameter_id] = rule

        # Declared parameters per machine type, for sampling intervals and envelopes.
        self.declarations: dict[tuple[int, int], MachineTypeParameter] = {}
        self.declarations_by_type: dict[int, list[MachineTypeParameter]] = {}
        for decl in session.scalars(select(MachineTypeParameter)):
            if not decl.is_active:
                continue
            self.declarations[(decl.machine_type_id, decl.machine_parameter_id)] = decl
            self.declarations_by_type.setdefault(
                decl.machine_type_id, []).append(decl)

    def rule_for(self, machine: Machine, parameter_id: int) -> AlertThresholdRule | None:
        """The threshold rule governing one parameter on one machine.

        Reached through the machine's own ``alert_threshold_profile_id``. A machine with
        no profile, or a profile with no rule for the parameter, yields ``None`` and the
        parameter is simply not threshold-monitored.
        """
        if machine.alert_threshold_profile_id is None:
            return None
        return self.rules_by_profile.get(
            machine.alert_threshold_profile_id, {}).get(parameter_id)

    def unit_of(self, parameter_id: int) -> str:
        parameter = self.parameters.get(parameter_id)
        return parameter.unit_of_measure if parameter is not None else ""

    def failure_category_name(self, category_id: int) -> str:
        """The mechanism a quality defect was attributed to, for the detection note.

        §E13 rule 8 requires a detection note a human can read, and naming the mechanism
        is what turns "a part failed" into evidence about a machine.
        """
        category = self.failure_categories.get(category_id)
        return category.category_name if category is not None else "unknown"

    def rank_of(self, severity_level_id: int) -> int:
        """Severity rank. Lower is more severe -- ``SEV-1`` is rank 1."""
        severity = self.severities.get(severity_level_id)
        return severity.severity_rank if severity is not None else 99

    def least_severe_id(self) -> int:
        return max(
            self.severities.values(), key=lambda s: s.severity_rank
        ).failure_severity_level_id

    def most_severe_id(self) -> int:
        return min(
            self.severities.values(), key=lambda s: s.severity_rank
        ).failure_severity_level_id


class MonitoringContext:
    """Master data, cursors, code sequences and the suppression gate."""

    def __init__(self, session: Session) -> None:
        self.master = MasterSnapshot(session)
        self.cursors = Cursors()
        self.episodes: dict[tuple[int, int, str], BreachEpisode] = {}
        self.samples: dict[tuple[int, int], list[tuple[datetime, Decimal]]] = {}
        """Recent samples per machine and parameter, for windowed gradients.

        A rate of change is a gradient over a window, not the difference between two
        adjacent samples: adjacent samples differ mostly by sensor noise, and dividing
        that by a short interval produces a large meaningless rate. Trimmed to the
        window each rule asks for, so the memory held is bounded by the rule.
        """
        self._code_counters: dict[str, int] = {}
        self._load_code_counters(session)
        self._recover_cursors(session)
        self._suppressed: dict[int, str] = {}

    # ------------------------------------------------------------------- codes

    def _load_code_counters(self, session: Session) -> None:
        """Continue the EVT and ALR series from the highest value already stored.

        Both codes are unique-constrained. Without this a second run would reissue a
        code and the first insert would fail.
        """
        for column in (
            OperationalEvent.operational_event_code,
            OperationalAlert.operational_alert_code,
        ):
            for existing in session.scalars(select(column)):
                scope, _, suffix = str(existing).rpartition("-")
                try:
                    value = int(suffix)
                except ValueError:
                    continue
                self._code_counters[scope] = max(
                    self._code_counters.get(scope, 0), value)

    def _next_code(self, prefix: str, moment: datetime) -> str:
        key = "%s-%s" % (prefix, moment.strftime("%Y%m%d"))
        nxt = self._code_counters.get(key, 0) + 1
        self._code_counters[key] = nxt
        return "%s-%04d" % (key, nxt)

    def event_code(self, moment: datetime) -> str:
        return self._next_code("EVT", moment)

    def alert_code(self, moment: datetime) -> str:
        return self._next_code("ALR", moment)

    # ----------------------------------------------------------------- cursors

    def _recover_cursors(self, session: Session) -> None:
        """Recover the read position from the agent's own previous output."""
        self.cursors.reading_id = int(session.execute(
            select(func.coalesce(func.max(OperationalEvent.triggering_reading_id), 0))
        ).scalar_one() or 0)

        for category, attribute in (
            ("machine_output", "progress_at"),
            ("quality", "inspection_at"),
            ("inventory", "movement_at"),
        ):
            newest = session.execute(
                select(func.max(OperationalEvent.detected_at)).where(
                    OperationalEvent.event_category == category)
            ).scalar_one()
            setattr(self.cursors, attribute, newest)
        self.cursors.scrap_at = self.cursors.inspection_at
        self.cursors.cycle_at = self.cursors.progress_at

    # ------------------------------------------------------------- suppression

    def refresh_suppression(self, session: Session) -> None:
        """Recompute which machines have their events suppressed this cycle.

        E13 rule 7 names three conditions: the machine is in ``setup``, it is in
        ``down_planned``, or it has an in-progress work record. ``offline`` is included
        because a machine that is not in service cannot produce a meaningful reading,
        and the alert vocabulary carries ``machine_offline`` precisely for it.
        """
        self._suppressed = {}

        for status in session.scalars(select(MachineOperationalStatus)):
            state = status.current_state.value
            reason = SUPPRESSED_STATES.get(state)
            if reason is not None:
                self._suppressed[status.machine_id] = reason

        working = session.scalars(
            select(MaintenanceWorkRecord.machine_id).where(
                MaintenanceWorkRecord.work_status.in_(ACTIVE_WORK_STATUSES))
        )
        for machine_id in working:
            self._suppressed[machine_id] = "maintenance_in_progress"

    def suppression_for(self, machine_id: int | None) -> str | None:
        """The reason this machine's events are suppressed, or ``None``."""
        if machine_id is None:
            return None
        return self._suppressed.get(machine_id)

    # --------------------------------------------------------------- utilities

    def shift_at(self, moment: datetime) -> Shift:
        """The production shift containing ``moment``.

        Every event records the shift it occurred in. Shift windows are local wall-clock
        and every stored timestamp is UTC, so the instant is converted through
        ``plant.timezone`` before comparison -- the same resolution the simulator used,
        so an event and the reading that triggered it agree on the shift.

        Only production shifts are considered: the general shift overlaps them, and
        choosing it would make two shifts contain the same instant.
        """
        clock = moment.astimezone(self.master.timezone).time()
        for shift in self.master.production_shifts:
            if shift.crosses_midnight:
                if clock >= shift.start_time or clock < shift.end_time:
                    return shift
            elif shift.start_time <= clock < shift.end_time:
                return shift
        return self.master.production_shifts[0]

    def line_of(self, machine_id: int | None) -> int | None:
        if machine_id is None:
            return None
        machine = self.master.machines.get(machine_id)
        return machine.production_line_id if machine is not None else None

    def stale_after(self, machine: Machine) -> timedelta | None:
        """How long telemetry silence must last before it counts as stale.

        Three times the **longest** declared sampling interval for the machine's type,
        so a parameter sampled every 60 seconds does not make a 5-second parameter look
        stale. Returns ``None`` when the type declares no parameters, in which case the
        machine emits no telemetry and staleness is meaningless.
        """
        declarations = self.master.declarations_by_type.get(machine.machine_type_id, [])
        if not declarations:
            return None
        longest = max(int(d.sampling_interval_seconds) for d in declarations)
        return timedelta(seconds=longest * STALE_SAMPLING_INTERVALS)
