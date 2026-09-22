"""Integration (SQLite, dev auth): memory v9 round 3 — the service under model outages and load.

- the gateway embedder retries a flaky gateway and gives up with a typed failure
- search degrades to its lexical leg when the embedder is down, and says so
- a synchronous ingest past the concurrency bound is refused at once with Retry-After
- an ingest during a model outage is queued for the worker, not failed
- the worker defers on a model outage instead of spending the episode's retry budget
"""

from __future__ import annotations

import httpx
import pytest
from sqlalchemy import select

from mlpal_memory_graph.core.config import get_settings
from mlpal_memory_graph.db import get_session_factory
from mlpal_memory_graph.db.models import Episode
from mlpal_memory_graph.services import resilience
from mlpal_memory_graph.services.embeddings_client import AssistantsEmbedder, get_embedder
from mlpal_memory_graph.services.metrics import REGISTRY
from mlpal_memory_graph.services.resilience import ModelUnavailable

H = {"X-Test-Org-Id": "orgH", "X-Test-User-Id": "alice", "X-Test-Permissions": "memory.read,memory.write"}
DOC = {"title": "session 2023-05-20", "content": "user: I redeemed a five dollar coupon on coffee creamer at Target last Sunday.\n"
                                                "assistant: Nice, coupons add up.", "source": "bench", "uri": "s1", "workspace": "bench", "scope": "org"}


@pytest.fixture(autouse=True)
def _fast(monkeypatch):
    async def _sleep(_):
        return None

    monkeypatch.setattr(resilience.asyncio, "sleep", _sleep)
    resilience.reset_breakers()
    from mlpal_memory_graph.api.v1 import documents

    documents.reset_ingest_slots()
    yield
    documents.reset_ingest_slots()


async def test_gateway_embedder_retries_then_returns_and_gives_up_typed():
    hits = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        if hits["n"] < 3:
            return httpx.Response(503, json={"error": "busy"})
        return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2]}]})

    emb = AssistantsEmbedder("http://gw", "text-embedding-3-small", 2, api_key="mlpal_x")
    emb._transport = httpx.MockTransport(handler)
    assert await emb.embed(["hello"]) == [[0.1, 0.2]] and hits["n"] == 3

    emb._transport = httpx.MockTransport(lambda r: httpx.Response(503, json={"error": "down"}))
    with pytest.raises(ModelUnavailable) as e:
        await emb.embed(["hello"])
    assert e.value.client == "embeddings" and e.value.reason == "status_5xx"


async def test_search_degrades_to_lexical_when_the_embedder_is_down(client, monkeypatch):
    r = await client.post("/api/v1/documents", json=DOC, headers=H)
    assert r.status_code == 202 and r.json()["status"] == "processed"

    async def broken(text):
        raise ModelUnavailable("embeddings", "breaker_open")

    # the instances the app holds (another test may have swapped the cached embedder's class)
    from mlpal_memory_graph.api import deps

    ret = deps.get_retrieval()
    for emb in {id(e): e for e in (ret.embedder, ret.direct.embedder, deps.get_updater().direct.embedder, get_embedder())}.values():
        monkeypatch.setattr(emb, "embed_one", broken)
    before = REGISTRY.counter("search_degraded", tier="direct", leg="vector")
    r = await client.get("/api/v1/memory/search", params={"q": "coffee creamer coupon", "workspace": "bench", "workspace_mode": "filter"}, headers=H)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["degraded"] == ["vector"], body.get("degraded")
    assert any("coffee creamer" in p["content"] for p in body["passages"]), "the lexical leg still answers"
    assert REGISTRY.counter("search_degraded", tier="direct", leg="vector") == before + 1


async def test_ingest_past_the_bound_is_refused_at_once_with_retry_after(client, monkeypatch):
    from mlpal_memory_graph.api.v1 import documents

    monkeypatch.setattr(get_settings(), "ingest_concurrency", 1)
    documents.reset_ingest_slots()
    slots = documents.ingest_slots()
    await slots.acquire()  # someone else's fold is in flight
    try:
        r = await client.post("/api/v1/documents", json={**DOC, "uri": "s2"}, headers=H)
        assert r.status_code == 503 and r.headers.get("retry-after") == "2", r.text
        assert REGISTRY.counter("ingest_rejected", reason="saturated") >= 1
    finally:
        slots.release()
    # the document was stored for the worker (processed=false), not lost
    async with get_session_factory()() as s:
        rows = (await s.execute(select(Episode).where(Episode.org_id == "orgH", Episode.processed.is_(False)))).scalars().all()
    assert len(rows) == 1
    r = await client.post("/api/v1/documents", json={**DOC, "uri": "s3"}, headers=H)
    assert r.status_code == 202, "the slot is free again"


async def test_ingest_during_a_model_outage_is_queued_not_failed(client, monkeypatch):
    from mlpal_memory_graph.api import deps

    async def outage(session, episode):
        raise ModelUnavailable("llm", "breaker_open")

    monkeypatch.setattr(deps.get_updater(), "process_episode", outage)
    r = await client.post("/api/v1/documents", json={**DOC, "uri": "s4", "event_id": "ev-queued-1"}, headers=H)
    assert r.status_code == 202 and r.json()["status"] == "queued", r.text
    async with get_session_factory()() as s:
        ep = await s.get(Episode, "ev-queued-1")
    assert ep is not None and ep.processed is False and ep.dead_at is None and (ep.error_count or 0) == 0


async def test_worker_defers_on_a_model_outage_without_spending_the_retry_budget(client, monkeypatch):
    from mlpal_memory_graph.api import deps
    from mlpal_memory_graph.services.worker import MemoryUpdateWorker

    async def outage(session, episode):
        raise ModelUnavailable("llm", "timeout")

    monkeypatch.setattr(deps.get_updater(), "process_episode", outage)
    r = await client.post("/api/v1/documents", json={**DOC, "uri": "s5", "event_id": "ev-defer-1"}, headers=H)
    assert r.json()["status"] == "queued"
    w = MemoryUpdateWorker()
    w.updater = deps.get_updater()
    with pytest.raises(ModelUnavailable):
        await w._process_one(get_session_factory(), "ev-defer-1")
    assert w.deferred == 1
    async with get_session_factory()() as s:
        ep = await s.get(Episode, "ev-defer-1")
    assert (ep.error_count or 0) == 0 and ep.dead_at is None and ep.processed is False


async def test_metrics_render_the_new_counters(client):
    REGISTRY.inc("model_call_failures", client="llm", reason="timeout")
    r = await client.get("/metrics")
    assert r.status_code == 200 and 'memory_model_call_failures_total{client="llm",reason="timeout"}' in r.text
