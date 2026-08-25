"""Shared pgvector session helpers — one implementation for every vector leg.

pgvector >= 0.8.0 supports ``hnsw.iterative_scan``, the recall guard for filter-after-ANN
queries (scope/temporal filters are applied as WHERE after the ANN scan; without iterative
scan a filtered query can silently return fewer rows than requested). Used by both the
derived-tier driver (graph/drivers/postgres.py) and the direct tier (services/direct.py).
"""

from __future__ import annotations

from sqlalchemy import text as sa_text

# per-engine cache of whether pgvector exposes hnsw.iterative_scan (0.8.0+)
_ITER_SCAN_CACHE: dict[int, bool] = {}


def _vec_ge(version: str | None, minimum: tuple[int, int, int]) -> bool:
    """``version`` (e.g. ``'0.8.2'``) >= ``minimum``. None/unparseable → False."""
    if not version:
        return False
    try:
        parts = tuple(int(x) for x in str(version).split(".")[:3])
    except ValueError:
        return False
    parts = (parts + (0, 0, 0))[:3]  # pad short versions ('0.8' → (0,8,0))
    return parts >= minimum


async def supports_iterative_scan(session) -> bool:
    """Detect via the extension VERSION (a catalog lookup), NOT ``pg_settings``: the
    ``hnsw.*`` GUCs are only registered once ``vector.so``'s _PG_init runs in the backend,
    so a cold connection sees them absent and would false-negative. Cached per engine."""
    key = id(session.bind)
    cached = _ITER_SCAN_CACHE.get(key)
    if cached is None:
        ver = (
            await session.execute(
                sa_text("SELECT extversion FROM pg_extension WHERE extname = 'vector'")
            )
        ).scalar()
        cached = _vec_ge(ver, (0, 8, 0))
        _ITER_SCAN_CACHE[key] = cached
    return cached


async def enable_iterative_scan(session) -> None:
    """Set the filter-after-ANN recall guard for this transaction (pgvector 0.8.0+). Default
    is ``off``, so the SET is required. We first force a vector op so ``vector.so`` is loaded
    and the ``hnsw.*`` GUCs are registered on this backend — otherwise the SET errors on a
    cold connection (the GUC only exists after the lib's _PG_init)."""
    await session.execute(sa_text("SELECT '[1]'::vector"))  # warm the lib → registers the GUC
    await session.execute(sa_text("SET LOCAL hnsw.iterative_scan = relaxed_order"))


def escape_like(term: str) -> str:
    r"""Escape LIKE/ILIKE wildcards in user input (used with ``escape='\\'``)."""
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
