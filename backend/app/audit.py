"""Append-only audit writer + query helpers."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app import models
from app.schemas import AgentOutput


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def write_audit(db: Session, incident_id: str, *, agent: str, action: str,
                rationale: str = "", confidence: float = 0.0) -> models.AuditEvent:
    """Append one audit event and commit."""
    event = models.AuditEvent(incident_id=incident_id, ts=_utcnow(), agent=agent,
                              action=action, rationale=rationale,
                              confidence=confidence)
    db.add(event)
    db.commit()
    return event


def write_agent_run(db: Session, incident_id: str, out: AgentOutput) -> models.AgentRun:
    """Append one agent-run record and commit."""
    run = models.AgentRun(
        incident_id=incident_id,
        agent_name=out.agent_name,
        confidence=out.confidence,
        success=out.success,
        rationale=out.rationale,
        data_json=json.dumps(out.data, default=str),
        ts=_utcnow(),
    )
    db.add(run)
    db.commit()
    return run


def get_audit_events(db: Session, incident_id: str) -> list[models.AuditEvent]:
    """Audit events for an incident, chronological."""
    return (
        db.query(models.AuditEvent)
        .filter(models.AuditEvent.incident_id == incident_id)
        .order_by(models.AuditEvent.id.asc())
        .all()
    )


def get_agent_runs(db: Session, incident_id: str) -> list[models.AgentRun]:
    """Agent runs for an incident, chronological."""
    return (
        db.query(models.AgentRun)
        .filter(models.AgentRun.incident_id == incident_id)
        .order_by(models.AgentRun.id.asc())
        .all()
    )
