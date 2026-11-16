"""Export du dataset « robotique » : épisodes de démonstration humaine.

Format inspiré de LeRobot (Hugging Face) v2, standard de fait pour
l'apprentissage par imitation :

    meta/info.json        fps, schéma des features, repères, articulations
    meta/tasks.jsonl      descriptions en langage naturel des tâches
    meta/episodes.jsonl   un épisode = une action continue d'un ouvrier
    meta/stats.json       moyennes / écarts-types pour la normalisation
    data/chunk-000/episode_000000.parquet
    README.md             dataset card (provenance, anonymisation, licence)

Pour chaque frame : ``observation.state`` (pose 15 articulations x 3 dans le
repère du corps), ``observation.base`` (x, z, cap, élévation sur le chantier),
``observation.target`` (cible BIM : position du parpaing à poser) et
``action`` (pose à la frame suivante : ce que le robot doit commander).
"""
from __future__ import annotations

import io
import json
import math
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pyarrow as pa
import pyarrow.parquet as pq

from ..config import settings
from ..site.plan import build_plan
from ..site.skeleton import JOINTS
from .gold import connect, has_data

DEFAULT_TASKS = ["pose_parpaing", "prise_parpaing", "mortier", "reglage", "chargement",
                 "dechargement", "transport_brouette", "gachage"]

TASK_TEXT = {
    "pose_parpaing": "Poser un parpaing à son emplacement sur le lit de mortier",
    "prise_parpaing": "Saisir un parpaing sur le stock au sol et le ramener contre soi",
    "mortier": "Étaler le mortier à la truelle sur le rang inférieur",
    "reglage": "Régler le niveau du parpaing posé en tapotant avec la truelle",
    "chargement": "Charger des parpaings depuis la palette dans la brouette",
    "dechargement": "Décharger des parpaings au pied du maçon",
    "transport_brouette": "Transporter une brouette chargée sur le chantier",
    "gachage": "Gâcher du mortier à la pelle près de la bétonnière",
}


def _to_body(pose_world: np.ndarray, x: float, z: float, heading: float, elev: float) -> np.ndarray:
    p = pose_world.reshape(-1, 3).astype(np.float64)
    s, c = math.sin(heading), math.cos(heading)
    dx, dz = p[:, 0] - x, p[:, 2] - z
    fwd = dx * s + dz * c
    lat = dx * c - dz * s
    return np.stack([lat, p[:, 1] - elev, fwd], axis=1)


def build_episodes(tasks: list[str] | None = None, min_len: int = 4, root: Path | None = None):
    tasks = tasks or DEFAULT_TASKS
    if not has_data("silver/tracks", root):
        return [], []
    con = connect(root)
    try:
        placeholders = ",".join("?" * len(tasks))
        rows = con.execute(
            f"SELECT badge, \"window\", t, x, z, heading, elev, action, role, pose FROM tracks "
            f"WHERE pose IS NOT NULL AND NOT stale AND action IN ({placeholders}) ORDER BY badge, t", tasks).fetchall()
        placed = []
        if has_data("silver/events", root):
            placed = con.execute("SELECT badge, t, slot FROM events WHERE type = 'block_placed' ORDER BY t").fetchall()
    finally:
        con.close()

    slots = build_plan().slot_by_id()
    episodes, current = [], []
    for row in rows:
        if current and (row[0] != current[-1][0] or row[7] != current[-1][7] or row[1] != current[-1][1] + 1):
            if len(current) >= min_len:
                episodes.append(current)
            current = []
        current.append(row)
    if len(current) >= min_len:
        episodes.append(current)

    out = []
    for frames in episodes:
        badge, action = frames[0][0], frames[0][7]
        t0, t1 = frames[0][2], frames[-1][2]
        target = None
        if action == "pose_parpaing":
            cands = [(abs(t - t1), slot) for b, t, slot in placed if b == badge and t0 - 0.5 <= t <= t1 + 2.0]
            if cands:
                target = slots.get(min(cands)[1])
        states, bases = [], []
        for (_, _, t, x, z, heading, elev, *_rest, pose) in frames:
            states.append(_to_body(np.asarray(pose), x, z, heading, elev or 0.0).flatten())
            bases.append([x, z, heading, elev or 0.0])
        out.append({"badge": badge, "role": frames[-1][8], "action": action, "t0": t0, "t1": t1,
                    "states": np.asarray(states, dtype=np.float32), "bases": np.asarray(bases, dtype=np.float32),
                    "times": [f[2] - t0 for f in frames], "target": target})
    return out, tasks


def build_zip(tasks: list[str] | None = None, root: Path | None = None) -> tuple[bytes, dict]:
    episodes, tasks = build_episodes(tasks, root=root)
    task_index = {t: i for i, t in enumerate(tasks)}
    buf = io.BytesIO()
    all_states = []
    summary = {"episodes": len(episodes), "frames": 0, "by_task": {}}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        ep_lines = []
        for i, ep in enumerate(episodes):
            n = len(ep["states"])
            actions = np.vstack([ep["states"][1:], ep["states"][-1:]])  # pose suivante = commande
            tgt = ep["target"]
            target = [tgt.x, tgt.y, tgt.z, tgt.rot] if tgt else [math.nan] * 4
            table = pa.table({
                "episode_index": pa.array([i] * n, pa.int32()),
                "frame_index": pa.array(range(n), pa.int32()),
                "timestamp": pa.array(ep["times"], pa.float32()),
                "observation.state": pa.array(ep["states"].tolist(), pa.list_(pa.float32(), 45)),
                "observation.base": pa.array(ep["bases"].tolist(), pa.list_(pa.float32(), 4)),
                "observation.target": pa.array([target] * n, pa.list_(pa.float32(), 4)),
                "action": pa.array(actions.tolist(), pa.list_(pa.float32(), 45)),
                "task_index": pa.array([task_index[ep["action"]]] * n, pa.int32()),
                "next.done": pa.array([False] * (n - 1) + [True], pa.bool_()),
            })
            sink = io.BytesIO()
            pq.write_table(table, sink, compression="zstd")
            zf.writestr(f"data/chunk-000/episode_{i:06d}.parquet", sink.getvalue())
            desc = TASK_TEXT[ep["action"]]
            if tgt:
                desc += f" (mur {tgt.wall}, rang {tgt.row}, emplacement {tgt.id})"
            ep_lines.append(json.dumps({
                "episode_index": i, "length": n, "tasks": [desc], "task": ep["action"],
                "worker": ep["badge"], "role": ep["role"], "t_start": round(ep["t0"], 2),
                "t_end": round(ep["t1"], 2), "bim_slot": tgt.id if tgt else None}, ensure_ascii=False))
            all_states.append(ep["states"])
            summary["frames"] += n
            summary["by_task"][ep["action"]] = summary["by_task"].get(ep["action"], 0) + 1

        zf.writestr("meta/episodes.jsonl", "\n".join(ep_lines))
        zf.writestr("meta/tasks.jsonl", "\n".join(
            json.dumps({"task_index": i, "task": t, "description": TASK_TEXT[t]}, ensure_ascii=False)
            for i, t in enumerate(tasks)))
        if all_states:
            st = np.vstack(all_states)
            stats = {"observation.state": {"mean": st.mean(0).round(4).tolist(), "std": st.std(0).round(4).tolist(),
                                           "min": st.min(0).round(4).tolist(), "max": st.max(0).round(4).tolist()}}
            zf.writestr("meta/stats.json", json.dumps(stats))
        info = {
            "codebase_version": "v2.0-compatible", "robot_type": "humanoid_demonstration",
            "source": "ConStruct-AI — capture multi-caméras de chantier", "site_id": settings.site_id,
            "fps": round(1 / settings.window, 2), "total_episodes": len(episodes),
            "total_frames": summary["frames"], "total_tasks": len(tasks),
            "data_path": "data/chunk-{episode_chunk:03d}/episode_{episode_index:06d}.parquet",
            "features": {
                "observation.state": {"dtype": "float32", "shape": [45],
                                      "names": [f"{j}.{a}" for j in JOINTS for a in ("lat", "up", "fwd")]},
                "observation.base": {"dtype": "float32", "shape": [4], "names": ["x", "z", "heading", "elevation"]},
                "observation.target": {"dtype": "float32", "shape": [4], "names": ["x", "y", "z", "rot"]},
                "action": {"dtype": "float32", "shape": [45], "names": "same as observation.state (t+1)"},
            },
            "frames": {"body": "origine au sol sous le bassin ; axes (latéral gauche, haut, avant), mètres",
                       "site": "x est, z nord, y haut ; origine centre de la maison, mètres"},
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        zf.writestr("meta/info.json", json.dumps(info, indent=2, ensure_ascii=False))
        zf.writestr("README.md", DATASET_CARD.format(**summary, site=settings.site_id))
    return buf.getvalue(), summary


DATASET_CARD = """# ConStruct-AI — Dataset de démonstrations humaines (chantier {site})

* Épisodes : {episodes} — Frames : {frames}
* Répartition par tâche : {by_task}

## Provenance
Trajectoires et poses 3D reconstruites par fusion de caméras fixes de chantier
et de caméras POV portées au torse, recalées sur la maquette BIM.

## Anonymisation (RGPD)
Aucune image, aucun visage, aucun nom : les ouvriers sont identifiés par un
pseudonyme de badge. Seuls des squelettes 3D et des événements sont exportés.

## Usage
Apprentissage par imitation / behaviour cloning pour robots de maçonnerie
(chargeable avec `pandas.read_parquet` ou `lerobot`).
"""


def export_to_disk(out_dir: Path | None = None) -> Path:
    data, summary = build_zip()
    out_dir = Path(out_dir or settings.lake_dir.parent / "exports")
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"construct_ai_dataset_{settings.site_id}.zip"
    path.write_bytes(data)
    return path
