"""mem0 OSS as a System (memory v8). Runs inside the `systems/venvs/mem0` interpreter.

Configuration (DESIGN.md §2): Claude Haiku 4.5 through the Anthropic key as mem0's write-time LLM
(fact extraction and the add/update/delete decisions), `text-embedding-3-small` through the OpenAI
key as its embedder, an embedded Qdrant under the run directory as its vector store, one `user_id`
per haystack. Each session is one `Memory.add(messages, user_id, metadata={session_id, date})`
call, so mem0 sees the same unit of ingest as everyone else. Search is `Memory.search(query,
filters={user_id}, limit=k)`; items are mem0's rewritten memory sentences, dated by the session metadata
mem0 kept on them. Model usage is counted by wrapping the Anthropic and OpenAI clients mem0 uses.

What mem0 cannot be configured to: it always calls the LLM on write (there is an `infer=False`
mode that stores raw text, which is a different system); its memories carry mem0's own
`created_at` (ingest time), so the session date must ride in metadata.
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
    """The session as chat messages (shared splitter), prefixed by a one-line session date: mem0 OSS
    rejects add(timestamp=...) ("platform-only"), and without the date every relative date in a 2023
    session is resolved against the ingest day (LEARNINGS L1). Named speakers keep their name in the text."""
    date = (doc.get("valid_at") or "")[:10]
    msgs = [{"role": "user", "content": f"(This conversation took place on {date}.)"}] if date else []
    for m in split_messages(doc["content"]):
        text = f"{m['speaker']}: {m['content']}" if m.get("speaker") else m["content"]
        msgs.append({"role": m["role"], "content": text})
    return msgs


@register
class Mem0OSS(System):
    name = "mem0"
    conditions = {
        "write-time LLM": f"{LLM_MODEL} via the Anthropic key (mem0's extraction and add/update/delete decisions); unavoidable",
        "embedder": f"{EMBED_MODEL} via the OpenAI key",
        "vector store": "embedded Qdrant under the run directory",
        "item": "mem0 memory sentence; session date and id from the metadata we attach on add",
        "ingest unit": "one Memory.add per session with the session's chat messages, prefixed by a one-line session date",
        "search": "top_k=k, threshold=0 (mem0 default 0.1 lifted)",
    }

    def setup(self, run_dir: Path) -> None:
        super().setup(run_dir)
        load_keys()
        os.environ.setdefault("MEM0_TELEMETRY", "False")
        home = run_dir / "mem0_home"
        home.mkdir(parents=True, exist_ok=True)
        os.environ["MEM0_DIR"] = str(home)
        meters.install()
        from mem0 import Memory

        config = {
            # temperature=None: anthropic SDK >= 1.7 dropped the sampling kwargs from Messages.create; mem0 would otherwise send its 0.1 default
            "llm": {"provider": "anthropic", "config": {"model": LLM_MODEL, "api_key": os.environ["ANTHROPIC_API_KEY"], "temperature": None, "top_p": None}},
            "embedder": {"provider": "openai", "config": {"model": EMBED_MODEL, "api_key": os.environ["OPENAI_API_KEY"]}},
            "vector_store": {"provider": "qdrant", "config": {"collection_name": "h2h", "path": str(run_dir / "qdrant"), "on_disk": True}},
            "history_db_path": str(run_dir / "mem0_history.db"),
        }
        self.memory = Memory.from_config(config)

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        before = meters.snapshot()
        t0 = time.perf_counter()
        created = 0
        errors = 0
        notes: list[str] = []
        for d in docs:
            try:
                out = self.memory.add(_messages(d), user_id=haystack_id, metadata={"session_id": d["id"], "date": d.get("valid_at") or "", "title": d.get("title") or ""})
                res = out.get("results") if isinstance(out, dict) else out
                created += len(res or [])
            except Exception as exc:  # noqa: BLE001 — a failed session is recorded, not fatal
                errors += 1
                if len(notes) < 5:
                    notes.append(f"{d['id']}: {str(exc)[:160]}")
        return IngestResult(ms=int((time.perf_counter() - t0) * 1000), items_created=created, usage=meters.delta(before), errors=errors, notes=notes)

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        before = meters.snapshot()
        t0 = time.perf_counter()
        # threshold=0: mem0's default similarity floor (0.1) returned 1 of 12 stored memories for one
        # question (LEARNINGS L4); the retrieval budget is k items, so the floor is lifted
        out = self.memory.search(question, filters={"user_id": haystack_id}, top_k=k, threshold=0.0)
        res = out.get("results") if isinstance(out, dict) else out
        items = []
        for m in res or []:
            md = m.get("metadata") or {}
            items.append(Item(id=str(m.get("id")), text=m.get("memory") or "", when=(md.get("date") or "")[:10] or None, kind="memory",
                              score=m.get("score"), source_id=md.get("session_id")))
        return SearchResult(items=items, ms=int((time.perf_counter() - t0) * 1000), usage=meters.delta(before), raw=res)

    def stats(self, haystack_id: str) -> dict:
        try:
            out = self.memory.get_all(filters={"user_id": haystack_id})
            res = out.get("results") if isinstance(out, dict) else out
            return {"memories": len(res or [])}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:120]}

    def trace(self, haystack_id: str) -> dict:
        try:
            out = self.memory.get_all(filters={"user_id": haystack_id})
            res = out.get("results") if isinstance(out, dict) else out
            return {"memories": [{k: v for k, v in m.items() if k in ("id", "memory", "created_at", "updated_at", "metadata", "hash")} for m in (res or [])]}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
