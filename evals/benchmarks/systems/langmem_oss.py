"""LangMem (LangChain) as a System (memory v8). Runs inside `systems/venvs/langmem`.

LangMem is a library, not a server: `create_memory_store_manager` runs an LLM "memory manager"
over a conversation and writes/updates memory items in a LangGraph store; the store's semantic index
answers searches. Configuration (DESIGN.md §2): Claude Haiku 4.5 through `langchain-anthropic` as
the manager model, `text-embedding-3-small` through `langchain-openai` as the store index, an
`InMemoryStore` per run (LangMem's reference setup; nothing persists beyond the process, so the
harness's ingest cache `.ingested-longmemeval-langmem.json` must be deleted before each run), one
namespace per haystack. Each session is one manager invocation with
the session's messages, prefixed by a one-line session date (LangMem has no timestamp input).
Search is `store.search(namespace, query, limit=k)`; items are the manager's memory contents, dated
by the session that wrote them (recorded by the adapter from the store's item creation order).

What LangMem cannot be configured to: `enable_deletes` is off by default and stays off (its
default); the manager also runs a query step (`query_limit=5`) to fetch existing memories before
each write — a read-time embedding call at ingest counted as ingest usage.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from . import meters
from .base import IngestResult, Item, SearchResult, System, load_keys, register, split_messages

LLM_MODEL = os.environ.get("H2H_LLM_MODEL", "claude-haiku-4-5-20251001")
EMBED_MODEL = os.environ.get("H2H_EMBED_MODEL", "text-embedding-3-small")


def _messages(doc: dict) -> list[dict]:
    date = (doc.get("valid_at") or "")[:10]
    msgs = [{"role": "user", "content": f"(This conversation took place on {date}.)"}] if date else []
    for m in split_messages(doc["content"]):
        msgs.append({"role": m["role"], "content": f"{m['speaker']}: {m['content']}" if m.get("speaker") else m["content"]})
    return msgs


@register
class LangMemOSS(System):
    name = "langmem"
    conditions = {
        "write-time LLM": f"{LLM_MODEL} via langchain-anthropic (LangMem memory manager: extract, update; deletes off = default)",
        "embedder": f"{EMBED_MODEL} via langchain-openai as the store index",
        "store": "LangGraph InMemoryStore per run, one namespace per haystack (LangMem's reference setup)",
        "item": "memory item content written by the manager; session = the session whose invocation created it",
        "ingest unit": "one manager invocation per session with the session's chat messages, session-date line prefixed",
        "search": "store.search(namespace, query, limit=k) — semantic index only",
    }

    def setup(self, run_dir: Path) -> None:
        super().setup(run_dir)
        load_keys()
        meters.install()
        from langchain_anthropic import ChatAnthropic
        from langchain_openai import OpenAIEmbeddings
        from langgraph.store.memory import InMemoryStore
        from langmem import create_memory_store_manager

        self.store = InMemoryStore(index={"embed": OpenAIEmbeddings(model=EMBED_MODEL), "dims": 1536})
        self.manager = create_memory_store_manager(ChatAnthropic(model=LLM_MODEL, max_tokens=4096), namespace=("memories", "{langgraph_user_id}"),
                                                   store=self.store)
        self._owner: dict[tuple[str, str], tuple[str, str | None]] = {}  # (haystack, item key) -> (session id, date)

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        before = meters.snapshot()
        t0 = time.perf_counter()
        errors = 0
        notes: list[str] = []
        ns = ("memories", haystack_id)
        for d in docs:
            seen = {it.key for it in self.store.search(ns, limit=10_000)}
            try:
                self.manager.invoke({"messages": _messages(d)}, config={"configurable": {"langgraph_user_id": haystack_id}})
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if len(notes) < 5:
                    notes.append(f"{d['id']}: {str(exc)[:160]}")
                continue
            for it in self.store.search(ns, limit=10_000):
                if it.key not in seen:
                    self._owner[(haystack_id, it.key)] = (d["id"], (d.get("valid_at") or "")[:10] or None)
        created = len(self.store.search(ns, limit=10_000))
        return IngestResult(ms=int((time.perf_counter() - t0) * 1000), items_created=created, usage=meters.delta(before), errors=errors, notes=notes)

    @staticmethod
    def _content(value) -> str:
        if isinstance(value, dict):
            return str(value.get("content") or value.get("memory") or value)
        return str(value)

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        before = meters.snapshot()
        t0 = time.perf_counter()
        hits = self.store.search(("memories", haystack_id), query=question, limit=k)
        items = []
        for h in hits:
            sid, when = self._owner.get((haystack_id, h.key), (None, None))
            items.append(Item(id=h.key, text=self._content(h.value), when=when, kind="memory", score=getattr(h, "score", None), source_id=sid))
        return SearchResult(items=items, ms=int((time.perf_counter() - t0) * 1000), usage=meters.delta(before),
                            raw=[{"key": h.key, "score": getattr(h, "score", None)} for h in hits])

    def stats(self, haystack_id: str) -> dict:
        return {"memories": len(self.store.search(("memories", haystack_id), limit=10_000))}

    def trace(self, haystack_id: str) -> dict:
        out = []
        for it in self.store.search(("memories", haystack_id), limit=10_000):
            sid, when = self._owner.get((haystack_id, it.key), (None, None))
            out.append({"key": it.key, "content": self._content(it.value), "session": sid, "date": when, "created_at": str(getattr(it, "created_at", ""))})
        return {"memories": out}
