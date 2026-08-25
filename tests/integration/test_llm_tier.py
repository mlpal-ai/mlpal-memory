"""#14 PR5: the LLM extraction tier + contradiction judge, end-to-end through the fold.

Offline/deterministic: the dev LLM extractor (sentence→Fact) and dev judge (negation heuristic)
run with no network. We drive Updater(llm_enabled=True) on synthetic content and assert: facts are
mined from content, provenance is stamped, content is redacted BEFORE the LLM sees it, a later fact
that contradicts an earlier one closes the older edge, and the tier is off by default.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select

from mlpal_memory_graph.db.models import Edge, Episode, Node
from mlpal_memory_graph.pipeline.updater import Updater

T1 = datetime(2026, 1, 1, tzinfo=UTC)
T2 = datetime(2026, 3, 1, tzinfo=UTC)
ORG = "orgL"


def _naive(dt):
    return dt.replace(tzinfo=None) if dt is not None and dt.tzinfo else dt


async def _fold(
    session,
    updater,
    *,
    action_type,
    event_id,
    occurred_at,
    content=None,
    statement=None,
    user="alice",
):
    ep = Episode(
        event_id=event_id,
        occurred_at=occurred_at,
        org_id=ORG,
        scope="org",
        scope_id=ORG,
        actor={"user_id": user},
        source="backend",
        action_type=action_type,
        subject={},
        payload={"statement": statement} if statement else {},
        content=content,
        processed=False,
    )
    session.add(ep)
    await session.flush()
    res = await updater.process_episode(session, ep)
    await session.flush()
    return res, ep


def test_llm_tier_off_by_default():
    # MLPAL_EXTRACTOR defaults to "rule" → no LLM extraction or judge unless explicitly enabled.
    assert Updater().llm_enabled is False


async def test_llm_tier_mines_facts_from_content_with_provenance(session):
    u = Updater(llm_enabled=True)
    res, _ = await _fold(
        session,
        u,
        action_type="chat.message",
        event_id="L1",
        occurred_at=T1,
        content="The team chose Postgres for storage. Alice will own the migration.",
    )
    assert res["tier"] == "llm"

    facts = {
        n.name
        for n in (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().all()
    }
    assert any("Postgres" in f for f in facts)

    # every LLM-extracted DECIDED edge is stamped with provenance for replay
    decided = (await session.execute(select(Edge).where(Edge.type == "DECIDED"))).scalars().all()
    assert decided
    assert all(e.props.get("extraction_version") and e.props.get("prompt_version") for e in decided)
    assert all("evidence_span" in e.props for e in decided)


async def test_llm_extraction_sees_redacted_content(session):
    u = Updater(llm_enabled=True)
    await _fold(
        session,
        u,
        action_type="chat.message",
        event_id="R1",
        occurred_at=T1,
        content="The deploy uses api_key = s3cr3tABC123 for the prod cluster.",
    )
    facts = (await session.execute(select(Node).where(Node.type == "Fact"))).scalars().all()
    blob = " ".join(f.name for f in facts)
    assert facts  # the (redacted) fact is still extracted
    assert "s3cr3tABC123" not in blob  # the secret was vaulted before the LLM saw the content
    assert "mlpal-secret://" in blob


async def test_contradiction_judge_closes_the_older_fact(session):
    u = Updater(llm_enabled=True)
    await _fold(
        session,
        u,
        action_type="fact.observed",
        event_id="K1",
        occurred_at=T1,
        statement="the platform runs on aws",
    )
    res, _ = await _fold(
        session,
        u,
        action_type="fact.observed",
        event_id="K2",
        occurred_at=T2,
        statement="the platform no longer runs on aws",
    )
    assert res["contradictions"] >= 1

    by_fact = {
        e.fact: e
        for e in (await session.execute(select(Edge).where(Edge.type == "DECIDED"))).scalars().all()
    }
    # the earlier claim is closed at the later claim's time; the later one stays open (append-only)
    assert _naive(by_fact["the platform runs on aws"].invalid_at) == _naive(T2)
    assert by_fact["the platform no longer runs on aws"].invalid_at is None


async def test_no_contradiction_for_unrelated_facts(session):
    u = Updater(llm_enabled=True)
    await _fold(
        session,
        u,
        action_type="fact.observed",
        event_id="N1",
        occurred_at=T1,
        statement="the platform runs on aws",
    )
    res, _ = await _fold(
        session,
        u,
        action_type="fact.observed",
        event_id="N2",
        occurred_at=T2,
        statement="the team enjoys coffee on fridays",
    )
    assert res["contradictions"] == 0  # unrelated → both stay open
    open_decided = (
        (
            await session.execute(
                select(Edge).where(Edge.type == "DECIDED", Edge.invalid_at.is_(None))
            )
        )
        .scalars()
        .all()
    )
    assert len(open_decided) == 2
