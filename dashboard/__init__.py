"""The FactoryFlow AI dashboard: a read-only Streamlit view over the existing platform.

This package adds no domain logic. It reads persisted rows through the ORM in
:mod:`models`, formats them with the presentation authority already published by
:mod:`notification.compose`, and draws them. Nothing here predicts, reasons, decides or
delivers.

**Read-only by construction, not by convention.** The package deliberately imports only
:mod:`models` and :mod:`notification.compose`. It does not import
:mod:`notification.notifier`, :mod:`notification.whatsapp`, :mod:`decision.agent`,
:mod:`prediction.agent`, :mod:`monitoring.agent` or :mod:`supervisor.orchestrator`, so no
code path exists from a page render to an LLM call, a model fit, a simulator tick or a
WhatsApp send. A page refresh cannot trigger any of them because the functions that do
them are not reachable from here.

Run it with::

    streamlit run dashboard/app.py
"""

__all__ = ["__doc__"]
