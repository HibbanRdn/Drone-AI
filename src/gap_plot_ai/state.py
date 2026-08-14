from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Dict, Optional, Set


class AppState(str, Enum):
    IDLE = "IDLE"
    STARTING = "STARTING"
    WARMING_UP = "WARMING_UP"
    RUNNING = "RUNNING"
    STOPPING = "STOPPING"
    ERROR = "ERROR"
    FINALIZING = "FINALIZING"


@dataclass(frozen=True)
class AppError:
    code: str
    message: str
    occurred_at: str
    recoverable: bool = True


_ALLOWED: Dict[AppState, Set[AppState]] = {
    AppState.IDLE: {AppState.STARTING, AppState.ERROR},
    AppState.STARTING: {AppState.WARMING_UP, AppState.RUNNING, AppState.STOPPING, AppState.ERROR},
    AppState.WARMING_UP: {AppState.RUNNING, AppState.STOPPING, AppState.ERROR},
    AppState.RUNNING: {AppState.STOPPING, AppState.ERROR},
    AppState.STOPPING: {AppState.FINALIZING, AppState.IDLE, AppState.ERROR},
    AppState.FINALIZING: {AppState.IDLE, AppState.ERROR},
    AppState.ERROR: {AppState.IDLE, AppState.STARTING, AppState.STOPPING},
}


class StateMachine:
    """Thread-safe operational state with explicit, auditable transitions."""

    def __init__(
        self, callback: Optional[Callable[[Dict[str, Any]], None]] = None
    ) -> None:
        self._lock = threading.RLock()
        self._state = AppState.IDLE
        self._last_error: Optional[AppError] = None
        self._changed_at = datetime.now(timezone.utc).isoformat()
        self._callback = callback

    @property
    def state(self) -> AppState:
        with self._lock:
            return self._state

    @property
    def last_error(self) -> Optional[AppError]:
        with self._lock:
            return self._last_error

    def transition(self, target: AppState) -> bool:
        with self._lock:
            if target == self._state:
                return False
            if target not in _ALLOWED[self._state]:
                raise RuntimeError(
                    "Transisi state tidak valid: {} -> {}".format(
                        self._state.value, target.value
                    )
                )
            self._state = target
            self._changed_at = datetime.now(timezone.utc).isoformat()
            if target == AppState.RUNNING or target == AppState.IDLE:
                if self._last_error and self._last_error.recoverable:
                    self._last_error = None
            snapshot = self._snapshot_unlocked()
        if self._callback is not None:
            self._callback(snapshot)
        return True

    def fail(self, code: str, message: str, *, recoverable: bool = True) -> None:
        with self._lock:
            self._last_error = AppError(
                code=code,
                message=message,
                occurred_at=datetime.now(timezone.utc).isoformat(),
                recoverable=recoverable,
            )
            if self._state != AppState.ERROR:
                if AppState.ERROR not in _ALLOWED[self._state]:
                    raise RuntimeError(
                        f"State {self._state.value} tidak dapat masuk ERROR"
                    )
                self._state = AppState.ERROR
            self._changed_at = datetime.now(timezone.utc).isoformat()
            snapshot = self._snapshot_unlocked()
        if self._callback is not None:
            self._callback(snapshot)

    def clear_error(self) -> None:
        with self._lock:
            if self._state != AppState.ERROR:
                return
        self.transition(AppState.IDLE)

    def snapshot(self) -> Dict[str, Any]:
        with self._lock:
            return self._snapshot_unlocked()

    def _snapshot_unlocked(self) -> Dict[str, Any]:
        return {
            "state": self._state.value,
            "changed_at": self._changed_at,
            "last_error": asdict(self._last_error) if self._last_error else None,
        }
