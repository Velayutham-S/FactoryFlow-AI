"""Monitoring Agent for FactoryFlow AI (Phase 4).

Detects conditions in the operational data the Factory Simulation Engine produced, and
records them as correlated cases. Four detectors, one alert layer, one orchestrator:

============================== =============================================================
:mod:`monitoring.agent`        the agent -- one cycle analyses current operational data
:mod:`monitoring.machine`      machine condition and telemetry data quality
:mod:`monitoring.production`   machine output
:mod:`monitoring.quality`      quality
:mod:`monitoring.inventory`    inventory
:mod:`monitoring.alerts`       correlation and the alert lifecycle
============================== =============================================================

:mod:`monitoring.context` holds the master snapshot, cursors and the suppression gate;
:mod:`monitoring.errors` holds the exceptions.

**Write scope.** The ownership model (FACTORY_OPERATIONAL_DATA_DESIGN.md §6.2, §6.3) gives
this component exactly two tables: ``operational_event``, insert only, and
``operational_alert``, insert and update. It additionally maintains
``machine_operational_status.open_alert_count``, which §46.2 assigns to its T-MON-1 and
T-MON-3 boundaries -- the count is a maintained total over the alert table, so this agent
is its only possible source, and §41.6 reconciles it against exactly that.

It writes nothing else. Predictions belong to the Prediction Agent, context to the
Supervisor Agent, recommendations to the Decision Agent, notifications to the Notification
Service, snapshots and human actions to the Dashboard, and ``system_health_status`` to the
Platform.

**Eleven of the twelve documented event types are implemented**, each against a limit that
master data declares:

========================== ================== ==================================================
``threshold_warning``      machine_condition  ``alert_threshold_rule`` warning limits
``threshold_critical``     machine_condition  ``alert_threshold_rule`` critical limits
``rate_of_change_exceeded`` machine_condition ``rate_of_change_limit_per_minute``
``sensor_out_of_range``    data_quality       ``machine_parameter`` physical range
``telemetry_stale``        data_quality       three sampling intervals, §E2 rule 9
``output_shortfall``       machine_output     ``max_hourly_output_units``
``cycle_deviation``        machine_output     ceiling implied by ``max_hourly_output_units``
``scrap_rate_exceeded``    quality            ``product.target_scrap_rate_pct``
``quality_failure_rate``   quality            ``product.target_scrap_rate_pct``, §E8 rule 9
``reorder_point_reached``  inventory          ``inventory_item.reorder_point``, §E10 rule 6
``safety_stock_breached``  inventory          ``inventory_item.safety_stock_qty``, §E10 rule 7
========================== ================== ==================================================

``sustained_deviation`` is **not** implemented: the vocabulary contains it but no document
states what triggers it, and inventing a trigger would be inventing a monitoring rule.

Typical use::

    from monitoring import monitor

    reports = monitor(r"C:\\factoryflow\\factoryflow.db", cycles=2)

or from the command line::

    python -m monitoring <database-path> [cycles]
"""

from monitoring.agent import CycleReport, MonitoringAgent, main, monitor
from monitoring.alerts import AlertOutcome, AlertWriter
from monitoring.context import Detection, MonitoringContext
from monitoring.errors import (
    CorrelationError,
    DetectionStateError,
    MasterDataUnavailableError,
    MonitoringError,
    OperationalDataUnavailableError,
)
from monitoring.inventory import InventoryMonitor
from monitoring.machine import MachineMonitor
from monitoring.production import ProductionMonitor
from monitoring.quality import QualityMonitor

__all__ = [
    "AlertOutcome",
    "AlertWriter",
    "CorrelationError",
    "CycleReport",
    "Detection",
    "DetectionStateError",
    "InventoryMonitor",
    "MachineMonitor",
    "MasterDataUnavailableError",
    "MonitoringAgent",
    "MonitoringContext",
    "MonitoringError",
    "OperationalDataUnavailableError",
    "ProductionMonitor",
    "QualityMonitor",
    "main",
    "monitor",
]
