"""Service EDGE : fait tourner le chantier simulé et publie les flux capteurs."""
from __future__ import annotations

import asyncio
import logging
import time

from ..config import Topics, settings
from .sensors import SensorRig
from .simulator import SiteSimulator

log = logging.getLogger("edge")


class EdgeService:
    def __init__(self, bus, speed: float | None = None) -> None:
        self.bus = bus
        self.sim = SiteSimulator()
        self.rig = SensorRig(self.sim)
        self.speed = speed or settings.speed
        self.paused = False

    def status(self) -> dict:
        return {
            "sim_t": round(self.sim.t, 2), "speed": self.speed, "paused": self.paused,
            "in_flight": self.rig.net.in_flight(), "sensor": dict(self.rig.stats),
            "buffered": dict(self.rig.net.buffered),
            "outage": {k: round(v - self.sim.t, 1) for k, v in self.rig.net.outage_until.items() if v > self.sim.t},
            "bus_stats": self.bus.stats.snapshot() if self.bus.kind == "kafka" else None,
        }

    async def _control(self) -> None:
        async for _, msg in self.bus.subscribe([Topics.CONTROL], group="edge-control"):
            cmd = msg.get("cmd")
            if cmd == "speed":
                self.speed = max(0.25, min(60.0, float(msg.get("value", self.speed))))
            elif cmd == "pause":
                self.paused = True
            elif cmd == "resume":
                self.paused = False
            log.info("Commande reçue : %s → vitesse x%s, pause=%s", msg, self.speed, self.paused)

    async def run(self) -> None:
        asyncio.create_task(self._control())
        log.info("Chantier %s : %d ouvriers, %d parpaings à poser, %d caméras fixes",
                 settings.site_id, len(self.sim.workers), len(self.sim.plan.slots), len(self.sim.plan.cameras))
        next_real = time.monotonic()
        last_status = 0.0
        while True:
            if self.paused:
                await asyncio.sleep(0.1)
                next_real = time.monotonic()
                continue
            self.sim.step()
            for topic, key, value in self.rig.step():
                await self.bus.publish(topic, value, key=key)
            if time.monotonic() - last_status > 1.0:
                last_status = time.monotonic()
                await self.bus.publish(Topics.METRICS, {"source": "edge", **self.status()}, key="edge")
            next_real += self.sim.dt / self.speed
            delay = next_real - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            else:
                await asyncio.sleep(0)
                if delay < -0.5:  # on ne rattrape pas indéfiniment le retard
                    next_real = time.monotonic()
