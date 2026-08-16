"""Thread-safe consecutive login failure tracking."""

from __future__ import annotations

import math
import threading
import time
from dataclasses import dataclass
from typing import Callable, Dict


@dataclass
class _AttemptState:
    failures: int
    last_failure: float
    locked_until: float = 0.0


class LoginAttemptLimiter:
    """Lock a username temporarily after consecutive authentication failures."""

    def __init__(
        self,
        max_failures: int = 5,
        lockout_seconds: int = 30 * 60,
        *,
        time_fn: Callable[[], float] = time.monotonic,
        max_entries: int = 10_000,
    ) -> None:
        self.max_failures = max(1, max_failures)
        self.lockout_seconds = max(1, lockout_seconds)
        self.failure_window_seconds = self.lockout_seconds
        self.max_entries = max(100, max_entries)
        self._time_fn = time_fn
        self._states: Dict[str, _AttemptState] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(username: str) -> str:
        return (username or "").strip()[:128]

    def retry_after(self, username: str) -> int:
        """Return remaining lock seconds, or zero when authentication may proceed."""
        key = self._key(username)
        now = self._time_fn()
        with self._lock:
            state = self._states.get(key)
            if state is None:
                return 0
            remaining = state.locked_until - now
            if remaining > 0:
                return max(1, math.ceil(remaining))
            if state.locked_until or now - state.last_failure >= self.failure_window_seconds:
                self._states.pop(key, None)
            return 0

    def record_failure(self, username: str) -> int:
        """Record a failed login and return lock seconds when the threshold is reached."""
        key = self._key(username)
        now = self._time_fn()
        with self._lock:
            state = self._states.get(key)
            if state is None or now - state.last_failure >= self.failure_window_seconds:
                state = _AttemptState(failures=0, last_failure=now)
                self._states[key] = state

            if state.locked_until > now:
                return max(1, math.ceil(state.locked_until - now))

            state.failures += 1
            state.last_failure = now
            if state.failures >= self.max_failures:
                state.locked_until = now + self.lockout_seconds
                return self.lockout_seconds

            self._prune(now)
            return 0

    def record_success(self, username: str) -> None:
        """A successful login breaks the consecutive failure sequence."""
        with self._lock:
            self._states.pop(self._key(username), None)

    def clear(self) -> None:
        """Clear all state; intended for tests and controlled maintenance."""
        with self._lock:
            self._states.clear()

    def _prune(self, now: float) -> None:
        stale = [
            key
            for key, state in self._states.items()
            if state.locked_until <= now
            and now - state.last_failure >= self.failure_window_seconds
        ]
        for key in stale:
            self._states.pop(key, None)
        if len(self._states) > self.max_entries:
            oldest = min(self._states, key=lambda key: self._states[key].last_failure)
            self._states.pop(oldest, None)
