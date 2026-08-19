"""Imports all 53 models so mappers configure and the MetaData is complete.

This is Alembic's target (§44.1, §44.5). It contains no logic: it imports and
re-exports, nothing else (§44.4).

Alembic points ``target_metadata`` at ``models.base.metadata`` and imports this
module. It then sees all 53 tables, all columns, all constraints and all indexes in
one namespace, with nothing to qualify and no per-group target to configure.

``render_as_batch=True`` is required in ``env.py`` and is not optional. SQLite's
ALTER TABLE can add a column, rename a column, rename a table and drop a column --
nothing else. Every other alteration is a create-copy-drop-rename rebuild, which
Alembic's batch mode performs; without ``render_as_batch`` autogenerate emits
``op.alter_column`` calls that SQLite rejects at run time rather than at generation
time (§44.5).

Importing this module is also what surfaces a relationship typo at startup rather
than at query time, because configuring the mappers resolves every string target
(§37.10).
"""

from models.base import Base, metadata
from models.master import (
    AlertThresholdProfile,
    AlertThresholdRule,
    BillOfMaterials,
    BusinessRule,
    Customer,
    Department,
    FailureCategory,
    FailureSeverityLevel,
    InventoryItem,
    InventoryLocation,
    Machine,
    MachineCategory,
    MachineMaintenanceSchedule,
    MachineParameter,
    MachineType,
    MachineTypeFailureMode,
    MachineTypeParameter,
    MaintenanceEngineer,
    MaintenanceTeam,
    NotificationRecipient,
    Plant,
    PlantArea,
    Product,
    ProductionLine,
    ProductLineCapability,
    Shift,
    Supplier,
    Worker,
    WorkerRole,
)
from models.operational import (
    AiRecommendation,
    CycleHistory,
    DashboardSnapshot,
    InventoryMovement,
    MachineMaintenanceActivity,
    MachineOperationalStatus,
    MachineSensorReading,
    MachineStateTransition,
    MaintenanceWorkRecord,
    Notification,
    NotificationDelivery,
    OperationalAlert,
    OperationalEvent,
    PredictionFeatureSnapshot,
    PredictionResult,
    ProductionCount,
    ProductionProgress,
    ProductionRun,
    QualityInspectionResult,
    RecommendationAction,
    ScrapRecord,
    SupervisorContext,
)
from models.system import AuditLog, SystemHealthStatus

__all__ = [
    "Base",
    "metadata",
    # master - 29
    "AlertThresholdProfile",
    "AlertThresholdRule",
    "BillOfMaterials",
    "BusinessRule",
    "Customer",
    "Department",
    "FailureCategory",
    "FailureSeverityLevel",
    "InventoryItem",
    "InventoryLocation",
    "Machine",
    "MachineCategory",
    "MachineMaintenanceSchedule",
    "MachineParameter",
    "MachineType",
    "MachineTypeFailureMode",
    "MachineTypeParameter",
    "MaintenanceEngineer",
    "MaintenanceTeam",
    "NotificationRecipient",
    "Plant",
    "PlantArea",
    "Product",
    "ProductLineCapability",
    "ProductionLine",
    "Shift",
    "Supplier",
    "Worker",
    "WorkerRole",
    # operational - 22
    "AiRecommendation",
    "CycleHistory",
    "DashboardSnapshot",
    "InventoryMovement",
    "MachineMaintenanceActivity",
    "MachineOperationalStatus",
    "MachineSensorReading",
    "MachineStateTransition",
    "MaintenanceWorkRecord",
    "Notification",
    "NotificationDelivery",
    "OperationalAlert",
    "OperationalEvent",
    "PredictionFeatureSnapshot",
    "PredictionResult",
    "ProductionCount",
    "ProductionProgress",
    "ProductionRun",
    "QualityInspectionResult",
    "RecommendationAction",
    "ScrapRecord",
    "SupervisorContext",
    # system - 2
    "AuditLog",
    "SystemHealthStatus",
]
