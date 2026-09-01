"""UI browse listings (GET /documents, GET /episodes): visibility mirrors retrieval —
org-internal rows are tenant-readable, personal rows are owner-only, and the episode
ledger exposes fold status without leaking content."""

from __future__ import annotations

import pytest

ALICE = {
    "X-Test-Org-Id": "orgA",
    "X-Test-User-Id": "alice",
    "X-Test-Permissions": "memory.read,memory.write",
}
BOB = {**ALICE, "X-Test-User-Id": "bob"}
OTHER_ORG = {**ALICE, "X-Test-Org-Id": "orgB", "X-Test-User-Id": "carol"}


async def _ingest(client, headers, *, scope: str, scope_id: str, content: str) -> None:
    r = await client.post(
        "/api/v1/documents",
        json={
            "content": content,
            "title": content[:30],
            "scope": scope,
            "scope_id": scope_id,
            "source": "md_file",
        },
        headers=headers,
    )
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_document_browse_visibility(client):
    await _ingest(client, ALICE, scope="user", scope_id="alice",
                  content="alice's private planning notes about the roadmap")
    await _ingest(client, ALICE, scope="org", scope_id=None,
                  content="the org-wide deployment runbook everyone shares")

    r = await client.get("/api/v1/documents", headers=ALICE)
    titles = [d["title"] for d in r.json()["documents"]]
    assert any("private planning" in t for t in titles)
    assert any("org-wide deployment" in t for t in titles)
    assert all(d["chunks"] >= 1 for d in r.json()["documents"])

    # bob sees the org doc but never alice's personal doc
    r = await client.get("/api/v1/documents", headers=BOB)
    titles = [d["title"] for d in r.json()["documents"]]
    assert any("org-wide deployment" in t for t in titles)
    assert not any("private planning" in t for t in titles)

    # another tenant sees neither
    r = await client.get("/api/v1/documents", headers=OTHER_ORG)
    assert r.json()["total"] == 0

    # detail: bob gets 404 on alice's personal doc (not 403 — existence is not leaked)
    r = await client.get("/api/v1/documents", headers=ALICE, params={"q": "private planning"})
    doc_id = r.json()["documents"][0]["id"]
    detail = await client.get(f"/api/v1/documents/{doc_id}", headers=ALICE)
    assert detail.status_code == 200
    assert detail.json()["chunk_contents"], "detail must include verbatim chunks"
    assert (await client.get(f"/api/v1/documents/{doc_id}", headers=BOB)).status_code == 404


@pytest.mark.asyncio
async def test_episode_ledger_status_and_privacy(client):
    await _ingest(client, ALICE, scope="user", scope_id="alice",
                  content="a session transcript that folds into an episode")

    r = await client.get("/api/v1/episodes", headers=ALICE, params={"status": "processed"})
    body = r.json()
    assert body["total"] >= 1
    ep = body["episodes"][0]
    assert ep["status"] == "processed" and ep["action_type"] == "document.ingested"
    assert "content" not in ep, "list rows must not carry raw content"

    detail = await client.get(f"/api/v1/episodes/{ep['event_id']}", headers=ALICE)
    assert detail.status_code == 200
    assert detail.json()["has_content"] is True
    # payload metadata (title/uri) is fine; the raw content body must not be served here
    assert "folds into an episode" not in detail.text, "detail exposes flags, not raw content"

    # bob cannot browse alice's personal episodes, by list or by id
    r = await client.get("/api/v1/episodes", headers=BOB)
    assert all(e["scope_id"] != "alice" for e in r.json()["episodes"])
    assert (
        await client.get(f"/api/v1/episodes/{ep['event_id']}", headers=BOB)
    ).status_code == 404


@pytest.mark.asyncio
async def test_document_order_valid_spans_history(client):
    """order=valid returns event-time ascending — the timeline's span, not
    yesterday's ingests (UI bug: newest-first collapsed the as-of slider)."""
    from datetime import UTC, datetime, timedelta

    now = datetime.now(UTC)
    for days, txt in ((400, "ancient scroll of ordering"), (5, "fresh note of ordering")):
        r = await client.post(
            "/api/v1/documents",
            json={"content": txt * 3, "title": txt, "scope": "user", "scope_id": "alice",
                  "source": "md_file", "workspace": "ord-lab",
                  "valid_at": (now - timedelta(days=days)).isoformat()},
            headers=ALICE,
        )
        assert r.status_code == 202
    r = await client.get("/api/v1/documents",
                         params={"workspace": "ord-lab", "order": "valid"}, headers=ALICE)
    titles = [d["title"] for d in r.json()["documents"]]
    assert titles.index("ancient scroll of ordering") < titles.index("fresh note of ordering")
