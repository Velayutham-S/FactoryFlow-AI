"""FactoryFlow AI dashboard entry point.

Run it with::

    streamlit run dashboard/app.py

Point it at a specific database with the ``FACTORYFLOW_DB`` environment variable; with
none set it finds the largest SQLite database in the repository, which is the populated
platform database.

**Navigation is controlled here rather than by Streamlit's ``pages/`` convention.** Native
multipage routing gives each page its own URL and its own top-level script, which makes
cross-page drill-down awkward: "open the machine behind this alert" has to smuggle state
through query parameters. The dashboard's value is in those jumps -- alert to machine,
prediction to recommendation, recommendation to evidence -- so a single entry point with an
explicit selection in ``st.session_state`` is the simpler mechanism and the sidebar stays
entirely ours to design.

**The navigation control is a column of buttons, and that is a correctness decision rather
than a stylistic one.** A ``radio`` with ``key="page"`` puts the current page *in a widget
key*, and Streamlit forbids assigning to a widget key once the widget exists in the current
run. Every cross-page button on this dashboard is rendered after the sidebar, so each one
would raise ``StreamlitAPIException`` the moment it tried to change page. Buttons hold no
persistent selection, so there is no key for navigation to collide with; the current page
lives in plain state under ``nav_page``. See :mod:`dashboard.views` for the full rule.

**Read-only.** Nothing on any page writes a row, calls an agent, loads a model or contacts
a provider. There is no send button, by design for this phase.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any

import streamlit as st

# Running `streamlit run dashboard/app.py` puts `dashboard/` on sys.path, not the
# repository root, so the sibling backend packages would not import. Prepending the root
# is what lets this file reuse `models` and `notification` rather than duplicating them.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dashboard.components.layout import empty  # noqa: E402
from dashboard.services import (  # noqa: E402
    DatabaseUnavailable,
    describe_database,
    load_filter_options,
    resolve_database_path,
)
from dashboard.styles.theme import inject_css, tone_colour  # noqa: E402
from dashboard.views import (  # noqa: E402
    ALL,
    alerts,
    current_page,
    evidence,
    machines,
    navigate,
    notifications,
    overview,
    predictions,
    recommendations,
)

LOG = logging.getLogger("factoryflow.dashboard")

PAGES: dict[str, Any] = {
    "Overview": overview,
    "Machines": machines,
    "Alerts": alerts,
    "Predictions": predictions,
    "AI Recommendations": recommendations,
    "Notifications": notifications,
    "Evidence": evidence,
}

# Icons chosen to say what the page is about, not to decorate it.
PAGE_ICONS: dict[str, str] = {
    "Overview": ":material/space_dashboard:",
    "Machines": ":material/precision_manufacturing:",
    "Alerts": ":material/notification_important:",
    "Predictions": ":material/insights:",
    "AI Recommendations": ":material/lightbulb:",
    "Notifications": ":material/send:",
    "Evidence": ":material/account_tree:",
}

# The sidebar, grouped by the question the operator is asking rather than as a flat list.
# The order follows the pipeline: what is happening now, what the platform concluded, who was
# told, and how to prove it.
NAV_GROUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Operations", ("Overview", "Machines")),
    ("Intelligence", ("Alerts", "Predictions", "AI Recommendations")),
    ("Communication", ("Notifications",)),
    ("Traceability", ("Evidence",)),
)

# A page missing from the groups would be unreachable, and a group naming a page that does
# not exist would render a dead button. Neither is a failure worth discovering in a browser,
# so it is caught at import.
_grouped = tuple(name for _, names in NAV_GROUPS for name in names)
assert set(_grouped) == set(PAGES), (
    "sidebar groups and PAGES disagree: %s" % sorted(set(_grouped) ^ set(PAGES)))
assert len(_grouped) == len(PAGES), "a page appears in more than one sidebar group"

FILTER_KEYS = ("f_machine", "f_line", "f_severity", "f_category",
               "f_alert_status", "f_decision", "f_delivery")


def main() -> None:
    st.set_page_config(
        page_title="FactoryFlow AI",
        page_icon=":material/precision_manufacturing:",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()

    try:
        db_path = str(resolve_database_path())
        database = describe_database(db_path)
        options = load_filter_options(db_path)
    except DatabaseUnavailable as exc:
        _fatal(exc)
        return
    except Exception as exc:  # noqa: BLE001 -- the screen must not show a traceback
        LOG.exception("dashboard could not start")
        _fatal(DatabaseUnavailable(
            "FactoryFlow AI could not read its database."), detail=str(exc))
        return

    page = current_page(list(PAGES))
    filters = _sidebar(page, database, options)

    # No try/except around the render. A page that cannot draw is a defect to fix, not a
    # condition to report, and swallowing it here is what previously turned a navigation
    # bug into a permanent red banner. The one guard that remains is for a database that
    # disappears mid-session, which is an environment fault rather than a code fault.
    try:
        PAGES[page].render(db_path, filters, database)
    except DatabaseUnavailable as exc:
        st.error("%s" % exc, icon=":material/database_off:")
        st.caption("Use Refresh data once the database is reachable again.")


# --------------------------------------------------------------------- sidebar


def _sidebar(
    page: str,
    database: dict[str, Any],
    options: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    with st.sidebar:
        st.markdown(
            '<div style="display:flex;align-items:center;gap:0.55rem;'
            'padding:0.15rem 0 0.9rem">'
            '<span style="width:0.62rem;height:0.62rem;border-radius:50%%;'
            'background:%s;display:inline-block"></span>'
            '<span style="font-size:1.13rem;font-weight:680;letter-spacing:-0.02em">'
            'FactoryFlow AI</span></div>'
            '<div style="font-size:0.76rem;line-height:1.45;margin:-0.55rem 0 1rem" '
            'class="ff-muted">Predictive Maintenance Intelligence</div>'
            % tone_colour("healthy"),
            unsafe_allow_html=True)

        _navigation(page)
        st.divider()
        filters = _filters(options)

        st.divider()
        # on_click, not an inline branch: the callback runs before the next script, which is
        # the only moment a widget key may be written. Clearing the cache here would be
        # legal either way, but keeping both actions in the callback keeps one rule.
        st.button("Refresh data", width="stretch", icon=":material/refresh:",
                  on_click=_refresh, key="nav_refresh")
        st.caption(
            "Reads are cached briefly and refreshed on demand. Refreshing re-queries the "
            "database only — it never runs the simulator, trains a model, calls Groq or "
            "sends a notification.")

        st.divider()
        st.markdown(
            '<div class="ff-muted" style="font-size:0.74rem;line-height:1.6">'
            '<b>Database</b><br><span class="ff-code">%s</span><br>%s MB'
            '%s</div>'
            % (database.get("name"), database.get("size_mb"),
               "<br>%s · %s" % (database.get("plant_code") or "",
                                database.get("timezone") or "")
               if database.get("plant_code") else ""),
            unsafe_allow_html=True)
        st.caption("Read-only session. No page in this dashboard writes to the database.")
    return filters


def _navigation(page: str) -> None:
    """The page list, grouped by what the operator is doing, as buttons.

    The grouping is the point. Seven flat entries make the reader work out for themselves
    that Alerts, Predictions and AI Recommendations are one train of thought while
    Notifications is the end of it. Naming the groups says so.

    Buttons rather than a selection widget, unchanged from before and for the same reason:
    the current page must live in plain state so any control anywhere on the screen can
    change it. The active page is a primary button, which is how the current location stays
    obvious without a widget holding it.
    """
    for group, names in NAV_GROUPS:
        st.markdown('<div class="ff-navgroup">%s</div>' % group,
                    unsafe_allow_html=True)
        for name in names:
            if st.button(name, key="nav_%s" % name.replace(" ", "_").lower(),
                         icon=PAGE_ICONS.get(name), width="stretch",
                         type="primary" if name == page else "secondary"):
                navigate(name)


def _refresh() -> None:
    """Drop cached reads. Runs as a callback, so it cannot collide with widget state."""
    st.cache_data.clear()


def _clear_filters() -> None:
    """Reset every filter to All.

    A callback, deliberately. These are ``selectbox`` keys, and assigning to them from the
    button's own branch -- after the widgets have been instantiated -- is exactly the
    illegal pattern that broke navigation. Callbacks run before the next script, so this is
    the supported way to reset a widget.
    """
    for key in FILTER_KEYS:
        st.session_state[key] = ALL


def _filters(options: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    """Global filters, every vocabulary read from the database.

    Nothing here is a literal list of machines, severities or categories. If master data
    gains a severity band or the plant gains a line, these controls gain the option without
    a code change.
    """
    st.markdown('<div class="ff-section" style="margin-top:0">Filters</div>',
                unsafe_allow_html=True)

    machine_codes = [ALL] + [m["code"] for m in options["machines"]]
    severities = [ALL] + [s["code"] for s in options["severities"] if s["code"]]
    categories = [ALL] + [c["name"] for c in options["categories"]]
    lines = [ALL] + [line["code"] for line in options["lines"]]
    alert_statuses = [ALL] + [s["code"] for s in options["alert_statuses"]]
    delivery_statuses = [ALL] + [s["code"] for s in options["delivery_statuses"]]
    decisions = [ALL] + [d["code"] for d in options["escalation_decisions"]]

    filters = {
        "machine": st.selectbox("Machine", machine_codes, key="f_machine"),
        "line": st.selectbox("Production line", lines, key="f_line"),
        "severity": st.selectbox("Severity", severities, key="f_severity",
                                 help="SEV-1 is the most severe band in this plant's "
                                      "vocabulary; SEV-5 is informational."),
        "category": st.selectbox("Failure category", categories, key="f_category"),
    }
    with st.expander("More filters"):
        filters["alert_status"] = st.selectbox("Alert status", alert_statuses,
                                              key="f_alert_status")
        filters["escalation_decision"] = st.selectbox(
            "Escalation verdict", decisions, key="f_decision")
        filters["delivery_status"] = st.selectbox(
            "Delivery status", delivery_statuses, key="f_delivery")

    active = [name for name, value in filters.items() if value not in (None, ALL)]
    if active:
        st.button("Clear filters", width="stretch", on_click=_clear_filters,
                  key="nav_clear_filters", icon=":material/filter_alt_off:")
        st.caption("%d filter(s) active." % len(active))
    return filters


# ------------------------------------------------------------------------ fatal


def _fatal(exc: DatabaseUnavailable, detail: str | None = None) -> None:
    """The one screen shown when there is no database to read.

    A clear sentence and what to do about it, rather than a traceback. The technical detail
    is available behind a disclosure for whoever is developing.
    """
    st.markdown(
        '<div class="ff-masthead"><h1>FactoryFlow AI</h1>'
        '<span class="ff-sub">AI-Powered Predictive Maintenance &amp; Decision Support'
        '</span></div>', unsafe_allow_html=True)
    st.error("FactoryFlow AI database is unavailable.", icon=":material/database_off:")
    st.markdown(str(exc))
    empty("Nothing can be displayed without a database.",
          "Set FACTORYFLOW_DB to the SQLite file, or run the pipeline once to create one.")
    if detail:
        with st.expander("Technical detail"):
            st.code(detail, language=None)


main()
