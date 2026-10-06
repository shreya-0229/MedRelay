"""Shared IncidentState ownership.

The StateManager is the sole authority that mutates and persists the
IncidentState for one pipeline run. Agents receive the state for reading
and return structured outputs; the manager validates (Pydantic) and
persists to the ``incidents`` table after every mutation, so no
uncontrolled or half-written state can reach the database.

``update()`` re-validates the *whole* state, so an invalid value (e.g.
``confidence=2.0``) raises ``pydantic.ValidationError`` instead of
corrupting the row.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app import models
from app.schemas import (
    AgentOutput,
    IncidentReport,
    IncidentState,
    Location,
    TimelineEntry,
)

# Pune city-centre fallback (mirrors IntakeAgent when no coords given).
_FALLBACK_LAT = 18.5204
_FALLBACK_LON = 73.8567


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class StateManager:
    """Owns one incident's IncidentState + its DB row."""

    def __init__(self, db: Session) -> None:
        self._db = db
        self._state: IncidentState | None = None
        self._row: models.Incident | None = None

    # -- lifecycle ------------------------------------------------------

    def create(self, report: IncidentReport) -> IncidentState:
        """Build the initial state and insert the incident row."""
        now = _utcnow()
        day = now.strftime("%Y%m%d")
        prefix = f"INC-{day}-"
        seq = (self._db.query(models.Incident)
               .filter(models.Incident.incident_id.like(prefix + "%"))
               .count() + 1)
        incident_id = f"{prefix}{seq:04d}"

        state = IncidentState(
            incident_id=incident_id,
            created_at=now,
            incident_type=report.incident_type,
            location=Location(
                lat=report.lat if report.lat is not None else _FALLBACK_LAT,
                lon=report.lon if report.lon is not None else _FALLBACK_LON,
                address=report.address or ""),
        )
        row = models.Incident(
            incident_id=incident_id,
            state_json=state.model_dump_json(),
            current_status=state.current_status,
            escalation_status=state.escalation_status,
            created_at=now,
        )
        self._db.add(row)
        self._db.commit()
        self._state = state
        self._row = row
        return state

    @property
    def state(self) -> IncidentState:
        """The live shared state (read-only for agents)."""
        if self._state is None:
            raise RuntimeError("StateManager.create() must be called first")
        return self._state

    def load(self, incident_id: str) -> IncidentState:
        """Attach to an existing incident row (recovery / review flows).

        Raises:
            LookupError: if no incident with that id exists.
        """
        row = (self._db.query(models.Incident)
               .filter(models.Incident.incident_id == incident_id)
               .first())
        if row is None:
            raise LookupError(f"incident {incident_id} not found")
        self._state = IncidentState.model_validate_json(row.state_json)
        self._row = row
        return self._state

    # -- validated mutation (the only write paths) ----------------------

    def update(self, **fields: Any) -> IncidentState:
        """Set top-level state fields; the full state is re-validated.

        Raises:
            pydantic.ValidationError: on any invalid value.
            RuntimeError: if called before :meth:`create`.
        """
        data = self.state.model_dump()
        data.update(fields)
        self._state = IncidentState.model_validate(data)
        self.persist()
        return self._state

    def apply_agent_output(self, agent_name: str,
                           out: AgentOutput) -> IncidentState:
        """Record an agent's output, then re-validate + persist the state."""
        self._state.agent_outputs[agent_name] = out
        self._state = IncidentState.model_validate(
            self._state.model_dump())
        self.persist()
        return self._state

    def note(self, event: str, detail: str = "") -> None:
        """Append a timeline entry and persist."""
        self.state.timeline.append(
            TimelineEntry(ts=_utcnow(), event=event, detail=detail))
        self.persist()

    def persist(self) -> None:
        """Write the current state back to the incident row."""
        assert self._state is not None and self._row is not None
        self._row.state_json = self._state.model_dump_json()
        self._row.current_status = self._state.current_status
        self._row.escalation_status = self._state.escalation_status
        self._db.add(self._row)
        self._db.commit()
