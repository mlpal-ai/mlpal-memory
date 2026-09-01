"""POST /api/v1/telemetry — the HTTP entry point for D11.x events."""

from __future__ import annotations

import pytest

H = {"X-Test-Org-Id": "x12-arm-a", "X-Test-User-Id": "harness",
     "X-Test-Permissions": "memory.write"}


def _d112(event_id: str, result="success", fc=None, **extra):
    return {
        "contract": "d11.2", "action_type": "run.completed", "event_id": event_id,
        "scope_id": "acme-widgets", "occurred_at": "2026-09-01T20:14:13.375Z",
        "payload": {
            "hop": {"name": "joint-memx", "version": "1.0.0"}, "repo": "acme-widgets",
            "model": "claude-opus-5", "tier": "frontier", "task_type": "joint-memx",
            "run_result": result, "failure_class": fc,
            "checks": {"self_check": {"fired": False}, "anti_churn": {"fired": False},
                       "observe": {"ran": False, "passed": False}, "agent": {"verdict": None}},
            "tokens": {"input": 2100, "output": 460, "cache_read_input": 800, "cache_creation_input": 100},
            "wall_ms": 52, "turns": 2, **extra,
        },
    }


@pytest.mark.asyncio
async def test_real_d112_events_land_as_episodes(client):
    r = await client.post("/api/v1/telemetry", json={"events": [
        _d112("e1"), _d112("e2", result="max_turns", fc="step_budget_stall"),
    ]}, headers=H)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body == {"accepted": 2, "duplicates": 0, "rejected": []}
    # idempotent by event_id
    r2 = await client.post("/api/v1/telemetry", json={"events": [_d112("e1")]}, headers=H)
    assert r2.json()["duplicates"] == 1
    # pinned to the caller's org, source harness_telemetry
    lst = await client.get("/api/v1/episodes", params={"source": "harness_telemetry"},
                           headers={**H, "X-Test-Permissions": "memory.read,memory.write"})
    assert lst.status_code == 200


@pytest.mark.asyncio
async def test_contract_violations_are_rejected_loudly_not_coerced(client):
    bad = _d112("e3", result="success", fc="other")          # null-iff-success broken
    bad_unit = _d112("e4"); bad_unit["payload"]["wall_s"] = 5  # unit drift under d11.2
    r = await client.post("/api/v1/telemetry", json={"events": [bad, _d112("e5"), bad_unit]}, headers=H)
    assert r.status_code == 202
    body = r.json()
    assert body["accepted"] == 1
    idx = sorted(x["index"] for x in body["rejected"])
    assert idx == [0, 2]
    reasons = " | ".join(x["reason"] for x in body["rejected"])
    assert "failure_class" in reasons and "wall_s" in reasons


@pytest.mark.asyncio
async def test_ledger_entries_route_by_action_type(client):
    r = await client.post("/api/v1/telemetry", json={"events": [{
        "action_type": "hop.eval_scored", "event_id": "l1",
        "payload": {"hop": {"name": "joint-memx"}, "to_version": "1.0.1",
                    "eval": {"suite_digest": "abc", "score": 900.0, "pass_bar": 0, "runs": 2,
                             "eval_run_id": "abc"}, "decision": "adopted", "proposed_by": "hop-optimizer"},
    }]}, headers=H)
    assert r.status_code == 202 and r.json()["accepted"] == 1, r.text


@pytest.mark.asyncio
async def test_write_permission_required(client):
    r = await client.post("/api/v1/telemetry", json={"events": [_d112("e9")]},
                          headers={**H, "X-Test-Permissions": "memory.read"})
    assert r.status_code == 403
