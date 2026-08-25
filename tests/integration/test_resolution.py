"""M3: cross-scope resolution. When the same fact exists at two layers, the narrower copy
is displayed and the broader one becomes ``also_known_at`` provenance; ``/explain`` shows
the trace. Query terms are substrings of the fact text (see test_scope_isolation note)."""

from __future__ import annotations

ALICE = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}

STATEMENT = "we use postgres"  # same statement → same (type, key) at every scope


async def _post(client, **env):
    body = {"episodes": [{"action_type": "fact.observed", **env}]}
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=ALICE)
    assert r.status_code == 202, r.text


async def test_narrower_scope_shadows_broader_with_provenance(client):
    await _post(client, payload={"statement": STATEMENT})  # org scope (default)
    await _post(client, scope="user", scope_id="alice", payload={"statement": STATEMENT})

    r = await client.get("/api/v1/memory/search", params={"q": "postgres"}, headers=ALICE)
    assert r.status_code == 200
    facts = [n for n in r.json()["nodes"] if n["type"] == "Fact"]

    # one merged fact, displayed at the user layer, org copy recorded as provenance
    assert len(facts) == 1
    assert facts[0]["scope"] == "user"
    assert "org:orgA" in facts[0]["also_known_at"]


async def test_explain_returns_resolution_trace(client):
    await _post(client, payload={"statement": STATEMENT})
    await _post(client, scope="user", scope_id="alice", payload={"statement": STATEMENT})

    r = await client.get("/api/v1/memory/explain", params={"q": "postgres"}, headers=ALICE)
    assert r.status_code == 200
    body = r.json()

    assert "user:alice" in body["accessible_scopes"]
    assert "org:orgA" in body["accessible_scopes"]
    assert body["candidates"] >= 2
    assert body["merged"] < body["candidates"]  # dedupe happened
    assert any(s["type"] == "Fact" for s in body["shadowed"])
