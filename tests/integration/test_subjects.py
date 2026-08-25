"""Memory compounds per subject (repo/service/agent), not only per user. A repo's accumulated
knowledge is org-internal and shared across users, but only surfaces when that subject scope
is activated in the query. See design-proposal §1 (generalized subject scopes)."""

from __future__ import annotations

ALICE = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}
BOB = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "bob"}

FACT = "the repo uses async sqlalchemy"


async def _post_repo_fact(client):
    body = {"episodes": [{
        "action_type": "fact.observed", "scope": "repo", "scope_id": "mlpal-backend",
        "payload": {"statement": FACT},
    }]}
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=ALICE)
    assert r.status_code == 202


async def _names(client, headers, **params):
    r = await client.get(
        "/api/v1/memory/search", params={"q": "async sqlalchemy", **params}, headers=headers
    )
    assert r.status_code == 200
    return {n["name"] for n in r.json()["nodes"]}


async def test_repo_memory_requires_activation(client):
    await _post_repo_fact(client)
    # not active unless the repo subject is named in the query
    assert FACT not in await _names(client, ALICE)
    assert FACT in await _names(client, ALICE, repo="mlpal-backend")


async def test_repo_memory_is_shared_org_internal(client):
    await _post_repo_fact(client)
    # a different user in the same org sees the repo's compounded knowledge
    assert FACT in await _names(client, BOB, repo="mlpal-backend")
