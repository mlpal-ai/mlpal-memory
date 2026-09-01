"""hop_eval ladder + hop_promote gate — hermetic (injected runner)."""

from __future__ import annotations

from pathlib import Path

import yaml

from mlpal_memory_graph.pipeline.hop_eval import meets_margin, run_ladder
from mlpal_memory_graph.pipeline.hop_promote import decide_promotion, is_apply_capable

HOP = {
    "spec": "mlpal/hop-v1", "name": "tuned-coding", "version": "0.1.0",
    "evals": [
        {"name": "probe-smoke", "tasks": "evals/probe", "scorer": "probe", "runs": 1, "passBar": 1.0, "role": "probe"},
        {"name": "coding-golden", "tasks": "evals/golden", "scorer": "golden", "runs": 3, "passBar": 1.0, "role": "golden"},
        {"name": "output-tokens", "tasks": "evals/frontier", "scorer": "frontier", "runs": 3, "passBar": 0, "role": "frontier"},
        {"name": "vibes", "tasks": "evals/vibes", "scorer": "judge", "runs": 1, "passBar": 1.0, "role": "golden", "gates": False},
    ],
    "tuning": {"cadence": "daily", "minRunsSinceLast": 200, "canaryFraction": 0.1,
               "canaryMinRuns": 50, "promote": "human", "frontierMetric": "output-tokens",
               "promotionMargin": "-5% at p<.05", "goldenSuite": "coding-golden"},
}


def _write(tmp_path: Path, doc=HOP) -> Path:
    p = tmp_path / "hop.yaml"
    p.write_text(yaml.safe_dump(doc))
    return p


def _runner(outcomes: dict):
    calls: list[str] = []

    def run(cmd: str, cwd: Path):
        calls.append(cmd)
        rc, out = outcomes[cmd]
        return rc, out
    run.calls = calls  # type: ignore[attr-defined]
    return run


def test_ladder_accepts_when_gates_green_and_margin_met(tmp_path):
    r = _runner({"probe": (0, ""), "golden": (0, ""), "frontier": (0, "tokens 900\n"),
                 "judge": (1, "meh")})
    v = run_ladder(_write(tmp_path), runner=r, baseline_frontier=1000.0)
    assert v.disposition == "accepted", v.reason
    assert v.frontier["score"] == 900.0 and v.frontier["meets_margin"] is True
    assert "not tested" in v.frontier["significance"]
    # LLM-judged suite failed but gates:false -> it never gates
    vibes = next(s for s in v.suites if s.name == "vibes")
    assert vibes.passed is None and vibes.gates is False


def test_ladder_stops_at_first_gating_failure_and_skips_frontier(tmp_path):
    r = _runner({"probe": (0, ""), "golden": (1, ""), "frontier": (0, "1\n"), "judge": (0, "")})
    v = run_ladder(_write(tmp_path), runner=r, baseline_frontier=1000.0)
    assert v.disposition == "rejected" and "coding-golden" in v.reason
    assert "frontier" not in r.calls  # no money spent past a failed gate
    assert next(s for s in v.suites if s.name == "output-tokens").skipped


def test_margin_sign_semantics():
    assert meets_margin(940, 1000, "-5% at p<.05") is True
    assert meets_margin(960, 1000, "-5% at p<.05") is False
    assert meets_margin(1060, 1000, "+5%") is True
    assert meets_margin(1, 1, "garbage") is None


def test_no_baseline_means_scored_not_decided(tmp_path):
    r = _runner({"probe": (0, ""), "golden": (0, ""), "frontier": (0, "42"), "judge": (0, "")})
    v = run_ladder(_write(tmp_path), runner=r)
    assert v.disposition == "scored" and v.frontier["score"] == 42.0


def test_unversioned_artifact_refused(tmp_path):
    p = tmp_path / "hop.yaml"
    p.write_text(yaml.safe_dump({"name": "x", "evals": []}))
    try:
        run_ladder(p, runner=_runner({}))
        assert False, "should refuse"
    except ValueError as e:
        assert "mlpal/hop-v1" in str(e)


def test_apply_capable_heuristic_is_conservative():
    assert is_apply_capable({}) is True                                   # [] = all tools
    assert is_apply_capable({"permissions": {"allow": ["Read", "Grep"]}}) is False
    assert is_apply_capable({"permissions": {"allow": ["Read", "Bash(git:*)"]}}) is True
    assert is_apply_capable({"tools": {"include": ["memory_answer", "Read"]}}) is False
    assert is_apply_capable({"tools": {"include": ["kubectl"]}}) is True


def test_promotion_gate_tuner_owns_blast_radius():
    accepted = {"disposition": "accepted", "reason": "ok"}
    hop_auto = {**HOP, "tuning": {**HOP["tuning"], "promote": "auto"}}
    d = decide_promotion(hop_auto, accepted)
    assert d.allowed and d.mode == "human" and "apply-capable" in d.reason
    readonly = {**hop_auto, "permissions": {"allow": ["Read", "Grep", "memory_answer"]}}
    assert decide_promotion(readonly, accepted).mode == "auto"
    # a rejected verdict never promotes, whatever the mode says
    assert decide_promotion(readonly, {"disposition": "rejected", "reason": "gate"}).allowed is False
    # auto with a non-gating golden falls back to human
    nogate = {**readonly, "evals": [{**s, "gates": False} if s["name"] == "coding-golden" else s
                                    for s in HOP["evals"]]}
    assert decide_promotion(nogate, accepted).mode == "human"
