"""Duplicate detection, performed before insertion.

Two passes per dataset: within the incoming records, and against what the table
already holds. Both run before anything is added to the session, so a duplicate is
reported as a duplicate rather than as an ``IntegrityError`` naming a constraint.

Relying on the constraint alone would be worse in three ways. SQLite reports the
first violation and stops, so a file with nine duplicates needs nine runs to clean
up. The report names the constraint, not the record, so the author still has to
find it. And a duplicate inside the incoming batch is indistinguishable from a
collision with existing data, which are different mistakes with different fixes.

Duplicates are never skipped and never overwritten. Skipping discards a record the
author meant to load; overwriting destroys one already loaded. Both are silent, so
neither is available: the dataset fails and every duplicate is named.

**Keys covered.** Every ``UNIQUE`` constraint and every non-partial unique index on
the table, which together are the business keys, the single-column unique keys and
the composite unique constraints. Primary keys need no check and are asserted
absent instead: the loader never supplies a surrogate key, so two records cannot
collide on one.

NULL is treated as distinct from NULL, matching SQL: a key tuple with a NULL
component is not compared. Partial unique indexes are left to SQLite, because their
predicate is SQL and cannot be evaluated against a record that does not exist yet.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from master_data.datasets import DatasetSpec
from master_data.errors import DuplicateRecordError


def assert_no_supplied_primary_key(spec: DatasetSpec) -> None:
    """Confirm the CSV contract exposes no primary key column.

    The guarantee behind "never manually generate AUTOINCREMENT values": if no
    primary key can be supplied, none can be duplicated or guessed.
    """
    keys = {column.name for column in spec.table_obj.primary_key.columns}
    exposed = keys & (spec.required_columns | spec.optional_columns)
    if exposed:
        raise DuplicateRecordError(
            spec.table,
            [
                "the dataset contract exposes primary key column(s) %s; SQLite "
                "assigns them and a file must not supply them"
                % ", ".join(sorted(exposed))
            ],
        )


def _key_of(values: dict[str, Any], group: tuple[str, ...]) -> tuple[Any, ...] | None:
    key: list[Any] = []
    for column in group:
        value = values.get(column)
        if value is None:
            return None  # NULL is distinct from NULL in SQL
        key.append(value)
    return tuple(key)


def _describe(spec: DatasetSpec, group: tuple[str, ...], key: tuple[Any, ...]) -> str:
    labels = [spec.csv_label(column) for column in group]
    pairs = ", ".join("%s=%r" % pair for pair in zip(labels, key))
    return pairs


def detect_within_dataset(
    spec: DatasetSpec,
    records: list[tuple[int, dict[str, Any]]],
) -> list[str]:
    """Find records in the incoming set that collide with each other."""
    findings: list[str] = []
    for group in spec.unique_groups:
        seen: dict[tuple[Any, ...], int] = {}
        for line, values in records:
            key = _key_of(values, group)
            if key is None:
                continue
            first = seen.get(key)
            if first is None:
                seen[key] = line
                continue
            findings.append(
                "line %d duplicates line %d on unique key (%s)"
                % (line, first, _describe(spec, group, key))
            )
    return findings


def detect_against_database(
    spec: DatasetSpec,
    session: Session,
    records: list[tuple[int, dict[str, Any]]],
) -> list[str]:
    """Find records that collide with rows already in the table.

    One query per unique key over a table bounded at a few dozen rows, so the
    whole existing key set is compared in memory rather than one lookup per record.
    """
    findings: list[str] = []
    for group in spec.unique_groups:
        columns = [spec.table_obj.columns[name] for name in group]
        existing = {
            tuple(row)
            for row in session.execute(select(*columns))
            if all(value is not None for value in row)
        }
        if not existing:
            continue
        for line, values in records:
            key = _key_of(values, group)
            if key is None:
                continue
            if key in existing:
                findings.append(
                    "line %d duplicates a row already in %s on unique key (%s)"
                    % (line, spec.table, _describe(spec, group, key))
                )
    return findings


def check(
    spec: DatasetSpec,
    session: Session,
    records: list[tuple[int, dict[str, Any]]],
) -> None:
    """Run both passes and raise if either found anything."""
    assert_no_supplied_primary_key(spec)
    findings = detect_within_dataset(spec, records)
    findings.extend(detect_against_database(spec, session, records))
    if findings:
        raise DuplicateRecordError(spec.table, findings)
