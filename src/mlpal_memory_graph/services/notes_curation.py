"""memory v10: nightly curation of the workspace notes — deterministic, no model.

The design gave markdown three roles; the workspace note is the curated projection the runs
update. In practice the runs append and nobody curates: on 2026-09-22 the infra note's Decisions
was empty and Open threads still carried run pointers from 09-04. Two rules, both derivable from
the graph without a model:

- **Open threads:** a bullet is closed (removed, with a ledger line) when every citation it carries
  is stale (a superseded edge, a missing node), or when its only pointer is a run report
  (`memory://hop-run:` / `memory://hop-run/`) older than `thread_ttl_days`. A bullet with no citation
  is a person's line and is left alone.
- **Now:** a `Current state` block, rebuilt each night from the current values of the HOP's
  `inject: true` state topics for the note's workspace (the registry says which). The block is
  bounded by markers so the person's own lines in Now survive.

Decisions and Preferences are never touched by this job: they are a person's.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from ..core.logging import get_logger
from ..db.models import Edge, MemoryHop, Node, Note
from .notes import SECTIONS, parse_sections, put_note, render_body, stale_citations

log = get_logger(__name__)

STATE_BEGIN = "<!-- current-state: rebuilt nightly by the memory service; edit the lines above -->"
STATE_END = "<!-- /current-state -->"
_RUN_POINTER = re.compile(r"memory://hop-run[:/]([^\s\]\)]+)")
_CITATION = re.compile(r"memory://(node|edge)/([0-9a-fA-F-]{8,})")


@dataclass
class CurationResult:
    workspace: str
    closed_threads: list[str]
    state_lines: int
    changed: bool


def close_stale_threads(section: str, stale: set[str], *, now: datetime, ttl_days: int,
                        run_dates: dict[str, datetime | None] | None = None) -> tuple[str, list[str]]:
    """Drop the bullets whose citations are all stale, or whose only memory pointers are run
    reports that are dangling (no document) or older than the ttl. `run_dates` maps a run slug to
    its document's date, None for a dangling pointer. Returns (new text, closed bullets)."""
    kept: list[str] = []
    closed: list[str] = []
    cutoff = now - timedelta(days=ttl_days)
    run_dates = run_dates or {}
    for line in (section or "").splitlines():
        s = line.strip()
        if not s.startswith("-"):
            kept.append(line)
            continue
        cites = [f"memory://{k}/{i}" for k, i in _CITATION.findall(s)]
        runs = _RUN_POINTER.findall(s)
        if cites and all(c in stale for c in cites):
            closed.append(s)
            continue
        if runs and not cites:
            dates = [run_dates.get(r, None) for r in runs]
            if all(d is None or d < cutoff for d in dates):
                closed.append(s)
                continue
        kept.append(line)
    return "\n".join(kept).strip("\n"), closed


async def _run_dates(session, org_id: str | None, body: str) -> dict[str, datetime | None]:
    """Resolve `memory://hop-run:<slug>` pointers to their run documents (source hop_runs, title
    `… <slug>` or uri == slug): slug → document date, None when no document exists."""
    from ..db.models import Episode

    slugs = sorted(set(_RUN_POINTER.findall(body or "")))
    if not slugs:
        return {}
    rows = (await session.execute(
        select(Episode.occurred_at, Episode.payload).where(Episode.org_id == org_id, Episode.source == "hop_runs", Episode.action_type == "document.ingested")
    )).all()
    out: dict[str, datetime | None] = {}
    for slug in slugs:
        found = None
        for occurred, payload in rows:
            pl = payload or {}
            title, uri = str(pl.get("title") or ""), str(pl.get("uri") or "")
            if uri == slug or title.endswith(" " + slug) or title == slug:
                if occurred is not None and (found is None or occurred > found):
                    found = occurred if occurred.tzinfo else occurred.replace(tzinfo=UTC)
        out[slug] = found
    return out


def rebuild_now(section: str, state_lines: list[str]) -> str:
    """Replace (or append) the bounded current-state block; everything outside it stays."""
    text = section or ""
    if STATE_BEGIN in text and STATE_END in text:
        head, rest = text.split(STATE_BEGIN, 1)
        _, tail = rest.split(STATE_END, 1)
        outside = (head.rstrip("\n") + ("\n" + tail.lstrip("\n") if tail.strip() else "")).strip("\n")
    else:
        outside = text.strip("\n")
    if not state_lines:
        return outside
    block = "\n".join([STATE_BEGIN, *state_lines, STATE_END])
    return f"{outside}\n{block}" if outside else block


async def _inject_topics(session, org_id: str | None, workspace: str) -> list[str]:
    hops = (await session.execute(select(MemoryHop).where(MemoryHop.org_id == org_id))).scalars().all()
    topics: list[str] = []
    for h in hops:
        if (h.workspace or "") != workspace:
            continue
        for t in (h.contract or {}).get("topics") or []:
            if isinstance(t, dict) and t.get("inject") and t.get("kind") == "state" and t.get("id") not in topics:
                topics.append(str(t["id"]))
    return topics


async def _current_state_lines(session, org_id: str | None, topics: list[str], limit: int = 12) -> list[str]:
    """`- topic/key = value (as of date)` for every live value under the injected topics."""
    if not topics:
        return []
    anchors = (await session.execute(
        select(Node).where(Node.org_id == org_id, Node.type == "Metric", Node.key.like("state:%"))
    )).scalars().all()
    # one line per topic: a date-keyed topic (cost-daily by date) has one anchor per key, and the
    # current state is the newest of them, not the whole history
    newest: dict[str, tuple[datetime | None, str]] = {}
    for a in anchors:
        topic = (a.props or {}).get("topic") if isinstance(a.props, dict) else None
        if not topic or not any(topic == t or (t.endswith("/{target}") and topic.startswith(t[: -len("{target}")])) for t in topics):
            continue
        live = (await session.execute(
            select(Edge).where(Edge.src_id == a.id, Edge.type == "HAS_VALUE", Edge.invalid_at.is_(None)).order_by(Edge.ingested_at.desc())
        )).scalars().first()
        if live is None:
            continue
        v = await session.get(Node, live.dst_id)
        if v is None or v.status != "committed":
            continue
        when = live.valid_at or live.ingested_at
        when = (when if when is None or when.tzinfo else when.replace(tzinfo=UTC))
        key = (a.props or {}).get("claim_key") if isinstance(a.props, dict) else None
        value = (v.props or {}).get("value") if isinstance(v.props, dict) else v.name
        line = f"- {topic}/{key} = {str(value)[:160]}" + (f" (as of {when.date().isoformat()})" if when else "")
        # a wildcard topic (watch/{target}) keeps one line per target; a keyed-by-date topic keeps the newest
        group = f"{topic}/{key}" if any(t.endswith("/{target}") and topic.startswith(t[: -len("{target}")]) for t in topics) else topic
        prev = newest.get(group)
        if prev is None or (when is not None and (prev[0] is None or when > prev[0])):
            newest[group] = (when, line)
    return sorted(line for _, line in newest.values())[:limit]


async def curate_workspace_notes(session, *, org_id: str | None, now: datetime | None = None, thread_ttl_days: int = 14,
                                 updated_by: str = "memory-curation") -> list[CurationResult]:
    """Curate every org-scope workspace note in the tenant. Idempotent: a second run on the same
    graph changes nothing."""
    now = now or datetime.now(UTC)
    notes = (await session.execute(select(Note).where(Note.org_id == org_id, Note.scope == "org"))).scalars().all()
    out: list[CurationResult] = []
    for note in notes:
        body = note.body or ""
        if not body.strip():
            continue
        sections = parse_sections(body)
        stale = {s["citation"] for s in await stale_citations(session, org_id, body)}
        run_dates = await _run_dates(session, org_id, sections.get("Open threads", ""))
        threads, closed = close_stale_threads(sections.get("Open threads", ""), stale, now=now, ttl_days=thread_ttl_days, run_dates=run_dates)
        topics = await _inject_topics(session, org_id, note.workspace or "")
        state_lines = await _current_state_lines(session, org_id, topics)
        new_now = rebuild_now(sections.get("Now", ""), state_lines)
        new_sections = {s: sections.get(s, "") for s in SECTIONS}
        new_sections["Open threads"] = threads
        new_sections["Now"] = new_now
        new_body = render_body(new_sections)
        changed = new_body.strip() != render_body({s: sections.get(s, "") for s in SECTIONS}).strip()
        if changed:
            reason = f"nightly curation: {len(closed)} thread(s) closed, current state {len(state_lines)} line(s)"
            await put_note(session, org_id=org_id, scope=note.scope, scope_id=note.scope_id, workspace=note.workspace or "",
                           body=new_body, updated_by=updated_by, reason=reason, base_version=note.version)
            log.info("notes.curated", workspace=note.workspace, closed=len(closed), state_lines=len(state_lines))
        out.append(CurationResult(workspace=note.workspace or "", closed_threads=closed, state_lines=len(state_lines), changed=changed))
    return out
