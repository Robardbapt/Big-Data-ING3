"""Capteurs « edge » : caméras fixes du chantier + caméras POV portées au torse.

Points clés (logique asynchrone) :
* chaque capteur a sa propre fréquence et sa propre latence réseau ;
* les body-cams perdent parfois le wifi : elles bufferisent puis renvoient
  en rafale (store & forward) → événements très en retard et désordonnés ;
* les caméras fixes sont masquées par les murs à mesure qu'ils montent.

Le temps d'un événement (``t``) est le temps de *capture* (event time), pas
le temps d'arrivée : c'est au stream processing de remettre de l'ordre.
"""
from __future__ import annotations

import heapq
import itertools
import math
import random
from datetime import timedelta

from ..config import Topics, settings
from ..site.navigation import segments_intersect
from .simulator import SiteSimulator, Worker


def iso(t: float) -> str:
    return (settings.site_start + timedelta(seconds=t)).isoformat()


class Network:
    """File d'envoi ordonnée par instant d'arrivée (temps simulé)."""

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self._heap: list = []
        self._seq = itertools.count()
        self.outage_until: dict[str, float] = {}
        self.buffered: dict[str, int] = {}

    def latency(self) -> float:
        base = self.rng.uniform(settings.net_latency_min, settings.net_latency_max)
        if self.rng.random() < 0.02:  # congestion ponctuelle
            base += self.rng.uniform(0.5, 1.8)
        return base

    def send(self, now: float, source: str, topic: str, key: str, value: dict) -> None:
        arrival = now + self.latency()
        until = self.outage_until.get(source, 0.0)
        if until > now:  # hors couverture : stocké sur la carte SD, renvoyé à la reconnexion
            arrival = until + self.rng.uniform(0.05, 0.6)
            self.buffered[source] = self.buffered.get(source, 0) + 1
        heapq.heappush(self._heap, (arrival, next(self._seq), topic, key, value))

    def due(self, now: float):
        while self._heap and self._heap[0][0] <= now:
            _, _, topic, key, value = heapq.heappop(self._heap)
            yield topic, key, value

    def in_flight(self) -> int:
        return len(self._heap)


class SensorRig:
    def __init__(self, sim: SiteSimulator) -> None:
        self.sim = sim
        self.rng = random.Random(sim.rng.random())
        self.net = Network(self.rng)
        self.frame = itertools.count()
        # dérive d'odométrie visuelle-inertielle par body-cam
        self.odom_drift = {w.badge: [0.0, 0.0] for w in sim.workers}
        self.heading_bias = {w.badge: self.rng.gauss(0, 0.03) for w in sim.workers}
        self._walls = [((wl.x0, wl.z0), (wl.x1, wl.z1), wl.name) for wl in sim.plan.walls]
        self._ticks = 0
        self.stats = {"captured": 0, "occluded": 0, "outages": 0}

    # ---------------------------------------------------------------- caméras fixes
    def _occluded(self, cam: dict, w: Worker) -> bool:
        p, q = (cam["x"], cam["z"]), (w.x, w.z)
        for a, b, name in self._walls:
            if segments_intersect(p, q, a, b) and self.sim.wall_height(name) > 1.1:
                return True
        return False

    def _fixed_cameras(self) -> None:
        t = self.sim.t
        for cam in self.sim.plan.cameras:
            detections = []
            for w in self.sim.workers:
                dx, dz = w.x - cam["x"], w.z - cam["z"]
                dist = math.hypot(dx, dz)
                ang = abs((math.atan2(dx, dz) - cam["yaw"] + math.pi) % (2 * math.pi) - math.pi)
                if dist > cam["range"] or ang > cam["fov"] / 2:
                    continue
                if self._occluded(cam, w):
                    self.stats["occluded"] += 1
                    continue
                if self.rng.random() > 0.93:  # détection ratée (soleil, poussière...)
                    continue
                sigma = 0.05 + 0.012 * dist
                detections.append({
                    "tag": w.badge,
                    "x": round(w.x + self.rng.gauss(0, sigma), 3),
                    "z": round(w.z + self.rng.gauss(0, sigma), 3),
                    "sigma": round(sigma, 3),
                    "conf": round(max(0.3, 0.98 - dist / 60 + self.rng.gauss(0, 0.03)), 3),
                    "h_px": int(900 / max(dist, 1.0)),
                })
            event = {"cam": cam["id"], "site": settings.site_id, "t": round(t, 3), "ts": iso(t),
                     "frame": next(self.frame), "detections": detections}
            self.stats["captured"] += 1
            self.net.send(t, cam["id"], Topics.FIXED_CAM, cam["id"], event)

    # ---------------------------------------------------------------- body-cams POV
    def _pov(self, w: Worker) -> None:
        t = self.sim.t
        drift = self.odom_drift[w.badge]
        if w.walk > 0:
            drift[0] += self.rng.gauss(0, 0.012)
            drift[1] += self.rng.gauss(0, 0.012)
        pose = w.pose_local()
        noisy = [[round(v + self.rng.gauss(0, 0.008), 3) for v in joint] for joint in pose.tolist()]
        event = {
            "badge": w.badge, "site": settings.site_id, "t": round(t, 3), "ts": iso(t),
            "frame": next(self.frame),
            "pose": noisy,  # 15 articulations, repère corps (latéral, haut, avant)
            "heading": round(w.heading + self.heading_bias[w.badge] + self.rng.gauss(0, 0.02), 4),
            "odom": [round(w.x + drift[0], 3), round(w.z + drift[1], 3)],
            "elev": round(w.elevation, 2),
            "contacts": dict(w.contacts),
        }
        self.net.send(t, f"pov:{w.badge}", Topics.POV, w.badge, event)

    def _outages(self) -> None:
        t = self.sim.t
        for w in self.sim.workers:
            src = f"pov:{w.badge}"
            if self.net.outage_until.get(src, 0) < t and self.rng.random() < settings.pov_outage_prob_per_s * self.sim.dt:
                self.net.outage_until[src] = t + self.rng.uniform(*settings.pov_outage_duration)
                self.stats["outages"] += 1

    # ---------------------------------------------------------------- tick
    def step(self) -> list[tuple[str, str, dict]]:
        """Capture à l'instant courant et retourne les messages arrivés au broker."""
        self._ticks += 1
        tick_hz = 1.0 / self.sim.dt
        self._outages()
        if self._ticks % max(1, int(tick_hz / settings.fixed_cam_hz)) == 0:
            self._fixed_cameras()
        if self._ticks % max(1, int(tick_hz / settings.pov_hz)) == 0:
            for w in self.sim.workers:
                self._pov(w)
        for ev in self.sim.events:
            ev["site"], ev["ts"] = settings.site_id, iso(ev["t"])
            self.net.send(self.sim.t, f"pov:{ev['badge']}", Topics.INTERACTIONS, ev["badge"], ev)
        self.sim.events.clear()
        # vérité terrain (1 Hz) : publiée immédiatement, utilisée uniquement pour l'évaluation
        out = []
        if self._ticks % int(tick_hz) == 0:
            for w in self.sim.workers:
                out.append((Topics.GROUND_TRUTH, w.badge, {
                    "badge": w.badge, "t": round(self.sim.t, 3), "x": round(w.x, 3), "z": round(w.z, 3),
                    "action": w.action, "role": w.role}))
        out.extend(self.net.due(self.sim.t))
        return out
