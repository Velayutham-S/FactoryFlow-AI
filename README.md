# FactoryFlow AI

AI-powered predictive maintenance and industrial decision support for a single
manufacturing plant.

The platform watches machine telemetry, correlates what it detects into alerts, scores the
risk of failure, decides which situations are worth a human's attention, reasons about those
with an LLM, and delivers the result to the engineer who has to act on it.

```
Factory Simulator → Monitoring Agent → Prediction Agent → Escalation Gate
                 → Decision Agent → Recommendation → Notification Service
                 → WhatsApp Cloud API → Final Response
```

Every stage writes its own rows, so the chain from a sensor reading to the message on an
engineer's phone is reconstructable after the fact:

```
Prediction → Alert → Supervisor Context → Recommendation → Notification
```

## Running the dashboard

```
streamlit run dashboard/app.py
```

That is the whole command. There is no API server, no separate frontend build and no
container to start; Streamlit imports the existing Python packages directly.

By default the dashboard finds the largest SQLite database in the repository. Point it at a
specific file when you have more than one:

```
set FACTORYFLOW_DB=E:\path\to\factoryflow.db        REM Windows
export FACTORYFLOW_DB=/path/to/factoryflow.db       # macOS / Linux
```

### What you need

* Python 3.11
* The packages the backend already uses, plus Streamlit: `streamlit`, `pandas`, `altair`,
  `sqlalchemy`, `scikit-learn`, `joblib`, `requests`, `python-dotenv`, `groq`
* A populated FactoryFlow database. The dashboard is read-only and will not create one; if
  it cannot find a database it says so and stops rather than showing an empty shell.
* No Groq or Meta credentials. The dashboard never calls either, so it runs without them.

### Pages

The sidebar groups them by the question being asked.

| Group | Page | What it answers |
|---|---|---|
| Operations | **Overview** | Is the plant healthy, what is the single most urgent situation, and did each pipeline stage run? |
| Operations | **Machines** | Per-asset state, live risk, and sensor trends against each parameter's declared healthy envelope |
| Intelligence | **Alerts** | Every correlated condition, its severity, and the escalation gate's recorded verdict |
| Intelligence | **Predictions** | Failure probability, horizon, model version and the feature attributions behind each score |
| Intelligence | **AI Recommendations** | The Decision Agent's diagnosis, action, recovery plan and reasoning, in full |
| Communication | **Notifications** | What was composed, what was suppressed and why, and what the provider accepted |
| Traceability | **Evidence** | The persisted basis for a decision, and the reference chain that ties it together |

Related records link to each other: an alert opens its prediction, a prediction opens its
recommendation, a recommendation opens its evidence and its notifications. Every link is a
foreign key that exists in the database, so a button appears only where it leads somewhere.

### Deployment and network binding

`.streamlit/config.toml` binds the server to `127.0.0.1`.

**The dashboard has no authentication.** It is read-only, so nothing can be altered through
it, but every machine, alert, prediction and recommendation in the plant is readable by
anyone who can reach the port. Streamlit's own default is to listen on all interfaces, which
is why the loopback binding is set explicitly rather than left implicit.

For shared or public deployment, place the Streamlit application behind an appropriate
authentication layer or an authenticated reverse proxy, and let that proxy reach
`127.0.0.1`. Do not simply widen the bind address.

Secrets never reach the dashboard: it does not read `.env`, and no credential name appears
anywhere in `dashboard/`. The WhatsApp destination and the Groq and Meta credentials are
used by the backend only.

## How data reaches the dashboard

```
SQLite (existing)
    │
    ▼
models/            existing SQLAlchemy ORM and session helpers — reused, not duplicated
    │
    ▼
dashboard/services/dashboard_service.py    read-only queries, cached, returns plain dicts
    │
    ▼
notification/compose.py                    existing formatting authority and dashboard_view()
    │
    ▼
dashboard/views/*                          seven pages
```

Two rules keep the dashboard honest:

**It reuses the platform's presentation authority.** Durations, timestamps, measurements,
prediction horizons and probability are formatted by `notification/compose.py` — the same
functions the WhatsApp message uses. The dashboard and the notification cannot disagree
about a number. Where a figure is formatted, the raw stored value is shown beside it.

**It is read-only.** No page writes a row, calls an agent, loads a model or contacts a
provider. Opening or refreshing the dashboard cannot run the simulator, train a model, call
Groq or send a WhatsApp message. Refresh re-queries the database and nothing else.

## Running the backend

Each phase has its own entry point and they run in order against one database file:

```
python -m master_data   <db> data/master     # create and seed master data
python -m factory_sim   <db> [hours] [seed]  # generate operational history
python -m monitoring    <db> [cycles]        # detect and correlate
python -m prediction    <db> train           # fit one model per machine category
python -m prediction    <db> predict         # score and record
python -m notification  <db> [cycles]        # supervise, reason, and deliver
```

`python -m notification` runs the Supervisor, which drives monitoring, prediction, the
escalation gate and the Decision Agent, then delivers the result. **It sends real WhatsApp
messages** when credentials are configured.

Configuration lives in `.env` and appears in no source file: `GROQ_API_KEY` for reasoning,
and the WhatsApp Cloud API credentials plus `WHATSAPP_PHONE_NUMBER` for delivery. A missing
value raises rather than recording a delivery that never happened.

## Repository layout

| Path | Contents |
|---|---|
| `models/` | The 53-table SQLAlchemy layer: master, operational and system groups |
| `master_data/` | CSV import and verification for the 29 master datasets |
| `factory_sim/` | The Factory Simulation Engine |
| `monitoring/` | The Monitoring Agent: detection and alert correlation |
| `prediction/` | The Prediction Agent: features, model, inference |
| `supervisor/` | Orchestration and the escalation gate |
| `decision/` | The Decision Agent: evidence, impact, assignment, reasoning |
| `notification/` | Composition, routing and WhatsApp delivery |
| `dashboard/` | The Streamlit dashboard (read-only) |
| `data/master/` | The master-data CSVs |
| `database/` | The SQLite schema |

The `FACTORY_*.md` and `PROJECT_OVERVIEW.md` documents are the design record: the data
model, the schema, the ORM specification and the platform overview.
