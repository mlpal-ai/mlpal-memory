"""PR4: `as_of` time-travel. Valid-time ("what was true in the world at T") and system-time
("what we believed at T") queries over the bi-temporal edges. Driver-level + via the REST API.
SQLite/offline, injected clock."""

from __future__ import annotations

from datetime import UTC, datetime

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 3, 1, tzinfo=UTC)
T1_5 = datetime(2026, 2, 1, tzinfo=UTC)
T2_5 = datetime(2026, 4, 1, tzinfo=UTC)

TENANT = "orgAO"
SCOPE = ScopeRef(Scope.ORG, TENANT)


async def _owned_by(drv, session, dst, valid_at):
    return await drv.upsert_edge(
        session,
        tenant_id=TENANT,
        scope=SCOPE,
        type_="OWNED_BY",
        src_id="svc1",
        dst_id=dst,
        fact=f"svc1 owned by {dst}",
        valid_at=valid_at,
    )


async def test_neighbors_as_of_valid_time(session):
    drv = PostgresDriver()
    old = await _owned_by(drv, session, "alice", T1)
    new = await _owned_by(drv, session, "bob", T2)
    await drv.invalidate_superseded(session, tenant_id=TENANT, scope=SCOPE, new_edge=new)
    await session.flush()

    # at T1.5 alice was the truth; at T2.5 bob is
    at_15 = await drv.neighbors(session, "svc1", as_of=T1_5)
    assert {e.dst_id for e in at_15} == {"alice"}
    at_25 = await drv.neighbors(session, "svc1", as_of=T2_5)
    assert {e.dst_id for e in at_25} == {"bob"}
    # current view (no as_of) = bob
    assert {e.dst_id for e in await drv.neighbors(session, "svc1")} == {"bob"}
    _ = old


async def test_neighbors_as_of_system_time(session):
    """System/belief-time: what we *knew* at T. Both edges were ingested ~now, so a past
    instant sees neither; 'now' sees the current belief."""
    drv = PostgresDriver()
    await _owned_by(drv, session, "alice", T1)
    await session.flush()
    past = await drv.neighbors(session, "svc1", as_of=T1, as_of_mode="system")
    assert past == []  # we hadn't learned anything as of 2026-01
    now_view = await drv.neighbors(session, "svc1", as_of=datetime.now(UTC), as_of_mode="system")
    assert {e.dst_id for e in now_view} == {"alice"}


async def test_search_api_as_of(client):
    headers = {"X-Test-Org-Id": "orgAO", "X-Test-User-Id": "alice"}

    async def post(event_id, occurred_at, statement):
        body = {
            "episodes": [
                {
                    "event_id": event_id,
                    "occurred_at": occurred_at.isoformat(),
                    "actor": {"user_id": "alice"},  # so the DECIDED edge (User→Fact) is created
                    "action_type": "fact.observed",
                    "payload": {"statement": statement},
                }
            ]
        }
        r = await client.post("/api/v1/episodes?process=true", json=body, headers=headers)
        assert r.status_code == 202, r.text

    await post("ao1", T1, "the alpha decision")
    await post("ao2", T2, "the beta decision")

    # as_of T1.5: only the alpha fact's edge was valid yet
    r = await client.get(
        "/api/v1/memory/search",
        params={"q": "decision", "as_of": T1_5.isoformat(), "depth": 1},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    facts = {e["fact"] for e in r.json()["edges"] if e.get("fact")}
    assert any("alpha" in f for f in facts)
    assert not any("beta" in f for f in facts)


# ── node-follows-edge-validity (Option 1) ─────────────────────────────────────


async def test_nodes_with_valid_edges_excludes_invalidated(session):
    drv = PostgresDriver()
    a = ScopeRef(Scope.ORG, "orgN")
    for t, k in (("User", "u"), ("Fact", "f1"), ("Fact", "f2")):
        await drv.upsert_node(session, tenant_id="orgN", scope=a, type_=t, key=k, name=k)
    nid = {
        k: (await drv.find_node(session, "orgN", a, t, k)).id
        for t, k in (("User", "u"), ("Fact", "f1"), ("Fact", "f2"))
    }
    e1 = await drv.upsert_edge(
        session,
        tenant_id="orgN",
        scope=a,
        type_="DECIDED",
        src_id=nid["u"],
        dst_id=nid["f1"],
        valid_at=T1,
    )
    e1.invalid_at = T2  # f1's only edge ends at T2
    await drv.upsert_edge(
        session,
        tenant_id="orgN",
        scope=a,
        type_="DECIDED",
        src_id=nid["u"],
        dst_id=nid["f2"],
        valid_at=T1,
    )
    await session.flush()
    ids = [nid["f1"], nid["f2"]]

    at15 = await drv.nodes_with_valid_edges(session, ids, as_of=T1_5)
    assert nid["f1"] in at15 and nid["f2"] in at15  # both edges valid at T1.5
    at25 = await drv.nodes_with_valid_edges(session, ids, as_of=T2_5)
    assert nid["f1"] not in at25 and nid["f2"] in at25  # f1 invalidated by T2.5


async def test_search_as_of_hides_node_without_valid_edge(client):
    # a node only surfaces at T if it has >=1 edge valid at T; current view is unchanged
    headers = {"X-Test-Org-Id": "orgN2", "X-Test-User-Id": "alice"}

    async def post(event_id, occurred_at, statement):
        body = {
            "episodes": [
                {
                    "event_id": event_id,
                    "occurred_at": occurred_at.isoformat(),
                    "actor": {"user_id": "alice"},
                    "action_type": "fact.observed",
                    "payload": {"statement": statement},
                }
            ]
        }
        r = await client.post("/api/v1/episodes?process=true", json=body, headers=headers)
        assert r.status_code == 202, r.text

    await post("nb1", T1, "the alpha decision")
    await post("nb2", T2, "the beta decision")  # its DECIDED edge isn't valid until T2

    async def names(params):
        r = await client.get(
            "/api/v1/memory/search", params={"q": "decision", **params}, headers=headers
        )
        assert r.status_code == 200, r.text
        return {n["name"] for n in r.json()["nodes"]}

    at15 = await names({"as_of": T1_5.isoformat()})
    assert any("alpha" in n for n in at15)
    assert not any("beta" in n for n in at15)  # beta node hidden — no edge valid at T1.5

    current = await names({})
    assert any("alpha" in n for n in current) and any("beta" in n for n in current)
