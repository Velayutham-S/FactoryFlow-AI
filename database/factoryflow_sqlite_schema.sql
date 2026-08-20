-- =====================================================================
-- FactoryFlow AI - SQLite 3 Physical Database Implementation
-- =====================================================================
--
-- Source of truth : FACTORY_SQLITE_DATABASE_SCHEMA.md (frozen)
-- Supporting      : PROJECT_OVERVIEW.md,
--                   FACTORY_MASTER_DATA_DESIGN.md,
--                   FACTORY_OPERATIONAL_DATA_DESIGN.md
--
-- Target engine   : SQLite 3 (accessed through Python sqlite3)
-- Object counts   : 53 tables, 53 primary keys (all AUTOINCREMENT),
--                   163 foreign keys (162 RESTRICT + 1 SET NULL),
--                   57 unique constraints, 8 unique indexes
--                   (7 partial + 1 expression-based),
--                   585 check constraints, 631 NOT NULL columns,
--                   65 controlled vocabularies over 101 columns,
--                   9 JSON columns, 0 triggers, 0 views
--                   (spec section 51.1)
--
-- ---------------------------------------------------------------------
-- IMPLEMENTATION NOTES (SQLite-specific, per the frozen specification)
-- ---------------------------------------------------------------------
--
-- 1. ONE DATABASE FILE. SQLite has no CREATE SCHEMA. The master,
--    operational, system, and analytics groups of the specification are
--    logical groupings only (spec section 8-11); every table below lives
--    in the single database file and no name is qualified.
--
-- 2. IDENTITY. Every table declares
--       <table>_id INTEGER PRIMARY KEY AUTOINCREMENT
--    written inline on the column, because AUTOINCREMENT is permitted
--    only on a column declared exactly INTEGER PRIMARY KEY and is
--    rejected on a table-level PRIMARY KEY clause (spec section 41.1).
--    AUTOINCREMENT is required, not optional: it prevents key reuse,
--    which audit_log.entity_id depends on (spec section 48.3).
--
-- 3. CONSTRAINTS ARE INLINE. SQLite's ALTER TABLE cannot ADD CONSTRAINT,
--    so every primary key, unique constraint, check constraint, and
--    foreign key is declared inside its CREATE TABLE statement. Table
--    creation therefore follows strict dependency order (spec sections
--    14 and 23) and no deferred constraint is required anywhere.
--
-- 4. NO ENUM TYPE. Each of the 65 controlled vocabularies (spec section
--    37.6) is a TEXT column with a named CHECK (column IN (...))
--    constraint, ck_<table>_<column>_allowed.
--
-- 5. BOOLEANS ARE INTEGER holding 0 or 1, each with a domain check
--    ck_<table>_<column>_bool (spec section 40.1).
--
-- 6. TIMESTAMPS ARE DATETIME holding UTC ISO-8601 text (spec 35.1).
--    Fixed-width format means text ordering is chronological ordering.
--
-- 7. DECLARED LENGTHS ARE NOT ENFORCED BY SQLITE, so every VARCHAR(n)
--    column carries ck_<table>_<column>_length CHECK(length(col) <= n)
--    and every CHAR(n) column CHECK(length(col) = n) (spec 12.3).
--
-- 8. FORMAT RULES USE GLOB, not REGEXP. REGEXP has no built-in
--    implementation in SQLite and would require the connecting
--    application to register a function, making the constraint depend on
--    the writer (spec section 38.3). Variable-length segments combine a
--    GLOB prefix test with a length() bound.
--
-- 9. NO COMMENT ON STATEMENTS. SQLite has no object-comment catalogue,
--    so the specification's documentation is carried as SQL comments.
--
-- 10. PRAGMA foreign_keys = ON is MANDATORY and per-connection. All 163
--     foreign keys below are declared correctly and enforce nothing
--     without it (spec section 31.1). It is set here for the creating
--     connection; every application connection must set it too.
--
-- =====================================================================

-- ---------------------------------------------------------------------
-- Connection and database configuration
-- Pragmas are issued outside the transaction: PRAGMA foreign_keys is a
-- no-op inside one, and journal_mode cannot be changed within one.
-- ---------------------------------------------------------------------

PRAGMA journal_mode = WAL;          -- durable; readers do not block the writer
PRAGMA foreign_keys = ON;           -- REQUIRED - see note 10
PRAGMA synchronous  = NORMAL;       -- durable across process crash under WAL

BEGIN TRANSACTION;

-- =====================================================================
-- MASTER GROUP - LAYER 0
-- Tables with no outbound foreign keys. Base of the dependency graph.
-- =====================================================================

-- ---------------------------------------------------------------------
-- M1. plant
-- The single manufacturing site. Root of the master hierarchy and anchor
-- for site-wide timezone and currency.
-- ---------------------------------------------------------------------
CREATE TABLE plant (
    plant_id                          INTEGER      NOT NULL
        CONSTRAINT pk_plant PRIMARY KEY AUTOINCREMENT,
    plant_code                        VARCHAR(10)  NOT NULL,
    plant_name                        VARCHAR(120) NOT NULL,
    address_line                      VARCHAR(200) NOT NULL,
    city                              VARCHAR(80)  NOT NULL,
    state_region                      VARCHAR(80)  NOT NULL,
    country_code                      CHAR(2)      NOT NULL,
    timezone                          VARCHAR(50)  NOT NULL,
    currency_code                     CHAR(3)      NOT NULL,
    operating_days_per_week           INTEGER      NOT NULL,
    shifts_per_day                    INTEGER      NOT NULL,
    commissioned_date                 DATE         NOT NULL,
    annual_production_capacity_units  INTEGER      NULL,
    is_active                         INTEGER      NOT NULL DEFAULT 1,
    created_at                        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_plant_code UNIQUE (plant_code),

    CONSTRAINT ck_plant_code_format
        CHECK (plant_code GLOB 'PLT-[0-9][0-9]'),
    CONSTRAINT ck_plant_country_code_format
        CHECK (country_code GLOB '[A-Z][A-Z]'),
    CONSTRAINT ck_plant_currency_code_format
        CHECK (currency_code GLOB '[A-Z][A-Z][A-Z]'),
    CONSTRAINT ck_plant_operating_days_range
        CHECK (operating_days_per_week BETWEEN 1 AND 7),
    CONSTRAINT ck_plant_shifts_per_day_range
        CHECK (shifts_per_day BETWEEN 1 AND 4),
    CONSTRAINT ck_plant_capacity_positive
        CHECK (annual_production_capacity_units IS NULL
               OR annual_production_capacity_units > 0),
    CONSTRAINT ck_plant_name_not_blank
        CHECK (length(trim(plant_name)) > 0),

    CONSTRAINT ck_plant_plant_code_length     CHECK (length(plant_code)    <= 10),
    CONSTRAINT ck_plant_plant_name_length     CHECK (length(plant_name)    <= 120),
    CONSTRAINT ck_plant_address_line_length   CHECK (length(address_line)  <= 200),
    CONSTRAINT ck_plant_city_length           CHECK (length(city)          <= 80),
    CONSTRAINT ck_plant_state_region_length   CHECK (length(state_region)  <= 80),
    CONSTRAINT ck_plant_country_code_length   CHECK (length(country_code)   = 2),
    CONSTRAINT ck_plant_timezone_length       CHECK (length(timezone)      <= 50),
    CONSTRAINT ck_plant_currency_code_length  CHECK (length(currency_code)  = 3),

    CONSTRAINT ck_plant_is_active_bool        CHECK (is_active IN (0, 1))
);
-- timezone is deliberately not check-constrained: SQLite carries no IANA
-- catalogue and a CHECK expression must be deterministic. Validation is a
-- mandatory application responsibility (spec M1, section 41.3).
-- commissioned_date is not constrained against the current date, because
-- CURRENT_DATE is non-deterministic (spec section 41.4).

-- ---------------------------------------------------------------------
-- M6. product
-- A finished good the plant manufactures. No outbound foreign keys.
-- ---------------------------------------------------------------------
CREATE TABLE product (
    product_id               INTEGER       NOT NULL
        CONSTRAINT pk_product PRIMARY KEY AUTOINCREMENT,
    product_code             VARCHAR(20)   NOT NULL,
    product_name             VARCHAR(150)  NOT NULL,
    product_family           VARCHAR(80)   NOT NULL,
    unit_of_measure          TEXT          NOT NULL,
    standard_selling_price   NUMERIC(12,2) NOT NULL,
    standard_material_cost   NUMERIC(12,2) NOT NULL,
    quality_criticality      TEXT          NOT NULL,
    target_scrap_rate_pct    NUMERIC(5,2)  NULL,
    shelf_life_days          INTEGER       NULL,
    drawing_revision         VARCHAR(12)   NULL,
    introduced_date          DATE          NOT NULL,
    is_active                INTEGER       NOT NULL DEFAULT 1,
    created_at               DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at               DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_product_code UNIQUE (product_code),

    -- PRD-XX-nnn : 2-4 upper-case letters then 3 digits
    CONSTRAINT ck_product_code_format
        CHECK (product_code GLOB 'PRD-[A-Z][A-Z]*-[0-9][0-9][0-9]'
               AND length(product_code) BETWEEN 10 AND 12),
    -- BOX is excluded here; inventory_item permits all six (spec 37.3)
    CONSTRAINT ck_product_unit_of_measure_allowed
        CHECK (unit_of_measure IN ('EA', 'KG', 'L', 'M', 'SET')),
    CONSTRAINT ck_product_selling_price_positive
        CHECK (standard_selling_price > 0),
    CONSTRAINT ck_product_material_cost_positive
        CHECK (standard_material_cost > 0),
    CONSTRAINT ck_product_margin_positive
        CHECK (standard_material_cost < standard_selling_price),
    CONSTRAINT ck_product_scrap_rate_range
        CHECK (target_scrap_rate_pct IS NULL
               OR target_scrap_rate_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_product_shelf_life_positive
        CHECK (shelf_life_days IS NULL OR shelf_life_days > 0),
    CONSTRAINT ck_product_name_not_blank
        CHECK (length(trim(product_name)) > 0),
    CONSTRAINT ck_product_quality_criticality_allowed
        CHECK (quality_criticality IN ('safety_critical', 'high', 'standard')),

    CONSTRAINT ck_product_product_code_length     CHECK (length(product_code)     <= 20),
    CONSTRAINT ck_product_product_name_length     CHECK (length(product_name)     <= 150),
    CONSTRAINT ck_product_product_family_length    CHECK (length(product_family)  <= 80),
    CONSTRAINT ck_product_drawing_revision_length  CHECK (drawing_revision IS NULL
                                                          OR length(drawing_revision) <= 12),

    CONSTRAINT ck_product_is_active_bool          CHECK (is_active IN (0, 1))
);

-- ---------------------------------------------------------------------
-- M8. machine_category
-- Broad equipment classes. Independent lookup, no outbound foreign keys.
-- ---------------------------------------------------------------------
CREATE TABLE machine_category (
    machine_category_id                 INTEGER     NOT NULL
        CONSTRAINT pk_machine_category PRIMARY KEY AUTOINCREMENT,
    machine_category_code               VARCHAR(12) NOT NULL,
    category_name                       VARCHAR(80) NOT NULL,
    description                         TEXT        NULL,
    equipment_class                     TEXT        NOT NULL,
    primary_maintenance_specialization  TEXT        NOT NULL,
    is_rotating_equipment               INTEGER     NOT NULL DEFAULT 0,
    requires_condition_monitoring       INTEGER     NOT NULL DEFAULT 1,
    typical_service_life_years          INTEGER     NULL,
    is_active                           INTEGER     NOT NULL DEFAULT 1,
    created_at                          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                          DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_machine_category_code UNIQUE (machine_category_code),

    CONSTRAINT ck_machine_category_code_format
        CHECK (machine_category_code GLOB 'MCAT-[A-Z][A-Z][A-Z]'),
    CONSTRAINT ck_machine_category_service_life_range
        CHECK (typical_service_life_years IS NULL
               OR typical_service_life_years BETWEEN 1 AND 50),
    CONSTRAINT ck_machine_category_name_not_blank
        CHECK (length(trim(category_name)) > 0),
    CONSTRAINT ck_machine_category_equipment_class_allowed
        CHECK (equipment_class IN ('rotating', 'robotic', 'conveying',
                                   'static', 'metrology')),
    CONSTRAINT ck_machine_category_primary_maintenance_specialization_allowed
        CHECK (primary_maintenance_specialization IN ('mechanical', 'electrical',
                                                      'automation', 'general')),

    CONSTRAINT ck_machine_category_machine_category_code_length
        CHECK (length(machine_category_code) <= 12),
    CONSTRAINT ck_machine_category_category_name_length
        CHECK (length(category_name) <= 80),

    CONSTRAINT ck_machine_category_is_rotating_equipment_bool
        CHECK (is_rotating_equipment IN (0, 1)),
    CONSTRAINT ck_machine_category_requires_condition_monitoring_bool
        CHECK (requires_condition_monitoring IN (0, 1)),
    CONSTRAINT ck_machine_category_is_active_bool
        CHECK (is_active IN (0, 1))
);

-- ---------------------------------------------------------------------
-- M11. machine_parameter
-- The catalogue of measurable machine parameters. Stores definitions,
-- never readings. Independent lookup.
-- ---------------------------------------------------------------------
CREATE TABLE machine_parameter (
    machine_parameter_id    INTEGER       NOT NULL
        CONSTRAINT pk_machine_parameter PRIMARY KEY AUTOINCREMENT,
    machine_parameter_code  VARCHAR(12)   NOT NULL,
    parameter_name          VARCHAR(80)   NOT NULL,
    unit_of_measure         VARCHAR(16)   NOT NULL,
    measurement_domain      TEXT          NOT NULL,
    data_type               TEXT          NOT NULL,
    physical_min            NUMERIC(12,4) NOT NULL,
    physical_max            NUMERIC(12,4) NOT NULL,
    degradation_direction   TEXT          NOT NULL,
    is_cumulative           INTEGER       NOT NULL DEFAULT 0,
    description             TEXT          NULL,
    is_active               INTEGER       NOT NULL DEFAULT 1,
    created_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_machine_parameter_code UNIQUE (machine_parameter_code),

    -- PRM-XXXX : 3-8 upper-case letters
    CONSTRAINT ck_machine_parameter_code_format
        CHECK (machine_parameter_code GLOB 'PRM-[A-Z][A-Z][A-Z]*'
               AND length(machine_parameter_code) BETWEEN 7 AND 12
               AND substr(machine_parameter_code, 5) NOT GLOB '*[^A-Z]*'),
    CONSTRAINT ck_machine_parameter_physical_range_ordered
        CHECK (physical_min < physical_max),
    CONSTRAINT ck_machine_parameter_cumulative_increasing
        CHECK (is_cumulative = 0 OR degradation_direction = 'increasing'),
    CONSTRAINT ck_machine_parameter_unit_not_blank
        CHECK (length(trim(unit_of_measure)) > 0),
    CONSTRAINT ck_machine_parameter_name_not_blank
        CHECK (length(trim(parameter_name)) > 0),
    CONSTRAINT ck_machine_parameter_measurement_domain_allowed
        CHECK (measurement_domain IN ('thermal', 'mechanical', 'electrical',
                                      'tooling', 'pneumatic', 'hydraulic',
                                      'positional')),
    CONSTRAINT ck_machine_parameter_data_type_allowed
        CHECK (data_type IN ('numeric_continuous', 'numeric_integer', 'boolean')),
    CONSTRAINT ck_machine_parameter_degradation_direction_allowed
        CHECK (degradation_direction IN ('increasing', 'decreasing', 'bidirectional')),

    CONSTRAINT ck_machine_parameter_machine_parameter_code_length
        CHECK (length(machine_parameter_code) <= 12),
    CONSTRAINT ck_machine_parameter_parameter_name_length
        CHECK (length(parameter_name) <= 80),
    CONSTRAINT ck_machine_parameter_unit_of_measure_length
        CHECK (length(unit_of_measure) <= 16),

    CONSTRAINT ck_machine_parameter_is_cumulative_bool CHECK (is_cumulative IN (0, 1)),
    CONSTRAINT ck_machine_parameter_is_active_bool     CHECK (is_active IN (0, 1))
);
-- unit_of_measure is VARCHAR(16) free text rather than a vocabulary:
-- units are an open set and the value is displayed, not compared
-- (spec sections 37.4, M11 notes).

-- ---------------------------------------------------------------------
-- M13. worker_role
-- Job roles and the authority each carries. Independent lookup.
-- ---------------------------------------------------------------------
CREATE TABLE worker_role (
    worker_role_id             INTEGER     NOT NULL
        CONSTRAINT pk_worker_role PRIMARY KEY AUTOINCREMENT,
    worker_role_code           VARCHAR(12) NOT NULL,
    role_name                  VARCHAR(80) NOT NULL,
    role_category              TEXT        NOT NULL,
    is_managerial              INTEGER     NOT NULL DEFAULT 0,
    seniority_rank             INTEGER     NOT NULL,
    can_authorize_line_stop    INTEGER     NOT NULL DEFAULT 0,
    can_authorize_maintenance  INTEGER     NOT NULL DEFAULT 0,
    requires_certification     INTEGER     NOT NULL DEFAULT 0,
    description                TEXT        NULL,
    is_active                  INTEGER     NOT NULL DEFAULT 1,
    created_at                 DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                 DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_worker_role_code UNIQUE (worker_role_code),

    -- ROL-XXX : 2-5 upper-case letters
    CONSTRAINT ck_worker_role_code_format
        CHECK (worker_role_code GLOB 'ROL-[A-Z][A-Z]*'
               AND length(worker_role_code) BETWEEN 6 AND 9
               AND substr(worker_role_code, 5) NOT GLOB '*[^A-Z]*'),
    CONSTRAINT ck_worker_role_seniority_range
        CHECK (seniority_rank BETWEEN 1 AND 10),
    CONSTRAINT ck_worker_role_managerial_can_stop_line
        CHECK (is_managerial = 0 OR can_authorize_line_stop = 1),
    CONSTRAINT ck_worker_role_name_not_blank
        CHECK (length(trim(role_name)) > 0),
    CONSTRAINT ck_worker_role_role_category_allowed
        CHECK (role_category IN ('operator', 'technician', 'engineer',
                                 'supervisor', 'manager', 'inspector',
                                 'planner', 'storekeeper')),

    CONSTRAINT ck_worker_role_worker_role_code_length CHECK (length(worker_role_code) <= 12),
    CONSTRAINT ck_worker_role_role_name_length        CHECK (length(role_name)        <= 80),

    CONSTRAINT ck_worker_role_is_managerial_bool
        CHECK (is_managerial IN (0, 1)),
    CONSTRAINT ck_worker_role_can_authorize_line_stop_bool
        CHECK (can_authorize_line_stop IN (0, 1)),
    CONSTRAINT ck_worker_role_can_authorize_maintenance_bool
        CHECK (can_authorize_maintenance IN (0, 1)),
    CONSTRAINT ck_worker_role_requires_certification_bool
        CHECK (requires_certification IN (0, 1)),
    CONSTRAINT ck_worker_role_is_active_bool
        CHECK (is_active IN (0, 1))
);

-- ---------------------------------------------------------------------
-- M21. supplier
-- External source of materials, components, or spare parts.
-- Independent reference table.
-- ---------------------------------------------------------------------
CREATE TABLE supplier (
    supplier_id               INTEGER       NOT NULL
        CONSTRAINT pk_supplier PRIMARY KEY AUTOINCREMENT,
    supplier_code             VARCHAR(10)   NOT NULL,
    supplier_name             VARCHAR(150)  NOT NULL,
    supplier_type             TEXT          NOT NULL,
    contact_person            VARCHAR(100)  NULL,
    contact_email             VARCHAR(150)  NULL,
    contact_phone             VARCHAR(20)   NULL,
    city                      VARCHAR(80)   NOT NULL,
    country_code              CHAR(2)       NOT NULL,
    standard_lead_time_days   INTEGER       NOT NULL,
    expedited_lead_time_days  INTEGER       NULL,
    reliability_rating        NUMERIC(2,1)  NOT NULL,
    on_time_delivery_pct      NUMERIC(5,2)  NULL,
    is_approved_vendor        INTEGER       NOT NULL DEFAULT 1,
    contract_expiry_date      DATE          NULL,
    is_active                 INTEGER       NOT NULL DEFAULT 1,
    created_at                DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_supplier_code UNIQUE (supplier_code),

    CONSTRAINT ck_supplier_code_format
        CHECK (supplier_code GLOB 'SUP-[0-9][0-9][0-9]'),
    CONSTRAINT ck_supplier_country_code_format
        CHECK (country_code GLOB '[A-Z][A-Z]'),
    CONSTRAINT ck_supplier_lead_time_non_negative
        CHECK (standard_lead_time_days >= 0),
    CONSTRAINT ck_supplier_expedited_faster
        CHECK (expedited_lead_time_days IS NULL
               OR expedited_lead_time_days < standard_lead_time_days),
    CONSTRAINT ck_supplier_reliability_range
        CHECK (reliability_rating BETWEEN 0.0 AND 5.0),
    CONSTRAINT ck_supplier_otd_range
        CHECK (on_time_delivery_pct IS NULL
               OR on_time_delivery_pct BETWEEN 0 AND 100),
    -- Deliberately basic address check: an @ with text either side and a
    -- dot in the domain (spec sections M3 notes, 38.3)
    CONSTRAINT ck_supplier_email_format
        CHECK (contact_email IS NULL
               OR (contact_email GLOB '?*@?*.?*'
                   AND contact_email NOT GLOB '*@*@*')),
    CONSTRAINT ck_supplier_name_not_blank
        CHECK (length(trim(supplier_name)) > 0),
    CONSTRAINT ck_supplier_supplier_type_allowed
        CHECK (supplier_type IN ('raw_material', 'component', 'spare_part',
                                 'consumable', 'service')),

    CONSTRAINT ck_supplier_supplier_code_length   CHECK (length(supplier_code) <= 10),
    CONSTRAINT ck_supplier_supplier_name_length   CHECK (length(supplier_name) <= 150),
    CONSTRAINT ck_supplier_contact_person_length  CHECK (contact_person IS NULL
                                                         OR length(contact_person) <= 100),
    CONSTRAINT ck_supplier_contact_email_length   CHECK (contact_email IS NULL
                                                         OR length(contact_email) <= 150),
    CONSTRAINT ck_supplier_contact_phone_length   CHECK (contact_phone IS NULL
                                                         OR length(contact_phone) <= 20),
    CONSTRAINT ck_supplier_city_length            CHECK (length(city) <= 80),
    CONSTRAINT ck_supplier_country_code_length    CHECK (length(country_code) = 2),

    CONSTRAINT ck_supplier_is_approved_vendor_bool CHECK (is_approved_vendor IN (0, 1)),
    CONSTRAINT ck_supplier_is_active_bool          CHECK (is_active IN (0, 1))
);

-- ---------------------------------------------------------------------
-- M22. customer
-- External buyer of finished goods. No outbound foreign keys and no
-- master children.
-- ---------------------------------------------------------------------
CREATE TABLE customer (
    customer_id                    INTEGER       NOT NULL
        CONSTRAINT pk_customer PRIMARY KEY AUTOINCREMENT,
    customer_code                  VARCHAR(10)   NOT NULL,
    customer_name                  VARCHAR(150)  NOT NULL,
    priority_tier                  TEXT          NOT NULL,
    industry_sector                VARCHAR(80)   NULL,
    city                           VARCHAR(80)   NOT NULL,
    country_code                   CHAR(2)       NOT NULL,
    contact_person                 VARCHAR(100)  NULL,
    contact_email                  VARCHAR(150)  NULL,
    late_delivery_penalty_per_day  NUMERIC(12,2) NULL,
    contractual_otd_target_pct     NUMERIC(5,2)  NULL,
    annual_order_value             NUMERIC(14,2) NULL,
    is_active                      INTEGER       NOT NULL DEFAULT 1,
    created_at                     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_customer_code UNIQUE (customer_code),

    CONSTRAINT ck_customer_code_format
        CHECK (customer_code GLOB 'CUS-[0-9][0-9][0-9]'),
    CONSTRAINT ck_customer_country_code_format
        CHECK (country_code GLOB '[A-Z][A-Z]'),
    CONSTRAINT ck_customer_penalty_non_negative
        CHECK (late_delivery_penalty_per_day IS NULL
               OR late_delivery_penalty_per_day >= 0),
    CONSTRAINT ck_customer_otd_target_range
        CHECK (contractual_otd_target_pct IS NULL
               OR contractual_otd_target_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_customer_annual_value_positive
        CHECK (annual_order_value IS NULL OR annual_order_value > 0),
    CONSTRAINT ck_customer_email_format
        CHECK (contact_email IS NULL
               OR (contact_email GLOB '?*@?*.?*'
                   AND contact_email NOT GLOB '*@*@*')),
    CONSTRAINT ck_customer_name_not_blank
        CHECK (length(trim(customer_name)) > 0),
    CONSTRAINT ck_customer_priority_tier_allowed
        CHECK (priority_tier IN ('gold', 'silver', 'bronze')),

    CONSTRAINT ck_customer_customer_code_length    CHECK (length(customer_code) <= 10),
    CONSTRAINT ck_customer_customer_name_length    CHECK (length(customer_name) <= 150),
    CONSTRAINT ck_customer_industry_sector_length  CHECK (industry_sector IS NULL
                                                          OR length(industry_sector) <= 80),
    CONSTRAINT ck_customer_city_length             CHECK (length(city) <= 80),
    CONSTRAINT ck_customer_country_code_length     CHECK (length(country_code) = 2),
    CONSTRAINT ck_customer_contact_person_length   CHECK (contact_person IS NULL
                                                          OR length(contact_person) <= 100),
    CONSTRAINT ck_customer_contact_email_length    CHECK (contact_email IS NULL
                                                          OR length(contact_email) <= 150),

    CONSTRAINT ck_customer_is_active_bool          CHECK (is_active IN (0, 1))
);

-- ---------------------------------------------------------------------
-- M23. failure_severity_level
-- The severity scale, with response commitment and escalation policy.
-- severity_rank = 1 is the MOST severe. Independent lookup.
-- ---------------------------------------------------------------------
CREATE TABLE failure_severity_level (
    failure_severity_level_id         INTEGER     NOT NULL
        CONSTRAINT pk_failure_severity_level PRIMARY KEY AUTOINCREMENT,
    failure_severity_level_code       VARCHAR(8)  NOT NULL,
    severity_name                     VARCHAR(40) NOT NULL,
    severity_rank                     INTEGER     NOT NULL,
    description                       TEXT        NOT NULL,
    target_response_time_minutes      INTEGER     NULL,
    requires_line_stop                INTEGER     NOT NULL DEFAULT 0,
    requires_immediate_escalation     INTEGER     NOT NULL DEFAULT 0,
    requires_manager_acknowledgement  INTEGER     NOT NULL DEFAULT 0,
    max_acknowledgement_minutes       INTEGER     NULL,
    display_color_hex                 CHAR(7)     NOT NULL,
    is_active                         INTEGER     NOT NULL DEFAULT 1,
    created_at                        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_failure_severity_level_code UNIQUE (failure_severity_level_code),
    CONSTRAINT uq_failure_severity_level_rank UNIQUE (severity_rank),

    CONSTRAINT ck_severity_code_format
        CHECK (failure_severity_level_code GLOB 'SEV-[0-9]'),
    CONSTRAINT ck_severity_rank_range
        CHECK (severity_rank BETWEEN 1 AND 9),
    CONSTRAINT ck_severity_response_time_positive
        CHECK (target_response_time_minutes IS NULL
               OR target_response_time_minutes > 0),
    CONSTRAINT ck_severity_ack_minutes_required
        CHECK (requires_manager_acknowledgement = 0
               OR max_acknowledgement_minutes IS NOT NULL),
    CONSTRAINT ck_severity_ack_minutes_positive
        CHECK (max_acknowledgement_minutes IS NULL
               OR max_acknowledgement_minutes > 0),
    CONSTRAINT ck_severity_color_hex_format
        CHECK (display_color_hex GLOB '#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'),
    CONSTRAINT ck_severity_name_not_blank
        CHECK (length(trim(severity_name)) > 0),
    CONSTRAINT ck_severity_description_not_blank
        CHECK (length(trim(description)) > 0),

    CONSTRAINT ck_failure_severity_level_code_length
        CHECK (length(failure_severity_level_code) <= 8),
    CONSTRAINT ck_failure_severity_level_severity_name_length
        CHECK (length(severity_name) <= 40),
    CONSTRAINT ck_failure_severity_level_display_color_hex_length
        CHECK (length(display_color_hex) = 7),

    CONSTRAINT ck_severity_requires_line_stop_bool
        CHECK (requires_line_stop IN (0, 1)),
    CONSTRAINT ck_severity_requires_immediate_escalation_bool
        CHECK (requires_immediate_escalation IN (0, 1)),
    CONSTRAINT ck_severity_requires_manager_acknowledgement_bool
        CHECK (requires_manager_acknowledgement IN (0, 1)),
    CONSTRAINT ck_failure_severity_level_is_active_bool
        CHECK (is_active IN (0, 1))
);
-- "target_response_time_minutes must increase as severity decreases" is a
-- cross-row rule no CHECK can express; it is a seed validation check
-- (spec section 41.3).

-- =====================================================================
-- MASTER GROUP - LAYER 1
-- =====================================================================

-- ---------------------------------------------------------------------
-- M2. plant_area
-- A distinct physical zone within the plant.
-- ---------------------------------------------------------------------
CREATE TABLE plant_area (
    plant_area_id           INTEGER       NOT NULL
        CONSTRAINT pk_plant_area PRIMARY KEY AUTOINCREMENT,
    plant_area_code         VARCHAR(12)   NOT NULL,
    plant_id                INTEGER       NOT NULL,
    area_name               VARCHAR(100)  NOT NULL,
    area_type               TEXT          NOT NULL,
    floor_level             INTEGER       NULL,
    floor_space_sqm         NUMERIC(10,2) NULL,
    nominal_ambient_temp_c  NUMERIC(5,2)  NULL,
    is_climate_controlled   INTEGER       NOT NULL DEFAULT 0,
    access_restriction      TEXT          NOT NULL DEFAULT 'general',
    is_active               INTEGER       NOT NULL DEFAULT 1,
    created_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_plant_area_code UNIQUE (plant_area_code),

    CONSTRAINT ck_plant_area_code_format
        CHECK (plant_area_code GLOB 'AREA-[A-Z][A-Z][A-Z]'),
    CONSTRAINT ck_plant_area_floor_level_range
        CHECK (floor_level IS NULL OR floor_level BETWEEN -2 AND 10),
    CONSTRAINT ck_plant_area_floor_space_positive
        CHECK (floor_space_sqm IS NULL OR floor_space_sqm > 0),
    CONSTRAINT ck_plant_area_ambient_range
        CHECK (nominal_ambient_temp_c IS NULL
               OR nominal_ambient_temp_c BETWEEN -20 AND 60),
    CONSTRAINT ck_plant_area_name_not_blank
        CHECK (length(trim(area_name)) > 0),
    CONSTRAINT ck_plant_area_area_type_allowed
        CHECK (area_type IN ('production', 'assembly', 'warehouse',
                             'spare_parts_store', 'maintenance_workshop',
                             'quality_lab', 'dispatch', 'utility')),
    CONSTRAINT ck_plant_area_access_restriction_allowed
        CHECK (access_restriction IN ('general', 'authorized_only', 'restricted')),

    CONSTRAINT ck_plant_area_plant_area_code_length CHECK (length(plant_area_code) <= 12),
    CONSTRAINT ck_plant_area_area_name_length       CHECK (length(area_name)       <= 100),

    CONSTRAINT ck_plant_area_is_climate_controlled_bool
        CHECK (is_climate_controlled IN (0, 1)),
    CONSTRAINT ck_plant_area_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_plant_area_plant FOREIGN KEY (plant_id)
        REFERENCES plant (plant_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Line-to-area-type and location-to-area-type alignment span tables and
-- are application-validated (spec section 41.3).

-- ---------------------------------------------------------------------
-- M3. department
-- An organisational unit that owns work and people.
-- No manager_worker_id column: the department/worker cycle is designed
-- out and leadership is resolved by worker_role.is_managerial.
-- ---------------------------------------------------------------------
CREATE TABLE department (
    department_id        INTEGER      NOT NULL
        CONSTRAINT pk_department PRIMARY KEY AUTOINCREMENT,
    department_code      VARCHAR(12)  NOT NULL,
    plant_id             INTEGER      NOT NULL,
    department_name      VARCHAR(100) NOT NULL,
    department_function  TEXT         NOT NULL,
    cost_center_code     VARCHAR(20)  NOT NULL,
    escalation_email     VARCHAR(150) NULL,
    headcount_budget     INTEGER      NULL,
    is_active            INTEGER      NOT NULL DEFAULT 1,
    created_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_department_code             UNIQUE (department_code),
    CONSTRAINT uq_department_cost_center_code UNIQUE (cost_center_code),

    CONSTRAINT ck_department_code_format
        CHECK (department_code GLOB 'DEP-[A-Z][A-Z][A-Z]'),
    CONSTRAINT ck_department_headcount_non_negative
        CHECK (headcount_budget IS NULL OR headcount_budget >= 0),
    CONSTRAINT ck_department_escalation_email_format
        CHECK (escalation_email IS NULL
               OR (escalation_email GLOB '?*@?*.?*'
                   AND escalation_email NOT GLOB '*@*@*')),
    CONSTRAINT ck_department_name_not_blank
        CHECK (length(trim(department_name)) > 0),
    CONSTRAINT ck_department_department_function_allowed
        CHECK (department_function IN ('production', 'maintenance', 'quality',
                                      'warehouse', 'planning', 'engineering')),

    CONSTRAINT ck_department_department_code_length  CHECK (length(department_code)  <= 12),
    CONSTRAINT ck_department_department_name_length  CHECK (length(department_name)  <= 100),
    CONSTRAINT ck_department_cost_center_code_length CHECK (length(cost_center_code) <= 20),
    CONSTRAINT ck_department_escalation_email_length CHECK (escalation_email IS NULL
                                                           OR length(escalation_email) <= 150),

    CONSTRAINT ck_department_is_active_bool CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_department_plant FOREIGN KEY (plant_id)
        REFERENCES plant (plant_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M4. shift
-- Working time patterns. start_time and end_time are local wall-clock
-- values interpreted against plant.timezone - the one place in this
-- schema where a stored value is deliberately not UTC (spec M4 notes).
-- ---------------------------------------------------------------------
CREATE TABLE shift (
    shift_id                INTEGER     NOT NULL
        CONSTRAINT pk_shift PRIMARY KEY AUTOINCREMENT,
    shift_code              VARCHAR(8)  NOT NULL,
    plant_id                INTEGER     NOT NULL,
    shift_name              VARCHAR(60) NOT NULL,
    start_time              TIME        NOT NULL,
    end_time                TIME        NOT NULL,
    crosses_midnight        INTEGER     NOT NULL DEFAULT 0,
    shift_type              TEXT        NOT NULL,
    sequence_order          INTEGER     NOT NULL,
    is_production_shift     INTEGER     NOT NULL DEFAULT 1,
    break_duration_minutes  INTEGER     NULL,
    is_active               INTEGER     NOT NULL DEFAULT 1,
    created_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at              DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_shift_code UNIQUE (shift_code),
    -- uq_shift_sequence_order_production is a PARTIAL unique index and is
    -- created in the index section below.

    -- SH-X : 1-4 upper-case letters
    CONSTRAINT ck_shift_code_format
        CHECK (shift_code GLOB 'SH-[A-Z]*'
               AND length(shift_code) BETWEEN 4 AND 7
               AND substr(shift_code, 4) NOT GLOB '*[^A-Z]*'),
    CONSTRAINT ck_shift_times_differ
        CHECK (start_time <> end_time),
    -- crosses_midnight is TRUE if and only if end_time <= start_time
    CONSTRAINT ck_shift_crosses_midnight_consistent
        CHECK (crosses_midnight = (CASE WHEN end_time <= start_time THEN 1 ELSE 0 END)),
    CONSTRAINT ck_shift_sequence_order_positive
        CHECK (sequence_order > 0),
    CONSTRAINT ck_shift_break_duration_range
        CHECK (break_duration_minutes IS NULL
               OR break_duration_minutes BETWEEN 0 AND 120),
    CONSTRAINT ck_shift_name_not_blank
        CHECK (length(trim(shift_name)) > 0),
    CONSTRAINT ck_shift_shift_type_allowed
        CHECK (shift_type IN ('production', 'general', 'maintenance_only')),

    CONSTRAINT ck_shift_shift_code_length CHECK (length(shift_code) <= 8),
    CONSTRAINT ck_shift_shift_name_length CHECK (length(shift_name) <= 60),

    CONSTRAINT ck_shift_crosses_midnight_bool    CHECK (crosses_midnight IN (0, 1)),
    CONSTRAINT ck_shift_is_production_shift_bool CHECK (is_production_shift IN (0, 1)),
    CONSTRAINT ck_shift_is_active_bool           CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_shift_plant FOREIGN KEY (plant_id)
        REFERENCES plant (plant_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M9. machine_type
-- A machine model and its engineering specifications. mttr_minutes is
-- the entry point to the business impact chain.
-- ---------------------------------------------------------------------
CREATE TABLE machine_type (
    machine_type_id        INTEGER      NOT NULL
        CONSTRAINT pk_machine_type PRIMARY KEY AUTOINCREMENT,
    machine_type_code      VARCHAR(24)  NOT NULL,
    machine_category_id    INTEGER      NOT NULL,
    type_name              VARCHAR(120) NOT NULL,
    manufacturer           VARCHAR(100) NOT NULL,
    model_number           VARCHAR(60)  NOT NULL,
    rated_power_kw         NUMERIC(8,2) NOT NULL,
    design_life_hours      INTEGER      NOT NULL,
    mtbf_hours             INTEGER      NOT NULL,
    mttr_minutes           INTEGER      NOT NULL,
    requires_tooling       INTEGER      NOT NULL DEFAULT 0,
    control_system         VARCHAR(80)  NULL,
    min_operators_required INTEGER      NOT NULL,
    is_active              INTEGER      NOT NULL DEFAULT 1,
    created_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at             DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_machine_type_code UNIQUE (machine_type_code),

    -- MTY-<model> : 3-20 characters from A-Z, 0-9, hyphen
    CONSTRAINT ck_machine_type_code_format
        CHECK (machine_type_code GLOB 'MTY-[A-Z0-9-][A-Z0-9-][A-Z0-9-]*'
               AND length(machine_type_code) BETWEEN 7 AND 24
               AND substr(machine_type_code, 5) NOT GLOB '*[^A-Z0-9-]*'),
    CONSTRAINT ck_machine_type_rated_power_positive
        CHECK (rated_power_kw > 0),
    CONSTRAINT ck_machine_type_design_life_positive
        CHECK (design_life_hours > 0),
    CONSTRAINT ck_machine_type_mtbf_positive
        CHECK (mtbf_hours > 0),
    CONSTRAINT ck_machine_type_mtbf_below_design_life
        CHECK (mtbf_hours < design_life_hours),
    CONSTRAINT ck_machine_type_mttr_positive
        CHECK (mttr_minutes > 0),
    CONSTRAINT ck_machine_type_operators_range
        CHECK (min_operators_required BETWEEN 0 AND 5),
    CONSTRAINT ck_machine_type_name_not_blank
        CHECK (length(trim(type_name)) > 0),

    CONSTRAINT ck_machine_type_machine_type_code_length CHECK (length(machine_type_code) <= 24),
    CONSTRAINT ck_machine_type_type_name_length         CHECK (length(type_name)         <= 120),
    CONSTRAINT ck_machine_type_manufacturer_length      CHECK (length(manufacturer)      <= 100),
    CONSTRAINT ck_machine_type_model_number_length      CHECK (length(model_number)      <= 60),
    CONSTRAINT ck_machine_type_control_system_length    CHECK (control_system IS NULL
                                                               OR length(control_system) <= 80),

    CONSTRAINT ck_machine_type_requires_tooling_bool CHECK (requires_tooling IN (0, 1)),
    CONSTRAINT ck_machine_type_is_active_bool        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_machine_type_category FOREIGN KEY (machine_category_id)
        REFERENCES machine_category (machine_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M24. failure_category
-- The controlled vocabulary of failure modes the platform reasons about.
-- description is a functional LLM input, not commentary - hence the
-- mandatory not-blank check.
-- ---------------------------------------------------------------------
CREATE TABLE failure_category (
    failure_category_id        INTEGER      NOT NULL
        CONSTRAINT pk_failure_category PRIMARY KEY AUTOINCREMENT,
    failure_category_code      VARCHAR(10)  NOT NULL,
    category_name              VARCHAR(100) NOT NULL,
    failure_domain             TEXT         NOT NULL,
    default_severity_level_id  INTEGER      NOT NULL,
    required_specialization    TEXT         NOT NULL,
    requires_spare_part        INTEGER      NOT NULL DEFAULT 0,
    has_safety_implication     INTEGER      NOT NULL DEFAULT 0,
    description                TEXT         NOT NULL,
    is_active                  INTEGER      NOT NULL DEFAULT 1,
    created_at                 DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                 DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_failure_category_code UNIQUE (failure_category_code),

    -- FC-XXXX : 3-6 upper-case letters
    CONSTRAINT ck_failure_category_code_format
        CHECK (failure_category_code GLOB 'FC-[A-Z][A-Z][A-Z]*'
               AND length(failure_category_code) BETWEEN 6 AND 9
               AND substr(failure_category_code, 4) NOT GLOB '*[^A-Z]*'),
    CONSTRAINT ck_failure_category_name_not_blank
        CHECK (length(trim(category_name)) > 0),
    CONSTRAINT ck_failure_category_description_not_blank
        CHECK (length(trim(description)) > 0),
    CONSTRAINT ck_failure_category_failure_domain_allowed
        CHECK (failure_domain IN ('mechanical', 'electrical', 'thermal',
                                  'tooling', 'hydraulic', 'pneumatic',
                                  'instrumentation', 'automation', 'process')),
    CONSTRAINT ck_failure_category_required_specialization_allowed
        CHECK (required_specialization IN ('mechanical', 'electrical',
                                           'automation', 'general')),

    CONSTRAINT ck_failure_category_failure_category_code_length
        CHECK (length(failure_category_code) <= 10),
    CONSTRAINT ck_failure_category_category_name_length
        CHECK (length(category_name) <= 100),

    CONSTRAINT ck_failure_category_requires_spare_part_bool
        CHECK (requires_spare_part IN (0, 1)),
    CONSTRAINT ck_failure_category_has_safety_implication_bool
        CHECK (has_safety_implication IN (0, 1)),
    CONSTRAINT ck_failure_category_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_failure_category_default_severity FOREIGN KEY (default_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- =====================================================================
-- MASTER GROUP - LAYER 2
-- =====================================================================

-- ---------------------------------------------------------------------
-- M5. production_line
-- A sequenced group of machines producing finished output. Two parents:
-- plant_area answers WHERE, department answers WHO OWNS IT.
-- ---------------------------------------------------------------------
CREATE TABLE production_line (
    production_line_id              INTEGER       NOT NULL
        CONSTRAINT pk_production_line PRIMARY KEY AUTOINCREMENT,
    production_line_code            VARCHAR(10)   NOT NULL,
    plant_area_id                   INTEGER       NOT NULL,
    department_id                   INTEGER       NOT NULL,
    line_name                       VARCHAR(120)  NOT NULL,
    line_type                       TEXT          NOT NULL,
    criticality                     TEXT          NOT NULL,
    design_capacity_units_per_hour  NUMERIC(10,2) NOT NULL,
    station_count                   INTEGER       NOT NULL,
    target_oee_percent              NUMERIC(5,2)  NULL,
    changeover_time_minutes         INTEGER       NULL,
    commissioned_date               DATE          NOT NULL,
    is_active                       INTEGER       NOT NULL DEFAULT 1,
    created_at                      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_production_line_code UNIQUE (production_line_code),

    CONSTRAINT ck_production_line_code_format
        CHECK (production_line_code GLOB 'LN-[0-9][0-9]'),
    CONSTRAINT ck_production_line_capacity_positive
        CHECK (design_capacity_units_per_hour > 0),
    CONSTRAINT ck_production_line_station_count_positive
        CHECK (station_count > 0),
    CONSTRAINT ck_production_line_target_oee_range
        CHECK (target_oee_percent IS NULL
               OR target_oee_percent BETWEEN 0 AND 100),
    CONSTRAINT ck_production_line_changeover_non_negative
        CHECK (changeover_time_minutes IS NULL OR changeover_time_minutes >= 0),
    CONSTRAINT ck_production_line_name_not_blank
        CHECK (length(trim(line_name)) > 0),
    CONSTRAINT ck_production_line_line_type_allowed
        CHECK (line_type IN ('machining', 'assembly', 'packaging',
                             'finishing', 'inspection')),
    CONSTRAINT ck_production_line_criticality_allowed
        CHECK (criticality IN ('critical', 'high', 'standard', 'low')),

    CONSTRAINT ck_production_line_production_line_code_length
        CHECK (length(production_line_code) <= 10),
    CONSTRAINT ck_production_line_line_name_length
        CHECK (length(line_name) <= 120),

    CONSTRAINT ck_production_line_is_active_bool CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_production_line_plant_area FOREIGN KEY (plant_area_id)
        REFERENCES plant_area (plant_area_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_production_line_department FOREIGN KEY (department_id)
        REFERENCES department (department_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M18. inventory_location
-- A physical storage location. average_retrieval_time_minutes is part of
-- repair time, so an honest downtime estimate is retrieval plus repair.
-- ---------------------------------------------------------------------
CREATE TABLE inventory_location (
    inventory_location_id           INTEGER      NOT NULL
        CONSTRAINT pk_inventory_location PRIMARY KEY AUTOINCREMENT,
    inventory_location_code         VARCHAR(16)  NOT NULL,
    location_name                   VARCHAR(100) NOT NULL,
    plant_area_id                   INTEGER      NOT NULL,
    location_type                   TEXT         NOT NULL,
    capacity_slots                  INTEGER      NULL,
    is_temperature_controlled       INTEGER      NOT NULL DEFAULT 0,
    average_retrieval_time_minutes  INTEGER      NOT NULL,
    stock_count_frequency_days      INTEGER      NULL,
    is_active                       INTEGER      NOT NULL DEFAULT 1,
    created_at                      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_inventory_location_code UNIQUE (inventory_location_code),

    -- LOC-XX-XXXX : 2 upper-case letters then 1-4 of A-Z or 0-9
    CONSTRAINT ck_inventory_location_code_format
        CHECK (inventory_location_code GLOB 'LOC-[A-Z][A-Z]-[A-Z0-9]*'
               AND length(inventory_location_code) BETWEEN 8 AND 11
               AND substr(inventory_location_code, 8) NOT GLOB '*[^A-Z0-9]*'),
    CONSTRAINT ck_inventory_location_capacity_positive
        CHECK (capacity_slots IS NULL OR capacity_slots > 0),
    CONSTRAINT ck_inventory_location_retrieval_range
        CHECK (average_retrieval_time_minutes BETWEEN 1 AND 240),
    CONSTRAINT ck_inventory_location_count_frequency_range
        CHECK (stock_count_frequency_days IS NULL
               OR stock_count_frequency_days BETWEEN 1 AND 365),
    CONSTRAINT ck_inventory_location_name_not_blank
        CHECK (length(trim(location_name)) > 0),
    CONSTRAINT ck_inventory_location_location_type_allowed
        CHECK (location_type IN ('raw_material_store', 'spare_parts_store',
                                 'tooling_crib', 'wip_buffer',
                                 'finished_goods_store', 'quarantine')),

    CONSTRAINT ck_inventory_location_inventory_location_code_length
        CHECK (length(inventory_location_code) <= 16),
    CONSTRAINT ck_inventory_location_location_name_length
        CHECK (length(location_name) <= 100),

    CONSTRAINT ck_inventory_location_is_temperature_controlled_bool
        CHECK (is_temperature_controlled IN (0, 1)),
    CONSTRAINT ck_inventory_location_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_inventory_location_plant_area FOREIGN KEY (plant_area_id)
        REFERENCES plant_area (plant_area_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M15. maintenance_team
-- A maintenance crew. No team_lead_engineer_id column: the cycle with
-- maintenance_engineer is designed out; is_team_lead marks the lead on
-- the child side.
-- ---------------------------------------------------------------------
CREATE TABLE maintenance_team (
    maintenance_team_id           INTEGER      NOT NULL
        CONSTRAINT pk_maintenance_team PRIMARY KEY AUTOINCREMENT,
    maintenance_team_code         VARCHAR(12)  NOT NULL,
    team_name                     VARCHAR(100) NOT NULL,
    department_id                 INTEGER      NOT NULL,
    shift_id                      INTEGER      NOT NULL,
    specialization                TEXT         NOT NULL,
    base_plant_area_id            INTEGER      NULL,
    contact_extension             VARCHAR(10)  NULL,
    max_concurrent_jobs           INTEGER      NOT NULL,
    is_emergency_response         INTEGER      NOT NULL DEFAULT 0,
    target_response_time_minutes  INTEGER      NOT NULL,
    is_active                     INTEGER      NOT NULL DEFAULT 1,
    created_at                    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_maintenance_team_code UNIQUE (maintenance_team_code),

    -- MTM-XXXX : 3-5 upper-case letters
    CONSTRAINT ck_maintenance_team_code_format
        CHECK (maintenance_team_code GLOB 'MTM-[A-Z][A-Z][A-Z]*'
               AND length(maintenance_team_code) BETWEEN 7 AND 9
               AND substr(maintenance_team_code, 5) NOT GLOB '*[^A-Z]*'),
    CONSTRAINT ck_maintenance_team_max_jobs_range
        CHECK (max_concurrent_jobs BETWEEN 1 AND 10),
    CONSTRAINT ck_maintenance_team_response_target_range
        CHECK (target_response_time_minutes BETWEEN 5 AND 480),
    CONSTRAINT ck_maintenance_team_extension_digits
        CHECK (contact_extension IS NULL
               OR (length(contact_extension) > 0
                   AND contact_extension NOT GLOB '*[^0-9]*')),
    CONSTRAINT ck_maintenance_team_name_not_blank
        CHECK (length(trim(team_name)) > 0),
    CONSTRAINT ck_maintenance_team_specialization_allowed
        CHECK (specialization IN ('mechanical', 'electrical', 'automation', 'general')),

    CONSTRAINT ck_maintenance_team_maintenance_team_code_length
        CHECK (length(maintenance_team_code) <= 12),
    CONSTRAINT ck_maintenance_team_team_name_length
        CHECK (length(team_name) <= 100),
    CONSTRAINT ck_maintenance_team_contact_extension_length
        CHECK (contact_extension IS NULL OR length(contact_extension) <= 10),

    CONSTRAINT ck_maintenance_team_is_emergency_response_bool
        CHECK (is_emergency_response IN (0, 1)),
    CONSTRAINT ck_maintenance_team_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_maintenance_team_department FOREIGN KEY (department_id)
        REFERENCES department (department_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_maintenance_team_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_maintenance_team_base_area FOREIGN KEY (base_plant_area_id)
        REFERENCES plant_area (plant_area_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- department_function = 'maintenance' on the referenced department is a
-- cross-table rule, application-validated (spec section 41.3).

-- ---------------------------------------------------------------------
-- M27. alert_threshold_profile
-- A named, versioned set of monitoring limits for a machine type.
-- Monitoring POLICY, distinct from the engineering envelope on M12.
-- ---------------------------------------------------------------------
CREATE TABLE alert_threshold_profile (
    alert_threshold_profile_id    INTEGER      NOT NULL
        CONSTRAINT pk_alert_threshold_profile PRIMARY KEY AUTOINCREMENT,
    alert_threshold_profile_code  VARCHAR(20)  NOT NULL,
    profile_name                  VARCHAR(120) NOT NULL,
    machine_type_id               INTEGER      NOT NULL,
    version                       INTEGER      NOT NULL DEFAULT 1,
    is_default                    INTEGER      NOT NULL DEFAULT 0,
    sensitivity                   TEXT         NOT NULL,
    effective_from_date           DATE         NOT NULL,
    review_due_date               DATE         NULL,
    notes                         TEXT         NULL,
    is_active                     INTEGER      NOT NULL DEFAULT 1,
    created_at                    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                    DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_alert_threshold_profile_code UNIQUE (alert_threshold_profile_code),
    -- uq_alert_threshold_profile_default is a PARTIAL unique index and is
    -- created in the index section below.

    -- ATP-XXXX : 3-15 characters from A-Z, 0-9, hyphen
    CONSTRAINT ck_atp_code_format
        CHECK (alert_threshold_profile_code GLOB 'ATP-[A-Z0-9-][A-Z0-9-][A-Z0-9-]*'
               AND length(alert_threshold_profile_code) BETWEEN 7 AND 19
               AND substr(alert_threshold_profile_code, 5) NOT GLOB '*[^A-Z0-9-]*'),
    CONSTRAINT ck_atp_version_positive
        CHECK (version > 0),
    CONSTRAINT ck_atp_review_after_effective
        CHECK (review_due_date IS NULL OR review_due_date > effective_from_date),
    CONSTRAINT ck_atp_profile_name_not_blank
        CHECK (length(trim(profile_name)) > 0),
    CONSTRAINT ck_atp_sensitivity_allowed
        CHECK (sensitivity IN ('tight', 'standard', 'relaxed')),

    CONSTRAINT ck_atp_alert_threshold_profile_code_length
        CHECK (length(alert_threshold_profile_code) <= 20),
    CONSTRAINT ck_atp_profile_name_length
        CHECK (length(profile_name) <= 120),

    CONSTRAINT ck_atp_is_default_bool CHECK (is_default IN (0, 1)),
    CONSTRAINT ck_atp_is_active_bool  CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_atp_machine_type FOREIGN KEY (machine_type_id)
        REFERENCES machine_type (machine_type_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M12. machine_type_parameter
-- Which parameters each machine type exposes and the healthy envelope.
-- Engineering FACT, distinct from the alert policy on M28.
-- ---------------------------------------------------------------------
CREATE TABLE machine_type_parameter (
    machine_type_parameter_id  INTEGER       NOT NULL
        CONSTRAINT pk_machine_type_parameter PRIMARY KEY AUTOINCREMENT,
    machine_type_id            INTEGER       NOT NULL,
    machine_parameter_id       INTEGER       NOT NULL,
    nominal_value              NUMERIC(12,4) NOT NULL,
    normal_min                 NUMERIC(12,4) NOT NULL,
    normal_max                 NUMERIC(12,4) NOT NULL,
    sampling_interval_seconds  INTEGER       NOT NULL,
    is_ml_feature              INTEGER       NOT NULL DEFAULT 1,
    expected_drift_direction   TEXT          NOT NULL,
    sensor_accuracy_pct        NUMERIC(5,2)  NULL,
    criticality_weight         NUMERIC(4,2)  NULL,
    is_active                  INTEGER       NOT NULL DEFAULT 1,
    created_at                 DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                 DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_machine_type_parameter_pair
        UNIQUE (machine_type_id, machine_parameter_id),

    CONSTRAINT ck_mtp_normal_range_ordered
        CHECK (normal_min < normal_max),
    CONSTRAINT ck_mtp_nominal_within_envelope
        CHECK (nominal_value BETWEEN normal_min AND normal_max),
    CONSTRAINT ck_mtp_sampling_interval_range
        CHECK (sampling_interval_seconds BETWEEN 1 AND 3600),
    CONSTRAINT ck_mtp_sensor_accuracy_range
        CHECK (sensor_accuracy_pct IS NULL
               OR sensor_accuracy_pct BETWEEN 0 AND 25),
    CONSTRAINT ck_mtp_criticality_weight_range
        CHECK (criticality_weight IS NULL
               OR criticality_weight BETWEEN 0 AND 5),
    CONSTRAINT ck_mtp_expected_drift_direction_allowed
        CHECK (expected_drift_direction IN ('increasing', 'decreasing', 'none')),

    CONSTRAINT ck_mtp_is_ml_feature_bool CHECK (is_ml_feature IN (0, 1)),
    CONSTRAINT ck_mtp_is_active_bool     CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_mtp_machine_type FOREIGN KEY (machine_type_id)
        REFERENCES machine_type (machine_type_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mtp_machine_parameter FOREIGN KEY (machine_parameter_id)
        REFERENCES machine_parameter (machine_parameter_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- The full threshold ordering
--   physical_min <= critical_low <= warning_low <= normal_min
--                <= nominal_value <= normal_max
--                <= warning_high <= critical_high <= physical_max
-- spans machine_parameter, this table, and alert_threshold_rule. Only the
-- middle segment is a single-row check; the rest is application-validated
-- (spec sections M12 notes, 41.3).

-- =====================================================================
-- MASTER GROUP - LAYER 3
-- =====================================================================

-- ---------------------------------------------------------------------
-- M10. machine
-- The core asset of the platform. NOTE: is_active is deliberately ABSENT
-- on this table - lifecycle_status replaces it, because a two-state flag
-- cannot express standby, under overhaul, and decommissioned
-- (spec section 12.2, documented exception).
-- ---------------------------------------------------------------------
CREATE TABLE machine (
    machine_id                     INTEGER       NOT NULL
        CONSTRAINT pk_machine PRIMARY KEY AUTOINCREMENT,
    machine_code                   VARCHAR(12)   NOT NULL,
    machine_type_id                INTEGER       NOT NULL,
    production_line_id             INTEGER       NOT NULL,
    line_position                  INTEGER       NOT NULL,
    alert_threshold_profile_id     INTEGER       NULL,
    machine_name                   VARCHAR(120)  NOT NULL,
    serial_number                  VARCHAR(60)   NOT NULL,
    asset_tag                      VARCHAR(30)   NULL,
    installation_date              DATE          NOT NULL,
    commissioned_date              DATE          NOT NULL,
    warranty_expiry_date           DATE          NULL,
    criticality                    TEXT          NOT NULL,
    is_bottleneck                  INTEGER       NOT NULL DEFAULT 0,
    downstream_buffer_units        INTEGER       NULL,
    rated_capacity_units_per_hour  NUMERIC(10,2) NULL,
    lifecycle_status               TEXT          NOT NULL DEFAULT 'in_service',
    is_monitored                   INTEGER       NOT NULL DEFAULT 1,
    installed_position_notes       TEXT          NULL,
    created_at                     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_machine_code          UNIQUE (machine_code),
    CONSTRAINT uq_machine_serial_number UNIQUE (serial_number),
    -- Unique when present: SQLite treats each NULL as distinct, so
    -- untagged machines do not conflict.
    CONSTRAINT uq_machine_asset_tag     UNIQUE (asset_tag),
    CONSTRAINT uq_machine_line_position UNIQUE (production_line_id, line_position),
    -- uq_machine_bottleneck_per_line is a PARTIAL unique index and is
    -- created in the index section below.

    CONSTRAINT ck_machine_code_format
        CHECK (machine_code GLOB 'MC-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_machine_line_position_positive
        CHECK (line_position > 0),
    CONSTRAINT ck_machine_buffer_non_negative
        CHECK (downstream_buffer_units IS NULL OR downstream_buffer_units >= 0),
    CONSTRAINT ck_machine_rated_capacity_positive
        CHECK (rated_capacity_units_per_hour IS NULL
               OR rated_capacity_units_per_hour > 0),
    CONSTRAINT ck_machine_commissioned_after_installation
        CHECK (commissioned_date >= installation_date),
    CONSTRAINT ck_machine_warranty_after_commissioned
        CHECK (warranty_expiry_date IS NULL
               OR warranty_expiry_date >= commissioned_date),
    -- A monitored machine with no thresholds cannot actually be monitored
    -- while appearing configured.
    CONSTRAINT ck_machine_monitored_requires_profile
        CHECK (is_monitored = 0 OR alert_threshold_profile_id IS NOT NULL),
    CONSTRAINT ck_machine_name_not_blank
        CHECK (length(trim(machine_name)) > 0),
    CONSTRAINT ck_machine_criticality_allowed
        CHECK (criticality IN ('critical', 'high', 'standard', 'low')),
    CONSTRAINT ck_machine_lifecycle_status_allowed
        CHECK (lifecycle_status IN ('in_service', 'standby',
                                    'under_overhaul', 'decommissioned')),

    CONSTRAINT ck_machine_machine_code_length  CHECK (length(machine_code)  <= 12),
    CONSTRAINT ck_machine_machine_name_length  CHECK (length(machine_name)  <= 120),
    CONSTRAINT ck_machine_serial_number_length CHECK (length(serial_number) <= 60),
    CONSTRAINT ck_machine_asset_tag_length     CHECK (asset_tag IS NULL
                                                      OR length(asset_tag) <= 30),

    CONSTRAINT ck_machine_is_bottleneck_bool CHECK (is_bottleneck IN (0, 1)),
    CONSTRAINT ck_machine_is_monitored_bool  CHECK (is_monitored IN (0, 1)),

    CONSTRAINT fk_machine_type FOREIGN KEY (machine_type_id)
        REFERENCES machine_type (machine_type_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_machine_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_machine_alert_threshold_profile FOREIGN KEY (alert_threshold_profile_id)
        REFERENCES alert_threshold_profile (alert_threshold_profile_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- The profile-to-machine-type match rule is cross-table and
-- application-validated (spec section 41.3).

-- ---------------------------------------------------------------------
-- M14. worker
-- A person employed at the plant. A person exists exactly once in this
-- database. Primary privacy boundary (spec Part XIII).
-- ---------------------------------------------------------------------
CREATE TABLE worker (
    worker_id           INTEGER      NOT NULL
        CONSTRAINT pk_worker PRIMARY KEY AUTOINCREMENT,
    worker_code         VARCHAR(12)  NOT NULL,
    first_name          VARCHAR(60)  NOT NULL,
    last_name           VARCHAR(60)  NOT NULL,
    worker_role_id      INTEGER      NOT NULL,
    department_id       INTEGER      NOT NULL,
    production_line_id  INTEGER      NULL,
    shift_id            INTEGER      NOT NULL,
    email               VARCHAR(150) NULL,
    phone_number        VARCHAR(20)  NULL,
    hire_date           DATE         NOT NULL,
    employment_type     TEXT         NOT NULL,
    skill_level         TEXT         NOT NULL,
    is_active           INTEGER      NOT NULL DEFAULT 1,
    created_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_worker_code  UNIQUE (worker_code),
    CONSTRAINT uq_worker_email UNIQUE (email),

    CONSTRAINT ck_worker_code_format
        CHECK (worker_code GLOB 'EMP-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_worker_email_format
        CHECK (email IS NULL
               OR (email GLOB '?*@?*.?*' AND email NOT GLOB '*@*@*')),
    -- E.164 : '+' then a leading non-zero digit then 7 to 14 more digits
    CONSTRAINT ck_worker_phone_e164_format
        CHECK (phone_number IS NULL
               OR (phone_number GLOB '+[1-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'
                   AND length(phone_number) BETWEEN 9 AND 16
                   AND substr(phone_number, 2) NOT GLOB '*[^0-9]*')),
    CONSTRAINT ck_worker_first_name_not_blank
        CHECK (length(trim(first_name)) > 0),
    CONSTRAINT ck_worker_last_name_not_blank
        CHECK (length(trim(last_name)) > 0),
    CONSTRAINT ck_worker_employment_type_allowed
        CHECK (employment_type IN ('permanent', 'contract', 'apprentice')),
    CONSTRAINT ck_worker_skill_level_allowed
        CHECK (skill_level IN ('trainee', 'junior', 'intermediate',
                               'senior', 'expert')),

    CONSTRAINT ck_worker_worker_code_length  CHECK (length(worker_code) <= 12),
    CONSTRAINT ck_worker_first_name_length   CHECK (length(first_name)  <= 60),
    CONSTRAINT ck_worker_last_name_length    CHECK (length(last_name)   <= 60),
    CONSTRAINT ck_worker_email_length        CHECK (email IS NULL
                                                    OR length(email) <= 150),
    CONSTRAINT ck_worker_phone_number_length CHECK (phone_number IS NULL
                                                    OR length(phone_number) <= 20),

    CONSTRAINT ck_worker_is_active_bool CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_worker_role FOREIGN KEY (worker_role_id)
        REFERENCES worker_role (worker_role_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_worker_department FOREIGN KEY (department_id)
        REFERENCES department (department_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_worker_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_worker_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M19. inventory_item
-- Stocking POLICY and sourcing. Holds NO quantity on hand - the running
-- balance lives on inventory_movement (spec M19).
-- ---------------------------------------------------------------------
CREATE TABLE inventory_item (
    inventory_item_id              INTEGER       NOT NULL
        CONSTRAINT pk_inventory_item PRIMARY KEY AUTOINCREMENT,
    inventory_item_code            VARCHAR(24)   NOT NULL,
    item_name                      VARCHAR(150)  NOT NULL,
    item_type                      TEXT          NOT NULL,
    unit_of_measure                TEXT          NOT NULL,
    unit_cost                      NUMERIC(12,2) NOT NULL,
    reorder_point                  NUMERIC(12,2) NOT NULL,
    safety_stock_qty               NUMERIC(12,2) NOT NULL,
    max_stock_qty                  NUMERIC(12,2) NOT NULL,
    lead_time_days                 INTEGER       NOT NULL,
    primary_supplier_id            INTEGER       NULL,
    default_inventory_location_id  INTEGER       NOT NULL,
    is_critical_spare              INTEGER       NOT NULL DEFAULT 0,
    abc_class                      CHAR(1)       NOT NULL,
    shelf_life_days                INTEGER       NULL,
    specification                  TEXT          NULL,
    is_active                      INTEGER       NOT NULL DEFAULT 1,
    created_at                     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                     DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_inventory_item_code UNIQUE (inventory_item_code),

    -- INV-XX-... : 2 upper-case letters then 2-14 of A-Z, 0-9, hyphen
    CONSTRAINT ck_inventory_item_code_format
        CHECK (inventory_item_code GLOB 'INV-[A-Z][A-Z]-[A-Z0-9-][A-Z0-9-]*'
               AND length(inventory_item_code) BETWEEN 9 AND 21
               AND substr(inventory_item_code, 8) NOT GLOB '*[^A-Z0-9-]*'),
    CONSTRAINT ck_inventory_item_abc_class_allowed
        CHECK (abc_class IN ('A', 'B', 'C')),
    CONSTRAINT ck_inventory_item_unit_cost_positive
        CHECK (unit_cost > 0),
    -- Any violation makes replenishment logic incoherent.
    CONSTRAINT ck_inventory_item_stock_thresholds_ordered
        CHECK (safety_stock_qty <= reorder_point
               AND reorder_point < max_stock_qty),
    CONSTRAINT ck_inventory_item_safety_stock_non_negative
        CHECK (safety_stock_qty >= 0),
    CONSTRAINT ck_inventory_item_lead_time_non_negative
        CHECK (lead_time_days >= 0),
    CONSTRAINT ck_inventory_item_critical_spare_has_buffer
        CHECK (is_critical_spare = 0 OR safety_stock_qty > 0),
    CONSTRAINT ck_inventory_item_shelf_life_positive
        CHECK (shelf_life_days IS NULL OR shelf_life_days > 0),
    CONSTRAINT ck_inventory_item_name_not_blank
        CHECK (length(trim(item_name)) > 0),
    CONSTRAINT ck_inventory_item_item_type_allowed
        CHECK (item_type IN ('raw_material', 'component', 'consumable',
                             'spare_part', 'tooling', 'finished_good')),
    -- All six values are valid here, including BOX (spec section 37.3).
    CONSTRAINT ck_inventory_item_unit_of_measure_allowed
        CHECK (unit_of_measure IN ('EA', 'KG', 'L', 'M', 'SET', 'BOX')),

    CONSTRAINT ck_inventory_item_inventory_item_code_length
        CHECK (length(inventory_item_code) <= 24),
    CONSTRAINT ck_inventory_item_item_name_length
        CHECK (length(item_name) <= 150),
    CONSTRAINT ck_inventory_item_abc_class_length
        CHECK (length(abc_class) = 1),

    CONSTRAINT ck_inventory_item_is_critical_spare_bool
        CHECK (is_critical_spare IN (0, 1)),
    CONSTRAINT ck_inventory_item_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_inventory_item_supplier FOREIGN KEY (primary_supplier_id)
        REFERENCES supplier (supplier_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_inventory_item_location FOREIGN KEY (default_inventory_location_id)
        REFERENCES inventory_location (inventory_location_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M7. product_line_capability
-- Resolves product <-> production_line as many-to-many.
-- cycle_time_seconds is the authoritative rate figure and lives here and
-- nowhere else. No business code: it qualifies a relationship.
-- ---------------------------------------------------------------------
CREATE TABLE product_line_capability (
    product_line_capability_id  INTEGER       NOT NULL
        CONSTRAINT pk_product_line_capability PRIMARY KEY AUTOINCREMENT,
    product_id                  INTEGER       NOT NULL,
    production_line_id          INTEGER       NOT NULL,
    capability_type             TEXT          NOT NULL,
    is_primary_line             INTEGER       NOT NULL DEFAULT 0,
    cycle_time_seconds          NUMERIC(8,2)  NOT NULL,
    max_hourly_output_units     NUMERIC(10,2) NOT NULL,
    changeover_minutes          INTEGER       NOT NULL,
    is_qualified                INTEGER       NOT NULL DEFAULT 0,
    qualification_expiry_date   DATE          NULL,
    tooling_available           INTEGER       NOT NULL DEFAULT 1,
    effective_from_date         DATE          NOT NULL,
    is_active                   INTEGER       NOT NULL DEFAULT 1,
    created_at                  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_product_line_capability_pair
        UNIQUE (product_id, production_line_id),
    -- uq_plc_primary_route_per_product is a PARTIAL unique index and is
    -- created in the index section below.

    CONSTRAINT ck_plc_cycle_time_positive
        CHECK (cycle_time_seconds > 0),
    CONSTRAINT ck_plc_max_output_positive
        CHECK (max_hourly_output_units > 0),
    CONSTRAINT ck_plc_changeover_non_negative
        CHECK (changeover_minutes >= 0),
    -- A finishing stage is never the primary production route.
    CONSTRAINT ck_plc_finishing_stage_not_primary
        CHECK (capability_type <> 'finishing_stage' OR is_primary_line = 0),
    -- An expiry date on an unqualified route is contradictory.
    CONSTRAINT ck_plc_qualification_expiry_requires_qualified
        CHECK (qualification_expiry_date IS NULL OR is_qualified = 1),
    CONSTRAINT ck_plc_capability_type_allowed
        CHECK (capability_type IN ('production_route', 'finishing_stage')),

    CONSTRAINT ck_plc_is_primary_line_bool   CHECK (is_primary_line IN (0, 1)),
    CONSTRAINT ck_plc_is_qualified_bool      CHECK (is_qualified IN (0, 1)),
    CONSTRAINT ck_plc_tooling_available_bool CHECK (tooling_available IN (0, 1)),
    CONSTRAINT ck_plc_is_active_bool         CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_plc_product FOREIGN KEY (product_id)
        REFERENCES product (product_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_plc_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- M28. alert_threshold_rule
-- Warning and critical limits for one parameter within one profile.
-- Rules are ROWS, not columns, so the Monitoring Agent holds no
-- knowledge of specific parameters. No business code.
-- ---------------------------------------------------------------------
CREATE TABLE alert_threshold_rule (
    alert_threshold_rule_id          INTEGER       NOT NULL
        CONSTRAINT pk_alert_threshold_rule PRIMARY KEY AUTOINCREMENT,
    alert_threshold_profile_id       INTEGER       NOT NULL,
    machine_parameter_id             INTEGER       NOT NULL,
    warning_low                      NUMERIC(12,4) NULL,
    warning_high                     NUMERIC(12,4) NULL,
    critical_low                     NUMERIC(12,4) NULL,
    critical_high                    NUMERIC(12,4) NULL,
    sustained_duration_seconds       INTEGER       NOT NULL DEFAULT 0,
    warning_severity_level_id        INTEGER       NOT NULL,
    critical_severity_level_id       INTEGER       NOT NULL,
    rate_of_change_limit_per_minute  NUMERIC(12,4) NULL,
    is_enabled                       INTEGER       NOT NULL DEFAULT 1,
    is_active                        INTEGER       NOT NULL DEFAULT 1,
    created_at                       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                       DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_alert_threshold_rule_pair
        UNIQUE (alert_threshold_profile_id, machine_parameter_id),

    -- A rule with every limit NULL checks nothing while appearing to be
    -- configuration.
    CONSTRAINT ck_atr_at_least_one_limit
        CHECK (warning_low IS NOT NULL
               OR warning_high IS NOT NULL
               OR critical_low IS NOT NULL
               OR critical_high IS NOT NULL
               OR rate_of_change_limit_per_minute IS NOT NULL),
    CONSTRAINT ck_atr_low_side_ordered
        CHECK (critical_low IS NULL OR warning_low IS NULL
               OR critical_low <= warning_low),
    CONSTRAINT ck_atr_high_side_ordered
        CHECK (critical_high IS NULL OR warning_high IS NULL
               OR warning_high <= critical_high),
    CONSTRAINT ck_atr_sustained_duration_range
        CHECK (sustained_duration_seconds BETWEEN 0 AND 3600),
    CONSTRAINT ck_atr_rate_limit_positive
        CHECK (rate_of_change_limit_per_minute IS NULL
               OR rate_of_change_limit_per_minute > 0),
    CONSTRAINT ck_atr_severities_differ
        CHECK (critical_severity_level_id <> warning_severity_level_id),

    CONSTRAINT ck_atr_is_enabled_bool CHECK (is_enabled IN (0, 1)),
    CONSTRAINT ck_atr_is_active_bool  CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_atr_profile FOREIGN KEY (alert_threshold_profile_id)
        REFERENCES alert_threshold_profile (alert_threshold_profile_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_atr_parameter FOREIGN KEY (machine_parameter_id)
        REFERENCES machine_parameter (machine_parameter_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_atr_warning_severity FOREIGN KEY (warning_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_atr_critical_severity FOREIGN KEY (critical_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- ck_atr_severities_differ is weaker than the frozen rule, which requires
-- the critical severity to OUTRANK the warning severity. That comparison
-- needs two joins to failure_severity_level, which a CHECK cannot do; the
-- full ordering is application-validated (spec M28 notes, section 41.3).

-- ---------------------------------------------------------------------
-- M29. business_rule
-- Tunable policy parameters. Typed value columns with value_type
-- declaring which applies, so escalation decisions stay explainable.
-- ---------------------------------------------------------------------
CREATE TABLE business_rule (
    business_rule_id     INTEGER       NOT NULL
        CONSTRAINT pk_business_rule PRIMARY KEY AUTOINCREMENT,
    business_rule_code   VARCHAR(32)   NOT NULL,
    rule_name            VARCHAR(150)  NOT NULL,
    rule_category        TEXT          NOT NULL,
    value_type           TEXT          NOT NULL,
    value_numeric        NUMERIC(14,4) NULL,
    value_text           VARCHAR(100)  NULL,
    value_boolean        INTEGER       NULL,
    unit                 VARCHAR(24)   NULL,
    production_line_id   INTEGER       NULL,
    description          TEXT          NOT NULL,
    effective_from_date  DATE          NOT NULL,
    is_active            INTEGER       NOT NULL DEFAULT 1,
    created_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Globally unique: a line-scoped override is a separate rule with its
    -- own code, so every rule stays individually citable.
    CONSTRAINT uq_business_rule_code UNIQUE (business_rule_code),

    -- BR-<CAT>-<KEY> : 3-5 upper-case letters, then 2-20 of A-Z, 0-9, hyphen
    CONSTRAINT ck_business_rule_code_format
        CHECK (business_rule_code GLOB 'BR-[A-Z][A-Z][A-Z]*-[A-Z0-9-][A-Z0-9-]*'
               AND length(business_rule_code) BETWEEN 9 AND 32
               AND business_rule_code NOT GLOB '*[^A-Z0-9-]*'),
    -- Exactly one value column is populated and it matches value_type.
    -- The most important constraint on this table: a row with the wrong
    -- column filled would fail silently at read time.
    CONSTRAINT ck_business_rule_exactly_one_value
        CHECK (
            (value_type = 'numeric'
             AND value_numeric IS NOT NULL
             AND value_text    IS NULL
             AND value_boolean IS NULL)
            OR
            (value_type = 'text'
             AND value_text    IS NOT NULL
             AND value_numeric IS NULL
             AND value_boolean IS NULL)
            OR
            (value_type = 'boolean'
             AND value_boolean IS NOT NULL
             AND value_numeric IS NULL
             AND value_text    IS NULL)
        ),
    CONSTRAINT ck_business_rule_name_not_blank
        CHECK (length(trim(rule_name)) > 0),
    CONSTRAINT ck_business_rule_description_not_blank
        CHECK (length(trim(description)) > 0),
    CONSTRAINT ck_business_rule_rule_category_allowed
        CHECK (rule_category IN ('escalation', 'prioritization', 'costing',
                                 'notification', 'maintenance_policy',
                                 'inventory_policy')),
    CONSTRAINT ck_business_rule_value_type_allowed
        CHECK (value_type IN ('numeric', 'text', 'boolean')),

    CONSTRAINT ck_business_rule_business_rule_code_length
        CHECK (length(business_rule_code) <= 32),
    CONSTRAINT ck_business_rule_rule_name_length
        CHECK (length(rule_name) <= 150),
    CONSTRAINT ck_business_rule_value_text_length
        CHECK (value_text IS NULL OR length(value_text) <= 100),
    CONSTRAINT ck_business_rule_unit_length
        CHECK (unit IS NULL OR length(unit) <= 24),

    -- The only nullable flag in the schema (spec section 40.1).
    CONSTRAINT ck_business_rule_value_boolean_bool
        CHECK (value_boolean IS NULL OR value_boolean IN (0, 1)),
    CONSTRAINT ck_business_rule_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_business_rule_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- =====================================================================
-- MASTER GROUP - LAYER 4
-- =====================================================================

-- ---------------------------------------------------------------------
-- M16. maintenance_engineer
-- A one-to-one specialisation of worker. Holds NO name, email, or phone -
-- all of it comes from the parent worker row.
-- ---------------------------------------------------------------------
CREATE TABLE maintenance_engineer (
    maintenance_engineer_id    INTEGER     NOT NULL
        CONSTRAINT pk_maintenance_engineer PRIMARY KEY AUTOINCREMENT,
    maintenance_engineer_code  VARCHAR(10) NOT NULL,
    worker_id                  INTEGER     NOT NULL,
    maintenance_team_id        INTEGER     NOT NULL,
    primary_specialization     TEXT        NOT NULL,
    is_team_lead               INTEGER     NOT NULL DEFAULT 0,
    years_experience           INTEGER     NOT NULL,
    certification_expiry_date  DATE        NULL,
    is_on_call                 INTEGER     NOT NULL DEFAULT 0,
    secondary_specialization   TEXT        NULL,
    is_active                  INTEGER     NOT NULL DEFAULT 1,
    created_at                 DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                 DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_maintenance_engineer_code   UNIQUE (maintenance_engineer_code),
    -- The one-to-one enforcement.
    CONSTRAINT uq_maintenance_engineer_worker UNIQUE (worker_id),
    -- uq_maintenance_engineer_team_lead is a PARTIAL unique index and is
    -- created in the index section below.

    CONSTRAINT ck_maintenance_engineer_code_format
        CHECK (maintenance_engineer_code GLOB 'ENG-[0-9][0-9]'),
    CONSTRAINT ck_maintenance_engineer_experience_range
        CHECK (years_experience BETWEEN 0 AND 50),
    CONSTRAINT ck_maintenance_engineer_specializations_differ
        CHECK (secondary_specialization IS NULL
               OR secondary_specialization <> primary_specialization),
    CONSTRAINT ck_maintenance_engineer_primary_specialization_allowed
        CHECK (primary_specialization IN ('mechanical', 'electrical',
                                          'automation', 'general')),
    CONSTRAINT ck_maintenance_engineer_secondary_specialization_allowed
        CHECK (secondary_specialization IS NULL
               OR secondary_specialization IN ('mechanical', 'electrical',
                                               'automation', 'general')),

    CONSTRAINT ck_maintenance_engineer_maintenance_engineer_code_length
        CHECK (length(maintenance_engineer_code) <= 10),

    CONSTRAINT ck_maintenance_engineer_is_team_lead_bool CHECK (is_team_lead IN (0, 1)),
    CONSTRAINT ck_maintenance_engineer_is_on_call_bool   CHECK (is_on_call IN (0, 1)),
    CONSTRAINT ck_maintenance_engineer_is_active_bool    CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_maintenance_engineer_worker FOREIGN KEY (worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_maintenance_engineer_team FOREIGN KEY (maintenance_team_id)
        REFERENCES maintenance_team (maintenance_team_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Only workers in a maintenance department may have a row here; that is
-- cross-table and application-validated (spec section 41.3).

-- ---------------------------------------------------------------------
-- M17. notification_recipient
-- Who receives notifications, on which channels, at what severity.
-- Holds NO contact details - endpoints resolve through worker.
-- No business code.
-- ---------------------------------------------------------------------
CREATE TABLE notification_recipient (
    notification_recipient_id   INTEGER  NOT NULL
        CONSTRAINT pk_notification_recipient PRIMARY KEY AUTOINCREMENT,
    worker_id                   INTEGER  NOT NULL,
    min_severity_level_id       INTEGER  NOT NULL,
    email_enabled               INTEGER  NOT NULL DEFAULT 1,
    whatsapp_enabled            INTEGER  NOT NULL DEFAULT 0,
    scope_production_line_id    INTEGER  NULL,
    notify_outside_shift_hours  INTEGER  NOT NULL DEFAULT 0,
    escalation_order            INTEGER  NOT NULL,
    max_notifications_per_hour  INTEGER  NULL,
    is_active                   INTEGER  NOT NULL DEFAULT 1,
    created_at                  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- The one-to-one enforcement.
    CONSTRAINT uq_notification_recipient_worker UNIQUE (worker_id),

    -- A recipient with both channels disabled is unreachable, and a
    -- configured-but-unreachable recipient is worse than none because it
    -- looks correct.
    CONSTRAINT ck_notification_recipient_channel_enabled
        CHECK (email_enabled = 1 OR whatsapp_enabled = 1),
    CONSTRAINT ck_notification_recipient_escalation_order_positive
        CHECK (escalation_order > 0),
    CONSTRAINT ck_notification_recipient_rate_limit_range
        CHECK (max_notifications_per_hour IS NULL
               OR max_notifications_per_hour BETWEEN 1 AND 60),

    CONSTRAINT ck_notification_recipient_email_enabled_bool
        CHECK (email_enabled IN (0, 1)),
    CONSTRAINT ck_notification_recipient_whatsapp_enabled_bool
        CHECK (whatsapp_enabled IN (0, 1)),
    CONSTRAINT ck_notification_recipient_notify_outside_shift_hours_bool
        CHECK (notify_outside_shift_hours IN (0, 1)),
    CONSTRAINT ck_notification_recipient_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_notification_recipient_worker FOREIGN KEY (worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_notification_recipient_severity FOREIGN KEY (min_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_notification_recipient_scope_line FOREIGN KEY (scope_production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- The channel-to-endpoint rule depends on worker.email and
-- worker.phone_number and is application-validated at configuration time.
-- "At least one active recipient must exist with the most severe
-- min_severity_level_id and notify_outside_shift_hours = 1" is a seed
-- validation check that gates simulator start (spec sections 41.3, 7).

-- ---------------------------------------------------------------------
-- M20. bill_of_materials
-- Materials consumed per unit of product. Resolves
-- product <-> inventory_item as many-to-many, with inventory_item
-- referenced twice: as material and as approved substitute.
-- No business code, and no unit_of_measure column - quantity is in the
-- referenced item's own unit.
-- ---------------------------------------------------------------------
CREATE TABLE bill_of_materials (
    bill_of_materials_id          INTEGER       NOT NULL
        CONSTRAINT pk_bill_of_materials PRIMARY KEY AUTOINCREMENT,
    product_id                    INTEGER       NOT NULL,
    inventory_item_id             INTEGER       NOT NULL,
    quantity_per_unit             NUMERIC(12,4) NOT NULL,
    scrap_allowance_pct           NUMERIC(5,2)  NOT NULL DEFAULT 0,
    is_critical_component         INTEGER       NOT NULL DEFAULT 0,
    substitute_inventory_item_id  INTEGER       NULL,
    effective_from_date           DATE          NOT NULL,
    is_active                     INTEGER       NOT NULL DEFAULT 1,
    created_at                    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_bill_of_materials_pair UNIQUE (product_id, inventory_item_id),

    CONSTRAINT ck_bom_quantity_positive
        CHECK (quantity_per_unit > 0),
    CONSTRAINT ck_bom_scrap_allowance_range
        CHECK (scrap_allowance_pct BETWEEN 0 AND 50),
    CONSTRAINT ck_bom_substitute_differs
        CHECK (substitute_inventory_item_id IS NULL
               OR substitute_inventory_item_id <> inventory_item_id),

    CONSTRAINT ck_bom_is_critical_component_bool
        CHECK (is_critical_component IN (0, 1)),
    CONSTRAINT ck_bom_is_active_bool
        CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_bom_product FOREIGN KEY (product_id)
        REFERENCES product (product_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_bom_inventory_item FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_bom_substitute_item FOREIGN KEY (substitute_inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- inventory_item_id must not reference a finished_good, and a substitute
-- should share the item's unit_of_measure. Both are cross-table and
-- application-validated (spec M20 notes, section 41.3).

-- ---------------------------------------------------------------------
-- M25. machine_type_failure_mode
-- Which failure modes are plausible on which machine type. Five parents -
-- the most connected table in the master group, and deliberately so: it
-- is the single join where equipment, failure taxonomy, severity policy,
-- telemetry, and inventory meet. No business code.
-- ---------------------------------------------------------------------
CREATE TABLE machine_type_failure_mode (
    machine_type_failure_mode_id       INTEGER  NOT NULL
        CONSTRAINT pk_machine_type_failure_mode PRIMARY KEY AUTOINCREMENT,
    machine_type_id                    INTEGER  NOT NULL,
    failure_category_id                INTEGER  NOT NULL,
    typical_severity_level_id          INTEGER  NOT NULL,
    primary_machine_parameter_id       INTEGER  NULL,
    required_inventory_item_id         INTEGER  NULL,
    leading_indicator_description      TEXT     NOT NULL,
    estimated_repair_duration_minutes  INTEGER  NOT NULL,
    typical_warning_period_hours       INTEGER  NULL,
    is_model_predictable               INTEGER  NOT NULL DEFAULT 0,
    relative_frequency                 TEXT     NOT NULL,
    is_active                          INTEGER  NOT NULL DEFAULT 1,
    created_at                         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                         DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_mtfm_pair UNIQUE (machine_type_id, failure_category_id),

    CONSTRAINT ck_mtfm_repair_duration_positive
        CHECK (estimated_repair_duration_minutes > 0),
    CONSTRAINT ck_mtfm_warning_period_positive
        CHECK (typical_warning_period_hours IS NULL
               OR typical_warning_period_hours > 0),
    -- An unpredictable failure cannot have a warning period.
    CONSTRAINT ck_mtfm_unpredictable_has_no_warning
        CHECK (is_model_predictable = 1 OR typical_warning_period_hours IS NULL),
    -- A failure claimed predictable must have a telemetry signal.
    CONSTRAINT ck_mtfm_predictable_has_indicator
        CHECK (is_model_predictable = 0 OR primary_machine_parameter_id IS NOT NULL),
    CONSTRAINT ck_mtfm_leading_indicator_not_blank
        CHECK (length(trim(leading_indicator_description)) > 0),
    CONSTRAINT ck_mtfm_relative_frequency_allowed
        CHECK (relative_frequency IN ('common', 'occasional', 'rare')),

    CONSTRAINT ck_mtfm_is_model_predictable_bool CHECK (is_model_predictable IN (0, 1)),
    CONSTRAINT ck_mtfm_is_active_bool            CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_mtfm_machine_type FOREIGN KEY (machine_type_id)
        REFERENCES machine_type (machine_type_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mtfm_failure_category FOREIGN KEY (failure_category_id)
        REFERENCES failure_category (failure_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mtfm_severity FOREIGN KEY (typical_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mtfm_parameter FOREIGN KEY (primary_machine_parameter_id)
        REFERENCES machine_parameter (machine_parameter_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mtfm_inventory_item FOREIGN KEY (required_inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- The stronger form of ck_mtfm_predictable_has_indicator - that the
-- indicator must also be flagged is_ml_feature on machine_type_parameter -
-- is cross-table and application-validated (spec section 41.3).

-- ---------------------------------------------------------------------
-- M26. machine_maintenance_schedule
-- Planned maintenance policy per machine. baseline_start_date is the ONLY
-- date stored: last_performed_date and next_due_date are deliberately
-- absent because both are derived from operational completion history,
-- and a cached due date that goes stale would make the platform wrong
-- about the exact thing it exists to get right (spec M26).
-- ---------------------------------------------------------------------
CREATE TABLE machine_maintenance_schedule (
    machine_maintenance_schedule_id    INTEGER     NOT NULL
        CONSTRAINT pk_machine_maintenance_schedule PRIMARY KEY AUTOINCREMENT,
    machine_maintenance_schedule_code  VARCHAR(12) NOT NULL,
    machine_id                         INTEGER     NOT NULL,
    maintenance_type                   TEXT        NOT NULL,
    interval_basis                     TEXT        NOT NULL,
    interval_value                     INTEGER     NOT NULL,
    estimated_duration_minutes         INTEGER     NOT NULL,
    requires_line_stop                 INTEGER     NOT NULL DEFAULT 0,
    assigned_maintenance_team_id       INTEGER     NULL,
    required_inventory_item_id         INTEGER     NULL,
    baseline_start_date                DATE        NOT NULL,
    can_be_deferred                    INTEGER     NOT NULL DEFAULT 1,
    max_deferral_days                  INTEGER     NULL,
    task_summary                       TEXT        NULL,
    is_active                          INTEGER     NOT NULL DEFAULT 1,
    created_at                         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at                         DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_machine_maintenance_schedule_code
        UNIQUE (machine_maintenance_schedule_code),

    CONSTRAINT ck_mms_code_format
        CHECK (machine_maintenance_schedule_code GLOB 'SCH-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_mms_interval_value_positive
        CHECK (interval_value > 0),
    CONSTRAINT ck_mms_duration_positive
        CHECK (estimated_duration_minutes > 0),
    -- A deferral limit on a non-deferrable task is contradictory and would
    -- surface as a bad recommendation.
    CONSTRAINT ck_mms_deferral_consistency
        CHECK (can_be_deferred = 1 OR max_deferral_days IS NULL),
    CONSTRAINT ck_mms_max_deferral_positive
        CHECK (max_deferral_days IS NULL OR max_deferral_days > 0),
    CONSTRAINT ck_mms_maintenance_type_allowed
        CHECK (maintenance_type IN ('preventive', 'predictive', 'calibration',
                                    'inspection', 'lubrication')),
    CONSTRAINT ck_mms_interval_basis_allowed
        CHECK (interval_basis IN ('calendar_days', 'operating_hours',
                                  'cycle_count')),

    CONSTRAINT ck_mms_machine_maintenance_schedule_code_length
        CHECK (length(machine_maintenance_schedule_code) <= 12),

    CONSTRAINT ck_mms_requires_line_stop_bool CHECK (requires_line_stop IN (0, 1)),
    CONSTRAINT ck_mms_can_be_deferred_bool    CHECK (can_be_deferred IN (0, 1)),
    CONSTRAINT ck_mms_is_active_bool          CHECK (is_active IN (0, 1)),

    CONSTRAINT fk_mms_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mms_team FOREIGN KEY (assigned_maintenance_team_id)
        REFERENCES maintenance_team (maintenance_team_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mms_inventory_item FOREIGN KEY (required_inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- A machine may have several schedules of different types and intervals -
-- the normal case, so no unique constraint restricts it to one.

-- =====================================================================
-- OPERATIONAL AND SYSTEM GROUPS - LAYER 0
-- Operational roots: no outbound operational foreign keys. Every one of
-- them references master tables only, which is what keeps the operational
-- reference graph acyclic (spec sections 14, 24).
--
-- No is_active on any operational table: operational rows are purged on a
-- retention schedule, not soft-retired (spec section 13.2).
-- created_by_component appears on all 24 and carries the shared
-- platform_component vocabulary.
-- =====================================================================

-- ---------------------------------------------------------------------
-- O4. production_run
-- One execution of a product order on a line. 11 inbound operational
-- references - the most connected operational table.
-- ---------------------------------------------------------------------
CREATE TABLE production_run (
    production_run_id           INTEGER       NOT NULL
        CONSTRAINT pk_production_run PRIMARY KEY AUTOINCREMENT,
    production_run_code         VARCHAR(16)   NOT NULL,
    product_id                  INTEGER       NOT NULL,
    production_line_id          INTEGER       NOT NULL,
    product_line_capability_id  INTEGER       NOT NULL,
    customer_id                 INTEGER       NOT NULL,
    planned_quantity_units      NUMERIC(12,2) NOT NULL,
    planned_start_at            DATETIME      NOT NULL,
    planned_end_at              DATETIME      NOT NULL,
    actual_start_at             DATETIME      NULL,
    actual_end_at               DATETIME      NULL,
    due_date                    DATE          NOT NULL,
    priority                    TEXT          NOT NULL DEFAULT 'normal',
    run_status                  TEXT          NOT NULL DEFAULT 'planned',
    pause_reason                TEXT          NULL,
    cancellation_reason         TEXT          NULL,
    created_at                  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component        TEXT          NOT NULL,
    updated_at                  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_production_run_code UNIQUE (production_run_code),
    -- uq_production_run_active_per_line is a PARTIAL unique index and is
    -- created in the index section below. It is the primary concurrency
    -- guard for the Simulator transaction.

    CONSTRAINT ck_pr_code_format
        CHECK (production_run_code
               GLOB 'RUN-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_pr_planned_quantity_positive
        CHECK (planned_quantity_units > 0),
    CONSTRAINT ck_pr_planned_window_ordered
        CHECK (planned_end_at > planned_start_at),
    CONSTRAINT ck_pr_actual_window_ordered
        CHECK (actual_end_at IS NULL OR actual_start_at IS NULL
               OR actual_end_at > actual_start_at),
    CONSTRAINT ck_pr_pause_reason_consistency
        CHECK ((pause_reason IS NOT NULL) = (run_status = 'paused')),
    CONSTRAINT ck_pr_cancellation_reason_required
        CHECK (run_status <> 'cancelled' OR cancellation_reason IS NOT NULL),
    CONSTRAINT ck_pr_started_when_beyond_setup
        CHECK (run_status IN ('planned', 'setup', 'cancelled')
               OR actual_start_at IS NOT NULL),
    CONSTRAINT ck_pr_ended_when_terminal
        CHECK (run_status <> 'completed' OR actual_end_at IS NOT NULL),
    CONSTRAINT ck_pr_priority_allowed
        CHECK (priority IN ('normal', 'high', 'urgent')),
    CONSTRAINT ck_pr_run_status_allowed
        CHECK (run_status IN ('planned', 'setup', 'running', 'paused',
                              'completed', 'cancelled')),
    CONSTRAINT ck_pr_pause_reason_allowed
        CHECK (pause_reason IS NULL
               OR pause_reason IN ('machine_down', 'material_shortage',
                                   'quality_hold', 'operator_unavailable',
                                   'shift_end', 'higher_priority_run')),
    CONSTRAINT ck_pr_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_pr_production_run_code_length
        CHECK (length(production_run_code) <= 16),

    CONSTRAINT fk_pr_product FOREIGN KEY (product_id)
        REFERENCES product (product_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_capability FOREIGN KEY (product_line_capability_id)
        REFERENCES product_line_capability (product_line_capability_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_customer FOREIGN KEY (customer_id)
        REFERENCES customer (customer_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- The four status-consistency checks reject illegal STATES. They cannot
-- express legal TRANSITIONS - no declarative constraint can compare a row
-- to its own previous version (spec O4 notes, section 41.3).

-- ---------------------------------------------------------------------
-- O14. operational_alert
-- The managed case correlating related events. No resolved_by_work_record_id
-- column: that would create the only circular dependency in the
-- operational schema (spec O14).
-- ---------------------------------------------------------------------
CREATE TABLE operational_alert (
    operational_alert_id        INTEGER       NOT NULL
        CONSTRAINT pk_operational_alert PRIMARY KEY AUTOINCREMENT,
    operational_alert_code      VARCHAR(20)   NOT NULL,
    correlation_key             VARCHAR(120)  NOT NULL,
    alert_category              TEXT          NOT NULL,
    machine_id                  INTEGER       NULL,
    production_line_id          INTEGER       NULL,
    inventory_item_id           INTEGER       NULL,
    initial_severity_level_id   INTEGER       NOT NULL,
    current_severity_level_id   INTEGER       NOT NULL,
    alert_status                TEXT          NOT NULL DEFAULT 'open',
    event_count                 INTEGER       NOT NULL DEFAULT 1,
    opened_at                   DATETIME      NOT NULL,
    first_event_at              DATETIME      NOT NULL,
    last_event_at               DATETIME      NOT NULL,
    acknowledged_at             DATETIME      NULL,
    acknowledged_by_worker_id   INTEGER       NULL,
    escalated_at                DATETIME      NULL,
    resolved_at                 DATETIME      NULL,
    resolution_type             TEXT          NULL,
    closed_at                   DATETIME      NULL,
    suppression_reason          TEXT          NULL,
    resolution_note             TEXT          NULL,
    created_at                  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component        TEXT          NOT NULL,
    updated_at                  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_oa_code UNIQUE (operational_alert_code),
    -- uq_oa_open_correlation_key is a PARTIAL unique index and is created
    -- in the index section below. It is the alert-storm prevention
    -- mechanism and the most important constraint in the operational group.

    CONSTRAINT ck_oa_code_format
        CHECK (operational_alert_code
               GLOB 'ALR-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_oa_event_count_positive
        CHECK (event_count >= 1),
    CONSTRAINT ck_oa_opened_equals_first_event
        CHECK (opened_at = first_event_at),
    CONSTRAINT ck_oa_last_event_not_before_first
        CHECK (last_event_at >= first_event_at),
    CONSTRAINT ck_oa_timestamp_sequence
        CHECK ((acknowledged_at IS NULL OR acknowledged_at >= opened_at)
               AND (escalated_at IS NULL OR escalated_at >= opened_at)
               AND (resolved_at  IS NULL OR resolved_at  >= opened_at)
               AND (closed_at    IS NULL OR closed_at    >= opened_at)
               AND (closed_at    IS NULL OR resolved_at IS NULL
                    OR closed_at >= resolved_at)),
    CONSTRAINT ck_oa_acknowledged_paired
        CHECK ((acknowledged_at IS NULL) = (acknowledged_by_worker_id IS NULL)),
    CONSTRAINT ck_oa_resolution_type_required
        CHECK (alert_status NOT IN ('resolved', 'closed')
               OR resolution_type IS NOT NULL),
    CONSTRAINT ck_oa_closed_requires_note
        CHECK (alert_status <> 'closed' OR resolution_note IS NOT NULL),
    -- An unexplained false positive teaches nothing, and these records
    -- drive the threshold tuning cycle.
    CONSTRAINT ck_oa_false_positive_requires_note
        CHECK (resolution_type IS NULL OR resolution_type <> 'false_positive'
               OR resolution_note IS NOT NULL),
    CONSTRAINT ck_oa_suppression_reason_required
        CHECK (alert_status <> 'suppressed' OR suppression_reason IS NOT NULL),
    CONSTRAINT ck_oa_correlation_key_not_blank
        CHECK (length(trim(correlation_key)) > 0),
    CONSTRAINT ck_oa_alert_category_allowed
        CHECK (alert_category IN ('machine_condition', 'machine_output',
                                  'quality', 'inventory', 'data_quality')),
    CONSTRAINT ck_oa_alert_status_allowed
        CHECK (alert_status IN ('open', 'acknowledged', 'escalated',
                                'resolved', 'closed', 'suppressed')),
    CONSTRAINT ck_oa_resolution_type_allowed
        CHECK (resolution_type IS NULL
               OR resolution_type IN ('auto_recovered', 'maintenance_performed',
                                      'false_positive', 'superseded',
                                      'manual_close')),
    CONSTRAINT ck_oa_suppression_reason_allowed
        CHECK (suppression_reason IS NULL
               OR suppression_reason IN ('maintenance_in_progress',
                                         'machine_offline', 'planned_downtime',
                                         'duplicate_condition', 'rate_limited')),
    CONSTRAINT ck_oa_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_oa_operational_alert_code_length
        CHECK (length(operational_alert_code) <= 20),
    CONSTRAINT ck_oa_correlation_key_length
        CHECK (length(correlation_key) <= 120),

    CONSTRAINT fk_oa_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oa_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oa_inventory_item FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oa_initial_severity FOREIGN KEY (initial_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oa_current_severity FOREIGN KEY (current_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oa_acknowledged_by FOREIGN KEY (acknowledged_by_worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- current_severity_level_id may only become MORE severe over an alert's
-- life. That compares a row to its previous version, so it is
-- application-enforced (spec O14 notes, section 41.3).

-- ---------------------------------------------------------------------
-- O22. dashboard_snapshot
-- Periodic materialised capture of factory state. Fully derived and
-- rebuildable, so it carries no reconciliation obligation.
-- Read by no agent, deliberately (spec O22 notes).
-- ---------------------------------------------------------------------
CREATE TABLE dashboard_snapshot (
    dashboard_snapshot_id          INTEGER  NOT NULL
        CONSTRAINT pk_dashboard_snapshot PRIMARY KEY AUTOINCREMENT,
    snapshot_at                    DATETIME NOT NULL,
    snapshot_scope                 TEXT     NOT NULL,
    production_line_id             INTEGER  NULL,
    machine_id                     INTEGER  NULL,
    snapshot_document              TEXT     NOT NULL,
    computed_from_window_seconds   INTEGER  NOT NULL,
    generation_duration_ms         INTEGER  NOT NULL,
    created_at                     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component           TEXT     NOT NULL,
    updated_at                     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- uq_ds_scope_subject_time is a unique EXPRESSION index over COALESCE
    -- and is created in the index section below. A plain unique constraint
    -- cannot express it, because SQLite treats every NULL as distinct.

    CONSTRAINT ck_ds_line_scope_subject
        CHECK (snapshot_scope <> 'production_line'
               OR production_line_id IS NOT NULL),
    CONSTRAINT ck_ds_machine_scope_subject
        CHECK (snapshot_scope <> 'machine' OR machine_id IS NOT NULL),
    CONSTRAINT ck_ds_plant_scope_no_subject
        CHECK (snapshot_scope <> 'plant'
               OR (production_line_id IS NULL AND machine_id IS NULL)),
    CONSTRAINT ck_ds_window_positive
        CHECK (computed_from_window_seconds > 0),
    CONSTRAINT ck_ds_generation_duration_non_negative
        CHECK (generation_duration_ms >= 0),
    -- JSON is stored as TEXT; structural validation only (spec section 39.3)
    CONSTRAINT ck_ds_snapshot_document_is_object
        CHECK (json_valid(snapshot_document)
               AND json_type(snapshot_document) = 'object'),
    CONSTRAINT ck_ds_snapshot_scope_allowed
        CHECK (snapshot_scope IN ('plant', 'production_line', 'machine')),
    CONSTRAINT ck_ds_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_ds_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ds_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- O23. audit_log  [SYSTEM GROUP]
-- Append-only record of significant system and human actions.
-- entity_id is DELIBERATELY NOT A FOREIGN KEY: the audit trail must
-- survive the retention purge of the row it describes (spec section 48.3).
-- ---------------------------------------------------------------------
CREATE TABLE audit_log (
    audit_log_id          INTEGER     NOT NULL
        CONSTRAINT pk_audit_log PRIMARY KEY AUTOINCREMENT,
    occurred_at           DATETIME    NOT NULL,
    component             TEXT        NOT NULL,
    action_type           TEXT        NOT NULL,
    entity_name           VARCHAR(60) NULL,
    entity_id             INTEGER     NULL,
    entity_code           VARCHAR(32) NULL,
    actor_worker_id       INTEGER     NULL,
    correlation_id        VARCHAR(40) NOT NULL,
    outcome               TEXT        NOT NULL,
    action_detail         TEXT        NULL,
    error_message         TEXT        NULL,
    created_at            DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component  TEXT        NOT NULL,

    CONSTRAINT ck_al_failure_requires_message
        CHECK (outcome <> 'failure' OR error_message IS NOT NULL),
    CONSTRAINT ck_al_correlation_id_not_blank
        CHECK (length(trim(correlation_id)) > 0),
    -- A row identifier without a table name is unusable.
    CONSTRAINT ck_al_entity_reference_paired
        CHECK (entity_id IS NULL OR entity_name IS NOT NULL),
    CONSTRAINT ck_al_action_detail_is_object
        CHECK (action_detail IS NULL
               OR (json_valid(action_detail)
                   AND json_type(action_detail) = 'object')),
    CONSTRAINT ck_al_component_allowed
        CHECK (component IN ('simulator', 'monitoring_agent', 'prediction_agent',
                             'supervisor_agent', 'decision_agent',
                             'notification_service', 'dashboard', 'platform')),
    CONSTRAINT ck_al_action_type_allowed
        CHECK (action_type IN ('entity_created', 'entity_updated',
                               'state_transition', 'decision_made',
                               'human_action', 'configuration_changed',
                               'component_error', 'retention_purge',
                               'reconciliation_run')),
    CONSTRAINT ck_al_outcome_allowed
        CHECK (outcome IN ('success', 'failure', 'denied')),
    CONSTRAINT ck_al_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_al_entity_name_length    CHECK (entity_name IS NULL
                                                  OR length(entity_name) <= 60),
    CONSTRAINT ck_al_entity_code_length    CHECK (entity_code IS NULL
                                                  OR length(entity_code) <= 32),
    CONSTRAINT ck_al_correlation_id_length CHECK (length(correlation_id) <= 40),

    CONSTRAINT fk_al_actor_worker FOREIGN KEY (actor_worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- actor_worker_id is NULL for system actions: the human/machine
-- distinction is the audit's core question.
-- Retention purges are themselves audited with
-- action_type = 'retention_purge', because the absence of data must be
-- explicable (spec O23 notes).

-- ---------------------------------------------------------------------
-- O24. system_health_status  [SYSTEM GROUP]
-- Current liveness, lag, and error state per pipeline component.
-- The ONLY table in the database with no foreign keys at all, because its
-- subject is a software component rather than a factory object.
-- ---------------------------------------------------------------------
CREATE TABLE system_health_status (
    system_health_status_id    INTEGER  NOT NULL
        CONSTRAINT pk_system_health_status PRIMARY KEY AUTOINCREMENT,
    component                  TEXT     NOT NULL,
    status                     TEXT     NOT NULL,
    last_heartbeat_at          DATETIME NOT NULL,
    last_successful_run_at     DATETIME NULL,
    consecutive_failure_count  INTEGER  NOT NULL DEFAULT 0,
    processing_lag_seconds     INTEGER  NULL,
    pending_backlog_count      INTEGER  NULL,
    last_error_at              DATETIME NULL,
    last_error_message         TEXT     NULL,
    metrics_document           TEXT     NULL,
    created_at                 DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component       TEXT     NOT NULL,
    updated_at                 DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- One row per component for the platform's life.
    CONSTRAINT uq_shs_component UNIQUE (component),

    CONSTRAINT ck_shs_failure_count_non_negative
        CHECK (consecutive_failure_count >= 0),
    CONSTRAINT ck_shs_lag_non_negative
        CHECK (processing_lag_seconds IS NULL OR processing_lag_seconds >= 0),
    CONSTRAINT ck_shs_backlog_non_negative
        CHECK (pending_backlog_count IS NULL OR pending_backlog_count >= 0),
    CONSTRAINT ck_shs_error_fields_paired
        CHECK ((last_error_at IS NULL) = (last_error_message IS NULL)),
    CONSTRAINT ck_shs_successful_run_not_after_heartbeat
        CHECK (last_successful_run_at IS NULL
               OR last_successful_run_at <= last_heartbeat_at),
    CONSTRAINT ck_shs_error_not_after_heartbeat
        CHECK (last_error_at IS NULL OR last_error_at <= last_heartbeat_at),
    CONSTRAINT ck_shs_metrics_document_is_object
        CHECK (metrics_document IS NULL
               OR (json_valid(metrics_document)
                   AND json_type(metrics_document) = 'object')),
    CONSTRAINT ck_shs_component_allowed
        CHECK (component IN ('simulator', 'monitoring_agent', 'prediction_agent',
                             'supervisor_agent', 'decision_agent',
                             'notification_service', 'dashboard', 'platform')),
    CONSTRAINT ck_shs_status_allowed
        CHECK (status IN ('healthy', 'degraded', 'failed', 'stopped')),
    CONSTRAINT ck_shs_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform'))
);
-- A stale heartbeat overrides the recorded status. That is a read-time
-- comparison against last_heartbeat_at rather than a stored value, and it
-- is an application rule (spec O24 notes, section 41.3).

-- =====================================================================
-- OPERATIONAL GROUP - LAYER 1
-- =====================================================================

-- ---------------------------------------------------------------------
-- O1. machine_sensor_reading
-- The raw signal of the platform and the base of the evidence chain.
-- Largest table in the database by two orders of magnitude
-- (~87,000 rows/day, ~32 million/year).
-- ---------------------------------------------------------------------
CREATE TABLE machine_sensor_reading (
    machine_sensor_reading_id  INTEGER       NOT NULL
        CONSTRAINT pk_machine_sensor_reading PRIMARY KEY AUTOINCREMENT,
    machine_id                 INTEGER       NOT NULL,
    machine_parameter_id       INTEGER       NOT NULL,
    recorded_at                DATETIME      NOT NULL,
    reading_value              NUMERIC(12,4) NOT NULL,
    quality_flag               TEXT          NOT NULL DEFAULT 'valid',
    machine_state_at_reading   TEXT          NOT NULL,
    shift_id                   INTEGER       NOT NULL,
    production_run_id          INTEGER       NULL,
    sequence_number            INTEGER       NOT NULL,
    created_at                 DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component       TEXT          NOT NULL,

    -- Ordering guarantee, and it doubles as an idempotency key for replay.
    CONSTRAINT uq_msr_machine_sequence UNIQUE (machine_id, sequence_number),

    CONSTRAINT ck_msr_sequence_number_positive
        CHECK (sequence_number > 0),
    CONSTRAINT ck_msr_quality_flag_allowed
        CHECK (quality_flag IN ('valid', 'out_of_physical_range',
                                'sensor_offline', 'interpolated', 'stale')),
    CONSTRAINT ck_msr_machine_state_at_reading_allowed
        CHECK (machine_state_at_reading IN ('running', 'idle', 'setup',
                                            'starved', 'blocked',
                                            'down_unplanned', 'down_planned',
                                            'offline')),
    CONSTRAINT ck_msr_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_msr_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_msr_parameter FOREIGN KEY (machine_parameter_id)
        REFERENCES machine_parameter (machine_parameter_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_msr_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_msr_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- reading_value is DELIBERATELY NOT constrained against the parameter's
-- physical_min/physical_max. A CHECK cannot reference another table, and
-- more importantly out-of-range readings must be STORED, not rejected:
-- discarding them would hide instrument failure, and a run of
-- out_of_physical_range readings is precisely how a failed sensor is
-- detected. The verdict is recorded in quality_flag (spec O1).

-- ---------------------------------------------------------------------
-- O5. production_progress
-- Cumulative run state captured every 15 minutes. A snapshot's purpose is
-- to freeze computed state, so storing derived values here is correct.
-- No business code.
-- ---------------------------------------------------------------------
CREATE TABLE production_progress (
    production_progress_id        INTEGER       NOT NULL
        CONSTRAINT pk_production_progress PRIMARY KEY AUTOINCREMENT,
    production_run_id             INTEGER       NOT NULL,
    snapshot_at                   DATETIME      NOT NULL,
    quantity_good_cumulative      NUMERIC(12,2) NOT NULL,
    quantity_scrapped_cumulative  NUMERIC(12,2) NOT NULL,
    quantity_rework_cumulative    NUMERIC(12,2) NOT NULL,
    percent_complete              NUMERIC(5,2)  NOT NULL,
    current_rate_units_per_hour   NUMERIC(10,2) NOT NULL,
    elapsed_production_seconds    INTEGER       NOT NULL,
    downtime_seconds_cumulative   INTEGER       NOT NULL DEFAULT 0,
    projected_completion_at       DATETIME      NULL,
    schedule_variance_minutes     INTEGER       NOT NULL,
    is_behind_schedule            INTEGER       NOT NULL,
    scrap_rate_pct                NUMERIC(5,2)  NOT NULL,
    shift_id                      INTEGER       NOT NULL,
    created_at                    DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component          TEXT          NOT NULL,

    -- One snapshot per run per interval; makes snapshot writing idempotent.
    CONSTRAINT uq_pp_run_snapshot UNIQUE (production_run_id, snapshot_at),

    CONSTRAINT ck_pp_quantities_non_negative
        CHECK (quantity_good_cumulative     >= 0
               AND quantity_scrapped_cumulative >= 0
               AND quantity_rework_cumulative   >= 0),
    -- Deliberately NOT capped at 100: overproduction is legitimate.
    CONSTRAINT ck_pp_percent_complete_non_negative
        CHECK (percent_complete >= 0),
    CONSTRAINT ck_pp_rate_non_negative
        CHECK (current_rate_units_per_hour >= 0),
    CONSTRAINT ck_pp_elapsed_non_negative
        CHECK (elapsed_production_seconds >= 0),
    CONSTRAINT ck_pp_downtime_non_negative
        CHECK (downtime_seconds_cumulative >= 0),
    CONSTRAINT ck_pp_scrap_rate_range
        CHECK (scrap_rate_pct BETWEEN 0 AND 100),
    CONSTRAINT ck_pp_projection_after_snapshot
        CHECK (projected_completion_at IS NULL
               OR projected_completion_at > snapshot_at),
    -- No default: this is a judgement the writing component must make
    -- (spec section 40.1).
    CONSTRAINT ck_pp_is_behind_schedule_bool
        CHECK (is_behind_schedule IN (0, 1)),
    CONSTRAINT ck_pp_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_pp_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pp_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- schedule_variance_minutes is deliberately signed with no lower bound: a
-- run ahead of schedule is a legitimate negative. The non-decreasing rule
-- on cumulative quantities is cross-row and is a reconciliation check
-- (spec section 41.6).

-- ---------------------------------------------------------------------
-- O6. production_count
-- Pre-aggregated counts per machine per interval. EXPLICITLY DERIVED from
-- cycle_history, so it can be dropped and rebuilt. No business code.
-- ---------------------------------------------------------------------
CREATE TABLE production_count (
    production_count_id       INTEGER  NOT NULL
        CONSTRAINT pk_production_count PRIMARY KEY AUTOINCREMENT,
    machine_id                INTEGER  NOT NULL,
    production_run_id         INTEGER  NULL,
    interval_from             DATETIME NOT NULL,
    interval_to               DATETIME NOT NULL,
    good_count                INTEGER  NOT NULL DEFAULT 0,
    scrap_count               INTEGER  NOT NULL DEFAULT 0,
    rework_count              INTEGER  NOT NULL DEFAULT 0,
    cycles_completed          INTEGER  NOT NULL DEFAULT 0,
    total_cycle_time_seconds  INTEGER  NOT NULL DEFAULT 0,
    running_seconds           INTEGER  NOT NULL DEFAULT 0,
    shift_id                  INTEGER  NOT NULL,
    created_at                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component      TEXT     NOT NULL,
    updated_at                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- The idempotency guarantee: re-aggregating an interval updates one
    -- row rather than inserting a duplicate.
    CONSTRAINT uq_pc_machine_interval UNIQUE (machine_id, interval_from),

    CONSTRAINT ck_pc_interval_ordered
        CHECK (interval_to > interval_from),
    CONSTRAINT ck_pc_counts_non_negative
        CHECK (good_count >= 0 AND scrap_count >= 0 AND rework_count >= 0),
    -- The self-checking invariant. A violation is an aggregation defect,
    -- caught at write time rather than discovered in a report.
    CONSTRAINT ck_pc_cycles_equal_outcomes
        CHECK (cycles_completed = good_count + scrap_count + rework_count),
    CONSTRAINT ck_pc_total_cycle_time_non_negative
        CHECK (total_cycle_time_seconds >= 0),
    CONSTRAINT ck_pc_running_seconds_non_negative
        CHECK (running_seconds >= 0),
    -- Both arguments are deterministic column values, so the interval
    -- computation is valid inside a CHECK (spec O6 notes).
    CONSTRAINT ck_pc_running_within_interval
        CHECK (running_seconds
               <= (strftime('%s', interval_to) - strftime('%s', interval_from))),
    CONSTRAINT ck_pc_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_pc_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pc_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pc_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- An interval where the machine never ran produces a row with ZERO counts,
-- not an absent row: absence would be ambiguous between "did not produce"
-- and "not yet aggregated" (spec O6 notes).

-- ---------------------------------------------------------------------
-- O7. cycle_history
-- Every individual machine cycle. Second-largest table (~1.3 million/year).
-- deviation_from_standard_pct is a mechanically independent degradation
-- signal from vibration. No business code.
-- ---------------------------------------------------------------------
CREATE TABLE cycle_history (
    cycle_history_id             INTEGER      NOT NULL
        CONSTRAINT pk_cycle_history PRIMARY KEY AUTOINCREMENT,
    machine_id                   INTEGER      NOT NULL,
    production_run_id            INTEGER      NOT NULL,
    cycle_number_in_run          INTEGER      NOT NULL,
    cycle_started_at             DATETIME     NOT NULL,
    cycle_ended_at               DATETIME     NOT NULL,
    cycle_time_seconds           NUMERIC(8,2) NOT NULL,
    deviation_from_standard_pct  NUMERIC(6,2) NULL,
    outcome                      TEXT         NOT NULL,
    interrupted                  INTEGER      NOT NULL DEFAULT 0,
    shift_id                     INTEGER      NOT NULL,
    sequence_number              INTEGER      NOT NULL,
    created_at                   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component         TEXT         NOT NULL,

    CONSTRAINT uq_ch_machine_run_cycle
        UNIQUE (machine_id, production_run_id, cycle_number_in_run),
    CONSTRAINT uq_ch_machine_sequence
        UNIQUE (machine_id, sequence_number),

    CONSTRAINT ck_ch_cycle_window_ordered
        CHECK (cycle_ended_at > cycle_started_at),
    CONSTRAINT ck_ch_cycle_time_positive
        CHECK (cycle_time_seconds > 0),
    CONSTRAINT ck_ch_cycle_number_positive
        CHECK (cycle_number_in_run > 0),
    CONSTRAINT ck_ch_sequence_number_positive
        CHECK (sequence_number > 0),
    -- Enforces the exclusion rule structurally rather than trusting every
    -- consumer to remember it: an interrupted cycle has a meaningless
    -- duration and must carry no deviation figure.
    CONSTRAINT ck_ch_interrupted_has_no_deviation
        CHECK (interrupted = 0 OR deviation_from_standard_pct IS NULL),
    CONSTRAINT ck_ch_outcome_allowed
        CHECK (outcome IN ('good', 'scrap', 'rework')),
    CONSTRAINT ck_ch_interrupted_bool
        CHECK (interrupted IN (0, 1)),
    CONSTRAINT ck_ch_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_ch_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ch_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ch_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- cycle_time_seconds must equal the interval between the two timestamps.
-- That is recorded as a reconciliation check rather than a per-row CHECK,
-- to avoid an interval computation on the second-highest-volume table.
-- This is the ONLY place in the schema where enforcement immediacy is
-- traded for insert throughput (spec O7 notes, section 41.6).

-- ---------------------------------------------------------------------
-- O15. prediction_feature_snapshot
-- The exact feature vector used for one inference. The ML reproducibility
-- contract. Recording an INSUFFICIENT snapshot rather than silently
-- skipping is what makes the absence of a prediction explicable.
-- ---------------------------------------------------------------------
CREATE TABLE prediction_feature_snapshot (
    prediction_feature_snapshot_id    INTEGER      NOT NULL
        CONSTRAINT pk_prediction_feature_snapshot PRIMARY KEY AUTOINCREMENT,
    prediction_feature_snapshot_code  VARCHAR(22)  NOT NULL,
    machine_id                        INTEGER      NOT NULL,
    generated_at                      DATETIME     NOT NULL,
    window_from                       DATETIME     NOT NULL,
    window_to                         DATETIME     NOT NULL,
    lookback_window_seconds           INTEGER      NOT NULL,
    feature_set_version               VARCHAR(20)  NOT NULL,
    feature_values                    TEXT         NOT NULL,
    source_reading_count              INTEGER      NOT NULL,
    excluded_reading_count            INTEGER      NOT NULL DEFAULT 0,
    data_completeness_pct             NUMERIC(5,2) NOT NULL,
    is_sufficient_for_inference       INTEGER      NOT NULL,
    insufficiency_reason              TEXT         NULL,
    triggering_alert_id               INTEGER      NULL,
    shift_id                          INTEGER      NOT NULL,
    created_at                        DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component              TEXT         NOT NULL,

    CONSTRAINT uq_pfs_code UNIQUE (prediction_feature_snapshot_code),

    CONSTRAINT ck_pfs_code_format
        CHECK (prediction_feature_snapshot_code
               GLOB 'FSN-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_pfs_window_ordered
        CHECK (window_to > window_from),
    CONSTRAINT ck_pfs_window_to_equals_generated
        CHECK (window_to = generated_at),
    CONSTRAINT ck_pfs_lookback_positive
        CHECK (lookback_window_seconds > 0),
    CONSTRAINT ck_pfs_counts_non_negative
        CHECK (source_reading_count >= 0 AND excluded_reading_count >= 0),
    CONSTRAINT ck_pfs_completeness_range
        CHECK (data_completeness_pct BETWEEN 0 AND 100),
    -- An insufficient snapshot must state why.
    CONSTRAINT ck_pfs_insufficiency_reason_required
        CHECK (is_sufficient_for_inference = 1
               OR insufficiency_reason IS NOT NULL),
    CONSTRAINT ck_pfs_sufficient_has_no_reason
        CHECK (is_sufficient_for_inference = 0
               OR insufficiency_reason IS NULL),
    -- Rejects malformed JSON and rejects a scalar or array where an object
    -- is required (spec section 39.3).
    CONSTRAINT ck_pfs_feature_values_is_object
        CHECK (json_valid(feature_values)
               AND json_type(feature_values) = 'object'),
    CONSTRAINT ck_pfs_feature_set_version_not_blank
        CHECK (length(trim(feature_set_version)) > 0),
    CONSTRAINT ck_pfs_insufficiency_reason_allowed
        CHECK (insufficiency_reason IS NULL
               OR insufficiency_reason IN ('completeness_below_threshold',
                                           'sensor_fault', 'machine_not_running',
                                           'window_spans_maintenance',
                                           'insufficient_history')),
    -- No default: a judgement the writing component must make.
    CONSTRAINT ck_pfs_is_sufficient_for_inference_bool
        CHECK (is_sufficient_for_inference IN (0, 1)),
    CONSTRAINT ck_pfs_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_pfs_prediction_feature_snapshot_code_length
        CHECK (length(prediction_feature_snapshot_code) <= 22),
    CONSTRAINT ck_pfs_feature_set_version_length
        CHECK (length(feature_set_version) <= 20),

    CONSTRAINT fk_pfs_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pfs_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pfs_triggering_alert FOREIGN KEY (triggering_alert_id)
        REFERENCES operational_alert (operational_alert_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Full schema validation of feature_values is an application
-- responsibility, since the expected keys depend on feature_set_version.

-- =====================================================================
-- OPERATIONAL GROUP - LAYER 2
-- =====================================================================

-- ---------------------------------------------------------------------
-- O13. operational_event
-- One detected condition as an immutable fact, with the evidence that
-- produced it. The evidentiary base of every recommendation.
--
-- threshold_value_breached is THE ONLY PLACE IN THE ENTIRE DATABASE where
-- a master value is copied. An event is quoted into LLM prompts and
-- notification bodies and must be readable as self-contained evidence: the
-- rule is referenced for lineage, the value is captured for evidence
-- (spec O13, operational document section 3.5).
--
-- Four nullable TYPED subject keys rather than a polymorphic
-- scope_type/scope_ref pair: the extra columns buy full referential
-- integrity, which a generic reference cannot have.
-- ---------------------------------------------------------------------
CREATE TABLE operational_event (
    operational_event_id        INTEGER       NOT NULL
        CONSTRAINT pk_operational_event PRIMARY KEY AUTOINCREMENT,
    operational_event_code      VARCHAR(20)   NOT NULL,
    operational_alert_id        INTEGER       NOT NULL,
    event_category              TEXT          NOT NULL,
    event_type                  TEXT          NOT NULL,
    detected_at                 DATETIME      NOT NULL,
    severity_level_id           INTEGER       NOT NULL,
    machine_id                  INTEGER       NULL,
    production_line_id          INTEGER       NULL,
    production_run_id           INTEGER       NULL,
    inventory_item_id           INTEGER       NULL,
    machine_parameter_id        INTEGER       NULL,
    alert_threshold_rule_id     INTEGER       NULL,
    observed_value              NUMERIC(12,4) NULL,
    threshold_value_breached    NUMERIC(12,4) NULL,
    threshold_direction         TEXT          NULL,
    sustained_duration_seconds  INTEGER       NULL,
    triggering_reading_id       INTEGER       NULL,
    shift_id                    INTEGER       NOT NULL,
    detection_note              TEXT          NULL,
    created_at                  DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component        TEXT          NOT NULL,

    CONSTRAINT uq_oe_code UNIQUE (operational_event_code),

    CONSTRAINT ck_oe_code_format
        CHECK (operational_event_code
               GLOB 'EVT-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_oe_machine_subject_required
        CHECK (event_category NOT IN ('machine_condition', 'machine_output',
                                      'data_quality')
               OR machine_id IS NOT NULL),
    CONSTRAINT ck_oe_inventory_subject_required
        CHECK (event_category <> 'inventory' OR inventory_item_id IS NOT NULL),
    CONSTRAINT ck_oe_quality_subject_required
        CHECK (event_category <> 'quality'
               OR (machine_id IS NOT NULL AND production_run_id IS NOT NULL)),
    -- An event claiming a breach without stating what was breached is not
    -- evidence. The most valuable constraint on this table.
    CONSTRAINT ck_oe_threshold_event_complete
        CHECK (event_type NOT IN ('threshold_warning', 'threshold_critical',
                                  'rate_of_change_exceeded')
               OR (machine_parameter_id         IS NOT NULL
                   AND alert_threshold_rule_id  IS NOT NULL
                   AND observed_value           IS NOT NULL
                   AND threshold_value_breached IS NOT NULL
                   AND threshold_direction      IS NOT NULL)),
    CONSTRAINT ck_oe_sustained_duration_non_negative
        CHECK (sustained_duration_seconds IS NULL
               OR sustained_duration_seconds >= 0),
    CONSTRAINT ck_oe_event_category_allowed
        CHECK (event_category IN ('machine_condition', 'machine_output',
                                  'quality', 'inventory', 'data_quality')),
    CONSTRAINT ck_oe_event_type_allowed
        CHECK (event_type IN ('threshold_warning', 'threshold_critical',
                              'rate_of_change_exceeded', 'sustained_deviation',
                              'output_shortfall', 'cycle_deviation',
                              'scrap_rate_exceeded', 'quality_failure_rate',
                              'reorder_point_reached', 'safety_stock_breached',
                              'sensor_out_of_range', 'telemetry_stale')),
    CONSTRAINT ck_oe_threshold_direction_allowed
        CHECK (threshold_direction IS NULL
               OR threshold_direction IN ('above_high', 'below_low',
                                          'rate_exceeded')),
    CONSTRAINT ck_oe_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_oe_operational_event_code_length
        CHECK (length(operational_event_code) <= 20),

    CONSTRAINT fk_oe_alert FOREIGN KEY (operational_alert_id)
        REFERENCES operational_alert (operational_alert_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_severity FOREIGN KEY (severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_inventory_item FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_machine_parameter FOREIGN KEY (machine_parameter_id)
        REFERENCES machine_parameter (machine_parameter_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_threshold_rule FOREIGN KEY (alert_threshold_rule_id)
        REFERENCES alert_threshold_rule (alert_threshold_rule_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_oe_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    -- THE SINGLE FOREIGN KEY IN THIS DATABASE THAT IS NOT ON DELETE
    -- RESTRICT. Telemetry is purged at 90 days; events are retained a year
    -- or more. RESTRICT would either pin individual readings in otherwise
    -- purgeable ranges or fail the purge outright. SET NULL is safe ONLY
    -- because observed_value, threshold_value_breached, and detected_at are
    -- captured onto the event itself: when the reading is purged the
    -- lineage pointer becomes NULL and the evidence survives intact
    -- (spec sections 32.3, 48.4).
    CONSTRAINT fk_oe_triggering_reading FOREIGN KEY (triggering_reading_id)
        REFERENCES machine_sensor_reading (machine_sensor_reading_id)
        ON DELETE SET NULL ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- O16. prediction_result
-- One model inference. THE SOLE ORIGIN OF THE ML CONFIDENCE FIGURE:
-- failure_probability is created here and nowhere else, because an LLM
-- restating a probability would quietly lose calibration
-- (PROJECT_OVERVIEW.md section 16.5).
-- ---------------------------------------------------------------------
CREATE TABLE prediction_result (
    prediction_result_id            INTEGER      NOT NULL
        CONSTRAINT pk_prediction_result PRIMARY KEY AUTOINCREMENT,
    prediction_result_code          VARCHAR(20)  NOT NULL,
    prediction_feature_snapshot_id  INTEGER      NOT NULL,
    machine_id                      INTEGER      NOT NULL,
    predicted_at                    DATETIME     NOT NULL,
    model_name                      VARCHAR(60)  NOT NULL,
    model_version                   VARCHAR(20)  NOT NULL,
    failure_probability             NUMERIC(5,4) NOT NULL,
    risk_severity_level_id          INTEGER      NOT NULL,
    predicted_failure_category_id   INTEGER      NULL,
    machine_type_failure_mode_id    INTEGER      NULL,
    prediction_horizon_hours        INTEGER      NOT NULL,
    confidence_band_low             NUMERIC(5,4) NULL,
    confidence_band_high            NUMERIC(5,4) NULL,
    top_contributing_features       TEXT         NOT NULL,
    triggering_alert_id             INTEGER      NULL,
    inference_duration_ms           INTEGER      NOT NULL,
    shift_id                        INTEGER      NOT NULL,
    created_at                      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component            TEXT         NOT NULL,

    CONSTRAINT uq_pr_code UNIQUE (prediction_result_code),

    -- PDN- rather than PRD- to avoid collision with master product codes.
    CONSTRAINT ck_pr_code_format
        CHECK (prediction_result_code
               GLOB 'PDN-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    -- NUMERIC(5,4) permits up to 9.9999, so this range check is
    -- load-bearing rather than decorative: without it a probability of 3.5
    -- would propagate into a recommendation as ML confidence.
    CONSTRAINT ck_pr_probability_range
        CHECK (failure_probability BETWEEN 0 AND 1),
    CONSTRAINT ck_pr_confidence_band_ordered
        CHECK ((confidence_band_low IS NULL AND confidence_band_high IS NULL)
               OR (confidence_band_low <= failure_probability
                   AND failure_probability <= confidence_band_high)),
    CONSTRAINT ck_pr_confidence_band_range
        CHECK ((confidence_band_low  IS NULL OR confidence_band_low  BETWEEN 0 AND 1)
               AND (confidence_band_high IS NULL OR confidence_band_high BETWEEN 0 AND 1)),
    CONSTRAINT ck_pr_confidence_band_paired
        CHECK ((confidence_band_low IS NULL) = (confidence_band_high IS NULL)),
    CONSTRAINT ck_pr_horizon_positive
        CHECK (prediction_horizon_hours > 0),
    CONSTRAINT ck_pr_inference_duration_non_negative
        CHECK (inference_duration_ms >= 0),
    -- Object or array, not a scalar (spec section 39.3).
    CONSTRAINT ck_pr_top_features_is_object
        CHECK (json_valid(top_contributing_features)
               AND json_type(top_contributing_features) IN ('object', 'array')),
    CONSTRAINT ck_pr_model_version_not_blank
        CHECK (length(trim(model_version)) > 0),
    CONSTRAINT ck_pr_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_pr_prediction_result_code_length
        CHECK (length(prediction_result_code) <= 20),
    CONSTRAINT ck_pr_model_name_length
        CHECK (length(model_name) <= 60),
    CONSTRAINT ck_pr_model_version_length
        CHECK (length(model_version) <= 20),

    CONSTRAINT fk_pr_snapshot FOREIGN KEY (prediction_feature_snapshot_id)
        REFERENCES prediction_feature_snapshot (prediction_feature_snapshot_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_risk_severity FOREIGN KEY (risk_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_failure_category FOREIGN KEY (predicted_failure_category_id)
        REFERENCES failure_category (failure_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_failure_mode FOREIGN KEY (machine_type_failure_mode_id)
        REFERENCES machine_type_failure_mode (machine_type_failure_mode_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_pr_triggering_alert FOREIGN KEY (triggering_alert_id)
        REFERENCES operational_alert (operational_alert_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Two rules central to the platform's honesty are cross-table and
-- therefore application-validated (spec section 41.3):
--   1. A prediction may only reference a snapshot with
--      is_sufficient_for_inference = 1. A CHECK cannot read the parent row,
--      and scoring on inadequate data returns a confident number with no
--      basis.
--   2. machine_type_failure_mode_id must reference a mode with
--      is_model_predictable = 1, and prediction_horizon_hours should not
--      exceed that mode's typical_warning_period_hours. Predicting further
--      ahead than the physics gives warning for is not a forecast.

-- =====================================================================
-- OPERATIONAL GROUP - LAYER 3
-- =====================================================================

-- ---------------------------------------------------------------------
-- O8. quality_inspection_result
-- The outcome of a quality inspection against sampled output, including
-- which machine the defect is attributed to.
--
-- machine_id and attributed_machine_id are DIFFERENT COLUMNS. The
-- inspection happens at a station; the defect was caused somewhere else.
-- Without the separation, quality data would blame the inspection station
-- for every defect it discovered (spec O8).
--
-- disposition is deliberately separate from the finding: a failed
-- inspection does not automatically mean scrap. A finding is not a
-- disposition.
--
-- No quality specification limits: this table records pass and fail counts
-- as judged, not the limits used to judge them.
-- ---------------------------------------------------------------------
CREATE TABLE quality_inspection_result (
    quality_inspection_result_id    INTEGER     NOT NULL
        CONSTRAINT pk_quality_inspection_result PRIMARY KEY AUTOINCREMENT,
    quality_inspection_result_code  VARCHAR(20) NOT NULL,
    production_run_id               INTEGER     NOT NULL,
    machine_id                      INTEGER     NULL,
    attributed_machine_id           INTEGER     NULL,
    attributed_failure_category_id  INTEGER     NULL,
    inspected_at                    DATETIME    NOT NULL,
    inspection_type                 TEXT        NOT NULL,
    sample_size                     INTEGER     NOT NULL,
    pass_count                      INTEGER     NOT NULL,
    fail_count                      INTEGER     NOT NULL,
    inspector_worker_id             INTEGER     NOT NULL,
    disposition                     TEXT        NOT NULL,
    primary_defect_note             TEXT        NULL,
    related_operational_event_id    INTEGER     NULL,
    shift_id                        INTEGER     NOT NULL,
    created_at                      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component            TEXT        NOT NULL,

    CONSTRAINT uq_qir_code UNIQUE (quality_inspection_result_code),

    CONSTRAINT ck_qir_code_format
        CHECK (quality_inspection_result_code
               GLOB 'QIR-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_qir_sample_size_positive
        CHECK (sample_size > 0),
    CONSTRAINT ck_qir_counts_non_negative
        CHECK (pass_count >= 0 AND fail_count >= 0),
    -- Self-checking: the parts add up or the row is rejected.
    CONSTRAINT ck_qir_counts_sum_to_sample
        CHECK (pass_count + fail_count = sample_size),
    -- A recorded failure with no description is not usable evidence.
    CONSTRAINT ck_qir_defect_note_required
        CHECK (fail_count = 0 OR primary_defect_note IS NOT NULL),
    -- Attributing a defect to a machine without naming a mechanism is half
    -- an inference. This constraint keeps the machine-to-quality inference
    -- honest: attribution is either fully usable or explicitly absent.
    CONSTRAINT ck_qir_attribution_paired
        CHECK ((attributed_machine_id IS NULL
                AND attributed_failure_category_id IS NULL)
               OR (attributed_machine_id IS NOT NULL
                   AND attributed_failure_category_id IS NOT NULL)),
    CONSTRAINT ck_qir_inspection_type_allowed
        CHECK (inspection_type IN ('first_article', 'in_process', 'final',
                                   'audit')),
    CONSTRAINT ck_qir_disposition_allowed
        CHECK (disposition IN ('accept', 'rework', 'scrap', 'quarantine')),
    CONSTRAINT ck_qir_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_qir_quality_inspection_result_code_length
        CHECK (length(quality_inspection_result_code) <= 20),

    CONSTRAINT fk_qir_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_qir_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_qir_attributed_machine FOREIGN KEY (attributed_machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_qir_attributed_failure_category
        FOREIGN KEY (attributed_failure_category_id)
        REFERENCES failure_category (failure_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_qir_inspector FOREIGN KEY (inspector_worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_qir_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_qir_related_event FOREIGN KEY (related_operational_event_id)
        REFERENCES operational_event (operational_event_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- The double reference to machine in two distinct roles creates no cycle:
-- machine is master data and references nothing operational.
-- The rule that attributed_machine_id must be on the same production line
-- as the run is cross-table and application-validated. A defect cannot be
-- attributed to a machine that never touched the part.

-- ---------------------------------------------------------------------
-- O17. supervisor_context
-- The Supervisor Agent's escalation decision and the context package
-- assembled for it. The audit trail of the platform's cost and noise gate.
--
-- A row is written EITHER WAY - escalated or suppressed. With suppressions
-- recorded, "the machine was showing symptoms, why didn't the system tell
-- me?" has an answer that points at the threshold, not at the platform.
--
-- context_document is preserved EXACTLY as the Decision Agent received it.
-- Reassembling it at audit time would reflect current data rather than what
-- was known at the decision moment. This is the input side of the
-- explainability contract (spec O17).
--
-- No business rule values are copied: applied_escalation_rule_id references
-- the governing business_rule row and nothing more.
-- ---------------------------------------------------------------------
CREATE TABLE supervisor_context (
    supervisor_context_id         INTEGER     NOT NULL
        CONSTRAINT pk_supervisor_context PRIMARY KEY AUTOINCREMENT,
    supervisor_context_code       VARCHAR(20) NOT NULL,
    machine_id                    INTEGER     NULL,
    production_line_id            INTEGER     NULL,
    assembled_at                  DATETIME    NOT NULL,
    triggering_alert_id           INTEGER     NOT NULL,
    triggering_prediction_id      INTEGER     NULL,
    related_alert_codes           TEXT        NULL,
    escalation_decision           TEXT        NOT NULL,
    applied_escalation_rule_id    INTEGER     NULL,
    escalation_rationale          TEXT        NOT NULL,
    context_document              TEXT        NULL,
    context_assembly_duration_ms  INTEGER     NOT NULL,
    shift_id                      INTEGER     NOT NULL,
    created_at                    DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component          TEXT        NOT NULL,

    CONSTRAINT uq_sc_code UNIQUE (supervisor_context_code),

    CONSTRAINT ck_sc_code_format
        CHECK (supervisor_context_code
               GLOB 'CTX-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    -- Read the next two constraints together: they make context_document
    -- present exactly when escalated and absent otherwise, which is both the
    -- correctness rule and the cost-control rule.
    CONSTRAINT ck_sc_escalated_requires_context
        CHECK (escalation_decision <> 'escalated'
               OR context_document IS NOT NULL),
    CONSTRAINT ck_sc_suppressed_has_no_context
        CHECK (escalation_decision = 'escalated'
               OR context_document IS NULL),
    -- Both verdicts turn on a threshold and both must name it.
    CONSTRAINT ck_sc_threshold_decisions_name_rule
        CHECK (escalation_decision NOT IN ('escalated',
                                           'suppressed_below_threshold')
               OR applied_escalation_rule_id IS NOT NULL),
    CONSTRAINT ck_sc_rationale_not_blank
        CHECK (length(trim(escalation_rationale)) > 0),
    CONSTRAINT ck_sc_assembly_duration_non_negative
        CHECK (context_assembly_duration_ms >= 0),
    CONSTRAINT ck_sc_context_document_is_object
        CHECK (context_document IS NULL
               OR (json_valid(context_document)
                   AND json_type(context_document) = 'object')),
    CONSTRAINT ck_sc_related_alerts_is_array
        CHECK (related_alert_codes IS NULL
               OR (json_valid(related_alert_codes)
                   AND json_type(related_alert_codes) = 'array')),
    -- Suppression reasons are enumerated so silence is always explicable.
    CONSTRAINT ck_sc_escalation_decision_allowed
        CHECK (escalation_decision IN ('escalated',
                                       'suppressed_below_threshold',
                                       'suppressed_duplicate',
                                       'suppressed_maintenance_in_progress',
                                       'suppressed_rate_limited',
                                       'suppressed_insufficient_data')),
    CONSTRAINT ck_sc_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_sc_supervisor_context_code_length
        CHECK (length(supervisor_context_code) <= 20),

    CONSTRAINT fk_sc_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sc_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sc_escalation_rule FOREIGN KEY (applied_escalation_rule_id)
        REFERENCES business_rule (business_rule_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sc_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sc_triggering_alert FOREIGN KEY (triggering_alert_id)
        REFERENCES operational_alert (operational_alert_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sc_triggering_prediction FOREIGN KEY (triggering_prediction_id)
        REFERENCES prediction_result (prediction_result_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Retention is SPLIT BY DECISION and is the only table whose retention
-- window depends on a column value: escalated contexts 2 years (effective
-- 5-year floor via spec section 13.4), suppressed contexts 180 days.

-- =====================================================================
-- OPERATIONAL GROUP - LAYER 4
-- =====================================================================

-- ---------------------------------------------------------------------
-- O9. scrap_record
-- Units scrapped, with quantity, reason, and attribution. A DISPOSITION,
-- NOT A FINDING: O8 records that a part failed inspection, this records
-- that material was written off. Not every failure becomes scrap, and
-- conflating them would systematically overstate material loss (spec O9).
--
-- No material_cost_impact column. Scrap cost is computed at read time as
-- quantity x product.standard_material_cost. Storing a captured cost would
-- extend the single permitted master-value-copy exception to a second place
-- with a much weaker justification than the threshold capture in O13.
--
-- No business code: scrap is analysed in aggregate and individual records
-- are not named on the shop floor.
-- ---------------------------------------------------------------------
CREATE TABLE scrap_record (
    scrap_record_id                 INTEGER       NOT NULL
        CONSTRAINT pk_scrap_record PRIMARY KEY AUTOINCREMENT,
    production_run_id               INTEGER       NOT NULL,
    machine_id                      INTEGER       NOT NULL,
    attributed_machine_id           INTEGER       NULL,
    attributed_failure_category_id  INTEGER       NULL,
    recorded_at                     DATETIME      NOT NULL,
    quantity_units                  NUMERIC(12,2) NOT NULL,
    scrap_reason                    TEXT          NOT NULL,
    quality_inspection_result_id    INTEGER       NULL,
    related_operational_event_id    INTEGER       NULL,
    recorded_by_worker_id           INTEGER       NOT NULL,
    shift_id                        INTEGER       NOT NULL,
    notes                           TEXT          NULL,
    created_at                      DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component            TEXT          NOT NULL,

    CONSTRAINT ck_sr_quantity_positive
        CHECK (quantity_units > 0),
    CONSTRAINT ck_sr_attribution_paired
        CHECK ((attributed_machine_id IS NULL
                AND attributed_failure_category_id IS NULL)
               OR (attributed_machine_id IS NOT NULL
                   AND attributed_failure_category_id IS NOT NULL)),
    -- The next two constraints work in OPPOSITE DIRECTIONS and are the most
    -- valuable on this table. Together they make the preventable-loss metric
    -- trustworthy by construction.
    -- Prevents UNDER-attribution, which would understate preventable loss:
    CONSTRAINT ck_sr_machine_fault_requires_attribution
        CHECK (scrap_reason <> 'machine_fault'
               OR (attributed_machine_id IS NOT NULL
                   AND attributed_failure_category_id IS NOT NULL)),
    -- Prevents OVER-attribution, which would inflate a machine's figure with
    -- a supplier's casting flaw and eventually drive a wrong decision:
    CONSTRAINT ck_sr_non_machine_reasons_unattributed
        CHECK (scrap_reason NOT IN ('material_defect', 'handling_damage')
               OR (attributed_machine_id IS NULL
                   AND attributed_failure_category_id IS NULL)),
    CONSTRAINT ck_sr_scrap_reason_allowed
        CHECK (scrap_reason IN ('dimensional_deviation', 'surface_defect',
                                'tool_mark', 'material_defect',
                                'setup_reject', 'machine_fault',
                                'handling_damage', 'process_deviation')),
    CONSTRAINT ck_sr_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_sr_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sr_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sr_attributed_machine FOREIGN KEY (attributed_machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sr_attributed_failure_category
        FOREIGN KEY (attributed_failure_category_id)
        REFERENCES failure_category (failure_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sr_inspection_result FOREIGN KEY (quality_inspection_result_id)
        REFERENCES quality_inspection_result (quality_inspection_result_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sr_related_event FOREIGN KEY (related_operational_event_id)
        REFERENCES operational_event (operational_event_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sr_recorded_by FOREIGN KEY (recorded_by_worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_sr_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- A reversal is a compensating record, never an edit.

-- ---------------------------------------------------------------------
-- O18. ai_recommendation
-- The Decision Agent's output: an explainable, business-aware
-- recommendation implementing every element of the PROJECT_OVERVIEW.md
-- section 16.5 contract. THE PLATFORM'S ACTUAL PRODUCT. Everything
-- upstream exists to produce this row.
--
-- THIS TABLE HAS NO failure_probability COLUMN, AND THAT ABSENCE IS THE
-- POINT. The overview requires ML confidence in every recommendation and
-- requires that it originate from the Prediction Agent and never be
-- restated by the LLM. This schema enforces it STRUCTURALLY:
-- prediction_result_id is NOT NULL and THERE IS NOWHERE TO WRITE A
-- PROBABILITY. The Decision Agent cannot invent, round, or adjust the
-- number because no column accepts one. A convention would have been
-- circumvented eventually; a missing column cannot be.
--
-- root_cause_failure_category_id is NOT NULL and references the twelve-value
-- controlled vocabulary in failure_category. The LLM CLASSIFIES WITHIN A
-- VALIDATED SET rather than generating free-form causes.
--
-- DO NOT ADD A PROBABILITY COLUMN TO THIS TABLE.
-- ---------------------------------------------------------------------
CREATE TABLE ai_recommendation (
    ai_recommendation_id            INTEGER     NOT NULL
        CONSTRAINT pk_ai_recommendation PRIMARY KEY AUTOINCREMENT,
    ai_recommendation_code          VARCHAR(20) NOT NULL,
    supervisor_context_id           INTEGER     NOT NULL,
    prediction_result_id            INTEGER     NOT NULL,
    machine_id                      INTEGER     NOT NULL,
    production_line_id              INTEGER     NOT NULL,
    production_run_id               INTEGER     NULL,
    generated_at                    DATETIME    NOT NULL,
    llm_model_name                  VARCHAR(60) NOT NULL,
    llm_model_version               VARCHAR(40) NOT NULL,
    priority_severity_level_id      INTEGER     NOT NULL,
    root_cause_failure_category_id  INTEGER     NOT NULL,
    root_cause_confidence           TEXT        NOT NULL,
    supporting_evidence             TEXT        NOT NULL,
    business_impact                 TEXT        NOT NULL,
    recommended_action              TEXT        NOT NULL,
    recovery_plan                   TEXT        NOT NULL,
    suggested_maintenance_team_id   INTEGER     NULL,
    suggested_engineer_id           INTEGER     NULL,
    required_inventory_item_id      INTEGER     NULL,
    estimated_downtime_minutes      INTEGER     NULL,
    recommended_action_by           DATETIME    NULL,
    reasoning_narrative             TEXT        NOT NULL,
    contract_complete               INTEGER     NOT NULL,
    generation_duration_ms          INTEGER     NOT NULL,
    prompt_token_count              INTEGER     NULL,
    completion_token_count          INTEGER     NULL,
    shift_id                        INTEGER     NOT NULL,
    created_at                      DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component            TEXT        NOT NULL,

    CONSTRAINT uq_ar_code UNIQUE (ai_recommendation_code),
    -- One recommendation per escalated context. Enforces the one-to-one and
    -- prevents duplicate reasoning on the same package - belt and braces on
    -- the platform's most expensive operation.
    CONSTRAINT uq_ar_supervisor_context UNIQUE (supervisor_context_id),

    CONSTRAINT ck_ar_code_format
        CHECK (ai_recommendation_code
               GLOB 'REC-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_ar_recommended_action_not_blank
        CHECK (length(trim(recommended_action)) > 0),
    CONSTRAINT ck_ar_recovery_plan_not_blank
        CHECK (length(trim(recovery_plan)) > 0),
    CONSTRAINT ck_ar_reasoning_narrative_not_blank
        CHECK (length(trim(reasoning_narrative)) > 0),
    CONSTRAINT ck_ar_supporting_evidence_is_object
        CHECK (json_valid(supporting_evidence)
               AND json_type(supporting_evidence) = 'object'),
    CONSTRAINT ck_ar_business_impact_is_object
        CHECK (json_valid(business_impact)
               AND json_type(business_impact) = 'object'),
    CONSTRAINT ck_ar_action_deadline_after_generation
        CHECK (recommended_action_by IS NULL
               OR recommended_action_by > generated_at),
    CONSTRAINT ck_ar_estimated_downtime_positive
        CHECK (estimated_downtime_minutes IS NULL
               OR estimated_downtime_minutes > 0),
    CONSTRAINT ck_ar_generation_duration_non_negative
        CHECK (generation_duration_ms >= 0),
    CONSTRAINT ck_ar_token_counts_non_negative
        CHECK ((prompt_token_count IS NULL OR prompt_token_count >= 0)
               AND (completion_token_count IS NULL
                    OR completion_token_count >= 0)),
    CONSTRAINT ck_ar_root_cause_confidence_allowed
        CHECK (root_cause_confidence IN ('high', 'moderate', 'low')),
    CONSTRAINT ck_ar_contract_complete_bool
        CHECK (contract_complete IN (0, 1)),
    CONSTRAINT ck_ar_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_ar_ai_recommendation_code_length
        CHECK (length(ai_recommendation_code) <= 20),
    CONSTRAINT ck_ar_llm_model_name_length
        CHECK (length(llm_model_name) <= 60),
    CONSTRAINT ck_ar_llm_model_version_length
        CHECK (length(llm_model_version) <= 40),

    CONSTRAINT fk_ar_supervisor_context FOREIGN KEY (supervisor_context_id)
        REFERENCES supervisor_context (supervisor_context_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    -- The ML confidence element, BY REFERENCE. NOT NULL is what makes the
    -- probability unforgeable.
    CONSTRAINT fk_ar_prediction_result FOREIGN KEY (prediction_result_id)
        REFERENCES prediction_result (prediction_result_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_production_line FOREIGN KEY (production_line_id)
        REFERENCES production_line (production_line_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_priority_severity FOREIGN KEY (priority_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_root_cause_category
        FOREIGN KEY (root_cause_failure_category_id)
        REFERENCES failure_category (failure_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_suggested_team FOREIGN KEY (suggested_maintenance_team_id)
        REFERENCES maintenance_team (maintenance_team_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_suggested_engineer FOREIGN KEY (suggested_engineer_id)
        REFERENCES maintenance_engineer (maintenance_engineer_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_required_item FOREIGN KEY (required_inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ar_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Immutable: the human response lives in recommendation_action precisely so
-- the recommendation stays untouched. An editable recommendation could be
-- quietly improved after the fact, destroying the audit trail.
-- Not enforceable here, listed in spec section 41.3: root_cause_confidence
-- = 'high' requires supporting_evidence to contain findings from at least
-- two independent measurement paths; required_inventory_item_id must derive
-- from the predicted failure mode's required part; suggested_engineer_id
-- must belong to the suggested team with valid certification.

-- =====================================================================
-- OPERATIONAL GROUP - LAYER 5
-- =====================================================================

-- ---------------------------------------------------------------------
-- O20. notification
-- One message composed for one recipient, with the decision of whether to
-- send it. Records what the platform said, to whom, and - when it stayed
-- silent - why.
--
-- SUPPRESSION IS RECORDED AS A ROW, NOT AS AN ABSENCE. Without this,
-- "was the supervisor told?" would be answered by the absence of a row, and
-- absence is ambiguous between deliberately suppressed, never composed, and
-- lost to a bug. Suppression stops transmission, never recording (spec O20).
--
-- The message body is STORED, NOT REGENERATED: what the recipient actually
-- saw is part of the audit trail.
--
-- acknowledgement_deadline_at is resolved and stored at composition from the
-- severity's max_acknowledgement_minutes. A clock whose deadline is
-- recomputed on every check is a clock that can drift.
--
-- No contact details are stored here; endpoints resolve through
-- notification_recipient_id to worker.
-- ---------------------------------------------------------------------
CREATE TABLE notification (
    notification_id              INTEGER      NOT NULL
        CONSTRAINT pk_notification PRIMARY KEY AUTOINCREMENT,
    notification_code            VARCHAR(22)  NOT NULL,
    notification_recipient_id    INTEGER      NOT NULL,
    notification_type            TEXT         NOT NULL,
    ai_recommendation_id         INTEGER      NULL,
    operational_alert_id         INTEGER      NULL,
    severity_level_id            INTEGER      NOT NULL,
    composed_at                  DATETIME     NOT NULL,
    subject                      VARCHAR(200) NOT NULL,
    body_text                    TEXT         NOT NULL,
    is_suppressed                INTEGER      NOT NULL DEFAULT 0,
    suppression_reason           TEXT         NULL,
    requires_acknowledgement     INTEGER      NOT NULL,
    acknowledgement_deadline_at  DATETIME     NULL,
    escalation_order_applied     INTEGER      NOT NULL,
    shift_id                     INTEGER      NOT NULL,
    created_at                   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component         TEXT         NOT NULL,

    CONSTRAINT uq_nt_code UNIQUE (notification_code),

    CONSTRAINT ck_nt_code_format
        CHECK (notification_code
               GLOB 'NTF-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_nt_recommendation_required
        CHECK (notification_type <> 'recommendation'
               OR ai_recommendation_id IS NOT NULL),
    -- The next two make suppression_reason present exactly when suppressed.
    CONSTRAINT ck_nt_suppression_reason_required
        CHECK (is_suppressed = 0 OR suppression_reason IS NOT NULL),
    CONSTRAINT ck_nt_suppression_reason_absent
        CHECK (is_suppressed = 1 OR suppression_reason IS NULL),
    CONSTRAINT ck_nt_ack_deadline_required
        CHECK (requires_acknowledgement = 0
               OR acknowledgement_deadline_at IS NOT NULL),
    CONSTRAINT ck_nt_ack_deadline_after_composed
        CHECK (acknowledgement_deadline_at IS NULL
               OR acknowledgement_deadline_at > composed_at),
    CONSTRAINT ck_nt_escalation_order_positive
        CHECK (escalation_order_applied > 0),
    CONSTRAINT ck_nt_subject_not_blank
        CHECK (length(trim(subject)) > 0),
    CONSTRAINT ck_nt_body_not_blank
        CHECK (length(trim(body_text)) > 0),
    CONSTRAINT ck_nt_notification_type_allowed
        CHECK (notification_type IN ('recommendation', 'alert_escalation',
                                     'acknowledgement_reminder',
                                     'inventory_warning', 'system_health')),
    CONSTRAINT ck_nt_suppression_reason_allowed
        CHECK (suppression_reason IS NULL
               OR suppression_reason IN ('quiet_hours', 'rate_limited',
                                         'below_min_severity',
                                         'recipient_inactive',
                                         'channel_unavailable',
                                         'already_acknowledged')),
    CONSTRAINT ck_nt_is_suppressed_bool
        CHECK (is_suppressed IN (0, 1)),
    CONSTRAINT ck_nt_requires_acknowledgement_bool
        CHECK (requires_acknowledgement IN (0, 1)),
    CONSTRAINT ck_nt_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_nt_notification_code_length
        CHECK (length(notification_code) <= 22),
    CONSTRAINT ck_nt_subject_length
        CHECK (length(subject) <= 200),

    CONSTRAINT fk_nt_recipient FOREIGN KEY (notification_recipient_id)
        REFERENCES notification_recipient (notification_recipient_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_nt_severity FOREIGN KEY (severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_nt_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_nt_recommendation FOREIGN KEY (ai_recommendation_id)
        REFERENCES ai_recommendation (ai_recommendation_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_nt_alert FOREIGN KEY (operational_alert_id)
        REFERENCES operational_alert (operational_alert_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- The most important rule for this table is NOT enforceable in the database
-- and is assigned to the Notification Service in spec section 41.3: at least
-- one non-suppressed notification must exist for any severity carrying
-- requires_immediate_escalation, with the departmental escalation_email as
-- fallback if every eligible recipient is suppressed, and an audit_log entry
-- when the fallback fires. A critical recommendation reaching nobody is the
-- platform's worst failure.

-- ---------------------------------------------------------------------
-- O11. maintenance_work_record
-- One maintenance job from request to closure. The operational record of
-- maintenance and THE PLATFORM'S ACCURACY SCORECARD.
--
-- It is the maintenance history the master model deliberately did not
-- cache: machine_maintenance_schedule excludes next_due_date and computes
-- due status from operational history instead. THIS TABLE IS THAT HISTORY.
--
-- It closes the prediction accuracy loop. reported_failure_category_id is
-- what was suspected at open, normally from the prediction;
-- confirmed_failure_category_id is what the engineer actually found.
-- Comparing the two across many jobs is the only honest measure of whether
-- the platform's predictions are correct - a model with 0.90 average
-- confidence and a 40% confirmation rate is not a good model, and no
-- internal validation metric would reveal that.
--
-- It quantifies what the platform is worth: work_type = 'predictive' means
-- the job exists BECAUSE FactoryFlow AI recommended it (spec O11).
-- ---------------------------------------------------------------------
CREATE TABLE maintenance_work_record (
    maintenance_work_record_id        INTEGER     NOT NULL
        CONSTRAINT pk_maintenance_work_record PRIMARY KEY AUTOINCREMENT,
    maintenance_work_record_code      VARCHAR(14) NOT NULL,
    machine_id                        INTEGER     NOT NULL,
    work_type                         TEXT        NOT NULL,
    machine_maintenance_schedule_id   INTEGER     NULL,
    triggering_alert_id               INTEGER     NULL,
    triggering_recommendation_id      INTEGER     NULL,
    reported_failure_category_id      INTEGER     NULL,
    confirmed_failure_category_id     INTEGER     NULL,
    priority_severity_level_id        INTEGER     NOT NULL,
    assigned_maintenance_team_id      INTEGER     NULL,
    assigned_engineer_id              INTEGER     NULL,
    work_status                       TEXT        NOT NULL DEFAULT 'open',
    opened_at                         DATETIME    NOT NULL,
    assigned_at                       DATETIME    NULL,
    started_at                        DATETIME    NULL,
    completed_at                      DATETIME    NULL,
    closed_at                         DATETIME    NULL,
    planned_duration_minutes          INTEGER     NULL,
    actual_duration_minutes           INTEGER     NULL,
    machine_downtime_minutes          INTEGER     NULL,
    did_stop_line                     INTEGER     NOT NULL DEFAULT 0,
    resolution_note                   TEXT        NULL,
    shift_id_opened                   INTEGER     NOT NULL,
    created_at                        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component              TEXT        NOT NULL,
    updated_at                        DATETIME    NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT uq_mwr_code UNIQUE (maintenance_work_record_code),

    CONSTRAINT ck_mwr_code_format
        CHECK (maintenance_work_record_code
               GLOB 'WO-[0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9]'),
    -- This is the definition of predictive work, and it makes the platform's
    -- own value metric self-enforcing. Without it a predictive record could
    -- exist with no recommendation behind it, and the count of
    -- platform-caused interventions would be inflated by preventive work.
    CONSTRAINT ck_mwr_predictive_requires_recommendation
        CHECK (work_type <> 'predictive'
               OR triggering_recommendation_id IS NOT NULL),
    -- The lifecycle chain opened <= assigned <= started <= completed <=
    -- closed, enforced for every pair where both values are present.
    -- opened_at is NOT NULL so it needs no null guard.
    CONSTRAINT ck_mwr_timestamp_sequence
        CHECK ((assigned_at  IS NULL OR opened_at <= assigned_at)
               AND (started_at   IS NULL OR opened_at <= started_at)
               AND (completed_at IS NULL OR opened_at <= completed_at)
               AND (closed_at    IS NULL OR opened_at <= closed_at)
               AND (assigned_at IS NULL OR started_at   IS NULL
                    OR assigned_at <= started_at)
               AND (assigned_at IS NULL OR completed_at IS NULL
                    OR assigned_at <= completed_at)
               AND (assigned_at IS NULL OR closed_at    IS NULL
                    OR assigned_at <= closed_at)
               AND (started_at  IS NULL OR completed_at IS NULL
                    OR started_at <= completed_at)
               AND (started_at  IS NULL OR closed_at    IS NULL
                    OR started_at <= closed_at)
               AND (completed_at IS NULL OR closed_at   IS NULL
                    OR completed_at <= closed_at)),
    CONSTRAINT ck_mwr_closed_requires_resolution
        CHECK (work_status <> 'closed'
               OR (closed_at IS NOT NULL AND resolution_note IS NOT NULL)),
    CONSTRAINT ck_mwr_durations_positive
        CHECK ((planned_duration_minutes IS NULL
                OR planned_duration_minutes > 0)
               AND (actual_duration_minutes IS NULL
                    OR actual_duration_minutes > 0)
               AND (machine_downtime_minutes IS NULL
                    OR machine_downtime_minutes > 0)),
    -- Downtime includes handover and restart that working time excludes.
    CONSTRAINT ck_mwr_downtime_at_least_duration
        CHECK (machine_downtime_minutes IS NULL
               OR actual_duration_minutes IS NULL
               OR machine_downtime_minutes >= actual_duration_minutes),
    CONSTRAINT ck_mwr_assigned_requires_team
        CHECK (work_status IN ('open', 'cancelled')
               OR assigned_maintenance_team_id IS NOT NULL),
    CONSTRAINT ck_mwr_work_type_allowed
        CHECK (work_type IN ('preventive', 'predictive', 'corrective',
                             'emergency', 'calibration', 'inspection')),
    CONSTRAINT ck_mwr_work_status_allowed
        CHECK (work_status IN ('open', 'assigned', 'in_progress',
                               'awaiting_parts', 'completed', 'closed',
                               'cancelled')),
    CONSTRAINT ck_mwr_did_stop_line_bool
        CHECK (did_stop_line IN (0, 1)),
    CONSTRAINT ck_mwr_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_mwr_maintenance_work_record_code_length
        CHECK (length(maintenance_work_record_code) <= 14),

    CONSTRAINT fk_mwr_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    -- Populating machine_maintenance_schedule_id is what marks the schedule
    -- as performed.
    CONSTRAINT fk_mwr_schedule FOREIGN KEY (machine_maintenance_schedule_id)
        REFERENCES machine_maintenance_schedule (machine_maintenance_schedule_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_reported_category FOREIGN KEY (reported_failure_category_id)
        REFERENCES failure_category (failure_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_confirmed_category
        FOREIGN KEY (confirmed_failure_category_id)
        REFERENCES failure_category (failure_category_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_severity FOREIGN KEY (priority_severity_level_id)
        REFERENCES failure_severity_level (failure_severity_level_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_team FOREIGN KEY (assigned_maintenance_team_id)
        REFERENCES maintenance_team (maintenance_team_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_engineer FOREIGN KEY (assigned_engineer_id)
        REFERENCES maintenance_engineer (maintenance_engineer_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_shift_opened FOREIGN KEY (shift_id_opened)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_triggering_alert FOREIGN KEY (triggering_alert_id)
        REFERENCES operational_alert (operational_alert_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mwr_triggering_recommendation
        FOREIGN KEY (triggering_recommendation_id)
        REFERENCES ai_recommendation (ai_recommendation_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- 5-year retention, the longest in the database, and THE TERMINUS OF THE
-- RETENTION DEPENDENCY CHAIN in spec section 13.4: this window transitively
-- pins ai_recommendation, supervisor_context, prediction_result,
-- prediction_feature_snapshot, operational_alert, and operational_event.
-- Application-validated (spec section 41.3): closure requires a
-- confirmed_failure_category_id for corrective, predictive, and emergency
-- work only. Routine preventive and inspection work legitimately confirms
-- nothing because nothing failed. Encoding that conditional set of work
-- types here would need revising every time the vocabulary grows.

-- =====================================================================
-- OPERATIONAL GROUP - LAYER 6
-- =====================================================================

-- ---------------------------------------------------------------------
-- O12. machine_maintenance_activity
-- The append-only timeline of steps performed within a maintenance job.
-- Makes the duration of a repair explicable rather than merely known.
--
-- A work record says a job took 255 minutes; this table says where those
-- minutes went. Two four-hour jobs are entirely different problems if one
-- spent three hours waiting for an engineer and the other spent three hours
-- on the repair: the first is a response-coverage problem, the second a
-- difficulty problem.
--
-- The intervals map onto commitments held in master data - response time
-- against maintenance_team.target_response_time_minutes, part retrieval
-- against inventory_location.average_retrieval_time_minutes, repair time
-- against machine_type_failure_mode.estimated_repair_duration_minutes.
-- MASTER DATA STATES COMMITMENTS; THIS TABLE IS WHERE THEY ARE HELD TO
-- ACCOUNT (spec O12).
--
-- Not a task checklist and not a procedure document. It records what
-- happened and when, never what should happen.
-- ---------------------------------------------------------------------
CREATE TABLE machine_maintenance_activity (
    machine_maintenance_activity_id  INTEGER  NOT NULL
        CONSTRAINT pk_machine_maintenance_activity PRIMARY KEY AUTOINCREMENT,
    maintenance_work_record_id       INTEGER  NOT NULL,
    activity_at                      DATETIME NOT NULL,
    activity_type                    TEXT     NOT NULL,
    performed_by_worker_id           INTEGER  NULL,
    duration_from_previous_seconds   INTEGER  NULL,
    notes                            TEXT     NULL,
    shift_id                         INTEGER  NOT NULL,
    created_at                       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component             TEXT     NOT NULL,

    -- Activities within a job are strictly time-ordered, and two identical
    -- steps at the same instant indicate a defect. Also makes activity
    -- insertion idempotent.
    CONSTRAINT uq_mma_work_record_activity
        UNIQUE (maintenance_work_record_id, activity_at, activity_type),

    CONSTRAINT ck_mma_duration_non_negative
        CHECK (duration_from_previous_seconds IS NULL
               OR duration_from_previous_seconds >= 0),
    -- This vocabulary is what makes interval measurement possible.
    CONSTRAINT ck_mma_activity_type_allowed
        CHECK (activity_type IN ('dispatched', 'arrived', 'diagnosis_started',
                                 'diagnosis_complete', 'part_requested',
                                 'part_collected', 'repair_started',
                                 'repair_complete', 'test_run', 'handover',
                                 'escalated', 'on_hold', 'resumed')),
    CONSTRAINT ck_mma_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    -- ON DELETE CASCADE was considered here and REJECTED (spec section 32.2):
    -- a cascade could silently remove activity history for a job whose
    -- duration is under dispute or whose intervals feed a service-level
    -- report. Deletion must be explicit and ordered.
    CONSTRAINT fk_mma_work_record FOREIGN KEY (maintenance_work_record_id)
        REFERENCES maintenance_work_record (maintenance_work_record_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mma_performed_by FOREIGN KEY (performed_by_worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mma_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);

-- ---------------------------------------------------------------------
-- O10. inventory_movement
-- The stock ledger. Every receipt, issue, return, adjustment, and
-- consumption, with the resulting balance. The single source of truth for
-- how much of anything is on hand.
--
-- inventory_item holds stocking POLICY and no quantity. This table is where
-- quantity lives, and it lives as a LEDGER rather than a balance. Storing
-- both the signed change and the resulting balance buys three things:
-- current stock is one indexed lookup rather than a SUM over the item's
-- whole history; the ledger is self-auditing, since every row's balance must
-- equal the previous row's plus this row's delta, making a break LOCATABLE
-- rather than merely detectable; and stock at any past moment is
-- recoverable, so "was the bearing in stock when we made the
-- recommendation?" is answerable without replay (spec O10).
--
-- A separate inventory_balance current-state table was considered and
-- REJECTED: it would be a second source of truth for the same number, and
-- the two would eventually disagree, at which point neither could be
-- trusted.
--
-- Balance is maintained PER ITEM, not per item-and-location.
-- inventory_location_id records where the transaction physically happened,
-- which matters because retrieval time differs by location and feeds the
-- repair estimate.
-- ---------------------------------------------------------------------
CREATE TABLE inventory_movement (
    inventory_movement_id        INTEGER       NOT NULL
        CONSTRAINT pk_inventory_movement PRIMARY KEY AUTOINCREMENT,
    inventory_movement_code      VARCHAR(22)   NOT NULL,
    inventory_item_id            INTEGER       NOT NULL,
    inventory_location_id        INTEGER       NOT NULL,
    movement_at                  DATETIME      NOT NULL,
    movement_type                TEXT          NOT NULL,
    quantity_delta               NUMERIC(12,4) NOT NULL,
    resulting_quantity_on_hand   NUMERIC(12,4) NOT NULL,
    production_run_id            INTEGER       NULL,
    maintenance_work_record_id   INTEGER       NULL,
    scrap_record_id              INTEGER       NULL,
    supplier_id                  INTEGER       NULL,
    recorded_by_worker_id        INTEGER       NOT NULL,
    shift_id                     INTEGER       NOT NULL,
    reference_note               TEXT          NULL,
    created_at                   DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component         TEXT          NOT NULL,

    CONSTRAINT uq_im_code UNIQUE (inventory_movement_code),

    CONSTRAINT ck_im_code_format
        CHECK (inventory_movement_code
               GLOB 'MOV-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9]'),
    CONSTRAINT ck_im_quantity_delta_non_zero
        CHECK (quantity_delta <> 0),
    -- Issuing more than is on hand is physically impossible.
    CONSTRAINT ck_im_balance_non_negative
        CHECK (resulting_quantity_on_hand >= 0),
    -- Inbound types must increase stock, outbound types must decrease it;
    -- adjustment may go either way.
    CONSTRAINT ck_im_delta_sign_matches_type
        CHECK ((movement_type IN ('receipt', 'return', 'transfer_in')
                AND quantity_delta > 0)
               OR (movement_type IN ('issue_production', 'issue_maintenance',
                                     'scrap_consumption', 'transfer_out')
                   AND quantity_delta < 0)
               OR movement_type = 'adjustment'),
    -- The four type-specific reference constraints together make an entire
    -- class of untraceable material movement impossible. Unreferenced
    -- consumption is material that left the store with no explanation, and a
    -- ledger containing it cannot be reconciled against a physical count.
    CONSTRAINT ck_im_production_reference_required
        CHECK (movement_type <> 'issue_production'
               OR production_run_id IS NOT NULL),
    CONSTRAINT ck_im_maintenance_reference_required
        CHECK (movement_type <> 'issue_maintenance'
               OR maintenance_work_record_id IS NOT NULL),
    CONSTRAINT ck_im_scrap_reference_required
        CHECK (movement_type <> 'scrap_consumption'
               OR scrap_record_id IS NOT NULL),
    CONSTRAINT ck_im_receipt_supplier_required
        CHECK (movement_type <> 'receipt' OR supplier_id IS NOT NULL),
    -- An unexplained stock correction destroys the ledger's credibility.
    CONSTRAINT ck_im_adjustment_note_required
        CHECK (movement_type <> 'adjustment' OR reference_note IS NOT NULL),
    CONSTRAINT ck_im_movement_type_allowed
        CHECK (movement_type IN ('receipt', 'issue_production',
                                 'issue_maintenance', 'return', 'adjustment',
                                 'scrap_consumption', 'transfer_out',
                                 'transfer_in')),
    CONSTRAINT ck_im_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_im_inventory_movement_code_length
        CHECK (length(inventory_movement_code) <= 22),

    CONSTRAINT fk_im_inventory_item FOREIGN KEY (inventory_item_id)
        REFERENCES inventory_item (inventory_item_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_im_inventory_location FOREIGN KEY (inventory_location_id)
        REFERENCES inventory_location (inventory_location_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_im_supplier FOREIGN KEY (supplier_id)
        REFERENCES supplier (supplier_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_im_recorded_by FOREIGN KEY (recorded_by_worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_im_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_im_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_im_work_record FOREIGN KEY (maintenance_work_record_id)
        REFERENCES maintenance_work_record (maintenance_work_record_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_im_scrap_record FOREIGN KEY (scrap_record_id)
        REFERENCES scrap_record (scrap_record_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Errors are corrected by an adjustment movement, never by editing.
-- THE BALANCE CHAIN IS THE AUDIT, and it cannot be a check constraint: each
-- row's balance depends on the previous row for the same item, which no
-- per-row predicate can see. It is the highest-priority reconciliation check
-- in spec section 41.6, and a break in the chain has a locatable origin,
-- which is precisely why the running balance was chosen over a computed sum.

-- ---------------------------------------------------------------------
-- O3. machine_state_transition
-- Every change of machine state as an immutable fact, with the duration of
-- the state being LEFT and the reason for leaving it. The history behind O2
-- and the foundation of availability analysis.
--
-- Storing duration_in_previous_state_seconds at the moment of leaving a
-- state means every completed interval is one row with a known length, which
-- turns availability analysis into a simple aggregation instead of a window
-- function over the whole history: availability is the sum of 'running'
-- durations over scheduled time; unplanned downtime is the sum of durations
-- where the state left was 'down_unplanned'; mean time to restore is the
-- average of those; upstream starvation is the sum of 'starved' durations
-- grouped by reason_code (spec O3).
--
-- The triggering references close the causal loop: when a machine goes down
-- because a detected condition led to a maintenance job, the transition
-- points at both, which is what lets Analytics answer whether acting on
-- recommendations measurably reduced downtime.
-- ---------------------------------------------------------------------
CREATE TABLE machine_state_transition (
    machine_state_transition_id         INTEGER  NOT NULL
        CONSTRAINT pk_machine_state_transition PRIMARY KEY AUTOINCREMENT,
    machine_id                          INTEGER  NOT NULL,
    from_state                          TEXT     NULL,
    to_state                            TEXT     NOT NULL,
    transition_at                       DATETIME NOT NULL,
    duration_in_previous_state_seconds  INTEGER  NULL,
    reason_code                         TEXT     NOT NULL,
    shift_id                            INTEGER  NOT NULL,
    production_run_id                   INTEGER  NULL,
    triggering_event_id                 INTEGER  NULL,
    triggering_work_record_id           INTEGER  NULL,
    notes                               TEXT     NULL,
    created_at                          DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component                TEXT     NOT NULL,

    -- A transition to the same state is not a transition.
    CONSTRAINT ck_mst_states_differ
        CHECK (from_state IS NULL OR from_state <> to_state),
    -- The first transition has no predecessor and therefore no duration;
    -- every subsequent one has both. Making it a constraint means the
    -- invariant holds regardless of which code path inserts the row.
    CONSTRAINT ck_mst_duration_consistency
        CHECK ((from_state IS NULL
                AND duration_in_previous_state_seconds IS NULL)
               OR (from_state IS NOT NULL
                   AND duration_in_previous_state_seconds IS NOT NULL)),
    CONSTRAINT ck_mst_duration_non_negative
        CHECK (duration_in_previous_state_seconds IS NULL
               OR duration_in_previous_state_seconds >= 0),
    CONSTRAINT ck_mst_from_state_allowed
        CHECK (from_state IS NULL
               OR from_state IN ('running', 'idle', 'setup', 'starved',
                                 'blocked', 'down_unplanned', 'down_planned',
                                 'offline')),
    CONSTRAINT ck_mst_to_state_allowed
        CHECK (to_state IN ('running', 'idle', 'setup', 'starved', 'blocked',
                            'down_unplanned', 'down_planned', 'offline')),
    -- reason_code turns a state log into a diagnostic record.
    CONSTRAINT ck_mst_reason_code_allowed
        CHECK (reason_code IN ('run_start', 'run_complete', 'changeover',
                               'tool_change', 'upstream_starvation',
                               'downstream_blockage', 'breakdown',
                               'planned_maintenance', 'quality_hold',
                               'operator_unavailable', 'shift_end',
                               'restored', 'asset_status_change')),
    CONSTRAINT ck_mst_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_mst_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mst_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mst_production_run FOREIGN KEY (production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mst_triggering_event FOREIGN KEY (triggering_event_id)
        REFERENCES operational_event (operational_event_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mst_triggering_work_record
        FOREIGN KEY (triggering_work_record_id)
        REFERENCES maintenance_work_record (maintenance_work_record_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Insert only, in the same transaction as the O2 update (spec section 46.1).
-- The rule that duration_in_previous_state_seconds must EQUAL the gap to the
-- previous transition is cross-row and cannot be a check constraint. It is a
-- reconciliation check per spec section 41.6, and a mismatch indicates a
-- lost or out-of-order transition, which would silently corrupt every
-- availability figure derived from this table.

-- ---------------------------------------------------------------------
-- O19. recommendation_action
-- What the human actually decided about a recommendation. The
-- human-in-the-loop audit record and the foundation of the decision
-- feedback loop.
--
-- PROJECT_OVERVIEW.md is unambiguous that the platform advises and the
-- manager decides. This is the only place in the database capturing a
-- human's judgement about the platform's output.
--
-- A SEPARATE TABLE SO THE RECOMMENDATION STAYS IMMUTABLE. Putting
-- status = 'accepted' on O18 would make the platform's product mutable, and
-- a recommendation that can be edited after a human responds to it is not an
-- audit record.
--
-- rejection_reason is enumerated so rejections aggregate into an improvement
-- signal: disagree_with_diagnosis points at the model, impractical_timing at
-- the scheduling logic, insufficient_evidence at confidence calibration.
-- no_action_taken records the platform's worst outcome, worse than rejection
-- because nobody engaged at all (spec O19).
--
-- THE LEAST REGENERABLE TABLE IN THE DATABASE: it records human judgement
-- that exists nowhere else and cannot be reconstructed from any other data.
-- ---------------------------------------------------------------------
CREATE TABLE recommendation_action (
    recommendation_action_id  INTEGER  NOT NULL
        CONSTRAINT pk_recommendation_action PRIMARY KEY AUTOINCREMENT,
    ai_recommendation_id      INTEGER  NOT NULL,
    action_taken              TEXT     NOT NULL,
    actioned_at               DATETIME NOT NULL,
    actioned_by_worker_id     INTEGER  NOT NULL,
    response_time_minutes     INTEGER  NOT NULL,
    modification_note         TEXT     NULL,
    rejection_reason          TEXT     NULL,
    rejection_note            TEXT     NULL,
    deferred_until            DATETIME NULL,
    resulting_work_record_id  INTEGER  NULL,
    shift_id                  INTEGER  NOT NULL,
    created_at                DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component      TEXT     NOT NULL,

    -- NO UNIQUE CONSTRAINT ON ai_recommendation_id. A recommendation may have
    -- more than one action row: a deferral followed by an acceptance is two
    -- decisions and both are recorded. The latest by actioned_at is
    -- operative; earlier ones remain as history.

    CONSTRAINT ck_ra_response_time_non_negative
        CHECK (response_time_minutes >= 0),
    -- An unexplained modification is lost feedback.
    CONSTRAINT ck_ra_modification_note_required
        CHECK (action_taken <> 'accepted_with_modification'
               OR modification_note IS NOT NULL),
    -- Rejections are the platform's most valuable improvement signal and an
    -- unexplained one teaches nothing.
    CONSTRAINT ck_ra_rejection_fields_required
        CHECK (action_taken <> 'rejected'
               OR (rejection_reason IS NOT NULL
                   AND rejection_note IS NOT NULL)),
    -- The less obvious half: prevents an ACCEPTED recommendation carrying a
    -- stale rejection reason, which would corrupt the aggregate that drives
    -- threshold and prompt tuning.
    CONSTRAINT ck_ra_rejection_fields_absent
        CHECK (action_taken = 'rejected'
               OR (rejection_reason IS NULL AND rejection_note IS NULL)),
    CONSTRAINT ck_ra_deferred_until_required
        CHECK (action_taken <> 'deferred' OR deferred_until IS NOT NULL),
    CONSTRAINT ck_ra_deferred_until_future
        CHECK (deferred_until IS NULL OR deferred_until > actioned_at),
    CONSTRAINT ck_ra_action_taken_allowed
        CHECK (action_taken IN ('accepted', 'accepted_with_modification',
                                'rejected', 'deferred', 'superseded',
                                'no_action_taken')),
    CONSTRAINT ck_ra_rejection_reason_allowed
        CHECK (rejection_reason IS NULL
               OR rejection_reason IN ('disagree_with_diagnosis',
                                       'impractical_timing',
                                       'resource_unavailable',
                                       'already_addressed',
                                       'insufficient_evidence',
                                       'business_priority_conflict')),
    CONSTRAINT ck_ra_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_ra_recommendation FOREIGN KEY (ai_recommendation_id)
        REFERENCES ai_recommendation (ai_recommendation_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ra_actioned_by FOREIGN KEY (actioned_by_worker_id)
        REFERENCES worker (worker_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_ra_shift FOREIGN KEY (shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    -- Proves a recommendation produced action.
    CONSTRAINT fk_ra_resulting_work_record FOREIGN KEY (resulting_work_record_id)
        REFERENCES maintenance_work_record (maintenance_work_record_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Written by the Dashboard only, the surface where a human records a
-- decision. Insert only, immutable: a change of mind is a NEW action row so
-- the decision sequence stays visible.
-- Application-validated (spec section 41.3): actioned_by_worker_id must hold
-- the authority the recommendation implies - a recommendation whose priority
-- requires_line_stop must be actioned by somebody with
-- can_authorize_line_stop.

-- ---------------------------------------------------------------------
-- O21. notification_delivery
-- One transmission attempt on one channel, with its outcome. Answers the
-- question the notification cannot: DID THE MESSAGE ACTUALLY ARRIVE?
--
-- Composing and delivering are different things, and the second fails in
-- ways the first cannot predict. delivery_status distinguishes 'sent' - the
-- platform handed the message to a provider - from 'delivered' - the provider
-- confirmed receipt. THE GAP BETWEEN THEM IS WHERE SILENT FAILURES LIVE, and
-- a platform recording only 'sent' will believe every message arrived.
--
-- failure_reason is enumerated so failures aggregate: invalid_address points
-- at stale master data, provider_error at infrastructure,
-- rate_limited_by_provider at volume. Each has a different fix (spec O21).
--
-- THIS TABLE HAS NO MASTER DATA REFERENCES AT ALL - the only one of the 53.
-- Its subject is entirely a transport concern and the recipient is reached
-- through the notification, which makes it trivially portable if the delivery
-- mechanism is ever replaced.
-- ---------------------------------------------------------------------
CREATE TABLE notification_delivery (
    notification_delivery_id  INTEGER      NOT NULL
        CONSTRAINT pk_notification_delivery PRIMARY KEY AUTOINCREMENT,
    notification_id           INTEGER      NOT NULL,
    channel                   TEXT         NOT NULL,
    attempt_number            INTEGER      NOT NULL DEFAULT 1,
    attempted_at              DATETIME     NOT NULL,
    delivery_status           TEXT         NOT NULL,
    delivered_at              DATETIME     NULL,
    provider_reference        VARCHAR(120) NULL,
    failure_reason            TEXT         NULL,
    failure_detail            TEXT         NULL,
    latency_ms                INTEGER      NULL,
    created_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component      TEXT         NOT NULL,
    updated_at                DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- Retries are distinguishable per channel, and this makes attempt
    -- recording idempotent.
    CONSTRAINT uq_nd_notification_channel_attempt
        UNIQUE (notification_id, channel, attempt_number),

    CONSTRAINT ck_nd_attempt_number_positive
        CHECK (attempt_number > 0),
    CONSTRAINT ck_nd_delivered_requires_timestamp
        CHECK (delivery_status <> 'delivered' OR delivered_at IS NOT NULL),
    CONSTRAINT ck_nd_delivered_at_not_before_attempt
        CHECK (delivered_at IS NULL OR delivered_at >= attempted_at),
    CONSTRAINT ck_nd_failure_reason_required
        CHECK (delivery_status NOT IN ('failed', 'bounced', 'rejected')
               OR failure_reason IS NOT NULL),
    CONSTRAINT ck_nd_failure_reason_absent_on_success
        CHECK (delivery_status NOT IN ('delivered', 'sent', 'queued')
               OR failure_reason IS NULL),
    CONSTRAINT ck_nd_latency_non_negative
        CHECK (latency_ms IS NULL OR latency_ms >= 0),
    CONSTRAINT ck_nd_channel_allowed
        CHECK (channel IN ('email', 'whatsapp')),
    -- 'sent' and 'delivered' deliberately distinct.
    CONSTRAINT ck_nd_delivery_status_allowed
        CHECK (delivery_status IN ('queued', 'sent', 'delivered', 'failed',
                                   'bounced', 'rejected')),
    CONSTRAINT ck_nd_failure_reason_allowed
        CHECK (failure_reason IS NULL
               OR failure_reason IN ('invalid_address', 'provider_error',
                                     'timeout', 'rate_limited_by_provider',
                                     'recipient_blocked',
                                     'message_too_large')),
    CONSTRAINT ck_nd_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT ck_nd_provider_reference_length
        CHECK (provider_reference IS NULL OR length(provider_reference) <= 120),

    CONSTRAINT fk_nd_notification FOREIGN KEY (notification_id)
        REFERENCES notification (notification_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Insert per attempt, and UPDATE ONLY to advance delivery_status from 'sent'
-- to 'delivered' when the provider confirms asynchronously. The single
-- documented mutation in Group G, and the reason this table carries
-- updated_at while notification does not.
-- The retry policy is a business rule with a physical consequence: 'failed'
-- and 'timeout' are transient and retryable; 'bounced', 'rejected', and
-- 'invalid_address' are PERMANENT and must not be retried, because retrying a
-- bounced address only generates more bounces. Spec section 41.3 assigns
-- enforcement to the Notification Service.
-- A recurring invalid_address failure is a MASTER DATA QUALITY PROBLEM, not a
-- delivery problem. This is the one place an operational data quality signal
-- points back at a master data defect, and it is only possible because the
-- two layers are cleanly separated.

-- =====================================================================
-- OPERATIONAL GROUP - LAYER 7 (deepest layer in the database)
-- =====================================================================

-- ---------------------------------------------------------------------
-- O2. machine_operational_status
-- The current operational state of each machine in exactly one row, with the
-- accumulated counters maintenance scheduling depends on. Answers "what is
-- happening right now" without scanning history.
--
-- Eight machines, eight rows, overwritten in place. THE ONLY TABLE IN THE
-- DATABASE WHOSE ROW COUNT NEVER GROWS.
--
-- The accumulated counters are the more consequential part.
-- machine_maintenance_schedule deliberately omits next_due_date; this table
-- supplies the operational input for computing it:
--   interval_basis 'operating_hours' -> accumulated_operating_hours minus
--                                       operating_hours_at_last_maintenance
--   interval_basis 'cycle_count'     -> accumulated_cycle_count minus
--                                       cycle_count_at_last_maintenance
--   interval_basis 'calendar_days'   -> baseline_start_date and closed work
--                                       records directly
--
-- Storing the value AT LAST MAINTENANCE rather than a resetting "since"
-- counter is deliberate: two absolute readings and a subtraction cannot
-- drift, whereas a resetting counter is a second mutable number that can
-- (spec O2).
--
-- This table sits at operational dependency layer 7, the deepest in the
-- database. It is the most derived table in the model and is fully
-- REGENERABLE by replaying machine_state_transition and cycle_history. That
-- regenerability is what makes its mutability safe: the row is a performance
-- convenience, never a source of truth.
-- ---------------------------------------------------------------------
CREATE TABLE machine_operational_status (
    machine_operational_status_id        INTEGER       NOT NULL
        CONSTRAINT pk_machine_operational_status PRIMARY KEY AUTOINCREMENT,
    machine_id                           INTEGER       NOT NULL,
    current_state                        TEXT          NOT NULL,
    state_since                          DATETIME      NOT NULL,
    current_production_run_id            INTEGER       NULL,
    current_shift_id                     INTEGER       NOT NULL,
    accumulated_operating_hours          NUMERIC(12,2) NOT NULL DEFAULT 0,
    accumulated_cycle_count              INTEGER       NOT NULL DEFAULT 0,
    operating_hours_at_last_maintenance  NUMERIC(12,2) NULL,
    cycle_count_at_last_maintenance      INTEGER       NULL,
    last_reading_at                      DATETIME      NULL,
    last_state_transition_id             INTEGER       NULL,
    open_alert_count                     INTEGER       NOT NULL DEFAULT 0,
    created_at                           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by_component                 TEXT          NOT NULL,
    updated_at                           DATETIME      NOT NULL DEFAULT CURRENT_TIMESTAMP,

    -- One row per machine for life.
    CONSTRAINT uq_mos_machine UNIQUE (machine_id),

    CONSTRAINT ck_mos_operating_hours_non_negative
        CHECK (accumulated_operating_hours >= 0),
    CONSTRAINT ck_mos_cycle_count_non_negative
        CHECK (accumulated_cycle_count >= 0),
    CONSTRAINT ck_mos_open_alert_count_non_negative
        CHECK (open_alert_count >= 0),
    CONSTRAINT ck_mos_maint_hours_not_ahead
        CHECK (operating_hours_at_last_maintenance IS NULL
               OR operating_hours_at_last_maintenance
                  <= accumulated_operating_hours),
    CONSTRAINT ck_mos_maint_cycles_not_ahead
        CHECK (cycle_count_at_last_maintenance IS NULL
               OR cycle_count_at_last_maintenance <= accumulated_cycle_count),
    -- The next two constraints work as a pair: together they make the
    -- state-to-run relationship a closed set of legal combinations, rejecting
    -- at write time an inconsistency that would otherwise produce nonsense on
    -- the dashboard and in the Supervisor Agent's context.
    -- Running without a run is a contradiction:
    CONSTRAINT ck_mos_running_requires_run
        CHECK (current_state <> 'running'
               OR current_production_run_id IS NOT NULL),
    -- A machine that is down or idle is not on a run:
    CONSTRAINT ck_mos_run_only_when_engaged
        CHECK (current_production_run_id IS NULL
               OR current_state IN ('running', 'setup', 'starved', 'blocked')),
    -- 'starved' and 'blocked' are distinguished because they mean opposite
    -- things about where the constraint is.
    CONSTRAINT ck_mos_current_state_allowed
        CHECK (current_state IN ('running', 'idle', 'setup', 'starved',
                                 'blocked', 'down_unplanned', 'down_planned',
                                 'offline')),
    CONSTRAINT ck_mos_created_by_component_allowed
        CHECK (created_by_component IN ('simulator', 'monitoring_agent',
               'prediction_agent', 'supervisor_agent', 'decision_agent',
               'notification_service', 'dashboard', 'platform')),

    CONSTRAINT fk_mos_machine FOREIGN KEY (machine_id)
        REFERENCES machine (machine_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mos_shift FOREIGN KEY (current_shift_id)
        REFERENCES shift (shift_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mos_production_run FOREIGN KEY (current_production_run_id)
        REFERENCES production_run (production_run_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT,
    CONSTRAINT fk_mos_last_transition FOREIGN KEY (last_state_transition_id)
        REFERENCES machine_state_transition (machine_state_transition_id)
        ON DELETE RESTRICT ON UPDATE RESTRICT
);
-- Insert once per machine, then update continuously. Permanent retention:
-- never archived, never purged. Both accumulated counters are maintained
-- running totals; spec section 41.6 states the reconciliation obligation
-- against cycle_history and machine_state_transition that keeps them honest.

-- =====================================================================
-- PART 3 - UNIQUE INDEXES (spec section 42.4)
--
-- Eight unique indexes, each enforcing a rule NO TABLE-LEVEL UNIQUE
-- CONSTRAINT CAN EXPRESS. These are the schema's highest-value constraints
-- and they are catalogued together because they are easy to omit during
-- implementation.
--
-- Seven are PARTIAL - they carry a WHERE clause, which SQLite supports
-- directly on CREATE UNIQUE INDEX. The eighth uses EXPRESSIONS rather than a
-- predicate, for the NULL reason given at its declaration.
--
-- BOOLEAN PREDICATES COMPARE AGAINST 1 EXPLICITLY. Because flags are INTEGER
-- rather than a boolean type (spec section 40.1), the predicate is written
-- WHERE is_bottleneck = 1 rather than WHERE is_bottleneck. SQLite would
-- accept the bare column as truthy, but the explicit comparison states the
-- domain and matches the check constraint on the same column.
-- =====================================================================

-- Rotation order unique among production shifts only. The general shift is
-- excluded from rotation ordering, so uniqueness must apply only to
-- production shifts; a plain unique constraint could not express this.
CREATE UNIQUE INDEX uq_shift_sequence_order_production
    ON shift (plant_id, sequence_order)
    WHERE shift_type = 'production';

-- Exactly one primary production route per product. A conditional uniqueness
-- rule across a subset of rows grouped by product.
CREATE UNIQUE INDEX uq_plc_primary_route_per_product
    ON product_line_capability (product_id)
    WHERE capability_type = 'production_route'
      AND is_primary_line = 1
      AND is_active = 1;

-- At most one bottleneck per line. A line has one constraint by definition;
-- two would make impact arithmetic contradictory. A plain unique constraint
-- could not express "at most one TRUE per group".
CREATE UNIQUE INDEX uq_machine_bottleneck_per_line
    ON machine (production_line_id)
    WHERE is_bottleneck = 1;

-- Exactly one lead per team: none leaves the team without an accountable
-- contact, two produce contradictory assignment.
CREATE UNIQUE INDEX uq_maintenance_engineer_team_lead
    ON maintenance_engineer (maintenance_team_id)
    WHERE is_team_lead = 1
      AND is_active = 1;

-- Exactly one default threshold profile per machine type.
CREATE UNIQUE INDEX uq_alert_threshold_profile_default
    ON alert_threshold_profile (machine_type_id)
    WHERE is_default = 1
      AND is_active = 1;

-- At most one active run per line - a line produces one product at a time,
-- and this is the single most important integrity rule on production_run.
-- ARCHITECTURAL WEIGHT: this is the database-level guarantee that two
-- concurrent Simulator writes cannot both schedule a run onto the same line.
-- In SQLite the guarantee is reinforced by the engine's single-writer model
-- (spec section 47.3): the two writes cannot even be in flight
-- simultaneously, and this index catches the case where the second writer
-- read a stale state before acquiring the write lock.
CREATE UNIQUE INDEX uq_production_run_active_per_line
    ON production_run (production_line_id)
    WHERE run_status IN ('setup', 'running', 'paused');

-- THE ALERT-STORM PREVENTION MECHANISM. An event matching an open key joins
-- that alert rather than creating another. Closed alerts may legitimately
-- share a key with a new open one, which is why the predicate is required.
-- THE SINGLE MOST VALUABLE INDEX IN THE OPERATIONAL GROUP, AND IT IS A
-- CORRECTNESS CONSTRAINT, NOT A PERFORMANCE ONE: it is the guarantee that
-- two events arriving milliseconds apart cannot each create an alert for the
-- same condition. Without it, correlation - the mechanism that makes the
-- platform usable rather than noisy - would silently degrade under load.
CREATE UNIQUE INDEX uq_oa_open_correlation_key
    ON operational_alert (correlation_key)
    WHERE alert_status IN ('open', 'acknowledged', 'escalated');

-- One snapshot per scope, per subject, per instant - which makes rebuild
-- idempotent.
--
-- THIS INDEX NEEDS EXPRESSIONS BECAUSE SQLITE TREATS NULLS AS DISTINCT. In a
-- unique index every NULL is considered different from every other NULL, so a
-- plain index over (snapshot_scope, production_line_id, machine_id,
-- snapshot_at) would place NO CONSTRAINT AT ALL on plant-scoped rows: those
-- carry NULL in both subject columns, so no two of them would ever conflict,
-- and duplicate snapshots at the same instant would be accepted silently.
--
-- -1 is safe as the substitute because AUTOINCREMENT issues only positive
-- integers, so no real identity value can collide with it. The rule the
-- frozen model states is preserved exactly; only the mechanism differs.
--
-- ONE CONSEQUENCE FOR QUERIES: an expression index is used by the planner
-- only when a query's predicate matches the expression. A lookup intended to
-- hit this index MUST be written with the same COALESCE form. This is the
-- kind of detail that turns a fast query into a table scan without any error
-- being raised.
CREATE UNIQUE INDEX uq_ds_scope_subject_time
    ON dashboard_snapshot (
        snapshot_scope,
        COALESCE(production_line_id, -1),
        COALESCE(machine_id, -1),
        snapshot_at
    );

-- =====================================================================
-- COMMIT
-- The whole schema is created in ONE transaction. SQLite is
-- transactional for DDL, so either every object above exists or none
-- does, and a partially-created schema is never left behind
-- (spec section 46).
-- =====================================================================

COMMIT;

-- =====================================================================
-- PART 4 - POST-CREATION VERIFICATION
--
-- Run these after applying this file. They are the structural
-- verification the specification requires (spec sections 31.1, 51.1).
-- Pragmas are outside the transaction because foreign_key_check and
-- integrity_check report rather than mutate.
-- =====================================================================

-- Must return no rows. Reports any row whose foreign key has no parent.
PRAGMA foreign_key_check;

-- Must return the single value 'ok'.
PRAGMA integrity_check;

-- Must return 1. WITHOUT THIS THE 163 FOREIGN KEYS ABOVE ENFORCE NOTHING.
-- The setting is PER-CONNECTION and is NOT persisted in the database file:
-- every application connection - Simulator, Monitoring Agent, Prediction
-- Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard,
-- Analytics - must issue PRAGMA foreign_keys = ON immediately after
-- connecting and before its first statement (spec sections 31.1, 47.3).
PRAGMA foreign_keys;

-- Expected object counts, per spec section 51.1:
--
--   SELECT count(*) FROM sqlite_master WHERE type = 'table'
--       AND name NOT LIKE 'sqlite_%';                     -- 53
--   SELECT count(*) FROM sqlite_master WHERE type = 'index'
--       AND sql IS NOT NULL;                              -- 8
--   SELECT count(*) FROM sqlite_sequence;                 -- 53 once seeded
--
-- Foreign keys are counted with PRAGMA foreign_key_list(<table>) per
-- table; the total across all 53 tables is 163 - 162 ON DELETE RESTRICT
-- plus the single ON DELETE SET NULL on
-- operational_event.triggering_reading_id.

-- =====================================================================
-- ANALYTICS GROUP
-- Reserved and intentionally EMPTY - 0 tables (spec section 51.1).
-- Analytics reads the operational tables directly; no table is created
-- here, and none should be added without amending the frozen
-- specification first.
-- =====================================================================

-- =====================================================================
-- END OF SCHEMA
-- =====================================================================
