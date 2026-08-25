"""Every read the dashboard performs. Nothing else in the package touches a session.

**Read-only, and no new database layer.** :mod:`models.session` already owns the engine,
the pragma hook and the session lifecycle, so this module calls
:func:`~models.session.create_factoryflow_engine` and
:func:`~models.session.create_session_factory` and adds none of its own. It deliberately
does *not* call :func:`~models.session.initialize_database`: that function creates a schema
when the file is absent, writes ``journal_mode`` and runs ``PRAGMA integrity_check`` over
the whole file. Those belong to the components that write. A viewer that created an empty
database because a path was mistyped, or that spent a cold start integrity-checking 439 MB,
would be doing the wrong thing on both counts. Every statement issued here is a ``SELECT``.

**Presentation is not reimplemented.** :mod:`notification.compose` is the published
formatting authority for measurements, durations, timestamps, horizons and probability, and
:func:`~notification.compose.dashboard_view` is the recommendation projection. Both are
imported and used as-is, so the dashboard and the WhatsApp message cannot disagree.

**Telemetry is read through the one index that exists.** ``machine_sensor_reading`` carries
3.6 million rows and a single index, the unique ``(machine_id, sequence_number)``. Every
query here is therefore anchored on ``machine_id`` and bounded by a ``sequence_number``
range, which is an index range scan; measured at 2-120 ms against the live database. A
``recorded_at`` predicate or a ``GROUP BY machine_parameter_id`` over the whole table is a
full scan of 3.6 million rows and is never issued. No index is added, because the schema is
frozen.

**Caching returns plain data, never ORM objects or sessions.** Every cached function
converts inside its session scope and hands back dicts and lists, so nothing is read from a
detached instance and no connection is held across a rerun.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import streamlit as st
from sqlalchemy import Engine, event, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from models.master import (
    FailureCategory,
    FailureSeverityLevel,
    Machine,
    MachineParameter,
    MachineType,
    MachineTypeParameter,
    MaintenanceEngineer,
    MaintenanceTeam,
    NotificationRecipient,
    Plant,
    ProductionLine,
    Worker,
)
from models.operational import (
    AiRecommendation,
    MachineOperationalStatus,
    MachineSensorReading,
    Notification,
    NotificationDelivery,
    OperationalAlert,
    OperationalEvent,
    PredictionFeatureSnapshot,
    PredictionResult,
    SupervisorContext,
)
from models.session import create_factoryflow_engine, create_session_factory
from models.system import SystemHealthStatus
from notification.compose import (
    dashboard_view,
    format_duration,
    format_horizon,
    format_measurement,
    format_timestamp,
    iso_timestamp,
)

# How long a read stays cached. Short enough that a dashboard left open follows a running
# pipeline within a minute, long enough that paging around does not re-query constantly.
# Master data changes far less often and gets its own longer window.
LIVE_TTL = 45
REFERENCE_TTL = 600

# How many readings one trend line is built from, and how many are fetched to build it.
# 900 points is finer than any monitor can resolve; 12 000 rows is what they are thinned
# from. Bounding by row count rather than by a sequence-number span matters: a span of
# 240 000 sequence numbers is 12 000 rows for a machine with six parameters and 68 000 for
# one with three, so the same window gave one machine a fast chart and another a slow one.
TREND_POINTS = 900
TREND_FETCH = 12_000

# Rows read from the tail of a machine's telemetry to find the latest value of each of its
# parameters. Readings cycle through a machine's parameters, so a few hundred rows always
# contain one of each; measured at 3 ms for all machines.
LATEST_TAIL = 600

# Alert statuses that mean the case is still being worked. Mirrors the vocabulary the
# Supervisor's gate treats as live.
LIVE_ALERT_STATUSES = ("open", "acknowledged", "escalated")

_ENV_VAR = "FACTORYFLOW_DB"


class DatabaseUnavailable(RuntimeError):
    """The dashboard could not open a FactoryFlow database.

    Raised with a message written for the person looking at the screen, because that is who
    reads it. The technical detail travels as ``__cause__`` for the log.
    """


# ----------------------------------------------------------------- connection


def resolve_database_path() -> Path:
    """Locate the database without hardcoding one.

    ``FACTORYFLOW_DB`` wins when set, which is how a deployment points the dashboard at its
    own file. Otherwise the repository is searched for a SQLite database, largest first --
    a populated platform database is orders of magnitude bigger than any scratch file, so
    size is a better discriminator than a guessed filename.
    """
    configured = os.environ.get(_ENV_VAR, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_file():
            raise DatabaseUnavailable(
                "%s points at %s, which is not a file." % (_ENV_VAR, path))
        return path.resolve()

    root = Path(__file__).resolve().parents[2]
    found = sorted(
        (p for p in root.rglob("*.db")
         if p.is_file() and p.stat().st_size > 0 and ".git" not in p.parts),
        key=lambda p: p.stat().st_size,
        reverse=True,
    )
    if not found:
        raise DatabaseUnavailable(
            "No FactoryFlow database was found under %s. Set %s to the database file, "
            "or run the pipeline once to create one." % (root, _ENV_VAR))
    return found[0].resolve()


@st.cache_resource(show_spinner=False)
def _engine(db_path: str) -> Engine:
    """One engine per database path, per process, and one that cannot write.

    ``cache_resource`` rather than ``cache_data``: an engine owns a connection pool and must
    not be copied per caller or serialised into a cache.

    **``PRAGMA query_only`` is what makes read-only real.** Everything else in this package
    -- importing no agent, calling no writer, rolling back every session -- is a property of
    the code, and code changes. ``query_only`` is a property of the connection: SQLite itself
    refuses any ``INSERT``, ``UPDATE``, ``DELETE`` or DDL on it, so a future edit that tried
    to write would fail loudly at the database rather than succeed quietly. It is set through
    a ``connect`` hook because, like ``foreign_keys``, it is per connection and does not
    persist; setting it once after ``create_engine`` would cover the first pooled connection
    and no other.

    The engine itself comes from :mod:`models.session`, so the platform's own pragma hook and
    pool configuration still apply. This adds one pragma and nothing else.
    """
    try:
        engine = create_factoryflow_engine(Path(db_path))
    except Exception as exc:  # noqa: BLE001 -- re-raised as the friendly error
        raise DatabaseUnavailable(
            "The FactoryFlow AI database at %s could not be opened." % db_path) from exc

    @event.listens_for(engine, "connect")
    def _read_only(dbapi_connection: Any, connection_record: Any) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA query_only = ON")
        cursor.close()

    return engine


@contextmanager
def _read(db_path: str) -> Iterator[Session]:
    """A session for one read. Rolls back on exit, so nothing can be written by accident.

    ``session_scope`` in :mod:`models.session` commits, which is right for the twenty
    transaction boundaries the platform writes through and wrong here: this package has no
    boundary of its own and must not create one. Rolling back unconditionally makes the
    read-only intent structural rather than assumed.
    """
    factory: sessionmaker[Session] = create_session_factory(_engine(db_path))
    session = factory()
    try:
        yield session
    except SQLAlchemyError as exc:
        raise DatabaseUnavailable(
            "A FactoryFlow AI database query failed.") from exc
    finally:
        session.rollback()
        session.close()


@st.cache_data(ttl=REFERENCE_TTL, show_spinner=False)
def describe_database(db_path: str) -> dict[str, Any]:
    """Enough about the database to prove the dashboard is attached to a real one."""
    path = Path(db_path)
    with _read(db_path) as session:
        plant = session.scalars(select(Plant).limit(1)).first()
        return {
            "path": str(path),
            "name": path.name,
            "size_mb": round(path.stat().st_size / 1e6, 1),
            "plant_name": None if plant is None else plant.plant_name,
            "plant_code": None if plant is None else plant.plant_code,
            "timezone": None if plant is None else plant.timezone,
        }


def _plant_timezone(session: Session) -> ZoneInfo:
    """The plant clock, from the same master column the Notification Service reads."""
    plant = session.scalars(select(Plant).limit(1)).first()
    if plant is None:
        return ZoneInfo("UTC")
    try:
        return ZoneInfo(plant.timezone)
    except Exception:  # noqa: BLE001 -- a bad zone must not blank the dashboard
        return ZoneInfo("UTC")


# --------------------------------------------------------------- small helpers


def _enum(value: Any) -> str | None:
    """The stored value of an enum column, not its Python repr.

    Load-bearing. The platform's vocabularies are ``class X(str, Enum)``, and on Python
    3.11 ``str(member)`` returns ``'AlertStatus.OPEN'`` rather than ``'open'`` -- ``Enum``
    supplies ``__str__`` and the ``str`` mixin does not override it. Every comparison
    against a vocabulary, every colour lookup and every filter in this dashboard keys on
    the stored value, so conversion goes through here rather than through ``str``.
    """
    if value is None:
        return None
    if isinstance(value, Enum):
        return str(value.value)
    return str(value)


def _probability(value: Decimal | float | None) -> float | None:
    return None if value is None else float(value)


def _percent(value: Decimal | float | None) -> str:
    """Failure probability, formatted the one way the platform formats it."""
    return "not predicted" if value is None else "%.2f%%" % (float(value) * 100.0)


def _severity(level: FailureSeverityLevel | None) -> dict[str, Any]:
    if level is None:
        return {"code": None, "name": "unknown", "rank": None, "colour": None}
    return {
        "code": level.failure_severity_level_code,
        "name": level.severity_name,
        "rank": level.severity_rank,
        "colour": getattr(level, "display_color_hex", None),
        "requires_line_stop": bool(level.requires_line_stop),
        "requires_immediate_escalation": bool(level.requires_immediate_escalation),
        "target_response_minutes": level.target_response_time_minutes,
    }


def _local(moment: datetime | None, tz: ZoneInfo) -> datetime | None:
    return None if moment is None else moment.astimezone(tz)


def _stamp(moment: datetime | None, tz: ZoneInfo) -> dict[str, Any]:
    """One instant, in both the readable and the auditable form."""
    return {
        "display": format_timestamp(_local(moment, tz)),
        "iso": iso_timestamp(moment),
    }


# -------------------------------------------------------------------- overview


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_overview(db_path: str) -> dict[str, Any]:
    """The counts the first screen reports. Every one is a ``COUNT`` over a real table."""
    with _read(db_path) as session:
        def count(entity: Any, *where: Any) -> int:
            statement = select(func.count()).select_from(entity)
            for clause in where:
                statement = statement.where(clause)
            return int(session.execute(statement).scalar_one())

        machines_total = count(Machine)
        monitored = count(Machine, Machine.is_monitored.is_(True))
        in_service = count(Machine, Machine.lifecycle_status == "in_service")
        live_alerts = count(
            OperationalAlert,
            OperationalAlert.alert_status.in_(LIVE_ALERT_STATUSES))
        escalated_alerts = count(
            OperationalAlert, OperationalAlert.alert_status == "escalated")

        # "Critical machines" is derived, not stored: a machine with a live alert whose
        # current severity demands immediate escalation. Both halves are real columns.
        critical_machines = int(session.execute(
            select(func.count(func.distinct(OperationalAlert.machine_id)))
            .join(FailureSeverityLevel,
                  FailureSeverityLevel.failure_severity_level_id
                  == OperationalAlert.current_severity_level_id)
            .where(OperationalAlert.alert_status.in_(LIVE_ALERT_STATUSES),
                   OperationalAlert.machine_id.is_not(None),
                   FailureSeverityLevel.requires_immediate_escalation.is_(True))
        ).scalar_one())

        escalated_contexts = count(
            SupervisorContext, SupervisorContext.escalation_decision == "escalated")
        suppressed_contexts = count(
            SupervisorContext, SupervisorContext.escalation_decision != "escalated")

        transmitted = count(
            NotificationDelivery,
            NotificationDelivery.delivery_status.in_(("sent", "delivered")))

        return {
            "machines_total": machines_total,
            "machines_monitored": monitored,
            "machines_in_service": in_service,
            "critical_machines": critical_machines,
            "events": count(OperationalEvent),
            "alerts_total": count(OperationalAlert),
            "alerts_live": live_alerts,
            "alerts_escalated": escalated_alerts,
            "predictions": count(PredictionResult),
            "feature_snapshots": count(PredictionFeatureSnapshot),
            "contexts": count(SupervisorContext),
            "contexts_escalated": escalated_contexts,
            "contexts_suppressed": suppressed_contexts,
            "recommendations": count(AiRecommendation),
            "notifications": count(Notification),
            "notifications_suppressed": count(
                Notification, Notification.is_suppressed.is_(True)),
            "deliveries": count(NotificationDelivery),
            "deliveries_transmitted": transmitted,
            "readings": int(session.execute(
                select(func.count()).select_from(MachineSensorReading)).scalar_one()),
        }


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_component_activity(db_path: str) -> list[dict[str, Any]]:
    """What each pipeline component has actually produced, and when.

    **This is evidence of work, not a heartbeat.** ``system_health_status`` is the
    platform's liveness table and it is empty in this database, so no component can be
    reported as "operational" -- doing that would be a claim the data does not support.
    What the database *can* prove is that a component wrote its output and when it last did
    so, which is what each row below reports. Where the health table has a row, its status
    is shown instead, because that is a real heartbeat.
    """
    with _read(db_path) as session:
        tz = _plant_timezone(session)
        heartbeats = {
            _enum(row.component): {
                "status": _enum(row.status),
                "heartbeat": _stamp(row.last_heartbeat_at, tz),
                "failures": row.consecutive_failure_count,
                "error": row.last_error_message,
            }
            for row in session.scalars(select(SystemHealthStatus))
        }

        def latest(entity: Any, column: Any) -> tuple[int, datetime | None]:
            total = int(session.execute(
                select(func.count()).select_from(entity)).scalar_one())
            newest = session.execute(select(func.max(column))).scalar()
            return total, newest

        # (label, component key in system_health_status, table, its own time column,
        #  what one row of that table proves)
        sources = [
            ("Monitoring Agent", "monitoring_agent", OperationalEvent,
             OperationalEvent.detected_at, "detected event"),
            ("Prediction Agent", "prediction_agent", PredictionResult,
             PredictionResult.predicted_at, "prediction"),
            ("Supervisor / Escalation", "supervisor_agent", SupervisorContext,
             SupervisorContext.assembled_at, "escalation verdict"),
            ("Decision Agent", "decision_agent", AiRecommendation,
             AiRecommendation.generated_at, "recommendation"),
            ("Notification Service", "notification_service", Notification,
             Notification.composed_at, "composed notification"),
            ("WhatsApp Delivery", "notification_service", NotificationDelivery,
             NotificationDelivery.attempted_at, "delivery attempt"),
        ]

        rows: list[dict[str, Any]] = []
        for label, key, entity, column, noun in sources:
            total, newest = latest(entity, column)
            beat = heartbeats.get(key)
            rows.append({
                "component": label,
                "output_count": total,
                "output_noun": noun,
                "last_output": _stamp(newest, tz),
                "has_output": total > 0,
                "heartbeat_status": None if beat is None else beat["status"],
                "heartbeat_at": None if beat is None else beat["heartbeat"],
                "heartbeat_error": None if beat is None else beat["error"],
            })

        # The delivery row is only meaningful about the provider if a reference came back.
        confirmed = int(session.execute(
            select(func.count()).select_from(NotificationDelivery)
            .where(NotificationDelivery.provider_reference.is_not(None))
        ).scalar_one())
        rows[-1]["provider_confirmations"] = confirmed
        return rows


# --------------------------------------------------------------------- filters


@st.cache_data(ttl=REFERENCE_TTL, show_spinner=False)
def load_filter_options(db_path: str) -> dict[str, list[dict[str, Any]]]:
    """Filter vocabularies, every one read from the database rather than written here."""
    with _read(db_path) as session:
        machines = [
            {"code": m.machine_code, "name": m.machine_name}
            for m in session.scalars(select(Machine).order_by(Machine.machine_code))
        ]
        severities = [
            _severity(s) for s in session.scalars(
                select(FailureSeverityLevel)
                .order_by(FailureSeverityLevel.severity_rank))
        ]
        categories = [
            {"code": c.failure_category_code, "name": c.category_name}
            for c in session.scalars(
                select(FailureCategory).order_by(FailureCategory.category_name))
        ]
        lines = [
            {"code": line.production_line_code, "name": line.line_name}
            for line in session.scalars(
                select(ProductionLine).order_by(ProductionLine.production_line_code))
        ]
        alert_statuses = sorted({
            _enum(v) for v in session.scalars(
                select(OperationalAlert.alert_status).distinct()) if v})
        delivery_statuses = sorted({
            _enum(v) for v in session.scalars(
                select(NotificationDelivery.delivery_status).distinct()) if v})
        decisions = sorted({
            _enum(v) for v in session.scalars(
                select(SupervisorContext.escalation_decision).distinct()) if v})
        return {
            "machines": machines,
            "severities": severities,
            "categories": categories,
            "lines": lines,
            "alert_statuses": [{"code": v, "name": v.replace("_", " ")}
                               for v in alert_statuses],
            "delivery_statuses": [{"code": v, "name": v} for v in delivery_statuses],
            "escalation_decisions": [{"code": v, "name": v.replace("_", " ")}
                                     for v in decisions],
        }


# -------------------------------------------------------------------- machines


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_machines(db_path: str) -> list[dict[str, Any]]:
    """Every machine, with its live state, worst live alert and newest prediction.

    Three bounded queries and a join in Python rather than one correlated statement per
    machine: eight machines is small, and the shape stays readable.
    """
    with _read(db_path) as session:
        tz = _plant_timezone(session)

        statuses = {
            row.machine_id: row
            for row in session.scalars(select(MachineOperationalStatus))
        }

        # Worst live alert per machine: lowest severity_rank is most severe.
        worst: dict[int, dict[str, Any]] = {}
        alert_rows = session.execute(
            select(OperationalAlert, FailureSeverityLevel)
            .join(FailureSeverityLevel,
                  FailureSeverityLevel.failure_severity_level_id
                  == OperationalAlert.current_severity_level_id)
            .where(OperationalAlert.alert_status.in_(LIVE_ALERT_STATUSES),
                   OperationalAlert.machine_id.is_not(None))
        ).all()
        live_counts: dict[int, int] = {}
        for alert, level in alert_rows:
            live_counts[alert.machine_id] = live_counts.get(alert.machine_id, 0) + 1
            current = worst.get(alert.machine_id)
            if current is None or level.severity_rank < current["severity"]["rank"]:
                worst[alert.machine_id] = {
                    "code": alert.operational_alert_code,
                    "status": _enum(alert.alert_status),
                    "category": _enum(alert.alert_category),
                    "severity": _severity(level),
                    "last_event": _stamp(alert.last_event_at, tz),
                    "event_count": alert.event_count,
                }

        # Newest prediction per machine.
        newest: dict[int, dict[str, Any]] = {}
        prediction_rows = session.execute(
            select(PredictionResult, FailureSeverityLevel, FailureCategory)
            .join(FailureSeverityLevel,
                  FailureSeverityLevel.failure_severity_level_id
                  == PredictionResult.risk_severity_level_id)
            .outerjoin(FailureCategory,
                       FailureCategory.failure_category_id
                       == PredictionResult.predicted_failure_category_id)
            .order_by(PredictionResult.predicted_at)
        ).all()
        for prediction, level, category in prediction_rows:
            newest[prediction.machine_id] = {
                "code": prediction.prediction_result_code,
                "probability": _probability(prediction.failure_probability),
                "probability_display": _percent(prediction.failure_probability),
                "horizon_hours": prediction.prediction_horizon_hours,
                "horizon_display": format_horizon(
                    None if prediction.prediction_horizon_hours is None
                    else prediction.prediction_horizon_hours * 60),
                "risk": _severity(level),
                "category": None if category is None else category.category_name,
                "predicted_at": _stamp(prediction.predicted_at, tz),
                "model": "%s %s" % (prediction.model_name, prediction.model_version),
            }

        # Machines that currently carry a recommendation.
        recommended = {
            row.machine_id: row.ai_recommendation_code
            for row in session.scalars(
                select(AiRecommendation).order_by(AiRecommendation.generated_at))
        }

        machines: list[dict[str, Any]] = []
        rows = session.execute(
            select(Machine, MachineType, ProductionLine)
            .join(MachineType, MachineType.machine_type_id == Machine.machine_type_id)
            .join(ProductionLine,
                  ProductionLine.production_line_id == Machine.production_line_id)
            .order_by(Machine.machine_code)
        ).all()
        for machine, machine_type, line in rows:
            status = statuses.get(machine.machine_id)
            machines.append({
                "machine_id": machine.machine_id,
                "code": machine.machine_code,
                "name": machine.machine_name,
                "type_code": machine_type.machine_type_code,
                "type_name": machine_type.type_name,
                "line_code": line.production_line_code,
                "line_name": line.line_name,
                "criticality": _enum(machine.criticality),
                "lifecycle": _enum(machine.lifecycle_status),
                "is_monitored": bool(machine.is_monitored),
                "is_bottleneck": bool(machine.is_bottleneck),
                "state": None if status is None else _enum(status.current_state),
                "state_since": None if status is None
                else _stamp(status.state_since, tz),
                "last_reading_at": None if status is None
                else _stamp(status.last_reading_at, tz),
                "has_telemetry": bool(status is not None
                                      and status.last_reading_at is not None),
                "open_alert_count": 0 if status is None else status.open_alert_count,
                "live_alert_count": live_counts.get(machine.machine_id, 0),
                "alert": worst.get(machine.machine_id),
                "prediction": newest.get(machine.machine_id),
                "recommendation": recommended.get(machine.machine_id),
            })
        return machines


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_machine_detail(db_path: str, machine_code: str) -> dict[str, Any] | None:
    """One machine, with the assigned response taken from its recommendation if any."""
    for machine in load_machines(db_path):
        if machine["code"] != machine_code:
            continue
        detail = dict(machine)
        code = machine.get("recommendation")
        if code:
            recommendation = load_recommendation_detail(db_path, code)
            if recommendation is not None:
                detail["assigned"] = {
                    "engineer": recommendation["engineer"],
                    "team": recommendation["team"],
                    "deadline": recommendation["fields"]["deadline"]["display"],
                    "downtime": recommendation["fields"]["estimated_downtime"]["display"],
                    "failure": recommendation["fields"]["failure_type"]["display"],
                    "recommendation_code": code,
                }
        return detail
    return None


# ------------------------------------------------------------------- telemetry


@st.cache_data(ttl=REFERENCE_TTL, show_spinner=False)
def load_parameter_catalogue(db_path: str, machine_code: str) -> list[dict[str, Any]]:
    """The parameters this machine's type actually declares, with their healthy envelope.

    Read from ``machine_type_parameter`` rather than discovered by scanning telemetry: the
    declaration is the authority on what a machine measures, and a ``DISTINCT`` over 3.6
    million readings to learn the same six rows would be a full table scan.
    """
    with _read(db_path) as session:
        machine = session.scalars(
            select(Machine).where(Machine.machine_code == machine_code)).first()
        if machine is None:
            return []
        rows = session.execute(
            select(MachineTypeParameter, MachineParameter)
            .join(MachineParameter,
                  MachineParameter.machine_parameter_id
                  == MachineTypeParameter.machine_parameter_id)
            .where(MachineTypeParameter.machine_type_id == machine.machine_type_id,
                   MachineTypeParameter.is_active.is_(True))
            .order_by(MachineParameter.machine_parameter_code)
        ).all()
        return [
            {
                "parameter_id": parameter.machine_parameter_id,
                "code": parameter.machine_parameter_code,
                "name": parameter.parameter_name,
                "unit": parameter.unit_of_measure,
                "normal_min": None if declared.normal_min is None
                else float(declared.normal_min),
                "normal_max": None if declared.normal_max is None
                else float(declared.normal_max),
                "normal_min_display": format_measurement(
                    declared.normal_min, parameter.unit_of_measure),
                "normal_max_display": format_measurement(
                    declared.normal_max, parameter.unit_of_measure),
                "nominal": None if declared.nominal_value is None
                else float(declared.nominal_value),
                "is_ml_feature": bool(declared.is_ml_feature),
                "sampling_seconds": declared.sampling_interval_seconds,
            }
            for declared, parameter in rows
        ]


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_latest_readings(db_path: str, machine_code: str) -> dict[str, dict[str, Any]]:
    """The newest value of each parameter, keyed by parameter code.

    Reads the tail of the machine's own telemetry by ``sequence_number``, which is the
    indexed path. Returns an empty mapping for a machine with no readings, which is a real
    state here: five of the eight machines declare parameters but record nothing.
    """
    with _read(db_path) as session:
        machine = session.scalars(
            select(Machine).where(Machine.machine_code == machine_code)).first()
        if machine is None:
            return {}
        tz = _plant_timezone(session)
        top = session.execute(
            select(func.max(MachineSensorReading.sequence_number))
            .where(MachineSensorReading.machine_id == machine.machine_id)
        ).scalar()
        if top is None:
            return {}

        parameters = {
            p.machine_parameter_id: p
            for p in session.scalars(select(MachineParameter))
        }
        latest: dict[str, dict[str, Any]] = {}
        rows = session.scalars(
            select(MachineSensorReading)
            .where(MachineSensorReading.machine_id == machine.machine_id,
                   MachineSensorReading.sequence_number > top - LATEST_TAIL)
            .order_by(MachineSensorReading.sequence_number.desc())
        )
        for reading in rows:
            parameter = parameters.get(reading.machine_parameter_id)
            if parameter is None or parameter.machine_parameter_code in latest:
                continue
            latest[parameter.machine_parameter_code] = {
                "parameter_code": parameter.machine_parameter_code,
                "parameter_name": parameter.parameter_name,
                "unit": parameter.unit_of_measure,
                "value": None if reading.reading_value is None
                else float(reading.reading_value),
                "display": format_measurement(reading.reading_value,
                                              parameter.unit_of_measure),
                "recorded_at": _stamp(reading.recorded_at, tz),
                "quality": _enum(reading.quality_flag),
                "state": _enum(reading.machine_state_at_reading),
            }
        return latest


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_trend(
    db_path: str,
    machine_code: str,
    parameter_code: str,
    *,
    only_valid: bool = True,
) -> dict[str, Any]:
    """One parameter's recent history, downsampled to a chart-sized series.

    Bounded twice: by an indexed ``sequence_number`` window, and by a stride that thins the
    window to about :data:`TREND_POINTS` points. Both are necessary -- a machine records a
    reading per parameter every few seconds, so an unthinned month of history is hundreds
    of thousands of points that no chart can draw and no browser should receive.

    ``only_valid`` drops readings whose ``quality_flag`` is not ``valid``. Those are
    instrument faults, not machine condition: the simulator's out-of-range spikes reach the
    parameter's physical maximum and would compress the real signal into a flat line. The
    count of what was excluded is returned so the page can say so rather than hide it.
    """
    with _read(db_path) as session:
        machine = session.scalars(
            select(Machine).where(Machine.machine_code == machine_code)).first()
        parameter = session.scalars(
            select(MachineParameter)
            .where(MachineParameter.machine_parameter_code == parameter_code)).first()
        if machine is None or parameter is None:
            return {"points": [], "excluded": 0, "unit": None}

        tz = _plant_timezone(session)
        top = session.execute(
            select(func.max(MachineSensorReading.sequence_number))
            .where(MachineSensorReading.machine_id == machine.machine_id)
        ).scalar()
        if top is None:
            return {"points": [], "excluded": 0,
                    "unit": parameter.unit_of_measure}

        # Columns, not entities. Materialising an ORM instance per row was measured at
        # 3.6 s against 0.1 s for a column select -- the object graph is pure overhead for
        # a chart series.
        columns = (MachineSensorReading.recorded_at, MachineSensorReading.reading_value,
                   MachineSensorReading.quality_flag,
                   MachineSensorReading.machine_state_at_reading)
        # The newest TREND_FETCH readings for this parameter. Ordering by
        # ``sequence_number`` descending walks the ``(machine_id, sequence_number)`` index
        # backwards from the machine's latest row, so the scan stops as soon as enough
        # matching rows are collected however long the machine's history is.
        rows = list(reversed(session.execute(
            select(*columns)
            .where(MachineSensorReading.machine_id == machine.machine_id,
                   MachineSensorReading.machine_parameter_id
                   == parameter.machine_parameter_id)
            .order_by(MachineSensorReading.sequence_number.desc())
            .limit(TREND_FETCH)
        ).all()))

        # Thinned in Python rather than by a SQL modulo on sequence_number. A parameter's
        # sequence numbers are an arithmetic progression whose step is set by its own
        # sampling interval, so `sequence_number % stride = 0` selects an unpredictable
        # fraction of them; slicing the fetched rows selects exactly one in `stride`.
        stride = max(1, len(rows) // TREND_POINTS)
        thinned = rows[::stride]
        # The newest reading is what an operator checks first, so it is never thinned out.
        if rows and thinned[-1] is not rows[-1]:
            thinned.append(rows[-1])

        excluded = 0
        points: list[dict[str, Any]] = []
        for recorded_at, value, quality, state in thinned:
            if value is None:
                continue
            if only_valid and _enum(quality) != "valid":
                excluded += 1
                continue
            points.append({
                "recorded_at": _local(recorded_at, tz),
                "value": float(value),
                "state": _enum(state),
            })

        return {
            "unit": parameter.unit_of_measure,
            "parameter_name": parameter.parameter_name,
            "excluded": excluded,
            "window_rows": len(rows),
            "stride": stride,
            "points": points,
        }


# ---------------------------------------------------------------------- alerts


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_alerts(db_path: str) -> list[dict[str, Any]]:
    """Every alert, with its machine, severity, gate verdict and prediction if any."""
    with _read(db_path) as session:
        tz = _plant_timezone(session)

        # The gate's verdict per alert, newest first.
        verdicts: dict[int, dict[str, Any]] = {}
        for context in session.scalars(
            select(SupervisorContext).order_by(SupervisorContext.assembled_at)
        ):
            verdicts[context.triggering_alert_id] = {
                "code": context.supervisor_context_code,
                "decision": _enum(context.escalation_decision),
                "rationale": context.escalation_rationale,
                "assembled_at": _stamp(context.assembled_at, tz),
                "prediction_id": context.triggering_prediction_id,
            }

        predictions = {
            p.prediction_result_id: p
            for p in session.scalars(select(PredictionResult))
        }
        by_alert = {
            p.triggering_alert_id: p for p in predictions.values()
            if p.triggering_alert_id is not None
        }

        rows = session.execute(
            select(OperationalAlert, FailureSeverityLevel, Machine)
            .join(FailureSeverityLevel,
                  FailureSeverityLevel.failure_severity_level_id
                  == OperationalAlert.current_severity_level_id)
            .outerjoin(Machine, Machine.machine_id == OperationalAlert.machine_id)
            .order_by(OperationalAlert.last_event_at.desc())
        ).all()

        alerts: list[dict[str, Any]] = []
        for alert, level, machine in rows:
            verdict = verdicts.get(alert.operational_alert_id)
            prediction = by_alert.get(alert.operational_alert_id)
            if prediction is None and verdict and verdict.get("prediction_id"):
                prediction = predictions.get(verdict["prediction_id"])
            alerts.append({
                "alert_id": alert.operational_alert_id,
                "code": alert.operational_alert_code,
                "category": _enum(alert.alert_category),
                "status": _enum(alert.alert_status),
                "severity": _severity(level),
                "machine_code": None if machine is None else machine.machine_code,
                "machine_name": None if machine is None else machine.machine_name,
                "event_count": alert.event_count,
                "opened_at": _stamp(alert.opened_at, tz),
                "last_event_at": _stamp(alert.last_event_at, tz),
                "escalated_at": _stamp(alert.escalated_at, tz)
                if alert.escalated_at else None,
                "resolved_at": _stamp(alert.resolved_at, tz)
                if alert.resolved_at else None,
                "correlation_key": alert.correlation_key,
                "suppression_reason": _enum(alert.suppression_reason),
                "resolution_type": _enum(alert.resolution_type),
                "verdict": verdict,
                "prediction_code": None if prediction is None
                else prediction.prediction_result_code,
                "probability": None if prediction is None
                else _probability(prediction.failure_probability),
                "probability_display": "not predicted" if prediction is None
                else _percent(prediction.failure_probability),
            })
        return alerts


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_alert_detail(db_path: str, alert_code: str) -> dict[str, Any] | None:
    """One alert, plus a bounded sample of the events that built it."""
    with _read(db_path) as session:
        alert = session.scalars(
            select(OperationalAlert)
            .where(OperationalAlert.operational_alert_code == alert_code)).first()
        if alert is None:
            return None
        tz = _plant_timezone(session)
        parameters = {
            p.machine_parameter_id: p
            for p in session.scalars(select(MachineParameter))
        }

        # Events are the evidence, and there can be thousands. The newest 200 are read for
        # display and the total is counted separately, so the page reports the real size
        # without transferring it.
        total = int(session.execute(
            select(func.count()).select_from(OperationalEvent)
            .where(OperationalEvent.operational_alert_id == alert.operational_alert_id)
        ).scalar_one())
        events = session.scalars(
            select(OperationalEvent)
            .where(OperationalEvent.operational_alert_id == alert.operational_alert_id)
            .order_by(OperationalEvent.detected_at.desc())
            .limit(200)
        ).all()

        by_type: dict[str, int] = {}
        for event in events:
            key = _enum(event.event_type) or "unknown"
            by_type[key] = by_type.get(key, 0) + 1

        header = next((a for a in load_alerts(db_path) if a["code"] == alert_code), None)
        return {
            "header": header,
            "event_total": total,
            "events_shown": len(events),
            "event_types": by_type,
            "events": [
                {
                    "code": event.operational_event_code,
                    "type": _enum(event.event_type),
                    "category": _enum(event.event_category),
                    "detected_at": _stamp(event.detected_at, tz),
                    "parameter": None if event.machine_parameter_id is None
                    else getattr(parameters.get(event.machine_parameter_id),
                                 "parameter_name", None),
                    "unit": None if event.machine_parameter_id is None
                    else getattr(parameters.get(event.machine_parameter_id),
                                 "unit_of_measure", None),
                    "observed": None if event.observed_value is None
                    else float(event.observed_value),
                    "observed_display": format_measurement(
                        event.observed_value,
                        getattr(parameters.get(event.machine_parameter_id or -1),
                                "unit_of_measure", None)),
                    "threshold": None if event.threshold_value_breached is None
                    else float(event.threshold_value_breached),
                    "threshold_display": format_measurement(
                        event.threshold_value_breached,
                        getattr(parameters.get(event.machine_parameter_id or -1),
                                "unit_of_measure", None)),
                    "direction": _enum(event.threshold_direction),
                    "sustained_seconds": event.sustained_duration_seconds,
                    "note": event.detection_note,
                }
                for event in events
            ],
        }


# ----------------------------------------------------------------- predictions


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_predictions(db_path: str) -> list[dict[str, Any]]:
    """Every prediction, quoted. No probability is recomputed here."""
    with _read(db_path) as session:
        tz = _plant_timezone(session)
        rows = session.execute(
            select(PredictionResult, FailureSeverityLevel, Machine, FailureCategory,
                   OperationalAlert)
            .join(FailureSeverityLevel,
                  FailureSeverityLevel.failure_severity_level_id
                  == PredictionResult.risk_severity_level_id)
            .join(Machine, Machine.machine_id == PredictionResult.machine_id)
            .outerjoin(FailureCategory,
                       FailureCategory.failure_category_id
                       == PredictionResult.predicted_failure_category_id)
            .outerjoin(OperationalAlert,
                       OperationalAlert.operational_alert_id
                       == PredictionResult.triggering_alert_id)
            .order_by(PredictionResult.predicted_at.desc())
        ).all()

        recommendations = {
            r.prediction_result_id: r.ai_recommendation_code
            for r in session.scalars(select(AiRecommendation))
        }

        return [
            {
                "prediction_id": prediction.prediction_result_id,
                "code": prediction.prediction_result_code,
                "machine_code": machine.machine_code,
                "machine_name": machine.machine_name,
                "category": None if category is None else category.category_name,
                "category_code": None if category is None
                else category.failure_category_code,
                "probability": _probability(prediction.failure_probability),
                "probability_display": _percent(prediction.failure_probability),
                "horizon_hours": prediction.prediction_horizon_hours,
                "horizon_display": format_horizon(
                    None if prediction.prediction_horizon_hours is None
                    else prediction.prediction_horizon_hours * 60),
                "risk": _severity(level),
                "predicted_at": _stamp(prediction.predicted_at, tz),
                "model_name": prediction.model_name,
                "model_version": prediction.model_version,
                "inference_ms": prediction.inference_duration_ms,
                "alert_code": None if alert is None
                else alert.operational_alert_code,
                "recommendation_code": recommendations.get(
                    prediction.prediction_result_id),
                "band_low": _probability(prediction.confidence_band_low),
                "band_high": _probability(prediction.confidence_band_high),
            }
            for prediction, level, machine, category, alert in rows
        ]


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_prediction_detail(db_path: str, code: str) -> dict[str, Any] | None:
    """One prediction, with its feature attributions and the snapshot behind it."""
    with _read(db_path) as session:
        prediction = session.scalars(
            select(PredictionResult)
            .where(PredictionResult.prediction_result_code == code)).first()
        if prediction is None:
            return None
        tz = _plant_timezone(session)
        snapshot = session.get(PredictionFeatureSnapshot,
                               prediction.prediction_feature_snapshot_id)

        raw = prediction.top_contributing_features
        contributions = [
            {
                "feature": str(item.get("feature", "")),
                "value": item.get("value"),
                "contribution": item.get("contribution"),
            }
            for item in (raw if isinstance(raw, list) else [])
            if isinstance(item, dict) and item.get("feature")
        ]

        header = next((p for p in load_predictions(db_path) if p["code"] == code), None)
        return {
            "header": header,
            "contributions": contributions,
            "snapshot": None if snapshot is None else {
                "code": snapshot.prediction_feature_snapshot_code,
                "generated_at": _stamp(snapshot.generated_at, tz),
                "window_from": _stamp(snapshot.window_from, tz),
                "window_to": _stamp(snapshot.window_to, tz),
                "lookback_display": format_duration(
                    snapshot.lookback_window_seconds // 60),
                "lookback_seconds": snapshot.lookback_window_seconds,
                "feature_set_version": snapshot.feature_set_version,
                "source_readings": snapshot.source_reading_count,
                "excluded_readings": snapshot.excluded_reading_count,
                "completeness": None if snapshot.data_completeness_pct is None
                else float(snapshot.data_completeness_pct),
                "is_sufficient": bool(snapshot.is_sufficient_for_inference),
                "insufficiency_reason": _enum(snapshot.insufficiency_reason),
                "feature_count": len(snapshot.feature_values)
                if isinstance(snapshot.feature_values, dict) else 0,
                "feature_values": snapshot.feature_values
                if isinstance(snapshot.feature_values, dict) else {},
            },
        }


# ------------------------------------------------------------- recommendations


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_recommendations(db_path: str) -> list[dict[str, Any]]:
    """Every recommendation, as a summary row. Full text comes from the detail loader."""
    with _read(db_path) as session:
        tz = _plant_timezone(session)
        rows = session.execute(
            select(AiRecommendation, Machine, FailureSeverityLevel, FailureCategory,
                   PredictionResult)
            .join(Machine, Machine.machine_id == AiRecommendation.machine_id)
            .join(FailureSeverityLevel,
                  FailureSeverityLevel.failure_severity_level_id
                  == AiRecommendation.priority_severity_level_id)
            .join(FailureCategory,
                  FailureCategory.failure_category_id
                  == AiRecommendation.root_cause_failure_category_id)
            .join(PredictionResult,
                  PredictionResult.prediction_result_id
                  == AiRecommendation.prediction_result_id)
            .order_by(AiRecommendation.generated_at.desc())
        ).all()
        return [
            {
                "code": recommendation.ai_recommendation_code,
                "machine_code": machine.machine_code,
                "machine_name": machine.machine_name,
                "severity": _severity(level),
                "category": category.category_name,
                "probability": _probability(prediction.failure_probability),
                "probability_display": _percent(prediction.failure_probability),
                "horizon_display": format_horizon(
                    None if prediction.prediction_horizon_hours is None
                    else prediction.prediction_horizon_hours * 60),
                "prediction_code": prediction.prediction_result_code,
                "downtime_display": format_duration(
                    recommendation.estimated_downtime_minutes),
                "downtime_minutes": recommendation.estimated_downtime_minutes,
                "deadline": _stamp(recommendation.recommended_action_by, tz),
                "generated_at": _stamp(recommendation.generated_at, tz),
                "confidence": _enum(recommendation.root_cause_confidence),
                "contract_complete": bool(recommendation.contract_complete),
                "llm": "%s %s" % (recommendation.llm_model_name,
                                  recommendation.llm_model_version),
                "action_chars": len(recommendation.recommended_action or ""),
            }
            for recommendation, machine, level, category, prediction in rows
        ]


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_recommendation_detail(db_path: str, code: str) -> dict[str, Any] | None:
    """One recommendation, projected through the platform's own dashboard contract.

    The field set comes from :func:`notification.compose.dashboard_view`, so the labels,
    the display strings and the raw values beside them are exactly what the presentation
    layer publishes -- the dashboard formats nothing itself and cannot drift from the
    WhatsApp message.
    """
    with _read(db_path) as session:
        recommendation = session.scalars(
            select(AiRecommendation)
            .where(AiRecommendation.ai_recommendation_code == code)).first()
        if recommendation is None:
            return None

        tz = _plant_timezone(session)
        machine = session.get(Machine, recommendation.machine_id)
        line = session.get(ProductionLine, recommendation.production_line_id)
        severity = session.get(FailureSeverityLevel,
                               recommendation.priority_severity_level_id)
        category = session.get(FailureCategory,
                               recommendation.root_cause_failure_category_id)
        prediction = session.get(PredictionResult, recommendation.prediction_result_id)
        context = session.get(SupervisorContext, recommendation.supervisor_context_id)

        engineer_code = None
        engineer_name = None
        if recommendation.suggested_engineer_id is not None:
            engineer = session.get(MaintenanceEngineer,
                                   recommendation.suggested_engineer_id)
            if engineer is not None:
                engineer_code = engineer.maintenance_engineer_code
                worker = session.get(Worker, engineer.worker_id)
                if worker is not None:
                    engineer_name = "%s %s" % (worker.first_name, worker.last_name)

        team_code = None
        team_name = None
        if recommendation.suggested_maintenance_team_id is not None:
            team = session.get(MaintenanceTeam,
                               recommendation.suggested_maintenance_team_id)
            if team is not None:
                team_code = team.maintenance_team_code
                team_name = team.team_name

        # Delivery state for this recommendation, so the projection can report it.
        notifications = session.scalars(
            select(Notification)
            .where(Notification.ai_recommendation_id
                   == recommendation.ai_recommendation_id)
        ).all()
        statuses: list[str] = []
        for notification in notifications:
            for delivery in session.scalars(
                select(NotificationDelivery)
                .where(NotificationDelivery.notification_id
                       == notification.notification_id)
            ):
                statuses.append(_enum(delivery.delivery_status) or "")
        notification_status = None
        for candidate in ("delivered", "sent", "queued", "failed", "rejected",
                          "bounced"):
            if candidate in statuses:
                notification_status = candidate
                break

        view = dashboard_view(
            recommendation,
            machine=machine,
            line=line,
            severity=severity,
            root_cause=category,
            failure_probability=(None if prediction is None
                                 else float(prediction.failure_probability)),
            prediction_code=(None if prediction is None
                             else prediction.prediction_result_code),
            prediction_horizon_hours=(None if prediction is None
                                      else prediction.prediction_horizon_hours),
            timezone=tz,
            engineer=engineer_code,
            team=team_code,
            notification_status=notification_status,
        )

        return {
            "code": recommendation.ai_recommendation_code,
            "fields": view,
            "machine_code": None if machine is None else machine.machine_code,
            "machine_name": None if machine is None else machine.machine_name,
            "severity": _severity(severity),
            "engineer": engineer_code,
            "engineer_name": engineer_name,
            "team": team_code,
            "team_name": team_name,
            "generated_at": _stamp(recommendation.generated_at, tz),
            "llm_model": "%s %s" % (recommendation.llm_model_name,
                                    recommendation.llm_model_version),
            "generation_ms": recommendation.generation_duration_ms,
            "prompt_tokens": recommendation.prompt_token_count,
            "completion_tokens": recommendation.completion_token_count,
            "confidence": _enum(recommendation.root_cause_confidence),
            "contract_complete": bool(recommendation.contract_complete),
            "business_impact": recommendation.business_impact
            if isinstance(recommendation.business_impact, dict) else {},
            "context_code": None if context is None
            else context.supervisor_context_code,
            "alert_code": None if context is None
            else getattr(context.triggering_alert, "operational_alert_code", None),
            "prediction_code": None if prediction is None
            else prediction.prediction_result_code,
            "notification_codes": [n.notification_code for n in notifications],
            "notification_status": notification_status,
        }


# -------------------------------------------------------------------- evidence


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_evidence(db_path: str, code: str) -> dict[str, Any] | None:
    """The ``supporting_evidence`` document as persisted, plus its headline counts.

    Nothing is regenerated. The document is the Decision Agent's own, verbatim from
    ``ai_recommendation.supporting_evidence``.
    """
    with _read(db_path) as session:
        recommendation = session.scalars(
            select(AiRecommendation)
            .where(AiRecommendation.ai_recommendation_code == code)).first()
        if recommendation is None:
            return None
        document = recommendation.supporting_evidence \
            if isinstance(recommendation.supporting_evidence, dict) else {}

        def listed(key: str) -> list[Any]:
            value = document.get(key)
            return value if isinstance(value, list) else []

        events = listed("events")
        readings = listed("readings")
        corroboration = listed("corroboration")
        contributions = listed("feature_contributions")
        parameters = sorted({
            str(entry.get("parameter")) for entry in events + readings
            if isinstance(entry, dict) and entry.get("parameter")
        })
        return {
            "code": code,
            "document": document,
            "event_count": len(events),
            "reading_count": len(readings),
            "corroboration_count": len(corroboration),
            "parameter_count": len(parameters),
            "parameters": parameters,
            "readings": readings,
            "corroboration": corroboration,
            "feature_contributions": contributions,
            "ml_confidence": document.get("ml_confidence")
            if isinstance(document.get("ml_confidence"), dict) else {},
            "sample_events": events[:50],
        }


# --------------------------------------------------------------- notifications


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_notifications(db_path: str) -> list[dict[str, Any]]:
    """Every notification with its delivery attempts.

    No phone number appears here, and none is read. ``notification_recipient`` holds a
    worker reference and channel switches; the WhatsApp destination lives in the
    environment, which this package never opens. The recipient is identified by worker code
    and name, which is what an operator needs and carries no secret.
    """
    with _read(db_path) as session:
        tz = _plant_timezone(session)

        attempts: dict[int, list[dict[str, Any]]] = {}
        for delivery in session.scalars(
            select(NotificationDelivery)
            .order_by(NotificationDelivery.notification_id,
                      NotificationDelivery.attempt_number)
        ):
            attempts.setdefault(delivery.notification_id, []).append({
                "attempt": delivery.attempt_number,
                "channel": _enum(delivery.channel),
                "status": _enum(delivery.delivery_status),
                "attempted_at": _stamp(delivery.attempted_at, tz),
                "delivered_at": _stamp(delivery.delivered_at, tz)
                if delivery.delivered_at else None,
                "provider_reference": delivery.provider_reference,
                "failure_reason": _enum(delivery.failure_reason),
                "failure_detail": delivery.failure_detail,
                "latency_ms": delivery.latency_ms,
            })

        rows = session.execute(
            select(Notification, AiRecommendation, FailureSeverityLevel)
            .outerjoin(AiRecommendation,
                       AiRecommendation.ai_recommendation_id
                       == Notification.ai_recommendation_id)
            .join(FailureSeverityLevel,
                  FailureSeverityLevel.failure_severity_level_id
                  == Notification.severity_level_id)
            .order_by(Notification.composed_at.desc(),
                      Notification.notification_id.desc())
        ).all()

        machines = {m.machine_id: m for m in session.scalars(select(Machine))}
        recipients = {
            r.notification_recipient_id: r
            for r in session.scalars(select(NotificationRecipient))
        }
        workers = {w.worker_id: w for w in session.scalars(select(Worker))}

        notifications: list[dict[str, Any]] = []
        for notification, recommendation, level in rows:
            recipient = recipients.get(notification.notification_recipient_id)
            worker = None if recipient is None else workers.get(recipient.worker_id)
            machine = None
            if recommendation is not None:
                machine = machines.get(recommendation.machine_id)
            tries = attempts.get(notification.notification_id, [])
            notifications.append({
                "code": notification.notification_code,
                "type": _enum(notification.notification_type),
                "severity": _severity(level),
                "recommendation_code": None if recommendation is None
                else recommendation.ai_recommendation_code,
                "machine_code": None if machine is None else machine.machine_code,
                "machine_name": None if machine is None else machine.machine_name,
                "recipient_code": None if worker is None else worker.worker_code,
                "recipient_name": None if worker is None
                else "%s %s" % (worker.first_name, worker.last_name),
                "escalation_order": notification.escalation_order_applied,
                "composed_at": _stamp(notification.composed_at, tz),
                "is_suppressed": bool(notification.is_suppressed),
                "suppression_reason": _enum(notification.suppression_reason),
                "requires_ack": bool(notification.requires_acknowledgement),
                "ack_deadline": _stamp(notification.acknowledgement_deadline_at, tz)
                if notification.acknowledgement_deadline_at else None,
                "subject": notification.subject,
                "body": notification.body_text,
                "attempts": tries,
                "status": tries[-1]["status"] if tries else None,
                "channel": tries[-1]["channel"] if tries else None,
                "provider_reference": tries[-1]["provider_reference"] if tries else None,
                "latency_ms": tries[-1]["latency_ms"] if tries else None,
            })
        return notifications


# ---------------------------------------------------------------- traceability


@st.cache_data(ttl=LIVE_TTL, show_spinner=False)
def load_traceability(db_path: str) -> list[dict[str, Any]]:
    """The chain, one row per recommendation, built only from foreign keys that exist.

    Prediction -> Alert -> Context -> Recommendation -> Notification. Every link is a real
    column: ``supervisor_context.triggering_prediction_id`` and ``triggering_alert_id``,
    ``ai_recommendation.supervisor_context_id`` and ``prediction_result_id``, and
    ``notification.ai_recommendation_id``. Nothing is inferred by matching codes or times,
    so a link that is absent is shown as absent.
    """
    with _read(db_path) as session:
        tz = _plant_timezone(session)
        chains: list[dict[str, Any]] = []
        for recommendation in session.scalars(
            select(AiRecommendation).order_by(AiRecommendation.generated_at.desc())
        ):
            context = session.get(SupervisorContext,
                                  recommendation.supervisor_context_id)
            prediction = session.get(PredictionResult,
                                     recommendation.prediction_result_id)
            alert = None if context is None else context.triggering_alert
            machine = session.get(Machine, recommendation.machine_id)
            notifications = session.scalars(
                select(Notification)
                .where(Notification.ai_recommendation_id
                       == recommendation.ai_recommendation_id)
                .order_by(Notification.notification_id)
            ).all()
            deliveries: list[str] = []
            for notification in notifications:
                for delivery in session.scalars(
                    select(NotificationDelivery)
                    .where(NotificationDelivery.notification_id
                           == notification.notification_id)
                ):
                    deliveries.append(_enum(delivery.delivery_status) or "")
            chains.append({
                "machine_code": None if machine is None else machine.machine_code,
                "prediction": None if prediction is None
                else prediction.prediction_result_code,
                "alert": None if alert is None else alert.operational_alert_code,
                "context": None if context is None
                else context.supervisor_context_code,
                "recommendation": recommendation.ai_recommendation_code,
                "notifications": [n.notification_code for n in notifications],
                "notification_count": len(notifications),
                "delivery_statuses": deliveries,
                "generated_at": _stamp(recommendation.generated_at, tz),
            })
        return chains
