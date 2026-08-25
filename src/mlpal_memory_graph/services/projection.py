"""Markdown projection — the always-on memory tier (M7).

Renders a small, scope-resolved ``MEMORY.md`` *from* the graph: the current facts across the
caller's accessible scopes, grouped by scope, capped to a token budget so it's cheap to keep
in every model call (prompt-cache friendly). It is a **rebuildable shadow** of the database —
never a source of truth (see design-proposal §7, §14). Reads only current facts
(``invalid_at IS NULL``); time-travel stays on the retrieved tier (``as_of``).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import or_, select

from ..core.scope import Scope
from ..db.models import Edge
from ..db.scoping import scope_clause
from .resolution import RetrievalContext, accessible_scopes

CHARS_PER_TOKEN = 4  # rough, model-agnostic estimate
_TRUNCATION_NOTE = "\n_(truncated to fit the context budget)_\n"


@dataclass
class Projection:
    markdown: str
    estimated_tokens: int
    fact_count: int
    truncated: bool


def _scope_label(edge: Edge) -> str:
    if edge.scope == Scope.GLOBAL.value:
        return "Global"
    return f"{edge.scope}:{edge.scope_id}"


async def render_projection(
    session, ctx: RetrievalContext, *, token_budget: int = 5000
) -> Projection:
    """Render the always-on Markdown memory for ``ctx``, capped at ``token_budget`` tokens."""
    scopes = accessible_scopes(ctx)
    if not scopes:
        return Projection("", 0, 0, False)

    stmt = (
        select(Edge)
        .where(
            or_(*[scope_clause(Edge, ctx.tenant_id, s) for s in scopes]),
            Edge.invalid_at.is_(None),
            Edge.fact.is_not(None),
        )
        .order_by(Edge.ingested_at.desc())
    )
    edges = (await session.execute(stmt)).scalars().all()

    # reserve room for the truncation note so the final doc never exceeds the budget
    char_budget = token_budget * CHARS_PER_TOKEN - len(_TRUNCATION_NOTE)
    header = "# Memory\n"
    used = len(header)
    groups: dict[str, list[str]] = {}
    seen: set[tuple[str, str]] = set()
    count = 0
    truncated = False

    for edge in edges:
        ref = _scope_label(edge)
        fact = (edge.fact or "").strip()
        key = (ref, fact)
        if not fact or key in seen:
            continue
        line = f"- {fact}\n"
        new_header = 0 if ref in groups else len(f"\n## {ref}\n")
        if used + len(line) + new_header > char_budget:
            truncated = True
            break
        seen.add(key)
        groups.setdefault(ref, []).append(line)
        used += len(line) + new_header
        count += 1

    parts = [header]
    for ref, lines in groups.items():
        parts.append(f"\n## {ref}\n")
        parts.extend(lines)
    if truncated:
        parts.append(_TRUNCATION_NOTE)
    markdown = "".join(parts)
    return Projection(markdown, max(1, len(markdown) // CHARS_PER_TOKEN), count, truncated)
