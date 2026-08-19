"""Record validation, derived from the frozen schema rather than restated.

Every rule applied here is read out of the mapped table: nullability from the
column, the target Python type from the column type, string bounds from
``String(n)``, vocabularies from ``Enum``, numeric precision from ``Numeric(p, s)``,
and code formats from the ``GLOB`` check constraints the schema already declares.
Nothing is transcribed, so this module cannot enforce a rule the database does not
have, and it cannot miss one that was added to a column it reads.

**What this module does not do.** It does not re-implement the schema's ~585 check
constraints. FACTORY_SQLALCHEMY_MODEL_SPECIFICATION.md §41 is explicit that value
ranges and cross-column rules belong to the database, that mirroring them by hand
is a transcription error waiting to happen, and that "a validator that is subtly
wrong is worse than none because it inspires confidence". Ranges, cross-column
rules and cross-row rules stay with SQLite, which applies them to every writer
rather than only to this one. What it does do is catch, before any write, the
entire class of defect the requirement names -- missing values, wrong types,
unknown vocabulary terms, over-long text, malformed codes -- and attach a reason to
each. A constraint that only SQLite can decide still surfaces as a named
``IntegrityError`` at flush; it is reported, never swallowed.

Type coercion is strict and never lossy. ``"1.0"`` is not an integer, ``"26.001"``
does not fit ``NUMERIC(10,2)``, and neither is quietly rounded into range --
silently altering a value is a worse outcome than rejecting it with a reason.
"""

from __future__ import annotations

import enum
import re
from datetime import date, datetime, time
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum as SAEnum,
    Integer,
    Numeric,
    String,
    Table,
    Time,
)

from master_data.datasets import NULL_TOKENS, DatasetSpec
from master_data.csv_source import SourceRow

# Cache of table name -> column name -> (positive patterns, negative patterns).
_PATTERN_CACHE: dict[str, dict[str, tuple[list[str], list[str]]]] = {}

_GLOB_CLAUSE = re.compile(r"(\w+)\s+(NOT\s+)?GLOB\s+'([^']*)'", re.IGNORECASE)


def glob_to_regex(pattern: str) -> str:
    """Translate a SQLite GLOB pattern into an equivalent anchored regex.

    GLOB is Unix glob syntax: ``*`` any sequence, ``?`` any single character,
    ``[...]`` a character class with ``^`` negating it. Everything else is literal.
    """
    out: list[str] = ["^"]
    index = 0
    while index < len(pattern):
        char = pattern[index]
        if char == "*":
            out.append(".*")
        elif char == "?":
            out.append(".")
        elif char == "[":
            close = pattern.find("]", index + 1)
            if close == -1:
                out.append(re.escape(char))
            else:
                body = pattern[index + 1:close]
                if body.startswith("^"):
                    body = "^" + body[1:]
                out.append("[" + body + "]")
                index = close
        else:
            out.append(re.escape(char))
        index += 1
    out.append("$")
    return "".join(out)


def _column_patterns(table: Table) -> dict[str, tuple[list[str], list[str]]]:
    """Extract per-column GLOB patterns from the table's check constraints.

    Only clauses whose left side is a bare column of this table are taken. A
    positive ``GLOB`` is a necessary condition for the value to be accepted by the
    database, and a ``NOT GLOB`` is a necessary condition for it to be rejected,
    so applying either can never reject a value SQLite would have accepted.
    Clauses wrapped in a function call -- ``substr(shift_code, 4) NOT GLOB ...`` --
    are left to SQLite.
    """
    cached = _PATTERN_CACHE.get(table.name)
    if cached is not None:
        return cached

    found: dict[str, tuple[list[str], list[str]]] = {}
    for constraint in table.constraints:
        if not isinstance(constraint, CheckConstraint):
            continue
        text = str(constraint.sqltext)
        for name, negated, pattern in _GLOB_CLAUSE.findall(text):
            if name not in table.columns:
                continue
            positive, negative = found.setdefault(name, ([], []))
            (negative if negated else positive).append(pattern)
    _PATTERN_CACHE[table.name] = found
    return found


def _is_null(raw: str) -> bool:
    return raw.strip().lower() in NULL_TOKENS


def _coerce(column: Any, raw: str) -> tuple[Any, str | None]:
    """Convert one CSV field to the column's Python type.

    Returns ``(value, None)`` on success or ``(None, reason)`` on failure.
    """
    kind = column.type
    text = raw.strip()

    if isinstance(kind, SAEnum):
        allowed = _enum_values(kind)
        if text not in allowed:
            return None, "%r is not one of the allowed values (%s)" % (
                text, ", ".join(sorted(allowed))
            )
        enum_class = getattr(kind, "enum_class", None)
        if enum_class is not None:
            return next(m for m in enum_class if m.value == text), None
        return text, None

    if isinstance(kind, Boolean):
        lowered = text.lower()
        if lowered in {"1", "true"}:
            return True, None
        if lowered in {"0", "false"}:
            return False, None
        return None, "%r is not a boolean; use 0 or 1" % text

    if isinstance(kind, Integer):
        if not re.fullmatch(r"[+-]?\d+", text):
            return None, "%r is not an integer" % text
        return int(text), None

    if isinstance(kind, Numeric):
        try:
            value = Decimal(text)
        except (InvalidOperation, ValueError):
            return None, "%r is not a decimal number" % text
        if not value.is_finite():
            return None, "%r is not a finite decimal" % text
        precision = kind.precision
        scale = kind.scale
        exponent = value.as_tuple().exponent
        places = -exponent if isinstance(exponent, int) and exponent < 0 else 0
        if scale is not None and places > scale:
            return None, "%r has %d decimal place(s); NUMERIC(%s,%s) allows %d" % (
                text, places, precision, scale, scale
            )
        if precision is not None:
            digits = len(value.as_tuple().digits)
            integral = digits - places
            if integral > precision - (scale or 0):
                return None, "%r is too large for NUMERIC(%s,%s)" % (
                    text, precision, scale
                )
        return value, None

    if isinstance(kind, DateTime):
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            return None, "%r is not an ISO-8601 datetime" % text
        if moment.tzinfo is None:
            return None, (
                "%r has no timezone; the ORM stores UTC and rejects naive "
                "datetimes" % text
            )
        return moment, None

    if isinstance(kind, Date):
        try:
            return date.fromisoformat(text), None
        except ValueError:
            return None, "%r is not an ISO-8601 date (YYYY-MM-DD)" % text

    if isinstance(kind, Time):
        try:
            return time.fromisoformat(text), None
        except ValueError:
            return None, "%r is not an ISO-8601 time (HH:MM or HH:MM:SS)" % text

    if isinstance(kind, String):
        limit = kind.length
        if limit is not None and len(text) > limit:
            return None, "is %d characters; the column allows %d" % (len(text), limit)
        return text, None

    return text, None


def _enum_values(kind: SAEnum) -> set[str]:
    """The vocabulary as it is stored.

    The models bind every enum with ``values_callable`` so the value, not the
    member name, reaches the column. Reading the enum class directly is therefore
    the reliable source; ``kind.enums`` is used only when no class is attached.
    """
    enum_class = getattr(kind, "enum_class", None)
    if enum_class is not None and issubclass(enum_class, enum.Enum):
        return {str(member.value) for member in enum_class}
    return {str(value) for value in kind.enums}


def validate_row(
    spec: DatasetSpec,
    row: SourceRow,
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate and coerce one record.

    Returns ``(values, [])`` when the record is acceptable, or ``(None, reasons)``
    when it is not. Foreign key code columns are carried through as raw strings for
    :mod:`master_data.importer` to resolve; every other column arrives as the
    Python type the model expects.
    """
    table = spec.table_obj
    patterns = _column_patterns(table)
    fk_csv_columns = {ref.csv_column for ref in spec.foreign_keys}

    values: dict[str, Any] = {}
    reasons: list[str] = []

    for name in spec.value_columns:
        column = table.columns[name]
        raw = row.values.get(name)

        if raw is None or _is_null(raw):
            if name in spec.required_columns:
                reasons.append(
                    "line %d, %s: required value is missing" % (row.line, name)
                )
            elif column.nullable:
                values[name] = None
            # Not nullable but defaulted: omit the key so the server default applies.
            continue

        value, reason = _coerce(column, raw)
        if reason is not None:
            reasons.append("line %d, %s: %s" % (row.line, name, reason))
            continue

        if isinstance(value, str):
            positive, negative = patterns.get(name, ([], []))
            # Codes are normalised upward by the ORM on assignment; compare the
            # normalised form so a lower-case code in a file is not rejected for a
            # format the ORM would have fixed.
            candidate = value.strip()
            probe = candidate.upper() if _normalises_upper(name) else candidate
            failed = [p for p in positive if not re.match(glob_to_regex(p), probe)]
            hit = [p for p in negative if re.match(glob_to_regex(p), probe)]
            if failed:
                reasons.append(
                    "line %d, %s: %r does not match the required format %s"
                    % (row.line, name, candidate, ", ".join(repr(p) for p in failed))
                )
                continue
            if hit:
                reasons.append(
                    "line %d, %s: %r matches the disallowed pattern %s"
                    % (row.line, name, candidate, ", ".join(repr(p) for p in hit))
                )
                continue
            if not candidate and not column.nullable:
                reasons.append(
                    "line %d, %s: value is blank" % (row.line, name)
                )
                continue

        values[name] = value

    for csv_column in sorted(fk_csv_columns):
        raw = row.values.get(csv_column)
        if raw is None or _is_null(raw):
            if csv_column in spec.required_columns:
                reasons.append(
                    "line %d, %s: required foreign key reference is missing"
                    % (row.line, csv_column)
                )
            else:
                values[csv_column] = None
            continue
        values[csv_column] = raw.strip()

    if reasons:
        return None, reasons
    return values, []


def _normalises_upper(column_name: str) -> bool:
    """Whether the ORM upper-cases this column on assignment.

    Mirrors the ``@validates`` normalisation in the frozen models, which applies to
    business codes and to the fixed-width code columns.
    """
    return (
        column_name.endswith("_code")
        or column_name in {
            "abc_class",
            "display_color_hex",
            "serial_number",
            "asset_tag",
            "model_number",
            "drawing_revision",
        }
    )
