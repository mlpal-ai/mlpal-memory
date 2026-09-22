"""Unit: memory v9 round 3 — bounded retries, the circuit breaker, and the typed failure."""

from __future__ import annotations

import httpx
import pytest

from mlpal_memory_graph.services import resilience
from mlpal_memory_graph.services.metrics import REGISTRY
from mlpal_memory_graph.services.resilience import Breaker, ModelUnavailable, classify, with_retries


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    async def _sleep(_):
        return None

    monkeypatch.setattr(resilience.asyncio, "sleep", _sleep)
    resilience.reset_breakers()


def _status_error(code: int) -> httpx.HTTPStatusError:
    req = httpx.Request("POST", "http://gw/v1/x")
    return httpx.HTTPStatusError("boom", request=req, response=httpx.Response(code, request=req))


def test_classify_names_retryable_failures_only():
    assert classify(httpx.ReadTimeout("t")) == "timeout"
    assert classify(_status_error(503)) == "status_5xx" and classify(_status_error(429)) == "status_429"
    assert classify(httpx.ConnectError("c")) == "transport"
    assert classify(_status_error(400)) is None and classify(ValueError("x")) is None


async def test_retries_then_succeeds_and_counts_the_retries():
    calls = {"n": 0}
    before = REGISTRY.counter("model_retries", client="t")

    async def call():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("slow")
        return "ok"

    assert await with_retries("t", call, attempts=3) == "ok"
    assert calls["n"] == 3 and REGISTRY.counter("model_retries", client="t") - before == 2


async def test_budget_exhausted_is_a_typed_failure_with_the_reason():
    async def call():
        raise _status_error(502)

    with pytest.raises(ModelUnavailable) as e:
        await with_retries("t", call, attempts=2)
    assert e.value.client == "t" and e.value.reason == "status_5xx"


async def test_a_non_retryable_error_fails_at_once():
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        raise _status_error(400)

    with pytest.raises(ModelUnavailable) as e:
        await with_retries("t", call, attempts=3)
    assert calls["n"] == 1 and e.value.reason == "error"


async def test_breaker_opens_after_consecutive_failures_and_fails_fast(monkeypatch):
    b = Breaker("t", failures_to_open=2, open_seconds=30)
    calls = {"n": 0}

    async def call():
        calls["n"] += 1
        raise httpx.ConnectError("down")

    for _ in range(2):
        with pytest.raises(ModelUnavailable):
            await with_retries("t", call, attempts=1, breaker=b)
    assert b.state == "open" and calls["n"] == 2
    with pytest.raises(ModelUnavailable) as e:
        await with_retries("t", call, attempts=1, breaker=b)
    assert e.value.reason == "breaker_open" and calls["n"] == 2, "no call is made while the breaker is open"
    # after the window one probe goes through; a success closes the breaker
    now = {"t": 1000.0}
    monkeypatch.setattr(resilience.time, "monotonic", lambda: now["t"])
    b.opened_at = 900.0
    assert b.state == "half_open"

    async def ok():
        return "up"

    assert await with_retries("t", ok, attempts=1, breaker=b) == "up"
    assert b.state == "closed" and b.failures == 0


def test_half_open_admits_one_probe_at_a_time(monkeypatch):
    now = {"t": 1000.0}
    monkeypatch.setattr(resilience.time, "monotonic", lambda: now["t"])
    b = Breaker("t", failures_to_open=1, open_seconds=10)
    b.record_failure()
    assert b.state == "open" and not b.allow()
    now["t"] += 11
    assert b.allow() and not b.allow(), "the second caller waits for the probe"
    b.record_failure()
    assert b.state == "open", "a failed probe re-opens the window"
