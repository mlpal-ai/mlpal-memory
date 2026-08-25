"""PR2 Postgres-only validation (P1.1): the pgvector `<=>` path is scope-filtered and ranked,
and matches the Python-cosine baseline. Opt-in — set MLPAL_TEST_POSTGRES_DSN to a real
Postgres+pgvector (e.g. the docker-compose pg) to run; skipped in the offline SQLite suite.

    MLPAL_TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:pw@localhost:5432/mlpal \\
      pytest -m postgres tests/integration/test_pgvector.py
"""

from __future__ import annotations

import os

import pytest

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.db.scoping import cosine
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver
from mlpal_memory_graph.services.embeddings_client import get_embedder

# pg_session comes from tests/integration/conftest.py — an `alembic upgrade head` migrated schema.
DSN = os.getenv("MLPAL_TEST_POSTGRES_DSN")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="set MLPAL_TEST_POSTGRES_DSN to run pgvector tests"),
]


async def _add(drv, session, tenant, scope, key, text_, emb):
    v = await emb.embed_one(text_)
    await drv.upsert_node(
        session,
        tenant_id=tenant,
        scope=scope,
        type_="Fact",
        key=key,
        name=text_,
        embedding=v,
        embedding_model=emb.name,
        embedding_dim=emb.dim,
    )


async def test_pgvector_search_is_scope_filtered_and_ranked(pg_session):
    drv, emb = PostgresDriver(), get_embedder()
    a = ScopeRef(Scope.ORG, "orgA")
    facts = ["postgres is the database", "kubernetes runs the pods", "alice owns checkout"]
    for i, t in enumerate(facts):
        await _add(drv, pg_session, "orgA", a, f"k{i}", t, emb)
    # decoy in another tenant with the *closest* text — must never leak
    await _add(
        drv, pg_session, "orgB", ScopeRef(Scope.ORG, "orgB"), "d0", "postgres is the database", emb
    )
    await pg_session.commit()

    # query the exact text of k0 (the offline embedder is bag-of-tokens, so an exact match → ~1.0)
    q = await emb.embed_one("postgres is the database")
    hits = await drv.search_nodes(
        pg_session, tenant_id="orgA", scopes=[a], query_embedding=q, limit=3
    )

    assert hits and all(h.node.org_id == "orgA" for h in hits), "scope filter must exclude orgB"
    assert hits[0].node.name == "postgres is the database", "nearest fact ranks first"
    assert hits[0].score == pytest.approx(1.0, abs=0.05)


async def test_pgvector_matches_python_cosine_topk(pg_session):
    drv, emb = PostgresDriver(), get_embedder()
    a = ScopeRef(Scope.ORG, "orgA")
    facts = ["postgres database", "kubernetes pods", "stripe billing", "github pull request"]
    for i, t in enumerate(facts):
        await _add(drv, pg_session, "orgA", a, f"k{i}", t, emb)
    await pg_session.commit()

    q = await emb.embed_one("the database is postgres")
    pg_hits = await drv.search_nodes(
        pg_session, tenant_id="orgA", scopes=[a], query_embedding=q, limit=3
    )
    pg_ids = [h.node.id for h in pg_hits]

    # Python-cosine baseline over the same rows
    all_nodes = await drv.search_nodes(pg_session, tenant_id="orgA", scopes=[a], limit=100)
    baseline = sorted(all_nodes, key=lambda h: cosine(q, h.node.embedding), reverse=True)[:3]
    baseline_ids = [h.node.id for h in baseline]
    assert pg_ids == baseline_ids, "pgvector top-k must match the Python-cosine baseline"
