"""memory v6 WP11: the host stamps the HOP's contract on each call; the service refuses a claim
outside `writes` and hides keyed topics outside `writes ∪ reads` on that HOP's reads."""

from __future__ import annotations

import pytest

H = {"X-Test-Org-Id": "orgW", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}
INFRA = {**H, "X-Memory-Writes": "infra/*,person/{me}/pref/infra", "X-Memory-Reads": "company/ownership"}


def _claim(topic, key, value, kind="state"):
    return {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "priya"},
            "content": value, "payload": {"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": ["c1"]}}


async def _post(client, env, headers):
    return await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=headers)


@pytest.mark.asyncio
async def test_claim_outside_writes_is_refused_and_inside_is_accepted(client):
    r = await _post(client, _claim("stock/state/aapl", "2026-09-16", "182.10"), INFRA)
    assert r.status_code == 403 and "outside this HOP's memory contract" in r.text and "infra/*" in r.text
    r = await _post(client, _claim("infra/state/cost-daily", "2026-09-16", "sent; $12"), INFRA)
    assert r.status_code == 202, r.text
    # a caller with no contract (a person, a legacy HOP) is unrestricted
    r = await _post(client, _claim("stock/state/aapl", "2026-09-16", "182.10"), H)
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_reads_hide_keyed_topics_outside_the_grant(client):
    for topic, key, value in (("stock/state/aapl", "2026-09-16", "AAPL close 182.10 dollars"),
                              ("company/ownership", "reconciler", "reconciler owned by backend team"),
                              ("infra/state/cost-daily", "2026-09-16", "cost report sent 12 dollars")):
        assert (await _post(client, _claim(topic, key, value), H)).status_code == 202
    names = lambda r: {n["name"] for n in r.json()["nodes"]}
    seen = names(await client.get("/api/v1/memory/search", params={"q": "aapl close ownership reconciler cost report", "limit": 25}, headers=INFRA))
    assert any("cost-daily" in n for n in seen) and any("ownership" in n for n in seen), seen
    assert not any("aapl" in n.lower() for n in seen), seen
    everyone = names(await client.get("/api/v1/memory/search", params={"q": "aapl close ownership reconciler cost report", "limit": 25}, headers=H))
    assert any("aapl" in n.lower() for n in everyone)
    # the projection's injected state obeys the same grant
    r = await client.get("/api/v1/memory/projection", params={"hop": "infra", "inject": "stock/state/aapl,infra/state/cost-daily"}, headers=INFRA)
    md = r.json()["markdown"]
    assert "cost-daily" in md and "aapl" not in md.lower(), md
