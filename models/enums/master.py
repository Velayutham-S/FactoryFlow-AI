"""The 30 master-group controlled vocabularies (§40.1).

Values are transcribed from FACTORY_SQLITE_DATABASE_SCHEMA.md §37.6 character
for character. Members are ordered as that catalogue lists them, not
alphabetically, so the two documents stay diffable by eye (§40.7). Classes are
ordered alphabetically within the module.

This package imports nothing but the standard library (§44.4).
"""

from enum import Enum


class AccessRestriction(str, Enum):
    """Vocabulary ``access_restriction``;
    bound by ck_plant_area_access_restriction_allowed."""

    GENERAL = "general"
    AUTHORIZED_ONLY = "authorized_only"
    RESTRICTED = "restricted"


class AreaType(str, Enum):
    """Vocabulary ``area_type``; bound by ck_plant_area_area_type_allowed."""

    PRODUCTION = "production"
    ASSEMBLY = "assembly"
    WAREHOUSE = "warehouse"
    SPARE_PARTS_STORE = "spare_parts_store"
    MAINTENANCE_WORKSHOP = "maintenance_workshop"
    QUALITY_LAB = "quality_lab"
    DISPATCH = "dispatch"
    UTILITY = "utility"


class BusinessRuleCategory(str, Enum):
    """Vocabulary ``business_rule_category``;
    bound by ck_business_rule_category_allowed."""

    ESCALATION = "escalation"
    PRIORITIZATION = "prioritization"
    COSTING = "costing"
    NOTIFICATION = "notification"
    MAINTENANCE_POLICY = "maintenance_policy"
    INVENTORY_POLICY = "inventory_policy"


class BusinessRuleValueType(str, Enum):
    """Vocabulary ``business_rule_value_type``;
    bound by ck_business_rule_value_type_allowed."""

    NUMERIC = "numeric"
    TEXT = "text"
    BOOLEAN = "boolean"


class CapabilityType(str, Enum):
    """Vocabulary ``capability_type``; bound by ck_plc_capability_type_allowed."""

    PRODUCTION_ROUTE = "production_route"
    FINISHING_STAGE = "finishing_stage"


class CriticalityLevel(str, Enum):
    """Vocabulary ``criticality_level``; bound on ProductionLine and Machine (§40.2)."""

    CRITICAL = "critical"
    HIGH = "high"
    STANDARD = "standard"
    LOW = "low"


class CustomerPriorityTier(str, Enum):
    """Vocabulary ``customer_priority_tier``;
    bound by ck_customer_priority_tier_allowed."""

    GOLD = "gold"
    SILVER = "silver"
    BRONZE = "bronze"


class DegradationDirection(str, Enum):
    """Vocabulary ``degradation_direction``;
    bound by ck_mtfm_degradation_direction_allowed."""

    INCREASING = "increasing"
    DECREASING = "decreasing"
    BIDIRECTIONAL = "bidirectional"


class DepartmentFunction(str, Enum):
    """Vocabulary ``department_function``; bound by ck_department_function_allowed."""

    PRODUCTION = "production"
    MAINTENANCE = "maintenance"
    QUALITY = "quality"
    WAREHOUSE = "warehouse"
    PLANNING = "planning"
    ENGINEERING = "engineering"


class DriftDirection(str, Enum):
    """Vocabulary ``drift_direction``; bound by ck_mtp_drift_direction_allowed."""

    INCREASING = "increasing"
    DECREASING = "decreasing"
    NONE = "none"


class EmploymentType(str, Enum):
    """Vocabulary ``employment_type``; bound by ck_worker_employment_type_allowed."""

    PERMANENT = "permanent"
    CONTRACT = "contract"
    APPRENTICE = "apprentice"


class EquipmentClass(str, Enum):
    """Vocabulary ``equipment_class``;
    bound by ck_machine_category_equipment_class_allowed."""

    ROTATING = "rotating"
    ROBOTIC = "robotic"
    CONVEYING = "conveying"
    STATIC = "static"
    METROLOGY = "metrology"


class FailureDomain(str, Enum):
    """Vocabulary ``failure_domain``; bound by ck_failure_category_domain_allowed."""

    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"
    THERMAL = "thermal"
    TOOLING = "tooling"
    HYDRAULIC = "hydraulic"
    PNEUMATIC = "pneumatic"
    INSTRUMENTATION = "instrumentation"
    AUTOMATION = "automation"
    PROCESS = "process"


class IntervalBasis(str, Enum):
    """Vocabulary ``interval_basis``; bound by ck_mms_interval_basis_allowed."""

    CALENDAR_DAYS = "calendar_days"
    OPERATING_HOURS = "operating_hours"
    CYCLE_COUNT = "cycle_count"


class InventoryItemType(str, Enum):
    """Vocabulary ``inventory_item_type``; bound by ck_inventory_item_type_allowed."""

    RAW_MATERIAL = "raw_material"
    COMPONENT = "component"
    CONSUMABLE = "consumable"
    SPARE_PART = "spare_part"
    TOOLING = "tooling"
    FINISHED_GOOD = "finished_good"


class InventoryLocationType(str, Enum):
    """Vocabulary ``inventory_location_type``;
    bound by ck_inventory_location_type_allowed."""

    RAW_MATERIAL_STORE = "raw_material_store"
    SPARE_PARTS_STORE = "spare_parts_store"
    TOOLING_CRIB = "tooling_crib"
    WIP_BUFFER = "wip_buffer"
    FINISHED_GOODS_STORE = "finished_goods_store"
    QUARANTINE = "quarantine"


class LineType(str, Enum):
    """Vocabulary ``line_type``; bound by ck_production_line_type_allowed."""

    MACHINING = "machining"
    ASSEMBLY = "assembly"
    PACKAGING = "packaging"
    FINISHING = "finishing"
    INSPECTION = "inspection"


class MachineLifecycleStatus(str, Enum):
    """Vocabulary ``machine_lifecycle_status``;
    bound by ck_machine_lifecycle_status_allowed.

    Machine carries this instead of ``is_active``, which is why it is the one
    master model that does not compose SoftDeleteMixin (§27, §34).
    """

    IN_SERVICE = "in_service"
    STANDBY = "standby"
    UNDER_OVERHAUL = "under_overhaul"
    DECOMMISSIONED = "decommissioned"


class MaintenanceSpecialization(str, Enum):
    """Vocabulary ``maintenance_specialization``; shared across 5 columns (§40.2).

    Matching a failed machine to a qualified team is a direct value comparison,
    which is why MachineCategory, MaintenanceTeam, MaintenanceEngineer and
    FailureCategory all bind this one class.
    """

    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"
    AUTOMATION = "automation"
    GENERAL = "general"


class MaintenanceType(str, Enum):
    """Vocabulary ``maintenance_type``; bound by ck_mms_maintenance_type_allowed."""

    PREVENTIVE = "preventive"
    PREDICTIVE = "predictive"
    CALIBRATION = "calibration"
    INSPECTION = "inspection"
    LUBRICATION = "lubrication"


class MeasurementDomain(str, Enum):
    """Vocabulary ``measurement_domain``;
    bound by ck_machine_parameter_domain_allowed."""

    THERMAL = "thermal"
    MECHANICAL = "mechanical"
    ELECTRICAL = "electrical"
    TOOLING = "tooling"
    PNEUMATIC = "pneumatic"
    HYDRAULIC = "hydraulic"
    POSITIONAL = "positional"


class ParameterDataType(str, Enum):
    """Vocabulary ``parameter_data_type``;
    bound by ck_machine_parameter_data_type_allowed."""

    NUMERIC_CONTINUOUS = "numeric_continuous"
    NUMERIC_INTEGER = "numeric_integer"
    BOOLEAN = "boolean"


class QualityCriticality(str, Enum):
    """Vocabulary ``quality_criticality``;
    bound by ck_product_quality_criticality_allowed."""

    SAFETY_CRITICAL = "safety_critical"
    HIGH = "high"
    STANDARD = "standard"


class RelativeFrequency(str, Enum):
    """Vocabulary ``relative_frequency``;
    bound by ck_mtfm_relative_frequency_allowed."""

    COMMON = "common"
    OCCASIONAL = "occasional"
    RARE = "rare"


class RoleCategory(str, Enum):
    """Vocabulary ``role_category``; bound by ck_worker_role_category_allowed."""

    OPERATOR = "operator"
    TECHNICIAN = "technician"
    ENGINEER = "engineer"
    SUPERVISOR = "supervisor"
    MANAGER = "manager"
    INSPECTOR = "inspector"
    PLANNER = "planner"
    STOREKEEPER = "storekeeper"


class ShiftType(str, Enum):
    """Vocabulary ``shift_type``; bound by ck_shift_type_allowed."""

    PRODUCTION = "production"
    GENERAL = "general"
    MAINTENANCE_ONLY = "maintenance_only"


class SkillLevel(str, Enum):
    """Vocabulary ``skill_level``; bound by ck_worker_skill_level_allowed."""

    TRAINEE = "trainee"
    JUNIOR = "junior"
    INTERMEDIATE = "intermediate"
    SENIOR = "senior"
    EXPERT = "expert"


class SupplierType(str, Enum):
    """Vocabulary ``supplier_type``; bound by ck_supplier_type_allowed."""

    RAW_MATERIAL = "raw_material"
    COMPONENT = "component"
    SPARE_PART = "spare_part"
    CONSUMABLE = "consumable"
    SERVICE = "service"


class ThresholdSensitivity(str, Enum):
    """Vocabulary ``threshold_sensitivity``; bound by ck_atp_sensitivity_allowed."""

    TIGHT = "tight"
    STANDARD = "standard"
    RELAXED = "relaxed"


class UnitOfMeasure(str, Enum):
    """Vocabulary ``unit_of_measure``; one class, two permitted sets (§40.3).

    ``inventory_item.unit_of_measure`` permits all six members.
    ``product.unit_of_measure`` permits five: ck_product_unit_of_measure_allowed
    excludes BOX. Assigning BOX to a Product is therefore caught by SQLite at
    flush rather than by the type checker, which §40.3 accepts deliberately.
    """

    EA = "EA"
    KG = "KG"
    L = "L"
    M = "M"
    SET = "SET"
    BOX = "BOX"
