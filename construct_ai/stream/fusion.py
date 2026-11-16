"""Fusion multi-capteurs en *event time* avec fenêtres tumbling et watermark.

Les messages arrivent dans le désordre (latence réseau variable, body-cams
qui renvoient leurs données en rafale après une coupure wifi). On applique la
stratégie classique des moteurs de streaming (Flink / Spark Structured
Streaming) :

* chaque message est rangé dans la fenêtre de son **temps de capture** ;
* ``watermark = max(event_time vu) - allowed_lateness`` ;
* une fenêtre est **fermée** (calculée puis émise) quand le watermark dépasse
  sa fin ; un message qui arrive après est compté comme *late* (il reste dans
  la couche bronze du data lake et sera pris en compte par le batch).

Pour chaque fenêtre et chaque ouvrier on fusionne :
* les détections des caméras fixes (moyenne pondérée par l'inverse de la variance) ;
* l'odométrie de la body-cam, recalée sur les caméras (estimation du biais de dérive) ;
* la pose 3D ego de la body-cam, replacée dans le repère monde.
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import timedelta

import numpy as np

from ..config import settings
from ..site.skeleton import to_world
from .activity import ActivityClassifier, RoleInference


@dataclass
class Track:
    badge: str
    x: float | None = None
    z: float | None = None
    heading: float = 0.0
    speed: float = 0.0
    bias: tuple[float, float] = (0.0, 0.0)
    last_fix_t: float = -1e9  # dernier recalage caméra
    last_seen_t: float = -1e9
    pose_local: np.ndarray | None = None
    contacts: dict = field(default_factory=dict)
    elev: float = 0.0
    action: str = "attente"
    action_conf: float = 0.0
    sources: int = 0
    seen_by: dict = field(default_factory=dict)  # capteur -> dernier instant vu


@dataclass
class WindowBuffer:
    cams: dict = field(default_factory=lambda: defaultdict(list))  # badge -> [(x,z,sigma)]
    pov: dict = field(default_factory=dict)  # badge -> dernier frame POV de la fenêtre
    gt: dict = field(default_factory=dict)  # badge -> vérité terrain (évaluation)


class FusionEngine:
    def __init__(self, window: float | None = None, lateness: float | None = None) -> None:
        self.W = window or settings.window
        self.lateness = lateness if lateness is not None else settings.allowed_lateness
        self.buffers: dict[int, WindowBuffer] = {}
        self.max_t = 0.0
        self.last_closed = -1
        self.tracks: dict[str, Track] = {}
        self.classifier = ActivityClassifier()
        self.roles = RoleInference()
        self.metrics = {"late": 0, "out_of_order": 0, "ingested": 0, "windows": 0}
        self.source_max: dict[str, float] = {}
        self.eval = {"n": 0, "action_ok": 0, "pos_err_sum": 0.0, "confusion": defaultdict(int)}

    # ------------------------------------------------------------------ ingestion
    def _window(self, t: float) -> int | None:
        w = int(t // self.W)
        if w <= self.last_closed:
            self.metrics["late"] += 1
            return None
        return w

    def _track_source(self, source: str, t: float) -> None:
        if t < self.source_max.get(source, -1.0):
            self.metrics["out_of_order"] += 1
        else:
            self.source_max[source] = t
        self.max_t = max(self.max_t, t)

    def ingest(self, topic: str, ev: dict) -> bool:
        """Range l'événement dans sa fenêtre. Retourne False s'il est trop en retard."""
        t = float(ev["t"])
        self.metrics["ingested"] += 1
        if topic.startswith("edge.fixedcam"):
            self._track_source(ev["cam"], t)
            w = self._window(t)
            if w is None:
                return False
            buf = self.buffers.setdefault(w, WindowBuffer())
            for d in ev["detections"]:
                buf.cams[d["tag"]].append((d["x"], d["z"], d["sigma"], ev["cam"]))
                self.tracks.setdefault(d["tag"], Track(d["tag"]))
        elif topic.startswith("edge.pov"):
            self._track_source("pov:" + ev["badge"], t)
            w = self._window(t)
            if w is None:
                return False
            buf = self.buffers.setdefault(w, WindowBuffer())
            prev = buf.pov.get(ev["badge"])
            if prev is None or prev["t"] <= t:
                buf.pov[ev["badge"]] = ev
            self.tracks.setdefault(ev["badge"], Track(ev["badge"]))
        elif topic == "sim.groundtruth":
            w = int(t // self.W)
            if w > self.last_closed:
                self.buffers.setdefault(w, WindowBuffer()).gt[ev["badge"]] = ev
        return True

    @property
    def watermark(self) -> float:
        return self.max_t - self.lateness

    # ------------------------------------------------------------------ fermeture
    def poll(self) -> list[dict]:
        """Ferme toutes les fenêtres dont la fin est passée sous le watermark."""
        out = []
        wm = self.watermark
        while (self.last_closed + 2) * self.W <= wm:
            w = self.last_closed + 1
            out.append(self._close(w, self.buffers.pop(w, WindowBuffer())))
            self.last_closed = w
        return out

    def _close(self, w: int, buf: WindowBuffer) -> dict:
        t_end = (w + 1) * self.W
        self.metrics["windows"] += 1
        workers = []
        for badge, tr in sorted(self.tracks.items()):
            cams = buf.cams.get(badge, [])
            pov = buf.pov.get(badge)
            px, pz = tr.x, tr.z
            meas = []
            cam_pos = None
            if cams:
                wsum = sum(1 / s ** 2 for _, _, s, _ in cams)
                cx = sum(x / s ** 2 for x, _, s, _ in cams) / wsum
                cz = sum(z / s ** 2 for _, z, s, _ in cams) / wsum
                cam_pos = (cx, cz, math.sqrt(1 / wsum))
                meas.append(cam_pos)
            if pov is not None:
                ox, oz = pov["odom"]
                if cam_pos is not None:  # recalage du biais de dérive de l'odométrie
                    bx, bz = tr.bias
                    a = 0.25 if tr.last_fix_t > 0 else 1.0
                    tr.bias = (bx + a * ((ox - cam_pos[0]) - bx), bz + a * ((oz - cam_pos[1]) - bz))
                    tr.last_fix_t = t_end
                sigma_odom = 0.08 + 0.03 * max(0.0, t_end - tr.last_fix_t)
                meas.append((ox - tr.bias[0], oz - tr.bias[1], sigma_odom))
                tr.heading = pov["heading"]
                tr.pose_local = np.asarray(pov["pose"], dtype=float)
                tr.contacts = pov["contacts"]
                tr.elev = pov.get("elev", 0.0)
            stale = not meas
            if meas:
                wsum = sum(1 / s ** 2 for _, _, s in meas)
                mx = sum(x / s ** 2 for x, _, s in meas) / wsum
                mz = sum(z / s ** 2 for _, z, s in meas) / wsum
                if tr.x is None:
                    tr.x, tr.z = mx, mz
                else:
                    alpha = 0.55
                    tr.x += alpha * (mx - tr.x)
                    tr.z += alpha * (mz - tr.z)
                tr.last_seen_t = t_end
                for c in cams:
                    tr.seen_by[c[3]] = t_end
                if pov:
                    tr.seen_by["pov"] = t_end
            tr.sources = sum(1 for v in tr.seen_by.values() if t_end - v <= 0.6)
            if tr.x is None:
                continue
            if px is not None:
                inst = math.hypot(tr.x - px, tr.z - pz) / self.W
                tr.speed += 0.6 * (inst - tr.speed)
            action, conf = self.classifier.classify(badge, tr.x, tr.z, tr.heading, tr.speed,
                                                    tr.pose_local, tr.contacts or {})
            tr.action, tr.action_conf = action, conf
            self.roles.update(badge, action)
            role, role_conf = self.roles.role(badge)
            pose_world = None
            if tr.pose_local is not None:
                pw = to_world(tr.pose_local, tr.x, tr.z, tr.heading)
                pw[:, 1] += tr.elev
                pose_world = np.round(pw, 3).flatten().tolist()
            gt = buf.gt.get(badge)
            if gt is not None:
                self.eval["n"] += 1
                self.eval["pos_err_sum"] += math.hypot(gt["x"] - tr.x, gt["z"] - tr.z)
                self.eval["action_ok"] += int(gt["action"] == action)
                self.eval["confusion"][(gt["action"], action)] += 1
            workers.append({
                "badge": badge, "x": round(tr.x, 3), "z": round(tr.z, 3),
                "heading": round(tr.heading, 3), "speed": round(tr.speed, 2), "elev": tr.elev,
                "action": action, "conf": conf, "role": role, "role_conf": role_conf,
                "contacts": tr.contacts, "stale": stale, "sources": tr.sources if not stale else 0,
                "pose": pose_world,
            })
        return {
            "window": w, "t": round(t_end, 3),
            "ts": (settings.site_start + timedelta(seconds=t_end)).isoformat(),
            "watermark": round(self.watermark, 3), "workers": workers,
        }

    def evaluation(self) -> dict:
        n = max(1, self.eval["n"])
        return {"samples": self.eval["n"], "action_accuracy": round(self.eval["action_ok"] / n, 3),
                "position_mae_m": round(self.eval["pos_err_sum"] / n, 3)}
