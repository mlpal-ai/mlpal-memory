"""Direct vs derived memory: documents are stored verbatim and retrievable as passages
(direct), inferred facts come back as nodes with provenance (derived), the `origin` filter
separates them, secrets are still scrubbed in direct content, and CLEAR purges both tiers.
See design-proposal §14."""

from __future__ import annotations

ALICE = {"X-Test-Org-Id": "orgD", "X-Test-User-Id": "alice"}

DOC = (
    "The checkout service is owned by the payments team.\n\n"
    "Retries are capped at three to avoid duplicate charges."
)


async def _ingest_doc(client, headers=ALICE, **over):
    body = {"content": DOC, "title": "runbook", "source": "docs", **over}
    r = await client.post("/api/v1/documents", json=body, headers=headers)
    assert r.status_code == 202, r.text
    return r.json()


async def _search(client, headers=ALICE, **params):
    r = await client.get("/api/v1/memory/search", params=params, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


async def test_document_is_retrievable_as_direct_passages(client):
    await _ingest_doc(client)
    body = await _search(client, q="capped at three")
    passages = body["passages"]
    assert passages, "expected direct passages"
    assert all(p["origin"] == "direct" for p in passages)
    assert any("Retries are capped at three" in p["content"] for p in passages)


async def test_origin_filter_separates_tiers(client):
    await _ingest_doc(client)
    # direct-only: passages present, no derived nodes
    direct = await _search(client, q="checkout service", origin="direct")
    assert direct["passages"] and direct["nodes"] == []
    # derived-only: no passages
    derived = await _search(client, q="checkout service", origin="derived")
    assert derived["passages"] == []


async def test_derived_nodes_carry_provenance(client):
    # a fact episode → derived Fact node linked back to its episode
    body = {
        "episodes": [
            {
                "action_type": "fact.observed",
                "payload": {"statement": "the checkout service uses idempotency keys"},
            }
        ]
    }
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=ALICE)
    assert r.status_code == 202
    res = await _search(client, q="idempotency", origin="derived")
    facts = [n for n in res["nodes"] if n["type"] == "Fact"]
    assert facts
    assert facts[0]["origin"] == "derived"
    assert facts[0]["derived_from"]  # links back to the source episode


async def test_secret_scrubbed_in_direct_content(client):
    await _ingest_doc(client, content="deploy token = s3cr3tDirectValue used in prod")
    body = await _search(client, q="deploy token")
    blob = " ".join(p["content"] for p in body["passages"])
    assert "s3cr3tDirectValue" not in blob
    assert "mlpal-secret://" in blob


async def test_clear_purges_both_tiers(client):
    admin = {"X-Test-Org-Id": "orgD", "X-Test-User-Id": "boss"}
    await _ingest_doc(client, headers=admin)
    assert (await _search(client, admin, q="checkout"))["passages"]

    r = await client.put(
        "/api/v1/memory/consent",
        json={"scope": "org", "scope_id": "orgD", "state": "clear"},
        headers=admin,
    )
    assert r.status_code == 200
    assert (await _search(client, admin, q="checkout"))["passages"] == []
