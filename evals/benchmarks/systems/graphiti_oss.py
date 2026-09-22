"""Graphiti (Zep OSS) as a System (memory v8). Runs inside `systems/venvs/graphiti`.

Configuration (DESIGN.md §2): FalkorDB in a container as the graph store (one `group_id` per
haystack; graphiti's FalkorDriver keeps one FalkorDB graph per group_id), Claude Haiku 4.5 through
the Anthropic key as Graphiti's extraction and edge-resolution model, `text-embedding-3-small`
through the OpenAI key as its embedder.

Ingest follows Zep's own LongMemEval evaluator: **one episode per message** (`EpisodeType.message`,
`"role: text"`), `reference_time` = the session date plus the message index in seconds so order is
kept, submitted per session through `add_episode_bulk` (Graphiti's bulk path: extraction per
episode, deduplication across the batch). The first shake-out ingested whole sessions as single
episodes and extraction dropped most of a 20–40k-char session (LEARNINGS L5, L9).

Search: `search_` with `COMBINED_HYBRID_SEARCH_RRF` (bm25 + cosine + graph traversal, reciprocal
rank fusion, no reranker model) and `limit=k`; items are the returned episodes (the verbatim
messages, kind=episode), entity edges' `fact` strings (kind=edge, dated by `valid_at`) and entity
node summaries (kind=node). Zep's evaluator used the cross-encoder variant (an OpenAI reranker
model at read time); RRF keeps the read path model-free like every other system here and is
recorded as a deviation. An episode's session is recovered from the episode name.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from . import meters
from .base import IngestResult, Item, SearchResult, System, load_keys, register, split_messages

LLM_MODEL = os.environ.get("H2H_LLM_MODEL", "claude-haiku-4-5-20251001")
EMBED_MODEL = os.environ.get("H2H_EMBED_MODEL", "text-embedding-3-small")
FALKOR_HOST = os.environ.get("FALKORDB_HOST", "localhost")
FALKOR_PORT = int(os.environ.get("FALKORDB_PORT", "6379"))


def _iso(s: str | None) -> datetime:
    if not s:
        return datetime(2000, 1, 1, tzinfo=UTC)
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return datetime(2000, 1, 1, tzinfo=UTC)


def _messages(content: str) -> list[str]:
    """One episode per message, `role: text` (Zep's evaluator feeds `speaker: text` message episodes)."""
    return [f"{m['speaker'] or m['role']}: {m['content']}" for m in split_messages(content)]


class _Loop:
    """One event loop on a background thread; the adapter's sync methods submit coroutines to it."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout: float = 3600):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)


@register
class GraphitiOSS(System):
    name = "graphiti"
    conditions = {
        "write-time LLM": f"{LLM_MODEL} via the Anthropic key (entity/edge extraction, dedup, temporal resolution); several calls per episode",
        "embedder": f"{EMBED_MODEL} via the OpenAI key",
        "graph store": "FalkorDB container, one graph per haystack (graphiti maps group_id to a FalkorDB graph)",
        "item": "episode (verbatim message), entity-edge fact with valid_at, entity node summary; session from the episode name",
        "ingest unit": "one episode per message as in Zep's LongMemEval evaluator, add_episode_bulk per session, reference_time = session date + message index, communities off",
        "search": "search_ COMBINED_HYBRID_SEARCH_RRF, limit=k; no reranker model (Zep's evaluator used the cross-encoder variant)",
    }

    def setup(self, run_dir: Path) -> None:
        super().setup(run_dir)
        load_keys()
        meters.install()
        self._loop = _Loop()
        self._loop.run(self._init())

    async def _init(self) -> None:
        from graphiti_core import Graphiti
        from graphiti_core.driver.falkordb_driver import FalkorDriver
        from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
        from graphiti_core.llm_client.anthropic_client import AnthropicClient
        from graphiti_core.llm_client.config import LLMConfig

        driver = FalkorDriver(host=FALKOR_HOST, port=FALKOR_PORT, database=os.environ.get("FALKORDB_DATABASE", "h2h"))
        llm = AnthropicClient(config=LLMConfig(api_key=os.environ["ANTHROPIC_API_KEY"], model=LLM_MODEL, small_model=LLM_MODEL))
        embedder = OpenAIEmbedder(config=OpenAIEmbedderConfig(api_key=os.environ["OPENAI_API_KEY"], embedding_model=EMBED_MODEL))
        self.g = Graphiti(graph_driver=driver, llm_client=llm, embedder=embedder)
        await self.g.build_indices_and_constraints()

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        from graphiti_core.nodes import EpisodeType
        from graphiti_core.utils.bulk_utils import RawEpisode

        before = meters.snapshot()
        t0 = time.perf_counter()
        created = 0
        errors = 0
        notes: list[str] = []
        for d in docs:
            base = _iso(d.get("valid_at"))
            episodes = [RawEpisode(name=f"{haystack_id}:{d['id']}:{i}", content=m, source_description=d.get("title") or "conversation session",
                                   source=EpisodeType.message, reference_time=base + timedelta(seconds=i))
                        for i, m in enumerate(_messages(d["content"]))]
            try:
                res = self._loop.run(self.g.add_episode_bulk(episodes, group_id=haystack_id))
                created += len(getattr(res, "edges", None) or [])
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if len(notes) < 5:
                    notes.append(f"{d['id']}: {str(exc)[:160]}")
        return IngestResult(ms=int((time.perf_counter() - t0) * 1000), items_created=created, usage=meters.delta(before), errors=errors, notes=notes)

    @staticmethod
    def _session_of(episode_name: str | None) -> str | None:
        # name = "<haystack>:<session id>:<index>"; session ids carry no colon
        parts = (episode_name or "").split(":")
        return parts[1] if len(parts) >= 3 else None

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        from graphiti_core.search.search_config_recipes import COMBINED_HYBRID_SEARCH_RRF

        before = meters.snapshot()
        t0 = time.perf_counter()
        cfg = COMBINED_HYBRID_SEARCH_RRF.model_copy(deep=True)
        cfg.limit = k
        res = self._loop.run(self.g.search_(question, config=cfg, group_ids=[haystack_id]))
        episodes = list(getattr(res, "episodes", None) or [])
        edges = list(getattr(res, "edges", None) or [])
        nodes = list(getattr(res, "nodes", None) or [])
        ep_session = self._episode_sessions(haystack_id)
        items = []
        for e in episodes:
            when = getattr(e, "valid_at", None)
            items.append(Item(id=str(e.uuid), text=getattr(e, "content", "") or "", when=when.date().isoformat() if when else None, kind="episode",
                              source_id=ep_session.get(e.uuid)))
        for e in edges:
            when = getattr(e, "valid_at", None)
            sid = next((ep_session[u] for u in (getattr(e, "episodes", None) or []) if u in ep_session), None)
            items.append(Item(id=str(e.uuid), text=getattr(e, "fact", "") or "", when=when.date().isoformat() if when else None, kind="edge", source_id=sid))
        for n in nodes:
            summary = getattr(n, "summary", "") or ""
            if summary:
                items.append(Item(id=str(n.uuid), text=f"{getattr(n, 'name', '')}: {summary}", kind="node"))
        raw = {"episodes": len(episodes), "edges": len(edges), "nodes": len(nodes), "communities": len(getattr(res, "communities", None) or []),
               "edge_facts": [getattr(e, "fact", None) for e in edges[:20]]}
        return SearchResult(items=items, ms=int((time.perf_counter() - t0) * 1000), usage=meters.delta(before), raw=raw)

    def _episode_sessions(self, haystack_id: str) -> dict[str, str | None]:
        """episode uuid -> our session id, from the haystack's Episodic nodes (cached per haystack;
        the combined search rarely returns the episodes an edge came from, so the map is read once)."""
        cache = self.__dict__.setdefault("_ep_cache", {})
        if haystack_id not in cache:
            try:
                eps, _, _ = self._loop.run(self._graph(haystack_id).execute_query("MATCH (e:Episodic) RETURN e.uuid AS uuid, e.name AS name"))
                cache[haystack_id] = {e["uuid"]: self._session_of(e["name"]) for e in eps or []}
            except Exception:  # noqa: BLE001 — recall is then unmeasured for this haystack, accuracy unaffected
                cache[haystack_id] = {}
        return cache[haystack_id]

    def _graph(self, haystack_id: str):
        # graphiti's FalkorDriver keeps one FalkorDB graph per group_id; queries must target that graph
        return self.g.driver.clone(database=haystack_id)

    def stats(self, haystack_id: str) -> dict:
        try:
            recs, _, _ = self._loop.run(self._graph(haystack_id).execute_query("MATCH (n) RETURN labels(n)[0] AS l, count(n) AS c"))
            erecs, _, _ = self._loop.run(self._graph(haystack_id).execute_query("MATCH ()-[r]->() RETURN type(r) AS t, count(r) AS c"))
            return {"nodes_by_label": {r["l"]: r["c"] for r in recs or []}, "edges_by_type": {r["t"]: r["c"] for r in erecs or []}}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:120]}

    def trace(self, haystack_id: str) -> dict:
        try:
            recs, _, _ = self._loop.run(self._graph(haystack_id).execute_query(
                "MATCH (a)-[r:RELATES_TO]->(b) RETURN a.name AS a, r.name AS rel, r.fact AS fact, r.valid_at AS valid_at, r.invalid_at AS invalid_at, "
                "r.expired_at AS expired_at, r.episodes AS episodes, b.name AS b LIMIT 4000"))
            eps, _, _ = self._loop.run(self._graph(haystack_id).execute_query("MATCH (e:Episodic) RETURN e.uuid AS uuid, e.name AS name"))
            ep_session = {e["uuid"]: self._session_of(e["name"]) for e in eps or []}
            edges = []
            for r in recs or []:
                r["sessions"] = sorted({s for s in (ep_session.get(u) for u in (r.get("episodes") or [])) if s})
                edges.append(r)
            nrecs, _, _ = self._loop.run(self._graph(haystack_id).execute_query("MATCH (n:Entity) RETURN n.name AS name, n.summary AS summary LIMIT 2000"))
            return {"edges": edges, "entities": [dict(n) for n in nrecs or []], "episodes": len(eps or [])}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
