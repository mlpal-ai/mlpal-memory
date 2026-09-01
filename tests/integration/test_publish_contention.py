"""v3 publish flow: personal → shared promotion, endorsement merge, and contention
points (conflicting knowledge coexists, linked CONTRADICTS, labeled in search)."""

from __future__ import annotations

import pytest

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.graph import get_driver

ORG = "orgA"


def _headers(user: str) -> dict:
    return {
        "X-Test-Org-Id": ORG,
        "X-Test-User-Id": user,
        "X-Test-Permissions": "memory.read,memory.write",
    }


async def _personal_insight(session, user: str, key: str, name: str):
    drv = get_driver()
    node = await drv.upsert_node(
        session,
        tenant_id=ORG,
        scope=ScopeRef(Scope.USER, user),
        type_="Convention",
        key=key,
        name=name,
    )
    node.derived_from = [f"ep-{user}-{key}"]
    await session.commit()
    return node


@pytest.mark.asyncio
async def test_publish_promotes_to_org(client, session):
    node = await _personal_insight(session, "alice", "test-style", "tests use pytest fixtures")
    r = await client.post(
        "/api/v1/memory/publish",
        json={"node_ids": [node.id], "scope": "org"},
        headers=_headers("alice"),
    )
    assert r.status_code == 200
    assert r.json() == {"published": 1, "merged": 0, "contentions": []}

    # bob (another org member) can now find it
    r = await client.get(
        "/api/v1/memory/search",
        params={"q": "pytest fixtures"},
        headers=_headers("bob"),
    )
    names = [n["name"] for n in r.json()["nodes"]]
    assert "tests use pytest fixtures" in names
    published = [n for n in r.json()["nodes"] if n["name"] == "tests use pytest fixtures"][0]
    assert published["status"] == "published"


@pytest.mark.asyncio
async def test_identical_publish_merges_as_endorsement(client, session):
    a = await _personal_insight(session, "alice", "fmt", "we format with ruff")
    b = await _personal_insight(session, "bob", "fmt", "we format with ruff")
    r1 = await client.post(
        "/api/v1/memory/publish", json={"node_ids": [a.id]}, headers=_headers("alice")
    )
    r2 = await client.post(
        "/api/v1/memory/publish", json={"node_ids": [b.id]}, headers=_headers("bob")
    )
    assert r1.json()["published"] == 1
    assert r2.json() == {"published": 0, "merged": 1, "contentions": []}


@pytest.mark.asyncio
async def test_conflicting_publish_creates_contention_and_labels_search(client, session):
    a = await _personal_insight(session, "alice", "branch-model", "we use trunk-based development")
    b = await _personal_insight(
        session, "bob", "branch-model", "we use gitflow with release branches"
    )

    await client.post(
        "/api/v1/memory/publish", json={"node_ids": [a.id]}, headers=_headers("alice")
    )
    r = await client.post(
        "/api/v1/memory/publish", json={"node_ids": [b.id]}, headers=_headers("bob")
    )
    body = r.json()
    assert body["published"] == 1
    assert len(body["contentions"]) == 1
    assert "disagrees" in body["contentions"][0]["fact"]

    # BOTH sides live and BOTH are labeled contested in search
    r = await client.get(
        "/api/v1/memory/search",
        params={"q": "trunk-based gitflow development"},
        headers=_headers("carol"),
    )
    hits = {n["name"]: n for n in r.json()["nodes"] if n["type"] == "Convention"}
    assert "we use trunk-based development" in hits
    assert "we use gitflow with release branches" in hits
    assert hits["we use trunk-based development"]["contested"] is True
    assert hits["we use gitflow with release branches"]["contested"] is True


@pytest.mark.asyncio
async def test_cannot_publish_someone_elses_memory(client, session):
    node = await _personal_insight(session, "alice", "priv", "alice's private habit")
    r = await client.post(
        "/api/v1/memory/publish", json={"node_ids": [node.id]}, headers=_headers("bob")
    )
    assert r.status_code == 403
