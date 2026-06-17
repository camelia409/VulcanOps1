"""
Lightweight circuit breaker for the OpenRouter LLM gateway.

No external dependencies. Supports three states:

    CLOSED   → normal operation
    OPEN     → fast-fail, no LLM calls
    HALF_OPEN→ allow one probe request

Configuration:
    MAX_FAILURES     = 3
    COOLDOWN_SECONDS = 60
    TIMEOUT_SECONDS  = 45
"""

import asyncio
import json
import logging
import time
from enum import Enum
from typing import Any, Callable

logger = logging.getLogger("vulcanops.pipeline")


class CircuitBreakerState(str, Enum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class CircuitBreakerOpen(Exception):
    """Raised when a request is rejected because the circuit is OPEN."""


class CircuitBreaker:
    """
    In-process circuit breaker.

    Thread-safe for the async single-threaded event loop via asyncio.Lock.
    """

    MAX_FAILURES = 3
    COOLDOWN_SECONDS = 60
    TIMEOUT_SECONDS = 45

    def __init__(self) -> None:
        self.state = CircuitBreakerState.CLOSED
        self.failure_count = 0
        self.last_failure_time = 0.0
        self._lock = asyncio.Lock()

    def _log_transition(self, state: CircuitBreakerState, reason: str | None = None) -> None:
        payload: dict[str, Any] = {"event": "circuit_breaker", "state": state.value}
        if reason:
            payload["reason"] = reason
        logger.info(json.dumps(payload))

    async def execute(
        self,
        fn: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute `fn` if the circuit allows it.

        - CLOSED: runs normally; resets failure counter on success.
        - OPEN : rejects immediately until cooldown expires, then moves to HALF_OPEN.
        - HALF_OPEN: allows one probe; success closes, failure re-opens.

        Any exception (including TIMEOUT_SECONDS expiry) counts as a failure.
        """
        async with self._lock:
            if self.state == CircuitBreakerState.OPEN:
                if time.monotonic() - self.last_failure_time >= self.COOLDOWN_SECONDS:
                    self.state = CircuitBreakerState.HALF_OPEN
                    self._log_transition(CircuitBreakerState.HALF_OPEN)
                else:
                    raise CircuitBreakerOpen("Circuit breaker is OPEN")

            # CLOSED or HALF_OPEN: attempt the call
            try:
                result = await asyncio.wait_for(
                    fn(*args, **kwargs),
                    timeout=self.TIMEOUT_SECONDS,
                )
            except Exception:
                self.failure_count += 1
                self.last_failure_time = time.monotonic()

                if self.state == CircuitBreakerState.HALF_OPEN:
                    self.state = CircuitBreakerState.OPEN
                    self._log_transition(
                        CircuitBreakerState.OPEN, reason="probe failed"
                    )
                elif self.failure_count >= self.MAX_FAILURES:
                    self.state = CircuitBreakerState.OPEN
                    self._log_transition(
                        CircuitBreakerState.OPEN,
                        reason=f"{self.failure_count} consecutive failures",
                    )
                raise

            # Success path
            if self.state != CircuitBreakerState.CLOSED:
                self._log_transition(CircuitBreakerState.CLOSED)
            self.state = CircuitBreakerState.CLOSED
            self.failure_count = 0
            self.last_failure_time = 0.0
            return result
