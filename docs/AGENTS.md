# Agent Architecture (MedRelay v2)

Six agents plus orchestration infrastructure. Every agent subclasses
`BaseAgent` (`backend/app/agents/base_agent.py`), runs async against the
shared `IncidentState`, mutates it in place, and returns a standard
`AgentOutput` envelope. Agents **never call each other directly** — they
communicate through the incident state and the `AgentBus` blackboard, which
keeps every pipeline run auditable and replayable.

## File map

| File | Class | Canonical name (registry key) |
|---|---|---|
| `backend/app/agents/intake_agent.py` | `IntakeAgent` | `IntakeAgent` |
| `backend/app/agents/triage_agent.py` | `TriageAgent` | `TriageAgent` |
| `backend/app/agents/dispatch_agent.py` | `DispatchAgent` | `DispatchAgent` |
| `backend/app/agents/hospital_agent.py` | `HospitalAgent` | `HospitalLiaisonAgent` |
| `backend/app/agents/communication_agent.py` | `CommunicationAgent` | `CommunicationAgent` |
| `backend/app/agents/verification_agent.py` | `VerificationAgent` | `VerificationAgent` |

> Note: the class is `HospitalAgent`, but its canonical name is
> `"HospitalLiaisonAgent"` — that string is the REST/WS dashboard contract
> (`AGENT_NAMES` in `app/api.py`), so the rename is class-only.

## IntakeAgent

- **File / class / name:** `intake_agent.py` · `IntakeAgent` · `"IntakeAgent"`.
- **Responsibility:** validate and normalize the raw caller report before
  anything else runs. It is the pipeline's front door — nothing downstream
  executes if intake rejects the report.
- **Constructor:** `IntakeAgent(report: IncidentReport, db: Session, llm=None)` —
  the only agent that takes the raw report.
- **Functions:** `run(state)`; `envelope()`; `llm_assist()` for free-text parsing;
  the standard helpers `write_agent_run` / `write_audit` (from `app.audit`)
  are called by the orchestrator after every stage.
- **Input:** the `IncidentReport` POSTed to `/api/incidents`. Structured fields
  are used directly when present; **free-text extraction** parses
  `report_text` / `voice_transcript` into structured fields (incident type,
  patient count, symptoms, breathing/bleeding status, location) when the
  caller reports in natural language instead of filling the form.
- **Decision / output fields** (on `IncidentState` and in the envelope
  `decision`): normalized `incident_type`, `location`, `patient_count`,
  `symptoms`, breathing/bleeding status, `family_contact`.
- **Rules:** lat/lon range checks; `patient_count >= 1`; `incident_type`
  lowercased with spaces/dashes → underscores; breathing/bleeding clamped to
  known vocab (`normal|labored|absent|agonal`, `none|minor|severe`) with safe
  defaults.
- **LLM assist:** a configured model may help interpret free-text reports
  (keyword extraction, symptom mapping). Its output is parsed and validated
  like any other input — on parse failure the deterministic defaults stand.
- **Confidence:** 0.98 on success, 0.2 on validation failure.
- **Safety rules:** validation is purely deterministic — no model ever decides
  whether a report is accepted. `requires_human=True` / `status="failed"` on
  validation failure; the orchestrator then **skips the rest of the pipeline**
  and escalates to a human immediately.

## TriageAgent

- **File / class / name:** `triage_agent.py` · `TriageAgent` · `"TriageAgent"`.
- **Responsibility:** clinical router — assigns severity and a care pathway.
  Must run before dispatch/hospital (they need pathway + severity).
- **Constructor:** `TriageAgent(db, llm=None)`.
- **Functions:** `run(state)`; `envelope()`; `llm_assist()` for unknown
  incident types only.
- **Input:** normalized `incident_type` (plus reported symptoms/vitals from the
  state).
- **Decision / output fields:** `severity` (1–5, now with human-readable
  **severity labels** for the dashboard/audit), `pathway` (e.g. `cath-lab`,
  `trauma-center`, `stroke-unit`, `general-er`, `obstetric`), `triage_result`
  (`severity`, `pathway`, `confidence`, `rationale`, `disclaimer`).
- **Rules:** deterministic lookup maps — `cardiac_arrest→5/cath-lab`,
  `road_accident→4/trauma-center`, `stroke→4/stroke-unit`, `trauma_fall→3/general-er`,
  `obstetric→3/obstetric`, `chest_pain→2/general-er`. A **vitals override** can
  escalate severity upward when reported vitals (e.g. absent/agony breathing,
  severe bleeding) contradict the type lookup — physiology beats the table.
  Every output carries a **disclaimer**: AI-assisted triage is decision
  support, not a clinical diagnosis; a human dispatcher owns the final call.
- **LLM assist:** consulted **only** for unknown incident types. It must reply
  `SEVERITY=<1-5> PATHWAY=<pathway> CONFIDENCE=<0-1>`; the pathway is validated
  against the known set and on any parse failure the deterministic fallback
  (severity 3 / `general-er` at 0.55) stands.
- **Confidence:** 0.95 for known types, 0.55 for unknown (LLM-assessed values
  pass through only if a real model answered).
- **Safety rules:** the lookup table is deterministic and auditable; LLM output
  is quarantined to unknown types and never overrides the vitals safety
  ladder. When confidence < 0.6 the envelope sets `requires_human=True` — a
  deliberate rail so the gate forces replan → escalation rather than quietly
  proceeding on a weak triage.

## DispatchAgent

- **File / class / name:** `dispatch_agent.py` · `DispatchAgent` · `"DispatchAgent"`.
- **Responsibility:** assign the nearest capable ambulance from the DB fleet.
  Runs in parallel with HospitalAgent (via `asyncio.gather` — they touch
  different tables, so this is safe on one session).
- **Constructor:** `DispatchAgent(db, llm=None)`.
- **Functions:** `run(state)`; `envelope()`; ETA math via haversine distance ÷
  40 km/h × 1.25 traffic factor; fleet rows from `app.seed.fleet_snapshot`.
- **Input:** `triage_result` (pathway, severity) + incident location.
- **Decision / output fields:** `selected_ambulance` (`id`, `capability`,
  `eta_min`), `ambulance_status` (`en_route` | `unavailable`).
- **Rules (deterministic, in this order):** skip any unit not `available`;
  **ALS is required** when severity ≥ 4 or pathway ∈ {`cath-lab`, `stroke-unit`},
  BLS otherwise; pick minimum ETA. On success the unit is marked `en_route`
  with `assigned_incident` and committed, so the rest of the pipeline and the
  dashboard see it immediately.
- **LLM assist:** none on the decision path. (The LLM has no role in dispatch —
  resource allocation is a hard deterministic rule.)
- **Confidence:** 0.92 on success, 0.0 on failure.
- **Safety rules:** dispatch never invents units — it reads the live DB fleet
  and commits the assignment atomically. `status="failed"` with
  `ambulance_status="unavailable"` when triage is missing or no capable unit
  exists; this is the trigger for the escalation drill in the demo scenarios.

## HospitalAgent (canonical name `HospitalLiaisonAgent`)

- **File / class / name:** `hospital_agent.py` · `HospitalAgent` ·
  `"HospitalLiaisonAgent"` (canonical name kept for the dashboard contract).
- **Responsibility:** reserve a bed at the nearest suitable hospital. Runs in
  parallel with DispatchAgent.
- **Constructor:** `HospitalAgent(db, llm=None)`.
- **Functions:** `run(state)`; `envelope()`; nearest-by-haversine over the
  seeded hospital table.
- **Input:** `triage_result` (pathway) + incident location.
- **Decision / output fields:** `selected_hospital` (`id`, `name`,
  `distance_km`), `hospital_status` (`reserved` | `unavailable`).
- **Rules:** candidate = pathway in the hospital's specialties **and**
  `free_beds > 0`; nearest by haversine wins; the bed is reserved by
  decrementing `free_beds` in the DB.
- **LLM assist:** none on the decision path.
- **Confidence:** 0.9 on success, 0.0 on failure.
- **Safety rules:** never reserves a bed it can't see (`free_beds > 0`
  required) and never books a hospital lacking the pathway specialty.
  `status="failed"` when triage is missing or no suitable hospital has a free
  bed → same replan/escalation path as dispatch.

## CommunicationAgent

- **File / class / name:** `communication_agent.py` · `CommunicationAgent` ·
  `"CommunicationAgent"`.
- **Responsibility:** multilingual notifier — family + ER. Always runs, even
  on escalated incidents (families need the escalation notice).
- **Constructor:** `CommunicationAgent(db, llm=None)`.
- **Functions:** `run(state)`; `envelope()`; template drafts per
  channel/language; persists each message as a `CommunicationMessage` row in
  the **`communications` DB table** (channel ∈ `family|bystander|hospital|escalation`,
  language ∈ `en|hi|mr`) in addition to the copy on the incident state.
- **Input:** selected ambulance/hospital, location, severity, escalation status.
- **Decision / output fields:** `communications` entries — English + Hindi +
  **Marathi** family SMS and an ER pre-alert; plus an escalation notice (all
  three languages) when the incident was escalated to a human.
- **LLM assist:** with a real provider configured it drafts the English/Hindi/Marathi
  family messages naturally, falling back to deterministic templates silently
  on any failure — **a model outage can never break notification**.
- **Confidence:** 0.97.
- **Safety rules:** the agent never invents facts — messages are built from
  the confirmed selections and status on the state, and template variables
  are escaped/quoted so LLM-drafted text can't leak into structured fields.
  It never returns failure; `requires_human` is never set.

## VerificationAgent (the safety gate)

- **File / class / name:** `verification_agent.py` · `VerificationAgent` ·
  `"VerificationAgent"`.
- **Responsibility:** independent cross-check of the pipeline's decisions,
  running after dispatch and hospital liaison. It **re-reads the database**
  rather than trusting in-memory state, so it catches stale or inconsistent
  resource assignments. It is the gatekeeper: a rejection sends the pipeline
  into replan (up to 2 retries) and then human escalation.
- **Constructor:** `VerificationAgent(db, llm=None)`.
- **Functions:** `run(state)`; `envelope()`; per-check helpers re-reading the
  `Ambulance` / `Hospital` DB rows.
- **Input:** `triage_result`, resource selections, all prior agent outputs,
  and the live DB rows.
- **Decision / output fields:** `verification_results` (`passed`, `issues`,
  `checks`).
- **The checks:**
  1. `required_fields_present` — triage output, resource selections, and prior
     agent outputs all exist before anything is judged.
  2. `triage_valid` — triage present, severity in 1–5, pathway in the known set.
  3. `triage_consistency_ok` — dispatch/hospital selections agree with the triage
     severity and pathway (e.g. no BLS unit on a severity-5 cath-lab case, no
     hospital booked without the required specialty).
  4. `confidence_thresholds_ok` — no agent output below `LOW_CONFIDENCE` (0.6) and
     none flagged `requires_human`.
  5. `ambulance_capability_ok` — selected ambulance's capability satisfies the
     ALS rule; vacuously true if none selected.
  6. `ambulance_assignment_ok` — the DB row really is `en_route` and assigned
     to *this* incident; vacuously true if none selected.
  7. `hospital_bed_ok` — the DB hospital has the pathway specialty **and**
     `hospital_status == "reserved"`; vacuously true if none selected.
  8. `resource_consistency_ok` — no contradiction between agent `success` flags
     and the selected resources (e.g. "dispatch failed but an ambulance is selected").
- **LLM assist:** none — the gate is deterministic by design.
- **Confidence:** 0.99 when passed, 0.45 when failed; `success` mirrors `passed`.
- **Safety rules:** verification is the one agent whose rejection **must**
  stop forward progress. It never trusts in-memory selections without a DB
  read, it never passes on missing required fields, and it flags low
  confidence / `requires_human` as hard failures so they route to replan →
  escalation, never to silent completion.

## Standard envelope (`AgentOutput`)

Every `run()` returns this envelope (built via `BaseAgent.envelope()`, which
fills **both** the canonical and the legacy dashboard fields):

**Canonical fields (new):**
- `agent: str` — canonical agent name (registry key).
- `incident_id: str`.
- `status: "success" | "failed" | "escalated"`.
- `decision: dict` — the agent's structured decision payload.
- `reasoning_summary: str` — human-readable rationale.
- `warnings: list[str]` — non-fatal concerns (e.g. "used fallback pathway").
- `requires_human: bool` — safety flag; the orchestrator treats it as a
  failure and routes through replan → escalation.
- `timestamp: datetime` — timezone-aware UTC.

**Legacy fields (dashboard mirror, still populated):**
- `agent_name`, `confidence` (0–1), `rationale`, `data` (copy of `decision`,
  plus `action` when set), `success` (`status == "success"`).

The REST/WS contract reads the legacy fields, so the frontend works unchanged.

## AgentBus / AgentMessage

Agents never call each other — they publish to the `AgentBus`, an append-only
in-memory blackboard owned by the orchestrator for one pipeline run.

- `AgentMessage`: `from_agent`, `to_agent`, `incident_id`, `payload` (dict),
  `timestamp` (UTC).
- `AgentBus`: `publish(from_agent, to_agent, incident_id, payload=None)` →
  appends and returns the message; `for_incident(incident_id)`; `latest(incident_id,
  from_agent)`; `clear()`; `len(bus)`.
- The orchestrator publishes one message per completed stage
  (`to_agent="blackboard"`, payload `{"action", "status", "confidence"}`),
  so the bus is a replayable trace of the run. It is currently in-memory
  (debugging/replay), not persisted — the durable record remains the audit
  log, agent runs, and the `communications` table.

## Agent registry

Each agent module self-registers at import time with `@register` under its
canonical name (`BaseAgent.name`). Importing `app.agents` imports every agent
module, so the registry is fully populated afterwards.

- `register(cls)` — class decorator; key is `cls.name`.
- `get_agent(name)` — returns the class; raises `KeyError` on unknown names
  (listing the known ones).
- `list_agents()` — sorted canonical names.
- The orchestrator resolves agents through `get_agent()` (`_agent_for`),
  including retries — no hardcoded per-agent constructors outside the
  registry.

## BaseAgent helper

`BaseAgent` (`base_agent.py`, `version = "2.0"`) supplies the shared machinery:

- `name: str` — canonical registry key; `version`; `input_schema` /
  `output_schema` (declared I/O contracts; output is always `AgentOutput`).
- `__init__(db, llm=None)` — stores the session; defaults to a
  `DeterministicProvider` so agents work fully offline.
- `run(state)` — abstract; subclasses implement the stage logic, mutating
  `IncidentState` in place.
- `llm_assist(prompt) -> str | None` — optional interpretation hook. Returns
  the model's text, or `None` when no real provider is configured. **Never
  raises** — a model outage must never break the deterministic pipeline.
- `envelope(state, *, status, confidence, decision, reasoning_summary,
  warnings, requires_human, action)` — builds the standard envelope, filling
  canonical + legacy fields and clamping confidence to [0, 1].
- `utcnow()` — timezone-aware UTC now, shared by agents, orchestrator, audit,
  and API for timeline/audit timestamps.

## Orchestrator — replan / escalation policy

`run_pipeline()` (`orchestrator.py`) executes the stages with these rules:

- **Resolution:** agents are built via `_agent_for(name, db, llm, report)` →
  `get_agent(name)`; `IntakeAgent` is the only one needing the raw report.
- **Gate** (`_gate_failed`): replan when verification failed, any agent
  failed, any agent set `requires_human=True`, or any agent confidence <
  `LOW_CONFIDENCE` = **0.6** (CommunicationAgent is exempt from the gate scan).
  A low-confidence triage (`requires_human`) therefore forces replan →
  escalation — the orchestrator **must escalate**, never silently complete.
- **Retry order** (`RETRY_ORDER`): TriageAgent → DispatchAgent →
  HospitalLiaisonAgent. If the agents claim success but verification disagrees,
  the resource steps are re-run against fresh fleet state.
- **Replan loop:** up to `MAX_REPLAN_ATTEMPTS` = **2**. Each attempt re-runs
  the failed agents (`_failed_agent_names`: failed, `requires_human`, or
  below-threshold, excluding Verification/Communication), re-verifies, and is
  logged in the timeline + audit ("Replan started", "Replan attempt N",
  "Replan succeeded"). During replanning, `escalation_status = "replanning"`
  (amber banner on the dashboard).
- **Escalation:** when replanning is exhausted (or intake validation fails),
  the incident is marked `escalation_status = "escalated"`,
  `current_status = "escalated"`, the reason is built from the failed agent
  rationales + verification issues, and the CommunicationAgent appends
  escalation notices (English + Hindi + Marathi) to the family messages.
- **Overall confidence** = minimum of all agent confidences in the final state.
- The orchestrator is **not** an agent: it has no `run()` output, appears in
  the audit log only as `agent="Orchestrator"` on replan/escalation entries,
  owns the `AgentBus` for the run, and its tuning knobs are the three module
  constants above plus `STAGE_PAUSE_S` (0.35 s stage delay for the dashboard
  animation). See [HANDOFF.md](HANDOFF.md) for how to tune them.
