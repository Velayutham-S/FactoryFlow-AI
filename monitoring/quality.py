"""Quality detection, from inspection results and scrap records.

Two of the twelve documented event types:

=========================  ==============================================================
``scrap_rate_exceeded``    scrap rate above ``product.target_scrap_rate_pct``
``quality_failure_rate``   inspection fail rate above the product's own quality tolerance
=========================  ==============================================================

**Both comparators are master data.** §E5 states that ``scrap_rate_pct`` is "compared
against ``product.target_scrap_rate_pct`` to detect a developing quality problem", and
§E9's consumer table says the Monitoring Agent "compares scrap rate against
``product.target_scrap_rate_pct`` and detects rising attributed scrap per machine". That
column is the only quality tolerance master data carries, and it is named for this agent
twice, so it is the comparator for both detections.

**Attribution is what makes quality a machine signal.** §E8 separates ``machine_id``, the
station that inspected, from ``attributed_machine_id``, the machine that caused the
defect. On a machining line the coordinate measuring machine at position 3 inspects parts
made by the mill at position 1, and a bore roundness deviation found at the inspection
station was created upstream. Detection follows the attribution: §E8 rule 9 says a rising
fail rate "across consecutive inspections for one attributed machine is a quality
condition the Monitoring Agent may raise as an event", so the subject of a quality event
is the attributed machine and never the inspecting station.

An unattributed failure raises nothing here. §E8 rule 4 pairs
``attributed_machine_id`` with ``attributed_failure_category_id`` precisely so that a
defect with no identified mechanism is not treated as machine evidence, and blaming a
station for a supplier's casting flaw would corrupt the preventable-loss figure the
platform is judged on.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from models.operational import (
    ProductionProgress,
    ProductionRun,
    QualityInspectionResult,
)

from monitoring.context import BreachEpisode, Detection, MonitoringContext

# §E8 rule 9 puts the signal in a fail rate "across consecutive inspections", so the
# floor is a number of inspections rather than a number of units. One inspection finding
# a bad part is a finding; a rate needs more than one inspection to be rising at all.
MINIMUM_INSPECTIONS = 2


class QualityMonitor:
    """Scrap rate and inspection failure rate against the product's tolerance."""

    def __init__(self, context: MonitoringContext) -> None:
        self.context = context

    def evaluate(self, session: Session) -> list[Detection]:
        detections: list[Detection] = []
        detections.extend(self._failure_rate(session))
        detections.extend(self._scrap_rate(session))
        return detections

    def _failure_rate(self, session: Session) -> list[Detection]:
        """Attributed machines whose inspection fail rate exceeds the tolerance."""
        context = self.context
        master = context.master
        cursor = context.cursors.inspection_at

        # The cursor selects which attributed machines to re-examine; the rate itself is
        # taken over every inspection for the pair. §E8 rule 9 puts the signal in a rate
        # "across consecutive inspections", and a sample bounded by how often monitoring
        # happens to run would rarely hold enough inspected units to be a rate at all.
        touched = select(
            QualityInspectionResult.attributed_machine_id,
            QualityInspectionResult.production_run_id,
        ).where(QualityInspectionResult.attributed_machine_id.is_not(None))
        if cursor is not None:
            touched = touched.where(QualityInspectionResult.inspected_at > cursor)

        detections: list[Detection] = []
        newest = cursor
        for machine_id, run_id in set(session.execute(touched).all()):
            inspected, failed, latest, category_id, inspections = session.execute(
                select(
                    func.sum(QualityInspectionResult.sample_size),
                    func.sum(QualityInspectionResult.fail_count),
                    func.max(QualityInspectionResult.inspected_at),
                    func.min(QualityInspectionResult.attributed_failure_category_id),
                    func.count(),
                ).where(
                    QualityInspectionResult.attributed_machine_id == machine_id,
                    QualityInspectionResult.production_run_id == run_id,
                )
            ).one()
            if latest is not None:
                newest = latest if newest is None else max(newest, latest)
            if not inspected or not failed or inspections < MINIMUM_INSPECTIONS:
                continue
            # No current-state suppression gate here, deliberately. These inspections
            # already happened; a machine now under repair does not make the defects it
            # produced beforehand less real, and discarding them would throw away the
            # evidence that justified the repair. E14 rule 10 gives the right mechanism:
            # the event is recorded and the alert is suppressed, because suppression
            # stops notification and never stops recording.
            episode_key = (machine_id, run_id, "quality")
            existing = context.episodes.get(episode_key)
            if existing is not None and existing.reported:
                continue  # one case per machine and run, not one per inspection

            run = session.get(ProductionRun, run_id)
            if run is None:
                continue
            product = master.products.get(run.product_id)
            if product is None or product.target_scrap_rate_pct is None:
                continue

            tolerance = float(product.target_scrap_rate_pct)
            observed = (float(failed) / float(inspected)) * 100.0
            if observed <= tolerance:
                continue

            machine = master.machines.get(machine_id)
            if machine is None:
                continue
            context.episodes[episode_key] = BreachEpisode(
                started_at=latest, reported=True)
            mechanism = ""
            if category_id is not None:
                mechanism = (
                    " Attributed mechanism: %s."
                    % master.failure_category_name(category_id)
                )

            detections.append(Detection(
                category="quality",
                event_type="quality_failure_rate",
                detected_at=latest,
                severity_level_id=self._quality_severity(product),
                correlation_subject=machine.machine_code,
                machine_id=machine_id,
                production_line_id=machine.production_line_id,
                production_run_id=run_id,
                observed_value=Decimal("%.2f" % observed),
                threshold_value_breached=Decimal("%.2f" % tolerance),
                threshold_direction="above_high",
                detection_note=(
                    "%d of %d units inspected on %s failed, a %.2f%% failure rate "
                    "attributed to %s against a %.2f%% target for %s.%s"
                    % (failed, inspected, run.production_run_code, observed,
                       machine.machine_code, tolerance, product.product_code, mechanism)
                ),
            ))

        context.cursors.inspection_at = newest
        return detections

    def _scrap_rate(self, session: Session) -> list[Detection]:
        """Runs whose cumulative scrap rate exceeds the product's target.

        ``production_progress.scrap_rate_pct`` is already computed per snapshot by the
        simulator against the same definition the product's target uses, so the
        comparison is snapshot against target with nothing recomputed.
        """
        context = self.context
        master = context.master
        cursor = context.cursors.scrap_at

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
            if progress.quantity_scrapped_cumulative <= 0:
                continue

            run = session.get(ProductionRun, progress.production_run_id)
            if run is None:
                continue
            product = master.products.get(run.product_id)
            if product is None or product.target_scrap_rate_pct is None:
                continue

            tolerance = product.target_scrap_rate_pct
            observed = progress.scrap_rate_pct
            if observed <= tolerance:
                continue

            # The run's line is the subject: scrap accumulates against the run, and
            # attributing it to one station would claim an attribution this aggregate
            # does not carry. A quality event requires a machine subject, so the run's
            # bottleneck-free choice is the line's first in-service machine, which is
            # where the material entered.
            machines = [
                m for m in master.machines.values()
                if m.production_line_id == run.production_line_id
            ]
            if not machines:
                continue
            subject = min(machines, key=lambda m: m.line_position)
            if context.suppression_for(subject.machine_id) is not None:
                continue

            detections.append(Detection(
                category="quality",
                event_type="scrap_rate_exceeded",
                detected_at=progress.snapshot_at,
                severity_level_id=self._quality_severity(product),
                correlation_subject=subject.machine_code,
                machine_id=subject.machine_id,
                production_line_id=run.production_line_id,
                production_run_id=run.production_run_id,
                observed_value=observed,
                threshold_value_breached=tolerance,
                threshold_direction="above_high",
                detection_note=(
                    "Scrap rate %s%% on %s exceeds the %s%% target for %s; %s units "
                    "scrapped of %s produced"
                    % (observed, run.production_run_code, tolerance,
                       product.product_code, progress.quantity_scrapped_cumulative,
                       progress.quantity_good_cumulative
                       + progress.quantity_scrapped_cumulative
                       + progress.quantity_rework_cumulative)
                ),
            ))

        context.cursors.scrap_at = newest
        return detections

    def _quality_severity(self, product) -> int:
        """Severity for a quality condition.

        ``product.quality_criticality`` is master data's statement of how much a defect
        on this product matters, so it selects the level rather than a fixed choice. A
        safety-critical product's quality condition is treated as seriously as the
        product is declared to be.
        """
        master = self.context.master
        ranked = sorted(master.severities.values(), key=lambda s: s.severity_rank)
        criticality = product.quality_criticality.value
        if criticality == "safety_critical" and len(ranked) >= 2:
            return ranked[1].failure_severity_level_id
        if criticality == "high" and len(ranked) >= 3:
            return ranked[2].failure_severity_level_id
        return master.least_severe_id()
