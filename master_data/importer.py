"""Importing one dataset: resolve references, build instances, insert as a batch.

The caller owns the transaction. :func:`import_dataset` runs inside a session it is
handed and never commits, so ``models.session.session_scope`` remains the single
commit-or-rollback boundary for the dataset. A failure anywhere in this module
propagates and that scope rolls the dataset back whole.

Order of operations, and why it is this order:

1. **Validate and coerce** every record against the schema. No database contact,
   so a malformed file costs nothing.
2. **Resolve foreign keys** from business code to surrogate id, one query per parent
   table.
3. **Construct model instances**, which is what runs the ORM's ``@validates``
   normalisation -- codes upper-cased, emails lower-cased, ``plant.timezone``
   checked against the IANA database. This is deliberately not
   ``session.execute(insert(Model), rows)``: passing dictionaries to a Core insert
   is faster and skips every validator, which would store un-normalised codes and
   let an invalid timezone through.
4. **Detect duplicates** using the values read back off the constructed instances,
   so the comparison sees the normalised form the database will actually hold.
5. **Insert as one batch** -- ``add_all`` then a single ``flush``. SQLAlchemy 2.0
   batches the INSERT statements; there is one flush per dataset, never one per row.

Steps 1 through 4 all complete before step 5 begins, so a dataset with any problem
is reported without a single row having been written.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from models.base import metadata

from master_data import duplicates
from master_data.csv_source import SourceRow
from master_data.datasets import DatasetSpec
from master_data.errors import (
    DatasetImportError,
    ForeignKeyResolutionError,
    RecordValidationError,
)
from master_data.validation import validate_row


@dataclass
class DatasetImport:
    """What happened to one dataset."""

    spec: DatasetSpec
    read: int = 0
    imported: int = 0
    rejections: list[str] = field(default_factory=list)

    @property
    def table(self) -> str:
        return self.spec.table


def _parent_code_map(spec: DatasetSpec, session: Session, parent_table: str,
                     code_column: str) -> dict[str, int]:
    """Map a parent's business code to its surrogate id.

    Keyed on the upper-cased code because the ORM upper-cases every code column on
    assignment, so that is the form the database holds and the form a file should be
    matched against regardless of how it was typed.
    """
    table = metadata.tables[parent_table]
    primary_key = list(table.primary_key.columns)[0]
    rows = session.execute(select(table.columns[code_column], primary_key))
    return {str(code).strip().upper(): int(key) for code, key in rows}


def _resolve_foreign_keys(
    spec: DatasetSpec,
    session: Session,
    validated: list[tuple[int, dict[str, Any]]],
) -> tuple[list[tuple[int, dict[str, Any]]], list[str]]:
    if not spec.foreign_keys:
        return validated, []

    maps = {
        ref.csv_column: _parent_code_map(
            spec, session, ref.parent_table, ref.parent_code_column
        )
        for ref in spec.foreign_keys
    }

    resolved: list[tuple[int, dict[str, Any]]] = []
    failures: list[str] = []
    for line, values in validated:
        record = {
            name: value
            for name, value in values.items()
            if name not in maps
        }
        ok = True
        for ref in spec.foreign_keys:
            code = values.get(ref.csv_column)
            if code is None:
                record[ref.column] = None
                continue
            key = maps[ref.csv_column].get(str(code).strip().upper())
            if key is None:
                failures.append(
                    "line %d, %s: %r does not match any %s.%s"
                    % (line, ref.csv_column, code, ref.parent_table,
                       ref.parent_code_column)
                )
                ok = False
                continue
            record[ref.column] = key
        if ok:
            resolved.append((line, record))
    return resolved, failures


def _instance_values(spec: DatasetSpec, instance: Any) -> dict[str, Any]:
    """Read every mapped column off a constructed instance.

    Columns left to a server default read as None before the flush, which is
    correct for duplicate detection: SQL compares NULL as distinct.
    """
    return {
        column.name: getattr(instance, column.name, None)
        for column in spec.table_obj.columns
    }


def import_dataset(
    spec: DatasetSpec,
    session: Session,
    source_rows: list[SourceRow],
    *,
    strict: bool = True,
) -> DatasetImport:
    """Validate, resolve, check and insert one dataset inside the caller's session.

    With ``strict`` set -- the default -- any rejected record aborts the dataset
    and nothing is written. A master table that is missing rows because some were
    rejected is not a working master table: its children fail to resolve, the
    §32.2 completeness checks fail, and the database looks populated while being
    incomplete. Loading the valid remainder and reporting the rest would leave
    exactly that state, so the dataset fails as a unit and every reason is listed.
    """
    report = DatasetImport(spec=spec, read=len(source_rows))

    validated: list[tuple[int, dict[str, Any]]] = []
    for row in source_rows:
        values, reasons = validate_row(spec, row)
        if values is None:
            report.rejections.extend(reasons)
            continue
        validated.append((row.line, values))

    if report.rejections and strict:
        raise RecordValidationError(spec.table, report.rejections)

    resolved, fk_failures = _resolve_foreign_keys(spec, session, validated)
    if fk_failures:
        report.rejections.extend(fk_failures)
        if strict:
            raise ForeignKeyResolutionError(spec.table, fk_failures)

    instances: list[Any] = []
    keyed: list[tuple[int, dict[str, Any]]] = []
    construction_failures: list[str] = []
    for line, values in resolved:
        try:
            instance = spec.model(**values)
        except (ValueError, TypeError) as exc:
            construction_failures.append("line %d: %s" % (line, exc))
            continue
        instances.append(instance)
        keyed.append((line, _instance_values(spec, instance)))

    if construction_failures:
        report.rejections.extend(construction_failures)
        if strict:
            raise RecordValidationError(spec.table, construction_failures)

    duplicates.check(spec, session, keyed)

    if not instances:
        return report

    try:
        session.add_all(instances)
        session.flush()
    except SQLAlchemyError as exc:
        raise DatasetImportError(
            "%s: insert failed and the dataset will be rolled back: %s"
            % (spec.table, exc)
        ) from exc

    report.imported = len(instances)
    return report
