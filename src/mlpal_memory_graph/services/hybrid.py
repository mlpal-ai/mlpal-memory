"""Hybrid fusion primitives — portable (dialect-independent) so the offline suite covers them.

Reciprocal Rank Fusion (RRF) combines the vector and lexical legs without needing comparable
score scales, then a node-distance boost lifts facts that sit graph-close to the caller's anchor
nodes (their USER node + activated subject nodes). The read path is LLM-free: this is all
deterministic ranking math. See PR3 / design-proposal §4.
"""

from __future__ import annotations

from collections.abc import Iterable

RRF_K = 60  # standard RRF constant; dampens the weight of any single leg's top ranks


def rrf_fuse(
    ranked_lists: Iterable[list[str]],
    k: int = RRF_K,
    weights: list[float] | None = None,
) -> dict[str, float]:
    """Reciprocal Rank Fusion over several ranked id-lists (each best→worst).

    score(id) = Σ_leg w_leg / (k + rank), rank 1-based. An id missing from a leg simply
    contributes nothing for that leg. ``weights`` (default all 1.0) lets a caller damp a
    leg whose signal is known-weak — e.g. the offline dev-hash embedder's vector leg,
    which must not dilute a strong lexical leg 1:1. Returns {id: fused_score}.
    """
    scores: dict[str, float] = {}
    lists = list(ranked_lists)
    w = list(weights) if weights is not None else [1.0] * len(lists)
    for weight, ranked in zip(w, lists, strict=True):
        for rank, node_id in enumerate(ranked, start=1):
            scores[node_id] = scores.get(node_id, 0.0) + weight / (k + rank)
    return scores


def workspace_boost(
    scores: dict[str, float],
    node_workspaces: dict[str, str | None],
    workspace: str | None,
    *,
    beta: float = 0.4,
) -> dict[str, float]:
    """Lift fused scores for memories learned in the caller's active workspace.

    factor = 1 + beta for an exact facet match; unfaceted or other-workspace memories keep
    their score (cross-workspace knowledge stays reachable — focus, not a filter).
    """
    if not workspace:
        return scores
    return {
        nid: s * (1.0 + beta) if node_workspaces.get(nid) == workspace else s
        for nid, s in scores.items()
    }


def distance_boost(
    scores: dict[str, float], distances: dict[str, int], *, beta: float = 0.5
) -> dict[str, float]:
    """Lift fused scores for ids graph-close to a caller anchor node.

    factor = 1 + beta/(1 + distance); an id with no recorded distance (unreachable within the
    traversal bound) keeps its score unchanged. Monotonic in closeness, bounded by (1 + beta).
    """
    out: dict[str, float] = {}
    for node_id, s in scores.items():
        d = distances.get(node_id)
        out[node_id] = s * (1.0 + beta / (1 + d)) if d is not None else s
    return out
