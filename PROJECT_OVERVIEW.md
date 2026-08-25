# FactoryFlow AI

**Agentic Manufacturing Monitoring & Intelligent Decision Support Platform**

---

| Field | Value |
|---|---|
| Project Name | FactoryFlow AI |
| Document Type | Project Understanding & Architecture Overview |
| Document Status | Baseline (authoritative reference for all future implementation) |
| Audience | Engineers, reviewers, technical interviewers, project maintainers |
| Scope of This Document | Documentation only. No implementation, no APIs, no schemas, no UI. |

> **Purpose of this document.** This is the single source of truth for *what* FactoryFlow AI is, *why* it exists, and *what the finished system must achieve*. Every subsequent design document, task breakdown, and implementation must remain consistent with the vision, architecture, and constraints described here. If a future decision conflicts with this document, this document wins unless it is explicitly revised.

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Project Introduction](#2-project-introduction) — includes [Resume Objective](#24-resume-objective)
3. [Background](#3-background)
4. [Problem Statement](#4-problem-statement)
5. [Proposed Solution](#5-proposed-solution)
6. [Vision](#6-vision)
7. [Mission](#7-mission)
8. [Project Goal](#8-project-goal)
9. [Objectives](#9-objectives)
10. [Scope](#10-scope)
11. [What We Are Building](#11-what-we-are-building)
12. [Why Agentic AI](#12-why-agentic-ai)
13. [Core Features](#13-core-features)
14. [Expected Outcomes](#14-expected-outcomes)
15. [Complete High-Level Workflow](#15-complete-high-level-workflow)
16. [Architectural Principles](#16-architectural-principles)
17. [Success Criteria](#17-success-criteria)
18. [Future Development Roadmap](#18-future-development-roadmap)

Closing sections:

- [Repository Philosophy](#repository-philosophy)
- [Document Governance](#document-governance)

---

## 1. Executive Summary

FactoryFlow AI is an agentic manufacturing intelligence platform that continuously monitors a simulated factory environment and helps factory managers act *before* production failures occur.

Conventional manufacturing dashboards are passive. They render temperature, RPM, torque, production counts, inventory levels, and maintenance records, and then leave the interpretation entirely to a human. A manager watching dozens of machines has to mentally correlate hundreds of metrics, infer which readings actually matter, estimate business impact, and decide what to do — under time pressure, repeatedly, every shift.

FactoryFlow AI closes that interpretation gap. A live factory simulator produces realistic operational telemetry. That telemetry is persisted to an operational database, which serves as the system's single source of truth. A chain of single-responsibility agents then processes it: a **Monitoring Agent** detects meaningful operational events, a **Prediction Agent** applies a trained machine learning model to quantify machine failure risk, a **Supervisor Agent** orchestrates the flow and enriches significant events with production, inventory, and maintenance context, and a **Decision Agent** powered by Gemini reasons over that enriched context to produce an explainable, business-aware recommendation. A **Notification Service** delivers the result to the factory manager through email and WhatsApp, and a dashboard exposes live state and operational history.

Three design commitments define the platform:

- **Layered intelligence.** Deterministic rules, probabilistic machine learning, and generative reasoning are separate stages, each with a single responsibility. Nothing is collapsed into one opaque model call.
- **Explainability by construction.** Every recommendation can be traced back through the reasoning, the risk score, the detected event, and the raw telemetry that triggered it.
- **Human-in-the-loop authority.** The platform observes and advises. It never actuates machinery. The final decision always belongs to the factory manager.

The result is a compact, modular, production-shaped system that demonstrates end-to-end AI engineering capability: simulation, data engineering, applied ML, multi-agent orchestration, LLM reasoning, and operational delivery — assembled cleanly enough to explain in a technical interview and defend under scrutiny.

---

## 2. Project Introduction

### 2.1 What the platform is

FactoryFlow AI is an **AI-powered manufacturing decision support platform**. It sits on top of factory operational data and answers the questions a dashboard cannot: which machine is at risk, why, what it will cost, and what to do next.

### 2.2 Why a simulated factory

The platform is built around a simulated factory rather than physical hardware. This is a deliberate and defensible engineering choice. Simulation gives full control over machine degradation patterns, failure scenarios, production schedules, and inventory movement — which makes the system reproducible, testable, and demonstrable on demand, without requiring access to proprietary industrial equipment or a licensed OT network. Every downstream component is built against the same interfaces it would use with real telemetry, so the simulator is a substitutable data source, not a shortcut baked into the architecture.

### 2.3 System composition

The platform integrates eight cooperating components into a single linear pipeline:

| Component | Single Responsibility |
|---|---|
| Factory Simulator | Generate realistic, continuous factory telemetry |
| Operational Database | Persist operational state and history as the system's source of truth |
| Monitoring Agent | Detect meaningful operational events from raw telemetry |
| Prediction Agent | Quantify machine failure risk using a trained ML model |
| Supervisor Agent | Orchestrate the pipeline and assemble decision context |
| Decision Agent (Gemini) | Reason over context and generate explainable recommendations |
| Notification Service | Deliver recommendations to the factory manager |
| Factory Manager | Review, judge, and act — the human decision authority |

Each component does exactly one thing. That constraint is the backbone of the architecture and the reason the system stays comprehensible as it grows.

FactoryFlow AI is developed as a portfolio-grade AI engineering project. It is intentionally scoped to be *complete and correct* rather than large: modular, production-shaped, maintainable, and straightforward to walk through on a whiteboard.

### 2.4 Resume objective

This project exists to demonstrate **end-to-end AI Engineering capability in a single coherent system** — not one skill in isolation, but the full path from raw data to a delivered, explainable decision.

Skills demonstrated, and where each one lives in the platform:

| Skill Area | Demonstrated By |
|---|---|
| **Machine Learning** | Predictive maintenance model that converts machine telemetry into calibrated failure probability and risk classification |
| **Agentic AI** | Multi-agent pipeline of single-responsibility agents coordinated by a Supervisor Agent, with escalation gating |
| **LLM Integration** | Gemini-powered Decision Agent producing structured, business-aware operational recommendations |
| **Prompt Engineering** | Context assembly and reasoning design that yields consistent, structured, explainable output rather than free-form text |
| **Backend Development** | Service composition, event-driven processing, notification delivery, and integration of ML and LLM components |
| **Database Design** | Operational data model serving as single source of truth for telemetry, events, predictions, recommendations, and history |
| **System Architecture** | Layered pipeline with strict separation of concerns, defined component boundaries, and justified technology choices |
| **Production Software Engineering** | Modular structure, graceful degradation, observability, traceability, reproducible setup, and maintainable code |

The intent is that a reviewer can see at a glance that the project spans data generation, data engineering, applied ML, agent orchestration, LLM reasoning, and operational delivery — and that each layer was chosen for a reason that can be defended in conversation.

---

## 3. Background

### 3.1 The cost of unplanned downtime

In discrete manufacturing, unplanned machine stoppage is one of the most expensive operational events that can occur. A single unexpected failure cascades:

- The affected machine stops producing.
- Downstream stations starve; upstream stations accumulate work-in-progress.
- Production orders slip and delivery commitments are missed.
- Maintenance teams are pulled into reactive firefighting instead of planned work.
- Spare parts are consumed unpredictably, disrupting inventory planning.

The financial damage is rarely the repair cost. It is the lost production hours, the schedule disruption, and the knock-on effect on committed orders.

### 3.2 Why the data alone is not enough

Modern factories are not short on data. Machines emit temperature, rotational speed, torque, tool wear, vibration, and cycle counts continuously. Production systems track output, cycle time, and scrap. Inventory systems track raw material and spare part levels. Maintenance systems track service history and team availability.

The problem is that this data arrives as **hundreds of independent numbers with no interpretation attached**. Monitoring systems are built to *display* those numbers faithfully. They are not built to reason about them.

So the hard work stays with the human:

- Deciding which readings are normal variance and which are early warning signs.
- Correlating a machine anomaly with the specific production order it threatens.
- Weighing a marginal risk on a critical line against a severe risk on an idle line.
- Judging whether to intervene now, at the end of shift, or at the next planned window.

This is cognitive load that scales linearly with the number of machines and does not scale at all with human attention.

### 3.3 Where machine learning alone stops short

Predictive maintenance using machine learning is well-established: given sensor telemetry, a trained model can output a meaningful failure probability. This is genuinely valuable, and FactoryFlow AI uses it.

But a probability is not a decision. A model that reports `failure_probability = 0.87` on Machine 04 has told the manager something important and nothing actionable. It does not know:

- Whether Machine 04 is currently on the critical path for a priority order.
- Whether the required spare part is in stock.
- Whether the qualified maintenance team is available this shift.
- What the production loss looks like if the machine runs to failure versus stopping now.
- How to explain any of this to the person who has to sign off on the decision.

The gap between *a number* and *a defensible operational decision* is exactly where business context, reasoning, and explanation live. That gap is what FactoryFlow AI is built to close.

### 3.4 The enabling shift

Two capabilities have matured enough to make this practical:

1. **Reliable applied ML on tabular sensor data** — well-understood, cheap to train, fast to serve, and appropriate for quantifying failure risk.
2. **LLM-based reasoning that can synthesize heterogeneous context into natural-language explanation** — appropriate for interpretation, impact analysis, and recommendation.

Neither replaces the other. Combining them with a clean orchestration layer, and keeping a human as the decision authority, is the architectural thesis of this project.

---

## 4. Problem Statement

**Traditional manufacturing monitoring systems are passive.**

They reliably display:

- Machine temperature
- Rotational speed (RPM)
- Torque
- Tool wear
- Production count and cycle output
- Inventory and spare part levels
- Maintenance records and service history

They cannot answer the questions that actually determine what a manager should do:

| Business Question | Answerable by a Traditional Dashboard? |
|---|---|
| Which machine is likely to fail, and when? | No — it shows current values, not forward risk |
| Why is this machine at risk? | No — no causal interpretation |
| What is the business impact if it fails? | No — no link between machine state and production commitments |
| Which production line should be prioritized right now? | No — no cross-line comparison of risk against value |
| Which maintenance team should be assigned? | No — no awareness of skills, availability, or workload |
| What action should the manager take immediately? | No — no recommendation, no reasoning, no plan |

### Consequences

- **Reactive maintenance.** Problems are addressed after they cause a stoppage, when the cost is highest.
- **Manual interpretation at scale.** Managers correlate hundreds of metrics by hand, every shift, under time pressure.
- **Inconsistent decisions.** Outcomes depend on who is watching and how experienced they are. Institutional knowledge stays in individuals' heads.
- **Alert fatigue.** Threshold-only alarms fire on transient noise, so real signals get ignored along with the false ones.
- **Business-blind alerts.** A warning carries no information about the order it threatens or the revenue at stake, so triage is guesswork.
- **No traceable rationale.** After an incident, there is no record of why a decision was made, which prevents the organization from learning from it.

### The problem, stated precisely

> Factory operational data is abundant, but the interpretation of that data — risk assessment, business impact analysis, prioritization, and recommended action — remains an entirely manual, unaided, non-reproducible human task. There is no system that continuously watches the factory, predicts what is about to go wrong, understands what it means for the business, and tells the manager what to do about it in language they can act on and challenge.

FactoryFlow AI is built to solve exactly that problem.

---

## 5. Proposed Solution

FactoryFlow AI introduces an **agentic intelligence layer** between raw factory telemetry and the human decision-maker. Instead of rendering metrics and stopping, the platform continuously observes, predicts, contextualizes, reasons, and recommends.

### 5.1 Solution shape

```
                    ┌──────────────────────────┐
                    │    Factory Simulator     │  generates telemetry
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │  Operational Database    │  source of truth
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │    Monitoring Agent      │  detects events
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │    Prediction Agent      │  quantifies risk (ML)
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │    Supervisor Agent      │  orchestrates + enriches
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │  Decision Agent (Gemini) │  reasons + recommends
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │   Notification Service   │  delivers
                    └────────────┬─────────────┘
                                 ▼
                    ┌──────────────────────────┐
                    │     Factory Manager      │  decides + acts
                    └──────────────────────────┘
```

### 5.2 What each stage contributes

**Factory Simulator — realistic operational reality.**
Produces continuous telemetry for machines, production lines, and inventory, including gradual degradation trends and injectable failure scenarios. This makes the entire platform demonstrable and reproducible on demand. It generates data and nothing else; it performs no analysis.

**Operational Database — the single source of truth.**
Every reading, event, prediction, recommendation, and notification is persisted here. Agents never pass hidden state between themselves out of band; they read from and write to this store. This is what makes the system auditable, replayable, and debuggable, and it is what powers both the dashboard and operational history.

**Monitoring Agent — deterministic event detection.**
Continuously evaluates incoming operational data against explicit, transparent rules and thresholds to distinguish meaningful operational events from ordinary variance. It answers one question: *did something worth attention just happen?* It does not predict and it does not reason. Keeping this layer deterministic means it is cheap, fast, fully testable, and it prevents the expensive downstream stages from being invoked on noise.

**Prediction Agent — quantified failure risk.**
Applies the trained machine learning model to machine telemetry to produce a failure probability and a risk classification. This is the platform's quantitative layer: it converts sensor patterns into a calibrated number and a discrete risk level. It produces measurements, not narrative.

**Supervisor Agent — orchestration and context assembly.**
The coordinator. It decides whether a detected and scored event warrants escalation, and if so, it assembles the complete decision context: the machine's state and history, the production orders and lines affected, relevant inventory and spare part availability, and applicable maintenance information. Its output is a single coherent context package. It does not predict and it does not generate recommendations — it decides *what deserves attention* and gathers *everything needed to reason about it*. This is also where cost and noise are controlled, because it gates access to the LLM stage.

**Decision Agent (Gemini) — reasoning and explainable recommendation.**
Consumes the enriched context package and produces the platform's actual output: an interpretation of what is happening, the likely contributing factors, the business impact, a priority level, a concrete recommended action, a recovery plan, and a maintenance assignment suggestion — all in language a factory manager can read, act on, and argue with. It reasons and explains. It never actuates anything.

**Notification Service — reliable delivery.**
Delivers finished recommendations to the factory manager through email and WhatsApp, so an urgent recommendation reaches the person who needs it whether or not they are looking at a screen. It transports messages; it makes no decisions about content.

**Factory Manager — the decision authority.**
Reviews the recommendation, applies judgment and knowledge the system does not have, and decides. The platform advises; the human decides and acts.

### 5.3 Why this arrangement works

- **Progressive filtering.** Cheap deterministic checks run on everything; expensive ML runs on detected events; the most expensive reasoning runs only on escalated, context-enriched situations. Compute and cost track importance.
- **Right tool per layer.** Rules for facts, ML for probability, LLM for interpretation. No layer is asked to do work it is poorly suited for.
- **Traceable output.** Every recommendation decomposes into reasoning → risk score → detected event → raw telemetry. Nothing is unexplainable.
- **Independently testable stages.** Each agent has defined inputs and outputs, so each can be verified on its own.
- **Safe by design.** No control path to machinery exists anywhere in the architecture.

---

## 6. Vision

> **Build an intelligent manufacturing decision support platform capable of transforming traditional factory monitoring into proactive, AI-driven operational intelligence.**

The vision is a shift in the role of the monitoring system itself — from a passive instrument panel that reports what *is* happening, to an active analytical partner that anticipates what is *about to* happen and explains what to do about it.

In that end state:

- Operational risk surfaces **before** it becomes downtime, not after.
- Every alert arrives with interpretation, business impact, and a recommended action attached.
- Prioritization is grounded in production value, not just sensor severity.
- The reasoning behind every recommendation is visible and challengeable.
- The manager's attention is spent on **judgment**, not on manual metric correlation.
- Operational decisions become consistent and reproducible instead of dependent on who happens to be on shift.

Throughout, the human remains in command. The vision is amplified human judgment, not automated control.

---

## 7. Mission

| Mission Statement | What It Means in Practice |
|---|---|
| **Reduce operational downtime** | Surface failure risk early enough that intervention can be planned rather than forced |
| **Improve maintenance planning** | Convert reactive firefighting into prioritized, context-aware, schedulable work |
| **Increase production reliability** | Protect production commitments by identifying threats to specific orders and lines before they materialize |
| **Support factory managers with explainable AI recommendations** | Deliver reasoning and business impact alongside every recommendation, never a bare score |
| **Combine Machine Learning and Agentic AI into one production-ready platform** | Demonstrate that predictive ML and LLM reasoning integrate cleanly in a modular, maintainable system |

---

## 8. Project Goal

**Primary goal:**

> Build an agentic manufacturing intelligence platform that continuously monitors a simulated manufacturing environment and assists factory managers by predicting operational risks, understanding business context, and generating intelligent recommendations *before* production failures occur.

**Engineering goal:**

> Deliver that capability as a clean, modular, production-shaped system in which every component has exactly one responsibility, every AI output is explainable, and the complete architecture can be described end-to-end in a technical interview without hand-waving.

Both goals carry equal weight. A system that works but cannot be explained fails this project's purpose. So does an architecture that is elegant on paper but does not actually run end to end.

---

## 9. Objectives

### 9.1 Functional objectives

| # | Objective | Definition of Done |
|---|---|---|
| F1 | Continuously monitor factory operations | Machine, production, and inventory telemetry flows continuously and is persisted without gaps |
| F2 | Detect meaningful operational events | Genuine anomalies are detected; ordinary variance does not generate events |
| F3 | Predict machine failures using Machine Learning | A trained model produces failure probability and risk classification for monitored machines |
| F4 | Understand production context | Detected risk is linked to the specific production lines and orders it affects |
| F5 | Analyze business impact | Recommendations state the operational and production consequence, not just the technical fault |
| F6 | Generate explainable recommendations using Gemini | Each recommendation satisfies the explainability contract in §16.5: supporting evidence, ML confidence, root cause, business impact, and recommended action with recovery guidance and priority |
| F7 | Notify factory managers | Recommendations are delivered reliably via email and WhatsApp |
| F8 | Improve operational efficiency | Managers receive prioritized, actionable guidance instead of raw metrics |

### 9.2 Technical objectives

| # | Objective | Definition of Done |
|---|---|---|
| T1 | Strict single responsibility per component | Each component's purpose can be stated in one sentence with no conjunction |
| T2 | Event-driven pipeline | Downstream work is triggered by upstream events, not by polling everything continuously |
| T3 | Database as single source of truth | No hidden inter-agent state; all meaningful state is persisted |
| T4 | Full traceability | Any recommendation can be traced to the telemetry that produced it |
| T5 | Independently testable modules | Each agent can be exercised in isolation with defined inputs and outputs |
| T6 | Controlled LLM invocation | Reasoning runs only on escalated, context-enriched situations |
| T7 | Graceful degradation | Failure of one stage does not silently corrupt or halt the whole pipeline |
| T8 | Substitutable data source | The simulator can be replaced by a real telemetry source without redesigning downstream stages |

### 9.3 Portfolio objectives

| # | Objective | Definition of Done |
|---|---|---|
| P1 | Demonstrate end-to-end AI engineering | Simulation, data, ML, agents, LLM reasoning, and delivery all present and working together |
| P2 | Interview-explainable architecture | Whole system can be drawn and defended on a whiteboard in a few minutes |
| P3 | Deliberately scoped complexity | Nothing in the system exists without a stated reason for being there |
| P4 | Professional repository quality | Clear structure, coherent documentation, reproducible setup and demo |

---

## 10. Scope

### 10.1 In scope

**Simulation**
- Machine telemetry generation (temperature, rotational speed, torque, tool wear, operational state)
- Production activity simulation (lines, orders, output, cycle progress)
- Inventory and spare part level simulation
- Gradual degradation behavior and injectable failure scenarios for demonstration

**Data and persistence**
- Persistence of operational telemetry
- Persistence of detected events, predictions, recommendations, and notification records
- Operational history retention for review and audit

**Monitoring**
- Continuous evaluation of operational data
- Rule- and threshold-based detection of meaningful operational events
- Suppression of ordinary variance and transient noise

**Machine Learning**
- Predictive maintenance model trained on machine operating parameters
- Machine failure probability output
- Risk classification into discrete severity levels

**Agentic orchestration**
- Escalation decisions on detected and scored events
- Assembly of production, inventory, and maintenance context
- Coordination of the pipeline from event through recommendation

**AI reasoning and decision support**
- Interpretation of enriched operational context
- Business impact analysis
- Priority assignment
- Recommended immediate action
- Recovery planning guidance
- Maintenance team assignment suggestion
- Human-readable explanation of the reasoning

**Delivery and interfaces**
- Dashboard visualization of live factory state, risk, events, and recommendations
- Operational history views
- Email notifications
- WhatsApp notifications

### 10.2 Out of scope

**Explicitly excluded by design:**

| Excluded | Reason |
|---|---|
| Direct machine control or actuation | The platform is advisory. Human-in-the-loop is a core safety principle, not a limitation |
| ERP functionality (finance, HR, procurement, accounting) | FactoryFlow AI is not an ERP |
| Manufacturing Execution System functionality (work order execution, shop floor routing, dispatch) | FactoryFlow AI is not an MES |
| Industrial control systems (PLC, SCADA, DCS integration) | FactoryFlow AI is not an ICS and never writes to control hardware |
| Physical hardware or IIoT gateway integration | Simulation provides reproducibility and demonstrability; real telemetry integration is a future extension |
| Quality management, compliance, and certification workflows | Outside the decision support problem being solved |
| Multi-tenant SaaS infrastructure, billing, org management | Unnecessary enterprise complexity for this project's purpose |
| Enterprise identity federation (SSO, SAML, LDAP) | Not required to demonstrate the core capability |
| Autonomous action without human review | Violates the human-in-the-loop principle |
| Distributed streaming and big-data infrastructure | Not justified by the data volume; would add complexity without adding capability |

### 10.3 Scope discipline

Scope is treated as a design constraint, not a wish list. Every addition to this platform must satisfy three tests:

1. **Does it serve the core problem?** Does it help predict, contextualize, explain, or deliver operational risk insight?
2. **Does it earn its complexity?** Does the capability gained justify the maintenance and cognitive cost added?
3. **Can it be explained in one sentence?** If its purpose cannot be stated simply, it does not belong.

Anything that fails these tests is out of scope, regardless of how technically interesting it is.

---

## 11. What We Are Building

### 11.1 Positioning

**FactoryFlow AI is NOT an ERP.**
It does not manage finance, procurement, human resources, or accounting. It holds no master business records.

**FactoryFlow AI is NOT a Manufacturing Execution System.**
It does not execute work orders, route jobs across the shop floor, dispatch operators, or manage production sequencing.

**FactoryFlow AI is NOT an Industrial Control System.**
It does not integrate with PLCs, issue setpoints, trip interlocks, or write to any control layer. There is no control path in the architecture.

**FactoryFlow AI IS an AI-powered Manufacturing Decision Support Platform.**

> The platform observes factory operations and provides intelligent recommendations. It never directly controls machines. The final decision always belongs to the factory manager.

### 11.2 What that means concretely

| The platform does | The platform does not |
|---|---|
| Observe operational data continuously | Command or configure machinery |
| Detect meaningful operational events | Suppress or override alarms in control systems |
| Predict machine failure risk | Guarantee failure outcomes |
| Assemble production and business context | Own or manage production planning |
| Reason about impact and generate recommendations | Execute those recommendations |
| Notify and inform the manager | Replace the manager's judgment |
| Record what it observed and why it recommended it | Act without a human in the loop |

### 11.3 The deliverable

A working, demonstrable platform consisting of:

1. A **live factory simulation** that produces realistic, continuous operational data with controllable degradation and failure scenarios.
2. An **operational data layer** that persists all telemetry, events, predictions, recommendations, and notifications as the system's source of truth.
3. A **monitoring layer** that continuously detects meaningful operational events with transparent, deterministic logic.
4. A **machine learning prediction layer** that converts machine telemetry into calibrated failure probability and risk classification.
5. An **agentic orchestration layer** that decides what warrants escalation and assembles complete decision context.
6. An **LLM reasoning layer** (Gemini) that produces explainable, business-aware recommendations.
7. A **notification layer** that delivers recommendations through email and WhatsApp.
8. A **dashboard** presenting live factory state, current risk, active events, recommendations, and operational history.

The system is judged on whether the whole chain works end to end and whether every part of it can be explained.

---

## 12. Why Agentic AI

This is the central architectural question the project must answer convincingly, so it is worth answering thoroughly.

### 12.1 Why a rules engine alone is insufficient

Threshold rules are fast, transparent, and cheap — and FactoryFlow AI uses them in the Monitoring Agent for exactly that reason. But rules alone cannot carry the platform:

- They detect **threshold crossings**, not **developing trends**. A machine drifting toward failure over hours stays "in range" until it suddenly is not.
- They cannot weigh multiple weak signals that are individually benign and jointly alarming.
- They produce facts (`temperature > limit`), never interpretation, impact, or recommendation.
- Tuned tightly they cause alert fatigue; tuned loosely they miss real events.

Rules answer *did a limit get crossed?* That is a necessary question, not a sufficient one.

### 12.2 Why an ML model alone is insufficient

A well-trained predictive model quantifies failure risk far better than any threshold — and FactoryFlow AI uses one in the Prediction Agent for exactly that reason. But a model alone stops short:

- Its output is a number. A manager cannot act on `0.87` without knowing what it implies.
- It has no business context: it cannot know which order the machine is running or what stock is available.
- It cannot compare situations across lines by production value.
- It cannot explain itself in operational language, and it cannot produce a recovery plan.

ML answers *how likely is failure?* Also necessary. Also not sufficient.

### 12.3 Why a single monolithic LLM call is insufficient

The tempting shortcut is to push all telemetry into one large LLM prompt and ask for a recommendation. That fails on multiple axes:

| Problem | Consequence |
|---|---|
| LLMs are poor numerical risk estimators | Failure probability becomes unreliable and uncalibrated compared to a trained model |
| Prompting on every reading is expensive | Cost scales with telemetry volume rather than with importance |
| One prompt doing detection, prediction, context, and reasoning is untestable | A bad output cannot be attributed to a specific stage |
| Deterministic facts become probabilistic | Threshold checks that should always be right become occasionally wrong |
| No progressive filtering | Noise reaches the most expensive layer |
| Opaque single-step output | Explainability collapses; you get an answer with no traceable derivation |

A monolithic prompt is not a simpler architecture. It is the same complexity with the seams hidden, which makes it harder to debug and impossible to defend.

### 12.4 What the agentic structure actually buys

Agentic AI here means **a pipeline of specialized, single-responsibility agents, each using the technique appropriate to its job, coordinated by a supervisor.** Concretely:

**1. Right tool per layer.**
Deterministic rules where facts are needed. Trained ML where calibrated probability is needed. LLM reasoning where interpretation and language are needed. No layer is stretched beyond what it is good at.

**2. Progressive filtering and cost control.**
Cheap checks run on all data. ML runs on detected events. LLM reasoning runs only on escalated, context-enriched situations. Compute spend follows significance — this is both an efficiency property and a noise-control property.

**3. Explainability by construction.**
Because each stage records its output, every recommendation decomposes cleanly:

```
Recommendation
   └── produced by reasoning over enriched context
         └── which included a risk classification and probability
               └── derived from a detected operational event
                     └── traced to specific telemetry readings
```

Explainability is a structural property of the pipeline, not a feature bolted on afterward.

**4. Testability and debuggability.**
Each agent has defined inputs and outputs, so each can be exercised in isolation. When output is wrong, the failing stage is identifiable rather than a matter of guesswork.

**5. Separation of orchestration from reasoning.**
The Supervisor Agent decides *what deserves attention* and *what information is needed*. The Decision Agent decides *what it means* and *what to do*. Splitting control flow from cognition keeps both simple and independently changeable.

**6. Maintainability and evolvability.**
Retraining the model does not touch monitoring. Adding a notification channel does not touch reasoning. Refining prompts does not touch detection. Boundaries make change local.

**7. Graceful degradation.**
If reasoning is unavailable, detection and prediction continue and the risk signal still reaches the manager. If notification delivery fails, the recommendation is still recorded and visible on the dashboard. Layers fail independently rather than collectively.

**8. Human-in-the-loop safety.**
The agentic chain terminates at a notification to a human. No agent holds authority to act on the physical world. The architecture makes unsafe autonomy structurally impossible, not merely discouraged.

### 12.5 The one-sentence justification

> Agentic AI is used because manufacturing decision support requires three fundamentally different kinds of computation — deterministic fact-checking, probabilistic prediction, and generative reasoning — and separating them into coordinated single-responsibility agents produces a system that is measurably more accurate, cheaper to run, easier to test, and far more explainable than any single-technique alternative.

---

## 13. Core Features

### 13.1 Simulation and data generation

| Feature | Description |
|---|---|
| Factory simulation | Continuous generation of realistic factory operating conditions with controllable scenarios |
| Machine telemetry generation | Machine operating parameters including temperature, rotational speed, torque, and tool wear |
| Production activity simulation | Production lines, orders, output progress, and cycle activity |
| Inventory simulation | Raw material and spare part level movement over time |
| Degradation and failure scenarios | Gradual wear patterns and injectable failure conditions for reliable demonstration |

### 13.2 Monitoring

| Feature | Description |
|---|---|
| Machine monitoring | Continuous observation of machine operating state and parameters |
| Production monitoring | Continuous observation of production line activity and output |
| Inventory monitoring | Continuous observation of stock and spare part availability |
| Operational event detection | Deterministic identification of meaningful operational events from raw telemetry |
| Noise suppression | Filtering of ordinary variance so only significant events proceed downstream |

### 13.3 Prediction

| Feature | Description |
|---|---|
| Predictive maintenance | ML-based forward-looking assessment of machine health |
| Machine failure prediction | Failure probability produced from machine operating parameters |
| Risk classification | Mapping of probability into discrete, interpretable severity levels |

### 13.4 Intelligence and decision support

| Feature | Description |
|---|---|
| AI reasoning | Interpretation of enriched operational context by the Gemini-powered Decision Agent |
| Root cause identification | Most likely contributing factor or failure mode, stated as a hypothesis consistent with the evidence |
| Business impact analysis | Assessment of operational and production consequence of the predicted risk |
| Recovery planning | Guidance on how to restore or protect normal operation |
| Maintenance recommendations | Recommended action and suggested maintenance team assignment |
| Supporting evidence | The specific telemetry, event, and trend that triggered the recommendation, carried through to the manager |
| ML confidence reporting | Failure probability and risk level surfaced alongside the recommendation so urgency can be calibrated |
| Explainable output | Human-readable reasoning accompanying every recommendation, meeting the contract in §16.5 |
| Prioritization | Priority level reflecting both technical severity and business impact |

### 13.5 Interfaces and delivery

| Feature | Description |
|---|---|
| Dashboard visualization | Live view of factory state, machine risk, active events, and recommendations |
| Operational history | Retained record of past events, predictions, recommendations, and outcomes |
| Email notifications | Delivery of recommendations to the factory manager by email |
| WhatsApp notifications | Delivery of urgent recommendations to the factory manager by WhatsApp |

---

## 14. Expected Outcomes

On completion, FactoryFlow AI will demonstrate:

### 14.1 Operational capability

| Outcome | Demonstrated By |
|---|---|
| Real-time manufacturing simulation | A live factory producing continuous, realistic telemetry with controllable scenarios |
| Continuous operational monitoring | Uninterrupted observation of machines, production, and inventory |
| Intelligent event detection | Meaningful events surfaced while ordinary variance is filtered out |
| Machine failure prediction | Calibrated failure probability and risk classification from the trained model |
| AI-powered operational reasoning | Contextual interpretation of factory situations by the Decision Agent |
| Explainable recommendations | Every recommendation accompanied by visible reasoning and traceable evidence |
| Business-aware decision support | Recommendations that reference affected production, impact, and priority |
| Operational notification delivery | Recommendations reaching the manager through email and WhatsApp |

### 14.2 Engineering quality

| Outcome | Demonstrated By |
|---|---|
| Production-ready modular architecture | Clear component boundaries, single responsibilities, independent testability |
| Clean separation of concerns | No component doing work that belongs to another |
| Event-driven processing | Downstream work triggered by upstream significance, not brute-force polling |
| Traceable AI decisions | Full derivation chain from recommendation back to raw telemetry |
| Graceful degradation | Independent stage failure without whole-pipeline collapse |
| Maintainable, extensible codebase | Changes localized to the component that owns the concern |

### 14.3 Portfolio value

| Outcome | Demonstrated By |
|---|---|
| Clean AI Engineer portfolio quality | A complete, coherent, runnable project with professional documentation |
| Breadth of demonstrated skill | Simulation, data engineering, applied ML, agent orchestration, LLM integration, delivery |
| Depth of architectural reasoning | Every design decision has a stated rationale and a considered alternative |
| Interview readiness | End-to-end architecture explainable on a whiteboard, with defensible trade-offs |
| Restraint | Deliberate absence of unnecessary technology and complexity |

---

## 15. Complete High-Level Workflow

### 15.1 Pipeline

```
Factory Simulator
        ↓
Operational Database
        ↓
Monitoring Agent
        ↓
Prediction Agent
        ↓
Supervisor Agent
        ↓
Decision Agent (Gemini)
        ↓
Notification Service
        ↓
Factory Manager
```

### 15.2 Stage-by-stage flow

**Stage 1 — Factory Simulator**
The simulator advances factory state on a continuous cycle, producing machine telemetry, production activity, and inventory movement. Machines exhibit realistic behavior including gradual degradation, and specific failure scenarios can be injected for demonstration.
*Produces:* a stream of operational readings.
*Responsibility boundary:* generates data only; performs no analysis.

**Stage 2 — Operational Database**
All generated telemetry is persisted, along with every artifact produced downstream — detected events, predictions, recommendations, and notification records. This is the system's single source of truth and the backing store for the dashboard and operational history.
*Produces:* durable, queryable operational state and history.
*Responsibility boundary:* stores and serves data; performs no analysis.

**Stage 3 — Monitoring Agent**
Reads current operational data and evaluates it against explicit rules and thresholds to identify meaningful operational events, filtering out ordinary variance. Detected events are recorded.
*Produces:* operational events.
*Responsibility boundary:* detects that something happened; does not predict or reason.

**Stage 4 — Prediction Agent**
Applies the trained machine learning model to relevant machine telemetry, producing a failure probability and a risk classification. Predictions are recorded against the triggering event.
*Produces:* failure probability and risk level.
*Responsibility boundary:* quantifies risk; produces no narrative or recommendation.

**Stage 5 — Supervisor Agent**
Evaluates the detected event together with its risk assessment and decides whether the situation warrants escalation. For escalated situations, it assembles the full decision context: machine state and recent history, affected production lines and orders, relevant inventory and spare part availability, and applicable maintenance information.
*Produces:* an escalation decision and a complete context package.
*Responsibility boundary:* orchestrates and enriches; does not predict or recommend. Acts as the gate controlling access to the reasoning stage.

**Stage 6 — Decision Agent (Gemini)**
Reasons over the enriched context package and produces the platform's operational output: interpretation of the situation, likely contributing factors, business impact, priority, recommended immediate action, recovery plan, and suggested maintenance assignment — expressed in language a factory manager can act on. The recommendation and its reasoning are recorded.
*Produces:* an explainable, business-aware recommendation.
*Responsibility boundary:* reasons and recommends; never executes or actuates.

**Stage 7 — Notification Service**
Delivers the finished recommendation to the factory manager through email and WhatsApp, so urgent guidance reaches the right person regardless of whether they are at a screen. Delivery outcomes are recorded.
*Produces:* delivered notifications and delivery records.
*Responsibility boundary:* transports messages; makes no decisions about content or priority.

**Stage 8 — Factory Manager**
Receives the recommendation with its reasoning and evidence, reviews it against knowledge the system does not have, and decides what to do. The dashboard and operational history support this review and any later retrospective.
*Produces:* the actual operational decision and action.
*Responsibility boundary:* holds full and final decision authority.

### 15.3 Cross-cutting flow properties

**Progressive narrowing.** Volume decreases and value per item increases at every stage. All telemetry is monitored; a fraction becomes events; a fraction of those is scored as risky; a fraction of those is escalated; those become recommendations. Expensive computation is reserved for what matters.

**Persistence at every stage.** Each stage writes its output to the operational database. Agents do not hold hidden state between one another, which is what makes the pipeline auditable, replayable, and debuggable.

**Traceability in both directions.** Forward: telemetry → event → prediction → context → recommendation → notification. Backward: any recommendation can be decomposed into the exact evidence that produced it.

**Termination at a human.** The pipeline's terminal node is a person, not an actuator. This is a structural guarantee of the architecture.

---

## 16. Architectural Principles

These principles are binding. Future implementation must conform to them, and any proposed deviation must be justified against them explicitly.

### 16.1 Separation of Concerns

Every component owns exactly one concern. Data generation, persistence, event detection, prediction, orchestration, reasoning, and delivery are distinct responsibilities held by distinct components.

**Practical test:** if a component's purpose cannot be stated in one sentence without the word "and," it is doing too much.

**Why it matters:** boundaries let each stage be understood, tested, replaced, and explained on its own.

### 16.2 Single Responsibility per Component

Stated concretely for each component:

| Component | Its one responsibility | What it must never do |
|---|---|---|
| Factory Simulator | Generate telemetry | Analyze, detect, or decide |
| Operational Database | Persist and serve data | Contain business or analytical logic |
| Monitoring Agent | Detect operational events | Predict, reason, or recommend |
| Prediction Agent | Quantify failure risk | Detect events, gather context, or recommend |
| Supervisor Agent | Orchestrate and assemble context | Predict risk or generate recommendations |
| Decision Agent | Reason and recommend | Detect, predict, actuate, or deliver |
| Notification Service | Deliver messages | Decide content, priority, or recipients' actions |

### 16.3 Modular Design

Components interact through defined inputs and outputs, not through shared internals. Any component can be modified, retrained, or replaced without redesigning its neighbours. The Factory Simulator in particular is a substitutable data source: replacing it with real telemetry must not require changes downstream.

### 16.4 Event-Driven Processing

Work flows forward on significance, not on brute-force polling of everything. Detection triggers prediction; prediction informs escalation; escalation triggers reasoning; reasoning triggers notification. This keeps the system responsive, keeps cost proportional to importance, and keeps noise away from expensive stages.

### 16.5 Explainable AI

No unexplained output reaches a human. Every recommendation carries its reasoning, and every reasoning step traces back through risk classification and detected event to the raw telemetry involved. Explainability is achieved structurally — by persisting each stage's output — rather than by asking a model to justify itself after the fact.

**Mandatory recommendation contents.** "Explainable" is not a quality goal, it is a contract. Every recommendation the platform produces must include all of the following elements. A recommendation missing any of them is incomplete and must not be delivered as final:

| Element | What It Must Contain | Why It Is Required |
|---|---|---|
| **Supporting evidence** | The specific telemetry readings, detected event, and observed trend that triggered the recommendation | Lets the manager verify the machine actually looks the way the system claims, instead of trusting a black box |
| **ML confidence** | The failure probability and risk classification from the Prediction Agent, presented as a number the manager can weigh | Distinguishes a marginal signal from a strong one, so the manager can calibrate urgency and their own trust in the output |
| **Root cause** | The most likely contributing factor or failure mode consistent with the evidence, stated as a hypothesis rather than a certainty | Turns "something is wrong" into "here is probably what is wrong," which is what makes the recommendation actionable |
| **Business impact** | The affected production line and orders, and the operational consequence if no action is taken | Enables prioritization by production value, not just by sensor severity |
| **Recommended action** | The concrete immediate step to take, plus recovery guidance and a suggested maintenance assignment | Removes the interpretation burden and gives the manager something to accept, modify, or reject |

**Design consequences of this contract:**

- The recommendation is a **structured object with named fields**, not a paragraph of prose. Structure is what makes it renderable on a dashboard, deliverable in a notification, and checkable for completeness.
- **ML confidence originates from the Prediction Agent, never from the LLM.** The Decision Agent carries the number forward and interprets it; it does not invent or restate it as its own estimate. This preserves calibration.
- **Supporting evidence originates from persisted telemetry and events**, so it is verifiable against the operational database rather than being generated narrative.
- **Root cause is always framed as a hypothesis.** The platform is advisory, and overstating diagnostic certainty would undermine the human-in-the-loop principle.
- Because each field maps to the stage that produced it, completeness of the contract and traceability of the pipeline are the same property.

**Practical test:** for any recommendation the system has produced, it must be possible to answer "why did it say that?" from stored data alone — and to point at each of the five elements above in the recommendation itself.

### 16.6 Human-in-the-Loop Decision Making

The platform advises; the human decides. No component has authority to act on the physical world, and no control path to machinery exists in the architecture. The pipeline terminates at a notification to a factory manager. This is a hard architectural boundary, not a configuration choice.

### 16.7 Single Source of Truth

The operational database is authoritative for all operational state and history. Agents read from it and write to it rather than passing hidden state between themselves. This is the precondition for auditability, replay, and reliable debugging.

### 16.8 Right Tool for Each Layer

Deterministic logic for facts. Trained ML for calibrated probability. LLM reasoning for interpretation and language. Using an LLM for arithmetic risk estimation or a threshold rule for business impact analysis are both architectural errors.

### 16.9 Deliberate Simplicity

Complexity must be earned. Every technology, component, and abstraction requires a stated reason for existing. Distributed streaming, multi-tenancy, enterprise identity federation, and speculative extension points are excluded because the problem does not require them. A system that is smaller and fully understood is worth more here than one that is larger and partially understood.

**Practical test:** if a component's removal would not reduce delivered capability, it should not be there.

### 16.10 Graceful Degradation

Stage failures are isolated. Unavailable reasoning does not stop monitoring and prediction. Failed notification delivery does not lose the recommendation, which remains recorded and visible on the dashboard. Failures are recorded rather than swallowed.

### 16.11 Observability

The system's own behavior is inspectable: what was detected, what was predicted, what was escalated, what was recommended, what was delivered, and what failed. Operational history serves both the factory manager's retrospective review and the engineer's debugging.

### 16.12 Reproducibility

Because the data source is simulated and every stage's output is persisted, scenarios can be re-run and outcomes re-examined. This makes the platform reliably demonstrable and makes behavioral regressions detectable.

---

## 17. Success Criteria

The project is complete when all criteria below are satisfied.

### 17.1 Functional success criteria

| # | Criterion |
|---|---|
| FS1 | The factory simulator runs continuously and produces realistic machine, production, and inventory data |
| FS2 | All operational data is persisted reliably without gaps |
| FS3 | The Monitoring Agent detects genuine operational events and does not fire on ordinary variance |
| FS4 | The Prediction Agent produces failure probability and risk classification for monitored machines |
| FS5 | The Supervisor Agent correctly escalates significant situations and assembles complete context |
| FS6 | The Decision Agent produces recommendations satisfying the full explainability contract in §16.5 — supporting evidence, ML confidence, root cause, business impact, and recommended action including recovery guidance, maintenance assignment suggestion, and priority |
| FS7 | Notifications are delivered successfully via both email and WhatsApp |
| FS8 | The dashboard presents live factory state, machine risk, active events, and recommendations |
| FS9 | Operational history is retained and reviewable |
| FS10 | A failure scenario can be injected and followed end to end through every stage to a delivered recommendation |

### 17.2 Technical success criteria

| # | Criterion |
|---|---|
| TS1 | Each component's responsibility is stated in one sentence and its implementation does not exceed it |
| TS2 | No component contains logic belonging to another |
| TS3 | Each agent can be exercised independently with defined inputs and outputs |
| TS4 | The operational database is the only place meaningful state lives |
| TS5 | LLM reasoning is invoked only on escalated, context-enriched situations |
| TS6 | Any recommendation can be traced back to the telemetry that produced it using stored data alone |
| TS7 | Failure of any single stage degrades the system gracefully and is recorded |
| TS8 | The simulator could be replaced by a real telemetry source without redesigning downstream stages |
| TS9 | No control path to machinery exists anywhere in the system |
| TS10 | Every technology in the stack has a stated justification |

### 17.3 AI quality success criteria

| # | Criterion |
|---|---|
| AQ1 | Failure predictions are calibrated and meaningfully better than threshold-only detection |
| AQ2 | Recommendations are specific and actionable, not generic advice |
| AQ3 | Business impact statements reference the actual affected production context, not boilerplate |
| AQ4 | Reasoning is consistent with the underlying telemetry and risk assessment |
| AQ5 | Priority assignments reflect both technical severity and business impact |
| AQ6 | Every AI output is explainable to a non-technical factory manager |
| AQ7 | No recommendation is delivered with any element of the §16.5 explainability contract missing |
| AQ8 | ML confidence reported in a recommendation matches the Prediction Agent's output exactly and is never re-estimated by the LLM |

### 17.4 Portfolio success criteria

| # | Criterion |
|---|---|
| PS1 | The full architecture can be drawn and explained end to end in a few minutes |
| PS2 | Every major design decision has a stated rationale and a considered alternative |
| PS3 | The project runs and demonstrates reproducibly from documented setup steps |
| PS4 | Documentation is coherent, professional, and consistent with the implementation |
| PS5 | The repository contains no unnecessary files, folders, or unused technology |
| PS6 | The project demonstrably spans simulation, data engineering, applied ML, agent orchestration, LLM reasoning, and delivery |

---

## 18. Future Development Roadmap

High-level direction only. Nothing in this section is committed scope, and nothing here may be used to justify complexity in the core build. The core platform must be complete and correct before any of it is considered.

### Phase 1 — Core Platform (this project)

Factory simulation, operational persistence, event detection, ML failure prediction, agentic orchestration, Gemini-powered reasoning, notification delivery, dashboard, and operational history — the complete pipeline described in this document.

### Phase 2 — Intelligence Depth

Direction: strengthen the quality of prediction and reasoning without changing the architecture.

- Richer degradation and failure modeling in the simulator
- Additional machine types and failure modes
- Improved feature engineering and model refinement
- Trend- and horizon-aware risk assessment
- More nuanced prioritization across competing risks

### Phase 3 — Decision Feedback Loop

Direction: let the system learn from what managers actually decided.

- Capture manager acceptance, rejection, or modification of recommendations
- Track realized outcomes against predictions
- Surface recommendation quality metrics over time
- Use accumulated feedback to refine reasoning and prioritization

### Phase 4 — Operational Breadth

Direction: widen the operational picture the platform reasons over.

- Cross-line and plant-level risk aggregation
- Maintenance scheduling awareness and window optimization
- Deeper spare part and supply constraint reasoning
- Shift and team capacity awareness

### Phase 5 — Real Telemetry Readiness

Direction: prove the simulator is genuinely substitutable.

- Ingestion path for real machine telemetry
- Data quality handling for missing, delayed, and noisy readings
- Model adaptation for real-world data distributions
- Strict preservation of the read-only, advisory, no-control boundary

### Phase 6 — Enterprise Hardening

Direction: deployment maturity, considered only if the project's purpose changes.

- Access control and role-based views
- Audit and compliance reporting
- Deployment and scaling hardening
- Multi-plant support

### Roadmap discipline

Later phases are recorded for context, not for anticipation. No abstraction, extension point, or dependency may be added to the core platform on the grounds that a future phase might need it. When a phase is actually undertaken, the required structure will be introduced then, with the benefit of real requirements.

---

## Repository Philosophy

Architecture describes how the system is arranged. This section describes how the code inside it is expected to be written. Both are binding.

This repository follows:

| Principle | What It Means Here | How It Is Checked |
|---|---|---|
| **Clean Architecture** | Business logic does not depend on delivery mechanisms or infrastructure. The prediction and reasoning layers do not know whether output goes to email, WhatsApp, or a dashboard. Data access is separated from the logic that uses it | Swapping a notification channel or the persistence layer touches one module, not several |
| **Modular Design** | Each component is a self-contained unit with defined inputs and outputs. Components communicate through those boundaries, never through shared internal state | Any component can be exercised in isolation and replaced without redesigning its neighbours |
| **Small Components** | Files, modules, and functions stay small enough to read in one sitting. A module with more than one reason to change should be split | A reviewer can understand any single file without holding three others in their head |
| **Production-ready code** | Errors handled explicitly, failures logged rather than swallowed, configuration externalized, no hardcoded credentials, no silent fallbacks that mask problems | The system behaves predictably when a stage fails, and the failure is visible afterwards |
| **Readable implementation** | Clear naming, consistent structure, comments that explain *why* rather than restating *what*, and no cleverness that costs comprehension | A reader unfamiliar with the project can follow the pipeline end to end from the code alone |
| **Minimal dependencies** | Every dependency must earn its place. No library is added for something the standard library or an existing dependency already does well | Each entry in the dependency list has a stated reason and is actually used |

**Why this matters beyond tidiness.** These conventions are what make the architecture in §16 real rather than aspirational. Single responsibility is only true if the code respects the boundary. Explainability is only true if the persisted data is complete. Graceful degradation is only true if errors are actually handled. The repository conventions are the enforcement mechanism for the architectural principles.

**Anti-patterns explicitly rejected:**

- Speculative abstraction layers added for hypothetical future needs
- Configuration options nobody uses
- Dead code, commented-out code, and unused files kept "just in case"
- Broad exception handling that hides real failures
- Business logic embedded in delivery or persistence code
- Dependencies pulled in for a single trivial utility function

---

## Document Governance

**Authority.** This document is the baseline understanding for FactoryFlow AI. Requirements documents, design documents, task breakdowns, and implementation must remain consistent with it.

**Change control.** If implementation reality requires a change to the vision, scope, architecture, or principles described here, this document is updated first and the rationale is recorded. Silent divergence between documentation and implementation is treated as a defect.

**Non-negotiable constraints.** The following may not be changed without explicitly revising this document:

1. The platform is advisory and never controls machines.
2. The factory manager holds final decision authority.
3. Each component has exactly one responsibility.
4. Every AI output is explainable and traceable.
5. Every recommendation satisfies the full explainability contract in §16.5 — supporting evidence, ML confidence, root cause, business impact, and recommended action.
6. The operational database is the single source of truth.
7. Complexity must be justified; unnecessary technology is excluded.
8. Implementation adheres to the Repository Philosophy.
