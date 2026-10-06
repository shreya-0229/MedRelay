# API Reference

Base URL: `http://localhost:8000`. All REST routes live under `/api`. The dashboard
is served at `/` and the WebSocket at `/ws`.

## Endpoints

### `GET /api/health`

Liveness + provider + inventory.

```json
{
  "status": "ok",
  "version": "0.1.0",
  "llm_provider": "deterministic",
  "incidents": 3,
  "agents": ["IntakeAgent", "TriageAgent", "DispatchAgent",
             "HospitalLiaisonAgent", "CommunicationAgent", "VerificationAgent"]
}
```

### `POST /api/incidents` → 201

Accepts a raw caller report and runs the **full agent pipeline synchronously**
(≈2–4 s with the default stage pauses). Responds with the complete `IncidentState`.

Request:

```json
{
  "incident_type": "cardiac_arrest",
  "lat": 18.5204,
  "lon": 73.8567,
  "address": "FC Road, Shivajinagar, Pune",
  "patient_count": 1,
  "symptoms": ["chest pain", "collapsed"],
  "breathing_status": "not_breathing",
  "bleeding_status": "none",
  "family_contact": "+91 98220 12345"
}
```

Response (201, trimmed):

```json
{
  "incident_id": "INC-20261006-0001",
  "created_at": "2026-10-06T02:10:00.123456+00:00",
  "incident_type": "cardiac_arrest",
  "severity": 5,
  "triage_result": {"severity": 5, "pathway": "cath-lab",
                    "confidence": 0.95, "rationale": "cardiac_arrest -> severity 5, pathway cath-lab"},
  "selected_ambulance": {"id": "A4", "capability": "ALS", "eta_min": 0.7},
  "selected_hospital": {"id": "h4", "name": "Hadapsar Metro Hospital", "distance_km": 7.4},
  "hospital_status": "reserved",
  "ambulance_status": "en_route",
  "escalation_status": "none",
  "current_status": "completed",
  "confidence": 0.9,
  "verification_results": {
    "passed": true, "issues": [],
    "checks": [
      {"name": "triage_valid", "passed": true, "detail": "severity=5, pathway=cath-lab"},
      {"name": "ambulance_capability_ok", "passed": true, "detail": "A4 capability=ALS, ALS required=True"},
      {"name": "ambulance_assignment_ok", "passed": true, "detail": "A4: status=en_route, assigned_incident=INC-20261006-0001"},
      {"name": "hospital_bed_ok", "passed": true, "detail": "h4: pathway cath-lab in ['cath-lab', 'general-er', 'stroke-unit'], hospital_status=reserved"},
      {"name": "resource_consistency_ok", "passed": true, "detail": "selections match agent outcomes"}
    ]
  },
  "agent_outputs": {"IntakeAgent": {"agent_name": "IntakeAgent", "confidence": 0.98, "success": true, ...}, ...},
  "communications": [{"channel": "sms_family_en", "text": "MedRelay update: …", "ts": "…"}, ...],
  "timeline": [{"ts": "…", "event": "received", "detail": "…"}, ...]
}
```

### `GET /api/incidents`

Newest-first summaries.

```json
{"incidents": [
  {"incident_id": "INC-20261006-0001", "incident_type": "cardiac_arrest",
   "severity": 5, "current_status": "completed", "escalation_status": "none",
   "created_at": "2026-10-06T02:10:00.123456+00:00",
   "ambulance_id": "A4", "hospital_name": "Hadapsar Metro Hospital"}
]}
```

### `GET /api/incidents/{incident_id}`

Full `IncidentState` (same shape as the 201 response). **404** if unknown.

### `GET /api/incidents/{incident_id}/audit`

Chronological audit events for one incident. **404** if the incident is unknown.

```json
{
  "incident_id": "INC-20261006-0001",
  "events": [
    {"id": 1, "ts": "2026-10-06T02:10:00.5+00:00", "agent": "IntakeAgent",
     "action": "Incident parsed", "rationale": "Parsed cardiac_arrest report: …",
     "confidence": 0.98},
    {"id": 7, "ts": "…", "agent": "VerificationAgent",
     "action": "Verification", "rationale": "All verification checks passed",
     "confidence": 0.99}
  ]
}
```

### `GET /api/fleet`

Current fleet snapshot (ambulances + hospitals).

```json
{
  "ambulances": [
    {"id": "A1", "lat": 18.53, "lon": 73.84, "capability": "ALS",
     "status": "en_route", "assigned_incident": "INC-20261006-0001"},
    {"id": "A5", "lat": 18.47, "lon": 73.88, "capability": "ALS",
     "status": "out_of_service", "assigned_incident": null}
  ],
  "hospitals": [
    {"id": "h1", "name": "PCCOE General Hospital", "lat": 18.545, "lon": 73.82,
     "specialties": ["general-er", "trauma-center", "obstetric"],
     "total_beds": 320, "free_beds": 96}
  ]
}
```

### `POST /api/incidents/{incident_id}/resolve`

Releases the assigned ambulance (`available`, unassigned), marks the incident
`completed`, appends a `resolved` timeline entry and an audit event. **404** if unknown.

```json
{"incident_id": "INC-20261006-0001", "current_status": "completed",
 "ambulance_status": "available", ...}
```

### `GET /api/stats`

Dashboard counters: `total`, `active` (not completed/escalated/failed), `completed`,
`escalated`, `avg_confidence` (mean of final incident confidences, `null` when empty).

### `POST /api/demo/reset`

Re-seeds the fleet to pristine state (all ambulances available except the two seeded
`out_of_service`, beds restored). Broadcasts `fleet_update`. Incidents and audit history
are **not** touched.

```json
{"status": "reset"}
```

### `POST /api/demo/fleet/ambulance/{amb_id}`

Failure-drill helper: force an ambulance's status. Body:
`{"status": "available" | "out_of_service" | "en_route"}`. Setting `available` clears
`assigned_incident`. **404** if the ambulance id is unknown. Broadcasts `fleet_update`.

```json
{"id": "A1", "lat": 18.53, "lon": 73.84, "capability": "ALS",
 "status": "out_of_service", "assigned_incident": null}
```

### `POST /api/incidents/{incident_id}/failures` → 200

**Autonomous failure recovery.** Inject a mid-plan failure (or a conflicting
agent output) on an active incident; the system runs the real recovery flow
and returns the updated `IncidentState`. **404** unknown incident, **409** when
the target isn't the actively assigned resource or the injection is invalid.

```json
// Scenario 1 — ambulance breakdown:
{"resource_type": "ambulance", "resource_id": "A1", "reason": "breakdown"}
// Scenario 2 — hospital outage:
{"resource_type": "hospital", "resource_id": "h2", "reason": "power outage"}
// Scenario 4 — conflicting agent output (test/drill hook):
{"resource_type": "conflict", "reason": "drill: tampered dispatch",
 "conflict": {"agent": "DispatchAgent",
              "decision": {"ambulance_id": "A6", "capabilities": "BLS",
                           "eta_min": 9.0}}}
```

Recovery sequence (ambulance): mark unit `out_of_service` → `FAILURE_DETECTED`
+ `AMBULANCE_FAILURE` → the real VerificationAgent validates the failure →
plan pauses (`replanning`) → `REPLANNING` → DispatchAgent re-searches (the
failed unit is excluded by its own availability query) → VerificationAgent
verifies the replacement → `RESOURCE_REPLACED` → trilingual family notice
(EN/HI/MR, persisted) → status `recovered`. Every step is audit-logged and
streamed over the WebSocket. No capable replacement → human escalation with
an escalation notice. Hospital recovery mirrors this with `HOSPITAL_FAILURE`.

### `GET /api/incidents/{incident_id}/review`

Operator review dossier for a paused incident (`current_status ==
"human_review_required"`): reason, affected decision, confidence, and the
full agent outputs. **404** unknown incident.

```json
{"incident_id": "INC-20261006-0002", "current_status": "human_review_required",
 "review": {"status": "pending", "reason": "Triage confidence below…",
            "affected_decision": "triage", "confidence": 0.55, ...},
 "agent_outputs": {"TriageAgent": {...}, "IntakeAgent": {...}}}
```

### `POST /api/incidents/{incident_id}/review/decision` → 200

Operator decision on a paused incident. Body:
`{"decision": "approve" | "reject" | "request_info" | "replan", "note": "…"}`.
**409** if the incident is not awaiting review. The decision + note are
audit-logged (`REVIEW_DECIDED`).

- `approve` — the operator takes responsibility for the flagged output
  (marked `human_approved`, exempt from the confidence floor); the pipeline
  resumes at the dispatch stage and still passes the verification gate.
- `reject` — incident becomes `cancelled`; nothing was reserved, nothing to release.
- `request_info` — incident stays paused, flagged as awaiting caller callback.
- `replan` — triage → dispatch → hospital → verification re-run honestly;
  pauses again if confidence is still low.

## WebSocket message catalog (`/ws`)

Frames are JSON objects with a `type` field. On connect the server pushes
`fleet_update` then `incident_list`; client frames are ignored.

- `agent_event` — after every agent stage:
  ```json
  {"type": "agent_event", "incident_id": "INC-20261006-0001",
   "agent": "DispatchAgent", "action": "Ambulance dispatched",
   "rationale": "A4 (ALS) dispatched, ETA 0.7 min; skipped 2 unavailable unit(s)",
   "confidence": 0.92, "ts": "2026-10-06T02:10:01.5+00:00"}
  ```
- `incident_update` — pipeline end (or any final state change):
  ```json
  {"type": "incident_update", "incident": { /* full IncidentState */ }}
  ```
- `fleet_update` — fleet changed (pipeline end, demo reset, fleet control):
  ```json
  {"type": "fleet_update", "fleet": { /* same shape as GET /api/fleet */ }}
  ```
- `incident_list` — newest-first summaries (on connect):
  ```json
  {"type": "incident_list", "incidents": [ /* IncidentSummary… */ ]}
  ```

## Error codes

| Code | When |
|------|------|
| `404` | Unknown incident id (get, audit, resolve, failures, review) or unknown ambulance id (demo fleet) — body `{"detail": "not found"}` |
| `409` | Recovery/review request that cannot be honored: failure target isn't the actively assigned resource, review decision on a non-paused incident, conflict injection with no detectable contradiction |
| `422` | `IncidentReport` validation fails (bad lat/lon range, `patient_count < 1`, wrong
types) or an invalid `status` in the demo fleet body (must be one of
`available`/`out_of_service`/`en_route`) |
