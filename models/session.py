"""Engine, session factory, initialization and lifecycle (§4.1, §9, §42, §44.3).

This module is the database integration layer. It owns the four runtime concerns
that the frozen ORM specification places outside the models themselves:

* the ``Engine``, with the connection-level pragma hook of §42.9,
* the ``sessionmaker`` of §42.5,
* database initialization -- existence checking, creation when required, and the
  verification sequence,
* the session and transaction lifecycle of §9 steps 9-13.

No model imports this module (§44.4). It imports ``models.registry`` so that the
single ``MetaData`` is complete and every mapper is configurable before any
connection is opened: without that import ``metadata`` is empty and schema creation
would silently produce nothing.

The driver is the standard library: SQLAlchemy's default SQLite dialect is
``pysqlite``, which is Python's built-in ``sqlite3``. There is no third-party driver
to install and no client library to keep aligned with a server.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, configure_mappers, sessionmaker

import models.registry  # noqa: F401  -- registers all 53 tables on the MetaData
from models.base import metadata

# Busy timeout in seconds, applied at the driver level. A writer that finds the
# database's write lock held retries for this long instead of raising
# SQLITE_BUSY immediately (§42.8).
BUSY_TIMEOUT_SECONDS = 30


def _register_connection_pragmas(engine: Engine) -> None:
    """Issue the per-connection pragmas on every new DBAPI connection (§42.9).

    THIS IS THE SINGLE MOST CONSEQUENTIAL OPERATIONAL REQUIREMENT IN THE LAYER.

    All 163 foreign keys -- the 162 RESTRICT actions protecting the evidence trail
    and the one SET NULL on ``operational_event.triggering_reading_id`` -- are
    declared correctly in the models and enforce NOTHING unless
    ``PRAGMA foreign_keys = ON`` is set on the connection performing the write.
    The pragma is off by default, is per connection rather than per database, does
    not persist in the file, is a no-op inside a transaction, and fails silently
    either way: there is no error when it is missing, and orphan rows are simply
    accepted.

    A ``connect`` hook is therefore the mechanism rather than a startup statement.
    It fires on every new connection, before any transaction on it, which is
    exactly the two conditions the pragma requires. Issuing it once after
    ``create_engine`` would cover the first connection and no other.

    What breaks without it, concretely: a retention purge deletes readings that
    events still reference, and instead of the SET NULL the schema specifies, the
    events keep pointing at rows that no longer exist. A master row is deleted
    despite its inbound references. The citation chain from a recommendation back
    to the reading that triggered it develops holes. None of it raises an error.

    ``synchronous = NORMAL`` is set here too because it also does not persist.
    With WAL it is durable across a process crash; only host power loss can lose
    the most recent commits, and ``FULL`` would cost an fsync per commit for a
    guarantee this platform does not need (§42.8).
    """

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys = ON")
        cursor.execute("PRAGMA synchronous = NORMAL")
        cursor.close()


def create_factoryflow_engine(database_path: str | Path) -> Engine:
    """Build the engine for an absolute database file path (§4.1).

    The path must be absolute. A relative path is resolved against the process
    working directory, which means the database found depends on where the process
    was started from -- exactly the environment-dependent behaviour
    PROJECT_OVERVIEW.md §16.12 requires the platform to avoid.

    The connection pool is left at SQLAlchemy's default. For a file-based SQLite
    URL that is ``QueuePool``, which suits a platform where each component holds a
    connection across many short transactions. ``NullPool`` would reopen the file
    per transaction and discard the page cache; ``StaticPool`` would share one
    connection across threads, which SQLite's default threading mode does not
    permit.
    """
    path = Path(database_path)
    if not path.is_absolute():
        raise ValueError(
            "database_path must be absolute; %r is relative and would resolve "
            "against the process working directory (§4.1)" % str(database_path)
        )
    engine = create_engine(
        "sqlite+pysqlite:///%s" % path,
        echo=False,
        connect_args={"timeout": BUSY_TIMEOUT_SECONDS},
    )
    _register_connection_pragmas(engine)
    return engine


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Build the session factory for one engine (§42.1, §42.5).

    ``expire_on_commit=False`` is deliberate and is paired with the
    ``raise_on_sql`` relationships on the four highest-volume models. With the
    default ``True``, every attribute of every object expires at commit and the
    next access emits a SELECT -- which on a ``raise_on_sql`` relationship raises
    instead. Post-commit logging would fail on ordinary code. Choosing one setting
    without the other produces a layer that breaks under normal use (§42.5).

    The trade-off: a long-lived object may hold values another transaction has
    since changed. That is accepted because sessions are scoped to one of the 20
    transaction boundaries, because table ownership means no two components write
    the same row, and because a component that must know a value is current
    re-queries rather than reusing a detached object.

    ``scoped_session`` is not used. It provides an implicit thread-local session
    any code can reach, which makes it impossible to tell from a call site which
    component's boundary a write belongs to. Explicit sessions make the boundary
    visible in the code that opens it (§42.1).

    A ``Session`` is not thread-safe: one per thread, never shared. The ``Engine``
    and its pool are thread-safe and shared, one per process (§42.8).
    """
    return sessionmaker(bind=engine, expire_on_commit=False)


def configure_journal_mode(engine: Engine) -> str:
    """Set ``PRAGMA journal_mode = WAL`` and return the mode the engine reports.

    This is the one pragma that persists in the database file, so it is applied
    once against the file rather than on every connection. It lets readers proceed
    during a write instead of blocking; without it the dashboard stalls whenever
    the Simulator commits a telemetry batch (§42.8).

    Because it persists, it is applied to an existing database as well as a new
    one: a file created without WAL would otherwise keep its rollback journal
    silently.
    """
    with engine.connect() as connection:
        return str(connection.exec_driver_sql(
            "PRAGMA journal_mode = WAL").scalar())


def create_schema(engine: Engine) -> None:
    """Create all 53 tables and the 8 unique indexes on a new database file.

    ``create_all`` is check-first: it creates only objects that are absent and
    never drops, alters or truncates anything.

    In production the schema is owned by Alembic, which targets the same MetaData
    through ``registry.py`` (§44.5). This function exists for first-time creation
    and for test setup.
    """
    configure_journal_mode(engine)
    metadata.create_all(engine)


class DatabaseVerificationError(RuntimeError):
    """A database failed one of the checks in :func:`verify_database`.

    Raised rather than logged, because every condition it reports makes the
    platform's guarantees untrue: a missing table, a disabled foreign key pragma,
    or a failed integrity check all mean the data cannot be relied on, and
    continuing past any of them produces wrong answers rather than errors.
    """


def database_exists(database_path: str | Path) -> bool:
    """True when the path is an existing SQLite database file with content.

    A zero-byte file is treated as absent. SQLite creates the file on the first
    connection, so an aborted earlier run can leave an empty one behind, and
    reporting that as an existing database would skip schema creation and leave a
    database with no tables.
    """
    path = Path(database_path)
    return path.is_file() and path.stat().st_size > 0


def initialize_database(
    database_path: str | Path,
    *,
    create_if_missing: bool = True,
) -> tuple[Engine, sessionmaker[Session]]:
    """Bring the database up and hand back the engine and session factory.

    The sequence is the runtime half of the lifecycle in §9:

    1. Configure the mappers, so a relationship typo fails here rather than at the
       first query (§9 step 6).
    2. Detect whether the database already exists.
    3. Create the engine, which registers the per-connection pragma hook (§42.9).
    4. Apply ``journal_mode = WAL``, which persists in the file.
    5. Create the schema **only when the database did not exist**.
    6. Verify the result and raise on any failure.

    **An existing database is never re-created, reset or dropped.** Step 5 is
    skipped entirely when the file is already present, so a database populated by
    Alembic or by ``database/factoryflow_sqlite_schema.sql`` is left exactly as it
    is. If such a database is missing tables, :func:`verify_database` reports
    which ones rather than quietly filling them in -- an incomplete database is a
    migration problem, and repairing it here would mask that.

    Set ``create_if_missing=False`` for a component that must attach to a database
    somebody else owns; a missing file is then an error rather than an empty
    database.
    """
    path = Path(database_path)
    configure_mappers()

    already_present = database_exists(path)
    if not already_present and not create_if_missing:
        raise FileNotFoundError(
            "no SQLite database at %s and create_if_missing is False" % path
        )

    engine = create_factoryflow_engine(path)
    try:
        if not already_present:
            path.parent.mkdir(parents=True, exist_ok=True)
            create_schema(engine)
        else:
            configure_journal_mode(engine)

        session_factory = create_session_factory(engine)
        verify_database(engine, session_factory)
    except Exception:
        # A failed initialization must not leave the pool holding the file open.
        # Without this the caller cannot delete, move or repair the database it
        # was just told is unusable.
        engine.dispose()
        raise
    return engine, session_factory


def verify_database(
    engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    """Run the verification sequence, raising on the first failure.

    In order: connectivity, metadata registration, session creation, table
    accessibility, foreign key enforcement, journal mode, busy timeout, database
    integrity, and referential integrity.

    Every pragma is read from a **pooled** connection rather than one opened for
    the check. ``foreign_keys`` is per connection and is set by the ``connect``
    hook, so confirming it on a connection the pool actually hands out is the only
    reading that means anything (§45.9 check 14).

    ``PRAGMA foreign_key_check`` is the last step because it detects damage rather
    than preventing it: it reports every row in the database that violates a
    foreign key, which is the correct assertion after any restore or any operation
    performed with enforcement disabled (schema document §31.1).
    """
    if not metadata.tables:
        raise DatabaseVerificationError(
            "the MetaData is empty; models.registry was not imported and no "
            "table is registered"
        )

    try:
        with engine.connect() as connection:
            if connection.exec_driver_sql("SELECT 1").scalar() != 1:
                raise DatabaseVerificationError(
                    "connectivity check did not return 1 from %r" % engine.url
                )

            foreign_keys = connection.exec_driver_sql(
                "PRAGMA foreign_keys").scalar()
            if foreign_keys != 1:
                raise DatabaseVerificationError(
                    "PRAGMA foreign_keys is %r on a pooled connection, not 1; "
                    "all 163 foreign keys are declared and inert (§42.9)"
                    % foreign_keys
                )

            journal_mode = str(connection.exec_driver_sql(
                "PRAGMA journal_mode").scalar()).lower()
            if journal_mode != "wal":
                raise DatabaseVerificationError(
                    "PRAGMA journal_mode is %r, not 'wal'" % journal_mode
                )

            busy_timeout = connection.exec_driver_sql(
                "PRAGMA busy_timeout").scalar()
            expected_timeout = BUSY_TIMEOUT_SECONDS * 1000
            if busy_timeout != expected_timeout:
                raise DatabaseVerificationError(
                    "PRAGMA busy_timeout is %r ms, expected %d ms"
                    % (busy_timeout, expected_timeout)
                )

            present = {
                row[0] for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            missing = sorted(set(metadata.tables) - present)
            if missing:
                raise DatabaseVerificationError(
                    "%d of %d registered tables are absent from the database: %s"
                    % (len(missing), len(metadata.tables), ", ".join(missing))
                )

            integrity = connection.exec_driver_sql(
                "PRAGMA integrity_check").scalar()
            if str(integrity).lower() != "ok":
                raise DatabaseVerificationError(
                    "PRAGMA integrity_check reported %r" % integrity
                )

            violations = connection.exec_driver_sql(
                "PRAGMA foreign_key_check").fetchall()
            if violations:
                raise DatabaseVerificationError(
                    "PRAGMA foreign_key_check found %d orphaned row(s); first: "
                    "%r" % (len(violations), violations[0])
                )
    except SQLAlchemyError as exc:
        raise DatabaseVerificationError(
            "could not verify the database at %r: %s" % (engine.url, exc)
        ) from exc

    # Table accessibility and session creation together: one session, one
    # bounded read per table. LIMIT 1 rather than count(*), so the check stays
    # cheap on machine_sensor_reading at 32 million rows a year.
    try:
        with session_scope(session_factory) as session:
            for name in sorted(metadata.tables):
                session.execute(text('SELECT 1 FROM "%s" LIMIT 1' % name))
    except SQLAlchemyError as exc:
        raise DatabaseVerificationError(
            "a registered table is not readable through a session: %s" % exc
        ) from exc


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """One session for one transaction boundary: commit, or roll back and re-raise.

    Each of the twenty boundaries named in §24 becomes exactly one of these scopes,
    and the boundary's own logic belongs to the component that owns it. This
    manager supplies only the lifecycle those boundaries share (§9 steps 9-13):

    * one ``commit`` per boundary, never inside a loop over rows (§42.6),
    * ``rollback`` on any exception, which is mandatory after ``IntegrityError``
      because a single violation aborts the whole transaction rather than the one
      statement (§41.5),
    * ``close`` always, so the connection returns to the pool on every path.

    The exception is re-raised rather than swallowed. Two ``IntegrityError`` cases
    are expected in normal operation -- the partial unique indexes on
    ``production_run`` and ``operational_alert`` firing when two instances of one
    component race -- and the caller retries the boundary. It can only do that if
    it sees the error (§42.8).

    ``scoped_session`` is deliberately not used: an implicit thread-local session
    any code can reach makes it impossible to tell from a call site which
    component's boundary a write belongs to (§42.1). A ``Session`` is not
    thread-safe -- one per thread, never shared.
    """
    session = session_factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def shutdown_database(engine: Engine) -> None:
    """Checkpoint the write-ahead log and dispose of the connection pool.

    The checkpoint folds the ``-wal`` file back into the database and truncates it,
    so a clean shutdown leaves one consistent file rather than a set of three.
    ``dispose`` then closes every pooled connection, which is what makes the
    shutdown leak-free.

    The checkpoint runs inside ``try``/``finally`` so the pool is always disposed,
    but a checkpoint failure still propagates: it means other connections were
    live at shutdown, which is a defect in the caller's lifecycle rather than
    something to swallow.
    """
    try:
        with engine.connect() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        engine.dispose()
