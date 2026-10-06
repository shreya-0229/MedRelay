"""HospitalAgent (registered as "HospitalLiaisonAgent" for the frontend
contract) — reserves a bed at the nearest hospital whose specialties cover
the triage care pathway.

Own backend tools: ``query_hospitals(db)``, ``eligible_hospitals`` (bed +
specialty match via ``check_beds``), and ``reserve_bed`` (decrements the
live counter and commits).
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import models
from app.agents.agent_registry import register
from app.agents.base_agent import BaseAgent
from app.schemas import AgentOutput, HospitalSelection, IncidentState
from app.seed import check_beds, haversine_km


def query_hospitals(db: Session) -> list[models.Hospital]:
    """Backend tool: every hospital row from the live SQLite table."""
    return (db.query(models.Hospital)
              .order_by(models.Hospital.id)
              .all())


def eligible_hospitals(hospitals: list[models.Hospital],
                       pathway: str) -> list[models.Hospital]:
    """Hospitals that list the pathway as a specialty AND have a free bed."""
    return [h for h in hospitals if check_beds(h, pathway)]


def reserve_bed(db: Session, hosp: models.Hospital) -> int:
    """Decrement the hospital's free-bed counter, commit, return remaining."""
    hosp.free_beds = max(0, (hosp.free_beds or 0) - 1)
    db.add(hosp)
    db.commit()
    return hosp.free_beds


@register
class HospitalAgent(BaseAgent):
    """Hospital liaison: nearest eligible hospital, bed reserved."""

    name = "HospitalLiaisonAgent"
    version = "2.0"

    async def run(self, state: IncidentState) -> AgentOutput:
        db = self.db
        triage = state.triage_result
        pathway = triage.pathway if triage else "general-er"
        lat, lon = state.location.lat, state.location.lon

        hospitals = query_hospitals(db)
        eligible = eligible_hospitals(hospitals, pathway)

        if not eligible:
            state.selected_hospital = None
            state.hospital_status = "unavailable"
            decision = {
                "hospital_id": None,
                "hospital_name": None,
                "available_capacity": 0,
                "specialty": pathway,
                "distance_km": None,
                "reservation_status": "failed",
            }
            return self.envelope(
                state, status="failed", confidence=0.0, decision=decision,
                reasoning_summary=(
                    f"No hospital with a free {pathway} bed is available."),
                warnings=[f"No eligible hospital for pathway {pathway!r}: "
                          "missing specialty or no free beds."],
                action="Hospital reservation failed")

        best = min(eligible,
                   key=lambda h: haversine_km(lat, lon, h.lat, h.lon))
        distance = round(haversine_km(lat, lon, best.lat, best.lon), 1)
        remaining = reserve_bed(db, best)

        state.selected_hospital = HospitalSelection(
            id=best.id, name=best.name, distance_km=distance)
        state.hospital_status = "reserved"

        decision = {
            "hospital_id": best.id,
            "hospital_name": best.name,
            "available_capacity": remaining,
            "specialty": pathway,
            "distance_km": distance,
            "reservation_status": "reserved",
        }
        return self.envelope(
            state, status="success", confidence=0.9, decision=decision,
            reasoning_summary=(f"Reserved a {pathway} bed at {best.name} "
                               f"({distance} km away, {remaining} beds left)."),
            action="Hospital bed reserved")
