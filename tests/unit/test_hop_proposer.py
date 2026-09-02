"""hop_proposer — deterministic, threshold-explicit proposals with citations."""

from __future__ import annotations

from mlpal_memory_graph.pipeline.hop_proposer import Fact, classify, propose


def _f(key, value, nid="n1"):
    return Fact(node_id=nid, key=key, value=value)


def test_budget_proposal_only_above_stall_threshold():
    assert propose([_f("hop:coding|stall|bugfix", "3/30")]) == []  # 10% < 20%
    ps = propose([_f("hop:coding|stall|bugfix", "9/30", nid="s1")])
    assert len(ps) == 1 and ps[0].kind == "budget"
    assert ps[0].knob == "budgets.maxTurns"
    assert ps[0].evidence == ["memory://node/s1"]


def test_waste_proposal_requires_zero_catches():
    assert propose([_f("hop:coding|waste-observe|docs", "1/400")]) == []
    ps = propose([_f("hop:coding|waste-observe|docs", "0/400", nid="w1")])
    assert ps[0].kind == "waste" and ps[0].knob == ""  # observe has no tunable knob in hop-v1
    assert "golden" in ps[0].risk
    ps2 = propose([_f("hop:coding|waste-agent|docs", "0/400", nid="w2")])
    assert ps2[0].knob == "verification.agent.riskGateMinChangedLines"


def test_routing_proposal_cheaper_tier_within_margin():
    facts = [
        _f("hop:coding|route|bugfix|frontier", "29/30 at 900 out-tokens", nid="rf"),
        _f("hop:coding|route|bugfix|cheap", "28/30 at 100 out-tokens", nid="rc"),
    ]
    ps = propose(facts)
    assert len(ps) == 1 and ps[0].kind == "route"
    assert ps[0].change["from"] == "frontier" and ps[0].change["to"] == "cheap"
    assert ps[0].knob == ""  # no routing.tier field exists — classify() will say so
    assert set(ps[0].evidence) == {"memory://node/rc", "memory://node/rf"}
    # beyond the margin: silence
    facts[1] = _f("hop:coding|route|bugfix|cheap", "20/30 at 100 out-tokens", nid="rc")
    assert propose(facts) == []


def test_routing_needs_two_known_tiers_else_silence():
    assert propose([_f("hop:coding|route|bugfix|cheap", "30/30 at 100 out-tokens")]) == []
    # unknown tier names (not in tier_order) never produce a proposal
    assert propose([
        _f("hop:coding|route|bugfix|zeta", "30/30 at 1 out-tokens"),
        _f("hop:coding|route|bugfix|omega", "30/30 at 1 out-tokens"),
    ]) == []


def test_regression_flag_is_advisory_with_rate_floor():
    assert propose([_f("hop:coding|failure|1.1|gateway_error", "2/40")]) == []
    ps = propose([_f("hop:coding|failure|1.1|gateway_error", "6/40", nid="g1")])
    assert ps[0].kind == "regression"
    assert ps[0].change["op"] == "investigate_or_rollback"
    assert ps[0].evidence == ["memory://node/g1"]


def test_every_proposal_cites_something():
    facts = [
        _f("hop:coding|stall|bugfix", "9/30", nid="a"),
        _f("hop:coding|waste-agent|docs", "0/50", nid="b"),
        _f("hop:coding|failure|2.0|other", "5/20", nid="c"),
    ]
    assert all(p.evidence for p in propose(facts))


JOINT_MEMX_TUNABLE = {
    "verification.selfCheck.minEdits": (1, 10),
    "verification.antiChurn.threshold": (3, 12),
    "verification.agent.riskGateMinChangedLines": (0, 50),
    "routing.escalation.patience": (1, 4),
}
JOINT_MEMX_LOCKED = {"budgets.maxTurns", "routing.subagents"}


def test_classify_against_joint_memx_surface():
    """The x12 artifact: routing/budget proposals must come out blocked or
    knob-less; only verification proposals can be enactable. This is the
    'no applicable knob' finding produced BY the tooling, not by eyeballing."""
    facts = [
        _f("hop:joint-memx|stall|joint-memx", "9/30", nid="a"),
        _f("hop:joint-memx|route|joint-memx|frontier", "29/30 at 900 out-tokens", nid="b"),
        _f("hop:joint-memx|route|joint-memx|cheap", "28/30 at 100 out-tokens", nid="c"),
        _f("hop:joint-memx|waste-observe|joint-memx", "0/60", nid="d"),
        _f("hop:joint-memx|waste-agent|joint-memx", "0/60", nid="e"),
        _f("hop:joint-memx|fired-anti-churn|joint-memx", "40/60", nid="g"),
        _f("hop:joint-memx|failure|1.0.0|step_budget_stall", "9/60", nid="h"),
    ]
    ps = classify(propose(facts), JOINT_MEMX_TUNABLE, JOINT_MEMX_LOCKED)
    app = {p.kind + ":" + p.knob: p.applicability for p in ps}
    assert app["budget:budgets.maxTurns"] == "blocked_locked"
    assert app["route:"] == "no_declared_knob"
    assert app["waste:"] == "no_declared_knob"
    assert app["waste:verification.agent.riskGateMinChangedLines"] == "enactable"
    assert app["verification:verification.antiChurn.threshold"] == "enactable"
    assert app["regression:version"] == "advisory"


def test_firing_rate_rules_have_thresholds_and_direction():
    assert propose([_f("hop:c|fired-self-check|t", "12/60")]) == []      # 20%: middle band, silence
    hi = propose([_f("hop:c|fired-self-check|t", "45/60", nid="x")])
    assert hi[0].knob == "verification.selfCheck.minEdits" and hi[0].change["op"] == "raise"
    lo = propose([_f("hop:c|fired-anti-churn|t", "1/60", nid="y")])
    assert lo[0].knob == "verification.antiChurn.threshold" and lo[0].change["op"] == "lower"


def test_unclassified_without_a_surface():
    ps = propose([_f("hop:c|stall|t", "9/30")])
    assert classify(ps, None, None)[0].applicability == "unclassified"
