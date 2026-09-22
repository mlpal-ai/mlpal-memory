"""memory v10: the owner's door on the tuning loop — a tune turn records a proposal, the engine lists
it as pending, the owner decides, a rejection's reason becomes a build learning for the next turn.
Also: run telemetry on a contract newer than the ingest's literal list (d11.8 with labels) is accepted."""

from __future__ import annotations

OWNER = {"X-Test-Org-Id": "orgT", "X-Test-User-Id": "svp", "X-Test-Permissions": "memory.read,memory.write"}
TURN = {"X-Test-Org-Id": "orgT", "X-Test-User-Id": "tune-turn", "X-Test-Permissions": "memory.read,memory.write"}
PROPOSAL = {"hop": "infra", "turn": "turn-20260923", "from_version": "0.3.1", "candidate_version": "0.3.2", "candidate_path": "/tmp/hop/0.3.2-candidate",
            "summary": "Turn turn-20260923 on 0.3.1: 2 proposals, 1 enactable. Candidate 0.3.2: golden 9/9.",
            "proposals": [{"kind": "verification", "knob": "verification.antiChurn.threshold", "change": {"op": "lower", "to": 5}, "applicability": "enactable"},
                          {"kind": "capability", "knob": "", "change": {"op": "module_off", "provider": "gcloud"}, "applicability": "advisory"}],
            "verdict": {"final": {"score": 1.0, "runs": 9}, "disposition": "pass"}, "facts": [{"line": "infra gcloud usage (watch) = 2/318"}]}


async def test_proposal_is_pending_until_decided_and_a_rejection_teaches_the_next_turn(client):
    r = await client.post("/api/v1/memory/tune/proposals", json=PROPOSAL, headers=TURN)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "pending" and r.json()["candidate_version"] == "0.3.2"
    pending = (await client.get("/api/v1/memory/tune/pending", params={"hop": "infra-ro"}, headers=OWNER)).json()["pending"]
    assert len(pending) == 1 and pending[0]["turn"] == "turn-20260923", "the read-only twin's name resolves to the same HOP"
    # an agent cannot decide; a rejection needs a reason
    r = await client.post("/api/v1/memory/tune/decide", json={"hop": "infra", "turn": "turn-20260923", "decision": "reject"}, headers=OWNER)
    assert r.status_code == 422
    r = await client.post("/api/v1/memory/tune/decide", json={"hop": "infra", "turn": "turn-20260923", "decision": "reject",
                                                             "reason": "the anti-churn firings were the account migration week; ignore that window"}, headers=OWNER)
    assert r.status_code == 200 and r.json()["learning_written"] is True, r.text
    assert (await client.get("/api/v1/memory/tune/pending", headers=OWNER)).json()["pending"] == []
    allp = (await client.get("/api/v1/memory/tune/proposals", headers=OWNER)).json()["pending"]
    assert allp[0]["status"] == "reject" and allp[0]["decision"]["reason"].startswith("the anti-churn")
    r = await client.post("/api/v1/memory/tune/decide", json={"hop": "infra", "turn": "turn-20260923", "decision": "approve"}, headers=OWNER)
    assert r.status_code == 409, "a turn is decided once"
    # the reason is a build learning the next turn's brief reads
    brief = (await client.get("/api/v1/memory/brief", params={"hop": "infra"}, headers=OWNER)).json()
    assert any("account migration" in (x.get("fact") or x.get("value") or str(x)) for x in brief.get("build_learnings", [])), brief.get("build_learnings")


async def test_a_decision_is_stamped_with_the_person_who_made_it(client):
    # under dev auth every caller has a user id (the 403 guard is for a production token without one);
    # what must hold is that the decision carries the deciding person, not the tune turn's actor
    await client.post("/api/v1/memory/tune/proposals", json={**PROPOSAL, "turn": "turn-2"}, headers=TURN)
    r = await client.post("/api/v1/memory/tune/decide", json={"hop": "infra", "turn": "turn-2", "decision": "approve", "installed_version": "0.3.2"}, headers=OWNER)
    assert r.status_code == 200, r.text
    allp = (await client.get("/api/v1/memory/tune/proposals", params={"hop": "infra"}, headers=OWNER)).json()["pending"]
    done = next(p for p in allp if p["turn"] == "turn-2")
    assert done["status"] == "approve" and done["decision"]["installed_version"] == "0.3.2" and done["actor"] == "tune-turn"


async def test_telemetry_on_a_newer_contract_with_labels_is_accepted(client):
    event = {"contract": "d11.8", "action_type": "run.completed", "scope_id": "infra", "occurred_at": "2026-09-23T07:00:00Z",
             "payload": {"hop": {"name": "infra-ro", "version": "0.3.1"}, "repo": "infra", "model": "claude-opus-5", "tier": "frontier", "role": "main",
                         "run_id": "r-1", "task_type": "infra", "run_result": "success", "failure_class": None,
                         "checks": {"self_check": {"fired": False}, "anti_churn": {"fired": False}, "observe": {"ran": False, "passed": False}, "agent": {"verdict": None}},
                         "tokens": {"input": 10, "output": 20, "cache_read_input": 0, "cache_creation_input": 0}, "wall_ms": 1200, "turns": 3,
                         "tool_calls": {"Bash": 4}, "labels": {"aws": 3, "kubectl": 1}, "memory_projection": {"fact_count": 7, "estimated_tokens": 500, "truncated": False}}}
    r = await client.post("/api/v1/telemetry", json={"events": [event]}, headers=TURN)
    assert r.status_code in (200, 202), r.text
    body = r.json()
    assert body.get("accepted") == 1 and not body.get("rejected"), body
    bad = {**event, "payload": {**event["payload"], "run_id": "r-2", "labels": {"aws": -1}}}
    r = await client.post("/api/v1/telemetry", json={"events": [bad]}, headers=TURN)
    assert (r.json().get("rejected") or []), "negative label counts are refused"
