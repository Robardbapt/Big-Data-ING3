"""Adaptateur VIDÉO RÉELLE → bus (expérimental).

Remplace le simulateur pour un ouvrier : lit une vidéo (ou une webcam), estime
la pose 3D avec MediaPipe Pose Landmarker, **floute les visages localement**,
puis publie uniquement le squelette et la position au sol, avec exactement le
même schéma que les capteurs simulés (``edge.pov.frames`` +
``edge.fixedcam.detections``). Aucune image ne quitte la machine (edge
computing / RGPD).

    pip install -r requirements-vision.txt
    python -m construct_ai.vision.video_adapter --video chantier.mp4 --bus kafka --show
    python -m construct_ai.vision.video_adapter --video 0 --dry-run      # webcam, affiche les messages

Calibration sol : la position du pied dans l'image est projetée sur une zone
du chantier (--zone x0 z0 x1 z1). Sur un vrai chantier on utiliserait une
homographie calibrée par caméra.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import math
import time
import urllib.request
from datetime import timedelta
from pathlib import Path

import numpy as np

from ..config import BASE_DIR, Topics, settings

log = logging.getLogger("video")
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/pose_landmarker/"
             "pose_landmarker_lite/float16/latest/pose_landmarker_lite.task")
# indices MediaPipe (33 points) → nos 15 articulations
MP = {"nose": 0, "l_sh": 11, "r_sh": 12, "l_el": 13, "r_el": 14, "l_wr": 15, "r_wr": 16,
      "l_hip": 23, "r_hip": 24, "l_kn": 25, "r_kn": 26, "l_an": 27, "r_an": 28}


def mediapipe_to_body(world: np.ndarray) -> tuple[np.ndarray, float]:
    """Landmarks monde MediaPipe (m, origine bassin, y vers le bas) → repère corps ConStruct-AI.

    Le repère corps est défini par le squelette lui-même (axe des hanches) :
    il est donc indépendant de l'orientation de la caméra.
    Retourne (pose 15x3 [latéral, haut, avant], cap de l'ouvrier vu par la caméra).
    """
    p = world.copy()
    p[:, 1] *= -1  # y vers le haut
    p[:, 2] *= -1  # on garde un repère direct (rotation de 180° autour de x)
    g = lambda k: p[MP[k]]
    pelvis = (g("l_hip") + g("r_hip")) / 2
    neck = (g("l_sh") + g("r_sh")) / 2
    joints = np.array([g("nose"), neck, g("l_sh"), g("r_sh"), g("l_el"), g("r_el"), g("l_wr"), g("r_wr"),
                       pelvis, g("l_hip"), g("r_hip"), g("l_kn"), g("r_kn"), g("l_an"), g("r_an")])
    left = g("l_hip") - g("r_hip")
    lx, lz = left[0], left[2]
    n = math.hypot(lx, lz) or 1.0
    lx, lz = lx / n, lz / n
    rel = joints - pelvis
    lat = rel[:, 0] * lx + rel[:, 2] * lz
    fwd = rel[:, 0] * -lz + rel[:, 2] * lx  # avant = gauche x haut
    yaw = math.atan2(-lz, lx)
    foot = min(joints[13, 1], joints[14, 1])
    up = rel[:, 1] - (foot - pelvis[1]) + 0.06
    return np.stack([lat, up, fwd], axis=1), yaw


def blur_faces(frame, landmarks) -> None:
    """Floutage du visage (RGPD) à partir des points de la tête."""
    import cv2

    h, w = frame.shape[:2]
    pts = [(lm.x * w, lm.y * h) for lm in landmarks[:11] if getattr(lm, "visibility", 1) > 0.3]
    if not pts:
        return
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    pad = max(25, int((max(xs) - min(xs)) * 0.9))
    x1, y1 = max(0, int(min(xs) - pad)), max(0, int(min(ys) - pad * 1.3))
    x2, y2 = min(w, int(max(xs) + pad)), min(h, int(max(ys) + pad))
    roi = frame[y1:y2, x1:x2]
    if roi.size:
        k = max(15, (min(roi.shape[:2]) // 3) * 2 + 1)
        frame[y1:y2, x1:x2] = cv2.GaussianBlur(roi, (k, k), 0)


class SimClock:
    """Aligne le temps des événements sur l'horloge du chantier (simulateur ou temps réel)."""

    def __init__(self) -> None:
        self.sim_t: float | None = None
        self.speed = 1.0
        self.rx = time.monotonic()
        self.start = time.monotonic()

    def on_metrics(self, m: dict) -> None:
        if m.get("source") == "edge":
            self.sim_t, self.speed, self.rx = m["sim_t"], m.get("speed", 1.0), time.monotonic()

    def now(self) -> float:
        if self.sim_t is None:
            return time.monotonic() - self.start
        return self.sim_t + (time.monotonic() - self.rx) * self.speed


async def run(args) -> None:
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions, vision

    model = Path(args.model)
    if not model.exists():
        model.parent.mkdir(parents=True, exist_ok=True)
        log.info("Téléchargement du modèle MediaPipe → %s", model)
        urllib.request.urlretrieve(MODEL_URL, model)
    landmarker = vision.PoseLandmarker.create_from_options(vision.PoseLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=str(model)), running_mode=vision.RunningMode.VIDEO))

    bus = None
    clock = SimClock()
    if not args.dry_run:
        from ..bus import create_bus
        bus = create_bus(args.bus)
        await bus.start()

        async def follow_clock():
            async for _, m in bus.subscribe([Topics.METRICS], group=f"video-{args.badge}"):
                clock.on_metrics(m)
        asyncio.create_task(follow_clock())

    src = int(args.video) if str(args.video).isdigit() else args.video
    cap = cv2.VideoCapture(src)
    if not cap.isOpened():
        raise FileNotFoundError(f"Vidéo illisible : {args.video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    x0, z0, x1, z1 = args.zone
    frame_id, sent, last_pub = 0, 0, 0.0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                if args.loop:
                    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                    continue
                break
            frame_id += 1
            ts_ms = int(frame_id * 1000 / fps)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            res = landmarker.detect_for_video(image, ts_ms)
            if res.pose_world_landmarks and time.monotonic() - last_pub >= 1 / settings.pov_hz:
                last_pub = time.monotonic()
                lms = res.pose_landmarks[0]
                world = np.array([[lm.x, lm.y, lm.z] for lm in res.pose_world_landmarks[0]])
                pose, yaw = mediapipe_to_body(world)
                foot_u = (lms[27].x + lms[28].x) / 2
                foot_v = max(lms[27].y, lms[28].y)
                gx = x0 + foot_u * (x1 - x0)
                gz = z1 - min(max(foot_v, 0.0), 1.0) * (z1 - z0)
                t = clock.now() if bus else frame_id / fps
                ts = (settings.site_start + timedelta(seconds=t)).isoformat()
                pov = {"badge": args.badge, "site": settings.site_id, "t": round(t, 3), "ts": ts,
                       "frame": frame_id, "pose": np.round(pose, 3).tolist(), "heading": round(yaw, 3),
                       "odom": [round(gx, 3), round(gz, 3)], "elev": 0.0,
                       "contacts": {"l": None, "r": None}, "source": "video"}
                cam = {"cam": "CAM-VIDEO", "site": settings.site_id, "t": round(t, 3), "ts": ts, "frame": frame_id,
                       "detections": [{"tag": args.badge, "x": round(gx, 3), "z": round(gz, 3), "sigma": 0.3,
                                       "conf": round(float(np.mean([lm.visibility for lm in lms])), 3), "h_px": 0}]}
                if bus:
                    await bus.publish(Topics.POV, pov, key=args.badge)
                    await bus.publish(Topics.FIXED_CAM, cam, key="CAM-VIDEO")
                elif sent < 3 or args.verbose:
                    print(json.dumps(pov)[:400], "...")
                sent += 1
            if args.show:
                if res.pose_landmarks:
                    blur_faces(frame, res.pose_landmarks[0])
                cv2.imshow("ConStruct-AI - edge (visages floutés)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
            await asyncio.sleep(0 if args.fast else 1 / fps)
    finally:
        cap.release()
        landmarker.close()
        if bus:
            await bus.stop()
    log.info("%d frames lues, %d poses publiées", frame_id, sent)


def main() -> None:
    ap = argparse.ArgumentParser(description="Vidéo réelle → flux ConStruct-AI")
    ap.add_argument("--video", required=True, help="fichier vidéo ou index de webcam (0)")
    ap.add_argument("--badge", default="H-200", help="badge pseudonymisé de l'ouvrier filmé")
    ap.add_argument("--bus", choices=["kafka", "memory"], default="kafka")
    ap.add_argument("--zone", type=float, nargs=4, default=[-12.0, -11.0, -6.0, -6.0],
                    metavar=("X0", "Z0", "X1", "Z1"), help="zone du chantier couverte par la caméra")
    ap.add_argument("--model", default=str(BASE_DIR / "data" / "models" / "pose_landmarker_lite.task"))
    ap.add_argument("--show", action="store_true", help="afficher la vidéo (visages floutés)")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--fast", action="store_true", help="ne pas respecter le fps de la vidéo")
    ap.add_argument("--dry-run", action="store_true", help="n'envoie rien, affiche les messages")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-7s %(message)s", datefmt="%H:%M:%S")
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
