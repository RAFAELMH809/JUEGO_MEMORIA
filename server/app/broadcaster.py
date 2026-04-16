from __future__ import annotations

import queue
import threading
import uuid
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Subscription:
    subscription_id: str
    updates: queue.Queue[dict[str, Any]]


class EventBroadcaster:
    """Difunde eventos a multiples suscriptores usando una cola por suscriptor."""

    def __init__(self, queue_size: int = 100) -> None:
        self._queue_size = queue_size
        self._subscriptions: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def subscribe(self) -> Subscription:
        subscription_id = str(uuid.uuid4())
        updates: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=self._queue_size)
        with self._lock:
            self._subscriptions[subscription_id] = updates
        return Subscription(subscription_id=subscription_id, updates=updates)

    def unsubscribe(self, subscription_id: str) -> None:
        with self._lock:
            self._subscriptions.pop(subscription_id, None)

    def publish(self, event: dict[str, Any]) -> None:
        with self._lock:
            targets = list(self._subscriptions.items())

        for _, updates in targets:
            if updates.full():
                try:
                    updates.get_nowait()
                except queue.Empty:
                    pass
            updates.put_nowait(event)

    @property
    def subscriber_count(self) -> int:
        with self._lock:
            return len(self._subscriptions)
