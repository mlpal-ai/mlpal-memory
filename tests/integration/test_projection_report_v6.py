"""memory v6 WP7: the projection leads with the person's preferences and the HOP's injected state,
ranks learnings by trust; the owner's report counts the ledgers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

H = {"X-Test-Org-Id": "orgP", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}


async def _claim(client, kind, topic, key, value, scope="org", event_id=None):
    env = {"scope": scope, "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "priya"},
           "content": value, "payload": {"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": ["c1"], "hop": "infra@0.3.0"}}
    if scope == "user":
        env["scope_id"] = "priya"
    if event_id:
        env["event_id"] = event_id
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_projection_leads_with_preferences_and_injected_state(client, session):
    await _claim(client, "preference", "person/priya/pref/infra", "cost_format", "table", scope="user")
    await _claim(client, "preference", "person/priya/pref", "channel", "email", scope="user")
    await _claim(client, "state", "infra/state/cost-daily", "2026-09-15", "sent 15:57Z; MTD $398")
    await _claim(client, "state", "infra/state/identity", "account", "024249678939")
    await _claim(client, "learning", "infra/learning", "dedup", "Always pass --context to kubectl on this machine; the default points at the old account.")
    r = await client.get("/api/v1/memory/projection", params={"hop": "infra", "inject": "infra/state/cost-daily,infra/state/identity", "workspace": "infra"}, headers=H)
    assert r.status_code == 200, r.text
    md = r.json()["markdown"]
    assert md.index("## Preferences (yours)") < md.index("## State (current)")
    assert "- cost_format: table" in md and "- channel: email" in md
    assert "infra/state/cost-daily:2026-09-15 = sent 15:57Z; MTD $398" in md and "infra/state/identity:account = 024249678939" in md
    assert "Always pass --context" in md
    # another user sees no preferences of priya's
    other = await client.get("/api/v1/memory/projection", params={"hop": "infra"}, headers={**H, "X-Test-User-Id": "marco"})
    assert "cost_format" not in other.json()["markdown"]


@pytest.mark.asyncio
async def test_report_counts_the_ledgers(client, session):
    await _claim(client, "state", "infra/state/cost-daily", "2026-09-14", "sent")
    await _claim(client, "deviation", "infra/deviation", "r1", "kind: correction\nexpected: x\nobserved: y\ncause: z\naction: w\nrun: r1")
    ev = {"action_type": "run.completed", "contract": "d11.4", "scope_id": "infra", "occurred_at": datetime.now(UTC).isoformat(),
          "payload": {"hop": {"name": "infra-ro", "version": "0.3.0"}, "model": "claude-opus-5", "task_type": "infra", "run_result": "success",
                      "failure_class": None, "wall_ms": 1000, "turns": 3, "tier": "frontier", "role": "main", "run_id": "run-A",
                      "tokens": {"input": 10, "output": 20}, "checks": {"self_check": {"fired": False}, "anti_churn": {"fired": False}, "observe": {"ran": True, "passed": True}, "agent": {"verdict": None}}}}
    r = await client.post("/api/v1/telemetry", json={"events": [ev]}, headers=H); assert r.status_code == 202 and not r.json().get("rejected"), r.text
    r = await client.get("/api/v1/memory/search", params={"q": "cost-daily", "limit": 5}, headers={**H, "X-Run-Id": "run-A"}); assert r.status_code == 200
    rep = (await client.get("/api/v1/memory/report", params={"window_days": 7, "hop": "infra", "hop_alias": "infra-ro=infra", "stale_after_days": 0.1}, headers=H)).json()
    assert rep["runs"]["total"] == 1 and rep["runs"]["by_result"] == {"success": 1}
    assert rep["contribution"]["runs_with_memory_read"] == 1 and rep["contribution"]["share"] == 1.0
    assert rep["quality"]["deviations"] == 1 and rep["quality"]["corrections"] == 1
    assert rep["freshness"]["state_values"] >= 1 and rep["freshness"]["stale"] == 0 and rep["freshness"]["stale_after_days"] == 0.1
    assert rep["cost"]["tokens_by_model"]["claude-opus-5"]["output"] == 20
    md = (await client.get("/api/v1/memory/report", params={"hop": "infra", "hop_alias": "infra-ro=infra", "format": "markdown"}, headers=H)).text
    assert md.startswith("# Memory report — infra")
