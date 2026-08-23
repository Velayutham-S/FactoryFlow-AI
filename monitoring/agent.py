"""The Monitoring Agent. One cycle analyses the current operational data.

A cycle runs the four detectors over everything that arrived since the previous cycle,
writes each detection as an ``operational_event`` on its correlating ``operational_alert``,
and then advances the alert lifecycle. Nothing is scheduled here: **the caller decides
when a cycle runs.** There is no background thread, no timer, and no loop that owns the
process.

Boundary order within a cycle, and why:

1. **Suppression is refreshed first.** §E13 rule 7 gates detection for machines in setup,
   in planned downtime, or under repair. Refreshing after detection would evaluate
   conditions the agent then had to discard.
2. **Detection reads, then T-MON-1 writes.** Reading is done outside the write boundary so
   the write transaction is as short as possible -- SQLite permits one writer at a time
   database-wide, so a boundary held open across a long scan blocks every other writer.
3. **Lifecycle last.** Escalation, suppression and closure act on alerts including any
   opened moments earlier in the same cycle, which is the correct order: a condition
   detected and immediately answered by a completed repair should close in the cycle that
   found it.

**The agent reads operational data and master data, and writes only what it owns**:
``operational_event``, ``operational_alert``, and ``machine_operational_status.open_alert_count``
per §46.2. It writes nothing to the prediction, recommendation, notification, dashboard or
system tables.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from models.operational import (
    MachineSensorReading,
    OperationalAlert,
    OperationalEvent,
)
from models.session import (
    initialize_database,
    session_scope,
    shutdown_database,
)

from monitoring.alerts import LIVE_STATUSES, AlertOutcome, AlertWriter
from monitoring.context import MonitoringContext
from monitoring.errors import OperationalDataUnavailableError
from monitoring.inventory import InventoryMonitor
from monitoring.machine import MachineMonitor
from monitoring.production import ProductionMonitor
from monitoring.quality import QualityMonitor


@dataclass
class CycleReport:
    """What one monitoring cycle observed and wrote."""

    detections: int = 0
    events_written: int = 0
    alerts_opened: int = 0
    alerts_updated: int = 0
    severities_escalated: int = 0
    escalated_for_no_ack: int = 0
    suppressed: int = 0
    resolved: int = 0
    closed: int = 0
    by_category: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    alert_codes: list[str] = field(default_factory=list)

    @property
    def wrote_anything(self) -> bool:
        return bool(self.events_written or self.resolved or self.escalated_for_no_ack)


class MonitoringAgent:
    """Observes the factory the simulator produced and records what it detects."""

    def __init__(
        self,
        engine: Engine,
        session_factory: sessionmaker[Session],
        *,
        quiet: bool = False,
    ) -> None:
        self.engine = engine
        self.session_factory = session_factory
        self.quiet = quiet

        with session_scope(session_factory) as session:
            self._require_operational_data(session)
            self.context = MonitoringContext(session)

        self.machine = MachineMonitor(self.context)
        self.production = ProductionMonitor(self.context)
        self.quality = QualityMonitor(self.context)
        self.inventory = InventoryMonitor(self.context)
        self.alerts = AlertWriter(self.context)

    @staticmethod
    def _require_operational_data(session: Session) -> None:
        """Refuse to report a healthy factory the agent never examined.

        An empty telemetry table means the simulator has not run. Detecting nothing and
        reporting success would be indistinguishable from a factory with no problems,
        which is the one outcome a monitoring system must never fake.
        """
        readings = session.execute(
            select(func.count()).select_from(MachineSensorReading)
        ).scalar_one()
        if readings == 0:
            raise OperationalDataUnavailableError(
                "machine_sensor_reading is empty; the Factory Simulation Engine has "
                "not produced telemetry for this database, so there is nothing to "
                "monitor"
            )

    def say(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    # ------------------------------------------------------------------- a cycle

    def run_cycle(self) -> CycleReport:
        """Analyse everything that arrived since the previous cycle."""
        report = CycleReport()

        with session_scope(self.session_factory) as session:
            self.context.refresh_suppression(session)

        # Detection is read-only. Held outside the write boundary so the write lock is
        # taken for as short a time as possible.
        with session_scope(self.session_factory) as session:
            detections = []
            detections.extend(self.machine.evaluate(session))
            detections.extend(self.production.evaluate(session))
            detections.extend(self.quality.evaluate(session))
            detections.extend(self.inventory.evaluate(session))

        report.detections = len(detections)
        for detection in detections:
            report.by_category[detection.category] = (
                report.by_category.get(detection.category, 0) + 1)
            report.by_type[detection.event_type] = (
                report.by_type.get(detection.event_type, 0) + 1)

        outcome = AlertOutcome()

        # T-MON-1 — correlate and record. One boundary for the whole batch: every event
        # in it references an alert created or found inside the same transaction, and
        # §46.2 requires the find-or-create and the insert to be inseparable.
        if detections:
            with session_scope(self.session_factory) as session:
                self.alerts.record(session, detections, outcome)

        # T-MON-3 — lifecycle. Escalation, suppression, resolution and closure.
        reference = self._reference_instant()
        with session_scope(self.session_factory) as session:
            self.alerts.suppress_during_maintenance(session, outcome)
        with session_scope(self.session_factory) as session:
            self.alerts.escalate_unacknowledged(session, reference, outcome)
        with session_scope(self.session_factory) as session:
            self.alerts.resolve_after_maintenance(session, reference, outcome)

        report.events_written = outcome.events_written
        report.alerts_opened = outcome.alerts_opened
        report.alerts_updated = outcome.alerts_updated
        report.severities_escalated = outcome.severities_escalated
        report.escalated_for_no_ack = outcome.escalated_for_no_ack
        report.suppressed = outcome.suppressed
        report.resolved = outcome.resolved
        report.closed = outcome.closed
        report.alert_codes = outcome.codes
        return report

    def _reference_instant(self) -> datetime:
        """The moment the agent treats as 'now'.

        The newest operational timestamp, not wall-clock time. The agent analyses a
        recorded factory: measuring an acknowledgement window against the real clock
        would escalate every alert the instant the simulator stopped, which says nothing
        about the factory and everything about when the agent happened to run.
        """
        with session_scope(self.session_factory) as session:
            newest = session.execute(
                select(func.max(MachineSensorReading.recorded_at))
            ).scalar_one_or_none()
            if newest is None:
                newest = session.execute(
                    select(func.max(OperationalAlert.last_event_at))
                ).scalar_one_or_none()
        if newest is None:
            raise OperationalDataUnavailableError(
                "no operational timestamp available to anchor the monitoring cycle")
        return newest

    # ----------------------------------------------------------------- reporting

    def summary(self) -> dict[str, int]:
        """Counts across the two tables this agent owns, plus live alert state."""
        with session_scope(self.session_factory) as session:
            events = int(session.execute(
                select(func.count()).select_from(OperationalEvent)).scalar_one())
            alerts = int(session.execute(
                select(func.count()).select_from(OperationalAlert)).scalar_one())
            live = int(session.execute(
                select(func.count()).select_from(OperationalAlert)
                .where(OperationalAlert.alert_status.in_(LIVE_STATUSES))
            ).scalar_one())
            closed = int(session.execute(
                select(func.count()).select_from(OperationalAlert)
                .where(OperationalAlert.alert_status == "closed")
            ).scalar_one())
        return {
            "operational_event": events,
            "operational_alert": alerts,
            "alerts_live": live,
            "alerts_closed": closed,
        }


def monitor(
    database_path: str | Path,
    *,
    cycles: int = 1,
    quiet: bool = False,
) -> list[CycleReport]:
    """Run ``cycles`` monitoring cycles against a database the simulator has populated.

    The database is opened through ``models.session.initialize_database`` and closed
    through ``shutdown_database`` on every path, including failure. More than one cycle is
    useful because each analyses only what arrived since the last: the second cycle over
    unchanged data should detect nothing, which is the property that proves the agent is
    not re-reporting.
    """
    engine, session_factory = initialize_database(database_path)
    try:
        agent = MonitoringAgent(engine, session_factory, quiet=quiet)
        agent.say("Monitoring Agent")
        agent.say(
            "Master data: %d machines, %d threshold profiles with rules, "
            "%d inventory items"
            % (
                len(agent.context.master.machines),
                len(agent.context.master.rules_by_profile),
                len(agent.context.master.items),
            )
        )

        reports: list[CycleReport] = []
        for index in range(max(cycles, 1)):
            report = agent.run_cycle()
            reports.append(report)
            agent.say("")
            agent.say(
                "Cycle %d: %d detections, %d events, %d alerts opened, "
                "%d updated"
                % (index + 1, report.detections, report.events_written,
                   report.alerts_opened, report.alerts_updated)
            )
            for category in sorted(report.by_category):
                agent.say("  %-18s %d" % (category, report.by_category[category]))
            if report.by_type:
                agent.say("  types: %s" % ", ".join(
                    "%s=%d" % (name, count)
                    for name, count in sorted(report.by_type.items())))
            if report.severities_escalated:
                agent.say("  severity escalated on %d alert(s)"
                          % report.severities_escalated)
            if report.escalated_for_no_ack:
                agent.say("  escalated for no acknowledgement: %d"
                          % report.escalated_for_no_ack)
            if report.suppressed:
                agent.say("  suppressed during maintenance: %d" % report.suppressed)
            if report.closed:
                agent.say("  resolved and closed: %d" % report.closed)

        summary = agent.summary()
        agent.say("")
        agent.say("Monitoring output:")
        for name in ("operational_event", "operational_alert"):
            agent.say("  %-24s %d" % (name, summary[name]))
        agent.say("  %-24s %d live, %d closed"
                  % ("alert state", summary["alerts_live"], summary["alerts_closed"]))
        agent.say("")
        agent.say("Monitoring Complete.")
        return reports
    finally:
        shutdown_database(engine)


def main(argv: list[str] | None = None) -> int:
    """``python -m monitoring <database-path> [cycles]``."""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        print("usage: python -m monitoring <database-path> [cycles]", file=sys.stderr)
        return 2
    path = Path(args[0]).resolve()
    cycles = int(args[1]) if len(args) > 1 else 1
    try:
        monitor(path, cycles=cycles)
    except Exception as exc:  # noqa: BLE001 -- reported, never swallowed
        print("", file=sys.stderr)
        print("Monitoring failed.", file=sys.stderr)
        print("%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 1
    return 0
