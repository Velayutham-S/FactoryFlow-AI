"""Re-exports the 29 master model classes (§44.1).

No master model holds a foreign key into an operational or system table, which is
what makes the master group a self-contained layer of the dependency graph (§35).
"""

from models.master.commercial import Customer
from models.master.equipment import Machine, MachineCategory, MachineType
from models.master.failure import (
    FailureCategory,
    FailureSeverityLevel,
    MachineTypeFailureMode,
)
from models.master.inventory import (
    BillOfMaterials,
    InventoryItem,
    InventoryLocation,
    Supplier,
)
from models.master.maintenance import MachineMaintenanceSchedule
from models.master.parameters import MachineParameter, MachineTypeParameter
from models.master.people import (
    MaintenanceEngineer,
    MaintenanceTeam,
    NotificationRecipient,
    Worker,
    WorkerRole,
)
from models.master.plant import Department, Plant, PlantArea, Shift
from models.master.production import Product, ProductionLine, ProductLineCapability
from models.master.thresholds import (
    AlertThresholdProfile,
    AlertThresholdRule,
    BusinessRule,
)

__all__ = [
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
]
