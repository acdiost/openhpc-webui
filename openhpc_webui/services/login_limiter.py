"""Cross-process login throttling with source and account dimensions."""

from __future__ import annotations

import ipaddress
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Callable, Optional, Tuple

from ..config import ENV_FILE


class LoginAttemptLimiter:
    """Rate-limit abusive sources without globally locking a targeted account."""

    def __init__(
        self,
        max_failures: int = 5,
        lockout_seconds: int = 30 * 60,
        *,
        source_max_failures: int = 20,
        max_delay_seconds: int = 5,
        time_fn: Callable[[], float] = time.time,
        max_entries: int = 10_000,
        db_path: Optional[Path] = None,
    ) -> None:
        self.max_failures = max(1, max_failures)
        self.source_max_failures = max(1, source_max_failures)
        self.lockout_seconds = max(1, lockout_seconds)
        self.failure_window_seconds = self.lockout_seconds
        self.max_delay_seconds = max(1, max_delay_seconds)
        self.max_entries = max(100, max_entries)
        self._time_fn = time_fn
        configured_path = os.getenv("LOGIN_LIMITER_DB_PATH", "").strip()
        self.db_path = Path(
            db_path
            or configured_path
            or ENV_FILE.parent / ".runtime" / "login_attempts.sqlite3"
        ).expanduser()
        self._lock = threading.Lock()
        self._initialize_database()

    @staticmethod
    def _username_key(username: str) -> str:
        return (username or "").strip().casefold()[:128]

    @staticmethod
    def _source_key(source: str) -> str:
        value = (source or "unknown").strip().casefold()[:128] or "unknown"
        try:
            address = ipaddress.ip_address(value)
        except ValueError:
            return value
        prefix_length = 32 if address.version == 4 else 64
        return str(ipaddress.ip_network(f"{address}/{prefix_length}", strict=False))

    def _keys(self, username: str, source: str) -> Tuple[str, str, str]:
        account = self._username_key(username)
        source_key = self._source_key(source)
        return account, source_key, f"{account}\x1f{source_key}"

    def _initialize_database(self) -> None:
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            os.chmod(self.db_path.parent, 0o700)
        except OSError:
            pass
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS login_attempts (
                    scope TEXT NOT NULL,
                    identifier TEXT NOT NULL,
                    failures INTEGER NOT NULL,
                    last_failure REAL NOT NULL,
                    locked_until REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (scope, identifier)
                )
                """
            )
        try:
            os.chmod(self.db_path, 0o600)
        except OSError:
            pass

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self.db_path), timeout=5)

    @staticmethod
    def _read_state(
        connection: sqlite3.Connection,
        scope: str,
        identifier: str,
    ) -> Optional[Tuple[int, float, float]]:
        return connection.execute(
            """
            SELECT failures, last_failure, locked_until
            FROM login_attempts
            WHERE scope = ? AND identifier = ?
            """,
            (scope, identifier),
        ).fetchone()

    def _active_state(
        self,
        connection: sqlite3.Connection,
        scope: str,
        identifier: str,
        now: float,
    ) -> Tuple[int, float, float]:
        state = self._read_state(connection, scope, identifier)
        if state is None:
            return 0, now, 0.0
        failures, last_failure, locked_until = state
        if locked_until <= now and now - last_failure >= self.failure_window_seconds:
            connection.execute(
                "DELETE FROM login_attempts WHERE scope = ? AND identifier = ?",
                (scope, identifier),
            )
            return 0, now, 0.0
        return failures, last_failure, locked_until

    @staticmethod
    def _write_state(
        connection: sqlite3.Connection,
        scope: str,
        identifier: str,
        failures: int,
        now: float,
        locked_until: float,
    ) -> None:
        connection.execute(
            """
            INSERT INTO login_attempts
                (scope, identifier, failures, last_failure, locked_until)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(scope, identifier) DO UPDATE SET
                failures = excluded.failures,
                last_failure = excluded.last_failure,
                locked_until = excluded.locked_until
            """,
            (scope, identifier, failures, now, locked_until),
        )

    def retry_after(self, username: str, source: str = "unknown") -> int:
        """Return a pair/source lock duration; accounts are never globally locked."""
        _, source_key, pair = self._keys(username, source)
        now = self._time_fn()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            remaining = 0.0
            for scope, identifier in (("pair", pair), ("source", source_key)):
                _, _, locked_until = self._active_state(
                    connection, scope, identifier, now
                )
                remaining = max(remaining, locked_until - now)
            return max(1, math.ceil(remaining)) if remaining > 0 else 0

    def record_failure(self, username: str, source: str = "unknown") -> int:
        """Atomically record account, source, and account/source failure state."""
        account, source_key, pair = self._keys(username, source)
        now = self._time_fn()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            lock_remaining = 0
            dimensions = (
                ("pair", pair, self.max_failures, True),
                ("source", source_key, self.source_max_failures, True),
                ("account", account, 0, False),
            )
            for scope, identifier, threshold, locks in dimensions:
                failures, _, locked_until = self._active_state(
                    connection, scope, identifier, now
                )
                if locked_until > now:
                    lock_remaining = max(
                        lock_remaining, math.ceil(locked_until - now)
                    )
                    continue
                failures += 1
                if locks and failures >= threshold:
                    locked_until = now + self.lockout_seconds
                    lock_remaining = max(lock_remaining, self.lockout_seconds)
                self._write_state(
                    connection,
                    scope,
                    identifier,
                    failures,
                    now,
                    locked_until,
                )
            self._prune(connection, now)
            return lock_remaining

    def failure_delay(self, username: str) -> int:
        """Return a small progressive delay for distributed account failures."""
        account = self._username_key(username)
        now = self._time_fn()
        with self._lock, self._connect() as connection:
            failures, _, _ = self._active_state(
                connection, "account", account, now
            )
        level = failures // self.max_failures
        if level <= 0:
            return 0
        return min(2 ** (level - 1), self.max_delay_seconds)

    def record_success(self, username: str, source: str = "unknown") -> None:
        """Clear account state while preserving failures from the source itself."""
        account, _, pair = self._keys(username, source)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "DELETE FROM login_attempts WHERE scope = ? AND identifier = ?",
                (("account", account), ("pair", pair)),
            )

    def clear(self) -> None:
        """Clear all shared state; intended for tests and controlled maintenance."""
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM login_attempts")

    def _prune(self, connection: sqlite3.Connection, now: float) -> None:
        cutoff = now - self.failure_window_seconds
        connection.execute(
            """
            DELETE FROM login_attempts
            WHERE locked_until <= ? AND last_failure <= ?
            """,
            (now, cutoff),
        )
        count = connection.execute(
            "SELECT COUNT(*) FROM login_attempts"
        ).fetchone()[0]
        excess = count - self.max_entries
        if excess > 0:
            connection.execute(
                """
                DELETE FROM login_attempts
                WHERE rowid IN (
                    SELECT rowid FROM login_attempts
                    WHERE locked_until <= ?
                    ORDER BY last_failure ASC
                    LIMIT ?
                )
                """,
                (now, excess),
            )
