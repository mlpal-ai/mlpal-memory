"""LLM contradiction judge (PR5, D1) — for NON-functional, overlapping edges only.

Functional + same-src + diff-dst + overlapping validity already auto-invalidates deterministically
(driver.invalidate_superseded). For the non-functional/ambiguous cases, after a new edge is written
we fetch fact_embedding-similar candidates (same scope + same src + same relation type + different
object + overlapping validity), ask the judge whether they contradict, and on "yes" invalidate the
OLDER edge at the newer's valid_at — append-only (invalidate-not-delete). Every decision is logged
for replay. Offline/dev uses a deterministic negation heuristic so the unit suite needs no network.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import UTC

from ..core.config import get_settings
from ..core.logging import get_logger
from ..core.temporal import windows_overlap
from ..services.llm_client import LLMClient, get_llm_client

log = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")
_NEGATION = frozenset(
    {"not", "no", "never", "stopped", "deprecated", "removed", "former", "ended", "cancelled"}
)


def _utc(dt):
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


class ContradictionJudge(ABC):
    @abstractmethod
    async def contradicts(self, fact_a: str, fact_b: str) -> bool:
        """Whether the two facts make incompatible claims about the same thing."""


class DevContradictionJudge(ContradictionJudge):
    """Deterministic offline judge: two facts contradict when they share >=2 content tokens AND
    exactly one carries a negation marker (e.g. 'runs on aws' vs 'no longer runs on aws')."""

    async def contradicts(self, fact_a: str, fact_b: str) -> bool:
        ta, tb = set(_TOKEN.findall(fact_a.lower())), set(_TOKEN.findall(fact_b.lower()))
        if len(ta & tb) < 2:
            return False
        return bool(ta & _NEGATION) != bool(tb & _NEGATION)


class GatewayContradictionJudge(ContradictionJudge):
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def contradicts(self, fact_a: str, fact_b: str) -> bool:
        system = (
            "You decide if two facts contradict — i.e. cannot both be true of the same subject at "
            "the same time. Reassertions or unrelated facts are NOT contradictions."
        )
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["contradicts"],
            "properties": {"contradicts": {"type": "boolean"}},
        }
        out = await self.client.complete_json(
            system=system, user=f"A: {fact_a}\nB: {fact_b}", schema=schema, max_tokens=10
        )
        return bool(out.get("contradicts"))


def get_judge() -> ContradictionJudge:
    s = get_settings()
    if s.dev_auth or s.environment in ("local", "test"):
        return DevContradictionJudge()
    return GatewayContradictionJudge(get_llm_client())


async def judge_and_invalidate(
    session,
    driver,
    judge: ContradictionJudge,
    *,
    tenant_id,
    scope,
    new_edge,
    fact_embedding,
    similarity_threshold: float = 0.6,
    limit: int = 5,
) -> int:
    """Find contradictions of ``new_edge`` among similar edges and close the older one. Returns the
    number of edges invalidated."""
    if not new_edge.fact or fact_embedding is None:
        return 0
    candidates = await driver.search_fact_edges(
        session, tenant_id=tenant_id, scopes=[scope], query_embedding=fact_embedding, limit=limit
    )
    invalidated = 0
    nv = _utc(new_edge.valid_at)
    for cand, score in candidates:
        if (
            cand.id == new_edge.id
            or score < similarity_threshold
            or cand.src_id != new_edge.src_id  # same subject
            or cand.type != new_edge.type  # same relation
            or cand.dst_id == new_edge.dst_id  # diff object (same = reassertion)
        ):
            continue
        if not windows_overlap(
            _utc(cand.valid_at), _utc(cand.invalid_at), nv, _utc(new_edge.invalid_at)
        ):
            continue
        if not await judge.contradicts(cand.fact or "", new_edge.fact):
            continue
        # close the OLDER edge at the newer's valid_at (backfill-safe); newer stays open.
        older, at = (cand, nv) if _utc(cand.valid_at) <= nv else (new_edge, _utc(cand.valid_at))
        invalidated += await driver.invalidate_edge(
            session,
            tenant_id=tenant_id,
            scope=scope,
            type_=older.type,
            src_id=older.src_id,
            dst_id=older.dst_id,
            at=at,
        )
        log.info(
            "contradiction.invalidated",
            kept=new_edge.id if older is cand else cand.id,
            closed=older.id,
            score=round(float(score), 3),
            judge=type(judge).__name__,
        )
    return invalidated
