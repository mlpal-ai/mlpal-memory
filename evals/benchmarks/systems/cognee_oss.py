"""Cognee as a System (memory v8). Runs inside `systems/venvs/cognee` (cognee 1.5.x; `cbor2` pinned
to a wheel-shipping version because 6.x needs a Rust toolchain on macOS).

Configuration (DESIGN.md §2): Claude Haiku 4.5 through litellm (`anthropic/…`) as cognee's LLM for
`cognify` (entity/relationship extraction, chunk summaries), `text-embedding-3-small` through the
OpenAI key as its embedder, cognee's embedded defaults for storage (sqlite, LanceDB, the embedded
graph store) under the run directory. One dataset per haystack. Each session is one `add` (text
prefixed with a session-date line, as for mem0), then one `cognify` per haystack. Search: `CHUNKS`
(top_k=k, the retrieval tier; one result whose payload is the chunk list) plus the `GRAPH_COMPLETION`
context with `only_context=True` (no completion call), split into one item per node/edge. A chunk's session is found by
locating the chunk text in the haystack's documents.

Model usage: cognee calls providers through litellm, so a litellm callback reports every call
(with prompts) into the shared meters.
"""

from __future__ import annotations

import asyncio
import os
import threading
import time
from pathlib import Path

from . import meters
from .base import IngestResult, Item, SearchResult, System, load_keys, register

LLM_MODEL = os.environ.get("H2H_LLM_MODEL", "claude-haiku-4-5-20251001")
EMBED_MODEL = os.environ.get("H2H_EMBED_MODEL", "text-embedding-3-small")
# cognee's default chunk cap is min(embedder max tokens, half the LLM context) ≈ 8k tokens, so a whole
# session is one chunk (pass 1: median chunk 14.6k chars, reader budget blown, LEARNINGS L10/L23).
# 0 = cognee default; 256 ≈ the 1,000-char passages the other systems retrieve over.
CHUNK_TOKENS = int(os.environ.get("COGNEE_CHUNK_TOKENS", "0"))


class _Loop:
    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()

    def run(self, coro, timeout: float = 3600):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(timeout=timeout)


def _install_litellm_meter() -> None:
    import litellm
    from litellm.integrations.custom_logger import CustomLogger

    class Meter(CustomLogger):
        def _rec(self, kwargs, response_obj):
            u = getattr(response_obj, "usage", None) or {}
            get = (lambda k: getattr(u, k, None) if not isinstance(u, dict) else u.get(k))
            call_type = str(kwargs.get("call_type") or "")
            if "embedding" in call_type:
                meters.record_embed({"model": kwargs.get("model"), "input": kwargs.get("input")}, response_obj, int(get("total_tokens") or 0))
            else:
                # litellm hands the request as `messages`; shape it like an OpenAI chat call for the log
                meters.record_llm("openai.chat", {"model": kwargs.get("model"), "messages": kwargs.get("messages") or []}, response_obj,
                                  int(get("prompt_tokens") or 0), int(get("completion_tokens") or 0))

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._rec(kwargs, response_obj)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            self._rec(kwargs, response_obj)

    if not any(isinstance(c, Meter) for c in litellm.callbacks):
        litellm.callbacks.append(Meter())


@register
class CogneeOSS(System):
    name = "cognee"
    conditions = {
        "write-time LLM": f"anthropic/{LLM_MODEL} via litellm (cognify: extraction, summaries); several calls per chunk",
        "embedder": f"{EMBED_MODEL} via the OpenAI key",
        "store": "cognee defaults under the run dir (sqlite, LanceDB, embedded graph store)",
        "item": "chunk (kind=chunk, session found by text match) and the GRAPH_COMPLETION context text (kind=graph_context, only_context=True)",
        "ingest unit": f"one add per session (session-date line prefixed), one cognify per haystack (dataset); chunk_size={CHUNK_TOKENS or 'cognee default (~8k tokens, session-sized)'}",
        "search": "CHUNKS top_k=k (chunk items first), then the GRAPH_COMPLETION only_context text split into node/edge items; session memory (CACHING) off",
    }

    def setup(self, run_dir: Path) -> None:
        super().setup(run_dir)
        load_keys()
        os.environ["SYSTEM_ROOT_DIRECTORY"] = str(run_dir / "system")
        os.environ["DATA_ROOT_DIRECTORY"] = str(run_dir / "data")
        os.environ["LLM_PROVIDER"] = "anthropic"
        os.environ["LLM_MODEL"] = f"anthropic/{LLM_MODEL}"
        os.environ["LLM_API_KEY"] = os.environ["ANTHROPIC_API_KEY"]
        os.environ["EMBEDDING_PROVIDER"] = "openai"
        os.environ["EMBEDDING_MODEL"] = f"openai/{EMBED_MODEL}"
        os.environ["EMBEDDING_DIMENSIONS"] = "1536"
        os.environ["EMBEDDING_API_KEY"] = os.environ["OPENAI_API_KEY"]
        os.environ.setdefault("TELEMETRY_DISABLED", "1")
        os.environ["CACHING"] = "false"  # cognee 1.x "session memory": an LLM call per search (SessionTurnAnalysis); off, like every other read path here
        meters.install()
        _install_litellm_meter()
        import cognee  # noqa: F401  (reads the environment above at import)

        self._loop = _Loop()
        self._docs: dict[str, list[dict]] = {}

    @staticmethod
    def _dataset(haystack_id: str) -> str:
        return haystack_id.replace("-", "_")

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        import cognee

        before = meters.snapshot()
        t0 = time.perf_counter()
        errors = 0
        notes: list[str] = []
        self._docs[haystack_id] = docs
        ds = self._dataset(haystack_id)

        async def _run():
            for d in docs:
                date = (d.get("valid_at") or "")[:10]
                text = (f"(This conversation took place on {date}.)\n" if date else "") + d["content"]
                await cognee.add(text, dataset_name=ds)
            await cognee.cognify(datasets=[ds], **({"chunk_size": CHUNK_TOKENS} if CHUNK_TOKENS else {}))

        try:
            self._loop.run(_run())
        except Exception as exc:  # noqa: BLE001
            errors += 1
            notes.append(f"cognify: {str(exc)[:200]}")
        stats = self.stats(haystack_id)
        return IngestResult(ms=int((time.perf_counter() - t0) * 1000), items_created=int(stats.get("nodes") or 0), usage=meters.delta(before), errors=errors, notes=notes)

    def _session_of(self, haystack_id: str, chunk: str) -> str | None:
        probe = chunk.strip()[:200]
        for d in self._docs.get(haystack_id, []):
            if probe and probe in d["content"]:
                return d["id"]
        # the first chunk carries the date line we prefixed; match on the remainder
        body = probe.split("\n", 1)[1] if probe.startswith("(This conversation") and "\n" in probe else None
        if body:
            for d in self._docs.get(haystack_id, []):
                if body[:120] in d["content"]:
                    return d["id"]
        return None

    @staticmethod
    def _payload(r):
        return r.get("search_result") if isinstance(r, dict) else getattr(r, "search_result", None)

    @staticmethod
    def _split_graph_context(ctx: str) -> list[tuple[str, str]]:
        """cognee's GRAPH_COMPLETION context is one text: a `Nodes:` section of
        `Node: <name>\n__node_content_start__ … __node_content_end__` blocks, then a `Connections:` /
        edges section. One item per node block and one per edge line keeps the reader budget
        selecting instead of truncating a 20–50k-char blob (LEARNINGS L10)."""
        out: list[tuple[str, str]] = []
        head, _, tail = ctx.partition("__node_content_start__")
        rest = head + "__node_content_start__" + tail if tail else ctx
        pos = 0
        while True:
            a = rest.find("Node: ", pos)
            if a < 0:
                break
            b = rest.find("__node_content_end__", a)
            if b < 0:
                break
            block = rest[a:b]
            name = block[len("Node: "):block.find("\n")].strip() if "\n" in block else ""
            body = block.split("__node_content_start__", 1)[-1].strip()
            out.append(("graph_node", f"{name}: {body}" if name else body))
            pos = b + len("__node_content_end__")
        remainder = rest[pos:]
        for line in remainder.split("\n"):
            line = line.strip()
            if line and not line.endswith(":") and " -- " in line or "->" in line:
                out.append(("graph_edge", line))
        return out or [("graph_context", ctx)]

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        import cognee
        from cognee import SearchType

        before = meters.snapshot()
        t0 = time.perf_counter()
        ds = self._dataset(haystack_id)
        dates = {d["id"]: (d.get("valid_at") or "")[:10] or None for d in self._docs.get(haystack_id, [])}
        items = []
        raw: dict = {}
        try:
            res = self._loop.run(cognee.search(query_text=question, query_type=SearchType.CHUNKS, datasets=[ds], top_k=k))
            chunks = []
            for r in res or []:
                payload = self._payload(r)
                chunks.extend(payload if isinstance(payload, list) else [payload])
            for i, c in enumerate(chunks):
                text = (c.get("text") if isinstance(c, dict) else str(c)) or ""
                sid = self._session_of(haystack_id, text)
                items.append(Item(id=f"chunk:{i}", text=text, when=dates.get(sid) if sid else None, kind="chunk", source_id=sid))
            raw["chunks"] = len(chunks)
        except Exception as exc:  # noqa: BLE001
            raw["chunks_error"] = str(exc)[:200]
        try:
            ctx = self._loop.run(cognee.search(query_text=question, query_type=SearchType.GRAPH_COMPLETION, datasets=[ds], top_k=k, only_context=True))
            texts = [self._payload(r) for r in (ctx or [])]
            raw["graph_context_chars"] = sum(len(t or "") for t in texts if isinstance(t, str))
            n = 0
            for t in texts:
                if isinstance(t, str) and t:
                    for kind, text in self._split_graph_context(t):
                        items.append(Item(id=f"graph:{n}", text=text, kind=kind))
                        n += 1
            raw["graph_items"] = n
        except Exception as exc:  # noqa: BLE001
            raw["graph_error"] = str(exc)[:200]
        return SearchResult(items=items, ms=int((time.perf_counter() - t0) * 1000), usage=meters.delta(before), raw=raw)

    def stats(self, haystack_id: str) -> dict:
        try:
            from cognee.infrastructure.databases.graph import get_graph_engine

            async def _count():
                g = await get_graph_engine()
                nodes, edges = await g.get_graph_data()
                return len(nodes), len(edges)

            n, e = self._loop.run(_count())
            return {"nodes": n, "edges": e, "note": "whole store (all datasets so far)"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:120]}

    def trace(self, haystack_id: str) -> dict:
        try:
            from cognee.infrastructure.databases.graph import get_graph_engine

            async def _dump():
                g = await get_graph_engine()
                nodes, edges = await g.get_graph_data()
                return nodes, edges

            nodes, edges = self._loop.run(_dump())
            return {"nodes": [{"id": str(n[0]), "type": (n[1] or {}).get("type"), "name": (n[1] or {}).get("name"), "text": str((n[1] or {}).get("text") or (n[1] or {}).get("description") or "")[:300]} for n in nodes[:2000]],
                    "edges": [{"from": str(e[0]), "to": str(e[1]), "rel": e[2]} for e in edges[:4000]],
                    "note": "whole store (all datasets so far)"}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
