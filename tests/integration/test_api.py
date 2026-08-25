from __future__ import annotations

ORG = {"X-Test-Org-Id": "orgA"}


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


async def test_ontology_endpoint(client):
    r = await client.get("/api/v1/ontology")
    assert r.status_code == 200
    body = r.json()
    assert body["version"].startswith("core/")
    assert any(c["name"] == "Agent" for c in body["node_classes"])


async def test_ingest_dedup_and_search(client):
    body = {
        "episodes": [
            {"event_id": "x1", "org_id": "orgA", "actor": {"user_id": "alice"},
             "source": "backend", "action_type": "agent.deployed",
             "subject": {"agent_id": "sales-bot"}},
            {"event_id": "x2", "org_id": "orgA", "actor": {"user_id": "alice"},
             "source": "mcp", "action_type": "mcp.deployed",
             "subject": {"server_id": "crm-mcp"}},
        ]
    }
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=ORG)
    assert r.status_code == 202
    data = r.json()
    assert data["accepted"] == 2
    assert data["processed"] == 2

    # idempotent re-post
    r2 = await client.post("/api/v1/episodes?process=true", json=body, headers=ORG)
    assert r2.json()["duplicates"] == 2

    # search finds the agent node
    r3 = await client.get("/api/v1/memory/search", params={"q": "sales-bot"}, headers=ORG)
    assert r3.status_code == 200
    names = {n["name"] for n in r3.json()["nodes"]}
    assert "sales-bot" in names


async def test_search_requires_no_cross_org_leak(client):
    body = {
        "episodes": [
            {
                "event_id": "o1", "org_id": "orgA", "actor": {"user_id": "alice"},
                "source": "backend", "action_type": "agent.deployed",
                "subject": {"agent_id": "secret-bot"},
            }
        ]
    }
    await client.post(
        "/api/v1/episodes?process=true", json=body, headers={"X-Test-Org-Id": "orgA"}
    )

    # a different org must not see orgA's nodes
    r = await client.get(
        "/api/v1/memory/search", params={"q": "secret-bot"}, headers={"X-Test-Org-Id": "orgB"}
    )
    assert r.status_code == 200
    assert all(n["name"] != "secret-bot" for n in r.json()["nodes"])
