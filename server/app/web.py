from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated
from typing import AsyncGenerator

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .game_engine import GameEngine

LOGGER = logging.getLogger("memory.web")


def create_web_app(engine: GameEngine, settings: Settings) -> FastAPI:
    app = FastAPI(
        title="Memory Game Distributed Server",
        description="Panel de monitoreo del servidor gRPC del juego de memoria",
        version="1.0.0",
    )

    templates = Jinja2Templates(directory=str(settings.data_dir.parent / "templates"))
    app.mount(
        "/static",
        StaticFiles(directory=str(settings.data_dir.parent / "static")),
        name="static",
    )

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "snapshot": engine.get_admin_snapshot(),
                "project_title": "Juego de Memoria Distribuido con gRPC",
            },
        )

    @app.get("/api/state", response_class=JSONResponse)
    async def api_state() -> JSONResponse:
        return JSONResponse(engine.get_admin_snapshot())

    @app.get("/api/stats", response_class=JSONResponse)
    async def api_stats(match_id: str = "") -> JSONResponse:
        return JSONResponse(engine.get_stats(match_id=match_id))

    @app.get("/api/history", response_class=JSONResponse)
    async def api_history(
        match_id: str = "",
        limit: Annotated[int, Query(ge=1, le=200)] = 25,
    ) -> JSONResponse:
        return JSONResponse(engine.get_match_history(match_id=match_id, limit=limit))

    @app.get("/events")
    async def sse_events() -> StreamingResponse:
        subscription = engine.broadcaster.subscribe()

        async def event_stream() -> AsyncGenerator[str, None]:
            try:
                initial = engine.build_full_sync_update()
                yield f"event: game_update\ndata: {json.dumps(initial, ensure_ascii=False)}\n\n"

                while True:
                    try:
                        payload = await asyncio.to_thread(subscription.updates.get, True, 15)
                        payload["snapshot"] = engine.get_admin_snapshot()
                        yield f"event: game_update\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"
                    except Exception:
                        yield "event: heartbeat\ndata: {}\n\n"
            except asyncio.CancelledError:
                LOGGER.info("SSE stream cancelado")
                raise
            finally:
                engine.broadcaster.unsubscribe(subscription.subscription_id)

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            },
        )

    return app
