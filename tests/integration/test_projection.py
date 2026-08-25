"""M7: the always-on Markdown projection — current facts across accessible scopes, grouped by
scope, budget-capped and rebuildable from the graph. SQLite/offline."""

from __future__ import annotations

ALICE = {"X-Test-Org-Id": "orgP", "X-Test-User-Id": "alice"}


async def _post(client, statement, scope=None, scope_id=None):
    ep = {
        "action_type": "fact.observed",
        "actor": {"user_id": "alice"},
        "payload": {"statement": statement},
    }
    if scope:
        ep["scope"], ep["scope_id"] = scope, scope_id
    r = await client.post("/api/v1/episodes?process=true", json={"episodes": [ep]}, headers=ALICE)
    assert r.status_code == 202, r.text


async def test_projection_renders_current_facts_grouped_by_scope(client):
    await _post(client, "the platform runs on postgres")  # org scope
    await _post(client, "alice prefers concise summaries", scope="user", scope_id="alice")

    r = await client.get("/api/v1/memory/projection", headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    md = body["markdown"]
    assert md.startswith("# Memory")
    assert "the platform runs on postgres" in md
    assert "alice prefers concise summaries" in md
    assert "## org:orgP" in md and "## user:alice" in md
    assert body["fact_count"] >= 2
    assert body["estimated_tokens"] == len(md) // 4 or body["estimated_tokens"] >= 1
    assert body["truncated"] is False


async def test_projection_respects_token_budget(client):
    # lexically-distinct facts so the semantic resolver doesn't dedup them into one node
    for i in range(25):
        await _post(client, f"alpha{i} bravo{i} charlie{i} delta{i} echo{i} foxtrot{i}")

    r = await client.get("/api/v1/memory/projection", params={"token_budget": 200}, headers=ALICE)
    assert r.status_code == 200
    body = r.json()
    assert body["truncated"] is True
    assert body["estimated_tokens"] <= 200
    assert "truncated to fit" in body["markdown"]
