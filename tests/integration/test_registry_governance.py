"""memory v7 WP5–WP10 (service side): the HOP registry and the index across HOP memories (route), the
builder's brief with build-phase topics, ontology extension entities with alias resolution, a member's
departure with provenance pointers and a certificate, fleet aggregates, the identity line."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mlpal_memory_graph.db.models import Episode, Node

H = {"X-Test-Org-Id": "orgR", "X-Test-User-Id": "priya", "X-Test-Permissions": "memory.read,memory.write"}
ADMIN = {**H, "X-Test-Permissions": "*"}
INFRA = {"version": "0.3.0", "owner": "priya", "mode": "review", "sharing": "none", "tenant": "orgR", "workspace": "infra",
         "topics": [{"id": "infra/state/cost-daily", "kind": "state", "key": "date", "read": "company", "inject": True},
                    {"id": "infra/state/watch/{target}", "kind": "state", "key": "target", "read": "company"},
                    {"id": "infra/learning", "kind": "learning", "key": "dedup", "read": "company"},
                    {"id": "build/infra/state", "kind": "state", "key": "version", "read": "owner", "phase": "build"},
                    {"id": "build/infra/learning", "kind": "learning", "key": "dedup", "read": "owner", "phase": "build"}],
         "reads": ["company/ownership"], "writes": ["infra/*", "build/infra/*"],
         "ontology": [{"name": "Volume", "parent": "Artifact", "description": "an EBS volume"}, {"name": "Cluster", "parent": "Artifact"}]}
STATUS = {"version": "0.1.0", "owner": "cs-lead", "mode": "auto", "workspace": "status",
          "topics": [{"id": "status/state/merchant-facing", "kind": "state", "key": "component", "read": "company"}],
          "reads": ["infra/state/watch/*"], "writes": ["status/*"]}


async def _claim(client, kind, topic, key, value, headers=H, **extra):
    env = {"scope": "org", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": headers["X-Test-User-Id"]}, "content": str(value),
           "payload": {"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": ["c1"], **extra}}
    r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=headers)
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_registry_routes_questions_across_hops(client):
    assert (await client.put("/api/v1/memory/hops/infra", json=INFRA, headers=H)).status_code == 200
    assert (await client.put("/api/v1/memory/hops/status", json=STATUS, headers=H)).status_code == 200
    hops = (await client.get("/api/v1/memory/hops", headers=H)).json()
    assert [h["name"] for h in hops["hops"]] == ["infra", "status"] and hops["hops"][0]["mode"] == "review"
    assert set(hops["ontology_extension"]) == {"Volume", "Cluster"} and hops["ontology_extension"]["Volume"]["hop"] == "infra"
    r = (await client.get("/api/v1/memory/route", params={"q": "is the merchant facing status component healthy"}, headers=H)).json()
    assert r["routes"][0]["hop"] == "status" and r["routes"][0]["topic"] == "status/state/merchant-facing", r
    r = (await client.get("/api/v1/memory/route", params={"q": "what did the cost daily state say"}, headers=H)).json()
    assert r["routes"][0]["topic"] == "infra/state/cost-daily", r
    # the keys a topic holds route a question that never names the topic
    await _claim(client, "state", "infra/state/watch/{target}", "web/hop-sandbox", "2/2 ready")
    r = (await client.get("/api/v1/memory/route", params={"q": "is the web deployment in hop-sandbox healthy"}, headers=H)).json()
    assert r["routes"] and r["routes"][0]["topic"] == "infra/state/watch/{target}" and "web" in r["routes"][0]["matched_keys"], r


@pytest.mark.asyncio
async def test_build_phase_feeds_the_brief_not_the_session(client, session):
    await client.put("/api/v1/memory/hops/infra", json=INFRA, headers=H)
    await _claim(client, "state", "build/infra/state", "0.3.0", "golden 57/59; promoted 2026-09-17", phase="build")
    await _claim(client, "learning", "build/infra/learning", "dedup", "Users asked for cost as a table with namespaces first; the 0.2.0 prose mail was ignored.", phase="build")
    await _claim(client, "learning", "infra/learning", "dedup", "Pass --context=mlpal-new-eks to kubectl; the default points at the old account.")
    ev = {"action_type": "hop.eval_scored", "scope": "repo", "scope_id": "infra", "source": "harness_telemetry", "actor": {"user_id": "priya"},
          "payload": {"hop": {"name": "infra"}, "to_version": "0.3.0", "eval": {"suite_digest": "sha256:abc", "score": 0.966, "pass_bar": 1.0, "runs": 59, "eval_run_id": "abc"}, "decision": "adopted"}}
    assert (await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [ev]}, headers=ADMIN)).status_code == 202
    b = (await client.get("/api/v1/memory/brief", params={"hop": "infra"}, headers=H)).json()
    assert b["registry"]["mode"] == "review" and b["build_topics"] == ["build/infra/state", "build/infra/learning"]
    assert any(x["key"] == "0.3.0" for x in b["build_state"]) and any("namespaces first" in x["fact"] for x in b["build_learnings"])
    assert b["scores"] and b["scores"][0]["version"] == "0.3.0" and b["scores"][0]["decision"] == "adopted"
    assert any("mlpal-new-eks" in x["fact"] for x in b["company"]) and "## Versions scored" in b["markdown"]
    pr = (await client.get("/api/v1/memory/projection", params={"hop": "infra", "workspace": "infra"}, headers=H)).json()["markdown"]
    assert pr.startswith("# Memory\n_for priya · orgR_") and "namespaces first" not in pr and "mlpal-new-eks" in pr, pr


@pytest.mark.asyncio
async def test_extension_entities_resolve_by_alias(client, session):
    await client.put("/api/v1/memory/hops/infra", json=INFRA, headers=H)
    await _claim(client, "state", "infra/state/watch/{target}", "vol-0c493e116e469dfa9", "in-use; 40 GiB; staging-db-data",
                 entities=[{"type": "Volume", "key": "vol-0c493e116e469dfa9", "name": "staging-db-data", "aliases": ["staging-db-data"]}])
    await _claim(client, "learning", "infra/learning", "dedup", "The staging-db-data volume is backed up nightly by the platform job.",
                 entities=[{"type": "Volume", "key": "staging-db-data", "aliases": ["vol-0c493e116e469dfa9"]}])
    vols = (await session.execute(select(Node).where(Node.org_id == "orgR", Node.type == "Volume"))).scalars().all()
    assert len(vols) == 1 and "staging-db-data" in (vols[0].props or {}).get("also_known_as", []), [(v.key, v.props) for v in vols]
    r = await client.get("/api/v1/memory/search", params={"q": "staging-db-data volume backed up", "type": "Volume"}, headers=H)
    assert any(n["type"] == "Volume" for n in r.json()["nodes"])


@pytest.mark.asyncio
async def test_departure_keeps_lifted_facts_with_a_pointer_and_issues_a_certificate(client, session):
    marco = {**H, "X-Test-User-Id": "marco"}
    L = "The reconciler service is owned by Backend; page marco's rota for incidents."
    env = {"scope": "user", "scope_id": "marco", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "marco"}, "content": L,
           "payload": {"kind": "learning", "topic": "infra/learning", "key": "dedup", "value": L, "evidence_ids": ["m1"]}}
    assert (await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [env]}, headers=marco)).status_code == 202
    mine = [n for n in (await client.get("/api/v1/memory/search", params={"q": "reconciler service owned Backend rota", "type": "Fact"}, headers=marco)).json()["nodes"] if "reconciler" in n["name"]]
    assert mine
    pub = await client.post("/api/v1/memory/publish", json={"node_ids": [mine[0]["id"]], "scope": "org"}, headers=marco)
    assert pub.status_code == 200 and pub.json()["published"] == 1, pub.text
    # departure needs an admin
    assert (await client.post("/api/v1/memory/depart", json={"user_id": "marco", "reason": "left 2026-09-17"}, headers=H)).status_code == 403
    cert = (await client.post("/api/v1/memory/depart", json={"user_id": "marco", "reason": "left 2026-09-17"}, headers=ADMIN)).json()
    assert cert["schema"] == "memory/departure-certificate-v1" and cert["purged"]["nodes"] >= 1 and cert["lifted_facts_kept_with_pointer"] == 1 and len(cert["sha256"]) == 64
    left = (await session.execute(select(Node).where(Node.org_id == "orgR", Node.scope == "user", Node.scope_id == "marco"))).scalars().all()
    assert left == []
    shared = [n for n in (await client.get("/api/v1/memory/search", params={"q": "reconciler service owned Backend rota", "type": "Fact"}, headers=H)).json()["nodes"] if "reconciler" in n["name"]]
    assert shared and shared[0]["props"]["provenance"]["from"] == "departed member"
    rows = (await session.execute(select(Episode).where(Episode.org_id == "orgR", Episode.action_type == "memory.departed"))).scalars().all()
    assert len(rows) == 1 and rows[0].payload["sha256"] == cert["sha256"]


@pytest.mark.asyncio
async def test_fleet_aggregates_need_two_companies_and_carry_no_tenant(client, session):
    from mlpal_memory_graph.tools.fleet_aggregate import compute

    def run(org, result, fc):
        return Episode(event_id=f"fleet-{org}-{result}-{fc}", occurred_at=datetime.now(UTC), org_id=org, scope="repo", scope_id="infra", lifecycle="committed",
                       actor={}, source="harness_telemetry", action_type="run.completed", subject={},
                       payload={"hop": {"name": "infra-ro", "version": "0.3.0"}, "run_result": result, "failure_class": fc, "tier": "frontier", "run_id": f"r-{org}-{fc}"},
                       processed=True, tier="deterministic")
    session.add_all([run("companyA", "success", None), run("companyA", "error", "other")])
    await session.commit()
    out = await compute(30, 2)
    assert out["written"] == []   # one company is not a fleet
    session.add_all([run("companyB", "success", None), run("companyB", "error", "policy_denied")])
    await session.commit()
    out = await compute(30, 2)
    assert out["written"] and out["written"][0]["companies"] == 2
    facts = (await client.get("/api/v1/memory/fleet", params={"hop": "infra"}, headers=H)).json()["facts"]
    assert facts and "companyA" not in facts[0]["fact"] and "2 companies" in facts[0]["fact"]
    b = (await client.get("/api/v1/memory/brief", params={"hop": "infra"}, headers=H)).json()
    assert b["fleet"]
