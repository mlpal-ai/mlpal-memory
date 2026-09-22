"""Unit: memory v8 C3 conversation-fact extractor — dated, grounded facts as Fact nodes beside passages."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from mlpal_memory_graph.pipeline.llm_extractor import ConversationFactExtractor

T = datetime(2023, 3, 14, tzinfo=UTC)
SESSION = "user: I just did the Walk for Hunger 5K two weeks ago and loved it.\nassistant: Great! Consider the Relay For Life in June."


class _Client:
    name = "fake"

    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    async def complete_json(self, *, system, user, schema, max_tokens=None):
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens})
        return {"facts": self.facts}


def _ep(content=SESSION):
    return SimpleNamespace(event_id="e1", actor={"user_id": "u1"}, content=content, occurred_at=T, payload={"uri": "sess_1"})


async def test_dated_fact_names_and_props():
    client = _Client([
        {"fact": "User participated in the Walk for Hunger 5K on 2023-02-28.", "speaker": "user", "event_date": "2023-02-28",
         "evidence_span": "I just did the Walk for Hunger 5K two weeks ago"},
        {"fact": "The assistant suggested the Relay For Life in June.", "speaker": "assistant", "event_date": None,
         "evidence_span": "Consider the Relay For Life in June."},
    ])
    ex = await ConversationFactExtractor(client).extract(_ep(), reference_time=T)
    facts = [e for e in ex.entities if e.type == "Fact"]
    assert [f.name for f in facts] == ["[2023-02-28] User participated in the Walk for Hunger 5K on 2023-02-28.",
                                       "[2023-03-14] The assistant suggested the Relay For Life in June."]
    assert facts[0].props["event_date"] == "2023-02-28" and facts[0].props["mention_date"] == "2023-03-14"
    assert facts[1].props["speaker"] == "assistant" and facts[1].props["source_uri"] == "sess_1"
    assert all(e.type == "DECIDED" and e.props["evidence_span"] for e in ex.edges)
    assert "session_date: 2023-03-14" in client.calls[0]["user"]


async def test_ungrounded_facts_are_dropped_and_empty_content_makes_no_call():
    client = _Client([{"fact": "User owns three tanks.", "speaker": "user", "event_date": None, "evidence_span": "three tanks in the garage"}])
    ex = await ConversationFactExtractor(client).extract(_ep(), reference_time=T)
    assert [e for e in ex.entities if e.type == "Fact"] == []
    ex2 = await ConversationFactExtractor(client).extract(_ep(""), reference_time=T)
    assert ex2.entities == [] and len(client.calls) == 1


def test_facts_mode_enables_the_extractor_without_the_judge(monkeypatch):
    from mlpal_memory_graph.core.config import get_settings
    from mlpal_memory_graph.pipeline import updater as up

    monkeypatch.setattr(get_settings(), "extractor", "facts")
    monkeypatch.setattr(up, "get_llm_extractor", lambda: "extractor")
    monkeypatch.setattr(up, "get_judge", lambda: "judge")
    u = up.Updater()
    assert u.llm_enabled and u.llm_extractor == "extractor" and u.judge is None
