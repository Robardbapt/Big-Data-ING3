"""Configuration centrale de ConStruct-AI (surchargée par variables d'environnement)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


@dataclass
class Settings:
    # --- Bus de messages ---------------------------------------------------
    bus: str = _env("CONSTRUCT_BUS", "memory")  # "memory" | "kafka"
    kafka_bootstrap: str = _env("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    kafka_partitions: int = int(_env("KAFKA_PARTITIONS", "6"))

    # --- Chantier ----------------------------------------------------------
    site_id: str = _env("CONSTRUCT_SITE_ID", "CHANTIER-PARIS-07")
    # Début du chantier (temps "événement" de référence)
    site_start: datetime = field(
        default_factory=lambda: datetime(2026, 3, 2, 7, 30, tzinfo=timezone.utc)
    )
    seed: int = int(_env("CONSTRUCT_SEED", "42"))
    n_masons: int = int(_env("CONSTRUCT_MASONS", "2"))

    # --- Simulation / capteurs (en secondes de temps simulé) ---------------
    speed: float = float(_env("CONSTRUCT_SPEED", "10"))  # x fois le temps réel
    tick: float = 0.1
    fixed_cam_hz: float = 5.0
    pov_hz: float = 10.0
    net_latency_min: float = 0.05
    net_latency_max: float = 0.40
    pov_outage_prob_per_s: float = 0.006  # proba qu'une body-cam perde le wifi
    pov_outage_duration: tuple[float, float] = (2.0, 7.0)

    # --- Stream processing --------------------------------------------------
    window: float = 0.2  # taille de fenêtre tumbling (temps événement)
    allowed_lateness: float = 2.0  # watermark = max(event_time) - allowed_lateness

    # --- Data lake ----------------------------------------------------------
    lake_dir: Path = Path(_env("CONSTRUCT_LAKE", str(BASE_DIR / "data" / "lake")))
    lake_flush_rows: int = 5000
    lake_flush_seconds: float = 5.0

    # --- API ----------------------------------------------------------------
    host: str = _env("CONSTRUCT_HOST", "0.0.0.0")
    port: int = int(_env("CONSTRUCT_PORT", "8000"))
    web_dir: Path = BASE_DIR / "web"


settings = Settings()


# Topics Kafka (convention <couche>.<domaine>)
class Topics:
    FIXED_CAM = "edge.fixedcam.detections"  # caméras globales du chantier
    POV = "edge.pov.frames"  # caméras torse (POV) : pose ego + odométrie
    INTERACTIONS = "edge.pov.interactions"  # prise / dépose d'objets vue en POV
    GROUND_TRUTH = "sim.groundtruth"  # vérité terrain (évaluation uniquement)
    TWIN_STATE = "twin.state"  # état fusionné du jumeau numérique
    TWIN_EVENTS = "twin.events"  # événements métier (bloc posé, alertes...)
    METRICS = "ops.metrics"  # métriques du pipeline
    CONTROL = "control.sim"  # commandes (vitesse, pause)

    EDGE = [FIXED_CAM, POV, INTERACTIONS]
    ALL = [FIXED_CAM, POV, INTERACTIONS, GROUND_TRUTH, TWIN_STATE, TWIN_EVENTS, METRICS, CONTROL]
