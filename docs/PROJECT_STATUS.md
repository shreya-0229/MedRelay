# Project Status — Phase 1

**MedRelay full-stack, Phase 1** · built for DecentraHack 2.0 (Agentic AI track) ·
final pitch 9 Oct 2026 · backend version `0.1.0` (agent layer v2 since 2026-10-06).

## 2026-10-06 — Agent layer refactor (v2)

The agent layer was restructured into canonical modules with a standard
envelope, a registry, and an inter-agent message bus. **Pipeline behavior is
preserved** — Intake → Triage → Dispatch ∥ HospitalLiaison → Verification gate
→ replan (2×) → escalation → Communication runs in exactly the same order
with the same retry/escalation policy.

**Old filenames → new filenames** (`backend/app/agents/`):

| Old | New | Class | Canonical name |
|---|---|---|---|
| `intake.py` | `intake_agent.py` | `IntakeAgent` | `IntakeAgent` |
| `triage.py` | `triage_agent.py` | `TriageAgent` | `TriageAgent` |
| `dispatch.py` | `dispatch_agent.py` | `DispatchAgent` | `DispatchAgent` |
| `hospital_liaison.py` | `hospital_agent.py` | `HospitalAgent` | `HospitalLiaisonAgent` (kept for dashboard contract) |
| `communication.py` | `communication_agent.py` | `CommunicationAgent` | `CommunicationAgent` |
| `verification.py` | `verification_agent.py` | `VerificationAgent` | `VerificationAgent` |
| `base.py` | `base_agent.py` | `BaseAgent` (+ `utcnow`) | — |

**Envelope extension:** `AgentOutput` now carries canonical fields — `agent`,
`incident_id`, `status` (`success|failed|escalated`), `decision`, `reasoning_summary`,
`warnings`, `requires_human`, `timestamp` — **plus** the legacy mirror fields
(`agent_name`, `confidence`, `rationale`, `data`, `success`) so the dashboard
keeps working unchanged. `BaseAgent.envelope()` builds both.

**Registry + bus added:** each agent self-registers with `@register` under
its canonical name; `get_agent(name)` / `list_agents()` in
`agent_registry.py`. The orchestrator resolves agents via `_agent_for()` →
registry (no hardcoded constructors). `AgentBus` (`agent_message.py`) is the
append-only inter-agent blackboard — one message per completed stage
(`to_agent="blackboard"`, payload `{"action","status","confidence"}`).

**Per-agent upgrades:**
- **Intake:** free-text extraction — `report_text` / `voice_transcript`
  (new optional `IncidentReport` fields) are parsed into structured fields
  when callers report in natural language.
- **Triage:** severity levels 1–5 now carry human-readable severity labels; a
  disclaimer is attached (decision support, not a diagnosis); a **vitals
  override** escalates severity when reported vitals contradict the
  type lookup. Low confidence sets `requires_human=True`.
- **Communication:** messages now persisted to the **`communications` DB
  table** (`CommunicationMessage`: channel `family|bystander|hospital|escalation`,
  language `en|hi|mr`, text) — the incident state keeps a copy for the
  dashboard; the table is the durable record. **Marathi templates added**
  (family messages + escalation notices now en/hi/mr).
- **Verification:** new checks — `required_fields_present`,
  `triage_consistency` (selections agree with triage severity/pathway), and
  `confidence_thresholds` (no agent below 0.6, none flagged `requires_human`),
  alongside the existing capability/assignment/bed/consistency checks.

**Orchestrator (`orchestrator.py`) rewired** to the registry + `AgentBus`
(same import surface otherwise); the gate now treats `requires_human` as
failure so a weak triage forces replan → escalation. `STAGE_PAUSE_S`,
`MAX_REPLAN_ATTEMPTS`, `LOW_CONFIDENCE`, `RETRY_ORDER` unchanged.

**What did NOT change:** the REST/WS contract (`/api/*`, `AGENT_NAMES`, WS
message shapes), the frontend (untouched), pipeline order, replan/escalation
policy, and the deterministic-first safety posture (dispatch/verification/
retry decisions still never touch the LLM).

## What Phase 1 delivered

- **6 agents + orchestrator** (`backend/app/agents/`, `backend/app/orchestrator.py`):
  IntakeAgent → TriageAgent → DispatchAgent ∥ HospitalLiaisonAgent (parallel via `asyncio.gather`)
  → VerificationAgent gate → replan loop (retry failed steps up to 2×, all logged) →
  human escalation → CommunicationAgent → append-only audit → WebSocket broadcast.
- **10 REST endpoints** (`backend/app/api.py`) under `/api`: health, create/list/get incident,
  per-incident audit, fleet, resolve, stats, demo reset, demo fleet control.
- **Live WebSocket dashboard** (`frontend/src/`): StatsBar, incident list, incident detail
  (6-stage pipeline view, verification panel, escalation/replanning banners, resources,
  bilingual communications, timeline, expandable audit trail), new-incident modal with a
  one-click cardiac-arrest demo fill, agent-activity live feed, WebSocket with HTTP polling
  fallback, one-command start (`./run.sh` / `run.bat`) on `http://localhost:8000`.
- **SQLite backend** (`backend/app/medrelay.db`, created on startup): 5 tables,
  seeded with 8 Pune hospitals + 14 ambulances (5 ALS / 9 BLS, 2 out of service).
- **LLM optional**: deterministic by default; real Gemini (env vars) drafts family
  messages and assists triage on unknown types, with silent template fallback on any failure.

## Verified working (before this doc was written)

- `GET /api/health` → `{"status": "ok", "llm_provider": "deterministic", ...}`.
- `POST /api/incidents` (cardiac arrest) → **201**, full `IncidentState` with
  **6 agent outputs** and **5/5 verification checks passed** (severity 5, ALS ambulance
  dispatched, cath-lab bed reserved).
- **Escalation drills verified**: unknown incident type (triage confidence 0.55 < 0.6 threshold)
  → replanning → escalated; forced fleet outage (all ALS units `out_of_service`) → dispatch
  failure → replanning → escalated — each with the escalation notice added to family
  communications and the incident flagged `escalated` on the dashboard.
- Dashboard verified live: agent events stream into the Activity feed, incident detail renders
  the pipeline/verification/audit panels, and `POST /api/demo/reset` restores the fleet.

## Known limitations (honest, judge-safe)

- Data is fictional-but-plausible Pune seed data; ETAs are straight-line haversine at
  40 km/h with a flat 1.25× traffic factor — **no real road routing**.
- Hospital beds are plain integer counters; no real booking integration, no SMS/calling
  — messages are drafted in the incident record only.
- Single city (Pune), single timezone-naive UTC timestamps, no authentication,
  no migrations (delete `medrelay.db` to reset), no tests yet.
- The LLM is optional and never on the safety rails: dispatch, verification, retry and
  escalation decisions are always deterministic.

## Explicitly NOT in Phase 1

Auth, multi-city support, real routing APIs, SMS/calling integration, Docker,
Alembic migrations, i18n (messages are English + Hindi templates only).
See [TODO.md](TODO.md) for the Phase-2 backlog.

## Update — 2026-10-06 (correctness audit)

- **Fixed: API rejected pure free-text reports (HTTP 422).** `IncidentReport`
  required `incident_type`/`lat`/`lon`, so the IntakeAgent's free-text parsing
  path was unreachable via the API. All three are now optional:
  `incident_type` defaults to `"unknown"`, missing coordinates fall back to
  Pune city centre (18.5204, 73.8567) and are flagged in `missing_information`
  as `precise_location` with a warning. Verified live: a text-only report
  ("Man collapsed near FC Road, not breathing properly, bleeding from head
  after bike accident") now returns 201 and runs the full pipeline; intake
  parsed `road_accident` and honestly reported missing
  `consciousness`/`exact_address`/`precise_location` without inventing them.
- Test suite: `backend/test_agents.py` — 48/48 pass (run inside `backend/.venv`).
- Failure path re-verified live: all ALS units out_of_service → dispatch fails
  → verification rejects (`confidence_thresholds_ok`) → 2 replan retries →
  `escalation_status: escalated`, escalation notice drafted in EN/HI/MR.
- WebSocket re-verified live: `fleet_update` → ordered `agent_event`s →
  `incident_update` → `fleet_update` during a real pipeline run.
- Communications now cover English, Hindi **and Marathi** (persisted to the
  `communications` table); the "templates only EN/HI" note above is stale.

## Update — 2026-10-06 (orchestration layer restructured)

- **Behavior-preserving refactor**: the single-module `backend/app/orchestrator.py`
  is now the `backend/app/orchestrator/` package —
  `event_bus.py` (canonical 13-type event system: INTAKE_COMPLETED,
  TRIAGE_STARTED/COMPLETED, DISPATCH_STARTED/COMPLETED,
  HOSPITAL_SEARCH_STARTED, HOSPITAL_SELECTED, VERIFICATION_STARTED,
  VERIFICATION_PASSED/FAILED, REPLAN_STARTED, HUMAN_ESCALATION,
  COMMUNICATION_SENT), `state_manager.py` (owns the shared IncidentState:
  validated `create`/`update`/`apply_agent_output`, persisted on every
  mutation), `workflow.py` (stage graph with the Dispatch/Hospital
  `asyncio.gather` fan-out), `orchestrator.py` (thin `Orchestrator` +
  `run_pipeline`, same signature/behavior).
- `AgentBus`/`AgentMessage` are now defined canonically in `event_bus.py`;
  `app/agents/agent_message.py` is a thin re-export (same objects).
- The bus persists stage start/completion to the audit DB and bridges them
  to the existing WebSocket `agent_event` shape — the dashboard Agent
  Activity feed now shows real started/completed events per stage; no
  frontend changes needed. DB schema and REST/WS contracts unchanged.
- Tests: existing `test_agents.py` 48/48 pass unchanged; new
  `backend/test_orchestrator.py` 34/34 pass (event vocabulary, fan-out
  ordering proven via event log, WS bridge shape, audit persistence,
  StateManager validation rejection, failure-path event sequence,
  `run_pipeline` signature).

## Update — 2026-10-06 (autonomous failure recovery)

New `backend/app/orchestrator/recovery.py` (`RecoveryManager`) + workflow
support for recovering **live** incidents — failures after a plan is
already active. The six agent modules are untouched; the dashboard shows
only real backend events.

- **Scenario 1 — ambulance breakdown:** `POST /api/incidents/{id}/failures`
  `{"resource_type":"ambulance","resource_id":"A1","reason":"breakdown"}` →
  unit marked `out_of_service` → `FAILURE_DETECTED` + `AMBULANCE_FAILURE` →
  the real VerificationAgent validates the failure → plan pauses
  (`replanning`) → DispatchAgent re-searches (failed unit excluded by its
  own availability query) → VerificationAgent verifies the replacement →
  `RESOURCE_REPLACED` → trilingual family notice (EN/HI/MR, persisted) →
  status `recovered`. No capable unit → human escalation + notice.
- **Scenario 2 — hospital outage:** same endpoint with
  `"resource_type":"hospital"` → `HOSPITAL_FAILURE`, HospitalLiaisonAgent
  re-searches (failed hospital has zero beds), re-verifies, reserves,
  notifies. Mirrors the ambulance flow.
- **Scenario 3 — low-confidence human review:** triage confidence < 0.6 now
  pauses the pipeline **before any reservation** —
  `current_status="human_review_required"` (distinct from `escalated`) +
  `REVIEW_REQUIRED`. `GET /api/incidents/{id}/review` returns the dossier
  (reason, affected decision, confidence, agent outputs);
  `POST …/review/decision` takes `approve` (operator takes responsibility —
  output marked `human_approved`, pipeline resumes at dispatch, still
  verification-gated), `reject` (→ `cancelled`), `request_info` (flagged for
  caller callback), `replan` (honest re-run). All decisions audit-logged.
- **Scenario 4 — agent conflict:** orchestrator-level `detect_conflicts()`
  (severity≥4 requires ALS; hospital specialty must match triage pathway —
  impossible for honest agents, so any hit fails closed) → verification
  marked failed with `conflict_detected`, `CONFLICT_DETECTED` event,
  reservations released, routed to human review (operator REPLAN is the
  re-evaluation path). Test/drill hook: `resource_type":"conflict"` with an
  overriding agent decision.
- **New event types:** `FAILURE_DETECTED`, `AMBULANCE_FAILURE`,
  `HOSPITAL_FAILURE`, `REPLANNING`, `RESOURCE_REPLACED`, `REVIEW_REQUIRED`,
  `REVIEW_DECIDED`, `CONFLICT_DETECTED` — all in the audit DB and the WS
  feed (`AgentEventMsg` shape unchanged).
- **Dashboard:** incident detail now shows FAILURE DETECTED (red, with
  resource + reason), AGENT CONFLICT DETECTED, NEW RESOURCE SELECTED, and a
  HUMAN REVIEW REQUIRED panel (reason, affected decision, confidence, triage
  output) with working APPROVE / REJECT / REQUEST MORE INFORMATION / REPLAN
  buttons, plus one-click failure-drill buttons for active assignments.
- **Tests:** `backend/test_recovery.py` — 62 checks across all four
  scenarios (replacement selection, failed-unit exclusion, trilingual
  notification, 11-step audit trail, pause-before-reservation, all four
  review decisions, conflict containment with bed/unit restoration).
  Full suite: 48 + 34 + 62 = 144 passing.

## Update — 2026-10-06 (command dashboard rebuild)

Frontend rebuilt as a 10-section emergency command center; backend untouched
(no new endpoints — `/api/fleet` already covered the resource panels).

- **Header**: System Online (health + WS), Agents Online (n/6), Active Incidents,
  live clock, LLM provider, version, + New incident modal (unchanged).
- **Live Incident Overview table**: Incident ID, Severity (CRITICAL/HIGH/
  MODERATE/LOW badges), Location, Status, Ambulance, Hospital, ETA,
  Confidence — rows clickable; full states fetched per row and refreshed by
  WS `incident_update` frames.
- **Agent Activity Panel**: six agents with IDLE/RUNNING/COMPLETED/FAILED/
  WAITING/HUMAN REVIEW derived strictly from `agent_outputs` (canonical
  timestamps) + live WS `agent_event` "…started" frames; predecessors give
  honest WAITING. No hardcoded statuses.
- **Agent Communication Graph**: SVG orchestrator↔agents network; each real
  backend event pulses its agent's node + edge exactly once (keyed 1.2s
  animation, settles — never loops); edges labeled with the latest real
  action per agent.
- **Incident Detail**: info, location, patient summary, severity, triage,
  ambulance, hospital, verification checks, confidence, status — all real
  state; timeline labels humanized ("Failure detected" etc.).
- **Timeline**: real timestamped entries, auto-fed by WS.
- **Ambulance panel**: fleet table (ID/status/ETA/capabilities/location)
  from `/api/fleet` + WS `fleet_update`; ETA joins the selected incident's
  real ETA when en route.
- **Hospital panel**: real bed counts + trauma capability from fleet data.
  ICU renders "n/a" — the backend has no ICU feed, so no numbers are
  invented (deliberate, documented in the UI tooltip).
- **Human Operator Panel**: rendered only when escalated/human_review_required;
  WHY breakdown (low confidence / conflict / failed verification / resource
  failure) derived from review dossier + timeline + verification state; the
  four buttons call the real review-decision API (extracted from
  IncidentDetail — no duplication).
- **Live Map**: Leaflet lazy-loaded (own code-split chunk, never blocks
  first render) with Esri World_Dark_Gray canvas tiles; incident/ambulance/
  hospital markers + straight-line route for the selected incident (honest:
  no road routing in prototype). If tiles fail, a clean SVG coordinate grid
  takes over automatically.
- Verified live: incident creation streams all 9 WS senders; failure drill
  produced failure_detected→…→resource_replaced→recovery_complete with status
  `recovered`; vague report → triage 0.55 → human_review_required → APPROVE
  → completed; banners (FAILURE DETECTED/REPLANNING/NEW RESOURCE SELECTED)
  keyed to real timeline/escalation state. Backend tests 144/144 pass.

## Update — 2026-10-06 (Demo Mode for hackathon judging)

- **New `backend/app/demo.py`**: 9 demo scenarios, each driving the REAL
  pipeline / RecoveryManager (nothing simulated). `GET /api/demo/scenarios`
  lists them; `POST /api/demo/scenarios/{id}/run` → 202 starts a tracked
  asyncio background task; `GET /api/demo/scenarios/runs/{run_id}` polls
  status/incident_id/terminal_state. Runs are tracked so reset cancels and
  ignores stale runs.
- **Scenarios**: critical_road_accident, heart_emergency,
  multiple_casualties, low_confidence (pauses at human_review_required for
  the operator), ambulance_failure + hospital_unavailable (full recovery →
  `recovered`), agent_conflict (→ human review), external_service_failure
  (honest mechanism: a per-run `_OutageProvider` whose `complete()` always
  raises, so `llm_assist` exercises the REAL deterministic fallback; the
  incident still completes and the audit records the fallback),
  main_judging — the 20-step flagship (accident → breakdown → replan →
  replacement → family update → new additive status `hospital_ready`).
- **`hospital_ready`** is a new additive incident status (green "Hospital
  Ready" badge via `statusLabel()`); excluded from the stats "active" count.
  Existing statuses/tests untouched.
- **Reset** (`POST /api/demo/reset`) now cancels tracked runs, wipes
  incidents/agent_runs/audit_events/communications, and reseeds the fleet —
  exactly like a fresh boot (verified: 0 incidents, fleet at seeded values).
- **Frontend**: new "Demo Scenarios" header tab (`DemoView.tsx`) — 8 cards +
  highlighted flagship card + Reset Demo (with confirm). Each card shows a
  live step checklist that ticks off ONLY as real WS backend events arrive
  (pointer scan over the chronological feed; parallel stages use match-all
  groups; repeated actions resolve to the correct occurrence). Completion
  links into the command view. `npm run build` clean.
- Verified live: flagship via HTTP → 22 real agent_events in order →
  `hospital_ready`; run twice back-to-back → both `hospital_ready`, no
  leaked fleet holds; reset → pristine. Backend tests 211/211 pass
  (48 agents + 34 orchestrator + 62 recovery + 67 new demo tests).
- Honest note: the flagship report text uses "bleeding heavily" (one word
  added to the script) — that is what deterministically drives the triage
  vitals override to CRITICAL; documented in `demo.py`.
