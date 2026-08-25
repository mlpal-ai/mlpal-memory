"""PR6: the RLS backstop (migration 0009) genuinely isolates tenants at the DB layer.

A superuser bypasses RLS, so we exercise it through a throwaway NON-superuser role: with the
app.current_org GUC set, that role sees only its tenant's rows; with the GUC unset the policy is
permissive (the app-layer scope_clause stays primary). @pytest.mark.postgres against the migrated
schema (which has 0009's policies)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver

DSN = os.getenv("MLPAL_TEST_POSTGRES_DSN")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="set MLPAL_TEST_POSTGRES_DSN to run"),
]

SCHEMA = "memory_ci_test"  # the migrated schema from tests/integration/conftest.py::pg_session
ROLE = "rls_probe_role"


async def test_rls_backstops_cross_tenant_for_non_superuser(pg_session):
    drv = PostgresDriver()
    await drv.upsert_node(
        pg_session,
        tenant_id="orgA",
        scope=ScopeRef(Scope.ORG, "orgA"),
        type_="Fact",
        key="fa",
        name="a",
    )
    await drv.upsert_node(
        pg_session,
        tenant_id="orgB",
        scope=ScopeRef(Scope.ORG, "orgB"),
        type_="Fact",
        key="fb",
        name="b",
    )
    await pg_session.commit()

    # a non-superuser role with SELECT — superusers bypass RLS, so the backstop only bites here
    await pg_session.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
    await pg_session.execute(text(f"CREATE ROLE {ROLE} LOGIN PASSWORD 'probe' NOSUPERUSER"))
    await pg_session.execute(text(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {ROLE}"))
    await pg_session.execute(text(f"GRANT SELECT ON ALL TABLES IN SCHEMA {SCHEMA} TO {ROLE}"))
    await pg_session.commit()

    import asyncpg

    url = make_url(DSN)
    try:
        conn = await asyncpg.connect(
            host=url.host,
            port=url.port,
            user=ROLE,
            password="probe",
            database=url.database,
            server_settings={"search_path": f"{SCHEMA},public"},
        )
        try:
            # GUC set to orgA → only orgA rows are visible (orgB blocked by RLS)
            await conn.execute("SELECT set_config('app.current_org', 'orgA', false)")
            scoped = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM nodes")}
            assert scoped == {"orgA"}, f"RLS leak: saw {scoped}"

            # GUC unset → permissive (app-layer stays primary): both tenants visible
            await conn.execute("SELECT set_config('app.current_org', NULL, false)")
            both = {r["org_id"] for r in await conn.fetch("SELECT org_id FROM nodes")}
            assert both == {"orgA", "orgB"}
        finally:
            await conn.close()
    finally:
        await pg_session.execute(text(f"REVOKE ALL ON ALL TABLES IN SCHEMA {SCHEMA} FROM {ROLE}"))
        await pg_session.execute(text(f"REVOKE ALL ON SCHEMA {SCHEMA} FROM {ROLE}"))
        await pg_session.execute(text(f"DROP ROLE IF EXISTS {ROLE}"))
        await pg_session.commit()
