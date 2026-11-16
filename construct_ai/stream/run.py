"""Service STREAM : consumers asynchrones indépendants (un *consumer group* chacun).

    edge.* ──► [stream-fusion]   fusion event-time ──► twin.state (+ alertes ergonomie)
    edge.pov.interactions ──► [stream-progress]  BIM matching ──► twin.events
    edge.* ──► [lake-bronze]     brut ──► Parquet bronze
    twin.* ──► [lake-silver]     ──► Parquet silver
"""
from __future__ import annotations

import asyncio
import logging


from ..config import Topics, settings
from .activity import trunk_bend
from .fusion import FusionEngine
from .lake import LakeWriter
from .progress import ProgressTracker

log = logging.getLogger("stream")


class StreamService:
    def __init__(self, bus, lake: LakeWriter | None = None) -> None:
        self.bus = bus
        self.fusion = FusionEngine()
        self.progress = ProgressTracker()
        self.lake = lake or LakeWriter()
        self.last_state_t = 0.0
        self._posture: dict[str, int] = {}
        self._last_alert: dict[str, float] = {}
        self.alerts = 0
        self.latency_ms: list[float] = []

    # ------------------------------------------------------------------ fusion
    async def fusion_loop(self) -> None:
        topics = [Topics.FIXED_CAM, Topics.POV, Topics.GROUND_TRUTH]
        async for topic, ev in self.bus.subscribe(topics, group="stream-fusion"):
            self.fusion.ingest(topic, ev)
            for state in self.fusion.poll():
                self.last_state_t = state["t"]
                await self.bus.publish(Topics.TWIN_STATE, state, key=settings.site_id)
                for alert in self._ergonomics(state):
                    await self.bus.publish(Topics.TWIN_EVENTS, alert, key=alert["badge"])

    def _ergonomics(self, state: dict) -> list[dict]:
        """Alerte TMS : dos fléchi > ~50° en portant une charge pendant > 1 s."""
        alerts = []
        for w in state["workers"]:
            if not w["pose"]:
                continue
            tr = self.fusion.tracks[w["badge"]]
            loaded = "parpaing" in (w.get("contacts") or {}).values()
            if loaded and tr.pose_local is not None and trunk_bend(tr.pose_local) > 0.66:
                self._posture[w["badge"]] = self._posture.get(w["badge"], 0) + 1
            else:
                self._posture[w["badge"]] = 0
            if self._posture[w["badge"]] >= 5 and state["t"] - self._last_alert.get(w["badge"], -1e9) > 45:
                self._last_alert[w["badge"]] = state["t"]
                self.alerts += 1
                alerts.append({"type": "posture_risque", "t": state["t"], "ts": state["ts"],
                               "badge": w["badge"], "x": w["x"], "z": w["z"],
                               "message": "Flexion du dos > 50° avec charge (risque TMS)"})
        return alerts

    # ------------------------------------------------------------------ BIM / avancement
    async def progress_loop(self) -> None:
        async for _, ev in self.bus.subscribe([Topics.INTERACTIONS], group="stream-progress"):
            out = None
            if ev["type"] == "block_release":
                out = self.progress.on_release(ev)
            elif ev["type"] in ("mortar_delivered", "barrow_unloaded", "segment_done"):
                out = {k: v for k, v in ev.items() if k != "site"}
            if out:
                out.setdefault("ts", ev.get("ts"))
                await self.bus.publish(Topics.TWIN_EVENTS, out, key=ev["badge"])

    # ------------------------------------------------------------------ data lake
    async def bronze_loop(self) -> None:
        async for topic, ev in self.bus.subscribe(Topics.EDGE, group="lake-bronze"):
            self.lake.bronze(topic, ev, ev.get("badge") or ev.get("cam") or "")
            if self.lake.should_flush():
                await self.lake.flush()

    async def silver_loop(self) -> None:
        async for topic, ev in self.bus.subscribe([Topics.TWIN_STATE, Topics.TWIN_EVENTS], group="lake-silver"):
            if topic == Topics.TWIN_STATE:
                self.lake.track_rows(ev)
            else:
                self.lake.event_row(ev)
            if self.lake.should_flush():
                await self.lake.flush()

    async def periodic_flush(self) -> None:
        while True:
            await asyncio.sleep(settings.lake_flush_seconds)
            if self.lake.pending_rows():
                await self.lake.flush()

    # ------------------------------------------------------------------ métriques
    def metrics(self) -> dict:
        f = self.fusion
        return {
            "source": "stream",
            "fusion": {**f.metrics, "watermark": round(f.watermark, 2), "max_event_t": round(f.max_t, 2),
                       "open_windows": len(f.buffers), "allowed_lateness": f.lateness, "window": f.W},
            "evaluation": f.evaluation(),
            "progress": self.progress.summary(self.last_state_t),
            "lake": self.lake.stats(),
            "alerts": self.alerts,
            "roles": {b: f.roles.role(b) for b in f.tracks},
            "bus_stats": self.bus.stats.snapshot() if self.bus.kind == "kafka" else None,
        }

    async def metrics_loop(self) -> None:
        while True:
            await asyncio.sleep(1.0)
            await self.bus.publish(Topics.METRICS, self.metrics(), key="stream")

    async def run(self) -> None:
        await asyncio.gather(self.fusion_loop(), self.progress_loop(), self.bronze_loop(),
                             self.silver_loop(), self.periodic_flush(), self.metrics_loop())
