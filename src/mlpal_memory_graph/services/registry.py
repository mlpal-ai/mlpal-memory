"""HOP registry, routing across HOP memories, the builder's brief, and per-tenant ontology
extensions (memory v7 WP5/WP6/WP7/WP8).

Registration is idempotent: the engine (or a deploy step) PUTs the HOP's memory block; the service
keeps owner, mode, sharing, tenant and the contract. Routing ranks every registered topic of the
tenant against a question; the brief is what a builder turn reads before step one: build-phase
topics, the HOP's version scores from the eval ledger, deviation counts, company facts for the
workspace, and fleet facts when present.
"""

from __future__ import annotations

import re
from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..db.models import Edge, Episode, MemoryHop, Node
from ..ontology.core import NODE_CLASSES
from ..pipeline.hop_names import hop_base

_TOK = re.compile(r"[a-z0-9]{2,}")


def _tokens(s: str) -> set[str]:
    return set(_TOK.findall((s or "").lower().replace("{", " ").replace("}", " ")))


async def register_hop(session, *, org_id: str | None, user_id: str | None, name: str, version: str | None,
                       owner: str | None, mode: str | None, sharing: str | None, tenant: str | None, workspace: str | None,
                       topics: list[dict], reads: list[str], writes: list[str], ontology: list[dict]) -> MemoryHop:
    row = (await session.execute(select(MemoryHop).where(MemoryHop.org_id == org_id, MemoryHop.name == name))).scalars().first()
    if row is None:
        row = MemoryHop(org_id=org_id, name=name, registered_by=user_id)
        session.add(row)
    row.version = version or row.version
    row.owner_user_id = owner or row.owner_user_id or user_id
    if mode:
        row.mode = mode
    if sharing:
        row.sharing = sharing
    row.tenant = tenant or row.tenant
    row.workspace = workspace or row.workspace
    row.contract = {"topics": topics, "reads": reads, "writes": writes, "ontology": ontology}
    await session.flush()
    return row


async def list_hops(session, org_id: str | None) -> list[MemoryHop]:
    return list((await session.execute(select(MemoryHop).where(MemoryHop.org_id == org_id).order_by(MemoryHop.name))).scalars().all())


async def extension_classes(session, org_id: str | None) -> dict[str, dict]:
    """The tenant's ontology extension: the union over its registered HOPs, name -> {parent, hop}."""
    out: dict[str, dict] = {}
    for h in await list_hops(session, org_id):
        for cls in (h.contract or {}).get("ontology") or []:
            name = cls.get("name")
            if name and name not in NODE_CLASSES:
                out[name] = {"parent": cls.get("parent"), "hop": h.name, "description": cls.get("description")}
    return out


async def route(session, org_id: str | None, question: str, limit: int = 5) -> list[dict]:
    """Which topics, of which HOPs, could answer this question: token overlap between the question
    and each topic id, its kind and its owning HOP's workspace; state topics get a small lift because
    a question about "now" is usually a keyed read."""
    qt = _tokens(question)
    if not qt:
        return []
    # the keys a topic actually holds are its best description: "web/hop-sandbox" under
    # infra/state/watch/{target} is what makes "is web healthy" route to that topic
    anchors = (await session.execute(select(Node).where(Node.org_id == org_id, Node.type == "Metric"))).scalars().all()
    key_tokens: dict[str, set[str]] = {}
    for a in anchors:
        topic = (a.props or {}).get("topic")
        if topic:
            key_tokens.setdefault(topic, set()).update(_tokens(str((a.props or {}).get("claim_key") or "")) | _tokens(a.name or ""))
    out = []
    for h in await list_hops(session, org_id):
        for t in (h.contract or {}).get("topics") or []:
            tid = t.get("id") or ""
            tt = _tokens(tid) | _tokens(t.get("kind") or "") | _tokens(h.workspace or "")
            pattern_prefix = tid.split("{", 1)[0]
            held = set()
            for topic, toks in key_tokens.items():
                if topic == tid or (pattern_prefix and topic.startswith(pattern_prefix)):
                    held |= toks
            hit = len(qt & tt) + len(qt & held)
            if hit:
                score = hit + (0.5 if t.get("kind") == "state" else 0.0)
                out.append({"hop": h.name, "topic": tid, "kind": t.get("kind"), "read": t.get("read"), "score": score,
                            "phase": t.get("phase") or "deploy", "matched_keys": sorted(qt & held)[:5]})
    out.sort(key=lambda x: -x["score"])
    return out[:limit]


async def brief(session, *, org_id: str | None, hop: str, window_days: int = 30) -> dict:
    """The builder's brief before step one (design: the build-side half of the retune brief)."""
    since = datetime.now(UTC) - timedelta(days=window_days)
    row = (await session.execute(select(MemoryHop).where(MemoryHop.org_id == org_id, MemoryHop.name == hop))).scalars().first()
    contract = (row.contract if row else {}) or {}
    build_topics = [t["id"] for t in contract.get("topics") or [] if (t.get("phase") or "deploy") == "build"]
    # build-phase memory: current keyed values and learnings under the build topics
    build_state: list[dict] = []
    build_learnings: list[dict] = []
    prefixes = [f"state:{t.replace('{me}', '')}" for t in build_topics if t]
    if prefixes:
        anchors = (await session.execute(select(Node).where(Node.org_id == org_id, Node.type == "Metric"))).scalars().all()
        anchors = [a for a in anchors if any(str(a.key).startswith(p.rstrip("*")) for p in prefixes)]
        if anchors:
            rows = (await session.execute(
                select(Edge, Node).join(Node, Node.id == Edge.dst_id)
                .where(Edge.src_id.in_([a.id for a in anchors]), Edge.type == "HAS_VALUE", Edge.invalid_at.is_(None))
            )).all()
            by = {a.id: a for a in anchors}
            for e, v in rows:
                a = by[e.src_id]
                build_state.append({"topic": (a.props or {}).get("topic"), "key": (a.props or {}).get("claim_key"),
                                    "value": (v.props or {}).get("value", v.name), "since": e.valid_at.isoformat() if e.valid_at else None})
    facts = (await session.execute(select(Node).where(Node.org_id == org_id, Node.type == "Fact"))).scalars().all()
    for f in facts:
        p = f.props or {}
        if p.get("phase") == "build" or (p.get("topic") or "").startswith(tuple(t.rstrip("*") for t in build_topics)):
            build_learnings.append({"id": f.id, "fact": p.get("statement") or f.name, "tier": (p.get("trust") or {}).get("tier")})
    # version scores from the eval ledger and deviation counts from the run ledger
    eps = (await session.execute(select(Episode).where(Episode.org_id == org_id, Episode.occurred_at >= since,
                                                       Episode.action_type.in_(("hop.eval_scored", "memory.claim", "deviation", "run.completed"))))).scalars().all()
    scores = []
    devs: Counter = Counter()
    runs: Counter = Counter()
    for e in eps:
        p = e.payload or {}
        if e.action_type == "hop.eval_scored" and (p.get("hop") or {}).get("name") == hop:
            scores.append({"version": p.get("to_version"), "score": (p.get("eval") or {}).get("score"), "runs": (p.get("eval") or {}).get("runs"),
                           "decision": p.get("decision"), "at": e.occurred_at.isoformat() if e.occurred_at else None})
        elif e.action_type == "run.completed" and hop_base((p.get("hop") or {}).get("name")) == hop_base(hop):
            runs[p.get("run_result")] += 1
        elif (e.action_type == "deviation") or (e.action_type == "memory.claim" and p.get("kind") == "deviation"):
            first = str(p.get("value") or e.content or "").strip().splitlines()[:1]
            kind = first[0].replace("kind:", "").strip() if first and first[0].startswith("kind:") else "unknown"
            devs[kind] += 1
    scores.sort(key=lambda s: s["at"] or "", reverse=True)
    # company facts for the HOP's workspace (shared, not build-phase) and fleet facts (global scope)
    ws = row.workspace if row else None
    company = [{"id": f.id, "fact": (f.props or {}).get("statement") or f.name} for f in facts
               if f.scope == "org" and (f.props or {}).get("phase") != "build" and (ws is None or f.workspace in (ws, None))][:15]
    fleet = (await session.execute(select(Node).where(Node.scope == "global", Node.type == "Fact"))).scalars().all()
    fleet_out = [{"fact": (f.props or {}).get("statement") or f.name} for f in fleet if hop in ((f.props or {}).get("hops") or [hop])][:10]
    md = [f"# Builder brief — {hop}\n"]
    if row:
        md.append(f"owner {row.owner_user_id or '?'} · mode {row.mode} · sharing {row.sharing} · version {row.version or '?'}\n")
    if scores:
        md.append("\n## Versions scored\n" + "".join(f"- {s['version']}: score {s['score']} over {s['runs']} runs → {s['decision'] or 'pending'}\n" for s in scores[:8]))
    if devs:
        md.append("\n## Deviations (window)\n" + "".join(f"- {k}: {n}\n" for k, n in devs.most_common()))
    if runs:
        md.append("\n## Runs (window)\n" + "".join(f"- {k}: {n}\n" for k, n in runs.most_common()))
    if build_state:
        md.append("\n## Build state\n" + "".join(f"- {x['topic']}/{x['key']} = {x['value']}\n" for x in build_state))
    if build_learnings:
        md.append("\n## What earlier builds learned\n" + "".join(f"- {x['fact']}" + (f"  ({x['tier']})" if x['tier'] else "") + "\n" for x in build_learnings[:20]))
    if company:
        md.append("\n## What the company already knows (workspace)\n" + "".join(f"- {x['fact']}\n" for x in company))
    if fleet_out:
        md.append("\n## Fleet (de-identified, all companies)\n" + "".join(f"- {x['fact']}\n" for x in fleet_out))
    return {"hop": hop, "registry": ({"owner": row.owner_user_id, "mode": row.mode, "sharing": row.sharing, "tenant": row.tenant, "version": row.version} if row else None),
            "build_topics": build_topics, "build_state": build_state, "build_learnings": build_learnings, "scores": scores,
            "deviations": dict(devs), "runs": dict(runs), "company": company, "fleet": fleet_out, "markdown": "".join(md)}
