from __future__ import annotations

import logging
import queue
from concurrent import futures

import grpc

from generated import memory_game_pb2, memory_game_pb2_grpc

from .config import Settings
from .game_engine import GameEngine

LOGGER = logging.getLogger("memory.grpc")

STATUS_MAP = {
    "WAITING_FOR_PLAYERS": memory_game_pb2.WAITING_FOR_PLAYERS,
    "IN_PROGRESS": memory_game_pb2.IN_PROGRESS,
    "FINISHED": memory_game_pb2.FINISHED,
}

EVENT_MAP = {
    "PLAYER_JOINED": memory_game_pb2.PLAYER_JOINED,
    "GAME_STARTED": memory_game_pb2.GAME_STARTED,
    "TURN_CHANGED": memory_game_pb2.TURN_CHANGED,
    "TURN_PLAYED": memory_game_pb2.TURN_PLAYED,
    "MATCH_FOUND": memory_game_pb2.MATCH_FOUND,
    "MISS_REVEALED": memory_game_pb2.MISS_REVEALED,
    "MISS_HIDDEN": memory_game_pb2.MISS_HIDDEN,
    "GAME_OVER": memory_game_pb2.GAME_OVER,
    "STATS_UPDATED": memory_game_pb2.STATS_UPDATED,
    "FULL_STATE_SYNC": memory_game_pb2.FULL_STATE_SYNC,
    "SYSTEM_MESSAGE": memory_game_pb2.SYSTEM_MESSAGE,
}


def _event_type_to_proto(name: str) -> int:
    return EVENT_MAP.get(name, memory_game_pb2.EVENT_TYPE_UNSPECIFIED)


def _status_to_proto(name: str) -> int:
    return STATUS_MAP.get(name, memory_game_pb2.GAME_STATUS_UNSPECIFIED)


def _to_event(event: dict) -> memory_game_pb2.EventEntry:
    return memory_game_pb2.EventEntry(
        event_id=event.get("event_id", ""),
        timestamp=event.get("timestamp", ""),
        event_type=_event_type_to_proto(event.get("event_type", "")),
        message=event.get("message", ""),
        actor_player_id=event.get("actor_player_id", ""),
    )


def _to_player_stats(player: dict) -> memory_game_pb2.PlayerStats:
    return memory_game_pb2.PlayerStats(
        player_id=player.get("player_id", ""),
        name=player.get("name", ""),
        score=player.get("score", 0),
        moves=player.get("moves", 0),
        pairs_found=player.get("pairs_found", 0),
        total_response_time=player.get("total_response_time", 0.0),
        average_response_time=player.get("average_response_time", 0.0),
        is_current_turn=player.get("is_current_turn", False),
        response_times=player.get("response_times", []),
    )


def _to_snapshot(snapshot: dict) -> memory_game_pb2.GameSnapshot:
    board = snapshot.get("board", {})
    players = snapshot.get("players", [])

    cell_items = [
        memory_game_pb2.CellPublic(
            row=cell.get("row", 0),
            col=cell.get("col", 0),
            is_revealed=cell.get("is_revealed", False),
            is_matched=cell.get("is_matched", False),
            value=cell.get("value", ""),
        )
        for cell in board.get("cells", [])
    ]

    player_items = [_to_player_stats(player) for player in players]

    board_state = memory_game_pb2.BoardPublicState(
        rows=board.get("rows", 0),
        cols=board.get("cols", 0),
        cells=cell_items,
        total_pairs=board.get("total_pairs", 0),
        matched_pairs=board.get("matched_pairs", 0),
    )

    return memory_game_pb2.GameSnapshot(
        match_id=snapshot.get("match_id", ""),
        status=_status_to_proto(snapshot.get("status", "")),
        board=board_state,
        players=player_items,
        current_turn_player_id=snapshot.get("current_turn_player_id", ""),
        current_turn_player_name=snapshot.get("current_turn_player_name", ""),
        connected_players=snapshot.get("connected_players", 0),
        min_players=snapshot.get("min_players", 0),
        game_over=snapshot.get("game_over", False),
        winners=snapshot.get("winners", []),
        started_at=snapshot.get("started_at", ""),
        finished_at=snapshot.get("finished_at", ""),
        remaining_pairs=snapshot.get("remaining_pairs", 0),
    )


def _to_update(update: dict) -> memory_game_pb2.GameUpdate:
    return memory_game_pb2.GameUpdate(
        event_type=_event_type_to_proto(update.get("event_type", "")),
        timestamp=update.get("timestamp", ""),
        message=update.get("message", ""),
        event=_to_event(update.get("event", {})),
        snapshot=_to_snapshot(update.get("snapshot", {})),
    )


class MemoryGameGrpcService(memory_game_pb2_grpc.MemoryGameServiceServicer):
    def __init__(self, engine: GameEngine) -> None:
        self.engine = engine

    def JoinGame(self, request, context):
        result = self.engine.join_game(request.player_name)
        if not result["accepted"]:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details(result["reason"])

        return memory_game_pb2.JoinGameResponse(
            accepted=result["accepted"],
            reason=result["reason"],
            player_id=result["player_id"],
            snapshot=_to_snapshot(result["snapshot"]),
        )

    def GetBoardState(self, request, context):
        snapshot = self.engine.get_snapshot()
        return memory_game_pb2.BoardStateResponse(
            success=True,
            reason="OK",
            snapshot=_to_snapshot(snapshot),
        )

    def PlayTurn(self, request, context):
        result = self.engine.play_turn(
            request.player_id,
            (request.first.row, request.first.col),
            (request.second.row, request.second.col),
        )

        if not result["accepted"]:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details(result["reason"])

        return memory_game_pb2.PlayTurnResponse(
            accepted=result["accepted"],
            reason=result["reason"],
            matched=result["matched"],
            game_over=result["game_over"],
            snapshot=_to_snapshot(result["snapshot"]),
            event=_to_event(result["event"]),
        )

    def SubscribeToUpdates(self, request, context):
        subscription = self.engine.broadcaster.subscribe()
        LOGGER.info(
            "Nuevo suscriptor de stream id=%s player_id=%s nombre=%s",
            subscription.subscription_id,
            request.player_id,
            request.subscriber_name,
        )

        try:
            full_sync = self.engine.build_full_sync_update()
            yield _to_update(full_sync)

            while context.is_active():
                try:
                    update = subscription.updates.get(timeout=1.0)
                except queue.Empty:
                    continue
                yield _to_update(update)
        finally:
            self.engine.broadcaster.unsubscribe(subscription.subscription_id)
            LOGGER.info("Suscriptor stream cerrado id=%s", subscription.subscription_id)

    def GetStats(self, request, context):
        result = self.engine.get_stats(request.match_id)
        if not result["success"]:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(result["reason"])

        return memory_game_pb2.StatsResponse(
            success=result["success"],
            reason=result["reason"],
            snapshot=_to_snapshot(result.get("snapshot", {})),
            ranking=[_to_player_stats(p) for p in result.get("ranking", [])],
            recent_events=[_to_event(e) for e in result.get("recent_events", [])],
        )

    def GetMatchHistory(self, request, context):
        result = self.engine.get_match_history(
            match_id=request.match_id,
            limit=request.limit or 25,
        )

        matches = []
        for match in result.get("matches", []):
            players = [
                memory_game_pb2.StoredPlayerResult(
                    player_id=item.get("player_id", ""),
                    name=item.get("name", ""),
                    score=item.get("score", 0),
                    moves=item.get("moves", 0),
                    pairs_found=item.get("pairs_found", 0),
                    total_response_time=item.get("total_response_time", 0.0),
                    average_response_time=item.get("average_response_time", 0.0),
                    response_times=item.get("response_times", []),
                )
                for item in match.get("players", [])
            ]

            matches.append(
                memory_game_pb2.StoredMatch(
                    match_id=match.get("match_id", ""),
                    started_at=match.get("started_at", ""),
                    finished_at=match.get("finished_at", ""),
                    rows=match.get("rows", 0),
                    cols=match.get("cols", 0),
                    status=match.get("status", ""),
                    winners=match.get("winners", []),
                    players=players,
                    events=[_to_event(e) for e in match.get("events", [])],
                )
            )

        if not result["success"]:
            context.set_code(grpc.StatusCode.NOT_FOUND)
            context.set_details(result["reason"])

        return memory_game_pb2.MatchHistoryResponse(
            success=result["success"],
            reason=result["reason"],
            matches=matches,
        )


def build_grpc_server(engine: GameEngine, settings: Settings) -> grpc.Server:
    grpc_server = grpc.server(futures.ThreadPoolExecutor(max_workers=settings.max_workers))
    memory_game_pb2_grpc.add_MemoryGameServiceServicer_to_server(
        MemoryGameGrpcService(engine), grpc_server
    )
    grpc_server.add_insecure_port(f"{settings.grpc_host}:{settings.grpc_port}")
    return grpc_server
