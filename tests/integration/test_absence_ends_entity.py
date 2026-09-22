"""memory v6 WP15: an `absent since` state claim on a watch topic ends the entity that carries the
key: its live edges close at the claim's valid time; the current view no longer reaches it, an
as-of read before the claim still does; the watch anchor keeps its 'absent since' value."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mlpal_memory_graph.db.models import Edge

H = {"X-Test-Org-Id": "orgV", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}
VOL = "vol-0c493e116e469dfa9"


async def _post(client, *envs):
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": list(envs)}, headers=H)
    assert r.status_code == 202, r.text


def _claim(topic, key, value, days_ago):
    when = datetime.now(UTC) - timedelta(days=days_ago)
    return {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "occurred_at": when.isoformat(),
            "actor": {"user_id": "priya"}, "content": value,
            "payload": {"kind": "state", "topic": topic, "key": key, "value": value, "evidence_ids": ["c1"]}}


@pytest.mark.asyncio
async def test_absent_claim_closes_the_entitys_live_edges_and_keeps_history(client, session):
    # the entity, as a run report would have extracted it: a Fact about the volume plus the volume itself
    from mlpal_memory_graph.graph.factory import get_driver
    from mlpal_memory_graph.core.scope import Scope, ScopeRef

    drv = get_driver(); scope = ScopeRef(Scope.ORG, "orgV")
    vol = await drv.upsert_node(session, tenant_id="orgV", scope=scope, type_="Artifact", key=VOL, name=VOL, props={}, embedding=None, embedding_model=None, embedding_dim=None, source="t")
    fact = await drv.upsert_node(session, tenant_id="orgV", scope=scope, type_="Fact", key="f1", name=f"{VOL} is a 10 GiB gp3 volume, unattached since 2026-07-26", props={}, embedding=None, embedding_model=None, embedding_dim=None, source="t")
    await drv.upsert_edge(session, tenant_id="orgV", scope=scope, type_="MENTIONS", src_id=fact.id, dst_id=vol.id, fact=f"{VOL} unattached", valid_at=datetime.now(UTC) - timedelta(days=20), props={})
    await session.commit()

    await _post(client, _claim(f"infra/state/watch/{VOL}", VOL, "present; 10 GiB gp3; unattached since 2026-07-26", days_ago=5))
    live = (await session.execute(select(Edge).where(Edge.org_id == "orgV", Edge.type == "MENTIONS", Edge.invalid_at.is_(None)))).scalars().all()
    assert len(live) == 1, "a present claim ends nothing"

    await _post(client, _claim(f"infra/state/watch/{VOL}", VOL, "absent since 2026-09-13", days_ago=1))
    for e in live:
        await session.refresh(e)
    assert all(e.invalid_at is not None for e in live), "the absent claim closes the entity's live edges"
    # the watch anchor's own current value survives and says absent
    r = await client.get("/api/v1/memory/answer", params={"q": f"is volume {VOL} still present"}, headers=H)
    assert "absent since 2026-09-13" in r.json()["markdown"]
    # as-of two days ago the volume's edge was live
    asof = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    r2 = await client.get("/api/v1/memory/search", params={"q": f"{VOL} unattached volume", "as_of": asof, "limit": 10}, headers=H)
    names_then = {n["name"] for n in r2.json()["nodes"]}
    r3 = await client.get("/api/v1/memory/search", params={"q": f"{VOL} unattached volume", "limit": 10}, headers=H)
    names_now = {n["name"] for n in r3.json()["nodes"]}
    assert VOL in names_then, names_then          # the volume itself was reachable then
    assert VOL not in names_now, names_now         # and is not in the current view now
    # the historical fact about it remains knowledge; its link to the entity is closed, not deleted
    assert any("10 GiB gp3 volume" in n for n in names_now)
    # seen again: the entity returns to the current view
    await _post(client, _claim(f"infra/state/watch/{VOL}", VOL, "present; 10 GiB gp3; reattached", days_ago=0.1))
    r4 = await client.get("/api/v1/memory/search", params={"q": f"{VOL} unattached volume", "limit": 10}, headers=H)
    assert VOL in {n["name"] for n in r4.json()["nodes"]}
