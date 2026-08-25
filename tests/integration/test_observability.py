"""PR6: observability — the contradiction-backlog invariant gauge + the ops stats endpoint."""

from __future__ import annotations

from datetime import UTC, datetime

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.db.models import Edge
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver
from mlpal_memory_graph.services.observability import contradiction_backlog, memory_stats

T1 = datetime(2026, 1, 1, tzinfo=UTC)
A = ScopeRef(Scope.ORG, "orgO")


async def test_contradiction_backlog_zero_then_detects_violation(session):
    drv = PostgresDriver()
    await drv.upsert_edge(
        session,
        tenant_id="orgO",
        scope=A,
        type_="DECIDED",
        src_id="s",
        dst_id="d1",
        fact="f1",
        valid_at=T1,
    )
    await session.flush()
    assert await contradiction_backlog(session) == 0  # one open edge per relation → healthy

    # inject a second OPEN edge for the same (org, src, dst, type) — violates invalidate-not-delete
    session.add(
        Edge(
            org_id="orgO",
            scope="org",
            scope_id="orgO",
            type="DECIDED",
            src_id="s",
            dst_id="d1",
            fact="dup",
            valid_at=T1,
        )
    )
    await session.flush()
    assert await contradiction_backlog(session) == 1


async def test_memory_stats_scoped_to_tenant(session):
    drv = PostgresDriver()
    await drv.upsert_node(session, tenant_id="orgO", scope=A, type_="Fact", key="f", name="f")
    await drv.upsert_node(
        session,
        tenant_id="orgX",
        scope=ScopeRef(Scope.ORG, "orgX"),
        type_="Fact",
        key="g",
        name="g",
    )
    await session.flush()
    s = await memory_stats(session, tenant_id="orgO")
    assert s["nodes"] == 1 and s["contradiction_backlog"] == 0  # orgX's node excluded


async def test_ops_stats_endpoint(client):
    headers = {"X-Test-Org-Id": "orgEP", "X-Test-User-Id": "alice"}
    body = {
        "episodes": [
            {
                "action_type": "fact.observed",
                "actor": {"user_id": "alice"},
                "payload": {"statement": "the service runs on kubernetes"},
            }
        ]
    }
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=headers)
    assert r.status_code == 202, r.text

    r2 = await client.get("/api/v1/ops/stats", headers=headers)
    assert r2.status_code == 200, r2.text
    stats = r2.json()
    assert stats["nodes"] >= 1 and stats["edges_open"] >= 1
    assert stats["contradiction_backlog"] == 0
