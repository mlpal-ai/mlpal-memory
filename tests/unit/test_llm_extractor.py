"""Unit: the deterministic offline LLM extractor + contradiction judge (no network)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from mlpal_memory_graph.pipeline.contradiction import DevContradictionJudge
from mlpal_memory_graph.pipeline.llm_extractor import DevLLMExtractor

T = datetime(2026, 1, 1, tzinfo=UTC)


def _ep(content, user="u1"):
    return SimpleNamespace(event_id="e", actor={"user_id": user}, content=content, occurred_at=T)


async def test_dev_extractor_one_fact_per_sentence_with_provenance():
    ex = await DevLLMExtractor().extract(
        _ep("The team uses Postgres. We deploy on Fridays."), reference_time=T
    )
    facts = [e for e in ex.entities if e.type == "Fact"]
    assert len(facts) == 2
    assert all(e.type == "DECIDED" for e in ex.edges)
    e0 = ex.edges[0]
    assert e0.props["evidence_span"] in ("The team uses Postgres.", "We deploy on Fridays.")
    assert e0.props["extraction_version"] and e0.props["prompt_version"]
    assert 0 < e0.props["confidence"] <= 1


async def test_dev_extractor_skips_fragments_and_empty():
    ex = await DevLLMExtractor().extract(
        _ep("ok. The team really likes async python a lot."), reference_time=T
    )
    facts = [e for e in ex.entities if e.type == "Fact"]
    assert len(facts) == 1  # "ok." is too short to be a fact
    empty = await DevLLMExtractor().extract(_ep(""), reference_time=T)
    assert empty.entities == [] and empty.edges == []  # no content → nothing


async def test_dev_extractor_is_deterministic():
    a = await DevLLMExtractor().extract(
        _ep("Alice owns the billing service today."), reference_time=T
    )
    b = await DevLLMExtractor().extract(
        _ep("Alice owns the billing service today."), reference_time=T
    )
    assert [e.key for e in a.entities] == [e.key for e in b.entities]


async def test_dev_judge_detects_negation_contradiction():
    j = DevContradictionJudge()
    assert await j.contradicts("the platform runs on aws", "the platform no longer runs on aws")
    # unrelated facts don't contradict
    assert not await j.contradicts("the platform runs on aws", "the team enjoys coffee")
    # a reassertion (same polarity) is not a contradiction
    assert not await j.contradicts("the platform runs on aws", "the platform runs on aws daily")
