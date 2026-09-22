"""hop_distiller — deterministic telemetry aggregation into watched facts.

Pin the floors (silence below MIN_RUNS, never a weak claim), the D11.1
exclusion rule (absent, never zero), and each fact family's shape.
"""

from __future__ import annotations

from mlpal_memory_graph.pipeline.hop_distiller import (
    MIN_REGRESSION,
    MIN_RUNS,
    distill_deviations,
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
        "role": "main",  # post-ingest rows carry an established role; unknown roles are excluded
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


def test_per_model_facts_only_when_a_tier_mixed_models():
    def run(model, **kw):
        e = _run(tier="max", out=500, **kw)
        e["model"] = model
        return e
    one = [run("claude-fable-5") for _ in range(MIN_RUNS)]
    ents, _ = distill_runs(one)
    assert not any("|route|bugfix|max|" in e.key for e in ents)          # one model: tier fact only
    mixed = one + [run("gpt-6-astra") for _ in range(MIN_RUNS)]
    ents, _ = distill_runs(mixed)
    keys = _keys(ents)
    assert "hop:coding|route|bugfix|max|claude-fable-5" in keys and "hop:coding|route|bugfix|max|gpt-6-astra" in keys
    assert "hop:coding|route|bugfix|max" in keys                          # the tier fact stays



def test_deviation_facts_one_per_hop_and_kind_floor_one():
    eps = [
        {"hop": "infra@0.2.0", "kind": "unmodelled", "slug": "dev-unmodelled-describe-volumes", "run": "r1"},
        {"hop": "infra@0.2.0", "kind": "unmodelled", "slug": "dev-unmodelled-sts", "run": "r2"},
        {"hop": "infra@0.2.0", "kind": "refusal", "slug": "dev-refusal-kubectl-scale"},
        {"hop": "", "kind": "surprise", "slug": "no-hop"},          # no provenance: dropped
    ]
    ents, edges = distill_deviations(eps)
    keys = sorted(e.key for e in ents if e.type == "Metric")
    assert keys == ["hop:infra|deviation|refusal", "hop:infra|deviation|unmodelled"]
    facts = {e.src_key: e.fact for e in edges}
    assert facts["hop:infra|deviation|unmodelled"] == "infra deviations (unmodelled) = 2 in window"
    evidence = {e.key: e.props["evidence_span"] for e in ents if e.type == "MetricValue"}
    span = evidence["hop:infra|deviation|unmodelled=2 in window"]
    assert "2 unmodelled deviation(s)" in span and "runs r1, r2" in span
    assert distill_deviations([]) == ([], [])



def test_hop_alias_counts_a_variant_toward_its_parent():
    from mlpal_memory_graph.tools.hop_distill import apply_hop_alias
    p = {"contract": "d11.4", "hop": {"name": "infra-ro", "version": "0.1.1"}, "role": "main"}
    q = apply_hop_alias(p, {"infra-ro": "infra"})
    assert q["hop"] == {"name": "infra", "version": "0.1.1", "variant": "infra-ro"}
    assert p["hop"]["name"] == "infra-ro"                       # input untouched
    assert apply_hop_alias(p, None) is p and apply_hop_alias(p, {"other": "x"}) is p


# ---- memory v10: newer contracts, the read-only twin, and the two prompt facts

def _run_v7(task="watch", injected=True, reread=False, tools=None, name="infra-ro"):
    r = _run(task=task, tier="frontier")
    r["contract"] = "d11.7"
    r["hop"] = {"name": name, "version": "0.3.0"}
    r["memory_projection"] = {"fact_count": 7 if injected else 0, "estimated_tokens": 500, "truncated": False}
    r["memories_injected"] = []
    r["tool_calls"] = tools if tools is not None else ({"Bash": 3, "mcp__memory__memory_search": 1} if reread else {"Bash": 3})
    return r


def test_newer_contracts_are_counted_and_the_twin_counts_toward_its_parent():
    ents, _ = distill_runs([_run_v7() for _ in range(MIN_RUNS)])
    keys = _keys(ents)
    assert "hop:infra|stall|watch" in keys, "d11.7 rows used to be silently excluded"
    assert not any(k.startswith("hop:infra-ro|") for k in keys), "infra-ro's runs belong to infra"


def test_memory_bypass_and_silent_facts():
    runs = [_run_v7(reread=(i % 2 == 0)) for i in range(MIN_RUNS)] + [_run_v7(injected=False, tools={}) for _ in range(10)]
    ents, _ = distill_runs(runs)
    vals = {e.key.split("=")[0]: e.props["value"] for e in ents if e.type == "MetricValue"}
    assert vals["hop:infra|memory-bypass|watch"] == f"{MIN_RUNS // 2}/{MIN_RUNS}"
    assert vals["hop:infra|silent|watch"] == f"10/{MIN_RUNS + 10}"


def test_older_contracts_without_tool_calls_do_not_count_as_silent():
    ents, _ = distill_runs([_run() for _ in range(MIN_RUNS)])  # d11.2: no tool_calls, no projection
    assert "hop:coding|silent|bugfix" not in _keys(ents) and "hop:coding|memory-bypass|bugfix" not in _keys(ents)


def test_capability_facts_from_labels():
    runs = [_run_v7() for _ in range(MIN_RUNS)]
    for i, r in enumerate(runs):
        r["contract"] = "d11.8"
        r["labels"] = {"aws": 3, "kubectl": 2, "gcloud": 1 if i == 0 else 0}
    ents, _ = distill_runs(runs)
    vals = {e.key.split("=")[0]: e.props["value"] for e in ents if e.type == "MetricValue"}
    assert vals["hop:infra|capability|watch|aws"] == f"{MIN_RUNS}/{MIN_RUNS}"
    assert vals["hop:infra|capability|watch|gcloud"] == f"1/{MIN_RUNS}"
    assert "hop:infra|capability|watch|az" not in vals, "a provider no run touched has no fact (absent, never zero)"
