"""Couche GOLD : agrégats analytiques calculés avec DuckDB directement sur le lake Parquet.

Utilisable :
* à la volée par l'API (tableau de bord) ;
* en job batch (``python -m construct_ai gold``) qui matérialise les tables
  gold en Parquet et exporte le dataset robotique.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from pathlib import Path

import duckdb

from ..config import settings

log = logging.getLogger("gold")


def _glob(dataset: str, root: Path | None = None) -> str:
    return str(Path(root or settings.lake_dir) / dataset / "**" / "*.parquet")


def has_data(dataset: str, root: Path | None = None) -> bool:
    base = Path(root or settings.lake_dir) / dataset
    return base.exists() and any(base.rglob("*.parquet"))


def connect(root: Path | None = None) -> duckdb.DuckDBPyConnection:
    """Connexion DuckDB avec des vues sur les tables silver (lecture Parquet + partitions Hive)."""
    con = duckdb.connect()
    for name, dataset in (("tracks", "silver/tracks"), ("events", "silver/events")):
        if has_data(dataset, root):
            con.execute(f"CREATE VIEW {name} AS SELECT * FROM read_parquet('{_glob(dataset, root)}', "
                        f"hive_partitioning = true, union_by_name = true)")
    return con


QUERIES = {
    "time_by_action": """
        SELECT badge, action, round(count(*) * {W}, 1) AS seconds
        FROM tracks GROUP BY ALL ORDER BY badge, seconds DESC""",
    "workers": """
        WITH d AS (
            SELECT badge, t, x, z, action, role,
                   sqrt(power(x - lag(x) OVER w, 2) + power(z - lag(z) OVER w, 2)) AS step
            FROM tracks WINDOW w AS (PARTITION BY badge ORDER BY t))
        SELECT badge,
               arg_max(role, t) AS role,
               round(sum(CASE WHEN step < 1.0 THEN step ELSE 0 END), 1) AS distance_m,
               round(count(*) * {W} / 60, 1) AS minutes_observed,
               round(100.0 * avg(CASE WHEN action = 'attente' THEN 1 ELSE 0 END), 1) AS idle_pct
        FROM d GROUP BY badge ORDER BY badge""",
    "blocks_by_worker": """
        SELECT badge, count(*) AS blocks FROM events WHERE type = 'block_placed' GROUP BY badge ORDER BY badge""",
    "progress_curve": """
        WITH b AS (SELECT floor(t / 60) * 60 AS bucket, count(*) AS blocks
                   FROM events WHERE type = 'block_placed' GROUP BY 1)
        SELECT bucket AS t, blocks, sum(blocks) OVER (ORDER BY bucket) AS cumulative FROM b ORDER BY bucket""",
    "blocks_by_wall_row": """
        SELECT wall, "row", count(*) AS blocks, round(min(t), 1) AS first_t, round(max(t), 1) AS last_t
        FROM events WHERE type = 'block_placed' GROUP BY ALL ORDER BY "row", wall""",
    "heatmap": """
        SELECT floor(x)::INT AS gx, floor(z)::INT AS gz, count(*) AS n
        FROM tracks WHERE NOT stale GROUP BY ALL""",
    "alerts": """
        SELECT badge, type, count(*) AS n FROM events
        WHERE type IN ('posture_risque', 'anomalie_pose') GROUP BY ALL""",
    "cycle_time": """
        WITH b AS (SELECT badge, t, t - lag(t) OVER (PARTITION BY badge ORDER BY t) AS dt
                   FROM events WHERE type = 'block_placed')
        SELECT badge, round(median(dt), 1) AS median_cycle_s, round(avg(dt), 1) AS mean_cycle_s
        FROM b WHERE dt IS NOT NULL AND dt < 120 GROUP BY badge ORDER BY badge""",
}


def run_query(con, name: str) -> list[dict]:
    sql = QUERIES[name].format(W=settings.window)
    try:
        cur = con.execute(sql)
    except duckdb.CatalogException:  # vue absente (lake encore vide)
        return []
    cols = [c[0] for c in cur.description]
    return [{c: (float(v) if isinstance(v, Decimal) else v) for c, v in zip(cols, row)} for row in cur.fetchall()]


def analytics(root: Path | None = None) -> dict:
    con = connect(root)
    try:
        return {**{name: run_query(con, name) for name in QUERIES}, "bucket_s": 60}
    finally:
        con.close()


def materialize(root: Path | None = None) -> dict[str, int]:
    """Job batch : écrit chaque agrégat en table gold Parquet."""
    root = Path(root or settings.lake_dir)
    out_dir = root / "gold"
    out_dir.mkdir(parents=True, exist_ok=True)
    con = connect(root)
    counts = {}
    try:
        for name in QUERIES:
            sql = QUERIES[name].format(W=settings.window)
            try:
                con.execute(f"COPY ({sql}) TO '{out_dir / (name + '.parquet')}' (FORMAT parquet)")
                counts[name] = con.execute(f"SELECT count(*) FROM read_parquet('{out_dir / (name + '.parquet')}')").fetchone()[0]
            except duckdb.CatalogException:
                counts[name] = 0
    finally:
        con.close()
    return counts


def replay(t0: float, t1: float, root: Path | None = None, max_frames: int = 2000) -> list[dict]:
    """Reconstitue les états du jumeau numérique entre t0 et t1 depuis la couche silver."""
    if not has_data("silver/tracks", root):
        return []
    con = connect(root)
    try:
        rows = con.execute(
            "SELECT \"window\", t, badge, x, z, heading, elev, action, role, contact_l, contact_r, stale, pose "
            "FROM tracks WHERE t >= ? AND t <= ? ORDER BY t, badge LIMIT ?",
            [t0, t1, max_frames * 10]).fetchall()
    finally:
        con.close()
    frames: dict[int, dict] = {}
    for (w, t, badge, x, z, heading, elev, action, role, cl, cr, stale, pose) in rows:
        fr = frames.setdefault(w, {"window": w, "t": round(t, 3), "workers": []})
        fr["workers"].append({
            "badge": badge, "x": round(x, 3), "z": round(z, 3), "heading": round(heading, 3),
            "elev": elev, "action": action, "role": role, "contacts": {"l": cl, "r": cr},
            "stale": stale, "pose": [round(v, 3) for v in pose] if pose is not None else None})
    return list(frames.values())[:max_frames]


def blocks_until(t: float, root: Path | None = None) -> list[dict]:
    if not has_data("silver/events", root):
        return []
    con = connect(root)
    try:
        rows = con.execute("SELECT slot, t, badge FROM events WHERE type = 'block_placed' AND t <= ? ORDER BY t",
                           [t]).fetchall()
    finally:
        con.close()
    return [{"slot": s, "t": tt, "badge": b} for s, tt, b in rows]
