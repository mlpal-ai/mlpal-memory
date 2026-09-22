"""memory v10: pinned facts — the owner's "always in front of me" set. A pinned claim renders in a
reserved slice of the projection ahead of the ranked learnings, leaves when its valid_until passes,
and a person can pin an existing memory through the endorse door."""

from __future__ import annotations

H = {"X-Test-Org-Id": "orgPin", "X-Test-User-Id": "svp", "X-Test-Permissions": "memory.read,memory.write"}
CREDIT = "$25,000 AWS credit granted 2026-09-01, expires 2027-03-31; report burn against it"


async def _claim(client, key, value, **extra):
    env = {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "svp"},
           "payload": {"kind": "state", "topic": "infra/state/identity", "key": key, "value": value, "evidence_ids": ["msg-1"], **extra}}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)
    assert r.status_code == 202, r.text


async def _learning(client, text):
    env = {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "svp"}, "content": text,
           "payload": {"kind": "learning", "topic": "infra/learning", "key": "dedup", "value": text, "evidence_ids": ["c1"]}}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=H)
    assert r.status_code == 202, r.text


async def test_pinned_claim_renders_first_and_a_lapsed_one_does_not(client):
    await _learning(client, "Cost Explorer restates closed days; re-read the same day on the next run before calling it settled.")
    await _claim(client, "credits.aws", CREDIT, pinned=True, valid_until="2027-03-31T00:00:00Z")
    await _claim(client, "credits.gcp", "$300 GCP trial credit, expired 2025-01-01", pinned=True, valid_until="2025-01-01T00:00:00Z")
    r = await client.get("/api/v1/memory/projection", params={"token_budget": 1500}, headers=H)
    assert r.status_code == 200, r.text
    md = r.json()["markdown"]
    assert "## Pinned" in md and "$25,000 AWS credit" in md and "(until 2027-03-31)" in md
    assert "GCP trial" not in md, "a pinned fact past its valid_until has left the projection"
    assert md.index("## Pinned") < md.index("Cost Explorer restates"), "pinned facts come before the ranked learnings"


async def test_pinned_slice_is_bounded_and_the_rest_still_renders(client):
    for i in range(40):
        await _claim(client, f"limit.{i}", f"hard limit number {i}: " + "x" * 120, pinned=True)
    await _learning(client, "The EBS volume named staging-db-data is production; scope cleanups by attachment state, never the Name tag.")
    r = await client.get("/api/v1/memory/projection", params={"token_budget": 800}, headers=H)
    md = r.json()["markdown"]
    pinned_lines = [ln for ln in md.split("## Pinned", 1)[1].split("\n## ", 1)[0].splitlines() if ln.startswith("- hard limit")]
    assert 0 < len(pinned_lines) < 40, "the pinned slice is a quarter of the budget, not all of it"
    assert "staging-db-data" in md, "learnings still get the rest of the budget"


async def test_endorse_can_pin_and_unpin(client):
    await _learning(client, "Pass --context=mlpal-new-eks to kubectl on shared machines; the default points at the old account.")
    nodes = (await client.get("/api/v1/memory/search", params={"q": "kubectl context old account", "type": "Fact", "limit": 5}, headers=H)).json()["nodes"]
    nid = [n for n in nodes if "mlpal-new-eks" in n["name"]][0]["id"]
    r = await client.post("/api/v1/memory/endorse", json={"node_ids": [nid], "pin": True}, headers=H)
    assert r.status_code == 200, r.text
    md = (await client.get("/api/v1/memory/projection", headers=H)).json()["markdown"]
    assert "## Pinned" in md and "mlpal-new-eks" in md.split("## Pinned", 1)[1].split("\n## ", 1)[0]
    r = await client.post("/api/v1/memory/endorse", json={"node_ids": [nid], "pin": True, "withdraw": True}, headers=H)
    assert r.status_code == 200
    md = (await client.get("/api/v1/memory/projection", headers=H)).json()["markdown"]
    assert "## Pinned" not in md and "mlpal-new-eks" in md, "unpinned, it is an ordinary learning again"
