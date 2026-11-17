"""Circuit Breaker implementation for LLM Providers (MATRIX Gateway Layer 2).

Maintains provider health states:
- CLOSED: Provider is healthy, requests flow normally.
- OPEN: Provider has failed repeatedly, requests are blocked until cooldown expires.
- HALF_OPEN: Cooldown has passed, trial requests test if provider has recovered.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from enum import Enum
from typing import Any, TypeVar

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpenError(RuntimeError):
    """Raised when an operation is attempted while the circuit breaker is OPEN."""

    def __init__(self, name: str, retry_after: float):
        super().__init__(f"Circuit breaker '{name}' is OPEN. Retry after {retry_after:.1f}s.")
        self.name = name
        self.retry_after = retry_after


@dataclass
class CircuitBreaker:
    name: str = "default"
    failure_threshold: int = 3
    cooldown_seconds: float = 30.0
    success_threshold: int = 1

    _state: CircuitState = CircuitState.CLOSED
    _failure_count: int = 0
    _success_count: int = 0
    _last_failure_time: float | None = None
    _last_state_change: float = 0.0

    def __post_init__(self) -> None:
        self._last_state_change = time.monotonic()

    @property
    def state(self) -> str:
        """Returns the current state, dynamically transitioning from OPEN to HALF_OPEN if cooldown elapsed."""
        now = time.monotonic()
        if (
            self._state == CircuitState.OPEN
            and self._last_failure_time is not None
            and (now - self._last_failure_time) >= self.cooldown_seconds
        ):
            self._state = CircuitState.HALF_OPEN
            self._success_count = 0
            self._last_state_change = now
        return self._state.value

    @property
    def failure_count(self) -> int:
        return self._failure_count

    @property
    def last_failure_time(self) -> float | None:
        return self._last_failure_time

    def can_execute(self) -> bool:
        """Checks if a request is allowed through the circuit breaker."""
        return self.state in (CircuitState.CLOSED.value, CircuitState.HALF_OPEN.value)

    def record_success(self) -> None:
        """Records a successful operation."""
        # Ensure state is updated if cooldown passed
        current = self.state
        if current == CircuitState.HALF_OPEN.value:
            self._success_count += 1
            if self._success_count >= self.success_threshold:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                self._success_count = 0
                self._last_state_change = time.monotonic()
        elif current == CircuitState.CLOSED.value:
            self._failure_count = 0

    def record_failure(self) -> None:
        """Records a failed operation and trips the breaker if threshold is exceeded."""
        now = time.monotonic()
        self._last_failure_time = now
        self._failure_count += 1

        if self._state == CircuitState.HALF_OPEN:
            # Any failure in HALF_OPEN immediately trips back to OPEN
            self._state = CircuitState.OPEN
            self._success_count = 0
            self._last_state_change = now
        elif self._failure_count >= self.failure_threshold:
            self._state = CircuitState.OPEN
            self._last_state_change = now

    def reset(self) -> None:
        """Resets the circuit breaker to clean CLOSED state."""
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time = None
        self._last_state_change = time.monotonic()

    def get_remaining_cooldown(self) -> float:
        """Returns remaining cooldown in seconds if OPEN, or 0.0."""
        if self.state != CircuitState.OPEN.value or self._last_failure_time is None:
            return 0.0
        elapsed = time.monotonic() - self._last_failure_time
        return max(0.0, self.cooldown_seconds - elapsed)

    async def call(self, func: Callable[..., Coroutine[Any, Any, T]], *args: Any, **kwargs: Any) -> T:
        """Executes an async function governed by this circuit breaker."""
        if not self.can_execute():
            raise CircuitBreakerOpenError(self.name, self.get_remaining_cooldown())

        try:
            result = await func(*args, **kwargs)
            self.record_success()
            return result
        except Exception:
            self.record_failure()
            raise

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "state": self.state,
            "failure_count": self._failure_count,
            "cooldown_seconds": self.cooldown_seconds,
            "failure_threshold": self.failure_threshold,
            "last_failure_time": self._last_failure_time,
        }
