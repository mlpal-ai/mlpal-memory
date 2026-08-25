"""Regression: POST /documents enforces the same write-scope gate as /episodes.

Before the fix, any memory:write caller could ingest direct-tier content into
TEAM/SERVICE/REPO/AGENT scopes that the episodes gate forbids.
"""

from __future__ import annotations

import pytest

DOC = {"content": "the payments service deploys from the release branch", "source": "test"}


def _headers(perms: str = "memory:write", user: str = "alice") -> dict:
    return {
        "X-Test-Org-Id": "orgA",
        "X-Test-User-Id": user,
        "X-Test-Permissions": perms,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "scope,scope_id",
    [("team", "team-x"), ("repo", "repo-x"), ("service", "svc-x"), ("agent", "agent-x")],
)
async def test_plain_writer_cannot_ingest_into_subject_scopes(client, scope, scope_id):
    r = await client.post(
        "/api/v1/documents",
        json={**DOC, "scope": scope, "scope_id": scope_id},
        headers=_headers(),
    )
    assert r.status_code == 403
    assert "elevated authorization" in r.json()["detail"]


@pytest.mark.asyncio
async def test_plain_writer_cannot_ingest_into_another_users_store(client):
    r = await client.post(
        "/api/v1/documents",
        json={**DOC, "scope": "user", "scope_id": "bob"},
        headers=_headers(user="alice"),
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_own_user_and_org_scopes_still_writable(client):
    for scope, scope_id in (("user", "alice"), ("org", "orgA")):
        r = await client.post(
            "/api/v1/documents",
            json={**DOC, "scope": scope, "scope_id": scope_id},
            headers=_headers(user="alice"),
        )
        assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_admin_may_ingest_into_subject_scopes(client):
    r = await client.post(
        "/api/v1/documents",
        json={**DOC, "scope": "team", "scope_id": "team-x"},
        headers=_headers(perms="memory:write,memory:admin"),
    )
    assert r.status_code == 202, r.text
