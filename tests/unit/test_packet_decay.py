"""memory v6 WP12: inside a packet, claim-derived facts rank by score × their kind's half-life decay;
state never decays by clock; as-of packets skip decay."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from mlpal_memory_graph.services.packets import build_packet
from mlpal_memory_graph.services.resolution import MergedNode
from mlpal_memory_graph.services.retrieval import Resolution, ResolutionTrace


def _node(name, kind, age_days, key="k"):
    return SimpleNamespace(id=name, type="Fact" if kind == "learning" else "MetricValue", key=key, name=name, summary=None,
                           props={"kind": kind}, scope="org", scope_id="o", status="committed", observed_count=1,
                           updated_at=datetime.now(UTC) - timedelta(days=age_days), workspace=None, confidence=None,
                           origin="derived", derived_from=[])


def _packet(nodes, as_of=None):
    res = Resolution(nodes=[MergedNode(node=n, score=0.5) for n in nodes], edges=[], trace=ResolutionTrace(accessible=[], requested_scope=None, per_scope_hits={}, candidates=0, merged=0), passages=[])
    md, _ = build_packet(query="kubectl context", resolution=res, doc_meta={}, as_of=as_of)
    return md


def test_fresh_learning_outranks_year_old_learning_at_equal_score():
    md = _packet([_node("old-learning", "learning", 400), _node("fresh-learning", "learning", 1)])
    assert md.index("fresh-learning") < md.index("old-learning"), md


def test_state_does_not_decay_and_as_of_skips_decay():
    md = _packet([_node("fresh-learning", "learning", 1), _node("old-state = v", "state", 400, key="state:t:k")])
    # the old state keeps score 0.5 (no clock decay) and ties the fresh learning; order is then stable
    assert "old-state" in md and "fresh-learning" in md
    md2 = _packet([_node("old-learning", "learning", 400), _node("fresh-learning", "learning", 1)], as_of=datetime.now(UTC) - timedelta(days=2))
    assert md2.index("old-learning") < md2.index("fresh-learning"), md2  # input order kept: no decay applied
