"""Memory packets — the answer format of the memory system (v3, task #5).

A packet is a *markdown document* built deterministically (no LLM) from a Resolution,
following llms.txt conventions (H1 topic, blockquote TL;DR, H2 sections, ``[Title](uri):
description`` link lines) so it reads well for humans AND parses trivially for agents:

    # <query>
    > TL;DR — best-supported answer line.
    ## Facts        — derived assertions with scope/workspace/validity/support labels
    ## Evidence     — verbatim passages with memory:// citations, freshness-ranked
    ## Contested    — disagreements, both sides, never silently resolved
    ## Gaps         — what memory does NOT know (explicit abstention beats hallucination)
    ## Freshness    — source-age summary so the reader can judge staleness

Ranking inside a packet is the v3 ranking model: retrieval score × recency decay
(half-life ``RECENCY_HALF_LIFE_DAYS`` on the source's valid-time) with re-observation
count as a tiebreak. Outdated sources therefore sink but stay reachable; as-of queries
bypass decay entirely (the past is exactly as true as it was).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

RECENCY_HALF_LIFE_DAYS = 180.0
RECENCY_FLOOR = 0.35  # old-but-relevant knowledge never decays to invisibility
MAX_FACTS = 8
MAX_PASSAGES = 5
EXCERPT_CHARS = 320


def _as_utc(dt) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


def recency_factor(valid_at, now: datetime) -> float:
    """exp2(-age/half_life), floored — recent knowledge outranks stale at equal relevance."""
    valid_at = _as_utc(valid_at)
    if valid_at is None:
        return 1.0
    age_days = max(0.0, (now - valid_at).total_seconds() / 86400.0)
    return max(RECENCY_FLOOR, math.pow(2.0, -age_days / RECENCY_HALF_LIFE_DAYS))


def _date(dt) -> str:
    dt = _as_utc(dt)
    return dt.date().isoformat() if dt else "undated"


def _fact_line(m) -> str:
    node = m.node
    labels: list[str] = [f"{node.scope}:{node.scope_id}" if node.scope_id else node.scope]
    if node.workspace:
        labels.append(f"ws:{node.workspace}")
    if (node.observed_count or 1) > 1:
        labels.append(f"observed ×{node.observed_count}")
    if node.status != "committed":
        labels.append(node.status)
    if m.contested:
        labels.append("⚠ contested")
    label_str = " · ".join(labels)
    summary = f" — {node.summary}" if node.summary else ""
    return f"- [{node.name}](memory://node/{node.id}): {label_str}{summary}"


FAILED_SOURCE_SUFFIX = "_failed"
FAILED_EVIDENCE_FACTOR = 0.6  # verified-run evidence outranks failed-run at equal relevance


def _from_failed_run(chunk) -> bool:
    return bool(chunk.source) and chunk.source.endswith(FAILED_SOURCE_SUFFIX)


def _passage_block(hit, doc_meta: dict) -> str:
    chunk = hit.chunk
    meta = doc_meta.get(chunk.document_id, {})
    title = meta.get("title") or chunk.source or "passage"
    when = _date(meta.get("valid_at"))
    excerpt = " ".join(chunk.content.split())
    if len(excerpt) > EXCERPT_CHARS:
        excerpt = excerpt[:EXCERPT_CHARS].rsplit(" ", 1)[0] + " …"
    block = f'> "{excerpt}"\n> — [{title}](memory://chunk/{chunk.id}), {when}'
    if _from_failed_run(chunk):
        # x3 finding 4: a failed run's confident narrative ("Found it…") outpersuades a
        # terse correct fact. Every tier reaching the context window carries outcome
        # provenance — evidence included.
        block += "\n> ⚠ from a FAILED attempt — its conclusions were NOT verified."
    return block


def build_packet(
    *,
    query: str,
    resolution,
    doc_meta: dict,
    now: datetime | None = None,
    as_of: datetime | None = None,
    workspace: str | None = None,
    agent_mode: bool = False,
) -> tuple[str, dict]:
    """Assemble the markdown packet + a structured summary (for the JSON envelope).

    ``doc_meta`` maps document_id -> {title, valid_at, uri} for the passages' parents.
    """
    now = now or datetime.now(UTC)

    # rank facts: retrieval score × observed-count tiebreak (facts carry no valid_at;
    # their edges do — supersession already removed the outdated ones).
    # x2 finding 3 (evidence pack §4b): insights distilled from FAILED runs are
    # hypotheses, not knowledge — memory amplifies mistakes at recall speed if they
    # present as Facts. They get their own labeled section and never lead the packet.
    ranked_nodes = sorted(
        resolution.nodes,
        key=lambda m: (m.score, m.node.observed_count or 1),
        reverse=True,
    )
    unverified = [
        m for m in ranked_nodes
        if (m.node.props or {}).get("hypothesis_from_failed_attempt")
    ][:MAX_FACTS]
    facts = [
        m for m in ranked_nodes
        if not (m.node.props or {}).get("hypothesis_from_failed_attempt")
    ][:MAX_FACTS]

    # rank passages: retrieval score × recency decay on the parent document's valid time.
    # As-of queries skip decay — point-in-time truth is not "stale".
    def _p_score(hit) -> float:
        base = hit.score if as_of is not None else hit.score * recency_factor(
            doc_meta.get(hit.chunk.document_id, {}).get("valid_at"), now
        )
        return base * (FAILED_EVIDENCE_FACTOR if _from_failed_run(hit.chunk) else 1.0)

    all_passages = sorted(resolution.passages, key=_p_score, reverse=True)
    if agent_mode:
        # x3 finding 5: models mine packets for prior-confirmation — labels don't stop
        # them quoting a failed attempt's confident narrative. Agent-mode packets
        # SUPPRESS failed-run content: no unverified hypotheses, no failed-run
        # evidence; negative knowledge appears only as one-line constraints.
        all_passages = [h for h in all_passages if not _from_failed_run(h.chunk)]
    passages = all_passages[:MAX_PASSAGES]
    contested = [m for m in facts if m.contested]

    lines: list[str] = [f"# {query}"]
    scope_note = f" · workspace `{workspace}`" if workspace else ""
    asof_note = f" · as of {_date(as_of)}" if as_of else ""

    if facts:
        top = facts[0].node
        lines.append(f"> {top.name}{'. ' + top.summary if top.summary else ''}")
    elif passages:
        first = " ".join(passages[0].chunk.content.split())[:160]
        lines.append(f"> {first} …")
    else:
        lines.append("> Memory holds no relevant knowledge for this query.")
    lines.append(f"_{len(facts)} facts · {len(passages)} passages{scope_note}{asof_note}_")

    if facts:
        lines.append("\n## Facts")
        lines.extend(_fact_line(m) for m in facts)

    if passages:
        lines.append("\n## Evidence")
        lines.extend(_passage_block(h, doc_meta) for h in passages)

    if contested:
        lines.append("\n## Contested")
        lines.append(
            "The following results have live disagreements — do not present either side "
            "as settled:"
        )
        lines.extend(f"- [{m.node.name}](memory://node/{m.node.id})" for m in contested)

    if unverified and agent_mode:
        lines.append("\n## Ruled out (do not pursue)")
        lines.append(
            "Prior attempts pursued these directions and FAILED verification — treat "
            "each as a dead end unless you have new evidence:"
        )
        lines.extend(f"- {m.node.name}" for m in unverified)
    elif unverified:
        lines.append("\n## Prior attempts (unverified)")
        lines.append(
            "A previous attempt produced these hypotheses but DID NOT verify them "
            "(the run did not pass). Treat as leads to check, not knowledge:"
        )
        for m in unverified:
            rr = (m.node.props or {}).get("run_result", "failed")
            lines.append(
                f"- [{m.node.name}](memory://node/{m.node.id}): from a `{rr}` run"
            )

    gaps: list[str] = []
    if not facts and not passages:
        gaps.append("No stored knowledge matched — answer from first principles and say so.")
    elif not facts:
        gaps.append("No structured facts — only verbatim passages; treat as unvetted context.")
    if gaps:
        lines.append("\n## Gaps")
        lines.extend(f"- {g}" for g in gaps)

    dates = [
        _as_utc(doc_meta.get(h.chunk.document_id, {}).get("valid_at"))
        for h in passages
    ]
    dates = [d for d in dates if d]
    if dates:
        lines.append("\n## Freshness")
        lines.append(
            f"- Evidence spans {_date(min(dates))} → {_date(max(dates))}; "
            f"ranking prefers recent sources (half-life {int(RECENCY_HALF_LIFE_DAYS)}d)."
        )

    summary = {
        "facts": len(facts),
        "passages": len(passages),
        "contested": len(contested),
        "unverified": len(unverified),
        "gaps": gaps,
        "top_fact_id": facts[0].node.id if facts else None,
    }
    return "\n".join(lines), summary
