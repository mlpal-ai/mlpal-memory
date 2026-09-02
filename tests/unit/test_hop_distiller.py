"""hop_distiller — deterministic telemetry aggregation into watched facts.

Pin the floors (silence below MIN_RUNS, never a weak claim), the D11.1
exclusion rule (absent, never zero), and each fact family's shape.
"""

from __future__ import annotations

from mlpal_memory_graph.pipeline.hop_distiller import (
    MIN_REGRESSION,
    MIN_RUNS,
    distill_runs,
)


def _run(result="success", fc=None, tier="cheap", task="bugfix", version="1.0",
         observe_ran=True, observe_passed=True, verdict=None, out=100):
    return {
        "contract": "d11.2",
        "hop": {"name": "coding", "version": version},
        "task_type": task,
        "run_result": result,
        "failure_class": fc,
        "tier": tier,
        "checks": {
            "self_check": {"fired": False},
            "anti_churn": {"fired": False},
            "observe": {"ran": observe_ran, "passed": observe_passed},
            "agent": {"verdict": verdict},
        },
        "tokens": {"input": 500, "output": out},
    }


def _keys(entities):
    return {e.key for e in entities if e.type == "Metric"}


def test_below_floor_is_silence_not_weak_claims():
    ents, edges = distill_runs([_run() for _ in range(MIN_RUNS - 1)])
    assert ents == [] and edges == []


def test_budget_and_waste_facts_at_floor():
    eps = [_run() for _ in range(MIN_RUNS - 3)] + [
        _run(result="max_turns", fc="step_budget_stall") for _ in range(3)
    ]
    ents, edges = distill_runs(eps)
    keys = _keys(ents)
    assert "hop:coding|stall|bugfix" in keys
    stall = next(e for e in ents if e.key == "hop:coding|stall|bugfix=3/30")
    assert "step_budget_stall" in stall.props["evidence_span"]
    # observe ran every time and caught nothing -> waste fact
    assert "hop:coding|waste-observe|bugfix" in keys
    assert all(g.functional for g in edges)


def test_firing_rate_facts_emitted_at_floor():
    eps = [_run() for _ in range(MIN_RUNS)]
    for i, ep in enumerate(eps):
        ep["checks"]["self_check"]["fired"] = i < 6      # 20%
        ep["checks"]["anti_churn"]["fired"] = i < 1      # ~3%
    ents, _ = distill_runs(eps)
    vals = {e.key.split("=")[0]: e.props["value"] for e in ents if e.type == "MetricValue"}
    assert vals["hop:coding|fired-self-check|bugfix"] == f"6/{MIN_RUNS}"
    assert vals["hop:coding|fired-anti-churn|bugfix"] == f"1/{MIN_RUNS}"


def test_check_that_catches_is_not_waste():
    eps = [_run(observe_passed=(i != 0)) for i in range(MIN_RUNS)]
    ents, _ = distill_runs(eps)
    assert "hop:coding|waste-observe|bugfix" not in _keys(ents)


def test_routing_fact_per_tier_with_median_tokens():
    eps = [_run(tier="cheap", out=100) for _ in range(MIN_RUNS)] + [
        _run(tier="frontier", out=900) for _ in range(MIN_RUNS - 1)  # below floor
    ]
    ents, _ = distill_runs(eps)
    keys = _keys(ents)
    assert "hop:coding|route|bugfix|cheap" in keys
    assert "hop:coding|route|bugfix|frontier" not in keys  # floor per tier
    v = next(e for e in ents if e.key.startswith("hop:coding|route|bugfix|cheap="))
    assert v.props["value"] == f"{MIN_RUNS}/{MIN_RUNS} at 100 out-tokens"
    # per-tier check firing is DESCRIPTIVE evidence (A8), never a rule input
    assert "self_check 0/30" in v.props["evidence_span"] and "agent ran 0/30" in v.props["evidence_span"]


def test_regression_fact_per_version_failure_class():
    eps = (
        [_run(version="1.1") for _ in range(MIN_RUNS)]
        + [_run(version="1.1", result="error", fc="gateway_error")
           for _ in range(MIN_REGRESSION)]
        + [_run(version="1.0", result="error", fc="gateway_error")
           for _ in range(MIN_REGRESSION - 1)]  # below floor on old version
    )
    ents, _ = distill_runs(eps)
    keys = _keys(ents)
    assert "hop:coding|failure|1.1|gateway_error" in keys
    assert "hop:coding|failure|1.0|gateway_error" not in keys


def test_d111_rows_are_absent_never_zero():
    """A D11.1 backlog must not dilute rates: excluded entirely, not counted
    as zero-stall zero-catch runs."""
    d111 = {"hop": {"name": "coding", "version": "1.0"}, "task_type": "bugfix",
            "run_result": "success", "tokens": {"output": 5}, "wall_s": 9}
    eps = [d111] * 500 + [_run(result="max_turns", fc="step_budget_stall")
                          for _ in range(MIN_RUNS)]
    ents, _ = distill_runs(eps)
    v = next(e for e in ents if e.key.startswith("hop:coding|stall|bugfix="))
    assert v.props["value"] == f"{MIN_RUNS}/{MIN_RUNS}"  # 500 legacy rows invisible
