# FactoryFlow AI — PostgreSQL Database Schema Specification

**Physical Database Design for an Agentic Manufacturing Monitoring & Decision Support Platform**

---

| Field | Value |
|---|---|
| Project | FactoryFlow AI |
| Document Type | PostgreSQL Physical Schema Specification |
| Target Platform | PostgreSQL 15 or later |
| Phase | Phase 0 — Physical design. Precedes DDL, ORM, and migration authoring |
| Status | Definitive specification for the physical database layer |
| Implements | `PROJECT_OVERVIEW.md`, `FACTORY_MASTER_DATA_DESIGN.md`, `FACTORY_OPERATIONAL_DATA_DESIGN.md` — all frozen |
| Table Count | 53 tables — 29 master, 24 operational |
| Scope | Specification only. No SQL, no DDL, no ORM, no migrations, no indexes, no executable code |

> **This document is an implementation, not a design.** The three input documents are frozen and authoritative. Every table, column, relationship, and business rule here derives from them. No entity is renamed, merged, split, added, or removed. No attribute is dropped. No relationship is simplified. Where this document makes a decision, that decision concerns **how PostgreSQL physically represents** an already-settled logical model — never what the model should be.

> **Traceability.** Master tables are numbered **M1–M29** matching `FACTORY_MASTER_DATA_DESIGN.md` §1–§29. Operational tables are numbered **O1–O24** matching `FACTORY_OPERATIONAL_DATA_DESIGN.md` §E1–§E24. Any reader can place a table in its logical source in one step.

---

## Table of Contents

| Part | Contents |
|---|---|
| [I](#part-i--database-overview) | Database Overview |
| [II](#part-ii--schema-organization) | Schema Organization |
| [III](#part-iii--master-tables) | Master Tables — M1 to M29 |
| [IV](#part-iv--operational-tables) | Operational Tables — O1 to O24 |
| [V](#part-v--relationship-architecture) | Relationship Architecture |
| [VI](#part-vi--postgresql-data-types) | PostgreSQL Data Types |
| [VII](#part-vii--constraint-strategy) | Constraint Strategy |
| [VIII](#part-viii--naming-standards) | Naming Standards |
| [IX](#part-ix--performance-strategy) | Performance Strategy |
| [X](#part-x--transaction-strategy) | Transaction Strategy |
| [XI](#part-xi--data-integrity-strategy) | Data Integrity Strategy |
| [XII](#part-xii--scalability-strategy) | Scalability Strategy |
| [XIII](#part-xiii--security-considerations) | Security Considerations |
| [XIV](#part-xiv--schema-summary) | Schema Summary |

---

# Part I — Database Overview

## 1. Purpose of PostgreSQL

PostgreSQL is the single persistence layer for FactoryFlow AI. Every static fact about the factory and every dynamic fact about its operation lives here, and every component in the pipeline reads or writes through it.

PostgreSQL was chosen because this platform's requirements happen to align precisely with its strengths:

| Requirement from the frozen design | PostgreSQL capability that satisfies it |
|---|---|
| 116 foreign key relationships with strict referential integrity | Mature, fully enforced declarative constraints with deferrable options |
| Business rules expressed as data-level invariants — 200+ check constraints | Rich `CHECK` support including multi-column and expression predicates |
| 65 closed value vocabularies across 101 columns | Native `ENUM` types with type safety and 4-byte storage |
| Document payloads written once, read whole — feature vectors, assembled context, recommendations | `JSONB` with binary storage, containment operators, and validation |
| Exact monetary and measurement arithmetic | `NUMERIC` with arbitrary precision and no floating-point drift |
| A time-series table growing at ~87,000 rows per day | Native declarative range partitioning, `TIMESTAMPTZ`, and BRIN-friendly append patterns |
| Multi-statement atomic units across several tables per agent cycle | Full ACID transactions with configurable isolation |
| Read-heavy dashboard concurrent with write-heavy simulation | MVCC — readers never block writers |
| Strict separation of master, operational, and platform data | Namespaced schemas within one database |

A single PostgreSQL database — not a polyglot arrangement of a relational store plus a time-series store plus a document store — is a deliberate choice. The volumes in this platform are modest (roughly 95,000 rows per day), the relationships are dense, and the explainability contract requires that a recommendation join to its evidence transactionally. Splitting the data across engines would introduce cross-store consistency problems to solve a scale problem this platform does not have. `PROJECT_OVERVIEW.md` §16.9 makes deliberate simplicity binding, and one database is the simplest arrangement that meets every requirement.

## 2. Database responsibilities

The database is responsible for exactly five things:

| Responsibility | How it is discharged |
|---|---|
| **Persist** master configuration and operational history | 53 tables across 4 schemas |
| **Enforce** structural integrity | Primary keys, foreign keys, unique constraints, not-null constraints |
| **Enforce** business invariants expressible declaratively | Check constraints, enumerated domains, exclusion of impossible states |
| **Guarantee** atomicity of multi-table agent operations | Transaction boundaries defined in Part X |
| **Preserve** the evidence chain | Deletion policy that makes silent loss of cited evidence impossible |

Equally important is what the database is **not** responsible for. It holds no business logic in triggers, no computation in stored procedures, and no derivation in views. Every rule that requires procedural evaluation lives in the owning application component. The reason is the ownership model in `FACTORY_OPERATIONAL_DATA_DESIGN.md` §6: each entity has exactly one writing component, and a trigger that wrote to a second table would silently violate that, making provenance unrecoverable. Declarative constraints reject bad data; they never author it.

## 3. Relationship with Factory Master Data

Master data is the frozen 29-entity model defining what exists in the factory. In PostgreSQL it becomes 29 tables in the `master` schema with these physical characteristics:

| Characteristic | Physical expression |
|---|---|
| Small and bounded | ~245 rows total across all 29 tables, permanently |
| Human-authored | Populated by seed data and administrative edit, never by an agent |
| Rarely changed | No high-frequency write path touches these tables |
| Never hard-deleted | `is_active BOOLEAN NOT NULL DEFAULT TRUE` on 28 tables; `lifecycle_status` on `machine` |
| Referenced, never duplicated | 78 foreign keys point **into** `master`; zero point out |
| Identity | `INTEGER GENERATED ALWAYS AS IDENTITY` — 32-bit is ample for bounded tables |

The single most important physical property: **`master` has no foreign key to `operational`.** Not one. The dependency runs strictly one way, which means `master` can be created, seeded, reviewed, and reasoned about entirely independently of any operational data. This is what makes the two logical documents readable in isolation, and it is enforced structurally rather than by convention.

## 4. Relationship with Operational Data

Operational data is the frozen 24-entity model recording what is happening. In PostgreSQL it becomes 22 tables in `operational` and 2 in `system`:

| Characteristic | Physical expression |
|---|---|
| Unbounded growth | ~95,000 rows per day, dominated by one table |
| Machine-authored | Written only by the owning component from §6 of the operational document |
| Append-only by default | 18 of 24 tables never receive an `UPDATE` |
| Time-indexed | Every table carries an explicit event-time column distinct from `created_at` |
| Retention-managed | Every table has a defined online window and archive or purge behaviour |
| Identity | `BIGINT GENERATED ALWAYS AS IDENTITY` — mandatory, not preferred; see §36.1 |

The 24 tables reference `master` 78 times and each other 37 times, for 115 foreign keys plus one deliberate soft reference. Every one of the 37 operational-to-operational references points strictly downward through the 7 dependency layers established in the operational document §13.4, which is what keeps the graph acyclic.

## 5. Relationship with Agents

Eight components interact with the database, and their access is asymmetric by design. Write access follows the ownership model exactly; read access is broad.

| Component | Writes (tables owned) | Reads |
|---|---|---|
| **Factory Simulator** | 12 operational tables — Groups A, B, C | `master` only. **Never any agent-produced table** |
| **Monitoring Agent** | `operational_event`, `operational_alert` | Telemetry, production, quality, inventory, `master` thresholds |
| **Prediction Agent** | `prediction_feature_snapshot`, `prediction_result` | Telemetry, cycles, status, events, scrap, `master` parameters |
| **Supervisor Agent** | `supervisor_context` | Alerts, predictions, runs, inventory, work records, `master` rules |
| **Decision Agent** | `ai_recommendation` | `supervisor_context.context_document` and nothing else |
| **Notification Service** | `notification`, `notification_delivery` | Recommendations, alerts, `master` recipients |
| **Dashboard** | `recommendation_action`, `dashboard_snapshot` | Nearly everything |
| **Platform** | `audit_log`, `system_health_status` | Both, for diagnostics |

Two access boundaries are architectural rather than incidental, and Part XIII specifies how role separation will enforce them:

**The Simulator must never read an agent-produced table.** If the simulator could observe that a failure had been predicted, its subsequent behaviour could be influenced by that prediction and every accuracy measurement would become circular. Physically this is a read-permission boundary, not a code convention.

**No component writes a table it does not own.** The database enforces this through table-level privileges granted per role, so a defect in one agent cannot corrupt another's data.

## 6. Relationship with Dashboard

The Dashboard is the broadest reader in the platform and a narrow writer. It owns exactly two tables:

`recommendation_action` records the human decision. It is owned by the Dashboard rather than the Decision Agent because the row records a *human* verdict on the platform's advice, and the component that produces advice must not be the component that records the verdict on it. Ownership follows the actor.

`dashboard_snapshot` is a materialised aggregate holding a `JSONB` document of factory state. It exists so the dashboard renders from one row rather than aggregating across ~87,000 telemetry rows per viewer, and so historical state can be replayed. It is fully derived and rebuildable, which makes it the most disposable table in the database.

**No agent reads `dashboard_snapshot`.** Agents read primary tables. A presentation aggregate in a reasoning path would be an unnecessary and potentially stale dependency.

## 7. Database lifecycle

| Stage | What happens | Frequency |
|---|---|---|
| **1. Schema creation** | Four schemas, 65 ENUM types, 53 tables created in dependency order | Once per environment |
| **2. Master seeding** | ~245 rows loaded across `master` in dependency-layer order 0 → 4 | Once, then edited administratively |
| **3. Master validation** | Completeness checks from the master document §32.2 run and must pass | After every seed or master edit |
| **4. Operational start** | Simulator begins writing; agents begin their cycles | Continuous |
| **5. Steady state** | ~95,000 operational rows per day; master effectively static | Continuous |
| **6. Reconciliation** | Four maintained totals verified against their sources | Daily and hourly per §28.3 of the operational document |
| **7. Aggregation** | Interval rollups and downsamples written before any purge | Continuous and nightly |
| **8. Retention** | Archive and purge per table, in reverse dependency order | Nightly |
| **9. Master evolution** | New machines, products, or thresholds added as rows | Occasional |

**Stage 3 gates stage 4.** The master document defines completeness rules — every monitored machine has a threshold profile, every profile has at least one rule, every line has at least one machine and one capability row, at least one notification recipient covers the most severe level outside shift hours. A database that violates any of these is structurally valid and operationally broken: it would present as configured while silently monitoring nothing. Validation therefore runs before the simulator is permitted to start.

**Stage 7 gates stage 8.** Purging a high-volume table before its aggregate has been written and verified loses the data permanently. The ordering is mandatory, not advisory.

**Stage 9 requires no schema change.** Adding machines, lines, sensors, products, or thresholds is row insertion. Part XII establishes this property table by table.

---

# Part II — Schema Organization

## 8. Four schemas

The database uses four PostgreSQL schemas inside a single database. Schemas — not separate databases — because every recommendation must join transactionally to its evidence across the master/operational boundary, and cross-database joins in PostgreSQL require foreign data wrappers, which would forfeit both referential integrity and transactional consistency for no benefit.

| Schema | Tables | Contents | Growth |
|---|---|---|---|
| `master` | 29 | Static factory configuration | Bounded, ~245 rows |
| `operational` | 22 | Dynamic factory activity | Unbounded, ~95,000 rows/day |
| `system` | 2 | Platform audit and component health | ~2,000 rows/day |
| `analytics` | 0 | Reserved for derived analytical structures | Empty at v1 |

## 9. Why each schema exists

### 9.1 `master` — static configuration

Holds all 29 master entities. Justified by four properties that differ categorically from operational data:

- **Different lifecycle.** Authored by humans, validated before use, changed rarely.
- **Different access pattern.** Read constantly by every component, written almost never.
- **Different privileges.** Application roles need `SELECT` only. Write access belongs to an administrative role. Part XIII specifies this.
- **Different backup profile.** Small enough to snapshot cheaply and often; a corrupted master schema is a total outage while a lost hour of telemetry is a gap.

Separating the schema makes all four differences enforceable rather than aspirational. A blanket `GRANT SELECT ON ALL TABLES IN SCHEMA master` to every application role is a one-line, self-documenting policy.

### 9.2 `operational` — dynamic activity

Holds 22 of the 24 operational entities: Groups A through G, plus `dashboard_snapshot`. This is where all growth, all partitioning, and all retention management occur.

`dashboard_snapshot` is placed here rather than in `analytics` because its content is factory state, its consumer is the live dashboard, and `analytics` is reserved for structures that do not yet exist.

### 9.3 `system` — platform metadata

Holds `audit_log` and `system_health_status`. These two are operational entities in the logical model, and moving them to a separate schema is a **physical placement decision only** — no logical grouping, ownership, or relationship changes.

The separation is justified because these two tables describe **the platform** rather than **the factory**:

- They are the only tables whose subject is a software component rather than a manufacturing object.
- `system_health_status` has no foreign keys at all, and `audit_log` has exactly one.
- `audit_log` must remain readable when the operational schema is unavailable — during recovery, an audit trail in the same schema as the damage is worth considerably less.
- Their privilege model differs: `audit_log` requires append-only access for every component and read access for very few.

### 9.4 `analytics` — reserved, empty

Created at v1 and left empty. It exists now, with nothing in it, for one reason: **creating it later would require a migration that grants privileges across an existing production database**, whereas creating it now costs one statement and settles the namespace question permanently.

`PROJECT_OVERVIEW.md` §18 places reporting and feedback-loop work in later phases and forbids pre-building for them. This schema honours that literally — it reserves a name and builds nothing. When Phase 3 or Phase 4 needs derived analytical structures, they land here rather than polluting `operational` with tables that have no operational consumer.

## 10. Cross-schema dependency rules

Three rules, all structurally enforced:

| # | Rule | Enforcement |
|---|---|---|
| 1 | `master` references nothing outside `master` | Zero outbound foreign keys exist |
| 2 | `operational` and `system` may reference `master` | 78 and 1 foreign keys respectively |
| 3 | `analytics` may reference anything; nothing references `analytics` | No inbound foreign keys will ever be created |

The dependency direction is strictly `analytics → operational/system → master`. This yields an unambiguous creation order, an unambiguous teardown order, and a guarantee that the master schema can be restored independently.

## 11. Search path policy

No component relies on `search_path` resolution. **Every table reference is schema-qualified.** Depending on a mutable session setting for correctness is a defect waiting for a connection-pool configuration change, and the ORM layer will declare an explicit schema on every model.

---

# Part III — Master Tables

## 12. Conventions applied to all master tables

### 12.1 Identity

Every master table declares:

```
<table>_id   INTEGER   GENERATED ALWAYS AS IDENTITY   PRIMARY KEY
```

`GENERATED ALWAYS AS IDENTITY` rather than `serial`. It is SQL-standard, it owns its sequence so a table drop cannot orphan one, and `ALWAYS` prevents an application from supplying an explicit value — which matters because the master document §3.1 states foreign keys reference the surrogate and application logic must never construct one.

`INTEGER` rather than `BIGINT` for master tables. All 29 are bounded at a few dozen rows; 32-bit is ample and halves the width of the 78 foreign key columns that reference them.

### 12.2 Standard trailing columns

Every master table carries these three columns, in this order, after its business columns. They are defined once here and referenced by each table rather than repeated 29 times.

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `is_active` | BOOLEAN | NOT NULL | `TRUE` | Soft retirement. Master data is never hard-deleted because operational history references it |
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | Audit — row creation. Immutable |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | Audit — last modification. Maintained by the application, not by a trigger |

**One documented exception.** `master.machine` (M10) carries `lifecycle_status` **instead of** `is_active`, per master document §3.3. A boolean cannot express the difference between standby, under overhaul, and decommissioned, and the Monitoring Agent needs that distinction. Carrying both would create two overlapping sources for one fact.

`updated_at` is maintained by the writing application rather than a trigger. A trigger would be the conventional choice, and it is rejected for the reason given in §2: the ownership model requires that no database-resident code write on a component's behalf.

### 12.3 Text length policy

The frozen documents specify `VARCHAR(n)` with explicit lengths. Those lengths are honoured exactly.

In PostgreSQL `VARCHAR(n)` and `TEXT` are the same storage with the same performance — the length is a constraint, not an optimisation. It is retained because it **is** a validation rule: a 150-character supplier name is a business limit worth enforcing, and enforcing it in the column type is cheaper and more reliable than in five application layers. Columns the frozen documents type as `TEXT` are unbounded free text and stay `TEXT`.

### 12.4 Table specification format

Every table below uses the identical eleven-heading structure: Purpose, Business Description, Primary Key, Columns, Constraints, Relationships, Read Components, Write Components, Growth, Retention, Notes.

Constraints are grouped as **Unique**, **Check**, and **Foreign Keys**. Every foreign key states its `ON DELETE` and `ON UPDATE` action. Part V explains why those actions are uniform.

---

## Group A — Plant & Organization (M1–M4)

---

### M1. `master.plant`

**Purpose**

The single manufacturing site the platform monitors. Root of the entire master hierarchy and anchor for site-wide timezone, currency, and calendar settings.

**Business Description**

One row. Every operational timestamp is interpreted in this row's `timezone`, and every monetary value in the database is denominated in its `currency_code`. Modelled as a table rather than assumed in configuration because both are business facts, and because multi-plant support becomes additional rows rather than a schema change.

**Primary Key**

`plant_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`. Surrogate rather than `plant_code` because every other master table carries `plant_id` and site codes are revised during corporate reorganisation; a rename must stay a one-row update.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `plant_id` | INTEGER | NOT NULL | identity | Primary key |
| `plant_code` | VARCHAR(10) | NOT NULL | — | Business key. Format `PLT-nn` |
| `plant_name` | VARCHAR(120) | NOT NULL | — | Trading name |
| `address_line` | VARCHAR(200) | NOT NULL | — | Street address |
| `city` | VARCHAR(80) | NOT NULL | — | Locality |
| `state_region` | VARCHAR(80) | NOT NULL | — | State or region |
| `country_code` | CHAR(2) | NOT NULL | — | ISO 3166-1 alpha-2 |
| `timezone` | VARCHAR(50) | NOT NULL | — | IANA name. **Critical** — all shift-boundary arithmetic depends on it |
| `currency_code` | CHAR(3) | NOT NULL | — | ISO 4217. Applies to every monetary column in the database |
| `operating_days_per_week` | INTEGER | NOT NULL | — | 1–7 |
| `shifts_per_day` | INTEGER | NOT NULL | — | 1–4 |
| `commissioned_date` | DATE | NOT NULL | — | Floor for all asset installation dates |
| `annual_production_capacity_units` | INTEGER | NULL | — | NULL when not formally rated |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_plant_code` on (`plant_code`)

*Check*
- `ck_plant_code_format` — `plant_code` matches `^PLT-[0-9]{2}$`
- `ck_plant_country_code_format` — `country_code` matches `^[A-Z]{2}$`
- `ck_plant_currency_code_format` — `currency_code` matches `^[A-Z]{3}$`
- `ck_plant_operating_days_range` — `operating_days_per_week` between 1 and 7
- `ck_plant_shifts_per_day_range` — `shifts_per_day` between 1 and 4
- `ck_plant_capacity_positive` — `annual_production_capacity_units` is NULL or > 0
- `ck_plant_name_not_blank` — trimmed `plant_name` length > 0

**`timezone` is not check-constrained.** Validating against `pg_timezone_names` requires a query, and `CHECK` predicates must be `IMMUTABLE`. Validation is therefore an application responsibility at write time, and it is mandatory — an invalid IANA name silently corrupts every shift-window calculation in the platform. This is recorded here rather than left implicit precisely because the constraint cannot live in the database.

**`commissioned_date` is not constrained against `CURRENT_DATE`.** `CURRENT_DATE` is `STABLE`, not `IMMUTABLE`, so PostgreSQL rejects it in a `CHECK`. The same limitation applies to every "not in the future" rule in the frozen documents; §41.4 sets out the uniform approach.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Child | `master.plant_area` | One-to-many |
| Child | `master.department` | One-to-many |
| Child | `master.shift` | One-to-many |

No outbound foreign keys. Sole root of the master dependency graph.

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative seed and edit only. No agent writes this table.

**Growth** — 1 row. Fixed. Additional plants would be additional rows requiring no schema change.

**Retention** — Permanent. Never archived, never purged.

**Notes**

`currency_code` being site-level is why no monetary column anywhere in the database carries its own currency. A single-site plant transacts in one currency and per-row currency codes would be dead weight on 14 monetary columns.

---

### M2. `master.plant_area`

**Purpose**

A distinct physical zone within the plant. Answers *where is this* for production lines and storage locations, and supplies the ambient thermal context that helps explain machine behaviour.

**Business Description**

Machining bays, assembly halls, warehouses, spare parts stores, maintenance workshops, quality labs. Areas are **physical**; departments are **organisational**. Deliberately separate because a machining bay physically contains lines owned by Production while Maintenance and Quality staff also work inside it. `nominal_ambient_temp_c` lets the Decision Agent distinguish *this machine is overheating* from *this whole area is hot today*.

**Primary Key**

`plant_area_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`. A composite natural key of (`plant_id`, `plant_area_code`) was rejected: it would propagate a two-column foreign key into `production_line` and `inventory_location` and again into everything referencing those.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `plant_area_id` | INTEGER | NOT NULL | identity | Primary key |
| `plant_area_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `AREA-XXX` |
| `plant_id` | INTEGER | NOT NULL | — | FK → `master.plant` |
| `area_name` | VARCHAR(100) | NOT NULL | — | Descriptive name |
| `area_type` | `master.area_type` | NOT NULL | — | ENUM, 8 values |
| `floor_level` | INTEGER | NULL | — | 0 is ground. NULL when meaningless |
| `floor_space_sqm` | NUMERIC(10,2) | NULL | — | NULL when not surveyed |
| `nominal_ambient_temp_c` | NUMERIC(5,2) | NULL | — | Thermal baseline. NULL where no meaningful figure |
| `is_climate_controlled` | BOOLEAN | NOT NULL | `FALSE` | Weakens ambient as an explanation for a thermal excursion |
| `access_restriction` | `master.access_restriction` | NOT NULL | `'general'` | ENUM, 3 values. Affects dispatch feasibility |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_plant_area_code` on (`plant_area_code`)

*Check*
- `ck_plant_area_code_format` — matches `^AREA-[A-Z]{3}$`
- `ck_plant_area_floor_level_range` — `floor_level` is NULL or between -2 and 10
- `ck_plant_area_floor_space_positive` — `floor_space_sqm` is NULL or > 0
- `ck_plant_area_ambient_range` — `nominal_ambient_temp_c` is NULL or between -20 and 60
- `ck_plant_area_name_not_blank` — trimmed `area_name` length > 0

*Foreign Keys*
- `fk_plant_area_plant` — `plant_id` → `master.plant(plant_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.plant` | Many-to-one |
| Child | `master.production_line` | One-to-many |
| Child | `master.inventory_location` | One-to-many |
| Child | `master.maintenance_team` (as `base_plant_area_id`) | One-to-many, optional |

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — 7 rows. Effectively fixed; grows only if the plant is physically extended.

**Retention** — Permanent.

**Notes**

Two business rules from master document §2 are **not** enforceable as check constraints because they span tables: a production line may only sit in an area of type `production` or `assembly`, and an inventory location's type must align with its area's type. Both are application-validated at write time and listed in §41.3.

---

### M3. `master.department`

**Purpose**

An organisational unit that owns work and people. Answers *who is responsible*, as distinct from `plant_area` which answers *where is it*.

**Business Description**

Production, Maintenance, Quality, Warehouse, Planning. Departments carry escalation ownership: a predicted machine failure implicates Production, which owns the output at risk, and Maintenance, which owns the repair. `cost_center_code` connects operational downtime to a financial owner.

**Primary Key**

`department_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**No manager foreign key exists.** A `manager_worker_id` column here would create a circular dependency with `master.worker`, which already carries `department_id`. Master document §30.4 resolves this by making leadership a property of the person: `worker_role.is_managerial` identifies managerial roles, and a department's manager is the active worker in that department holding one. The cycle is designed out rather than tolerated.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `department_id` | INTEGER | NOT NULL | identity | Primary key |
| `department_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `DEP-XXX` |
| `plant_id` | INTEGER | NOT NULL | — | FK → `master.plant` |
| `department_name` | VARCHAR(100) | NOT NULL | — | Full name |
| `department_function` | `master.department_function` | NOT NULL | — | ENUM, 6 values. Drives escalation routing |
| `cost_center_code` | VARCHAR(20) | NOT NULL | — | Finance identifier. Unique |
| `escalation_email` | VARCHAR(150) | NULL | — | Shared mailbox. NULL when none. **Fallback when no individual recipient matches** |
| `headcount_budget` | INTEGER | NULL | — | Approved staffing. NULL when not budgeted |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_department_code` on (`department_code`)
- `uq_department_cost_center_code` on (`cost_center_code`) — two departments sharing a cost centre would make downtime cost attribution ambiguous

*Check*
- `ck_department_code_format` — matches `^DEP-[A-Z]{3}$`
- `ck_department_headcount_non_negative` — `headcount_budget` is NULL or >= 0
- `ck_department_escalation_email_format` — `escalation_email` is NULL or matches a basic address pattern containing `@` and a dot in the domain
- `ck_department_name_not_blank` — trimmed `department_name` length > 0

*Foreign Keys*
- `fk_department_plant` — `plant_id` → `master.plant(plant_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.plant` | Many-to-one |
| Child | `master.production_line` | One-to-many |
| Child | `master.worker` | One-to-many |
| Child | `master.maintenance_team` | One-to-many |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 5 rows. Fixed.

**Retention** — Permanent.

**Notes**

Email format checking is deliberately basic. A regex that fully implements RFC 5322 is unreadable, unmaintainable, and still rejects valid addresses; the constraint catches obvious data entry errors and delivery failure catches the rest — `operational.notification_delivery.failure_reason = 'invalid_address'` is the real detector, and the operational document §E21 routes those back to master data review.

---

### M4. `master.shift`

**Purpose**

Defines the plant's working time patterns. Establishes when production is expected, which crew is on duty, and what "now" means operationally.

**Business Description**

Three rotating production shifts plus a general day shift. Shifts matter in four ways: the simulator generates shift-dependent behaviour, the Notification Service routes to whoever is actually on duty, the Supervisor Agent identifies maintenance windows, and the Decision Agent anchors trends to shift boundaries — *"rising since the start of B shift"* is far more useful to a manager than a raw timestamp. This is the most referenced master table in the database, with 17 operational tables carrying a `shift_id`.

**Primary Key**

`shift_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `shift_id` | INTEGER | NOT NULL | identity | Primary key |
| `shift_code` | VARCHAR(8) | NOT NULL | — | Business key. Format `SH-X` |
| `plant_id` | INTEGER | NOT NULL | — | FK → `master.plant` |
| `shift_name` | VARCHAR(60) | NOT NULL | — | Descriptive name |
| `start_time` | TIME | NOT NULL | — | Local time in `plant.timezone`, never UTC |
| `end_time` | TIME | NOT NULL | — | Local time |
| `crosses_midnight` | BOOLEAN | NOT NULL | `FALSE` | **Stored, not inferred.** Eliminates a recurring off-by-one-day defect |
| `shift_type` | `master.shift_type` | NOT NULL | — | ENUM, 3 values |
| `sequence_order` | INTEGER | NOT NULL | — | Rotation order. Unique among production shifts |
| `is_production_shift` | BOOLEAN | NOT NULL | `TRUE` | FALSE suppresses low-output events during planned non-production |
| `break_duration_minutes` | INTEGER | NULL | — | NULL when unstructured |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_shift_code` on (`shift_code`)
- `uq_shift_sequence_order_production` — a **partial** unique constraint on (`plant_id`, `sequence_order`) where `shift_type = 'production'`. The general shift is excluded from rotation ordering, so uniqueness must apply only to production shifts. PostgreSQL partial unique indexes express this exactly; a plain unique constraint could not.

*Check*
- `ck_shift_code_format` — matches `^SH-[A-Z]{1,4}$`
- `ck_shift_times_differ` — `start_time` <> `end_time`
- `ck_shift_crosses_midnight_consistent` — `crosses_midnight` is TRUE if and only if `end_time <= start_time`. **The most valuable check constraint on this table** — a mismatch produces silently wrong shift-window arithmetic across 17 operational tables
- `ck_shift_sequence_order_positive` — `sequence_order` > 0
- `ck_shift_break_duration_range` — `break_duration_minutes` is NULL or between 0 and 120
- `ck_shift_name_not_blank` — trimmed `shift_name` length > 0

*Foreign Keys*
- `fk_shift_plant` — `plant_id` → `master.plant(plant_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.plant` | Many-to-one |
| Child | `master.worker` | One-to-many |
| Child | `master.maintenance_team` | One-to-many |
| Referenced by | 17 operational tables | One-to-many each |

**Read Components** — All eight components

**Write Components** — Administrative only

**Growth** — 4 rows. Fixed.

**Retention** — Permanent.

**Notes**

Shift times are stored as `TIME` without a zone and interpreted against `plant.timezone`. `TIMETZ` is deliberately avoided: PostgreSQL documentation itself discourages it, and a shift starts at 06:00 local regardless of daylight saving, which a fixed offset cannot express. The frozen master document §4 rule 6 states this requirement and `TIME` plus `plant.timezone` is the faithful implementation.

---

## Group B — Production Structure (M5–M7)

---

### M5. `master.production_line`

**Purpose**

A sequenced group of machines producing finished output. The unit at which production is planned, output measured, and business impact assessed.

**Business Description**

Machines on a line are interdependent — a stop anywhere halts everything downstream — which is why the line, not the machine, is the right unit for impact. `criticality` is the single most consequential column: two machines can carry identical failure probability while their lines differ completely in business importance, and without line criticality the platform could only rank by technical severity.

**Primary Key**

`production_line_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `production_line_id` | INTEGER | NOT NULL | identity | Primary key |
| `production_line_code` | VARCHAR(10) | NOT NULL | — | Business key. Format `LN-nn` |
| `plant_area_id` | INTEGER | NOT NULL | — | FK → `master.plant_area`. **Physical** location |
| `department_id` | INTEGER | NOT NULL | — | FK → `master.department`. **Organisational** owner |
| `line_name` | VARCHAR(120) | NOT NULL | — | Descriptive name |
| `line_type` | `master.line_type` | NOT NULL | — | ENUM, 5 values |
| `criticality` | `master.criticality_level` | NOT NULL | — | ENUM, 4 values. **Primary prioritisation input** |
| `design_capacity_units_per_hour` | NUMERIC(10,2) | NOT NULL | — | Nameplate throughput |
| `station_count` | INTEGER | NOT NULL | — | Bounds `machine.line_position` |
| `target_oee_percent` | NUMERIC(5,2) | NULL | — | NULL when not formally targeted |
| `changeover_time_minutes` | INTEGER | NULL | — | NULL for single-product lines |
| `commissioned_date` | DATE | NOT NULL | — | Bounds machine installation dates |

Plus the standard trailing columns from §12.2.

**Two parents is intentional.** `plant_area_id` answers *where* and `department_id` answers *who owns it*. These are orthogonal in a real plant and forcing a single parent would drop one of two facts the platform needs.

**Deliberately absent:** `machine_count` — derivable by counting active machines, and storing it would guarantee drift. `plant_id` — reachable through `plant_area_id`.

**Constraints**

*Unique*
- `uq_production_line_code` on (`production_line_code`)

*Check*
- `ck_production_line_code_format` — matches `^LN-[0-9]{2}$`
- `ck_production_line_capacity_positive` — `design_capacity_units_per_hour` > 0
- `ck_production_line_station_count_positive` — `station_count` > 0
- `ck_production_line_target_oee_range` — `target_oee_percent` is NULL or between 0 and 100
- `ck_production_line_changeover_non_negative` — `changeover_time_minutes` is NULL or >= 0
- `ck_production_line_name_not_blank` — trimmed `line_name` length > 0

*Foreign Keys*
- `fk_production_line_plant_area` — `plant_area_id` → `master.plant_area(plant_area_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_production_line_department` — `department_id` → `master.department(department_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.plant_area`, `master.department` | Many-to-one each |
| Child | `master.machine` | One-to-many |
| Child | `master.product_line_capability` | One-to-many |
| Child | `master.worker` | One-to-many, optional |
| Child | `master.business_rule` | One-to-many, optional |
| Child | `master.notification_recipient` | One-to-many, optional |
| Referenced by | 6 operational tables | One-to-many each |

**Read Components** — All eight components

**Write Components** — Administrative only

**Growth** — 4 rows. Grows only when a physical line is added.

**Retention** — Permanent.

**Notes**

`station_count >= COUNT(active machines on the line)` and `criticality = 'critical'` implying a line-scoped downtime cost rule are both cross-table rules, application-validated per §41.3.

---

### M6. `master.product`

**Purpose**

A finished good the plant manufactures. Supplies the commercial identity that connects machine risk to customer orders and revenue.

**Business Description**

`standard_selling_price` less `standard_material_cost` gives contribution margin per unit, which converts *"we will lose 60 units"* into a quantified margin figure. `quality_criticality` captures what throughput arithmetic misses: on a safety-critical product a degrading machine is a scrap and liability risk before it is a downtime risk.

**Cycle time is deliberately not a column here.** The same product runs at different rates on different lines, so a product-level figure would be wrong for most lines. It lives exclusively on `product_line_capability` (M7).

**Primary Key**

`product_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`. Particularly valuable here: product codes are revised by engineering change more often than any other master code, and a revision must not cascade through bills of materials, capabilities, and years of operational runs.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `product_id` | INTEGER | NOT NULL | identity | Primary key |
| `product_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `PRD-XX-nnn` |
| `product_name` | VARCHAR(150) | NOT NULL | — | Descriptive name |
| `product_family` | VARCHAR(80) | NOT NULL | — | Grouping for family-level impact reasoning |
| `unit_of_measure` | `master.unit_of_measure` | NOT NULL | — | ENUM. **`BOX` excluded by check constraint** — see below |
| `standard_selling_price` | NUMERIC(12,2) | NOT NULL | — | Revenue per unit in `plant.currency_code` |
| `standard_material_cost` | NUMERIC(12,2) | NOT NULL | — | Material cost per unit |
| `quality_criticality` | `master.quality_criticality` | NOT NULL | — | ENUM, 3 values |
| `target_scrap_rate_pct` | NUMERIC(5,2) | NULL | — | NULL when not formally targeted |
| `shelf_life_days` | INTEGER | NULL | — | NULL for durable goods, the normal case |
| `drawing_revision` | VARCHAR(12) | NULL | — | NULL when not revision-controlled |
| `introduced_date` | DATE | NOT NULL | — | Bounds historical production validity |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_product_code` on (`product_code`)

*Check*
- `ck_product_code_format` — matches `^PRD-[A-Z]{2,4}-[0-9]{3}$`
- `ck_product_unit_of_measure_allowed` — `unit_of_measure` in (`EA`, `KG`, `L`, `M`, `SET`). **The shared `unit_of_measure` ENUM carries six values because `inventory_item` uses `BOX`; the frozen master document gives `product` only five.** One shared type plus a narrowing check honours both definitions exactly without duplicating the type. See §37.3
- `ck_product_selling_price_positive` — `standard_selling_price` > 0
- `ck_product_material_cost_positive` — `standard_material_cost` > 0
- `ck_product_margin_positive` — `standard_material_cost` < `standard_selling_price`. A negative contribution margin would invert the Decision Agent's impact reasoning
- `ck_product_scrap_rate_range` — `target_scrap_rate_pct` is NULL or between 0 and 100
- `ck_product_shelf_life_positive` — `shelf_life_days` is NULL or > 0
- `ck_product_name_not_blank` — trimmed `product_name` length > 0

*Foreign Keys*

None. `product` is an independent reference table with no outbound references, placing it at the base of the master dependency graph alongside `plant`.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Child | `master.product_line_capability` | One-to-many |
| Child | `master.bill_of_materials` | One-to-many |
| Referenced by | `operational.production_run` | One-to-many |

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 3 rows. Grows as the product portfolio grows; no schema change.

**Retention** — Permanent.

**Notes**

`ck_product_margin_positive` is the most operationally valuable constraint on this table. It rejects at write time a data error that would otherwise surface as a recommendation asserting that a stoppage *saves* money.

---

### M7. `master.product_line_capability`

**Purpose**

Resolves the many-to-many between products and production lines, carrying the attributes that exist only for a specific pairing: achievable cycle time, changeover cost, qualification, and whether the line is a substitutable production route.

**Business Description**

Answers two questions the Decision Agent asks constantly: *can this product be made somewhere else*, and *how much output are we actually losing*. `is_qualified` is the subtle one — a line can be technically capable while not customer- or regulator-approved, and rerouting to a capable-but-unqualified line produces unsellable parts. `capability_type` separates a substitutable production route from a finishing stage, so the platform never proposes moving machining to a packaging line.

**Primary Key**

`product_line_capability_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`, with a composite unique constraint on the natural key. The surrogate keeps `operational.production_run`'s reference to a single column; the unique constraint enforces the real rule.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `product_line_capability_id` | INTEGER | NOT NULL | identity | Primary key |
| `product_id` | INTEGER | NOT NULL | — | FK → `master.product` |
| `production_line_id` | INTEGER | NOT NULL | — | FK → `master.production_line` |
| `capability_type` | `master.capability_type` | NOT NULL | — | ENUM, 2 values. **Only `production_route` rows are reroute candidates** |
| `is_primary_line` | BOOLEAN | NOT NULL | `FALSE` | Exactly one TRUE per product among `production_route` rows |
| `cycle_time_seconds` | NUMERIC(8,2) | NOT NULL | — | **The authoritative rate figure.** Lives here and nowhere else |
| `max_hourly_output_units` | NUMERIC(10,2) | NOT NULL | — | Sustainable hourly output including normal losses |
| `changeover_minutes` | INTEGER | NOT NULL | — | Determines whether rerouting is worth recommending |
| `is_qualified` | BOOLEAN | NOT NULL | `FALSE` | FALSE blocks rerouting even when technically capable |
| `qualification_expiry_date` | DATE | NULL | — | NULL when approval does not expire |
| `tooling_available` | BOOLEAN | NOT NULL | `TRUE` | FALSE means a tooling transfer is needed first |
| `effective_from_date` | DATE | NOT NULL | — | Keeps historical output interpretable against the rate then in force |

Plus the standard trailing columns from §12.2.

**No business code.** This table has no `_code` column. It qualifies a relationship rather than naming a thing, and nobody on the shop floor refers to a capability row by name. Master document §3.1 states this convention.

**Constraints**

*Unique*
- `uq_product_line_capability_pair` on (`product_id`, `production_line_id`) — one declaration per pairing

*Check*
- `ck_plc_cycle_time_positive` — `cycle_time_seconds` > 0
- `ck_plc_max_output_positive` — `max_hourly_output_units` > 0
- `ck_plc_changeover_non_negative` — `changeover_minutes` >= 0
- `ck_plc_finishing_stage_not_primary` — `capability_type <> 'finishing_stage'` OR `is_primary_line = FALSE`. A finishing stage is never the primary production route
- `ck_plc_qualification_expiry_requires_qualified` — `qualification_expiry_date` IS NULL OR `is_qualified = TRUE`. An expiry date on an unqualified route is contradictory

*Foreign Keys*
- `fk_plc_product` — `product_id` → `master.product(product_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_plc_production_line` — `production_line_id` → `master.production_line(production_line_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.product`, `master.production_line` | Many-to-one each |
| Referenced by | `operational.production_run` | One-to-many |

Together the two parents resolve **product ↔ production_line as many-to-many**, one of five genuine many-to-many relationships in the master model.

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — 7 rows. Grows as products × capable lines.

**Retention** — Permanent. Superseded capabilities are soft-retired via `is_active`, never edited — which is what allows `operational.cycle_history` to compute deviation against a pinned capability without copying the standard.

**Notes**

*"Exactly one `production_route` row per product carries `is_primary_line = TRUE`"* cannot be a table constraint — it is a conditional uniqueness rule across a subset of rows grouped by product. A **partial unique index** on (`product_id`) where `capability_type = 'production_route' AND is_primary_line = TRUE AND is_active` expresses it exactly, and Part IX records it as the one place where an index carries a correctness obligation rather than a performance one.

---

## Group C — Asset Hierarchy (M8–M12)

---

### M8. `master.machine_category`

**Purpose**

Groups machine types into broad equipment classes. Carries the maintenance specialisation and condition-monitoring policy that hold for a whole equipment family.

**Business Description**

CNC machining, assembly automation, material handling, packaging, inspection. The category earns its place by holding two facts that would otherwise be duplicated across every machine type in the family: which maintenance discipline owns it, and whether vibration monitoring is physically meaningful.

**Primary Key**

`machine_category_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_category_id` | INTEGER | NOT NULL | identity | Primary key |
| `machine_category_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `MCAT-XXX` |
| `category_name` | VARCHAR(80) | NOT NULL | — | Family name |
| `description` | TEXT | NULL | — | Context for the Decision Agent. NULL when self-evident |
| `equipment_class` | `master.equipment_class` | NOT NULL | — | ENUM, 5 values |
| `primary_maintenance_specialization` | `master.maintenance_specialization` | NOT NULL | — | **Shared ENUM** with `maintenance_team.specialization` and `failure_category.required_specialization` |
| `is_rotating_equipment` | BOOLEAN | NOT NULL | `FALSE` | Determines whether vibration is a signal or noise |
| `requires_condition_monitoring` | BOOLEAN | NOT NULL | `TRUE` | FALSE means tracked for context but not a prediction target |
| `typical_service_life_years` | INTEGER | NULL | — | NULL when highly variable |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_machine_category_code` on (`machine_category_code`)

*Check*
- `ck_machine_category_code_format` — matches `^MCAT-[A-Z]{3}$`
- `ck_machine_category_service_life_range` — `typical_service_life_years` is NULL or between 1 and 50
- `ck_machine_category_name_not_blank` — trimmed `category_name` length > 0

*Foreign Keys*

None. Independent lookup at the base of the master dependency graph.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Child | `master.machine_type` | One-to-many |

**Read Components** — Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — 5 rows. Fixed; new categories only for genuinely new equipment families.

**Retention** — Permanent.

**Notes**

`primary_maintenance_specialization` uses the **shared** `master.maintenance_specialization` ENUM type, referenced by five columns across four tables. Sharing one type rather than declaring three identical ones is what makes matching a failed machine to a qualified team a direct value comparison. §37.2 catalogues the shared types and the maintenance obligation that comes with them.

---

### M9. `master.machine_type`

**Purpose**

A specific machine model and its engineering specifications. Where reliability characteristics live — MTBF, MTTR, rated power, design life.

**Business Description**

Three identical machining centres on the floor are three `machine` rows sharing one `machine_type` row. `mtbf_hours` is the reliability baseline the simulator uses to set degradation rate and the Prediction Agent uses as wear context. `mttr_minutes` is what converts a predicted failure into a downtime estimate, and a downtime estimate into a business impact figure — it is the first number in the entire impact chain.

**Primary Key**

`machine_type_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_type_id` | INTEGER | NOT NULL | identity | Primary key |
| `machine_type_code` | VARCHAR(24) | NOT NULL | — | Business key. Format `MTY-<model>` |
| `machine_category_id` | INTEGER | NOT NULL | — | FK → `master.machine_category` |
| `type_name` | VARCHAR(120) | NOT NULL | — | Full model name |
| `manufacturer` | VARCHAR(100) | NOT NULL | — | Equipment maker |
| `model_number` | VARCHAR(60) | NOT NULL | — | Manufacturer designation |
| `rated_power_kw` | NUMERIC(8,2) | NOT NULL | — | Nameplate electrical rating |
| `design_life_hours` | INTEGER | NOT NULL | — | Total expected operating life |
| `mtbf_hours` | INTEGER | NOT NULL | — | Mean time between failures |
| `mttr_minutes` | INTEGER | NOT NULL | — | Mean time to repair. **Entry point to the impact chain** |
| `requires_tooling` | BOOLEAN | NOT NULL | `FALSE` | Determines whether tool wear is a meaningful parameter |
| `control_system` | VARCHAR(80) | NULL | — | NULL for equipment without a programmable controller |
| `min_operators_required` | INTEGER | NOT NULL | — | 0 for fully automatic equipment |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_machine_type_code` on (`machine_type_code`)

*Check*
- `ck_machine_type_code_format` — matches `^MTY-[A-Z0-9-]{3,20}$`
- `ck_machine_type_rated_power_positive` — `rated_power_kw` > 0
- `ck_machine_type_design_life_positive` — `design_life_hours` > 0
- `ck_machine_type_mtbf_positive` — `mtbf_hours` > 0
- `ck_machine_type_mtbf_below_design_life` — `mtbf_hours` < `design_life_hours`. A machine expected never to fail within its service life does not exist
- `ck_machine_type_mttr_positive` — `mttr_minutes` > 0
- `ck_machine_type_operators_range` — `min_operators_required` between 0 and 5
- `ck_machine_type_name_not_blank` — trimmed `type_name` length > 0

*Foreign Keys*
- `fk_machine_type_category` — `machine_category_id` → `master.machine_category(machine_category_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.machine_category` | Many-to-one |
| Child | `master.machine` | One-to-many |
| Child | `master.machine_type_parameter` | One-to-many |
| Child | `master.machine_type_failure_mode` | One-to-many |
| Child | `master.alert_threshold_profile` | One-to-many |

**Read Components** — Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — 6 rows. Grows when new equipment models are introduced.

**Retention** — Permanent.

**Notes**

Three cross-table rules are application-validated per §41.3: tool wear may only be declared for types with `requires_tooling = TRUE`; vibration only for types whose category has `is_rotating_equipment = TRUE`; and every type used by a monitored machine must declare at least one ML-feature parameter.

---

### M10. `master.machine`

**Purpose**

An individual physical machine. The core asset of the platform and the subject of every telemetry reading, event, prediction, and most recommendations.

**Business Description**

`line_position` places the machine in the process sequence, enabling cascade reasoning. `is_bottleneck` identifies the line constraint, which has immediate financial consequence — a bottleneck stoppage costs full output while a non-bottleneck stoppage may be partly absorbed. `downstream_buffer_units` quantifies the grace period, converting *"the line will stop"* into *"the line will stop in about 45 minutes"*.

**`lifecycle_status` is asset state, not operational run state.** It records whether the asset is part of the working factory and changes a handful of times in a machine's life. Whether the machine is currently running, idle, or down is operational and lives in `operational.machine_operational_status`.

**Deliberately absent:** `accumulated_operating_hours` and `current_status` (operational), `last_maintenance_date` (derived from operational history), `plant_area_id` (reachable through `production_line_id`; storing it would let a machine claim a different area from its own line).

**Primary Key**

`machine_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`. The surrogate matters more here than anywhere else in the database: 13 operational tables reference it, and `operational.machine_sensor_reading` alone accumulates ~2.6 million references per month. Those must survive a machine being renumbered, relocated, or re-tagged.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_id` | INTEGER | NOT NULL | identity | Primary key |
| `machine_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `MC-nnnn` |
| `machine_type_id` | INTEGER | NOT NULL | — | FK → `master.machine_type` |
| `production_line_id` | INTEGER | NOT NULL | — | FK → `master.production_line`. Exactly one line |
| `line_position` | INTEGER | NOT NULL | — | Process sequence. Unique within the line |
| `alert_threshold_profile_id` | INTEGER | NULL | — | FK → `master.alert_threshold_profile`. NULL only for unmonitored assets |
| `machine_name` | VARCHAR(120) | NOT NULL | — | Functional name, more meaningful than the model name |
| `serial_number` | VARCHAR(60) | NOT NULL | — | Manufacturer serial. Unique |
| `asset_tag` | VARCHAR(30) | NULL | — | Finance register tag. Unique when present |
| `installation_date` | DATE | NOT NULL | — | Physical installation |
| `commissioned_date` | DATE | NOT NULL | — | Entry into production; anchor for age |
| `warranty_expiry_date` | DATE | NULL | — | NULL when out of warranty. **Changes the recommended action** — in-warranty failures route to the manufacturer |
| `criticality` | `master.criticality_level` | NOT NULL | — | ENUM, 4 values. Machine-level importance |
| `is_bottleneck` | BOOLEAN | NOT NULL | `FALSE` | At most one TRUE per line |
| `downstream_buffer_units` | INTEGER | NULL | — | NULL when no buffer. Converts a stoppage into a grace period |
| `rated_capacity_units_per_hour` | NUMERIC(10,2) | NULL | — | NULL for non-producing stations such as inspection |
| `lifecycle_status` | `master.machine_lifecycle_status` | NOT NULL | `'in_service'` | ENUM, 4 values. **Replaces `is_active` on this table** |
| `is_monitored` | BOOLEAN | NOT NULL | `TRUE` | FALSE for assets tracked for context without instrumentation |
| `installed_position_notes` | TEXT | NULL | — | Siting and access constraints |

Plus `created_at` and `updated_at` from §12.2. **`is_active` is absent** — the documented exception.

**Constraints**

*Unique*
- `uq_machine_code` on (`machine_code`)
- `uq_machine_serial_number` on (`serial_number`)
- `uq_machine_asset_tag` on (`asset_tag`) — unique when present; NULLs do not conflict in PostgreSQL
- `uq_machine_line_position` on (`production_line_id`, `line_position`) — two machines cannot occupy one station
- `uq_machine_bottleneck_per_line` — a **partial** unique index on (`production_line_id`) where `is_bottleneck = TRUE`. A line has one constraint by definition; two would make impact arithmetic contradictory. A plain unique constraint could not express "at most one TRUE per group"

*Check*
- `ck_machine_code_format` — matches `^MC-[0-9]{4}$`
- `ck_machine_line_position_positive` — `line_position` > 0
- `ck_machine_buffer_non_negative` — `downstream_buffer_units` is NULL or >= 0
- `ck_machine_rated_capacity_positive` — `rated_capacity_units_per_hour` is NULL or > 0
- `ck_machine_commissioned_after_installation` — `commissioned_date` >= `installation_date`
- `ck_machine_warranty_after_commissioned` — `warranty_expiry_date` is NULL or >= `commissioned_date`
- `ck_machine_monitored_requires_profile` — `is_monitored = FALSE` OR `alert_threshold_profile_id` IS NOT NULL. **A monitored machine with no thresholds cannot actually be monitored** while appearing configured
- `ck_machine_name_not_blank` — trimmed `machine_name` length > 0

*Foreign Keys*
- `fk_machine_type` — `machine_type_id` → `master.machine_type(machine_type_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_machine_production_line` — `production_line_id` → `master.production_line(production_line_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_machine_alert_threshold_profile` — `alert_threshold_profile_id` → `master.alert_threshold_profile(alert_threshold_profile_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.machine_type`, `master.production_line`, `master.alert_threshold_profile` | Many-to-one each |
| Child | `master.machine_maintenance_schedule` | One-to-many |
| Referenced by | 13 operational tables | One-to-many each |

**Read Components** — All eight components

**Write Components** — Administrative only

**Growth** — 8 rows. Grows as equipment is added; the highest-value growth axis in the model and requiring no schema change.

**Retention** — Permanent. Decommissioning sets `lifecycle_status = 'decommissioned'`; the row is never deleted because years of telemetry, events, predictions, and recommendations reference it and deleting it would destroy the audit trail the explainability contract rests on.

**Notes**

`ck_machine_monitored_requires_profile` and the profile-to-type match rule together prevent the single worst master data defect in this platform: a machine that appears monitored on the dashboard and is silently checked against nothing, or against limits belonging to different equipment. The type-match half is cross-table and application-validated; §41.3 records it.

---

### M11. `master.machine_parameter`

**Purpose**

The controlled vocabulary of measurable machine parameters. The shared dictionary the simulator, Monitoring Agent, and Prediction Agent all read.

**Business Description**

Temperature, rotational speed, torque, tool wear, vibration, power draw, air pressure — with unit, physical range, degradation direction, and whether the value accumulates. **This table stores no readings.** It stores definitions. The distinction matters: the frozen documents exclude sensor *readings* from master data, and this is the parameter *catalogue*, without which the parameter vocabulary would be hardcoded in three places and drift apart.

`is_cumulative` separates parameters that accumulate and reset at maintenance from those that oscillate, which changes generation, interpretation, and ML feature engineering. `degradation_direction` records which way is worse — temperature rising is bad, air pressure falling is bad — so the Monitoring Agent applies one generic rule instead of a special case per parameter.

**Primary Key**

`machine_parameter_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_parameter_id` | INTEGER | NOT NULL | identity | Primary key |
| `machine_parameter_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `PRM-XXXX` |
| `parameter_name` | VARCHAR(80) | NOT NULL | — | Appears verbatim in recommendations |
| `unit_of_measure` | VARCHAR(16) | NOT NULL | — | Free text, not an ENUM — `°C`, `mm/s`, `Nm`, `rpm`, `bar`, `kW`, `%` are open-ended |
| `measurement_domain` | `master.measurement_domain` | NOT NULL | — | ENUM, 7 values. Groups correlated signals |
| `data_type` | `master.parameter_data_type` | NOT NULL | — | ENUM, 3 values |
| `physical_min` | NUMERIC(12,4) | NOT NULL | — | **Validation floor.** Below this is a sensor fault, not a machine fault |
| `physical_max` | NUMERIC(12,4) | NOT NULL | — | Validation ceiling |
| `degradation_direction` | `master.degradation_direction` | NOT NULL | — | ENUM, 3 values |
| `is_cumulative` | BOOLEAN | NOT NULL | `FALSE` | TRUE for tool wear |
| `description` | TEXT | NULL | — | Precise physical definition. Grounds the Decision Agent's hypotheses |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_machine_parameter_code` on (`machine_parameter_code`)

*Check*
- `ck_machine_parameter_code_format` — matches `^PRM-[A-Z]{3,8}$`
- `ck_machine_parameter_physical_range_ordered` — `physical_min` < `physical_max`
- `ck_machine_parameter_cumulative_increasing` — `is_cumulative = FALSE` OR `degradation_direction = 'increasing'`. A quantity that accumulates can only accumulate upward
- `ck_machine_parameter_unit_not_blank` — trimmed `unit_of_measure` length > 0. **Mandatory and never blank** — every value the platform surfaces to a human or an LLM carries its unit
- `ck_machine_parameter_name_not_blank` — trimmed `parameter_name` length > 0

*Foreign Keys*

None. Independent lookup at the base of the master dependency graph.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Child | `master.machine_type_parameter` | One-to-many |
| Child | `master.alert_threshold_rule` | One-to-many |
| Referenced by | `operational.machine_sensor_reading`, `operational.operational_event`, `master.machine_type_failure_mode` | One-to-many each |

**Read Components** — Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — 7 rows. Grows when new instrumentation is introduced.

**Retention** — Permanent. Codes are effectively immutable once operational data references them — renaming one would orphan historical telemetry and break the traceability chain.

**Notes**

`unit_of_measure` is `VARCHAR(16)` rather than an ENUM deliberately. Units are an open set — a new parameter may introduce `Pa`, `dB`, or `µm` — and forcing an `ALTER TYPE` for each would be friction with no integrity benefit, since the value is displayed rather than compared. This is the one place the frozen documents specify a constrained-looking field that is correctly left as text, and §37.4 sets out the general rule for that judgement.

---

### M12. `master.machine_type_parameter`

**Purpose**

Declares which parameters each machine type exposes and the healthy operating envelope for each. The specification the simulator generates against and the Prediction Agent draws its feature set from.

**Business Description**

A machining centre reports temperature, speed, torque, tool wear, and vibration; a conveyor reports speed and power and has no tooling. Declaring applicability per type keeps the platform from generating meaningless data and feeding empty features to a model.

The envelope here is **engineering fact** — how the equipment behaves when healthy. Alert limits in `alert_threshold_rule` are **operations policy** — when we want to be told. Collapsing the two would mean retuning an alert silently changes what the simulator considers healthy, making the pipeline circular and untestable.

`is_ml_feature` defines the model's input set **as data**, so the feature set is inspectable and adjustable without touching the training pipeline.

**Primary Key**

`machine_type_parameter_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`, with a composite unique constraint on the natural pair.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_type_parameter_id` | INTEGER | NOT NULL | identity | Primary key |
| `machine_type_id` | INTEGER | NOT NULL | — | FK → `master.machine_type` |
| `machine_parameter_id` | INTEGER | NOT NULL | — | FK → `master.machine_parameter` |
| `nominal_value` | NUMERIC(12,4) | NOT NULL | — | Simulator's centre of generation |
| `normal_min` | NUMERIC(12,4) | NOT NULL | — | **Not an alert limit** — the envelope of correct behaviour |
| `normal_max` | NUMERIC(12,4) | NOT NULL | — | Upper bound of healthy operation |
| `sampling_interval_seconds` | INTEGER | NOT NULL | — | Drives emission frequency and ML resolution |
| `is_ml_feature` | BOOLEAN | NOT NULL | `TRUE` | Defines the model's input set as data |
| `expected_drift_direction` | `master.drift_direction` | NOT NULL | — | ENUM, 3 values. The degradation signature |
| `sensor_accuracy_pct` | NUMERIC(5,2) | NULL | — | Separates real drift from instrument noise |
| `criticality_weight` | NUMERIC(4,2) | NULL | — | Relative diagnostic importance when several parameters drift at once |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_machine_type_parameter_pair` on (`machine_type_id`, `machine_parameter_id`)

*Check*
- `ck_mtp_normal_range_ordered` — `normal_min` < `normal_max`
- `ck_mtp_nominal_within_envelope` — `nominal_value` between `normal_min` and `normal_max`. A nominal outside its own healthy range is a data error
- `ck_mtp_sampling_interval_range` — `sampling_interval_seconds` between 1 and 3600
- `ck_mtp_sensor_accuracy_range` — `sensor_accuracy_pct` is NULL or between 0 and 25
- `ck_mtp_criticality_weight_range` — `criticality_weight` is NULL or between 0 and 5

*Foreign Keys*
- `fk_mtp_machine_type` — `machine_type_id` → `master.machine_type(machine_type_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mtp_machine_parameter` — `machine_parameter_id` → `master.machine_parameter(machine_parameter_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.machine_type`, `master.machine_parameter` | Many-to-one each |

Resolves **machine_type ↔ machine_parameter as many-to-many**.

**Read Components** — Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — ~28 rows. Grows as machine types × declared parameters.

**Retention** — Permanent.

**Notes**

The **threshold ordering rule** — the most important numeric invariant in the whole database — spans this table, `machine_parameter`, and `alert_threshold_rule`:

```
physical_min ≤ critical_low ≤ warning_low ≤ normal_min
                                          ≤ nominal_value
                                          ≤ normal_max ≤ warning_high ≤ critical_high ≤ physical_max
```

Only the middle segment (`normal_min ≤ nominal_value ≤ normal_max`) is enforceable here as a single-row check. The rest crosses three tables and is application-validated per §41.3. A violation means **a healthy machine generates alerts** — the single most common configuration error in condition monitoring and the fastest route to alert fatigue.

---

## Group D — People & Contacts (M13–M17)

---

### M13. `master.worker_role`

**Purpose**

Defines job roles and, more importantly for this platform, the **authority** each role carries.

**Business Description**

The Decision Agent frequently recommends actions requiring authority — stopping a line, approving unplanned maintenance. Sending *"stop Line 01 within 45 minutes"* to somebody who cannot authorise a line stop is a delivered notification that produces no action. `can_authorize_line_stop` and `can_authorize_maintenance` encode authority as data so routing is correct rather than hopeful.

`is_managerial` also serves the structural purpose set out in M3: it replaces the manager back-reference foreign keys that would otherwise create three circular dependencies.

**Primary Key**

`worker_role_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `worker_role_id` | INTEGER | NOT NULL | identity | Primary key |
| `worker_role_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `ROL-XXX` |
| `role_name` | VARCHAR(80) | NOT NULL | — | Job title, shown in notifications |
| `role_category` | `master.role_category` | NOT NULL | — | ENUM, 8 values |
| `is_managerial` | BOOLEAN | NOT NULL | `FALSE` | **Resolves leadership without a circular foreign key** |
| `seniority_rank` | INTEGER | NOT NULL | — | 1–10, higher is more senior. Escalation ordering |
| `can_authorize_line_stop` | BOOLEAN | NOT NULL | `FALSE` | A line-stop recommendation must reach somebody with this |
| `can_authorize_maintenance` | BOOLEAN | NOT NULL | `FALSE` | Who signs off unscheduled intervention |
| `requires_certification` | BOOLEAN | NOT NULL | `FALSE` | Flags roles where lapsed certification blocks assignment |
| `description` | TEXT | NULL | — | Role summary for the Decision Agent |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_worker_role_code` on (`worker_role_code`)

*Check*
- `ck_worker_role_code_format` — matches `^ROL-[A-Z]{2,5}$`
- `ck_worker_role_seniority_range` — `seniority_rank` between 1 and 10
- `ck_worker_role_managerial_can_stop_line` — `is_managerial = FALSE` OR `can_authorize_line_stop = TRUE`. A manager who cannot stop a line is not a useful escalation target
- `ck_worker_role_name_not_blank` — trimmed `role_name` length > 0

*Foreign Keys*

None. Independent lookup.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Child | `master.worker` | One-to-many |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 10 rows. Fixed.

**Retention** — Permanent.

**Notes**

Authority flags are role properties with **no per-person override**, by design. Per-person exceptions would make routing unauditable, and the platform must be able to explain why a given recipient was chosen. `seniority_rank` values need not be unique — several roles legitimately sit at the same level — so no unique constraint is placed on it.

---

### M14. `master.worker`

**Purpose**

A person employed at the plant. The single record of an individual, referenced by every other people-related table.

**Business Description**

**A person exists exactly once in this database.** A maintenance engineer is a worker with maintenance attributes attached through M16; a notification recipient is a worker with delivery preferences attached through M17. Neither repeats a name, an email, or a phone number. If personal data were duplicated, a changed phone number would need several updates and the first missed one would send an urgent breakdown call to a dead number.

`production_line_id` being nullable carries meaning: NULL means plant-wide, which is normal for maintenance, quality, stores, and management staff.

**Primary Key**

`worker_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `worker_id` | INTEGER | NOT NULL | identity | Primary key |
| `worker_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `EMP-nnnn` |
| `first_name` | VARCHAR(60) | NOT NULL | — | Used to address notifications |
| `last_name` | VARCHAR(60) | NOT NULL | — | Family name |
| `worker_role_id` | INTEGER | NOT NULL | — | FK → `master.worker_role` |
| `department_id` | INTEGER | NOT NULL | — | FK → `master.department`. Exactly one |
| `production_line_id` | INTEGER | NULL | — | FK → `master.production_line`. **NULL means plant-wide** |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift`. Default shift |
| `email` | VARCHAR(150) | NULL | — | **Delivery endpoint for email notifications.** Unique when present |
| `phone_number` | VARCHAR(20) | NULL | — | **Delivery endpoint for WhatsApp.** E.164 format |
| `hire_date` | DATE | NOT NULL | — | Employment start |
| `employment_type` | `master.employment_type` | NOT NULL | — | ENUM, 3 values |
| `skill_level` | `master.skill_level` | NOT NULL | — | ENUM, 5 values. **Stored here for all workers, not repeated on M16** |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_worker_code` on (`worker_code`)
- `uq_worker_email` on (`email`) — unique when present; two workers sharing an address would make delivery ambiguous

*Check*
- `ck_worker_code_format` — matches `^EMP-[0-9]{4}$`
- `ck_worker_email_format` — `email` is NULL or matches a basic address pattern
- `ck_worker_phone_e164_format` — `phone_number` is NULL or matches `^\+[1-9][0-9]{7,14}$`
- `ck_worker_first_name_not_blank` — trimmed `first_name` length > 0
- `ck_worker_last_name_not_blank` — trimmed `last_name` length > 0

*Foreign Keys*
- `fk_worker_role` — `worker_role_id` → `master.worker_role(worker_role_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_worker_department` — `department_id` → `master.department(department_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_worker_production_line` — `production_line_id` → `master.production_line(production_line_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_worker_shift` — `shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.worker_role`, `master.department`, `master.production_line`, `master.shift` | Many-to-one each |
| Child | `master.maintenance_engineer` | **One-to-one**, optional |
| Child | `master.notification_recipient` | **One-to-one**, optional |
| Referenced by | 5 operational tables | One-to-many each |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 13 rows in the sample seed; a complete roster is ~105. Grows with headcount.

**Retention** — Permanent. Soft-retired via `is_active` when an employee leaves, because historical maintenance, acknowledgement, and notification records reference them.

**Notes**

**This table holds personal data.** The model deliberately stores no home address, date of birth, identity number, or salary — none serves any FactoryFlow AI use case, and holding data with no consumer is a liability. Part XIII treats this table as the database's primary privacy boundary and specifies column-level access separation for `email` and `phone_number`.

Two conditional rules are cross-table and application-validated per §41.3: only workers in a `maintenance` department may have a M16 row, and a M17 recipient with an enabled channel must have the corresponding endpoint populated here. The second is the more consequential — a recipient with an enabled channel and no endpoint is a **silent** delivery failure, the worst kind because it looks configured.

---

### M15. `master.maintenance_team`

**Purpose**

A maintenance crew defined by specialisation, shift coverage, and response commitment. The unit the Decision Agent assigns work to.

**Business Description**

`specialization` must match the failure, and its ENUM vocabulary is deliberately identical to `machine_category.primary_maintenance_specialization` and `failure_category.required_specialization` — matching a failed machine to a qualified team is a direct value comparison rather than an inference rule. `shift_id` determines availability: a recommendation at 02:00 assigning the day-shift team is not actionable. `is_emergency_response` separates teams that handle breakdowns from those doing planned work only.

**No team lead foreign key exists.** As with M3, a `team_lead_engineer_id` here would create a cycle with M16, which already carries `maintenance_team_id`. `maintenance_engineer.is_team_lead` marks the lead on the child side.

**Primary Key**

`maintenance_team_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `maintenance_team_id` | INTEGER | NOT NULL | identity | Primary key |
| `maintenance_team_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `MTM-XXXX` |
| `team_name` | VARCHAR(100) | NOT NULL | — | Named in recommendations |
| `department_id` | INTEGER | NOT NULL | — | FK → `master.department`. Must be a maintenance department |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift`. **Determines availability** |
| `specialization` | `master.maintenance_specialization` | NOT NULL | — | **Shared ENUM.** Matched against machine category |
| `base_plant_area_id` | INTEGER | NULL | — | FK → `master.plant_area`. NULL when mobile |
| `contact_extension` | VARCHAR(10) | NULL | — | Internal phone. Included in recommendations |
| `max_concurrent_jobs` | INTEGER | NOT NULL | — | Capacity limit, 1–10 |
| `is_emergency_response` | BOOLEAN | NOT NULL | `FALSE` | FALSE teams must not be assigned emergencies |
| `target_response_time_minutes` | INTEGER | NOT NULL | — | **A target, not a measured average.** Actuals live in operational history |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_maintenance_team_code` on (`maintenance_team_code`)

*Check*
- `ck_maintenance_team_code_format` — matches `^MTM-[A-Z]{3,5}$`
- `ck_maintenance_team_max_jobs_range` — `max_concurrent_jobs` between 1 and 10
- `ck_maintenance_team_response_target_range` — `target_response_time_minutes` between 5 and 480
- `ck_maintenance_team_extension_digits` — `contact_extension` is NULL or matches `^[0-9]+$`
- `ck_maintenance_team_name_not_blank` — trimmed `team_name` length > 0

*Foreign Keys*
- `fk_maintenance_team_department` — `department_id` → `master.department(department_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_maintenance_team_shift` — `shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_maintenance_team_base_area` — `base_plant_area_id` → `master.plant_area(plant_area_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.department`, `master.shift`, `master.plant_area` | Many-to-one each |
| Child | `master.maintenance_engineer` | One-to-many |
| Child | `master.machine_maintenance_schedule` | One-to-many, optional |
| Referenced by | `operational.maintenance_work_record`, `operational.ai_recommendation` | One-to-many each |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 4 rows. Grows with maintenance organisation changes.

**Retention** — Permanent.

**Notes**

`department_function = 'maintenance'` on the referenced department is a cross-table rule, application-validated. The **coverage gap check** — that for every specialisation and production shift covering monitored equipment at least one emergency-response team exists — is a data quality report rather than a constraint, and it is genuinely valuable: the sample seed contains a real gap where automation faults have no specialist outside the general shift.

---

### M16. `master.maintenance_engineer`

**Purpose**

The maintenance-specific attributes of a worker: team, discipline, certification, experience, on-call status, and leadership. A **one-to-one specialisation** of `worker`, not a second person record.

**Business Description**

Putting `maintenance_team_id`, `is_team_lead`, and `certification_expiry_date` on `worker` would leave them NULL for most rows and would permit a store keeper to be marked a mechanical team lead. A separate table keeps the columns where they mean something and makes the constraint enforceable.

Equally, a standalone table with its own name and contact fields would duplicate personal data. **This table holds no name, email, or phone** — all of it comes from the parent `worker` row.

`certification_expiry_date` has direct operational consequence: an engineer whose certification has lapsed cannot legally or safely be assigned certain work, so a recommendation naming them would be unsafe rather than merely unhelpful.

**Note on the absent skill level.** `skill_level` lives on `worker` and is not repeated here. What this table adds is *discipline* and *certification*.

**Primary Key**

`maintenance_engineer_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`, with a **unique constraint on `worker_id`** enforcing the one-to-one.

Using `worker_id` directly as the primary key is a legitimate pattern for a strict one-to-one and was considered. A separate surrogate was chosen for consistency with all 52 other tables, and because `maintenance_engineer_code` is a distinct identifier maintenance staff genuinely use in work assignment, separate from the employee number.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `maintenance_engineer_id` | INTEGER | NOT NULL | identity | Primary key |
| `maintenance_engineer_code` | VARCHAR(10) | NOT NULL | — | Business key. Format `ENG-nn` |
| `worker_id` | INTEGER | NOT NULL | — | FK → `master.worker`. **UNIQUE** — enforces one-to-one |
| `maintenance_team_id` | INTEGER | NOT NULL | — | FK → `master.maintenance_team`. Exactly one team |
| `primary_specialization` | `master.maintenance_specialization` | NOT NULL | — | **Shared ENUM** |
| `is_team_lead` | BOOLEAN | NOT NULL | `FALSE` | **Leadership on the child.** Avoids a circular FK on M15 |
| `years_experience` | INTEGER | NOT NULL | — | 0–50 |
| `certification_expiry_date` | DATE | NULL | — | NULL when no certification required. **Lapsed blocks assignment** |
| `is_on_call` | BOOLEAN | NOT NULL | `FALSE` | Determines whether an off-shift specialist is reachable |
| `secondary_specialization` | `master.maintenance_specialization` | NULL | — | Cross-trained second discipline. Widens the available pool |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_maintenance_engineer_code` on (`maintenance_engineer_code`)
- `uq_maintenance_engineer_worker` on (`worker_id`) — **the one-to-one enforcement**
- `uq_maintenance_engineer_team_lead` — a **partial** unique index on (`maintenance_team_id`) where `is_team_lead = TRUE AND is_active`. Exactly one lead per team: none leaves the team without an accountable contact, two produce contradictory assignment

*Check*
- `ck_maintenance_engineer_code_format` — matches `^ENG-[0-9]{2}$`
- `ck_maintenance_engineer_experience_range` — `years_experience` between 0 and 50
- `ck_maintenance_engineer_specializations_differ` — `secondary_specialization` IS NULL OR `secondary_specialization <> primary_specialization`

*Foreign Keys*
- `fk_maintenance_engineer_worker` — `worker_id` → `master.worker(worker_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_maintenance_engineer_team` — `maintenance_team_id` → `master.maintenance_team(maintenance_team_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.worker` | **One-to-one** |
| Parent | `master.maintenance_team` | Many-to-one |
| Referenced by | `operational.maintenance_work_record`, `operational.ai_recommendation` | One-to-many each |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 5 rows. Grows with the maintenance team.

**Retention** — Permanent. Cannot be soft-retired while `is_team_lead = TRUE`; leadership must be reassigned first.

**Notes**

An engineer-to-machine-category qualification junction was considered and rejected in the frozen master model §35: specialisation matching already resolves qualification, and the junction would add a table with no consumer. Part XII records it as a Phase 2 candidate requiring no restructuring of this table.

---

### M17. `master.notification_recipient`

**Purpose**

Defines who receives notifications, on which channels, at what severity, for which scope, and how often. The configuration connecting a finished recommendation to a specific person.

**Business Description**

`min_severity_level_id` implements graduated escalation, the main defence against alert fatigue: a line supervisor wants medium-severity conditions on their own line, the plant manager wants only critical ones. `scope_production_line_id` limits a recipient to one line — NULL means plant-wide. `max_notifications_per_hour` is a hard rate limit, because a flapping condition would otherwise flood a recipient and effectively disable the channel.

**No contact details are stored here.** Addresses and phone numbers live on `worker`. This table stores only *whether* a channel is enabled, so a changed phone number is one update in one place with no possibility of an urgent recommendation going to a stale number that exists only in notification configuration.

**Primary Key**

`notification_recipient_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`, with a **unique constraint on `worker_id`** enforcing one-to-one.

**No business code.** This table has no `_code` column: it is configuration attached to exactly one worker, and `worker_code` already identifies it unambiguously.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `notification_recipient_id` | INTEGER | NOT NULL | identity | Primary key |
| `worker_id` | INTEGER | NOT NULL | — | FK → `master.worker`. **UNIQUE.** Endpoints resolve through here |
| `min_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level`. **Primary defence against alert fatigue** |
| `email_enabled` | BOOLEAN | NOT NULL | `TRUE` | Requires non-NULL `worker.email` |
| `whatsapp_enabled` | BOOLEAN | NOT NULL | `FALSE` | Requires non-NULL `worker.phone_number` |
| `scope_production_line_id` | INTEGER | NULL | — | FK → `master.production_line`. **NULL means plant-wide** |
| `notify_outside_shift_hours` | BOOLEAN | NOT NULL | `FALSE` | FALSE respects off-duty time |
| `escalation_order` | INTEGER | NOT NULL | — | Contact order within a scope. Lower first |
| `max_notifications_per_hour` | INTEGER | NULL | — | NULL means unlimited. 1–60 |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_notification_recipient_worker` on (`worker_id`) — the one-to-one enforcement

*Check*
- `ck_notification_recipient_channel_enabled` — `email_enabled = TRUE` OR `whatsapp_enabled = TRUE`. **A recipient with both channels disabled is unreachable, and a configured-but-unreachable recipient is worse than none because it looks correct**
- `ck_notification_recipient_escalation_order_positive` — `escalation_order` > 0
- `ck_notification_recipient_rate_limit_range` — `max_notifications_per_hour` is NULL or between 1 and 60

*Foreign Keys*
- `fk_notification_recipient_worker` — `worker_id` → `master.worker(worker_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_notification_recipient_severity` — `min_severity_level_id` → `master.failure_severity_level(failure_severity_level_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_notification_recipient_scope_line` — `scope_production_line_id` → `master.production_line(production_line_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.worker` | **One-to-one** |
| Parent | `master.failure_severity_level`, `master.production_line` | Many-to-one each |
| Referenced by | `operational.notification` | One-to-many |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 5 rows. Grows with notification policy changes.

**Retention** — Permanent.

**Notes**

`ck_notification_recipient_channel_enabled` is enforceable in-row. The channel-to-endpoint rule is not — it depends on `worker.email` and `worker.phone_number` — and is application-validated at configuration time rather than discovered at send time.

The most important completeness rule for this table cannot be a constraint at all: **at least one active recipient must exist with the most severe `min_severity_level_id` and `notify_outside_shift_hours = TRUE`.** Without it a critical failure at 03:00 reaches nobody. It is a seed validation check per §41.3 and gates simulator start per §7.

---

## Group E — Materials & Partners (M18–M22)

---

### M18. `master.inventory_location`

**Purpose**

A physical storage location within the plant. Answers *where is this material, and how long does it take to get it*.

**Business Description**

`average_retrieval_time_minutes` is the column that matters most: **retrieval time is part of repair time**, so an honest downtime estimate is retrieval plus repair. A part 15 minutes away is not the same as one in a crib 4 minutes from the machine. `stock_count_frequency_days` addresses a subtler problem — stock records are not always right, and a store counted quarterly may have drifted, so the Decision Agent can qualify its own availability claim rather than asserting certainty it does not have.

**Access restriction is not stored here.** It lives on `plant_area` and applies to everything inside the area; duplicating it would let a location claim open access inside a restricted area.

**Primary Key**

`inventory_location_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `inventory_location_id` | INTEGER | NOT NULL | identity | Primary key |
| `inventory_location_code` | VARCHAR(16) | NOT NULL | — | Business key. Format `LOC-XX-XXXX` |
| `location_name` | VARCHAR(100) | NOT NULL | — | Shown in recommendations |
| `plant_area_id` | INTEGER | NOT NULL | — | FK → `master.plant_area`. Access rules inherited from here |
| `location_type` | `master.inventory_location_type` | NOT NULL | — | ENUM, 6 values |
| `capacity_slots` | INTEGER | NULL | — | NULL when unbounded, as for open floor buffers |
| `is_temperature_controlled` | BOOLEAN | NOT NULL | `FALSE` | Required for heat-sensitive shelf life |
| `average_retrieval_time_minutes` | INTEGER | NOT NULL | — | **Added to repair time for an honest downtime estimate** |
| `stock_count_frequency_days` | INTEGER | NULL | — | NULL for locations not cycle-counted. **Qualifies confidence in the balance** |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_inventory_location_code` on (`inventory_location_code`)

*Check*
- `ck_inventory_location_code_format` — matches `^LOC-[A-Z]{2}-[A-Z0-9]{1,4}$`
- `ck_inventory_location_capacity_positive` — `capacity_slots` is NULL or > 0
- `ck_inventory_location_retrieval_range` — `average_retrieval_time_minutes` between 1 and 240
- `ck_inventory_location_count_frequency_range` — `stock_count_frequency_days` is NULL or between 1 and 365
- `ck_inventory_location_name_not_blank` — trimmed `location_name` length > 0

*Foreign Keys*
- `fk_inventory_location_plant_area` — `plant_area_id` → `master.plant_area(plant_area_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.plant_area` | Many-to-one |
| Child | `master.inventory_item` | One-to-many |
| Referenced by | `operational.inventory_movement` | One-to-many |

**Read Components** — Simulator, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — 5 rows. Fixed unless storage is reorganised.

**Retention** — Permanent.

**Notes**

Location-type to area-type alignment and the rule that `wip_buffer` locations carry NULL `stock_count_frequency_days` are both cross-table or conditional rules, application-validated per §41.3.

---

### M19. `master.inventory_item`

**Purpose**

A material, component, consumable, spare part, or tool the plant stocks. Carries the **stocking policy** and sourcing information needed to answer whether a repair can proceed.

**Business Description**

**The master/operational boundary is at its sharpest in this table.** It holds policy — `reorder_point`, `safety_stock_qty`, `max_stock_qty`, `lead_time_days` — and holds **no quantity on hand.** Current stock changes with every issue and receipt, making it operational; it is the running balance on `operational.inventory_movement`. Availability is answered by comparing an operational balance against these master thresholds.

`is_critical_spare` marks parts whose absence stops a machine, and it is **independent of cost**: a cheap drive belt can idle a machining centre as effectively as an expensive servo module.

**No machine-type link exists here.** The relationship the platform needs is more specific — *which failure mode on which machine type requires which part* — and that belongs on M26, where one part can serve several failure modes.

**Primary Key**

`inventory_item_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `inventory_item_id` | INTEGER | NOT NULL | identity | Primary key |
| `inventory_item_code` | VARCHAR(24) | NOT NULL | — | Business key. Format `INV-XX-...` |
| `item_name` | VARCHAR(150) | NOT NULL | — | Appears verbatim in recommendations |
| `item_type` | `master.inventory_item_type` | NOT NULL | — | ENUM, 6 values |
| `unit_of_measure` | `master.unit_of_measure` | NOT NULL | — | **Shared ENUM, all 6 values valid here** including `BOX` |
| `unit_cost` | NUMERIC(12,2) | NOT NULL | — | Standard cost in `plant.currency_code` |
| `reorder_point` | NUMERIC(12,2) | NOT NULL | — | **Master policy, not a live quantity** |
| `safety_stock_qty` | NUMERIC(12,2) | NOT NULL | — | Buffer against demand and lead-time variability |
| `max_stock_qty` | NUMERIC(12,2) | NOT NULL | — | Upper stocking limit |
| `lead_time_days` | INTEGER | NOT NULL | — | **Converts a stockout into a concrete delay** |
| `primary_supplier_id` | INTEGER | NULL | — | FK → `master.supplier`. NULL for internally produced items |
| `default_inventory_location_id` | INTEGER | NOT NULL | — | FK → `master.inventory_location`. Determines retrieval time |
| `is_critical_spare` | BOOLEAN | NOT NULL | `FALSE` | **Independent of cost** |
| `abc_class` | CHAR(1) | NOT NULL | — | `A`, `B`, or `C`. Standard value classification |
| `shelf_life_days` | INTEGER | NULL | — | NULL for non-perishable, the normal case |
| `specification` | TEXT | NULL | — | Technical description precise enough for a storekeeper to confirm the part |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_inventory_item_code` on (`inventory_item_code`)

*Check*
- `ck_inventory_item_code_format` — matches `^INV-[A-Z]{2}-[A-Z0-9-]{2,14}$`
- `ck_inventory_item_abc_class_allowed` — `abc_class` in (`A`, `B`, `C`)
- `ck_inventory_item_unit_cost_positive` — `unit_cost` > 0
- `ck_inventory_item_stock_thresholds_ordered` — `safety_stock_qty` <= `reorder_point` AND `reorder_point` < `max_stock_qty`. **Any violation makes replenishment logic incoherent**
- `ck_inventory_item_safety_stock_non_negative` — `safety_stock_qty` >= 0
- `ck_inventory_item_lead_time_non_negative` — `lead_time_days` >= 0
- `ck_inventory_item_critical_spare_has_buffer` — `is_critical_spare = FALSE` OR `safety_stock_qty` > 0. A critical spare with no buffer defeats its classification
- `ck_inventory_item_shelf_life_positive` — `shelf_life_days` is NULL or > 0
- `ck_inventory_item_name_not_blank` — trimmed `item_name` length > 0

*Foreign Keys*
- `fk_inventory_item_supplier` — `primary_supplier_id` → `master.supplier(supplier_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_inventory_item_location` — `default_inventory_location_id` → `master.inventory_location(inventory_location_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.supplier`, `master.inventory_location` | Many-to-one each |
| Child | `master.bill_of_materials` | One-to-many |
| Referenced by | `master.machine_type_failure_mode`, `master.machine_maintenance_schedule` | One-to-many each |
| Referenced by | `operational.inventory_movement`, `operational.operational_event`, `operational.operational_alert`, `operational.ai_recommendation` | One-to-many each |

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 11 rows. Grows with the parts catalogue.

**Retention** — Permanent.

**Notes**

`ck_inventory_item_stock_thresholds_ordered` is a three-way ordering in a single check constraint, which PostgreSQL handles directly. It is the highest-value constraint on this table because every replenishment and shortage decision in the platform depends on the ordering holding.

`abc_class` is `CHAR(1)` with a check rather than an ENUM. A three-value single-character classification is a case where a check constraint is lighter than a type declaration and equally safe, and §37.4 records the general rule.

---

### M20. `master.bill_of_materials`

**Purpose**

The materials consumed to produce one unit of a product, with quantity, scrap allowance, criticality, and approved substitute. Links production activity to material consumption.

**Business Description**

Without a bill of materials the simulator would have no basis for depleting stock, inventory would stay static, and inventory monitoring would have nothing to monitor. `is_critical_component` distinguishes materials that halt production from those that merely degrade it. `substitute_inventory_item_id` turns a shortage from a stoppage into a documented workaround the Decision Agent can propose.

**No versioned header table exists.** The conventional ERP structure — versioned header plus component lines — was considered and excluded in the frozen master model §34.13. Versioning exists so an ERP can reproduce the recipe used for a batch two years ago, which **no FactoryFlow AI component requires**. `effective_from_date` provides the audit trail of recipe change without a join, a version-resolution rule, and an active-version constraint.

**No unit of measure column.** Quantity is expressed in the referenced item's `unit_of_measure`. Repeating it would allow the two to disagree.

**Primary Key**

`bill_of_materials_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`, with a composite unique constraint on the natural pair. No business code: nobody names an individual BOM line.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `bill_of_materials_id` | INTEGER | NOT NULL | identity | Primary key |
| `product_id` | INTEGER | NOT NULL | — | FK → `master.product` |
| `inventory_item_id` | INTEGER | NOT NULL | — | FK → `master.inventory_item`. Must not be a `finished_good` |
| `quantity_per_unit` | NUMERIC(12,4) | NOT NULL | — | In the item's own unit. Fractional values are normal for consumables and tooling |
| `scrap_allowance_pct` | NUMERIC(5,2) | NOT NULL | `0` | Keeps material projections honest rather than systematically optimistic |
| `is_critical_component` | BOOLEAN | NOT NULL | `FALSE` | **Distinguishes a stoppage from a slowdown** |
| `substitute_inventory_item_id` | INTEGER | NULL | — | FK → `master.inventory_item`. Approved alternate |
| `effective_from_date` | DATE | NOT NULL | — | Audit trail of recipe change |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_bill_of_materials_pair` on (`product_id`, `inventory_item_id`)

*Check*
- `ck_bom_quantity_positive` — `quantity_per_unit` > 0
- `ck_bom_scrap_allowance_range` — `scrap_allowance_pct` between 0 and 50
- `ck_bom_substitute_differs` — `substitute_inventory_item_id` IS NULL OR `substitute_inventory_item_id <> inventory_item_id`

*Foreign Keys*
- `fk_bom_product` — `product_id` → `master.product(product_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_bom_inventory_item` — `inventory_item_id` → `master.inventory_item(inventory_item_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_bom_substitute_item` — `substitute_inventory_item_id` → `master.inventory_item(inventory_item_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.product` | Many-to-one |
| Parent | `master.inventory_item` | Many-to-one, **twice** — as material and as substitute |

Resolves **product ↔ inventory_item as many-to-many**. The double reference to `inventory_item` from one table is legitimate and creates no cycle, since `inventory_item` does not reference `bill_of_materials`. Both columns are role-qualified in their names.

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only

**Growth** — 10 rows. Grows as products × components.

**Retention** — Permanent.

**Notes**

Two rules are cross-table and application-validated: `inventory_item_id` must not reference an item of type `finished_good` — sub-assembly explosion is excluded because no consumer needs recursive traversal — and a substitute should share the referenced item's `unit_of_measure`, since a differently-measured substitute produces wrong quantity arithmetic.

---

### M21. `master.supplier`

**Purpose**

An external source of materials, components, or spare parts. Carries the sourcing facts that determine how quickly a shortage can be resolved.

**Business Description**

Primarily an answer to *how fast can we get the part, and can we trust that estimate*. `standard_lead_time_days` turns a stockout into a dated delay. `expedited_lead_time_days` is the recovery lever — often the single most valuable element of a recovery plan when a critical spare is unavailable. `on_time_delivery_pct` qualifies the promise, letting the Decision Agent hedge honestly rather than presenting a lead time as a guarantee.

**`on_time_delivery_pct` is master data, not a computed metric.** It is a **periodically maintained scorecard value**, exactly as a real ERP holds it: purchasing reviews supplier performance quarterly and updates the rating. It changes a few times a year, set by a human, and it is explicitly **never** recomputed by the platform from operational receipts.

**No supplier-item multi-sourcing junction exists.** Excluded in the frozen master model §34: it would enable alternate-source recommendations, which no current consumer requires, and `inventory_item.primary_supplier_id` answers the question actually asked.

**Primary Key**

`supplier_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `supplier_id` | INTEGER | NOT NULL | identity | Primary key |
| `supplier_code` | VARCHAR(10) | NOT NULL | — | Business key. Format `SUP-nnn` |
| `supplier_name` | VARCHAR(150) | NOT NULL | — | Named in ordering recommendations |
| `supplier_type` | `master.supplier_type` | NOT NULL | — | ENUM, 5 values |
| `contact_person` | VARCHAR(100) | NULL | — | NULL when only a general channel exists |
| `contact_email` | VARCHAR(150) | NULL | — | Ordering address |
| `contact_phone` | VARCHAR(20) | NULL | — | **Needed for an expedited order**, normally placed by phone |
| `city` | VARCHAR(80) | NOT NULL | — | Correlates with transit time |
| `country_code` | CHAR(2) | NOT NULL | — | ISO 3166-1 alpha-2 |
| `standard_lead_time_days` | INTEGER | NOT NULL | — | Normal replenishment time |
| `expedited_lead_time_days` | INTEGER | NULL | — | NULL when the supplier does not expedite |
| `reliability_rating` | NUMERIC(2,1) | NOT NULL | — | 0.0–5.0 composite judgement |
| `on_time_delivery_pct` | NUMERIC(5,2) | NULL | — | Scorecard figure. NULL for new suppliers |
| `is_approved_vendor` | BOOLEAN | NOT NULL | `TRUE` | FALSE blocks ordering entirely |
| `contract_expiry_date` | DATE | NULL | — | NULL for spot purchasing |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_supplier_code` on (`supplier_code`)

*Check*
- `ck_supplier_code_format` — matches `^SUP-[0-9]{3}$`
- `ck_supplier_country_code_format` — matches `^[A-Z]{2}$`
- `ck_supplier_lead_time_non_negative` — `standard_lead_time_days` >= 0
- `ck_supplier_expedited_faster` — `expedited_lead_time_days` IS NULL OR `expedited_lead_time_days` < `standard_lead_time_days`. **An expedited option no faster than standard is not an expedited option**
- `ck_supplier_reliability_range` — `reliability_rating` between 0.0 and 5.0
- `ck_supplier_otd_range` — `on_time_delivery_pct` is NULL or between 0 and 100
- `ck_supplier_email_format` — `contact_email` is NULL or matches a basic address pattern
- `ck_supplier_name_not_blank` — trimmed `supplier_name` length > 0

*Foreign Keys*

None. Independent reference table at the base of the master dependency graph.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Child | `master.inventory_item` | One-to-many |
| Referenced by | `operational.inventory_movement` | One-to-many |

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 4 rows. Grows with the vendor base.

**Retention** — Permanent. Cannot be soft-retired while active items designate them as primary source; an item left with no source cannot be replenished at all.

**Notes**

`reliability_rating` is `NUMERIC(2,1)` — one integer digit and one decimal, covering 0.0 to 9.9 with the check constraint narrowing to 5.0. This is precise sizing rather than a generic numeric, consistent with the frozen specification.

---

### M22. `master.customer`

**Purpose**

An external buyer of finished goods. Supplies the commercial weighting that turns lost output into a prioritised business consequence.

**Business Description**

`priority_tier` is this table's reason for existing. When two lines are at risk simultaneously the platform must prioritise, and without customer tiering it could rank by units or margin but not by **relationship consequence** — which is often what actually drives the decision. `late_delivery_penalty_per_day` makes disruption cost contractual rather than estimated. `contractual_otd_target_pct` records how little slack exists before an agreement is breached.

**No master customer-to-product table exists.** Which customer buys which product is commercial and changes with every order, making it transactional. Modelling it as master data would produce a table that is either constantly wrong or constantly updated, and no consumer needs it — the Decision Agent learns the customer from the affected run.

**Primary Key**

`customer_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `customer_id` | INTEGER | NOT NULL | identity | Primary key |
| `customer_code` | VARCHAR(10) | NOT NULL | — | Business key. Format `CUS-nnn` |
| `customer_name` | VARCHAR(150) | NOT NULL | — | Named in business impact statements |
| `priority_tier` | `master.customer_priority_tier` | NOT NULL | — | ENUM, 3 values. **Primary input when prioritising between disruptions** |
| `industry_sector` | VARCHAR(80) | NULL | — | Context for delay tolerance |
| `city` | VARCHAR(80) | NOT NULL | — | Delivery location |
| `country_code` | CHAR(2) | NOT NULL | — | Domestic versus export |
| `contact_person` | VARCHAR(100) | NULL | — | NULL when only a general channel exists |
| `contact_email` | VARCHAR(150) | NULL | — | Relevant when a recovery plan includes informing the customer |
| `late_delivery_penalty_per_day` | NUMERIC(12,2) | NULL | — | NULL when no penalty clause. **Converts a delay into contractual cash** |
| `contractual_otd_target_pct` | NUMERIC(5,2) | NULL | — | NULL when uncontracted. Indicates available slack |
| `annual_order_value` | NUMERIC(14,2) | NULL | — | Gives `priority_tier` a magnitude rather than only a label |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_customer_code` on (`customer_code`)

*Check*
- `ck_customer_code_format` — matches `^CUS-[0-9]{3}$`
- `ck_customer_country_code_format` — matches `^[A-Z]{2}$`
- `ck_customer_penalty_non_negative` — `late_delivery_penalty_per_day` is NULL or >= 0
- `ck_customer_otd_target_range` — `contractual_otd_target_pct` is NULL or between 0 and 100
- `ck_customer_annual_value_positive` — `annual_order_value` is NULL or > 0
- `ck_customer_email_format` — `contact_email` is NULL or matches a basic address pattern
- `ck_customer_name_not_blank` — trimmed `customer_name` length > 0

*Foreign Keys*

None. A pure reference table with **no outbound foreign keys and no master children** — the only master table whose entire value is realised through the operational layer.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Referenced by | `operational.production_run` | One-to-many |

**Read Components** — Simulator, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 3 rows. Grows with the customer base.

**Retention** — Permanent.

**Notes**

`annual_order_value` is `NUMERIC(14,2)` — wider than other monetary columns because it represents an annual aggregate rather than a per-unit figure, and 12 digits of precision would cap at 10 billion in minor units.

The platform **never contacts a customer automatically.** `contact_email` supports a recovery plan that recommends informing them; the act is a commercial decision belonging to a human, consistent with the human-in-the-loop principle.

---

## Group F — Reliability & Policy (M23–M29)

---

### M23. `master.failure_severity_level`

**Purpose**

The severity scale used throughout the platform, with response commitment and escalation policy attached to each level.

**Business Description**

The most widely referenced concept in the platform: failure categories carry a default severity, failure modes a type-specific one, threshold rules produce warning and critical severities, recipients filter by minimum severity, and escalation policy is expressed in severity terms. Six operational tables and three master tables reference it.

Making severity a table rather than an ENUM is what allows each level to carry **policy**: `target_response_time_minutes` converts a label into a deadline, `requires_line_stop` tells the Decision Agent that stopping the line belongs in the recommended action, and `max_acknowledgement_minutes` drives the escalation clock.

**Primary Key**

`failure_severity_level_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `failure_severity_level_id` | INTEGER | NOT NULL | identity | Primary key |
| `failure_severity_level_code` | VARCHAR(8) | NOT NULL | — | Business key. Format `SEV-n` |
| `severity_name` | VARCHAR(40) | NOT NULL | — | Appears verbatim in recommendations |
| `severity_rank` | INTEGER | NOT NULL | — | **1 is most severe.** Unique. Makes "at or above" a numeric test |
| `description` | TEXT | NOT NULL | — | **Given to the Decision Agent** so severity assignment is grounded in a definition rather than the model's reading of a label |
| `target_response_time_minutes` | INTEGER | NULL | — | NULL for informational levels needing no response |
| `requires_line_stop` | BOOLEAN | NOT NULL | `FALSE` | The platform recommends; it never stops a line itself |
| `requires_immediate_escalation` | BOOLEAN | NOT NULL | `FALSE` | Bypasses batching and quiet-hours windows |
| `requires_manager_acknowledgement` | BOOLEAN | NOT NULL | `FALSE` | The human-in-the-loop audit trail |
| `max_acknowledgement_minutes` | INTEGER | NULL | — | Required when acknowledgement is required |
| `display_color_hex` | CHAR(7) | NOT NULL | — | Defined once so dashboard, email, and reports cannot diverge |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_failure_severity_level_code` on (`failure_severity_level_code`)
- `uq_failure_severity_level_rank` on (`severity_rank`)

*Check*
- `ck_severity_code_format` — matches `^SEV-[0-9]$`
- `ck_severity_rank_range` — `severity_rank` between 1 and 9
- `ck_severity_response_time_positive` — `target_response_time_minutes` is NULL or > 0
- `ck_severity_ack_minutes_required` — `requires_manager_acknowledgement = FALSE` OR `max_acknowledgement_minutes` IS NOT NULL. **An acknowledgement requirement with no timeout would stall escalation indefinitely**
- `ck_severity_ack_minutes_positive` — `max_acknowledgement_minutes` is NULL or > 0
- `ck_severity_color_hex_format` — matches `^#[0-9A-F]{6}$`
- `ck_severity_name_not_blank` — trimmed `severity_name` length > 0
- `ck_severity_description_not_blank` — trimmed `description` length > 0

*Foreign Keys*

None. Independent lookup and the most widely referenced table in the database.

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Child | `master.failure_category`, `master.machine_type_failure_mode`, `master.alert_threshold_rule` (×2), `master.notification_recipient` | One-to-many each |
| Referenced by | 6 operational tables | One-to-many each |

**Read Components** — All eight components

**Write Components** — Administrative only

**Growth** — 5 rows. Effectively immutable — adding a level mid-project would require re-evaluating every threshold rule, failure category, and recipient filter referencing the scale.

**Retention** — Permanent. Never deleted; historical events and recommendations reference these rows indefinitely.

**Notes**

**`severity_rank = 1` is the most severe**, and the direction is stated explicitly in the column note because reversing it silently inverts every comparison in the platform. The `uq_failure_severity_level_rank` unique constraint prevents two levels claiming the same position.

*"`target_response_time_minutes` must increase as severity decreases"* is a cross-row rule that no table constraint can express. It is a seed validation check per §41.3.

---

### M24. `master.failure_category`

**Purpose**

The controlled vocabulary of failure modes the platform can reason about. Constrains the Decision Agent's root-cause hypotheses to a known, reviewable set.

**Business Description**

This table is the platform's failure vocabulary, and it exists for a reason central to the architecture. `PROJECT_OVERVIEW.md` §16.5 makes root cause a mandatory element of every recommendation. Left to free generation, an LLM produces plausible-sounding causes that may not correspond to how the equipment actually fails. Constraining root cause to a defined vocabulary means the hypothesis is always drawn from a set a maintenance engineer has endorsed — **converting LLM root-cause reasoning from open-ended generation into classification within a validated set.**

`required_specialization` maps a failure directly to a maintenance discipline using the shared ENUM. `has_safety_implication` raises urgency independently of production impact.

**No repair duration column.** Repair duration is held in exactly two places with a stated precedence: `machine_type_failure_mode.estimated_repair_duration_minutes` when the failure mode is identified, and `machine_type.mttr_minutes` as the fallback. A third category-level average would be a middle layer nobody would know when to use.

**Primary Key**

`failure_category_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `failure_category_id` | INTEGER | NOT NULL | identity | Primary key |
| `failure_category_code` | VARCHAR(10) | NOT NULL | — | Business key. Format `FC-XXXX` |
| `category_name` | VARCHAR(100) | NOT NULL | — | **Appears verbatim as the root cause in recommendations** |
| `failure_domain` | `master.failure_domain` | NOT NULL | — | ENUM, 9 values. Groups correlated evidence |
| `default_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level` |
| `required_specialization` | `master.maintenance_specialization` | NOT NULL | — | **Shared ENUM.** Matched against team specialisation |
| `requires_spare_part` | BOOLEAN | NOT NULL | `FALSE` | TRUE means the recovery plan must include a stock check |
| `has_safety_implication` | BOOLEAN | NOT NULL | `FALSE` | **Raises urgency independently of production impact** |
| `description` | TEXT | NOT NULL | — | **Grounds the Decision Agent's hypothesis in engineering fact.** A thin description directly degrades hypothesis quality |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_failure_category_code` on (`failure_category_code`)

*Check*
- `ck_failure_category_code_format` — matches `^FC-[A-Z]{3,6}$`
- `ck_failure_category_name_not_blank` — trimmed `category_name` length > 0
- `ck_failure_category_description_not_blank` — trimmed `description` length > 0. **Mandatory and substantive** — this text is a functional input to the LLM, not commentary

*Foreign Keys*
- `fk_failure_category_default_severity` — `default_severity_level_id` → `master.failure_severity_level(failure_severity_level_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.failure_severity_level` | Many-to-one |
| Child | `master.machine_type_failure_mode` | One-to-many |
| Referenced by | `operational.quality_inspection_result`, `operational.scrap_record`, `operational.maintenance_work_record` (×2), `operational.prediction_result`, `operational.ai_recommendation` | One-to-many each |

**Read Components** — Simulator, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 12 rows. Grows deliberately, with engineering review, not to accommodate a single unexplained event.

**Retention** — Permanent. Never deleted; historical predictions and recommendations reference these rows.

**Notes**

`ck_failure_category_description_not_blank` deserves emphasis. In most schemas a not-blank check on a description column is hygiene. Here the description is **supplied to the Decision Agent as grounding**, so an empty or thin one measurably degrades root-cause quality. This is one of very few places in any database where documentation text is a functional input.

---

### M25. `master.machine_type_failure_mode`

**Purpose**

Declares which failure modes are plausible on which machine type, with the telemetry signature preceding each, the spare part consumed, the repair duration, and the expected warning period.

**Business Description**

Makes root-cause reasoning specific rather than generic. A generic catalogue says bearing degradation exists; this table says *bearing degradation on a VMC-500 presents as progressive vibration increase at constant speed, requires part `INV-CP-BRG-6205`, takes about 240 minutes, and typically gives around a week of warning.*

`typical_warning_period_hours` converts a prediction into a plan: 168 hours of warning can be scheduled into the next window, 8 hours must be handled this shift — same probability, entirely different action. `is_model_predictable` is an honesty mechanism preventing the platform from implying it can forecast a failure nothing precedes.

**This is where the failure-to-part link lives**, rather than on `inventory_item`, because the relationship the platform needs is failure-mode-specific and one part can serve several modes.

**Primary Key**

`machine_type_failure_mode_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`, with a composite unique constraint on the natural pair. No business code.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_type_failure_mode_id` | INTEGER | NOT NULL | identity | Primary key |
| `machine_type_id` | INTEGER | NOT NULL | — | FK → `master.machine_type` |
| `failure_category_id` | INTEGER | NOT NULL | — | FK → `master.failure_category` |
| `typical_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level`. **Overrides the category default per type** |
| `primary_machine_parameter_id` | INTEGER | NULL | — | FK → `master.machine_parameter`. NULL when no telemetry precursor |
| `required_inventory_item_id` | INTEGER | NULL | — | FK → `master.inventory_item`. **The authoritative failure-to-part link** |
| `leading_indicator_description` | TEXT | NOT NULL | — | The telemetry signature in engineering language |
| `estimated_repair_duration_minutes` | INTEGER | NOT NULL | — | **The authoritative repair estimate** when the mode is identified |
| `typical_warning_period_hours` | INTEGER | NULL | — | NULL when the failure gives no warning |
| `is_model_predictable` | BOOLEAN | NOT NULL | `FALSE` | **FALSE prevents implying forecast capability that does not exist** |
| `relative_frequency` | `master.relative_frequency` | NOT NULL | — | ENUM, 3 values. Maintained engineering judgement, never computed |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_mtfm_pair` on (`machine_type_id`, `failure_category_id`)

*Check*
- `ck_mtfm_repair_duration_positive` — `estimated_repair_duration_minutes` > 0
- `ck_mtfm_warning_period_positive` — `typical_warning_period_hours` is NULL or > 0
- `ck_mtfm_unpredictable_has_no_warning` — `is_model_predictable = TRUE` OR `typical_warning_period_hours` IS NULL. **An unpredictable failure cannot have a warning period**
- `ck_mtfm_predictable_has_indicator` — `is_model_predictable = FALSE` OR `primary_machine_parameter_id` IS NOT NULL. A failure claimed predictable must have a telemetry signal
- `ck_mtfm_leading_indicator_not_blank` — trimmed `leading_indicator_description` length > 0

*Foreign Keys*
- `fk_mtfm_machine_type` — `machine_type_id` → `master.machine_type(machine_type_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mtfm_failure_category` — `failure_category_id` → `master.failure_category(failure_category_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mtfm_severity` — `typical_severity_level_id` → `master.failure_severity_level(failure_severity_level_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mtfm_parameter` — `primary_machine_parameter_id` → `master.machine_parameter(machine_parameter_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mtfm_inventory_item` — `required_inventory_item_id` → `master.inventory_item(inventory_item_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.machine_type`, `master.failure_category`, `master.failure_severity_level`, `master.machine_parameter`, `master.inventory_item` | Many-to-one each |
| Referenced by | `operational.prediction_result` | One-to-many |

Resolves **machine_type ↔ failure_category as many-to-many**. With **five parents** this is the most connected table in the master schema, and the connectivity is the point: it is the single join where equipment, failure taxonomy, severity policy, telemetry, and inventory meet — exactly what the Decision Agent needs from one lookup.

**Read Components** — Simulator, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — ~22 rows. Grows as machine types × plausible failure modes.

**Retention** — Permanent.

**Notes**

Two of the five check constraints on this table implement the honesty rules from the frozen model, and both are worth stating as constraints rather than conventions. A row claiming predictability without an indicator, or claiming a warning period on an unpredictable failure, would produce a prediction the platform cannot actually support. The stronger version of `ck_mtfm_predictable_has_indicator` — that the indicator must also be flagged `is_ml_feature` on M12 — is cross-table and application-validated.

---

### M26. `master.machine_maintenance_schedule`

**Purpose**

The planned maintenance policy per machine: what work, how often, how long, whether it stops the line, who performs it, and whether it can be deferred.

**Business Description**

One of the most valuable pieces of context the Supervisor Agent can supply. Two situations look identical in telemetry and are entirely different in reality: a machine showing rising vibration serviced last week, and one showing the same rising vibration 200 operating hours **overdue** for that service.

`can_be_deferred` and `max_deferral_days` make recovery planning realistic. A preventive service can usually slip two weeks; a regulatory calibration cannot slip at all. Without these the Decision Agent would either never propose deferral or propose deferring something that legally cannot be deferred.

**The critical boundary decision: no `next_due_date` column.** This table stores `baseline_start_date` — an immutable anchor — and nothing else about timing. `last_performed_date` and `next_due_date` are **deliberately absent** because both are derived from operational maintenance completion history. Caching them would create two sources of truth, and cached due dates go stale when an update fails or a completion is recorded late. In a predictive maintenance platform, being wrong about whether maintenance is overdue is being wrong about the exact thing the platform exists to get right.

Due status is computed by the Supervisor Agent from this table plus `operational.machine_operational_status` counters and closed `operational.maintenance_work_record` rows.

**Primary Key**

`machine_maintenance_schedule_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_maintenance_schedule_id` | INTEGER | NOT NULL | identity | Primary key |
| `machine_maintenance_schedule_code` | VARCHAR(12) | NOT NULL | — | Business key. Format `SCH-nnnn` |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `maintenance_type` | `master.maintenance_type` | NOT NULL | — | ENUM, 5 values |
| `interval_basis` | `master.interval_basis` | NOT NULL | — | ENUM, 3 values. **Operating-hour intervals depend on usage**, which is why due status must be computed |
| `interval_value` | INTEGER | NOT NULL | — | Interval length in the units of `interval_basis` |
| `estimated_duration_minutes` | INTEGER | NOT NULL | — | Lets the Decision Agent judge whether the work fits a window |
| `requires_line_stop` | BOOLEAN | NOT NULL | `FALSE` | Determines whether a planned window is needed |
| `assigned_maintenance_team_id` | INTEGER | NULL | — | FK → `master.maintenance_team`. NULL when unassigned |
| `required_inventory_item_id` | INTEGER | NULL | — | FK → `master.inventory_item`. Lets the platform confirm the task can be performed |
| `baseline_start_date` | DATE | NOT NULL | — | **Immutable anchor.** The only date stored |
| `can_be_deferred` | BOOLEAN | NOT NULL | `TRUE` | **FALSE for regulatory or safety-mandated work** |
| `max_deferral_days` | INTEGER | NULL | — | Must be NULL when not deferrable |
| `task_summary` | TEXT | NULL | — | What the work involves |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_machine_maintenance_schedule_code` on (`machine_maintenance_schedule_code`)

*Check*
- `ck_mms_code_format` — matches `^SCH-[0-9]{4}$`
- `ck_mms_interval_value_positive` — `interval_value` > 0
- `ck_mms_duration_positive` — `estimated_duration_minutes` > 0
- `ck_mms_deferral_consistency` — `can_be_deferred = TRUE` OR `max_deferral_days` IS NULL. **A deferral limit on a non-deferrable task is contradictory and would surface as a bad recommendation**
- `ck_mms_max_deferral_positive` — `max_deferral_days` is NULL or > 0

*Foreign Keys*
- `fk_mms_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mms_team` — `assigned_maintenance_team_id` → `master.maintenance_team(maintenance_team_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mms_inventory_item` — `required_inventory_item_id` → `master.inventory_item(inventory_item_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.machine`, `master.maintenance_team`, `master.inventory_item` | Many-to-one each |
| Referenced by | `operational.maintenance_work_record` | One-to-many |

**Read Components** — Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only

**Growth** — 8 rows. Grows as machines × maintenance policies.

**Retention** — Permanent.

**Notes**

A machine may have several schedules of different types and intervals — this is the normal case, not an exception, so no unique constraint restricts a machine to one schedule.

The rule that calibration schedules normally carry `can_be_deferred = FALSE` is guidance rather than a constraint, because a non-regulatory calibration may legitimately be deferrable. What **is** constrained is the contradiction: a deferral limit on non-deferrable work.

---

### M27. `master.alert_threshold_profile`

**Purpose**

A named, versioned set of monitoring limits for a machine type. The reusable policy unit that lets machines share a configuration and lets identical machines be monitored differently when their business context differs.

**Business Description**

A profile is **monitoring policy** — when do we want to be told — not an engineering specification. The clearest justification is that two identical machining centres can need different monitoring: the bottleneck on a critical line serving a Gold-tier customer warrants earlier warning and more false positives than a non-bottleneck station on a standard line. Without profiles this would require either per-machine threshold columns, duplicating limits and guaranteeing drift, or one policy per type, ignoring business context entirely.

`version` and `effective_from_date` support the tuning cycle: teams tighten limits until alert volume becomes intrusive, then relax them, and versioning records that history. `sensitivity` gives the Decision Agent useful context — a warning from a `tight` profile is a weaker signal than the same warning from a `relaxed` one.

**Primary Key**

`alert_threshold_profile_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `alert_threshold_profile_id` | INTEGER | NOT NULL | identity | Primary key |
| `alert_threshold_profile_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `ATP-XXXX`. Self-describing, since engineers assign these by hand |
| `profile_name` | VARCHAR(120) | NOT NULL | — | Explains the profile's intent |
| `machine_type_id` | INTEGER | NOT NULL | — | FK → `master.machine_type`. **A profile is type-specific** |
| `version` | INTEGER | NOT NULL | `1` | Incremented on each retune |
| `is_default` | BOOLEAN | NOT NULL | `FALSE` | Exactly one TRUE per machine type |
| `sensitivity` | `master.threshold_sensitivity` | NOT NULL | — | ENUM, 3 values. **Qualifies how much weight a warning carries** |
| `effective_from_date` | DATE | NOT NULL | — | Makes historical alert volume interpretable |
| `review_due_date` | DATE | NULL | — | Prevents thresholds set once and never revisited |
| `notes` | TEXT | NULL | — | Rationale for the settings, for whoever reviews next |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_alert_threshold_profile_code` on (`alert_threshold_profile_code`)
- `uq_alert_threshold_profile_default` — a **partial** unique index on (`machine_type_id`) where `is_default = TRUE AND is_active`. Exactly one default per type

*Check*
- `ck_atp_code_format` — matches `^ATP-[A-Z0-9-]{3,15}$`
- `ck_atp_version_positive` — `version` > 0
- `ck_atp_review_after_effective` — `review_due_date` IS NULL OR `review_due_date` > `effective_from_date`
- `ck_atp_profile_name_not_blank` — trimmed `profile_name` length > 0

*Foreign Keys*
- `fk_atp_machine_type` — `machine_type_id` → `master.machine_type(machine_type_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.machine_type` | Many-to-one |
| Child | `master.alert_threshold_rule` | One-to-many |
| Child | `master.machine` | One-to-many |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only. Changes are audited to `system.audit_log` with before and after values per §7 of the operational document.

**Growth** — 6 rows. Grows with retuning, since superseded versions are soft-retired rather than edited.

**Retention** — Permanent. **Superseded versions are never edited**, which is what allows `operational.operational_event` to reference a rule for lineage while capturing the breached value for evidence.

**Notes**

Cannot be soft-retired while active monitored machines reference it — they would be left monitored by nothing. This is enforced by `RESTRICT` on `fk_machine_alert_threshold_profile` for deletion, and by application validation for soft retirement, since PostgreSQL cannot constrain `is_active` transitions against child references.

---

### M28. `master.alert_threshold_rule`

**Purpose**

The warning and critical limits for one parameter within one profile, including persistence requirement and rate-of-change limit. Where the Monitoring Agent's decisions are configured.

**Business Description**

**Rules are rows, not columns.** The obvious alternative — a wide profile table with `temp_warning_high`, `rpm_critical_low`, and so on — was rejected on four counts, and the decisive one is that the Monitoring Agent would have to hardcode column names and thereby the entire parameter vocabulary. With row-based rules the agent **holds no knowledge of specific parameters**: it loads the rules for a machine and iterates them. Adding vibration monitoring to a type that never had it becomes a data change rather than a code change, which is what keeps that agent genuinely single-responsibility.

`sustained_duration_seconds` is the main defence against alert noise: a momentary spike is not a fault, a breach held for sixty seconds is. `rate_of_change_limit_per_minute` catches what static limits miss — a spindle climbing 4 °C per minute is in trouble while still inside its normal range. Static thresholds detect a state; rate limits detect a trajectory.

**Nullable limits are meaningful.** Tool wear has no meaningful lower bound. A NULL limit means *do not check this direction* — an explicit statement rather than a placeholder.

**Primary Key**

`alert_threshold_rule_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`, with a composite unique constraint on the natural pair. No business code.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `alert_threshold_rule_id` | INTEGER | NOT NULL | identity | Primary key |
| `alert_threshold_profile_id` | INTEGER | NOT NULL | — | FK → `master.alert_threshold_profile` |
| `machine_parameter_id` | INTEGER | NOT NULL | — | FK → `master.machine_parameter` |
| `warning_low` | NUMERIC(12,4) | NULL | — | NULL when low readings are not a concern |
| `warning_high` | NUMERIC(12,4) | NULL | — | NULL when high readings are not a concern |
| `critical_low` | NUMERIC(12,4) | NULL | — | NULL when not applicable |
| `critical_high` | NUMERIC(12,4) | NULL | — | NULL when not applicable |
| `sustained_duration_seconds` | INTEGER | NOT NULL | `0` | **The primary noise filter.** 0 means immediate |
| `warning_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level` |
| `critical_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level`. Must outrank the warning severity |
| `rate_of_change_limit_per_minute` | NUMERIC(12,4) | NULL | — | **Detects a dangerous trajectory while the value is still in range** |
| `is_enabled` | BOOLEAN | NOT NULL | `TRUE` | FALSE suspends a rule without deleting it — the correct response to a suspected faulty sensor |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_alert_threshold_rule_pair` on (`alert_threshold_profile_id`, `machine_parameter_id`)

*Check*
- `ck_atr_at_least_one_limit` — at least one of `warning_low`, `warning_high`, `critical_low`, `critical_high`, `rate_of_change_limit_per_minute` is NOT NULL. **A rule with every limit NULL checks nothing while appearing to be configuration**
- `ck_atr_low_side_ordered` — `critical_low` IS NULL OR `warning_low` IS NULL OR `critical_low` <= `warning_low`
- `ck_atr_high_side_ordered` — `critical_high` IS NULL OR `warning_high` IS NULL OR `warning_high` <= `critical_high`
- `ck_atr_sustained_duration_range` — `sustained_duration_seconds` between 0 and 3600
- `ck_atr_rate_limit_positive` — `rate_of_change_limit_per_minute` is NULL or > 0
- `ck_atr_severities_differ` — `critical_severity_level_id <> warning_severity_level_id`

*Foreign Keys*
- `fk_atr_profile` — `alert_threshold_profile_id` → `master.alert_threshold_profile(alert_threshold_profile_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_atr_parameter` — `machine_parameter_id` → `master.machine_parameter(machine_parameter_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_atr_warning_severity` — `warning_severity_level_id` → `master.failure_severity_level(failure_severity_level_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_atr_critical_severity` — `critical_severity_level_id` → `master.failure_severity_level(failure_severity_level_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.alert_threshold_profile`, `master.machine_parameter` | Many-to-one each |
| Parent | `master.failure_severity_level` | Many-to-one, **twice** — warning and critical |
| Referenced by | `operational.operational_event` | One-to-many |

Resolves **alert_threshold_profile ↔ machine_parameter as many-to-many**.

**Read Components** — Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard

**Write Components** — Administrative only. Changes are audited with before and after values.

**Growth** — ~26 rows. Grows as profiles × monitored parameters.

**Retention** — Permanent.

**Notes**

`ck_atr_severities_differ` is weaker than the frozen rule, which requires the critical severity to **outrank** the warning severity — a lower `severity_rank`. That comparison requires joining to `failure_severity_level` twice, which a `CHECK` constraint cannot do. The in-row check catches the common error of setting both to the same level; the full ordering is application-validated per §41.3.

The complete threshold ordering across `machine_parameter`, `machine_type_parameter`, and this table is likewise cross-table. **This is the most consequential application-validated rule in the database**, because a violation means healthy machines generate alerts, and §41.3 assigns it to seed validation and to every threshold edit.

---

### M29. `master.business_rule`

**Purpose**

Tunable parameters governing platform behaviour: escalation cut-offs, downtime cost rates, prioritisation weights, notification and maintenance policy.

**Business Description**

Several pipeline decisions depend on numbers that are **business policy, not engineering fact**. Hardcoding them would have two consequences: changing an escalation cut-off would require a deployment, and — more importantly — **the platform could not explain why it escalated.** *"The model decided to escalate"* is not traceable. *"Failure probability 0.74 exceeded the 0.70 threshold defined in `BR-ESC-PROB`"* is.

That traceability is this table's real justification. It turns the Supervisor Agent's escalation decision from an opaque judgement into a stated rule anyone can inspect.

**Typed value columns rather than a single text field.** `value_numeric`, `value_text`, and `value_boolean`, with `value_type` declaring which applies. A single text `value` column would forfeit numeric comparison in queries, database-level range validation, and would surface parse failures at runtime in the middle of an escalation decision. The cost is two mostly-NULL columns per row on a table of a few dozen rows — an easy trade.

**Line-scoped overrides via a nullable foreign key.** `production_line_id` NULL means global, populated means line-specific. A general polymorphic scope was rejected: it could not be enforced by foreign keys and no consumer needs scoping beyond the line.

**Primary Key**

`business_rule_id` — `INTEGER GENERATED ALWAYS AS IDENTITY`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `business_rule_id` | INTEGER | NOT NULL | identity | Primary key |
| `business_rule_code` | VARCHAR(32) | NOT NULL | — | Business key. **Cited in recommendations and logs to explain a decision** |
| `rule_name` | VARCHAR(150) | NOT NULL | — | Understandable without reading the description |
| `rule_category` | `master.business_rule_category` | NOT NULL | — | ENUM, 6 values. Lets each consumer load only what it needs |
| `value_type` | `master.business_rule_value_type` | NOT NULL | — | ENUM, 3 values. Declares which value column applies |
| `value_numeric` | NUMERIC(14,4) | NULL | — | Directly comparable in queries without parsing |
| `value_text` | VARCHAR(100) | NULL | — | Typically a code such as a severity level |
| `value_boolean` | BOOLEAN | NULL | — | On/off policy switches |
| `unit` | VARCHAR(24) | NULL | — | `INR/hour`, `days`, `multiplier`. **Prevents a rate being misread as a total** |
| `production_line_id` | INTEGER | NULL | — | FK → `master.production_line`. **NULL means global** |
| `description` | TEXT | NOT NULL | — | **Cited when explaining a decision**, so written for a manager not a developer |
| `effective_from_date` | DATE | NOT NULL | — | Makes historical behaviour interpretable |

Plus the standard trailing columns from §12.2.

**Constraints**

*Unique*
- `uq_business_rule_code` on (`business_rule_code`) — **globally unique.** A line-scoped override is a separate rule with its own code, so every rule stays individually citable

*Check*
- `ck_business_rule_code_format` — matches `^BR-[A-Z]{3,5}-[A-Z0-9-]{2,20}$`
- `ck_business_rule_exactly_one_value` — exactly one value column is populated and it matches `value_type`. Expressed as three mutually exclusive branches: `value_type = 'numeric'` requires `value_numeric` NOT NULL and the other two NULL; `'text'` requires `value_text` NOT NULL and the others NULL; `'boolean'` requires `value_boolean` NOT NULL and the others NULL. **The most important constraint on this table** — a row with the wrong column filled would fail silently at read time
- `ck_business_rule_name_not_blank` — trimmed `rule_name` length > 0
- `ck_business_rule_description_not_blank` — trimmed `description` length > 0

*Foreign Keys*
- `fk_business_rule_production_line` — `production_line_id` → `master.production_line(production_line_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Cardinality |
|---|---|---|
| Parent | `master.production_line` | Many-to-one, optional |
| Referenced by | `operational.supervisor_context` | One-to-many |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard

**Write Components** — Administrative only. **Rules are read by agents and never written by them** — an agent that could rewrite its own escalation threshold would make the platform's behaviour unexplainable.

**Growth** — 11 rows. Grows with policy refinement.

**Retention** — Permanent. **Superseded values are soft-retired with a new row created, never edited in place.** Editing would destroy the record of what policy produced a past decision, and that record is part of the audit trail. This discipline is also what allows `operational.supervisor_context` to reference a rule without copying its value.

**Notes**

`ck_business_rule_exactly_one_value` is the clearest example in this schema of a business rule that is fully expressible as a declarative constraint. It is a three-branch boolean expression over four columns, PostgreSQL evaluates it per row at no meaningful cost, and it makes an entire class of silent runtime failure impossible.

`unit` being mandatory for dimensioned numerics is a data quality rule that cannot be constrained in-row — the database cannot know which numerics carry a dimension — and is application-validated.

---

# Part IV — Operational Tables

## 13. Conventions applied to all operational tables

### 13.1 Identity

```
<table>_id   BIGINT   GENERATED ALWAYS AS IDENTITY   PRIMARY KEY
```

`BIGINT` is **mandatory, not preferred.** `operational.machine_sensor_reading` alone generates roughly 87,000 rows per day — about 2.6 million per month and 32 million per year. A 32-bit identity exhausts at 2.1 billion, which this table would reach in under seventy years at current volume and in a few years at any realistic expansion. Discovering that in production is an avoidable outage, and the cost of `BIGINT` is four bytes per row.

For consistency and because `BIGINT` foreign keys between operational tables must match, **every** operational table uses `BIGINT` identity, including the eight-row ones.

### 13.2 Standard trailing columns

| Column | Type | Null | Default | Applies to |
|---|---|---|---|---|
| `created_at` | TIMESTAMPTZ | NOT NULL | `now()` | All 24 tables. **Record time**, distinct from event time |
| `created_by_component` | `system.platform_component` | NOT NULL | — | All 24 tables. Provenance, enforcing the ownership model |
| `updated_at` | TIMESTAMPTZ | NOT NULL | `now()` | **Only the 8 tables that receive `UPDATE`** |

**`updated_at` is deliberately absent from 16 tables.** Those tables never change after insert, and carrying an `updated_at` on them would suggest they might — which would eventually tempt somebody to use it. The eight tables that carry it are: `machine_operational_status`, `production_run`, `production_count`, `maintenance_work_record`, `operational_alert`, `notification_delivery`, `dashboard_snapshot`, and `system.system_health_status`.

**No `is_active` on any operational table.** Operational rows are purged on a retention schedule, not soft-retired. Soft deletion exists in `master` because operational history references those rows forever; nothing references an operational row after its retention window except through the citation exemption, which is handled by the deletion policy rather than a flag.

### 13.3 Event time versus record time

Every operational table carries an explicit **event-time** column — `recorded_at`, `detected_at`, `occurred_at`, `movement_at`, `transition_at`, `generated_at`, `composed_at` — that is distinct from `created_at`.

This is not redundancy. A recommendation stating *"vibration has been rising since the start of B shift"* is a claim about event time. If the pipeline stalls four minutes and processes a backlog, record time is four minutes later and using it would make the statement wrong. Separating them is what keeps time-based reasoning honest and what makes replay possible: re-running the pipeline produces new record times against unchanged event times.

**All event-time columns are indexed candidates and partition keys.** Part IX treats them as such.

### 13.4 The retention dependency finding

Physical design surfaced a tension the logical documents did not need to resolve, and it is recorded here because it affects every Retention subsection below.

The frozen operational document states a retention window per table. Those windows are **not independent**, because `ON DELETE RESTRICT` on a foreign key means a parent row cannot be purged while a child references it. Working the reference graph produces a longer effective floor for several tables:

| Table | Stated window | Pinned by | Effective floor |
|---|---|---|---|
| `prediction_feature_snapshot` | 180 days | `prediction_result` (NOT NULL FK) | Governed by `prediction_result` |
| `prediction_result` | 2 years | `ai_recommendation` (NOT NULL FK) | Governed by `ai_recommendation` |
| `supervisor_context` (escalated) | 2 years | `ai_recommendation` (NOT NULL FK) | Governed by `ai_recommendation` |
| `ai_recommendation` | 3 years | `maintenance_work_record` (nullable FK) | **5 years** |
| `operational_alert` | 2 years | `maintenance_work_record` (nullable FK) | **5 years** |

The chain terminates at `maintenance_work_record`, which carries the longest retention in the database at 5 years because asset maintenance history outlives everything else.

**This is not a contradiction of the frozen model — it is its consequence.** The operational document §8.4 rule 1 already states that any row cited by a retained recommendation is exempt from purge, and §13.7 already draws the evidence chain. `RESTRICT` is the mechanism that enforces both. The stated windows are therefore **minimums**, and the effective window for any table is the maximum of its own window and that of everything referencing it.

Two consequences follow, and both are recorded rather than worked around:

- **Purge must run in reverse dependency order**, from layer 7 down to layer 0. §41.5 specifies this.
- **Retention configuration must be expressed as a floor, not an exact age.** A purge job that deletes strictly by age will fail against `RESTRICT`, and that failure is the correct outcome — it reveals that the evidence chain would otherwise have been broken.

**One foreign key is exempted from `RESTRICT` for exactly this reason.** `operational_event.triggering_reading_id` uses `ON DELETE SET NULL`. §32.3 explains why this single exception is safe and necessary.

### 13.5 Table specification format

Identical eleven-heading structure to Part III.

---

## Group A — Machine Telemetry & State (O1–O3)

---

### O1. `operational.machine_sensor_reading`

**Purpose**

Every parameter measurement from every monitored machine. The raw signal of the platform and the ultimate evidence behind every claim about machine condition.

**Business Description**

The highest-volume table in the database and the base of the evidence chain. When a recommendation states *"vibration reached 4.8 mm/s at 18:42"*, this is the row that proves it.

Which parameters a machine reports, in what unit, within what bounds, and how often is entirely determined by master data. **This table stores none of that** — only machine, parameter, time, and value.

`quality_flag` separates a machine problem from an instrument problem. Readings outside a parameter's physical range indicate sensor failure, not machine failure, and must never reach the Prediction Agent as valid input. `machine_state_at_reading` is a **declared denormalisation**: the same fact is derivable by joining `machine_state_transition` on a time range, but doing so for every reading during feature extraction would be the most expensive query in the database. It is safe because the reading is immutable and both tables share one writer.

**No `is_anomalous` flag and no severity.** Whether a reading is abnormal is the Monitoring Agent's judgement and is recorded on `operational_event`. Allowing a second component to write a row the simulator owns would break the ownership model.

**Primary Key**

`machine_sensor_reading_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`.

A composite natural key of (`machine_id`, `machine_parameter_id`, `recorded_at`) is the conventional time-series choice and was rejected because `operational_event` must cite specific readings, and a three-column foreign key propagating into the event table is considerably worse than one `BIGINT`.

**Partitioning implication.** If this table is later range-partitioned on `recorded_at` — and §44.3 identifies it as the primary candidate — PostgreSQL requires the partition key to be part of every unique constraint, so the primary key would become (`machine_sensor_reading_id`, `recorded_at`). That is a breaking change to the referencing foreign key. It is recorded here so the decision is deliberate: **v1 uses the single-column key**, because partitioning is not yet warranted at 87,000 rows per day and pre-emptively composite-keying a table on speculation is the kind of complexity `PROJECT_OVERVIEW.md` §16.9 forbids. §44.3 sets out the migration path.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_sensor_reading_id` | BIGINT | NOT NULL | identity | Primary key |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `machine_parameter_id` | INTEGER | NOT NULL | — | FK → `master.machine_parameter` |
| `recorded_at` | TIMESTAMPTZ | NOT NULL | — | **Event time.** All trend and rate reasoning uses this |
| `reading_value` | NUMERIC(12,4) | NOT NULL | — | In the parameter's declared unit |
| `quality_flag` | `operational.reading_quality_flag` | NOT NULL | `'valid'` | ENUM, 5 values. **Only `valid` reaches feature generation** |
| `machine_state_at_reading` | `operational.machine_operational_state` | NOT NULL | — | ENUM, 8 values. **Declared denormalisation** |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |
| `production_run_id` | BIGINT | NULL | — | FK → `operational.production_run`. NULL when idle, in setup, or down — itself meaningful |
| `sequence_number` | BIGINT | NOT NULL | — | Monotonic per machine. Ordering guarantee when two readings share a timestamp; makes replay deterministic |

Plus `created_at` and `created_by_component` from §13.2. No `updated_at`.

**Constraints**

*Unique*
- `uq_msr_machine_sequence` on (`machine_id`, `sequence_number`) — the ordering guarantee, and it doubles as an idempotency key for replay

*Check*
- `ck_msr_sequence_number_positive` — `sequence_number` > 0

**Deliberately not constrained:** `reading_value` against the parameter's `physical_min` and `physical_max`. Those bounds live on `master.machine_parameter` and a `CHECK` cannot reference another table. More importantly, **out-of-range readings must be stored, not rejected** — discarding them would hide instrument failure, and a run of `out_of_physical_range` readings is precisely how a failed sensor is detected. Range evaluation is the writer's responsibility and its verdict is recorded in `quality_flag`.

*Foreign Keys*
- `fk_msr_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_msr_parameter` — `machine_parameter_id` → `master.machine_parameter(machine_parameter_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_msr_shift` — `shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_msr_production_run` — `production_run_id` → `operational.production_run(production_run_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.machine_parameter`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.production_run` | Operational | Many-to-one, optional |
| Referenced by | `operational.operational_event` (`triggering_reading_id`) | Operational | One-to-many |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard, Analytics

**Write Components** — **Factory Simulator only.** Insert only, never updated.

**Growth** — ~87,000 rows/day · ~2.6 million/month · ~32 million/year. **The largest table in the database by two orders of magnitude.**

**Retention** — 90 days at full resolution, then downsampled to hourly aggregates and the raw rows purged. **Aggregate before purge is mandatory** — purging first loses the resolution permanently. Individual readings cited by a retained event survive via §32.3's `SET NULL` treatment, which preserves the event's evidence without pinning the reading.

**Notes**

`uq_msr_machine_sequence` earns its place twice. It guarantees deterministic ordering within a machine when timestamps collide, and it makes reading insertion idempotent — a retried simulator batch cannot double-insert. On a table of this volume that second property is worth the index cost on its own.

---

### O2. `operational.machine_operational_status`

**Purpose**

The current operational state of each machine in exactly one row, with the accumulated counters maintenance scheduling depends on. Answers *what is happening right now* without scanning history.

**Business Description**

Eight machines, eight rows, overwritten in place. **The only table in the database whose row count never grows.**

It exists because deriving current state from `machine_state_transition` means finding the latest transition per machine on every Monitoring Agent cycle. One indexed row per machine turns the most frequent read in the platform into a trivial lookup.

The **accumulated counters** are the more consequential part. `master.machine_maintenance_schedule` deliberately omits `next_due_date`; this table supplies the operational input for computing it:

| Schedule `interval_basis` | Computed against |
|---|---|
| `operating_hours` | `accumulated_operating_hours` − `operating_hours_at_last_maintenance` |
| `cycle_count` | `accumulated_cycle_count` − `cycle_count_at_last_maintenance` |
| `calendar_days` | `baseline_start_date` and closed work records directly |

Storing the value **at last maintenance** rather than a resetting "since" counter is deliberate: two absolute readings and a subtraction cannot drift, whereas a resetting counter is a second mutable number that can.

**Both counters are maintained running totals**, derivable from `cycle_history` and `machine_state_transition` respectively. Summing millions of rows on every maintenance-due check is not viable, and a machine hour meter is the standard MES pattern. §41.6 states the reconciliation obligation that keeps them honest.

**Primary Key**

`machine_operational_status_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with a **unique constraint on `machine_id`** enforcing one row per machine.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_operational_status_id` | BIGINT | NOT NULL | identity | Primary key |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine`. **UNIQUE** — one row per machine for life |
| `current_state` | `operational.machine_operational_state` | NOT NULL | — | ENUM, 8 values. `starved` and `blocked` are distinguished because they mean opposite things about where the constraint is |
| `state_since` | TIMESTAMPTZ | NOT NULL | — | Current duration is `now()` minus this |
| `current_production_run_id` | BIGINT | NULL | — | FK → `operational.production_run`. NULL when idle, down, or offline |
| `current_shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |
| `accumulated_operating_hours` | NUMERIC(12,2) | NOT NULL | `0` | **Machine hour meter.** Monotonically non-decreasing |
| `accumulated_cycle_count` | BIGINT | NOT NULL | `0` | Total cycles since commissioning. Monotonically non-decreasing |
| `operating_hours_at_last_maintenance` | NUMERIC(12,2) | NULL | — | NULL before first service. **The anchor for operating-hour due calculation** |
| `cycle_count_at_last_maintenance` | BIGINT | NULL | — | NULL before first service |
| `last_reading_at` | TIMESTAMPTZ | NULL | — | **Staleness detection.** NULL for unmonitored machines |
| `last_state_transition_id` | BIGINT | NULL | — | FK → `operational.machine_state_transition`. Direct traceability from state to its cause |
| `open_alert_count` | INTEGER | NOT NULL | `0` | **Maintained for dashboard performance.** Reconcilable against the alert table |

Plus `created_at`, `created_by_component`, and `updated_at` from §13.2.

**Constraints**

*Unique*
- `uq_mos_machine` on (`machine_id`) — the one-row-per-machine enforcement

*Check*
- `ck_mos_operating_hours_non_negative` — `accumulated_operating_hours` >= 0
- `ck_mos_cycle_count_non_negative` — `accumulated_cycle_count` >= 0
- `ck_mos_open_alert_count_non_negative` — `open_alert_count` >= 0
- `ck_mos_maint_hours_not_ahead` — `operating_hours_at_last_maintenance` IS NULL OR `operating_hours_at_last_maintenance` <= `accumulated_operating_hours`
- `ck_mos_maint_cycles_not_ahead` — `cycle_count_at_last_maintenance` IS NULL OR `cycle_count_at_last_maintenance` <= `accumulated_cycle_count`
- `ck_mos_running_requires_run` — `current_state <> 'running'` OR `current_production_run_id` IS NOT NULL. **Running without a run is a contradiction**
- `ck_mos_run_only_when_engaged` — `current_production_run_id` IS NULL OR `current_state` IN (`running`, `setup`, `starved`, `blocked`). A machine that is down or idle is not on a run

*Foreign Keys*
- `fk_mos_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mos_shift` — `current_shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mos_production_run` — `current_production_run_id` → `operational.production_run(production_run_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mos_last_transition` — `last_state_transition_id` → `operational.machine_state_transition(machine_state_transition_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine` | Master | **One-to-one** |
| Parent | `master.shift` | Master | Many-to-one |
| Parent | `operational.production_run`, `operational.machine_state_transition` | Operational | Many-to-one each |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics

**Write Components** — **Factory Simulator only.** Insert once per machine, then update continuously.

**Growth** — **8 rows, fixed.** Grows only when a machine is added. Zero net growth in steady state.

**Retention** — Permanent. Never archived, never purged.

**Notes**

**This table sits at operational dependency layer 7 — the deepest in the database.** That is correct: it is the most derived table in the model, a materialised summary of everything below it, and it is fully **regenerable** by replaying `machine_state_transition` and `cycle_history`. That regenerability is what makes its mutability safe — the row is a performance convenience, never a source of truth.

The two `ck_mos_*_requires_run` constraints are worth noting as a pair. Together they make the state-to-run relationship a closed set of legal combinations, rejecting at write time an inconsistency that would otherwise produce nonsense on the dashboard and in the Supervisor Agent's context.

---

### O3. `operational.machine_state_transition`

**Purpose**

Every change of machine state as an immutable fact, with the duration of the state being left and the reason for leaving it. The history behind O2 and the foundation of availability analysis.

**Business Description**

Its central column is `duration_in_previous_state_seconds`. Storing the duration of the state being *left*, at the moment of leaving it, means every completed interval is one row with a known length — which turns availability analysis into a simple aggregation instead of a window function over the whole history:

| Question | Answered by |
|---|---|
| Availability this shift | Sum `running` durations ÷ scheduled time |
| Total unplanned downtime | Sum durations where the state left was `down_unplanned` |
| Mean time to restore | Average `down_unplanned` duration |
| Starvation caused upstream | Sum `starved` durations grouped by `reason_code` |

`reason_code` turns a state log into a diagnostic record. The triggering references close the causal loop: when a machine goes down because a detected condition led to a maintenance job, the transition points at both, which is what lets Analytics answer whether acting on recommendations measurably reduced downtime.

**Primary Key**

`machine_state_transition_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. `BIGINT` because O2 references it and a composite key would propagate into a row read on every monitoring cycle.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_state_transition_id` | BIGINT | NOT NULL | identity | Primary key |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `from_state` | `operational.machine_operational_state` | NULL | — | **NULL only for the first transition** after commissioning |
| `to_state` | `operational.machine_operational_state` | NOT NULL | — | Must differ from `from_state` |
| `transition_at` | TIMESTAMPTZ | NOT NULL | — | **Event time.** Boundary of both the closing and opening intervals |
| `duration_in_previous_state_seconds` | INTEGER | NULL | — | NULL only when `from_state` is NULL. **Turns availability analysis into a sum** |
| `reason_code` | `operational.state_transition_reason` | NOT NULL | — | ENUM, 13 values |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |
| `production_run_id` | BIGINT | NULL | — | FK → `operational.production_run` |
| `triggering_event_id` | BIGINT | NULL | — | FK → `operational.operational_event`. **Closes the loop from detection to downtime** |
| `triggering_work_record_id` | BIGINT | NULL | — | FK → `operational.maintenance_work_record` |
| `notes` | TEXT | NULL | — | Populated where a human decision drove the change |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Check*
- `ck_mst_states_differ` — `from_state` IS NULL OR `from_state <> to_state`. A transition to the same state is not a transition
- `ck_mst_duration_consistency` — (`from_state` IS NULL AND `duration_in_previous_state_seconds` IS NULL) OR (`from_state` IS NOT NULL AND `duration_in_previous_state_seconds` IS NOT NULL). The two are populated or absent together
- `ck_mst_duration_non_negative` — `duration_in_previous_state_seconds` IS NULL OR >= 0

*Foreign Keys*
- `fk_mst_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mst_shift` — `shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mst_production_run` — `production_run_id` → `operational.production_run(production_run_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mst_triggering_event` — `triggering_event_id` → `operational.operational_event(operational_event_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mst_triggering_work_record` — `triggering_work_record_id` → `operational.maintenance_work_record(maintenance_work_record_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.production_run`, `operational.operational_event`, `operational.maintenance_work_record` | Operational | Many-to-one each, optional |
| Referenced by | `operational.machine_operational_status` | Operational | One-to-one for the latest |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics

**Write Components** — **Factory Simulator only.** Insert only, in the same transaction as the O2 update. §46.1 specifies that boundary.

**Growth** — ~200 rows/day · ~73,000/year.

**Retention** — 2 years online, then archived indefinitely. **This is the availability record and the basis of all downtime reporting**, so archive rather than purge.

**Notes**

`ck_mst_duration_consistency` expresses a rule that is easy to state and easy to get wrong in application code: the first transition has no predecessor and therefore no duration, and every subsequent one has both. Making it a constraint means the invariant holds regardless of which code path inserts the row.

The rule that `duration_in_previous_state_seconds` must **equal** the gap to the previous transition is cross-row and cannot be a check constraint. It is a reconciliation check per §41.6, and it is genuinely valuable because a mismatch indicates a lost or out-of-order transition — which would silently corrupt every availability figure derived from the table.

---

## Group B — Production Execution (O4–O9)

Four deliberately different grains, none derivable from a coarser one except `production_count`, which is declared derived:

| Table | Grain | Class |
|---|---|---|
| `production_run` | One order execution | Lifecycle |
| `production_progress` | Run, every 15 minutes | Snapshot |
| `production_count` | Machine × 30-minute interval | Aggregated |
| `cycle_history` | Individual cycle | Append-only |

---

### O4. `operational.production_run`

**Purpose**

One execution of a product order on a line: what is being made, in what quantity, for which customer, by when, and how execution is progressing.

**Business Description**

The operational transaction at the centre of the business, and the table that makes business impact assessment possible. When the Prediction Agent flags a machine, the Decision Agent's impact statement is assembled almost entirely from the active run: the product gives margin, the customer gives tier and penalty exposure, the due date gives urgency, and the pinned capability gives the rate at which output is being lost.

**`customer_id` is `NOT NULL`.** The frozen master model states that every production order references exactly one customer and that make-to-stock is not modelled. There is deliberately no nullable customer and no separate stock-replenishment path, because the platform's impact reasoning depends on an identifiable customer behind every unit at risk.

**`product_line_capability_id` pins the rate.** Rather than re-deriving which capability applies, the run references the governing row, which carries the authoritative `cycle_time_seconds`. Because master capability rows are soft-retired rather than edited, the pinned reference stays valid for the run's whole life — which is why no cycle time is copied into operational data anywhere.

**No cumulative quantity columns.** Progress lives on O5, and a completed run's final figures are read from its terminal snapshot. This differs from O2, which *does* carry maintained totals, and the difference is read frequency: machine counters are read on every monitoring cycle across millions of rows, run quantities occasionally across a few hundred snapshots. Same principle, different cost profile, different answer.

**Primary Key**

`production_run_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `production_run_code` unique. A code is warranted: planners and operators refer to run numbers constantly and it appears in recommendations.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `production_run_id` | BIGINT | NOT NULL | identity | Primary key |
| `production_run_code` | VARCHAR(16) | NOT NULL | — | Business key. Format `RUN-yyyy-nnnn` |
| `product_id` | INTEGER | NOT NULL | — | FK → `master.product` |
| `production_line_id` | INTEGER | NOT NULL | — | FK → `master.production_line` |
| `product_line_capability_id` | INTEGER | NOT NULL | — | FK → `master.product_line_capability`. **Pins the governing rate** |
| `customer_id` | INTEGER | NOT NULL | — | FK → `master.customer`. **Mandatory** |
| `planned_quantity_units` | NUMERIC(12,2) | NOT NULL | — | Denominator for percent complete |
| `planned_start_at` | TIMESTAMPTZ | NOT NULL | — | Scheduled start |
| `planned_end_at` | TIMESTAMPTZ | NOT NULL | — | **The baseline schedule variance is measured against** |
| `actual_start_at` | TIMESTAMPTZ | NULL | — | Set once on transition to `running` |
| `actual_end_at` | TIMESTAMPTZ | NULL | — | NULL until completed or cancelled |
| `due_date` | DATE | NOT NULL | — | **Customer commitment.** With the customer penalty, converts delay into contractual cost |
| `priority` | `operational.run_priority` | NOT NULL | `'normal'` | ENUM, 3 values |
| `run_status` | `operational.run_status` | NOT NULL | `'planned'` | ENUM, 6 values. **Lifecycle position** |
| `pause_reason` | `operational.run_pause_reason` | NULL | — | ENUM, 6 values. NULL unless paused |
| `cancellation_reason` | TEXT | NULL | — | Required when cancelled |

Plus `created_at`, `created_by_component`, and `updated_at`.

**Constraints**

*Unique*
- `uq_production_run_code` on (`production_run_code`)
- `uq_production_run_active_per_line` — a **partial** unique index on (`production_line_id`) where `run_status` IN (`setup`, `running`, `paused`). **At most one active run per line** — a line produces one product at a time, and this is the single most important integrity rule on the table

*Check*
- `ck_pr_code_format` — matches `^RUN-[0-9]{4}-[0-9]{4}$`
- `ck_pr_planned_quantity_positive` — `planned_quantity_units` > 0
- `ck_pr_planned_window_ordered` — `planned_end_at` > `planned_start_at`
- `ck_pr_actual_window_ordered` — `actual_end_at` IS NULL OR `actual_start_at` IS NULL OR `actual_end_at` > `actual_start_at`
- `ck_pr_pause_reason_consistency` — `pause_reason` IS NOT NULL if and only if `run_status = 'paused'`
- `ck_pr_cancellation_reason_required` — `run_status <> 'cancelled'` OR `cancellation_reason` IS NOT NULL
- `ck_pr_started_when_beyond_setup` — `run_status` IN (`planned`, `setup`, `cancelled`) OR `actual_start_at` IS NOT NULL
- `ck_pr_ended_when_terminal` — `run_status <> 'completed'` OR `actual_end_at` IS NOT NULL

*Foreign Keys*
- `fk_pr_product` — `product_id` → `master.product(product_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_production_line` — `production_line_id` → `master.production_line(production_line_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_capability` — `product_line_capability_id` → `master.product_line_capability(product_line_capability_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_customer` — `customer_id` → `master.customer(customer_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.product`, `master.production_line`, `master.product_line_capability`, `master.customer` | Master | Many-to-one each |
| Child | `production_progress`, `production_count`, `cycle_history`, `quality_inspection_result`, `scrap_record` | Operational | One-to-many each |
| Referenced by | `machine_sensor_reading`, `machine_state_transition`, `machine_operational_status`, `inventory_movement`, `operational_event`, `ai_recommendation` | Operational | One-to-many each |

**11 inbound operational references — the most connected operational table.** It has **no outbound operational foreign keys**, placing it at dependency layer 0 as an operational root.

**Read Components** — All eight components

**Write Components** — **Factory Simulator only.** Insert then update through the lifecycle.

**Growth** — ~4–8 rows/day · ~2,000/year.

**Retention** — 2 years after `actual_end_at`, then archived indefinitely. This is the production record.

**Notes**

`uq_production_run_active_per_line` is the clearest case in this schema where a partial unique index enforces a business rule that no other mechanism can. Without it, two concurrent simulator transactions could each schedule a run onto the same line and the database would accept both. Part XI treats this as the primary concurrency guard for the Simulator transaction.

The four `ck_pr_*` status-consistency constraints together make the lifecycle state machine partially self-enforcing. They cannot express legal *transitions* — no declarative constraint can compare a row to its own previous version — but they can reject illegal *states*, which eliminates most of the failure surface.

---

### O5. `operational.production_progress`

**Purpose**

Cumulative run state captured at fixed intervals: how much has been made, at what rate, and whether the run is on schedule.

**Business Description**

Written every 15 minutes for every active run. It exists because a run header can only hold *current* progress, and current progress destroys history. Two questions require the time series: *was this run behind schedule when the excursion started*, and *did output degrade before the machine failed*. A rate drifting from 24 to 22.5 units per hour over four hours is a degradation signal that arrives independently of any sensor threshold.

`projected_completion_at`, `schedule_variance_minutes`, and `is_behind_schedule` are computed at snapshot time and stored. A snapshot's entire purpose is to freeze computed state, so storing derived values here is correct rather than duplicative — recomputing them for an arbitrary past moment would mean replaying every underlying cycle.

`downtime_seconds_cumulative` separates *not producing* from *producing slowly*, which is the availability-versus-performance split OEE depends on.

**Primary Key**

`production_progress_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. No business code.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `production_progress_id` | BIGINT | NOT NULL | identity | Primary key |
| `production_run_id` | BIGINT | NOT NULL | — | FK → `operational.production_run` |
| `snapshot_at` | TIMESTAMPTZ | NOT NULL | — | **Event time.** Aligned to the interval boundary |
| `quantity_good_cumulative` | NUMERIC(12,2) | NOT NULL | — | Non-decreasing across a run's snapshots |
| `quantity_scrapped_cumulative` | NUMERIC(12,2) | NOT NULL | — | Non-decreasing |
| `quantity_rework_cumulative` | NUMERIC(12,2) | NOT NULL | — | Non-decreasing |
| `percent_complete` | NUMERIC(5,2) | NOT NULL | — | May exceed 100 on legitimate overproduction |
| `current_rate_units_per_hour` | NUMERIC(10,2) | NOT NULL | — | Recent window, not the run average. **Compared against the capability rate** |
| `elapsed_production_seconds` | INTEGER | NOT NULL | — | Time in `running` since `actual_start_at` |
| `downtime_seconds_cumulative` | INTEGER | NOT NULL | `0` | **Separates availability loss from performance loss** |
| `projected_completion_at` | TIMESTAMPTZ | NULL | — | NULL when the rate is zero and no forecast is meaningful |
| `schedule_variance_minutes` | INTEGER | NOT NULL | — | **Signed** — positive is late, negative early |
| `is_behind_schedule` | BOOLEAN | NOT NULL | — | Cheap dashboard filter |
| `scrap_rate_pct` | NUMERIC(5,2) | NOT NULL | — | Compared against `product.target_scrap_rate_pct` |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_pp_run_snapshot` on (`production_run_id`, `snapshot_at`) — one snapshot per run per interval. Makes snapshot writing idempotent

*Check*
- `ck_pp_quantities_non_negative` — all three cumulative quantities >= 0
- `ck_pp_percent_complete_non_negative` — `percent_complete` >= 0. **Deliberately not capped at 100** — overproduction is legitimate
- `ck_pp_rate_non_negative` — `current_rate_units_per_hour` >= 0
- `ck_pp_elapsed_non_negative` — `elapsed_production_seconds` >= 0
- `ck_pp_downtime_non_negative` — `downtime_seconds_cumulative` >= 0
- `ck_pp_scrap_rate_range` — `scrap_rate_pct` between 0 and 100
- `ck_pp_projection_after_snapshot` — `projected_completion_at` IS NULL OR `projected_completion_at` > `snapshot_at`

*Foreign Keys*
- `fk_pp_production_run` — `production_run_id` → `operational.production_run(production_run_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pp_shift` — `shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `operational.production_run` | Operational | Many-to-one |
| Parent | `master.shift` | Master | Many-to-one |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard, Analytics

**Write Components** — **Factory Simulator only.** Insert only.

**Growth** — ~380 rows/day · ~140,000/year.

**Retention** — 180 days, then purged. **The terminal snapshot per run is exempt** and retained with the run, because it holds the final quantities the run header deliberately does not. Fully **regenerable** from `production_count` and `machine_state_transition`, which is why aggressive purge is safe.

**Notes**

The non-decreasing rule on cumulative quantities is cross-row and cannot be a check constraint. It is a data quality check per §41.6; a decrease indicates a snapshot ordering defect.

`schedule_variance_minutes` is deliberately signed with no lower bound constraint, since a run ahead of schedule is a legitimate negative. Constraining it to non-negative would be a plausible-looking error that silently rejects good news.

---

### O6. `operational.production_count`

**Purpose**

Pre-aggregated good, scrap, and rework counts per machine per interval. Exists so performance analysis and dashboard rendering never scan raw cycle data.

**Business Description**

**Explicitly derived.** Every value is obtainable by aggregating `cycle_history`. It exists because that aggregation, repeated over millions of cycle rows for every OEE calculation and dashboard chart, would dominate query cost.

Declaring it derived rather than treating it as a source of truth has three practical consequences: it can be **dropped and rebuilt**, so an aggregation defect is a recoverable bug rather than data loss; it can be **reconciled** against its source; and it can be **retained longer** than the cycle data it summarises, becoming the surviving record after cycles are purged.

Machine-level rather than run-level because a line's stations perform differently and the difference is diagnostic — counting per machine reveals that the *bottleneck* has slowed, where counting per run reveals only that the line has.

**Primary Key**

`production_count_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with a composite unique constraint on (`machine_id`, `interval_from`). **The unique constraint is what makes rebuilds idempotent** — re-aggregating an interval updates one row rather than inserting a duplicate. Without it a retried aggregation job would double-count.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `production_count_id` | BIGINT | NOT NULL | identity | Primary key |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `production_run_id` | BIGINT | NULL | — | FK → `operational.production_run`. NULL if idle throughout |
| `interval_from` | TIMESTAMPTZ | NOT NULL | — | Boundary-aligned start |
| `interval_to` | TIMESTAMPTZ | NOT NULL | — | Interval end |
| `good_count` | INTEGER | NOT NULL | `0` | Units passing without intervention |
| `scrap_count` | INTEGER | NOT NULL | `0` | Units scrapped |
| `rework_count` | INTEGER | NOT NULL | `0` | Units sent for rework |
| `cycles_completed` | INTEGER | NOT NULL | `0` | **Must equal good + scrap + rework** — a built-in integrity check |
| `total_cycle_time_seconds` | INTEGER | NOT NULL | `0` | With `cycles_completed`, gives mean cycle time without touching `cycle_history` |
| `running_seconds` | INTEGER | NOT NULL | `0` | **The availability input to OEE** |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at`, `created_by_component`, and `updated_at` (rebuild).

**Constraints**

*Unique*
- `uq_pc_machine_interval` on (`machine_id`, `interval_from`) — **the idempotency guarantee**

*Check*
- `ck_pc_interval_ordered` — `interval_to` > `interval_from`
- `ck_pc_counts_non_negative` — `good_count`, `scrap_count`, `rework_count` all >= 0
- `ck_pc_cycles_equal_outcomes` — `cycles_completed` = `good_count` + `scrap_count` + `rework_count`. **The self-checking invariant.** A violation is an aggregation defect, caught at write time rather than discovered in a report
- `ck_pc_total_cycle_time_non_negative` — `total_cycle_time_seconds` >= 0
- `ck_pc_running_seconds_non_negative` — `running_seconds` >= 0
- `ck_pc_running_within_interval` — `running_seconds` <= the interval length in seconds

*Foreign Keys*
- `fk_pc_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pc_production_run` — `production_run_id` → `operational.production_run(production_run_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pc_shift` — `shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.production_run` | Operational | Many-to-one, optional |
| Derived from | `cycle_history`, `machine_state_transition` | Operational | Aggregation |

**Read Components** — Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard, Analytics. **Not read by the Prediction Agent** — aggregation destroys the per-cycle variance that carries the degradation signal.

**Write Components** — **Factory Simulator only.** Insert at interval close, with idempotent rebuild permitted.

**Growth** — ~1,600 rows/day · ~580,000/year. The third-largest table.

**Retention** — 2 years, then archived. **Deliberately longer than `cycle_history`'s 90 days**, because once cycles are purged this becomes the surviving production record. Regenerable only while cycles survive; after cycle purge it is treated as a source of truth.

**Notes**

An interval where the machine never ran produces a row with **zero counts, not an absent row**. Absence would be ambiguous between *did not produce* and *not yet aggregated*, and the defaults of `0` on all count columns make the zero row the natural result of aggregating nothing.

`ck_pc_running_within_interval` requires computing the interval length from two timestamp columns inside the check expression. PostgreSQL evaluates this per row without difficulty, and it catches a class of aggregation defect — attributing more running time than the interval contains — that would otherwise inflate every availability figure downstream.

---

### O7. `operational.cycle_history`

**Purpose**

Every individual machine cycle with its timing and outcome. The finest grain of production data and the source of the cycle-time deviation signal.

**Business Description**

One row per part per machine. Its value concentrates in `deviation_from_standard_pct`: a machine beginning to fail takes longer per cycle, and it does so before any sensor threshold is crossed. Rising spindle friction costs a fraction of a second per part — invisible in one cycle, unmistakable across a hundred.

This matters because it is a **mechanically independent** signal. Vibration comes from an accelerometer; cycle time from the machine's own completion timing. When both drift together they agree through separate measurement paths, which is a far stronger basis for a root-cause hypothesis than either alone.

**The standard is not copied here.** Deviation is measured against `master.product_line_capability.cycle_time_seconds`, reached through the run's pinned capability. Because master capability rows are soft-retired rather than edited, the referenced row cannot mutate under a completed run, so deviation stays reproducible without denormalising the standard.

`interrupted` flags cycles cut short by a stoppage. An interrupted cycle has a meaningless duration and must be excluded from deviation statistics, or a single breakdown would appear as a catastrophic excursion and corrupt the trend the column exists to reveal.

**Primary Key**

`cycle_history_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. No business code.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `cycle_history_id` | BIGINT | NOT NULL | identity | Primary key |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `production_run_id` | BIGINT | NOT NULL | — | FK → `operational.production_run` |
| `cycle_number_in_run` | INTEGER | NOT NULL | — | Sequence within the run |
| `cycle_started_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of cycle start |
| `cycle_ended_at` | TIMESTAMPTZ | NOT NULL | — | Event time of completion |
| `cycle_time_seconds` | NUMERIC(8,2) | NOT NULL | — | Actual duration |
| `deviation_from_standard_pct` | NUMERIC(6,2) | NULL | — | **The degradation signal.** NULL when interrupted |
| `outcome` | `operational.cycle_outcome` | NOT NULL | — | ENUM, 3 values |
| `interrupted` | BOOLEAN | NOT NULL | `FALSE` | **TRUE excludes the row from all deviation statistics** |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |
| `sequence_number` | BIGINT | NOT NULL | — | Absolute ordering per machine across all runs |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_ch_machine_run_cycle` on (`machine_id`, `production_run_id`, `cycle_number_in_run`) — one row per cycle, and an idempotency key
- `uq_ch_machine_sequence` on (`machine_id`, `sequence_number`) — absolute ordering per machine

*Check*
- `ck_ch_cycle_window_ordered` — `cycle_ended_at` > `cycle_started_at`
- `ck_ch_cycle_time_positive` — `cycle_time_seconds` > 0
- `ck_ch_cycle_number_positive` — `cycle_number_in_run` > 0
- `ck_ch_sequence_number_positive` — `sequence_number` > 0
- `ck_ch_interrupted_has_no_deviation` — `interrupted = FALSE` OR `deviation_from_standard_pct` IS NULL. **Enforces the exclusion rule structurally** rather than trusting every consumer to remember it

*Foreign Keys*
- `fk_ch_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ch_production_run` — `production_run_id` → `operational.production_run(production_run_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ch_shift` — `shift_id` → `master.shift(shift_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.production_run` | Operational | Many-to-one |
| Aggregated into | `operational.production_count` | Operational | Many-to-one |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Analytics. **Not read by the Dashboard**, which uses `production_count`.

**Write Components** — **Factory Simulator only.** Insert only.

**Growth** — ~3,500 rows/day · ~1.3 million/year. **The second-largest table.**

**Retention** — 90 days at full grain, then purged. `production_count` survives as the aggregate. **Aggregate before purge is mandatory.** A secondary partition candidate per §44.3.

**Notes**

`ck_ch_interrupted_has_no_deviation` is a small constraint with disproportionate value. Without it, an interrupted cycle could carry a deviation figure computed from a meaningless duration, and a repair that stopped a machine mid-cycle would register as a large cycle-time *improvement* — polluting the exact trend the Prediction Agent relies on. Making the exclusion structural means no consumer can forget it.

`cycle_time_seconds` must equal the interval between the two timestamps. That is a self-checking arithmetic invariant, and while it is expressible as a check constraint over an interval subtraction, it is recorded as a reconciliation check per §41.6 to avoid a per-row interval computation on the second-highest-volume table in the database. This is a deliberate trade of enforcement immediacy for insert throughput, and it is the only place in this schema where that trade is made.

---

### O8. `operational.quality_inspection_result`

**Purpose**

The outcome of a quality inspection against sampled output, including which machine the defect is attributed to. Links product quality back to machine condition.

**Business Description**

Its most important design feature is that **`machine_id` and `attributed_machine_id` are different columns.** The inspection happens at a station; the defect was caused somewhere else. A bore roundness deviation found at the inspection station at position 3 was created by the mill at position 1. Without the separation, quality data would blame the inspection station for every defect it discovered, and the connection between machine degradation and product quality — one of the platform's most valuable inferences — would be unavailable.

`attributed_failure_category_id` goes further, linking the defect to the failure taxonomy. A roundness deviation attributed to a specific machine with category `FC-BRG` is direct physical confirmation of the degradation mechanism the sensor and cycle data suggest.

`disposition` is deliberately separate from the finding, because a failed inspection does not automatically mean scrap — a part may be reworked and recovered. That separation is also why `scrap_record` is a distinct table: **a finding is not a disposition.**

**No quality specification limits.** Tolerances and measurement limits are excluded from the model; this table records pass and fail counts as judged, not the limits used to judge them.

**Primary Key**

`quality_inspection_result_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `quality_inspection_result_code` unique. A code is warranted: inspection records are referenced in quality documentation and cited by `scrap_record`.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `quality_inspection_result_id` | BIGINT | NOT NULL | identity | Primary key |
| `quality_inspection_result_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `QIR-yyyymmdd-nnnn` |
| `production_run_id` | BIGINT | NOT NULL | — | FK → `operational.production_run` |
| `machine_id` | INTEGER | NULL | — | FK → `master.machine`. **Where inspected.** NULL for manual inspection |
| `attributed_machine_id` | INTEGER | NULL | — | FK → `master.machine`. **Which machine caused it.** Deliberately distinct |
| `attributed_failure_category_id` | INTEGER | NULL | — | FK → `master.failure_category`. Mechanism responsible |
| `inspected_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** |
| `inspection_type` | `operational.inspection_type` | NOT NULL | — | ENUM, 4 values |
| `sample_size` | INTEGER | NOT NULL | — | Units examined |
| `pass_count` | INTEGER | NOT NULL | — | Within specification |
| `fail_count` | INTEGER | NOT NULL | — | Outside specification |
| `inspector_worker_id` | INTEGER | NOT NULL | — | FK → `master.worker`. Accountability |
| `disposition` | `operational.inspection_disposition` | NOT NULL | — | ENUM, 4 values. **Separate from the finding** |
| `primary_defect_note` | TEXT | NULL | — | Required when `fail_count` > 0. **Read by the Decision Agent as corroborating evidence** |
| `related_operational_event_id` | BIGINT | NULL | — | FK → `operational.operational_event`. Closes the loop from condition to quality |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_qir_code` on (`quality_inspection_result_code`)

*Check*
- `ck_qir_code_format` — matches `^QIR-[0-9]{8}-[0-9]{4}$`
- `ck_qir_sample_size_positive` — `sample_size` > 0
- `ck_qir_counts_non_negative` — `pass_count` >= 0 AND `fail_count` >= 0
- `ck_qir_counts_sum_to_sample` — `pass_count` + `fail_count` = `sample_size`. **Self-checking**
- `ck_qir_defect_note_required` — `fail_count` = 0 OR `primary_defect_note` IS NOT NULL. **A recorded failure with no description is not usable evidence**
- `ck_qir_attribution_paired` — (`attributed_machine_id` IS NULL AND `attributed_failure_category_id` IS NULL) OR (`attributed_machine_id` IS NOT NULL AND `attributed_failure_category_id` IS NOT NULL). **Attributing a defect to a machine without naming a mechanism is half an inference**

*Foreign Keys*
- `fk_qir_production_run` — → `operational.production_run` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_qir_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_qir_attributed_machine` — `attributed_machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_qir_attributed_failure_category` — → `master.failure_category` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_qir_inspector` — `inspector_worker_id` → `master.worker(worker_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_qir_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_qir_related_event` — `related_operational_event_id` → `operational.operational_event(operational_event_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine` **twice** (location and attribution), `master.failure_category`, `master.worker`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.production_run`, `operational.operational_event` | Operational | Many-to-one each |
| Child | `operational.scrap_record` | Operational | One-to-many |

The double reference to `master.machine` in two distinct roles is legitimate and creates no cycle, since `machine` is master data and references nothing operational. Both columns are role-qualified.

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics

**Write Components** — **Factory Simulator only.** Insert only.

**Growth** — ~30 rows/day · ~11,000/year.

**Retention** — 3 years, then archived indefinitely. **Among the longest retentions in the database**, because quality history outlives the material it describes.

**Notes**

`ck_qir_attribution_paired` is the constraint that keeps the machine-to-quality inference honest. Attribution is the mechanism by which Analytics answers *how much material did we lose to preventable machine degradation*, and a half-populated attribution would either be silently dropped from that calculation or counted with an unknown mechanism. Requiring both columns together makes the data either fully usable or explicitly absent.

The rule that `attributed_machine_id` must be on the same production line as the run is cross-table and application-validated: a defect cannot be attributed to a machine that never touched the part.

---

### O9. `operational.scrap_record`

**Purpose**

Units scrapped, with quantity, reason, and attribution. The material and financial consequence of quality failure, and the link between machine condition and cost.

**Business Description**

A disposition, not a finding. O8 records that a part failed inspection; this records that material was written off. The two are separate because **not every failure becomes scrap** — a part may be reworked, accepted under concession, or quarantined. Conflating them would systematically overstate material loss.

Scrap also occurs without any inspection — a setup reject discarded by an operator, a part damaged in handling — which is why `quality_inspection_result_id` is nullable.

**No `material_cost_impact` column.** Scrap cost is computed at read time as quantity × `master.product.standard_material_cost`. The trade is accepted deliberately: standard costs are revised infrequently, and no consumer requires point-in-time cost reproduction to the rupee. Storing a captured cost would extend the single permitted master-value-copy exception to a second place with a much weaker justification than the threshold capture in O13.

**Primary Key**

`scrap_record_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. No business code: scrap is analysed in aggregate and individual records are not named on the shop floor.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `scrap_record_id` | BIGINT | NOT NULL | identity | Primary key |
| `production_run_id` | BIGINT | NOT NULL | — | FK → `operational.production_run` |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine`. **Where recorded** |
| `attributed_machine_id` | INTEGER | NULL | — | FK → `master.machine`. **Machine responsible.** NULL for material or handling defects |
| `attributed_failure_category_id` | INTEGER | NULL | — | FK → `master.failure_category` |
| `recorded_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of the scrap decision |
| `quantity_units` | NUMERIC(12,2) | NOT NULL | — | In the product's unit of measure |
| `scrap_reason` | `operational.scrap_reason` | NOT NULL | — | ENUM, 8 values. The Pareto dimension |
| `quality_inspection_result_id` | BIGINT | NULL | — | FK → O8. **NULL for scrap not arising from formal inspection** |
| `related_operational_event_id` | BIGINT | NULL | — | FK → O13. **The link that makes preventable loss measurable** |
| `recorded_by_worker_id` | INTEGER | NOT NULL | — | FK → `master.worker` |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |
| `notes` | TEXT | NULL | — | Additional context |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Check*
- `ck_sr_quantity_positive` — `quantity_units` > 0. A zero-quantity scrap record is meaningless
- `ck_sr_attribution_paired` — both attribution columns populated together or both NULL
- `ck_sr_machine_fault_requires_attribution` — `scrap_reason <> 'machine_fault'` OR (`attributed_machine_id` IS NOT NULL AND `attributed_failure_category_id` IS NOT NULL). **Blaming a machine without naming which machine and which mechanism is not usable data**
- `ck_sr_non_machine_reasons_unattributed` — `scrap_reason` NOT IN (`material_defect`, `handling_damage`) OR (`attributed_machine_id` IS NULL AND `attributed_failure_category_id` IS NULL). **Neither is machine-caused, and attributing them would corrupt the preventable-loss figure**

*Foreign Keys*
- `fk_sr_production_run` — → `operational.production_run` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sr_machine` — `machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sr_attributed_machine` — `attributed_machine_id` → `master.machine(machine_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sr_attributed_failure_category` — → `master.failure_category` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sr_inspection_result` — → `operational.quality_inspection_result` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sr_related_event` — → `operational.operational_event` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sr_recorded_by` — `recorded_by_worker_id` → `master.worker(worker_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sr_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine` **twice**, `master.failure_category`, `master.worker`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.production_run`, `operational.quality_inspection_result`, `operational.operational_event` | Operational | Many-to-one each |
| Referenced by | `operational.inventory_movement` | Operational | One-to-many |

**Read Components** — Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics

**Write Components** — **Factory Simulator only.** Insert only. A reversal is a compensating record, never an edit.

**Growth** — ~15 rows/day · ~5,500/year.

**Retention** — 3 years alongside quality records, then archived indefinitely.

**Notes**

The two reason-to-attribution constraints are the most valuable on this table and they work in opposite directions. `ck_sr_machine_fault_requires_attribution` prevents under-attribution, which would understate preventable loss. `ck_sr_non_machine_reasons_unattributed` prevents over-attribution, which would inflate a machine's preventable-loss figure with a supplier's casting flaw and eventually drive a wrong maintenance decision. **Together they make the preventable-loss metric — the clearest measure of what the platform is worth — trustworthy by construction.**

---

## Group C — Material & Maintenance (O10–O12)

---

### O10. `operational.inventory_movement`

**Purpose**

The stock ledger. Every receipt, issue, return, adjustment, and consumption, with the resulting balance. The single source of truth for how much of anything is on hand.

**Business Description**

`master.inventory_item` holds stocking **policy** and no quantity. This table is where quantity lives, and it lives as a **ledger** rather than a balance.

Each movement stores both the signed change and the balance that resulted. This is the standard accounting pattern and it buys three things: current stock is one indexed lookup rather than a `SUM` over the item's whole history; the ledger is **self-auditing**, since every row's balance must equal the previous row's plus this row's delta, making a break locatable rather than merely detectable; and stock at any past moment is recoverable, so *"was the bearing in stock when we made the recommendation?"* is answerable without replay.

A separate `inventory_balance` current-state table was considered and rejected — it would be a second source of truth for the same number, and the two would eventually disagree, at which point neither could be trusted.

`movement_type` separates planned production consumption from unplanned spare consumption. Issuing a blank to a run is bill-of-materials-driven flow; issuing a bearing to a work order is unplanned consumption of a critical spare and may drop it below its reorder point.

**Balance is maintained per item, not per item-and-location.** Master data assigns each item a single default location, so the two are effectively one-to-one. `inventory_location_id` records **where the transaction physically happened**, which matters because retrieval time differs by location and feeds the repair estimate.

**Primary Key**

`inventory_movement_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `inventory_movement_code` unique. Stores staff reference transaction numbers when reconciling physical stock.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `inventory_movement_id` | BIGINT | NOT NULL | identity | Primary key |
| `inventory_movement_code` | VARCHAR(22) | NOT NULL | — | Business key. Format `MOV-yyyymmdd-nnnnn` |
| `inventory_item_id` | INTEGER | NOT NULL | — | FK → `master.inventory_item` |
| `inventory_location_id` | INTEGER | NOT NULL | — | FK → `master.inventory_location`. Supplies retrieval time |
| `movement_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** |
| `movement_type` | `operational.inventory_movement_type` | NOT NULL | — | ENUM, 8 values |
| `quantity_delta` | NUMERIC(12,4) | NOT NULL | — | **Signed.** Negative for issues and consumption |
| `resulting_quantity_on_hand` | NUMERIC(12,4) | NOT NULL | — | **The running balance.** Makes current stock one lookup and the ledger self-auditing |
| `production_run_id` | BIGINT | NULL | — | FK → O4. Required for `issue_production` |
| `maintenance_work_record_id` | BIGINT | NULL | — | FK → O11. Required for `issue_maintenance` |
| `scrap_record_id` | BIGINT | NULL | — | FK → O9. Required for `scrap_consumption` |
| `supplier_id` | INTEGER | NULL | — | FK → `master.supplier`. Required for `receipt` |
| `recorded_by_worker_id` | INTEGER | NOT NULL | — | FK → `master.worker` |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift`. **A part needed on a shift with no storekeeper is a real constraint** |
| `reference_note` | TEXT | NULL | — | Required for `adjustment` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_im_code` on (`inventory_movement_code`)

*Check*
- `ck_im_code_format` — matches `^MOV-[0-9]{8}-[0-9]{5}$`
- `ck_im_quantity_delta_non_zero` — `quantity_delta` <> 0
- `ck_im_balance_non_negative` — `resulting_quantity_on_hand` >= 0. **Issuing more than is on hand is physically impossible**
- `ck_im_delta_sign_matches_type` — inbound types (`receipt`, `return`, `transfer_in`) require `quantity_delta` > 0; outbound types (`issue_production`, `issue_maintenance`, `scrap_consumption`, `transfer_out`) require `quantity_delta` < 0; `adjustment` may be either
- `ck_im_production_reference_required` — `movement_type <> 'issue_production'` OR `production_run_id` IS NOT NULL
- `ck_im_maintenance_reference_required` — `movement_type <> 'issue_maintenance'` OR `maintenance_work_record_id` IS NOT NULL
- `ck_im_scrap_reference_required` — `movement_type <> 'scrap_consumption'` OR `scrap_record_id` IS NOT NULL
- `ck_im_receipt_supplier_required` — `movement_type <> 'receipt'` OR `supplier_id` IS NOT NULL
- `ck_im_adjustment_note_required` — `movement_type <> 'adjustment'` OR `reference_note` IS NOT NULL. **An unexplained stock correction destroys the ledger's credibility**

*Foreign Keys*
- `fk_im_inventory_item` — → `master.inventory_item` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_im_inventory_location` — → `master.inventory_location` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_im_supplier` — → `master.supplier` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_im_recorded_by` — → `master.worker` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_im_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_im_production_run` — → `operational.production_run` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_im_work_record` — → `operational.maintenance_work_record` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_im_scrap_record` — → `operational.scrap_record` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.inventory_item`, `master.inventory_location`, `master.supplier`, `master.worker`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.production_run`, `operational.maintenance_work_record`, `operational.scrap_record` | Operational | Many-to-one each, optional |

**Read Components** — Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read by the Prediction Agent** — stock level is not a machine failure predictor.

**Write Components** — **Factory Simulator only.** Insert only. Errors are corrected by an `adjustment` movement, never by editing.

**Growth** — ~60 rows/day · ~22,000/year.

**Retention** — 3 years, then archived indefinitely. This is the material record and must reconcile to physical stock counts.

**Notes**

The four type-specific reference constraints and the sign constraint together make an entire class of untraceable material movement impossible. Unreferenced consumption is material that left the store with no explanation, and a ledger containing it cannot be reconciled against a physical count.

**The balance chain is the audit**, and it cannot be a check constraint — each row's balance depends on the previous row for the same item, which no per-row predicate can see. It is the highest-priority reconciliation check in §41.6, and a break in the chain has a locatable origin, which is precisely why the running balance was chosen over a computed sum.

---

### O11. `operational.maintenance_work_record`

**Purpose**

One maintenance job from request to closure: what was needed, why, who did it, what they found, how long the machine was down, and how it was resolved. The operational record of maintenance and the platform's accuracy scorecard.

**Business Description**

Three aspects give it disproportionate importance.

**It is the maintenance history the master model deliberately did not cache.** `master.machine_maintenance_schedule` excludes `next_due_date` and computes due status from operational history instead. **This table is that history** — a closed record referencing a schedule is what establishes when that schedule was last satisfied.

**It closes the prediction accuracy loop.** `reported_failure_category_id` records what was suspected at open, normally from the prediction. `confirmed_failure_category_id` records what the engineer actually found. Comparing the two across many jobs is **the only honest measure of whether the platform's predictions are correct** — a model with 0.90 average confidence and a 40 % confirmation rate is not a good model, and no internal validation metric would reveal that.

**It quantifies what the platform is worth.** `work_type = 'predictive'` means the job exists *because* FactoryFlow AI recommended it. Counting predictive jobs that displaced corrective ones is the business case expressed in data.

**Primary Key**

`maintenance_work_record_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `maintenance_work_record_code` unique. The work order number is used plant-wide, spoken aloud, and cited in recommendations.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `maintenance_work_record_id` | BIGINT | NOT NULL | identity | Primary key |
| `maintenance_work_record_code` | VARCHAR(14) | NOT NULL | — | Business key. Format `WO-yyyy-nnnn` |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `work_type` | `operational.maintenance_work_type` | NOT NULL | — | ENUM, 6 values. **The platform's value metric** |
| `machine_maintenance_schedule_id` | INTEGER | NULL | — | FK → `master.machine_maintenance_schedule`. **Populating it is what marks the schedule as performed** |
| `triggering_alert_id` | BIGINT | NULL | — | FK → O14 |
| `triggering_recommendation_id` | BIGINT | NULL | — | FK → O18. **Required when `work_type = 'predictive'`** |
| `reported_failure_category_id` | INTEGER | NULL | — | FK → `master.failure_category`. Suspected at open |
| `confirmed_failure_category_id` | INTEGER | NULL | — | FK → `master.failure_category`. **The ground truth every prediction is scored against** |
| `priority_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level` |
| `assigned_maintenance_team_id` | INTEGER | NULL | — | FK → `master.maintenance_team` |
| `assigned_engineer_id` | INTEGER | NULL | — | FK → `master.maintenance_engineer` |
| `work_status` | `operational.maintenance_work_status` | NOT NULL | `'open'` | ENUM, 7 values. **Lifecycle position** |
| `opened_at` | TIMESTAMPTZ | NOT NULL | — | When raised |
| `assigned_at` | TIMESTAMPTZ | NULL | — | NULL while open |
| `started_at` | TIMESTAMPTZ | NULL | — | When work physically began |
| `completed_at` | TIMESTAMPTZ | NULL | — | When the repair finished |
| `closed_at` | TIMESTAMPTZ | NULL | — | **Only on closure do the machine's maintenance counters update** |
| `planned_duration_minutes` | INTEGER | NULL | — | From the failure mode estimate plus retrieval time |
| `actual_duration_minutes` | INTEGER | NULL | — | **Compared against planned to validate estimates** |
| `machine_downtime_minutes` | INTEGER | NULL | — | Exceeds working time by handover and restart |
| `did_stop_line` | BOOLEAN | NOT NULL | `FALSE` | **The difference between a nuisance and a production loss** |
| `resolution_note` | TEXT | NULL | — | Required when closed |
| `shift_id_opened` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at`, `created_by_component`, and `updated_at`.

**Constraints**

*Unique*
- `uq_mwr_code` on (`maintenance_work_record_code`)

*Check*
- `ck_mwr_code_format` — matches `^WO-[0-9]{4}-[0-9]{4}$`
- `ck_mwr_predictive_requires_recommendation` — `work_type <> 'predictive'` OR `triggering_recommendation_id` IS NOT NULL. **This is the definition of predictive work**
- `ck_mwr_timestamp_sequence` — the chain `opened_at` <= `assigned_at` <= `started_at` <= `completed_at` <= `closed_at` holds for every pair where both values are present
- `ck_mwr_closed_requires_resolution` — `work_status <> 'closed'` OR `closed_at` IS NOT NULL AND `resolution_note` IS NOT NULL
- `ck_mwr_durations_positive` — `planned_duration_minutes`, `actual_duration_minutes`, `machine_downtime_minutes` each NULL or > 0
- `ck_mwr_downtime_at_least_duration` — `machine_downtime_minutes` IS NULL OR `actual_duration_minutes` IS NULL OR `machine_downtime_minutes` >= `actual_duration_minutes`. **Downtime includes handover and restart that working time excludes**
- `ck_mwr_assigned_requires_team` — `work_status` IN (`open`, `cancelled`) OR `assigned_maintenance_team_id` IS NOT NULL

*Foreign Keys*
- `fk_mwr_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_schedule` — → `master.machine_maintenance_schedule` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_reported_category` — `reported_failure_category_id` → `master.failure_category` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_confirmed_category` — `confirmed_failure_category_id` → `master.failure_category` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_severity` — → `master.failure_severity_level` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_team` — → `master.maintenance_team` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_engineer` — → `master.maintenance_engineer` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_shift_opened` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_triggering_alert` — → `operational.operational_alert` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mwr_triggering_recommendation` — → `operational.ai_recommendation` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.machine_maintenance_schedule`, `master.failure_category` **twice**, `master.failure_severity_level`, `master.maintenance_team`, `master.maintenance_engineer`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.operational_alert`, `operational.ai_recommendation` | Operational | Many-to-one each, optional |
| Child | `operational.machine_maintenance_activity`, `operational.inventory_movement` | Operational | One-to-many each |
| Referenced by | `operational.machine_state_transition`, `operational.recommendation_action` | Operational | One-to-many each |

**Read Components** — All eight components

**Write Components** — **Factory Simulator only.** Insert then update through the lifecycle.

**Growth** — under 10 rows/day · ~2,500/year.

**Retention** — **5 years, then archived indefinitely. The longest retention in the database.** Asset maintenance history outlives every other record and is required for warranty and reliability analysis. **This table is the terminus of the retention dependency chain in §13.4** — its 5-year window transitively pins `ai_recommendation`, `supervisor_context`, `prediction_result`, `prediction_feature_snapshot`, `operational_alert`, and `operational_event`.

**Notes**

`ck_mwr_predictive_requires_recommendation` makes the platform's own value metric self-enforcing. Without it, a `predictive` work record could exist with no recommendation behind it, and the count of platform-caused interventions — the business case — would be inflated by ordinary preventive work.

The rule that closure requires a `confirmed_failure_category_id` applies only to corrective, predictive, and emergency work. Routine preventive and inspection work legitimately confirms nothing because nothing failed. This is expressible as a check constraint over `work_type` and `work_status`; it is recorded in §41.3 as application-validated instead, because the frozen model states the requirement conditionally and encoding a conditional set of work types in a constraint would need revising every time the work-type vocabulary grows.

---

### O12. `operational.machine_maintenance_activity`

**Purpose**

The append-only timeline of steps performed within a maintenance job. Makes the duration of a repair explicable rather than merely known.

**Business Description**

A work record says a job took 255 minutes. This table says where those minutes went — and that distinction has direct operational consequence. Two four-hour jobs are entirely different problems if one spent three hours waiting for an engineer and the other spent three hours on the repair. The first is a response-coverage problem; the second is a difficulty problem.

The activity log decomposes a job into intervals that map onto commitments held in master data: response time against `maintenance_team.target_response_time_minutes`, part retrieval against `inventory_location.average_retrieval_time_minutes`, repair time against `machine_type_failure_mode.estimated_repair_duration_minutes`. **Master data states commitments; this table is where they are held to account.**

**Not a task checklist and not a procedure document.** It records what happened and when, never what should happen.

**Primary Key**

`machine_maintenance_activity_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. No business code: a child of a coded parent.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `machine_maintenance_activity_id` | BIGINT | NOT NULL | identity | Primary key |
| `maintenance_work_record_id` | BIGINT | NOT NULL | — | FK → O11 |
| `activity_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of the step |
| `activity_type` | `operational.maintenance_activity_type` | NOT NULL | — | ENUM, 13 values. **The vocabulary is what makes interval measurement possible** |
| `performed_by_worker_id` | INTEGER | NULL | — | FK → `master.worker`. NULL for system-recorded events |
| `duration_from_previous_seconds` | INTEGER | NULL | — | NULL for the first activity. **Makes interval analysis a read rather than a window function** |
| `notes` | TEXT | NULL | — | Step detail |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift`. **A job spanning a shift change shows the handover here** |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_mma_work_record_activity` on (`maintenance_work_record_id`, `activity_at`, `activity_type`) — activities within a job are strictly time-ordered, and two identical steps at the same instant indicate a defect. This also makes activity insertion idempotent

*Check*
- `ck_mma_duration_non_negative` — `duration_from_previous_seconds` IS NULL OR >= 0

*Foreign Keys*
- `fk_mma_work_record` — `maintenance_work_record_id` → `operational.maintenance_work_record(maintenance_work_record_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mma_performed_by` — → `master.worker` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_mma_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `operational.maintenance_work_record` | Operational | Many-to-one |
| Parent | `master.worker`, `master.shift` | Master | Many-to-one each |

**Read Components** — Supervisor Agent, Decision Agent, Dashboard, Analytics

**Write Components** — **Factory Simulator only.** Insert only.

**Growth** — under 10 rows/day per active job · ~10,000/year.

**Retention** — 5 years with the parent work record, then archived.

**Notes**

`ON DELETE CASCADE` from the parent work record was considered here — it is the conventional choice for a tightly-coupled child timeline, and it would simplify purge. It is **rejected** for the reason set out in §32.2: a cascade delete could silently remove activity history for a job whose duration is under dispute or whose intervals feed a service-level report. Deletion must be explicit and ordered, so `RESTRICT` applies uniformly here as everywhere else.

---

## Group D — Detection (O13–O14)

The split between immutable observation and mutable managed case. **A degrading bearing produces dozens of events over hours; correlating them into one alert is what separates monitoring from noise.**

---

### O13. `operational.operational_event`

**Purpose**

One detected condition as an immutable fact, with the evidence that produced it: the value observed, the limit breached, and the reading that triggered it. The evidentiary base of every recommendation.

**Business Description**

An event is the Monitoring Agent saying *"this happened, and here is why I say so."* It is deliberately narrow — not a judgement about what to do, not a prediction, not a message — and it never changes.

Events arrive on five detection paths, and having more than one is what makes detection robust: threshold and rate breaches from telemetry, cycle and output deviation from production, attributed failure rates from quality, threshold breaches from inventory, and validity problems from data quality. A single physical problem often produces events in three categories from three independent measurement paths.

**The point-in-time threshold capture.** `threshold_value_breached` stores the limit in force when the event fired, and this is the **only place in the entire database where a master value is copied.** Threshold profiles are versioned and retuned. Storing only `alert_threshold_rule_id` would mean re-reading the rule later returns the *current* limit, and a recommendation citing *"4.74 mm/s against a warning limit of 4.70"* would silently become wrong. More decisively: an event is **quoted into LLM prompts and notification bodies** and must be readable as self-contained evidence without a master lookup. The rule is referenced for lineage; the value is captured for evidence.

**Four nullable typed subject keys, not a polymorphic reference.** An event's subject varies by category — a machine, a line, a run, or an item. A generic `scope_type` plus `scope_ref` pattern cannot be enforced by foreign keys; four typed nullable columns keep full referential integrity while expressing a varying subject. The extra columns buy integrity.

**`operational_alert_id` is `NOT NULL` and set at insert.** The Monitoring Agent finds or creates the correlating alert **first**, then writes the event with the alert already known. That ordering is what allows the event to be genuinely immutable — attaching the alert afterwards would require updating every event and the immutability guarantee would be fiction.

**Primary Key**

`operational_event_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `operational_event_code` unique. A code is essential: events are cited as supporting evidence and the explainability contract depends on that citation being followable by a human.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `operational_event_id` | BIGINT | NOT NULL | identity | Primary key |
| `operational_event_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `EVT-yyyymmdd-nnnn` |
| `operational_alert_id` | BIGINT | NOT NULL | — | FK → O14. **Set at insert**, which is what keeps the event immutable |
| `event_category` | `operational.event_category` | NOT NULL | — | ENUM, 5 values. Governs which subject keys apply |
| `event_type` | `operational.event_type` | NOT NULL | — | ENUM, 12 values |
| `detected_at` | TIMESTAMPTZ | NOT NULL | — | **Event time**, after sustained duration has elapsed |
| `severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level` |
| `machine_id` | INTEGER | NULL | — | FK → `master.machine` |
| `production_line_id` | INTEGER | NULL | — | FK → `master.production_line` |
| `production_run_id` | BIGINT | NULL | — | FK → O4 |
| `inventory_item_id` | INTEGER | NULL | — | FK → `master.inventory_item` |
| `machine_parameter_id` | INTEGER | NULL | — | FK → `master.machine_parameter` |
| `alert_threshold_rule_id` | INTEGER | NULL | — | FK → `master.alert_threshold_rule`. **Lineage** |
| `observed_value` | NUMERIC(12,4) | NULL | — | **Evidence** |
| `threshold_value_breached` | NUMERIC(12,4) | NULL | — | **The one permitted master value copy** |
| `threshold_direction` | `operational.threshold_direction` | NULL | — | ENUM, 3 values |
| `sustained_duration_seconds` | INTEGER | NULL | — | **Distinguishes a transient from a real condition** |
| `triggering_reading_id` | BIGINT | NULL | — | FK → O1. **The deepest link in the evidence chain.** ON DELETE SET NULL |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |
| `detection_note` | TEXT | NULL | — | **Quoted directly in recommendations and notifications** |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_oe_code` on (`operational_event_code`)

*Check*
- `ck_oe_code_format` — matches `^EVT-[0-9]{8}-[0-9]{4}$`
- `ck_oe_machine_subject_required` — `event_category` NOT IN (`machine_condition`, `machine_output`, `data_quality`) OR `machine_id` IS NOT NULL
- `ck_oe_inventory_subject_required` — `event_category <> 'inventory'` OR `inventory_item_id` IS NOT NULL
- `ck_oe_quality_subject_required` — `event_category <> 'quality'` OR (`machine_id` IS NOT NULL AND `production_run_id` IS NOT NULL)
- `ck_oe_threshold_event_complete` — `event_type` NOT IN (`threshold_warning`, `threshold_critical`, `rate_of_change_exceeded`) OR (`machine_parameter_id` IS NOT NULL AND `alert_threshold_rule_id` IS NOT NULL AND `observed_value` IS NOT NULL AND `threshold_value_breached` IS NOT NULL AND `threshold_direction` IS NOT NULL). **An event claiming a breach without stating what was breached is not evidence**
- `ck_oe_sustained_duration_non_negative` — `sustained_duration_seconds` IS NULL OR >= 0

*Foreign Keys*
- `fk_oe_alert` — `operational_alert_id` → `operational.operational_alert(operational_alert_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_severity` — → `master.failure_severity_level` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_production_line` — → `master.production_line` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_inventory_item` — → `master.inventory_item` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_machine_parameter` — → `master.machine_parameter` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_threshold_rule` — → `master.alert_threshold_rule` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_production_run` — → `operational.production_run` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oe_triggering_reading` — `triggering_reading_id` → `operational.machine_sensor_reading(machine_sensor_reading_id)` · **ON DELETE SET NULL** · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `operational.operational_alert` | Operational | Many-to-one, **mandatory** |
| Parent | 7 master tables | Master | Many-to-one each |
| Parent | `operational.production_run`, `operational.machine_sensor_reading` | Operational | Many-to-one each |
| Referenced by | `machine_state_transition`, `quality_inspection_result`, `scrap_record` | Operational | One-to-many each |

**Read Components** — Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read or written by the Simulator** — a strict boundary: the simulator generates reality, never the platform's interpretation of it.

**Write Components** — **Monitoring Agent only.** Insert only. **Absolutely immutable** — the explainability contract collapses if evidence can be rewritten.

**Growth** — ~40 rows/day · ~15,000/year.

**Retention** — 1 year, then archived indefinitely. Effective floor governed by `maintenance_work_record` via the chain in §13.4.

**Notes**

**`fk_oe_triggering_reading` is the single foreign key in this database that does not use `ON DELETE RESTRICT`, and the reason is precise.** Telemetry is purged at 90 days; events are retained a year or more. `RESTRICT` would either block the telemetry purge for every cited reading — pinning individual rows in otherwise-purgeable ranges and defeating any future partition-drop strategy — or fail the purge outright.

`SET NULL` is safe **only because** the frozen model deliberately captured `observed_value`, `threshold_value_breached`, and `detected_at` onto the event itself. When the reading is purged the lineage pointer becomes NULL and **the evidence survives intact.** This is the point-in-time capture in §3.5 of the operational document paying for itself: without it, `SET NULL` would destroy evidence and `RESTRICT` would deadlock retention against explainability. The design anticipated the conflict; this foreign key is where the anticipation is cashed in.

`ck_oe_threshold_event_complete` is a five-column conditional completeness check and the most valuable constraint on the table. It makes it impossible to record a threshold breach that cannot be cited as evidence.

---

### O14. `operational.operational_alert`

**Purpose**

The managed case correlating related events and carrying their human lifecycle: acknowledgement, escalation, resolution, closure. Converts a stream of detections into a bounded number of things a person is asked to deal with.

**Business Description**

Its central function is **correlation**. A degrading bearing can produce 34 events over eleven hours as a value flaps above and below a warning limit. Every one is a legitimate observation and none should be a separate notification. `correlation_key` is the deterministic rule that groups them — machine plus event category — so all condition events on one machine within an open window belong to one case. **That single mechanism is the difference between a platform that reduces cognitive load and one that adds to it.**

`resolution_type` is where the platform's honesty lives. An alert closed as `false_positive` is a permanent record that the platform raised something that did not matter, and Analytics aggregating those by threshold profile is what makes the tuning cycle evidence-based rather than a matter of opinion. **A monitoring system that cannot count its own false positives cannot be improved.**

`current_severity_level_id` versus `initial_severity_level_id` keeps the deterioration path visible rather than overwritten, letting Analytics distinguish *opened critical* from *deteriorated to critical*.

**No resolving-job reference.** A `resolved_by_work_record_id` column here would create **the only circular dependency in the operational schema**, because `maintenance_work_record.triggering_alert_id` already points the other way. Both directions are semantically real — a job is raised *because of* an alert and an alert is resolved *by* a job — which is exactly what made it a trap. The reference is kept on the child that knows its own cause at insert time, and the resolving job is derived by querying closed work records referencing the alert.

**Denormalised subject keys.** `machine_id`, `production_line_id`, and `inventory_item_id` duplicate the subject of the alert's events. This is a declared denormalisation: the dashboard queries open alerts by machine on every refresh, and resolving the subject through the event table each time would add a join to the platform's most frequent read.

**Primary Key**

`operational_alert_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `operational_alert_code` unique. Alerts are acknowledged, discussed, and referenced by humans.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `operational_alert_id` | BIGINT | NOT NULL | identity | Primary key |
| `operational_alert_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `ALR-yyyymmdd-nnnn` |
| `correlation_key` | VARCHAR(120) | NOT NULL | — | **The deduplication rule.** Deterministic, so correlation is reproducible rather than heuristic |
| `alert_category` | `operational.event_category` | NOT NULL | — | **Same ENUM type as `operational_event.event_category`** |
| `machine_id` | INTEGER | NULL | — | FK → `master.machine`. Declared denormalisation for dashboard reads |
| `production_line_id` | INTEGER | NULL | — | FK → `master.production_line` |
| `inventory_item_id` | INTEGER | NULL | — | FK → `master.inventory_item` |
| `initial_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level`. **Never changes** |
| `current_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level`. Escalates as events worsen |
| `alert_status` | `operational.alert_status` | NOT NULL | `'open'` | ENUM, 6 values |
| `event_count` | INTEGER | NOT NULL | `1` | **Maintained total.** Directly measures the noise correlation absorbed |
| `opened_at` | TIMESTAMPTZ | NOT NULL | — | At the first event |
| `first_event_at` | TIMESTAMPTZ | NOT NULL | — | Earliest correlated event |
| `last_event_at` | TIMESTAMPTZ | NOT NULL | — | **Staleness signal** — no recent events may mean self-recovery |
| `acknowledged_at` | TIMESTAMPTZ | NULL | — | NULL while unacknowledged |
| `acknowledged_by_worker_id` | INTEGER | NULL | — | FK → `master.worker`. **The human-in-the-loop audit trail** |
| `escalated_at` | TIMESTAMPTZ | NULL | — | NULL if acknowledged in time |
| `resolved_at` | TIMESTAMPTZ | NULL | — | When the condition ceased |
| `resolution_type` | `operational.alert_resolution_type` | NULL | — | ENUM, 5 values. **`false_positive` is the honesty mechanism** |
| `closed_at` | TIMESTAMPTZ | NULL | — | When the case was closed |
| `suppression_reason` | `operational.alert_suppression_reason` | NULL | — | ENUM, 5 values. **Distinguishes deliberate silence from failure** |
| `resolution_note` | TEXT | NULL | — | Required when closed |

Plus `created_at`, `created_by_component`, and `updated_at`.

**Constraints**

*Unique*
- `uq_oa_code` on (`operational_alert_code`)
- `uq_oa_open_correlation_key` — a **partial** unique index on (`correlation_key`) where `alert_status` IN (`open`, `acknowledged`, `escalated`). **The mechanism that prevents alert storms** — an event matching an open key joins that alert rather than creating another. This is the single most important constraint in the operational schema for noise control, and only a partial unique index can express it, since closed alerts may legitimately share a key with a new open one

*Check*
- `ck_oa_code_format` — matches `^ALR-[0-9]{8}-[0-9]{4}$`
- `ck_oa_event_count_positive` — `event_count` >= 1
- `ck_oa_opened_equals_first_event` — `opened_at` = `first_event_at`
- `ck_oa_last_event_not_before_first` — `last_event_at` >= `first_event_at`
- `ck_oa_timestamp_sequence` — `acknowledged_at`, `escalated_at`, `resolved_at`, `closed_at` each NULL or >= `opened_at`; and `closed_at` NULL or >= `resolved_at`
- `ck_oa_acknowledged_paired` — `acknowledged_at` IS NULL if and only if `acknowledged_by_worker_id` IS NULL
- `ck_oa_resolution_type_required` — `alert_status` NOT IN (`resolved`, `closed`) OR `resolution_type` IS NOT NULL
- `ck_oa_closed_requires_note` — `alert_status <> 'closed'` OR `resolution_note` IS NOT NULL
- `ck_oa_false_positive_requires_note` — `resolution_type <> 'false_positive'` OR `resolution_note` IS NOT NULL. **An unexplained false positive teaches nothing**, and these records drive threshold tuning
- `ck_oa_suppression_reason_required` — `alert_status <> 'suppressed'` OR `suppression_reason` IS NOT NULL
- `ck_oa_correlation_key_not_blank` — trimmed `correlation_key` length > 0

*Foreign Keys*
- `fk_oa_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oa_production_line` — → `master.production_line` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oa_inventory_item` — → `master.inventory_item` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oa_initial_severity` — → `master.failure_severity_level` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oa_current_severity` — → `master.failure_severity_level` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_oa_acknowledged_by` — → `master.worker` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.production_line`, `master.inventory_item`, `master.failure_severity_level` **twice**, `master.worker` | Master | Many-to-one each |
| Child | `operational.operational_event` | Operational | **One-to-many** |
| Referenced by | `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `maintenance_work_record`, `notification` | Operational | One-to-many each |

**This table has no outbound operational foreign keys**, making it an operational root at dependency layer 0. That is deliberate and it is what keeps the operational graph acyclic: everything downstream points **at** the alert, and a case does not depend on its own consequences.

**Read Components** — Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read or written by the Simulator.**

**Write Components** — **Monitoring Agent only.** Insert then update. Acknowledgement is captured through the Dashboard but **written by the Monitoring Agent**, so the table retains a single writer and the ownership model holds.

**Growth** — under 10 rows/day · ~2,500/year.

**Retention** — 2 years, then archived indefinitely. Effective floor 5 years via the chain in §13.4.

**Notes**

`uq_oa_open_correlation_key` deserves emphasis as the schema's clearest example of a partial unique index carrying architectural weight. It is not a performance optimisation — it is the database-level guarantee that concurrent Monitoring Agent evaluations cannot both create an alert for the same condition. Without it, two events arriving milliseconds apart could each find no open alert and each create one, and the correlation that makes the platform usable would silently degrade under load.

`current_severity_level_id` may only become **more** severe over an alert's life. That is a transition rule comparing a row to its previous version, which no declarative constraint can express; it is application-enforced and listed in §41.3. Allowing de-escalation would hide deterioration, which is why the rule exists.

---

## Group E — Prediction (O15–O16)

Split into the input and the answer, so that a prediction is **reproducible**: given a retained snapshot and a model version, re-running must return the identical result.

---

### O15. `operational.prediction_feature_snapshot`

**Purpose**

The exact feature vector used for one inference, with the data quality of the window it was computed from. The ML reproducibility contract and the deepest layer of prediction explainability.

**Business Description**

Raw telemetry becomes features: not *"vibration is 4.8"* but *"vibration has risen 0.31 mm/s per hour over four hours, is 6.7 % above its healthy maximum, and has spent 780 seconds above the warning limit."* Storing them makes three things possible — reproducibility, explainability at depth for the feature attributions cited in O16, and model comparison by re-scoring historical snapshots on a new version.

`data_completeness_pct` and `is_sufficient_for_inference` are the **quality gate**. Invalid readings are counted in `excluded_reading_count`, completeness is computed against expected sampling, and below threshold the snapshot is marked insufficient and **no prediction is produced**. Recording the insufficient snapshot rather than silently skipping is what makes the absence of a prediction explicable.

**Primary Key**

`prediction_feature_snapshot_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `prediction_feature_snapshot_code` unique. Snapshots are cited in model audit.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `prediction_feature_snapshot_id` | BIGINT | NOT NULL | identity | Primary key |
| `prediction_feature_snapshot_code` | VARCHAR(22) | NOT NULL | — | Business key. Format `FSN-yyyymmdd-nnnnn` |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `generated_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of feature computation |
| `window_from` | TIMESTAMPTZ | NOT NULL | — | Start of the lookback window |
| `window_to` | TIMESTAMPTZ | NOT NULL | — | End of the window |
| `lookback_window_seconds` | INTEGER | NOT NULL | — | Stored explicitly so a snapshot is interpretable without recomputing the interval |
| `feature_set_version` | VARCHAR(20) | NOT NULL | — | **Essential for reproducibility.** A vector without its definition is uninterpretable a year later |
| `feature_values` | JSONB | NOT NULL | — | **The feature vector.** Structure specified in §E15 of the operational document |
| `source_reading_count` | INTEGER | NOT NULL | — | Valid readings used |
| `excluded_reading_count` | INTEGER | NOT NULL | `0` | Readings dropped for quality. **Non-zero is a signal worth reading** |
| `data_completeness_pct` | NUMERIC(5,2) | NOT NULL | — | **The quality gate** |
| `is_sufficient_for_inference` | BOOLEAN | NOT NULL | — | **FALSE means no prediction is produced** |
| `insufficiency_reason` | `operational.snapshot_insufficiency_reason` | NULL | — | ENUM, 5 values. **Makes silence explicable** |
| `triggering_alert_id` | BIGINT | NULL | — | FK → O14. NULL for routine scheduled generation |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_pfs_code` on (`prediction_feature_snapshot_code`)

*Check*
- `ck_pfs_code_format` — matches `^FSN-[0-9]{8}-[0-9]{5}$`
- `ck_pfs_window_ordered` — `window_to` > `window_from`
- `ck_pfs_window_to_equals_generated` — `window_to` = `generated_at`
- `ck_pfs_lookback_positive` — `lookback_window_seconds` > 0
- `ck_pfs_counts_non_negative` — `source_reading_count` >= 0 AND `excluded_reading_count` >= 0
- `ck_pfs_completeness_range` — `data_completeness_pct` between 0 and 100
- `ck_pfs_insufficiency_reason_required` — `is_sufficient_for_inference = TRUE` OR `insufficiency_reason` IS NOT NULL. **An insufficient snapshot must state why**
- `ck_pfs_sufficient_has_no_reason` — `is_sufficient_for_inference = FALSE` OR `insufficiency_reason` IS NULL
- `ck_pfs_feature_values_is_object` — `feature_values` is a JSON object, not a scalar or array
- `ck_pfs_feature_set_version_not_blank` — trimmed `feature_set_version` length > 0

*Foreign Keys*
- `fk_pfs_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pfs_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pfs_triggering_alert` — → `operational.operational_alert` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.operational_alert` | Operational | Many-to-one, optional |
| Child | `operational.prediction_result` | Operational | **One-to-many** |

**Read Components** — Prediction Agent, Supervisor Agent (reads `data_completeness_pct` to judge how much weight to place on a prediction), Decision Agent, Analytics. **Not read by** the Monitoring Agent, Notification Service, or Dashboard.

**Write Components** — **Prediction Agent only.** Insert only, immutable.

**Growth** — ~170 rows/day · ~62,000/year.

**Retention** — 180 days stated. **Effective floor governed by `prediction_result` via the `NOT NULL` foreign key**, and transitively by `maintenance_work_record` at 5 years — see §13.4. Regenerable from telemetry only while telemetry survives at 90 days; permanently fixed thereafter, which is why the stated window deliberately exceeds telemetry's.

**Notes**

`feature_values` is `JSONB` rather than child rows, and the justification is access pattern rather than convenience. The vector is **written once and read whole** — the model needs the entire thing — and its shape varies by machine type because `master.machine_type_parameter.is_ml_feature` determines which parameters participate. Decomposing it into rows would add a join and a reassembly step to serve a query pattern that never occurs. The frozen master model argued the opposite case for threshold rules, which **are** queried per parameter; the principle is the same in both cases — model to the access pattern — and it produces different answers for different patterns.

`ck_pfs_feature_values_is_object` uses PostgreSQL's `jsonb_typeof` to reject a scalar or array where an object is required. This is cheap structural validation that prevents a malformed payload from being discovered at model-load time. Full schema validation of the document is an application responsibility, since the expected keys depend on `feature_set_version`.

---

### O16. `operational.prediction_result`

**Purpose**

One model inference: failure probability, risk classification, predicted failure mode, and the features that drove the score. The platform's quantitative risk assessment and **the sole origin of the ML confidence figure.**

**Business Description**

Immutable, reproducible from its snapshot, and **the only place in the platform where a failure probability is created.** That is a hard architectural rule: `PROJECT_OVERVIEW.md` §16.5 requires the Decision Agent to carry the number forward unchanged rather than forming its own estimate, because LLMs are poor numerical risk estimators and a restated probability would quietly lose calibration. Confining probability creation to this table is the structural enforcement.

`machine_type_failure_mode_id` bounds the prediction to a mode declared `is_model_predictable` for that machine's type, preventing the model from claiming to forecast something no signal precedes. `prediction_horizon_hours` converts a probability into a plan — 72 hours can be scheduled, 4 hours must be handled this shift, same number and entirely different action.

`risk_severity_level_id` maps probability onto the platform-wide severity scale so a prediction participates in the same escalation and notification machinery as a threshold breach rather than needing a parallel path.

**Primary Key**

`prediction_result_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `prediction_result_code` unique. The `PDN-` prefix avoids collision with master `PRD-` product codes.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `prediction_result_id` | BIGINT | NOT NULL | identity | Primary key |
| `prediction_result_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `PDN-yyyymmdd-nnnn` |
| `prediction_feature_snapshot_id` | BIGINT | NOT NULL | — | FK → O15. **The input.** With the model version, the complete reproducibility pair |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `predicted_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of inference |
| `model_name` | VARCHAR(60) | NOT NULL | — | Which model produced this |
| `model_version` | VARCHAR(20) | NOT NULL | — | **Mandatory.** A prediction whose producer is unknown cannot be reproduced or audited |
| `failure_probability` | NUMERIC(5,4) | NOT NULL | — | **The ML confidence. Created here and nowhere else** |
| `risk_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level` |
| `predicted_failure_category_id` | INTEGER | NULL | — | FK → `master.failure_category`. NULL when risk is elevated without an attributed mode |
| `machine_type_failure_mode_id` | INTEGER | NULL | — | FK → `master.machine_type_failure_mode`. **Constrains the prediction to a declared-predictable mode** |
| `prediction_horizon_hours` | INTEGER | NOT NULL | — | **Converts a number into a plan** |
| `confidence_band_low` | NUMERIC(5,4) | NULL | — | NULL when the model produces no interval |
| `confidence_band_high` | NUMERIC(5,4) | NULL | — | **A wide band is itself information** |
| `top_contributing_features` | JSONB | NOT NULL | — | **Feature attributions.** The model's account of its own reasoning |
| `triggering_alert_id` | BIGINT | NULL | — | FK → O14. NULL for scheduled scoring |
| `inference_duration_ms` | INTEGER | NOT NULL | — | Feeds health monitoring and cost tracking |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_pr_code` on (`prediction_result_code`)

*Check*
- `ck_pr_code_format` — matches `^PDN-[0-9]{8}-[0-9]{4}$`
- `ck_pr_probability_range` — `failure_probability` between 0 and 1. **`NUMERIC(5,4)` permits up to 9.9999, so the range check is load-bearing rather than decorative**
- `ck_pr_confidence_band_ordered` — both bands NULL, or `confidence_band_low` <= `failure_probability` <= `confidence_band_high`
- `ck_pr_confidence_band_range` — each band NULL or between 0 and 1
- `ck_pr_confidence_band_paired` — `confidence_band_low` IS NULL if and only if `confidence_band_high` IS NULL
- `ck_pr_horizon_positive` — `prediction_horizon_hours` > 0
- `ck_pr_inference_duration_non_negative` — `inference_duration_ms` >= 0
- `ck_pr_top_features_is_object` — `top_contributing_features` is a JSON object or array, not a scalar
- `ck_pr_model_version_not_blank` — trimmed `model_version` length > 0

*Foreign Keys*
- `fk_pr_snapshot` — `prediction_feature_snapshot_id` → `operational.prediction_feature_snapshot(...)` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_risk_severity` — → `master.failure_severity_level` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_failure_category` — → `master.failure_category` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_failure_mode` — → `master.machine_type_failure_mode` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_pr_triggering_alert` — → `operational.operational_alert` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `operational.prediction_feature_snapshot` | Operational | Many-to-one, **mandatory** |
| Parent | `master.machine`, `master.failure_severity_level`, `master.failure_category`, `master.machine_type_failure_mode`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.operational_alert` | Operational | Many-to-one, optional |
| Referenced by | `operational.supervisor_context`, `operational.ai_recommendation` | Operational | One-to-many each |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read by the Simulator** — reading this would make every accuracy measurement circular. **Not read by the Monitoring Agent** — detection is independent of prediction, which keeps the two layers separately testable.

**Write Components** — **Prediction Agent only.** Insert only, immutable.

**Growth** — ~170 rows/day · ~62,000/year.

**Retention** — 2 years stated. Effective floor 5 years via §13.4. **Predictions deliberately outlive their snapshots** because accuracy scoring against confirmed failures happens over long periods.

**Notes**

Two rules central to the platform's honesty are **cross-table and therefore application-validated**, and both are listed in §41.3:

- A prediction may only reference a snapshot with `is_sufficient_for_inference = TRUE`. The database cannot express this — a check constraint cannot read the parent row — and it matters because scoring on inadequate data returns a confident number with no basis.
- `machine_type_failure_mode_id` must reference a mode with `is_model_predictable = TRUE`, and `prediction_horizon_hours` should not exceed that mode's `typical_warning_period_hours`. Predicting further ahead than the physics gives warning for is not a forecast.

`ck_pr_probability_range` is worth calling out. `NUMERIC(5,4)` was chosen from the frozen specification and permits values up to 9.9999, so without the explicit range check a probability of 3.5 would be accepted and would propagate into a recommendation as ML confidence. This is a case where the type is necessary but not sufficient.

---

## Group F — Reasoning & Decision (O17–O19)

---

### O17. `operational.supervisor_context`

**Purpose**

The Supervisor Agent's escalation decision and the context package assembled for it. The audit trail of the platform's cost and noise gate.

**Business Description**

A row is written **either way** — escalated or suppressed — and that is this table's most important characteristic. If only escalations were recorded, the platform could not answer the question a manager asks after a surprising incident: *"the machine was showing symptoms, why didn't the system tell me?"* With suppressions recorded, the answer is a row: *probability 0.61 was evaluated against the 0.70 threshold in `BR-ESC-PROB` and did not escalate.* That is defensible and actionable — it points at the threshold, not at the platform.

`context_document` is the assembled package, preserved **exactly as the Decision Agent received it.** If the context were reassembled at audit time it would reflect current data rather than what was known at the decision moment, and *"why did it recommend that?"* would become unanswerable. **This is the input side of the explainability contract.**

**No business rule values are copied.** `applied_escalation_rule_id` references the governing `master.business_rule` row and nothing more, which is sufficient precisely because master data records superseded rule values as new rows rather than editing in place.

**Primary Key**

`supervisor_context_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `supervisor_context_code` unique.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `supervisor_context_id` | BIGINT | NOT NULL | identity | Primary key |
| `supervisor_context_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `CTX-yyyymmdd-nnnn` |
| `machine_id` | INTEGER | NULL | — | FK → `master.machine`. NULL for line- or inventory-scoped situations |
| `production_line_id` | INTEGER | NULL | — | FK → `master.production_line`. **The scope at which thresholds may be overridden** |
| `assembled_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of the decision |
| `triggering_alert_id` | BIGINT | NOT NULL | — | FK → O14. The case being evaluated |
| `triggering_prediction_id` | BIGINT | NULL | — | FK → O16. **NULL when an alert had no prediction** — itself a reason not to escalate |
| `related_alert_codes` | JSONB | NULL | — | Array of alert codes considered alongside |
| `escalation_decision` | `operational.escalation_decision` | NOT NULL | — | ENUM, 6 values. **Suppression reasons are enumerated so silence is always explicable** |
| `applied_escalation_rule_id` | INTEGER | NULL | — | FK → `master.business_rule`. **Referenced, never copied** |
| `escalation_rationale` | TEXT | NOT NULL | — | Plain-language reason, written for a manager |
| `context_document` | JSONB | NULL | — | Required when escalated. **Preserved as the LLM saw it** |
| `context_assembly_duration_ms` | INTEGER | NOT NULL | — | Feeds pipeline latency monitoring |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_sc_code` on (`supervisor_context_code`)

*Check*
- `ck_sc_code_format` — matches `^CTX-[0-9]{8}-[0-9]{4}$`
- `ck_sc_escalated_requires_context` — `escalation_decision <> 'escalated'` OR `context_document` IS NOT NULL. **Escalating without a package gives the Decision Agent nothing to reason over**
- `ck_sc_suppressed_has_no_context` — `escalation_decision = 'escalated'` OR `context_document` IS NULL. Assembling a package for a suppressed situation would waste the queries the gate exists to avoid
- `ck_sc_threshold_decisions_name_rule` — `escalation_decision` NOT IN (`escalated`, `suppressed_below_threshold`) OR `applied_escalation_rule_id` IS NOT NULL. **Both verdicts turn on a threshold and both must name it**
- `ck_sc_rationale_not_blank` — trimmed `escalation_rationale` length > 0
- `ck_sc_assembly_duration_non_negative` — `context_assembly_duration_ms` >= 0
- `ck_sc_context_document_is_object` — `context_document` IS NULL OR is a JSON object
- `ck_sc_related_alerts_is_array` — `related_alert_codes` IS NULL OR is a JSON array

*Foreign Keys*
- `fk_sc_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sc_production_line` — → `master.production_line` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sc_escalation_rule` — → `master.business_rule` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sc_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sc_triggering_alert` — → `operational.operational_alert` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_sc_triggering_prediction` — → `operational.prediction_result` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.machine`, `master.production_line`, `master.business_rule`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.operational_alert`, `operational.prediction_result` | Operational | Many-to-one each |
| Child | `operational.ai_recommendation` | Operational | **One-to-one in practice** |

**Read Components** — Decision Agent (consumes `context_document` as its entire input), Dashboard, Analytics. **Not read by** the Simulator, Monitoring Agent, or Prediction Agent.

**Write Components** — **Supervisor Agent only.** Insert only, immutable. It never writes a recommendation — orchestration and reasoning stay separate.

**Growth** — ~25 rows/day · ~9,000/year, of which roughly 8 % are escalations.

**Retention** — **Split by decision.** Escalated contexts 2 years, then archived with their recommendation; effective floor 5 years via §13.4. **Suppressed contexts 180 days**, since they are higher volume and their value is threshold tuning rather than long-term audit. This is the only table in the database whose retention window depends on a column value, and §44.5 records the consequence for the purge job.

**Notes**

The paired constraints `ck_sc_escalated_requires_context` and `ck_sc_suppressed_has_no_context` are worth reading together: they make `context_document` present exactly when escalated and absent otherwise, which is both the correctness rule and the cost-control rule. The 8-millisecond suppression versus 212-millisecond escalation gap observed in the frozen model lives in this pair.

---

### O18. `operational.ai_recommendation`

**Purpose**

The Decision Agent's output: an explainable, business-aware recommendation implementing every element of the `PROJECT_OVERVIEW.md` §16.5 contract. **The platform's actual product.**

**Business Description**

Everything upstream exists to produce this row.

**This table has no failure probability column, and that absence is the point.** The overview requires ML confidence in every recommendation and requires that it originate from the Prediction Agent and never be restated by the LLM. This schema enforces it **structurally**: `prediction_result_id` is `NOT NULL` and **there is nowhere to write a probability.** The Decision Agent cannot invent, round, or adjust the number because no column accepts one. A convention would have been broken eventually; a missing column cannot be.

`root_cause_failure_category_id` is `NOT NULL` and references the twelve-value controlled vocabulary in `master.failure_category`. The LLM **classifies within a validated set** rather than generating free-form causes, which is what makes the root cause checkable by an engineer, matchable to a maintenance specialisation, and linkable to a spare part.

`contract_complete` records whether all five mandatory elements were produced. A recommendation with `contract_complete = FALSE` must not be delivered as final.

**Primary Key**

`ai_recommendation_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `ai_recommendation_code` unique. Referenced by managers, cited in notifications, and recorded on the work orders it produces.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `ai_recommendation_id` | BIGINT | NOT NULL | identity | Primary key |
| `ai_recommendation_code` | VARCHAR(20) | NOT NULL | — | Business key. Format `REC-yyyymmdd-nnnn` |
| `supervisor_context_id` | BIGINT | NOT NULL | — | FK → O17. Every recommendation traces to one escalated context |
| `prediction_result_id` | BIGINT | NOT NULL | — | FK → O16. **The ML confidence element, by reference.** There is no probability column |
| `machine_id` | INTEGER | NOT NULL | — | FK → `master.machine` |
| `production_line_id` | INTEGER | NOT NULL | — | FK → `master.production_line` |
| `production_run_id` | BIGINT | NULL | — | FK → O4. Run at risk |
| `generated_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** |
| `llm_model_name` | VARCHAR(60) | NOT NULL | — | Which model reasoned |
| `llm_model_version` | VARCHAR(40) | NOT NULL | — | **Quality attribution** — a change in recommendation quality must be traceable to a model change |
| `priority_severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level` |
| `root_cause_failure_category_id` | INTEGER | NOT NULL | — | FK → `master.failure_category`. **From the controlled vocabulary** |
| `root_cause_confidence` | `operational.root_cause_confidence` | NOT NULL | — | ENUM, 3 values. Forces a stated hypothesis strength |
| `supporting_evidence` | JSONB | NOT NULL | — | **Contract element 1** |
| `business_impact` | JSONB | NOT NULL | — | **Contract element 4** |
| `recommended_action` | TEXT | NOT NULL | — | **Contract element 5** |
| `recovery_plan` | TEXT | NOT NULL | — | Recovery guidance including contingency |
| `suggested_maintenance_team_id` | INTEGER | NULL | — | FK → `master.maintenance_team` |
| `suggested_engineer_id` | INTEGER | NULL | — | FK → `master.maintenance_engineer` |
| `required_inventory_item_id` | INTEGER | NULL | — | FK → `master.inventory_item` |
| `estimated_downtime_minutes` | INTEGER | NULL | — | Failure mode estimate plus retrieval time |
| `recommended_action_by` | TIMESTAMPTZ | NULL | — | **Deadline**, derived from grace period and available windows |
| `reasoning_narrative` | TEXT | NOT NULL | — | What a manager reads to decide whether to trust it |
| `contract_complete` | BOOLEAN | NOT NULL | — | **All five §16.5 elements produced.** FALSE must not be delivered as final |
| `generation_duration_ms` | INTEGER | NOT NULL | — | LLM latency |
| `prompt_token_count` | INTEGER | NULL | — | **Cost monitoring** — the metric that proves the escalation gate pays for itself |
| `completion_token_count` | INTEGER | NULL | — | Output size |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_ar_code` on (`ai_recommendation_code`)
- `uq_ar_supervisor_context` on (`supervisor_context_id`) — **one recommendation per escalated context.** Enforces the one-to-one and prevents duplicate reasoning on the same package

*Check*
- `ck_ar_code_format` — matches `^REC-[0-9]{8}-[0-9]{4}$`
- `ck_ar_recommended_action_not_blank` — trimmed length > 0
- `ck_ar_recovery_plan_not_blank` — trimmed length > 0
- `ck_ar_reasoning_narrative_not_blank` — trimmed length > 0
- `ck_ar_supporting_evidence_is_object` — `supporting_evidence` is a JSON object
- `ck_ar_business_impact_is_object` — `business_impact` is a JSON object
- `ck_ar_action_deadline_after_generation` — `recommended_action_by` IS NULL OR > `generated_at`
- `ck_ar_estimated_downtime_positive` — `estimated_downtime_minutes` IS NULL OR > 0
- `ck_ar_generation_duration_non_negative` — `generation_duration_ms` >= 0
- `ck_ar_token_counts_non_negative` — each token count NULL or >= 0

*Foreign Keys*
- `fk_ar_supervisor_context` — → `operational.supervisor_context` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_prediction_result` — → `operational.prediction_result` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_production_line` — → `master.production_line` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_priority_severity` — → `master.failure_severity_level` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_root_cause_category` — → `master.failure_category` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_suggested_team` — → `master.maintenance_team` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_suggested_engineer` — → `master.maintenance_engineer` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_required_item` — → `master.inventory_item` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ar_production_run` — → `operational.production_run` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `operational.supervisor_context` | Operational | **One-to-one** |
| Parent | `operational.prediction_result`, `operational.production_run` | Operational | Many-to-one each |
| Parent | 8 master tables | Master | Many-to-one each |
| Child | `operational.recommendation_action`, `operational.notification` | Operational | One-to-many each |
| Referenced by | `operational.maintenance_work_record` | Operational | One-to-many |

**Read Components** — Notification Service, Dashboard, Analytics, and the Decision Agent itself as precedent. **Not read by the Simulator** — the simulator must never see recommendations about the factory it generates.

**Write Components** — **Decision Agent only.** Insert only. **Immutable** — the human response is a separate table precisely so the recommendation stays untouched. An editable recommendation could be quietly improved after the fact, which would destroy the audit trail entirely.

**Growth** — ~2 rows/day · ~700/year. **One of the smallest tables and the most valuable.**

**Retention** — 3 years, then archived indefinitely. Effective floor 5 years via `maintenance_work_record`. **Every table it cites is protected from purge by the `RESTRICT` chain**, so the evidence chain never breaks.

**Notes**

**The absence of a `failure_probability` column is the single most important physical design decision in this schema.** It converts an architectural principle into a structural impossibility. Every other enforcement of the ML-confidence rule — documentation, code review, a prompt instruction — could be circumvented; a missing column cannot.

`uq_ar_supervisor_context` enforces the one-to-one with the context and simultaneously prevents the duplicate-reasoning failure mode the Supervisor Agent's `suppressed_duplicate` decision guards against at the application layer. Belt and braces on the platform's most expensive operation.

**Not enforceable, and listed in §41.3:** `root_cause_confidence = 'high'` requires the `supporting_evidence` document to contain findings from at least two independent measurement paths; `required_inventory_item_id` must derive from the predicted failure mode's required part; and `suggested_engineer_id` must belong to the suggested team with valid certification. All three require reading other tables or parsing `JSONB` semantics.

---

### O19. `operational.recommendation_action`

**Purpose**

What the human actually decided about a recommendation. The human-in-the-loop audit record and the foundation of the decision feedback loop.

**Business Description**

`PROJECT_OVERVIEW.md` is unambiguous that the platform advises and the manager decides. This is where that decision is recorded, and it is the only place in the database capturing a human's judgement about the platform's output.

**A separate table so the recommendation stays immutable.** Putting `status = 'accepted'` on O18 would make the platform's product mutable, and a recommendation that can be edited after a human responds to it is not an audit record.

`action_taken = 'accepted_with_modification'` is the most informative value and the commonest real outcome: it says the reasoning was right and the scheduling judgement was incomplete. `rejection_reason` is enumerated so rejections aggregate into an improvement signal — `disagree_with_diagnosis` points at the model, `impractical_timing` at the scheduling logic, `insufficient_evidence` at confidence calibration. `no_action_taken` records the platform's worst outcome, worse than rejection because nobody engaged at all.

**Primary Key**

`recommendation_action_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. No business code: a child of a coded recommendation.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `recommendation_action_id` | BIGINT | NOT NULL | identity | Primary key |
| `ai_recommendation_id` | BIGINT | NOT NULL | — | FK → O18 |
| `action_taken` | `operational.recommendation_action_type` | NOT NULL | — | ENUM, 6 values |
| `actioned_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of the decision |
| `actioned_by_worker_id` | INTEGER | NOT NULL | — | FK → `master.worker`. Accountability |
| `response_time_minutes` | INTEGER | NOT NULL | — | **The platform's real effectiveness measure** |
| `modification_note` | TEXT | NULL | — | Required when accepted with modification. **The richest feedback the platform receives** |
| `rejection_reason` | `operational.rejection_reason` | NULL | — | ENUM, 6 values. Required when rejected |
| `rejection_note` | TEXT | NULL | — | Required when rejected |
| `deferred_until` | TIMESTAMPTZ | NULL | — | Required when deferred |
| `resulting_work_record_id` | BIGINT | NULL | — | FK → O11. **Proves a recommendation produced action** |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Check*
- `ck_ra_response_time_non_negative` — `response_time_minutes` >= 0
- `ck_ra_modification_note_required` — `action_taken <> 'accepted_with_modification'` OR `modification_note` IS NOT NULL. **An unexplained modification is lost feedback**
- `ck_ra_rejection_fields_required` — `action_taken <> 'rejected'` OR (`rejection_reason` IS NOT NULL AND `rejection_note` IS NOT NULL). **Rejections are the platform's most valuable improvement signal and an unexplained one teaches nothing**
- `ck_ra_rejection_fields_absent` — `action_taken = 'rejected'` OR (`rejection_reason` IS NULL AND `rejection_note` IS NULL)
- `ck_ra_deferred_until_required` — `action_taken <> 'deferred'` OR `deferred_until` IS NOT NULL
- `ck_ra_deferred_until_future` — `deferred_until` IS NULL OR `deferred_until` > `actioned_at`

**No unique constraint on `ai_recommendation_id`.** A recommendation may have **more than one** action row — a deferral followed by an acceptance is two decisions, and both are recorded. The latest by `actioned_at` is operative; earlier ones remain as history.

*Foreign Keys*
- `fk_ra_recommendation` — → `operational.ai_recommendation` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ra_actioned_by` — → `master.worker` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ra_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ra_resulting_work_record` — → `operational.maintenance_work_record` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `operational.ai_recommendation` | Operational | Many-to-one |
| Parent | `master.worker`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.maintenance_work_record` | Operational | Many-to-one, optional |

**Read Components** — Supervisor Agent, Decision Agent, Notification Service (stops escalating once an action is recorded), Dashboard, Analytics

**Write Components** — **Dashboard only**, the surface where a human records a decision. Insert only, immutable — a change of mind is a **new** action row so the decision sequence stays visible.

**Growth** — ~2 rows/day · ~700/year.

**Retention** — 3 years alongside its recommendation, then archived indefinitely.

**Notes**

**The least regenerable table in the database.** It records human judgement that exists nowhere else and cannot be reconstructed from any other data. That property is why it is append-only and why it is retained as long as the recommendation it responds to.

The four paired rejection and modification constraints make the enumerated feedback signal reliable. `ck_ra_rejection_fields_absent` is the less obvious half — it prevents an *accepted* recommendation carrying a stale rejection reason, which would corrupt the aggregate that drives threshold and prompt tuning.

`actioned_by_worker_id` must hold the authority the recommendation implies — a recommendation whose priority `requires_line_stop` must be actioned by somebody with `can_authorize_line_stop`. That is cross-table and application-validated per §41.3.

---

## Group G — Delivery (O20–O21)

---

### O20. `operational.notification`

**Purpose**

One message composed for one recipient, with the decision of whether to send it. Records what the platform said, to whom, and — when it stayed silent — why.

**Business Description**

**Suppression is recorded as a row, not as an absence.** A recipient skipped for quiet hours, a rate limit, or a severity floor still gets a row with `is_suppressed = TRUE` and a reason. Without this, *"was the supervisor told?"* would be answered by the absence of a row, and absence is ambiguous between deliberately suppressed, never composed, and lost to a bug. Suppressed notifications remain fully visible on the dashboard — **suppression stops transmission, never recording.**

**The message body is stored, not regenerated.** What the recipient actually saw is part of the audit trail; regenerating it later from the recommendation would produce current wording against a past decision.

`acknowledgement_deadline_at` is resolved and stored at composition from the severity's `max_acknowledgement_minutes`, because the escalation clock runs against it and a clock whose deadline is recomputed on every check is a clock that can drift.

**No contact details are stored here.** Endpoints resolve through `notification_recipient_id` to `master.worker`.

**Primary Key**

`notification_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with `notification_code` unique for delivery support queries.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `notification_id` | BIGINT | NOT NULL | identity | Primary key |
| `notification_code` | VARCHAR(22) | NOT NULL | — | Business key. Format `NTF-yyyymmdd-nnnnn` |
| `notification_recipient_id` | INTEGER | NOT NULL | — | FK → `master.notification_recipient`. Endpoints resolve through here |
| `notification_type` | `operational.notification_type` | NOT NULL | — | ENUM, 5 values |
| `ai_recommendation_id` | BIGINT | NULL | — | FK → O18. Required when type is `recommendation` |
| `operational_alert_id` | BIGINT | NULL | — | FK → O14 |
| `severity_level_id` | INTEGER | NOT NULL | — | FK → `master.failure_severity_level` |
| `composed_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of composition |
| `subject` | VARCHAR(200) | NOT NULL | — | Carries severity, machine, and deadline — many recipients decide whether to open from this line alone |
| `body_text` | TEXT | NOT NULL | — | **The message as sent.** Stored, not regenerated |
| `is_suppressed` | BOOLEAN | NOT NULL | `FALSE` | **Suppressed rows are still recorded and displayed** |
| `suppression_reason` | `operational.notification_suppression_reason` | NULL | — | ENUM, 6 values. Required when suppressed |
| `requires_acknowledgement` | BOOLEAN | NOT NULL | — | From the severity's `requires_manager_acknowledgement` |
| `acknowledgement_deadline_at` | TIMESTAMPTZ | NULL | — | **The escalation clock.** Required when acknowledgement is required |
| `escalation_order_applied` | INTEGER | NOT NULL | — | The recipient's position in the chain |
| `shift_id` | INTEGER | NOT NULL | — | FK → `master.shift` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Unique*
- `uq_nt_code` on (`notification_code`)

*Check*
- `ck_nt_code_format` — matches `^NTF-[0-9]{8}-[0-9]{5}$`
- `ck_nt_recommendation_required` — `notification_type <> 'recommendation'` OR `ai_recommendation_id` IS NOT NULL
- `ck_nt_suppression_reason_required` — `is_suppressed = FALSE` OR `suppression_reason` IS NOT NULL
- `ck_nt_suppression_reason_absent` — `is_suppressed = TRUE` OR `suppression_reason` IS NULL
- `ck_nt_ack_deadline_required` — `requires_acknowledgement = FALSE` OR `acknowledgement_deadline_at` IS NOT NULL
- `ck_nt_ack_deadline_after_composed` — `acknowledgement_deadline_at` IS NULL OR > `composed_at`
- `ck_nt_escalation_order_positive` — `escalation_order_applied` > 0
- `ck_nt_subject_not_blank` — trimmed `subject` length > 0
- `ck_nt_body_not_blank` — trimmed `body_text` length > 0

*Foreign Keys*
- `fk_nt_recipient` — → `master.notification_recipient` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_nt_severity` — → `master.failure_severity_level` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_nt_shift` — → `master.shift` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_nt_recommendation` — → `operational.ai_recommendation` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_nt_alert` — → `operational.operational_alert` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.notification_recipient`, `master.failure_severity_level`, `master.shift` | Master | Many-to-one each |
| Parent | `operational.ai_recommendation`, `operational.operational_alert` | Operational | Many-to-one each, optional |
| Child | `operational.notification_delivery` | Operational | **One-to-many** |

**Read Components** — Notification Service, Dashboard, Analytics

**Write Components** — **Notification Service only.** Insert only, immutable.

**Growth** — under 10 rows/day · ~2,500/year.

**Retention** — 1 year, then archived. Notifications for retained recommendations are protected by the `RESTRICT` chain.

**Notes**

The paired suppression constraints make `suppression_reason` present exactly when suppressed. The most important rule for this table is **not** enforceable in the database: at least one non-suppressed notification must exist for any severity carrying `requires_immediate_escalation`, with the departmental `escalation_email` as fallback if every eligible recipient is suppressed. **A critical recommendation reaching nobody is the platform's worst failure**, and §41.3 assigns the check to the Notification Service with a `system.audit_log` entry when the fallback fires.

---

### O21. `operational.notification_delivery`

**Purpose**

One transmission attempt on one channel, with its outcome. Answers the question the notification cannot: **did the message actually arrive?**

**Business Description**

Composing and delivering are different things, and the second fails in ways the first cannot predict. `delivery_status` distinguishes `sent` — the platform handed the message to a provider — from `delivered` — the provider confirmed receipt. **The gap between them is where silent failures live**, and a platform recording only `sent` will believe every message arrived.

`failure_reason` is enumerated so failures aggregate: `invalid_address` points at stale master data, `provider_error` at infrastructure, `rate_limited_by_provider` at volume. Each has a different fix.

**Primary Key**

`notification_delivery_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. No business code.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `notification_delivery_id` | BIGINT | NOT NULL | identity | Primary key |
| `notification_id` | BIGINT | NOT NULL | — | FK → O20 |
| `channel` | `operational.delivery_channel` | NOT NULL | — | ENUM, 2 values |
| `attempt_number` | INTEGER | NOT NULL | `1` | Retry sequence |
| `attempted_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of the attempt |
| `delivery_status` | `operational.delivery_status` | NOT NULL | — | ENUM, 6 values. **`sent` and `delivered` deliberately distinct** |
| `delivered_at` | TIMESTAMPTZ | NULL | — | Provider-confirmed time. Required when delivered |
| `provider_reference` | VARCHAR(120) | NULL | — | **Needed to investigate a disputed delivery** |
| `failure_reason` | `operational.delivery_failure_reason` | NULL | — | ENUM, 6 values. Required on failure |
| `failure_detail` | TEXT | NULL | — | Provider error text |
| `latency_ms` | INTEGER | NULL | — | Feeds health monitoring |

Plus `created_at`, `created_by_component`, and `updated_at`.

**Constraints**

*Unique*
- `uq_nd_notification_channel_attempt` on (`notification_id`, `channel`, `attempt_number`) — **retries are distinguishable per channel**, and this makes attempt recording idempotent

*Check*
- `ck_nd_attempt_number_positive` — `attempt_number` > 0
- `ck_nd_delivered_requires_timestamp` — `delivery_status <> 'delivered'` OR `delivered_at` IS NOT NULL
- `ck_nd_delivered_at_not_before_attempt` — `delivered_at` IS NULL OR >= `attempted_at`
- `ck_nd_failure_reason_required` — `delivery_status` NOT IN (`failed`, `bounced`, `rejected`) OR `failure_reason` IS NOT NULL
- `ck_nd_failure_reason_absent_on_success` — `delivery_status` NOT IN (`delivered`, `sent`, `queued`) OR `failure_reason` IS NULL
- `ck_nd_latency_non_negative` — `latency_ms` IS NULL OR >= 0

*Foreign Keys*
- `fk_nd_notification` — `notification_id` → `operational.notification(notification_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `operational.notification` | Operational | Many-to-one |

**This table has no master data references at all — the only one of the 53.** Its subject is entirely a transport concern and the recipient is reached through the notification, which makes it trivially portable if the delivery mechanism is ever replaced.

**Read Components** — Notification Service, Dashboard, Analytics

**Write Components** — **Notification Service only.** Insert per attempt, and `UPDATE` **only** to advance `delivery_status` from `sent` to `delivered` when the provider confirms asynchronously. **The single documented mutation in Group G**, and the reason this table carries `updated_at` while O20 does not.

**Growth** — under 20 rows/day · ~5,000/year.

**Retention** — 180 days, then purged. **The shortest retention in the database** — delivery mechanics have no long-term audit value once the notification itself is retained.

**Notes**

The retry policy is a business rule with a physical consequence: `failed` and `timeout` are transient and retryable; `bounced`, `rejected`, and `invalid_address` are **permanent** and must not be retried, because retrying a bounced address only generates more bounces. The status vocabulary makes the distinction expressible, and §41.3 assigns enforcement to the Notification Service.

A recurring `invalid_address` failure is a **master data quality problem**, not a delivery problem. This is the one place an operational data quality signal points back at a master data defect, and it is only possible because the two layers are cleanly separated.

---

## Group H — Platform & Observability (O22–O24)

`dashboard_snapshot` sits in the `operational` schema because its content is factory state. `audit_log` and `system_health_status` sit in `system` because their subject is the platform rather than the factory — a **physical placement decision only**, changing no logical grouping, ownership, or relationship. §9.3 justifies it.

---

### O22. `operational.dashboard_snapshot`

**Purpose**

A periodic materialised capture of factory state, so the dashboard renders from one row instead of aggregating across high-volume telemetry, and so historical state can be replayed.

**Business Description**

The dashboard needs machine states, OEE components, open alerts, run progress, risk scores, and stock warnings assembled and current. Computing that live means aggregating across telemetry, cycles, and counts for **every viewer**. A snapshot computes it once every five minutes regardless of how many people are looking.

It also makes historical replay a single indexed read rather than a reconstruction, which is what allows an incident to be reviewed as it appeared at the time rather than as it appears now.

**Fully derived and rebuildable**, which makes it the most disposable table in the database — it can be dropped entirely and rebuilt from its sources, so it carries no reconciliation obligation.

**Primary Key**

`dashboard_snapshot_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with a composite unique constraint making rebuilds idempotent.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `dashboard_snapshot_id` | BIGINT | NOT NULL | identity | Primary key |
| `snapshot_at` | TIMESTAMPTZ | NOT NULL | — | **Event time.** Aligned to the interval boundary |
| `snapshot_scope` | `operational.snapshot_scope` | NOT NULL | — | ENUM, 3 values |
| `production_line_id` | INTEGER | NULL | — | FK → `master.production_line`. Required when scope is `production_line` |
| `machine_id` | INTEGER | NULL | — | FK → `master.machine`. Required when scope is `machine` |
| `snapshot_document` | JSONB | NOT NULL | — | **The materialised aggregate** |
| `computed_from_window_seconds` | INTEGER | NOT NULL | — | **Stored so rate and OEE figures are interpretable** without knowing the job's configuration |
| `generation_duration_ms` | INTEGER | NOT NULL | — | Feeds health monitoring |

Plus `created_at`, `created_by_component`, and `updated_at` (rebuild).

**Constraints**

*Unique*
- `uq_ds_scope_subject_time` on (`snapshot_scope`, `production_line_id`, `machine_id`, `snapshot_at`) declared **`NULLS NOT DISTINCT`** — the idempotency guarantee. This is a genuine PostgreSQL-specific detail worth stating: by default PostgreSQL treats NULLs as distinct in unique constraints, so plant-scoped rows — which carry NULL in both subject columns — would **not** conflict and duplicate snapshots at the same instant would be accepted. `NULLS NOT DISTINCT` is available from PostgreSQL 15, which is why §Front Matter sets that as the minimum version

*Check*
- `ck_ds_line_scope_subject` — `snapshot_scope <> 'production_line'` OR `production_line_id` IS NOT NULL
- `ck_ds_machine_scope_subject` — `snapshot_scope <> 'machine'` OR `machine_id` IS NOT NULL
- `ck_ds_plant_scope_no_subject` — `snapshot_scope <> 'plant'` OR (`production_line_id` IS NULL AND `machine_id` IS NULL)
- `ck_ds_window_positive` — `computed_from_window_seconds` > 0
- `ck_ds_generation_duration_non_negative` — `generation_duration_ms` >= 0
- `ck_ds_snapshot_document_is_object` — `snapshot_document` is a JSON object

*Foreign Keys*
- `fk_ds_production_line` — → `master.production_line` · ON DELETE RESTRICT · ON UPDATE RESTRICT
- `fk_ds_machine` — → `master.machine` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.production_line`, `master.machine` | Master | Many-to-one each, optional |
| Derived from | `machine_operational_status`, `production_progress`, `production_count`, `operational_alert`, `prediction_result`, `inventory_movement` | Operational | Aggregation |

**Read Components** — Dashboard, Analytics. **Read by no agent** — agents read primary tables, and a presentation aggregate in a reasoning path would be an unnecessary and potentially stale dependency.

**Write Components** — **Dashboard only.** Insert on interval, with idempotent rebuild permitted.

**Growth** — ~288 rows/day at plant scope alone · ~105,000/year across all scopes.

**Retention** — 90 days at full interval, then downsampled to hourly and purged. **Fully disposable** with no reconciliation obligation, and a partition candidate per §44.3.

**Notes**

The three scope-consistency checks form a complete, mutually exclusive specification: exactly the right subject column is populated for each scope value. Together with the `NULLS NOT DISTINCT` unique constraint they make the table self-describing — a reader can determine what any row represents from the row alone.

**No agent reads this table**, and that restriction is recorded here rather than left implicit because it is the kind of boundary that erodes quietly. The first agent to read a snapshot for convenience would introduce a dependency on a rebuildable presentation artefact, and a stale or dropped snapshot would then affect reasoning rather than only rendering.

---

### O23. `system.audit_log`

**Purpose**

An append-only record of significant system and human actions across every component. The platform's own accountability record, distinct from the operational data it describes.

**Business Description**

Operational tables record what happened *in the factory*. This records what happened *in the platform*: which component did what, to which row, when, and whether it succeeded. That distinction matters when something goes wrong with the platform rather than the factory — a recommendation that never reached anybody, a prediction that failed to run, a threshold changed by an unnamed hand.

`correlation_id` is the table's most valuable column. One incident produces rows in a dozen tables across six components; a shared correlation identifier generated at first detection and carried through every subsequent step lets the whole pipeline pass be reconstructed with **one query** instead of joining a dozen tables on approximate timestamps.

**Not a debug log and not application tracing.** It records **significant** actions — state transitions, agent decisions, human actions, configuration changes, failures. Routine reads, loop iterations, and diagnostic output belong in application logs, which are not data and are not modelled here.

**Primary Key**

`audit_log_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`. No business code.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `audit_log_id` | BIGINT | NOT NULL | identity | Primary key |
| `occurred_at` | TIMESTAMPTZ | NOT NULL | — | **Event time** of the action |
| `component` | `system.platform_component` | NOT NULL | — | **Shared ENUM** with O24. Which component acted |
| `action_type` | `system.audit_action_type` | NOT NULL | — | ENUM, 9 values |
| `entity_name` | VARCHAR(60) | NULL | — | Table affected. NULL for component-level actions |
| `entity_id` | BIGINT | NULL | — | Row affected. **Deliberately not a foreign key** |
| `entity_code` | VARCHAR(32) | NULL | — | Business code. **Remains readable after the row is purged**, which is the point |
| `actor_worker_id` | INTEGER | NULL | — | FK → `master.worker`. **NULL for system actions** — the human/machine distinction is the audit's core question |
| `correlation_id` | VARCHAR(40) | NOT NULL | — | **Traces one pipeline pass end to end** |
| `outcome` | `system.audit_outcome` | NOT NULL | — | ENUM, 3 values |
| `action_detail` | JSONB | NULL | — | Structured particulars |
| `error_message` | TEXT | NULL | — | Required when outcome is `failure` |

Plus `created_at` and `created_by_component`. No `updated_at`.

**Constraints**

*Check*
- `ck_al_failure_requires_message` — `outcome <> 'failure'` OR `error_message` IS NOT NULL
- `ck_al_correlation_id_not_blank` — trimmed `correlation_id` length > 0
- `ck_al_entity_reference_paired` — `entity_id` IS NULL OR `entity_name` IS NOT NULL. A row identifier without a table name is unusable
- `ck_al_action_detail_is_object` — `action_detail` IS NULL OR is a JSON object

*Foreign Keys*
- `fk_al_actor_worker` — `actor_worker_id` → `master.worker(worker_id)` · ON DELETE RESTRICT · ON UPDATE RESTRICT

**`entity_id` is deliberately not a foreign key.** This is the schema's one intentional soft reference, and the reasoning is decisive: **the audit trail must survive the retention purge of the row it describes.** A foreign key would either block the purge or cascade the audit row away with its subject — and an audit log that disappears alongside what it audited is not an audit log. `entity_code` preserves human readability after the row is gone, which is why both columns exist.

**Relationships**

| Direction | Table | Kind | Cardinality |
|---|---|---|---|
| Parent | `master.worker` | Master | Many-to-one, optional |
| Soft reference | Any table, via `entity_name` + `entity_id` | — | **Not a foreign key** — see above |

**Read Components** — Platform diagnostics, Dashboard, Analytics. **No agent reads it** — an agent reading the audit trail would be reasoning about the platform rather than the factory, and that boundary is deliberate.

**Write Components** — **Platform audit interface**, on behalf of every component. All eight components emit entries through **one shared write path** rather than writing the table directly. §25.2 explains why this preserves single ownership in the sense that matters: one code path, one schema authority, one format. The exception is safe because the table is strictly append-only, every row carries `component` so provenance is explicit, and there is no state to corrupt.

**Growth** — ~2,000 rows/day · ~730,000/year. **The fourth-largest table.**

**Retention** — 1 year online, then archived **indefinitely and never deleted.** Configuration changes and human actions have permanent audit value. A partition candidate per §44.3.

**Notes**

Configuration changes to master data must be audited with before and after values in `action_detail`. **A threshold changed without an audit entry is an unexplainable change in platform behaviour**, and the tuning cycle described in the master document depends on knowing who changed what and when.

**Retention purges are themselves audited** with `action_type = 'retention_purge'`, recording the table, cut-off, and row count. The absence of data must itself be explicable — otherwise a purged range is indistinguishable from a period when the platform was not running.

---

### O24. `system.system_health_status`

**Purpose**

Current liveness, lag, and error state of each pipeline component. Answers *is the platform working* — a question no operational table can answer, because a stalled component produces no data at all.

**Business Description**

Every other table records what the platform did. This records whether it is still doing it.

The distinction is essential because **a stalled component is silent, and silence looks exactly like a healthy quiet period.** If the Prediction Agent stops running, no predictions appear, nothing errors, and the dashboard shows no risk on any machine — indistinguishable from a factory in perfect health. Without an explicit heartbeat the platform's most dangerous failure mode is invisible.

`processing_lag_seconds` is the most operationally useful column. A component can be alive and falling behind: the Monitoring Agent processing readings 400 seconds old is technically healthy and practically useless, because a 400-second-old threshold breach has already become a failure. Lag catches the degradation that liveness misses.

**Primary Key**

`system_health_status_id` — `BIGINT GENERATED ALWAYS AS IDENTITY`, with a **unique constraint on `component`** enforcing one row per component.

**Columns**

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `system_health_status_id` | BIGINT | NOT NULL | identity | Primary key |
| `component` | `system.platform_component` | NOT NULL | — | **UNIQUE.** Shared ENUM with O23 |
| `status` | `system.component_health_status` | NOT NULL | — | ENUM, 4 values. `degraded` means running but lagging or erroring intermittently |
| `last_heartbeat_at` | TIMESTAMPTZ | NOT NULL | — | **Liveness.** A stale heartbeat is the primary failure signal |
| `last_successful_run_at` | TIMESTAMPTZ | NULL | — | **Distinct from heartbeat** — a component can be alive and failing every cycle |
| `consecutive_failure_count` | INTEGER | NOT NULL | `0` | Drives the transition to `degraded` then `failed` |
| `processing_lag_seconds` | INTEGER | NULL | — | **Catches degradation that liveness misses** |
| `pending_backlog_count` | INTEGER | NULL | — | A growing backlog is a capacity problem before it is an outage |
| `last_error_at` | TIMESTAMPTZ | NULL | — | NULL if none since start |
| `last_error_message` | TEXT | NULL | — | Required when `last_error_at` is present |
| `metrics_document` | JSONB | NULL | — | Component-specific metrics, kept flexible because each measures different things |

Plus `created_at`, `created_by_component`, and `updated_at`.

**Constraints**

*Unique*
- `uq_shs_component` on (`component`) — one row per component for the platform's life

*Check*
- `ck_shs_failure_count_non_negative` — `consecutive_failure_count` >= 0
- `ck_shs_lag_non_negative` — `processing_lag_seconds` IS NULL OR >= 0
- `ck_shs_backlog_non_negative` — `pending_backlog_count` IS NULL OR >= 0
- `ck_shs_error_fields_paired` — `last_error_at` IS NULL if and only if `last_error_message` IS NULL
- `ck_shs_successful_run_not_after_heartbeat` — `last_successful_run_at` IS NULL OR <= `last_heartbeat_at`
- `ck_shs_error_not_after_heartbeat` — `last_error_at` IS NULL OR <= `last_heartbeat_at`
- `ck_shs_metrics_document_is_object` — `metrics_document` IS NULL OR is a JSON object

*Foreign Keys*

**None. The only table in the database with no foreign keys at all**, because its subject is a software component rather than a factory object.

**Relationships**

None. Fully standalone.

**Read Components** — Platform monitoring, Dashboard, Analytics. **Optionally read by the Supervisor Agent** to suppress escalation when the Prediction Agent is failed or badly lagging — **escalating on stale predictions is worse than not escalating**, because it produces confident recommendations from outdated evidence.

**Write Components** — **Platform only.** Each component reports its own status through the shared interface. Insert once per component, then update on heartbeat.

**Growth** — **~8 rows, fixed.** Zero net growth.

**Retention** — Permanent. Never archived, never purged. Historical health is recoverable from `system.audit_log`.

**Notes**

`ck_shs_successful_run_not_after_heartbeat` enforces the ordering that makes the two-timestamp design meaningful. A component alive at 18:50 whose last success was 18:49 is `degraded`; a single "last seen" timestamp would show it as healthy. Keeping both columns and constraining their order is what makes that gap visible.

**A stale heartbeat overrides the recorded `status`.** The platform must not trust a component's self-assessment once it has stopped reporting, so the staleness evaluation is a read-time comparison against `last_heartbeat_at` rather than a stored value. This is an application rule and it is recorded in §41.3 because getting it wrong means a crashed component that reported `healthy` on its final heartbeat stays `healthy` forever.

---

## 14. Operational table inventory

All 24, in dependency-layer order. Layers follow §13.4 of the operational document, and this ordering is the authoritative creation, seeding, and reverse-purge sequence.

| Layer | Tables | Schema |
|---|---|---|
| **0** | `production_run`, `operational_alert`, `dashboard_snapshot` | `operational` |
| **0** | `audit_log`, `system_health_status` | `system` |
| **1** | `machine_sensor_reading`, `production_progress`, `production_count`, `cycle_history`, `prediction_feature_snapshot` | `operational` |
| **2** | `operational_event`, `prediction_result` | `operational` |
| **3** | `quality_inspection_result`, `supervisor_context` | `operational` |
| **4** | `scrap_record`, `ai_recommendation` | `operational` |
| **5** | `notification`, `maintenance_work_record` | `operational` |
| **6** | `machine_maintenance_activity`, `inventory_movement`, `machine_state_transition`, `recommendation_action`, `notification_delivery` | `operational` |
| **7** | `machine_operational_status` | `operational` |

**Totals:** 5 + 5 + 2 + 2 + 2 + 2 + 5 + 1 = **24 tables.** Maximum operational dependency depth 7.

`machine_operational_status` occupying the deepest layer is correct — it is the most derived table in the database, a materialised summary of everything below it, and fully regenerable.

---

# Part V — Relationship Architecture

## 15. The three reference classes

| Class | Direction | Count | Physical rule |
|---|---|---|---|
| **Master → Master** | Within `master` | 51 | Foreign key. `RESTRICT` both actions |
| **Operational → Master** | `operational`/`system` → `master` | 78 | Foreign key. `RESTRICT` both actions |
| **Operational → Operational** | Within `operational` | 37 | Foreign key. `RESTRICT` both actions, **one documented `SET NULL`** |
| **Soft reference** | `system.audit_log` → anything | 1 | **Deliberately not a foreign key** |

**Total: 166 foreign keys and 1 soft reference across 53 tables.**

**`master` references nothing outside `master`.** Not once. The dependency runs strictly one way, which is what allows the master schema to be created, seeded, validated, restored, and reasoned about entirely independently.

## 16. Master → Operational reference map

The 78 references from operational tables into master, grouped by target. The ranking is informative about where the model's weight sits.

| Master table | Referenced by | Count |
|---|---|---|
| `shift` | 17 operational tables | **17** |
| `machine` | `machine_sensor_reading`, `machine_operational_status`, `machine_state_transition`, `production_count`, `cycle_history`, `quality_inspection_result` (×2), `scrap_record` (×2), `maintenance_work_record`, `operational_event`, `operational_alert`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `ai_recommendation`, `dashboard_snapshot` | **17** |
| `failure_severity_level` | `maintenance_work_record`, `operational_event`, `operational_alert` (×2), `prediction_result`, `ai_recommendation`, `notification` | **7** |
| `worker` | `quality_inspection_result`, `scrap_record`, `inventory_movement`, `machine_maintenance_activity`, `operational_alert`, `recommendation_action`, `audit_log` | **7** |
| `production_line` | `operational_event`, `operational_alert`, `supervisor_context`, `ai_recommendation`, `dashboard_snapshot`, `production_run` | **6** |
| `failure_category` | `quality_inspection_result`, `scrap_record`, `maintenance_work_record` (×2), `prediction_result`, `ai_recommendation` | **6** |
| `inventory_item` | `inventory_movement`, `operational_event`, `operational_alert`, `ai_recommendation` | **4** |
| `machine_parameter` | `machine_sensor_reading`, `operational_event` | **2** |
| `maintenance_team`, `maintenance_engineer` | `maintenance_work_record`, `ai_recommendation` | **2** each |
| `product`, `product_line_capability`, `customer` | `production_run` | **1** each |
| `inventory_location`, `supplier` | `inventory_movement` | **1** each |
| `machine_maintenance_schedule` | `maintenance_work_record` | **1** |
| `alert_threshold_rule` | `operational_event` | **1** |
| `machine_type_failure_mode` | `prediction_result` | **1** |
| `business_rule` | `supervisor_context` | **1** |
| `notification_recipient` | `notification` | **1** |

**`shift` and `machine` tie at 17 references each**, and the reason differs interestingly. `machine` is the platform's central subject, which is expected. `shift` reaching the same count was not planned — it emerged from applying the convention that every operational table records the shift it occurred in. That turns out to be right: segmenting by crew is the first thing any plant manager does with operational data, and a model requiring a timestamp-to-shift join would make the most common analysis the most expensive one.

**Two tables reference no master data.** `notification_delivery` is pure transport mechanics; `system_health_status` describes software. Both are cleanly separable from the manufacturing domain.

**Ten master tables are referenced by nothing operational** — `plant`, `plant_area`, `department`, `machine_category`, `machine_type`, `machine_type_parameter`, `worker_role`, `bill_of_materials`, `alert_threshold_profile`, `failure_severity_level`'s peers in the hierarchy. They are reached transitively, which is correct: an operational row references the leaf it concerns, not every ancestor.

## 17. Operational → Operational reference map

All 37, grouped by target.

| Target | Referenced by | Count |
|---|---|---|
| `production_run` | `machine_sensor_reading`, `machine_operational_status`, `machine_state_transition`, `production_progress`, `production_count`, `cycle_history`, `quality_inspection_result`, `scrap_record`, `inventory_movement`, `operational_event`, `ai_recommendation` | **11** |
| `operational_alert` | `operational_event`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `maintenance_work_record`, `notification` | **6** |
| `maintenance_work_record` | `machine_state_transition`, `machine_maintenance_activity`, `inventory_movement`, `recommendation_action` | **4** |
| `ai_recommendation` | `recommendation_action`, `notification`, `maintenance_work_record` | **3** |
| `operational_event` | `machine_state_transition`, `quality_inspection_result`, `scrap_record` | **3** |
| `prediction_result` | `supervisor_context`, `ai_recommendation` | **2** |
| `machine_sensor_reading` | `operational_event` | **1** |
| `prediction_feature_snapshot` | `prediction_result` | **1** |
| `supervisor_context` | `ai_recommendation` | **1** |
| `quality_inspection_result` | `scrap_record` | **1** |
| `scrap_record` | `inventory_movement` | **1** |
| `notification` | `notification_delivery` | **1** |
| `machine_state_transition` | `machine_operational_status` | **1** |
| `production_progress`, `production_count`, `cycle_history`, `machine_maintenance_activity`, `recommendation_action`, `notification_delivery`, `dashboard_snapshot`, `audit_log`, `system_health_status` | — | **0** |

## 18. One-to-one relationships

Four, each enforced by a `UNIQUE` constraint on the referencing column rather than by making the foreign key the primary key.

| Child | Parent | Enforced by | Why one-to-one |
|---|---|---|---|
| `master.maintenance_engineer` | `master.worker` | `uq_maintenance_engineer_worker` | Maintenance attributes belong to at most one worker record |
| `master.notification_recipient` | `master.worker` | `uq_notification_recipient_worker` | Notification policy per person |
| `operational.machine_operational_status` | `master.machine` | `uq_mos_machine` | Exactly one current-state row per machine |
| `system.system_health_status` | — | `uq_shs_component` | Exactly one row per component (no FK; the parent is a value, not a table) |
| `operational.ai_recommendation` | `operational.supervisor_context` | `uq_ar_supervisor_context` | One recommendation per escalated context |

**Why a surrogate PK plus a unique constraint rather than using the parent key as the PK.** Using `worker_id` as the primary key of `maintenance_engineer` would enforce the one-to-one structurally and save an index. It was rejected for consistency: all 53 tables use a surrogate identity, and two of these children (`maintenance_engineer`, and conceptually `ai_recommendation`) carry their own business code that staff genuinely use. Uniform identity handling also simplifies the ORM layer, which is a stated quality requirement.

## 19. One-to-many and many-to-one patterns

Every non-unique foreign key in the schema is a many-to-one from the referencing side and a one-to-many from the referenced side. Three patterns recur and are worth naming because they carry different physical implications.

**Pattern A — Reference lookup.** A high-volume operational table references a small master table. `machine_sensor_reading.machine_parameter_id` is the extreme case: 87,000 rows per day referencing a 7-row table. The physical consequence is that the referenced table is effectively always in cache, so the join is nearly free, and the foreign key check on insert is a single index probe.

**Pattern B — Parent-child within operational.** `notification` → `notification_delivery`, `maintenance_work_record` → `machine_maintenance_activity`. Both parents are low-volume and both children are bounded per parent. No cascade is used; §32.2 explains why.

**Pattern C — Optional causal link.** `machine_state_transition.triggering_event_id`, `maintenance_work_record.triggering_recommendation_id`, `scrap_record.related_operational_event_id`. All nullable, and the NULL carries meaning — *this happened for a routine reason*, as distinct from *this happened because the platform detected something*. These links are what make the platform's value measurable, and §28 sets out the general treatment of meaningful NULLs.

## 20. Many-to-many resolution

Five genuine many-to-many relationships, all in `master`, all resolved by a junction table carrying its own attributes.

| # | Relationship | Junction | Own attributes | Composite unique |
|---|---|---|---|---|
| 1 | `product` ↔ `production_line` | `product_line_capability` | 8 | (`product_id`, `production_line_id`) |
| 2 | `machine_type` ↔ `machine_parameter` | `machine_type_parameter` | 8 | (`machine_type_id`, `machine_parameter_id`) |
| 3 | `product` ↔ `inventory_item` | `bill_of_materials` | 5 | (`product_id`, `inventory_item_id`) |
| 4 | `machine_type` ↔ `failure_category` | `machine_type_failure_mode` | 8 | (`machine_type_id`, `failure_category_id`) |
| 5 | `alert_threshold_profile` ↔ `machine_parameter` | `alert_threshold_rule` | 8 | (`alert_threshold_profile_id`, `machine_parameter_id`) |

**No many-to-many exists in `operational`.** Every operational relationship is a foreign key, which is a direct consequence of the pipeline being a directed flow: each stage produces rows referencing what came before, and nothing needs a symmetric association.

**Each junction uses a surrogate PK plus a composite unique constraint**, not a composite primary key. The surrogate is what lets `production_run.product_line_capability_id` be a single column — a composite primary key on `product_line_capability` would have forced a two-column foreign key into the operational schema and from there into anything referencing runs.

## 21. Self-references and multi-role references

Five tables reference the same parent twice in distinct roles. None creates a cycle, because in every case the referenced table does not reference back.

| Table | Target | Roles |
|---|---|---|
| `master.bill_of_materials` | `master.inventory_item` | material, approved substitute |
| `master.alert_threshold_rule` | `master.failure_severity_level` | warning severity, critical severity |
| `master.maintenance_engineer` | — | `primary_specialization`, `secondary_specialization` are ENUM values, not references |
| `operational.quality_inspection_result` | `master.machine` | inspection location, defect attribution |
| `operational.scrap_record` | `master.machine` | recording location, defect attribution |
| `operational.maintenance_work_record` | `master.failure_category` | reported, confirmed |
| `operational.operational_alert` | `master.failure_severity_level` | initial, current |

**Every one is role-qualified in its column name.** `attributed_machine_id` versus `machine_id`, `confirmed_failure_category_id` versus `reported_failure_category_id`. This is a naming rule with a correctness purpose: an unqualified second reference is the commonest source of a wrong join.

**There is no recursive self-reference anywhere in the schema.** No table references itself. Multi-level bill-of-materials explosion was excluded in the frozen master model, and its absence means no query in this database requires a recursive CTE.

## 22. Cross-schema reference rules

| # | Rule | Physical enforcement |
|---|---|---|
| 1 | `master` references only `master` | Zero outbound cross-schema foreign keys exist |
| 2 | `operational` may reference `master` and `operational` | 78 and 37 foreign keys |
| 3 | `system` may reference `master` | 1 foreign key — `audit_log.actor_worker_id` |
| 4 | `system` does not reference `operational` | Enforced by the soft reference in `audit_log` |
| 5 | Nothing references `system` | No inbound foreign keys |
| 6 | Nothing references `analytics` | Reserved and empty |

Rule 4 is the interesting one and it is deliberate. `system.audit_log` describes operational rows without referencing them, which is exactly what allows the audit trail to outlive its subject. If `audit_log` held real foreign keys into `operational`, the retention purge would either be blocked by the audit trail or would delete it — and both outcomes defeat the purpose.

**Creation order is therefore unambiguous:** `master` → `operational` → `system` → `analytics`. Teardown reverses it.

## 23. Dependency layers and creation order

**Master schema — 5 layers, depth 4.**

| Layer | Tables |
|---|---|
| 0 | `plant`, `product`, `machine_category`, `machine_parameter`, `worker_role`, `supplier`, `customer`, `failure_severity_level` |
| 1 | `plant_area`, `department`, `shift`, `machine_type`, `failure_category` |
| 2 | `production_line`, `inventory_location`, `maintenance_team`, `alert_threshold_profile`, `machine_type_parameter` |
| 3 | `machine`, `worker`, `inventory_item`, `product_line_capability`, `alert_threshold_rule`, `business_rule` |
| 4 | `maintenance_engineer`, `notification_recipient`, `bill_of_materials`, `machine_type_failure_mode`, `machine_maintenance_schedule` |

**Operational and system schemas — 8 layers, depth 7.** Set out in §14.

**Combined creation order:** master layers 0→4, then operational and system layers 0→7. **No deferred constraints and no temporary NULLs are required at any point**, which is a direct benefit of the acyclic design and it means migrations can be written as a simple ordered sequence.

## 24. Acyclicity analysis

**Proof by layer assignment.** Every table is assigned a layer in which all of its foreign keys reference strictly lower layers, within its own schema or a lower schema. A graph admitting such an assignment is acyclic by construction: a cycle would require some table to reference its own layer or higher, and none does.

**Four cycles were designed out of the logical model and their absence is preserved here:**

| Would-be cycle | Natural instinct | Resolution in this schema |
|---|---|---|
| `department` ⇄ `worker` | `department.manager_worker_id` | Column absent. `worker_role.is_managerial` identifies managers |
| `production_line` ⇄ `worker` | `production_line.supervisor_worker_id` | Column absent. Role plus line assignment |
| `maintenance_team` ⇄ `maintenance_engineer` | `maintenance_team.team_lead_engineer_id` | Column absent. `maintenance_engineer.is_team_lead` |
| `operational_alert` ⇄ `maintenance_work_record` | `operational_alert.resolved_by_work_record_id` | Column absent. Derived from work records referencing the alert |

**The pattern, stated once for both schemas:**

> **A foreign key lives on the table that knows the fact at the moment the row is written. The reverse direction is derived, never stored as a back-pointer.**

The physical benefit is concrete: no `SET CONSTRAINTS DEFERRED`, no two-pass inserts, no nullable placeholder that must be filled in a second statement, and a single valid ordering for creation, seeding, and teardown.

**Nullable foreign keys are not cycles.** Roughly 40 of the 166 references are optional. Optionality expresses a business fact and every one still points strictly downward.

## 25. Data ownership and write boundaries

### 25.1 The rule

> **Every table has exactly one owning component. That component is the only one permitted to write it.**

Physically this is a `GRANT`-level policy, specified in Part XIII. It is not a convention.

| Owner | Tables | Count |
|---|---|---|
| **Administrative** (seed and edit) | All 29 `master` tables | 29 |
| **Factory Simulator** | O1–O12 | 12 |
| **Monitoring Agent** | `operational_event`, `operational_alert` | 2 |
| **Prediction Agent** | `prediction_feature_snapshot`, `prediction_result` | 2 |
| **Supervisor Agent** | `supervisor_context` | 1 |
| **Decision Agent** | `ai_recommendation` | 1 |
| **Notification Service** | `notification`, `notification_delivery` | 2 |
| **Dashboard** | `recommendation_action`, `dashboard_snapshot` | 2 |
| **Platform** | `audit_log`, `system_health_status` | 2 |

**The Supervisor and Decision Agents own one table each**, which is the clearest possible statement that their responsibilities are narrow.

### 25.2 Two documented ownership exceptions

**`recommendation_action` is owned by the Dashboard, not the Decision Agent.** The row records a *human* verdict on the platform's advice. Assigning it to the Decision Agent would mean the component producing advice also records the verdict on its own advice — the exact conflict the human-in-the-loop principle exists to avoid. Ownership follows the actor.

**`system.audit_log` is written by all eight components.** Ownership belongs to the **Platform audit interface**: a single shared write path through which components emit entries. They do not write the table directly. Single ownership is preserved in the sense that matters — one code path, one schema authority, one format — and the exception is safe for three reasons:

1. The table is strictly append-only. No component ever modifies another's rows.
2. Every row carries `component`, so provenance is explicit rather than inferred.
3. There is no state to corrupt. An audit entry is a fact, not a position in a lifecycle.

Physically this is expressed as `INSERT`-only privilege on `system.audit_log` granted to every application role, with no `UPDATE` or `DELETE` granted to any of them.

### 25.3 Acknowledgement: a write that crosses a surface

`operational_alert.acknowledged_at` and `acknowledged_by_worker_id` are set when a human acknowledges an alert **through the Dashboard**, yet the table is owned by the Monitoring Agent.

This is not a fourth exception. The Dashboard **submits** the acknowledgement; the Monitoring Agent **writes** it. Physically, `UPDATE` privilege on `operational_alert` is granted to the Monitoring Agent role only, and the Dashboard's acknowledgement action is a call into the Monitoring Agent rather than a direct write. Recording this explicitly matters because the alternative — granting the Dashboard `UPDATE` on the alert table — would be the easy shortcut and would silently give it write access to severity, status, and resolution as well.

## 26. Read boundaries

Ownership governs writes. Two **read** restrictions are equally architectural.

**The Factory Simulator reads no agent-produced table.** It has no `SELECT` on `operational_event`, `operational_alert`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `ai_recommendation`, `recommendation_action`, `notification`, `notification_delivery`, or `dashboard_snapshot`.

If the simulator could observe that a failure had been predicted, its behaviour could be influenced by that prediction and every accuracy measurement would become circular. **This is enforced by withheld privilege, not by code review.**

The one apparent exception is not one. The simulator creates `maintenance_work_record` rows carrying `triggering_recommendation_id`, which looks like reading a recommendation. The reference arrives through the **human decision** in `recommendation_action` — the simulator is modelling a manager acting on advice, which is a real causal path in the world, not the simulator reading the platform's conclusions. Physically, the simulator receives the recommendation identifier as a parameter; it does not query the recommendation table.

**No agent reads `dashboard_snapshot` or `system.audit_log`.** The first would introduce a dependency on a rebuildable presentation artefact; the second would mean an agent reasoning about the platform rather than the factory.

## 27. Deliberate non-relationships

References a reviewer might expect and will not find. Every one traces to a decision in the frozen logical models.

| Expected | Absent because |
|---|---|
| `department.manager_worker_id` | Cycle. Resolved via `worker_role.is_managerial` |
| `production_line.supervisor_worker_id` | Cycle. Resolved via role plus line assignment |
| `maintenance_team.team_lead_engineer_id` | Cycle. Resolved via `maintenance_engineer.is_team_lead` |
| `operational_alert.resolved_by_work_record_id` | **The only cycle in the operational schema.** Derived instead |
| `machine.plant_area_id` | Derivable through `production_line`. Storing it would let a machine claim a different area from its own line |
| `production_line.plant_id` | Derivable through `plant_area` |
| `production_line.machine_count` | Derivable. Would drift the first time a machine was added |
| `inventory_item.quantity_on_hand` | Operational. The running balance on `inventory_movement` |
| `inventory_item.machine_type_id` | Less precise than `machine_type_failure_mode.required_inventory_item_id` |
| `machine_maintenance_schedule.next_due_date` | Derived from operational history. Two sources of truth for one fact |
| `ai_recommendation.failure_probability` | **ML confidence must never be restated by the LLM.** Referenced only |
| `ai_recommendation.status` | Would make the platform's product mutable. The response is `recommendation_action` |
| `production_run.quantity_good` | Read from the terminal `production_progress` snapshot |
| `supplier` ↔ `inventory_item` junction | Multi-sourcing has no consumer |
| `customer` ↔ `product` master link | Transactional, established per run |
| `worker` ↔ `shift` roster junction | Shift rotation is operational scheduling with no consumer |
| Recursive `bill_of_materials` | Multi-level explosion has no consumer; avoids recursive CTEs entirely |
| `audit_log.entity_id` as a foreign key | Would block or cascade the purge of the row it audits |
| Any `master` → `operational` reference | The dependency is strictly one-way |
| Any machine control or setpoint table | **The platform is advisory. No control path exists** |

The last row is a hard architectural boundary inherited from `PROJECT_OVERVIEW.md`. **Among 53 tables and 166 references, not one represents an instruction to a machine.**

## 28. Nullability semantics

Roughly 40 foreign keys and many scalar columns are nullable, and in this schema **NULL almost always carries meaning** rather than indicating missing data. The distinction matters for query authors and for the ORM layer.

| NULL pattern | Example | Meaning |
|---|---|---|
| **Scope: universal** | `worker.production_line_id` | Plant-wide, not line-assigned |
| **Scope: global** | `business_rule.production_line_id` | Applies to all lines |
| **Scope: plant-level** | `dashboard_snapshot.machine_id` | Aggregate above machine level |
| **Not applicable** | `machine.downstream_buffer_units` | No buffer exists |
| **Not applicable** | `alert_threshold_rule.warning_low` | Low readings are not a concern for this parameter |
| **Causal absence** | `machine_state_transition.triggering_event_id` | Routine change, not detection-driven |
| **Lifecycle: not yet** | `maintenance_work_record.closed_at` | Job not closed |
| **Policy absent** | `customer.late_delivery_penalty_per_day` | No penalty clause |
| **History absent** | `machine_operational_status.operating_hours_at_last_maintenance` | Never serviced |
| **Deliberately unmeasured** | `prediction_result.confidence_band_low` | Model produces no interval |
| **Lineage severed** | `operational_event.triggering_reading_id` | Reading purged; evidence retained on the event |

**Where NULL would be ambiguous, it is forbidden.** Sixteen paired check constraints exist specifically to prevent a half-populated state that could be read two ways — `ck_qir_attribution_paired`, `ck_sr_attribution_paired`, `ck_oa_acknowledged_paired`, `ck_pr_confidence_band_paired`, `ck_shs_error_fields_paired`, `ck_mst_duration_consistency`, and the suppression and rejection pairs. Each turns a potentially ambiguous NULL into either a complete fact or an explicit absence.

## 29. Denormalisation register

Three declared denormalisations exist. Each is a deliberate trade of normal form for read performance, each is justified where it appears, and **each carries a reconciliation obligation** — that is the condition under which it was accepted.

| Column | Duplicates | Justification | Reconciled per |
|---|---|---|---|
| `machine_sensor_reading.machine_state_at_reading` | Derivable from `machine_state_transition` by time-range join | 87,000 rows/day; the join would be the most expensive query in the database | §41.6 |
| `operational_alert.machine_id`, `production_line_id`, `inventory_item_id` | Subject of the alert's events | The dashboard queries open alerts by machine on every refresh | §41.6 |
| `operational_event.threshold_value_breached` | `alert_threshold_rule` limit at detection time | **Evidentiary.** An event is quoted into LLM prompts and must be self-contained | Not reconciled — see below |

**The third is not reconciled, and deliberately so.** It is a **point-in-time capture**, not a cache: the master row holds current policy and the event holds a historical observation. They are *expected* to diverge once a profile is retuned, and reconciling them would be wrong. This is the single permitted master-value copy in the database, and §37 of the operational document establishes it.

## 30. Derived and maintained values

Five tables hold values obtainable from other data. Each is declared, and the declaration determines whether it can be rebuilt.

| Table or column | Derived from | Rebuildable while |
|---|---|---|
| `production_count` (whole table) | `cycle_history`, `machine_state_transition` | Cycles survive — 90 days |
| `production_progress` (whole table) | `production_count`, `machine_state_transition` | Counts survive — 2 years |
| `dashboard_snapshot` (whole table) | Six operational tables | Sources survive |
| `machine_operational_status` (whole row) | `machine_state_transition`, `cycle_history`, `maintenance_work_record` | History survives |
| `machine_operational_status.open_alert_count` | `operational_alert` | Always |
| `operational_alert.event_count` | `operational_event` | Events survive |

**Two become non-regenerable over time**, and the schema accounts for it. `production_count` is derived for its first 90 days and becomes a source of truth once cycles are purged — which is exactly why its retention is 2 years against cycles' 90 days. `prediction_feature_snapshot` is regenerable for 90 days and permanently fixed thereafter, which is why its stated window deliberately exceeds telemetry's.

## 31. Referential actions overview

| Action | Applied to | Count |
|---|---|---|
| `ON DELETE RESTRICT` | 165 of 166 foreign keys | **165** |
| `ON DELETE SET NULL` | `operational_event.triggering_reading_id` | **1** |
| `ON DELETE CASCADE` | **None** | **0** |
| `ON UPDATE RESTRICT` | All 166 | **166** |

`ON UPDATE RESTRICT` is uniform and effectively inert: every primary key in the schema is a `GENERATED ALWAYS AS IDENTITY` surrogate that is never updated. It is declared explicitly rather than left to the default so that the intent is unambiguous in the DDL, and so that an accidental attempt to update an identity value fails loudly.

## 32. Cascade, delete, and update policy

### 32.1 Uniform `RESTRICT`, and why

**Every foreign key uses `ON DELETE RESTRICT` except one.** This is the most consequential physical policy in the schema and the reasoning is specific to this platform.

`PROJECT_OVERVIEW.md` §16.5 requires that every recommendation be traceable to the evidence that produced it, and §8.4 of the operational document requires that any row cited by a retained recommendation be exempt from purge. **`RESTRICT` is the mechanism that enforces both.**

The consequence is that a deletion which would break the evidence chain **fails**, loudly, at the moment it is attempted. That failure is the correct outcome. It reveals that the retention job was about to remove something a recommendation still depends on, and it converts a silent explainability loss into a visible operational error.

Three secondary benefits follow:

- **Purge ordering becomes explicit.** A retention job must delete in reverse dependency order, and `RESTRICT` catches any ordering defect immediately rather than after data is gone.
- **Master data cannot be hard-deleted by accident.** Master tables use soft retirement, and `RESTRICT` on all 78 inbound references means a stray `DELETE` fails rather than orphaning years of history.
- **Retention windows become auditable.** §13.4's dependency finding was discovered *because* `RESTRICT` makes the pinning explicit. With `CASCADE` the same chain would have silently deleted evidence and nobody would have noticed until an audit asked for it.

### 32.2 Why `ON DELETE CASCADE` is used nowhere

`CASCADE` is the conventional choice for tightly-coupled children, and three pairs in this schema are the natural candidates:

| Candidate | Why `CASCADE` is rejected |
|---|---|
| `operational_event` → `operational_alert` | Events are **evidence**. Cascading from an alert could silently delete observations cited in a retained recommendation |
| `notification_delivery` → `notification` | Delivery outcomes are the record of whether a critical message arrived. Silent loss is unacceptable |
| `machine_maintenance_activity` → `maintenance_work_record` | Activity intervals feed service-level reporting and may be under dispute |

The common thread: **in this platform, a child row is frequently evidence for a claim the parent does not itself contain.** `CASCADE` optimises for convenience of deletion, and deletion convenience is worth very little here compared to the guarantee that evidence cannot vanish without an explicit, ordered, audited operation.

`CASCADE` would also have concealed the retention dependency chain in §13.4 entirely. That the chain was discoverable at all is a direct benefit of `RESTRICT`.

### 32.3 The single `SET NULL` exception

**`operational_event.triggering_reading_id` uses `ON DELETE SET NULL`.** This is the only non-`RESTRICT` referential action in the schema, and the justification is precise.

**The conflict.** Telemetry is purged at 90 days. Events are retained a year or more, and effectively five years under the §13.4 chain. `RESTRICT` would therefore either block the telemetry purge for every cited reading — pinning individual rows inside otherwise-purgeable ranges, and permanently defeating any future partition-drop strategy on the largest table in the database — or fail the purge outright.

**Why `SET NULL` is safe here and nowhere else.** The frozen model deliberately captured `observed_value`, `threshold_value_breached`, `threshold_direction`, and `detected_at` **onto the event itself**. When the reading is purged the lineage pointer becomes NULL and **the evidence survives completely intact.** The event can still state *"4.74 mm/s against a warning limit of 4.70 at 18:42:00"* because none of that lives in the reading.

This is the point-in-time capture of §29 paying for itself. Without it, `SET NULL` would destroy evidence and `RESTRICT` would deadlock retention against explainability. **The logical design anticipated the conflict; this foreign key is where the anticipation is cashed in.**

The column is already nullable in the frozen model — an event may legitimately have no single triggering reading — so `SET NULL` introduces no new state the application must handle. A NULL means either *no single reading* or *reading purged*, and §28 records that both are valid readings of the same absence.

### 32.4 Update policy

**Primary keys are never updated.** All 53 are `GENERATED ALWAYS AS IDENTITY`, which prevents an application from supplying or changing a value.

**Business codes may be updated.** `machine_code`, `product_code`, and their peers are `UNIQUE` but not primary keys, precisely so a renumbering is a single-row update rather than a cascade through years of operational history. This is the central justification for the surrogate-plus-code strategy in §42.

**Operational rows are updated only on the 8 mutable tables**, and only by the owning component. The 16 append-only tables have no `UPDATE` privilege granted to any role, which makes their immutability a database guarantee rather than a discipline.

### 32.5 Delete policy by schema

| Schema | Delete policy |
|---|---|
| `master` | **No hard deletes.** Soft retirement via `is_active`, or `lifecycle_status` on `machine`. `RESTRICT` on all 78 inbound references makes an accidental hard delete fail |
| `operational` | Deletion only by the retention job, in reverse dependency order, subject to the §13.4 floors |
| `system` | `system_health_status` is never deleted. `audit_log` is archived and **never deleted** |
| `analytics` | Empty |

**No application role holds `DELETE` on any table.** Retention is executed by a dedicated maintenance role, and every purge writes a `system.audit_log` entry recording the table, cut-off, and row count — so the absence of data is always explicable.

---

# Part VI — PostgreSQL Data Types

## 33. Type selection principles

Four principles govern every type decision in this schema, applied in order:

1. **Exactness over convenience.** Any value a human will check, a contract will reference, or an LLM will quote uses an exact type. No floating point appears anywhere.
2. **The type is the first line of validation.** A `CHAR(2)` country code, a `NUMERIC(5,4)` probability, and an `ENUM` state each reject a class of bad data before any check constraint runs.
3. **Model to the access pattern.** The same information is stored as rows or as a document depending on whether individual elements are queried. §39 works this through.
4. **Width matters where volume is high.** On a table growing 87,000 rows per day, four bytes per column is 128 MB per year. On an eight-row table it is irrelevant. Type width is optimised where it pays and ignored where it does not.

## 34. Numeric types

### 34.1 `NUMERIC(p,s)` — the default for all measured and monetary values

Used for every physical measurement, quantity, percentage, probability, and monetary amount. **Never `REAL` or `DOUBLE PRECISION`, anywhere in the schema.**

The reason is not theoretical. Three concrete failures are avoided:

- **Monetary drift.** `standard_selling_price − standard_material_cost` must produce an exact contribution margin, because the Decision Agent multiplies it by a unit count and states the result to a manager. Binary floating point cannot represent 4850.00 exactly, and the error compounds across a 108-unit calculation.
- **Threshold comparison.** A reading of exactly 4.7000 against a warning limit of exactly 4.7000 must compare equal. With floating point it may not, and the event either fires or does not depending on representation error.
- **Ledger integrity.** `inventory_movement`'s balance chain requires that previous balance plus delta equals the new balance exactly, across thousands of rows. One floating-point rounding breaks the chain and the reconciliation in §41.6 would report a phantom defect.

**Precision assignments follow the frozen documents exactly.** The pattern behind them:

| Precision | Applied to | Rationale |
|---|---|---|
| `NUMERIC(12,4)` | Physical measurements and thresholds — `reading_value`, `physical_min/max`, `nominal_value`, `normal_min/max`, all four threshold limits, `quantity_delta`, `resulting_quantity_on_hand`, `quantity_per_unit` | 4 decimal places matches sensor resolution and permits fractional consumption |
| `NUMERIC(12,2)` | Money and counted quantities — prices, costs, stock thresholds, `planned_quantity_units`, cumulative quantities, `accumulated_operating_hours` | 2 places is currency-natural and quantity-natural |
| `NUMERIC(14,2)` | `customer.annual_order_value` | An annual aggregate needs more headroom than a per-unit figure |
| `NUMERIC(10,2)` | Rates — `design_capacity_units_per_hour`, `max_hourly_output_units`, `current_rate_units_per_hour`, `rated_capacity_units_per_hour` | Rates are bounded well below measurement magnitudes |
| `NUMERIC(8,2)` | `cycle_time_seconds`, `rated_power_kw` | Both bounded in the hundreds |
| `NUMERIC(6,2)` | `deviation_from_standard_pct` | **Signed** and may exceed 100 |
| `NUMERIC(5,2)` | Percentages — `target_oee_percent`, `scrap_rate_pct`, `data_completeness_pct`, `sensor_accuracy_pct`, `on_time_delivery_pct` | 0.00–100.00 fits exactly |
| `NUMERIC(5,4)` | Probabilities — `failure_probability`, `confidence_band_low/high` | 4 decimal places on a 0–1 range |
| `NUMERIC(4,2)` | `criticality_weight` | 0.00–5.00 |
| `NUMERIC(2,1)` | `reliability_rating` | 0.0–5.0 |

**`NUMERIC(5,4)` deserves a note.** It permits values up to 9.9999, so a probability column of this type accepts 3.5. The type is necessary but not sufficient, which is why `ck_pr_probability_range` exists. This is the clearest example in the schema of a type and a check constraint doing different halves of one job.

### 34.2 `INTEGER` — counts, intervals, and master keys

Used for durations in named units (`*_minutes`, `*_seconds`, `*_hours`, `*_days`), counts, ranks, sequence positions, and all 29 master primary keys plus the 78 foreign keys referencing them.

**Durations are integers in explicit units, never `INTERVAL`.** PostgreSQL's `INTERVAL` type is expressive and it is deliberately avoided:

- `INTERVAL` has ambiguous arithmetic — adding one month to 31 January is undefined behaviour that PostgreSQL resolves by convention rather than by mathematics.
- Aggregating intervals is awkward compared to summing integers, and §O3's availability analysis is entirely `SUM(duration_in_previous_state_seconds)`.
- The frozen documents specify durations with the unit in the column name, and `duration_seconds` as an integer is unambiguous to every consumer including an LLM reading it.

The one cost is that the application must know the unit. The column name carries it, which is the naming rule in §43.

### 34.3 `BIGINT` — operational keys and unbounded counters

All 24 operational primary keys, the 37 operational foreign keys, `sequence_number` on two tables, and `accumulated_cycle_count`. §36.1 sets out the exhaustion arithmetic.

## 35. Temporal types

### 35.1 `TIMESTAMPTZ` — every instant, without exception

**All 108 timestamp columns in the schema are `TIMESTAMPTZ`. `TIMESTAMP` without a zone appears nowhere.**

`TIMESTAMPTZ` stores a UTC instant and renders it in the session or specified zone. `TIMESTAMP` stores a wall-clock reading with no zone, which means two rows written a second apart across a daylight-saving boundary can appear an hour apart or in the wrong order.

The platform is acutely exposed to this. `plant.timezone` is `Asia/Kolkata`, which has no daylight saving — but the type choice is not made for the current deployment. It is made because a recommendation stating *"vibration has been rising since the start of B shift"* depends on correct instant ordering, and a schema that works only in zones without DST is a latent defect rather than a design.

**Every table separates event time from record time**, and both are `TIMESTAMPTZ`:

| Event-time column | Tables |
|---|---|
| `recorded_at` | `machine_sensor_reading` |
| `transition_at` | `machine_state_transition` |
| `snapshot_at` | `production_progress`, `dashboard_snapshot` |
| `detected_at` | `operational_event` |
| `movement_at` | `inventory_movement` |
| `occurred_at` | `audit_log` |
| `generated_at`, `predicted_at`, `assembled_at`, `composed_at`, `inspected_at`, `recorded_at`, `activity_at`, `attempted_at`, `actioned_at`, `opened_at` | remaining tables |
| `created_at` | **All 53** — record time |

### 35.2 `DATE` — calendar dates with no meaningful time

Used for 17 columns where a time component would be noise: `commissioned_date`, `installation_date`, `hire_date`, `due_date`, `effective_from_date`, `baseline_start_date`, `qualification_expiry_date`, `certification_expiry_date`, `contract_expiry_date`, `review_due_date`, `warranty_expiry_date`, `introduced_date`.

**`production_run.due_date` is `DATE` deliberately.** A customer commitment is a day, not an instant. Storing it as `TIMESTAMPTZ` would force an arbitrary time-of-day choice that the Decision Agent would then reason against as though it were meaningful.

### 35.3 `TIME` — shift boundaries only

Two columns: `shift.start_time` and `shift.end_time`.

**`TIMETZ` is deliberately not used.** PostgreSQL's own documentation discourages it, and it is wrong here for a substantive reason: a shift starts at 06:00 **local** regardless of daylight saving, which a fixed UTC offset cannot express. The correct model is a zoneless local time interpreted against `plant.timezone`, which is exactly what `TIME` plus the plant row provides.

`crosses_midnight` exists as a stored boolean precisely because `TIME` alone cannot express a window spanning midnight, and `ck_shift_crosses_midnight_consistent` keeps it truthful.

## 36. Integer and identity strategy

### 36.1 Why operational keys are `BIGINT` and master keys are `INTEGER`

`INTEGER` exhausts at 2,147,483,647.

| Table | Rows/day | Years to exhaust `INTEGER` |
|---|---|---|
| `machine_sensor_reading` | 87,000 | ~67 |
| `cycle_history` | 3,500 | ~1,680 |
| `audit_log` | 2,000 | ~2,900 |

Sixty-seven years looks comfortable and is not. §49 establishes that this schema must accommodate factory expansion without change: at ten times the machine count and a finer sampling interval, telemetry reaches 2 million rows per day and `INTEGER` exhausts in **under three years.** Identity exhaustion in production is an outage requiring a table rewrite on the largest table in the database.

`BIGINT` costs four bytes per row — about 128 MB per year on telemetry at current volume — and removes the failure mode permanently.

**All 24 operational tables use `BIGINT` even the eight-row ones.** Consistency here is not aesthetic: `machine_operational_status.last_state_transition_id` must match `machine_state_transition`'s key type, and mixed key widths across a schema invite exactly the kind of subtle join defect that is expensive to find.

**All 29 master tables use `INTEGER`.** They are bounded at a few dozen rows permanently, and this halves the width of 78 foreign key columns — several of which sit on the highest-volume tables in the database.

### 36.2 `GENERATED ALWAYS AS IDENTITY` rather than `serial`

Applied uniformly to all 53 primary keys.

| Property | `GENERATED ALWAYS AS IDENTITY` | `serial` |
|---|---|---|
| Standard | SQL:2003 | PostgreSQL-specific |
| Sequence ownership | Owned by the column; dropped with the table | Loosely coupled; can be orphaned |
| Explicit insert | **Rejected** unless `OVERRIDING SYSTEM VALUE` | Silently permitted |
| Permission model | No separate sequence grant needed | Requires `USAGE` on the sequence |

**The rejection of explicit inserts is the decisive property.** The frozen master document §3.1 states that foreign keys reference the surrogate and application logic must never construct one. `ALWAYS` makes that a database guarantee: an ORM or a migration script attempting to supply an identity value fails loudly rather than creating a row whose key collides with a future generated value.

`BY DEFAULT` was considered for seed-data convenience — loading master data with known identifiers simplifies fixture authoring. It is rejected because that convenience is exactly the loophole `ALWAYS` closes, and seeds should reference rows by business code rather than by surrogate.

### 36.3 `SMALLINT` is not used

`SMALLINT` would fit several columns — `severity_rank` (1–9), `operating_days_per_week` (1–7), `line_position`, `attempt_number`. It is not used anywhere.

The saving is two bytes on columns that appear on low-volume tables, and the cost is a second integer width in the schema that every ORM mapping, every migration, and every reviewer must track. Uniform `INTEGER` for all non-key integers is the better trade at this scale, and §33 principle 4 makes width optimisation conditional on volume — which none of these columns has.

## 37. ENUM strategy

### 37.1 Native `ENUM` rather than `TEXT` with a check constraint

**65 native PostgreSQL `ENUM` types are declared, used across 101 columns.**

The alternative — `TEXT` plus `CHECK (col IN (...))` — is a defensible choice and is genuinely easier to migrate. Native `ENUM` was selected for four reasons, and the trade-off is stated honestly below.

| Property | Native `ENUM` | `TEXT` + `CHECK` |
|---|---|---|
| Storage | 4 bytes fixed | 1 byte + string length |
| Type safety | A value from the wrong vocabulary is a **type error** | Only the listed values are rejected; a typo in a different column's vocabulary passes if it happens to be valid there |
| Self-documentation | The type name appears in the column definition | The vocabulary is buried in a constraint expression |
| Shared vocabularies | One type, referenced by 5 columns, changed once | The list is duplicated in 5 constraints and must be kept aligned by hand |

**Storage matters on one table specifically.** `machine_sensor_reading` carries two ENUM columns and grows 32 million rows per year. As `TEXT`, `machine_state_at_reading` averages about 12 bytes plus overhead against 4 bytes for an ENUM — roughly 300 MB per year on one column of one table.

**Type safety matters most for the shared vocabularies.** `master.maintenance_specialization` is used by five columns across four tables, and the platform's team-matching logic depends on those five agreeing exactly. With `TEXT` and five separate check constraints, adding a discipline to four of them and forgetting the fifth is a silent defect that surfaces as a machine that can never be matched to a team. With one shared type it is impossible.

**The honest trade-off.** Native `ENUM` is harder to migrate:

- `ALTER TYPE ... ADD VALUE` cannot run inside a transaction block before PostgreSQL 12. The target is 15, so this is resolved.
- **Values cannot be removed or reordered.** This is a permanent constraint, not a version issue.
- **Alembic does not autogenerate `ENUM` changes.** Every vocabulary change requires a hand-written migration.

All three are accepted because they align with the frozen model's own discipline: master data is never deleted, vocabularies are stable reference data, and the master document §23 states that severity levels are effectively immutable because changing the scale would require re-evaluating every threshold rule and recipient filter. **A type that resists casual change is a feature here, not a defect.**

**The migration rule, stated so it is not discovered later:** ENUM vocabularies are **additive only.** A value is added by `ALTER TYPE ... ADD VALUE`, in a hand-written migration, with a `system.audit_log` entry. A value is never removed; it is retired by ceasing to write it, exactly as master rows are retired by `is_active`.

### 37.2 Shared ENUM types

Four types are used by more than one column, and each sharing is deliberate rather than incidental.

| Type | Values | Used by | Why shared |
|---|---|---|---|
| `master.maintenance_specialization` | 4 | `machine_category.primary_maintenance_specialization`, `maintenance_team.specialization`, `maintenance_engineer.primary_specialization`, `maintenance_engineer.secondary_specialization`, `failure_category.required_specialization` | **Matching a failed machine to a qualified team is a direct value comparison.** Separate types would make it an inference rule |
| `master.criticality_level` | 4 | `production_line.criticality`, `machine.criticality` | Prioritisation compares line and machine criticality together |
| `master.unit_of_measure` | 6 | `product.unit_of_measure`, `inventory_item.unit_of_measure` | Quantity arithmetic crosses the two. See §37.3 |
| `operational.event_category` | 5 | `operational_event.event_category`, `operational_alert.alert_category` | **Correlation depends on the alert's category matching its events' exactly** |
| `system.platform_component` | 8 | `audit_log.component`, `system_health_status.component`, and `created_by_component` on all 24 operational tables | One vocabulary for provenance across the whole database — **26 columns** |

**`system.platform_component` is the most widely used type in the schema**, and its placement in `system` follows the dependency rule in §37.5: a type used across schemas lives in the lowest-dependency schema that all users can reach.

**Maintenance obligation.** Adding a value to a shared type affects every user. `master.maintenance_specialization` in particular must be extended in all five columns' business logic simultaneously — adding a discipline without updating team matching produces machines that cannot be assigned. §41.3 records this as a cross-table rule.

### 37.3 The `unit_of_measure` case — one type, two permitted sets

The frozen master document gives `product.unit_of_measure` five values (`EA`, `KG`, `L`, `M`, `SET`) and `inventory_item.unit_of_measure` six (the same plus `BOX`). These are different vocabularies for the same concept.

Three options were available:

| Option | Assessment |
|---|---|
| Two separate ENUM types | Duplicates five of six values. Quantity arithmetic in `bill_of_materials` crosses product and item units, and two types would require a cast |
| One type with all six values, no narrowing | Would permit `BOX` on a product, contradicting the frozen model |
| **One type with six values plus a narrowing check on `product`** | **Selected** |

`master.unit_of_measure` carries all six values. `ck_product_unit_of_measure_allowed` restricts `product` to five. **Both frozen definitions are honoured exactly, the shared concept keeps one type, and the difference between them is visible as an explicit constraint** rather than as two near-identical types a reader must diff.

This is the general pattern for a vocabulary that is a superset relationship rather than two distinct concepts.

### 37.4 When a constrained vocabulary is deliberately *not* an ENUM

Three columns look like ENUM candidates and are correctly typed otherwise.

| Column | Type | Why not an ENUM |
|---|---|---|
| `machine_parameter.unit_of_measure` | `VARCHAR(16)` | **Units are an open set.** A new parameter may introduce `Pa`, `dB`, or `µm`. The value is displayed rather than compared, so there is no integrity benefit, and forcing `ALTER TYPE` per new instrument is friction with no return |
| `inventory_item.abc_class` | `CHAR(1)` + check | A three-value single-character classification. The check is lighter than a type declaration and equally safe, and the values are conventionally single letters rather than words |
| `country_code`, `currency_code` | `CHAR(2)`, `CHAR(3)` + check | ISO standards with hundreds of values maintained externally. An ENUM would need to enumerate ISO 3166 and ISO 4217 and track their revisions |

**The rule:** a vocabulary becomes an ENUM when it is **closed, small, stable, and compared**. Open sets, externally-maintained standards, and display-only values stay as constrained text.

### 37.5 ENUM type placement

An ENUM type lives in the schema of the tables that use it. A type used across schemas lives in the **lowest-dependency** schema all users can reach.

| Schema | Types | Note |
|---|---|---|
| `master` | 30 | Used only by master tables |
| `operational` | 31 | Used only by operational tables, including `machine_operational_state` which spans four operational columns |
| `system` | 4 | Includes `platform_component`, reachable from `operational` and `master` |

This respects the dependency direction established in §22: `operational` depends on `master` and `system`, never the reverse.

### 37.6 Complete ENUM catalogue

**`master` schema — 30 types**

| Type | Values |
|---|---|
| `area_type` | `production`, `assembly`, `warehouse`, `spare_parts_store`, `maintenance_workshop`, `quality_lab`, `dispatch`, `utility` |
| `access_restriction` | `general`, `authorized_only`, `restricted` |
| `department_function` | `production`, `maintenance`, `quality`, `warehouse`, `planning`, `engineering` |
| `shift_type` | `production`, `general`, `maintenance_only` |
| `line_type` | `machining`, `assembly`, `packaging`, `finishing`, `inspection` |
| `criticality_level` | `critical`, `high`, `standard`, `low` |
| `unit_of_measure` | `EA`, `KG`, `L`, `M`, `SET`, `BOX` |
| `quality_criticality` | `safety_critical`, `high`, `standard` |
| `capability_type` | `production_route`, `finishing_stage` |
| `equipment_class` | `rotating`, `robotic`, `conveying`, `static`, `metrology` |
| `maintenance_specialization` | `mechanical`, `electrical`, `automation`, `general` |
| `machine_lifecycle_status` | `in_service`, `standby`, `under_overhaul`, `decommissioned` |
| `measurement_domain` | `thermal`, `mechanical`, `electrical`, `tooling`, `pneumatic`, `hydraulic`, `positional` |
| `parameter_data_type` | `numeric_continuous`, `numeric_integer`, `boolean` |
| `degradation_direction` | `increasing`, `decreasing`, `bidirectional` |
| `drift_direction` | `increasing`, `decreasing`, `none` |
| `role_category` | `operator`, `technician`, `engineer`, `supervisor`, `manager`, `inspector`, `planner`, `storekeeper` |
| `employment_type` | `permanent`, `contract`, `apprentice` |
| `skill_level` | `trainee`, `junior`, `intermediate`, `senior`, `expert` |
| `inventory_location_type` | `raw_material_store`, `spare_parts_store`, `tooling_crib`, `wip_buffer`, `finished_goods_store`, `quarantine` |
| `inventory_item_type` | `raw_material`, `component`, `consumable`, `spare_part`, `tooling`, `finished_good` |
| `supplier_type` | `raw_material`, `component`, `spare_part`, `consumable`, `service` |
| `customer_priority_tier` | `gold`, `silver`, `bronze` |
| `failure_domain` | `mechanical`, `electrical`, `thermal`, `tooling`, `hydraulic`, `pneumatic`, `instrumentation`, `automation`, `process` |
| `relative_frequency` | `common`, `occasional`, `rare` |
| `maintenance_type` | `preventive`, `predictive`, `calibration`, `inspection`, `lubrication` |
| `interval_basis` | `calendar_days`, `operating_hours`, `cycle_count` |
| `threshold_sensitivity` | `tight`, `standard`, `relaxed` |
| `business_rule_category` | `escalation`, `prioritization`, `costing`, `notification`, `maintenance_policy`, `inventory_policy` |
| `business_rule_value_type` | `numeric`, `text`, `boolean` |

**`operational` schema — 31 types**

| Type | Values |
|---|---|
| `reading_quality_flag` | `valid`, `out_of_physical_range`, `sensor_offline`, `interpolated`, `stale` |
| `machine_operational_state` | `running`, `idle`, `setup`, `starved`, `blocked`, `down_unplanned`, `down_planned`, `offline` |
| `state_transition_reason` | `run_start`, `run_complete`, `changeover`, `tool_change`, `upstream_starvation`, `downstream_blockage`, `breakdown`, `planned_maintenance`, `quality_hold`, `operator_unavailable`, `shift_end`, `restored`, `asset_status_change` |
| `run_priority` | `normal`, `high`, `urgent` |
| `run_status` | `planned`, `setup`, `running`, `paused`, `completed`, `cancelled` |
| `run_pause_reason` | `machine_down`, `material_shortage`, `quality_hold`, `operator_unavailable`, `shift_end`, `higher_priority_run` |
| `cycle_outcome` | `good`, `scrap`, `rework` |
| `inspection_type` | `first_article`, `in_process`, `final`, `audit` |
| `inspection_disposition` | `accept`, `rework`, `scrap`, `quarantine` |
| `scrap_reason` | `dimensional_deviation`, `surface_defect`, `tool_mark`, `material_defect`, `setup_reject`, `machine_fault`, `handling_damage`, `process_deviation` |
| `inventory_movement_type` | `receipt`, `issue_production`, `issue_maintenance`, `return`, `adjustment`, `scrap_consumption`, `transfer_out`, `transfer_in` |
| `maintenance_work_type` | `preventive`, `predictive`, `corrective`, `emergency`, `calibration`, `inspection` |
| `maintenance_work_status` | `open`, `assigned`, `in_progress`, `awaiting_parts`, `completed`, `closed`, `cancelled` |
| `maintenance_activity_type` | `dispatched`, `arrived`, `diagnosis_started`, `diagnosis_complete`, `part_requested`, `part_collected`, `repair_started`, `repair_complete`, `test_run`, `handover`, `escalated`, `on_hold`, `resumed` |
| `event_category` | `machine_condition`, `machine_output`, `quality`, `inventory`, `data_quality` |
| `event_type` | `threshold_warning`, `threshold_critical`, `rate_of_change_exceeded`, `sustained_deviation`, `output_shortfall`, `cycle_deviation`, `scrap_rate_exceeded`, `quality_failure_rate`, `reorder_point_reached`, `safety_stock_breached`, `sensor_out_of_range`, `telemetry_stale` |
| `threshold_direction` | `above_high`, `below_low`, `rate_exceeded` |
| `alert_status` | `open`, `acknowledged`, `escalated`, `resolved`, `closed`, `suppressed` |
| `alert_resolution_type` | `auto_recovered`, `maintenance_performed`, `false_positive`, `superseded`, `manual_close` |
| `alert_suppression_reason` | `maintenance_in_progress`, `machine_offline`, `planned_downtime`, `duplicate_condition`, `rate_limited` |
| `snapshot_insufficiency_reason` | `completeness_below_threshold`, `sensor_fault`, `machine_not_running`, `window_spans_maintenance`, `insufficient_history` |
| `escalation_decision` | `escalated`, `suppressed_below_threshold`, `suppressed_duplicate`, `suppressed_maintenance_in_progress`, `suppressed_rate_limited`, `suppressed_insufficient_data` |
| `root_cause_confidence` | `high`, `moderate`, `low` |
| `recommendation_action_type` | `accepted`, `accepted_with_modification`, `rejected`, `deferred`, `superseded`, `no_action_taken` |
| `rejection_reason` | `disagree_with_diagnosis`, `impractical_timing`, `resource_unavailable`, `already_addressed`, `insufficient_evidence`, `business_priority_conflict` |
| `notification_type` | `recommendation`, `alert_escalation`, `acknowledgement_reminder`, `inventory_warning`, `system_health` |
| `notification_suppression_reason` | `quiet_hours`, `rate_limited`, `below_min_severity`, `recipient_inactive`, `channel_unavailable`, `already_acknowledged` |
| `delivery_channel` | `email`, `whatsapp` |
| `delivery_status` | `queued`, `sent`, `delivered`, `failed`, `bounced`, `rejected` |
| `delivery_failure_reason` | `invalid_address`, `provider_error`, `timeout`, `rate_limited_by_provider`, `recipient_blocked`, `message_too_large` |
| `snapshot_scope` | `plant`, `production_line`, `machine` |

**`system` schema — 4 types**

| Type | Values |
|---|---|
| `platform_component` | `simulator`, `monitoring_agent`, `prediction_agent`, `supervisor_agent`, `decision_agent`, `notification_service`, `dashboard`, `platform` |
| `audit_action_type` | `entity_created`, `entity_updated`, `state_transition`, `decision_made`, `human_action`, `configuration_changed`, `component_error`, `retention_purge`, `reconciliation_run` |
| `audit_outcome` | `success`, `failure`, `denied` |
| `component_health_status` | `healthy`, `degraded`, `failed`, `stopped` |

**Two ENUM types share value sets and are deliberately separate.** `alert_suppression_reason` (5 values) and `notification_suppression_reason` (6 values) describe different decisions at different pipeline stages — an alert suppressed because maintenance is in progress is not the same fact as a notification suppressed for quiet hours. Merging them would produce a type where half the values are invalid in each context, which is worse than two precise types.

## 38. Text types

### 38.1 `VARCHAR(n)` for bounded business values

In PostgreSQL `VARCHAR(n)` and `TEXT` are the **same storage with the same performance** — the length is a constraint, not an optimisation. Many schemas conclude from this that `TEXT` should be used everywhere.

This schema retains the specified lengths, because the length **is** a validation rule. A 150-character supplier name and a 200-character notification subject are business limits, and enforcing them in the column is cheaper and more reliable than in five application layers. The frozen documents specify them, and honouring them costs nothing.

`CHAR(n)` is used for exactly four columns where the value is genuinely fixed-width: `country_code` `CHAR(2)`, `currency_code` `CHAR(3)`, `abc_class` `CHAR(1)`, `display_color_hex` `CHAR(7)`. Anywhere else `CHAR` would introduce trailing-space semantics for no benefit.

### 38.2 `TEXT` for unbounded content

Used for 24 columns where content length is genuinely unpredictable: descriptions, notes, `escalation_rationale`, `recommended_action`, `recovery_plan`, `reasoning_narrative`, `body_text`, `resolution_note`, `leading_indicator_description`, `error_message`, `failure_detail`.

**`reasoning_narrative` and `body_text` are the significant ones.** Both hold LLM-generated prose whose length varies with the complexity of the situation, and both must be stored complete because they are the record of what a human actually read. Truncating either would silently damage the audit trail.

**Twelve `TEXT` columns carry a not-blank check constraint**, because the frozen models make several of them functionally mandatory. `failure_category.description` is the clearest case: it is supplied to the Decision Agent as grounding, so an empty one measurably degrades root-cause quality. This is one of very few places in any database where documentation text is a functional input rather than commentary.

### 38.3 Regex patterns on business codes

Twenty-three business code columns carry a format check constraint. `machine_code` matches `^MC-[0-9]{4}$`, `production_run_code` matches `^RUN-[0-9]{4}-[0-9]{4}$`, and so on.

**These are worth the cost.** Codes appear in notifications, in LLM prompts, and in conversation between operators, and a malformed code is a data quality defect that propagates into human communication. The check runs once per insert on tables that are almost all low-volume; only `operational_event`, `inventory_movement`, and `quality_inspection_result` carry a coded format check on a table exceeding 10 rows per day, and none exceeds 60.

**No format check is placed on the two highest-volume tables**, because neither `machine_sensor_reading` nor `cycle_history` has a business code — which is itself a consequence of the §42.2 rule that codes exist only where humans name the row.

## 39. `JSONB` strategy

### 39.1 `JSONB`, never `JSON`

Seven columns are document-valued, all `JSONB`:

| Column | Table |
|---|---|
| `feature_values` | `prediction_feature_snapshot` |
| `top_contributing_features` | `prediction_result` |
| `context_document`, `related_alert_codes` | `supervisor_context` |
| `supporting_evidence`, `business_impact` | `ai_recommendation` |
| `snapshot_document` | `dashboard_snapshot` |
| `action_detail` | `audit_log` |
| `metrics_document` | `system_health_status` |

`JSON` stores the input text verbatim, reparsing on every access and preserving insignificant whitespace and duplicate keys. `JSONB` stores a decomposed binary form: parsed once on write, supports containment and existence operators, and can be indexed. For payloads written once and read whole there is no scenario where `JSON` is preferable.

### 39.2 Why documents rather than child tables

This is the schema's most consequential type decision after the ENUM strategy, and it appears to contradict the frozen master model — which argued for `alert_threshold_rule` as **rows** rather than wide columns specifically so the Monitoring Agent could iterate generically.

The two decisions are the same principle applied to different access patterns:

| | `alert_threshold_rule` (rows) | These seven columns (documents) |
|---|---|---|
| Access | **Queried per element** — *what is the limit for `PRM-VIB`?* | **Written once, read whole.** No element is ever queried alone |
| Shape | Fixed and known | Varies by machine type, situation, and component |
| Consumer | An agent iterates it row by row | An agent or the UI consumes the entire payload |
| Right model | Child rows | Document |

**A feature vector is read entirely or not at all** — the model needs the whole thing. Decomposing it into a `prediction_feature_value` child table would add a join and a reassembly step to serve a query pattern that never occurs, and it would make the vector's variable shape a schema problem rather than a data one.

**The principle: model to the access pattern.** It produces rows for threshold rules and documents for feature vectors, and both answers are correct.

### 39.3 Structural validation only

Each document column carries a check constraint verifying the top-level type — `jsonb_typeof(col) = 'object'`, or `'array'` for `related_alert_codes`. Nothing deeper is validated in the database.

**Full schema validation is an application responsibility**, and deliberately so. The expected keys in `feature_values` depend on `feature_set_version`; the blocks in `context_document` depend on the situation. Encoding either in a check constraint would mean a migration every time a feature set or context block changed — precisely the coupling that made a document the right choice in the first place.

The structural check is still worth having: it costs almost nothing and it prevents a malformed payload — a scalar where an object is expected — from being discovered at model-load time inside the prediction pipeline.

### 39.4 What is deliberately not stored as `JSONB`

**No master data uses `JSONB`.** Every master attribute is a typed column, because master data is queried by attribute, validated by constraint, and edited by humans. A `JSONB` configuration blob would forfeit all three.

**No operational scalar is stored in a document.** `failure_probability` is a `NUMERIC` column, not a key inside `top_contributing_features`, because the Supervisor Agent compares it against a threshold and the comparison must be indexable and exact. The rule: **anything the platform compares, filters, or aggregates on is a column; anything it renders or reasons over as a whole is a document.**

## 40. Boolean, array, and UUID

### 40.1 `BOOLEAN`

Forty-one boolean columns, **all `NOT NULL` with an explicit default.** A nullable boolean has three states and forces every consumer to decide what NULL means; the frozen models never intend a third state, so none is permitted.

Defaults follow the safe interpretation rather than the common one:

| Column | Default | Why |
|---|---|---|
| `machine.is_monitored` | `TRUE` | A new machine is monitored unless declared otherwise |
| `machine.is_bottleneck` | `FALSE` | Bottleneck status is asserted, never assumed |
| `notification_recipient.email_enabled` | `TRUE` | Email is the baseline channel |
| `notification_recipient.whatsapp_enabled` | `FALSE` | Urgent channel is opt-in |
| `machine_maintenance_schedule.can_be_deferred` | `TRUE` | Most maintenance is deferrable; non-deferrable is the exception worth declaring |
| `product_line_capability.is_qualified` | `FALSE` | **Qualification must be asserted.** Defaulting TRUE would let an unqualified line become a reroute candidate silently |
| `notification.is_suppressed` | `FALSE` | Suppression is a decision, recorded explicitly |
| `cycle_history.interrupted` | `FALSE` | Interruption is the exception |
| `alert_threshold_rule.is_enabled` | `TRUE` | A configured rule is active unless suspended |

`is_qualified` defaulting to `FALSE` is the one worth emphasising: the safe default and the convenient default point in opposite directions, and the safe one wins.

### 40.2 Arrays are not used

PostgreSQL arrays are not used anywhere in this schema.

Two columns are natural candidates. `supervisor_context.related_alert_codes` is a list of codes and could be `VARCHAR(20)[]`; a `maintenance_engineer` could carry a certifications array.

Both are rejected:

- `related_alert_codes` is **`JSONB` instead**, because it is part of a document-oriented payload written and read whole alongside `context_document`, and mixing an array column with a document column for related data would be inconsistent.
- Certifications were excluded from the logical model entirely as a junction with no consumer.

**The general position:** an array is appropriate when the elements are homogeneous, order matters, and no element is ever joined to. In this schema, every candidate list either needs referential integrity — which arrays cannot provide — or belongs inside an existing document. Arrays would add a third collection idiom alongside rows and `JSONB` for no gain.

### 40.3 `UUID` is not used

No `UUID` column exists.

**`audit_log.correlation_id` is the obvious candidate** and is `VARCHAR(40)` per the frozen model, holding values like `inc-20260729-mc0101-a7f3`. That is a **semantic** correlation identifier — it encodes the date and the subject machine, so a human reading a log line can see what the trace belongs to without querying anything. A UUID would be opaque and would forfeit that, in exchange for global uniqueness this platform does not need: correlation identifiers are generated by one platform instance and never merged across systems.

**Surrogate keys are not UUIDs** either. There is no distributed generation requirement and no cross-system merge requirement. Integers are half the width, faster to join, index more densely, and are readable while debugging. A `BIGINT` identity on a 32-million-row table indexes considerably better than a random UUID, which scatters index inserts across the whole B-tree.

**If multi-plant deployment ever requires globally unique keys** — §49.6 treats this — the migration path is to add a plant discriminator rather than to convert 53 primary keys to UUID.

---

# Part VII — Constraint Strategy

## 41. Constraint layers

Integrity is enforced at four layers. Each catches a class the others cannot, and the assignment of a rule to a layer is a design decision rather than an accident of convenience.

| Layer | Enforces | Catches |
|---|---|---|
| **1. Type** | Domain, precision, vocabulary | Structurally impossible values |
| **2. Declarative constraint** | Keys, uniqueness, nullability, in-row predicates | Semantically wrong single rows |
| **3. Application validation** | Cross-table and cross-row rules | What no per-row predicate can see |
| **4. Scheduled reconciliation** | Maintained totals and denormalised values | Drift that accumulates silently |

### 41.1 Primary keys

53 primary keys, one per table, all `GENERATED ALWAYS AS IDENTITY`. Named `pk_<table>`.

**No composite primary key exists anywhere in the schema**, including on the five junction tables and the four one-to-one specialisations. §42.3 sets out why.

### 41.2 Declarative constraint inventory

| Constraint kind | Count | Naming |
|---|---|---|
| Primary key | 53 | `pk_<table>` |
| Foreign key | 166 | `fk_<table>_<column_root>` |
| Unique constraint | 41 | `uq_<table>_<columns>` |
| **Partial unique index** | **8** | `uq_<table>_<rule>` |
| Check constraint | ~210 | `ck_<table>_<rule>` |
| NOT NULL | ~430 columns | Column-level |

**The eight partial unique indexes are the schema's most interesting constraints**, because each enforces a business rule that no other declarative mechanism can express. §42.4 catalogues them.

### 41.3 Application-validated rules register

These rules come from the frozen logical models and **cannot** be expressed as declarative constraints. Each is recorded here with the reason and the owning component, so none is lost between design and implementation.

**Cross-table rules — master data**

| Rule | Why not declarative | Validated by |
|---|---|---|
| `plant.timezone` is a valid IANA name | Requires querying `pg_timezone_names`; `CHECK` must be `IMMUTABLE` | Seed and admin validation |
| A production line sits only in an area of type `production` or `assembly` | Reads the parent row | Seed and admin validation |
| An inventory location's type aligns with its area's type | Reads the parent row | Seed and admin validation |
| `production_line.station_count` >= count of active machines on the line | Aggregate over children | Admin validation |
| A machine's threshold profile must target the machine's own `machine_type_id` | Compares two parents | Admin validation |
| Tool wear may be declared only for types with `requires_tooling = TRUE` | Reads the parent row | Seed validation |
| Vibration may be declared only for types whose category has `is_rotating_equipment = TRUE` | Two-hop parent read | Seed validation |
| `machine_type_failure_mode.primary_machine_parameter_id` must be declared on that machine type | Reads a sibling junction | Seed validation |
| A mode with `is_model_predictable = TRUE` must have an indicator flagged `is_ml_feature = TRUE` | Reads a sibling junction | Seed validation |
| `alert_threshold_rule` may reference only parameters declared on the profile's machine type | Two-hop read | Admin validation |
| `critical_severity_level` must **outrank** `warning_severity_level` by `severity_rank` | Requires joining `failure_severity_level` twice | Admin validation |
| `failure_severity_level.target_response_time_minutes` increases as `severity_rank` increases | Cross-row comparison | Seed validation |
| **The full threshold ordering** across `machine_parameter`, `machine_type_parameter`, and `alert_threshold_rule` | Spans three tables | **Seed validation and every threshold edit** |
| `maintenance_team.department_id` must reference a `maintenance` function department | Reads the parent row | Seed validation |
| Only workers in a `maintenance` department may have a `maintenance_engineer` row | Reads a grandparent | Admin validation |
| `bill_of_materials.inventory_item_id` must not be a `finished_good` | Reads the parent row | Seed validation |
| A substitute item should share the material's `unit_of_measure` | Compares two parents | Seed validation |
| `business_rule.unit` is mandatory for dimensioned numerics | The database cannot know which numerics carry a dimension | Admin validation |
| A profile may not be soft-retired while active monitored machines reference it | `is_active` transitions cannot be constrained against children | Admin validation |
| Adding a value to `maintenance_specialization` must update all five consuming columns' logic | Type-level change with behavioural consequences | Migration review |

**Completeness rules — gate simulator start per §7**

| Rule | Consequence if violated |
|---|---|
| Every active line has at least one active machine and one active capability row | Line reports capacity but produces nothing |
| Every active product has at least one active capability row and one BOM line | Product cannot be made, or consumes nothing |
| Every active product has at least one `production_route` capability | Only finishing stages, no place it is actually made |
| Every monitored machine has a threshold profile | **Machine appears monitored and is not** |
| Every active profile has at least one active rule | **Profile monitors nothing while looking configured** |
| Every monitored machine type declares at least one ML-feature parameter | Prediction target with no features |
| Every machine type used by a monitored machine has at least one failure mode | Decision Agent has no root-cause vocabulary for that equipment |
| Every department has at least one active worker in a managerial role | Department manager cannot be resolved |
| Every active maintenance team has at least one active engineer | Team can be assigned work it cannot perform |
| A recipient with an enabled channel has the corresponding endpoint on `worker` | **Silent delivery failure that looks configured** |
| **At least one active recipient at the most severe level with `notify_outside_shift_hours = TRUE`** | **A critical failure at 03:00 reaches nobody** |
| Every `rule_category` a consumer depends on has at least one active global `business_rule` | Consumer has no policy and would default silently in code |
| Every active critical line has a line-scoped downtime cost rule | Impact can be described but not quantified |
| For every specialisation and production shift covering monitored equipment, at least one emergency-response team exists | A class of failure has no responder on that shift |

The last is a **coverage report** rather than a hard gate, because a genuine gap may be an accepted operational reality — but it must be visible rather than discovered during an incident.

**Cross-table rules — operational**

| Rule | Why not declarative | Owning component |
|---|---|---|
| A reading outside the parameter's physical range must be flagged, not rejected | Range lives on a parent; and rejection would hide instrument failure | Simulator |
| `machine_state_at_reading` agrees with the transition log at `recorded_at` | Cross-table time-range comparison | Reconciliation §41.6 |
| A prediction may reference only a snapshot with `is_sufficient_for_inference = TRUE` | Reads the parent row | Prediction Agent |
| `machine_type_failure_mode_id` must reference a mode with `is_model_predictable = TRUE` | Reads a master parent | Prediction Agent |
| `prediction_horizon_hours` should not exceed the mode's `typical_warning_period_hours` | Reads a master parent | Prediction Agent |
| `operational_alert.current_severity_level_id` may only become more severe | **Compares a row to its own previous version** | Monitoring Agent |
| An alert's subject keys must match its events' | Cross-table comparison | Reconciliation §41.6 |
| Closure of corrective, predictive, or emergency work requires `confirmed_failure_category_id` | Conditional on a work-type subset that will grow | Simulator |
| An assigned engineer must belong to the assigned team, hold valid certification, and be on shift or on call | Three parent reads plus a date comparison | Simulator, Decision Agent |
| `ai_recommendation.required_inventory_item_id` must derive from the predicted failure mode | Two-hop read | Decision Agent |
| `root_cause_confidence = 'high'` requires two independent measurement paths in `supporting_evidence` | Requires parsing `JSONB` semantics | Decision Agent |
| `recommendation_action.actioned_by_worker_id` must hold the authority the recommendation implies | Two-hop read to `worker_role` | Dashboard |
| At least one non-suppressed notification must exist for a severity with `requires_immediate_escalation`; otherwise the departmental fallback fires | Aggregate across siblings | Notification Service |
| `bounced`, `rejected`, and `invalid_address` are permanent and must not be retried | Retry policy, not a data shape | Notification Service |
| A stale heartbeat overrides the recorded `status` | Read-time comparison against `now()` | Platform |
| A cited row is exempt from retention purge | Aggregate across referencing tables | Retention job |

### 41.4 The `IMMUTABLE` limitation

PostgreSQL requires `CHECK` predicates to be `IMMUTABLE`. `CURRENT_DATE`, `now()`, and `CURRENT_TIMESTAMP` are `STABLE`, so **no "not in the future" rule can be a check constraint.**

The frozen documents state this rule on roughly 30 columns — every `recorded_at`, `detected_at`, `occurred_at`, `commissioned_date`, and their peers. All are affected.

**Uniform approach:**

| Rule shape | Enforcement |
|---|---|
| Value not in the future | **Application validation at write time.** All writers are platform components, so there is exactly one code path per table |
| Value not before a fixed reference in the same row | **Check constraint** — `commissioned_date >= installation_date` is `IMMUTABLE` and is enforced |
| Value not before a reference in a parent row | Application validation |

**Why not a trigger.** A `BEFORE INSERT` trigger could enforce future-dating, and it is rejected for the reason in §2: the ownership model requires that no database-resident code write or reject on a component's behalf, because provenance must stay unambiguous. Every writer here is a controlled platform component, not an ad-hoc client, so application validation is sufficient and keeps the enforcement visible in the code that owns the table.

The in-row temporal ordering constraints **are** enforced declaratively, and there are 19 of them — `ck_mwr_timestamp_sequence`, `ck_pr_planned_window_ordered`, `ck_ch_cycle_window_ordered`, `ck_oa_timestamp_sequence` and their peers. These catch the majority of temporal defects, since a corrupted clock or a swapped assignment usually violates an ordering rather than merely producing a future date.

### 41.5 Purge ordering

`RESTRICT` on 165 foreign keys means the retention job must delete in **reverse dependency order.** The authoritative sequence is §14's layering, walked from layer 7 down to layer 0:

```
7  machine_operational_status        (never purged — fixed row count)
6  notification_delivery · recommendation_action · machine_state_transition
   inventory_movement · machine_maintenance_activity
5  notification · maintenance_work_record
4  scrap_record · ai_recommendation
3  quality_inspection_result · supervisor_context
2  operational_event · prediction_result
1  cycle_history · production_count · production_progress
   prediction_feature_snapshot · machine_sensor_reading
0  production_run · operational_alert · dashboard_snapshot · audit_log
```

**A purge attempted out of order fails against `RESTRICT`, and that failure is the correct outcome.** It reveals an ordering defect before data is lost rather than after.

Three additional rules bind the purge job:

1. **Aggregate before purge.** `cycle_history` may be purged only after `production_count` for the same interval is written and reconciled; `machine_sensor_reading` only after the hourly downsample is written and verified.
2. **Honour the citation exemption.** Rows referenced by a retained `ai_recommendation` are exempt. `RESTRICT` enforces this automatically; the job must be written to expect and skip them rather than to fail.
3. **Audit every purge.** Each run writes a `system.audit_log` entry with `action_type = 'retention_purge'` recording table, cut-off, and row count. **The absence of data must itself be explicable.**

`supervisor_context` is the one table whose purge predicate depends on a column value — escalated rows at 2 years, suppressed rows at 180 days — and the job must therefore filter on `escalation_decision` rather than on age alone.

### 41.6 Reconciliation register

Four maintained totals and two denormalisations exist for performance. **Each carries a reconciliation obligation, and that obligation is the condition under which it was accepted.**

| Maintained value | Reconcile against | Frequency | On divergence |
|---|---|---|---|
| `machine_operational_status.accumulated_cycle_count` | Count of `cycle_history` per machine | Daily | Rebuild from history; raise a data quality incident |
| `machine_operational_status.accumulated_operating_hours` | Sum of `running` durations in `machine_state_transition` | Daily | Rebuild from history; raise an incident |
| `machine_operational_status.open_alert_count` | Count of open `operational_alert` per machine | Hourly | Recount from alerts |
| `operational_alert.event_count` | Count of `operational_event` per alert | Hourly | Recount from events |

| Denormalisation | Reconcile against | Frequency |
|---|---|---|
| `machine_sensor_reading.machine_state_at_reading` | `machine_state_transition` at `recorded_at` | Daily, sampled |
| `operational_alert` subject keys | Subject keys of its events | Hourly |

**Self-checking arithmetic invariants** that no per-row constraint can verify:

| Invariant | Table | Catches |
|---|---|---|
| Each balance = previous balance + delta, per item | `inventory_movement` | **The exact transaction where the ledger broke** |
| `duration_in_previous_state_seconds` = gap to previous transition | `machine_state_transition` | Missing or out-of-order transitions |
| `duration_from_previous_seconds` = gap to previous activity | `machine_maintenance_activity` | Missing timeline entries |
| `cycle_time_seconds` = `cycle_ended_at` − `cycle_started_at` | `cycle_history` | Timestamp or duration corruption |
| Cumulative quantities non-decreasing across snapshots | `production_progress` | Snapshot ordering defects |
| `production_count` totals match aggregated `cycle_history` | `production_count` | Aggregation defects |

**`operational_event.threshold_value_breached` is deliberately excluded from reconciliation.** It is a point-in-time capture, not a cache: the master row holds current policy and the event holds a historical observation. They are *expected* to diverge once a profile is retuned, and reconciling them would be wrong. §29 establishes this.

## 42. Key strategy

### 42.1 Surrogate keys

**Every table has a surrogate identity primary key.** No exceptions across 53 tables.

The rationale differs by schema and both matter:

**Master.** Business codes change — a plant renumbers its lines, a product code is revised by engineering change. With a natural primary key that rename cascades into every referencing row and into years of operational history. With a surrogate it is a single-column update on one row. `product_code` is the sharpest case, since engineering revisions are routine.

**Operational.** Volume. `machine_id` is referenced by roughly 32 million telemetry rows per year, and those references must survive a machine being renumbered, relocated to another line, or re-tagged for finance.

### 42.2 Natural keys as unique business codes

**32 tables carry a `_code` column with a unique constraint and a format check.** 21 tables do not.

The rule, from the frozen models: **a code exists where a human or an AI-generated message refers to the row.**

| Has a code | Reason |
|---|---|
| All 29 master tables except `product_line_capability`, `machine_type_parameter`, `bill_of_materials`, `machine_type_failure_mode`, `alert_threshold_rule`, `notification_recipient` | People name machines, products, lines, workers, and policies |
| `production_run`, `operational_event`, `operational_alert`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `ai_recommendation`, `notification`, `maintenance_work_record`, `inventory_movement`, `quality_inspection_result` | Cited in recommendations, notifications, or shop-floor conversation |

| Has no code | Reason |
|---|---|
| Six master junction and configuration tables | They qualify a relationship or extend a parent; nobody names them |
| Thirteen operational tables | High-volume rows or child rows of a coded parent |

**Codes are `UNIQUE` but never primary keys.** That is precisely what makes a renumbering a one-row update.

**The `PDN-` prefix on `prediction_result` deserves a note.** The natural choice was `PRD-`, which collides with master product codes. A collision between a product and a prediction inside an LLM-generated message would be genuinely confusing, so the prefix was changed. This is a small decision with a real consequence, and it illustrates that code formats in this schema serve machine-generated prose as well as humans.

### 42.3 Composite keys

**No composite primary key exists in the schema.** Five junction tables and four one-to-one specialisations are the natural candidates, and all use a surrogate plus a composite `UNIQUE`.

| Table | Composite unique | Why not a composite PK |
|---|---|---|
| `product_line_capability` | (`product_id`, `production_line_id`) | `production_run` references it. A composite PK would force a two-column foreign key into the operational schema and from there into anything referencing runs |
| `machine_type_parameter` | (`machine_type_id`, `machine_parameter_id`) | Consistency, and a stable row identity if a pairing is re-created |
| `bill_of_materials` | (`product_id`, `inventory_item_id`) | Consistency |
| `machine_type_failure_mode` | (`machine_type_id`, `failure_category_id`) | `prediction_result` references it |
| `alert_threshold_rule` | (`alert_threshold_profile_id`, `machine_parameter_id`) | `operational_event` references it |
| `machine_sensor_reading` | (`machine_id`, `sequence_number`) | `operational_event` cites individual readings; a three-column natural key would propagate into the event table |

**The pattern:** a composite natural key is correct until something references the row. Five of these six are referenced, and the sixth follows the same convention for uniformity.

**Eleven composite `UNIQUE` constraints exist in total**, and several serve a second purpose beyond identity: they make writes **idempotent.** `uq_pc_machine_interval`, `uq_pp_run_snapshot`, `uq_ch_machine_run_cycle`, `uq_nd_notification_channel_attempt`, and `uq_ds_scope_subject_time` each mean a retried job updates one row rather than inserting a duplicate. On `production_count` and `dashboard_snapshot` — both rebuildable — this is what makes rebuild safe.

### 42.4 Partial unique index register

Eight partial unique indexes, each enforcing a rule no other declarative mechanism can express. **These are the schema's highest-value constraints** and they are catalogued together because they are easy to omit during implementation.

| Index | Table | Predicate | Rule enforced |
|---|---|---|---|
| `uq_shift_sequence_order_production` | `master.shift` | `shift_type = 'production'` | Rotation order unique among production shifts only; the general shift is excluded |
| `uq_plc_primary_route_per_product` | `master.product_line_capability` | `capability_type = 'production_route' AND is_primary_line AND is_active` | **Exactly one primary production route per product** |
| `uq_machine_bottleneck_per_line` | `master.machine` | `is_bottleneck = TRUE` | **At most one bottleneck per line.** Two would make impact arithmetic contradictory |
| `uq_maintenance_engineer_team_lead` | `master.maintenance_engineer` | `is_team_lead = TRUE AND is_active` | Exactly one lead per team |
| `uq_alert_threshold_profile_default` | `master.alert_threshold_profile` | `is_default = TRUE AND is_active` | Exactly one default profile per machine type |
| `uq_production_run_active_per_line` | `operational.production_run` | `run_status IN ('setup','running','paused')` | **At most one active run per line.** The primary concurrency guard for the Simulator transaction |
| `uq_oa_open_correlation_key` | `operational.operational_alert` | `alert_status IN ('open','acknowledged','escalated')` | **The alert-storm prevention mechanism.** Closed alerts may legitimately share a key with a new open one |
| `uq_ds_scope_subject_time` | `operational.dashboard_snapshot` | `NULLS NOT DISTINCT` | Idempotent rebuild including plant-scoped rows where both subject columns are NULL |

**Two carry architectural weight rather than merely enforcing tidiness.**

`uq_production_run_active_per_line` is the database-level guarantee that two concurrent Simulator transactions cannot both schedule a run onto the same line. Without it the application would need advisory locking or serialisable isolation to achieve the same result.

`uq_oa_open_correlation_key` is the guarantee that two events arriving milliseconds apart cannot each create an alert for the same condition. Without it, correlation — the mechanism that makes the platform usable rather than noisy — would silently degrade under load. **This is the single most valuable index in the operational schema, and it is a correctness constraint, not a performance one.**

`uq_ds_scope_subject_time` requires `NULLS NOT DISTINCT`, available from PostgreSQL 15. By default PostgreSQL treats NULLs as distinct in unique constraints, so plant-scoped snapshots — carrying NULL in both subject columns — would not conflict and duplicates would be accepted. This is the specific reason the minimum version is 15 rather than 13 or 14.

---

# Part VIII — Naming Standards

## 43. Naming conventions

Naming is deterministic throughout, so that an implementer can derive every identifier from the logical model without judgement calls. That property is what makes ORM and migration generation mechanical.

### 43.1 Schemas and tables

| Object | Convention | Example |
|---|---|---|
| Schema | Lowercase, singular, functional | `master`, `operational`, `system`, `analytics` |
| Table | `snake_case`, **singular** | `production_line`, not `production_lines` |
| Qualification | **Always schema-qualified** in every reference | `master.machine` |

**Singular table names**, matching the frozen documents. A row is one machine, and `machine.machine_code` reads correctly where `machines.machine_code` does not.

### 43.2 Columns

| Element | Convention | Example |
|---|---|---|
| Primary key | `<table>_id` | `machine_id` |
| Business key | `<table>_code` | `machine_code` |
| Foreign key | `<referenced_table>_id` | `production_line_id` |
| Role-qualified FK | `<role>_<referenced_table>_id` | `attributed_machine_id`, `primary_supplier_id`, `confirmed_failure_category_id` |
| Boolean | `is_`, `has_`, `can_`, `requires_`, `did_` prefix | `is_bottleneck`, `has_safety_implication`, `can_authorize_line_stop`, `requires_line_stop`, `did_stop_line` |
| Instant | `*_at` | `recorded_at`, `acknowledged_at` |
| Calendar date | `*_date` | `commissioned_date`, `due_date` |
| Range bound | `*_from` / `*_to` | `interval_from`, `window_to` |
| Duration | **Unit always in the name** | `duration_seconds`, `mttr_minutes`, `lead_time_days`, `prediction_horizon_hours` |
| Physical quantity | Unit suffix | `rated_power_kw`, `floor_space_sqm`, `nominal_ambient_temp_c` |
| Percentage | `*_pct` or `*_percent` | `scrap_rate_pct`, `target_oee_percent` |
| Count | `*_count` | `event_count`, `pass_count` |
| Money | Bare noun; currency from `plant.currency_code` | `unit_cost`, `standard_selling_price` |

**Units in column names are non-negotiable in this schema.** A column called `duration` invites a wrong assumption; `estimated_duration_minutes` does not. This matters more than usual here because **an LLM reads these values and reasons about them in natural language** — a Decision Agent given a bare `duration` has no way to know whether 240 means minutes or seconds, and a recommendation stating the wrong one is worse than no recommendation.

The one deliberate inconsistency is `*_pct` alongside `target_oee_percent`. Both come from the frozen documents and are preserved exactly rather than normalised, because renaming a frozen attribute is outside this document's remit.

### 43.3 Constraints

| Constraint | Pattern | Example |
|---|---|---|
| Primary key | `pk_<table>` | `pk_machine` |
| Foreign key | `fk_<table>_<column_root>` | `fk_machine_production_line` |
| Unique | `uq_<table>_<columns>` | `uq_machine_code`, `uq_machine_line_position` |
| Partial unique index | `uq_<table>_<rule>` | `uq_machine_bottleneck_per_line` |
| Check | `ck_<table>_<rule>` | `ck_machine_monitored_requires_profile` |

**Table abbreviation in constraint names.** PostgreSQL identifiers are capped at 63 characters, and several table names are long enough that a fully-spelled constraint would exceed it — `ck_prediction_feature_snapshot_insufficiency_reason_required` is 59 and near the limit. Where the full form would exceed 63, a consistent abbreviation of the table name is used, declared once per table in its Constraints section: `plc` for `product_line_capability`, `mtp` for `machine_type_parameter`, `mtfm` for `machine_type_failure_mode`, `mms` for `machine_maintenance_schedule`, `atp` for `alert_threshold_profile`, `atr` for `alert_threshold_rule`, `msr` for `machine_sensor_reading`, `mos` for `machine_operational_status`, `mst` for `machine_state_transition`, `pr` for `production_run` and `prediction_result` in their respective sections, `pp` for `production_progress`, `pc` for `production_count`, `ch` for `cycle_history`, `qir` for `quality_inspection_result`, `sr` for `scrap_record`, `im` for `inventory_movement`, `mwr` for `maintenance_work_record`, `mma` for `machine_maintenance_activity`, `oe` for `operational_event`, `oa` for `operational_alert`, `pfs` for `prediction_feature_snapshot`, `sc` for `supervisor_context`, `ar` for `ai_recommendation`, `ra` for `recommendation_action`, `nt` for `notification`, `nd` for `notification_delivery`, `ds` for `dashboard_snapshot`, `al` for `audit_log`, `shs` for `system_health_status`.

**Constraint names must be explicit, never database-generated.** PostgreSQL will invent a name if none is given, and those names appear in error messages. `ck_machine_monitored_requires_profile` tells an operator what went wrong; `machine_check3` does not. On a schema with ~210 check constraints this is the difference between a diagnosable failure and a search.

### 43.4 Sequences

Identity columns own their sequences implicitly, and PostgreSQL names them `<table>_<column>_seq`. **No sequence is created or named manually**, which is one of the advantages of `GENERATED ALWAYS AS IDENTITY` over `serial` noted in §36.2.

### 43.5 ENUM types

| Element | Convention | Example |
|---|---|---|
| Type name | `snake_case`, singular, describes the domain | `machine_operational_state` |
| Value | `snake_case` lowercase | `down_unplanned` |
| Exception | Unit-of-measure values stay uppercase | `EA`, `KG`, `SET` |
| Schema | Lowest-dependency schema of all users | `master`, `operational`, `system` |

Type names describe the **domain**, not the column. `machine_operational_state` rather than `current_state_enum`, because the same type serves four columns with three different names. `_enum` suffixes are not used — the type's role is clear from its use, and the suffix adds width to 101 column definitions for no information.

The uppercase unit-of-measure values are the sole deviation, preserved from the frozen documents because `EA` and `KG` are established trade abbreviations that read wrongly in lowercase.

### 43.6 Reserved word avoidance

No identifier in the schema collides with a PostgreSQL or SQL reserved word, so **no identifier requires double-quoting.**

Three columns were at risk and are named to avoid it: `alert_threshold_profile.version` is permitted as a non-reserved keyword but is qualified in every reference; `business_rule.unit` and `machine_parameter.unit_of_measure` avoid the bare word `unit` where it would be ambiguous. Nothing is named `user`, `order`, `group`, `check`, `default`, `table`, `column`, `end`, `all`, or `references`.

**`production_run.run_status` rather than `status`** is a deliberate example: `status` is not reserved but it is generic, and a schema with several status-like columns benefits from each being self-describing. The same reasoning gives `alert_status`, `work_status`, and `delivery_status` their prefixes.

---

# Part IX — Performance Strategy

> **Scope note.** This part explains **strategy only**. No index is defined, and no index definition appears anywhere in this document. The eight partial unique indexes catalogued in §42.4 are **constraints** that PostgreSQL happens to implement as indexes; they exist for correctness and would be required even if the schema had no performance concerns at all. Index design is a later phase, informed by measured query plans rather than by anticipation.

## 44. Volume, growth, and large-table strategy

### 44.1 Volume classification

All 53 tables, classified by steady-state growth at the factory size in the frozen models — eight machines, seven monitored, four lines, three production shifts, six operating days per week.

| Class | Rows/day | Rows/year | Tables |
|---|---|---|---|
| **Very high** | 87,000 | ~32 M | `machine_sensor_reading` |
| **High** | 1,600–3,500 | 0.6–1.3 M | `cycle_history`, `production_count`, `audit_log` |
| **Moderate** | 170–380 | 62 K–140 K | `production_progress`, `dashboard_snapshot`, `machine_state_transition`, `prediction_feature_snapshot`, `prediction_result` |
| **Low** | 15–60 | 5 K–22 K | `operational_event`, `inventory_movement`, `supervisor_context`, `quality_inspection_result`, `scrap_record` |
| **Very low** | under 20 | under 8 K | `production_run`, `operational_alert`, `maintenance_work_record`, `machine_maintenance_activity`, `ai_recommendation`, `recommendation_action`, `notification`, `notification_delivery` |
| **Fixed** | 0 net | 8 rows each | `machine_operational_status`, `system_health_status` |
| **Bounded** | 0 net | ~245 total | All 29 `master` tables |

**Total steady state: ~95,000 rows/day, ~35 million rows/year.**

**The distribution is the important observation.** One table accounts for 91 % of all rows. Four tables account for 99 %. The remaining 48 tables together produce under 1 % of the volume — and they include every table the Decision Agent reads to build a recommendation.

This shape is what makes the platform's performance profile tractable: **optimisation effort concentrates on four tables, and the analytically richest tables are small enough that almost any access pattern performs acceptably.**

### 44.2 Read and write profile

| Table | Write pattern | Read pattern | Dominant concern |
|---|---|---|---|
| `machine_sensor_reading` | **Append-heavy**, continuous, batched per interval | Range scans by machine and time window | **Insert throughput and time-range scan cost** |
| `cycle_history` | Append-heavy, per cycle | Aggregation by machine and interval; window statistics for features | Insert throughput; aggregation cost |
| `production_count` | Periodic insert with idempotent upsert | Frequent aggregation for dashboards and OEE | Read cost |
| `audit_log` | Append-only from eight components | Rare, by `correlation_id` or time range | Insert throughput; correlation trace lookup |
| `machine_operational_status` | **Update-heavy on 8 rows** | **Read on every Monitoring Agent cycle** | Update contention on a tiny table |
| `operational_alert` | Low-volume insert plus update | Read on every dashboard refresh, filtered to open | Correlation-key lookup |
| All 29 `master` tables | Effectively never | **Constantly, by every component** | **None** — permanently cached |

**Two tables have genuinely unusual profiles and both are worth calling out.**

`machine_operational_status` holds eight rows and receives the highest update rate in the database — every state change, every reading, every completed cycle. PostgreSQL's MVCC creates a new row version per update, so this table accumulates dead tuples faster than any other despite never growing. **It requires an aggressive autovacuum setting rather than the cluster default**, and that is a table-level storage parameter rather than an index concern. Left at defaults, an eight-row table can bloat to thousands of pages and the platform's most frequent read becomes measurably slower.

`master` tables are read constantly and written almost never. At ~245 rows total the entire master schema fits comfortably in shared buffers and stays there. **This is why the 78 operational-to-master foreign key checks are effectively free** — each is an index probe into a cached table.

### 44.3 Partition candidates

Four tables are candidates for declarative range partitioning on their event-time column. **None is partitioned at v1.**

| Table | Partition key | Suggested granularity | Justification |
|---|---|---|---|
| `machine_sensor_reading` | `recorded_at` | Monthly | 32 M rows/year; 90-day retention makes partition drop dramatically cheaper than row deletion |
| `cycle_history` | `cycle_started_at` | Monthly | 1.3 M rows/year; same 90-day retention benefit |
| `audit_log` | `occurred_at` | Monthly | 730 K rows/year; archived not deleted, so detach rather than drop |
| `dashboard_snapshot` | `snapshot_at` | Monthly | 105 K rows/year; fully disposable, so drop is trivially safe |

**Why not at v1.** At 32 million rows per year, `machine_sensor_reading` is well within the range a single PostgreSQL table handles without difficulty. Partitioning adds planning overhead, a partition maintenance job, and constraints on primary keys and unique indexes. `PROJECT_OVERVIEW.md` §16.9 makes deliberate simplicity binding and forbids pre-building for anticipated need. **Partitioning is the correct answer to a problem this schema does not yet have.**

**The trigger for revisiting it**, stated so the decision is not left to intuition:

- Telemetry exceeds roughly 200 million rows, or
- The 90-day retention delete begins to take longer than its maintenance window, or
- Machine count or sampling frequency increases such that daily volume exceeds ~500,000 rows.

**The one migration cost to be aware of now.** PostgreSQL requires the partition key to be part of every unique constraint on a partitioned table. Partitioning `machine_sensor_reading` on `recorded_at` would therefore require the primary key to become (`machine_sensor_reading_id`, `recorded_at`), which is a breaking change to `operational_event.triggering_reading_id`.

**This is recorded rather than pre-empted.** The alternative — making the primary key composite now, on speculation — would propagate a two-column foreign key into the event table today to serve a migration that may never happen. §O1 states the decision explicitly so that whoever performs the migration finds the analysis already done rather than discovering the constraint mid-change.

**`production_count` is deliberately not a partition candidate** despite 580,000 rows per year, because its 2-year retention with archive rather than purge means partition drop offers no benefit, and it is queried across wide time ranges where partition pruning would help little.

### 44.4 Large-table strategy without partitioning

Five mechanisms keep the four high-volume tables manageable at v1, all of them schema-level rather than index-level.

**1. Aggregate-then-purge.** `production_count` summarises `cycle_history`, and an hourly downsample summarises `machine_sensor_reading`. Both aggregates are written and verified **before** the source is purged, so the fine-grained table never needs to be retained for reporting. This is the primary volume control and it is why `production_count` has a longer retention than the table it derives from.

**2. Short retention on the largest tables.** Telemetry and cycles are purged at 90 days. **The largest table in the database is therefore bounded at roughly 8 million rows in steady state**, not 32 million — the annual figure is throughput, not resident size.

**3. Append-only physical behaviour.** Sixteen of 24 operational tables never receive an `UPDATE`, including all four high-volume ones. Append-only tables produce no dead tuples from updates, keep near-perfect physical clustering on their insert order, and vacuum cheaply. This is a direct performance benefit of the immutability the explainability contract required for entirely different reasons.

**4. Narrow rows on the widest tables.** `machine_sensor_reading` carries nine business columns plus three trailing, with the two ENUM columns at 4 bytes each rather than variable-length text. A narrow row means more rows per page and fewer pages per range scan. §37.1 quantifies the ENUM saving at roughly 300 MB per year on one column.

**5. Monotonic insert keys.** Identity primary keys and `sequence_number` both increase monotonically, so index inserts land at the right-hand edge of the B-tree rather than scattering. This is the concrete reason §40.3 rejects UUID surrogates: a random UUID on a 32-million-row table scatters inserts across the whole index and inflates both write amplification and index size.

### 44.5 Retention as the primary performance mechanism

**Retention is not housekeeping in this schema — it is the main reason performance stays predictable.** Without it the database grows without bound and every large-table access degrades.

Three consequences of the retention design bear on performance directly:

**The `RESTRICT` policy makes purge ordering mandatory.** §41.5 specifies the reverse-layer sequence. A purge job that deletes by age alone will fail, and the failure is correct. Practically this means the retention job is a **sequence of ordered deletes**, not a set of independent per-table jobs that can run in parallel.

**`supervisor_context` requires a value-dependent purge predicate.** Escalated rows are retained 2 years, suppressed rows 180 days. The purge must filter on `escalation_decision`, and since suppressions are roughly 92 % of the table, the 180-day tier does most of the volume reduction.

**The §13.4 dependency chain means stated windows are floors.** `maintenance_work_record` at 5 years transitively pins `ai_recommendation`, `supervisor_context`, `prediction_result`, `prediction_feature_snapshot`, `operational_alert`, and `operational_event`. Those tables are all low-volume, so the pinning costs little space — but the retention job must be written to expect `RESTRICT` failures on cited rows and skip them rather than abort.

## 45. Query access patterns

Access patterns are documented here because they are what a later index-design phase must serve. **No index is proposed.**

| Pattern | Tables | Shape | Frequency |
|---|---|---|---|
| Latest state per machine | `machine_operational_status` | Point lookup by `machine_id` | Every Monitoring Agent cycle |
| Recent readings for a machine and parameter | `machine_sensor_reading` | Range scan on (`machine_id`, `machine_parameter_id`, `recorded_at`) | Continuous |
| Feature window aggregation | `machine_sensor_reading`, `cycle_history` | Range scan plus aggregate over a 4-hour window per machine | Per prediction cycle |
| Open alerts by machine | `operational_alert` | Filtered scan on status, grouped by machine | Every dashboard refresh |
| Open alert by correlation key | `operational_alert` | Point lookup on `correlation_key` filtered to open | Every detected event |
| Current stock per item | `inventory_movement` | Latest row per `inventory_item_id` by `movement_at` | Per availability check |
| Maintenance due computation | `machine_operational_status`, `machine_maintenance_schedule`, `maintenance_work_record` | Join of three small tables plus latest closed record per schedule | Per escalation |
| Evidence chain resolution | 6 operational tables | Five-hop join from a recommendation to a reading | Per audit or dashboard drill-down |
| Interval aggregation for OEE | `production_count`, `machine_state_transition` | Aggregate by machine and shift over a date range | Dashboard and analytics |
| Correlation trace | `audit_log` | Filtered scan on `correlation_id` | Per incident investigation |
| Prediction accuracy scoring | `prediction_result`, `maintenance_work_record` | Join on machine and time proximity, comparing failure categories | Analytics, batch |

**Two patterns deserve comment.**

**Current stock per item** is a "latest row per group" query, which is the one place the ledger design imposes a cost the balance-table alternative would not. It is accepted because the ledger's self-auditing property and point-in-time recoverability are worth more than the query simplicity, and because the item count is 11 — the group cardinality is tiny.

**Evidence chain resolution** is a five-hop join, and it is deliberately not optimised with a denormalised path. It runs on audit and drill-down rather than in any hot path, every hop is a primary-key lookup into a low-volume table, and the frozen model's five-hop maximum depth was chosen precisely so this query stays cheap.

---

# Part X — Transaction Strategy

## 46. Logical transaction boundaries

Each component's write cycle defines one or more atomic units. A transaction boundary is drawn where **partial application would leave the database in a state the model declares impossible.**

Default isolation is **`READ COMMITTED`**, PostgreSQL's default, for every component. Two boundaries warrant stronger isolation and both are noted. `SERIALIZABLE` is not used anywhere, because in each case a partial unique index provides the same guarantee at lower cost — the database rejects the conflicting write rather than aborting a transaction for retry.

### 46.1 Simulator transactions

The Simulator owns 12 tables and runs several distinct boundaries.

**T-SIM-1 — Telemetry batch.** Insert a batch of `machine_sensor_reading` rows for one interval, and update `machine_operational_status.last_reading_at`.

*Atomic because:* a reading batch that partially applied would leave `last_reading_at` disagreeing with the newest reading, and §O2's staleness detection would then report a telemetry outage that did not occur.

**T-SIM-2 — State change.** Insert one `machine_state_transition` and update `machine_operational_status` — `current_state`, `state_since`, `last_state_transition_id`, and the accumulated counters.

*Atomic because:* **this is the most important boundary in the Simulator.** The current-state row is a materialised summary of the transition history, and a partial application would leave the current state disagreeing with its own history — breaking the regenerability property in §30 that makes the mutable row safe. `ck_mos_running_requires_run` and the `last_state_transition_id` foreign key both depend on the two writes landing together.

**T-SIM-3 — Cycle completion.** Insert one `cycle_history` row and increment `machine_operational_status.accumulated_cycle_count`.

*Atomic because:* the counter is a maintained total over this exact table, and §41.6 reconciles the two daily. Partial application creates a divergence that the reconciliation would correctly report as a defect.

**T-SIM-4 — Interval close.** Insert or upsert `production_count` for the closing interval, and insert `production_progress` for each active run.

*Atomic because:* both derive from the same cycle and state data over the same window, and a progress snapshot without its corresponding counts would be inconsistent with the aggregate it should agree with. Idempotent via `uq_pc_machine_interval` and `uq_pp_run_snapshot`, so a retry is safe.

**T-SIM-5 — Run lifecycle change.** Update `production_run.run_status` and the relevant actual timestamp, and update `machine_operational_status.current_production_run_id` for affected machines.

*Atomic because:* `ck_mos_running_requires_run` and `ck_mos_run_only_when_engaged` span the two tables' consistency. **Concurrency:** `uq_production_run_active_per_line` is the guard preventing two transactions from activating a run on the same line, which is why `SERIALIZABLE` is unnecessary here.

**T-SIM-6 — Quality and scrap.** Insert one `quality_inspection_result`, any resulting `scrap_record` rows, and the `inventory_movement` recording material consumption.

*Atomic because:* §41.3 requires that an inspection with `disposition = 'scrap'` has a corresponding scrap record and that scrap consuming material has a corresponding movement. Partial application would break the cross-entity consistency the reconciliation checks for, and would leave stock overstating what is physically present.

**T-SIM-7 — Maintenance job progression.** Update `maintenance_work_record` status and timestamps, insert one `machine_maintenance_activity`, and on `part_collected` insert the `issue_maintenance` `inventory_movement`.

*Atomic because:* the activity timeline must not record a part collection without the corresponding stock issue.

**T-SIM-8 — Work order closure.** Update `maintenance_work_record` to `closed` with `confirmed_failure_category_id` and `resolution_note`, insert the closing `machine_maintenance_activity`, and update `machine_operational_status.operating_hours_at_last_maintenance` and `cycle_count_at_last_maintenance`.

*Atomic because:* **§M26 makes this the sole mechanism by which maintenance due status advances.** A partial application would leave a closed job whose schedule still reads as overdue, or a reset counter against a job that never closed — and either would make the Supervisor Agent's due computation wrong in the exact situation the platform exists to handle.

### 46.2 Monitoring Agent transaction

**T-MON-1 — Detection.** Find or create the correlating `operational_alert`, insert the `operational_event` with that alert identifier, update the alert's `event_count`, `last_event_at`, and possibly `current_severity_level_id`, and increment `machine_operational_status.open_alert_count` if the alert is new.

*Atomic because:* `operational_event.operational_alert_id` is `NOT NULL` and set at insert, which is what makes the event genuinely immutable. The find-or-create and the insert must be one unit or an event could be written against an alert that a concurrent rollback removed.

**Concurrency — the critical case.** Two events for the same condition arriving milliseconds apart must not each create an alert. `uq_oa_open_correlation_key` guarantees this: the second transaction's insert fails on the unique violation, and the agent retries the find branch. **This is the alert-storm prevention mechanism operating at the transaction level**, and it is why `SERIALIZABLE` is not needed for the most concurrency-sensitive write in the platform.

**T-MON-2 — Acknowledgement.** Update `operational_alert` with `acknowledged_at` and `acknowledged_by_worker_id`.

Submitted by the Dashboard, **written by the Monitoring Agent** per §25.3, so the table keeps a single writer.

**T-MON-3 — Resolution and closure.** Update `operational_alert` status, `resolution_type`, `resolved_at`, `closed_at`, `resolution_note`, and decrement `machine_operational_status.open_alert_count`.

### 46.3 Prediction Agent transaction

**T-PRED-1 — Featurise and score.** Insert one `prediction_feature_snapshot` and, if sufficient, one `prediction_result`.

*Atomic because:* `prediction_result.prediction_feature_snapshot_id` is `NOT NULL`, so the snapshot must exist before the result. More importantly, **the reproducibility contract requires the pair to be inseparable** — a result whose snapshot was rolled back could never be reproduced or audited.

*Insufficient snapshots commit alone.* This is deliberate: the row records why no prediction was produced, which §O15 identifies as what makes the platform's silence explicable.

### 46.4 Supervisor Agent transaction

**T-SUP-1 — Escalation decision.** Insert one `supervisor_context`, escalated or suppressed.

Single-table, single-row, and the simplest boundary in the platform. That simplicity is a direct consequence of the Supervisor Agent owning exactly one table — the narrowness of its responsibility shows up as the narrowness of its transaction.

### 46.5 Decision Agent transaction

**T-DEC-1 — Recommendation generation.** Insert one `ai_recommendation`.

Single-table, single-row. **The LLM call happens outside the transaction**, and this matters: an LLM call taking four seconds inside an open transaction would hold a snapshot for four seconds on every reasoning cycle. The agent reads `supervisor_context.context_document`, closes that read, calls the model, then opens a transaction solely to insert the result.

`uq_ar_supervisor_context` guarantees that a retry after an ambiguous failure cannot produce two recommendations for one context.

### 46.6 Notification Service transactions

**T-NOT-1 — Compose.** Insert one `notification` per qualifying recipient, including suppressed ones, for a single triggering recommendation or alert.

*Atomic because:* the suppression audit is only meaningful if the full recipient evaluation is recorded together. A partial commit could show two recipients notified and omit the record that a third was deliberately suppressed, which §O20 identifies as the ambiguity the suppression row exists to remove.

**T-NOT-2 — Delivery attempt.** Insert one `notification_delivery` per channel attempt.

Separate from composition because delivery is an external call with unpredictable latency. Holding a transaction open across a provider round-trip would be the same defect as T-DEC-1 avoids.

**T-NOT-3 — Delivery confirmation.** Update `notification_delivery.delivery_status` from `sent` to `delivered`.

**The only `UPDATE` in the Notification Service**, arriving asynchronously from the provider, and the reason `notification_delivery` carries `updated_at` while `notification` does not.

### 46.7 Dashboard transactions

**T-DASH-1 — Record human action.** Insert one `recommendation_action`.

Single-row, immutable. A change of mind is a new row, so no update boundary exists.

**T-DASH-2 — Snapshot generation.** Insert or upsert `dashboard_snapshot` rows for each scope at the interval boundary.

Idempotent via `uq_ds_scope_subject_time`. Fully rebuildable, so a failed run needs no compensation — the next run supersedes it.

**All Dashboard reads are outside any transaction** and use `READ COMMITTED`. The dashboard tolerates reading data a few hundred milliseconds stale, and holding read transactions open on the platform's broadest reader would block vacuum on the tables that need it most.

### 46.8 Retention transaction

**T-RET-1 — Purge cycle.** Delete expired rows table by table in the reverse-layer order of §41.5, then insert the `system.audit_log` entry recording table, cut-off, and row count.

*One transaction per table, not per cycle.* A single transaction spanning all 24 tables would hold locks for the whole run and generate an enormous amount of WAL. Per-table transactions mean a failure part-way leaves earlier tables purged and later ones not — which is safe, because the ordering is from leaf to root and a partial purge never orphans anything.

**The audit entry commits in the same transaction as its purge**, so the record of what was deleted cannot survive without the deletion, or vice versa.

### 46.9 Isolation summary

| Component | Isolation | Concurrency guard |
|---|---|---|
| Simulator | `READ COMMITTED` | `uq_production_run_active_per_line`; single-writer ownership on all 12 tables |
| Monitoring Agent | `READ COMMITTED` | **`uq_oa_open_correlation_key`** — the critical guard |
| Prediction Agent | `READ COMMITTED` | Single writer; no contended row |
| Supervisor Agent | `READ COMMITTED` | Single writer; insert only |
| Decision Agent | `READ COMMITTED` | `uq_ar_supervisor_context` |
| Notification Service | `READ COMMITTED` | `uq_nd_notification_channel_attempt` |
| Dashboard | `READ COMMITTED` | `uq_ds_scope_subject_time` |
| Retention | `READ COMMITTED` | `RESTRICT` plus ordered execution |

**`READ COMMITTED` is sufficient everywhere, and the reason is structural rather than fortunate.** Single-component ownership per table (§25) means no two writers ever contend for the same row. The only genuine concurrency risks are two *instances* of the same component racing, and in each such case a partial unique index converts the race into a clean unique violation the application retries — cheaper and more predictable than serialisation failure and retry.

**No advisory locks are required**, and no `SELECT FOR UPDATE` appears in any boundary above. That is a direct dividend of the ownership model and of the eight partial unique indexes.

---

# Part XI — Data Integrity Strategy

## 47. ACID properties as applied

### 47.1 Atomicity

Guaranteed by the transaction boundaries in §46. Eight of the fifteen boundaries span more than one table, and in every case the boundary is drawn where **partial application would produce a state the model declares impossible.**

The three most consequential:

| Boundary | What partial application would break |
|---|---|
| **T-SIM-2** state change | The current-state row would disagree with its own history, destroying the regenerability that makes the mutable row safe |
| **T-SIM-8** work order closure | A closed job whose schedule still reads overdue, or reset counters against a job that never closed — making maintenance due status wrong in exactly the situation the platform exists to handle |
| **T-MON-1** detection | An event referencing an alert that a concurrent rollback removed, violating the `NOT NULL` alert reference that makes the event immutable |

### 47.2 Consistency

Enforced at the four layers in §41. The distribution across layers is itself informative:

| Layer | Rules | Coverage |
|---|---|---|
| Type | 65 ENUM types, 12 precision families, 4 fixed-width `CHAR` | Structurally impossible values |
| Declarative | 53 PK, 166 FK, 41 unique, 8 partial unique, ~210 check, ~430 NOT NULL | Semantically wrong single rows |
| Application | ~55 cross-table and cross-row rules, registered in §41.3 | What no per-row predicate can see |
| Reconciliation | 4 maintained totals, 2 denormalisations, 6 arithmetic invariants, registered in §41.6 | Drift that accumulates silently |

**Roughly 900 declarative constraints against ~55 application-validated rules.** That ratio is deliberate: every rule that *can* be declarative is, because a declarative constraint holds regardless of which code path writes the row, and this database has eight distinct writing components.

**The 55 that cannot be declarative are all registered**, with the reason and the owning component. That register is the deliverable that prevents a rule from being lost between design and implementation — which is the normal failure mode for cross-table rules.

### 47.3 Isolation

`READ COMMITTED` throughout, with the reasoning in §46.9. The structural basis is that **single-component ownership eliminates cross-writer contention entirely** — no two components write the same table, so no two components contend for the same row.

The residual risk is two *instances* of one component racing, and each such case is guarded by a partial unique index that converts the race into a unique violation the application retries. This is preferable to `SERIALIZABLE` on both correctness and cost: a unique violation names exactly which invariant was hit, whereas a serialisation failure reports only that something conflicted.

**Long-running reads are kept out of transactions.** The Dashboard reads outside any transaction, and both the Decision Agent's LLM call and the Notification Service's provider call happen between transactions rather than inside one. On a database where `machine_operational_status` receives the highest update rate, a held snapshot would block the vacuum that table depends on.

### 47.4 Durability

Standard PostgreSQL WAL durability with synchronous commit. No table uses `UNLOGGED`.

`dashboard_snapshot` is the one plausible candidate — it is fully rebuildable and purely presentational, and `UNLOGGED` would reduce its WAL volume. It is **rejected** because `UNLOGGED` tables are truncated on crash recovery, and the historical-replay capability in §O22 would silently lose its history after any unclean shutdown. The WAL saving on 105,000 rows per year is not worth a capability that disappears without warning.

## 48. Orphan prevention

### 48.1 How orphans are made impossible

| Mechanism | Effect |
|---|---|
| **166 foreign keys, all enforced** | No child row can reference a non-existent parent |
| **`ON DELETE RESTRICT` on 165 of them** | No parent can be deleted while children exist |
| **No `CASCADE` anywhere** | No deletion propagates silently; every removal is explicit and ordered |
| **No hard deletes in `master`** | Soft retirement means operational history never loses its referent |
| **Ordered purge in `operational`** | Leaf-to-root deletion, with `RESTRICT` catching any ordering defect |
| **`NOT NULL` on structurally mandatory references** | 12 operational foreign keys are mandatory, so the relationship cannot be absent |

**The `RESTRICT` policy is the load-bearing element**, and its value is that it converts a silent data-loss defect into a loud operational error. A purge job attempting to remove a row that a retained recommendation still cites **fails**, and that failure reveals the problem before evidence is lost rather than after.

### 48.2 The mandatory-reference set

Twelve operational foreign keys are `NOT NULL`, and each encodes a structural claim about the model:

| Column | Claim |
|---|---|
| `operational_event.operational_alert_id` | Every event belongs to a managed case. **There are no orphan events** |
| `prediction_result.prediction_feature_snapshot_id` | Every prediction has a reproducible input |
| `ai_recommendation.supervisor_context_id` | Every recommendation traces to a recorded escalation decision |
| `ai_recommendation.prediction_result_id` | **Every recommendation has ML confidence by reference** |
| `supervisor_context.triggering_alert_id` | Every escalation decision concerns a real case |
| `production_run.customer_id` | Every unit produced has an identifiable customer |
| `production_run.product_line_capability_id` | Every run has a governing rate |
| `cycle_history.production_run_id` | Every cycle belongs to a run |
| `production_progress.production_run_id` | Every snapshot measures a run |
| `notification_delivery.notification_id` | Every attempt delivers a real message |
| `machine_maintenance_activity.maintenance_work_record_id` | Every activity belongs to a job |
| `recommendation_action.ai_recommendation_id` | Every human decision responds to real advice |

Making these mandatory rather than nullable is what allows the evidence chain in §26.3 to be traversed without null-handling at every hop.

### 48.3 The one deliberate soft reference

`system.audit_log.entity_id` is **not** a foreign key, and this is the schema's single intentional exception to referential enforcement.

**The reason is decisive:** the audit trail must survive the retention purge of the row it describes. A foreign key would either block the purge — making the audit log a retention hostage — or cascade the audit row away with its subject. **An audit log that disappears alongside what it audited is not an audit log.**

`entity_code` exists alongside `entity_id` precisely so the record stays human-readable after the row is gone. `ck_al_entity_reference_paired` requires `entity_name` whenever `entity_id` is present, so the soft reference is at least self-describing.

### 48.4 The `SET NULL` case revisited

`operational_event.triggering_reading_id` is the only non-`RESTRICT` action, and it does not create an orphan — it severs a lineage pointer while leaving the referencing row and its evidence fully intact.

This works **only because** the frozen model captured `observed_value`, `threshold_value_breached`, `threshold_direction`, and `detected_at` onto the event itself. §32.3 works through why `RESTRICT` would deadlock retention against explainability here and `SET NULL` does not.

### 48.5 Completeness as a distinct concern

Referential integrity prevents orphans. It does **not** prevent the opposite failure: a parent that should have children and does not.

The schema cannot express "every active line has at least one machine" — no declarative constraint can require the existence of a child. **These are the completeness rules in §41.3, and they gate simulator start per §7.**

That gate matters because the failures in question are silent by nature. A monitored machine with no threshold profile, a profile with no rules, a line with no capability row, a critical severity level with no recipient configured to receive it — each presents as configured while doing nothing. **A database that violates any of them is structurally valid and operationally broken**, and only an explicit validation pass catches it.

---

# Part XII — Scalability Strategy

## 49. Growth without schema change

The design commitment: **every realistic growth axis is row insertion, not schema modification.** This section establishes that table by table.

### 49.1 More machines

Adding a machine is one `master.machine` row plus one `operational.machine_operational_status` row.

| What it requires | What it does not |
|---|---|
| One master row, one status row | No new table, column, or type |
| An existing `machine_type_id` | No change to telemetry, events, predictions, or any downstream table |
| An existing `alert_threshold_profile_id` if monitored | No change to the 13 tables referencing `machine` |
| `line_position` within the line's `station_count` | — |

**A machine of a genuinely new kind** requires one `machine_type` row, its `machine_type_parameter` declarations, its `machine_type_failure_mode` rows, and one `alert_threshold_profile` with rules. All row insertion. **This is the single most valuable scalability property in the model**, and it is a direct consequence of the master data design pushing every machine characteristic into referenced rows rather than into the machine row itself.

**Volume consequence.** Telemetry scales linearly with monitored machines. Seven monitored machines produce ~87,000 rows/day; seventy would produce ~870,000. §44.3's partition trigger is set at ~500,000 rows/day, so a tenfold machine increase is the point at which partitioning becomes warranted — and §O1 records the migration cost so it is not a surprise.

### 49.2 More production lines

One `master.production_line` row, plus machines, plus at least one `product_line_capability` row per product it will run.

The six operational tables referencing `production_line` require no change. **`business_rule` scoping is the one place a new line has policy implications:** a critical line should receive a line-scoped downtime cost rule and may receive a line-scoped escalation threshold. Both are row insertions, and §41.3 makes the first a completeness check.

### 49.3 More sensors and parameters

A new parameter on an existing machine type is one `machine_type_parameter` row plus one `alert_threshold_rule` row per profile that should monitor it.

**Nothing in the telemetry path changes.** `machine_sensor_reading` references `machine_parameter_id` generically, and the Monitoring Agent iterates threshold rules without knowing parameter names. **This is the payoff of the frozen master model's decision to make threshold rules rows rather than wide columns** — adding vibration monitoring to a machine type that never had it is a data change, and §M28 sets out why.

A genuinely new *kind* of measurement is one `master.machine_parameter` row. The `unit_of_measure` column being `VARCHAR(16)` rather than an ENUM (§37.4) means a new unit needs no type migration either.

### 49.4 More products and customers

One `master.product` row, its `product_line_capability` rows, and its `bill_of_materials` rows. One `master.customer` row.

No operational table changes. `production_run` references both generically.

### 49.5 Longer history

Retention is the control, and it is configuration rather than schema.

| Change | Consequence |
|---|---|
| Extend telemetry retention beyond 90 days | Linear storage growth; brings the §44.3 partition trigger closer |
| Extend `maintenance_work_record` beyond 5 years | Transitively extends six tables via the §13.4 chain — the retention floors move together |
| Reduce any retention | Immediate space recovery. **Must not fall below the §13.4 floors**, or purge fails against `RESTRICT` |

**No schema change is required for any retention adjustment.** The one structural constraint is the dependency chain, and `RESTRICT` enforces it automatically rather than requiring the operator to remember it.

### 49.6 Future factory expansion — multi-plant

The frozen master model holds one `plant` row and states that additional plants are additional rows. **This schema honours that literally**, and the physical position is worth being precise about.

**What already works.** `plant_area`, `department`, and `shift` all carry `plant_id`. A second plant is one `plant` row plus its areas, departments, shifts, lines, and machines — all row insertion. Every operational table reaches its plant transitively through the machine or the line, so **no operational table needs a `plant_id` column.**

**What would need attention.** Three things, none requiring a table redesign:

| Concern | Resolution |
|---|---|
| Business codes are globally unique | `LN-01` at two plants would collide. Either prefix codes per plant, or relax the unique constraint to (`plant_id`, code) — a constraint change, not a table change |
| `master.business_rule` scopes to line or global | "Global" would become ambiguous across plants. A nullable `plant_id` alongside the existing nullable `production_line_id` extends the two-step resolution to three |
| Surrogate keys stay plant-agnostic | **No UUID conversion is needed.** §40.3 states the position: a plant discriminator is the migration path, not global identifiers |

**What must not change.** The `plant.timezone` and `plant.currency_code` columns already make per-site interpretation correct. A multi-plant deployment inherits that without modification, which is the specific reason §M1 argued for modelling a single plant as a table rather than assuming it in configuration.

### 49.7 What would genuinely require schema change

Stated honestly, because a scalability claim is only credible if its limits are named.

| Change | Why it is structural |
|---|---|
| Machine sub-component hierarchy | Prediction at component rather than machine level needs a new table and a new prediction subject |
| Multi-level bill of materials | Recursive explosion needs a self-reference and recursive traversal, both deliberately absent |
| Supplier multi-sourcing | A junction table with per-supplier lead time and price |
| Worker shift rosters | A junction table; the current model holds a default shift only |
| Batch-level BOM traceability | A versioned header table |
| Quality specification limits | Tolerance columns or a limits table |
| Role-based access control | Tables for roles, permissions, and grants |

Each is a **deferred extension** in the frozen models with no current consumer, and each is listed there as a roadmap candidate. `PROJECT_OVERVIEW.md` §18 forbids pre-building for them, and this schema contains no speculative column, table, or abstraction serving any of them.

---

# Part XIII — Security Considerations

> **Scope note.** This part specifies **access boundaries and privilege structure only.** No authentication mechanism, credential store, connection configuration, encryption implementation, or deployment detail appears here.

## 50. Access boundaries and privilege structure

### 50.1 Roles

Nine database roles, mapping one-to-one onto the ownership model in §25. **Role separation is what makes the ownership model a database guarantee rather than a coding convention.**

| Role | Purpose |
|---|---|
| `ff_admin` | Master data seed and administrative edit. The only role with write access to `master` |
| `ff_simulator` | Owns 12 operational tables |
| `ff_monitoring_agent` | Owns `operational_event`, `operational_alert` |
| `ff_prediction_agent` | Owns `prediction_feature_snapshot`, `prediction_result` |
| `ff_supervisor_agent` | Owns `supervisor_context` |
| `ff_decision_agent` | Owns `ai_recommendation` |
| `ff_notification_service` | Owns `notification`, `notification_delivery` |
| `ff_dashboard` | Owns `recommendation_action`, `dashboard_snapshot` |
| `ff_retention` | The only role holding `DELETE` on any table |

### 50.2 Privilege matrix

| Role | `master` | Own operational tables | Other operational tables | `system.audit_log` | `system.system_health_status` |
|---|---|---|---|---|---|
| `ff_admin` | SELECT, INSERT, UPDATE | — | — | INSERT | — |
| `ff_simulator` | **SELECT only** | INSERT, UPDATE | **See §50.4** | INSERT | UPDATE own row |
| `ff_monitoring_agent` | SELECT only | INSERT, UPDATE | SELECT | INSERT | UPDATE own row |
| `ff_prediction_agent` | SELECT only | INSERT | SELECT | INSERT | UPDATE own row |
| `ff_supervisor_agent` | SELECT only | INSERT | SELECT | INSERT | UPDATE own row |
| `ff_decision_agent` | SELECT only | INSERT | SELECT — **`supervisor_context` only** | INSERT | UPDATE own row |
| `ff_notification_service` | SELECT only | INSERT, UPDATE | SELECT | INSERT | UPDATE own row |
| `ff_dashboard` | SELECT only | INSERT, UPDATE | SELECT | INSERT | SELECT |
| `ff_retention` | SELECT | — | **DELETE** | INSERT | — |

Three properties of this matrix are architectural rather than administrative:

**No application role holds `UPDATE` on the 16 append-only tables.** Immutability becomes a database guarantee rather than a discipline. The explainability contract requires that evidence cannot be rewritten, and withheld privilege is the only enforcement that survives a defect in the owning component.

**No application role holds `DELETE` on any table.** Only `ff_retention` does, and it runs the ordered purge in §41.5 and audits every run.

**No role holds `UPDATE` or `DELETE` on `system.audit_log`.** Every role has `INSERT` only, which is what makes the audit trail append-only at the privilege level. §25.2 explains why shared insert is a safe exception to single ownership.

### 50.3 Read-only versus mutable surfaces

| Surface | Roles with write access | Character |
|---|---|---|
| All 29 `master` tables | `ff_admin` only | **Read-only to every agent.** A blanket `GRANT SELECT ON ALL TABLES IN SCHEMA master` is a one-line, self-documenting policy — one of the concrete reasons §9.1 separates the schema |
| 16 append-only operational tables | Owning role, INSERT only | Immutable by privilege |
| 8 mutable operational tables | Owning role, INSERT + UPDATE | The only tables where any role can change existing data |
| `system.audit_log` | All roles, INSERT only | Append-only by privilege |
| `system.system_health_status` | Each role updates its own row | Self-reporting |

### 50.4 The Simulator read boundary

**The Simulator's read restriction is enforced by withheld privilege, not by code review.** `ff_simulator` holds no `SELECT` on:

`operational_event` · `operational_alert` · `prediction_feature_snapshot` · `prediction_result` · `supervisor_context` · `ai_recommendation` · `recommendation_action` · `notification` · `notification_delivery` · `dashboard_snapshot`

If the Simulator could observe that a failure had been predicted, its subsequent behaviour could be influenced by that prediction and **every accuracy measurement in the platform would become circular.** §26 sets out the reasoning; this is where it is enforced.

The apparent exception is not one. The Simulator writes `maintenance_work_record.triggering_recommendation_id`, receiving the identifier as a parameter from the human decision path. It never queries `ai_recommendation`, and it has no privilege to.

### 50.5 The Decision Agent read boundary

`ff_decision_agent` holds `SELECT` on `supervisor_context` and on `master`, and on **nothing else operational.**

This is deliberate and it enforces an architectural property: the Decision Agent's entire input is `supervisor_context.context_document`. It issues no queries of its own, which is what makes the recommendation reproducible in its inputs even though LLM output is not reproducible in its wording. Granting broader read access would let the agent quietly acquire dependencies the preserved context was designed to eliminate.

### 50.6 Personal data boundary

`master.worker` is the database's primary privacy boundary. It holds `first_name`, `last_name`, `email`, and `phone_number`.

**What the model deliberately does not hold:** home address, date of birth, national identity number, salary, or any performance rating. None serves a FactoryFlow AI use case, and holding data with no consumer is a liability rather than an asset.

**Column-level access separation applies.** `email` and `phone_number` are delivery endpoints needed only by `ff_notification_service`. Roles that need to *name* a worker — `ff_supervisor_agent`, `ff_decision_agent`, `ff_dashboard` — need `first_name`, `last_name`, `worker_code`, and role, not contact details. PostgreSQL supports column-level grants, and the specification is:

| Role | `worker` columns |
|---|---|
| `ff_notification_service` | All, including `email` and `phone_number` |
| `ff_supervisor_agent`, `ff_decision_agent`, `ff_dashboard`, `ff_monitoring_agent` | All except `email` and `phone_number` |
| `ff_simulator` | All except `email` and `phone_number` |

**A related constraint on LLM prompt construction:** no personal data beyond the name and role required to address a recommendation may be placed in `supervisor_context.context_document` or `ai_recommendation.supporting_evidence`. Those columns are read by an external model. This is an application rule with a schema consequence — the columns are `JSONB` and the database cannot inspect their semantics — and it is recorded here because it is the kind of boundary that erodes when convenient.

### 50.7 Audit requirements

| Requirement | Mechanism |
|---|---|
| Every significant action is attributable | `system.audit_log.component` and `actor_worker_id`; `created_by_component` on all 24 operational tables |
| Human actions are distinguishable from machine actions | `actor_worker_id` is NULL for system actions. **The audit's core question** |
| Master data changes are traceable with before and after values | `action_type = 'configuration_changed'` with values in `action_detail` |
| One incident is traceable end to end | `correlation_id`, generated at first detection |
| Retention purges are explicable | `action_type = 'retention_purge'` with table, cut-off, and row count |
| The audit trail cannot be altered | INSERT-only privilege for every role |
| The audit trail survives its subject | `entity_id` is deliberately not a foreign key |

**Threshold changes are the audit requirement with the sharpest operational edge.** `master.alert_threshold_profile` and `alert_threshold_rule` govern what the platform detects, and the master model's tuning cycle depends on knowing who changed what and when. **A threshold changed without an audit entry is an unexplainable change in platform behaviour**, and the accountability chain for a missed detection would have a gap exactly where it matters.

### 50.8 Future RBAC compatibility

The schema is RBAC-ready without containing any RBAC implementation. Three properties make it so:

**Authority is already modelled as data.** `worker_role.can_authorize_line_stop`, `can_authorize_maintenance`, and `is_managerial` express operational authority. A future application-level RBAC layer maps onto these rather than replacing them.

**Actor attribution already exists.** Nine columns across seven tables record which worker performed an action — `acknowledged_by_worker_id`, `actioned_by_worker_id`, `inspector_worker_id`, `recorded_by_worker_id`, `performed_by_worker_id`, `actor_worker_id`. An RBAC layer needs no new attribution.

**Scope is already modelled.** `notification_recipient.scope_production_line_id` and `worker.production_line_id` express line-level scoping, which is the natural axis for row-level access if it is ever needed.

**What is deliberately absent:** no `role`, `permission`, `user`, `session`, or `grant` table exists. `PROJECT_OVERVIEW.md` §18 places access control in Phase 6 and forbids pre-building, and §49.7 lists RBAC among the changes that would genuinely require new tables. When it is built, PostgreSQL row-level security is the natural mechanism and the scope columns above are what it would filter on.

---

# Part XIV — Schema Summary

## 51. Schema at a glance

### 51.1 Object counts

| Object | Count |
|---|---|
| **Schemas** | **4** — `master`, `operational`, `system`, `analytics` |
| **Tables** | **53** |
| — Master tables | 29 (`master`) |
| — Operational tables | 24 — 22 in `operational`, 2 in `system` |
| — Analytics tables | 0 — schema reserved and empty |
| **Foreign keys** | **166** |
| — Master → Master | 51 |
| — Operational → Master | 78 |
| — Operational → Operational | 37 |
| — Master → anything outside `master` | **0** |
| **Soft references** (not foreign keys) | 1 — `audit_log.entity_id` |
| **Primary keys** | 53, all `GENERATED ALWAYS AS IDENTITY` |
| **Composite primary keys** | **0** |
| **Unique constraints** | 41 |
| **Partial unique indexes** | 8 — all correctness constraints, catalogued in §42.4 |
| **Check constraints** | ~210 |
| **NOT NULL columns** | ~430 |
| **ENUM types** | 65 — 30 `master`, 31 `operational`, 4 `system` |
| **ENUM columns** | 101 |
| **`JSONB` columns** | 10 |
| **Business code columns** | 32 |
| **Referential actions** | 165 `RESTRICT`, 1 `SET NULL`, **0 `CASCADE`** |
| **Triggers, functions, views, materialised views, stored procedures** | **0** |

### 51.2 Relationship cardinality breakdown

| Cardinality | Count | Notes |
|---|---|---|
| Many-to-one | 157 | The default shape of every non-unique foreign key |
| One-to-one | 5 | Enforced by unique constraint, never by composite PK |
| Many-to-many | 5 | All in `master`, all resolved by an attribute-carrying junction |
| Self-referencing | 0 | **No recursive reference anywhere** — no query needs a recursive CTE |
| Multi-role to one parent | 7 | All role-qualified in column names |
| Circular | **0** | Four designed out; §24 proves acyclicity by layer assignment |

### 51.3 Growth profile

| Horizon | Master | Operational | Total |
|---|---|---|---|
| Initial seed | ~245 rows | 8 status + 8 health rows | ~261 |
| Per day | 0 | ~95,000 | ~95,000 |
| Per year | 0 | ~35 million | ~35 million |
| **Steady state resident** | **~245 rows** | **~10 million rows** | **~10 million** |

**Resident size is roughly 10 million rows, not 35 million.** The difference is retention: the two largest tables are purged at 90 days, so the annual figure is throughput rather than resident volume. This is the single most important number in the schema's performance profile, and §44.4 sets out the five mechanisms producing it.

### 51.4 Largest tables

| Rank | Table | Rows/year | Share | Retention |
|---|---|---|---|---|
| 1 | `machine_sensor_reading` | ~32 M | **91 %** | 90 days, downsample then purge |
| 2 | `cycle_history` | ~1.3 M | 3.7 % | 90 days, aggregate then purge |
| 3 | `production_count` | ~580 K | 1.7 % | 2 years, archive |
| 4 | `audit_log` | ~730 K | 2.1 % | 1 year, archive **never delete** |
| 5 | `production_progress` | ~140 K | 0.4 % | 180 days, purge |
| 6 | `dashboard_snapshot` | ~105 K | 0.3 % | 90 days, downsample then purge |

**Four tables account for 99 % of all rows.** The remaining 49 together produce under 1 %.

### 51.5 Smallest tables

| Table | Rows | Character |
|---|---|---|
| `master.plant` | **1** | Single site |
| `master.product`, `master.customer` | 3 each | Grows with the business |
| `master.shift`, `master.production_line`, `master.maintenance_team`, `master.supplier` | 4 each | Structural |
| `operational.machine_operational_status` | **8, fixed** | One per machine. **Zero net growth** |
| `system.system_health_status` | **8, fixed** | One per component. **Zero net growth** |

The two fixed-size operational tables are worth noting together: `machine_operational_status` receives the **highest update rate in the database** on eight rows, which §44.2 identifies as requiring an aggressive autovacuum setting rather than the cluster default.

### 51.6 Critical tables

Criticality here means *what breaks if this table is wrong or unavailable*, not size.

| Table | Why critical |
|---|---|
| `master.machine` | Referenced by 13 operational tables. **A defect here corrupts every downstream stage** |
| `master.shift` | Referenced by 17 operational tables — the most referenced in the database |
| `master.failure_severity_level` | The shared currency of urgency. Referenced by 6 operational and 3 master tables. Effectively immutable |
| `master.alert_threshold_rule` | **Governs what the platform detects.** A wrong threshold means healthy machines alert or failing ones do not |
| `master.business_rule` | Governs escalation, cost, and prioritisation. **Wrong values make the platform behave wrongly and explain itself correctly** |
| `master.notification_recipient` | A gap here means a critical recommendation reaches nobody |
| `operational.machine_operational_status` | Read on every Monitoring Agent cycle; supplies the counters maintenance due status depends on |
| `operational.operational_alert` | Correlation lives here. **Without `uq_oa_open_correlation_key` the platform becomes noise** |
| `operational.prediction_result` | **The sole origin of ML confidence** |
| `operational.ai_recommendation` | The platform's product and accountability record |
| `operational.maintenance_work_record` | The accuracy scorecard and the terminus of the retention chain |

**Two are critical for a reason that is easy to overlook.** `master.business_rule` and `master.alert_threshold_rule` are small configuration tables whose values determine platform behaviour. A wrong threshold or escalation cut-off produces a platform that operates exactly as designed and reaches wrong conclusions — the hardest class of defect to detect, which is why §50.7 makes changes to both audited with before and after values.

### 51.7 Core dependency diagram

```
════════════════════ master (29 tables, ~245 rows, read-only to agents) ════════════════════

  L0   plant   product   machine_category   machine_parameter   worker_role   supplier
       customer   failure_severity_level
         │         │            │                  │              │
  L1   plant_area  department  shift        machine_type    failure_category
         │  │        │  │        │  │  │            │  │  │           │
  L2   production_line   inventory_location   maintenance_team
       alert_threshold_profile      machine_type_parameter
         │   │    │                      │
  L3   machine   worker   inventory_item   product_line_capability
       alert_threshold_rule   business_rule
         │        │  │              │
  L4   maintenance_engineer   notification_recipient   bill_of_materials
       machine_type_failure_mode   machine_maintenance_schedule

                              ▲  78 foreign keys inbound
                              │  0 outbound — master references nothing outside itself
                              │
═══════════════ operational (22 tables) + system (2 tables) ═══════════════

  L0   production_run ◄──────────┐        operational_alert ◄────────┐
       dashboard_snapshot        │        audit_log  system_health_status
         │                       │          │
  L1   machine_sensor_reading    │        prediction_feature_snapshot
       production_progress       │          │
       production_count          │          │
       cycle_history             │          │
         │                       │          │
  L2   operational_event ────────┘        prediction_result
         │                                  │
  L3   quality_inspection_result          supervisor_context
         │                                  │
  L4   scrap_record                       ai_recommendation
         │                                  │
  L5   ├────────────────► notification ◄────┤
       └──────────────► maintenance_work_record
                          │
  L6   machine_maintenance_activity   inventory_movement
       machine_state_transition       recommendation_action
       notification_delivery
                          │
  L7   machine_operational_status
```

**The pipeline is legible in the layering.** Detection at L0–L2, prediction at L1–L2, reasoning at L3–L4, delivery at L5, and the materialised current state at L7. Maximum depth is 4 in `master` and 7 in `operational`, and the combined creation order is master L0→L4 then operational L0→L7 with **no deferred constraints and no temporary NULLs at any point.**

### 51.8 The evidence chain

The most important join path in the database — how a recommendation resolves to its raw evidence.

```
ai_recommendation
  ├─► prediction_result ──────────► prediction_feature_snapshot
  │      (ML confidence,              (the exact model input)
  │       by reference only)
  └─► supervisor_context
         ├─► master.business_rule     (why it escalated)
         └─► operational_alert
                └─► operational_event
                       ├─► master.alert_threshold_rule    (lineage of the limit)
                       └─► machine_sensor_reading         (the measurement)
```

**Five hops maximum, every hop an immutable row, every hop a primary-key lookup into a low-volume table.** This is the property the schema was built to have, because `PROJECT_OVERVIEW.md` §16.5 requires that any recommendation be traceable to its evidence — and a chain that were longer, or that passed through mutable rows, would make the guarantee unenforceable.

Three physical decisions protect this chain, and each was made for this reason:

| Decision | Protects |
|---|---|
| `ON DELETE RESTRICT` on 165 foreign keys | Retention cannot silently remove cited evidence |
| `operational_event.threshold_value_breached` captured at detection | Evidence survives a threshold retune and a telemetry purge |
| `ON DELETE SET NULL` on `triggering_reading_id` | Telemetry purge severs lineage without destroying evidence |

### 51.9 Conformance to the frozen models

| Frozen requirement | Physical expression |
|---|---|
| 29 master entities | 29 tables in `master`, numbered M1–M29 matching §1–§29 of the master document |
| 24 operational entities | 24 tables, numbered O1–O24 matching §E1–§E24 of the operational document |
| No entity renamed, merged, split, added, or removed | Table count and names match exactly |
| No attribute removed | Every attribute in both frozen documents appears as a column |
| Master data referenced, never duplicated | 78 foreign keys; **one** documented value copy, in `operational_event`, justified in §29 |
| Master data never modified by operational processing | No role holds write access to `master` except `ff_admin` |
| One owner per table | Nine roles, privilege matrix in §50.2. Two documented exceptions in §25.2 |
| Simulator reads no agent output | Withheld `SELECT` privilege, §50.4 |
| Append-only is the default | 16 tables with no `UPDATE` privilege granted to any role |
| Evidence is immutable | Withheld `UPDATE` on events, predictions, contexts, recommendations, actions |
| **Failure probability originates only in `prediction_result`** | **`ai_recommendation` has no probability column** |
| Root cause from a controlled vocabulary | `root_cause_failure_category_id` `NOT NULL` referencing `master.failure_category` |
| Suppressions are recorded | `supervisor_context` written for every evaluation; `notification.is_suppressed`; `alert_resolution_type = 'false_positive'` |
| Acyclic dependency graph | Proven by layer assignment, §24. Four cycles designed out |
| Event time distinct from record time | Every operational table carries both |
| Retention per table, purge preserves the evidence chain | §22 windows, §41.5 ordering, §13.4 dependency floors |
| Every maintained value has a reconciliation rule | §41.6 register |
| **No table represents a command to a machine** | **Among 53 tables and 166 references, none does** |

### 51.10 Deliverable boundaries observed

**Not present anywhere in this document:** SQL, DDL, `CREATE TABLE`, `ALTER TABLE`, DML, triggers, stored procedures, functions, views, materialised views, index definitions, Alembic migrations, SQLAlchemy models, Python, ORM code, API design, repository or service patterns, simulator algorithms, ML models, prompt engineering, frontend code, authentication implementation, deployment configuration, container or orchestration manifests, or test code.

**Present:** a complete physical specification of 53 tables, 166 relationships, 65 ENUM types, ~210 check constraints, 8 correctness-critical partial unique indexes, and the strategy documents that govern types, constraints, naming, performance, transactions, integrity, scalability, and access.

---

## 52. Document governance

**Authority.** This document is the definitive specification for the physical database layer. Every SQLAlchemy model, Alembic migration, and component query derives from it.

**Position in the document set.**

| Document | Defines | Status |
|---|---|---|
| `PROJECT_OVERVIEW.md` | Vision, architecture, principles, explainability contract | Frozen |
| `FACTORY_MASTER_DATA_DESIGN.md` | 29 static entities | Frozen |
| `FACTORY_OPERATIONAL_DATA_DESIGN.md` | 24 dynamic entities | Frozen |
| `FACTORY_POSTGRESQL_DATABASE_SCHEMA.md` | **The physical PostgreSQL implementation of all three** | **This document** |

**This document implements; it does not design.** Where it makes a decision, that decision concerns how PostgreSQL physically represents an already-settled logical model. Every such decision is recorded with its rationale and, where an alternative was rejected, with the alternative named.

**Change control.**

| Change | Requires |
|---|---|
| New column | A corresponding attribute in a frozen document. **This document may not add attributes** |
| New table | A corresponding entity in a frozen document |
| Changed type | Justification against §33's four principles |
| New constraint | Confirming it does not contradict a frozen business rule |
| Moving a rule between enforcement layers | Updating the §41.3 or §41.6 register |
| New ENUM value | A hand-written migration, additive only, audited per §37.1 |
| Changed referential action | Explicit justification against §32. **`CASCADE` requires revising this document** |
| New index | Part IX is strategy only. Index definitions belong to a later phase |
| Changed retention | Confirming the §13.4 floors still hold |

**Three findings this document contributes back**, which the frozen logical models did not need to resolve and which any implementer must know:

1. **The retention dependency chain (§13.4).** Stated retention windows are **floors**, not exact ages, because `RESTRICT` pins parents while children reference them. `maintenance_work_record` at 5 years transitively pins six tables. A purge job written to delete strictly by age will fail — correctly.

2. **The `SET NULL` exception (§32.3).** `operational_event.triggering_reading_id` cannot use `RESTRICT` without deadlocking the 90-day telemetry purge against the multi-year event retention. `SET NULL` is safe **only because** the frozen model captured the evidence values onto the event, which is that decision paying for itself.

3. **The partition primary-key implication (§44.3).** Range-partitioning `machine_sensor_reading` on `recorded_at` would require a composite primary key, breaking `operational_event.triggering_reading_id`. v1 uses the single-column key deliberately; the migration analysis is recorded rather than the change pre-empted.

---

*End of PostgreSQL Database Schema Specification.*
