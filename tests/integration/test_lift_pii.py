"""memory v6 lift rule: PII never lands above the person tier, and a lift of a personal memory
that names a person is refused."""

from __future__ import annotations

import pytest
from sqlalchemy import select

from mlpal_memory_graph.db.models import Episode, Node

H = {"X-Test-Org-Id": "orgL", "X-Test-User-Id": "marco", "X-Test-Permissions": "memory.read,memory.write"}


def _claim(kind, topic, key, value, scope="org"):
    env = {"scope": scope, "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "marco"},
           "content": value, "payload": {"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": ["msg-1"]}}
    if scope == "user":
        env["scope_id"] = "marco"
    return env


async def _post(client, env):
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)
    assert r.status_code == 202, r.text
    return r.json()


@pytest.mark.asyncio
async def test_org_claim_with_pii_is_dropped_with_the_reason_and_user_claim_is_kept(client, session):
    await _post(client, _claim("learning", "infra/learning", "dedup", "The on-call for payments is Marco, page him at +1 415-555-0142 first."))
    await _post(client, _claim("learning", "infra/learning", "dedup", "The on-call rota for payments lives in the backend team's runbook, page the rota first.", scope="user"))
    eps = (await session.execute(select(Episode).where(Episode.org_id == "orgL", Episode.action_type == "memory.claim"))).scalars().all()
    reasons = sorted((e.dropped_reason or "-") for e in eps)
    assert reasons == ["-", "pii:phone"]
    facts = (await session.execute(select(Node).where(Node.org_id == "orgL", Node.type == "Fact"))).scalars().all()
    assert len(facts) == 1 and facts[0].scope == "user" and "rota" in facts[0].name


@pytest.mark.asyncio
async def test_publish_refuses_a_personal_memory_that_names_a_person(client, session):
    await _post(client, _claim("learning", "infra/learning", "dedup", "Escalations go to priya.k@ledgerly.io when the rota is empty.", scope="user"))
    node = (await session.execute(select(Node).where(Node.org_id == "orgL", Node.type == "Fact", Node.scope == "user"))).scalars().first()
    r = await client.post("/api/v1/memory/publish", json={"node_ids": [node.id], "scope": "org"}, headers=H)
    assert r.status_code == 422 and "names a person (email)" in r.json()["detail"]
    # the same knowledge by role lifts fine
    await _post(client, _claim("learning", "infra/learning", "dedup", "Escalations go to the platform manager when the rota is empty.", scope="user"))
    clean = [n for n in (await session.execute(select(Node).where(Node.org_id == "orgL", Node.type == "Fact", Node.scope == "user"))).scalars().all() if "platform manager" in n.name][0]
    r = await client.post("/api/v1/memory/publish", json={"node_ids": [clean.id], "scope": "org"}, headers=H)
    assert r.status_code == 200 and r.json()["published"] == 1
