"""The declarative base: one MetaData, one naming convention, one type map.

FACTORY_SQLALCHEMY_MODEL_SPECIFICATION.md §11, §12, §32, §44.1.

This module declares no column (§11, §44.4). All 53 tables live in the single
MetaData below, and no model carries a ``schema=`` argument because SQLite has
one table namespace per database file (§31).
"""

from typing import Any

from sqlalchemy import JSON, Boolean, MetaData, Numeric
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import DeclarativeBase

from models.types import (
    Hours,
    JsonDoc,
    Measurement,
    Money,
    MoneyLarge,
    Percent,
    Probability,
    Quantity,
    Rate,
    Ratio1,
    RuleNumeric,
    Seconds2,
    SignedPercent,
    TimestampTz,
    UtcDateTime,
    Weight,
)

# --------------------------------------------------------------------------
# Naming convention (§32)
#
# Named constraints are the difference between a diagnostic that identifies a
# rule and one that identifies only a table: SQLite reports "CHECK constraint
# failed: ck_machine_monitored_requires_profile", which names the rule, or
# "CHECK constraint failed: machine", which does not.
#
# ``pk`` reproduces the frozen schema's pk_<table> exactly. ``uq``, ``fk`` and
# ``ix`` contain no %(constraint_name)s token, so the explicit names the models
# supply -- taken verbatim from FACTORY_SQLITE_DATABASE_SCHEMA.md -- pass through
# untouched.
#
# ``ck`` does contain the token, which means SQLAlchemy re-templates a check
# constraint that already has a name. The frozen schema names many checks from a
# table abbreviation rather than the table name -- ck_atp_is_default_bool on
# alert_threshold_profile, ck_bom_is_active_bool on bill_of_materials,
# ck_severity_requires_line_stop_bool on failure_severity_level -- and no
# table-derived template can produce those. Every check constraint in this layer
# therefore supplies its name through ``sqlalchemy.sql.elements.conv()``, which
# marks a name as already conventionalised so the frozen name is emitted
# character for character (§45.9 check 2).
# --------------------------------------------------------------------------
NAMING_CONVENTION = {
    "pk": "pk_%(table_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
}

metadata = MetaData(naming_convention=NAMING_CONVENTION)


# --------------------------------------------------------------------------
# DDL rendering for the two types whose SQLite storage the specification names
# explicitly.
#
# SQLite derives a column's affinity from substrings of its declared type name:
# a name containing INT gets INTEGER affinity, one containing CHAR, CLOB or TEXT
# gets TEXT affinity, and a name matching none of the rules gets NUMERIC. Neither
# "BOOLEAN" nor "JSON" matches any rule, so SQLAlchemy's default rendering of
# these two types would give both NUMERIC affinity.
#
# The frozen schema declares flags INTEGER and JSON documents TEXT, and the
# specification states the same in both cases -- §29 "Storage is INTEGER holding
# 0 or 1" and §39.6 "mapped ... to SQLAlchemy's generic JSON type, stored in a
# TEXT column". Rendering the declared names accordingly is what makes the
# generated DDL and its affinities identical to
# FACTORY_SQLITE_DATABASE_SCHEMA.md.
#
# Only the emitted type name changes. Boolean still presents ``bool`` in Python
# and stores 0 or 1, and JSON still serialises and deserialises the document, so
# the Python-side contract of §29 and §39.6 is untouched.
#
# Vocabulary columns are deliberately not treated this way: §40.4 records that
# Enum(native_enum=False) renders VARCHAR(n) where the frozen schema writes TEXT,
# that the two are the same SQLite column, and that nothing depends on the
# spelling. VARCHAR contains CHAR, so the affinity is already TEXT and there is
# nothing to correct.
# --------------------------------------------------------------------------
@compiles(Boolean, "sqlite")
def _render_boolean_as_integer(type_, compiler, **kw) -> str:
    return "INTEGER"


@compiles(JSON, "sqlite")
def _render_json_as_text(type_, compiler, **kw) -> str:
    return "TEXT"


class Base(DeclarativeBase):
    """The single declarative base. All 53 models derive from it."""

    metadata = metadata

    # ----------------------------------------------------------------------
    # Resolution for the 15 role-marker aliases of §38.4, plus the two plain
    # Python types that need a non-default SQLAlchemy type.
    #
    # ``MasterPk`` and ``OperationalPk`` are absent by necessity: they carry a
    # whole ``mapped_column()`` declaration rather than a type, and a map value
    # must be a type. SQLAlchemy resolves them from that declaration directly.
    #
    # ``TimestampTz`` -> UtcDateTime, which renders DATETIME and carries the
    # read-side half of the UTC contract: SQLite persists no offset, so a value
    # read through a plain DateTime comes back naive and the round trip is lossy
    # (§28, §41.3).
    #
    # ``asdecimal`` is stated on every Numeric so it cannot be changed by
    # accident (§38.2). The 11 precisions below are the complete register of
    # §39.4 and are written here exactly once.
    #
    # ``bool`` -> Boolean, which presents ``bool`` in Python and stores 0 or 1,
    # so the Python type the frozen models specify is preserved while the
    # storage matches the schema's INTEGER declaration (§29).
    # ``create_constraint`` is False because the frozen schema declares all 73
    # boolean domain checks explicitly by name; letting the type emit its own
    # would produce two constraints for one rule, the duplication §40.4 avoids
    # for enums.
    #
    # ``list[Any]`` -> JSON for supervisor_context.related_alert_codes, the one
    # JSON column whose root type is an array rather than an object (§39.6).
    # ----------------------------------------------------------------------
    type_annotation_map = {
        TimestampTz: UtcDateTime(),
        JsonDoc: JSON(),
        Measurement: Numeric(12, 4, asdecimal=True),
        Money: Numeric(12, 2, asdecimal=True),
        MoneyLarge: Numeric(14, 2, asdecimal=True),
        Quantity: Numeric(12, 2, asdecimal=True),
        Hours: Numeric(12, 2, asdecimal=True),
        Rate: Numeric(10, 2, asdecimal=True),
        Percent: Numeric(5, 2, asdecimal=True),
        SignedPercent: Numeric(6, 2, asdecimal=True),
        Probability: Numeric(5, 4, asdecimal=True),
        Seconds2: Numeric(8, 2, asdecimal=True),
        Ratio1: Numeric(2, 1, asdecimal=True),
        Weight: Numeric(4, 2, asdecimal=True),
        RuleNumeric: Numeric(14, 4, asdecimal=True),
        bool: Boolean(create_constraint=False),
        list[Any]: JSON(),
    }
