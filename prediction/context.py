"""Shared state: the master snapshot, the policy thresholds, and the code sequences.

**Every threshold this agent applies is policy read from ``business_rule``.** E15 rule 6
says the completeness threshold "comes from `business_rule`, not from a constant"; E16
rule 6 says `risk_severity_level_id` is derived "using `business_rule` cut-offs, never a
hardcoded mapping". Both are looked up here by code, and their absence is an error rather
than a default -- master data rule 4 is explicit that a consumer defaulting silently in
code defeats the point of the entity.

The snapshot holds detached ORM instances; only mapped column attributes are read from
them, never a relationship. Same convention the simulator and Monitoring Agent use.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.master import (
    AlertThresholdRule,
    BusinessRule,
    FailureCategory,
    FailureSeverityLevel,
    Machine,
    MachineCategory,
    MachineParameter,
    MachineType,
    MachineTypeFailureMode,
    MachineTypeParameter,
    Plant,
    Shift,
)
from models.operational import PredictionFeatureSnapshot, PredictionResult

from prediction.errors import MasterDataUnavailableError

PREDICTION_COMPONENT = "prediction_agent"

# The feature definition this implementation produces. Stored on every snapshot so a
# vector remains interpretable against the definition that made it (E15 rule 8).
FEATURE_SET_VERSION = "fs-v1.0"

# Policy rule codes read from business_rule. The documents state these values are
# business_rule data but define no codes for them, so the codes below are this agent's
# lookup contract and are reported as required master data rather than assumed.
RULE_COMPLETENESS_MIN = "BR-PRED-COMPLETE-MIN"
RULE_RISK_PREFIX = "BR-PRED-RISK-"

# The eight per-parameter features and ten machine-level features named in E15.
PARAMETER_FEATURES = (
    "latest_value", "window_mean", "window_min", "window_max", "window_stddev",
    "slope_per_hour", "pct_above_normal_max", "seconds_above_warning_limit",
)
MACHINE_FEATURES = (
    "accumulated_operating_hours", "hours_since_last_maintenance",
    "cycles_since_last_maintenance", "pct_of_design_life", "pct_of_mtbf_elapsed",
    "mean_cycle_deviation_pct", "cycle_deviation_slope", "event_count_24h",
    "attributed_scrap_count_24h", "age_days",
)


@dataclass(frozen=True)
class RiskBand:
    """One probability cut-off mapping onto a severity level."""

    severity_level_id: int
    severity_code: str
    minimum_probability: float
    business_rule_id: int


class MasterSnapshot:
    """Every master row the agent reads, loaded once and indexed."""

    def __init__(self, session: Session) -> None:
        self.plant: Plant | None = session.scalars(select(Plant)).first()
        if self.plant is None:
            raise MasterDataUnavailableError("no plant row; master data is not seeded")
        self.timezone = ZoneInfo(self.plant.timezone)

        self.machines: dict[int, Machine] = {
            m.machine_id: m for m in session.scalars(select(Machine))
        }
        self.machine_types: dict[int, MachineType] = {
            t.machine_type_id: t for t in session.scalars(select(MachineType))
        }
        self.categories: dict[int, MachineCategory] = {
            c.machine_category_id: c for c in session.scalars(select(MachineCategory))
        }
        self.parameters: dict[int, MachineParameter] = {
            p.machine_parameter_id: p
            for p in session.scalars(select(MachineParameter))
        }
        self.severities: dict[int, FailureSeverityLevel] = {
            s.failure_severity_level_id: s
            for s in session.scalars(select(FailureSeverityLevel))
        }
        if not self.severities:
            raise MasterDataUnavailableError("no failure_severity_level rows")
        self.failure_categories: dict[int, FailureCategory] = {
            c.failure_category_id: c for c in session.scalars(select(FailureCategory))
        }
        self.shifts = [
            s for s in session.scalars(select(Shift))
            if s.shift_type.value == "production" and s.is_active
        ]
        if not self.shifts:
            raise MasterDataUnavailableError("no active production shift")

        # Declared parameters per type. E15 rule 3: the vector must contain a block for
        # every parameter where is_ml_feature = 1, and only those participate.
        self.ml_parameters_by_type: dict[int, list[MachineTypeParameter]] = {}
        self.declarations: dict[tuple[int, int], MachineTypeParameter] = {}
        for decl in session.scalars(select(MachineTypeParameter)):
            if not decl.is_active:
                continue
            self.declarations[(decl.machine_type_id, decl.machine_parameter_id)] = decl
            if decl.is_ml_feature:
                self.ml_parameters_by_type.setdefault(
                    decl.machine_type_id, []).append(decl)
        for group in self.ml_parameters_by_type.values():
            group.sort(key=lambda d: self.parameters[d.machine_parameter_id]
                       .machine_parameter_code)

        # Declared failure modes. Only is_model_predictable modes may ever be predicted
        # (E16 rule 4), so the predictable set is indexed separately.
        self.modes_by_type: dict[int, list[MachineTypeFailureMode]] = {}
        self.predictable_by_type: dict[int, list[MachineTypeFailureMode]] = {}
        for mode in session.scalars(select(MachineTypeFailureMode)):
            if not mode.is_active:
                continue
            self.modes_by_type.setdefault(mode.machine_type_id, []).append(mode)
            if mode.is_model_predictable:
                self.predictable_by_type.setdefault(
                    mode.machine_type_id, []).append(mode)

        # Warning limits, used for seconds_above_warning_limit.
        self.rules_by_profile: dict[int, dict[int, AlertThresholdRule]] = {}
        for rule in session.scalars(select(AlertThresholdRule)):
            if rule.is_active and rule.is_enabled:
                self.rules_by_profile.setdefault(
                    rule.alert_threshold_profile_id, {}
                )[rule.machine_parameter_id] = rule

    def scored_machines(self) -> list[Machine]:
        """Machines eligible for a snapshot.

        E15 rule 1: only machines that are monitored, in service, and whose category
        has ``requires_condition_monitoring = 1``. A machine whose type declares no ML
        feature is also excluded -- rule 3 requires a block per declared feature, and
        with none declared there is no vector to build.
        """
        eligible = []
        for machine in self.machines.values():
            if not machine.is_monitored:
                continue
            if machine.lifecycle_status.value != "in_service":
                continue
            machine_type = self.machine_types.get(machine.machine_type_id)
            if machine_type is None:
                continue
            category = self.categories.get(machine_type.machine_category_id)
            if category is None or not category.requires_condition_monitoring:
                continue
            if not self.ml_parameters_by_type.get(machine.machine_type_id):
                continue
            eligible.append(machine)
        return sorted(eligible, key=lambda m: m.machine_code)

    def category_of(self, machine: Machine) -> MachineCategory | None:
        machine_type = self.machine_types.get(machine.machine_type_id)
        if machine_type is None:
            return None
        return self.categories.get(machine_type.machine_category_id)

    def warning_limits(
        self,
        machine: Machine,
        parameter_id: int,
    ) -> tuple[Decimal | None, Decimal | None]:
        if machine.alert_threshold_profile_id is None:
            return None, None
        rule = self.rules_by_profile.get(
            machine.alert_threshold_profile_id, {}).get(parameter_id)
        if rule is None:
            return None, None
        return rule.warning_low, rule.warning_high

    def shift_at(self, moment: datetime) -> Shift:
        clock = moment.astimezone(self.timezone).time()
        for shift in self.shifts:
            if shift.crosses_midnight:
                if clock >= shift.start_time or clock < shift.end_time:
                    return shift
            elif shift.start_time <= clock < shift.end_time:
                return shift
        return self.shifts[0]


class PredictionContext:
    """Master data, policy thresholds and code sequences."""

    def __init__(
        self,
        session: Session,
        *,
        completeness_minimum: float | None = None,
        risk_bands: list[RiskBand] | None = None,
    ) -> None:
        self.master = MasterSnapshot(session)
        self._codes: dict[str, int] = {}
        self._load_code_counters(session)
        self.completeness_minimum = (
            completeness_minimum
            if completeness_minimum is not None
            else self._load_completeness_minimum(session)
        )
        self.risk_bands = (
            risk_bands if risk_bands is not None else self._load_risk_bands(session)
        )

    # ------------------------------------------------------------------ policy

    def _load_completeness_minimum(self, session: Session) -> float:
        rule = session.scalars(
            select(BusinessRule).where(
                BusinessRule.business_rule_code == RULE_COMPLETENESS_MIN,
                BusinessRule.is_active.is_(True),
            )
        ).first()
        if rule is None or rule.value_numeric is None:
            raise MasterDataUnavailableError(
                "no active business_rule %r supplying the minimum "
                "data_completeness_pct for inference. E15 rule 6 requires this "
                "threshold to come from business_rule rather than a constant, so the "
                "agent will not substitute one. Add a numeric global rule with that "
                "code, or pass completeness_minimum explicitly."
                % RULE_COMPLETENESS_MIN
            )
        return float(rule.value_numeric)

    def _load_risk_bands(self, session: Session) -> list[RiskBand]:
        """Probability cut-offs mapping onto the severity scale.

        One rule per severity level, coded ``BR-PRED-RISK-<severity code suffix>``,
        whose numeric value is the minimum probability for that level. Bands are
        returned most severe first so the first match wins.
        """
        bands: list[RiskBand] = []
        for rule in session.scalars(
            select(BusinessRule).where(
                BusinessRule.business_rule_code.like(RULE_RISK_PREFIX + "%"),
                BusinessRule.is_active.is_(True),
            )
        ):
            if rule.value_numeric is None:
                continue
            suffix = rule.business_rule_code[len(RULE_RISK_PREFIX):]
            severity = next(
                (s for s in self.master.severities.values()
                 if s.failure_severity_level_code.upper().endswith(suffix.upper())
                 or s.failure_severity_level_code.upper() == suffix.upper()),
                None,
            )
            if severity is None:
                continue
            bands.append(RiskBand(
                severity_level_id=severity.failure_severity_level_id,
                severity_code=severity.failure_severity_level_code,
                minimum_probability=float(rule.value_numeric),
                business_rule_id=rule.business_rule_id,
            ))
        if not bands:
            raise MasterDataUnavailableError(
                "no active business_rule rows matching %r supplying the "
                "probability-to-severity cut-offs. E16 rule 6 requires this mapping to "
                "come from business_rule and forbids a hardcoded one, so the agent will "
                "not substitute a mapping. Add one numeric global rule per severity "
                "level, or pass risk_bands explicitly."
                % (RULE_RISK_PREFIX + "*")
            )
        bands.sort(key=lambda b: b.minimum_probability, reverse=True)
        return bands

    def severity_for(self, probability: float) -> RiskBand:
        """The severity band a probability falls into."""
        for band in self.risk_bands:
            if probability >= band.minimum_probability:
                return band
        return self.risk_bands[-1]

    # ------------------------------------------------------------------- codes

    def _load_code_counters(self, session: Session) -> None:
        for column in (
            PredictionFeatureSnapshot.prediction_feature_snapshot_code,
            PredictionResult.prediction_result_code,
        ):
            for existing in session.scalars(select(column)):
                scope, _, suffix = str(existing).rpartition("-")
                try:
                    value = int(suffix)
                except ValueError:
                    continue
                self._codes[scope] = max(self._codes.get(scope, 0), value)

    def _next(self, prefix: str, moment: datetime, width: int) -> str:
        key = "%s-%s" % (prefix, moment.astimezone(self.master.timezone)
                         .strftime("%Y%m%d"))
        nxt = self._codes.get(key, 0) + 1
        self._codes[key] = nxt
        return "%s-%0*d" % (key, width, nxt)

    def snapshot_code(self, moment: datetime) -> str:
        return self._next("FSN", moment, 5)

    def prediction_code(self, moment: datetime) -> str:
        return self._next("PDN", moment, 4)
