"""Memobase (memodb-io) as a System (memory v8). Runs inside `systems/venvs/memobase` against the
compose stack from `scripts/h2h/memobase_server.sh`.

Memobase is profile-centric: it buffers chat blobs per user, and on flush an LLM extracts profile
slots (topic/sub-topic → value) and "events" (dated gists with tags), which are embedded for
search. Configuration (DESIGN.md §2): the server's OpenAI-compatible LLM and embedding endpoints
point at the counting proxy (`scripts/h2h/llm_proxy.py`), which forwards chat to Claude Haiku 4.5
through the MLPal gateway and embeddings to `text-embedding-3-small`, so usage is metered by
reading the proxy's counters. One Memobase user per haystack. Each session is one `ChatBlob`
(`created_at` = session date, per-message `created_at` too) inserted then flushed synchronously,
so ingest wall includes extraction. Search returns the user's profile (kind=profile, one item per
slot) and `search_event` hits (kind=event, dated by the event's time), `time_range_in_days`
widened so 2023 sessions are not filtered out by Memobase's 180-day default.
"""

from __future__ import annotations

import os
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .base import IngestResult, Item, SearchResult, System, Usage, register, split_messages

BASE = os.environ.get("MEMOBASE_URL", "http://localhost:8019")
TOKEN = os.environ.get("MEMOBASE_TOKEN", "secret")
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


def _dt(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=UTC)
    except ValueError:
        return None


@register
class MemobaseOSS(System):
    name = "memobase"
    conditions = {
        "write-time LLM": "claude-haiku-4-5-20251001 via the counting proxy → MLPal gateway (profile + event extraction on flush)",
        "embedder": "text-embedding-3-small via the counting proxy (event embeddings)",
        "store": "Memobase server (prebuilt image) + pgvector + redis under docker compose",
        "item": "profile slot (topic/sub-topic: value) and event gist with tags; dates as Memobase writes them into the text ([mention …, event …]); no item date field (its created_at is the ingest day)",
        "ingest unit": "one ChatBlob per session (created_at = session date), insert + flush(sync)",
        "search": "profile() plus search_event(query, topk=k, similarity_threshold=0, time_range_in_days=36500)",
    }

    def setup(self, run_dir: Path) -> None:
        super().setup(run_dir)
        from memobase import MemoBaseClient

        self.client = MemoBaseClient(project_url=BASE, api_key=TOKEN)
        assert self.client.ping(), "memobase server not reachable"
        self._users: dict[str, object] = {}

    def _user(self, haystack_id: str):
        if haystack_id not in self._users:
            uid = self.client.add_user({"haystack": haystack_id})
            self._users[haystack_id] = self.client.get_user(uid)
        return self._users[haystack_id]

    @staticmethod
    def _messages(doc: dict) -> list[dict]:
        when = _dt(doc.get("valid_at"))
        msgs = []
        for m in split_messages(doc["content"]):
            out = {"role": m["role"], "content": m["content"]}
            if m.get("speaker"):
                out["alias"] = m["speaker"]
            if when:
                out["created_at"] = when.isoformat()  # message timestamps are strings in the client model; the blob's is a datetime
            msgs.append(out)
        return msgs

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        from memobase import ChatBlob

        before = _proxy_usage()
        t0 = time.perf_counter()
        errors = 0
        notes: list[str] = []
        user = self._user(haystack_id)
        for d in docs:
            try:
                blob = ChatBlob(messages=self._messages(d), fields={"session_id": d["id"]}, **({"created_at": _dt(d.get("valid_at"))} if _dt(d.get("valid_at")) else {}))
                user.insert(blob, sync=True)
            except Exception as exc:  # noqa: BLE001
                errors += 1
                if len(notes) < 5:
                    notes.append(f"{d['id']}: {str(exc)[:160]}")
        try:
            user.flush(sync=True)
        except Exception as exc:  # noqa: BLE001
            errors += 1
            notes.append(f"flush: {str(exc)[:160]}")
        st = self.stats(haystack_id)
        return IngestResult(ms=int((time.perf_counter() - t0) * 1000), items_created=int(st.get("profile_slots") or 0) + int(st.get("events") or 0),
                            usage=_delta(before, _proxy_usage()), errors=errors, notes=notes)

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        before = _proxy_usage()
        t0 = time.perf_counter()
        user = self._user(haystack_id)
        items = []
        raw: dict = {}
        try:
            prof = user.profile(max_token_size=4000)
            for p in prof:
                # no `when`: a slot's updated_at is the ingest day; Memobase writes the mention/event dates into the text itself
                items.append(Item(id=str(getattr(p, "id", "")), text=f"{p.topic} / {p.sub_topic}: {p.content}", kind="profile"))
            raw["profile_slots"] = len(prof)
        except Exception as exc:  # noqa: BLE001
            raw["profile_error"] = str(exc)[:200]
        try:
            evs = user.search_event(question, topk=k, similarity_threshold=0.0, time_range_in_days=36500)
            for e in evs:
                data = getattr(e, "event_data", None)
                gist = getattr(data, "event_tip", None) or ""
                deltas = getattr(data, "profile_delta", None) or []
                text = gist or "; ".join(f"{getattr(x.attributes, 'topic', '')}/{getattr(x.attributes, 'sub_topic', '')}: {x.content}" for x in deltas if hasattr(x, "content"))
                items.append(Item(id=str(getattr(e, "id", "")), text=text, kind="event", score=getattr(e, "similarity", None)))  # dates are in the text
            raw["events"] = len(evs)
        except Exception as exc:  # noqa: BLE001
            raw["events_error"] = str(exc)[:200]
        return SearchResult(items=items, ms=int((time.perf_counter() - t0) * 1000), usage=_delta(before, _proxy_usage()), raw=raw)

    def stats(self, haystack_id: str) -> dict:
        try:
            user = self._user(haystack_id)
            return {"profile_slots": len(user.profile(max_token_size=100000)), "events": len(user.event(topk=1000))}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:120]}

    def trace(self, haystack_id: str) -> dict:
        try:
            user = self._user(haystack_id)
            prof = [{"topic": p.topic, "sub_topic": p.sub_topic, "content": p.content, "updated_at": str(getattr(p, "updated_at", ""))} for p in user.profile(max_token_size=100000)]
            evs = []
            for e in user.event(topk=1000):
                data = getattr(e, "event_data", None)
                evs.append({"id": str(getattr(e, "id", "")), "created_at": str(getattr(e, "created_at", "")), "gist": getattr(data, "event_tip", None),
                            "tags": [getattr(t, "tag", None) for t in (getattr(data, "event_tags", None) or [])],
                            "profile_delta": [f"{getattr(x.attributes, 'topic', '')}/{getattr(x.attributes, 'sub_topic', '')}: {x.content}" for x in (getattr(data, "profile_delta", None) or []) if hasattr(x, "content")]})
            return {"profile": prof, "events": evs}
        except Exception as exc:  # noqa: BLE001
            return {"error": str(exc)[:200]}
