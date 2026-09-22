#!/usr/bin/env python3
"""Public memory benchmarks through the public API (memory v7 WP12).

    uv run python evals/benchmarks/bench.py run --bench longmemeval --split oracle --limit 60 --stratify
    uv run python evals/benchmarks/bench.py run --bench locomo --limit 200
    uv run python evals/benchmarks/bench.py run --bench convomem --category user_evidence_1 --context-sizes 1,20,100 --per-size 20

Protocol (DESIGN.md §3.14): one isolated org per haystack (a LongMemEval question, a LoCoMo
conversation, a ConvoMem test case); every session is one document with its session date as valid
time; nothing but the public API is used (documents, search, answer). Two arms: `direct` (no model on
the write path, the default) and `llm` (extraction on, budgeted). The reader answers from the top-k
passages and current facts; the judge is the benchmark's own prompt (LongMemEval: the official
per-type templates; LoCoMo: the official token F1 plus the Mem0-style LLM judge on categories 1-4;
ConvoMem: exact/semantic match by category). Every question records ingest time, search and answer
latency, context tokens, recall@5/@15 (session level), the reader's answer, the judge's label and
the model cost in tokens and compute units. A run writes rows.jsonl, summary.json and RESULTS.md.

The reader and judge run through the MLPal gateway (`MLPAL_LLM_API_KEY`). The official LongMemEval
judge is gpt-4o-2024-08-06; the gateway serves Claude models, so the judge model is recorded on every
row and the number is labelled with it. Nothing here reads the operator's tenant: every org is
`bench-<bench>-<id>`.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import string
import sys
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DATA = HERE / "datasets"
BASE = os.environ.get("MEMORY_URL", "http://localhost:8000")
GATEWAY = os.environ.get("MLPAL_GATEWAY_URL", "https://models.mlpal.ai")
KEY = os.environ.get("MLPAL_LLM_API_KEY", "")
READER = os.environ.get("BENCH_READER", "claude-sonnet-5")
JUDGE = os.environ.get("BENCH_JUDGE", "claude-sonnet-5")
TOPK = int(os.environ.get("BENCH_TOPK", "20"))
PER_DOCUMENT = int(os.environ.get("BENCH_PER_DOCUMENT", "8"))  # passages per session in the page
CONTEXT_TOKEN_BUDGET = int(os.environ.get("BENCH_CONTEXT_TOKENS", "6000"))
CHARS_PER_TOKEN = 4
CONTEXT_SOURCE = os.environ.get("BENCH_CONTEXT", "passages")  # passages | packet

# USD per million tokens (input, output); estimates for the report, the gateway's compute units are the meter
PRICE = {"claude-sonnet-5": (3.0, 15.0), "claude-haiku-4-5-20251001": (1.0, 5.0), "claude-opus-5": (15.0, 75.0)}


def hdr(org: str, user: str = "bench") -> dict:
    return {"X-Test-Org-Id": org, "X-Test-User-Id": user, "X-Test-Permissions": "memory.read,memory.write", "Content-Type": "application/json"}


# ------------------------------------------------------------------ gateway

class Gateway:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(base_url=GATEWAY, timeout=180, headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"})
        self.usage: dict[str, dict] = defaultdict(lambda: {"input": 0, "output": 0, "calls": 0, "compute_units": 0.0, "latency_ms": 0})

    async def chat(self, model: str, system: str | None, user: str, max_tokens: int = 400) -> tuple[str, dict]:
        text, tok = await self._chat(model, system, user, max_tokens)
        if not text and tok.get("finish_reason") == "length":
            # memory v8 L29: Sonnet 5 through the gateway spends output tokens on reasoning before the
            # answer; when reasoning alone fills `max_tokens` the content comes back empty and the
            # question is scored wrong for a budget, not a memory, reason. One retry at four times the budget.
            text, tok2 = await self._chat(model, system, user, max_tokens * 4)
            tok = {**tok2, "retried_after_length": True, "first_attempt": tok}
        return text, tok

    async def _chat(self, model: str, system: str | None, user: str, max_tokens: int) -> tuple[str, dict]:
        msgs = ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]
        body = {"model": model, "max_tokens": max_tokens, "messages": msgs}
        for attempt in range(4):
            try:
                r = await self.client.post("/v1/chat/completions", json=body)
                if r.status_code == 429 or r.status_code >= 500:
                    raise httpx.HTTPStatusError("retry", request=r.request, response=r)
                r.raise_for_status()
                d = r.json()
                break
            except (httpx.HTTPError, json.JSONDecodeError) as exc:
                if attempt == 3:
                    return "", {"error": str(exc)[:200]}
                await asyncio.sleep(2 ** attempt)
        # the gateway answers in its own shape ({content, cost}) or OpenAI's ({choices, usage})
        if "choices" in d:
            text = (d["choices"][0].get("message") or {}).get("content") or ""
            u = d.get("usage") or {}
            tok = {"input": int(u.get("prompt_tokens") or 0), "output": int(u.get("completion_tokens") or 0), "compute_units": 0.0, "latency_ms": 0,
                   "finish_reason": d["choices"][0].get("finish_reason")}
        else:
            text = d.get("content") or ""
            c = d.get("cost") or {}
            t = c.get("tokens") or {}
            tok = {"input": int(t.get("input_tokens") or 0), "output": int(t.get("output_tokens") or 0),
                   "compute_units": float(c.get("compute_units") or 0.0), "latency_ms": int(c.get("latency_ms") or 0),
                   "reasoning": int(t.get("reasoning_tokens") or 0), "finish_reason": d.get("finish_reason")}
        agg = self.usage[model]
        agg["input"] += tok["input"]; agg["output"] += tok["output"]; agg["calls"] += 1
        agg["compute_units"] += tok["compute_units"]; agg["latency_ms"] += tok["latency_ms"]
        return text.strip(), tok

    def cost_usd(self) -> float:
        total = 0.0
        for model, u in self.usage.items():
            pin, pout = PRICE.get(model, (3.0, 15.0))
            total += u["input"] / 1e6 * pin + u["output"] / 1e6 * pout
        return round(total, 4)


# ------------------------------------------------------------------ memory client

class Memory:
    def __init__(self, org: str) -> None:
        self.org = org
        self.client = httpx.AsyncClient(base_url=BASE, timeout=300, headers=hdr(org))

    async def ingest(self, docs: list[dict], concurrency: int = 4) -> dict:
        """docs: [{id, title, content, valid_at}] — one document per session; idempotent by id."""
        sem = asyncio.Semaphore(concurrency)
        statuses: Counter = Counter()
        stage: dict[str, int] = {}
        t0 = time.perf_counter()

        async def one(d):
            async with sem:
                # the episodes table's event_id is 64 chars; org + a ConvoMem conversation UUID overran it
                # (every document errored, found by the ConvoMem runs), so the id is a stable hash
                import hashlib
                eid = f"bench:{hashlib.sha256(f'{self.org}:{d['id']}'.encode()).hexdigest()[:48]}"
                body = {"event_id": eid, "title": d["title"], "content": d["content"], "source": "bench",
                        "uri": d["id"], "workspace": "bench", "scope": "org", **({"valid_at": d["valid_at"]} if d.get("valid_at") else {})}
                for attempt in range(3):
                    try:
                        r = await self.client.post("/api/v1/documents", json=body)
                        r.raise_for_status()
                        j = r.json()
                        statuses[j.get("status", "?")] += 1
                        for k, v in (j.get("timings_ms") or {}).items():
                            stage[k] = stage.get(k, 0) + int(v or 0)
                        return
                    except httpx.HTTPError:
                        if attempt == 2:
                            statuses["error"] += 1
                        await asyncio.sleep(1 + attempt)

        await asyncio.gather(*(one(d) for d in docs))
        return {"docs": len(docs), "chars": sum(len(d["content"]) for d in docs), "ms": int((time.perf_counter() - t0) * 1000), "statuses": dict(statuses),
                "stage_ms": stage}

    async def search(self, q: str, limit: int = TOPK) -> tuple[dict, int]:
        t0 = time.perf_counter()
        r = await self.client.get("/api/v1/memory/search", params={"q": q, "limit": limit, "workspace": "bench", "workspace_mode": "filter", "per_document": PER_DOCUMENT})
        r.raise_for_status()
        return r.json(), int((time.perf_counter() - t0) * 1000)

    async def answer_packet(self, q: str) -> tuple[dict, int]:
        t0 = time.perf_counter()
        r = await self.client.get("/api/v1/memory/answer", params={"q": q, "workspace": "bench", "per_document": PER_DOCUMENT,
                                                                  "max_passages": TOPK, "full_passages": "true"})
        r.raise_for_status()
        return r.json(), int((time.perf_counter() - t0) * 1000)

    async def aclose(self) -> None:
        await self.client.aclose()


# ------------------------------------------------------------------ datasets

def _lme_date(s: str) -> str | None:
    try:
        return datetime.strptime(s.strip(), "%Y/%m/%d (%a) %H:%M").replace(tzinfo=UTC).isoformat()
    except ValueError:
        return None


def load_longmemeval(split: str, limit: int | None, stratify: bool) -> list[dict]:
    path = DATA / "longmemeval" / ("longmemeval_oracle.json" if split == "oracle" else "longmemeval_s.json")
    data = json.load(open(path))
    if stratify and limit:
        per = max(1, limit // 6)
        picked, seen = [], Counter()
        for q in data:
            if seen[q["question_type"]] < per:
                picked.append(q); seen[q["question_type"]] += 1
        data = picked
    elif limit:
        data = data[:limit]
    items = []
    for q in data:
        docs = []
        for sid, date, sess in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"], strict=True):
            text = "\n".join(f"{t['role']}: {t['content']}" for t in sess)
            docs.append({"id": sid, "title": f"session {date}", "content": text, "valid_at": _lme_date(date)})
        items.append({"id": q["question_id"], "split": split, "type": q["question_type"], "question": q["question"], "answer": q["answer"],
                      "question_date": q["question_date"], "gold_sessions": set(q["answer_session_ids"]), "docs": docs,
                      "abstention": str(q["question_id"]).endswith("_abs")})
    return items


def _locomo_date(s: str) -> str | None:
    for fmt in ("%I:%M %p on %d %B, %Y", "%I:%M %p on %d %B %Y"):
        try:
            return datetime.strptime(s.strip(), fmt).replace(tzinfo=UTC).isoformat()
        except ValueError:
            continue
    return None


def load_locomo(limit: int | None, conversations: int | None) -> list[dict]:
    data = json.load(open(DATA / "locomo" / "locomo10.json"))
    if conversations:
        data = data[:conversations]
    items = []
    for ci, conv in enumerate(data):
        c = conv["conversation"]
        docs = []
        n = 1
        while f"session_{n}" in c:
            turns = c[f"session_{n}"]
            date = c.get(f"session_{n}_date_time", "")
            text = "\n".join(f"{t['speaker']}: {t['text']}" + (f" [shared a photo: {t['blip_caption']}]" if t.get("blip_caption") else "") for t in turns)
            docs.append({"id": f"session_{n}", "title": f"session {n} ({date})", "content": f"Conversation on {date}\n{text}", "valid_at": _locomo_date(date)})
            n += 1
        qa = conv["qa"] if not limit else conv["qa"][: max(1, limit // len(data))]
        for qi, q in enumerate(qa):
            gold = {f"session_{e.split(':')[0][1:]}" for e in (q.get("evidence") or []) if ":" in e}
            # the haystack is the conversation: all of its questions share one org (protocol §1)
            items.append({"id": f"c{ci}-q{qi}", "haystack_id": f"c{ci}", "conversation": ci, "type": str(q.get("category")), "question": q["question"],
                          "answer": str(q.get("answer", q.get("adversarial_answer", ""))), "question_date": None, "gold_sessions": gold, "docs": docs,
                          "abstention": q.get("category") == 5})
    return items


def load_convomem(category: str, sizes: list[int], per_size: int) -> list[dict]:
    files = sorted((DATA / "convomem" / category).glob("batched_*.json"))
    items: list[dict] = []
    want = {s: per_size for s in sizes}
    base_day = datetime(2025, 1, 1, tzinfo=UTC)
    for f in files:
        for tc in json.load(open(f)):
            cs = tc.get("contextSize")
            if want.get(cs, 0) <= 0:
                continue
            want[cs] -= 1
            ev = tc["evidenceItems"][0]
            docs = []
            for i, conv in enumerate(tc["conversations"]):
                text = "\n".join(f"{m['speaker']}: {m['text']}" for m in conv["messages"])
                docs.append({"id": conv["id"], "title": f"conversation {i + 1}", "content": text, "valid_at": (base_day + timedelta(days=i)).isoformat(), "evidence": conv.get("containsEvidence", False)})
            items.append({"id": f"{category}-{cs}-{len(items)}", "type": f"{category}@{cs}", "question": ev["question"], "answer": ev["answer"],
                          "question_date": None, "gold_sessions": {d["id"] for d in docs if d["evidence"]}, "docs": docs,
                          "abstention": category.startswith("abstention"), "context_size": cs})
        if all(v <= 0 for v in want.values()):
            break
    return items


# ------------------------------------------------------------------ scoring

def normalize_answer(s: str) -> str:
    s = s.replace(",", "")
    s = "".join(ch for ch in s.lower() if ch not in set(string.punctuation))
    s = re.sub(r"\b(a|an|the|and)\b", " ", s)
    return " ".join(s.split())


def f1_score(prediction: str, ground_truth: str) -> float:
    p, g = normalize_answer(prediction).split(), normalize_answer(ground_truth).split()
    common = Counter(p) & Counter(g)
    ns = sum(common.values())
    if ns == 0:
        return 0.0
    prec, rec = ns / len(p), ns / len(g)
    return 2 * prec * rec / (prec + rec)


LME_TEMPLATES = {
    "default": "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "temporal-reasoning": "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response is equivalent to the correct answer or contains all the intermediate steps to get the correct answer, you should also answer yes. If the response only contains a subset of the information required by the answer, answer no. In addition, do not penalize off-by-one errors for the number of days. If the question asks for the number of days/weeks/months, etc., and the model makes off-by-one errors (e.g., predicting 19 days when the answer is 18), the model's response is still correct. \n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "knowledge-update": "I will give you a question, a correct answer, and a response from a model. Please answer yes if the response contains the correct answer. Otherwise, answer no. If the response contains some previous information along with an updated answer, the response should be considered as correct as long as the updated answer is the required answer.\n\nQuestion: {}\n\nCorrect Answer: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "single-session-preference": "I will give you a question, a rubric for desired personalized response, and a response from a model. Please answer yes if the response satisfies the desired response. Otherwise, answer no. The model does not need to reflect all the points in the rubric. The response is correct as long as it recalls and utilizes the user's personal information correctly.\n\nQuestion: {}\n\nRubric: {}\n\nModel Response: {}\n\nIs the model response correct? Answer yes or no only.",
    "abstention": "I will give you an unanswerable question, an explanation, and a response from a model. Please answer yes if the model correctly identifies the question as unanswerable. The model could say that the information is incomplete, or some other information is given but the asked information is not.\n\nQuestion: {}\n\nExplanation: {}\n\nModel Response: {}\n\nDoes the model correctly identify the question as unanswerable? Answer yes or no only.",
}

MEM0_JUDGE = """Label the generated answer as CORRECT or WRONG.

## Rules

1. **PARTIAL CREDIT**: If the generated answer includes AT LEAST ONE correct item from the gold answer's list, mark CORRECT. Getting 1 out of 2, 2 out of 4, etc. is always acceptable. Only mark WRONG if NONE of the gold answer items appear.
2. **PARAPHRASES COUNT**: Same concept in different words is CORRECT.
3. **EXTRA DETAIL IS FINE**: A longer answer that includes the gold answer's key facts plus additional information is CORRECT.
4. **DATE TOLERANCE**: Dates within 14 days of each other are CORRECT. Durations within 50% are CORRECT.
5. **SEMANTIC OVERLAP**: Judge whether the generated answer addresses the same topic and captures the core idea of the gold answer.
6. **SAME REFERENT**: If the generated answer references the same named entity, person, or concept as the gold answer, mark CORRECT.
7. **FOCUS ON KNOWLEDGE, NOT WORDING**: Only mark WRONG when the generated answer demonstrates a genuinely different or incorrect understanding.

## ONLY mark WRONG if:
- The generated answer contains ZERO correct items from the gold answer
- The answer addresses a completely different topic

## Question
Question: {question}
Gold answer: {answer}
Generated answer: {response}

Return JSON with "reasoning" (one sentence) and "label" (CORRECT or WRONG). Do NOT include both labels."""

READER_PROMPT = """You answer a question about a person from retrieved memory. The memory below holds verbatim passages from their past conversations, each tagged with the date of that conversation, and current facts. Rules:
- Use only what is in the memory. Combine details across passages; for counts and totals, list every distinct item with its date first, then count or sum.
- When the same fact was stated at different times, the most recent statement (latest date) is the current one; earlier values are superseded.
- For time questions, compute from the conversation dates and the question date, never from today.
- If the question asks for a recommendation, suggestion or advice, give one that applies the person's stated preferences, constraints and situation from the memory (their interests, location, equipment, prior efforts); this is not a case of missing information.
- Only when the memory holds nothing relevant to a factual question, say so in one sentence ("The information is not available in the conversations") — do not guess.

Question date: {qdate}

{memory}

Question: {question}

Answer in one or two sentences, then a final line "ANSWER: <short answer>"."""


READER_PROMPT_V3 = """You answer a question about a person from retrieved memory. The memory below holds verbatim passages from their past conversations, each tagged with the date of that conversation, and current facts.

Work in two steps, and show both.

Step 1 — EVIDENCE. Go through every passage. For each one that bears on the question, write one line: the passage date, and the specific fact it states (name, number, item, place, time). Include every distinct item you find, even if it seems minor; do not stop at the first match. If two passages state the same fact, list it once with both dates. If passages state different values for the same thing, list all with their dates.

Step 2 — ANSWER, from the evidence lines only:
- Counting or totals: count or sum the listed items; say how many lines you used.
- "What is / what was" with several dated values: the latest date is the current value; earlier ones are superseded (mention them only if asked what changed).
- Time questions: compute from the listed dates and the question date, never from today; show the arithmetic in one clause.
- Recommendations or advice: apply the person's stated preferences, constraints and situation from the evidence; this is not missing information.
- Only when Step 1 produced no relevant line for a factual question: "The information is not available in the conversations".

Question date: {qdate}

{memory}

Question: {question}

Write "EVIDENCE:" then the lines, then "ANSWER: <short answer>" as the final line."""

PROMPT_VERSION = os.environ.get("BENCH_PROMPT", "v2")


def reader_prompt() -> str:
    return READER_PROMPT_V3 if PROMPT_VERSION == "v3" else READER_PROMPT


def build_memory_block(search: dict, budget_tokens: int = CONTEXT_TOKEN_BUDGET) -> tuple[str, int, list[str]]:
    lines: list[str] = []
    used = 0
    for n in search.get("nodes", [])[:10]:
        if n.get("type") in ("Metric", "User", "Agent"):
            continue
        line = f"- fact: {n.get('name')}"
        if used + len(line) // CHARS_PER_TOKEN > budget_tokens // 4:
            break
        lines.append(line); used += len(line) // CHARS_PER_TOKEN
    sessions: list[str] = []
    for p in search.get("passages", []):
        text = p["content"].strip()
        when = (p.get("document_title") or "").replace("session ", "").strip() or (p.get("valid_at") or "")[:10]
        line = f"- [{when}] {text}"
        if used + len(line) // CHARS_PER_TOKEN > budget_tokens:
            break
        lines.append(line); used += len(line) // CHARS_PER_TOKEN
        uri = p.get("document_uri") or p.get("document_id")
        if uri not in sessions:
            sessions.append(uri)
    return "Memory:\n" + "\n".join(lines) if lines else "Memory: (empty)", used, sessions


def recall_at(retrieved_sessions: list[str], gold: set[str], k: int) -> float | None:
    if not gold:
        return None
    return len(set(retrieved_sessions[:k]) & gold) / len(gold)


# ------------------------------------------------------------------ run

ORG_TAG = os.environ.get("BENCH_ORG_TAG", "")  # a fresh set of orgs for an ingest-side experiment (chunking, embedder)


# memory v8: a competitor (or ours through the adapter contract) instead of the raw service client
SYSTEM = None          # a systems.base.System instance when --system is given
TRACES_DIR: Path | None = None


def haystack_id(bench: str, item: dict) -> str:
    return f"{bench}-{item.get('split') + '-' if item.get('split') else ''}{item.get('haystack_id') or item['id']}"


def build_memory_block_from_items(items: list[dict], budget_tokens: int = CONTEXT_TOKEN_BUDGET) -> tuple[str, int, list[str]]:
    """The same context format for every system: `- [date] text`, in the system's rank order,
    under the shared budget. Returns (block, tokens used, source ids in rank order)."""
    lines: list[str] = []
    used = 0
    sources: list[str] = []
    for it in items:
        text = " ".join((it.get("text") or "").split())
        if not text:
            continue
        when = it.get("when") or ""
        line = f"- [{when}] {text}" if when else f"- {text}"
        if used + len(line) // CHARS_PER_TOKEN > budget_tokens:
            break
        lines.append(line); used += len(line) // CHARS_PER_TOKEN
        sid = it.get("source_id")
        if sid and sid not in sources:
            sources.append(sid)
    return ("Memory:\n" + "\n".join(lines)) if lines else "Memory: (empty)", used, sources


def org_for(bench: str, item: dict) -> str:
    # the org names the haystack: the same LongMemEval question has a different haystack per split;
    # an ingest-side experiment tags its orgs so the cached ingest of the baseline is not reused
    tag = f"{ORG_TAG}-" if ORG_TAG else ""
    return f"bench-{bench}-{tag}{item.get('split') + '-' if item.get('split') else ''}{item.get('haystack_id') or item['id']}"


async def ingest_item(item: dict, bench: str, ingest_state: dict) -> None:
    org = org_for(bench, item)
    if org in ingest_state:
        return
    if SYSTEM is not None:
        hid = haystack_id(bench, item)
        res = await asyncio.to_thread(SYSTEM.ingest, hid, item["docs"])
        rec = {"docs": len(item["docs"]), "chars": sum(len(d["content"]) for d in item["docs"]), **res.to_dict()}
        try:
            rec["stats"] = await asyncio.to_thread(SYSTEM.stats, hid)
            if TRACES_DIR is not None:
                (TRACES_DIR / f"{hid}.json").write_text(json.dumps(await asyncio.to_thread(SYSTEM.trace, hid), indent=1, default=str))
        except Exception as exc:  # noqa: BLE001
            rec["trace_error"] = str(exc)[:200]
        if res.errors:
            # not cached: the next run retries the haystack instead of querying a half-ingested one
            print(f"  ingest errors for {hid}: {res.errors} of {len(item['docs'])} docs; {res.notes[:1]}", file=sys.stderr)
        else:
            ingest_state[org] = rec
        return
    mem = Memory(org)
    try:
        ingest_state[org] = await mem.ingest(item["docs"])
    finally:
        await mem.aclose()


async def run_item(item: dict, bench: str, gw: Gateway, judge_model: str, reader_model: str, ingest_state: dict) -> dict:
    org = org_for(bench, item)
    mem = Memory(org)
    row = {"id": item["id"], "type": item["type"], "org": org, "question": item["question"], "gold": item["answer"], "system": SYSTEM.name if SYSTEM else "ours-raw"}
    try:
        if org not in ingest_state:
            if SYSTEM is not None:
                await ingest_item(item, bench, ingest_state)
            else:
                ingest_state[org] = await mem.ingest(item["docs"])
        row["ingest"] = ingest_state[org]
        if SYSTEM is not None:
            hid = haystack_id(bench, item)
            sr = await asyncio.to_thread(SYSTEM.search, hid, item["question"], TOPK)
            items = [i.to_dict() for i in sr.items]
            row["search_ms"] = sr.ms
            row["search_usage"] = sr.usage.to_dict()
            row["search_raw"] = sr.raw  # the system's own view of the result (counts, facts, timings) for the trace reading
            row["items"] = items
            memory_block, ctx_tokens, ranked_sessions = build_memory_block_from_items(items)
            all_sessions = []
            for i in items:
                if i.get("source_id") and i["source_id"] not in all_sessions:
                    all_sessions.append(i["source_id"])
            search = {"passages": [], "nodes": []}
        else:
            search, search_ms = await mem.search(item["question"])
            row["search_ms"] = search_ms
            row["search_timings_ms"] = search.get("timings_ms")
            memory_block, ctx_tokens, ranked_sessions = build_memory_block(search)
            all_sessions = []
            for p in search.get("passages", []):
                u = p.get("document_uri") or p.get("document_id")
                if u not in all_sessions:
                    all_sessions.append(u)
        if CONTEXT_SOURCE == "packet" and SYSTEM is None:
            # the service's own answer packet (facts + freshness-ranked, dated passages with citations)
            packet, packet_ms = await mem.answer_packet(item["question"])
            row["packet_ms"] = packet_ms
            memory_block = "Memory:\n" + packet.get("markdown", "")
            ctx_tokens = len(memory_block) // CHARS_PER_TOKEN
        # session-level recall on the ranked items (unique sources, in rank order)
        row["recall@5"] = recall_at(all_sessions, item["gold_sessions"], 5)
        row["recall@15"] = recall_at(all_sessions, item["gold_sessions"], 15)
        row["context_tokens"] = ctx_tokens
        row["passages"] = len(row.get("items") or search.get("passages", []))
        await answer_and_judge(row, item, bench, memory_block, gw, judge_model, reader_model)
    except Exception as exc:  # noqa: BLE001 — one question's failure is a row, not a crash
        row["error"] = str(exc)[:300]
        row["correct"] = None
    finally:
        await mem.aclose()
    return row


async def answer_and_judge(row: dict, item: dict, bench: str, memory_block: str, gw: Gateway, judge_model: str, reader_model: str) -> None:
    """The reader over the memory block, then the benchmark's judge; writes the answer and verdict fields of `row`."""
    if True:
        t0 = time.perf_counter()
        prompt = reader_prompt().format(qdate=item.get("question_date") or "unknown", memory=memory_block, question=item["question"])
        text, tok = await gw.chat(reader_model, None, prompt, max_tokens=400 if PROMPT_VERSION == "v2" else 900)
        row["answer_ms"] = int((time.perf_counter() - t0) * 1000)
        row["reader_tokens"] = tok
        final = text.split("ANSWER:")[-1].strip() if "ANSWER:" in text else text
        row["response"] = text
        row["short_answer"] = final[:500]
        # judge
        if bench == "longmemeval":
            tpl = LME_TEMPLATES["abstention"] if item["abstention"] else LME_TEMPLATES.get(item["type"], LME_TEMPLATES["default"])
            verdict, jtok = await gw.chat(judge_model, None, tpl.format(item["question"], item["answer"], text), max_tokens=10)
            row["judge"] = {"model": judge_model, "raw": verdict, "tokens": jtok}
            row["correct"] = "yes" in verdict.lower()
        elif bench == "locomo":
            row["f1"] = round(f1_score(final, item["answer"]), 3)
            if item["type"] in ("1", "2", "3", "4"):
                verdict, jtok = await gw.chat(judge_model, "You are evaluating conversational AI memory recall. Return JSON only with the format requested.",
                                              MEM0_JUDGE.format(question=item["question"], answer=item["answer"], response=text), max_tokens=120)
                row["judge"] = {"model": judge_model, "raw": verdict[:300], "tokens": jtok}
                row["correct"] = '"CORRECT"' in verdict.upper() or ("CORRECT" in verdict.upper() and "WRONG" not in verdict.upper())
            else:
                # adversarial (category 5): correct when the reader abstains
                row["correct"] = bool(re.search(r"not (available|mentioned|specified|in the conversations)|no information|cannot be determined|unknown", text, re.I))
        else:  # convomem
            if item["abstention"]:
                row["correct"] = bool(re.search(r"not (available|mentioned|specified|in the conversations)|no information|cannot be determined|don't know|do not know", text, re.I))
            else:
                verdict, jtok = await gw.chat(judge_model, None, LME_TEMPLATES["default"].format(item["question"], item["answer"], text), max_tokens=10)
                row["judge"] = {"model": judge_model, "raw": verdict, "tokens": jtok}
                row["correct"] = "yes" in verdict.lower()


async def rescore_async(args) -> int:
    """Re-ask the reader (and re-judge) for the rows of a finished run whose answer came back empty
    (memory v8 L29: the reasoning budget), from the items the run stored. Rewrites rows.jsonl,
    summary.json and RESULTS.md in place and records what changed. LongMemEval system runs only."""
    out = Path(args.run_dir)
    summary = json.load(open(out / "summary.json"))
    if summary.get("bench") != "longmemeval" or not summary.get("system") or summary["system"] == "ours-raw":
        print("rescore: LongMemEval runs with stored items only"); return 2
    rows = [json.loads(l) for l in (out / "rows.jsonl").read_text().splitlines() if l.strip()]
    todo = [r for r in rows if r.get("correct") is not None and not (r.get("response") or "").strip() and r.get("items") is not None]
    if not todo:
        print("rescore: no empty answers"); return 0
    by_id = {q["id"]: q for q in load_longmemeval(summary["split"], None, False)}
    gw = Gateway()
    was = summary["accuracy"]
    changed = []
    for r in todo:
        item = by_id[r["id"]]
        block, _, _ = build_memory_block_from_items(r["items"])
        before = r["correct"]
        await answer_and_judge(r, item, "longmemeval", block, gw, summary["judge"], summary["reader"])
        r["rescored"] = {"at": datetime.now(UTC).isoformat(), "was_correct": before}
        changed.append((r["id"], r["type"], before, r["correct"]))
        print(f"{'✓' if r['correct'] else '✗'} {r['id']} {r['type']:<28} was {before} -> {r['correct']}  {r['short_answer'][:70]!r}")
    (out / "rows.jsonl").write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))
    ok = [r for r in rows if r.get("correct") is not None]
    summary["accuracy"] = round(sum(1 for r in ok if r["correct"]) / len(ok), 3)
    for t, group in _group(ok, "type").items():
        summary["by_type"][t]["accuracy"] = round(sum(1 for r in group if r["correct"]) / len(group), 3)
    summary["cost_usd_estimate"] = round((summary.get("cost_usd_estimate") or 0) + gw.cost_usd(), 4)
    summary.setdefault("rescored", []).append({"at": datetime.now(UTC).isoformat(), "rows": len(todo), "accuracy_before": was, "accuracy_after": summary["accuracy"],
                                               "fixed": sum(1 for c in changed if c[3] and not c[2])})
    json.dump(summary, open(out / "summary.json", "w"), indent=1, default=str)
    (out / "RESULTS.md").write_text(render_md(summary))
    await gw.client.aclose()
    print(f"rescored {len(todo)} rows: accuracy {was} -> {summary['accuracy']}")
    return 0


def summarize(rows: list[dict], bench: str, args, gw: Gateway, wall_s: float) -> dict:
    ok = [r for r in rows if r.get("correct") is not None]
    by_type: dict[str, dict] = {}
    for t, group in _group(ok, "type").items():
        by_type[t] = {"n": len(group), "accuracy": round(sum(1 for r in group if r["correct"]) / len(group), 3),
                      **({"f1": round(sum(r.get("f1", 0) for r in group) / len(group), 3)} if bench == "locomo" else {}),
                      "recall@5": _mean([r["recall@5"] for r in group if r.get("recall@5") is not None]),
                      "recall@15": _mean([r["recall@15"] for r in group if r.get("recall@15") is not None])}
    lat = lambda k: {"p50": _pct([r[k] for r in ok if k in r], 0.5), "p95": _pct([r[k] for r in ok if k in r], 0.95)}  # noqa: E731
    ingests = [r["ingest"] for r in rows if r.get("ingest")]
    out = {
        "bench": bench, "split": getattr(args, "split", None), "arm": args.arm, "reader": args.reader, "judge": args.judge, "topk": TOPK, "per_document": PER_DOCUMENT, "context_token_budget": CONTEXT_TOKEN_BUDGET, "context_source": CONTEXT_SOURCE, "reader_prompt_version": ("v3-evidence-then-answer" if PROMPT_VERSION == "v3" else "v2-dated-preferences"), "org_tag": ORG_TAG or None,
        "judge_note": "official LongMemEval judge is gpt-4o-2024-08-06; this run used the gateway model named in `judge`" if bench == "longmemeval" else None,
        "n": len(rows), "scored": len(ok), "errors": len(rows) - len(ok),
        "accuracy": round(sum(1 for r in ok if r["correct"]) / len(ok), 3) if ok else None,
        **({"f1": round(sum(r.get("f1", 0) for r in ok) / len(ok), 3),
            "accuracy_cat1_4": _acc([r for r in ok if r["type"] in ("1", "2", "3", "4")])} if bench == "locomo" and ok else {}),
        "recall@5": _mean([r["recall@5"] for r in ok if r.get("recall@5") is not None]),
        "recall@15": _mean([r["recall@15"] for r in ok if r.get("recall@15") is not None]),
        "context_tokens": {"mean": _mean([r["context_tokens"] for r in ok if "context_tokens" in r]), "p95": _pct([r["context_tokens"] for r in ok if "context_tokens" in r], 0.95)},
        "search_ms": lat("search_ms"), "answer_ms": lat("answer_ms"),
        "search_stage_ms_mean": {k: _mean([r["search_timings_ms"][k] for r in ok if r.get("search_timings_ms") and k in r["search_timings_ms"]])
                                 for k in ("derived_ms", "direct_ms", "direct_embed_ms", "direct_vector_ms", "direct_idf_ms", "direct_lexical_ms", "direct_title_ms")},
        "ingest_stage_ms": {k: sum((i.get("stage_ms") or {}).get(k, 0) for i in ingests) for k in ("chunks", "chunk_ms", "embed_ms", "insert_ms", "direct_ms", "derived_ms")},
        "ingest": {"docs": sum(i["docs"] for i in ingests), "chars": sum(i["chars"] for i in ingests), "ms": sum(i["ms"] for i in ingests),
                   "chars_per_s": round(sum(i["chars"] for i in ingests) / max(1e-9, sum(i["ms"] for i in ingests) / 1000)) if ingests else None},
        "by_type": by_type,
        "model_usage": dict(gw.usage), "cost_usd_estimate": gw.cost_usd(),
        "system": (SYSTEM.name if SYSTEM else "ours-raw"),
        "system_usage": _system_usage(rows) if SYSTEM else None,
        "wall_s": round(wall_s, 1), "service": BASE, "at": datetime.now(UTC).isoformat(),
    }
    return out


SYSTEM_PRICE = {"llm_in": 1.0, "llm_out": 5.0, "embed": 0.02}  # USD per million tokens: Haiku 4.5, text-embedding-3-small


def _system_usage(rows: list[dict]) -> dict:
    ing = {}
    seen = set()
    tot = {"ingest": {"llm_calls": 0, "llm_input_tokens": 0, "llm_output_tokens": 0, "embed_calls": 0, "embed_tokens": 0},
           "search": {"llm_calls": 0, "llm_input_tokens": 0, "llm_output_tokens": 0, "embed_calls": 0, "embed_tokens": 0},
           "items_created": 0, "ingest_errors": 0}
    for r in rows:
        org = r.get("org")
        if org not in seen and isinstance(r.get("ingest"), dict) and "usage" in r["ingest"]:
            seen.add(org)
            for k, v in r["ingest"]["usage"].items():
                tot["ingest"][k] += int(v or 0)
            tot["items_created"] += int(r["ingest"].get("items_created") or 0)
            tot["ingest_errors"] += int(r["ingest"].get("errors") or 0)
        for k, v in (r.get("search_usage") or {}).items():
            tot["search"][k] += int(v or 0)
    def usd(u):
        return round(u["llm_input_tokens"] / 1e6 * SYSTEM_PRICE["llm_in"] + u["llm_output_tokens"] / 1e6 * SYSTEM_PRICE["llm_out"] + u["embed_tokens"] / 1e6 * SYSTEM_PRICE["embed"], 4)
    tot["ingest_usd_estimate"] = usd(tot["ingest"]); tot["search_usd_estimate"] = usd(tot["search"])
    return tot


def _group(rows, key):
    g: dict[str, list] = defaultdict(list)
    for r in rows:
        g[str(r.get(key))].append(r)
    return dict(sorted(g.items()))


def _mean(v):
    return round(sum(v) / len(v), 3) if v else None


def _acc(v):
    return round(sum(1 for r in v if r["correct"]) / len(v), 3) if v else None


def _pct(v, p):
    if not v:
        return None
    s = sorted(v)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def render_md(s: dict) -> str:
    lines = [f"# {s['bench']} — {s.get('split') or ''} — arm {s['arm']} — {s['at'][:16]}Z", "",
             f"reader `{s['reader']}` · judge `{s['judge']}` · service {s['service']} · n={s['n']} scored={s['scored']} errors={s['errors']}", ""]
    if s.get("judge_note"):
        lines += [f"_{s['judge_note']}_", ""]
    if s.get("system_usage"):
        su = s["system_usage"]
        lines += [f"system `{s['system']}` · write-time model usage: {su['ingest']} ≈ ${su['ingest_usd_estimate']} · search-time: {su['search']} ≈ ${su['search_usd_estimate']} · items created {su['items_created']} · ingest errors {su['ingest_errors']}", ""]
    head = "| metric | value |\n|---|---|"
    body = [f"| accuracy | {s['accuracy']} |"]
    if "f1" in s:
        body += [f"| F1 (official LoCoMo metric) | {s['f1']} |", f"| accuracy, categories 1-4 (Mem0-style judge) | {s['accuracy_cat1_4']} |"]
    body += [f"| recall@5 (session) | {s['recall@5']} |", f"| recall@15 (session) | {s['recall@15']} |",
             f"| context tokens mean / p95 | {s['context_tokens']['mean']} / {s['context_tokens']['p95']} |",
             f"| search ms p50 / p95 | {s['search_ms']['p50']} / {s['search_ms']['p95']} |",
             f"| answer ms p50 / p95 (reader call) | {s['answer_ms']['p50']} / {s['answer_ms']['p95']} |",
             f"| ingest | {s['ingest']['docs']} docs, {s['ingest']['chars']} chars, {s['ingest']['ms']} ms ({s['ingest']['chars_per_s']} chars/s) |",
             f"| model cost | {json.dumps({m: {k: v for k, v in u.items() if k != 'latency_ms'} for m, u in s['model_usage'].items()})} ≈ ${s['cost_usd_estimate']} |",
             f"| wall | {s['wall_s']} s |"]
    lines += [head, *body, "", "## By type", "", "| type | n | accuracy | recall@5 | recall@15 |", "|---|---|---|---|---|"]
    for t, v in s["by_type"].items():
        lines.append(f"| {t} | {v['n']} | {v['accuracy']}{(' (F1 ' + str(v['f1']) + ')') if 'f1' in v else ''} | {v['recall@5']} | {v['recall@15']} |")
    return "\n".join(lines) + "\n"


async def main_async(args) -> int:
    if not KEY:
        print("MLPAL_LLM_API_KEY is required for the reader and judge", file=sys.stderr)
        return 2
    if args.bench == "longmemeval":
        items = load_longmemeval(args.split, args.limit, args.stratify)
    elif args.bench == "locomo":
        items = load_locomo(args.limit, args.conversations)
    else:
        items = load_convomem(args.category, [int(x) for x in args.context_sizes.split(",")], args.per_size)
    global SYSTEM, TRACES_DIR
    sysname = getattr(args, "system", None)
    tag = f"{args.bench}-{args.split or args.category or 'all'}-{sysname + '-' if sysname else ''}{args.arm}-{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"
    out = HERE / "results" / tag
    out.mkdir(parents=True, exist_ok=True)
    state_path = HERE / "results" / (f".ingested-{args.bench}-{sysname}.json" if sysname else f".ingested-{args.bench}.json")
    if sysname:
        import importlib
        import pkgutil

        import systems as _systems  # noqa: F401  (evals/benchmarks/systems)
        for m in pkgutil.iter_modules(_systems.__path__):
            importlib.import_module(f"systems.{m.name}")
        from systems.base import REGISTRY

        if sysname not in REGISTRY:
            print(f"unknown system {sysname}; known: {sorted(REGISTRY)}", file=sys.stderr)
            return 2
        SYSTEM = REGISTRY[sysname]()
        TRACES_DIR = out / "traces"
        TRACES_DIR.mkdir(exist_ok=True)
        from systems import meters

        meters.log_to(out / "model_calls.jsonl")  # every SDK model call the system makes, prompts included (DESIGN §7)
        SYSTEM.setup(HERE / "results" / f".system-{sysname}-{args.bench}")
        (out / "CONDITIONS.json").write_text(json.dumps(SYSTEM.conditions, indent=1))
    ingest_state = json.load(open(state_path)) if state_path.exists() else {}
    gw = Gateway()
    rows: list[dict] = []
    t0 = time.perf_counter()
    sem = asyncio.Semaphore(args.concurrency)
    total = len(items)
    done = 0

    # phase 1: index every haystack; phase 2: query. Reads measured while other haystacks were
    # being embedded carried the ingest's CPU (8 s search p50 on the first S run); the protocol is
    # index, then query, as every published harness does.
    seen_orgs: set[str] = set()
    todo = []
    for i in items:
        o = org_for(args.bench, i)
        if o in ingest_state or o in seen_orgs:
            continue
        seen_orgs.add(o); todo.append(i)
    # memory v8 DESIGN §6: a system's write-time model spend for this run is capped; at the cap the
    # remaining haystacks are not ingested and phase 2 scores only the haystacks that were
    cap = getattr(args, "ingest_usd_cap", None)
    cap_state = {"usd": 0.0, "hit": False, "skipped": 0}
    if todo:
        print(f"phase 1: ingesting {len(todo)} haystacks ({sum(len(i['docs']) for i in todo)} documents)", flush=True)
        ingested = 0

        async def ing(item):
            nonlocal ingested
            async with sem:
                if cap_state["hit"]:
                    cap_state["skipped"] += 1
                    return
                await ingest_item(item, args.bench, ingest_state)
            ingested += 1
            st = ingest_state.get(org_for(args.bench, item)) or {}
            print(f"  ingested {ingested}/{len(todo)} {item['type']:<28} {st.get('docs')} docs {st.get('chars')} chars {st.get('ms')} ms", flush=True)
            if cap is not None and isinstance(st.get("usage"), dict):
                cap_state["usd"] += _system_usage([{"org": org_for(args.bench, item), "ingest": st}])["ingest_usd_estimate"]
                if cap_state["usd"] >= cap and not cap_state["hit"]:
                    cap_state["hit"] = True
                    print(f"  ingest spend ${cap_state['usd']:.2f} reached the cap ${cap:.2f} after {ingested} haystacks; the rest are skipped", flush=True)
            if ingested % 5 == 0:
                json.dump(ingest_state, open(state_path, "w"))

        await asyncio.gather(*(ing(i) for i in todo))
        json.dump(ingest_state, open(state_path, "w"))
        if cap_state["hit"]:
            items = [i for i in items if org_for(args.bench, i) in ingest_state]
            total = len(items)
            print(f"phase 2: querying {total} questions whose haystacks were ingested ({cap_state['skipped']} haystacks skipped at the cap)", flush=True)
        else:
            print("phase 2: querying", flush=True)

    async def one(item):
        nonlocal done
        async with sem:
            row = await run_item(item, args.bench, gw, args.judge, args.reader, ingest_state)
        rows.append(row)
        done += 1
        with open(out / "rows.jsonl", "a") as f:
            f.write(json.dumps(row, default=str) + "\n")
        mark = "✓" if row.get("correct") else ("✗" if row.get("correct") is False else "!")
        print(f"{mark} {done}/{total} {row['type']:<28} r@15={row.get('recall@15')} search={row.get('search_ms')}ms  {str(row.get('short_answer') or row.get('error'))[:70]}", flush=True)
        if done % 10 == 0:
            json.dump(ingest_state, open(state_path, "w"))

    await asyncio.gather(*(one(i) for i in items))
    json.dump(ingest_state, open(state_path, "w"))
    summary = summarize(rows, args.bench, args, gw, time.perf_counter() - t0)
    if cap is not None:
        summary["ingest_usd_cap"] = {"cap": cap, "spent_this_run": round(cap_state["usd"], 4), "hit": cap_state["hit"], "haystacks_skipped": cap_state["skipped"]}
    json.dump(summary, open(out / "summary.json", "w"), indent=1, default=str)
    (out / "RESULTS.md").write_text(render_md(summary))
    await gw.client.aclose()
    print(f"\n{out}\naccuracy={summary['accuracy']} recall@15={summary['recall@15']} cost≈${summary['cost_usd_estimate']} wall={summary['wall_s']}s")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rs = sub.add_parser("rescore", help="re-ask the reader for a finished run's empty answers (memory v8 L29) and rewrite its files")
    rs.add_argument("run_dir")
    r = sub.add_parser("run")
    r.add_argument("--bench", required=True, choices=("longmemeval", "locomo", "convomem"))
    r.add_argument("--split", default="oracle", choices=("oracle", "s"))
    r.add_argument("--limit", type=int, default=None)
    r.add_argument("--stratify", action="store_true")
    r.add_argument("--conversations", type=int, default=None)
    r.add_argument("--category", default="user_evidence_1")
    r.add_argument("--context-sizes", default="1,20,100")
    r.add_argument("--per-size", type=int, default=20)
    r.add_argument("--arm", default="direct", choices=("direct", "llm"))
    r.add_argument("--reader", default=READER)
    r.add_argument("--judge", default=JUDGE)
    r.add_argument("--concurrency", type=int, default=2)
    r.add_argument("--system", default=None, help="memory v8: run a System adapter (ours, mem0, ...) instead of the raw service client")
    r.add_argument("--ingest-usd-cap", type=float, default=None, help="memory v8: stop ingesting once this run's write-time model spend (Haiku/embedding list prices) reaches this many USD; score what was ingested")
    args = ap.parse_args()
    if args.cmd == "rescore":
        return asyncio.run(rescore_async(args))
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
