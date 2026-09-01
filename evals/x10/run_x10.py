#!/usr/bin/env python3
"""x10 runner — Claude Code with vs without memory (see PREREG.md).

    python evals/x10/run_x10.py [--arms baseline,memory] [--model haiku]

Each run: headless `claude -p` from the org code root, JSON output parsed for
usage/turns; wall time measured here; correctness graded by regex.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import yaml

X10 = Path(__file__).resolve().parent
CODE_ROOT = Path.home() / "Downloads" / "Coding" / "mlpal" / "code"
MCP_JSON = X10 / "mcp.json"

# haiku list rates ($/Mtok) for $/task; single model both arms
RATES = {"in": 1.0, "out": 5.0, "cache_read": 0.1, "cache_write": 1.25}

BASE_TOOLS = "Read,Grep,Glob,Bash(grep:*),Bash(rg:*),Bash(cat:*),Bash(find:*),Bash(ls:*)"
MEM_TOOLS = BASE_TOOLS + (
    ",mcp__memory__memory_answer,mcp__memory__memory_search,mcp__memory__memory_get"
)
MEM_HINT = (
    " Note: this organization runs MLPal Memory — the `memory` MCP tools answer "
    "questions from org experience with citations; prefer them over searching files."
)


def run_task(task: dict, arm: str, model: str) -> dict:
    prompt = task["q"] + (MEM_HINT if arm == "memory" else "")
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
           "--allowedTools", MEM_TOOLS if arm == "memory" else BASE_TOOLS]
    if arm == "memory":
        cmd += ["--mcp-config", str(MCP_JSON)]
    t0 = time.monotonic()
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=420, cwd=CODE_ROOT)
    wall = round(time.monotonic() - t0, 1)
    text, usage, turns = "", {}, None
    try:
        out = json.loads(r.stdout)
        text = out.get("result") or ""
        usage = out.get("usage") or {}
        turns = out.get("num_turns")
    except json.JSONDecodeError:
        text = r.stdout[-2000:]
    cost = round(
        (usage.get("input_tokens", 0) * RATES["in"]
         + usage.get("output_tokens", 0) * RATES["out"]
         + usage.get("cache_read_input_tokens", 0) * RATES["cache_read"]
         + usage.get("cache_creation_input_tokens", 0) * RATES["cache_write"]) / 1e6,
        4,
    )
    correct = bool(re.search(task["answer_regex"], text, re.I))
    stale_served = bool(
        task.get("stale")
        and re.search(task["stale_regex"], text, re.I)
        and not correct
    )
    return {
        "task": task["id"], "arm": arm, "wall_s": wall, "cost_usd": cost,
        "turns": turns, "correct": correct, "stale_served": stale_served,
        "answer_tail": text[-300:],
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="baseline,memory")
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--only", default=None, help="comma-separated task ids")
    args = ap.parse_args()
    MCP_JSON.write_text(json.dumps({
        "mcpServers": {"memory": {"type": "http", "url": "http://localhost:8011/mcp"}}
    }))
    tasks = yaml.safe_load((X10 / "tasks.yaml").read_text())["tasks"]
    if args.only:
        keep = set(args.only.split(","))
        tasks = [t for t in tasks if t["id"] in keep]
    rows = []
    for arm in args.arms.split(","):
        for t in tasks:
            row = run_task(t, arm, args.model)
            rows.append(row)
            mark = "✓" if row["correct"] else ("⚠STALE" if row["stale_served"] else "✗")
            print(f"  {arm:8s} {t['id']:20s} {mark:6s} {row['wall_s']:6.1f}s "
                  f"${row['cost_usd']:.4f} turns={row['turns']}")

    def med(vals):
        s = sorted(v for v in vals if v is not None)
        return s[len(s) // 2] if s else None

    summary = {"at": datetime.now(UTC).isoformat(timespec="seconds"),
               "model": args.model, "arms": {}}
    for arm in args.arms.split(","):
        a = [r for r in rows if r["arm"] == arm]
        stale_tasks = [r for r in a if any(
            t["id"] == r["task"] and t.get("stale") for t in tasks)]
        summary["arms"][arm] = {
            "correct": f"{sum(r['correct'] for r in a)}/{len(a)}",
            "stale_served": sum(r["stale_served"] for r in a),
            "median_wall_s": med([r["wall_s"] for r in a]),
            "median_cost_usd": med([r["cost_usd"] for r in a]),
            "median_turns": med([r["turns"] for r in a]),
            "stale_subgroup_correct":
                f"{sum(r['correct'] for r in stale_tasks)}/{len(stale_tasks)}",
        }
        print(f"[{arm}] {summary['arms'][arm]}")
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = X10.parent / "results" / f"{stamp}-x10-cc.json"
    out.write_text(json.dumps({**summary, "rows": rows}, indent=1))
    print(f"results → {out.relative_to(X10.parents[1])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
