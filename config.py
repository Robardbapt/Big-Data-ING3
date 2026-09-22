from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
VIDEO_PATH = Path(os.getenv("CONSTRUCT_VIDEO", BASE_DIR / "chantier.mp4"))
KAFKA_BOOTSTRAP_SERVERS = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
KAFKA_TOPIC = os.getenv("KAFKA_TOPIC", "construct-ai-mouvements")
KAFKA_CLIENT_ID = os.getenv("KAFKA_CLIENT_ID", "construct-ai-poc")
SITE_ID = os.getenv("CONSTRUCT_SITE_ID", "Paris-Secteur-7")
WORKER_ID = os.getenv("CONSTRUCT_WORKER_ID", "Anonyme-01")
FRAME_WIDTH = int(os.getenv("CONSTRUCT_FRAME_WIDTH", "800"))
FRAME_HEIGHT = int(os.getenv("CONSTRUCT_FRAME_HEIGHT", "600"))
DISPLAY_WINDOW = os.getenv("CONSTRUCT_DISPLAY", "1").lower() not in {"0", "false", "no"}
KAFKA_CONNECT_TIMEOUT_MS = int(os.getenv("KAFKA_CONNECT_TIMEOUT_MS", "1500"))
