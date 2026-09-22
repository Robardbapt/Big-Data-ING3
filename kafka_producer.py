import json
import queue
import threading
from typing import Any


class AsyncKafkaProducer:
    """Envoie les messages dans un thread sans bloquer la boucle vidéo."""

    def __init__(
        self,
        topic: str,
        bootstrap_servers: str,
        client_id: str = "construct-ai-poc",
        connect_timeout_ms: int = 1500,
    ) -> None:
        self.topic = topic
        self._messages: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._producer = None
        self._simulation = False

        try:
            from kafka import KafkaProducer

            self._producer = KafkaProducer(
                bootstrap_servers=bootstrap_servers,
                client_id=client_id,
                value_serializer=lambda value: json.dumps(value).encode("utf-8"),
                request_timeout_ms=connect_timeout_ms,
            )
            print(f"Kafka actif: {bootstrap_servers} / topic={topic}")
        except Exception as error:
            self._simulation = True
            print(f"Kafka indisponible ({error}). Mode simulation active.")

        self._thread = threading.Thread(target=self._run, name="construct-kafka-producer", daemon=True)
        self._thread.start()

    @property
    def simulation(self) -> bool:
        return self._simulation

    def send(self, data: dict[str, Any]) -> None:
        """Place un message dans la file et retourne immediatement."""
        self._messages.put(data)

    def _run(self) -> None:
        while True:
            data = self._messages.get()
            try:
                if data is None:
                    return
                if self._producer is None:
                    print(f"[SIMULATION] {json.dumps(data, ensure_ascii=False)}")
                else:
                    self._producer.send(self.topic, value=data)
            except Exception as error:
                print(f"Erreur d'envoi Kafka ({error}); message affiche en simulation.")
                print(f"[SIMULATION] {json.dumps(data, ensure_ascii=False)}")
            finally:
                self._messages.task_done()

    def close(self) -> None:
        """Attend les messages en file puis ferme proprement le thread."""
        self._messages.join()
        self._messages.put(None)
        self._thread.join(timeout=2)
        if self._producer is not None:
            self._producer.flush(timeout=2)
            self._producer.close()
