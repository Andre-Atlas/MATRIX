"""Per-project RPM and TPM Rate Limiter for MATRIX Gateway (Layer 2).

Enforces:
- Requests Per Minute (RPM) sliding window
- Tokens Per Minute (TPM) sliding window
- Per-project custom threshold overrides
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProjectUsageWindow:
    requests: deque[float] = field(default_factory=deque)
    tokens: deque[tuple[float, int]] = field(default_factory=deque)

    def prune(self, cutoff: float) -> None:
        while self.requests and self.requests[0] <= cutoff:
            self.requests.popleft()
        while self.tokens and self.tokens[0][0] <= cutoff:
            self.tokens.popleft()

    def current_rpm(self, cutoff: float) -> int:
        self.prune(cutoff)
        return len(self.requests)

    def current_tpm(self, cutoff: float) -> int:
        self.prune(cutoff)
        return sum(item[1] for item in self.tokens)


@dataclass
class GatewayRateLimiter:
    default_rpm: int = 60
    default_tpm: int = 100_000
    window_seconds: float = 60.0

    _project_limits: dict[str, tuple[int, int]] = field(default_factory=dict)  # project_id -> (rpm, tpm)
    _usage: dict[str, ProjectUsageWindow] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def set_project_limits(self, project_id: str, rpm: int, tpm: int) -> None:
        """Sets custom RPM and TPM limits for a project."""
        with self._lock:
            self._project_limits[project_id] = (rpm, tpm)

    def get_limits(self, project_id: str) -> tuple[int, int]:
        """Gets (rpm, tpm) limits for a project."""
        with self._lock:
            return self._project_limits.get(project_id, (self.default_rpm, self.default_tpm))

    def check_rate_limit(self, project_id: str, estimated_tokens: int = 0) -> tuple[bool, str | None]:
        """Checks if a request can proceed under the project's RPM and TPM limits.

        Returns (allowed, reason_if_blocked).
        """
        now = time.monotonic()
        cutoff = now - self.window_seconds
        rpm_limit, tpm_limit = self.get_limits(project_id)

        with self._lock:
            window = self._usage.setdefault(project_id, ProjectUsageWindow())
            current_rpm = window.current_rpm(cutoff)
            current_tpm = window.current_tpm(cutoff)

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

    def record_request(self, project_id: str, tokens: int = 0) -> None:
        """Records a completed request and token usage."""
        now = time.monotonic()
        cutoff = now - self.window_seconds

        with self._lock:
            window = self._usage.setdefault(project_id, ProjectUsageWindow())
            window.prune(cutoff)
            window.requests.append(now)
            if tokens > 0:
                window.tokens.append((now, tokens))

    def get_usage(self, project_id: str) -> dict[str, Any]:
        """Returns the current usage stats and remaining quotas for a project."""
        now = time.monotonic()
        cutoff = now - self.window_seconds
        rpm_limit, tpm_limit = self.get_limits(project_id)

        with self._lock:
            window = self._usage.setdefault(project_id, ProjectUsageWindow())
            current_rpm = window.current_rpm(cutoff)
            current_tpm = window.current_tpm(cutoff)

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
            self._usage.clear()
            self._project_limits.clear()
