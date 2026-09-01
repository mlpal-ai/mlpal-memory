"""GET /nodes/{id} scope enforcement + scope-confined, bounded neighbor expansion.

Closes the within-tenant side doors: team nodes readable by non-members, and BFS
expansion walking into scopes the caller cannot read (another user's personal edges).
"""

from __future__ import annotations

import pytest

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.graph import get_driver

ORG = "orgA"
ORG_SCOPE = ScopeRef(Scope.ORG, ORG)


async def _seed_org_node_with_personal_edge(session):
    """org node --edge(user-scope bob)--> bob's personal node."""
    drv = get_driver()
    org_node = await drv.upsert_node(
        session, tenant_id=ORG, scope=ORG_SCOPE, type_="Service", key="svc-pay",
        name="payments service",
    )
    bob_scope = ScopeRef(Scope.USER, "bob")
    bob_node = await drv.upsert_node(
        session, tenant_id=ORG, scope=bob_scope, type_="Preference", key="bob-pref",
        name="bob prefers tabs",
    )
    await drv.upsert_edge(
        session, tenant_id=ORG, scope=bob_scope, type_="ABOUT",
        src_id=bob_node.id, dst_id=org_node.id, fact="bob works on payments",
    )
    await session.commit()
    return org_node, bob_node


def _headers(user: str, perms: str = "memory.read") -> dict:
    return {
        "X-Test-Org-Id": ORG,
        "X-Test-User-Id": user,
        "X-Test-Permissions": perms,
    }


@pytest.mark.asyncio
async def test_neighbors_do_not_leak_another_users_personal_edges(client, session):
    org_node, _ = await _seed_org_node_with_personal_edge(session)

    r = await client.get(f"/api/v1/memory/nodes/{org_node.id}", headers=_headers("alice"))
    assert r.status_code == 200
    assert r.json()["edges"] == []  # bob's personal edge is invisible to alice

    r = await client.get(f"/api/v1/memory/nodes/{org_node.id}", headers=_headers("bob"))
    assert r.status_code == 200
    assert len(r.json()["edges"]) == 1  # the owner still sees it


@pytest.mark.asyncio
async def test_team_node_requires_membership(client, session):
    drv = get_driver()
    team_scope = ScopeRef(Scope.TEAM, "team-x")
    node = await drv.upsert_node(
        session, tenant_id=ORG, scope=team_scope, type_="Decision", key="d1",
        name="we use trunk-based development",
    )
    await session.commit()

    r = await client.get(f"/api/v1/memory/nodes/{node.id}", headers=_headers("alice"))
    assert r.status_code == 403

    r = await client.get(
        f"/api/v1/memory/nodes/{node.id}", headers=_headers("alice", "memory.read,team:team-x")
    )
    assert r.status_code == 200

    r = await client.get(
        f"/api/v1/memory/nodes/{node.id}",
        headers=_headers("admin", "memory.read,memory.admin"),
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_neighbors_fan_out_is_bounded(session):
    drv = get_driver()
    hub = await drv.upsert_node(
        session, tenant_id=ORG, scope=ORG_SCOPE, type_="Org", key="hub", name="hub"
    )
    for i in range(6):
        spoke = await drv.upsert_node(
            session, tenant_id=ORG, scope=ORG_SCOPE, type_="User", key=f"u{i}", name=f"user {i}"
        )
        await drv.upsert_edge(
            session, tenant_id=ORG, scope=ORG_SCOPE, type_="MEMBER_OF",
            src_id=spoke.id, dst_id=hub.id,
        )
    await session.commit()

    edges = await drv.neighbors(
        session, hub.id, depth=1, tenant_id=ORG, scopes=[ORG_SCOPE], max_edges=3
    )
    assert len(edges) == 3
