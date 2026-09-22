"""memory v9 round 3: bounded retries and a circuit breaker for the model clients.

The gateway embedder and the LLM client used to make one attempt with a fixed timeout and raise
whatever httpx raised; a blip became a failed ingest (the harness counted them as errors) and an
outage became one slow failure per request. Now:

- `with_retries` retries a call on timeouts, transport errors, 429 and 5xx with jittered
  exponential backoff, a bounded number of attempts, and one structured log line per retry.
- `Breaker` opens after N consecutive failures and fails fast for a while, then lets one call
  through (half-open); a success closes it. One breaker per client, process-wide.
- Every failure is a typed `ModelUnavailable(client, reason)` so callers can degrade on purpose:
  search drops to its lexical leg, an ingest is queued for the worker, the worker defers instead of
  burning an episode's retry budget.
- Counters: `memory_model_call_failures_total{client,reason}`, `memory_model_retries_total{client}`,
  `memory_breaker_open_total{client}` — through the in-process metrics registry (`/metrics`).
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable
from typing import TypeVar

import httpx

from ..core.logging import get_logger
from .metrics import REGISTRY as registry

log = get_logger(__name__)
T = TypeVar("T")

RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class ModelUnavailable(RuntimeError):
    """A model client could not complete a call within its retry budget, or its breaker is open."""

    def __init__(self, client: str, reason: str, detail: str = "") -> None:
        super().__init__(f"{client} unavailable: {reason}{(' — ' + detail) if detail else ''}")
        self.client = client
        self.reason = reason


def classify(exc: BaseException) -> str | None:
    """A retryable failure's short reason, or None when the failure is not retryable."""
    if isinstance(exc, httpx.TimeoutException):
        return "timeout"
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        if code == 429:
            return "status_429"
        if code in RETRYABLE_STATUS:
            return "status_5xx"
        return None
    if isinstance(exc, httpx.TransportError):
        return "transport"
    return None


class Breaker:
    """Consecutive-failure circuit breaker. Not thread-safe by design: one asyncio loop per process."""

    def __init__(self, name: str, failures_to_open: int = 5, open_seconds: float = 30.0) -> None:
        self.name = name
        self.failures_to_open = max(1, failures_to_open)
        self.open_seconds = open_seconds
        self.failures = 0
        self.opened_at: float | None = None
        self._half_open_inflight = False

    @property
    def state(self) -> str:
        if self.opened_at is None:
            return "closed"
        if time.monotonic() - self.opened_at >= self.open_seconds:
            return "half_open"
        return "open"

    def allow(self) -> bool:
        st = self.state
        if st == "closed":
            return True
        if st == "half_open" and not self._half_open_inflight:
            self._half_open_inflight = True  # one probe at a time
            return True
        return False

    def record_success(self) -> None:
        if self.opened_at is not None:
            log.info("model.breaker_closed", client=self.name)
        self.failures = 0
        self.opened_at = None
        self._half_open_inflight = False

    def record_failure(self) -> None:
        self.failures += 1
        self._half_open_inflight = False
        if self.opened_at is None and self.failures >= self.failures_to_open:
            self.opened_at = time.monotonic()
            registry.inc("breaker_open", client=self.name)
            log.error("model.breaker_opened", client=self.name, failures=self.failures, open_seconds=self.open_seconds)
        elif self.opened_at is not None:
            self.opened_at = time.monotonic()  # a failed probe re-opens for another window


_breakers: dict[str, Breaker] = {}


def breaker_for(client: str, failures_to_open: int, open_seconds: float) -> Breaker:
    b = _breakers.get(client)
    if b is None:
        b = _breakers[client] = Breaker(client, failures_to_open, open_seconds)
    return b


def reset_breakers() -> None:  # tests
    _breakers.clear()


async def with_retries(client: str, call: Callable[[], Awaitable[T]], *, attempts: int = 3, base_delay: float = 0.5,
                       max_delay: float = 8.0, breaker: Breaker | None = None) -> T:
    """Run `call` with bounded retries on retryable failures. Raises `ModelUnavailable` when the
    budget is exhausted, the failure is not retryable, or the breaker is open."""
    if breaker is not None and not breaker.allow():
        registry.inc("model_call_failures", client=client, reason="breaker_open")
        raise ModelUnavailable(client, "breaker_open")
    last: BaseException | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            out = await call()
        except Exception as exc:  # noqa: BLE001 — classified below
            reason = classify(exc)
            last = exc
            if reason is None:
                if breaker is not None:
                    breaker.record_failure()
                registry.inc("model_call_failures", client=client, reason="error")
                log.error("model.call_failed", client=client, attempt=attempt, reason="error", error=str(exc)[:200])
                raise ModelUnavailable(client, "error", str(exc)[:200]) from exc
            if attempt >= attempts:
                if breaker is not None:
                    breaker.record_failure()
                registry.inc("model_call_failures", client=client, reason=reason)
                log.error("model.call_failed", client=client, attempt=attempt, reason=reason, error=str(exc)[:200])
                raise ModelUnavailable(client, reason, str(exc)[:200]) from exc
            delay = min(max_delay, base_delay * (2 ** (attempt - 1))) * (0.5 + random.random())  # noqa: S311 — jitter, not security
            registry.inc("model_retries", client=client)
            log.warning("model.call_retry", client=client, attempt=attempt, reason=reason, sleep_s=round(delay, 2))
            await asyncio.sleep(delay)
        else:
            if breaker is not None:
                breaker.record_success()
            return out
    raise ModelUnavailable(client, "exhausted", str(last)[:200] if last else "")  # pragma: no cover — loop always returns or raises
