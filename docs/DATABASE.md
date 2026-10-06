# Database

SQLite, file at **`backend/app/medrelay.db`**. Created with
`Base.metadata.create_all()` on every startup (no migrations — delete the file for a
pristine demo); fleet tables are seeded with `seed_fleet()` only when empty.

## Tables

**`incidents`** — one row per emergency; the full `IncidentState` is stored as JSON.

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `incident_id` | STRING(32) UNIQUE, indexed | e.g. `INC-20261006-0001` |
| `state_json` | TEXT | serialized `IncidentState` (updated after every pipeline stage) |
| `current_status` | STRING(32), default `received` | received → triaged → dispatched → verified → communicating → completed; or replanning / escalated |
| `escalation_status` | STRING(32), default `none` | `none` \| `replanning` \| `escalated` |
| `created_at` | DATETIME | UTC |

**`agent_runs`** — one recorded agent execution (append-only).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `incident_id` | STRING(32), FK → `incidents.incident_id`, indexed | |
| `agent_name` | STRING(64) | |
| `confidence` | FLOAT | |
| `success` | BOOLEAN | |
| `rationale` | TEXT | |
| `data_json` | TEXT | serialized agent `data` dict |
| `ts` | DATETIME | UTC |

**`audit_events`** — one audit-trail entry (append-only).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `incident_id` | STRING(32), FK → `incidents.incident_id`, indexed | |
| `ts` | DATETIME | UTC |
| `agent` | STRING(64) | agent name, or `Orchestrator` / `API` |
| `action` | STRING(128) | short label, e.g. "Ambulance dispatched" |
| `rationale` | TEXT | |
| `confidence` | FLOAT | |

**`ambulances`** — fleet units A1…A14.

| Column | Type | Notes |
|--------|------|-------|
| `id` | STRING(8) PK | `A1`–`A14` |
| `lat`, `lon` | FLOAT | station position |
| `capability` | STRING(8) | `ALS` \| `BLS` |
| `status` | STRING(32), default `available` | `available` \| `out_of_service` \| `en_route` |
| `assigned_incident` | STRING(32), nullable | incident id while `en_route` |

**`hospitals`** — h1…h8.

| Column | Type | Notes |
|--------|------|-------|
| `id` | STRING(8) PK | `h1`–`h8` |
| `name` | STRING(128) | |
| `lat`, `lon` | FLOAT | |
| `specialties` | JSON | list of pathways, e.g. `["cath-lab", "stroke-unit", "general-er"]` |
| `total_beds` | INTEGER | |
| `free_beds` | INTEGER | decremented on reservation; restored on `demo/reset` |

## Relationships

- `agent_runs.incident_id` → `incidents.incident_id` (many-to-one)
- `audit_events.incident_id` → `incidents.incident_id` (many-to-one)
- `ambulances` / `hospitals` are standalone fleet tables — agents mutate `status`,
  `assigned_incident`, and `free_beds` directly; the per-incident selections live
  inside `state_json` and are cross-checked against these tables by the
  VerificationAgent.

## Seed data (Pune, fictional-but-plausible)

**8 hospitals** (lat/lon around Pune center 18.5204, 73.8567):

| ID | Name | Specialties | Beds (total/free) |
|----|------|-------------|-------------------|
| h1 | PCCOE General Hospital | general-er, trauma-center, obstetric | 320 / 96 |
| h2 | Baner Lifeline Hospital | cath-lab, stroke-unit, general-er | 400 / 118 |
| h3 | Kothrud City Care | general-er, obstetric | 150 / 44 |
| h4 | Hadapsar Metro Hospital | cath-lab, general-er, stroke-unit | 280 / 71 |
| h5 | Viman Nagar Health Centre | general-er, trauma-center | 120 / 33 |
| h6 | Shivajinagar Trauma Institute | trauma-center, stroke-unit, general-er | 350 / 105 |
| h7 | Wakad Sunrise Hospital | cath-lab, obstetric, general-er | 220 / 58 |
| h8 | Katraj Community Hospital | general-er, obstetric | 80 / 24 |

**14 ambulances**: A1–A4 `ALS` available, **A5 `ALS` `out_of_service`**,
A6–A13 `BLS` available, **A14 `BLS` `out_of_service`** — i.e. 12 available of 14.

Geo helpers in `seed.py`: `haversine_km`, `get_eta` (40 km/h, 1.25× traffic),
`check_capability` (ALS required for severity ≥ 4 or `cath-lab`/`stroke-unit` pathways),
`check_beds` (pathway specialty + free bed).

## Append-only guarantee

`audit.py` exposes only `write_audit()` and `write_agent_run()` — both insert + commit.
No code path updates or deletes rows in `audit_events` or `agent_runs`; there is no
`DELETE FROM audit_events` anywhere in the codebase, and `POST /api/demo/reset`
wipes only the fleet tables. The audit trail for an incident is therefore tamper-proof
for the lifetime of the database file.
