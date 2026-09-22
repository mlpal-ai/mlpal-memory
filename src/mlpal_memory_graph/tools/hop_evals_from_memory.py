"""hop_evals_from_memory — draft candidate eval scenarios from deviation memories (hop-v1.1 §9.2).

Reads the org's ``harness_memory/deviation`` episodes for one HOP in a window and writes one
scenario skeleton per deviation under ``--out`` (``evals/candidates/<slug>/scenario.yaml``). The
skeleton carries the deviation verbatim (expected / observed / cause / action) and a task_class
guess; the prompt, fixture and oracle are the builder's to author, and a person reviews the case
before it becomes golden. Idempotent: an existing candidate dir is left untouched.

    uv run python -m mlpal_memory_graph.tools.hop_evals_from_memory --org <org> --hop infra --out <dir>
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import select

from ..db import get_session_factory
from ..db.models import Episode

# a deviation kind says what kind of case would reproduce it
TASK_CLASS_FOR_KIND = {
    "refusal": "refuse",
    "escalation": "refuse",
    "unmodelled": "diagnose",
    "surprise": "diagnose",
    "verifier_fail": "diagnose",
    "correction": "ask",
}

_SLUG = re.compile(r"[^a-z0-9-]+")


def _slug(text: str) -> str:
    return _SLUG.sub("-", text.lower()).strip("-")[:64] or "deviation"


def draft_candidates(payloads: list[dict], out: Path) -> list[Path]:
    """Pure: one skeleton per payload; returns the paths written (existing dirs are skipped)."""
    written: list[Path] = []
    out.mkdir(parents=True, exist_ok=True)
    for p in payloads:
        kind = str(p.get("kind") or "unknown")
        slug = _slug(str(p.get("slug") or f"dev-{kind}"))
        case_dir = out / slug
        if case_dir.exists():
            continue
        case_dir.mkdir(parents=True)
        seed = int(hashlib.sha256(slug.encode()).hexdigest()[:6], 16) % 100000
        doc = {
            "id": f"cand-{slug}",
            "seed": seed,
            "family": "candidates",
            "task_class": TASK_CLASS_FOR_KIND.get(kind, "diagnose"),
            "tier": 0,
            "environment": {"kind": "snapshot", "fixture": "fixture.json"},
            "prompt": "TODO: the task that produced this deviation, as the person asked it",
            "oracle": {"TODO": f"the outcome the deviation says was expected: {p.get('expected') or '?'}"},
            "safety": {"forbidden_call_classes": ["mutative", "unknown"]},
            "verifier_version": 1,
            "deviation": {
                "kind": kind,
                "expected": p.get("expected"),
                "observed": p.get("observed"),
                "cause": p.get("cause"),
                "action": p.get("action"),
                "run": p.get("run"),
                "hop": p.get("hop"),
                "origin": p.get("origin"),
                "memory": f"memory://episode/{p.get('event_id')}" if p.get("event_id") else None,
            },
        }
        header = (f"# candidate drafted from a deviation memory ({kind}); a person reviews this before it is golden.\n"
                  f"# Author: prompt, fixture.json (record the reads), oracle. Then reference + mutant must pass/fail.\n")
        (case_dir / "scenario.yaml").write_text(header + yaml.safe_dump(doc, sort_keys=False, width=100))
        written.append(case_dir / "scenario.yaml")
    return written


async def _mine(org: str, hop: str, window_days: int, out: Path) -> None:
    factory = get_session_factory()
    since = datetime.now(UTC) - timedelta(days=window_days)
    async with factory() as session:
        rows = (
            await session.execute(
                select(Episode)
                .where(
                    Episode.org_id == org,
                    Episode.source == "harness_memory",
                    Episode.action_type == "deviation",
                    Episode.occurred_at >= since,
                )
                .order_by(Episode.occurred_at)
            )
        ).scalars().all()
    payloads = [dict(r.payload, event_id=r.event_id) for r in rows
                if str(r.payload.get("hop") or "").split("@", 1)[0] == hop]
    written = draft_candidates(payloads, out)
    print(f"{len(payloads)} deviation memories for hop={hop} in {window_days}d; {len(written)} new candidate(s) under {out}")
    for w in written:
        print(f"  {w}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True)
    ap.add_argument("--hop", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--out", required=True, help="candidates dir, e.g. <hop repo>/evals/candidates")
    a = ap.parse_args()
    asyncio.run(_mine(a.org, a.hop, a.window_days, Path(a.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
