"""Master models M13-M17: roles, people, and the two role extensions of a worker.

``MaintenanceEngineer`` and ``NotificationRecipient`` extend ``Worker`` as
one-to-one tables rather than by ORM inheritance. A worker may be an engineer
*and* a recipient simultaneously, which single inheritance cannot express at all,
and a joined-table subclass would add an implicit join to every ``Worker`` query
(§33).

Column order follows the frozen schema exactly. Where that differs from §45.3's
declaration order -- ``Worker`` places ``first_name`` and ``last_name`` before its
foreign keys -- the frozen schema governs, because the generated DDL has to match
it column for column.
"""

from datetime import date
from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.master import (
    EmploymentType,
    MaintenanceSpecialization,
    RoleCategory,
    SkillLevel,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk

if TYPE_CHECKING:
    from models.master.failure import FailureSeverityLevel
    from models.master.maintenance import MachineMaintenanceSchedule
    from models.master.plant import Department, PlantArea, Shift
    from models.master.production import ProductionLine


class WorkerRole(SoftDeleteMixin, TimestampCreatedMixin,
                 TimestampUpdatedMixin, Base):
    """Table ``worker_role``, master group: the job function and the authority flags
    that decide who may authorise a line stop or a maintenance intervention."""

    __tablename__ = "worker_role"
    __table_args__ = (
        UniqueConstraint("worker_role_code", name="uq_worker_role_code"),
        CheckConstraint(
            "worker_role_code GLOB 'ROL-[A-Z][A-Z]*' "
            "AND length(worker_role_code) BETWEEN 6 AND 9 "
            "AND substr(worker_role_code, 5) NOT GLOB '*[^A-Z]*'",
            name=conv("ck_worker_role_code_format")),
        CheckConstraint("seniority_rank BETWEEN 1 AND 10",
                        name=conv("ck_worker_role_seniority_range")),
        CheckConstraint(
            "is_managerial = 0 OR can_authorize_line_stop = 1",
            name=conv("ck_worker_role_managerial_can_stop_line")),
        CheckConstraint("length(trim(role_name)) > 0",
                        name=conv("ck_worker_role_name_not_blank")),
        CheckConstraint(
            "role_category IN ('operator', 'technician', 'engineer', "
            "'supervisor', 'manager', 'inspector', 'planner', 'storekeeper')",
            name=conv("ck_worker_role_role_category_allowed")),
        CheckConstraint("length(worker_role_code) <= 12",
                        name=conv("ck_worker_role_worker_role_code_length")),
        CheckConstraint("length(role_name) <= 80",
                        name=conv("ck_worker_role_role_name_length")),
        CheckConstraint("is_managerial IN (0, 1)",
                        name=conv("ck_worker_role_is_managerial_bool")),
        CheckConstraint("can_authorize_line_stop IN (0, 1)",
                        name=conv("ck_worker_role_can_authorize_line_stop_bool")),
        CheckConstraint("can_authorize_maintenance IN (0, 1)",
                        name=conv("ck_worker_role_can_authorize_maintenance_bool")),
        CheckConstraint("requires_certification IN (0, 1)",
                        name=conv("ck_worker_role_requires_certification_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_worker_role_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    worker_role_id: Mapped[MasterPk]
    worker_role_code: Mapped[str] = mapped_column(String(12))
    role_name: Mapped[str] = mapped_column(String(80))
    role_category: Mapped[RoleCategory] = mapped_column(
        Enum(RoleCategory, name="role_category", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    is_managerial: Mapped[bool] = mapped_column(server_default=text("0"))
    seniority_rank: Mapped[int] = mapped_column(Integer)
    # The authority flags recommendation_action is validated against: a
    # recommendation whose priority requires a line stop must be actioned by
    # somebody holding can_authorize_line_stop. That check spans tables and
    # belongs to the Dashboard (§41.3).
    can_authorize_line_stop: Mapped[bool] = mapped_column(server_default=text("0"))
    can_authorize_maintenance: Mapped[bool] = mapped_column(server_default=text("0"))
    requires_certification: Mapped[bool] = mapped_column(server_default=text("0"))
    description: Mapped[Optional[str]] = mapped_column(Text)

    workers: Mapped[list["Worker"]] = relationship(
        back_populates="worker_role", lazy="select"
    )

    @validates("worker_role_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class Worker(SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin, Base):
    """Table ``worker``, master group: the people the platform notifies and the
    accountability anchor on every inspection, scrap record and human decision."""

    __tablename__ = "worker"
    __table_args__ = (
        UniqueConstraint("worker_code", name="uq_worker_code"),
        UniqueConstraint("email", name="uq_worker_email"),
        CheckConstraint("worker_code GLOB 'EMP-[0-9][0-9][0-9][0-9]'",
                        name=conv("ck_worker_code_format")),
        CheckConstraint(
            "email IS NULL OR (email GLOB '?*@?*.?*' "
            "AND email NOT GLOB '*@*@*')",
            name=conv("ck_worker_email_format")),
        CheckConstraint(
            "phone_number IS NULL OR (phone_number GLOB "
            "'+[1-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]*' "
            "AND length(phone_number) BETWEEN 9 AND 16 "
            "AND substr(phone_number, 2) NOT GLOB '*[^0-9]*')",
            name=conv("ck_worker_phone_e164_format")),
        CheckConstraint("length(trim(first_name)) > 0",
                        name=conv("ck_worker_first_name_not_blank")),
        CheckConstraint("length(trim(last_name)) > 0",
                        name=conv("ck_worker_last_name_not_blank")),
        CheckConstraint(
            "employment_type IN ('permanent', 'contract', 'apprentice')",
            name=conv("ck_worker_employment_type_allowed")),
        CheckConstraint(
            "skill_level IN ('trainee', 'junior', 'intermediate', 'senior', "
            "'expert')",
            name=conv("ck_worker_skill_level_allowed")),
        CheckConstraint("length(worker_code) <= 12",
                        name=conv("ck_worker_worker_code_length")),
        CheckConstraint("length(first_name) <= 60",
                        name=conv("ck_worker_first_name_length")),
        CheckConstraint("length(last_name) <= 60",
                        name=conv("ck_worker_last_name_length")),
        CheckConstraint("email IS NULL OR length(email) <= 150",
                        name=conv("ck_worker_email_length")),
        CheckConstraint("phone_number IS NULL OR length(phone_number) <= 20",
                        name=conv("ck_worker_phone_number_length")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_worker_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    worker_id: Mapped[MasterPk]
    worker_code: Mapped[str] = mapped_column(String(12))
    first_name: Mapped[str] = mapped_column(String(60))
    last_name: Mapped[str] = mapped_column(String(60))
    worker_role_id: Mapped[int] = mapped_column(
        ForeignKey("worker_role.worker_role_id", name="fk_worker_role",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    department_id: Mapped[int] = mapped_column(
        ForeignKey("department.department_id", name="fk_worker_department",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    production_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_worker_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_worker_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    email: Mapped[Optional[str]] = mapped_column(String(150))
    phone_number: Mapped[Optional[str]] = mapped_column(String(20))
    hire_date: Mapped[date] = mapped_column(Date)
    employment_type: Mapped[EmploymentType] = mapped_column(
        Enum(EmploymentType, name="employment_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    skill_level: Mapped[SkillLevel] = mapped_column(
        Enum(SkillLevel, name="skill_level", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )

    worker_role: Mapped["WorkerRole"] = relationship(
        back_populates="workers", lazy="select"
    )
    department: Mapped["Department"] = relationship(
        back_populates="workers", lazy="select"
    )
    production_line: Mapped[Optional["ProductionLine"]] = relationship(
        back_populates="workers", lazy="select"
    )
    shift: Mapped["Shift"] = relationship(
        back_populates="workers", lazy="select"
    )
    maintenance_engineer: Mapped[Optional["MaintenanceEngineer"]] = relationship(
        back_populates="worker", lazy="select", uselist=False
    )
    notification_recipient: Mapped[Optional["NotificationRecipient"]] = relationship(
        back_populates="worker", lazy="select", uselist=False
    )

    # Seven operational reverse collections are deliberately unmapped, led by
    # audit_log at ~730,000 rows a year. Rule L1 (§19.1).

    @validates("worker_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()

    @validates("email")
    def _normalise_email(self, key: str, value: Optional[str]) -> Optional[str]:
        return value if value is None else value.strip().lower()

    @validates("first_name", "last_name")
    def _strip_only(self, key: str, value: str) -> str:
        # Strip, never case-fold: a person's name is not the ORM's to reformat
        # (§41.3).
        return value.strip()


class MaintenanceTeam(SoftDeleteMixin, TimestampCreatedMixin,
                      TimestampUpdatedMixin, Base):
    """Table ``maintenance_team``, master group: the dispatch unit, carrying the
    response-time commitment that machine_maintenance_activity holds to account."""

    __tablename__ = "maintenance_team"
    __table_args__ = (
        UniqueConstraint("maintenance_team_code", name="uq_maintenance_team_code"),
        CheckConstraint(
            "maintenance_team_code GLOB 'MTM-[A-Z][A-Z][A-Z]*' "
            "AND length(maintenance_team_code) BETWEEN 7 AND 9 "
            "AND substr(maintenance_team_code, 5) NOT GLOB '*[^A-Z]*'",
            name=conv("ck_maintenance_team_code_format")),
        CheckConstraint("max_concurrent_jobs BETWEEN 1 AND 10",
                        name=conv("ck_maintenance_team_max_jobs_range")),
        CheckConstraint("target_response_time_minutes BETWEEN 5 AND 480",
                        name=conv("ck_maintenance_team_response_target_range")),
        CheckConstraint(
            "contact_extension IS NULL OR (length(contact_extension) > 0 "
            "AND contact_extension NOT GLOB '*[^0-9]*')",
            name=conv("ck_maintenance_team_extension_digits")),
        CheckConstraint("length(trim(team_name)) > 0",
                        name=conv("ck_maintenance_team_name_not_blank")),
        CheckConstraint(
            "specialization IN ('mechanical', 'electrical', 'automation', "
            "'general')",
            name=conv("ck_maintenance_team_specialization_allowed")),
        CheckConstraint(
            "length(maintenance_team_code) <= 12",
            name=conv("ck_maintenance_team_maintenance_team_code_length")),
        CheckConstraint("length(team_name) <= 100",
                        name=conv("ck_maintenance_team_team_name_length")),
        CheckConstraint(
            "contact_extension IS NULL OR length(contact_extension) <= 10",
            name=conv("ck_maintenance_team_contact_extension_length")),
        CheckConstraint(
            "is_emergency_response IN (0, 1)",
            name=conv("ck_maintenance_team_is_emergency_response_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_maintenance_team_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    maintenance_team_id: Mapped[MasterPk]
    maintenance_team_code: Mapped[str] = mapped_column(String(12))
    team_name: Mapped[str] = mapped_column(String(100))
    department_id: Mapped[int] = mapped_column(
        ForeignKey("department.department_id",
                   name="fk_maintenance_team_department",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_maintenance_team_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Shared vocabulary: matching a failed machine to a qualified team is a
    # direct value comparison against failure_category.required_specialization
    # (§40.2).
    specialization: Mapped[MaintenanceSpecialization] = mapped_column(
        Enum(MaintenanceSpecialization, name="maintenance_specialization",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Where stationed, not organisational ownership.
    base_plant_area_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("plant_area.plant_area_id",
                   name="fk_maintenance_team_base_area",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    contact_extension: Mapped[Optional[str]] = mapped_column(String(10))
    max_concurrent_jobs: Mapped[int] = mapped_column(Integer)
    is_emergency_response: Mapped[bool] = mapped_column(server_default=text("0"))
    target_response_time_minutes: Mapped[int] = mapped_column(Integer)

    department: Mapped["Department"] = relationship(
        back_populates="maintenance_teams", lazy="select"
    )
    shift: Mapped["Shift"] = relationship(
        back_populates="maintenance_teams", lazy="select"
    )
    base_plant_area: Mapped[Optional["PlantArea"]] = relationship(
        back_populates="based_maintenance_teams", lazy="select"
    )
    engineers: Mapped[list["MaintenanceEngineer"]] = relationship(
        back_populates="maintenance_team", lazy="select"
    )
    maintenance_schedules: Mapped[list["MachineMaintenanceSchedule"]] = relationship(
        back_populates="assigned_maintenance_team", lazy="select"
    )

    @validates("maintenance_team_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class MaintenanceEngineer(SoftDeleteMixin, TimestampCreatedMixin,
                          TimestampUpdatedMixin, Base):
    """Table ``maintenance_engineer``, master group: a worker's maintenance
    qualification, as a one-to-one extension rather than a subtype."""

    __tablename__ = "maintenance_engineer"
    __table_args__ = (
        UniqueConstraint("maintenance_engineer_code",
                         name="uq_maintenance_engineer_code"),
        UniqueConstraint("worker_id", name="uq_maintenance_engineer_worker"),
        CheckConstraint("maintenance_engineer_code GLOB 'ENG-[0-9][0-9]'",
                        name=conv("ck_maintenance_engineer_code_format")),
        CheckConstraint("years_experience BETWEEN 0 AND 50",
                        name=conv("ck_maintenance_engineer_experience_range")),
        CheckConstraint(
            "secondary_specialization IS NULL "
            "OR secondary_specialization <> primary_specialization",
            name=conv("ck_maintenance_engineer_specializations_differ")),
        CheckConstraint(
            "primary_specialization IN ('mechanical', 'electrical', "
            "'automation', 'general')",
            name=conv(
                "ck_maintenance_engineer_primary_specialization_allowed")),
        CheckConstraint(
            "secondary_specialization IS NULL "
            "OR secondary_specialization IN ('mechanical', 'electrical', "
            "'automation', 'general')",
            name=conv(
                "ck_maintenance_engineer_secondary_specialization_allowed")),
        CheckConstraint(
            "length(maintenance_engineer_code) <= 10",
            name=conv(
                "ck_maintenance_engineer_maintenance_engineer_code_length")),
        CheckConstraint("is_team_lead IN (0, 1)",
                        name=conv("ck_maintenance_engineer_is_team_lead_bool")),
        CheckConstraint("is_on_call IN (0, 1)",
                        name=conv("ck_maintenance_engineer_is_on_call_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_maintenance_engineer_is_active_bool")),
        # Exactly one lead per team: none leaves the team without an accountable
        # contact, two produce contradictory assignment (§37.9).
        Index(
            "uq_maintenance_engineer_team_lead",
            "maintenance_team_id",
            unique=True,
            sqlite_where=text("is_team_lead = 1 AND is_active = 1"),
        ),
        {"sqlite_autoincrement": True},
    )

    maintenance_engineer_id: Mapped[MasterPk]
    maintenance_engineer_code: Mapped[str] = mapped_column(String(10))
    worker_id: Mapped[int] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_maintenance_engineer_worker",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    maintenance_team_id: Mapped[int] = mapped_column(
        ForeignKey("maintenance_team.maintenance_team_id",
                   name="fk_maintenance_engineer_team",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    primary_specialization: Mapped[MaintenanceSpecialization] = mapped_column(
        Enum(MaintenanceSpecialization, name="maintenance_specialization",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    is_team_lead: Mapped[bool] = mapped_column(server_default=text("0"))
    years_experience: Mapped[int] = mapped_column(Integer)
    certification_expiry_date: Mapped[Optional[date]] = mapped_column(Date)
    is_on_call: Mapped[bool] = mapped_column(server_default=text("0"))
    # The same enum class on a second column, which in SQLite means two
    # independent check constraints carrying the same value list. Both are
    # transcribed from the single catalogue entry rather than from each other,
    # which is what keeps them identical (§40.2).
    secondary_specialization: Mapped[Optional[MaintenanceSpecialization]] = (
        mapped_column(
            Enum(MaintenanceSpecialization, name="maintenance_specialization",
                 native_enum=False, create_constraint=False,
                 values_callable=lambda enum_cls: [m.value for m in enum_cls])
        )
    )

    worker: Mapped["Worker"] = relationship(
        back_populates="maintenance_engineer", lazy="select"
    )
    maintenance_team: Mapped["MaintenanceTeam"] = relationship(
        back_populates="engineers", lazy="select"
    )

    @validates("maintenance_engineer_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class NotificationRecipient(SoftDeleteMixin, TimestampCreatedMixin,
                            TimestampUpdatedMixin, Base):
    """Table ``notification_recipient``, master group: who is told what, and the
    per-person severity floor that is the platform's first noise filter.

    No business key column: identity is the worker. Stores no contact details --
    endpoints resolve through ``worker`` (§O20).
    """

    __tablename__ = "notification_recipient"
    __table_args__ = (
        UniqueConstraint("worker_id", name="uq_notification_recipient_worker"),
        CheckConstraint(
            "email_enabled = 1 OR whatsapp_enabled = 1",
            name=conv("ck_notification_recipient_channel_enabled")),
        CheckConstraint(
            "escalation_order > 0",
            name=conv("ck_notification_recipient_escalation_order_positive")),
        CheckConstraint(
            "max_notifications_per_hour IS NULL "
            "OR max_notifications_per_hour BETWEEN 1 AND 60",
            name=conv("ck_notification_recipient_rate_limit_range")),
        CheckConstraint(
            "email_enabled IN (0, 1)",
            name=conv("ck_notification_recipient_email_enabled_bool")),
        CheckConstraint(
            "whatsapp_enabled IN (0, 1)",
            name=conv("ck_notification_recipient_whatsapp_enabled_bool")),
        CheckConstraint(
            "notify_outside_shift_hours IN (0, 1)",
            name=conv(
                "ck_notification_recipient_notify_outside_shift_hours_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_notification_recipient_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    notification_recipient_id: Mapped[MasterPk]
    worker_id: Mapped[int] = mapped_column(
        ForeignKey("worker.worker_id", name="fk_notification_recipient_worker",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Severity is a foreign key rather than an enum because each level carries
    # behaviour -- response time, line-stop requirement, escalation flags,
    # acknowledgement deadline, display colour -- that a vocabulary cannot hold
    # (§40.6).
    min_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_notification_recipient_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # email_enabled = 1 requires a non-NULL worker.email. That spans two tables,
    # so it belongs to the Notification Service (§41.3); a recipient configured
    # for email with no address is a silent delivery failure.
    email_enabled: Mapped[bool] = mapped_column(server_default=text("1"))
    whatsapp_enabled: Mapped[bool] = mapped_column(server_default=text("0"))
    # NULL means plant-wide rather than missing -- one of the nullability
    # patterns that breaks a filter written by someone who assumed otherwise
    # (§38.5).
    scope_production_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_notification_recipient_scope_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    notify_outside_shift_hours: Mapped[bool] = mapped_column(
        server_default=text("0")
    )
    escalation_order: Mapped[int] = mapped_column(Integer)
    max_notifications_per_hour: Mapped[Optional[int]] = mapped_column(Integer)

    worker: Mapped["Worker"] = relationship(
        back_populates="notification_recipient", lazy="select"
    )
    min_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        back_populates="notification_recipients", lazy="select"
    )
    scope_production_line: Mapped[Optional["ProductionLine"]] = relationship(
        back_populates="notification_recipients", lazy="select"
    )
