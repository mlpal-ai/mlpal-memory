"""#12 hybrid retrieval — offline (SQLite) coverage of the lexical leg + the fused read path.

The vector + lexical legs are validated on real Postgres in test_hybrid_pg.py (postgres marker);
here we cover the portable lexical fallback and that the wired hybrid resolve surfaces matches.
"""

from __future__ import annotations

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver

A = ScopeRef(Scope.ORG, "orgA")


async def _node(drv, session, key, name, type_="Service"):
    await drv.upsert_node(session, tenant_id="orgA", scope=A, type_=type_, key=key, name=name)


async def test_lexical_leg_identifier_recall(session):
    drv = PostgresDriver()
    await _node(drv, session, "checkout-svc", "checkout-svc")
    await _node(drv, session, "billing-svc", "billing-svc")
    await _node(drv, session, "k3", "the platform uses postgres", type_="Fact")
    await session.flush()

    # identifier prefix/token recall: 'checkout' finds checkout-svc, not the unrelated identifier
    names = {
        h.node.name
        for h in await drv.lexical_search_nodes(
            session, tenant_id="orgA", scopes=[A], text="checkout"
        )
    }
    assert "checkout-svc" in names
    assert "billing-svc" not in names

    # word match against name text
    pg = {
        h.node.name
        for h in await drv.lexical_search_nodes(
            session, tenant_id="orgA", scopes=[A], text="postgres"
        )
    }
    assert "the platform uses postgres" in pg


async def test_lexical_leg_respects_scope(session):
    drv = PostgresDriver()
    await _node(drv, session, "checkout-svc", "checkout-svc")
    other = ScopeRef(Scope.ORG, "orgB")
    await drv.upsert_node(
        session,
        tenant_id="orgB",
        scope=other,
        type_="Service",
        key="checkout-svc",
        name="checkout-svc",
    )
    await session.flush()
    hits = await drv.lexical_search_nodes(session, tenant_id="orgA", scopes=[A], text="checkout")
    assert all(h.node.org_id == "orgA" for h in hits)  # never leaks orgB


async def test_graph_distance_from_anchor(session):
    # alice—f1 (dist 1), f1—f2 (dist 2), f3 disconnected. Recursive-CTE traversal on SQLite.
    drv = PostgresDriver()
    await drv.upsert_node(
        session, tenant_id="orgA", scope=A, type_="User", key="alice", name="alice"
    )
    for k in ("f1", "f2", "f3"):
        await drv.upsert_node(session, tenant_id="orgA", scope=A, type_="Fact", key=k, name=k)
    await session.flush()
    nid = {}
    for t, k in (("User", "alice"), ("Fact", "f1"), ("Fact", "f2"), ("Fact", "f3")):
        nid[k] = (await drv.find_node(session, "orgA", A, t, k)).id
    await drv.upsert_edge(
        session, tenant_id="orgA", scope=A, type_="DECIDED", src_id=nid["alice"], dst_id=nid["f1"]
    )
    await drv.upsert_edge(
        session, tenant_id="orgA", scope=A, type_="RELATES_TO", src_id=nid["f1"], dst_id=nid["f2"]
    )
    await session.flush()

    d = await drv.graph_distance(
        session,
        anchor_ids=[nid["alice"]],
        candidate_ids=[nid["f1"], nid["f2"], nid["f3"]],
        max_depth=3,
    )
    assert d.get(nid["f1"]) == 1
    assert d.get(nid["f2"]) == 2
    assert nid["f3"] not in d  # disconnected → no distance, no boost


async def test_hybrid_resolve_surfaces_match(client):
    # end-to-end through the fused (vector ∪ lexical → RRF) read path
    headers = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}
    body = {
        "episodes": [
            {
                "action_type": "fact.observed",
                "actor": {"user_id": "alice"},
                "payload": {"statement": "the checkout service uses stripe"},
            }
        ]
    }
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=headers)
    assert r.status_code == 202, r.text
    r2 = await client.get("/api/v1/memory/search", params={"q": "checkout stripe"}, headers=headers)
    assert r2.status_code == 200
    assert any("checkout" in n["name"] for n in r2.json()["nodes"])
