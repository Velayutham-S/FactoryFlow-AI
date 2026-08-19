"""The 29 master datasets, in the load order of FACTORY_MASTER_DATA_DESIGN.md §30.3.

Two things live here and nothing else: the authoritative load order, and the CSV
column contract for each dataset.

**The load order is written out explicitly** rather than derived from a topological
sort. §30.3 assigns every entity to a layer and that assignment is the
specification; a sort would produce *some* valid order, not *the* documented one,
and a future schema change could silently reorder the run. :func:`verify_load_order`
then checks the written order against the actual foreign key graph, so the two
authorities cannot drift apart unnoticed.

**The column contract is derived from the frozen ORM metadata**, not restated here.
Restating ~600 column definitions would duplicate the schema in a second place that
can disagree with it, which is precisely the failure the model specification argues
against in §41. Nullability, types, lengths, vocabularies, uniqueness and foreign
key targets are read from the mapped tables at import time, so this module cannot
describe a column the schema does not have.

Three rules define the contract:

* **Surrogate primary keys are absent from the CSV.** They are
  ``INTEGER PRIMARY KEY AUTOINCREMENT`` and SQLite assigns them.
* **``created_at`` and ``updated_at`` are absent.** They are system-maintained and
  carry ``CURRENT_TIMESTAMP`` server defaults.
* **Foreign keys are referenced by business code, never by surrogate id.** A CSV
  holding ``machine_type_id = 4`` is unreviewable and breaks the moment the file is
  loaded into a different database. The column ``<name>_id`` therefore becomes
  ``<name>_code`` in the CSV and is resolved against the parent's own code column
  during import. This is the shape the example records in
  FACTORY_MASTER_DATA_DESIGN.md already use -- ``MTY-VMC-500``, ``LN-01``,
  ``ATP-VMC-TIGHT``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import Table, UniqueConstraint

import models.registry  # noqa: F401  -- registers all 53 tables on the MetaData
from models.base import Base, metadata

from master_data.errors import LoadOrderError

# --------------------------------------------------------------------------
# Load order -- FACTORY_MASTER_DATA_DESIGN.md §30.3, layers 0 through 4.
#
# Layer 0 is unordered within itself ("Load first, in any order"); the sequence
# below follows the order the section prints. Every later layer references only
# entities in strictly lower layers, which is what makes the graph acyclic
# (§30.4).
# --------------------------------------------------------------------------
LAYERS: tuple[tuple[str, ...], ...] = (
    # Layer 0 -- roots, no dependencies (8)
    (
        "plant",
        "product",
        "worker_role",
        "machine_category",
        "machine_parameter",
        "failure_severity_level",
        "supplier",
        "customer",
    ),
    # Layer 1 -- direct children of roots (5)
    (
        "plant_area",
        "department",
        "shift",
        "machine_type",
        "failure_category",
    ),
    # Layer 2 -- structural and specification entities (5)
    (
        "production_line",
        "inventory_location",
        "maintenance_team",
        "alert_threshold_profile",
        "machine_type_parameter",
    ),
    # Layer 3 -- core assets, people and policy (6)
    (
        "machine",
        "worker",
        "inventory_item",
        "product_line_capability",
        "alert_threshold_rule",
        "business_rule",
    ),
    # Layer 4 -- specializations and cross-domain junctions (5)
    (
        "maintenance_engineer",
        "notification_recipient",
        "bill_of_materials",
        "machine_type_failure_mode",
        "machine_maintenance_schedule",
    ),
)

# Columns the loader never accepts from a file. Surrogate keys are SQLite's to
# assign; the two timestamps are the mixins' to maintain.
SYSTEM_COLUMNS: frozenset[str] = frozenset({"created_at", "updated_at"})

# CSV spellings that mean SQL NULL. The empty field is the normal form; the
# literal is accepted because the example records in the design document write it.
NULL_TOKENS: frozenset[str] = frozenset({"", "null"})


@dataclass(frozen=True)
class ForeignKeyRef:
    """One foreign key, expressed the way the CSV expresses it."""

    column: str
    """The real column, e.g. ``machine_type_id``."""

    csv_column: str
    """The CSV column carrying the parent's business code, e.g. ``machine_type_code``."""

    parent_table: str
    """The referenced table, e.g. ``machine_type``."""

    parent_code_column: str
    """The parent's own business code column, e.g. ``machine_type_code``."""

    required: bool
    """False when the foreign key column is nullable."""


@dataclass(frozen=True)
class DatasetSpec:
    """The complete contract for one master dataset."""

    table: str
    layer: int
    sequence: int
    model: type[Any]
    table_obj: Table
    code_column: str | None
    """The entity's own business code column, or None for the junction entities
    that have no code of their own (§3.4 lists 23 of 29 entities)."""

    value_columns: tuple[str, ...]
    """Non-key columns the CSV carries, in table order."""

    foreign_keys: tuple[ForeignKeyRef, ...]
    required_columns: frozenset[str]
    optional_columns: frozenset[str]
    unique_groups: tuple[tuple[str, ...], ...]
    """Unique constraints in real column terms, for duplicate detection.

    Real rather than CSV names because duplicates are checked after foreign key
    resolution, when a record holds ``machine_type_id`` and can be compared with
    what the database already stores. :meth:`csv_label` renders them back for
    messages."""

    partial_unique_indexes: tuple[str, ...]
    """Names of unique indexes carrying a WHERE clause. Left to SQLite: the
    predicate is SQL and cannot be evaluated against a pending Python row."""

    def csv_label(self, column: str) -> str:
        """The CSV column that supplies ``column``, for diagnostics."""
        for ref in self.foreign_keys:
            if ref.column == column:
                return ref.csv_column
        return column

    @property
    def filename(self) -> str:
        """``01_plant.csv`` -- the numeric prefix makes load order visible on disk."""
        return "%02d_%s.csv" % (self.sequence, self.table)

    @property
    def title(self) -> str:
        """``machine_type`` -> ``Machine Type``, for progress output."""
        return self.table.replace("_", " ").title()

    @property
    def csv_columns(self) -> tuple[str, ...]:
        """Every column the CSV may carry, in a stable order."""
        return tuple(sorted(self.required_columns | self.optional_columns))


def _model_for_table(table_name: str) -> type[Any]:
    for mapper in Base.registry.mappers:
        if mapper.local_table is not None and mapper.local_table.name == table_name:
            return mapper.class_
    raise LoadOrderError(
        "no mapped model for table %r; models.registry does not register it"
        % table_name
    )


def _build_spec(table_name: str, layer: int, sequence: int) -> DatasetSpec:
    table = metadata.tables[table_name]
    model = _model_for_table(table_name)

    code_column = "%s_code" % table_name
    if code_column not in table.columns:
        code_column = None  # type: ignore[assignment]

    foreign_keys: list[ForeignKeyRef] = []
    fk_columns: set[str] = set()
    for column in table.columns:
        if not column.foreign_keys:
            continue
        if len(column.foreign_keys) != 1:
            raise LoadOrderError(
                "%s.%s carries %d foreign keys; the loader assumes one"
                % (table_name, column.name, len(column.foreign_keys))
            )
        target = next(iter(column.foreign_keys)).column
        parent_table = target.table.name
        parent_code = "%s_code" % parent_table
        if parent_code not in target.table.columns:
            raise LoadOrderError(
                "%s.%s references %s, which has no %s column to reference it by"
                % (table_name, column.name, parent_table, parent_code)
            )
        if not column.name.endswith("_id"):
            raise LoadOrderError(
                "foreign key column %s.%s does not end in _id, so its CSV column "
                "name cannot be derived" % (table_name, column.name)
            )
        foreign_keys.append(
            ForeignKeyRef(
                column=column.name,
                csv_column="%s_code" % column.name[: -len("_id")],
                parent_table=parent_table,
                parent_code_column=parent_code,
                required=not column.nullable,
            )
        )
        fk_columns.add(column.name)

    value_columns: list[str] = []
    required: set[str] = set()
    optional: set[str] = set()
    for column in table.columns:
        if column.primary_key or column.name in SYSTEM_COLUMNS:
            continue
        if column.name in fk_columns:
            continue
        value_columns.append(column.name)
        # A server default makes the column optional in the file: omit it and the
        # database supplies the documented default rather than the loader guessing.
        if column.nullable or column.server_default is not None:
            optional.add(column.name)
        else:
            required.add(column.name)

    for ref in foreign_keys:
        if ref.required:
            required.add(ref.csv_column)
        else:
            optional.add(ref.csv_column)

    unique_groups: list[tuple[str, ...]] = []
    for constraint in table.constraints:
        if isinstance(constraint, UniqueConstraint):
            unique_groups.append(tuple(c.name for c in constraint.columns))

    partial_unique: list[str] = []
    for index in table.indexes:
        if not index.unique:
            continue
        where = index.dialect_options.get("sqlite", {}).get("where")
        if where is not None:
            partial_unique.append(index.name or "<unnamed>")
        else:
            unique_groups.append(tuple(c.name for c in index.columns))

    return DatasetSpec(
        table=table_name,
        layer=layer,
        sequence=sequence,
        model=model,
        table_obj=table,
        code_column=code_column,
        value_columns=tuple(value_columns),
        foreign_keys=tuple(foreign_keys),
        required_columns=frozenset(required),
        optional_columns=frozenset(optional),
        unique_groups=tuple(dict.fromkeys(unique_groups)),
        partial_unique_indexes=tuple(partial_unique),
    )


def master_datasets() -> tuple[DatasetSpec, ...]:
    """Every master dataset, in §30.3 load order."""
    specs: list[DatasetSpec] = []
    sequence = 0
    for layer, tables in enumerate(LAYERS):
        for table_name in tables:
            sequence += 1
            specs.append(_build_spec(table_name, layer, sequence))
    return tuple(specs)


def verify_load_order(specs: tuple[DatasetSpec, ...]) -> None:
    """Check the written order against the real foreign key graph.

    Three assertions: the order covers exactly the 29 master tables, every
    foreign key parent is loaded before its child, and no entity depends on one in
    its own layer or above -- the property §30.4 relies on for acyclicity.
    """
    failures: list[str] = []

    if len(specs) != 29:
        failures.append("load order lists %d datasets, expected 29" % len(specs))

    duplicates = [t for t in {s.table for s in specs} if
                  [s.table for s in specs].count(t) > 1]
    if duplicates:
        failures.append("duplicated in load order: %s" % ", ".join(sorted(duplicates)))

    position = {spec.table: spec.sequence for spec in specs}
    layer_of = {spec.table: spec.layer for spec in specs}

    for spec in specs:
        for ref in spec.foreign_keys:
            parent = ref.parent_table
            if parent not in position:
                failures.append(
                    "%s references %s, which is not in the master load order"
                    % (spec.table, parent)
                )
                continue
            if position[parent] >= spec.sequence:
                failures.append(
                    "%s (#%d) is loaded before its parent %s (#%d)"
                    % (spec.table, spec.sequence, parent, position[parent])
                )
            if layer_of[parent] >= spec.layer:
                failures.append(
                    "%s is in layer %d and depends on %s in layer %d; §30.4 "
                    "requires every dependency to sit in a strictly lower layer"
                    % (spec.table, spec.layer, parent, layer_of[parent])
                )

    if failures:
        raise LoadOrderError(
            "the load order does not satisfy the schema:\n  %s"
            % "\n  ".join(failures)
        )
