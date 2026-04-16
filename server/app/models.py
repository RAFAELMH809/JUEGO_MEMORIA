from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


@dataclass(slots=True)
class Player:
    player_id: str
    name: str
    score: int = 0
    moves: int = 0
    pairs_found: int = 0
    total_response_time: float = 0.0
    response_times: list[float] = field(default_factory=list)
    turn_history: list[dict[str, Any]] = field(default_factory=list)

    @property
    def average_response_time(self) -> float:
        if not self.response_times:
            return 0.0
        return self.total_response_time / len(self.response_times)

    def to_public_dict(self, is_current_turn: bool = False) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "name": self.name,
            "score": self.score,
            "moves": self.moves,
            "pairs_found": self.pairs_found,
            "total_response_time": round(self.total_response_time, 6),
            "average_response_time": round(self.average_response_time, 6),
            "response_times": [round(x, 6) for x in self.response_times],
            "is_current_turn": is_current_turn,
        }

    def to_storage_dict(self) -> dict[str, Any]:
        payload = self.to_public_dict(False)
        payload["turn_history"] = self.turn_history
        return payload


@dataclass(slots=True)
class GameEvent:
    event_id: str
    timestamp: str
    event_type: str
    message: str
    actor_player_id: str = ""

    @staticmethod
    def now(event_id: str, event_type: str, message: str, actor_player_id: str = "") -> "GameEvent":
        return GameEvent(
            event_id=event_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            event_type=event_type,
            message=message,
            actor_player_id=actor_player_id,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type,
            "message": self.message,
            "actor_player_id": self.actor_player_id,
        }
