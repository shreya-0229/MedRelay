# Architecture

## System diagram

```
 ┌──────────────────────────────────────────────────────────────┐
 │ Browser (dashboard)                                          │
 │ React 19 + TS + Vite + Tailwind v3  (#0a1628 dark navy)       │
 │   App.tsx ── api.ts ──►  fetch /api/*   (same origin)        │
 │     │                                                         │
 │     └─ useWebSocket.ts ──►  ws://…/ws  (JSON frames)         │
 └──────────────────────────────┬───────────────────────────────┘
                                │ port 8000
 ┌──────────────────────────────▼───────────────────────────────┐
 │ Backend (single uvicorn process)                             │
 │                                                              │
 │  app/main.py  ── FastAPI                                    │
 │    ├── /api/*        ──► app/api.py      (REST router)        │
 │    ├── /ws           ──► ConnectionManager broadcast         │
 │    └── /{path:path}  ──► frontend/dist  (SPA catch-all)      │
 │                                                              │
 │  app/orchestrator.py ── run_pipeline()                       │
 │    Intake → Triage → ┌───────────────┐ → Verification gate   │
 │                      │ Dispatch   ∥   │    → replan ×2 max    │
 │                      │ Hospital   ∥   │    → escalate         │
 │                      └───────────────┘ → Communication       │
 │                       └─▶ audit (append-only) + WS broadcast  │
 │                                                              │
 │  app/agents/*.py  ── 6 agents, mutate shared IncidentState   │
 │  app/llm.py       ── Deterministic | GeminiProvider           │
 │                                                              │
 │  SQLAlchemy 2.0 (SYNC) ──► app/medrelay.db (SQLite)          │
 │    incidents │ agent_runs │ audit_events │ ambulances │ hospitals│
 └──────────────────────────────────────────────────────────────┘
```

## Request flow (`POST /api/incidents`)

1. FastAPI validates the body against `IncidentReport` and calls `run_pipeline()`.
2. The orchestrator creates the incident row (`INC-YYYYMMDD-0001…`) and persists the
   `IncidentState` blackboard as JSON **after every stage**.
3. Each stage calls `agent.run(state)` → records an `AgentRun` + `AuditEvent`, appends a
   timeline entry, persists, broadcasts an `agent_event` over WS, then pauses
   **0.35 s** (`STAGE_PAUSE_S`) so the dashboard visibly animates.
4. Triage runs first (Dispatch/Hospital need its pathway + severity), then
   `asyncio.gather` fans Dispatch and Hospital Liaison out **in parallel**; they are
   safe to share one session because they touch different tables (ambulances vs hospitals).
5. Verification runs; on failure the gate triggers the replan loop (retry the failed
   agents, max 2 attempts, re-verify) and escalates to a human if it still fails.
6. Communication drafts the family/ER messages (plus an escalation notice when escalated).
7. Final broadcast: `incident_update` (full state) + `fleet_update`; response is `201`
   with the complete `IncidentState`.

Other endpoints (`GET /incidents/{id}/audit`, `/fleet`, `/resolve`, `/stats`, demo
helpers) are plain synchronous SQLAlchemy queries — one session per request via the
`get_db` dependency.

## Module responsibilities

| File | Responsibility |
|------|----------------|
| `backend/app/main.py` | FastAPI app, lifespan (create_all + seed), WS endpoint, serves `frontend/dist/` |
| `backend/app/api.py` | REST router `/api/*` (10 endpoints); dashboard-readable payloads |
| `backend/app/orchestrator.py` | Pipeline order, verification gate, replan (2 attempts), escalation, broadcasts |
| `backend/app/agents/*.py` | 6 agents; each `run(state)` mutates the shared state and returns an `AgentOutput` |
| `backend/app/models.py` | SQLAlchemy models, sync engine, session factory |
| `backend/app/seed.py` | Pune hospitals/ambulances, haversine/ETA/capability helpers, reseed |
| `backend/app/audit.py` | Append-only `write_audit` / `write_agent_run` + queries |
| `backend/app/llm.py` | Provider abstraction: `DeterministicProvider` (default) / `GeminiProvider` |
| `backend/app/schemas.py` | Pydantic v2: `IncidentReport`, `IncidentState`, `AgentOutput`, WS messages |
| `frontend/src/App.tsx` | Dashboard shell, WS state merging, stats polling (5 s) |
| `frontend/src/api.ts` | Typed `fetch` helpers, same origin |
| `frontend/src/types.ts` | TS mirrors of the backend schemas |
| `frontend/src/hooks/useWebSocket.ts` | WS client, reconnect backoff, HTTP polling fallback |
| `frontend/src/components/` | StatsBar, IncidentList, IncidentDetail (pipeline, verification, escalation,
  comms, timeline, audit), NewIncidentModal, LiveFeed, badges |

## Why sync SQLAlchemy

The whole pipeline runs in a single-threaded uvicorn event loop, and agents only ever
await each other — not the database. One sync session per pipeline run (or per request
for reads) is simpler than an async engine and avoids session-sharing bugs. The one
concurrent section (`asyncio.gather` over Dispatch + Hospital Liaison) mutates disjoint
tables, so a shared session is safe there. Trade-off accepted for Phase 1: no concurrent
requests hit the same session because each HTTP request gets its own via `get_db`.

## WebSocket broadcast design

- `ConnectionManager` in `main.py` holds a set of connected sockets; `broadcast(dict)`
  sends JSON to all of them and silently prunes dead connections.
- Emitted frames: `agent_event` after **every** agent stage (drives the live feed and the
  pipeline animation), `incident_update` + `fleet_update` at pipeline end, `fleet_update`
  after demo fleet changes, and `fleet_update` + `incident_list` immediately on connect.
- Client messages are ignored — the server just keeps the connection alive with
  `receive_text()`.
- The frontend `useWebSocket` hook reconnects with exponential backoff (500 ms → 5 s max);
  after **3 consecutive failures** it switches to polling `GET /api/incidents` every
  **4 s** and shows an "OFFLINE · POLLING" pill until the socket recovers.

## Frontend dist-serving design

`main.py` serves the dashboard from `backend/../../frontend/dist/` **if it was built**:
`/assets` is mounted as static files and a `GET /{path:path}` SPA catch-all returns
`index.html`. API routes and `/ws` are registered first, so they always win over the
catch-all. If `dist/` doesn't exist, `/` returns a JSON hint to build the frontend.
`run.sh`/`run.bat` skip the frontend build when `dist/` is already present (it ships
pre-built in the transfer zip), so the backend is the only process you ever run.
