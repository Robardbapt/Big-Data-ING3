"""Avancement du chantier : rattachement des poses de parpaings à la maquette BIM."""
from __future__ import annotations

import math
from collections import deque

import numpy as np

from ..site.plan import build_plan


class ProgressTracker:
    def __init__(self) -> None:
        self.plan = build_plan()
        self.slots = self.plan.slots
        self.coords = np.array([[s.x, s.y, s.z] for s in self.slots])
        self.built: dict[str, dict] = {}
        self.built_mask = np.zeros(len(self.slots), dtype=bool)
        self.history: deque = deque(maxlen=400)  # (t, nb posés) pour l'ETA
        self.by_wall_total = {w.name: 0 for w in self.plan.walls}
        for s in self.slots:
            self.by_wall_total[s.wall] += 1
        self.by_wall = {w.name: 0 for w in self.plan.walls}
        self.anomalies = 0
        # appuis physiques : blocs du rang inférieur qui portent chaque emplacement
        self.supports: list[list[int]] = []
        for s in self.slots:
            sup = []
            if s.row > 0:
                for j, o in enumerate(self.slots):
                    if o.wall == s.wall and o.row == s.row - 1:
                        along = abs((s.x - o.x) if s.rot == 0 else (s.z - o.z))
                        if along < (s.length + o.length) / 2 - 0.02:
                            sup.append(j)
            self.supports.append(sup)

    def match(self, est: list[float]) -> int | None:
        """Emplacement BIM libre le plus plausible.

        Coût = distance (la hauteur compte triple : les rangs sont espacés de 20 cm)
        + pénalité si les blocs du dessous ne sont pas encore posés (on ne pose pas dans le vide).
        """
        d = self.coords - np.asarray(est)
        cost = np.sqrt(d[:, 0] ** 2 + (3 * d[:, 1]) ** 2 + d[:, 2] ** 2)
        cost[self.built_mask] = np.inf
        for i in np.argsort(cost)[:6]:
            missing = sum(1 for j in self.supports[i] if not self.built_mask[j])
            cost[i] += 0.35 * missing
        i = int(np.argmin(cost))
        return i if cost[i] < 0.6 else None

    def on_release(self, ev: dict) -> dict:
        i = self.match(ev["est"])
        if i is None:
            self.anomalies += 1
            return {"type": "anomalie_pose", "t": ev["t"], "badge": ev["badge"], "est": ev["est"],
                    "message": "Parpaing posé hors maquette BIM"}
        slot = self.slots[i]
        self.built_mask[i] = True
        self.built[slot.id] = {"t": ev["t"], "badge": ev["badge"]}
        self.by_wall[slot.wall] += 1
        self.history.append((ev["t"], len(self.built)))
        return {"type": "block_placed", "t": ev["t"], "badge": ev["badge"], "slot": slot.id,
                "wall": slot.wall, "row": slot.row, "progress": round(self.progress, 4),
                "error_cm": round(100 * math.dist(ev["est"], (slot.x, slot.y, slot.z)), 1)}

    @property
    def progress(self) -> float:
        return len(self.built) / len(self.slots)

    def summary(self, now_t: float) -> dict:
        rate = 0.0
        eta = None
        if len(self.history) >= 5:
            t0, n0 = self.history[0]
            t1, n1 = self.history[-1]
            if t1 > t0:
                rate = (n1 - n0) / (t1 - t0)  # blocs / seconde
        remaining = len(self.slots) - len(self.built)
        if rate > 0 and remaining:
            eta = round(now_t + remaining / rate, 1)
        return {
            "built": len(self.built), "total": len(self.slots), "progress": round(self.progress, 4),
            "blocks_per_hour": round(rate * 3600, 1), "eta_t": eta, "anomalies": self.anomalies,
            "walls": {k: {"built": self.by_wall[k], "total": v} for k, v in self.by_wall_total.items()},
        }
