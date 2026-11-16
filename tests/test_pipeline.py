"""Tests de bout en bout du pipeline (sans Kafka) : python -m pytest -q"""
import asyncio
import io
import json
import zipfile

import pytest

from construct_ai.bus import MemoryBus
from construct_ai.config import Topics
from construct_ai.edge.sensors import SensorRig
from construct_ai.edge.simulator import SiteSimulator
from construct_ai.site.plan import build_plan
from construct_ai.stream.fusion import FusionEngine
from construct_ai.stream.lake import LakeWriter
from construct_ai.stream.progress import ProgressTracker


def test_plan_bim():
    plan = build_plan()
    ids = [s.id for s in plan.slots]
    assert len(ids) == len(set(ids)) == 518
    assert {s.wall for s in plan.slots} == {"S", "E", "N", "W"}


def _cam(t, x=0.0):
    return {"cam": "CAM-X", "t": t, "detections": [{"tag": "H-1", "x": x, "z": 0.0, "sigma": 0.1}]}


def test_watermark_handles_out_of_order_and_late_events():
    f = FusionEngine(window=0.2, lateness=1.0)
    for t in (0.1, 0.5, 0.3, 0.9, 0.7):  # désordre < retard toléré : rien n'est perdu
        assert f.ingest(Topics.FIXED_CAM, _cam(t))
    assert f.metrics["out_of_order"] == 2
    f.ingest(Topics.FIXED_CAM, _cam(5.0))
    closed = f.poll()
    assert closed and closed[-1]["t"] <= f.watermark
    assert not f.ingest(Topics.FIXED_CAM, _cam(0.2))  # derrière le watermark → late
    assert f.metrics["late"] == 1


@pytest.fixture(scope="module")
def run_pipeline(tmp_path_factory):
    """Fait tourner 6 minutes de chantier à travers capteurs → fusion → BIM → lake."""
    lake = LakeWriter(tmp_path_factory.mktemp("lake"))
    sim = SiteSimulator(seed=7)
    rig = SensorRig(sim)
    fusion, progress = FusionEngine(), ProgressTracker()
    while sim.t < 360:
        sim.step()
        for topic, key, ev in rig.step():
            if topic == Topics.INTERACTIONS:
                if ev["type"] == "block_release":
                    out = progress.on_release(ev)
                    out["ts"] = ev["ts"]
                    lake.event_row(out)
            else:
                fusion.ingest(topic, ev)
                if topic != Topics.GROUND_TRUTH:
                    lake.bronze(topic, ev, key)
        for state in fusion.poll():
            lake.track_rows(state)
    lake.flush_sync()
    return sim, fusion, progress, lake


def test_fusion_quality(run_pipeline):
    sim, fusion, progress, _ = run_pipeline
    ev = fusion.evaluation()
    assert ev["action_accuracy"] > 0.85
    assert ev["position_mae_m"] < 0.25
    roles = {b: fusion.roles.role(b)[0] for b in fusion.tracks}
    assert roles == {w.badge: w.role for w in sim.workers}  # rôles déduits sans être déclarés


def test_bim_progress_matches_ground_truth(run_pipeline):
    sim, _, progress, _ = run_pipeline
    # tous les blocs posés (sauf ceux encore « en vol » sur le réseau) sont rattachés au bon emplacement
    assert len(sim.built) - 3 <= len(progress.built) <= len(sim.built)
    assert set(progress.built) <= set(sim.built)
    assert progress.anomalies == 0


def test_lake_analytics_replay_and_dataset(run_pipeline):
    from construct_ai.batch import dataset, gold

    lake = run_pipeline[3]
    a = gold.analytics(lake.root)
    assert sum(r["blocks"] for r in a["blocks_by_worker"]) == len(run_pipeline[2].built)
    assert len(a["workers"]) == 5
    frames = gold.replay(100, 110, lake.root)
    assert len(frames) >= 45 and len(frames[0]["workers"][0]["pose"]) == 45
    data, summary = dataset.build_zip(root=lake.root)
    zf = zipfile.ZipFile(io.BytesIO(data))
    names = zf.namelist()
    assert {"meta/info.json", "meta/episodes.jsonl", "meta/tasks.jsonl", "README.md"} <= set(names)
    assert summary["episodes"] > 50 and "pose_parpaing" in summary["by_task"]
    first = json.loads(zf.read("meta/episodes.jsonl").decode().splitlines()[0])
    assert first["length"] >= 4


def test_memory_bus_fanout_per_consumer_group():
    async def scenario():
        bus = MemoryBus()
        got = {"a": [], "b": []}

        async def consume(group):
            async for _, msg in bus.subscribe(["topic"], group=group):
                got[group].append(msg["i"])
                if len(got[group]) == 3:
                    return

        tasks = [asyncio.create_task(consume(g)) for g in got]
        await asyncio.sleep(0)
        for i in range(3):
            await bus.publish("topic", {"i": i})
        await asyncio.wait_for(asyncio.gather(*tasks), 2)
        return got

    assert asyncio.run(scenario()) == {"a": [0, 1, 2], "b": [0, 1, 2]}
