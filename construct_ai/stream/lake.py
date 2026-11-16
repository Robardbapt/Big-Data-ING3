"""Écriture du data lake en architecture médaillon (Parquet partitionné).

    lake/
      bronze/<topic>/date=YYYY-MM-DD/hour=HH/part-*.parquet   données brutes (JSON)
      silver/tracks/date=.../hour=.../part-*.parquet          trajectoires + poses fusionnées
      silver/events/date=.../hour=.../part-*.parquet          événements métier
      gold/*.parquet                                          agrégats (job batch)

Le partitionnement date/heure permet le *partition pruning* lors des requêtes
(replay d'un créneau précis, analyse d'une journée...).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq

from ..config import settings

log = logging.getLogger("lake")

TRACK_SCHEMA = pa.schema([
    ("t", pa.float64()), ("ts", pa.string()), ("window", pa.int64()), ("badge", pa.string()),
    ("x", pa.float32()), ("z", pa.float32()), ("heading", pa.float32()), ("speed", pa.float32()),
    ("elev", pa.float32()), ("action", pa.string()), ("conf", pa.float32()),
    ("role", pa.string()), ("contact_l", pa.string()), ("contact_r", pa.string()),
    ("stale", pa.bool_()), ("sources", pa.int16()), ("pose", pa.list_(pa.float32())),
])
EVENT_SCHEMA = pa.schema([
    ("t", pa.float64()), ("ts", pa.string()), ("type", pa.string()), ("badge", pa.string()),
    ("slot", pa.string()), ("wall", pa.string()), ("row", pa.int16()), ("payload", pa.string()),
])
BRONZE_SCHEMA = pa.schema([
    ("t", pa.float64()), ("ts", pa.string()), ("key", pa.string()), ("payload", pa.string()),
])


def _partition(ts: str) -> str:
    return f"date={ts[:10]}/hour={ts[11:13]}"


class LakeWriter:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root or settings.lake_dir)
        self.root.mkdir(parents=True, exist_ok=True)
        self.buffers: dict[tuple[str, str], list[dict]] = defaultdict(list)
        self.last_flush = time.monotonic()
        self.files_written = 0
        self.rows_written = 0
        self.bytes_written = 0

    # ---------------------------------------------------------------- append
    def bronze(self, topic: str, ev: dict, key: str = "") -> None:
        ts = ev.get("ts", "1970-01-01T00:00")
        self.buffers[(f"bronze/{topic}", _partition(ts))].append({
            "t": float(ev.get("t", 0.0)), "ts": ts, "key": key,
            "payload": json.dumps(ev, separators=(",", ":"), ensure_ascii=False)})

    def track_rows(self, state: dict) -> None:
        part = _partition(state["ts"])
        rows = self.buffers[("silver/tracks", part)]
        for w in state["workers"]:
            c = w.get("contacts") or {}
            rows.append({
                "t": state["t"], "ts": state["ts"], "window": state["window"], "badge": w["badge"],
                "x": w["x"], "z": w["z"], "heading": w["heading"], "speed": w["speed"],
                "elev": w["elev"], "action": w["action"], "conf": w["conf"], "role": w["role"],
                "contact_l": c.get("l"), "contact_r": c.get("r"), "stale": w["stale"],
                "sources": w["sources"], "pose": w["pose"],
            })

    def event_row(self, ev: dict) -> None:
        ts = ev.get("ts", "1970-01-01T00:00")
        self.buffers[("silver/events", _partition(ts))].append({
            "t": float(ev["t"]), "ts": ts, "type": ev["type"], "badge": ev.get("badge"),
            "slot": ev.get("slot"), "wall": ev.get("wall"), "row": ev.get("row"),
            "payload": json.dumps(ev, separators=(",", ":"), ensure_ascii=False)})

    # ---------------------------------------------------------------- flush
    def pending_rows(self) -> int:
        return sum(len(v) for v in self.buffers.values())

    def should_flush(self) -> bool:
        return (self.pending_rows() >= settings.lake_flush_rows
                or time.monotonic() - self.last_flush >= settings.lake_flush_seconds)

    def _write(self, batches: dict) -> None:
        for (dataset, part), rows in batches.items():
            if not rows:
                continue
            schema = (TRACK_SCHEMA if dataset == "silver/tracks"
                      else EVENT_SCHEMA if dataset == "silver/events" else BRONZE_SCHEMA)
            table = pa.Table.from_pylist(rows, schema=schema)
            out = self.root / dataset / part
            out.mkdir(parents=True, exist_ok=True)
            path = out / f"part-{int(time.time() * 1000)}-{uuid.uuid4().hex[:6]}.parquet"
            tmp = path.with_suffix(".tmp")
            pq.write_table(table, tmp, compression="zstd")
            tmp.rename(path)  # écriture atomique : un lecteur ne voit jamais de fichier partiel
            self.files_written += 1
            self.rows_written += len(rows)
            self.bytes_written += path.stat().st_size

    async def flush(self) -> None:
        batches, self.buffers = self.buffers, defaultdict(list)
        self.last_flush = time.monotonic()
        if batches:
            await asyncio.to_thread(self._write, batches)

    def flush_sync(self) -> None:
        batches, self.buffers = self.buffers, defaultdict(list)
        self._write(batches)

    def stats(self) -> dict:
        return {"files": self.files_written, "rows": self.rows_written,
                "mb": round(self.bytes_written / 1e6, 2), "pending": self.pending_rows()}
