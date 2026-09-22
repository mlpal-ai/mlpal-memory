"""d11.6 / d11.7 (memory v7 WP1): the service accepts the engine's newer contracts instead of dropping
them into the legacy branch. d11.6 adds memory_projection counts; d11.7 adds tool_calls counts."""

from __future__ import annotations

import pytest

from mlpal_memory_graph.ingest.plugins.harness_telemetry import TelemetryContractError, normalize_run_outcome


def _event(contract: str, **extra) -> dict:
    return {"action_type": "run.completed", "contract": contract, "scope_id": "infra", "occurred_at": "2026-09-17T03:17:00Z",
            "payload": {"hop": {"name": "infra-ro", "version": "0.3.0"}, "model": "m", "task_type": "watch", "run_result": "success",
                        "failure_class": None, "wall_ms": 27000, "turns": 5, "tier": "frontier", "role": "main", "run_id": "r-1",
                        "tokens": {"input": 1, "output": 1},
                        "checks": {"self_check": {"fired": False}, "anti_churn": {"fired": False}, "observe": {"ran": True, "passed": True}, "agent": {"verdict": None}},
                        **extra}}


def test_d116_keeps_checks_run_id_and_memory_projection():
    env = normalize_run_outcome(_event("d11.6", memory_projection={"fact_count": 4, "estimated_tokens": 284, "truncated": False},
                                       memories_injected=["ev-1"]), user_id="u")
    p = env.payload
    assert p["contract"] == "d11.6" and p["run_id"] == "r-1" and p["wall_ms"] == 27000 and "checks" in p
    assert p["memory_projection"] == {"fact_count": 4, "estimated_tokens": 284, "truncated": False}
    assert p["memories_injected"] == ["ev-1"]


def test_d117_carries_tool_call_counts():
    env = normalize_run_outcome(_event("d11.7", tool_calls={"Bash": 2, "mcp__memory__memory_search": 1}), user_id="u")
    assert env.payload["tool_calls"] == {"Bash": 2, "mcp__memory__memory_search": 1}


def test_d117_rejects_malformed_tool_calls():
    with pytest.raises(TelemetryContractError):
        normalize_run_outcome(_event("d11.7", tool_calls={"Bash": "two"}), user_id="u")
    with pytest.raises(TelemetryContractError):
        normalize_run_outcome(_event("d11.6", memory_projection={"fact_count": "4"}), user_id="u")


def test_d115_does_not_admit_newer_fields():
    env = normalize_run_outcome(_event("d11.5", tool_calls={"Bash": 2}, memory_projection={"fact_count": 1, "estimated_tokens": 1, "truncated": False}), user_id="u")
    assert "tool_calls" not in env.payload and "memory_projection" not in env.payload
