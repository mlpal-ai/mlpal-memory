"""D11.3: the parked terminal outcome (needs_approval) + failure_class_vocab@v2, with v1/D11.2
kept frozen. Agreed with the harness session 2026-09-03 (spec hop-v1.1 §10.1)."""
from __future__ import annotations

import pytest

from mlpal_memory_graph.ingest.plugins.harness_telemetry import (
    FAILURE_CLASSES,
    FAILURE_CLASSES_V2,
    TelemetryContractError,
    normalize_run_outcome,
)
from mlpal_memory_graph.pipeline.hop_distiller import MIN_RUNS, distill_runs

CHECKS = {"self_check": {"fired": False}, "anti_churn": {"fired": False},
          "observe": {"ran": True, "passed": True}, "agent": {"verdict": None}}


def _event(contract, run_result, fc, **extra):
    return {
        "contract": contract, "action_type": "run.completed", "scope_id": "mlpal-hops/infra",
        "payload": {"hop": {"name": "infra", "version": "0.1.0"}, "task_type": "apply",
                    "run_result": run_result, "failure_class": fc, "tier": "frontier",
                    "wall_ms": 1200, "checks": CHECKS, "tokens": {"input": 10, "output": 5}, **extra},
    }


def test_v2_vocab_is_a_strict_superset_of_v1():
    assert FAILURE_CLASSES < FAILURE_CLASSES_V2
    assert {"approval_pending", "policy_denied", "approval_declined", "preflight_failed"} <= FAILURE_CLASSES_V2


def test_d113_parked_run_is_accepted_and_stamped():
    env = normalize_run_outcome(_event("d11.3", "needs_approval", "approval_pending"), user_id="u")
    assert env.payload["contract"] == "d11.3"
    assert env.payload["run_result"] == "needs_approval"
    assert env.payload["failure_class"] == "approval_pending"


def test_d113_parked_run_requires_approval_pending():
    with pytest.raises(TelemetryContractError, match="approval_pending"):
        normalize_run_outcome(_event("d11.3", "needs_approval", "other"), user_id="u")
    with pytest.raises(TelemetryContractError, match="approval_pending"):
        normalize_run_outcome(_event("d11.3", "needs_approval", None), user_id="u")


def test_d113_approval_pending_only_with_needs_approval():
    with pytest.raises(TelemetryContractError, match="only valid with run_result needs_approval"):
        normalize_run_outcome(_event("d11.3", "error", "approval_pending"), user_id="u")


def test_d113_gate_failures_are_real_failures():
    for fc in ("policy_denied", "approval_declined", "preflight_failed"):
        env = normalize_run_outcome(_event("d11.3", "error", fc), user_id="u")
        assert env.payload["failure_class"] == fc


def test_d112_stays_frozen():
    with pytest.raises(TelemetryContractError, match="run_result"):
        normalize_run_outcome(_event("d11.2", "needs_approval", "approval_pending"), user_id="u")
    with pytest.raises(TelemetryContractError, match="vocab@v1"):
        normalize_run_outcome(_event("d11.2", "error", "policy_denied"), user_id="u")


def _run(contract="d11.3", result="success", fc=None, role="main"):
    """A post-ingest payload; d11.3 rows carry the role the shipper derived (main here)."""
    return {"contract": contract, "hop": {"name": "infra", "version": "0.1.0"}, "task_type": "apply",
            "run_result": result, "failure_class": fc, "tier": "frontier", "checks": CHECKS,
            "tokens": {"input": 10, "output": 5}, **({"role": role} if role else {})}


def test_distiller_emits_edge_fact_from_d113_parks_only():
    eps = [_run() for _ in range(MIN_RUNS - 4)] + [_run(result="needs_approval", fc="approval_pending") for _ in range(4)]
    ents, _ = distill_runs(eps)
    vals = {e.key.split("=")[0]: e.props["value"] for e in ents if e.type == "MetricValue"}
    assert vals["hop:infra|edge|apply"] == f"4/{MIN_RUNS}"


def test_distiller_does_not_read_d112_cancelled_as_parked():
    eps = [_run(contract="d11.2") for _ in range(MIN_RUNS - 3)] + [
        _run(contract="d11.2", result="cancelled", fc="user_cancelled") for _ in range(3)]
    ents, _ = distill_runs(eps)
    vals = {e.key.split("=")[0]: e.props["value"] for e in ents if e.type == "MetricValue"}
    assert vals["hop:infra|edge|apply"] == f"0/{MIN_RUNS}"  # absent-never-zero for the PARK itself


def test_role_rules_by_contract():
    # d11.2: no blanket main (the sink lived in buildSession; x12 rows are main by inspection)
    e = normalize_run_outcome(_event("d11.2", "success", None), user_id="u")
    assert "role" not in e.payload
    e = normalize_run_outcome(_event("d11.2", "success", None, role="main", role_source="verified-single-event-per-run"), user_id="u")
    assert e.payload["role_source"] == "verified-single-event-per-run"
    # d11.3: role optional; absent = unknown (NOT main); derived roles carry their source
    e = normalize_run_outcome(_event("d11.3", "success", None), user_id="u")
    assert "role" not in e.payload
    e = normalize_run_outcome(_event("d11.3", "success", None, role="subagent", role_source="derived-by-shipper"), user_id="u")
    assert e.payload["role"] == "subagent" and e.payload["role_source"] == "derived-by-shipper"
    with pytest.raises(TelemetryContractError, match="role"):
        normalize_run_outcome(_event("d11.3", "success", None, role="verifier"), user_id="u")
    # d11.4: role + run_id required, parent_run_id kept
    with pytest.raises(TelemetryContractError, match="d11.4 requires role"):
        normalize_run_outcome(_event("d11.4", "success", None), user_id="u")
    with pytest.raises(TelemetryContractError, match="run_id"):
        normalize_run_outcome(_event("d11.4", "success", None, role="main"), user_id="u")
    e = normalize_run_outcome(_event("d11.4", "success", None, role="subagent", run_id="r2", parent_run_id="r1"), user_id="u")
    assert e.payload["run_id"] == "r2" and e.payload["parent_run_id"] == "r1"


def test_distiller_counts_main_only_and_never_guesses():
    eps = ([_run() for _ in range(MIN_RUNS)]
           + [_run(result="max_turns", fc="step_budget_stall", role="subagent") for _ in range(3 * MIN_RUNS)]
           + [_run(result="max_turns", fc="step_budget_stall", role=None) for _ in range(MIN_RUNS)])  # d11.3, role unknown
    ents, _ = distill_runs(eps)
    vals = {e.key.split("=")[0]: e.props["value"] for e in ents if e.type == "MetricValue"}
    assert vals["hop:infra|stall|apply"] == f"0/{MIN_RUNS}"
