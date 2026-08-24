"""Transition T1 — readings become features.

Builds the exact feature vector §E15 specifies: one eight-field block per parameter
where ``machine_type_parameter.is_ml_feature = 1``, plus a ten-field machine-level block.
Nothing is invented; every field in both blocks is named in the document.

The machine-level block is what makes the model more than a threshold checker. A
parameter drifting on a machine 462 hours into a 500-hour interval and 78 % through its
MTBF is a different proposition from the same drift on a freshly serviced machine, and
none of that context is in the telemetry.

**Three gates decide sufficiency**, per §11.2, and each can stop the pipeline:

1. **Validity.** Only readings with ``quality_flag = 'valid'`` contribute; the rest are
   counted in ``excluded_reading_count`` (E15 rule 2).
2. **Completeness.** ``data_completeness_pct`` is computed against the expected count
   derived from ``sampling_interval_seconds`` and the window length -- not estimated
   (E15 rule 4). Below the ``business_rule`` threshold the snapshot is insufficient.
3. **Window integrity.** A window spanning a maintenance intervention is insufficient,
   because features mixing pre-repair and post-repair condition describe a machine that
   does not exist (E15 rule 7).

When a gate fails the snapshot is still written, with ``is_sufficient_for_inference = 0``
and a reason. That row is what makes the platform's silence explicable.

**One window and a whole history are the same computation.** :meth:`FeatureExtractor.
extract` scores one instant; :meth:`FeatureExtractor.walk` produces every window across a
machine's history for training. Both load an :class:`_History` and hand it to the same
private window builder, so a training vector and a serving vector cannot drift apart --
and ``walk`` loads its history in **bounded segments** rather than per window. Querying
the reading table once per window per parameter turned a few hundred windows into
thousands of range scans; the segment loader makes it a handful of queries per day of
history, which matters because the training walk is the only part of this agent that
touches a large table more than once.
"""

from __future__ import annotations

import statistics
from bisect import bisect_right
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any, Iterator

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.master import Machine
from models.operational import (
    CycleHistory,
    MachineOperationalStatus,
    MachineSensorReading,
    MaintenanceWorkRecord,
    OperationalEvent,
    ScrapRecord,
)

from prediction.context import (
    MACHINE_FEATURES,
    PARAMETER_FEATURES,
    PredictionContext,
)

# Work statuses whose presence in the window means a repair touched it.
MAINTENANCE_STATUSES = ("in_progress", "awaiting_parts", "completed", "closed")

# How much history one segment of a training walk holds. Large enough that the query
# count stays proportional to the history rather than to the window count, small enough
# that a 90-day telemetry retention never has to be resident all at once.
SEGMENT_HOURS = 24

# The machine-level block needs a 24-hour tail for its two count features, which can
# reach further back than the feature window itself.
COUNT_WINDOW_HOURS = 24

# Machine states in which the telemetry describes a working machine.
RUNNING_STATES = ("running", "idle", "starved", "blocked", "setup")


@dataclass
class ExtractedWindow:
    """One machine's feature window, ready to become a snapshot row."""

    machine_id: int
    generated_at: datetime
    window_from: datetime
    lookback_seconds: int
    feature_values: dict[str, Any]
    source_reading_count: int
    excluded_reading_count: int
    data_completeness_pct: Decimal
    is_sufficient: bool
    insufficiency_reason: str | None = None
    ordered_features: dict[str, float] = field(default_factory=dict)
    """The flat name-to-value mapping the model consumes, derived from the document's
    nested structure so the stored vector and the model input cannot diverge."""


@dataclass
class _Series:
    """One parameter's readings over a loaded span, as parallel sorted lists."""

    times: list[datetime] = field(default_factory=list)
    values: list[float] = field(default_factory=list)
    valid: list[bool] = field(default_factory=list)


@dataclass
class _History:
    """Everything one machine's windows are computed from, over a loaded span.

    Held as sorted primitive lists rather than ORM rows: a window is a slice, and
    ``bisect`` over a sorted list of instants is the whole of the indexing this needs.
    """

    readings: dict[int, _Series] = field(default_factory=dict)
    cycle_times: list[datetime] = field(default_factory=list)
    cycle_deviations: list[float] = field(default_factory=list)
    event_times: list[datetime] = field(default_factory=list)
    scrap_times: list[datetime] = field(default_factory=list)
    maintenance: list[tuple[datetime, datetime]] = field(default_factory=list)
    status: MachineOperationalStatus | None = None


class FeatureExtractor:
    """Turns a lookback window of operational data into the documented vector."""

    def __init__(self, context: PredictionContext) -> None:
        self.context = context

    # ------------------------------------------------------------------ one window

    def extract(
        self,
        session: Session,
        machine: Machine,
        *,
        generated_at: datetime,
        lookback_seconds: int,
        at_reference_instant: bool = True,
    ) -> ExtractedWindow:
        """Build the window ending at ``generated_at``.

        ``at_reference_instant`` states whether the window ends *now*.
        ``machine_operational_status`` holds one mutable row per machine describing its
        present state, so the ``machine_not_running`` gate can only be evaluated for a
        window that ends at the current instant. Applying it to a back-filled historical
        window would test today's state against last week's data, and a machine that
        happened to be down when the walk started would have its whole history discarded.
        For historical windows the gate is skipped: a machine that was not running
        emitted no telemetry, so completeness already excludes those windows on the
        evidence rather than on a guess.
        """
        window_from = generated_at - timedelta(seconds=lookback_seconds)
        earliest_needed = min(
            window_from, generated_at - timedelta(hours=COUNT_WINDOW_HOURS))
        history = self._load(session, machine, earliest_needed, generated_at)
        return self._window(
            machine, history,
            generated_at=generated_at,
            lookback_seconds=lookback_seconds,
            at_reference_instant=at_reference_instant,
        )

    # ------------------------------------------------------------- whole history

    def walk(
        self,
        session: Session,
        machine: Machine,
        *,
        lookback_seconds: int,
        stride_seconds: int,
    ) -> Iterator[ExtractedWindow]:
        """Every window across the machine's telemetry history, oldest first.

        History is loaded one segment at a time and each segment's windows are sliced
        from memory, so the number of queries follows the length of the history rather
        than the number of windows.
        """
        bounds = session.execute(
            select(
                func.min(MachineSensorReading.recorded_at),
                func.max(MachineSensorReading.recorded_at),
            ).where(MachineSensorReading.machine_id == machine.machine_id)
        ).one()
        earliest, latest = bounds
        if earliest is None or latest is None:
            return

        tail = timedelta(
            seconds=max(lookback_seconds, COUNT_WINDOW_HOURS * 3600))
        segment = timedelta(hours=SEGMENT_HOURS)
        stride = timedelta(seconds=stride_seconds)

        moment = earliest + timedelta(seconds=lookback_seconds)
        while moment <= latest:
            segment_end = min(moment + segment, latest)
            history = self._load(session, machine, moment - tail, segment_end)
            while moment <= segment_end:
                yield self._window(
                    machine, history,
                    generated_at=moment,
                    lookback_seconds=lookback_seconds,
                    at_reference_instant=False,
                )
                moment = moment + stride

    # ------------------------------------------------------------------- loading

    def _load(
        self,
        session: Session,
        machine: Machine,
        since: datetime,
        until: datetime,
    ) -> _History:
        """Six queries for everything the windows in ``[since, until]`` need."""
        master = self.context.master
        history = _History()

        declarations = master.ml_parameters_by_type.get(machine.machine_type_id, [])
        for declaration in declarations:
            history.readings[declaration.machine_parameter_id] = _Series()
        if declarations:
            for parameter_id, recorded_at, value, flag in session.execute(
                select(
                    MachineSensorReading.machine_parameter_id,
                    MachineSensorReading.recorded_at,
                    MachineSensorReading.reading_value,
                    MachineSensorReading.quality_flag,
                )
                .where(
                    MachineSensorReading.machine_id == machine.machine_id,
                    MachineSensorReading.machine_parameter_id.in_(
                        [d.machine_parameter_id for d in declarations]),
                    MachineSensorReading.recorded_at > since,
                    MachineSensorReading.recorded_at <= until,
                )
                .order_by(MachineSensorReading.recorded_at)
            ):
                series = history.readings.get(parameter_id)
                if series is None:
                    continue
                series.times.append(recorded_at)
                series.values.append(float(value))
                series.valid.append(flag.value == "valid")

        for ended_at, deviation in session.execute(
            select(CycleHistory.cycle_ended_at,
                   CycleHistory.deviation_from_standard_pct)
            .where(
                CycleHistory.machine_id == machine.machine_id,
                CycleHistory.interrupted.is_(False),
                CycleHistory.deviation_from_standard_pct.is_not(None),
                CycleHistory.cycle_ended_at > since,
                CycleHistory.cycle_ended_at <= until,
            )
            .order_by(CycleHistory.cycle_ended_at)
        ):
            history.cycle_times.append(ended_at)
            history.cycle_deviations.append(float(deviation))

        history.event_times = list(session.scalars(
            select(OperationalEvent.detected_at).where(
                OperationalEvent.machine_id == machine.machine_id,
                OperationalEvent.detected_at > since,
                OperationalEvent.detected_at <= until,
            ).order_by(OperationalEvent.detected_at)
        ))
        history.scrap_times = list(session.scalars(
            select(ScrapRecord.recorded_at).where(
                ScrapRecord.attributed_machine_id == machine.machine_id,
                ScrapRecord.recorded_at > since,
                ScrapRecord.recorded_at <= until,
            ).order_by(ScrapRecord.recorded_at)
        ))

        # E15 rule 7 needs the repair intervals that overlap the loaded span. A job with
        # no closure yet is treated as reaching the end of the span, because an open
        # repair is still touching the machine.
        for started_at, completed_at, closed_at in session.execute(
            select(
                MaintenanceWorkRecord.started_at,
                MaintenanceWorkRecord.completed_at,
                MaintenanceWorkRecord.closed_at,
            ).where(
                MaintenanceWorkRecord.machine_id == machine.machine_id,
                MaintenanceWorkRecord.work_status.in_(MAINTENANCE_STATUSES),
                MaintenanceWorkRecord.started_at.is_not(None),
                MaintenanceWorkRecord.started_at <= until,
            )
        ):
            history.maintenance.append(
                (started_at, closed_at or completed_at or until))

        history.status = session.scalars(
            select(MachineOperationalStatus).where(
                MachineOperationalStatus.machine_id == machine.machine_id)
        ).first()
        return history

    # ---------------------------------------------------------- window assembly

    def _window(
        self,
        machine: Machine,
        history: _History,
        *,
        generated_at: datetime,
        lookback_seconds: int,
        at_reference_instant: bool,
    ) -> ExtractedWindow:
        context = self.context
        master = context.master
        window_from = generated_at - timedelta(seconds=lookback_seconds)

        declarations = master.ml_parameters_by_type.get(machine.machine_type_id, [])
        parameters_block: dict[str, Any] = {}
        flat: dict[str, float] = {}
        total_valid = 0
        total_excluded = 0
        expected_total = 0
        sensor_fault = False

        for declaration in declarations:
            parameter = master.parameters[declaration.machine_parameter_id]
            series = history.readings.get(
                declaration.machine_parameter_id, _Series())
            start = bisect_right(series.times, window_from)
            end = bisect_right(series.times, generated_at)
            valid = [
                (series.times[i], series.values[i])
                for i in range(start, end) if series.valid[i]
            ]
            excluded = (end - start) - len(valid)
            total_valid += len(valid)
            total_excluded += excluded

            interval = max(int(declaration.sampling_interval_seconds), 1)
            expected_total += max(lookback_seconds // interval, 1)
            if excluded > 0 and excluded >= len(valid):
                sensor_fault = True

            block = self._parameter_block(machine, declaration, valid)
            parameters_block[parameter.machine_parameter_code] = block
            for name, value in block.items():
                flat["%s.%s" % (parameter.machine_parameter_code, name)] = value

        machine_block = self._machine_block(
            machine, history, window_from, generated_at)
        flat.update(machine_block)

        completeness = (
            min(100.0, (total_valid / expected_total) * 100.0)
            if expected_total > 0 else 0.0
        )

        reason: str | None = None
        sufficient = True
        if not declarations or total_valid == 0:
            sufficient, reason = False, "insufficient_history"
        elif any(start <= generated_at and end >= window_from
                 for start, end in history.maintenance):
            sufficient, reason = False, "window_spans_maintenance"
        elif sensor_fault:
            sufficient, reason = False, "sensor_fault"
        elif completeness < context.completeness_minimum:
            sufficient, reason = False, "completeness_below_threshold"
        elif at_reference_instant and not (
            history.status is not None
            and history.status.current_state.value in RUNNING_STATES
        ):
            sufficient, reason = False, "machine_not_running"

        return ExtractedWindow(
            machine_id=machine.machine_id,
            generated_at=generated_at,
            window_from=window_from,
            lookback_seconds=lookback_seconds,
            feature_values={"parameters": parameters_block, "machine": machine_block},
            source_reading_count=total_valid,
            excluded_reading_count=total_excluded,
            data_completeness_pct=Decimal("%.2f" % completeness),
            is_sufficient=sufficient,
            insufficiency_reason=reason,
            ordered_features=flat,
        )

    # ------------------------------------------------------------- per parameter

    def _parameter_block(
        self,
        machine: Machine,
        declaration: Any,
        valid: list[tuple[datetime, float]],
    ) -> dict[str, float]:
        """The eight per-parameter fields §E15 names."""
        if not valid:
            return {name: 0.0 for name in PARAMETER_FEATURES}

        values = [value for _, value in valid]
        latest = values[-1]
        normal_max = float(declaration.normal_max)
        pct_above = (
            ((latest - normal_max) / normal_max) * 100.0
            if normal_max > 0 and latest > normal_max else 0.0
        )

        _, warning_high = self.context.master.warning_limits(
            machine, declaration.machine_parameter_id)
        seconds_above = 0.0
        if warning_high is not None:
            limit = float(warning_high)
            previous_at: datetime | None = None
            for moment, value in valid:
                if previous_at is not None and value > limit:
                    seconds_above += (moment - previous_at).total_seconds()
                previous_at = moment

        return {
            "latest_value": latest,
            "window_mean": statistics.fmean(values),
            "window_min": min(values),
            "window_max": max(values),
            "window_stddev": statistics.pstdev(values) if len(values) > 1 else 0.0,
            "slope_per_hour": self._slope_per_hour(valid),
            "pct_above_normal_max": pct_above,
            "seconds_above_warning_limit": seconds_above,
        }

    @staticmethod
    def _slope_per_hour(samples: list[tuple[datetime, float]]) -> float:
        """Least-squares slope in units per hour.

        The document's own example expresses the signal as "risen 0.31 mm/s per hour
        over four hours", which is a fitted gradient rather than an endpoint difference.
        """
        if len(samples) < 2:
            return 0.0
        base = samples[0][0]
        times = [(t - base).total_seconds() / 3600.0 for t, _ in samples]
        values = [v for _, v in samples]
        count = float(len(samples))
        mean_t = sum(times) / count
        mean_v = sum(values) / count
        variance = sum((t - mean_t) ** 2 for t in times)
        if variance <= 0:
            return 0.0
        return sum((t - mean_t) * (v - mean_v)
                   for t, v in zip(times, values)) / variance

    # ------------------------------------------------------------- machine block

    def _machine_block(
        self,
        machine: Machine,
        history: _History,
        window_from: datetime,
        generated_at: datetime,
    ) -> dict[str, float]:
        """The ten machine-level fields §E15 names, from four different entities.

        One honest limitation. Five of the ten -- ``accumulated_operating_hours``,
        ``hours_since_last_maintenance``, ``cycles_since_last_maintenance``,
        ``pct_of_design_life`` and ``pct_of_mtbf_elapsed`` -- derive from
        ``machine_operational_status``, which by design holds a single mutable row per
        machine describing its **present** position. They are exact for a window ending
        now and are the current values for a back-filled historical window. E15 names
        that table as a source for this entity and does not name
        ``machine_state_transition``, so the current row is used and the limit is stated
        rather than worked around with an undocumented derivation. The other five --
        ``mean_cycle_deviation_pct``, ``cycle_deviation_slope``, ``event_count_24h``,
        ``attributed_scrap_count_24h`` and ``age_days`` -- are computed against the
        window and are correct at any instant.
        """
        master = self.context.master
        machine_type = master.machine_types.get(machine.machine_type_id)
        status = history.status

        operating_hours = float(status.accumulated_operating_hours) if status else 0.0
        cycles = status.accumulated_cycle_count if status else 0
        hours_at_maintenance = (
            float(status.operating_hours_at_last_maintenance)
            if status is not None
            and status.operating_hours_at_last_maintenance is not None else 0.0
        )
        cycles_at_maintenance = (
            status.cycle_count_at_last_maintenance
            if status is not None
            and status.cycle_count_at_last_maintenance is not None else 0
        )
        since_maintenance = max(operating_hours - hours_at_maintenance, 0.0)

        design_life = float(machine_type.design_life_hours or 0) if machine_type else 0.0
        mtbf = float(machine_type.mtbf_hours or 0) if machine_type else 0.0

        start = bisect_right(history.cycle_times, window_from)
        end = bisect_right(history.cycle_times, generated_at)
        samples = [
            (history.cycle_times[i], history.cycle_deviations[i])
            for i in range(start, end)
        ]
        mean_deviation = (
            statistics.fmean([d for _, d in samples]) if samples else 0.0)

        day_from = generated_at - timedelta(hours=COUNT_WINDOW_HOURS)
        event_count = (
            bisect_right(history.event_times, generated_at)
            - bisect_right(history.event_times, day_from)
        )
        scrap_count = (
            bisect_right(history.scrap_times, generated_at)
            - bisect_right(history.scrap_times, day_from)
        )

        age_days = 0.0
        if machine.commissioned_date is not None:
            age_days = float(
                (generated_at.astimezone(master.timezone).date()
                 - machine.commissioned_date).days
            )

        return {
            "accumulated_operating_hours": operating_hours,
            "hours_since_last_maintenance": since_maintenance,
            "cycles_since_last_maintenance": float(
                max(cycles - cycles_at_maintenance, 0)),
            "pct_of_design_life": (
                (operating_hours / design_life) * 100.0 if design_life > 0 else 0.0),
            "pct_of_mtbf_elapsed": (
                (since_maintenance / mtbf) * 100.0 if mtbf > 0 else 0.0),
            "mean_cycle_deviation_pct": mean_deviation,
            "cycle_deviation_slope": self._slope_per_hour(samples),
            "event_count_24h": float(event_count),
            "attributed_scrap_count_24h": float(scrap_count),
            "age_days": age_days,
        }

    # -------------------------------------------------------------- model scope

    @staticmethod
    def feature_names(
        context: PredictionContext,
        machine_type_ids: list[int],
    ) -> list[str]:
        """The stable, ordered feature name list for a model scope.

        The union of ML-declared parameters across the machine types in the scope, so a
        model trained for a category accepts every machine in it. Stored in the model
        metadata and re-checked at load, because a silently reordered vector would make
        every probability wrong without raising anything.
        """
        names: list[str] = []
        codes: set[str] = set()
        for type_id in machine_type_ids:
            for declaration in context.master.ml_parameters_by_type.get(type_id, []):
                codes.add(
                    context.master.parameters[declaration.machine_parameter_id]
                    .machine_parameter_code
                )
        for code in sorted(codes):
            for field_name in PARAMETER_FEATURES:
                names.append("%s.%s" % (code, field_name))
        names.extend(MACHINE_FEATURES)
        return names
