"""memory v10: an unprompted learning (the engine's Stop hook, `origin: stop-hook`) on probation
reaches only the person and HOP it came from, ranks below prompted learnings, and at most three of
them enter one projection — agreed with the engine side on 2026-09-22."""

from __future__ import annotations

SVP = {"X-Test-Org-Id": "orgU", "X-Test-User-Id": "svp", "X-Test-Permissions": "memory.read,memory.write"}
ANA = {"X-Test-Org-Id": "orgU", "X-Test-User-Id": "ana", "X-Test-Permissions": "memory.read,memory.write"}


async def _learning(client, headers, text, origin=None, hop="infra@0.3.1"):
    payload = {"kind": "learning", "topic": "infra/learning", "key": "dedup", "value": text, "evidence_ids": ["c1"], "hop": hop}
    if origin:
        payload["origin"] = origin
    env = {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": headers["X-Test-User-Id"]}, "content": text, "payload": payload}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=headers)
    assert r.status_code == 202, r.text


async def _proj(client, headers, hop="infra"):
    r = await client.get("/api/v1/memory/projection", params={"hop": hop, "token_budget": 3000}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["markdown"]


async def test_unprompted_learnings_stay_with_their_person_and_hop_and_are_capped(client):
    await _learning(client, SVP, "Prompted: pass --context=mlpal-new-eks to kubectl on shared machines.")
    # five distinct learnings (the fold deduplicates by meaning, so near-copies would collapse)
    texts = ["the reconciler restarts when the RDS proxy rotates its certificate",
             "CloudWatch alarms in INSUFFICIENT_DATA are the NAT gateway metrics after a subnet move",
             "the EKS node group scales to zero on weekends because of the scheduled action",
             "Cost Explorer lags by fourteen hours for Bedrock invocations",
             "the hop-sandbox web deployment has no readiness probe so rollouts look instant"]
    for i, text in enumerate(texts):
        await _learning(client, SVP, f"Unprompted number {i}: {text}.", origin="stop-hook")
    await _learning(client, SVP, "Unprompted for another HOP: the stock screener rate limit resets at 09:30 ET.", origin="stop-hook", hop="stock@0.1.0")
    md = await _proj(client, SVP)
    assert "mlpal-new-eks" in md
    shown = [i for i in range(5) if f"Unprompted number {i}" in md]
    assert len(shown) == 3, shown
    assert md.index("mlpal-new-eks") < md.index(f"Unprompted number {shown[0]}"), "prompted learnings come first"
    assert "stock screener" not in md, "another HOP's unprompted learning is not injected into this HOP's session"
    md_other = await _proj(client, ANA)
    assert "mlpal-new-eks" in md_other and "Unprompted number" not in md_other, "someone else's unprompted learnings stay with them"
    md_stock = await _proj(client, SVP, hop="stock")
    assert "stock screener" in md_stock and "Unprompted number" not in md_stock
