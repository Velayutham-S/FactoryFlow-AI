# FactoryFlow AI — Factory Operational Data Design

**Production-Grade Dynamic Data Model for an Agentic Manufacturing Monitoring Platform**

---

| Field | Value |
|---|---|
| Project | FactoryFlow AI |
| Document Type | Factory Operational Data Design (Logical Data Model) |
| Phase | Phase 0 — Foundation. Precedes schema, simulator, and agent implementation |
| Document Status | Baseline blueprint for every dynamic entity |
| Depends On | `PROJECT_OVERVIEW.md` (frozen), `FACTORY_MASTER_DATA_DESIGN.md` (frozen) |
| Realised By | `FACTORY_SQLITE_DATABASE_SCHEMA.md` (physical schema), `FACTORY_SQLALCHEMY_MODEL_SPECIFICATION.md` (ORM layer) |
| Target Database | SQLite 3 — a single embedded database file, accessed through Python's `sqlite3` module |
| Scope | Design only. No SQL, no ORM models, no APIs, no simulator logic, no agent logic, no UI |
| Entity Count | 24 operational entities across 8 functional groups |

> **Purpose.** The master data document answers *"what exists in this factory?"* This document answers *"what is happening in it?"* It defines every dynamic entity produced while the factory runs — telemetry, production execution, material movement, maintenance, detection, prediction, reasoning, decision, delivery, and platform observability. Every future SQLite table, simulator module, agent query, dashboard view, and analytics job derives from it.

> **Boundary commitment.** This document **references** master data and never redefines it. No attribute of `machine`, `product`, `worker`, `production_line`, `inventory_item`, `business_rule`, `alert_threshold_rule`, `failure_category`, or any other master entity is copied here. Where operational data needs a master fact, it holds a foreign key and nothing more.

---

## Table of Contents

- [Part I — Foundations](#part-i--foundations)
  - [1. Document Objective](#1-document-objective)
  - [2. The Operational Data Boundary](#2-the-operational-data-boundary)
  - [3. Design Conventions](#3-design-conventions)
  - [4. Time-Series Design Taxonomy](#4-time-series-design-taxonomy)
  - [5. Entity Catalogue](#5-entity-catalogue)
- [Part II — Entity Designs](#part-ii--entity-designs)
  - [Group A — Machine Telemetry & State](#group-a--machine-telemetry--state)
  - [Group B — Production Execution](#group-b--production-execution)
  - [Group C — Material & Maintenance](#group-c--material--maintenance)
  - [Group D — Detection](#group-d--detection)
  - [Group E — Prediction](#group-e--prediction)
  - [Group F — Reasoning & Decision](#group-f--reasoning--decision)
  - [Group G — Delivery](#group-g--delivery)
  - [Group H — Platform & Observability](#group-h--platform--observability)
- [Part III — Ownership, Simulator Contract, Retention](#part-iii--ownership-simulator-contract-retention)
- [Part IV — Lifecycles & Pipelines](#part-iv--lifecycles--pipelines)
- [Part V — Relationship Model](#part-v--relationship-model)
- [Part VI — Governance](#part-vi--governance)

**Reference convention.** Cross-cutting sections are numbered **§1–§16** continuously across Parts I, III, IV, V, and VI. The 24 entity designs in Part II use a separate **E1–E24** series. So `§9.3` is the reconciliation rules section, while `§E9` is the `scrap_record` entity. References to the two frozen companion documents always name them explicitly — `PROJECT_OVERVIEW.md §16.5`, or "master data §26".

---

# Part I — Foundations

## 1. Document Objective

### 1.1 What this document defines

The **Factory Operational Data** is the continuously growing record of factory activity. It is created by the simulator and by every agent in the pipeline, and it is never authored by hand.

It answers questions master data cannot:

| Question | Answered by |
|---|---|
| What did MC-0101 report at 18:42:10? | `machine_sensor_reading` |
| Is MC-0101 running right now, and since when? | `machine_operational_status` |
| How much of run RUN-2026-0714 is complete? | `production_progress` |
| Is the spindle bearing in stock, and how much is left? | `inventory_movement` |
| Was the 500-hour service performed, and when? | `maintenance_work_record` |
| What condition did the Monitoring Agent detect? | `operational_event` |
| Is that condition still open, and has anybody acknowledged it? | `operational_alert` |
| How likely is failure, and on what evidence? | `prediction_result`, `prediction_feature_snapshot` |
| Why was this escalated and that one suppressed? | `supervisor_context` |
| What did the platform recommend, and why? | `ai_recommendation` |
| What did the manager actually decide? | `recommendation_action` |
| Did the message reach anybody? | `notification`, `notification_delivery` |

### 1.2 Position in the pipeline

```
FACTORY MASTER DATA  ────────── frozen, referenced by everything below
        │
        ▼
Factory Simulator ──────────► machine_sensor_reading, machine_operational_status,
                              machine_state_transition, production_run,
                              production_progress, production_count, cycle_history,
                              quality_inspection_result, scrap_record,
                              inventory_movement, machine_maintenance_activity
        │
        ▼
Operational Database ───────► the persistence layer for everything in this document
        │
        ▼
Monitoring Agent ───────────► operational_event, operational_alert
        │
        ▼
Prediction Agent ───────────► prediction_feature_snapshot, prediction_result
        │
        ▼
Supervisor Agent ───────────► supervisor_context
        │
        ▼
Decision Agent ─────────────► ai_recommendation
        │
        ▼
Notification Service ───────► notification, notification_delivery
        │
        ▼
Dashboard ──────────────────► dashboard_snapshot, recommendation_action
        │
        ▼
Factory Manager ────────────► the human decision, recorded as recommendation_action
```

Two entities sit outside the pipeline and serve all of it: `audit_log` and `system_health_status`.

### 1.3 The justification test

Every entity here passes the same three tests the master data model applied:

1. **Business reason.** A real MES would record this, for a stated operational purpose.
2. **Named consumer.** At least one FactoryFlow AI component reads it, named in the entity's design.
3. **No duplication.** The information exists in exactly one place, and anything derivable is derived — or, where a running total is genuinely required for performance, it is declared as a maintained total with a stated reconciliation rule.

### 1.4 What makes operational data different

Master data is small, hand-authored, and mostly static. Operational data is the opposite, and four properties follow from that:

| Property | Consequence for the design |
|---|---|
| **Unbounded growth** | Retention policy is mandatory per entity, not optional. §8 |
| **Machine-authored** | Every entity has exactly one writing component. Shared writes would make provenance unrecoverable. §6 |
| **Time is a first-class dimension** | Every entity carries an explicit event time, and the taxonomy in §4 classifies how each behaves over time |
| **History is evidence** | Append-only by default. Mutation is the exception and must be justified, because the explainability contract depends on being able to reconstruct what was known at decision time |

---

## 2. The Operational Data Boundary

### 2.1 The two-sided rule

The master data document (§2.1) defined the boundary from its side. This document restates it from the operational side, so the two are checkable against each other.

| | Master Data (frozen) | Operational Data (this document) |
|---|---|---|
| Answers | What exists | What is happening, what happened |
| Change frequency | Days to months | Seconds to minutes |
| Authored by | Humans, via configuration | The simulator and the agents |
| Row growth | Bounded, ~245 rows total | Unbounded, millions |
| Mutability | Edited occasionally, soft-retired | Append-only by default |
| Deletion | Never — soft retire only | Archived and purged on a retention schedule |
| Example | MC-0101 is a VMC-500 at position 1 on LN-01 | MC-0101 reported 4.8 mm/s vibration at 18:42:10 |

### 2.2 Master entities referenced, never redefined

All 29 master entities are available by reference. The following are referenced most heavily, and **not one of their attributes is copied into any operational entity**:

| Master entity | Referenced by operational entities for |
|---|---|
| `machine` | The asset every reading, event, prediction, and repair belongs to |
| `machine_parameter` | The typed, unit-aware definition of every reading |
| `production_line` | The unit at which output and impact are assessed |
| `product` | What is being produced |
| `customer` | Whose order is at risk |
| `worker` | Who inspected, acknowledged, or repaired |
| `inventory_item`, `inventory_location` | What material moved, and from where |
| `failure_category`, `failure_severity_level` | The failure taxonomy and urgency scale |
| `alert_threshold_rule` | The rule that fired |
| `machine_maintenance_schedule` | The policy a maintenance job fulfils |
| `maintenance_team`, `maintenance_engineer` | Who was assigned |
| `notification_recipient` | Who receives what, on which channel |
| `business_rule` | The policy value that drove an escalation or a cost figure |
| `shift` | The crew on duty when something happened |

**The anti-duplication rule, stated concretely.** An operational row records `machine_id`, not the machine's name, type, line, or criticality. It records `alert_threshold_rule_id`, not the limit value that was breached — with **one deliberate exception** covered in §3.5, where the threshold in force at detection time is captured for evidentiary reasons. That exception is the only place in this document where a master value is copied, and the reason is stated where it occurs.

### 2.3 Deliberately absent from operational data

| Not included | Why |
|---|---|
| Any master attribute copy | Referenced by foreign key. §2.2 |
| Machine specifications, thresholds, stocking policy | Master data |
| Financial ledger, invoicing, payroll | Outside the decision support problem |
| Operator keystroke or UI interaction telemetry | No consumer; would be surveillance, not monitoring |
| Raw LLM request and response payloads | `ai_recommendation` stores the structured output and the reasoning text. Storing raw prompts and token streams serves no consumer and would retain data indefinitely for no purpose |
| Machine control commands or setpoint writes | The platform is advisory. No control path exists anywhere in the architecture |

The last row is a hard architectural boundary inherited from `PROJECT_OVERVIEW.md`. There is no operational entity in this model that represents an instruction to a machine, and there must never be one.

---

## 3. Design Conventions

Stated once, applied to all 24 entities.

### 3.1 Primary key strategy

**Every operational entity uses a surrogate `INTEGER PRIMARY KEY AUTOINCREMENT` named `<entity>_id`.**

Volume is the reason to think about key width at all: `machine_sensor_reading` alone generates roughly 2.6 million rows per month at the sampling intervals declared in master data, so a 32-bit key would exhaust within a few years of operation. **SQLite removes the decision rather than requiring a wider type.** It exposes one integer type — a signed 64-bit value stored in 1 to 8 bytes according to magnitude — so operational keys and master keys are declared identically and there is no exhaustion horizon inside the life of the plant.

**`INTEGER` is the required spelling, not merely the convenient one.** A column declared exactly `INTEGER PRIMARY KEY` becomes an alias for SQLite's internal 64-bit rowid, which is the fastest access path the engine has. A wider spelling such as `BIGINT` would make it an ordinary indexed column and would reject `AUTOINCREMENT` outright.

**`AUTOINCREMENT` is deliberate.** Without it SQLite assigns `max(rowid) + 1`, so a retention purge that removes the oldest telemetry would make those identifiers available for reuse — silently repointing anything that still referenced them. `AUTOINCREMENT` maintains a high-water mark and never reissues a key, which is what the evidence trail in §9 depends on.

**Business codes only where a human or an AI-generated message refers to the row.** Master data gave nearly every entity a `_code` because people name machines and products. Operational rows are mostly machine-authored and machine-read, and inventing codes for them would create identifiers nobody uses.

| Entity | Code format | Example | Why a code is needed |
|---|---|---|---|
| `production_run` | `RUN-<yyyy>-<nnnn>` | `RUN-2026-0714` | Named by planners and operators on the shop floor |
| `operational_event` | `EVT-<yyyymmdd>-<nnnn>` | `EVT-20260729-0412` | Cited as supporting evidence in recommendations |
| `operational_alert` | `ALR-<yyyymmdd>-<nnnn>` | `ALR-20260729-0087` | Acknowledged and discussed by humans |
| `prediction_feature_snapshot` | `FSN-<yyyymmdd>-<nnnnn>` | `FSN-20260729-04188` | Cited for reproducibility and model audit |
| `prediction_result` | `PDN-<yyyymmdd>-<nnnn>` | `PDN-20260729-0203` | Cited as ML confidence evidence |
| `supervisor_context` | `CTX-<yyyymmdd>-<nnnn>` | `CTX-20260729-0044` | Cited when explaining an escalation decision |
| `ai_recommendation` | `REC-<yyyymmdd>-<nnnn>` | `REC-20260729-0031` | Referenced by managers and in notifications |
| `notification` | `NTF-<yyyymmdd>-<nnnnn>` | `NTF-20260729-00119` | Referenced in delivery support queries |
| `maintenance_work_record` | `WO-<yyyy>-<nnnn>` | `WO-2026-0341` | The work order number, used across the plant |
| `inventory_movement` | `MOV-<yyyymmdd>-<nnnnn>` | `MOV-20260730-00287` | Stores staff reference transaction numbers |
| `quality_inspection_result` | `QIR-<yyyymmdd>-<nnnn>` | `QIR-20260729-0158` | Referenced in quality records |

**No code:** `machine_sensor_reading`, `machine_operational_status`, `machine_state_transition`, `production_progress`, `production_count`, `cycle_history`, `scrap_record`, `machine_maintenance_activity`, `recommendation_action`, `notification_delivery`, `dashboard_snapshot`, `audit_log`, `system_health_status`. These are high-volume rows or child rows of a coded parent, and nobody names them.

`PDN-` is used for predictions rather than the more natural `PRD-` because master data already uses `PRD-` for product codes. A collision between a product and a prediction in an AI-generated message would be genuinely confusing.

### 3.2 Time attributes

Time is the organising dimension of this model, so its conventions are strict.

| Convention | Rule |
|---|---|
| All instants are `DATETIME` | Stored in UTC, rendered in `plant.timezone`. Never a naive local timestamp |
| **Event time vs record time are distinct** | `occurred_at` (or `recorded_at`) is when the thing happened. `created_at` is when the row was written. They differ under batch processing, retry, and replay |
| `*_at` for instants | `occurred_at`, `acknowledged_at`, `resolved_at` |
| `*_from` / `*_to` for ranges | `interval_from`, `interval_to` |
| Durations always name their unit | `duration_seconds`, `response_time_minutes`, `downtime_minutes` |
| Every entity carries the shift it occurred in | `shift_id` where meaningful, so analytics can segment by crew without recomputing shift windows from timestamps |

**Why event time and record time are both stored.** A recommendation says *"vibration has been rising since the start of B shift."* That claim is about event time. If the pipeline stalls for four minutes and processes a backlog, record time is four minutes later and using it would make the statement wrong. Separating them is what keeps time-based reasoning honest, and it is what makes replay possible: re-running the pipeline over historical data produces new record times against unchanged event times.

**UTC is a convention this design upholds, not something the column records.** SQLite has no timezone-aware type and stores no offset, so a `DATETIME` value is ISO-8601 text and nothing in the database distinguishes a UTC instant from a local one. Two obligations follow, and both belong to the writing component: every instant is converted to UTC before it is written, and a naive timestamp is rejected rather than assumed. Guessing wrong on `machine_sensor_reading.recorded_at` shifts every reading by the UTC offset, which moves readings across shift boundaries and misattributes hours of production — a silent corruption that surfaces weeks later as a shift-report anomaly.

**One property of the storage format is relied on throughout.** Because UTC ISO-8601 text is fixed-width, timestamps sort and compare chronologically as text. Every interval query, every shift-window comparison, and every "since" claim in this document therefore works directly on the stored value with no conversion.

**How to read the timestamp examples in this document.** Every example instant below is written in **plant-local time with its offset shown** — `2026-07-29 18:42:10+05:30` — because the worked incident in §5.4 is easier to follow against shift times a reader recognises, and because the example-record tables use the same clock. **The stored value is the same instant expressed in UTC with no offset:** `2026-07-29 13:12:10`. The offset appears in these examples for the reader's benefit and never in the column.

### 3.3 Implicit columns

| Column | Type | On | Purpose |
|---|---|---|---|
| `<entity>_id` | INTEGER, primary key, autoincrement | All 24 | Surrogate primary key |
| `created_at` | DATETIME | All 24 | When the row was written, in UTC. Immutable |
| `updated_at` | DATETIME | Mutable entities only | Last modification, in UTC. Absent from append-only entities, where its presence would imply mutability that does not exist |
| `created_by_component` | TEXT + CHECK | All 24 | Which component wrote the row: `simulator`, `monitoring_agent`, `prediction_agent`, `supervisor_agent`, `decision_agent`, `notification_service`, `dashboard`, `platform`. Provenance for debugging and for the ownership rule in §6 |

**`updated_at` is deliberately absent from append-only entities.** Eighteen of the 24 entities never change after insert. Carrying an `updated_at` on them would suggest they might, and would eventually tempt somebody to use it.

**`created_by_component` carries more weight here than provenance columns usually do.** SQLite has no users, no roles, and no per-table permissions: anything that can open the database file can write any table in it. The single-writer-per-entity rule in §6 is therefore a **convention** rather than something the engine enforces, and this column is what makes a breach of it detectable after the fact. It is `NOT NULL` with no default, so the writing component must state its identity on every insert.

### 3.4 Data types

Same logical vocabulary as master data, with two additions.

| Logical type | Used for | Notes |
|---|---|---|
| `INTEGER` | All primary keys, all foreign keys, counts, intervals | One integer type. SQLite's `INTEGER` is 64-bit signed, so operational and master keys are declared identically (§3.1) |
| `NUMERIC(p,s)` | Measurements, quantities, money, probabilities | Precision and scale are declared as design information and drive the range checks. Never handled as a floating-point value in application code |
| `DATETIME` | All instants | UTC by convention (§3.2) |
| `TEXT + CHECK` | Fixed operational vocabularies | State names, movement types, outcomes. The permitted values are listed per attribute and enforced by a named check constraint |
| `INTEGER` (flag) | Flags | `0` = false, `1` = true, `NOT NULL`, with a check constraint restricting it to those two values |
| `JSON document` | Structured payloads written once and read whole | JSON serialised into a `TEXT` column. Feature vectors, assembled context, snapshot aggregates. See §3.6 |
| `TEXT` | Reasoning narrative, notes, error detail | Unbounded |

**Three notes on how these land in SQLite 3**, so they are not repeated on every attribute.

**A declared type is documentation; a check constraint is enforcement.** SQLite is dynamically typed — a declared type gives a column an affinity, which steers how a value is stored, and does not restrict what can be stored. Wherever this design relies on a type to enforce a rule, the rule becomes a named check constraint instead. Nothing is relaxed; the rule changes layer.

**There is no `ENUM` type, and none is needed.** Every fixed vocabulary in this document is a `TEXT` column with `CHECK (column IN (...))` listing its permitted values, named `ck_<table>_<column>_allowed`. The value list is enforced for every writer and is visible in the schema itself rather than in a separate type object. Every vocabulary is preserved exactly, value for value.

**JSON lives in `TEXT`.** SQLite has no distinct JSON type; a document is serialised to a JSON string in a `TEXT` column, and well-formedness is enforced with a `json_valid()` check. §3.6 explains why four entities carry documents at all.

### 3.5 The one permitted master value copy

Operational rows reference master data and copy nothing from it — with one exception, stated here so it is never mistaken for drift.

`operational_event` captures **the threshold values that were in force at detection time**: the limit breached, and the observed value against it.

The reason is evidentiary. `alert_threshold_profile` is versioned and retuned; §27 of the master document describes the tuning cycle explicitly. If an event stored only `alert_threshold_rule_id`, then re-reading that rule six months later would return the *current* limit, not the limit that actually fired. A recommendation citing *"78.4 °C against a warning limit of 76 °C"* would silently become wrong the first time somebody retuned the profile.

This is the standard point-in-time capture pattern: **the rule is referenced for lineage, the value is captured for evidence.** It is not duplication, because the master row holds current policy and the event holds a historical observation. Nowhere else in this document is a master attribute copied.

### 3.6 On JSON documents

Four entities carry a structured document rather than child rows: `prediction_feature_snapshot`, `supervisor_context`, `ai_recommendation`, and `dashboard_snapshot`.

This deserves justification, because the master data document argued the opposite case for `alert_threshold_rule` — rows, not wide columns, so the Monitoring Agent could iterate generically.

The distinction is **whether individual elements are queried in SQL**:

| | `alert_threshold_rule` (master) | These four (operational) |
|---|---|---|
| Access pattern | Queried per parameter: *"what is the limit for PRM-VIB?"* | Written once, read whole, never queried element by element |
| Shape | Fixed and known | Varies per machine type and per situation |
| Consumer | The Monitoring Agent iterates it | An agent or the UI consumes the whole payload |
| Right model | Child rows | A document |

A feature snapshot is read entirely or not at all — the model needs the whole vector. Decomposing it into rows would add a join and a reassembly step to serve a query pattern that never occurs. The principle being applied is the same in both cases: **model to the access pattern.** It simply produces different answers for different access patterns.

Every document-valued attribute in this design states its expected structure in the entity's business description, so the payload is specified rather than free-form.

**How a document is stored.** SQLite has no dedicated JSON type. Each document is serialised to a JSON string and held in a `TEXT` column, with a `json_valid()` check constraint so a malformed payload is rejected at write time rather than surfacing in whichever component tried to parse it. Where the document must be an object rather than an array, a `json_type()` check states that too.

**What the database does and does not guarantee about these payloads.** It guarantees the text is well-formed JSON. It does not validate the *shape* — the keys a feature vector carries, or the sections an assembled context contains — because that shape is the writing component's contract and is versioned by the component, not by the schema. `prediction_feature_snapshot.feature_set_version` exists for exactly that reason.

**One consequence for readers.** A document is replaced whole, never edited in place. That is already implied by three of the four entities being append-only, and it holds for `dashboard_snapshot` too: a rebuild writes a new document rather than amending the previous one. SQLite's `json_set()` and related functions are available and are deliberately not used here, because a partial in-place edit would make the payload disagree with the version that describes its shape.

### 3.7 Volume classes

Per-entity growth at the sampling intervals and factory size declared in master data — eight machines, seven monitored, four lines, three shifts, six operating days per week.

| Class | Approximate rows/day | Entities |
|---|---|---|
| **Very high** | 100k+ | `machine_sensor_reading` (~87k) |
| **High** | 1k–100k | `cycle_history` (~3.5k), `production_count` (~1.6k), `audit_log` (~2k) |
| **Moderate** | 100–1k | `production_progress` (~380), `machine_state_transition` (~200), `dashboard_snapshot` (~288), `prediction_feature_snapshot` (~170), `prediction_result` (~170) |
| **Low** | 10–100 | `operational_event` (~40), `inventory_movement` (~60), `supervisor_context` (~25), `quality_inspection_result` (~30), `scrap_record` (~15) |
| **Very low** | under 10 | `operational_alert`, `ai_recommendation`, `notification`, `notification_delivery`, `recommendation_action`, `maintenance_work_record`, `machine_maintenance_activity` |
| **Fixed** | 0 net | `machine_operational_status` (8 rows), `system_health_status` (~8 rows) |

The shape of this table is the point. Volume falls by roughly four orders of magnitude from raw telemetry to delivered recommendations, which is **progressive filtering made visible in the data model.** 87,000 readings a day become around 40 events, around 25 escalation decisions, and a handful of recommendations. That is the architecture from `PROJECT_OVERVIEW.md` §5.3 expressed as row counts, and it is the reason expensive LLM reasoning is affordable.

---

## 4. Time-Series Design Taxonomy

The brief requires that entities be classified by temporal behavior. Six classes, each with a distinct reason for existing.

### 4.1 The six classes

| Class | Definition | Mutability | Recomputable | Entities |
|---|---|---|---|---|
| **Streaming** | Continuous high-frequency measurement arriving on a fixed interval | Immutable | No — source of truth | `machine_sensor_reading` |
| **Append-only historical** | Discrete facts recorded as they occur, never revised | Immutable | No — source of truth | `machine_state_transition`, `cycle_history`, `quality_inspection_result`, `scrap_record`, `inventory_movement`, `machine_maintenance_activity`, `operational_event`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `ai_recommendation`, `recommendation_action`, `notification`, `notification_delivery`, `audit_log` |
| **Snapshot** | Periodic capture of a computed state at a point in time | Immutable | Yes, from sources | `production_progress`, `dashboard_snapshot` |
| **Aggregated** | Pre-computed rollup over an interval | Immutable | Yes, from finer-grained data | `production_count` |
| **Current-state** | Exactly one row per subject, overwritten in place | Mutable | Yes, from history | `machine_operational_status`, `system_health_status` |
| **Lifecycle** | A case that advances through defined states over time | Mutable | No — carries human decisions | `production_run`, `operational_alert`, `maintenance_work_record` |

### 4.2 Why each class exists

**Streaming** is the raw signal. One entity, the highest volume in the model, and the ultimate source of evidence for every claim the platform makes about machine condition. Immutable because a reading is an observation: correcting it would falsify history. Sensor faults are flagged, never edited.

**Append-only historical** is the model's default and covers 15 of 24 entities. Each row is a fact that happened at a moment. The explainability contract in `PROJECT_OVERVIEW.md` §16.5 requires that any recommendation be traceable to the evidence that produced it, and that is only possible if the evidence cannot be rewritten. A mutable prediction row would make *"why did it say that?"* unanswerable.

**Snapshot** solves a problem append-only history cannot: *"what did this look like at 14:00?"* Progress and dashboard state are computed values, and recomputing them for an arbitrary past moment would require replaying every underlying row. Capturing them periodically makes historical review cheap. Recomputable, therefore safe to purge aggressively.

**Aggregated** exists purely for query cost. `production_count` is derivable by summing `cycle_history`, but OEE calculations and dashboard charts would otherwise scan millions of cycle rows. One entity, explicitly declared derived, with a stated reconciliation rule against its source.

**Current-state** answers *"what is true right now?"* without scanning history. Machine state is the clearest case: the Monitoring Agent needs the current state of eight machines on every evaluation cycle, and deriving it from the transition log each time would be wasteful. Mutable, and paired with an append-only history entity so nothing is lost — `machine_operational_status` is the current row, `machine_state_transition` is the record of how it got there.

**Lifecycle** entities carry state machines that advance over hours or days and record human decisions. They are the only entities that are both mutable and non-recomputable, because their state reflects choices that exist nowhere else. An alert that a supervisor acknowledged at 18:51 cannot be recomputed from telemetry.

### 4.3 The current-state and history pairing

Three places in this model pair a mutable current-state or lifecycle entity with an immutable history entity. The pattern is deliberate and worth naming.

| Current / lifecycle | Paired history | What the pairing buys |
|---|---|---|
| `machine_operational_status` | `machine_state_transition` | Fast "what now" plus complete "how it got here" |
| `operational_alert` | `operational_event` | A managed case plus the immutable observations that justify it |
| `maintenance_work_record` | `machine_maintenance_activity` | A job header plus the append-only timeline of what was actually done |

In every case the mutable row is **reconstructible** from its history. That is what makes the mutability safe: the current row is a performance convenience, not a source of truth. If it is ever wrong, the history can rebuild it, and §9.3 states that reconciliation as a data quality requirement.

---

## 5. Entity Catalogue

### 5.1 Full catalogue

| # | Entity | Group | Class | Owner | One-line purpose |
|---|---|---|---|---|---|
| 1 | `machine_sensor_reading` | A | Streaming | Simulator | Raw parameter measurements over time |
| 2 | `machine_operational_status` | A | Current-state | Simulator | What each machine is doing right now |
| 3 | `machine_state_transition` | A | Append-only | Simulator | Every state change, with duration |
| 4 | `production_run` | B | Lifecycle | Simulator | Execution of a product order on a line |
| 5 | `production_progress` | B | Snapshot | Simulator | Periodic completion and rate capture per run |
| 6 | `production_count` | B | Aggregated | Simulator | Good, scrap, and rework counts per machine per interval |
| 7 | `cycle_history` | B | Append-only | Simulator | Individual machine cycles with timing deviation |
| 8 | `quality_inspection_result` | B | Append-only | Simulator | Inspection outcomes against sampled output |
| 9 | `scrap_record` | B | Append-only | Simulator | Scrapped quantity with attributed cause |
| 10 | `inventory_movement` | C | Append-only | Simulator | The stock ledger — every issue, receipt, and adjustment |
| 11 | `maintenance_work_record` | C | Lifecycle | Simulator | A maintenance job from request to closure |
| 12 | `machine_maintenance_activity` | C | Append-only | Simulator | Timeline of steps performed within a job |
| 13 | `operational_event` | D | Append-only | Monitoring Agent | An immutable detected condition with its evidence |
| 14 | `operational_alert` | D | Lifecycle | Monitoring Agent | The managed case correlating related events |
| 15 | `prediction_feature_snapshot` | E | Append-only | Prediction Agent | The exact feature vector used for one inference |
| 16 | `prediction_result` | E | Append-only | Prediction Agent | Failure probability, risk class, and predicted mode |
| 17 | `supervisor_context` | F | Append-only | Supervisor Agent | The escalation decision and assembled context |
| 18 | `ai_recommendation` | F | Append-only | Decision Agent | The explainable recommendation, contract-complete |
| 19 | `recommendation_action` | F | Append-only | Dashboard | What the human actually decided |
| 20 | `notification` | G | Append-only | Notification Service | A message composed for one recipient |
| 21 | `notification_delivery` | G | Append-only | Notification Service | One delivery attempt on one channel |
| 22 | `dashboard_snapshot` | H | Snapshot | Dashboard | Materialised factory state for fast rendering and replay |
| 23 | `audit_log` | H | Append-only | Platform | Significant system and human actions |
| 24 | `system_health_status` | H | Current-state | Platform | Liveness and lag of each pipeline component |

### 5.2 Group summary

| Group | Entities | Purpose |
|---|---|---|
| **A — Machine Telemetry & State** | 3 | What the machines are doing and reporting |
| **B — Production Execution** | 6 | What is being made, how fast, and to what quality |
| **C — Material & Maintenance** | 3 | What was consumed and what was repaired |
| **D — Detection** | 2 | What conditions were detected, and their managed lifecycle |
| **E — Prediction** | 2 | What is likely to fail, on what evidence |
| **F — Reasoning & Decision** | 3 | What it means, what to do, and what was decided |
| **G — Delivery** | 2 | Who was told, and whether it arrived |
| **H — Platform & Observability** | 3 | Presentation, audit, and pipeline health |

### 5.3 Coverage against the brief

All 24 required entities are present, with two named exactly as the brief specifies and the remainder using the model's `snake_case` convention.

| Required | Modelled as | Note |
|---|---|---|
| Machine Sensor Reading | `machine_sensor_reading` | |
| Machine Operational Status | `machine_operational_status` | Current-state, one row per machine |
| Machine State Transition | `machine_state_transition` | Append-only history of the above |
| Production Run | `production_run` | Carries the customer commitment |
| Production Progress | `production_progress` | Run-level, cumulative, snapshot |
| Production Count | `production_count` | Machine-level, per-interval, aggregated |
| Cycle History | `cycle_history` | Per-cycle, the finest production grain |
| Quality Inspection Result | `quality_inspection_result` | |
| Scrap Record | `scrap_record` | Separate from inspection: a finding is not a disposition |
| Inventory Movement | `inventory_movement` | The ledger. Current stock is its running balance |
| Machine Maintenance Activity | `machine_maintenance_activity` | Timeline entries within a job |
| Maintenance Work Record | `maintenance_work_record` | The job header and its lifecycle |
| Operational Event | `operational_event` | Immutable observation |
| Operational Alert | `operational_alert` | Mutable managed case |
| Prediction Result | `prediction_result` | |
| Prediction Feature Snapshot | `prediction_feature_snapshot` | The reproducibility contract |
| Supervisor Context | `supervisor_context` | Records suppressions as well as escalations |
| AI Recommendation | `ai_recommendation` | Implements the §16.5 explainability contract |
| Recommendation Action | `recommendation_action` | The human-in-the-loop record |
| Notification | `notification` | |
| Notification Delivery | `notification_delivery` | Per-channel attempt, with retries |
| Dashboard Snapshot | `dashboard_snapshot` | |
| Audit Log | `audit_log` | |
| System Health Status | `system_health_status` | |

**No entities were added.** The master data model added five beyond its brief because the simulator and agents needed vocabulary that did not exist. Here the required 24 are sufficient, and two candidates were considered and rejected:

| Considered | Rejected because |
|---|---|
| `inventory_balance` current-state entity | `inventory_movement.resulting_quantity_on_hand` carries the running balance, making current stock a single indexed lookup. A separate balance table would be a second source of truth for the same number |
| `prediction_feature_value` child rows | Feature vectors are written once and read whole, never queried per feature. §3.6 |

### 5.4 The worked incident used throughout

Every example record in Part II belongs to one coherent incident, so the pipeline can be traced end to end across all 24 entities rather than examined entity by entity in isolation.

> **The MC-0101 bearing incident, 29–30 July 2026.**
>
> `MC-0101` (Housing Rough Mill, a `MTY-VMC-500`, position 1 on `LN-01`, the line's bottleneck with an 18-unit downstream buffer) is running `RUN-2026-0714` — 480 units of `PRD-GH-100` for `CUS-001` Apex Drivetrain Systems, a Gold-tier customer, due 3 August. The machine runs the tightened monitoring profile `ATP-VMC-TIGHT` because it is the constraint on a critical line.
>
> During B shift on 29 July, vibration drifts from its 2.1 mm/s nominal toward 4.8 mm/s — past the 4.5 healthy maximum and the 4.7 warning limit. Spindle temperature rises with it. The Monitoring Agent raises events and opens an alert. The Prediction Agent returns a 0.68 failure probability for bearing degradation within 72 hours. The Supervisor Agent escalates, because `LN-01` carries a 0.55 escalation threshold rather than the global 0.70. The Decision Agent recommends a planned bearing replacement at the next shift change, combined with the preventive service already due. The line supervisor accepts with a modification. A work order is raised, the bearing is issued from stores, and the repair completes on the morning of 30 July.

All figures are consistent with the master data seed values in `FACTORY_MASTER_DATA_DESIGN.md`.

---

# Part II — Entity Designs

Each entity is documented under nine consistent headings: purpose, business description, primary key, attributes, relationships, lifecycle, business rules, example records, and consumers.

Throughout, **master data references are foreign keys only.** Where an example shows a master code such as `MC-0101` or `PRM-VIB`, that is the referenced row rendered readably — the stored value is the surrogate key.

---

## Group A — Machine Telemetry & State

What the machines are reporting, what they are doing, and how they got there. This group is the foundation of the entire pipeline: every event, prediction, and recommendation traces back to it.

The group applies the **current-state and history pairing** from §4.3. `machine_operational_status` answers *"what now?"* in one indexed row per machine. `machine_state_transition` answers *"how did it get here?"* as immutable history. The first is reconstructible from the second, which is what makes its mutability safe.

---

### E1. `machine_sensor_reading`

**Purpose**

Records every parameter measurement taken from every monitored machine. It is the raw signal of the platform and the ultimate evidence behind every claim the system makes about machine condition.

**Business description**

This is the highest-volume entity in the model and the base of the evidence chain. When a recommendation states *"vibration reached 4.8 mm/s at 18:42"*, this is the row that proves it.

Each reading is one value of one parameter on one machine at one instant. Which parameters a machine reports, in what unit, within what physical bounds, and how often, is entirely determined by master data — `machine_type_parameter` declares the parameter set and sampling interval, `machine_parameter` supplies the unit and physical range. **This entity stores none of that.** It stores the machine, the parameter, the time, and the value.

Two attributes beyond the obvious carry real weight.

**`quality_flag`** separates a machine problem from an instrument problem. The master data model (§11, §28) is explicit that readings outside a parameter's physical range indicate sensor failure rather than machine failure, and must never reach the Prediction Agent as valid input. That rule needs somewhere to be recorded, and this is it. A reading flagged `out_of_physical_range` is retained as a data quality fact and excluded from feature generation — which is what prevents a broken sensor from producing a confident wrong prediction.

**`machine_state_at_reading`** captures what the machine was doing when the value was taken. A 4.8 mm/s vibration reading means something entirely different on a running spindle than on one in setup. This is a **declared denormalisation**: the same fact is derivable by joining `machine_state_transition` on a time range, but doing that for every reading during feature extraction would be the most expensive query in the model. It is safe because the reading is immutable and both entities are written by the same component, so the two cannot drift. §9.3 states the reconciliation rule.

**Note on what this entity does not carry.** There is no `is_anomalous` flag and no severity. Whether a reading is abnormal is the Monitoring Agent's judgement, made against `alert_threshold_rule`, and it is recorded on `operational_event`. Allowing a second component to write to a row the simulator owns would break the ownership rule in §6 and make provenance unrecoverable.

**Primary key**

`machine_sensor_reading_id` — surrogate `INTEGER`.

No business code: nobody names an individual reading. This is the entity where key range matters most — at the sampling intervals declared in master data it generates roughly 87,000 rows per day — and SQLite's `INTEGER` is a signed 64-bit rowid, so there is no exhaustion horizon to plan around (§3.1). The declared type must be exactly `INTEGER`; a wider spelling would forfeit the rowid alias and reject `AUTOINCREMENT`.

A composite natural key of (`machine_id`, `machine_parameter_id`, `recorded_at`) was considered. It is the conventional time-series key and would be defensible, but it was rejected because `operational_event` needs to cite the specific readings that triggered it, and a three-column foreign key propagating into the event evidence payload is considerably worse than one `INTEGER`.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine` with `is_monitored = 1` and `lifecycle_status = 'in_service'` | The asset the reading came from |
| `machine_parameter_id` | INTEGER (FK master) | Yes | `PRM-VIB` | References `machine_parameter` declared for this machine's type in `machine_type_parameter` | What was measured. Unit and physical range come from master data |
| `recorded_at` | DATETIME | Yes | `2026-07-29 18:42:10+05:30` | Not in the future | **Event time** — when the measurement was taken. All trend and rate reasoning uses this, never `created_at` |
| `reading_value` | NUMERIC(12,4) | Yes | `4.8000` | Numeric; range validated against `machine_parameter` physical bounds, with breaches flagged rather than rejected | The measured value, in the parameter's declared unit |
| `quality_flag` | TEXT + CHECK | Yes | `valid` | One of: `valid`, `out_of_physical_range`, `sensor_offline`, `interpolated`, `stale` | **Data quality verdict.** Only `valid` readings reach feature generation. Separates instrument failure from machine failure |
| `machine_state_at_reading` | TEXT + CHECK | Yes | `running` | One of the eight states in §E2; must match the machine's state at `recorded_at` | What the machine was doing. **Declared denormalisation** — makes a reading interpretable without a time-range join |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift`; must contain `recorded_at` in plant-local time | Crew on duty. Lets analytics segment by shift without recomputing shift windows over 87,000 rows a day |
| `production_run_id` | INTEGER (FK op) | No | `RUN-2026-0714` | References `production_run` when present | The run active at the time. NULL when the machine was idle, in setup, or down — which is itself meaningful |
| `sequence_number` | INTEGER | Yes | `4188201` | Monotonically increasing per machine | Ordering guarantee within a machine when two readings share a timestamp. Makes replay deterministic |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine` | Master | Many-to-one | The asset measured |
| Parent | `machine_parameter` | Master | Many-to-one | What was measured |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `production_run` | Operational | Many-to-one, optional | Run active at the time |
| Referenced by | `prediction_feature_snapshot` | Operational | Many-to-many via the snapshot's source window | Readings aggregated into features |
| Referenced by | `operational_event` | Operational | Many-to-one | Events cite the readings that triggered them |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, on each parameter's declared sampling interval |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Monitoring Agent (threshold and rate evaluation), Prediction Agent (feature generation), Decision Agent (evidence citation), Dashboard (charts), Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A reading is an observation; correcting it would falsify history. Suspect readings are flagged, never edited |
| **Append-only** | Yes |
| **Expires** | Yes. Full resolution retained 90 days, then downsampled to hourly aggregates and the raw rows purged. §8 |
| **Regenerable** | No. This is a source of truth. Once purged, the original resolution is gone permanently — which is why downsampling precedes purge rather than replacing it |

**Business rules**

1. A reading may only exist for a machine that is `in_service` and `is_monitored = 1`. Unmonitored assets such as `MC-0103` produce no telemetry by design.
2. A reading may only exist for a parameter declared on that machine's type in `machine_type_parameter`. A reading for an undeclared parameter is a simulator defect, not data.
3. Values outside the parameter's physical range are **stored and flagged**, never discarded. Discarding them would hide instrument failure; a run of `out_of_physical_range` readings is exactly how a failed sensor is detected.
4. Only `quality_flag = 'valid'` readings enter `prediction_feature_snapshot`. This is the rule that keeps a faulty sensor from producing a confident wrong prediction.
5. Sampling interval is governed by `machine_type_parameter.sampling_interval_seconds`. The simulator does not choose its own rate.
6. `machine_state_at_reading` must agree with `machine_state_transition` for that machine at `recorded_at`. Reconciled periodically; a mismatch indicates a simulator ordering defect.
7. Readings are never back-dated. `recorded_at` is assigned at generation, and replay produces new rows with new `created_at` values against unchanged `recorded_at`.
8. Cumulative parameters — `PRM-TWEAR`, per `machine_parameter.is_cumulative` — reset to near zero after a tool change. The reset is visible as a discontinuity in the series and is legitimate, not a data error.

**Example records**

Vibration and temperature on `MC-0101` during the incident, 29 July 2026. Sampling interval is 10 seconds for both parameters on a `MTY-VMC-500`.

| machine | parameter | recorded_at | reading_value | quality_flag | state | shift | run |
|---|---|---|---|---|---|---|---|
| `MC-0101` | `PRM-VIB` | 18:41:50 | 4.6300 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0101` | `PRM-TEMP` | 18:41:50 | 73.4000 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0101` | `PRM-VIB` | 18:42:00 | 4.7400 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0101` | `PRM-TEMP` | 18:42:00 | 73.9000 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0101` | `PRM-VIB` | 18:42:10 | 4.8000 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0101` | `PRM-TEMP` | 18:42:10 | 74.2000 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0101` | `PRM-TORQ` | 18:42:10 | 71.5000 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0101` | `PRM-TWEAR` | 18:42:00 | 62.4000 | `valid` | `running` | `SH-B` | `RUN-2026-0714` |
| `MC-0202` | `PRM-PWR` | 18:42:00 | 3.0000 | `valid` | `starved` | `SH-B` | NULL |
| `MC-0301` | `PRM-VIB` | 18:42:10 | 12.4000 | `out_of_physical_range` | `running` | `SH-B` | `RUN-2026-0715` |

Reading this against the master data seed values shows the design working:

- **Vibration is crossing its limits in sequence.** The healthy maximum for `PRM-VIB` on a `MTY-VMC-500` is 4.5 mm/s. The `ATP-VMC-TIGHT` warning limit is 4.7 and critical is 6.0. At 18:41:50 the reading is above healthy but below warning; by 18:42:00 it has crossed warning; the rate of change is roughly 0.05 mm/s per 10 seconds, which is 0.3 per minute — exactly at the profile's 0.3 rate limit. Both a static breach and a rate breach are present, and §E13 shows the Monitoring Agent recording both.
- **Temperature is rising in step.** 74.2 °C against a 72.0 healthy maximum and a 73.5 tight warning limit. Two mechanical-domain and thermal-domain parameters drifting together is the signature `machine_type_failure_mode` describes for `FC-BRG` on this machine type.
- **`MC-0202` reports while `starved`.** A conveyor drawing 3.0 kW with no active run is waiting on the upstream weld cell. The reading is valid and the state explains it — which is why `machine_state_at_reading` is stored.
- **`MC-0301` shows an instrument fault.** 12.4 mm/s is within the parameter's 0–50 physical range but wildly outside anything a healthy lathe produces, and in this case the flag was set because the preceding readings were physically impossible. The row is retained, flagged, and excluded from features. Without the flag, this single value would be the strongest "evidence" in the model and would drive a confidently wrong prediction.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Generates values per `machine_type_parameter` envelope and sampling interval, applies degradation drift, and sets `quality_flag` when simulating sensor faults |
| **Monitoring Agent** | **Primary reader.** Evaluates each valid reading against the machine's `alert_threshold_rule` set, tracks sustained duration across consecutive readings, and computes rate of change between them |
| **Prediction Agent** | Aggregates valid readings over a lookback window into `prediction_feature_snapshot`. Excludes every non-`valid` row |
| **Supervisor Agent** | Reads recent readings when assembling context, primarily to characterise the trend rather than the instantaneous value |
| **Decision Agent** | **Cites specific readings as supporting evidence.** The §16.5 contract element "supporting evidence" resolves to rows in this table |
| **Notification Service** | Includes the triggering reading and its unit in message bodies so a recipient can verify the claim on the machine itself |
| **Dashboard** | Renders parameter charts with threshold bands from master data overlaid |
| **Analytics** | Trend analysis, sensor reliability reporting, and the downsampled hourly aggregates that survive raw-row purge |

---

### E2. `machine_operational_status`

**Purpose**

Holds the current operational state of each machine in exactly one row, together with the accumulated counters that maintenance scheduling depends on. It answers *"what is happening right now?"* without scanning history.

**Business description**

Eight machines, eight rows, overwritten in place. This is the only entity in the model whose row count never grows.

It exists because the alternative is unacceptable. Deriving current state from `machine_state_transition` means finding the latest transition per machine on every evaluation cycle, and the Monitoring Agent runs that query continuously. A single indexed row per machine turns the most frequent read in the platform into a trivial lookup.

The **accumulated counters** are the more consequential part. The master data model deliberately excluded `next_due_date` from `machine_maintenance_schedule` (§2.3, §26) on the grounds that maintenance due status must be computed from schedule definition plus operational history. This entity is where that computation gets its operational input:

| Schedule `interval_basis` | Computed against |
|---|---|
| `operating_hours` | `accumulated_operating_hours` minus `operating_hours_at_last_maintenance` |
| `cycle_count` | `accumulated_cycle_count` minus `cycle_count_at_last_maintenance` |
| `calendar_days` | `baseline_start_date` and the maintenance history directly |

Storing the value **at last maintenance** rather than a "since last maintenance" counter is deliberate. A counter that resets is a second mutable number that can drift; two absolute readings and a subtraction cannot. The counters themselves only ever increase.

**On the counters being derivable.** `accumulated_cycle_count` is the count of `cycle_history` rows for the machine, and `accumulated_operating_hours` is derivable by summing `running` durations from `machine_state_transition`. Both are nonetheless stored as **maintained running totals**, because summing millions of rows on every maintenance-due check is not viable and because a machine hour meter is the standard MES pattern. §9.3 states the reconciliation rule that keeps them honest.

**Note on `offline` versus master lifecycle status.** `machine.lifecycle_status` is master data and records whether the asset is part of the working factory — `in_service`, `standby`, `under_overhaul`, `decommissioned`. This entity's `offline` state is the operational reflection of an asset that is not `in_service`. The two are linked by a business rule, not by duplication: the master row is authoritative about the asset, and this row is authoritative about the moment.

**Primary key**

`machine_operational_status_id` — surrogate `INTEGER`, with a **unique constraint on `machine_id`** enforcing one row per machine.

Using `machine_id` as the primary key directly was considered and is a legitimate pattern for a strict one-to-one. A surrogate was chosen for consistency with every other entity in both documents, with the unique constraint carrying the real rule.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | **Unique**; references `machine` | The machine. One row per machine, for life |
| `current_state` | TEXT + CHECK | Yes | `running` | One of: `running`, `idle`, `setup`, `starved`, `blocked`, `down_unplanned`, `down_planned`, `offline` | **What the machine is doing now.** `starved` and `blocked` are distinguished because they mean opposite things about where the constraint is |
| `state_since` | DATETIME | Yes | `2026-07-29 14:12:30+05:30` | Not in the future | When the current state began. Current duration is `now` minus this |
| `current_production_run_id` | INTEGER (FK op) | No | `RUN-2026-0714` | References `production_run` with status `running` or `setup` when present | Active run. NULL when idle, down, or offline |
| `current_shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing the present moment | Crew currently on duty |
| `accumulated_operating_hours` | NUMERIC(12,2) | Yes | `11482.50` | Monotonically non-decreasing; ≤ `machine_type.design_life_hours` | **Machine hour meter.** Total time in `running` state since commissioning. Drives operating-hour maintenance intervals and gives the Prediction Agent a wear position |
| `accumulated_cycle_count` | INTEGER | Yes | `486220` | Monotonically non-decreasing | Total cycles produced since commissioning. Drives cycle-count maintenance intervals |
| `operating_hours_at_last_maintenance` | NUMERIC(12,2) | No | `11020.00` | ≤ `accumulated_operating_hours` when present | Hour meter reading at the last completed maintenance. NULL before first service. **The anchor for operating-hour due calculation** |
| `cycle_count_at_last_maintenance` | INTEGER | No | `462180` | ≤ `accumulated_cycle_count` when present | Cycle count at last maintenance. NULL before first service |
| `last_reading_at` | DATETIME | No | `2026-07-29 18:42:10+05:30` | Not in the future | Most recent telemetry received. NULL for unmonitored machines. **Staleness detection** — a monitored machine silent for several sampling intervals has a data pipeline problem, not a machine problem |
| `last_state_transition_id` | INTEGER (FK op) | No | `881204` | References `machine_state_transition` | The transition that produced the current state. NULL only before the first transition. Direct traceability from current state to its cause |
| `open_alert_count` | INTEGER | Yes | `1` | ≥ 0; default 0 | Count of `operational_alert` rows currently open for this machine. **Maintained for dashboard performance** — reconcilable against the alert table |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine` | Master | **One-to-one** | Unique constraint on `machine_id` |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `production_run` | Operational | Many-to-one, optional | Active run |
| Parent | `machine_state_transition` | Operational | Many-to-one, optional | The transition that set the current state |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, once per machine at initialisation |
| **Updated by** | Factory Simulator only. Every state change, every reading, every completed cycle |
| **Read by** | Monitoring Agent (state gating), Prediction Agent (wear context), Supervisor Agent (maintenance due computation), Decision Agent (current condition), Dashboard (live view), Analytics |
| **Archived by** | Never archived. The row count is fixed at the number of machines |
| **Immutable** | No. **Mutable current-state**, overwritten in place |
| **Append-only** | No — the only Group A entity that is not |
| **Expires** | No |
| **Regenerable** | **Yes**, fully. State and counters can be rebuilt by replaying `machine_state_transition` and `cycle_history`. This is what makes the mutability safe: the row is a performance convenience, never a source of truth |

**Business rules**

1. Exactly one row per machine, including unmonitored and decommissioned ones. `MC-0103` has a row despite emitting no telemetry, because it occupies a station and its state affects the line.
2. Machines whose `machine.lifecycle_status` is not `in_service` must have `current_state = 'offline'`. The master row is authoritative about the asset; this row reflects it.
3. `current_production_run_id` must be NULL unless `current_state` is `running`, `setup`, `starved`, or `blocked`. A machine that is down or idle is not on a run.
4. `current_state = 'running'` requires a non-NULL `current_production_run_id`. Running without a run is a contradiction.
5. `accumulated_operating_hours` and `accumulated_cycle_count` **only ever increase.** A decrease indicates a defect or an unauthorised write.
6. Every change to `current_state` must be accompanied by a `machine_state_transition` row in the same unit of work, and `last_state_transition_id` must point to it. A state change with no transition record breaks the history pairing.
7. `state_since` must equal the `transition_at` of the referenced transition.
8. `operating_hours_at_last_maintenance` and `cycle_count_at_last_maintenance` are updated only when a `maintenance_work_record` reaches `closed`. They are never set by any other path.
9. `last_reading_at` older than three sampling intervals for a monitored, non-offline machine indicates a telemetry pipeline fault. This surfaces on `system_health_status`, not as a machine event — an important distinction, because treating a data outage as a machine problem produces meaningless alerts.
10. `open_alert_count` and the accumulated counters are **maintained totals**, reconciled against their sources on a schedule. Reconciliation failure is a data quality incident.

**Example records**

Factory state at 18:42:15 on 29 July 2026, mid-incident.

| machine | current_state | state_since | run | shift | acc_operating_hours | acc_cycles | hours_at_last_maint | open_alerts |
|---|---|---|---|---|---|---|---|---|
| `MC-0101` | `running` | 07-29 14:12:30 | `RUN-2026-0714` | `SH-B` | 11482.50 | 486220 | 11020.00 | 1 |
| `MC-0102` | `running` | 07-29 14:14:05 | `RUN-2026-0714` | `SH-B` | 10945.25 | 502140 | 10610.00 | 0 |
| `MC-0103` | `running` | 07-29 14:20:00 | `RUN-2026-0714` | `SH-B` | 8210.00 | 121505 | 7980.00 | 0 |
| `MC-0201` | `running` | 07-29 14:08:00 | `RUN-2026-0716` | `SH-B` | 9877.75 | 96340 | 9640.00 | 0 |
| `MC-0202` | `starved` | 07-29 18:39:40 | `RUN-2026-0716` | `SH-B` | 14203.00 | 96280 | 13950.00 | 0 |
| `MC-0301` | `running` | 07-29 14:05:15 | `RUN-2026-0715` | `SH-B` | 7654.50 | 288910 | 7420.00 | 1 |
| `MC-0302` | `idle` | 07-29 17:55:00 | NULL | `SH-B` | 6120.25 | 175330 | 5890.00 | 0 |
| `MC-0401` | `running` | 07-29 14:02:00 | `RUN-2026-0717` | `SH-B` | 15880.00 | 1420600 | 15700.00 | 0 |

Several facts fall directly out of this table, and each one feeds a later stage of the pipeline:

- **`MC-0101` is 462.5 hours into a 500-hour interval.** 11482.50 minus 11020.00. Schedule `SCH-0001` is a 500-operating-hour preventive service, so the machine is roughly 37 running hours — about two days — from a service that is already planned and that stops the line. **This single derived number is why the Decision Agent recommends combining the bearing replacement with the due service rather than treating them as two separate stoppages.** It is the clearest illustration in this document of why the master model refused to cache a due date and computed it instead.
- **`MC-0202` is `starved`, not `blocked`.** The conveyor sits at position 2 on `LN-02`, downstream of the weld cell `MC-0201`. Starvation means it is waiting for input, which points upstream. Had it been `blocked`, the constraint would be downstream. Collapsing the two into a single `waiting` state would destroy that directionality, and with it the Supervisor Agent's ability to reason about cascade direction.
- **`MC-0302` is `idle` with a NULL run.** The valve body mill has no work. `LN-03` is running `RUN-2026-0715` on `MC-0301` only. Idle with no run is consistent; running with no run would violate rule 4.
- **Two machines carry open alerts.** `MC-0101` from the vibration excursion, `MC-0301` from the sensor fault seen in §1. Both are visible on the dashboard from one eight-row query.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates and updates every row.** Sole writer. Advances state, increments counters, updates `last_reading_at` |
| **Monitoring Agent** | Gates evaluation by state. Suppresses low-output events when a machine is legitimately `setup`, `starved`, or `down_planned` — a large source of false positives eliminated by one lookup |
| **Prediction Agent** | Uses `accumulated_operating_hours` against `machine_type.design_life_hours` and `mtbf_hours` as wear-position features |
| **Supervisor Agent** | **Computes maintenance due status** from the counters and `machine_maintenance_schedule`. Reads current state to establish whether the machine is even producing |
| **Decision Agent** | States current condition and how far the machine is from its next planned service — which is what makes "combine these two jobs" a viable recommendation |
| **Notification Service** | Includes current state so a recipient knows whether the machine is still running |
| **Dashboard** | **Primary live view.** One row per machine gives the whole factory state in a single query |
| **Analytics** | Reconciliation source for counter integrity; state distribution for OEE availability |

---

### E3. `machine_state_transition`

**Purpose**

Records every change of machine state as an immutable fact, with the duration of the state being left and the reason for leaving it. It is the history behind `machine_operational_status` and the foundation of availability analysis.

**Business description**

Every time a machine changes state, one row is written. Over a shift a busy machine produces a few dozen; the entity is moderate volume and permanently valuable.

Its central attribute is **`duration_in_previous_state_seconds`**. Storing the duration of the state being *left*, at the moment of leaving it, means every completed state interval is one row with a known length. The alternative — deriving durations by comparing consecutive transitions — requires a window function over the whole history every time anybody asks how long a machine was down.

That single attribute is what makes availability analysis a simple aggregation:

| Question | Answered by |
|---|---|
| Availability this shift | Sum `running` durations ÷ scheduled time |
| Total unplanned downtime | Sum durations where the state left was `down_unplanned` |
| Mean time to restore | Average `down_unplanned` duration |
| Starvation caused by upstream | Sum `starved` durations, grouped by `reason_code` |

**`reason_code`** is why the transition happened, and it is what turns a state log into a diagnostic record. Knowing a machine went from `running` to `down_unplanned` is useful; knowing it did so with `reason_code = 'breakdown'` rather than `'quality_hold'` is actionable.

**The triggering references** — `triggering_event_id` and `triggering_work_record_id` — close the causal loop. When a machine goes down because a detected condition led to a maintenance job, the transition points at both. That is what lets Analytics answer the question the whole platform exists to improve: *did acting on the recommendation actually prevent or shorten downtime?*

**Note on the relationship to `machine_operational_status`.** These two entities are written together in one unit of work. The transition is the immutable record; the status row is the mutable current position. Neither duplicates the other: one is an event, the other is a position. The status row is rebuildable from these transitions, which §4.3 identifies as what makes its mutability acceptable.

**Primary key**

`machine_state_transition_id` — surrogate `INTEGER`.

No business code. `INTEGER` because `machine_operational_status.last_state_transition_id` references it, and a composite key would propagate awkwardly into a row that is read on every monitoring cycle.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine` | The machine that changed state |
| `from_state` | TEXT + CHECK | No | `running` | One of the eight states | State being left. **NULL only for the first transition** after commissioning, where there is no prior state |
| `to_state` | TEXT + CHECK | Yes | `down_planned` | One of the eight states; must differ from `from_state` | State being entered |
| `transition_at` | DATETIME | Yes | `2026-07-30 06:05:00+05:30` | Not in the future; > the previous transition for this machine | **Event time** of the change. The boundary of both the closing and opening intervals |
| `duration_in_previous_state_seconds` | INTEGER | No | `57150` | ≥ 0; NULL only when `from_state` is NULL | **How long the previous state lasted.** Turns availability analysis into a sum instead of a window function |
| `reason_code` | TEXT + CHECK | Yes | `planned_maintenance` | One of: `run_start`, `run_complete`, `changeover`, `tool_change`, `upstream_starvation`, `downstream_blockage`, `breakdown`, `planned_maintenance`, `quality_hold`, `operator_unavailable`, `shift_end`, `restored`, `asset_status_change` | **Why the change happened.** Turns a state log into a diagnostic record |
| `shift_id` | INTEGER (FK master) | Yes | `SH-A` | References `shift` containing `transition_at` | Crew on duty at the transition |
| `production_run_id` | INTEGER (FK op) | No | `RUN-2026-0714` | References `production_run` when present | Run affected. NULL when no run was active |
| `triggering_event_id` | INTEGER (FK op) | No | `EVT-20260729-0412` | References `operational_event` when present | Detected condition that led to this transition. NULL for routine changes. **Closes the causal loop from detection to downtime** |
| `triggering_work_record_id` | INTEGER (FK op) | No | `WO-2026-0341` | References `maintenance_work_record` when present | Maintenance job that caused this transition. NULL when not maintenance-related |
| `notes` | TEXT | No | `Bearing replacement per REC-20260729-0031, combined with SCH-0001 service` | — | Free-text context. NULL for routine transitions. Populated where a human decision drove the change |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine` | Master | Many-to-one | The machine |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `production_run` | Operational | Many-to-one, optional | Run affected |
| Parent | `operational_event` | Operational | Many-to-one, optional | Triggering detection |
| Parent | `maintenance_work_record` | Operational | Many-to-one, optional | Triggering maintenance job |
| Referenced by | `machine_operational_status` | Operational | One-to-one for the latest | The current state points at its causing transition |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, on every state change, in the same unit of work as the `machine_operational_status` update |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Monitoring Agent (state context), Prediction Agent (downtime and cycling features), Supervisor Agent (recent history), Decision Agent (trend narrative), Dashboard (state timelines), Analytics (availability and OEE) |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A state change happened at a moment; it cannot be revised |
| **Append-only** | Yes |
| **Expires** | Archived after 2 years, retained in archive indefinitely. This is the availability record and the basis of all downtime reporting |
| **Regenerable** | No. Source of truth for machine availability |

**Business rules**

1. `to_state` must differ from `from_state`. A transition to the same state is not a transition.
2. `from_state` is NULL only for a machine's very first transition.
3. `duration_in_previous_state_seconds` must equal the interval between this transition and the previous one for the same machine. Reconciled as a data quality check.
4. Every transition must be accompanied by an update to `machine_operational_status` in the same unit of work. Partial application would leave the current state disagreeing with its own history.
5. `reason_code` must be consistent with the state pair. `breakdown` implies `to_state = 'down_unplanned'`; `changeover` implies `to_state = 'setup'`; `upstream_starvation` implies `to_state = 'starved'`.
6. Transitions to `offline` carry `reason_code = 'asset_status_change'` and reflect a change to `machine.lifecycle_status`.
7. `triggering_event_id` should be populated whenever a transition to `down_unplanned` or `down_planned` followed a detected condition. This is the link that makes prevention measurable, and leaving it NULL when a cause exists is a lost opportunity rather than a defect.
8. Transitions are never deleted or edited. A mis-recorded transition is corrected by a compensating transition, so the error and its correction are both visible.
9. Transition timestamps are strictly increasing per machine. Two transitions at the same instant for one machine indicate a simulator defect.

**Example records**

The state history of `MC-0101` across the incident.

| machine | from_state | to_state | transition_at | duration_prev_sec | reason_code | shift | run | triggering_event | triggering_wo |
|---|---|---|---|---|---|---|---|---|---|
| `MC-0101` | `idle` | `setup` | 07-29 14:05:10 | 2110 | `changeover` | `SH-B` | `RUN-2026-0714` | NULL | NULL |
| `MC-0101` | `setup` | `running` | 07-29 14:12:30 | 440 | `run_start` | `SH-B` | `RUN-2026-0714` | NULL | NULL |
| `MC-0101` | `running` | `setup` | 07-29 20:48:00 | 23730 | `tool_change` | `SH-B` | `RUN-2026-0714` | NULL | NULL |
| `MC-0101` | `setup` | `running` | 07-29 20:56:20 | 500 | `run_start` | `SH-B` | `RUN-2026-0714` | NULL | NULL |
| `MC-0101` | `running` | `down_planned` | 07-30 06:05:00 | 33520 | `planned_maintenance` | `SH-A` | `RUN-2026-0714` | `EVT-20260729-0412` | `WO-2026-0341` |
| `MC-0101` | `down_planned` | `setup` | 07-30 10:35:00 | 16200 | `restored` | `SH-A` | `RUN-2026-0714` | NULL | `WO-2026-0341` |
| `MC-0101` | `setup` | `running` | 07-30 10:47:30 | 750 | `run_start` | `SH-A` | `RUN-2026-0714` | NULL | NULL |

The whole incident is legible from these seven rows:

- **The fifth row is the outcome the platform exists to produce.** `MC-0101` went down `planned`, not `unplanned`, and both `triggering_event_id` and `triggering_work_record_id` are populated. The chain runs backward from a planned stoppage to the work order, to the recommendation, to the alert, to the event, to the vibration readings in §1. A failure that was detected, reasoned about, decided on, and scheduled — rather than one that stopped the line without warning.
- **Downtime was 16,200 seconds, 4.5 hours.** The sixth row's duration. `machine_type_failure_mode` estimates 240 minutes for `FC-BRG` on a `MTY-VMC-500`; adding the 15-minute part retrieval from `LOC-SP-B2` gives 255 minutes, and the actual was 270. Close enough to make the original estimate credible, and the gap is exactly what Analytics compares.
- **The tool change at 20:48 is unrelated and routine.** `PRM-TWEAR` had reached 62.4 % at 18:42 and continued climbing; the change happened before the 88 % warning limit. This is what a normal maintenance interaction looks like next to an incident, and having both in one series is what lets Analytics distinguish them.
- **Duration arithmetic is self-checking.** 14:12:30 to 20:48:00 is 23,730 seconds, matching the third row. Any mismatch is a detectable defect rather than a silent one.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Sole writer, paired with the status update |
| **Monitoring Agent** | Reads recent transitions to establish whether a condition coincided with a state change — a vibration rise that began at a tool change has a different explanation from one that began mid-run |
| **Prediction Agent** | Derives features: downtime frequency, state-cycling rate, time since last stoppage. Frequent short stoppages are themselves a degradation signal |
| **Supervisor Agent** | Assembles recent state history into decision context, and computes `running` time toward operating-hour maintenance intervals |
| **Decision Agent** | Builds the trend narrative — *"running continuously since the 14:12 changeover"* — which is what anchors evidence in a frame the manager recognises |
| **Notification Service** | Reports how long a machine has been in its current state |
| **Dashboard** | Renders state timeline bars per machine and per shift |
| **Analytics** | **Primary source for availability and OEE.** Downtime Pareto by `reason_code`; and via `triggering_event_id`, whether acting on recommendations measurably reduced unplanned downtime |

---

## Group B — Production Execution

What is being made, how fast, and to what quality. This group converts a technical machine condition into a business consequence: without it, a vibration excursion is an engineering curiosity rather than a threat to a customer commitment.

The group holds production data at **four deliberately different grains**, and the distinction between them is the design's central point:

| Entity | Grain | Class | Question it answers |
|---|---|---|---|
| `production_run` | One order execution | Lifecycle | What are we making, for whom, by when? |
| `production_progress` | Run, periodic | Snapshot | How far along is it, and will it be late? |
| `production_count` | Machine × interval | Aggregated | How is each station performing? |
| `cycle_history` | Individual cycle | Append-only | How long did each part take, and did that change? |

Each grain has a distinct consumer and none is derivable from a coarser one. `production_count` **is** derivable from `cycle_history` and is declared as such — it exists so that dashboards and OEE calculations do not scan millions of rows.

---

### E4. `production_run`

**Purpose**

Represents one execution of a product order on a production line: what is being made, in what quantity, for which customer, by when, and how the execution is progressing through its lifecycle.

**Business description**

A production run is the operational transaction at the centre of the business. Master data records that `LN-01` *can* produce `PRD-GH-100` at 145 seconds per unit; a run records that it *is* producing 480 of them for Apex Drivetrain Systems, due on 3 August.

This entity is what makes business impact assessment possible. When the Prediction Agent flags `MC-0101`, the Decision Agent's business impact statement is assembled almost entirely from the active run: the product gives margin, the customer gives tier and penalty exposure, the due date gives urgency, and the line capability gives the rate at which output is being lost.

**`customer_id` is mandatory.** The frozen master data model (§22, rule 1) states that every operational production order references exactly one customer and that make-to-stock production is not modelled. That decision is honoured here: there is no nullable customer and no separate stock-replenishment path. The platform's business impact reasoning depends on there being an identifiable customer behind every unit at risk.

**`product_line_capability_id` pins the rate.** Rather than referencing product and line separately and re-deriving which capability row applies, the run references the specific capability row governing it. That row carries the authoritative `cycle_time_seconds` and `max_hourly_output_units`, and because master data soft-retires capability rows rather than editing them when rates change, the pinned reference remains valid for the run's whole life. This is why no cycle time is copied into operational data anywhere.

**Note on absent cumulative quantities.** There is deliberately no `quantity_good` or `quantity_scrapped` on this entity. Cumulative progress lives on `production_progress`, and the final figures for a completed run are read from its terminal snapshot.

This differs from the treatment of `machine_operational_status`, which *does* carry maintained running totals, and the difference is worth being explicit about. Machine counters are read on **every monitoring cycle** and span the machine's entire commissioned life across millions of rows — deriving them repeatedly is not viable. Run quantities are read **occasionally** and span a few hundred progress snapshots. Same principle, different cost profile, different answer. A maintained total is justified by read frequency and derivation cost, not adopted as a habit.

**Primary key**

`production_run_id` — surrogate `INTEGER`, with `production_run_code` unique.

A code is warranted: planners, operators, and supervisors refer to run numbers constantly, and the code appears in recommendations and notifications. The `RUN-<yyyy>-<nnnn>` format restarts numbering annually, which is standard practice and keeps codes short enough to speak aloud.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `production_run_code` | VARCHAR(16) | Yes | `RUN-2026-0714` | Unique; matches `^RUN-[0-9]{4}-[0-9]{4}$` | Run number used on the shop floor and cited in recommendations |
| `product_id` | INTEGER (FK master) | Yes | `PRD-GH-100` | References active `product` | What is being made. Margin and quality criticality come from master data |
| `production_line_id` | INTEGER (FK master) | Yes | `LN-01` | References active `production_line` | Where it is being made. Criticality and capacity come from master data |
| `product_line_capability_id` | INTEGER (FK master) | Yes | `PRD-GH-100` on `LN-01` | References active `product_line_capability` matching the product and line above | **Pins the governing rate.** Supplies `cycle_time_seconds` and `max_hourly_output_units` without copying them |
| `customer_id` | INTEGER (FK master) | Yes | `CUS-001` | References active `customer` | Whose order this is. **Mandatory** — tier, penalty, and on-time target drive impact assessment |
| `planned_quantity_units` | NUMERIC(12,2) | Yes | `480.00` | > 0 | Order quantity. The denominator for percent complete |
| `planned_start_at` | DATETIME | Yes | `2026-07-29 14:00:00+05:30` | — | Scheduled start |
| `planned_end_at` | DATETIME | Yes | `2026-07-30 10:00:00+05:30` | > `planned_start_at` | Scheduled finish. **The baseline schedule variance is measured against** |
| `actual_start_at` | DATETIME | No | `2026-07-29 14:12:30+05:30` | ≥ `planned_start_at` − tolerance | When production actually began. NULL while `planned` |
| `actual_end_at` | DATETIME | No | `2026-07-30 15:20:00+05:30` | > `actual_start_at` when present | When it finished. NULL until `completed` or `cancelled` |
| `due_date` | DATE | Yes | `2026-08-03` | ≥ `planned_end_at` date | **Customer commitment date.** With `customer.late_delivery_penalty_per_day`, converts a delay into a contractual cost |
| `priority` | TEXT + CHECK | Yes | `high` | One of: `normal`, `high`, `urgent` | Scheduling priority. Combines with customer tier when ranking competing disruptions |
| `run_status` | TEXT + CHECK | Yes | `running` | One of: `planned`, `setup`, `running`, `paused`, `completed`, `cancelled` | **Lifecycle position.** Only one run per line may be `setup`, `running`, or `paused` at a time |
| `pause_reason` | TEXT + CHECK | No | NULL | One of: `machine_down`, `material_shortage`, `quality_hold`, `operator_unavailable`, `shift_end`, `higher_priority_run` | Why a paused run stopped. NULL unless `paused`. **Distinguishes a machine problem from a material problem** without inspecting other entities |
| `cancellation_reason` | TEXT | No | NULL | Non-empty when `run_status = 'cancelled'` | Why the run was abandoned. NULL otherwise |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `product` | Master | Many-to-one | What is made |
| Parent | `production_line` | Master | Many-to-one | Where |
| Parent | `product_line_capability` | Master | Many-to-one | Governing rate |
| Parent | `customer` | Master | Many-to-one | Whose order |
| Child | `production_progress` | Operational | One-to-many | Periodic progress snapshots |
| Child | `production_count` | Operational | One-to-many | Per-machine interval counts |
| Child | `cycle_history` | Operational | One-to-many | Individual cycles |
| Child | `quality_inspection_result` | Operational | One-to-many | Inspections against this run |
| Child | `scrap_record` | Operational | One-to-many | Scrap attributed to this run |
| Referenced by | `machine_sensor_reading`, `machine_state_transition`, `machine_operational_status`, `inventory_movement` | Operational | One-to-many | The run active at the time |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, when a run is scheduled onto a line |
| **Updated by** | Factory Simulator only. Advances `run_status` and sets actual timestamps |
| **Read by** | All eight components |
| **Archived by** | Platform retention job |
| **Immutable** | No. **Lifecycle entity** — status advances through defined states |
| **Append-only** | No |
| **Expires** | Archived 2 years after `actual_end_at`, retained in archive indefinitely. This is the production record |
| **Regenerable** | No. Records a scheduling and execution decision that exists nowhere else |

**State machine**

```
planned ──► setup ──► running ──► completed
                 │        │  ▲
                 │        ▼  │
                 │      paused
                 │        │
                 └────────┴──► cancelled
```

| Transition | Trigger |
|---|---|
| `planned` → `setup` | Changeover begins on the line |
| `setup` → `running` | First cycle completes; `actual_start_at` is set |
| `running` → `paused` | Machine down, material shortage, quality hold, or shift end; `pause_reason` is set |
| `paused` → `running` | Cause cleared; `pause_reason` is cleared |
| `running` → `completed` | Planned quantity reached; `actual_end_at` is set |
| any → `cancelled` | Order withdrawn; `cancellation_reason` is required |

**Business rules**

1. **At most one run per line** in `setup`, `running`, or `paused` at any moment. A line produces one product at a time.
2. The referenced `product_line_capability` must match the run's product and line, and must have `capability_type = 'production_route'`. A run cannot be scheduled onto a finishing stage.
3. `customer_id` is mandatory. No make-to-stock path exists, consistent with the frozen master model.
4. `actual_start_at` is set exactly once, on the transition to `running`, and never revised.
5. `pause_reason` is non-NULL if and only if `run_status = 'paused'`.
6. `cancellation_reason` is required when `run_status = 'cancelled'`.
7. A run may not be `completed` while its terminal `production_progress` shows cumulative good quantity below `planned_quantity_units`, unless short-closed — in which case the shortfall is recorded in `cancellation_reason`.
8. `due_date` must be on or after `planned_end_at`. A commitment earlier than the plan is a planning error the platform should surface, not absorb.
9. Cumulative quantities are **not** stored here. They are read from the latest `production_progress` snapshot.
10. When a run pauses because a machine went down, the corresponding `machine_state_transition` carries the causal references. The run records *that* it paused and why in general terms; the machine history records the specific cause.

**Example records**

Runs active during the incident.

| code | product | line | customer | planned_qty | planned_start | planned_end | actual_start | actual_end | due_date | priority | status |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `RUN-2026-0714` | `PRD-GH-100` | `LN-01` | `CUS-001` | 480.00 | 07-29 14:00 | 07-30 10:00 | 07-29 14:12:30 | 07-30 15:20 | 2026-08-03 | `high` | `completed` |
| `RUN-2026-0715` | `PRD-VB-075` | `LN-03` | `CUS-003` | 900.00 | 07-29 14:00 | 07-30 15:00 | 07-29 14:05:15 | NULL | 2026-08-10 | `normal` | `running` |
| `RUN-2026-0716` | `PRD-PMP-220` | `LN-02` | `CUS-002` | 96.00 | 07-29 14:00 | 07-30 05:00 | 07-29 14:08:00 | 07-30 06:40 | 2026-08-06 | `normal` | `completed` |
| `RUN-2026-0717` | `PRD-GH-100` | `LN-04` | `CUS-001` | 480.00 | 07-29 14:00 | 07-29 18:00 | 07-29 14:02:00 | 07-29 18:12 | 2026-08-03 | `high` | `completed` |

The set is internally consistent with master data and shows the incident's cost:

- **`RUN-2026-0714` finished 5 hours 20 minutes late.** Planned end 10:00, actual 15:20. The 4.5-hour planned stoppage plus setup accounts for it almost exactly. The run still met its 3 August due date, which is precisely the outcome the platform is built to produce: the delay was absorbed by schedule float because the failure was planned rather than sudden.
- **The plan arithmetic checks out.** 480 units at the capability's 24 units per hour is 20 hours; 14:00 to 10:00 next day is 20 hours. `RUN-2026-0716` is 96 pump assemblies at 11 units per hour, roughly 8.7 hours plus changeover against a 15-hour window — comfortable, as a `normal` priority run should be.
- **`RUN-2026-0717` is the packaging stage** for the same product and customer as `RUN-2026-0714`, running on `LN-04` at 120 units per hour. It is a separate run against the same commitment, which is how the model handles a finishing stage without introducing routing sequences — consistent with the `capability_type` decision in master data §7.
- **`CUS-001` is Gold tier with a 12,000 per day penalty and a 98 % on-time commitment.** Two of the four runs serve that customer, and both are `high` priority. That is the business weighting the Supervisor Agent applies when deciding whether a 0.68 probability on `LN-01` is worth escalating.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates and updates every row.** Schedules runs, advances status, sets actual timestamps |
| **Monitoring Agent** | Establishes what should be producing, so a low-output condition can be distinguished from a legitimately idle line |
| **Prediction Agent** | Uses the active run for context; production load is a wear factor |
| **Supervisor Agent** | **Core business context.** Resolves affected product, customer, quantity, and due date when assembling a context package |
| **Decision Agent** | **The business impact element** of the §16.5 contract resolves largely to this entity plus its master references — which order, which customer, what tier, what penalty, how late |
| **Notification Service** | Names the affected run and customer so urgency is legible to the recipient |
| **Dashboard** | Shows what each line is producing, against plan |
| **Analytics** | Schedule adherence, on-time delivery by customer, disruption impact by product and tier |

---

### E5. `production_progress`

**Purpose**

Captures the cumulative state of a production run at regular intervals: how much has been made, at what rate, and whether the run is on schedule. It is the time series of run performance.

**Business description**

A progress snapshot is written on a fixed interval for every active run — 15 minutes in this design, giving 96 rows per run per day.

It exists because a run header can only hold *current* progress, and current progress destroys history. Two questions the platform genuinely needs to answer require a time series:

**"Was this run behind schedule when the vibration excursion started?"** If the run was already 90 minutes behind at 18:42, a 4.5-hour stoppage has a very different consequence from the same stoppage on a run that was ahead. The Decision Agent needs the position at the moment of the incident, not the position now.

**"Did output degrade before the machine failed?"** Falling rate with unchanged state is one of the clearest leading indicators of mechanical degradation, and it is completely invisible in a single mutable current-progress figure. A rate that drifts from 24 to 22.5 units per hour over four hours is a signal that arrives independently of any sensor threshold.

**`projected_completion_at` and `is_behind_schedule`** are computed at snapshot time and stored. A snapshot's entire purpose is to freeze a computed state, so storing derived values here is correct rather than duplicative — recomputing them for an arbitrary past moment would mean replaying every underlying cycle.

**`downtime_seconds_cumulative`** separates "not producing" from "producing slowly," which is the availability-versus-performance split that OEE depends on. Without it, a run that lost two hours to a breakdown looks identical to one that ran slowly throughout.

**Primary key**

`production_progress_id` — surrogate `INTEGER`. No business code: nobody names a snapshot.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `production_run_id` | INTEGER (FK op) | Yes | `RUN-2026-0714` | References `production_run` | The run being measured |
| `snapshot_at` | DATETIME | Yes | `2026-07-29 18:45:00+05:30` | Not in the future; aligned to the snapshot interval | **Event time** of the capture |
| `quantity_good_cumulative` | NUMERIC(12,2) | Yes | `102.00` | ≥ 0; non-decreasing across snapshots | Good units produced so far |
| `quantity_scrapped_cumulative` | NUMERIC(12,2) | Yes | `3.00` | ≥ 0; non-decreasing | Units scrapped so far |
| `quantity_rework_cumulative` | NUMERIC(12,2) | Yes | `1.00` | ≥ 0; non-decreasing | Units sent for rework so far |
| `percent_complete` | NUMERIC(5,2) | Yes | `21.25` | 0–100 | Good quantity as a share of planned. The headline progress figure |
| `current_rate_units_per_hour` | NUMERIC(10,2) | Yes | `22.50` | ≥ 0 | Achieved rate over the recent window. **Compared against the capability's `max_hourly_output_units` to detect underperformance** |
| `elapsed_production_seconds` | INTEGER | Yes | `16350` | ≥ 0 | Time in `running` state since `actual_start_at` |
| `downtime_seconds_cumulative` | INTEGER | Yes | `0` | ≥ 0; non-decreasing | Time lost to non-producing states. **Separates availability loss from performance loss** |
| `projected_completion_at` | DATETIME | No | `2026-07-30 11:33:00+05:30` | > `snapshot_at` when present | Forecast finish at the current rate. NULL when the rate is zero and no forecast is meaningful |
| `schedule_variance_minutes` | INTEGER | Yes | `93` | Positive is late, negative is early | Projected completion against `planned_end_at`. **The number that makes lateness concrete** |
| `is_behind_schedule` | INTEGER | Yes | `1` | — | Whether projected completion exceeds plan beyond tolerance. Cheap dashboard filter |
| `scrap_rate_pct` | NUMERIC(5,2) | Yes | `2.83` | 0–100 | Scrap as a share of total produced. **Compared against `product.target_scrap_rate_pct`** to detect a developing quality problem |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `snapshot_at` | Crew on duty. Enables per-shift performance comparison |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `production_run` | Operational | Many-to-one | The run measured |
| Parent | `shift` | Master | Many-to-one | Crew on duty |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, on a fixed interval for every active run |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Monitoring Agent (underperformance and scrap-rate detection), Supervisor Agent (schedule position), Decision Agent (impact quantification), Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes |
| **Append-only** | Yes |
| **Expires** | Raw snapshots purged after 180 days; the terminal snapshot per run is retained with the run indefinitely, because it holds the final quantities |
| **Regenerable** | **Yes**, from `production_count` and `machine_state_transition`. Recomputable, which is why aggressive purge is safe |

**Business rules**

1. Snapshots are written only for runs in `setup`, `running`, or `paused`.
2. Cumulative quantities are non-decreasing across a run's snapshots. A decrease indicates a defect.
3. `percent_complete` is `quantity_good_cumulative` ÷ `production_run.planned_quantity_units` × 100 and may exceed 100 on overproduction, which is legitimate.
4. `current_rate_units_per_hour` measures a recent window, not the run average, so it reflects present performance rather than being smoothed by history.
5. A `current_rate_units_per_hour` persistently below the capability's `max_hourly_output_units` is an underperformance condition the Monitoring Agent may raise as an event — output-based detection that requires no sensor threshold at all.
6. `scrap_rate_pct` above `product.target_scrap_rate_pct` is a quality condition the Monitoring Agent may raise. For `PRD-GH-100` the target is 1.50 %.
7. `is_behind_schedule` is set when `schedule_variance_minutes` exceeds a tolerance drawn from `business_rule`, not from a hardcoded constant.
8. The **terminal snapshot** — the last one before a run completes — is the authoritative record of final quantities and is exempt from purge.
9. Snapshots are never edited. A correction is a new snapshot.

**Example records**

Progress on `RUN-2026-0714` through the incident.

| snapshot_at | good | scrap | rework | pct_complete | rate/hr | downtime_sec | projected_completion | variance_min | behind | scrap_rate |
|---|---|---|---|---|---|---|---|---|---|---|
| 07-29 16:00 | 40.00 | 1.00 | 0.00 | 8.33 | 23.80 | 0 | 07-30 10:22 | 22 | 0 | 2.44 |
| 07-29 18:30 | 96.00 | 3.00 | 1.00 | 20.00 | 22.90 | 0 | 07-30 11:12 | 72 | 1 | 3.03 |
| 07-29 18:45 | 102.00 | 3.00 | 1.00 | 21.25 | 22.50 | 0 | 07-30 11:33 | 93 | 1 | 2.83 |
| 07-29 21:00 | 148.00 | 4.00 | 1.00 | 30.83 | 22.30 | 500 | 07-30 12:05 | 125 | 1 | 2.63 |
| 07-30 06:15 | 350.00 | 7.00 | 2.00 | 72.92 | 22.10 | 900 | 07-30 15:48 | 348 | 1 | 1.96 |
| 07-30 15:15 | 480.00 | 9.00 | 3.00 | 100.00 | 21.60 | 17100 | 07-30 15:20 | 320 | 1 | 1.84 |

This series carries the incident's independent corroborating evidence:

- **The rate is decaying before any threshold fires.** 23.80 → 22.90 → 22.50 → 22.30 → 22.10 units per hour, against a capability maximum of 24.00. By 18:45 the machine is running 6 % below its rated rate with `PRM-VIB` only just crossing its warning limit. **Output degradation is visible in production data before the sensor threshold breach is confirmed.** This is why the Decision Agent's evidence is stronger than any single parameter: two independent signals, from two different entities, pointing the same way.
- **Downtime and performance loss are separable.** At 21:00 downtime is 500 seconds — the tool change from §E3 — while the rate continues to fall. The 06:15 snapshot on 30 July shows 900 seconds; the final snapshot shows 17,100, which is the 4.5-hour repair. Availability loss and performance loss never get conflated.
- **Schedule variance grows monotonically**, from 22 minutes to 348, giving the Decision Agent a concrete lateness figure at the moment of the decision rather than a vague concern.
- **Scrap rate stays near target.** It peaks at 3.03 % against a 1.50 % target but settles to 1.84 % as volume grows. Elevated early-run scrap is normal after changeover, which is why a rate is compared over a run rather than reacted to instantly.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Sole writer |
| **Monitoring Agent** | Detects underperformance against the capability rate and scrap rate against product target — **detection paths that need no sensor at all** |
| **Prediction Agent** | Rate decay over a window is a degradation feature independent of sensor telemetry |
| **Supervisor Agent** | Establishes the run's schedule position at the moment of the incident |
| **Decision Agent** | **Quantifies impact.** Combines remaining quantity, current rate, and expected downtime into units and margin at risk |
| **Notification Service** | Reports completion percentage and lateness so a recipient understands the stakes |
| **Dashboard** | Progress bars, rate charts, and schedule variance indicators per line |
| **Analytics** | OEE performance component, rate stability, per-shift comparison |

---

### E6. `production_count`

**Purpose**

Pre-aggregated good, scrap, and rework counts per machine per interval. It exists so that performance analysis and dashboard rendering never scan raw cycle data.

**Business description**

One row per machine per interval — 30 minutes in this design. Around 1,600 rows a day across the factory.

This entity is **explicitly derived.** Every value is obtainable by aggregating `cycle_history`. It exists purely because that aggregation, run repeatedly over millions of cycle rows, would dominate the platform's query cost. OEE calculations, dashboard charts, per-shift comparisons, and machine performance rankings all read counts, not cycles.

Declaring it derived rather than pretending it is a source of truth has three practical consequences that matter:

1. It can be **dropped and rebuilt** from `cycle_history` at any time, so a defect in aggregation is a recoverable bug rather than data loss.
2. It can be **reconciled** against its source, and §9.3 makes that a data quality requirement.
3. It can be **purged more aggressively** than the cycle data it summarises — or, once cycles are purged, retained *longer*, because it becomes the surviving record.

**Why machine-level rather than run-level.** `production_progress` already covers the run. This entity covers the machine, because a line's stations perform differently and the difference is diagnostic. On `LN-01`, `MC-0101` is the bottleneck at 26 units per hour rated while `MC-0102` is rated 31. Counting per machine is what reveals that the bottleneck has slowed; counting per run only reveals that the line has.

**Note on the relationship to `cycle_history`.** Cycles carry timing and per-part outcome; counts carry totals. Neither is redundant: the Prediction Agent needs cycle-time deviation per part, and the dashboard needs hourly totals. Same underlying events, two access patterns, and §3.6 explains why that justifies two representations.

**Primary key**

`production_count_id` — surrogate `INTEGER`, with a **composite unique constraint on (`machine_id`, `interval_from`)**.

The composite constraint is what makes rebuilds idempotent: re-aggregating an interval updates one row rather than inserting a duplicate. Without it, a retried aggregation job would double-count.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine`; unique with `interval_from` | The station counted |
| `production_run_id` | INTEGER (FK op) | No | `RUN-2026-0714` | References `production_run` when present | Run active during the interval. NULL if the machine was idle throughout |
| `interval_from` | DATETIME | Yes | `2026-07-29 18:30:00+05:30` | Aligned to the interval boundary; unique with `machine_id` | Start of the counted window |
| `interval_to` | DATETIME | Yes | `2026-07-29 19:00:00+05:30` | = `interval_from` + interval length | End of the window |
| `good_count` | INTEGER | Yes | `11` | ≥ 0 | Units passing without intervention |
| `scrap_count` | INTEGER | Yes | `1` | ≥ 0 | Units scrapped |
| `rework_count` | INTEGER | Yes | `0` | ≥ 0 | Units sent for rework |
| `cycles_completed` | INTEGER | Yes | `12` | ≥ 0; = good + scrap + rework | Total cycles. The equality is a built-in integrity check |
| `total_cycle_time_seconds` | INTEGER | Yes | `1782` | ≥ 0 | Sum of cycle times. With `cycles_completed`, gives mean cycle time without touching `cycle_history` |
| `running_seconds` | INTEGER | Yes | `1800` | 0 to interval length | Time in `running` state during the interval. **The availability input to OEE** |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `interval_from` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine` | Master | Many-to-one | The station |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `production_run` | Operational | Many-to-one, optional | Run active in the interval |
| Derived from | `cycle_history`, `machine_state_transition` | Operational | Aggregation | Source data |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, at the close of each interval |
| **Updated by** | Factory Simulator only, on rebuild. Idempotent by the composite unique constraint |
| **Read by** | Monitoring Agent, Dashboard, Analytics. **Not read by** the Prediction Agent, which needs cycle-level detail |
| **Archived by** | Platform retention job |
| **Immutable** | Effectively yes in normal operation; rebuildable by design |
| **Append-only** | Yes in normal operation |
| **Expires** | Retained 2 years, then archived. **Retained longer than `cycle_history`**, because once cycles are purged this becomes the surviving production record |
| **Regenerable** | **Yes**, fully, while `cycle_history` survives. After cycle purge it becomes non-regenerable and is treated as a source of truth from that point |

**Business rules**

1. One row per machine per interval, enforced by the composite unique constraint.
2. `cycles_completed` must equal `good_count` + `scrap_count` + `rework_count`. A violation is an aggregation defect.
3. `running_seconds` cannot exceed the interval length.
4. Intervals are fixed-width and boundary-aligned, so counts are directly comparable and summable across machines and shifts.
5. An interval where the machine never ran produces a row with zero counts, **not** an absent row. Absence would be ambiguous between "did not produce" and "not yet aggregated."
6. Rows are reconciled against `cycle_history` on a schedule. Divergence is a data quality incident, and the resolution is always to rebuild from cycles.
7. `production_run_id` is NULL only when no run was active for the whole interval. An interval spanning a changeover records the run that was active for the majority of it.
8. This entity is **never** the basis of a prediction feature. The Prediction Agent reads `cycle_history` directly, because aggregation destroys the per-cycle variance that carries the degradation signal.

**Example records**

`LN-01` stations across three intervals spanning the incident.

| machine | run | interval_from | interval_to | good | scrap | rework | cycles | total_cycle_sec | running_sec | shift |
|---|---|---|---|---|---|---|---|---|---|---|
| `MC-0101` | `RUN-2026-0714` | 07-29 18:00 | 18:30 | 12 | 0 | 0 | 12 | 1764 | 1800 | `SH-B` |
| `MC-0102` | `RUN-2026-0714` | 07-29 18:00 | 18:30 | 12 | 0 | 0 | 12 | 1392 | 1800 | `SH-B` |
| `MC-0101` | `RUN-2026-0714` | 07-29 18:30 | 19:00 | 11 | 1 | 0 | 12 | 1782 | 1800 | `SH-B` |
| `MC-0102` | `RUN-2026-0714` | 07-29 18:30 | 19:00 | 12 | 0 | 0 | 12 | 1395 | 1800 | `SH-B` |
| `MC-0101` | `RUN-2026-0714` | 07-30 06:00 | 06:30 | 2 | 0 | 0 | 2 | 298 | 300 | `SH-A` |
| `MC-0102` | `RUN-2026-0714` | 07-30 06:00 | 06:30 | 2 | 0 | 0 | 2 | 290 | 300 | `SH-A` |
| `MC-0101` | `RUN-2026-0714` | 07-30 06:30 | 07:00 | 0 | 0 | 0 | 0 | 0 | 0 | `SH-A` |

Three things are visible:

- **Mean cycle time on `MC-0101` is drifting.** 1764 ÷ 12 = 147.0 seconds in the 18:00 interval; 1782 ÷ 12 = 148.5 in the next. The capability standard is 145.0. A 2.4 % deviation growing to 2.4 %+ is small but directional, and it corroborates the vibration trend from an entirely independent measurement. `MC-0102` holds steady at 116 seconds against its own standard — the degradation is machine-specific, not line-wide.
- **The 06:30 interval is all zeros with `running_seconds = 0`,** not a missing row. `MC-0101` went down at 06:05 for the planned repair. Rule 5 is what makes this unambiguous: an explicit zero says "did not produce," where absence would say nothing.
- **Both `LN-01` stations produce 12 units per interval** despite different rated capacities — 26 versus 31 units per hour. The line runs at the bottleneck's pace, which is exactly what `machine.is_bottleneck` asserts in master data, now confirmed by operational data.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row** by aggregating cycles at interval close |
| **Monitoring Agent** | Detects sustained output shortfall per machine over several intervals — slower and more reliable than reacting to a single cycle |
| **Prediction Agent** | **Does not read this entity.** Aggregation destroys the per-cycle variance that carries the signal. Reads `cycle_history` instead |
| **Supervisor Agent** | Recent per-machine output as context, to establish whether a station has actually slowed |
| **Decision Agent** | Quantifies output already lost during a developing condition |
| **Notification Service** | Does not read this entity |
| **Dashboard** | **Primary chart source.** Hourly and per-shift output without touching cycle data |
| **Analytics** | **OEE foundation.** Availability from `running_seconds`, performance from cycle time against standard, quality from good against total |

---

### E7. `cycle_history`

**Purpose**

Records every individual machine cycle with its timing and outcome. It is the finest grain of production data and the source of the cycle-time deviation signal that detects degradation independently of any sensor.

**Business description**

One row per part per machine. Around 3,500 rows a day — the second-highest volume in the model after raw telemetry.

Its value is concentrated in one attribute: **`deviation_from_standard_pct`**. A machine that is beginning to fail takes longer to complete each cycle, and it does so before any sensor threshold is crossed. Rising spindle friction from a degrading bearing costs a fraction of a second per part, invisible in any single cycle and unmistakable across a hundred.

This matters because it is a **mechanically independent** signal. Vibration comes from an accelerometer. Cycle time comes from the machine's own completion timing. If both drift together, the two agree through entirely separate measurement paths — which is a far stronger basis for a root-cause hypothesis than either alone, and it is why the Prediction Agent reads this entity directly rather than the aggregated counts.

**Where the standard comes from, and why it is not copied.** Deviation is measured against `product_line_capability.cycle_time_seconds`, reached through `production_run.product_line_capability_id`. The run pins a specific capability row, and master data soft-retires capability rows rather than editing them when rates change (§7 of the master document). The referenced row therefore cannot mutate under a completed run, so the deviation stays reproducible without copying the standard into operational data. The two documents interlock here deliberately: the master model's immutability discipline is what lets this model avoid a copy.

**`interrupted`** flags cycles cut short by a stoppage. An interrupted cycle has a meaningless duration and must be excluded from deviation statistics, or a single breakdown would appear as a catastrophic cycle-time excursion and corrupt the trend the entity exists to reveal.

**Primary key**

`cycle_history_id` — surrogate `INTEGER`. No business code.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine` | The machine that ran the cycle |
| `production_run_id` | INTEGER (FK op) | Yes | `RUN-2026-0714` | References `production_run` | The run the cycle belongs to |
| `cycle_number_in_run` | INTEGER | Yes | `106` | > 0; unique per machine per run | Sequence within the run. Makes a cycle identifiable in conversation — "the 106th part" |
| `cycle_started_at` | DATETIME | Yes | `2026-07-29 18:41:22+05:30` | Not in the future | **Event time** of cycle start |
| `cycle_ended_at` | DATETIME | Yes | `2026-07-29 18:43:51+05:30` | > `cycle_started_at` | Event time of completion |
| `cycle_time_seconds` | NUMERIC(8,2) | Yes | `149.40` | > 0; = the interval between start and end | Actual duration. The measured quantity |
| `deviation_from_standard_pct` | NUMERIC(6,2) | No | `3.03` | Positive is slower than standard | Deviation against the run's pinned capability rate. NULL when `interrupted`. **The degradation signal** |
| `outcome` | TEXT + CHECK | Yes | `good` | One of: `good`, `scrap`, `rework` | Result for this part. Aggregates into `production_count` |
| `interrupted` | INTEGER | Yes | `0` | Default 0 | Whether the cycle was cut short. **`1` excludes the row from deviation statistics** |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `cycle_started_at` | Crew on duty |
| `sequence_number` | INTEGER | Yes | `486218` | Monotonically increasing per machine | Absolute ordering per machine across all runs. Makes replay deterministic and feeds the machine's lifetime cycle count |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine` | Master | Many-to-one | The machine |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `production_run` | Operational | Many-to-one | The run |
| Aggregated into | `production_count` | Operational | Many-to-one | Interval rollup |
| Referenced by | `scrap_record` | Operational | Indirectly, via run and machine | Scrap traced to cycles |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, on each cycle completion |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Prediction Agent (cycle deviation features), Monitoring Agent (deviation trend), Decision Agent (evidence), Analytics. **Not read by** the Dashboard, which uses `production_count` |
| **Archived by** | Platform retention job |
| **Immutable** | Yes |
| **Append-only** | Yes |
| **Expires** | Retained 90 days at full grain, then purged. `production_count` survives as the aggregate record |
| **Regenerable** | No. Source of truth for cycle-level timing. Once purged, per-cycle variance is gone permanently, which is why the aggregate is written before the purge rather than after |

**Business rules**

1. `cycle_time_seconds` must equal the interval between `cycle_started_at` and `cycle_ended_at`. Self-checking.
2. `cycle_number_in_run` is unique per machine per run and increments without gaps. A gap indicates a lost row.
3. `deviation_from_standard_pct` is measured against `product_line_capability.cycle_time_seconds` reached through the run. It is NULL when `interrupted` is 1.
4. Interrupted cycles are **excluded** from all deviation statistics, trend calculations, and prediction features. Including them would let one breakdown masquerade as extreme cycle-time degradation.
5. Cycles are only written for machines in `running` state. A machine in `setup` produces no cycles.
6. `outcome` must be consistent with quality records: a `scrap` outcome should have a corresponding `scrap_record` for the same run and machine.
7. Cycles are never edited. A mis-recorded cycle stays, and its correction is recorded elsewhere.
8. Aggregation into `production_count` happens at interval close. Cycles are the source; counts are the derivative.
9. `sequence_number` contributes to `machine_operational_status.accumulated_cycle_count`, and the two are reconciled per §9.3.

**Example records**

Cycles on `MC-0101` immediately around the vibration excursion.

| cycle # | started_at | ended_at | cycle_time_sec | deviation_pct | outcome | interrupted |
|---|---|---|---|---|---|---|
| 103 | 18:36:20 | 18:38:47 | 147.00 | 1.38 | `good` | 0 |
| 104 | 18:38:52 | 18:41:20 | 148.00 | 2.07 | `good` | 0 |
| 105 | 18:41:22 | 18:43:51 | 149.40 | 3.03 | `good` | 0 |
| 106 | 18:43:55 | 18:46:26 | 151.20 | 4.28 | `scrap` | 0 |
| 107 | 18:46:30 | 18:48:59 | 149.10 | 2.83 | `good` | 0 |
| 108 | 18:49:02 | 18:51:33 | 150.60 | 3.86 | `good` | 0 |
| 141 | 07-30 06:03:10 | 06:05:00 | 110.00 | NULL | `scrap` | **1** |

The story these seven rows tell:

- **Cycle time is climbing against a 145.00-second standard.** 147.0 → 148.0 → 149.4 → 151.2, deviation growing from 1.38 % to 4.28 %. This is happening at exactly the timestamps where §E1 shows vibration crossing 4.7 mm/s. **Two independent measurement paths — an accelerometer and the machine's own cycle timer — are drifting together.** That agreement is what elevates the root cause from a guess to a hypothesis with corroboration, and it is the single strongest piece of evidence in the whole incident.
- **Cycle 106 is the scrap**, and it is also the slowest at 151.20 seconds. A cycle running 4.28 % long that also produces an out-of-tolerance part is consistent with a spindle losing rigidity — the mechanism §8 confirms through dimensional inspection.
- **Cycle 141 is interrupted** and its deviation is NULL. The machine went down at 06:05 for the planned repair mid-cycle. Its 110.00-second duration is meaningless as a performance measure, and rule 4 keeps it out of every statistic. Without that exclusion, the repair would register as a 24 % cycle-time *improvement* and pollute the trend.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Sole writer. Generates cycle times from the capability standard with degradation-driven drift |
| **Monitoring Agent** | Tracks rolling mean deviation per machine. A sustained rise is an event trigger that requires no sensor threshold |
| **Prediction Agent** | **Reads this entity directly, not the aggregate.** Mean deviation, variance, and trend slope are among the strongest non-sensor features available |
| **Supervisor Agent** | Recent deviation trend as corroborating context |
| **Decision Agent** | **Cites cycle-time deviation as independent supporting evidence** — the second signal that makes a root-cause hypothesis defensible rather than speculative |
| **Notification Service** | Does not read this entity |
| **Dashboard** | Does not read this entity. Uses `production_count` |
| **Analytics** | OEE performance component; cycle-time capability studies |

---

### E8. `quality_inspection_result`

**Purpose**

Records the outcome of a quality inspection against sampled output, including which machine the defect is attributed to. It is what links product quality back to machine condition.

**Business description**

An inspection result is a finding. A sample is taken, measured, and judged, and the row records what was found and what was decided about it.

The entity's most important design feature is that **`machine_id` and `attributed_machine_id` are different fields.** The inspection happens at a station; the defect was caused somewhere else. On `LN-01`, the coordinate measuring machine `MC-0103` at position 3 inspects parts that were machined by `MC-0101` at position 1. A bore roundness deviation found at position 3 was created at position 1.

Without that separation, quality data would blame the inspection station for every defect it discovered, and the connection between machine degradation and product quality — one of the platform's most valuable inferences — would be unavailable.

**`attributed_failure_category_id`** takes it further by linking the defect to the failure taxonomy. A bore roundness deviation attributed to `MC-0101` with failure category `FC-BRG` is not merely a quality record: it is direct physical confirmation of the degradation mechanism the sensor and cycle data suggest. Bearing wear reduces spindle rigidity, which produces dimensional error. When the Decision Agent hypothesises bearing degradation, this row is the evidence that the mechanism is already producing consequences.

**`disposition`** records what was decided about the affected material — accepted, sent for rework, scrapped, or quarantined. It is deliberately separate from the finding, because a failed inspection does not automatically mean scrap. That separation is also why `scrap_record` is a distinct entity: **a finding is not a disposition**, and conflating them would lose the reworked material entirely.

**Note on quality specifications.** Dimensional tolerances and measurement limits are **not** in this model. Master data §35 lists quality specification limits as a deferred extension with no consumer, and the frozen scope excludes quality management. This entity records pass and fail counts as judged, not the limits used to judge them.

**Primary key**

`quality_inspection_result_id` — surrogate `INTEGER`, with `quality_inspection_result_code` unique.

A code is warranted: inspection records are referenced in quality documentation and cited by `scrap_record`.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `quality_inspection_result_code` | VARCHAR(20) | Yes | `QIR-20260729-0158` | Unique; matches `^QIR-[0-9]{8}-[0-9]{4}$` | Inspection record number, referenced in quality documentation |
| `production_run_id` | INTEGER (FK op) | Yes | `RUN-2026-0714` | References `production_run` | The run whose output was sampled |
| `machine_id` | INTEGER (FK master) | No | `MC-0103` | References `machine` when present | **Where the inspection took place.** NULL for manual inspection away from a station |
| `attributed_machine_id` | INTEGER (FK master) | No | `MC-0101` | References `machine` on the same line when present | **Which machine caused the defect.** NULL when no attribution is possible or the sample passed. Deliberately distinct from `machine_id` |
| `attributed_failure_category_id` | INTEGER (FK master) | No | `FC-BRG` | References `failure_category` when present | Failure mode believed responsible. NULL when unattributed. **Links product quality to the failure taxonomy** |
| `inspected_at` | DATETIME | Yes | `2026-07-29 18:52:00+05:30` | Not in the future | **Event time** of inspection |
| `inspection_type` | TEXT + CHECK | Yes | `in_process` | One of: `first_article`, `in_process`, `final`, `audit` | Inspection stage. First-article checks a changeover; in-process samples during production |
| `sample_size` | INTEGER | Yes | `5` | > 0 | Units examined |
| `pass_count` | INTEGER | Yes | `4` | ≥ 0; pass + fail = sample_size | Units within specification |
| `fail_count` | INTEGER | Yes | `1` | ≥ 0 | Units outside specification |
| `inspector_worker_id` | INTEGER (FK master) | Yes | `EMP-1020` | References active `worker` whose role has `role_category = 'inspector'` or is managerial | Who performed the inspection. Accountability and traceability |
| `disposition` | TEXT + CHECK | Yes | `scrap` | One of: `accept`, `rework`, `scrap`, `quarantine` | **What was decided about the affected material.** Separate from the finding — a failure does not automatically mean scrap |
| `primary_defect_note` | TEXT | No | `Bore roundness 0.021 mm out of tolerance on 1 of 5; consistent with reduced spindle rigidity` | Non-empty when `fail_count` > 0 | What was found, in inspector's language. **Read by the Decision Agent as corroborating evidence** |
| `related_operational_event_id` | INTEGER (FK op) | No | `EVT-20260729-0412` | References `operational_event` when present | Detected condition the defect is believed related to. NULL when unrelated. **Closes the loop from machine condition to product quality** |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `inspected_at` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `production_run` | Operational | Many-to-one | Run sampled |
| Parent | `machine` (inspection station) | Master | Many-to-one, optional | Where inspected |
| Parent | `machine` (attributed) | Master | Many-to-one, optional | What caused it |
| Parent | `failure_category` | Master | Many-to-one, optional | Failure mode responsible |
| Parent | `worker` | Master | Many-to-one | Inspector |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `operational_event` | Operational | Many-to-one, optional | Related detected condition |
| Child | `scrap_record` | Operational | One-to-many | Scrap arising from this finding |

Note that `machine` is referenced **twice** from this entity, in two distinct roles. Both are role-qualified in their names, and neither creates a cycle since `machine` is master data and references nothing operational.

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, modelling inspection activity |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Monitoring Agent (quality condition detection), Supervisor Agent (quality context), Decision Agent (corroborating evidence), Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. An inspection judgement was made at a moment. A re-inspection is a new row |
| **Append-only** | Yes |
| **Expires** | Retained 3 years, then archived indefinitely. Quality records have the longest retention in the model, because quality history outlives the material it describes |
| **Regenerable** | No. Source of truth for quality |

**Business rules**

1. `pass_count` + `fail_count` must equal `sample_size`. Self-checking.
2. `primary_defect_note` is required when `fail_count` > 0. A recorded failure with no description is not usable evidence.
3. `attributed_machine_id`, when present, must be on the same production line as the run. A defect cannot be attributed to a machine that did not touch the part.
4. `attributed_machine_id` and `attributed_failure_category_id` are populated together or both left NULL. Attributing a defect to a machine without naming a mechanism is half an inference.
5. `disposition = 'scrap'` requires a corresponding `scrap_record`. The finding and the material consequence are separate facts and both must exist.
6. `disposition = 'accept'` with `fail_count` > 0 is permitted — a concession — and the reason belongs in `primary_defect_note`.
7. `inspector_worker_id` must reference a worker whose role permits inspection. Master data `worker_role` records that authority.
8. Inspections are never edited. A re-inspection is a new row referencing the same run.
9. A rising `fail_count` rate across consecutive inspections for one attributed machine is a quality condition the Monitoring Agent may raise as an event, independently of any sensor threshold.

**Example records**

Inspections on `RUN-2026-0714`.

| code | run | station | attributed_machine | attributed_failure | inspected_at | type | sample | pass | fail | inspector | disposition | related_event |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `QIR-20260729-0142` | `RUN-2026-0714` | `MC-0103` | NULL | NULL | 07-29 14:25 | `first_article` | 3 | 3 | 0 | `EMP-1020` | `accept` | NULL |
| `QIR-20260729-0151` | `RUN-2026-0714` | `MC-0103` | NULL | NULL | 07-29 16:40 | `in_process` | 5 | 5 | 0 | `EMP-1020` | `accept` | NULL |
| `QIR-20260729-0158` | `RUN-2026-0714` | `MC-0103` | `MC-0101` | `FC-BRG` | 07-29 18:52 | `in_process` | 5 | 4 | 1 | `EMP-1020` | `scrap` | `EVT-20260729-0412` |
| `QIR-20260729-0164` | `RUN-2026-0714` | `MC-0103` | `MC-0101` | `FC-BRG` | 07-29 21:15 | `in_process` | 5 | 4 | 1 | `EMP-1020` | `rework` | `EVT-20260729-0412` |
| `QIR-20260730-0031` | `RUN-2026-0714` | `MC-0103` | NULL | NULL | 07-30 11:30 | `first_article` | 3 | 3 | 0 | `EMP-1020` | `accept` | NULL |

This sequence is the third independent confirmation of the same underlying problem:

- **The first two inspections pass cleanly.** Baseline established before the excursion.
- **`QIR-20260729-0158` at 18:52 is the turn.** It comes 12 minutes after cycle 106 was scrapped and 10 minutes after vibration crossed its warning limit. The defect is attributed to `MC-0101` with `FC-BRG`, and it references the same event. **Bore roundness deviation is the physical consequence of the mechanism the sensor data suggests:** a degrading bearing reduces spindle rigidity, and reduced rigidity produces out-of-round bores.
- **So three mechanically independent signals now agree.** Vibration from an accelerometer (§E1), cycle time from the machine's own timer (§E7), and dimensional geometry from a coordinate measuring machine (§E8). No single one of these would justify a confident root-cause claim. Together they make the hypothesis in §E18 defensible under challenge — which is the whole reason the explainability contract demands supporting evidence rather than a bare probability.
- **`QIR-20260729-0164` shows disposition doing its job.** Same defect type, same attribution, but this unit is sent for **rework** rather than scrapped. If the finding and the disposition were one field, this recovered unit would be indistinguishable from the lost one.
- **`QIR-20260730-0031` is the post-repair first-article check.** Three parts, all pass, no attribution. This row is how the platform closes the loop and demonstrates the repair worked.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Generates inspections on realistic sampling frequencies, with defect rates that rise as attributed machines degrade |
| **Monitoring Agent** | Detects rising failure rates per attributed machine — a quality-based detection path independent of sensors |
| **Prediction Agent** | Recent attributed failure counts are a degradation feature, and one with unusually high signal-to-noise |
| **Supervisor Agent** | Assembles quality context. A machine with attributed defects is a materially stronger case than one with a sensor drift alone |
| **Decision Agent** | **Cites `primary_defect_note` as corroborating evidence**, and uses `attributed_failure_category_id` to confirm the root-cause hypothesis against physical consequence |
| **Notification Service** | Reports whether quality has already been affected, which changes how a recipient reads the urgency |
| **Dashboard** | Quality trend per line and per machine |
| **Analytics** | First-pass yield, defect Pareto by attributed machine and failure category, and correlation between machine condition and quality loss |

---

### E9. `scrap_record`

**Purpose**

Records units scrapped, with quantity, reason, and attribution. It is the material and financial consequence of quality failure, and the link between machine condition and cost.

**Business description**

A scrap record is a disposition, not a finding. `quality_inspection_result` records that a part failed inspection; this entity records that material was written off as a result.

The two are separate because **not every failure becomes scrap.** A part may be reworked and recovered, accepted under concession, or quarantined pending review. Conflating finding with disposition would erase that distinction and systematically overstate material loss.

Scrap is also produced without any inspection at all — a setup reject discarded by the operator, a part damaged in handling. Those have no inspection record, which is why `quality_inspection_result_id` is nullable.

**Attribution mirrors the inspection entity.** `machine_id` is where the scrap occurred; `attributed_machine_id` is the machine responsible. `attributed_failure_category_id` names the mechanism. This is what allows Analytics to answer a question that directly justifies the platform's existence: *how much material did we lose to machine degradation that we could have prevented?*

**Note on scrap cost.** There is no `material_cost_impact` attribute. Scrap cost is computed at read time as quantity × `product.standard_material_cost`. The tradeoff is accepted deliberately: standard costs are revised infrequently, and a historical scrap report reflecting current standard cost is acceptable to every consumer here. Storing a point-in-time cost would extend the §3.5 evidentiary exception to a second place, and the case for it is much weaker than for thresholds — nobody's recommendation hinges on a scrap cost figure being reproducible to the rupee, whereas a recommendation citing a threshold value depends entirely on that value being the one that actually fired.

**Primary key**

`scrap_record_id` — surrogate `INTEGER`. No business code: scrap is analysed in aggregate, and individual records are not named on the shop floor.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `production_run_id` | INTEGER (FK op) | Yes | `RUN-2026-0714` | References `production_run` | The run the material belonged to |
| `machine_id` | INTEGER (FK master) | Yes | `MC-0103` | References `machine` | **Where the scrap was identified and recorded** |
| `attributed_machine_id` | INTEGER (FK master) | No | `MC-0101` | References `machine` on the same line when present | **Machine responsible for the defect.** NULL for material defects or handling damage |
| `attributed_failure_category_id` | INTEGER (FK master) | No | `FC-BRG` | References `failure_category` when present | Mechanism responsible. NULL when not machine-caused |
| `recorded_at` | DATETIME | Yes | `2026-07-29 18:53:00+05:30` | Not in the future | **Event time** of the scrap decision |
| `quantity_units` | NUMERIC(12,2) | Yes | `1.00` | > 0 | Units written off, in the product's unit of measure |
| `scrap_reason` | TEXT + CHECK | Yes | `dimensional_deviation` | One of: `dimensional_deviation`, `surface_defect`, `tool_mark`, `material_defect`, `setup_reject`, `machine_fault`, `handling_damage`, `process_deviation` | **Why the material was scrapped.** The Pareto dimension for scrap analysis |
| `quality_inspection_result_id` | INTEGER (FK op) | No | `QIR-20260729-0158` | References `quality_inspection_result` when present | Inspection that found the defect. **NULL for scrap not arising from formal inspection**, such as a setup reject |
| `related_operational_event_id` | INTEGER (FK op) | No | `EVT-20260729-0412` | References `operational_event` when present | Detected machine condition the scrap is believed related to. **The link that makes preventable loss measurable** |
| `recorded_by_worker_id` | INTEGER (FK master) | Yes | `EMP-1020` | References active `worker` | Who recorded the scrap. Accountability |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `recorded_at` | Crew on duty |
| `notes` | TEXT | No | NULL | — | Additional context. NULL normally |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `production_run` | Operational | Many-to-one | Run affected |
| Parent | `machine` (location) | Master | Many-to-one | Where recorded |
| Parent | `machine` (attributed) | Master | Many-to-one, optional | Responsible machine |
| Parent | `failure_category` | Master | Many-to-one, optional | Mechanism |
| Parent | `worker` | Master | Many-to-one | Who recorded it |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `quality_inspection_result` | Operational | Many-to-one, optional | Originating finding |
| Parent | `operational_event` | Operational | Many-to-one, optional | Related condition |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Monitoring Agent (scrap rate), Supervisor Agent (quality and cost context), Decision Agent (loss quantification), Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A write-off happened. A reversal is a separate compensating record with a note, never an edit |
| **Append-only** | Yes |
| **Expires** | Retained 3 years alongside quality records, then archived indefinitely |
| **Regenerable** | No. Source of truth for material loss |

**Business rules**

1. `quantity_units` must be greater than zero. A zero-quantity scrap record is meaningless.
2. `attributed_machine_id` and `attributed_failure_category_id` are populated together or both NULL.
3. `attributed_machine_id`, when present, must be on the same line as the run.
4. `scrap_reason = 'machine_fault'` requires both attribution fields to be populated. Blaming a machine without naming which machine and which mechanism is not usable data.
5. `scrap_reason` values `material_defect` and `handling_damage` must have NULL attribution — neither is machine-caused, and attributing them would corrupt the preventable-loss figure.
6. Every `quality_inspection_result` with `disposition = 'scrap'` must have at least one scrap record. The converse does not hold: scrap can exist without an inspection.
7. Scrap consumes material, so a corresponding `inventory_movement` of type `scrap_consumption` is expected for the material the scrapped units contained.
8. Scrap cost is **computed at read time**, never stored. See the note above.
9. Records are never edited or deleted. A reversal is a compensating record, so both the error and its correction remain visible.
10. Scrap attributed to a machine and linked to an event is the basis of preventable-loss reporting, which is how the platform's value is ultimately measured.

**Example records**

Scrap on `RUN-2026-0714`, plus one contrasting record from another line.

| run | machine | attributed_machine | attributed_failure | recorded_at | qty | scrap_reason | inspection | related_event | recorded_by |
|---|---|---|---|---|---|---|---|---|---|
| `RUN-2026-0714` | `MC-0101` | NULL | NULL | 07-29 14:18 | 2.00 | `setup_reject` | NULL | NULL | `EMP-1003` |
| `RUN-2026-0714` | `MC-0103` | `MC-0101` | `FC-BRG` | 07-29 18:53 | 1.00 | `dimensional_deviation` | `QIR-20260729-0158` | `EVT-20260729-0412` | `EMP-1020` |
| `RUN-2026-0714` | `MC-0101` | NULL | NULL | 07-29 20:52 | 1.00 | `tool_mark` | NULL | NULL | `EMP-1003` |
| `RUN-2026-0714` | `MC-0103` | `MC-0101` | `FC-BRG` | 07-30 04:10 | 2.00 | `dimensional_deviation` | NULL | `EVT-20260729-0412` | `EMP-1005` |
| `RUN-2026-0715` | `MC-0301` | NULL | NULL | 07-29 15:30 | 3.00 | `material_defect` | NULL | NULL | `EMP-1030` |

Each row demonstrates a different path through the model:

- **Row 1: setup reject, no attribution, no inspection.** Two parts discarded during changeover at 14:18, six minutes after the run started. Entirely normal, and correctly attributed to nothing — it is a process cost, not a machine failure.
- **Row 2: the fully-linked scrap.** Attributed machine, attributed mechanism, originating inspection, and related event all populated. This is the shape of a preventable loss: one unit of `PRD-GH-100`, and at a standard material cost of 2,140 the loss is computable at read time without storing a figure.
- **Row 3: a tool mark with no attribution.** Recorded at 20:52, four minutes after the tool change in §3. A tool-related surface defect right after a tool change is a setup artefact, not bearing degradation. Not attributing it is what keeps the bearing's preventable-loss figure honest.
- **Row 4: attributed scrap with no inspection.** Two more units at 04:10 on 30 July, found by the night-shift operator without a formal inspection. Attribution is still possible because the event was open and the defect type matched. Rule 6's asymmetry — inspection implies scrap, scrap does not imply inspection — is what makes this row expressible.
- **Row 5: material defect on another line, deliberately unattributed.** Three valve bodies scrapped for a casting flaw. Rule 5 forbids attribution here, because blaming `MC-0301` for a supplier's material problem would inflate the machine's preventable-loss figure and eventually drive a wrong maintenance decision.

Total scrap attributed to `MC-0101`'s bearing degradation across the incident: **3 units.** That is a small, concrete, defensible number — and it exists only because attribution is modelled deliberately rather than inferred after the fact.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Generates scrap at realistic rates that rise with attributed machine degradation |
| **Monitoring Agent** | Compares scrap rate against `product.target_scrap_rate_pct` and detects rising attributed scrap per machine |
| **Prediction Agent** | Attributed scrap count over a window is a degradation feature with high signal-to-noise |
| **Supervisor Agent** | Adds quality and material loss to decision context, strengthening the case for escalation |
| **Decision Agent** | **Quantifies loss already incurred**, which is often more persuasive than projected loss because it has already happened |
| **Notification Service** | Reports material already lost, making the cost of inaction concrete |
| **Dashboard** | Scrap rate and reason breakdown per line and machine |
| **Analytics** | **Preventable-loss reporting.** Scrap attributed to machine causes and linked to events is the clearest measure of what the platform saves when its recommendations are acted on |

---

## Group C — Material & Maintenance

What was consumed and what was repaired. This group supplies the two facts that determine whether a recommendation can actually be carried out: **is the part on the shelf**, and **was the work done**.

The group also closes the platform's accuracy loop. `maintenance_work_record.confirmed_failure_category_id` records what the engineer actually found, against `prediction_result.predicted_failure_category_id` recording what the model expected. That comparison is the only honest measure of whether the platform's predictions are right, and it is the reason this group exists in as much detail as it does.

`maintenance_work_record` and `machine_maintenance_activity` apply the lifecycle-and-history pairing from §4.3: the work record is a mutable job header, the activity log is its immutable timeline.

---

### E10. `inventory_movement`

**Purpose**

The stock ledger. Every receipt, issue, return, adjustment, and consumption, with the resulting balance. It is the single source of truth for how much of anything is on hand.

**Business description**

Master data holds stocking **policy** — reorder point, safety stock, maximum, lead time — and deliberately holds no quantity (§19 of the master document). This entity is where quantity lives, and it lives as a ledger rather than as a number.

**Why a ledger with a running balance rather than a balance table.** Each movement stores both the signed change and the balance that resulted. This is the standard accounting pattern and it buys three things:

1. **Current stock is one indexed lookup** — the latest movement for an item — rather than a `SUM` over the item's entire history.
2. **The ledger is self-auditing.** Every row's balance must equal the previous row's balance plus this row's delta. A break in that chain is immediately locatable rather than merely detectable.
3. **Stock at any past moment is recoverable** by finding the last movement before that time. "Was the bearing in stock when we made the recommendation?" is answerable without replay.

A separate `inventory_balance` current-state entity was considered and rejected. It would be a second source of truth for the same number, and the two would eventually disagree — at which point neither could be trusted. §5.3 records the decision.

**Balance is maintained per item, not per item-and-location.** Master data assigns each item a single `default_inventory_location_id`, so item and location are effectively one-to-one in this factory. `inventory_location_id` on the movement records **where the transaction physically happened**, which matters because `average_retrieval_time_minutes` differs by location and feeds the repair-time estimate. Multi-location balances are a deferred extension with no current consumer.

**`movement_type` distinguishes production consumption from maintenance consumption**, and the distinction is not cosmetic. Issuing a cast iron blank to a production run is planned material flow driven by the bill of materials. Issuing a spindle bearing to a work order is unplanned consumption of a critical spare, and it is the event that may drop that spare below its reorder point. The two need different monitoring, and one enumerated value separates them.

**Primary key**

`inventory_movement_id` — surrogate `INTEGER`, with `inventory_movement_code` unique.

A code is warranted: stores staff reference transaction numbers when reconciling physical stock, and the code appears on issue documentation.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `inventory_movement_code` | VARCHAR(22) | Yes | `MOV-20260730-00287` | Unique; matches `^MOV-[0-9]{8}-[0-9]{5}$` | Transaction number used by stores staff |
| `inventory_item_id` | INTEGER (FK master) | Yes | `INV-CP-BRG-6205` | References active `inventory_item` | What moved. Thresholds and lead time come from master data |
| `inventory_location_id` | INTEGER (FK master) | Yes | `LOC-SP-B2` | References active `inventory_location` | **Where the transaction happened.** Supplies retrieval time for repair estimates |
| `movement_at` | DATETIME | Yes | `2026-07-30 06:38:00+05:30` | Not in the future | **Event time** of the movement |
| `movement_type` | TEXT + CHECK | Yes | `issue_maintenance` | One of: `receipt`, `issue_production`, `issue_maintenance`, `return`, `adjustment`, `scrap_consumption`, `transfer_out`, `transfer_in` | **Why stock moved.** Separates planned production draw from unplanned spare consumption |
| `quantity_delta` | NUMERIC(12,4) | Yes | `-1.0000` | Non-zero; sign must match `movement_type` | **Signed change.** Negative for issues and consumption, positive for receipts and returns |
| `resulting_quantity_on_hand` | NUMERIC(12,4) | Yes | `8.0000` | ≥ 0; = previous balance + `quantity_delta` | **The running balance.** Makes current stock a single lookup and the ledger self-auditing |
| `production_run_id` | INTEGER (FK op) | No | NULL | References `production_run` when present | Run that consumed the material. Required for `issue_production` |
| `maintenance_work_record_id` | INTEGER (FK op) | No | `WO-2026-0341` | References `maintenance_work_record` when present | Job that consumed the part. Required for `issue_maintenance` |
| `scrap_record_id` | INTEGER (FK op) | No | NULL | References `scrap_record` when present | Scrap that consumed the material. Required for `scrap_consumption` |
| `supplier_id` | INTEGER (FK master) | No | NULL | References active `supplier` when present | Source of a receipt. Required for `receipt` |
| `recorded_by_worker_id` | INTEGER (FK master) | Yes | `EMP-1030` | References active `worker` | Who performed the transaction. Accountability |
| `shift_id` | INTEGER (FK master) | Yes | `SH-A` | References `shift` containing `movement_at` | Crew on duty. **A part needed on a shift with no storekeeper is a real constraint** |
| `reference_note` | TEXT | No | `Spindle bearing for WO-2026-0341, combined bearing replacement and 500h service` | — | Context. NULL for routine movements |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `inventory_item` | Master | Many-to-one | What moved |
| Parent | `inventory_location` | Master | Many-to-one | Where |
| Parent | `supplier` | Master | Many-to-one, optional | Receipt source |
| Parent | `worker` | Master | Many-to-one | Who recorded it |
| Parent | `shift` | Master | Many-to-one | Crew on duty |
| Parent | `production_run` | Operational | Many-to-one, optional | Consuming run |
| Parent | `maintenance_work_record` | Operational | Many-to-one, optional | Consuming job |
| Parent | `scrap_record` | Operational | Many-to-one, optional | Consuming scrap |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Monitoring Agent (threshold breach detection), Supervisor Agent (availability), Decision Agent (can the repair proceed), Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A stock transaction happened. Errors are corrected by an `adjustment` movement, never by editing |
| **Append-only** | Yes |
| **Expires** | Retained 3 years, then archived indefinitely. This is the material record and it must reconcile to physical stock counts |
| **Regenerable** | No. Source of truth for stock |

**Business rules**

1. `resulting_quantity_on_hand` must equal the previous movement's balance for the same item plus this movement's `quantity_delta`. **The chain is the audit.** A break is a data quality incident with a locatable origin.
2. Sign must match type. `receipt`, `return`, and `transfer_in` are positive; `issue_production`, `issue_maintenance`, `scrap_consumption`, and `transfer_out` are negative. `adjustment` may be either.
3. `resulting_quantity_on_hand` may never go negative. Issuing more than is on hand is a physical impossibility and indicates a defect or an unrecorded receipt.
4. Reference requirements by type: `issue_production` requires a run, `issue_maintenance` requires a work record, `scrap_consumption` requires a scrap record, `receipt` requires a supplier. Unreferenced consumption is untraceable material.
5. **Current stock is defined as the `resulting_quantity_on_hand` of the latest movement** for an item. It is not stored anywhere else.
6. A movement leaving stock at or below `inventory_item.reorder_point` is a replenishment condition the Monitoring Agent raises as an event.
7. A movement leaving a `is_critical_spare` item at or below `safety_stock_qty` is a **high-severity** condition. The master model's rule that critical spares must carry non-zero safety stock exists precisely so this check has a floor to compare against.
8. `adjustment` movements require a `reference_note`. An unexplained stock correction destroys the ledger's credibility.
9. `issue_production` quantities should be consistent with `bill_of_materials` quantity per unit plus scrap allowance for the units produced. Reconciled as a data quality check.
10. Movements are never deleted. A reversal is a compensating movement, leaving both visible.

**Example records**

Movements spanning the incident.

| code | item | location | movement_at | type | delta | resulting_qty | run | work_record | recorded_by |
|---|---|---|---|---|---|---|---|---|---|
| `MOV-20260729-00241` | `INV-RM-CI250` | `LOC-RM-A1` | 07-29 14:02 | `issue_production` | -122.0000 | 498.0000 | `RUN-2026-0714` | NULL | `EMP-1030` |
| `MOV-20260729-00258` | `INV-CN-COOL-20L` | `LOC-RM-A1` | 07-29 16:30 | `issue_production` | -9.7600 | 312.2400 | `RUN-2026-0714` | NULL | `EMP-1030` |
| `MOV-20260729-00263` | `INV-RM-CI250` | `LOC-RM-A1` | 07-29 18:53 | `scrap_consumption` | -1.0000 | 497.0000 | `RUN-2026-0714` | NULL | `EMP-1020` |
| `MOV-20260729-00270` | `INV-TL-EM-C12` | `LOC-TC-01` | 07-29 20:50 | `issue_production` | -1.0000 | 24.0000 | `RUN-2026-0714` | NULL | `EMP-1003` |
| `MOV-20260730-00287` | `INV-CP-BRG-6205` | `LOC-SP-B2` | 07-30 06:38 | `issue_maintenance` | -1.0000 | **8.0000** | NULL | `WO-2026-0341` | `EMP-1030` |
| `MOV-20260730-00288` | `INV-CP-BELT-V38` | `LOC-SP-B2` | 07-30 06:40 | `issue_maintenance` | -1.0000 | 5.0000 | NULL | `WO-2026-0341` | `EMP-1030` |
| `MOV-20260730-00301` | `INV-CP-BRG-6205` | `LOC-SP-B2` | 07-30 15:10 | `receipt` | +12.0000 | 20.0000 | NULL | NULL | `EMP-1030` |

The ledger tells its own story:

- **The bearing issue at 06:38 leaves exactly 8 on hand — the reorder point.** Master data sets `reorder_point = 8.00` and `safety_stock_qty = 4.00` for `INV-CP-BRG-6205`. The movement lands precisely on the reorder threshold, so rule 6 fires and the Monitoring Agent raises a replenishment event. **One repair simultaneously resolved a machine risk and created a supply risk**, and the model surfaces both from the same row.
- **The 06:38 timestamp validates the retrieval estimate.** The part was requested at 06:22 and collected at 06:38 — 16 minutes against the 15-minute `average_retrieval_time_minutes` for `LOC-SP-B2`. The estimate the Decision Agent used in its downtime figure held.
- **A drive belt was also consumed.** Not predicted, but replaced opportunistically while the spindle was open. Real maintenance does this, and the ledger captures it without the model needing a concept of "opportunistic work."
- **Material consumption reconciles against the bill of materials.** 122 blanks issued for a run producing 102 good plus 3 scrap plus 1 rework at 1 unit each with a 2 % scrap allowance — the issue runs ahead of consumption, as batch material issues do. Rule 9 makes that check possible.
- **Receipt at 15:10 restores stock to 20**, comfortably below the 24 maximum. The supplier `SUP-002` quotes a 7-day standard lead time, so this receipt was already in transit when the bearing was issued — which is exactly the situation safety stock exists to cover.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Generates production consumption from bills of materials, maintenance consumption from work orders, and receipts on supplier lead times |
| **Monitoring Agent** | Compares each movement's resulting balance against `reorder_point` and `safety_stock_qty` and raises replenishment events. **A single indexed read per movement, not an aggregate** |
| **Prediction Agent** | Does not read this entity. Stock levels are not a machine failure predictor |
| **Supervisor Agent** | **Resolves part availability** for the inventory element of decision context, using the latest balance per item |
| **Decision Agent** | **Determines whether a repair can proceed.** Part on hand means a maintenance-window decision; part short means a lead-time problem and a different recommendation entirely |
| **Notification Service** | States part availability in the message so the recipient does not have to check separately |
| **Dashboard** | Stock levels against master thresholds, highlighting critical spares near their floor |
| **Analytics** | Consumption trends, spare usage by failure category, stock accuracy against physical counts |

---

### E11. `maintenance_work_record`

**Purpose**

Represents one maintenance job from request to closure: what was needed, why, who did it, what they found, how long the machine was down, and how it was resolved. It is the operational record of maintenance and the platform's accuracy scorecard.

**Business description**

A work record is the work order. It advances through a lifecycle over hours or days and carries decisions that exist nowhere else, which makes it one of only three mutable, non-recomputable entities in the model.

Three aspects give it disproportionate importance.

**It is the maintenance history the master model deliberately did not cache.** Master data §26 excluded `last_performed_date` and `next_due_date` from `machine_maintenance_schedule`, computing due status instead from `baseline_start_date` plus operational history. **This entity is that history.** A closed work record referencing a schedule is what establishes when that schedule was last satisfied, and the Supervisor Agent reads exactly that to determine whether maintenance is due or overdue.

**It closes the prediction accuracy loop.** `reported_failure_category_id` records what was suspected when the job opened — normally copied from the prediction. `confirmed_failure_category_id` records what the engineer actually found. Comparing the two across many jobs is the **only honest measure of whether the platform's predictions are correct.** A model with a 0.90 average confidence and a 40 % confirmation rate is not a good model, and no amount of internal validation metrics would reveal that; only the engineer's findings will.

**It quantifies what the platform is worth.** `machine_downtime_minutes` plus `did_stop_line`, combined with whether the job was `predictive` or `corrective`, is how the value of acting on recommendations gets measured. Predictive jobs that prevented unplanned stoppages are the return; corrective jobs that followed missed predictions are the gap.

**`work_type = 'predictive'` is distinct from `preventive` and `corrective`**, and the distinction is the point of the whole platform:

| Work type | Meaning |
|---|---|
| `preventive` | Scheduled by interval. Would have happened anyway |
| `predictive` | **Triggered by a platform recommendation.** Would not have happened without FactoryFlow AI |
| `corrective` | Reactive repair after a failure. What the platform exists to reduce |
| `emergency` | Unplanned breakdown requiring immediate response |
| `calibration`, `inspection` | Compliance and verification work |

Counting `predictive` jobs that displaced `corrective` ones is the platform's business case, expressed in data rather than in argument.

**Primary key**

`maintenance_work_record_id` — surrogate `INTEGER`, with `maintenance_work_record_code` unique.

A code is essential: the work order number is used across the plant, spoken aloud, written on paper, and cited in recommendations and notifications.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `maintenance_work_record_code` | VARCHAR(14) | Yes | `WO-2026-0341` | Unique; matches `^WO-[0-9]{4}-[0-9]{4}$` | Work order number used plant-wide |
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine` not `decommissioned` | The machine being worked on |
| `work_type` | TEXT + CHECK | Yes | `predictive` | One of: `preventive`, `predictive`, `corrective`, `emergency`, `calibration`, `inspection` | **The platform's value metric.** `predictive` means the job exists because FactoryFlow AI recommended it |
| `machine_maintenance_schedule_id` | INTEGER (FK master) | No | `SCH-0001` | References active `machine_maintenance_schedule` for the same machine when present | Schedule this job satisfies. **Populating it is what marks the schedule as performed.** NULL for purely reactive work |
| `triggering_alert_id` | INTEGER (FK op) | No | `ALR-20260729-0087` | References `operational_alert` when present | Alert that led to this job. NULL for scheduled work |
| `triggering_recommendation_id` | INTEGER (FK op) | No | `REC-20260729-0031` | References `ai_recommendation` when present | **Recommendation that caused this job.** Required when `work_type = 'predictive'` |
| `reported_failure_category_id` | INTEGER (FK master) | No | `FC-BRG` | References `failure_category` when present | **Suspected** mechanism at open, normally from the prediction. NULL for routine preventive work |
| `confirmed_failure_category_id` | INTEGER (FK master) | No | `FC-BRG` | References `failure_category`; required when `work_status = 'closed'` and the job was corrective or predictive | **What the engineer actually found.** The ground truth against which every prediction is scored |
| `priority_severity_level_id` | INTEGER (FK master) | Yes | `SEV-2` | References active `failure_severity_level` | Job urgency, using the platform-wide severity scale |
| `assigned_maintenance_team_id` | INTEGER (FK master) | No | `MTM-MECH` | References active `maintenance_team` when present | Team responsible. NULL while `open` |
| `assigned_engineer_id` | INTEGER (FK master) | No | `ENG-01` | References active `maintenance_engineer` on the assigned team when present | Named engineer. NULL until assigned |
| `work_status` | TEXT + CHECK | Yes | `closed` | One of: `open`, `assigned`, `in_progress`, `awaiting_parts`, `completed`, `closed`, `cancelled` | **Lifecycle position** |
| `opened_at` | DATETIME | Yes | `2026-07-29 19:15:00+05:30` | Not in the future | When the job was raised |
| `assigned_at` | DATETIME | No | `2026-07-29 19:20:00+05:30` | ≥ `opened_at` | When a team was assigned. NULL while `open` |
| `started_at` | DATETIME | No | `2026-07-30 06:05:00+05:30` | ≥ `assigned_at` | When work physically began |
| `completed_at` | DATETIME | No | `2026-07-30 10:20:00+05:30` | ≥ `started_at` | When the repair finished |
| `closed_at` | DATETIME | No | `2026-07-30 10:35:00+05:30` | ≥ `completed_at` | When the job was signed off. **Only on closure do the machine's maintenance counters update** |
| `planned_duration_minutes` | INTEGER | No | `255` | > 0 when present | Expected duration, from `machine_type_failure_mode.estimated_repair_duration_minutes` plus retrieval time. NULL for unplanned work |
| `actual_duration_minutes` | INTEGER | No | `255` | > 0 when present; = `completed_at` − `started_at` | Actual working time. **Compared against planned to validate estimates** |
| `machine_downtime_minutes` | INTEGER | No | `270` | ≥ `actual_duration_minutes` when present | Total time the machine was unavailable. Exceeds working time by handover and restart |
| `did_stop_line` | INTEGER | Yes | `1` | Default 0 | Whether the line stopped. **The difference between a nuisance and a production loss** |
| `resolution_note` | TEXT | No | `Front spindle bearing replaced, 0.04 mm radial play measured on removal. Drive belt replaced opportunistically. 500h service performed concurrently. Vibration 1.9 mm/s on test run.` | Required when `work_status = 'closed'` | What was done and found. **Read by the Decision Agent for future similar situations, and by Analytics for accuracy scoring** |
| `shift_id_opened` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `opened_at` | Crew on duty when raised |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine` | Master | Many-to-one | Machine worked on |
| Parent | `machine_maintenance_schedule` | Master | Many-to-one, optional | Schedule satisfied |
| Parent | `failure_category` ×2 | Master | Many-to-one, optional | Reported and confirmed mechanisms |
| Parent | `failure_severity_level` | Master | Many-to-one | Job priority |
| Parent | `maintenance_team`, `maintenance_engineer` | Master | Many-to-one, optional | Assignment |
| Parent | `shift` | Master | Many-to-one | Crew at open |
| Parent | `operational_alert` | Operational | Many-to-one, optional | Triggering alert |
| Parent | `ai_recommendation` | Operational | Many-to-one, optional | Triggering recommendation |
| Child | `machine_maintenance_activity` | Operational | One-to-many | Timeline of steps |
| Child | `inventory_movement` | Operational | One-to-many | Parts consumed |
| Referenced by | `machine_state_transition` | Operational | One-to-many | Downtime caused |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, modelling both scheduled and reactive maintenance |
| **Updated by** | Factory Simulator only. Advances status and sets timestamps |
| **Read by** | All eight components |
| **Archived by** | Platform retention job |
| **Immutable** | No. **Lifecycle entity** |
| **Append-only** | No |
| **Expires** | Retained 5 years, then archived indefinitely. **The longest retention in the model** — asset maintenance history outlives most other records and is required for warranty and reliability analysis |
| **Regenerable** | No. Carries the engineer's findings, which exist nowhere else |

**State machine**

```
open ──► assigned ──► in_progress ──► completed ──► closed
                            │  ▲
                            ▼  │
                      awaiting_parts
   │            │            │
   └────────────┴────────────┴──► cancelled
```

| Transition | Trigger and effect |
|---|---|
| `open` → `assigned` | Team and engineer assigned; `assigned_at` set |
| `assigned` → `in_progress` | Work begins; `started_at` set; machine transitions to a down state |
| `in_progress` → `awaiting_parts` | Required part unavailable. Downtime continues to accrue |
| `awaiting_parts` → `in_progress` | Part received |
| `in_progress` → `completed` | Repair finished; `completed_at` and `actual_duration_minutes` set |
| `completed` → `closed` | Signed off; `closed_at`, `confirmed_failure_category_id`, and `resolution_note` set. **The machine's maintenance counters update here and only here** |
| any → `cancelled` | Job withdrawn |

**Business rules**

1. `work_type = 'predictive'` **requires** `triggering_recommendation_id`. That is the definition: predictive work exists because the platform recommended it.
2. `confirmed_failure_category_id` and `resolution_note` are **required** to reach `closed` for corrective, predictive, and emergency work. Closing without recording what was found destroys the accuracy loop.
3. `machine_maintenance_schedule_id`, when populated on a closed record, marks that schedule as performed. This is the **sole mechanism** by which maintenance due status advances.
4. On transition to `closed`, `machine_operational_status.operating_hours_at_last_maintenance` and `cycle_count_at_last_maintenance` are updated to the machine's current counters. No other path may update them.
5. The assigned team's `specialization` should match the machine's category `primary_maintenance_specialization`, or the confirmed failure's `required_specialization`. A mismatch is a data quality flag, not a hard constraint — cross-trained engineers are legitimate.
6. The assigned engineer must belong to the assigned team, must have a valid `certification_expiry_date`, and must be `is_on_call` if assigned outside their shift. All three are master data facts the platform checks before recommending.
7. `machine_downtime_minutes` must be at least `actual_duration_minutes`. Downtime includes handover and restart that working time excludes.
8. Every job in `in_progress` must have a corresponding `machine_state_transition` to a down state, with `triggering_work_record_id` populated.
9. `awaiting_parts` requires a `required_inventory_item_id` on the associated failure mode whose current stock is zero. Otherwise the status is unexplained.
10. A job may satisfy a schedule **and** address a predicted failure simultaneously — the combined job in the worked example. Both references are populated, and this is the outcome the Decision Agent should prefer, because one stoppage is cheaper than two.
11. Records are never deleted. Cancellation preserves the row and its reason.

**Example records**

| code | machine | work_type | schedule | trig_alert | trig_rec | reported | confirmed | severity | team | engineer | status | opened_at | started_at | completed_at | planned_min | actual_min | downtime_min | stopped_line |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `WO-2026-0341` | `MC-0101` | `predictive` | `SCH-0001` | `ALR-20260729-0087` | `REC-20260729-0031` | `FC-BRG` | `FC-BRG` | `SEV-2` | `MTM-MECH` | `ENG-01` | `closed` | 07-29 19:15 | 07-30 06:05 | 07-30 10:20 | 255 | 255 | 270 | 1 |
| `WO-2026-0338` | `MC-0202` | `inspection` | `SCH-0006` | NULL | NULL | NULL | NULL | `SEV-4` | `MTM-MECH` | `ENG-05` | `closed` | 07-28 09:00 | 07-28 09:30 | 07-28 10:10 | 45 | 40 | 40 | 0 |
| `WO-2026-0344` | `MC-0301` | `corrective` | NULL | `ALR-20260729-0091` | NULL | `FC-SENS` | `FC-SENS` | `SEV-3` | `MTM-AUTO` | `ENG-03` | `closed` | 07-30 09:15 | 07-30 09:40 | 07-30 10:25 | NULL | 45 | 45 | 0 |

Reading these three together shows the model's accuracy loop working:

- **`WO-2026-0341` is the platform's success case.** `work_type = 'predictive'`, triggered by a recommendation, and `reported_failure_category_id` equals `confirmed_failure_category_id` — the model predicted bearing degradation and the engineer found bearing degradation, with 0.04 mm radial play measured on removal. **This is a scored, confirmed correct prediction**, not a self-assessment.
- **The estimate held.** Planned 255 minutes against actual 255. The planned figure came from `machine_type_failure_mode.estimated_repair_duration_minutes` of 240 for `FC-BRG` on a `MTY-VMC-500`, plus the 15-minute retrieval time from `LOC-SP-B2`. That the master data estimates proved accurate is itself a finding worth reporting.
- **One stoppage did two jobs.** `SCH-0001` is populated, so the 500-hour preventive service — which §E2 showed was 37 running hours away — was performed concurrently. The machine stopped once instead of twice, and on closure its `operating_hours_at_last_maintenance` advanced to reflect it. This is rule 10 producing exactly the outcome it was written for.
- **`WO-2026-0344` is a sensor fault, correctly typed as `corrective`.** It resolves the `out_of_physical_range` readings on `MC-0301` from §1. Note it did not stop the line: a failed vibration sensor does not stop a lathe. Assigned to `MTM-AUTO` because master data gives `FC-SENS` a `required_specialization` of `automation`, despite the fault sounding mechanical — the sort of correct-but-counterintuitive routing that only explicit data produces.
- **`WO-2026-0338` is routine preventive work** with no reported or confirmed failure, because nothing failed. Nulls here are correct, not missing data, which is why rule 2 scopes the requirement to corrective, predictive, and emergency work only.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates and updates every row.** Sole writer. Generates scheduled and reactive jobs and advances them through the lifecycle |
| **Monitoring Agent** | Suppresses events for machines with an in-progress job. A machine being repaired is expected to behave abnormally, and alerting on it is noise |
| **Prediction Agent** | Uses `closed_at` and `confirmed_failure_category_id` as **training labels.** This entity is where supervised learning gets its ground truth |
| **Supervisor Agent** | **Computes maintenance due status** from closed records against schedules, and checks whether a job is already open before escalating something already being handled |
| **Decision Agent** | Reads `resolution_note` from similar past jobs as precedent, and checks for an existing open job so it never recommends work already underway |
| **Notification Service** | Reports the work order number so a recipient can track the job |
| **Dashboard** | Maintenance backlog, work in progress, and compliance against schedule |
| **Analytics** | **Prediction accuracy** (reported versus confirmed), **platform value** (predictive versus corrective job mix), MTTR against `machine_type.mttr_minutes`, and response time against team targets |

---

### E12. `machine_maintenance_activity`

**Purpose**

The append-only timeline of individual steps performed within a maintenance job. It is what makes the duration of a repair explicable rather than merely known.

**Business description**

A work record says a job took 255 minutes. This entity says where those minutes went.

That distinction has direct operational consequence. Two jobs both taking four hours are entirely different problems if one spent three hours waiting for an engineer and the other spent three hours on the repair itself. The first is a response-coverage problem; the second is a difficulty problem. Only a timeline separates them.

The activity log **decomposes the job into measurable intervals** that map onto commitments held in master data:

| Interval | From → to | Compared against |
|---|---|---|
| Response time | `opened_at` → `arrived` | `maintenance_team.target_response_time_minutes` |
| Diagnosis time | `arrived` → `diagnosis_complete` | No master target; a baseline built from history |
| Part retrieval | `part_requested` → `part_collected` | `inventory_location.average_retrieval_time_minutes` |
| Repair time | `repair_started` → `repair_complete` | `machine_type_failure_mode.estimated_repair_duration_minutes` |
| Verification | `test_run` → `handover` | No target; verification of the fix |

Every one of those comparisons is a claim the master data made and this entity tests. That is the entity's real justification: **master data states commitments, and this is where they are held to account.**

**Why append-only alongside a mutable parent.** The work record's status advances and its timestamps get filled in; the activity log never changes. That pairing — from §4.3 — means the job's current position is cheap to read while its history is immutable evidence. A disputed repair duration is settled by the activity log, not by the header.

**Note on scope.** This is not a task checklist and not a procedure document. It records **what happened and when**, not what should happen. Maintenance procedures are documentation, not data, and no consumer here reads them.

**Primary key**

`machine_maintenance_activity_id` — surrogate `INTEGER`. No business code: activities are child rows of a coded parent, and nobody names an individual step.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `maintenance_work_record_id` | INTEGER (FK op) | Yes | `WO-2026-0341` | References `maintenance_work_record` | The job this step belongs to |
| `activity_at` | DATETIME | Yes | `2026-07-30 06:38:00+05:30` | Not in the future; ≥ the job's `opened_at` | **Event time** of the step |
| `activity_type` | TEXT + CHECK | Yes | `part_collected` | One of: `dispatched`, `arrived`, `diagnosis_started`, `diagnosis_complete`, `part_requested`, `part_collected`, `repair_started`, `repair_complete`, `test_run`, `handover`, `escalated`, `on_hold`, `resumed` | **What happened.** The vocabulary is what makes interval measurement possible |
| `performed_by_worker_id` | INTEGER (FK master) | No | `EMP-1030` | References active `worker` when present | Who performed the step. NULL for system-recorded events. **Note the storekeeper, not the engineer, collected the part** |
| `duration_from_previous_seconds` | INTEGER | No | `960` | ≥ 0; NULL for the first activity | Time since the previous step in this job. **Makes interval analysis a read rather than a window function** |
| `notes` | TEXT | No | `Bearing 6205-2RS collected from LOC-SP-B2` | — | Step detail. NULL for self-evident steps |
| `shift_id` | INTEGER (FK master) | Yes | `SH-A` | References `shift` containing `activity_at` | Crew on duty. **A job spanning a shift change shows the handover here** |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `maintenance_work_record` | Operational | Many-to-one | The job |
| Parent | `worker` | Master | Many-to-one, optional | Who performed the step |
| Parent | `shift` | Master | Many-to-one | Crew on duty |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | Factory Simulator, as each step occurs |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Supervisor Agent (job progress), Decision Agent (precedent for duration estimates), Dashboard (job timeline), Analytics (interval performance) |
| **Archived by** | Platform retention job, alongside the parent job |
| **Immutable** | Yes |
| **Append-only** | Yes |
| **Expires** | Retained with the parent work record for 5 years, then archived |
| **Regenerable** | No. Source of truth for what was actually done, and when |

**Business rules**

1. Every activity belongs to exactly one work record.
2. `activity_at` must be on or after the job's `opened_at`.
3. `duration_from_previous_seconds` must equal the interval since the previous activity in the same job. NULL only for the first.
4. Activities within a job must be strictly time-ordered. Two at the same instant indicate a defect.
5. Expected sequence for a corrective or predictive job: `dispatched` → `arrived` → `diagnosis_started` → `diagnosis_complete` → optionally `part_requested` → `part_collected` → `repair_started` → `repair_complete` → `test_run` → `handover`. Deviations are permitted and informative; an absent `test_run` means the repair was never verified, which is worth reporting.
6. `part_requested` must be followed by `part_collected` before `repair_started`, unless the job entered `awaiting_parts`.
7. `part_collected` should have a corresponding `inventory_movement` of type `issue_maintenance` at approximately the same time. The two reconcile.
8. A job crossing a shift boundary should record a `handover` activity. An absent handover on a cross-shift job is a process gap.
9. Response time — `opened_at` to the `arrived` activity — is compared against the assigned team's `target_response_time_minutes`. This is the primary service-level measurement in the model.
10. Activities are never edited or deleted. A correction is a new activity with an explanatory note.

**Example records**

The full timeline of `WO-2026-0341`.

| activity_at | activity_type | performed_by | duration_from_prev_sec | shift | notes |
|---|---|---|---|---|---|
| 07-30 05:50:00 | `dispatched` | NULL | NULL | `SH-C` | Scheduled dispatch ahead of 06:00 shift change per REC-20260729-0031 |
| 07-30 06:00:00 | `arrived` | `EMP-1011` | 600 | `SH-A` | |
| 07-30 06:05:00 | `diagnosis_started` | `EMP-1011` | 300 | `SH-A` | Machine stopped, guarding removed |
| 07-30 06:20:00 | `diagnosis_complete` | `EMP-1011` | 900 | `SH-A` | Front spindle bearing confirmed: 0.04 mm radial play, audible roughness on rotation |
| 07-30 06:22:00 | `part_requested` | `EMP-1011` | 120 | `SH-A` | Bearing 6205-2RS and drive belt V-38 |
| 07-30 06:38:00 | `part_collected` | `EMP-1030` | 960 | `SH-A` | Collected from LOC-SP-B2 |
| 07-30 06:45:00 | `repair_started` | `EMP-1011` | 420 | `SH-A` | |
| 07-30 09:50:00 | `repair_complete` | `EMP-1011` | 11100 | `SH-A` | Bearing and belt replaced, spindle reassembled, 500h service items completed |
| 07-30 10:05:00 | `test_run` | `EMP-1011` | 900 | `SH-A` | 30-cycle no-load run. Vibration 1.9 mm/s, temperature stable at 56 °C |
| 07-30 10:20:00 | `handover` | `EMP-1011` | 900 | `SH-A` | Released to production. First-article inspection requested |

Every master data commitment is now measurable, and the results are informative:

| Measured interval | Actual | Master data reference | Verdict |
|---|---|---|---|
| Response — `opened_at` 19:15 to `arrived` 06:00 | 645 min | `MTM-MECH` target 30 min | **Deliberately deferred, not a breach.** The recommendation scheduled the work for shift change |
| Dispatch to arrival | 10 min | `MTM-MECH` target 30 min | **Comfortably met** once actually dispatched |
| Part retrieval — requested to collected | 16 min | `LOC-SP-B2` average 15 min | **Estimate held** |
| Repair — started to complete | 185 min | `FC-BRG` on `MTY-VMC-500` estimate 240 min | **Faster than estimated**, because the machine was already open |
| Verification | 15 min | No master target | Vibration returned to 1.9 mm/s against a 2.1 nominal |

Two observations that only the timeline makes possible:

- **The 645-minute response looks like a severe service-level breach and is not one.** The job was opened at 19:15 on 29 July and the engineer arrived at 06:00 on 30 July. Measured naively, `MTM-MECH` missed its 30-minute target by more than ten hours. In fact the recommendation deliberately scheduled the work for the shift change to avoid stopping a critical line mid-run, the supervisor accepted that plan, and dispatch-to-arrival was 10 minutes. **Without the `dispatched` activity, this correct decision would be indistinguishable from a failure to respond** — and the team's performance metrics would be wrong.
- **The test run is the proof the repair worked.** Vibration at 1.9 mm/s against a 2.1 nominal and a 4.5 healthy maximum, temperature stable at 56 °C against a 58 nominal. The machine is measurably healthier than its own baseline. That single row closes the loop the first vibration reading in §E1 opened.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Creates every row.** Sole writer. Generates realistic activity sequences with durations drawn from master data targets |
| **Monitoring Agent** | Does not read this entity |
| **Prediction Agent** | Does not read this entity directly. Uses the parent record's confirmed failure as a label |
| **Supervisor Agent** | Reads the latest activity to establish how far an in-progress job has got, which determines whether escalation would add anything |
| **Decision Agent** | Reads activity timings from similar past jobs as **precedent** — "this repair took 185 minutes last time" is a better estimate than a master data average |
| **Notification Service** | Reports current job progress in status updates |
| **Dashboard** | Renders the job timeline, which is how a supervisor tracks an active repair |
| **Analytics** | **Service-level reporting.** Response time against team targets, retrieval time against location estimates, repair time against failure mode estimates. The whole master data commitment set, held to account |

---

## Group D — Detection

The Monitoring Agent's output. Two entities with a deliberate and important split:

| | `operational_event` | `operational_alert` |
|---|---|---|
| **Is** | An immutable observation | A mutable managed case |
| **Answers** | What was detected, and on what evidence | Is it still a problem, and has anybody dealt with it |
| **Volume** | ~40 per day | A handful per day |
| **Lifecycle** | Created once, never changed | Advances through acknowledgement, escalation, resolution, closure |
| **Correlation** | Many events | One alert |

**Why the split matters.** A degrading bearing produces vibration breaches every few minutes for eleven hours — dozens of events. If each raised a notification, the recipient would mute the channel by the second hour, and the platform would have made things measurably worse. Correlating many events into one managed alert is what makes the difference between monitoring and noise.

The split is also what makes the event lifecycle in §10 possible: **facts cannot be revised, but cases must be.** Putting acknowledgement state on an event would require mutating evidence.

---

### E13. `operational_event`

**Purpose**

Records one detected condition as an immutable fact, with the evidence that produced it: the value observed, the limit breached, and the reading that triggered it. It is the evidentiary base of every recommendation.

**Business description**

An event is the Monitoring Agent saying *"this happened, and here is why I say so."*

It is deliberately narrow. An event is not a judgement about what to do, not a prediction, and not a message to anybody. It is an observation with its supporting numbers attached, and it never changes.

**Events come from five detection paths**, and having more than one is what makes detection robust:

| `event_category` | Detected from | Example |
|---|---|---|
| `machine_condition` | `machine_sensor_reading` against `alert_threshold_rule` | Vibration above warning limit for 30 seconds |
| `machine_output` | `cycle_history` and `production_progress` | Cycle time deviation rising; output below capability rate |
| `quality` | `quality_inspection_result` and `scrap_record` | Attributed failure rate rising on one machine |
| `inventory` | `inventory_movement` against master thresholds | Balance reached reorder point |
| `data_quality` | `machine_sensor_reading.quality_flag`, staleness | Sensor reporting outside physical range |

A single physical problem often produces events in three of these categories from three independent measurement paths — which is exactly what happened in the worked incident and why its root-cause hypothesis is defensible.

**The point-in-time threshold capture.** `threshold_value_breached` stores the limit that was actually in force when the event fired, and this is the **one permitted master value copy** in the entire model (§3.5). Threshold profiles are versioned and retuned; storing only `alert_threshold_rule_id` would mean that re-reading the rule six months later returns the current limit, not the one that fired. A recommendation citing *"4.74 mm/s against a warning limit of 4.70"* would silently become wrong the first time somebody retuned the profile. The rule is referenced for lineage; the value is captured for evidence.

**On the subject being one of four typed keys.** An event's subject varies by category — a machine, a line, a run, or an inventory item — so four nullable typed foreign keys are present with a rule governing which apply per category.

This is deliberately **not** a generic polymorphic reference. The master data document rejected a `scope_type` plus `scope_ref` pattern for `business_rule` (§34.11), and the reasoning holds: a generic reference cannot be enforced by the database. Four typed nullable foreign keys keep full referential integrity while still expressing a varying subject. The difference between the two approaches is integrity, and it is worth the extra columns.

**`operational_alert_id` is set at insert, not later.** The Monitoring Agent finds or creates the correlating alert **first**, then writes the event with the alert already known. That ordering is what allows the event to remain genuinely immutable — had the alert been attached afterwards, every event would need updating and the immutability guarantee would be fiction.

**Primary key**

`operational_event_id` — surrogate `INTEGER`, with `operational_event_code` unique.

A code is essential: events are cited as supporting evidence in recommendations and notifications, and the §16.5 explainability contract depends on that citation being followable by a human.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `operational_event_code` | VARCHAR(20) | Yes | `EVT-20260729-0412` | Unique; matches `^EVT-[0-9]{8}-[0-9]{4}$` | Event reference **cited as supporting evidence** in recommendations |
| `operational_alert_id` | INTEGER (FK op) | Yes | `ALR-20260729-0087` | References `operational_alert` | The correlated case. **Set at insert**, which is what keeps the event immutable |
| `event_category` | TEXT + CHECK | Yes | `machine_condition` | One of: `machine_condition`, `machine_output`, `quality`, `inventory`, `data_quality` | Detection path. Governs which subject keys apply |
| `event_type` | TEXT + CHECK | Yes | `threshold_warning` | One of: `threshold_warning`, `threshold_critical`, `rate_of_change_exceeded`, `sustained_deviation`, `output_shortfall`, `cycle_deviation`, `scrap_rate_exceeded`, `quality_failure_rate`, `reorder_point_reached`, `safety_stock_breached`, `sensor_out_of_range`, `telemetry_stale` | **Precisely what was detected.** The specific condition, not the domain |
| `detected_at` | DATETIME | Yes | `2026-07-29 18:42:00+05:30` | Not in the future | **Event time.** When the condition was confirmed, which is after sustained duration has elapsed |
| `severity_level_id` | INTEGER (FK master) | Yes | `SEV-2` | References active `failure_severity_level` | Severity, from the threshold rule's warning or critical mapping |
| `machine_id` | INTEGER (FK master) | No | `MC-0101` | References `machine`; required for `machine_condition`, `machine_output`, `data_quality` | Subject machine |
| `production_line_id` | INTEGER (FK master) | No | `LN-01` | References `production_line` when present | Subject line, for line-level output conditions |
| `production_run_id` | INTEGER (FK op) | No | `RUN-2026-0714` | References `production_run` when present | Run affected |
| `inventory_item_id` | INTEGER (FK master) | No | NULL | References `inventory_item`; required for `inventory` | Subject item |
| `machine_parameter_id` | INTEGER (FK master) | No | `PRM-VIB` | References `machine_parameter`; required for threshold and rate events | Parameter that breached |
| `alert_threshold_rule_id` | INTEGER (FK master) | No | rule for `ATP-VMC-TIGHT`/`PRM-VIB` | References `alert_threshold_rule` when present | **Lineage** — which rule fired |
| `observed_value` | NUMERIC(12,4) | No | `4.7400` | NULL only for events with no measured value | The measured value. **Evidence** |
| `threshold_value_breached` | NUMERIC(12,4) | No | `4.7000` | NULL when no threshold applies | **The limit in force at detection time.** The one permitted master value copy — §3.5 |
| `threshold_direction` | TEXT + CHECK | No | `above_high` | One of: `above_high`, `below_low`, `rate_exceeded` | Which way the breach went. Needed because parameters degrade in different directions |
| `sustained_duration_seconds` | INTEGER | No | `30` | ≥ 0 when present | How long the condition actually persisted before confirmation. **Distinguishes a transient from a real condition** |
| `triggering_reading_id` | INTEGER (FK op) | No | `4188201` | References `machine_sensor_reading` when present | The specific reading that confirmed it. **The deepest link in the evidence chain** |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `detected_at` | Crew on duty |
| `detection_note` | TEXT | No | `Vibration 4.74 mm/s sustained 30s above 4.70 warning limit (ATP-VMC-TIGHT); healthy maximum 4.50` | — | Human-readable summary. **Quoted directly in recommendations and notifications** |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `operational_alert` | Operational | Many-to-one | The correlated case |
| Parent | `machine`, `production_line`, `inventory_item`, `machine_parameter`, `alert_threshold_rule`, `failure_severity_level`, `shift` | Master | Many-to-one, mostly optional | Subject and lineage |
| Parent | `production_run`, `machine_sensor_reading` | Operational | Many-to-one, optional | Run affected and triggering reading |
| Referenced by | `machine_state_transition`, `quality_inspection_result`, `scrap_record`, `prediction_result`, `supervisor_context` | Operational | One-to-many | Downstream citation |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Monitoring Agent.** The only writer |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read by** the Simulator, which must never see the platform's own conclusions |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. **Absolutely.** An event is evidence; the explainability contract collapses if evidence can be rewritten |
| **Append-only** | Yes |
| **Expires** | Retained 1 year, then archived indefinitely. Events cited by a retained recommendation are exempt from purge |
| **Regenerable** | **Partially.** Re-running detection over retained telemetry would reproduce equivalent events, but not identical ones — threshold profiles may have been retuned since. Treated as non-regenerable in practice |

**Business rules**

1. Every event belongs to exactly one alert, assigned at insert. There are no orphan events.
2. Subject keys must match the category: `machine_condition`, `machine_output`, and `data_quality` require `machine_id`; `inventory` requires `inventory_item_id`; `quality` requires `machine_id` and `production_run_id`.
3. Threshold and rate events require `machine_parameter_id`, `alert_threshold_rule_id`, `observed_value`, `threshold_value_breached`, and `threshold_direction`. An event claiming a breach without stating what was breached is not evidence.
4. `threshold_value_breached` is captured at detection and **never** re-read from master data afterwards. §3.5.
5. `sustained_duration_seconds` must be at least the rule's configured `sustained_duration_seconds`. An event confirmed early is a defect.
6. Events are **not** raised from readings whose `quality_flag` is not `valid`, with one exception: a `sensor_out_of_range` event of category `data_quality` is raised precisely *because* the reading was invalid. That inversion is deliberate — it is how instrument failure gets detected rather than silently ignored.
7. Events are suppressed for machines in `setup`, `down_planned`, or with an in-progress work record. Abnormal behavior during a repair is expected, not newsworthy.
8. `detection_note` is mandatory in practice for any event that will be cited, and must state the observed value, the limit, and the unit. It is quoted verbatim to humans, so its quality is functional rather than cosmetic.
9. Events are **never** edited or deleted. A false positive is resolved on the alert, leaving the event intact — because the event correctly records what was observed even when the conclusion drawn from it was wrong.
10. The Monitoring Agent is the sole writer. No other component may create an event, because provenance of detection must be unambiguous.

**Example records**

Events across the incident, spanning four of the five detection paths.

| code | alert | category | event_type | detected_at | severity | machine | parameter | observed | threshold | direction | sustained_s |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `EVT-20260729-0412` | `ALR-20260729-0087` | `machine_condition` | `threshold_warning` | 07-29 18:42:00 | `SEV-2` | `MC-0101` | `PRM-VIB` | 4.7400 | 4.7000 | `above_high` | 30 |
| `EVT-20260729-0413` | `ALR-20260729-0087` | `machine_condition` | `threshold_warning` | 07-29 18:42:00 | `SEV-2` | `MC-0101` | `PRM-TEMP` | 73.9000 | 73.5000 | `above_high` | 30 |
| `EVT-20260729-0414` | `ALR-20260729-0087` | `machine_condition` | `rate_of_change_exceeded` | 07-29 18:42:10 | `SEV-2` | `MC-0101` | `PRM-VIB` | 0.3000 | 0.3000 | `rate_exceeded` | 60 |
| `EVT-20260729-0417` | `ALR-20260729-0087` | `machine_output` | `cycle_deviation` | 07-29 18:48:00 | `SEV-3` | `MC-0101` | NULL | 3.38 | 2.00 | `above_high` | 900 |
| `EVT-20260729-0419` | `ALR-20260729-0089` | `quality` | `quality_failure_rate` | 07-29 18:53:00 | `SEV-3` | `MC-0101` | NULL | 20.00 | 10.00 | `above_high` | NULL |
| `EVT-20260729-0421` | `ALR-20260729-0091` | `data_quality` | `sensor_out_of_range` | 07-29 18:42:20 | `SEV-3` | `MC-0301` | `PRM-VIB` | 12.4000 | 50.0000 | `above_high` | 20 |
| `EVT-20260730-0088` | `ALR-20260730-0012` | `inventory` | `reorder_point_reached` | 07-30 06:38:00 | `SEV-4` | NULL | NULL | 8.0000 | 8.0000 | `below_low` | NULL |

What this set demonstrates:

- **Four independent detection paths agree on one physical problem.** Vibration threshold (sensor), temperature threshold (sensor, different domain), rate of change (derivative), cycle deviation (production timing), and quality failure rate (dimensional measurement). Five events, four measurement mechanisms, one bearing. **This is why the Decision Agent's root cause in §E18 is a hypothesis with corroboration rather than a guess** — and it is only possible because detection was not built as a single threshold checker.
- **The rate event fires at exactly the limit.** Vibration rising 0.05 mm/s per 10 seconds is 0.30 per minute, against the `ATP-VMC-TIGHT` rate limit of 0.3000. A static-threshold-only monitor would have caught the breach; the rate limit caught the *trajectory*, which is what turns "it is high" into "it is getting worse fast."
- **Correlation produced three alerts, not seven events' worth of noise.** All four machine-condition and output events on `MC-0101` correlated into `ALR-20260729-0087`. The quality events formed a separate alert because they are a different category. The sensor fault on `MC-0301` is an entirely unrelated case. Seven events, three managed cases.
- **`EVT-20260730-0088` is the consequence of the fix.** Issuing the bearing dropped stock to its reorder point, and the model detected that from the same movement row that recorded the repair. `SEV-4` is correct: a replenishment signal is not an emergency.
- **`EVT-20260729-0421` inverts the usual rule.** It exists *because* a reading was invalid. Rule 6's exception is what makes instrument failure visible instead of silently discarded — and it produced `WO-2026-0344` in §11.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write.** A strict boundary: the simulator generates reality, never the platform's interpretation of it |
| **Monitoring Agent** | **Creates every row.** Sole writer |
| **Prediction Agent** | Recent event counts and severities per machine are features. Frequent events are themselves a degradation signal |
| **Supervisor Agent** | Reads events on the triggering alert to assemble evidence, and tests severity against the `BR-ESC-SEV` escalation floor |
| **Decision Agent** | **The supporting evidence element** of the §16.5 contract resolves to these rows. `detection_note` and the observed-against-threshold pair are quoted directly |
| **Notification Service** | Quotes `detection_note` so a recipient can verify the claim at the machine |
| **Dashboard** | Event feed per machine and line, and threshold breach markers on charts |
| **Analytics** | Event frequency by machine, parameter, and type. **False positive rate by threshold profile**, which is what drives the tuning cycle |

---

### E14. `operational_alert`

**Purpose**

The managed case that correlates related events and carries their human lifecycle: acknowledgement, escalation, resolution, and closure. It is what converts a stream of detections into a bounded number of things a person is asked to deal with.

**Business description**

An alert is the answer to *"is this still a problem, and has anybody dealt with it?"*

Its central function is **correlation**. The bearing degradation produced 34 events over eleven hours as vibration flapped above and below its warning limit. Every one is a legitimate observation, and none should have been a separate notification. `correlation_key` is the deterministic rule that groups them: **machine plus event category**, so all machine-condition events on one machine within an open window belong to one case.

That single mechanism is the difference between a platform that reduces cognitive load and one that adds to it.

**The lifecycle carries facts that exist nowhere else.** Who acknowledged it, when, and how it was ultimately resolved are human decisions. They cannot be recomputed from telemetry, which is why this is one of only three mutable, non-recomputable entities in the model.

**`resolution_type` is where the platform's honesty lives.** An alert closed as `false_positive` is a permanent record that the platform raised something that did not matter. Analytics aggregates those by threshold profile, and that aggregation drives the tuning cycle described in master data §27. **A monitoring system that cannot count its own false positives cannot be improved**, and most do not record them because recording them is uncomfortable.

**`current_severity_level_id` versus `initial_severity_level_id`.** A case can worsen. An alert opened at `SEV-2` on a warning breach escalates to `SEV-1` if a critical limit is later crossed. Keeping both means the escalation path is visible rather than overwritten, and it lets Analytics distinguish "opened critical" from "deteriorated to critical" — which are different failures of prediction.

**`suppression_reason` records deliberate silence.** An alert opened while a work order is already in progress is suppressed rather than notified, and the reason is recorded. Silent suppression would be indistinguishable from a delivery failure.

**Note on the denormalised subject keys.** The alert carries `machine_id`, `production_line_id`, and `inventory_item_id`, duplicating the subject of its events. This is a **declared denormalisation** on the same footing as `machine_sensor_reading.machine_state_at_reading`: the dashboard queries open alerts by machine on every refresh, and resolving the subject through the event table each time would add a join to the platform's most frequent read. Business rule 3 requires the alert's subject to match its events.

**Primary key**

`operational_alert_id` — surrogate `INTEGER`, with `operational_alert_code` unique.

A code is essential: alerts are acknowledged, discussed, and referenced by humans.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `operational_alert_code` | VARCHAR(20) | Yes | `ALR-20260729-0087` | Unique; matches `^ALR-[0-9]{8}-[0-9]{4}$` | Alert reference used by humans |
| `correlation_key` | VARCHAR(120) | Yes | `MC-0101\|machine_condition` | Non-empty; unique among **open** alerts | **The deduplication rule.** Deterministic, so correlation is reproducible rather than heuristic |
| `alert_category` | TEXT + CHECK | Yes | `machine_condition` | Same vocabulary as `operational_event.event_category` | Category of the correlated events |
| `machine_id` | INTEGER (FK master) | No | `MC-0101` | References `machine`; must match the events | Subject machine. **Declared denormalisation** for dashboard read performance |
| `production_line_id` | INTEGER (FK master) | No | `LN-01` | References `production_line` when present | Subject or affected line |
| `inventory_item_id` | INTEGER (FK master) | No | NULL | References `inventory_item` when present | Subject item for inventory alerts |
| `initial_severity_level_id` | INTEGER (FK master) | Yes | `SEV-2` | References active `failure_severity_level` | Severity when the alert opened. **Never changes** |
| `current_severity_level_id` | INTEGER (FK master) | Yes | `SEV-2` | References active `failure_severity_level` | Severity now. **Escalates as events worsen**, so the deterioration path stays visible |
| `alert_status` | TEXT + CHECK | Yes | `closed` | One of: `open`, `acknowledged`, `escalated`, `resolved`, `closed`, `suppressed` | **Lifecycle position.** §10 |
| `event_count` | INTEGER | Yes | `34` | ≥ 1 | Correlated events. **Maintained total**, reconcilable against the event table. Directly measures the noise the correlation absorbed |
| `opened_at` | DATETIME | Yes | `2026-07-29 18:42:00+05:30` | Not in the future | When the case opened, at the first event |
| `first_event_at` | DATETIME | Yes | `2026-07-29 18:42:00+05:30` | = `opened_at` | Earliest correlated event |
| `last_event_at` | DATETIME | Yes | `2026-07-30 05:40:00+05:30` | ≥ `first_event_at` | Most recent correlated event. **Staleness signal** — an alert with no recent events may have self-recovered |
| `acknowledged_at` | DATETIME | No | `2026-07-29 18:51:00+05:30` | ≥ `opened_at` | When a human confirmed receipt. NULL while unacknowledged |
| `acknowledged_by_worker_id` | INTEGER (FK master) | No | `EMP-1002` | References active `worker` when present | Who acknowledged. **The human-in-the-loop audit trail** |
| `escalated_at` | DATETIME | No | NULL | ≥ `opened_at` when present | When it escalated for lack of acknowledgement. NULL if acknowledged in time |
| `resolved_at` | DATETIME | No | `2026-07-30 10:20:00+05:30` | ≥ `opened_at` | When the underlying condition ceased |
| `resolution_type` | TEXT + CHECK | No | `maintenance_performed` | One of: `auto_recovered`, `maintenance_performed`, `false_positive`, `superseded`, `manual_close`; required when `resolved` or `closed` | **How it ended.** `false_positive` is the honesty mechanism that drives threshold tuning |
| *(no resolving-job reference)* | — | — | — | — | **Deliberately absent to keep the graph acyclic.** `maintenance_work_record.triggering_alert_id` points from the job to the alert; a back-reference here would create a cycle. The resolving job is derived — see rule 8 |
| `closed_at` | DATETIME | No | `2026-07-30 10:35:00+05:30` | ≥ `resolved_at` | When the case was closed |
| `suppression_reason` | TEXT + CHECK | No | NULL | One of: `maintenance_in_progress`, `machine_offline`, `planned_downtime`, `duplicate_condition`, `rate_limited`; required when `suppressed` | **Why the platform deliberately stayed silent.** Distinguishes intent from failure |
| `resolution_note` | TEXT | No | `Resolved by bearing replacement under WO-2026-0341. Post-repair vibration 1.9 mm/s.` | Required when `closed` | How the case ended, in plain language |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine`, `production_line`, `inventory_item`, `failure_severity_level` ×2, `worker` | Master | Many-to-one, mostly optional | Subject, severity, acknowledger |
| Child | `operational_event` | Operational | **One-to-many** | The correlated observations |
| Referenced by | `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `maintenance_work_record`, `notification` | Operational | One-to-many | Downstream consumption |

**This entity has no outgoing operational foreign keys.** That is deliberate: it makes `operational_alert` a root of the operational dependency graph and is what keeps the graph acyclic. Everything downstream points **at** the alert; the alert points at nothing operational.

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Monitoring Agent**, when an event arrives with no matching open `correlation_key` |
| **Updated by** | **Monitoring Agent** for severity, counts, and event times. Acknowledgement is recorded through the Dashboard but written by the Monitoring Agent, so the entity retains a single writer |
| **Read by** | All components except the Simulator |
| **Archived by** | Platform retention job |
| **Immutable** | No. **Lifecycle entity** |
| **Append-only** | No |
| **Expires** | Retained 2 years, then archived indefinitely. Alerts referenced by a retained recommendation are exempt |
| **Regenerable** | No. Carries human acknowledgement and resolution decisions |

**State machine**

```
                    ┌──────────► suppressed ──────► closed
                    │
open ──► acknowledged ──► resolved ──► closed
  │                          ▲
  └──► escalated ────────────┘
```

| Transition | Trigger |
|---|---|
| — → `open` | First event with an unmatched `correlation_key` |
| `open` → `suppressed` | Maintenance in progress, machine offline, or recipient rate limit reached; `suppression_reason` set |
| `open` → `acknowledged` | A human confirms receipt within `failure_severity_level.max_acknowledgement_minutes` |
| `open` → `escalated` | Acknowledgement window elapsed; next recipient in `escalation_order` notified |
| `escalated` → `acknowledged` | A human eventually confirms |
| `acknowledged` / `escalated` → `resolved` | Condition ceases, or maintenance completes; `resolution_type` set |
| `resolved` → `closed` | Case signed off; `resolution_note` required |

**Business rules**

1. **`correlation_key` is unique among open alerts.** This is the mechanism that prevents alert storms: an event matching an open key joins that alert rather than creating another.
2. `correlation_key` is composed deterministically from subject and category, never heuristically. Reproducible correlation is what makes the dedup behavior explainable.
3. The alert's subject keys must match those of its events.
4. `current_severity_level_id` may only become **more** severe over an alert's life. A worsening case escalates; it does not quietly de-escalate, because de-escalation would hide deterioration.
5. `event_count` is a maintained total, reconciled against the event table. Divergence is a data quality incident.
6. Acknowledgement requires a worker whose role has the authority the severity implies. A `SEV-1` alert whose severity `requires_line_stop` must be acknowledged by somebody with `can_authorize_line_stop`.
7. Escalation fires when `acknowledged_at` is still NULL after `failure_severity_level.max_acknowledgement_minutes`. The window comes from master data, never from a constant.
8. `resolution_type = 'maintenance_performed'` requires that a **closed `maintenance_work_record` reference this alert** through its `triggering_alert_id`. The resolving job is derived by that query rather than stored as a back-reference here, because a mutual reference between the two entities would be the model's only circular dependency. This is the same pattern master data used for leadership (§30.4 of the master document): **the reference lives on the child that knows its cause, never as a back-pointer on the parent.** The rare case of an alert resolved incidentally by a job not raised against it is accepted as unlinked, which is a small and honest loss next to a cycle.
9. `resolution_type = 'false_positive'` requires a `resolution_note` explaining why. **These records drive threshold tuning**, and an unexplained false positive teaches nothing.
10. `suppressed` requires a `suppression_reason`. Suppressed alerts remain fully visible on the dashboard — suppression stops notification, never recording.
11. An alert with no new events for a defined window may auto-resolve as `auto_recovered`. The window is a `business_rule` value, not a constant.
12. Alerts are never deleted. A mistaken alert is closed as `false_positive`, preserving the record.

**Example records**

The resolving job column is absent by design — it is derived from work records referencing each alert, per rule 8.

| code | correlation_key | category | machine | line | init_sev | curr_sev | status | events | opened_at | last_event_at | ack_at | ack_by | resolved_at | resolution_type |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `ALR-20260729-0087` | `MC-0101\|machine_condition` | `machine_condition` | `MC-0101` | `LN-01` | `SEV-2` | `SEV-2` | `closed` | 34 | 07-29 18:42 | 07-30 05:40 | 07-29 18:51 | `EMP-1002` | 07-30 10:20 | `maintenance_performed` |
| `ALR-20260729-0089` | `MC-0101\|quality` | `quality` | `MC-0101` | `LN-01` | `SEV-3` | `SEV-3` | `closed` | 4 | 07-29 18:53 | 07-30 04:10 | 07-29 19:02 | `EMP-1002` | 07-30 10:20 | `maintenance_performed` | `WO-2026-0341` |
| `ALR-20260729-0091` | `MC-0301\|data_quality` | `data_quality` | `MC-0301` | `LN-03` | `SEV-3` | `SEV-3` | `closed` | 118 | 07-29 18:42 | 07-30 09:40 | 07-30 08:15 | `EMP-1005` | 07-30 10:25 | `maintenance_performed` | `WO-2026-0344` |
| `ALR-20260730-0012` | `INV-CP-BRG-6205\|inventory` | `inventory` | NULL | NULL | `SEV-4` | `SEV-4` | `resolved` | 1 | 07-30 06:38 | 07-30 06:38 | NULL | NULL | 07-30 15:10 | `auto_recovered` | NULL |
| `ALR-20260728-0055` | `MC-0202\|machine_condition` | `machine_condition` | `MC-0202` | `LN-02` | `SEV-3` | `SEV-3` | `closed` | 6 | 07-28 11:20 | 07-28 11:44 | 07-28 11:31 | `EMP-1004` | 07-28 12:00 | `false_positive` | NULL |

The correlation numbers are the headline:

- **`ALR-20260729-0087` absorbed 34 events into one case.** Eleven hours of vibration flapping above and below 4.70 mm/s, and the supervisor was asked to deal with it **once**. Acknowledged in nine minutes, well inside the 30-minute `SEV-2` window from master data. Without correlation this would have been 34 notifications to one person overnight, and the channel would have been muted by the fifth.
- **`ALR-20260729-0091` absorbed 118 events** — a failed sensor reporting continuously. It was acknowledged only at 08:15 the next morning, because `SEV-3` reaches the line supervisor rather than the night response team. That is the correct outcome: a broken vibration sensor on a lathe is not worth waking anybody for, and the severity scale delivered that judgement automatically.
- **Two alerts, one work order.** Both `ALR-20260729-0087` and `ALR-20260729-0089` were resolved by `WO-2026-0341`. Condition and quality correlate separately by design — different categories — but converge on one physical fix. The Supervisor Agent's context in §E17 references both, which is how the platform reasons across correlated cases without collapsing them.
- **`ALR-20260728-0055` is recorded as a false positive**, and the record is permanent. A `SEV-3` vibration condition on the conveyor that resolved itself in 24 minutes with no intervention. `ATP-CONV-STD` is a `relaxed` profile, so this warrants review of whether even that is too tight. **This row is the platform admitting it was wrong**, and Analytics aggregating such rows by profile is what makes the tuning cycle evidence-based rather than a matter of opinion.
- **`ALR-20260730-0012` auto-recovered** when the receipt at 15:10 restored stock above the reorder point. Never acknowledged, because nobody needed to act — it resolved itself. Correct handling of a condition that fixed itself.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write** |
| **Monitoring Agent** | **Creates and updates every row.** Sole writer. Correlates, escalates, resolves |
| **Prediction Agent** | Open alert count and severity per machine are features. A machine with a long-open condition is at higher risk |
| **Supervisor Agent** | **The escalation entry point.** Reads open alerts, tests severity against `BR-ESC-SEV`, and pairs them with predictions |
| **Decision Agent** | Reads the alert as the case being reasoned about, and its events as evidence |
| **Notification Service** | **Notifies per alert, not per event.** This is what makes graduated escalation and rate limiting workable |
| **Dashboard** | **Primary operator surface.** Open alerts by machine, line, and severity — one query, and the reason `machine_id` is denormalised here |
| **Analytics** | **False positive rate by threshold profile** (the tuning input), acknowledgement time against severity targets, mean time to resolve |

---

## Group E — Prediction

The Prediction Agent's output, split into the input and the answer:

| | `prediction_feature_snapshot` | `prediction_result` |
|---|---|---|
| **Is** | The exact feature vector fed to the model | The model's output |
| **Purpose** | Reproducibility and explainability | The quantified risk |
| **Retention** | 180 days | 2 years |

**Why two entities rather than one.** Four reasons, and the first is the decisive one:

1. **Reproducibility.** Given a retained snapshot and a model version, a prediction must be exactly reproducible. That is the ML audit contract, and it is impossible if the input was never stored.
2. **One snapshot can serve several predictions** — different models, different horizons, or a re-scoring after a model upgrade. Storing the vector once avoids duplicating it per prediction.
3. **Retention differs.** Results are cited by recommendations and kept for years. Feature vectors are bulky and lose value quickly once the prediction they produced has been validated.
4. **Data sufficiency is a property of the input, not the output.** A snapshot can be *insufficient* for inference, in which case no prediction is produced at all — and that non-event still needs recording, because silence must be explicable.

This group also holds the platform's most important honesty constraint. `prediction_result.failure_probability` is the **ML confidence** that `PROJECT_OVERVIEW.md` §16.5 requires in every recommendation, and that the Decision Agent must carry forward unchanged. The number originates here and nowhere else.

---

### E15. `prediction_feature_snapshot`

**Purpose**

Captures the exact feature vector used for one inference, together with the data quality of the window it was computed from. It is the ML reproducibility contract and the deepest layer of prediction explainability.

**Business description**

Before the model can score a machine, raw telemetry must become features: not *"vibration is 4.8"* but *"vibration has risen 0.31 mm/s per hour over four hours, is 6.7 % above its healthy maximum, and has spent 780 seconds above the warning limit."* Those are the quantities a model can learn from, and this entity is where they are recorded.

Storing them is not optional bookkeeping. It is what makes three things possible:

**Reproducibility.** *"Re-run the model on this exact input and confirm it returns 0.68."* Without the stored vector, a prediction can never be verified, only trusted.

**Explainability at depth.** `prediction_result.top_contributing_features` names which features drove the score. Those names are only meaningful if the values behind them survive.

**Model comparison.** When the model is retrained, historical snapshots can be re-scored to compare versions on identical inputs. Without them, comparing model versions means waiting months for new data.

**The feature vector is a document, and its structure is specified.** §3.6 explains why: the vector is written once and read whole, never queried feature by feature, and its shape varies by machine type because `machine_type_parameter.is_ml_feature` determines which parameters participate. Expected structure:

```
Per-parameter block, one per parameter where is_ml_feature = 1:
    latest_value, window_mean, window_min, window_max, window_stddev,
    slope_per_hour, pct_above_normal_max, seconds_above_warning_limit

Machine-level block:
    accumulated_operating_hours, hours_since_last_maintenance,
    cycles_since_last_maintenance, pct_of_design_life, pct_of_mtbf_elapsed,
    mean_cycle_deviation_pct, cycle_deviation_slope,
    event_count_24h, attributed_scrap_count_24h, age_days
```

The **machine-level block is what makes the model more than a threshold checker.** A parameter drifting on a machine 462 hours into a 500-hour maintenance interval and 78 % through its MTBF is a materially different proposition from the same drift on a freshly serviced machine, and none of that context is in the telemetry.

**`data_completeness_pct` and `is_sufficient_for_inference` are the quality gate.** Master data (§11, §28) is explicit that readings outside physical bounds must never reach the model. This entity enforces it: invalid readings are counted in `excluded_reading_count`, completeness is computed against expected sampling, and if completeness falls below threshold the snapshot is marked insufficient and **no prediction is produced.**

That is the correct behavior. A model scoring a machine on 40 % of its expected data will return a number, and the number will be meaningless. Recording the insufficient snapshot rather than silently skipping is what makes the absence of a prediction explicable — *"MC-0301 was not scored because its vibration sensor was faulty"* is a real answer.

**Primary key**

`prediction_feature_snapshot_id` — surrogate `INTEGER`, with `prediction_feature_snapshot_code` unique.

A code is warranted: snapshots are cited in model audit and reproducibility discussion, and a human needs to be able to point at one.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `prediction_feature_snapshot_code` | VARCHAR(22) | Yes | `FSN-20260729-04188` | Unique; matches `^FSN-[0-9]{8}-[0-9]{5}$` | Snapshot reference, cited in model audit |
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine` with `is_monitored = 1` | The machine scored |
| `generated_at` | DATETIME | Yes | `2026-07-29 18:45:00+05:30` | Not in the future | **Event time** of feature computation |
| `window_from` | DATETIME | Yes | `2026-07-29 14:45:00+05:30` | < `window_to` | Start of the lookback window |
| `window_to` | DATETIME | Yes | `2026-07-29 18:45:00+05:30` | = `generated_at` | End of the window |
| `lookback_window_seconds` | INTEGER | Yes | `14400` | > 0 | Window length. **Stored explicitly** so a snapshot is interpretable without recomputing the interval |
| `feature_set_version` | VARCHAR(20) | Yes | `fs-v2.1` | Non-empty | Which feature definition produced this vector. **Essential for reproducibility** — features change over a project's life, and a vector is only meaningful against its definition |
| `feature_values` | JSON in TEXT | Yes | see below | Must conform to `feature_set_version` | **The feature vector.** Structure specified above |
| `source_reading_count` | INTEGER | Yes | `1440` | ≥ 0 | Valid readings used |
| `excluded_reading_count` | INTEGER | Yes | `2` | ≥ 0 | Readings dropped for quality. **Non-zero is a signal worth reading** |
| `data_completeness_pct` | NUMERIC(5,2) | Yes | `99.86` | 0–100 | Valid readings against expected count for the window. **The quality gate** |
| `is_sufficient_for_inference` | INTEGER | Yes | `1` | — | Whether the model may be run. **`0` means no prediction is produced** |
| `insufficiency_reason` | TEXT + CHECK | No | NULL | One of: `completeness_below_threshold`, `sensor_fault`, `machine_not_running`, `window_spans_maintenance`, `insufficient_history`; required when insufficient | Why inference was skipped. **Makes silence explicable** |
| `triggering_alert_id` | INTEGER (FK op) | No | `ALR-20260729-0087` | References `operational_alert` when present | Alert that prompted an off-schedule snapshot. NULL for routine scheduled generation |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `generated_at` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine`, `shift` | Master | Many-to-one | Machine scored, crew on duty |
| Parent | `operational_alert` | Operational | Many-to-one, optional | Prompting alert |
| Child | `prediction_result` | Operational | **One-to-many** | Predictions produced from this vector |
| Derived from | `machine_sensor_reading`, `cycle_history`, `machine_operational_status`, `operational_event`, `scrap_record` | Operational | Aggregation | Source data |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Prediction Agent.** On a fixed schedule per monitored machine, and additionally on alert |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Prediction Agent (inference), Decision Agent (feature values behind cited contributions), Analytics (model audit, re-scoring). **Not read by** the Monitoring Agent, Notification Service, or Dashboard |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A feature vector is the input to a specific inference. Changing it would break reproducibility |
| **Append-only** | Yes |
| **Expires** | Retained 180 days. Snapshots referenced by a recommendation that is still retained are exempt — the evidence chain must not break |
| **Regenerable** | **Yes, within the telemetry retention window.** Recomputable from readings and cycles while those survive. After raw telemetry is purged at 90 days, it becomes permanently non-regenerable — which is why its retention deliberately exceeds telemetry's |

**Business rules**

1. Snapshots are generated only for machines that are `is_monitored`, `in_service`, and whose category has `requires_condition_monitoring = 1`.
2. **Only readings with `quality_flag = 'valid'` contribute.** Everything else is counted in `excluded_reading_count`. This is the master data rule from §11 and §28 enforced in practice.
3. `feature_values` must contain a block for every parameter where `machine_type_parameter.is_ml_feature = 1` for that machine's type. A missing feature is an incomplete vector, not a sparse one.
4. `data_completeness_pct` is computed against the expected reading count derived from `machine_type_parameter.sampling_interval_seconds` and the window length. It is not estimated.
5. `is_sufficient_for_inference = 0` requires an `insufficiency_reason`, and **no `prediction_result` may reference an insufficient snapshot.**
6. A completeness threshold below which snapshots are insufficient comes from `business_rule`, not from a constant.
7. Windows spanning a maintenance intervention are marked `window_spans_maintenance` and treated as insufficient. A repair resets the machine's condition, so features spanning it mix two different machines.
8. `feature_set_version` must be recorded on every snapshot. A vector without its definition is uninterpretable a year later.
9. Snapshots are never edited. A recomputation is a new snapshot with a new code.
10. The Prediction Agent is the sole writer.

**Example records**

| code | machine | generated_at | window | lookback_s | feature_set | readings | excluded | completeness | sufficient | insufficiency | triggering_alert |
|---|---|---|---|---|---|---|---|---|---|---|---|
| `FSN-20260729-04188` | `MC-0101` | 07-29 18:45 | 14:45→18:45 | 14400 | `fs-v2.1` | 1440 | 2 | 99.86 | 1 | NULL | `ALR-20260729-0087` |
| `FSN-20260729-04191` | `MC-0301` | 07-29 18:45 | 14:45→18:45 | 14400 | `fs-v2.1` | 612 | 828 | 42.50 | **0** | `sensor_fault` | `ALR-20260729-0091` |
| `FSN-20260729-04205` | `MC-0201` | 07-29 19:00 | 15:00→19:00 | 14400 | `fs-v2.1` | 1436 | 0 | 100.00 | 1 | NULL | NULL |
| `FSN-20260730-00412` | `MC-0101` | 07-30 11:00 | 07:00→11:00 | 14400 | `fs-v2.1` | 340 | 0 | 23.61 | **0** | `window_spans_maintenance` | NULL |

The `feature_values` document for `FSN-20260729-04188`, abbreviated to the parameters that mattered:

```
parameters:
  PRM-VIB:   latest 4.80  mean 4.31  min 3.94  max 4.83  stddev 0.24
             slope_per_hour 0.31   pct_above_normal_max 6.67
             seconds_above_warning_limit 780
  PRM-TEMP:  latest 74.20 mean 71.05 min 67.80 max 74.20 stddev 1.71
             slope_per_hour 0.28   pct_above_normal_max 3.06
             seconds_above_warning_limit 420
  PRM-TORQ:  latest 71.50 mean 68.90 min 64.10 max 72.30 stddev 1.92
             slope_per_hour 0.19   pct_above_normal_max 0.00
             seconds_above_warning_limit 0
  PRM-TWEAR: latest 62.40 mean 48.20 min 30.10 max 62.40 stddev 9.85
             slope_per_hour 7.10   pct_above_normal_max 0.00
             seconds_above_warning_limit 0
machine:
  accumulated_operating_hours 11482.50   hours_since_last_maintenance 462.50
  cycles_since_last_maintenance 24040    pct_of_design_life 19.14
  pct_of_mtbf_elapsed 11.01              mean_cycle_deviation_pct 2.71
  cycle_deviation_slope 0.42             event_count_24h 12
  attributed_scrap_count_24h 1           age_days 2613
```

Three points worth drawing out:

- **`FSN-20260729-04191` is the design working correctly.** `MC-0301`'s faulty vibration sensor produced 828 invalid readings out of 1,440, giving 42.5 % completeness. The snapshot is marked insufficient with reason `sensor_fault`, and **no prediction was produced.** The alternative — scoring the machine on 42 % of its data — would have returned a confident number derived largely from a broken instrument. The record of the skip is what lets the platform answer *"why was MC-0301 never scored that evening?"*
- **`FSN-20260730-00412` shows the maintenance-window rule.** Computed at 11:00 on 30 July, its window spans the 06:05–10:35 repair. Features mixing pre-repair and post-repair condition would describe a machine that does not exist. Marked insufficient, and normal scoring resumes once a clean window is available.
- **The machine-level block carries the context the telemetry cannot.** `hours_since_last_maintenance` of 462.50 against a 500-hour interval, `pct_of_mtbf_elapsed` of 11.01, `mean_cycle_deviation_pct` of 2.71, `attributed_scrap_count_24h` of 1. **Four facts from four different entities, none of them a sensor reading**, and together they are what turns a vibration slope into a risk assessment.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write.** Strict boundary |
| **Monitoring Agent** | **Does not read.** Detection is threshold-based against current readings, not feature-based |
| **Prediction Agent** | **Creates every row** and reads it back as model input. Sole writer |
| **Supervisor Agent** | Reads `data_completeness_pct` to judge how much weight to place on a prediction. A 99 % snapshot and a 71 % snapshot do not deserve equal trust |
| **Decision Agent** | Reads the feature values behind the contributions cited in the prediction, so evidence statements are grounded in actual numbers |
| **Notification Service** | Does not read this entity |
| **Dashboard** | Does not read this entity. Feature vectors are not an operator surface |
| **Analytics** | **Model audit and re-scoring.** Reproducing historical predictions, comparing model versions on identical inputs, and monitoring feature drift |

---

### E16. `prediction_result`

**Purpose**

Records one model inference: the failure probability, the risk classification, the predicted failure mode, and which features drove the score. It is the platform's quantitative risk assessment and the sole origin of the ML confidence figure.

**Business description**

A prediction result is the model's answer. It is immutable, it is reproducible from its snapshot, and it is the **only** place in the platform where a failure probability is created.

That last point is a hard architectural rule inherited from `PROJECT_OVERVIEW.md` §16.5. The Decision Agent must include ML confidence in every recommendation, and it must **carry the number forward unchanged** rather than forming its own estimate. LLMs are poor numerical risk estimators, and if the Decision Agent were permitted to restate the probability as its own judgement, calibration would be quietly lost — the platform would report confident-sounding numbers with no basis. Confining probability creation to this entity is the structural enforcement of that rule.

**`predicted_failure_category_id` and `machine_type_failure_mode_id` together bound the prediction.** Master data §25 declares which failure modes are plausible on which machine type, which telemetry signature precedes each, and — critically — which are `is_model_predictable`. The model may only predict a mode declared predictable for that machine's type. That constraint prevents the model from claiming to forecast something no signal precedes, such as a sudden control board failure.

Referencing the **failure mode** rather than only the category also gives the prediction its own consistency check. The mode carries `typical_warning_period_hours`, and a prediction horizon should fall inside it. Master data gives `FC-BRG` on a `MTY-VMC-500` a 168-hour warning period, so a 72-hour horizon is plausible; a 400-hour horizon would not be.

**`risk_severity_level_id` maps probability onto the platform-wide severity scale.** Every other component speaks in severities — thresholds, recipients, escalation floors. Translating probability into that shared vocabulary is what lets a prediction participate in the same escalation and notification machinery as a threshold breach, rather than needing a parallel path. The mapping thresholds are `business_rule` values.

**`top_contributing_features`** names the features that drove the score, with their contribution weights. This is the model's own account of its reasoning, and it is what the Decision Agent turns into a sentence a manager can check. Without it, the probability is a bare number and the explainability contract cannot be met.

**`prediction_horizon_hours`** is what converts a probability into a plan. A 0.68 probability within 72 hours can be scheduled into the next planned window; the same probability within 4 hours must be handled this shift. Same number, entirely different recommended action — which is why horizon is stored rather than implied.

**Primary key**

`prediction_result_id` — surrogate `INTEGER`, with `prediction_result_code` unique.

A code is essential: predictions are cited as the ML confidence evidence in recommendations. The `PDN-` prefix avoids collision with master data's `PRD-` product codes.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `prediction_result_code` | VARCHAR(20) | Yes | `PDN-20260729-0203` | Unique; matches `^PDN-[0-9]{8}-[0-9]{4}$` | Prediction reference **cited as ML confidence evidence** |
| `prediction_feature_snapshot_id` | INTEGER (FK op) | Yes | `FSN-20260729-04188` | References a snapshot with `is_sufficient_for_inference = 1` | **The input.** Together with the model version, makes this prediction reproducible |
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine`; must match the snapshot's machine | The machine assessed |
| `predicted_at` | DATETIME | Yes | `2026-07-29 18:45:02+05:30` | ≥ the snapshot's `generated_at` | **Event time** of inference |
| `model_name` | VARCHAR(60) | Yes | `factoryflow-pdm` | Non-empty | Which model produced this |
| `model_version` | VARCHAR(20) | Yes | `1.3.0` | Non-empty | **Model version.** With the snapshot, the complete reproducibility pair |
| `failure_probability` | NUMERIC(5,4) | Yes | `0.6800` | 0.0000–1.0000 | **The ML confidence.** Created here and nowhere else. Carried forward unchanged by every downstream stage |
| `risk_severity_level_id` | INTEGER (FK master) | Yes | `SEV-2` | References active `failure_severity_level` | Probability mapped onto the platform severity scale, using `business_rule` cut-offs |
| `predicted_failure_category_id` | INTEGER (FK master) | No | `FC-BRG` | References `failure_category` when present | Predicted mechanism. NULL when the model predicts elevated risk without attributing a mode |
| `machine_type_failure_mode_id` | INTEGER (FK master) | No | `MTY-VMC-500`/`FC-BRG` | References `machine_type_failure_mode` with `is_model_predictable = 1` for this machine's type | **The specific declared mode.** Constrains the prediction to a plausible, declared-predictable failure |
| `prediction_horizon_hours` | INTEGER | Yes | `72` | > 0; should not exceed the mode's `typical_warning_period_hours` | **Time window the probability applies to.** Converts a number into a plan |
| `confidence_band_low` | NUMERIC(5,4) | No | `0.5900` | ≤ `failure_probability` when present | Lower bound of the model's uncertainty. NULL when the model produces no interval |
| `confidence_band_high` | NUMERIC(5,4) | No | `0.7600` | ≥ `failure_probability` when present | Upper bound. **A wide band is itself information the Decision Agent should convey** |
| `top_contributing_features` | JSON in TEXT | Yes | see below | Non-empty | **Feature attributions.** The model's account of its own reasoning, turned into prose by the Decision Agent |
| `triggering_alert_id` | INTEGER (FK op) | No | `ALR-20260729-0087` | References `operational_alert` when present | Alert that prompted this inference. NULL for scheduled scoring |
| `inference_duration_ms` | INTEGER | Yes | `34` | ≥ 0 | Model execution time. Feeds `system_health_status` and cost monitoring |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `predicted_at` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `prediction_feature_snapshot` | Operational | Many-to-one | The input vector |
| Parent | `machine`, `failure_severity_level`, `failure_category`, `machine_type_failure_mode`, `shift` | Master | Many-to-one, some optional | Subject, severity, predicted mode |
| Parent | `operational_alert` | Operational | Many-to-one, optional | Prompting alert |
| Referenced by | `supervisor_context`, `ai_recommendation` | Operational | One-to-many | Downstream consumption |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Prediction Agent.** Sole writer |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A model produced this output from this input at this moment. Revising it would destroy both reproducibility and the audit trail |
| **Append-only** | Yes |
| **Expires** | Retained 2 years, then archived indefinitely. **Predictions outlive their snapshots** because accuracy scoring against confirmed failures happens over long periods |
| **Regenerable** | **Yes, while the snapshot survives** — re-running the same model version on the same vector must return the same result, and that property is the audit contract. Non-regenerable once the snapshot is purged |

**Business rules**

1. **The failure probability originates here and is never recreated.** The Decision Agent carries it forward unchanged. This is the structural enforcement of the `PROJECT_OVERVIEW.md` §16.5 rule that ML confidence must never be restated by the LLM.
2. A prediction may only reference a snapshot with `is_sufficient_for_inference = 1`.
3. `machine_id` must match the snapshot's machine.
4. `machine_type_failure_mode_id`, when present, must reference a mode declared for this machine's type with `is_model_predictable = 1`. **The model may not predict a failure mode nothing precedes.**
5. `prediction_horizon_hours` should not exceed the referenced mode's `typical_warning_period_hours`. Predicting further ahead than the physics gives warning for is not a forecast.
6. `risk_severity_level_id` is derived from `failure_probability` using `business_rule` cut-offs, never a hardcoded mapping.
7. `top_contributing_features` must name features present in the referenced snapshot. An attribution to a feature the model never received is incoherent.
8. `model_name` and `model_version` are mandatory. A prediction whose producer is unknown cannot be reproduced, audited, or trusted.
9. Predictions are never edited. A re-score is a new row, and both survive so the change is visible.
10. Accuracy is scored by comparing `predicted_failure_category_id` against `maintenance_work_record.confirmed_failure_category_id` for the resulting job. **This comparison is the only honest measure of model quality**, and it is why both entities exist in the detail they do.
11. The Prediction Agent is the sole writer.

**Example records**

| code | snapshot | machine | predicted_at | model | version | probability | risk_sev | predicted_category | horizon_h | band | inference_ms | triggering_alert |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `PDN-20260729-0203` | `FSN-20260729-04188` | `MC-0101` | 07-29 18:45:02 | `factoryflow-pdm` | `1.3.0` | 0.6800 | `SEV-2` | `FC-BRG` | 72 | 0.59–0.76 | 34 | `ALR-20260729-0087` |
| `PDN-20260729-0207` | `FSN-20260729-04205` | `MC-0201` | 07-29 19:00:01 | `factoryflow-pdm` | `1.3.0` | 0.1100 | `SEV-5` | NULL | 72 | 0.06–0.18 | 29 | NULL |
| `PDN-20260729-0244` | `FSN-20260729-04371` | `MC-0101` | 07-29 22:30:03 | `factoryflow-pdm` | `1.3.0` | 0.7400 | `SEV-2` | `FC-BRG` | 48 | 0.66–0.81 | 31 | `ALR-20260729-0087` |
| `PDN-20260730-0119` | `FSN-20260730-00588` | `MC-0101` | 07-30 15:00:02 | `factoryflow-pdm` | `1.3.0` | 0.0600 | `SEV-5` | NULL | 72 | 0.02–0.11 | 33 | NULL |

`top_contributing_features` for `PDN-20260729-0203`:

```
1. PRM-VIB.slope_per_hour                    0.31   contribution 0.29
2. cycle_deviation_slope                     0.42   contribution 0.21
3. PRM-VIB.seconds_above_warning_limit       780    contribution 0.16
4. hours_since_last_maintenance              462.50 contribution 0.13
5. PRM-TEMP.slope_per_hour                   0.28   contribution 0.11
6. attributed_scrap_count_24h                1      contribution 0.06
```

This set carries the whole prediction narrative:

- **`PDN-20260729-0203` is the prediction that drove the incident.** 0.68 probability of `FC-BRG` within 72 hours, mapped to `SEV-2`. Master data gives this mode a 168-hour warning period, so a 72-hour horizon sits comfortably inside what the physics allows. The confidence band of 0.59–0.76 is reasonably tight — the model is not hedging.
- **The attributions span four entities.** Vibration slope from telemetry, cycle deviation slope from `cycle_history`, maintenance position from `machine_operational_status`, attributed scrap from `scrap_record`. **The top two contributions come from mechanically independent measurements**, which is precisely why the Decision Agent can present the root cause as corroborated rather than speculative.
- **`PDN-20260729-0244` shows the risk growing.** Four hours later the probability has risen to 0.74 and the horizon narrowed to 48 hours. The condition is deteriorating, the model is tracking it, and both predictions survive so the trajectory is visible rather than overwritten.
- **`PDN-20260730-0119` closes the loop.** After the repair, the same machine and model return 0.06. That drop is the quantitative confirmation that the intervention worked, and it is only visible because predictions are immutable and retained.
- **`MC-0301` appears nowhere.** Its snapshot was insufficient, so no prediction exists — correctly. The absence is explicable from `FSN-20260729-04191` rather than being a silent gap.
- **`PDN-20260729-0207` has a NULL predicted category** at 0.11 probability. The model reports low risk without attributing a mode, which is the honest output when no signature is present. Forcing a category at low probability would manufacture false specificity.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write.** The simulator must never see the platform's predictions about it |
| **Monitoring Agent** | Does not read. Detection is independent of prediction, which keeps the two layers separable and testable |
| **Prediction Agent** | **Creates every row.** Sole writer |
| **Supervisor Agent** | **The primary escalation input.** Tests `failure_probability` against the `BR-ESC-PROB` threshold — global or line-scoped — to decide whether LLM reasoning is warranted |
| **Decision Agent** | **Carries `failure_probability` forward unchanged** as the ML confidence contract element, and turns `top_contributing_features` into prose a manager can check |
| **Notification Service** | States the probability and horizon so the recipient can calibrate urgency |
| **Dashboard** | Risk score per machine, and probability trend over time |
| **Analytics** | **Model accuracy** against confirmed failures, calibration curves, feature drift, and inference latency |

---

## Group F — Reasoning & Decision

The three entities where the platform stops observing and starts advising, and where a human takes over.

| Entity | Written by | Records |
|---|---|---|
| `supervisor_context` | Supervisor Agent | The escalation decision and the assembled context — **including suppressions** |
| `ai_recommendation` | Decision Agent | The explainable recommendation, contract-complete |
| `recommendation_action` | Dashboard | What the human actually decided |

Two properties of this group are worth stating before the entities.

**Suppressions are recorded, not just escalations.** `supervisor_context` exists for every evaluated situation, including those that did not warrant reasoning. *"Why wasn't I told about this?"* is as important a question as *"why was I told?"*, and a gate that only logs its positive decisions cannot answer it.

**The recommendation has no probability column.** ML confidence is referenced, never stored. §E18 explains why this absence is a structural guarantee rather than an omission.

---

### E17. `supervisor_context`

**Purpose**

Records the Supervisor Agent's escalation decision and the context package assembled for it. It is the audit trail of the platform's cost and noise gate, and the single most useful entity for explaining why the platform did or did not speak.

**Business description**

The Supervisor Agent sits between cheap detection and expensive reasoning. Every alert-and-prediction pair reaches it, and it decides one thing: **does this warrant the Decision Agent?**

A row is written either way. That is the entity's most important characteristic.

If only escalations were recorded, the platform would be unable to answer the question a manager asks after an incident that surprised them: *"the machine was showing symptoms — why didn't the system tell me?"* With suppressions recorded, the answer is a row: *"probability 0.61 was evaluated against the 0.70 global threshold in `BR-ESC-PROB` and did not escalate."* That is a defensible answer, and it is also actionable — it points at the threshold, not at the platform.

**`context_document` is the assembled package**, and its structure is specified because the Decision Agent's output quality depends directly on it:

```
machine:      current state, state duration, hours since last maintenance,
              accumulated hours, age, open alert codes
production:   run code, product, customer, tier, quantity remaining,
              current rate, schedule variance, due date
cascade:      line position, is_bottleneck, downstream buffer units,
              grace period minutes, downstream machine codes
inventory:    required item, quantity on hand, reorder point,
              lead time days, retrieval time minutes, is_critical_spare
maintenance:  due schedules with deferability, open work records,
              available teams and engineers on shift with specialisation
business:     downtime cost rate, customer penalty per day,
              priority weights, applied escalation thresholds
evidence:     event codes with observed values, thresholds, and units
```

Every block is assembled by **reference resolution across master and operational data**, and the document holds the resolved values. This is the one place where consolidation is the point: the Decision Agent receives one coherent package rather than issuing fifteen queries, and the package is preserved exactly as the LLM saw it.

That preservation matters more than convenience. If the context were reassembled at audit time, it would reflect current data rather than what was known at the decision moment, and *"why did it recommend that?"* would become unanswerable. **The context document is the input side of the explainability contract.**

**No business rule values are copied.** `applied_escalation_rule_id` references the `business_rule` row that governed the decision, and nothing more. Master data §29 rule 8 requires that superseded rule values are recorded as **new rows rather than edited in place**, so the referenced row still holds the value that actually applied. Referencing is sufficient precisely because the frozen master model made its policy history immutable — the two documents interlock deliberately here.

**Note on what the Supervisor Agent does not do.** It does not predict, does not reason in natural language, and does not recommend. It decides what deserves attention and gathers what is needed to think about it. `PROJECT_OVERVIEW.md` §5.2 draws that boundary, and this entity's attributes respect it: there is no root cause, no recommended action, and no narrative here.

**Primary key**

`supervisor_context_id` — surrogate `INTEGER`, with `supervisor_context_code` unique.

A code is warranted: escalation decisions are cited when explaining why the platform did or did not act.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `supervisor_context_code` | VARCHAR(20) | Yes | `CTX-20260729-0044` | Unique; matches `^CTX-[0-9]{8}-[0-9]{4}$` | Context reference, cited when explaining an escalation decision |
| `machine_id` | INTEGER (FK master) | No | `MC-0101` | References `machine` when present | Subject machine. NULL for line- or inventory-scoped situations |
| `production_line_id` | INTEGER (FK master) | No | `LN-01` | References `production_line` when present | Affected line. **The scope at which escalation thresholds may be overridden** |
| `assembled_at` | DATETIME | Yes | `2026-07-29 18:46:00+05:30` | Not in the future | **Event time** of the decision |
| `triggering_alert_id` | INTEGER (FK op) | Yes | `ALR-20260729-0087` | References `operational_alert` | The case being evaluated |
| `triggering_prediction_id` | INTEGER (FK op) | No | `PDN-20260729-0203` | References `prediction_result` when present | The prediction evaluated. **NULL when an alert had no prediction** — which is itself a reason not to escalate |
| `related_alert_codes` | JSON in TEXT | No | `["ALR-20260729-0089"]` | Array of alert codes | Other open alerts on the same machine that were considered. **Correlated cases reasoned about together without merging them** |
| `escalation_decision` | TEXT + CHECK | Yes | `escalated` | One of: `escalated`, `suppressed_below_threshold`, `suppressed_duplicate`, `suppressed_maintenance_in_progress`, `suppressed_rate_limited`, `suppressed_insufficient_data` | **The gate's verdict.** Suppression reasons are enumerated so silence is always explicable |
| `applied_escalation_rule_id` | INTEGER (FK master) | No | `BR-ESC-PROB-LN01` | References active `business_rule` with `rule_category = 'escalation'` | **Which rule governed.** Referenced, never copied — master data preserves superseded values as new rows |
| `escalation_rationale` | TEXT | Yes | `Failure probability 0.68 met the Line 01 escalation threshold of 0.55 (BR-ESC-PROB-LN01); severity SEV-2 met the SEV-2 floor (BR-ESC-SEV). Line criticality: critical. Bottleneck machine.` | Non-empty | **Plain-language reason.** Read by humans when auditing the gate; written for a manager, not a developer |
| `context_document` | JSON in TEXT | No | see below | Required when `escalated` | **The assembled package**, preserved exactly as the Decision Agent received it. NULL for suppressions, where no package was built |
| `context_assembly_duration_ms` | INTEGER | Yes | `212` | ≥ 0 | Assembly time. Feeds `system_health_status` and pipeline latency monitoring |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `assembled_at` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `machine`, `production_line`, `business_rule`, `shift` | Master | Many-to-one, some optional | Subject, scope, governing rule |
| Parent | `operational_alert` | Operational | Many-to-one | Case evaluated |
| Parent | `prediction_result` | Operational | Many-to-one, optional | Prediction evaluated |
| Child | `ai_recommendation` | Operational | **One-to-one** in practice | The recommendation produced, when escalated |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Supervisor Agent.** Sole writer. One row per evaluated situation, escalated or not |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Decision Agent (consumes the context document), Dashboard, Analytics. **Not read by** the Simulator, Monitoring Agent, or Prediction Agent |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A decision was made on this information at this moment. Reassembling it later would reflect current data, not what was known |
| **Append-only** | Yes |
| **Expires** | Escalated contexts retained 2 years with their recommendation. **Suppressed contexts retained 180 days** — they are high-volume and their value is in threshold tuning rather than long-term audit |
| **Regenerable** | No. The context document captures a point-in-time assembly that cannot be faithfully rebuilt |

**Business rules**

1. **A row is written for every evaluated situation**, escalated or suppressed. There is no silent path through the gate.
2. `escalation_decision = 'escalated'` requires a non-NULL `context_document`. Escalating without a package gives the Decision Agent nothing to reason over.
3. Suppressions have NULL `context_document`. Assembling a package for a situation not being escalated would waste the queries the gate exists to avoid.
4. `applied_escalation_rule_id` is required for `escalated` and `suppressed_below_threshold`. Both verdicts turn on a threshold, and both must name it.
5. **Escalation threshold resolution is two-step**: a rule scoped to the affected line, falling back to the global rule. Master data §29 rule 3 defines this, and the resolved rule is recorded here.
6. `suppressed_maintenance_in_progress` requires an open `maintenance_work_record` for the machine. Escalating a problem already being fixed adds nothing.
7. `suppressed_rate_limited` occurs when no eligible recipient remains within their `max_notifications_per_hour`. **The context is still recorded and remains visible on the dashboard** — rate limiting suppresses delivery, never recording.
8. `suppressed_insufficient_data` occurs when the prediction's snapshot had low completeness. Reasoning over a weak prediction produces a confident-sounding recommendation with no basis.
9. `escalation_rationale` is mandatory and written for a manager. It is quoted when auditing the gate, so its clarity is functional.
10. `context_document` must contain all seven blocks when escalated. A missing block means the Decision Agent cannot satisfy the corresponding element of the §16.5 contract — most often business impact.
11. The Supervisor Agent is the sole writer, and it never writes a recommendation. Orchestration and reasoning stay separate.

**Example records**

| code | machine | line | assembled_at | trig_alert | trig_prediction | decision | applied_rule | assembly_ms |
|---|---|---|---|---|---|---|---|---|
| `CTX-20260729-0044` | `MC-0101` | `LN-01` | 07-29 18:46 | `ALR-20260729-0087` | `PDN-20260729-0203` | `escalated` | `BR-ESC-PROB-LN01` | 212 |
| `CTX-20260729-0045` | `MC-0201` | `LN-02` | 07-29 19:01 | `ALR-20260729-0093` | `PDN-20260729-0207` | `suppressed_below_threshold` | `BR-ESC-PROB` | 8 |
| `CTX-20260729-0046` | `MC-0301` | `LN-03` | 07-29 18:47 | `ALR-20260729-0091` | NULL | `suppressed_insufficient_data` | NULL | 11 |
| `CTX-20260729-0061` | `MC-0101` | `LN-01` | 07-29 22:31 | `ALR-20260729-0087` | `PDN-20260729-0244` | `suppressed_duplicate` | `BR-ESC-PROB-LN01` | 14 |
| `CTX-20260730-0009` | `MC-0101` | `LN-01` | 07-30 07:15 | `ALR-20260729-0087` | NULL | `suppressed_maintenance_in_progress` | NULL | 9 |

`context_document` for `CTX-20260729-0044`, abbreviated:

```
machine:      MC-0101, running since 14:12:30, 462.5 h since last maintenance,
              11482.5 accumulated hours, 2613 days old, open alerts
              [ALR-20260729-0087, ALR-20260729-0089]
production:   RUN-2026-0714, PRD-GH-100, CUS-001 (gold),
              378 of 480 units remaining, rate 22.5/h vs 24.0 capability,
              93 min behind schedule, due 2026-08-03
cascade:      position 1 of 3, is_bottleneck 1, downstream buffer 18 units,
              grace period 45 min, downstream [MC-0102, MC-0103]
inventory:    INV-CP-BRG-6205, 9 on hand, reorder point 8, lead time 7 d,
              retrieval 15 min from LOC-SP-B2, is_critical_spare 1
maintenance:  SCH-0001 due in 37.5 operating hours, deferrable 14 d,
              requires_line_stop 1; no open work records;
              MTM-MECH on SH-A from 06:00, ENG-01 certified to 2027-06-30, on-call
business:     downtime cost 42000 INR/h (BR-COST-DOWN-LN01),
              penalty 12000 INR/day (CUS-001), gold weight 1.4 (BR-PRIOR-GOLD),
              escalation threshold 0.55 (BR-ESC-PROB-LN01)
evidence:     EVT-20260729-0412 vibration 4.74 vs 4.70 mm/s warning;
              EVT-20260729-0413 temperature 73.9 vs 73.5 °C;
              EVT-20260729-0414 rate 0.30 vs 0.30 mm/s/min;
              EVT-20260729-0417 cycle deviation 3.38 % vs 2.00 %
```

The five rows demonstrate the gate working in five distinct ways:

- **`CTX-20260729-0044` escalated on a line-scoped threshold.** Probability 0.68 against `LN-01`'s 0.55, not the global 0.70. **The same prediction on `LN-03` would not have escalated.** That is the business-aware behavior the platform exists to provide, and it is entirely data-driven — nothing in the code knows about Line 01.
- **`CTX-20260729-0045` is the counterfactual.** `MC-0201` at 0.11 probability, evaluated against the global 0.70, suppressed. Assembly took 8 milliseconds against 212 for the escalation, because no context package was built. **Rule 3 is where the cost saving actually lives**: the expensive work happens only for situations that warrant it.
- **`CTX-20260729-0046` records why a machine was never assessed.** `MC-0301`'s sensor fault produced an insufficient snapshot, so there was no prediction to evaluate. Without this row, `MC-0301`'s silence that evening would be an unexplained gap.
- **`CTX-20260729-0061` prevents duplicate reasoning.** Four hours later the probability had risen to 0.74 — above the threshold — but a recommendation for this alert already existed. Suppressed as duplicate. **Without this rule the platform would re-reason every four hours on an unresolved condition** and bury the original recommendation under near-identical repeats.
- **`CTX-20260730-0009` suppresses during the repair.** The work order was in progress at 07:15. Escalating a problem being actively fixed would be noise, and the reason is recorded rather than inferred.

Between them, one escalation produced one recommendation, and four suppressions each have a stated, auditable reason. That ratio is the gate doing its job.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write** |
| **Monitoring Agent** | Does not read. Detection is upstream of escalation |
| **Prediction Agent** | Does not read. Prediction is upstream of escalation |
| **Supervisor Agent** | **Creates every row.** Sole writer |
| **Decision Agent** | **Consumes `context_document` as its entire input.** It issues no queries of its own — everything it reasons over is in this one payload |
| **Notification Service** | Does not read this entity |
| **Dashboard** | Shows escalation decisions including suppressions, so operators can see what the platform chose not to raise |
| **Analytics** | **Escalation rate and threshold tuning.** Suppressed contexts followed by an actual failure are **missed escalations** — the most valuable signal for adjusting thresholds, and only visible because suppressions are recorded |

---

### E18. `ai_recommendation`

**Purpose**

The Decision Agent's output: an explainable, business-aware recommendation implementing every element of the `PROJECT_OVERVIEW.md` §16.5 contract. It is the platform's actual product.

**Business description**

Everything upstream exists to produce this row. Telemetry, detection, prediction, and context assembly all converge into one recommendation that a factory manager can read, act on, and challenge.

**The recommendation has no failure probability column, and that absence is deliberate.**

`PROJECT_OVERVIEW.md` §16.5 requires ML confidence in every recommendation and states that it must originate from the Prediction Agent and never be restated by the LLM. This model enforces that **structurally**: `prediction_result_id` is a mandatory reference, and there is nowhere on this entity to write a probability. The Decision Agent cannot invent, round, or "adjust" the number because no column accepts one. A convention would have been broken eventually; a missing column cannot be.

**Root cause is constrained to the controlled vocabulary.** `root_cause_failure_category_id` references `failure_category` — master data, twelve reviewed values. The LLM classifies within a validated set rather than generating free-form causes. That constraint is what makes the root cause checkable by an engineer, matchable to a maintenance specialisation, and linkable to a spare part. Master data §24 rule 1 states the vocabulary is controlled; this column is where the constraint binds.

**`root_cause_confidence` keeps the hypothesis honest.** Master data §16.5 requires root cause to be framed as a hypothesis rather than a certainty. Three values — `high`, `moderate`, `low` — force the Decision Agent to state how sure it is, and `high` should require corroboration from more than one measurement path. In the worked incident three independent signals agree, which is what justifies `high`.

**`contract_complete` is a self-check.** The five mandatory elements are supporting evidence, ML confidence, root cause, business impact, and recommended action. This boolean records whether all five were produced. A recommendation with `contract_complete = 0` **must not be delivered as final** — master data's companion rule — and the flag is what makes that enforceable rather than aspirational.

**The structured payloads.** `supporting_evidence` and `business_impact` are documents because they are composed once and rendered whole into notifications and dashboards. Their structures:

```
supporting_evidence:
  events:      [code, parameter, observed, threshold, unit, detected_at]
  readings:    [parameter, latest, healthy_max, pct_above, unit]
  corroboration: [source entity, finding]     ← the independent-signal list
  feature_contributions: [feature, value, contribution]

business_impact:
  affected_run, product, customer, customer_tier
  units_at_risk, margin_at_risk, downtime_cost, penalty_exposure
  grace_period_minutes, schedule_variance_minutes
  reroute_options: [line, rate, changeover_minutes, qualified]
```

**Primary key**

`ai_recommendation_id` — surrogate `INTEGER`, with `ai_recommendation_code` unique.

A code is essential: recommendations are referenced by managers, cited in notifications, and recorded on the work orders they produce.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `ai_recommendation_code` | VARCHAR(20) | Yes | `REC-20260729-0031` | Unique; matches `^REC-[0-9]{8}-[0-9]{4}$` | Recommendation reference used by managers and cited on work orders |
| `supervisor_context_id` | INTEGER (FK op) | Yes | `CTX-20260729-0044` | References a context with `escalation_decision = 'escalated'` | **The input.** Every recommendation traces to one escalated context |
| `prediction_result_id` | INTEGER (FK op) | Yes | `PDN-20260729-0203` | References `prediction_result` | **The ML confidence element, by reference.** There is no probability column — see above |
| `machine_id` | INTEGER (FK master) | Yes | `MC-0101` | References `machine` | Subject machine |
| `production_line_id` | INTEGER (FK master) | Yes | `LN-01` | References `production_line` | Affected line |
| `production_run_id` | INTEGER (FK op) | No | `RUN-2026-0714` | References `production_run` when present | Run at risk |
| `generated_at` | DATETIME | Yes | `2026-07-29 18:49:00+05:30` | ≥ the context's `assembled_at` | **Event time** of generation |
| `llm_model_name` | VARCHAR(60) | Yes | `gemini` | Non-empty | Which model reasoned |
| `llm_model_version` | VARCHAR(40) | Yes | `2.5-pro` | Non-empty | Model version. **Reproducibility and quality attribution** — a change in recommendation quality must be attributable to a model change |
| `priority_severity_level_id` | INTEGER (FK master) | Yes | `SEV-2` | References active `failure_severity_level` | Recommendation priority, reflecting technical severity **and** business impact |
| `root_cause_failure_category_id` | INTEGER (FK master) | Yes | `FC-BRG` | References active `failure_category` | **Root cause, from the controlled vocabulary.** The LLM classifies, never invents |
| `root_cause_confidence` | TEXT + CHECK | Yes | `high` | One of: `high`, `moderate`, `low` | **How sure the hypothesis is.** `high` requires corroboration from more than one measurement path |
| `supporting_evidence` | JSON in TEXT | Yes | see above | Non-empty | **Contract element 1.** Events, readings, corroboration, feature attributions |
| `business_impact` | JSON in TEXT | Yes | see above | Non-empty | **Contract element 4.** Units, margin, cost, penalty, grace period, reroute options |
| `recommended_action` | TEXT | Yes | `Schedule spindle bearing replacement at the 06:00 shift change on 30 July. Combine with the SCH-0001 500-hour service, now 37.5 operating hours away, to avoid a second stoppage. Do not stop the line mid-batch.` | Non-empty | **Contract element 5.** The concrete immediate step |
| `recovery_plan` | TEXT | Yes | `Bearing INV-CP-BRG-6205 is in stock (9 on hand, retrieval 15 min from LOC-SP-B2). Expected downtime 255 min. Run RUN-2026-0714 has 5 days of float against its 3 August due date, so a planned 06:00 stop keeps the commitment. If vibration exceeds 6.0 mm/s critical before then, stop immediately — grace period is 45 min on the 18-unit downstream buffer.` | Non-empty | Recovery guidance including contingency |
| `suggested_maintenance_team_id` | INTEGER (FK master) | No | `MTM-MECH` | References active `maintenance_team` when present | Suggested team, matched on specialisation and shift availability |
| `suggested_engineer_id` | INTEGER (FK master) | No | `ENG-01` | References active `maintenance_engineer` when present | Suggested engineer, checked for certification and on-call status |
| `required_inventory_item_id` | INTEGER (FK master) | No | `INV-CP-BRG-6205` | References active `inventory_item` when present | Part needed, from the predicted failure mode |
| `estimated_downtime_minutes` | INTEGER | No | `255` | > 0 when present | Expected downtime, from the failure mode estimate plus retrieval time |
| `recommended_action_by` | DATETIME | No | `2026-07-30 06:00:00+05:30` | > `generated_at` when present | **Deadline for acting.** Derived from grace period, warning period, and available windows |
| `reasoning_narrative` | TEXT | Yes | see below | Non-empty | The Decision Agent's explanation in prose. **What a manager reads to decide whether to trust it** |
| `contract_complete` | INTEGER | Yes | `1` | — | **Whether all five §16.5 elements were produced.** `0` must not be delivered as final |
| `generation_duration_ms` | INTEGER | Yes | `4180` | ≥ 0 | LLM latency. Feeds health monitoring |
| `prompt_token_count` | INTEGER | No | `3240` | ≥ 0 when present | Input size. **Cost monitoring** — the metric that proves the escalation gate is paying for itself |
| `completion_token_count` | INTEGER | No | `812` | ≥ 0 when present | Output size |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `generated_at` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `supervisor_context` | Operational | **One-to-one** in practice | The escalated context |
| Parent | `prediction_result` | Operational | Many-to-one | ML confidence, by reference |
| Parent | `machine`, `production_line`, `failure_severity_level`, `failure_category`, `maintenance_team`, `maintenance_engineer`, `inventory_item`, `shift` | Master | Many-to-one, some optional | Subject, priority, root cause, suggested assignment, required part |
| Parent | `production_run` | Operational | Many-to-one, optional | Run at risk |
| Child | `recommendation_action` | Operational | One-to-many | Human responses |
| Child | `notification` | Operational | One-to-many | Messages composed from it |
| Referenced by | `maintenance_work_record` | Operational | One-to-many | Jobs it caused |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Decision Agent.** Sole writer |
| **Updated by** | Nobody. **Immutable.** The human response is a separate entity, precisely so the recommendation stays untouched |
| **Read by** | Notification Service, Dashboard, Analytics, and the Decision Agent itself as precedent for similar future situations |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. **This is the platform's product and its accountability record.** An editable recommendation could be quietly improved after the fact, which would destroy the audit trail entirely |
| **Append-only** | Yes |
| **Expires** | Retained 3 years, then archived indefinitely. **Every entity it cites is exempt from purge while it survives**, so the evidence chain never breaks |
| **Regenerable** | No. LLM output is not deterministic, and even with the same context and model version a re-run would differ. This is the strongest argument for storing it rather than reconstructing it |

**Business rules**

1. Every recommendation references exactly one escalated `supervisor_context`. No recommendation exists without a recorded escalation decision.
2. **`prediction_result_id` is mandatory and there is no probability column.** ML confidence is referenced, never restated. Structural enforcement of the §16.5 rule.
3. `root_cause_failure_category_id` must reference a category in the controlled vocabulary, and should be one declared for this machine's type in `machine_type_failure_mode`. A root cause implausible for the equipment is a reasoning failure worth detecting.
4. `root_cause_confidence = 'high'` requires the `supporting_evidence` corroboration list to contain findings from **at least two independent measurement paths.** One signal is a hypothesis; two agreeing is a corroborated one.
5. All five contract elements must be present for `contract_complete = 1`. A recommendation with `contract_complete = 0` **must not be delivered as final** and must be flagged for review.
6. `suggested_engineer_id`, when present, must belong to the suggested team, hold valid certification, and be on shift or `is_on_call`. The platform must never recommend an engineer who cannot legally or practically attend.
7. `required_inventory_item_id`, when present, must come from the predicted failure mode's `required_inventory_item_id`. The part is derived from the failure, not guessed.
8. `estimated_downtime_minutes` should equal the failure mode's `estimated_repair_duration_minutes` plus the part location's `average_retrieval_time_minutes`. Both are master data; the sum is the honest estimate.
9. `recommended_action_by` must account for the grace period from `machine.downstream_buffer_units` and the current rate. A deadline later than the line will starve is not a usable deadline.
10. The recommendation **advises and never actuates.** No attribute represents a command, a setpoint, or an instruction to a machine. This is the hard architectural boundary from `PROJECT_OVERVIEW.md`, and its enforcement is the absence of any such column.
11. Recommendations are never edited or deleted. A superseding recommendation is a new row, and the original stays.
12. The Decision Agent is the sole writer.

**Example record**

`REC-20260729-0031`:

| Field | Value |
|---|---|
| code | `REC-20260729-0031` |
| context | `CTX-20260729-0044` |
| prediction | `PDN-20260729-0203` — probability **0.68**, `SEV-2`, `FC-BRG`, 72 h horizon |
| machine / line / run | `MC-0101` / `LN-01` / `RUN-2026-0714` |
| generated_at | 2026-07-29 18:49:00 |
| llm model | `gemini` `2.5-pro` |
| priority | `SEV-2` |
| root cause | `FC-BRG` — Bearing Degradation |
| root cause confidence | `high` |
| suggested team / engineer | `MTM-MECH` / `ENG-01` |
| required part | `INV-CP-BRG-6205` |
| estimated downtime | 255 minutes |
| recommended action by | 2026-07-30 06:00:00 |
| contract complete | `1` |
| generation | 4,180 ms · 3,240 prompt tokens · 812 completion tokens |

`supporting_evidence`:

```
events:      EVT-20260729-0412  PRM-VIB   4.74 vs 4.70 mm/s warning   18:42:00
             EVT-20260729-0413  PRM-TEMP  73.9 vs 73.5 °C warning     18:42:00
             EVT-20260729-0414  PRM-VIB   0.30 vs 0.30 mm/s/min rate  18:42:10
             EVT-20260729-0417  cycle deviation 3.38 % vs 2.00 %      18:48:00
readings:    PRM-VIB   latest 4.80, healthy max 4.50, 6.7 % above
             PRM-TEMP  latest 74.2, healthy max 72.0, 3.1 % above
corroboration:
             machine_sensor_reading  vibration slope 0.31 mm/s per hour over 4 h
             cycle_history           mean deviation 2.71 %, slope 0.42 — independent
                                     of sensors, measured by the machine's own timer
             quality_inspection_result  QIR-20260729-0158 bore roundness 0.021 mm
                                     out of tolerance, attributed to MC-0101 / FC-BRG
feature_contributions:
             PRM-VIB.slope_per_hour        0.31    0.29
             cycle_deviation_slope         0.42    0.21
             PRM-VIB.seconds_above_warning 780     0.16
             hours_since_last_maintenance  462.5   0.13
```

`business_impact`:

```
affected_run      RUN-2026-0714, PRD-GH-100, CUS-001 Apex Drivetrain (gold)
units_at_risk     108 units if unplanned failure (4.5 h at 24 units/h)
margin_at_risk    292,680 INR (108 × 2,710 contribution per unit)
downtime_cost     189,000 INR (4.5 h × 42,000 INR/h, BR-COST-DOWN-LN01)
penalty_exposure  0 INR if the 3 August due date is met; 12,000 INR per day if not
grace_period      45 minutes (18-unit downstream buffer at 24 units/h)
schedule_variance 93 minutes behind at time of assessment
reroute_options   none — PRD-GH-100 has no qualified alternative production route
```

`reasoning_narrative`:

> Spindle vibration on MC-0101 has risen from a 2.1 mm/s nominal to 4.8 mm/s over four hours, crossing the 4.70 mm/s warning limit of the tightened monitoring profile and reaching its 0.3 mm/s per minute rate limit. Spindle temperature is rising in step. Two further signals agree through entirely independent measurement: mean cycle time has drifted 2.71 % above the 145-second standard, measured by the machine's own timer rather than any sensor, and a dimensional inspection at 18:52 found bore roundness 0.021 mm out of tolerance, attributed to this machine. Rising vibration with rising temperature, lengthening cycles, and loss of bore roundness together are the recognised signature of front spindle bearing degradation, which master data records as the most common failure mode for this machine type with a typical warning period of about a week. The predictive model assigns a 0.68 probability of bearing failure within 72 hours.
>
> MC-0101 is the bottleneck on Line 01, so its stoppage costs the line its full 24 units per hour. The 18-unit downstream buffer gives roughly 45 minutes before MC-0102 and MC-0103 starve. Run RUN-2026-0714 is producing for Apex Drivetrain, a Gold-tier account with a 98 % on-time commitment, and is already 93 minutes behind schedule. An unplanned failure would cost approximately 108 units, 292,680 INR of contribution margin, and 189,000 INR of line downtime.
>
> The run has five days of float against its 3 August due date, so a planned stoppage is affordable where an unplanned one would not be. The 500-hour preventive service under SCH-0001 is 37.5 operating hours away — about two days — and also requires a line stop. Performing both at the 06:00 shift change on 30 July costs one stoppage instead of two and avoids interrupting the current batch. The bearing is in stock with nine on hand, and ENG-01 of the mechanical team is certified, on call, and on shift from 06:00.
>
> If vibration reaches the 6.0 mm/s critical limit before then, stop immediately: at that point the 45-minute grace period is the only remaining margin.

Six things are worth drawing out:

- **The probability appears once, and it is quoted from the prediction.** 0.68, cited as the model's figure. The recommendation has no column to hold its own, so the number cannot drift.
- **Root cause confidence is `high` and rule 4 is satisfied.** Three corroborating findings from three independent measurement paths — accelerometer, cycle timer, coordinate measuring machine. Any one alone would justify `moderate` at best.
- **The reroute option is honestly stated as none.** Master data gives `PRD-GH-100` only one capability row of type `production_route`, on `LN-01`. The Decision Agent checked and reported the absence rather than inventing an option. `PRD-VB-075` would have had an alternative; this product does not.
- **The recommendation combines two jobs.** This is the most valuable thing in the whole document, and it was only possible because `machine_operational_status` carried the counters that made `SCH-0001` computably 37.5 hours away. The master data model's refusal to cache a due date (§26) paid off precisely here.
- **The contingency is concrete.** *"If vibration reaches 6.0, stop immediately — grace is 45 minutes."* A specific trigger, a specific number, a specific consequence. Not "monitor closely."
- **Cost is measured: 3,240 prompt tokens for one recommendation.** At roughly 40 escalations a day suppressed down to a handful of recommendations, the escalation gate is what makes LLM reasoning affordable — and `prompt_token_count` is how that claim gets verified rather than asserted.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write.** The simulator must never see recommendations about the factory it is generating |
| **Monitoring Agent** | Does not read |
| **Prediction Agent** | Does not read |
| **Supervisor Agent** | Reads only to check whether a recommendation already exists for an alert, which is how `suppressed_duplicate` is decided |
| **Decision Agent** | **Creates every row.** Sole writer. Also reads past recommendations for similar machines as precedent |
| **Notification Service** | **Composes messages from this entity.** Renders the action, impact, and evidence per recipient |
| **Dashboard** | **The primary management surface.** Active recommendations with full reasoning, evidence, and impact |
| **Analytics** | Acceptance rate, quality by model version, contract completeness rate, and — via `recommendation_action` and `maintenance_work_record` — whether acting on recommendations measurably reduced unplanned downtime |

---

### E19. `recommendation_action`

**Purpose**

Records what the human actually decided about a recommendation. It is the human-in-the-loop audit record and the foundation of the decision feedback loop.

**Business description**

`PROJECT_OVERVIEW.md` is unambiguous that the platform advises and the manager decides. This entity is where that decision is recorded, and it is the only place in the model where a human's judgement about the platform's output is captured.

**It is a separate entity so the recommendation stays immutable.** Putting `status = 'accepted'` on `ai_recommendation` would make the platform's product mutable, and a recommendation that can be edited after a human responds to it is not an audit record. Keeping the response separate means the advice and the decision are both permanent and both attributable.

**`action_taken = 'accepted_with_modification'` is the most informative value.** A manager who accepts the diagnosis but changes the timing is telling the platform something specific: the reasoning was right and the scheduling judgement was incomplete. That is far more useful feedback than a binary accept or reject, and it is the commonest real outcome. In the worked incident the supervisor accepted the bearing diagnosis and moved the work to the shift change — the platform was right about *what* and improvable about *when*.

**`rejection_reason` is the platform's error catalogue.** Enumerated so rejections aggregate into something actionable: `disagree_with_diagnosis` points at the model, `impractical_timing` at the scheduling logic, `already_addressed` at the duplicate-detection rule, `insufficient_evidence` at the confidence calibration. Free-text rejections would be unaggregatable and the platform would never learn from them.

**`response_time_minutes` measures the platform's real effectiveness.** A recommendation acted on in twenty minutes prevented something. The same recommendation acted on six hours later probably did not. This is the metric that determines whether notification routing and severity thresholds are working.

**This entity is the Phase 3 foundation.** `PROJECT_OVERVIEW.md` §18 describes a decision feedback loop — capturing acceptance, tracking outcomes, refining reasoning. The loop is not built now, but its data is captured from the start, because feedback that was never recorded cannot be recovered retrospectively.

**Primary key**

`recommendation_action_id` — surrogate `INTEGER`. No business code: it is a child of a coded recommendation and nobody names it separately.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `ai_recommendation_id` | INTEGER (FK op) | Yes | `REC-20260729-0031` | References `ai_recommendation` | The recommendation responded to |
| `action_taken` | TEXT + CHECK | Yes | `accepted_with_modification` | One of: `accepted`, `accepted_with_modification`, `rejected`, `deferred`, `superseded`, `no_action_taken` | **The human decision.** `accepted_with_modification` is the most informative and the commonest |
| `actioned_at` | DATETIME | Yes | `2026-07-29 19:10:00+05:30` | ≥ the recommendation's `generated_at` | **Event time** of the decision |
| `actioned_by_worker_id` | INTEGER (FK master) | Yes | `EMP-1002` | References active `worker` whose role carries the required authority | **Who decided.** Accountability, and the check that the decider had the authority |
| `response_time_minutes` | INTEGER | Yes | `21` | ≥ 0; = `actioned_at` − `generated_at` | **Time from recommendation to decision.** The platform's real effectiveness measure |
| `modification_note` | TEXT | No | `Diagnosis accepted. Deferring the stop to the 06:00 shift change to complete the current batch of 40 units; vibration to be watched against the 6.0 critical limit overnight by SH-C.` | Required when `accepted_with_modification` | **What the human changed and why.** The richest feedback the platform receives |
| `rejection_reason` | TEXT + CHECK | No | NULL | One of: `disagree_with_diagnosis`, `impractical_timing`, `resource_unavailable`, `already_addressed`, `insufficient_evidence`, `business_priority_conflict`; required when `rejected` | **Why it was rejected.** Enumerated so rejections aggregate into an improvement signal |
| `rejection_note` | TEXT | No | NULL | Required when `rejected` | Rejection detail in plain language |
| `deferred_until` | DATETIME | No | NULL | > `actioned_at`; required when `deferred` | When to reconsider. NULL otherwise |
| `resulting_work_record_id` | INTEGER (FK op) | No | `WO-2026-0341` | References `maintenance_work_record` when present | Job created from this decision. **The link that proves a recommendation produced action** |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `actioned_at` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `ai_recommendation` | Operational | Many-to-one | The recommendation |
| Parent | `worker`, `shift` | Master | Many-to-one | Decider and crew |
| Parent | `maintenance_work_record` | Operational | Many-to-one, optional | Resulting job |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Dashboard**, the surface where a human records a decision |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Supervisor Agent (whether already addressed), Decision Agent (precedent), Notification Service (stop escalating once acknowledged), Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A decision was made at a moment by a named person. A change of mind is a **new** action row, so the sequence of decisions stays visible |
| **Append-only** | Yes |
| **Expires** | Retained 3 years alongside its recommendation, then archived indefinitely |
| **Regenerable** | No. **The least regenerable entity in the model** — it records human judgement that exists nowhere else |

**Business rules**

1. A recommendation may have **more than one** action row. A deferral followed by an acceptance is two decisions, and both are recorded.
2. The latest action by `actioned_at` is the operative one. Earlier actions remain as history.
3. `actioned_by_worker_id` must hold the authority the recommendation implies. A recommendation whose priority `requires_line_stop` must be actioned by somebody with `can_authorize_line_stop`.
4. `accepted_with_modification` requires a `modification_note`. An unexplained modification is lost feedback.
5. `rejected` requires both `rejection_reason` and `rejection_note`. **Rejections are the platform's most valuable improvement signal** and an unexplained one teaches nothing.
6. `deferred` requires `deferred_until`, and the recommendation is re-surfaced then.
7. `accepted` or `accepted_with_modification` should produce a `resulting_work_record_id` when the action involves maintenance. Acceptance with no resulting job is a process gap worth reporting.
8. `no_action_taken` is recorded when a recommendation expired without a decision. **This is a delivery or attention failure and must be visible** — an unanswered recommendation is the platform's worst outcome, worse than a rejected one, because nobody even engaged with it.
9. Any resulting work record with `work_type = 'predictive'` must reference this recommendation, closing the loop from advice to action.
10. Action rows are never edited or deleted.

**Example records**

| recommendation | action_taken | actioned_at | actioned_by | response_min | rejection_reason | resulting_wo |
|---|---|---|---|---|---|---|
| `REC-20260729-0031` | `accepted_with_modification` | 07-29 19:10 | `EMP-1002` | 21 | NULL | `WO-2026-0341` |
| `REC-20260728-0022` | `accepted` | 07-28 10:15 | `EMP-1010` | 8 | NULL | `WO-2026-0338` |
| `REC-20260726-0014` | `rejected` | 07-26 14:40 | `EMP-1004` | 35 | `insufficient_evidence` | NULL |
| `REC-20260722-0009` | `deferred` | 07-22 09:20 | `EMP-1010` | 14 | NULL | NULL |
| `REC-20260722-0009` | `accepted` | 07-24 08:05 | `EMP-1010` | 2,819 | NULL | `WO-2026-0319` |
| `REC-20260719-0003` | `no_action_taken` | 07-20 06:00 | `EMP-1001` | 1,140 | NULL | NULL |

Each row is a different lesson:

- **The incident: accepted with modification in 21 minutes.** Priya Nair, the Line 01 supervisor, accepted the bearing diagnosis and moved the stoppage to the 06:00 shift change to finish the current batch. **The platform was right about what and improvable about when.** The modification note is precisely the feedback a Phase 3 loop would learn from, and capturing it now costs nothing.
- **A rejection for `insufficient_evidence`.** One rejection is a data point. Twenty rejections with the same reason against the same threshold profile is a calibration problem with an address.
- **`REC-20260722-0009` has two action rows.** Deferred on 22 July, accepted on 24 July. Both survive, so the decision sequence is visible. Rule 2 makes the second operative without erasing the first.
- **`REC-20260719-0003` recorded `no_action_taken` after 19 hours.** Nobody engaged with it at all. This is the platform's worst outcome — worse than rejection, because a rejection at least means somebody read it. Rule 8 makes it visible instead of letting it disappear, and it points at notification routing rather than at reasoning quality.
- **Response times span 8 to 2,819 minutes**, which is exactly the distribution Analytics needs to establish whether severity thresholds and recipient routing are working.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write.** Human decisions are outside the simulation |
| **Monitoring Agent** | Does not read |
| **Prediction Agent** | Does not read now. **Phase 3 would use acceptance and outcome as a training signal** |
| **Supervisor Agent** | Checks whether a recommendation was already actioned, which prevents re-escalating a handled situation |
| **Decision Agent** | Reads past actions as **precedent** — a repeatedly modified recommendation type suggests the reasoning needs adjusting |
| **Notification Service** | **Stops escalating once an action is recorded.** This is what terminates the escalation chain |
| **Dashboard** | **Creates every row.** Sole writer. Also displays decision history per recommendation |
| **Analytics** | **The platform's scorecard.** Acceptance rate, modification patterns, rejection reasons by profile, response time by severity, and the rate of unanswered recommendations |

---

## Group G — Delivery

Getting the recommendation to a person. Two entities, split along the same principle as detection:

| | `notification` | `notification_delivery` |
|---|---|---|
| **Is** | The message composed for one recipient | One transmission attempt on one channel |
| **Cardinality** | One per recipient per event | One per channel per attempt, including retries |
| **Records** | What was said, to whom, and whether it was suppressed | Whether it actually arrived |

**Why the split.** One recommendation goes to three recipients on two channels with a retry — that is 3 notifications and up to 8 delivery attempts. Collapsing them would either lose the per-channel outcome or duplicate the message body per attempt. The split also separates two genuinely different failure modes: *deliberately not sent* (suppression, recorded on the notification) and *sent but did not arrive* (recorded on the delivery).

---

### E20. `notification`

**Purpose**

One message composed for one recipient, with the decision of whether to send it. It records what the platform said, to whom, and — when it stayed silent — why.

**Business description**

A notification is a composed message targeted at a specific person. The Notification Service reads `notification_recipient` from master data to determine who qualifies for a given severity and scope, and writes one notification per qualifying recipient.

**Suppression is recorded as a notification, not as an absence.** A recipient who was skipped because of quiet hours, a rate limit, or a severity floor still gets a row, with `is_suppressed = 1` and a reason. This is one of the model's more important small decisions: without it, "was Priya told?" would be answered by the absence of a row, and absence is ambiguous between *deliberately suppressed*, *never composed*, and *lost to a bug*. A suppressed notification remains fully visible on the dashboard — **suppression stops transmission, never recording.**

**The message body is stored, not regenerated.** A notification is a communication that happened, and what the recipient actually saw is part of the audit trail. Regenerating it later from the recommendation would produce current wording against a past decision.

**`requires_acknowledgement` and `acknowledgement_deadline_at`** come from `failure_severity_level` in master data — `requires_manager_acknowledgement` and `max_acknowledgement_minutes`. The deadline is resolved and stored at composition because the escalation clock runs against it, and a clock whose deadline is recomputed on every check is a clock that can drift.

**Primary key**

`notification_id` — surrogate `INTEGER`, with `notification_code` unique. A code is warranted for delivery support queries.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `notification_code` | VARCHAR(22) | Yes | `NTF-20260729-00119` | Unique; matches `^NTF-[0-9]{8}-[0-9]{5}$` | Message reference for support and audit |
| `notification_recipient_id` | INTEGER (FK master) | Yes | recipient row for `EMP-1002` | References active `notification_recipient` | **Who this was for.** Contact endpoints resolve through the recipient to `worker` — no contact detail is copied here |
| `notification_type` | TEXT + CHECK | Yes | `recommendation` | One of: `recommendation`, `alert_escalation`, `acknowledgement_reminder`, `inventory_warning`, `system_health` | What prompted the message. Most are recommendations; some are direct alert escalations |
| `ai_recommendation_id` | INTEGER (FK op) | No | `REC-20260729-0031` | References `ai_recommendation`; required when type is `recommendation` | The recommendation conveyed |
| `operational_alert_id` | INTEGER (FK op) | No | `ALR-20260729-0087` | References `operational_alert` when present | The alert conveyed or escalated |
| `severity_level_id` | INTEGER (FK master) | Yes | `SEV-2` | References active `failure_severity_level` | Message severity. Must be at or above the recipient's `min_severity_level_id` unless suppressed for that reason |
| `composed_at` | DATETIME | Yes | `2026-07-29 18:49:30+05:30` | Not in the future | **Event time** of composition |
| `subject` | VARCHAR(200) | Yes | `SEV-2: MC-0101 bearing degradation predicted — action needed by 06:00` | Non-empty | Message subject. **Carries severity, machine, and deadline**, because many recipients decide whether to open it from this line alone |
| `body_text` | TEXT | Yes | see below | Non-empty | The message as sent. **Stored, not regenerated** |
| `is_suppressed` | INTEGER | Yes | `0` | Default 0 | Whether transmission was withheld. **Suppressed rows are still recorded and displayed** |
| `suppression_reason` | TEXT + CHECK | No | NULL | One of: `quiet_hours`, `rate_limited`, `below_min_severity`, `recipient_inactive`, `channel_unavailable`, `already_acknowledged`; required when suppressed | Why the platform stayed silent for this recipient |
| `requires_acknowledgement` | INTEGER | Yes | `1` | From the severity's `requires_manager_acknowledgement` | Whether a human must confirm receipt |
| `acknowledgement_deadline_at` | DATETIME | No | `2026-07-29 19:19:30+05:30` | > `composed_at`; required when `requires_acknowledgement` | **The escalation clock.** Resolved at composition from the severity's `max_acknowledgement_minutes` |
| `escalation_order_applied` | INTEGER | Yes | `1` | > 0 | The recipient's position in the chain, from `notification_recipient.escalation_order` |
| `shift_id` | INTEGER (FK master) | Yes | `SH-B` | References `shift` containing `composed_at` | Crew on duty |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `notification_recipient`, `failure_severity_level`, `shift` | Master | Many-to-one | Recipient, severity, crew |
| Parent | `ai_recommendation`, `operational_alert` | Operational | Many-to-one, optional | What is being conveyed |
| Child | `notification_delivery` | Operational | **One-to-many** | Per-channel attempts |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Notification Service.** Sole writer |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Notification Service (escalation clock), Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. A message was composed and either sent or withheld. Both are historical facts |
| **Append-only** | Yes |
| **Expires** | Retained 1 year, then archived. Notifications for retained recommendations are exempt |
| **Regenerable** | No. The body as sent is part of the communication record |

**Business rules**

1. One notification per qualifying recipient per triggering event. Three recipients produce three rows.
2. Recipients qualify by `min_severity_level_id` and `scope_production_line_id` from master data. A line-scoped recipient receives only their own line's notifications.
3. **Suppression produces a row, never an absence.** `is_suppressed = 1` requires a `suppression_reason`.
4. `quiet_hours` suppression applies only when `notification_recipient.notify_outside_shift_hours = 0` **and** the severity does not have `requires_immediate_escalation`. A critical condition overrides quiet hours — master data's severity policy takes precedence over recipient preference.
5. `rate_limited` suppression applies when the recipient has reached `max_notifications_per_hour`.
6. `requires_acknowledgement` and `acknowledgement_deadline_at` derive from the severity level, never from constants.
7. **At least one non-suppressed notification must exist for any severity whose `requires_immediate_escalation` is 1.** If every eligible recipient is suppressed, the departmental `escalation_email` is the fallback — a critical recommendation must never reach nobody.
8. Once a `recommendation_action` is recorded, further escalation notifications for that recommendation are suppressed as `already_acknowledged`.
9. `body_text` must contain the machine, the severity, the recommended action, and the deadline. A message requiring the recipient to open the dashboard to learn what to do has failed at its one job.
10. Notifications are never edited or deleted.

**Example records**

| code | recipient | type | recommendation | severity | composed_at | suppressed | suppression_reason | ack_required | ack_deadline | esc_order |
|---|---|---|---|---|---|---|---|---|---|---|
| `NTF-20260729-00119` | `EMP-1002` | `recommendation` | `REC-20260729-0031` | `SEV-2` | 07-29 18:49:30 | 0 | NULL | 1 | 07-29 19:19:30 | 1 |
| `NTF-20260729-00120` | `EMP-1010` | `recommendation` | `REC-20260729-0031` | `SEV-2` | 07-29 18:49:31 | 0 | NULL | 1 | 07-29 19:19:31 | 2 |
| `NTF-20260729-00121` | `EMP-1001` | `recommendation` | `REC-20260729-0031` | `SEV-2` | 07-29 18:49:31 | **1** | `below_min_severity` | 0 | NULL | 3 |
| `NTF-20260729-00122` | `EMP-1014` | `recommendation` | `REC-20260729-0031` | `SEV-2` | 07-29 18:49:31 | **1** | `quiet_hours` | 1 | NULL | 1 |
| `NTF-20260730-00004` | `EMP-1004` | `inventory_warning` | NULL | `SEV-4` | 07-30 06:38:20 | **1** | `below_min_severity` | 0 | NULL | 1 |

`body_text` of `NTF-20260729-00119`, abbreviated:

> **MC-0101 (Housing Rough Mill, Line 01) — bearing degradation predicted, SEV-2**
>
> Spindle vibration has risen to 4.8 mm/s against a 4.5 healthy maximum, with temperature, cycle time, and bore roundness all drifting in agreement. Predictive model confidence: **0.68 probability of bearing failure within 72 hours**.
>
> **Recommended action:** schedule bearing replacement at the 06:00 shift change on 30 July, combined with the SCH-0001 500-hour service now 37.5 operating hours away. Do not stop the line mid-batch.
>
> **Impact if unplanned:** ~108 units and 292,680 INR contribution margin on RUN-2026-0714 for Apex Drivetrain (Gold). Grace period 45 minutes on the 18-unit buffer.
>
> **Parts:** bearing INV-CP-BRG-6205 in stock, 9 on hand, 15 min retrieval. **Team:** MTM-MECH, ENG-01 on shift from 06:00.
>
> **If vibration exceeds 6.0 mm/s, stop immediately.** Acknowledge by 19:19.
>
> Recommendation REC-20260729-0031 · Alert ALR-20260729-0087

Graduated escalation working exactly as master data configured it:

- **Two of four recipients were notified.** The Line 01 supervisor (`SEV-3` floor, scoped to `LN-01`) and the Maintenance Manager (`SEV-2` floor, plant-wide). Both qualified, both received it.
- **The Plant Manager was suppressed as `below_min_severity`.** `EMP-1001` has a `SEV-1` floor, and this is `SEV-2`. **Correct behavior**, and recorded so it is visibly deliberate. A plant manager notified about every high-severity condition would stop reading them.
- **Night Response was suppressed for `quiet_hours`.** `EMP-1014` works `SH-C` and it was 18:49. Had this been `SEV-1` — which carries `requires_immediate_escalation` — rule 4 would have overridden the suppression. Severity policy beats recipient preference at the top of the scale.
- **The inventory warning reached nobody.** `SEV-4` is below every recipient's floor, so the reorder condition is dashboard-only. Correct: a replenishment signal does not warrant interrupting anybody.
- **The escalation clock never ran.** Acknowledgement deadline 19:19:30, and `EMP-1002` recorded her decision at 19:10 — nine minutes early. `escalation_order` 2 was already notified in parallel, so no chain escalation was needed.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write** |
| **Monitoring Agent** | Does not read |
| **Prediction Agent** | Does not read |
| **Supervisor Agent** | Checks whether any recipient is eligible before escalating. A situation nobody can be told about needs different handling |
| **Decision Agent** | Does not read. It produces the recommendation; composition is downstream |
| **Notification Service** | **Creates every row.** Sole writer. Runs the escalation clock against `acknowledgement_deadline_at` |
| **Dashboard** | Shows what was sent and **what was suppressed and why** — the transparency that makes silence auditable |
| **Analytics** | Suppression rate by reason, acknowledgement time against severity targets, and coverage gaps where notifications reached nobody |

---

### E21. `notification_delivery`

**Purpose**

Records one transmission attempt on one channel, with its outcome. It answers the question the notification cannot: **did the message actually arrive?**

**Business description**

Composing a message and delivering it are different things, and the second fails in ways the first cannot predict. An email address bounces. A WhatsApp provider rate-limits. A network times out. Each is a delivery failure that leaves the notification perfectly composed and completely useless.

This entity makes the difference visible. One notification produces one delivery row per channel per attempt — so an email and a WhatsApp message with one retry produce three rows.

**`delivery_status` distinguishes sent from delivered.** `sent` means the platform handed the message to a provider. `delivered` means the provider confirmed receipt. The gap between them is where silent failures live, and a platform that only records `sent` will believe every message arrived.

**`failure_reason` is enumerated** so failures aggregate. `invalid_address` points at stale master data — a `worker.email` that no longer exists. `provider_error` points at infrastructure. `rate_limited_by_provider` points at message volume. Each has a different fix, and free-text failures would be unaggregatable.

**Note on the master data connection.** A recurring `invalid_address` failure is a **master data quality problem**, not a delivery problem. Master data §17 rule 3 requires that a recipient with an enabled channel has the corresponding endpoint populated, validated at configuration time. This entity is where that validation gets tested against reality, and repeated failures for one recipient should trigger a master data review rather than more retries.

**Primary key**

`notification_delivery_id` — surrogate `INTEGER`. No business code: a delivery attempt is a child of a coded notification.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `notification_id` | INTEGER (FK op) | Yes | `NTF-20260729-00119` | References `notification` with `is_suppressed = 0` | The message being delivered |
| `channel` | TEXT + CHECK | Yes | `whatsapp` | One of: `email`, `whatsapp` | Transport used. Must be enabled for the recipient in master data |
| `attempt_number` | INTEGER | Yes | `1` | > 0; unique per notification and channel | Retry sequence. **Unique per channel** so retries are distinguishable |
| `attempted_at` | DATETIME | Yes | `2026-07-29 18:49:34+05:30` | ≥ the notification's `composed_at` | **Event time** of the attempt |
| `delivery_status` | TEXT + CHECK | Yes | `delivered` | One of: `queued`, `sent`, `delivered`, `failed`, `bounced`, `rejected` | **Outcome.** `sent` and `delivered` are deliberately distinct — the gap between them is where silent failure hides |
| `delivered_at` | DATETIME | No | `2026-07-29 18:49:37+05:30` | ≥ `attempted_at`; required when `delivered` | Provider-confirmed delivery time. NULL until confirmed |
| `provider_reference` | VARCHAR(120) | No | `wa-8f31c07a2b` | Non-empty when present | Provider's message identifier. **Needed to investigate a disputed delivery** |
| `failure_reason` | TEXT + CHECK | No | NULL | One of: `invalid_address`, `provider_error`, `timeout`, `rate_limited_by_provider`, `recipient_blocked`, `message_too_large`; required when failed, bounced, or rejected | Why it failed. Enumerated so failures aggregate into an actionable signal |
| `failure_detail` | TEXT | No | NULL | Non-empty when a failure reason is present | Provider error text |
| `latency_ms` | INTEGER | No | `3100` | ≥ 0 when present | Time from attempt to confirmation. Feeds health monitoring |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `notification` | Operational | Many-to-one | The message |

This entity has **no master data references at all** — the only one in the model. Its subject is entirely a transport concern, and the recipient is reached through the notification. That is worth noting because it makes the entity trivially portable if the delivery mechanism ever changes.

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Notification Service.** One row per attempt |
| **Updated by** | **Notification Service**, and only to advance `delivery_status` from `sent` to `delivered` when the provider confirms asynchronously. The **single documented mutation** in Group G |
| **Read by** | Notification Service (retry logic), Dashboard, Analytics |
| **Archived by** | Platform retention job |
| **Immutable** | **Almost.** Status advances on asynchronous provider confirmation, which arrives after the row is written. No other field ever changes |
| **Append-only** | Yes for rows; status is the sole mutable field |
| **Expires** | Retained 180 days, then purged. **The shortest retention in the model** — delivery mechanics have no long-term audit value once the notification itself is retained |
| **Regenerable** | No. Records what a provider did |

**Business rules**

1. Delivery rows exist only for notifications where `is_suppressed = 0`. A suppressed message was never attempted.
2. `channel` must be enabled for the recipient in master data — `email_enabled` or `whatsapp_enabled`.
3. `attempt_number` is unique per notification and channel, and increments on retry.
4. Retries apply only to `failed` and `timeout` outcomes. `bounced`, `rejected`, and `invalid_address` are **permanent** and must not be retried — retrying a bounced address only generates more bounces.
5. Retry count is capped by a `business_rule` value, not a constant.
6. `delivered` requires `delivered_at`. `failed`, `bounced`, and `rejected` require a `failure_reason`.
7. **When every channel fails permanently for a recipient, the notification is treated as undelivered** and escalation proceeds to the next `escalation_order`. A failed delivery must not silently end the chain.
8. Repeated `invalid_address` failures for one recipient are a **master data quality issue** and should trigger a review of `worker.email` or `worker.phone_number`, not further retries.
9. `provider_reference` should be captured whenever the provider supplies one, because a disputed delivery cannot be investigated without it.
10. Rows are never deleted.

**Example records**

| notification | channel | attempt | attempted_at | status | delivered_at | provider_ref | failure_reason | latency_ms |
|---|---|---|---|---|---|---|---|---|
| `NTF-20260729-00119` | `email` | 1 | 07-29 18:49:32 | `delivered` | 07-29 18:49:35 | `em-4c92a118` | NULL | 3,010 |
| `NTF-20260729-00119` | `whatsapp` | 1 | 07-29 18:49:34 | `delivered` | 07-29 18:49:37 | `wa-8f31c07a2b` | NULL | 3,100 |
| `NTF-20260729-00120` | `email` | 1 | 07-29 18:49:33 | `delivered` | 07-29 18:49:36 | `em-4c92a119` | NULL | 2,880 |
| `NTF-20260729-00120` | `whatsapp` | 1 | 07-29 18:49:35 | `failed` | NULL | NULL | `timeout` | 30,000 |
| `NTF-20260729-00120` | `whatsapp` | 2 | 07-29 18:50:10 | `delivered` | 07-29 18:50:13 | `wa-8f31c07a31` | NULL | 3,240 |
| `NTF-20260726-00088` | `email` | 1 | 07-26 09:14:02 | `bounced` | NULL | `em-4c8f0a02` | `invalid_address` | 1,450 |

- **The supervisor received both channels within five seconds.** Email and WhatsApp both `delivered` with provider references. She acted 21 minutes later, so the routing worked.
- **The Maintenance Manager's WhatsApp timed out and succeeded on retry.** 30-second timeout, retried 35 seconds later, delivered. Rule 4 permits this because `timeout` is transient. Without the retry, one of two recipients would have had email only — and the record shows the recovery rather than hiding it.
- **The bounced email is a master data problem.** `invalid_address` on 26 July means a `worker.email` in master data is stale. Rule 4 forbids retrying it and rule 8 sends it to master data review. **This is the model catching a configuration error that would otherwise be invisible** — the notification looked composed and sent, and nobody received it.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write** |
| **Monitoring Agent** · **Prediction Agent** · **Supervisor Agent** · **Decision Agent** | Do not read |
| **Notification Service** | **Creates every row** and reads them for retry logic and escalation continuation. Sole writer |
| **Dashboard** | Delivery status per notification, so a supervisor can confirm a colleague was actually reached |
| **Analytics** | Delivery success rate by channel, latency distribution, and **`invalid_address` counts as a master data quality metric** |

---

## Group H — Platform & Observability

Three entities that serve the whole pipeline rather than sitting inside it: the dashboard's materialised view, the audit trail, and component health.

---

### E22. `dashboard_snapshot`

**Purpose**

A periodic materialised capture of factory state, so the dashboard renders from one row instead of aggregating across high-volume telemetry, and so historical state can be replayed.

**Business description**

The dashboard needs machine states, OEE components, open alerts, run progress, risk scores, and stock warnings — assembled and current. Computing that live on every page load means aggregating across `machine_sensor_reading`, `cycle_history`, and `production_count` for every viewer.

Two problems make a snapshot the right answer:

**Query cost.** The live aggregation is expensive and repeated per viewer. A snapshot computes it once every five minutes regardless of how many people are looking.

**Historical replay.** *"What did the factory look like at 18:45 yesterday?"* is a question the dashboard should answer, and replaying it from raw data means reconstructing every aggregate for an arbitrary past moment. A stored snapshot makes it a single indexed read — and it is what allows an incident to be reviewed as it appeared at the time rather than as it appears now.

**Fully derived and recomputable**, which makes this the most disposable entity in the model. It can be dropped entirely and rebuilt from its sources, so it carries the shortest retention after `notification_delivery` and no reconciliation obligation.

**The snapshot document** holds the resolved aggregate:

```
machines:   per machine — state, state duration, risk probability,
            open alert count and max severity, hours to next maintenance
lines:      per line — active run, percent complete, rate vs capability,
            schedule variance, availability, performance, quality, OEE
alerts:     open counts by severity
recommendations: active count, awaiting acknowledgement count
inventory:  items at or below reorder point, critical spares below safety stock
```

**Primary key**

`dashboard_snapshot_id` — surrogate `INTEGER`, with a **composite unique constraint on (`snapshot_scope`, `production_line_id`, `machine_id`, `snapshot_at`)**. The constraint makes rebuilds idempotent.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `snapshot_at` | DATETIME | Yes | `2026-07-29 18:45:00+05:30` | Aligned to the snapshot interval | **Event time** of the capture |
| `snapshot_scope` | TEXT + CHECK | Yes | `plant` | One of: `plant`, `production_line`, `machine` | Aggregation level. Plant-level for the overview; finer scopes for drill-down |
| `production_line_id` | INTEGER (FK master) | No | NULL | References `production_line`; required when scope is `production_line` | Scoped line |
| `machine_id` | INTEGER (FK master) | No | NULL | References `machine`; required when scope is `machine` | Scoped machine |
| `snapshot_document` | JSON in TEXT | Yes | see above | Non-empty | **The materialised aggregate** |
| `computed_from_window_seconds` | INTEGER | Yes | `3600` | > 0 | Lookback used for rate and OEE figures. **Stored so the numbers are interpretable** without knowing the job's configuration |
| `generation_duration_ms` | INTEGER | Yes | `640` | ≥ 0 | Computation time. Feeds health monitoring |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `production_line`, `machine` | Master | Many-to-one, optional | Scope |
| Derived from | `machine_operational_status`, `production_progress`, `production_count`, `operational_alert`, `prediction_result`, `inventory_movement` | Operational | Aggregation | Sources |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Dashboard.** On a fixed interval, five minutes in this design |
| **Updated by** | **Dashboard** only, on rebuild. Idempotent by the composite unique constraint |
| **Read by** | Dashboard, Analytics. **Read by no agent** — agents read primary entities, never a presentation aggregate |
| **Archived by** | Platform retention job |
| **Immutable** | Effectively yes; rebuildable by design |
| **Append-only** | Yes in normal operation |
| **Expires** | Retained 90 days at full interval, then downsampled to hourly and purged. **Fully disposable** |
| **Regenerable** | **Yes, entirely**, while its sources survive |

**Business rules**

1. One snapshot per scope and subject per interval, enforced by the composite unique constraint.
2. Snapshots are aligned to fixed interval boundaries so they are comparable and chartable.
3. **No agent reads this entity.** Agents read primary entities. A presentation aggregate in a reasoning path would be an unnecessary dependency and a stale one.
4. `computed_from_window_seconds` is recorded on every row. A rate figure is meaningless without its window.
5. Snapshots may be dropped and rebuilt at any time. There is no reconciliation obligation, because the entity is not a source of truth.
6. A snapshot is never the basis of a recommendation, an event, or a prediction.
7. Downsampling to hourly precedes purge, so long-term trend charts survive.

**Example record**

Plant-scoped snapshot at 18:45 on 29 July, abbreviated:

```
snapshot_at 2026-07-29 18:45:00 · scope plant · window 3600 s · generated in 640 ms

machines:
  MC-0101  running  4h33m  risk 0.68  alerts 2 (max SEV-2)  next maint 37.5 h
  MC-0102  running  4h31m  risk 0.09  alerts 0              next maint 165.0 h
  MC-0103  running  4h25m  risk  —    alerts 0              next maint  —
  MC-0201  running  4h37m  risk 0.11  alerts 0              next maint 162.3 h
  MC-0202  starved  0h05m  risk 0.04  alerts 0              next maint  12 d
  MC-0301  running  4h40m  risk  —    alerts 1 (max SEV-3)  next maint 185.5 h
  MC-0302  idle     0h50m  risk 0.03  alerts 0              next maint 210.0 h
  MC-0401  running  4h43m  risk 0.07  alerts 0              next maint  41 d

lines:
  LN-01  RUN-2026-0714  21.25 %  22.5/24.0 u/h  -93 min   A 100.0  P 93.8  Q 97.1  OEE 91.1
  LN-02  RUN-2026-0716  38.50 %  10.2/11.0 u/h  -41 min   A  98.6  P 92.7  Q 99.0  OEE 90.5
  LN-03  RUN-2026-0715  19.80 %  34.1/36.0 u/h  -55 min   A 100.0  P 94.7  Q 96.4  OEE 91.3
  LN-04  RUN-2026-0717  92.10 % 118.0/120.0 u/h  +8 min   A 100.0  P 98.3  Q 99.6  OEE 97.9

alerts:           SEV-1 0 · SEV-2 1 · SEV-3 2 · SEV-4 0 · SEV-5 0
recommendations:  active 1 · awaiting acknowledgement 1
inventory:        at reorder point 0 · critical spares below safety 0
```

Two observations:

- **`MC-0101` and `MC-0301` show `risk —`, and for opposite reasons.** `MC-0103` has no risk because `machine.is_monitored = 0` — it is not a prediction target. `MC-0301` has no risk because its snapshot was insufficient after the sensor fault. Same display, different causes, both traceable to their source entities. The dashboard shows the absence honestly rather than displaying a stale or invented number.
- **`LN-01` shows the lowest performance component at 93.8 %** and the largest schedule variance at −93 minutes. The line with the degrading bottleneck is measurably the worst-performing line on the floor, from an aggregate computed entirely independently of the vibration data.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **Factory Simulator** | **Does not read or write** |
| **Monitoring Agent** · **Prediction Agent** · **Supervisor Agent** · **Decision Agent** · **Notification Service** | **None read this entity.** Rule 3 — agents read primary data |
| **Dashboard** | **Creates and reads every row.** Sole writer. Live rendering and historical replay |
| **Analytics** | Long-term OEE and availability trends from downsampled snapshots |

---

### E23. `audit_log`

**Purpose**

An append-only record of significant system and human actions across every component. It is the platform's own accountability record, distinct from the operational data it describes.

**Business description**

Operational entities record what happened *in the factory*. This entity records what happened *in the platform*: which component did what, to which row, when, and whether it succeeded.

That distinction matters when something goes wrong with the platform rather than the factory. A recommendation that never reached anybody, a prediction that failed to run, a threshold changed by an unnamed hand — none of those are visible in operational data alone.

**`correlation_id` is the entity's most valuable attribute.** One incident produces rows in a dozen entities across six components. A shared correlation identifier, generated when a triggering event is first detected and carried through every subsequent step, lets the whole pipeline pass be reconstructed with one query. Without it, tracing an incident means joining a dozen tables on timestamps and hoping.

**On the ownership rule.** §6 requires exactly one owning component per entity, and this entity is written by all of them. The exception is resolved by ownership belonging to the **Platform audit interface**: components emit audit entries through one shared write path rather than writing the table directly. Single ownership is preserved where it matters — one code path, one schema authority, one format. The exception is also safe because the entity is strictly append-only and no component ever modifies another's rows.

**Note on scope.** This is not a debug log and not application tracing. It records **significant** actions — state transitions, agent decisions, human actions, configuration changes, and failures. Routine reads, loop iterations, and diagnostic output belong in application logs, which are not data and are not modelled here.

**Primary key**

`audit_log_id` — surrogate `INTEGER`. No business code.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `occurred_at` | DATETIME | Yes | `2026-07-29 18:49:00+05:30` | Not in the future | **Event time** of the action |
| `component` | TEXT + CHECK | Yes | `decision_agent` | One of: `simulator`, `monitoring_agent`, `prediction_agent`, `supervisor_agent`, `decision_agent`, `notification_service`, `dashboard`, `platform` | Which component acted |
| `action_type` | TEXT + CHECK | Yes | `recommendation_generated` | One of: `entity_created`, `entity_updated`, `state_transition`, `decision_made`, `human_action`, `configuration_changed`, `component_error`, `retention_purge`, `reconciliation_run` | What kind of action |
| `entity_name` | VARCHAR(60) | No | `ai_recommendation` | Non-empty when present | Entity affected. NULL for component-level actions with no single subject |
| `entity_id` | INTEGER | No | `31` | > 0 when present | Row affected. **Deliberately not a foreign key** — the audit trail must survive the purge of the row it describes |
| `entity_code` | VARCHAR(32) | No | `REC-20260729-0031` | Non-empty when present | Business code of the row. **Remains readable after the row is purged**, which is the point |
| `actor_worker_id` | INTEGER (FK master) | No | NULL | References `worker` when present | Human who acted. **NULL for system actions** — the distinction between human and machine action is the audit's core question |
| `correlation_id` | VARCHAR(40) | Yes | `inc-20260729-mc0101-a7f3` | Non-empty | **Traces one pipeline pass end to end.** Generated at first detection, carried through every downstream step |
| `outcome` | TEXT + CHECK | Yes | `success` | One of: `success`, `failure`, `denied` | Whether the action succeeded |
| `action_detail` | JSON in TEXT | No | `{"probability": 0.68, "root_cause": "FC-BRG", "tokens": 4052}` | — | Structured particulars. NULL for self-evident actions |
| `error_message` | TEXT | No | NULL | Required when `outcome = 'failure'` | Failure detail |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| Parent | `worker` | Master | Many-to-one, optional | Human actor |
| Soft reference | Any entity, via `entity_name` and `entity_id` | — | — | **Deliberately not a foreign key.** See rule 3 |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Platform audit interface**, on behalf of every component |
| **Updated by** | Nobody. **Immutable** |
| **Read by** | Platform diagnostics, Dashboard, Analytics. **No agent reads it** — an agent reading the audit trail would be reasoning about the platform rather than the factory |
| **Archived by** | Platform retention job |
| **Immutable** | Yes. **Absolutely.** An editable audit log is not an audit log |
| **Append-only** | Yes |
| **Expires** | Retained 1 year online, then archived indefinitely. **Never deleted** — configuration changes and human actions have permanent audit value |
| **Regenerable** | No |

**Business rules**

1. Append-only, always. No component may update or delete an audit row, including its own.
2. Every entity creation, state transition, agent decision, human action, configuration change, and component failure produces an entry. Routine reads do not.
3. **`entity_id` is deliberately not a foreign key.** The audit trail must survive the retention purge of the row it describes, and a foreign key would either block the purge or cascade the audit row away with it. `entity_code` preserves human readability after the row is gone.
4. `correlation_id` is generated once per pipeline pass — at first detection — and carried through every downstream action. This is what makes end-to-end tracing a single query.
5. `actor_worker_id` is NULL for system actions and populated for human ones. The distinction is the audit's central question.
6. `outcome = 'failure'` requires an `error_message`.
7. Configuration changes to master data must be audited with the before and after values in `action_detail`. **A threshold changed without an audit entry is an unexplainable change in platform behavior**, and the tuning cycle depends on knowing who changed what.
8. Retention purges are themselves audited, so the absence of data is explicable.
9. Written through the shared platform interface, never directly. One format, one schema authority.

**Example records**

One pipeline pass, traced by `correlation_id = inc-20260729-mc0101-a7f3`.

| occurred_at | component | action_type | entity | entity_code | actor | outcome |
|---|---|---|---|---|---|---|
| 18:42:00 | `monitoring_agent` | `entity_created` | `operational_alert` | `ALR-20260729-0087` | NULL | `success` |
| 18:42:00 | `monitoring_agent` | `entity_created` | `operational_event` | `EVT-20260729-0412` | NULL | `success` |
| 18:45:00 | `prediction_agent` | `entity_created` | `prediction_feature_snapshot` | `FSN-20260729-04188` | NULL | `success` |
| 18:45:02 | `prediction_agent` | `entity_created` | `prediction_result` | `PDN-20260729-0203` | NULL | `success` |
| 18:46:00 | `supervisor_agent` | `decision_made` | `supervisor_context` | `CTX-20260729-0044` | NULL | `success` |
| 18:49:00 | `decision_agent` | `entity_created` | `ai_recommendation` | `REC-20260729-0031` | NULL | `success` |
| 18:49:35 | `notification_service` | `entity_created` | `notification_delivery` | NULL | NULL | `success` |
| 18:50:05 | `notification_service` | `component_error` | `notification_delivery` | NULL | NULL | `failure` |
| 19:10:00 | `dashboard` | `human_action` | `recommendation_action` | NULL | `EMP-1002` | `success` |
| 06:05:00 | `simulator` | `state_transition` | `machine_operational_status` | `MC-0101` | NULL | `success` |
| 10:35:00 | `simulator` | `entity_updated` | `maintenance_work_record` | `WO-2026-0341` | NULL | `success` |

Plus two unrelated entries showing other audit categories:

| occurred_at | component | action_type | entity | entity_code | actor | outcome |
|---|---|---|---|---|---|---|
| 07-25 11:20 | `platform` | `configuration_changed` | `alert_threshold_rule` | `ATP-VMC-TIGHT`/`PRM-VIB` | `EMP-1011` | `success` |
| 07-30 02:00 | `platform` | `retention_purge` | `machine_sensor_reading` | NULL | NULL | `success` |

- **Eleven rows reconstruct the entire incident**, from first detection at 18:42 to work order closure at 10:35 the next day, across six components and 16 hours. One `correlation_id`, one query. **That is the value of the attribute** — without it this trace would require joining eleven entities on approximate timestamps.
- **The one human action is clearly marked.** `EMP-1002` at 19:10 with a populated `actor_worker_id`. Every other row has NULL. The human-in-the-loop moment is unambiguous in a trace of otherwise autonomous activity.
- **The WhatsApp timeout appears as `component_error`** at 18:50:05, matching the failed delivery in §7. The platform's own failures sit in the same trace as its successes.
- **The threshold change on 25 July is attributed to `EMP-1011`** — Suresh Iyer, the mechanical engineer, tightening the vibration limit on `ATP-VMC-TIGHT` four days before the incident. Rule 7 makes that traceable. **If the profile had been too tight and produced false positives, this row is who to ask** — and it is also the row that would explain a sudden change in alert volume.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **All eight components** | **Write** through the shared platform audit interface |
| **No agent reads it** | An agent reading the audit trail would be reasoning about the platform rather than the factory. Deliberate boundary |
| **Dashboard** | Renders the trace for an incident, and configuration change history |
| **Analytics** | Pipeline latency per stage, error rates by component, human response patterns, and configuration change attribution |

---

### E24. `system_health_status`

**Purpose**

Current liveness, lag, and error state of each pipeline component. It answers *"is the platform working?"* — a question no operational entity can answer, because a stalled component produces no data at all.

**Business description**

Every other entity records what the platform did. This one records whether it is still doing it.

The distinction is essential because **a stalled component is silent, and silence looks exactly like a healthy quiet period.** If the Prediction Agent stops running, no predictions appear. Nothing errors, nothing alerts, and the dashboard shows no risk on any machine — which is indistinguishable from a factory in perfect health. Without an explicit heartbeat, the platform's most dangerous failure mode is invisible.

One row per component, overwritten in place. Around eight rows that never grow.

**`processing_lag_seconds` is the most operationally useful attribute.** A component can be alive and falling behind. The Monitoring Agent processing readings 400 seconds old is technically healthy and practically useless — a 400-second-old threshold breach has already become a failure. Lag catches the degradation that liveness misses.

**The critical interaction with telemetry staleness.** `machine_operational_status.last_reading_at` going stale looks like a machine problem and is often a pipeline problem. §E2 rule 9 routes that condition here rather than raising a machine event, and the reason is important: **a telemetry outage misdiagnosed as a machine fault produces meaningless alerts on every machine at once.** Distinguishing the two is what this entity is for.

**Primary key**

`system_health_status_id` — surrogate `INTEGER`, with a **unique constraint on `component`** enforcing one row per component.

**Attributes**

| Attribute | Type | Req | Example | Validation | Purpose & business meaning |
|---|---|---|---|---|---|
| `component` | TEXT + CHECK | Yes | `prediction_agent` | **Unique**; same vocabulary as `audit_log.component` | The component. One row each, for the platform's life |
| `status` | TEXT + CHECK | Yes | `healthy` | One of: `healthy`, `degraded`, `failed`, `stopped` | Current state. `degraded` means running but lagging or erroring intermittently |
| `last_heartbeat_at` | DATETIME | Yes | `2026-07-29 18:45:02+05:30` | Not in the future | **Liveness.** A stale heartbeat is the primary failure signal |
| `last_successful_run_at` | DATETIME | No | `2026-07-29 18:45:02+05:30` | ≤ `last_heartbeat_at` | Last completed work cycle. **Distinct from heartbeat** — a component can be alive and failing every cycle |
| `consecutive_failure_count` | INTEGER | Yes | `0` | ≥ 0 | Failures since the last success. Drives the transition to `degraded` and then `failed` |
| `processing_lag_seconds` | INTEGER | No | `2` | ≥ 0 when present | How far behind real time the component is. **Catches degradation that liveness misses** |
| `pending_backlog_count` | INTEGER | No | `0` | ≥ 0 when present | Unprocessed items queued. A growing backlog is a capacity problem before it is an outage |
| `last_error_at` | DATETIME | No | NULL | ≤ `last_heartbeat_at` when present | Most recent error. NULL if none since start |
| `last_error_message` | TEXT | No | NULL | Non-empty when `last_error_at` is present | Error detail |
| `metrics_document` | JSON in TEXT | No | `{"inferences_last_hour": 96, "mean_inference_ms": 32}` | — | Component-specific metrics. Kept flexible because each component measures different things |

**Relationships**

| Direction | Related entity | Kind | Cardinality | Meaning |
|---|---|---|---|---|
| — | None | — | — | **The only entity in the model with no relationships at all.** Its subject is a software component, not a factory object |

**Lifecycle**

| Aspect | Detail |
|---|---|
| **Created by** | **Platform**, once per component at first start |
| **Updated by** | **Platform**, on each component heartbeat. Each component reports its own status through the shared interface |
| **Read by** | Platform monitoring, Dashboard, Analytics. **Optionally read by the Supervisor Agent** to avoid escalating on data from a lagging pipeline |
| **Archived by** | Never. Row count is fixed at the number of components |
| **Immutable** | No. **Mutable current-state** |
| **Append-only** | No |
| **Expires** | No |
| **Regenerable** | **Partially.** Current status is re-established on the next heartbeat; historical health is recoverable from `audit_log` |

**Business rules**

1. Exactly one row per component, enforced by the unique constraint.
2. A heartbeat older than a component-specific threshold moves `status` to `failed` regardless of what the row last reported. **The platform must not trust a component's self-assessment when it has stopped reporting.**
3. `consecutive_failure_count` above a threshold moves `status` to `degraded`, and further failures to `failed`. Thresholds are `business_rule` values.
4. `processing_lag_seconds` above a component-specific threshold moves `status` to `degraded` even when every cycle is succeeding. Correct-but-late is a real failure mode.
5. `status = 'stopped'` is a **deliberate** halt, distinct from `failed`. A maintenance window is not an outage, and conflating them would cause false alarms.
6. **Telemetry staleness is diagnosed here, not as a machine event.** A monitored machine with no readings for several sampling intervals indicates a simulator or ingestion problem, and §E2 rule 9 routes it accordingly.
7. The Supervisor Agent should suppress escalation when the Prediction Agent is `failed` or severely lagging. **Escalating on stale predictions is worse than not escalating**, because it produces confident recommendations from outdated evidence.
8. Health degradation of any component is auditable in `audit_log`, so the history survives the overwrite.
9. `status = 'failed'` for a pipeline component is itself a notifiable condition, delivered through the ordinary notification path with `notification_type = 'system_health'`.

**Example records**

Component health at 18:46 on 29 July.

| component | status | last_heartbeat_at | last_successful_run_at | consec_failures | lag_s | backlog | last_error |
|---|---|---|---|---|---|---|---|
| `simulator` | `healthy` | 18:46:00 | 18:46:00 | 0 | 0 | 0 | NULL |
| `monitoring_agent` | `healthy` | 18:45:55 | 18:45:55 | 0 | 5 | 0 | NULL |
| `prediction_agent` | `healthy` | 18:45:02 | 18:45:02 | 0 | 2 | 0 | NULL |
| `supervisor_agent` | `healthy` | 18:46:00 | 18:46:00 | 0 | 1 | 0 | NULL |
| `decision_agent` | `healthy` | 18:45:12 | 18:45:12 | 0 | 3 | 0 | NULL |
| `notification_service` | `degraded` | 18:50:12 | 18:49:36 | 1 | 8 | 1 | `whatsapp provider timeout after 30000 ms` |
| `dashboard` | `healthy` | 18:45:00 | 18:45:00 | 0 | 0 | 0 | NULL |
| `platform` | `healthy` | 18:46:00 | 18:46:00 | 0 | 0 | 0 | NULL |

- **The Notification Service is `degraded`, not `failed`.** One WhatsApp timeout, one backlog item, and a successful email on the same notification. It is working with reduced reliability, and rule 3 makes that distinction. The retry in §E21 succeeded 35 seconds later and this row would return to `healthy` on the next heartbeat.
- **Lag is single-digit seconds across the pipeline.** The Monitoring Agent is 5 seconds behind, the Prediction Agent 2. Both are healthy and would remain so until lag crossed their thresholds. **These numbers are what let the Supervisor Agent trust the prediction it is escalating on**, per rule 7.
- **`last_heartbeat_at` and `last_successful_run_at` differ for the Notification Service** — 18:50:12 against 18:49:36. Alive at 18:50:12, last succeeded at 18:49:36. Rule 2's insistence on both columns is what makes that gap visible; a single "last seen" timestamp would show a healthy component.

**FactoryFlow AI consumers**

| Consumer | How it uses this entity |
|---|---|
| **All eight components** | **Report** their own status through the shared platform interface |
| **Supervisor Agent** | **The only agent that reads it.** Suppresses escalation when the Prediction Agent is failed or badly lagging, so recommendations are never built on stale predictions |
| **Notification Service** | Sends `system_health` notifications when a component fails |
| **Dashboard** | Pipeline health panel — the operator's answer to "is the platform working?" |
| **Analytics** | Component uptime, lag distributions, and error rates over time |

---

# Part III — Ownership, Simulator Contract, Retention

## 6. Data Ownership

### 6.1 The rule

> **Every operational entity has exactly one owning component. That component is the only one permitted to write it. No entity has shared ownership.**

This is not a stylistic preference. Three consequences follow from it, and each is load-bearing.

**Provenance is recoverable.** When a row is wrong, exactly one component produced it. With shared writes, diagnosing a bad row means determining which of several writers was responsible — and if they interleave, possibly none of them alone.

**Single responsibility becomes verifiable.** `PROJECT_OVERVIEW.md` §16.2 assigns each component exactly one responsibility. Ownership is how that claim is checked: if the Monitoring Agent could write predictions, its responsibility would have quietly expanded regardless of what the documentation said.

**Boundaries stay honest.** The simulator writes reality and never reads the platform's interpretation of it. The Monitoring Agent detects and never predicts. Those boundaries are enforced by write permissions, not by discipline.

### 6.2 Ownership map

| # | Entity | Owner | Writes | Reads by others |
|---|---|---|---|---|
| 1 | `machine_sensor_reading` | **Factory Simulator** | Insert only | 6 components |
| 2 | `machine_operational_status` | **Factory Simulator** | Insert + update | 6 components |
| 3 | `machine_state_transition` | **Factory Simulator** | Insert only | 6 components |
| 4 | `production_run` | **Factory Simulator** | Insert + update | 7 components |
| 5 | `production_progress` | **Factory Simulator** | Insert only | 5 components |
| 6 | `production_count` | **Factory Simulator** | Insert + rebuild | 4 components |
| 7 | `cycle_history` | **Factory Simulator** | Insert only | 4 components |
| 8 | `quality_inspection_result` | **Factory Simulator** | Insert only | 6 components |
| 9 | `scrap_record` | **Factory Simulator** | Insert only | 6 components |
| 10 | `inventory_movement` | **Factory Simulator** | Insert only | 6 components |
| 11 | `maintenance_work_record` | **Factory Simulator** | Insert + update | 7 components |
| 12 | `machine_maintenance_activity` | **Factory Simulator** | Insert only | 4 components |
| 13 | `operational_event` | **Monitoring Agent** | Insert only | 6 components |
| 14 | `operational_alert` | **Monitoring Agent** | Insert + update | 6 components |
| 15 | `prediction_feature_snapshot` | **Prediction Agent** | Insert only | 3 components |
| 16 | `prediction_result` | **Prediction Agent** | Insert only | 5 components |
| 17 | `supervisor_context` | **Supervisor Agent** | Insert only | 3 components |
| 18 | `ai_recommendation` | **Decision Agent** | Insert only | 3 components |
| 19 | `recommendation_action` | **Dashboard** | Insert only | 5 components |
| 20 | `notification` | **Notification Service** | Insert only | 2 components |
| 21 | `notification_delivery` | **Notification Service** | Insert + status update | 2 components |
| 22 | `dashboard_snapshot` | **Dashboard** | Insert + rebuild | 1 component |
| 23 | `audit_log` | **Platform** (shared audit interface) | Insert only | 2 components |
| 24 | `system_health_status` | **Platform** | Insert + update | 3 components |

### 6.3 Ownership by component

| Component | Owns | Entities |
|---|---|---|
| **Factory Simulator** | 12 | All of Groups A, B, and C. Everything that represents the factory itself |
| **Monitoring Agent** | 2 | `operational_event`, `operational_alert` |
| **Prediction Agent** | 2 | `prediction_feature_snapshot`, `prediction_result` |
| **Supervisor Agent** | 1 | `supervisor_context` |
| **Decision Agent** | 1 | `ai_recommendation` |
| **Notification Service** | 2 | `notification`, `notification_delivery` |
| **Dashboard** | 2 | `recommendation_action`, `dashboard_snapshot` |
| **Platform** | 2 | `audit_log`, `system_health_status` |

The distribution mirrors the pipeline exactly. The simulator owns 12 entities because it generates the entire factory; each agent owns only what it produces. **The Supervisor and Decision Agents own one entity each**, which is the clearest possible statement that their responsibilities are narrow.

### 6.4 Two documented exceptions, and why they are safe

**`recommendation_action` is owned by the Dashboard, not the Decision Agent.**

The row records a *human* decision, and the Dashboard is the surface where a human makes it. Assigning it to the Decision Agent would mean the component that produces advice also records the verdict on its own advice — which is exactly the conflict the human-in-the-loop principle exists to avoid. Ownership follows the actor, not the subject.

**`audit_log` is written by all eight components.**

Ownership belongs to the **Platform audit interface**: a single shared write path through which components emit entries. They do not write the table directly. Single ownership is preserved where it matters — one code path, one schema authority, one format — and the exception is safe for three reasons:

1. The entity is strictly append-only. No component ever modifies another's rows.
2. Every row carries `component`, so provenance is explicit rather than inferred.
3. There is no state to corrupt. An audit entry is a fact, not a position in a lifecycle.

### 6.5 The critical read boundary

Ownership governs writes. One **read** restriction is equally important:

> **The Factory Simulator reads no entity that any agent produces.**

The simulator never reads `operational_event`, `operational_alert`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `ai_recommendation`, `recommendation_action`, `notification`, `notification_delivery`, or `dashboard_snapshot`.

The reason is that violating it would make the entire platform untestable. If the simulator could see that a failure had been predicted, its subsequent behavior could be influenced by that prediction — and every accuracy measurement would be circular. The simulator generates reality; the platform interprets it; the two must not touch.

The one apparent exception is not one. The simulator creates `maintenance_work_record` rows carrying `triggering_recommendation_id`, which looks like reading a recommendation. In fact the reference arrives through the **human decision** recorded in `recommendation_action` — the simulator is modelling a manager acting on advice, which is a real-world causal path and not the simulator reading the platform's mind.

---

## 7. Simulator Contract

This section is mandatory and definitive. For each of the 24 entities it states exactly what the Factory Simulator does, and which component writes it if the simulator does not.

### 7.1 Created and updated by the Simulator

| Entity | Simulator behaviour | Update pattern |
|---|---|---|
| `machine_sensor_reading` | **Creates** on each parameter's sampling interval | Insert only, never updated |
| `machine_operational_status` | **Creates** once per machine, then **updates** continuously | Update on every state change, reading, and cycle |
| `machine_state_transition` | **Creates** on every state change | Insert only, paired with the status update in one unit of work |
| `production_run` | **Creates** when scheduled, then **updates** through the lifecycle | Status advances; actual timestamps set once each |
| `production_progress` | **Creates** on a 15-minute interval per active run | Insert only |
| `production_count` | **Creates** at each 30-minute interval close | Insert, with idempotent rebuild permitted |
| `cycle_history` | **Creates** on each cycle completion | Insert only |
| `quality_inspection_result` | **Creates** on inspection activity | Insert only |
| `scrap_record` | **Creates** on scrap disposition | Insert only |
| `inventory_movement` | **Creates** on every stock transaction | Insert only |
| `maintenance_work_record` | **Creates** on job raise, then **updates** through the lifecycle | Status advances; counters update on closure |
| `machine_maintenance_activity` | **Creates** as each step occurs | Insert only |

Twelve entities, all of Groups A, B, and C. This is the simulator's whole job: **generate a factory.**

### 7.2 Never touched by the Simulator

The simulator neither reads nor writes any of these:

| Entity | Written by | Why the simulator must not touch it |
|---|---|---|
| `operational_event` | Monitoring Agent | The platform's detection, not the factory's reality |
| `operational_alert` | Monitoring Agent | A managed case, which is a platform concept |
| `prediction_feature_snapshot` | Prediction Agent | Derived from reality, not part of it |
| `prediction_result` | Prediction Agent | **Reading this would make every accuracy measurement circular** |
| `supervisor_context` | Supervisor Agent | An escalation decision about the factory |
| `ai_recommendation` | Decision Agent | Advice about the factory |
| `recommendation_action` | Dashboard | A human decision, outside the simulation |
| `notification` | Notification Service | Communication about the factory |
| `notification_delivery` | Notification Service | Transport mechanics |
| `dashboard_snapshot` | Dashboard | A presentation aggregate |
| `audit_log` | Platform | The simulator **emits** audit entries through the platform interface but does not own the entity |
| `system_health_status` | Platform | The simulator **reports** its own health through the platform interface but does not own the entity |

### 7.3 Created by each agent

| Component | Creates | Updates |
|---|---|---|
| **Monitoring Agent** | `operational_event`, `operational_alert` | `operational_alert` — severity, event count, event times, lifecycle status |
| **Prediction Agent** | `prediction_feature_snapshot`, `prediction_result` | Nothing. Both are immutable |
| **Supervisor Agent** | `supervisor_context` | Nothing. Immutable |
| **Decision Agent** | `ai_recommendation` | Nothing. Immutable |
| **Notification Service** | `notification`, `notification_delivery` | `notification_delivery.delivery_status` only, on asynchronous provider confirmation |
| **Dashboard** | `recommendation_action`, `dashboard_snapshot` | `dashboard_snapshot` on rebuild only |
| **Platform** | `audit_log`, `system_health_status` | `system_health_status` on each heartbeat |

### 7.4 Master data the Simulator must respect

The simulator does not invent factory behaviour. Every characteristic it generates is governed by frozen master data:

| Simulator behaviour | Governed by |
|---|---|
| Which machines emit telemetry | `machine.is_monitored`, `machine.lifecycle_status` |
| Which parameters each machine reports | `machine_type_parameter` |
| Sampling frequency per parameter | `machine_type_parameter.sampling_interval_seconds` |
| Healthy value ranges | `machine_type_parameter` nominal, normal min and max |
| Degradation direction and rate | `machine_type_parameter.expected_drift_direction`, `machine_type.mtbf_hours` |
| Which failures are possible per machine type | `machine_type_failure_mode` |
| Failure telemetry signatures and warning periods | `machine_type_failure_mode` leading indicator and warning period |
| Repair durations | `machine_type_failure_mode.estimated_repair_duration_minutes` |
| Production rates | `product_line_capability.cycle_time_seconds`, `max_hourly_output_units` |
| Material consumption | `bill_of_materials` quantity per unit and scrap allowance |
| Maintenance intervals | `machine_maintenance_schedule` interval basis and value |
| Part retrieval times | `inventory_location.average_retrieval_time_minutes` |
| Supplier replenishment lead times | `supplier.standard_lead_time_days` |
| Shift patterns and working calendar | `shift`, `plant.operating_days_per_week` |
| Ambient thermal context | `plant_area.nominal_ambient_temp_c` |

**The simulator has no free parameters of its own.** Everything it does is traceable to a master data row, which is what makes generated scenarios reproducible and defensible. A reviewer asking "why does this machine fail every 4,200 hours?" gets a data answer, not a code answer.

### 7.5 What the Simulator must never do

| Prohibited | Reason |
|---|---|
| Read any agent-produced entity | Would make accuracy measurement circular. §6.5 |
| Write to an entity it does not own | Breaks provenance |
| Generate telemetry for unmonitored or decommissioned machines | Contradicts master data |
| Generate a parameter not declared for a machine's type | Contradicts master data |
| Choose its own sampling interval, degradation rate, or repair duration | All are master data values |
| Produce values outside a parameter's physical range **except** when deliberately simulating a sensor fault | Physical impossibility must be intentional and flagged |
| Skip the `quality_flag` when generating a faulty reading | An unflagged bad reading would silently corrupt the feature set |
| Create an `operational_event` to represent a failure | Failures manifest as telemetry and state; **detection is the Monitoring Agent's job** |

The last row is the one most easily got wrong. When the simulator injects a bearing failure scenario, it must express that as **drifting vibration, rising temperature, lengthening cycles, and dimensional defects** — never as a pre-made event. If the simulator could create events directly, the Monitoring Agent's detection logic would never be exercised, and the platform's core capability would be untested.

---

## 8. Retention Policy

### 8.1 Why retention is mandatory here

Master data holds roughly 245 rows indefinitely. Operational data adds approximately **95,000 rows per day**, dominated by telemetry. Without a retention policy the database grows without bound, query performance degrades, and the platform eventually fails from success.

Retention is also a **design** concern rather than an operations concern, because what may be purged depends on what is regenerable — and that is a property of the model.

### 8.2 Full retention table

| # | Entity | Online | Then | Class | Mutability | Regenerable |
|---|---|---|---|---|---|---|
| 1 | `machine_sensor_reading` | 90 days | Downsample to hourly, purge raw | Streaming | Immutable | **No** |
| 2 | `machine_operational_status` | Forever | — | Current-state | Mutable | **Yes**, from history |
| 3 | `machine_state_transition` | 2 years | Archive indefinitely | Append-only | Immutable | **No** |
| 4 | `production_run` | 2 years | Archive indefinitely | Lifecycle | Mutable | **No** |
| 5 | `production_progress` | 180 days | Purge; keep terminal snapshot per run | Snapshot | Immutable | **Yes** |
| 6 | `production_count` | 2 years | Archive indefinitely | Aggregated | Immutable | **Yes**, until cycles purge |
| 7 | `cycle_history` | 90 days | Purge | Append-only | Immutable | **No** |
| 8 | `quality_inspection_result` | 3 years | Archive indefinitely | Append-only | Immutable | **No** |
| 9 | `scrap_record` | 3 years | Archive indefinitely | Append-only | Immutable | **No** |
| 10 | `inventory_movement` | 3 years | Archive indefinitely | Append-only | Immutable | **No** |
| 11 | `maintenance_work_record` | **5 years** | Archive indefinitely | Lifecycle | Mutable | **No** |
| 12 | `machine_maintenance_activity` | 5 years | Archive with parent | Append-only | Immutable | **No** |
| 13 | `operational_event` | 1 year | Archive indefinitely | Append-only | Immutable | Partially |
| 14 | `operational_alert` | 2 years | Archive indefinitely | Lifecycle | Mutable | **No** |
| 15 | `prediction_feature_snapshot` | 180 days | Purge | Append-only | Immutable | **Yes**, until telemetry purge |
| 16 | `prediction_result` | 2 years | Archive indefinitely | Append-only | Immutable | **Yes**, while snapshot survives |
| 17 | `supervisor_context` | Escalated 2 years · **suppressed 180 days** | Archive escalated | Append-only | Immutable | **No** |
| 18 | `ai_recommendation` | 3 years | Archive indefinitely | Append-only | Immutable | **No** |
| 19 | `recommendation_action` | 3 years | Archive indefinitely | Append-only | Immutable | **No** |
| 20 | `notification` | 1 year | Archive | Append-only | Immutable | **No** |
| 21 | `notification_delivery` | **180 days** | Purge | Append-only | Status only | **No** |
| 22 | `dashboard_snapshot` | 90 days | Downsample hourly, purge | Snapshot | Rebuildable | **Yes** |
| 23 | `audit_log` | 1 year | Archive **indefinitely, never deleted** | Append-only | Immutable | **No** |
| 24 | `system_health_status` | Forever | — | Current-state | Mutable | Partially |

### 8.3 The retention hierarchy, and why it is shaped this way

Retention rises with **evidentiary value**, not with volume:

| Retention | Entities | Rationale |
|---|---|---|
| **Forever** | `machine_operational_status`, `system_health_status` | Fixed row count. Nothing to purge |
| **5 years** | `maintenance_work_record`, `machine_maintenance_activity` | **Longest.** Asset maintenance history outlives everything else and is needed for warranty, reliability analysis, and resale |
| **3 years** | `quality_inspection_result`, `scrap_record`, `inventory_movement`, `ai_recommendation`, `recommendation_action` | Quality and material records for traceability; recommendations and decisions as the platform's accountability record |
| **2 years** | `machine_state_transition`, `production_run`, `production_count`, `operational_alert`, `prediction_result` | Downtime, production, and prediction accuracy analysis over multiple annual cycles |
| **1 year** | `operational_event`, `notification`, `audit_log` | Detection and communication history. Audit archived permanently |
| **180 days** | `production_progress`, `prediction_feature_snapshot`, `notification_delivery`, suppressed contexts | High volume, low long-term value, mostly recomputable |
| **90 days** | `machine_sensor_reading`, `cycle_history`, `dashboard_snapshot` | **Highest volume.** Downsampled before purge where trends matter |

### 8.4 The three purge rules that protect the evidence chain

Retention cannot be applied entity by entity in isolation. The explainability contract requires that a recommendation remain fully traceable, and a naive purge would break that chain.

**Rule 1 — Citation exemption.** Any row **cited by a retained `ai_recommendation`** is exempt from purge for as long as that recommendation survives. This applies to the events in its supporting evidence, its prediction, its feature snapshot, its supervisor context, and the readings the events reference.

Consequence: a three-year-old recommendation still resolves to its original evidence, even though sibling telemetry from the same day was purged after 90 days. **A recommendation that cannot show its evidence is not explainable**, and retention must not be allowed to create that condition.

**Rule 2 — Aggregate before purge.** A high-volume entity may only be purged after its aggregate has been written and verified:

```
cycle_history (90 d)         →  production_count written and reconciled    →  purge cycles
machine_sensor_reading (90 d) →  hourly downsample written and verified    →  purge raw readings
dashboard_snapshot (90 d)     →  hourly downsample written                →  purge fine-grained
```

Purging before aggregation would lose the data permanently. The order is not optional.

**Rule 3 — Purge is audited.** Every retention run writes an `audit_log` entry with `action_type = 'retention_purge'` recording the entity, the cut-off, and the row count. **The absence of data must itself be explicable** — otherwise a purged range is indistinguishable from a period when the platform was not running.

### 8.5 Derived and recomputable entities

Five entities are declared derived. This matters because a derived entity can be dropped and rebuilt, which makes an aggregation defect a recoverable bug rather than data loss.

| Entity | Derived from | Rebuild safe while |
|---|---|---|
| `production_count` | `cycle_history`, `machine_state_transition` | Cycles survive — 90 days |
| `production_progress` | `production_count`, `machine_state_transition` | Counts survive — 2 years |
| `dashboard_snapshot` | Six operational entities | Sources survive |
| `machine_operational_status` | `machine_state_transition`, `cycle_history`, `maintenance_work_record` | History survives |
| `prediction_feature_snapshot` | `machine_sensor_reading`, `cycle_history`, and others | Telemetry survives — 90 days |

**Two of these become non-regenerable over time**, and the design accounts for it. `production_count` is derived for its first 90 days and becomes a source of truth once cycles are purged. `prediction_feature_snapshot` is regenerable for 90 days and permanently fixed thereafter — which is precisely why its 180-day retention deliberately exceeds telemetry's 90.

---

## 9. Data Quality & Reconciliation

### 9.1 Why operational data quality is different

A master data defect is a wrong configuration value, found by review. An operational data defect is a wrong fact among millions, found only if something checks for it.

Three classes matter here:

| Class | Example | Detection |
|---|---|---|
| **Referential** | An event referencing a purged reading | Foreign key constraints, plus purge rules |
| **Arithmetic** | A movement balance that does not follow from its predecessor | Self-checking chains |
| **Cross-entity** | A maintained counter diverging from its source | Scheduled reconciliation |

### 9.2 Self-checking invariants

Several entities are designed so that a defect is **locatable rather than merely detectable**. This is deliberate, and it is cheap:

| Invariant | Entity | What it catches |
|---|---|---|
| Balance chain: each balance = previous + delta | `inventory_movement` | The exact transaction where the ledger broke |
| `cycles_completed` = good + scrap + rework | `production_count` | Aggregation defects |
| `cycle_time_seconds` = end − start | `cycle_history` | Timestamp or duration corruption |
| `duration_in_previous_state_seconds` = gap to previous transition | `machine_state_transition` | Missing or out-of-order transitions |
| `duration_from_previous_seconds` = gap to previous activity | `machine_maintenance_activity` | Missing timeline entries |
| `pass_count` + `fail_count` = `sample_size` | `quality_inspection_result` | Inspection recording errors |
| `response_time_minutes` = actioned − generated | `recommendation_action` | Clock or timezone defects |
| Cumulative quantities non-decreasing | `production_progress` | Snapshot ordering defects |

### 9.3 Reconciliation rules

Four maintained totals exist for performance and must be reconciled against their sources on a schedule. Each was justified where it appears; each carries an obligation.

| Maintained value | Entity | Reconcile against | Frequency | On divergence |
|---|---|---|---|---|
| `accumulated_cycle_count` | `machine_operational_status` | Count of `cycle_history` per machine | Daily | Rebuild from history; raise a data quality incident |
| `accumulated_operating_hours` | `machine_operational_status` | Sum of `running` durations in `machine_state_transition` | Daily | Rebuild from history; raise an incident |
| `open_alert_count` | `machine_operational_status` | Count of open `operational_alert` per machine | Hourly | Recount from alerts |
| `event_count` | `operational_alert` | Count of `operational_event` per alert | Hourly | Recount from events |

Two further cross-entity consistency checks:

| Check | Entities | Catches |
|---|---|---|
| `machine_state_at_reading` agrees with the transition log at `recorded_at` | `machine_sensor_reading` vs `machine_state_transition` | Simulator ordering defects in the declared denormalisation |
| Alert subject keys match those of its events | `operational_alert` vs `operational_event` | Correlation defects in the declared denormalisation |

**Both declared denormalisations in the model are reconciled.** That is the condition under which the denormalisation was accepted: a performance shortcut is permissible only if a check exists to prove it has not drifted. `machine_state_at_reading` and the alert's subject keys are the two, and both appear above.

### 9.4 Cross-entity consistency requirements

| Requirement | Why it matters |
|---|---|
| A `scrap` cycle outcome has a corresponding `scrap_record` | Otherwise scrap is counted in one place and not the other |
| An inspection with `disposition = 'scrap'` has a corresponding `scrap_record` | A finding without its material consequence |
| Scrap consuming material has a corresponding `inventory_movement` | Otherwise stock overstates what is physically present |
| A work record in `in_progress` has a `machine_state_transition` to a down state | Otherwise a machine is being repaired while apparently running |
| `part_collected` has a corresponding `issue_maintenance` movement | Otherwise a part left the store without being recorded |
| A `predictive` work record references a recommendation | The definition of predictive work |
| A closed corrective or predictive record has a confirmed failure category | Otherwise the accuracy loop has a gap |
| Every escalated context has exactly one recommendation | An escalation that produced nothing is a Decision Agent failure |
| A non-suppressed notification has at least one delivery row | Otherwise a message was composed and never attempted |
| A `SEV-1` recommendation has at least one non-suppressed notification | **A critical recommendation reaching nobody is the platform's worst failure** |

### 9.5 Data quality as an operational signal

Three quality measures are not merely hygiene — they are inputs the platform reasons with:

| Measure | Where it is used |
|---|---|
| `machine_sensor_reading.quality_flag` | Excludes invalid readings from features, and detects instrument failure |
| `prediction_feature_snapshot.data_completeness_pct` | Gates inference entirely, and tells the Supervisor Agent how much to trust a prediction |
| `notification_delivery.failure_reason = 'invalid_address'` | Surfaces stale contact details in **master data** |

The third is worth noting: an operational data quality signal that points at a master data defect. The two layers check each other, which is only possible because they are cleanly separated in the first place.

---

# Part IV — Lifecycles & Pipelines

## 10. Event Lifecycle

The complete life of a detected condition, from first observation to permanent archive. The lifecycle is split across two entities because **facts cannot be revised but cases must be**: `operational_event` holds immutable observations, `operational_alert` holds the mutable case.

### 10.1 The seven stages

```
                    ┌─────────────────────────────────────────┐
   telemetry ──────►│  1. CREATION                            │
   cycles           │  Monitoring Agent detects a condition    │
   quality          │  → operational_event  (immutable)       │
   inventory        │  → operational_alert  (found or created)│
                    └────────────────┬────────────────────────┘
                                     ▼
                    ┌─────────────────────────────────────────┐
   more events      │  2. UPDATE                              │
   arrive ─────────►│  Alert absorbs them: event_count++,     │
                    │  last_event_at, severity may rise       │
                    │  Events themselves never change         │
                    └────────────────┬────────────────────────┘
                                     ▼
                    ┌─────────────────────────────────────────┐
                    │  3. ACKNOWLEDGEMENT                     │
   human ──────────►│  A human confirms receipt within        │
                    │  max_acknowledgement_minutes            │
                    └──────┬──────────────────────┬───────────┘
                    ack'd  │                      │ window elapsed
                           ▼                      ▼
                    ┌─────────────┐   ┌─────────────────────────┐
                    │             │   │  4. ESCALATION          │
                    │             │◄──│  Next escalation_order  │
                    │             │   │  recipient notified     │
                    └──────┬──────┘   └─────────────────────────┘
                           ▼
                    ┌─────────────────────────────────────────┐
                    │  5. RESOLUTION                          │
                    │  Condition ceases or repair completes   │
                    │  resolution_type recorded               │
                    └────────────────┬────────────────────────┘
                                     ▼
                    ┌─────────────────────────────────────────┐
                    │  6. CLOSURE                             │
                    │  Signed off, resolution_note required   │
                    └────────────────┬────────────────────────┘
                                     ▼
                    ┌─────────────────────────────────────────┐
                    │  7. RETENTION                           │
                    │  Events 1 yr, alerts 2 yr, then archive │
                    │  Cited rows exempt while REC survives   │
                    └─────────────────────────────────────────┘
```

### 10.2 Stage 1 — Creation

**Trigger.** The Monitoring Agent detects a condition on one of five paths: a threshold or rate breach in telemetry, a cycle or output deviation, a quality failure rate, an inventory threshold, or a data quality problem.

**Ordering matters, and it is deliberate.** In one unit of work the agent:

1. Computes the `correlation_key` from subject and category.
2. Looks for an **open** alert with that key.
3. If none exists, creates the alert. If one exists, reuses it.
4. Writes the event **with the alert already known**.

Step 4 is why `operational_event.operational_alert_id` is mandatory and set at insert. Attaching the alert afterwards would require updating the event, and the immutability guarantee would be fiction rather than fact.

**What is captured.** The observed value, the threshold in force at that moment (§3.5), the direction, the actual sustained duration, the triggering reading, and a human-readable note. The event is self-contained evidence — readable without joining to master data, which matters because it is quoted into LLM prompts and notification bodies.

**Suppression at creation.** Events are not raised for machines in `setup`, `down_planned`, or with an in-progress work record. Abnormal behaviour during a repair is expected, and alerting on it is pure noise.

### 10.3 Stage 2 — Update

**The event never changes. The alert does.**

As further events arrive with the same `correlation_key`, the alert absorbs them:

| Alert field | Change |
|---|---|
| `event_count` | Incremented |
| `last_event_at` | Advanced |
| `current_severity_level_id` | May rise if a critical limit is later breached |
| `initial_severity_level_id` | **Never changes** — so the deterioration path stays visible |

**Severity is monotonic upward.** An alert may escalate from `SEV-2` to `SEV-1`; it never de-escalates, because a quiet interval does not mean the underlying condition improved, and silently lowering severity would hide deterioration.

In the worked incident this stage absorbed **34 events over eleven hours into one case.** That number is the whole justification for the two-entity split: 34 notifications to one supervisor overnight would have destroyed the channel's credibility by the fifth.

### 10.4 Stage 3 — Acknowledgement

**The window comes from master data**, never from a constant: `failure_severity_level.max_acknowledgement_minutes` — 15 minutes for `SEV-1`, 30 for `SEV-2`, 120 for `SEV-3`, and none required below that.

**Authority is checked.** A `SEV-1` alert whose severity carries `requires_line_stop` must be acknowledged by a worker whose role has `can_authorize_line_stop`. Acknowledgement by somebody who cannot act on it is not acknowledgement.

**Acknowledgement is recorded on the alert, not the event.** `acknowledged_at` and `acknowledged_by_worker_id` are the human-in-the-loop audit trail, and they belong on the mutable case.

In the incident, `EMP-1002` acknowledged at 18:51 — nine minutes after opening, well inside the 30-minute `SEV-2` window.

### 10.5 Stage 4 — Escalation

**Trigger.** `acknowledged_at` is still NULL when `max_acknowledgement_minutes` elapses.

**Action.** The Notification Service composes a notification for the next recipient by `notification_recipient.escalation_order`. The alert's `alert_status` becomes `escalated` and `escalated_at` is set.

**The chain terminates in three ways:**

| Termination | Meaning |
|---|---|
| Somebody acknowledges | Normal. Escalation stops |
| A `recommendation_action` is recorded | The recommendation was actioned, which supersedes acknowledgement |
| The chain exhausts | **Departmental `escalation_email` is the fallback.** A critical condition must never reach nobody |

The third case is the important one. Master data §17 rule 7 requires at least one recipient covering the most severe level with `notify_outside_shift_hours = 1`, precisely so the chain cannot exhaust silently on a `SEV-1`.

**Escalation did not occur in the incident** — acknowledgement came nine minutes early, and `escalation_order` 2 had been notified in parallel anyway.

### 10.6 Stage 5 — Resolution

Five resolution types, each meaning something different:

| `resolution_type` | Meaning | Required |
|---|---|---|
| `maintenance_performed` | A repair fixed it | A closed `maintenance_work_record` referencing this alert |
| `auto_recovered` | The condition ceased on its own | — |
| `false_positive` | The platform raised something that did not matter | `resolution_note` explaining why |
| `superseded` | Replaced by a more severe or specific alert | — |
| `manual_close` | A human closed it without repair or recovery | `resolution_note` |

**`false_positive` is the stage's most valuable value.** It is a permanent record of the platform being wrong. Analytics aggregates false positives by `alert_threshold_profile`, and that aggregation is the evidence base for the threshold tuning cycle described in master data §27. **A monitoring system that cannot count its own false positives cannot be improved** — and most systems do not record them, because recording them is uncomfortable.

**Auto-resolution.** An alert with no new events for a window defined by `business_rule` resolves as `auto_recovered`. The window is data, not a constant.

### 10.7 Stage 6 — Closure

**Trigger.** A human or the system signs off a resolved alert.

**Required.** `closed_at` and `resolution_note`. The note is written in plain language and is what somebody reads a year later when the same condition recurs.

**Closure is not deletion.** The alert and every one of its events survive. A mistaken alert is closed as `false_positive`, and **the events remain intact** — because an event correctly records what was observed even when the conclusion drawn from it was wrong. That distinction is worth being precise about: the observation was true, the inference was not.

### 10.8 Stage 7 — Retention

| Entity | Online | Then |
|---|---|---|
| `operational_event` | 1 year | Archive indefinitely |
| `operational_alert` | 2 years | Archive indefinitely |

**Alerts outlive their events** because alert-level analysis — acknowledgement times, resolution types, false positive rates — remains useful after the individual observations have been archived.

**The citation exemption overrides both.** Any event cited in the `supporting_evidence` of a retained `ai_recommendation` is exempt from purge while that recommendation survives, which can be three years. §8.4 rule 1. A recommendation that cannot show its evidence is not explainable, and retention must never be allowed to create that condition.

### 10.9 The worked incident through all seven stages

| Stage | What happened | Timestamp |
|---|---|---|
| **1. Creation** | `EVT-20260729-0412` created; `ALR-20260729-0087` opened with key `MC-0101\|machine_condition` | 18:42:00 |
| **2. Update** | 33 further events absorbed; severity held at `SEV-2` | 18:42 → 05:40 |
| **3. Acknowledgement** | `EMP-1002` acknowledged, 9 min into a 30-min window | 18:51:00 |
| **4. Escalation** | **Did not occur.** Acknowledged in time | — |
| **5. Resolution** | `maintenance_performed` via `WO-2026-0341` | 07-30 10:20 |
| **6. Closure** | Closed with note recording post-repair vibration of 1.9 mm/s | 07-30 10:35 |
| **7. Retention** | Events until July 2027, alert until July 2028. **The four cited events are exempt until `REC-20260729-0031` expires in July 2029** | — |

---

## 11. Prediction Pipeline

The transformation from a raw measurement to a delivered decision, with every transition specified. Seven stages, six transitions, and a volume reduction of roughly four orders of magnitude.

### 11.1 The pipeline

```
   machine_sensor_reading            ~87,000 rows/day
            │
            │  T1: aggregate a lookback window into features
            ▼
   prediction_feature_snapshot       ~170 rows/day
            │
            │  T2: run the model
            ▼
   prediction_result                 ~170 rows/day
            │
            │  T3: test against escalation policy, assemble context
            ▼
   supervisor_context                ~25 rows/day  (escalated: ~2)
            │
            │  T4: reason over context, produce recommendation
            ▼
   ai_recommendation                 ~2 rows/day
            │
            │  T5: resolve recipients, compose, deliver
            ▼
   notification + notification_delivery
            │
            │  T6: human decides
            ▼
   recommendation_action             the decision
            │
            │  T7: decision becomes work
            ▼
   maintenance_work_record           the outcome
```

### 11.2 Transition T1 — Reading to Feature Snapshot

| | Detail |
|---|---|
| **Performed by** | Prediction Agent |
| **Trigger** | Fixed schedule per monitored machine, plus on alert |
| **Input** | `machine_sensor_reading` over the lookback window, plus `cycle_history`, `machine_operational_status`, `operational_event`, `scrap_record` |
| **Output** | One `prediction_feature_snapshot` |

**What happens.** Raw values become derived quantities. Instead of *"vibration is 4.8"*, the snapshot holds *"slope 0.31 mm/s per hour, 6.7 % above healthy maximum, 780 seconds above the warning limit."*

**Three gates apply, and each can stop the pipeline here:**

1. **Validity.** Only readings with `quality_flag = 'valid'` contribute. Invalid readings are counted in `excluded_reading_count`.
2. **Completeness.** `data_completeness_pct` is computed against the expected count derived from `machine_type_parameter.sampling_interval_seconds`. Below a `business_rule` threshold, the snapshot is marked insufficient.
3. **Window integrity.** A window spanning a maintenance intervention is insufficient, because features mixing pre-repair and post-repair condition describe a machine that does not exist.

**When a gate fails, the snapshot is still written** with `is_sufficient_for_inference = 0` and a reason, and **no prediction is produced.** That is why `MC-0301` was never scored on 29 July, and the row is what makes the absence explicable rather than a gap.

**Volume collapse: 87,000 → 170.** Roughly 500 readings become one feature vector.

### 11.3 Transition T2 — Feature Snapshot to Prediction

| | Detail |
|---|---|
| **Performed by** | Prediction Agent |
| **Input** | One sufficient `prediction_feature_snapshot` |
| **Output** | One `prediction_result` |

**What happens.** The model scores the vector and returns a probability, a predicted failure mode, a horizon, and feature attributions. The probability is mapped onto the platform severity scale using `business_rule` cut-offs.

**Two constraints from master data bound the output:**

- The predicted mode must be declared for that machine's type with `is_model_predictable = 1`. **The model may not predict a failure nothing precedes.**
- The horizon should not exceed the mode's `typical_warning_period_hours`. Predicting further ahead than the physics gives warning for is not a forecast.

**This is the only place a failure probability is created.** `PROJECT_OVERVIEW.md` §16.5 requires that ML confidence originate here and be carried forward unchanged, and §18 enforces it structurally — `ai_recommendation` has no probability column.

**Reproducibility contract.** Snapshot plus `model_version` must reproduce the identical result. This is what makes the prediction auditable rather than merely trusted.

### 11.4 Transition T3 — Prediction to Supervisor Context

| | Detail |
|---|---|
| **Performed by** | Supervisor Agent |
| **Input** | One `operational_alert` and, usually, one `prediction_result` |
| **Output** | One `supervisor_context` — **escalated or suppressed** |

**This is the pipeline's cost and noise gate**, and the most consequential transition in the model.

**Escalation test, in order:**

1. Resolve the escalation threshold: a `business_rule` scoped to the affected line, falling back to global.
2. Test `failure_probability` against it.
3. Test alert severity against the `BR-ESC-SEV` floor.
4. Check for suppression conditions: an existing recommendation for this alert, an in-progress work order, insufficient snapshot data, or no eligible recipient within rate limits.

**A row is written either way.** Suppressions are recorded with an enumerated reason, which is what allows the platform to answer *"why wasn't I told?"* — a question a gate that only logs its positive decisions cannot answer.

**Only escalations assemble a context document.** Suppression took 8 milliseconds in the worked example against 212 for the escalation, because no package was built. **The cost saving lives precisely here.**

**Line-scoped thresholds are where business awareness enters the pipeline.** `LN-01` escalates at 0.55 rather than the global 0.70. The same 0.68 prediction on `LN-03` would not escalate. Nothing in the code knows about Line 01; the difference is entirely `business_rule` data.

**Volume collapse: 170 → 25 evaluated → ~2 escalated.**

### 11.5 Transition T4 — Context to Recommendation

| | Detail |
|---|---|
| **Performed by** | Decision Agent (Gemini) |
| **Input** | One escalated `supervisor_context` — **its `context_document` and nothing else** |
| **Output** | One `ai_recommendation` |

**The Decision Agent issues no queries.** Everything it reasons over is in the context document. That is a deliberate architectural property: the LLM's input is a single preserved payload, so the recommendation is reproducible in its inputs even though LLM output is not reproducible in its wording.

**Five contract elements are produced**, per `PROJECT_OVERVIEW.md` §16.5:

| Element | Source |
|---|---|
| Supporting evidence | Events, readings, and the corroboration list from the context |
| ML confidence | **Referenced from `prediction_result`, never restated** |
| Root cause | Classified within the `failure_category` controlled vocabulary |
| Business impact | Run, customer, units, margin, cost, penalty, grace period |
| Recommended action | Concrete step, recovery plan, team and engineer, part, deadline |

`contract_complete` records whether all five were produced. A recommendation with `contract_complete = 0` must not be delivered as final.

**Two constraints keep the reasoning honest:**

- Root cause must come from the twelve-value controlled vocabulary, not free-form generation. Classification within a reviewed set is checkable; invention is not.
- `root_cause_confidence = 'high'` requires corroboration from at least two independent measurement paths. In the incident there were three — accelerometer, cycle timer, coordinate measuring machine.

### 11.6 Transition T5 — Recommendation to Notification

| | Detail |
|---|---|
| **Performed by** | Notification Service |
| **Input** | One `ai_recommendation` |
| **Output** | One `notification` per qualifying recipient, one `notification_delivery` per channel per attempt |

**Recipient resolution** reads `notification_recipient` from master data and filters by minimum severity, line scope, shift eligibility, and rate limit. In the incident, four recipients were evaluated: two notified, two suppressed with recorded reasons.

**Suppression produces a row**, never an absence. Without that, "was Priya told?" would be answered by a missing row, and absence is ambiguous between deliberate suppression, a composition failure, and a bug.

**Delivery is separate** so that *deliberately not sent* and *sent but did not arrive* are distinguishable. `sent` and `delivered` are distinct statuses, and the gap between them is where silent failures live.

### 11.7 Transition T6 — Notification to Decision

| | Detail |
|---|---|
| **Performed by** | A human, recorded by the Dashboard |
| **Input** | A delivered `notification` and its `ai_recommendation` |
| **Output** | One `recommendation_action` |

**This is where the platform stops and the human takes over.** `PROJECT_OVERVIEW.md` makes the pipeline terminate at a person, and this row is that termination made concrete.

Six possible outcomes, of which two carry the most information:

- `accepted_with_modification` — the commonest real outcome, and the richest feedback. In the incident, the diagnosis was accepted and the timing changed.
- `no_action_taken` — **the platform's worst outcome**, worse than rejection, because nobody engaged at all. Recorded explicitly so it is visible rather than silent.

`response_time_minutes` is the platform's real effectiveness measure. Twenty-one minutes in the incident.

### 11.8 Transition T7 — Decision to Work

| | Detail |
|---|---|
| **Performed by** | Factory Simulator, modelling the human's instruction |
| **Input** | An accepted `recommendation_action` |
| **Output** | One `maintenance_work_record` with `work_type = 'predictive'` |

**This closes both loops.**

**The value loop.** A `predictive` work record exists because the platform recommended it. Counting predictive jobs that displaced corrective ones is the business case, expressed in data.

**The accuracy loop.** On closure, `confirmed_failure_category_id` records what the engineer actually found, against `prediction_result.predicted_failure_category_id`. In the incident both were `FC-BRG`, with 0.04 mm radial play measured on removal — **a scored, confirmed correct prediction**, not a self-assessment.

**Note on the read boundary.** The simulator creates a work record referencing a recommendation, which appears to violate §6.5. It does not: the reference arrives through the human decision in `recommendation_action`. The simulator is modelling a manager acting on advice, which is a real causal path in the world, not the simulator reading the platform's conclusions.

### 11.9 The pipeline as row counts

| Stage | Rows/day | Reduction |
|---|---|---|
| `machine_sensor_reading` | ~87,000 | — |
| `prediction_feature_snapshot` | ~170 | 512× |
| `prediction_result` | ~170 | 1× |
| `supervisor_context` | ~25 | 7× |
| escalated contexts | ~2 | 12× |
| `ai_recommendation` | ~2 | 1× |
| **Total** | | **~43,500×** |

**This table is the architecture from `PROJECT_OVERVIEW.md` §5.3 expressed as data.** Progressive filtering is not a description of intent — it is a measurable property of the model. Cheap deterministic checks run on 87,000 rows; a trained model runs on 170; expensive LLM reasoning runs on 2. At 3,240 prompt tokens per recommendation, the escalation gate is what makes the platform affordable, and `prompt_token_count` is how that claim is verified rather than asserted.

---

## 12. Data Flow Diagram

### 12.1 Complete operational pipeline

```
┌──────────────────────────────────────────────────────────────────────────┐
│  FACTORY MASTER DATA (frozen, 29 entities)                               │
│  plant · areas · lines · machines · products · workers · thresholds ...   │
└───────────────────────────────┬──────────────────────────────────────────┘
                    read by every stage below
                                │
┌───────────────────────────────▼──────────────────────────────────────────┐
│  1. FACTORY SIMULATOR                                    owns 12 entities│
│                                                                          │
│  Reads:  master data only. Never reads any agent output                  │
│  Writes: sensor readings · machine status · state transitions            │
│          runs · progress · counts · cycles · inspections · scrap         │
│          inventory movements · work records · maintenance activities     │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  T0: persist
┌───────────────────────────────▼──────────────────────────────────────────┐
│  2. OPERATIONAL DATABASE                                                 │
│     ~95,000 rows/day. Every row references master data by key            │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │
┌───────────────────────────────▼──────────────────────────────────────────┐
│  3. MONITORING AGENT                                      owns 2 entities│
│                                                                          │
│  Reads:  readings · cycles · progress · inspections · scrap · movements  │
│          machine status (state gating) · alert_threshold_rule (master)   │
│  Writes: operational_event (immutable) · operational_alert (case)        │
│  Gate:   suppress during setup, planned downtime, and open work orders   │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  T1
┌───────────────────────────────▼──────────────────────────────────────────┐
│  4. PREDICTION AGENT                                      owns 2 entities│
│                                                                          │
│  Reads:  readings · cycles · machine status · events · scrap             │
│          machine_type_parameter (master, feature set)                    │
│  Writes: prediction_feature_snapshot · prediction_result                 │
│  Gate:   validity · completeness · window integrity                     │
│          insufficient → snapshot written, NO prediction                  │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  T2, T3
┌───────────────────────────────▼──────────────────────────────────────────┐
│  5. SUPERVISOR AGENT                                      owns 1 entity  │
│                                                                          │
│  Reads:  alerts · predictions · runs · progress · movements              │
│          work records · machine status · business_rule (master)          │
│  Writes: supervisor_context — ESCALATED **or** SUPPRESSED                │
│  Gate:   probability vs line-scoped threshold · severity floor           │
│          duplicate · maintenance in progress · rate limit · data quality │
└───────────────────────────────┬──────────────────────────────────────────┘
                     escalated only │  T4
┌───────────────────────────────▼──────────────────────────────────────────┐
│  6. DECISION AGENT (Gemini)                               owns 1 entity  │
│                                                                          │
│  Reads:  supervisor_context.context_document — and nothing else         │
│  Writes: ai_recommendation                                              │
│  Contract: evidence · ML confidence (by reference) · root cause          │
│            business impact · recommended action                          │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  T5
┌───────────────────────────────▼──────────────────────────────────────────┐
│  7. NOTIFICATION SERVICE                                  owns 2 entities│
│                                                                          │
│  Reads:  recommendations · alerts · notification_recipient (master)      │
│  Writes: notification (incl. suppressed) · notification_delivery         │
│  Gate:   min severity · line scope · quiet hours · rate limit            │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  email · WhatsApp
┌───────────────────────────────▼──────────────────────────────────────────┐
│  8. FACTORY MANAGER                                        the authority │
│     Reviews, judges, decides. The platform advises and never actuates    │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  T6
┌───────────────────────────────▼──────────────────────────────────────────┐
│  9. DASHBOARD                                             owns 2 entities│
│                                                                          │
│  Reads:  nearly everything                                              │
│  Writes: recommendation_action (the human decision)                     │
│          dashboard_snapshot (materialised state)                        │
└───────────────────────────────┬──────────────────────────────────────────┘
                                │  T7  — accepted actions become work
                                ▼
                    back to the SIMULATOR, which creates
                    maintenance_work_record (work_type = predictive)
                    closing the value loop and the accuracy loop

┌──────────────────────────────────────────────────────────────────────────┐
│  CROSS-CUTTING — PLATFORM                                 owns 2 entities│
│  audit_log (all components emit) · system_health_status (all report)     │
└──────────────────────────────────────────────────────────────────────────┘
```

### 12.2 Transition summary

| # | Transition | By | Input → Output | Filter |
|---|---|---|---|---|
| T0 | Generate → persist | Simulator | Master data → 12 operational entities | — |
| T1 | Detect | Monitoring Agent | Telemetry, cycles, quality, inventory → events, alerts | State gating; sustained duration |
| T2 | Featurise and score | Prediction Agent | Readings and context → snapshot, prediction | Validity, completeness, window integrity |
| T3 | Gate and assemble | Supervisor Agent | Alert + prediction → context | Probability, severity, duplicate, maintenance, rate, data quality |
| T4 | Reason | Decision Agent | Context document → recommendation | Contract completeness |
| T5 | Deliver | Notification Service | Recommendation → notifications, deliveries | Severity, scope, quiet hours, rate limit |
| T6 | Decide | Human via Dashboard | Notification → action | Human judgement |
| T7 | Act | Simulator | Accepted action → work record | — |

### 12.3 The three loops

**The evidence loop — backward traceability.** Every recommendation decomposes to its raw measurements:

```
ai_recommendation
  └─ supervisor_context           the context as the LLM saw it
       ├─ operational_alert       the managed case
       │    └─ operational_event  the observations, with thresholds as they were
       │         └─ machine_sensor_reading   the measurement
       └─ prediction_result       the ML confidence
            └─ prediction_feature_snapshot   the exact model input
```

Five hops from a recommendation to a raw reading, every hop an immutable row. **This is what the explainability contract requires, and §8.4 rule 1 is what keeps it intact through retention.**

**The value loop — did it help?**

```
ai_recommendation → recommendation_action (accepted)
                  → maintenance_work_record (work_type = predictive)
                  → machine_state_transition (down_PLANNED, not unplanned)
```

Counting planned stoppages that displaced unplanned ones is the platform's business case in data.

**The accuracy loop — was it right?**

```
prediction_result.predicted_failure_category_id
        compared against
maintenance_work_record.confirmed_failure_category_id
```

The engineer's finding is the ground truth. In the incident both were `FC-BRG`. **This comparison is the only honest measure of model quality**, and it is why both entities carry the failure category explicitly.

### 12.4 Where each gate reduces volume

| Gate | Stage | Removes |
|---|---|---|
| Sustained duration | Detection | Transient spikes |
| State gating | Detection | Abnormality during setup, planned downtime, and repairs |
| Correlation by key | Detection | Duplicate cases — 34 events into 1 alert |
| Reading validity | Featurisation | Sensor faults masquerading as machine faults |
| Completeness | Featurisation | Predictions on inadequate data |
| Window integrity | Featurisation | Features spanning a repair |
| Probability threshold | Escalation | Low-risk situations, line-aware |
| Severity floor | Escalation | Low-severity conditions |
| Duplicate detection | Escalation | Re-reasoning on an unresolved condition |
| Maintenance in progress | Escalation | Problems already being fixed |
| Min severity per recipient | Delivery | Messages below a person's threshold |
| Line scope | Delivery | Other lines' problems |
| Quiet hours | Delivery | Off-shift interruption below critical |
| Rate limit | Delivery | Channel flooding |

Fourteen gates. **Every one is configured by master data or a `business_rule`, and none is a hardcoded constant.** That is what makes the platform's filtering behaviour inspectable, tunable, and explainable — and it is why a manager asking "why didn't I hear about this?" always has an answer with a rule code attached.

---

# Part V — Relationship Model

## 13. Entity Relationship Model

The brief requires master data, operational data, and cross-references to be kept separate, and circular dependencies avoided. This section does both.

### 13.1 The three reference classes

| Class | Direction | Count | Rule |
|---|---|---|---|
| **Master references** | Operational → Master | 78 | Foreign key only. **No master attribute is ever copied**, with the single documented exception in §3.5 |
| **Operational references** | Operational → Operational | 37 | Must keep the operational graph acyclic |
| **Soft references** | `audit_log` → anything | 1 | **Deliberately not a foreign key**, so the audit trail survives the purge of what it describes |

**Master data never references operational data.** Not once. The 29 master entities are frozen and self-contained, and the dependency runs strictly one way. That is what allows master data to be seeded, reviewed, and reasoned about entirely on its own — and it is why the two documents can be read independently.

### 13.2 Master data references by operational entity

| # | Operational entity | Master entities referenced |
|---|---|---|
| 1 | `machine_sensor_reading` | `machine`, `machine_parameter`, `shift` |
| 2 | `machine_operational_status` | `machine`, `shift` |
| 3 | `machine_state_transition` | `machine`, `shift` |
| 4 | `production_run` | `product`, `production_line`, `product_line_capability`, `customer` |
| 5 | `production_progress` | `shift` |
| 6 | `production_count` | `machine`, `shift` |
| 7 | `cycle_history` | `machine`, `shift` |
| 8 | `quality_inspection_result` | `machine` ×2, `failure_category`, `worker`, `shift` |
| 9 | `scrap_record` | `machine` ×2, `failure_category`, `worker`, `shift` |
| 10 | `inventory_movement` | `inventory_item`, `inventory_location`, `supplier`, `worker`, `shift` |
| 11 | `maintenance_work_record` | `machine`, `machine_maintenance_schedule`, `failure_category` ×2, `failure_severity_level`, `maintenance_team`, `maintenance_engineer`, `shift` |
| 12 | `machine_maintenance_activity` | `worker`, `shift` |
| 13 | `operational_event` | `machine`, `production_line`, `inventory_item`, `machine_parameter`, `alert_threshold_rule`, `failure_severity_level`, `shift` |
| 14 | `operational_alert` | `machine`, `production_line`, `inventory_item`, `failure_severity_level` ×2, `worker` |
| 15 | `prediction_feature_snapshot` | `machine`, `shift` |
| 16 | `prediction_result` | `machine`, `failure_severity_level`, `failure_category`, `machine_type_failure_mode`, `shift` |
| 17 | `supervisor_context` | `machine`, `production_line`, `business_rule`, `shift` |
| 18 | `ai_recommendation` | `machine`, `production_line`, `failure_severity_level`, `failure_category`, `maintenance_team`, `maintenance_engineer`, `inventory_item`, `shift` |
| 19 | `recommendation_action` | `worker`, `shift` |
| 20 | `notification` | `notification_recipient`, `failure_severity_level`, `shift` |
| 21 | `notification_delivery` | **None** |
| 22 | `dashboard_snapshot` | `production_line`, `machine` |
| 23 | `audit_log` | `worker` |
| 24 | `system_health_status` | **None** |

**Most-referenced master entities**, and what the ranking reveals:

| Master entity | Referenced by | Observation |
|---|---|---|
| `shift` | 17 entities | **The most referenced of all.** Nearly every operational fact is attributed to a crew, because "which shift" is how manufacturing performance is universally analysed |
| `machine` | 13 entities | The platform's central subject |
| `failure_severity_level` | 6 entities | Severity is the shared currency of urgency |
| `failure_category` | 5 entities | The failure taxonomy runs from quality through prediction to confirmed repair |
| `production_line` | 6 entities | The unit at which impact is assessed |
| `worker` | 5 entities | Human accountability at every point a person acts |

That `shift` outranks `machine` was not planned; it emerged from applying the convention in §3.2 that every entity carries the shift it occurred in. It turns out to be right — segmenting by crew is the first thing any plant manager does with operational data, and a model that made it a join through timestamps would make the most common analysis the most expensive one.

**Two entities reference no master data at all.** `notification_delivery` is pure transport mechanics, and `system_health_status` describes software rather than a factory object. Both are cleanly separable from the domain, which is a useful property if either mechanism is ever replaced.

### 13.3 Operational-to-operational references

All 37, grouped by target.

| Target | Referenced by | Meaning |
|---|---|---|
| `production_run` | `machine_sensor_reading`, `machine_operational_status`, `machine_state_transition`, `production_progress`, `production_count`, `cycle_history`, `quality_inspection_result`, `scrap_record`, `inventory_movement`, `operational_event`, `ai_recommendation` | **11 references — the most connected operational entity.** The run is the operational context for almost everything |
| `operational_alert` | `operational_event`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `maintenance_work_record`, `notification` | 6 references. The managed case everything downstream attaches to |
| `maintenance_work_record` | `machine_state_transition`, `machine_maintenance_activity`, `inventory_movement`, `recommendation_action` | 4 references. Repairs cause downtime, consume parts, and fulfil decisions |
| `ai_recommendation` | `recommendation_action`, `notification`, `maintenance_work_record` | 3 references |
| `prediction_result` | `supervisor_context`, `ai_recommendation` | 2 references |
| `operational_event` | `machine_state_transition`, `quality_inspection_result`, `scrap_record` | 3 references |
| `machine_sensor_reading` | `operational_event` | 1 reference — the triggering reading |
| `prediction_feature_snapshot` | `prediction_result` | 1 reference |
| `supervisor_context` | `ai_recommendation` | 1 reference |
| `quality_inspection_result` | `scrap_record` | 1 reference |
| `scrap_record` | `inventory_movement` | 1 reference |
| `notification` | `notification_delivery` | 1 reference |
| `machine_state_transition` | `machine_operational_status` | 1 reference |

### 13.4 Operational dependency layers

Every operational entity sits in the layer immediately above its deepest operational dependency. Master references are excluded from layering, because master data is frozen, separately acyclic, and always available.

**Layer 0 — Operational roots (5).** No operational foreign keys.

```
production_run        operational_alert        dashboard_snapshot
audit_log             system_health_status
```

**Layer 1 (5).**

```
machine_sensor_reading      → production_run
production_progress         → production_run
production_count            → production_run
cycle_history               → production_run
prediction_feature_snapshot → operational_alert
```

**Layer 2 (2).**

```
operational_event  → operational_alert, production_run, machine_sensor_reading
prediction_result  → prediction_feature_snapshot, operational_alert
```

**Layer 3 (2).**

```
quality_inspection_result → production_run, operational_event
supervisor_context        → operational_alert, prediction_result
```

**Layer 4 (2).**

```
scrap_record       → production_run, quality_inspection_result, operational_event
ai_recommendation  → supervisor_context, prediction_result, production_run
```

**Layer 5 (2).**

```
notification             → ai_recommendation, operational_alert
maintenance_work_record  → operational_alert, ai_recommendation
```

**Layer 6 (5).**

```
machine_maintenance_activity → maintenance_work_record
inventory_movement           → production_run, maintenance_work_record, scrap_record
machine_state_transition     → production_run, operational_event, maintenance_work_record
recommendation_action        → ai_recommendation, maintenance_work_record
notification_delivery        → notification
```

**Layer 7 (1).**

```
machine_operational_status → production_run, machine_state_transition
```

**Totals:** 5 + 5 + 2 + 2 + 2 + 2 + 5 + 1 = **24 entities.** Maximum depth 7.

**Practical consequences.** Test fixtures can build a valid operational state by walking layers 0 to 7 with no deferred constraints. Purge runs in reverse. And the layering makes an important property visible: **`machine_operational_status` sits at the deepest layer**, which is correct — it is the most derived entity in the model, a materialised summary of everything below it.

### 13.5 Acyclicity analysis

**Proof by layer assignment.** §27.4 assigns every entity a layer in which all of its operational foreign keys reference strictly lower layers. A graph admitting such an assignment is acyclic by construction: a cycle would require some entity to reference its own layer or higher, and none does. Master references cannot introduce a cycle because master data never references operational data.

**The cycle that was designed out.** One genuine cycle appeared during design and was removed:

| Would-be cycle | Natural instinct | Resolution |
|---|---|---|
| `operational_alert` ⇄ `maintenance_work_record` | `alert.resolved_by_work_record_id` alongside `work_record.triggering_alert_id` | **Reference kept only on the work record.** The resolving job is derived by querying closed work records that reference the alert |

Both directions were semantically real — a job is raised *because of* an alert, and an alert is resolved *by* a job — which is exactly what made it a trap. The resolution keeps the reference on the **child that knows its own cause at insert time**, and derives the reverse direction.

This is the same pattern the frozen master data model applied to department managers, line supervisors, and maintenance team leads (§30.4 of that document), where a back-reference from the organisational unit to the person would have created three cycles. Stated once, as a rule that now spans both documents:

> **A reference lives on the entity that knows the fact at the moment it is written. The reverse direction is derived, never stored as a back-pointer.**

`operational_alert` consequently has **no outgoing operational foreign keys**, making it one of five operational roots. That is a good outcome rather than an accident: an alert is a case that other things attach to, and cases should not depend on their own consequences.

**Nullable references are not cycles.** Many operational references are optional — `machine_sensor_reading.production_run_id`, `operational_event.triggering_reading_id`, `maintenance_work_record.triggering_recommendation_id`, and others. Optionality expresses a business fact (the machine was idle, the event had no single reading, the job was scheduled rather than recommended), and every one still points strictly downward.

**Repeated references to one target are not cycles.** `quality_inspection_result` and `scrap_record` each reference `machine` twice, in the distinct roles of location and attribution. `maintenance_work_record` references `failure_category` twice, as reported and confirmed. `operational_alert` references `failure_severity_level` twice, as initial and current. All are role-qualified in their names, and none creates a cycle because the targets are master entities that reference nothing operational.

### 13.6 Textual entity relationship diagram

Arrows point from child to parent. Master references are summarised rather than drawn, to keep the operational structure legible.

```
════════════════ MASTER DATA (frozen, 29 entities) ════════════════
   Referenced by 78 foreign keys from below. References nothing here.
═══════════════════════════════════════════════════════════════════
                              ▲
                              │  (all operational entities reference master)
                              │
──── LAYER 0 — operational roots ─────────────────────────────────
┌────────────────┐  ┌──────────────────┐  ┌───────────────────┐
│ production_run │  │ operational_alert│  │ dashboard_snapshot│
└───────┬────────┘  └────────┬─────────┘  └───────────────────┘
        │                    │             ┌───────────┐ ┌──────────────────────┐
        │                    │             │ audit_log │ │ system_health_status │
        │                    │             └───────────┘ └──────────────────────┘
──── LAYER 1 ──────────────────────────────────────────────────────
        ├──────────┬──────────┬──────────┐  └──────────────┐
        ▼          ▼          ▼          ▼                 ▼
┌──────────────┐ ┌──────────┐ ┌────────┐ ┌─────────────────────────────┐
│ machine_     │ │production │ │production│ │ prediction_feature_snapshot│
│ sensor_      │ │_progress  │ │_count   │ └──────────────┬──────────────┘
│ reading      │ └──────────┘ └────────┘   ┌───────────┐  │
└──────┬───────┘                            │cycle_     │  │
       │                                    │history    │  │
       │                                    └───────────┘  │
──── LAYER 2 ──────────────────────────────────────────────────────
       ▼                                                   ▼
┌──────────────────┐                          ┌────────────────────┐
│ operational_event│                          │ prediction_result  │
└────────┬─────────┘                          └─────────┬──────────┘
──── LAYER 3 ──────────────────────────────────────────────────────
         ├─────────────────────────┐                     │
         ▼                         │                     ▼
┌──────────────────────────┐       │      ┌──────────────────────┐
│ quality_inspection_result│       │      │ supervisor_context   │
└────────────┬─────────────┘       │      └──────────┬───────────┘
──── LAYER 4 ──────────────────────────────────────────────────────
             ▼                     │                  ▼
      ┌──────────────┐             │        ┌────────────────────┐
      │ scrap_record │◄────────────┘        │ ai_recommendation  │
      └──────┬───────┘                      └─────────┬──────────┘
──── LAYER 5 ──────────────────────────────────────────────────────
             │                        ┌───────────────┴──────────┐
             │                        ▼                          ▼
             │              ┌──────────────┐   ┌──────────────────────────┐
             │              │ notification │   │ maintenance_work_record  │
             │              └──────┬───────┘   └────────────┬─────────────┘
──── LAYER 6 ──────────────────────────────────────────────────────
             ├────────────────────────────────────────────┤
             ▼                     ▼                      ▼
   ┌───────────────────┐ ┌──────────────────┐ ┌──────────────────────────────┐
   │ inventory_movement│ │notification_     │ │ machine_maintenance_activity │
   └───────────────────┘ │delivery          │ └──────────────────────────────┘
   ┌──────────────────────┐ └────────────────┘ ┌────────────────────────┐
   │machine_state_        │                    │ recommendation_action  │
   │transition            │                    └────────────────────────┘
   └──────────┬───────────┘
──── LAYER 7 ──────────────────────────────────────────────────────
              ▼
   ┌────────────────────────────┐
   │ machine_operational_status │
   └────────────────────────────┘
```

### 13.7 The evidence chain as a join path

The single most important path in the model — how a recommendation resolves to its raw evidence:

```
ai_recommendation                         REC-20260729-0031
  ├─→ prediction_result                   PDN-20260729-0203   probability 0.68
  │     └─→ prediction_feature_snapshot   FSN-20260729-04188  the exact model input
  └─→ supervisor_context                  CTX-20260729-0044   the context as seen
        ├─→ business_rule       (master)   BR-ESC-PROB-LN01    why it escalated
        └─→ operational_alert              ALR-20260729-0087   the case, 34 events
              └─→ operational_event        EVT-20260729-0412   4.74 vs 4.70 mm/s
                    ├─→ alert_threshold_rule (master)          lineage of the limit
                    └─→ machine_sensor_reading                 the measurement
```

**Every hop is an immutable row, and the maximum depth is five.** That is not incidental — it is the property the model was designed to have, because the explainability contract in `PROJECT_OVERVIEW.md` §16.5 requires that any recommendation be traceable to its evidence, and a chain that were longer or that passed through mutable rows would make the guarantee unenforceable.

The parallel path for the **outcome** of that recommendation:

```
ai_recommendation
  └─→ recommendation_action                accepted_with_modification, EMP-1002
        └─→ maintenance_work_record         WO-2026-0341, work_type = predictive
              ├─→ machine_maintenance_activity   the repair timeline
              ├─→ inventory_movement             the bearing consumed
              ├─→ machine_state_transition       down_PLANNED, not unplanned
              └─→ failure_category (master)      CONFIRMED FC-BRG
```

The last line is where the platform is scored. `confirmed_failure_category_id` against `prediction_result.predicted_failure_category_id` — the engineer's finding against the model's forecast.

### 13.8 Deliberate non-relationships

References a reviewer might expect and will not find.

| Expected | Excluded because |
|---|---|
| `operational_alert.resolved_by_work_record_id` | Would create the model's only cycle. Derived instead. §13.5 |
| `operational_event.acknowledged_at` | Acknowledgement belongs on the mutable alert. An event is immutable evidence |
| `ai_recommendation.failure_probability` | **ML confidence must never be restated by the LLM.** Referenced, never stored. §E18 |
| `ai_recommendation.status` | Would make the platform's product mutable. The human response is `recommendation_action` |
| `production_run.quantity_good` | Read from the terminal `production_progress` snapshot |
| `machine_maintenance_schedule.next_due_date` | **Master data, and deliberately absent there too.** Computed from schedule plus this document's work records |
| `inventory_item.quantity_on_hand` | Master data. Current stock is the latest `inventory_movement` balance |
| `audit_log.entity_id` as a foreign key | Would block or cascade the purge of the row it audits |
| Any operational → master **write** | Operational data never modifies master data. The dependency is strictly one-way |
| Any master → operational reference | Master data is frozen and self-contained |
| Any machine control or setpoint entity | **The platform is advisory. No control path exists anywhere in the architecture** |

The last row is the hard architectural boundary from `PROJECT_OVERVIEW.md`, and its enforcement in this document is total: among 24 entities and 116 references, **not one represents an instruction to a machine.**

---

# Part VI — Governance

## 14. Document authority and change control

**Authority.** This document is the blueprint for every dynamic entity in FactoryFlow AI. The operational SQLite schema, simulator modules, agent queries, dashboard views, and analytics jobs all derive from it. Where implementation reality requires a change, this document is revised first and the rationale recorded.

**Position in the document set.**

| Document | Defines | Status |
|---|---|---|
| `PROJECT_OVERVIEW.md` | Vision, architecture, principles, explainability contract | Frozen |
| `FACTORY_MASTER_DATA_DESIGN.md` | 29 static entities — what exists | Frozen |
| `FACTORY_OPERATIONAL_DATA_DESIGN.md` | 24 dynamic entities — what is happening | **This document** |

This document **implements** what the overview requires and **references** what master data defines. It restates neither and overrides neither. Where the three touch — the master/operational boundary, the explainability contract, single responsibility, deliberate simplicity — this document is the operational-layer expression of a decision already made.

**Change control.**

| Change | Requires |
|---|---|
| New attribute | A stated business reason and at least one named consumer |
| New entity | A business reason, a named consumer, no duplication, and an assigned owner |
| New operational reference | Confirming the layer assignment in §13.4 still holds and the graph stays acyclic |
| New master reference | Confirming no master attribute is being copied |
| Changing an owner | Justifying why the current owner is wrong. Ownership changes are architectural, not incidental |
| New mutable field on an append-only entity | Explicit justification. **The default is immutable** and every exception in this document is documented at the point it occurs |
| Changing a retention period | Confirming the §8.4 purge rules still protect the evidence chain |
| New maintained total or denormalisation | A stated reconciliation rule added to §9.3 |

## 15. Non-negotiable design constraints

These may not be changed without explicitly revising this document.

1. **The master/operational boundary holds.** No master attribute is copied into operational data, except the point-in-time threshold capture in §3.5, whose reasoning is stated where it occurs.
2. **Master data is never modified by operational processing.** The dependency runs one way, always.
3. **One owner per entity.** No shared writes. The two documented exceptions in §6.4 are justified there and nowhere else.
4. **The Simulator reads no agent output.** Violating this makes every accuracy measurement circular and the platform untestable. §6.5.
5. **The Simulator never creates events, predictions, recommendations, or notifications.** Failures manifest as telemetry and state; detection, prediction, and reasoning are the agents' work.
6. **Append-only is the default.** The six mutable entities are declared in §4.1, and each is either reconstructible from history or carries human decisions that exist nowhere else.
7. **Evidence is immutable.** Events, predictions, feature snapshots, contexts, recommendations, and actions can never be edited. The explainability contract collapses otherwise.
8. **Failure probability originates only in `prediction_result`.** `ai_recommendation` has no probability column, which makes the §16.5 rule structurally unbreakable rather than merely stated.
9. **Root cause is drawn from the `failure_category` controlled vocabulary.** The LLM classifies within a reviewed set; it never invents a cause.
10. **Suppressions are recorded.** Suppressed escalations, suppressed notifications, and false-positive resolutions are all first-class rows. **Silence must always be explicable.**
11. **The operational dependency graph stays acyclic.** A reference lives on the entity that knows the fact when it is written; the reverse is derived. §13.5.
12. **Every entity carries an explicit event time distinct from its record time.** §3.2.
13. **Every entity has a retention policy, and purge never breaks the evidence chain.** §8.4.
14. **Every maintained total and every denormalisation has a reconciliation rule.** §9.3.
15. **No entity represents a command to a machine.** The platform advises; the manager decides; nothing actuates.

## 16. Coverage confirmation

**All 24 required entities are present**, mapped in §5.3. No entities were added, and the two candidates considered and rejected are recorded there.

**All nine required subsections** are documented for each of the 24 entities: purpose, business description, primary key with justification, attributes with type, requirement, example, validation and business meaning, relationships across master and operational data, lifecycle covering creator, updater, readers, archiver, mutability, append-only status, expiry and regenerability, business rules, example records, and consumers across all eight components.

**Required cross-cutting sections:**

| Required | Location |
|---|---|
| Time-series design — append-only, current-state, historical, aggregated, snapshot, streaming | §4, with the reason each class exists |
| Event lifecycle — creation, update, acknowledgement, escalation, resolution, closure, retention | §10, all seven stages, traced through the worked incident |
| Prediction pipeline — reading → snapshot → prediction → context → decision → notification → manager | §11, seven stages and seven transitions, each specified |
| **Simulator contract** — created, updated, never touched, and which agent writes what | §7, all 24 entities, plus what the simulator must never do |
| Data ownership — one owner per entity | §6, with two documented exceptions |
| Retention policy — keep, archive, delete, derived, recomputable, immutable, mutable | §8, all 24 entities, plus three purge rules |
| Master data references — referenced, never duplicated | §27.2, all 78 references |
| Entity relationship model — master, operational, cross-references, acyclic | §13, with a layer-assignment proof |
| Data flow diagram — full pipeline with every transition explained | §12 |

**Constraints observed.** No SQL, no DDL, no ORM models, no indexes, no API design, no repository or service patterns, no simulator algorithms, no ML models, no prompt engineering, no frontend, no authentication, no deployment, no infrastructure. One document, no folders created, no placeholder files.

---

*End of Factory Operational Data Design.*
