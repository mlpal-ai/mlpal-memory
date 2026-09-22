"""Markdown projection — the always-on memory tier (M7).

Renders a small, scope-resolved ``MEMORY.md`` *from* the graph: the current facts across the
caller's accessible scopes, grouped by scope, capped to a token budget so it's cheap to keep
in every model call (prompt-cache friendly). It is a **rebuildable shadow** of the database —
never a source of truth (see design-proposal §7, §14). Reads only current facts
(``invalid_at IS NULL``); time-travel stays on the retrieved tier (``as_of``).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, or_, select

from ..core.scope import Scope
from ..db.models import Edge
from ..db.scoping import scope_clause
from .resolution import RetrievalContext, accessible_scopes

CHARS_PER_TOKEN = 4  # rough, model-agnostic estimate
def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


UNPROMPTED_PER_PROJECTION = 3  # memory v10: unprompted probation learnings per session

_TRUNCATION_NOTE = "\n_(truncated to fit the context budget)_\n"


@dataclass
class Projection:
    markdown: str
    estimated_tokens: int
    fact_count: int
    truncated: bool
    node_ids: list[str] = field(default_factory=list)  # every node rendered: the run's served set


def _scope_label(edge: Edge) -> str:
    if edge.scope == Scope.GLOBAL.value:
        return "Global"
    return f"{edge.scope}:{edge.scope_id}"


_TIER_RANK = {"endorsed": 0, "corroborated": 1, "probation": 2}


async def _current_values(session, ctx: RetrievalContext, prefixes: list[str], scopes) -> list[tuple[str, str, str, str]]:
    """(anchor key, value, valid date, value node id) for every current HAS_VALUE under a Metric whose key starts
    with one of ``prefixes``, within the caller's scopes. The keyed-state read behind session start."""
    from ..db.models import Node

    if not prefixes or not scopes:
        return []

    anchors = (await session.execute(
        select(Node).where(
            or_(*[scope_clause(Node, ctx.tenant_id, s) for s in scopes]),
            Node.type == "Metric",
            or_(*[Node.key.like(pfx + "%") for pfx in prefixes]),
        )
    )).scalars().all()
    if not anchors:
        return []
    by_id = {a.id: a for a in anchors}
    rows = (await session.execute(
        select(Edge, Node)
        .join(Node, Node.id == Edge.dst_id)
        .where(Edge.src_id.in_(list(by_id)), Edge.type == "HAS_VALUE", Edge.invalid_at.is_(None),
               or_(Edge.expires_at.is_(None), Edge.expires_at > func.now()))
        .order_by(Edge.valid_at.desc())
    )).all()
    out = []
    for e, v in rows:
        a = by_id.get(e.src_id)
        out.append((a.key if a else "?", str((v.props or {}).get("value", v.name)),
                    e.valid_at.date().isoformat() if e.valid_at else "?", v.id))
    return out


async def render_projection(
    session, ctx: RetrievalContext, *, token_budget: int = 5000,
    hop: str | None = None, inject_topics: list[str] | None = None,
) -> Projection:
    """Render the always-on Markdown memory for ``ctx``, capped at ``token_budget`` tokens.

    memory v6 (§9.3 host obligation): the caller's preferences (cross-HOP and per-HOP) and the current
    values of the HOP's ``inject: true`` state topics come first and are never truncated; learnings
    follow, ordered by trust tier (endorsed, corroborated, probation) and then recency.
    """
    scopes = accessible_scopes(ctx)
    if not scopes:
        return Projection("", 0, 0, False)
    char_budget = token_budget * CHARS_PER_TOKEN - len(_TRUNCATION_NOTE)
    lead: list[str] = []
    count = 0
    served: list[str] = []
    if ctx.user_id:
        pfx = [f"pref:person/{ctx.user_id}/pref:"] + ([f"pref:person/{ctx.user_id}/pref/{hop}:"] if hop else [])
        prefs = await _current_values(session, ctx, pfx, scopes)
        if prefs:
            lead.append("\n## Preferences (yours)\n")
            for key, value, _, nid in prefs:
                pref_field = key.rsplit(":", 1)[-1]
                lead.append(f"- {pref_field}: {value}\n"); count += 1; served.append(nid)
    if inject_topics:
        me = str(ctx.user_id or "")
        if ctx.topic_grant is not None:
            inject_topics = [t for t in inject_topics if ctx.topic_grant.may_read(t.replace("{me}", me))]
        state = await _current_values(session, ctx, [f"state:{t.replace('{me}', me)}:" for t in inject_topics], scopes)
        if state:
            lead.append("\n## State (current)\n")
            for key, value, when, nid in state:
                topic_key = key.split(":", 1)[1] if ":" in key else key
                lead.append(f"- {topic_key} = {value} (as of {when})\n"); count += 1; served.append(nid)

    # memory v10: pinned facts — the owner's "always in front of me" set (a credit, a hard limit).
    # They take up to a quarter of the budget ahead of the ranked learnings and are never truncated
    # inside that slice; a pinned keyed value leaves when superseded or past its valid_until.
    from ..db.models import Node as _Node

    pinned_rows = (await session.execute(
        select(_Node).where(
            or_(*[scope_clause(_Node, ctx.tenant_id, s) for s in scopes]),
            _Node.type.in_(("MetricValue", "Fact")),
            _Node.status == "committed",
            or_(_Node.expires_at.is_(None), _Node.expires_at > func.now()),
        ).order_by(_Node.updated_at.desc())
    )).scalars().all()
    pinned_nodes = [n for n in pinned_rows if isinstance(n.props, dict) and n.props.get("pinned")
                    and not (n.props.get("valid_until") and str(n.props["valid_until"]) < _now_iso())]
    pinned_ids: set[str] = set()
    if pinned_nodes:
        slice_budget = char_budget // 4
        lines_p: list[str] = []
        used_p = 0
        for n in pinned_nodes:
            text = (n.props.get("statement") or n.props.get("value") or n.name or "").strip() if isinstance(n.props, dict) else n.name
            until = n.props.get("valid_until") if isinstance(n.props, dict) else None
            line = f"- {text}" + (f" (until {str(until)[:10]})" if until else "") + "\n"
            if used_p + len(line) > slice_budget:
                break
            lines_p.append(line); used_p += len(line); pinned_ids.add(n.id)
        if lines_p:
            lead.append("\n## Pinned\n")
            lead.extend(lines_p); count += len(lines_p); served.extend(n.id for n in pinned_nodes if n.id in pinned_ids)

    stmt = (
        select(Edge)
        .where(
            or_(*[scope_clause(Edge, ctx.tenant_id, s) for s in scopes]),
            Edge.invalid_at.is_(None),
            or_(Edge.expires_at.is_(None), Edge.expires_at > func.now()),
            Edge.fact.is_not(None),
        )
        .order_by(Edge.ingested_at.desc())
    )
    edges = (await session.execute(stmt)).scalars().all()
    # learnings ordered by trust tier (on the fact node) then recency; keyed values are rendered
    # above as state, so skip HAS_VALUE edges here
    from ..db.models import Node as _Node

    edges = [e for e in edges if e.type != "HAS_VALUE"]
    dst_ids = list({e.dst_id for e in edges})
    tiers: dict[str, str] = {}
    build: set[str] = set()
    unprompted: set[str] = set()   # memory v10: written by the engine's Stop hook, nobody asked
    foreign_unprompted: set[str] = set()
    if dst_ids:
        from ..pipeline.hop_names import hop_base

        for n in (await session.execute(select(_Node).where(_Node.id.in_(dst_ids)))).scalars().all():
            props = n.props if isinstance(n.props, dict) else {}
            t = (props.get("trust") or {}).get("tier")
            if t:
                tiers[n.id] = t
            if props.get("phase") == "build":
                build.add(n.id)   # memory v7 WP5: build memory feeds the builder's brief, not the session
            if props.get("origin") == "stop-hook" and (t or "probation") == "probation":
                # an unprompted learning on probation reaches only the person and HOP it came from
                # (blast radius = the person; grounding is the only gate) until corroboration or
                # endorsement lifts it; agreed with the engine side 2026-09-22. The person is the
                # User node behind the DECIDED edge (org-scope facts carry no owner column).
                same_hop = hop is None or hop_base(str(props.get("hop") or "").split("@", 1)[0]) == hop_base(hop)
                (unprompted if same_hop else foreign_unprompted).add(n.id)
    my_user_ids: set[str] = set()
    if unprompted and ctx.user_id:
        my_user_ids = {n.id for n in (await session.execute(
            select(_Node).where(_Node.org_id == ctx.tenant_id, _Node.type == "User", _Node.key == str(ctx.user_id))
        )).scalars().all()}
    for e in edges:
        if e.dst_id in unprompted and e.src_id not in my_user_ids:
            foreign_unprompted.add(e.dst_id)   # someone else's unprompted learning: not for this session
    unprompted -= foreign_unprompted
    edges = [e for e in edges if e.dst_id not in build and e.dst_id not in pinned_ids and e.dst_id not in foreign_unprompted]
    edges.sort(key=lambda e: (_TIER_RANK.get(tiers.get(e.dst_id, ""), 3) + (0.5 if e.dst_id in unprompted else 0),
                              -(e.ingested_at.timestamp() if e.ingested_at else 0)))
    unprompted_left = UNPROMPTED_PER_PROJECTION

    # memory v7 WP10: one identity line from auth, never from memory (design §8)
    who = f"_for {ctx.user_id}" + (f" · {ctx.tenant_id}" if ctx.tenant_id else "") + "_\n" if ctx.user_id else ""
    header = "# Memory\n" + who + "".join(lead)
    used = len(header)
    groups: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    truncated = False

    for edge in edges:
        ref = _scope_label(edge)
        fact = (edge.fact or "").strip()
        key = (ref, fact)
        if not fact or key in seen:
            continue
        if edge.dst_id in unprompted:
            if unprompted_left <= 0:
                continue
            unprompted_left -= 1
        tier = tiers.get(edge.dst_id)
        line = f"- {fact}" + (f"  ({tier})" if tier else "") + "\n"
        new_header = 0 if ref in groups else len(f"\n## {ref}\n")
        if used + len(line) + new_header > char_budget:
            truncated = True
            break
        seen.add(key)
        groups.setdefault(ref, []).append(line)
        used += len(line) + new_header
        count += 1
        served.append(edge.dst_id)

    parts = [header]
    for ref, lines in groups.items():
        parts.append(f"\n## {ref}\n")
        parts.extend(lines)
    if truncated:
        parts.append(_TRUNCATION_NOTE)
    markdown = "".join(parts)
    return Projection(markdown, max(1, len(markdown) // CHARS_PER_TOKEN), count, truncated, list(dict.fromkeys(served)))


@dataclass
class Profile:
    markdown: str
    preferences: list[dict]
    state: list[dict]
    learnings: list[dict]
    recent: list[dict]
    estimated_tokens: int


async def render_profile(session, ctx: RetrievalContext, *, hop: str | None = None,
                         learnings: int = 10, recent: int = 10) -> Profile:
    """memory v7 WP11: the person in one call. Stable facts = their preferences (cross-HOP and
    per-HOP) and every current state value in their scopes; learnings = the top facts by trust tier
    then recency; recent = the last things they wrote or read, content-free (topic, kind, when).
    The same data the projection renders, returned structured, with a Markdown view."""
    from ..db.models import Episode
    from ..db.models import Node as _Node

    scopes = accessible_scopes(ctx)
    if not scopes:
        return Profile("", [], [], [], [], 0)
    prefs_out: list[dict] = []
    state_out: list[dict] = []
    if ctx.user_id:
        pfx = [f"pref:person/{ctx.user_id}/pref:"] + ([f"pref:person/{ctx.user_id}/pref/{hop}:"] if hop else [])
        for key, value, when, nid in await _current_values(session, ctx, pfx, scopes):
            prefs_out.append({"field": key.rsplit(":", 1)[-1], "value": value, "since": when, "id": nid})
    for key, value, when, nid in await _current_values(session, ctx, ["state:"], scopes):
        topic_key = key.split(":", 1)[1] if ":" in key else key
        state_out.append({"key": topic_key, "value": value, "since": when, "id": nid})

    stmt = (
        select(Edge)
        .where(or_(*[scope_clause(Edge, ctx.tenant_id, s) for s in scopes]), Edge.invalid_at.is_(None),
               or_(Edge.expires_at.is_(None), Edge.expires_at > func.now()), Edge.fact.is_not(None), Edge.type != "HAS_VALUE")
        .order_by(Edge.ingested_at.desc()).limit(200)
    )
    edges = (await session.execute(stmt)).scalars().all()
    ids = list({e.dst_id for e in edges})
    tiers: dict[str, str] = {}
    if ids:
        for n in (await session.execute(select(_Node).where(_Node.id.in_(ids)))).scalars().all():
            t = ((n.props or {}).get("trust") or {}).get("tier") if isinstance(n.props, dict) else None
            if t:
                tiers[n.id] = t
    edges.sort(key=lambda e: (_TIER_RANK.get(tiers.get(e.dst_id, ""), 3), -(e.ingested_at.timestamp() if e.ingested_at else 0)))
    seen: set[str] = set()
    learn_out: list[dict] = []
    for e in edges:
        f = (e.fact or "").strip()
        if not f or f in seen:
            continue
        seen.add(f)
        learn_out.append({"id": e.dst_id, "fact": f, "tier": tiers.get(e.dst_id), "scope": _scope_label(e)})
        if len(learn_out) >= learnings:
            break

    recent_out: list[dict] = []
    if ctx.user_id:
        eps = (await session.execute(
            select(Episode).where(Episode.org_id == ctx.tenant_id, Episode.action_type.in_(("memory.claim", "memory.served", "memory.endorsed", "memory.retracted")))
            .order_by(Episode.occurred_at.desc()).limit(200)
        )).scalars().all()
        for ep in eps:
            actor = (ep.actor or {}).get("user_id")
            pl = ep.payload or {}
            if actor != ctx.user_id and ep.action_type != "memory.served":
                continue
            item = {"at": ep.occurred_at.isoformat() if ep.occurred_at else None, "action": ep.action_type}
            if ep.action_type == "memory.claim":
                item.update({"kind": pl.get("kind"), "topic": pl.get("topic")})
            elif ep.action_type == "memory.served":
                item.update({"tool": pl.get("tool"), "hop": pl.get("hop"), "n": len(pl.get("node_ids") or [])})
            recent_out.append(item)
            if len(recent_out) >= recent:
                break

    parts = ["# Profile\n"]
    if prefs_out:
        parts.append("\n## Preferences\n" + "".join(f"- {x['field']}: {x['value']}\n" for x in prefs_out))
    if state_out:
        parts.append("\n## State (current)\n" + "".join(f"- {x['key']} = {x['value']} (as of {x['since']})\n" for x in state_out))
    if learn_out:
        parts.append("\n## Learnings\n" + "".join(f"- {x['fact']}" + (f"  ({x['tier']})" if x["tier"] else "") + "\n" for x in learn_out))
    if recent_out:
        parts.append("\n## Recent\n" + "".join(f"- {x['at']} {x['action']} {x.get('topic') or x.get('tool') or ''}\n" for x in recent_out))
    md = "".join(parts)
    return Profile(md, prefs_out, state_out, learn_out, recent_out, max(1, len(md) // CHARS_PER_TOKEN))
