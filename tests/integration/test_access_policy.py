"""M4 access/visibility policy:
* personal (user-scoped) memory is owner-only — even an admin cannot fetch it by id;
* team-scoped memory is visible only to callers with a ``team:<id>`` grant.
"""

from __future__ import annotations

ALICE = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}
# admin in the same org, default dev identity grants ["*"]
ADMIN = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "boss"}


async def _post(client, headers, **env):
    body = {"episodes": [{"action_type": "fact.observed", **env}]}
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=headers)
    assert r.status_code == 202, r.text


async def test_admin_cannot_fetch_another_users_personal_node(client):
    await _post(
        client, ALICE, scope="user", scope_id="alice",
        payload={"statement": "alice has a personal secret"},
    )
    # alice can find her own node id
    r = await client.get("/api/v1/memory/search", params={"q": "personal secret"}, headers=ALICE)
    fact = next(n for n in r.json()["nodes"] if n["type"] == "Fact")

    # the admin (full perms, same org) is refused the personal node by id
    r2 = await client.get(f"/api/v1/memory/nodes/{fact['id']}", headers=ADMIN)
    assert r2.status_code == 403
    assert "personal" in r2.json()["detail"]

    # ...and alice herself can fetch it
    r3 = await client.get(f"/api/v1/memory/nodes/{fact['id']}", headers=ALICE)
    assert r3.status_code == 200


async def test_team_scope_visible_only_with_team_grant(client):
    # someone records a team-scoped fact for team "eng"
    await _post(
        client, ALICE, scope="team", scope_id="eng",
        payload={"statement": "eng team owns the billing service"},
    )

    member = {
        "X-Test-Org-Id": "orgA", "X-Test-User-Id": "carol",
        "X-Test-Permissions": "memory:read,team:eng",
    }
    outsider = {
        "X-Test-Org-Id": "orgA", "X-Test-User-Id": "dave",
        "X-Test-Permissions": "memory:read",
    }

    async def names(headers):
        r = await client.get("/api/v1/memory/search", params={"q": "billing"}, headers=headers)
        assert r.status_code == 200
        return {n["name"] for n in r.json()["nodes"]}

    fact = "eng team owns the billing service"
    assert fact in await names(member)
    assert fact not in await names(outsider)
