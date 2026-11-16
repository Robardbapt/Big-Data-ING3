"""Point d'entrée : ``python -m construct_ai <commande>``.

    demo     tout-en-un (edge + stream + api) — bus mémoire ou Kafka
    edge     simulateur de chantier + capteurs → Kafka
    stream   traitement temps réel + data lake ← Kafka
    api      serveur web / WebSocket ← Kafka
    gold     job batch : tables gold + export du dataset robotique
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import shutil

from .config import settings


def _setup_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)-7s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("aiokafka").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def _reset_lake() -> None:
    if settings.lake_dir.exists():
        shutil.rmtree(settings.lake_dir)
    settings.lake_dir.mkdir(parents=True, exist_ok=True)


async def _serve_api(bus) -> None:
    import uvicorn

    from .api.server import create_app

    app, hub = create_app(bus)
    server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port, log_level="warning"))
    logging.getLogger("api").info("Jumeau numérique : http://localhost:%s", settings.port)
    await asyncio.gather(server.serve(), hub.run())


async def _main(args) -> None:
    from .bus import create_bus

    bus = create_bus(args.bus)
    await bus.start()
    tasks = []
    try:
        if args.cmd in ("demo", "stream") and not args.keep_lake:
            _reset_lake()
        if args.cmd in ("demo", "api"):
            tasks.append(asyncio.create_task(_serve_api(bus)))
        if args.cmd in ("demo", "stream"):
            from .stream.run import StreamService
            tasks.append(asyncio.create_task(StreamService(bus).run()))
        if args.cmd in ("demo", "edge"):
            from .edge.run import EdgeService
            await asyncio.sleep(1.0)  # laisse les consumers s'abonner
            tasks.append(asyncio.create_task(EdgeService(bus, speed=args.speed).run()))
        await asyncio.gather(*tasks)
    finally:
        await bus.stop()


def main() -> None:
    parser = argparse.ArgumentParser(prog="construct_ai", description="ConStruct-AI — jumeau numérique de chantier")
    parser.add_argument("cmd", choices=["demo", "edge", "stream", "api", "gold"])
    parser.add_argument("--bus", choices=["memory", "kafka"], default=None,
                        help="bus de messages (défaut : memory pour demo, kafka sinon)")
    parser.add_argument("--speed", type=float, default=None, help="accélération du temps simulé")
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--keep-lake", action="store_true", help="ne pas vider le data lake au démarrage")
    args = parser.parse_args()
    _setup_logging()
    if args.port:
        settings.port = args.port
    if args.bus is None:
        args.bus = settings.bus if args.cmd == "demo" else "kafka"

    if args.cmd == "gold":
        from .batch import dataset, gold
        log = logging.getLogger("gold")
        log.info("Tables gold : %s", gold.materialize())
        log.info("Dataset robotique exporté : %s", dataset.export_to_disk())
        return
    try:
        asyncio.run(_main(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
