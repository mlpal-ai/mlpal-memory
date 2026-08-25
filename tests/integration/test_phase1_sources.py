"""Phase-1 ingestion plugins (deterministic backbone). Tested offline: the pure row→envelope
mappers, the fail-loud column contract, and the end-to-end fold (mapped row → episode → graph)
producing the right deterministic edges. The asyncpg watermark tail itself is Postgres-only
(@pytest.mark.postgres, separate). Producer schemas verified 2026-06 via the recon workflow."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from mlpal_memory_graph.db.models import Edge, Episode, Node
from mlpal_memory_graph.ingest import SOURCE_REGISTRY, SourceConfig, source_item_to_envelope
from mlpal_memory_graph.ingest.plugins import (  # noqa: F401  registers the plugins
    backend_agents,
    backend_audit,
    mcp_servers,
    skills,
)
from mlpal_memory_graph.ingest.plugins._producer_tail import (
    SourceContractError,
    decode_cursor,
    encode_cursor,
)
from mlpal_memory_graph.pipeline.updater import Updater

T = datetime(2026, 5, 1, tzinfo=UTC)
T2 = datetime(2026, 5, 2, tzinfo=UTC)


def _plugin(source_type: str):
    return SOURCE_REGISTRY[source_type](SourceConfig(type=source_type))


def _envelopes(source_type: str, row: dict):
    plugin = _plugin(source_type)
    return [
        source_item_to_envelope(item, source_type=source_type, default_scope="org")
        for item in plugin.map_row(row)
    ]


async def _fold(session, env):
    ep = Episode(**env.to_episode_kwargs(capture_content=False))
    session.add(ep)
    await session.flush()
    await Updater().process_episode(session, ep)
    await session.flush()


async def _edge_types(session):
    return {e.type for e in (await session.execute(select(Edge))).scalars().all()}


# ── registry ──────────────────────────────────────────────────────────────────


def test_phase1_plugins_registered_and_incremental():
    for st in ("backend_audit", "backend_agents", "mcp_servers", "skills"):
        assert st in SOURCE_REGISTRY
        assert SOURCE_REGISTRY[st].capabilities().incremental


def test_column_contract_fails_loud_on_drift():
    plugin = _plugin("backend_audit")
    with pytest.raises(SourceContractError):
        plugin.validate_row({"id": 1})  # missing organization_id/actor_user_id/action/created_at


# ── backend_audit → MEMBER_OF ─────────────────────────────────────────────────


async def test_backend_audit_member_added(session):
    row = {
        "id": 1,
        "organization_id": 42,
        "actor_user_id": 7,
        "action": "member_added",
        "target_user_id": 9,
        "target_email": None,
        "details": {},
        "created_at": T,
    }
    [env] = _envelopes("backend_audit", row)
    assert env.action_type == "org.member_added"
    assert env.org_id == "42" and env.scope == "org" and env.scope_id == "42"
    assert env.actor.user_id == "7" and env.subject.target_user_id == "9"
    assert env.source == "backend_audit"

    await _fold(session, env)
    assert "MEMBER_OF" in await _edge_types(session)
    users = {
        n.key
        for n in (await session.execute(select(Node).where(Node.type == "User"))).scalars().all()
    }
    assert "9" in users  # the added member


# ── backend_agents → AUTHORED + DEPLOYED ──────────────────────────────────────


async def test_backend_agents_running_emits_created_and_deployed(session):
    row = {
        "id": 5,
        "name": "sales-bot",
        "status": "active",
        "deployment_status": "running",
        "deployed_at": T2,
        "created_at": T,
        "updated_at": T2,
        "author_user_id": 7,
        "org_id": 42,
    }
    envs = _envelopes("backend_agents", row)
    assert {e.action_type for e in envs} == {"agent.created", "agent.deployed"}
    for env in envs:
        assert env.subject.agent_id == "5" and env.actor.user_id == "7"
        await _fold(session, env)
    assert {"AUTHORED", "DEPLOYED"} <= await _edge_types(session)


def test_backend_agents_pool_agent_without_owner_is_skipped():
    row = {
        "id": 6,
        "name": "pool",
        "status": "active",
        "deployment_status": "not_deployed",
        "deployed_at": None,
        "created_at": T,
        "updated_at": T,
        "author_user_id": None,
        "org_id": None,
    }
    assert _envelopes("backend_agents", row) == []


# ── mcp_servers → DEPLOYED + EXPOSES ──────────────────────────────────────────


async def test_mcp_server_deployed_exposes_tools(session):
    row = {
        "id": 3,
        "owner_user_id": 7,
        "owner_type": "user",
        "name": "crm-mcp",
        "status": "deployed",
        "tools_metadata": [{"name": "query"}, {"name": "create"}],
        "updated_at": T,
    }
    [env] = _envelopes("mcp_servers", row)
    assert env.action_type == "mcp.deployed" and env.scope == "user" and env.scope_id == "7"
    assert env.payload["tools"] == ["query", "create"]
    await _fold(session, env)
    assert {"DEPLOYED", "EXPOSES"} <= await _edge_types(session)
    tools = {
        n.key
        for n in (await session.execute(select(Node).where(Node.type == "Tool"))).scalars().all()
    }
    assert {"query", "create"} <= tools


def test_mcp_platform_server_is_global_and_draft_skipped():
    deployed = {
        "id": 4,
        "owner_user_id": 1,
        "owner_type": "platform",
        "name": "web-search",
        "status": "deployed",
        "tools_metadata": [],
        "updated_at": T,
    }
    [env] = _envelopes("mcp_servers", deployed)
    assert env.scope == "global"
    draft = {**deployed, "id": 5, "status": "draft"}
    assert _envelopes("mcp_servers", draft) == []


# ── skills → USED_SKILL ───────────────────────────────────────────────────────


async def test_skill_use_event(session):
    row = {
        "id": 1,
        "event_id": "evt-1",
        "event_type": "script.executed",
        "skill_id": 11,
        "owner_user_id": 7,
        "consumer_user_id": 9,
        "consumer_org_id": 42,
        "agent_id": None,
        "metadata_json": {},
        "timestamp": T,
    }
    [env] = _envelopes("skills", row)
    assert env.action_type == "skill.used" and env.scope == "org" and env.scope_id == "42"
    assert env.actor.user_id == "9" and env.subject.skill_id == "11"
    await _fold(session, env)
    assert "USED_SKILL" in await _edge_types(session)


def test_skill_metadata_surfaced_is_skipped():
    row = {
        "id": 2,
        "event_id": "evt-2",
        "event_type": "metadata.surfaced",
        "skill_id": 11,
        "consumer_user_id": None,
        "consumer_org_id": None,
        "agent_id": None,
        "metadata_json": {},
        "timestamp": T,
    }
    assert _envelopes("skills", row) == []


def test_skill_outcome_event_is_a_use():
    row = {
        "id": 3,
        "event_id": "evt-3",
        "event_type": "outcome",
        "skill_id": 11,
        "consumer_user_id": 9,
        "consumer_org_id": None,
        "agent_id": 5,
        "metadata_json": {"ok": True},
        "timestamp": T,
    }
    [env] = _envelopes("skills", row)
    assert env.action_type == "skill.used"
    assert env.scope == "user" and env.scope_id == "9"  # no org → lands under the consumer
    assert env.subject.agent_id == "5"


def test_skill_use_without_consumer_or_org_is_skipped():
    # a real *use* event but no actor to attribute it to → dropped, not landed orphaned
    row = {
        "id": 4,
        "event_id": "evt-4",
        "event_type": "script.executed",
        "skill_id": 11,
        "consumer_user_id": None,
        "consumer_org_id": None,
        "agent_id": None,
        "metadata_json": {},
        "timestamp": T,
    }
    assert _envelopes("skills", row) == []


# ── unmapped events are skipped; member_removed ENDS membership (stative) ──────


def test_backend_audit_member_removed_maps_to_removal(session):
    # removal is now modeled: it ENDS the target's MEMBER_OF (closes the edge in the fold)
    row = {
        "id": 2,
        "organization_id": 42,
        "actor_user_id": 7,
        "action": "member_removed",
        "target_user_id": 9,
        "details": {},
        "created_at": T,
    }
    [env] = _envelopes("backend_audit", row)
    assert env.action_type == "org.member_removed"
    assert env.subject.target_user_id == "9"


def test_backend_audit_unmapped_action_is_skipped():
    # genuinely unmapped events (invitations/settings) still produce nothing — no guessing
    row = {
        "id": 7,
        "organization_id": 42,
        "actor_user_id": 7,
        "action": "settings_changed",
        "target_user_id": None,
        "details": {},
        "created_at": T,
    }
    assert _envelopes("backend_audit", row) == []


async def test_backend_audit_role_changed_affirms_membership(session):
    row = {
        "id": 3,
        "organization_id": 42,
        "actor_user_id": 7,
        "action": "member_role_changed",
        "target_user_id": 9,
        "details": {"role": "admin"},
        "created_at": T,
    }
    [env] = _envelopes("backend_audit", row)
    assert env.action_type == "org.role_changed" and env.subject.target_user_id == "9"
    await _fold(session, env)
    assert "MEMBER_OF" in await _edge_types(session)
    users = {
        n.key
        for n in (await session.execute(select(Node).where(Node.type == "User"))).scalars().all()
    }
    assert "9" in users  # the re-affirmed member, not just the actor


async def test_mcp_server_stopped_records_stopped_status(session):
    row = {
        "id": 8,
        "owner_user_id": 7,
        "owner_type": "user",
        "name": "crm-mcp",
        "status": "stopped",
        "tools_metadata": [{"name": "query"}],
        "updated_at": T,
    }
    [env] = _envelopes("mcp_servers", row)
    assert env.action_type == "mcp.stopped"
    await _fold(session, env)
    edges = await _edge_types(session)
    # a stop records the stative HAS_STATUS=stopped, but not a (re)deploy/expose
    assert "HAS_STATUS" in edges
    assert "DEPLOYED" not in edges and "EXPOSES" not in edges


def test_backend_agent_draft_emits_only_created():
    row = {
        "id": 9,
        "name": "wip-bot",
        "status": "active",
        "deployment_status": "not_deployed",
        "deployed_at": None,
        "created_at": T,
        "updated_at": T,
        "author_user_id": 7,
        "org_id": 42,
    }
    envs = _envelopes("backend_agents", row)
    assert [e.action_type for e in envs] == ["agent.created"]


# ── composite watermark cursor (same-timestamp safety) ─────────────────────────


def test_cursor_roundtrip_preserves_timestamp_and_id():
    cur = encode_cursor(T2, 5)
    assert decode_cursor(cur) == (T2, "5")
    assert decode_cursor(None) is None
    assert decode_cursor("") is None


def test_cursor_id_survives_ids_containing_the_delimiter():
    # row ids are usually ints, but uuid/string keys must round-trip too (rpartition on '|')
    cur = encode_cursor(T, "a|b|c")
    assert decode_cursor(cur) == (T, "a|b|c")
