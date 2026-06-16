# VulcanOps

**Autonomous Reliability Operations Platform**

VulcanOps is a lightweight, agentic industrial maintenance platform. It ingests machine registry, sensor, maintenance, manual, and SOP data; runs a multi-agent LangGraph pipeline autonomously; and surfaces the results through a clean three-tab dashboard for operators, engineers, supervisors, and managers.

> Built for hackathons and demos: minimal dependencies, containerised data services, and a production-ready React + FastAPI split.

## What it does

- **Data Ingestion** — drag-and-drop CSV/PDF uploads. File types are detected by content/headers, not filenames.
- **Autonomous Pipeline** — runs anomaly detection, RUL prediction, evidence retrieval, diagnosis, verification, impact assessment, maintenance strategy, plant priority, and role reporting for every machine.
- **Chat Copilot** — multi-turn conversation that persists history. Ask about machines, RUL, risk, or low-confidence diagnoses.
- **Reports Browser** — reports grouped by ingestion date, with Engineer / Supervisor / Manager views and PDF export.
- **Circuit Breaker** — lightweight resilience layer around the OpenRouter/DeepSeek LLM gateway. After 3 consecutive failures it returns safe fallbacks and auto-recovers after a cooldown.

## Stack

| Layer          | Technology                          |
|----------------|-------------------------------------|
| Frontend       | React 18 + TypeScript + Vite        |
| Backend        | FastAPI + Python 3.11+              |
| Database       | PostgreSQL 16                       |
| Time-series    | InfluxDB 2.7                        |
| Cache/Queue    | Redis 7                             |
| AI orchestration | LangGraph + OpenRouter (DeepSeek) |

## Project structure

```
VulcanOps/
├── backend/                 # FastAPI application
│   ├── app/
│   │   ├── api/v1/routes/   # HTTP routes (ingest, chat, reports)
│   │   ├── agents/          # 9 reliability agents
│   │   ├── orchestrator/    # LangGraph builder & runner
│   │   ├── services/        # LLM service, ingestion, reports, circuit breaker
│   │   ├── models/          # SQLAlchemy ORM models
│   │   └── core/            # config, enums, state contract
│   ├── alembic/             # Database migrations
│   ├── tests/               # Test placeholder
│   └── requirements.txt
├── frontend/                # React + Vite application
│   └── src/
│       ├── pages/           # Platform shell
│       ├── components/tabs/ # Data Ingestion, Chat, Reports
│       └── theme.ts         # Shared light-dashboard theme
├── infra/                   # Architecture docs
├── test_data/               # Sample CSVs and PDFs
├── docker-compose.yml       # Postgres, Redis, InfluxDB
├── .env.example             # Environment template

```

## Quick start

### Prerequisites
- Docker & Docker Compose
- Python 3.11+
- Node.js 20+

### 1. Environment
```bash
cp .env.example .env
# Edit .env and add your OPENROUTER_API_KEY

cd frontend && cp .env.example .env && cd ..
# Optional: set VITE_API_URL in frontend/.env for local dev with a remote backend.
# By default Vite proxies /api to localhost:8000.
```

### 2. Start data services
```bash
docker-compose up -d
```

### 3. Backend
```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate

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

Then open `http://localhost:5173` (or the port Vite selects).

## Running with sample data

Use the files in `test_data/` to exercise the full flow:

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
|----------|-------------|
| `POST /api/v1/ingest` | Upload mixed CSV/PDF files and trigger pipeline |
| `GET /api/v1/ingest/status` | Latest ingestion status + summary cards |
| `POST /api/v1/chat` | Industrial copilot query |
| `GET /api/v1/chat/history` | Recent conversation turns |
| `GET /api/v1/reports` | List ingestion events with batch summaries |
| `GET /api/v1/reports/event/{id}` | Full event with all machine batches |
| `GET /api/v1/reports/batch/{id}/pdf?role=engineer` | PDF export |

Interactive docs: `http://localhost:8000/docs`

## Deployment

### Frontend (Vercel)

1. Add a `frontend/` project on Vercel.
2. Set the environment variable: `VITE_API_URL=https://your-backend.onrender.com`.
3. Deploy. Vercel will run `npm run build` automatically.

### Backend (Render)

1. Create a **Web Service** from `backend/`. Use:
   - Build command: `pip install -r requirements.txt`
   - Start command: `./start.sh` (or `bash start.sh` if the executable bit is not set)
   - Python version: `backend/.python-version` pins `3.11.10` (Render reads this file; `runtime.txt` is not supported)
2. Set environment variables:
   - `DATABASE_URL=postgresql+asyncpg://user:pass@host:5432/db` (Render also accepts `postgres://`; config converts it)
   - `ALLOWED_ORIGINS=https://your-frontend.vercel.app,http://localhost:5173`
   - `OPENROUTER_API_KEY=sk-...`
   - `APP_ENV=production`
3. Render runs `alembic upgrade head` before starting uvicorn. Uploaded files are stored on the service's ephemeral disk.

### Required environment variables

- Backend: see `backend/.env.example`
- Frontend: see `frontend/.env.example`

## Circuit breaker

The LLM gateway (`backend/app/services/llm_service.py`) is wrapped by a small in-process circuit breaker (`backend/app/services/circuit_breaker.py`):

- **CLOSED** — normal calls.
- **OPEN** — after 3 consecutive failures, no calls hit OpenRouter; safe fallbacks are returned immediately.
- **HALF_OPEN** — after 60 seconds one probe is allowed; success closes the circuit, failure re-opens it.

Diagnosis fallback:
- `root_cause`: `"manual inspection required"`
- `failure_mode`: `"insufficient evidence"`
- `confidence`: `0.2`

Communication fallback (all roles):
- `"Evidence is insufficient to determine root cause. Perform manual inspection before repair actions."`

## Reliability pipeline agents

1. Anomaly detection
2. Prognostics (RUL)
3. Evidence retrieval
4. Diagnosis (LLM #1)
5. Evidence verification
6. Operational impact
7. Maintenance strategy
8. Plant priority
9. Communication (LLM #2)

When diagnosis confidence is below `0.70`, the pipeline deliberately suppresses unsupported root causes and repair instructions, surfacing a manual-inspection message instead.

## Default ports

| Service    | URL                        |
|------------|----------------------------|
| Frontend   | http://localhost:5173      |
| Backend    | http://localhost:8000      |
| API Docs   | http://localhost:8000/docs |
| PostgreSQL | localhost:5432             |
| Redis      | localhost:6379             |
| InfluxDB   | http://localhost:8086      |

## License

MIT — built for demo and hackathon use.
