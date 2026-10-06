"""Pune city fleet data + geo helpers, adapted from v1 (DB-backed).

Demo simplifications (honest, for the jury):
- Ambulances drive in straight lines at 40 km/h with a flat 1.25x traffic
  factor — no road network, no routing.
- Hospital beds are plain integer counters in the DB.
"""
from __future__ import annotations

import math

from sqlalchemy.orm import Session

from app import models

# Pune city center (approx).
PUNE_CENTER = (18.5204, 73.8567)

# Fictional-but-plausible Pune hospitals (~33 km x 33 km box around center).
HOSPITALS: list[dict] = [
    {"id": "h1", "name": "PCCOE General Hospital", "lat": 18.5450, "lon": 73.8200,
     "specialties": ["general-er", "trauma-center", "obstetric"], "total_beds": 320, "free_beds": 96},
    {"id": "h2", "name": "Baner Lifeline Hospital", "lat": 18.5642, "lon": 73.7777,
     "specialties": ["cath-lab", "stroke-unit", "general-er"], "total_beds": 400, "free_beds": 118},
    {"id": "h3", "name": "Kothrud City Care", "lat": 18.5045, "lon": 73.8067,
     "specialties": ["general-er", "obstetric"], "total_beds": 150, "free_beds": 44},
    {"id": "h4", "name": "Hadapsar Metro Hospital", "lat": 18.5074, "lon": 73.9252,
     "specialties": ["cath-lab", "general-er", "stroke-unit"], "total_beds": 280, "free_beds": 71},
    {"id": "h5", "name": "Viman Nagar Health Centre", "lat": 18.5645, "lon": 73.9154,
     "specialties": ["general-er", "trauma-center"], "total_beds": 120, "free_beds": 33},
    {"id": "h6", "name": "Shivajinagar Trauma Institute", "lat": 18.5314, "lon": 73.8553,
     "specialties": ["trauma-center", "stroke-unit", "general-er"], "total_beds": 350, "free_beds": 105},
    {"id": "h7", "name": "Wakad Sunrise Hospital", "lat": 18.6008, "lon": 73.7623,
     "specialties": ["cath-lab", "obstetric", "general-er"], "total_beds": 220, "free_beds": 58},
    {"id": "h8", "name": "Katraj Community Hospital", "lat": 18.4529, "lon": 73.8619,
     "specialties": ["general-er", "obstetric"], "total_beds": 80, "free_beds": 24},
]

# 14 ambulances: 5 ALS + 9 BLS, 12 available + 2 out of service.
AMBULANCES: list[dict] = [
    {"id": "A1",  "lat": 18.53, "lon": 73.84, "capability": "ALS", "status": "available"},
    {"id": "A2",  "lat": 18.56, "lon": 73.78, "capability": "ALS", "status": "available"},
    {"id": "A3",  "lat": 18.50, "lon": 73.90, "capability": "ALS", "status": "available"},
    {"id": "A4",  "lat": 18.52, "lon": 73.86, "capability": "ALS", "status": "available"},
    {"id": "A5",  "lat": 18.47, "lon": 73.88, "capability": "ALS", "status": "out_of_service"},
    {"id": "A6",  "lat": 18.54, "lon": 73.82, "capability": "BLS", "status": "available"},
    {"id": "A7",  "lat": 18.51, "lon": 73.81, "capability": "BLS", "status": "available"},
    {"id": "A8",  "lat": 18.58, "lon": 73.91, "capability": "BLS", "status": "available"},
    {"id": "A9",  "lat": 18.49, "lon": 73.93, "capability": "BLS", "status": "available"},
    {"id": "A10", "lat": 18.60, "lon": 73.76, "capability": "BLS", "status": "available"},
    {"id": "A11", "lat": 18.45, "lon": 73.86, "capability": "BLS", "status": "available"},
    {"id": "A12", "lat": 18.55, "lon": 73.90, "capability": "BLS", "status": "available"},
    {"id": "A13", "lat": 18.52, "lon": 73.79, "capability": "BLS", "status": "available"},
    {"id": "A14", "lat": 18.48, "lon": 73.84, "capability": "BLS", "status": "out_of_service"},
]

AVG_SPEED_KMH = 40.0     # demo cruise speed
TRAFFIC_FACTOR = 1.25    # flat Pune-traffic penalty, applied to ETAs


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def get_eta(amb: models.Ambulance, lat: float, lon: float) -> float:
    """ETA in minutes from ambulance position to a point (straight-line,
    40 km/h, with the 1.25x traffic factor)."""
    dist = haversine_km(amb.lat, amb.lon, lat, lon)
    return round(dist / AVG_SPEED_KMH * 60 * TRAFFIC_FACTOR, 1)


def check_capability(amb: models.Ambulance, pathway: str, severity: int) -> bool:
    """Can this ambulance serve the case? ALS is required for cath-lab /
    stroke-unit pathways or severity >= 4; BLS is fine otherwise."""
    if amb.status != "available":
        return False
    needs_als = pathway in ("cath-lab", "stroke-unit") or severity >= 4
    return amb.capability == "ALS" if needs_als else True


def check_beds(hosp: models.Hospital, pathway: str) -> bool:
    """Hospital can accept the case: pathway in its specialties AND a bed free."""
    return pathway in (hosp.specialties or []) and (hosp.free_beds or 0) > 0


def seed_fleet(db: Session) -> None:
    """Insert the 8 hospitals + 14 ambulances if the fleet tables are empty."""
    if db.query(models.Ambulance).count() == 0:
        for a in AMBULANCES:
            db.add(models.Ambulance(id=a["id"], lat=a["lat"], lon=a["lon"],
                                    capability=a["capability"], status=a["status"],
                                    assigned_incident=None))
    if db.query(models.Hospital).count() == 0:
        for h in HOSPITALS:
            db.add(models.Hospital(id=h["id"], name=h["name"], lat=h["lat"],
                                   lon=h["lon"], specialties=h["specialties"],
                                   total_beds=h["total_beds"], free_beds=h["free_beds"]))
    db.commit()


def reseed_fleet(db: Session) -> None:
    """Wipe the fleet and re-insert pristine city data (demo reset)."""
    db.query(models.Ambulance).delete()
    db.query(models.Hospital).delete()
    db.commit()
    seed_fleet(db)


def fleet_snapshot(db: Session) -> dict:
    """Current fleet state, shaped for /api/fleet and WS fleet_update."""
    ambs = db.query(models.Ambulance).all()
    hosps = db.query(models.Hospital).all()
    ambs.sort(key=lambda a: int(a.id[1:]))
    hosps.sort(key=lambda h: int(h.id[1:]))
    return {
        "ambulances": [
            {"id": a.id, "lat": a.lat, "lon": a.lon, "capability": a.capability,
             "status": a.status, "assigned_incident": a.assigned_incident}
            for a in ambs
        ],
        "hospitals": [
            {"id": h.id, "name": h.name, "lat": h.lat, "lon": h.lon,
             "specialties": h.specialties, "total_beds": h.total_beds,
             "free_beds": h.free_beds}
            for h in hosps
        ],
    }
