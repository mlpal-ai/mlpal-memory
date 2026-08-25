"""M1 isolation guarantee: personal (user-scoped) memory must not leak across users,
and a single-layer ``scope`` filter must narrow results. Runs on SQLite via the driver's
scope predicate (the same predicate Postgres RLS backstops in M4).

Queries are chosen as substrings of the fact text because v1 lexical search is a hard
``ILIKE`` filter; proper hybrid (vector ∪ lexical) ranking lands in M2. These tests exercise
*scope*, not ranking quality.
"""

from __future__ import annotations

ALICE = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}
BOB = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "bob"}


async def _post(client, headers, **env):
    body = {"episodes": [{"action_type": "fact.observed", **env}]}
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()


async def _names(client, headers, q, **params):
    r = await client.get("/api/v1/memory/search", params={"q": q, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return {n["name"] for n in r.json()["nodes"]}


async def test_personal_memory_does_not_leak_across_users(client):
    # org-wide fact (default org scope) + alice's personal fact
    await _post(client, ALICE, payload={"statement": "the platform runs on postgres"})
    await _post(
        client, ALICE, scope="user", scope_id="alice",
        payload={"statement": "alice prefers vim"},
    )

    # alice sees her personal fact; bob (same org) does not
    assert "alice prefers vim" in await _names(client, ALICE, "vim")
    assert "alice prefers vim" not in await _names(client, BOB, "vim")

    # the org-wide fact is visible to both
    assert "the platform runs on postgres" in await _names(client, ALICE, "postgres")
    assert "the platform runs on postgres" in await _names(client, BOB, "postgres")


async def test_scope_filter_narrows_to_single_layer(client):
    await _post(client, ALICE, payload={"statement": "retention is ninety days"})
    await _post(
        client, ALICE, scope="user", scope_id="alice",
        payload={"statement": "alice retention scratch note"},
    )

    user_only = await _names(client, ALICE, "retention", scope="user")
    assert "alice retention scratch note" in user_only
    assert "retention is ninety days" not in user_only

    org_only = await _names(client, ALICE, "retention", scope="org")
    assert "retention is ninety days" in org_only
    assert "alice retention scratch note" not in org_only
