"""hop_proposer — deterministic, threshold-explicit proposals with citations."""

from __future__ import annotations

from mlpal_memory_graph.pipeline.hop_proposer import Fact, propose


def _f(key, value, nid="n1"):
    return Fact(node_id=nid, key=key, value=value)


def test_budget_proposal_only_above_stall_threshold():
    assert propose([_f("hop:coding|stall|bugfix", "3/30")]) == []  # 10% < 20%
    ps = propose([_f("hop:coding|stall|bugfix", "9/30", nid="s1")])
    assert len(ps) == 1 and ps[0].kind == "budget"
    assert ps[0].knob == "budgets.max_turns[bugfix]"
    assert ps[0].evidence == ["memory://node/s1"]


def test_waste_proposal_requires_zero_catches():
    assert propose([_f("hop:coding|waste-observe|docs", "1/400")]) == []
    ps = propose([_f("hop:coding|waste-observe|docs", "0/400", nid="w1")])
    assert ps[0].kind == "waste" and ps[0].change == {"op": "skip_for", "task_type": "docs"}
    assert "golden" in ps[0].risk


def test_routing_proposal_cheaper_tier_within_margin():
    facts = [
        _f("hop:coding|route|bugfix|frontier", "29/30 at 900 out-tokens", nid="rf"),
        _f("hop:coding|route|bugfix|cheap", "28/30 at 100 out-tokens", nid="rc"),
    ]
    ps = propose(facts)
    assert len(ps) == 1 and ps[0].kind == "route"
    assert ps[0].change == {"from": "frontier", "to": "cheap"}
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
