"""End-to-end guardrail: a secret shared in an episode is never folded into memory as
plaintext — only an mlpal-secret:// reference is stored. See design-proposal §2.3."""

from __future__ import annotations

ORG = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}


async def test_secret_not_persisted_as_plaintext(client):
    body = {"episodes": [{
        "action_type": "fact.observed",
        "payload": {"statement": "the deploy api_key = s3cr3tValue123 for the prod cluster"},
    }]}
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=ORG)
    assert r.status_code == 202

    r2 = await client.get("/api/v1/memory/search", params={"q": "deploy api_key"}, headers=ORG)
    assert r2.status_code == 200
    facts = [n for n in r2.json()["nodes"] if n["type"] == "Fact"]
    assert facts, "expected the fact to be stored (redacted)"
    blob = " ".join(f["name"] + str(f["props"]) for f in facts)
    assert "s3cr3tValue123" not in blob
    assert "mlpal-secret://" in blob
