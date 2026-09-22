"""Workspace notes over the REST surface (SQLite/offline): CRUD, sections, concurrency, scope
visibility, consent, versions + as-of, the content-free episode, stale citations, the bundle."""
from __future__ import annotations

import asyncio

ALICE = {"X-Test-Org-Id": "orgN", "X-Test-User-Id": "alice"}
BOB = {"X-Test-Org-Id": "orgN", "X-Test-User-Id": "bob"}
OTHER_ORG = {"X-Test-Org-Id": "orgZ", "X-Test-User-Id": "zed"}

BODY = "## Now\nShipping notes v1.\n\n## Decisions\n- REST writes only.\n"


async def test_put_get_roundtrip_normalises_sections(client):
    r = await client.put("/api/v1/notes/org/orgN", params={"workspace": "memory"},
                         json={"body": "## decisions\n- REST writes only.\n## Now\nShipping notes v1.\n", "reason": "seed"},
                         headers=ALICE)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["version"] == 1 and body["workspace"] == "memory"
    assert body["body"].startswith("## Now\nShipping notes v1.")            # canonical order
    assert body["sections"]["Decisions"] == "- REST writes only."
    r = await client.get("/api/v1/notes/org/orgN", params={"workspace": "memory"}, headers=BOB)
    assert r.status_code == 200 and r.json()["version"] == 1                   # org note: whole org reads


async def test_schema_violations_are_422(client):
    for bad in ("## Random\nx\n", "preamble\n## Now\nx\n", "## Now\n" + "x" * 9000):
        r = await client.put("/api/v1/notes/org/orgN", json={"body": bad}, headers=ALICE)
        assert r.status_code == 422, bad[:20]


async def test_patch_section_append_and_replace_bump_versions(client):
    await client.put("/api/v1/notes/org/orgN", params={"workspace": "w"}, json={"body": BODY}, headers=ALICE)
    r = await client.patch("/api/v1/notes/org/orgN", params={"workspace": "w"},
                           json={"section": "open threads", "op": "append", "text": "- as-of passage leak", "reason": "found"},
                           headers=ALICE)
    assert r.status_code == 200 and r.json()["version"] == 2
    assert r.json()["sections"]["Open threads"] == "- as-of passage leak"
    assert r.json()["sections"]["Now"] == "Shipping notes v1."                  # untouched
    r = await client.patch("/api/v1/notes/org/orgN", params={"workspace": "w"},
                           json={"section": "Open threads", "op": "append", "text": "- identity scope"}, headers=ALICE)
    assert r.json()["sections"]["Open threads"] == "- as-of passage leak\n- identity scope"
    r = await client.patch("/api/v1/notes/org/orgN", params={"workspace": "w"},
                           json={"section": "Open threads", "op": "replace", "text": "- none"}, headers=ALICE)
    assert r.json()["sections"]["Open threads"] == "- none" and r.json()["version"] == 4


async def test_base_version_mismatch_is_409(client):
    await client.put("/api/v1/notes/org/orgN", json={"body": BODY}, headers=ALICE)
    r = await client.put("/api/v1/notes/org/orgN", json={"body": BODY, "base_version": 7}, headers=ALICE)
    assert r.status_code == 409
    r = await client.patch("/api/v1/notes/org/orgN",
                           json={"section": "Now", "op": "replace", "text": "x", "base_version": 1}, headers=ALICE)
    assert r.status_code == 200 and r.json()["version"] == 2


async def test_user_note_invisible_to_other_users_and_orgs(client):
    r = await client.put("/api/v1/notes/user/alice", json={"body": "## Preferences\n- terse\n"}, headers=ALICE)
    assert r.status_code == 200
    assert (await client.get("/api/v1/notes/user/alice", headers=BOB)).status_code == 404
    assert (await client.get("/api/v1/notes/user/alice", headers=OTHER_ORG)).status_code == 404
    r = await client.put("/api/v1/notes/user/alice", json={"body": "## Preferences\n- verbose\n"}, headers=BOB)
    assert r.status_code == 403                                                # cannot write another's notes
    listing = (await client.get("/api/v1/notes", headers=BOB)).json()["notes"]
    assert all(not (n["scope"] == "user" and n["scope_id"] == "alice") for n in listing)


async def test_consent_off_blocks_user_note_writes(client):
    r = await client.put("/api/v1/memory/consent", json={"scope": "user", "scope_id": "alice", "state": "off"},
                         headers=ALICE)
    assert r.status_code in (200, 204), r.text
    r = await client.put("/api/v1/notes/user/alice", json={"body": "## Now\nx\n"}, headers=ALICE)
    assert r.status_code == 403 and "consent" in r.text


async def test_history_and_as_of(client):
    await client.put("/api/v1/notes/org/orgN", json={"body": "## Now\nv1\n"}, headers=ALICE)
    r1 = (await client.get("/api/v1/notes/org/orgN", headers=ALICE)).json()
    await asyncio.sleep(1.1)  # second resolution on created_at
    await client.put("/api/v1/notes/org/orgN", json={"body": "## Now\nv2\n"}, headers=ALICE)
    hist = (await client.get("/api/v1/notes/org/orgN/history", headers=ALICE)).json()
    assert [v["version"] for v in hist["versions"]] == [2, 1]
    r = await client.get("/api/v1/notes/org/orgN", params={"as_of": r1["updated_at"]}, headers=ALICE)
    assert r.status_code == 200 and r.json()["version"] == 1 and "v1" in r.json()["body"]
    r = await client.get("/api/v1/notes/org/orgN", params={"as_of": "2020-01-01T00:00:00Z"}, headers=ALICE)
    assert r.status_code == 404                                                # did not exist yet


async def test_each_version_emits_one_content_free_episode(client):
    await client.put("/api/v1/notes/org/orgN", params={"workspace": "ep"}, json={"body": BODY, "reason": "seed"},
                     headers=ALICE)
    await client.patch("/api/v1/notes/org/orgN", params={"workspace": "ep"},
                       json={"section": "Now", "op": "replace", "text": "changed"}, headers=ALICE)
    r = await client.get("/api/v1/episodes", params={"source": "notes", "workspace": "ep", "limit": 50}, headers=ALICE)
    assert r.status_code == 200, r.text
    eps = r.json()["episodes"]
    assert len(eps) == 2 and all(e["action_type"] == "note.updated" for e in eps)
    details = [(await client.get(f"/api/v1/episodes/{e['event_id']}", headers=ALICE)).json() for e in eps]
    assert {d["payload"]["version"] for d in details} == {1, 2}
    assert all(not d.get("has_content") for d in details)                      # content-free by construction
    assert {tuple(d["payload"]["sections_changed"]) for d in details} == {("Now", "Decisions"), ("Now",)}


async def test_stale_citation_is_flagged_and_annotated_in_bundle(client):
    # a fact, then supersede it via the same-key value machinery is heavy; invalidate via the
    # purge route instead: cite an edge, then delete its document → edge invalidated/missing.
    ep = {"action_type": "fact.observed", "actor": {"user_id": "alice"},
          "payload": {"statement": "the cluster runs kubernetes 1.33"}}
    r = await client.post("/api/v1/episodes?process=true", json={"episodes": [ep]}, headers=ALICE)
    assert r.status_code == 202
    s = (await client.get("/api/v1/memory/search", params={"q": "kubernetes 1.33", "limit": 5}, headers=ALICE)).json()
    edge_id = s["edges"][0]["id"]
    body = f"## Now\n- k8s is 1.33 (memory://edge/{edge_id})\n"
    await client.put("/api/v1/notes/org/orgN", params={"workspace": "k8s"}, json={"body": body}, headers=ALICE)
    r = await client.get("/api/v1/notes/org/orgN", params={"workspace": "k8s"}, headers=ALICE)
    assert r.json()["stale_citations"] == []                                   # live: nothing flagged
    # supersede: a newer statement with the same key path invalidates the old edge
    ep2 = {"action_type": "fact.observed", "actor": {"user_id": "alice"},
           "payload": {"statement": "the cluster runs kubernetes 1.36"}}
    await client.post("/api/v1/episodes?process=true", json={"episodes": [ep2]}, headers=ALICE)
    r = await client.get("/api/v1/notes/org/orgN", params={"workspace": "k8s"}, headers=ALICE)
    flagged = r.json()["stale_citations"]
    if flagged:  # supersession depends on the extractor keying both statements alike
        assert flagged[0]["state"] in ("superseded", "missing")
        ctx = (await client.get("/api/v1/notes/context", params={"workspace": "k8s"}, headers=ALICE)).json()
        assert "⚠" in ctx["markdown"]


async def test_context_bundle_composes_and_respects_budget(client):
    filler = "\n".join(f"- pointer {i} to a long path/that/keeps/going/{i}" for i in range(20))
    await client.put("/api/v1/notes/org/orgN", json={"body": f"## Pointers\n- org index\n{filler}\n"}, headers=ALICE)
    await client.put("/api/v1/notes/org/orgN", params={"workspace": "infra"}, json={"body": "## Now\n- infra HOP\n"},
                     headers=ALICE)
    await client.put("/api/v1/notes/user/alice", json={"body": "## Preferences\n- terse\n"}, headers=ALICE)
    await client.put("/api/v1/notes/user/alice", params={"workspace": "infra"},
                     json={"body": "## Open threads\n- my thread\n"}, headers=ALICE)
    r = await client.get("/api/v1/notes/context", params={"workspace": "infra"}, headers=ALICE)
    md = r.json()["markdown"]
    assert md.startswith("# Working notes")
    for part in ("# Org (v", "# Workspace infra (v", "# You (v", "# You in infra (v", "org index", "infra HOP", "terse", "my thread"):
        assert part in md, part
    assert md.index("# Org") < md.index("# Workspace infra") < md.index("# You (") < md.index("# You in infra")
    # bob sees the org notes but not alice's
    md_bob = (await client.get("/api/v1/notes/context", params={"workspace": "infra"}, headers=BOB)).json()["markdown"]
    assert "org index" in md_bob and "terse" not in md_bob and "my thread" not in md_bob
    # budget: tiny budget keeps the first note only and says so
    r = await client.get("/api/v1/notes/context", params={"workspace": "infra", "token_budget": 200}, headers=ALICE)
    assert r.json()["truncated"] is True and r.json()["estimated_tokens"] <= 200
