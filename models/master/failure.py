"""Master models M23-M25: the severity scale, the failure taxonomy, and the
per-type failure modes.

``FailureSeverityLevel`` is a table rather than a vocabulary, and that is the
schema's most consequential non-enum decision: each level carries behaviour --
response time, line-stop requirement, escalation flags, acknowledgement deadline,
display colour -- that a value list cannot hold. It is why 11 foreign keys point
here and why ``severity_rank`` exists as the comparison axis (§40.6).
"""

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Enum,
    ForeignKey,
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
    FailureDomain,
    MaintenanceSpecialization,
    RelativeFrequency,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk

if TYPE_CHECKING:
    from models.master.equipment import MachineType
    from models.master.inventory import InventoryItem
    from models.master.parameters import MachineParameter
    from models.master.people import NotificationRecipient
    from models.master.thresholds import AlertThresholdRule


class FailureSeverityLevel(SoftDeleteMixin, TimestampCreatedMixin,
                           TimestampUpdatedMixin, Base):
    """Table ``failure_severity_level``, master group: the severity scale, carrying
    the behaviour each level implies rather than only its name."""

    __tablename__ = "failure_severity_level"
    __table_args__ = (
        UniqueConstraint("failure_severity_level_code",
                         name="uq_failure_severity_level_code"),
        UniqueConstraint("severity_rank", name="uq_failure_severity_level_rank"),
        CheckConstraint("failure_severity_level_code GLOB 'SEV-[0-9]'",
                        name=conv("ck_severity_code_format")),
        CheckConstraint("severity_rank BETWEEN 1 AND 9",
                        name=conv("ck_severity_rank_range")),
        CheckConstraint(
            "target_response_time_minutes IS NULL "
            "OR target_response_time_minutes > 0",
            name=conv("ck_severity_response_time_positive")),
        CheckConstraint(
            "requires_manager_acknowledgement = 0 "
            "OR max_acknowledgement_minutes IS NOT NULL",
            name=conv("ck_severity_ack_minutes_required")),
        CheckConstraint(
            "max_acknowledgement_minutes IS NULL "
            "OR max_acknowledgement_minutes > 0",
            name=conv("ck_severity_ack_minutes_positive")),
        CheckConstraint(
            "display_color_hex GLOB "
            "'#[0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F][0-9A-F]'",
            name=conv("ck_severity_color_hex_format")),
        CheckConstraint("length(trim(severity_name)) > 0",
                        name=conv("ck_severity_name_not_blank")),
        CheckConstraint("length(trim(description)) > 0",
                        name=conv("ck_severity_description_not_blank")),
        CheckConstraint("length(failure_severity_level_code) <= 8",
                        name=conv("ck_failure_severity_level_code_length")),
        CheckConstraint("length(severity_name) <= 40",
                        name=conv("ck_failure_severity_level_severity_name_length")),
        CheckConstraint(
            "length(display_color_hex) = 7",
            name=conv("ck_failure_severity_level_display_color_hex_length")),
        CheckConstraint("requires_line_stop IN (0, 1)",
                        name=conv("ck_severity_requires_line_stop_bool")),
        CheckConstraint(
            "requires_immediate_escalation IN (0, 1)",
            name=conv("ck_severity_requires_immediate_escalation_bool")),
        CheckConstraint(
            "requires_manager_acknowledgement IN (0, 1)",
            name=conv("ck_severity_requires_manager_acknowledgement_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_failure_severity_level_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    failure_severity_level_id: Mapped[MasterPk]
    failure_severity_level_code: Mapped[str] = mapped_column(String(8))
    severity_name: Mapped[str] = mapped_column(String(40))
    # Unique, and the axis every severity comparison in the platform uses.
    severity_rank: Mapped[int] = mapped_column(Integer)
    # NOT NULL: the scale has to be self-documenting, because every threshold
    # rule and recipient filter is calibrated against it.
    description: Mapped[str] = mapped_column(Text)
    target_response_time_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    requires_line_stop: Mapped[bool] = mapped_column(server_default=text("0"))
    requires_immediate_escalation: Mapped[bool] = mapped_column(
        server_default=text("0")
    )
    requires_manager_acknowledgement: Mapped[bool] = mapped_column(
        server_default=text("0")
    )
    # notification.acknowledgement_deadline_at is resolved from this at
    # composition, because a clock recomputed on every check can drift (§O20).
    max_acknowledgement_minutes: Mapped[Optional[int]] = mapped_column(Integer)
    display_color_hex: Mapped[str] = mapped_column(CHAR(7))

    failure_categories: Mapped[list["FailureCategory"]] = relationship(
        back_populates="default_severity_level", lazy="select"
    )
    typical_for_failure_modes: Mapped[list["MachineTypeFailureMode"]] = relationship(
        back_populates="typical_severity_level", lazy="select"
    )
    warning_threshold_rules: Mapped[list["AlertThresholdRule"]] = relationship(
        back_populates="warning_severity_level", lazy="select",
        foreign_keys="AlertThresholdRule.warning_severity_level_id"
    )
    critical_threshold_rules: Mapped[list["AlertThresholdRule"]] = relationship(
        back_populates="critical_severity_level", lazy="select",
        foreign_keys="AlertThresholdRule.critical_severity_level_id"
    )
    notification_recipients: Mapped[list["NotificationRecipient"]] = relationship(
        back_populates="min_severity_level", lazy="select"
    )

    # Six operational reverse collections are deliberately unmapped: five
    # severity rows against six growing operational tables (§19.1).
    #
    # Effectively immutable in practice: adding a level mid-project would require
    # re-evaluating every threshold rule, failure category and recipient filter
    # referencing the scale. Procedural, not an ORM hook (§M23).

    @validates("failure_severity_level_code", "display_color_hex")
    def _normalise_upper(self, key: str, value: str) -> str:
        # display_color_hex keeps its leading '#'; the check constraint requires
        # upper-case hex digits, so a lower-case input would be rejected without
        # this normalisation (§41.3).
        return value.strip().upper()


class FailureCategory(SoftDeleteMixin, TimestampCreatedMixin,
                      TimestampUpdatedMixin, Base):
    """Table ``failure_category``, master group: the controlled failure taxonomy the
    Decision Agent classifies a root cause within."""

    __tablename__ = "failure_category"
    __table_args__ = (
        UniqueConstraint("failure_category_code", name="uq_failure_category_code"),
        CheckConstraint(
            "failure_category_code GLOB 'FC-[A-Z][A-Z][A-Z]*' "
            "AND length(failure_category_code) BETWEEN 6 AND 9 "
            "AND substr(failure_category_code, 4) NOT GLOB '*[^A-Z]*'",
            name=conv("ck_failure_category_code_format")),
        CheckConstraint("length(trim(category_name)) > 0",
                        name=conv("ck_failure_category_name_not_blank")),
        CheckConstraint("length(trim(description)) > 0",
                        name=conv("ck_failure_category_description_not_blank")),
        CheckConstraint(
            "failure_domain IN ('mechanical', 'electrical', 'thermal', "
            "'tooling', 'hydraulic', 'pneumatic', 'instrumentation', "
            "'automation', 'process')",
            name=conv("ck_failure_category_failure_domain_allowed")),
        CheckConstraint(
            "required_specialization IN ('mechanical', 'electrical', "
            "'automation', 'general')",
            name=conv("ck_failure_category_required_specialization_allowed")),
        CheckConstraint("length(failure_category_code) <= 10",
                        name=conv("ck_failure_category_failure_category_code_length")),
        CheckConstraint("length(category_name) <= 100",
                        name=conv("ck_failure_category_category_name_length")),
        CheckConstraint("requires_spare_part IN (0, 1)",
                        name=conv("ck_failure_category_requires_spare_part_bool")),
        CheckConstraint(
            "has_safety_implication IN (0, 1)",
            name=conv("ck_failure_category_has_safety_implication_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_failure_category_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    failure_category_id: Mapped[MasterPk]
    failure_category_code: Mapped[str] = mapped_column(String(10))
    category_name: Mapped[str] = mapped_column(String(100))
    failure_domain: Mapped[FailureDomain] = mapped_column(
        Enum(FailureDomain, name="failure_domain", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    default_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_failure_category_default_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Compared directly against maintenance_team.specialization and
    # maintenance_engineer's two specialization columns (§40.2).
    required_specialization: Mapped[MaintenanceSpecialization] = mapped_column(
        Enum(MaintenanceSpecialization, name="maintenance_specialization",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    requires_spare_part: Mapped[bool] = mapped_column(server_default=text("0"))
    has_safety_implication: Mapped[bool] = mapped_column(server_default=text("0"))
    description: Mapped[str] = mapped_column(Text)

    default_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        back_populates="failure_categories", lazy="select"
    )
    failure_modes: Mapped[list["MachineTypeFailureMode"]] = relationship(
        back_populates="failure_category", lazy="select"
    )

    @validates("failure_category_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class MachineTypeFailureMode(SoftDeleteMixin, TimestampCreatedMixin,
                             TimestampUpdatedMixin, Base):
    """Table ``machine_type_failure_mode``, master group: how a machine type fails,
    what signals it, how long the repair takes, and which spare it needs."""

    __tablename__ = "machine_type_failure_mode"
    __table_args__ = (
        UniqueConstraint("machine_type_id", "failure_category_id",
                         name="uq_mtfm_pair"),
        CheckConstraint("estimated_repair_duration_minutes > 0",
                        name=conv("ck_mtfm_repair_duration_positive")),
        CheckConstraint(
            "typical_warning_period_hours IS NULL "
            "OR typical_warning_period_hours > 0",
            name=conv("ck_mtfm_warning_period_positive")),
        CheckConstraint(
            "is_model_predictable = 1 OR typical_warning_period_hours IS NULL",
            name=conv("ck_mtfm_unpredictable_has_no_warning")),
        CheckConstraint(
            "is_model_predictable = 0 "
            "OR primary_machine_parameter_id IS NOT NULL",
            name=conv("ck_mtfm_predictable_has_indicator")),
        CheckConstraint("length(trim(leading_indicator_description)) > 0",
                        name=conv("ck_mtfm_leading_indicator_not_blank")),
        CheckConstraint(
            "relative_frequency IN ('common', 'occasional', 'rare')",
            name=conv("ck_mtfm_relative_frequency_allowed")),
        CheckConstraint("is_model_predictable IN (0, 1)",
                        name=conv("ck_mtfm_is_model_predictable_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_mtfm_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    machine_type_failure_mode_id: Mapped[MasterPk]
    machine_type_id: Mapped[int] = mapped_column(
        ForeignKey("machine_type.machine_type_id", name="fk_mtfm_machine_type",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    failure_category_id: Mapped[int] = mapped_column(
        ForeignKey("failure_category.failure_category_id",
                   name="fk_mtfm_failure_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    typical_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_mtfm_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # The telemetry signal. A predictable mode with no signal is not predictable,
    # which ck_mtfm_predictable_has_indicator enforces.
    primary_machine_parameter_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine_parameter.machine_parameter_id",
                   name="fk_mtfm_parameter",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    required_inventory_item_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inventory_item.inventory_item_id",
                   name="fk_mtfm_inventory_item",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    leading_indicator_description: Mapped[str] = mapped_column(Text)
    estimated_repair_duration_minutes: Mapped[int] = mapped_column(Integer)
    # NULL for sudden failures: not applicable to this row rather than unknown
    # (§38.5).
    typical_warning_period_hours: Mapped[Optional[int]] = mapped_column(Integer)
    is_model_predictable: Mapped[bool] = mapped_column(server_default=text("0"))
    relative_frequency: Mapped[RelativeFrequency] = mapped_column(
        Enum(RelativeFrequency, name="relative_frequency", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )

    machine_type: Mapped["MachineType"] = relationship(
        back_populates="failure_modes", lazy="select"
    )
    failure_category: Mapped["FailureCategory"] = relationship(
        back_populates="failure_modes", lazy="select"
    )
    typical_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        back_populates="typical_for_failure_modes", lazy="select"
    )
    primary_machine_parameter: Mapped[Optional["MachineParameter"]] = relationship(
        back_populates="primary_for_failure_modes", lazy="select"
    )
    required_inventory_item: Mapped[Optional["InventoryItem"]] = relationship(
        back_populates="required_by_failure_modes", lazy="select"
    )
