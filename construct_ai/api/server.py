"""Service API : jumeau numérique en direct (WebSocket), replay, analytics, export."""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import deque

from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from ..batch import dataset, gold
from ..config import Topics, settings
from ..site.plan import build_plan
from ..stream.activity import ACTIONS, ROLE_LABELS

log = logging.getLogger("api")


class LiveHub:
    """Consomme les topics twin.* et ops.metrics et les diffuse aux navigateurs."""

    def __init__(self, bus) -> None:
        self.bus = bus
        self.clients: set[WebSocket] = set()
        self.last_state: dict | None = None
        self.blocks: list[dict] = []
        self.events: deque = deque(maxlen=300)
        self.metrics: dict[str, dict] = {}
        self._last_push = 0.0
        self.group = f"api-{uuid.uuid4().hex[:6]}"

    async def _broadcast(self, msg: dict) -> None:
        if not self.clients:
            return
        data = json.dumps(msg, separators=(",", ":"))
        dead = []
        for ws in list(self.clients):
            try:
                await asyncio.wait_for(ws.send_text(data), timeout=1.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.clients.discard(ws)

    async def run(self) -> None:
        # Reprise sur incident : l'avancement déjà persisté est relu depuis le data lake
        self.blocks = await asyncio.to_thread(gold.blocks_until, 1e12)
        asyncio.create_task(self._events_loop())
        asyncio.create_task(self._metrics_loop())
        async for _, state in self.bus.subscribe([Topics.TWIN_STATE], group=self.group + "-state"):
            self.last_state = state
            now = time.monotonic()
            if now - self._last_push >= 0.05:  # 20 Hz max vers les navigateurs
                self._last_push = now
                await self._broadcast({"type": "state", "data": state})

    async def _events_loop(self) -> None:
        known = {b["slot"] for b in self.blocks}
        async for _, ev in self.bus.subscribe([Topics.TWIN_EVENTS], group=self.group + "-events"):
            if ev["type"] == "block_placed" and ev["slot"] not in known:
                known.add(ev["slot"])
                self.blocks.append({"slot": ev["slot"], "t": ev["t"], "badge": ev["badge"]})
            self.events.append(ev)
            await self._broadcast({"type": "event", "data": ev})

    async def _metrics_loop(self) -> None:
        async for _, m in self.bus.subscribe([Topics.METRICS], group=self.group + "-metrics"):
            self.metrics[m.get("source", "?")] = m
            await self._broadcast({"type": "metrics", "data": self.pipeline()})

    def pipeline(self) -> dict:
        stats = self.bus.stats.snapshot()
        if self.bus.kind == "kafka":
            # chaque micro-service publie ses propres débits : on les agrège
            for src in ("edge", "stream"):
                remote = (self.metrics.get(src) or {}).get("bus_stats") or {}
                for key in ("total", "rate", "bytes_rate", "bytes_total"):
                    merged = dict(stats.get(key, {}))
                    for topic, v in remote.get(key, {}).items():
                        merged[topic] = merged.get(topic, 0) + v
                    stats[key] = merged
        return {"bus": self.bus.kind, "bus_stats": stats,
                "edge": self.metrics.get("edge"), "stream": self.metrics.get("stream")}


def create_app(bus) -> tuple[FastAPI, LiveHub]:
    app = FastAPI(title="ConStruct-AI", version="1.0",
                  description="Jumeau numérique de chantier & dataset pour la robotique")
    hub = LiveHub(bus)
    web = settings.web_dir

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(web / "index.html")

    app.mount("/web", StaticFiles(directory=web), name="web")

    @app.get("/api/site")
    async def site():
        return {"site_id": settings.site_id, "start": settings.site_start.isoformat(),
                "plan": build_plan().to_dict(), "actions": ACTIONS, "roles": ROLE_LABELS,
                "window": settings.window}

    @app.get("/api/snapshot")
    async def snapshot():
        return {"state": hub.last_state, "blocks": hub.blocks, "events": list(hub.events)[-60:],
                "pipeline": hub.pipeline()}

    @app.get("/api/replay")
    async def replay(t0: float = Query(...), t1: float = Query(...)):
        t1 = min(t1, t0 + 120)
        frames = await asyncio.to_thread(gold.replay, t0, t1)
        return {"t0": t0, "t1": t1, "frames": frames}

    @app.get("/api/blocks")
    async def blocks(until: float = Query(1e12)):
        return [b for b in hub.blocks if b["t"] <= until]

    @app.get("/api/analytics")
    async def analytics():
        return await asyncio.to_thread(gold.analytics)

    @app.get("/api/export/dataset.zip")
    async def export(tasks: str | None = None):
        task_list = [t for t in tasks.split(",") if t in dataset.TASK_TEXT] if tasks else None
        data, summary = await asyncio.to_thread(dataset.build_zip, task_list)
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="construct_ai_dataset_{settings.site_id}.zip"',
            "X-Episodes": str(summary["episodes"]), "X-Frames": str(summary["frames"])})

    @app.get("/api/export/summary")
    async def export_summary():
        episodes, _ = await asyncio.to_thread(dataset.build_episodes)
        by_task: dict[str, int] = {}
        for ep in episodes:
            by_task[ep["action"]] = by_task.get(ep["action"], 0) + 1
        return {"episodes": len(episodes), "frames": int(sum(len(e["states"]) for e in episodes)),
                "by_task": by_task}

    @app.post("/api/control")
    async def control(body: dict):
        if body.get("cmd") not in {"speed", "pause", "resume"}:
            return JSONResponse({"error": "commande inconnue"}, status_code=400)
        await bus.publish(Topics.CONTROL, body, key="control")
        return {"ok": True}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket):
        await ws.accept()
        hub.clients.add(ws)
        try:
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.clients.discard(ws)

    return app, hub
