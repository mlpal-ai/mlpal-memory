"""supermemory self-hosted ("supermemory lite", one-binary server) as a System (memory v8). Runs
inside `systems/venvs/supermemory` against a server started by `scripts/h2h/supermemory_server.sh`.

Configuration (DESIGN.md §2): the server gets the Anthropic key as its only LLM key (its memory
agent then writes with Claude; the binary names claude-haiku-4-5) and the OpenAI key only through
`SUPERMEMORY_EMBEDDING_API_KEY` for `text-embedding-3-small`. One `container_tag` per haystack.
Each session is one `add(content, container_tag, document_date=<session date>, custom_id,
metadata={session_id})`; the server chunks, embeds, and runs its "memory agent" asynchronously, so
ingest waits until every document of the haystack reports `done` and the wall time includes that.
Search is `search.memories(q, container_tags, search_mode="hybrid", limit=k, threshold=0)`, which
returns memory sentences and matching chunks; items carry the session id supermemory keeps in the
memory's metadata and the date we recorded for that session.

Metering: the server calls the model itself, so its LLM provider is "OpenAI" pointed at the counting
proxy (`scripts/h2h/llm_proxy.py`), which forwards to Claude Haiku 4.5 through the MLPal gateway;
the adapter reads the proxy's counters before and after each ingest/search.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from .base import IngestResult, Item, SearchResult, System, Usage, register

BASE = os.environ.get("SUPERMEMORY_URL", "http://localhost:6767")
PROXY = os.environ.get("H2H_PROXY_URL", "http://127.0.0.1:8787")


def _proxy_usage() -> Usage:
    try:
        u = httpx.get(f"{PROXY}/usage", timeout=10).json()
        return Usage(llm_calls=u["llm_calls"], llm_input_tokens=u["llm_input_tokens"], llm_output_tokens=u["llm_output_tokens"],
                     embed_calls=u["embed_calls"], embed_tokens=u["embed_tokens"])
    except Exception:  # noqa: BLE001 — proxy down: usage unmetered for this call, recorded as zeros
        return Usage()


def _delta(a: Usage, b: Usage) -> Usage:
    return Usage(**{k: getattr(b, k) - getattr(a, k) for k in a.to_dict()})
POLL_S = 2.0
PROCESS_TIMEOUT_S = float(os.environ.get("SUPERMEMORY_PROCESS_TIMEOUT_S", "1800"))


@register
class SupermemoryOSS(System):
    name = "supermemory"
    conditions = {
        "write-time LLM": "the server's memory agent via its OpenAI provider → counting proxy → claude-haiku-4-5-20251001 through the MLPal gateway (metered)",
        "embedder": "text-embedding-3-small via SUPERMEMORY_EMBEDDING_API_KEY (OpenAI key, direct; not metered)",
        "store": "supermemory lite encrypted local storage (pglite) under results/.system-supermemory-data",
        "item": "memory sentence (kind=memory) or matching chunk (kind=chunk) from hybrid search; session id from the metadata we attach",
        "ingest unit": "one add per session with document_date = session date; ingest waits for the server's async processing to finish",
        "search": "search_mode=hybrid, limit=k, threshold=0, rerank off (default)",
    }

    def setup(self, run_dir: Path) -> None:
        super().setup(run_dir)
        import supermemory

        self.client = supermemory.Supermemory(api_key=os.environ.get("SUPERMEMORY_API_KEY", "local"), base_url=BASE, timeout=120)
        self._dates: dict[tuple[str, str], str] = {}  # (haystack, session id) -> date

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        before = _proxy_usage()
        t0 = time.perf_counter()
        errors = 0
        notes: list[str] = []
        ids: list[str] = []
        for d in docs:
            date = (d.get("valid_at") or "")[:10]
            self._dates[(haystack_id, d["id"])] = date
            try:
                r = self.client.add(content=d["content"], container_tag=haystack_id, custom_id=f"{haystack_id}:{d['id']}",
                                    metadata={"session_id": d["id"], "title": d.get("title") or ""}, **({"document_date": date} if date else {}))
                ids.append(r.id)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if len(notes) < 5:
                    notes.append(f"{d['id']}: {str(exc)[:160]}")
        # the server processes asynchronously; wait for every document, counting its memories
        created = 0
        deadline = time.monotonic() + PROCESS_TIMEOUT_S
        pending = set(ids)
        while pending and time.monotonic() < deadline:
            for doc_id in list(pending):
                try:
                    doc = self.client.documents.get(doc_id)
                except Exception:  # noqa: BLE001 — transient; retried on the next poll
                    continue
                if doc.status == "done":
                    pending.discard(doc_id)
                    created += len(getattr(doc, "memories", None) or [])
                elif doc.status == "failed":
                    pending.discard(doc_id)
                    errors += 1
                    if len(notes) < 5:
                        notes.append(f"{doc_id}: processing failed")
            if pending:
                time.sleep(POLL_S)
        if pending:
            errors += len(pending)
            notes.append(f"{len(pending)} documents still processing after {PROCESS_TIMEOUT_S:.0f}s")
        return IngestResult(ms=int((time.perf_counter() - t0) * 1000), items_created=created, usage=_delta(before, _proxy_usage()), errors=errors, notes=notes)

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        before = _proxy_usage()
        t0 = time.perf_counter()
        res = self.client.search.memories(q=question, container_tags=[haystack_id], limit=k, search_mode="hybrid", threshold=0.0)
        items = []
        raw = []
        for r in res.results or []:
            rd = r.model_dump()
            raw.append({kk: rd.get(kk) for kk in ("id", "memory", "chunk", "similarity", "metadata", "version", "is_aggregated")})
            md = rd.get("metadata") or {}
            sid = md.get("session_id")
            when = self._dates.get((haystack_id, sid)) if sid else None
            if rd.get("memory"):
                items.append(Item(id=str(rd.get("id")), text=rd["memory"], when=when, kind="memory", score=rd.get("similarity"), source_id=sid))
            elif rd.get("chunk"):
                items.append(Item(id=str(rd.get("id")), text=rd["chunk"], when=when, kind="chunk", score=rd.get("similarity"), source_id=sid))
        return SearchResult(items=items, ms=int((time.perf_counter() - t0) * 1000), usage=_delta(before, _proxy_usage()), raw={"total": res.total, "timing_ms": res.timing, "results": raw})

    def _documents(self, haystack_id: str) -> list:
        out = self.client.documents.list(container_tags=[haystack_id], limit=500)
        return list(getattr(out, "memories", None) or [])

    def stats(self, haystack_id: str) -> dict:
        try:
            docs = self._documents(haystack_id)
            return {"documents": len(docs), "status": {s: sum(1 for d in docs if d.status == s) for s in {d.status for d in docs}}}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:120]}

    def trace(self, haystack_id: str) -> dict:
        try:
            out = []
            for d in self._documents(haystack_id):
                full = self.client.documents.get(d.id).model_dump()
                out.append({"id": d.id, "custom_id": full.get("custom_id"), "status": full.get("status"), "title": full.get("title"),
                            "metadata": full.get("metadata"),
                            "memories": [{kk: m.get(kk) for kk in ("id", "memory", "is_static", "is_inference", "is_latest", "version", "parent_memory_id", "source_count")}
                                         for m in (full.get("memories") or [])]})
            return {"documents": out}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
