"""One palette and one stylesheet for the whole dashboard.

**Severity colour is not invented here.** ``failure_severity_level.display_color_hex`` is
a real master-data column, so the five severity bands already have an agreed colour and
this module quotes it rather than choosing one. :func:`severity_colour` falls back to the
semantic tone for the band's ``severity_rank`` only when the column is absent, which keeps
the dashboard working against a database seeded before that column was populated.

**Every other colour is semantic, not decorative.** Five tones carry meaning -- healthy,
warning, critical, information, neutral -- and nothing in the dashboard picks a colour
outside them. A page that wants to say "this is bad" asks for the ``critical`` tone; it
does not know which hex that is.

**Readability comes before brand.** Accents are used for borders, dots and small labels,
never for body text on a coloured field. Card backgrounds are translucent
(``color-mix`` over the app's own surface) so the same stylesheet reads correctly on
Streamlit's light and dark themes without either being special-cased, and card text
inherits the theme's own foreground colour rather than being pinned to a hex that would
disappear on one of them.
"""

from __future__ import annotations

import streamlit as st

# The five semantic tones. Chosen for adequate contrast against both a near-white and a
# near-black surface, because the dashboard does not control which the viewer is using.
TONES: dict[str, str] = {
    "healthy": "#2E9E5B",
    "warning": "#D69A19",
    "critical": "#D6453D",
    "info": "#3C7FD4",
    "neutral": "#7E8A99",
}

# severity_rank -> tone. Rank 1 is the most severe: SEV-1 is Critical and SEV-5 is
# Informational, which is the opposite of the reading a traffic-light guess would give,
# so the mapping is stated explicitly against the real vocabulary.
SEVERITY_RANK_TONE: dict[int, str] = {
    1: "critical",
    2: "critical",
    3: "warning",
    4: "healthy",
    5: "info",
}

# machine_operational_state -> tone. 'idle' is neutral rather than a warning: a machine
# between jobs is not a fault, and colouring it amber would cry wolf on every shift break.
MACHINE_STATE_TONE: dict[str, str] = {
    "running": "healthy",
    "idle": "neutral",
    "setup": "info",
    "starved": "warning",
    "blocked": "warning",
    "down_unplanned": "critical",
    "down_planned": "info",
    "offline": "neutral",
}

# alert_status -> tone.
ALERT_STATUS_TONE: dict[str, str] = {
    "open": "warning",
    "acknowledged": "info",
    "escalated": "critical",
    "resolved": "healthy",
    "closed": "neutral",
    "suppressed": "neutral",
}

# delivery_status -> tone. 'sent' is information rather than success: the platform has
# handed the message to the provider and not yet been told it arrived, and §O21 keeps the
# two distinct precisely because that gap is where silent failures live.
DELIVERY_STATUS_TONE: dict[str, str] = {
    "queued": "neutral",
    "sent": "info",
    "delivered": "healthy",
    "failed": "critical",
    "bounced": "critical",
    "rejected": "critical",
}

# escalation_decision -> tone. Every suppression is neutral, not negative: a suppressed
# situation is the gate working, not a failure.
ESCALATION_TONE: dict[str, str] = {
    "escalated": "critical",
    "suppressed_below_threshold": "neutral",
    "suppressed_duplicate": "neutral",
    "suppressed_maintenance_in_progress": "neutral",
    "suppressed_rate_limited": "neutral",
    "suppressed_insufficient_data": "neutral",
}


def tone_colour(tone: str) -> str:
    """The hex for a semantic tone, neutral for anything unrecognised."""
    return TONES.get(tone, TONES["neutral"])


def severity_colour(
    display_color_hex: str | None,
    severity_rank: int | None = None,
) -> str:
    """The colour for one severity band.

    Master data first: ``display_color_hex`` is the agreed colour for the band and is used
    as given. The rank fallback exists so a database without that column still renders
    with meaningful rather than arbitrary colour.
    """
    candidate = (display_color_hex or "").strip()
    if candidate.startswith("#") and len(candidate) in (4, 7):
        return candidate
    return tone_colour(SEVERITY_RANK_TONE.get(severity_rank or 0, "neutral"))


# The stylesheet. Deliberately short: a handful of reusable classes rather than per-page
# rules, so the visual language cannot drift page to page.
#
# `color-mix(in srgb, X 6%, transparent)` gives a wash of the accent over whatever the
# theme's own surface is. It degrades to no background on older engines, which loses
# decoration and keeps every word readable -- the right way round for a failure mode.
_CSS = """
<style>
:root {
  --ff-radius: 10px;
  --ff-border: color-mix(in srgb, currentColor 14%, transparent);
  --ff-surface: color-mix(in srgb, currentColor 4%, transparent);
  --ff-muted: color-mix(in srgb, currentColor 62%, transparent);
}

/* Give the app room to breathe without going edge to edge on a large monitor. */
.block-container { padding-top: 2.1rem; max-width: 1500px; }

/* ---------------------------------------------------------------- masthead */
.ff-masthead {
  display: flex; align-items: baseline; gap: 0.85rem; flex-wrap: wrap;
  padding-bottom: 0.9rem; margin-bottom: 1.35rem;
  border-bottom: 1px solid var(--ff-border);
}
.ff-masthead h1 {
  font-size: 1.72rem; font-weight: 680; letter-spacing: -0.022em;
  margin: 0; padding: 0; line-height: 1.15;
}
.ff-masthead .ff-sub {
  font-size: 0.94rem; color: var(--ff-muted); font-weight: 420;
}

/* ------------------------------------------------------------------- cards */
.ff-card {
  border: 1px solid var(--ff-border); border-radius: var(--ff-radius);
  background: var(--ff-surface); padding: 1.05rem 1.15rem; height: 100%;
}
.ff-card--accent { border-left: 3px solid var(--ff-accent, var(--ff-border)); }

.ff-kpi-label {
  font-size: 0.7rem; font-weight: 620; letter-spacing: 0.075em;
  text-transform: uppercase; color: var(--ff-muted);
}
.ff-kpi-value {
  font-size: 2.05rem; font-weight: 660; line-height: 1.1;
  letter-spacing: -0.03em; margin-top: 0.32rem;
  font-variant-numeric: tabular-nums;
}
.ff-kpi-note { font-size: 0.78rem; color: var(--ff-muted); margin-top: 0.28rem; }

/* ------------------------------------------------------------------ badges */
.ff-badge {
  display: inline-flex; align-items: center; gap: 0.42rem;
  font-size: 0.755rem; font-weight: 600; letter-spacing: 0.012em;
  padding: 0.2rem 0.62rem; border-radius: 999px; white-space: nowrap;
  border: 1px solid color-mix(in srgb, var(--ff-tone) 42%, transparent);
  background: color-mix(in srgb, var(--ff-tone) 13%, transparent);
}
.ff-badge .ff-dot {
  width: 0.5rem; height: 0.5rem; border-radius: 50%;
  background: var(--ff-tone); flex: 0 0 auto;
}

/* --------------------------------------------------------------- field grid */
.ff-fields { display: flex; flex-wrap: wrap; gap: 1.5rem 2.4rem; }
.ff-field { min-width: 8.5rem; }
.ff-field .ff-k {
  font-size: 0.685rem; font-weight: 620; letter-spacing: 0.075em;
  text-transform: uppercase; color: var(--ff-muted);
}
.ff-field .ff-v {
  font-size: 1.02rem; font-weight: 560; margin-top: 0.22rem;
  font-variant-numeric: tabular-nums;
}
.ff-field .ff-raw {
  font-size: 0.715rem; color: var(--ff-muted); margin-top: 0.1rem;
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace;
}

/* --------------------------------------------------------------- prose block */
/* max-width in `ch` caps the measure at roughly 80 characters. Generated reasoning runs to
   several hundred words, and a line that spans a 1920-pixel monitor is measurably harder to
   read than one that stops; the cap costs nothing on a narrow window because it is a
   maximum rather than a width. */
.ff-prose {
  font-size: 0.95rem; line-height: 1.62; max-width: 80ch;
  border-left: 2px solid var(--ff-border); padding: 0.15rem 0 0.15rem 0.95rem;
}

/* ------------------------------------------------------------ section titles */
.ff-section {
  font-size: 0.735rem; font-weight: 660; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--ff-muted);
  margin: 1.6rem 0 0.65rem; padding-bottom: 0.32rem;
  border-bottom: 1px solid var(--ff-border);
}

/* ------------------------------------------------------------- sidebar nav */
/* A group label, not a section rule: the sidebar is narrow and a full-width border under
   every heading would fragment it into stripes. */
.ff-navgroup {
  font-size: 0.655rem; font-weight: 680; letter-spacing: 0.11em;
  text-transform: uppercase; color: var(--ff-muted);
  margin: 0.95rem 0 0.3rem; padding: 0 0.1rem;
}
.ff-navgroup:first-of-type { margin-top: 0.15rem; }

/* Make sidebar buttons read as navigation rather than as a stack of form controls:
   text left-aligned against the icon, and a quieter resting state. Streamlit's own
   primary/secondary treatment still carries the active page. */
section[data-testid="stSidebar"] .stButton > button {
  justify-content: flex-start; font-weight: 520; text-align: left;
}

/* --------------------------------------------------------- compact metadata */
/* Reference identifiers, several to a row. Denser than the field grid because an ID needs
   no headline treatment -- it needs to be findable and copyable. */
.ff-meta { display: flex; flex-wrap: wrap; gap: 0.5rem 1.5rem; }
.ff-meta .ff-pair { display: flex; align-items: baseline; gap: 0.42rem; }
.ff-meta .ff-k {
  font-size: 0.665rem; font-weight: 620; letter-spacing: 0.07em;
  text-transform: uppercase; color: var(--ff-muted); white-space: nowrap;
}
.ff-meta .ff-v {
  font-size: 0.85rem; font-weight: 560;
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace;
}

/* ------------------------------------------------------------- traceability */
.ff-chain { display: flex; align-items: stretch; flex-wrap: wrap; gap: 0.4rem; }
.ff-link {
  border: 1px solid var(--ff-border); border-radius: 8px;
  background: var(--ff-surface); padding: 0.5rem 0.8rem; min-width: 9.4rem;
}
.ff-link .ff-k {
  font-size: 0.645rem; font-weight: 620; letter-spacing: 0.07em;
  text-transform: uppercase; color: var(--ff-muted);
}
.ff-link .ff-v {
  font-size: 0.85rem; font-weight: 600; margin-top: 0.16rem;
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace;
}
.ff-arrow {
  display: flex; align-items: center; color: var(--ff-muted);
  font-size: 1rem; padding: 0 0.1rem;
}

/* Monospace for identifiers, so codes line up and are obviously identifiers. */
.ff-code {
  font-family: ui-monospace, "SFMono-Regular", "Cascadia Mono", monospace;
  font-size: 0.88rem;
}
.ff-muted { color: var(--ff-muted); }
.ff-empty {
  border: 1px dashed var(--ff-border); border-radius: var(--ff-radius);
  padding: 1.5rem; text-align: center; color: var(--ff-muted);
  font-size: 0.9rem;
}

/* Tighten Streamlit's own furniture just enough to look deliberate. */
[data-testid="stSidebarNav"] { display: none; }
[data-testid="stMetricValue"] { font-variant-numeric: tabular-nums; }
div[data-testid="stExpander"] details { border-radius: var(--ff-radius); }
</style>
"""


def inject_css() -> None:
    """Install the stylesheet once per session run.

    Called at the top of :mod:`dashboard.app` before any page renders, so every component
    can assume the classes exist.
    """
    st.markdown(_CSS, unsafe_allow_html=True)
