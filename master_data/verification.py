"""Verification after each dataset, and once across the whole master set.

Five checks per dataset, in the order the phase requires them:

1. **Record count** -- the table holds exactly what the import reported writing.
2. **Foreign key validity** -- no row references a parent that is not there.
3. **Constraint validity** -- no NOT NULL column holds NULL, and every vocabulary
   column holds a term the schema allows.
4. **Relationship integrity** -- every foreign key traverses to a real row through
   the mapped relationship, not merely to a non-null integer.
5. **Expected table population** -- the table is not empty.

Checks 2 and 3 look redundant against a database that enforces both, and they are
not. They are read back *after* the commit, from the stored rows, which is what
distinguishes "the write was accepted" from "the data is correct": a server default
that did not apply, a foreign key written while enforcement was off, or a restored
file all produce a table that inserted cleanly and is wrong. Verification that only
repeats what the insert already proved would find none of them.

Failures accumulate and are reported together. A run that stops at the first problem
turns one bad file into one round trip per bad row.

**Scope note.** FACTORY_MASTER_DATA_DESIGN.md §32.2 defines 18 completeness rules --
"every active production line has at least one active machine", "exactly one profile
per machine type has ``is_default = 1``", and so on -- and §32.4 runs them once after
seeding. They are cross-table rules, which §41 of the model specification assigns to
the writing component rather than to the schema. They are deliberately not
implemented here: this module verifies the checks this phase enumerates, and the
completeness rules are a separate gate over the finished master set rather than a
per-dataset import check.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import Engine, Enum as SAEnum, func, select
from sqlalchemy.orm import Session, sessionmaker

from models.base import metadata
from models.session import verify_database

from master_data.datasets import DatasetSpec
from master_data.errors import ImportVerificationError
from master_data.validation import _enum_values


@dataclass
class DatasetVerification:
    """The verification outcome for one dataset."""

    table: str
    row_count: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def _count(session: Session, spec: DatasetSpec) -> int:
    return int(
        session.execute(select(func.count()).select_from(spec.table_obj)).scalar_one()
    )


def verify_dataset(
    spec: DatasetSpec,
    session: Session,
    expected_rows: int,
) -> DatasetVerification:
    """Run the five checks for one dataset against committed data."""
    result = DatasetVerification(table=spec.table)
    table = spec.table_obj

    # 1. Record count.
    result.row_count = _count(session, spec)
    if result.row_count != expected_rows:
        result.failures.append(
            "%s holds %d row(s); the import reported %d"
            % (spec.table, result.row_count, expected_rows)
        )

    # 5. Expected table population.
    if result.row_count == 0:
        result.failures.append(
            "%s is empty; every master table is expected to hold rows" % spec.table
        )

    # 2 and 4. Foreign key validity and relationship integrity.
    for ref in spec.foreign_keys:
        parent = metadata.tables[ref.parent_table]
        parent_key = list(parent.primary_key.columns)[0]
        child_column = table.columns[ref.column]
        orphans = int(
            session.execute(
                select(func.count())
                .select_from(
                    table.outerjoin(parent, child_column == parent_key)
                )
                .where(child_column.is_not(None), parent_key.is_(None))
            ).scalar_one()
        )
        if orphans:
            result.failures.append(
                "%s.%s has %d row(s) referencing a %s that does not exist"
                % (spec.table, ref.column, orphans, ref.parent_table)
            )

        if ref.required:
            nulls = int(
                session.execute(
                    select(func.count())
                    .select_from(table)
                    .where(child_column.is_(None))
                ).scalar_one()
            )
            if nulls:
                result.failures.append(
                    "%s.%s is NOT NULL but %d row(s) hold NULL"
                    % (spec.table, ref.column, nulls)
                )

    # 3. Constraint validity, read back from stored rows.
    for column in table.columns:
        if not column.nullable:
            nulls = int(
                session.execute(
                    select(func.count()).select_from(table).where(column.is_(None))
                ).scalar_one()
            )
            if nulls:
                result.failures.append(
                    "%s.%s is NOT NULL but %d row(s) hold NULL"
                    % (spec.table, column.name, nulls)
                )
        if isinstance(column.type, SAEnum):
            allowed = _enum_values(column.type)
            stored = {
                row[0]
                for row in session.execute(select(column).distinct())
                if row[0] is not None
            }
            stored_values = {
                value.value if hasattr(value, "value") else str(value)
                for value in stored
            }
            unknown = sorted(stored_values - allowed)
            if unknown:
                result.failures.append(
                    "%s.%s holds value(s) outside its vocabulary: %s"
                    % (spec.table, column.name, ", ".join(unknown))
                )

    return result


def verify_master_set(
    engine: Engine,
    session_factory: sessionmaker[Session],
    specs: tuple[DatasetSpec, ...],
) -> list[str]:
    """Verify the finished master set as a whole.

    Delegates the file-level assertions to ``models.session.verify_database``, which
    already owns them: ``PRAGMA integrity_check`` for structural damage and
    ``PRAGMA foreign_key_check`` for orphaned rows anywhere in the database. Both are
    raw pragmas the schema document §31.1 names explicitly, and reimplementing them
    here would duplicate the integration layer this phase is required to reuse.
    """
    failures: list[str] = []
    try:
        verify_database(engine, session_factory)
    except Exception as exc:
        failures.append("database verification failed: %s" % exc)

    from models.session import session_scope

    with session_scope(session_factory) as session:
        for spec in specs:
            if _count(session, spec) == 0:
                failures.append(
                    "%s is empty after seeding" % spec.table
                )
    return failures


def require(failures: list[str]) -> None:
    """Raise when anything failed. Verification never reports by returning quietly."""
    if failures:
        raise ImportVerificationError(failures)
