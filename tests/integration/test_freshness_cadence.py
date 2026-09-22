"""memory v6 WP12: a state value is stale after two of its own key's observed cadences: a daily key
is stale at > 2 days, a weekly key at > 14, regardless of the report's global threshold."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

H = {"X-Test-Org-Id": "orgF", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}


def _claim(topic, key, value, days_ago):
    when = datetime.now(UTC) - timedelta(days=days_ago)
    return {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "occurred_at": when.isoformat(),
            "actor": {"user_id": "priya"}, "content": value,
            "payload": {"kind": "state", "topic": topic, "key": key, "value": value, "evidence_ids": ["c1"]}}


@pytest.mark.asyncio
async def test_staleness_follows_each_keys_cadence(client):
    daily = [_claim("infra/state/cost-daily", "mtd", f"${i}", d) for i, d in enumerate((8, 7, 6, 5))]      # daily, last 5 days ago
    weekly = [_claim("infra/state/watch/backup", "backup", f"ok {i}", d) for i, d in enumerate((31, 24, 17, 10))]  # weekly, last 10 days ago
    once = [_claim("infra/state/identity", "account", "024", 3)]  # seen once: global threshold applies
    burst = [_claim("infra/state/probe", "today", v, d) for v, d in (("a", 0.0010), ("b", 0.0005))]  # two writes a minute apart: no cadence
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": daily + weekly + once + burst}, headers=H)
    assert r.status_code == 202, r.text
    rep = (await client.get("/api/v1/memory/report", params={"window_days": 30, "stale_after_days": 2}, headers=H)).json()
    items = {it["key"]: it for it in rep["freshness"]["items"]}
    d, w, o = items["state:infra/state/cost-daily:mtd"], items["state:infra/state/watch/backup:backup"], items["state:infra/state/identity:account"]
    assert d["stale"] and d["rule"] == "2x cadence" and 0.9 < d["cadence_days"] < 1.1, d
    assert not w["stale"] and 6.9 < w["cadence_days"] < 7.1 and w["stale_after"] > 13.9, w
    assert o["stale"] and o["rule"] == "default", o
    b = items["state:infra/state/probe:today"]
    assert b["rule"] == "default" and not b["stale"], b
