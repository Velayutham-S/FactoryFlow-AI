"""Exceptions for the Monitoring Agent.

One root so a caller can catch the whole layer, and one subclass per cause a caller
could reasonably react to differently. Nothing here is logged-and-swallowed: a
monitoring cycle that could not complete must reach the caller, because a detection
that silently did not happen is indistinguishable from a factory with no problems.
"""

from __future__ import annotations


class MonitoringError(Exception):
    """Root of every error the Monitoring Agent raises."""


class MasterDataUnavailableError(MonitoringError):
    """Master data the agent needs to detect anything is missing.

    Detection is entirely master-data driven: thresholds come from
    ``alert_threshold_rule``, severities from ``failure_severity_level``, stock floors
    from ``inventory_item``. Without them the agent cannot evaluate a condition, and
    running anyway would report a healthy factory it never actually examined.
    """


class OperationalDataUnavailableError(MonitoringError):
    """The operational tables the agent reads are absent or empty.

    Distinct from :class:`MasterDataUnavailableError` because the remedy differs: this
    means the simulator has not run, not that the factory is misconfigured.
    """


class CorrelationError(MonitoringError):
    """An event could not be attached to an alert.

    ``operational_event.operational_alert_id`` is NOT NULL and set at insert, which is
    what makes the event immutable. If find-or-create cannot produce an alert there is
    no valid event to write, so the boundary fails rather than writing an orphan.
    """


class DetectionStateError(MonitoringError):
    """The agent reached a state its own documented rules declare impossible."""
