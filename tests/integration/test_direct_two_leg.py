"""Regression: direct-tier search must not require the literal query substring.

Before the fix, `ILIKE %query%` was a hard prefilter ahead of cosine ranking — a
semantically relevant passage without the exact substring was unreachable. Now the
lexical match is one leg of an RRF fusion; the semantic leg reaches everything.
"""

from __future__ import annotations

import pytest

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.services.direct import DirectMemory

ORG = "orgA"
SCOPE = ScopeRef(Scope.ORG, ORG)


@pytest.mark.asyncio
async def test_semantic_leg_reaches_passages_without_the_substring(session):
    dm = DirectMemory()
    # shares tokens ("deployment", "cluster") with the query but NOT the phrase
    await dm.add_document(
        session,
        tenant_id=ORG,
        scope=SCOPE,
        content="Our cluster deployment relies on rolling updates and health probes.",
        source="test",
    )
    # contains the literal phrase
    await dm.add_document(
        session,
        tenant_id=ORG,
        scope=SCOPE,
        content="See the kubernetes deployment guide for the full manifest reference.",
        source="test",
    )
    await session.commit()

    hits = await dm.search(
        session,
        tenant_id=ORG,
        scopes=[SCOPE],
        query="kubernetes deployment",
        limit=10,
    )
    contents = [h.chunk.content for h in hits]
    assert any("rolling updates" in c for c in contents), (
        "semantic-only passage must be reachable without the literal substring"
    )
    assert any("manifest reference" in c for c in contents)


@pytest.mark.asyncio
async def test_lexical_wildcards_are_escaped(session):
    dm = DirectMemory()
    await dm.add_document(
        session,
        tenant_id=ORG,
        scope=SCOPE,
        content="discount table: 100% is a full discount",
        source="test",
    )
    await session.commit()
    # a bare % in the query must not become a match-everything wildcard (and must not error)
    hits = await dm.search(
        session, tenant_id=ORG, scopes=[SCOPE], query="100%", limit=5
    )
    assert hits  # matches via both legs; no SQL error


@pytest.mark.asyncio
async def test_empty_scopes_still_return_nothing(session):
    dm = DirectMemory()
    assert await dm.search(session, tenant_id=ORG, scopes=[], query="anything") == []


@pytest.mark.asyncio
async def test_single_leg_ablation(session):
    """`legs` runs one leg alone (the evals' naive-RAG / FTS baselines): the lexical-only
    arm never embeds the query, and both arms still return scoped results."""
    dm = DirectMemory()
    await dm.add_document(
        session, tenant_id=ORG, scope=SCOPE,
        content="the deploy pipeline promotes canaries before full rollout",
        source="test",
    )
    await session.commit()

    calls = []
    orig = dm.embedder.embed_one

    async def counting(text):
        calls.append(text)
        return await orig(text)

    dm.embedder.embed_one = counting
    lex = await dm.search(
        session, tenant_id=ORG, scopes=[SCOPE], query="deploy canaries", legs={"lexical"}
    )
    assert lex and not calls, "lexical-only must not embed the query"
    vec = await dm.search(
        session, tenant_id=ORG, scopes=[SCOPE], query="deploy canaries", legs={"vector"}
    )
    assert vec and calls
