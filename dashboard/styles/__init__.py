"""Presentation styling for the dashboard. One palette, one stylesheet, no local colours."""

from dashboard.styles.theme import (
    ALERT_STATUS_TONE,
    DELIVERY_STATUS_TONE,
    ESCALATION_TONE,
    MACHINE_STATE_TONE,
    TONES,
    inject_css,
    severity_colour,
    tone_colour,
)

__all__ = [
    "ALERT_STATUS_TONE",
    "DELIVERY_STATUS_TONE",
    "ESCALATION_TONE",
    "MACHINE_STATE_TONE",
    "TONES",
    "inject_css",
    "severity_colour",
    "tone_colour",
]
