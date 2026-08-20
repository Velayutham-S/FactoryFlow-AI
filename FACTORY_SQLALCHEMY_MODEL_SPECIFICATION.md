# FactoryFlow AI — SQLAlchemy ORM Model Specification

**Document type** — ORM layer specification
**Status** — Complete. Derived entirely from frozen inputs.
**Target** — SQLAlchemy 2.0 (Declarative, typed), Python 3.11+, SQLite 3, Alembic

---

## Input documents

This specification is derived from four frozen documents and contains nothing that is not traceable to them.

| Document | Contributes |
|---|---|
| `PROJECT_OVERVIEW.md` | Component set, ownership model, explainability contract, resume objective |
| `FACTORY_MASTER_DATA_DESIGN.md` | 29 master entities, business meaning, cardinality intent |
| `FACTORY_OPERATIONAL_DATA_DESIGN.md` | 24 operational entities, event flow, immutability rules |
| `FACTORY_SQLITE_DATABASE_SCHEMA.md` | 53 physical tables, 512 columns, 163 foreign keys, 65 controlled vocabularies, ~285 check constraints, 58 unique constraints, 8 unique indexes, 20 transaction boundaries |

**Nothing in this document redesigns any of them.** No table is renamed, no column is renamed, no type is changed, no relationship is added or removed, no cardinality is altered, no constraint is relaxed. Where this document appears to add something — a mixin, a type alias, a loading policy — it is adding a *Python-side expression* of a fact the schema already states, never a new fact.

---

## How to read this document

- **Parts I–III** establish the architecture every model obeys. Read these first; the 53 model specifications assume them.
- **Parts IV–V** specify all 53 models in an identical 17-subsection format. These are reference entries, not narrative.
- **Parts VI–XV** are the cross-cutting policies: relationships, types, enums, validation, sessions, performance, packaging, standards, and totals.

Sections are numbered continuously (§1–§46) across Parts so that any statement can be cited precisely.

**A note on code.** This document is a specification, not an implementation. Constructs are named in prose (`Mapped` annotation, `mapped_column` directive, `relationship` directive) and their configuration is stated as facts in tables. Two fenced blocks are ASCII diagrams rather than code — a package tree in §44 and a dependency graph in §46.

**Five short fenced blocks do contain literal code or SQL**, and each is there because prose cannot state it without losing the precision an implementer needs: the database URL forms (§4.1), the `COALESCE` expression index (§37.9), the table-rebuild sequence for widening a vocabulary (§40.5), the connection event hook that issues `PRAGMA foreign_keys = ON` (§42.9), and Alembic's `render_as_batch` configuration (§44.5). All five are engine-level or migration-level rather than model-level; **no model class is written out anywhere in this document.**

---
---

# Part I — ORM Overview

---

## 1. Purpose of this document

The SQLite schema is frozen and complete: 53 tables in one database file, every column typed, every constraint named, every foreign key action decided. What does not yet exist is the Python-side representation of it.

This document is that representation, specified rather than implemented. It exists so that the implementation phase is **transcription, not design**. Every model class, every attribute, every type, every relationship, every loading policy is decided here. An implementer working from this document makes no architectural choices, and two implementers working from it independently produce the same code.

That property is the point. It is also what makes the ORM layer reviewable: a reviewer can compare the implementation against this document line by line without needing to re-derive intent from the database.

---

## 2. Purpose of SQLAlchemy in FactoryFlow AI

SQLAlchemy occupies exactly one position in the platform: it is the **typed boundary between Python objects and SQLite rows**. Everything above it — the simulator, the four agents, the notification service, the dashboard — works with Python objects. Everything below it is relational.

Four things are asked of it, and nothing else.

| Responsibility | What it means here |
|---|---|
| **Declarative mapping** | Each of the 53 tables has exactly one Python class. The class is the single place a developer looks to learn a table's shape. |
| **Type fidelity** | A `NUMERIC(12,4)` column arrives in Python as `Decimal`, never `float`. A `DATETIME` arrives timezone-aware. A vocabulary column arrives as a Python enum member, not a bare string. |
| **Relationship navigation** | Traversal along the 163 foreign keys is an attribute access rather than a hand-written join, where and only where that traversal is bounded and safe. |
| **Unit of work** | The 20 transaction boundaries in the frozen schema §46 become sessions that flush and commit as a unit. |

It is explicitly **not** asked to own business logic, to validate business rules, to define the schema, or to generate the schema. Those belong elsewhere, and §8 states where.

---

## 3. Why an ORM exists in this platform

Raw SQL through a driver would work. It was considered and rejected for reasons specific to this platform's shape.

**The traversal depth is real.** The operational dependency graph is seven layers deep (§46.7). A Decision Agent producing one `ai_recommendation` reads across `supervisor_context`, `prediction_result`, `prediction_feature_snapshot`, `operational_alert`, `operational_event`, `machine`, `machine_type`, `machine_type_failure_mode`, `failure_category`, `failure_severity_level`, and `inventory_item`. Hand-written SQL for that path is a large join whose correctness depends on 11 tables' worth of column names being spelled right, and which must be re-verified every time any of them changes. An ORM makes the same traversal a sequence of attribute accesses that a type checker verifies.

**The type surface is wide and easy to get wrong, and SQLite widens it further.** 512 columns spanning 11 distinct `NUMERIC` precisions, 100 vocabulary columns across 65 vocabularies, 10 `TEXT` documents holding JSON, 45 boolean flags stored as `INTEGER`, and `DATETIME` throughout. **The engine returns storage classes, not domain types.** `sqlite3` hands back a `float` for a `NUMERIC`, an `int` for a boolean, a `str` for a JSON document, a `str` for a timestamp with no zone attached, and a `str` for every vocabulary value. Every one of those is a defect waiting for a specific input, and none of them is something the database can be asked to fix — SQLite has no type system to enforce a domain on the way out.

Mapping the conversion once, in one place, per column, is therefore doing more work here than it would against a server database, and it is the single strongest argument for the ORM in this platform (§3).

**Writes must be transactional and are already grouped.** The frozen schema specifies 20 transaction boundaries by name. A unit-of-work session expresses each as a scope. Hand-rolled transaction management across eight components would reproduce that logic eight times — and on a single-writer engine, a boundary held open longer than it needs to be blocks every other writer (§42.8), so getting the scope right matters more here than it would against a server database.

**The honest cost.** An ORM makes it easy to write an accidental N+1 query, and it hides how much SQL a line of Python emits. On `machine_sensor_reading` — 32 million rows a year — that cost is not theoretical. §19 and §43 address it directly, and the answer is deliberately restrictive: unbounded collections are **not mapped at all**, and relationships from the four highest-volume models refuse to emit implicit SQL. The ORM is used where it helps and constrained where it hurts.

---

## 4. Relationship with SQLite

**SQLite is the authority. The ORM is a client.**

This is not a stylistic preference; it determines several concrete decisions.

| Concern | Where authority sits | ORM's role |
|---|---|---|
| Schema definition | The SQLite database file, via Alembic migrations generated from these models | Declares the intent; never creates tables at runtime |
| Identity generation | SQLite `INTEGER PRIMARY KEY AUTOINCREMENT` | Never supplies a primary key value; reads it back after flush |
| Row defaults (`CURRENT_TIMESTAMP`, `1`, `0`) | SQLite server-side column defaults | Declares them as server defaults, does not compute them in Python |
| The ~285 check constraints | SQLite | Does not duplicate them (§41) |
| Referential integrity, 163 FKs | SQLite, 162 `RESTRICT` + 1 `SET NULL`, **conditional on `PRAGMA foreign_keys = ON`** | Declares the constraints; performs no cascading of its own (§22) |
| Uniqueness, including 8 unique indexes | SQLite | Declares them in table arguments so Alembic emits them |
| Controlled vocabularies | SQLite check constraints on `TEXT` columns | Binds a Python enum to the column; does not restate the value list |

The consequence worth stating plainly: **an integrity violation surfaces as a database error, not as a Python validation failure.** That is intended. A constraint enforced in Python is a constraint that a second writer bypasses, and SQLite has no privilege system to prevent a second writer from existing. There is only one place a rule cannot be bypassed, and it is not the ORM.

### 4.1 The engine, the driver, and the URL

**The driver is the standard library.** SQLAlchemy's default SQLite dialect uses `pysqlite`, which is Python's built-in `sqlite3` module. There is no third-party driver to install, no compiled dependency to build, and no client-library version to keep aligned with a server. The entire persistence stack is SQLAlchemy plus the standard library.

**The URL names a file.**

```
sqlite+pysqlite:///factoryflow.db
```

`sqlite:///factoryflow.db` is the same thing written shorter, since `pysqlite` is the default dialect. Three forms matter:

| URL | Meaning |
|---|---|
| `sqlite:///factoryflow.db` | Relative path, resolved against the process working directory |
| `sqlite:////var/lib/factoryflow/factoryflow.db` | Absolute path — note the four slashes |
| `sqlite:///:memory:` | In-memory database, discarded on close |

**The relative form is not used in the platform.** A path resolved against the working directory means the database found depends on where the process was started from, which is exactly the kind of environment-dependent behaviour `PROJECT_OVERVIEW.md` §16.12 requires the platform to avoid. The configured path is absolute.

**Engine configuration is minimal, and every setting is deliberate.**

| Setting | Value | Reason |
|---|---|---|
| `echo` | `False` | SQL logging is enabled per-session during development, not globally |
| `future` | Default in 2.0 | No legacy behaviour is requested |
| Connection pool | SQLAlchemy's default for file-based SQLite | See below |
| `connect_args` | `{"timeout": ...}` | Sets the busy timeout at the driver level (§42.8) |

**The pool is left at its default and that is the correct choice.** For a file-based SQLite URL, SQLAlchemy uses `QueuePool`, which maintains a small set of reusable connections. That suits this platform because each component holds a connection across many short transactions. `NullPool` would reopen the file per transaction, discarding the page cache each time; `StaticPool` would share one connection across threads, which SQLite's default threading mode does not permit. Neither is warranted.

**One thing the engine cannot do, and it is the most important operational fact in this document.** `PRAGMA foreign_keys = ON` is per-connection, is off by default, and does not persist in the file. A pooled connection that has not executed it enforces no referential integrity at all, silently. The engine must therefore issue it on **every** new connection, which is done with a connection-level event hook rather than once at startup. §42.9 states the requirement and what breaks without it.

## 5. Relationship with Python

The models are the platform's **type vocabulary**. Every component that touches data touches these classes, and every attribute carries a real annotation.

Consequences:

- A static type checker catches a misspelled attribute, a wrong-typed comparison, or a `None` dereference on a nullable column **before the code runs**. Across 512 columns that is a large share of the defects that would otherwise be runtime errors.
- `Decimal` is used for every `NUMERIC` column without exception. Money, quantities, probabilities, and sensor readings never pass through `float`. §38 explains why this is non-negotiable rather than fastidious.
- Nullability is visible in the annotation. A column the schema declares `NULL` is `Optional` in Python; a column it declares `NOT NULL` is not. There is no third state and no "nullable in the database but assumed present in code" — that assumption is the origin of most `NoneType` failures in data-heavy systems.
- Enum members, not strings, are compared. A comparison against a value from the wrong vocabulary is a type error rather than a silently false condition.

---

## 6. Relationship with the agents

Four agents plus the simulator and notification service read and write through these models. The frozen schema's ownership model (§25 of the schema document) states that **every table has exactly one writing component**, and the ORM layer must not blur that.

| Component | Writes (owns) | Reads |
|---|---|---|
| Factory Simulator | `machine_sensor_reading`, `machine_operational_status`, `machine_state_transition`, `production_run`, `production_progress`, `production_count`, `cycle_history`, `quality_inspection_result`, `scrap_record`, `inventory_movement`, `maintenance_work_record`, `machine_maintenance_activity` | Master data only |
| Monitoring Agent | `operational_event`, `operational_alert` | Telemetry, master thresholds |
| Prediction Agent | `prediction_feature_snapshot`, `prediction_result` | Telemetry, alerts, master |
| Supervisor Agent | `supervisor_context` | Alerts, predictions, business rules, health status |
| Decision Agent | `ai_recommendation` | Context, predictions, master taxonomy |
| Notification Service | `notification`, `notification_delivery` | Recommendations, alerts, recipients |
| Dashboard | `recommendation_action`, `dashboard_snapshot` | Nearly everything |
| Platform audit interface | `audit_log`, `system_health_status` | — |

**How the ORM supports this without inventing a mechanism.** The models are shared; the *sessions* are not. Each component opens its own session against its own transaction boundary and writes only the models it owns.

**Ownership is a convention, and this document says so rather than implying more than SQLite delivers.** SQLite has no roles, no `GRANT`, and no per-table permissions: any process that can open the file can write any table in it. The schema document's §50.2 states the same thing and names the compensating control — `created_by_component`, present on all 24 operational tables and check-constrained to the `platform_component` vocabulary, so every row records which component wrote it. A boundary violation is therefore **detectable after the fact** rather than preventable at write time, and the audit trail is what makes it detectable.

Ownership is *documented* per model in the **Write Components** subsection of every entry in Parts IV and V, and the mixin that supplies `created_by_component` is specified in §34. The ORM layer adds no enforcement of its own beyond that column, because an enforcement layer in shared Python would be a second authority — and the schema document already rejected that pattern for triggers, for the same reason.

**The one property the agents need that shapes the ORM.** Agent reasoning is a hot loop. An implicit lazy load inside it is a query the developer did not know they were making, repeated per row. That is why §19 sets `raise_on_sql` behaviour on relationships from the high-volume models: an accidental load fails loudly in development rather than quietly degrading in production.

---

## 7. Relationship with the dashboard

The dashboard is the widest reader in the platform and one of only two components that writes a table a human causes (`recommendation_action`).

Three ORM-relevant facts follow.

- **Reads are broad and shallow, not deep.** A dashboard panel wants many rows of a few columns. That is the profile eager loading serves well and lazy loading serves worst, so dashboard read paths specify their loading explicitly per query rather than relying on relationship defaults (§20).
- **`dashboard_snapshot` exists so the dashboard is not the reason the schema needs an aggregate.** It carries a `TEXT` document that the ORM maps as a `dict` and does not interpret. The ORM has no opinion about the document's shape; that belongs to the dashboard.
- **`recommendation_action` is append-only.** A human changing their mind produces a new row. The ORM enforces this as field immutability (§41.4), which is the one category of validation that genuinely belongs in the ORM rather than in the database.

---

## 8. ORM responsibilities and non-responsibilities

Stated as a boundary, because a large fraction of ORM-layer decay comes from this boundary eroding.

**The ORM layer is responsible for:**

1. One declarative class per table, mapped to the exact table name.
2. Correct Python and SQLAlchemy types for all 512 columns.
3. Correct nullability for all 512 columns.
4. Server-side default declarations matching the schema exactly.
5. Relationship declarations for the traversals the platform actually performs, with explicit loading strategy on each.
6. Enum classes bound to the 65 controlled vocabularies the schema enforces as `TEXT` plus a named `CHECK`.
7. Table arguments carrying unique constraints, check constraint names, and the 8 unique indexes, so Alembic can generate migrations.
8. Input normalisation and immutability enforcement, and nothing else, as validation (§41).
9. A single `MetaData` with a naming convention, so constraint names are deterministic.

**The ORM layer is explicitly not responsible for:**

| Not the ORM's job | Where it belongs |
|---|---|
| Business rules and thresholds | `business_rule`, read by agents |
| Duplicating the ~285 check constraints | SQLite |
| Cascading deletes | Nowhere — the schema is 162 `RESTRICT` and one `SET NULL`, and deletion is not a platform operation |
| Query composition for a specific screen or agent step | The calling component |
| Aggregation | `production_count`, `dashboard_snapshot`, and the reserved analytics group |
| Enabling `PRAGMA foreign_keys` | Engine configuration, via a connection event hook (§4.1, §42.9) |
| Serialisation to JSON for an API | Out of scope for this project phase |
| Connection pooling policy, retries, timeouts | Engine configuration, out of scope here |
| Migration authoring | Alembic, informed by these models |

No repository classes. No service layer. No DTOs. No Pydantic. A model is a mapped table and nothing more.

---

## 9. ORM lifecycle

The order in which the layer comes to life, because several ordering constraints are easy to violate and produce confusing failures.

**Import-time**

1. The declarative base is defined, carrying one `MetaData` with the naming convention (§32) and the `type_annotation_map` that resolves the Annotated type aliases (§38.4).
2. All 65 enum classes are defined. They depend on nothing.
3. The four mixins are defined (§34). They depend on nothing but the base's type map.
4. All 53 model classes are imported through a single aggregating module, in dependency order for readability although SQLAlchemy does not require it.
5. Relationships resolve. All relationship targets are **strings**, so a model may reference a model imported later without an import cycle (§18).
6. Mapper configuration completes on first use. Any typo in a relationship target or `back_populates` name fails here — at import, not at query time.

**Migration-time**

7. Alembic autogenerates against the populated `MetaData`. It sees tables, columns, constraints, and indexes. It does **not** see a widened vocabulary value list inside a `CHECK` (§40.8) — those stay hand-written, always, and on SQLite they are table rebuilds written in batch mode (§44.5).

**Runtime**

8. The engine is created, and a connection-level event hook issues `PRAGMA foreign_keys = ON` on every new connection. **This is part of the lifecycle rather than an optional tuning step:** the pragma is off by default and per-connection, so without the hook all 163 foreign keys are declared and inert (§4.1, §42.9).
9. A component opens a session scoped to one of the 20 transaction boundaries.
10. Objects are added, or loaded and mutated. Loading strategies are as specified per relationship, overridden per query where a component knows better.
11. Flush emits SQL. Identity values come back from SQLite as the driver's last-inserted rowid. Server defaults are applied by SQLite, not Python.
12. Commit or rollback. Sessions do not expire attributes on commit (§42.5).
13. The session closes. Nothing survives it except detached objects whose loaded attributes remain readable.

**The ordering constraint that matters most is now step 8 rather than a migration ordering.** A vocabulary is a check constraint on a column, so there is no type to create before the tables and no first-migration ordering trap — §40.5 records what that removes. What replaces it is the pragma: it must be set on **every** connection the pool hands out, not once at startup, and it is silently ignored inside a transaction. A layer that looks entirely correct will accept orphan rows if the hook is missing.

---
---

# Part II — ORM Architecture

---

## 10. Declarative architecture

**SQLAlchemy 2.0 typed Declarative, using a single `DeclarativeBase` subclass, `Mapped` annotations, and `mapped_column` directives.**

Three alternatives were available.

| Approach | Assessment |
|---|---|
| Imperative (Classical) mapping — `Table` objects mapped to plain classes | Separates a table's definition from its class across two locations. With 53 tables that is 106 places to look and two files to keep aligned. Rejected. |
| Legacy `declarative_base()` with untyped `Column` | Works, but forfeits the entire static-typing benefit described in §5. On 512 columns that benefit is the main reason to use 2.0 at all. Rejected. |
| **Typed Declarative with `DeclarativeBase`, `Mapped`, `mapped_column`** | **Selected.** One class per table, annotations carry Python type and nullability, and the type map removes repetition. |

What "typed" buys concretely: the annotation is the source of truth for the Python type and for nullability. `Optional` in the annotation means nullable; its absence means `NOT NULL`. There is no second place where nullability is declared and therefore no way for the two to disagree — which is precisely the defect that untyped `Column(nullable=...)` invites.

Where the annotation is insufficient — an explicit precision, a server default, a foreign key, a database column name that differs, a comment — the `mapped_column` directive carries it. The division is consistent across all 53 models: **annotation for type and nullability, directive for everything else.**

---

## 11. Base model philosophy

**One abstract declarative base. No abstract intermediate model classes. Behaviour shared through four narrow mixins.**

The base carries only what is genuinely universal to all 53 tables:

| Base carries | Why |
|---|---|
| The single `MetaData` with naming convention | §12, §32 |
| The `type_annotation_map` resolving Annotated aliases | §38.4 |
| Nothing else | See below |

The base carries **no columns.** Not the primary key, not the audit fields.

That is a deliberate rejection of the most common pattern in ORM codebases — a `Base` with `id`, `created_at`, `updated_at` on it — and the reason is specific to this schema.

- **Primary key names are not uniform, and this alone settles it.** Every table's key is `<table>_id`, per the frozen schema. There is no `id` column anywhere in the database. A base-level `id` would either rename 53 columns — forbidden — or require a mapping override on all 53, which is worse than declaring the column.
- **The key's *type* is uniform, and that is the one simplification available.** All 53 keys are `INTEGER PRIMARY KEY AUTOINCREMENT`, because SQLite has a single integer width (§25). A server-database design would have faced a narrow-versus-wide decision between the eight-row master tables and the 32-million-row telemetry table; here there is nothing to decide. It still does not justify a base-level column, for the naming reason above.
- **Audit columns are not uniform.** Master tables carry `is_active`, `created_at`, `updated_at`. Operational tables carry `created_at`, `created_by_component`, and `updated_at` on **only 8 of 24**. And `machine` carries `lifecycle_status` **instead of** `is_active`.

A single God-mixin or a fat base would need one exception for `machine` and sixteen exceptions for the append-only operational tables. **Seventeen exceptions to a rule means the rule is wrong.** §34 specifies four atomic mixins that compose without exceptions instead.

---

## 12. Metadata organization

**One `MetaData` instance for the entire database, carried on the declarative base.**

SQLite has a single table namespace per database file, so there is no schema object to model and no `schema=` argument to supply anywhere in this layer. The question a server-database design would face — one `MetaData` or several — does not arise: there is exactly one namespace and therefore exactly one `MetaData`.

| Option | Assessment |
|---|---|
| Several `MetaData` instances, one per logical group | Would mirror the master/operational/system grouping, but the grouping is documentary (§13) and not a database construct. Alembic autogenerate targets a single `target_metadata`, and every one of the 82 operational→master foreign keys would span a `MetaData` boundary for no benefit. Rejected |
| **One `MetaData`, no schema qualification anywhere** | **Selected.** Alembic sees everything through one target. Every foreign key resolves within one collection because every table is in one namespace |

The single `MetaData` carries the naming convention specified in §32. That convention is what makes Alembic-generated constraint names match the names `FACTORY_SQLITE_DATABASE_SCHEMA.md` assigns, which is what makes a generated migration reviewable against the schema document.

**Table names are bare.** A model's `__tablename__` is `machine`, never a dotted or prefixed name. All 53 names are globally unique across the database, and none required disambiguation.

**The analytics group is reserved and empty.** No model exists for it. It appears in this specification only as a note that a later phase's derived structures belong there rather than accumulating among the operational models.

## 13. Logical organization

53 tables, 53 models, **one SQLite database file**.

| Group | Tables | Models | Character |
|---|---|---|---|
| Master | 29 | M1–M29 | Reference data. Zero outbound foreign keys from the group as a whole |
| Operational | 22 | O1–O22 | Event and transaction data |
| System | 2 | O23–O24 | `audit_log`, `system_health_status` |
| Analytics | 0 | — | Reserved. Empty by design |

**The grouping is documentary, and it does real work in exactly two places.** It organises this specification, and it organises the ORM package layout (§44) — `models/master/`, `models/operational/`, `models/system/`. It appears nowhere in the database, in no table name, and in no model attribute.

**The system models are numbered O23 and O24** because `FACTORY_SQLITE_DATABASE_SCHEMA.md` numbers them that way within its operational part, and this document does not renumber anything. Their group membership is stated in each entry so there is no ambiguity.

**The property that shapes everything downstream: the master group has zero outbound foreign keys.** All 163 foreign keys are master→master (46), operational→master (81), system→master (1), or operational→operational (36). No master table points at an operational or system table. This is what makes the import graph acyclic (§18) and what allows master models to be imported first, always, without conditional imports.

**One consequence of the single namespace worth stating.** Because there is no schema qualification, a table name collision would be a hard error rather than something two namespaces could absorb. All 53 names are distinct, and §45.1 records that adding a model requires checking its name against the full set rather than against one group.

## 14. Model organization

**One class per table. No exceptions, no merges, no splits, no polymorphic hierarchies.**

| Grouping | Count | Character |
|---|---|---|
| Master reference models | 29 | Small, wide, rarely written, heavily read |
| Operational event models | 24 | Narrow, high-volume, append-only or narrowly mutable |
| Association object models | 5 | Junctions that carry attributes: `product_line_capability`, `machine_type_parameter`, `bill_of_materials`, `machine_type_failure_mode`, `alert_threshold_rule` |
| One-to-one models | 5 | `maintenance_engineer`, `notification_recipient`, `machine_operational_status`, `system_health_status`, `ai_recommendation` |

The association objects are not a separate category of class — they are ordinary models. They are listed separately because **all five carry attributes beyond their two foreign keys**, which is why `secondary=` is used nowhere in this specification (§37.5).

Class naming is mechanical: PascalCase of the table name, singular exactly as the table is singular. `machine_sensor_reading` becomes `MachineSensorReading`. `bill_of_materials` becomes `BillOfMaterials` — the table name is already singular in the frozen schema despite the plural-looking `materials`, and it is not "corrected". `ai_recommendation` becomes `AiRecommendation`, not `AIRecommendation`, because mechanical PascalCase of every segment is a rule that needs no exception list (§45.2).

---

## 15. Package organization

Summarised here because dependency rules reference it; specified fully in §44.

Eight concerns, each in its own module or package: metadata and base; enums; mixins; master models; operational models; system models; an aggregating registry that imports everything; and session configuration. Dependencies flow in one direction only, from models toward base and enums, never back.

---

## 16. Dependency rules

Four rules, in force across the whole layer.

**R1 — Base and enums depend on nothing.** They import from SQLAlchemy and the standard library only. No model, no mixin.

**R2 — Mixins depend on the base only.** They declare columns using the base's type map. They never reference a model or an enum other than `platform_component`, which the provenance mixin needs.

**R3 — Models depend on base, enums, and mixins. Never on each other, at import time.** A model may *reference* another model, always by string name in a relationship. It never imports one at module scope for that purpose. Type-checking imports are permitted under a `TYPE_CHECKING` guard, which the runtime never executes (§17).

**R4 — Nothing above the ORM is imported by the ORM.** No model imports an agent, the simulator, the dashboard, or a configuration module. The dependency arrow points one way, always.

The result is a strictly layered import graph:

- Layer 0 — SQLAlchemy, standard library
- Layer 1 — metadata and base, enums
- Layer 2 — mixins
- Layer 3 — all 53 models, mutually independent at import time
- Layer 4 — the aggregating registry
- Layer 5 — session configuration

**No cycle is possible**, because layer 3 has no intra-layer edges.

---

## 17. Import strategy

Four rules.

1. **Relationship targets are always strings.** A relationship to `Machine` names it `"Machine"`, resolved by SQLAlchemy's registry after all models are imported. This is what makes R3 achievable, and it is applied uniformly — never "strings only where needed", because a mixed convention means a reader must check which style a given line uses.

2. **Type-checking-only imports go under a `TYPE_CHECKING` guard.** A relationship's annotation needs the target class name for the type checker. The guard gives the checker the import and gives the runtime nothing. This is the mechanism that lets `Machine` and `MachineOperationalStatus` reference each other with full type information and zero runtime coupling.

3. **The aggregating registry is the only module that imports all models.** Alembic imports it. Application code imports the models it needs directly, or the registry for convenience. Nothing else needs to know the full list.

4. **Import order within a module follows §45.7:** standard library, then SQLAlchemy, then intra-package (base, enums, mixins), then the `TYPE_CHECKING` block last. Alphabetical within each group.

---

## 18. Circular dependency prevention

The schema contains bidirectional *data* relationships. It contains no circular *dependency*, and this section records both why and how that is preserved.

**In the database.** The frozen schema (§39) designed out four candidate cycles and states the graph is acyclic. The two that most obviously look circular are worth naming, because a reader will otherwise expect a problem:

- `machine` ↔ `machine_operational_status`. One row of status per machine, and status points at machine. The reverse is *not* a column on `machine`; it is a relationship. No cycle.
- `operational_alert` ↔ `operational_event`. Events belong to an alert mandatorily; the alert's `event_count` is a maintained counter, not a foreign key. No cycle.
- `maintenance_work_record` → `ai_recommendation` → `supervisor_context` → `prediction_result` → `operational_alert`, while `machine_state_transition` → `maintenance_work_record`. A long chain, still a chain.
- `bill_of_materials` referencing `inventory_item` **twice** (material and substitute), and `quality_inspection_result`/`scrap_record` referencing `machine` twice (location and attribution). Self-referential in appearance, acyclic in fact, because the target is master data that references nothing operational.

**In Python.** Prevented structurally rather than by care:

| Mechanism | Effect |
|---|---|
| String relationship targets | A model never needs another model's class object at import time |
| `TYPE_CHECKING` guard for annotations | Type information without runtime import |
| R3 (§16) | No model-to-model import at module scope, ever |
| Single aggregating registry | One place where the full set is assembled, imported by nothing the models import |

**The failure mode this avoids, stated so it is recognised if it appears.** If a model imports another model directly and both do so, Python raises `ImportError` at a location unrelated to the cause, often naming a third module. With 53 models the search space is large. Applying string targets uniformly makes the failure impossible rather than rare.

---

## 19. Lazy loading strategy

This is the section where the ORM's main risk is addressed, so it states a rule rather than a preference.

**Default for a mapped relationship in this specification: `select` for many-to-one, `raise_on_sql` for anything on a high-volume model, and unmapped for anything unbounded.**

### 19.1 Rule L1 — an unbounded reverse collection is not mapped at all

If a parent may have more than roughly a hundred children, the collection is **not declared as a relationship**. Not with `lazy="raise"`, not with `lazy="dynamic"`. It is absent.

Consequences, listed because they are the surprising part of this specification:

| Not mapped | Would have been | Why absent |
|---|---|---|
| `Machine.sensor_readings` | one-to-many | ~4 million rows per machine per year |
| `Machine.state_transitions`, `Machine.production_counts`, `Machine.cycle_history` | one-to-many | Tens of thousands to hundreds of thousands per machine |
| All 17 reverse collections on `Shift` | one-to-many each | 4 shift rows against every operational table in the database. `Shift.sensor_readings` would be 8 million rows on a 4-row table |
| `ProductionRun.cycle_history` | one-to-many | ~650 per run and unbounded upward |
| `ProductionRun.sensor_readings` | one-to-many | Millions per run |
| `Worker.audit_log_entries` | one-to-many | ~730,000 rows a year |
| `FailureSeverityLevel.*` operational reverses | one-to-many each | 5 rows against six operational tables |

Reasoning: a mapped collection is an invitation. `lazy="raise"` converts the invitation into a runtime error, which is better than a silent 8-million-row load but still means the attribute exists, appears in autocomplete, and appears in a reviewer's mental model of the class. **Absence is the only loading strategy that cannot be misused.** The query these attributes would serve is a filtered, limited, explicitly written query against the child model — which is what the platform actually wants in every case.

### 19.2 Rule L2 — bounded collections are mapped with `select`

A collection whose size is structurally bounded is mapped and lazy-loaded. Examples: `Plant.plant_areas` (7), `MachineType.parameters` (~5 each), `AlertThresholdProfile.rules` (~4 each), `MaintenanceWorkRecord.activities` (single digits), `Notification.deliveries` (1–3), `OperationalAlert.events` (bounded by `event_count`, typically under 10).

### 19.3 Rule L3 — many-to-one is mapped with `select` and loaded eagerly per query when needed

Every foreign key gets a many-to-one relationship. It resolves to at most one row, so lazy `select` is one indexed primary-key lookup — cheap, and usually served from the session's identity map because master data is small and repeatedly referenced. Where a component reads many rows and needs the parent of each, it specifies `joinedload` or `selectinload` **at the query**, which is where that knowledge lives (§20).

### 19.4 Rule L4 — relationships on the four highest-volume models use `raise_on_sql`

`MachineSensorReading`, `CycleHistory`, `ProductionCount`, and `AuditLog` are read in bulk, inside loops, by agents. Their many-to-one relationships are declared with `raise_on_sql`: they are usable when the caller has eagerly loaded them, and they raise immediately when they would emit SQL.

`raise_on_sql` rather than `raise` is deliberate — it permits the load when the object is already present in the identity map, which is the common case for `machine` and `shift`, and raises only for a genuine database round trip. The effect is that an N+1 on the 32-million-row table is a loud failure in development instead of a slow afternoon in production.

### 19.5 Summary

| Situation | Strategy |
|---|---|
| Unbounded reverse collection | **Not mapped** |
| Bounded reverse collection | `select` |
| Many-to-one, ordinary model | `select` |
| Many-to-one, high-volume model | `raise_on_sql` |
| One-to-one | `select`, with `uselist=False` on the scalar side |
| Anything a specific query needs eagerly | Specified per query, not per relationship |

---

## 20. Eager loading strategy

**No relationship declares `joinedload` or `selectinload` as its default. Eager loading is a property of a query, not of a model.**

The reason is that the correct strategy differs per caller for the same relationship. `PredictionResult.machine` should be joined when the Decision Agent reads one prediction, and batched with `selectinload` when the dashboard reads two hundred. Baking either into the model makes the other caller pay for it silently.

Guidance for callers, so the choice is not arbitrary:

| Loader | When | Why |
|---|---|---|
| `joinedload` | Many-to-one, one or few root rows, small parent | One round trip. `LEFT OUTER JOIN` widens the result set, which is harmless when the parent is a master row |
| `selectinload` | Any collection; many-to-one across many root rows | A second `IN`-clause query. Does not multiply rows, which matters for collections where a join would duplicate the parent per child |
| `raiseload` | Defensively, on a query that must not lazy-load anything | Turns an accidental traversal into an error at the query boundary |
| `contains_eager` | The relationship is already in an explicit join the caller wrote | Reuses the join instead of adding a second one |

**Never `joinedload` on a collection from a high-volume child.** Joining `machine_sensor_reading` onto `machine` multiplies the machine row by four million. `selectinload` is the only correct collection loader at this scale, and §19.1 means most such collections do not exist to be loaded.

---

## 21. Relationship ownership

For every bidirectional relationship, one side is the **owner** and the other is the **reverse**. Ownership is not decorative; it determines naming and it determines which side a reader treats as authoritative.

**The rule: the side holding the foreign key column owns the relationship.**

| Side | Holds | Name form | Example |
|---|---|---|---|
| Owner (many-to-one) | The FK column | Singular noun, the target's role | `PlantArea.plant` |
| Reverse (one-to-many) | Nothing | Plural noun, the child's role | `Plant.plant_areas` |

Both sides name each other through `back_populates`. `backref` is used nowhere — it creates an attribute on a class whose own file does not mention it, and with 53 models that is 53 files a reader cannot trust to be complete. Explicit `back_populates` on both sides costs one line and makes every class self-describing.

**Where the reverse is unmapped (§19.1), the owning side still exists and still declares no `back_populates`.** `MachineSensorReading.machine` is a complete, valid, one-directional relationship. Unidirectionality is a legitimate configuration, not a defect, and here it is the load-bearing decision that keeps a 32-million-row table off `Machine`.

**Role-qualified relationships.** Where a model holds two foreign keys to the same target, both relationships are named for their role and both specify which foreign key they use. There are nine such cases, and they are the ones most likely to be mis-implemented:

| Model | Target | Relationship names |
|---|---|---|
| `BillOfMaterials` | `InventoryItem` | `inventory_item`, `substitute_inventory_item` |
| `QualityInspectionResult` | `Machine` | `machine`, `attributed_machine` |
| `ScrapRecord` | `Machine` | `machine`, `attributed_machine` |
| `MaintenanceWorkRecord` | `FailureCategory` | `reported_failure_category`, `confirmed_failure_category` |
| `AlertThresholdRule` | `FailureSeverityLevel` | `warning_severity_level`, `critical_severity_level` |
| `OperationalAlert` | `FailureSeverityLevel` | `initial_severity_level`, `current_severity_level` |
| `MaintenanceEngineer` | `MaintenanceSpecialization` enum | `primary_specialization`, `secondary_specialization` (columns, not relationships) |
| `MachineOperationalStatus` | `ProductionRun` | `current_production_run` |
| `RecommendationAction` | `MaintenanceWorkRecord` | `resulting_work_record` |

---

## 22. Cascade philosophy

**The ORM performs no cascading deletes. Anywhere.**

This follows directly from the frozen schema rather than being an ORM-layer opinion: of 163 foreign keys, **162 are `ON DELETE RESTRICT`** and **one is `ON DELETE SET NULL`** (`operational_event.triggering_reading_id`). **Zero are `CASCADE`.**

**And all 163 are inert unless `PRAGMA foreign_keys = ON` is set on the connection** (§42.9). That is worth stating in this section specifically, because the no-cascade position depends on `RESTRICT` actually restricting. Without the pragma the schema does not merely fail to restrict — it accepts the delete and leaves the children pointing at nothing, which is the outcome the whole cascade discussion exists to prevent.

| Cascade setting | Value in this specification |
|---|---|
| `cascade` on relationships | `"save-update, merge"` — the SQLAlchemy default. Explicitly **not** `"all, delete-orphan"` |
| `passive_deletes` | Not set, because there is no delete path for it to optimise |
| `delete-orphan` | Used nowhere |
| `single_parent` | Used nowhere |

**Why `delete-orphan` is wrong here even where it looks right.** `Notification` → `NotificationDelivery` is the strongest candidate in the schema: a delivery has no meaning without its notification. But the schema declares that foreign key `RESTRICT`, and deleting a notification is not an operation the platform performs — retention is handled by a purge job that deletes children first, in dependency order, as the schema's §44.5 specifies. Adding `delete-orphan` would create an ORM-side deletion behaviour that no component uses and that would silently diverge from the purge job's ordering the moment either changed.

**The single `SET NULL`, and what the ORM must not do about it.** `operational_event.triggering_reading_id` becomes `NULL` when the 90-day telemetry purge removes the reading it cites. The event keeps its `observed_value` and `threshold_value_breached`, so its evidence survives; only the pointer to the raw row is lost. The ORM declares the column `Optional` and the relationship ordinary. It does **not** model the purge, and it does not treat a `NULL` here as an anomaly — a null `triggering_reading` on an event older than 90 days is the designed steady state.

**What this means for a developer.** Deleting a parent that has children raises a database `IntegrityError`. That is correct and intended. The `RESTRICT` chain is what guarantees the explainability contract's evidence trail cannot be broken by a careless delete, and the ORM's job is to not undermine it.

---

## 23. Session philosophy

**A synchronous `Session`, created per transaction boundary, owned by the component that opened it, never shared across threads.**

| Decision | Choice | Rejected alternative |
|---|---|---|
| Sync or async | **Synchronous** | `AsyncSession`. Nothing in the frozen documents describes an async runtime, and async would change the shape of every component's control flow for no stated requirement |
| Scope | **One session per transaction boundary from schema §46** | Session-per-request or session-per-component-lifetime. Long-lived sessions accumulate identity-map entries and hold connections |
| Registry | **Explicit session objects, created by a factory** | `scoped_session`. Thread-local implicit sessions make ownership ambiguous, which conflicts directly with the single-writer model in §6 |
| `expire_on_commit` | **`False`** | The default `True`. See below |
| `autoflush` | **`True`** (default) | Manual flushing. Autoflush keeps reads consistent with pending writes within a boundary |

**Why `expire_on_commit=False`.** With the default, every attribute of every object is expired at commit, so the next attribute access emits a `SELECT`. Combined with §19.4's `raise_on_sql`, that would turn ordinary post-commit logging into an exception. More importantly, it means a component that commits and then reads what it wrote pays for a second round trip it did not ask for. Setting it `False` makes committed objects readable as plain data, which is what every component in this platform does with them.

The trade-off, stated: a long-lived object may hold values another transaction has since changed. It is accepted because sessions here are short and scoped to one boundary, and because a component reading data it must know is current re-queries rather than reusing a detached object.

---

## 24. Transaction boundaries

The frozen schema document §46 already specifies **twenty** transaction boundaries by name. **This specification reuses them and defines none of its own.** Each becomes one session scope.

| ID | Boundary | Models written | Owner |
|---|---|---|---|
| T-SIM-1 | Sensor reading batch | `MachineSensorReading` | Simulator |
| T-SIM-2 | State transition + status update | `MachineStateTransition`, `MachineOperationalStatus` | Simulator |
| T-SIM-3 | Cycle completion | `CycleHistory` | Simulator |
| T-SIM-4 | Interval count close | `ProductionCount` | Simulator |
| T-SIM-5 | Progress snapshot | `ProductionProgress` | Simulator |
| T-SIM-6 | Run lifecycle change | `ProductionRun` | Simulator |
| T-SIM-7 | Quality result and scrap | `QualityInspectionResult`, `ScrapRecord`, `InventoryMovement` | Simulator |
| T-SIM-8 | Maintenance work and activity | `MaintenanceWorkRecord`, `MachineMaintenanceActivity`, `InventoryMovement` | Simulator |
| T-MON-1 | Event + alert creation | `OperationalEvent`, `OperationalAlert` | Monitoring Agent |
| T-MON-2 | Event appended to existing alert | `OperationalEvent`, `OperationalAlert` | Monitoring Agent |
| T-MON-3 | Alert acknowledgement or resolution | `OperationalAlert` | Monitoring Agent |
| T-PRED-1 | Snapshot + prediction | `PredictionFeatureSnapshot`, `PredictionResult` | Prediction Agent |
| T-SUP-1 | Context assembly | `SupervisorContext` | Supervisor Agent |
| T-DEC-1 | Recommendation generation | `AiRecommendation` | Decision Agent |
| T-NOT-1 | Notification composition | `Notification` | Notification Service |
| T-NOT-2 | Delivery attempt | `NotificationDelivery` | Notification Service |
| T-NOT-3 | Delivery confirmation | `NotificationDelivery` | Notification Service |
| T-DASH-1 | Human action recorded | `RecommendationAction` | Dashboard |
| T-DASH-2 | Snapshot generation | `DashboardSnapshot` | Dashboard |
| T-RET-1 | Retention purge | Deletes, in dependency order | Retention job |

**One discrepancy between the two frozen documents, recorded rather than silently resolved.** Both specify twenty boundaries with the same IDs, but the schema document's §46 sub-divides the Simulator's boundaries differently: its T-SIM-4 covers the interval close *and* the progress snapshot together, so its T-SIM-5 through T-SIM-8 are run lifecycle, quality and scrap, maintenance progression, and work order closure. The table above keeps this document's own division, in which the interval close and the progress snapshot are separate scopes. **The atomic units are the same in both; only which ID names which unit differs**, and no boundary in either document spans a set of models the other splits across a commit. An implementer should treat the schema document's §46 as authoritative when reading a boundary ID in a schema-side statement, and this table when reading one here. Nothing about the ORM's session scoping changes either way.

**The engine serialises these boundaries whether or not the code intends it.** SQLite permits one writer at a time database-wide (§42.8), so any two of the twenty that would overlap in time are ordered by the write lock rather than by isolation rules. That is what makes every boundary above atomic without any per-boundary configuration — and it is also why a boundary that stays open longer than its work requires blocks every other writer, which is the practical reason the scopes are drawn as tightly as they are.

**Three ORM-relevant properties of this list.**

**T-SIM-2 is the boundary that most constrains the ORM.** Inserting a `MachineStateTransition` and updating the corresponding `MachineOperationalStatus` must be atomic — the status row's `last_state_transition_id` points at the row inserted in the same transaction. That requires a flush mid-transaction to obtain the generated identity, then the update, then one commit. Autoflush handles it, and the ordering is stated here so it is not rediscovered.

**T-MON-1 and T-MON-2 differ only in whether the alert exists.** `operational_event.operational_alert_id` is `NOT NULL`, so an event can never be written without an alert. The ORM expresses this as a non-optional relationship, and the correlation logic that decides create-or-append belongs to the Monitoring Agent, not the model.

`T-RET-1` writes nothing and deletes in child-first order. It is the one boundary where the ORM's lack of cascade (§22) is load-bearing: the job's explicit ordering is the only deletion ordering in the system.

---
---

# Part III — Shared Base Model

---

## 25. Primary key strategy

Every one of the 53 tables has a **single-column surrogate primary key** named `<table>_id`. There are no composite primary keys and no natural-key primary keys anywhere in the database.

| Group | Column name | SQLite declaration | Python type | SQLAlchemy type |
|---|---|---|---|---|
| 29 master models | `<table>_id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | `int` | `Integer`, primary key, `autoincrement=True` |
| 24 operational models | `<table>_id` | `INTEGER PRIMARY KEY AUTOINCREMENT` | `int` | `Integer`, primary key, `autoincrement=True` |

**Both groups use the identical declaration, and that is a simplification SQLite provides rather than a decision this layer makes.** SQLite has one integer type — a signed 64-bit value stored in 1 to 8 bytes according to magnitude — so there is no narrow-versus-wide key trade-off, no `BigInteger` for the high-volume tables, and no migration risk from having chosen wrong. The 32-million-row telemetry table and the eight-row status table declare their keys the same way.

**`Integer` is mandatory, not preferred.** SQLAlchemy's `BigInteger` renders `BIGINT` on SQLite, and SQLite makes a column an alias for its internal 64-bit rowid **only** when the declared type is exactly `INTEGER`. A `BIGINT` primary key would be an ordinary indexed column, `AUTOINCREMENT` would be rejected on it, and the fastest access path in the engine would be lost. Every primary key in this specification is therefore `Integer`.

**The key is declared on each model, not inherited from the base.** §11 gives the three reasons. The most concrete: there is no `id` column in this database, and inheriting one would require renaming 53 columns, which the frozen documents forbid.

**Two Annotated aliases carry the repetition instead**, resolved through the base's `type_annotation_map`:

| Alias | Resolves to | Used by |
|---|---|---|
| `MasterPk` | `Integer`, primary key, `autoincrement=True` | 29 master models |
| `OperationalPk` | `Integer`, primary key, `autoincrement=True` | 24 operational models |

**The two aliases resolve identically, and both names are kept deliberately.** They resolved to different types in a server-database design and they do not here. Retaining both preserves the master/operational distinction that the package layout, the mixin compositions, and every model entry in Parts IV and V rely on — a model's key alias states which group it belongs to at a glance. Collapsing them to one name would remove that signal for no gain.

**Business keys are unique constraints, never primary keys.** 27 of 29 master tables carry a `<table>_code` business key with a unique constraint; `machine` also carries `serial_number` unique, and `notification_recipient` and `bill_of_materials` have no code column because their identity is their parent pair. Business keys are mapped as ordinary unique columns. Lookups by code are ordinary filtered queries. **A relationship never targets a business key**, exactly as the frozen schema specifies.

## 26. Identity generation

`INTEGER PRIMARY KEY AUTOINCREMENT` gives SQLite responsibility for assigning keys, and the ORM's obligation is to stay out of the way.

| Property | Value | Consequence |
|---|---|---|
| `autoincrement` | `True` | SQLAlchemy omits the column from `INSERT` |
| Explicit value | Never supplied | SQLite would accept it and disturb the high-water mark |
| Value availability | After flush | The key is `None` until flush, then populated from the driver's last-inserted-rowid |
| `server_default` on the key | Not declared | Identity is a column property, not a default |
| Key reuse after delete | **Impossible** — that is what `AUTOINCREMENT` buys | Required because `audit_log.entity_id` and `operational_event.triggering_reading_id` cite rows by identifier |

**SQLite does not refuse an explicit key, so the prohibition is the ORM's to enforce.** A server database using `GENERATED ALWAYS` would reject a supplied value outright. SQLite accepts it, inserts it, and — if it exceeds the current maximum — advances the high-water mark past it. Supplying keys is therefore prohibited by convention, and §45.4 lists it as a review-checklist item rather than relying on the engine.

**How the key comes back.** The `sqlite3` driver reports the inserted rowid, and SQLAlchemy reads it after the `INSERT` to populate the attribute. There is no `RETURNING` clause involved on the default path, which matters for the bulk case below.

**The pattern this forces, and it is the right one.** Code that needs a generated key to build a child row must flush first. T-SIM-2 (§24) is the canonical case: insert the transition, flush to obtain `machine_state_transition_id`, assign it to `machine_operational_status.last_state_transition_id`, commit. Assigning the ORM object rather than the integer — setting the relationship attribute instead of the foreign key attribute — lets SQLAlchemy order the statements itself, and is the preferred form.

**Bulk inserts on `machine_sensor_reading`.** The simulator writes ~87,000 rows a day. Because nothing references a reading at insert time, the returned keys are not needed, and the insert should be executed as a single multi-row statement without fetching identifiers. §43.7 records this: the difference is one statement against 87,000 round trips through the ORM's unit of work.

**One cost of `AUTOINCREMENT`, stated honestly.** SQLite maintains the high-water mark in an internal `sqlite_sequence` table, which is one extra small write per insert on the affected table. On the telemetry table that is measurable and small, and it is accepted because identifier reuse would silently repoint historical citations at unrelated rows.

## 27. Audit fields

Audit columns are **not universal**, and the variance is the reason §34 uses four mixins instead of one base.

**Master tables — all 29:**

| Column | SQLite type | Null | Default | Python type |
|---|---|---|---|---|
| `is_active` | `INTEGER` | NOT NULL | `1` | `bool` |
| `created_at` | `DATETIME` | NOT NULL | `CURRENT_TIMESTAMP` | `datetime` |
| `updated_at` | `DATETIME` | NOT NULL | `CURRENT_TIMESTAMP` | `datetime` |

**The documented exception: `machine` (M10) carries `lifecycle_status` instead of `is_active`.** A `machine_lifecycle_status` vocabulary distinguishes in-service, standby, under overhaul, and decommissioned — states a two-valued flag cannot express and the Monitoring Agent needs. `Machine` therefore composes the timestamp mixins but **not** the soft-delete mixin, and declares `lifecycle_status` as an ordinary vocabulary column.

**Operational tables — all 24:**

| Column | SQLite type | Null | Default | Applies to |
|---|---|---|---|---|
| `created_at` | `DATETIME` | NOT NULL | `CURRENT_TIMESTAMP` | All 24 |
| `created_by_component` | `TEXT` | NOT NULL | — | All 24 |
| `updated_at` | `DATETIME` | NOT NULL | `CURRENT_TIMESTAMP` | **Only 8** |

The eight models that carry `updated_at` are exactly the eight that receive `UPDATE`:

`MachineOperationalStatus`, `ProductionRun`, `ProductionCount`, `MaintenanceWorkRecord`, `OperationalAlert`, `NotificationDelivery`, `DashboardSnapshot`, `SystemHealthStatus`.

**The remaining 16 operational models are append-only.** They do not carry `updated_at` because they are never updated. The absence of the column is the schema's statement of immutability, and the ORM reinforces it with field-level immutability validation (§41.4) rather than by adding a column the database does not have.

**`created_by_component` is provenance, not decoration.** It is a `TEXT` column bound to the `PlatformComponent` enum and constrained by `ck_<table>_created_by_component_allowed`. It is `NOT NULL` with no default, so the writing component must state its identity explicitly on every insert. That friction is intentional, and in SQLite it carries more weight than it would elsewhere: because the engine has no privilege system to restrict who writes which table (§50 of the schema document), this column is the only thing that makes a write attributable after the fact.

## 28. Timestamp strategy

**Every timestamp column is `DATETIME` and stores UTC. Python-side values are timezone-aware `datetime` objects throughout this layer.**

| Concern | Decision |
|---|---|
| SQLAlchemy type | `DateTime` |
| Python type | `datetime.datetime`, always tz-aware, always UTC |
| Annotated alias | `TimestampTz` |
| Storage format | ISO-8601 text, `YYYY-MM-DD HH:MM:SS`, in UTC |
| Display timezone | `plant.timezone`, an IANA name, applied at presentation only |
| `created_at` / `updated_at` defaults | **Server-side** `CURRENT_TIMESTAMP`, declared as `server_default` |
| `updated_at` on update | **Application-maintained**, declared as `onupdate` at the ORM level |

**SQLite stores no time zone, and this is the single most important consequence in this section.** There is no timezone-aware column type. `DateTime(timezone=True)` is accepted by the SQLite dialect but the offset is **not** persisted — a value written as tz-aware comes back naive. Relying on the flag would silently produce naive datetimes at the application boundary, which is precisely the defect §38.3 exists to prevent.

**The resolution is that the ORM owns the contract the database cannot express.**

1. **Every value written is UTC**, converted by the application before it reaches the session.
2. **`@validates` rejects a naive datetime** on every timestamp attribute (§41.3). This is the hook that makes the convention enforceable rather than aspirational.
3. **Every value read is re-attached to UTC** by the type layer, so the attribute is tz-aware on the way out as well as in. Without this step the round trip is lossy.
4. **Local time exists only at presentation**, derived from `plant.timezone`.

**The `TimestampTz` alias name is retained deliberately.** It no longer names a database capability — it names the *contract*: the value is a UTC instant, tz-aware in Python, regardless of the fact that the column stores an offset-free string. Renaming it would break the correspondence with §38.4 and with every model entry, and would lose the reminder that these attributes are instants rather than local wall-clock times.

**Text ordering is chronological.** Because the stored format is fixed-width ISO-8601, a `DATETIME` column sorts correctly as text, so every `ORDER BY` on a timestamp and every range comparison in a check constraint works without conversion.

**Record time and event time are different columns and must not be conflated.** `machine_sensor_reading.recorded_at` is when the reading was taken. `created_at` is when the row was written. `operational_event.detected_at` is when detection happened; `created_at` is when the event row landed. Every operational model has both, and the ORM keeps them as separate attributes with no derivation between them.

**`DATE` and `TIME` are separate concerns.** 19 `DATE` columns map to `datetime.date`; `shift.start_time` and `shift.end_time` map to `datetime.time`. Both are stored as ISO-8601 text. `shift.crosses_midnight` exists because `end_time < start_time` for night shifts, and the ORM stores both times as given without deriving anything from them. These two columns are the one place in the schema where a stored value is deliberately not UTC, because a recurring wall-clock time is not an instant.

## 29. Boolean defaults

The database carries 45 flag columns. All but one are `NOT NULL` with an explicit server default, and the default is not uniformly false.

**Storage is `INTEGER` holding 0 or 1.** SQLite has no boolean type. The ORM maps these with SQLAlchemy's `Boolean`, which presents `bool` in Python and stores 0 or 1 — so the Python type the frozen models specify is preserved exactly while the storage matches the schema document's `INTEGER` declaration.

**`Boolean(create_constraint=True)` is used**, which emits the `CHECK (col IN (0, 1))` domain constraint the schema document specifies for every flag. Without it the column would accept `2` or `-1`, and a truthiness test in application code would treat several out-of-domain values as true. The constraint is what makes the column genuinely two-valued, and it is the direct replacement for what a boolean type would have guaranteed.

| Default | Count | Examples |
|---|---|---|
| `0` (false) | Majority | `is_climate_controlled`, `is_bottleneck`, `is_primary_line`, `is_managerial`, `is_critical_spare`, `did_stop_line`, `interrupted`, `is_suppressed`, `requires_line_stop` |
| `1` (true) | Several | `is_active` (all 28 soft-deletable master models), `is_production_shift`, `tooling_available`, `requires_condition_monitoring`, `is_ml_feature`, `is_monitored`, `email_enabled`, `is_approved_vendor`, `can_be_deferred`, `is_enabled` |
| No default, `NOT NULL` | 4 | `production_progress.is_behind_schedule`, `prediction_feature_snapshot.is_sufficient_for_inference`, `notification.requires_acknowledgement`, `ai_recommendation.contract_complete` |
| Nullable | 1 | `business_rule.value_boolean` — nullable because only one of three value columns is populated per rule |

**All defaults are declared `server_default`, never Python-side `default`.** The distinction matters more in SQLite than elsewhere: a Python-side default is applied by SQLAlchemy and is invisible to any other writer, and SQLite has no privilege system preventing another writer from existing. A server default is the column's actual definition and applies to every insert from any source, including a manual `sqlite3` session. Since the frozen schema declares these as column defaults, the ORM declares them the same way, and Alembic then generates DDL that matches the schema document.

**The four with no default are computed, not defaulted.** `is_behind_schedule` is a judgement the simulator makes from `schedule_variance_minutes`. `contract_complete` is the Decision Agent's assertion that every element of the explainability contract is present. Giving either a default would let a writer omit a value the platform requires it to decide.

## 30. Soft delete philosophy

**Nothing in this database is ever hard-deleted by application code.**

| Data class | Retirement mechanism | ORM expression |
|---|---|---|
| Master data, 28 of 29 tables | `is_active = 0` | `SoftDeleteMixin` |
| `machine` | `lifecycle_status = 'decommissioned'` | Ordinary vocabulary column |
| Operational data, all 24 tables | **No soft delete.** Rows are purged or archived by retention policy | None |

The reason is stated in the frozen schema and drives the ORM's non-behaviour: **operational history references master rows.** Deleting a `worker` would orphan years of maintenance records, acknowledgements, and notifications. Deleting a `machine` would destroy the telemetry, event, prediction, and recommendation trail that the explainability contract rests on. The 162 `RESTRICT` foreign keys make the attempt fail — **provided `PRAGMA foreign_keys = ON` is set** (§42.9) — and `is_active` is how retirement is expressed instead.

**What the ORM deliberately does not do.**

| Tempting feature | Why rejected |
|---|---|
| A global query filter that hides `is_active = 0` rows | Would make historical queries silently wrong. A `maintenance_work_record` from 2024 must resolve its engineer even if that engineer has left. An automatic filter would return `None` for a `NOT NULL` foreign key, which is a nonsensical state |
| A `delete()` method that sets `is_active = 0` | Overloads a standard verb with non-standard behaviour. Setting the attribute is clearer and needs no documentation |
| Cascading `is_active` to children | Would be business logic in the ORM, and the schema's cross-table rules (§41.3) already specify where such rules live |

**Filtering by `is_active` is the caller's explicit responsibility.** Reading current master data filters on it; reading historical operational data joined to master data does not. Those are different questions and the ORM does not guess which one is being asked.

**Three retirement pre-conditions from the frozen schema, recorded here because they are cross-table rules the ORM does not enforce:** a `maintenance_engineer` cannot be retired while `is_team_lead` is true; a `supplier` cannot be retired while an active `inventory_item` names it as primary source; an `alert_threshold_profile` version is retired rather than edited, never both. All three belong to the writing component (§41.3).

## 31. Table binding

Every model declares its table name and nothing else about placement. There is no schema to bind to.

| Models | `__tablename__` | Qualification |
|---|---|---|
| M1–M29 | The frozen table name, bare | None |
| O1–O22 | The frozen table name, bare | None |
| O23–O24 | The frozen table name, bare | None |

**No `schema=` argument appears anywhere in this layer.** SQLite has a single namespace per database file, so a model's table arguments carry unique constraints, check constraints, and indexes — and no placement key. A server-database design would need one per model; here the absence is uniform and total.

**This removes a class of environment-dependent failure.** In a server database, a model that omits its schema resolves against the connection's search path, which can differ between the application, a migration, and an interactive session — producing a model that works in one environment and silently addresses the wrong table in another. With one namespace there is no search path, no default, and no ambiguity about which table a name resolves to.

**Cross-group foreign keys need no special handling.** A foreign key from `production_run` to `product` names its target bare and resolves within the single `MetaData` (§12). All 81 operational→master keys, the 1 system→master key, and all 36 operational→operational keys work identically.

**Attached databases are not used.** SQLite's `ATTACH DATABASE` would introduce a second namespace and would require schema qualification to disambiguate. `FACTORY_SQLITE_DATABASE_SCHEMA.md` §44.3 records it as the archive option if the telemetry table ever outgrows one file, and notes it is not needed at v1. No model in this specification is bound to an attached database.

## 32. Metadata and naming convention

The single `MetaData` carries a naming convention so that **every constraint and index name is generated deterministically and matches the names `FACTORY_SQLITE_DATABASE_SCHEMA.md` assigns.**

| Constraint kind | Convention | Produces |
|---|---|---|
| Primary key | `pk_` + table | `pk_plant` |
| Unique constraint | `uq_` + table + columns | `uq_plant_code` |
| Foreign key | `fk_` + table + columns + referred table | `fk_plant_area_plant_id_plant` |
| Check constraint | `ck_` + table + constraint key | `ck_plant_code_format` |
| Index | `ix_` + table + columns | `ix_machine_sensor_reading_machine_id_recorded_at` |

**Why this is not cosmetic.** Without a convention, SQLAlchemy generates names for some constraint types and leaves others unnamed, and an unnamed constraint in SQLite produces a diagnostic that identifies only the table. `CHECK constraint failed: ck_machine_monitored_requires_profile` tells an operator which rule broke; `CHECK constraint failed: machine` does not. With the convention on the `MetaData`, names are a function of table and column names — stable, reviewable, and reproducible.

**One naming limitation is specific to SQLite and worth stating.** A primary key declared as `INTEGER PRIMARY KEY AUTOINCREMENT` must use the column-level form, and although SQLite's grammar permits a `CONSTRAINT` name on it, the engine reports a primary key violation as `UNIQUE constraint failed: <table>.<column>` without echoing the name. The `pk_<table>` convention is therefore retained for traceability between this document, the DDL, and the migration history — not because an operator will see it.

**Check constraints supply their own key.** The frozen schema names all ~285 of them. Each check constraint in a model's table arguments is declared with the exact name from the schema document, so a reviewer can match them one-to-one.

**The 8 unique indexes are declared explicitly**, seven with a `sqlite_where` predicate and one over `COALESCE` expressions. Alembic will not infer either. They are correctness-critical — the frozen schema's §42.4 says so — and each appears in its model's table arguments. §37.9 lists all eight.

## 33. Inheritance strategy

**No ORM inheritance is used. No single-table inheritance, no joined-table inheritance, no concrete inheritance, no polymorphic identity.**

The frozen schema contains no discriminator column and no table pair in a subtype relationship. Several designs look like candidates and are not:

| Apparent candidate | Why it is not inheritance |
|---|---|
| `machine_category` → `machine_type` → `machine` | Three levels of **classification**, each a separate entity with its own attributes and its own rows. A `machine` is not a subtype of a `machine_type`; it references one |
| `operational_event` / `operational_alert` | Correlation, not subtyping. An alert groups events and carries its own lifecycle columns |
| `maintenance_engineer` extending `worker` | A one-to-one extension table, mapped as two models with a one-to-one relationship. Joined-table inheritance would make `MaintenanceEngineer` a `Worker` subclass, which would break `Worker` queries by adding an implicit join and would make the 5 `maintenance_engineer` rows a subtype of the 13 `worker` rows in the type system. It is a role, not a subtype |
| `notification_recipient` extending `worker` | Same. Also, a worker may be a recipient *and* an engineer simultaneously, which single inheritance cannot express at all |

**What is shared is behaviour, not identity**, and mixins carry behaviour without implying an is-a relationship. §34.

---

## 34. Common conventions and the mixin set

Four mixins, each carrying exactly one concern, composed per model. No mixin knows about another.

| Mixin | Columns | Applies to |
|---|---|---|
| `SoftDeleteMixin` | `is_active` — `INTEGER NOT NULL DEFAULT 1`, with `CHECK (is_active IN (0,1))` | 28 master models (all except `Machine`) |
| `TimestampCreatedMixin` | `created_at` — `DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP` | All 53 models |
| `TimestampUpdatedMixin` | `updated_at` — `DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP`, ORM `onupdate` | 29 master + 8 operational = 37 models |
| `ComponentProvenanceMixin` | `created_by_component` — `TEXT NOT NULL`, `platform_component` vocabulary | All 24 operational models |

**Four compositions cover all 53 models with zero exceptions:**

| Composition | Mixins | Models | Count |
|---|---|---|---|
| Master standard | SoftDelete + Created + Updated | M1–M9, M11–M29 | 28 |
| Master machine | Created + Updated | M10 `Machine` | 1 |
| Operational append-only | Created + Provenance | O1, O3, O5, O7–O10, O12, O13, O15–O20, O23 | 16 |
| Operational mutable | Created + Updated + Provenance | O2, O4, O6, O11, O14, O21, O22, O24 | 8 |

**Why four narrow mixins beat one wide one.** A single `AuditMixin` carrying all four columns would need to be overridden on `Machine` (no `is_active`) and on 16 append-only operational models (no `updated_at`). Seventeen exceptions. With atomic mixins, `Machine`'s difference is *not composing* the soft-delete mixin — visible in one line of its class definition — and an append-only model's difference is *not composing* the updated mixin. **The variance becomes part of the declaration instead of an exception to it**, which means a reader can tell whether a model is append-only by looking at its class line.

`ComponentProvenanceMixin` is the only mixin that references an enum, and it references exactly one: `platform_component`. That is the sole reason mixins may import from the enums module (§16, R2).

**Declaration order within every model class is fixed** so that 53 files read identically:

1. Table arguments — constraints, indexes, schema
2. Primary key
3. Business key, where the table has one
4. Foreign key columns
5. Business columns, in the frozen schema's column order
6. Mixin columns — inherited, not restated
7. Relationships, owners before reverses
8. `@validates` hooks

§45 states this as a standard.

---
---

# Part IV — Master Models

---

## 35. Master model conventions

All 29 master models share the following, stated once here rather than 29 times.

| Property | Value |
|---|---|
| Logical group | `master` — a documentation and packaging grouping only; all 53 tables share one database file (§13) |
| Primary key | `<table>_id`, `MasterPk` alias — `Integer`, `INTEGER PRIMARY KEY AUTOINCREMENT`, never application-assigned |
| Mixins | `SoftDeleteMixin` + `TimestampCreatedMixin` + `TimestampUpdatedMixin`, except `Machine` which omits `SoftDeleteMixin` |
| Trailing columns | `is_active` (`bool`, server default `1`), `created_at`, `updated_at` (both `datetime`, server default `CURRENT_TIMESTAMP`) |
| Outbound group references | **None.** No master model holds a foreign key into an operational or system table |
| Write component | Administrative seed and edit only. **No agent writes any master table** |
| Delete behaviour | None. Retirement is `is_active = 0`, or `lifecycle_status` on `Machine` |
| Foreign key `ON DELETE` | `RESTRICT` on all 51 master→master keys |
| Business key | `<table>_code` with a unique constraint, on 27 of 29 |

**Subsection format.** Every model below carries the same 17 subsections in the same order. Three of the fields named in the required format are expressed as **table columns rather than separate subsections**, because they are per-attribute facts and a per-model prose paragraph would repeat the table: **Nullable Rules** and **Default Strategy** are the `Nullable` and `Default` columns of the Columns table, and **Relationship Cardinality** is the `Cardinality` column of the Relationships table. **Business Notes** and **Implementation Notes** are combined in **Notes**, which distinguishes them inline.

**Reading the Columns table.** Mixin-supplied columns appear as a final grouped row rather than being restated, since §34 defines them once. The `Default` column states the **server-side** default exactly as the frozen schema declares it; an em dash means the column has no default and the writer must supply a value. Boolean defaults appear as `1` and `0`, because that is what SQLite stores and what the DDL contains (§29).

**Reading the Logical Group subsection.** It names which of the three groups in §13 the model belongs to. It is not a `schema=` argument and never becomes one — SQLite has one table namespace per file, so the group determines only which package the class lives in (§44) and which part of this document describes it.

---

### M1. Plant

**Purpose**

The single manufacturing site the platform monitors. Root of the master hierarchy and the anchor for site-wide timezone and currency, on which every timestamp interpretation and every monetary value in the database depends.

**Python Class Name**

`Plant`

**Mapped Table**

`plant`

**Logical Group**

`master`

**Primary Key**

`plant_id` — `int`, `MasterPk`. Surrogate rather than `plant_code`, because every other master table carries `plant_id` and a site code revision must remain a one-row update.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `plant_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `plant_code` | VARCHAR(10) | `str` | `String(10)` | No | — | Business key, unique. Format `PLT-nn` |
| `plant_name` | VARCHAR(120) | `str` | `String(120)` | No | — | Trading name |
| `address_line` | VARCHAR(200) | `str` | `String(200)` | No | — | |
| `city` | VARCHAR(80) | `str` | `String(80)` | No | — | |
| `state_region` | VARCHAR(80) | `str` | `String(80)` | No | — | |
| `country_code` | CHAR(2) | `str` | `CHAR(2)` | No | — | ISO 3166-1 alpha-2 |
| `timezone` | VARCHAR(50) | `str` | `String(50)` | No | — | IANA name. Not check-constrained — ORM validation is mandatory |
| `currency_code` | CHAR(3) | `str` | `CHAR(3)` | No | — | ISO 4217. Applies to every monetary column in the database |
| `operating_days_per_week` | INTEGER | `int` | `Integer` | No | — | 1–7 |
| `shifts_per_day` | INTEGER | `int` | `Integer` | No | — | 1–4 |
| `commissioned_date` | DATE | `date` | `Date` | No | — | Floor for all asset installation dates |
| `annual_production_capacity_units` | INTEGER | `Optional[int]` | `Integer` | Yes | — | NULL when not formally rated |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` per §34 |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `plant_areas` | One-to-many | `PlantArea` | `select` | Bounded at 7 |
| `departments` | One-to-many | `Department` | `select` | Bounded at 5 |
| `shifts` | One-to-many | `Shift` | `select` | Bounded at 4 |

No many-to-one relationships. Sole root of the master dependency graph.

**Back Populates**

| This side | Target side |
|---|---|
| `Plant.plant_areas` | `PlantArea.plant` |
| `Plant.departments` | `Department.plant` |
| `Plant.shifts` | `Shift.plant` |

**Python Types**

`int`, `str`, `date`, `Optional[int]`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `CHAR`, `Date`, `Boolean`, `DateTime`.

**Enum Usage**

None. This is one of six master models with no enum column.

**Validation**

- `timezone` — **mandatory ORM validation.** The frozen schema states this cannot be a check constraint because SQLite carries no timezone catalogue to validate against and a `CHECK` expression must be deterministic. An invalid IANA name silently corrupts every shift-window calculation in the platform. `@validates` verifies the value resolves as a real zone and rejects it otherwise.
- `country_code`, `currency_code` — normalise to upper case before the database check constraint sees them, so a lower-case input is corrected rather than rejected.
- `plant_code` — normalise to upper case and strip whitespace.
- `commissioned_date` not in the future — **not** ORM-validated. Belongs to the writing component per §41.3, because a `CHECK` expression must be deterministic and `CURRENT_DATE` is not, and because the rule applies uniformly across the schema.

**Loading Strategy**

All three collections `select` and bounded. A caller reading the plant with its structure specifies `selectinload` per query. Effectively always resident in the identity map since there is one row.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative seed and edit only. No agent writes this model.

**Growth**

1 row. Fixed. Additional plants are additional rows requiring no schema change.

**Notes**

*Business* — `currency_code` being site-level is why no monetary column anywhere carries its own currency; a single-site plant transacts in one currency and per-row codes would be dead weight on 14 monetary columns.

*Implementation* — the `timezone` validator is the single highest-value validation hook in the entire ORM layer. It guards one column on a one-row table, and a wrong value there is wrong everywhere.

---

### M2. PlantArea

**Purpose**

A distinct physical zone within the plant. Answers *where is this* for production lines and storage, and supplies the ambient thermal context that helps distinguish a hot machine from a hot area.

**Python Class Name**

`PlantArea`

**Mapped Table**

`plant_area`

**Logical Group**

`master`

**Primary Key**

`plant_area_id` — `int`, `MasterPk`. A composite natural key was rejected because it would propagate a two-column foreign key into `production_line` and `inventory_location` and onward.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `plant_area_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `plant_area_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique. Format `AREA-XXX` |
| `plant_id` | INTEGER | `int` | `Integer` | No | — | FK → `plant`, `RESTRICT` |
| `area_name` | VARCHAR(100) | `str` | `String(100)` | No | — | |
| `area_type` | TEXT | `AreaType` | `Enum(AreaType)` + `ck_*_area_type_allowed` | No | — | 8 values |
| `floor_level` | INTEGER | `Optional[int]` | `Integer` | Yes | — | 0 is ground. NULL when meaningless |
| `floor_space_sqm` | NUMERIC(10,2) | `Optional[Decimal]` | `Rate` → `Numeric(10,2)` | Yes | — | NULL when not surveyed |
| `nominal_ambient_temp_c` | NUMERIC(5,2) | `Optional[Decimal]` | `Percent` → `Numeric(5,2)` | Yes | — | Thermal baseline |
| `is_climate_controlled` | INTEGER | `bool` | `Boolean` | No | `0` | Weakens ambient as an explanation for a thermal excursion |
| `access_restriction` | TEXT | `AccessRestriction` | `Enum(AccessRestriction)` + `ck_*_access_restriction_allowed` | No | `'general'` | 3 values. Affects dispatch feasibility |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `plant` | Many-to-one | `Plant` | `select` | Mandatory |
| `production_lines` | One-to-many | `ProductionLine` | `select` | Bounded at 4 |
| `inventory_locations` | One-to-many | `InventoryLocation` | `select` | Bounded at 5 |
| `based_maintenance_teams` | One-to-many, optional | `MaintenanceTeam` | `select` | Reverse of `base_plant_area_id` |

**Back Populates**

| This side | Target side |
|---|---|
| `PlantArea.plant` | `Plant.plant_areas` |
| `PlantArea.production_lines` | `ProductionLine.plant_area` |
| `PlantArea.inventory_locations` | `InventoryLocation.plant_area` |
| `PlantArea.based_maintenance_teams` | `MaintenanceTeam.base_plant_area` |

**Python Types**

`int`, `str`, `bool`, `Optional[int]`, `Optional[Decimal]`, `AreaType`, `AccessRestriction`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Numeric`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `area_type` | `AreaType` | `area_type` | No |
| `access_restriction` | `AccessRestriction` | `access_restriction` | No |

**Validation**

- `plant_area_code` — upper-case and strip.
- Range checks on `floor_level`, `floor_space_sqm`, `nominal_ambient_temp_c` — **database only.** Four named check constraints already enforce them; duplicating them in the ORM creates two authorities that drift (§41.2).

**Loading Strategy**

`plant` is a lazy many-to-one, near-always in the identity map. All three collections bounded and `select`.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

7 rows. Effectively fixed; grows only if the plant is physically extended.

**Notes**

*Business* — areas are physical, departments are organisational, and the separation is deliberate: a machining bay physically contains lines owned by Production while Maintenance and Quality staff also work inside it.

*Implementation* — `based_maintenance_teams` is named for the role of the foreign key (`base_plant_area_id`) rather than as a bare `maintenance_teams`, because a plain name would suggest teams *belong* to an area when the relationship is where they are stationed.

---

### M3. Department

**Purpose**

The organisational unit that owns cost and headcount. Supplies the escalation address for notifications and the cost centre for maintenance charge-out.

**Python Class Name**

`Department`

**Mapped Table**

`department`

**Logical Group**

`master`

**Primary Key**

`department_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `department_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `department_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique |
| `plant_id` | INTEGER | `int` | `Integer` | No | — | FK → `plant`, `RESTRICT` |
| `department_name` | VARCHAR(100) | `str` | `String(100)` | No | — | |
| `department_function` | TEXT | `DepartmentFunction` | `Enum(DepartmentFunction)` + `ck_*_department_function_allowed` | No | — | |
| `cost_center_code` | VARCHAR(20) | `str` | `String(20)` | No | — | |
| `escalation_email` | VARCHAR(150) | `Optional[str]` | `String(150)` | Yes | — | Departmental escalation address |
| `headcount_budget` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `plant` | Many-to-one | `Plant` | `select` | Mandatory |
| `production_lines` | One-to-many | `ProductionLine` | `select` | Bounded at 4 |
| `workers` | One-to-many | `Worker` | `select` | Bounded by departmental headcount |
| `maintenance_teams` | One-to-many | `MaintenanceTeam` | `select` | Bounded at 4 |

**Back Populates**

| This side | Target side |
|---|---|
| `Department.plant` | `Plant.departments` |
| `Department.production_lines` | `ProductionLine.department` |
| `Department.workers` | `Worker.department` |
| `Department.maintenance_teams` | `MaintenanceTeam.department` |

**Python Types**

`int`, `str`, `Optional[str]`, `Optional[int]`, `DepartmentFunction`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Enum`, `Boolean`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `department_function` | `DepartmentFunction` | `department_function` | No |

**Validation**

- `escalation_email` — normalise to lower case and strip. Format is already check-constrained; the ORM normalises so a mixed-case input is accepted rather than rejected.
- `department_code`, `cost_center_code` — upper-case and strip.

**Loading Strategy**

All collections bounded and `select`. `workers` is the largest at roughly 20–30 rows and remains within the L2 bound (§19.2).

**Read Components**

Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

5 rows. Fixed.

**Notes**

*Business* — `escalation_email` is nullable because not every department maintains a shared address; recipient-level addressing on `worker` is the primary path and this is the fallback.

*Implementation* — `workers` is mapped despite being the largest master collection in the schema because it is bounded by headcount, which is an organisational fact rather than a data-volume one. Contrast `Shift.workers` in M4, which is mapped for the same reason, and `Shift`'s 17 operational collections, which are not mapped at all.

---

### M4. Shift

**Purpose**

The recurring work period that every operational record is stamped with. The unit of comparison for all shift-over-shift performance analysis in the platform.

**Python Class Name**

`Shift`

**Mapped Table**

`shift`

**Logical Group**

`master`

**Primary Key**

`shift_id` — `int`, `MasterPk`. Referenced by 17 operational tables, which is why the key is narrow.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `shift_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `shift_code` | VARCHAR(8) | `str` | `String(8)` | No | — | Business key, unique |
| `plant_id` | INTEGER | `int` | `Integer` | No | — | FK → `plant`, `RESTRICT` |
| `shift_name` | VARCHAR(60) | `str` | `String(60)` | No | — | |
| `start_time` | TIME | `time` | `Time` | No | — | Clock time, no date, no zone |
| `end_time` | TIME | `time` | `Time` | No | — | May be earlier than `start_time` |
| `crosses_midnight` | INTEGER | `bool` | `Boolean` | No | `0` | Explicit because `end_time < start_time` is legitimate |
| `shift_type` | TEXT | `ShiftType` | `Enum(ShiftType)` + `ck_*_shift_type_allowed` | No | — | |
| `sequence_order` | INTEGER | `int` | `Integer` | No | — | Ordering within the day |
| `is_production_shift` | INTEGER | `bool` | `Boolean` | No | `1` | |
| `break_duration_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `plant` | Many-to-one | `Plant` | `select` | Mandatory |
| `workers` | One-to-many | `Worker` | `select` | Bounded by roster |
| `maintenance_teams` | One-to-many | `MaintenanceTeam` | `select` | Bounded at 4 |

**Deliberately unmapped — 17 operational reverse collections.** `machine_sensor_reading`, `machine_operational_status`, `machine_state_transition`, `production_progress`, `production_count`, `cycle_history`, `quality_inspection_result`, `scrap_record`, `inventory_movement`, `maintenance_work_record`, `machine_maintenance_activity`, `operational_event`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `ai_recommendation`, `recommendation_action`, `notification` all carry a `shift_id`. **None is mapped as a collection on `Shift`.** Rule L1 (§19.1) — `Shift` has 4 rows and `Shift.sensor_readings` would be an eight-million-row attribute on one of them. Queries by shift filter the child model.

**Back Populates**

| This side | Target side |
|---|---|
| `Shift.plant` | `Plant.shifts` |
| `Shift.workers` | `Worker.shift` |
| `Shift.maintenance_teams` | `MaintenanceTeam.shift` |

Every operational model's `shift` relationship is **unidirectional** and declares no `back_populates`.

**Python Types**

`int`, `str`, `bool`, `time`, `Optional[int]`, `ShiftType`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Time`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `shift_type` | `ShiftType` | `shift_type` | No |

**Validation**

- `crosses_midnight` consistency with `start_time` and `end_time` — **writing component**, not ORM. It is a two-column relationship the database expresses as a check constraint, and re-deriving it in Python would create a second authority.
- `shift_code` — upper-case and strip.
- **Partial unique index** `uq_shift_sequence_order_production` on (`plant_id`, `sequence_order`) `WHERE shift_type = 'production'`. Declared in table arguments with its `sqlite_where` predicate. The general shift is excluded from rotation ordering, so uniqueness must apply to production shifts only, and a plain unique constraint could not express that (§37.9).
- No ORM arithmetic on `start_time`/`end_time`. Shift-window computation needs `plant.timezone` and a calendar date, and belongs to the component performing it.

**Loading Strategy**

Three bounded collections, `select`. All 4 rows are effectively permanently resident in any session's identity map, so the many-to-one `shift` on operational models almost never emits SQL — which is why `raise_on_sql` on the high-volume models (§19.4) is tolerable rather than obstructive.

**Read Components**

All eight components.

**Write Components**

Administrative only.

**Growth**

4 rows. Fixed.

**Notes**

*Business* — `crosses_midnight` is a stored fact rather than a derived one because a night shift's `end_time` is legitimately earlier than its `start_time`, and every consumer would otherwise re-derive the same comparison.

*Implementation* — `Shift` is the clearest demonstration of Rule L1 in the schema. It is the smallest table with the widest inbound reference count, which is exactly the shape that makes reverse collections catastrophic and forward references cheap.

---

### M5. ProductionLine

**Purpose**

The physical sequence of machines that produces a product. The unit at which capacity, OEE targets, and line-stop authority are defined, and the aggregation boundary for most dashboard views.

**Python Class Name**

`ProductionLine`

**Mapped Table**

`production_line`

**Logical Group**

`master`

**Primary Key**

`production_line_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `production_line_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `production_line_code` | VARCHAR(10) | `str` | `String(10)` | No | — | Business key, unique |
| `plant_area_id` | INTEGER | `int` | `Integer` | No | — | FK → `plant_area`, `RESTRICT`. Physical location |
| `department_id` | INTEGER | `int` | `Integer` | No | — | FK → `department`, `RESTRICT`. Organisational owner |
| `line_name` | VARCHAR(120) | `str` | `String(120)` | No | — | |
| `line_type` | TEXT | `LineType` | `Enum(LineType)` + `ck_*_line_type_allowed` | No | — | |
| `criticality` | TEXT | `CriticalityLevel` | `Enum(CriticalityLevel)` + `ck_*_criticality_level_allowed` | No | — | **Shared type**, also on `Machine` |
| `design_capacity_units_per_hour` | NUMERIC(10,2) | `Decimal` | `Rate` → `Numeric(10,2)` | No | — | |
| `station_count` | INTEGER | `int` | `Integer` | No | — | |
| `target_oee_percent` | NUMERIC(5,2) | `Optional[Decimal]` | `Percent` → `Numeric(5,2)` | Yes | — | |
| `changeover_time_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `commissioned_date` | DATE | `date` | `Date` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `plant_area` | Many-to-one | `PlantArea` | `select` | Mandatory |
| `department` | Many-to-one | `Department` | `select` | Mandatory |
| `machines` | One-to-many | `Machine` | `select` | Bounded at 8 |
| `line_capabilities` | One-to-many | `ProductLineCapability` | `select` | Bounded at 7 |
| `workers` | One-to-many, optional | `Worker` | `select` | Reverse of nullable `worker.production_line_id` |
| `business_rules` | One-to-many, optional | `BusinessRule` | `select` | Line-scoped rules only |
| `notification_recipients` | One-to-many, optional | `NotificationRecipient` | `select` | Reverse of `scope_production_line_id` |

**Deliberately unmapped — 6 operational reverse collections.** `production_run`, `operational_event`, `operational_alert`, `supervisor_context`, `ai_recommendation`, `dashboard_snapshot`. All grow without bound relative to 4 line rows.

**Back Populates**

| This side | Target side |
|---|---|
| `ProductionLine.plant_area` | `PlantArea.production_lines` |
| `ProductionLine.department` | `Department.production_lines` |
| `ProductionLine.machines` | `Machine.production_line` |
| `ProductionLine.line_capabilities` | `ProductLineCapability.production_line` |
| `ProductionLine.workers` | `Worker.production_line` |
| `ProductionLine.business_rules` | `BusinessRule.production_line` |
| `ProductionLine.notification_recipients` | `NotificationRecipient.scope_production_line` |

**Python Types**

`int`, `str`, `date`, `Decimal`, `Optional[Decimal]`, `Optional[int]`, `LineType`, `CriticalityLevel`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Numeric`, `Date`, `Enum`, `Boolean`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `line_type` | `LineType` | `line_type` | No |
| `criticality` | `CriticalityLevel` | `criticality_level` | No |

`CriticalityLevel` is one of five shared enum types (§40.2). It is shared with `Machine.criticality` so that prioritisation can compare line and machine criticality as a direct value comparison.

**Validation**

- `production_line_code` — upper-case and strip.
- `commissioned_date` not before `plant.commissioned_date` — **writing component.** It is a cross-table rule and §41.3 places it there.

**Loading Strategy**

Two many-to-one, five bounded collections, all `select`. Dashboard line views specify `selectinload` on `machines` per query.

**Read Components**

All eight components.

**Write Components**

Administrative only.

**Growth**

4 rows. Grows only when a physical line is added.

**Notes**

*Business* — two mandatory parents, physical and organisational, is the point of separating `plant_area` from `department`. A line has a location and an owner and they are different facts.

*Implementation* — three of the seven collections are reverses of **nullable** foreign keys, so they are legitimately empty for most rows. `business_rules` in particular holds only line-scoped rules; plant-wide rules have `production_line_id IS NULL` and never appear here. A caller reading applicable rules for a line must union the line-scoped set with the global set, which is why that logic is the Supervisor Agent's rather than a relationship.

---

### M6. Product

**Purpose**

What the factory makes. Carries the selling price and material cost that convert a production delay or a scrap event into a currency figure, which is what makes the Decision Agent's business impact statement possible.

**Python Class Name**

`Product`

**Mapped Table**

`product`

**Logical Group**

`master`

**Primary Key**

`product_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `product_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `product_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `product_name` | VARCHAR(150) | `str` | `String(150)` | No | — | |
| `product_family` | VARCHAR(80) | `str` | `String(80)` | No | — | Grouping for reporting |
| `unit_of_measure` | TEXT | `UnitOfMeasure` | `Enum(UnitOfMeasure)` + `ck_*_unit_of_measure_allowed` | No | — | **Shared type, 6 values. Narrowed to 5 for products by check constraint** |
| `standard_selling_price` | NUMERIC(12,2) | `Decimal` | `Money` → `Numeric(12,2)` | No | — | Plant currency |
| `standard_material_cost` | NUMERIC(12,2) | `Decimal` | `Money` → `Numeric(12,2)` | No | — | |
| `quality_criticality` | TEXT | `QualityCriticality` | `Enum(QualityCriticality)` + `ck_*_quality_criticality_allowed` | No | — | |
| `target_scrap_rate_pct` | NUMERIC(5,2) | `Optional[Decimal]` | `Percent` → `Numeric(5,2)` | Yes | — | |
| `shelf_life_days` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `drawing_revision` | VARCHAR(12) | `Optional[str]` | `String(12)` | Yes | — | |
| `introduced_date` | DATE | `date` | `Date` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `line_capabilities` | One-to-many | `ProductLineCapability` | `select` | Bounded at 7 |
| `bom_lines` | One-to-many | `BillOfMaterials` | `select` | Bounded at 10 |

**Deliberately unmapped.** `production_run` — ~2,000 rows a year against 3 product rows.

**Back Populates**

| This side | Target side |
|---|---|
| `Product.line_capabilities` | `ProductLineCapability.product` |
| `Product.bom_lines` | `BillOfMaterials.product` |

**Python Types**

`int`, `str`, `date`, `Decimal`, `Optional[str]`, `Optional[int]`, `Optional[Decimal]`, `UnitOfMeasure`, `QualityCriticality`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Numeric`, `Date`, `Enum`, `Boolean`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `unit_of_measure` | `UnitOfMeasure` | `unit_of_measure` | No |
| `quality_criticality` | `QualityCriticality` | `quality_criticality` | No |

**`UnitOfMeasure` is the one enum where the Python class is deliberately wider than the column permits.** The type carries 6 values; `ck_product_unit_of_measure_allowed` restricts `product` to 5, excluding `BOX`. The ORM does **not** define a second narrower enum class — that would duplicate five of six members and require a cast in `bill_of_materials` arithmetic, which crosses product and item units. §40.3 records the consequence: assigning `BOX` to a product is caught by the database, not by the type system, and that is the accepted trade.

**Validation**

- `product_code`, `drawing_revision` — upper-case and strip.
- `unit_of_measure != BOX` — **database only**, via the named check constraint. Deliberately not mirrored in the ORM; see §40.3.
- `standard_selling_price >= standard_material_cost` — **not validated anywhere.** The frozen schema does not constrain it, because a loss-leader is a legitimate business state. Recorded here so its absence is understood as intentional.

**Loading Strategy**

Two bounded collections, `select`.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

3 rows. Grows as the portfolio grows; no schema change.

**Notes**

*Business* — `standard_selling_price` and `standard_material_cost` are the only reason the platform can say what an hour of downtime costs. Without them, every recommendation's business impact would be a qualitative adjective.

*Implementation* — `bom_lines` is the reverse of `bill_of_materials.product_id` only. `BillOfMaterials` also holds `substitute_inventory_item_id`, but that points at `InventoryItem`, not here, so `Product` has exactly one BOM collection.

---

### M7. ProductLineCapability

**Purpose**

Which lines can make which products, and at what cycle time. The association object resolving product ↔ production_line as a many-to-many, and the source of the standard against which every cycle's deviation is measured.

**Python Class Name**

`ProductLineCapability`

**Mapped Table**

`product_line_capability`

**Logical Group**

`master`

**Primary Key**

`product_line_capability_id` — `int`, `MasterPk`. A surrogate rather than the composite pair, because `production_run` references it as a single column.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `product_line_capability_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `product_id` | INTEGER | `int` | `Integer` | No | — | FK → `product`, `RESTRICT` |
| `production_line_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_line`, `RESTRICT` |
| `capability_type` | TEXT | `CapabilityType` | `Enum(CapabilityType)` + `ck_*_capability_type_allowed` | No | — | |
| `is_primary_line` | INTEGER | `bool` | `Boolean` | No | `0` | **Partial unique index** — one primary line per product |
| `cycle_time_seconds` | NUMERIC(8,2) | `Decimal` | `Seconds2` → `Numeric(8,2)` | No | — | The standard `cycle_history` measures deviation against |
| `max_hourly_output_units` | NUMERIC(10,2) | `Decimal` | `Rate` → `Numeric(10,2)` | No | — | |
| `changeover_minutes` | INTEGER | `int` | `Integer` | No | — | |
| `is_qualified` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `qualification_expiry_date` | DATE | `Optional[date]` | `Date` | Yes | — | |
| `tooling_available` | INTEGER | `bool` | `Boolean` | No | `1` | |
| `effective_from_date` | DATE | `date` | `Date` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `product` | Many-to-one | `Product` | `select` | Mandatory |
| `production_line` | Many-to-one | `ProductionLine` | `select` | Mandatory |

**Deliberately unmapped.** `production_run` — unbounded relative to 7 capability rows.

**Back Populates**

| This side | Target side |
|---|---|
| `ProductLineCapability.product` | `Product.line_capabilities` |
| `ProductLineCapability.production_line` | `ProductionLine.line_capabilities` |

**Python Types**

`int`, `bool`, `date`, `Decimal`, `Optional[date]`, `CapabilityType`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `Numeric`, `Date`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `capability_type` | `CapabilityType` | `capability_type` | No |

**Validation**

- Immutability of `cycle_time_seconds` — **enforced by convention and retention policy, not by the ORM.** The frozen schema states superseded capabilities are soft-retired via `is_active` and never edited, which is what lets `cycle_history` compute deviation against a pinned capability without copying the standard. An `@validates` immutability hook is **not** applied, because administrative correction of a data-entry error is legitimate. The discipline is procedural and recorded here.
- One primary line per product — **partial unique index**, `WHERE is_primary_line`. Declared in table arguments; not re-checked in the ORM.

**Loading Strategy**

Two many-to-one, `select`. This is an **association object**, so callers traverse `Product.line_capabilities` then `.production_line` — two hops. `secondary=` is not used here or anywhere (§37.5), because this table carries ten attributes beyond its two keys.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

7 rows. Grows as products × capable lines.

**Notes**

*Business* — this is one of five genuine many-to-many relationships in the master model, and like all five it carries attributes. A product on a line is not just permitted; it has a cycle time, a qualification, and an expiry.

*Implementation* — the partial unique index on `is_primary_line` is one of eight in the schema and is correctness-critical (§37.9). It cannot be expressed as a unique constraint and Alembic will not infer the predicate, so it is declared explicitly with its `sqlite_where` clause.

---

### M8. MachineCategory

**Purpose**

The broadest equipment classification. Determines which maintenance discipline owns a class of machine and whether condition monitoring applies at all.

**Python Class Name**

`MachineCategory`

**Mapped Table**

`machine_category`

**Logical Group**

`master`

**Primary Key**

`machine_category_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_category_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `machine_category_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique |
| `category_name` | VARCHAR(80) | `str` | `String(80)` | No | — | |
| `description` | TEXT | `Optional[str]` | `Text` | Yes | — | Unbounded free text |
| `equipment_class` | TEXT | `EquipmentClass` | `Enum(EquipmentClass)` + `ck_*_equipment_class_allowed` | No | — | |
| `primary_maintenance_specialization` | TEXT | `MaintenanceSpecialization` | `Enum(MaintenanceSpecialization)` + `ck_*_maintenance_specialization_allowed` | No | — | **Shared type, 4 values, 5 columns across 4 tables** |
| `is_rotating_equipment` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `requires_condition_monitoring` | INTEGER | `bool` | `Boolean` | No | `1` | |
| `typical_service_life_years` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine_types` | One-to-many | `MachineType` | `select` | Bounded at 6 |

**Back Populates**

| This side | Target side |
|---|---|
| `MachineCategory.machine_types` | `MachineType.machine_category` |

**Python Types**

`int`, `str`, `bool`, `Optional[str]`, `Optional[int]`, `EquipmentClass`, `MaintenanceSpecialization`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `equipment_class` | `EquipmentClass` | `equipment_class` | No |
| `primary_maintenance_specialization` | `MaintenanceSpecialization` | `maintenance_specialization` | No |

`MaintenanceSpecialization` is the most consequential shared enum in the master group: 5 columns across 4 tables, and the platform's team-matching logic is a direct value comparison between them. **One Python class holds the vocabulary once; the database holds it as five identical check constraints**, all transcribed from the same catalogue entry, so the Python class is what keeps them from diverging (§40.2).

**Validation**

- `machine_category_code` — upper-case and strip.
- No consistency rule between `is_rotating_equipment` and `requires_condition_monitoring`. They are independent facts and the schema does not relate them.

**Loading Strategy**

One bounded collection, `select`.

**Read Components**

Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

5 rows. Fixed; new categories only for genuinely new equipment families.

**Notes**

*Business* — three levels of equipment classification (category → type → machine) exist because failure modes attach at type level, thresholds attach at type level, and maintenance discipline attaches at category level. Collapsing them would force one of those three to be restated per machine.

*Implementation* — `description` is `TEXT`, not `VARCHAR`. The frozen schema uses `TEXT` only for genuinely unbounded free text (§39.5) and the distinction is preserved exactly: `Text` maps it, never `String` with an arbitrary length.

---

### M9. MachineType

**Purpose**

The equipment model. Carries the manufacturer's reliability figures — MTBF, MTTR, design life — that give the Prediction Agent a prior before any telemetry exists, and anchors the parameter set, failure modes, and threshold profiles that apply to every machine of this model.

**Python Class Name**

`MachineType`

**Mapped Table**

`machine_type`

**Logical Group**

`master`

**Primary Key**

`machine_type_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_type_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `machine_type_code` | VARCHAR(24) | `str` | `String(24)` | No | — | Business key, unique |
| `machine_category_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_category`, `RESTRICT` |
| `type_name` | VARCHAR(120) | `str` | `String(120)` | No | — | |
| `manufacturer` | VARCHAR(100) | `str` | `String(100)` | No | — | |
| `model_number` | VARCHAR(60) | `str` | `String(60)` | No | — | |
| `rated_power_kw` | NUMERIC(8,2) | `Decimal` | `Seconds2` → `Numeric(8,2)` | No | — | Same precision alias, different semantics — see Notes |
| `design_life_hours` | INTEGER | `int` | `Integer` | No | — | |
| `mtbf_hours` | INTEGER | `int` | `Integer` | No | — | Mean time between failures |
| `mttr_minutes` | INTEGER | `int` | `Integer` | No | — | Mean time to repair |
| `requires_tooling` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `control_system` | VARCHAR(80) | `Optional[str]` | `String(80)` | Yes | — | |
| `min_operators_required` | INTEGER | `int` | `Integer` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine_category` | Many-to-one | `MachineCategory` | `select` | Mandatory |
| `machines` | One-to-many | `Machine` | `select` | Bounded at 8 |
| `type_parameters` | One-to-many | `MachineTypeParameter` | `select` | ~5 per type |
| `failure_modes` | One-to-many | `MachineTypeFailureMode` | `select` | ~4 per type |
| `alert_threshold_profiles` | One-to-many | `AlertThresholdProfile` | `select` | Bounded at 6 |

**Back Populates**

| This side | Target side |
|---|---|
| `MachineType.machine_category` | `MachineCategory.machine_types` |
| `MachineType.machines` | `Machine.machine_type` |
| `MachineType.type_parameters` | `MachineTypeParameter.machine_type` |
| `MachineType.failure_modes` | `MachineTypeFailureMode.machine_type` |
| `MachineType.alert_threshold_profiles` | `AlertThresholdProfile.machine_type` |

**Python Types**

`int`, `str`, `bool`, `Decimal`, `Optional[str]`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Numeric`, `Boolean`, `DateTime`.

**Enum Usage**

None. One of six master models with no enum column.

**Validation**

- `machine_type_code`, `model_number` — upper-case and strip.
- `mtbf_hours`, `mttr_minutes`, `design_life_hours` positivity — **database only**, already check-constrained.

**Loading Strategy**

One many-to-one and four bounded collections, all `select`. The Prediction Agent's typical read is a machine with its type, parameters, and failure modes — a three-level traversal it specifies with `selectinload` chains per query rather than relying on defaults.

**Read Components**

Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

6 rows. Grows when new equipment models are introduced.

**Notes**

*Business* — MTBF and MTTR being type-level rather than machine-level is what lets the Prediction Agent produce a calibrated prior for a machine with no failure history: the model's reliability figures apply until the machine's own history is long enough to matter.

*Implementation* — `rated_power_kw` resolves through the `Seconds2` alias because both are `NUMERIC(8,2)`. Sharing an alias across unrelated semantics is a readability cost, and §38.4 states the position: aliases are named for their **precision role**, and a caller reading `rated_power_kw` learns its meaning from the attribute name, not the alias. Defining a `PowerKw` alias with identical DDL would be a second name for one type.

---

### M10. Machine

**Purpose**

The physical asset. The centre of gravity of the entire platform: every sensor reading, state transition, prediction, and recommendation is about a machine. Referenced by 13 operational tables.

**Python Class Name**

`Machine`

**Mapped Table**

`machine`

**Logical Group**

`master`

**Primary Key**

`machine_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `machine_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique |
| `machine_type_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_type`, `RESTRICT` |
| `production_line_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_line`, `RESTRICT` |
| `line_position` | INTEGER | `int` | `Integer` | No | — | Sequence on the line. **Partial unique index** with `production_line_id` |
| `alert_threshold_profile_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `alert_threshold_profile`, `RESTRICT`. NULL falls back to the type default |
| `machine_name` | VARCHAR(120) | `str` | `String(120)` | No | — | |
| `serial_number` | VARCHAR(60) | `str` | `String(60)` | No | — | Unique |
| `asset_tag` | VARCHAR(30) | `Optional[str]` | `String(30)` | Yes | — | |
| `installation_date` | DATE | `date` | `Date` | No | — | |
| `commissioned_date` | DATE | `date` | `Date` | No | — | |
| `warranty_expiry_date` | DATE | `Optional[date]` | `Date` | Yes | — | |
| `criticality` | TEXT | `CriticalityLevel` | `Enum(CriticalityLevel)` + `ck_*_criticality_level_allowed` | No | — | **Shared type** with `ProductionLine.criticality` |
| `is_bottleneck` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `downstream_buffer_units` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `rated_capacity_units_per_hour` | NUMERIC(10,2) | `Optional[Decimal]` | `Rate` → `Numeric(10,2)` | Yes | — | |
| `lifecycle_status` | TEXT | `MachineLifecycleStatus` | `Enum(MachineLifecycleStatus)` + `ck_*_machine_lifecycle_status_allowed` | No | `'in_service'` | **Replaces `is_active`.** The one documented mixin exception |
| `is_monitored` | INTEGER | `bool` | `Boolean` | No | `1` | |
| `installed_position_notes` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `created_at`, `updated_at` only. **No `is_active`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine_type` | Many-to-one | `MachineType` | `select` | Mandatory |
| `production_line` | Many-to-one | `ProductionLine` | `select` | Mandatory |
| `alert_threshold_profile` | Many-to-one, optional | `AlertThresholdProfile` | `select` | NULL means fall back to the type default |
| `maintenance_schedules` | One-to-many | `MachineMaintenanceSchedule` | `select` | Bounded at 8 |
| `operational_status` | **One-to-one**, optional | `MachineOperationalStatus` | `select`, `uselist=False` | Exactly one row per monitored machine |

**Deliberately unmapped — 12 operational reverse collections.** `machine_sensor_reading` (~4 million rows per machine per year), `machine_state_transition`, `production_count`, `cycle_history`, `quality_inspection_result` (×2 roles), `scrap_record` (×2 roles), `maintenance_work_record`, `operational_event`, `operational_alert`, `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `ai_recommendation`, `dashboard_snapshot`. Rule L1 (§19.1).

**Back Populates**

| This side | Target side |
|---|---|
| `Machine.machine_type` | `MachineType.machines` |
| `Machine.production_line` | `ProductionLine.machines` |
| `Machine.alert_threshold_profile` | `AlertThresholdProfile.machines` |
| `Machine.maintenance_schedules` | `MachineMaintenanceSchedule.machine` |
| `Machine.operational_status` | `MachineOperationalStatus.machine` |

**Python Types**

`int`, `str`, `bool`, `date`, `Optional[int]`, `Optional[str]`, `Optional[date]`, `Optional[Decimal]`, `CriticalityLevel`, `MachineLifecycleStatus`; from mixins `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Numeric`, `Date`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `criticality` | `CriticalityLevel` | `criticality_level` | No |
| `lifecycle_status` | `MachineLifecycleStatus` | `machine_lifecycle_status` | No, default `'in_service'` |

**Validation**

- `machine_code`, `serial_number`, `asset_tag` — upper-case and strip.
- `commissioned_date >= installation_date` — **database**, already check-constrained.
- Retirement is `lifecycle_status = 'decommissioned'`, never a delete. **Not ORM-enforced**; the 13 inbound `RESTRICT` foreign keys make a delete fail regardless, which is the stronger guarantee.
- No validator asserts `is_monitored` implies an `operational_status` row exists. That is a cross-table invariant the Simulator maintains (§41.3).

**Loading Strategy**

Three many-to-one and two collections, all `select`. `operational_status` is a scalar one-to-one — a single indexed lookup, and the most frequently traversed relationship in the platform. Dashboard machine grids specify `selectinload` on it.

**Read Components**

All eight components.

**Write Components**

Administrative only. Its operational state lives in `machine_operational_status`, written by the Simulator.

**Growth**

8 rows. Grows as equipment is added; the highest-value growth axis in the model and requiring no schema change.

**Notes**

*Business* — `lifecycle_status` replaces `is_active` because a boolean cannot distinguish standby from under-overhaul from decommissioned, and the Monitoring Agent needs that distinction. Carrying both would create two overlapping sources for one fact.

*Implementation* — this model is where three of this specification's structural decisions become visible at once. It is the **only** master model that omits `SoftDeleteMixin`, which is why §34 uses four atomic mixins. It has the **most unmapped reverse collections** of any model, which is Rule L1's justification in a single class. And its `alert_threshold_profile` is a **nullable** many-to-one whose NULL is meaningful — fall back to the machine type's default profile — so a caller must handle `None` as a branch, not as missing data.

---
### M11. MachineParameter

**Purpose**

The catalogue of measurable quantities — temperature, vibration, pressure, current, cycle time. Defines what a reading *means*, including its physical bounds and which direction indicates degradation.

**Python Class Name**

`MachineParameter`

**Mapped Table**

`machine_parameter`

**Logical Group**

`master`

**Primary Key**

`machine_parameter_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_parameter_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `machine_parameter_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique. Effectively immutable once telemetry references it |
| `parameter_name` | VARCHAR(80) | `str` | `String(80)` | No | — | |
| `unit_of_measure` | VARCHAR(16) | `str` | `String(16)` | No | — | **Deliberately not a vocabulary** — units are an open set (§40.6) |
| `measurement_domain` | TEXT | `MeasurementDomain` | `Enum(MeasurementDomain)` + `ck_*_measurement_domain_allowed` | No | — | |
| `data_type` | TEXT | `ParameterDataType` | `Enum(ParameterDataType)` + `ck_*_parameter_data_type_allowed` | No | — | |
| `physical_min` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | Sensor floor, not a normal-range bound |
| `physical_max` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | |
| `degradation_direction` | TEXT | `DegradationDirection` | `Enum(DegradationDirection)` + `ck_*_degradation_direction_allowed` | No | — | Which way is worse |
| `is_cumulative` | INTEGER | `bool` | `Boolean` | No | `0` | A counter rather than an instantaneous value |
| `description` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `type_parameters` | One-to-many | `MachineTypeParameter` | `select` | ~28 total |
| `threshold_rules` | One-to-many | `AlertThresholdRule` | `select` | ~26 total |
| `primary_for_failure_modes` | One-to-many, optional | `MachineTypeFailureMode` | `select` | Reverse of `primary_machine_parameter_id` |

**Deliberately unmapped.** `machine_sensor_reading` (32 million rows a year), `operational_event`.

**Back Populates**

| This side | Target side |
|---|---|
| `MachineParameter.type_parameters` | `MachineTypeParameter.machine_parameter` |
| `MachineParameter.threshold_rules` | `AlertThresholdRule.machine_parameter` |
| `MachineParameter.primary_for_failure_modes` | `MachineTypeFailureMode.primary_machine_parameter` |

**Python Types**

`int`, `str`, `bool`, `Decimal`, `Optional[str]`, `MeasurementDomain`, `ParameterDataType`, `DegradationDirection`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Numeric`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `measurement_domain` | `MeasurementDomain` | `measurement_domain` | No |
| `data_type` | `ParameterDataType` | `parameter_data_type` | No |
| `degradation_direction` | `DegradationDirection` | `degradation_direction` | No |

`unit_of_measure` here is `VARCHAR(16)`, **not** the `unit_of_measure` enum used by `Product` and `InventoryItem`. Different concept, different type, and the ORM must not conflate them: a new instrument may introduce `Pa`, `dB`, or `µm`, and the value is displayed rather than compared.

**Validation**

- `machine_parameter_code` — upper-case and strip. **Treat as immutable in practice**: renaming one orphans historical telemetry and breaks the traceability chain. Recorded as a procedural rule, not an ORM hook, because administrative correction before any telemetry exists is legitimate.
- `physical_max > physical_min` — **database**, already check-constrained.

**Loading Strategy**

Three bounded collections, `select`. No many-to-one — this is a root of the parameter subgraph.

**Read Components**

Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

7 rows. Grows when new instrumentation is introduced.

**Notes**

*Business* — `physical_min`/`physical_max` are sensor limits, distinct from `machine_type_parameter.normal_min`/`normal_max` which are operational expectations. A reading outside physical bounds is a sensor fault; a reading outside normal bounds is a process condition. Two different columns on two different tables because they are two different judgements.

*Implementation* — `degradation_direction` is what lets the Prediction Agent interpret a trend without a per-parameter rule table. Rising temperature is bad, rising throughput is not, and the direction is data rather than code.

---

### M12. MachineTypeParameter

**Purpose**

Which parameters a machine type exposes, with the nominal value, normal range, and sampling interval that apply. The association object resolving machine_type ↔ machine_parameter as a many-to-many, and the definition of the ML feature set.

**Python Class Name**

`MachineTypeParameter`

**Mapped Table**

`machine_type_parameter`

**Logical Group**

`master`

**Primary Key**

`machine_type_parameter_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_type_parameter_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `machine_type_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_type`, `RESTRICT` |
| `machine_parameter_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_parameter`, `RESTRICT` |
| `nominal_value` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | Expected steady-state value |
| `normal_min` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | Operational expectation, not a sensor limit |
| `normal_max` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | |
| `sampling_interval_seconds` | INTEGER | `int` | `Integer` | No | — | Drives simulator emission rate |
| `is_ml_feature` | INTEGER | `bool` | `Boolean` | No | `1` | Defines the Prediction Agent's feature set |
| `expected_drift_direction` | TEXT | `DriftDirection` | `Enum(DriftDirection)` + `ck_*_drift_direction_allowed` | No | — | |
| `sensor_accuracy_pct` | NUMERIC(5,2) | `Optional[Decimal]` | `Percent` → `Numeric(5,2)` | Yes | — | |
| `criticality_weight` | NUMERIC(4,2) | `Optional[Decimal]` | `Weight` → `Numeric(4,2)` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine_type` | Many-to-one | `MachineType` | `select` | Mandatory |
| `machine_parameter` | Many-to-one | `MachineParameter` | `select` | Mandatory |

**Back Populates**

| This side | Target side |
|---|---|
| `MachineTypeParameter.machine_type` | `MachineType.type_parameters` |
| `MachineTypeParameter.machine_parameter` | `MachineParameter.type_parameters` |

**Python Types**

`int`, `bool`, `Decimal`, `Optional[Decimal]`, `DriftDirection`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `Numeric`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `expected_drift_direction` | `DriftDirection` | `drift_direction` | No |

**Validation**

- `normal_min <= nominal_value <= normal_max`, and both within the parameter's physical bounds — **database** for the first, **writing component** for the second, since it spans two tables (§41.3).
- Unique on (`machine_type_id`, `machine_parameter_id`) — database constraint.

**Loading Strategy**

Two many-to-one, `select`. **Association object**; `secondary=` not used. The Prediction Agent's feature-set read is `MachineType.type_parameters` with `selectinload` chained to `machine_parameter`, filtered on `is_ml_feature`.

**Read Components**

Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

~28 rows. Grows as machine types × declared parameters.

**Notes**

*Business* — `is_ml_feature` makes the feature set data rather than code. Adding a parameter to the model is a row edit, not a deployment.

*Implementation* — `criticality_weight` uses the `Weight` alias, `NUMERIC(4,2)`, which appears exactly once in the schema. It still gets an alias rather than an inline type, because §38.4's rule is that every repeated *or semantically named* precision is aliased, so no model declares a bare `Numeric(p, s)` and a reader never has to judge whether a precision is significant.

---

### M13. WorkerRole

**Purpose**

The job function, carrying the authority flags that determine who may stop a line or authorise maintenance. Those two booleans are what let the platform route a recommendation to someone who can act on it.

**Python Class Name**

`WorkerRole`

**Mapped Table**

`worker_role`

**Logical Group**

`master`

**Primary Key**

`worker_role_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `worker_role_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `worker_role_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique |
| `role_name` | VARCHAR(80) | `str` | `String(80)` | No | — | |
| `role_category` | TEXT | `RoleCategory` | `Enum(RoleCategory)` + `ck_*_role_category_allowed` | No | — | |
| `is_managerial` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `seniority_rank` | INTEGER | `int` | `Integer` | No | — | Escalation ordering |
| `can_authorize_line_stop` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `can_authorize_maintenance` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `requires_certification` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `description` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `workers` | One-to-many | `Worker` | `select` | Bounded by roster |

**Back Populates**

| This side | Target side |
|---|---|
| `WorkerRole.workers` | `Worker.worker_role` |

**Python Types**

`int`, `str`, `bool`, `Optional[str]`, `RoleCategory`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `role_category` | `RoleCategory` | `role_category` | No |

**Validation**

- `worker_role_code` — upper-case and strip.
- No rule relating `is_managerial` to the two authority flags. A senior technician may authorise maintenance without being managerial, and the schema deliberately keeps them independent.

**Loading Strategy**

One bounded collection, `select`.

**Read Components**

Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

10 rows. Fixed.

**Notes**

*Business* — the authority booleans are read, never written, by agents. A recommendation routed to someone who cannot authorise the action it recommends is a recommendation that cannot be acted on, and this is the table that prevents it.

*Implementation* — `seniority_rank` is an ordering integer, not a foreign key to a level table. The frozen schema chose that, and the ORM maps it as a plain `int` with no attempt to model it as a scale.

---

### M14. Worker

**Purpose**

A person. The actor on every human-attributed operational record — inspections, scrap entries, acknowledgements, maintenance activities, and recorded decisions on recommendations.

**Python Class Name**

`Worker`

**Mapped Table**

`worker`

**Logical Group**

`master`

**Primary Key**

`worker_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `worker_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `worker_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique |
| `first_name` | VARCHAR(60) | `str` | `String(60)` | No | — | |
| `last_name` | VARCHAR(60) | `str` | `String(60)` | No | — | |
| `worker_role_id` | INTEGER | `int` | `Integer` | No | — | FK → `worker_role`, `RESTRICT` |
| `department_id` | INTEGER | `int` | `Integer` | No | — | FK → `department`, `RESTRICT` |
| `production_line_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_line`, `RESTRICT`. NULL for non-line staff |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `email` | VARCHAR(150) | `Optional[str]` | `String(150)` | Yes | — | Notification channel |
| `phone_number` | VARCHAR(20) | `Optional[str]` | `String(20)` | Yes | — | WhatsApp channel |
| `hire_date` | DATE | `date` | `Date` | No | — | |
| `employment_type` | TEXT | `EmploymentType` | `Enum(EmploymentType)` + `ck_*_employment_type_allowed` | No | — | |
| `skill_level` | TEXT | `SkillLevel` | `Enum(SkillLevel)` + `ck_*_skill_level_allowed` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `worker_role` | Many-to-one | `WorkerRole` | `select` | Mandatory |
| `department` | Many-to-one | `Department` | `select` | Mandatory |
| `production_line` | Many-to-one, optional | `ProductionLine` | `select` | NULL for non-line staff |
| `shift` | Many-to-one | `Shift` | `select` | Mandatory |
| `maintenance_engineer` | **One-to-one**, optional | `MaintenanceEngineer` | `select`, `uselist=False` | Present only for engineers |
| `notification_recipient` | **One-to-one**, optional | `NotificationRecipient` | `select`, `uselist=False` | Present only for recipients |

**Deliberately unmapped — 5 operational reverse collections.** `quality_inspection_result`, `scrap_record`, `inventory_movement`, `machine_maintenance_activity`, `operational_alert` (as acknowledger), `recommendation_action`, `audit_log` (~730,000 rows a year).

**Back Populates**

| This side | Target side |
|---|---|
| `Worker.worker_role` | `WorkerRole.workers` |
| `Worker.department` | `Department.workers` |
| `Worker.production_line` | `ProductionLine.workers` |
| `Worker.shift` | `Shift.workers` |
| `Worker.maintenance_engineer` | `MaintenanceEngineer.worker` |
| `Worker.notification_recipient` | `NotificationRecipient.worker` |

**Python Types**

`int`, `str`, `date`, `Optional[int]`, `Optional[str]`, `EmploymentType`, `SkillLevel`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Date`, `Enum`, `Boolean`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `employment_type` | `EmploymentType` | `employment_type` | No |
| `skill_level` | `SkillLevel` | `skill_level` | No |

**Validation**

- `email` — lower-case and strip. Format is check-constrained.
- `worker_code` — upper-case and strip.
- `first_name`, `last_name` — strip only. No case normalisation; a person's name is not the ORM's to reformat.
- Retirement via `is_active = FALSE` when an employee leaves, never a delete. Historical maintenance, acknowledgement, and notification records reference them.

**Loading Strategy**

Four many-to-one and two scalar one-to-one, all `select`. **Both one-to-one relationships are legitimately `None` for most rows** — 5 of ~13 workers are engineers, 5 are recipients — so every caller must handle `None` rather than assuming presence.

**Read Components**

Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

13 rows in the sample seed; a complete roster is ~105. Grows with headcount.

**Notes**

*Business* — `email` and `phone_number` are both nullable, and `notification_recipient` carries the per-channel enable flags. A recipient with `email_enabled = TRUE` and a NULL `worker.email` is a configuration error the Notification Service must detect; the schema does not prevent it because the two facts live on two tables (§41.3).

*Implementation* — this is one of two models with **two** optional one-to-one children, and §33 explains why they are separate models rather than joined-table inheritance: a worker may be an engineer *and* a recipient simultaneously, which single inheritance cannot express.

---

### M15. MaintenanceTeam

**Purpose**

The crew that responds to a failure. Its `specialization` is matched directly against `failure_category.required_specialization`, which is what turns a detected failure into a dispatch target.

**Python Class Name**

`MaintenanceTeam`

**Mapped Table**

`maintenance_team`

**Logical Group**

`master`

**Primary Key**

`maintenance_team_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `maintenance_team_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `maintenance_team_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique |
| `team_name` | VARCHAR(100) | `str` | `String(100)` | No | — | |
| `department_id` | INTEGER | `int` | `Integer` | No | — | FK → `department`, `RESTRICT` |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `specialization` | TEXT | `MaintenanceSpecialization` | `Enum(MaintenanceSpecialization)` + `ck_*_maintenance_specialization_allowed` | No | — | **Shared type.** Matched against failure category |
| `base_plant_area_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `plant_area`, `RESTRICT`. Station, not ownership |
| `contact_extension` | VARCHAR(10) | `Optional[str]` | `String(10)` | Yes | — | |
| `max_concurrent_jobs` | INTEGER | `int` | `Integer` | No | — | Dispatch capacity |
| `is_emergency_response` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `target_response_time_minutes` | INTEGER | `int` | `Integer` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `department` | Many-to-one | `Department` | `select` | Mandatory |
| `shift` | Many-to-one | `Shift` | `select` | Mandatory |
| `base_plant_area` | Many-to-one, optional | `PlantArea` | `select` | Where stationed |
| `engineers` | One-to-many | `MaintenanceEngineer` | `select` | Bounded at 5 |
| `maintenance_schedules` | One-to-many, optional | `MachineMaintenanceSchedule` | `select` | Reverse of `assigned_maintenance_team_id` |

**Deliberately unmapped.** `maintenance_work_record`, `ai_recommendation` (as suggested team).

**Back Populates**

| This side | Target side |
|---|---|
| `MaintenanceTeam.department` | `Department.maintenance_teams` |
| `MaintenanceTeam.shift` | `Shift.maintenance_teams` |
| `MaintenanceTeam.base_plant_area` | `PlantArea.based_maintenance_teams` |
| `MaintenanceTeam.engineers` | `MaintenanceEngineer.maintenance_team` |
| `MaintenanceTeam.maintenance_schedules` | `MachineMaintenanceSchedule.assigned_maintenance_team` |

**Python Types**

`int`, `str`, `bool`, `Optional[int]`, `Optional[str]`, `MaintenanceSpecialization`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `specialization` | `MaintenanceSpecialization` | `maintenance_specialization` | No |

**Validation**

- `maintenance_team_code` — upper-case and strip.
- `max_concurrent_jobs` against open work records — **writing component.** It is a live count against an operational table and no database constraint can express it.

**Loading Strategy**

Three many-to-one and two bounded collections, all `select`.

**Read Components**

Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

4 rows. Grows with maintenance organisation changes.

**Notes**

*Business* — `specialization` sharing one enum type with `failure_category.required_specialization` is what makes team matching a value comparison instead of an inference rule. That is the single strongest argument for shared enum types in the schema (§40.2).

*Implementation* — `base_plant_area` is nullable and named for its role. A team may be plant-wide, and NULL means exactly that rather than missing data.

---

### M16. MaintenanceEngineer

**Purpose**

A worker's maintenance-specific profile: their disciplines, certification currency, on-call status, and team. The extension table that keeps maintenance concerns off `worker`.

**Python Class Name**

`MaintenanceEngineer`

**Mapped Table**

`maintenance_engineer`

**Logical Group**

`master`

**Primary Key**

`maintenance_engineer_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `maintenance_engineer_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `maintenance_engineer_code` | VARCHAR(10) | `str` | `String(10)` | No | — | Business key, unique |
| `worker_id` | INTEGER | `int` | `Integer` | No | — | FK → `worker`, `RESTRICT`. **Unique — one-to-one** |
| `maintenance_team_id` | INTEGER | `int` | `Integer` | No | — | FK → `maintenance_team`, `RESTRICT` |
| `primary_specialization` | TEXT | `MaintenanceSpecialization` | `Enum(MaintenanceSpecialization)` + `ck_*_maintenance_specialization_allowed` | No | — | **Shared type** |
| `is_team_lead` | INTEGER | `bool` | `Boolean` | No | `0` | **Partial unique index** — one lead per team |
| `years_experience` | INTEGER | `int` | `Integer` | No | — | |
| `certification_expiry_date` | DATE | `Optional[date]` | `Date` | Yes | — | NULL when no certification applies |
| `is_on_call` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `secondary_specialization` | TEXT | `Optional[MaintenanceSpecialization]` | `Enum(MaintenanceSpecialization)` + `ck_*_maintenance_specialization_allowed` | Yes | — | **Same shared type, second column** |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `worker` | **One-to-one** | `Worker` | `select` | Mandatory, unique |
| `maintenance_team` | Many-to-one | `MaintenanceTeam` | `select` | Mandatory |

**Deliberately unmapped.** `maintenance_work_record` (as assigned engineer), `ai_recommendation` (as suggested engineer).

**Back Populates**

| This side | Target side |
|---|---|
| `MaintenanceEngineer.worker` | `Worker.maintenance_engineer` |
| `MaintenanceEngineer.maintenance_team` | `MaintenanceTeam.engineers` |

**Python Types**

`int`, `str`, `bool`, `Optional[date]`, `MaintenanceSpecialization`, `Optional[MaintenanceSpecialization]`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Date`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `primary_specialization` | `MaintenanceSpecialization` | `maintenance_specialization` | No |
| `secondary_specialization` | `MaintenanceSpecialization` | `maintenance_specialization` | Yes |

**Two columns on this model bind the same enum class**, and in SQLite that means two independent check constraints carrying the same value list — `ck_maintenance_engineer_primary_specialization_allowed` and `ck_maintenance_engineer_secondary_specialization_allowed`. There is no shared type object, so there is no duplicate-creation hazard to guard against; what has to be guarded instead is that the two lists stay identical, which is why both are transcribed from the single catalogue entry rather than from each other (§40.2, §40.4).

**Validation**

- `secondary_specialization != primary_specialization` — **database**, check-constrained.
- Cannot be soft-retired while `is_team_lead = TRUE`; leadership must be reassigned first. **Writing component** (§41.3) — it is a state pre-condition no constraint can express.
- One lead per team — **partial unique index**, `WHERE is_team_lead`.

**Loading Strategy**

One one-to-one and one many-to-one, both `select`. The `worker` side is mandatory, so unlike `Worker.maintenance_engineer` it is never `None`.

**Read Components**

Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

5 rows. Grows with the maintenance team.

**Notes**

*Business* — the one-to-one is a role, not a subtype. §33 explains why joined-table inheritance was rejected: it would make 5 engineer rows a subclass of 13 worker rows and add an implicit join to every `Worker` query.

*Implementation* — the asymmetry of the one-to-one is worth stating precisely. `MaintenanceEngineer.worker` is `Worker` — non-optional. `Worker.maintenance_engineer` is `Optional[MaintenanceEngineer]` — the same relationship, opposite nullability. Getting this backwards produces a type checker that permits a real `None` dereference.

---

### M17. NotificationRecipient

**Purpose**

Who gets told what, through which channel, and in what escalation order. Its `min_severity_level_id` is the filter that keeps the platform from becoming noise.

**Python Class Name**

`NotificationRecipient`

**Mapped Table**

`notification_recipient`

**Logical Group**

`master`

**Primary Key**

`notification_recipient_id` — `int`, `MasterPk`. No business key column — identity is the worker.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `notification_recipient_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `worker_id` | INTEGER | `int` | `Integer` | No | — | FK → `worker`, `RESTRICT`. **Unique — one-to-one** |
| `min_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT`. The noise filter |
| `email_enabled` | INTEGER | `bool` | `Boolean` | No | `1` | |
| `whatsapp_enabled` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `scope_production_line_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_line`, `RESTRICT`. NULL means plant-wide |
| `notify_outside_shift_hours` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `escalation_order` | INTEGER | `int` | `Integer` | No | — | |
| `max_notifications_per_hour` | INTEGER | `Optional[int]` | `Integer` | Yes | — | NULL means unlimited |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `worker` | **One-to-one** | `Worker` | `select` | Mandatory, unique |
| `min_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Mandatory |
| `scope_production_line` | Many-to-one, optional | `ProductionLine` | `select` | NULL means plant-wide |

**Deliberately unmapped.** `notification` — ~2,500 rows a year against 5 recipient rows.

**Back Populates**

| This side | Target side |
|---|---|
| `NotificationRecipient.worker` | `Worker.notification_recipient` |
| `NotificationRecipient.min_severity_level` | `FailureSeverityLevel.notification_recipients` |
| `NotificationRecipient.scope_production_line` | `ProductionLine.notification_recipients` |

**Python Types**

`int`, `bool`, `Optional[int]`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `Boolean`, `DateTime`.

**Enum Usage**

None. One of six master models with no enum column. Severity is a foreign key to `failure_severity_level`, not an enum — deliberately, because severity carries behaviour columns an enum cannot.

**Validation**

- At least one channel enabled — **database**, check-constrained.
- `email_enabled = TRUE` requires a non-NULL `worker.email` — **writing component.** It spans two tables, so no single-row constraint can express it, and a recipient configured for email with no address is a silent delivery failure. §41.3.

**Loading Strategy**

Three many-to-one, `select`. The Notification Service's recipient resolution reads all 5 rows with `selectinload` on `worker` and `min_severity_level`, which is one query plus two.

**Read Components**

Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

5 rows. Grows with notification policy changes.

**Notes**

*Business* — `scope_production_line_id` NULL meaning plant-wide is a **meaningful null**, not missing data. A caller filtering recipients for a line must match `scope_production_line_id = :line OR scope_production_line_id IS NULL`, and getting that wrong silently excludes every plant-wide recipient.

*Implementation* — one of two models with no `<table>_code` business key, because its identity is its worker and a separate code would be a second way to name the same person.

---

### M18. InventoryLocation

**Purpose**

Where material physically sits. Its `average_retrieval_time_minutes` is what lets a recommendation state how long fetching a spare part will add to a repair.

**Python Class Name**

`InventoryLocation`

**Mapped Table**

`inventory_location`

**Logical Group**

`master`

**Primary Key**

`inventory_location_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `inventory_location_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `inventory_location_code` | VARCHAR(16) | `str` | `String(16)` | No | — | Business key, unique |
| `location_name` | VARCHAR(100) | `str` | `String(100)` | No | — | |
| `plant_area_id` | INTEGER | `int` | `Integer` | No | — | FK → `plant_area`, `RESTRICT` |
| `location_type` | TEXT | `InventoryLocationType` | `Enum(InventoryLocationType)` + `ck_*_inventory_location_type_allowed` | No | — | |
| `capacity_slots` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `is_temperature_controlled` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `average_retrieval_time_minutes` | INTEGER | `int` | `Integer` | No | — | Feeds repair duration estimates |
| `stock_count_frequency_days` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `plant_area` | Many-to-one | `PlantArea` | `select` | Mandatory |
| `inventory_items` | One-to-many | `InventoryItem` | `select` | Reverse of `default_inventory_location_id`. Bounded at 11 |

**Deliberately unmapped.** `inventory_movement` — ~22,000 rows a year.

**Back Populates**

| This side | Target side |
|---|---|
| `InventoryLocation.plant_area` | `PlantArea.inventory_locations` |
| `InventoryLocation.inventory_items` | `InventoryItem.default_inventory_location` |

**Python Types**

`int`, `str`, `bool`, `Optional[int]`, `InventoryLocationType`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `location_type` | `InventoryLocationType` | `inventory_location_type` | No |

**Validation**

- `inventory_location_code` — upper-case and strip.

**Loading Strategy**

One many-to-one and one bounded collection, `select`.

**Read Components**

Simulator, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

5 rows. Fixed unless storage is reorganised.

**Notes**

*Business* — `average_retrieval_time_minutes` is `NOT NULL` because a location whose retrieval time is unknown cannot participate in a downtime estimate, and the Decision Agent's estimated downtime would silently understate reality.

*Implementation* — `inventory_items` is the reverse of a **default** location, not a current one. Actual stock position lives in `inventory_movement.resulting_quantity_on_hand`. The relationship name deliberately does not say "items stored here", because that is a different question answered by a different table.

---

### M19. InventoryItem

**Purpose**

Raw materials, components, and spare parts. Carries the reorder policy and lead time that let a recommendation say whether the required part is available now or must be ordered.

**Python Class Name**

`InventoryItem`

**Mapped Table**

`inventory_item`

**Logical Group**

`master`

**Primary Key**

`inventory_item_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `inventory_item_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `inventory_item_code` | VARCHAR(24) | `str` | `String(24)` | No | — | Business key, unique |
| `item_name` | VARCHAR(150) | `str` | `String(150)` | No | — | |
| `item_type` | TEXT | `InventoryItemType` | `Enum(InventoryItemType)` + `ck_*_inventory_item_type_allowed` | No | — | |
| `unit_of_measure` | TEXT | `UnitOfMeasure` | `Enum(UnitOfMeasure)` + `ck_*_unit_of_measure_allowed` | No | — | **All 6 values permitted here**, unlike `Product` |
| `unit_cost` | NUMERIC(12,2) | `Decimal` | `Money` → `Numeric(12,2)` | No | — | |
| `reorder_point` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `safety_stock_qty` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `max_stock_qty` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `lead_time_days` | INTEGER | `int` | `Integer` | No | — | |
| `primary_supplier_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `supplier`, `RESTRICT` |
| `default_inventory_location_id` | INTEGER | `int` | `Integer` | No | — | FK → `inventory_location`, `RESTRICT` |
| `is_critical_spare` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `abc_class` | CHAR(1) | `str` | `CHAR(1)` | No | — | **Deliberately not a vocabulary class** — a three-letter check instead (§40.6) |
| `shelf_life_days` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `specification` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `primary_supplier` | Many-to-one, optional | `Supplier` | `select` | NULL when unsourced |
| `default_inventory_location` | Many-to-one | `InventoryLocation` | `select` | Mandatory |
| `bom_lines` | One-to-many | `BillOfMaterials` | `select` | Reverse of `inventory_item_id`. Bounded at 10 |
| `bom_lines_as_substitute` | One-to-many, optional | `BillOfMaterials` | `select` | Reverse of `substitute_inventory_item_id` |
| `required_by_failure_modes` | One-to-many, optional | `MachineTypeFailureMode` | `select` | Reverse of `required_inventory_item_id` |
| `required_by_maintenance_schedules` | One-to-many, optional | `MachineMaintenanceSchedule` | `select` | Reverse of `required_inventory_item_id` |

**Deliberately unmapped.** `inventory_movement`, `operational_event`, `operational_alert`, `ai_recommendation`.

**Back Populates**

| This side | Target side |
|---|---|
| `InventoryItem.primary_supplier` | `Supplier.inventory_items` |
| `InventoryItem.default_inventory_location` | `InventoryLocation.inventory_items` |
| `InventoryItem.bom_lines` | `BillOfMaterials.inventory_item` |
| `InventoryItem.bom_lines_as_substitute` | `BillOfMaterials.substitute_inventory_item` |
| `InventoryItem.required_by_failure_modes` | `MachineTypeFailureMode.required_inventory_item` |
| `InventoryItem.required_by_maintenance_schedules` | `MachineMaintenanceSchedule.required_inventory_item` |

**Python Types**

`int`, `str`, `bool`, `Decimal`, `Optional[int]`, `Optional[str]`, `InventoryItemType`, `UnitOfMeasure`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `CHAR`, `Text`, `Numeric`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `item_type` | `InventoryItemType` | `inventory_item_type` | No |
| `unit_of_measure` | `UnitOfMeasure` | `unit_of_measure` | No |

`abc_class` is `CHAR(1)` with a check constraint, **not** an enum. The frozen schema's reasoning (§37.4) is that a three-value single-character classification is lighter as a check and the values are conventionally letters rather than words. The ORM maps it as `str` and does not invent an enum for it.

**Validation**

- `inventory_item_code` — upper-case and strip.
- `abc_class` — upper-case. The check constraint would otherwise reject a lower-case input that is semantically correct.
- `safety_stock_qty <= reorder_point <= max_stock_qty` — **database**, check-constrained.
- Cannot be sourced from a retired supplier — **writing component** (§41.3).

**Loading Strategy**

Two many-to-one and four bounded collections, all `select`. Four of the six relationships resolve nullable foreign keys and are legitimately empty.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

11 rows. Grows with the parts catalogue.

**Notes**

*Business* — `is_critical_spare` combined with `lead_time_days` is what makes the difference between a recommendation that says "replace the bearing" and one that says "replace the bearing; the part is a critical spare with a 14-day lead time and none is on hand".

*Implementation* — this model has the most **role-qualified reverse collections** of any master model: four of six are reverses of nullable, role-named foreign keys. Each is named for the role rather than the target table, because four collections all called `bill_of_materials` or `failure_modes` would be indistinguishable.

---

### M20. BillOfMaterials

**Purpose**

What each product is made of, and how much. The association object resolving product ↔ inventory_item as a many-to-many, and the multiplier that converts a scrapped unit into consumed material.

**Python Class Name**

`BillOfMaterials`

**Mapped Table**

`bill_of_materials`

**Logical Group**

`master`

**Primary Key**

`bill_of_materials_id` — `int`, `MasterPk`. No business key column.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `bill_of_materials_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `product_id` | INTEGER | `int` | `Integer` | No | — | FK → `product`, `RESTRICT` |
| `inventory_item_id` | INTEGER | `int` | `Integer` | No | — | FK → `inventory_item`, `RESTRICT`. **Role: material** |
| `quantity_per_unit` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | Four decimals because fractional consumption is real |
| `scrap_allowance_pct` | NUMERIC(5,2) | `Decimal` | `Percent` → `Numeric(5,2)` | No | `0` | |
| `is_critical_component` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `substitute_inventory_item_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `inventory_item`, `RESTRICT`. **Role: substitute** |
| `effective_from_date` | DATE | `date` | `Date` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `product` | Many-to-one | `Product` | `select` | Mandatory |
| `inventory_item` | Many-to-one | `InventoryItem` | `select` | Mandatory. Uses `inventory_item_id` |
| `substitute_inventory_item` | Many-to-one, optional | `InventoryItem` | `select` | Uses `substitute_inventory_item_id` |

**Back Populates**

| This side | Target side |
|---|---|
| `BillOfMaterials.product` | `Product.bom_lines` |
| `BillOfMaterials.inventory_item` | `InventoryItem.bom_lines` |
| `BillOfMaterials.substitute_inventory_item` | `InventoryItem.bom_lines_as_substitute` |

**Python Types**

`int`, `bool`, `date`, `Decimal`, `Optional[int]`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `Numeric`, `Date`, `Boolean`, `DateTime`.

**Enum Usage**

None. One of six master models with no enum column.

**Validation**

- `substitute_inventory_item_id != inventory_item_id` — **database**, check-constrained.
- `quantity_per_unit > 0` — **database**.
- Unique on (`product_id`, `inventory_item_id`) — database constraint.

**Loading Strategy**

Three many-to-one, `select`. **Association object**; `secondary=` not used, because this table carries five attributes beyond its keys.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only.

**Growth**

10 rows. Grows as products × components.

**Notes**

*Business* — `substitute_inventory_item_id` is what allows a material shortage to be a recoverable condition rather than a stop. The Decision Agent reads it before recommending a production hold.

*Implementation* — **two foreign keys to the same target from one model.** Both relationships must declare which foreign key they use, because SQLAlchemy cannot otherwise determine the join condition and raises an ambiguity error at mapper configuration. This is one of nine such cases (§21) and the first of two where the double reference is to a master table from a master table.

---
### M21. Supplier

**Purpose**

Who supplies parts, how reliably, and how fast. `reliability_rating` and `standard_lead_time_days` are what let a recommendation weigh replacing a part against waiting for one.

**Python Class Name**

`Supplier`

**Mapped Table**

`supplier`

**Logical Group**

`master`

**Primary Key**

`supplier_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `supplier_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `supplier_code` | VARCHAR(10) | `str` | `String(10)` | No | — | Business key, unique |
| `supplier_name` | VARCHAR(150) | `str` | `String(150)` | No | — | |
| `supplier_type` | TEXT | `SupplierType` | `Enum(SupplierType)` + `ck_*_supplier_type_allowed` | No | — | |
| `contact_person` | VARCHAR(100) | `Optional[str]` | `String(100)` | Yes | — | |
| `contact_email` | VARCHAR(150) | `Optional[str]` | `String(150)` | Yes | — | |
| `contact_phone` | VARCHAR(20) | `Optional[str]` | `String(20)` | Yes | — | |
| `city` | VARCHAR(80) | `str` | `String(80)` | No | — | |
| `country_code` | CHAR(2) | `str` | `CHAR(2)` | No | — | ISO 3166-1 alpha-2 |
| `standard_lead_time_days` | INTEGER | `int` | `Integer` | No | — | |
| `expedited_lead_time_days` | INTEGER | `Optional[int]` | `Integer` | Yes | — | NULL when expediting is unavailable |
| `reliability_rating` | NUMERIC(2,1) | `Decimal` | `Ratio1` → `Numeric(2,1)` | No | — | One decimal, single digit |
| `on_time_delivery_pct` | NUMERIC(5,2) | `Optional[Decimal]` | `Percent` → `Numeric(5,2)` | Yes | — | |
| `is_approved_vendor` | INTEGER | `bool` | `Boolean` | No | `1` | |
| `contract_expiry_date` | DATE | `Optional[date]` | `Date` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `inventory_items` | One-to-many | `InventoryItem` | `select` | Reverse of `primary_supplier_id`. Bounded at 11 |

**Deliberately unmapped.** `inventory_movement`.

**Back Populates**

| This side | Target side |
|---|---|
| `Supplier.inventory_items` | `InventoryItem.primary_supplier` |

**Python Types**

`int`, `str`, `bool`, `Decimal`, `Optional[str]`, `Optional[int]`, `Optional[Decimal]`, `Optional[date]`, `SupplierType`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `CHAR`, `Numeric`, `Date`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `supplier_type` | `SupplierType` | `supplier_type` | No |

**Validation**

- `supplier_code`, `country_code` — upper-case and strip.
- `contact_email` — lower-case and strip.
- `expedited_lead_time_days < standard_lead_time_days` when present — **database**, check-constrained.
- **Cannot be soft-retired while an active `inventory_item` names it as primary source** — writing component (§41.3). An item left with no source cannot be replenished at all.

**Loading Strategy**

One bounded collection, `select`. No many-to-one.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

4 rows. Grows with the vendor base.

**Notes**

*Business* — `reliability_rating` at `NUMERIC(2,1)` holds one digit and one decimal, so 0.0–9.9. That precision is the frozen schema's, and the ORM honours it exactly rather than widening it to a more "convenient" scale.

*Implementation* — the `Ratio1` alias appears once in the schema. It exists so this column does not become the sole place a bare `Numeric(2, 1)` is written (§38.4).

---

### M22. Customer

**Purpose**

Who the output is for, and what a late delivery costs. `priority_tier` and `late_delivery_penalty_per_day` are what let the platform rank two delayed runs by consequence rather than by size.

**Python Class Name**

`Customer`

**Mapped Table**

`customer`

**Logical Group**

`master`

**Primary Key**

`customer_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `customer_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `customer_code` | VARCHAR(10) | `str` | `String(10)` | No | — | Business key, unique |
| `customer_name` | VARCHAR(150) | `str` | `String(150)` | No | — | |
| `priority_tier` | TEXT | `CustomerPriorityTier` | `Enum(CustomerPriorityTier)` + `ck_*_customer_priority_tier_allowed` | No | — | |
| `industry_sector` | VARCHAR(80) | `Optional[str]` | `String(80)` | Yes | — | |
| `city` | VARCHAR(80) | `str` | `String(80)` | No | — | |
| `country_code` | CHAR(2) | `str` | `CHAR(2)` | No | — | ISO 3166-1 alpha-2 |
| `contact_person` | VARCHAR(100) | `Optional[str]` | `String(100)` | Yes | — | |
| `contact_email` | VARCHAR(150) | `Optional[str]` | `String(150)` | Yes | — | |
| `late_delivery_penalty_per_day` | NUMERIC(12,2) | `Optional[Decimal]` | `Money` → `Numeric(12,2)` | Yes | — | NULL when no contractual penalty |
| `contractual_otd_target_pct` | NUMERIC(5,2) | `Optional[Decimal]` | `Percent` → `Numeric(5,2)` | Yes | — | |
| `annual_order_value` | NUMERIC(14,2) | `Optional[Decimal]` | `MoneyLarge` → `Numeric(14,2)` | Yes | — | Wider than `Money` — annual aggregate |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

None mapped. `Customer` holds no foreign keys, and its only inbound reference — `production_run` — is unmapped as a reverse collection under Rule L1.

**Deliberately unmapped.** `production_run` — ~2,000 rows a year against 3 customer rows.

**Back Populates**

None. `ProductionRun.customer` is a **unidirectional** many-to-one and declares no `back_populates`.

**Python Types**

`int`, `str`, `Optional[str]`, `Optional[Decimal]`, `CustomerPriorityTier`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `CHAR`, `Numeric`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `priority_tier` | `CustomerPriorityTier` | `customer_priority_tier` | No |

**Validation**

- `customer_code`, `country_code` — upper-case and strip.
- `contact_email` — lower-case and strip.

**Loading Strategy**

No relationships. Three rows, always cheap to read whole.

**Read Components**

Simulator, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

3 rows. Grows with the customer base.

**Notes**

*Business* — `late_delivery_penalty_per_day` being nullable matters to the Decision Agent's impact calculation: NULL means no contractual penalty, not zero penalty, and the two lead to different phrasing in a recommendation.

*Implementation* — the only master model with **zero mapped relationships**. It is included in the dependency graph as an isolated node with one inbound edge, and its class definition is columns and mixins only.

---

### M23. FailureSeverityLevel

**Purpose**

The severity scale, carrying the behaviour each level implies — response time, line-stop requirement, escalation, acknowledgement deadline, display colour. Referenced by 5 master and 6 operational tables.

**Python Class Name**

`FailureSeverityLevel`

**Mapped Table**

`failure_severity_level`

**Logical Group**

`master`

**Primary Key**

`failure_severity_level_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `failure_severity_level_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `failure_severity_level_code` | VARCHAR(8) | `str` | `String(8)` | No | — | Business key, unique |
| `severity_name` | VARCHAR(40) | `str` | `String(40)` | No | — | |
| `severity_rank` | INTEGER | `int` | `Integer` | No | — | Unique. The comparison axis |
| `description` | TEXT | `str` | `Text` | No | — | **NOT NULL** — the scale must be self-documenting |
| `target_response_time_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `requires_line_stop` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `requires_immediate_escalation` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `requires_manager_acknowledgement` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `max_acknowledgement_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `display_color_hex` | CHAR(7) | `str` | `CHAR(7)` | No | — | Dashboard rendering. `#RRGGBB` |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `failure_categories` | One-to-many | `FailureCategory` | `select` | Reverse of `default_severity_level_id`. Bounded at 12 |
| `typical_for_failure_modes` | One-to-many | `MachineTypeFailureMode` | `select` | Reverse of `typical_severity_level_id` |
| `warning_threshold_rules` | One-to-many | `AlertThresholdRule` | `select` | Reverse of `warning_severity_level_id` |
| `critical_threshold_rules` | One-to-many | `AlertThresholdRule` | `select` | Reverse of `critical_severity_level_id` |
| `notification_recipients` | One-to-many | `NotificationRecipient` | `select` | Reverse of `min_severity_level_id`. Bounded at 5 |

**Deliberately unmapped — 6 operational reverse collections.** `operational_event`, `operational_alert` (×2 roles), `maintenance_work_record`, `prediction_result`, `ai_recommendation`, `notification`. Five severity rows against six growing operational tables.

**Back Populates**

| This side | Target side |
|---|---|
| `FailureSeverityLevel.failure_categories` | `FailureCategory.default_severity_level` |
| `FailureSeverityLevel.typical_for_failure_modes` | `MachineTypeFailureMode.typical_severity_level` |
| `FailureSeverityLevel.warning_threshold_rules` | `AlertThresholdRule.warning_severity_level` |
| `FailureSeverityLevel.critical_threshold_rules` | `AlertThresholdRule.critical_severity_level` |
| `FailureSeverityLevel.notification_recipients` | `NotificationRecipient.min_severity_level` |

**Python Types**

`int`, `str`, `bool`, `Optional[int]`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `CHAR`, `Text`, `Boolean`, `DateTime`.

**Enum Usage**

None. **This is the schema's most important non-vocabulary.** Severity is a table rather than a check-constrained vocabulary precisely because each level carries behaviour columns — response time, escalation flags, colour — that a value list cannot hold. §40.6 records the general rule.

**Validation**

- `failure_severity_level_code` — upper-case and strip.
- `display_color_hex` — upper-case, preserving the leading `#`. The check constraint enforces `#RRGGBB` with upper-case hex digits, so a lower-case input would be rejected without normalisation.
- `requires_manager_acknowledgement = TRUE` requires `max_acknowledgement_minutes` — **database**, check-constrained.
- **Effectively immutable in practice.** Adding a level mid-project would require re-evaluating every threshold rule, failure category, and recipient filter referencing the scale. Not ORM-enforced; recorded as a procedural rule.

**Loading Strategy**

Five bounded collections, `select`. Five rows, permanently resident in any session's identity map, so the many-to-one relationships pointing here are almost free.

**Read Components**

All eight components.

**Write Components**

Administrative only.

**Growth**

5 rows. Effectively immutable.

**Notes**

*Business* — `description` is `NOT NULL` because a severity scale nobody can interpret is worse than none. Every other master `description` column is nullable; this one is not, and the difference is deliberate.

*Implementation* — **two of the five collections are reverses of two foreign keys on the same child** (`AlertThresholdRule`). Both must specify their foreign key, and both are named for their role. A single `threshold_rules` collection would silently union warning and critical rules and make the count wrong (§21).

---

### M24. FailureCategory

**Purpose**

The failure taxonomy. Its `required_specialization` is matched against `maintenance_team.specialization` to route a repair, and its `default_severity_level_id` supplies the severity when nothing more specific applies.

**Python Class Name**

`FailureCategory`

**Mapped Table**

`failure_category`

**Logical Group**

`master`

**Primary Key**

`failure_category_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `failure_category_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `failure_category_code` | VARCHAR(10) | `str` | `String(10)` | No | — | Business key, unique |
| `category_name` | VARCHAR(100) | `str` | `String(100)` | No | — | |
| `failure_domain` | TEXT | `FailureDomain` | `Enum(FailureDomain)` + `ck_*_failure_domain_allowed` | No | — | |
| `default_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT` |
| `required_specialization` | TEXT | `MaintenanceSpecialization` | `Enum(MaintenanceSpecialization)` + `ck_*_maintenance_specialization_allowed` | No | — | **Shared type.** Matched against team specialization |
| `requires_spare_part` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `has_safety_implication` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `description` | TEXT | `str` | `Text` | No | — | **NOT NULL** |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `default_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Mandatory |
| `failure_modes` | One-to-many | `MachineTypeFailureMode` | `select` | ~22 total |

**Deliberately unmapped — 6 operational references.** `quality_inspection_result`, `scrap_record`, `maintenance_work_record` (×2 roles: reported and confirmed), `prediction_result`, `ai_recommendation`.

**Back Populates**

| This side | Target side |
|---|---|
| `FailureCategory.default_severity_level` | `FailureSeverityLevel.failure_categories` |
| `FailureCategory.failure_modes` | `MachineTypeFailureMode.failure_category` |

**Python Types**

`int`, `str`, `bool`, `FailureDomain`, `MaintenanceSpecialization`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `failure_domain` | `FailureDomain` | `failure_domain` | No |
| `required_specialization` | `MaintenanceSpecialization` | `maintenance_specialization` | No |

**Validation**

- `failure_category_code` — upper-case and strip.
- Growth discipline: categories are added with engineering review, not to accommodate a single unexplained event. Procedural, not ORM.

**Loading Strategy**

One many-to-one and one collection, `select`. Twelve rows, effectively always cached.

**Read Components**

Simulator, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

12 rows. Grows deliberately, with engineering review.

**Notes**

*Business* — `has_safety_implication` is what makes a failure category non-deferrable regardless of its severity level. The two are independent facts and the schema keeps them so.

*Implementation* — `maintenance_work_record` references this model **twice**, as `reported_failure_category_id` and `confirmed_failure_category_id`. Neither reverse is mapped, but both forward relationships on the child must name their foreign key explicitly (§21).

---

### M25. MachineTypeFailureMode

**Purpose**

How a given machine type actually fails, with its leading indicator, repair duration, warning period, and whether a model can predict it. The association object resolving machine_type ↔ failure_category as a many-to-many, and **the most connected table in the master schema** with five parents.

**Python Class Name**

`MachineTypeFailureMode`

**Mapped Table**

`machine_type_failure_mode`

**Logical Group**

`master`

**Primary Key**

`machine_type_failure_mode_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_type_failure_mode_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `machine_type_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_type`, `RESTRICT` |
| `failure_category_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_category`, `RESTRICT` |
| `typical_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT` |
| `primary_machine_parameter_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine_parameter`, `RESTRICT`. The telemetry signal |
| `required_inventory_item_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `inventory_item`, `RESTRICT`. The spare part |
| `leading_indicator_description` | TEXT | `str` | `Text` | No | — | **NOT NULL** |
| `estimated_repair_duration_minutes` | INTEGER | `int` | `Integer` | No | — | |
| `typical_warning_period_hours` | INTEGER | `Optional[int]` | `Integer` | Yes | — | NULL for sudden failures |
| `is_model_predictable` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `relative_frequency` | TEXT | `RelativeFrequency` | `Enum(RelativeFrequency)` + `ck_*_relative_frequency_allowed` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine_type` | Many-to-one | `MachineType` | `select` | Mandatory |
| `failure_category` | Many-to-one | `FailureCategory` | `select` | Mandatory |
| `typical_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Mandatory |
| `primary_machine_parameter` | Many-to-one, optional | `MachineParameter` | `select` | NULL when no single signal |
| `required_inventory_item` | Many-to-one, optional | `InventoryItem` | `select` | NULL when no part needed |

**Deliberately unmapped.** `prediction_result` — ~62,000 rows a year.

**Back Populates**

| This side | Target side |
|---|---|
| `MachineTypeFailureMode.machine_type` | `MachineType.failure_modes` |
| `MachineTypeFailureMode.failure_category` | `FailureCategory.failure_modes` |
| `MachineTypeFailureMode.typical_severity_level` | `FailureSeverityLevel.typical_for_failure_modes` |
| `MachineTypeFailureMode.primary_machine_parameter` | `MachineParameter.primary_for_failure_modes` |
| `MachineTypeFailureMode.required_inventory_item` | `InventoryItem.required_by_failure_modes` |

**Python Types**

`int`, `str`, `bool`, `Optional[int]`, `RelativeFrequency`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `Text`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `relative_frequency` | `RelativeFrequency` | `relative_frequency` | No |

**Validation**

- `is_model_predictable = TRUE` requires `primary_machine_parameter_id` — **database**, check-constrained. A predictable mode with no signal is not predictable.
- Unique on (`machine_type_id`, `failure_category_id`) — database constraint.

**Loading Strategy**

Five many-to-one, all `select`. **This is the model where per-query eager loading matters most.** The Decision Agent reads a failure mode with all five parents in one step, which is a `joinedload` chain of five master tables — cheap, since all five are small. Relying on lazy loading here would be five round trips per mode.

**Read Components**

Simulator, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

~22 rows. Grows as machine types × plausible failure modes.

**Notes**

*Business* — the connectivity is the point. This is the single join where equipment, failure taxonomy, severity policy, telemetry, and inventory meet, which is exactly what the Decision Agent needs from one lookup.

*Implementation* — five many-to-one relationships on 22 rows is the clearest case in the schema for eager loading being a **query** decision rather than a model default (§20). A dashboard listing all 22 modes wants `selectinload` on each parent; the Decision Agent reading one wants `joinedload`. Baking either in would penalise the other.

---

### M26. MachineMaintenanceSchedule

**Purpose**

The preventive maintenance plan per machine: what interval, what basis, how long, who does it, what part it needs, and whether it may be deferred. The baseline against which the platform judges whether maintenance is overdue.

**Python Class Name**

`MachineMaintenanceSchedule`

**Mapped Table**

`machine_maintenance_schedule`

**Logical Group**

`master`

**Primary Key**

`machine_maintenance_schedule_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_maintenance_schedule_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `machine_maintenance_schedule_code` | VARCHAR(12) | `str` | `String(12)` | No | — | Business key, unique |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `maintenance_type` | TEXT | `MaintenanceType` | `Enum(MaintenanceType)` + `ck_*_maintenance_type_allowed` | No | — | |
| `interval_basis` | TEXT | `IntervalBasis` | `Enum(IntervalBasis)` + `ck_*_interval_basis_allowed` | No | — | Calendar, operating hours, or cycles |
| `interval_value` | INTEGER | `int` | `Integer` | No | — | Interpreted per `interval_basis` |
| `estimated_duration_minutes` | INTEGER | `int` | `Integer` | No | — | |
| `requires_line_stop` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `assigned_maintenance_team_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_team`, `RESTRICT` |
| `required_inventory_item_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `inventory_item`, `RESTRICT` |
| `baseline_start_date` | DATE | `date` | `Date` | No | — | Anchor for interval arithmetic |
| `can_be_deferred` | INTEGER | `bool` | `Boolean` | No | `1` | |
| `max_deferral_days` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `task_summary` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one | `Machine` | `select` | Mandatory |
| `assigned_maintenance_team` | Many-to-one, optional | `MaintenanceTeam` | `select` | NULL means assign at dispatch |
| `required_inventory_item` | Many-to-one, optional | `InventoryItem` | `select` | NULL when no part needed |

**Deliberately unmapped.** `maintenance_work_record` — ~2,500 rows a year.

**Back Populates**

| This side | Target side |
|---|---|
| `MachineMaintenanceSchedule.machine` | `Machine.maintenance_schedules` |
| `MachineMaintenanceSchedule.assigned_maintenance_team` | `MaintenanceTeam.maintenance_schedules` |
| `MachineMaintenanceSchedule.required_inventory_item` | `InventoryItem.required_by_maintenance_schedules` |

**Python Types**

`int`, `str`, `bool`, `date`, `Optional[int]`, `Optional[str]`, `MaintenanceType`, `IntervalBasis`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Date`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `maintenance_type` | `MaintenanceType` | `maintenance_type` | No |
| `interval_basis` | `IntervalBasis` | `interval_basis` | No |

**Validation**

- `can_be_deferred = FALSE` requires `max_deferral_days IS NULL` — **database**, check-constrained.
- Due-date computation from `interval_basis`, `interval_value`, `baseline_start_date`, and the machine's accumulated hours or cycles — **writing component.** It reads `machine_operational_status`, so it is a cross-table rule and no per-row predicate or ORM hook can see it.

**Loading Strategy**

Three many-to-one, `select`.

**Read Components**

Simulator, Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only.

**Growth**

8 rows. Grows as machines × maintenance policies.

**Notes**

*Business* — `interval_basis` is why this cannot be a simple date interval. A schedule may be every 90 days, every 2,000 operating hours, or every 50,000 cycles, and the operating-hour and cycle bases read live counters from `machine_operational_status`.

*Implementation* — `interval_value` is a bare integer whose meaning depends on a sibling enum column. The ORM maps both as-is and derives nothing. A property that returned "days until due" would need to read an operational table from a master model, which R4 (§16) forbids.

---

### M27. AlertThresholdProfile

**Purpose**

A named, versioned set of threshold rules for a machine type. Versioning by row rather than by edit is what allows an event to cite the rule that fired it years later.

**Python Class Name**

`AlertThresholdProfile`

**Mapped Table**

`alert_threshold_profile`

**Logical Group**

`master`

**Primary Key**

`alert_threshold_profile_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `alert_threshold_profile_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `alert_threshold_profile_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `profile_name` | VARCHAR(120) | `str` | `String(120)` | No | — | |
| `machine_type_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_type`, `RESTRICT` |
| `version` | INTEGER | `int` | `Integer` | No | `1` | Superseded versions are retired, never edited |
| `is_default` | INTEGER | `bool` | `Boolean` | No | `0` | **Partial unique index** — one default per machine type |
| `sensitivity` | TEXT | `ThresholdSensitivity` | `Enum(ThresholdSensitivity)` + `ck_*_threshold_sensitivity_allowed` | No | — | |
| `effective_from_date` | DATE | `date` | `Date` | No | — | |
| `review_due_date` | DATE | `Optional[date]` | `Date` | Yes | — | |
| `notes` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine_type` | Many-to-one | `MachineType` | `select` | Mandatory |
| `rules` | One-to-many | `AlertThresholdRule` | `select` | ~4 per profile |
| `machines` | One-to-many | `Machine` | `select` | Machines overriding the type default. Bounded at 8 |

**Back Populates**

| This side | Target side |
|---|---|
| `AlertThresholdProfile.machine_type` | `MachineType.alert_threshold_profiles` |
| `AlertThresholdProfile.rules` | `AlertThresholdRule.alert_threshold_profile` |
| `AlertThresholdProfile.machines` | `Machine.alert_threshold_profile` |

**Python Types**

`int`, `str`, `bool`, `date`, `Optional[date]`, `Optional[str]`, `ThresholdSensitivity`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Date`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `sensitivity` | `ThresholdSensitivity` | `threshold_sensitivity` | No |

**Validation**

- `alert_threshold_profile_code` — upper-case and strip.
- One default per machine type — **partial unique index**, `WHERE is_default`.
- **Superseded versions are never edited.** Retuning creates a new row with an incremented `version` and retires the old one. Procedural, not ORM-enforced, because administrative correction of a typo before the profile is in use is legitimate.
- Changes are audited to `audit_log` with before and after values. **Writing component** — the audit interface, not the ORM.

**Loading Strategy**

One many-to-one and two bounded collections, `select`. The Monitoring Agent's threshold read is a profile with `selectinload` on `rules` chained to `machine_parameter` — two queries for a complete rule set.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only, with audit.

**Growth**

6 rows. Grows with retuning, since superseded versions are soft-retired rather than edited.

**Notes**

*Business* — versioning by row is what allows `operational_event` to reference a rule for lineage while separately capturing the breached value as evidence. If profiles were edited in place, an old event's cited rule would describe a threshold that never fired it.

*Implementation* — `machines` is the reverse of a **nullable** foreign key on `Machine`, so it holds only machines that override their type's default profile. Most machines have `alert_threshold_profile_id IS NULL` and appear in no profile's `machines` collection. Reading "which machines use this profile" therefore requires the default-fallback logic, which belongs to the Monitoring Agent.

---

### M28. AlertThresholdRule

**Purpose**

One parameter's warning and critical bounds within a profile, with the sustained duration and rate-of-change limit that distinguish a real excursion from a spike. The association object resolving alert_threshold_profile ↔ machine_parameter as a many-to-many.

**Python Class Name**

`AlertThresholdRule`

**Mapped Table**

`alert_threshold_rule`

**Logical Group**

`master`

**Primary Key**

`alert_threshold_rule_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `alert_threshold_rule_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `alert_threshold_profile_id` | INTEGER | `int` | `Integer` | No | — | FK → `alert_threshold_profile`, `RESTRICT` |
| `machine_parameter_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_parameter`, `RESTRICT` |
| `warning_low` | NUMERIC(12,4) | `Optional[Decimal]` | `Measurement` → `Numeric(12,4)` | Yes | — | NULL when no lower warning applies |
| `warning_high` | NUMERIC(12,4) | `Optional[Decimal]` | `Measurement` → `Numeric(12,4)` | Yes | — | |
| `critical_low` | NUMERIC(12,4) | `Optional[Decimal]` | `Measurement` → `Numeric(12,4)` | Yes | — | |
| `critical_high` | NUMERIC(12,4) | `Optional[Decimal]` | `Measurement` → `Numeric(12,4)` | Yes | — | |
| `sustained_duration_seconds` | INTEGER | `int` | `Integer` | No | `0` | 0 means fire immediately |
| `warning_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT`. **Role: warning** |
| `critical_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT`. **Role: critical** |
| `rate_of_change_limit_per_minute` | NUMERIC(12,4) | `Optional[Decimal]` | `Measurement` → `Numeric(12,4)` | Yes | — | |
| `is_enabled` | INTEGER | `bool` | `Boolean` | No | `1` | Distinct from `is_active` |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `alert_threshold_profile` | Many-to-one | `AlertThresholdProfile` | `select` | Mandatory |
| `machine_parameter` | Many-to-one | `MachineParameter` | `select` | Mandatory |
| `warning_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Uses `warning_severity_level_id` |
| `critical_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Uses `critical_severity_level_id` |

**Deliberately unmapped.** `operational_event` — ~15,000 rows a year.

**Back Populates**

| This side | Target side |
|---|---|
| `AlertThresholdRule.alert_threshold_profile` | `AlertThresholdProfile.rules` |
| `AlertThresholdRule.machine_parameter` | `MachineParameter.threshold_rules` |
| `AlertThresholdRule.warning_severity_level` | `FailureSeverityLevel.warning_threshold_rules` |
| `AlertThresholdRule.critical_severity_level` | `FailureSeverityLevel.critical_threshold_rules` |

**Python Types**

`int`, `bool`, `Optional[Decimal]`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `Numeric`, `Boolean`, `DateTime`.

**Enum Usage**

None. One of six master models with no enum column.

**Validation**

- At least one of the four bound columns must be non-NULL — **database**, check-constrained. A rule with no bounds cannot fire.
- `critical_low <= warning_low` and `critical_high >= warning_high` where both are present — **database**.
- Unique on (`alert_threshold_profile_id`, `machine_parameter_id`) — database constraint.
- `is_enabled` and `is_active` are **different facts** and neither implies the other. `is_enabled = FALSE` is a temporary suppression by the threshold owner; `is_active = FALSE` is retirement of the rule row. The ORM keeps both and derives nothing.

**Loading Strategy**

Four many-to-one, `select`. **Association object.** The Monitoring Agent loads a profile's rules with `selectinload` on `machine_parameter` and both severity levels — three additional queries for a complete, ready-to-evaluate rule set.

**Read Components**

Simulator, Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard.

**Write Components**

Administrative only, with audit.

**Growth**

~26 rows. Grows as profiles × monitored parameters.

**Notes**

*Business* — `sustained_duration_seconds` defaulting to 0 rather than being nullable means "fire immediately" is a value rather than an absence. The Monitoring Agent's evaluation is then uniform: it always compares elapsed time against a number.

*Implementation* — **two foreign keys to `FailureSeverityLevel`**, both mandatory, both role-named, both specifying their foreign key. This is the only model in the schema with two mandatory references to the same target, which makes it the case most likely to be implemented with an ambiguous join condition if the foreign key is not stated (§21).

---

### M29. BusinessRule

**Purpose**

Configurable platform policy — escalation thresholds, suppression windows, severity floors — stored as data so that changing agent behaviour is a row edit rather than a deployment. Read by agents, never written by them.

**Python Class Name**

`BusinessRule`

**Mapped Table**

`business_rule`

**Logical Group**

`master`

**Primary Key**

`business_rule_id` — `int`, `MasterPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `business_rule_id` | INTEGER | `int` | `MasterPk` | No | autoincrement | Primary key |
| `business_rule_code` | VARCHAR(32) | `str` | `String(32)` | No | — | Business key, unique. How agents look a rule up |
| `rule_name` | VARCHAR(150) | `str` | `String(150)` | No | — | |
| `rule_category` | TEXT | `BusinessRuleCategory` | `Enum(BusinessRuleCategory)` + `ck_*_business_rule_category_allowed` | No | — | |
| `value_type` | TEXT | `BusinessRuleValueType` | `Enum(BusinessRuleValueType)` + `ck_*_business_rule_value_type_allowed` | No | — | Discriminates which value column is populated |
| `value_numeric` | NUMERIC(14,4) | `Optional[Decimal]` | `RuleNumeric` → `Numeric(14,4)` | Yes | — | Populated when `value_type` is numeric |
| `value_text` | VARCHAR(100) | `Optional[str]` | `String(100)` | Yes | — | |
| `value_boolean` | INTEGER | `Optional[bool]` | `Boolean` | Yes | — | **The only nullable boolean in the database** |
| `unit` | VARCHAR(24) | `Optional[str]` | `String(24)` | Yes | — | |
| `production_line_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_line`, `RESTRICT`. NULL means plant-wide |
| `description` | TEXT | `str` | `Text` | No | — | **NOT NULL** |
| `effective_from_date` | DATE | `date` | `Date` | No | — | |
| *mixins* | — | — | — | — | — | `is_active`, `created_at`, `updated_at` |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `production_line` | Many-to-one, optional | `ProductionLine` | `select` | NULL means plant-wide |

**Deliberately unmapped.** `supervisor_context` — ~9,000 rows a year.

**Back Populates**

| This side | Target side |
|---|---|
| `BusinessRule.production_line` | `ProductionLine.business_rules` |

**Python Types**

`int`, `str`, `date`, `Optional[Decimal]`, `Optional[str]`, `Optional[bool]`, `Optional[int]`, `BusinessRuleCategory`, `BusinessRuleValueType`; from mixins `bool`, `datetime`.

**SQLAlchemy Types**

`Integer`, `String`, `Text`, `Numeric`, `Date`, `Boolean`, `Enum`, `DateTime`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `rule_category` | `BusinessRuleCategory` | `business_rule_category` | No |
| `value_type` | `BusinessRuleValueType` | `business_rule_value_type` | No |

**Validation**

- **Exactly one of `value_numeric`, `value_text`, `value_boolean` is populated, matching `value_type`** — database, check-constrained. The ORM does **not** expose a single `value` property that switches on `value_type`; that would be a typed abstraction over three differently typed columns, and it would have to return a union the caller must narrow anyway. Callers read the column their `value_type` indicates.
- `business_rule_code` — upper-case and strip.
- **Superseded values are soft-retired with a new row created, never edited in place.** Procedural. Editing would destroy the record of what policy produced a past decision, which is part of the audit trail and is what lets `supervisor_context` reference a rule without copying its value.

**Loading Strategy**

One optional many-to-one, `select`. Agents read rules by code, typically loading the whole 11-row table once per cycle rather than querying per rule.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard.

**Write Components**

Administrative only. **Rules are read by agents and never written by them** — an agent that could rewrite its own escalation threshold would make the platform's behaviour unexplainable.

**Growth**

11 rows. Grows with policy refinement.

**Notes**

*Business* — line-scoped rules override plant-wide ones, and the resolution logic is the reading agent's. A caller must union `production_line_id = :line` with `production_line_id IS NULL` and prefer the former, which is why §M5 declines to model it as a relationship on `ProductionLine`.

*Implementation* — `value_boolean` is the **only nullable boolean column in the entire database**. Every other boolean is `NOT NULL` with a default. It is nullable here because only one of three value columns is populated per row, and the ORM types it `Optional[bool]` — a three-state attribute that callers must not treat as falsy when absent.

---
---

# Part V — Operational Models

---

## 36. Operational model conventions

All 24 operational models share the following, stated once here rather than 24 times.

| Property | Value |
|---|---|
| Logical group | `operational` for O1–O22, **`system` for O23–O24**. Documentation and packaging only; one database file (§13) |
| Primary key | `<table>_id`, `OperationalPk` alias — `Integer`, `INTEGER PRIMARY KEY AUTOINCREMENT` |
| Mixins, append-only (16 models) | `TimestampCreatedMixin` + `ComponentProvenanceMixin` |
| Mixins, mutable (8 models) | `TimestampCreatedMixin` + `TimestampUpdatedMixin` + `ComponentProvenanceMixin` |
| Trailing columns | `created_at` (`datetime`, server default `CURRENT_TIMESTAMP`), `created_by_component` (`PlatformComponent`, no default), `updated_at` on the 8 mutable models only |
| Soft delete | **None.** No operational model carries `is_active`. Retirement is retention-driven purge or archive |
| Foreign key `ON DELETE` | `RESTRICT` on all 114 outbound keys except **one** `SET NULL` — `operational_event.triggering_reading_id` |
| Record time vs event time | Every model carries both. `created_at` is when the row landed; the model's own timestamp column is when the thing happened |
| Write component | Exactly one per model, per the ownership model |

**The eight mutable models**, restated because it is the single most consulted fact in this Part: `MachineOperationalStatus`, `ProductionRun`, `ProductionCount`, `MaintenanceWorkRecord`, `OperationalAlert`, `NotificationDelivery`, `DashboardSnapshot`, `SystemHealthStatus`. **Every other operational model is append-only**, its absence of `updated_at` is the schema's statement of immutability, and §41.4 specifies the ORM hooks that enforce it.

**Reverse collections.** Rule L1 (§19.1) bites hardest here. Most operational models are children of `Machine`, `Shift`, and `ProductionRun`, and almost none of those reverse collections is mapped. Each model's Relationships subsection lists what is mapped; a **Deliberately unmapped** note lists what is not and why.

**Loading on the four high-volume models.** `MachineSensorReading`, `CycleHistory`, `ProductionCount`, and `AuditLog` declare `raise_on_sql` on every many-to-one, per Rule L4 (§19.4). Their entries say so explicitly rather than leaving it to this preamble.

**Subsection format** is identical to Part IV, including the mapping of Nullable Rules, Default Strategy, and Relationship Cardinality onto table columns (§35).

---

### O1. MachineSensorReading

**Purpose**

One measurement of one parameter on one machine at one instant. The raw telemetry stream — the platform's ground truth about machine condition, and the input to every detection and prediction the platform performs.

**Python Class Name**

`MachineSensorReading`

**Mapped Table**

`machine_sensor_reading`

**Logical Group**

`operational`

**Primary Key**

`machine_sensor_reading_id` — `int`, `OperationalPk` (`Integer`). At 32 million rows a year this is the one key where range matters, and **SQLite removes the concern rather than requiring a wider type**: `INTEGER` is a 64-bit signed rowid, so there is no exhaustion horizon inside the asset lifetime of the machines being measured. `BigInteger` must **not** be substituted here — it renders `BIGINT`, which is not a rowid alias and rejects `AUTOINCREMENT`, and `AUTOINCREMENT` is what prevents the retention purge from recycling identifiers (§39.3).

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_sensor_reading_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `machine_parameter_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine_parameter`, `RESTRICT` |
| `recorded_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time.** When the reading was taken |
| `reading_value` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | Never `float`. See Notes |
| `quality_flag` | TEXT | `ReadingQualityFlag` | `Enum(ReadingQualityFlag)` + `ck_*_reading_quality_flag_allowed` | No | `'valid'` | Sensor trust, not process judgement |
| `machine_state_at_reading` | TEXT | `MachineOperationalState` | `Enum(MachineOperationalState)` + `ck_*_machine_operational_state_allowed` | No | — | Denormalised deliberately — see Notes |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `production_run_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_run`, `RESTRICT`. NULL when idle |
| `sequence_number` | INTEGER | `int` | `Integer` | No | — | Monotonic per machine. Deterministic ordering when timestamps tie |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one | `Machine` | **`raise_on_sql`** | Rule L4 |
| `machine_parameter` | Many-to-one | `MachineParameter` | **`raise_on_sql`** | Rule L4 |
| `shift` | Many-to-one | `Shift` | **`raise_on_sql`** | Rule L4. Unidirectional |
| `production_run` | Many-to-one, optional | `ProductionRun` | **`raise_on_sql`** | Unidirectional |

**Deliberately unmapped.** `operational_event.triggering_reading_id` — the reverse would be a collection on a 32-million-row table, and the relationship is one-directional by design because the event cites the reading, not the other way round.

**Back Populates**

**None.** All four relationships are unidirectional. `Machine`, `MachineParameter`, `Shift`, and `ProductionRun` all decline to map a reverse collection (Rule L1), so no `back_populates` pairing exists on any of them.

**Python Types**

`int`, `datetime`, `Decimal`, `Optional[int]`, `ReadingQualityFlag`, `MachineOperationalState`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Numeric`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `quality_flag` | `ReadingQualityFlag` | `reading_quality_flag` | No, default `'valid'` |
| `machine_state_at_reading` | `MachineOperationalState` | `machine_operational_state` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**These two enum columns are the reason the frozen schema chose native `Enum` over `TEXT`.** At 32 million rows a year, `machine_state_at_reading` as text averages ~12 bytes plus overhead against 4 for an enum — roughly 300 MB a year on one column of one table.

**Validation**

- `recorded_at` must be timezone-aware — **ORM `@validates`.** This is the highest-volume timestamp in the database and a naive value here would corrupt every window computation downstream (§41.3).
- **Immutability: every column.** Append-only, insert-only, never updated. `@validates` on all mapped attributes rejects reassignment once the instance is persistent (§41.4).
- `reading_value` within the parameter's physical bounds — **writing component.** It is a cross-table rule against `machine_parameter`, and no single-row constraint can express it.
- `quality_flag` is sensor trust; it does **not** mean the process was in spec. The ORM makes no inference between it and `reading_value`.

**Loading Strategy**

**`raise_on_sql` on all four relationships.** This is Rule L4's primary case. An agent iterating a day of readings and touching `.machine` would emit 87,000 queries; `raise_on_sql` makes that an immediate error while still permitting the load when the parent is already in the identity map — which it always is for `Machine` and `Shift`, both of which have single-digit row counts.

Bulk reads specify `selectinload` or, more usually, select the foreign key columns directly and resolve parents once from a small dictionary. Bulk inserts decline to fetch generated keys (§26).

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard, Analytics.

**Write Components**

**Factory Simulator only.** Insert only, never updated.

**Growth**

~87,000 rows/day · ~2.6 million/month · **~32 million/year. The largest table in the database by two orders of magnitude** — roughly 91 % of all rows.

**Notes**

*Business* — `machine_state_at_reading` is denormalised onto every row on purpose. Resolving a reading's machine state through `machine_state_transition` would require a temporal range join on 32 million rows for every analytical query. Storing 4 bytes per row instead is the correct trade.

*Implementation* — three decisions converge on this model and each is load-bearing. `Decimal`, never `float`: a `float` reading value accumulates representation error that becomes a false threshold breach at the fourth decimal. `raise_on_sql`: the only defence against an N+1 that would be invisible in testing and fatal in production. And `Integer` on both the key and `sequence_number`, because the sequence is monotonic per machine over the asset's whole life.

---

### O2. MachineOperationalStatus

**Purpose**

The current state of each machine — one row per machine, updated in place. The single lookup that answers *what is this machine doing right now*, and the carrier of the accumulated hour and cycle counters that drive usage-based maintenance.

**Python Class Name**

`MachineOperationalStatus`

**Mapped Table**

`machine_operational_status`

**Logical Group**

`operational`

**Primary Key**

`machine_operational_status_id` — `int`, `OperationalPk`. `INTEGER` for uniformity with the operational group despite the table holding 8 rows; the frozen schema declares it, and the ORM does not optimise it away.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_operational_status_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT`. **Unique — one-to-one** |
| `current_state` | TEXT | `MachineOperationalState` | `Enum(MachineOperationalState)` + `ck_*_machine_operational_state_allowed` | No | — | |
| `state_since` | DATETIME | `datetime` | `TimestampTz` | No | — | When the current state began |
| `current_production_run_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_run`, `RESTRICT`. NULL when idle |
| `current_shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `accumulated_operating_hours` | NUMERIC(12,2) | `Decimal` | `Hours` → `Numeric(12,2)` | No | `0` | Lifetime counter |
| `accumulated_cycle_count` | INTEGER | `int` | `Integer` | No | `0` | Lifetime counter |
| `operating_hours_at_last_maintenance` | NUMERIC(12,2) | `Optional[Decimal]` | `Hours` → `Numeric(12,2)` | Yes | — | NULL before first maintenance |
| `cycle_count_at_last_maintenance` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `last_reading_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | Staleness detection |
| `last_state_transition_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine_state_transition`, `RESTRICT` |
| `open_alert_count` | INTEGER | `int` | `Integer` | No | `0` | Maintained for dashboard performance. Reconcilable |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | **One-to-one** | `Machine` | `select` | Mandatory, unique |
| `current_shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `current_production_run` | Many-to-one, optional | `ProductionRun` | `select` | Unidirectional. NULL when idle |
| `last_state_transition` | Many-to-one, optional | `MachineStateTransition` | `select` | Unidirectional. NULL before first transition |

**Back Populates**

| This side | Target side |
|---|---|
| `MachineOperationalStatus.machine` | `Machine.operational_status` |

The other three are unidirectional.

**Python Types**

`int`, `datetime`, `Decimal`, `Optional[int]`, `Optional[Decimal]`, `Optional[datetime]`, `MachineOperationalState`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Numeric`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `current_state` | `MachineOperationalState` | `machine_operational_state` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `state_since` and `last_reading_at` must be timezone-aware — **ORM `@validates`**.
- **Mutable model.** No immutability hooks. `machine_id` is the exception: it is set once at insert and must never change, since the row *is* the machine's status. `@validates` enforces that one column's immutability.
- Counters are monotonic non-decreasing — **writing component.** The database constrains them non-negative; monotonicity across updates is a temporal rule no constraint can express.
- `open_alert_count` reconciliation against `operational_alert` — **Monitoring Agent.** It is a maintained counter, and the ORM does not derive it.

**Loading Strategy**

Four many-to-one, all `select`. The one-to-one `machine` is the most-traversed relationship in the platform and is almost always already in the identity map. Dashboard machine grids read all 8 rows with `selectinload` on `machine` and `current_production_run`.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics.

**Write Components**

**Factory Simulator only.** Insert once per machine, then update continuously.

**Growth**

**8 rows, fixed.** Grows only when a machine is added. Zero net growth in steady state.

**Notes**

*Business* — this is the only operational table that is updated continuously rather than appended to, and the only one whose row count is bounded by the asset count. The append-only history it summarises lives in `machine_state_transition`.

*Implementation* — `last_state_transition_id` is the reason T-SIM-2 (§24) needs a mid-transaction flush: the transition row must exist before its identity can be assigned here. Assigning the **relationship attribute** rather than the integer lets SQLAlchemy order the two statements itself, which is the preferred form and removes the explicit flush entirely.

---

### O3. MachineStateTransition

**Purpose**

Every change of machine state, with its reason and the duration of the state it left. The availability record — the basis of all downtime reporting and OEE calculation in the platform.

**Python Class Name**

`MachineStateTransition`

**Mapped Table**

`machine_state_transition`

**Logical Group**

`operational`

**Primary Key**

`machine_state_transition_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_state_transition_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `from_state` | TEXT | `Optional[MachineOperationalState]` | `Enum(MachineOperationalState)` + `ck_*_machine_operational_state_allowed` | Yes | — | **NULL on the first transition of a machine's life** |
| `to_state` | TEXT | `MachineOperationalState` | `Enum(MachineOperationalState)` + `ck_*_machine_operational_state_allowed` | No | — | |
| `transition_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `duration_in_previous_state_seconds` | INTEGER | `Optional[int]` | `Integer` | Yes | — | NULL when `from_state` is NULL |
| `reason_code` | TEXT | `StateTransitionReason` | `Enum(StateTransitionReason)` + `ck_*_state_transition_reason_allowed` | No | — | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `production_run_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_run`, `RESTRICT` |
| `triggering_event_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `operational_event`, `RESTRICT` |
| `triggering_work_record_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_work_record`, `RESTRICT` |
| `notes` | TEXT | `Optional[str]` | `Text` | Yes | — | Populated where a human decision drove the change |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one | `Machine` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `production_run` | Many-to-one, optional | `ProductionRun` | `select` | Unidirectional |
| `triggering_event` | Many-to-one, optional | `OperationalEvent` | `select` | Unidirectional |
| `triggering_work_record` | Many-to-one, optional | `MaintenanceWorkRecord` | `select` | Unidirectional |

**Deliberately unmapped.** `machine_operational_status.last_state_transition_id` — the reverse would be a one-to-one for the latest transition only, which is a *filtered* relationship rather than a structural one. Callers read `Machine.operational_status.last_state_transition` instead.

**Back Populates**

**None.** All five relationships are unidirectional.

**Python Types**

`int`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[MachineOperationalState]`, `MachineOperationalState`, `StateTransitionReason`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Text`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `from_state` | `MachineOperationalState` | `machine_operational_state` | **Yes** |
| `to_state` | `MachineOperationalState` | `machine_operational_state` | No |
| `reason_code` | `StateTransitionReason` | `state_transition_reason` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Two columns bind the same enum class**, one nullable and one not. Both declare `create_type=False` so migration emits the type once (§40.4).

**Validation**

- `transition_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only (§41.4).
- `from_state != to_state`, and `from_state IS NULL` implies `duration_in_previous_state_seconds IS NULL` — **database**, check-constrained.

**Loading Strategy**

Five many-to-one, `select`. Not a Rule L4 model — 73,000 rows a year is two orders of magnitude below the telemetry stream — but downtime analysis reads long ranges and should specify `selectinload` on `machine` and `triggering_work_record` rather than traversing per row.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics.

**Write Components**

**Factory Simulator only.** Insert only, in the same transaction as the `MachineOperationalStatus` update — boundary T-SIM-2.

**Growth**

~200 rows/day · ~73,000/year.

**Notes**

*Business* — `from_state` being nullable is the schema's way of marking a machine's first-ever transition without a sentinel state. A caller must treat `None` as "no previous state", never as an error.

*Implementation* — `duration_in_previous_state_seconds` is stored rather than derived. Deriving it would require the previous row per machine, which is a window function on a growing table for a value that is known at write time. The ORM stores it and computes nothing.

---

### O4. ProductionRun

**Purpose**

A manufacturing order: make this quantity of this product on this line for this customer by this date. **The most connected operational table**, with 11 inbound operational references, and the operational graph's layer-0 root — it holds no outbound operational foreign keys at all.

**Python Class Name**

`ProductionRun`

**Mapped Table**

`production_run`

**Logical Group**

`operational`

**Primary Key**

`production_run_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `production_run_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `production_run_code` | VARCHAR(16) | `str` | `String(16)` | No | — | Business key, unique |
| `product_id` | INTEGER | `int` | `Integer` | No | — | FK → `product`, `RESTRICT` |
| `production_line_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_line`, `RESTRICT` |
| `product_line_capability_id` | INTEGER | `int` | `Integer` | No | — | FK → `product_line_capability`, `RESTRICT`. Pins the cycle-time standard |
| `customer_id` | INTEGER | `int` | `Integer` | No | — | FK → `customer`, `RESTRICT` |
| `planned_quantity_units` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `planned_start_at` | DATETIME | `datetime` | `TimestampTz` | No | — | |
| `planned_end_at` | DATETIME | `datetime` | `TimestampTz` | No | — | |
| `actual_start_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | NULL until started |
| `actual_end_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | NULL until finished |
| `due_date` | DATE | `date` | `Date` | No | — | Customer commitment |
| `priority` | TEXT | `RunPriority` | `Enum(RunPriority)` + `ck_*_run_priority_allowed` | No | `'normal'` | |
| `run_status` | TEXT | `RunStatus` | `Enum(RunStatus)` + `ck_*_run_status_allowed` | No | `'planned'` | Lifecycle state |
| `pause_reason` | TEXT | `Optional[RunPauseReason]` | `Enum(RunPauseReason)` + `ck_*_run_pause_reason_allowed` | Yes | — | Required when paused |
| `cancellation_reason` | TEXT | `Optional[str]` | `Text` | Yes | — | Required when cancelled |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `product` | Many-to-one | `Product` | `select` | Unidirectional |
| `production_line` | Many-to-one | `ProductionLine` | `select` | Unidirectional |
| `product_line_capability` | Many-to-one | `ProductLineCapability` | `select` | Unidirectional |
| `customer` | Many-to-one | `Customer` | `select` | Unidirectional |
| `progress_snapshots` | One-to-many | `ProductionProgress` | `select` | ~70 per run |
| `quality_inspections` | One-to-many | `QualityInspectionResult` | `select` | ~6 per run |
| `scrap_records` | One-to-many | `ScrapRecord` | `select` | ~3 per run |

**Deliberately unmapped — 8 reverse collections.** `machine_sensor_reading` (millions per run), `cycle_history` (~650 per run), `production_count` (~290 per run), `machine_state_transition`, `machine_operational_status`, `inventory_movement`, `operational_event`, `ai_recommendation`. `cycle_history` and `production_count` both exceed the L2 bound of roughly 100 per parent, so both are excluded even though they are conceptually children.

**Back Populates**

| This side | Target side |
|---|---|
| `ProductionRun.progress_snapshots` | `ProductionProgress.production_run` |
| `ProductionRun.quality_inspections` | `QualityInspectionResult.production_run` |
| `ProductionRun.scrap_records` | `ScrapRecord.production_run` |

The four many-to-one relationships are unidirectional — no master model maps a `production_runs` collection.

**Python Types**

`int`, `str`, `date`, `datetime`, `Decimal`, `Optional[datetime]`, `Optional[str]`, `RunPriority`, `RunStatus`, `Optional[RunPauseReason]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `Numeric`, `Date`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `priority` | `RunPriority` | `run_priority` | No, default `'normal'` |
| `run_status` | `RunStatus` | `run_status` | No, default `'planned'` |
| `pause_reason` | `RunPauseReason` | `run_pause_reason` | Yes |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- All four timestamps timezone-aware — **ORM `@validates`**.
- **Mutable model**, but `production_run_code`, `product_id`, `production_line_id`, `product_line_capability_id`, and `customer_id` are **immutable after insert** — `@validates` enforces it. Changing a run's product mid-flight would invalidate every child record already attributed to it.
- `run_status = 'paused'` requires `pause_reason`; `'cancelled'` requires `cancellation_reason`; `actual_end_at >= actual_start_at` — **database**, check-constrained.
- Legal status transitions (planned → released → running → paused/completed/cancelled) — **writing component.** A state machine is behaviour, not a constraint, and the frozen schema places it with the Simulator.

**Loading Strategy**

Four many-to-one and three bounded collections, all `select`. The dashboard's run list specifies `selectinload` on `product` and `customer`. **No caller should traverse `progress_snapshots` for a current figure** — the latest snapshot is a filtered query, and loading 70 rows to read one is the mistake the collection makes available.

**Read Components**

All eight components.

**Write Components**

**Factory Simulator only.** Insert then update through the lifecycle.

**Growth**

~4–8 rows/day · ~2,000/year.

**Notes**

*Business* — the run header deliberately carries no quantity-produced columns. Final quantities live on the terminal `production_progress` snapshot, which is why that snapshot is exempt from the 180-day purge. Duplicating them here would create two authorities for one number.

*Implementation* — being the operational graph's layer-0 root means `ProductionRun` can be inserted with no operational prerequisites, which is what makes T-SIM-6 a single-model boundary. It also means the model imports nothing operational, so it is the natural first operational model to implement.

---

### O5. ProductionProgress

**Purpose**

A point-in-time snapshot of a run's cumulative output, rate, and schedule variance. What the dashboard shows as *how is this run doing*, and the only place a run's final quantities are recorded.

**Python Class Name**

`ProductionProgress`

**Mapped Table**

`production_progress`

**Logical Group**

`operational`

**Primary Key**

`production_progress_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `production_progress_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `production_run_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_run`, `RESTRICT` |
| `snapshot_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `quantity_good_cumulative` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `quantity_scrapped_cumulative` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `quantity_rework_cumulative` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `percent_complete` | NUMERIC(5,2) | `Decimal` | `Percent` → `Numeric(5,2)` | No | — | |
| `current_rate_units_per_hour` | NUMERIC(10,2) | `Decimal` | `Rate` → `Numeric(10,2)` | No | — | |
| `elapsed_production_seconds` | INTEGER | `int` | `Integer` | No | — | |
| `downtime_seconds_cumulative` | INTEGER | `int` | `Integer` | No | `0` | |
| `projected_completion_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | NULL when rate is zero |
| `schedule_variance_minutes` | INTEGER | `int` | `Integer` | No | — | Signed. Negative is ahead |
| `is_behind_schedule` | INTEGER | `bool` | `Boolean` | No | — | **No default** — a judgement the writer makes |
| `scrap_rate_pct` | NUMERIC(5,2) | `Decimal` | `Percent` → `Numeric(5,2)` | No | — | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `production_run` | Many-to-one | `ProductionRun` | `select` | Mandatory |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |

**Back Populates**

| This side | Target side |
|---|---|
| `ProductionProgress.production_run` | `ProductionRun.progress_snapshots` |

**Python Types**

`int`, `bool`, `datetime`, `Decimal`, `Optional[datetime]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Numeric`, `Boolean`, `DateTime`, `Enum` (mixin only).

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

No enum column of its own. One of three operational models in that position, the others being `ProductionCount` and `PredictionResult`.

**Validation**

- `snapshot_at`, `projected_completion_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only (§41.4).
- Cumulative quantities non-decreasing across snapshots of a run — **writing component.** It is a temporal rule across rows.
- `is_behind_schedule` consistency with `schedule_variance_minutes` — **writing component.** The schema gives the boolean no default precisely because it is the writer's judgement, and re-deriving it in the ORM would make the stored value redundant.

**Loading Strategy**

Two many-to-one, `select`. Reading a run's current progress is `ORDER BY snapshot_at DESC LIMIT 1` on this model, **not** a traversal of `ProductionRun.progress_snapshots`.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Dashboard, Analytics.

**Write Components**

**Factory Simulator only.** Insert only.

**Growth**

~380 rows/day · ~140,000/year.

**Notes**

*Business* — the terminal snapshot per run is exempt from the 180-day purge and retained with the run, because it holds the final quantities the run header deliberately does not. A purge job that does not honour that exemption destroys the production record.

*Implementation* — fully regenerable from `production_count` and `machine_state_transition`, which is why aggressive purge is safe. The ORM has no part in regeneration; it is the Simulator's idempotent rebuild path.

---

### O6. ProductionCount

**Purpose**

Per-machine output aggregated over a fixed interval: good, scrap, rework, cycles, and running time. The dashboard's throughput source, and the surviving production record once per-cycle detail is purged.

**Python Class Name**

`ProductionCount`

**Mapped Table**

`production_count`

**Logical Group**

`operational`

**Primary Key**

`production_count_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `production_count_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `production_run_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_run`, `RESTRICT`. NULL when idle |
| `interval_from` | DATETIME | `datetime` | `TimestampTz` | No | — | Interval start |
| `interval_to` | DATETIME | `datetime` | `TimestampTz` | No | — | Interval end |
| `good_count` | INTEGER | `int` | `Integer` | No | `0` | |
| `scrap_count` | INTEGER | `int` | `Integer` | No | `0` | |
| `rework_count` | INTEGER | `int` | `Integer` | No | `0` | |
| `cycles_completed` | INTEGER | `int` | `Integer` | No | `0` | |
| `total_cycle_time_seconds` | INTEGER | `int` | `Integer` | No | `0` | |
| `running_seconds` | INTEGER | `int` | `Integer` | No | `0` | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** (rebuild) |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one | `Machine` | **`raise_on_sql`** | Rule L4 |
| `production_run` | Many-to-one, optional | `ProductionRun` | **`raise_on_sql`** | Rule L4 |
| `shift` | Many-to-one | `Shift` | **`raise_on_sql`** | Rule L4 |

**Back Populates**

**None.** All three relationships are unidirectional. `ProductionRun` declines the reverse because ~290 counts per run exceeds the L2 bound.

**Python Types**

`int`, `datetime`, `Optional[int]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `DateTime`, `Enum` (mixin only).

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `interval_from`, `interval_to` timezone-aware — **ORM `@validates`**.
- **Mutable, but only by idempotent rebuild.** `machine_id`, `interval_from`, and `interval_to` are immutable after insert — they are the row's natural identity and the unique constraint's columns. `@validates` enforces it. The count columns may be rewritten by a rebuild.
- `interval_to > interval_from`; counts non-negative; `total_cycle_time_seconds` consistent with `cycles_completed` — **database**, check-constrained.
- Unique on (`machine_id`, `interval_from`) — database constraint. This is what makes rebuild idempotent.

**Loading Strategy**

**`raise_on_sql` on all three relationships** — Rule L4. At 580,000 rows a year and dashboard queries spanning days, an accidental `.machine` traversal per row is the third-most-likely N+1 in the platform.

**Read Components**

Monitoring Agent, Supervisor Agent, Decision Agent, Dashboard, Analytics. **Not read by the Prediction Agent** — aggregation destroys the per-cycle variance that carries the degradation signal.

**Write Components**

**Factory Simulator only.** Insert at interval close, with idempotent rebuild permitted.

**Growth**

~1,600 rows/day · ~580,000/year. **The third-largest table.**

**Notes**

*Business* — retention is 2 years, deliberately longer than `cycle_history`'s 90 days, because once cycles are purged this becomes the surviving production record. It is regenerable only while cycles survive; after cycle purge it is a source of truth.

*Implementation* — one of only two operational models whose `updated_at` exists for **rebuild** rather than lifecycle mutation (the other is `DashboardSnapshot`). The distinction matters: a caller must not read `updated_at` as "when the state changed", because nothing about the interval changed — it was recomputed.

---

### O7. CycleHistory

**Purpose**

One machine cycle, with its actual duration and deviation from the pinned standard. The finest-grained production record and the primary carrier of the degradation signal the Prediction Agent learns from.

**Python Class Name**

`CycleHistory`

**Mapped Table**

`cycle_history`

**Logical Group**

`operational`

**Primary Key**

`cycle_history_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `cycle_history_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `production_run_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_run`, `RESTRICT`. **Mandatory** |
| `cycle_number_in_run` | INTEGER | `int` | `Integer` | No | — | |
| `cycle_started_at` | DATETIME | `datetime` | `TimestampTz` | No | — | |
| `cycle_ended_at` | DATETIME | `datetime` | `TimestampTz` | No | — | |
| `cycle_time_seconds` | NUMERIC(8,2) | `Decimal` | `Seconds2` → `Numeric(8,2)` | No | — | Two decimals — sub-second variance is the signal |
| `deviation_from_standard_pct` | NUMERIC(6,2) | `Optional[Decimal]` | `SignedPercent` → `Numeric(6,2)` | Yes | — | Signed, wider than `Percent`. NULL when no standard applies |
| `outcome` | TEXT | `CycleOutcome` | `Enum(CycleOutcome)` + `ck_*_cycle_outcome_allowed` | No | — | |
| `interrupted` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `sequence_number` | INTEGER | `int` | `Integer` | No | — | Monotonic per machine |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one | `Machine` | **`raise_on_sql`** | Rule L4 |
| `production_run` | Many-to-one | `ProductionRun` | **`raise_on_sql`** | Rule L4. Mandatory |
| `shift` | Many-to-one | `Shift` | **`raise_on_sql`** | Rule L4 |

**Back Populates**

**None.** All three unidirectional. `ProductionRun` declines the reverse — ~650 cycles per run.

**Python Types**

`int`, `bool`, `datetime`, `Decimal`, `Optional[Decimal]`, `CycleOutcome`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Numeric`, `Boolean`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `outcome` | `CycleOutcome` | `cycle_outcome` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- Both timestamps timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only (§41.4).
- `cycle_ended_at > cycle_started_at`; `cycle_time_seconds > 0` — **database**.
- Unique on (`machine_id`, `sequence_number`) — database constraint.
- `deviation_from_standard_pct` is computed against `product_line_capability.cycle_time_seconds` at write time — **writing component**, and it is stored rather than derived because the capability row may later be retired.

**Loading Strategy**

**`raise_on_sql` on all three** — Rule L4. The Prediction Agent reads 90 days of cycles per machine, which is roughly 160,000 rows; a lazy `.machine` per row would be the second-worst N+1 in the platform after telemetry.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Analytics. **Not read by the Dashboard**, which uses `production_count`.

**Write Components**

**Factory Simulator only.** Insert only.

**Growth**

~3,500 rows/day · ~1.3 million/year. **The second-largest table.**

**Notes**

*Business* — `deviation_from_standard_pct` uses `NUMERIC(6,2)` rather than `NUMERIC(5,2)` because deviation is signed and can exceed 100 % in both directions. Reusing the `Percent` alias here would silently truncate a badly degraded cycle, which is precisely the case the column exists to record.

*Implementation* — retention is 90 days at full grain, then purged with `production_count` surviving as the aggregate. **Aggregate before purge is mandatory**; purging first loses the resolution permanently. The ORM plays no part in that ordering — it is T-RET-1's, and §22 explains why no cascade exists to interfere with it.

---

### O8. QualityInspectionResult

**Purpose**

An inspection and its verdict, with optional attribution of failures to a specific machine and failure category. The link between a quality problem and the equipment that caused it.

**Python Class Name**

`QualityInspectionResult`

**Mapped Table**

`quality_inspection_result`

**Logical Group**

`operational`

**Primary Key**

`quality_inspection_result_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `quality_inspection_result_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `quality_inspection_result_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `production_run_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_run`, `RESTRICT` |
| `machine_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine`, `RESTRICT`. **Role: where inspected** |
| `attributed_machine_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine`, `RESTRICT`. **Role: blamed** |
| `attributed_failure_category_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `failure_category`, `RESTRICT` |
| `inspected_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `inspection_type` | TEXT | `InspectionType` | `Enum(InspectionType)` + `ck_*_inspection_type_allowed` | No | — | |
| `sample_size` | INTEGER | `int` | `Integer` | No | — | |
| `pass_count` | INTEGER | `int` | `Integer` | No | — | |
| `fail_count` | INTEGER | `int` | `Integer` | No | — | |
| `inspector_worker_id` | INTEGER | `int` | `Integer` | No | — | FK → `worker`, `RESTRICT` |
| `disposition` | TEXT | `InspectionDisposition` | `Enum(InspectionDisposition)` + `ck_*_inspection_disposition_allowed` | No | — | |
| `primary_defect_note` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| `related_operational_event_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `operational_event`, `RESTRICT` |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `production_run` | Many-to-one | `ProductionRun` | `select` | Mandatory |
| `machine` | Many-to-one, optional | `Machine` | `select` | Uses `machine_id` |
| `attributed_machine` | Many-to-one, optional | `Machine` | `select` | Uses `attributed_machine_id` |
| `attributed_failure_category` | Many-to-one, optional | `FailureCategory` | `select` | Unidirectional |
| `inspector` | Many-to-one | `Worker` | `select` | Unidirectional. Named for the role |
| `related_operational_event` | Many-to-one, optional | `OperationalEvent` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `scrap_records` | One-to-many | `ScrapRecord` | `select` | Bounded — a handful per inspection |

**Back Populates**

| This side | Target side |
|---|---|
| `QualityInspectionResult.production_run` | `ProductionRun.quality_inspections` |
| `QualityInspectionResult.scrap_records` | `ScrapRecord.quality_inspection_result` |

The other six are unidirectional.

**Python Types**

`int`, `str`, `datetime`, `Optional[int]`, `Optional[str]`, `InspectionType`, `InspectionDisposition`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `inspection_type` | `InspectionType` | `inspection_type` | No |
| `disposition` | `InspectionDisposition` | `inspection_disposition` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `inspected_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only (§41.4).
- `pass_count + fail_count = sample_size`; `fail_count > 0` required when `disposition` is a reject — **database**, check-constrained.
- `attributed_machine_id` present implies `attributed_failure_category_id` present — **database**.

**Loading Strategy**

Seven many-to-one and one bounded collection, `select`. Quality analysis reads ranges and should `selectinload` `attributed_machine` and `attributed_failure_category`, which are the two columns the analysis is about.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics.

**Write Components**

**Factory Simulator only.** Insert only.

**Growth**

~30 rows/day · ~11,000/year.

**Notes**

*Business* — `machine_id` and `attributed_machine_id` are different questions. Where a defect was *found* is often not where it was *caused*, and conflating them would make every root-cause statistic wrong.

*Implementation* — **two foreign keys to `Machine`, both nullable, both role-named.** Both relationships must state their foreign key or mapper configuration fails with an ambiguity error. This is one of nine such cases (§21) and the first of two where the double reference is from an operational table to a master table.

---
### O9. ScrapRecord

**Purpose**

Material rejected, with the quantity, reason, and optional attribution to the machine and failure category responsible. The record that converts a quality problem into a currency figure via `product.standard_material_cost`.

**Python Class Name**

`ScrapRecord`

**Mapped Table**

`scrap_record`

**Logical Group**

`operational`

**Primary Key**

`scrap_record_id` — `int`, `OperationalPk`. No business key column.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `scrap_record_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `production_run_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_run`, `RESTRICT` |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT`. **Role: where scrapped** |
| `attributed_machine_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine`, `RESTRICT`. **Role: blamed** |
| `attributed_failure_category_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `failure_category`, `RESTRICT` |
| `recorded_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `quantity_units` | NUMERIC(12,2) | `Decimal` | `Quantity` → `Numeric(12,2)` | No | — | |
| `scrap_reason` | TEXT | `ScrapReason` | `Enum(ScrapReason)` + `ck_*_scrap_reason_allowed` | No | — | |
| `quality_inspection_result_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `quality_inspection_result`, `RESTRICT` |
| `related_operational_event_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `operational_event`, `RESTRICT` |
| `recorded_by_worker_id` | INTEGER | `int` | `Integer` | No | — | FK → `worker`, `RESTRICT` |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `notes` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `production_run` | Many-to-one | `ProductionRun` | `select` | Mandatory |
| `machine` | Many-to-one | `Machine` | `select` | Uses `machine_id` |
| `attributed_machine` | Many-to-one, optional | `Machine` | `select` | Uses `attributed_machine_id` |
| `attributed_failure_category` | Many-to-one, optional | `FailureCategory` | `select` | Unidirectional |
| `quality_inspection_result` | Many-to-one, optional | `QualityInspectionResult` | `select` | |
| `related_operational_event` | Many-to-one, optional | `OperationalEvent` | `select` | Unidirectional |
| `recorded_by` | Many-to-one | `Worker` | `select` | Unidirectional. Named for the role |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `inventory_movements` | One-to-many, optional | `InventoryMovement` | `select` | Material consumed by the scrap |

**Back Populates**

| This side | Target side |
|---|---|
| `ScrapRecord.production_run` | `ProductionRun.scrap_records` |
| `ScrapRecord.quality_inspection_result` | `QualityInspectionResult.scrap_records` |
| `ScrapRecord.inventory_movements` | `InventoryMovement.scrap_record` |

The other six are unidirectional.

**Python Types**

`int`, `datetime`, `Decimal`, `Optional[int]`, `Optional[str]`, `ScrapReason`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Text`, `Numeric`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `scrap_reason` | `ScrapReason` | `scrap_reason` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `recorded_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only. **A reversal is a compensating record, never an edit** — the ORM enforces this by rejecting reassignment on a persistent instance (§41.4).
- `quantity_units > 0` — **database**. A negative scrap is a reversal and must be its own row with its own reason.
- `attributed_machine_id` present implies `attributed_failure_category_id` present — **database**.

**Loading Strategy**

Eight many-to-one and one bounded collection, `select`. Scrap cost analysis specifies `selectinload` on `production_run` chained to `product`, which is where `standard_material_cost` lives.

**Read Components**

Monitoring Agent, Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics.

**Write Components**

**Factory Simulator only.** Insert only. A reversal is a compensating record.

**Growth**

~15 rows/day · ~5,500/year.

**Notes**

*Business* — scrap consumes material, which is why `inventory_movement` may point back here. One scrap record can produce several movements when the product's bill of materials has several components.

*Implementation* — nine relationships makes this the widest operational model by relationship count after `AiRecommendation`. Eight are many-to-one and six of those are optional, so most instances have most relationships resolving to `None`. A caller must not assume any optional parent is present.

---

### O10. InventoryMovement

**Purpose**

Every change in stock position, with the resulting quantity on hand carried on the row. The material record, and the only place current stock level can be read.

**Python Class Name**

`InventoryMovement`

**Mapped Table**

`inventory_movement`

**Logical Group**

`operational`

**Primary Key**

`inventory_movement_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `inventory_movement_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `inventory_movement_code` | VARCHAR(22) | `str` | `String(22)` | No | — | Business key, unique |
| `inventory_item_id` | INTEGER | `int` | `Integer` | No | — | FK → `inventory_item`, `RESTRICT` |
| `inventory_location_id` | INTEGER | `int` | `Integer` | No | — | FK → `inventory_location`, `RESTRICT` |
| `movement_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `movement_type` | TEXT | `InventoryMovementType` | `Enum(InventoryMovementType)` + `ck_*_inventory_movement_type_allowed` | No | — | |
| `quantity_delta` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | **Signed.** Negative is issue, positive is receipt |
| `resulting_quantity_on_hand` | NUMERIC(12,4) | `Decimal` | `Measurement` → `Numeric(12,4)` | No | — | Running balance after this movement |
| `production_run_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_run`, `RESTRICT` |
| `maintenance_work_record_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_work_record`, `RESTRICT` |
| `scrap_record_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `scrap_record`, `RESTRICT` |
| `supplier_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `supplier`, `RESTRICT`. Receipts only |
| `recorded_by_worker_id` | INTEGER | `int` | `Integer` | No | — | FK → `worker`, `RESTRICT` |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `reference_note` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `inventory_item` | Many-to-one | `InventoryItem` | `select` | Unidirectional |
| `inventory_location` | Many-to-one | `InventoryLocation` | `select` | Unidirectional |
| `production_run` | Many-to-one, optional | `ProductionRun` | `select` | Unidirectional |
| `maintenance_work_record` | Many-to-one, optional | `MaintenanceWorkRecord` | `select` | |
| `scrap_record` | Many-to-one, optional | `ScrapRecord` | `select` | |
| `supplier` | Many-to-one, optional | `Supplier` | `select` | Unidirectional |
| `recorded_by` | Many-to-one | `Worker` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |

**Back Populates**

| This side | Target side |
|---|---|
| `InventoryMovement.maintenance_work_record` | `MaintenanceWorkRecord.inventory_movements` |
| `InventoryMovement.scrap_record` | `ScrapRecord.inventory_movements` |

The other six are unidirectional.

**Python Types**

`int`, `str`, `datetime`, `Decimal`, `Optional[int]`, `Optional[str]`, `InventoryMovementType`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `Numeric`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `movement_type` | `InventoryMovementType` | `inventory_movement_type` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `movement_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only. **Errors are corrected by an `adjustment` movement, never by editing** (§41.4).
- `quantity_delta != 0`; `resulting_quantity_on_hand >= 0`; sign of `quantity_delta` consistent with `movement_type` — **database**, check-constrained.
- `resulting_quantity_on_hand` equals the previous balance plus `quantity_delta` — **writing component.** It is a temporal rule across rows and must reconcile to physical stock counts.

**Loading Strategy**

Eight many-to-one, `select`. Current stock is `ORDER BY movement_at DESC LIMIT 1` per item and location — never an aggregation over all movements, and never a traversal from `InventoryItem`.

**Read Components**

Monitoring Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read by the Prediction Agent** — stock level is not a machine failure predictor.

**Write Components**

**Factory Simulator only.** Insert only.

**Growth**

~60 rows/day · ~22,000/year.

**Notes**

*Business* — `resulting_quantity_on_hand` is stored rather than derived because deriving stock from a sum of deltas over three years of history is expensive and fragile. Storing the balance makes the current level a single-row read, and the reconciliation obligation is the price.

*Implementation* — three of the eight foreign keys (`production_run_id`, `maintenance_work_record_id`, `scrap_record_id`) are mutually exclusive causes in practice, but the schema does not constrain them to be. The ORM maps all three as independent optional relationships and asserts nothing about their exclusivity.

---

### O11. MaintenanceWorkRecord

**Purpose**

A maintenance job from open to closed: what triggered it, who was assigned, what was actually wrong, how long it took, and whether the line stopped. **The terminus of the retention dependency chain** — its 5-year window transitively pins six other operational tables.

**Python Class Name**

`MaintenanceWorkRecord`

**Mapped Table**

`maintenance_work_record`

**Logical Group**

`operational`

**Primary Key**

`maintenance_work_record_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `maintenance_work_record_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `maintenance_work_record_code` | VARCHAR(14) | `str` | `String(14)` | No | — | Business key, unique |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `work_type` | TEXT | `MaintenanceWorkType` | `Enum(MaintenanceWorkType)` + `ck_*_maintenance_work_type_allowed` | No | — | |
| `machine_maintenance_schedule_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine_maintenance_schedule`, `RESTRICT`. Preventive jobs only |
| `triggering_alert_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `operational_alert`, `RESTRICT` |
| `triggering_recommendation_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `ai_recommendation`, `RESTRICT` |
| `reported_failure_category_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `failure_category`, `RESTRICT`. **Role: reported** |
| `confirmed_failure_category_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `failure_category`, `RESTRICT`. **Role: confirmed** |
| `priority_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT` |
| `assigned_maintenance_team_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_team`, `RESTRICT` |
| `assigned_engineer_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_engineer`, `RESTRICT` |
| `work_status` | TEXT | `MaintenanceWorkStatus` | `Enum(MaintenanceWorkStatus)` + `ck_*_maintenance_work_status_allowed` | No | `'open'` | Lifecycle state |
| `opened_at` | DATETIME | `datetime` | `TimestampTz` | No | — | |
| `assigned_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `started_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `completed_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `closed_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `planned_duration_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `actual_duration_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `machine_downtime_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | Distinct from work duration |
| `did_stop_line` | INTEGER | `bool` | `Boolean` | No | `0` | |
| `resolution_note` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| `shift_id_opened` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT`. Name is the schema's |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one | `Machine` | `select` | Unidirectional |
| `machine_maintenance_schedule` | Many-to-one, optional | `MachineMaintenanceSchedule` | `select` | Unidirectional |
| `triggering_alert` | Many-to-one, optional | `OperationalAlert` | `select` | Unidirectional |
| `triggering_recommendation` | Many-to-one, optional | `AiRecommendation` | `select` | Unidirectional |
| `reported_failure_category` | Many-to-one, optional | `FailureCategory` | `select` | Uses `reported_failure_category_id` |
| `confirmed_failure_category` | Many-to-one, optional | `FailureCategory` | `select` | Uses `confirmed_failure_category_id` |
| `priority_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Unidirectional |
| `assigned_maintenance_team` | Many-to-one, optional | `MaintenanceTeam` | `select` | Unidirectional |
| `assigned_engineer` | Many-to-one, optional | `MaintenanceEngineer` | `select` | Unidirectional |
| `shift_opened` | Many-to-one | `Shift` | `select` | Unidirectional |
| `activities` | One-to-many | `MachineMaintenanceActivity` | `select` | ~4 per record |
| `inventory_movements` | One-to-many, optional | `InventoryMovement` | `select` | Parts consumed |

**Deliberately unmapped.** `machine_state_transition.triggering_work_record_id`, `recommendation_action.resulting_work_record_id` — both are cross-references from elsewhere in the flow, and neither reverse is a structural child collection.

**Back Populates**

| This side | Target side |
|---|---|
| `MaintenanceWorkRecord.activities` | `MachineMaintenanceActivity.maintenance_work_record` |
| `MaintenanceWorkRecord.inventory_movements` | `InventoryMovement.maintenance_work_record` |

The ten many-to-one relationships are unidirectional.

**Python Types**

`int`, `str`, `bool`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[datetime]`, `MaintenanceWorkType`, `MaintenanceWorkStatus`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `Boolean`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `work_type` | `MaintenanceWorkType` | `maintenance_work_type` | No |
| `work_status` | `MaintenanceWorkStatus` | `maintenance_work_status` | No, default `'open'` |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- All five timestamps timezone-aware — **ORM `@validates`**.
- **Mutable model**, but `maintenance_work_record_code`, `machine_id`, `work_type`, and `opened_at` are **immutable after insert** — `@validates` enforces it.
- Timestamp ordering (`opened_at ≤ assigned_at ≤ started_at ≤ completed_at ≤ closed_at`), and each status implying its required timestamps — **database**, check-constrained.
- Legal status transitions — **writing component.**
- `assigned_engineer` must belong to `assigned_maintenance_team` — **writing component.** Cross-table (§41.3).
- Engineer specialization matching the confirmed failure category's `required_specialization` — **writing component**, and it is a direct enum value comparison thanks to the shared `MaintenanceSpecialization` type.

**Loading Strategy**

Ten many-to-one and two bounded collections, `select`. Maintenance history views specify `selectinload` on `machine`, `assigned_engineer`, and `confirmed_failure_category` — the three parents the view actually renders.

**Read Components**

All eight components.

**Write Components**

**Factory Simulator only.** Insert then update through the lifecycle.

**Growth**

under 10 rows/day · ~2,500/year.

**Notes**

*Business* — `machine_downtime_minutes` and `actual_duration_minutes` are different numbers. A four-hour repair on a standby machine causes zero downtime, and reporting the two as one would overstate availability loss.

*Implementation* — **5-year retention makes this the terminus of the retention chain**, transitively pinning `ai_recommendation`, `supervisor_context`, `prediction_result`, `prediction_feature_snapshot`, `operational_alert`, and `operational_event`. That chain is enforced by `RESTRICT` foreign keys, which is why §22's no-cascade rule is load-bearing rather than stylistic: a cascade anywhere in that chain would let a purge silently break the evidence trail.

---

### O12. MachineMaintenanceActivity

**Purpose**

A single step within a maintenance job, timestamped, with the elapsed time since the previous step. The audit trail of what was actually done, in order.

**Python Class Name**

`MachineMaintenanceActivity`

**Mapped Table**

`machine_maintenance_activity`

**Logical Group**

`operational`

**Primary Key**

`machine_maintenance_activity_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `machine_maintenance_activity_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `maintenance_work_record_id` | INTEGER | `int` | `Integer` | No | — | FK → `maintenance_work_record`, `RESTRICT` |
| `activity_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `activity_type` | TEXT | `MaintenanceActivityType` | `Enum(MaintenanceActivityType)` + `ck_*_maintenance_activity_type_allowed` | No | — | |
| `performed_by_worker_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `worker`, `RESTRICT`. NULL for automated steps |
| `duration_from_previous_seconds` | INTEGER | `Optional[int]` | `Integer` | Yes | — | NULL on the first activity |
| `notes` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `maintenance_work_record` | Many-to-one | `MaintenanceWorkRecord` | `select` | Mandatory |
| `performed_by` | Many-to-one, optional | `Worker` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |

**Back Populates**

| This side | Target side |
|---|---|
| `MachineMaintenanceActivity.maintenance_work_record` | `MaintenanceWorkRecord.activities` |

**Python Types**

`int`, `datetime`, `Optional[int]`, `Optional[str]`, `MaintenanceActivityType`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Text`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `activity_type` | `MaintenanceActivityType` | `maintenance_activity_type` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `activity_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only (§41.4).
- `activity_at` within the parent record's open window — **writing component.** Cross-table.

**Loading Strategy**

Three many-to-one, `select`. A job's activity timeline is `MaintenanceWorkRecord.activities` ordered by `activity_at` — the collection is bounded and this is its intended use.

**Read Components**

Supervisor Agent, Decision Agent, Dashboard, Analytics.

**Write Components**

**Factory Simulator only.** Insert only.

**Growth**

under 10 rows/day per active job · ~10,000/year.

**Notes**

*Business* — `duration_from_previous_seconds` being NULL on the first activity is the same pattern as `machine_state_transition.from_state`. A caller treats `None` as "no previous step", never as an error.

*Implementation* — retention is 5 years with the parent work record. There is no ORM cascade to express that; the purge job deletes activities before their parent, in dependency order (§22).

---

### O13. OperationalEvent

**Purpose**

One detected abnormality, with the observed value, the threshold breached, and the rule that fired. **Absolutely immutable** — the explainability contract collapses if evidence can be rewritten.

**Python Class Name**

`OperationalEvent`

**Mapped Table**

`operational_event`

**Logical Group**

`operational`

**Primary Key**

`operational_event_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `operational_event_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `operational_event_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `operational_alert_id` | INTEGER | `int` | `Integer` | No | — | FK → `operational_alert`, `RESTRICT`. **Mandatory** |
| `event_category` | TEXT | `EventCategory` | `Enum(EventCategory)` + `ck_*_event_category_allowed` | No | — | **Shared type** with `OperationalAlert.alert_category` |
| `event_type` | TEXT | `EventType` | `Enum(EventType)` + `ck_*_event_type_allowed` | No | — | |
| `detected_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT` |
| `machine_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine`, `RESTRICT` |
| `production_line_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_line`, `RESTRICT` |
| `production_run_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_run`, `RESTRICT` |
| `inventory_item_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `inventory_item`, `RESTRICT` |
| `machine_parameter_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine_parameter`, `RESTRICT` |
| `alert_threshold_rule_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `alert_threshold_rule`, `RESTRICT`. Lineage |
| `observed_value` | NUMERIC(12,4) | `Optional[Decimal]` | `Measurement` → `Numeric(12,4)` | Yes | — | Evidence, captured not referenced |
| `threshold_value_breached` | NUMERIC(12,4) | `Optional[Decimal]` | `Measurement` → `Numeric(12,4)` | Yes | — | Evidence |
| `threshold_direction` | TEXT | `Optional[ThresholdDirection]` | `Enum(ThresholdDirection)` + `ck_*_threshold_direction_allowed` | Yes | — | |
| `sustained_duration_seconds` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `triggering_reading_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine_sensor_reading`, **`SET NULL`** |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| `detection_note` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `operational_alert` | Many-to-one | `OperationalAlert` | `select` | **Mandatory** |
| `severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Unidirectional |
| `machine` | Many-to-one, optional | `Machine` | `select` | Unidirectional |
| `production_line` | Many-to-one, optional | `ProductionLine` | `select` | Unidirectional |
| `production_run` | Many-to-one, optional | `ProductionRun` | `select` | Unidirectional |
| `inventory_item` | Many-to-one, optional | `InventoryItem` | `select` | Unidirectional |
| `machine_parameter` | Many-to-one, optional | `MachineParameter` | `select` | Unidirectional |
| `alert_threshold_rule` | Many-to-one, optional | `AlertThresholdRule` | `select` | Unidirectional |
| `triggering_reading` | Many-to-one, optional | `MachineSensorReading` | `select` | **`SET NULL` on purge** |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |

**Deliberately unmapped.** `machine_state_transition`, `quality_inspection_result`, `scrap_record` — all three cite an event, and none is a structural child.

**Back Populates**

| This side | Target side |
|---|---|
| `OperationalEvent.operational_alert` | `OperationalAlert.events` |

The other nine are unidirectional.

**Python Types**

`int`, `str`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[Decimal]`, `EventCategory`, `EventType`, `Optional[ThresholdDirection]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `Numeric`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `event_category` | `EventCategory` | `event_category` | No |
| `event_type` | `EventType` | `event_type` | No |
| `threshold_direction` | `ThresholdDirection` | `threshold_direction` | Yes |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

`EventCategory` is shared with `OperationalAlert.alert_category`, and the sharing is functional rather than cosmetic: **correlation depends on the alert's category matching its events' exactly**, which one shared type guarantees and two types could not (§40.2).

**Validation**

- `detected_at` timezone-aware — **ORM `@validates`**.
- **Immutability: every column, with no exception.** This is the strictest immutability in the ORM layer. `@validates` on all mapped attributes rejects any reassignment on a persistent instance, and the schema reinforces it by omitting `updated_at` entirely (§41.4).
- At least one subject reference (`machine_id`, `production_line_id`, `inventory_item_id`) must be present — **database**, check-constrained.
- Threshold-breach events require `observed_value`, `threshold_value_breached`, and `threshold_direction` — **database**.

**Loading Strategy**

Ten many-to-one, `select`. The Supervisor Agent reads an alert with its events and each event's `machine_parameter` and `alert_threshold_rule`, using `selectinload` chains. **Nine of ten relationships are optional**, so every traversal is a `None` check.

**Read Components**

Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read or written by the Simulator** — a strict boundary: the simulator generates reality, never the platform's interpretation of it.

**Write Components**

**Monitoring Agent only.** Insert only. Absolutely immutable.

**Growth**

~40 rows/day · ~15,000/year.

**Notes**

*Business* — `observed_value` and `threshold_value_breached` are **captured on the row**, not resolved through `alert_threshold_rule` at read time. That is what allows a threshold profile to be retuned without changing what a past event says happened, and it is why `alert_threshold_rule_id` is lineage rather than evidence.

*Implementation* — `triggering_reading_id` is **the only `SET NULL` foreign key in the database.** When the 90-day telemetry purge removes the cited reading, the pointer becomes NULL and the event's evidence survives in `observed_value`. A NULL `triggering_reading` on an event older than 90 days is the designed steady state, not an anomaly, and §22 states that the ORM must not treat it as one.

---

### O14. OperationalAlert

**Purpose**

The correlated grouping of related events into one thing a human is asked to care about, with its own lifecycle from open through acknowledged to resolved. What prevents fifty threshold breaches from becoming fifty notifications.

**Python Class Name**

`OperationalAlert`

**Mapped Table**

`operational_alert`

**Logical Group**

`operational`

**Primary Key**

`operational_alert_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `operational_alert_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `operational_alert_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `correlation_key` | VARCHAR(120) | `str` | `String(120)` | No | — | **Partial unique index** while open |
| `alert_category` | TEXT | `EventCategory` | `Enum(EventCategory)` + `ck_*_event_category_allowed` | No | — | **Shared type** with `OperationalEvent.event_category` |
| `machine_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine`, `RESTRICT` |
| `production_line_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_line`, `RESTRICT` |
| `inventory_item_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `inventory_item`, `RESTRICT` |
| `initial_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT`. **Role: initial** |
| `current_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT`. **Role: current** |
| `alert_status` | TEXT | `AlertStatus` | `Enum(AlertStatus)` + `ck_*_alert_status_allowed` | No | `'open'` | Lifecycle state |
| `event_count` | INTEGER | `int` | `Integer` | No | `1` | Maintained counter, not a foreign key |
| `opened_at` | DATETIME | `datetime` | `TimestampTz` | No | — | |
| `first_event_at` | DATETIME | `datetime` | `TimestampTz` | No | — | |
| `last_event_at` | DATETIME | `datetime` | `TimestampTz` | No | — | Advances as events correlate in |
| `acknowledged_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | Set through the Dashboard, written by the Monitoring Agent |
| `acknowledged_by_worker_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `worker`, `RESTRICT` |
| `escalated_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `resolved_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `resolution_type` | TEXT | `Optional[AlertResolutionType]` | `Enum(AlertResolutionType)` + `ck_*_alert_resolution_type_allowed` | Yes | — | |
| `closed_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `suppression_reason` | TEXT | `Optional[AlertSuppressionReason]` | `Enum(AlertSuppressionReason)` + `ck_*_alert_suppression_reason_allowed` | Yes | — | |
| `resolution_note` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one, optional | `Machine` | `select` | Unidirectional |
| `production_line` | Many-to-one, optional | `ProductionLine` | `select` | Unidirectional |
| `inventory_item` | Many-to-one, optional | `InventoryItem` | `select` | Unidirectional |
| `initial_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Uses `initial_severity_level_id` |
| `current_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Uses `current_severity_level_id` |
| `acknowledged_by` | Many-to-one, optional | `Worker` | `select` | Unidirectional |
| `events` | **One-to-many** | `OperationalEvent` | `select` | Bounded by `event_count`, typically under 10 |

**Deliberately unmapped — 5 reverse collections.** `prediction_feature_snapshot`, `prediction_result`, `supervisor_context`, `maintenance_work_record`, `notification`. All five cite the alert as a trigger and none is a structural child.

**Back Populates**

| This side | Target side |
|---|---|
| `OperationalAlert.events` | `OperationalEvent.operational_alert` |

The six many-to-one relationships are unidirectional.

**Python Types**

`int`, `str`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[datetime]`, `EventCategory`, `AlertStatus`, `Optional[AlertResolutionType]`, `Optional[AlertSuppressionReason]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `alert_category` | `EventCategory` | `event_category` | No |
| `alert_status` | `AlertStatus` | `alert_status` | No, default `'open'` |
| `resolution_type` | `AlertResolutionType` | `alert_resolution_type` | Yes |
| `suppression_reason` | `AlertSuppressionReason` | `alert_suppression_reason` | Yes |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

Four enum columns — the joint highest count on any model, with `AiRecommendation` at one and `NotificationDelivery` at four.

**Validation**

- All six timestamps timezone-aware — **ORM `@validates`**.
- **Mutable model**, but `operational_alert_code`, `correlation_key`, `alert_category`, `initial_severity_level_id`, `opened_at`, and `first_event_at` are **immutable after insert** — `@validates` enforces it. `current_severity_level_id`, `last_event_at`, `event_count`, and the lifecycle timestamps are the mutable set.
- Timestamp ordering; each status implying its required timestamps; `resolved` requiring `resolution_type`; `suppressed` requiring `suppression_reason` — **database**, check-constrained.
- **One open alert per correlation key** — partial unique index, `WHERE alert_status = 'open'`. This is the correctness-critical index that makes correlation work; without it, T-MON-1 and T-MON-2 would race and produce duplicate open alerts (§37.9).
- `event_count` reconciliation against `events` — **Monitoring Agent.** It is a maintained counter and the ORM derives nothing from the collection.

**Loading Strategy**

Six many-to-one and one bounded collection, `select`. **`events` is the one operational parent→child collection that is genuinely intended for traversal**, since it is bounded by `event_count` and the Supervisor Agent needs all of it as evidence. Alert dashboards specify `selectinload` on `machine` and `current_severity_level`.

**Read Components**

Prediction Agent, Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read or written by the Simulator.**

**Write Components**

**Monitoring Agent only.** Insert then update. Acknowledgement is captured through the Dashboard but **written by the Monitoring Agent**, so the table retains a single writer and the ownership model holds.

**Growth**

under 10 rows/day · ~2,500/year.

**Notes**

*Business* — `initial_severity_level_id` and `current_severity_level_id` both exist so that an alert escalating from warning to critical keeps a record of where it started. Overwriting one value would erase the escalation history that `escalated_at` claims happened.

*Implementation* — acknowledgement is the schema's one documented write that crosses a surface: a human acts on the Dashboard, and the Monitoring Agent performs the write. The ORM has no mechanism for that and needs none — the Dashboard calls the Monitoring Agent, and the session that commits belongs to the Monitoring Agent's boundary T-MON-3 (§24).

---

### O15. PredictionFeatureSnapshot

**Purpose**

The exact feature vector fed to the model at inference time, with the data-quality figures that say how much the resulting prediction can be trusted. What makes a prediction reproducible after the telemetry behind it is purged.

**Python Class Name**

`PredictionFeatureSnapshot`

**Mapped Table**

`prediction_feature_snapshot`

**Logical Group**

`operational`

**Primary Key**

`prediction_feature_snapshot_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `prediction_feature_snapshot_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `prediction_feature_snapshot_code` | VARCHAR(22) | `str` | `String(22)` | No | — | Business key, unique |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `generated_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `window_from` | DATETIME | `datetime` | `TimestampTz` | No | — | Telemetry window start |
| `window_to` | DATETIME | `datetime` | `TimestampTz` | No | — | Telemetry window end |
| `lookback_window_seconds` | INTEGER | `int` | `Integer` | No | — | |
| `feature_set_version` | VARCHAR(20) | `str` | `String(20)` | No | — | Pins the feature definition |
| `feature_values` | TEXT | `dict[str, Any]` | `JsonDoc` -> `JSON` | No | — | The vector itself. **ORM does not interpret it** |
| `source_reading_count` | INTEGER | `int` | `Integer` | No | — | |
| `excluded_reading_count` | INTEGER | `int` | `Integer` | No | `0` | Readings dropped for quality |
| `data_completeness_pct` | NUMERIC(5,2) | `Decimal` | `Percent` → `Numeric(5,2)` | No | — | Read by the Supervisor Agent |
| `is_sufficient_for_inference` | INTEGER | `bool` | `Boolean` | No | — | **No default** — a judgement the writer makes |
| `insufficiency_reason` | TEXT | `Optional[SnapshotInsufficiencyReason]` | `Enum(SnapshotInsufficiencyReason)` + `ck_*_snapshot_insufficiency_reason_allowed` | Yes | — | Required when insufficient |
| `triggering_alert_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `operational_alert`, `RESTRICT` |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one | `Machine` | `select` | Unidirectional |
| `triggering_alert` | Many-to-one, optional | `OperationalAlert` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `prediction_results` | **One-to-many** | `PredictionResult` | `select` | Typically 1, bounded low |

**Back Populates**

| This side | Target side |
|---|---|
| `PredictionFeatureSnapshot.prediction_results` | `PredictionResult.prediction_feature_snapshot` |

The three many-to-one relationships are unidirectional.

**Python Types**

`int`, `str`, `bool`, `datetime`, `Decimal`, `dict[str, Any]`, `Optional[int]`, `Optional[SnapshotInsufficiencyReason]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Numeric`, `Boolean`, `DateTime`, `TEXT`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `insufficiency_reason` | `SnapshotInsufficiencyReason` | `snapshot_insufficiency_reason` | Yes |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- All three timestamps timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only, and the immutability is what makes a prediction auditable (§41.4).
- `is_sufficient_for_inference = FALSE` requires `insufficiency_reason`; `window_to > window_from`; `data_completeness_pct` between 0 and 100 — **database**, check-constrained.
- **`feature_values` is not schema-validated by the ORM.** Its shape is the Prediction Agent's contract with itself, versioned by `feature_set_version`. Validating it here would put the model's feature definition in the ORM layer, which R4 (§16) forbids and which would need changing on every model revision.

**Loading Strategy**

Three many-to-one and one bounded collection, `select`. `feature_values` is a `TEXT` column loaded with the row; there is no deferred loading on it, because a snapshot is read precisely to obtain it.

**Read Components**

Prediction Agent, Supervisor Agent (reads `data_completeness_pct` to judge how much weight to place on a prediction), Decision Agent, Analytics. **Not read by** the Monitoring Agent, Notification Service, or Dashboard.

**Write Components**

**Prediction Agent only.** Insert only, immutable.

**Growth**

~170 rows/day · ~62,000/year.

**Notes**

*Business* — the 180-day stated retention is deliberately longer than telemetry's 90 days, because a snapshot is regenerable from telemetry only while telemetry survives. After 90 days it is permanently fixed, which is exactly why it exists as a stored row rather than a recomputation.

*Implementation* — one of ten `TEXT` columns in the database and the largest by content. It is mapped as `dict[str, Any]` and treated as opaque. `MutableDict` tracking is **not** applied: the row is immutable, so in-place mutation is a defect the immutability hook catches, not a case to support.

---

### O16. PredictionResult

**Purpose**

One model inference: the failure probability, its confidence band, the predicted category and mode, and the top contributing features. **The only place `failure_probability` exists in the database.**

**Python Class Name**

`PredictionResult`

**Mapped Table**

`prediction_result`

**Logical Group**

`operational`

**Primary Key**

`prediction_result_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `prediction_result_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `prediction_result_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `prediction_feature_snapshot_id` | INTEGER | `int` | `Integer` | No | — | FK → `prediction_feature_snapshot`, `RESTRICT`. **Mandatory** |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `predicted_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `model_name` | VARCHAR(60) | `str` | `String(60)` | No | — | |
| `model_version` | VARCHAR(20) | `str` | `String(20)` | No | — | |
| `failure_probability` | NUMERIC(5,4) | `Decimal` | `Probability` → `Numeric(5,4)` | No | — | 0.0000–1.0000 |
| `risk_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT` |
| `predicted_failure_category_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `failure_category`, `RESTRICT` |
| `machine_type_failure_mode_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine_type_failure_mode`, `RESTRICT` |
| `prediction_horizon_hours` | INTEGER | `int` | `Integer` | No | — | |
| `confidence_band_low` | NUMERIC(5,4) | `Optional[Decimal]` | `Probability` → `Numeric(5,4)` | Yes | — | |
| `confidence_band_high` | NUMERIC(5,4) | `Optional[Decimal]` | `Probability` → `Numeric(5,4)` | Yes | — | |
| `top_contributing_features` | TEXT | `dict[str, Any]` | `JsonDoc` -> `JSON` | No | — | Explainability payload |
| `triggering_alert_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `operational_alert`, `RESTRICT` |
| `inference_duration_ms` | INTEGER | `int` | `Integer` | No | — | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `prediction_feature_snapshot` | Many-to-one | `PredictionFeatureSnapshot` | `select` | **Mandatory** |
| `machine` | Many-to-one | `Machine` | `select` | Unidirectional |
| `risk_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Unidirectional |
| `predicted_failure_category` | Many-to-one, optional | `FailureCategory` | `select` | Unidirectional |
| `machine_type_failure_mode` | Many-to-one, optional | `MachineTypeFailureMode` | `select` | Unidirectional |
| `triggering_alert` | Many-to-one, optional | `OperationalAlert` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |

**Deliberately unmapped.** `supervisor_context.triggering_prediction_id`, `ai_recommendation.prediction_result_id`.

**Back Populates**

| This side | Target side |
|---|---|
| `PredictionResult.prediction_feature_snapshot` | `PredictionFeatureSnapshot.prediction_results` |

The other six are unidirectional.

**Python Types**

`int`, `str`, `datetime`, `Decimal`, `dict[str, Any]`, `Optional[int]`, `Optional[Decimal]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Numeric`, `DateTime`, `TEXT`, `Enum` (mixin only).

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

No enum column of its own. Risk is a foreign key to `failure_severity_level`, not an enum, for the reason §40.6 gives: severity carries behaviour columns.

**Validation**

- `predicted_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only. An editable prediction would make every accuracy measurement meaningless (§41.4).
- `failure_probability` between 0 and 1; `confidence_band_low <= failure_probability <= confidence_band_high` when both present — **database**, check-constrained.
- `machine_id` must match the snapshot's `machine_id` — **writing component.** Cross-table (§41.3).

**Loading Strategy**

Seven many-to-one, `select`. The Supervisor Agent reads a prediction with its snapshot and the machine's failure mode, using `joinedload` on the mandatory parents and `selectinload` where it reads many predictions at once.

**Read Components**

Supervisor Agent, Decision Agent, Notification Service, Dashboard, Analytics. **Not read by the Simulator** — reading this would make every accuracy measurement circular. **Not read by the Monitoring Agent** — detection is independent of prediction, which keeps the two layers separately testable.

**Write Components**

**Prediction Agent only.** Insert only, immutable.

**Growth**

~170 rows/day · ~62,000/year.

**Notes**

*Business* — `NUMERIC(5,4)` on `failure_probability` gives four decimal places, which is the resolution the severity mapping needs. `float` would introduce representation error at exactly the boundary where a probability crosses a severity threshold, which is the one place error is not tolerable.

*Implementation* — **predictions deliberately outlive their snapshots** (2 years stated against 180 days) because accuracy scoring against confirmed failures happens over long periods. The `NOT NULL` foreign key to the snapshot means the `RESTRICT` chain raises the snapshot's effective floor to match — the ORM does nothing to enable that, and §22's no-cascade rule is what keeps it intact.

---
### O17. SupervisorContext

**Purpose**

The assembled evidence package and the escalation decision made on it. The Supervisor Agent's entire output, and the Decision Agent's entire input. **The only table in the database whose retention window depends on a column value.**

**Python Class Name**

`SupervisorContext`

**Mapped Table**

`supervisor_context`

**Logical Group**

`operational`

**Primary Key**

`supervisor_context_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `supervisor_context_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `supervisor_context_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `machine_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine`, `RESTRICT` |
| `production_line_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_line`, `RESTRICT` |
| `assembled_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `triggering_alert_id` | INTEGER | `int` | `Integer` | No | — | FK → `operational_alert`, `RESTRICT`. **Mandatory** |
| `triggering_prediction_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `prediction_result`, `RESTRICT` |
| `related_alert_codes` | TEXT | `Optional[list[Any]]` | `JsonDoc` -> `JSON` | Yes | — | Codes, not foreign keys — see Notes |
| `escalation_decision` | TEXT | `EscalationDecision` | `Enum(EscalationDecision)` + `ck_*_escalation_decision_allowed` | No | — | Drives retention |
| `applied_escalation_rule_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `business_rule`, `RESTRICT`. Lineage |
| `escalation_rationale` | TEXT | `str` | `Text` | No | — | **NOT NULL** — a decision without a reason is not explainable |
| `context_document` | TEXT | `Optional[dict[str, Any]]` | `JsonDoc` -> `JSON` | Yes | — | The Decision Agent's entire input |
| `context_assembly_duration_ms` | INTEGER | `int` | `Integer` | No | — | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `machine` | Many-to-one, optional | `Machine` | `select` | Unidirectional |
| `production_line` | Many-to-one, optional | `ProductionLine` | `select` | Unidirectional |
| `triggering_alert` | Many-to-one | `OperationalAlert` | `select` | **Mandatory**, unidirectional |
| `triggering_prediction` | Many-to-one, optional | `PredictionResult` | `select` | Unidirectional |
| `applied_escalation_rule` | Many-to-one, optional | `BusinessRule` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `ai_recommendation` | **One-to-one**, optional | `AiRecommendation` | `select`, `uselist=False` | Present only for escalated contexts |

**Back Populates**

| This side | Target side |
|---|---|
| `SupervisorContext.ai_recommendation` | `AiRecommendation.supervisor_context` |

The six many-to-one relationships are unidirectional.

**Python Types**

`int`, `str`, `datetime`, `Optional[int]`, `Optional[dict[str, Any]]`, `Optional[list[Any]]`, `EscalationDecision`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `DateTime`, `TEXT`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `escalation_decision` | `EscalationDecision` | `escalation_decision` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `assembled_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only (§41.4).
- Escalated decisions require `context_document`; `escalation_rationale` non-blank — **database**, check-constrained.
- **`related_alert_codes` is not referentially validated.** It holds alert *codes*, deliberately not foreign keys, so a correlated alert can be cited without pinning it against purge. The ORM maps it as an opaque list and resolves nothing.

**Loading Strategy**

Six many-to-one and one scalar one-to-one, `select`. The Decision Agent reads one context and consumes `context_document` directly; it does not traverse the relationships to rebuild what the document already contains. That is the point of the document existing.

**Read Components**

Decision Agent (consumes `context_document` as its entire input), Dashboard, Analytics. **Not read by** the Simulator, Monitoring Agent, or Prediction Agent.

**Write Components**

**Supervisor Agent only.** Insert only, immutable. **It never writes a recommendation** — orchestration and reasoning stay separate.

**Growth**

~25 rows/day · ~9,000/year, of which roughly 8 % are escalations.

**Notes**

*Business* — retention splits by `escalation_decision`: escalated contexts 2 years with an effective 5-year floor via the maintenance chain, **suppressed contexts 180 days**. This is the only value-dependent retention window in the database, and the purge job must branch on the column.

*Implementation* — `ai_recommendation` is a **one-to-one that is `None` for roughly 92 % of rows**, because most contexts are suppressed. That is the normal case, not an exception, and a caller that treats a missing recommendation as an error will fail on nearly every row it reads.

---

### O18. AiRecommendation

**Purpose**

The platform's advice: root cause, supporting evidence, business impact, recommended action, recovery plan, and reasoning narrative. The Decision Agent's only output, and **the single most valuable row type in the database**.

**Python Class Name**

`AiRecommendation`

**Mapped Table**

`ai_recommendation`

**Logical Group**

`operational`

**Primary Key**

`ai_recommendation_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `ai_recommendation_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `ai_recommendation_code` | VARCHAR(20) | `str` | `String(20)` | No | — | Business key, unique |
| `supervisor_context_id` | INTEGER | `int` | `Integer` | No | — | FK → `supervisor_context`, `RESTRICT`. **Unique — one-to-one** |
| `prediction_result_id` | INTEGER | `int` | `Integer` | No | — | FK → `prediction_result`, `RESTRICT`. **Mandatory** |
| `machine_id` | INTEGER | `int` | `Integer` | No | — | FK → `machine`, `RESTRICT` |
| `production_line_id` | INTEGER | `int` | `Integer` | No | — | FK → `production_line`, `RESTRICT` |
| `production_run_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_run`, `RESTRICT` |
| `generated_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `llm_model_name` | VARCHAR(60) | `str` | `String(60)` | No | — | |
| `llm_model_version` | VARCHAR(40) | `str` | `String(40)` | No | — | |
| `priority_severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT` |
| `root_cause_failure_category_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_category`, `RESTRICT`. **Mandatory** |
| `root_cause_confidence` | TEXT | `RootCauseConfidence` | `Enum(RootCauseConfidence)` + `ck_*_root_cause_confidence_allowed` | No | — | |
| `supporting_evidence` | TEXT | `dict[str, Any]` | `JsonDoc` -> `JSON` | No | — | **Contract element 1** |
| `business_impact` | TEXT | `dict[str, Any]` | `JsonDoc` -> `JSON` | No | — | **Contract element 4** |
| `recommended_action` | TEXT | `str` | `Text` | No | — | **Contract element 5** |
| `recovery_plan` | TEXT | `str` | `Text` | No | — | |
| `suggested_maintenance_team_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_team`, `RESTRICT` |
| `suggested_engineer_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_engineer`, `RESTRICT` |
| `required_inventory_item_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `inventory_item`, `RESTRICT` |
| `estimated_downtime_minutes` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `recommended_action_by` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | Deadline |
| `reasoning_narrative` | TEXT | `str` | `Text` | No | — | The explanation |
| `contract_complete` | INTEGER | `bool` | `Boolean` | No | — | **No default** — the agent's own assertion |
| `generation_duration_ms` | INTEGER | `int` | `Integer` | No | — | |
| `prompt_token_count` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `completion_token_count` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**There is no `failure_probability` column on this model, and its absence is deliberate.** The explainability contract requires the ML confidence to be *cited* from `prediction_result`, not restated here where it could drift. The mandatory `prediction_result_id` is what makes the citation possible, and the missing column is the structural enforcement.

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `supervisor_context` | **One-to-one** | `SupervisorContext` | `select` | Mandatory, unique |
| `prediction_result` | Many-to-one | `PredictionResult` | `select` | **Mandatory.** Carries the cited probability |
| `machine` | Many-to-one | `Machine` | `select` | Unidirectional |
| `production_line` | Many-to-one | `ProductionLine` | `select` | Unidirectional |
| `production_run` | Many-to-one, optional | `ProductionRun` | `select` | Unidirectional |
| `priority_severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Unidirectional |
| `root_cause_failure_category` | Many-to-one | `FailureCategory` | `select` | Unidirectional |
| `suggested_maintenance_team` | Many-to-one, optional | `MaintenanceTeam` | `select` | Unidirectional |
| `suggested_engineer` | Many-to-one, optional | `MaintenanceEngineer` | `select` | Unidirectional |
| `required_inventory_item` | Many-to-one, optional | `InventoryItem` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `actions` | One-to-many | `RecommendationAction` | `select` | Human responses. Typically 1 |
| `notifications` | One-to-many | `Notification` | `select` | Bounded by recipient count |

**Deliberately unmapped.** `maintenance_work_record.triggering_recommendation_id`.

**Back Populates**

| This side | Target side |
|---|---|
| `AiRecommendation.supervisor_context` | `SupervisorContext.ai_recommendation` |
| `AiRecommendation.actions` | `RecommendationAction.ai_recommendation` |
| `AiRecommendation.notifications` | `Notification.ai_recommendation` |

The ten remaining many-to-one relationships are unidirectional.

**Python Types**

`int`, `str`, `bool`, `datetime`, `dict[str, Any]`, `Optional[int]`, `Optional[datetime]`, `RootCauseConfidence`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `Boolean`, `DateTime`, `TEXT`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `root_cause_confidence` | `RootCauseConfidence` | `root_cause_confidence` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `generated_at`, `recommended_action_by` timezone-aware — **ORM `@validates`**.
- **Immutability: every column.** The human response is a separate table precisely so the recommendation stays untouched. An editable recommendation could be quietly improved after the fact, which would destroy the audit trail entirely (§41.4).
- `contract_complete = TRUE` requires all contract elements non-blank; `recommended_action` and `reasoning_narrative` non-blank; `required_inventory_item_id` required when the root cause category has `requires_spare_part` — the first two are **database** checks, the third is **cross-table** and belongs to the Decision Agent (§41.3).
- **`supporting_evidence` and `business_impact` shapes are not ORM-validated.** They are the Decision Agent's contract, and putting their schema in the ORM would place prompt-output structure in the persistence layer.

**Loading Strategy**

Eleven many-to-one and two collections, `select`. **This is the widest model in the schema by relationship count**, and the Dashboard's recommendation detail view is the query that needs most of them. It specifies a `joinedload` set on the mandatory master parents and `selectinload` on `actions` and `notifications`.

**Read Components**

Notification Service, Dashboard, Analytics, and the Decision Agent itself as precedent. **Not read by the Simulator** — the simulator must never see recommendations about the factory it generates.

**Write Components**

**Decision Agent only.** Insert only, immutable.

**Growth**

~2 rows/day · ~700/year. **One of the smallest tables and the most valuable.**

**Notes**

*Business* — the five explainability contract elements are all present and all `NOT NULL`: supporting evidence, ML confidence (cited via `prediction_result_id`), root cause (`root_cause_failure_category_id` plus `root_cause_confidence`), business impact, and recommended action. `contract_complete` is the agent asserting it satisfied its own contract, and it has no default because that assertion cannot be a default.

*Implementation* — three structural facts converge here. The **absent** `failure_probability` column enforces citation over restatement. The mandatory one-to-one to `supervisor_context` means a recommendation cannot exist without the evidence package that produced it. And **every table it cites is protected from purge by the `RESTRICT` chain**, so the evidence chain never breaks — which is why §22 forbids cascades even where they would look convenient.

---

### O19. RecommendationAction

**Purpose**

The human verdict on a recommendation: accepted, modified, rejected, or deferred, with the response time. **Written by the Dashboard, not the Decision Agent**, because ownership follows the actor.

**Python Class Name**

`RecommendationAction`

**Mapped Table**

`recommendation_action`

**Logical Group**

`operational`

**Primary Key**

`recommendation_action_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `recommendation_action_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `ai_recommendation_id` | INTEGER | `int` | `Integer` | No | — | FK → `ai_recommendation`, `RESTRICT` |
| `action_taken` | TEXT | `RecommendationActionType` | `Enum(RecommendationActionType)` + `ck_*_recommendation_action_type_allowed` | No | — | |
| `actioned_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `actioned_by_worker_id` | INTEGER | `int` | `Integer` | No | — | FK → `worker`, `RESTRICT` |
| `response_time_minutes` | INTEGER | `int` | `Integer` | No | — | From recommendation to action |
| `modification_note` | TEXT | `Optional[str]` | `Text` | Yes | — | Required when modified |
| `rejection_reason` | TEXT | `Optional[RejectionReason]` | `Enum(RejectionReason)` + `ck_*_rejection_reason_allowed` | Yes | — | Required when rejected |
| `rejection_note` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| `deferred_until` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | Required when deferred |
| `resulting_work_record_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `maintenance_work_record`, `RESTRICT` |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `ai_recommendation` | Many-to-one | `AiRecommendation` | `select` | Mandatory |
| `actioned_by` | Many-to-one | `Worker` | `select` | Unidirectional |
| `resulting_work_record` | Many-to-one, optional | `MaintenanceWorkRecord` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |

**Back Populates**

| This side | Target side |
|---|---|
| `RecommendationAction.ai_recommendation` | `AiRecommendation.actions` |

**Python Types**

`int`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[datetime]`, `RecommendationActionType`, `Optional[RejectionReason]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Text`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `action_taken` | `RecommendationActionType` | `recommendation_action_type` | No |
| `rejection_reason` | `RejectionReason` | `rejection_reason` | Yes |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `actioned_at`, `deferred_until` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only, and this is the case where immutability is most visible in behaviour: **a change of mind is a new action row**, so the decision sequence stays visible (§41.4).
- `modified` requires `modification_note`; `rejected` requires `rejection_reason`; `deferred` requires `deferred_until` — **database**, check-constrained.
- `actioned_by` must hold a role with the relevant authority flag — **writing component.** It reads `worker_role`, so it is cross-table (§41.3).

**Loading Strategy**

Four many-to-one, `select`. The Dashboard's recommendation view reads actions through `AiRecommendation.actions` with `selectinload` on `actioned_by`.

**Read Components**

Supervisor Agent, Decision Agent, Notification Service (stops escalating once an action is recorded), Dashboard, Analytics.

**Write Components**

**Dashboard only**, the surface where a human records a decision. Insert only, immutable.

**Growth**

~2 rows/day · ~700/year.

**Notes**

*Business* — this is one of the schema's two documented ownership exceptions. The row records a *human* verdict on the platform's advice; assigning it to the Decision Agent would mean the component producing advice also records the verdict on its own advice, which is the exact conflict human-in-the-loop exists to avoid.

*Implementation* — `resulting_work_record_id` closes the loop from advice to action to work performed, and it is nullable because an accepted recommendation may be actioned without opening a formal job. The reverse is unmapped on `MaintenanceWorkRecord` because it is a cross-reference, not a child collection.

---

### O20. Notification

**Purpose**

A composed message for one recipient, with its suppression state and acknowledgement requirement. Composed once; delivered separately, possibly several times.

**Python Class Name**

`Notification`

**Mapped Table**

`notification`

**Logical Group**

`operational`

**Primary Key**

`notification_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `notification_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `notification_code` | VARCHAR(22) | `str` | `String(22)` | No | — | Business key, unique |
| `notification_recipient_id` | INTEGER | `int` | `Integer` | No | — | FK → `notification_recipient`, `RESTRICT` |
| `notification_type` | TEXT | `NotificationType` | `Enum(NotificationType)` + `ck_*_notification_type_allowed` | No | — | |
| `ai_recommendation_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `ai_recommendation`, `RESTRICT` |
| `operational_alert_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `operational_alert`, `RESTRICT` |
| `severity_level_id` | INTEGER | `int` | `Integer` | No | — | FK → `failure_severity_level`, `RESTRICT` |
| `composed_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `subject` | VARCHAR(200) | `str` | `String(200)` | No | — | |
| `body_text` | TEXT | `str` | `Text` | No | — | |
| `is_suppressed` | INTEGER | `bool` | `Boolean` | No | `0` | Composed but not sent |
| `suppression_reason` | TEXT | `Optional[NotificationSuppressionReason]` | `Enum(NotificationSuppressionReason)` + `ck_*_notification_suppression_reason_allowed` | Yes | — | Required when suppressed |
| `requires_acknowledgement` | INTEGER | `bool` | `Boolean` | No | — | **No default** — derived from severity at compose time |
| `acknowledgement_deadline_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `escalation_order_applied` | INTEGER | `int` | `Integer` | No | — | Which escalation tier fired |
| `shift_id` | INTEGER | `int` | `Integer` | No | — | FK → `shift`, `RESTRICT` |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `notification_recipient` | Many-to-one | `NotificationRecipient` | `select` | Unidirectional |
| `ai_recommendation` | Many-to-one, optional | `AiRecommendation` | `select` | |
| `operational_alert` | Many-to-one, optional | `OperationalAlert` | `select` | Unidirectional |
| `severity_level` | Many-to-one | `FailureSeverityLevel` | `select` | Unidirectional |
| `shift` | Many-to-one | `Shift` | `select` | Unidirectional |
| `deliveries` | **One-to-many** | `NotificationDelivery` | `select` | 1–3 per notification |

**Back Populates**

| This side | Target side |
|---|---|
| `Notification.ai_recommendation` | `AiRecommendation.notifications` |
| `Notification.deliveries` | `NotificationDelivery.notification` |

The four remaining many-to-one relationships are unidirectional.

**Python Types**

`int`, `str`, `bool`, `datetime`, `Optional[int]`, `Optional[datetime]`, `NotificationType`, `Optional[NotificationSuppressionReason]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `Boolean`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `notification_type` | `NotificationType` | `notification_type` | No |
| `suppression_reason` | `NotificationSuppressionReason` | `notification_suppression_reason` | Yes |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

`NotificationSuppressionReason` is a **distinct type** from `AlertSuppressionReason` on `OperationalAlert`. The two vocabularies differ, and the frozen schema keeps them separate — the ORM must not merge them into one class on the grounds that both are "suppression reasons".

**Validation**

- `composed_at`, `acknowledgement_deadline_at` timezone-aware — **ORM `@validates`**.
- **Immutability: all columns.** Append-only. Delivery state lives on the child, which is why this model needs no `updated_at` (§41.4).
- `is_suppressed = TRUE` requires `suppression_reason`; `requires_acknowledgement = TRUE` requires `acknowledgement_deadline_at`; at least one of `ai_recommendation_id` and `operational_alert_id` present — **database**, check-constrained.
- Recipient's `min_severity_level` must be satisfied — **Notification Service.** Cross-table (§41.3).

**Loading Strategy**

Five many-to-one and one bounded collection, `select`. `deliveries` is one of the few collections intended for traversal, since it is 1–3 rows and delivery status is what a caller reading a notification wants.

**Read Components**

Notification Service, Dashboard, Analytics.

**Write Components**

**Notification Service only.** Insert only, immutable.

**Growth**

under 10 rows/day · ~2,500/year.

**Notes**

*Business* — a suppressed notification is **composed and stored, not discarded**. That is what makes suppression auditable: the platform can show what it decided not to send and why.

*Implementation* — the split between this model and `NotificationDelivery` is why this one is append-only and that one is mutable. Composition happens once; delivery is attempted, retried, and asynchronously confirmed. Merging them would force `updated_at` onto the message content.

---

### O21. NotificationDelivery

**Purpose**

One delivery attempt on one channel, with its outcome, provider reference, and latency. The only operational model with **zero master foreign keys**.

**Python Class Name**

`NotificationDelivery`

**Mapped Table**

`notification_delivery`

**Logical Group**

`operational`

**Primary Key**

`notification_delivery_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `notification_delivery_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `notification_id` | INTEGER | `int` | `Integer` | No | — | FK → `notification`, `RESTRICT`. **The only foreign key** |
| `channel` | TEXT | `DeliveryChannel` | `Enum(DeliveryChannel)` + `ck_*_delivery_channel_allowed` | No | — | |
| `attempt_number` | INTEGER | `int` | `Integer` | No | `1` | |
| `attempted_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `delivery_status` | TEXT | `DeliveryStatus` | `Enum(DeliveryStatus)` + `ck_*_delivery_status_allowed` | No | — | **The one mutable column** |
| `delivered_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | Set on async confirmation |
| `provider_reference` | VARCHAR(120) | `Optional[str]` | `String(120)` | Yes | — | External message id |
| `failure_reason` | TEXT | `Optional[DeliveryFailureReason]` | `Enum(DeliveryFailureReason)` + `ck_*_delivery_failure_reason_allowed` | Yes | — | Required on failure |
| `failure_detail` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| `latency_ms` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `notification` | Many-to-one | `Notification` | `select` | Mandatory. **The only relationship** |

**Back Populates**

| This side | Target side |
|---|---|
| `NotificationDelivery.notification` | `Notification.deliveries` |

**Python Types**

`int`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[datetime]`, `DeliveryChannel`, `DeliveryStatus`, `Optional[DeliveryFailureReason]`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `DateTime`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `channel` | `DeliveryChannel` | `delivery_channel` | No |
| `delivery_status` | `DeliveryStatus` | `delivery_status` | No |
| `failure_reason` | `DeliveryFailureReason` | `delivery_failure_reason` | Yes |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `attempted_at`, `delivered_at` timezone-aware — **ORM `@validates`**.
- **Mutable, but narrowly.** Every column except `delivery_status`, `delivered_at`, and `latency_ms` is **immutable after insert** — `@validates` enforces it. The permitted mutation is exactly one transition: `sent` → `delivered` when the provider confirms asynchronously. **This is the single documented mutation in the notification group**, and it is why this model carries `updated_at` while `Notification` does not.
- Failed status requires `failure_reason`; delivered status requires `delivered_at` — **database**, check-constrained.
- Unique on (`notification_id`, `channel`, `attempt_number`) — database constraint.

**Loading Strategy**

One many-to-one, `select`. Read almost exclusively through `Notification.deliveries`.

**Read Components**

Notification Service, Dashboard, Analytics.

**Write Components**

**Notification Service only.** Insert per attempt, and `UPDATE` **only** to advance `delivery_status` from `sent` to `delivered`.

**Growth**

under 20 rows/day · ~5,000/year.

**Notes**

*Business* — retention is 180 days, **the shortest in the database**. Delivery mechanics have no long-term audit value once the notification itself is retained.

*Implementation* — the only operational model with **zero master foreign keys** and exactly one relationship. It is also the strongest `delete-orphan` candidate in the schema and deliberately does not use it (§22): the foreign key is `RESTRICT`, deletion is the purge job's, and an ORM-side cascade would diverge from the job's ordering.

---

### O22. DashboardSnapshot

**Purpose**

A precomputed presentation aggregate at plant, line, or machine scope. Exists so the dashboard is not the reason the schema needs an aggregate table, and so agents never read a presentation artefact.

**Python Class Name**

`DashboardSnapshot`

**Mapped Table**

`dashboard_snapshot`

**Logical Group**

`operational`

**Primary Key**

`dashboard_snapshot_id` — `int`, `OperationalPk`.

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `dashboard_snapshot_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `snapshot_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `snapshot_scope` | TEXT | `SnapshotScope` | `Enum(SnapshotScope)` + `ck_*_snapshot_scope_allowed` | No | — | Plant, line, or machine |
| `production_line_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `production_line`, `RESTRICT`. NULL at plant scope |
| `machine_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `machine`, `RESTRICT`. NULL above machine scope |
| `snapshot_document` | TEXT | `dict[str, Any]` | `JsonDoc` -> `JSON` | No | — | The whole aggregate. **ORM does not interpret it** |
| `computed_from_window_seconds` | INTEGER | `int` | `Integer` | No | — | |
| `generation_duration_ms` | INTEGER | `int` | `Integer` | No | — | |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** (rebuild) |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `production_line` | Many-to-one, optional | `ProductionLine` | `select` | Unidirectional |
| `machine` | Many-to-one, optional | `Machine` | `select` | Unidirectional |

**Back Populates**

**None.** Both relationships are unidirectional.

**Python Types**

`int`, `datetime`, `dict[str, Any]`, `Optional[int]`, `SnapshotScope`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `DateTime`, `TEXT`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `snapshot_scope` | `SnapshotScope` | `snapshot_scope` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- `snapshot_at` timezone-aware — **ORM `@validates`**.
- **Mutable by idempotent rebuild only.** `snapshot_at`, `snapshot_scope`, `production_line_id`, and `machine_id` are immutable after insert — they are the row's natural identity. The document and timing columns may be recomputed.
- Scope consistency — plant scope requires both foreign keys NULL, line scope requires `production_line_id` and NULL `machine_id`, machine scope requires `machine_id` — **database**, check-constrained. **One unique expression index**, `uq_ds_scope_subject_time`, declared over `COALESCE(production_line_id, -1)` and `COALESCE(machine_id, -1)`, enforces one snapshot per scope and subject per instant. The `COALESCE` is load-bearing: SQLite treats every NULL in a unique index as distinct, so a plain index would constrain nothing for plant-scoped rows (§37.9).
- **`snapshot_document` shape is not ORM-validated.** It is the Dashboard's contract with its own front end.

**Loading Strategy**

Two optional many-to-one, `select`. The document is loaded with the row; no deferred loading, because the document is the reason the row is read.

**Read Components**

Dashboard, Analytics. **Read by no agent** — agents read primary tables, and a presentation aggregate in a reasoning path would be an unnecessary and potentially stale dependency.

**Write Components**

**Dashboard only.** Insert on interval, with idempotent rebuild permitted.

**Growth**

~288 rows/day at plant scope alone · ~105,000/year across all scopes.

**Notes**

*Business* — **fully disposable** with no reconciliation obligation. Everything it holds is derivable from primary tables, which is what makes 90-day retention followed by downsampling and purge safe.

*Implementation* — the second of two models whose `updated_at` reflects rebuild rather than state change (the other is `ProductionCount`). Its unique index must be declared over **`COALESCE` expressions** rather than over the bare columns, because plant-scoped snapshots carry NULL in both subject columns and SQLite treats every NULL in a unique index as distinct from every other. A plain index would therefore place no constraint at all on exactly the rows the idempotency rule is about. §37.9 gives the declaration; a lookup that does not use the same `COALESCE` form will not use the index.

---

### O23. AuditLog

**Purpose**

Every configuration change and human action across the platform, append-only. The record of what the platform and its operators did, as distinct from what the factory did.

**Python Class Name**

`AuditLog`

**Mapped Table**

`audit_log`

**Logical Group**

**`system`**

**Primary Key**

`audit_log_id` — `int`, `OperationalPk` (`Integer`).

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `audit_log_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `occurred_at` | DATETIME | `datetime` | `TimestampTz` | No | — | **Event time** |
| `component` | TEXT | `PlatformComponent` | `Enum(PlatformComponent)` + `ck_*_platform_component_allowed` | No | — | Which component acted |
| `action_type` | TEXT | `AuditActionType` | `Enum(AuditActionType)` + `ck_*_audit_action_type_allowed` | No | — | |
| `entity_name` | VARCHAR(60) | `Optional[str]` | `String(60)` | Yes | — | **Soft reference**, not a foreign key |
| `entity_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | **Soft reference.** See Notes |
| `entity_code` | VARCHAR(32) | `Optional[str]` | `String(32)` | Yes | — | Human-readable soft reference |
| `actor_worker_id` | INTEGER | `Optional[int]` | `Integer` | Yes | — | FK → `worker`, `RESTRICT`. **The only foreign key** |
| `correlation_id` | VARCHAR(40) | `str` | `String(40)` | No | — | Ties entries across components |
| `outcome` | TEXT | `AuditOutcome` | `Enum(AuditOutcome)` + `ck_*_audit_outcome_allowed` | No | — | |
| `action_detail` | TEXT | `Optional[dict[str, Any]]` | `JsonDoc` -> `JSON` | Yes | — | Before and after values |
| `error_message` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`. **No `updated_at`** |

**Relationships**

| Attribute | Cardinality | Target | Loading | Notes |
|---|---|---|---|---|
| `actor` | Many-to-one, optional | `Worker` | **`raise_on_sql`** | Rule L4. Named for the role |

**Deliberately unmapped.** `Worker.audit_log_entries` — ~730,000 rows a year against ~13 worker rows.

**Back Populates**

**None.** Unidirectional.

**Python Types**

`int`, `str`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[dict[str, Any]]`, `PlatformComponent`, `AuditActionType`, `AuditOutcome`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `String`, `Text`, `DateTime`, `TEXT`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `component` | `PlatformComponent` | `platform_component` | No |
| `action_type` | `AuditActionType` | `audit_action_type` | No |
| `outcome` | `AuditOutcome` | `audit_outcome` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**`PlatformComponent` binds twice on this model** — as `component` (which component acted) and as `created_by_component` (which component wrote the row). They are usually the same value and are not the same fact, and the schema keeps both.

**Validation**

- `occurred_at` timezone-aware — **ORM `@validates`**.
- **Immutability: every column.** Strictly append-only. **Enforced by the ORM hook and by convention, not by the engine** — SQLite has no privilege system, so there is no way to withhold `UPDATE` and `DELETE` from a writer that can open the file. This is the one model where that limitation is worth restating in its own entry, because the value of an audit trail depends entirely on it not having been edited. §41.4 sets out what compensates: filesystem permissions on the file, `created_by_component` for attribution, and periodic backup for detection.
- Failed outcome requires `error_message`; `entity_id` present requires `entity_name` — **database**, check-constrained.
- **`entity_id` is deliberately not a foreign key** and the ORM declares no relationship for it. It may reference any table in any schema, so no single constraint could cover it. A caller resolving it must dispatch on `entity_name`, and that dispatch is the caller's.

**Loading Strategy**

One many-to-one with **`raise_on_sql`** — Rule L4. At 730,000 rows a year, an audit trail view iterating rows and touching `.actor` is a straightforward N+1. `actor_worker_id` is NULL for system-initiated entries, which is most of them.

**Read Components**

Platform diagnostics, Dashboard, Analytics. **No agent reads it** — an agent reading the audit trail would be reasoning about the platform rather than the factory, and that boundary is deliberate.

**Write Components**

**Platform audit interface**, on behalf of every component. All eight emit entries through **one shared write path** rather than writing the table directly, which preserves single ownership in the sense that matters: one code path, one schema authority, one format.

**Growth**

~2,000 rows/day · ~730,000/year. **The fourth-largest table.**

**Notes**

*Business* — retention is 1 year online then archived **indefinitely and never deleted**. Configuration changes and human actions have permanent audit value.

*Implementation* — the soft reference is the schema's one deliberate departure from referential integrity, and it is safe because the table is append-only, every row carries `component` so provenance is explicit, and there is no state to corrupt. The ORM must not "improve" it by adding a polymorphic relationship — that would require a discriminator-to-model registry the schema does not define.

---

### O24. SystemHealthStatus

**Purpose**

Each platform component's liveness, lag, and backlog. One row per component, updated on heartbeat. Read by the Supervisor Agent to suppress escalation when the Prediction Agent is failed or badly lagging.

**Python Class Name**

`SystemHealthStatus`

**Mapped Table**

`system_health_status`

**Logical Group**

**`system`**

**Primary Key**

`system_health_status_id` — `int`, `OperationalPk` (`Integer`).

**Columns**

| Attribute | SQLite Type | Python Type | SQLAlchemy Type | Nullable | Default | Notes |
|---|---|---|---|---|---|---|
| `system_health_status_id` | INTEGER | `int` | `OperationalPk` | No | autoincrement | Primary key |
| `component` | TEXT | `PlatformComponent` | `Enum(PlatformComponent)` + `ck_*_platform_component_allowed` | No | — | **Unique** — one row per component |
| `status` | TEXT | `ComponentHealthStatus` | `Enum(ComponentHealthStatus)` + `ck_*_component_health_status_allowed` | No | — | |
| `last_heartbeat_at` | DATETIME | `datetime` | `TimestampTz` | No | — | Liveness |
| `last_successful_run_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `consecutive_failure_count` | INTEGER | `int` | `Integer` | No | `0` | |
| `processing_lag_seconds` | INTEGER | `Optional[int]` | `Integer` | Yes | — | Read by the Supervisor Agent |
| `pending_backlog_count` | INTEGER | `Optional[int]` | `Integer` | Yes | — | |
| `last_error_at` | DATETIME | `Optional[datetime]` | `TimestampTz` | Yes | — | |
| `last_error_message` | TEXT | `Optional[str]` | `Text` | Yes | — | |
| `metrics_document` | TEXT | `Optional[dict[str, Any]]` | `JsonDoc` -> `JSON` | Yes | — | Component-specific metrics |
| *mixins* | — | — | — | — | — | `created_at`, `created_by_component`, **`updated_at`** |

**Relationships**

**None.** Zero foreign keys, zero relationships. Fully standalone.

**Back Populates**

None.

**Python Types**

`int`, `datetime`, `Optional[int]`, `Optional[str]`, `Optional[datetime]`, `Optional[dict[str, Any]]`, `PlatformComponent`, `ComponentHealthStatus`; from mixins `datetime`, `PlatformComponent`.

**SQLAlchemy Types**

`Integer`, `Integer`, `Text`, `DateTime`, `TEXT`, `Enum`.

**Enum Usage**

| Attribute | Enum class | Vocabulary | Nullable |
|---|---|---|---|
| `component` | `PlatformComponent` | `platform_component` | No, unique |
| `status` | `ComponentHealthStatus` | `component_health_status` | No |
| *mixin* `created_by_component` | `PlatformComponent` | `platform_component` | No |

**Validation**

- All four timestamps timezone-aware — **ORM `@validates`**.
- **Mutable model**, but `component` is **immutable after insert** — it is the row's identity and carries the unique constraint. `@validates` enforces it.
- Failed status requires `last_error_at` and `last_error_message` — **database**, check-constrained.
- Heartbeat staleness thresholds — **reading component.** Whether a 90-second-old heartbeat means degraded depends on the component, and that judgement is not the ORM's.

**Loading Strategy**

No relationships. The Supervisor Agent reads all 8 rows in one query per cycle.

**Read Components**

Platform monitoring, Dashboard, Analytics. **Optionally read by the Supervisor Agent** to suppress escalation when the Prediction Agent is failed or badly lagging — **escalating on stale predictions is worse than not escalating**, because it produces confident recommendations from outdated evidence.

**Write Components**

**Platform only.** Each component reports its own status through the shared interface. Insert once per component, then update on heartbeat.

**Growth**

**~8 rows, fixed.** Zero net growth.

**Notes**

*Business* — one of two one-to-one-style models enforced by a unique constraint on a non-key column (`component`), and the only one whose uniqueness is against an enum rather than a foreign key.

*Implementation* — the only model in the schema with **zero foreign keys and zero relationships**, alongside `Customer` which has one inbound edge but no mapped relationship. Its class definition is columns and mixins only, and it can be implemented and tested in complete isolation.

---
---

# Part VI — Relationship Architecture

---

## 37. Relationship architecture

### 37.1 Foreign key accounting, and a discrepancy in the frozen schema

**The number this specification uses is 163 foreign keys**, derived by enumerating every named `fk_*` constraint in the frozen schema's per-table Constraints listings.

| Class | Direction | Count |
|---|---|---|
| Master → Master | Within the master group | **46** |
| Operational / System → Master | Operational and system groups → master group | **81** |
| Operational → Operational | Within the operational group | **36** |
| **Total foreign keys** | | **163** |
| Soft reference | `audit_log` → anything, via `entity_name` + `entity_id` | 1, **not a foreign key** |

**An earlier revision of the schema document's §15 summary table stated 166 total, split 51 / 78 / 37**, and the figure is recorded here because an ORM built to it would have three relationships with no column behind them. Enumerating the named constraints that document declares per table yields **163** distinct names split 46 / 81 / 36. Three constraint names — `fk_machine_production_line`, `fk_machine_alert_threshold_profile`, and `fk_oe_triggering_reading` — were each counted twice, once in a table's Constraints listing and once in a later discussion section. The schema document's §15 and §51.1 now both state 163.

**This is recorded rather than silently reconciled**, and the ORM layer follows the per-table listings for three reasons. Those listings are what name each constraint, so they are what Alembic must reproduce. They are what determine how many relationship attributes exist, so they are what Parts IV and V had to enumerate. And an ORM built to the headline figure would have three relationships with no column behind them.

**Nothing is renamed, added, or removed as a result.** Every foreign key the frozen schema declares per table has exactly one many-to-one relationship in this specification, and no relationship exists without a declared foreign key behind it.

### 37.2 Relationship inventory

**223 relationship attributes across 53 models**, of which 163 are the owning side of a foreign key and 60 are mapped reverse sides.

| Group | Attributes | Owning (FK-holding) | Mapped reverse |
|---|---|---|---|
| Master models M1–M29 | 93 | 46 | 47 |
| Operational models O1–O24 | 130 | 117 | 13 |
| **Total** | **223** | **163** | **60** |

**The asymmetry between the two groups is the whole loading strategy in one table.** Master models map 47 reverse collections because master row counts are bounded by physical and organisational facts. Operational models map only 13, because operational row counts are bounded by time.

### 37.3 One-to-one relationships

Five, each enforced by a unique constraint on the child's foreign key or on a discriminating column.

| Child (FK holder) | Parent | Enforced by | Parent-side attribute | Optional on parent side |
|---|---|---|---|---|
| `MaintenanceEngineer` | `Worker` | Unique on `worker_id` | `Worker.maintenance_engineer` | Yes — 5 of ~13 workers |
| `NotificationRecipient` | `Worker` | Unique on `worker_id` | `Worker.notification_recipient` | Yes — 5 of ~13 workers |
| `MachineOperationalStatus` | `Machine` | Unique on `machine_id` | `Machine.operational_status` | Yes — monitored machines only |
| `AiRecommendation` | `SupervisorContext` | Unique on `supervisor_context_id` | `SupervisorContext.ai_recommendation` | Yes — ~8 % of contexts escalate |
| `SystemHealthStatus` | — | Unique on `component` | — | Not a relationship. Uniqueness against an enum |

**Configuration.** The parent side declares `uselist=False`, which is what makes the attribute a scalar rather than a list. Both sides declare `back_populates`. The child side is a plain many-to-one whose uniqueness the database guarantees.

**The nullability asymmetry is the error most likely to be made.** On the child, the parent is mandatory and non-optional. On the parent, the child is `Optional` — and in four of five cases it is `None` for the majority of rows. `SupervisorContext.ai_recommendation` is `None` for roughly 92 % of contexts. Typing the parent side non-optional would produce a type checker that permits a real `None` dereference on nearly every row.

### 37.4 One-to-many and many-to-one

**Every one of the 163 foreign keys has a many-to-one relationship on the holder.** No exceptions, including where the reverse is unmapped.

**60 of those 163 have a mapped one-to-many reverse.** The remaining 103 are unidirectional, and that is a deliberate configuration rather than incompleteness (§21).

| Reverse mapped when | Reverse unmapped when |
|---|---|
| Children per parent are bounded below roughly 100 by a physical or organisational fact | Children per parent grow with time |
| The collection is what a caller actually wants — `OperationalAlert.events`, `Notification.deliveries`, `MaintenanceWorkRecord.activities` | The correct query is a filtered, ordered, limited query on the child |
| The parent is a small master row and the child set is a configuration set | The parent is small and the child table is large — `Shift`, `FailureSeverityLevel` |

### 37.5 Many-to-many and association objects

**Five many-to-many relationships. All five are resolved by association object models. `secondary=` is used nowhere in this specification.**

| Junction model | Resolves | Attributes beyond its two keys |
|---|---|---|
| `ProductLineCapability` | `Product` ↔ `ProductionLine` | 10 |
| `MachineTypeParameter` | `MachineType` ↔ `MachineParameter` | 9 |
| `BillOfMaterials` | `Product` ↔ `InventoryItem` | 5 |
| `MachineTypeFailureMode` | `MachineType` ↔ `FailureCategory` | 6 |
| `AlertThresholdRule` | `AlertThresholdProfile` ↔ `MachineParameter` | 9 |

**Why `secondary=` is excluded absolutely.** A `secondary=` relationship presents a junction as a plain collection of the far side and hides the junction row. Every one of these five carries attributes that are the reason the junction exists — the cycle time, the normal range, the quantity per unit, the repair duration, the threshold bounds. Presenting `Product.production_lines` as a list of lines would hide `cycle_time_seconds`, which is the most consulted column on `product_line_capability`. Traversal is therefore two hops, always: `Product.line_capabilities` then `.production_line`.

**Two of the five carry a third parent or more**, which `secondary=` could not express at all: `MachineTypeFailureMode` has five parents, `AlertThresholdRule` has four. `secondary=` assumes exactly two.

**`viewonly` many-to-many shortcuts are also excluded.** They would be read-only conveniences that bypass the junction's attributes, and having both a shortcut and the real path invites callers to use whichever they remember.

### 37.6 Role-qualified relationships

Nine models hold two or more foreign keys to the same target. **Each such relationship must state which foreign key it uses**, or mapper configuration fails with an ambiguous-join error — which is a loud failure, and the reason these are listed together is that the *names* are easy to get wrong even when the join is right.

| Model | Target | Relationship names | Both mandatory |
|---|---|---|---|
| `BillOfMaterials` | `InventoryItem` | `inventory_item`, `substitute_inventory_item` | No |
| `QualityInspectionResult` | `Machine` | `machine`, `attributed_machine` | No |
| `ScrapRecord` | `Machine` | `machine`, `attributed_machine` | First only |
| `MaintenanceWorkRecord` | `FailureCategory` | `reported_failure_category`, `confirmed_failure_category` | No |
| `AlertThresholdRule` | `FailureSeverityLevel` | `warning_severity_level`, `critical_severity_level` | **Yes, both** |
| `OperationalAlert` | `FailureSeverityLevel` | `initial_severity_level`, `current_severity_level` | **Yes, both** |
| `FailureSeverityLevel` | `AlertThresholdRule` (reverse ×2) | `warning_threshold_rules`, `critical_threshold_rules` | — |
| `InventoryItem` | `BillOfMaterials` (reverse ×2) | `bom_lines`, `bom_lines_as_substitute` | — |
| `AuditLog` | `PlatformComponent` enum ×2 | `component`, `created_by_component` | Columns, not relationships |

**Naming rule: the role, not the target.** A relationship named `machine` when a sibling is also a machine is only acceptable when one of the two is the unqualified default — `machine_id` is where the thing happened, `attributed_machine_id` is what is blamed. Where neither is a default, both are qualified: `warning_severity_level` and `critical_severity_level`, never `severity_level` and `severity_level_2`.

### 37.7 Cascade strategy and passive deletes

Restated from §22 because this is where a reader looks for it.

| Setting | Value | Reason |
|---|---|---|
| `cascade` | `"save-update, merge"` — the default | 162 of 163 declared foreign keys are `RESTRICT`; zero are `CASCADE` |
| `delete-orphan` | **Used nowhere** | Deletion is not an application operation. Retention purge deletes children first, in explicit dependency order |
| `passive_deletes` | **Not set** | There is no delete path to optimise |
| `single_parent` | Used nowhere | No relationship requires it, since `delete-orphan` is absent |

**The one `SET NULL`.** `operational_event.triggering_reading_id` becomes NULL when the 90-day telemetry purge removes the cited reading. The ORM declares the column `Optional` and the relationship ordinary, and it must not treat a NULL here as an anomaly — on an event older than 90 days it is the designed steady state.

### 37.8 Loading policy summary

| Policy | Applies to | Count |
|---|---|---|
| **Not mapped** | Unbounded reverse collections | ~40 collections that would otherwise exist |
| `select` | Bounded collections, ordinary many-to-one, one-to-one | 212 attributes |
| `raise_on_sql` | Every many-to-one on `MachineSensorReading`, `ProductionCount`, `CycleHistory`, `AuditLog` | 11 attributes |
| `joinedload` / `selectinload` as a relationship default | **Nowhere** | 0 |

The eleven `raise_on_sql` attributes guard the four tables holding roughly 94 % of all rows in the database.

### 37.9 Unique index register

Eight unique indexes, taken verbatim from the frozen schema's §42.4. **Each is a correctness constraint, not a performance index.** Seven are **partial** — they carry a `WHERE` clause, which SQLite supports directly on `CREATE UNIQUE INDEX` — and the eighth is declared over **expressions**, for the NULL reason below.

Each is declared in its model's `__table_args__` as an `Index(..., unique=True)`, and each partial one carries its predicate as `sqlite_where=text(...)`. **Alembic will not infer a predicate**, so an omitted one produces an index that compiles, applies to every row, and rejects rows the schema permits — or, in the boolean cases, silently constrains nothing useful. The predicate is part of the constraint, not a hint.

| Index | Model | Predicate or expression | Rule |
|---|---|---|---|
| `uq_shift_sequence_order_production` | `Shift` | `WHERE shift_type = 'production'` | Rotation order unique among production shifts only; the general shift is excluded |
| `uq_plc_primary_route_per_product` | `ProductLineCapability` | `WHERE capability_type = 'production_route' AND is_primary_line = 1 AND is_active = 1` | Exactly one primary production route per product |
| `uq_machine_bottleneck_per_line` | `Machine` | `WHERE is_bottleneck = 1` | At most one bottleneck per line |
| `uq_maintenance_engineer_team_lead` | `MaintenanceEngineer` | `WHERE is_team_lead = 1 AND is_active = 1` | Exactly one lead per team |
| `uq_alert_threshold_profile_default` | `AlertThresholdProfile` | `WHERE is_default = 1 AND is_active = 1` | Exactly one default profile per machine type |
| `uq_production_run_active_per_line` | `ProductionRun` | `WHERE run_status IN ('setup','running','paused')` | At most one active run per line |
| `uq_oa_open_correlation_key` | `OperationalAlert` | `WHERE alert_status IN ('open','acknowledged','escalated')` | Alert-storm prevention |
| `uq_ds_scope_subject_time` | `DashboardSnapshot` | `COALESCE(production_line_id, -1)`, `COALESCE(machine_id, -1)` | Idempotent rebuild, including plant-scoped rows where both subject columns are NULL |

**Boolean predicates compare against `1` explicitly.** Flags are stored as `INTEGER` (§29), so the predicate is written `is_bottleneck = 1` rather than as a bare truthy column. SQLite would accept the bare form; the explicit comparison states the domain and matches the check constraint on the same column.

**Two carry architectural weight.** `uq_production_run_active_per_line` is the database-level guarantee that two concurrent Simulator writes cannot both schedule a run onto the same line. `uq_oa_open_correlation_key` is the guarantee that two events milliseconds apart cannot each create an alert for the same condition. Both replace what would otherwise require application-level locking, and §42.8 records the consequence: **no ORM-level locking is needed anywhere.** In SQLite the first of the two is reinforced by the engine's single-writer model — the two writes cannot be in flight at once, and the index catches the case where the second writer read a stale state before acquiring the write lock.

**`uq_ds_scope_subject_time` is an expression index because SQLite treats every NULL as distinct.** There is no `NULLS NOT DISTINCT` option and no way to change the behaviour, so a plain unique index over (`snapshot_scope`, `production_line_id`, `machine_id`, `snapshot_at`) would constrain nothing at all for plant-scoped rows: those carry NULL in both subject columns, no two of them would ever conflict, and duplicate snapshots at the same instant would be accepted silently. The index is therefore declared over expressions:

```python
Index(
    "uq_ds_scope_subject_time",
    "snapshot_scope",
    func.coalesce(production_line_id, -1),
    func.coalesce(machine_id, -1),
    "snapshot_at",
    unique=True,
)
```

`-1` is safe as the substitute because `AUTOINCREMENT` issues only positive integers, so no real identity value can collide with it. The rule the frozen model states — one snapshot per scope, per subject, per instant — is preserved exactly; only the mechanism differs.

**One consequence the ORM must respect.** An expression index is used only when a query's predicate matches the expression. A component looking a snapshot up by scope and subject must write the same `COALESCE` form, or it gets a table scan with no error raised. This is the single place in this specification where a query has to be written a particular way for a constraint's index to be usable, and §43.4 repeats it where read patterns are discussed.

### 37.10 Circular import prevention

Restated as a checklist, because it is the mechanism a reviewer should verify per file.

| Rule | Verifiable by |
|---|---|
| Every relationship target is a string | No model imports another model at module scope |
| Annotations use `TYPE_CHECKING` imports | The import block appears last and under a guard |
| No model imports another model at runtime | Grep the model package for cross-model imports outside the guard |
| One aggregating registry imports all 53 | Exactly one module lists them |
| Mapper configuration errors surface at import | A relationship typo fails at startup, not at query time |

**Why it cannot fail structurally.** The import graph has all 53 models in one layer with no intra-layer edges (§16). A cycle requires an edge between two models, and string targets mean no such edge exists.

---
---

# Part VII — Python Type Strategy

---

## 38. Python type strategy

### 38.1 The complete type vocabulary

Nine Python types and one wrapper cover all 512 columns. Nothing else appears in any annotation.

| Python type | Columns | Maps from | Why this type |
|---|---|---|---|
| `int` | ~180 | `INTEGER` | SQLite has one integer type, 64-bit signed. Python integers are arbitrary precision, so nothing overflows on the way in |
| `str` | ~120 | `VARCHAR(n)`, `CHAR(n)`, `TEXT` | Length is a database constraint, not a Python distinction |
| `Decimal` | 74 | `NUMERIC(p,s)` | **Exact decimal arithmetic. Never `float`** — see §38.2 |
| `bool` | 42 | `INTEGER` | Direct |
| `datetime` | ~90 | `DATETIME` | **Always timezone-aware** — see §38.3 |
| `date` | 19 | `DATE` | Calendar day with no time and no zone |
| `time` | 2 | `TIME` | `shift.start_time`, `shift.end_time`. Clock time, no zone |
| `dict[str, Any]` | 9 | `TEXT` documents | Opaque to the ORM |
| `list[Any]` | 1 | `supervisor_context.related_alert_codes` | Opaque to the ORM |
| Enum classes | 100 | 65 vocabularies | The 65 classes in Part IX |
| `Optional[T]` | ~170 | Any nullable column | Wraps the above |

**`UUID` is not used.** No column in the frozen schema is a UUID. Every key is an identity integer, and `audit_log.correlation_id` is `VARCHAR(40)`, mapped as `str` because the schema types it as text and the ORM does not reinterpret it.

**`float` appears nowhere.** Not on one column.

### 38.2 `Decimal`, and why `float` is excluded absolutely

All 74 `NUMERIC` columns map to `decimal.Decimal`. The `Numeric` type is configured `asdecimal=True`, which is its default and is stated explicitly so it cannot be changed by accident.

**What this does and does not claim, stated first so the rest of the section is read correctly.** SQLite gives `NUMERIC(p,s)` numeric affinity and implements no fixed-point storage: a fractional value is stored as an 8-byte double. **So the storage layer here is floating point, and `Decimal` is the type at the application boundary, not a guarantee about the bytes on disk** (§39.4 states the storage side in full). This section is about why the *Python* type must nevertheless be `Decimal` — which matters more, not less, once the storage is understood, because every arithmetic operation the platform performs happens in Python.

**Three reasons, in order of severity for this platform.**

**A `float` threshold comparison produces false alerts.** `machine_sensor_reading.reading_value` is `NUMERIC(12,4)` and is compared against `alert_threshold_rule.critical_high`, also `NUMERIC(12,4)`. In binary floating point, a value that is exactly equal to a threshold in decimal may compare as greater or less depending on the arithmetic path that produced it. On 32 million readings a year, that produces a stream of alerts nobody can reproduce. Converting to `Decimal` on read and rounding at the declared scale before comparing collapses the arithmetic path to one; leaving the value a `float` lets every consumer accumulate its own error.

**A `float` probability crosses a severity boundary wrongly.** `prediction_result.failure_probability` is `NUMERIC(5,4)`, mapped to a severity level by threshold comparison. The one place error is intolerable is precisely at the boundary, and that is exactly where `float` misbehaves.

**Money does not survive `float`.** 14 monetary columns feed the business impact figures in every recommendation. Accumulated representation error in a downtime cost calculation makes the platform's most visible output wrong in a way a reader can spot.

**The cost, stated honestly.** `Decimal` arithmetic is slower than `float` and `Decimal` objects are larger. On a bulk read of a million readings that is measurable. It is accepted because the alternative is incorrect, and because bulk numerical work belongs to the analytics layer, which may read the raw column and convert deliberately — a conscious, local decision rather than a silent, global one.

**A rule that follows: no ORM attribute mixes `Decimal` and `float`.** Not in a validator, not in a computed property, not in a default. A single `float` in an expression converts the whole expression — and since the value arrived from the driver as a `float` before `asdecimal=True` converted it, letting one back in undoes the only place the boundary is drawn.

**Where the imprecision genuinely lives, so it is not looked for in the wrong place.** It is confined to one step: the storage round trip. A value written as `Decimal('12.3456')` is stored as the nearest double and reconstructed as a `Decimal` from that double. `NUMERIC(14,2)` is the widest column here, and a double carries 15–17 significant decimal digits, so the reconstruction is exact at the declared scale for every column in this schema. What is *not* safe is treating a reconstructed value as exact beyond its declared scale, which is why §39.4 makes rounding at the declared scale an application obligation.

### 38.3 `datetime`, timezone-aware without exception

| Property | Value |
|---|---|
| Python type | `datetime.datetime`, tz-aware, always UTC |
| SQLAlchemy type | `DateTime` — **never** `DateTime(timezone=True)` |
| Storage | ISO-8601 text with **no offset**. UTC is a convention the ORM upholds, not something the column records |
| Naive input | **Rejected** by `@validates` on the timestamp columns that anchor time-window logic (§41.3) |
| Value on read | Naive from the driver, **re-attached to UTC** by the type layer so the round trip is not lossy (§28) |
| Display conversion | At presentation, using `plant.timezone` |

**The contract is entirely the ORM's, because SQLite has nothing to say about it.** There is no date/time type and no zone concept: the column holds a string. `DateTime(timezone=True)` is accepted by the dialect and does not persist an offset, so declaring it would suggest a guarantee that does not exist. Both directions of the conversion therefore belong here — reject naive on the way in, attach UTC on the way out — and §28 states them as the four-step resolution.

**Why rejection rather than silent localisation.** Assuming a naive datetime is UTC, or is local time, is a guess. Guessing wrong on `machine_sensor_reading.recorded_at` shifts every reading by the UTC offset, which moves readings across shift boundaries and misattributes hours of production. The failure would be silent and would only be noticed as a shift-report anomaly weeks later. Rejecting the value at assignment makes the defect immediate and local.

**One property of the storage format is genuinely useful and worth relying on.** Fixed-width ISO-8601 text sorts chronologically as text, so `ORDER BY recorded_at`, range predicates, and the timestamp comparisons inside check constraints all work directly with no conversion and no function call — which is also why the format is fixed rather than left to the driver.

**`date` and `time` are not datetimes and are not converted.** `shift.start_time` is a wall-clock time that recurs daily; combining it with a date requires the plant timezone and a calendar day, and that combination belongs to the component doing it, not to the ORM.

### 38.4 `Annotated` type aliases

Repeated types are declared once as `Annotated` aliases and resolved through the base's `type_annotation_map`. **A model never writes a bare `Numeric(p, s)` or `DateTime`.**

| Alias | Resolves to | Semantic role | Columns |
|---|---|---|---|
| `MasterPk` | `Integer`, primary key, `autoincrement=True` | Master primary key | 29 |
| `OperationalPk` | `Integer`, primary key, `autoincrement=True` | Operational primary key | 24 |
| `TimestampTz` | `DateTime` | Any timestamp — names the UTC contract, not a column capability (§28) | ~90 |
| `JsonDoc` | `JSON` | Any JSON document, stored as `TEXT` (§39.6) | 10 |
| `Measurement` | `Numeric(12,4)` | Sensor values, thresholds, quantities at 4 dp | 15 |
| `Money` | `Numeric(12,2)` | Currency amounts | 8 |
| `MoneyLarge` | `Numeric(14,2)` | `customer.annual_order_value` | 1 |
| `Quantity` | `Numeric(12,2)` | Production and stock quantities | 9 |
| `Hours` | `Numeric(12,2)` | Accumulated operating hours | 2 |
| `Rate` | `Numeric(10,2)` | Units per hour, floor space | 5 |
| `Percent` | `Numeric(5,2)` | Unsigned percentages, 0–100 | 10 |
| `SignedPercent` | `Numeric(6,2)` | `cycle_history.deviation_from_standard_pct` | 1 |
| `Probability` | `Numeric(5,4)` | Failure probability and confidence band | 3 |
| `Seconds2` | `Numeric(8,2)` | Cycle times, and `machine_type.rated_power_kw` | 3 |
| `Ratio1` | `Numeric(2,1)` | `supplier.reliability_rating` | 1 |
| `Weight` | `Numeric(4,2)` | `machine_type_parameter.criticality_weight` | 1 |
| `RuleNumeric` | `Numeric(14,4)` | `business_rule.value_numeric` | 1 |

**Three decisions in this table need defending.**

**`MasterPk` and `OperationalPk` are now identical and both are kept.** In SQLite there is one integer width, so both resolve to `Integer` with `autoincrement=True` and emit the same `INTEGER PRIMARY KEY AUTOINCREMENT` (§25). Collapsing them into one alias was considered and rejected: the two names carry the master/operational distinction into the package layout (§44) and into all 53 model entries, and a single `Pk` alias would erase a signal that every other part of this specification uses. Identical DDL under two names is not duplication when the names carry meaning — the same argument as `Money` and `Quantity` below.

**`Money` and `Quantity` are separate aliases with identical DDL.** Both are `Numeric(12,2)`. They are kept apart because a reader of `unit_cost` and a reader of `safety_stock_qty` are asking different questions, and because if either precision ever changed, changing one alias would not silently change the other. Identical DDL with different names is not duplication when the names carry meaning.

**Aliases exist even where a precision appears once.** `Ratio1`, `Weight`, `RuleNumeric`, `MoneyLarge`, and `SignedPercent` each cover one column. They exist so that **no model declares a bare precision anywhere**, which means a reviewer never has to judge whether a given `Numeric(5, 2)` was intentional or a typo for `Numeric(5, 4)`. That judgement is the error this convention removes, and it is worth five single-use names.

**Where an alias would be misleading, it is not used.** `machine_type.rated_power_kw` resolves through `Seconds2` because both are `NUMERIC(8,2)`. A `PowerKw` alias with identical DDL would be a second name for one type, and §45 rejects synonyms. The attribute name carries the meaning.

### 38.5 `Optional` and nullability

**The annotation is the single source of truth for nullability.** `Optional[T]` means the column is `NULL`; its absence means `NOT NULL`. `nullable=` is never passed to `mapped_column`, so the two can never disagree.

Roughly 170 of 512 columns are nullable, and **the frozen schema's nulls are meaningful rather than incidental.** Six patterns recur, and a caller must know which applies:

| NULL means | Examples |
|---|---|
| Not applicable to this row | `machine_type_failure_mode.typical_warning_period_hours` — sudden failures have no warning period |
| Not yet known | `production_run.actual_start_at` before the run starts |
| Not measured or not surveyed | `plant_area.floor_space_sqm` |
| **A scope of "all"** | `business_rule.production_line_id` NULL means plant-wide; `notification_recipient.scope_production_line_id` NULL means all lines |
| **Fall back to a default elsewhere** | `machine.alert_threshold_profile_id` NULL means use the machine type's default profile |
| **The referenced row was purged** | `operational_event.triggering_reading_id` after 90 days |

The last three are the ones that break code written by someone who assumed NULL meant "missing". A filter of `production_line_id = :line` silently excludes every plant-wide business rule, and that is a correctness bug with no error message.

### 38.6 Typed relationships

| Relationship kind | Annotation shape |
|---|---|
| Many-to-one, mandatory | The target class |
| Many-to-one, optional | `Optional[Target]` |
| One-to-one, child side | The target class |
| One-to-one, parent side | `Optional[Target]` |
| One-to-many | `list[Target]` |

Target classes are imported under `TYPE_CHECKING` so the annotation is real to a type checker and absent at runtime (§17). The relationship's `back_populates`, loading strategy, and foreign key are configured in the `relationship` directive; the annotation carries only the type and the cardinality.

### 38.7 What is deliberately absent

| Not used | Why |
|---|---|
| `float` | §38.2 |
| `UUID` | No UUID column exists |
| Naive `datetime` | §38.3 |
| `TypedDict` for `TEXT` payloads | Would put the Prediction Agent's feature schema and the Decision Agent's prompt-output shape in the persistence layer |
| Pydantic models | Explicitly out of scope, and would duplicate every column a third time |
| Hybrid properties computing derived values | Every derived value in this schema is either a stored column or belongs to a component. A hybrid would be a third authority |
| `association_proxy` | Would recreate the `secondary=` shortcut that §37.5 rejects |
| Custom `TypeDecorator` classes | The Annotated aliases achieve the same reuse without introducing types whose behaviour a reader must go and read |

---
---

# Part VIII — SQLite Type Mapping

---

## 39. SQLite type mapping

### 39.1 The complete mapping table

Every declared type in the frozen schema, its SQLAlchemy construct, and its Python type. **This table is the whole mapping; nothing outside it appears in any model.**

| SQLite declared type | Affinity | SQLAlchemy | Python | Columns | Notes |
|---|---|---|---|---|---|
| `INTEGER PRIMARY KEY AUTOINCREMENT` | INTEGER | `Integer`, primary key, `autoincrement=True` | `int` | 53 | Via `MasterPk` / `OperationalPk`. Aliases the rowid |
| `INTEGER` | INTEGER | `Integer` | `int` | ~200 | All 163 foreign keys, counts, durations |
| `INTEGER` (flag) | INTEGER | `Boolean(create_constraint=True)` | `bool` | 45 | Stores 0 or 1 with a domain check (§29) |
| `NUMERIC(p,s)` | NUMERIC | `Numeric(p, s, asdecimal=True)` | `Decimal` | 74 | 11 distinct precisions, all behind aliases (§38.4). See §39.4 |
| `VARCHAR(n)` | TEXT | `String(n)` | `str` | ~95 | Length not enforced by the engine; a check constraint enforces it (§39.5) |
| `CHAR(n)` | TEXT | `CHAR(n)` | `str` | 6 | Fixed-width codes. No blank padding in SQLite |
| `TEXT` | TEXT | `Text` | `str` | ~30 | Unbounded free text |
| `TEXT` (vocabulary) | TEXT | `Enum(..., native_enum=False)` | The enum class | 100 | Configuration in §40.4 |
| `TEXT` (JSON) | TEXT | `JSON` | `dict[str, Any]` or `list[Any]` | 10 | Via `JsonDoc`. See §39.6 |
| `DATETIME` | NUMERIC | `DateTime` | `datetime`, tz-aware UTC | ~90 | Via `TimestampTz`. Stored as ISO-8601 text |
| `DATE` | NUMERIC | `Date` | `date` | 19 | Stored as ISO-8601 text |
| `TIME` | NUMERIC | `Time` | `time` | 2 | `shift.start_time`, `shift.end_time` |

**The affinity column is included because it is the only thing SQLite actually acts on.** A declared type name gives a column an affinity, which is the storage class SQLite prefers when converting an incoming value; it does not restrict what can be stored. Every rule the affinity does not enforce is a check constraint, and §39.2 states that as a principle.

### 39.2 Mapping philosophy

Five principles, applied without exception.

**1 — The declared type is documentation; the constraint is enforcement.** SQLite is dynamically typed. `VARCHAR(150)` will hold a 5,000-character string, `INTEGER` will hold `'abc'`, and `NUMERIC(5,4)` will hold `9.9999`. Where the frozen model relies on the type to enforce a rule, this specification declares the type *and* names the check constraint that actually enforces it. The rule is never dropped; it changes layer.

**2 — Precision is never widened or narrowed.** All 11 `NUMERIC` precisions are reproduced exactly. `cycle_history.deviation_from_standard_pct` stays `NUMERIC(6,2)` and does not get folded into the more common `NUMERIC(5,2)`, because it is signed and can exceed 100 in both directions.

**3 — Generic SQLAlchemy types are preferred over dialect types.** `JSON` rather than a dialect-specific JSON type, `Enum(native_enum=False)` rather than a dialect enum, `DateTime` rather than a dialect timestamp. The SQLite dialect module is imported only where a genuinely SQLite-specific construct is needed, which in this schema is nowhere in the column definitions and only in the `sqlite_where` predicate on seven indexes. **The models are not portable to another database and are not intended to be** — but using generic types keeps the declarations readable and avoids depending on dialect internals.

**4 — Server-side defaults are declared as server defaults.** Never as Python-side defaults. `CURRENT_TIMESTAMP`, `1`, `0`, `'general'`, `'valid'`, `'open'`, `'planned'`, `'normal'`, `'in_service'` are all column definitions in the database. Declaring them Python-side would make them invisible to any other writer — and SQLite has no privilege system preventing another writer from existing, which makes this stricter than a preference.

**5 — One integer type.** SQLAlchemy's `SmallInteger` and `BigInteger` both resolve to the same SQLite storage, and `BigInteger` actively breaks the rowid alias on a primary key. `Integer` is used for every whole-number column in the schema.

### 39.3 Identity columns

| Concern | Handling |
|---|---|
| DDL | `INTEGER PRIMARY KEY AUTOINCREMENT`, emitted by Alembic from the primary key declaration |
| `autoincrement` | `True` — SQLAlchemy omits the column from `INSERT` |
| Explicit value | Never supplied. SQLite would accept it; the prohibition is the ORM's (§26) |
| Value availability | After flush, from the driver's last-inserted-rowid |
| Key reuse | Impossible — `AUTOINCREMENT` maintains a high-water mark |
| Bulk insert on `machine_sensor_reading` | One multi-row statement, no key fetch (§43.7) |

**`AUTOINCREMENT` is required rather than merely chosen.** Without it SQLite assigns `max(rowid) + 1`, so the retention purge that removes the oldest telemetry rows would make those identifiers available for reuse — silently repointing `operational_event.triggering_reading_id` and `audit_log.entity_id` at unrelated rows. The frozen schema's §13.1 states this and the ORM must not weaken it.

**`Integer` on the primary key is not interchangeable with `BigInteger`.** §25 states why: only a column declared exactly `INTEGER` becomes the rowid alias, and `AUTOINCREMENT` is rejected on anything else.

### 39.4 `NUMERIC` precision register and what SQLite stores

The complete set, so that no precision is invented during implementation.

| Precision | Alias | Semantic | Example columns |
|---|---|---|---|
| `NUMERIC(2,1)` | `Ratio1` | 0.0–9.9 rating | `supplier.reliability_rating` |
| `NUMERIC(4,2)` | `Weight` | Weighting factor | `machine_type_parameter.criticality_weight` |
| `NUMERIC(5,2)` | `Percent` | Unsigned percentage | `target_oee_percent`, `data_completeness_pct`, `scrap_rate_pct` |
| `NUMERIC(5,4)` | `Probability` | 0.0000–1.0000 | `failure_probability`, `confidence_band_low/high` |
| `NUMERIC(6,2)` | `SignedPercent` | Signed percentage, may exceed 100 | `deviation_from_standard_pct` |
| `NUMERIC(8,2)` | `Seconds2` | Cycle seconds; also rated power | `cycle_time_seconds`, `rated_power_kw` |
| `NUMERIC(10,2)` | `Rate` | Units per hour; floor space | `design_capacity_units_per_hour`, `floor_space_sqm` |
| `NUMERIC(12,2)` | `Money` / `Quantity` / `Hours` | Currency, quantity, accumulated hours | `unit_cost`, `planned_quantity_units`, `accumulated_operating_hours` |
| `NUMERIC(12,4)` | `Measurement` | Sensor values, thresholds, BOM quantities | `reading_value`, `warning_high`, `quantity_per_unit` |
| `NUMERIC(14,2)` | `MoneyLarge` | Annual aggregate currency | `customer.annual_order_value` |
| `NUMERIC(14,4)` | `RuleNumeric` | Configurable rule value | `business_rule.value_numeric` |

**How SQLite stores these, stated plainly.** `NUMERIC(p,s)` is accepted as a type name and given **NUMERIC affinity**. SQLite implements no fixed-point semantics: a value with a fractional component is stored as `REAL`, an 8-byte IEEE 754 double, and a value without one as `INTEGER`. The declared precision and scale are **not enforced** by the engine.

**`asdecimal=True` is declared on every `Numeric` column, and what it does here is worth being precise about.** SQLAlchemy converts the value to a Python `Decimal` on the way out, so the Python-side type the frozen models specify is preserved and every model entry in Parts IV and V reads as written. What it does **not** do is recover precision the storage round trip already discarded — the value is reconstructed from a double.

**FactoryFlow AI does not require accounting-grade fixed-point storage, and that is why this is acceptable.** The platform has no general ledger, reconciles to no financial system, and issues no invoices. Its monetary columns hold *standard* prices and costs from which the Decision Agent computes an *estimated* business impact — a magnitude a human reads, not a balance. A double carries about 15–17 significant decimal digits, comfortably more than the widest column here (`NUMERIC(14,2)`) needs.

**Two obligations follow, and both belong to the application rather than the engine.**

- **Round at the declared scale before comparing for equality.** Equality on a raw double is unreliable at the last bit. Range comparisons — which is what this schema's ~90 numeric constraints and every threshold evaluation actually perform — are unaffected.
- **Do not accumulate money in a loop without rounding.** Summing thousands of doubles drifts. Aggregations round at the declared scale.

**`float` still appears in no annotation.** `Decimal` remains the Python type on all 74 columns, for the reasons §38.2 gives. The storage layer is floating point; the application layer is not, and keeping the boundary at the type layer is what confines the imprecision to one place.

### 39.5 `VARCHAR(n)`, `CHAR(n)`, and `TEXT`

The frozen schema distinguishes three text forms and this specification reproduces all three.

| Schema type | SQLAlchemy | Applied to | Length enforcement |
|---|---|---|---|
| `VARCHAR(n)` | `String(n)` | Business keys, names, codes — anything with a business length limit | `CHECK (length(col) <= n)` |
| `CHAR(n)` | `CHAR(n)` | Fixed-width codes: `country_code`, `currency_code`, `abc_class`, `display_color_hex` | `CHECK (length(col) = n)` |
| `TEXT` | `Text` | Unbounded free text | None; several carry a not-blank check instead |

**SQLite enforces no declared length.** `String(150)` renders `VARCHAR(150)`, which the engine treats as TEXT affinity and against which it will store any length. The declared length is retained because it is real design information — a 150-character supplier name is a business limit the frozen model states — and the enforcement moves to a check constraint declared in the model's table arguments.

**The length checks are stated once as a rule rather than repeated per model.** Every column declared `VARCHAR(n)` in Parts IV and V has a corresponding `length(col) <= n` check, and every `CHAR(n)` column has `length(col) = n`. The frozen schema's §12.3 and §38.1 state the same rule from the schema side, and §46.1 counts the resulting constraints.

**`length()` counts characters, not bytes**, so a multi-byte name is measured as the frozen model intends. This matters for `supplier_name` and `customer_name`, which may legitimately contain non-ASCII characters.

**`CHAR(n)` is not folded into `String(n)`.** SQLite does not blank-pad, so a `CHAR(2)` column holding `'IN'` stores exactly two characters and comparisons behave as written — simpler than the padding semantics a server database applies. The distinction is preserved because the frozen schema makes it and because the equality-length check differs from the upper-bound check.

### 39.6 JSON

Ten columns hold JSON documents, all mapped through the `JsonDoc` alias to SQLAlchemy's generic **`JSON`** type, stored in a `TEXT` column.

| Model | Column | Python type | Nullable |
|---|---|---|---|
| `PredictionFeatureSnapshot` | `feature_values` | `dict[str, Any]` | No |
| `PredictionResult` | `top_contributing_features` | `dict[str, Any]` | No |
| `SupervisorContext` | `related_alert_codes` | `Optional[list[Any]]` | Yes |
| `SupervisorContext` | `context_document` | `Optional[dict[str, Any]]` | Yes |
| `AiRecommendation` | `supporting_evidence` | `dict[str, Any]` | No |
| `AiRecommendation` | `business_impact` | `dict[str, Any]` | No |
| `DashboardSnapshot` | `snapshot_document` | `dict[str, Any]` | No |
| `AuditLog` | `action_detail` | `Optional[dict[str, Any]]` | Yes |
| `SystemHealthStatus` | `metrics_document` | `Optional[dict[str, Any]]` | Yes |

**SQLAlchemy's `JSON` type serialises on write and deserialises on read.** A Python `dict` is converted to a compact JSON string when the column is written and parsed back to a `dict` when it is read, so the attribute is a native Python structure at the application boundary while the storage is `TEXT`. No adapter or custom type is required — this is the generic type's behaviour on the SQLite dialect.

**Three rules.**

**The ORM does not interpret any document.** No `TypedDict`, no schema validation, no accessor properties. Each document's shape is its writing component's contract — `feature_set_version` versions one of them explicitly — and moving that contract into the ORM would require an ORM change on every model revision or prompt revision.

**Structural validation is the database's, via check constraints.** The frozen schema's §39.3 places `json_valid()` and `json_type()` checks on all ten columns. The ORM does not duplicate them (§41.2), and it benefits from them: a malformed payload is rejected at write time rather than surfacing in whichever component tried to parse it.

**`MutableDict` tracking is not applied.** Eight of the ten live on append-only models where in-place mutation is a defect the immutability hook catches (§41.4). The two on mutable models — `DashboardSnapshot.snapshot_document` and `SystemHealthStatus.metrics_document` — are replaced wholesale on rebuild or heartbeat, never edited in place. Without `MutableDict`, an in-place edit to a loaded dict is **not** detected by the unit of work and would be silently discarded, so the whole-replacement convention is load-bearing rather than stylistic. §45.4 records it.

**No deferred loading.** A row carrying a document is read precisely to obtain it, so deferring the column would guarantee a second query.

### 39.7 Controlled vocabularies

100 columns across 65 vocabularies. Full configuration in §40.4; the type-mapping facts are here.

| Concern | Handling |
|---|---|
| SQLite storage | `TEXT` |
| SQLAlchemy construct | `Enum(PythonEnumClass, native_enum=False)` |
| `native_enum` | **`False`** — SQLite has no enum type |
| `create_constraint` | **`False`** — the frozen schema declares the check explicitly by name |
| `length` | Not passed. SQLAlchemy derives it from the longest member value |
| `values_callable` | **Mandatory** — see §40.4 |
| Python type | The enum class, or `Optional[EnumClass]` where nullable |

**`native_enum=False` is what keeps this a text column.** With the default, SQLAlchemy asks the dialect for a native enum type; SQLite has none, so it falls back to text plus a generated check. Stating `native_enum=False` makes the intent explicit rather than relying on fallback behaviour, and it is what makes `create_constraint=False` meaningful.

**The rendered type is `VARCHAR(n)` rather than the literal `TEXT` the frozen schema writes, and the two are the same SQLite column.** `n` is the length of the longest member value. Both spellings are given TEXT affinity, neither is length-enforced, and both store the string exactly as supplied — so storage, comparison, indexing, and the check constraint are identical. The frozen schema's `TEXT` is the clearer thing to write by hand; `VARCHAR(n)` is what SQLAlchemy emits from a non-native enum. No rule anywhere in either document depends on which name appears in the DDL, and §39.5 records the same equivalence for `VARCHAR(n)` generally.

**`create_constraint=False` avoids a duplicate constraint.** SQLAlchemy would otherwise emit its own unnamed `IN (...)` check alongside the named `ck_<table>_<column>_allowed` the frozen schema specifies. Two constraints enforcing one rule is redundant, and the unnamed one produces a worse diagnostic. The named constraint is declared in the model's table arguments.

**There is no type-creation sequencing to manage.** A server-database design would need every enum type created before any table that references it. Here the vocabulary is a constraint on a column, so it comes into existence with the table. §40.5 records what that removes from the migration path.

### 39.8 Types deliberately not used

| Not used | Where it might have been | Why not |
|---|---|---|
| `BigInteger` | Operational primary keys, `sequence_number` | Renders `BIGINT`, which breaks the rowid alias and rejects `AUTOINCREMENT`. `Integer` is already 64-bit in SQLite |
| `SmallInteger` | `operating_days_per_week`, `shifts_per_day`, `line_position` | Resolves to the same storage. Two names for one type |
| `Float` | The 74 `NUMERIC` columns | Would make `float` the Python type. `Decimal` is preserved via `asdecimal=True` (§38.2, §39.4) |
| Dialect JSON type | The 10 JSON columns | Generic `JSON` already serialises to `TEXT` on SQLite |
| Native `Enum` | The 100 vocabulary columns | SQLite has no enum type |
| `ARRAY` | `supervisor_context.related_alert_codes` | SQLite has no array type. The schema types it as JSON |
| `Interval` | The many `*_minutes`, `*_seconds`, `*_hours` columns | The schema uses `INTEGER` with the unit in the column name. Honoured exactly |
| `Uuid` | Any surrogate key | No UUID column exists. Keys are integer identities |
| `LargeBinary` | — | No column holds binary content. Model artefacts live on the filesystem |
| `TypeDecorator` subclasses | Repeated precisions | Annotated aliases achieve the reuse without adding types whose behaviour must be read elsewhere |

# Part IX — Enum Strategy

---

## 40. Enum strategy

### 40.1 The 65 enum classes

**65 controlled vocabularies, 65 Python enum classes, 100 columns.** One class per vocabulary, no merging, no splitting.

In the database each vocabulary is a `TEXT` column plus a named `CHECK (column IN (...))` constraint — there is no type object, because SQLite has none (§39.7, and the schema document's §37.1). That makes these classes more load-bearing than they would be against a server database: **the Python enum class is the only place a vocabulary is named as a single thing.** In the database the same value list is repeated once per column, so the classes here and the catalogue in the schema document's §37.6 are what keep 100 constraints agreeing with one another.

Class names are PascalCase of the vocabulary name. Nothing is prefixed or qualified, because all 53 tables live in one database file and the 65 vocabulary names are already unique across the whole schema — there is exactly one `area_type` and it is ambiguous with nothing.

The three group headings below — master, operational, system — are the logical groupings of §13, not database schemas. They determine which module a class lives in (§40.7) and nothing else.

**A note on 100 versus 101, because both numbers are correct about different things.** The schema document counts **101** columns carrying a `ck_<table>_<column>_allowed` membership check. **100** of them bind to one of the 65 enum classes below: 76 declared explicitly in the Columns tables of Parts IV and V, plus `created_by_component` on all 24 operational models, which the `ComponentProvenanceMixin` supplies and no entry restates (§34). The 101st is `inventory_item.abc_class` — a three-letter classification enforced by exactly the same mechanism but deliberately given no enum class, for the reason §40.6 sets out. **So: 101 membership checks, 100 enum bindings.** Every count in this Part refers to bindings.

**Master group — 30 vocabularies**

| Vocabulary | Python class |
|---|---|
| `access_restriction` | `AccessRestriction` |
| `area_type` | `AreaType` |
| `business_rule_category` | `BusinessRuleCategory` |
| `business_rule_value_type` | `BusinessRuleValueType` |
| `capability_type` | `CapabilityType` |
| `criticality_level` | `CriticalityLevel` |
| `customer_priority_tier` | `CustomerPriorityTier` |
| `degradation_direction` | `DegradationDirection` |
| `department_function` | `DepartmentFunction` |
| `drift_direction` | `DriftDirection` |
| `employment_type` | `EmploymentType` |
| `equipment_class` | `EquipmentClass` |
| `failure_domain` | `FailureDomain` |
| `interval_basis` | `IntervalBasis` |
| `inventory_item_type` | `InventoryItemType` |
| `inventory_location_type` | `InventoryLocationType` |
| `line_type` | `LineType` |
| `machine_lifecycle_status` | `MachineLifecycleStatus` |
| `maintenance_specialization` | `MaintenanceSpecialization` |
| `maintenance_type` | `MaintenanceType` |
| `measurement_domain` | `MeasurementDomain` |
| `parameter_data_type` | `ParameterDataType` |
| `quality_criticality` | `QualityCriticality` |
| `relative_frequency` | `RelativeFrequency` |
| `role_category` | `RoleCategory` |
| `shift_type` | `ShiftType` |
| `skill_level` | `SkillLevel` |
| `supplier_type` | `SupplierType` |
| `threshold_sensitivity` | `ThresholdSensitivity` |
| `unit_of_measure` | `UnitOfMeasure` |

**Operational group — 31 vocabularies**

| Vocabulary | Python class |
|---|---|
| `alert_resolution_type` | `AlertResolutionType` |
| `alert_status` | `AlertStatus` |
| `alert_suppression_reason` | `AlertSuppressionReason` |
| `cycle_outcome` | `CycleOutcome` |
| `delivery_channel` | `DeliveryChannel` |
| `delivery_failure_reason` | `DeliveryFailureReason` |
| `delivery_status` | `DeliveryStatus` |
| `escalation_decision` | `EscalationDecision` |
| `event_category` | `EventCategory` |
| `event_type` | `EventType` |
| `inspection_disposition` | `InspectionDisposition` |
| `inspection_type` | `InspectionType` |
| `inventory_movement_type` | `InventoryMovementType` |
| `machine_operational_state` | `MachineOperationalState` |
| `maintenance_activity_type` | `MaintenanceActivityType` |
| `maintenance_work_status` | `MaintenanceWorkStatus` |
| `maintenance_work_type` | `MaintenanceWorkType` |
| `notification_suppression_reason` | `NotificationSuppressionReason` |
| `notification_type` | `NotificationType` |
| `reading_quality_flag` | `ReadingQualityFlag` |
| `recommendation_action_type` | `RecommendationActionType` |
| `rejection_reason` | `RejectionReason` |
| `root_cause_confidence` | `RootCauseConfidence` |
| `run_pause_reason` | `RunPauseReason` |
| `run_priority` | `RunPriority` |
| `run_status` | `RunStatus` |
| `scrap_reason` | `ScrapReason` |
| `snapshot_insufficiency_reason` | `SnapshotInsufficiencyReason` |
| `snapshot_scope` | `SnapshotScope` |
| `state_transition_reason` | `StateTransitionReason` |
| `threshold_direction` | `ThresholdDirection` |

**System group — 4 vocabularies**

| Vocabulary | Python class |
|---|---|
| `audit_action_type` | `AuditActionType` |
| `audit_outcome` | `AuditOutcome` |
| `component_health_status` | `ComponentHealthStatus` |
| `platform_component` | `PlatformComponent` |

**No enum class name collides with any of the 53 model class names.** Verified across both sets: the nearest pairs are `MachineOperationalState` (enum) against `MachineOperationalStatus` (model), `InventoryMovementType` against `InventoryMovement`, `NotificationType` against `Notification`, and `CycleOutcome` against `CycleHistory`. All distinct, so both live in one namespace without qualification.

### 40.2 Shared enum classes

Five vocabularies serve more than one column. Each sharing is functional, and in three cases the platform's correctness depends on it.

| Enum class | Values | Columns | Models | Why shared |
|---|---|---|---|---|
| `MaintenanceSpecialization` | 4 | **5** | `MachineCategory`, `MaintenanceTeam`, `MaintenanceEngineer` (×2), `FailureCategory` | **Matching a failed machine to a qualified team is a direct value comparison.** Separate vocabularies would make it an inference rule |
| `CriticalityLevel` | 4 | 2 | `ProductionLine`, `Machine` | Prioritisation compares line and machine criticality together |
| `UnitOfMeasure` | 6 | 2 | `Product`, `InventoryItem` | Quantity arithmetic in `BillOfMaterials` crosses the two. See §40.3 |
| `EventCategory` | 5 | 2 | `OperationalEvent.event_category`, `OperationalAlert.alert_category` | **Correlation depends on the alert's category matching its events' exactly** |
| `PlatformComponent` | 8 | **26** | `AuditLog.component`, `SystemHealthStatus.component`, and `created_by_component` on all 24 operational models | One provenance vocabulary across the whole database |

**What sharing means on each side of the boundary, because the two sides differ here.** In Python a shared vocabulary is **one class**, imported by every model that uses it, and a value assigned to one column is the same object as a value assigned to another. In the database it is **the same value list written out once per column**, because a check constraint belongs to the column it constrains rather than existing as an object columns point at. `platform_component` is therefore one Python class and 26 identical constraints.

**The ORM is not the authority for those value lists.** The schema document's §37.6 is, and every class below is transcribed from it. What makes the transcription checkable rather than merely careful is §40.4's `values_callable` requirement: a member value that does not match the catalogue fails the column's check constraint on first insert, loudly, rather than drifting quietly.

**`PlatformComponent` is the most widely used vocabulary in the schema**, and §40.7 explains that there is no placement decision to make because a vocabulary is a check constraint on a column rather than a database object all users can reach. It is the only enum the `ComponentProvenanceMixin` references, and the only reason mixins may import from the enums module (§16, R2).

**A note on the count.** The original frozen draft introduced this as "four types" and then tabulated five. The table was the authoritative content; the reference schema document's §37.2 now states **five** in both its lead sentence and its table, and the ORM defines five shared classes to match. Nothing is renamed or removed as a result.

**Maintenance obligation.** Adding a value to a shared vocabulary affects every user, and in SQLite it also affects every constraint. `MaintenanceSpecialization` must be extended in all five columns' business logic simultaneously — adding a discipline without updating team matching produces machines that cannot be assigned to anyone — and the widening itself is a table rebuild per affected table (§40.5).

### 40.3 `UnitOfMeasure` — one class, two permitted sets

`unit_of_measure` carries six values. `product.unit_of_measure` permits five of them; `ck_product_unit_of_measure_allowed` excludes `BOX`. `inventory_item.unit_of_measure` permits all six.

**The ORM defines one class with all six members.** It does not define a narrower `ProductUnitOfMeasure`.

| Option | Assessment |
|---|---|
| Two enum classes, one per permitted set | Duplicates five of six members. `BillOfMaterials` arithmetic crosses product and item units and would need a conversion between two Python types representing one concept. Rejected |
| One class, six members, narrowing enforced by `ck_product_unit_of_measure_allowed` | **Selected** |

**This is one place where the check-constraint approach is simpler than a shared type object would be.** Each column states exactly the set it permits — six values on `inventory_item`, five on `product` — so the narrowing needs no cast, no subtype, and no wider type to derive from. The difference between the two columns is visible by reading the two constraints, and the schema document's §37.3 records the same thing from the schema side.

**The accepted consequence, stated plainly:** assigning `UnitOfMeasure.BOX` to a `Product` is caught by SQLite at flush, not by the type checker at edit time. That is a real loss of static safety on one column. It is accepted because the alternative is two near-identical types that a reader must diff to understand, and because the frozen model deliberately expressed this as one vocabulary with one column narrowed rather than as two vocabularies.

### 40.4 Database binding

Every enum column's binding is configured identically. **Six settings, and two of them are correctness requirements rather than preferences.**

| Setting | Value | Consequence if wrong |
|---|---|---|
| Python base | `str` and `enum.Enum` (a string enum) | Members compare equal to their string values, which is convenient in logs and query filters |
| Member names | `UPPER_SNAKE_CASE` | Python convention; distinct from the values |
| Member values | **The exact string in the schema document's §37.6**, character for character | A mismatch is a rejected insert or a silently unmatched filter |
| `native_enum` | **`False`** | SQLite has no enum type. `True` relies on the dialect's fallback rather than stating the intent, and reads as though a type object existed |
| `create_constraint` | **`False`** | `True` emits SQLAlchemy's own unnamed `IN (...)` check *in addition to* the named `ck_<table>_<column>_allowed` the schema declares — two constraints for one rule, the extra one unnameable in a migration and producing the worse diagnostic |
| `values_callable` | **Mandatory** | See below |
| `name` | The vocabulary name, matching the schema document's catalogue entry | Not used for DDL here, but it is what Alembic prints and what identifies which catalogue entry the class came from |

**`schema=` is not passed.** It has no meaning in a single-file database, and §13 explains what replaced it.

**`values_callable` is not optional, and omitting it is the single most likely defect in this layer.**

By default, SQLAlchemy persists a Python enum by its **member name**, not its value. With members named `UPPER_SNAKE_CASE` and values holding the schema's lower-case strings, the default behaviour sends `IN_SERVICE` to a column whose check constraint permits only `in_service`. Every enum insert fails, on all 100 columns.

`values_callable` instructs SQLAlchemy to persist member **values** instead. It must be supplied on all 100 bindings. It is stated here, in the per-model Enum Usage subsections, and in §45's standards list, because it fails loudly on first insert and confusingly on code review.

**A note on the rendered column type, so it is not mistaken for a discrepancy.** With `native_enum=False`, SQLAlchemy renders the column as `VARCHAR(n)`, where *n* is the longest member value. The schema document declares these columns `TEXT`. **In SQLite the two are the same column:** both are given TEXT affinity, neither is length-enforced, and both store the string exactly as written. The declared name differs; the storage, the comparisons, and the check constraint do not. Nothing in the schema document's §37 depends on the spelling, and §39.5 states the same equivalence for `VARCHAR(n)` generally.

**Why `str` enums rather than plain `Enum`.** A string enum's members are usable directly in log messages, comparison against raw strings from an external source, and JSON document construction, without an explicit `.value`. That removes a class of small errors at no cost, since the value is still what persists.

**Where the named constraint is declared.** In the model's `__table_args__`, as a `CheckConstraint` with the schema document's exact name and exact value list — not generated from the Python class. The two are written from one source (§37.6 of the schema document) and verified against each other, rather than one being derived from the other, because a derived constraint would silently follow a mistaken Python member into the database.

### 40.5 Migration sequencing

**There is no type-creation step, and that removes the entire ordering problem a server database has here.** A vocabulary is a check constraint on a column, so it comes into existence with its table and disappears with it. The order is therefore short:

1. Create the 53 tables, in dependency order, each with its columns, its `CHECK (column IN (...))` vocabulary constraints, and its foreign keys.
2. Create the indexes, including the 8 unique indexes — 7 partial, 1 expression-based (§37.9).

No schemas to create, no 65 types to create first, and no migration that fails because a table referenced a type that did not exist yet.

**Widening a vocabulary is the operation that costs something.** SQLite cannot alter a check constraint in place: `ALTER TABLE` can add a column, rename a column, rename a table, and drop a column, and nothing else. Adding a value therefore requires the standard table-rebuild sequence, in a hand-written migration:

```sql
PRAGMA foreign_keys = OFF;
BEGIN TRANSACTION;
CREATE TABLE operational_alert_new ( ... widened CHECK ... );
INSERT INTO operational_alert_new SELECT * FROM operational_alert;
DROP TABLE operational_alert;
ALTER TABLE operational_alert_new RENAME TO operational_alert;
-- recreate every index that belonged to the table
PRAGMA foreign_key_check;
COMMIT;
PRAGMA foreign_keys = ON;
```

**Three obligations in that sequence, each easy to miss.** Foreign key enforcement must be off for the rebuild, because `DROP TABLE` on a referenced parent would otherwise fail — and it must be re-enabled afterwards, per connection, as §42.9 requires. `PRAGMA foreign_key_check` must run before the commit, because violations introduced while enforcement was off are not reported any other way. And indexes are dropped with the table and must be recreated explicitly.

**Alembic's batch mode does most of this**, and §44.5 records how. `op.batch_alter_table(..., recreate="always")` performs the create-copy-drop-rename cycle, which is why every migration touching a constraint in this project is written in batch mode rather than as a bare `op.alter_column`.

**A shared vocabulary is widened once per table that uses it.** Adding a `PlatformComponent` value rebuilds 26 columns across 26 tables. That is the cost of a vocabulary being a per-column constraint, and it is accepted because §40.8 records that these vocabularies are stable reference data whose values change rarely.

### 40.6 When a constrained vocabulary is deliberately not an enum

Four cases in the frozen schema look like enum candidates and are correctly typed otherwise. **The ORM must not "improve" any of them.**

| Column | Type | Why not an enum |
|---|---|---|
| `machine_parameter.unit_of_measure` | `VARCHAR(16)` | **Units are an open set.** A new instrument may introduce `Pa`, `dB`, or `µm`. The value is displayed rather than compared, so there is no integrity benefit |
| `inventory_item.abc_class` | `CHAR(1)` + check | Three single-character values. `CHECK (abc_class IN ('A','B','C'))` is the same mechanism at lower cost, and the values are conventionally letters |
| `country_code`, `currency_code` | `CHAR(2)`, `CHAR(3)` + check | ISO 3166 and ISO 4217 — hundreds of values, maintained externally, revised independently. A format check is enforced; membership is not |
| **Severity** | `failure_severity_level`, a **table** | Each level carries behaviour: response time, line-stop requirement, escalation flags, acknowledgement deadline, display colour. A vocabulary can hold none of that |

**The rule: a vocabulary becomes an enum class when it is closed, small, stable, and compared.** Open sets, externally maintained standards, display-only values, and vocabularies that carry attributes stay as text with a format check, or become tables.

**Severity being a table rather than an enum is the most consequential of the four**, because it is why 11 foreign keys point at `failure_severity_level` and why `severity_rank` exists as the comparison axis.

**A second-order consequence worth naming.** Because `abc_class`, `country_code`, and `currency_code` are enforced by check constraints and so is every enum column, the four cases above and the 100 enum columns use the *same* database mechanism. The distinction is entirely a Python-side one: the 100 have an enum class and a `Mapped[EnumClass]` annotation; these four are `Mapped[str]` and the check is the whole of their enforcement. The line is drawn on whether the set is closed enough to name in Python, not on what the database does.

### 40.7 Module organisation

There is no type object to place, so there is nothing to organise physically — every vocabulary constraint lives on the column it constrains, in the one database file. What is left to organise is the Python side.

**Module organisation mirrors the logical groups of §13**, so a reader looking for a class knows where it is without searching:

| Module | Classes |
|---|---|
| `enums/master.py` | 30 |
| `enums/operational.py` | 31 |
| `enums/system.py` | 4 |
| `enums/__init__.py` | Re-exports all 65 |

Within a module, classes are ordered alphabetically. Members within a class are ordered **as the schema document's §37.6 lists them, not alphabetically** — the order carries no database meaning in SQLite, since no ordinal is stored and comparison is on the string, but keeping the two documents in the same order is what makes them diffable by eye. Reordering a Python class would make a reviewer's job harder for no gain.

**This is a place where SQLite is more forgiving than a server database, and the specification says so rather than leaving the reader to wonder.** A native enum type stores an ordinal, so member order is part of the type's semantics and reordering is a data migration. Here the stored value is the string, so order is presentation only. The prohibition on reordering (§40.8) is therefore a documentation-hygiene rule here rather than a correctness rule — which is the *only* respect in which this approach is looser, and it is stated so that no one reads §40.8's "never" as protecting something it no longer protects.

**The enums package imports nothing but the standard library.** It is layer 1 with base and metadata (§16), and it is the only part of the ORM that could be extracted and reused with no dependencies at all.

### 40.8 Maintenance and future extension

**Vocabularies are additive only.** This is the frozen schema's rule and the ORM inherits it.

| Operation | How |
|---|---|
| Add a value | A **hand-written** migration performing a batch table rebuild per affected table (§40.5), plus the Python member, plus an `audit_log` entry |
| Remove a value | **Never.** A value is retired by ceasing to write it, exactly as master rows are retired by `is_active` |
| Reorder values | **Never.** Not because SQLite would notice — it stores no ordinal — but because the Python class and the schema document's catalogue are kept in one order so they can be compared (§40.7) |
| Rename a value | **Never.** It would invalidate historical rows, and correcting them would mean rewriting append-only evidence |

**Alembic does not autogenerate vocabulary changes.** Autogenerate compares tables and columns; a widened `IN (...)` list inside a check constraint is not something it renders, and on SQLite the change is a table rebuild rather than an in-place alteration in any case. Every vocabulary change is hand-written in batch mode, and this is accepted rather than worked around: a vocabulary that resists casual change is a feature in a schema whose severity scale is effectively immutable and whose master data is never deleted.

**Adding a value to a shared vocabulary is a cross-cutting change.** The checklist is: add the Python member, rebuild every table carrying a column bound to that vocabulary, review every column bound to it, and review the business logic of every one. For `MaintenanceSpecialization` that is 5 columns across 4 models plus the team-matching logic; for `PlatformComponent` it is 26 columns across 26 tables.

**One thing this design makes cheap, for balance.** Because there is no type object, a vocabulary used by exactly one column can be widened by rebuilding exactly one table — no coordination, no shared object other columns depend on, no risk of a widening reaching a column that was not meant to accept the new value. 60 of the 65 vocabularies are in that position.

---
---

# Part X — Validation Strategy

---

## 41. Validation strategy

### 41.1 The four layers, and what belongs in each

The frozen schema declares roughly 285 check constraints, 58 unique constraints, 8 unique indexes, ~430 `NOT NULL` columns, and 163 foreign keys. **The ORM duplicates none of them.**

| Layer | Owns | Count | Failure mode |
|---|---|---|---|
| **SQLite** | Nullability, ranges, formats, text lengths, controlled vocabularies, boolean domains, cross-column rules within a row, uniqueness, referential integrity | ~1,000 declarative constraints | `IntegrityError` at flush. **Applies to every writer, including seed scripts and migrations** |
| **ORM** | Input normalisation; timezone-awareness; immutability of append-only fields | ~3 categories, applied narrowly | `ValueError` at attribute assignment, before any SQL |
| **Writing component** | Cross-table rules, state machines, temporal rules across rows, live-count checks | ~20 rules the schema names | Application error before the write is attempted |
| **Reading component** | Interpretation — staleness thresholds, rule scope resolution, `is_active` filtering | Judgement, not validation | Wrong answer, not an error. Which is why these are documented per model |

**The governing principle: a rule lives in exactly one place.** A rule enforced in two places is a rule that will disagree with itself after the first change to either copy, and the copy that is wrong will be the one nobody checked.

**The database layer carries more weight here than it would against a server database, and that is deliberate.** Types do not enforce anything in SQLite — a declared type sets an affinity, not a domain — so rules a server design would have expressed as a type are check constraints instead: 101 vocabulary membership checks, 45 boolean domain checks, and ~145 text length checks. **None of them is new; all of them changed layer** (§39.2, and the schema document's §33). The ORM's share of the work is unchanged by that, which is the point of stating it: the ORM's three categories are the same three either way.

**One caveat that belongs in this table rather than a footnote.** Of the layers above, referential integrity is the only one that can be switched off: foreign keys are enforced per connection and only when `PRAGMA foreign_keys = ON` is set. Every other constraint in the layer is always active. §42.9 makes the pragma a lifecycle requirement rather than a tuning option for exactly this reason.

### 41.2 Why the ORM does not mirror the check constraints

The temptation is real — mirroring gives friendlier errors and fails earlier. It is rejected for four reasons.

**Two authorities drift.** `ck_plant_operating_days_range` allows 1–7. If the business later permits an eight-day cycle, the constraint changes in a migration and the ORM copy does not, and the ORM rejects a value the database would accept. The failure is a correct value refused by the wrong layer.

**The ORM is not the only writer.** Seed scripts, administrative edits, migrations, and the retention job all write. A rule enforced only in the ORM is enforced for one writer out of several, and the schema's ownership model exists precisely so that guarantees do not depend on which code path was used.

**Mirroring ~285 constraints is ~285 hand-written validators.** Each is a place to make a transcription error, and a validator that is subtly wrong is worse than none because it inspires confidence. The figure is larger than a server-database design's would be — length limits and vocabularies are constraints here rather than types — which makes the argument stronger rather than weaker.

**The database's error already names the rule.** `ck_plant_area_ambient_range` is a more precise diagnosis than a generic range message, because it names the exact constraint the frozen schema documents.

**What the ORM does instead:** it normalises input so that values which are semantically correct but formatted differently are accepted rather than rejected. That is complementary to the constraints, not a duplicate of them.

### 41.3 ORM validation — the three permitted categories

**Category 1 — Input normalisation.** Applied where a check constraint would otherwise reject a semantically correct value.

| Normalisation | Applied to |
|---|---|
| Upper-case and strip | All 27 `<table>_code` business keys, `country_code`, `currency_code`, `abc_class`, `display_color_hex`, `serial_number`, `asset_tag`, `model_number`, `cost_center_code`, `drawing_revision` |
| Lower-case and strip | `worker.email`, `department.escalation_email`, `supplier.contact_email`, `customer.contact_email` |
| Strip only | `first_name`, `last_name`, and free-text names — **no case change**, because a person's name is not the ORM's to reformat |

**Category 2 — Timezone-awareness.** A naive `datetime` assigned to any `DATETIME` attribute is rejected. Applied to every event-time column and to the mixin timestamps.

The reason it is ORM-level rather than database-level: **SQLite has no date/time type and no concept of a zone at all.** A `DATETIME` column stores whatever string it is given, so a naive datetime is accepted, written, and read back looking exactly as legitimate as a correct one. There is no offset in the stored value to disagree with and nothing for a check constraint to test — a well-formedness check cannot distinguish `2026-07-31 06:00:00` meaning 06:00 UTC from the same text meaning 06:00 local. The corruption is invisible, and its consequence — readings crossing shift boundaries, hours misattributed — surfaces weeks later as a shift-report anomaly.

**This is the one validation where the ORM catches something the database structurally cannot, and the storage layer makes it more load-bearing rather than less.** Two obligations follow and both belong here:

- **Reject naive input at assignment.** The value never reaches the database, so the check has to be at the boundary.
- **Re-attach UTC on the way out.** SQLite stores no offset, so a value read back is naive. The `DateTime` type's result processing returns a naive `datetime`, and the ORM must attach `timezone.utc` so the Python-side contract stated in §38.3 holds in both directions. The `TimestampTz` alias names that contract (§38.4); it does not name a database capability.

**Category 3 — Immutability of append-only fields.** §41.4.

**Everything else is explicitly excluded**, including several things that look like natural ORM work:

| Excluded | Belongs to |
|---|---|
| Range and format checks | Database |
| Cross-column rules within a row | Database |
| `timezone` being a valid IANA name | **ORM, exceptionally** — see below |
| Cross-table rules | Writing component |
| State machine transitions | Writing component |
| Temporal rules across rows | Writing component |
| Live-count checks | Writing component |

**`plant.timezone` is the one exception to "no value validation in the ORM".** It cannot be a check constraint: SQLite carries no timezone catalogue to validate against, and a `CHECK` expression must be deterministic. The frozen schema states the validation is an application responsibility **and that it is mandatory**, because an invalid IANA name silently corrupts every shift-window calculation in the platform. One validator, one column, one row — and the highest-value hook in the layer.

**The cross-table and stateful rules the frozen schema names**, recorded here so no implementer looks for them in the ORM:

| Rule | Owner |
|---|---|
| A `maintenance_engineer` cannot be retired while `is_team_lead = 1` | Administrative writer |
| A `supplier` cannot be retired while an active item names it as primary source | Administrative writer |
| `email_enabled = 1` requires a non-NULL `worker.email` | Notification Service |
| `machine_type_parameter` normal range within the parameter's physical bounds | Administrative writer |
| `production_line.commissioned_date >= plant.commissioned_date` | Administrative writer |
| `reading_value` within the parameter's physical bounds | Simulator |
| Maintenance due-date computation from interval basis and live counters | Simulator |
| `assigned_engineer` belongs to `assigned_maintenance_team` | Simulator |
| Engineer specialization matches the confirmed failure category | Simulator |
| `prediction_result.machine_id` matches its snapshot's | Prediction Agent |
| Recipient's `min_severity_level` satisfied before composing | Notification Service |
| `actioned_by` holds a role with the relevant authority flag | Dashboard |
| `required_inventory_item_id` present when the root cause requires a spare part | Decision Agent |
| Cumulative quantity monotonicity across progress snapshots | Simulator |
| `resulting_quantity_on_hand` equals previous balance plus delta | Simulator |
| `open_alert_count` and `event_count` reconciliation | Monitoring Agent |
| Legal status transitions on run, work record, and alert | Respective owner |
| Heartbeat staleness interpretation | Reading component |

### 41.4 Immutable fields

**16 of 24 operational models are append-only.** Their absence of `updated_at` is the schema's statement of immutability; the ORM enforces it at the attribute level so a violation fails in Python rather than being silently persisted.

**Full immutability — every mapped attribute rejects reassignment once the instance is persistent.** 16 models:

`MachineSensorReading`, `MachineStateTransition`, `ProductionProgress`, `CycleHistory`, `QualityInspectionResult`, `ScrapRecord`, `InventoryMovement`, `MachineMaintenanceActivity`, `OperationalEvent`, `PredictionFeatureSnapshot`, `PredictionResult`, `SupervisorContext`, `AiRecommendation`, `RecommendationAction`, `Notification`, `AuditLog`.

**Partial immutability — identity and provenance columns are frozen; a named mutable set is permitted.** 8 models:

| Model | Immutable after insert | Mutable |
|---|---|---|
| `MachineOperationalStatus` | `machine_id` | State, counters, timestamps, `open_alert_count` |
| `ProductionRun` | `production_run_code`, `product_id`, `production_line_id`, `product_line_capability_id`, `customer_id` | Status, priority, actual timestamps, reasons |
| `ProductionCount` | `machine_id`, `interval_from`, `interval_to` | All count columns (rebuild) |
| `MaintenanceWorkRecord` | `maintenance_work_record_code`, `machine_id`, `work_type`, `opened_at` | Status, assignment, lifecycle timestamps, durations, resolution |
| `OperationalAlert` | `operational_alert_code`, `correlation_key`, `alert_category`, `initial_severity_level_id`, `opened_at`, `first_event_at` | `current_severity_level_id`, `alert_status`, `event_count`, `last_event_at`, lifecycle timestamps |
| `NotificationDelivery` | Everything except the three below | `delivery_status`, `delivered_at`, `latency_ms` |
| `DashboardSnapshot` | `snapshot_at`, `snapshot_scope`, `production_line_id`, `machine_id` | `snapshot_document`, timing columns (rebuild) |
| `SystemHealthStatus` | `component` | Status, heartbeat, counters, error fields, metrics |

**Master models are not immutable.** Administrative correction is legitimate, and retirement is `is_active = 0`. Four master columns are immutable **by convention rather than by hook**, and the distinction is deliberate: `product_line_capability.cycle_time_seconds`, `machine_parameter.machine_parameter_code`, `alert_threshold_profile` version rows, and `business_rule` value columns are never edited once operational data references them — but correcting a data-entry error before anything references them is legitimate, and a hook would prevent it.

**Why immutability is ORM-level, and what that honestly does and does not guarantee.** A database could prevent an `UPDATE` only with a trigger, and the frozen schema declares **zero triggers** because the ownership model requires that no database-resident code write on a component's behalf. So the hook is the mechanism.

**On `audit_log`, this specification claims less than a server-database design could.** SQLite has no roles and no `GRANT`, so there is no way to make the table physically `INSERT`-only for one writer and not another: any process holding the file can update or delete any row in it. Immutability of the audit trail is therefore **enforced by the ORM hook and by convention, not by the engine** — and this is stated plainly rather than left to be discovered, because the audit trail is the one table whose value depends entirely on not having been edited.

What compensates, and what does not:

| Control | What it actually gives |
|---|---|
| `@validates` immutability hook on `AuditLog` | Rejects reassignment through the ORM. **The only writer this covers is the ORM** |
| `created_by_component` on all 24 operational models | Provenance, so an unexpected write is attributable after the fact |
| Filesystem permissions on the database file | The real access boundary. A process that cannot open the file cannot write anything (§4.1) |
| Periodic backup of the file | Detection of tampering by comparison, and recovery from it |

**The honest summary: the ORM hook makes accidental mutation impossible and deliberate mutation easy.** For this platform's threat model — a single-operator desktop deployment where the adversary is a bug, not a person — that is sufficient, and it is the same trade the schema document's §50.2 records for every other privilege-based guarantee.

**Two behaviours the immutability hooks enable, both of which are the platform's design rather than the ORM's:**

- **A scrap reversal is a compensating record**, never an edit.
- **A change of mind about a recommendation is a new `RecommendationAction` row**, so the decision sequence stays visible.

### 41.5 What validation failure looks like

| Layer | Raised as | When | Rollback needed |
|---|---|---|---|
| ORM normalisation / timezone / immutability | `ValueError` | At attribute assignment | No — nothing was sent |
| Database `NOT NULL`, check, unique, foreign key | `IntegrityError` | At flush | **Yes.** The transaction is aborted and the session must roll back |
| Writing component rule | Component's own error | Before the write | No |

**A single `IntegrityError` aborts the whole transaction, not the one statement.** On a boundary that writes several models — T-SIM-2, T-SIM-7, T-SIM-8, T-MON-1, T-PRED-1 — a constraint violation on the last insert discards the earlier ones. That is correct and is why the boundaries are defined as they are, but it means a component cannot "continue past" a rejected row within a boundary; it must roll back and retry the boundary.

**The two expected `IntegrityError`s in normal operation**, both from partial unique indexes and both retried rather than treated as bugs: `uq_production_run_active_per_line` when two Simulator instances race to schedule a line, and `uq_oa_open_correlation_key` when two Monitoring Agent evaluations race to open an alert for the same condition. §42.8 explains why this is preferred to serialisable isolation.

---
---

# Part XI — Session & Transaction Strategy

---

## 42. Session and transaction strategy

### 42.1 Session ownership

**One session per transaction boundary, created by the component that owns the boundary, closed by it.**

| Property | Value | Rejected alternative |
|---|---|---|
| Session type | Synchronous `Session` | `AsyncSession` — nothing in the frozen documents describes an async runtime |
| Creation | A `sessionmaker` factory, bound to one `Engine` | Per-call construction — loses configuration consistency |
| Scope | One of the 20 boundaries in §24 | Session-per-request or per-component-lifetime — accumulates identity-map entries and holds connections |
| Registry | Explicit session objects | `scoped_session` — thread-local implicit sessions make ownership ambiguous, which conflicts with the single-writer model |
| Sharing | **Never across components or threads** | — |

**Why `scoped_session` is rejected specifically.** It provides an implicit, thread-local session that any code can reach. That is convenient in a web request cycle and wrong here: the frozen schema's ownership model says each table has exactly one writing component, and an ambiently available session makes it impossible to tell from a call site which component's boundary a write belongs to. Explicit sessions make the boundary visible in the code that opens it.

### 42.2 Unit of work

Within a boundary, the session is a unit of work: objects are added or loaded and mutated, and the session determines the SQL and its order at flush.

**Two consequences worth stating.**

**Assign relationships, not foreign key integers.** Setting `status.last_state_transition = transition` lets SQLAlchemy order the insert before the update and populate the key itself. Setting `status.last_state_transition_id = transition.machine_state_transition_id` requires the transition to have been flushed already, so the caller must sequence it manually. Both work; the first is the standard and the second is the fallback where no ORM object is at hand.

**Insert order within a boundary is SQLAlchemy's, not the caller's.** It sorts by dependency, which is what makes T-SIM-7 and T-SIM-8 — each writing three models with foreign keys between them — a single `add_all` and one commit.

### 42.3 Transaction lifecycle

| Phase | What happens |
|---|---|
| Begin | Implicit on first database use — SQLAlchemy emits `BEGIN` for the session's transaction. `begin()` is used explicitly where the boundary is a block |
| Autoflush | On, so a query within the boundary sees pending writes |
| Flush | Emits SQL. Identity values return as the driver's **last inserted rowid**. Server defaults are applied by SQLite |
| Commit | One `COMMIT` per boundary. Flushes first |
| Rollback | `ROLLBACK` on any exception. Mandatory after `IntegrityError` |
| Close | Returns the connection to the pool |

**SQLite transactions are fully ACID, and the guarantee is the same one a server database gives.** A `COMMIT` is durable once it returns, a `ROLLBACK` leaves the file exactly as it was, and a process killed mid-transaction leaves a database that recovers to its last committed state on next open. The mechanism is a journal — the write-ahead log in this platform's configuration (§42.8) — rather than a server process, and the guarantee does not weaken as a result.

**Isolation is effectively serialisable, and it is not a choice this layer makes.** SQLite has one isolation level: a write transaction holds an exclusive write lock for its duration, so no two write transactions interleave and no boundary can observe another boundary's partial work. There is no `READ COMMITTED` to select and no `SERIALIZABLE` to opt into — the stronger behaviour is the only behaviour. §42.8 records what that means for the platform's concurrency, which is less than it sounds: the ownership model already meant no two components wrote the same row.

**Identity values arrive by a different route than a server database would use, and one consequence follows.** There is no `RETURNING` clause in the flush path for these models; the driver reports the rowid of the row it just inserted, and SQLAlchemy populates the primary key attribute from it. Because that mechanism reports **one** row at a time, a multi-row `INSERT` that needs its generated keys back has to be executed as separate statements. This is exactly why §43.7 specifies the telemetry bulk insert as a single multi-row statement that **declines** to fetch keys: nothing references a reading by identity at insert time, so the round trips are avoidable.

**`BEGIN IMMEDIATE` for the two racing boundaries.** SQLite's default `BEGIN` is deferred: the write lock is not taken until the first write, so a transaction that reads, decides, and then writes can find another writer took the lock in between and fail late with `SQLITE_BUSY`. For T-SIM-2 and T-MON-1 — the two boundaries where two instances of the same component can genuinely race (§42.8) — the transaction is begun with `BEGIN IMMEDIATE`, which acquires the write lock up front and converts a late failure into an early wait. Everywhere else the default is correct and cheaper.

### 42.4 Flush strategy

| Setting | Value |
|---|---|
| `autoflush` | `True` (default) |
| Explicit `flush()` | Only where a generated key is needed mid-boundary and no relationship assignment is available |
| Flush order | SQLAlchemy's dependency sort |
| Bulk insert on `machine_sensor_reading` | Batched, **declining to fetch generated keys** (§43.7) |

**The one boundary that historically needs an explicit flush is T-SIM-2**, and assigning the relationship attribute removes even that. It is documented in `MachineOperationalStatus`'s Notes because it is the first thing an implementer will hit.

### 42.5 `expire_on_commit=False`

**Set `False` on the session factory.** The default is `True`.

| With `True` (default) | With `False` (selected) |
|---|---|
| Every attribute of every object expires at commit | Attributes remain readable after commit |
| The next attribute access emits a `SELECT` | No SQL after commit |
| Combined with `raise_on_sql`, post-commit logging **raises** | Post-commit logging works |
| A component that commits and then reads what it wrote pays a second round trip | It does not |

**The trade-off, stated.** A long-lived object may hold values another transaction has since changed. It is accepted because sessions here are short and scoped to one boundary, because table ownership means no two components write the same row, and because a component that must know a value is current re-queries rather than reusing a detached object.

**This setting and `raise_on_sql` are a pair.** With `expire_on_commit=True`, any attribute access on a committed high-volume object would attempt SQL and hit the raise. Choosing one without the other produces a layer that fails on ordinary code.

### 42.6 Commit and rollback

**One commit per boundary. Never a commit inside a loop over rows.**

| Situation | Handling |
|---|---|
| Boundary completes | One `commit()` |
| Any exception | `rollback()`, then propagate |
| `IntegrityError` from an expected partial unique index | `rollback()`, then retry the boundary — this is normal operation, not a bug |
| Partial success within a boundary | **Not possible, by design.** The boundary is atomic |
| Bulk telemetry insert | One commit per batch. The batch **is** the boundary |

**A per-row commit on `machine_sensor_reading` would be 87,000 transactions a day** where the boundary specifies batches. It is called out because it is the natural thing to write and the wrong thing to write.

### 42.7 Session scope and the 20 boundaries

Each boundary in §24 maps to one session scope. Grouped by owner:

| Owner | Boundaries | Character |
|---|---|---|
| Simulator | T-SIM-1 … T-SIM-8 | High frequency. T-SIM-1 is batched; the rest are small multi-model writes |
| Monitoring Agent | T-MON-1, T-MON-2, T-MON-3 | Two models each. T-MON-1 and T-MON-2 differ only in whether the alert already exists |
| Prediction Agent | T-PRED-1 | Snapshot then prediction, atomic — the prediction's foreign key to the snapshot is `NOT NULL` |
| Supervisor Agent | T-SUP-1 | One model |
| Decision Agent | T-DEC-1 | One model |
| Notification Service | T-NOT-1, T-NOT-2, T-NOT-3 | One model each. T-NOT-3 is the only `UPDATE` |
| Dashboard | T-DASH-1, T-DASH-2 | One model each |
| Retention job | T-RET-1 | Deletes only, child-first, in explicit dependency order |

**T-RET-1 is the boundary where §22's no-cascade rule is load-bearing.** It performs the only deletions in the system, and its explicit ordering is the only deletion ordering. An ORM-side `delete-orphan` anywhere would introduce a second, uncoordinated ordering.

### 42.8 Thread safety and concurrency

| Concern | Position |
|---|---|
| `Session` thread safety | **A session is not thread-safe.** One session per thread, never shared |
| `Engine` and connection pool | Thread-safe and shared. One engine per process |
| Model classes | Stateless and shareable. Instances belong to their session |
| `scoped_session` | Not used (§42.1) |
| Row-level locking | **Does not exist in SQLite.** Locking is whole-database, and there is no `SELECT ... FOR UPDATE` to write |
| Application-level locks | **Not used.** The database's own write lock is the serialisation point |
| Isolation level selection | **Nothing to select.** Write transactions are serialised by the engine (§42.3) |
| ORM optimistic concurrency (`version_id_col`) | **Not used** — no version column exists in the frozen schema |

**SQLite's concurrency model, stated plainly because every position above follows from it.** One writer at a time, database-wide. A write transaction takes an exclusive write lock on the whole file; a second write transaction cannot start until the first commits or rolls back. Readers are not blocked by the writer in WAL mode, and the writer is not blocked by readers. There is no row-level or table-level granularity to reason about, so a design that would need it would need a different database.

**This is a good fit for FactoryFlow AI rather than a limitation it works around.** The platform's write volume is one component writing telemetry in batches plus six components writing occasionally, on one desktop machine. Its concurrency requirement is *correctness under occasional overlap*, not throughput under sustained parallel writes. The single-writer model delivers the first for free.

**Three settings make the model workable, and all three are engine configuration rather than model configuration.**

| Setting | Value | Why |
|---|---|---|
| `PRAGMA journal_mode = WAL` | Set once; **persists in the file** | Readers proceed during a write instead of blocking. Without it the dashboard stalls whenever the Simulator commits a telemetry batch |
| `PRAGMA synchronous = NORMAL` | Set per connection | With WAL this is durable across process crashes; only a host power loss can lose the most recent commits. `FULL` costs an `fsync` per commit for a guarantee this platform does not need |
| `connect_args={"timeout": 30}` | Per connection | The driver's busy timeout. A writer that finds the lock held retries for up to 30 seconds instead of raising `SQLITE_BUSY` immediately |

**`journal_mode` is the one pragma that persists**, so it is set at database creation and does not need re-issuing per connection. `synchronous` and the busy timeout do not persist and are applied on every connection, alongside `foreign_keys` (§42.9).

**Why no application locking is needed, and this is structural rather than fortunate.** Single-component ownership per table means no two *components* ever contend for the same row. The only genuine concurrency risk is two *instances* of the same component racing, and in each such case a unique index converts the race into a clean unique violation the application retries — with the engine's write lock already making the interleaving that would corrupt the decision impossible.

**A unique violation names exactly which invariant was hit**, whereas a busy-timeout expiry reports only that someone else held the lock. `uq_production_run_active_per_line` and `uq_oa_open_correlation_key` are the two indexes doing this work (§37.9), and §42.3 records why those two boundaries begin with `BEGIN IMMEDIATE`.

**Two failure modes to expect, and what each means.**

| Error | Cause | Handling |
|---|---|---|
| `IntegrityError` on a unique index | Two instances of one component raced; the loser's row is genuinely a duplicate | Roll back, retry the boundary. Normal operation (§42.6) |
| `OperationalError: database is locked` | The busy timeout expired — another writer held the lock for over 30 seconds | **Not normal.** It means a transaction is far longer than any boundary in §24 specifies, and the boundary that held it is the bug |

**No `version_id_col` is added.** It would require a column the frozen schema does not have, which would be a schema change, which is forbidden. The ownership model and the single-writer engine make it unnecessary in any case.

### 42.9 `PRAGMA foreign_keys = ON`

**This is the single most consequential operational requirement in this specification, and it is stated as its own subsection so it cannot be skimmed past.**

All 163 foreign keys — the 162 `RESTRICT` actions that protect the evidence trail and the one `SET NULL` on `operational_event.triggering_reading_id` — are declared correctly in the models and **enforce nothing unless this pragma is set on the connection performing the write.**

| Property | Consequence |
|---|---|
| **Off by default** | A connection that has not executed it enforces no referential integrity |
| **Per connection, not per database** | Setting it on one connection says nothing about the next one the pool hands out |
| **Does not persist in the file** | Unlike `journal_mode`, it cannot be set once at creation |
| **A no-op inside a transaction** | Issuing it after a transaction has begun silently does nothing |
| **Fails silently either way** | There is no error when it is missing. Orphan rows are simply accepted |

**The implementation is a connection-level event hook, not a startup statement.**

```python
@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA synchronous = NORMAL")
    cursor.close()
```

The hook fires on **every** new DBAPI connection, before any transaction on it, which is exactly the two conditions the pragma requires. Issuing it once after `create_engine` would cover the first connection and no other.

**What breaks without it, concretely.** A retention purge deletes readings that `operational_event.triggering_reading_id` still references, and instead of the `SET NULL` the schema specifies, the events keep pointing at rows that no longer exist. A master row is deleted despite 82 inbound references. The five-hop citation chain from a recommendation back to the reading that triggered it develops holes. **None of this raises an error**, in the ORM or in the database, and none of it is visible until an agent follows a reference and finds nothing.

**Two places the pragma is deliberately disabled, and both re-enable it.** A vocabulary-widening table rebuild (§40.5), because `DROP TABLE` on a referenced parent would otherwise fail; and a full teardown between test runs. In both cases `PRAGMA foreign_key_check` runs before the commit, because violations introduced while enforcement was off are not reported any other way.

**`PRAGMA foreign_key_check` is the verification tool** and belongs in the project's test setup rather than only in migrations. It reports every row violating a foreign key across the whole database, which makes it the correct assertion after any bulk load, any restore, and any operation performed with enforcement off. It detects damage; it does not prevent it.

**Everything in §22, §37, and §41.1 is conditional on this pragma.** The `RESTRICT` chain, the no-cascade position, and the referential-integrity row of the constraint-layer table are all declared correctly and all inert without it.

---
---

# Part XII — Performance Strategy

---

## 43. Performance strategy

> **Scope note.** This part states **strategy**. No optimisation is implemented, no index is designed, and no query is written. Index design belongs to a later phase informed by measured plans; the 8 unique indexes in §37.9 are correctness constraints that SQLite happens to implement as indexes.

### 43.1 Where the rows are

Four tables hold roughly 94 % of all rows in the database, and every performance decision in this specification follows from that distribution.

| Model | Rows/year | Share | Loading policy |
|---|---|---|---|
| `MachineSensorReading` | ~32,000,000 | ~91 % | `raise_on_sql` on all 4 relationships |
| `CycleHistory` | ~1,300,000 | ~3.7 % | `raise_on_sql` on all 3 |
| `AuditLog` | ~730,000 | ~2.1 % | `raise_on_sql` on its 1 |
| `ProductionCount` | ~580,000 | ~1.7 % | `raise_on_sql` on all 3 |
| `ProductionProgress` | ~140,000 | | `select` |
| `DashboardSnapshot` | ~105,000 | | `select` |
| `MachineStateTransition` | ~73,000 | | `select` |
| `PredictionFeatureSnapshot` / `PredictionResult` | ~62,000 each | | `select` |
| All other operational models | under 25,000 each | | `select` |
| All 29 master models | 1–~105 rows total each | | `select` |

**The four largest tables are read in loops by agents.** That single fact produces Rule L4 (§19.4), `expire_on_commit=False` (§42.5), and the batched-insert position below.

### 43.2 Relationship loading — the three defences against N+1

Applied in order of strength.

| Defence | Mechanism | Effect |
|---|---|---|
| **1. Absence** | Unbounded reverse collections are not mapped (Rule L1) | The misuse is impossible, not merely detectable |
| **2. `raise_on_sql`** | 11 many-to-one attributes on the four largest models | An accidental traversal raises immediately in development |
| **3. Explicit per-query loading** | `selectinload`, `joinedload`, `contains_eager` chosen by the caller | The caller states its intent, and the intent is visible in the query |

**Why absence is the strongest.** `lazy="raise"` on `Machine.sensor_readings` would prevent the 4-million-row load, but the attribute would still exist, still appear in autocomplete, and still appear in a reviewer's model of the class. Not mapping it removes the question.

**`raise_on_sql` rather than `raise` is deliberate.** It permits the load when the parent is already in the identity map — the common case for `Machine`, `Shift`, `MachineParameter`, and `FailureSeverityLevel`, all of which have single-digit or low-double-digit row counts — and raises only for a genuine round trip. So it blocks the N+1 without obstructing ordinary code.

### 43.3 Large collections

**Roughly 40 reverse collections that would otherwise exist are not mapped.** The complete rationale is Rule L1 (§19.1); the pattern to recognise is a small parent table with a large child table.

| Parent | Rows | Largest unmapped child | Child rows/year |
|---|---|---|---|
| `Shift` | 4 | `machine_sensor_reading` | ~32,000,000 |
| `Machine` | 8 | `machine_sensor_reading` | ~32,000,000 |
| `MachineParameter` | 7 | `machine_sensor_reading` | ~32,000,000 |
| `ProductionRun` | ~2,000 | `machine_sensor_reading` | ~32,000,000 |
| `FailureSeverityLevel` | 5 | `operational_event` | ~15,000 |
| `Worker` | ~13 | `audit_log` | ~730,000 |

**The replacement is always the same shape:** a filtered, ordered, limited query against the child model. "The latest progress snapshot for this run" is `ORDER BY snapshot_at DESC LIMIT 1` on `ProductionProgress`, not a traversal of a 70-element collection to read one row.

### 43.4 Read-heavy models

The 29 master models are read by every component on nearly every operation and total a few hundred rows.

| Property | Consequence |
|---|---|
| Small and stable | Effectively resident in any session's identity map after first touch |
| Read by all eight components | Many-to-one traversals to master rows are usually free |
| Never written by an agent | No invalidation concern within a boundary |

**This is why `raise_on_sql` is tolerable.** The parents it guards — `Machine`, `Shift`, `MachineParameter` — are almost always already loaded, so the guard fires only on a genuine round trip, which is precisely the case worth blocking.

**Caching is deliberately not specified.** No second-level cache, no `Query.cache_key` scheme, no application-level dictionary of master rows. The identity map already provides per-session caching, master data fits in memory trivially, and a cross-session cache would introduce an invalidation problem where none currently exists. **If profiling later shows master lookups are hot, that is the moment to add caching** — not before.

### 43.5 Write-heavy models

| Model | Write pattern | Boundary |
|---|---|---|
| `MachineSensorReading` | ~87,000 inserts/day, batched | T-SIM-1 |
| `CycleHistory` | ~3,500 inserts/day | T-SIM-3 |
| `ProductionCount` | ~1,600 inserts/day at interval close | T-SIM-4 |
| `AuditLog` | ~2,000 inserts/day from all components | Its own path |
| `MachineOperationalStatus` | 8 rows updated continuously | T-SIM-2 |

**`MachineOperationalStatus` is the only continuously updated model in the database**, and it is 8 rows. There is no lock contention because the Simulator is its sole writer.

### 43.6 Identity map and memory

| Concern | Position |
|---|---|
| Identity map growth | Bounded by session scope. A boundary that reads a million rows holds a million objects |
| Bulk reads | Should not load full ORM objects. Select the columns needed |
| `yield_per` | Available for streaming a large result without materialising all of it |
| Session lifetime | Short, per boundary. The strongest memory control available |
| `expunge` / manual eviction | Not specified. Closing the session is the mechanism |

**The specific risk.** A Prediction Agent reading 90 days of `cycle_history` for one machine is roughly 160,000 rows. As full ORM objects that is a large identity map for a task that needs three columns. **The instruction is to select columns, not entities, for bulk numerical work** — which also sidesteps the `Decimal` cost noted in §38.2, because the conversion becomes a deliberate local choice.

### 43.7 Bulk operations

| Operation | Approach |
|---|---|
| Telemetry insert | One multi-row `INSERT` per batch, inside one transaction. **Decline to fetch generated keys** — nothing references a reading at insert time |
| `production_count` rebuild | Idempotent upsert on (`machine_id`, `interval_from`), via SQLite's `INSERT ... ON CONFLICT DO UPDATE` |
| `dashboard_snapshot` rebuild | Idempotent, guarded by the `COALESCE`-based `uq_ds_scope_subject_time` expression index (§37.9) |
| Retention purge | Bulk `DELETE` by time range, **child-first in dependency order**, executed as Core statements rather than ORM object deletion |
| Master seeding | Ordinary ORM inserts in dependency order. Volume is trivial |

**Fetching generated keys on the telemetry insert is the difference between one statement and 87,000.** SQLAlchemy's default is to fetch them, and on SQLite that default is expensive in a specific way: the driver reports one last-inserted rowid per statement, so getting a key back for every row means one `INSERT` per row rather than one statement for the batch (§42.3). Declining the keys is what allows the whole batch to be a single statement, and nothing references a reading by identity at insert time.

**The batch is the transaction, and that is where most of the cost actually is.** SQLite's per-commit cost is a durability operation, not a network round trip: committing per row would mean 87,000 journal syncs a day where the boundary specifies batches. One `BEGIN`, one multi-row `INSERT`, one `COMMIT`. This is the single largest performance decision in the layer, and it comes from the transaction model rather than from query planning.

**`ON CONFLICT DO UPDATE` is used rather than a read-then-write.** SQLite supports it directly, and it makes the `production_count` rebuild a single statement whose idempotency is enforced by `uq_pc_machine_interval` rather than by the caller having checked first. A read-then-write would be two statements with a window between them — narrow, given the single-writer model, but pointless to leave open when the engine offers the atomic form.

**The purge uses Core `DELETE`, not ORM deletion.** ORM deletion would load each row to delete it, and there is nothing to cascade because no relationship declares one (§22). Core statements also make the child-first ordering explicit in the job's code, which is where it belongs.

### 43.8 Future optimisation, and what is deliberately deferred

Stated so that none of it is attempted prematurely.

| Available later | Trigger |
|---|---|
| Performance indexes beyond the correctness ones | **Measured query plans**, read with `EXPLAIN QUERY PLAN`. Anticipated indexes cost writes and often serve no query |
| Covering indexes on the telemetry read paths | A measured plan showing a table lookup after an index seek on `machine_sensor_reading` |
| `PRAGMA cache_size` and `mmap_size` tuning | Measured page-cache pressure. Both are per-connection settings, applied in the same hook as §42.9 |
| Archiving `machine_sensor_reading` to a separate database file | Measured purge duration or file growth. SQLite has no partitioning; **attaching a second file for cold telemetry is the equivalent move**, and the frozen schema's retention policy is what makes it possible |
| The same treatment for `cycle_history`, `audit_log`, `dashboard_snapshot` | Secondary candidates, in that order |
| A second-level cache for master data | Profiling showing master lookups are hot. Unlikely — 29 tables of 1 to ~105 rows sit in the page cache already |
| Async sessions | A stated concurrency requirement. Nothing in the frozen documents describes one, and a single-writer engine gains little from one |
| Derived tables in the analytics group | The group is reserved and empty by design. SQLite has no materialised views, so a derived structure there would be an ordinary table with a rebuild job — the same shape as `dashboard_snapshot` |

**Four things a server-database version of this section would list are simply not available, and they are named so that no one goes looking.** There is no table partitioning, no read replica, no materialised view, and no query-planner hint. What replaces the first two is the retention policy already in the frozen schema, plus WAL mode letting the Dashboard read while the Simulator writes (§42.8). What replaces the third is a rebuilt table. There is no replacement for the fourth, and none is needed: SQLite's planner has few enough choices that `EXPLAIN QUERY PLAN` output is short enough to read in full.

**The position on all of these: nothing is optimised without a measurement.** The four things this specification *does* do for performance — not mapping unbounded collections, `raise_on_sql` on the four largest models, declining generated keys on the telemetry insert, and making the batch the transaction — are not optimisations. They are defences against known, structural N+1, round-trip, and commit-frequency patterns whose cost is derivable from the row counts in §43.1 without profiling.

---
---

# Part XIII — Package Organization

---

## 44. Package organization

### 44.1 Structure

Eight concerns, each in its own module or package. The tree below is an **ASCII diagram of organisation**, not a file listing and not code.

```
models/
├── base.py                  Declarative base: one MetaData with naming
│                            convention, type_annotation_map resolving the
│                            17 Annotated aliases
│
├── types.py                 The 17 Annotated type aliases (§38.4)
│
├── enums/
│   ├── __init__.py          Re-exports all 65 classes
│   ├── master.py            30 classes
│   ├── operational.py       31 classes
│   └── system.py            4 classes
│
├── mixins.py                SoftDeleteMixin, TimestampCreatedMixin,
│                            TimestampUpdatedMixin, ComponentProvenanceMixin
│
├── master/
│   ├── __init__.py          Re-exports the 29 model classes
│   ├── plant.py             Plant, PlantArea, Department, Shift
│   ├── production.py        ProductionLine, Product, ProductLineCapability
│   ├── equipment.py         MachineCategory, MachineType, Machine
│   ├── parameters.py        MachineParameter, MachineTypeParameter
│   ├── people.py            WorkerRole, Worker, MaintenanceTeam,
│   │                        MaintenanceEngineer, NotificationRecipient
│   ├── inventory.py         InventoryLocation, InventoryItem,
│   │                        BillOfMaterials, Supplier
│   ├── commercial.py        Customer
│   ├── failure.py           FailureSeverityLevel, FailureCategory,
│   │                        MachineTypeFailureMode
│   ├── maintenance.py       MachineMaintenanceSchedule
│   └── thresholds.py        AlertThresholdProfile, AlertThresholdRule,
│                            BusinessRule
│
├── operational/
│   ├── __init__.py          Re-exports the 22 model classes
│   ├── telemetry.py         MachineSensorReading, MachineOperationalStatus,
│   │                        MachineStateTransition
│   ├── production.py        ProductionRun, ProductionProgress,
│   │                        ProductionCount, CycleHistory
│   ├── quality.py           QualityInspectionResult, ScrapRecord
│   ├── inventory.py         InventoryMovement
│   ├── maintenance.py       MaintenanceWorkRecord,
│   │                        MachineMaintenanceActivity
│   ├── events.py            OperationalEvent, OperationalAlert
│   ├── prediction.py        PredictionFeatureSnapshot, PredictionResult
│   ├── decision.py          SupervisorContext, AiRecommendation,
│   │                        RecommendationAction
│   ├── notification.py      Notification, NotificationDelivery
│   └── dashboard.py         DashboardSnapshot
│
├── system/
│   ├── __init__.py          Re-exports the 2 model classes
│   ├── audit.py             AuditLog
│   └── health.py            SystemHealthStatus
│
├── registry.py              Imports all 53 models. Alembic's target
│
└── session.py               Engine and sessionmaker configuration
```

### 44.2 Why grouped modules rather than one file per model

53 single-model files would be 53 imports to trace and 53 places where a related pair is separated. Grouping by **cohesive subject** keeps models that are read together in one place — `OperationalEvent` and `OperationalAlert` are always read together, and so are `PredictionFeatureSnapshot` and `PredictionResult`.

| Option | Assessment |
|---|---|
| One file, all 53 models | Several thousand lines. No navigation. Rejected |
| One file per model, 53 files | Correct-looking and tedious. Separates pairs that are never read apart. Rejected |
| **Grouped by subject, mirroring the schema documents' own groups** | **Selected.** 10 master modules, 10 operational modules, 2 system modules |

The grouping follows the frozen schema documents' own section groups, so a reader moving between the schema document and the ORM finds the same neighbours in both.

### 44.3 Module responsibilities

| Module | Owns | Depends on |
|---|---|---|
| `base.py` | The single `MetaData`, naming convention, `type_annotation_map` | SQLAlchemy only |
| `types.py` | 17 Annotated aliases | SQLAlchemy only |
| `enums/` | 65 enum classes | Standard library only |
| `mixins.py` | 4 mixins | `base`, `types`, and `PlatformComponent` |
| `master/`, `operational/`, `system/` | 53 model classes, their table arguments, relationships, and `@validates` hooks | `base`, `types`, `enums`, `mixins` |
| `registry.py` | Importing all 53 so mappers configure and `MetaData` is complete | All model packages |
| `session.py` | `Engine`, `sessionmaker` with `expire_on_commit=False` | `base` |

**Nothing above the ORM appears anywhere in this tree.** No agent, no simulator, no dashboard, no configuration loader. Rule R4 (§16).

### 44.4 What each module must not do

| Module | Must not |
|---|---|
| `base.py` | Declare any column. §11 |
| `enums/` | Import a model, a mixin, or SQLAlchemy's ORM |
| `mixins.py` | Import a model, or reference any enum but `PlatformComponent` |
| Model modules | Import another model at module scope. Relationship targets are strings; annotations use `TYPE_CHECKING` |
| `registry.py` | Contain logic. It imports and re-exports, nothing else |
| `session.py` | Be imported by a model |

### 44.5 Alembic's view

Alembic imports `registry.py` and targets the single `MetaData` on `base.py`. It sees all 53 tables, all columns, all constraints, and all indexes — in one namespace, with nothing to qualify and no per-group target to configure.

**The first migration is short because there is nothing to create before the tables.** No schemas, no 65 types, no ordering trap (§40.5). Tables in dependency order, then the 8 unique indexes, and the database exists.

**`render_as_batch=True` is set in `env.py`, and it is not optional.** SQLite's `ALTER TABLE` can add a column, rename a column, rename a table, and drop a column — nothing else. Every other alteration is a create-copy-drop-rename rebuild. Alembic's batch mode performs that rebuild, and without `render_as_batch` autogenerate emits `op.alter_column` calls that SQLite rejects at run time rather than at generation time.

```python
context.configure(
    connection=connection,
    target_metadata=target_metadata,
    render_as_batch=True,
)
```

**What it does not see, and must therefore be hand-written:**

| Hand-written | Why |
|---|---|
| Widening a vocabulary's `IN (...)` value list | Autogenerate compares tables, columns, and constraint *presence*; it does not diff a check expression. On SQLite the change is a table rebuild in any case (§40.8) |
| Any constraint alteration | The batch rebuild is generated, but the intent has to be written. `recreate="always"` is stated explicitly where a rebuild is required |
| Index recreation after a rebuild | Indexes are dropped with the table. Batch mode recreates the ones in `MetaData`; anything created outside the models is lost |
| `PRAGMA foreign_keys = OFF` around a rebuild, and `PRAGMA foreign_key_check` before the commit | Migration-time concerns, not `MetaData` content (§40.5, §42.9) |
| `PRAGMA journal_mode = WAL` at database creation | A one-time durable setting on the file (§42.8) |

**The one thing that no longer needs hand-writing, recorded because it was the largest hand-written block a server-database version of this layer would have carried:** the 65 type creations and every `ALTER TYPE ... ADD VALUE`. A vocabulary is a check constraint on a column, so it is created with its table and appears in autogenerate output like any other constraint. What replaced that block is the batch-mode requirement above — less code, but a setting that must not be forgotten.

**Migrations run with foreign keys enabled, like everything else.** `env.py` uses the same engine configuration as the application, including the connection hook from §42.9, so a migration that would orphan a row fails during the migration rather than leaving damage for `foreign_key_check` to find later. The only exception is the deliberate rebuild window.

---
---

# Part XIV — Implementation Standards

---

## 45. Implementation standards

### 45.1 Naming — the complete rule set

| Element | Convention | Example |
|---|---|---|
| Model class | PascalCase of the table name, mechanically per segment | `machine_sensor_reading` → `MachineSensorReading` |
| Table name | **Exactly the frozen schema's.** Never changed | `bill_of_materials` |
| Column attribute | **Exactly the frozen schema's column name** | `shift_id_opened`, not `opened_shift_id` |
| Many-to-one relationship | The target's role, singular, FK suffix dropped | `production_line_id` → `production_line` |
| One-to-many relationship | The child's role, plural | `Plant.plant_areas` |
| One-to-one, both sides | Singular | `Machine.operational_status` |
| Role-qualified relationship | The role, not the target | `attributed_machine`, `warning_severity_level` |
| Enum class | PascalCase of the vocabulary name | `area_type` → `AreaType` |
| Enum member | UPPER_SNAKE_CASE | `IN_SERVICE` |
| Enum value | **Exactly the string in the check constraint** | `'in_service'` |
| Annotated alias | PascalCase, named for the precision role | `Measurement`, `Probability` |
| Mixin | PascalCase ending in `Mixin` | `ComponentProvenanceMixin` |
| Constraint / index | **Exactly the frozen schema's name** | `ck_plant_code_format` |

**Mechanical PascalCase has no exception list.** `ai_recommendation` becomes `AiRecommendation`, not `AIRecommendation`. `bill_of_materials` becomes `BillOfMaterials`, and the plural-looking `materials` is not "corrected" because the table name is the frozen schema's.

### 45.2 Relationship naming — dropping the `_id` suffix

A many-to-one relationship is the foreign key column's name with `_id` removed. `machine_id` → `machine`. `attributed_machine_id` → `attributed_machine`. `shift_id_opened` → `shift_opened`, because the suffix is not terminal and mechanical removal would give `shift_opened` either way.

**The foreign key attribute and the relationship attribute both exist and are both mapped.** `MachineSensorReading` has both `machine_id` (an `int`) and `machine` (a `Machine`). Both are needed: bulk operations set the integer, and traversal uses the relationship. Suppressing either would force a workaround somewhere.

### 45.3 Declaration order within a model class

Fixed, so all 53 files read identically.

1. Class docstring — one line stating the table and its purpose
2. `__tablename__`
3. `__table_args__` — unique constraints, check constraints, then unique indexes. **No `{"schema": ...}` entry**, on any of the 53 models (§12)
4. Primary key
5. Business key, where the table has one
6. Foreign key columns, in the frozen schema's order
7. Business columns, in the frozen schema's order
8. Relationships — owning many-to-one first, then one-to-many and one-to-one reverses
9. `@validates` hooks

**Mixin-supplied columns are never restated.** They arrive by composition and appear in the class line, which is where a reader learns whether a model is append-only.

### 45.4 Column declaration standards

| Standard | Rule |
|---|---|
| Type and nullability | **From the annotation only.** `nullable=` is never passed |
| Repeated precision | **Always via an Annotated alias.** No bare `Numeric(p, s)` anywhere |
| Timestamps | Always `TimestampTz`. Never bare `DateTime` |
| Server defaults | Always `server_default`. **Never** Python-side `default` |
| Boolean defaults | `server_default=text("1")` or `text("0")` — the values SQLite stores (§29) |
| `created_at` / `updated_at` | `server_default=text("CURRENT_TIMESTAMP")` for insert; ORM `onupdate` for update on the 37 models with `updated_at` |
| `updated_at` | `server_default` for insert, ORM `onupdate` for update |
| Foreign keys | Bare target — `ForeignKey("machine.machine_id")`, never qualified. `ondelete` stated explicitly on all 163 |
| Enum bindings | All six settings from §40.4, `values_callable` included, on all 100 |
| Vocabulary check constraints | Declared by name in `__table_args__`, matching the schema document exactly. Never generated from the Python class (§40.4) |
| Text length checks | A named `CHECK (length(col) <= n)` for every `VARCHAR(n)` column and `= n` for every `CHAR(n)` column (§39.5) |
| `JSON` columns | Whole-document replacement only. `MutableDict` is **not** applied, so an in-place edit is silently discarded (§39.6) |
| Identity keys | Via `MasterPk` or `OperationalPk`. Never declared inline, and never `BigInteger` (§39.3) |

### 45.5 Relationship declaration standards

| Standard | Rule |
|---|---|
| Target | **Always a string.** Never a class reference |
| Annotation type | Imported under `TYPE_CHECKING` |
| `back_populates` | On **both** sides of every bidirectional relationship |
| `backref` | **Never used.** It creates an attribute the target's own file does not mention |
| `lazy` | **Always stated explicitly**, even where the value equals the default |
| `uselist=False` | On the parent side of all five one-to-one relationships |
| `foreign_keys` | Stated on every relationship where the model holds two or more keys to the same target — all nine cases in §37.6 |
| `cascade` | Left at the default. `delete-orphan` never used |
| `secondary` | **Never used.** All five junctions are association objects |
| `viewonly` | Never used |
| `order_by` | Not specified on any relationship. Ordering is the caller's |

**`lazy` is stated even when redundant** because a reviewer scanning 223 relationships should not have to remember which default applies to which cardinality. An explicit `select` and an explicit `raise_on_sql` are then visually comparable.

### 45.6 Validation hook standards

| Standard | Rule |
|---|---|
| Permitted categories | Only the three in §41.3: normalisation, timezone-awareness, immutability |
| Never | Range checks, format checks, cross-column rules, cross-table rules, state machines |
| Placement | Last in the class body, after all relationships |
| Failure | `ValueError` with a message naming the attribute and the reason |
| Immutability | Raises on reassignment when the instance is persistent — not when it is pending |

### 45.7 Import ordering

Four groups, alphabetical within each, with the guard last.

1. Standard library — `datetime`, `decimal`, `Enum`, `typing`
2. SQLAlchemy — core, then `orm`, then `dialects.sqlite` where a dialect type is required
3. Intra-package — `base`, `types`, `enums`, `mixins`
4. `TYPE_CHECKING` block — relationship target classes only

**Group 4 is the only place a model names another model**, and it never executes at runtime.

### 45.8 Documentation style

| Element | Requirement |
|---|---|
| Model class docstring | One line: the table name, its logical group, and its purpose in a clause |
| Enum class docstring | One line: the vocabulary it enforces and the check constraint that binds it |
| Mixin docstring | One line: the columns it supplies and which models compose it |
| Column note | A **Python source comment** on the column declaration, only where the frozen schema's Notes carry something a reader could not infer — for example `machine_state_at_reading` being denormalised deliberately, or `triggering_reading_id` being the single `SET NULL` |
| Redundant comments | **Omitted.** A comment restating a column's name is noise on 512 columns |
| Cross-references | Cite this document's section numbers, so a reader can find the reasoning |

**Descriptive text lives in the source and in these documents, never in the database.** SQLite has no `COMMENT ON` statement and no object-comment catalogue, so SQLAlchemy's `comment=` argument has no DDL to render on this dialect — it would be accepted and produce nothing. It is therefore **not used on any column or table**, and the equivalent information is a source comment or a docstring, which is where a developer reads it anyway.

**One consequence worth stating rather than discovering.** A server-database design can carry column documentation inside the database, so a `\d+` or catalogue query explains the schema to someone who has only a connection. Here, the schema explains itself through **named constraints** instead: `.schema` output shows `ck_machine_monitored_requires_profile` and the check expression beside the column, which is a different kind of documentation and in some respects a more reliable one, because it cannot drift from the rule it describes. What it cannot carry is business intent, and that is what these documents are for.

**The frozen documents remain the authority for business meaning.** The ORM's docstrings identify and locate; they do not re-explain what `FACTORY_MASTER_DATA_DESIGN.md` and `FACTORY_OPERATIONAL_DATA_DESIGN.md` already state.

### 45.9 Consistency rules — the review checklist

Fourteen checks. Each is mechanically verifiable, and each corresponds to a defect this specification exists to prevent.

| # | Check |
|---|---|
| 1 | 53 model classes, one per table, none merged or split |
| 2 | Every table name, column name, and constraint name matches the frozen schema exactly |
| 3 | No bare `Numeric(p, s)` or `DateTime(...)` — all via Annotated aliases |
| 4 | `nullable=` passed nowhere; nullability comes from the annotation |
| 5 | All 100 enum bindings carry `values_callable`, `native_enum=False`, and `create_constraint=False`. **No binding passes `schema=`** |
| 6 | All 163 foreign keys state `ondelete` explicitly, with **unqualified** target table names |
| 7 | All 8 unique indexes present — the 7 partial ones with their `sqlite_where` predicates, and `uq_ds_scope_subject_time` over its `COALESCE` expressions |
| 8 | Every relationship target is a string; no model imports a model outside a `TYPE_CHECKING` guard |
| 9 | Every bidirectional relationship declares `back_populates` on both sides; `backref` appears nowhere |
| 10 | `secondary=` and `delete-orphan` appear nowhere |
| 11 | Every `mapped_column` server default matches the frozen schema's declared default exactly — `1`/`0` for booleans, `CURRENT_TIMESTAMP` for timestamps |
| 12 | `BigInteger` appears nowhere. Every whole-number column is `Integer` (§39.3) |
| 13 | Every `VARCHAR(n)` column has a named `length(col) <= n` check, and every `CHAR(n)` column a `length(col) = n` check (§39.5) |
| 14 | The engine issues `PRAGMA foreign_keys = ON` from a `connect` event hook, not once at startup (§42.9) |

**Check 5 is the one most likely to fail during implementation**, and it fails on all 100 columns at once on the first insert if `values_callable` is missed (§40.4).

**Check 14 is the one most likely to fail in production**, and it is the more dangerous of the two because **nothing fails.** A missing `values_callable` raises on the first insert; a missing pragma accepts orphan rows quietly for as long as the mistake survives. It is verified by asserting `PRAGMA foreign_keys` returns `1` on a connection drawn from the pool — not on a connection created for the test.

---
---

# Part XV — Model Summary

---

## 46. Model summary

### 46.1 Totals

| Metric | Count |
|---|---|
| **Total ORM models** | **53** |
| Master models (M1–M29) | 29 |
| Operational models (O1–O24) | 24 |
| — in the operational group | 22 |
| — in the system group | 2 |
| Mapped columns | 512 |
| Foreign keys | **163** (§37.1) |
| Relationship attributes | **223** |
| Enum classes | **65**, across 100 columns |
| Declarative base | 1 |
| Mixins | 4, in 4 compositions |
| Association object models | 5 |
| One-to-one relationships | 5 |
| Annotated type aliases | 17 |
| Unique indexes | 8 — 7 partial, 1 expression-based |
| Transaction boundaries | 20, all reused from the frozen schema §46 |
| Logical groups populated | 3 of 4 — `analytics` is reserved and empty |
| Database files | **1** |
| `schema=` arguments in the entire layer | **0** |

### 46.2 Relationship breakdown

| Class | Count |
|---|---|
| Many-to-one (owning side of a foreign key) | 158 |
| One-to-one, child side | 4 |
| One-to-one, parent side (`uselist=False`) | 4 |
| One-to-many (mapped reverse collections) | 56 |
| Many-to-many via `secondary=` | **0** |
| Association object junctions | 5 |
| Unbounded reverse collections **not mapped** | ~40 |
| **Total mapped relationship attributes** | **223** |

`SystemHealthStatus`'s uniqueness on `component` is a one-to-one-style constraint against an enum, not a relationship, so it appears in §37.3 but not in this count.

### 46.3 Enum counts

| Group | Classes | Module | Notes |
|---|---|---|---|
| Master | 30 | `enums/master.py` | Used only by master models |
| Operational | 31 | `enums/operational.py` | Includes `MachineOperationalState`, spanning four columns |
| System | 4 | `enums/system.py` | Includes `PlatformComponent`, the most-used vocabulary at 26 columns |
| **Total** | **65** | | Bound to 100 columns |

Five vocabularies are shared across more than one column (§40.2). **Six** master models and **three** operational models have no enum column of their own: `Plant`, `MachineType`, `NotificationRecipient`, `BillOfMaterials`, `FailureSeverityLevel`, `AlertThresholdRule`, and `ProductionProgress`, `ProductionCount`, `PredictionResult`. The three operational ones still carry `created_by_component` from the provenance mixin, so their Enum Usage entry has one row rather than none.

**On the database side these 65 classes correspond to 100 named check constraints, not 65 objects.** A vocabulary has no independent existence in SQLite, so the count of Python classes and the count of database constraints differ by design — 65 definitions, 100 enforcements. §40.2 explains what keeps them consistent.

### 46.4 Shared base and mixins

| Composition | Mixins | Models | Count |
|---|---|---|---|
| Master standard | SoftDelete + Created + Updated | M1–M9, M11–M29 | 28 |
| Master machine | Created + Updated | `Machine` | 1 |
| Operational append-only | Created + Provenance | 16 operational models | 16 |
| Operational mutable | Created + Updated + Provenance | 8 operational models | 8 |
| | | **Total** | **53** |

**Four compositions, zero exceptions.** The base declares no columns (§11).

### 46.5 Largest models by volume

| Rank | Model | Rows/year | Share | Guard |
|---|---|---|---|---|
| 1 | `MachineSensorReading` | ~32,000,000 | ~91 % | `raise_on_sql` ×4, 4 unmapped reverses |
| 2 | `CycleHistory` | ~1,300,000 | ~3.7 % | `raise_on_sql` ×3 |
| 3 | `AuditLog` | ~730,000 | ~2.1 % | `raise_on_sql` ×1 |
| 4 | `ProductionCount` | ~580,000 | ~1.7 % | `raise_on_sql` ×3 |
| 5 | `ProductionProgress` | ~140,000 | | — |
| 6 | `DashboardSnapshot` | ~105,000 | | — |
| 7 | `MachineStateTransition` | ~73,000 | | — |
| 8= | `PredictionFeatureSnapshot` | ~62,000 | | — |
| 8= | `PredictionResult` | ~62,000 | | — |

**Smallest by row count and largest by value:** `AiRecommendation`, ~700 rows a year.

**Fixed-size operational models:** `MachineOperationalStatus` (8 rows) and `SystemHealthStatus` (~8 rows), both zero net growth.

### 46.6 Most connected models

By total mapped relationship attributes.

| Rank | Model | Attributes | FKs held | Mapped reverses |
|---|---|---|---|---|
| 1 | `AiRecommendation` | 13 | 11 | 2 |
| 2 | `MaintenanceWorkRecord` | 12 | 10 | 2 |
| 3 | `OperationalEvent` | 10 | 10 | 0 |
| 4 | `ScrapRecord` | 9 | 8 | 1 |
| 5= | `InventoryMovement` | 8 | 8 | 0 |
| 5= | `QualityInspectionResult` | 8 | 7 | 1 |
| 7= | `ProductionLine` | 7 | 2 | 5 |
| 7= | `ProductionRun` | 7 | 4 | 3 |
| 7= | `OperationalAlert` | 7 | 6 | 1 |
| 7= | `PredictionResult` | 7 | 7 | 0 |
| 7= | `SupervisorContext` | 7 | 6 | 1 |

**Most connected master model by foreign keys held: `MachineTypeFailureMode`, with five parents.** The connectivity is the point — it is the single join where equipment, failure taxonomy, severity policy, telemetry, and inventory meet.

**Least connected: `SystemHealthStatus`, with zero foreign keys and zero relationships.** `Customer` has one inbound reference and no mapped relationship.

### 46.7 Dependency graph

The graph is **acyclic**. Master depth is 4 layers, operational depth is 7. The diagram below shows layer assignment; it is an ASCII diagram, not code.

```
MASTER  —  L0 to L4, 46 internal edges, zero outbound
────────────────────────────────────────────────────────────────
L0  plant   product   machine_category   machine_parameter
    worker_role   supplier   customer   failure_severity_level
      │
L1  plant_area   department   shift   machine_type
    failure_category
      │
L2  production_line   inventory_location   maintenance_team
    alert_threshold_profile   machine_type_parameter
      │
L3  machine   product_line_capability   worker   inventory_item
    alert_threshold_rule   business_rule
      │
L4  maintenance_engineer   notification_recipient
    machine_maintenance_schedule   bill_of_materials
    machine_type_failure_mode


OPERATIONAL  —  L0 to L7, 36 internal edges, 81 edges into master
────────────────────────────────────────────────────────────────
L0  production_run   operational_alert   dashboard_snapshot
    audit_log   system_health_status
      │
L1  machine_sensor_reading   production_progress
    production_count   cycle_history
    prediction_feature_snapshot
      │
L2  operational_event   prediction_result
      │
L3  quality_inspection_result   supervisor_context
      │
L4  scrap_record   ai_recommendation
      │
L5  maintenance_work_record   notification
      │
L6  recommendation_action   inventory_movement
    machine_maintenance_activity   machine_state_transition
    notification_delivery
      │
L7  machine_operational_status


CROSS-GROUP  —  one direction only
────────────────────────────────────────────────────────────────
    operational  ──81 FKs──▶  master
    system       ──1 FK────▶  master
    master       ──0 FKs───▶  anything outside master
```

All three groups are tables in the same database file; the arrows are dependency direction, not a boundary the engine knows about (§13).

**Two properties of this graph carry the whole import strategy.**

**Master has zero outbound edges.** It can be created, seeded, validated, restored, and reasoned about entirely independently, and master models can always be imported first.

**No cycle exists at any depth.** The four candidate cycles the frozen schema designed out — `machine` ↔ `machine_operational_status`, `operational_alert` ↔ `operational_event`, the maintenance chain, and the double references from `bill_of_materials` and from `quality_inspection_result`/`scrap_record` — are all resolved as chains (§18). In Python this is reinforced structurally: all 53 models sit in one import layer with no edges between them, because every relationship target is a string.

### 46.8 Write ownership map

| Component | Models owned | Boundaries |
|---|---|---|
| Factory Simulator | 12 | T-SIM-1 … T-SIM-8 |
| Monitoring Agent | 2 | T-MON-1, T-MON-2, T-MON-3 |
| Prediction Agent | 2 | T-PRED-1 |
| Supervisor Agent | 1 | T-SUP-1 |
| Decision Agent | 1 | T-DEC-1 |
| Notification Service | 2 | T-NOT-1, T-NOT-2, T-NOT-3 |
| Dashboard | 2 | T-DASH-1, T-DASH-2 |
| Platform audit interface | 2 | Its own path |
| Administrative | 29 master models | Seed and edit |

**The Supervisor and Decision Agents own one model each**, which is the clearest available statement that their responsibilities are narrow.

**Ownership here is a convention, and the map is its only expression.** SQLite grants no per-table permissions, so nothing in the engine prevents a component from writing outside its column of this table. What makes a violation visible is `created_by_component` on all 24 operational models, check-constrained to the `platform_component` vocabulary — the map says what should write each model, and the column records what did (§6, and the schema document's §50.2).

### 46.9 ORM layer summary

**What this specification delivers.** 53 declarative models mapping 512 columns in **one SQLite database file**, with 163 foreign keys expressed as 223 typed relationship attributes, 65 enum classes bound to 100 vocabulary columns, 4 mixins in 4 exception-free compositions, 17 Annotated type aliases eliminating every bare precision, 8 unique indexes carried into table arguments, and 20 transaction boundaries reused verbatim from the frozen schema.

**What it deliberately does not deliver.** No repository classes. No service layer. No DTOs. No Pydantic. No business logic. No duplicated check constraints. No cascade deletes. No `secondary=` relationships. No caching. No optimisation without measurement. No abstraction that the frozen schema does not already imply.

**The three decisions that shape the layer most.**

**Unbounded reverse collections are not mapped at all.** Roughly 40 relationships that a conventional ORM design would include are absent, because `Shift.sensor_readings` on a four-row table is an eight-million-row attribute and no loading strategy makes that safe. Absence is the only strategy that cannot be misused.

**Four atomic mixins instead of a fat base.** `machine` carries `lifecycle_status` instead of `is_active`, and 16 of 24 operational models are append-only with no `updated_at`. A single audit mixin would need seventeen exceptions. Composition turns each of those exceptions into a visible fact in the class definition.

**SQLite keeps every guarantee the frozen schema declares.** ~285 check constraints, 58 unique constraints, 8 unique indexes, ~430 `NOT NULL` columns, and 163 foreign keys stay in the database. The ORM adds exactly three narrow validation categories — normalisation, timezone-awareness, immutability — and one exceptional value check on `plant.timezone`, which cannot be a constraint and whose absence would silently corrupt every shift-window calculation in the platform.

**Two of those guarantees carry a condition, and both are stated where they are relied on rather than only here.** The 163 foreign keys enforce nothing unless `PRAGMA foreign_keys = ON` is set on every pooled connection (§42.9). And immutability of `audit_log` is a hook plus a convention rather than an engine guarantee, because SQLite has no privileges to withhold `UPDATE` with (§41.4). Everything else in the list above applies to every writer unconditionally.

**Readiness.** Every model class, attribute, type, nullability, default, relationship, loading strategy, enum binding, and validation hook is specified. The implementation phase is transcription. Alembic generates from a single `MetaData` whose naming convention reproduces the frozen schema's constraint names, in batch mode (§44.5), with two hand-written elements the tool cannot see: a widened vocabulary value list, and the pragma sequence around a table rebuild. There are no schemas to create and no types to create before the tables.

**One discrepancy in a frozen input is recorded rather than silently reconciled.** Enumerating the schema document's own per-table `fk_*` listings yields **163** distinct constraint names, not the 166 an earlier revision of its §15 summary reported: three names — `fk_machine_production_line`, `fk_machine_alert_threshold_profile`, and `fk_oe_triggering_reading` — were counted both in a table's Constraints listing and again in a later discussion section. That document's §15 and §51.1 now both state 163. This specification follows the per-table listings, because those are what name each constraint, what determine how many relationships exist, and what Alembic must reproduce. Nothing was renamed, added, or removed as a result (§37.1).

---

**End of FACTORY_SQLALCHEMY_MODEL_SPECIFICATION.md**
