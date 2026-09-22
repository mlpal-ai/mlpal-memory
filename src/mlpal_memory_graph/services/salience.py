"""Salience admission for documents (memory v7 WP4; design §9 "admit by salience, never by size").

A document is admitted when its salience clears the policy floor and its source has budget left
for the day. The score has the three terms the design named:

    recency          how recently the content was written (valid time), exp(-age / 365 d)
    distinctiveness  the share of the document's vocabulary the tenant has never stored:
                     sampled terms with zero document frequency in the direct tier
    budget           a per-source daily cap, so a noisy connector cannot flood the tenant

The score is deterministic and model-free. A declined document is recorded with the reason
(`salience:<score><<floor>` or `budget:<source>`), never dropped silently.
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from ..db.models import Chunk, Document

_TERM = re.compile(r"[a-zA-Z][a-zA-Z0-9_-]{3,}")
_STOP = frozenset("""this that with from have were been they their there which would about into
also than then them these those such other more most some what when where will your just like
over only very much many each while after before because could should might shall being does
""".split())
SAMPLE_TERMS = 24


def terms_of(text: str) -> list[str]:
    seen: dict[str, int] = {}
    for t in _TERM.findall(text.lower()):
        if t in _STOP:
            continue
        seen[t] = seen.get(t, 0) + 1
    # the document's own most frequent terms describe it best; ties by first appearance
    return [t for t, _ in sorted(seen.items(), key=lambda kv: -kv[1])]


def recency(valid_at: datetime | None, now: datetime | None = None) -> float:
    if valid_at is None:
        return 0.5   # unknown age: neither fresh nor old
    at = valid_at if valid_at.tzinfo else valid_at.replace(tzinfo=UTC)
    age_days = max(0.0, ((now or datetime.now(UTC)) - at).total_seconds() / 86400)
    return math.exp(-age_days / 365.0)


async def distinctiveness(session, tenant_id: str | None, text: str, sample: int = SAMPLE_TERMS) -> float:
    """Share of the document's leading terms that no stored chunk of this tenant contains."""
    terms = terms_of(text)[:sample]
    if not terms:
        return 0.0
    novel = 0
    for t in terms:
        n = (await session.execute(
            select(func.count()).select_from(Chunk).where(Chunk.org_id == tenant_id, Chunk.content.ilike(f"%{t}%")).limit(1)
        )).scalar() or 0
        if n == 0:
            novel += 1
    return novel / len(terms)


async def admitted_today(session, tenant_id: str | None, source: str | None) -> int:
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return (await session.execute(
        select(func.count()).select_from(Document).where(Document.org_id == tenant_id, Document.source == source, Document.ingested_at >= start)
    )).scalar() or 0


async def salience(session, tenant_id: str | None, *, text: str, valid_at: datetime | None) -> dict:
    r = recency(valid_at)
    d = await distinctiveness(session, tenant_id, text)
    return {"recency": round(r, 3), "distinctiveness": round(d, 3), "score": round(0.5 * r + 0.5 * d, 3)}


async def admission_reason(session, tenant_id: str | None, *, source: str | None, text: str, valid_at: datetime | None,
                           min_salience: float | None, budget_per_day: int | None) -> tuple[str | None, dict]:
    """(reason to decline or None, the score record). Budget is checked first: it is the cheaper test."""
    if budget_per_day is not None:
        n = await admitted_today(session, tenant_id, source)
        if n >= budget_per_day:
            return f"budget:{source}:{n}/{budget_per_day}", {"admitted_today": n, "budget": budget_per_day}
    if min_salience is None:
        return None, {}
    rec = await salience(session, tenant_id, text=text, valid_at=valid_at)
    if rec["score"] < min_salience:
        return f"salience:{rec['score']}<{min_salience}", rec
    return None, rec


def next_day() -> datetime:
    return (datetime.now(UTC) + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
