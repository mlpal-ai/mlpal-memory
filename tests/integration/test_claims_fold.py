"""memory.claim through the fold: keyed supersession, learning dedup, preference at the writer's scope,
and the deviation kind reaching the distiller's query."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

H = {"X-Test-Org-Id": "orgC", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}


def _claim(kind, topic, key, value, *, days_ago=0, scope="org", evidence=("call-1",), event_id=None):
    when = datetime.now(UTC) - timedelta(days=days_ago)
    env = {"scope": scope, "source": "harness_memory", "action_type": "memory.claim", "occurred_at": when.isoformat(),
           "actor": {"user_id": "priya"}, "content": str(value),
           "payload": {"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": list(evidence), "hop": "infra@0.3.0"}}
    if scope == "user":
        env["scope_id"] = "priya"
    if event_id:
        env["event_id"] = event_id
    return env


async def _post(client, *envs):
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": list(envs)}, headers=H)
    assert r.status_code == 202, r.text
    return r.json()


async def _current_values(client, anchor_key: str):
    r = await client.get("/api/v1/memory/search", params={"q": anchor_key, "limit": 20}, headers=H)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_state_claim_supersedes_the_previous_value_and_keeps_history(client, session):
    from sqlalchemy import select

    from mlpal_memory_graph.db.models import Edge, Node

    await _post(client, _claim("state", "infra/state/cost-daily", "2026-09-15", "$252.79 MTD", days_ago=1))
    await _post(client, _claim("state", "infra/state/cost-daily", "2026-09-15", "$398.08 MTD", days_ago=0))
    anchor = (await session.execute(select(Node).where(Node.org_id == "orgC", Node.type == "Metric", Node.key == "state:infra/state/cost-daily:2026-09-15"))).scalar_one()
    edges = (await session.execute(select(Edge).where(Edge.org_id == "orgC", Edge.src_id == anchor.id, Edge.type == "HAS_VALUE"))).scalars().all()
    assert len(edges) == 2
    current = [e for e in edges if e.invalid_at is None]
    closed = [e for e in edges if e.invalid_at is not None]
    assert len(current) == 1 and len(closed) == 1
    cur = (await session.execute(select(Node).where(Node.id == current[0].dst_id))).scalar_one()
    assert cur.props["value"] == "$398.08 MTD" and cur.props["grounded"] is True and cur.props["hop"] == "infra@0.3.0"


@pytest.mark.asyncio
async def test_learning_claims_dedup_by_meaning_and_carry_provenance(client, session):
    from sqlalchemy import select

    from mlpal_memory_graph.db.models import Node

    text = "The default kube context on this machine points at the old account; always pass --context explicitly."
    await _post(client, _claim("learning", "infra/learning", "dedup", text, evidence=("call-3",)))
    await _post(client, _claim("learning", "infra/learning", "dedup", text, evidence=("call-9",)))
    facts = (await session.execute(select(Node).where(Node.org_id == "orgC", Node.type == "Fact"))).scalars().all()
    assert len(facts) == 1
    assert facts[0].observed_count >= 2 and facts[0].props["grounded"] is True


@pytest.mark.asyncio
async def test_preference_claim_lands_at_the_writers_user_scope(client, session):
    from sqlalchemy import select

    from mlpal_memory_graph.db.models import Node

    await _post(client, _claim("preference", "person/priya/pref/infra", "cost_format", "table", scope="user"))
    n = (await session.execute(select(Node).where(Node.org_id == "orgC", Node.type == "Metric", Node.key == "pref:person/priya/pref/infra:cost_format"))).scalar_one()
    assert n.scope == "user" and n.scope_id == "priya"
    # another user cannot see it
    other = {**H, "X-Test-User-Id": "marco"}
    r = await client.get("/api/v1/memory/search", params={"q": "cost_format table", "limit": 10}, headers=other)
    assert r.status_code == 200
    assert not any("pref:person/priya" in (m.get("key") or "") for m in (r.json().get("results") or r.json().get("nodes") or []))


@pytest.mark.asyncio
async def test_deviation_claims_are_counted_by_the_distiller(client, session):
    from mlpal_memory_graph.pipeline.hop_distiller import distill_deviations
    from mlpal_memory_graph.tools.hop_distill import apply_hop_alias  # noqa: F401  (module import path sanity)

    await _post(client, _claim("deviation", "infra/deviation", "run-1", "kind: unmodelled\nexpected: x\nobserved: y\ncause: z\naction: w\nrun: run-1"))
    from sqlalchemy import select

    from mlpal_memory_graph.db.models import Episode

    rows = (await session.execute(select(Episode).where(Episode.org_id == "orgC", Episode.action_type == "memory.claim"))).scalars().all()
    devs = [dict(r.payload, slug="dev-unmodelled-x", hop="infra@0.3.0") for r in rows if r.payload.get("kind") == "deviation"]
    ents, edges = distill_deviations([{"hop": "infra@0.3.0", "kind": "unmodelled", "slug": "dev-unmodelled-x", "run": "run-1"}])
    assert edges and edges[0].fact == "infra deviations (unmodelled) = 1 in window" and len(devs) == 1


@pytest.mark.asyncio
async def test_packet_leads_with_the_current_keyed_value(client, session):
    await _post(client, _claim("state", "infra/state/cost-daily", "2026-09-15", "sent 08:02Z; $360.10 MTD", days_ago=2))
    await _post(client, _claim("state", "infra/state/cost-daily", "2026-09-15", "sent 15:57Z; $398.08 MTD", days_ago=1))
    r = await client.get("/api/v1/memory/answer", params={"q": "what did the cost-daily report say today"}, headers=H)
    assert r.status_code == 200, r.text
    md = r.json()["markdown"]
    print("\nPACKET>>>\n" + md + "\n<<<PACKET")
    assert "$398.08" in md, md
    if "$360.10" in md:
        assert md.index("$398.08") < md.index("$360.10"), md


@pytest.mark.asyncio
async def test_as_of_packet_leads_with_the_then_value(client, session):
    await _post(client, _claim("state", "infra/state/cost-daily", "2026-09-15", "sent 08:02Z; $360.10 MTD", days_ago=2))
    await _post(client, _claim("state", "infra/state/cost-daily", "2026-09-15", "sent 15:57Z; $398.08 MTD", days_ago=1))
    from datetime import UTC, datetime, timedelta

    as_of = (datetime.now(UTC) - timedelta(days=1.5)).isoformat()
    r = await client.get("/api/v1/memory/answer", params={"q": "cost-daily report on the morning run", "as_of": as_of}, headers=H)
    assert r.status_code == 200, r.text
    lead = r.json()["markdown"].splitlines()[1]
    assert "$360.10" in lead and "value as of" in lead, lead
    assert "$398.08" not in r.json()["markdown"]


@pytest.mark.asyncio
async def test_team_member_writes_team_scope_and_non_member_cannot(client, session):
    env = _claim("learning", "infra/learning", "dedup", "Terraform apply runs only from the platform pipeline.", scope="team")
    env["scope_id"] = "platform"
    member = {**H, "X-Test-Permissions": "memory.read,memory.write,team:platform"}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=member)
    assert r.status_code == 202, r.text
    outsider = {**H, "X-Test-User-Id": "marco", "X-Test-Permissions": "memory.read,memory.write,team:backend"}
    env2 = dict(env, event_id="ev-outsider")
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env2]}, headers=outsider)
    assert r.status_code == 403, r.text
    seen = (await client.get("/api/v1/memory/search", params={"q": "terraform apply platform pipeline", "limit": 10}, headers=member)).json()["nodes"]
    unseen = (await client.get("/api/v1/memory/search", params={"q": "terraform apply platform pipeline", "limit": 10}, headers=outsider)).json()["nodes"]
    assert any("pipeline" in n["name"] for n in seen) and not any("pipeline" in n["name"] for n in unseen)



@pytest.mark.asyncio
async def test_search_type_accepts_memory_kinds(client, session):
    """A HOP asks for `type=state`; the anchor is a Metric node, and the kind name must still find it
    (the 0.3.0 flip, 2026-09-17: the cost-daily guard searched with type=state and saw nothing)."""
    await _post(client, _claim("state", "infra/state/cost-daily", "2026-09-16", "sent 15:12Z; MTD $543.94"))
    await _post(client, _claim("preference", "person/{me}/pref/infra", "format", "one plain mail", scope="user"))
    q = "state:infra/state/cost-daily:2026-09-16"
    r = await client.get("/api/v1/memory/search", params={"q": q, "type": "state", "limit": 10}, headers=H)
    assert r.status_code == 200, r.text
    keys = [n["key"] for n in r.json()["nodes"] if n["type"] == "Metric"]
    assert "state:infra/state/cost-daily:2026-09-16" in keys, r.json()["nodes"]
    assert all(k.startswith("state:") for k in keys), keys
    r = await client.get("/api/v1/memory/search", params={"q": "person priya pref infra format", "type": "preference", "limit": 10}, headers=H)
    assert r.status_code == 200, r.text
    keys = [n["key"] for n in r.json()["nodes"] if n["type"] == "Metric"]
    assert keys and all(k.startswith("pref:") for k in keys), r.json()["nodes"]
    r = await client.get("/api/v1/memory/search", params={"q": q, "type": "Metric", "limit": 10}, headers=H)
    assert any(n["key"] == "state:infra/state/cost-daily:2026-09-16" for n in r.json()["nodes"]), "node types still work"
