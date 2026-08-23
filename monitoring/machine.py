"""Machine condition and data quality detection, from telemetry.

Four of the twelve documented event types, all reached from
``machine_sensor_reading``:

============================  ============================================================
``threshold_warning``         value beyond ``alert_threshold_rule`` warning limit
``threshold_critical``        value beyond ``alert_threshold_rule`` critical limit
``rate_of_change_exceeded``   change per minute beyond ``rate_of_change_limit_per_minute``
``sensor_out_of_range``       reading flagged ``out_of_physical_range``
``telemetry_stale``           no reading for three sampling intervals
============================  ============================================================

**Detection is per episode, not per reading.** §E14 records one alert absorbing 34 events
over eleven hours as vibration flapped above and below its limit -- roughly one event per
excursion across a period holding thousands of samples. An episode opens on the first
breaching reading, is confirmed once it has persisted for the rule's
``sustained_duration_seconds``, produces exactly one event at confirmation, and closes
when the value returns inside the limit. Emitting an event per breaching sample would
produce thousands of events per hour and defeat the correlation it feeds.

**Rule 5 is enforced, not assumed.** ``sustained_duration_seconds`` on the event is the
duration the condition **actually** persisted, and it is never less than the rule's
configured value, because confirmation is what triggers the write. §E13 rule 5 calls an
event confirmed early a defect.

**The threshold value is captured, not referenced.** ``threshold_value_breached`` stores
the limit in force at detection time. This is the one permitted master value copy in the
entire model (§3.5): profiles are versioned and retuned, so re-reading the rule six
months later would return the current limit rather than the one that fired, and a
recommendation citing "4.74 mm/s against a warning limit of 4.70" would silently become
wrong. The rule is referenced for lineage; the value is captured for evidence.

**Invalid readings raise nothing, with one deliberate inversion.** §E13 rule 6: events are
not raised from readings whose ``quality_flag`` is not ``valid`` -- except
``sensor_out_of_range``, which is raised *because* the reading was invalid. That inversion
is how instrument failure gets detected rather than silently ignored, and it is why a
broken sensor produces a ``data_quality`` case rather than a phantom machine fault.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.operational import MachineOperationalStatus, MachineSensorReading

from monitoring.context import BreachEpisode, Detection, MonitoringContext

# Readings per cycle. Bounds the work one cycle does on the highest-volume table in the
# database; the cursor guarantees the remainder is picked up by the next cycle.
READING_BATCH = 40000

# States in which a reading is not evidence of a machine condition, judged from the
# reading's own recorded state. E13 rule 7: abnormal behaviour during a changeover or a
# planned stoppage is expected. ``down_unplanned`` is absent deliberately -- a machine
# that has broken down is exactly when its readings matter most.
GATED_READING_STATES = frozenset({"setup", "down_planned", "offline"})


class MachineMonitor:
    """Machine condition and telemetry data quality."""

    def __init__(self, context: MonitoringContext) -> None:
        self.context = context

    def evaluate(self, session: Session) -> list[Detection]:
        """Examine readings arriving since the last cycle."""
        context = self.context
        readings = list(session.scalars(
            select(MachineSensorReading)
            .where(MachineSensorReading.machine_sensor_reading_id
                   > context.cursors.reading_id)
            .order_by(MachineSensorReading.machine_sensor_reading_id)
            .limit(READING_BATCH)
        ))
        if not readings:
            return []

        detections: list[Detection] = []
        for reading in readings:
            context.cursors.reading_id = max(
                context.cursors.reading_id, reading.machine_sensor_reading_id)

            flag = reading.quality_flag.value
            if flag != "valid":
                detection = self._sensor_fault(reading, flag)
                if detection is not None:
                    detections.append(detection)
                continue

            # E13 rule 7 gates machine-subject events, and the state that matters is the
            # one the machine was in **when the reading was taken** -- not the one it is
            # in now. ``machine_state_at_reading`` is denormalised onto the reading for
            # exactly this: §E1 records that the Monitoring Agent "gates evaluation by
            # state ... a large source of false positives eliminated by one lookup".
            #
            # Gating on current state instead would discard a machine's entire history
            # the moment it went in for repair, which is precisely the evidence that
            # explains why it needed repairing.
            if reading.machine_state_at_reading.value in GATED_READING_STATES:
                continue

            detections.extend(self._threshold(reading))

        detections.extend(self._staleness(session))
        return detections

    # ------------------------------------------------------------ data quality

    def _sensor_fault(
        self,
        reading: MachineSensorReading,
        flag: str,
    ) -> Detection | None:
        """A reading the instrument itself could not be trusted to produce.

        Only ``out_of_physical_range`` maps to a documented event type. The other flags
        -- ``sensor_offline``, ``interpolated``, ``stale`` -- record sensor trust on the
        reading and have no event type in the vocabulary, so they are left as the data
        quality facts they are rather than forced into a type that does not describe
        them.
        """
        if flag != "out_of_physical_range":
            return None

        context = self.context
        master = context.master
        machine = master.machines.get(reading.machine_id)
        if machine is None:
            return None
        parameter = master.parameters.get(reading.machine_parameter_id)
        if parameter is None:
            return None

        value = reading.reading_value
        above = value > parameter.physical_max
        limit = parameter.physical_max if above else parameter.physical_min
        unit = master.unit_of(reading.machine_parameter_id)

        # A failed instrument is a data problem, not a machine problem. The severity
        # comes from the machine's own threshold rule for the parameter when one exists,
        # so a sensor on a tightly monitored parameter is treated as seriously as the
        # parameter is; otherwise it falls to the least severe level, because an
        # unmonitored parameter's sensor is not urgent.
        rule = master.rule_for(machine, reading.machine_parameter_id)
        severity_id = (
            rule.warning_severity_level_id if rule is not None
            else master.least_severe_id()
        )

        return Detection(
            category="data_quality",
            event_type="sensor_out_of_range",
            detected_at=reading.recorded_at,
            severity_level_id=severity_id,
            correlation_subject=machine.machine_code,
            machine_id=machine.machine_id,
            production_line_id=machine.production_line_id,
            machine_parameter_id=reading.machine_parameter_id,
            observed_value=value,
            threshold_value_breached=limit,
            threshold_direction="above_high" if above else "below_low",
            triggering_reading_id=reading.machine_sensor_reading_id,
            detection_note=(
                "%s reported %s %s, outside its physical range %s to %s %s. "
                "Instrument fault: the reading is excluded from condition assessment."
                % (parameter.parameter_name, value, unit,
                   parameter.physical_min, parameter.physical_max, unit)
            ),
        )

    def _staleness(self, session: Session) -> list[Detection]:
        """Monitored machines that have stopped reporting.

        E2 rule 9: ``last_reading_at`` older than three sampling intervals for a
        monitored, non-offline machine indicates a telemetry pipeline fault. The rule
        also says this is a data problem rather than a machine problem -- "treating a
        data outage as a machine problem produces meaningless alerts" -- which is why the
        event is raised under ``data_quality`` and not ``machine_condition``.

        The reference instant is the newest reading in the database, not wall-clock time.
        The agent analyses a recorded factory: comparing against now would report every
        machine as stale the moment the simulator stops.
        """
        context = self.context
        master = context.master

        newest = session.execute(
            select(MachineSensorReading.recorded_at)
            .order_by(MachineSensorReading.recorded_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        if newest is None:
            return []

        detections: list[Detection] = []
        for status in session.scalars(select(MachineOperationalStatus)):
            machine = master.machines.get(status.machine_id)
            if machine is None or not machine.is_monitored:
                continue
            if status.current_state.value == "offline":
                continue
            if context.suppression_for(machine.machine_id) is not None:
                continue

            window = context.stale_after(machine)
            if window is None:
                continue  # the type declares no parameters; it emits no telemetry
            if status.last_reading_at is None:
                continue  # never reported at all; not a staleness transition

            silence = newest - status.last_reading_at
            if silence <= window:
                continue

            key = (machine.machine_id, 0, "stale")
            if key in context.episodes:
                continue  # already reported; not re-raised until telemetry resumes
            context.episodes[key] = BreachEpisode(started_at=status.last_reading_at,
                                                  reported=True)

            detections.append(Detection(
                category="data_quality",
                event_type="telemetry_stale",
                detected_at=newest,
                severity_level_id=master.least_severe_id(),
                correlation_subject=machine.machine_code,
                machine_id=machine.machine_id,
                production_line_id=machine.production_line_id,
                sustained_duration_seconds=int(silence.total_seconds()),
                detection_note=(
                    "No telemetry from %s for %d seconds, exceeding three sampling "
                    "intervals (%d seconds). Data pipeline fault, not a machine fault."
                    % (machine.machine_code, int(silence.total_seconds()),
                       int(window.total_seconds()))
                ),
            ))
        return detections

    # -------------------------------------------------------- machine condition

    def _threshold(self, reading: MachineSensorReading) -> list[Detection]:
        """Threshold, and rate-of-change, against the machine's own profile."""
        context = self.context
        master = context.master
        machine = master.machines.get(reading.machine_id)
        if machine is None:
            return []
        rule = master.rule_for(machine, reading.machine_parameter_id)
        if rule is None:
            return []  # not threshold-monitored; a master data statement, not a gap

        detections: list[Detection] = []
        value = reading.reading_value

        # Critical is tested first so a value beyond both limits reports the more severe
        # condition rather than both.
        breach = self._limit_breach(value, rule.critical_low, rule.critical_high)
        if breach is not None:
            detection = self._episode(
                reading, machine, rule, "threshold_critical", breach,
                rule.critical_severity_level_id)
            if detection is not None:
                detections.append(detection)
            self._close_episode(reading, "threshold_warning")
        else:
            self._close_episode(reading, "threshold_critical")
            breach = self._limit_breach(value, rule.warning_low, rule.warning_high)
            if breach is not None:
                detection = self._episode(
                    reading, machine, rule, "threshold_warning", breach,
                    rule.warning_severity_level_id)
                if detection is not None:
                    detections.append(detection)
            else:
                self._close_episode(reading, "threshold_warning")

        rate = self._rate_breach(reading, rule)
        if rate is not None:
            detections.append(rate)
        return detections

    @staticmethod
    def _limit_breach(
        value: Decimal,
        low: Decimal | None,
        high: Decimal | None,
    ) -> tuple[str, Decimal] | None:
        """Which limit the value crossed, and its value.

        A NULL limit is not a missing value: §28 records it as "low readings are not a
        concern for this parameter". Only the limits master data declares are tested.
        """
        if high is not None and value > high:
            return "above_high", high
        if low is not None and value < low:
            return "below_low", low
        return None

    def _episode(
        self,
        reading: MachineSensorReading,
        machine,
        rule,
        event_type: str,
        breach: tuple[str, Decimal],
        severity_level_id: int,
    ) -> Detection | None:
        """Track a breach across readings and report it once, on confirmation."""
        context = self.context
        direction, limit = breach
        key = (reading.machine_id, reading.machine_parameter_id, event_type)
        episode = context.episodes.get(key)

        if episode is None:
            context.episodes[key] = BreachEpisode(
                started_at=reading.recorded_at,
                last_value=reading.reading_value,
                last_reading_id=reading.machine_sensor_reading_id,
            )
            episode = context.episodes[key]

        episode.last_value = reading.reading_value
        episode.last_reading_id = reading.machine_sensor_reading_id
        if episode.reported:
            return None

        required = int(rule.sustained_duration_seconds or 0)
        persisted = int((reading.recorded_at - episode.started_at).total_seconds())
        if persisted < required:
            return None  # E13 rule 5: an event confirmed early is a defect

        episode.reported = True
        parameter = context.master.parameters.get(reading.machine_parameter_id)
        unit = context.master.unit_of(reading.machine_parameter_id)
        declaration = context.master.declarations.get(
            (machine.machine_type_id, reading.machine_parameter_id))
        envelope = ""
        if declaration is not None:
            envelope = "; healthy range %s to %s %s" % (
                declaration.normal_min, declaration.normal_max, unit)

        return Detection(
            category="machine_condition",
            event_type=event_type,
            detected_at=reading.recorded_at,
            severity_level_id=severity_level_id,
            correlation_subject=machine.machine_code,
            machine_id=machine.machine_id,
            production_line_id=machine.production_line_id,
            production_run_id=reading.production_run_id,
            machine_parameter_id=reading.machine_parameter_id,
            alert_threshold_rule_id=rule.alert_threshold_rule_id,
            observed_value=reading.reading_value,
            threshold_value_breached=limit,
            threshold_direction=direction,
            sustained_duration_seconds=persisted,
            triggering_reading_id=reading.machine_sensor_reading_id,
            detection_note=(
                "%s at %s %s sustained %ds %s the %s limit of %s %s%s"
                % (
                    parameter.parameter_name if parameter is not None else "parameter",
                    reading.reading_value, unit, persisted,
                    "above" if direction == "above_high" else "below",
                    "critical" if event_type == "threshold_critical" else "warning",
                    limit, unit, envelope,
                )
            ),
        )

    @staticmethod
    def _slope_per_minute(history: list[tuple]) -> float:
        """Least-squares gradient per minute over the samples in the window.

        A rate of change is the slope of the values against time, and the least-squares
        slope is the estimator for it. Two point samples would also give a slope, but its
        error is the full sensor noise; a fitted slope over ``n`` samples reduces that
        error by roughly the square root of ``n``, which is the difference between
        measuring the machine and measuring the instrument.
        """
        if len(history) < 2:
            return 0.0
        base = history[0][0]
        times = [(moment - base).total_seconds() / 60.0 for moment, _ in history]
        values = [float(value) for _, value in history]
        n = float(len(history))
        mean_t = sum(times) / n
        mean_v = sum(values) / n
        variance = sum((t - mean_t) ** 2 for t in times)
        if variance <= 0:
            return 0.0
        covariance = sum(
            (t - mean_t) * (v - mean_v) for t, v in zip(times, values))
        return covariance / variance

    def _close_episode(self, reading: MachineSensorReading, event_type: str) -> None:
        """Forget a breach once the value is back inside its limit.

        Closing the episode is what allows the next excursion to be reported: this is the
        mechanism behind an alert absorbing many events as a value flaps across a limit.
        """
        self.context.episodes.pop(
            (reading.machine_id, reading.machine_parameter_id, event_type), None)

    def _rate_breach(self, reading: MachineSensorReading, rule) -> Detection | None:
        """Gradient per minute beyond the rule's configured rate limit.

        A rate limit catches a fast excursion that has not yet crossed a level limit --
        a thermal runaway is visible in the gradient before it is visible in the value.
        A NULL limit means the parameter has no rate concern and none is tested.

        **The gradient is measured over the rule's own sustained window, not between two
        adjacent samples.** Adjacent samples differ mostly by sensor noise, and dividing
        that difference by a ten-second interval yields a large rate that describes the
        instrument rather than the machine. §E13's own worked example carries
        ``sustained_duration_seconds`` of 60 on a rate event whose rule configures 30,
        which is a gradient measured across a window rather than a point-to-point delta.

        **Reported once per excursion.** Like a threshold breach, a rate breach is an
        episode: it is reported when it begins and not again until the gradient falls
        back inside the limit. Without that, a parameter drifting for an hour would emit
        one event per sample, which is the alert storm §E14 exists to prevent.
        """
        limit = rule.rate_of_change_limit_per_minute
        if limit is None:
            return None

        context = self.context
        sample_key = (reading.machine_id, reading.machine_parameter_id)
        window = int(rule.sustained_duration_seconds or 0)
        if window <= 0:
            declaration = context.master.declarations.get(
                (context.master.machines[reading.machine_id].machine_type_id,
                 reading.machine_parameter_id)
            )
            window = (
                int(declaration.sampling_interval_seconds)
                if declaration is not None else 60
            )

        history = context.samples.setdefault(sample_key, [])
        history.append((reading.recorded_at, reading.reading_value))
        # Keep one sample beyond the window so a full-window gradient is available.
        cutoff = reading.recorded_at - timedelta(seconds=window)
        while len(history) > 2 and history[1][0] < cutoff:
            history.pop(0)

        episode_key = (reading.machine_id, reading.machine_parameter_id, "rate")
        oldest_at, _ = history[0]
        seconds = (reading.recorded_at - oldest_at).total_seconds()
        if seconds < window or seconds <= 0:
            return None  # not yet a full window; a partial gradient is not the rate

        rate = abs(self._slope_per_minute(history))
        if rate <= float(limit):
            context.episodes.pop(episode_key, None)  # recovered; re-arm the detector
            return None

        episode = context.episodes.get(episode_key)
        if episode is not None and episode.reported:
            return None
        context.episodes[episode_key] = BreachEpisode(
            started_at=reading.recorded_at,
            reported=True,
            last_value=reading.reading_value,
            last_reading_id=reading.machine_sensor_reading_id,
        )

        machine = context.master.machines.get(reading.machine_id)
        if machine is None:
            return None
        parameter = context.master.parameters.get(reading.machine_parameter_id)
        unit = context.master.unit_of(reading.machine_parameter_id)

        return Detection(
            category="machine_condition",
            event_type="rate_of_change_exceeded",
            detected_at=reading.recorded_at,
            severity_level_id=rule.warning_severity_level_id,
            correlation_subject=machine.machine_code,
            machine_id=machine.machine_id,
            production_line_id=machine.production_line_id,
            production_run_id=reading.production_run_id,
            machine_parameter_id=reading.machine_parameter_id,
            alert_threshold_rule_id=rule.alert_threshold_rule_id,
            observed_value=Decimal("%.4f" % rate),
            threshold_value_breached=limit,
            threshold_direction="rate_exceeded",
            sustained_duration_seconds=int(seconds),
            triggering_reading_id=reading.machine_sensor_reading_id,
            detection_note=(
                "%s changed at %.4f %s per minute measured over %ds, exceeding the "
                "configured rate limit of %s %s per minute"
                % (parameter.parameter_name if parameter is not None else "parameter",
                   rate, unit, int(seconds), limit, unit)
            ),
        )
