"""Unit: memory v9 C4 — a running state per topic folded from conversation facts, as keyed state."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

from mlpal_memory_graph.pipeline.llm_extractor import ConversationTopicExtractor
from mlpal_memory_graph.pipeline.topic_state import fold_topic, render_topic_state, topic_slug

T = datetime(2023, 5, 28, tzinfo=UTC)


def test_add_counts_items_and_sums_money_by_unit():
    st = fold_topic(None, [
        {"fact": "User bought a bike chain for $25", "date": "2023-04-20", "op": "add", "qty": 25, "unit": "USD"},
        {"fact": "User got bike lights for $40", "date": "2023-05-05", "op": "add", "qty": 40, "unit": "USD"},
        {"fact": "User paid $120 for a tune-up", "date": "2023-05-10", "op": "add", "qty": 120, "unit": "USD"},
    ], "2023-05-10")
    assert st["count"] == 3 and st["sums"] == {"usd": 185.0} and st["as_of"] == "2023-05-10"
    line = render_topic_state("bike expenses", st)
    assert line.startswith("bike expenses: 3 items as of 2023-05-10; total 185 usd: 2023-04-20 User bought a bike chain")
    assert len(line) <= 400


def test_set_resets_the_tally_and_swallows_adds_dated_on_or_before_it():
    # v8 L31: "25 titles as of 05/28, plus Amistad and Hotel Rwanda added that same day" must read 25, not 27
    st = fold_topic(None, [
        {"fact": "User added Amistad to the to-watch list", "date": "2023-05-28", "op": "add", "qty": 1, "unit": "items"},
        {"fact": "User's to-watch list now has 25 titles", "date": "2023-05-28", "op": "set", "qty": 25, "unit": "items"},
        {"fact": "User added Hotel Rwanda to the to-watch list", "date": "2023-05-28", "op": "add", "qty": 1, "unit": "items"},
    ], "2023-05-28")
    assert st["count"] == 25
    later = fold_topic(st, [{"fact": "User added Casablanca to the to-watch list", "date": "2023-06-02", "op": "add", "qty": 1, "unit": "items"}], "2023-06-02")
    assert later["count"] == 26 and "stated 25 items on 2023-05-28" in render_topic_state("to-watch list", later)


def test_remove_and_replay_are_safe():
    st = fold_topic(None, [{"fact": "User acquired a peace lily", "date": "2023-05-05", "op": "add", "qty": None, "unit": None}], "2023-05-05")
    st = fold_topic(st, [{"fact": "User acquired a succulent", "date": "2023-05-12", "op": "add", "qty": None, "unit": None},
                         {"fact": "User acquired a peace lily", "date": "2023-05-05", "op": "add", "qty": None, "unit": None}], "2023-05-12")
    assert st["count"] == 2, "a replayed fact is not counted twice"
    st = fold_topic(st, [{"fact": "The succulent died", "date": "2023-05-20", "op": "remove", "qty": None, "unit": None}], "2023-05-20")
    assert st["count"] == 1
    st = fold_topic(st, [{"fact": "User gave away two plants", "date": "2023-05-21", "op": "remove", "qty": 2, "unit": "items"}], "2023-05-21")
    assert st["count"] == 0, "never negative"


def test_render_is_capped_and_skips_informational_items():
    facts = [{"fact": f"User attended charity run number {i} in a city with a very long name indeed", "date": f"2023-0{1 + i // 9}-{1 + i % 9:02d}", "op": "add", "qty": None, "unit": None} for i in range(30)]
    facts.append({"fact": "User likes running in the morning", "date": "2023-05-01", "op": "none", "qty": None, "unit": None})
    st = fold_topic(None, facts, "2023-05-01")
    line = render_topic_state("charity runs", st)
    assert st["count"] == 30 and line.startswith("charity runs: 30 items") and len(line) <= 400 and line.endswith("…")
    assert "likes running" not in line


class _Client:
    name = "fake"

    def __init__(self, facts):
        self.facts = facts
        self.calls = []

    async def complete_json(self, *, system, user, schema, max_tokens=None):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return {"facts": self.facts}


SESSION = "user: I picked up a snake plant yesterday, that makes three plants this month.\nassistant: Lovely, snake plants are easy."


def _ep():
    return SimpleNamespace(event_id="e1", actor={"user_id": "u1"}, content=SESSION, occurred_at=T, payload={"uri": "sess_9"})


async def test_topic_extractor_folds_onto_the_current_state_and_emits_keyed_state():
    client = _Client([
        {"fact": "User picked up a snake plant", "speaker": "user", "topic": "plants acquired", "op": "add",
         "quantity": {"value": 1, "unit": "items"}, "event_date": "2023-05-27", "evidence_span": "I picked up a snake plant yesterday"},
        {"fact": "User has three plants this month", "speaker": "user", "topic": "plants acquired", "op": "set",
         "quantity": {"value": 3, "unit": "items"}, "event_date": "2023-05-28", "evidence_span": "that makes three plants this month"},
        {"fact": "Made up", "speaker": "user", "topic": "plants acquired", "op": "add", "quantity": None, "event_date": None, "evidence_span": "not in the text"},
    ])
    prev = {"plants-acquired": ("plants acquired", fold_topic(None, [
        {"fact": "User acquired a peace lily", "date": "2023-05-05", "op": "add", "qty": 1, "unit": "items"},
        {"fact": "User acquired a succulent", "date": "2023-05-12", "op": "add", "qty": 1, "unit": "items"}], "2023-05-12"))}
    ex = ConversationTopicExtractor(client)
    out = await ex.extract(_ep(), reference_time=T, known_topics=["plants acquired"], current_states=prev)
    assert '"plants acquired"' in client.calls[0]["user"] and "session_date: 2023-05-28" in client.calls[0]["user"]
    assert "current_state" in client.calls[0]["user"] and "2023-05-12 User acquired a succulent" in client.calls[0]["user"]
    types = [e.type for e in out.entities]
    assert types == ["Metric", "MetricValue"] and out.entities[0].key == "state:conv/plants-acquired:u1"
    value = out.entities[1]
    assert value.props["count"] == 3 and value.props["unit"] == "state" and value.props["as_of"] == "2023-05-28"
    assert value.name.startswith("plants acquired: 3 items as of 2023-05-28")
    assert len(value.props["items"]) == 4, "the ungrounded fact was dropped, the grounded ones joined the evidence"
    assert [e.type for e in out.edges] == ["HAS_VALUE"] and out.edges[0].functional
    assert not [e for e in out.entities if e.type == "Fact"], "no fact list beside the passages (v8 L31)"


def test_topic_slug_is_stable():
    assert topic_slug("Plants acquired!") == "plants-acquired" and topic_slug("") == "topic"


def test_topics_mode_enables_the_extractor_without_the_judge(monkeypatch):
    from mlpal_memory_graph.core.config import get_settings
    from mlpal_memory_graph.pipeline import updater as up

    monkeypatch.setattr(get_settings(), "extractor", "topics")
    monkeypatch.setattr(up, "get_llm_extractor", lambda: "extractor")
    monkeypatch.setattr(up, "get_judge", lambda: "judge")
    u = up.Updater()
    assert u.llm_enabled and u.extractor_mode == "topics" and u.judge is None


async def test_a_topic_with_nothing_to_tally_emits_no_state():
    client = _Client([{"fact": "User is learning Premiere Pro", "speaker": "user", "topic": "Premiere Pro learning", "op": "none",
                       "quantity": None, "event_date": None, "evidence_span": "I picked up a snake plant yesterday"}])
    out = await ConversationTopicExtractor(client).extract(_ep(), reference_time=T)
    assert out.entities == [] and out.edges == []
