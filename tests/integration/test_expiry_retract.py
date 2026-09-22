"""memory v7 WP3 — time: a writer-set expiry hides a claim after its date (and keeps it as-of), the
sweep closes it bitemporally, a person can retract a memory, and a date-keyed topic is judged fresh on
its newest key only."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from mlpal_memory_graph.db.models import Edge, Episode, Node

H = {"X-Test-Org-Id": "orgT3", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}


async def _claim(client, kind, topic, key, value, *, valid_until=None, days_ago=0, scope="org", headers=H, actor="priya"):
    when = datetime.now(UTC) - timedelta(days=days_ago)
    env = {"scope": scope, "source": "harness_memory", "action_type": "memory.claim", "occurred_at": when.isoformat(),
           "actor": {"user_id": actor}, "content": str(value),
           "payload": {"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": ["c1"], "hop": "infra@0.3.0",
                       **({"valid_until": valid_until} if valid_until else {})}}
    if scope == "user":
        env["scope_id"] = actor
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=headers)
    assert r.status_code == 202, r.text


async def _nodes(client, q, **params):
    r = await client.get("/api/v1/memory/search", params={"q": q, "limit": 10, **params}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()["nodes"]


@pytest.mark.asyncio
async def test_writer_set_expiry_hides_after_the_date_and_keeps_it_as_of(client, session):
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    tomorrow = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    await _claim(client, "state", "person/priya/pref/infra", "exam", "exam on 2026-09-16, keep mails short", valid_until=yesterday, days_ago=3, scope="user")
    await _claim(client, "state", "infra/state/watch/web", "web", "2/2 ready; freeze until tomorrow", valid_until=tomorrow)
    names = [n["name"] for n in await _nodes(client, "exam keep mails short")]
    assert not any("exam" in n for n in names), names
    names = [n["name"] for n in await _nodes(client, "web freeze until tomorrow")]
    assert any("freeze" in n for n in names), names
    # as of two days ago the exam note was still true
    as_of = (datetime.now(UTC) - timedelta(days=2)).isoformat()
    names = [n["name"] for n in await _nodes(client, "exam keep mails short", as_of=as_of)]
    assert any("exam" in n for n in names), names
    # the projection never injects it
    r = await client.get("/api/v1/memory/projection", params={"hop": "infra", "inject": "infra/state/watch/web"}, headers=H)
    assert "exam" not in r.json()["markdown"] and "freeze" in r.json()["markdown"], r.json()["markdown"]


@pytest.mark.asyncio
async def test_sweep_closes_committed_expiries_bitemporally(client, session):
    yesterday = (datetime.now(UTC) - timedelta(days=1)).isoformat()
    await _claim(client, "state", "infra/state/watch/db", "db", "maintenance window open", valid_until=yesterday, days_ago=2)
    from mlpal_memory_graph.services import worker as worker_mod

    w = worker_mod.MemoryUpdateWorker.__new__(worker_mod.MemoryUpdateWorker)
    w.settings = worker_mod.get_settings()
    await w._close_expired_committed(session)
    val = (await session.execute(select(Node).where(Node.org_id == "orgT3", Node.type == "MetricValue", Node.name.like("%maintenance window%")))).scalars().first()
    await session.refresh(val)
    assert val.status == "expired"
    edges = (await session.execute(select(Edge).where(Edge.dst_id == val.id))).scalars().all()
    assert edges and all(e.invalid_at is not None for e in edges)
    assert (await session.execute(select(Node).where(Node.id == val.id))).scalars().first() is not None, "closed, not deleted"


@pytest.mark.asyncio
async def test_retract_closes_the_fact_and_leaves_a_ledger_row(client, session):
    L = "Athena queries must name the analytics workgroup or they fail on permissions."
    await _claim(client, "learning", "infra/learning", "dedup", L)
    fact = [n for n in await _nodes(client, "athena workgroup permissions", type="Fact") if "Athena" in n["name"]][0]
    r = await client.post("/api/v1/memory/retract", json={"node_ids": [fact["id"]], "reason": "the workgroup default was fixed on 09-17"}, headers=H)
    assert r.status_code == 200 and r.json() == {"retracted": 1, "unchanged": 0}, r.text
    md = (await client.get("/api/v1/memory/answer", params={"q": "athena workgroup permissions"}, headers=H)).json()["markdown"]
    assert "Athena" not in md, md
    assert (await client.post("/api/v1/memory/retract", json={"node_ids": [fact["id"]], "reason": "again"}, headers=H)).json()["unchanged"] == 1
    rows = (await session.execute(select(Episode).where(Episode.org_id == "orgT3", Episode.action_type == "memory.retracted"))).scalars().all()
    assert len(rows) == 1 and rows[0].payload["node_ids"] == [fact["id"]]
    # another person's private memory cannot be retracted by priya
    marco = {**H, "X-Test-User-Id": "marco"}
    await _claim(client, "learning", "infra/learning", "dedup", "Marco keeps his Athena scratch tables under the tmp schema.", scope="user", headers=marco, actor="marco")
    r = await client.get("/api/v1/memory/search", params={"q": "marco athena scratch tables tmp schema", "type": "Fact"}, headers=marco)
    mine = [n for n in r.json()["nodes"] if "scratch" in n["name"]][0]
    r = await client.post("/api/v1/memory/retract", json={"node_ids": [mine["id"]], "reason": "not mine"}, headers=H)
    assert r.status_code == 403, r.text


@pytest.mark.asyncio
async def test_freshness_judges_the_newest_key_of_a_date_topic(client, session):
    await _claim(client, "state", "infra/state/cost-daily", "2026-09-10", "sent; $100", days_ago=7)
    await _claim(client, "state", "infra/state/cost-daily", "2026-09-17", "sent; $120", days_ago=0)
    rep = (await client.get("/api/v1/memory/report", params={"window_days": 30}, headers=H)).json()
    keys = [i["key"] for i in rep["freshness"]["items"] if "cost-daily" in i["key"]]
    assert keys == ["state:infra/state/cost-daily:2026-09-17"], keys
    assert not [i for i in rep["freshness"]["items"] if "cost-daily" in i["key"] and i["stale"]]
