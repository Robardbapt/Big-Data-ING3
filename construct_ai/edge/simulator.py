"""Simulateur de chantier : ouvriers, comportements, poses 3D (vérité terrain).

Chaque ouvrier est piloté par un *générateur* Python (une coroutine de
comportement) qui avance d'un pas de temps à chaque tick. Le simulateur ne
publie rien lui-même : ce sont les capteurs (``sensors.py``) qui l'observent,
avec bruit, occlusions et latence, exactement comme sur un vrai chantier.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable, Generator, Optional

import numpy as np

from ..config import settings
from ..site.navigation import route
from ..site.plan import BLOCK_H, Slot, build_plan
from ..site.skeleton import body_pose, to_body, to_world

Behaviour = Generator[None, None, None]

ROLE_LABELS = {"macon": "Maçon", "manoeuvre": "Manœuvre", "chef": "Chef de chantier"}


@dataclass
class Worker:
    id: str  # identifiant pseudonymisé (RGPD)
    badge: str  # badge casque lu par les caméras
    role: str
    x: float
    z: float
    heading: float = 0.0
    elevation: float = 0.0  # sur tréteau / échafaudage
    action: str = "attente"
    phase: float = 0.0
    walk: float = 0.0
    bend: float = 0.0
    squat: float = 0.0
    l_hand: Optional[tuple] = None
    r_hand: Optional[tuple] = None
    contacts: dict = field(default_factory=lambda: {"l": None, "r": None})
    carrying: Optional[str] = None
    load: int = 0
    # spécifique maçon
    stock: int = 4
    mortar: float = 10.0
    stock_point: tuple[float, float] = (0.0, 0.0)
    wall_normal: tuple[float, float] = (0.0, -1.0)
    done: bool = False
    behaviour: Optional[Behaviour] = None

    def pose_world(self) -> np.ndarray:
        local = body_pose(self.phase, self.walk, self.bend, self.squat, self.l_hand, self.r_hand)
        world = to_world(local, self.x, self.z, self.heading)
        world[:, 1] += self.elevation
        return world

    def pose_local(self) -> np.ndarray:
        return body_pose(self.phase, self.walk, self.bend, self.squat, self.l_hand, self.r_hand)


def _ease(u: float) -> float:
    return u * u * (3 - 2 * u)


def _lerp(a, b, u):
    return tuple(ai + (bi - ai) * u for ai, bi in zip(a, b))


class SiteSimulator:
    def __init__(self, seed: int | None = None, n_masons: int | None = None) -> None:
        self.rng = random.Random(seed if seed is not None else settings.seed)
        self.plan = build_plan()
        self.t = 0.0
        self.dt = settings.tick
        self.built: dict[str, dict] = {}  # slot_id -> {t, worker}
        self.events: list[dict] = []  # interactions vues par les POV (à publier)
        self.wall_blocks = {w.name: 0 for w in self.plan.walls}
        self.wall_total = {w.name: sum(1 for s in self.plan.slots if s.wall == w.name) for w in self.plan.walls}
        self.finished_at: float | None = None
        self.workers: list[Worker] = []
        self._create_workers(n_masons or settings.n_masons)

    # ------------------------------------------------------------------ setup
    def _create_workers(self, n_masons: int) -> None:
        wall_groups = {1: [["S", "E", "N", "W"]], 2: [["S", "E"], ["N", "W"]],
                       3: [["S"], ["E", "N"], ["W"]], 4: [["S"], ["E"], ["N"], ["W"]]}[max(1, min(4, n_masons))]
        badge = 1
        masons = []
        for i, walls in enumerate(wall_groups):
            w = Worker(f"W{badge:02d}", f"H-{100 + badge * 7}", "macon", -2.0 + 2 * i, -6.0)
            badge += 1
            masons.append((w, walls))
            self.workers.append(w)
        for i, (mason, _) in enumerate(masons):
            lab = Worker(f"W{badge:02d}", f"H-{100 + badge * 7}", "manoeuvre", -8.0 + 3 * i, -8.0)
            badge += 1
            lab.behaviour = self._labourer(lab, mason)
            self.workers.append(lab)
        for mason, walls in masons:
            mason.behaviour = self._mason(mason, walls)
        chef = Worker(f"W{badge:02d}", f"H-{100 + badge * 7}", "chef", 11.0, 7.0)
        chef.behaviour = self._chef(chef)
        self.workers.append(chef)

    # ------------------------------------------------------------- primitives
    def _turn_towards(self, w: Worker, target_heading: float, rate: float = 5.0) -> None:
        diff = (target_heading - w.heading + math.pi) % (2 * math.pi) - math.pi
        step = rate * self.dt
        w.heading += max(-step, min(step, diff))

    def _reset_pose(self, w: Worker) -> None:
        w.walk, w.bend, w.squat = 0.0, 0.0, 0.0
        w.l_hand = w.r_hand = None

    def walk_to(self, w: Worker, x: float, z: float, action: str = "marche",
                speed: float = 1.25, hands: Callable[[Worker], None] | None = None) -> Behaviour:
        w.elevation = 0.0
        for wx, wz in route((w.x, w.z), (x, z)):
            while True:
                dx, dz = wx - w.x, wz - w.z
                dist = math.hypot(dx, dz)
                if dist < 0.05:
                    break
                w.action = action
                self._turn_towards(w, math.atan2(dx, dz), rate=6.0)
                step = min(dist, speed * self.dt)
                w.x += dx / dist * step
                w.z += dz / dist * step
                w.walk = min(1.0, speed / 1.2)
                w.phase = (w.phase + step * 2 * math.pi / 1.3) % (2 * math.pi)
                w.bend, w.squat = 0.08, 0.0
                w.l_hand = w.r_hand = None
                if hands:
                    hands(w)
                yield
        w.walk = 0.0

    def face(self, w: Worker, heading: float) -> Behaviour:
        for _ in range(20):
            diff = abs((heading - w.heading + math.pi) % (2 * math.pi) - math.pi)
            if diff < 0.05:
                break
            self._turn_towards(w, heading, rate=4.0)
            yield

    def act(self, w: Worker, action: str, duration: float, pose: Callable[[Worker, float], None]) -> Behaviour:
        n = max(1, int(duration / self.dt))
        for i in range(n):
            w.action = action
            w.walk = 0.0
            pose(w, (i + 1) / n)
            yield

    def emit(self, w: Worker, kind: str, **data) -> None:
        self.events.append({"badge": w.badge, "t": round(self.t, 3), "type": kind, **data})

    # ------------------------------------------------------------- poses
    @staticmethod
    def _bend_for_height(h: float) -> tuple[float, float]:
        """Flexion du tronc et des genoux pour atteindre une hauteur h (relative aux pieds)."""
        bend = max(0.0, min(0.9, (1.15 - h) / 0.95))
        squat = max(0.0, min(0.8, (0.55 - h) / 0.5))
        return bend, squat

    def _barrow_hands(self, w: Worker) -> None:
        w.l_hand, w.r_hand = (0.27, 0.72, 0.45), (-0.27, 0.72, 0.45)
        w.bend = 0.18
        w.contacts = {"l": "brouette", "r": "brouette"}
        w.carrying = "brouette"

    def _bucket_hands(self, w: Worker) -> None:
        w.r_hand = (-0.30, 0.45, 0.05)
        w.contacts = {"l": None, "r": "seau"}
        w.carrying = "seau"

    # ------------------------------------------------------------- behaviours
    def _mason_slots(self, walls: list[str]) -> list[Slot]:
        order = {name: i for i, name in enumerate(walls)}
        slots = [s for s in self.plan.slots if s.wall in order]
        return sorted(slots, key=lambda s: (s.row, order[s.wall], s.index))

    def _mason(self, w: Worker, walls: list[str]) -> Behaviour:
        walls_by_name = {wl.name: wl for wl in self.plan.walls}
        for slot in self._mason_slots(walls):
            wall = walls_by_name[slot.wall]
            nx, nz = wall.outward
            w.wall_normal = (nx, nz)
            sx, sz = slot.x + nx * 0.55, slot.z + nz * 0.55
            if math.hypot(sx - w.x, sz - w.z) > 0.25:
                yield from self.walk_to(w, sx, sz)
            w.elevation = 0.6 if slot.row >= 6 else 0.0
            w.stock_point = (sx + nx * 0.9, sz + nz * 0.9)
            wall_heading = math.atan2(-nx, -nz)
            back_heading = math.atan2(nx, nz)

            # 1) attendre le stock de parpaings (approvisionné par le manœuvre)
            while w.stock <= 0 or w.mortar <= 0:
                yield from self.act(w, "attente", 1.0, self._idle_pose)

            # 2) se retourner et prendre un parpaing sur le stock (au sol)
            yield from self.face(w, back_heading)
            w.contacts = {"l": None, "r": None}

            def pick(wk: Worker, u: float) -> None:
                down = math.sin(min(u, 0.999) * math.pi)
                wk.bend = 0.15 + 0.65 * down
                wk.squat = 0.35 * down
                low = (0.22, 0.30, 0.55)
                chest = (0.20, 1.02, 0.32)
                l = _lerp(chest, low, down) if u > 0.5 else _lerp((0.22, 0.75, 0.3), low, down)
                wk.l_hand = l
                wk.r_hand = (-l[0], l[1], l[2])
                if u > 0.5:
                    wk.contacts = {"l": "parpaing", "r": "parpaing"}
            yield from self.act(w, "prise_parpaing", 2.4 + self.rng.uniform(-0.3, 0.5), pick)
            w.stock -= 1
            self.emit(w, "block_pick", stock_left=w.stock)

            # 3) face au mur : étaler le mortier à la truelle
            yield from self.face(w, wall_heading)
            top = slot.y - BLOCK_H / 2 - w.elevation
            target = to_body((slot.x, top + 0.02, slot.z), w.x, w.z, w.heading)
            bend, squat = self._bend_for_height(top)

            def mortar(wk: Worker, u: float) -> None:
                wk.bend, wk.squat = bend, squat
                wk.contacts = {"l": None, "r": "truelle"}
                sweep = 0.22 * math.sin(u * 4 * math.pi)
                wk.r_hand = (target[0] + sweep, target[1] + 0.05, target[2] - 0.05)
                wk.l_hand = (0.28, 0.85, 0.15)
            yield from self.act(w, "mortier", 3.0 + self.rng.uniform(-0.4, 0.8), mortar)
            w.mortar -= 1

            # 4) poser le parpaing
            center = to_body((slot.x, slot.y - w.elevation, slot.z), w.x, w.z, w.heading)
            b2, s2 = self._bend_for_height(center[1])

            def place(wk: Worker, u: float) -> None:
                e = _ease(u)
                wk.bend, wk.squat = b2 * e + 0.1 * (1 - e), s2 * e
                wk.contacts = {"l": "parpaing", "r": "parpaing"}
                l0, r0 = (0.20, 1.02, 0.32), (-0.20, 1.02, 0.32)
                l1 = (center[0] + 0.21, center[1] + 0.02, center[2])
                r1 = (center[0] - 0.21, center[1] + 0.02, center[2])
                wk.l_hand, wk.r_hand = _lerp(l0, l1, e), _lerp(r0, r1, e)
            yield from self.act(w, "pose_parpaing", 2.4 + self.rng.uniform(-0.3, 0.6), place)
            self.built[slot.id] = {"t": self.t, "worker": w.id}
            self.wall_blocks[slot.wall] += 1
            noise = lambda s: self.rng.gauss(0, s)
            self.emit(w, "block_release", est=[round(slot.x + noise(0.06), 3),
                                               round(slot.y + noise(0.03), 3),
                                               round(slot.z + noise(0.06), 3)])

            # 5) régler le niveau (petits coups de manche de truelle)
            def level(wk: Worker, u: float) -> None:
                wk.contacts = {"l": None, "r": "truelle"}
                tap = 0.06 * abs(math.sin(u * 6 * math.pi))
                wk.r_hand = (center[0], center[1] + 0.14 + tap, center[2])
                wk.l_hand = (center[0] + 0.25, center[1] + 0.12, center[2] - 0.05)
            yield from self.act(w, "reglage", 1.4 + self.rng.uniform(0, 0.6), level)
            w.contacts = {"l": None, "r": None}
            self._reset_pose(w)

        w.done = True
        self.emit(w, "segment_done")
        yield from self.walk_to(w, self.plan.cabin[0] - 2 + self.rng.random(), self.plan.cabin[1] - 2)
        while True:
            yield from self.act(w, "attente", 2.0, self._idle_pose)

    def _idle_pose(self, w: Worker, u: float) -> None:
        w.bend, w.squat = 0.05, 0.0
        w.l_hand = (0.22, 0.95 + 0.02 * math.sin(u * math.pi), 0.12)
        w.r_hand = (-0.22, 0.95, 0.12)
        w.contacts = {"l": None, "r": None}

    def _labourer(self, w: Worker, mason: Worker) -> Behaviour:
        pallet = min(self.plan.pallets.values(), key=lambda p: math.dist(p, (w.x, w.z)))
        while not mason.done:
            if mason.mortar <= 3:
                # aller gâcher du mortier à la bétonnière puis l'apporter au maçon
                mx, mz = self.plan.mixer
                w.carrying = None
                yield from self.walk_to(w, mx + 1.1, mz)
                yield from self.face(w, math.atan2(-1, 0))

                def mix(wk: Worker, u: float) -> None:
                    wk.contacts = {"l": "pelle", "r": "pelle"}
                    a = u * 6 * math.pi
                    wk.bend = 0.35 + 0.15 * math.sin(a)
                    wk.l_hand = (0.12 + 0.08 * math.cos(a), 0.75 + 0.1 * math.sin(a), 0.55)
                    wk.r_hand = (-0.10 + 0.08 * math.cos(a), 0.45 + 0.1 * math.sin(a), 0.65)
                yield from self.act(w, "gachage", 6.0 + self.rng.uniform(0, 2), mix)
                self.emit(w, "mortar_mixed")
                sx, sz = mason.stock_point
                nx, nz = mason.wall_normal
                yield from self.walk_to(w, sx + nx * 0.6, sz + nz * 0.6, "transport_seau", 1.1, self._bucket_hands)
                yield from self.face(w, math.atan2(-nx, -nz))

                def pour(wk: Worker, u: float) -> None:
                    wk.contacts = {"l": "seau", "r": "seau"}
                    wk.bend = 0.5 * math.sin(u * math.pi)
                    wk.l_hand = (0.15, 0.7 - 0.3 * math.sin(u * math.pi), 0.45)
                    wk.r_hand = (-0.15, 0.7 - 0.3 * math.sin(u * math.pi), 0.45)
                yield from self.act(w, "dechargement", 2.0, pour)
                mason.mortar = 10.0
                self.emit(w, "mortar_delivered", to=mason.badge)
                w.carrying, w.contacts = None, {"l": None, "r": None}
                self._reset_pose(w)
            elif mason.stock < 6:
                # chercher 4 parpaings à la palette avec la brouette
                px, pz = pallet
                yield from self.walk_to(w, px, pz + 1.0, "transport_brouette", 1.15, self._barrow_hands)
                yield from self.face(w, math.atan2(0, -1))

                def load(wk: Worker, u: float) -> None:
                    cyc = (u * 4) % 1.0
                    down = math.sin(cyc * math.pi)
                    wk.bend, wk.squat = 0.2 + 0.55 * down, 0.2 * down
                    wk.contacts = {"l": "parpaing", "r": "parpaing"}
                    wk.l_hand = (0.22, 1.0 - 0.6 * down, 0.35 + 0.2 * down)
                    wk.r_hand = (-0.22, 1.0 - 0.6 * down, 0.35 + 0.2 * down)
                yield from self.act(w, "chargement", 4 * 1.4 + self.rng.uniform(0, 1), load)
                w.load = 4
                self.emit(w, "barrow_loaded", count=4)
                sx, sz = mason.stock_point
                nx, nz = mason.wall_normal
                yield from self.walk_to(w, sx + nx * 0.6, sz + nz * 0.6, "transport_brouette", 1.0, self._barrow_hands)
                yield from self.face(w, math.atan2(-nx, -nz))

                def unload(wk: Worker, u: float) -> None:
                    cyc = (u * 4) % 1.0
                    down = math.sin(cyc * math.pi)
                    wk.bend, wk.squat = 0.25 + 0.45 * down, 0.15 * down
                    wk.contacts = {"l": "parpaing", "r": "parpaing"}
                    wk.l_hand = (0.22, 0.9 - 0.5 * down, 0.45)
                    wk.r_hand = (-0.22, 0.9 - 0.5 * down, 0.45)
                yield from self.act(w, "dechargement", 4 * 1.0 + self.rng.uniform(0, 0.8), unload)
                mason.stock += w.load
                self.emit(w, "barrow_unloaded", count=w.load, to=mason.badge)
                w.load = 0
                w.carrying, w.contacts = None, {"l": None, "r": None}
                self._reset_pose(w)
            else:
                yield from self.act(w, "attente", 2.0, self._idle_pose)
        yield from self.walk_to(w, self.plan.cabin[0] - 3 + self.rng.random(), self.plan.cabin[1] - 2.5)
        while True:
            yield from self.act(w, "attente", 2.0, self._idle_pose)

    def _chef(self, w: Worker) -> Behaviour:
        x0, z0, x1, z1 = self.plan.house
        spots = [(0.0, z0 - 2.5), (x1 + 2.5, 0.0), (0.0, z1 + 2.5), (x0 - 2.5, 0.0),
                 (0.0, 0.0), (self.plan.cabin[0] - 1.5, self.plan.cabin[1] - 1.5),
                 (self.plan.pallets["P1"][0] + 1.5, self.plan.pallets["P1"][1] + 1.5),
                 (self.plan.mixer[0] + 2, self.plan.mixer[1] - 1)]
        while True:
            x, z = self.rng.choice(spots)
            yield from self.walk_to(w, x + self.rng.uniform(-0.5, 0.5), z + self.rng.uniform(-0.5, 0.5), speed=1.05)
            yield from self.face(w, math.atan2(-w.x, -w.z) if math.hypot(w.x, w.z) > 1 else w.heading + 1.5)

            def inspect(wk: Worker, u: float) -> None:
                wk.contacts = {"l": "tablette", "r": "tablette"}
                wk.bend = 0.12
                wk.l_hand = (0.13, 1.08, 0.30)
                wk.r_hand = (-0.10, 1.12 + 0.03 * math.sin(u * 9), 0.33)
            yield from self.act(w, "inspection", self.rng.uniform(6, 14), inspect)
            self.emit(w, "inspection_done", x=round(w.x, 2), z=round(w.z, 2))
            self._reset_pose(w)
            w.contacts = {"l": None, "r": None}

    # ------------------------------------------------------------------ loop
    def step(self) -> None:
        self.t += self.dt
        for w in self.workers:
            if w.behaviour is not None:
                next(w.behaviour)
        if self.finished_at is None and len(self.built) == len(self.plan.slots):
            self.finished_at = self.t

    def wall_height(self, wall: str) -> float:
        rows = self.wall_blocks[wall] / max(1, self.wall_total[wall]) * 10
        return rows * BLOCK_H

    @property
    def progress(self) -> float:
        return len(self.built) / len(self.plan.slots)
