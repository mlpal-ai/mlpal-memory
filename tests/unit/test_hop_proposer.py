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
    assert ps[0].knob == "model.main"  # hop-v1.1 §8: the main-loop tier is a declared knob
    assert "not graded correctness" in ps[0].rationale  # completion != correctness, stated
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
    # x12's surface (hop-v1.0) declared no model.main: the knob now exists but is not tunable there
    assert app["route:model.main"] == "not_tunable"
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


def test_routing_tie_surfaces_cheaper_tier():
    """x12 A10: equal completion rates must still yield the route proposal
    (classified later as no_declared_knob on joint-memx) — never silence."""
    facts = [
        _f("hop:jm|route|jm|cheap", "32/32 at 6832 out-tokens", nid="c"),
        _f("hop:jm|route|jm|frontier", "32/32 at 5936 out-tokens", nid="f"),
    ]
    ps = propose(facts)
    assert len(ps) == 1 and ps[0].change == {"from": "frontier", "to": "cheap", "scope_note": "class jm"}


def test_enum_set_range_classifies_model_main():
    from mlpal_memory_graph.pipeline.hop_proposer import Proposal, classify
    from mlpal_memory_graph.tools.hop_propose import _parse_tunable
    surface = _parse_tunable("model.main=frontier|max,budgets.maxTurns=10:80")
    assert surface["model.main"] == frozenset({"frontier", "max"}) and surface["budgets.maxTurns"] == (10.0, 80.0)
    ok = Proposal(hop="infra", kind="route", knob="model.main", change={"from": "frontier", "to": "max"},
                  rationale="r", predicted="p", evidence=["memory://node/x"])
    bad = Proposal(hop="infra", kind="route", knob="model.main", change={"from": "frontier", "to": "cheap"},
                   rationale="r", predicted="p", evidence=["memory://node/x"])
    out = classify([ok, bad], surface, set())
    assert out[0].applicability == "enactable" and out[1].applicability == "not_tunable"



def test_deviation_fact_becomes_an_advisory_eval_proposal():
    ps = propose([_f("hop:infra|deviation|unmodelled", "2 in window", nid="d1")])
    assert len(ps) == 1 and ps[0].kind == "eval" and ps[0].knob == ""
    assert ps[0].change == {"op": "author_case", "deviation": "unmodelled", "count": 2}
    assert ps[0].evidence == ["memory://node/d1"]
    classify(ps, tunable={"budgets.maxTurns": (10, 200)}, locked=set())
    assert ps[0].applicability == "advisory"          # never enacted by the candidate builder


# ---- memory v10: prompt facts become advisory proposals

def test_memory_bypass_and_silent_are_advisory_prompt_proposals():
    assert propose([_f("hop:infra|memory-bypass|watch", "5/30")]) == []          # 17 % < 50 %
    ps = propose([_f("hop:infra|memory-bypass|watch", "24/30", nid="b1"), _f("hop:infra|silent|watch", "9/30", nid="s1")])
    assert [p.kind for p in ps] == ["memory", "memory"] and all(p.knob == "" for p in ps)
    assert ps[0].change["op"] == "prompt" and ps[0].evidence == ["memory://node/b1"]
    out = classify(ps, tunable={"budgets.maxTurns": (60, 300)}, locked={"permissions.defaultMode"})
    assert all(p.applicability == "advisory" for p in out)


def test_unused_capability_is_an_advisory_module_off_proposal():
    assert propose([_f("hop:infra|capability|watch|aws", "300/318")]) == []
    ps = propose([_f("hop:infra|capability|watch|gcloud", "2/318", nid="c1")])
    assert len(ps) == 1 and ps[0].kind == "capability" and ps[0].knob == "modules.gcloud.enabled" and ps[0].change["to"] == "off"
    assert classify(ps, tunable={}, locked=set())[0].applicability == "advisory"
    ps = propose([_f("hop:infra|capability|watch|gcloud", "2/318", nid="c1")])
    assert classify(ps, tunable={"modules.gcloud.enabled": frozenset({"on", "off"})}, locked=set())[0].applicability == "enactable"
