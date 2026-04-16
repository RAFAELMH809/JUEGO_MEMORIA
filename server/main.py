from __future__ import annotations

import logging
import threading

import uvicorn

from app.broadcaster import EventBroadcaster
from app.config import load_settings
from app.game_engine import GameEngine
from app.grpc_server import build_grpc_server
from app.storage import JsonMatchStorage
from app.web import create_web_app


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def main() -> None:
    configure_logging()
    settings = load_settings()

    storage = JsonMatchStorage(settings.history_file)
    broadcaster = EventBroadcaster(queue_size=200)
    engine = GameEngine(settings=settings, broadcaster=broadcaster, storage=storage)

    grpc_server = build_grpc_server(engine, settings)
    app = create_web_app(engine, settings)

    grpc_server.start()
    logging.getLogger("memory.main").info(
        "Servidor gRPC escuchando en %s:%s", settings.grpc_host, settings.grpc_port
    )

    def wait_grpc() -> None:
        grpc_server.wait_for_termination()

    grpc_wait_thread = threading.Thread(target=wait_grpc, daemon=True)
    grpc_wait_thread.start()

    try:
        uvicorn.run(
            app,
            host=settings.web_host,
            port=settings.web_port,
            log_level="info",
        )
    finally:
        logging.getLogger("memory.main").info("Deteniendo servidor gRPC")
        grpc_server.stop(grace=2)


if __name__ == "__main__":
    main()
