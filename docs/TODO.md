# TODO — Phase-2 Backlog

Everything below is **not started**. Phase 1 is the DecentraHack 2.0 final pitch
(9 Oct 2026); pick from this list only after that.

## High value

- [ ] **Auth & roles** — login for dispatchers vs view-only judges; protect
      `/api/demo/*` (today anyone can reset the fleet).
- [ ] **Alembic migrations** — replacing the current "delete `medrelay.db`" workflow
      (`create_all` on startup has no schema evolution story).
- [ ] **Real routing** — replace straight-line haversine ETA (40 km/h × 1.25) with a
      road-network ETA (OSRM / Google Routes), with live traffic.
- [ ] **SMS / calling integration** — actually deliver the drafted family messages
      (Twilio / local SMS gateway); add delivery receipts to the incident record.

## Medium value

- [ ] **Multi-city support** — seeded fleet is Pune-only; per-city fleet tables +
      geofencing which city's agents handle an incident.
- [x] **Automated tests** — DONE 2026-10-06: 246 checks across 4 suites
      (`backend/test_agents.py`, `test_orchestrator.py`, `test_recovery.py`,
      `test_demo.py`) — agent logic, gate, replan loop, recovery, demo scenarios,
      sample-dataset seeding.
- [ ] **Ambulance movement simulation** — units progress toward the scene over time
      instead of jumping `en_route`; en-route tracking on the map.
- [ ] **Hospital capacity model** — per-specialty bed counts and ER load instead of
      a single `free_beds` integer.
- [ ] **Resolve flow completion** — handover checklist, outcome recording (patient
      stabilized / transferred), post-incident report.

## Nice to have

- [ ] **i18n** — dashboard in Hindi/Marathi (agent messages are already EN/HI/MR).
- [ ] **Docker** — `docker-compose` for one-image deployment (today: `run.sh` + venv).
- [ ] **Config surface** — env-file for `STAGE_PAUSE_S`, speed/traffic factors, seed city.
- [ ] **LLM evaluation harness** — measure Gemini triage quality on unknown types vs the
      deterministic fallback before relying on it.
- [ ] **Audit export** — download an incident's audit trail as PDF for compliance.
