from __future__ import annotations

import random
from datetime import datetime, timezone
from typing import Iterable

CARD_SYMBOLS = [
    "🍎", "🍌", "🍒", "🍇", "🍓", "🥝", "🍍", "🥥", "🍉", "🥕", "🌽", "🍄",
    "⚽", "🏀", "🎾", "🏐", "🎲", "🎯", "🎹", "🎧", "🚗", "🚲", "🚀", "🛸",
    "🐶", "🐱", "🦊", "🐼", "🐧", "🦁", "🐙", "🦋", "🌈", "☀️", "⭐", "⚡",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def chunked(values: list[str], size: int) -> list[list[str]]:
    return [values[idx : idx + size] for idx in range(0, len(values), size)]


def create_shuffled_board(rows: int, cols: int) -> list[list[str]]:
    total_cells = rows * cols
    pair_count = total_cells // 2

    if pair_count > len(CARD_SYMBOLS):
        raise ValueError("No hay suficientes simbolos para el tamano de tablero solicitado")

    selected = CARD_SYMBOLS[:pair_count]
    cards = selected + selected
    random.shuffle(cards)
    return chunked(cards, cols)


def flatten_board(rows: int, cols: int) -> Iterable[tuple[int, int]]:
    for row in range(rows):
        for col in range(cols):
            yield row, col
