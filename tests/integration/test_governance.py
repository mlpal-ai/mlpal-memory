"""M5: consent opt-out + per-scope extraction policy at the single fold gate.

Fold-level behaviour is exercised directly through ``Updater.process_episode`` (the choke
point); the admin surface is exercised through the REST endpoints.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.db.models import ConsentState, Episode, ExtractionPolicy, Node
from mlpal_memory_graph.pipeline.updater import Updater


async def _episode(session, **kw):
    base = dict(
        event_id="g1",
        occurred_at=datetime.now(UTC),
        org_id="orgZ",
        scope="org",
        scope_id="orgZ",
        actor={"user_id": "u1"},
        source="backend",
        action_type="fact.observed",
        subject={},
        payload={"statement": "widgets ship friday"},
        processed=False,
    )
    base.update(kw)
    ep = Episode(**base)
    session.add(ep)
    await session.flush()
    return ep


async def _node_count(session):
    return len((await session.execute(select(Node))).scalars().all())


# ── fold gate ───────────────────────────────────────────────────────────────


async def test_consent_off_blocks_fold(session):
    session.add(ConsentState(org_id="orgZ", scope="org", scope_id="orgZ", state="off"))
    await session.flush()
    ep = await _episode(session)

    res = await Updater().process_episode(session, ep)

    assert res["dropped"] == "consent:off"
    assert ep.dropped_reason == "consent:off"
    assert ep.processed is True
    assert await _node_count(session) == 0


async def test_org_off_overrides_user_scope(session):
    # deny-precedence: an org OFF cannot be loosened by a narrower (user) scope
    session.add(ConsentState(org_id="orgZ", scope="org", scope_id="orgZ", state="off"))
    await session.flush()
    ep = await _episode(session, event_id="g2", scope="user", scope_id="alice")

    res = await Updater().process_episode(session, ep)
    assert res["dropped"] == "consent:off"


async def test_source_deny_drops_item(session):
    session.add(
        ExtractionPolicy(
            org_id="orgZ", scope="org", scope_id="orgZ", policy={"deny_sources": ["billing"]}
        )
    )
    await session.flush()
    ep = await _episode(session, event_id="g3", source="billing")

    res = await Updater().process_episode(session, ep)
    assert res["dropped"] == "deny_source:billing"
    assert await _node_count(session) == 0


async def test_metadata_deny_drops_item(session):
    session.add(
        ExtractionPolicy(
            org_id="orgZ",
            scope="org",
            scope_id="orgZ",
            policy={"metadata_deny": {"channel": ["hr"]}},
        )
    )
    await session.flush()
    ep = await _episode(session, event_id="g4", payload={"channel": "hr", "statement": "x"})

    res = await Updater().process_episode(session, ep)
    assert res["dropped"] == "metadata_deny:channel"


async def test_active_consent_allows_fold(session):
    ep = await _episode(session, event_id="g5")
    res = await Updater().process_episode(session, ep)
    assert "dropped" not in res
    assert await _node_count(session) > 0


# ── admin surface ─────────────────────────────────────────────────────────────

ADMIN = {"X-Test-Org-Id": "orgZ", "X-Test-User-Id": "boss"}
ALICE = {
    "X-Test-Org-Id": "orgZ",
    "X-Test-User-Id": "alice",
    "X-Test-Permissions": "memory.read,memory.write",
}


async def _write_widgets(client, headers):
    episode = {"action_type": "fact.observed", "payload": {"statement": "widgets ship"}}
    r = await client.post(
        "/api/v1/episodes?process=true", json={"episodes": [episode]}, headers=headers
    )
    assert r.status_code == 202


async def test_clear_purges_scope(client):
    await _write_widgets(client, ADMIN)
    r = await client.get("/api/v1/memory/search", params={"q": "widgets"}, headers=ADMIN)
    assert any("widgets" in n["name"] for n in r.json()["nodes"])

    r2 = await client.put(
        "/api/v1/memory/consent",
        json={"scope": "org", "scope_id": "orgZ", "state": "clear"},
        headers=ADMIN,
    )
    assert r2.status_code == 200
    assert r2.json()["purged_nodes"] > 0
    assert r2.json()["state"] == "off"

    r3 = await client.get("/api/v1/memory/search", params={"q": "widgets"}, headers=ADMIN)
    assert not any("widgets" in n["name"] for n in r3.json()["nodes"])


async def test_user_cannot_govern_org_scope(client):
    r = await client.put(
        "/api/v1/memory/consent",
        json={"scope": "org", "scope_id": "orgZ", "state": "off"},
        headers=ALICE,
    )
    assert r.status_code == 403


async def test_user_can_opt_out_own_scope(client):
    r = await client.put(
        "/api/v1/memory/consent",
        json={"scope": "user", "scope_id": "alice", "state": "off"},
        headers=ALICE,
    )
    assert r.status_code == 200
    assert r.json()["state"] == "off"


async def test_user_cannot_govern_another_users_scope(client):
    # cross-user scope_id smuggling: a non-admin can't set consent for someone else's personal scope
    r = await client.put(
        "/api/v1/memory/consent",
        json={"scope": "user", "scope_id": "bob", "state": "off"},
        headers=ALICE,  # alice, pinned non-admin
    )
    assert r.status_code == 403
