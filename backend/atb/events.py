"""In-process publish/subscribe used for the live activity feed."""

import asyncio
import time
from contextlib import contextmanager


class EventBus:
    def __init__(self, queue_size: int = 500):
        self._subscribers: set[asyncio.Queue] = set()
        self._queue_size = queue_size

    def publish(self, kind: str, owner_id: str, **data) -> None:
        event = {"type": kind, "owner_id": owner_id, "ts": time.time(), **data}
        for queue in list(self._subscribers):
            if queue.full():
                queue.get_nowait()  # a slow consumer loses the oldest events
            queue.put_nowait(event)

    @contextmanager
    def subscribe(self):
        queue: asyncio.Queue = asyncio.Queue(self._queue_size)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)
