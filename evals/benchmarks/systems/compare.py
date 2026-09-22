"""Side-by-side comparison of head-to-head runs (memory v8).

    python evals/benchmarks/systems/compare.py RUN_DIR [RUN_DIR ...] [--md OUT.md]

Each RUN_DIR is a harness output directory (`summary.json`, `rows.jsonl`). Rows are joined on
question id, so only questions every run answered are compared (the intersection is reported).
Adds one metric the harness does not compute: `gold_hit@k`, whether any returned item's text
contains the gold answer (LEARNINGS L8: session-level recall is 1.0 for a rewritten item that lost
the fact). Cost per correct answer combines the system's own model usage (ingest + search) with
the shared reader/judge spend prorated per question.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

SYSTEM_PRICE = {"llm_in": 1.0, "llm_out": 5.0, "embed": 0.02}  # USD per million tokens: Haiku 4.5, text-embedding-3-small


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def gold_hit(row: dict) -> bool | None:
    gold = row.get("gold")
    items = row.get("items")
    if not gold or items is None:
        return None
    g = _norm(str(gold))
    if not g:
        return None
    # short numeric / date golds: a token match on the normalised text is what the reader gets to see
    return any(g in _norm(i.get("text", "")) for i in items)


def load(run_dir: Path) -> tuple[dict, dict[str, dict]]:
    summary = json.loads((run_dir / "summary.json").read_text())
    rows = {}
    for line in (run_dir / "rows.jsonl").read_text().splitlines():
        if line.strip():
            r = json.loads(line)
            rows[r["id"]] = r
    return summary, rows


def _usd(u: dict) -> float:
    return (u.get("llm_input_tokens", 0) / 1e6 * SYSTEM_PRICE["llm_in"] + u.get("llm_output_tokens", 0) / 1e6 * SYSTEM_PRICE["llm_out"]
            + u.get("embed_tokens", 0) / 1e6 * SYSTEM_PRICE["embed"])


def _mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 3) if xs else None


def _pct(xs, p):
    xs = sorted(x for x in xs if x is not None)
    return xs[min(len(xs) - 1, int(p * len(xs)))] if xs else None


def compare(run_dirs: list[Path]) -> dict:
    runs = [(d, *load(d)) for d in run_dirs]
    common = set.intersection(*(set(rows) for _, _, rows in runs))
    out = {"n_common": len(common), "systems": {}, "per_question": {}}
    for d, summary, rows in runs:
        rs = [rows[i] for i in sorted(common)]
        ok = [r for r in rs if r.get("correct") is not None]
        by_type = defaultdict(list)
        for r in ok:
            by_type[r["type"]].append(r)
        # the system's own spend: each haystack's ingest counted once, plus every search
        seen, ingest_usd, search_usd = set(), 0.0, 0.0
        for r in rs:
            org = r.get("org")
            if org not in seen and isinstance(r.get("ingest"), dict) and "usage" in r["ingest"]:
                seen.add(org)
                ingest_usd += _usd(r["ingest"]["usage"])
            search_usd += _usd(r.get("search_usage") or {})
        shared_per_q = (summary.get("cost_usd_estimate") or 0) / max(1, summary.get("n") or 1)
        correct = sum(1 for r in ok if r["correct"])
        total_usd = ingest_usd + search_usd + shared_per_q * len(rs)
        name = summary.get("system") or "ours-raw"
        out["systems"][name] = {
            "run": d.name,
            "accuracy": round(correct / len(ok), 3) if ok else None,
            "correct": correct, "scored": len(ok),
            "by_type": {t: {"n": len(g), "accuracy": round(sum(1 for r in g if r["correct"]) / len(g), 3)} for t, g in sorted(by_type.items())},
            "recall@15_session": _mean([r.get("recall@15") for r in ok]),
            "gold_hit@k": _mean([gold_hit(r) for r in ok]),
            "items_returned_mean": _mean([len(r.get("items") or []) for r in ok]),
            "context_tokens_mean": _mean([r.get("context_tokens") for r in ok]),
            "search_ms_p50": _pct([r.get("search_ms") for r in ok], 0.5),
            "search_ms_p95": _pct([r.get("search_ms") for r in ok], 0.95),
            "ingest_ms_per_haystack": _mean([r["ingest"].get("ms") for r in rs if isinstance(r.get("ingest"), dict) and r.get("org") in seen]) if seen else None,
            "items_created": sum(int(r["ingest"].get("items_created") or 0) for r in rs if isinstance(r.get("ingest"), dict) and "usage" in r["ingest"]),
            "ingest_errors": sum(int(r["ingest"].get("errors") or 0) for r in rs if isinstance(r.get("ingest"), dict)),
            "usd_system_ingest": round(ingest_usd, 4), "usd_system_search": round(search_usd, 4),
            "usd_shared_reader_judge": round(shared_per_q * len(rs), 4),
            "usd_per_correct": round(total_usd / correct, 4) if correct else None,
            "usd_per_1k_questions": round(total_usd / max(1, len(rs)) * 1000, 2),
        }
        for r in rs:
            out["per_question"].setdefault(r["id"], {"type": r["type"], "question": r["question"], "gold": r.get("gold")})[name] = {
                "correct": r.get("correct"), "recall@15": r.get("recall@15"), "gold_hit": gold_hit(r), "answer": (r.get("short_answer") or r.get("response") or "")[:120]}
    return out


def markdown(cmp: dict) -> str:
    names = list(cmp["systems"])
    lines = [f"Common questions: {cmp['n_common']}", "", "| metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for key in ("accuracy", "recall@15_session", "gold_hit@k", "items_returned_mean", "context_tokens_mean", "search_ms_p50", "search_ms_p95",
                "ingest_ms_per_haystack", "items_created", "ingest_errors", "usd_system_ingest", "usd_system_search", "usd_shared_reader_judge",
                "usd_per_correct", "usd_per_1k_questions"):
        lines.append(f"| {key} | " + " | ".join(str(cmp["systems"][n].get(key)) for n in names) + " |")
    types = sorted({t for n in names for t in cmp["systems"][n]["by_type"]})
    lines += ["", "| type | n | " + " | ".join(names) + " |", "|---|---|" + "---|" * len(names)]
    for t in types:
        n0 = next((cmp["systems"][n]["by_type"][t]["n"] for n in names if t in cmp["systems"][n]["by_type"]), "")
        lines.append(f"| {t} | {n0} | " + " | ".join(str(cmp["systems"][n]["by_type"].get(t, {}).get("accuracy")) for n in names) + " |")
    # where they differ: questions one system got and another did not
    lines += ["", "Disagreements (correct by system):", ""]
    for qid, q in cmp["per_question"].items():
        verdicts = {n: q.get(n, {}).get("correct") for n in names}
        if len({v for v in verdicts.values()}) > 1:
            lines.append(f"- `{qid}` [{q['type']}] " + ", ".join(f"{n}={'✓' if v else '✗'}" for n, v in verdicts.items()) + f" — {q['question'][:90]}")
    return "\n".join(lines) + "\n"


def detail(run_dirs: list[Path], qid: str, max_items: int = 12) -> str:
    """One question across runs: what each system returned, for reading traces side by side."""
    out = []
    for d in run_dirs:
        summary, rows = load(d)
        r = rows.get(qid)
        if r is None:
            continue
        name = summary.get("system") or "ours-raw"
        out.append(f"### {name} — {'✓' if r.get('correct') else '✗'}  answer: {(r.get('short_answer') or r.get('response') or '')[:200]}")
        out.append(f"question: {r['question']}\ngold: {r.get('gold')}  recall@15={r.get('recall@15')}  gold_hit={gold_hit(r)}  context_tokens={r.get('context_tokens')}")
        for i, it in enumerate((r.get("items") or [])[:max_items]):
            out.append(f"  {i + 1:2}. [{it.get('kind')}] {(it.get('when') or '')} {(it.get('source_id') or '')[:18]} | {it.get('text', '')[:220].replace(chr(10), ' ')}")
        if len(r.get("items") or []) > max_items:
            out.append(f"  … {len(r['items']) - max_items} more")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_dirs", nargs="+", type=Path)
    ap.add_argument("--md", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--detail", metavar="QID", help="print what every run returned for one question")
    ap.add_argument("--disagreements", action="store_true", help="print the detail view for every question the runs disagree on")
    a = ap.parse_args()
    if a.detail:
        sys.stdout.write(detail(a.run_dirs, a.detail))
        return 0
    cmp = compare(a.run_dirs)
    if a.disagreements:
        names = list(cmp["systems"])
        for qid, q in cmp["per_question"].items():
            if len({q.get(n, {}).get("correct") for n in names}) > 1:
                sys.stdout.write(detail(a.run_dirs, qid) + "\n---\n")
        return 0
    md = markdown(cmp)
    if a.md:
        a.md.write_text(md)
    if a.json:
        a.json.write_text(json.dumps(cmp, indent=1))
    sys.stdout.write(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
