"""D11.1 harness-telemetry contract fixtures — the shapes telemetryEmit will send.

These fixtures ARE the contract test the harness side builds against: if a shape
here drifts from docs/redesign/v4-memory-lakehouse.md §D11.1, one of these fails.
"""

from __future__ import annotations

import pytest

from mlpal_memory_graph.ingest.plugins.harness_telemetry import (
    TelemetryContractError,
    normalize_ledger_entry,
    normalize_run_outcome,
)

RUN_OUTCOME_FIXTURE = {
    "event_id": "run-abc123",
    "action_type": "run.completed",
    "scope_id": "acme-api",
    "source_ref": "session-7f12",
    "occurred_at": "2026-08-25T10:00:00+00:00",
    "payload": {
        "hop": {"name": "coding", "version": "1.2.0"},
        "model": "claude-sonnet-4-6",
        "task_type": "refactor",
        "run_result": "success",
        "feedback_outcome": "accepted",
        "verifier": {"verdict": "PASS", "findings_ref": "memory://chunk/abc"},
        "self_check_fired": True,
        "anti_churn_fired": False,
        "tokens": {
            "input": 27,
            "output": 4455,
            "cache_read_input": 559916,
            "cache_creation_input": 36618,
        },
        "wall_s": 158,
        "turns": 34,
    },
}


def test_run_outcome_normalizes_to_content_free_episode():
    env = normalize_run_outcome(RUN_OUTCOME_FIXTURE, user_id="svc")
    assert env.content is None  # content-free BY CONSTRUCTION
    assert env.source == "harness_telemetry"
    assert env.scope == "repo" and env.scope_id == "acme-api"
    assert env.workspace == "acme-api"
    assert env.payload["hop"] == {"name": "coding", "version": "1.2.0"}
    assert env.payload["run_result"] == "success"
    assert env.payload["tokens"]["cache_read_input"] == 559916  # all four fields
    assert env.event_id == "run-abc123"  # idempotent replay


def test_engine_vocabularies_enforced_verbatim():
    bad = {**RUN_OUTCOME_FIXTURE, "payload": {**RUN_OUTCOME_FIXTURE["payload"]}}
    bad["payload"]["run_result"] = "passed"  # the pre-freeze vocab we corrected
    with pytest.raises(TelemetryContractError, match="run_result"):
        normalize_run_outcome(bad, user_id="svc")


def test_findings_must_be_pointers_never_text():
    bad = {**RUN_OUTCOME_FIXTURE, "payload": {**RUN_OUTCOME_FIXTURE["payload"]}}
    bad["payload"]["verifier"] = {"verdict": "FAIL", "findings_ref": "the test failed because…"}
    with pytest.raises(TelemetryContractError, match="memory://"):
        normalize_run_outcome(bad, user_id="svc")


def test_unknown_payload_keys_never_cross_the_allowlist():
    smuggle = {**RUN_OUTCOME_FIXTURE, "payload": {**RUN_OUTCOME_FIXTURE["payload"]}}
    smuggle["payload"]["conversation_excerpt"] = "user said something private"
    env = normalize_run_outcome(smuggle, user_id="svc")
    assert "conversation_excerpt" not in env.payload  # projection, not passthrough


def test_ledger_entry_normalizes():
    env = normalize_ledger_entry(
        {
            "event_id": "ledger-1",
            "action_type": "hop.version_published",
            "payload": {
                "hop": {"name": "coding"},
                "from_version": "1.2.0",
                "to_version": "1.3.0",
                "diff_paths": ["verification.antiChurn.threshold"],
                "eval": {
                    "suite_digest": "sha256:deadbeef",
                    "score": 0.87,
                    "pass_bar": 0.85,
                    "runs": 5,
                    "eval_run_id": "er_123",
                },
                "decision": "adopted",
                "proposed_by": "tuner",
            },
        },
        user_id="svc",
    )
    assert env.scope == "org"
    assert env.payload["to_version"] == "1.3.0"
    assert env.payload["eval"]["eval_run_id"] == "er_123"
    assert env.content is None
