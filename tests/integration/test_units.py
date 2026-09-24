"""memory v12 §2b: an org defines its own hierarchy; visibility rolls up, oversight is a grant,
publishing goes one level up, the tree refuses cycles and accidental deletes."""

from __future__ import annotations

import pytest

ORG = "acme"
ADMIN = {"X-Test-Org-Id": ORG, "X-Test-User-Id": "root-admin", "X-Test-Permissions": "*"}


def person(user: str) -> dict:
    return {"X-Test-Org-Id": ORG, "X-Test-User-Id": user, "X-Test-Permissions": "memory.read,memory.write"}


async def _unit(client, name, parent_id=None, policy=None, headers=ADMIN):
    r = await client.post("/api/v1/units", json={"name": name, "parent_id": parent_id, "kind": "unit", "policy": policy or {}}, headers=headers)
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def _member(client, unit_id, user, role="member", headers=ADMIN):
    r = await client.post(f"/api/v1/units/{unit_id}/members", json={"user_id": user, "role": role}, headers=headers)
    assert r.status_code == 201, r.text


async def _doc(client, headers, unit_id, title, content):
    r = await client.post("/api/v1/documents", headers=headers,
                          json={"scope": "team", "scope_id": unit_id, "source": "repo_doc", "title": title, "content": content})
    return r


async def _titles(client, headers, q):
    r = await client.get("/api/v1/memory/search", params={"q": q, "limit": 20}, headers=headers)
    assert r.status_code == 200, r.text
    return {p["document_title"] for p in r.json()["passages"]}


@pytest.fixture
async def tree(client):
    eng = await _unit(client, "engineering")
    platform = await _unit(client, "platform", eng, policy={"read_descendants": True})
    squad = await _unit(client, "memory-squad", platform)
    sales = await _unit(client, "sales")
    await _member(client, squad, "alice")
    await _member(client, platform, "bob")
    await _member(client, platform, "carol", role="admin")
    await _member(client, sales, "dan")
    return {"eng": eng, "platform": platform, "squad": squad, "sales": sales}


async def test_visibility_rolls_up_not_down_and_oversight_is_a_grant(client, tree):
    assert (await _doc(client, person("alice"), tree["squad"], "squad-retro", "The memory squad retro: the embedder budget is 8 threads.")).status_code == 202
    assert (await _doc(client, person("bob"), tree["platform"], "platform-plan", "Platform plan: the embedder budget rolls into Q4.")).status_code == 202
    assert (await _doc(client, ADMIN, tree["eng"], "eng-allhands", "Engineering all-hands: embedder budget is frozen.")).status_code == 202
    assert (await _doc(client, person("dan"), tree["sales"], "sales-notes", "Sales notes: the embedder budget question from a customer.")).status_code == 202
    alice = await _titles(client, person("alice"), "embedder budget")
    assert {"squad-retro", "platform-plan", "eng-allhands"} <= alice and "sales-notes" not in alice, alice
    bob = await _titles(client, person("bob"), "embedder budget")
    assert {"platform-plan", "eng-allhands"} <= bob and "squad-retro" not in bob, "a member does not see the squad below"
    carol = await _titles(client, person("carol"), "embedder budget")
    assert "squad-retro" in carol, "an admin with read_descendants sees the subtree"
    dan = await _titles(client, person("dan"), "embedder budget")
    assert dan == {"sales-notes"}, dan


async def test_writing_goes_up_one_level_never_down_or_sideways(client, tree):
    assert (await _doc(client, person("alice"), tree["platform"], "up", "alice publishes upward")).status_code == 202
    assert (await _doc(client, person("alice"), tree["eng"], "up2", "alice publishes to the root of her line")).status_code == 202
    assert (await _doc(client, person("bob"), tree["squad"], "down", "bob cannot write into the squad below")).status_code == 403
    assert (await _doc(client, person("dan"), tree["platform"], "side", "dan is in sales")).status_code == 403
    listing = (await client.get("/api/v1/units", headers=person("alice"))).json()
    assert listing["mine"] == [tree["squad"], tree["platform"], tree["eng"]], "nearest first"
    assert listing["administered"] == [] and listing["admin"] is False
    assert (await client.get("/api/v1/units", headers=ADMIN)).json()["admin"] is True, "a memory admin governs every unit"


async def test_administration_is_by_role_and_the_tree_refuses_cycles_and_accidental_deletes(client, tree):
    # carol administers platform and everything under it; she may add a sub-unit and a member there
    sub = await _unit(client, "search-squad", tree["platform"], headers=person("carol"))
    await _member(client, sub, "erin", headers=person("carol"))
    # but not sales, and not a top-level unit
    r = await client.post("/api/v1/units", json={"name": "x", "parent_id": tree["sales"]}, headers=person("carol"))
    assert r.status_code == 403
    r = await client.post("/api/v1/units", json={"name": "top", "parent_id": None}, headers=person("carol"))
    assert r.status_code == 403
    # a cycle is refused
    r = await client.patch(f"/api/v1/units/{tree['platform']}", json={"move": True, "parent_id": sub}, headers=ADMIN)
    assert r.status_code == 422 and "descendants" in r.json()["detail"]
    # a non-empty unit is not deleted by accident
    r = await client.delete(f"/api/v1/units/{tree['platform']}", headers=ADMIN)
    assert r.status_code == 409
    r = await client.delete(f"/api/v1/units/{sub}", headers=ADMIN)
    assert r.status_code == 409, "it has a member"
    r = await client.delete(f"/api/v1/units/{sub}/members/erin", headers=ADMIN)
    assert r.status_code == 204
    r = await client.delete(f"/api/v1/units/{sub}", headers=ADMIN)
    assert r.status_code == 204
    # a policy typo is refused, a valid policy is stored
    r = await client.patch(f"/api/v1/units/{tree['eng']}", json={"policy": {"read_descendant": True}}, headers=ADMIN)
    assert r.status_code == 422
    r = await client.patch(f"/api/v1/units/{tree['eng']}", json={"policy": {"read_descendants": True, "publish_up": "admins"}}, headers=ADMIN)
    assert r.status_code == 200 and r.json()["policy"]["publish_up"] == "admins"


async def test_unit_membership_is_visible_on_the_next_request(client, tree):
    """The per-request cache must not hide a membership change from the person it concerns."""
    assert await _titles(client, person("frank"), "anything") == set()
    await _member(client, tree["squad"], "frank")
    assert (await _doc(client, person("frank"), tree["squad"], "frank-doc", "frank is in the squad now")).status_code == 202


async def _learning(client, headers, unit_id, key, text):
    env = {"scope": "team", "scope_id": unit_id, "source": "harness_memory", "action_type": "memory.claim",
           "actor": {"user_id": headers["X-Test-User-Id"]}, "content": text,
           "payload": {"kind": "learning", "topic": "infra/learning", "key": key, "value": text, "evidence_ids": ["c1"]}}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=headers)
    assert r.status_code == 202, r.text


async def _fact_id(client, headers, needle):
    r = await client.get("/api/v1/memory/search", params={"q": needle, "type": "Fact", "limit": 10}, headers=headers)
    return next(n["id"] for n in r.json()["nodes"] if needle.split()[0] in n["name"])


async def test_lift_by_policy_rolls_endorsed_learnings_one_level_up_with_a_ledger_row(client, session, tree):
    from mlpal_memory_graph.services.units_lift import lift_by_policy

    alice = person("alice")
    await _learning(client, alice, tree["squad"], "reembed", "Reembed after a model change or the vector leg lies quietly.")
    await _learning(client, alice, tree["squad"], "swap", "Swap on the laptop crashed Postgres thrice; it was the box, not the service.")
    endorsed = await _fact_id(client, alice, "Reembed after")
    r = await client.post("/api/v1/memory/endorse", json={"node_ids": [endorsed]}, headers=alice)
    assert r.status_code == 200 and r.json()["tiers"][endorsed] == "endorsed", r.text
    r = await client.patch(f"/api/v1/units/{tree['squad']}", json={"policy": {"lift": {"tier": "endorsed", "to": "parent"}}}, headers=ADMIN)
    assert r.status_code == 200, r.text

    results = await lift_by_policy(session, org_id=ORG)
    await session.commit()
    squad = next(x for x in results if x.unit_id == tree["squad"])
    assert (squad.lifted, squad.merged) == (1, 0) and squad.target == f"team:{tree['platform']}"
    # bob, a member of platform, now reads the endorsed learning but not the probation one
    bob = await client.get("/api/v1/memory/search", params={"q": "reembed model change vector", "type": "Fact"}, headers=person("bob"))
    names = [n["name"] for n in bob.json()["nodes"]]
    assert any("Reembed after" in n for n in names), names
    bob2 = await client.get("/api/v1/memory/search", params={"q": "swap laptop postgres", "type": "Fact"}, headers=person("bob"))
    assert not any("Swap on the laptop" in n["name"] for n in bob2.json()["nodes"]), "probation learnings do not roll up"
    lifted = next(n for n in bob.json()["nodes"] if "Reembed after" in n["name"])
    assert lifted["props"]["lifted_from"] == tree["squad"] and lifted["status"] == "published"
    # a ledger row says so, in the target unit's scope (a member of platform sees it; the browse
    # listing scopes team rows to members, so a tenant admin outside the tree does not)
    r = await client.get("/api/v1/episodes", params={"source": "units"}, headers=person("bob"))
    rows = [e for e in r.json()["episodes"] if e["action_type"] == "memory.lifted"]
    assert rows and rows[0]["scope_id"] == tree["platform"], r.json()
    # a second night merges, never duplicates
    again = await lift_by_policy(session, org_id=ORG)
    await session.commit()
    squad2 = next(x for x in again if x.unit_id == tree["squad"])
    assert (squad2.lifted, squad2.merged) == (0, 1)


async def test_publish_up_admins_and_hops_may_write_are_enforced(client, tree):
    # engineering only takes publishes from admins below it
    r = await client.patch(f"/api/v1/units/{tree['eng']}", json={"policy": {"publish_up": "admins"}}, headers=ADMIN)
    assert r.status_code == 200
    assert (await _doc(client, person("alice"), tree["eng"], "a", "alice is a member, not an admin")).status_code == 403
    assert (await _doc(client, person("carol"), tree["eng"], "c", "carol administers platform, below eng")).status_code == 202
    assert (await _doc(client, person("alice"), tree["platform"], "p", "platform still takes members")).status_code == 202
    # the squad refuses writes that arrive under a HOP; a person's write still lands
    r = await client.patch(f"/api/v1/units/{tree['squad']}", json={"policy": {"hops_may_write": False}}, headers=ADMIN)
    assert r.status_code == 200
    hop_headers = {**person("alice"), "X-Hop": "infra@0.3.1", "X-Run-Id": "r-1"}
    assert (await _doc(client, hop_headers, tree["squad"], "h", "a HOP writing into the squad")).status_code == 403
    assert (await _doc(client, person("alice"), tree["squad"], "h2", "alice herself writing into the squad")).status_code == 202
    assert (await _doc(client, hop_headers, tree["platform"], "h3", "the HOP may still write to platform")).status_code == 202


async def test_unit_notes_and_unit_governance_follow_the_tree(client, tree):
    body = "## Now\n- squad note\n\n## Decisions\n\n## Open threads\n\n## Preferences\n\n## Pointers\n"
    r = await client.put(f"/api/v1/notes/team/{tree['squad']}", params={"workspace": "demo"}, json={"body": body}, headers=person("alice"))
    assert r.status_code == 200, r.text
    assert (await client.get(f"/api/v1/notes/team/{tree['squad']}", params={"workspace": "demo"}, headers=person("carol"))).status_code == 200, "admin above reads it"
    assert (await client.get(f"/api/v1/notes/team/{tree['squad']}", params={"workspace": "demo"}, headers=person("bob"))).status_code == 404, "a member above without the grant does not"
    assert (await client.get(f"/api/v1/notes/team/{tree['squad']}", params={"workspace": "demo"}, headers=person("dan"))).status_code == 404
    mine = (await client.get("/api/v1/notes", headers=person("alice"))).json()["notes"]
    assert any(n["scope"] == "team" and n["scope_id"] == tree["squad"] for n in mine)
    # consent on a unit: its admin may set it, a plain member may not, sales' member may not
    r = await client.put("/api/v1/memory/consent", json={"scope": "team", "scope_id": tree["squad"], "state": "off"}, headers=person("carol"))
    assert r.status_code == 200, r.text
    r = await client.put("/api/v1/memory/consent", json={"scope": "team", "scope_id": tree["squad"], "state": "active"}, headers=person("alice"))
    assert r.status_code == 403
    r = await client.put("/api/v1/memory/consent", json={"scope": "team", "scope_id": tree["squad"], "state": "active"}, headers=person("dan"))
    assert r.status_code == 403


async def test_upload_is_the_same_ingest_as_a_posted_document(client, tree):
    md = b"# Runbook\n\nThe on-call rotation restarts on the first Monday; the pager handoff is at 09:00 UTC.\n"
    r = await client.post("/api/v1/documents/upload", headers=person("alice"),
                          files={"file": ("runbook.md", md, "text/markdown")},
                          data={"scope": "team", "scope_id": tree["squad"], "workspace": "demo"})
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "processed"
    titles = await _titles(client, person("alice"), "pager handoff rotation")
    assert "runbook.md" in titles
    r = await client.post("/api/v1/documents/upload", headers=person("alice"), files={"file": ("photo.png", b"\x89PNG....", "image/png")})
    assert r.status_code == 415
    r = await client.post("/api/v1/documents/upload", headers=person("dan"), files={"file": ("x.md", b"# x\nsideways", "text/markdown")},
                          data={"scope": "team", "scope_id": tree["squad"]})
    assert r.status_code == 403


async def test_pdf_upload_extracts_text(client):
    from pathlib import Path

    pdf = Path(__file__).resolve().parent.parent / "fixtures" / "budget.pdf"
    r = await client.post("/api/v1/documents/upload", headers=ADMIN, files={"file": ("budget.pdf", pdf.read_bytes(), "application/pdf")},
                          data={"workspace": "finance"})
    assert r.status_code == 202, r.text
    titles = await _titles(client, ADMIN, "budget freeze November")
    assert "budget.pdf" in titles
