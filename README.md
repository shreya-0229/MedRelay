# MedRelay — Autonomous Multi-Agent Emergency Response Network

An autonomous agent network that takes a raw emergency call report and, in seconds, runs it through
six coordinated agents — Intake → Triage → Dispatch ∥ Hospital Liaison → Verification → Communication —
assigning the nearest capable ambulance, reserving a hospital bed, verifying the plan with five safety
checks, and notifying the family in English and Hindi. Every decision is written to an append-only audit
trail and streamed to a live dashboard. This is the Phase-1 full-stack prototype built for
**DecentraHack 2.0 (Agentic AI track)** — final pitch **9 October 2026**.

## Tech stack

| Layer    | Stack                                                                       |
|----------|-----------------------------------------------------------------------------|
| Backend  | Python 3.12, FastAPI, SQLAlchemy 2.0 (sync), Pydantic v2, SQLite, uvicorn    |
| Frontend | React 19 + TypeScript + Vite, Tailwind CSS v3, native WebSocket              |
| LLM      | Deterministic by default; optional Gemini via `MEDRELAY_LLM_PROVIDER=gemini` |

## How to run

```bash
./run.sh          # Linux / Mac
run.bat           # Windows
```

Then open **http://localhost:8000**. That's it.

The script creates `backend/.venv` (isolated Python env — avoids PEP-668 issues), installs the four
backend deps, builds the frontend only if `frontend/dist/` is missing, and starts uvicorn on port 8000.
The backend serves the built dashboard itself, so **one process = API + dashboard**.

Manual steps (if you prefer):

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../frontend && npm install && npm run build
cd ../backend && .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## LLM setup (optional)

Default mode is fully deterministic — no keys needed, identical results every run. To enable the real
model (used for triage on unknown incident types and drafting family messages):

```bash
export MEDRELAY_LLM_PROVIDER=gemini
export MEDRELAY_LLM_KEY=<your AI Studio key>   # never commit this
./run.sh
```

The dashboard header shows the active provider (`deterministic` / `gemini`).

## What works

- **6-agent pipeline per incident** (see [docs/AGENTS.md](docs/AGENTS.md)): Intake, Triage,
  Dispatch + Hospital Liaison (in parallel), Verification gate, Communication — all DB-backed.
- **Safety-first flow**: a verification gate re-checks every assignment against the DB;
  failed steps are retried up to 2× (logged), and anything still failing is escalated to a
  human dispatcher instead of guessing.
- **Live dashboard**: agent activity feed, incident list, 6-stage pipeline view, verification panel,
  escalation banner, family communications, timeline, audit trail — all pushed over WebSocket
  with an HTTP polling fallback.
- **Demo controls**: `POST /api/demo/reset` re-seeds the fleet; force any ambulance
  `out_of_service` for failure drills.
- **Seeded Pune city**: 8 hospitals, 14 ambulances (5 ALS / 9 BLS, 2 out of service).

## 2-minute judge demo script

1. **0:00** — Open `http://localhost:8000`. Point at the header: LIVE WebSocket pill, LLM indicator,
   clock; StatsBar below; empty incident list.
2. **0:15** — Click **+ New incident** → **Fill cardiac-arrest demo** → **Dispatch agents**.
3. **0:25–0:50** — Watch the *Agent activity* feed light up in real time: Intake → Triage →
   Dispatch + Hospital Liaison (parallel) → Verification → Communication (stages pause 0.35 s so the
   dashboard animates).
4. **0:55** — Select the incident: severity **S5**, pathway `cath-lab`, ambulance **A4 (ALS)**
   `en_route` with ETA, bed reserved at **Hadapsar Metro Hospital** — and the Verification panel
   showing **8/8 checks PASSED**.
5. **1:10** — Scroll to Family communications (English + Hindi + Marathi SMS, ER pre-alert) and expand the
   Audit trail — every agent action is logged with rationale and confidence.
6. **1:25** — Failure drill (second incident): force the ALS fleet down —
   `POST /api/demo/fleet/ambulance/A1 … A4` with `{"status":"out_of_service"}` —
   submit another cardiac arrest → watch **REPLANNING** flip to the red **ESCALATED** banner.
7. **1:50** — Note the StatsBar (escalated = 1, avg confidence), then reset:
   `POST /api/demo/reset`, and hit **Resolve incident** on the first case to release A4 back to the fleet.
8. **2:00** — Close: *"Deterministic, verified, and audited — when the AI can't guarantee a safe
   plan, it escalates to a human instead of guessing."*

## Limitations (honest, judge-safe)

- **Simulated city**: fictional-but-plausible Pune data — 8 hospitals, 14 ambulances. Not real
  fleet telemetry.
- **Straight-line ETAs**: haversine distance at 40 km/h with a flat 1.25× traffic factor.
  No road-network routing.
- **No real SMS gateway**: family/bystander/hospital messages are drafted and stored in the
  database (and shown on the dashboard), not actually delivered.
- **Bed counts are plain integers**: no real hospital booking integration.
- **Single city, no auth**: this is a local demo — do not expose it to a network. Every
  endpoint (including demo reset and failure injection) is open by design for judging.
- **LLM is optional and never on the safety rails**: dispatch selection, bed reservation,
  verification verdicts, and escalation decisions are always deterministic. The LLM only
  drafts message text and classifies unknown incident types (its confidence is capped so
  it can never bypass human review).

## Deploy to Render (free)

The repo ships a `Dockerfile` and a `render.yaml` Blueprint:

1. In Render: **New → Blueprint** → connect the `MedRelay` repo → **Apply**.
2. Render builds the image (frontend + backend) and serves it on its public URL.
3. Health check: `/api/health`.

Notes: the free tier sleeps after ~15 min idle (first load takes ~30–60 s to wake).
SQLite lives inside the container, so demo data resets on redeploy — fine for judging,
not for production. For the live pitch, run it locally with `run.bat` instead.

## Project layout

```
medrelay-fullstack/
├── README.md
├── run.sh / run.bat                  # one-command start
├── docs/                             # PROJECT_STATUS, ARCHITECTURE, AGENTS, API,
│                                     #   DATABASE, DEMO_SCENARIOS, TODO, HANDOFF
├── backend/
│   ├── requirements.txt              # fastapi, uvicorn[standard], sqlalchemy, pydantic
│   ├── app/
│   │   ├── main.py                   # FastAPI app, WS hub, dist/ serving
│   │   ├── api.py                    # REST routes (/api/*)
│   │   ├── orchestrator.py           # pipeline order, verify gate, replan, escalate
│   │   ├── agents/                   # the 6 agents + base
│   │   ├── models.py                 # SQLAlchemy models + SQLite setup
│   │   ├── seed.py                   # Pune hospitals/ambulances + geo helpers
│   │   ├── audit.py                  # append-only audit writers
│   │   ├── llm.py                    # Deterministic / Gemini providers
│   │   ├── schemas.py                # Pydantic: state, agent I/O, WS messages
│   │   └── medrelay.db               # SQLite (created on startup)
│   └── frontend/                     # shipped as dist/, served by the backend
├── frontend/
│   ├── src/
│   │   ├── App.tsx                   # dashboard layout + state merging
│   │   ├── api.ts                    # typed fetch helpers
│   │   ├── types.ts                  # mirrors of backend schemas
│   │   ├── hooks/useWebSocket.ts     # live feed + polling fallback
│   │   └── components/               # StatsBar, IncidentList, IncidentDetail,
│   │                                 #   LiveFeed, NewIncidentModal, badges
│   ├── dist/                         # built dashboard (served at /)
│   └── package.json
```

Full documentation: [`docs/PROJECT_STATUS.md`](docs/PROJECT_STATUS.md) ·
[`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) · [`docs/AGENTS.md`](docs/AGENTS.md) ·
[`docs/API.md`](docs/API.md) · [`docs/DATABASE.md`](docs/DATABASE.md) ·
[`docs/DEMO_SCENARIOS.md`](docs/DEMO_SCENARIOS.md) · [`docs/TODO.md`](docs/TODO.md) ·
[`docs/HANDOFF.md`](docs/HANDOFF.md)
