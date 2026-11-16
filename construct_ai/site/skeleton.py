"""Squelette humain 3D (15 articulations) généré de façon procédurale.

Convention : cap (heading) θ → direction avant = (sin θ, cos θ) dans le plan (x, z).
Le repère *corps* est : x = droite... en pratique on travaille avec
(latéral, haut, avant) puis on transforme vers le monde.
"""
from __future__ import annotations

import math

import numpy as np

JOINTS = [
    "head", "neck", "l_shoulder", "r_shoulder", "l_elbow", "r_elbow",
    "l_wrist", "r_wrist", "pelvis", "l_hip", "r_hip", "l_knee", "r_knee",
    "l_ankle", "r_ankle",
]
J = {name: i for i, name in enumerate(JOINTS)}
BONES = [
    ("head", "neck"), ("neck", "l_shoulder"), ("neck", "r_shoulder"),
    ("l_shoulder", "l_elbow"), ("l_elbow", "l_wrist"), ("r_shoulder", "r_elbow"),
    ("r_elbow", "r_wrist"), ("neck", "pelvis"), ("pelvis", "l_hip"), ("pelvis", "r_hip"),
    ("l_hip", "l_knee"), ("l_knee", "l_ankle"), ("r_hip", "r_knee"), ("r_knee", "r_ankle"),
]

UPPER_ARM, FOREARM = 0.30, 0.28
THIGH, SHIN = 0.45, 0.43


def _ik(root: np.ndarray, target: np.ndarray, l1: float, l2: float, bend_dir: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """IK 2 segments : retourne (articulation intermédiaire, effecteur atteignable)."""
    d = target - root
    dist = float(np.linalg.norm(d))
    max_reach = (l1 + l2) * 0.999
    if dist > max_reach:
        target = root + d / dist * max_reach
        dist = max_reach
    dist = max(dist, 1e-4)
    axis = (target - root) / dist
    a = (l1 * l1 - l2 * l2 + dist * dist) / (2 * dist)
    h = math.sqrt(max(l1 * l1 - a * a, 0.0))
    perp = bend_dir - axis * float(np.dot(bend_dir, axis))
    n = float(np.linalg.norm(perp))
    perp = perp / n if n > 1e-6 else np.array([0.0, -1.0, 0.0])
    mid = root + axis * a + perp * h
    return mid, target


def body_pose(
    phase: float = 0.0,
    walk: float = 0.0,
    bend: float = 0.0,
    squat: float = 0.0,
    l_hand: tuple[float, float, float] | None = None,
    r_hand: tuple[float, float, float] | None = None,
) -> np.ndarray:
    """Pose dans le repère corps : colonnes (latéral +gauche, haut, avant).

    phase : phase du cycle de marche (rad) ; walk : amplitude 0..1
    bend : flexion du tronc 0..1 (≈ 0..75°) ; squat : flexion des genoux 0..1
    l_hand / r_hand : cibles des mains dans le repère corps (None = bras ballants)
    """
    p = np.zeros((len(JOINTS), 3))
    pelvis_h = 0.95 - 0.30 * squat - 0.02 * walk * abs(math.cos(phase))
    pelvis = np.array([0.0, pelvis_h, -0.05 * squat])
    p[J["pelvis"]] = pelvis
    trunk_angle = bend * math.radians(75)
    trunk_dir = np.array([0.0, math.cos(trunk_angle), math.sin(trunk_angle)])
    neck = pelvis + trunk_dir * 0.52
    p[J["neck"]] = neck
    p[J["head"]] = neck + (trunk_dir * 0.22 + np.array([0, 0.02, 0.04]))
    p[J["l_shoulder"]] = neck + np.array([0.19, -0.04, 0.0])
    p[J["r_shoulder"]] = neck + np.array([-0.19, -0.04, 0.0])
    p[J["l_hip"]] = pelvis + np.array([0.11, -0.03, 0.0])
    p[J["r_hip"]] = pelvis + np.array([-0.11, -0.03, 0.0])

    # Jambes : marche (pieds alternés) ou appui fixe
    for side, sgn, ph in (("l", 1.0, phase), ("r", -1.0, phase + math.pi)):
        hip = p[J[f"{side}_hip"]]
        stride = 0.32 * walk * math.sin(ph)
        lift = 0.10 * walk * max(0.0, math.cos(ph))
        foot = np.array([hip[0] + 0.02 * sgn, 0.06 + lift, stride + 0.05 * squat])
        knee, foot = _ik(hip, foot, THIGH, SHIN, np.array([0.0, 0.0, 1.0]))
        p[J[f"{side}_knee"]] = knee
        p[J[f"{side}_ankle"]] = foot

    # Bras
    for side, sgn, target, ph in (("l", 1.0, l_hand, phase + math.pi), ("r", -1.0, r_hand, phase)):
        sh = p[J[f"{side}_shoulder"]]
        if target is None:
            swing = 0.22 * walk * math.sin(ph)
            tgt = sh + np.array([0.05 * sgn, -0.55, swing + 0.05])
        else:
            tgt = np.array(target, dtype=float)
        elbow, wrist = _ik(sh, tgt, UPPER_ARM, FOREARM, np.array([0.4 * sgn, -0.6, -0.5]))
        p[J[f"{side}_elbow"]] = elbow
        p[J[f"{side}_wrist"]] = wrist
    return p


def to_world(local: np.ndarray, x: float, z: float, heading: float) -> np.ndarray:
    """Repère corps → monde. Avant = (sin θ, cos θ), gauche = (cos θ, -sin θ)."""
    s, c = math.sin(heading), math.cos(heading)
    lat, up, fwd = local[:, 0], local[:, 1], local[:, 2]
    wx = x + fwd * s + lat * c
    wz = z + fwd * c - lat * s
    return np.stack([wx, up, wz], axis=1)


def to_body(world_pt: tuple[float, float, float], x: float, z: float, heading: float) -> tuple[float, float, float]:
    """Monde → repère corps (inverse de to_world) pour un point."""
    s, c = math.sin(heading), math.cos(heading)
    dx, dz = world_pt[0] - x, world_pt[2] - z
    fwd = dx * s + dz * c
    lat = dx * c - dz * s
    return (lat, world_pt[1], fwd)
