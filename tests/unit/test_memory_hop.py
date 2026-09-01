"""The memory hop: bounded loop, reformulation, budget stop, citation enforcement."""

from __future__ import annotations

import pytest

from mlpal_memory_graph.services import memory_hop
from mlpal_memory_graph.services.memory_hop import enforce_citations, run_memory_hop


class StubClient:
    def __init__(self, decisions):
        self.decisions = list(decisions)
        self.json_calls = 0
        self.text_calls = 0

    async def complete_json(self, **_):
        self.json_calls += 1
        return self.decisions.pop(0)

    async def complete_text(self, **_):
        self.text_calls += 1
        return "Composed from packets [memory://chunk/aaa].", {}


def _packets(mapping):
    async def fetch(q):
        return mapping.get(q, f"# {q}\n> Memory holds no relevant knowledge.")
    return fetch


@pytest.mark.asyncio
async def test_hop_reformulates_then_answers(monkeypatch):
    stub = StubClient([
        {"action": "search", "queries": ["valkey tls connection"]},
        {"action": "answer", "answer": "It is valkey 8.2, TLS-only [memory://chunk/bbb]."},
    ])
    monkeypatch.setattr(memory_hop, "get_llm_client", lambda: stub)
    res = await run_memory_hop(
        query="what cache engine do we use",
        fetch_packet=_packets({
            "what cache engine do we use": "# q\nEvidence mentions valkey [memory://chunk/aaa]",
            "valkey tls connection": "# q2\nvalkey 8.2 tls only [memory://chunk/bbb]",
        }),
        max_hops=3,
    )
    assert res.answer.startswith("It is valkey 8.2")
    assert res.trace == ["what cache engine do we use", "valkey tls connection"]
    assert res.hops == 2 and res.invented_citations == 0


@pytest.mark.asyncio
async def test_hop_budget_forces_final_compose(monkeypatch):
    stub = StubClient([
        {"action": "search", "queries": ["a"]},
        {"action": "search", "queries": ["b"]},
    ])
    monkeypatch.setattr(memory_hop, "get_llm_client", lambda: stub)
    res = await run_memory_hop(
        query="q0",
        fetch_packet=_packets({
            "q0": "packet [memory://chunk/aaa]",
            "a": "found more [memory://chunk/bbb]",  # new citations keep the loop alive
            "b": "and more [memory://chunk/ccc]",
        }),
        max_hops=2,
    )
    # 2 hop decisions + 1 forced compose; never exceeds budget+1 model calls
    assert res.hops == 3 and stub.text_calls == 1
    assert res.answer.startswith("Composed")


@pytest.mark.asyncio
async def test_hop_never_repeats_queries_and_stops_on_stall(monkeypatch):
    stub = StubClient([{"action": "search", "queries": ["q0"]}])  # repeat of original
    monkeypatch.setattr(memory_hop, "get_llm_client", lambda: stub)
    res = await run_memory_hop(
        query="q0", fetch_packet=_packets({"q0": "p [memory://chunk/aaa]"}), max_hops=3
    )
    assert res.trace == ["q0"]  # the repeat was refused; loop stalled -> compose
    assert stub.json_calls == 1 and stub.text_calls == 1


def test_enforce_citations_strips_inventions():
    answer = "Port is 8011 [memory://chunk/real]. Cost is $10 [memory://chunk/fake]."
    cleaned, n = enforce_citations(answer, {"memory://chunk/real"})
    assert n == 1
    assert "memory://chunk/fake" not in cleaned
    assert "memory://chunk/real" in cleaned


@pytest.mark.asyncio
async def test_hop_early_stops_when_no_new_citations(monkeypatch):
    """P0.3: reformulations that surface nothing new end the loop immediately —
    unanswerable questions must not burn the whole hop budget."""
    stub = StubClient([
        {"action": "search", "queries": ["variant one"]},
        {"action": "search", "queries": ["variant two"]},  # must never be reached
    ])
    monkeypatch.setattr(memory_hop, "get_llm_client", lambda: stub)
    res = await run_memory_hop(
        query="unanswerable q",
        fetch_packet=_packets({"unanswerable q": "empty [memory://chunk/aaa]"}),
        max_hops=5,
    )
    # 1 decision + forced compose; the second decision never happens
    assert stub.json_calls == 1 and stub.text_calls == 1
    assert res.hops == 2
