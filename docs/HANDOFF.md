# Handoff — How to Continue

## Repo layout (what's where)

```
backend/app/
  main.py          FastAPI app, WS hub, serves frontend/dist
  api.py           10 REST endpoints under /api
  orchestrator.py  run_pipeline: stages → verify gate → replan (2×) → escalate
  agents/          base.py (BaseAgent) + 6 agents: intake, triage, dispatch,
                   hospital_liaison, verification, communication
  models.py        5 SQLAlchemy tables + sync engine/session
  seed.py          Pune hospitals/ambulances + haversine/ETA/capability helpers
  audit.py         append-only writers
  llm.py           DeterministicProvider / GeminiProvider, chosen by env
  schemas.py       Pydantic v2: IncidentReport, IncidentState, AgentOutput, WS msgs
  medrelay.db      SQLite, auto-created (git-ignored; safe to delete)
frontend/src/
  App.tsx, api.ts, types.ts, hooks/useWebSocket.ts, components/
run.sh / run.bat  one-command start → http://localhost:8000
```

## How to run

`./run.sh` (or `run.bat` on Windows) — creates `backend/.venv`, installs the 4 Python
deps, builds the frontend if `dist/` is missing, starts uvicorn on port 8000.
Manual: see the README. Demo helpers: `POST /api/demo/reset`,
`POST /api/demo/fleet/ambulance/{id}` (see [API.md](API.md)).

## How to add a 7th agent

1. Create `backend/app/agents/<name>.py` with `class XyzAgent(BaseAgent)` and
   `async def run(self, state: IncidentState) -> AgentOutput` — read what you need from
   `state`, mutate it in place, and return the `AgentOutput` envelope
   (`agent_name`, `confidence`, `rationale`, `data`, `success`).
2. Export it from `backend/app/agents/__init__.py` and add it to `_agent_for()` in
   `orchestrator.py` (so retries can rebuild it by name).
3. Insert an `await stage(...)` call in `run_pipeline()` at the right point in the
   sequence — `stage()` handles the agent-run/audit records, timeline, persistence,
   and WS broadcast automatically.
4. If the agent can fail and should be retried, add its name to `RETRY_ORDER`
   (and consider the confidence gate in `_gate_failed`).
5. If it produces new state fields, add them to `IncidentState` in `schemas.py` and
   mirror them in `frontend/src/types.ts`.
6. Add it to `PIPELINE_AGENTS` in `frontend/src/types.ts` so the dashboard renders
   its pipeline card (order here = display order).

## How the replanning policy is tuned

All knobs live at the top of `backend/app/orchestrator/workflow.py`:

- `MAX_REPLAN_ATTEMPTS = 2` — replan retries before escalation.
- `LOW_CONFIDENCE = 0.6` — per-agent confidence safety rail (also the triage
  `success` cutoff). LLM-supplied triage confidence is additionally capped at
  0.59 so a real model can never bypass this floor (2026-10-06 audit fix).
- `RETRY_ORDER = ["TriageAgent", "DispatchAgent", "HospitalLiaisonAgent"]` —
  which failed agents get retried, in which order.
- `STAGE_PAUSE_S = 0.35` — delay between stages so the dashboard animates live;
  raise it for slower demos, drop it toward 0 for load testing.
- `_gate_failed()` defines the failure conditions (verification fail / agent fail /
  low confidence); edit it to change what counts as "needs replanning".

## Gotchas

- **PEP-668**: never `pip install` into the system Python — use `backend/.venv`.
- **Pristine demo**: delete `backend/app/medrelay.db` before starting; tables and the
  seed are recreated on startup. `POST /api/demo/reset` now does a FULL wipe —
  cancels tracked scenario runs, deletes all incidents/agent-runs/audit/communications,
  and reseeds the fleet (exactly like a fresh boot).
- **`frontend/dist/` ships pre-built** in the transfer zip and `run.sh` skips the
  build when it's present — rebuild (`npm run build`) after any frontend change.
- **WS client frames are ignored** by the server; the socket is purely a push channel.
- **Seed data is fictional** (Pune hospitals/ambulances are plausible, not real) and
  ETAs are straight-line — don't present the numbers as operational.
- **LLM never touches safety**: dispatch, verification, replan, and escalation are
  always deterministic; the Gemini path only drafts messages / assists unknown-type
  triage, with silent template fallback.
- `GET /api/stats` counts `active` as any incident not `completed`/`escalated`/`failed`;
  `resolve` flips a completed incident to `completed` and frees its ambulance.
- Incident IDs are `INC-YYYYMMDD-NNNN` per day, allocated by counting existing rows —
  a concurrent-create race retries once with a random suffix instead of 500ing
  (2026-10-06 audit fix).
