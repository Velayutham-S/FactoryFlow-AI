"""Machine output detection, from production progress and cycle history.

Two of the twelve documented event types:

=====================  =====================================================================
``output_shortfall``   achieved rate below ``product_line_capability.max_hourly_output_units``
``cycle_deviation``    mean cycle time above the capability's own implied ceiling
=====================  =====================================================================

**Both limits come from the capability row the run pins.** §E5 states that
``current_rate_units_per_hour`` is "compared against the capability's
``max_hourly_output_units`` to detect underperformance", which gives ``output_shortfall``
its comparator directly.

``cycle_deviation`` uses the same row from the other side. A capability declares both a
standard ``cycle_time_seconds`` and a ``max_hourly_output_units``, and the second implies
a per-unit ceiling of ``3600 / max_hourly_output_units``. On ``LN-01`` that is 150 seconds
against a 145-second standard -- master data itself declares the tolerance, so no constant
is introduced. A mean cycle time above the implied ceiling is the documented degradation
signal of §E7: "a machine that is beginning to fail takes longer to complete each cycle,
and it does so before any sensor threshold is crossed."

**Interrupted cycles are excluded.** §E7 rule 4 is explicit that including them "would let
one breakdown masquerade as extreme cycle-time degradation". They carry a NULL deviation
for exactly this reason.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.operational import CycleHistory, ProductionProgress, ProductionRun

from monitoring.context import BreachEpisode, Detection, MonitoringContext

# Minimum completed cycles before a mean is trusted. §E7: degradation is "invisible in
# any single cycle and unmistakable across a hundred", so a mean over a handful of parts
# is noise. This is a sample-size floor, not a threshold on the measured quantity.
MINIMUM_CYCLE_SAMPLE = 20


class ProductionMonitor:
    """Output rate and cycle time against the pinned capability."""

    def __init__(self, context: MonitoringContext) -> None:
        self.context = context

    def evaluate(self, session: Session) -> list[Detection]:
        detections: list[Detection] = []
        detections.extend(self._output_shortfall(session))
        detections.extend(self._cycle_deviation(session))
        return detections

    def _output_shortfall(self, session: Session) -> list[Detection]:
        """Progress snapshots whose achieved rate is below the capability rate."""
        context = self.context
        master = context.master
        cursor = context.cursors.progress_at

        query = select(ProductionProgress).order_by(ProductionProgress.snapshot_at)
        if cursor is not None:
            query = query.where(ProductionProgress.snapshot_at > cursor)

        detections: list[Detection] = []
        newest = cursor
        for progress in session.scalars(query):
            newest = (
                progress.snapshot_at if newest is None
                else max(newest, progress.snapshot_at)
            )
            run = session.get(ProductionRun, progress.production_run_id)
            if run is None:
                continue
            capability = master.capabilities.get(run.product_line_capability_id)
            if capability is None:
                continue

            expected = capability.max_hourly_output_units
            achieved = progress.current_rate_units_per_hour
            if expected is None or achieved >= expected:
                continue
            # A snapshot taken before the first unit completed reports a zero rate that
            # says nothing about performance.
            if progress.quantity_good_cumulative <= 0:
                continue

            line = master.lines.get(run.production_line_id)
            product = master.products.get(run.product_id)
            severity_id = self._shortfall_severity(progress, expected, achieved)

            # ck_oe_machine_subject_required and §E13 rule 2 both require a machine on a
            # machine_output event, so a line-level shortfall has to name the machine
            # responsible for the line's rate.
            subject = self._rate_governing_machine(run.production_line_id)
            if subject is None:
                continue
            if context.suppression_for(subject.machine_id) is not None:
                continue

            detections.append(Detection(
                category="machine_output",
                event_type="output_shortfall",
                detected_at=progress.snapshot_at,
                severity_level_id=severity_id,
                correlation_subject=subject.machine_code,
                machine_id=subject.machine_id,
                production_line_id=run.production_line_id,
                production_run_id=run.production_run_id,
                observed_value=achieved,
                threshold_value_breached=expected,
                threshold_direction="below_low",
                detection_note=(
                    "%s achieving %s units/hour against a capability rate of %s "
                    "units/hour for %s on %s; %s%% complete, schedule variance "
                    "%d minutes. Rate attributed to %s."
                    % (
                        line.line_name if line is not None else "line",
                        achieved, expected,
                        product.product_code if product is not None else "product",
                        run.production_run_code,
                        progress.percent_complete,
                        progress.schedule_variance_minutes,
                        subject.machine_code,
                    )
                ),
            ))

        context.cursors.progress_at = newest
        return detections

    def _rate_governing_machine(self, line_id: int):
        """The machine a line's output rate is attributed to.

        The bottleneck, when master data declares one. ``machine.is_bottleneck`` carries
        a partial unique index allowing at most one per line, and §42.4 explains why:
        two would make impact arithmetic contradictory. The bottleneck is by definition
        the station that sets the line's rate, so a shortfall belongs to it.

        With no bottleneck declared, the last station is used: that is where a finished
        unit leaves the line, and therefore where the achieved rate was measured.
        """
        machines = [
            m for m in self.context.master.machines.values()
            if m.production_line_id == line_id
            and m.lifecycle_status.value == "in_service"
        ]
        if not machines:
            return None
        for machine in machines:
            if machine.is_bottleneck:
                return machine
        return max(machines, key=lambda m: m.line_position)

    def _shortfall_severity(
        self,
        progress: ProductionProgress,
        expected: Decimal,
        achieved: Decimal,
    ) -> int:
        """Severity for an output shortfall.

        A run already behind schedule is the more serious case, and
        ``is_behind_schedule`` is a flag the simulator computed against the plan rather
        than something invented here. Everything else falls to the least severe level,
        because a run running slightly slow while still on time is not urgent.
        """
        master = self.context.master
        if not progress.is_behind_schedule:
            return master.least_severe_id()

        ranked = sorted(master.severities.values(), key=lambda s: s.severity_rank)
        # One step more severe than the floor, never the most severe: an output shortfall
        # is a schedule problem, and reserving the top level for conditions that stop the
        # line is what keeps the scale meaningful.
        if len(ranked) >= 2:
            return ranked[-2].failure_severity_level_id
        return master.least_severe_id()

    def _cycle_deviation(self, session: Session) -> list[Detection]:
        """Machines whose mean cycle time exceeds the capability's implied ceiling.

        **The cursor selects which machines to re-examine; it does not bound the sample.**
        Averaging only the cycles that arrived since the last monitoring run would make
        the sample size depend on how often the caller chooses to run a cycle, so a
        frequent caller would never accumulate enough cycles to see a trend and an
        infrequent one would average a whole run into a single figure. §E7 puts the
        signal "across a hundred" cycles, so the mean is taken over the most recent
        window of cycles for the pair regardless of when monitoring last ran.
        """
        context = self.context
        master = context.master
        cursor = context.cursors.cycle_at

        # Which machine-and-run pairs have produced cycles since the last look.
        touched = select(
            CycleHistory.machine_id,
            CycleHistory.production_run_id,
            func.max(CycleHistory.cycle_ended_at).label("latest"),
        ).group_by(CycleHistory.machine_id, CycleHistory.production_run_id)
        if cursor is not None:
            touched = touched.where(CycleHistory.cycle_ended_at > cursor)

        detections: list[Detection] = []
        newest = cursor
        for machine_id, run_id, latest in session.execute(touched).all():
            newest = latest if newest is None else max(newest, latest)
            if context.suppression_for(machine_id) is not None:
                continue

            # The most recent completed cycles for this pair, interrupted ones excluded
            # per §E7 rule 4: including them would let one breakdown masquerade as
            # extreme cycle-time degradation.
            recent = session.execute(
                select(CycleHistory.cycle_time_seconds)
                .where(
                    CycleHistory.machine_id == machine_id,
                    CycleHistory.production_run_id == run_id,
                    CycleHistory.interrupted.is_(False),
                )
                .order_by(CycleHistory.cycle_ended_at.desc())
                .limit(MINIMUM_CYCLE_SAMPLE)
            ).scalars().all()
            samples = len(recent)
            if samples < MINIMUM_CYCLE_SAMPLE:
                continue
            mean_seconds = sum(float(v) for v in recent) / float(samples)

            run = session.get(ProductionRun, run_id)
            if run is None:
                continue
            capability = master.capabilities.get(run.product_line_capability_id)
            if capability is None or not capability.max_hourly_output_units:
                continue

            standard = float(capability.cycle_time_seconds)
            ceiling = 3600.0 / float(capability.max_hourly_output_units)
            if standard <= 0 or ceiling <= 0:
                continue
            observed = float(mean_seconds)
            episode_key = (machine_id, run_id, "cycle")
            if observed <= ceiling:
                context.episodes.pop(episode_key, None)  # recovered; re-arm
                continue
            existing = context.episodes.get(episode_key)
            if existing is not None and existing.reported:
                continue  # already reported for this run; one case, not one per cycle

            machine = master.machines.get(machine_id)
            if machine is None:
                continue
            context.episodes[episode_key] = BreachEpisode(
                started_at=latest, reported=True)

            observed_pct = ((observed - standard) / standard) * 100.0
            allowed_pct = ((ceiling - standard) / standard) * 100.0

            detections.append(Detection(
                category="machine_output",
                event_type="cycle_deviation",
                detected_at=latest,
                severity_level_id=master.least_severe_id(),
                correlation_subject=machine.machine_code,
                machine_id=machine_id,
                production_line_id=machine.production_line_id,
                production_run_id=run_id,
                observed_value=Decimal("%.2f" % observed_pct),
                threshold_value_breached=Decimal("%.2f" % allowed_pct),
                threshold_direction="above_high",
                sustained_duration_seconds=None,
                detection_note=(
                    "%s averaging %.2fs per cycle over %d cycles against a %.2fs "
                    "standard, %.2f%% slow; the capability's %s units/hour implies a "
                    "%.2fs ceiling (%.2f%%)"
                    % (machine.machine_code, observed, samples, standard, observed_pct,
                       capability.max_hourly_output_units, ceiling, allowed_pct)
                ),
            ))

        context.cursors.cycle_at = newest
        return detections
