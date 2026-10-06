# Demo Scenarios (judge scripts)

MedRelay ships a **Demo Mode** for hackathon judging: open the
**Demo Scenarios** tab in the dashboard header. Nine one-button scenarios,
each driving the REAL backend pipeline — the step checklists tick off only
as genuine backend events arrive over the WebSocket. Nothing is animated or
hardcoded.

API equivalents (for scripting / curl):

```bash
# list scenarios
curl http://localhost:8000/api/demo/scenarios

# run one (202 immediately; the pipeline runs async)
curl -X POST http://localhost:8000/api/demo/scenarios/main_judging/run

# poll status → incident_id → terminal_state
curl http://localhost:8000/api/demo/scenarios/runs/<scenario_run_id>

# full reset (cancels runs, wipes incidents, reseeds fleet)
curl -X POST http://localhost:8000/api/demo/reset
```

## The 2-minute judge script (one button)

1. Open the **Demo Scenarios** tab.
2. Hit **Run** on **"Flagship: Crash → Breakdown → Recovery"**.
3. Narrate the checklist as it ticks (20 real steps):
   - *"Intake parses a free-text 112-style report — two injured, one
     unconscious."*
   - *"Triage, Dispatch and Hospital Liaison fan out in parallel — watch
     the communication graph light up."*
   - *"Verification passes, ambulance and hospital reserved, family
     notified in English, Hindi and Marathi."*
   - *"Now the twist: the ambulance breaks down mid-response."*
   - *"The system detects it, verifies the failure, replans — the failed
     unit is excluded — and a replacement is dispatched and verified."*
   - *"Family gets the new ETA. Every step is in the audit trail."*
   - *"Incident ends at HOSPITAL READY — the receiving team is standing by."*
4. Click **View incident** to show the audit trail + timeline in the
   command view.
5. **Reset Demo** and take questions.

Total runtime: ~15 seconds. Repeatable — run it twice back-to-back; both
reach `hospital_ready` and the fleet is never corrupted.

## The 9 scenarios

| # | Scenario | What it proves | Terminal state |
|---|----------|----------------|----------------|
| 1 | Critical Road Accident | Free-text intake → CRITICAL triage → full pipeline | `completed` |
| 2 | Heart Emergency | Cardiac pathway, conscious patient | `completed` |
| 3 | Multiple Casualties | 5 patients, bus accident | `completed` |
| 4 | Low Confidence Report | Vague report → triage 0.55 → pipeline pauses *before* any reservation | `human_review_required` (operator decides: Approve / Reject / Request info / Replan) |
| 5 | Ambulance Failure | Breakdown mid-response → 11-step recovery, failed unit excluded | `recovered` |
| 6 | Hospital Unavailable | Hospital goes dark → replan to a new facility | `recovered` |
| 7 | Agent Conflict | BLS unit injected for a critical case → verification blocks, reservations released | `human_review_required` |
| 8 | External Service Failure | AI provider outage → deterministic fallback completes the job | `completed` |
| 9 | **Flagship** | 1 → 5 → `hospital_ready`, 20 steps, one button | `hospital_ready` |

Scenario 8's mechanism is honest, not a fake service: a per-run provider
whose `complete()` always raises, so the real `llm_assist`
catch-and-fallback path executes exactly as in a production outage.

## Reliability notes

- Scenario tasks are tracked server-side; **Reset Demo** cancels stale runs,
  so a reset mid-scenario can never corrupt the next run.
- The flagship report text uses "bleeding heavily" (one word added to the
  spoken script) — that deterministically drives the triage vitals override
  to CRITICAL. Everything else is verbatim.
- The checklist matcher resolves repeated actions to the correct occurrence
  (the flagship dispatches twice) and treats parallel stages as match-all
  groups, so it cannot tick out of order.

## Appendix — manual curl drills (pre-demo-mode, still valid)

### A1 — Cardiac arrest happy path

```bash
curl -X POST http://localhost:8000/api/incidents \
  -H 'Content-Type: application/json' -d '{
    "incident_type": "cardiac_arrest",
    "lat": 18.5204, "lon": 73.8567,
    "address": "FC Road, Shivajinagar, Pune",
    "patient_count": 1,
    "symptoms": ["chest pain", "collapsed", "unresponsive"],
    "breathing_status": "not_breathing",
    "bleeding_status": "none",
    "family_contact": "+91 98220 12345"
  }'
```

**Expected:** `201`. Severity 5, pathway `cath-lab` → ambulance **A4 (ALS)**
dispatched `en_route`, bed reserved at the nearest hospital with `cath-lab`
+ free beds, Verification **PASSED**, family communications in English +
Hindi + Marathi plus the ER pre-alert.

### A2 — Unknown incident type → human review

```bash
curl -X POST http://localhost:8000/api/incidents \
  -H 'Content-Type: application/json' -d '{
    "incident_type": "mystery_syndrome_xyz",
    "lat": 18.5314, "lon": 73.8446, "address": "Deccan Gymkhana",
    "breathing_status": "normal", "bleeding_status": "none"
  }'
```

**Expected:** triage confidence 0.55 → `human_review_required`, no
reservations made. Decide via `POST
/api/incidents/{id}/review/decision` (`approve` / `reject` /
`request_info` / `replan`).

### A3 — Ambulance breakdown drill (manual)

```bash
# 1. create the incident (A1), note the selected ambulance id
# 2. inject the failure:
curl -X POST http://localhost:8000/api/incidents/<id>/failures \
  -H 'Content-Type: application/json' -d '{
    "resource_type": "ambulance", "resource_id": "<amb_id>",
    "reason": "breakdown"
  }'
```

**Expected:** `Failure detected → Ambulance failure → Verification failed →
Replanning → replacement dispatched → verified → family notified`, status
`recovered`.
