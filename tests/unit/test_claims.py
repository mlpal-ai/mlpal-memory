"""memory.claim (hop-v1.1 §9.3): the deterministic mapping of a claim onto the store's primitives."""

from __future__ import annotations

from types import SimpleNamespace as NS

from mlpal_memory_graph.pipeline.claims import anchor_key, claim_provenance, extract_claim


def _ep(kind, topic="infra/state/cost-daily", key="2026-09-15", value="$398.08 MTD", evidence=("call-12",), **extra):
    return NS(action_type="memory.claim", actor={"user_id": "sai"}, content=str(value),
              payload={"kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": list(evidence), **extra})


def test_state_claim_is_a_keyed_functional_value():
    ex = extract_claim(_ep("state", hop="infra@0.3.0", origin="routine:cost-daily"))
    types = [e.type for e in ex.entities]
    assert types == ["Metric", "MetricValue"]
    assert ex.entities[0].key == anchor_key("state", "infra/state/cost-daily", "2026-09-15") == "state:infra/state/cost-daily:2026-09-15"
    assert ex.entities[1].key.endswith("=$398.08 MTD")
    [edge] = ex.edges
    assert edge.type == "HAS_VALUE" and edge.functional and edge.props["grounded"] is True
    assert edge.props["evidence_ids"] == ["call-12"] and edge.props["hop"] == "infra@0.3.0" and edge.props["origin"] == "routine:cost-daily"


def test_preference_claim_uses_the_pref_anchor():
    ex = extract_claim(_ep("preference", topic="person/sai/pref/infra", key="cost_format", value="table"))
    assert ex.entities[0].key == "pref:person/sai/pref/infra:cost_format"
    assert ex.entities[0].props["kind"] == "preference"


def test_learning_claim_is_a_fact_the_actor_decided_with_confidence_by_grounding():
    ex = extract_claim(_ep("learning", topic="infra/learning", key="dedup", value="The default kube context points at the old account; always pass --context."))
    assert [e.type for e in ex.entities] == ["User", "Fact"]
    [edge] = ex.edges
    assert edge.type == "DECIDED" and edge.src_key == "sai" and edge.props["confidence"] == 0.7
    ungrounded = extract_claim(_ep("learning", topic="infra/learning", key="dedup", value="Some longer learned sentence here.", evidence=()))
    assert ungrounded.edges[0].props["confidence"] == 0.4 and ungrounded.edges[0].props["grounded"] is False


def test_record_and_deviation_claims_extract_nothing_and_junk_is_ignored():
    assert extract_claim(_ep("record")).entities == []
    assert extract_claim(_ep("deviation", topic="infra/deviation", key="r1", value="kind: refusal\nexpected: x")).entities == []
    assert extract_claim(_ep("state", value="")).entities == []                       # no value, no fact
    assert extract_claim(_ep("state", key="")).entities == []                         # no key, no fact
    assert extract_claim(_ep("learning", topic="infra/learning", key="dedup", value="too short")).entities == []
    assert extract_claim(NS(action_type="document.ingested", actor={}, content="x", payload={"kind": "state"})).entities == []


def test_provenance_marks_grounding_and_keeps_only_present_keys():
    p = claim_provenance({"kind": "state", "topic": "t", "key": "k", "evidence_ids": ["", " ", "c1"], "hop": "h@1"})
    assert p["evidence_ids"] == ["c1"] and p["grounded"] and p["hop"] == "h@1" and "origin" not in p
    assert claim_provenance({"kind": "state", "topic": "t", "key": "k"})["grounded"] is False


def test_me_placeholder_resolves_to_the_actor():
    from mlpal_memory_graph.pipeline.claims import extract_claim

    class E:
        action_type = "memory.claim"
        actor = {"user_id": "priya"}
        payload = {"kind": "preference", "topic": "person/{me}/pref/infra", "key": "cost-email-time", "value": "08:00 PT", "evidence_ids": ["c1"]}

    out = extract_claim(E())
    keys = {e.key for e in out.entities}
    assert "pref:person/priya/pref/infra:cost-email-time" in keys
    assert not any("{me}" in k for k in keys)


def test_pinned_claim_carries_the_flag_into_its_props():
    from types import SimpleNamespace

    from mlpal_memory_graph.pipeline.claims import CLAIM_ACTION, extract_claim

    ep = SimpleNamespace(action_type=CLAIM_ACTION, actor={"user_id": "svp"},
                         payload={"kind": "state", "topic": "infra/state/identity", "key": "credits.aws",
                                  "value": "$25,000 AWS credit, expires 2027-03-31", "pinned": True, "valid_until": "2027-03-31T00:00:00Z",
                                  "evidence_ids": ["msg-1"]})
    out = extract_claim(ep)
    value = next(e for e in out.entities if e.type == "MetricValue")
    assert value.props["pinned"] is True and value.props["valid_until"].startswith("2027-03-31")
    plain = SimpleNamespace(action_type=CLAIM_ACTION, actor={"user_id": "svp"},
                            payload={"kind": "state", "topic": "t", "key": "k", "value": "v"})
    assert "pinned" not in next(e for e in extract_claim(plain).entities if e.type == "MetricValue").props
