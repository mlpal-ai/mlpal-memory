"""GLOBAL scope is cross-tenant by design — only the platform service identity may
write it. An org admin's authority ends at their tenant boundary (closes the
admin→global cross-tenant write vector)."""

from __future__ import annotations

import pytest

EPISODE = {
    "event_id": "g1",
    "org_id": None,
    "actor": {"user_id": "alice"},
    "source": "test",
    "action_type": "fact.observed",
    "scope": "global",
    "scope_id": "global",
    "payload": {"statement": "platform fact"},
}


@pytest.mark.asyncio
async def test_org_admin_cannot_write_global(client):
    r = await client.post(
        "/api/v1/episodes",
        json={"episodes": [EPISODE]},
        headers={
            "X-Test-Org-Id": "orgA",
            "X-Test-User-Id": "admin-user",
            "X-Test-Permissions": "memory.write,memory.admin",
        },
    )
    assert r.status_code == 403
    assert "platform-curated" in r.json()["detail"]


@pytest.mark.asyncio
async def test_service_identity_can_write_global(client):
    r = await client.post(
        "/api/v1/episodes",
        json={"episodes": [EPISODE]},
        headers={"X-Internal-Service-Key": "dev-internal-key"},
    )
    assert r.status_code == 202
    assert r.json()["accepted"] == 1


@pytest.mark.asyncio
async def test_documents_global_also_locked(client):
    r = await client.post(
        "/api/v1/documents",
        json={"content": "x", "source": "test", "scope": "global", "scope_id": "global"},
        headers={
            "X-Test-Org-Id": "orgA",
            "X-Test-Permissions": "memory.write,memory.admin",
        },
    )
    assert r.status_code == 403
