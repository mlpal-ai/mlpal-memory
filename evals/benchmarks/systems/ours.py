"""Our own service as a System (memory v8): the same public API the v7 harness used, expressed
through the adapter contract so it runs in the same loop as every competitor.

Conditions: the service on the box runs with `MLPAL_EMBEDDINGS_PROVIDER=gateway` (text-embedding-
3-small through the MLPal gateway) for the head-to-head, so the embedder matches the others; the
direct arm calls no model on write. Items are passages (chunks) with the session date from the
document title and the session id from the document uri; `per_document=8` as in v7.
"""

from __future__ import annotations

import hashlib
import os
import time

import httpx

from .base import IngestResult, Item, SearchResult, System, Usage, register

BASE = os.environ.get("MEMORY_URL", "http://localhost:8000")
PER_DOCUMENT = int(os.environ.get("BENCH_PER_DOCUMENT", "8"))
# memory v8 C3: "rrf" asks the service for one fused list across facts and passages and returns items
# in that order; "" keeps the v7 shape (passages, then up to 10 facts appended)
FUSION = os.environ.get("OURS_FUSION", "")
SHOW_FACTS = os.environ.get("OURS_FACTS", "") == "1"   # C3's fact list beside passages (measured net zero, v8 L31)
MAX_STATES = int(os.environ.get("OURS_MAX_STATES", "5"))  # C4 topic states served first


def _hdr(org: str) -> dict:
    return {"X-Test-Org-Id": org, "X-Test-User-Id": "bench", "X-Test-Permissions": "memory.read,memory.write", "Content-Type": "application/json"}


@register
class Ours(System):
    name = "ours"
    conditions = {
        "write-time LLM": "none (direct arm) | claude-haiku-4-5 via the gateway, one call per session (MLPAL_EXTRACTOR=facts, C3)",
        "embedder": "text-embedding-3-small via the MLPal gateway (MLPAL_EMBEDDINGS_PROVIDER=gateway)",
        "item": "passage (1,000-char chunk) with session date and session id; up to 8 per session",
    }

    def __init__(self) -> None:
        self.client = httpx.Client(base_url=BASE, timeout=300)

    def _org(self, haystack_id: str) -> str:
        # BENCH_ORG_TAG names an ingest-side experiment (memory v8 C3: the facts extractor); its haystacks
        # live in their own orgs so the service does not dedupe them against the baseline's documents
        tag = os.environ.get("BENCH_ORG_TAG", "")
        return f"h2h-ours-{tag + '-' if tag else ''}{haystack_id}"[:60]

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        org = self._org(haystack_id)
        t0 = time.perf_counter()
        errors = 0
        created = 0
        notes: list[str] = []
        for d in docs:
            eid = f"h2h:{hashlib.sha256(f'{org}:{d['id']}'.encode()).hexdigest()[:48]}"
            body = {"event_id": eid, "title": d["title"], "content": d["content"], "source": "bench", "uri": d["id"],
                    "workspace": "bench", "scope": "org", **({"valid_at": d["valid_at"]} if d.get("valid_at") else {})}
            try:
                r = self.client.post("/api/v1/documents", json=body, headers=_hdr(org))
                r.raise_for_status()
                if r.json().get("status") == "processed":
                    created += 1
            except httpx.HTTPStatusError as exc:
                errors += 1
                if len(notes) < 3:
                    notes.append(f"{d['id']}: HTTP {exc.response.status_code} {exc.response.text[:120]}")
            except httpx.HTTPError as exc:
                errors += 1
                if len(notes) < 3:
                    notes.append(f"{d['id']}: {type(exc).__name__} {str(exc)[:100]}")
        return IngestResult(ms=int((time.perf_counter() - t0) * 1000), items_created=created, usage=Usage(), errors=errors, notes=notes)

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        org = self._org(haystack_id)
        t0 = time.perf_counter()
        params = {"q": question, "limit": k, "workspace": "bench", "workspace_mode": "filter", "per_document": PER_DOCUMENT}
        if FUSION:
            params["fusion"] = FUSION
        r = self.client.get("/api/v1/memory/search", params=params, headers=_hdr(org))
        r.raise_for_status()
        j = r.json()
        passages = {}
        for p in j.get("passages", []):
            when = (p.get("document_title") or "").replace("session ", "").strip() or None
            passages[p["id"]] = Item(id=p["id"], text=p["content"], when=when, kind="passage", score=p.get("score"),
                                     source_id=p.get("document_uri") or p.get("document_id"))
        facts = {}
        states = {}
        for n in j.get("nodes", []):
            if n.get("type") in ("Metric", "User", "Agent"):
                continue
            pr = n.get("props") or {}
            if n.get("type") == "MetricValue" and pr.get("unit") == "state":
                # memory v9 C4: one complete running-state line per topic, served before the passages
                states[n["id"]] = Item(id=n["id"], text=n.get("name") or "", when=pr.get("as_of"), kind="state", score=n.get("score"),
                                       source_id=pr.get("source_uri"))
                continue
            facts[n["id"]] = Item(id=n["id"], text=n.get("name") or "", when=pr.get("event_date") or pr.get("mention_date"), kind="fact",
                                  score=n.get("score"), source_id=pr.get("source_uri"))
        items = []
        if FUSION and j.get("fused"):
            for f in j["fused"]:
                it = passages.get(f["id"]) if f["kind"] == "passage" else facts.get(f["id"])
                if it is not None:
                    items.append(it)
        else:
            items = list(states.values())[:MAX_STATES] + list(passages.values()) + (list(facts.values())[:10] if SHOW_FACTS else [])
        return SearchResult(items=items, ms=int((time.perf_counter() - t0) * 1000), usage=Usage(),
                            raw={"timings_ms": j.get("timings_ms"), "nodes": len(j.get("nodes", [])), "passages": len(passages), "fusion": FUSION or None})

    def stats(self, haystack_id: str) -> dict:
        org = self._org(haystack_id)
        r = self.client.get("/api/v1/ops/stats", headers=_hdr(org))
        return r.json() if r.status_code == 200 else {}

    def trace(self, haystack_id: str) -> dict:
        # our stored state is the documents' chunks; the search rows already carry what was returned
        return {"note": "direct tier: verbatim chunks per session; see the service for facts"}
