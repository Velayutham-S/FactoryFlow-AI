"""Factory Simulation Engine for FactoryFlow AI (Phase 3).

Generates the factory. Six engines plus an orchestrator, one module each:

=============================== ==============================================
:mod:`factory_sim.simulator`    Factory Simulator — the orchestrator
:mod:`factory_sim.production`   Production Engine — runs, cycles, counts, progress
:mod:`factory_sim.machine_state` Machine State Engine — state, history, telemetry
:mod:`factory_sim.inventory`    Inventory Engine — the stock ledger
:mod:`factory_sim.failure`      Failure Engine — degradation and breakdown
:mod:`factory_sim.maintenance`  Maintenance Engine — work orders and timelines
:mod:`factory_sim.quality`      Quality Engine — inspections and scrap
=============================== ==============================================

:mod:`factory_sim.context` holds the shared clock, RNG and master data snapshot;
:mod:`factory_sim.errors` holds the exceptions.

**Write scope.** The simulator writes exactly the twelve tables the ownership model
assigns it (FACTORY_OPERATIONAL_DATA_DESIGN.md §6.2, §7.1): ``machine_sensor_reading``,
``machine_operational_status``, ``machine_state_transition``, ``production_run``,
``production_progress``, ``production_count``, ``cycle_history``,
``quality_inspection_result``, ``scrap_record``, ``inventory_movement``,
``maintenance_work_record`` and ``machine_maintenance_activity``.

It writes none of the other twelve operational tables, and §7.2 is explicit that it must
neither read nor write them. Events and alerts belong to the Monitoring Agent, feature
snapshots and predictions to the Prediction Agent, context to the Supervisor Agent,
recommendations to the Decision Agent, notifications to the Notification Service, and
snapshots and human actions to the Dashboard. §7.5 singles out the case most easily got
wrong: a failure must reach the database as drifting telemetry, lengthening cycles and
dimensional defects — **never as a pre-made event** — because otherwise the Monitoring
Agent's detection is never exercised by the data meant to test it.

**No free parameters.** Every characteristic generated here traces to a master data row:
sampling intervals, healthy bands and drift directions from ``machine_type_parameter``;
which failures are possible and how long they warn from ``machine_type_failure_mode``;
rates from ``product_line_capability``; material from ``bill_of_materials``; intervals from
``machine_maintenance_schedule``; response targets from ``maintenance_team``; retrieval
times from ``inventory_location``; lead times from ``supplier``; the working calendar from
``shift`` and ``plant``.

Typical use::

    from factory_sim import simulate

    report = simulate(r"C:\\factoryflow\\factoryflow.db", hours=8, seed=20260731)
    print(report.row_counts)

or from the command line::

    python -m factory_sim <database-path> [hours] [seed]
"""

from factory_sim.context import SimulationContext
from factory_sim.errors import (
    MasterDataIncompleteError,
    SimulationError,
    SimulationStateError,
)
from factory_sim.failure import FailureEngine
from factory_sim.inventory import InventoryEngine
from factory_sim.machine_state import MachineStateEngine
from factory_sim.maintenance import MaintenanceEngine
from factory_sim.production import ProductionEngine
from factory_sim.quality import QualityEngine
from factory_sim.simulator import (
    SIMULATOR_TABLES,
    FactorySimulator,
    SimulationReport,
    main,
    simulate,
)

__all__ = [
    "SIMULATOR_TABLES",
    "FactorySimulator",
    "FailureEngine",
    "InventoryEngine",
    "MachineStateEngine",
    "MaintenanceEngine",
    "MasterDataIncompleteError",
    "ProductionEngine",
    "QualityEngine",
    "SimulationContext",
    "SimulationError",
    "SimulationReport",
    "SimulationStateError",
    "main",
    "simulate",
]
