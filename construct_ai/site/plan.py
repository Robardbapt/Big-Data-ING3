"""Maquette BIM simplifiée du chantier : une maison en parpaings 8 m x 6 m.

Repère monde : x vers l'est, z vers le nord, y vers le haut (mètres).
Chaque emplacement de parpaing (« slot ») a un identifiant stable, ce qui permet
de rattacher chaque pose détectée par les caméras à la maquette (BIM matching).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from functools import lru_cache

BLOCK_L, BLOCK_H, BLOCK_W = 0.50, 0.20, 0.20  # parpaing standard 50x20x20
ROWS = 10  # 2 m de hauteur


@dataclass
class Slot:
    id: str
    wall: str
    row: int
    index: int
    x: float
    y: float
    z: float
    length: float
    rot: float  # 0 = mur orienté est-ouest, pi/2 = nord-sud


@dataclass
class Wall:
    name: str
    label: str
    x0: float
    z0: float
    x1: float
    z1: float
    outward: tuple[float, float]  # normale vers l'extérieur


@dataclass
class SitePlan:
    width: float = 34.0
    depth: float = 26.0
    house: tuple[float, float, float, float] = (-4.0, -3.0, 4.0, 3.0)
    walls: list[Wall] = field(default_factory=list)
    slots: list[Slot] = field(default_factory=list)
    pallets: dict[str, tuple[float, float]] = field(default_factory=dict)
    mixer: tuple[float, float] = (-10.0, 6.0)
    cabin: tuple[float, float] = (12.0, 8.5)
    gate: tuple[float, float] = (0.0, -12.5)
    cameras: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "width": self.width,
            "depth": self.depth,
            "house": self.house,
            "walls": [asdict(w) for w in self.walls],
            "slots": [asdict(s) for s in self.slots],
            "pallets": self.pallets,
            "mixer": self.mixer,
            "cabin": self.cabin,
            "gate": self.gate,
            "cameras": self.cameras,
            "block": {"l": BLOCK_L, "h": BLOCK_H, "w": BLOCK_W},
            "rows": ROWS,
        }

    def slot_by_id(self) -> dict[str, Slot]:
        return {s.id: s for s in self.slots}

    def wall(self, name: str) -> Wall:
        return next(w for w in self.walls if w.name == name)


def _openings(wall: str, row: int, center: float) -> bool:
    """Retourne True si l'emplacement tombe dans une ouverture (porte / fenêtres)."""
    if wall == "S" and row <= 8 and -0.55 < center < 0.55:  # porte
        return True
    if wall == "N" and 3 <= row <= 6 and (-2.05 < center < -0.95 or 0.95 < center < 2.05):
        return True
    if wall == "E" and 3 <= row <= 6 and -0.55 < center < 0.55:
        return True
    return False


def _tile(start: float, end: float, offset: float) -> list[tuple[float, float]]:
    """Découpe un segment en parpaings (appareillage à joints décalés)."""
    pieces: list[tuple[float, float]] = []
    cursor = start
    first = BLOCK_L - offset if offset else BLOCK_L
    length = first
    while cursor < end - 1e-6:
        piece = min(length, end - cursor)
        if piece > 0.05:
            pieces.append((cursor + piece / 2, piece))
        cursor += piece
        length = BLOCK_L
    return pieces


@lru_cache(maxsize=1)
def build_plan() -> SitePlan:
    plan = SitePlan()
    x0, z0, x1, z1 = plan.house
    t = BLOCK_W
    plan.walls = [
        Wall("S", "Mur sud (façade)", x0, z0 + t / 2, x1, z0 + t / 2, (0.0, -1.0)),
        Wall("E", "Mur est", x1 - t / 2, z0 + t, x1 - t / 2, z1 - t, (1.0, 0.0)),
        Wall("N", "Mur nord", x1, z1 - t / 2, x0, z1 - t / 2, (0.0, 1.0)),
        Wall("W", "Mur ouest", x0 + t / 2, z1 - t, x0 + t / 2, z0 + t, (-1.0, 0.0)),
    ]
    for row in range(ROWS):
        y = row * BLOCK_H + BLOCK_H / 2
        offset = BLOCK_L / 2 if row % 2 else 0.0
        for wall in plan.walls:
            horizontal = abs(wall.z1 - wall.z0) < 1e-6
            a, b = (wall.x0, wall.x1) if horizontal else (wall.z0, wall.z1)
            lo, hi = min(a, b), max(a, b)
            pieces = _tile(lo, hi, offset)
            if (b < a):  # sens de pose : suit le tour de la maison
                pieces = pieces[::-1]
            idx = 0
            for center, length in pieces:
                if _openings(wall.name, row, center):
                    continue
                if horizontal:
                    x, z, rot = center, wall.z0, 0.0
                else:
                    x, z, rot = wall.x0, center, math.pi / 2
                plan.slots.append(
                    Slot(f"{wall.name}-R{row}-{idx:02d}", wall.name, row, idx,
                         round(x, 3), round(y, 3), round(z, 3), round(length, 3), rot)
                )
                idx += 1

    plan.pallets = {"P1": (-7.5, -9.0), "P2": (7.5, -9.0)}
    # Caméras fixes sur mâts aux 4 coins du chantier (hauteur 6 m)
    for cam_id, (cx, cz) in {
        "CAM-NO": (-15.0, 11.0), "CAM-NE": (15.0, 11.0),
        "CAM-SE": (15.0, -11.0), "CAM-SO": (-15.0, -11.0),
    }.items():
        yaw = math.atan2(-cx, -cz)  # regarde vers le centre
        plan.cameras.append({"id": cam_id, "x": cx, "y": 6.0, "z": cz, "yaw": yaw,
                             "fov": math.radians(85), "range": 28.0})
    return plan
