"""Master models M27-M29: what counts as abnormal, and the tunable policy values.

Nothing here is copied into operational rows. An event references the rule that
fired and captures the value it breached; a supervisor context references the
escalation rule and copies nothing. That works precisely because superseded
versions are recorded as new rows and retired, never edited in place (§O13, §O17).
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
    BusinessRuleCategory,
    BusinessRuleValueType,
    ThresholdSensitivity,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk, Measurement, RuleNumeric

if TYPE_CHECKING:
    from models.master.equipment import Machine, MachineType
    from models.master.failure import FailureSeverityLevel
    from models.master.parameters import MachineParameter
    from models.master.production import ProductionLine


class AlertThresholdProfile(SoftDeleteMixin, TimestampCreatedMixin,
                            TimestampUpdatedMixin, Base):
    """Table ``alert_threshold_profile``, master group: a named, versioned set of
    threshold rules for a machine type."""

    __tablename__ = "alert_threshold_profile"
    __table_args__ = (
        UniqueConstraint("alert_threshold_profile_code",
                         name="uq_alert_threshold_profile_code"),
        CheckConstraint(
            "alert_threshold_profile_code GLOB "
            "'ATP-[A-Z0-9-][A-Z0-9-][A-Z0-9-]*' "
            "AND length(alert_threshold_profile_code) BETWEEN 7 AND 19 "
            "AND substr(alert_threshold_profile_code, 5) "
            "NOT GLOB '*[^A-Z0-9-]*'",
            name=conv("ck_atp_code_format")),
        CheckConstraint("version > 0", name=conv("ck_atp_version_positive")),
        CheckConstraint(
            "review_due_date IS NULL OR review_due_date > effective_from_date",
            name=conv("ck_atp_review_after_effective")),
        CheckConstraint("length(trim(profile_name)) > 0",
                        name=conv("ck_atp_profile_name_not_blank")),
        CheckConstraint("sensitivity IN ('tight', 'standard', 'relaxed')",
                        name=conv("ck_atp_sensitivity_allowed")),
        CheckConstraint("length(alert_threshold_profile_code) <= 20",
                        name=conv("ck_atp_alert_threshold_profile_code_length")),
        CheckConstraint("length(profile_name) <= 120",
                        name=conv("ck_atp_profile_name_length")),
        CheckConstraint("is_default IN (0, 1)",
                        name=conv("ck_atp_is_default_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_atp_is_active_bool")),
        # Exactly one default profile per machine type (§37.9).
        Index(
            "uq_alert_threshold_profile_default",
            "machine_type_id",
            unique=True,
            sqlite_where=text("is_default = 1 AND is_active = 1"),
        ),
        {"sqlite_autoincrement": True},
    )

    alert_threshold_profile_id: Mapped[MasterPk]
    alert_threshold_profile_code: Mapped[str] = mapped_column(String(20))
    profile_name: Mapped[str] = mapped_column(String(120))
    machine_type_id: Mapped[int] = mapped_column(
        ForeignKey("machine_type.machine_type_id", name="fk_atp_machine_type",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    # Retuning creates a new row with an incremented version and retires the old
    # one; a superseded version is never edited. That is what lets an event
    # reference a rule for lineage without copying its value (§M27).
    version: Mapped[int] = mapped_column(Integer, server_default=text("1"))
    is_default: Mapped[bool] = mapped_column(server_default=text("0"))
    sensitivity: Mapped[ThresholdSensitivity] = mapped_column(
        Enum(ThresholdSensitivity, name="threshold_sensitivity",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    effective_from_date: Mapped[date] = mapped_column(Date)
    review_due_date: Mapped[Optional[date]] = mapped_column(Date)
    notes: Mapped[Optional[str]] = mapped_column(Text)

    machine_type: Mapped["MachineType"] = relationship(
        back_populates="alert_threshold_profiles", lazy="select"
    )
    rules: Mapped[list["AlertThresholdRule"]] = relationship(
        back_populates="alert_threshold_profile", lazy="select"
    )
    machines: Mapped[list["Machine"]] = relationship(
        back_populates="alert_threshold_profile", lazy="select"
    )

    @validates("alert_threshold_profile_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()


class AlertThresholdRule(SoftDeleteMixin, TimestampCreatedMixin,
                         TimestampUpdatedMixin, Base):
    """Table ``alert_threshold_rule``, master group: the per-parameter bounds whose
    breach an operational_event records, with the severity each side implies."""

    __tablename__ = "alert_threshold_rule"
    __table_args__ = (
        UniqueConstraint("alert_threshold_profile_id", "machine_parameter_id",
                         name="uq_alert_threshold_rule_pair"),
        # A rule with no bounds cannot fire.
        CheckConstraint(
            "warning_low IS NOT NULL OR warning_high IS NOT NULL "
            "OR critical_low IS NOT NULL OR critical_high IS NOT NULL "
            "OR rate_of_change_limit_per_minute IS NOT NULL",
            name=conv("ck_atr_at_least_one_limit")),
        CheckConstraint(
            "critical_low IS NULL OR warning_low IS NULL "
            "OR critical_low <= warning_low",
            name=conv("ck_atr_low_side_ordered")),
        CheckConstraint(
            "critical_high IS NULL OR warning_high IS NULL "
            "OR warning_high <= critical_high",
            name=conv("ck_atr_high_side_ordered")),
        CheckConstraint("sustained_duration_seconds BETWEEN 0 AND 3600",
                        name=conv("ck_atr_sustained_duration_range")),
        CheckConstraint(
            "rate_of_change_limit_per_minute IS NULL "
            "OR rate_of_change_limit_per_minute > 0",
            name=conv("ck_atr_rate_limit_positive")),
        CheckConstraint(
            "critical_severity_level_id <> warning_severity_level_id",
            name=conv("ck_atr_severities_differ")),
        CheckConstraint("is_enabled IN (0, 1)",
                        name=conv("ck_atr_is_enabled_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_atr_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    alert_threshold_rule_id: Mapped[MasterPk]
    alert_threshold_profile_id: Mapped[int] = mapped_column(
        ForeignKey("alert_threshold_profile.alert_threshold_profile_id",
                   name="fk_atr_profile",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_parameter_id: Mapped[int] = mapped_column(
        ForeignKey("machine_parameter.machine_parameter_id",
                   name="fk_atr_parameter",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    warning_low: Mapped[Optional[Measurement]]
    warning_high: Mapped[Optional[Measurement]]
    critical_low: Mapped[Optional[Measurement]]
    critical_high: Mapped[Optional[Measurement]]
    # 0 means fire immediately.
    sustained_duration_seconds: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    warning_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_atr_warning_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    critical_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_atr_critical_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    rate_of_change_limit_per_minute: Mapped[Optional[Measurement]]
    # A different fact from is_active, and neither implies the other:
    # is_enabled = 0 is a temporary suppression by the threshold owner,
    # is_active = 0 is retirement of the rule row. The ORM derives nothing.
    is_enabled: Mapped[bool] = mapped_column(server_default=text("1"))

    alert_threshold_profile: Mapped["AlertThresholdProfile"] = relationship(
        back_populates="rules", lazy="select"
    )
    machine_parameter: Mapped["MachineParameter"] = relationship(
        back_populates="threshold_rules", lazy="select"
    )
    # Two keys to one target, so both state foreign_keys explicitly and both are
    # named for the role rather than the target (§37.6, §45.5).
    warning_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        back_populates="warning_threshold_rules", lazy="select",
        foreign_keys=[warning_severity_level_id]
    )
    critical_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        back_populates="critical_threshold_rules", lazy="select",
        foreign_keys=[critical_severity_level_id]
    )


class BusinessRule(SoftDeleteMixin, TimestampCreatedMixin,
                   TimestampUpdatedMixin, Base):
    """Table ``business_rule``, master group: the tunable policy values agents read
    rather than hard-code, including the escalation thresholds."""

    __tablename__ = "business_rule"
    __table_args__ = (
        UniqueConstraint("business_rule_code", name="uq_business_rule_code"),
        CheckConstraint(
            "business_rule_code GLOB "
            "'BR-[A-Z][A-Z][A-Z]*-[A-Z0-9-][A-Z0-9-]*' "
            "AND length(business_rule_code) BETWEEN 9 AND 32 "
            "AND business_rule_code NOT GLOB '*[^A-Z0-9-]*'",
            name=conv("ck_business_rule_code_format")),
        # value_type discriminates which of the three value columns is
        # populated, and exactly one must be.
        CheckConstraint(
            "(value_type = 'numeric' AND value_numeric IS NOT NULL "
            "AND value_text IS NULL AND value_boolean IS NULL) "
            "OR (value_type = 'text' AND value_text IS NOT NULL "
            "AND value_numeric IS NULL AND value_boolean IS NULL) "
            "OR (value_type = 'boolean' AND value_boolean IS NOT NULL "
            "AND value_numeric IS NULL AND value_text IS NULL)",
            name=conv("ck_business_rule_exactly_one_value")),
        CheckConstraint("length(trim(rule_name)) > 0",
                        name=conv("ck_business_rule_name_not_blank")),
        CheckConstraint("length(trim(description)) > 0",
                        name=conv("ck_business_rule_description_not_blank")),
        CheckConstraint(
            "rule_category IN ('escalation', 'prioritization', 'costing', "
            "'notification', 'maintenance_policy', 'inventory_policy')",
            name=conv("ck_business_rule_rule_category_allowed")),
        CheckConstraint("value_type IN ('numeric', 'text', 'boolean')",
                        name=conv("ck_business_rule_value_type_allowed")),
        CheckConstraint("length(business_rule_code) <= 32",
                        name=conv("ck_business_rule_business_rule_code_length")),
        CheckConstraint("length(rule_name) <= 150",
                        name=conv("ck_business_rule_rule_name_length")),
        CheckConstraint("value_text IS NULL OR length(value_text) <= 100",
                        name=conv("ck_business_rule_value_text_length")),
        CheckConstraint("unit IS NULL OR length(unit) <= 24",
                        name=conv("ck_business_rule_unit_length")),
        CheckConstraint("value_boolean IS NULL OR value_boolean IN (0, 1)",
                        name=conv("ck_business_rule_value_boolean_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_business_rule_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    business_rule_id: Mapped[MasterPk]
    # How an agent looks a rule up, and what supervisor_context cites when it
    # records which threshold a decision turned on.
    business_rule_code: Mapped[str] = mapped_column(String(32))
    rule_name: Mapped[str] = mapped_column(String(150))
    rule_category: Mapped[BusinessRuleCategory] = mapped_column(
        Enum(BusinessRuleCategory, name="business_rule_category",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    value_type: Mapped[BusinessRuleValueType] = mapped_column(
        Enum(BusinessRuleValueType, name="business_rule_value_type",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    value_numeric: Mapped[Optional[RuleNumeric]]
    value_text: Mapped[Optional[str]] = mapped_column(String(100))
    # The only nullable boolean in the database: nullable because exactly one of
    # the three value columns is populated per rule (§29).
    value_boolean: Mapped[Optional[bool]]
    unit: Mapped[Optional[str]] = mapped_column(String(24))
    # NULL means plant-wide, not missing. A filter of production_line_id = :line
    # silently excludes every plant-wide rule, which is a correctness bug with no
    # error message (§38.5).
    production_line_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("production_line.production_line_id",
                   name="fk_business_rule_production_line",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    description: Mapped[str] = mapped_column(Text)
    effective_from_date: Mapped[date] = mapped_column(Date)

    production_line: Mapped[Optional["ProductionLine"]] = relationship(
        back_populates="business_rules", lazy="select"
    )

    @validates("business_rule_code")
    def _normalise_code(self, key: str, value: str) -> str:
        return value.strip().upper()
