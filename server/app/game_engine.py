from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any

from .broadcaster import EventBroadcaster
from .config import Settings
from .models import GameEvent, Player
from .storage import JsonMatchStorage
from .utils import create_shuffled_board, flatten_board, utc_now_iso

HIDDEN = "hidden"
REVEALED = "revealed"
MATCHED = "matched"
HIDDEN_MARKER = "■"

WAITING_FOR_PLAYERS = "WAITING_FOR_PLAYERS"
IN_PROGRESS = "IN_PROGRESS"
FINISHED = "FINISHED"


class GameEngine:
    def __init__(
        self,
        settings: Settings,
        broadcaster: EventBroadcaster,
        storage: JsonMatchStorage,
    ) -> None:
        self.settings = settings
        self.broadcaster = broadcaster
        self.storage = storage

        self._lock = threading.Lock()
        self._status: str = WAITING_FOR_PLAYERS
        self._match_id = str(uuid.uuid4())
        self._started_at = ""
        self._finished_at = ""

        self._players: dict[str, Player] = {}
        self._player_order: list[str] = []
        self._current_turn_index: int = 0
        self._turn_started_monotonic: float = time.monotonic()

        self._board_values = create_shuffled_board(settings.board_rows, settings.board_cols)
        self._board_states = [
            [HIDDEN for _ in range(settings.board_cols)] for _ in range(settings.board_rows)
        ]

        self._pending_miss = False
        self._winners: list[str] = []
        self._persisted = False
        self._event_history: deque[dict[str, Any]] = deque(maxlen=settings.event_history_limit)

        self._add_system_event_locked(
            "SYSTEM_MESSAGE",
            f"Servidor inicializado. Esperando al menos {settings.min_players} jugadores.",
        )

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def join_game(self, player_name: str) -> dict[str, Any]:
        cleaned_name = (player_name or "").strip()
        if not cleaned_name:
            return {
                "accepted": False,
                "reason": "El nombre del jugador no puede estar vacio",
                "player_id": "",
                "snapshot": self.get_snapshot(),
            }

        with self._lock:
            if self._status != WAITING_FOR_PLAYERS:
                return {
                    "accepted": False,
                    "reason": "La partida ya comenzo o finalizo",
                    "player_id": "",
                    "snapshot": self._build_snapshot_locked(),
                }

            player_id = str(uuid.uuid4())
            player = Player(player_id=player_id, name=cleaned_name)
            self._players[player_id] = player
            self._player_order.append(player_id)

            event = self._add_event_locked(
                "PLAYER_JOINED",
                f"Jugador {cleaned_name} se unio a la partida",
                actor_player_id=player_id,
            )
            self._publish_update_locked(event)

            if (
                self.settings.auto_start_on_min_players
                and len(self._player_order) >= self.settings.min_players
            ):
                self._start_game_locked()

            return {
                "accepted": True,
                "reason": "Jugador registrado",
                "player_id": player_id,
                "snapshot": self._build_snapshot_locked(),
            }

    def get_snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._build_snapshot_locked()

    def get_admin_snapshot(self) -> dict[str, Any]:
        """Snapshot para panel web de administracion con tablero real visible."""
        with self._lock:
            snapshot = self._build_snapshot_locked()
            snapshot["admin_board"] = self._admin_board_locked()
            return snapshot

    def get_stats(self, match_id: str = "") -> dict[str, Any]:
        with self._lock:
            if match_id and match_id != self._match_id:
                record = self.storage.get_match(match_id)
                if not record:
                    return {
                        "success": False,
                        "reason": f"No existe la partida {match_id}",
                        "snapshot": {},
                        "ranking": [],
                        "recent_events": [],
                    }
                players = record.get("players", [])
                ranking = sorted(players, key=lambda item: (-item.get("score", 0), item.get("average_response_time", 0)))
                return {
                    "success": True,
                    "reason": "OK",
                    "snapshot": {
                        "match_id": record.get("match_id", ""),
                        "status": record.get("status", "FINISHED"),
                        "board": {
                            "rows": record.get("rows", 0),
                            "cols": record.get("cols", 0),
                            "cells": [],
                            "total_pairs": (record.get("rows", 0) * record.get("cols", 0)) // 2,
                            "matched_pairs": (record.get("rows", 0) * record.get("cols", 0)) // 2,
                        },
                        "players": ranking,
                        "current_turn_player_id": "",
                        "current_turn_player_name": "",
                        "connected_players": len(ranking),
                        "min_players": self.settings.min_players,
                        "game_over": True,
                        "winners": record.get("winners", []),
                        "started_at": record.get("started_at", ""),
                        "finished_at": record.get("finished_at", ""),
                        "remaining_pairs": 0,
                    },
                    "ranking": ranking,
                    "recent_events": record.get("events", [])[-30:],
                }

            ranking = self._ranking_locked()
            return {
                "success": True,
                "reason": "OK",
                "snapshot": self._build_snapshot_locked(),
                "ranking": ranking,
                "recent_events": list(self._event_history)[-30:],
            }

    def get_match_history(self, match_id: str = "", limit: int = 25) -> dict[str, Any]:
        if match_id:
            match = self.storage.get_match(match_id)
            if not match:
                return {"success": False, "reason": "Partida no encontrada", "matches": []}
            return {"success": True, "reason": "OK", "matches": [match]}

        matches = self.storage.list_matches(limit=limit)
        return {"success": True, "reason": "OK", "matches": matches}

    def play_turn(self, player_id: str, first: tuple[int, int], second: tuple[int, int]) -> dict[str, Any]:
        with self._lock:
            validation_error = self._validate_turn_locked(player_id, first, second)
            if validation_error:
                return {
                    "accepted": False,
                    "reason": validation_error,
                    "matched": False,
                    "game_over": self._status == FINISHED,
                    "snapshot": self._build_snapshot_locked(),
                    "event": self._add_event_locked(
                        "SYSTEM_MESSAGE",
                        f"Turno rechazado para jugador {player_id}: {validation_error}",
                        actor_player_id=player_id,
                    ),
                }

            player = self._players[player_id]
            response_time = max(0.0, time.monotonic() - self._turn_started_monotonic)
            player.total_response_time += response_time
            player.response_times.append(response_time)
            player.moves += 1

            first_row, first_col = first
            second_row, second_col = second

            self._board_states[first_row][first_col] = REVEALED
            self._board_states[second_row][second_col] = REVEALED

            turn_event = self._add_event_locked(
                "TURN_PLAYED",
                f"{player.name} jugo ({first_row},{first_col}) y ({second_row},{second_col})",
                actor_player_id=player_id,
            )
            self._publish_update_locked(turn_event)

            first_value = self._board_values[first_row][first_col]
            second_value = self._board_values[second_row][second_col]

            if first_value == second_value:
                player.score += 1
                player.pairs_found += 1
                player.turn_history.append(
                    {
                        "first": {"row": first_row, "col": first_col, "value": first_value},
                        "second": {"row": second_row, "col": second_col, "value": second_value},
                        "matched": True,
                        "response_time": round(response_time, 6),
                        "timestamp": utc_now_iso(),
                    }
                )

                self._board_states[first_row][first_col] = MATCHED
                self._board_states[second_row][second_col] = MATCHED

                match_event = self._add_event_locked(
                    "MATCH_FOUND",
                    f"{player.name} encontro pareja {first_value} y conserva el turno",
                    actor_player_id=player_id,
                )
                self._publish_update_locked(match_event)

                game_over = self._all_pairs_found_locked()
                if game_over:
                    self._finish_game_locked()
                    game_over_event = self._add_event_locked(
                        "GAME_OVER",
                        "Partida finalizada. Todas las parejas fueron encontradas.",
                    )
                    self._publish_update_locked(game_over_event)
                    self._persist_current_match_locked()
                    return {
                        "accepted": True,
                        "reason": "Pareja encontrada",
                        "matched": True,
                        "game_over": True,
                        "snapshot": self._build_snapshot_locked(),
                        "event": game_over_event,
                    }

                self._turn_started_monotonic = time.monotonic()
                stats_event = self._add_event_locked(
                    "STATS_UPDATED",
                    f"Estadisticas actualizadas para {player.name}",
                    actor_player_id=player_id,
                )
                self._publish_update_locked(stats_event)

                return {
                    "accepted": True,
                    "reason": "Pareja encontrada",
                    "matched": True,
                    "game_over": False,
                    "snapshot": self._build_snapshot_locked(),
                    "event": match_event,
                }

            player.turn_history.append(
                {
                    "first": {"row": first_row, "col": first_col, "value": first_value},
                    "second": {"row": second_row, "col": second_col, "value": second_value},
                    "matched": False,
                    "response_time": round(response_time, 6),
                    "timestamp": utc_now_iso(),
                }
            )

            self._pending_miss = True
            next_turn_index = (self._current_turn_index + 1) % len(self._player_order)
            miss_event = self._add_event_locked(
                "MISS_REVEALED",
                f"{player.name} fallo. Las cartas se ocultaran en {self.settings.reveal_delay_seconds:.2f}s",
                actor_player_id=player_id,
            )
            self._publish_update_locked(miss_event)

            resolver = threading.Thread(
                target=self._resolve_miss,
                args=(
                    self._match_id,
                    first,
                    second,
                    next_turn_index,
                ),
                daemon=True,
            )
            resolver.start()

            return {
                "accepted": True,
                "reason": "No hubo pareja",
                "matched": False,
                "game_over": False,
                "snapshot": self._build_snapshot_locked(),
                "event": miss_event,
            }

    def _resolve_miss(
        self,
        match_id: str,
        first: tuple[int, int],
        second: tuple[int, int],
        next_turn_index: int,
    ) -> None:
        time.sleep(self.settings.reveal_delay_seconds)

        with self._lock:
            if self._status != IN_PROGRESS or self._match_id != match_id:
                return

            first_row, first_col = first
            second_row, second_col = second

            if self._board_states[first_row][first_col] == REVEALED:
                self._board_states[first_row][first_col] = HIDDEN
            if self._board_states[second_row][second_col] == REVEALED:
                self._board_states[second_row][second_col] = HIDDEN

            self._current_turn_index = next_turn_index
            self._turn_started_monotonic = time.monotonic()
            self._pending_miss = False

            current_player = self._players[self._player_order[self._current_turn_index]]
            hide_event = self._add_event_locked(
                "MISS_HIDDEN",
                f"Cartas ocultas nuevamente. Turno para {current_player.name}",
                actor_player_id=current_player.player_id,
            )
            self._publish_update_locked(hide_event)

            turn_event = self._add_event_locked(
                "TURN_CHANGED",
                f"Turno cambiado a {current_player.name}",
                actor_player_id=current_player.player_id,
            )
            self._publish_update_locked(turn_event)

    def _validate_turn_locked(
        self,
        player_id: str,
        first: tuple[int, int],
        second: tuple[int, int],
    ) -> str:
        if self._status == WAITING_FOR_PLAYERS:
            return "La partida aun no ha iniciado"

        if self._status == FINISHED:
            return "La partida ya finalizo"

        if self._pending_miss:
            return "Espera a que termine la revelacion temporal del miss"

        if player_id not in self._players:
            return "Jugador no registrado"

        expected_player_id = self._player_order[self._current_turn_index]
        if player_id != expected_player_id:
            expected_name = self._players[expected_player_id].name
            return f"No es tu turno. Turno actual: {expected_name}"

        first_row, first_col = first
        second_row, second_col = second

        if not self._is_valid_position(first_row, first_col) or not self._is_valid_position(second_row, second_col):
            return "Coordenadas fuera de rango"

        if first_row == second_row and first_col == second_col:
            return "Debes seleccionar dos cartas diferentes"

        if self._board_states[first_row][first_col] == MATCHED or self._board_states[second_row][second_col] == MATCHED:
            return "No puedes elegir cartas ya emparejadas"

        return ""

    def _is_valid_position(self, row: int, col: int) -> bool:
        return 0 <= row < self.settings.board_rows and 0 <= col < self.settings.board_cols

    def _start_game_locked(self) -> None:
        if self._status != WAITING_FOR_PLAYERS:
            return

        self._status = IN_PROGRESS
        self._started_at = utc_now_iso()
        self._turn_started_monotonic = time.monotonic()
        self._current_turn_index = 0

        starter = self._players[self._player_order[self._current_turn_index]]
        start_event = self._add_event_locked(
            "GAME_STARTED",
            f"Partida iniciada con {len(self._player_order)} jugadores. Turno para {starter.name}",
            actor_player_id=starter.player_id,
        )
        self._publish_update_locked(start_event)

        turn_event = self._add_event_locked(
            "TURN_CHANGED",
            f"Turno inicial para {starter.name}",
            actor_player_id=starter.player_id,
        )
        self._publish_update_locked(turn_event)

    def _finish_game_locked(self) -> None:
        self._status = FINISHED
        self._finished_at = utc_now_iso()
        ranking = self._ranking_locked()
        if not ranking:
            self._winners = []
            return

        best_score = ranking[0]["score"]
        winners = [item["name"] for item in ranking if item["score"] == best_score]
        self._winners = winners

    def _all_pairs_found_locked(self) -> bool:
        for row, col in flatten_board(self.settings.board_rows, self.settings.board_cols):
            if self._board_states[row][col] != MATCHED:
                return False
        return True

    def _public_board_locked(self) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        matched_pairs = 0
        for row, col in flatten_board(self.settings.board_rows, self.settings.board_cols):
            state = self._board_states[row][col]
            value = self._board_values[row][col] if state in {REVEALED, MATCHED} else HIDDEN_MARKER
            if state == MATCHED:
                matched_pairs += 1
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "is_revealed": state == REVEALED,
                    "is_matched": state == MATCHED,
                    "value": value,
                }
            )

        total_pairs = (self.settings.board_rows * self.settings.board_cols) // 2
        return {
            "rows": self.settings.board_rows,
            "cols": self.settings.board_cols,
            "cells": cells,
            "total_pairs": total_pairs,
            "matched_pairs": matched_pairs // 2,
        }

    def _admin_board_locked(self) -> dict[str, Any]:
        """Vista del tablero para administracion: siempre muestra emoji real."""
        cells: list[dict[str, Any]] = []
        matched_pairs = 0
        for row, col in flatten_board(self.settings.board_rows, self.settings.board_cols):
            state = self._board_states[row][col]
            if state == MATCHED:
                matched_pairs += 1
            cells.append(
                {
                    "row": row,
                    "col": col,
                    "is_revealed": state == REVEALED,
                    "is_matched": state == MATCHED,
                    "value": self._board_values[row][col],
                }
            )

        total_pairs = (self.settings.board_rows * self.settings.board_cols) // 2
        return {
            "rows": self.settings.board_rows,
            "cols": self.settings.board_cols,
            "cells": cells,
            "total_pairs": total_pairs,
            "matched_pairs": matched_pairs // 2,
        }

    def _build_snapshot_locked(self) -> dict[str, Any]:
        current_player_id = ""
        current_player_name = ""
        if self._status == IN_PROGRESS and self._player_order:
            current_player_id = self._player_order[self._current_turn_index]
            current_player_name = self._players[current_player_id].name

        players = [
            self._players[player_id].to_public_dict(is_current_turn=(player_id == current_player_id))
            for player_id in self._player_order
        ]

        board = self._public_board_locked()
        remaining_pairs = board["total_pairs"] - board["matched_pairs"]

        return {
            "match_id": self._match_id,
            "status": self._status,
            "board": board,
            "players": players,
            "current_turn_player_id": current_player_id,
            "current_turn_player_name": current_player_name,
            "connected_players": len(self._player_order),
            "min_players": self.settings.min_players,
            "game_over": self._status == FINISHED,
            "winners": self._winners,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "remaining_pairs": remaining_pairs,
        }

    def _ranking_locked(self) -> list[dict[str, Any]]:
        entries = [player.to_public_dict() for player in self._players.values()]
        entries.sort(key=lambda item: (-item["score"], item["average_response_time"], item["moves"]))
        return entries

    def _add_event_locked(
        self,
        event_type: str,
        message: str,
        actor_player_id: str = "",
    ) -> dict[str, Any]:
        event = GameEvent.now(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            message=message,
            actor_player_id=actor_player_id,
        )
        payload = event.to_dict()
        self._event_history.append(payload)
        return payload

    def _add_system_event_locked(self, event_type: str, message: str) -> None:
        event = self._add_event_locked(event_type=event_type, message=message)
        self.broadcaster.publish(
            {
                "event_type": event_type,
                "timestamp": event["timestamp"],
                "message": event["message"],
                "event": event,
                "snapshot": self._build_snapshot_locked(),
            }
        )

    def _publish_update_locked(self, event: dict[str, Any]) -> None:
        self.broadcaster.publish(
            {
                "event_type": event["event_type"],
                "timestamp": event["timestamp"],
                "message": event["message"],
                "event": event,
                "snapshot": self._build_snapshot_locked(),
            }
        )

    def build_full_sync_update(self) -> dict[str, Any]:
        with self._lock:
            return {
                "event_type": "FULL_STATE_SYNC",
                "timestamp": utc_now_iso(),
                "message": "Sincronizacion completa del estado actual",
                "event": self._add_event_locked(
                    "FULL_STATE_SYNC",
                    "Sincronizacion completa enviada a suscriptor",
                ),
                "snapshot": self._build_snapshot_locked(),
            }

    def _persist_current_match_locked(self) -> None:
        if self._persisted:
            return

        record = {
            "match_id": self._match_id,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "rows": self.settings.board_rows,
            "cols": self.settings.board_cols,
            "status": self._status,
            "winners": self._winners,
            "players": [
                self._players[player_id].to_storage_dict() for player_id in self._player_order
            ],
            "events": list(self._event_history),
        }
        self.storage.save_match(record)
        self._persisted = True
