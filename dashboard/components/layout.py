"""The dashboard's visual vocabulary: masthead, cards, badges, field grids, chain.

Every page draws from this module rather than writing its own markup, which is what keeps
one look across seven pages. No function here reads the database; each takes plain values
and returns or renders markup.

**Everything from the database is escaped.** Recommendation text, machine names and
rationales are all free text, and one stray angle bracket in a model's output would
otherwise break the page layout or inject markup. :func:`_esc` is applied at every
insertion point.
"""

from __future__ import annotations

from html import escape
from typing import Any, Iterable, Sequence

import streamlit as st

from dashboard.styles.theme import (
    ALERT_STATUS_TONE,
    DELIVERY_STATUS_TONE,
    ESCALATION_TONE,
    MACHINE_STATE_TONE,
    severity_colour,
    tone_colour,
)


def _esc(value: Any) -> str:
    """Text safe to place inside the markup, with a visible dash for absent values."""
    if value is None or value == "":
        return "&mdash;"
    return escape(str(value))


# ------------------------------------------------------------------- masthead


def masthead(title: str, subtitle: str, meta: str | None = None) -> None:
    """The product header. One per page, always the same shape."""
    extra = "" if not meta else (
        '<span class="ff-sub ff-code" style="margin-left:auto">%s</span>' % _esc(meta))
    st.markdown(
        '<div class="ff-masthead"><h1>%s</h1>'
        '<span class="ff-sub">%s</span>%s</div>'
        % (_esc(title), _esc(subtitle), extra),
        unsafe_allow_html=True,
    )


def section(title: str) -> None:
    """A quiet divider with a label. Used instead of ``st.subheader`` for hierarchy."""
    st.markdown('<div class="ff-section">%s</div>' % _esc(title),
                unsafe_allow_html=True)


# ---------------------------------------------------------------------- badges


def badge(text: str, tone: str = "neutral", colour: str | None = None) -> str:
    """A pill with a coloured dot. Returns markup so it can be embedded in a card.

    The dot carries the colour and the text inherits the theme's own foreground, so a badge
    stays readable on a light or a dark background whatever hue it is given.
    """
    return ('<span class="ff-badge" style="--ff-tone:%s">'
            '<span class="ff-dot"></span>%s</span>'
            % (colour or tone_colour(tone), _esc(text)))


def severity_badge(severity: dict[str, Any] | None, with_name: bool = True) -> str:
    """A severity pill coloured by ``failure_severity_level.display_color_hex``."""
    if not severity or not severity.get("code"):
        return badge("severity unknown", "neutral")
    label = severity["code"]
    if with_name and severity.get("name"):
        label = "%s %s" % (severity["code"], severity["name"])
    return badge(label, colour=severity_colour(severity.get("colour"),
                                               severity.get("rank")))


def status_badge(value: str | None, kind: str) -> str:
    """A pill for one of the platform's status vocabularies.

    ``kind`` selects the mapping: ``alert``, ``delivery``, ``machine`` or ``escalation``.
    An unrecognised value still renders, in neutral, rather than being dropped -- a
    vocabulary that grows should not make a row invisible.
    """
    if not value:
        return badge("not recorded", "neutral")
    table = {
        "alert": ALERT_STATUS_TONE,
        "delivery": DELIVERY_STATUS_TONE,
        "machine": MACHINE_STATE_TONE,
        "escalation": ESCALATION_TONE,
    }.get(kind, {})
    return badge(str(value).replace("_", " "), table.get(str(value), "neutral"))


# ----------------------------------------------------------------------- cards


def card(body: str, accent: str | None = None) -> None:
    """Render arbitrary markup inside the standard card frame."""
    style = "" if accent is None else ' style="--ff-accent:%s"' % accent
    klass = "ff-card ff-card--accent" if accent else "ff-card"
    st.markdown('<div class="%s"%s>%s</div>' % (klass, style, body),
                unsafe_allow_html=True)


def kpi_row(items: Sequence[dict[str, Any]], columns: int | None = None) -> None:
    """A row of KPI cards.

    Each item takes ``label``, ``value`` and optionally ``note``, ``tone`` or ``colour``.
    Laid out with ``st.columns`` so the row reflows rather than scrolling sideways on a
    narrow window.
    """
    if not items:
        return
    width = columns or len(items)
    for start in range(0, len(items), width):
        chunk = items[start:start + width]
        for column, item in zip(st.columns(len(chunk), gap="small"), chunk):
            accent = item.get("colour") or (
                tone_colour(item["tone"]) if item.get("tone") else None)
            note = ('<div class="ff-kpi-note">%s</div>' % _esc(item["note"])
                    if item.get("note") else "")
            with column:
                card(
                    '<div class="ff-kpi-label">%s</div>'
                    '<div class="ff-kpi-value">%s</div>%s'
                    % (_esc(item["label"]), _esc(item["value"]), note),
                    accent=accent,
                )


# ------------------------------------------------------------------ field grid


def field_grid(fields: Iterable[dict[str, Any]], framed: bool = True) -> None:
    """A block of label/value pairs, optionally with the raw value beneath.

    ``raw`` is the point of this component. The platform's contract is that a display
    string and the stored value travel together, so a field can show ``2.00 mm/s`` to an
    operator and ``2.0025`` to whoever has to reconcile it, from one source.
    """
    blocks: list[str] = []
    for item in fields:
        if item is None:
            continue
        raw = item.get("raw")
        raw_markup = ""
        if raw not in (None, ""):
            raw_markup = '<div class="ff-raw">raw %s</div>' % _esc(raw)
        blocks.append(
            '<div class="ff-field"><div class="ff-k">%s</div>'
            '<div class="ff-v">%s</div>%s</div>'
            % (_esc(item.get("label")), item.get("html") or _esc(item.get("value")),
               raw_markup))
    if not blocks:
        return
    grid = '<div class="ff-fields">%s</div>' % "".join(blocks)
    if framed:
        card(grid)
    else:
        st.markdown(grid, unsafe_allow_html=True)


def meta_row(pairs: Iterable[tuple[str, Any]], framed: bool = False) -> None:
    """Reference identifiers, several to a row, in a compact label/value pairing.

    Deliberately denser than :func:`field_grid`. An identifier is looked up and copied
    rather than read as a headline, so giving five of them the same visual weight as a
    failure probability would flatten the page's hierarchy -- which is exactly what made the
    Evidence page hard to scan. Absent links render as a dash rather than disappearing, so
    a gap in the chain stays visible.
    """
    blocks = [
        '<div class="ff-pair"><span class="ff-k">%s</span>'
        '<span class="ff-v">%s</span></div>' % (_esc(label), _esc(value))
        for label, value in pairs
    ]
    if not blocks:
        return
    markup = '<div class="ff-meta">%s</div>' % "".join(blocks)
    if framed:
        card(markup)
    else:
        st.markdown(markup, unsafe_allow_html=True)


def prose(text: str | None, placeholder: str = "Not recorded.") -> None:
    """A block of generated text, shown whole.

    Newlines are converted to breaks so the Decision Agent's paragraphing survives, and the
    text is escaped first so its content cannot introduce markup.
    """
    body = (text or "").strip()
    if not body:
        st.markdown('<div class="ff-prose ff-muted">%s</div>' % _esc(placeholder),
                    unsafe_allow_html=True)
        return
    st.markdown('<div class="ff-prose">%s</div>'
                % escape(body).replace("\n\n", "<br><br>").replace("\n", "<br>"),
                unsafe_allow_html=True)


def empty(message: str, hint: str | None = None) -> None:
    """The empty state. Every list and chart on every page routes through this."""
    extra = "" if not hint else '<br><span style="font-size:0.82rem">%s</span>' % _esc(hint)
    st.markdown('<div class="ff-empty">%s%s</div>' % (_esc(message), extra),
                unsafe_allow_html=True)


# ----------------------------------------------------------------- traceability


def traceability_chain(chain: dict[str, Any]) -> None:
    """Prediction -> Alert -> Context -> Recommendation -> Notification, as it exists.

    A link with no value renders as absent rather than being skipped, so the chain shows
    where the trail actually stops instead of implying a shorter pipeline.
    """
    notifications = chain.get("notifications") or []
    if len(notifications) == 1:
        notification_value = notifications[0]
    elif notifications:
        notification_value = "%s +%d more" % (notifications[0], len(notifications) - 1)
    else:
        notification_value = None

    links = [
        ("Prediction", chain.get("prediction")),
        ("Alert", chain.get("alert")),
        ("Context", chain.get("context")),
        ("Recommendation", chain.get("recommendation")),
        ("Notification", notification_value),
    ]
    parts: list[str] = []
    for index, (label, value) in enumerate(links):
        if index:
            parts.append('<div class="ff-arrow">&rarr;</div>')
        parts.append(
            '<div class="ff-link"><div class="ff-k">%s</div>'
            '<div class="ff-v">%s</div></div>' % (_esc(label), _esc(value)))
    st.markdown('<div class="ff-chain">%s</div>' % "".join(parts),
                unsafe_allow_html=True)


def pipeline_flow(stages: Sequence[dict[str, Any]]) -> None:
    """The pipeline as a labelled chain of real counts.

    This is the thirty-second explanation of the platform: readings become events, events
    become alerts, alerts become predictions, predictions are escalated or suppressed,
    escalations become reasoning, reasoning becomes a message. Each figure is a row count
    from the database, so the strip doubles as proof the stage actually ran.
    """
    parts: list[str] = []
    for index, stage in enumerate(stages):
        if index:
            parts.append('<div class="ff-arrow">&rarr;</div>')
        tone = stage.get("tone")
        accent = ('style="border-left:3px solid %s"' % tone_colour(tone)) if tone else ""
        parts.append(
            '<div class="ff-link" %s><div class="ff-k">%s</div>'
            '<div class="ff-v">%s</div></div>'
            % (accent, _esc(stage.get("label")), _esc(stage.get("value"))))
    st.markdown('<div class="ff-chain">%s</div>' % "".join(parts),
                unsafe_allow_html=True)
