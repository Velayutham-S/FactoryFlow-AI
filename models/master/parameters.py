"""Master models M11-M12: what is measured, and what normal looks like per type."""

from typing import TYPE_CHECKING, Optional

from sqlalchemy import (
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
    DegradationDirection,
    DriftDirection,
    MeasurementDomain,
    ParameterDataType,
)
from models.mixins import SoftDeleteMixin, TimestampCreatedMixin, TimestampUpdatedMixin
from models.types import MasterPk, Measurement, Percent, Weight

if TYPE_CHECKING:
    from models.master.equipment import MachineType
    from models.master.failure import MachineTypeFailureMode
    from models.master.thresholds import AlertThresholdRule


class MachineParameter(SoftDeleteMixin, TimestampCreatedMixin,
                       TimestampUpdatedMixin, Base):
    """Table ``machine_parameter``, master group: the catalogue of measurable
    quantities, with the physical envelope every reading is judged against."""

    __tablename__ = "machine_parameter"
    __table_args__ = (
        UniqueConstraint("machine_parameter_code", name="uq_machine_parameter_code"),
        CheckConstraint(
            "machine_parameter_code GLOB 'PRM-[A-Z][A-Z][A-Z]*' "
            "AND length(machine_parameter_code) BETWEEN 7 AND 12 "
            "AND substr(machine_parameter_code, 5) NOT GLOB '*[^A-Z]*'",
            name=conv("ck_machine_parameter_code_format")),
        CheckConstraint("physical_min < physical_max",
                        name=conv("ck_machine_parameter_physical_range_ordered")),
        CheckConstraint(
            "is_cumulative = 0 OR degradation_direction = 'increasing'",
            name=conv("ck_machine_parameter_cumulative_increasing")),
        CheckConstraint("length(trim(unit_of_measure)) > 0",
                        name=conv("ck_machine_parameter_unit_not_blank")),
        CheckConstraint("length(trim(parameter_name)) > 0",
                        name=conv("ck_machine_parameter_name_not_blank")),
        CheckConstraint(
            "measurement_domain IN ('thermal', 'mechanical', 'electrical', "
            "'tooling', 'pneumatic', 'hydraulic', 'positional')",
            name=conv("ck_machine_parameter_measurement_domain_allowed")),
        CheckConstraint(
            "data_type IN ('numeric_continuous', 'numeric_integer', 'boolean')",
            name=conv("ck_machine_parameter_data_type_allowed")),
        CheckConstraint(
            "degradation_direction IN ('increasing', 'decreasing', "
            "'bidirectional')",
            name=conv("ck_machine_parameter_degradation_direction_allowed")),
        CheckConstraint(
            "length(machine_parameter_code) <= 12",
            name=conv("ck_machine_parameter_machine_parameter_code_length")),
        CheckConstraint("length(parameter_name) <= 80",
                        name=conv("ck_machine_parameter_parameter_name_length")),
        CheckConstraint("length(unit_of_measure) <= 16",
                        name=conv("ck_machine_parameter_unit_of_measure_length")),
        CheckConstraint("is_cumulative IN (0, 1)",
                        name=conv("ck_machine_parameter_is_cumulative_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_machine_parameter_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    machine_parameter_id: Mapped[MasterPk]
    machine_parameter_code: Mapped[str] = mapped_column(String(12))
    parameter_name: Mapped[str] = mapped_column(String(80))
    # NOT the unit_of_measure vocabulary used by Product and InventoryItem.
    # Units are an open set -- a new instrument may introduce Pa, dB or um --
    # and the value is displayed rather than compared, so there is no integrity
    # benefit to closing it (§40.6). The two must not be conflated.
    unit_of_measure: Mapped[str] = mapped_column(String(16))
    measurement_domain: Mapped[MeasurementDomain] = mapped_column(
        Enum(MeasurementDomain, name="measurement_domain", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    data_type: Mapped[ParameterDataType] = mapped_column(
        Enum(ParameterDataType, name="parameter_data_type", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    # Sensor floor and ceiling, not a normal-range bound.
    physical_min: Mapped[Measurement]
    physical_max: Mapped[Measurement]
    degradation_direction: Mapped[DegradationDirection] = mapped_column(
        Enum(DegradationDirection, name="degradation_direction",
             native_enum=False, create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    is_cumulative: Mapped[bool] = mapped_column(server_default=text("0"))
    description: Mapped[Optional[str]] = mapped_column(Text)

    type_parameters: Mapped[list["MachineTypeParameter"]] = relationship(
        back_populates="machine_parameter", lazy="select"
    )
    threshold_rules: Mapped[list["AlertThresholdRule"]] = relationship(
        back_populates="machine_parameter", lazy="select"
    )
    primary_for_failure_modes: Mapped[list["MachineTypeFailureMode"]] = relationship(
        back_populates="primary_machine_parameter", lazy="select"
    )

    # machine_sensor_reading (32 million rows a year) and operational_event are
    # deliberately unmapped under rule L1 (§19.1).

    @validates("machine_parameter_code")
    def _normalise_code(self, key: str, value: str) -> str:
        # Treat as immutable in practice: renaming one orphans historical
        # telemetry. Procedural rather than a hook, because administrative
        # correction before any telemetry exists is legitimate (§41.4).
        return value.strip().upper()


class MachineTypeParameter(SoftDeleteMixin, TimestampCreatedMixin,
                           TimestampUpdatedMixin, Base):
    """Table ``machine_type_parameter``, master group: the per-type operating envelope
    and the definition of the Prediction Agent's feature set."""

    __tablename__ = "machine_type_parameter"
    __table_args__ = (
        UniqueConstraint("machine_type_id", "machine_parameter_id",
                         name="uq_machine_type_parameter_pair"),
        CheckConstraint("normal_min < normal_max",
                        name=conv("ck_mtp_normal_range_ordered")),
        CheckConstraint("nominal_value BETWEEN normal_min AND normal_max",
                        name=conv("ck_mtp_nominal_within_envelope")),
        CheckConstraint("sampling_interval_seconds BETWEEN 1 AND 3600",
                        name=conv("ck_mtp_sampling_interval_range")),
        CheckConstraint(
            "sensor_accuracy_pct IS NULL OR sensor_accuracy_pct BETWEEN 0 AND 25",
            name=conv("ck_mtp_sensor_accuracy_range")),
        CheckConstraint(
            "criticality_weight IS NULL OR criticality_weight BETWEEN 0 AND 5",
            name=conv("ck_mtp_criticality_weight_range")),
        CheckConstraint(
            "expected_drift_direction IN ('increasing', 'decreasing', 'none')",
            name=conv("ck_mtp_expected_drift_direction_allowed")),
        CheckConstraint("is_ml_feature IN (0, 1)",
                        name=conv("ck_mtp_is_ml_feature_bool")),
        CheckConstraint("is_active IN (0, 1)",
                        name=conv("ck_mtp_is_active_bool")),
        {"sqlite_autoincrement": True},
    )

    machine_type_parameter_id: Mapped[MasterPk]
    machine_type_id: Mapped[int] = mapped_column(
        ForeignKey("machine_type.machine_type_id", name="fk_mtp_machine_type",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    machine_parameter_id: Mapped[int] = mapped_column(
        ForeignKey("machine_parameter.machine_parameter_id",
                   name="fk_mtp_machine_parameter",
                   ondelete="RESTRICT", onupdate="RESTRICT")
    )
    nominal_value: Mapped[Measurement]
    # Operational expectation, not a sensor limit. The rule that this range sits
    # within the parameter's physical bounds spans two tables and belongs to the
    # administrative writer (§41.3).
    normal_min: Mapped[Measurement]
    normal_max: Mapped[Measurement]
    sampling_interval_seconds: Mapped[int] = mapped_column(Integer)
    # Determines which parameters participate in a feature vector, and therefore
    # the shape of prediction_feature_snapshot.feature_values (§39.2).
    is_ml_feature: Mapped[bool] = mapped_column(server_default=text("1"))
    expected_drift_direction: Mapped[DriftDirection] = mapped_column(
        Enum(DriftDirection, name="drift_direction", native_enum=False,
             create_constraint=False,
             values_callable=lambda enum_cls: [m.value for m in enum_cls])
    )
    sensor_accuracy_pct: Mapped[Optional[Percent]]
    criticality_weight: Mapped[Optional[Weight]]

    machine_type: Mapped["MachineType"] = relationship(
        back_populates="type_parameters", lazy="select"
    )
    machine_parameter: Mapped["MachineParameter"] = relationship(
        back_populates="type_parameters", lazy="select"
    )
