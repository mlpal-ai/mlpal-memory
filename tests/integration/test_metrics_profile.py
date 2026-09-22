"""memory v7 WP11: every read reports its latency, /metrics exposes histograms per route, the profile
returns stable facts and recent activity in one call, and ?fusion=rrf ranks both tiers together."""

from __future__ import annotations

import pytest

H = {"X-Test-Org-Id": "orgM", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}


async def _claim(client, kind, topic, key, value, scope="org"):
    env = {"scope": scope, "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "priya"}, "content": str(value),
           "payload": {"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": ["c1"], "hop": "infra@0.3.0"}}
    if scope == "user":
        env["scope_id"] = "priya"
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_reads_report_latency_and_metrics_expose_histograms(client):
    await _claim(client, "state", "infra/state/cost-daily", "2026-09-17", "sent; $120")
    r = await client.get("/api/v1/memory/search", params={"q": "cost daily 2026-09-17", "limit": 5}, headers=H)
    assert r.status_code == 200 and isinstance(r.json()["took_ms"], int) and "X-Took-Ms" in r.headers
    r = await client.get("/api/v1/memory/projection", params={"hop": "infra", "inject": "infra/state/cost-daily"}, headers=H)
    assert r.status_code == 200 and isinstance(r.json()["took_ms"], int)
    m = await client.get("/metrics")
    assert m.status_code == 200
    body = m.text
    assert 'memory_http_request_duration_ms_bucket{route="/api/v1/memory/search"' in body
    assert 'memory_http_requests_total{route="/api/v1/memory/projection"' in body
    from mlpal_memory_graph.services.metrics import REGISTRY
    snap = REGISTRY.snapshot()
    assert snap["/api/v1/memory/search"]["count"] >= 1 and snap["/api/v1/memory/search"]["p50_ms"] is not None


@pytest.mark.asyncio
async def test_profile_returns_stable_facts_and_recent_activity(client):
    await _claim(client, "preference", "person/{me}/pref/infra", "format", "one plain mail", scope="user")
    await _claim(client, "state", "infra/state/identity", "account", "123456789012")
    await _claim(client, "learning", "infra/learning", "dedup", "Pass --context=mlpal-new-eks to kubectl; the default points at the old account.")
    r = await client.get("/api/v1/memory/profile", params={"hop": "infra"}, headers=H)
    assert r.status_code == 200, r.text
    p = r.json()
    assert [x["field"] for x in p["preferences"]] == ["format"]
    assert any(x["key"].startswith("infra/state/identity") for x in p["state"])
    assert any("mlpal-new-eks" in x["fact"] for x in p["learnings"])
    assert p["recent"] and p["recent"][0]["action"] in ("memory.claim", "memory.served")
    assert "## Preferences" in p["markdown"] and isinstance(p["took_ms"], int)


@pytest.mark.asyncio
async def test_fusion_ranks_facts_and_passages_in_one_list(client):
    await _claim(client, "learning", "infra/learning", "dedup", "The reconciler runs in namespace payments on the new cluster.")
    r = await client.post("/api/v1/documents", json={"title": "runbook", "content": "The reconciler service is deployed in namespace payments; restart it with kubectl rollout restart.", "scope": "org", "workspace": "infra"}, headers=H)
    assert r.status_code in (200, 201, 202), r.text
    r = await client.get("/api/v1/memory/search", params={"q": "reconciler namespace payments", "limit": 10, "fusion": "rrf"}, headers=H)
    assert r.status_code == 200, r.text
    fused = r.json()["fused"]
    assert fused and {h["kind"] for h in fused} >= {"node"} and all(h["score"] > 0 for h in fused)
    assert fused == sorted(fused, key=lambda h: -h["score"])
