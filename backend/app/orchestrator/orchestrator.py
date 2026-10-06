"""Thin Orchestrator: wires the Workflow, EventBus and StateManager.

``run_pipeline(report, ctx)`` keeps the exact signature and behavior the
API layer relies on; all stage logic lives in :mod:`workflow`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Awaitable, Callable

from sqlalchemy.orm import Session, sessionmaker

from app.llm import LLMProvider
from app.orchestrator.event_bus import EventBus
from app.orchestrator.state_manager import StateManager
from app.orchestrator.workflow import Workflow
from app.schemas import IncidentReport, IncidentState


@dataclass
class PipelineContext:
    """Everything run_pipeline needs that isn't the report itself."""

    session_factory: sessionmaker
    llm: LLMProvider
    broadcast: Callable[[dict], Awaitable[None]]


class Orchestrator:
    """Builds the per-run collaborators and executes the workflow."""

    def __init__(self, ctx: PipelineContext) -> None:
        self.ctx = ctx

    async def run(self, report: IncidentReport) -> IncidentState:
        db: Session = self.ctx.session_factory()
        try:
            bus = EventBus(db=db, broadcast=self.ctx.broadcast)
            state = StateManager(db)
            workflow = Workflow(db=db, llm=self.ctx.llm, report=report,
                                state=state, bus=bus)
            return await workflow.run()
        finally:
            db.close()


async def run_pipeline(report: IncidentReport,
                       ctx: PipelineContext) -> IncidentState:
    """Run the full agent pipeline for one incident report."""
    return await Orchestrator(ctx).run(report)
