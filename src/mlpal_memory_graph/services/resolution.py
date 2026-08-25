"""Read-time scope resolution.

The caller's :class:`RetrievalContext` is turned into the ordered set of
:class:`~mlpal_memory_graph.core.scope.ScopeRef` they may read from. This is the one place
that decides *which* layers a query spans; the driver then applies them as a hard predicate.

M1 derives the accessible set structurally (own user + teams + org + global). Access-policy
gating (RBAC/ABAC, who may address which scope) is layered on in M4, and cross-scope
merge/rank/override + ``explain`` in M3 — both build on this function.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ..core.scope import Scope, ScopeRef
from ..graph import ScoredNode

_MIN_DT = datetime.min.replace(tzinfo=UTC)  # tie-break floor for rows lacking created_at


@dataclass(frozen=True)
class RetrievalContext:
    tenant_id: str | None  # org_id — the hard tenant boundary
    user_id: str | None = None
    team_ids: tuple[str, ...] = ()
    # arbitrary subject scopes the caller has activated (repo/service/agent/…). This is what
    # lets memory compound per repo/service/agent, not only per person. See design-proposal §1.
    subjects: tuple[ScopeRef, ...] = ()
    use_case: str | None = None
    include_global: bool = True
    # v3: the active workspace facet ("me, in repo X") — focuses ranking inside the
    # personal store (facet-matching memories get a boost); never an authz filter.
    workspace: str | None = None


def accessible_scopes(ctx: RetrievalContext) -> list[ScopeRef]:
    """The scopes ``ctx`` may read, unioned and ordered narrowest → broadest.

    Narrowest-first lets the merge step keep the closest-scope copy of a duplicated fact
    (override semantics) without re-sorting. Subject scopes (repo/service/agent) coexist —
    different subjects hold different facts and simply union. See design-proposal §1.2.
    """
    scopes: list[ScopeRef] = []
    if ctx.user_id:
        scopes.append(ScopeRef(Scope.USER, ctx.user_id))
    scopes.extend(ctx.subjects)
    scopes.extend(ScopeRef(Scope.TEAM, t) for t in ctx.team_ids)
    if ctx.tenant_id:
        scopes.append(ScopeRef(Scope.ORG, ctx.tenant_id))
    if ctx.include_global:
        scopes.append(ScopeRef.global_())
    # de-dupe while preserving order, then sort narrowest-first by rank
    seen: set[ScopeRef] = set()
    unique = [s for s in scopes if not (s in seen or seen.add(s))]
    unique.sort(key=lambda s: s.scope.rank, reverse=True)
    return unique


def narrow_to(scopes: list[ScopeRef], requested: Scope | None) -> list[ScopeRef]:
    """Intersect accessible scopes with an optional single-layer request filter."""
    if requested is None:
        return scopes
    return [s for s in scopes if s.scope is requested]


# ── Merge / rank across scopes (design-proposal §1.2) ───────────────────────


@dataclass
class MergedNode:
    """One canonical fact after cross-scope merge. ``node`` is the narrowest-scope copy
    (override semantics); ``also_known_at`` lists the broader scopes it shadowed."""

    node: object  # db.models.Node — kept loose to avoid an import cycle
    score: float
    also_known_at: list[ScopeRef] = field(default_factory=list)
    # v3: a live CONTRADICTS edge touches this node — surface the disagreement instead of
    # asserting false confidence. The consuming agent/UI decides how to present it.
    contested: bool = False

    @property
    def scope_ref(self) -> ScopeRef:
        if self.node.scope == Scope.GLOBAL.value:
            return ScopeRef.global_()
        return ScopeRef(Scope(self.node.scope), self.node.scope_id)


@dataclass
class ResolutionTrace:
    """Why the resolver returned what it did — backs the ``explain`` endpoint."""

    accessible: list[ScopeRef]
    requested_scope: Scope | None
    per_scope_hits: dict[str, int]
    candidates: int
    merged: int
    shadowed: list[dict] = field(default_factory=list)


def _node_scope_ref(node) -> ScopeRef:
    if node.scope == Scope.GLOBAL.value:
        return ScopeRef.global_()
    return ScopeRef(Scope(node.scope), node.scope_id)


def merge_scored(
    scored: list[ScoredNode],
    accessible: list[ScopeRef],
    requested_scope: Scope | None,
) -> tuple[list[MergedNode], ResolutionTrace]:
    """Union → dedupe-by-canonical-entity → narrowest-wins → rank.

    Duplicate ``(type, key)`` across scopes collapse to the narrowest-scope copy; the
    broader copies become ``also_known_at`` provenance. Ranking is score-desc, tie-broken
    by scope narrowness then recency. (Salience weighting is added with the LLM extractor
    in M8 — see §1.2.)
    """
    per_scope_hits: dict[str, int] = {}
    groups: dict[tuple[str, str], list[ScoredNode]] = {}
    for s in scored:
        per_scope_hits[str(_node_scope_ref(s.node))] = (
            per_scope_hits.get(str(_node_scope_ref(s.node)), 0) + 1
        )
        groups.setdefault((s.node.type, s.node.key), []).append(s)

    merged: list[MergedNode] = []
    shadowed: list[dict] = []
    for members in groups.values():
        # narrowest scope wins as the displayed copy
        members.sort(key=lambda s: s.node.scope and Scope(s.node.scope).rank, reverse=True)
        winner = members[0]
        others = [_node_scope_ref(m.node) for m in members[1:]]
        merged.append(
            MergedNode(
                node=winner.node,
                score=max(m.score for m in members),
                also_known_at=others,
            )
        )
        if others:
            shadowed.append(
                {
                    "type": winner.node.type,
                    "key": winner.node.key,
                    "displayed_scope": str(_node_scope_ref(winner.node)),
                    "shadowed_scopes": [str(o) for o in others],
                }
            )

    merged.sort(
        key=lambda m: (m.score, Scope(m.node.scope).rank, m.node.created_at or _MIN_DT),
        reverse=True,
    )
    trace = ResolutionTrace(
        accessible=accessible,
        requested_scope=requested_scope,
        per_scope_hits=per_scope_hits,
        candidates=len(scored),
        merged=len(merged),
        shadowed=shadowed,
    )
    return merged, trace
