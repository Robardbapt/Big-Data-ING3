"""Reconnaissance d'activité et déduction du rôle de chaque ouvrier.

Classifieur à règles explicables (v1) basé sur :
* la cinématique fusionnée (vitesse, cap, distance aux murs) ;
* la pose 3D ego (flexion du tronc, position des mains) issue de la body-cam ;
* les contacts main/objet détectés en POV (parpaing, truelle, brouette...).

Les données produites (pose + label) servent ensuite à entraîner un modèle ML
(v2) — c'est exactement le dataset que l'on vend aux roboticiens.
"""
from __future__ import annotations

import math
from collections import Counter, deque

import numpy as np

from ..site.plan import build_plan
from ..site.skeleton import J

ACTIONS = {
    "marche": "Marche",
    "transport_brouette": "Transport brouette",
    "transport_seau": "Transport mortier",
    "chargement": "Chargement palette",
    "dechargement": "Déchargement",
    "gachage": "Gâchage mortier",
    "prise_parpaing": "Prise parpaing",
    "mortier": "Étalage mortier",
    "pose_parpaing": "Pose parpaing",
    "reglage": "Réglage niveau",
    "inspection": "Inspection",
    "attente": "Attente",
}

ROLE_PROFILES = {
    "macon": {"prise_parpaing", "mortier", "pose_parpaing", "reglage"},
    "manoeuvre": {"transport_brouette", "transport_seau", "chargement", "dechargement", "gachage"},
    "chef": {"inspection"},
}
ROLE_LABELS = {"macon": "Maçon", "manoeuvre": "Manœuvre", "chef": "Chef de chantier", "?": "Indéterminé"}


def trunk_bend(pose_local: np.ndarray) -> float:
    """Angle de flexion du tronc normalisé (0 = droit, 1 ≈ 75°)."""
    v = pose_local[J["neck"]] - pose_local[J["pelvis"]]
    ang = math.atan2(v[2], max(v[1], 1e-6))
    return max(0.0, ang / math.radians(75))


class ActivityClassifier:
    def __init__(self) -> None:
        plan = build_plan()
        self.walls = plan.walls
        self.pallets = list(plan.pallets.values())
        self.mixer = plan.mixer
        self.history: dict[str, deque] = {}

    def _wall_info(self, x: float, z: float) -> tuple[float, tuple[float, float]]:
        best, normal = 1e9, (0.0, 0.0)
        for w in self.walls:
            ax, az, bx, bz = w.x0, w.z0, w.x1, w.z1
            vx, vz = bx - ax, bz - az
            u = max(0.0, min(1.0, ((x - ax) * vx + (z - az) * vz) / (vx * vx + vz * vz)))
            d = math.hypot(x - (ax + u * vx), z - (az + u * vz))
            if d < best:
                best, normal = d, w.outward
        return best, normal

    def classify(self, badge: str, x: float, z: float, heading: float, speed: float,
                 pose_local: np.ndarray | None, contacts: dict) -> tuple[str, float]:
        hist = self.history.setdefault(badge, deque(maxlen=5))
        objs = {contacts.get("l"), contacts.get("r")} - {None}
        if pose_local is not None and "truelle" in objs:
            hist.append(pose_local[J["r_wrist"]].copy())
        else:
            hist.clear()
        stride = 0.0
        if pose_local is not None:
            stride = abs(pose_local[J["l_ankle"]][2] - pose_local[J["r_ankle"]][2])
        if speed > 0.5 or (speed > 0.25 and stride > 0.12):
            if "brouette" in objs:
                return "transport_brouette", 0.95
            if "seau" in objs:
                return "transport_seau", 0.9
            return "marche", 0.9
        if pose_local is None:
            return "attente", 0.3
        bend = trunk_bend(pose_local)
        if "tablette" in objs:
            return "inspection", 0.9
        if "pelle" in objs:
            return "gachage", 0.9
        wall_d, normal = self._wall_info(x, z)
        facing_wall = (math.sin(heading) * -normal[0] + math.cos(heading) * -normal[1]) > 0.3
        if "truelle" in objs:
            if len(hist) >= 3:
                lateral = float(np.std(np.array(hist)[:, 0]))
                return ("mortier", 0.85) if lateral > 0.05 else ("reglage", 0.75)
            return "mortier", 0.6
        if "seau" in objs:
            return "dechargement", 0.7
        if "parpaing" in objs or "brouette" in objs:
            if min(math.dist((x, z), p) for p in self.pallets) < 2.2:
                return "chargement", 0.85
            if wall_d < 1.0:
                return ("pose_parpaing", 0.8) if facing_wall else ("prise_parpaing", 0.75)
            return "dechargement", 0.7
        if wall_d < 1.0 and bend > 0.35:
            return "prise_parpaing", 0.55
        return "attente", 0.6


class RoleInference:
    """Le rôle n'est pas déclaré : il est déduit du profil d'activité cumulé."""

    def __init__(self) -> None:
        self.counts: dict[str, Counter] = {}

    def update(self, badge: str, action: str) -> None:
        self.counts.setdefault(badge, Counter())[action] += 1

    def role(self, badge: str) -> tuple[str, float]:
        c = self.counts.get(badge)
        if not c:
            return "?", 0.0
        scores = {role: sum(c[a] for a in acts) for role, acts in ROLE_PROFILES.items()}
        # le chef « inspecte » peu souvent : on pondère fortement ce signal rare
        scores["chef"] *= 4
        total = sum(scores.values())
        if total < 20:
            return "?", 0.0
        role = max(scores, key=scores.get)
        return role, round(scores[role] / total, 2)
