import cv2

from config import (
    DISPLAY_WINDOW,
    FRAME_HEIGHT,
    FRAME_WIDTH,
    KAFKA_BOOTSTRAP_SERVERS,
    KAFKA_CLIENT_ID,
    KAFKA_CONNECT_TIMEOUT_MS,
    KAFKA_TOPIC,
    SITE_ID,
    VIDEO_PATH,
    WORKER_ID,
)
from kafka_producer import AsyncKafkaProducer
from vision_engine import VisionEngine


def main() -> None:
    capture = cv2.VideoCapture(str(VIDEO_PATH))
    if not capture.isOpened():
        raise FileNotFoundError(f"Video introuvable ou illisible: {VIDEO_PATH}")

    producer = AsyncKafkaProducer(
        topic=KAFKA_TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        client_id=KAFKA_CLIENT_ID,
        connect_timeout_ms=KAFKA_CONNECT_TIMEOUT_MS,
    )
    vision = VisionEngine()
    print("--- DEMARRAGE DU FLUX ConStruct-AI ---")

    try:
        while True:
            ret, frame = capture.read()
            if not ret:
                print("Fin de la video.")
                break

            frame = cv2.resize(frame, (FRAME_WIDTH, FRAME_HEIGHT))
            frame, packet = vision.process_frame(frame, SITE_ID, WORKER_ID)
            if packet is not None:
                producer.send(packet)

            if DISPLAY_WINDOW:
                cv2.imshow("ConStruct-AI - Capture anonymisee", frame)
                if cv2.waitKey(10) & 0xFF == ord("q"):
                    break
    finally:
        capture.release()
        vision.close()
        producer.close()
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
