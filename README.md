# VulcanOps — Agentic Maintenance Decision Support

Multi-agent LangGraph pipeline that ingests industrial machine data, runs autonomous diagnosis and prognosis, and surfaces role-filtered recommendations and real-time alerts to engineers, supervisors, and managers.

## Submission summary for evaluators

This README opens with a requirement coverage matrix that maps every PS §4–§9 line item to the exact file that implements it, so completeness can be verified without reading code. All "Where" links point to real files; items that are absent or partial are marked honestly. The full setup, API reference, and architectural notes follow the matrix.

---

## 1. Requirement Coverage Matrix

### Functional Requirements (PS §6)

| # | Required Capability | VulcanOps Implementation | Where |
|---|---|---|---|
| 6.1 | Contextual reasoning using LLMs/SLMs | Three ReAct LLM agents (diagnosis, evidence verification, maintenance strategy) plus a single-shot LLM planner (supervisor) and role-report generator (communication). All calls go through one circuit-breaker-wrapped service; safe fallbacks fire on repeated LLM failure. | [diagnosis_agent.py](backend/app/agents/diagnosis_agent.py), [evidence_verification_agent.py](backend/app/agents/evidence_verification_agent.py), [maintenance_strategy_agent.py](backend/app/agents/maintenance_strategy_agent.py), [supervisor_agent.py](backend/app/agents/supervisor_agent.py), [communication_agent.py](backend/app/agents/communication_agent.py), [llm_service.py](backend/app/services/llm_service.py), [circuit_breaker.py](backend/app/services/circuit_breaker.py) |
| 6.2 | Knowledge integration (manuals, SOPs, history, logs) | PDFs (manuals + SOPs) chunked and embedded with fastembed (BAAI/bge-small-en-v1.5, 384-dim) into `document_chunks` with pgvector cosine retrieval. Maintenance history ingested to `maintenance_records`. Sensor history in `sensor_readings`. Spare-parts inventory in `spare_parts`. Failure reports are not a separate entity — they arrive as maintenance records. | [document_ingestion_service.py](backend/app/services/document_ingestion_service.py), [embedding_service.py](backend/app/services/embedding_service.py), [evidence_retrieval_agent.py](backend/app/agents/evidence_retrieval_agent.py), [005_add_document_chunks_pgvector.py](backend/alembic/versions/005_add_document_chunks_pgvector.py) |
| 6.3 | Natural-language multi-turn interaction | LangGraph chat graph with pronoun resolution, HITL clarification pause, and DB-backed session persistence (PostgreSQL via custom `VulcanOpsCheckpointer`). Deterministic keyword intent router covers 11 intents with no LLM needed. Frontend chat tab is live. | [chat.py](backend/app/api/v1/routes/chat.py), [chat_graph.py](backend/app/orchestrator/chat_graph.py), [chat_checkpointer.py](backend/app/services/chat_checkpointer.py), [query_router.py](backend/app/services/query_router.py), [ChatPanel.tsx](frontend/src/components/chat/ChatPanel.tsx) |
| 6.4 | Explainable recommendations | Diagnosis stores a full ReAct reasoning trace (thought → tool-call → observation per iteration). Finalize node computes `evidence_chain` and `explainability_score`. Verification contradictions and revision count are surfaced in every report JSON. Three role-specific report depths (engineer, supervisor, manager). | [diagnosis_agent.py](backend/app/agents/diagnosis_agent.py), [graph_builder.py](backend/app/orchestrator/graph_builder.py), [report_builder.py](backend/app/services/report_builder.py), [state_contract.py](backend/app/core/state_contract.py) |
| 6.5 | Abnormality detection & failure prediction | Threshold-based anomaly detection across five sensor types. Linear-regression RUL with R²-weighted confidence. Early-warning alert fires when RUL < 48 h. Process-related defect detection (quality, yield) is **not implemented** — detection is limited to sensor thresholds. | [anomaly_agent.py](backend/app/agents/anomaly_agent.py), [prognostics_agent.py](backend/app/agents/prognostics_agent.py), [graph_builder.py](backend/app/orchestrator/graph_builder.py) |
| 6.6 | Feedback-driven improvement | Engineers submit thumbs/verdict/actual-root-cause via REST. Feedback is stored in `engineer_feedback` and retrieved by a 3-tier heuristic (same machine+failure-mode → same failure-mode → same machine). Matching feedback is injected into the diagnosis LLM context on the next run; a demonstrated test case shows root-cause flipping from "seal assembly failure" to "coupling misalignment" purely from prior feedback context. | [feedback.py](backend/app/api/v1/routes/feedback.py), [feedback_retrieval.py](backend/app/services/feedback_retrieval.py), [graph_builder.py](backend/app/orchestrator/graph_builder.py), [007_add_engineer_feedback.py](backend/alembic/versions/007_add_engineer_feedback.py) |
| 6.7 | Real-time alerting | In-process asyncio pub/sub (`AlertBus`) fans alerts to role-filtered WebSocket subscribers. Four alert types with role targets below. Backend API fully implemented; frontend WebSocket client is **not implemented** (no WS consumer in `frontend/src/`). | [alert_bus.py](backend/app/services/alert_bus.py), [alerts.py](backend/app/api/v1/routes/alerts.py) |

**Alert type → role mapping (§6.7):**

| Alert type | Trigger | Roles |
|---|---|---|
| `critical_anomaly` | sensor deviation > 50% above critical threshold | engineer, supervisor, manager |
| `low_rul` | RUL < 48 h | engineer, supervisor, manager |
| `high_risk_job` | pipeline finalizes with risk = high or critical | supervisor, manager |
| `contested_diagnosis` | feedback submitted with `verdict = "wrong"` | engineer, supervisor |

---

### Expected Inputs (PS §4)

| # | Input Type | Ingestion Path | Where |
|---|---|---|---|
| 4.1 | Operational & failure data (fault messages, maintenance logs) | CSV upload to `POST /upload/maintenance` → `maintenance_records` table. Delay logs and incident records as distinct entities are **not implemented**; all operational history arrives as maintenance records. | [maintenance_ingestion_service.py](backend/app/services/maintenance_ingestion_service.py), [upload.py](backend/app/api/v1/routes/upload.py) |
| 4.2 | Condition monitoring (sensor data, anomaly indicators) | CSV upload to `POST /upload/sensors` → `sensor_readings` table (timestamp, temperature, vibration, pressure, load, rpm). Anomaly agent runs automatically on every ingestion. | [sensor_ingestion_service.py](backend/app/services/sensor_ingestion_service.py), [anomaly_agent.py](backend/app/agents/anomaly_agent.py) |
| 4.3 | Knowledge & documentation (manuals, SOPs, spare-parts inventory) | PDFs to `POST /upload/manuals` or `/upload/sops` → chunked + embedded → `document_chunks` (pgvector). Spare-parts CSV to `POST /upload/spares` → `spare_parts` table with lead-time and cost. Machine registry CSV to `POST /upload/machines`. | [document_ingestion_service.py](backend/app/services/document_ingestion_service.py), [spares_ingestion_service.py](backend/app/services/spares_ingestion_service.py), [machine_ingestion_service.py](backend/app/services/machine_ingestion_service.py) |
| 4.4 | User interaction (NL queries, multi-turn follow-ups) | `POST /api/v1/chat` with optional `session_id`. Pronoun resolution and HITL clarification handled in LangGraph graph. Session memory persists across restarts. | [chat.py](backend/app/api/v1/routes/chat.py), [chat_graph.py](backend/app/orchestrator/chat_graph.py) |

---

### Expected Outputs (PS §5)

| Category | Delivered As | Where |
|---|---|---|
| **5.1 Diagnostic & predictive** | `root_cause`, `failure_mode`, `confidence` from diagnosis agent. `hours_remaining`, `confidence`, `basis`, `sensor_trends` from prognostics agent. Early-warning alert at 48 h RUL. Process-related defect detection is **not implemented**. | [diagnosis_agent.py](backend/app/agents/diagnosis_agent.py), [prognostics_agent.py](backend/app/agents/prognostics_agent.py) |
| **5.2 Risk & priority** | `risk_level` (low/medium/high/critical) from operational impact agent. `priority_score` (0–100) and `priority_rank` (P1–P4) from plant priority agent; weights: criticality 35%, anomaly severity 25%, RUL 25%, risk level 15%. Spare lead-time vs RUL gap exposed as `procurement_gap` in report. | [operational_impact_agent.py](backend/app/agents/operational_impact_agent.py), [plant_priority_agent.py](backend/app/agents/plant_priority_agent.py), [graph_builder.py](backend/app/orchestrator/graph_builder.py) |
| **5.3 Maintenance recommendations** | `StrategyDecision` with `recommended_action`, `parts_required`, `procurement_strategy`, and `constraint_violations`. Parts cross-checked against live `spare_parts` inventory. Role-specific recommended actions in engineer/supervisor/manager reports. | [maintenance_strategy_agent.py](backend/app/agents/maintenance_strategy_agent.py), [communication_agent.py](backend/app/agents/communication_agent.py) |
| **5.4 Reporting** | Structured JSON stored in `report_batches` + `stored_role_reports`. Three role views (engineer, supervisor, manager) with PDF export. Alert reports are transient (in-memory pub/sub, not persisted). Digital logbook as a distinct named feature is **not implemented**; `report_batches` acts as a de facto log. | [reports.py](backend/app/api/v1/routes/reports.py), [pdf_service.py](backend/app/services/pdf_service.py), [report_persistence_service.py](backend/app/services/report_persistence_service.py) |

---

### Optional Enhancements (PS §7)

| Enhancement | Status | Where |
|---|---|---|
| Conversational interface for maintenance engineers | Implemented — multi-turn chat copilot with session memory, pronoun resolution, and HITL clarification | [chat_graph.py](backend/app/orchestrator/chat_graph.py), [ChatPanel.tsx](frontend/src/components/chat/ChatPanel.tsx) |
| Visualization dashboard | Partial — 3-tab React UI (Ingest / Chat / Reports) with plant overview summary card and machine risk cards. No live sensor time-series charts. | [PlatformPage.tsx](frontend/src/pages/PlatformPage.tsx), [CopilotPanel.tsx](frontend/src/components/chat/CopilotPanel.tsx) |
| IoT / equipment monitoring dashboard integration | Not implemented — sensor data arrives via CSV upload only; no live IoT stream or push ingest endpoint | — |
| Dynamic per-equipment knowledge base | Implemented — `document_chunks` indexed by content; `evidence_retrieval_agent` queries per-machine using pgvector cosine similarity; spare-parts and machine registry updatable via CSV upload at any time | [evidence_retrieval_agent.py](backend/app/agents/evidence_retrieval_agent.py), [embedding_service.py](backend/app/services/embedding_service.py) |
| Automatic digital logbook | Not implemented as a distinct feature — `report_batches` and `stored_role_reports` tables function as a de facto log, but there is no dedicated logbook UI or export | — |
| User-role-based alerts and recommendations | Implemented — WebSocket alerts are role-filtered at the channel level; communication agent generates three distinct report views (engineer / supervisor / manager) | [alert_bus.py](backend/app/services/alert_bus.py), [alerts.py](backend/app/api/v1/routes/alerts.py), [communication_agent.py](backend/app/agents/communication_agent.py) |

---

### Deliverables (PS §9)

| Deliverable | Status | Where |
|---|---|---|
| Working prototype source code | Complete — full backend (FastAPI + LangGraph) and frontend (React + Vite) | [backend/](backend/), [frontend/](frontend/) |
| System architecture doc | Complete — component diagram, deep-analysis pipeline Mermaid graph, and chat copilot graph in §2 of this README | [§2 System Architecture](#2-system-architecture) |
| Tech stack doc | Complete — stack table in this README | [Stack table](#stack) below |
| Data flow / system flow | Complete | [infra/DATA_FLOW.md](infra/DATA_FLOW.md) |
| Model design & reasoning pipeline | Partial — agent pipeline described in this README and in each agent's module docstring; no dedicated standalone document | [Reliability pipeline agents](#reliability-pipeline-agents) below |
| Alerting & prediction logic | Complete — documented in module docstrings with thresholds and role-routing table | [alert_bus.py](backend/app/services/alert_bus.py), [anomaly_agent.py](backend/app/agents/anomaly_agent.py), [prognostics_agent.py](backend/app/agents/prognostics_agent.py) |
| Assumptions & limitations | Complete — consolidated in §8 of this README (data, LLM, persistence, security, scale) | [§8 Assumptions & Limitations](#8-assumptions--limitations) |
| Install / run docs | Complete | [Quick start](#quick-start) below |
| Sample input / output demo | Complete — §10 covers deep-analysis report with feedback flip, feedback POST shape, 4-turn HITL chat, all 4 alert types, and spare-parts upload | [§10 Sample I/O](#10-sample-input--output-demonstration) |
| Screen recording | Not produced | — |

---

## 2. System Architecture

### 2.1 Component Diagram

```mermaid
graph LR
    subgraph Client
        UI[React UI\n3-tab shell]
    end

    subgraph FastAPI["FastAPI  (backend/app)"]
        REST[REST routes\n/upload /chat /reports /feedback]
        WS[WebSocket\n/ws/alerts/:role]
    end

    subgraph Orchestration["LangGraph Orchestration"]
        PIPE[Deep-Analysis Pipeline\ngraph_builder.py]
        CHAT[Chat Copilot Graph\nchat_graph.py]
    end

    subgraph Storage["Storage"]
        PG[(PostgreSQL 16\n+ pgvector)]
        INFLUX[(InfluxDB 2.7)]
    end

    subgraph AI["AI Services"]
        LLM[LLM Gateway\nllm_service.py\nOllama / OpenAI-compat]
        EMBED[fastembed\nBAAI bge-small-en-v1.5]
    end

    subgraph InProcess["In-Process"]
        BUS[AlertBus\nasyncio pub/sub]
        CKPT[VulcanOpsCheckpointer\nPostgreSQL-backed]
    end

    UI -->|HTTP| REST
    UI <-->|WebSocket| WS
    REST --> PIPE
    REST --> CHAT
    PIPE --> LLM
    PIPE --> EMBED
    PIPE --> PG
    PIPE --> INFLUX
    CHAT --> LLM
    CHAT --> PG
    CHAT --> CKPT
    CKPT --> PG
    PIPE -->|publish| BUS
    REST -->|publish| BUS
    BUS --> WS
```

### 2.2 Deep Analysis Pipeline

Verbatim output of `compiled_graph.draw_mermaid()`:

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	supervisor_agent(supervisor_agent)
	anomaly_agent(anomaly_agent)
	prognostics_agent(prognostics_agent)
	evidence_retrieval_agent(evidence_retrieval_agent)
	diagnosis_agent(diagnosis_agent)
	evidence_verification_agent(evidence_verification_agent)
	operational_impact_agent(operational_impact_agent)
	maintenance_strategy_agent(maintenance_strategy_agent)
	plant_priority_agent(plant_priority_agent)
	communication_agent(communication_agent)
	finalize_report(finalize_report)
	__end__([<p>__end__</p>]):::last
	__start__ --> supervisor_agent;
	communication_agent --> finalize_report;
	finalize_report --> __end__;
	supervisor_agent -.-> anomaly_agent;
	supervisor_agent -.-> prognostics_agent;
	supervisor_agent -.-> evidence_retrieval_agent;
	supervisor_agent -.-> diagnosis_agent;
	supervisor_agent -.-> evidence_verification_agent;
	supervisor_agent -.-> operational_impact_agent;
	supervisor_agent -.-> maintenance_strategy_agent;
	supervisor_agent -.-> plant_priority_agent;
	supervisor_agent -.-> communication_agent;
	anomaly_agent -.-> prognostics_agent;
	anomaly_agent -.-> evidence_retrieval_agent;
	anomaly_agent -.-> diagnosis_agent;
	anomaly_agent -.-> evidence_verification_agent;
	anomaly_agent -.-> operational_impact_agent;
	anomaly_agent -.-> maintenance_strategy_agent;
	anomaly_agent -.-> plant_priority_agent;
	anomaly_agent -.-> communication_agent;
	prognostics_agent -.-> evidence_retrieval_agent;
	prognostics_agent -.-> diagnosis_agent;
	prognostics_agent -.-> evidence_verification_agent;
	prognostics_agent -.-> operational_impact_agent;
	prognostics_agent -.-> maintenance_strategy_agent;
	prognostics_agent -.-> plant_priority_agent;
	prognostics_agent -.-> communication_agent;
	evidence_retrieval_agent -.-> diagnosis_agent;
	evidence_retrieval_agent -.-> evidence_verification_agent;
	evidence_retrieval_agent -.-> operational_impact_agent;
	evidence_retrieval_agent -.-> maintenance_strategy_agent;
	evidence_retrieval_agent -.-> plant_priority_agent;
	evidence_retrieval_agent -.-> communication_agent;
	diagnosis_agent -.-> evidence_verification_agent;
	diagnosis_agent -.-> operational_impact_agent;
	diagnosis_agent -.-> maintenance_strategy_agent;
	diagnosis_agent -.-> plant_priority_agent;
	diagnosis_agent -.-> communication_agent;
	operational_impact_agent -.-> maintenance_strategy_agent;
	operational_impact_agent -.-> plant_priority_agent;
	operational_impact_agent -.-> communication_agent;
	maintenance_strategy_agent -.-> plant_priority_agent;
	maintenance_strategy_agent -.-> communication_agent;
	plant_priority_agent -.-> communication_agent;
	evidence_verification_agent -.-> diagnosis_agent;
	evidence_verification_agent -.-> operational_impact_agent;
	evidence_verification_agent -.-> maintenance_strategy_agent;
	evidence_verification_agent -.-> plant_priority_agent;
	evidence_verification_agent -.-> communication_agent;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

Dashed arrows (`-.->`) are conditional edges decided at runtime by `supervisor_agent`. Solid arrows (`-->`) are unconditional. All conditional edges originate from the previous agent in pipeline order, allowing each agent to send control directly to any later-stage agent, enabling skip-forward without a central router re-entry.

### 2.3 Chat Copilot Graph

Verbatim output of `chat_graph.draw_mermaid()`:

```mermaid
---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([<p>__start__</p>]):::first
	analyze(analyze)
	request_clarification(request_clarification<hr/><small><em>__interrupt = before</em></small>)
	execute_query(execute_query)
	__end__([<p>__end__</p>]):::last
	__start__ --> analyze;
	execute_query --> __end__;
	request_clarification --> execute_query;
	analyze -.-> request_clarification;
	analyze -.-> execute_query;
	analyze -.-> __end__;
	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
```

`__interrupt = before` means LangGraph pauses execution before `request_clarification` fires; the HTTP response returns `status: "needs_clarification"`. The next POST with the same `session_id` calls `aupdate_state()` then `ainvoke(None, config)` to resume without replaying earlier nodes.

---

## 3. What's distinctive

- **Three genuine ReAct agents** — diagnosis, evidence verification, and maintenance strategy each run tool-calling loops (up to 4, 3, and 4 iterations respectively), not single-shot prompts.
- **Verification → diagnosis back-edge** — evidence_verification_agent can recommend `revise_diagnosis`, which triggers a LangGraph cycle back to diagnosis_agent; the cycle fires demonstrably when contradictory maintenance records are present.
- **Local-first SLM option** — `Modelfile` defines `vulcanops-qwen` (Qwen 2.5 7B Instruct, num_ctx 8192) for fully offline operation; the OpenAI-compatible `llm_service.py` switches endpoint via `LLM_BASE_URL`.
- **Engineer feedback loop with demonstrated diagnosis flip** — prior engineer corrections are retrieved by a 3-tier heuristic and injected into the diagnosis LLM context; root-cause changed from "seal assembly failure" to "coupling misalignment" in a documented end-to-end test.
- **HITL clarification in chat copilot** — LangGraph `interrupt_before` pauses the graph when a query contains unresolvable pronouns; the session resumes with the user's next message without losing state.
- **Role-filtered real-time WebSocket alerts** — four alert types with distinct role targets; `AlertBus.publish()` is sync so agents never block the event loop; dead queues are drained in `finally` on disconnect.
- **Adaptive pipeline planning** — `supervisor_agent` uses a heuristic + LLM to decide which of the nine downstream agents to skip per run (e.g. skips evidence retrieval when no documents are ingested), reducing unnecessary LLM calls.

---

## What it does

- **Data Ingestion** — drag-and-drop CSV/PDF uploads. File types are detected by content/headers, not filenames.
- **Autonomous Pipeline** — runs anomaly detection, RUL prediction, evidence retrieval, diagnosis, verification, impact assessment, maintenance strategy, plant priority, and role reporting for every machine.
- **Chat Copilot** — multi-turn conversation that persists history. Ask about machines, RUL, risk, or low-confidence diagnoses.
- **Reports Browser** — reports grouped by ingestion date, with Engineer / Supervisor / Manager views and PDF export.
- **Circuit Breaker** — lightweight resilience layer around the LLM gateway. After 3 consecutive failures it returns safe fallbacks and auto-recovers after a cooldown.

## Stack

| Layer | Technology |
|---|---|
| Frontend | React 18 + TypeScript + Vite |
| Backend | FastAPI + Python 3.11+ |
| AI orchestration | LangGraph 0.2 + OpenAI-compatible endpoint |
| Local SLM | Qwen 2.5 7B Instruct via Ollama (`Modelfile`) |
| Database | PostgreSQL 16 + pgvector |
| Embeddings | fastembed (BAAI/bge-small-en-v1.5, local, no API key) |
| Time-series | InfluxDB 2.7 |

## Project structure

```
VulcanOps/
├── backend/
│   ├── app/
│   │   ├── agents/          # 9 reliability agents (anomaly, prognostics, evidence,
│   │   │                    #   diagnosis, verification, impact, strategy, priority, comms)
│   │   ├── orchestrator/    # LangGraph pipeline graph + chat graph
│   │   ├── api/v1/routes/   # HTTP + WebSocket routes
│   │   ├── services/        # LLM, ingestion, alerts, checkpointer, feedback
│   │   ├── models/          # SQLAlchemy ORM models
│   │   └── core/            # config, enums, VulcanOpsState contract
│   ├── alembic/versions/    # 8 migrations (001–008)
│   └── requirements.txt
├── frontend/
│   └── src/
│       ├── pages/           # PlatformPage (3-tab shell)
│       ├── components/tabs/ # DataIngestion, Chat, Reports tabs
│       └── components/chat/ # ChatPanel, CopilotPanel, ResultsPanel
├── infra/                   # DATA_FLOW.md
├── test_data/               # 5 sample input files (CSV + PDF)
├── Modelfile                # Ollama local-SLM definition (Qwen 2.5 7B)
└── docker-compose.yml       # PostgreSQL, InfluxDB
```

## 8. Assumptions & Limitations

### Data

- **Sensor data is assumed clean at upload time.** There is no outlier rejection or missing-value imputation before anomaly detection. A single bad CSV row can distort the anomaly baseline.
- **Timestamps must be ISO-8601 or parseable by `pd.to_datetime`.** Non-parseable rows are silently skipped by the ingestion services.
- **RUL prediction requires at least 3 sensor readings with monotonic trend.** With fewer points, or when R² < 0.3, the prognostics agent returns `null` RUL and skips the low-RUL alert.
- **Maintenance records must use consistent machine names or IDs.** Fuzzy-matching across name variants is not implemented; a machine listed as "Pump 2" vs "Cooling Pump 2" is treated as two different assets.
- **Spare-parts inventory is point-in-time.** The `spare_parts` table reflects the last CSV upload; there is no live ERP connection or delta-update mechanism.

### LLM

- **All LLM calls are routed through a single OpenAI-compatible endpoint** (`LLM_BASE_URL` / `LLM_API_KEY`). The `Modelfile` defines a local Qwen 2.5 7B variant (`vulcanops-qwen`) for fully offline use; quality degrades vs. GPT-4-class models, particularly for evidence cross-referencing.
- **ReAct agents are capped at 4 iterations** (diagnosis), 3 (evidence verification), and 4 (maintenance strategy) to bound latency. Complex multi-cause failures may exhaust the cap before converging.
- **The supervisor agent planner uses a heuristic first, LLM second.** If the LLM planner fails (circuit breaker open), the full agent list runs. There is no graceful partial skip in that fallback.
- **Prompt templates are in English.** Non-English maintenance records or manuals will be processed but reasoning quality is not validated.

### Persistence & Session

- **`VulcanOpsCheckpointer` uses msgpack BYTEA blobs** instead of `AsyncPostgresSaver` from `langgraph-checkpoint-postgres` because `psycopg` v3 requires `libpq`, which is unavailable on the development machine. The interface is identical; swap the class if `libpq` becomes available.
- **Chat session memory survives server restarts** (stored in `chat_checkpoints` table). Sessions are not garbage-collected; the table will grow indefinitely without a manual `DELETE` or TTL job.
- **`AlertBus` is in-process asyncio pub/sub.** Alerts are lost if no WebSocket client is subscribed at the moment of publish. In a multi-worker deployment (Gunicorn + multiple Uvicorn processes) each worker has its own `AlertBus` singleton — cross-worker delivery requires replacing `AlertBus` with a Redis pub/sub adapter.

### Security

- **No authentication on any endpoint.** The API is designed for a controlled demo environment. All routes, including feedback submission and alert subscriptions, are open.
- **CORS is configured from `ALLOWED_ORIGINS` env var.** Default is `*` in the example `.env`.
- **WebSocket role parameter is not validated against a user session.** Any caller can connect as `engineer`, `supervisor`, or `manager`.

### Scale

- **Single Uvicorn worker assumed throughout.** `AlertBus`, `VulcanOpsCheckpointer`, and `llm_service` circuit breaker all use in-process state.
- **pgvector index is IVFFlat with 100 lists.** For corpora > 1 M document chunks, recall may degrade without re-indexing (`REINDEX INDEX`).
- **Deep-analysis pipeline runs as a `BackgroundTasks` task** (FastAPI), not a persistent job queue. If the server restarts mid-analysis, the job silently disappears. The `deep_analysis_jobs` table records status but there is no resume or retry mechanism.

---

## 10. Sample Input & Output Demonstration

All JSON below is from real runs against the live system unless otherwise noted.

### 10.1 Deep Analysis — Feedback-Influenced Diagnosis

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/reports/deep-analyze/bd9c66b3-ad3c-2d6d-1a3d-1fa7bc8960a9
```

**Stored report** (batch `9b9a6d73-658f-4bde-9e2a-93c01d8bd495`, machine: Cooling Pump 2, retrieved via `GET /api/v1/reports/9b9a6d73-658f-4bde-9e2a-93c01d8bd495`):

```json
{
  "report_id": "9b9a6d73-658f-4bde-9e2a-93c01d8bd495",
  "machine_id": "bd9c66b3-ad3c-2d6d-1a3d-1fa7bc8960a9",
  "generated_at": "2026-06-17T11:39:33.909158+00:00",
  "final_report_status": "specific",
  "fallback_used": false,
  "pipeline_errors": 0,
  "root_cause": "Coupling misalignment caused the seal vibration",
  "failure_mode": "Seal Leakage",
  "confidence": 0.85,
  "risk_level": "high",
  "rul_hours": null,
  "recommended_action": "Reduce load on Cooling Pump 2 to prevent further damage.",
  "estimated_cost_usd": 52000.0,
  "estimated_downtime_hours": 5.2,
  "procurement_gap": true,
  "constraint_violations": ["bearing out of stock, 21d lead"],
  "explainability_score": 70,
  "prior_feedback_considered": [
    {
      "verdict": "wrong",
      "failure_mode": "Seal Leakage",
      "actual_root_cause": "Coupling misalignment caused the seal vibration",
      "notes": "On inspection, seal was intact. Coupling spider was cracked."
    }
  ],
  "agents_skipped": ["prognostics_agent", "plant_priority_agent", "evidence_retrieval_agent"],
  "skip_reasons": {
    "prognostics_agent": "insufficient sensor data for trend analysis",
    "plant_priority_agent": "skipped by supervisor",
    "evidence_retrieval_agent": "no documents ingested"
  }
}
```

Note: `root_cause` flipped from the original "seal assembly failure" to "Coupling misalignment caused the seal vibration" because a prior engineer correction was retrieved and injected into the diagnosis LLM context. `prior_feedback_considered` shows the evidence that drove the flip.

### 10.2 Engineer Feedback Submission

The `engineer_feedback` table was cleared after the test run that produced the §10.1 report. The request/response shape is shown from code ([feedback.py](backend/app/api/v1/routes/feedback.py)):

**Request:**
```bash
curl -X POST http://localhost:8000/api/v1/feedback \
  -H "Content-Type: application/json" \
  -d '{
    "report_batch_id": "9b9a6d73-658f-4bde-9e2a-93c01d8bd495",
    "machine_id": "bd9c66b3-ad3c-2d6d-1a3d-1fa7bc8960a9",
    "role": "engineer",
    "verdict": "wrong",
    "failure_mode": "Seal Leakage",
    "actual_root_cause": "Coupling misalignment caused the seal vibration",
    "notes": "On inspection, seal was intact. Coupling spider was cracked."
  }'
```

**Response (201 Created):**
```json
{
  "id": "<uuid>",
  "report_batch_id": "9b9a6d73-658f-4bde-9e2a-93c01d8bd495",
  "machine_id": "bd9c66b3-ad3c-2d6d-1a3d-1fa7bc8960a9",
  "role": "engineer",
  "verdict": "wrong",
  "failure_mode": "Seal Leakage",
  "actual_root_cause": "Coupling misalignment caused the seal vibration",
  "notes": "On inspection, seal was intact. Coupling spider was cracked.",
  "created_at": "<iso-timestamp>"
}
```

A `contested_diagnosis` WebSocket alert is published synchronously on verdict = `"wrong"` (see §10.4).

### 10.3 Multi-Turn Chat with HITL Clarification

Session state persists in `chat_checkpoints` table across turns and server restarts. Captured from [scripts/test_p16_chat_session.py](backend/scripts/test_p16_chat_session.py):

**Turn 1 — direct query (session A):**
```
POST /api/v1/chat  {"query": "Show me the status of all machines", "session_id": "demo-session-A"}
-> status: "answered"
   routing_intent: "investigate_machine"
   last_machine_id: "23b8c1e9-..."   (Blast Furnace 1, highest-risk machine returned first)
```

**Turn 2 — pronoun resolved from session context:**
```
POST /api/v1/chat  {"query": "What is its RUL?", "session_id": "demo-session-A"}
-> status: "answered"
   routing_intent: "rul_query"
   resolved_query: "What is machine 23b8c1e9-... RUL?"
```

**Turn 3 — pronoun with no prior context (session B, fresh):**
```
POST /api/v1/chat  {"query": "Show me its maintenance history.", "session_id": "demo-session-B"}
-> status: "needs_clarification"
   clarification_question: "Which machine are you referring to? Please specify the machine name or ID."
```
Graph paused at `request_clarification` node via `interrupt_before`. Next POST resumes without replaying Turn 3.

**Turn 4 — HITL resume:**
```
POST /api/v1/chat  {"query": "Cooling Pump 2", "session_id": "demo-session-B"}
-> status: "answered"
   resolved_query: "Show me its maintenance history. (clarification: Cooling Pump 2)"
   last_machine_id: "bd9c66b3-..."
```

### 10.4 Real-Time Alert Stream

Connect as engineer:
```bash
wscat -c ws://localhost:8000/api/v1/ws/alerts/engineer
```

Handshake frame (sent immediately on connect):
```json
{"type": "connected", "role": "engineer", "server_time": "2026-06-17T11:39:00.000Z"}
```

All four alert types captured from [scripts/test_p20_alerts.py](backend/scripts/test_p20_alerts.py):

**`critical_anomaly`** (engineer + supervisor + manager):
```json
{
  "type": "critical_anomaly",
  "severity": "critical",
  "machine_name": "Blast Furnace 1",
  "title": "Critical anomaly: temperature on Blast Furnace 1",
  "detail": "Sensor temperature reading 102.5 is 20.6% above critical threshold",
  "target_roles": ["engineer", "supervisor", "manager"],
  "links": {}
}
```

**`low_rul`** (engineer + supervisor + manager):
```json
{
  "type": "low_rul",
  "severity": "high",
  "machine_name": "Cooling Pump 2",
  "title": "Low RUL warning: Cooling Pump 2",
  "detail": "Estimated 31h remaining — Linear extrapolation of vibration to critical threshold 10.0",
  "target_roles": ["engineer", "supervisor", "manager"],
  "links": {}
}
```

**`high_risk_job`** (supervisor + manager only — engineer excluded):
```json
{
  "type": "high_risk_job",
  "severity": "high",
  "machine_name": "Robotic Arm 3",
  "title": "High-risk analysis complete: Robotic Arm 3",
  "detail": "Risk level: high — bearing wear in gearbox. Action: Replace bearings and perform lubrication within 24h",
  "target_roles": ["supervisor", "manager"],
  "links": {"report_batch_id": "<uuid>"}
}
```

**`contested_diagnosis`** (engineer + supervisor — manager excluded):
```json
{
  "type": "contested_diagnosis",
  "severity": "medium",
  "machine_name": "Cooling Pump 2",
  "title": "Contested diagnosis: Cooling Pump 2",
  "detail": "Engineer reported root cause 'coupling misalignment' contradicts system diagnosis 'seal assembly failure'",
  "target_roles": ["engineer", "supervisor"],
  "links": {"feedback_id": "<uuid>"}
}
```

### 10.5 Spare Parts CSV Upload

```bash
curl -X POST http://localhost:8000/api/v1/upload/spares \
  -F "file=@test_data/spare_parts.csv"
```

Expected CSV headers: `part_number, part_name, machine_type, quantity_on_hand, unit_cost_usd, lead_time_days`

**Response:**
```json
{
  "status": "ok",
  "rows_ingested": 12,
  "rows_skipped": 0,
  "file_id": "<uuid>"
}
```

Parts are cross-checked against `rul_hours` during maintenance strategy planning; `procurement_gap: true` fires when `lead_time_days > rul_hours / 24`.

---

## Quick start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Node.js 20+

### 1. Environment
```bash
cp .env.example .env
# Edit .env: set LLM_BASE_URL and LLM_API_KEY for your LLM endpoint
# (or run Ollama locally: ollama create vulcanops-qwen -f Modelfile)

cd frontend && cp .env.example .env && cd ..
```

### 2. Start data services
```bash
docker-compose up -d
```

### 3. Backend
```bash
cd backend
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate

pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

### 4. Frontend
```bash
cd frontend
npm install
npm run dev
```

Then open `http://localhost:5173`.

## Running with sample data

```bash
curl -s -X POST http://localhost:8000/api/v1/ingest \
  -F "files=@test_data/machines.csv" \
  -F "files=@test_data/sensor_readings.csv" \
  -F "files=@test_data/maintenance_history.csv" \
  -F "files=@test_data/compressor_manual.pdf" \
  -F "files=@test_data/maintenance_sop.pdf"
```

The pipeline runs in the background. Poll status with:

```bash
curl -s http://localhost:8000/api/v1/ingest/status | python -m json.tool
```

## API overview

| Endpoint | Description |
|---|---|
| `POST /api/v1/ingest` | Upload mixed CSV/PDF files and trigger pipeline |
| `GET /api/v1/ingest/status` | Latest ingestion status + summary cards |
| `POST /api/v1/upload/spares` | Upload spare-parts inventory CSV |
| `POST /api/v1/chat` | Industrial copilot (add `session_id` for multi-turn memory) |
| `GET /api/v1/chat/history` | Recent conversation turns |
| `GET /api/v1/chat/plant-overview` | Aggregate fleet statistics |
| `GET /api/v1/reports` | List ingestion events with batch summaries |
| `GET /api/v1/reports/batch/{id}/pdf?role=engineer` | PDF export (engineer / supervisor / manager) |
| `POST /api/v1/reports/deep-analyze/{machine_id}` | Enqueue on-demand full analysis for one machine |
| `POST /api/v1/reports/{batch_id}/feedback` | Submit engineer feedback (verdict, actual root cause) |
| `GET /api/v1/feedback/recent?machine_id=...` | Recent feedback for a machine |
| `WS /api/v1/ws/alerts/{role}` | Real-time alert stream (role = engineer / supervisor / manager) |

Interactive docs: `http://localhost:8000/docs`

## Deployment

### Frontend (Vercel)

1. Add a `frontend/` project on Vercel.
2. Set `VITE_API_URL=https://your-backend.onrender.com`.
3. Deploy. Vercel runs `npm run build` automatically.

### Backend (Render)

1. Create a Web Service from `backend/`. Use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `./start.sh`
   - Python version: `backend/.python-version` (pins 3.11.10)
2. Set environment variables:
   - `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db`
   - `ALLOWED_ORIGINS=https://your-frontend.vercel.app,http://localhost:5173`
   - `LLM_API_KEY=...`
   - `LLM_BASE_URL=...`
   - `APP_ENV=production`
3. Render runs `alembic upgrade head` before starting uvicorn.

### Required environment variables

- Backend: see `backend/.env.example`
- Frontend: see `frontend/.env.example`

## Circuit breaker

`llm_service.py` is wrapped by `circuit_breaker.py`:

- **CLOSED** — normal calls.
- **OPEN** — after 3 consecutive failures, no calls hit the LLM endpoint; safe fallbacks are returned immediately.
- **HALF_OPEN** — after 60 seconds one probe is allowed; success closes the circuit, failure re-opens it.

Diagnosis fallback: `root_cause = "manual inspection required"`, `failure_mode = "insufficient evidence"`, `confidence = 0.2`.

## Reliability pipeline agents

Ordered execution (supervisor agent may skip stages based on data availability):

1. **Anomaly detection** — deterministic threshold check across 5 sensors
2. **Prognostics (RUL)** — linear regression extrapolated to critical threshold
3. **Evidence retrieval** — pgvector cosine similarity against ingested documents
4. **Diagnosis (LLM #1, ReAct)** — tool-calling loop: retrieve_more, get_sensor_history, search_maintenance, conclude
5. **Evidence verification (LLM #2, ReAct)** — adversarial challenge; can cycle back to step 4
6. **Operational impact** — deterministic: downtime, cost, risk level
7. **Maintenance strategy (LLM #3, ReAct)** — tool-calling: check_spares_inventory, estimate_repair_cost, propose_plan
8. **Plant priority** — deterministic scoring: criticality 35%, severity 25%, RUL 25%, risk 15%
9. **Communication (LLM #4)** — generates Engineer / Supervisor / Manager role reports

When diagnosis confidence is below 0.70 and verification score is low, the finalize node suppresses concrete repair instructions and surfaces a manual-inspection message instead.

## Default ports

| Service | URL |
|---|---|
| Frontend | http://localhost:5173 |
| Backend | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |
| PostgreSQL | localhost:5432 |
| InfluxDB | http://localhost:8086 |

## License

MIT — built for demo and hackathon use.
