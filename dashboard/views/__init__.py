"""The seven pages, plus the navigation, selection and filter helpers they share.

**One navigation pattern, and the reason it looks like this.**

Streamlit forbids assigning to ``st.session_state[k]`` once a widget with key ``k`` has been
instantiated in the current run. Navigation therefore cannot be driven by writing to the key
of the widget that displays it: a "open the machine behind this alert" button is rendered
*after* the sidebar, so writing the sidebar widget's key from that button raises
``StreamlitAPIException``.

The rule this package follows, everywhere, without exception:

* The current page lives in :data:`NAV_KEY` (``nav_page``) and the current drill-down
  selections live in :data:`SELECTION_KEYS`. **None of those is ever a widget key.**
* The sidebar navigation is built from :func:`streamlit.button`, which holds no persistent
  selection of its own, so there is no widget key for navigation to collide with.
* Anything that wants to move the user calls :func:`navigate`, which writes only plain state
  and then reruns.
* A widget key is only ever written from an ``on_click`` / ``on_change`` callback, which
  Streamlit runs *before* the next script executes, or from :func:`seed_widget` before the
  widget is created. Both are legal; assigning mid-render is not.

Filtering happens here rather than in SQL. The service layer returns whole, already-cached
collections -- eight machines, sixteen alerts, three predictions -- and narrowing them in
Python keeps one cache entry per table instead of one per filter combination. That is the
right trade at this size; a table that grew past a few thousand rows would want the
predicate pushed into the query instead.
"""

from __future__ import annotations

from typing import Any, Sequence

import streamlit as st

ALL = "All"

# The authoritative page. A plain state key, deliberately not the key of any widget.
NAV_KEY = "nav_page"

DEFAULT_PAGE = "Overview"

# Drill-down state, one key per entity. Also plain state keys, never widget keys.
SELECTION_KEYS = (
    "selected_machine",
    "selected_alert",
    "selected_prediction",
    "selected_recommendation",
    "selected_notification",
)

# Which selection each page owns, so opening a page cannot inherit an unrelated one.
PAGE_SELECTION: dict[str, str] = {
    "Machines": "selected_machine",
    "Alerts": "selected_alert",
    "Predictions": "selected_prediction",
    "AI Recommendations": "selected_recommendation",
    "Evidence": "selected_recommendation",
    "Notifications": "selected_notification",
}


# ------------------------------------------------------------------- navigation


def current_page(pages: Sequence[str]) -> str:
    """The page to render, defaulting sanely and surviving a browser refresh.

    A value not in ``pages`` -- stale state from an older build, or a hand-edited URL --
    falls back to the default rather than raising.
    """
    page = st.session_state.get(NAV_KEY)
    if page not in pages:
        page = DEFAULT_PAGE if DEFAULT_PAGE in pages else pages[0]
        st.session_state[NAV_KEY] = page
    return str(page)


def selection(name: str) -> Any:
    """The current drill-down selection for one entity, or ``None``."""
    return st.session_state.get(name)


def clear_selections(keep: str | None = None) -> None:
    """Drop every drill-down selection except the one the target page owns.

    Without this, opening Alerts after having opened a machine would leave a machine
    selected, and going back to Machines would land on a detail view the user never asked
    for.
    """
    for key in SELECTION_KEYS:
        if key != keep:
            st.session_state[key] = None


def navigate(page: str, **selections: Any) -> None:
    """Move to ``page``, carrying zero or more selections, then rerun.

    The only way anything in this dashboard changes page. It writes
    :data:`NAV_KEY` and the selection keys -- all plain state -- so it is safe to call from
    a button handler rendered anywhere on the screen, which is precisely what the previous
    design got wrong.
    """
    st.session_state[NAV_KEY] = page
    clear_selections(keep=PAGE_SELECTION.get(page))
    for key, value in selections.items():
        st.session_state[key] = value
    st.rerun()


def open_detail(name: str, value: Any) -> None:
    """Open a detail view on the current page by setting its selection and rerunning."""
    st.session_state[name] = value
    st.rerun()


def close_detail(name: str) -> None:
    """Return from a detail view to its list."""
    st.session_state[name] = None
    st.rerun()


# ------------------------------------------------------------------ widget state


def seed_widget(key: str, value: Any, options: Sequence[Any]) -> None:
    """Point a keyed selection widget at ``value`` *before* the widget is created.

    Two jobs, both about correctness rather than convenience:

    1. Honour a cross-page selection. A keyed ``selectbox`` remembers its own value and
       ignores ``index=`` on later runs, so arriving from another page with a specific
       record would otherwise show whatever was chosen last time.
    2. Repair a stale value. When the option list changes -- a different machine's
       parameters, a filter vocabulary that grew -- a remembered value that is no longer in
       ``options`` must be dropped, or the widget is constructed against an option it cannot
       display.

    Assigning here is legal because it happens before instantiation. Callers must invoke it
    ahead of the widget it seeds.
    """
    stored = st.session_state.get(key)
    if stored is not None and stored not in options:
        st.session_state.pop(key, None)
        stored = None
    if value is not None and value in options and stored != value:
        st.session_state[key] = value


# ---------------------------------------------------------------------- filters


def selected(filters: dict[str, Any], key: str) -> str | None:
    """The chosen value for one filter, or ``None`` when it is unrestricted."""
    value = filters.get(key)
    return None if value in (None, ALL, "") else str(value)


def keep(row: dict[str, Any], filters: dict[str, Any], mapping: dict[str, str]) -> bool:
    """Whether one row survives the active filters.

    ``mapping`` maps a filter name to the row key it tests. A row missing the key is kept:
    a filter should narrow what it can speak about, not silently delete rows it has no
    opinion on.
    """
    for filter_name, row_key in mapping.items():
        wanted = selected(filters, filter_name)
        if wanted is None:
            continue
        actual = row.get(row_key)
        if isinstance(actual, dict):
            actual = actual.get("code")
        if actual is None:
            continue
        if str(actual) != wanted:
            return False
    return True


def apply_filters(
    rows: Sequence[dict[str, Any]],
    filters: dict[str, Any],
    mapping: dict[str, str],
) -> list[dict[str, Any]]:
    return [row for row in rows if keep(row, filters, mapping)]


def search(rows: Sequence[dict[str, Any]], term: str,
           fields: Sequence[str]) -> list[dict[str, Any]]:
    """Free-text narrowing over the named fields. Case-insensitive substring match."""
    needle = (term or "").strip().lower()
    if not needle:
        return list(rows)
    kept: list[dict[str, Any]] = []
    for row in rows:
        for field in fields:
            value = row.get(field)
            if value is not None and needle in str(value).lower():
                kept.append(row)
                break
    return kept
