"""Operational models O15-O16: the feature vector, and the model inference.

``PredictionResult`` is the sole origin of the ML confidence figure.
``failure_probability`` is created here and nowhere else, because an LLM restating a
probability would quietly lose calibration (PROJECT_OVERVIEW.md §16.5).
"""

from typing import TYPE_CHECKING, Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    inspect,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates
from sqlalchemy.sql.elements import conv

from models.base import Base
from models.enums.operational import SnapshotInsufficiencyReason
from models.mixins import (
    ComponentProvenanceMixin,
    TimestampCreatedMixin,
    require_timezone_aware,
)
from models.types import (
    JsonDoc,
    OperationalPk,
    Percent,
    Probability,
    TimestampTz,
)

if TYPE_CHECKING:
    from models.master.equipment import Machine
    from models.master.failure import (
        FailureCategory,
        FailureSeverityLevel,
        MachineTypeFailureMode,
    )
    from models.master.plant import Shift
    from models.operational.events import OperationalAlert


class PredictionFeatureSnapshot(TimestampCreatedMixin,
                                ComponentProvenanceMixin, Base):
    """Table ``prediction_feature_snapshot``, operational group: the exact feature
    vector fed to the model, with an honest statement of its own adequacy.
    Append-only."""

    __tablename__ = "prediction_feature_snapshot"
    __table_args__ = (
        UniqueConstraint("prediction_feature_snapshot_code", name="uq_pfs_code"),
        CheckConstraint(
            "prediction_feature_snapshot_code GLOB "
            "'FSN-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9][0-9]'",
            name=conv("ck_pfs_code_format")),
        CheckConstraint("window_to > window_from",
                        name=conv("ck_pfs_window_ordered")),
        CheckConstraint("window_to = generated_at",
                        name=conv("ck_pfs_window_to_equals_generated")),
        CheckConstraint("lookback_window_seconds > 0",
                        name=conv("ck_pfs_lookback_positive")),
        CheckConstraint(
            "source_reading_count >= 0 AND excluded_reading_count >= 0",
            name=conv("ck_pfs_counts_non_negative")),
        CheckConstraint("data_completeness_pct BETWEEN 0 AND 100",
                        name=conv("ck_pfs_completeness_range")),
        # An insufficient snapshot must state why; a sufficient one must not
        # carry a reason. Read together, they make the field present exactly when
        # it is meaningful.
        CheckConstraint(
            "is_sufficient_for_inference = 1 "
            "OR insufficiency_reason IS NOT NULL",
            name=conv("ck_pfs_insufficiency_reason_required")),
        CheckConstraint(
            "is_sufficient_for_inference = 0 OR insufficiency_reason IS NULL",
            name=conv("ck_pfs_sufficient_has_no_reason")),
        # Object, not a scalar or array. Cheap structural validation that stops a
        # malformed payload becoming a parse error inside the Prediction Agent.
        CheckConstraint(
            "json_valid(feature_values) "
            "AND json_type(feature_values) = 'object'",
            name=conv("ck_pfs_feature_values_is_object")),
        CheckConstraint("length(trim(feature_set_version)) > 0",
                        name=conv("ck_pfs_feature_set_version_not_blank")),
        CheckConstraint(
            "insufficiency_reason IS NULL OR insufficiency_reason IN "
            "('completeness_below_threshold', 'sensor_fault', "
            "'machine_not_running', 'window_spans_maintenance', "
            "'insufficient_history')",
            name=conv("ck_pfs_insufficiency_reason_allowed")),
        CheckConstraint("is_sufficient_for_inference IN (0, 1)",
                        name=conv("ck_pfs_is_sufficient_for_inference_bool")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_pfs_created_by_component_allowed")),
        CheckConstraint(
            "length(prediction_feature_snapshot_code) <= 22",
            name=conv("ck_pfs_prediction_feature_snapshot_code_length")),
        CheckConstraint("length(feature_set_version) <= 20",
                        name=conv("ck_pfs_feature_set_version_length")),
        {"sqlite_autoincrement": True},
    )

    prediction_feature_snapshot_id: Mapped[OperationalPk]
    prediction_feature_snapshot_code: Mapped[str] = mapped_column(String(22))
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_pfs_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    generated_at: Mapped[TimestampTz]
    window_from: Mapped[TimestampTz]
    window_to: Mapped[TimestampTz]
    lookback_window_seconds: Mapped[int] = mapped_column(Integer)
    # Pins the shape of feature_values, which is what makes the document
    # versioned rather than migrated (§39.2).
    feature_set_version: Mapped[str] = mapped_column(String(20))
    # Not schema-validated by the ORM: the shape is the Prediction Agent's
    # contract with itself, and validating it here would need an ORM change on
    # every model revision (§39.6).
    feature_values: Mapped[JsonDoc]
    source_reading_count: Mapped[int] = mapped_column(Integer)
    excluded_reading_count: Mapped[int] = mapped_column(
        Integer, server_default=text("0")
    )
    data_completeness_pct: Mapped[Percent]
    # No default: the writer must decide. Scoring on inadequate data returns a
    # confident number with no basis, so a prediction may only reference a
    # snapshot where this is true -- a cross-table rule the Prediction Agent owns.
    is_sufficient_for_inference: Mapped[bool]
    insufficiency_reason: Mapped[Optional[SnapshotInsufficiencyReason]] = (
        mapped_column(
            Enum(SnapshotInsufficiencyReason,
                 name="snapshot_insufficiency_reason", native_enum=False,
                 create_constraint=False,
                 values_callable=lambda enum_cls: [m.value for m in enum_cls])
        )
    )
    triggering_alert_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("operational_alert.operational_alert_id",
                   name="fk_pfs_triggering_alert",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_pfs_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    # The three many-to-one relationships are unidirectional (§O15).
    machine: Mapped["Machine"] = relationship(lazy="select")
    triggering_alert: Mapped[Optional["OperationalAlert"]] = relationship(
        lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")
    prediction_results: Mapped[list["PredictionResult"]] = relationship(
        back_populates="prediction_feature_snapshot", lazy="select"
    )

    @validates("prediction_feature_snapshot_code", "machine_id",
               "generated_at", "window_from", "window_to",
               "lookback_window_seconds", "feature_set_version",
               "feature_values", "source_reading_count",
               "excluded_reading_count", "data_completeness_pct",
               "is_sufficient_for_inference", "insufficiency_reason",
               "triggering_alert_id", "shift_id", "machine",
               "triggering_alert", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and all three timestamps timezone-aware (§41.3, §41.4).

        The immutability is what makes a prediction auditable: if the vector could
        change after the fact, no scoring of the model against ground truth would
        mean anything.
        """
        if inspect(self).persistent:
            raise ValueError(
                "prediction_feature_snapshot is append-only; %s cannot be "
                "reassigned once the row is persistent" % key
            )
        if key in ("generated_at", "window_from", "window_to"):
            return require_timezone_aware(key, value)
        if key == "prediction_feature_snapshot_code":
            return value.strip().upper()
        return value


class PredictionResult(TimestampCreatedMixin, ComponentProvenanceMixin, Base):
    """Table ``prediction_result``, operational group: one model inference, and the
    sole origin of the ML confidence figure. Append-only."""

    __tablename__ = "prediction_result"
    __table_args__ = (
        UniqueConstraint("prediction_result_code", name="uq_pr_code"),
        # PDN- rather than PRD-, to avoid collision with master product codes.
        CheckConstraint(
            "prediction_result_code GLOB "
            "'PDN-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-"
            "[0-9][0-9][0-9][0-9]'",
            name=conv("ck_pr_code_format")),
        # NUMERIC(5,4) permits up to 9.9999, so this range check is load-bearing
        # rather than decorative: without it a probability of 3.5 would propagate
        # into a recommendation as ML confidence (§39.4).
        CheckConstraint("failure_probability BETWEEN 0 AND 1",
                        name=conv("ck_pr_probability_range")),
        CheckConstraint(
            "(confidence_band_low IS NULL AND confidence_band_high IS NULL) "
            "OR (confidence_band_low <= failure_probability "
            "AND failure_probability <= confidence_band_high)",
            name=conv("ck_pr_confidence_band_ordered")),
        CheckConstraint(
            "(confidence_band_low IS NULL "
            "OR confidence_band_low BETWEEN 0 AND 1) "
            "AND (confidence_band_high IS NULL "
            "OR confidence_band_high BETWEEN 0 AND 1)",
            name=conv("ck_pr_confidence_band_range")),
        CheckConstraint(
            "(confidence_band_low IS NULL) = (confidence_band_high IS NULL)",
            name=conv("ck_pr_confidence_band_paired")),
        CheckConstraint("prediction_horizon_hours > 0",
                        name=conv("ck_pr_horizon_positive")),
        CheckConstraint("inference_duration_ms >= 0",
                        name=conv("ck_pr_inference_duration_non_negative")),
        # Object or array, not a scalar (§39.3 of the schema document).
        CheckConstraint(
            "json_valid(top_contributing_features) "
            "AND json_type(top_contributing_features) IN ('object', 'array')",
            name=conv("ck_pr_top_features_is_object")),
        CheckConstraint("length(trim(model_version)) > 0",
                        name=conv("ck_pr_model_version_not_blank")),
        CheckConstraint(
            "created_by_component IN ('simulator', 'monitoring_agent', "
            "'prediction_agent', 'supervisor_agent', 'decision_agent', "
            "'notification_service', 'dashboard', 'platform')",
            name=conv("ck_pr_created_by_component_allowed")),
        CheckConstraint("length(prediction_result_code) <= 20",
                        name=conv("ck_pr_prediction_result_code_length")),
        CheckConstraint("length(model_name) <= 60",
                        name=conv("ck_pr_model_name_length")),
        CheckConstraint("length(model_version) <= 20",
                        name=conv("ck_pr_model_version_length")),
        {"sqlite_autoincrement": True},
    )

    prediction_result_id: Mapped[OperationalPk]
    prediction_result_code: Mapped[str] = mapped_column(String(20))
    prediction_feature_snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("prediction_feature_snapshot.prediction_feature_snapshot_id",
                   name="fk_pr_snapshot",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_id: Mapped[int] = mapped_column(
        ForeignKey("machine.machine_id", name="fk_pr_machine",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    predicted_at: Mapped[TimestampTz]
    model_name: Mapped[str] = mapped_column(String(60))
    # Quality attribution: a change in prediction quality must be traceable to a
    # model change.
    model_version: Mapped[str] = mapped_column(String(20))
    # Created here and nowhere else. ai_recommendation has no column that could
    # hold it, which converts the ML-confidence rule from a convention into a
    # structural impossibility (§O18).
    failure_probability: Mapped[Probability]
    risk_severity_level_id: Mapped[int] = mapped_column(
        ForeignKey("failure_severity_level.failure_severity_level_id",
                   name="fk_pr_risk_severity",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    predicted_failure_category_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("failure_category.failure_category_id",
                   name="fk_pr_failure_category",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_type_failure_mode_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("machine_type_failure_mode.machine_type_failure_mode_id",
                   name="fk_pr_failure_mode",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    prediction_horizon_hours: Mapped[int] = mapped_column(Integer)
    confidence_band_low: Mapped[Optional[Probability]]
    confidence_band_high: Mapped[Optional[Probability]]
    top_contributing_features: Mapped[JsonDoc]
    triggering_alert_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("operational_alert.operational_alert_id",
                   name="fk_pr_triggering_alert",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    inference_duration_ms: Mapped[int] = mapped_column(Integer)
    shift_id: Mapped[int] = mapped_column(
        ForeignKey("shift.shift_id", name="fk_pr_shift",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )

    prediction_feature_snapshot: Mapped["PredictionFeatureSnapshot"] = (
        relationship(back_populates="prediction_results", lazy="select")
    )
    # The other six are unidirectional (§O16).
    machine: Mapped["Machine"] = relationship(lazy="select")
    risk_severity_level: Mapped["FailureSeverityLevel"] = relationship(
        lazy="select"
    )
    predicted_failure_category: Mapped[Optional["FailureCategory"]] = relationship(
        lazy="select"
    )
    machine_type_failure_mode: Mapped[Optional["MachineTypeFailureMode"]] = (
        relationship(lazy="select")
    )
    triggering_alert: Mapped[Optional["OperationalAlert"]] = relationship(
        lazy="select"
    )
    shift: Mapped["Shift"] = relationship(lazy="select")

    @validates("prediction_result_code", "prediction_feature_snapshot_id",
               "machine_id", "predicted_at", "model_name", "model_version",
               "failure_probability", "risk_severity_level_id",
               "predicted_failure_category_id", "machine_type_failure_mode_id",
               "prediction_horizon_hours", "confidence_band_low",
               "confidence_band_high", "top_contributing_features",
               "triggering_alert_id", "inference_duration_ms", "shift_id",
               "prediction_feature_snapshot", "machine", "risk_severity_level",
               "predicted_failure_category", "machine_type_failure_mode",
               "triggering_alert", "shift")
    def _validate_assignment(self, key: str, value: Any) -> Any:
        """Append-only, and ``predicted_at`` timezone-aware (§41.3, §41.4).

        An editable prediction would make every accuracy measurement meaningless.
        """
        if inspect(self).persistent:
            raise ValueError(
                "prediction_result is append-only; %s cannot be reassigned "
                "once the row is persistent" % key
            )
        if key == "predicted_at":
            return require_timezone_aware(key, value)
        if key == "prediction_result_code":
            return value.strip().upper()
        return value
