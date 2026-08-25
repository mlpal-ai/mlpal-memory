from __future__ import annotations

from types import SimpleNamespace

from mlpal_memory_graph.pipeline.extractor import extract


def _ep(**kw):
    base = dict(
        org_id="org1",
        actor={"user_id": "u1"},
        subject={},
        action_type="",
        payload={},
        content=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_agent_deployed_extraction():
    ex = extract(_ep(action_type="agent.deployed", subject={"agent_id": "a1"}))
    types = {e.type for e in ex.entities}
    assert {"Org", "User", "Agent"} <= types
    edge_types = {e.type for e in ex.edges}
    assert "DEPLOYED" in edge_types
    assert "MEMBER_OF" in edge_types  # baseline org membership


def test_mcp_tool_invoked_links_tool_and_server():
    ex = extract(
        _ep(action_type="mcp.tool.invoked", subject={"server_id": "s1", "tool_name": "query"})
    )
    edge_types = {e.type for e in ex.edges}
    assert "INVOKED" in edge_types
    assert "EXPOSES" in edge_types


def test_fact_observed_creates_fact():
    ex = extract(_ep(action_type="fact.observed", payload={"statement": "team prefers Postgres"}))
    assert any(e.type == "Fact" for e in ex.entities)
    assert any(e.type == "DECIDED" for e in ex.edges)


def test_deploy_emits_functional_status_edge():
    ex = extract(_ep(action_type="agent.deployed", subject={"agent_id": "a1"}))
    status = next(e for e in ex.edges if e.type == "HAS_STATUS")
    assert status.functional is True  # sourced from the ontology → drives supersession
    assert status.dst_type == "Status" and status.dst_key == "running"
    # non-functional relations are not flagged
    assert next(e for e in ex.edges if e.type == "DEPLOYED").functional is False


def test_stop_emits_stopped_status():
    ex = extract(_ep(action_type="agent.stopped", subject={"agent_id": "a1"}))
    assert any(e.type == "HAS_STATUS" and e.dst_key == "stopped" for e in ex.edges)


def test_member_removed_produces_an_invalidation_not_an_edge():
    ex = extract(_ep(action_type="org.member_removed", subject={"target_user_id": "9"}))
    assert not any(e.type == "MEMBER_OF" and e.src_key == "9" for e in ex.edges)
    inv = next(i for i in ex.invalidations if i.type == "MEMBER_OF")
    assert inv.src_key == "9" and inv.dst_type == "Org"
