"""Per-project RPM and TPM Rate Limiter for MATRIX Gateway (Layer 2 / T8).

Enforces:
- Requests Per Minute (RPM) fixed 60-second window
- Tokens Per Minute (TPM) fixed 60-second window
- Shared persistent state via `gateway_rate_windows` (atomic Postgres / SQLite upsert)
  when `session_factory` is provided, with in-memory fallback for standalone unit tests
- Check-and-reserve before calling the provider, then reconcile actual tokens afterward
- Per-project custom threshold overrides
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session, sessionmaker

from matrix_server.gateway.models import GatewayRateWindow


def _current_window_start(window_seconds: float, now: float | None = None) -> int:
    ts = int(now if now is not None else time.time())
    w = max(1, int(window_seconds))
    return (ts // w) * w


@dataclass
class _MemoryWindow:
    window_start: int
    request_count: int = 0
    token_count: int = 0


@dataclass
class GatewayRateLimiter:
    default_rpm: int = 60
    default_tpm: int = 100_000
    window_seconds: float = 60.0
    session_factory: sessionmaker[Session] | None = None
    window_retention_seconds: int = 3600
    cleanup_interval: int = 100
    cleanup_batch_size: int = 500

    _project_limits: dict[str, tuple[int, int]] = field(default_factory=dict)  # project_id -> (rpm, tpm)
    _memory_windows: dict[tuple[str, str], _MemoryWindow] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _op_counter: int = 0

    def set_project_limits(self, project_id: str, rpm: int, tpm: int) -> None:
        """Sets custom RPM and TPM limits for a project."""
        with self._lock:
            self._project_limits[project_id] = (rpm, tpm)

    def get_limits(self, project_id: str) -> tuple[int, int]:
        """Gets (rpm, tpm) limits for a project."""
        with self._lock:
            return self._project_limits.get(project_id, (self.default_rpm, self.default_tpm))

    def acquire(
        self,
        project_id: str,
        estimated_tokens: int = 0,
        scope: str = "default",
    ) -> tuple[bool, str | None, int]:
        """Atomically checks and reserves 1 request and `estimated_tokens` in the current 60s window.

        Must be called BEFORE dispatching to an LLM provider.
        Returns `(allowed, reason_if_blocked, window_start)`.
        """
        est = max(0, int(estimated_tokens))
        window_start = _current_window_start(self.window_seconds)
        rpm_limit, tpm_limit = self.get_limits(project_id)

        if self.session_factory is not None:
            return self._acquire_db(project_id, scope, window_start, est, rpm_limit, tpm_limit)
        return self._acquire_memory(project_id, scope, window_start, est, rpm_limit, tpm_limit)

    def _acquire_db(
        self,
        project_id: str,
        scope: str,
        window_start: int,
        estimated_tokens: int,
        rpm_limit: int,
        tpm_limit: int,
    ) -> tuple[bool, str | None, int]:
        assert self.session_factory is not None
        with self.session_factory() as db:
            dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
            insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert

            stmt = (
                insert_fn(GatewayRateWindow)
                .values(
                    project_id=project_id,
                    scope=scope,
                    window_start=window_start,
                    request_count=1,
                    token_count=estimated_tokens,
                )
                .on_conflict_do_update(
                    index_elements=["project_id", "scope", "window_start"],
                    set_={
                        "request_count": GatewayRateWindow.request_count + 1,
                        "token_count": GatewayRateWindow.token_count + estimated_tokens,
                    },
                )
                .returning(GatewayRateWindow.request_count, GatewayRateWindow.token_count)
            )
            row = db.execute(stmt).one()
            new_rpm, new_tpm = int(row[0]), int(row[1])

            if new_rpm > rpm_limit:
                db.rollback()
                return (
                    False,
                    f"RPM limit of {rpm_limit} exceeded for project {project_id} (current: {new_rpm - 1})",
                    window_start,
                )
            if new_tpm > tpm_limit:
                db.rollback()
                return (
                    False,
                    (
                        f"TPM limit of {tpm_limit} exceeded for project {project_id} "
                        f"(current: {new_tpm - estimated_tokens}, requested: {estimated_tokens})"
                    ),
                    window_start,
                )

            db.commit()

        self._maybe_cleanup_db(window_start)
        return True, None, window_start

    def _acquire_memory(
        self,
        project_id: str,
        scope: str,
        window_start: int,
        estimated_tokens: int,
        rpm_limit: int,
        tpm_limit: int,
    ) -> tuple[bool, str | None, int]:
        key = (project_id, scope)
        with self._lock:
            win = self._memory_windows.get(key)
            if win is None or win.window_start != window_start:
                win = _MemoryWindow(window_start=window_start)
                self._memory_windows[key] = win

            if win.request_count >= rpm_limit:
                return (
                    False,
                    f"RPM limit of {rpm_limit} exceeded for project {project_id} (current: {win.request_count})",
                    window_start,
                )
            if (win.token_count + estimated_tokens) > tpm_limit:
                return (
                    False,
                    (
                        f"TPM limit of {tpm_limit} exceeded for project {project_id} "
                        f"(current: {win.token_count}, requested: {estimated_tokens})"
                    ),
                    window_start,
                )

            win.request_count += 1
            win.token_count += estimated_tokens
            return True, None, window_start

    def reconcile_tokens(
        self,
        project_id: str,
        window_start: int,
        estimated_tokens: int,
        actual_tokens: int,
        scope: str = "default",
    ) -> None:
        """Adjusts the window's token count after provider execution with actual usage."""
        delta = max(0, int(actual_tokens)) - max(0, int(estimated_tokens))
        if delta == 0:
            return

        if self.session_factory is not None:
            with self.session_factory() as db:
                row = db.scalar(
                    select(GatewayRateWindow).where(
                        GatewayRateWindow.project_id == project_id,
                        GatewayRateWindow.scope == scope,
                        GatewayRateWindow.window_start == window_start,
                    )
                )
                if row is not None:
                    new_total = max(0, int(row.token_count) + delta)
                    db.execute(
                        update(GatewayRateWindow)
                        .where(
                            GatewayRateWindow.project_id == project_id,
                            GatewayRateWindow.scope == scope,
                            GatewayRateWindow.window_start == window_start,
                        )
                        .values(token_count=new_total)
                    )
                    db.commit()
            return

        key = (project_id, scope)
        with self._lock:
            win = self._memory_windows.get(key)
            if win is not None and win.window_start == window_start:
                win.token_count = max(0, win.token_count + delta)

    def _maybe_cleanup_db(self, current_window_start: int) -> None:
        if self.session_factory is None:
            return
        with self._lock:
            self._op_counter += 1
            should_clean = (self._op_counter % max(1, self.cleanup_interval)) == 0
        if not should_clean:
            return
        self.cleanup_expired_windows(current_window_start)

    def cleanup_expired_windows(self, current_window_start: int | None = None) -> int:
        """Bounded cleanup of expired rate-limit window rows."""
        if self.session_factory is None:
            return 0
        now_win = (
            current_window_start
            if current_window_start is not None
            else _current_window_start(self.window_seconds)
        )
        cutoff = now_win - self.window_retention_seconds
        with self.session_factory() as db:
            stale_keys = db.execute(
                select(
                    GatewayRateWindow.project_id,
                    GatewayRateWindow.scope,
                    GatewayRateWindow.window_start,
                )
                .where(GatewayRateWindow.window_start < cutoff)
                .limit(self.cleanup_batch_size)
            ).all()
            if not stale_keys:
                return 0
            deleted = 0
            for proj_id, sc, ws in stale_keys:
                res = db.execute(
                    delete(GatewayRateWindow).where(
                        GatewayRateWindow.project_id == proj_id,
                        GatewayRateWindow.scope == sc,
                        GatewayRateWindow.window_start == ws,
                    )
                )
                deleted += res.rowcount or 0
            db.commit()
            return deleted

    def check_rate_limit(
        self,
        project_id: str,
        estimated_tokens: int = 0,
        scope: str = "default",
    ) -> tuple[bool, str | None]:
        """Non-mutating check of whether a request can proceed under the project's RPM and TPM limits."""
        usage = self.get_usage(project_id, scope=scope)
        rpm_limit = usage["rpm_limit"]
        tpm_limit = usage["tpm_limit"]
        current_rpm = usage["rpm_current"]
        current_tpm = usage["tpm_current"]

        if current_rpm >= rpm_limit:
            return (
                False,
                f"RPM limit of {rpm_limit} exceeded for project {project_id} (current: {current_rpm})",
            )
        if (current_tpm + estimated_tokens) > tpm_limit:
            return (
                False,
                (
                    f"TPM limit of {tpm_limit} exceeded for project {project_id} "
                    f"(current: {current_tpm}, requested: {estimated_tokens})"
                ),
            )
        return True, None

    def record_request(self, project_id: str, tokens: int = 0, scope: str = "default") -> None:
        """Records a request and token usage in the current fixed window."""
        window_start = _current_window_start(self.window_seconds)
        tok = max(0, int(tokens))
        if self.session_factory is not None:
            with self.session_factory() as db:
                dialect = db.bind.dialect.name if db.bind is not None else "sqlite"
                insert_fn = pg_insert if dialect == "postgresql" else sqlite_insert
                stmt = (
                    insert_fn(GatewayRateWindow)
                    .values(
                        project_id=project_id,
                        scope=scope,
                        window_start=window_start,
                        request_count=1,
                        token_count=tok,
                    )
                    .on_conflict_do_update(
                        index_elements=["project_id", "scope", "window_start"],
                        set_={
                            "request_count": GatewayRateWindow.request_count + 1,
                            "token_count": GatewayRateWindow.token_count + tok,
                        },
                    )
                )
                db.execute(stmt)
                db.commit()
            return

        key = (project_id, scope)
        with self._lock:
            win = self._memory_windows.get(key)
            if win is None or win.window_start != window_start:
                win = _MemoryWindow(window_start=window_start)
                self._memory_windows[key] = win
            win.request_count += 1
            win.token_count += tok

    def get_usage(self, project_id: str, scope: str = "default") -> dict[str, Any]:
        """Returns the current usage stats and remaining quotas for a project."""
        window_start = _current_window_start(self.window_seconds)
        rpm_limit, tpm_limit = self.get_limits(project_id)

        if self.session_factory is not None:
            with self.session_factory() as db:
                row = db.scalar(
                    select(GatewayRateWindow).where(
                        GatewayRateWindow.project_id == project_id,
                        GatewayRateWindow.scope == scope,
                        GatewayRateWindow.window_start == window_start,
                    )
                )
                current_rpm = int(row.request_count) if row is not None else 0
                current_tpm = int(row.token_count) if row is not None else 0
        else:
            key = (project_id, scope)
            with self._lock:
                win = self._memory_windows.get(key)
                if win is None or win.window_start != window_start:
                    current_rpm = 0
                    current_tpm = 0
                else:
                    current_rpm = win.request_count
                    current_tpm = win.token_count

        return {
            "project_id": project_id,
            "rpm_current": current_rpm,
            "rpm_limit": rpm_limit,
            "rpm_remaining": max(0, rpm_limit - current_rpm),
            "tpm_current": current_tpm,
            "tpm_limit": tpm_limit,
            "tpm_remaining": max(0, tpm_limit - current_tpm),
        }

    def reset(self) -> None:
        """Resets all usage counters and project limit overrides."""
        with self._lock:
            self._memory_windows.clear()
            self._project_limits.clear()
        if self.session_factory is not None:
            with self.session_factory() as db:
                db.execute(delete(GatewayRateWindow))
                db.commit()
