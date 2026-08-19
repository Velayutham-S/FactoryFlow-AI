"""Reading a dataset file and validating its structure.

Structural defects are file-level, not row-level. A misspelled or shifted column
misassigns every value in the file, so the whole dataset is rejected rather than
individual rows: importing the valid-looking remainder of a structurally broken
file is how silent corruption gets into a database.

Row content is not inspected here. This module answers "is this a well-formed
table with the columns the schema needs", and :mod:`master_data.validation`
answers "are these values acceptable".
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

from master_data.datasets import DatasetSpec
from master_data.errors import DatasetFileError, DatasetStructureError


@dataclass(frozen=True)
class SourceRow:
    """One raw CSV row, with the file line number for diagnostics.

    ``line`` is the physical line in the file, so a reported rejection points at
    something the author can find in an editor. Header is line 1.
    """

    line: int
    values: dict[str, str]


def dataset_path(spec: DatasetSpec, directory: Path) -> Path:
    return directory / spec.filename


def read_dataset(spec: DatasetSpec, directory: Path) -> list[SourceRow]:
    """Read and structurally validate one dataset file.

    Raises :class:`DatasetFileError` when the file cannot be read at all and
    :class:`DatasetStructureError` when it can be read but is not the table this
    dataset needs.
    """
    path = dataset_path(spec, directory)

    if not path.exists():
        raise DatasetFileError(
            "%s: no dataset file at %s\n  required columns: %s\n  optional columns: %s"
            % (
                spec.table,
                path,
                ", ".join(sorted(spec.required_columns)) or "(none)",
                ", ".join(sorted(spec.optional_columns)) or "(none)",
            )
        )
    if not path.is_file():
        raise DatasetFileError("%s: %s is not a file" % (spec.table, path))
    if path.stat().st_size == 0:
        raise DatasetFileError("%s: %s is empty" % (spec.table, path))

    # utf-8-sig strips a byte order mark if a spreadsheet wrote one. Left in
    # place it becomes part of the first column name and every header check fails
    # with a confusing message.
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.reader(handle)
            rows = list(reader)
    except UnicodeDecodeError as exc:
        raise DatasetFileError(
            "%s: %s is not valid UTF-8: %s" % (spec.table, path, exc)
        ) from exc
    except OSError as exc:
        raise DatasetFileError(
            "%s: could not read %s: %s" % (spec.table, path, exc)
        ) from exc

    if not rows:
        raise DatasetStructureError("%s: %s has no header row" % (spec.table, path))

    header = [name.strip() for name in rows[0]]
    _validate_header(spec, header, path)

    known = spec.required_columns | spec.optional_columns
    source_rows: list[SourceRow] = []
    for offset, raw in enumerate(rows[1:], start=2):
        if not any(field.strip() for field in raw):
            continue  # a blank separator line is not a record
        if len(raw) != len(header):
            raise DatasetStructureError(
                "%s: %s line %d has %d field(s), the header declares %d"
                % (spec.table, path.name, offset, len(raw), len(header))
            )
        values = {
            name: raw[index].strip()
            for index, name in enumerate(header)
            if name in known
        }
        source_rows.append(SourceRow(line=offset, values=values))

    if not source_rows:
        raise DatasetStructureError(
            "%s: %s has a header but no records" % (spec.table, path.name)
        )
    return source_rows


def _validate_header(spec: DatasetSpec, header: list[str], path: Path) -> None:
    if not any(header):
        raise DatasetStructureError("%s: %s has an empty header row" % (spec.table, path))

    seen: set[str] = set()
    duplicated = sorted({name for name in header if name in seen or seen.add(name)})
    if duplicated:
        raise DatasetStructureError(
            "%s: %s declares column(s) more than once: %s"
            % (spec.table, path.name, ", ".join(duplicated))
        )

    known = spec.required_columns | spec.optional_columns
    unknown = [name for name in header if name not in known]
    if unknown:
        raise DatasetStructureError(
            "%s: %s declares column(s) the table does not accept: %s\n"
            "  accepted: %s"
            % (
                spec.table,
                path.name,
                ", ".join(unknown),
                ", ".join(spec.csv_columns),
            )
        )

    missing = sorted(spec.required_columns - set(header))
    if missing:
        raise DatasetStructureError(
            "%s: %s is missing required column(s): %s\n"
            "  every column here is NOT NULL in the schema and has no default, so "
            "the loader has no value to supply"
            % (spec.table, path.name, ", ".join(missing))
        )
