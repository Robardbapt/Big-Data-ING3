from datetime import datetime, timezone
from typing import Any

import cv2
import mediapipe as mp


class VisionEngine:
    """Detecte une pose, anonymise la tete et extrait les points utiles."""

    LANDMARKS = {
        "poignet_gauche": mp.solutions.pose.PoseLandmark.LEFT_WRIST,
        "poignet_droit": mp.solutions.pose.PoseLandmark.RIGHT_WRIST,
        "coude_gauche": mp.solutions.pose.PoseLandmark.LEFT_ELBOW,
        "coude_droit": mp.solutions.pose.PoseLandmark.RIGHT_ELBOW,
        "cheville_gauche": mp.solutions.pose.PoseLandmark.LEFT_ANKLE,
        "cheville_droite": mp.solutions.pose.PoseLandmark.RIGHT_ANKLE,
    }

    def __init__(self, min_detection_confidence: float = 0.5, min_tracking_confidence: float = 0.5) -> None:
        self._pose_module = mp.solutions.pose
        self._pose = self._pose_module.Pose(
            static_image_mode=False,
            min_detection_confidence=min_detection_confidence,
            min_tracking_confidence=min_tracking_confidence,
        )
        self._drawing = mp.solutions.drawing_utils

    def process_frame(self, frame: Any, site_id: str, worker_id: str) -> tuple[Any, dict[str, Any] | None]:
        image_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self._pose.process(image_rgb)
        if not results.pose_landmarks:
            return frame, None

        self._blur_head(frame, results.pose_landmarks.landmark)
        self._drawing.draw_landmarks(
            frame,
            results.pose_landmarks,
            self._pose_module.POSE_CONNECTIONS,
            self._drawing.DrawingSpec(color=(0, 255, 0), thickness=2, circle_radius=2),
            self._drawing.DrawingSpec(color=(0, 0, 255), thickness=2, circle_radius=2),
        )

        coordinates: dict[str, dict[str, float | None]] = {}
        for name, landmark_id in self.LANDMARKS.items():
            landmark = results.pose_landmarks.landmark[landmark_id]
            coordinates[name] = {
                "x": round(landmark.x, 4),
                "y": round(landmark.y, 4),
                "z": round(landmark.z, 4),
                "visibilite": round(landmark.visibility, 4),
            }

        packet = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "chantier_id": site_id,
            "worker_id": worker_id,
            "mouvement": coordinates,
        }
        return frame, packet

    @staticmethod
    def _blur_head(frame: Any, landmarks: list[Any]) -> None:
        height, width = frame.shape[:2]
        head_ids = [
            mp.solutions.pose.PoseLandmark.NOSE,
            mp.solutions.pose.PoseLandmark.LEFT_EAR,
            mp.solutions.pose.PoseLandmark.RIGHT_EAR,
            mp.solutions.pose.PoseLandmark.LEFT_EYE,
            mp.solutions.pose.PoseLandmark.RIGHT_EYE,
        ]
        visible = [landmarks[index] for index in head_ids if landmarks[index].visibility >= 0.35]
        if not visible:
            return

        x_values = [int(point.x * width) for point in visible]
        y_values = [int(point.y * height) for point in visible]
        padding_x = max(20, int((max(x_values) - min(x_values) + 1) * 0.8))
        padding_y = max(25, int((max(y_values) - min(y_values) + 1) * 1.2))
        x1 = max(0, min(x_values) - padding_x)
        y1 = max(0, min(y_values) - padding_y)
        x2 = min(width, max(x_values) + padding_x)
        y2 = min(height, max(y_values) + padding_y)
        region = frame[y1:y2, x1:x2]
        if region.size:
            kernel = max(15, ((min(region.shape[:2]) // 3) * 2) + 1)
            frame[y1:y2, x1:x2] = cv2.GaussianBlur(region, (kernel, kernel), 0)

    def close(self) -> None:
        self._pose.close()
