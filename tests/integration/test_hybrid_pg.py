"""#12 hybrid + #18 filtered-recall on REAL Postgres (pgvector + pg_trgm). Opt-in via
MLPAL_TEST_POSTGRES_DSN. The vector + BM25 (tsvector/pg_trgm) legs and the filter-after-ANN
recall fix are validated here; RRF / node-distance fusion is SQLite-tested (portable).

    MLPAL_TEST_POSTGRES_DSN=postgresql+asyncpg://mlpal:***@127.0.0.1:5432/mlpal \\
      pytest -m postgres tests/integration/test_hybrid_pg.py
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.db.models import Edge
from mlpal_memory_graph.graph.drivers.postgres import PostgresDriver
from mlpal_memory_graph.services.embeddings_client import get_embedder

# pg_session comes from tests/integration/conftest.py — an `alembic upgrade head` migrated schema
# (NOT create_all + hand-added extensions), so the pg_trgm/FTS path matches prod, not a bootstrap.
DSN = os.getenv("MLPAL_TEST_POSTGRES_DSN")
pytestmark = [
    pytest.mark.postgres,
    pytest.mark.skipif(not DSN, reason="set MLPAL_TEST_POSTGRES_DSN to run"),
]

A = ScopeRef(Scope.ORG, "orgA")
B = ScopeRef(Scope.ORG, "orgB")


# ── BM25 lexical leg (tsvector + pg_trgm) ─────────────────────────────────────


async def test_lexical_leg_on_real_pg(pg_session):
    drv, emb = PostgresDriver(), get_embedder()
    for k in ("checkout-svc", "billing-svc"):
        await drv.upsert_node(pg_session, tenant_id="orgA", scope=A, type_="Service", key=k, name=k)
    await drv.upsert_node(
        pg_session,
        tenant_id="orgA",
        scope=A,
        type_="Fact",
        key="f1",
        name="the platform runs on postgres",
    )
    await pg_session.commit()
    _ = emb

    # identifier recall via pg_trgm/prefix
    ids = {
        h.node.name
        for h in await drv.lexical_search_nodes(
            pg_session, tenant_id="orgA", scopes=[A], text="checkout"
        )
    }
    assert "checkout-svc" in ids and "billing-svc" not in ids
    # FTS word match via tsvector
    fts = {
        h.node.name
        for h in await drv.lexical_search_nodes(
            pg_session, tenant_id="orgA", scopes=[A], text="postgres"
        )
    }
    assert "the platform runs on postgres" in fts


async def test_graph_distance_recursive_cte_on_real_pg(pg_session):
    drv = PostgresDriver()
    for t, k in (("User", "alice"), ("Fact", "f1"), ("Fact", "f2"), ("Fact", "f3")):
        await drv.upsert_node(pg_session, tenant_id="orgA", scope=A, type_=t, key=k, name=k)
    nid = {
        k: (await drv.find_node(pg_session, "orgA", A, t, k)).id
        for t, k in (("User", "alice"), ("Fact", "f1"), ("Fact", "f2"), ("Fact", "f3"))
    }
    await drv.upsert_edge(
        pg_session,
        tenant_id="orgA",
        scope=A,
        type_="DECIDED",
        src_id=nid["alice"],
        dst_id=nid["f1"],
    )
    await drv.upsert_edge(
        pg_session,
        tenant_id="orgA",
        scope=A,
        type_="RELATES_TO",
        src_id=nid["f1"],
        dst_id=nid["f2"],
    )
    await pg_session.commit()
    d = await drv.graph_distance(
        pg_session,
        anchor_ids=[nid["alice"]],
        candidate_ids=[nid["f1"], nid["f2"], nid["f3"]],
        max_depth=3,
    )
    assert d.get(nid["f1"]) == 1 and d.get(nid["f2"]) == 2 and nid["f3"] not in d


# ── #18: filter-after-ANN recall on the heavily-filtered edge fact-vector path ─

PAST = datetime(2025, 1, 1, tzinfo=UTC)
T0 = datetime(2026, 1, 1, tzinfo=UTC)
MID = datetime(2025, 6, 1, tzinfo=UTC)
N_DECOY, N_STALE, N_TARGET = 200, 50, 20


async def _seed_recall_dataset(session, emb):
    """Decoys (orgB) and stale (orgA, invalidated) sit *nearer* the query than the targets
    (orgA, current), so the HNSW candidate pool is dominated by rows the WHERE filters drop —
    the filter-after-ANN gotcha. The HNSW index on edges.fact_embedding comes from migration 0007
    (the migrated schema), so the ANN path is used."""
    near = await emb.embed_one("alpha")  # decoys/stale fact text == query → distance 0
    far = await emb.embed_one("alpha beta")  # targets → strictly farther (~0.29)
    rows = []

    def edge(i, scope, fact_vec, *, org, invalid_at, valid_at=PAST):
        return Edge(
            org_id=org,
            scope="org",
            scope_id=org,
            type="DECIDED",
            src_id=f"s{i}",
            dst_id=f"d{i}",
            fact="alpha",
            fact_embedding=fact_vec,
            embedding_model=emb.name,
            embedding_dim=emb.dim,
            valid_at=valid_at,
            invalid_at=invalid_at,
        )

    for i in range(N_DECOY):
        rows.append(edge(i, B, near, org="orgB", invalid_at=None))
    for i in range(N_STALE):
        rows.append(edge(10_000 + i, A, near, org="orgA", invalid_at=T0))  # ended at T0
    for i in range(N_TARGET):
        rows.append(edge(20_000 + i, A, far, org="orgA", invalid_at=None))
    session.add_all(rows)
    await session.commit()  # HNSW index (ix_edge_fact_embedding_hnsw) already exists from 0007


async def _force_hnsw(session, ef_search=40):
    """On a 270-row test table the planner seqscans (exact) and bypasses HNSW — so the filter-
    after-ANN regime never appears (a seqscan can't lose recall). Force the index path with a
    bounded candidate pool to reproduce the large-table behavior prod actually runs. The seed's
    vector inserts already loaded vector.so so the hnsw.* GUCs exist on this backend."""
    await session.execute(text("SET LOCAL enable_seqscan = off"))
    await session.execute(text(f"SET LOCAL hnsw.ef_search = {ef_search}"))


async def test_filtered_recall_with_iterative_scan(pg_session):
    drv, emb = PostgresDriver(), get_embedder()
    if not await drv._supports_iterative_scan(pg_session):
        pytest.skip("needs pgvector >= 0.8 (hnsw.iterative_scan)")
    await _seed_recall_dataset(pg_session, emb)
    q = await emb.embed_one("alpha")
    await _force_hnsw(pg_session)

    # current view: org + invalid_at filters drop the 250 nearer rows; iterative_scan must still
    # recover all 20 targets (complete recall, no filter-after-ANN loss) and never leak orgB/stale.
    hits = await drv.search_fact_edges(
        pg_session, tenant_id="orgA", scopes=[A], query_embedding=q, limit=N_TARGET
    )
    assert len(hits) == N_TARGET, f"recall loss: got {len(hits)}/{N_TARGET}"
    assert all(e.org_id == "orgA" and e.invalid_at is None for e, _ in hits)


async def test_iterative_scan_recall_complete_and_never_worse(pg_session):
    # Our scope/temporal indexes are selective enough that the planner often uses an exact path
    # (no recall loss) — iterative_scan is the safety net for when HNSW IS chosen. The robust
    # invariant that holds either way: iterative_scan gives COMPLETE filtered recall and is NEVER
    # worse than classic HNSW (off). (A strict "classic loses recall" assertion is planner-
    # dependent and flaky here precisely because the filter is selectively indexable.)
    drv, emb = PostgresDriver(), get_embedder()
    if not await drv._supports_iterative_scan(pg_session):
        pytest.skip("needs pgvector >= 0.8")
    await _seed_recall_dataset(pg_session, emb)
    q = await emb.embed_one("alpha")
    await _force_hnsw(pg_session)

    classic = (
        await pg_session.execute(
            text(
                "SELECT count(*) FROM (SELECT id FROM edges "
                "WHERE org_id = 'orgA' AND invalid_at IS NULL AND fact_embedding IS NOT NULL "
                "ORDER BY fact_embedding <=> (:q)::vector LIMIT :k) t"
            ).bindparams(q=str(q), k=N_TARGET)
        )
    ).scalar()
    fixed = len(
        await drv.search_fact_edges(
            pg_session, tenant_id="orgA", scopes=[A], query_embedding=q, limit=N_TARGET
        )
    )
    assert fixed == N_TARGET  # iterative_scan → complete filtered recall
    assert fixed >= classic  # never worse than classic HNSW


async def test_filtered_recall_as_of(pg_session):
    # as_of before the stale edges ended (T0): they were valid then → an as_of query includes them,
    # and iterative_scan must recover them under the forced-HNSW + temporal filter.
    drv, emb = PostgresDriver(), get_embedder()
    if not await drv._supports_iterative_scan(pg_session):
        pytest.skip("needs pgvector >= 0.8")
    await _seed_recall_dataset(pg_session, emb)
    q = await emb.embed_one("alpha")
    await _force_hnsw(pg_session)
    hits = await drv.search_fact_edges(
        pg_session,
        tenant_id="orgA",
        scopes=[A],
        query_embedding=q,
        as_of=MID,
        limit=N_STALE + N_TARGET,
    )
    # at MID the stale edges were still valid (ended at T0 > MID); targets valid from PAST too
    assert all(e.org_id == "orgA" for e, _ in hits)
    assert len(hits) >= N_STALE  # the once-valid edges are recovered, not silently dropped
