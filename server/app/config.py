from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Settings:
    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50051
    web_host: str = "0.0.0.0"
    web_port: int = 8000
    min_players: int = 2
    max_players: int = 4
    board_rows: int = 4
    board_cols: int = 4
    reveal_delay_seconds: float = 1.75
    auto_start_on_min_players: bool = False
    max_workers: int = 32
    data_dir: Path = Path(__file__).resolve().parents[1] / "data"
    history_file_name: str = "matches_history.json"
    event_history_limit: int = 300

    @property
    def history_file(self) -> Path:
        return self.data_dir / self.history_file_name


def _parse_bool(raw: str, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


def load_settings() -> Settings:
    settings = Settings(
        grpc_host=os.getenv("GRPC_HOST", "0.0.0.0"),
        grpc_port=int(os.getenv("GRPC_PORT", "50051")),
        web_host=os.getenv("WEB_HOST", "0.0.0.0"),
        web_port=int(os.getenv("WEB_PORT", "8000")),
        min_players=int(os.getenv("MIN_PLAYERS", "2")),
        max_players=int(os.getenv("MAX_PLAYERS", "4")),
        board_rows=int(os.getenv("BOARD_ROWS", "4")),
        board_cols=int(os.getenv("BOARD_COLS", "4")),
        reveal_delay_seconds=float(os.getenv("REVEAL_DELAY_SECONDS", "1.75")),
        auto_start_on_min_players=_parse_bool(
            os.getenv("AUTO_START_ON_MIN_PLAYERS"), False
        ),
        max_workers=int(os.getenv("GRPC_MAX_WORKERS", "32")),
    )
    validate_settings(settings)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    return settings


def validate_settings(settings: Settings) -> None:
    if not (4 <= settings.board_rows <= 8 and 4 <= settings.board_cols <= 8):
        raise ValueError("BOARD_ROWS y BOARD_COLS deben estar entre 4 y 8")

    if settings.board_rows % 2 != 0 or settings.board_cols % 2 != 0:
        raise ValueError("BOARD_ROWS y BOARD_COLS deben ser pares")

    if (settings.board_rows * settings.board_cols) % 2 != 0:
        raise ValueError("El tablero debe tener un numero par de celdas")

    if settings.min_players < 2:
        raise ValueError("MIN_PLAYERS debe ser al menos 2")

    if settings.max_players < settings.min_players:
        raise ValueError("MAX_PLAYERS debe ser mayor o igual a MIN_PLAYERS")

    if settings.max_players > 4:
        raise ValueError("MAX_PLAYERS no puede ser mayor que 4 para esta practica")

    if settings.reveal_delay_seconds <= 0:
        raise ValueError("REVEAL_DELAY_SECONDS debe ser mayor que cero")
