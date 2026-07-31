"""The 17 Annotated type aliases (FACTORY_SQLALCHEMY_MODEL_SPECIFICATION.md §38.4).

Every repeated type in the ORM is declared once here and resolved to a concrete
SQLAlchemy type through the declarative base's ``type_annotation_map`` (§44.1).
No model declares a bare ``Numeric(p, s)``, a bare ``DateTime`` or a bare
``JSON`` anywhere (§45.4).

Each alias carries a role marker rather than a type. The marker is what makes it
a distinct key in the base's map -- ``Money``, ``Quantity`` and ``Hours`` are
three names for NUMERIC(12,2) and must stay distinguishable -- and the concrete
precision is written exactly once, in ``base.py``.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Optional

from sqlalchemy import DateTime, Dialect, Integer, TypeDecorator
from sqlalchemy.orm import mapped_column


class UtcDateTime(TypeDecorator[datetime]):
    """``DATETIME`` that upholds the UTC contract in both directions (§28, §41.3).

    SQLite has no date/time type and no zone concept: the column holds a string
    with no offset. That makes the contract entirely the ORM's, and it has two
    halves, both required.

    On the way in, a naive value is rejected rather than guessed at. Assuming a
    naive ``recorded_at`` is UTC when it is local time shifts every reading by the
    offset, moves readings across shift boundaries, and surfaces weeks later as a
    shift-report anomaly. An aware value in any zone is converted to UTC and its
    offset dropped, so what lands in the column is always the UTC wall time.

    On the way out, ``timezone.utc`` is re-attached. Without this the round trip is
    lossy: a value written aware comes back naive, and the Python-side contract of
    §38.3 would hold in one direction only.

    The model-level ``@validates`` hooks reject naive input at *assignment*, before
    any SQL is emitted, which is where a defect should surface. This type is the
    backstop and the read-side half of the same contract.

    ``DateTime(timezone=True)`` is deliberately not used as the implementation:
    the SQLite dialect accepts the flag and persists no offset, so it would imply
    a guarantee the engine does not give.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(
        self, value: Optional[datetime], dialect: Dialect
    ) -> Optional[datetime]:
        if value is None:
            return None
        if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
            raise ValueError(
                "a DATETIME column requires a timezone-aware datetime in UTC; "
                "got a naive value"
            )
        return value.astimezone(timezone.utc).replace(tzinfo=None)

    def process_result_value(
        self, value: Optional[datetime], dialect: Dialect
    ) -> Optional[datetime]:
        if value is None:
            return None
        return value.replace(tzinfo=timezone.utc)

# --------------------------------------------------------------------------
# Identity aliases (§25, §39.3)
#
# These two are the only aliases that carry a whole column declaration rather
# than a role marker, because ``primary_key`` and ``autoincrement`` are column
# properties that no type can express and §45.4 forbids declaring an identity
# key inline in a model.
#
# ``Integer`` is mandatory rather than preferred: SQLite makes a column an alias
# for its internal 64-bit rowid only when the declared type is exactly INTEGER,
# and AUTOINCREMENT is rejected on anything else (§25). ``BigInteger`` would
# render BIGINT and lose both.
#
# Both aliases resolve identically because SQLite has one integer type. Both
# names are retained deliberately: a model's key alias states which logical
# group it belongs to at a glance, a signal the package layout and the mixin
# compositions both rely on (§38.4).
# --------------------------------------------------------------------------
MasterPk = Annotated[int, mapped_column(Integer, primary_key=True,
                                        autoincrement=True)]
OperationalPk = Annotated[int, mapped_column(Integer, primary_key=True,
                                             autoincrement=True)]

# --------------------------------------------------------------------------
# Temporal alias (§28, §38.3)
#
# The name states a contract, not a column capability: the value is a UTC
# instant, timezone-aware in Python. SQLite persists no offset, so
# ``DateTime(timezone=True)`` would imply a guarantee the engine does not give.
# The ORM upholds the contract in both directions -- naive input rejected at
# assignment, UTC re-attached on read (§41.3).
# --------------------------------------------------------------------------
TimestampTz = Annotated[datetime, "TimestampTz"]

# --------------------------------------------------------------------------
# Document alias (§39.6)
#
# Generic JSON serialised into a TEXT column. Structural validation belongs to
# the database's json_valid() check constraints and is not duplicated here
# (§41.2). ``MutableDict`` is deliberately not applied, so a document is
# replaced wholesale and never edited in place (§39.6, §45.4).
# --------------------------------------------------------------------------
JsonDoc = Annotated[dict[str, Any], "JsonDoc"]

# --------------------------------------------------------------------------
# Numeric aliases (§38.4, §39.4)
#
# All 11 precisions of the frozen schema. The Python type is ``Decimal`` on all
# 74 numeric columns and ``float`` appears in no annotation anywhere in this
# layer (§38.2).
#
# Aliases exist even where a precision covers one column -- Ratio1, Weight,
# RuleNumeric, MoneyLarge, SignedPercent -- so that a reviewer never has to
# judge whether a given precision was intentional or a typo.
# --------------------------------------------------------------------------
Measurement = Annotated[Decimal, "Measurement"]
Money = Annotated[Decimal, "Money"]
MoneyLarge = Annotated[Decimal, "MoneyLarge"]
Quantity = Annotated[Decimal, "Quantity"]
Hours = Annotated[Decimal, "Hours"]
Rate = Annotated[Decimal, "Rate"]
Percent = Annotated[Decimal, "Percent"]
SignedPercent = Annotated[Decimal, "SignedPercent"]
Probability = Annotated[Decimal, "Probability"]
Seconds2 = Annotated[Decimal, "Seconds2"]
Ratio1 = Annotated[Decimal, "Ratio1"]
Weight = Annotated[Decimal, "Weight"]
RuleNumeric = Annotated[Decimal, "RuleNumeric"]

__all__ = [
    "Hours",
    "JsonDoc",
    "UtcDateTime",
    "MasterPk",
    "Measurement",
    "Money",
    "MoneyLarge",
    "OperationalPk",
    "Percent",
    "Probability",
    "Quantity",
    "Rate",
    "Ratio1",
    "RuleNumeric",
    "Seconds2",
    "SignedPercent",
    "TimestampTz",
    "Weight",
]
