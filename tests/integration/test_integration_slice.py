"""Integration phase-1 acceptance: one closed loop through the real REST surface.

This is the demo-as-a-test for the #17 vertical slice. A "session" is a distinct caller
identity making its own request; the MCP layer forwards exactly these identities (it sends the
caller's bearer token — see tests/unit/test_mcp_identity.py — and the REST API resolves it into
the AuthIdentity asserted here). We exercise the architecture end-to-end: push write → fold →
scope-correct retrieval → point-in-time (as_of) read.

Proves:
  1. cross-session persistence — session 1 writes, a *separate* session 2 reads it back;
  2. tenant isolation — another org cannot see it;
  3. personal-scope isolation — a peer in the same org cannot see user-scoped memory;
  4. point-in-time truth — an as_of query returns only what was valid then.

SQLite/offline via dev auth (X-Test-* headers), mirroring the rest of the suite.
"""

from __future__ import annotations

from datetime import UTC, datetime

# distinct "sessions": (org, user). Headers are the dev-auth identity the MCP would forward.
ALICE_A = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}
BOB_A = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "bob"}
CAROL_B = {"X-Test-Org-Id": "orgB", "X-Test-User-Id": "carol"}

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T1_5 = datetime(2026, 2, 1, tzinfo=UTC)
T2 = datetime(2026, 3, 1, tzinfo=UTC)


async def _write(client, headers, *, statement, occurred_at=None, scope=None, scope_id=None):
    """Seed one fact via the push path (POST /episodes), folded inline so it's queryable."""
    env: dict = {
        "action_type": "fact.observed",
        "actor": {"user_id": headers["X-Test-User-Id"]},  # so the DECIDED edge is created
        "payload": {"statement": statement},
    }
    if occurred_at is not None:
        env["occurred_at"] = occurred_at.isoformat()
    if scope is not None:
        env["scope"], env["scope_id"] = scope, scope_id
    r = await client.post(
        "/api/v1/episodes?process=true", json={"episodes": [env]}, headers=headers
    )
    assert r.status_code == 202, r.text


async def _search_names(client, headers, q, **params):
    r = await client.get("/api/v1/memory/search", params={"q": q, **params}, headers=headers)
    assert r.status_code == 200, r.text
    return {n["name"] for n in r.json()["nodes"]}


async def test_cross_session_retrieval_and_tenant_isolation(client):
    # session 1: alice (orgA) records two facts — one org-wide, one personal
    await _write(client, ALICE_A, statement="the checkout service uses stripe for payments")
    await _write(
        client, ALICE_A, statement="alice prefers dark mode", scope="user", scope_id="alice"
    )

    # session 2: alice queries in a *separate* request and gets her org fact back (persistence)
    assert "the checkout service uses stripe for payments" in await _search_names(
        client, ALICE_A, "checkout"
    )

    # tenant isolation: carol in a different org sees nothing of orgA's memory
    assert await _search_names(client, CAROL_B, "checkout") == set()

    # personal-scope isolation: bob (same org) cannot see alice's user-scoped fact...
    assert "alice prefers dark mode" not in await _search_names(client, BOB_A, "dark mode")
    # ...but alice can
    assert "alice prefers dark mode" in await _search_names(client, ALICE_A, "dark mode")


async def test_write_cannot_smuggle_into_another_tenant(client):
    # alice (orgA) tries to plant a fact in orgB by putting org_id in the body
    r = await client.post(
        "/api/v1/episodes?process=true",
        json={
            "episodes": [
                {
                    "org_id": "orgB",  # ← smuggled tenant
                    "actor": {"user_id": "alice"},
                    "action_type": "fact.observed",
                    "payload": {"statement": "planted cross tenant fact"},
                }
            ]
        },
        headers=ALICE_A,  # authenticated as orgA
    )
    assert r.status_code == 202, r.text

    # it must have landed in orgA (the caller's tenant), NOT orgB
    assert "planted cross tenant fact" not in await _search_names(client, CAROL_B, "planted")
    assert "planted cross tenant fact" in await _search_names(client, ALICE_A, "planted")


async def test_service_key_may_target_a_tenant(client):
    # the trusted machine-to-machine path keeps multi-tenant ingest (per-episode org_id)
    svc = {"X-Internal-Service-Key": "dev-internal-key"}
    r = await client.post(
        "/api/v1/episodes?process=true",
        json={
            "episodes": [
                {
                    "org_id": "orgC",
                    "actor": {"user_id": "system"},
                    "action_type": "fact.observed",
                    "payload": {"statement": "ingested for org c"},
                }
            ]
        },
        headers=svc,
    )
    assert r.status_code == 202, r.text
    # a reader in orgC sees it; an unrelated tenant does not
    org_c = {"X-Test-Org-Id": "orgC", "X-Test-User-Id": "carl"}
    assert "ingested for org c" in await _search_names(client, org_c, "ingested")
    assert "ingested for org c" not in await _search_names(client, CAROL_B, "ingested")


async def test_cannot_plant_in_another_users_personal_scope(client):
    # a non-admin caller can't write to someone else's user scope (mirrors document ingest guard).
    # dev-auth defaults to ['*'] (admin), so pin non-admin perms to exercise the guard.
    alice_user = {**ALICE_A, "X-Test-Permissions": "memory.read,memory.write"}
    r = await client.post(
        "/api/v1/episodes?process=true",
        json={
            "episodes": [
                {
                    "scope": "user",
                    "scope_id": "bob",  # ← not the caller
                    "actor": {"user_id": "alice"},
                    "action_type": "fact.observed",
                    "payload": {"statement": "planted in bobs head"},
                }
            ]
        },
        headers=alice_user,
    )
    assert r.status_code == 403, r.text


async def test_non_admin_cannot_write_subject_scope(client):
    # HARD GATE: subject scopes (team/service/repo/agent) need per-subject write authz we don't
    # model yet, so a non-admin/non-service caller is refused (task #21).
    alice_user = {**ALICE_A, "X-Test-Permissions": "memory.read,memory.write"}
    r = await client.post(
        "/api/v1/episodes?process=true",
        json={
            "episodes": [
                {
                    "scope": "service",
                    "scope_id": "checkout",
                    "actor": {"user_id": "alice"},
                    "action_type": "fact.observed",
                    "payload": {"statement": "checkout uses adyen"},
                }
            ]
        },
        headers=alice_user,
    )
    assert r.status_code == 403, r.text


async def test_as_of_shows_point_in_time_truth(client):
    # two decisions about the same area, recorded at different world-times
    await _write(client, ALICE_A, statement="alpha rollout decision", occurred_at=T1)
    await _write(client, ALICE_A, statement="beta rollout decision", occurred_at=T2)

    async def facts_as_of(instant):
        r = await client.get(
            "/api/v1/memory/search",
            params={"q": "decision", "as_of": instant.isoformat(), "depth": 1},
            headers=ALICE_A,
        )
        assert r.status_code == 200, r.text
        return {e["fact"] for e in r.json()["edges"] if e.get("fact")}

    # at T1.5 only the alpha decision was valid; beta hadn't happened yet
    at_15 = await facts_as_of(T1_5)
    assert any("alpha" in f for f in at_15)
    assert not any("beta" in f for f in at_15)

    # by T2 both are valid
    at_2 = await facts_as_of(T2)
    assert any("alpha" in f for f in at_2)
    assert any("beta" in f for f in at_2)
