"""SQLAlchemy 2.0 models + sync engine/session setup (SQLite).

Sync SQLAlchemy is a deliberate prototype choice: one session per request /
pipeline run, single-threaded event loop, no async engine needed.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Iterator

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


class Base(DeclarativeBase):
    """Declarative base for all MedRelay tables."""


class Incident(Base):
    """One emergency incident. The full IncidentState is stored as JSON."""

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    state_json: Mapped[str] = mapped_column(Text)
    current_status: Mapped[str] = mapped_column(String(32), default="received")
    escalation_status: Mapped[str] = mapped_column(String(32), default="none")
    created_at: Mapped[datetime] = mapped_column(DateTime)
    # Demo flag: seeded "sample day" rows for the judging dashboard.
    # Additive column — existing rows default to False.
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class AgentRun(Base):
    """One recorded agent execution (append-only)."""

    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("incidents.incident_id"), index=True)
    agent_name: Mapped[str] = mapped_column(String(64))
    confidence: Mapped[float] = mapped_column(Float)
    success: Mapped[bool] = mapped_column(Boolean)
    rationale: Mapped[str] = mapped_column(Text, default="")
    data_json: Mapped[str] = mapped_column(Text, default="{}")
    ts: Mapped[datetime] = mapped_column(DateTime)


class AuditEvent(Base):
    """One audit-trail entry (append-only)."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("incidents.incident_id"), index=True)
    ts: Mapped[datetime] = mapped_column(DateTime)
    agent: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(128))
    rationale: Mapped[str] = mapped_column(Text, default="")
    confidence: Mapped[float] = mapped_column(Float, default=0.0)


class CommunicationMessage(Base):
    """One outbound message drafted by CommunicationAgent (append-only).

    Every message the agent generates is persisted here — the incident's
    state_json keeps a copy for the dashboard, but this table is the
    durable record.
    """

    __tablename__ = "communications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    incident_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("incidents.incident_id"), index=True)
    channel: Mapped[str] = mapped_column(String(64))   # family | bystander | hospital | escalation
    language: Mapped[str] = mapped_column(String(8), default="en")  # en | hi | mr
    text: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(DateTime)


class Ambulance(Base):
    """Fleet unit A1..A14."""

    __tablename__ = "ambulances"

    id: Mapped[str] = mapped_column(String(8), primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    capability: Mapped[str] = mapped_column(String(8))  # ALS | BLS
    status: Mapped[str] = mapped_column(String(32), default="available")
    assigned_incident: Mapped[str | None] = mapped_column(String(32), nullable=True)


class Hospital(Base):
    """Hospital h1..h8."""

    __tablename__ = "hospitals"

    id: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    specialties: Mapped[list] = mapped_column(JSON)
    total_beds: Mapped[int] = mapped_column(Integer)
    free_beds: Mapped[int] = mapped_column(Integer)


# --- Engine / session -------------------------------------------------------

DB_PATH = Path(__file__).resolve().parent / "medrelay.db"
engine = create_engine(f"sqlite:///{DB_PATH}", connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
