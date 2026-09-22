"""memory v9 round 3: a load run against the service — long histories ingested concurrently while
queries run against the ones already in, with latency percentiles and error classes recorded.

    python evals/load/load_run.py --histories 200 --ingest-concurrency 6 --query-concurrency 4 \\
        --queries-per-history 3 --out evals/load/results/<name>

Data: LongMemEval-S haystacks (≈ 48 sessions each) from `evals/benchmarks/datasets/`, one org per
history (`load-<run>-<n>`), sessions posted as documents through the public API with dev auth.
Queries: the haystack's own question plus generic ones, against a random already-ingested org.
Output: `rows.jsonl` (one line per request: kind, org, ms, status, error class), `summary.json`
(percentiles per kind, throughput, error classes, the service's /metrics counters before and after,
container memory if docker is reachable), `SUMMARY.md`. No model spend: the direct arm, local
embedder; the reader is not involved.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import random
import subprocess
import time
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
DATA = HERE.parent / "benchmarks" / "datasets" / "longmemeval" / "longmemeval_s.json"
BASE = "http://localhost:8000"
GENERIC = ["What did I say about my work?", "Which plans did I mention for the weekend?", "What have I bought recently?",
           "Remind me what you recommended last time.", "How many events did I attend?"]


def _hdr(org: str) -> dict:
    return {"X-Test-Org-Id": org, "X-Test-User-Id": "load", "X-Test-Permissions": "memory.read,memory.write", "Content-Type": "application/json"}


def _pct(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def _err_class(exc: Exception | None, status: int | None) -> str | None:
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, httpx.TransportError):
            return "transport"
        return type(exc).__name__
    if status is None or status < 400:
        return None
    return f"http_{status}"


async def _metrics(client: httpx.AsyncClient) -> dict[str, str]:
    try:
        r = await client.get(f"{BASE}/metrics", timeout=10)
        out = {}
        for line in r.text.splitlines():
            if line.startswith("memory_") and " " in line and not line.startswith("#"):
                k, v = line.rsplit(" ", 1)
                out[k] = v
        return out
    except Exception:  # noqa: BLE001
        return {}


def _docker_mem() -> dict[str, str]:
    try:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}} {{.CPUPerc}}"], capture_output=True, text=True, timeout=20).stdout
        return {ln.split()[0]: " ".join(ln.split()[1:]) for ln in out.splitlines() if ln.strip()}
    except Exception:  # noqa: BLE001
        return {}


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--histories", type=int, default=200)
    ap.add_argument("--ingest-concurrency", type=int, default=6)
    ap.add_argument("--query-concurrency", type=int, default=4)
    ap.add_argument("--queries-per-history", type=int, default=3)
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--out", type=Path, required=True)
    a = ap.parse_args()
    a.out.mkdir(parents=True, exist_ok=True)
    run = hashlib.sha1(str(time.time()).encode()).hexdigest()[:6]
    data = json.load(open(DATA))[: a.histories]
    rows_f = open(a.out / "rows.jsonl", "a")
    rows: list[dict] = []
    ingested: list[tuple[str, str]] = []  # (org, question)
    stop = asyncio.Event()

    def rec(row: dict) -> None:
        rows.append(row)
        rows_f.write(json.dumps(row) + "\n"); rows_f.flush()

    async with httpx.AsyncClient(timeout=a.timeout) as client:
        m0 = await _metrics(client)
        t_start = time.perf_counter()
        ing_sem = asyncio.Semaphore(a.ingest_concurrency)

        async def ingest_one(n: int, q: dict) -> None:
            org = f"load-{run}-{n}"
            async with ing_sem:
                t0 = time.perf_counter()
                errors = 0
                for sid, date, sess in zip(q["haystack_session_ids"], q["haystack_dates"], q["haystack_sessions"], strict=True):
                    text = "\n".join(f"{t['role']}: {t['content']}" for t in sess)
                    body = {"event_id": f"load:{hashlib.sha256(f'{org}:{sid}'.encode()).hexdigest()[:48]}", "title": f"session {date}", "content": text,
                            "source": "load", "uri": sid, "workspace": "load", "scope": "org"}
                    t1 = time.perf_counter(); status = None; exc = None
                    for attempt in range(6):
                        try:
                            r = await client.post(f"{BASE}/api/v1/documents", json=body, headers=_hdr(org))
                            status = r.status_code
                            if status == 503 and attempt < 5:
                                await asyncio.sleep(float(r.headers.get("retry-after", "2")))
                                continue
                            break
                        except Exception as e:  # noqa: BLE001
                            exc = e
                            await asyncio.sleep(1.0)
                    ec = _err_class(exc, status)
                    errors += bool(ec)
                    rec({"kind": "ingest_doc", "org": org, "ms": int((time.perf_counter() - t1) * 1000), "status": status, "error": ec, "at": time.time()})
                rec({"kind": "ingest_history", "org": org, "ms": int((time.perf_counter() - t0) * 1000), "docs": len(q["haystack_sessions"]), "errors": errors, "at": time.time()})
                ingested.append((org, q["question"]))

        async def query_loop(i: int) -> None:
            while not stop.is_set():
                if not ingested:
                    await asyncio.sleep(1.0); continue
                org, question = random.choice(ingested)
                q = question if random.random() < 0.5 else random.choice(GENERIC)
                t1 = time.perf_counter(); status = None; exc = None; degraded = None; n = 0
                try:
                    r = await client.get(f"{BASE}/api/v1/memory/search", params={"q": q, "limit": 20, "workspace": "load", "workspace_mode": "filter", "per_document": 8}, headers=_hdr(org))
                    status = r.status_code
                    if status == 200:
                        j = r.json(); degraded = j.get("degraded"); n = len(j.get("passages") or [])
                except Exception as e:  # noqa: BLE001
                    exc = e
                rec({"kind": "search", "org": org, "ms": int((time.perf_counter() - t1) * 1000), "status": status, "error": _err_class(exc, status), "passages": n, "degraded": degraded, "at": time.time()})
                await asyncio.sleep(random.random() * 0.5)

        queriers = [asyncio.create_task(query_loop(i)) for i in range(a.query_concurrency)]
        await asyncio.gather(*(ingest_one(n, q) for n, q in enumerate(data)))
        # a settle period of queries against the full set
        settle_until = time.perf_counter() + 60
        while time.perf_counter() < settle_until:
            await asyncio.sleep(1)
        stop.set()
        await asyncio.gather(*queriers, return_exceptions=True)
        wall = time.perf_counter() - t_start
        m1 = await _metrics(client)

    by = defaultdict(list); errs = defaultdict(Counter); n_by = Counter()
    for r in rows:
        n_by[r["kind"]] += 1
        if r.get("error"):
            errs[r["kind"]][r["error"]] += 1
        else:
            by[r["kind"]].append(r["ms"])
    docs = sum(r.get("docs", 0) for r in rows if r["kind"] == "ingest_history")
    summary = {
        "histories": len(data), "docs": docs, "wall_s": round(wall, 1), "docs_per_s": round(docs / wall, 2) if wall else None,
        "ingest_concurrency": a.ingest_concurrency, "query_concurrency": a.query_concurrency,
        "latency_ms": {k: {"n": n_by[k], "p50": _pct(v, 0.5), "p95": _pct(v, 0.95), "p99": _pct(v, 0.99), "max": max(v) if v else None} for k, v in by.items()},
        "errors": {k: dict(c) for k, c in errs.items()},
        "degraded_searches": sum(1 for r in rows if r["kind"] == "search" and r.get("degraded")),
        "metrics_delta": {k: (m1.get(k), m0.get(k)) for k in sorted(set(m0) | set(m1)) if m0.get(k) != m1.get(k) and not k.startswith("memory_http_request_duration")},
        "containers": _docker_mem(), "at": datetime.now(UTC).isoformat(),
    }
    json.dump(summary, open(a.out / "summary.json", "w"), indent=1)
    lines = [f"# Load run — {summary['histories']} histories, {docs} documents, {summary['wall_s']} s ({summary['docs_per_s']} docs/s)", "",
             f"ingest concurrency {a.ingest_concurrency}, query concurrency {a.query_concurrency}", "", "| kind | n | p50 ms | p95 ms | p99 ms | max ms | errors |", "|---|---|---|---|---|---|---|"]
    for k, v in summary["latency_ms"].items():
        lines.append(f"| {k} | {v['n']} | {v['p50']} | {v['p95']} | {v['p99']} | {v['max']} | {summary['errors'].get(k) or '-'} |")
    lines += ["", f"degraded searches: {summary['degraded_searches']}", "", "containers: " + ", ".join(f"{k} {v}" for k, v in summary["containers"].items())]
    (a.out / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
