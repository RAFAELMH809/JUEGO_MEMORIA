from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel, Field
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .config import Settings
from .game_engine import GameEngine

LOGGER = logging.getLogger("memory.web")


class JoinPayload(BaseModel):
    player_name: str = Field(min_length=1, max_length=40)
    session_id: str = Field(default="", max_length=80)
    client_latency_ms: float | None = None


class PlayPayload(BaseModel):
    player_id: str = Field(min_length=1)
    first_row: int
    first_col: int
    second_row: int
    second_col: int
    session_id: str = Field(default="", max_length=80)
    client_latency_ms: float | None = None


class PreviewPayload(BaseModel):
    player_id: str = Field(min_length=1)
    row: int
    col: int
    session_id: str = Field(default="", max_length=80)
    client_latency_ms: float | None = None


class AdminBoardSizePayload(BaseModel):
    size: int


class AdminRemovePlayerPayload(BaseModel):
    player_id: str = Field(min_length=1)


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
    async def player_frontend(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="play.html",
            context={
                "snapshot": engine.get_snapshot(),
                "project_title": "Juego de Memoria Distribuido con gRPC",
            },
        )

    @app.get("/admin", response_class=HTMLResponse)
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
        return JSONResponse(engine.get_snapshot())

    @app.get("/api/state/admin", response_class=JSONResponse)
    async def api_state_admin() -> JSONResponse:
        return JSONResponse(engine.get_admin_snapshot())

    @app.post("/api/join", response_class=JSONResponse)
    async def api_join(payload: JoinPayload) -> JSONResponse:
        result = engine.join_game(
            payload.player_name,
            session_id=payload.session_id,
            client_latency_ms=payload.client_latency_ms,
        )
        if not result["accepted"]:
            raise HTTPException(status_code=400, detail=result["reason"])
        return JSONResponse(result)

    @app.post("/api/play", response_class=JSONResponse)
    async def api_play(payload: PlayPayload) -> JSONResponse:
        result = engine.play_turn(
            payload.player_id,
            (payload.first_row, payload.first_col),
            (payload.second_row, payload.second_col),
            session_id=payload.session_id,
            client_latency_ms=payload.client_latency_ms,
        )
        if not result["accepted"]:
            raise HTTPException(status_code=400, detail=result["reason"])
        return JSONResponse(result)

    @app.post("/api/preview", response_class=JSONResponse)
    async def api_preview(payload: PreviewPayload) -> JSONResponse:
        result = engine.preview_first_pick(
            payload.player_id,
            payload.row,
            payload.col,
            session_id=payload.session_id,
            client_latency_ms=payload.client_latency_ms,
        )
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["reason"])
        return JSONResponse(result)

    @app.post("/api/admin/start", response_class=JSONResponse)
    async def api_admin_start() -> JSONResponse:
        result = engine.admin_start_game()
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["reason"])
        return JSONResponse(result)

    @app.post("/api/admin/reset", response_class=JSONResponse)
    async def api_admin_reset() -> JSONResponse:
        result = engine.admin_reset_match()
        return JSONResponse(result)

    @app.post("/api/admin/board-size", response_class=JSONResponse)
    async def api_admin_board_size(payload: AdminBoardSizePayload) -> JSONResponse:
        result = engine.admin_set_board_size(payload.size)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["reason"])
        return JSONResponse(result)

    @app.post("/api/admin/board-size/auto", response_class=JSONResponse)
    async def api_admin_board_size_auto() -> JSONResponse:
        result = engine.admin_use_auto_board()
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["reason"])
        return JSONResponse(result)

    @app.post("/api/admin/remove-player", response_class=JSONResponse)
    async def api_admin_remove_player(payload: AdminRemovePlayerPayload) -> JSONResponse:
        result = engine.admin_remove_player(payload.player_id)
        if not result["success"]:
            raise HTTPException(status_code=400, detail=result["reason"])
        return JSONResponse(result)

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
                        public_payload = dict(payload)
                        public_payload["snapshot"] = engine.get_snapshot()
                        yield f"event: game_update\ndata: {json.dumps(public_payload, ensure_ascii=False)}\n\n"
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

    @app.get("/events/admin")
    async def sse_events_admin() -> StreamingResponse:
        subscription = engine.broadcaster.subscribe()

        async def event_stream() -> AsyncGenerator[str, None]:
            try:
                initial = engine.build_full_sync_update()
                initial["snapshot"] = engine.get_admin_snapshot()
                yield f"event: game_update\ndata: {json.dumps(initial, ensure_ascii=False)}\n\n"

                while True:
                    try:
                        payload = await asyncio.to_thread(subscription.updates.get, True, 15)
                        admin_payload = dict(payload)
                        admin_payload["snapshot"] = engine.get_admin_snapshot()
                        yield f"event: game_update\ndata: {json.dumps(admin_payload, ensure_ascii=False)}\n\n"
                    except Exception:
                        yield "event: heartbeat\ndata: {}\n\n"
            except asyncio.CancelledError:
                LOGGER.info("SSE admin stream cancelado")
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
