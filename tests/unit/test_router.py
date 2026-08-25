from __future__ import annotations

from mlpal_memory_graph.pipeline.router import extraction_tier, importance


def test_lifecycle_events_are_deterministic_even_when_important():
    # no raw content → never the LLM path, regardless of importance
    for a in ("agent.deployed", "mcp.deployed", "skill.published", "org.member_added"):
        assert extraction_tier(a, has_content=False) == "deterministic"
        assert importance(a) >= 0.6  # still high-importance for retrieval


def test_content_bearing_with_content_is_llm():
    assert extraction_tier("chat.message", has_content=True) == "llm"
    assert extraction_tier("agent.decided", has_content=True) == "llm"


def test_content_bearing_without_content_stays_deterministic():
    # a content-bearing action_type but nothing actually captured → no LLM
    assert extraction_tier("chat.message", has_content=False) == "deterministic"


def test_non_content_action_with_content_is_still_deterministic():
    # raw content present but not a known content-bearing type → don't LLM unknown content
    assert extraction_tier("agent.deployed", has_content=True) == "deterministic"


def test_signals_are_low_importance():
    assert importance("mcp.tool.invoked") < 0.3
    assert importance("usage.recorded") < 0.3
