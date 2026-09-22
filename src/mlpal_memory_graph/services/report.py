"""The owner's memory report (memory v6 DESIGN §10): what a customer sees about one HOP's memory over a
window. Every number is a count over the ledgers (episodes, nodes, edges) with its condition; cost is
tokens here (the engine's catalog prices them at the edge). Deterministic, no model calls.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..db.models import Edge, Episode, Node

def _utc(dt):
    """Ledger timestamps may come back naive from SQLite in tests; treat naive as UTC."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


PASS = {"success"}
EDGE = {"needs_approval"}
FAIL = {"error", "max_turns", "cancelled"}


import re as _re

_DATE_KEY = _re.compile(r"^\d{4}-\d{2}-\d{2}$")
MEMORY_TOOL_PREFIXES = ("mcp__memory__",)
MEMORY_TOOL_NAMES = frozenset({"Memorize", "Digest"})   # the harness's own memory write and the hash helper: not estate reads


def _is_memory_tool(name: str) -> bool:
    return name in MEMORY_TOOL_NAMES or name.startswith(MEMORY_TOOL_PREFIXES)


async def build_report(session, org: str, *, window_days: int = 7, hop: str | None = None,
                       stale_after_days: float = 2.0, hop_aliases: dict[str, str] | None = None) -> dict:
    since = datetime.now(UTC) - timedelta(days=window_days)
    now = datetime.now(UTC)
    eps = (await session.execute(select(Episode).where(Episode.org_id == org, Episode.occurred_at >= since))).scalars().all()

    def hop_name(p: dict) -> str | None:
        n = (p.get("hop") or {}).get("name") if isinstance(p.get("hop"), dict) else (p.get("hop").split("@")[0] if isinstance(p.get("hop"), str) else None)
        return (hop_aliases or {}).get(n, n)

    runs = [e for e in eps if e.action_type == "run.completed" and (e.payload or {}).get("role") in (None, "main")
            and (hop is None or hop_name(e.payload or {}) == hop)]
    run_ids = {(e.payload or {}).get("run_id") for e in runs if (e.payload or {}).get("run_id")}
    results = Counter((e.payload or {}).get("run_result") for e in runs)
    tokens: dict[str, dict[str, int]] = defaultdict(lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0})
    for e in runs:
        p = e.payload or {}; t = p.get("tokens") or {}
        m = tokens[str(p.get("model") or "?")]
        m["input"] += int(t.get("input") or 0); m["output"] += int(t.get("output") or 0)
        m["cache_read"] += int(t.get("cache_read_input") or 0); m["cache_write"] += int(t.get("cache_creation_input") or 0)

    served = [e for e in eps if e.action_type == "memory.served"]
    served_runs = {(e.payload or {}).get("run_id") for e in served}
    runs_with_memory = len(run_ids & served_runs)
    # memory v7: contribution as the design meant it — a run that read memory (a served row, an
    # injected projection, or local topics) and touched no estate tool. Needs d11.7 tool_calls;
    # runs without it are "unknown", never counted either way.
    memory_only = 0; with_tool_calls = 0
    for e in runs:
        p = e.payload or {}
        tc = p.get("tool_calls")
        if not isinstance(tc, dict):
            continue
        with_tool_calls += 1
        read_memory = p.get("run_id") in served_runs or bool(p.get("memory_projection")) or bool(p.get("memories_injected"))
        live = [t for t, n in tc.items() if n and not _is_memory_tool(t)]
        if read_memory and not live:
            memory_only += 1
    claims = [e for e in eps if e.action_type == "memory.claim"]
    deviations = [e for e in eps if (e.action_type == "deviation") or (e.action_type == "memory.claim" and (e.payload or {}).get("kind") == "deviation")]
    corrections = [e for e in deviations if str((e.payload or {}).get("value") or e.content or "").lstrip().startswith("kind: correction")]   # content capture is off by default; the claim value carries the grammar
    outcomes = [e for e in claims if (e.payload or {}).get("kind") == "outcome"]
    pii_drops = [e for e in eps if (e.dropped_reason or "").startswith("pii:")]
    forgotten = [e for e in eps if e.action_type in ("memory.forgotten", "memory.workspace_purged")]

    anchors = (await session.execute(select(Node).where(Node.org_id == org, Node.type == "Metric", Node.key.like("state:%")))).scalars().all()
    fresh = []
    if anchors:
        # memory v6 WP12 (§2, §7): a state value is stale when older than two cadences of its own
        # key, the cadence being the median gap between its successive values; a key seen once
        # falls back to `stale_after_days`. A host-stamped `half_life` like '2x' sets the multiple.
        from statistics import median

        MIN_CADENCE_DAYS = 1 / 24

        from ..core.halflife import parse_half_life

        hist = (await session.execute(
            select(Edge).where(Edge.src_id.in_([a.id for a in anchors]), Edge.type == "HAS_VALUE")
        )).scalars().all()
        by_anchor: dict[str, list] = {}
        for e in hist:
            by_anchor.setdefault(e.src_id, []).append(e)
        # memory v7 WP3: a date-keyed topic (cost-daily by date) is one stream, not one key per day;
        # only its newest key is judged for freshness, older dates are history, not stale state.
        newest_by_topic: dict[str, str] = {}
        for a in anchors:
            ap = a.props or {}
            ck = str(ap.get("claim_key") or "")
            if _DATE_KEY.match(ck) and ap.get("topic"):
                if ck > newest_by_topic.get(ap["topic"], ""):
                    newest_by_topic[ap["topic"]] = ck
        anchors = [a for a in anchors
                   if not (_DATE_KEY.match(str((a.props or {}).get("claim_key") or "")) and (a.props or {}).get("topic")
                           and newest_by_topic.get((a.props or {})["topic"]) != str((a.props or {}).get("claim_key")))]
        for a in anchors:
            edges = sorted(by_anchor.get(a.id, []), key=lambda e: _utc(e.valid_at or e.ingested_at) or now)
            current = [e for e in edges if e.invalid_at is None]
            if not current:
                continue
            stamp = _utc(current[-1].valid_at or current[-1].ingested_at)
            age_days = (now - stamp).total_seconds() / 86400 if stamp else None
            stamps = [_utc(e.valid_at or e.ingested_at) for e in edges]
            # a burst (retries, a probe written twice in a minute) is not a cadence: gaps under an
            # hour are ignored; a key with only such gaps is treated as seen once
            gaps = [g for g in ((b - x).total_seconds() / 86400 for x, b in zip(stamps, stamps[1:], strict=False) if x and b and b > x) if g >= MIN_CADENCE_DAYS]
            cadence = median(gaps) if gaps else None
            hl = parse_half_life((a.props or {}).get("half_life")) if isinstance(a.props, dict) else None
            multiple = float(hl[:-1]) if isinstance(hl, str) else 2.0
            threshold = cadence * multiple if cadence else (hl if isinstance(hl, float) else stale_after_days)
            fresh.append({"key": a.key, "age_days": round(age_days, 2) if age_days is not None else None,
                          "cadence_days": round(cadence, 2) if cadence else None,
                          "stale_after": round(threshold, 2),
                          "rule": f"{multiple:g}x cadence" if cadence else ("half_life" if isinstance(hl, float) else "default"),
                          "stale": bool(age_days is not None and age_days > threshold)})
    scored = (await session.execute(select(Node).where(Node.org_id == org, Node.type.in_(("Fact", "MetricValue"))))).scalars().all()
    tiers = Counter(((n.props or {}).get("trust") or {}).get("tier", "untiered") if isinstance(n.props, dict) else "untiered" for n in scored)
    grounded = sum(1 for n in scored if isinstance(n.props, dict) and n.props.get("grounded"))
    by_type = Counter(n.type for n in (await session.execute(select(Node).where(Node.org_id == org))).scalars().all())
    per100 = lambda n: round(100.0 * n / len(runs), 1) if runs else None
    return {
        "org": org, "hop": hop, "window_days": window_days, "generated_at": now.isoformat(),
        "runs": {"total": len(runs), "by_result": dict(results), "edge_stops_per_100": per100(sum(v for k, v in results.items() if k in EDGE)),
                 "errors_per_100": per100(sum(v for k, v in results.items() if k in FAIL))},
        "cost": {"tokens_by_model": dict(tokens)},
        "contribution": {"runs_with_memory_read": runs_with_memory, "share": (round(runs_with_memory / len(run_ids), 3) if run_ids else None),
                         "served_events": len(served),
                         "answered_without_live_read": memory_only, "runs_with_tool_calls": with_tool_calls,
                         "answered_share": (round(memory_only / with_tool_calls, 3) if with_tool_calls else None)},
        "quality": {"deviations": len(deviations), "deviations_per_100": per100(len(deviations)),
                    "corrections": len(corrections), "corrections_per_100": per100(len(corrections)), "outcomes": len(outcomes)},
        "freshness": {"state_values": len(fresh), "stale": sum(1 for f in fresh if f["stale"]), "stale_after_days": stale_after_days,
                      "items": sorted(fresh, key=lambda f: -(f["age_days"] or 0))[:20]},
        "trust": {"scored": len(scored), "tiers": dict(tiers), "grounded": grounded},
        "governance": {"pii_drops": len(pii_drops), "forget_actions": len(forgotten), "claims": len(claims),
                       "endorsements": sum(1 for e in eps if e.action_type == "memory.endorsed" and not (e.payload or {}).get("withdraw"))},
        "storage": {"nodes_by_type": dict(by_type), "episodes_in_window": len(eps)},
    }


def render_markdown(r: dict) -> str:
    runs = r["runs"]; c = r["contribution"]; q = r["quality"]; f = r["freshness"]; t = r["trust"]; g = r["governance"]
    lines = [f"# Memory report — {r['hop'] or r['org']} — last {r['window_days']} days", "",
             f"Runs: {runs['total']} ({', '.join(f'{k} {v}' for k, v in sorted(runs['by_result'].items()))}); edge stops per 100: {runs['edge_stops_per_100']}; errors per 100: {runs['errors_per_100']}",
             f"Memory contribution: {c['runs_with_memory_read']} runs read memory ({c['share']}), {c['served_events']} reads logged; "
             f"answered without a live read: {c['answered_without_live_read']} of {c['runs_with_tool_calls']} runs with tool counts ({c['answered_share']})",
             f"Quality: deviations {q['deviations']} ({q['deviations_per_100']} per 100 runs), corrections {q['corrections']}, outcomes {q['outcomes']}",
             f"Freshness: {f['state_values']} state values, {f['stale']} stale (two observed cadences of each key; > {f['stale_after_days']} d when seen once)",
             f"Trust: {t['scored']} scored facts, tiers {t['tiers']}, grounded {t['grounded']}",
             f"Governance: {g['pii_drops']} PII drops, {g['forget_actions']} forget actions, {g['claims']} claims written", ""]
    if f["items"]:
        lines.append("| state | age (days) | cadence (days) | stale after | stale |"); lines.append("|---|---|---|---|---|")
        for it in f["items"][:10]:
            lines.append(f"| {it['key']} | {it['age_days']} | {it.get('cadence_days') or ''} | {it.get('stale_after')} ({it.get('rule')}) | {'yes' if it['stale'] else ''} |")
        lines.append("")
    lines.append("Tokens by model: " + ", ".join(f"{m}: in {v['input']:,} / out {v['output']:,} / cache {v['cache_read']:,}" for m, v in r["cost"]["tokens_by_model"].items()))
    return "\n".join(lines) + "\n"
