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


# ── D11.2 acceptance (landed 2026-09-01: engine mlpal-harness@0df42fb, spec
# mlpal-ai/hop@ace4cfc §6.3). These are the tests the emitter side runs a real
# event through end-to-end. ───────────────────────────────────────────────────

def _d112_event(**payload_overrides):
    payload = {
        "hop": {"name": "tuned-coding", "version": "1.4.2"},
        "repo": "mlpal-backend",
        "model": "claude-haiku-4-5-20251001",
        "tier": "cheap",
        "task_type": "bugfix",
        "run_result": "success",
        "failure_class": None,
        "checks": {
            "self_check": {"fired": True},
            "anti_churn": {"fired": False},
            "observe": {"ran": True, "passed": True},
            "agent": {"verdict": "PASS"},
        },
        "tokens": {"input": 100, "output": 40, "cache_read_input": 0, "cache_creation_input": 0},
        "wall_ms": 8421,
        "turns": 3,
    }
    payload.update(payload_overrides)
    return {
        "contract": "d11.2",
        "action_type": "run.completed",
        "scope_id": "mlpal-backend",
        "occurred_at": "2026-09-01T00:00:00Z",
        "payload": payload,
    }


def test_d112_full_event_normalizes():
    env = normalize_run_outcome(_d112_event(), user_id="svc")
    p = env.payload
    assert p["contract"] == "d11.2"
    assert p["wall_ms"] == 8421 and "wall_s" not in p
    assert p["failure_class"] is None
    assert p["tier"] == "cheap"
    assert p["checks"]["observe"] == {"ran": True, "passed": True}
    assert p["checks"]["agent"]["verdict"] == "PASS"
    assert "self_check_fired" not in p  # legacy loose bools are D11.1-only
    assert env.content is None  # content-free by construction


def test_d112_failure_class_invariant():
    # null IFF success — both directions rejected
    with pytest.raises(TelemetryContractError, match="failure_class"):
        normalize_run_outcome(
            _d112_event(run_result="success", failure_class="other"), user_id="svc")
    with pytest.raises(TelemetryContractError, match="failure_class"):
        normalize_run_outcome(
            _d112_event(run_result="error", failure_class=None), user_id="svc")
    # outside the frozen vocab: rejected, never coerced to a near bucket
    with pytest.raises(TelemetryContractError, match="vocab"):
        normalize_run_outcome(
            _d112_event(run_result="error", failure_class="mystery"), user_id="svc")
    # "other" is the honest catch-all and is accepted
    env = normalize_run_outcome(
        _d112_event(run_result="error", failure_class="other"), user_id="svc")
    assert env.payload["failure_class"] == "other"
    # a field simply missing (vs explicit null) is rejected
    ev = _d112_event()
    del ev["payload"]["failure_class"]
    with pytest.raises(TelemetryContractError, match="failure_class"):
        normalize_run_outcome(ev, user_id="svc")


def test_d112_tier_omitted_is_absent_not_empty():
    ev = _d112_event()
    del ev["payload"]["tier"]
    env = normalize_run_outcome(ev, user_id="svc")
    assert "tier" not in env.payload
    with pytest.raises(TelemetryContractError, match="tier"):
        normalize_run_outcome(_d112_event(tier=""), user_id="svc")


def test_d112_wall_s_rejected():
    """Silent unit drift inside one contract version is the failure class the
    D11.1→D11.2 bump exists to kill — wall_s under d11.2 is a hard error."""
    ev = _d112_event()
    ev["payload"]["wall_s"] = 8
    with pytest.raises(TelemetryContractError, match="wall_s"):
        normalize_run_outcome(ev, user_id="svc")
    ev2 = _d112_event()
    del ev2["payload"]["wall_ms"]
    with pytest.raises(TelemetryContractError, match="wall_ms"):
        normalize_run_outcome(ev2, user_id="svc")


def test_d112_verdict_lives_in_checks_agent_and_verifier_optional():
    """yodex leaves the legacy verifier{} unset (no double emit; it writes no
    findings to memory://). Null agent verdict is valid (no agent check ran)."""
    env = normalize_run_outcome(
        _d112_event(checks={
            "self_check": {"fired": False},
            "anti_churn": {"fired": False},
            "observe": {"ran": False, "passed": False},
            "agent": {"verdict": None},
        }),
        user_id="svc",
    )
    assert "verifier" not in env.payload
    assert env.payload["checks"]["agent"]["verdict"] is None
    with pytest.raises(TelemetryContractError, match="checks"):
        normalize_run_outcome(_d112_event(checks={}), user_id="svc")


def test_d111_events_still_normalize_unchanged():
    """Dual-version ingest: D11.1 events (no contract discriminator) keep the
    legacy shape — wall_s, loose bools, no failure_class/tier/checks."""
    env = normalize_run_outcome(
        {
            "action_type": "run.completed",
            "scope_id": "repo-x",
            "payload": {
                "hop": {"name": "coding", "version": "1.0"},
                "run_result": "success",
                "self_check_fired": True,
                "tokens": {"input": 1, "output": 1},
                "wall_s": 9,
                "turns": 2,
            },
        },
        user_id="svc",
    )
    assert env.payload["wall_s"] == 9
    assert env.payload["self_check_fired"] is True
    assert "contract" not in env.payload
    assert "failure_class" not in env.payload  # absent, never zero/null-filled


def test_occurred_at_parsed_to_aware_datetime_and_malformed_rejected():
    from datetime import UTC

    env = normalize_run_outcome(_d112_event(), user_id="svc")
    assert env.occurred_at.tzinfo is not None
    assert env.occurred_at.astimezone(UTC).isoformat().startswith("2026-09-01T00:00:00")
    bad = _d112_event()
    bad["occurred_at"] = "yesterday-ish"
    with pytest.raises(TelemetryContractError, match="ISO-8601"):
        normalize_run_outcome(bad, user_id="svc")
