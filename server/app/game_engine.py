from __future__ import annotations

import threading
import time
import uuid
from statistics import mean
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
        self._board_rows = settings.board_rows
        self._board_cols = settings.board_cols
        self._manual_board_size: tuple[int, int] | None = None

        self._players: dict[str, Player] = {}
        self._player_order: list[str] = []
        self._current_turn_index: int = 0
        self._turn_started_monotonic: float = time.monotonic()

        self._board_values: list[list[str]] = []
        self._board_states: list[list[str]] = []

        self._pending_miss = False
        self._pending_first_pick: dict[str, tuple[int, int]] = {}
        self._winners: list[str] = []
        self._persisted = False
        self._round_started_monotonic: float | None = None
        self._round_elapsed_ms: float = 0.0
        self._player_sessions: dict[str, str] = {}
        self._player_help_counts: dict[str, int] = {}
        self._player_latency_samples: dict[str, list[float]] = {}
        self._event_history: deque[dict[str, Any]] = deque(maxlen=settings.event_history_limit)
        self._initialize_board_locked()

        self._add_system_event_locked(
            "SYSTEM_MESSAGE",
            f"Servidor inicializado. Esperando al menos {settings.min_players} jugadores.",
        )

    @property
    def lock(self) -> threading.Lock:
        return self._lock

    def join_game(
        self,
        player_name: str,
        session_id: str = "",
        client_latency_ms: float | None = None,
    ) -> dict[str, Any]:
        cleaned_name = (player_name or "").strip()
        if not cleaned_name:
            return {
                "accepted": False,
                "reason": "El nombre del jugador no puede estar vacio",
                "player_id": "",
                "snapshot": self.get_snapshot(),
            }

        with self._lock:
            if self._status == FINISHED:
                self._reset_for_new_match_locked()

            if self._status == IN_PROGRESS:
                existing_player_id = self._find_player_id_by_name_locked(cleaned_name)
                if existing_player_id:
                    self._set_player_session_locked(existing_player_id, session_id)
                    self._register_latency_locked(existing_player_id, client_latency_ms)
                    return {
                        "accepted": True,
                        "reason": "Jugador reconectado a la partida en curso",
                        "player_id": existing_player_id,
                        "snapshot": self._build_snapshot_locked(),
                    }
                return {
                    "accepted": False,
                    "reason": "La partida ya comenzo. Espera a que termine para iniciar una nueva ronda.",
                    "player_id": "",
                    "snapshot": self._build_snapshot_locked(),
                }

            if len(self._player_order) >= self.settings.max_players:
                return {
                    "accepted": False,
                    "reason": f"La sala esta llena (maximo {self.settings.max_players} jugadores)",
                    "player_id": "",
                    "snapshot": self._build_snapshot_locked(),
                }

            player_id = str(uuid.uuid4())
            player = Player(player_id=player_id, name=cleaned_name)
            self._players[player_id] = player
            self._player_order.append(player_id)
            self._set_player_session_locked(player_id, session_id)
            self._register_latency_locked(player_id, client_latency_ms)

            event = self._add_event_locked(
                "PLAYER_JOINED",
                f"Jugador {cleaned_name} se unio a la partida",
                actor_player_id=player_id,
            )
            self._publish_update_locked(event)

            self._adapt_board_size_for_waiting_locked()

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

    def admin_start_game(self) -> dict[str, Any]:
        with self._lock:
            if self._status == IN_PROGRESS:
                return {
                    "success": False,
                    "reason": "La partida ya esta en curso",
                    "snapshot": self._build_snapshot_locked(),
                }

            if len(self._player_order) < self.settings.min_players:
                return {
                    "success": False,
                    "reason": f"Se requieren al menos {self.settings.min_players} jugadores",
                    "snapshot": self._build_snapshot_locked(),
                }

            if self._status == FINISHED:
                self._reset_for_new_match_locked()
                return {
                    "success": False,
                    "reason": "Partida finalizada. Registra jugadores para la nueva ronda",
                    "snapshot": self._build_snapshot_locked(),
                }

            self._start_game_locked()
            return {
                "success": True,
                "reason": "Partida iniciada por administrador",
                "snapshot": self._build_snapshot_locked(),
            }

    def admin_reset_match(self) -> dict[str, Any]:
        with self._lock:
            self._reset_for_new_match_locked()
            return {
                "success": True,
                "reason": "Partida reiniciada por administrador",
                "snapshot": self._build_snapshot_locked(),
            }

    def admin_set_board_size(self, size: int) -> dict[str, Any]:
        with self._lock:
            if self._status != WAITING_FOR_PLAYERS:
                return {
                    "success": False,
                    "reason": "Solo puedes ajustar el tablero antes de iniciar la partida",
                    "snapshot": self._build_snapshot_locked(),
                }

            if size not in {4, 6, 8}:
                return {
                    "success": False,
                    "reason": "Tamano invalido. Usa 4, 6 u 8",
                    "snapshot": self._build_snapshot_locked(),
                }

            self._manual_board_size = (size, size)
            self._board_rows, self._board_cols = size, size
            self._initialize_board_locked()

            event = self._add_event_locked(
                "SYSTEM_MESSAGE",
                f"Admin fijo tablero manualmente en {size}x{size}",
            )
            self._publish_update_locked(event)

            return {
                "success": True,
                "reason": f"Tablero configurado manualmente en {size}x{size}",
                "snapshot": self._build_snapshot_locked(),
            }

    def admin_use_auto_board(self) -> dict[str, Any]:
        with self._lock:
            if self._status != WAITING_FOR_PLAYERS:
                return {
                    "success": False,
                    "reason": "Solo puedes activar modo automatico antes de iniciar la partida",
                    "snapshot": self._build_snapshot_locked(),
                }

            self._manual_board_size = None
            self._adapt_board_size_for_waiting_locked(force=True)

            event = self._add_event_locked(
                "SYSTEM_MESSAGE",
                "Admin activo el modo automatico de tamano de tablero.",
            )
            self._publish_update_locked(event)

            return {
                "success": True,
                "reason": "Modo automatico activado",
                "snapshot": self._build_snapshot_locked(),
            }

    def admin_remove_player(self, player_id: str) -> dict[str, Any]:
        with self._lock:
            if self._status != WAITING_FOR_PLAYERS:
                return {
                    "success": False,
                    "reason": "Solo puedes quitar jugadores antes de iniciar la partida",
                    "snapshot": self._build_snapshot_locked(),
                }

            if player_id not in self._players:
                return {
                    "success": False,
                    "reason": "Jugador no encontrado en la sala",
                    "snapshot": self._build_snapshot_locked(),
                }

            removed = self._players.pop(player_id)
            self._player_order = [pid for pid in self._player_order if pid != player_id]
            self._pending_first_pick.pop(player_id, None)

            self._adapt_board_size_for_waiting_locked()

            event = self._add_event_locked(
                "SYSTEM_MESSAGE",
                f"Admin removio al jugador {removed.name} de la sala.",
                actor_player_id=player_id,
            )
            self._publish_update_locked(event)

            return {
                "success": True,
                "reason": f"Jugador {removed.name} removido",
                "snapshot": self._build_snapshot_locked(),
            }

    def preview_first_pick(
        self,
        player_id: str,
        row: int,
        col: int,
        session_id: str = "",
        client_latency_ms: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if self._status != IN_PROGRESS:
                return {
                    "success": False,
                    "reason": "La partida aun no ha iniciado",
                    "snapshot": self._build_snapshot_locked(),
                }

            if self._pending_miss:
                return {
                    "success": False,
                    "reason": "Espera a que termine la jugada actual",
                    "snapshot": self._build_snapshot_locked(),
                }

            if player_id not in self._players:
                return {
                    "success": False,
                    "reason": "Jugador no registrado",
                    "snapshot": self._build_snapshot_locked(),
                }

            self._set_player_session_locked(player_id, session_id)
            self._register_latency_locked(player_id, client_latency_ms)

            expected_player_id = self._player_order[self._current_turn_index]
            if player_id != expected_player_id:
                expected_name = self._players[expected_player_id].name
                return {
                    "success": False,
                    "reason": f"No es tu turno. Turno actual: {expected_name}",
                    "snapshot": self._build_snapshot_locked(),
                }

            if not self._is_valid_position(row, col):
                return {
                    "success": False,
                    "reason": "Coordenadas fuera de rango",
                    "snapshot": self._build_snapshot_locked(),
                }

            if self._board_states[row][col] == MATCHED:
                return {
                    "success": False,
                    "reason": "No puedes elegir una carta ya emparejada",
                    "snapshot": self._build_snapshot_locked(),
                }

            existing = self._pending_first_pick.get(player_id)
            if existing and existing != (row, col):
                return {
                    "success": False,
                    "reason": "Ya elegiste la primera carta. Selecciona la segunda.",
                    "snapshot": self._build_snapshot_locked(),
                }

            self._pending_first_pick[player_id] = (row, col)
            self._player_help_counts[player_id] = self._player_help_counts.get(player_id, 0) + 1

            return {
                "success": True,
                "reason": "Primera carta seleccionada",
                "row": row,
                "col": col,
                "value": self._board_values[row][col],
                "snapshot": self._build_snapshot_locked(),
            }

    def _find_player_id_by_name_locked(self, player_name: str) -> str:
        lowered = player_name.strip().lower()
        for player_id, player in self._players.items():
            if player.name.strip().lower() == lowered:
                return player_id
        return ""

    def _initialize_board_locked(self) -> None:
        self._board_values = create_shuffled_board(
            self._board_rows,
            self._board_cols,
        )
        self._board_states = [
            [HIDDEN for _ in range(self._board_cols)]
            for _ in range(self._board_rows)
        ]

    def _suggest_board_size_by_players_locked(self) -> tuple[int, int]:
        count = len(self._player_order)
        if count <= 2:
            return (4, 4)
        if count == 3:
            return (6, 6)
        return (8, 8)

    def _adapt_board_size_for_waiting_locked(self, force: bool = False) -> None:
        if self._status != WAITING_FOR_PLAYERS:
            return
        if self._manual_board_size and not force:
            return
        rows, cols = self._suggest_board_size_by_players_locked()
        if (rows, cols) == (self._board_rows, self._board_cols):
            return

        self._board_rows, self._board_cols = rows, cols
        self._initialize_board_locked()
        event = self._add_event_locked(
            "SYSTEM_MESSAGE",
            f"Tamano de tablero ajustado a {rows}x{cols} para {len(self._player_order)} jugadores en sala.",
        )
        self._publish_update_locked(event)

    def _reset_for_new_match_locked(self) -> None:
        self._status = WAITING_FOR_PLAYERS
        self._match_id = str(uuid.uuid4())
        self._started_at = ""
        self._finished_at = ""
        self._players.clear()
        self._player_order.clear()
        self._current_turn_index = 0
        self._turn_started_monotonic = time.monotonic()
        self._pending_miss = False
        self._pending_first_pick.clear()
        self._manual_board_size = None
        self._winners = []
        self._persisted = False
        self._round_started_monotonic = None
        self._round_elapsed_ms = 0.0
        self._player_sessions.clear()
        self._player_help_counts.clear()
        self._player_latency_samples.clear()
        self._event_history.clear()
        self._initialize_board_locked()

        event = self._add_event_locked(
            "SYSTEM_MESSAGE",
            f"Nueva partida creada. Esperando al menos {self.settings.min_players} jugadores.",
        )
        self._publish_update_locked(event)

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

    def play_turn(
        self,
        player_id: str,
        first: tuple[int, int],
        second: tuple[int, int],
        session_id: str = "",
        client_latency_ms: float | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            self._set_player_session_locked(player_id, session_id)
            self._register_latency_locked(player_id, client_latency_ms)

            pending_first = self._pending_first_pick.get(player_id)
            if pending_first and pending_first != first:
                return {
                    "accepted": False,
                    "reason": "La primera carta no coincide con tu seleccion previa.",
                    "matched": False,
                    "game_over": self._status == FINISHED,
                    "snapshot": self._build_snapshot_locked(),
                    "event": self._add_event_locked(
                        "SYSTEM_MESSAGE",
                        f"Turno rechazado para jugador {player_id}: primera carta invalida",
                        actor_player_id=player_id,
                    ),
                }

            validation_error = self._validate_turn_locked(player_id, first, second)
            if validation_error:
                self._pending_first_pick.pop(player_id, None)
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
            response_time_ms = response_time * 1000.0
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
                        "result": "hit",
                        "response_time": round(response_time, 6),
                        "t_resp_ms": round(response_time_ms, 3),
                        "lat_red_ms": round(self._average_latency_ms_locked(player_id), 3),
                        "timestamp": utc_now_iso(),
                    }
                )

                self._board_states[first_row][first_col] = MATCHED
                self._board_states[second_row][second_col] = MATCHED
                self._pending_first_pick.pop(player_id, None)

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
                    "result": "miss",
                    "response_time": round(response_time, 6),
                    "t_resp_ms": round(response_time_ms, 3),
                    "lat_red_ms": round(self._average_latency_ms_locked(player_id), 3),
                    "timestamp": utc_now_iso(),
                }
            )

            self._pending_miss = True
            self._pending_first_pick.pop(player_id, None)
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
            self._pending_first_pick.clear()

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
        return 0 <= row < self._board_rows and 0 <= col < self._board_cols

    def _start_game_locked(self) -> None:
        if self._status != WAITING_FOR_PLAYERS:
            return

        self._status = IN_PROGRESS
        self._started_at = utc_now_iso()
        self._round_started_monotonic = time.monotonic()
        self._round_elapsed_ms = 0.0
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
        if self._round_started_monotonic is not None:
            self._round_elapsed_ms = max(
                0.0, (time.monotonic() - self._round_started_monotonic) * 1000.0
            )
        ranking = self._ranking_locked()
        if not ranking:
            self._winners = []
            return

        best_score = ranking[0]["score"]
        winners = [item["name"] for item in ranking if item["score"] == best_score]
        self._winners = winners

    def _all_pairs_found_locked(self) -> bool:
        for row, col in flatten_board(self._board_rows, self._board_cols):
            if self._board_states[row][col] != MATCHED:
                return False
        return True

    def _public_board_locked(self) -> dict[str, Any]:
        cells: list[dict[str, Any]] = []
        matched_pairs = 0
        for row, col in flatten_board(self._board_rows, self._board_cols):
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

        total_pairs = (self._board_rows * self._board_cols) // 2
        return {
            "rows": self._board_rows,
            "cols": self._board_cols,
            "cells": cells,
            "total_pairs": total_pairs,
            "matched_pairs": matched_pairs // 2,
        }

    def _admin_board_locked(self) -> dict[str, Any]:
        """Vista del tablero para administracion: siempre muestra emoji real."""
        cells: list[dict[str, Any]] = []
        matched_pairs = 0
        for row, col in flatten_board(self._board_rows, self._board_cols):
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

        total_pairs = (self._board_rows * self._board_cols) // 2
        return {
            "rows": self._board_rows,
            "cols": self._board_cols,
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
            "max_players": self.settings.max_players,
            "game_over": self._status == FINISHED,
            "winners": self._winners,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "remaining_pairs": remaining_pairs,
            "board_recommendation": f"{self._suggest_board_size_by_players_locked()[0]}x{self._suggest_board_size_by_players_locked()[1]}",
            "board_selected": f"{self._board_rows}x{self._board_cols}",
            "board_mode": "manual" if self._manual_board_size else "automatico",
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

        round_metrics = self._build_round_metrics_locked()

        record = {
            "match_id": self._match_id,
            "id_ron": self._match_id,
            "started_at": self._started_at,
            "finished_at": self._finished_at,
            "rows": self._board_rows,
            "cols": self._board_cols,
            "tam_tab": f"{self._board_rows}x{self._board_cols}",
            "niv_dif": self._difficulty_level_locked(),
            "status": self._status,
            "winners": self._winners,
            "players": [
                self._players[player_id].to_storage_dict() for player_id in self._player_order
            ],
            "round_metrics": round_metrics,
            "events": list(self._event_history),
        }
        self.storage.save_match(record)
        self._persisted = True

    def _set_player_session_locked(self, player_id: str, session_id: str) -> None:
        if player_id not in self._players:
            return
        cleaned_session = (session_id or "").strip()
        if cleaned_session:
            self._player_sessions[player_id] = cleaned_session
            return
        if player_id not in self._player_sessions:
            self._player_sessions[player_id] = str(uuid.uuid4())

    def _register_latency_locked(self, player_id: str, latency_ms: float | None) -> None:
        if player_id not in self._players or latency_ms is None:
            return
        safe_latency = max(0.0, float(latency_ms))
        samples = self._player_latency_samples.setdefault(player_id, [])
        samples.append(safe_latency)
        if len(samples) > 50:
            del samples[: len(samples) - 50]

    def _average_latency_ms_locked(self, player_id: str) -> float:
        samples = self._player_latency_samples.get(player_id, [])
        if not samples:
            return 0.0
        return mean(samples)

    def _difficulty_level_locked(self) -> str:
        if self._board_rows <= 4:
            return "bajo"
        if self._board_rows <= 6:
            return "medio"
        return "alto"

    def _build_round_metrics_locked(self) -> list[dict[str, Any]]:
        pairs_total = (self._board_rows * self._board_cols) // 2
        expected_round_ms = float(pairs_total * 3000)
        t_ron_ms = round(self._round_elapsed_ms, 3)
        round_samples: list[dict[str, Any]] = []

        for player_id in self._player_order:
            player = self._players[player_id]
            total_moves = player.moves
            total_hits = player.pairs_found
            total_errors = max(0, total_moves - total_hits)
            tasa_aci = (total_hits / total_moves) if total_moves else 0.0
            turn_response_ms = [
                turn.get("t_resp_ms", round(turn.get("response_time", 0.0) * 1000.0, 3))
                for turn in player.turn_history
            ]
            avg_t_resp_ms = mean(turn_response_ms) if turn_response_ms else 0.0
            max_racha_aci, max_racha_err = self._compute_streaks_locked(player.turn_history)
            rec_par = self._compute_rec_par_locked(player.turn_history)
            idx_efi = self._compute_idx_efi_locked(
                t_ron_ms=t_ron_ms,
                expected_round_ms=expected_round_ms,
                tasa_aci=tasa_aci,
                tot_err=total_errors,
                total_moves=total_moves,
            )
            etiqueta = self._classify_performance_locked(
                t_ron_ms=t_ron_ms,
                expected_round_ms=expected_round_ms,
                tasa_aci=tasa_aci,
                idx_efi=idx_efi,
            )

            round_samples.append(
                {
                    "id_jug": player_id,
                    "id_ses": self._player_sessions.get(player_id, ""),
                    "id_ron": self._match_id,
                    "niv_dif": self._difficulty_level_locked(),
                    "tam_tab": f"{self._board_rows}x{self._board_cols}",
                    "t_resp_ms": round(avg_t_resp_ms, 3),
                    "t_resp_ms_hist": turn_response_ms,
                    "t_ron_ms": t_ron_ms,
                    "tot_aci": total_hits,
                    "tot_err": total_errors,
                    "tasa_aci": round(tasa_aci, 6),
                    "racha_err": max_racha_err,
                    "racha_aci": max_racha_aci,
                    "rec_par": round(rec_par, 6),
                    "tot_ayu": self._player_help_counts.get(player_id, 0),
                    "lat_red_ms": round(self._average_latency_ms_locked(player_id), 3),
                    "idx_efi": round(idx_efi, 6),
                    "desempeno": etiqueta,
                }
            )
        return round_samples

    def _compute_streaks_locked(self, turn_history: list[dict[str, Any]]) -> tuple[int, int]:
        max_hits = 0
        max_misses = 0
        cur_hits = 0
        cur_misses = 0

        for turn in turn_history:
            if bool(turn.get("matched")):
                cur_hits += 1
                max_hits = max(max_hits, cur_hits)
                cur_misses = 0
            else:
                cur_misses += 1
                max_misses = max(max_misses, cur_misses)
                cur_hits = 0
        return max_hits, max_misses

    def _compute_rec_par_locked(self, turn_history: list[dict[str, Any]]) -> float:
        seen_positions: set[tuple[int, int]] = set()
        matched_attempts = 0
        remembered_hits = 0

        for turn in turn_history:
            first = turn.get("first", {})
            second = turn.get("second", {})
            first_pos = (int(first.get("row", -1)), int(first.get("col", -1)))
            second_pos = (int(second.get("row", -1)), int(second.get("col", -1)))
            matched = bool(turn.get("matched"))

            if matched:
                matched_attempts += 1
                if first_pos in seen_positions or second_pos in seen_positions:
                    remembered_hits += 1

            seen_positions.add(first_pos)
            seen_positions.add(second_pos)

        if matched_attempts == 0:
            return 0.0
        return remembered_hits / matched_attempts

    def _compute_idx_efi_locked(
        self,
        t_ron_ms: float,
        expected_round_ms: float,
        tasa_aci: float,
        tot_err: int,
        total_moves: int,
    ) -> float:
        speed = min(1.0, expected_round_ms / max(1.0, t_ron_ms))
        control = 1.0 - min(1.0, (tot_err / max(1, total_moves)))
        idx = (0.45 * tasa_aci) + (0.35 * speed) + (0.20 * control)
        return max(0.0, min(1.0, idx))

    def _classify_performance_locked(
        self,
        t_ron_ms: float,
        expected_round_ms: float,
        tasa_aci: float,
        idx_efi: float,
    ) -> str:
        if idx_efi >= 0.75 and tasa_aci >= 0.70 and t_ron_ms <= expected_round_ms * 1.10:
            return "alto"
        if idx_efi >= 0.45 and tasa_aci >= 0.40 and t_ron_ms <= expected_round_ms * 1.80:
            return "medio"
        return "bajo"
