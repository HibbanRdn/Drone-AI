from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Generic, Optional, TypeVar

T = TypeVar("T")


@dataclass(frozen=True)
class QueueStats:
    capacity: int
    queued: int
    pushed: int
    popped: int
    dropped: int
    closed: bool


class LatestFrameQueue(Generic[T]):
    """Bounded queue that discards oldest work instead of creating latency backlog."""

    def __init__(self, capacity: int = 2):
        if capacity < 1:
            raise ValueError("capacity minimal 1")
        self._capacity = capacity
        self._items: Deque[T] = deque()
        self._condition = threading.Condition()
        self._closed = False
        self._pushed = 0
        self._popped = 0
        self._dropped = 0

    def put(self, item: T) -> bool:
        with self._condition:
            if self._closed:
                return False
            self._pushed += 1
            if len(self._items) >= self._capacity:
                self._items.popleft()
                self._dropped += 1
            self._items.append(item)
            self._condition.notify()
            return True

    def get(self, timeout: Optional[float] = None) -> Optional[T]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._condition:
            while not self._items and not self._closed:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return None
                self._condition.wait(remaining)
            if not self._items:
                return None
            self._popped += 1
            return self._items.popleft()

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def stats(self) -> QueueStats:
        with self._condition:
            return QueueStats(
                capacity=self._capacity,
                queued=len(self._items),
                pushed=self._pushed,
                popped=self._popped,
                dropped=self._dropped,
                closed=self._closed,
            )
