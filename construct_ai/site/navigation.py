"""Navigation autour de la maison (graphe de visibilité + Dijkstra)."""
from __future__ import annotations

import heapq
import math
from functools import lru_cache

from .plan import build_plan

Point = tuple[float, float]


def _ccw(a: Point, b: Point, c: Point) -> float:
    return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])


def segments_intersect(p1: Point, p2: Point, q1: Point, q2: Point) -> bool:
    d1, d2 = _ccw(q1, q2, p1), _ccw(q1, q2, p2)
    d3, d4 = _ccw(p1, p2, q1), _ccw(p1, p2, q2)
    return (d1 * d2 < 0) and (d3 * d4 < 0)


@lru_cache(maxsize=1)
def obstacles() -> list[tuple[Point, Point]]:
    """Murs (avec ouverture de porte au sud), légèrement épaissis."""
    x0, z0, x1, z1 = build_plan().house
    m = 0.35
    return [
        ((x0 - m, z0 - m), (-0.55, z0 - m)), ((0.55, z0 - m), (x1 + m, z0 - m)),  # sud + porte
        ((x0 - m, z0 + m), (-0.55, z0 + m)), ((0.55, z0 + m), (x1 + m, z0 + m)),
        ((x1 + m, z0 - m), (x1 + m, z1 + m)), ((x1 - m, z0 + m), (x1 - m, z1 - m)),
        ((x1 + m, z1 + m), (x0 - m, z1 + m)), ((x1 - m, z1 - m), (x0 + m, z1 - m)),
        ((x0 - m, z1 + m), (x0 - m, z0 - m)), ((x0 + m, z1 - m), (x0 + m, z0 + m)),
    ]


@lru_cache(maxsize=1)
def waypoints() -> list[Point]:
    x0, z0, x1, z1 = build_plan().house
    e = 0.9
    return [
        (x0 - e, z0 - e), (x1 + e, z0 - e), (x1 + e, z1 + e), (x0 - e, z1 + e),
        (0.0, z0 - 1.0), (0.0, z0 + 0.9),  # porte extérieur / intérieur
    ]


def visible(a: Point, b: Point) -> bool:
    return not any(segments_intersect(a, b, q1, q2) for q1, q2 in obstacles())


def route(a: Point, b: Point) -> list[Point]:
    """Plus court chemin de a vers b évitant les murs. Retourne les points intermédiaires + b."""
    if visible(a, b):
        return [b]
    nodes = [a, b] + waypoints()
    n = len(nodes)
    dist = [math.inf] * n
    prev = [-1] * n
    dist[0] = 0.0
    heap = [(0.0, 0)]
    while heap:
        d, i = heapq.heappop(heap)
        if d > dist[i]:
            continue
        if i == 1:
            break
        for j in range(n):
            if j == i or not visible(nodes[i], nodes[j]):
                continue
            nd = d + math.dist(nodes[i], nodes[j])
            if nd < dist[j]:
                dist[j], prev[j] = nd, i
                heapq.heappush(heap, (nd, j))
    if prev[1] == -1:
        return [b]
    path, i = [], 1
    while i != 0:
        path.append(nodes[i])
        i = prev[i]
    return path[::-1]
