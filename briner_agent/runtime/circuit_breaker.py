"""
briner_agent/runtime/circuit_breaker.py

Three-state circuit breaker: CLOSED → OPEN → HALF_OPEN → CLOSED.

CLOSED:     All calls pass through. Failure counter increments on each failure.
OPEN:       All calls are rejected immediately (no actual LLM call made).
            Transitions to HALF_OPEN after recovery_seconds.
HALF_OPEN:  A single probe call is allowed. Success → CLOSED. Failure → OPEN.

Thread-safe. No external dependencies.
"""

import logging
import threading
import time
from enum import Enum

logger = logging.getLogger(__name__)


class CircuitState(Enum):
    CLOSED    = "closed"
    OPEN      = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(Exception):
    """Raised when a call is blocked by an open circuit breaker."""


class CircuitBreaker:
    def __init__(
        self,
        name: str = "default",
        failure_threshold: int = 3,
        recovery_seconds: float = 60.0,
    ):
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_seconds = recovery_seconds

        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._opened_at: float | None = None
        self._lock = threading.Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._check_transition()

    def _check_transition(self) -> CircuitState:
        """Call only while holding self._lock."""
        if self._state == CircuitState.OPEN:
            if self._opened_at and (time.monotonic() - self._opened_at) >= self.recovery_seconds:
                self._state = CircuitState.HALF_OPEN
                logger.info("[CircuitBreaker:%s] OPEN → HALF_OPEN (probe allowed)", self.name)
        return self._state

    def is_open(self) -> bool:
        with self._lock:
            state = self._check_transition()
            return state == CircuitState.OPEN

    def before_call(self):
        """
        Call before attempting an LLM operation.
        Raises CircuitOpenError if the circuit is OPEN.
        """
        with self._lock:
            state = self._check_transition()
            if state == CircuitState.OPEN:
                raise CircuitOpenError(
                    f"[CircuitBreaker:{self.name}] Circuit is OPEN. "
                    f"Retry after {self.recovery_seconds:.0f}s."
                )

    def record_success(self):
        with self._lock:
            prev = self._state
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._opened_at = None
            if prev != CircuitState.CLOSED:
                logger.info("[CircuitBreaker:%s] %s → CLOSED (success)", self.name, prev.value.upper())

    def record_failure(self, reason: str = ""):
        with self._lock:
            self._failure_count += 1
            logger.warning(
                "[CircuitBreaker:%s] failure %d/%d: %s",
                self.name, self._failure_count, self.failure_threshold, reason,
            )
            if self._failure_count >= self.failure_threshold and self._state == CircuitState.CLOSED:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.error(
                    "[CircuitBreaker:%s] CLOSED → OPEN after %d failures",
                    self.name, self._failure_count,
                )
            elif self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                logger.error("[CircuitBreaker:%s] HALF_OPEN → OPEN (probe failed)", self.name)

    def status_dict(self) -> dict:
        with self._lock:
            state = self._check_transition()
            return {
                "state": state.value,
                "failure_count": self._failure_count,
                "opened_at": self._opened_at,
                "recovery_seconds": self.recovery_seconds,
            }
