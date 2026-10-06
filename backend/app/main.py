"""MedRelay full-stack backend: FastAPI app, WebSocket hub, frontend hosting.

- create_all + seed on startup
- WS /ws with broadcast manager
- serves the frontend dist/ (if built) with an SPA catch-all
"""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app import models
from app.api import incident_list_payload, router as api_router
from app.llm import get_provider
from app.seed import fleet_snapshot, seed_fleet

DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class ConnectionManager:
    """WebSocket connection set with a broadcast() helper."""

    def __init__(self) -> None:
        self.active: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.active.discard(ws)

    async def broadcast(self, message: dict) -> None:
        dead: list[WebSocket] = []
        for ws in self.active:
            try:
                await ws.send_json(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.active.discard(ws)


manager = ConnectionManager()


@asynccontextmanager
async def lifespan(app: FastAPI):
    models.Base.metadata.create_all(models.engine)
    # Additive migration: existing DBs created before the is_sample column
    # need the column added by hand (create_all never alters tables).
    with models.engine.begin() as conn:
        cols = [row[1] for row in conn.exec_driver_sql("PRAGMA table_info(incidents)")]
        if "is_sample" not in cols:
            conn.exec_driver_sql(
                "ALTER TABLE incidents ADD COLUMN is_sample BOOLEAN DEFAULT 0")
    db = models.SessionLocal()
    try:
        seed_fleet(db)
    finally:
        db.close()
    yield


app = FastAPI(title="MedRelay", version="0.1.0", lifespan=lifespan)
app.state.session_factory = models.SessionLocal
app.state.llm = get_provider()
app.state.broadcast = manager.broadcast

app.include_router(api_router)


@app.websocket("/ws")
async def ws_endpoint(websocket: WebSocket) -> None:
    await manager.connect(websocket)
    db = models.SessionLocal()
    try:
        await websocket.send_json({"type": "fleet_update",
                                   "fleet": fleet_snapshot(db)})
        await websocket.send_json({"type": "incident_list",
                                   "incidents": incident_list_payload(db)})
    finally:
        db.close()
    try:
        while True:
            # Keep the connection alive; client messages are ignored.
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)


if (DIST / "index.html").exists():
    assets_dir = DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}")
    async def spa_fallback(path: str) -> FileResponse:
        # API routes and /ws are registered first, so they win over this
        # catch-all; everything else serves the SPA entry point.
        return FileResponse(DIST / "index.html")
else:
    @app.get("/")
    async def root() -> dict:
        return {"service": "medrelay-fullstack", "dashboard": False,
                "hint": "build the frontend"}
