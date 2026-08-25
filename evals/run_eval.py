#!/usr/bin/env python3
"""Retrieval-quality eval over the live local store (v3 task #6).

    python evals/run_eval.py                     # memory vs grep baseline, P@5/MRR
    python evals/run_eval.py --ablate workspace  # rerun memory arm without ws focus
    python evals/run_eval.py --k 10

Deterministic grading (uri/regex golds — no LLM judge). Results are appended to
evals/results/ as datestamped JSON + a markdown report; configs are stored inline so
every number is reproducible. The grep baseline scans the same source files the
collectors ingested (sessions, md/skills, repo docs) and ranks files by match count —
the "what you'd do without a memory system" control.
"""

from __future__ import annotations

import argparse
import getpass
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import httpx
import yaml

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"
CODE_ROOT = Path.home() / "Downloads" / "Coding" / "mlpal" / "code"
PROJECTS_DIR = Path.home() / ".claude" / "projects"


def _match(gold: dict, *, uri: str | None, content: str) -> bool:
    if (frag := gold.get("uri_contains")) and uri and frag in uri:
        return True
    if (rx := gold.get("content_regex")) and re.search(rx, content):
        return True
    return False


# ---------------------------------------------------------------- memory arm
def memory_arm(
    client: httpx.Client, spec: dict, k: int, ablate: set[str], legs: str | None = None
) -> dict:
    params = {"q": spec["q"], "limit": k, "origin": "direct"}
    if legs:
        params["legs"] = legs
    if spec.get("workspace") and "workspace" not in ablate:
        params["workspace"] = spec["workspace"]
    t0 = time.monotonic()
    r = client.get("/api/v1/memory/search", params=params)
    r.raise_for_status()
    took = (time.monotonic() - t0) * 1000
    passages = r.json().get("passages", [])[:k]
    hits = []
    rank = None
    for i, p in enumerate(passages, start=1):
        ok = _match(spec["gold"], uri=p.get("document_uri"), content=p["content"])
        hits.append(ok)
        if ok and rank is None:
            rank = i
    return {"hit": any(hits), "rank": rank, "returned": len(passages), "ms": round(took)}


# ---------------------------------------------------------------- grep baseline
def _corpus_files() -> list[str]:
    """EXACTLY the files the collectors ingested (from the collector state keys) — the
    baseline competes on an identical corpus, not the whole disk."""
    state_path = Path.home() / ".mlpal-memory" / "collectors.json"
    if not state_path.exists():
        return []
    keys = json.loads(state_path.read_text()).keys()
    files = []
    for key in keys:
        _, _, path = key.partition(":")
        if path.startswith("repo-card"):
            continue
        if Path(path).exists():
            files.append(path)
    return files


_CORPUS_CACHE: list[str] | None = None


def _grep_files(query: str, k: int) -> list[Path]:
    """Rank corpus files by case-insensitive match count over the query's terms
    (OR-semantics — a developer grepping a couple of keywords)."""
    global _CORPUS_CACHE
    if _CORPUS_CACHE is None:
        _CORPUS_CACHE = _corpus_files()
    terms = [t for t in re.findall(r"[a-zA-Z]{4,}", query)][:4]
    if not terms or not _CORPUS_CACHE:
        return []
    pattern = "|".join(terms)
    counts: dict[Path, int] = {}
    for batch_start in range(0, len(_CORPUS_CACHE), 200):
        batch = _CORPUS_CACHE[batch_start : batch_start + 200]
        try:
            out = subprocess.run(
                ["grep", "-Eioc", pattern, *batch],
                capture_output=True, text=True, timeout=60,
            ).stdout
        except subprocess.TimeoutExpired:
            continue
        for line in out.splitlines():
            path, _, n = line.rpartition(":")
            try:
                if int(n) > 0:
                    counts[Path(path)] = counts.get(Path(path), 0) + int(n)
            except ValueError:
                continue
    return [p for p, _ in sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:k]]


def grep_arm(spec: dict, k: int) -> dict:
    t0 = time.monotonic()
    files = _grep_files(spec["q"], k)
    took = (time.monotonic() - t0) * 1000
    rank = None
    for i, f in enumerate(files, start=1):
        try:
            content = f.read_text(errors="replace")[:200_000]
        except OSError:
            continue
        if _match(spec["gold"], uri=str(f), content=content):
            rank = i
            break
    return {"hit": rank is not None, "rank": rank, "returned": len(files), "ms": round(took)}


def mrr(ranks: list[int | None]) -> float:
    return sum(1.0 / r for r in ranks if r) / max(1, len(ranks))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--org", default="local")
    ap.add_argument("--user", default=getpass.getuser())
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--ablate", action="append", default=[], choices=["workspace"])
    ap.add_argument(
        "--legs", default=None, choices=["vector", "lexical"],
        help="single-leg baseline arm: vector-only (naive RAG) or lexical-only (FTS)",
    )
    ap.add_argument("--skip-baseline", action="store_true")
    ap.add_argument("--name", default=None, help="run label for the results file")
    args = ap.parse_args()

    goldset = yaml.safe_load((EVALS_DIR / "goldset.yaml").read_text())["queries"]
    client = httpx.Client(
        base_url=args.base_url,
        timeout=60,
        headers={"X-Test-Org-Id": args.org, "X-Test-User-Id": args.user},
    )
    ablate = set(args.ablate)
    label = args.name or (
        "memory"
        + ("".join(f"-no-{a}" for a in sorted(ablate)) or "")
        + (f"-{args.legs}-only" if args.legs else "")
    )
    # live store composition + active embedding space — recorded so every number is
    # attributable to the exact configuration that produced it
    stats = client.get("/api/v1/memory/stats").json()

    rows = []
    for spec in goldset:
        mem = memory_arm(client, spec, args.k, ablate, args.legs)
        base = None if args.skip_baseline else grep_arm(spec, args.k)
        rows.append({"id": spec["id"], "memory": mem, "grep": base})
        flag = "✓" if mem["hit"] else "✗"
        bflag = "" if base is None else (" | grep " + ("✓" if base["hit"] else "✗"))
        print(f"  {flag} {spec['id']:22s} rank={mem['rank']} {mem['ms']}ms{bflag}")

    n = len(rows)
    m_hits = sum(r["memory"]["hit"] for r in rows)
    m_mrr = mrr([r["memory"]["rank"] for r in rows])
    m_p95 = sorted(r["memory"]["ms"] for r in rows)[int(0.95 * (n - 1))]
    summary = {
        "run": label,
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "k": args.k,
        "queries": n,
        "memory": {"hit_rate": round(m_hits / n, 3), "mrr": round(m_mrr, 3), "p95_ms": m_p95},
        "config": {
            "embedder": stats.get("embedder", {}),
            "corpus": f"live store: {stats.get('documents')} docs / {stats.get('chunks')} chunks"
                      f" / {stats.get('nodes')} nodes",
            "ablations": sorted(ablate),
            "legs": args.legs or "both",
        },
        "rows": rows,
    }
    if not args.skip_baseline:
        b_hits = sum(r["grep"]["hit"] for r in rows)
        summary["grep"] = {
            "hit_rate": round(b_hits / n, 3),
            "mrr": round(mrr([r["grep"]["rank"] for r in rows]), 3),
        }

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-{label}.json"
    out.write_text(json.dumps(summary, indent=2))
    print(f"\nhit@{args.k}: memory {summary['memory']['hit_rate']:.0%}"
          + (f" vs grep {summary['grep']['hit_rate']:.0%}" if "grep" in summary else "")
          + f" · MRR {summary['memory']['mrr']:.2f} · p95 {m_p95}ms")
    print(f"results → {out.relative_to(EVALS_DIR.parent)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
