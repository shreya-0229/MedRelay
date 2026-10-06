"""DispatchAgent — picks the nearest capable ambulance from the live fleet.

Own backend tool: ``query_available_ambulances(db)`` reads the SQLite
ambulances table (IDs are never hardcoded). Candidates are filtered with
``check_capability`` (ALS rule) and the minimum-ETA unit wins. Failure
(no capable unit) returns status="failed" so the orchestrator replans.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from app import models
from app.agents.agent_registry import register
from app.agents.base_agent import BaseAgent
from app.schemas import AgentOutput, AmbulanceSelection, IncidentState
from app.seed import check_capability, get_eta


def query_available_ambulances(db: Session) -> list[models.Ambulance]:
    """Backend tool: every ambulance whose status is 'available'.

    Reads the live SQLite table — ambulance IDs are never hardcoded.
    """
    return (db.query(models.Ambulance)
              .filter(models.Ambulance.status == "available")
              .order_by(models.Ambulance.id)
              .all())


@register
class DispatchAgent(BaseAgent):
    """Fleet dispatcher: nearest ambulance that satisfies the ALS rule."""

    name = "DispatchAgent"
    version = "2.0"

    async def run(self, state: IncidentState) -> AgentOutput:
        db = self.db
        triage = state.triage_result
        severity = state.severity if state.severity is not None else (
            triage.severity if triage else 3)
        pathway = triage.pathway if triage else "general-er"
        lat, lon = state.location.lat, state.location.lon

        all_units = (db.query(models.Ambulance)
                       .order_by(models.Ambulance.id).all())
        available = query_available_ambulances(db)
        skipped_unavailable = len(all_units) - len(available)
        candidates = [a for a in available
                      if check_capability(a, pathway, severity)]

        if not candidates:
            state.selected_ambulance = None
            state.ambulance_status = "unavailable"
            decision = {
                "ambulance_id": None,
                "eta_min": None,
                "capabilities": None,
                "status": "unavailable",
                "candidates_considered": 0,
                "skipped_unavailable": skipped_unavailable,
            }
            return self.envelope(
                state, status="failed", confidence=0.0, decision=decision,
                reasoning_summary=(
                    f"No capable ambulance available for a severity "
                    f"{severity} {pathway} case."),
                warnings=["No available ambulance satisfies the ALS "
                          "capability rule for this case."],
                action="Dispatch failed — no capable unit")

        best = min(candidates, key=lambda a: get_eta(a, lat, lon))
        eta = get_eta(best, lat, lon)

        best.status = "en_route"
        best.assigned_incident = state.incident_id
        db.add(best)
        db.commit()

        state.selected_ambulance = AmbulanceSelection(
            id=best.id, capability=best.capability, eta_min=eta)
        state.ambulance_status = "en_route"

        decision = {
            "ambulance_id": best.id,
            "eta_min": eta,
            "capabilities": best.capability,
            "status": "en_route",
            "candidates_considered": len(candidates),
            "skipped_unavailable": skipped_unavailable,
        }
        return self.envelope(
            state, status="success", confidence=0.92, decision=decision,
            reasoning_summary=(f"Dispatched ambulance {best.id} "
                               f"({best.capability}), ETA {eta} min."),
            action="Ambulance dispatched")
