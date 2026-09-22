"""memory v6 WP13: a person's endorsement flips the tier at once, shows in the packet, is idempotent,
can be withdrawn, and never reaches another org's node."""

from __future__ import annotations

import pytest

H = {"X-Test-Org-Id": "orgE", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}
L = "Pass --context=mlpal-new-eks to kubectl on shared machines; the default still points at the old account."


async def _learning(client):
    env = {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "priya"}, "content": L,
           "payload": {"kind": "learning", "topic": "infra/learning", "key": "dedup", "value": L, "evidence_ids": ["c1"]}}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)
    assert r.status_code == 202, r.text
    nodes = (await client.get("/api/v1/memory/search", params={"q": "kubectl context old account", "type": "Fact", "limit": 5}, headers=H)).json()["nodes"]
    return [n for n in nodes if "mlpal-new-eks" in n["name"]][0]["id"]


@pytest.mark.asyncio
async def test_endorse_flips_tier_is_idempotent_and_withdrawable(client):
    nid = await _learning(client)
    r = await client.post("/api/v1/memory/endorse", json={"node_ids": [nid]}, headers=H)
    assert r.status_code == 200 and r.json()["endorsed"] == 1 and r.json()["tiers"][nid] == "endorsed", r.text
    md = (await client.get("/api/v1/memory/answer", params={"q": "kubectl context old account"}, headers=H)).json()["markdown"]
    assert "trust:endorsed" in md, md
    again = (await client.post("/api/v1/memory/endorse", json={"node_ids": [nid]}, headers=H)).json()
    assert again["endorsed"] == 0 and again["unchanged"] == 1
    # a second person's endorsement is recorded beside the first
    marco = {**H, "X-Test-User-Id": "marco"}
    assert (await client.post("/api/v1/memory/endorse", json={"node_ids": [nid]}, headers=marco)).json()["endorsed"] == 1
    node = [n for n in (await client.get("/api/v1/memory/search", params={"q": "kubectl context old account", "type": "Fact", "limit": 5}, headers=H)).json()["nodes"] if n["id"] == nid][0]
    assert sorted(node["props"]["endorsed_by"]) == ["marco", "priya"]
    # withdrawals: the tier holds while one endorsement remains, then falls back to the computed tier
    assert (await client.post("/api/v1/memory/endorse", json={"node_ids": [nid], "withdraw": True}, headers=H)).json()["tiers"][nid] == "endorsed"
    assert (await client.post("/api/v1/memory/endorse", json={"node_ids": [nid], "withdraw": True}, headers=marco)).json()["tiers"][nid] == "probation"
    rep = (await client.get("/api/v1/memory/report", params={"window_days": 7}, headers=H)).json()
    assert rep["governance"]["endorsements"] == 2


@pytest.mark.asyncio
async def test_endorse_refuses_another_orgs_node_and_an_unreadable_scope(client):
    nid = await _learning(client)
    other = {**H, "X-Test-Org-Id": "orgZ"}
    assert (await client.post("/api/v1/memory/endorse", json={"node_ids": [nid]}, headers=other)).status_code == 404
    # dana's private learning cannot be endorsed by marco: he cannot read it
    env = {"scope": "user", "scope_id": "dana", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "dana"},
           "content": "Athena ad-hoc queries use workgroup data-adhoc.", "payload": {"kind": "learning", "topic": "infra/learning", "key": "dedup",
           "value": "Athena ad-hoc queries use workgroup data-adhoc.", "evidence_ids": ["c1"]}}
    dana = {**H, "X-Test-User-Id": "dana"}
    assert (await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=dana)).status_code == 202
    private = [n for n in (await client.get("/api/v1/memory/search", params={"q": "Athena workgroup data-adhoc", "limit": 5}, headers=dana)).json()["nodes"] if "data-adhoc" in n["name"]][0]["id"]
    marco = {**H, "X-Test-User-Id": "marco"}
    assert (await client.post("/api/v1/memory/endorse", json={"node_ids": [private]}, headers=marco)).status_code == 403
