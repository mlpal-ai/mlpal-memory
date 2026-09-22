"""Trust by consequence end to end inside the service: a run's reads are logged as memory.served, its
verdict arrives as telemetry (d11.5 with memories_injected), the trust tool joins them, and the packet
shows the tier."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mlpal_memory_graph.db.models import Episode, Node

H = {"X-Test-Org-Id": "orgT", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}


async def _claim(client, value, event_id):
    env = {"event_id": event_id, "scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "priya"},
           "content": value, "payload": {"kind": "learning", "topic": "infra/learning", "key": "dedup", "value": value, "evidence_ids": ["c1"]}}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)
    assert r.status_code == 202, r.text


async def _telemetry(client, run_id, result, injected=None):
    ev = {"action_type": "run.completed", "contract": "d11.5", "scope_id": "infra", "occurred_at": datetime.now(UTC).isoformat(),
          "payload": {"hop": {"name": "infra", "version": "0.3.0"}, "model": "m", "task_type": "infra", "run_result": result,
                      "failure_class": None if result == "success" else "other", "wall_ms": 1000, "turns": 3, "tier": "frontier",
                      "role": "main", "run_id": run_id, "tokens": {"input": 1, "output": 1},
                      "checks": {"self_check": {"fired": False}, "anti_churn": {"fired": False}, "observe": {"ran": True, "passed": True}, "agent": {"verdict": None}},
                      **({"memories_injected": injected} if injected is not None else {})}}
    r = await client.post("/api/v1/telemetry", json={"events": [ev]}, headers=H)
    assert r.status_code == 202, r.text
    body = r.json()
    assert not body.get("rejected"), body
    return body


@pytest.mark.asyncio
async def test_reads_are_logged_per_run_and_trust_tiers_follow_verdicts(client, session):
    await _claim(client, "Always pass --context to kubectl on this machine; the default points at the old account.", "ev-learn-1")
    # a run reads it (the sidecar forwards the run id), then passes; two more runs the same
    for run in ("r1", "r2", "r3"):
        r = await client.get("/api/v1/memory/search", params={"q": "kubectl context old account", "limit": 5},
                             headers={**H, "X-Run-Id": run, "X-Hop": "infra@0.3.0", "X-Origin": "routine:infra-watch"})
        assert r.status_code == 200 and r.json()["nodes"], "the fact must be served"
        await _telemetry(client, run, "success")
    served = (await session.execute(select(Episode).where(Episode.org_id == "orgT", Episode.action_type == "memory.served"))).scalars().all()
    assert len(served) == 3 and all(e.processed and e.payload["tool"] == "search" and e.payload["hop"] == "infra@0.3.0" for e in served)

    from mlpal_memory_graph.tools.hop_trust import compute
    out = await compute("orgT", 7)
    assert out["runs_with_verdict"] == 3 and out["tiers"].get("corroborated", 0) >= 1
    fact = (await session.execute(select(Node).where(Node.org_id == "orgT", Node.type == "Fact"))).scalars().first()
    await session.refresh(fact)
    assert fact.props["trust"]["tier"] == "corroborated" and fact.props["trust"]["passes"] == 3 and fact.props["trust"]["fails"] == 0

    # a failing run that had the fact injected at session start (d11.5) pulls it back to probation
    await _telemetry(client, "r4", "error", injected=["ev-learn-1"])
    out = await compute("orgT", 7)
    await session.refresh(fact)
    assert fact.props["trust"]["fails"] == 1 and fact.props["trust"]["tier"] == "probation"

    # the packet shows the tier
    r = await client.get("/api/v1/memory/answer", params={"q": "kubectl context old account"}, headers=H)
    assert r.status_code == 200 and "trust:probation" in r.json()["markdown"]


@pytest.mark.asyncio
async def test_answer_and_projection_reads_are_served_rows_too(client, session):
    """Every door a run reads through feeds consequence: search, answer and the session-start projection."""
    await _claim(client, "Athena queries must name the analytics workgroup or they fail on permissions.", "ev-learn-2")
    run = {**H, "X-Run-Id": "r-doors", "X-Hop": "infra@0.3.0", "X-Origin": "routine:infra-watch"}
    r = await client.get("/api/v1/memory/answer", params={"q": "athena workgroup permissions"}, headers=run)
    assert r.status_code == 200 and "workgroup" in r.json()["markdown"]
    r = await client.get("/api/v1/memory/projection", params={"workspace": "infra", "hop": "infra"}, headers=run)
    assert r.status_code == 200 and "workgroup" in r.json()["markdown"]
    served = (await session.execute(select(Episode).where(Episode.org_id == "orgT", Episode.action_type == "memory.served"))).scalars().all()
    by_tool = {e.payload["tool"]: e.payload for e in served if e.payload["run_id"] == "r-doors"}
    assert set(by_tool) == {"answer", "projection"}, by_tool
    fact = (await session.execute(select(Node).where(Node.org_id == "orgT", Node.type == "Fact"))).scalars().first()
    assert fact.id in by_tool["answer"]["node_ids"] and fact.id in by_tool["projection"]["node_ids"]
