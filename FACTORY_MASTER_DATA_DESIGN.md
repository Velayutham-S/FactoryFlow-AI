# FactoryFlow AI — Factory Master Data Design

**Production-Grade Static Data Model for a Medium-Sized Discrete Manufacturing Plant**

---

| Field | Value |
|---|---|
| Project | FactoryFlow AI |
| Document Type | Factory Master Data Design (Logical Data Model) |
| Phase | Phase 0 — Foundation. Precedes simulator, database, ML, and agent implementation |
| Document Status | Baseline blueprint for all future schema, simulator, and agent work |
| Companion Document | `PROJECT_OVERVIEW.md` (vision, architecture, principles) |
| Realised By | `FACTORY_SQLITE_DATABASE_SCHEMA.md` (physical schema), `FACTORY_SQLALCHEMY_MODEL_SPECIFICATION.md` (ORM layer) |
| Target Database | SQLite 3 — a single embedded database file, accessed through Python's `sqlite3` module |
| Scope of This Document | Design only. No SQL, no ORM models, no APIs, no simulator logic, no UI |
| Entity Count | 29 master entities across 6 functional groups |

> **Purpose of this document.** This is the single source of truth for every static entity that exists inside the FactoryFlow AI factory. It defines *what exists* in the plant before anything starts moving. Every future SQLite table, SQLAlchemy model, simulator module, backend service, and AI agent must derive its structure from this document. Where implementation reality forces a change, this document is revised first.

---

## Table of Contents

- [Part I — Foundations](#part-i--foundations)
  - [1. Document Objective](#1-document-objective)
  - [2. The Master Data / Operational Data Boundary](#2-the-master-data--operational-data-boundary)
  - [3. Design Conventions](#3-design-conventions)
  - [4. Entity Catalogue](#4-entity-catalogue)
- [Part II — Entity Designs](#part-ii--entity-designs)
  - [Group A — Plant & Organization](#group-a--plant--organization)
  - [Group B — Production Structure](#group-b--production-structure)
  - [Group C — Asset Hierarchy](#group-c--asset-hierarchy)
  - [Group D — People & Contacts](#group-d--people--contacts)
  - [Group E — Materials & Partners](#group-e--materials--partners)
  - [Group F — Reliability & Policy](#group-f--reliability--policy)
- [Part III — Relationship Model](#part-iii--relationship-model)
- [Part IV — Master Data Quality & Consumers](#part-iv--master-data-quality--consumers)
- [Part V — Design Decisions & Rejected Alternatives](#part-v--design-decisions--rejected-alternatives)
- [Part VI — Governance](#part-vi--governance)

---

# Part I — Foundations

## 1. Document Objective

### 1.1 What this document defines

The **Factory Master Data** is the static, slowly-changing description of the factory: the plant, its physical areas, its organizational units, its production lines, its machines and machine specifications, its products and material structures, its people and teams, its suppliers and customers, and the configuration that governs monitoring and escalation behavior.

It is the answer to the question: **"What exists in this factory?"**

It is deliberately not the answer to: *"What is happening in this factory right now?"* That is operational data, designed in a later phase.

### 1.2 Why this comes first

Every downstream component in the FactoryFlow AI pipeline reads master data:

```
Factory Master Data   ← this document
        ↓
Factory Simulator          needs: what machines exist, what they emit, what normal looks like
        ↓
Operational Database       needs: foreign key targets for every reading and event
        ↓
Monitoring Agent           needs: which parameters to watch and at what limits
        ↓
Prediction Agent           needs: machine specifications and which parameters are model features
        ↓
Supervisor Agent           needs: production context, inventory, maintenance, escalation policy
        ↓
Decision Agent (Gemini)    needs: business impact inputs, failure taxonomy, team capability
        ↓
Notification Service       needs: who to contact, on which channel, at which severity
        ↓
Factory Manager
```

If master data is designed poorly, every stage above inherits the defect. If it is designed well, each stage becomes a straightforward read against a stable structure. This is why the master data model is built before a single line of simulator or agent code exists.

### 1.3 Design brief

Design a realistic medium-sized discrete manufacturing plant — the kind of data an ERP or MES would hold — scoped precisely to what FactoryFlow AI actually consumes. Realistic enough to be credible to a manufacturing engineer. Small enough that every entity can be justified in one sentence.

### 1.4 The justification test

Every entity in this document passes three tests:

1. **Business reason.** A real factory would hold this information, for a stated operational reason.
2. **Named consumer.** At least one FactoryFlow AI component reads it, and that component is named in the entity's design.
3. **No duplication.** The information exists in exactly one place, and anything derivable is derived rather than stored.

An entity that fails any test is excluded. Part V records the entities and attributes that were considered and rejected, and why.

---

## 2. The Master Data / Operational Data Boundary

This is the most important architectural decision in the document, and the one most often got wrong. Getting it right is what keeps the operational database clean and the agents simple.

### 2.1 The rule

| | Master Data (this document) | Operational Data (later phase) |
|---|---|---|
| **Answers** | What exists | What happened, what is happening |
| **Change frequency** | Rarely — days, weeks, months | Constantly — seconds to minutes |
| **Changed by** | Humans, via configuration | The system, via simulation and processing |
| **Row growth** | Bounded and small | Unbounded and continuous |
| **Lifecycle** | Created once, edited occasionally, soft-retired | Appended, never edited |
| **Example** | Machine MC-0101 is a VMC-500 on Line 01 | Machine MC-0101 reported 74.2 °C at 14:32:05 |

### 2.2 Explicitly excluded from master data

The following belong to operational tables and appear **nowhere** in this document as stored attributes:

| Excluded | Why it is operational | Where it will live |
|---|---|---|
| Sensor readings (temperature, RPM, torque, tool wear, vibration, power) | High-frequency time series, appended continuously | Operational telemetry tables |
| Machine operational status (running, idle, down, changeover) | Changes minute to minute | Operational machine state |
| Production counts and actual output | Accumulates continuously | Operational production records |
| Current inventory quantity on hand | Changes with every consumption and receipt | Operational inventory movements |
| Production orders and order status | Transactional, created and closed daily | Operational order tables |
| Detected operational events and alerts | Generated by the Monitoring Agent | Operational event log |
| Prediction results, failure probabilities, risk scores | Generated by the Prediction Agent | Operational prediction records |
| Recommendations and decision history | Generated by the Decision Agent | Operational recommendation records |
| Notification delivery logs | Generated by the Notification Service | Operational notification log |
| Maintenance work order execution and completion | Transactional maintenance history | Operational maintenance records |

### 2.3 Three boundary cases worth stating explicitly

These three are where the master/operational line is genuinely ambiguous, so the design decision is recorded rather than left implicit.

**Case 1 — Inventory thresholds vs inventory quantity.**
`reorder_point`, `safety_stock_qty`, and `max_stock_qty` are **master data**: they are policy, set by a planner, and they change rarely. `quantity_on_hand` is **operational**: it changes with every issue and receipt. The `inventory_item` entity therefore carries thresholds and no quantity. When the Supervisor Agent asks "is the spare part available?", it compares an operational quantity against a master threshold — two sources, one join, no ambiguity about which is authoritative.

**Case 2 — Maintenance schedule definition vs maintenance due status.**
`machine_maintenance_schedule` defines the *policy*: this machine needs preventive maintenance every 500 operating hours, taking 120 minutes, performed by the mechanical team. That is master data. `last_performed_date` and `next_due_date` are **derived facts** that depend on operational maintenance history, and they are deliberately **not stored** on the schedule. The schedule carries a `baseline_start_date` — an immutable anchor — and due status is computed by the Supervisor Agent from the schedule definition plus operational history. Storing a cached `next_due_date` would create two sources of truth for the same fact, and cached due dates going stale is a classic source of wrong maintenance decisions.

**Case 3 — Asset lifecycle status vs machine operational status.**
`machine.lifecycle_status` (in service, standby, under overhaul, decommissioned) is **master data**: it describes whether the asset is part of the factory, and it changes a handful of times in a machine's life. Machine *operational* status (running, idle, down) is **operational**: it changes constantly. These are different facts with different lifetimes and they are never conflated. A decommissioned machine is excluded from monitoring entirely; a machine that is `in_service` but currently `down` is very much the Monitoring Agent's business.

### 2.4 One necessary clarification about monitored parameters

The project brief excludes temperature, RPM, torque, and tool wear from master data. That exclusion applies to **readings** — the values.

The **definitions** are master data and are essential:

- *"Spindle temperature is a monitored parameter, measured in °C, and it is a model feature"* → master data (`machine_parameter`, `machine_type_parameter`).
- *"On a VMC-500, spindle temperature above 82 °C sustained for 60 seconds is a critical condition"* → master data (`alert_threshold_rule`).
- *"Machine MC-0101 reported 84.1 °C at 14:32:05"* → operational data, excluded from this document.

Without the definitions, the simulator would not know what to emit and the Monitoring Agent would have thresholds hardcoded in application logic. Both would be architectural failures.

---

## 3. Design Conventions

Conventions are stated once here and applied uniformly across all 29 entities. They are not repeated per entity.

### 3.1 Primary key strategy

**Every entity uses a surrogate integer primary key named `<entity>_id`, plus a unique human-readable business code named `<entity>_code`.**

| Aspect | Decision |
|---|---|
| Surrogate PK | `machine_id` — integer, system-generated, immutable, never displayed |
| Business key | `machine_code` — e.g. `MC-0101`, unique, indexed, shown to humans and used in AI prompts |
| Foreign keys | Always reference the surrogate `_id`, never the code |

**Why both, rather than one or the other:**

- **Why not the business code as PK?** Business codes change. A plant renumbers its lines, a product code is revised after an engineering change. With a natural PK, that rename cascades into every referencing row and every historical operational record. With a surrogate PK, a rename is a single-column update on one row.
- **Why not a surrogate PK alone?** The project brief requires human-readable identifiers, and the requirement is real: a recommendation that says *"Machine MC-0101 on Line LN-01"* is usable by a factory manager, while *"machine_id 47 on production_line_id 3"* is not. Codes also make AI prompts and log output legible, and they make seed data reviewable by a human.
- **Why not UUIDs?** No distributed generation requirement and no merge-across-systems requirement exists. Integers are smaller, faster to join, and easier to read while debugging. UUIDs would be complexity without benefit here.

**Junction entities** (`product_line_capability`, `machine_type_parameter`, `machine_type_failure_mode`, `bill_of_materials`, `alert_threshold_rule`) use a surrogate PK plus a **composite unique constraint** on the natural key pair. The surrogate keeps child references and application code simple; the unique constraint enforces the real business rule.

**Not every entity carries a business code.** The `_code` attribute exists where a human refers to the row by name — a machine, a line, a product, a person. It is deliberately absent from rows that only qualify a relationship or extend a parent: the five junction entities above, and `notification_recipient`, which is identified by its parent `worker_code`. Adding a code to these would create a second identifier for something nobody names on the shop floor. Where a code is absent, the entity's design section says so and states what identifies the row instead.

### 3.2 Naming conventions

| Rule | Convention | Example |
|---|---|---|
| Entity names | `snake_case`, **singular** | `production_line`, not `production_lines` |
| Primary key | `<entity>_id` | `machine_id` |
| Business key | `<entity>_code` | `machine_code` |
| Foreign key | `<referenced_entity>_id` | `production_line_id` |
| Role-qualified FK | `<role>_<entity>_id` | `primary_supplier_id`, `assigned_team_id` |
| Booleans | `is_` or `requires_` prefix | `is_critical_spare`, `requires_line_stop` |
| Units in name | Always suffix the unit | `duration_minutes`, `rated_power_kw`, `cycle_time_seconds`, `lead_time_days`, `scrap_allowance_pct` |
| Enumerated values | `snake_case` lowercase, constrained set | `raw_material`, `spare_part` |
| Dates vs timestamps | `_date` for calendar dates, `_at` for instants | `installation_date`, `created_at` |

**Units are always in the column name.** A column called `duration` invites a wrong assumption; `estimated_duration_minutes` does not. This costs a few characters and eliminates an entire class of bug — particularly relevant when an LLM reads these values and reasons about them in natural language.

### 3.3 Implicit columns on every entity

The following exist on **all 29 entities** and are omitted from the per-entity attribute tables to keep them readable:

| Column | Type | Purpose |
|---|---|---|
| `<entity>_id` | INTEGER, primary key, autoincrement | Surrogate primary key |
| `is_active` | INTEGER (0 = 0, 1 = 1), NOT NULL, default `1` | Soft retirement. Master data is never hard-deleted, because operational history references it |
| `created_at` | DATETIME, NOT NULL | Audit — when the record was created. Stored in UTC |
| `updated_at` | DATETIME, NOT NULL | Audit — when the record was last modified. Stored in UTC |

**Soft delete is mandatory, not optional.** A machine that is scrapped still appears in three years of historical telemetry, events, predictions, and recommendations. Hard-deleting it would orphan that history and destroy the audit trail that the platform's explainability guarantee depends on. Retirement is therefore always `is_active = 0`.

**One documented exception:** `machine` uses `lifecycle_status` instead of `is_active`, because a boolean cannot express the difference between *standby*, *under overhaul*, and *decommissioned* — a distinction the Monitoring Agent needs. Carrying both flags would create two overlapping sources of the same fact. See §17.

### 3.4 Business code formats

Fixed formats, so seed data is consistent and codes are self-describing on sight:

| Entity | Format | Example |
|---|---|---|
| `plant` | `PLT-<nn>` | `PLT-01` |
| `plant_area` | `AREA-<XXX>` | `AREA-MCH` |
| `department` | `DEP-<XXX>` | `DEP-MNT` |
| `shift` | `SH-<X>` | `SH-A` |
| `production_line` | `LN-<nn>` | `LN-01` |
| `product` | `PRD-<XX>-<nnn>` | `PRD-GH-100` |
| `machine_category` | `MCAT-<XXX>` | `MCAT-CNC` |
| `machine_type` | `MTY-<model>` | `MTY-VMC-500` |
| `machine` | `MC-<line><nn>` | `MC-0101` |
| `machine_parameter` | `PRM-<XXXX>` | `PRM-TEMP` |
| `worker_role` | `ROL-<XXX>` | `ROL-LSUP` |
| `worker` | `EMP-<nnnn>` | `EMP-1002` |
| `maintenance_team` | `MTM-<XXXX>` | `MTM-MECH` |
| `maintenance_engineer` | `ENG-<nn>` | `ENG-01` |
| `inventory_item` | `INV-<class>-<key>` | `INV-CP-BRG-6205` |
| `inventory_location` | `LOC-<zone>-<bin>` | `LOC-SP-B2` |
| `supplier` | `SUP-<nnn>` | `SUP-002` |
| `customer` | `CUS-<nnn>` | `CUS-001` |
| `failure_severity_level` | `SEV-<n>` | `SEV-1` |
| `failure_category` | `FC-<XXXX>` | `FC-BRG` |
| `machine_maintenance_schedule` | `SCH-<nnnn>` | `SCH-0001` |
| `alert_threshold_profile` | `ATP-<XXXX>` | `ATP-VMC-STD` |
| `business_rule` | `BR-<CAT>-<KEY>` | `BR-ESC-PROB` |

The `MC-<line><nn>` format encodes the line in the machine code (`MC-0101` = line 01, machine 01). This is common practice on real shop floors and it makes log output and AI-generated text immediately locatable by a human — but the **authoritative** line assignment is the `production_line_id` foreign key, never the code substring. Application logic must never parse codes to derive relationships.

### 3.5 Data types

Logical types are used throughout. They map directly to SQLite 3 but describe intent, not DDL.

| Logical type | Used for | Notes |
|---|---|---|
| `INTEGER` | Surrogate keys, counts, whole-number intervals | Also the boolean carrier — see below |
| `VARCHAR(n)` | Codes and names with a known bound | Length stated per attribute |
| `TEXT` | Free-form descriptions, notes | Unbounded |
| `NUMERIC(p,s)` | Money, physical measurements, percentages | Precision and scale are declared and are design information |
| `INTEGER` (flag) | Flags | `0` = 0, `1` = 1. Always `NOT NULL` with an explicit default |
| `DATE` | Calendar dates | `installation_date` |
| `TIME` | Wall-clock time of day | `shift.start_time` |
| `DATETIME` | Instants | Always UTC |
| `TEXT + CHECK` (constrained set) | Small fixed vocabularies | Allowed values listed per attribute |

**Four notes on how these logical types land in SQLite 3**, stated here so they are not repeated on every attribute.

**Flags are `INTEGER`, holding `0` or `1`.** SQLite has no boolean type. Every flag in this document is an `INTEGER` column carrying `0` for false and `1` for true, `NOT NULL`, with a stated default and a check constraint restricting it to those two values. Example records in this document show `0` and `1` for the same reason — that is what a reader of the table will see.

**Instants are `DATETIME`, stored in UTC.** SQLite has no timezone-aware type and stores no offset, so UTC is a convention the application upholds rather than something the column records. Values are written as UTC ISO-8601 text and rendered in `plant.timezone` at presentation. Because the format is fixed-width, timestamps sort and compare chronologically as text.

**Constrained vocabularies are `TEXT` with a `CHECK` constraint** listing the permitted values, named `ck_<table>_<column>_allowed`. SQLite has no `ENUM` type, and this is the direct equivalent: the value list is enforced by the database for every writer, and it is visible in the schema itself rather than in a separate type object. Every vocabulary in this document is preserved exactly, value for value.

**`NUMERIC(p,s)` is declared for its design meaning.** SQLite gives it numeric affinity rather than fixed-point storage, so the declared precision and scale document intent and drive the range check constraints; they are not enforced by the engine. FactoryFlow AI holds standard costs and estimated impact figures rather than ledger balances, so this is acceptable — and no monetary value is ever handled as a Python `float`.

**On constrained vocabularies versus lookup tables.** Small, stable, code-only vocabularies (`area_type`, `interval_basis`, `item_type`) are modelled as constrained value sets rather than separate lookup tables. Vocabularies that carry their own attributes, are referenced from several places, or are expected to be extended by users (`failure_category`, `failure_severity_level`, `worker_role`, `machine_parameter`) are proper entities. The dividing line: **if the vocabulary needs attributes of its own, it is an entity; if it is only a label, it is a constrained value.** This keeps the model from acquiring a dozen two-column lookup tables that add joins without adding information.

### 3.6 Attribute table format

Each entity's attributes are presented in one table with these columns:

| Column | Meaning |
|---|---|
| **Attribute** | Column name |
| **Type** | Logical data type |
| **Req** | `Yes` = NOT NULL, `No` = nullable, with the meaning of NULL stated in the purpose column |
| **Example** | A realistic value from the sample factory |
| **Validation** | Constraint enforced at database or application level |
| **Purpose & business meaning** | Why the attribute exists and what it means on the shop floor |

The brief lists *Purpose* and *Business Meaning* separately. In a six-column table these two columns would restate each other, so they are merged into one column that covers both — what the attribute is for, and what it means operationally. All seven requested facets are present.

### 3.7 The sample factory

All example records describe one coherent fictional plant, so relationships can be traced across entities:

> **Northgate Precision Works** — a medium-sized discrete manufacturing plant in Coimbatore, India. It machines and assembles gearbox housings, centrifugal pump assemblies, and valve bodies for three industrial customers. Four production lines, eight machines, seven plant areas, five departments, four shifts, four maintenance teams.

All names, codes, and figures are fictional and internally consistent. Person names are placeholders.

---

## 4. Entity Catalogue

29 entities in 6 functional groups.

### 4.1 Full catalogue

| # | Entity | Group | Kind | One-line purpose |
|---|---|---|---|---|
| 1 | `plant` | A | Root | The manufacturing site being monitored |
| 2 | `plant_area` | A | Structural | Physical zones within the plant |
| 3 | `department` | A | Structural | Organizational units that own work |
| 4 | `shift` | A | Reference | Working time patterns |
| 5 | `production_line` | B | Structural | Sequenced groups of machines producing output |
| 6 | `product` | B | Reference | Finished goods the plant manufactures |
| 7 | `product_line_capability` | B | Junction | Which products can run on which lines, and how fast |
| 8 | `machine_category` | C | Lookup | Broad machine groupings |
| 9 | `machine_type` | C | Reference | Machine models and their engineering specifications |
| 10 | `machine` | C | Core asset | Individual physical machines |
| 11 | `machine_parameter` | C | Lookup | Catalogue of monitorable parameters |
| 12 | `machine_type_parameter` | C | Junction | Which parameters a machine type exposes, and its normal envelope |
| 13 | `worker_role` | D | Lookup | Job roles and their authority |
| 14 | `worker` | D | Core people | Plant personnel |
| 15 | `maintenance_team` | D | Structural | Maintenance crews by specialization and shift |
| 16 | `maintenance_engineer` | D | Specialization | Maintenance-specific attributes of a worker |
| 17 | `notification_recipient` | D | Configuration | Who receives notifications, on which channel |
| 18 | `inventory_location` | E | Structural | Physical storage locations |
| 19 | `inventory_item` | E | Reference | Materials, components, consumables, spare parts |
| 20 | `bill_of_materials` | E | Junction | Materials consumed per unit of product |
| 21 | `supplier` | E | Reference | External material sources |
| 22 | `customer` | E | Reference | External buyers of finished goods |
| 23 | `failure_severity_level` | F | Lookup | Severity scale with response targets |
| 24 | `failure_category` | F | Lookup | Controlled vocabulary of failure modes |
| 25 | `machine_type_failure_mode` | F | Junction | Which failures are plausible on which machine type |
| 26 | `machine_maintenance_schedule` | F | Configuration | Planned maintenance policy per machine |
| 27 | `alert_threshold_profile` | F | Configuration | Named monitoring policy sets |
| 28 | `alert_threshold_rule` | F | Configuration | Warning and critical limits per parameter |
| 29 | `business_rule` | F | Configuration | Tunable platform behavior parameters |

### 4.2 Entity kinds

| Kind | Characteristics | Entities |
|---|---|---|
| **Root** | No outgoing foreign keys; the top of the hierarchy | `plant` |
| **Structural** | Defines the physical or organizational skeleton | `plant_area`, `department`, `production_line`, `maintenance_team`, `inventory_location` |
| **Core** | The primary subjects the platform reasons about | `machine`, `worker` |
| **Reference** | Substantial entities referenced widely | `machine_type`, `product`, `inventory_item`, `supplier`, `customer`, `shift` |
| **Lookup** | Small controlled vocabularies with their own attributes | `machine_category`, `machine_parameter`, `worker_role`, `failure_category`, `failure_severity_level` |
| **Junction** | Resolves a genuine many-to-many, carrying its own attributes | `product_line_capability`, `machine_type_parameter`, `machine_type_failure_mode`, `bill_of_materials`, `alert_threshold_rule` |
| **Specialization** | One-to-one extension of a core entity | `maintenance_engineer` |
| **Configuration** | Tunable policy that governs platform behavior | `alert_threshold_profile`, `alert_threshold_rule`, `machine_maintenance_schedule`, `business_rule`, `notification_recipient` |

Every junction entity in this model carries **its own attributes** — a cycle time, an operating envelope, a threshold, a quantity. None is a bare pair of foreign keys. That is the test applied before adding one: a junction that carries no attributes usually means the relationship should have been a foreign key instead.

### 4.3 Coverage against the brief

All 24 required entities are present. Five additional entities were added, each with a named consumer.

| Required entity | Modelled as | Note |
|---|---|---|
| Plant | `plant` | |
| Plant Areas | `plant_area` | |
| Departments | `department` | |
| Production Lines | `production_line` | |
| Machine Categories | `machine_category` | |
| Machine Types | `machine_type` | |
| Machines | `machine` | |
| Products | `product` | |
| Bills of Materials | `bill_of_materials` | Required — inventory consumption depends on it |
| Inventory Items | `inventory_item` | |
| Inventory Locations | `inventory_location` | |
| Suppliers | `supplier` | |
| Customers | `customer` | |
| Workers | `worker` | |
| Worker Roles | `worker_role` | |
| Shifts | `shift` | |
| Maintenance Teams | `maintenance_team` | |
| Maintenance Engineers | `maintenance_engineer` | 1:1 specialization of `worker`, not a duplicate person record |
| Business Rules | `business_rule` | |
| Alert Threshold Profiles | `alert_threshold_profile` + `alert_threshold_rule` | Split into policy header and per-parameter rules |
| Notification Recipients | `notification_recipient` | |
| Machine Maintenance Schedule | `machine_maintenance_schedule` | |
| Failure Categories | `failure_category` | |
| Failure Severity Levels | `failure_severity_level` | |

| Added entity | Consumer that requires it |
|---|---|
| `machine_parameter` | Simulator (what to emit), Monitoring Agent (what to check), Prediction Agent (feature vocabulary) |
| `machine_type_parameter` | Simulator (normal operating envelope per machine type), Prediction Agent (which parameters are model features) |
| `product_line_capability` | Simulator (line output rate), Decision Agent (lost output and alternate-line routing) |
| `machine_type_failure_mode` | Decision Agent (constrains root-cause hypotheses to failure modes plausible for that machine type, and names the spare part required) |
| `alert_threshold_rule` | Monitoring Agent (parameter-level limits, iterable without knowing column names) |

---

# Part II — Entity Designs

Each entity is documented under eight consistent headings: purpose, business description, primary key, attributes, relationships, business rules, example records, and why FactoryFlow AI needs it.

---

## Group A — Plant & Organization

The physical and organizational skeleton of the factory. Everything else hangs off these four entities.

---

### 1. `plant`

**Purpose**

Represents the single manufacturing site that FactoryFlow AI monitors. It is the root of the entire master data hierarchy and the anchor for site-wide settings that every other entity inherits by context — timezone, currency, and operating calendar.

**Business description**

A plant is a physical manufacturing facility with an address, a working calendar, and a management structure. In a real ERP, the plant is the highest-level organizational object under which all production, inventory, and personnel data is grouped. Here there is exactly one plant, but it is modelled as a proper entity rather than assumed, for two concrete reasons.

First, **timezone**. Every operational timestamp — every telemetry reading, event, prediction, and notification — must be interpretable in local plant time. A recommendation that says *"temperature has been rising since the start of the night shift"* is only correct if the system knows the plant's local time. Hardcoding a timezone in application configuration puts a business fact in the wrong layer.

Second, **currency**. The Decision Agent produces business impact statements with monetary figures. Those figures need a currency, and the currency belongs to the site, not to the code.

**Primary key**

`plant_id` — surrogate integer.

Chosen over using `plant_code` directly because every other entity in the model carries a `plant_id` foreign key, and site codes do get revised during corporate reorganizations. A single surrogate keeps that rename to one row. `plant_code` carries the unique business constraint.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `plant_code` | VARCHAR(10) | Yes | `PLT-01` | Unique; matches `^PLT-[0-9]{2}$` | Human-readable site identifier used in reports, notifications, and AI-generated text |
| `plant_name` | VARCHAR(120) | Yes | `Northgate Precision Works` | Non-empty | Trading name of the facility, shown on dashboards and in notification subject lines |
| `address_line` | VARCHAR(200) | Yes | `Plot 42, SIDCO Industrial Estate` | Non-empty | Street address, used on reports and for on-site service dispatch |
| `city` | VARCHAR(80) | Yes | `Coimbatore` | Non-empty | Locality of the plant |
| `state_region` | VARCHAR(80) | Yes | `Tamil Nadu` | Non-empty | State or region, relevant for regulatory reporting |
| `country_code` | CHAR(2) | Yes | `IN` | ISO 3166-1 alpha-2 | Country, standardised for unambiguous interpretation |
| `timezone` | VARCHAR(50) | Yes | `Asia/Kolkata` | Valid IANA timezone name | **Critical.** Converts stored UTC instants into local plant time so shift boundaries, "since this morning", and quiet-hours logic are all correct |
| `currency_code` | CHAR(3) | Yes | `INR` | ISO 4217 | Currency for every monetary value in the model — unit costs, downtime cost rates, late-delivery penalties |
| `operating_days_per_week` | INTEGER | Yes | `6` | Between 1 and 7 | Standard working week, used to convert calendar intervals into working intervals for maintenance planning |
| `shifts_per_day` | INTEGER | Yes | `3` | Between 1 and 4 | Expected shift count, used as a consistency check against the `shift` records |
| `commissioned_date` | DATE | Yes | `2016-04-01` | Not in the future | When the plant began production; provides a floor for all asset installation dates |
| `annual_production_capacity_units` | INTEGER | No | `480000` | Greater than 0 when present | Nameplate site capacity. NULL when not formally rated. Gives the Decision Agent a denominator for expressing lost output as a share of site capacity |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Child | `plant_area` | One-to-many | A plant contains many physical areas |
| Child | `department` | One-to-many | A plant contains many organizational departments |
| Child | `shift` | One-to-many | Shift patterns are defined at plant level |

`plant` has **no outgoing foreign keys**. It is the single root of the dependency graph, which is what makes the graph provably acyclic (see §30.4).

**Business rules**

1. Exactly one `plant` row is active in this deployment. Multi-plant support is a Phase 6 roadmap item and requires no structural change — additional rows and existing foreign keys already accommodate it.
2. `timezone` must be a valid IANA name, not a UTC offset. Offsets do not handle daylight saving transitions and would silently corrupt shift-boundary logic in regions that observe DST.
3. All monetary attributes anywhere in the model are expressed in `plant.currency_code`. The model stores no per-row currency, because a single-site plant transacts in one currency and per-row currency codes would be dead weight.
4. `commissioned_date` must be on or before the earliest `machine.installation_date`. A machine cannot be installed in a plant that does not yet exist.
5. `shifts_per_day` should equal the count of active non-general `shift` rows. This is a data quality check, not a hard constraint, since a general shift overlaps the operational shifts.

**Example records**

| plant_code | plant_name | city | country_code | timezone | currency_code | operating_days_per_week | shifts_per_day | commissioned_date |
|---|---|---|---|---|---|---|---|---|
| `PLT-01` | Northgate Precision Works | Coimbatore | `IN` | `Asia/Kolkata` | `INR` | 6 | 3 | 2016-04-01 |

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Reads `timezone`, `operating_days_per_week`, and `shifts_per_day` to generate telemetry on a realistic working calendar rather than a flat 24×7 stream |
| Operational Database | Provides the root context for all operational records |
| Monitoring Agent | Uses `timezone` to evaluate time-of-day conditions correctly |
| Supervisor Agent | Uses the working calendar to distinguish "next available maintenance window" from "next calendar day" |
| Decision Agent | Uses `currency_code` for monetary impact statements and `annual_production_capacity_units` to frame lost output proportionally |
| Notification Service | Uses `timezone` for quiet-hours logic and for local timestamps in message bodies |
| Dashboard | Displays site identity and renders all timestamps in local plant time |

---

### 2. `plant_area`

**Purpose**

Represents a distinct physical zone inside the plant. It answers the question *"where is this, physically?"* for production lines and storage locations, and it supplies the environmental context that helps explain machine behavior.

**Business description**

A plant is divided into physical areas: machining bays, assembly halls, packaging and dispatch, warehouses, a spare parts store, a maintenance workshop, a quality lab. Areas matter operationally for three reasons.

**Walking distance.** When the Decision Agent recommends dispatching a maintenance engineer, the physical location determines response time. A team based in the maintenance workshop reaches the machining bay faster than the packaging area.

**Ambient conditions.** Machining bays run hotter than assembly halls. A spindle at 74 °C in a bay with a 38 °C nominal ambient is a different situation from the same reading in a 26 °C climate-controlled hall. Recording nominal ambient temperature per area gives the Decision Agent the context to distinguish "this machine is overheating" from "this whole area is hot today."

**Physical grouping of risk.** If three machines in the same area degrade together, the likely cause is environmental — ventilation, power quality, compressed air supply — not three coincident machine faults. Area membership is what makes that pattern visible.

Areas are **physical**, departments are **organizational**. They are deliberately separate. A machining bay (area) can contain lines owned by the Production department, while the Maintenance and Quality departments also work inside it. Collapsing the two would force an artificial one-to-one mapping that does not hold in a real plant.

**Primary key**

`plant_area_id` — surrogate integer, with `plant_area_code` carrying the unique business constraint.

A composite natural key of (`plant_id`, `area_code`) was considered and rejected: it would propagate a two-column foreign key into `production_line` and `inventory_location`, and again into anything referencing those. Area codes are globally unique in a single-plant deployment anyway.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `plant_area_code` | VARCHAR(12) | Yes | `AREA-MCH` | Unique; matches `^AREA-[A-Z]{3}$` | Shop-floor identifier, recognisable to anyone working in the plant |
| `plant_id` | INTEGER (FK) | Yes | `1` | References active `plant` | The site this area belongs to |
| `area_name` | VARCHAR(100) | Yes | `Machining Bay` | Non-empty | Descriptive name used on dashboards and in recommendations |
| `area_type` | TEXT + CHECK | Yes | `production` | One of: `production`, `assembly`, `warehouse`, `spare_parts_store`, `maintenance_workshop`, `quality_lab`, `dispatch`, `utility` | Classifies the area's function. Determines whether production lines may be located here and which storage types are valid |
| `floor_level` | INTEGER | No | `0` | Between -2 and 10 when present | Building level; 0 is ground. NULL for single-level areas where it is meaningless. Affects access time for maintenance response |
| `floor_space_sqm` | NUMERIC(10,2) | No | `1850.00` | Greater than 0 when present | Usable area. NULL when not surveyed. Supports capacity planning and reporting |
| `nominal_ambient_temp_c` | NUMERIC(5,2) | No | `34.00` | Between -20 and 60 when present | Typical ambient temperature. NULL for areas without a meaningful figure. **Gives the Decision Agent thermal context** when interpreting machine temperature readings |
| `is_climate_controlled` | INTEGER | Yes | `0` | Default 0 | Whether ambient conditions are actively regulated. A climate-controlled area makes ambient a weaker explanation for a temperature excursion |
| `access_restriction` | TEXT + CHECK | Yes | `general` | One of: `general`, `authorized_only`, `restricted` | Who may enter. Affects whether an engineer can be dispatched immediately or needs an escort or permit |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `plant` | Many-to-one | Every area belongs to exactly one plant |
| Child | `production_line` | One-to-many | Lines are physically located in an area |
| Child | `inventory_location` | One-to-many | Storage locations sit inside an area |
| Child | `maintenance_team` | One-to-many | A team may be based in an area (optional) |

**Business rules**

1. Every area belongs to exactly one plant. Areas are not shared between sites.
2. A production line may only be located in an area whose `area_type` is `production` or `assembly`. Machines are not sited in warehouses or quality labs.
3. An `inventory_location` of type `spare_parts_store` should sit in an area of type `spare_parts_store` or `warehouse`. This alignment keeps physical and logical storage models consistent.
4. `nominal_ambient_temp_c` is expected for areas of type `production` and `assembly`, where thermal context affects machine interpretation. It is optional elsewhere.
5. An area may not be soft-retired while it still contains active production lines or active inventory locations. Retirement is a deliberate sequence, not a single flag flip.
6. Areas are physical only. They carry no ownership or reporting relationship — that is `department`.

**Example records**

| plant_area_code | area_name | area_type | floor_level | floor_space_sqm | nominal_ambient_temp_c | is_climate_controlled | access_restriction |
|---|---|---|---|---|---|---|---|
| `AREA-MCH` | Machining Bay | `production` | 0 | 1850.00 | 34.00 | 0 | `general` |
| `AREA-ASM` | Assembly Hall | `assembly` | 0 | 1200.00 | 28.00 | 0 | `general` |
| `AREA-PKG` | Packaging & Dispatch | `dispatch` | 0 | 640.00 | 31.00 | 0 | `general` |
| `AREA-WHR` | Raw Material Warehouse | `warehouse` | 0 | 980.00 | 32.00 | 0 | `authorized_only` |
| `AREA-SPR` | Spare Parts Store | `spare_parts_store` | 0 | 180.00 | 29.00 | 0 | `authorized_only` |
| `AREA-MNT` | Maintenance Workshop | `maintenance_workshop` | 0 | 320.00 | 30.00 | 0 | `authorized_only` |
| `AREA-QLB` | Quality Laboratory | `quality_lab` | 1 | 150.00 | 22.00 | 1 | `restricted` |

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Uses `nominal_ambient_temp_c` as the thermal baseline when generating realistic machine temperature, so readings correlate with their environment instead of drifting independently |
| Monitoring Agent | Enables area-level correlation — several machines degrading in one area points to an environmental cause rather than coincident machine faults |
| Supervisor Agent | Assembles physical context: which area, what ambient conditions, what access restrictions apply to dispatch |
| Decision Agent | Uses ambient and climate-control context to qualify root-cause hypotheses, and access restrictions to qualify response feasibility |
| Dashboard | Groups machines and lines by physical area, which is how shop-floor staff naturally navigate a plant |

---

### 3. `department`

**Purpose**

Represents an organizational unit that owns work and people. It answers *"who is responsible?"* — as distinct from `plant_area`, which answers *"where is it?"*

**Business description**

Departments are the plant's accountability structure. Production owns the lines and the operators. Maintenance owns the machine health and the engineers. Quality owns inspection. Warehouse and Logistics owns material movement. Planning owns the schedule.

Departments matter to FactoryFlow AI for escalation ownership. When a machine is predicted to fail, two departments are implicated: Production owns the output at risk, Maintenance owns the repair. A recommendation that names both, and reaches the right manager in each, is actionable. One that names neither is a broadcast.

Departments also carry the cost centre, which is what connects operational downtime to a financial owner.

**Primary key**

`department_id` — surrogate integer, with `department_code` unique.

**Note on the absent manager foreign key.** A `manager_worker_id` column on `department` is the obvious first instinct, and it is deliberately not here. `worker.department_id` already points from worker to department; adding a manager pointer back from department to worker creates a circular foreign key dependency between the two tables. That breaks clean insert ordering, complicates seeding, and the brief explicitly requires avoiding circular dependencies.

Instead, **leadership is a property of the person, not of the unit.** `worker_role.is_managerial` marks managerial roles, and the department manager is the active worker in that department holding a managerial role. This pattern is applied consistently to department managers, line supervisors, and maintenance team leads. It keeps the dependency graph acyclic, and it models reality more honestly: a person holds a role, and roles change more often than organizational structures.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `department_code` | VARCHAR(12) | Yes | `DEP-MNT` | Unique; matches `^DEP-[A-Z]{3}$` | Short organizational identifier used in reports and notification routing |
| `plant_id` | INTEGER (FK) | Yes | `1` | References active `plant` | The site this department operates within |
| `department_name` | VARCHAR(100) | Yes | `Maintenance` | Non-empty | Full department name shown to users |
| `department_function` | TEXT + CHECK | Yes | `maintenance` | One of: `production`, `maintenance`, `quality`, `warehouse`, `planning`, `engineering` | Classifies what the department does. Drives escalation routing: a machine failure risk routes to `maintenance` for repair and `production` for output impact |
| `cost_center_code` | VARCHAR(20) | Yes | `CC-2200` | Unique; non-empty | Finance identifier. Connects downtime cost to the accountable budget owner and supports future cost reporting |
| `escalation_email` | VARCHAR(150) | No | `maintenance@northgate.example` | Valid email format when present | Departmental distribution address. NULL when the department has no shared mailbox and relies solely on named recipients. Provides a fallback when no individual recipient matches |
| `headcount_budget` | INTEGER | No | `18` | Greater than or equal to 0 when present | Approved staffing level. NULL when not budgeted separately. Lets the Supervisor Agent compare available staff against approved capacity |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `plant` | Many-to-one | Every department belongs to one plant |
| Child | `production_line` | One-to-many | A department owns lines |
| Child | `worker` | One-to-many | A worker belongs to exactly one department |
| Child | `maintenance_team` | One-to-many | Maintenance teams belong to a department |

**Business rules**

1. **A worker belongs to exactly one department.** Matrix reporting is excluded — it would require a many-to-many junction that no FactoryFlow AI component consumes, and it would make "who is accountable" ambiguous, which is the opposite of what escalation needs.
2. A department belongs to exactly one plant.
3. Every production line is owned by exactly one department, normally one with `department_function = 'production'`.
4. Maintenance teams belong to a department with `department_function = 'maintenance'`.
5. Each department should have exactly one active worker holding a managerial role — the de facto manager. Enforced as a data quality check rather than a database constraint, since transitions leave brief gaps.
6. `cost_center_code` is unique across departments. Two departments sharing a cost centre would make downtime cost attribution ambiguous.
7. A department cannot be soft-retired while active workers or active production lines still reference it.

**Example records**

| department_code | department_name | department_function | cost_center_code | escalation_email | headcount_budget |
|---|---|---|---|---|---|
| `DEP-PRD` | Production | `production` | `CC-2100` | `production@northgate.example` | 62 |
| `DEP-MNT` | Maintenance | `maintenance` | `CC-2200` | `maintenance@northgate.example` | 18 |
| `DEP-QLT` | Quality Assurance | `quality` | `CC-2300` | `quality@northgate.example` | 9 |
| `DEP-WHS` | Warehouse & Logistics | `warehouse` | `CC-2400` | `stores@northgate.example` | 11 |
| `DEP-PLN` | Production Planning | `planning` | `CC-2500` | `planning@northgate.example` | 5 |

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Supervisor Agent | Resolves organizational ownership when assembling context — which department owns the affected line, which owns the repair |
| Decision Agent | Names the accountable department in recommendations, so an action has an owner rather than being addressed to nobody |
| Notification Service | Uses `escalation_email` as a departmental fallback when no individual recipient matches the severity or scope |
| Dashboard | Groups risk and recommendations by owning department for management-level views |
| Future reporting | `cost_center_code` connects accumulated downtime to financial reporting without retrofitting the model |

---

### 4. `shift`

**Purpose**

Defines the working time patterns of the plant. Establishes when the factory is producing, which crew is on duty, and what "now" means in operational terms.

**Business description**

A medium-sized plant typically runs three rotating eight-hour production shifts plus a general day shift for staff who work standard office hours — planners, quality engineers, automation specialists.

Shifts matter to FactoryFlow AI in four distinct ways:

**Simulation realism.** Machines behave differently across shifts. Output typically dips on the night shift; changeovers cluster at shift boundaries. A simulator that ignores shifts produces a flat, unconvincing data stream.

**Notification timing.** A high-severity recommendation at 03:00 should reach the night response team, not a day-shift supervisor who is asleep. Shift definitions are what make that routing possible.

**Maintenance windows.** The Supervisor Agent needs to know when a machine can be stopped for planned work. Shift boundaries and non-production periods are the natural windows.

**Interpretation of trends.** A recommendation stating *"torque has drifted upward since the start of B shift"* is far more useful to a manager than one giving a raw timestamp. It anchors the observation in the crew's own frame of reference.

**Primary key**

`shift_id` — surrogate integer, with `shift_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `shift_code` | VARCHAR(8) | Yes | `SH-A` | Unique; matches `^SH-[A-Z]{1,4}$` | Short identifier used by shop-floor staff and in notification text |
| `plant_id` | INTEGER (FK) | Yes | `1` | References active `plant` | Shift patterns are defined per site |
| `shift_name` | VARCHAR(60) | Yes | `Morning Shift` | Non-empty | Descriptive name shown on dashboards |
| `start_time` | TIME | Yes | `06:00` | Valid time of day | Local start time in `plant.timezone`. Defines the shift boundary for trend anchoring and crew handover |
| `end_time` | TIME | Yes | `14:00` | Valid time of day; must differ from `start_time` | Local end time. Combined with `crosses_midnight`, fully determines the shift window |
| `crosses_midnight` | INTEGER | Yes | `0` | Default 0; must be 1 when `end_time` <= `start_time` | **Explicitly stored rather than inferred.** A night shift running 22:00 to 06:00 has an end time numerically lower than its start. Storing the flag removes the need for every consumer to re-derive the same comparison, and eliminates a recurring off-by-one-day bug |
| `shift_type` | TEXT + CHECK | Yes | `production` | One of: `production`, `general`, `maintenance_only` | Distinguishes rotating production shifts from the general day shift and from dedicated maintenance windows. Only `production` shifts count toward `plant.shifts_per_day` |
| `sequence_order` | INTEGER | Yes | `1` | Greater than 0; unique among production shifts | Rotation order of production shifts. Determines which shift follows which, needed for "next shift" reasoning in recovery plans |
| `is_production_shift` | INTEGER | Yes | `1` | Default 1 | Whether production output is expected. `0` for maintenance-only windows, which prevents the Monitoring Agent from raising low-output events during planned downtime |
| `break_duration_minutes` | INTEGER | No | `30` | Between 0 and 120 when present | Scheduled non-productive time. NULL when unstructured. Lets output expectations account for planned breaks rather than flagging them as anomalies |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `plant` | Many-to-one | Shifts are defined at plant level |
| Child | `worker` | One-to-many | A worker is assigned to a default shift |
| Child | `maintenance_team` | One-to-many | A team covers a shift |

**Business rules**

1. Every worker is assigned exactly one default shift. Rotation between shifts is operational scheduling and is not modelled in master data — that would be a roster, which no FactoryFlow AI component consumes.
2. `crosses_midnight` must be 1 whenever `end_time` is less than or equal to `start_time`. This is a check constraint, not a convention, because a mismatch here produces silently wrong shift-window arithmetic.
3. Active production shifts must together cover the plant's operating hours without gaps. Overlaps are permitted only for handover periods.
4. `sequence_order` is unique among production shifts and defines the rotation. The general shift is excluded from rotation ordering.
5. At least one shift must have `is_production_shift = 1`, otherwise the plant produces nothing and the simulator has no basis for generating output.
6. Shift times are local to `plant.timezone`. They are never stored as UTC, because a shift starts at 06:00 local regardless of daylight saving.
7. A shift cannot be soft-retired while active workers or maintenance teams are assigned to it.

**Example records**

| shift_code | shift_name | start_time | end_time | crosses_midnight | shift_type | sequence_order | is_production_shift | break_duration_minutes |
|---|---|---|---|---|---|---|---|---|
| `SH-A` | Morning Shift | 06:00 | 14:00 | 0 | `production` | 1 | 1 | 30 |
| `SH-B` | Afternoon Shift | 14:00 | 22:00 | 0 | `production` | 2 | 1 | 30 |
| `SH-C` | Night Shift | 22:00 | 06:00 | 1 | `production` | 3 | 1 | 45 |
| `SH-GEN` | General Day Shift | 09:00 | 18:00 | 0 | `general` | 4 | 0 | 60 |

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Generates shift-dependent behavior — output variation, changeover clustering at boundaries, reduced night-shift throughput — instead of a flat unrealistic stream |
| Monitoring Agent | Suppresses low-output events during non-production shifts and planned breaks, which removes a large source of false alerts |
| Supervisor Agent | Identifies the crew on duty and the next available maintenance window when assembling context |
| Decision Agent | Anchors trends to shift boundaries in its reasoning, and schedules recommended actions into real windows rather than abstract time |
| Notification Service | Routes by who is actually on duty, and applies quiet-hours policy correctly |
| Dashboard | Presents operational history segmented by shift, which is how plant performance is conventionally reviewed |

---

## Group B — Production Structure

What the plant makes, where it makes it, and how fast. This group supplies the production context that converts a technical machine risk into a business impact statement.

---

### 5. `production_line`

**Purpose**

Represents a sequenced group of machines that together produce finished output. It is the unit at which production is planned, output is measured, and business impact is assessed.

**Business description**

A production line is a chain of machines arranged in process sequence. Material enters at the first station and finished product leaves the last. The defining operational characteristic of a line is that **the machines are interdependent**: if one station stops, the line stops. Throughput is limited by the slowest station, and a failure anywhere halts everything downstream.

That interdependence is exactly why the line is the right unit for business impact. When the Prediction Agent flags Machine MC-0102, the question a manager actually cares about is not "what happens to that machine?" but "what happens to Line LN-01?" — because the line is what produces the output that fulfils the order.

`criticality` is the single most consequential attribute here. Two machines can carry identical failure probability while their lines differ completely in business importance. A 70 % risk on a critical line producing a Gold-tier customer's order outranks a 90 % risk on a standard line running to stock. Without line criticality, the platform can only rank by technical severity, which is precisely the business-blind behavior described in the problem statement.

`design_capacity_units_per_hour` gives the Decision Agent the arithmetic for lost output: capacity multiplied by expected downtime yields units lost, which combined with the line's downtime cost rule yields a monetary figure.

**Primary key**

`production_line_id` — surrogate integer, with `production_line_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `production_line_code` | VARCHAR(10) | Yes | `LN-01` | Unique; matches `^LN-[0-9]{2}$` | Shop-floor line identifier, used in every notification and recommendation |
| `plant_area_id` | INTEGER (FK) | Yes | `1` | References active `plant_area` with `area_type` in (`production`, `assembly`) | **Physical** location of the line. Determines ambient context and maintenance travel time |
| `department_id` | INTEGER (FK) | Yes | `1` | References active `department` | **Organizational** owner of the line. Determines escalation accountability |
| `line_name` | VARCHAR(120) | Yes | `Gearbox Housing Machining Line` | Non-empty | Descriptive name shown to users and used in AI-generated text |
| `line_type` | TEXT + CHECK | Yes | `machining` | One of: `machining`, `assembly`, `packaging`, `finishing`, `inspection` | Process character of the line. Groups lines for comparison and sets expectations about which machine categories appear on it |
| `criticality` | TEXT + CHECK | Yes | `critical` | One of: `critical`, `high`, `standard`, `low` | **Business importance of the line.** The primary input to prioritization when several risks compete. A critical line is one whose stoppage has immediate customer or revenue consequence |
| `design_capacity_units_per_hour` | NUMERIC(10,2) | Yes | `24.00` | Greater than 0 | Nameplate throughput. The basis for computing lost output from expected downtime |
| `station_count` | INTEGER | Yes | `3` | Greater than 0 | Number of process stations in the line sequence. Provides the valid range for `machine.line_position` and lets the Supervisor Agent reason about upstream and downstream reach |
| `target_oee_percent` | NUMERIC(5,2) | No | `82.00` | Between 0 and 100 when present | Target overall equipment effectiveness. NULL when the line is not formally targeted. Gives the dashboard a reference line and future reporting a benchmark |
| `changeover_time_minutes` | INTEGER | No | `45` | Greater than or equal to 0 when present | Typical time to switch the line between products. NULL for single-product lines. Affects whether rerouting production to another line is a realistic recovery option |
| `commissioned_date` | DATE | Yes | `2016-06-15` | On or after `plant.commissioned_date` | When the line entered service. Bounds the installation dates of its machines |

**Note on attributes deliberately absent.** `machine_count` is not stored — it is a count of active `machine` rows referencing the line, and storing it would create a value that drifts out of step with reality the first time a machine is added. `plant_id` is not stored either: it is reachable through `plant_area_id`. Both omissions follow the no-duplication principle, and both are worth stating because storing them is the more common instinct.

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `plant_area` | Many-to-one | The line is physically located in one area |
| Parent | `department` | Many-to-one | The line is owned by one department |
| Child | `machine` | One-to-many | A line contains many machines in sequence |
| Child | `product_line_capability` | One-to-many | A line is capable of producing several products |
| Child | `worker` | One-to-many | Workers may be assigned to a line (optional assignment) |
| Child | `business_rule` | One-to-many | Line-scoped rule overrides (optional scoping) |
| Child | `notification_recipient` | One-to-many | Recipients may be scoped to a single line |

**Two parents is intentional, not an error.** `plant_area_id` answers *where* and `department_id` answers *who owns it*. These are genuinely orthogonal in a real plant: the Machining Bay physically contains lines owned by Production, while Maintenance and Quality staff also work inside it. Forcing a single parent would require either collapsing physical and organizational structure into one hierarchy — which does not hold — or dropping one of two facts the platform needs.

**Business rules**

1. A production line belongs to exactly one plant area and exactly one department.
2. A line must be located in an area whose `area_type` is `production` or `assembly`.
3. `station_count` must be greater than or equal to the number of distinct `line_position` values among the line's active machines. A line cannot have more occupied positions than it has stations.
4. Every active line should have at least one active machine. A line with no machines produces nothing and would generate misleading capacity figures.
5. Every active line must have at least one active `product_line_capability` row. A line that cannot produce any product has no purpose, and its capacity figure would be meaningless.
6. `design_capacity_units_per_hour` is the nameplate figure, independent of which product is running. Product-specific achievable rates live on `product_line_capability`, because they genuinely differ per product.
7. Lines marked `criticality = 'critical'` are expected to have a line-scoped downtime cost `business_rule`. Without it, the Decision Agent can state that impact is high but cannot quantify it.
8. `commissioned_date` must be on or after the plant's commissioning date and on or before the installation date of its earliest machine.
9. A line cannot be soft-retired while it has active machines.

**Example records**

| production_line_code | line_name | plant_area | department | line_type | criticality | design_capacity_units_per_hour | station_count | target_oee_percent | changeover_time_minutes |
|---|---|---|---|---|---|---|---|---|---|
| `LN-01` | Gearbox Housing Machining Line | `AREA-MCH` | `DEP-PRD` | `machining` | `critical` | 24.00 | 3 | 82.00 | 45 |
| `LN-02` | Pump Assembly Line | `AREA-ASM` | `DEP-PRD` | `assembly` | `high` | 11.00 | 2 | 78.00 | 60 |
| `LN-03` | Valve Body Machining Line | `AREA-MCH` | `DEP-PRD` | `machining` | `standard` | 36.00 | 2 | 80.00 | 30 |
| `LN-04` | Packaging Line | `AREA-PKG` | `DEP-PRD` | `packaging` | `standard` | 120.00 | 1 | 88.00 | 15 |

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Generates output at a rate consistent with `design_capacity_units_per_hour` and applies changeover behavior between products |
| Monitoring Agent | Detects line-level conditions — a stopped upstream station starving downstream machines — which are invisible when looking at one machine at a time |
| Prediction Agent | Groups machine risk by line, so risk can be reported at the unit a manager acts on |
| Supervisor Agent | **Primary escalation input.** Combines `criticality` with predicted risk to decide whether a situation warrants LLM reasoning. This is where business importance enters the pipeline |
| Decision Agent | Computes lost output from capacity and downtime, states impact at line level, and evaluates whether rerouting to another capable line is viable using `changeover_time_minutes` |
| Notification Service | Routes to line-scoped recipients, so a supervisor receives alerts for their own line rather than the whole plant |
| Dashboard | The primary grouping for live factory state — managers think in lines, not individual machines |

---

### 6. `product`

**Purpose**

Represents a finished good the plant manufactures. Provides the commercial and material identity that connects machine risk to customer orders and revenue.

**Business description**

Products are what the factory sells. Each has a code, a name, a family, a selling price, and a material structure defined by its bill of materials.

Products are the bridge between the shop floor and the business. A machine failure by itself is a technical event. A machine failure that halts production of the gearbox housing promised to a Gold-tier customer next Tuesday is a business event. `product` is what makes the second framing possible.

`standard_selling_price` and `standard_material_cost` together yield contribution margin per unit, which is what converts "we will lose 60 units" into "we will lose a quantifiable amount of margin." That figure is what makes a recommendation persuasive to a manager who has to justify stopping a line.

`quality_criticality` captures a fact that pure throughput arithmetic misses: for some products, a degrading machine is a scrap risk before it is a downtime risk. A machine drifting out of tolerance while producing a safety-critical component is more urgent than the same drift on a cosmetic part, even though the failure probability is identical.

**Note on cycle time.** Cycle time is deliberately **not** an attribute of `product`. The same product runs at different rates on different lines, so a single product-level cycle time would be either wrong for most lines or a duplicate of the line-specific value. It lives exclusively on `product_line_capability`. See §7.

**Primary key**

`product_id` — surrogate integer, with `product_code` unique.

Product codes are revised by engineering change more often than almost any other master data code, which makes a surrogate key particularly valuable here. A revision updates one row instead of cascading through the bill of materials, line capabilities, and years of operational order history.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `product_code` | VARCHAR(20) | Yes | `PRD-GH-100` | Unique; matches `^PRD-[A-Z]{2,4}-[0-9]{3}$` | Commercial part number used on orders, drawings, and in customer communication |
| `product_name` | VARCHAR(150) | Yes | `Gearbox Housing GH-100` | Non-empty | Descriptive name shown to users and in AI-generated text |
| `product_family` | VARCHAR(80) | Yes | `Gearbox Components` | Non-empty | Grouping of related products. Lets the Decision Agent reason about substitution and grouped impact across a family |
| `unit_of_measure` | TEXT + CHECK | Yes | `EA` | One of: `EA`, `KG`, `L`, `M`, `SET` | Unit in which the product is counted. `EA` (each) for discrete parts. Ensures quantity arithmetic is dimensionally consistent |
| `standard_selling_price` | NUMERIC(12,2) | Yes | `4850.00` | Greater than 0 | Revenue per unit in `plant.currency_code`. Input to revenue-at-risk calculations |
| `standard_material_cost` | NUMERIC(12,2) | Yes | `2140.00` | Greater than 0; less than `standard_selling_price` | Material cost per unit. With selling price, yields contribution margin — the correct basis for expressing the value of lost output |
| `quality_criticality` | TEXT + CHECK | Yes | `high` | One of: `safety_critical`, `high`, `standard` | Consequence of a quality defect. **Raises urgency independently of downtime risk**: on a safety-critical product, machine drift is a scrap and liability risk, not merely a throughput risk |
| `target_scrap_rate_pct` | NUMERIC(5,2) | No | `1.50` | Between 0 and 100 when present | Acceptable scrap rate. NULL when not formally targeted. Gives the Monitoring Agent a baseline for distinguishing normal scrap from a developing process problem |
| `shelf_life_days` | INTEGER | No | `NULL` | Greater than 0 when present | Storage life for perishable output. NULL for durable engineered goods, which is the normal case here. Present because it changes whether building buffer stock ahead of planned downtime is a viable recovery option |
| `drawing_revision` | VARCHAR(12) | No | `Rev-C` | Non-empty when present | Current engineering drawing revision. NULL when not revision-controlled. Supports traceability and explains why cycle times or tolerances may have changed |
| `introduced_date` | DATE | Yes | `2019-02-11` | On or after `plant.commissioned_date` | When the product entered production. Bounds the validity of historical production data |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Child | `product_line_capability` | One-to-many | A product can be produced on several lines |
| Child | `bill_of_materials` | One-to-many | A product consumes several materials |
| Referenced by | Operational production orders | One-to-many | Orders reference the product being made (operational, out of scope here) |

`product` has **no outgoing foreign keys**. It is an independent reference entity, which places it at the base of the dependency graph alongside `plant`.

**Business rules**

1. **A product may only be produced on lines explicitly declared capable of producing it.** This is enforced by `product_line_capability` — capability is declared data, never assumed. A line and a product with no capability row cannot be paired, which prevents the simulator from generating physically impossible production and prevents the Decision Agent from recommending an infeasible reroute.
2. Every active product must have at least one active `product_line_capability` row. A product that cannot be made anywhere is not a real product.
3. Every active product must have at least one active `bill_of_materials` row. A product consuming no material is not manufactured.
4. Exactly one `production_route` capability row per product carries `is_primary_line = 1`. Without a designated primary, "where is this normally made" has no answer.
5. `standard_material_cost` must be less than `standard_selling_price`. A negative contribution margin indicates a data error, and it would invert the Decision Agent's impact reasoning.
6. `standard_material_cost` should be broadly consistent with the sum of its bill of materials lines valued at item unit cost. Enforced as a data quality check rather than a constraint, since the standard cost includes overhead that the material sum does not.
7. Products marked `safety_critical` are expected to have `target_scrap_rate_pct` populated. Quality consequence without a quality baseline cannot be assessed.
8. A product cannot be soft-retired while active capability rows or open operational orders reference it.

**Example records**

| product_code | product_name | product_family | uom | standard_selling_price | standard_material_cost | quality_criticality | target_scrap_rate_pct | drawing_revision | introduced_date |
|---|---|---|---|---|---|---|---|---|---|
| `PRD-GH-100` | Gearbox Housing GH-100 | Gearbox Components | `EA` | 4850.00 | 2140.00 | `high` | 1.50 | `Rev-C` | 2019-02-11 |
| `PRD-PMP-220` | Centrifugal Pump Assembly PMP-220 | Pump Assemblies | `EA` | 18600.00 | 9250.00 | `safety_critical` | 0.80 | `Rev-F` | 2018-07-30 |
| `PRD-VB-075` | Valve Body VB-075 | Valve Components | `EA` | 2310.00 | 985.00 | `standard` | 2.20 | `Rev-B` | 2020-11-05 |

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Determines what each line is producing and drives material consumption through the bill of materials |
| Monitoring Agent | Compares observed scrap against `target_scrap_rate_pct` to detect developing quality problems |
| Supervisor Agent | Assembles product context — what is running, what it is worth, how quality-critical it is |
| Decision Agent | **Core business impact input.** Converts lost units into lost margin, and uses `quality_criticality` to escalate scrap risk independently of downtime risk |
| Dashboard | Shows what each line is producing and the value at risk |
| Future reporting | Enables product-level and family-level downtime and loss analysis |

---

### 7. `product_line_capability`

**Purpose**

Resolves the many-to-many relationship between products and production lines, and carries the attributes that only exist for a specific product-line pairing: achievable cycle time, changeover cost, and whether the line is the primary route.

**Business description**

In a real plant, product-to-line assignment is neither one-to-one nor unconstrained. A product can usually run on more than one line, but not on every line — each line has particular machines, tooling, and fixtures. And the same product runs at genuinely different speeds on different lines, because the equipment differs.

This entity captures that reality. It is the answer to two questions the Decision Agent asks constantly:

**"Can this product be made somewhere else?"** When a line is about to stop, the most valuable recovery option is often rerouting to another capable line. Only declared capability makes that recommendation safe. Without it, the platform would either never suggest rerouting, or suggest routes that are physically impossible.

**"How much output are we actually losing?"** Lost units depend on the achievable rate of *this product* on *this line*, not on a generic line capacity.

`is_qualified` deserves particular attention. A line can be technically capable while not being *qualified* — the customer or a regulator has not approved production of that part on that line. This is routine in automotive and aerospace supply. Rerouting to a capable-but-unqualified line would produce unsellable parts. Recording qualification separately from capability prevents the Decision Agent from recommending a reroute that is engineering-feasible and commercially worthless.

**Primary key**

`product_line_capability_id` — surrogate integer, with a **composite unique constraint on (`product_id`, `production_line_id`)**.

The surrogate keeps references and application code simple. The composite unique constraint enforces the real rule: one capability declaration per product-line pair. A composite natural primary key was rejected because two-column foreign keys propagate awkwardly, and because a surrogate gives the row a stable identity if the pairing is ever re-created.

This entity has no business code. It is a relationship, not a thing a shop-floor operator names.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `product_id` | INTEGER (FK) | Yes | `1` | References active `product`; unique with `production_line_id` | The product this capability declaration covers |
| `production_line_id` | INTEGER (FK) | Yes | `1` | References active `production_line`; unique with `product_id` | The line capable of producing it |
| `capability_type` | TEXT + CHECK | Yes | `production_route` | One of: `production_route`, `finishing_stage` | **Whether this line makes the product or processes it afterwards.** A machining line is a `production_route`; a packaging line is a `finishing_stage` that handles output from elsewhere. Only `production_route` rows are reroute candidates |
| `is_primary_line` | INTEGER | Yes | `1` | Default 0; exactly one `1` per product among `production_route` rows | Whether this is the normal production route. Alternatives are fallbacks used for recovery. Always `0` for `finishing_stage` rows |
| `cycle_time_seconds` | NUMERIC(8,2) | Yes | `145.00` | Greater than 0 | **Achievable seconds per unit of this product on this line.** The authoritative rate figure — lives here and nowhere else, because it genuinely varies by line |
| `max_hourly_output_units` | NUMERIC(10,2) | Yes | `24.00` | Greater than 0; consistent with `cycle_time_seconds` | Sustainable hourly output including normal losses. Slightly below the theoretical figure derived from cycle time alone. Used directly in lost-output arithmetic |
| `changeover_minutes` | INTEGER | Yes | `45` | Greater than or equal to 0 | Time to switch this line onto this product. **Determines whether rerouting is worth recommending** — a two-hour changeover to avoid a one-hour stoppage is not a recovery plan |
| `is_qualified` | INTEGER | Yes | `1` | Default 0 | Whether the customer or regulator has approved this product on this line. **`0` blocks rerouting even when the line is technically capable**, because output would be unsellable |
| `qualification_expiry_date` | DATE | No | `2027-03-31` | Must be in the future when `is_qualified` is 1 | When approval lapses. NULL when qualification does not expire. Prevents recommending a route whose approval has quietly run out |
| `tooling_available` | INTEGER | Yes | `1` | Default 1 | Whether required tooling and fixtures are physically present on this line. `0` means the route needs a tooling transfer first, which adds time the recovery plan must account for |
| `effective_from_date` | DATE | Yes | `2019-03-01` | On or after `product.introduced_date` and `production_line.commissioned_date` | When this capability became valid. Keeps historical output figures interpretable against the rate that applied at the time |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `product` | Many-to-one | Each row covers one product |
| Parent | `production_line` | Many-to-one | Each row covers one line |

Together these resolve **product ↔ production_line as many-to-many**. This is one of only five genuine many-to-many relationships in the model, and it qualifies because it carries five attributes of its own that belong to neither parent.

**Business rules**

1. One capability row per product-line pair. Enforced by the composite unique constraint.
2. **Exactly one `production_route` row per product has `is_primary_line = 1.`** The primary route is where the product is normally made; the others exist for recovery. `finishing_stage` rows always carry `0`, because a packaging line is never an alternative to a machining line.
3. A product may only be produced on a line where an active capability row exists. This is the rule that keeps the simulator physically honest and keeps reroute recommendations feasible.
4. **Rerouting may only be recommended to a `production_route` line** where `is_qualified = 1`, `tooling_available = 1`, and `qualification_expiry_date` is either NULL or in the future. All four conditions must hold — capability alone is not sufficient, and a finishing stage is never a substitute for a production route.
5. `max_hourly_output_units` must be consistent with `cycle_time_seconds`, and normally slightly lower than the theoretical maximum, since sustained output includes minor stoppages and normal losses. A value above the theoretical maximum indicates a data error.
6. `cycle_time_seconds` on a non-primary line is typically higher than on the primary line, reflecting less suitable equipment. Not enforced, but a lower value on a fallback route warrants review of why it is not primary.
7. `changeover_minutes` may legitimately differ per direction in reality; this model stores a single representative figure, because direction-specific changeover matrices add a dimension no consumer uses.
8. A capability row cannot be soft-retired if it is the only active row for its product, or if it is the product's primary route while alternatives remain — retirement requires reassigning primary first.

**Example records**

| product | line | capability_type | is_primary_line | cycle_time_seconds | max_hourly_output_units | changeover_minutes | is_qualified | qualification_expiry_date | tooling_available |
|---|---|---|---|---|---|---|---|---|---|
| `PRD-GH-100` | `LN-01` | `production_route` | 1 | 145.00 | 24.00 | 45 | 1 | 2027-03-31 | 1 |
| `PRD-VB-075` | `LN-03` | `production_route` | 1 | 95.00 | 36.00 | 30 | 1 | NULL | 1 |
| `PRD-VB-075` | `LN-01` | `production_route` | 0 | 128.00 | 27.00 | 55 | 1 | 2026-09-30 | 1 |
| `PRD-PMP-220` | `LN-02` | `production_route` | 1 | 310.00 | 11.00 | 60 | 1 | 2028-01-15 | 1 |
| `PRD-GH-100` | `LN-04` | `finishing_stage` | 0 | 28.00 | 120.00 | 15 | 1 | NULL | 1 |
| `PRD-VB-075` | `LN-04` | `finishing_stage` | 0 | 22.00 | 150.00 | 15 | 1 | NULL | 1 |
| `PRD-PMP-220` | `LN-04` | `finishing_stage` | 0 | 45.00 | 76.00 | 20 | 1 | NULL | 1 |

Two things are worth reading out of this table.

**The third row is the recovery option.** Valve bodies normally run on LN-03, but LN-01 can also make them — slower, with a longer changeover, and with a qualification that expires in 2026. That single row is what allows the Decision Agent to say: *"LN-03 is at risk. Valve body production can be moved to LN-01 at 27 units per hour instead of 36, after a 55-minute changeover. Qualification is valid until September 2026."* That is a concrete, checkable recovery plan rather than a vague suggestion.

**The last three rows are the packaging stage, not alternative routes.** All three products pass through LN-04 after production, which is why they have capability rows there — but `capability_type = 'finishing_stage'` means the Decision Agent will never propose moving machining to the packaging line. Without that distinction, three perfectly valid rows would look like three viable reroute destinations, and the platform would eventually recommend one.

**Stated limitation: routing sequences are not modelled.** This entity records *which lines handle a product*, not *in what order*. A real MES would hold a routing with operation sequence numbers, and FactoryFlow AI deliberately does not: no consumer needs multi-stage routing, because risk and impact are assessed per line independently. `capability_type` captures the one distinction that does affect a recommendation — whether a line is a substitutable production route — and stops there. If sequence-aware impact analysis is ever needed, a routing entity is added then.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Uses `cycle_time_seconds` and `max_hourly_output_units` to generate realistic per-line output, and `changeover_minutes` to model changeover downtime |
| Monitoring Agent | Compares actual output against `max_hourly_output_units` to detect underperformance that raw machine telemetry does not reveal |
| Supervisor Agent | Assembles the set of alternative capable lines as part of the decision context |
| Decision Agent | **Recovery planning core.** Determines whether rerouting is feasible, at what rate, after what changeover, and under what qualification constraints. Also supplies the rate used to quantify lost output |
| Dashboard | Shows which products each line can run and at what rate |

**Design note.** This entity is a good illustration of the difference between a real many-to-many and a lazy one. A bare `(product_id, production_line_id)` pair would record only that a pairing is possible. The six additional attributes are what make the relationship useful: they let the platform answer *how fast*, *at what switching cost*, *with what approval*, *is the tooling even there*, and *is this line a substitute at all*. A recovery recommendation built without them would be a guess.

---

## Group C — Asset Hierarchy

The machines the platform actually watches, and the specifications that define what normal looks like. This is the most consequential group in the model: the Prediction Agent's features, the Monitoring Agent's checks, and the simulator's entire output all derive from it.

The hierarchy is three levels deep, and each level answers a different question:

```
machine_category          "What kind of equipment is this, broadly?"      →  MCAT-CNC
        ↓
machine_type              "What model is it, and what are its specs?"     →  MTY-VMC-500
        ↓
machine                   "Which physical asset, and where does it sit?"  →  MC-0101
```

Alongside the hierarchy sits the **parameter catalogue** — `machine_parameter` defines what can be measured, and `machine_type_parameter` declares which parameters each machine type actually exposes and what its healthy envelope is.

---

### 8. `machine_category`

**Purpose**

Groups machine types into broad equipment classes. Provides the coarse classification used for maintenance specialization routing, condition-monitoring policy, and reporting.

**Business description**

A plant's equipment falls into a handful of recognisable families: CNC machining centres, assembly automation, material handling, packaging equipment, inspection and metrology. Everyone on the shop floor uses these groupings without being asked to.

The category earns its place in the model by carrying two facts that hold true for the whole family and would otherwise be repeated on every machine type:

**Which maintenance specialization owns it.** A CNC machining centre is normally a mechanical team's responsibility; a robotic welder belongs to automation and controls; a conveyor is mechanical; a coordinate measuring machine is metrology-adjacent and typically handled by automation. `primary_maintenance_specialization` is what lets the Decision Agent suggest a *specific* team rather than "maintenance." Storing this at category level rather than per machine type keeps one fact in one place.

**Whether vibration monitoring is meaningful.** Rotating equipment degrades in ways vibration reveals. Static equipment does not. `is_rotating_equipment` tells the platform whether vibration is a legitimate diagnostic signal or noise, which affects both what the simulator generates and how the Monitoring Agent interprets it.

Without this level, both facts would be duplicated across every machine type in the family, and they would drift apart the first time someone edited one and not the others.

**Primary key**

`machine_category_id` — surrogate integer, with `machine_category_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_category_code` | VARCHAR(12) | Yes | `MCAT-CNC` | Unique; matches `^MCAT-[A-Z]{3}$` | Short category identifier used in reports and groupings |
| `category_name` | VARCHAR(80) | Yes | `CNC Machining` | Non-empty | Descriptive family name shown to users |
| `description` | TEXT | No | `Computer-controlled subtractive machining equipment` | Non-empty when present | Explanatory text. NULL when the name is self-evident. Included primarily as context for the Decision Agent, which reasons better with a sentence than with a code |
| `equipment_class` | TEXT + CHECK | Yes | `rotating` | One of: `rotating`, `robotic`, `conveying`, `static`, `metrology` | Physical operating character of the family. Determines which failure signatures are physically plausible |
| `primary_maintenance_specialization` | TEXT + CHECK | Yes | `mechanical` | One of: `mechanical`, `electrical`, `automation`, `general`; must match a value used by `maintenance_team.specialization` | **The default skill match for repairs.** Lets the Decision Agent suggest a specific qualified team rather than a generic maintenance referral |
| `is_rotating_equipment` | INTEGER | Yes | `1` | Default 0 | Whether the family contains rotating elements. **Determines whether vibration is a diagnostic signal or noise**, which affects simulation and monitoring interpretation alike |
| `requires_condition_monitoring` | INTEGER | Yes | `1` | Default 1 | Whether continuous condition monitoring is warranted. `0` for simple equipment where periodic inspection is the norm, which keeps the platform from over-monitoring assets that do not repay it |
| `typical_service_life_years` | INTEGER | No | `15` | Between 1 and 50 when present | Expected family service life. NULL when highly variable. Provides age context — a machine at 90 % of expected life warrants different interpretation from one at 20 % |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Child | `machine_type` | One-to-many | A category contains many machine types |

`machine_category` has **no outgoing foreign keys**. It is an independent lookup at the base of the dependency graph.

**Business rules**

1. A machine type belongs to exactly one category.
2. `primary_maintenance_specialization` must be a value that `maintenance_team.specialization` also uses. The two vocabularies are deliberately shared so the Decision Agent can match a failed machine to a qualified team by direct comparison rather than by an inference rule.
3. Vibration parameters (`PRM-VIB`) should only be declared on machine types whose category has `is_rotating_equipment = 1`. Vibration on static equipment is not a meaningful signal, and generating it would teach the ML model to fit noise.
4. Categories with `requires_condition_monitoring = 0` are expected to have fewer declared parameters and looser thresholds. The platform still tracks them for production context but does not attempt failure prediction.
5. A category cannot be soft-retired while active machine types reference it.
6. Categories are stable reference data. New categories are added when genuinely new equipment families arrive, not to accommodate a single unusual machine.

**Example records**

| machine_category_code | category_name | equipment_class | primary_maintenance_specialization | is_rotating_equipment | requires_condition_monitoring | typical_service_life_years |
|---|---|---|---|---|---|---|
| `MCAT-CNC` | CNC Machining | `rotating` | `mechanical` | 1 | 1 | 15 |
| `MCAT-ASM` | Assembly Automation | `robotic` | `automation` | 0 | 1 | 12 |
| `MCAT-MHL` | Material Handling | `conveying` | `mechanical` | 1 | 1 | 20 |
| `MCAT-PKG` | Packaging Equipment | `static` | `mechanical` | 0 | 1 | 10 |
| `MCAT-INS` | Inspection & Metrology | `metrology` | `automation` | 0 | 0 | 12 |

Note `MCAT-INS`: the coordinate measuring machine is tracked as an asset because it occupies a station on Line 01 and its unavailability blocks the line, but it is not a condition-monitoring target. That distinction is exactly what `requires_condition_monitoring` exists to express.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Uses `equipment_class` and `is_rotating_equipment` to generate physically plausible telemetry — vibration only where rotating elements exist |
| Monitoring Agent | Uses `requires_condition_monitoring` to decide which assets are prediction targets and which are tracked for context only |
| Prediction Agent | Groups machines by category for model scoping, so a single model is not asked to generalise across incompatible equipment physics |
| Decision Agent | Uses `primary_maintenance_specialization` to name the right team, and `typical_service_life_years` to frame age-related root causes |
| Dashboard | Groups equipment into families that shop-floor staff recognise |

---

### 9. `machine_type`

**Purpose**

Represents a specific machine model and its engineering specifications. This is where reliability characteristics live — mean time between failures, mean time to repair, rated power, design life — the numbers that turn a machine into something the platform can reason about quantitatively.

**Business description**

A machine type is a model, not an individual asset. Three identical vertical machining centres on the shop floor are three `machine` rows sharing one `machine_type` row. The specification is stated once.

Two attributes carry disproportionate weight in this model:

**`mtbf_hours` — mean time between failures.** This is the reliability baseline for the whole platform. The simulator uses it to set a realistic degradation rate, so machines fail at a believable frequency rather than randomly or never. The Prediction Agent uses it as context: a machine 400 hours into a 4,000-hour MTBF cycle is in a different risk posture from one at 3,900 hours, even with identical current readings.

**`mttr_minutes` — mean time to repair.** This is what converts a predicted failure into a downtime estimate, and a downtime estimate into a business impact figure. Without it, the Decision Agent can say a failure is likely but cannot say what it will cost, and the whole impact chain — downtime minutes, lost units, lost margin — has no starting number.

`design_life_hours` provides asset age context. `rated_power_kw` supports energy reasoning and gives the expected magnitude of the power-draw parameter. `requires_tooling` determines whether tool wear is a relevant signal at all: a machining centre consumes cutting tools, a conveyor does not.

**Primary key**

`machine_type_id` — surrogate integer, with `machine_type_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_type_code` | VARCHAR(24) | Yes | `MTY-VMC-500` | Unique; matches `^MTY-[A-Z0-9-]{3,20}$` | Model identifier used in specifications, spare part catalogues, and AI-generated text |
| `machine_category_id` | INTEGER (FK) | Yes | `1` | References active `machine_category` | The equipment family this model belongs to |
| `type_name` | VARCHAR(120) | Yes | `Vertical Machining Centre 500` | Non-empty | Full descriptive model name |
| `manufacturer` | VARCHAR(100) | Yes | `Meridian Machine Tools` | Non-empty | Equipment maker. Relevant for spare part sourcing and warranty claims |
| `model_number` | VARCHAR(60) | Yes | `VMC-500-XT` | Non-empty | Manufacturer's model designation, needed when ordering parts or requesting service |
| `rated_power_kw` | NUMERIC(8,2) | Yes | `18.50` | Greater than 0 | Nameplate electrical rating. Sets the expected magnitude of power draw and supports energy-anomaly interpretation |
| `design_life_hours` | INTEGER | Yes | `60000` | Greater than 0 | Total expected operating life. With age, gives a wear position: a machine at 85 % of design life carries different baseline risk |
| `mtbf_hours` | INTEGER | Yes | `4200` | Greater than 0; less than `design_life_hours` | **Mean time between failures.** The reliability baseline used by the simulator to set degradation rate and by the Prediction Agent as risk context |
| `mttr_minutes` | INTEGER | Yes | `240` | Greater than 0 | **Mean time to repair.** Converts a predicted failure into a downtime estimate, which is the first number in the entire business impact chain |
| `requires_tooling` | INTEGER | Yes | `1` | Default 0 | Whether the machine consumes cutting or forming tooling. **Determines whether tool wear is a meaningful parameter** for this type |
| `control_system` | VARCHAR(80) | No | `Meridian CNC Series 8` | Non-empty when present | Controller family. NULL for equipment without a programmable controller. Tells the Decision Agent whether an automation specialist is needed alongside a mechanical technician |
| `min_operators_required` | INTEGER | Yes | `1` | Between 0 and 5 | Operators needed to run the machine. 0 for fully automatic equipment. Lets the Supervisor Agent check whether a staffing shortfall, not a machine fault, explains reduced output |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `machine_category` | Many-to-one | Every type belongs to one category |
| Child | `machine` | One-to-many | Many physical machines share one type |
| Child | `machine_type_parameter` | One-to-many | A type exposes several monitorable parameters |
| Child | `machine_type_failure_mode` | One-to-many | A type has several plausible failure modes |
| Child | `alert_threshold_profile` | One-to-many | Profiles are authored for a specific type |

**Business rules**

1. Every machine type belongs to exactly one category.
2. `mtbf_hours` must be less than `design_life_hours`. A machine is expected to fail and be repaired many times within its service life; MTBF exceeding design life would mean the machine never fails, which no real equipment does.
3. Machine types with `requires_tooling = 1` are expected to declare a tool wear parameter in `machine_type_parameter`. Types with `requires_tooling = 0` must not declare one — tool wear on a conveyor is meaningless and would pollute the ML feature set.
4. Every machine type used by an active monitored machine must have at least one active `machine_type_parameter` row. A monitored machine with no declared parameters gives the simulator nothing to emit.
5. Every machine type should have at least one `machine_type_failure_mode` row. Without plausible failure modes, the Decision Agent has no controlled vocabulary from which to form a root-cause hypothesis.
6. Vibration parameters may only be declared for types whose category has `is_rotating_equipment = 1`.
7. Specifications are engineering facts and are not edited to reflect observed behavior. If a machine consistently outperforms or underperforms its type specification, that is an operational finding recorded against the machine, not a rewrite of the type.
8. A machine type cannot be soft-retired while active machines reference it.

**Example records**

| machine_type_code | type_name | category | manufacturer | model_number | rated_power_kw | design_life_hours | mtbf_hours | mttr_minutes | requires_tooling | min_operators_required |
|---|---|---|---|---|---|---|---|---|---|---|
| `MTY-VMC-500` | Vertical Machining Centre 500 | `MCAT-CNC` | Meridian Machine Tools | `VMC-500-XT` | 18.50 | 60000 | 4200 | 240 | 1 | 1 |
| `MTY-CNC-LATHE-200` | CNC Turning Centre 200 | `MCAT-CNC` | Meridian Machine Tools | `TC-200-S` | 15.00 | 55000 | 3800 | 210 | 1 | 1 |
| `MTY-CMM-BRIDGE` | Bridge Coordinate Measuring Machine | `MCAT-INS` | Kestrel Metrology | `BCM-900` | 3.20 | 70000 | 9500 | 300 | 0 | 1 |
| `MTY-ROBO-WELD-6X` | Six-Axis Robotic Welding Cell | `MCAT-ASM` | Aravalli Automation | `RW-6X-220` | 22.00 | 48000 | 3200 | 180 | 0 | 0 |
| `MTY-CONV-BELT-12` | Belt Conveyor 12 m | `MCAT-MHL` | Beltrix Conveyors | `BC-12-HD` | 4.50 | 80000 | 11000 | 90 | 0 | 0 |
| `MTY-CARTON-SEAL-3` | Carton Sealing Unit 3 | `MCAT-PKG` | SealPro Systems | `CS-3-AUTO` | 2.80 | 45000 | 6400 | 60 | 0 | 1 |

Reading the reliability figures across the table tells a coherent story. The robotic welding cell has the shortest MTBF (3,200 hours) but a fast repair (180 minutes). The conveyor almost never fails (11,000 hours) and is quick to fix (90 minutes). The machining centre sits in between on failure frequency but is the most expensive to repair (240 minutes). Those relationships are what make simulated failures and predicted impacts believable rather than arbitrary.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Uses `mtbf_hours` to set degradation rate, `rated_power_kw` to scale power draw, and `requires_tooling` to decide whether tool wear accumulates |
| Monitoring Agent | Interprets readings against type specifications rather than universal constants |
| Prediction Agent | Uses type specifications as model features and scopes models by type, so equipment with different physics is not forced into one model |
| Supervisor Agent | Uses `mttr_minutes` to estimate downtime and `min_operators_required` to rule out staffing as an alternative explanation |
| Decision Agent | **`mttr_minutes` is the entry point to the impact chain:** downtime minutes → lost units → lost margin. `control_system` and the category's specialization together determine which skills the repair needs |
| Dashboard | Displays machine specifications alongside live state |

---

### 10. `machine`

**Purpose**

Represents an individual physical machine on the shop floor. This is the core asset of the platform — the subject of every telemetry reading, every detected event, every failure prediction, and most recommendations.

**Business description**

A machine is a specific physical asset with a serial number, an installation date, a position on a production line, and a monitoring configuration. It is the entity the factory manager thinks about when something goes wrong, and the entity every operational record points to.

Three attributes deserve individual attention because they carry most of the analytical weight.

**`line_position`** places the machine in the process sequence. This is what makes cascade reasoning possible. If the machine at position 1 stops, positions 2 and 3 starve. If position 3 stops, positions 1 and 2 accumulate work-in-progress until buffers fill and then stop too. Without an explicit sequence, the platform could only report "a machine on this line is at risk"; with it, the Decision Agent can state which stations are affected and in what order.

**`is_bottleneck`** identifies the line's constraint. This distinction has immediate financial consequence: when the bottleneck stops, the line loses output at full rate. When a non-bottleneck station stops, buffers and spare capacity may absorb part of the loss. Two machines with identical failure probability produce genuinely different impacts depending on this one flag, and it is not derivable from the other data.

**`downstream_buffer_units`** quantifies the grace period. If 18 units sit in the buffer after this station and the line runs at 24 units per hour, downstream has roughly 45 minutes of work before it starves. That converts "the line will stop" into "the line will stop in about 45 minutes unless we act," which is the difference between an alarm and a usable warning.

**`lifecycle_status` is asset state, not operational state.** It records whether the asset is part of the working factory — in service, on standby, undergoing overhaul, or decommissioned. It changes a handful of times across a machine's life. Whether the machine is *currently running, idle, or down* is operational data and lives nowhere in this document. Conflating the two is a common modelling error and it would put a high-frequency column in a low-frequency table.

**Attributes deliberately absent.** `accumulated_operating_hours` is operational — it increments continuously and belongs with telemetry. `current_status` is operational. `last_maintenance_date` is derived from operational maintenance history. `plant_area_id` is reachable through `production_line_id` and is therefore not stored: duplicating it would allow a machine to claim one area while its line sits in another.

**Primary key**

`machine_id` — surrogate integer, with `machine_code` unique.

The surrogate key matters more here than anywhere else in the model. Every operational telemetry row, event, prediction, and recommendation references `machine_id`. Those references accumulate into the millions, and they must survive a machine being renumbered, relocated to another line, or reassigned a new asset tag. A natural key would make any of those routine changes a mass update across historical data — and would put the audit trail at risk, which the platform's explainability guarantee depends on.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_code` | VARCHAR(12) | Yes | `MC-0101` | Unique; matches `^MC-[0-9]{4}$` | Shop-floor asset identifier. Appears in every notification and recommendation, and is how operators refer to the machine |
| `machine_type_id` | INTEGER (FK) | Yes | `1` | References active `machine_type` | The model this asset is, supplying all engineering specifications |
| `production_line_id` | INTEGER (FK) | Yes | `1` | References active `production_line` | The line this machine belongs to. **A machine belongs to exactly one line** |
| `line_position` | INTEGER | Yes | `1` | Greater than 0; at most `production_line.station_count`; unique within the line | **Process sequence position.** Enables upstream and downstream cascade reasoning |
| `alert_threshold_profile_id` | INTEGER (FK) | No | `2` | References active `alert_threshold_profile` whose `machine_type_id` matches this machine's type; required when `is_monitored` is 1 | The monitoring policy applied to this machine. NULL only for unmonitored assets. See §27 |
| `machine_name` | VARCHAR(120) | Yes | `Housing Rough Mill` | Non-empty | Functional name describing what the machine does on its line. More meaningful to a manager than the model name |
| `serial_number` | VARCHAR(60) | Yes | `MMT-VMC-2019-4471` | Unique; non-empty | Manufacturer serial number. Required for warranty claims and parts ordering |
| `asset_tag` | VARCHAR(30) | No | `FA-004471` | Unique when present | Finance asset register tag. NULL when not capitalised separately. Links the machine to depreciation and capital records for future reporting |
| `installation_date` | DATE | Yes | `2019-05-20` | On or after `production_line.commissioned_date` | When the machine was physically installed |
| `commissioned_date` | DATE | Yes | `2019-06-03` | On or after `installation_date` | When it entered production. The start of its operating life, and the anchor for age calculations |
| `warranty_expiry_date` | DATE | No | `2022-06-03` | On or after `commissioned_date` when present | When manufacturer warranty ends. NULL when out of warranty or unwarranted. **Changes the recommended action**: an in-warranty failure should route to the manufacturer, not to internal repair |
| `criticality` | TEXT + CHECK | Yes | `critical` | One of: `critical`, `high`, `standard`, `low` | Machine-level business importance. Combined with line criticality during prioritization. A machine can be critical on a standard line, or routine on a critical one |
| `is_bottleneck` | INTEGER | Yes | `1` | Default 0; at most one `1` per line | **Whether this station constrains line throughput.** Determines whether a stoppage costs full output or partial |
| `downstream_buffer_units` | INTEGER | No | `18` | Greater than or equal to 0 when present | Work-in-progress buffer after this station. NULL when there is no buffer. **Converts a stoppage into a grace period** the recovery plan can use |
| `rated_capacity_units_per_hour` | NUMERIC(10,2) | No | `26.00` | Greater than 0 when present | Station throughput. NULL for non-producing stations such as inspection. Identifies the true constraint when it differs from the assumed bottleneck |
| `lifecycle_status` | TEXT + CHECK | Yes | `in_service` | One of: `in_service`, `standby`, `under_overhaul`, `decommissioned` | **Asset lifecycle state, not operational run state.** Only `in_service` machines are monitored and predicted. Replaces the standard `is_active` flag for this entity |
| `is_monitored` | INTEGER | Yes | `1` | Default 1 | Whether telemetry is collected from this asset. `0` for machines tracked for production context but without instrumentation. Lets a brownfield reality be represented without deleting the asset |
| `installed_position_notes` | TEXT | No | `NULL` | — | Free-text siting notes, such as access constraints. NULL normally. Occasionally decisive for response feasibility — a machine reachable only by removing a guard takes longer to service |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `machine_type` | Many-to-one | Many machines share one model specification |
| Parent | `production_line` | Many-to-one | Every machine belongs to exactly one line |
| Parent | `alert_threshold_profile` | Many-to-one | Many machines can share one monitoring policy |
| Child | `machine_maintenance_schedule` | One-to-many | A machine has several planned maintenance definitions |
| Referenced by | Operational telemetry, events, predictions, recommendations | One-to-many | The primary target of all operational data (out of scope here) |

**Business rules**

1. **A machine belongs to exactly one production line.** Shared machines serving two lines are not modelled — it would turn a clean many-to-one into a many-to-many that no consumer needs, and it would make line-level impact ambiguous.
2. `line_position` is unique within a line. Two machines cannot occupy the same station.
3. `line_position` must not exceed the line's `station_count`.
4. **At most one machine per line has `is_bottleneck = 1`.** A line has one constraint by definition; two would make impact arithmetic contradictory.
5. `alert_threshold_profile_id` is required when `is_monitored = 1` and the machine's category has `requires_condition_monitoring = 1`. A monitored machine without thresholds cannot be monitored in practice.
6. The assigned threshold profile must belong to the same `machine_type_id` as the machine. Applying a conveyor profile to a machining centre would produce meaningless limits, so the constraint is enforced rather than trusted.
7. Only machines with `lifecycle_status = 'in_service'` and `is_monitored = 1` are eligible for monitoring and prediction. Decommissioned machines are excluded from the pipeline entirely while remaining fully present in historical records.
8. `installation_date` must be on or after the line's commissioning date, and `commissioned_date` on or after `installation_date`.
9. Machines are **never hard-deleted.** Decommissioning sets `lifecycle_status = 'decommissioned'`. Years of telemetry, events, predictions, and recommendations reference the machine, and deleting it would destroy the audit trail the explainability guarantee rests on.
10. `plant_area_id` is not stored. Physical location is derived through the production line, which guarantees a machine can never claim to be in a different area from its own line.
11. A machine with `rated_capacity_units_per_hour` below the line's `design_capacity_units_per_hour` is the de facto constraint and should normally be the one flagged `is_bottleneck`. Enforced as a data quality check, since a line may be constrained by tooling or staffing rather than machine capacity.

**Example records**

| machine_code | machine_name | type | line | pos | criticality | is_bottleneck | downstream_buffer_units | rated_capacity | lifecycle_status | is_monitored | threshold_profile |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `MC-0101` | Housing Rough Mill | `MTY-VMC-500` | `LN-01` | 1 | `critical` | 1 | 18 | 26.00 | `in_service` | 1 | `ATP-VMC-TIGHT` |
| `MC-0102` | Housing Finish Turn | `MTY-CNC-LATHE-200` | `LN-01` | 2 | `high` | 0 | 12 | 31.00 | `in_service` | 1 | `ATP-LATHE-STD` |
| `MC-0103` | Housing Final Inspection | `MTY-CMM-BRIDGE` | `LN-01` | 3 | `standard` | 0 | 0 | NULL | `in_service` | 0 | NULL |
| `MC-0201` | Pump Body Weld Cell | `MTY-ROBO-WELD-6X` | `LN-02` | 1 | `critical` | 1 | 6 | 12.00 | `in_service` | 1 | `ATP-ROBO-STD` |
| `MC-0202` | Assembly Transfer Conveyor | `MTY-CONV-BELT-12` | `LN-02` | 2 | `standard` | 0 | 0 | 40.00 | `in_service` | 1 | `ATP-CONV-STD` |
| `MC-0301` | Valve Body Turn | `MTY-CNC-LATHE-200` | `LN-03` | 1 | `high` | 1 | 24 | 38.00 | `in_service` | 1 | `ATP-LATHE-STD` |
| `MC-0302` | Valve Body Mill | `MTY-VMC-500` | `LN-03` | 2 | `standard` | 0 | 0 | 44.00 | `in_service` | 1 | `ATP-VMC-STD` |
| `MC-0401` | Carton Seal Unit | `MTY-CARTON-SEAL-3` | `LN-04` | 1 | `standard` | 1 | 0 | 130.00 | `in_service` | 1 | `ATP-SEAL-STD` |

Several design points are visible in this data:

- **MC-0103** is `in_service` but `is_monitored = 0`, so it carries no threshold profile. It occupies station 3 on Line 01 and its unavailability would block the line, so it must exist as an asset — but it is not a prediction target. This is why `is_monitored` and `lifecycle_status` are separate flags.
- **MC-0101 and MC-0302** are the same machine type running different threshold profiles: the tighter `ATP-VMC-TIGHT` on the critical line, the standard profile on the standard line. Same equipment, different monitoring policy, because the business consequence differs.
- **MC-0101** is the bottleneck on Line 01 with an 18-unit downstream buffer. At the line's 24 units per hour, that is roughly 45 minutes of grace — a concrete number the Decision Agent can put into a recovery plan.
- **MC-0102** has higher rated capacity (31) than the bottleneck (26), which is consistent with MC-0101 being the true constraint.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | The subject of generation. Emits telemetry per machine, scaled by type specifications and modulated by age since `commissioned_date` |
| Operational Database | Foreign key target for every telemetry reading, event, prediction, and recommendation |
| Monitoring Agent | Evaluates each monitored machine against its assigned threshold profile |
| Prediction Agent | **The prediction subject.** Produces a failure probability and risk classification per machine |
| Supervisor Agent | Uses `line_position`, `is_bottleneck`, and `downstream_buffer_units` to assess cascade impact and compute the grace period before the line starves |
| Decision Agent | Names the machine, states which stations are affected, uses `warranty_expiry_date` to choose between manufacturer service and internal repair, and uses the buffer to set the deadline for action |
| Notification Service | Includes `machine_code` and `machine_name` so recipients know exactly which asset is involved |
| Dashboard | The primary object displayed, arranged by line and position |

---

### 11. `machine_parameter`

**Purpose**

Defines the controlled vocabulary of measurable machine parameters. It is the shared dictionary that the simulator, the Monitoring Agent, and the Prediction Agent all read, so that "spindle temperature" means exactly one thing across the whole platform.

**Business description**

This entity is the catalogue of *what can be measured*: temperature, rotational speed, torque, tool wear, vibration, power draw, air pressure. For each it records the unit, the physically possible range, the direction in which degradation moves the value, and whether the value accumulates.

**This is master data, and the distinction matters.** The project brief excludes temperature, RPM, torque, and tool wear from master data — and that exclusion applies to *readings*. This entity stores no readings. It stores the definition:

- *"Spindle temperature is measured in °C, physically ranges 0–150, rises as degradation advances, and does not accumulate"* → definition, master data, this entity.
- *"MC-0101 reported 84.1 °C at 14:32:05"* → reading, operational data, excluded from this document.

Without this catalogue, the parameter vocabulary would be hardcoded in three places — the simulator, the Monitoring Agent, and the ML feature pipeline — and they would drift apart. A shared dictionary is what keeps them consistent.

Two attributes are worth calling out.

**`is_cumulative`** separates parameters that accumulate from those that fluctuate. Tool wear climbs monotonically and resets to zero at tool change. Temperature rises and falls continuously. This single flag changes how the simulator generates the value, how the Monitoring Agent interprets a rise, and how the ML pipeline engineers the feature — a rate of change means something quite different for a cumulative parameter than for an oscillating one.

**`degradation_direction`** records which way is worse. Temperature rising is bad. Air pressure *falling* is bad. Without this, the platform would need direction hardcoded per parameter, and a threshold rule could not be validated for sensible orientation.

**Primary key**

`machine_parameter_id` — surrogate integer, with `machine_parameter_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_parameter_code` | VARCHAR(12) | Yes | `PRM-TEMP` | Unique; matches `^PRM-[A-Z]{3,8}$` | Stable parameter identifier used by the simulator, threshold rules, and ML feature names |
| `parameter_name` | VARCHAR(80) | Yes | `Spindle Temperature` | Non-empty | Human-readable name. Appears verbatim in recommendations, so it must read naturally to a factory manager |
| `unit_of_measure` | VARCHAR(16) | Yes | `°C` | Non-empty | Measurement unit. **Essential for AI reasoning** — a Decision Agent that reports a bare number invites misinterpretation |
| `measurement_domain` | TEXT + CHECK | Yes | `thermal` | One of: `thermal`, `mechanical`, `electrical`, `tooling`, `pneumatic`, `hydraulic`, `positional` | Physical domain of the measurement. Lets the Decision Agent group correlated signals — several mechanical parameters drifting together points to one mechanical cause |
| `data_type` | TEXT + CHECK | Yes | `numeric_continuous` | One of: `numeric_continuous`, `numeric_integer`, `boolean` | Value shape. Determines valid statistical treatment and how the simulator generates it |
| `physical_min` | NUMERIC(12,4) | Yes | `0.0000` | Less than `physical_max` | Lowest physically possible value. **Validation floor** — a reading below this is a sensor fault, not a machine fault, and must not be fed to the model as real |
| `physical_max` | NUMERIC(12,4) | Yes | `150.0000` | Greater than `physical_min` | Highest physically possible value. Validation ceiling, same reasoning |
| `degradation_direction` | TEXT + CHECK | Yes | `increasing` | One of: `increasing`, `decreasing`, `bidirectional` | **Which direction indicates deterioration.** Temperature increasing is bad; air pressure decreasing is bad. Lets threshold orientation be validated instead of assumed |
| `is_cumulative` | INTEGER | Yes | `0` | Default 0 | Whether the value accumulates and resets at maintenance. `1` for tool wear, `0` for temperature. **Changes generation, interpretation, and feature engineering** |
| `description` | TEXT | No | `Spindle bearing housing temperature measured at the front bearing` | Non-empty when present | Precise definition of what is measured and where. NULL when self-evident. Provides the Decision Agent with the physical context to form a specific root-cause hypothesis rather than a generic one |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Child | `machine_type_parameter` | One-to-many | A parameter is declared on several machine types |
| Child | `alert_threshold_rule` | One-to-many | A parameter has threshold rules in several profiles |

`machine_parameter` has **no outgoing foreign keys**. It is an independent lookup at the base of the dependency graph.

**Business rules**

1. Parameter codes are permanent once operational data references them. Renaming a code would orphan historical telemetry and break the traceability chain from a recommendation back to its evidence.
2. `physical_min` must be less than `physical_max`. These bounds are sensor and physics limits, deliberately wider than any alert threshold.
3. Readings outside the physical range indicate instrumentation failure, not machine failure. They are recorded and flagged as data quality issues, never passed to the Prediction Agent as valid input. This is what prevents a faulty sensor from producing a confident wrong prediction.
4. `is_cumulative = 1` requires `degradation_direction = 'increasing'`. A quantity that accumulates can only accumulate upward.
5. `unit_of_measure` is mandatory and never blank. Every value the platform surfaces to a human or an LLM carries its unit.
6. New parameters are added to the catalogue, not encoded as variants of existing ones. Two parameters measuring different physical things must be two rows.
7. A parameter cannot be soft-retired while active `machine_type_parameter` or `alert_threshold_rule` rows reference it.

**Example records**

| machine_parameter_code | parameter_name | unit | measurement_domain | data_type | physical_min | physical_max | degradation_direction | is_cumulative |
|---|---|---|---|---|---|---|---|---|
| `PRM-TEMP` | Spindle Temperature | `°C` | `thermal` | `numeric_continuous` | 0.0000 | 150.0000 | `increasing` | 0 |
| `PRM-RPM` | Rotational Speed | `rpm` | `mechanical` | `numeric_integer` | 0.0000 | 15000.0000 | `bidirectional` | 0 |
| `PRM-TORQ` | Spindle Torque | `Nm` | `mechanical` | `numeric_continuous` | 0.0000 | 200.0000 | `increasing` | 0 |
| `PRM-TWEAR` | Tool Wear | `%` | `tooling` | `numeric_continuous` | 0.0000 | 100.0000 | `increasing` | 1 |
| `PRM-VIB` | Vibration Velocity | `mm/s` | `mechanical` | `numeric_continuous` | 0.0000 | 50.0000 | `increasing` | 0 |
| `PRM-PWR` | Power Draw | `kW` | `electrical` | `numeric_continuous` | 0.0000 | 60.0000 | `bidirectional` | 0 |
| `PRM-AIRP` | Compressed Air Pressure | `bar` | `pneumatic` | `numeric_continuous` | 0.0000 | 12.0000 | `decreasing` | 0 |

Note the three different `degradation_direction` values. Temperature and vibration are bad when rising. Air pressure is bad when falling. Rotational speed and power draw are bad in *either* direction — a spindle running slow suggests load or drive trouble, running fast suggests a control fault. Encoding this as data rather than logic means the Monitoring Agent applies one generic rule across all parameters instead of a special case per parameter.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Reads the catalogue to know what to emit, in what unit, within what physical bounds, and whether the value accumulates |
| Operational Database | Provides the foreign key target that gives every telemetry reading a typed, unit-aware definition |
| Monitoring Agent | Applies `degradation_direction` generically instead of hardcoding per-parameter logic, and uses physical bounds to reject sensor faults before they reach prediction |
| Prediction Agent | Supplies the feature vocabulary, so feature names are data-driven rather than hardcoded strings |
| Decision Agent | Uses `parameter_name`, `unit_of_measure`, and `description` to describe evidence in language a manager can verify, and `measurement_domain` to group correlated signals into one hypothesis |
| Dashboard | Labels charts correctly with names and units |

---

### 12. `machine_type_parameter`

**Purpose**

Declares which parameters each machine type actually exposes, and what the healthy operating envelope is for each. This is the specification the simulator generates against and the Prediction Agent draws its feature set from.

**Business description**

Not every machine measures everything. A machining centre reports spindle temperature, speed, torque, tool wear, and vibration. A conveyor reports speed and power draw and has no tooling at all. Declaring the applicable parameters per machine type is what keeps the platform from generating meaningless data and from feeding empty features to a model.

Beyond applicability, this entity carries the **healthy operating envelope**: the nominal value and the normal minimum and maximum for a machine of this type running correctly. That envelope is the simulator's baseline — it generates values inside the envelope for a healthy machine and drifts them outward as degradation advances.

The envelope is deliberately distinct from alert thresholds, and the distinction is worth being precise about:

| | `machine_type_parameter` envelope | `alert_threshold_rule` limits |
|---|---|---|
| **Nature** | Engineering fact — how this equipment behaves when healthy | Operations policy — when we want to be told |
| **Source** | Machine specification and commissioning data | Set and tuned by the maintenance and production teams |
| **Changes** | Almost never; changing it means the specification was wrong | Regularly, as teams tune sensitivity against alert fatigue |
| **Consumer** | Simulator generation, ML feature scaling | Monitoring Agent event detection |
| **Example** | A healthy VMC-500 runs 45–72 °C | Warn above 72 °C, critical above 82 °C |

Collapsing the two into one set of numbers would mean that retuning an alert threshold silently changes what the simulator considers healthy — which would make the whole pipeline circular and untestable.

**`is_ml_feature`** determines the model's input set. Not every monitored parameter is a useful predictor: rotational speed on a conveyor is a control setpoint rather than a health signal. Marking features as data rather than in code means the feature set is inspectable and adjustable without touching the training pipeline.

**Primary key**

`machine_type_parameter_id` — surrogate integer, with a **composite unique constraint on (`machine_type_id`, `machine_parameter_id`)**.

One declaration per type-parameter pair. The surrogate keeps child references simple; the composite unique constraint enforces the real rule.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_type_id` | INTEGER (FK) | Yes | `1` | References active `machine_type`; unique with `machine_parameter_id` | The machine model this declaration applies to |
| `machine_parameter_id` | INTEGER (FK) | Yes | `1` | References active `machine_parameter`; unique with `machine_type_id` | The parameter being declared |
| `nominal_value` | NUMERIC(12,4) | Yes | `58.0000` | Between `normal_min` and `normal_max` | Typical value for a healthy machine of this type. The simulator's centre of generation |
| `normal_min` | NUMERIC(12,4) | Yes | `45.0000` | At least `machine_parameter.physical_min`; less than `normal_max` | Lower bound of healthy operation. **Not an alert limit** — the envelope of correct behavior |
| `normal_max` | NUMERIC(12,4) | Yes | `72.0000` | At most `machine_parameter.physical_max`; greater than `normal_min` | Upper bound of healthy operation |
| `sampling_interval_seconds` | INTEGER | Yes | `10` | Between 1 and 3600 | How often the parameter is read. Fast-moving signals sample more often than slow ones. Drives simulator emission frequency and sets the resolution available to the ML model |
| `is_ml_feature` | INTEGER | Yes | `1` | Default 1 | Whether this parameter feeds the failure prediction model. **Defines the model's input set as data**, so it can be inspected and changed without editing the training pipeline |
| `expected_drift_direction` | TEXT + CHECK | Yes | `increasing` | One of: `increasing`, `decreasing`, `none`; consistent with `machine_parameter.degradation_direction` | How the value moves as *this type* degrades. Specifies the degradation signature the simulator reproduces and the model learns |
| `sensor_accuracy_pct` | NUMERIC(5,2) | No | `1.50` | Between 0 and 25 when present | Measurement uncertainty as a percentage of reading. NULL when unspecified. **Separates real drift from instrument noise** — a 1 °C move on a sensor with ±1.5 % accuracy is not a signal |
| `criticality_weight` | NUMERIC(4,2) | No | `1.00` | Between 0 and 5 when present | Relative diagnostic importance of this parameter for this type. NULL means unweighted. Lets the Decision Agent emphasise the most informative signal when several drift at once |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `machine_type` | Many-to-one | Each row declares a parameter for one type |
| Parent | `machine_parameter` | Many-to-one | Each row declares one parameter |

Together these resolve **machine_type ↔ machine_parameter as many-to-many**, carrying eight attributes that belong to neither parent.

**Business rules**

1. One declaration per machine type and parameter pair.
2. `normal_min` and `normal_max` must sit inside the parameter's physical range. The healthy envelope is necessarily narrower than what is physically possible.
3. `nominal_value` must fall within the normal envelope. A nominal outside its own healthy range is a data error.
4. The envelope is narrower than the corresponding alert thresholds, and the alert thresholds are narrower than the physical range. The ordering is: `physical_min` ≤ `critical_low` ≤ `warning_low` ≤ `normal_min` ≤ `nominal_value` ≤ `normal_max` ≤ `warning_high` ≤ `critical_high` ≤ `physical_max`. A violation of this ordering means healthy operation would trigger alerts, which is the single most common configuration error in condition monitoring.
5. Tool wear (`PRM-TWEAR`) may only be declared for machine types with `requires_tooling = 1`.
6. Vibration (`PRM-VIB`) may only be declared for types whose category has `is_rotating_equipment = 1`.
7. `expected_drift_direction` must be consistent with the parameter's `degradation_direction`. A parameter defined as degrading upward cannot drift downward on a specific type.
8. Every monitored machine type must declare at least one parameter with `is_ml_feature = 1`. A prediction target with no features cannot be predicted.
9. Parameters with `data_type = 'boolean'` do not carry a meaningful envelope; `nominal_value`, `normal_min`, and `normal_max` are set to 0 or 1 by convention and the envelope check is skipped.
10. A declaration cannot be soft-retired while an `alert_threshold_rule` exists for the same type and parameter combination. Removing the specification while the policy remains would leave a rule monitoring a parameter the machine no longer reports.

**Example records**

Declarations for `MTY-VMC-500` (vertical machining centre):

| machine_parameter | nominal_value | normal_min | normal_max | sampling_interval_seconds | is_ml_feature | expected_drift_direction | sensor_accuracy_pct | criticality_weight |
|---|---|---|---|---|---|---|---|---|
| `PRM-TEMP` | 58.0000 | 45.0000 | 72.0000 | 10 | 1 | `increasing` | 1.50 | 1.40 |
| `PRM-RPM` | 6000.0000 | 500.0000 | 12000.0000 | 5 | 1 | `decreasing` | 0.50 | 0.80 |
| `PRM-TORQ` | 62.0000 | 20.0000 | 88.0000 | 5 | 1 | `increasing` | 2.00 | 1.30 |
| `PRM-TWEAR` | 30.0000 | 0.0000 | 85.0000 | 60 | 1 | `increasing` | 3.00 | 1.20 |
| `PRM-VIB` | 2.1000 | 0.5000 | 4.5000 | 10 | 1 | `increasing` | 2.50 | 1.50 |
| `PRM-PWR` | 12.4000 | 6.0000 | 17.5000 | 30 | 1 | `increasing` | 1.00 | 0.90 |

Declarations for `MTY-CONV-BELT-12` (belt conveyor):

| machine_parameter | nominal_value | normal_min | normal_max | sampling_interval_seconds | is_ml_feature | expected_drift_direction | sensor_accuracy_pct | criticality_weight |
|---|---|---|---|---|---|---|---|---|
| `PRM-RPM` | 90.0000 | 60.0000 | 120.0000 | 15 | 0 | `none` | 1.00 | NULL |
| `PRM-PWR` | 3.1000 | 2.0000 | 4.2000 | 30 | 1 | `increasing` | 1.50 | 1.60 |
| `PRM-TEMP` | 41.0000 | 30.0000 | 58.0000 | 60 | 1 | `increasing` | 2.00 | 1.10 |

The contrast between the two tables is the point of this entity:

- The conveyor declares **no tool wear** (`requires_tooling = 0`) and **no vibration** (its category is `conveying`, but `is_rotating_equipment = 1`, so vibration would be permissible — it is simply not instrumented here).
- Conveyor `PRM-RPM` has `is_ml_feature = 0` and `expected_drift_direction = none`, because belt speed is a control setpoint, not a health signal. Feeding it to the model would add a constant-valued feature.
- Conveyor power draw carries the highest criticality weight (1.60). On a belt conveyor, rising power draw with steady speed is the clearest early indication of a seizing roller or a tensioning problem — it is the diagnostic signal that matters most for this equipment.
- Sampling intervals differ by how fast each signal moves: speed and torque every 5 seconds on the machining centre, tool wear every 60 seconds because it accumulates slowly.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | **Primary generation specification.** Determines which parameters to emit per machine, the healthy envelope to generate within, the interval to emit at, and the drift direction to apply as degradation advances |
| Monitoring Agent | Knows which parameters to expect per machine, and uses `sensor_accuracy_pct` to avoid treating instrument noise as drift |
| Prediction Agent | **Defines the model feature set** via `is_ml_feature`, and uses the envelope to normalise features consistently across machine types |
| Supervisor Agent | Uses `criticality_weight` to rank which drifting parameters matter most when assembling context |
| Decision Agent | Uses `criticality_weight` to lead with the most diagnostic signal, and the envelope to state how far outside normal a reading actually is — "12 °C above the healthy maximum" is far more useful than a bare value |
| Dashboard | Draws normal-range bands on charts so an operator can see at a glance whether a value sits inside its healthy envelope |

---

## Group D — People & Contacts

Who works in the factory, what authority they hold, which team they belong to, and how the platform reaches them. This group is what turns a recommendation into something delivered to a named person with the authority to act on it.

The group applies one structural pattern consistently: **a person is stored once.** `worker` holds identity, employment, and contact details. `maintenance_engineer` and `notification_recipient` are one-to-one specializations that add role-specific attributes without repeating a single field of personal data.

---

### 13. `worker_role`

**Purpose**

Defines job roles and — more importantly for this platform — the **authority** each role carries. It is what allows a recommendation to be routed to somebody who can actually approve the action it proposes.

**Business description**

A role is a job function: machine operator, senior operator, line supervisor, maintenance technician, maintenance engineer, quality inspector, department manager, plant manager, store keeper, production planner.

Roles matter to FactoryFlow AI for a reason that is easy to overlook. The Decision Agent frequently recommends actions that require **authority**: stopping a line, approving unplanned maintenance, releasing a spare part. Sending "stop Line 01 within 45 minutes" to an operator who cannot authorise a line stop is a delivered notification that produces no action. `can_authorize_line_stop` and `can_authorize_maintenance` encode that authority as data, so routing can be correct rather than hopeful.

`is_managerial` serves a second structural purpose. As described in §3, this model deliberately avoids back-reference manager foreign keys on `department`, `production_line`, and `maintenance_team`, because they create circular dependencies. `is_managerial` is what replaces them: the manager of a department is the active worker in that department holding a managerial role. Leadership becomes a property of the person, which is both acyclic and closer to how organizations actually work — people change roles far more often than departments change shape.

`seniority_rank` gives escalation a defined order. When a recommendation is not acknowledged, the platform needs to know who is next, and "next" means more senior.

**Primary key**

`worker_role_id` — surrogate integer, with `worker_role_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `worker_role_code` | VARCHAR(12) | Yes | `ROL-LSUP` | Unique; matches `^ROL-[A-Z]{2,5}$` | Short role identifier used in staffing records and routing rules |
| `role_name` | VARCHAR(80) | Yes | `Line Supervisor` | Non-empty | Job title. Appears in notifications so a recipient understands why they were contacted |
| `role_category` | TEXT + CHECK | Yes | `supervisor` | One of: `operator`, `technician`, `engineer`, `supervisor`, `manager`, `inspector`, `planner`, `storekeeper` | Broad classification of the role. Groups roles for routing without enumerating every title |
| `is_managerial` | INTEGER | Yes | `1` | Default 0 | Whether the role carries management responsibility. **Resolves department, line, and team leadership without a circular foreign key** |
| `seniority_rank` | INTEGER | Yes | `4` | Between 1 and 10; higher is more senior | Escalation ordering. When a recommendation goes unacknowledged, the platform escalates to a higher rank |
| `can_authorize_line_stop` | INTEGER | Yes | `1` | Default 0 | Whether the role may stop a production line. **A recommendation to stop a line must reach somebody with this authority**, or it cannot be acted on |
| `can_authorize_maintenance` | INTEGER | Yes | `0` | Default 0 | Whether the role may approve unplanned maintenance. Determines who signs off on an unscheduled intervention |
| `requires_certification` | INTEGER | Yes | `0` | Default 0 | Whether the role requires a formal certification. Flags roles where an expired certification blocks assignment |
| `description` | TEXT | No | `Supervises operators and equipment on a single production line` | Non-empty when present | Role summary. NULL when the title is self-explanatory. Gives the Decision Agent the context to address a recipient appropriately |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Child | `worker` | One-to-many | Many workers hold the same role |

`worker_role` has **no outgoing foreign keys**. It is an independent lookup at the base of the dependency graph.

**Business rules**

1. A worker holds exactly one role. Dual-role assignment is excluded — it would require a many-to-many junction that no consumer reads, and it would make authority resolution ambiguous precisely when clarity matters most.
2. `seniority_rank` orders escalation. Ranks need not be unique: several roles can sit at the same level.
3. Authority flags are role properties, never individual overrides. A specific person cannot be granted an exception, because per-person exceptions make routing unauditable — and the platform must be able to explain why a given recipient was chosen.
4. Roles with `is_managerial = 1` are expected to have `can_authorize_line_stop = 1`. A manager who cannot stop a line is not a useful escalation target.
5. Every department should have at least one active worker in a managerial role. This is how the department manager is identified.
6. Roles with `requires_certification = 1` should be held only by workers with a valid certification. For maintenance engineers this is enforced through `maintenance_engineer.certification_expiry_date`.
7. A role cannot be soft-retired while active workers hold it.

**Example records**

| worker_role_code | role_name | role_category | is_managerial | seniority_rank | can_authorize_line_stop | can_authorize_maintenance | requires_certification |
|---|---|---|---|---|---|---|---|
| `ROL-OP` | Machine Operator | `operator` | 0 | 1 | 0 | 0 | 0 |
| `ROL-SRO` | Senior Operator | `operator` | 0 | 2 | 1 | 0 | 0 |
| `ROL-MTECH` | Maintenance Technician | `technician` | 0 | 2 | 0 | 0 | 1 |
| `ROL-QINS` | Quality Inspector | `inspector` | 0 | 3 | 1 | 0 | 1 |
| `ROL-MENG` | Maintenance Engineer | `engineer` | 0 | 4 | 1 | 1 | 1 |
| `ROL-LSUP` | Line Supervisor | `supervisor` | 1 | 4 | 1 | 1 | 0 |
| `ROL-PLAN` | Production Planner | `planner` | 0 | 3 | 0 | 0 | 0 |
| `ROL-STK` | Store Keeper | `storekeeper` | 0 | 2 | 0 | 0 | 0 |
| `ROL-DMGR` | Department Manager | `manager` | 1 | 7 | 1 | 1 | 0 |
| `ROL-PMGR` | Plant Manager | `manager` | 1 | 9 | 1 | 1 | 0 |

Two rows illustrate why authority is modelled separately from seniority. `ROL-SRO` (Senior Operator, rank 2) **can** stop a line — an operator at the machine must be able to halt production on a safety concern — but cannot authorise maintenance spending. `ROL-QINS` (Quality Inspector, rank 3) can also stop a line, because releasing defective product is worse than losing output. Authority does not follow rank linearly, and encoding the two separately is what lets routing be correct.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Supervisor Agent | Resolves department and line leadership without a circular reference, and identifies who holds the authority a proposed action requires |
| Decision Agent | Addresses recommendations to a role that can actually act. A line-stop recommendation is directed to someone with `can_authorize_line_stop` |
| Notification Service | Uses `seniority_rank` for escalation ordering when a recommendation is not acknowledged |
| Dashboard | Presents role-appropriate views |

---

### 14. `worker`

**Purpose**

Represents a person employed at the plant. It is the single record of an individual — identity, employment, department, line assignment, shift, and contact details — referenced by every other people-related entity in the model.

**Business description**

Workers are the people who operate machines, supervise lines, inspect quality, repair equipment, manage stores, and plan production.

The design commitment here is that **a person exists exactly once in this model.** A maintenance engineer is a worker with maintenance-specific attributes attached through `maintenance_engineer`. A notification recipient is a worker with delivery preferences attached through `notification_recipient`. Neither repeats a name, an email, or a phone number.

That matters more than it might appear. If personal data were duplicated across three tables, a change of phone number would need three updates, and the first time one was missed the platform would send an urgent recommendation to a disconnected number. Storing contact details once removes that failure mode entirely.

`production_line_id` is nullable, and the nullability carries meaning: production staff are assigned to a specific line, while maintenance engineers, quality inspectors, store keepers, and managers serve the whole plant. NULL means plant-wide, not missing data.

`email` and `phone_number` are the actual delivery endpoints for the Notification Service. `notification_recipient` decides *whether and when* to contact somebody; `worker` holds *where* to reach them.

**Primary key**

`worker_id` — surrogate integer, with `worker_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `worker_code` | VARCHAR(12) | Yes | `EMP-1002` | Unique; matches `^EMP-[0-9]{4}$` | Employee number used in HR records and shown in notifications |
| `first_name` | VARCHAR(60) | Yes | `Priya` | Non-empty | Given name. Used to address notifications personally |
| `last_name` | VARCHAR(60) | Yes | `Nair` | Non-empty | Family name |
| `worker_role_id` | INTEGER (FK) | Yes | `6` | References active `worker_role` | The role held, and through it the authority carried |
| `department_id` | INTEGER (FK) | Yes | `1` | References active `department` | Organizational home. **A worker belongs to exactly one department** |
| `production_line_id` | INTEGER (FK) | No | `1` | References active `production_line` when present | Line assignment. **NULL means plant-wide** — normal for maintenance, quality, stores, and management |
| `shift_id` | INTEGER (FK) | Yes | `1` | References active `shift` | Default working shift. Determines whether the worker is on duty at a given time |
| `email` | VARCHAR(150) | No | `priya.nair@northgate.example` | Valid email format when present; unique when present | Work email. **The delivery endpoint for email notifications.** NULL for staff without an account, who are then unreachable by email |
| `phone_number` | VARCHAR(20) | No | `+919876543210` | E.164 format when present | Mobile number. **The delivery endpoint for WhatsApp notifications.** NULL when unavailable |
| `hire_date` | DATE | Yes | `2020-08-17` | On or after `plant.commissioned_date` | Employment start. With `skill_level`, indicates experience at this plant |
| `employment_type` | TEXT + CHECK | Yes | `permanent` | One of: `permanent`, `contract`, `apprentice` | Employment basis. Affects who may be assigned to critical work and who may be called in outside shift hours |
| `skill_level` | TEXT + CHECK | Yes | `senior` | One of: `trainee`, `junior`, `intermediate`, `senior`, `expert` | General competence level. **Stored here for all workers, not repeated on `maintenance_engineer`** |

**Note on personal data.** All names and contact details in this document are fictional placeholders. In a real deployment this entity holds personal data and should be treated accordingly — minimum necessary fields, access restricted to those who need it, and no personal data forwarded into LLM prompts beyond the name and role required to address a recommendation. The model deliberately stores no home address, date of birth, identity number, or salary: none of it serves any FactoryFlow AI use case, and holding data with no consumer is a liability rather than an asset.

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `worker_role` | Many-to-one | The role held |
| Parent | `department` | Many-to-one | Exactly one department |
| Parent | `production_line` | Many-to-one, optional | Line assignment, or NULL for plant-wide |
| Parent | `shift` | Many-to-one | Default shift |
| Child | `maintenance_engineer` | One-to-one, optional | Maintenance-specific attributes, when the worker is an engineer |
| Child | `notification_recipient` | One-to-one, optional | Notification preferences, when the worker receives alerts |

**Business rules**

1. **A worker belongs to exactly one department.** Matrix reporting is excluded.
2. A worker holds exactly one role and is assigned to exactly one default shift.
3. `production_line_id` is NULL for workers who serve the whole plant. It should be populated for workers in a `production` function department holding operator or supervisor roles.
4. A worker may have at most one `maintenance_engineer` row and at most one `notification_recipient` row. Both relationships are one-to-one, enforced by a unique constraint on `worker_id` in each child.
5. Only workers in a department with `department_function = 'maintenance'` may have a `maintenance_engineer` row. An operator is not a maintenance engineer.
6. A worker with a `notification_recipient` row must have a populated `email` when `email_enabled = 1`, and a populated `phone_number` when `whatsapp_enabled = 1`. A recipient with an enabled channel and no endpoint is a silent delivery failure — the worst kind, because it looks configured.
7. `email` is unique when present. Two workers sharing an address would make delivery ambiguous.
8. Workers are soft-retired, never deleted. Historical maintenance and notification records reference them, and the audit trail must survive an employee leaving.
9. A worker cannot be soft-retired while an active `maintenance_engineer` row designates them a team lead. The lead must be reassigned first, or the team is left leaderless.

**Example records**

| worker_code | first_name | last_name | role | department | line | shift | employment_type | skill_level |
|---|---|---|---|---|---|---|---|---|
| `EMP-1001` | Anand | Selvam | `ROL-PMGR` | `DEP-PRD` | NULL | `SH-GEN` | `permanent` | `expert` |
| `EMP-1002` | Priya | Nair | `ROL-LSUP` | `DEP-PRD` | `LN-01` | `SH-A` | `permanent` | `senior` |
| `EMP-1003` | Ravi | Kumar | `ROL-OP` | `DEP-PRD` | `LN-01` | `SH-A` | `permanent` | `intermediate` |
| `EMP-1004` | Meera | Joseph | `ROL-LSUP` | `DEP-PRD` | `LN-02` | `SH-A` | `permanent` | `senior` |
| `EMP-1005` | Karthik | Rajan | `ROL-SRO` | `DEP-PRD` | `LN-03` | `SH-B` | `permanent` | `senior` |
| `EMP-1010` | Divya | Menon | `ROL-DMGR` | `DEP-MNT` | NULL | `SH-GEN` | `permanent` | `expert` |
| `EMP-1011` | Suresh | Iyer | `ROL-MENG` | `DEP-MNT` | NULL | `SH-A` | `permanent` | `expert` |
| `EMP-1012` | Vinod | Balan | `ROL-MENG` | `DEP-MNT` | NULL | `SH-A` | `permanent` | `senior` |
| `EMP-1013` | Latha | Chandran | `ROL-MTECH` | `DEP-MNT` | NULL | `SH-GEN` | `permanent` | `intermediate` |
| `EMP-1014` | Ganesh | Pillai | `ROL-MENG` | `DEP-MNT` | NULL | `SH-C` | `permanent` | `senior` |
| `EMP-1015` | Arjun | Das | `ROL-MTECH` | `DEP-MNT` | NULL | `SH-A` | `contract` | `junior` |
| `EMP-1020` | Fathima | Rasheed | `ROL-QINS` | `DEP-QLT` | NULL | `SH-A` | `permanent` | `senior` |
| `EMP-1030` | Mohan | Kurup | `ROL-STK` | `DEP-WHS` | NULL | `SH-A` | `permanent` | `intermediate` |

Every maintenance and quality worker has `production_line_id = NULL`, because they serve all four lines. Only operators and line supervisors carry a line assignment. `EMP-1010` (Divya Menon) holds `ROL-DMGR`, which is managerial — she is therefore the Maintenance department manager, resolved through her role rather than through a foreign key on `department`.

**These 13 rows are a representative subset, not a complete roster.** The plant's `headcount_budget` figures total around 105 people. The sample deliberately includes only enough workers to exercise every relationship this entity participates in — each role category, both line-assigned and plant-wide assignment, all four shifts, both employment types, and the workers referenced by `maintenance_engineer` and `notification_recipient`. A complete seed adds the remaining operators, inspectors, planners, and store staff, and must satisfy every completeness rule in §32.2 — including a managerial worker in each of the five departments, which this subset covers for Production and Maintenance only.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Supervisor Agent | Identifies who is on duty on the affected line and which engineers are available on the current shift |
| Decision Agent | Names specific people in recommendations — "assign to Suresh Iyer, Mechanical Maintenance" is actionable in a way that "assign to maintenance" is not |
| Notification Service | **Reads `email` and `phone_number` as the actual delivery endpoints.** Uses `first_name` to address messages |
| Dashboard | Shows staffing context alongside line state |
| Future reporting | Enables analysis of response performance by shift and team |

---

### 15. `maintenance_team`

**Purpose**

Represents a maintenance crew defined by its specialization, its shift coverage, and its response commitment. It is the unit the Decision Agent assigns work to.

**Business description**

Maintenance is organized into teams by discipline. Mechanical handles bearings, spindles, belts, and alignment. Electrical handles motors, drives, and power. Automation and controls handles PLCs, robots, and sensors. A night response team provides general cover outside the day shift.

Three attributes make a team assignment recommendation concrete rather than vague.

**`specialization`** must match the failure. Its vocabulary is deliberately identical to `machine_category.primary_maintenance_specialization`, which means matching a failed machine to a qualified team is a direct comparison rather than an inference rule. A spindle failure on a CNC machining centre maps to `mechanical`, and the mechanical team is the answer. Sharing the vocabulary between the two entities is what makes that lookup trivial — and it is the reason the vocabularies were designed together rather than independently.

**`shift_id`** determines availability. A recommendation at 02:00 that assigns the day-shift mechanical team is not actionable. The night response team is.

**`is_emergency_response`** distinguishes teams that handle unplanned breakdowns from those that only perform planned work. The automation team may work standard hours on scheduled projects and not be callable for a 3 a.m. breakdown. Recommending them for an emergency would waste the response window.

**`target_response_time_minutes`** is the team's commitment. It is deliberately a **target**, not a measured average: measured response time is derived from operational maintenance history and belongs there. Storing the target here lets the Decision Agent state an expected response and lets future reporting compare actual against commitment.

**Primary key**

`maintenance_team_id` — surrogate integer, with `maintenance_team_code` unique.

**Note on the absent team lead foreign key.** As with `department`, a `team_lead_engineer_id` column here would create a circular dependency: `maintenance_engineer.maintenance_team_id` points one way and the lead pointer would point back. Instead `maintenance_engineer.is_team_lead` marks the lead on the child side. Same pattern, same reason, applied consistently across all three leadership relationships in the model.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `maintenance_team_code` | VARCHAR(12) | Yes | `MTM-MECH` | Unique; matches `^MTM-[A-Z]{3,5}$` | Team identifier used in work assignment and notifications |
| `team_name` | VARCHAR(100) | Yes | `Mechanical Maintenance Team` | Non-empty | Descriptive name shown in recommendations |
| `department_id` | INTEGER (FK) | Yes | `2` | References active `department` with `department_function = 'maintenance'` | Owning department. Teams belong to Maintenance |
| `shift_id` | INTEGER (FK) | Yes | `1` | References active `shift` | Shift the team covers. **Determines when the team is available** |
| `specialization` | TEXT + CHECK | Yes | `mechanical` | One of: `mechanical`, `electrical`, `automation`, `general`; same vocabulary as `machine_category.primary_maintenance_specialization` | **The team's discipline.** Matched directly against the failed machine's category to check qualification |
| `base_plant_area_id` | INTEGER (FK) | No | `6` | References active `plant_area` when present | Where the team is based. NULL when mobile with no fixed base. Affects travel time to the affected machine |
| `contact_extension` | VARCHAR(10) | No | `214` | Digits only when present | Internal phone extension. NULL when the team has no shared line. Included in recommendations so a supervisor can call directly |
| `max_concurrent_jobs` | INTEGER | Yes | `3` | Between 1 and 10 | How many jobs the team can run at once. **Capacity limit** — lets the Supervisor Agent check whether the team has headroom before the Decision Agent assigns more work |
| `is_emergency_response` | INTEGER | Yes | `1` | Default 0 | Whether the team handles unplanned breakdowns. `0` for planned-work-only teams, which must not be assigned emergencies |
| `target_response_time_minutes` | INTEGER | Yes | `30` | Between 5 and 480 | Committed time to reach a machine after being called. **A target, not a measured average** — actuals live in operational history |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `department` | Many-to-one | Teams belong to the Maintenance department |
| Parent | `shift` | Many-to-one | Each team covers one shift |
| Parent | `plant_area` | Many-to-one, optional | Physical base, or NULL if mobile |
| Child | `maintenance_engineer` | One-to-many | A team contains several engineers |
| Child | `machine_maintenance_schedule` | One-to-many | Scheduled maintenance is assigned to a team |

**Business rules**

1. **A maintenance engineer belongs to exactly one maintenance team.** Split assignment across teams is excluded: it would make "who is responding" ambiguous during a breakdown, which is when clarity matters most.
2. A team belongs to exactly one department, and that department must have `department_function = 'maintenance'`.
3. A team covers exactly one shift. Round-the-clock coverage is achieved with several teams on different shifts, not by one team spanning all of them.
4. **Exactly one engineer per team has `is_team_lead = 1.`** A team without a lead has no accountable point of contact; two leads produce contradictory assignment.
5. Every active team must have at least one active engineer. An empty team cannot be assigned work, and recommending it would produce a silent failure.
6. `specialization` must be drawn from the same vocabulary as `machine_category.primary_maintenance_specialization`. This shared vocabulary is a deliberate design decision, not a coincidence, and it must be preserved on both sides.
7. Only teams with `is_emergency_response = 1` may be assigned unplanned breakdown work. Planned-work teams may only appear on `machine_maintenance_schedule` rows.
8. For every combination of specialization and production shift that covers monitored equipment, at least one emergency-response team should exist. A gap means a class of failure has no responder during that shift — a coverage hole the Supervisor Agent should be able to detect and report rather than discover during an incident.
9. A team with `general` specialization can respond to any failure but is expected to have a longer `target_response_time_minutes`, reflecting broader but shallower capability.
10. A team cannot be soft-retired while active engineers are assigned to it or active maintenance schedules reference it.

**Example records**

| maintenance_team_code | team_name | department | shift | specialization | base_area | ext | max_concurrent_jobs | is_emergency_response | target_response_time_minutes |
|---|---|---|---|---|---|---|---|---|---|
| `MTM-MECH` | Mechanical Maintenance Team | `DEP-MNT` | `SH-A` | `mechanical` | `AREA-MNT` | `214` | 3 | 1 | 30 |
| `MTM-ELEC` | Electrical Maintenance Team | `DEP-MNT` | `SH-A` | `electrical` | `AREA-MNT` | `215` | 2 | 1 | 30 |
| `MTM-AUTO` | Automation & Controls Team | `DEP-MNT` | `SH-GEN` | `automation` | `AREA-MNT` | `216` | 2 | 0 | 120 |
| `MTM-NIGHT` | Night Response Team | `DEP-MNT` | `SH-C` | `general` | `AREA-MNT` | `217` | 2 | 1 | 45 |

This small set exposes a real coverage gap, which is exactly the kind of finding the model should make visible. `MTM-AUTO` is the only automation team, it works the general day shift, and it is **not** an emergency responder. So a robotic welding cell control fault at 02:00 has no specialist available — only `MTM-NIGHT`, whose `general` specialization means broad but shallow capability and a 45-minute response.

That is a genuine operational constraint, and because it is encoded in data rather than assumed, the Decision Agent can state it plainly: *"No automation specialist is on shift. Night Response can attend within 45 minutes for containment; automation support is available from 09:00."* A model that could not express the gap would produce a recommendation that quietly assumed help was available.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Supervisor Agent | Assembles maintenance context — which teams are on shift, which are qualified, and whether they have capacity |
| Decision Agent | **Produces the maintenance assignment element of every recommendation.** Matches specialization to the failure, checks shift availability and emergency eligibility, and states the expected response time |
| Notification Service | Uses `contact_extension` so a supervisor can reach the team directly rather than through a chain |
| Dashboard | Shows maintenance coverage by shift, which makes gaps visible before they are discovered during an incident |
| Future reporting | Compares actual response times against `target_response_time_minutes` |

---

### 16. `maintenance_engineer`

**Purpose**

Holds the maintenance-specific attributes of a worker: team membership, discipline, certification validity, experience, on-call status, and team leadership. It is a one-to-one specialization of `worker`, not a second person record.

**Business description**

Some workers are maintenance engineers and technicians. They carry attributes that no other worker has — a discipline, a certification with an expiry date, on-call availability, and possibly team leadership — and those attributes have no meaning for an operator or a store keeper.

**Why a separate entity rather than more columns on `worker`.** Putting `maintenance_team_id`, `is_team_lead`, and `certification_expiry_date` on `worker` would leave them NULL for the large majority of rows, and it would allow a nonsensical combination — a store keeper marked as a mechanical team lead. A one-to-one specialization keeps the columns where they mean something, and makes the constraint enforceable: only workers in a maintenance department can have a row here.

**Why not a standalone entity with its own name and contact fields.** That would duplicate personal data. An engineer's phone number would exist in two places, and the first time they differed the platform would call the wrong number during a breakdown. `maintenance_engineer` therefore holds **no** name, no email, and no phone — all of it comes from the parent `worker` row.

`certification_expiry_date` has a direct operational consequence. An engineer whose certification has lapsed cannot legally or safely be assigned certain work. A recommendation that assigns them would be invalid, so the platform must be able to check.

**`is_team_lead`** is the child-side leadership marker described in §3 and §15. It exists here rather than as `maintenance_team.team_lead_engineer_id` specifically to keep the dependency graph acyclic.

**Note on the absent skill level.** `skill_level` lives on `worker` and is not repeated here. General competence applies to all workers; repeating it for engineers would create two values for one fact. What this entity adds is *discipline* and *certification*, which are maintenance-specific.

**Primary key**

`maintenance_engineer_id` — surrogate integer, with `maintenance_engineer_code` unique and a **unique constraint on `worker_id`** enforcing the one-to-one relationship.

Using `worker_id` itself as the primary key was considered — it is a legitimate pattern for one-to-one specializations and would enforce uniqueness structurally. A separate surrogate was chosen for consistency with every other entity in the model, and because `maintenance_engineer_code` (`ENG-01`) is a distinct identifier that maintenance staff genuinely use in work assignment, separate from the employee number.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `maintenance_engineer_code` | VARCHAR(10) | Yes | `ENG-01` | Unique; matches `^ENG-[0-9]{2}$` | Engineer identifier used in maintenance work assignment |
| `worker_id` | INTEGER (FK) | Yes | `7` | **Unique**; references active `worker` in a department with `department_function = 'maintenance'` | The person. **Unique constraint enforces one-to-one** — all identity and contact data comes from here |
| `maintenance_team_id` | INTEGER (FK) | Yes | `1` | References active `maintenance_team` | Team membership. **An engineer belongs to exactly one team** |
| `primary_specialization` | TEXT + CHECK | Yes | `mechanical` | One of: `mechanical`, `electrical`, `automation`, `general`; same vocabulary as `maintenance_team.specialization` | The engineer's discipline. Normally matches their team, but may differ for a cross-trained engineer |
| `is_team_lead` | INTEGER | Yes | `1` | Default 0; exactly one `1` per team | **Team leadership, marked on the child.** Avoids a circular foreign key on `maintenance_team` |
| `years_experience` | INTEGER | Yes | `12` | Between 0 and 50 | Total maintenance experience. Distinguishes an engineer who has seen a failure mode before from one who has not, which affects who should handle an unusual fault |
| `certification_expiry_date` | DATE | No | `2027-06-30` | Must be in the future for assignment eligibility | When formal certification lapses. NULL when no certification is required for the role. **An expired certification blocks assignment** |
| `is_on_call` | INTEGER | Yes | `1` | Default 0 | Whether the engineer can be called outside their shift. **Determines whether an off-shift specialist is reachable at all** |
| `secondary_specialization` | TEXT + CHECK | No | `electrical` | One of the specialization values; must differ from `primary_specialization` | Cross-trained second discipline. NULL for single-discipline engineers. Widens the pool when the primary specialist is unavailable — often the difference between a 30-minute and a 9-hour wait |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `worker` | **One-to-one** | The person, enforced by a unique constraint on `worker_id` |
| Parent | `maintenance_team` | Many-to-one | Exactly one team |

**Business rules**

1. **A maintenance engineer belongs to exactly one maintenance team.**
2. One `maintenance_engineer` row per worker, enforced by the unique constraint on `worker_id`.
3. Only workers in a department with `department_function = 'maintenance'` may have a row here.
4. **Exactly one engineer per team has `is_team_lead = 1.`**
5. An engineer with a lapsed `certification_expiry_date` must not be assigned work requiring certification. The Decision Agent checks this before suggesting an assignment, because a recommendation naming an uncertified engineer is not merely unhelpful — it is unsafe.
6. `primary_specialization` normally matches the team's specialization. A mismatch is permitted for cross-trained engineers but is worth flagging in review, since it may indicate a data entry error.
7. `secondary_specialization`, when present, must differ from `primary_specialization`.
8. Only engineers with `is_on_call = 1` may be recommended outside their assigned shift.
9. Personal and contact data is **never** stored here. Name, email, and phone come from the parent `worker` row, so there is exactly one place to update them.
10. An engineer cannot be soft-retired while marked `is_team_lead` — leadership must be reassigned first, or the team is left without an accountable contact.

**Example records**

| maintenance_engineer_code | worker | team | primary_specialization | is_team_lead | years_experience | certification_expiry_date | is_on_call | secondary_specialization |
|---|---|---|---|---|---|---|---|---|
| `ENG-01` | `EMP-1011` (Suresh Iyer) | `MTM-MECH` | `mechanical` | 1 | 12 | 2027-06-30 | 1 | `electrical` |
| `ENG-02` | `EMP-1012` (Vinod Balan) | `MTM-ELEC` | `electrical` | 1 | 8 | 2026-11-15 | 1 | NULL |
| `ENG-03` | `EMP-1013` (Latha Chandran) | `MTM-AUTO` | `automation` | 1 | 5 | 2028-02-28 | 0 | NULL |
| `ENG-04` | `EMP-1014` (Ganesh Pillai) | `MTM-NIGHT` | `general` | 1 | 9 | 2027-09-30 | 1 | `mechanical` |
| `ENG-05` | `EMP-1015` (Arjun Das) | `MTM-MECH` | `mechanical` | 0 | 3 | 2026-08-31 | 0 | NULL |

Two rows carry real consequence for the Decision Agent.

`ENG-03` (automation, the only automation specialist) has `is_on_call = 0`. Combined with `MTM-AUTO` not being an emergency-response team, this confirms the coverage gap identified in §15 — an automation fault outside the general shift has no specialist available, and the platform can say so precisely.

`ENG-01` has `secondary_specialization = 'electrical'` and `is_on_call = 1`. When the electrical specialist is unavailable, he is a legitimate fallback. That single attribute is what allows the Decision Agent to offer a real alternative instead of reporting that nobody is available.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Supervisor Agent | Assembles the available engineer pool for the current shift, filtered by specialization, certification validity, and on-call status |
| Decision Agent | **Produces the named-assignment part of the maintenance recommendation.** Verifies certification, matches discipline, checks on-call eligibility, and falls back to a cross-trained engineer when the primary specialist is unavailable |
| Notification Service | Resolves the engineer's contact details through the parent `worker` row |
| Dashboard | Shows maintenance capability and certification status by team and shift |

---

### 17. `notification_recipient`

**Purpose**

Defines who receives platform notifications, on which channels, at what severity, for which scope, and how often. It is the configuration that connects a finished recommendation to a specific person.

**Business description**

The Notification Service needs to answer four questions for every recommendation: **who**, **which channel**, **at what severity**, and **how often at most**. This entity holds all four as data.

Without it, notification routing would be hardcoded, and every change to who gets alerted would require a code change. More importantly, the platform could not explain *why* a particular person was contacted — and the explainability contract requires that every step be traceable.

`min_severity_level_id` implements graduated escalation, which is the main defence against alert fatigue. A line supervisor wants to know about medium-severity conditions on their own line. The plant manager wants only critical situations. Sending everything to everyone trains recipients to ignore notifications, and once that happens the platform has made things worse rather than better.

`scope_production_line_id` limits a recipient to a single line. NULL means plant-wide. A Line 01 supervisor should not receive Line 03 alerts — they cannot act on them, and each irrelevant message erodes attention for the relevant ones.

`max_notifications_per_hour` is a hard rate limit. If a machine oscillates around a threshold, or several correlated events fire together, an unlimited pipeline would flood a recipient and effectively disable the channel. The limit is per recipient because tolerance genuinely differs: a plant manager receiving four messages an hour is being informed; forty an hour and they will mute the channel.

**No contact details are stored here.** Email addresses and phone numbers live on `worker`. This entity stores only *whether* a channel is enabled. That separation means a change of phone number is one update in one place, and there is no possibility of an urgent recommendation being sent to a stale number that only exists in the notification configuration.

**Primary key**

`notification_recipient_id` — surrogate integer, with a **unique constraint on `worker_id`** enforcing one-to-one with `worker`.

**No business code.** This entity has no `_code` attribute, unlike most entities in the model. It is a configuration row attached to exactly one worker, and `worker_code` already identifies it unambiguously. Inventing a second code would create a redundant identifier for the same person. The same reasoning applies to `product_line_capability` (§7) and `machine_type_parameter` (§12): rows that exist to qualify a relationship or a parent do not need independent names, because nobody on the shop floor refers to them by one.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `worker_id` | INTEGER (FK) | Yes | `2` | **Unique**; references active `worker` | The person to notify. **Unique constraint enforces one-to-one.** Contact endpoints come from this row |
| `min_severity_level_id` | INTEGER (FK) | Yes | `3` | References active `failure_severity_level` | **Lowest severity that reaches this recipient.** The primary defence against alert fatigue |
| `email_enabled` | INTEGER | Yes | `1` | Default 1; requires non-NULL `worker.email` | Whether to send email. Suited to detail — full reasoning, evidence, and impact |
| `whatsapp_enabled` | INTEGER | Yes | `1` | Default 0; requires non-NULL `worker.phone_number` | Whether to send WhatsApp. Suited to urgency — short, immediate, read away from a desk |
| `scope_production_line_id` | INTEGER (FK) | No | `1` | References active `production_line` when present | Line the recipient cares about. **NULL means plant-wide.** Prevents a supervisor receiving alerts for lines they cannot act on |
| `notify_outside_shift_hours` | INTEGER | Yes | `0` | Default 0 | Whether to contact this recipient outside their assigned shift. `0` respects off-duty time; `1` is appropriate for managers and on-call staff |
| `escalation_order` | INTEGER | Yes | `1` | Greater than 0 | Contact order within the same scope. Lower numbers first. Defines the escalation chain when a recommendation is not acknowledged |
| `max_notifications_per_hour` | INTEGER | No | `6` | Between 1 and 60 when present | **Rate limit.** NULL means unlimited. Prevents a flapping condition from flooding a recipient and causing them to mute the channel entirely |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `worker` | **One-to-one** | The person, enforced by a unique constraint on `worker_id` |
| Parent | `failure_severity_level` | Many-to-one | The minimum severity that reaches them |
| Parent | `production_line` | Many-to-one, optional | Line scope, or NULL for plant-wide |

**Business rules**

1. One `notification_recipient` row per worker, enforced by the unique constraint on `worker_id`.
2. At least one channel must be enabled. A recipient with both channels disabled is unreachable, and a configured-but-unreachable recipient is worse than no configuration at all, because it looks correct.
3. `email_enabled = 1` requires a populated `worker.email`. `whatsapp_enabled = 1` requires a populated `worker.phone_number`. Validated at configuration time, not discovered at send time.
4. A recipient only receives notifications at or above `min_severity_level_id`.
5. A recipient with a non-NULL `scope_production_line_id` receives only notifications concerning that line. NULL scope receives all lines.
6. Recipients with `notify_outside_shift_hours = 0` are skipped outside their assigned shift, and the Notification Service moves to the next `escalation_order`. If no eligible recipient exists for a critical situation, the departmental `escalation_email` is the fallback — a critical recommendation must never fail to reach anybody.
7. At least one active recipient must exist with `min_severity_level_id` set to the most severe level and `notify_outside_shift_hours = 1`. Without this, a critical failure at 03:00 reaches nobody.
8. `escalation_order` values should be distinct within the same scope and severity band, so the chain is unambiguous.
9. Contact details are **never** stored here. They live on `worker`.
10. Recommendations that exceed a recipient's rate limit are suppressed for delivery but still recorded and visible on the dashboard. Suppression affects the channel, never the record — the operational history stays complete.

**Example records**

| worker | min_severity | email_enabled | whatsapp_enabled | scope_line | notify_outside_shift_hours | escalation_order | max_notifications_per_hour |
|---|---|---|---|---|---|---|---|
| `EMP-1002` (Priya Nair, LN-01 Supervisor) | `SEV-3` | 1 | 1 | `LN-01` | 0 | 1 | 6 |
| `EMP-1004` (Meera Joseph, LN-02 Supervisor) | `SEV-3` | 1 | 1 | `LN-02` | 0 | 1 | 6 |
| `EMP-1014` (Ganesh Pillai, Night Response) | `SEV-2` | 0 | 1 | NULL | 1 | 1 | 8 |
| `EMP-1010` (Divya Menon, Maintenance Manager) | `SEV-2` | 1 | 1 | NULL | 1 | 2 | 10 |
| `EMP-1001` (Anand Selvam, Plant Manager) | `SEV-1` | 1 | 0 | NULL | 1 | 3 | 4 |

The configuration forms a deliberate escalation ladder:

- **Line supervisors** get medium severity (`SEV-3`) and above, but only for their own line, and only during their shift. Highest volume, narrowest scope.
- **Night Response** gets high severity (`SEV-2`) and above, plant-wide, at any hour, by WhatsApp only — the right channel for someone on the shop floor at 03:00 rather than at a desk.
- **Maintenance Manager** gets high severity plant-wide at any hour on both channels, second in the chain.
- **Plant Manager** gets critical only (`SEV-1`), email only, four per hour maximum. Least volume, highest threshold.

Every recipient's volume is inversely proportional to their seniority. That is what makes graduated escalation work: the people closest to the machine see the most, and the people furthest from it see only what genuinely warrants their attention.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Supervisor Agent | Checks whether an eligible recipient exists before escalating. A situation nobody can be told about needs a different handling path |
| Decision Agent | Tailors detail to the audience — a supervisor needs the technical evidence, a plant manager needs the business impact |
| Notification Service | **Primary configuration source.** Determines recipients, channels, severity filtering, line scope, quiet hours, escalation order, and rate limits |
| Dashboard | Shows notification configuration so gaps in coverage are visible before an incident exposes them |
| Future reporting | Enables analysis of notification volume and acknowledgement rates per recipient, which is how rate limits and severity thresholds get tuned |

---

## Group E — Materials & Partners

What the factory consumes, where it is stored, who supplies it, and who buys the output. This group supplies two things the Decision Agent cannot produce without it: **whether the spare part needed for a repair is even available**, and **whose order is at risk if the line stops**.

---

### 18. `inventory_location`

**Purpose**

Represents a physical storage location within the plant. It answers *"where is this material, and how long does it take to get it?"*

**Business description**

Material is stored in defined places: raw material racks in the warehouse, spare parts bins in the parts store, a tooling crib near the machining bay, work-in-progress buffers beside the lines, finished goods racks by dispatch.

Locations matter to FactoryFlow AI for one reason that dominates the others: **retrieval time is part of the repair time.** When the Decision Agent recommends replacing a spindle bearing, the honest downtime estimate is retrieval time plus repair time. A part sitting in a bin 15 minutes away is not the same as one in the tooling crib four minutes from the machine. `average_retrieval_time_minutes` makes that difference explicit rather than leaving it as an optimistic assumption.

`stock_count_frequency_days` addresses a subtler and very real problem: **stock records are not always right.** A spare parts store counted every 90 days may have drifted from its recorded quantity. When the platform reports "the bearing is in stock", the confidence in that statement depends on when the location was last verified. Recording the counting policy lets the Decision Agent qualify its own claim — *"one unit shown in stock, though this location is counted only quarterly"* — rather than asserting availability it cannot guarantee. Overstating certainty about a part being on the shelf is how a recovery plan fails at the worst possible moment.

**Note on absent attributes.** Access restriction is **not** stored here — it lives on `plant_area` and applies to everything inside the area. Duplicating it would allow a location to claim open access inside a restricted area.

**Primary key**

`inventory_location_id` — surrogate integer, with `inventory_location_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `inventory_location_code` | VARCHAR(16) | Yes | `LOC-SP-B2` | Unique; matches `^LOC-[A-Z]{2}-[A-Z0-9]{1,4}$` | Bin or rack identifier used by store staff to physically find material |
| `location_name` | VARCHAR(100) | Yes | `Spare Parts Bin B2` | Non-empty | Descriptive name shown in recommendations, so a storekeeper knows where to go |
| `plant_area_id` | INTEGER (FK) | Yes | `5` | References active `plant_area` | Physical area containing this location. Access restrictions are inherited from here |
| `location_type` | TEXT + CHECK | Yes | `spare_parts_store` | One of: `raw_material_store`, `spare_parts_store`, `tooling_crib`, `wip_buffer`, `finished_goods_store`, `quarantine` | What the location holds. Constrains which item types may be stored here |
| `capacity_slots` | INTEGER | No | `480` | Greater than 0 when present | Number of bin or rack positions. NULL when unbounded, as for open floor buffers. Supports capacity reporting |
| `is_temperature_controlled` | INTEGER | Yes | `0` | Default 0 | Whether conditions are regulated. Required for items with a shelf life sensitive to heat |
| `average_retrieval_time_minutes` | INTEGER | Yes | `15` | Between 1 and 240 | **Typical time to fetch an item from here to the shop floor.** Added to repair time to give an honest downtime estimate |
| `stock_count_frequency_days` | INTEGER | No | `90` | Between 1 and 365 when present | How often physical stock is verified. NULL for locations not cycle-counted. **Qualifies confidence in the recorded quantity** |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `plant_area` | Many-to-one | Every location sits inside one area |
| Child | `inventory_item` | One-to-many | Items have a default storage location |

**Business rules**

1. Every location belongs to exactly one plant area.
2. `location_type` must be consistent with the containing area's `area_type`. A `spare_parts_store` location belongs in a `spare_parts_store` or `warehouse` area, not in a quality lab.
3. Item types must match location types. Spare parts are stored in a `spare_parts_store` or `tooling_crib`, raw material in a `raw_material_store`, finished goods in a `finished_goods_store`.
4. Items with a shelf life sensitive to temperature should be stored in a location with `is_temperature_controlled = 1`.
5. `average_retrieval_time_minutes` is a planning figure maintained by the warehouse, not a measured average. Actual retrieval times, if ever captured, are operational.
6. Locations with `location_type = 'wip_buffer'` are not cycle-counted and carry NULL `stock_count_frequency_days`. Work-in-progress is transient by nature.
7. A location cannot be soft-retired while active items designate it as their default location.

**Example records**

| inventory_location_code | location_name | plant_area | location_type | capacity_slots | is_temperature_controlled | average_retrieval_time_minutes | stock_count_frequency_days |
|---|---|---|---|---|---|---|---|
| `LOC-RM-A1` | Raw Material Rack A1 | `AREA-WHR` | `raw_material_store` | 240 | 0 | 12 | 30 |
| `LOC-SP-B2` | Spare Parts Bin B2 | `AREA-SPR` | `spare_parts_store` | 480 | 0 | 15 | 90 |
| `LOC-TC-01` | Tooling Crib | `AREA-MCH` | `tooling_crib` | 120 | 0 | 4 | 30 |
| `LOC-WP-01` | WIP Buffer Line 01 | `AREA-MCH` | `wip_buffer` | 60 | 0 | 1 | NULL |
| `LOC-FG-01` | Finished Goods Rack | `AREA-PKG` | `finished_goods_store` | 300 | 0 | 8 | 30 |

The tooling crib is deliberately sited in the machining bay itself — four minutes from the machines — while spare parts sit in a separate store 15 minutes away. That difference is a real operational choice: consumable tooling is needed many times a shift, spare parts a few times a year. It also means a tooling problem and a bearing problem produce materially different downtime estimates, which is exactly the distinction the Decision Agent should be making.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Generates material movements against realistic storage locations |
| Supervisor Agent | Resolves where a required part physically is when assembling inventory context |
| Decision Agent | **Adds `average_retrieval_time_minutes` to the repair estimate** for an honest downtime figure, and uses `stock_count_frequency_days` to qualify how much to trust the stock number |
| Dashboard | Displays inventory grouped by physical location |

---

### 19. `inventory_item`

**Purpose**

Represents a material, component, consumable, spare part, or tool that the plant stocks. It carries the **stocking policy** — reorder point, safety stock, maximum — and the sourcing information needed to answer whether a repair can proceed.

**Business description**

Inventory items are everything the factory consumes: cast iron blanks and aluminium billets, pump impellers and shafts, cutting coolant, carbide end mills, and the spare bearings, belts, and drive modules that keep machines running.

For FactoryFlow AI, the spare parts matter most. When the Prediction Agent flags a likely spindle bearing failure, the first question in any real recovery plan is: **is the bearing on the shelf?** If it is, the repair is a maintenance-window decision. If it is not, the answer becomes a seven-day lead time, and the whole recommendation changes character — from "schedule a replacement" to "order the part today and manage the risk in the meantime."

**The master/operational boundary is at its sharpest here.** This entity holds **policy**: `reorder_point`, `safety_stock_qty`, `max_stock_qty`, `lead_time_days`. It holds **no quantity on hand.** Current stock changes with every issue and receipt, making it operational by every test in §2.1. The platform answers "is the part available?" by comparing an operational quantity against these master thresholds — two sources, one join, and no confusion about which is authoritative.

`is_critical_spare` marks parts whose absence stops a machine. It is not the same as expensive: a 640-rupee drive belt can idle a machining centre just as effectively as a 48,500-rupee servo module. Criticality is about consequence of absence, and it drives stocking policy and escalation independently of value.

`abc_class` is the standard inventory value classification — A items are high-value and tightly controlled, C items are low-value and loosely managed. It is included because it is genuinely present in every real ERP and because it gives the Decision Agent a sense of how much attention a shortage warrants commercially, as distinct from operationally.

**Note on the absent machine-type link.** A `machine_type_id` column here — "which machine does this part serve?" — was considered and rejected. The relationship the platform actually needs is more specific: *which failure mode on which machine type requires which part.* That belongs on `machine_type_failure_mode` (§25), where it can also express that one part serves several failure modes and one failure mode needs a specific part. Putting a machine type on the item would be both less precise and a duplicate of information better held on the failure mode.

**Primary key**

`inventory_item_id` — surrogate integer, with `inventory_item_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `inventory_item_code` | VARCHAR(24) | Yes | `INV-CP-BRG-6205` | Unique; matches `^INV-[A-Z]{2}-[A-Z0-9-]{2,14}$` | Part number used in stores, on requisitions, and in recommendations |
| `item_name` | VARCHAR(150) | Yes | `Spindle Bearing 6205-2RS` | Non-empty | Descriptive name. Appears verbatim in recommendations, so it must be recognisable to a storekeeper |
| `item_type` | TEXT + CHECK | Yes | `spare_part` | One of: `raw_material`, `component`, `consumable`, `spare_part`, `tooling`, `finished_good` | What kind of item this is. Constrains valid storage locations and determines whether it appears in a bill of materials or in a repair |
| `unit_of_measure` | TEXT + CHECK | Yes | `EA` | One of: `EA`, `KG`, `L`, `M`, `SET`, `BOX` | Unit of stocking and issue. Keeps quantity arithmetic dimensionally consistent across bills of materials and stock checks |
| `unit_cost` | NUMERIC(12,2) | Yes | `2150.00` | Greater than 0 | Standard cost per unit in `plant.currency_code`. Values a shortage and feeds material cost roll-up |
| `reorder_point` | NUMERIC(12,2) | Yes | `8.00` | Greater than or equal to `safety_stock_qty` | **Stock level at which replenishment is triggered.** Master policy, not a live quantity |
| `safety_stock_qty` | NUMERIC(12,2) | Yes | `4.00` | Greater than or equal to 0; less than or equal to `reorder_point` | Buffer held against demand and lead-time variability. Falling below it is a genuine risk signal |
| `max_stock_qty` | NUMERIC(12,2) | Yes | `24.00` | Greater than `reorder_point` | Upper stocking limit. Bounds replenishment quantity |
| `lead_time_days` | INTEGER | Yes | `7` | Greater than or equal to 0 | Days from order to receipt. **Converts a stockout into a concrete delay** the recovery plan must work around |
| `primary_supplier_id` | INTEGER (FK) | No | `2` | References active `supplier` when present | Normal source of supply. NULL for internally produced items. Identifies who to contact for an expedited order |
| `default_inventory_location_id` | INTEGER (FK) | Yes | `2` | References active `inventory_location` with a compatible `location_type` | Where the item is normally stored, and therefore how long retrieval takes |
| `is_critical_spare` | INTEGER | Yes | `1` | Default 0 | Whether absence stops a machine. **Independent of cost** — a cheap belt can be as critical as an expensive drive |
| `abc_class` | CHAR(1) | Yes | `A` | One of: `A`, `B`, `C` | Standard value classification. A items are tightly controlled, C items loosely. Indicates commercial attention warranted by a shortage |
| `shelf_life_days` | INTEGER | No | `NULL` | Greater than 0 when present | Storage life. NULL for non-perishable items, the normal case. Present because it limits whether stockpiling ahead of planned downtime is viable |
| `specification` | TEXT | No | `Deep groove ball bearing, 25 x 52 x 15 mm, double rubber sealed` | Non-empty when present | Technical description. NULL when the name suffices. **Lets the Decision Agent describe a part precisely enough for a storekeeper to confirm the right one** |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `inventory_location` | Many-to-one | Default storage location |
| Parent | `supplier` | Many-to-one, optional | Primary source, or NULL if internally produced |
| Child | `bill_of_materials` | One-to-many | Consumed by products |
| Referenced by | `machine_type_failure_mode` | One-to-many | Required as the spare part for specific failure modes |
| Referenced by | Operational inventory movements | One-to-many | Stock transactions (operational, out of scope) |

**Business rules**

1. **Every item has a reorder threshold and a safety stock level.** These are stocking policy and are mandatory: an item with no thresholds cannot be monitored for shortage, which makes it invisible to the platform until it runs out.
2. The ordering `safety_stock_qty` ≤ `reorder_point` < `max_stock_qty` must hold. Any violation makes replenishment logic incoherent.
3. **`quantity_on_hand` is not stored here.** It is operational. Availability is determined by comparing the operational quantity against these thresholds.
4. `item_type` must be compatible with the default location's `location_type`.
5. Items with `is_critical_spare = 1` must have `safety_stock_qty` greater than 0. A critical spare with no buffer defeats the purpose of classifying it as critical.
6. Items with `item_type = 'raw_material'`, `component`, or `consumable` must have a `primary_supplier_id`. Externally sourced material without a known source cannot be replenished.
7. Items with a non-NULL `shelf_life_days` should be stored in a temperature-controlled location where the shelf life is heat-sensitive.
8. `unit_cost` is a standard cost, revised periodically. It is not a live market price, and the model deliberately holds no price history — no consumer needs one.
9. An item cannot be soft-retired while active bill of materials rows or failure mode rows reference it.

**Example records**

| inventory_item_code | item_name | item_type | uom | unit_cost | reorder_point | safety_stock_qty | max_stock_qty | lead_time_days | supplier | location | is_critical_spare | abc_class |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `INV-RM-CI250` | Cast Iron Blank CI-250 | `raw_material` | `EA` | 780.00 | 300.00 | 150.00 | 1200.00 | 14 | `SUP-001` | `LOC-RM-A1` | 0 | `A` |
| `INV-RM-AL6061` | Aluminium Billet 6061 | `raw_material` | `EA` | 520.00 | 200.00 | 100.00 | 900.00 | 10 | `SUP-001` | `LOC-RM-A1` | 0 | `B` |
| `INV-RM-AL7075` | Aluminium Billet 7075 | `raw_material` | `EA` | 610.00 | 80.00 | 40.00 | 300.00 | 12 | `SUP-001` | `LOC-RM-A1` | 0 | `C` |
| `INV-CM-IMP-220` | Impeller IMP-220 | `component` | `EA` | 3200.00 | 40.00 | 20.00 | 150.00 | 21 | `SUP-004` | `LOC-RM-A1` | 0 | `A` |
| `INV-CM-SHFT-45` | Pump Shaft 45 mm | `component` | `EA` | 1450.00 | 60.00 | 30.00 | 200.00 | 18 | `SUP-004` | `LOC-RM-A1` | 0 | `B` |
| `INV-CM-SEAL-K12` | Mechanical Seal Kit K12 | `component` | `SET` | 890.00 | 50.00 | 25.00 | 180.00 | 15 | `SUP-004` | `LOC-RM-A1` | 0 | `B` |
| `INV-CP-BRG-6205` | Spindle Bearing 6205-2RS | `spare_part` | `EA` | 2150.00 | 8.00 | 4.00 | 24.00 | 7 | `SUP-002` | `LOC-SP-B2` | 1 | `A` |
| `INV-CP-BELT-V38` | Spindle Drive Belt V-38 | `spare_part` | `EA` | 640.00 | 6.00 | 3.00 | 18.00 | 7 | `SUP-002` | `LOC-SP-B2` | 1 | `B` |
| `INV-CP-SERVO-D4` | Servo Drive Module D4 | `spare_part` | `EA` | 48500.00 | 1.00 | 1.00 | 3.00 | 30 | `SUP-002` | `LOC-SP-B2` | 1 | `A` |
| `INV-CN-COOL-20L` | Cutting Coolant Concentrate | `consumable` | `L` | 145.00 | 200.00 | 100.00 | 600.00 | 5 | `SUP-003` | `LOC-RM-A1` | 0 | `C` |
| `INV-TL-EM-C12` | Carbide End Mill 12 mm | `tooling` | `EA` | 1850.00 | 20.00 | 10.00 | 80.00 | 12 | `SUP-002` | `LOC-TC-01` | 0 | `B` |

Three spare parts, three quite different risk profiles, and the data expresses each distinctly:

- **`INV-CP-BRG-6205`** — moderate cost, seven-day lead time, safety stock of four. A bearing failure is manageable: the part is on the shelf and replaceable within a maintenance window.
- **`INV-CP-BELT-V38`** — cheap but flagged `is_critical_spare`, because a snapped belt stops the machine regardless of the part's price. This is exactly why criticality is stored separately from cost.
- **`INV-CP-SERVO-D4`** — very expensive, **30-day lead time**, and only one unit held. If that single module is consumed, the next failure means a month of downtime. That combination is the most consequential fact in this whole table, and a recommendation involving a servo drive should say so plainly.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Consumes material through bills of materials and draws spare parts on simulated repairs, keeping stock levels moving realistically |
| Monitoring Agent | Compares operational stock against `reorder_point` and `safety_stock_qty` to detect shortages before they stop production |
| Supervisor Agent | **Assembles the inventory element of decision context** — is the required part available, and what is the lead time if not |
| Decision Agent | **Determines whether a repair can actually proceed.** Uses `lead_time_days` to convert a stockout into a concrete delay, `specification` to describe the part precisely, and `is_critical_spare` to set urgency |
| Notification Service | Includes part availability in recommendations so the recipient does not have to check separately |
| Dashboard | Displays stock status against thresholds, highlighting critical spares below safety stock |

---

### 20. `bill_of_materials`

**Purpose**

Defines the materials consumed to produce one unit of a product, with quantities, scrap allowance, criticality, and approved substitutes. It is what links production activity to material consumption.

**Business description**

A bill of materials is the recipe. One gearbox housing consumes one cast iron blank, a fraction of a litre of coolant, and a fraction of an end mill's usable life. One pump assembly consumes an impeller, a shaft, a seal kit, and an aluminium billet.

Two purposes justify this entity, and both have named consumers.

**Material consumption for the simulator.** Without a bill of materials, a simulator producing 24 units an hour would have no basis for depleting stock. Inventory would stay static, the Monitoring Agent would never see a shortage develop, and inventory monitoring — a listed core feature — would have nothing to monitor. The bill of materials is what makes material flow real.

**Shortage impact for the Decision Agent.** When a component runs low, the platform needs to know *which products stop*. The bill of materials answers it directly: if the impeller is short, pump assemblies stop. `is_critical_component` distinguishes materials that halt production from those that merely degrade it — running low on coolant slows things down, running out of impellers stops the line.

`substitute_inventory_item_id` deserves its place. Approved alternate materials are routine in manufacturing — a second aluminium grade, an equivalent seal kit — and they turn a shortage from a stoppage into an inconvenience. Recording the approved substitute lets the Decision Agent propose it. Without it, the platform would report a shortage that the plant already has a documented answer for, which erodes trust in its recommendations.

`scrap_allowance_pct` reflects that consumption exceeds theoretical usage: some material is lost to scrap and setup. A 2 % allowance on a blank means 102 blanks are consumed per 100 good housings. Ignoring it would make every material projection systematically optimistic.

**Note on absent BOM versioning.** A conventional ERP splits this into a versioned header (`bom_id`, `version`, `effective_from`, `is_active`) and component lines. That structure was considered and **deliberately excluded**. Versioning exists so an ERP can reproduce the exact recipe used for a batch produced two years ago — a genuine requirement for warranty and recall traceability, and one **no FactoryFlow AI component has.** Every consumer here asks the same question: *what does this product consume now?* Adding a header table would introduce a join, a version-resolution rule, and an active-version constraint, all to serve a use case outside the project's scope. `effective_from_date` on the line provides an audit trail of when a recipe changed, which is as much history as any consumer needs. If batch-level traceability is ever required, the header is added then, with real requirements to shape it.

**Note on absent unit of measure.** Quantity is expressed in the referenced item's `unit_of_measure`. Repeating it here would allow the two to disagree, which is precisely the class of bug that dimensional mistakes come from.

**Primary key**

`bill_of_materials_id` — surrogate integer, with a **composite unique constraint on (`product_id`, `inventory_item_id`)**.

One line per product-material pair. No business code: nobody on the shop floor names an individual BOM line.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `product_id` | INTEGER (FK) | Yes | `1` | References active `product`; unique with `inventory_item_id` | The product being made |
| `inventory_item_id` | INTEGER (FK) | Yes | `1` | References active `inventory_item`; unique with `product_id`; `item_type` must not be `finished_good` | The material consumed |
| `quantity_per_unit` | NUMERIC(12,4) | Yes | `1.0000` | Greater than 0 | Quantity consumed per unit of product, in the item's own unit of measure. Fractional values are normal for consumables and tooling |
| `scrap_allowance_pct` | NUMERIC(5,2) | Yes | `2.00` | Between 0 and 50; default 0 | Extra consumption expected from scrap and setup. **Keeps material projections honest** rather than systematically optimistic |
| `is_critical_component` | INTEGER | Yes | `1` | Default 0 | Whether absence halts production of this product. **Distinguishes a stoppage from a slowdown** |
| `substitute_inventory_item_id` | INTEGER (FK) | No | `3` | References active `inventory_item`; must differ from `inventory_item_id` | Approved alternate material. NULL when none exists. **Turns a shortage into a documented workaround** the Decision Agent can propose |
| `effective_from_date` | DATE | Yes | `2019-03-01` | On or after `product.introduced_date` | When this line became valid. Provides an audit trail of recipe changes without a versioned header |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `product` | Many-to-one | Each line belongs to one product |
| Parent | `inventory_item` | Many-to-one | Each line references one material |
| Parent | `inventory_item` (as substitute) | Many-to-one, optional | Approved alternate material |

Together the first two resolve **product ↔ inventory_item as many-to-many**. Note that `inventory_item` is referenced twice from this entity — once as the material, once as its substitute. Both point to the same table, which is legitimate and creates no cycle: `inventory_item` does not reference `bill_of_materials`.

**Business rules**

1. One line per product and material pair.
2. Every active product must have at least one active bill of materials line. A product consuming nothing is not manufactured.
3. `inventory_item_id` must not reference an item of type `finished_good`. Sub-assembly structures are excluded from this model: no consumer needs multi-level explosion, and adding it would introduce recursive traversal for no gain.
4. `substitute_inventory_item_id` must differ from `inventory_item_id` and should share the same `unit_of_measure`. A substitute measured differently would produce wrong quantity arithmetic.
5. A substitute may only be proposed by the Decision Agent when the substitute itself has stock available. Recommending a substitute that is also short is worse than reporting the original shortage, because it wastes a check the manager then has to redo.
6. Items with `is_critical_component = 1` should also carry `safety_stock_qty` greater than 0 on `inventory_item`. A critical component with no buffer is a stoppage waiting for a delivery delay.
7. `scrap_allowance_pct` above 10 % warrants review. Consumables and tooling can legitimately exceed it; a raw material at 15 % suggests a process problem, or a data error.
8. Effective dates provide an audit trail only. There is no version resolution: the active lines for a product are its current recipe.
9. A line cannot be soft-retired if it is the only active line for its product.

**Example records**

| product | inventory_item | quantity_per_unit | scrap_allowance_pct | is_critical_component | substitute |
|---|---|---|---|---|---|
| `PRD-GH-100` | `INV-RM-CI250` | 1.0000 | 2.00 | 1 | NULL |
| `PRD-GH-100` | `INV-CN-COOL-20L` | 0.0800 | 0.00 | 0 | NULL |
| `PRD-GH-100` | `INV-TL-EM-C12` | 0.0150 | 0.00 | 0 | NULL |
| `PRD-PMP-220` | `INV-CM-IMP-220` | 1.0000 | 0.50 | 1 | NULL |
| `PRD-PMP-220` | `INV-CM-SHFT-45` | 1.0000 | 0.50 | 1 | NULL |
| `PRD-PMP-220` | `INV-CM-SEAL-K12` | 1.0000 | 1.00 | 1 | NULL |
| `PRD-PMP-220` | `INV-RM-AL6061` | 1.0000 | 3.00 | 1 | `INV-RM-AL7075` |
| `PRD-VB-075` | `INV-RM-AL6061` | 1.0000 | 3.00 | 1 | `INV-RM-AL7075` |
| `PRD-VB-075` | `INV-CN-COOL-20L` | 0.0500 | 0.00 | 0 | NULL |
| `PRD-VB-075` | `INV-TL-EM-C12` | 0.0110 | 0.00 | 0 | NULL |

Several design points are visible:

- **Fractional quantities are normal.** One housing consumes 0.015 of an end mill — meaning roughly 67 housings per tool. That is how tooling consumption actually works, and expressing it as a fraction is what lets the simulator deplete tooling stock at a believable rate.
- **The aluminium substitute appears on two products.** Both the pump assembly and the valve body can fall back to grade 7075 when 6061 is short. A single shortage therefore has a single documented answer covering two products, and the Decision Agent can state it once.
- **Criticality separates stoppages from slowdowns.** The cast iron blank is critical: no blank, no housing. Coolant is not: running low slows production and raises tool wear but does not halt the line. That distinction changes the urgency of a shortage entirely.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | **Drives material consumption.** Depletes stock as units are produced, including scrap allowance, so inventory levels move realistically and shortages can actually develop |
| Monitoring Agent | Detects material shortages that will stop specific products, not just generic low-stock conditions |
| Supervisor Agent | Determines which products and lines a shortage affects when assembling context |
| Decision Agent | States which products stop, distinguishes critical from non-critical shortage, and **proposes approved substitutes** rather than reporting a problem the plant already has an answer for |
| Dashboard | Shows material coverage per product |

---

### 21. `supplier`

**Purpose**

Represents an external source of materials, components, or spare parts. It carries the sourcing facts that determine how quickly a shortage can be resolved.

**Business description**

Suppliers provide what the plant does not make: castings, machined components, bearings and drive parts, coolant and tooling.

For FactoryFlow AI, a supplier is primarily an answer to *"how fast can we get the part, and can we trust that estimate?"* Three attributes carry that.

**`standard_lead_time_days`** is the normal replenishment time. It is what turns "the part is out of stock" into "the part is out of stock and the next one arrives in seven days" — a statement a manager can plan against.

**`expedited_lead_time_days`** is the recovery lever. Most suppliers will rush an order at a premium. A bearing that normally takes seven days may arrive in two. When a critical spare is unavailable, expedited supply is often the single most valuable element of a recovery plan, and it is a fact the platform must hold rather than assume.

**`on_time_delivery_pct`** qualifies the promise. A supplier at 97 % on-time delivery makes a seven-day lead time reliable planning input. A supplier at 89 % makes the same figure a hope. Recording it lets the Decision Agent hedge honestly — *"nominally five days, though this supplier delivers on time about 89 % of the time"* — instead of presenting a lead time as a guarantee.

**Note on `on_time_delivery_pct` and the master/operational boundary.** This figure looks derived, and it would be if computed from live receipts. It is included here as a **periodically maintained scorecard value**, which is exactly how real ERPs hold it: purchasing reviews supplier performance quarterly and updates the rating. It changes a few times a year, set by a human, which places it squarely on the master side of every test in §2.1. It is explicitly *not* recomputed from operational data by the platform.

**Note on absent multi-sourcing.** A `supplier_item` junction — several suppliers per item, each with its own lead time and price — is the conventional ERP structure and was **deliberately excluded**. It would enable alternate-source recommendations, which is genuinely useful, but it adds a table, a join, and a preferred-source resolution rule to serve a use case no current consumer has. `inventory_item.primary_supplier_id` answers the question the Decision Agent actually asks: *who do we call about this part?* Multi-sourcing is recorded in Part V as a Phase 4 candidate, to be added when a consumer needs it.

**Primary key**

`supplier_id` — surrogate integer, with `supplier_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `supplier_code` | VARCHAR(10) | Yes | `SUP-002` | Unique; matches `^SUP-[0-9]{3}$` | Vendor number used on purchase orders |
| `supplier_name` | VARCHAR(150) | Yes | `PrecisionBear Industrial Supplies` | Non-empty | Legal or trading name. Named in recommendations that involve ordering |
| `supplier_type` | TEXT + CHECK | Yes | `spare_part` | One of: `raw_material`, `component`, `spare_part`, `consumable`, `service` | What the supplier provides. Groups vendors and sanity-checks item-to-supplier assignment |
| `contact_person` | VARCHAR(100) | No | `Sales Desk` | Non-empty when present | Named contact. NULL when only a general channel exists |
| `contact_email` | VARCHAR(150) | No | `orders@precisionbear.example` | Valid email format when present | Ordering address. NULL when orders are placed by phone or portal |
| `contact_phone` | VARCHAR(20) | No | `+912055512345` | E.164 format when present | Phone contact. **Needed for an expedited order**, which is normally placed by phone rather than email |
| `city` | VARCHAR(80) | Yes | `Pune` | Non-empty | Supplier location. Correlates with realistic transit time |
| `country_code` | CHAR(2) | Yes | `IN` | ISO 3166-1 alpha-2 | Country. Distinguishes domestic from import supply, which affects lead time reliability |
| `standard_lead_time_days` | INTEGER | Yes | `7` | Greater than or equal to 0 | Normal replenishment time. **Converts a stockout into a dated delay** |
| `expedited_lead_time_days` | INTEGER | No | `2` | Greater than or equal to 0; less than `standard_lead_time_days` | Rush order time. NULL when the supplier does not expedite. **The primary recovery lever for a critical spare shortage** |
| `reliability_rating` | NUMERIC(2,1) | Yes | `4.6` | Between 0.0 and 5.0 | Overall vendor rating covering quality and service. A composite judgement maintained by purchasing |
| `on_time_delivery_pct` | NUMERIC(5,2) | No | `97.10` | Between 0 and 100 when present | Delivery performance from the periodic scorecard. NULL for new suppliers with no history. **Qualifies how much to trust the lead time** |
| `is_approved_vendor` | INTEGER | Yes | `1` | Default 1 | Whether the supplier is currently approved for purchasing. `0` blocks ordering entirely, regardless of lead time |
| `contract_expiry_date` | DATE | No | `2028-01-31` | In the future for active suppliers | When the supply agreement lapses. NULL for spot purchasing. An expired contract may mean renegotiation before an order can be placed |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Child | `inventory_item` | One-to-many | A supplier is the primary source for several items |

`supplier` has **no outgoing foreign keys**. It is an independent reference entity at the base of the dependency graph.

**Business rules**

1. An inventory item has at most one primary supplier. Multi-sourcing is out of scope; see the note above and Part V.
2. `expedited_lead_time_days`, when present, must be less than `standard_lead_time_days`. An expedited option no faster than standard is not an expedited option.
3. Only suppliers with `is_approved_vendor = 1` may be recommended for ordering. An unapproved vendor cannot be purchased from, so recommending them wastes the manager's time.
4. `supplier_type` should be consistent with the item types the supplier actually provides. A `consumable` supplier appearing as the primary source for a spare part warrants review.
5. `on_time_delivery_pct` is a maintained scorecard figure, refreshed on a purchasing review cycle. It is **never** recomputed by the platform from operational receipts.
6. Suppliers with `on_time_delivery_pct` below 90 % should have their stated lead times treated as indicative. The Decision Agent is expected to qualify recommendations that depend on them rather than presenting the lead time as firm.
7. A supplier whose `contract_expiry_date` has passed should be reviewed before being recommended, since ordering may require renegotiation.
8. A supplier cannot be soft-retired while active items designate them as primary source — an item left with no source cannot be replenished at all.

**Example records**

| supplier_code | supplier_name | supplier_type | city | country | standard_lead_time_days | expedited_lead_time_days | reliability_rating | on_time_delivery_pct | is_approved_vendor | contract_expiry_date |
|---|---|---|---|---|---|---|---|---|---|---|
| `SUP-001` | Sundaram Castings Pvt Ltd | `raw_material` | Coimbatore | `IN` | 14 | 7 | 4.2 | 94.50 | 1 | 2027-03-31 |
| `SUP-002` | PrecisionBear Industrial Supplies | `spare_part` | Pune | `IN` | 7 | 2 | 4.6 | 97.10 | 1 | 2028-01-31 |
| `SUP-003` | ChemLube Solutions | `consumable` | Chennai | `IN` | 5 | 2 | 3.8 | 89.00 | 1 | 2026-12-31 |
| `SUP-004` | Hydroflow Components | `component` | Bengaluru | `IN` | 21 | 10 | 4.0 | 91.20 | 1 | 2027-06-30 |

The contrast between `SUP-002` and `SUP-003` shows why reliability is stored alongside lead time. The bearing supplier quotes seven days and delivers on time 97 % of the time — dependable planning input. The coolant supplier quotes five days but delivers on time only 89 % of the time, so a plan built on five days carries real risk of slipping. Same kind of number, quite different confidence, and the platform can only convey that distinction if both are recorded.

`SUP-004`'s 21-day standard lead time on pump components is the constraint that makes the pump assembly line's material buffer genuinely important — a three-week replenishment cycle leaves little room to react.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Replenishes stock on realistic lead times rather than instantly |
| Monitoring Agent | Uses lead time to judge how far ahead of a stockout a shortage warning must fire |
| Supervisor Agent | Includes sourcing and lead time in the inventory element of decision context |
| Decision Agent | **Builds the material side of recovery plans.** States when a part will arrive, whether expedited supply is available and how much faster, and qualifies the estimate using `on_time_delivery_pct` |
| Notification Service | Includes supplier contact details so an expedited order can be placed without a separate lookup |
| Dashboard | Shows sourcing exposure and lead-time risk on critical items |

---

### 22. `customer`

**Purpose**

Represents an external buyer of the plant's finished goods. It supplies the commercial weighting that turns lost output into a prioritised business consequence.

**Business description**

Customers buy what the factory makes. This model holds only what is needed to assess the business impact of a production disruption — no sales history, no credit terms, no order pipeline.

`priority_tier` is the entity's reason for existing. When two lines are at risk simultaneously and one is producing for a Gold-tier customer, the platform must be able to prioritise. Without customer tiering, the Decision Agent could rank by units or by margin but not by **relationship consequence** — and relationship consequence is often what actually drives the decision. Losing a day of output for a strategic customer under a long-term contract is not equivalent to losing a day for a spot buyer, even when the revenue is identical.

`late_delivery_penalty_per_day` makes disruption cost concrete and contractual. Lost margin is an internal estimate; a contractual penalty is a real cash consequence with a number attached. When both are available, the Decision Agent can state total exposure rather than only opportunity cost, which is a substantially more persuasive case for stopping a line to prevent a failure.

`contractual_otd_target_pct` records the on-time delivery commitment. A customer with a 98 % contractual target has very little slack: a single missed shipment can breach the agreement. A customer at 92 % has room to absorb one late delivery. Same delay, different consequence.

**Note on the relationship to products.** Customers connect to products only through operational production orders. There is deliberately no master `customer_product` table: which customer buys which product is a commercial fact that changes with every order, making it transactional rather than static. Modelling it as master data would produce a table that is either constantly wrong or constantly updated — and no consumer needs it, because the Decision Agent learns the customer from the affected order, not from a master assignment.

**Primary key**

`customer_id` — surrogate integer, with `customer_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `customer_code` | VARCHAR(10) | Yes | `CUS-001` | Unique; matches `^CUS-[0-9]{3}$` | Customer account number used on orders and shipping documents |
| `customer_name` | VARCHAR(150) | Yes | `Apex Drivetrain Systems` | Non-empty | Legal or trading name. Named in business impact statements |
| `priority_tier` | TEXT + CHECK | Yes | `gold` | One of: `gold`, `silver`, `bronze` | **Commercial importance of the relationship.** The primary input when prioritising between competing disruptions |
| `industry_sector` | VARCHAR(80) | No | `Automotive` | Non-empty when present | Sector served. NULL when not classified. Provides context for how tolerant the customer is of delay — automotive assembly lines have far less slack than general industrial buyers |
| `city` | VARCHAR(80) | Yes | `Pune` | Non-empty | Delivery location. Correlates with transit time, which affects how much of a delay can be recovered in shipping |
| `country_code` | CHAR(2) | Yes | `IN` | ISO 3166-1 alpha-2 | Country. Distinguishes domestic from export, which changes recovery options |
| `contact_person` | VARCHAR(100) | No | `Purchasing Desk` | Non-empty when present | Named contact. NULL when only a general channel exists |
| `contact_email` | VARCHAR(150) | No | `purchasing@apexdrive.example` | Valid email format when present | Contact address. NULL when communication runs through an account manager. Relevant when a recovery plan includes proactively informing the customer |
| `late_delivery_penalty_per_day` | NUMERIC(12,2) | No | `12000.00` | Greater than or equal to 0 when present | Contractual penalty per day late, in `plant.currency_code`. NULL when no penalty clause exists. **Converts a delay into a contractual cash consequence** |
| `contractual_otd_target_pct` | NUMERIC(5,2) | No | `98.00` | Between 0 and 100 when present | Agreed on-time delivery commitment. NULL when uncontracted. **Indicates how little slack exists** before the agreement is breached |
| `annual_order_value` | NUMERIC(14,2) | No | `42000000.00` | Greater than 0 when present | Approximate yearly business value. NULL for new accounts. Quantifies the relationship at risk, giving `priority_tier` a magnitude rather than only a label |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Referenced by | Operational production orders | One-to-many | Orders reference the customer they fulfil (operational, out of scope) |

`customer` has **no outgoing foreign keys and no master-data children.** It is a pure reference entity, consumed exclusively through operational order data. This is worth stating explicitly: it is the only entity in the model whose entire value is realised through the operational layer, and it is included in master data because customer attributes are static while orders are not.

**Business rules**

1. Every operational production order references exactly one customer. Make-to-stock production is not modelled: it would require a nullable customer on orders and a separate impact path, and the platform's business-impact reasoning depends on there being an identifiable customer behind the output.
2. `priority_tier` drives prioritisation between simultaneous disruptions. Gold outranks silver, silver outranks bronze, applied consistently rather than case by case.
3. Customers with `late_delivery_penalty_per_day` populated allow **quantified** delay impact. Where it is NULL, the Decision Agent must fall back to lost margin and say so, rather than implying a penalty exists.
4. Customers with `contractual_otd_target_pct` at or above 98 % are treated as low-slack accounts. A single missed shipment materially threatens the agreement, and recommendations affecting them warrant higher urgency at the same technical severity.
5. There is no master customer-to-product assignment. The link is established per order.
6. Customer contact details are used only for recovery plans that include proactively notifying the customer. The platform never contacts a customer automatically — that is a commercial decision belonging to a human, consistent with the human-in-the-loop principle.
7. `annual_order_value` is an approximate maintained figure, refreshed periodically. It is not computed from operational orders.
8. A customer cannot be soft-retired while open operational orders reference them.

**Example records**

| customer_code | customer_name | priority_tier | industry_sector | city | country | late_delivery_penalty_per_day | contractual_otd_target_pct | annual_order_value |
|---|---|---|---|---|---|---|---|---|
| `CUS-001` | Apex Drivetrain Systems | `gold` | Automotive | Pune | `IN` | 12000.00 | 98.00 | 42000000.00 |
| `CUS-002` | Marine Pumps International | `silver` | Marine Equipment | Kochi | `IN` | 8000.00 | 95.00 | 26500000.00 |
| `CUS-003` | Hydroline Controls | `bronze` | Industrial Automation | Ahmedabad | `IN` | NULL | 92.00 | 9800000.00 |

These three rows are what allow the Decision Agent to prioritise correctly when two lines are simultaneously at risk:

- **`CUS-001`** — Gold tier, automotive, 98 % on-time commitment, 12,000 per day penalty, largest account. A delay here has a contractual cost, threatens a tight commitment, and puts the biggest relationship at risk. Any disruption affecting this customer's order outranks most alternatives.
- **`CUS-003`** — Bronze tier, no penalty clause, 92 % target, smallest account. A delay costs lost margin and nothing contractual.

The practical consequence: a 60 % failure risk on Line 01 producing for Apex may legitimately outrank an 85 % risk on Line 03 producing for Hydroline. That inversion — lower technical risk, higher priority — is precisely the business-aware behavior the platform exists to provide, and it is impossible without customer data.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Generates production orders against realistic customers with differing priorities |
| Supervisor Agent | **Escalation weighting.** Combines customer tier with line criticality and predicted risk to decide what warrants LLM reasoning |
| Decision Agent | **Core business impact input.** Names the customer at risk, quantifies delay cost via the penalty clause, and prioritises across competing disruptions using tier, contractual slack, and account value |
| Notification Service | Includes customer impact in recommendations, which is what makes urgency legible to a manager |
| Dashboard | Shows which customer orders each line is serving and the exposure at risk |
| Future reporting | Enables analysis of disruption impact by customer and tier |

---

## Group F — Reliability & Policy

The failure taxonomy the platform reasons with, and the tunable configuration that governs its behavior. This group is what makes FactoryFlow AI's judgements **data-driven and explainable** rather than hardcoded.

Everything here shares one property: it is set by humans and it can be changed without touching code. A maintenance engineer retunes a threshold. A planner adjusts an escalation cut-off. A manager updates a downtime cost rate. In each case the platform's behavior changes, and the change is visible in a table where anyone can inspect it — which is what allows a recommendation to explain *why* it was produced.

---

### 23. `failure_severity_level`

**Purpose**

Defines the severity scale used throughout the platform, with the response commitment and escalation policy attached to each level. It is the shared vocabulary for "how bad is this?"

**Business description**

Severity is the single most widely referenced concept in the platform. Failure categories carry a default severity. Failure modes carry a type-specific severity. Threshold rules produce warning and critical severities. Notification recipients filter by minimum severity. Escalation policy is expressed in severity terms.

Making severity an entity rather than a bare enumerated string is what allows each level to carry its own **policy**:

**`target_response_time_minutes`** is the response commitment. Critical means fifteen minutes, high means an hour, medium means four hours. This is what converts a severity label into a deadline the Decision Agent can state and the Notification Service can escalate against.

**`requires_line_stop`** encodes whether the level implies halting production. Only the most severe level carries it, and even then the platform *recommends* the stop — it never executes one. The flag tells the Decision Agent that stopping the line belongs in the recommended action, and tells the Notification Service the message must reach somebody with `can_authorize_line_stop`.

**`max_acknowledgement_minutes`** drives the escalation clock. If a critical recommendation is not acknowledged within fifteen minutes, the platform moves to the next recipient in `escalation_order`. Without a per-level acknowledgement window, escalation would need either a single global timeout — wrong for both ends of the scale — or logic hardcoded per severity.

`display_color_hex` is presentational, and it is here deliberately. Severity colour needs to be identical on the dashboard, in email bodies, and in any future report. Defining it once in the data means those surfaces cannot drift apart, which is a small consistency win for a single column.

**Primary key**

`failure_severity_level_id` — surrogate integer, with `failure_severity_level_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `failure_severity_level_code` | VARCHAR(8) | Yes | `SEV-1` | Unique; matches `^SEV-[0-9]$` | Severity identifier used across configuration and in notification subject lines |
| `severity_name` | VARCHAR(40) | Yes | `Critical` | Non-empty | Human-readable label. Appears verbatim in recommendations, so it must convey urgency plainly |
| `severity_rank` | INTEGER | Yes | `1` | Unique; between 1 and 9; **1 is most severe** | Ordering for comparison. A lower rank outranks a higher one. Makes "at or above this severity" a simple numeric test |
| `description` | TEXT | Yes | `Imminent failure or safety risk requiring immediate intervention` | Non-empty | What the level means operationally. **Given to the Decision Agent so severity assignment is grounded in a definition** rather than the model's own interpretation of a label |
| `target_response_time_minutes` | INTEGER | No | `15` | Greater than 0 when present | Committed time to respond. NULL for informational levels needing no response. **Turns a severity label into a stated deadline** |
| `requires_line_stop` | INTEGER | Yes | `1` | Default 0 | Whether the level implies halting production. The platform recommends; it never stops a line itself |
| `requires_immediate_escalation` | INTEGER | Yes | `1` | Default 0 | Whether to notify without waiting for batching or quiet-hours windows |
| `requires_manager_acknowledgement` | INTEGER | Yes | `1` | Default 0 | Whether a human must confirm receipt. **The audit trail of human-in-the-loop decision making** |
| `max_acknowledgement_minutes` | INTEGER | No | `15` | Greater than 0 when present; required when `requires_manager_acknowledgement` is 1 | Time before escalating to the next recipient. NULL when acknowledgement is not required |
| `display_color_hex` | CHAR(7) | Yes | `#C62828` | Matches `^#[0-9A-F]{6}$` | Presentation colour. Defined once so dashboard, email, and reports cannot diverge |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Child | `failure_category` | One-to-many | Categories carry a default severity |
| Child | `machine_type_failure_mode` | One-to-many | Failure modes carry a type-specific severity |
| Child | `alert_threshold_rule` | One-to-many | Rules map warning and critical breaches to severities |
| Child | `notification_recipient` | One-to-many | Recipients filter by minimum severity |

`failure_severity_level` has **no outgoing foreign keys**. It is an independent lookup at the base of the dependency graph, and it is the most widely referenced entity in the model.

**Business rules**

1. `severity_rank` is unique, and **rank 1 is the most severe.** The direction is stated explicitly because reversing it silently inverts every comparison in the platform.
2. Severity levels are stable reference data. Adding a level mid-project would require re-evaluating every threshold rule, failure category, and recipient filter that references the scale.
3. `requires_line_stop = 1` is expected only at the most severe level. A recommendation carrying it must be routed to a recipient whose role has `can_authorize_line_stop = 1`.
4. `max_acknowledgement_minutes` is required when `requires_manager_acknowledgement` is 1. An acknowledgement requirement with no timeout would stall escalation indefinitely.
5. `target_response_time_minutes` must increase as severity decreases. A medium-severity condition cannot demand a faster response than a critical one.
6. At least one active `notification_recipient` must be configured for the most severe level. A critical condition that reaches nobody is the worst possible failure mode of the platform itself.
7. Team commitments (`maintenance_team.target_response_time_minutes`) should be consistent with the severity levels they are expected to serve. A team with a 120-minute target cannot meet a 15-minute critical commitment, and a mismatch is a coverage gap worth surfacing.
8. Severity levels are never deleted. Historical events and recommendations reference them permanently.

**Example records**

| code | severity_name | rank | target_response_time_minutes | requires_line_stop | requires_immediate_escalation | requires_manager_acknowledgement | max_acknowledgement_minutes | display_color_hex |
|---|---|---|---|---|---|---|---|---|
| `SEV-1` | Critical | 1 | 15 | 1 | 1 | 1 | 15 | `#C62828` |
| `SEV-2` | High | 2 | 60 | 0 | 1 | 1 | 30 | `#EF6C00` |
| `SEV-3` | Medium | 3 | 240 | 0 | 0 | 1 | 120 | `#F9A825` |
| `SEV-4` | Low | 4 | 1440 | 0 | 0 | 0 | NULL | `#558B2F` |
| `SEV-5` | Informational | 5 | NULL | 0 | 0 | 0 | NULL | `#1565C0` |

The scale is deliberately graduated across every dimension at once. Critical demands a response in 15 minutes, implies a line stop, escalates immediately, and must be acknowledged within 15 minutes. Informational demands nothing. The middle levels step between them consistently — response time roughly quadruples at each step down, which matches how maintenance organizations actually triage.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Monitoring Agent | Assigns severity to detected events using threshold rule mappings |
| Prediction Agent | Maps failure probability onto the severity scale to produce risk classification |
| Supervisor Agent | Compares event severity against the escalation cut-off in `business_rule` to decide what warrants LLM reasoning |
| Decision Agent | Uses `description` to ground severity assignment in a definition, and `target_response_time_minutes` to state a concrete deadline |
| Notification Service | **Core routing input.** Filters recipients by minimum severity, applies immediate escalation, and runs the acknowledgement clock |
| Dashboard | Colours and orders risk consistently with every other surface |

---

### 24. `failure_category`

**Purpose**

Defines the controlled vocabulary of failure modes the platform can reason about. It is what constrains the Decision Agent's root-cause hypotheses to a known, reviewable set of possibilities.

**Business description**

Machines fail in recognisable, categorisable ways: bearings degrade, spindles seize, motors draw excess current, tools wear and break, lubrication systems fail, belts stretch, alignments drift, sensors fail, controls fault, pneumatics leak, material jams.

This entity is the platform's failure vocabulary, and it exists for a reason that goes to the heart of the architecture. **The Decision Agent must produce a root-cause hypothesis** — §16.5 of the companion overview makes it a mandatory element of every recommendation. Left to free-form generation, an LLM will produce plausible-sounding causes that may not correspond to how the equipment actually fails. Constraining root cause to a defined vocabulary means the hypothesis is always drawn from a set a maintenance engineer has reviewed and endorsed.

This is one of the most important design decisions in the model. It is what converts LLM root-cause reasoning from open-ended generation into **classification within a validated set** — inspectable, consistent between recommendations, and defensible when a manager challenges it.

`required_specialization` maps a failure directly to a maintenance discipline, using the same vocabulary as `maintenance_team.specialization` and `machine_category.primary_maintenance_specialization`. Three entities deliberately share one vocabulary, which makes matching a failure to a qualified team a direct comparison rather than a chain of inference.

`has_safety_implication` raises urgency independently of production impact. A material jam may cost little output but presents an injury risk during clearing. A recommendation involving a safety-implicated failure warrants elevated urgency even when the business impact is modest, and encoding it as data means that judgement is applied consistently rather than depending on how the LLM happens to frame it.

**Note on absent repair duration.** A `typical_repair_duration_minutes` column here was considered and removed. Repair duration is held in exactly two places with a stated precedence: `machine_type_failure_mode.estimated_repair_duration_minutes` for a specific failure on a specific machine model, and `machine_type.mttr_minutes` as the fallback when the failure mode is not yet identified. A third category-level average would be a middle layer nobody would know when to use — and three sources for one number is how estimates quietly diverge.

**Primary key**

`failure_category_id` — surrogate integer, with `failure_category_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `failure_category_code` | VARCHAR(10) | Yes | `FC-BRG` | Unique; matches `^FC-[A-Z]{3,6}$` | Failure mode identifier used in predictions, recommendations, and maintenance records |
| `category_name` | VARCHAR(100) | Yes | `Bearing Degradation` | Non-empty | Failure mode name. **Appears verbatim as the root cause in recommendations**, so it must read as an engineer would describe it |
| `failure_domain` | TEXT + CHECK | Yes | `mechanical` | One of: `mechanical`, `electrical`, `thermal`, `tooling`, `hydraulic`, `pneumatic`, `instrumentation`, `automation`, `process` | Physical domain of the failure. Lets the Decision Agent group correlated evidence — several mechanical parameters drifting together supports a mechanical hypothesis over an electrical one |
| `default_severity_level_id` | INTEGER (FK) | Yes | `2` | References active `failure_severity_level` | Baseline severity for this failure mode. Overridable per machine type on `machine_type_failure_mode` |
| `required_specialization` | TEXT + CHECK | Yes | `mechanical` | One of: `mechanical`, `electrical`, `automation`, `general`; same vocabulary as `maintenance_team.specialization` | **The discipline needed to repair it.** Matched directly against team specialization to identify a qualified responder |
| `requires_spare_part` | INTEGER | Yes | `1` | Default 0 | Whether repair typically consumes a part. `1` means the recovery plan must include a stock check before the repair can be committed to |
| `has_safety_implication` | INTEGER | Yes | `0` | Default 0 | Whether the failure or its repair presents a safety risk. **Raises urgency independently of production impact** |
| `description` | TEXT | Yes | `Progressive wear of rolling element bearings, typically indicated by rising vibration at constant speed` | Non-empty | Definition of the failure mode and how it presents. **Grounds the Decision Agent's hypothesis in engineering fact** rather than in the model's own associations with the name |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `failure_severity_level` | Many-to-one | Default severity for the category |
| Child | `machine_type_failure_mode` | One-to-many | A category applies to several machine types |

**Business rules**

1. Failure categories are a **controlled vocabulary.** The Decision Agent must select a root cause from this set. It may express uncertainty, rank alternatives, or state that no category fits — but it may not invent a category, because an invented cause cannot be validated, matched to a specialization, or linked to a spare part.
2. `required_specialization` must be drawn from the shared vocabulary used by `maintenance_team.specialization` and `machine_category.primary_maintenance_specialization`. All three must be maintained together.
3. Categories with `requires_spare_part = 1` should have a `required_inventory_item_id` populated on their `machine_type_failure_mode` rows. Otherwise the platform knows a part is needed but not which one, which is not actionable.
4. Categories with `has_safety_implication = 1` carry elevated urgency at any given severity, and the Decision Agent is expected to state the safety consideration explicitly in the recommendation.
5. `description` is mandatory and substantive. It is supplied to the Decision Agent as grounding, so a thin description directly degrades hypothesis quality — this is one of the few places where documentation text is a functional input rather than commentary.
6. Every category should have at least one `machine_type_failure_mode` row. A category applicable to no machine type in the plant is dead vocabulary.
7. New categories are added deliberately, with an engineering review, not to accommodate a single unexplained event.
8. Categories are never deleted. Historical predictions and recommendations reference them permanently.

**Example records**

| code | category_name | failure_domain | default_severity | required_specialization | requires_spare_part | has_safety_implication |
|---|---|---|---|---|---|---|
| `FC-SPDL` | Spindle Failure | `mechanical` | `SEV-1` | `mechanical` | 1 | 1 |
| `FC-OVHT` | Thermal Overload | `thermal` | `SEV-1` | `mechanical` | 0 | 1 |
| `FC-BRG` | Bearing Degradation | `mechanical` | `SEV-2` | `mechanical` | 1 | 0 |
| `FC-MOTR` | Drive Motor Fault | `electrical` | `SEV-2` | `electrical` | 1 | 0 |
| `FC-LUBR` | Lubrication System Failure | `mechanical` | `SEV-2` | `mechanical` | 1 | 0 |
| `FC-CTRL` | Control System Fault | `automation` | `SEV-2` | `automation` | 0 | 0 |
| `FC-TOOL` | Excessive Tool Wear or Breakage | `tooling` | `SEV-3` | `mechanical` | 1 | 0 |
| `FC-BELT` | Belt or Chain Wear | `mechanical` | `SEV-3` | `mechanical` | 1 | 0 |
| `FC-ALGN` | Misalignment or Imbalance | `mechanical` | `SEV-3` | `mechanical` | 0 | 0 |
| `FC-SENS` | Sensor or Instrumentation Fault | `instrumentation` | `SEV-3` | `automation` | 1 | 0 |
| `FC-PNEU` | Pneumatic Pressure Loss | `pneumatic` | `SEV-3` | `mechanical` | 0 | 0 |
| `FC-JAM` | Material Jam or Blockage | `process` | `SEV-3` | `mechanical` | 0 | 1 |

Twelve categories cover the realistic failure space of this plant without becoming a taxonomy nobody maintains. Two rows illustrate why the flags are separate concerns:

- **`FC-JAM`** is only medium severity and needs no spare part, but it is safety-implicated: clearing a jam means reaching into a machine. A recommendation should say so, even though the production impact is small.
- **`FC-SENS`** is a mechanical-sounding problem that requires the **automation** specialization, because instrumentation faults are a controls discipline. Without `required_specialization` as explicit data, a plausible-but-wrong mechanical team assignment would be the natural inference.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Generates failure scenarios drawn from realistic, catalogued failure modes rather than generic breakdowns |
| Prediction Agent | Classifies predicted failures into named categories, so a probability arrives attached to a *kind* of failure |
| Supervisor Agent | Uses `required_specialization` to identify qualified teams and `requires_spare_part` to know whether a stock check is needed |
| Decision Agent | **The controlled vocabulary for root-cause hypotheses.** Uses `description` as engineering grounding, `failure_domain` to group correlated evidence, and `has_safety_implication` to elevate urgency |
| Notification Service | States the failure mode in plain language a recipient recognises |
| Dashboard | Groups and reports risk by failure mode and domain |
| Future reporting | Enables failure mode analysis across machines and time — the foundation of any reliability programme |

---

### 25. `machine_type_failure_mode`

**Purpose**

Declares which failure modes are plausible on which machine types, and for each pairing records the telemetry signature that precedes it, the spare part it consumes, the repair duration, and how much advance warning to expect.

**Business description**

This is the entity that makes root-cause reasoning specific rather than generic. A generic failure catalogue tells the platform that bearing degradation exists. This entity tells it that **bearing degradation on a VMC-500 presents as progressive vibration increase at constant speed, requires part `INV-CP-BRG-6205`, takes about 240 minutes to repair, and typically gives around a week of warning.**

That difference is what separates a recommendation a manager can act on from one they have to investigate themselves.

Four attributes carry that specificity.

**`leading_indicator_description`** describes the telemetry signature in the language an experienced engineer would use. When the Decision Agent sees rising vibration with steady speed on a machining centre, this text is what lets it match the observation to a named failure mode and explain the match. It is the bridge between a number and a diagnosis.

**`primary_machine_parameter_id`** provides the same link in structured form. Free text grounds the LLM's reasoning; the foreign key lets the Supervisor Agent query it directly — *which failure modes have this drifting parameter as their primary indicator?* Both are present because they serve different consumers, and the structured link is what keeps the text honest.

**`required_inventory_item_id`** answers the question that determines whether a repair can proceed at all. **This is where the spare-part link lives**, and §19 explains why it belongs here rather than on `inventory_item`: the relationship the platform needs is failure-mode-specific, and one part can serve several failure modes across several machine types.

**`typical_warning_period_hours`** is how much lead time the telemetry usually gives. This is what converts a prediction into a plan. A failure mode with 168 hours of warning can be scheduled into the next planned window. One with 8 hours must be handled this shift. Same probability, entirely different recommended action — and without this attribute the platform could state risk but not urgency.

**`is_model_predictable`** is an honesty mechanism. Not every failure is predictable from telemetry: a control board failing electrically gives no warning. Marking these explicitly stops the platform from implying it can forecast something it cannot, and stops the ML pipeline from training on labels that carry no learnable signal.

**Primary key**

`machine_type_failure_mode_id` — surrogate integer, with a **composite unique constraint on (`machine_type_id`, `failure_category_id`)**.

One declaration per type-failure pair. No business code: this is a relationship qualification, not a named thing.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_type_id` | INTEGER (FK) | Yes | `1` | References active `machine_type`; unique with `failure_category_id` | The machine model this failure mode applies to |
| `failure_category_id` | INTEGER (FK) | Yes | `3` | References active `failure_category`; unique with `machine_type_id` | The failure mode |
| `typical_severity_level_id` | INTEGER (FK) | Yes | `2` | References active `failure_severity_level` | Severity for this failure **on this machine type.** Overrides the category default — overheating on a critical spindle is more severe than on a conveyor |
| `primary_machine_parameter_id` | INTEGER (FK) | No | `5` | References active `machine_parameter` that is declared on this machine type | The main telemetry indicator. NULL when the failure has no telemetry precursor. **Structured link for query-based matching** |
| `required_inventory_item_id` | INTEGER (FK) | No | `7` | References active `inventory_item` with `item_type` in (`spare_part`, `tooling`, `component`) | Spare part consumed by the repair. NULL when none is needed. **The authoritative failure-to-part link** |
| `leading_indicator_description` | TEXT | Yes | `Progressive vibration velocity increase at constant spindle speed, with gradual temperature rise` | Non-empty | The telemetry signature in engineering language. **Grounds the Decision Agent's root-cause match and lets it explain the reasoning** |
| `estimated_repair_duration_minutes` | INTEGER | Yes | `240` | Greater than 0 | Expected repair time for this failure on this type. **The authoritative repair estimate**, used in preference to the type-level MTTR when the failure mode is identified |
| `typical_warning_period_hours` | INTEGER | No | `168` | Greater than 0 when present | Typical advance warning from telemetry. NULL when the failure gives no warning. **Converts a prediction into a schedulable plan or an immediate action** |
| `is_model_predictable` | INTEGER | Yes | `1` | Default 0 | Whether telemetry can predict this failure. **`0` prevents the platform from implying forecast capability it does not have** |
| `relative_frequency` | TEXT + CHECK | Yes | `common` | One of: `common`, `occasional`, `rare` | How often this failure mode occurs on this type. A maintained engineering judgement, not a computed statistic. Helps the Decision Agent rank competing hypotheses when evidence is ambiguous |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `machine_type` | Many-to-one | The machine model |
| Parent | `failure_category` | Many-to-one | The failure mode |
| Parent | `failure_severity_level` | Many-to-one | Type-specific severity |
| Parent | `machine_parameter` | Many-to-one, optional | Primary telemetry indicator |
| Parent | `inventory_item` | Many-to-one, optional | Required spare part |

This entity resolves **machine_type ↔ failure_category as many-to-many** and is the most connected junction in the model, with five parents. Each is justified: it is the single point where equipment, failure taxonomy, severity policy, telemetry, and inventory meet — which is exactly the join the Decision Agent needs to produce a complete recommendation from one lookup.

**Business rules**

1. One declaration per machine type and failure category pair.
2. Every machine type used by a monitored machine should have at least one failure mode row. Without them, the Decision Agent has no vocabulary from which to hypothesise a cause for that equipment.
3. `primary_machine_parameter_id`, when present, must reference a parameter actually declared on this machine type in `machine_type_parameter`. A failure mode indicated by a parameter the machine does not measure is undetectable, and declaring it would create a false expectation.
4. `is_model_predictable = 1` requires a non-NULL `primary_machine_parameter_id` that is also flagged `is_ml_feature = 1`. A failure claimed as predictable must have a telemetry signal the model actually receives.
5. `typical_warning_period_hours` must be NULL when `is_model_predictable = 0`. An unpredictable failure cannot have a warning period.
6. `required_inventory_item_id` should be populated whenever the failure category has `requires_spare_part = 1`. Knowing a part is needed without knowing which part is not actionable.
7. **Repair duration precedence is fixed:** use `estimated_repair_duration_minutes` from this entity when the failure mode is identified; fall back to `machine_type.mttr_minutes` when it is not. Exactly two sources, one stated rule, no ambiguity.
8. `typical_severity_level_id` may differ from the category default. Where it does, the difference should be explicable by the machine's role — the same overheating condition is genuinely more severe on a critical spindle than on a conveyor.
9. `relative_frequency` is maintained engineering judgement, refreshed on review. It is never computed from operational failure history by the platform.
10. A row cannot be soft-retired while operational predictions reference the failure mode.

**Example records**

Failure modes for `MTY-VMC-500` (vertical machining centre):

| failure_category | severity | primary_parameter | required_part | est_repair_minutes | warning_hours | predictable | frequency |
|---|---|---|---|---|---|---|---|
| `FC-BRG` | `SEV-2` | `PRM-VIB` | `INV-CP-BRG-6205` | 240 | 168 | 1 | `common` |
| `FC-TOOL` | `SEV-3` | `PRM-TWEAR` | `INV-TL-EM-C12` | 45 | 8 | 1 | `common` |
| `FC-SPDL` | `SEV-1` | `PRM-VIB` | `INV-CP-BRG-6205` | 480 | 72 | 1 | `occasional` |
| `FC-OVHT` | `SEV-1` | `PRM-TEMP` | NULL | 180 | 24 | 1 | `occasional` |
| `FC-BELT` | `SEV-3` | `PRM-TORQ` | `INV-CP-BELT-V38` | 90 | 120 | 1 | `occasional` |
| `FC-LUBR` | `SEV-2` | `PRM-TEMP` | NULL | 120 | 48 | 1 | `rare` |
| `FC-SENS` | `SEV-3` | NULL | NULL | 60 | NULL | 0 | `occasional` |

Failure modes for `MTY-ROBO-WELD-6X` (robotic welding cell):

| failure_category | severity | primary_parameter | required_part | est_repair_minutes | warning_hours | predictable | frequency |
|---|---|---|---|---|---|---|---|
| `FC-MOTR` | `SEV-2` | `PRM-PWR` | `INV-CP-SERVO-D4` | 300 | 96 | 1 | `occasional` |
| `FC-CTRL` | `SEV-2` | NULL | `INV-CP-SERVO-D4` | 240 | NULL | 0 | `occasional` |
| `FC-PNEU` | `SEV-3` | `PRM-AIRP` | NULL | 60 | 12 | 1 | `common` |

Failure modes for `MTY-CONV-BELT-12` (belt conveyor):

| failure_category | severity | primary_parameter | required_part | est_repair_minutes | warning_hours | predictable | frequency |
|---|---|---|---|---|---|---|---|
| `FC-BELT` | `SEV-3` | `PRM-PWR` | NULL | 90 | 240 | 1 | `common` |
| `FC-JAM` | `SEV-3` | `PRM-PWR` | NULL | 30 | NULL | 0 | `common` |
| `FC-BRG` | `SEV-4` | `PRM-PWR` | NULL | 120 | 336 | 1 | `occasional` |

Reading across the three tables shows what this entity contributes that a generic failure list could not:

- **The same failure mode carries different severity on different equipment.** `FC-BRG` is `SEV-2` on the machining centre and `SEV-4` on the conveyor. Identical physics, very different consequence, because one is a precision spindle and the other is a belt roller.
- **The same failure mode is detected through different parameters.** `FC-BELT` shows up as torque fluctuation on the machining centre and as rising power draw on the conveyor, because the two machines are instrumented differently. Hardcoding one indicator per failure mode would miss half of these.
- **Warning periods span two orders of magnitude.** Tool wear gives 8 hours; conveyor bearing degradation gives 336. That range is precisely why urgency cannot be inferred from probability alone.
- **Unpredictable failures are marked honestly.** `FC-CTRL` on the robot and `FC-JAM` on the conveyor have `is_model_predictable = 0` and NULL warning periods. The platform monitors and reacts to them but never claims to forecast them.
- **One part serves several failure modes.** `INV-CP-BRG-6205` covers both bearing degradation and spindle failure on the VMC-500. This is exactly why the part link lives here rather than as a single machine-type column on `inventory_item`.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Generates realistic failure scenarios with correct telemetry signatures, warning periods, and drift patterns per machine type |
| Prediction Agent | Restricts predicted failure modes to those flagged `is_model_predictable`, and uses `primary_machine_parameter_id` to align features with the failure being predicted |
| Supervisor Agent | Queries which failure modes match the observed drifting parameters, and checks availability of the required part before escalating |
| Decision Agent | **The root-cause engine.** Matches evidence to a plausible failure mode from a validated set, names the required part, states the repair duration, and uses `typical_warning_period_hours` to set urgency and choose between "schedule it" and "act now" |
| Notification Service | Conveys the failure mode, part requirement, and expected repair time so the recipient has what they need without a follow-up query |
| Dashboard | Shows plausible failure modes and their indicators per machine |

---

### 26. `machine_maintenance_schedule`

**Purpose**

Defines the planned maintenance policy for each machine: what work is required, how often, how long it takes, whether it stops the line, who performs it, and whether it can be deferred.

**Business description**

Every machine has planned maintenance obligations. A machining centre needs lubrication every 120 operating hours, a preventive service every 500 hours, and a calibration every 180 days. A conveyor needs a monthly inspection.

This entity holds the **policy**, and it is one of the most valuable pieces of context the Supervisor Agent can supply. Two situations look identical in telemetry and are entirely different in reality:

- A machine showing rising vibration that had its preventive service last week.
- A machine showing the same rising vibration that is 200 operating hours **overdue** for that service.

The second has an obvious, cheap first action. The first does not. Without maintenance policy in context, the Decision Agent cannot tell them apart, and would give the same recommendation for both.

**`can_be_deferred` and `max_deferral_days` are the attributes that make recovery planning realistic.** Not all maintenance is equally negotiable. A preventive service can usually slip two weeks. A regulatory calibration cannot slip at all. When the Decision Agent proposes combining an urgent repair with a due maintenance task, or deferring maintenance to protect a critical order, it must know which tasks have flexibility. Without these two attributes it would either never propose deferral, or propose deferring something that legally cannot be deferred.

**`requires_line_stop`** determines whether the work needs a production window or can be done while running. This decides whether maintenance can be slotted into a shift change or needs a planned stoppage.

**The critical boundary decision: no `next_due_date`.** This entity stores `baseline_start_date` — an immutable anchor recording when the schedule took effect — and nothing else about timing. `last_performed_date` and `next_due_date` are **deliberately absent**, for the reason set out in §2.3:

Both are **derived facts** that depend on operational maintenance completion history. Caching them here would create two sources of truth for one fact. Cached due dates go stale — the update fails, or a completion is recorded late, and the platform then confidently reports maintenance as current when it is overdue. In a predictive maintenance system that is not a cosmetic defect: it is the platform being wrong about the exact thing it exists to get right.

Instead, due status is computed by the Supervisor Agent from `baseline_start_date`, `interval_basis`, `interval_value`, and operational maintenance history. One authoritative derivation, always current, always traceable to the records it came from.

**Primary key**

`machine_maintenance_schedule_id` — surrogate integer, with `machine_maintenance_schedule_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_maintenance_schedule_code` | VARCHAR(12) | Yes | `SCH-0001` | Unique; matches `^SCH-[0-9]{4}$` | Schedule identifier referenced on work orders |
| `machine_id` | INTEGER (FK) | Yes | `1` | References `machine` with `lifecycle_status` other than `decommissioned` | The machine this schedule applies to |
| `maintenance_type` | TEXT + CHECK | Yes | `preventive` | One of: `preventive`, `predictive`, `calibration`, `inspection`, `lubrication` | Kind of work. Distinguishes routine servicing from regulatory calibration, which have very different deferral flexibility |
| `interval_basis` | TEXT + CHECK | Yes | `operating_hours` | One of: `calendar_days`, `operating_hours`, `cycle_count` | What the interval is measured in. **Operating-hours intervals depend on actual usage**, which is why due status must be computed rather than stored |
| `interval_value` | INTEGER | Yes | `500` | Greater than 0 | Interval length in the units of `interval_basis`. Every 500 operating hours, every 180 days, every 50,000 cycles |
| `estimated_duration_minutes` | INTEGER | Yes | `120` | Greater than 0 | Expected time to complete. **Lets the Decision Agent evaluate whether the work fits an available window** |
| `requires_line_stop` | INTEGER | Yes | `1` | Default 0 | Whether production must halt. Determines whether a planned window is needed or the work can be done running |
| `assigned_maintenance_team_id` | INTEGER (FK) | No | `1` | References active `maintenance_team` when present | Team responsible. NULL when unassigned. Should have a specialization matching the machine's category |
| `required_inventory_item_id` | INTEGER (FK) | No | `11` | References active `inventory_item` when present | Part or consumable the task needs. NULL when none. **Lets the platform confirm the task can actually be performed** before recommending it |
| `baseline_start_date` | DATE | Yes | `2024-01-08` | On or after `machine.commissioned_date` | **Immutable anchor** from which intervals are calculated. The only date stored — everything else is derived |
| `can_be_deferred` | INTEGER | Yes | `1` | Default 1 | Whether the task may be postponed. **`0` for regulatory or safety-mandated work** |
| `max_deferral_days` | INTEGER | No | `14` | Greater than 0 when present; must be NULL when `can_be_deferred` is 0 | Maximum permitted postponement. NULL when not deferrable, or when no formal limit exists |
| `task_summary` | TEXT | No | `Inspect and regrease spindle bearings, verify coolant flow, check way lubrication` | Non-empty when present | What the work involves. NULL when covered by a standard procedure document. Gives the Decision Agent enough detail to explain whether the task addresses the observed symptom |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `machine` | Many-to-one | A machine has several maintenance schedules |
| Parent | `maintenance_team` | Many-to-one, optional | Team responsible for the work |
| Parent | `inventory_item` | Many-to-one, optional | Part or consumable required |

**Business rules**

1. A machine may have several schedules of different types and intervals. Multiple schedules per machine is the normal case, not an exception.
2. **`next_due_date` and `last_performed_date` are not stored.** Due status is derived from `baseline_start_date`, the interval, and operational maintenance history. See §2.3.
3. `interval_basis = 'operating_hours'` schedules depend on accumulated operating hours, which is operational data. This is the clearest illustration of why due status cannot live in master data: the same schedule becomes due at different calendar times depending on how hard the machine has been run.
4. `max_deferral_days` must be NULL when `can_be_deferred = 0`. A deferral limit on a non-deferrable task is contradictory, and the contradiction would surface as a bad recommendation.
5. Calibration schedules normally have `can_be_deferred = 0`. Calibration intervals are frequently a regulatory or customer-audit requirement, and the platform must never propose deferring one.
6. `assigned_maintenance_team_id`, when present, should have a `specialization` matching the machine's category `primary_maintenance_specialization`. A mismatch means the assigned team may not be qualified.
7. `required_inventory_item_id`, when present, must have stock available before the platform recommends performing the task. Recommending work that cannot be done wastes the response window.
8. Only machines with `lifecycle_status` other than `decommissioned` may have active schedules. Maintaining a retired asset is not a real recommendation.
9. Schedules with `requires_line_stop = 1` must be scheduled into a window where the line is not producing. The Supervisor Agent identifies candidate windows from `shift` data.
10. A schedule cannot be soft-retired while operational work orders reference it.

**Example records**

| code | machine | maintenance_type | interval_basis | interval_value | est_duration_minutes | requires_line_stop | team | required_part | baseline_start_date | can_be_deferred | max_deferral_days |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `SCH-0001` | `MC-0101` | `preventive` | `operating_hours` | 500 | 120 | 1 | `MTM-MECH` | NULL | 2024-01-08 | 1 | 14 |
| `SCH-0002` | `MC-0101` | `lubrication` | `operating_hours` | 120 | 30 | 0 | `MTM-MECH` | NULL | 2024-01-08 | 1 | 3 |
| `SCH-0003` | `MC-0101` | `calibration` | `calendar_days` | 180 | 240 | 1 | `MTM-AUTO` | NULL | 2024-02-01 | **0** | NULL |
| `SCH-0004` | `MC-0102` | `preventive` | `operating_hours` | 500 | 100 | 1 | `MTM-MECH` | NULL | 2024-01-15 | 1 | 14 |
| `SCH-0005` | `MC-0201` | `preventive` | `operating_hours` | 400 | 180 | 1 | `MTM-AUTO` | `INV-CP-SERVO-D4` | 2024-01-22 | 1 | 10 |
| `SCH-0006` | `MC-0202` | `inspection` | `calendar_days` | 30 | 45 | 0 | `MTM-MECH` | NULL | 2024-01-05 | 1 | 7 |
| `SCH-0007` | `MC-0301` | `preventive` | `operating_hours` | 500 | 120 | 1 | `MTM-MECH` | NULL | 2024-02-12 | 1 | 14 |
| `SCH-0008` | `MC-0401` | `inspection` | `calendar_days` | 60 | 30 | 0 | `MTM-MECH` | NULL | 2024-01-19 | 1 | 14 |

Three rows show the entity earning its place:

- **`MC-0101` carries three schedules** with different intervals, durations, teams, and deferral limits. Lubrication every 120 hours can slip three days and does not stop the line. The 500-hour preventive service stops the line and can slip two weeks. The calibration stops the line and **cannot slip at all.** One machine, three different negotiability profiles — which is exactly what a recovery plan needs to know.
- **`SCH-0003` has `can_be_deferred = 0`.** If the Decision Agent is weighing whether to defer maintenance to protect a Gold-tier order, this is the one task it must not touch. Encoding that as data rather than as an assumption is what keeps the recommendation legally and contractually safe.
- **`SCH-0005` requires `INV-CP-SERVO-D4`** — the 48,500-rupee module with a 30-day lead time and only one unit stocked. So the robot's preventive service consumes the same part that an unplanned motor failure would need. That is a genuine and non-obvious operational tension, and because the model records it, the platform can surface it rather than discovering it when the part is gone.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Applies maintenance events on realistic intervals, resetting cumulative parameters such as tool wear and adjusting degradation after service |
| Monitoring Agent | Suppresses events during scheduled maintenance windows, avoiding alerts for planned downtime |
| Supervisor Agent | **Computes maintenance due status** from baseline, interval, and operational history, and includes it in decision context. Identifies candidate windows for recommended work |
| Decision Agent | **Recovery planning core.** Determines whether an overdue service explains the symptom, whether the fix can ride along with due maintenance, and — critically — which tasks may be deferred and by how much |
| Notification Service | States the maintenance context so a recipient understands whether this is a new problem or a known-overdue one |
| Dashboard | Shows maintenance compliance and upcoming work per machine |

---

### 27. `alert_threshold_profile`

**Purpose**

A named, versioned set of monitoring limits for a machine type. It is the reusable policy unit that lets several machines share one monitoring configuration, and lets identical machines be monitored differently when their business context differs.

**Business description**

A threshold profile is a **monitoring policy**, not an engineering specification. It answers "when do we want to be told?" rather than "how does this machine behave?" The distinction is set out in §12 and it is the reason profiles are separate from the operating envelope on `machine_type_parameter`.

The profile exists as a header, with the actual limits held as `alert_threshold_rule` rows — one per parameter. §28 explains why.

The clearest justification for profiles is the case already visible in the machine data. `MC-0101` and `MC-0302` are both `MTY-VMC-500` machining centres. `MC-0101` is the bottleneck on a **critical** line producing for a Gold-tier customer. `MC-0302` is a non-bottleneck station on a **standard** line. Same equipment, same physics, same healthy envelope — but a very different appetite for risk. `MC-0101` runs `ATP-VMC-TIGHT`, which warns earlier and accepts more false positives in exchange for more warning time. `MC-0302` runs `ATP-VMC-STD`.

Without profiles, achieving that would require either per-machine threshold columns — duplicating limits across every machine and guaranteeing they drift — or one policy for all machines of a type, which ignores business context entirely.

**`version` and `effective_from_date` support the tuning cycle.** Threshold tuning is iterative: teams tighten limits until alert volume becomes intrusive, then relax them. Versioning records that history, so when someone asks why alert volume changed in June, the answer is in the data.

**`sensitivity`** is a plain-language descriptor — `tight`, `standard`, `relaxed` — that makes a profile's intent legible without reading every rule. It also gives the Decision Agent useful context: a warning from a `tight` profile is a weaker signal than the same warning from a `relaxed` one, and saying so honestly is better than presenting both as equivalent.

**Primary key**

`alert_threshold_profile_id` — surrogate integer, with `alert_threshold_profile_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `alert_threshold_profile_code` | VARCHAR(20) | Yes | `ATP-VMC-TIGHT` | Unique; matches `^ATP-[A-Z0-9-]{3,15}$` | Profile identifier. Self-describing on sight, which matters because engineers assign these by hand |
| `profile_name` | VARCHAR(120) | Yes | `VMC-500 Tightened Monitoring` | Non-empty | Descriptive name explaining the profile's intent |
| `machine_type_id` | INTEGER (FK) | Yes | `1` | References active `machine_type` | The machine model this profile is authored for. **A profile is type-specific** — limits are meaningless across different equipment |
| `version` | INTEGER | Yes | `2` | Greater than 0 | Revision number, incremented on each retune. Records the tuning history |
| `is_default` | INTEGER | Yes | `1` | Default 0; exactly one `1` per machine type | Whether this is the standard profile for the type. Applied to new machines unless explicitly overridden |
| `sensitivity` | TEXT + CHECK | Yes | `standard` | One of: `tight`, `standard`, `relaxed` | Intent of the profile. **Qualifies how much weight a warning carries** — a breach of a tight profile is a weaker signal than a breach of a relaxed one |
| `effective_from_date` | DATE | Yes | `2025-04-01` | Not in the future | When this version took effect. Makes historical alert volume interpretable against the policy in force at the time |
| `review_due_date` | DATE | No | `2026-04-01` | After `effective_from_date` when present | When the profile should next be reviewed. NULL when no review cycle is set. Prevents thresholds being set once and never revisited — the most common cause of alert fatigue |
| `notes` | TEXT | No | `Tightened for critical-line bottleneck machines; accepts higher false positive rate for earlier warning` | Non-empty when present | Rationale for the profile's settings. NULL when self-evident. **Explains the intent to whoever reviews it next**, and to the Decision Agent |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `machine_type` | Many-to-one | A profile is authored for one machine model |
| Child | `alert_threshold_rule` | One-to-many | A profile contains one rule per monitored parameter |
| Child | `machine` | One-to-many | Many machines can share one profile |

**Business rules**

1. A profile is authored for exactly one machine type. Cross-type profiles are prohibited: a conveyor limit applied to a machining centre would produce nonsense, and the constraint on `machine.alert_threshold_profile_id` enforces the match.
2. **Exactly one profile per machine type has `is_default = 1.`** New machines of that type receive it unless deliberately assigned another.
3. Every active profile must have at least one active `alert_threshold_rule`. A profile with no rules monitors nothing while appearing configured — a silent failure of exactly the kind §17 warns about.
4. Threshold limits in a profile's rules must be consistent with the machine type's operating envelope, following the ordering in §12: warning limits sit outside the healthy envelope, critical limits outside the warning limits, and all inside the physical range.
5. Rules may only reference parameters declared on the profile's machine type in `machine_type_parameter`. A rule for a parameter the machine does not measure can never fire, which is worse than no rule at all because it looks like coverage.
6. `version` is incremented on retune. Superseded versions are soft-retired rather than edited, preserving the record of what policy was in force when.
7. Profiles with `sensitivity = 'tight'` are expected to produce higher alert volume. That is the intended trade — earlier warning for more false positives — and the Decision Agent should account for it when weighing a breach.
8. A profile cannot be soft-retired while active monitored machines reference it. Machines must be reassigned first, or they would be left monitored by nothing.

**Example records**

| code | profile_name | machine_type | version | is_default | sensitivity | effective_from_date | review_due_date |
|---|---|---|---|---|---|---|---|
| `ATP-VMC-STD` | VMC-500 Standard Monitoring | `MTY-VMC-500` | 2 | 1 | `standard` | 2025-04-01 | 2026-04-01 |
| `ATP-VMC-TIGHT` | VMC-500 Tightened Monitoring | `MTY-VMC-500` | 1 | 0 | `tight` | 2025-06-15 | 2026-06-15 |
| `ATP-LATHE-STD` | Lathe-200 Standard Monitoring | `MTY-CNC-LATHE-200` | 1 | 1 | `standard` | 2025-04-01 | 2026-04-01 |
| `ATP-ROBO-STD` | Robotic Weld Cell Standard | `MTY-ROBO-WELD-6X` | 1 | 1 | `standard` | 2025-04-01 | 2026-04-01 |
| `ATP-CONV-STD` | Conveyor Standard Monitoring | `MTY-CONV-BELT-12` | 1 | 1 | `relaxed` | 2025-04-01 | NULL |
| `ATP-SEAL-STD` | Carton Sealer Standard | `MTY-CARTON-SEAL-3` | 1 | 1 | `standard` | 2025-04-01 | NULL |

Two observations:

- **`MTY-VMC-500` has two profiles.** The standard one is on version 2, having already been retuned once. The tightened one was introduced later, in June, specifically for critical-line machines. That is the tuning cycle recorded as data rather than as institutional memory.
- **`ATP-CONV-STD` is `relaxed` with no review date.** The conveyor has an 11,000-hour MTBF and a 90-minute repair. Tight monitoring on that equipment would generate noise for a machine that rarely fails and is quick to fix. Monitoring intensity is matched to consequence, which is a deliberate choice the profile makes visible.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Monitoring Agent | **Primary configuration source.** Resolves each machine's profile and evaluates readings against its rules |
| Prediction Agent | Uses profile context to interpret which readings the monitoring layer already considers abnormal |
| Supervisor Agent | Uses `sensitivity` to weigh how much a breach means when deciding whether to escalate |
| Decision Agent | Qualifies evidence honestly — a tight-profile warning is stated as an early indication, not an imminent failure |
| Dashboard | Draws threshold lines on charts and shows which policy applies to each machine |

---

### 28. `alert_threshold_rule`

**Purpose**

Defines the actual warning and critical limits for one parameter within one profile, including how long a breach must persist and how fast a value may change. This is where the Monitoring Agent's decisions are configured.

**Business description**

Each rule answers a complete monitoring question for a single parameter: at what value do we warn, at what value is it critical, how long must the condition hold, how fast is too fast a change, and what severity does each breach carry.

**Why rules are rows rather than columns.** The obvious alternative is a wide `alert_threshold_profile` table with columns like `temp_warning_high`, `temp_critical_high`, `rpm_warning_low`, and so on. That design was considered and rejected on four counts:

| Wide-column design | Row-per-parameter design |
|---|---|
| Adding a monitored parameter requires a schema migration | Adding a parameter is a new row |
| Every machine type has NULLs for parameters it lacks — a conveyor has no tool wear columns | Only applicable parameters have rows |
| The Monitoring Agent must know column names, hardcoding the parameter vocabulary in code | The agent iterates rows generically, with no parameter knowledge in code |
| Per-parameter attributes such as sustained duration must be duplicated per column | Each rule carries its own attributes naturally |

The row-based design means the Monitoring Agent contains **no knowledge of specific parameters.** It loads the rules for a machine, compares each reading to its limits, and raises events. Adding vibration monitoring to a machine type that never had it is a data change, not a code change. That is the difference between configuration and hardcoding, and it is what keeps the Monitoring Agent genuinely single-responsibility.

**`sustained_duration_seconds` is the main defence against alert noise.** A momentary temperature spike is not a fault; a temperature above the warning limit for sixty continuous seconds is. Requiring persistence eliminates most transient false positives — the single biggest cause of alert fatigue in condition monitoring — without relaxing the limits themselves.

**`rate_of_change_limit_per_minute` catches what static limits miss.** A spindle climbing 4 °C per minute is in trouble even while still inside its normal range, because at that rate it will breach the critical limit within minutes. Static thresholds detect a state; rate limits detect a trajectory. Both are needed, and having the rate limit as data means the Monitoring Agent applies one generic rule rather than special-casing per parameter.

**Nullable limits are meaningful.** Not every parameter needs limits in both directions. Tool wear has no meaningful lower bound — a fresh tool at zero wear is ideal. Temperature has both: too cold suggests a stalled machine or a failed sensor. A NULL limit means "do not check this direction", which is an explicit statement rather than a placeholder.

**Primary key**

`alert_threshold_rule_id` — surrogate integer, with a **composite unique constraint on (`alert_threshold_profile_id`, `machine_parameter_id`)**.

One rule per parameter per profile. No business code: a rule is identified by its profile and parameter.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `alert_threshold_profile_id` | INTEGER (FK) | Yes | `1` | References active `alert_threshold_profile`; unique with `machine_parameter_id` | The profile this rule belongs to |
| `machine_parameter_id` | INTEGER (FK) | Yes | `1` | References active `machine_parameter` declared on the profile's machine type; unique with profile | The parameter being limited |
| `warning_low` | NUMERIC(12,4) | No | `42.0000` | Less than or equal to `machine_type_parameter.normal_min`; greater than or equal to `critical_low` | Lower warning limit. NULL when low readings are not a concern |
| `warning_high` | NUMERIC(12,4) | No | `76.0000` | Greater than or equal to `machine_type_parameter.normal_max`; less than or equal to `critical_high` | Upper warning limit. NULL when high readings are not a concern |
| `critical_low` | NUMERIC(12,4) | No | `35.0000` | Greater than or equal to `machine_parameter.physical_min`; less than or equal to `warning_low` | Lower critical limit. NULL when not applicable |
| `critical_high` | NUMERIC(12,4) | No | `84.0000` | Less than or equal to `machine_parameter.physical_max`; greater than or equal to `warning_high` | Upper critical limit. NULL when not applicable |
| `sustained_duration_seconds` | INTEGER | Yes | `60` | Between 0 and 3600; default 0 | How long a breach must persist before an event is raised. 0 means immediate. **The primary noise filter** |
| `warning_severity_level_id` | INTEGER (FK) | Yes | `3` | References active `failure_severity_level` | Severity assigned when a warning limit is breached |
| `critical_severity_level_id` | INTEGER (FK) | Yes | `1` | References active `failure_severity_level`; `severity_rank` must be lower than the warning severity's rank | Severity assigned when a critical limit is breached. Must be more severe than the warning severity |
| `rate_of_change_limit_per_minute` | NUMERIC(12,4) | No | `2.5000` | Greater than 0 when present | Maximum acceptable change per minute. NULL when rate is not monitored. **Detects a dangerous trajectory while the value is still within range** |
| `is_enabled` | INTEGER | Yes | `1` | Default 1 | Whether the rule is evaluated. `0` suspends a rule without deleting it — useful while investigating a suspected faulty sensor |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `alert_threshold_profile` | Many-to-one | The profile containing this rule |
| Parent | `machine_parameter` | Many-to-one | The parameter being limited |
| Parent | `failure_severity_level` | Many-to-one (twice) | Warning severity and critical severity |

This entity resolves **alert_threshold_profile ↔ machine_parameter as many-to-many**. It references `failure_severity_level` twice, from two role-qualified foreign keys — legitimate, and creating no cycle, since severity levels reference nothing.

**Business rules**

1. One rule per profile and parameter pair.
2. A rule may only reference a parameter declared on the profile's machine type. A rule for an unmeasured parameter can never fire, and would create a false impression of coverage.
3. **At least one limit must be non-NULL.** A rule with all four limits NULL and no rate limit checks nothing while appearing to be configuration.
4. **The full ordering must hold** (from §12): `physical_min` ≤ `critical_low` ≤ `warning_low` ≤ `normal_min` ≤ `nominal_value` ≤ `normal_max` ≤ `warning_high` ≤ `critical_high` ≤ `physical_max`. NULL limits are skipped. Violating this ordering means a healthy machine generates alerts — the single most common configuration error in condition monitoring, and the reason the rule is stated as a constraint rather than a guideline.
5. `critical_severity_level_id` must be more severe than `warning_severity_level_id`, meaning a lower `severity_rank`.
6. `sustained_duration_seconds` should exceed the parameter's `sampling_interval_seconds`, so a breach is confirmed by more than one reading. A 60-second duration on a 10-second sampling interval requires six consecutive breaching readings.
7. Rate-of-change limits should be set for parameters where trajectory matters — temperature and vibration in particular. They are unnecessary for slow cumulative parameters such as tool wear, where the value only moves in one direction by design.
8. `is_enabled = 0` suspends evaluation while preserving the configuration. This is the correct response to a suspected faulty sensor: stop the noise without losing the policy.
9. Readings outside the parameter's physical range are treated as sensor faults, not threshold breaches. They are recorded as data quality issues and never fed to the Prediction Agent — which is what prevents a broken sensor from producing a confident wrong prediction.
10. A rule cannot be soft-retired while its profile is the only monitoring configuration for the parameter on active machines.

**Example records**

Rules for `ATP-VMC-STD` (standard profile, `MTY-VMC-500`). The healthy envelope from §12 is shown for comparison:

| parameter | healthy envelope | warning_low | warning_high | critical_low | critical_high | sustained_sec | warn_sev | crit_sev | rate_limit/min |
|---|---|---|---|---|---|---|---|---|---|
| `PRM-TEMP` | 45.0 – 72.0 | 42.0000 | 76.0000 | 35.0000 | 84.0000 | 60 | `SEV-3` | `SEV-1` | 2.5000 |
| `PRM-VIB` | 0.5 – 4.5 | NULL | 5.0000 | NULL | 7.1000 | 45 | `SEV-3` | `SEV-1` | 0.4000 |
| `PRM-TORQ` | 20.0 – 88.0 | NULL | 92.0000 | NULL | 105.0000 | 30 | `SEV-3` | `SEV-2` | NULL |
| `PRM-TWEAR` | 0.0 – 85.0 | NULL | 88.0000 | NULL | 95.0000 | 0 | `SEV-3` | `SEV-2` | NULL |
| `PRM-PWR` | 6.0 – 17.5 | 5.0000 | 19.0000 | 4.0000 | 21.5000 | 120 | `SEV-4` | `SEV-2` | NULL |
| `PRM-RPM` | 500 – 12000 | 400.0000 | 12600.0000 | 250.0000 | 13500.0000 | 20 | `SEV-4` | `SEV-2` | NULL |

Rules for `ATP-VMC-TIGHT` (tightened profile, same machine type, same envelope):

| parameter | healthy envelope | warning_low | warning_high | critical_low | critical_high | sustained_sec | warn_sev | crit_sev | rate_limit/min |
|---|---|---|---|---|---|---|---|---|---|
| `PRM-TEMP` | 45.0 – 72.0 | 43.5000 | 73.5000 | 38.0000 | 79.0000 | 30 | `SEV-2` | `SEV-1` | 1.8000 |
| `PRM-VIB` | 0.5 – 4.5 | NULL | 4.7000 | NULL | 6.0000 | 30 | `SEV-2` | `SEV-1` | 0.3000 |
| `PRM-TORQ` | 20.0 – 88.0 | NULL | 89.5000 | NULL | 98.0000 | 20 | `SEV-2` | `SEV-1` | NULL |
| `PRM-TWEAR` | 0.0 – 85.0 | NULL | 86.0000 | NULL | 92.0000 | 0 | `SEV-3` | `SEV-2` | NULL |

Comparing the two profiles shows precisely what "tightened" means in practice:

- **Warning limits move closer to the envelope.** Temperature warns at 76 °C on the standard profile, 73.5 °C on the tight one — barely above the 72 °C healthy maximum.
- **Sustained durations halve.** 60 seconds becomes 30, so breaches are reported sooner with less confirmation.
- **Warning severity rises.** `SEV-3` becomes `SEV-2`, which pushes the alert past more recipients' minimum severity filters and into immediate escalation.
- **Rate limits tighten.** 2.5 °C per minute becomes 1.8, catching slower thermal excursions.

Every one of those changes trades false positives for warning time. That is the correct trade for `MC-0101` — the bottleneck on a critical line serving a Gold-tier customer — and the wrong trade for `MC-0302`. Same equipment, opposite optimum, and the profile mechanism is what makes both available.

Two other details are worth noting:

- **`PRM-TWEAR` has `sustained_duration_seconds = 0`.** Tool wear is cumulative and monotonic: it cannot spike and recover, so requiring persistence would add delay for no noise reduction. This is `machine_parameter.is_cumulative` having a direct configuration consequence.
- **`PRM-RPM` and `PRM-PWR` carry only `SEV-4` warnings on the standard profile.** Both are bidirectional and both fluctuate with load, so a modest excursion is weak evidence. Low warning severity keeps them out of most recipients' inboxes while still recording the event for the Prediction Agent.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Factory Simulator | Generates values that cross these limits during failure scenarios, so detection can be demonstrated end to end |
| Monitoring Agent | **The complete configuration for event detection.** Iterates rules generically, applies limits, persistence, and rate checks, and assigns severity — with no parameter knowledge in code |
| Prediction Agent | Uses breach history as model context, and relies on physical-range validation to keep sensor faults out of the feature set |
| Supervisor Agent | Uses assigned severity to test the escalation cut-off from `business_rule` |
| Decision Agent | **Cites specific limits as supporting evidence** — "78.4 °C against a warning limit of 76 °C and a healthy maximum of 72 °C" is verifiable, where "temperature is high" is not |
| Dashboard | Draws warning and critical bands on parameter charts |

---

### 29. `business_rule`

**Purpose**

Holds the tunable parameters that govern platform behavior: escalation cut-offs, downtime cost rates, prioritization weights, notification policy, and maintenance and inventory policy figures. It is what keeps the platform's judgement configurable and explainable rather than buried in code.

**Business description**

Several decisions in the pipeline depend on numbers that are **business policy, not engineering fact**:

- What failure probability warrants escalating to the Decision Agent?
- What does an hour of downtime cost on each line?
- How much extra weight does a Gold-tier customer's order carry?
- Should non-critical notifications be suppressed outside shift hours?
- How long may preventive maintenance be deferred by default?

Each of these is a judgement a manager should own and be able to change. Hardcoding them would have two consequences, both bad. Changing the escalation cut-off would require a code deployment. And more importantly, **the platform could not explain why it escalated.** The explainability contract requires that every step be traceable, and "the model decided to escalate" is not traceable. "Failure probability 0.74 exceeded the escalation threshold of 0.70 defined in `BR-ESC-PROB`" is.

That traceability is this entity's real justification. It turns the Supervisor Agent's escalation decision from an opaque judgement into a stated rule with a value anybody can inspect.

**Typed value columns rather than a single text field.** The entity uses `value_numeric`, `value_text`, and `value_boolean`, with `value_type` declaring which one applies and a constraint ensuring exactly one is populated. The alternative — a single `value` text column with parsing at read time — would be more compact and considerably worse: no numeric comparison in queries, no database-level range validation, and a parse failure discovered at runtime in the middle of an escalation decision. Typed columns cost two mostly-NULL columns per row and buy validation and queryability. On a table holding a few dozen rows, that is an easy trade.

**Line-scoped overrides via a nullable foreign key.** `production_line_id` is nullable: NULL means the rule is global, populated means it applies to that line only. This exists because downtime cost genuinely differs per line, and a critical line warrants a lower escalation threshold than a standard one. Resolution is a simple two-step rule: look for a line-specific rule, fall back to the global one.

A general polymorphic scope — scope type plus scope reference, allowing rules scoped to machines, categories, or departments — was considered and rejected. It would require application-level resolution logic, could not be enforced by foreign keys, and no consumer needs scoping beyond the line. A single nullable foreign key does the job with database-enforced integrity.

**Primary key**

`business_rule_id` — surrogate integer, with `business_rule_code` unique.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `business_rule_code` | VARCHAR(32) | Yes | `BR-ESC-PROB` | Unique; matches `^BR-[A-Z]{3,5}-[A-Z0-9-]{2,20}$` | Rule identifier. **Cited in recommendations and logs to explain why a decision was taken** |
| `rule_name` | VARCHAR(150) | Yes | `Minimum failure probability for escalation` | Non-empty | Descriptive name a manager can understand without reading the description |
| `rule_category` | TEXT + CHECK | Yes | `escalation` | One of: `escalation`, `prioritization`, `costing`, `notification`, `maintenance_policy`, `inventory_policy` | Which decision the rule governs. Lets each consumer load only the rules it needs |
| `value_type` | TEXT + CHECK | Yes | `numeric` | One of: `numeric`, `text`, `boolean` | Which value column applies. Declares the type explicitly instead of inferring it |
| `value_numeric` | NUMERIC(14,4) | No | `0.7000` | Non-NULL if and only if `value_type` is `numeric` | Numeric value. Directly comparable in queries without parsing |
| `value_text` | VARCHAR(100) | No | NULL | Non-NULL if and only if `value_type` is `text` | Text value, typically a code such as a severity level |
| `value_boolean` | INTEGER | No | NULL | Non-NULL if and only if `value_type` is `boolean` | Boolean value for on/off policy switches |
| `unit` | VARCHAR(24) | No | NULL | Non-empty when present | Unit of the numeric value — `INR/hour`, `days`, `multiplier`. NULL for dimensionless values, text, and booleans. **Prevents a rate being misread as a total** |
| `production_line_id` | INTEGER (FK) | No | `1` | References active `production_line` when present | Scope. **NULL means global**; populated means line-specific override |
| `description` | TEXT | Yes | `Failure probability at or above this value escalates the event to the Decision Agent for reasoning` | Non-empty | What the rule governs and how it is applied. **Cited when explaining a decision**, so it must be written for a manager rather than a developer |
| `effective_from_date` | DATE | Yes | `2025-04-01` | Not in the future | When the value took effect. Makes historical behavior interpretable against the policy in force at the time |

**Relationships**

| Direction | Related entity | Cardinality | Meaning |
|---|---|---|---|
| Parent | `production_line` | Many-to-one, optional | Line scope, or NULL for global |

**Business rules**

1. **Exactly one value column is populated**, matching `value_type`. Enforced as a check constraint, because a row with the wrong column filled would fail silently at read time.
2. `business_rule_code` is unique globally. A line-scoped override is a **separate rule with its own code** — `BR-ESC-PROB-LN01` alongside `BR-ESC-PROB` — rather than a second row sharing a code with a different scope. This keeps every rule individually citable, which the explainability requirement depends on.
3. **Resolution order:** look for a rule scoped to the relevant line; if none exists, use the global rule. Exactly two levels, no deeper hierarchy.
4. Every `rule_category` that a consumer depends on must have at least one active global rule. A missing global rule with no line override leaves a consumer with no policy at all, and defaulting silently in code would defeat the purpose of this entity.
5. Numeric rules representing probability must have `value_numeric` between 0 and 1. Rules representing percentages must be between 0 and 100. The unit is what distinguishes them, which is why `unit` is required for dimensioned values.
6. `unit` is mandatory for numeric values that carry a dimension. A cost rate without `INR/hour` could be read as a total rather than a rate, and the error would surface as a wrong impact figure in a recommendation.
7. Every active line with `criticality = 'critical'` should have a line-scoped downtime cost rule. Without it the Decision Agent can state that impact is high but cannot quantify it, which weakens the recommendation exactly where it matters most.
8. Superseded values are soft-retired with a new row created, not edited in place. Editing would destroy the record of what policy produced a past decision — and that record is part of the audit trail.
9. Rules are **read** by agents and **never written** by them. An agent that could rewrite its own escalation threshold would make the platform's behavior unexplainable.
10. `description` is written for a factory manager, not a developer. It is quoted when explaining a decision, so its clarity is a functional property rather than a documentation nicety.

**Example records**

| code | rule_name | category | value_type | value | unit | scope | effective_from |
|---|---|---|---|---|---|---|---|
| `BR-ESC-PROB` | Minimum failure probability for escalation | `escalation` | `numeric` | 0.7000 | NULL | global | 2025-04-01 |
| `BR-ESC-PROB-LN01` | Minimum failure probability for escalation — Line 01 | `escalation` | `numeric` | 0.5500 | NULL | `LN-01` | 2025-06-15 |
| `BR-ESC-SEV` | Minimum severity for escalation | `escalation` | `text` | `SEV-2` | NULL | global | 2025-04-01 |
| `BR-COST-DOWN-DEF` | Default downtime cost rate | `costing` | `numeric` | 15000.0000 | `INR/hour` | global | 2025-04-01 |
| `BR-COST-DOWN-LN01` | Downtime cost rate — Line 01 | `costing` | `numeric` | 42000.0000 | `INR/hour` | `LN-01` | 2025-04-01 |
| `BR-COST-DOWN-LN02` | Downtime cost rate — Line 02 | `costing` | `numeric` | 28000.0000 | `INR/hour` | `LN-02` | 2025-04-01 |
| `BR-PRIOR-GOLD` | Priority weight for gold-tier customers | `prioritization` | `numeric` | 1.4000 | `multiplier` | global | 2025-04-01 |
| `BR-PRIOR-SAFETY` | Priority weight for safety-implicated failures | `prioritization` | `numeric` | 1.6000 | `multiplier` | global | 2025-04-01 |
| `BR-NOTIF-QUIET` | Suppress non-critical notifications outside shift hours | `notification` | `boolean` | 1 | NULL | global | 2025-04-01 |
| `BR-MAINT-DEFER-MAX` | Default maximum preventive maintenance deferral | `maintenance_policy` | `numeric` | 14.0000 | `days` | global | 2025-04-01 |
| `BR-INV-CRIT-MULT` | Safety stock multiplier for critical spares | `inventory_policy` | `numeric` | 1.5000 | `multiplier` | global | 2025-04-01 |

The escalation rules are the clearest illustration of why this entity exists.

Globally, escalation requires a failure probability of 0.70. On Line 01 — critical, bottleneck-constrained, serving a Gold-tier customer — the threshold drops to **0.55**. The line was given an earlier escalation point in June, at the same time as the tightened threshold profile.

The practical result: a 0.60 probability on Line 03 is recorded and monitored but does not reach the Decision Agent. The same 0.60 probability on Line 01 escalates, triggers reasoning, and produces a recommendation. That difference in behavior is entirely data-driven. Nothing in the code knows about Line 01.

And when a manager asks why one machine produced a recommendation and another did not, the answer is citable: *"Line 01 escalates at 0.55 under `BR-ESC-PROB-LN01`; Line 03 escalates at 0.70 under `BR-ESC-PROB`."* That is what configuration-as-data buys, and it is why the explainability contract is achievable at all.

**Why FactoryFlow AI needs this**

| Consumer | Dependency |
|---|---|
| Monitoring Agent | Reads notification and suppression policy |
| Prediction Agent | Reads risk classification cut-offs mapping probability onto the severity scale |
| Supervisor Agent | **Primary consumer.** Reads escalation thresholds — probability and severity, global and line-scoped — to decide what warrants LLM reasoning. This is where cost and noise are controlled |
| Decision Agent | Reads downtime cost rates to quantify impact, and prioritization weights to rank competing risks. **Cites rule codes in reasoning** so escalation and prioritisation are explainable |
| Notification Service | Reads quiet-hours policy and suppression rules |
| Dashboard | Displays active policy so managers can see and adjust the platform's behavior |

---

# Part III — Relationship Model

## 30. Entity Relationship Model

### 30.1 Complete foreign key inventory

Every foreign key in the model, in entity order. This is the authoritative relationship list — nothing exists outside this table.

| # | Entity | Foreign key | References | Req | Meaning |
|---|---|---|---|---|---|
| 1 | `plant` | — | — | — | **Root.** No outgoing references |
| 2 | `plant_area` | `plant_id` | `plant` | Yes | Area belongs to a plant |
| 3 | `department` | `plant_id` | `plant` | Yes | Department belongs to a plant |
| 4 | `shift` | `plant_id` | `plant` | Yes | Shift pattern defined per plant |
| 5 | `production_line` | `plant_area_id` | `plant_area` | Yes | Physical location |
| 5 | `production_line` | `department_id` | `department` | Yes | Organizational owner |
| 6 | `product` | — | — | — | **Root.** Independent reference entity |
| 7 | `product_line_capability` | `product_id` | `product` | Yes | Product covered |
| 7 | `product_line_capability` | `production_line_id` | `production_line` | Yes | Line capable of producing it |
| 8 | `machine_category` | — | — | — | **Root.** Independent lookup |
| 9 | `machine_type` | `machine_category_id` | `machine_category` | Yes | Equipment family |
| 10 | `machine` | `machine_type_id` | `machine_type` | Yes | Model specification |
| 10 | `machine` | `production_line_id` | `production_line` | Yes | Line membership |
| 10 | `machine` | `alert_threshold_profile_id` | `alert_threshold_profile` | No | Monitoring policy; NULL only when unmonitored |
| 11 | `machine_parameter` | — | — | — | **Root.** Independent lookup |
| 12 | `machine_type_parameter` | `machine_type_id` | `machine_type` | Yes | Type declaring the parameter |
| 12 | `machine_type_parameter` | `machine_parameter_id` | `machine_parameter` | Yes | Parameter declared |
| 13 | `worker_role` | — | — | — | **Root.** Independent lookup |
| 14 | `worker` | `worker_role_id` | `worker_role` | Yes | Role held |
| 14 | `worker` | `department_id` | `department` | Yes | Organizational home |
| 14 | `worker` | `production_line_id` | `production_line` | No | Line assignment; NULL means plant-wide |
| 14 | `worker` | `shift_id` | `shift` | Yes | Default shift |
| 15 | `maintenance_team` | `department_id` | `department` | Yes | Owning department |
| 15 | `maintenance_team` | `shift_id` | `shift` | Yes | Shift covered |
| 15 | `maintenance_team` | `base_plant_area_id` | `plant_area` | No | Physical base; NULL if mobile |
| 16 | `maintenance_engineer` | `worker_id` | `worker` | Yes | **Unique** — one-to-one |
| 16 | `maintenance_engineer` | `maintenance_team_id` | `maintenance_team` | Yes | Team membership |
| 17 | `notification_recipient` | `worker_id` | `worker` | Yes | **Unique** — one-to-one |
| 17 | `notification_recipient` | `min_severity_level_id` | `failure_severity_level` | Yes | Minimum severity received |
| 17 | `notification_recipient` | `scope_production_line_id` | `production_line` | No | Line scope; NULL means plant-wide |
| 18 | `inventory_location` | `plant_area_id` | `plant_area` | Yes | Containing area |
| 19 | `inventory_item` | `default_inventory_location_id` | `inventory_location` | Yes | Default storage |
| 19 | `inventory_item` | `primary_supplier_id` | `supplier` | No | Primary source; NULL if internal |
| 20 | `bill_of_materials` | `product_id` | `product` | Yes | Product made |
| 20 | `bill_of_materials` | `inventory_item_id` | `inventory_item` | Yes | Material consumed |
| 20 | `bill_of_materials` | `substitute_inventory_item_id` | `inventory_item` | No | Approved alternate; NULL if none |
| 21 | `supplier` | — | — | — | **Root.** Independent reference entity |
| 22 | `customer` | — | — | — | **Root.** No master children; consumed via operational orders |
| 23 | `failure_severity_level` | — | — | — | **Root.** Most widely referenced lookup |
| 24 | `failure_category` | `default_severity_level_id` | `failure_severity_level` | Yes | Baseline severity |
| 25 | `machine_type_failure_mode` | `machine_type_id` | `machine_type` | Yes | Machine model |
| 25 | `machine_type_failure_mode` | `failure_category_id` | `failure_category` | Yes | Failure mode |
| 25 | `machine_type_failure_mode` | `typical_severity_level_id` | `failure_severity_level` | Yes | Type-specific severity |
| 25 | `machine_type_failure_mode` | `primary_machine_parameter_id` | `machine_parameter` | No | Leading indicator; NULL if none |
| 25 | `machine_type_failure_mode` | `required_inventory_item_id` | `inventory_item` | No | Spare part; NULL if none |
| 26 | `machine_maintenance_schedule` | `machine_id` | `machine` | Yes | Machine maintained |
| 26 | `machine_maintenance_schedule` | `assigned_maintenance_team_id` | `maintenance_team` | No | Responsible team |
| 26 | `machine_maintenance_schedule` | `required_inventory_item_id` | `inventory_item` | No | Part consumed |
| 27 | `alert_threshold_profile` | `machine_type_id` | `machine_type` | Yes | Type the profile targets |
| 28 | `alert_threshold_rule` | `alert_threshold_profile_id` | `alert_threshold_profile` | Yes | Containing profile |
| 28 | `alert_threshold_rule` | `machine_parameter_id` | `machine_parameter` | Yes | Parameter limited |
| 28 | `alert_threshold_rule` | `warning_severity_level_id` | `failure_severity_level` | Yes | Severity on warning breach |
| 28 | `alert_threshold_rule` | `critical_severity_level_id` | `failure_severity_level` | Yes | Severity on critical breach |
| 29 | `business_rule` | `production_line_id` | `production_line` | No | Line scope; NULL means global |

**Totals:** 51 foreign keys across 29 entities. 8 entities are roots with no outgoing references. 2 relationships are one-to-one (enforced by unique constraints). 5 are many-to-many (resolved by junction entities).

### 30.2 Entity roles in the graph

| Role | Definition | Entities |
|---|---|---|
| **Root** | No outgoing foreign keys | `plant`, `product`, `machine_category`, `machine_parameter`, `worker_role`, `supplier`, `customer`, `failure_severity_level` |
| **Parent only** | Referenced by others, references only roots | `plant_area`, `department`, `shift`, `machine_type`, `failure_category` |
| **Parent and child** | Both references others and is referenced | `production_line`, `machine`, `worker`, `maintenance_team`, `inventory_location`, `inventory_item`, `alert_threshold_profile` |
| **Leaf** | References others, referenced by nothing in master data | `product_line_capability`, `machine_type_parameter`, `maintenance_engineer`, `notification_recipient`, `bill_of_materials`, `machine_type_failure_mode`, `machine_maintenance_schedule`, `alert_threshold_rule`, `business_rule` |

**Most referenced entities**, and what that tells us about the model's centre of gravity:

| Entity | Referenced by | Observation |
|---|---|---|
| `failure_severity_level` | 5 foreign keys across 4 entities | Severity is the platform's shared currency for urgency |
| `production_line` | 6 foreign keys across 6 entities | The line is the operational unit around which everything organises |
| `machine_type` | 4 foreign keys across 4 entities | Specification-driven design — behavior is defined at the model level, not per asset |
| `inventory_item` | 5 foreign keys across 4 entities | Material availability touches production, repair, and maintenance alike |
| `plant_area` | 3 foreign keys across 3 entities | Physical location is a real organising dimension, separate from ownership |

That `production_line` and `failure_severity_level` sit at the top is a good sign: the model's most connected entities are the business unit of production and the shared measure of urgency, which are exactly the two axes the platform reasons along.

### 30.3 Dependency layers and load order

Every entity is assigned to the layer immediately above its deepest dependency. This yields a valid seeding and migration order.

**Layer 0 — Roots (8 entities).** No dependencies. Load first, in any order.

```
plant                    machine_category         supplier
product                  machine_parameter        customer
worker_role              failure_severity_level
```

**Layer 1 — Direct children of roots (5 entities).**

```
plant_area        → plant
department        → plant
shift             → plant
machine_type      → machine_category
failure_category  → failure_severity_level
```

**Layer 2 — Structural and specification entities (5 entities).**

```
production_line          → plant_area, department
inventory_location       → plant_area
maintenance_team         → department, shift, plant_area
alert_threshold_profile  → machine_type
machine_type_parameter   → machine_type, machine_parameter
```

**Layer 3 — Core assets, people, and policy (6 entities).**

```
machine                  → machine_type, production_line, alert_threshold_profile
worker                   → worker_role, department, production_line, shift
inventory_item           → inventory_location, supplier
product_line_capability  → product, production_line
alert_threshold_rule     → alert_threshold_profile, machine_parameter, failure_severity_level
business_rule            → production_line
```

**Layer 4 — Specializations and cross-domain junctions (5 entities).**

```
maintenance_engineer          → worker, maintenance_team
notification_recipient        → worker, failure_severity_level, production_line
bill_of_materials             → product, inventory_item
machine_type_failure_mode     → machine_type, failure_category, failure_severity_level,
                                machine_parameter, inventory_item
machine_maintenance_schedule  → machine, maintenance_team, inventory_item
```

**Layer totals:** 8 + 5 + 5 + 6 + 5 = **29 entities.** Maximum dependency depth is 4.

**Practical consequences of the layering:**

- **Seeding** runs layer 0 through layer 4 with no deferred constraints and no temporary NULLs.
- **Migrations** create tables in the same order; teardown reverses it.
- **Test fixtures** can construct a minimal valid factory by walking the layers, since no entity requires anything from its own layer or above.
- **A depth of 4 is shallow** for a 29-entity model. That is a direct consequence of `plant` being the only root of the physical hierarchy and of leadership being modelled as child attributes rather than back-reference foreign keys.

### 30.4 Acyclicity analysis

The brief requires that circular dependencies be avoided. This section demonstrates that the model has none, and records the three places where a cycle was specifically designed out.

**Proof by layer assignment.** In §30.3 every entity is assigned a layer such that **all of its foreign keys reference entities in strictly lower layers.** A graph admitting such an assignment is a directed acyclic graph by construction: a cycle would require some entity to reference an entity at its own layer or higher, which no row in §30.1 does. The dependency graph is therefore provably acyclic.

**The three cycles that were designed out.** Each would have been the natural first modelling instinct.

| Would-be cycle | Natural instinct | Design applied | Why |
|---|---|---|---|
| `department` ⇄ `worker` | `department.manager_worker_id` alongside `worker.department_id` | `worker_role.is_managerial` on the child | The manager is the active worker in the department holding a managerial role |
| `production_line` ⇄ `worker` | `production_line.supervisor_worker_id` alongside `worker.production_line_id` | `worker_role.is_managerial` plus the worker's line assignment | The supervisor is the managerial worker assigned to that line |
| `maintenance_team` ⇄ `maintenance_engineer` | `maintenance_team.team_lead_engineer_id` alongside `maintenance_engineer.maintenance_team_id` | `maintenance_engineer.is_team_lead` on the child | The lead is the engineer on the team flagged as lead |

**The pattern, stated once:**

> **Leadership is modelled as an attribute of the person, never as a back-reference from the organizational unit.**

This is worth being explicit about, because it is the kind of decision that looks like a minor preference and is not. Three benefits follow from it:

1. **The graph stays acyclic**, so seeding, migration, and teardown all have a single valid order with no deferred constraints or two-pass inserts.
2. **It models reality more accurately.** A person holds a role; a department does not own a pointer to a person. Roles change far more often than organizational structures, and putting the mutable fact on the mutable entity is the correct placement.
3. **Leadership transitions are single-row operations.** Promoting a new team lead updates two engineer rows. With a back-reference it would touch the team row too, and a partial failure would leave the two representations disagreeing about who is in charge.

**Nullable foreign keys are not cycles.** Several relationships are optional — `machine.alert_threshold_profile_id`, `worker.production_line_id`, `business_rule.production_line_id`, `inventory_item.primary_supplier_id`, and others. Optionality expresses a business fact (unmonitored asset, plant-wide worker, global rule, internally produced item), and every one of these still points strictly downward through the layers. Nullability is not a workaround for a cycle anywhere in this model.

**Self-references.** `bill_of_materials` references `inventory_item` twice — once as the material, once as its approved substitute. `alert_threshold_rule` references `failure_severity_level` twice, for warning and critical severity. `machine_type_failure_mode` references `inventory_item` and `machine_parameter` alongside three other parents. None of these creates a cycle, because in each case the referenced entity does not reference back. Two foreign keys to the same table from one entity is a legitimate pattern when the two carry distinct roles — which is why both are role-qualified in their names.

### 30.5 Many-to-many relationships

Five, each resolved by a junction entity carrying its own attributes. The test applied before adding any of them: **a junction with no attributes of its own means the relationship should have been a foreign key instead.**

| # | Relationship | Junction | Attributes it carries | Why a junction is genuinely required |
|---|---|---|---|---|
| 1 | `product` ↔ `production_line` | `product_line_capability` | Capability type, cycle time, hourly output, changeover, qualification, tooling availability | A product runs on several lines at genuinely different rates; a line runs several products. The rate belongs to the pairing, not to either side |
| 2 | `machine_type` ↔ `machine_parameter` | `machine_type_parameter` | Operating envelope, sampling interval, ML feature flag, drift direction, sensor accuracy, weight | A parameter appears on several types with a different healthy envelope on each; a type exposes several parameters |
| 3 | `product` ↔ `inventory_item` | `bill_of_materials` | Quantity per unit, scrap allowance, criticality, substitute | A product consumes several materials; a material feeds several products. Quantity belongs to the pairing |
| 4 | `machine_type` ↔ `failure_category` | `machine_type_failure_mode` | Severity, leading indicator, required part, repair duration, warning period, predictability, frequency | A failure mode occurs on several types with different severity and signature; a type has several plausible failure modes |
| 5 | `alert_threshold_profile` ↔ `machine_parameter` | `alert_threshold_rule` | Four limits, sustained duration, two severities, rate limit, enabled flag | A profile limits several parameters; a parameter is limited in several profiles at different values |

**One-to-one relationships (2).** Both are specializations of `worker`, enforced by a unique constraint on `worker_id`:

| Relationship | Purpose | Why not columns on `worker` |
|---|---|---|
| `worker` ⇄ `maintenance_engineer` | Maintenance discipline, certification, team, leadership | Would be NULL for most workers, and would permit a store keeper to be marked a mechanical team lead |
| `worker` ⇄ `notification_recipient` | Channel enablement, severity filter, scope, rate limit | Would be NULL for most workers, and mixes notification policy into a personnel record |

Neither child stores any personal or contact data. Name, email, and phone exist once, on `worker`.

### 30.6 Textual entity relationship diagram

Read top to bottom; every arrow points from child to parent. Layer numbers correspond to §30.3.

```
LAYER 0 — ROOTS
┌──────────┐ ┌─────────┐ ┌────────────────┐ ┌──────────────────┐
│  plant   │ │ product │ │ machine_category│ │ machine_parameter│
└────┬─────┘ └────┬────┘ └────────┬───────┘ └────────┬─────────┘
     │            │               │                  │
┌──────────────┐ ┌──────────┐ ┌──────────┐ ┌────────────────────────┐
│ worker_role  │ │ supplier │ │ customer │ │ failure_severity_level │
└──────┬───────┘ └────┬─────┘ └────┬─────┘ └───────────┬────────────┘
       │              │            │  (operational only)│

LAYER 1
     ┌────────────┬───────────┐         │                  │
     ▼            ▼           ▼         ▼                  ▼
┌──────────┐ ┌──────────┐ ┌───────┐ ┌──────────────┐ ┌──────────────────┐
│plant_area│ │department│ │ shift │ │ machine_type │ │ failure_category │
└────┬─────┘ └────┬─────┘ └───┬───┘ └──────┬───────┘ └──────────────────┘
     │            │           │            │
LAYER 2
     ├────────────┴───────────┴────┐       ├──────────────────┐
     ▼                             ▼       ▼                  ▼
┌─────────────────┐  ┌──────────────────┐ ┌───────────────────────┐
│ production_line │  │ maintenance_team │ │alert_threshold_profile│
└────────┬────────┘  └────────┬─────────┘ └───────────┬───────────┘
     ▼                                                 │
┌────────────────────┐                    ┌────────────────────────┐
│ inventory_location │                    │ machine_type_parameter │
└─────────┬──────────┘                    └────────────────────────┘
          │
LAYER 3
     ┌────┴──────────┬─────────────────┬──────────────┬─────────────┐
     ▼               ▼                 ▼              ▼             ▼
┌─────────┐  ┌────────────────┐  ┌──────────┐  ┌──────────────────────┐
│ machine │  │ inventory_item │  │  worker  │  │ alert_threshold_rule │
└────┬────┘  └───────┬────────┘  └────┬─────┘  └──────────────────────┘
     │               │                │
┌─────────────────────────┐  ┌───────────────┐
│ product_line_capability │  │ business_rule │
└─────────────────────────┘  └───────────────┘

LAYER 4
     ├───────────────┬────────────────┬──────────────┬───────────────┐
     ▼               ▼                ▼              ▼               ▼
┌──────────────┐ ┌──────────────┐ ┌──────────┐ ┌──────────────────────────┐
│ maintenance_ │ │notification_ │ │ bill_of_ │ │machine_type_failure_mode │
│  engineer    │ │  recipient   │ │materials │ └──────────────────────────┘
└──────────────┘ └──────────────┘ └──────────┘
┌──────────────────────────────┐
│ machine_maintenance_schedule │
└──────────────────────────────┘
```

**The four principal navigation paths** the platform actually walks:

```
Physical hierarchy      plant → plant_area → production_line → machine
Asset specification     machine_category → machine_type → machine
Monitoring policy       machine_type → alert_threshold_profile → alert_threshold_rule → machine_parameter
Failure reasoning       machine_type → machine_type_failure_mode → failure_category → failure_severity_level
                                                                 → inventory_item (required part)
```

**The join that produces a complete recommendation**, as the Decision Agent walks it:

```
machine
  ├─→ machine_type ─→ machine_type_failure_mode ─→ failure_category      (what is failing, and why)
  │                                              ─→ failure_severity_level (how urgent)
  │                                              ─→ inventory_item        (part needed and available?)
  ├─→ production_line ─→ product_line_capability ─→ product              (output at risk, and reroute options)
  │                    ─→ department                                      (who is accountable)
  │                    ─→ business_rule                                   (downtime cost, escalation cut-off)
  ├─→ alert_threshold_profile ─→ alert_threshold_rule                     (evidence against limits)
  └─→ machine_maintenance_schedule ─→ maintenance_team                    (who repairs it, and when)
                                        └─→ maintenance_engineer ─→ worker (named, qualified, reachable)
```

Every element of the §16.5 explainability contract is reachable from `machine` in at most four hops. That is not a coincidence — it is the property the model was designed to have, because a recommendation that cannot assemble its own evidence in a few joins is a recommendation the platform cannot produce reliably.

### 30.7 Deliberate non-relationships

Relationships a reviewer might expect and will not find. Each was considered and excluded for a stated reason.

| Expected relationship | Excluded because |
|---|---|
| `department.manager_worker_id` | Circular dependency. Resolved via `worker_role.is_managerial` |
| `production_line.supervisor_worker_id` | Circular dependency. Resolved via role plus line assignment |
| `maintenance_team.team_lead_engineer_id` | Circular dependency. Resolved via `maintenance_engineer.is_team_lead` |
| `machine.plant_area_id` | Derivable through `production_line`. Storing it would let a machine claim a different area from its own line |
| `production_line.plant_id` | Derivable through `plant_area` |
| `machine.accumulated_operating_hours` | Operational. Increments continuously |
| `production_line.machine_count` | Derivable. Would drift from reality the first time a machine was added |
| `inventory_item.quantity_on_hand` | Operational. Changes with every issue and receipt |
| `inventory_item.machine_type_id` | Less precise than `machine_type_failure_mode.required_inventory_item_id`, and would duplicate it |
| `machine_maintenance_schedule.next_due_date` | Derived from operational history. Two sources of truth for one fact, and cached due dates go stale |
| `supplier` ↔ `inventory_item` many-to-many | Multi-sourcing has no current consumer. `primary_supplier_id` answers the question actually asked |
| `customer` ↔ `product` master link | Transactional, not static. Established per operational order |
| `worker` ↔ `shift` many-to-many roster | Shift rotation is operational scheduling. No consumer reads a roster |
| `worker` ↔ `worker_role` many-to-many | Dual roles would make authority resolution ambiguous exactly when clarity matters |
| `worker` ↔ `department` many-to-many | Matrix reporting would make accountability ambiguous |
| `product` ↔ `product` BOM recursion | Multi-level explosion has no consumer and would require recursive traversal |
| `maintenance_engineer` ↔ `machine_category` many-to-many | Specialization matching already resolves qualification with no junction |
| `bill_of_materials` versioned header | Batch-level traceability has no consumer. `effective_from_date` provides the audit trail |
| `machine` ↔ `production_line` many-to-many | A shared machine would make line-level impact ambiguous |
| `machine.current_status` | Operational. Changes minute to minute |

Two patterns account for most of this list. **Derivable facts are derived, not stored** — area, plant, machine count, due date. **Many-to-many relationships require a consumer** — multi-sourcing, rosters, dual roles, and BOM recursion are all realistic in an ERP and all unread by any component here.

---

# Part IV — Master Data Quality & Consumers

## 31. Consumer dependency matrix

Which downstream component depends on which entity. **●** marks a primary dependency — the component cannot perform its function without it. **○** marks a secondary dependency, read for context.

Columns: **SIM** Factory Simulator · **DB** Operational Database (foreign key target) · **MON** Monitoring Agent · **PRD** Prediction Agent · **SUP** Supervisor Agent · **DEC** Decision Agent · **NOT** Notification Service · **DASH** Dashboard.

| # | Entity | SIM | DB | MON | PRD | SUP | DEC | NOT | DASH |
|---|---|:--:|:--:|:--:|:--:|:--:|:--:|:--:|:--:|
| 1 | `plant` | ● | ○ | ○ | | ○ | ● | ● | ● |
| 2 | `plant_area` | ● | | ○ | | ● | ● | | ● |
| 3 | `department` | | | | | ● | ● | ● | ● |
| 4 | `shift` | ● | | ● | | ● | ● | ● | ● |
| 5 | `production_line` | ● | ● | ○ | ○ | ● | ● | ● | ● |
| 6 | `product` | ● | ● | ○ | | ● | ● | ○ | ● |
| 7 | `product_line_capability` | ● | | ● | | ● | ● | | ● |
| 8 | `machine_category` | ● | | ● | ● | ○ | ● | | ● |
| 9 | `machine_type` | ● | | ● | ● | ● | ● | | ● |
| 10 | `machine` | ● | ● | ● | ● | ● | ● | ● | ● |
| 11 | `machine_parameter` | ● | ● | ● | ● | ○ | ● | | ● |
| 12 | `machine_type_parameter` | ● | | ● | ● | ● | ● | | ● |
| 13 | `worker_role` | | | | | ● | ● | ● | ○ |
| 14 | `worker` | | | | | ● | ● | ● | ○ |
| 15 | `maintenance_team` | | | | | ● | ● | ● | ● |
| 16 | `maintenance_engineer` | | | | | ● | ● | ● | ● |
| 17 | `notification_recipient` | | | | | ● | ● | ● | ● |
| 18 | `inventory_location` | ● | | | | ● | ● | | ● |
| 19 | `inventory_item` | ● | ● | ● | | ● | ● | ● | ● |
| 20 | `bill_of_materials` | ● | | ● | | ● | ● | | ● |
| 21 | `supplier` | ● | | ● | | ● | ● | ● | ● |
| 22 | `customer` | ● | ● | | | ● | ● | ● | ● |
| 23 | `failure_severity_level` | | ● | ● | ● | ● | ● | ● | ● |
| 24 | `failure_category` | ● | ● | ○ | ● | ● | ● | ● | ● |
| 25 | `machine_type_failure_mode` | ● | | ○ | ● | ● | ● | ● | ● |
| 26 | `machine_maintenance_schedule` | ● | | ● | | ● | ● | ● | ● |
| 27 | `alert_threshold_profile` | ○ | | ● | ● | ● | ● | | ● |
| 28 | `alert_threshold_rule` | ● | ● | ● | ● | ● | ● | | ● |
| 29 | `business_rule` | | | ● | ● | ● | ● | ● | ● |

**Consumer breadth:**

| Consumer | Primary dependencies | Reads |
|---|---|---|
| Decision Agent | 29 | **Everything.** It is the only component that touches every entity, because a complete recommendation draws on equipment, failure taxonomy, production, business, inventory, and people at once |
| Supervisor Agent | 26 | Nearly everything — its job is assembling context, so breadth is inherent to the role |
| Dashboard | 25 | Presents the full factory picture |
| Factory Simulator | 20 | Everything needed to generate a realistic factory |
| Notification Service | 15 | People, severity, scope, and enough content context to compose a message |
| Monitoring Agent | 17 | Thresholds, parameters, schedules, and the context needed to suppress false positives |
| Prediction Agent | 11 | Deliberately narrow. Features, specifications, and severity mapping — nothing about people or business |
| Operational Database | 10 | Foreign key targets for telemetry, events, predictions, and recommendations |

Two observations worth drawing out.

**The Prediction Agent reads the least.** It needs machine specifications, parameter definitions, ML feature flags, threshold context, and the severity scale. It reads nothing about customers, workers, teams, or costs. That narrowness is a design property, not an oversight: prediction is a quantitative task, and giving it business context would invite it to blur measurement with judgement. Business awareness enters the pipeline at the Supervisor Agent, deliberately and one stage later.

**The Decision Agent reads everything.** That is equally deliberate. It is the only stage asked to synthesise across domains, and every element of the §16.5 explainability contract draws from a different corner of the model — evidence from thresholds, confidence from prediction, root cause from the failure taxonomy, business impact from products and customers, recommended action from teams and inventory. Reading broadly is what the role requires.

**Every entity has at least three primary consumers.** No entity in this model exists without demonstrated demand, which is the justification test from §1.4 applied and passed across the whole design.

---

## 32. Data quality requirements

Master data defects are more damaging than operational defects, because they are silent and systematic. A wrong sensor reading produces one wrong event. A wrong threshold produces wrong events indefinitely, and nobody notices until somebody audits the configuration.

### 32.1 Referential integrity

| Requirement | Enforcement |
|---|---|
| Every foreign key references an existing row | Database foreign key constraints, all 51 |
| Foreign keys point to **active** rows | Application-level validation; soft-deleted parents must not gain new children |
| No hard deletes on any master entity | `is_active = 0` (or `lifecycle_status` on `machine`) |
| Retirement respects dependents | A parent cannot be retired while active children reference it |

### 32.2 Completeness rules

Relationships that must exist for the platform to function. Each is stated as a check that can be run against seed data.

| Rule | Consequence if violated |
|---|---|
| Every active production line has at least one active machine | Line reports capacity but produces nothing |
| Every active production line has at least one active `product_line_capability` | Line cannot produce anything; capacity figure is meaningless |
| Every active product has at least one active capability row and one BOM line | Product cannot be made, or consumes nothing |
| Exactly one `production_route` capability row per product has `is_primary_line = 1` | "Where is this normally made" has no answer |
| Every active product has at least one `production_route` capability row | Product has only finishing-stage rows and no place it is actually made |
| Every monitored machine has an `alert_threshold_profile_id` | Machine appears monitored and is not |
| Every profile has at least one active `alert_threshold_rule` | Profile monitors nothing while looking configured |
| Every monitored machine type declares at least one `is_ml_feature` parameter | Prediction target with no features |
| Every machine type used by a monitored machine has at least one failure mode | Decision Agent has no root-cause vocabulary for that equipment |
| Exactly one profile per machine type has `is_default = 1` | New machines have no default policy |
| At most one machine per line has `is_bottleneck = 1` | Impact arithmetic becomes contradictory |
| Exactly one engineer per maintenance team has `is_team_lead = 1` | Team has no accountable contact, or two |
| Every active maintenance team has at least one active engineer | Team can be assigned work it cannot perform |
| Every department has at least one active worker in a managerial role | Department manager cannot be resolved |
| At least one `notification_recipient` covers the most severe level with `notify_outside_shift_hours = 1` | **A critical failure at 03:00 reaches nobody** |
| Every `rule_category` a consumer depends on has at least one active global `business_rule` | Consumer has no policy and would default silently in code |
| Every active critical line has a line-scoped downtime cost rule | Impact can be described but not quantified |
| Every recipient with a channel enabled has the corresponding endpoint on `worker` | Silent delivery failure that looks configured |

The last item in that list deserves emphasis. **A configured-but-unreachable recipient is worse than no configuration**, because the dashboard shows coverage that does not exist. The same is true of a profile with no rules and a machine with no profile. This category of defect — apparent configuration that does nothing — is the one the completeness checks exist to catch.

### 32.3 Consistency rules

Cross-entity invariants that no single foreign key can enforce.

**The threshold ordering rule.** The most important numeric invariant in the model:

```
physical_min ≤ critical_low ≤ warning_low ≤ normal_min
                                          ≤ nominal_value
                                          ≤ normal_max ≤ warning_high ≤ critical_high ≤ physical_max
```

Spanning `machine_parameter` (physical bounds), `machine_type_parameter` (healthy envelope), and `alert_threshold_rule` (alert limits). NULL limits are skipped. A violation means **a healthy machine generates alerts**, which is the single most common configuration error in condition monitoring and the fastest route to alert fatigue.

**Shared vocabularies.** Three entities use one specialization vocabulary — `mechanical`, `electrical`, `automation`, `general`:

- `machine_category.primary_maintenance_specialization`
- `maintenance_team.specialization`
- `failure_category.required_specialization`

This sharing is deliberate. It makes matching a failed machine to a qualified team a direct value comparison rather than a chain of inference rules. All three must be maintained together: adding a discipline to one without the others silently breaks team matching.

**Date chain consistency:**

```
plant.commissioned_date
  ≤ production_line.commissioned_date
      ≤ machine.installation_date ≤ machine.commissioned_date
          ≤ machine_maintenance_schedule.baseline_start_date
```

**Other cross-entity invariants:**

| Invariant | Reason |
|---|---|
| `machine.alert_threshold_profile_id` → profile's `machine_type_id` = machine's `machine_type_id` | A conveyor profile on a machining centre produces meaningless limits |
| `alert_threshold_rule.machine_parameter_id` must be declared in `machine_type_parameter` for the profile's type | A rule on an unmeasured parameter can never fire |
| `machine_type_failure_mode.primary_machine_parameter_id` must be declared on that machine type | An indicator the machine does not measure makes the failure undetectable |
| `is_model_predictable = 1` requires a primary parameter flagged `is_ml_feature = 1` | A failure claimed predictable must have a signal the model receives |
| `typical_warning_period_hours` must be NULL when `is_model_predictable = 0` | An unpredictable failure has no warning period |
| `PRM-TWEAR` only on types with `requires_tooling = 1` | Tool wear on a conveyor is meaningless and would pollute the feature set |
| `PRM-VIB` only on categories with `is_rotating_equipment = 1` | Vibration on static equipment teaches the model to fit noise |
| `critical_severity_level` rank < `warning_severity_level` rank | Critical must outrank warning |
| `target_response_time_minutes` increases as severity rank increases | A medium condition cannot demand a faster response than a critical one |
| `sustained_duration_seconds` > `sampling_interval_seconds` | A breach should be confirmed by more than one reading |
| `machine.line_position` ≤ `production_line.station_count`, unique per line | Two machines cannot occupy one station |
| `safety_stock_qty` ≤ `reorder_point` < `max_stock_qty` | Otherwise replenishment logic is incoherent |
| `is_critical_spare = 1` requires `safety_stock_qty` > 0 | A critical spare with no buffer defeats its classification |
| `expedited_lead_time_days` < `standard_lead_time_days` | An expedited option no faster than standard is not one |
| `standard_material_cost` < `standard_selling_price` | Negative margin would invert impact reasoning |
| `max_deferral_days` NULL when `can_be_deferred = 0` | Contradictory, and would surface as a bad recommendation |
| `mtbf_hours` < `design_life_hours` | A machine that never fails within its service life does not exist |
| `substitute_inventory_item_id` shares `unit_of_measure` with the material | Otherwise quantity arithmetic is wrong |
| `crosses_midnight = 1` when `end_time` ≤ `start_time` | Otherwise shift-window arithmetic is silently wrong |
| Exactly one `business_rule` value column populated, matching `value_type` | A mismatch fails silently at read time |

### 32.4 Validation layers

| Layer | Enforces | Examples |
|---|---|---|
| Database constraints | Types, NOT NULL, uniqueness, foreign keys, check constraints, enumerated sets | Code formats, value ranges, composite uniqueness, `crosses_midnight` |
| Application validation | Cross-entity rules a single constraint cannot express | Threshold ordering, profile-to-type match, parameter declaration checks |
| Seed data validation | Completeness rules from §32.2 | Run once after seeding; failure blocks startup |
| Periodic audit | Drift and staleness | Profiles past `review_due_date`, expired certifications, expired qualifications, lapsed contracts |

The layering matters. Constraints catch what is structurally impossible. Application validation catches what is semantically wrong. Seed validation catches what is missing. Periodic audit catches what was correct when it was set and is no longer. All four are needed, because each catches a class the others cannot.

### 32.5 Seed data volumes

Approximate row counts for the sample factory. Small enough to review by hand, large enough to exercise every relationship.

| Entity | Rows | Entity | Rows |
|---|---:|---|---:|
| `plant` | 1 | `inventory_location` | 5 |
| `plant_area` | 7 | `inventory_item` | 11 |
| `department` | 5 | `bill_of_materials` | 10 |
| `shift` | 4 | `supplier` | 4 |
| `production_line` | 4 | `customer` | 3 |
| `product` | 3 | `failure_severity_level` | 5 |
| `product_line_capability` | 7 | `failure_category` | 12 |
| `machine_category` | 5 | `machine_type_failure_mode` | ~22 |
| `machine_type` | 6 | `machine_maintenance_schedule` | 8 |
| `machine` | 8 | `alert_threshold_profile` | 6 |
| `machine_parameter` | 7 | `alert_threshold_rule` | ~26 |
| `machine_type_parameter` | ~28 | `business_rule` | 11 |
| `worker_role` | 10 | | |
| `worker` | 13 | | |
| `maintenance_team` | 4 | | |
| `maintenance_engineer` | 5 | | |
| `notification_recipient` | 5 | | |

**Total: roughly 245 rows across 29 entities.**

That number is a deliberate design target. The entire factory fits in a few hundred rows, which means:

- Seed data can be **reviewed by a human** and checked for realism.
- Every relationship is exercised, so integration issues surface in seeding rather than at runtime.
- The full factory can be rebuilt in seconds, making scenario demonstrations reproducible.
- A reviewer can hold the whole factory in their head — which is what makes the project explainable in an interview.

The bounded size is also what distinguishes master data from operational data. Master data stays at roughly 240 rows indefinitely. Operational telemetry will exceed that within the first minute of simulation.

---

## 33. Capability support

How the model supports each capability named in the brief.

**Factory Simulator.** Everything needed to generate a realistic factory: which machines exist and where they sit in each line's sequence (`machine`, `line_position`), what each emits and within what healthy envelope (`machine_type_parameter`), how often (`sampling_interval_seconds`), how fast it degrades (`machine_type.mtbf_hours`, `expected_drift_direction`), what failure modes are plausible with what signatures and warning periods (`machine_type_failure_mode`), what it produces and how fast (`product_line_capability`), what material that consumes (`bill_of_materials`), and when maintenance intervenes and resets cumulative parameters (`machine_maintenance_schedule`). Shift patterns make output vary realistically across the day rather than running flat.

**Operational Database.** Ten entities serve as foreign key targets for operational records: `machine` for telemetry and events, `machine_parameter` for typed readings, `production_line` and `product` for output, `customer` for orders, `inventory_item` for stock movements, `failure_category` and `failure_severity_level` for predictions and events, `alert_threshold_rule` for the rule that fired, and `worker` for acknowledgements. Every operational row is anchored to master data, which is what makes the traceability chain from a recommendation back to raw telemetry possible.

**Machine Learning.** The feature set is defined as **data**, not code: `machine_type_parameter.is_ml_feature` declares which parameters feed the model, the operating envelope provides consistent normalisation across machine types, `machine_parameter.is_cumulative` and `degradation_direction` inform feature engineering, `machine_category` scopes models so equipment with different physics is not forced into one model, and `machine_type_failure_mode.is_model_predictable` restricts training labels to failures that carry learnable signal. Physical bounds on `machine_parameter` keep sensor faults out of the training set.

**Prediction Agent.** Machine and type specifications as features, `mtbf_hours` and commissioning date as age and wear context, the declared feature set, and `failure_severity_level` for mapping probability onto risk classification. Deliberately no access to customers, costs, or people — prediction stays quantitative.

**Supervisor Agent.** The broadest reader after the Decision Agent. Escalation policy from `business_rule` including line-scoped overrides, production context from lines and capabilities, cascade context from `line_position`, `is_bottleneck`, and `downstream_buffer_units`, inventory availability against master thresholds, maintenance due status computed from schedules plus operational history, team availability filtered by shift, specialization, certification, and on-call status, and recipient eligibility. Everything needed to assemble a complete context package and to decide whether one is warranted.

**Decision Agent.** Reads all 29 entities, and each element of the §16.5 explainability contract maps to specific master data:

| Contract element | Master data supplying it |
|---|---|
| Supporting evidence | `alert_threshold_rule` limits, `machine_type_parameter` envelope, `machine_parameter` names and units |
| ML confidence | `failure_severity_level` scale (the probability itself comes from the Prediction Agent, never restated) |
| Root cause | `failure_category` controlled vocabulary, `machine_type_failure_mode` leading indicators and frequency |
| Business impact | `production_line` capacity and criticality, `product` margin, `customer` tier and penalty, `business_rule` cost rates |
| Recommended action | `machine_type_failure_mode` repair duration and required part, `inventory_item` availability and lead time, `maintenance_team` and `maintenance_engineer` qualification and availability, `machine_maintenance_schedule` deferral flexibility, `product_line_capability` reroute options |

**Dashboard.** Physical grouping by area and line, asset detail with specifications, threshold bands drawn from rules and envelopes, severity colours defined once in `failure_severity_level`, stock status against master thresholds, maintenance compliance, notification coverage, and active policy so managers can see and adjust the platform's behavior.

**Notification Service.** Recipients, channels, severity filters, line scope, quiet hours, escalation order, and rate limits from `notification_recipient`; delivery endpoints from `worker`; departmental fallback from `department.escalation_email`; acknowledgement windows from `failure_severity_level`; and enough content context — machine, line, product, customer, part, team — to compose a message that needs no follow-up query.

**Future reporting.** `cost_center_code` connects downtime to finance. `failure_category` and `failure_domain` enable failure mode analysis. `customer.priority_tier` enables impact analysis by segment. `target_oee_percent` and `target_response_time_minutes` provide benchmarks for actual-versus-target reporting. None of this required adding an entity — it falls out of attributes that already have operational consumers.

**Historical auditing.** Soft delete on every entity means operational history never orphans. `effective_from_date` on capabilities, BOM lines, profiles, and business rules records when policy changed. `alert_threshold_profile.version` records the tuning history. `business_rule` supersession by new row rather than in-place edit preserves the record of which policy produced a past decision. Together these mean a recommendation from a year ago can still be explained against the configuration that was in force when it was made — which is what the explainability contract requires and what in-place edits would quietly destroy.

---

# Part V — Design Decisions & Rejected Alternatives

## 34. Key design decisions

Fourteen decisions where a defensible alternative existed. Each records what was chosen, what was rejected, and why — the material an interviewer is most likely to probe.

### 34.1 Surrogate primary keys with business codes

**Chosen:** Every entity has an integer `<entity>_id` plus a unique human-readable `<entity>_code`.

**Rejected:** Natural keys as primary keys. Business codes change — lines get renumbered, products revised by engineering change. With a natural PK, a rename cascades into every referencing row and into millions of operational records.

**Rejected:** Surrogate keys alone. The brief requires human-readable identifiers, and the requirement is real: *"Machine MC-0101 on Line LN-01"* is usable by a manager; *"machine_id 47"* is not. Codes also make AI prompts legible and seed data reviewable.

**Rejected:** UUIDs. No distributed generation or cross-system merge requirement exists. Integers join faster, read easier, and use less space.

### 34.2 Leadership as a child attribute, not a parent foreign key

**Chosen:** `worker_role.is_managerial` and `maintenance_engineer.is_team_lead` identify leaders.

**Rejected:** `department.manager_worker_id`, `production_line.supervisor_worker_id`, `maintenance_team.team_lead_engineer_id`. All three create circular foreign key dependencies with the child entities that already point at them. Beyond the acyclicity requirement, the chosen pattern models reality better — a person holds a role, and roles change more often than organizational structures — and makes a leadership transition a single-entity update.

### 34.3 Maintenance engineer as a specialization of worker

**Chosen:** One-to-one with `worker`, holding only maintenance-specific attributes.

**Rejected:** A standalone entity with its own name, email, and phone. Duplicating personal data means a changed phone number needs two updates, and the first missed one sends an urgent breakdown call to a dead number.

**Rejected:** Adding the columns to `worker`. They would be NULL for the large majority of rows, and nothing would prevent a store keeper being marked a mechanical team lead.

### 34.4 Threshold rules as rows, not columns

**Chosen:** `alert_threshold_profile` header plus one `alert_threshold_rule` row per parameter.

**Rejected:** A wide table with `temp_warning_high`, `rpm_critical_low`, and so on. Four reasons, laid out in §28: adding a parameter would need a schema migration; every machine type would carry NULLs for parameters it lacks; the Monitoring Agent would hardcode column names and thereby the parameter vocabulary; and per-parameter attributes such as sustained duration would be duplicated per column.

The decisive consideration is the third. The row-based design means the Monitoring Agent holds **no knowledge of specific parameters** — it iterates rules generically. Adding vibration monitoring becomes a data change, not a code change, which is what keeps that agent genuinely single-responsibility.

### 34.5 Operating envelope separate from alert thresholds

**Chosen:** `machine_type_parameter` holds the healthy envelope (engineering fact); `alert_threshold_rule` holds the alert limits (operations policy).

**Rejected:** One set of numbers serving both. They have different sources, different change frequencies, and different consumers. Worse, collapsing them would make retuning an alert threshold silently change what the simulator considers healthy — which would make the whole pipeline circular and untestable.

### 34.6 No cached maintenance due date

**Chosen:** `baseline_start_date` only; due status computed by the Supervisor Agent from schedule definition plus operational history.

**Rejected:** Storing `last_performed_date` and `next_due_date`. Both are derived from operational maintenance completion. Caching them creates two sources of truth, and cached due dates go stale when an update fails or a completion is recorded late. In a predictive maintenance platform, being wrong about whether maintenance is overdue is not a cosmetic defect — it is the platform being wrong about the exact thing it exists to get right.

This is the model's clearest illustration of the master/operational boundary being enforced even where the pragmatic shortcut is tempting.

### 34.7 Inventory thresholds without inventory quantity

**Chosen:** `reorder_point`, `safety_stock_qty`, `max_stock_qty` as master policy. No `quantity_on_hand`.

**Rejected:** Storing current stock on the item. It changes with every issue and receipt, making it operational by every test in §2.1. Availability is answered by comparing an operational quantity against master thresholds.

### 34.8 Spare part linked through failure mode, not item

**Chosen:** `machine_type_failure_mode.required_inventory_item_id`.

**Rejected:** `inventory_item.machine_type_id`. The question the platform asks is not "which machine does this part serve?" but "which part does *this failure* on *this machine type* need?" The failure mode link is more precise, naturally expresses one part serving several failure modes, and avoids duplicating a fact better held at the specific level.

### 34.9 Cycle time only on product-line capability

**Chosen:** `cycle_time_seconds` lives exclusively on `product_line_capability`.

**Rejected:** A `standard_cycle_time_seconds` on `product` as well. The same product genuinely runs at different rates on different lines, so a product-level figure would be either wrong for most lines or a duplicate of the line-specific value. Removing it entirely means there is one authoritative rate per pairing and no reconciliation question.

### 34.10 Business rules with typed value columns

**Chosen:** `value_numeric`, `value_text`, `value_boolean`, with `value_type` declaring which applies and a constraint ensuring exactly one is populated.

**Rejected:** A single text `value` column with parsing at read time. No numeric comparison in queries, no database-level range validation, and a parse failure discovered at runtime in the middle of an escalation decision.

**Rejected:** Separate strongly-typed tables per rule category. Five or six tables to hold a few dozen rows, with no shared query path for "show me all active policy."

The trade is two mostly-NULL columns per row in exchange for validation and queryability. On a table this size that is straightforward.

### 34.11 Line-scoped rules via nullable foreign key

**Chosen:** `business_rule.production_line_id` nullable — NULL means global, populated means line-specific.

**Rejected:** A polymorphic scope (scope type plus scope reference) allowing rules scoped to machines, categories, or departments. It cannot be enforced by foreign keys, needs application-level resolution logic, and no consumer requires scoping beyond the line. A single nullable foreign key achieves it with database-enforced integrity and a two-step resolution rule.

### 34.12 Machine type failure mode as a full junction

**Chosen:** A junction with five parents carrying severity, indicator, part, duration, warning period, predictability, and frequency.

**Rejected:** Failure categories scoped to machine categories via a single foreign key. Simpler, and it would lose the type-specific severity, the per-type telemetry signature, the part link, and the warning period — which is most of what makes root-cause reasoning specific rather than generic.

This is the model's most connected entity, and the connectivity is the point: it is the single join where equipment, failure taxonomy, severity policy, telemetry, and inventory meet, which is exactly what the Decision Agent needs from one lookup.

### 34.13 Bill of materials without a versioned header

**Chosen:** A single line-level entity with `effective_from_date`.

**Rejected:** The conventional ERP structure of a versioned header plus component lines. Versioning exists so an ERP can reproduce the exact recipe used for a batch produced two years ago — genuine for warranty and recall traceability, and required by **no FactoryFlow AI component.** Every consumer asks the same question: what does this product consume now? The header would add a join, a version-resolution rule, and an active-version constraint to serve a use case outside scope.

### 34.14 Machine lifecycle status instead of an active flag

**Chosen:** `machine.lifecycle_status` with four values, replacing the standard `is_active` boolean.

**Rejected:** Carrying both. Two overlapping flags for one fact, and they would eventually disagree.

**Rejected:** `is_active` alone. A boolean cannot distinguish `standby` from `under_overhaul` from `decommissioned`, and the Monitoring Agent needs that distinction — a standby machine is not a fault, an overhauled one is expected to be offline, and a decommissioned one should leave the pipeline entirely.

This is the model's only documented exception to the convention in §3.3, and it is recorded there rather than left as a surprise.

## 35. Deferred extensions

Structures a reviewer might expect from a full ERP, deliberately excluded, mapped to the companion overview's roadmap. **None of these may be pre-built.** The corresponding structure is added when a consumer needs it, shaped by real requirements rather than anticipation.

| Deferred | What it would enable | Phase | Why not now |
|---|---|---|---|
| `supplier_item` multi-sourcing | Alternate-source recommendations with per-supplier lead time and price | 4 | `primary_supplier_id` answers the question actually asked |
| Versioned BOM header | Batch-level recipe traceability | — | No consumer; warranty and recall are out of scope |
| Multi-level BOM recursion | Sub-assembly explosion | — | No consumer; would require recursive traversal |
| `worker_shift_roster` | Actual rotation rather than default shift | 4 | Shift rotation is operational scheduling |
| Engineer skill matrix junction | Per-machine-category qualification | 2 | Specialization matching already resolves qualification |
| Machine sub-component hierarchy | Failure prediction at component level | 2 | Machine-level prediction is the current target |
| Tooling instance tracking | Individual tool life histories | 2 | Tool wear is tracked per machine, which is sufficient |
| Quality specification limits | Dimensional tolerance monitoring | — | Quality management is explicitly out of scope |
| Energy tariff structure | Cost-optimised scheduling | — | No consumer |
| Multi-plant hierarchy | Several sites under one platform | 6 | Existing `plant_id` foreign keys already accommodate additional rows |
| Access control and role permissions | Role-based views and authorisation | 6 | Not required to demonstrate the core capability |
| Contractor and external vendor staff | Third-party maintenance assignment | 4 | Internal teams cover the current scope |

The pattern across this table: each item is realistic, each is present in real ERPs, and each fails the §1.4 justification test on the **named consumer** criterion. That is the test that keeps a master data model from growing into a schema nobody can explain.

---

# Part VI — Governance

## 36. Document authority and change control

**Authority.** This document is the blueprint for the FactoryFlow AI factory master data. The SQLite schema, SQLAlchemy models, simulator seed data, and every agent's queries derive from it. Where implementation reality requires a change, **this document is revised first** and the rationale recorded.

**Relationship to the companion overview.** `PROJECT_OVERVIEW.md` defines what FactoryFlow AI is and the principles it must uphold. This document defines the static structure of the factory those principles operate on. Where the two touch — the master/operational boundary, explainability, single responsibility, deliberate simplicity — this document implements what the overview requires. It does not restate or override it.

**Change control.**

| Change | Requires |
|---|---|
| New attribute | A stated business reason and at least one named consumer |
| New entity | Passing all three §1.4 tests: business reason, named consumer, no duplication |
| New relationship | Confirming the dependency graph stays acyclic and updating §30 |
| Removing an attribute | Confirming no consumer reads it and no operational data depends on it |
| Changing an enumerated set | Checking every shared-vocabulary counterpart in §32.3 |
| New business rule value | A new row, never an in-place edit — the audit trail depends on it |

## 37. Non-negotiable design constraints

These may not be changed without explicitly revising this document.

1. **The master/operational boundary holds.** No sensor readings, no machine run status, no production counts, no stock quantities, no orders, no events, no predictions, no recommendations, no notification logs. §2.2 is the full exclusion list.
2. **Every entity has a surrogate primary key.** Foreign keys reference `_id`, never a business code, and application logic never parses a code to derive a relationship.
3. **The dependency graph stays acyclic.** Leadership is a child attribute, never a parent back-reference.
4. **Nothing derivable is stored.** Machine area, line plant, machine count, maintenance due date, stock on hand.
5. **A person is stored once.** Personal and contact data lives only on `worker`. Specializations add role-specific attributes and never repeat identity.
6. **Master data is never hard-deleted.** Soft retirement only, because operational history references it and the audit trail must survive.
7. **Every entity has a named consumer.** An entity no component reads does not belong in the model.
8. **Shared vocabularies stay synchronised.** The specialization vocabulary spans three entities and must be maintained across all of them.
9. **The threshold ordering rule holds.** Physical bounds ⊇ alert limits ⊇ healthy envelope, on every parameter of every profile.
10. **Configuration is data, not code.** Thresholds, escalation cut-offs, cost rates, and notification policy live in tables so that platform behavior is inspectable and every decision is explainable.

## 38. Coverage confirmation

**All 24 required entities are present**, with the mapping in §4.3. Five entities were added — `machine_parameter`, `machine_type_parameter`, `product_line_capability`, `machine_type_failure_mode`, `alert_threshold_rule` — each with a named consumer stated in §4.3 and §31.

**All eight required subsections** are documented for each of the 29 entities: purpose, business description, primary key with justification, attributes with type, requirement, example, validation and business meaning, relationships with cardinality, business rules, example records, and downstream consumers.

**Required deliverables:**

| Required | Location |
|---|---|
| Entity relationship model with parents, children, lookups, and configuration entities | §30.1, §30.2, §30.6 |
| Circular dependency avoidance | §30.4, with the three designed-out cycles recorded |
| Master data quality supporting all named capabilities | §31, §32, §33 |
| Exclusion of operational data | §2.2, §2.3, §30.7 |

**Constraints observed.** No SQL, no ORM models, no APIs, no simulator logic, no frontend code, no placeholder files. One document, no folders created.

---

*End of Factory Master Data Design.*
