"""Forgetting: direct deletion (audited) + two-phase NL curation + hop streaming."""

from __future__ import annotations

import pytest

H = {
    "X-Test-Org-Id": "orgA",
    "X-Test-User-Id": "alice",
    "X-Test-Permissions": "memory.read,memory.write",
}


async def _ingest(client, content, ws="cur-lab", user="alice", headers=None):
    r = await client.post(
        "/api/v1/documents",
        json={"content": content, "title": content[:40], "scope": "user",
              "scope_id": user, "source": "md_file", "workspace": ws},
        headers=headers or H,
    )
    assert r.status_code == 202
    return r.json()["event_id"]


@pytest.mark.asyncio
async def test_forget_document_deletes_and_audits(client):
    await _ingest(client, "ephemeral scratch note about zz-throwaway experiments")
    r = await client.get("/api/v1/documents", params={"q": "ephemeral scratch"}, headers=H)
    doc = r.json()["documents"][0]
    r = await client.delete(f"/api/v1/documents/{doc['id']}", headers=H)
    assert r.status_code == 200 and r.json()["purged_chunks"] >= 1
    # gone from search and listing
    r = await client.get("/api/v1/memory/search", params={"q": "zz-throwaway"}, headers=H)
    assert not any("zz-throwaway" in p["content"] for p in r.json()["passages"])
    # audited as a governance episode
    r = await client.get("/api/v1/episodes", params={"source": "governance"}, headers=H)
    assert any(e["action_type"] == "memory.forgotten" for e in r.json()["episodes"])
    # another user cannot forget alice's doc (404 — existence not leaked)
    doc2 = await _ingest(client, "alice private zz-keep note")
    r = await client.get("/api/v1/documents", params={"q": "alice private"}, headers=H)
    did = r.json()["documents"][0]["id"]
    r = await client.delete(f"/api/v1/documents/{did}",
                            headers={**H, "X-Test-User-Id": "bob"})
    assert r.status_code == 404
    assert doc2


@pytest.mark.asyncio
async def test_curate_two_phase(client, monkeypatch):
    from mlpal_memory_graph.services import llm_client as lc

    await _ingest(client, "migration step 1: raised quotas (play-by-play)", ws="mig")
    await _ingest(client, "migration step 2: copied s3 buckets (play-by-play)", ws="mig")
    await _ingest(client, "KEY FACT: production account is the new standalone one", ws="mig")

    r = await client.get("/api/v1/documents", params={"workspace": "mig"}, headers=H)
    docs = {d["title"]: d["id"] for d in r.json()["documents"]}

    class Stub:
        async def complete_json(self, **kw):
            # model proposes forgetting the play-by-play only
            ids = [i for t, i in docs.items() if "migration step" in t]
            return {"forget": [{"id": i, "reason": "play-by-play, migration done"}
                               for i in ids]}

    monkeypatch.setattr(lc, "get_llm_client", lambda: Stub())
    r = await client.post("/api/v1/memory/curate",
                          json={"instruction": "migration done; keep key facts only",
                                "workspace": "mig"}, headers=H)
    prev = r.json()
    assert prev["mode"] == "preview" and len(prev["candidates"]) == 2
    assert prev["keep_count"] == 1  # nothing deleted yet
    r = await client.get("/api/v1/memory/search", params={"q": "copied s3 buckets"},
                         headers=H)
    assert any("copied s3" in p["content"] for p in r.json()["passages"])

    # phase 2: confirm exactly the previewed ids
    r = await client.post("/api/v1/memory/curate",
                          json={"workspace": "mig",
                                "confirm_ids": [c["id"] for c in prev["candidates"]]},
                          headers=H)
    assert r.json()["mode"] == "executed" and r.json()["forgotten"] == 2
    r = await client.get("/api/v1/memory/search", params={"q": "copied s3 buckets"},
                         headers=H)
    assert not any("copied s3" in p["content"] for p in r.json()["passages"])
    # the key fact survives
    r = await client.get("/api/v1/memory/search", params={"q": "production account"},
                         headers=H)
    assert any("standalone" in p["content"] for p in r.json()["passages"])
    # confirm_ids outside the visible workspace are refused wholesale
    r = await client.post("/api/v1/memory/curate",
                          json={"workspace": "mig", "confirm_ids": ["not-a-doc"]},
                          headers=H)
    assert r.status_code == 422


@pytest.mark.asyncio
async def test_hop_stream_emits_events(client, monkeypatch):
    from mlpal_memory_graph.services import memory_hop as mh

    async def fake_hop(*, query, fetch_packet, max_hops, model, on_event=None):
        await fetch_packet(query)
        if on_event:
            await on_event({"type": "retrieved", "hop": 0, "query": query, "citations": 1})
        return mh.HopResult(answer="streamed [memory://chunk/x]", hops=1, trace=[query])

    monkeypatch.setattr(mh, "run_memory_hop", fake_hop)
    await _ingest(client, "stream lab: the widget frobnicates at level 7", ws="stream")
    async with client.stream(
        "GET", "/api/v1/memory/answer/stream",
        params={"q": "widget frobnicate level", "workspace": "stream"}, headers=H,
    ) as r:
        assert r.status_code == 200
        body = (await r.aread()).decode()
    assert "event: retrieved" in body and "event: answer" in body
    assert "streamed" in body


@pytest.mark.asyncio
async def test_workspace_purge_both_tiers_owner_only(client):
    """DELETE /memory/workspaces/{ws}: forgets a project from BOTH tiers within
    the caller's own personal scope only — the 'experiment residue' fix."""
    await _ingest(client, "the platform costs $52.6 per day in wslab", ws="purge-lab")
    await _ingest(client, "wslab trading insight: buy the dip on catalyst", ws="purge-lab")
    await _ingest(client, "keepme: unrelated durable note elsewhere", ws="keep-lab")

    r = await client.delete("/api/v1/memory/workspaces/purge-lab", headers=H)
    body = r.json()
    assert body["documents"] == 2 and body["chunks"] >= 2
    # both tiers gone from search; other workspace intact
    r = await client.get("/api/v1/memory/search", params={"q": "wslab catalyst dip"},
                         headers=H)
    assert not any("wslab" in p["content"] for p in r.json()["passages"])
    r = await client.get("/api/v1/memory/search", params={"q": "keepme durable note"},
                         headers=H)
    assert any("keepme" in p["content"] for p in r.json()["passages"])
    # audited
    r = await client.get("/api/v1/episodes", params={"source": "governance"}, headers=H)
    assert any(e["action_type"] == "memory.workspace_purged"
               for e in r.json()["episodes"])
    # another user purging the same name touches NOTHING of alice's
    await _ingest(client, "alice second wslab note", ws="purge-lab2")
    r = await client.delete("/api/v1/memory/workspaces/purge-lab2",
                            headers={**H, "X-Test-User-Id": "bob"})
    assert r.json()["documents"] == 0
    r = await client.get("/api/v1/memory/search", params={"q": "alice second wslab"},
                         headers=H)
    assert any("second wslab" in p["content"] for p in r.json()["passages"])
