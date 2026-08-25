"""Default driver: a labeled property graph modeled relationally on Postgres.

Runs unchanged on SQLite (tests). Semantic search is a cosine overlay computed over the
candidate nodes in the accessible scopes; production should add a pgvector column + HNSW
index for scale (see docs/redesign/design-proposal.md §7, M2). Traversal uses bounded BFS
over the edge table.

Isolation invariant: ``_scope_filter`` is the single place that turns the accessible-scope
set into a SQL predicate. The tenant boundary (``org_id``) is baked into every non-GLOBAL
clause, so a buggy caller cannot leak across tenants or scopes.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from sqlalchemy import Float, and_, case, delete, func, literal, literal_column, or_, select

from ...core.scope import Scope
from ...core.temporal import windows_overlap
from ...db.models import Edge, Node
from ...db.scoping import classification_for as _classification_for
from ...db.scoping import cosine as _cosine
from ...db.scoping import scope_clause as _scope_clause
from ..driver import GraphDriver, ScoredNode

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _lexical_score(query: str, node) -> float:
    """Portable (SQLite/offline) lexical score: token overlap + an exact/prefix identifier bonus.
    Mirrors the Postgres tsvector+pg_trgm leg's intent so offline ranking tests stay faithful."""
    q = (query or "").lower().strip()
    if not q:
        return 0.0
    q_toks = set(_TOKEN_RE.findall(q))
    hay = f"{node.name} {node.summary or ''} {node.key}".lower()
    hay_toks = set(_TOKEN_RE.findall(hay))
    overlap = len(q_toks & hay_toks)
    if not overlap and q not in hay:
        return 0.0
    score = overlap / max(1, len(q_toks))
    # identifier recall: exact key, key prefix, or query as a substring of the name (sk_/repo/svc)
    if (
        node.key
        and (node.key.lower() == q or node.key.lower().startswith(q))
        or q in node.name.lower()
    ):
        score += 0.5
    return score


def _as_utc(dt):
    """Treat a naive stored datetime as UTC (our storage convention). Postgres returns
    tz-aware values; SQLite drops the tzinfo on reload, so Python-side comparisons in the
    supersession paths must normalize or they raise on naive-vs-aware."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _as_of_clause(as_of, mode: str):
    """Bi-temporal point-in-time predicate for edges (PR4 / P1.5).

    ``valid`` (default) = what was true in the world at ``as_of``; ``system`` = what we
    believed (had ingested and not yet retracted) at ``as_of``.
    """
    if mode == "system":
        return [Edge.ingested_at <= as_of, or_(Edge.expired_at.is_(None), Edge.expired_at > as_of)]
    return [Edge.valid_at <= as_of, or_(Edge.invalid_at.is_(None), Edge.invalid_at > as_of)]


class PostgresDriver(GraphDriver):
    name = "postgres"

    async def _supports_iterative_scan(self, session) -> bool:
        """Delegates to the shared helper (db/pgvector_support) — one implementation for
        both tiers. Kept as a method because every vector leg in this driver calls it."""
        from ...db.pgvector_support import supports_iterative_scan

        return await supports_iterative_scan(session)

    async def _enable_iterative_scan(self, session) -> None:
        from ...db.pgvector_support import enable_iterative_scan

        await enable_iterative_scan(session)

    async def find_node(self, session, tenant_id, scope, type_, key):
        stmt = select(Node).where(
            _scope_clause(Node, tenant_id, scope), Node.type == type_, Node.key == key
        )
        return (await session.execute(stmt)).scalar_one_or_none()

    async def get_node(self, session, node_id):
        return await session.get(Node, node_id)

    async def upsert_node(
        self,
        session,
        *,
        tenant_id,
        scope,
        type_,
        key,
        name,
        summary=None,
        props=None,
        embedding=None,
        embedding_model=None,
        embedding_dim=None,
        source=None,
    ):
        node = await self.find_node(session, tenant_id, scope, type_, key)
        if node is not None:
            if name:
                node.name = name
            if summary:
                node.summary = summary
            if props:
                node.props = {**(node.props or {}), **props}
            if embedding is not None:
                node.embedding = embedding
                node.embedding_model = embedding_model
                node.embedding_dim = embedding_dim
            if source and node.source is None:  # keep first-learned provenance
                node.source = source
            await session.flush()
            return node
        node = Node(
            org_id=tenant_id,
            scope=scope.scope.value,
            scope_id=scope.scope_id,
            type=type_,
            key=key,
            name=name,
            summary=summary,
            classification=_classification_for(scope),
            owner_user_id=scope.scope_id if scope.scope is Scope.USER else None,
            source=source,
            props=props or {},
            embedding=embedding,
            embedding_model=embedding_model if embedding is not None else None,
            embedding_dim=embedding_dim if embedding is not None else None,
        )
        session.add(node)
        await session.flush()
        return node

    async def upsert_edge(
        self,
        session,
        *,
        tenant_id,
        scope,
        type_,
        src_id,
        dst_id,
        fact=None,
        props=None,
        valid_at=None,
        source=None,
        fact_embedding=None,
        embedding_model=None,
        embedding_dim=None,
    ):
        stmt = select(Edge).where(
            _scope_clause(Edge, tenant_id, scope),
            Edge.type == type_,
            Edge.src_id == src_id,
            Edge.dst_id == dst_id,
            Edge.invalid_at.is_(None),
        )
        existing = (await session.execute(stmt)).scalars().first()
        if existing is not None:
            if fact:
                existing.fact = fact
            if props:
                existing.props = {**(existing.props or {}), **props}
            if fact_embedding is not None:
                existing.fact_embedding = fact_embedding
                existing.embedding_model = embedding_model
                existing.embedding_dim = embedding_dim
            await session.flush()
            return existing
        edge = Edge(
            org_id=tenant_id,
            scope=scope.scope.value,
            scope_id=scope.scope_id,
            type=type_,
            src_id=src_id,
            dst_id=dst_id,
            fact=fact,
            classification=_classification_for(scope),
            source=source,
            props=props or {},
            valid_at=valid_at or datetime.now(UTC),
            fact_embedding=fact_embedding,
            embedding_model=embedding_model if fact_embedding is not None else None,
            embedding_dim=embedding_dim if fact_embedding is not None else None,
        )
        session.add(edge)
        await session.flush()
        return edge

    async def invalidate_superseded(self, session, *, tenant_id, scope, new_edge):
        """Bi-temporal contradiction handling for a just-written functional edge.

        Compares ``new_edge`` against the other active same-``(src, type)`` edges with a
        *different* dst (the deterministic functional fast-path — no LLM). For each
        contradicting (validity-overlapping) old edge:

        * if the new fact is the later one (``new.valid_at >= old.valid_at``) → close the OLD
          edge at ``new.valid_at``;
        * otherwise the new edge is a backfilled *earlier-world* fact → bound the NEW edge at
          ``old.valid_at`` instead, leaving the newer still-true fact open.

        Append-only: only ``invalid_at``/``expired_at`` are ever set, never a DELETE. Returns
        the number of edges bounded.
        """
        stmt = select(Edge).where(
            _scope_clause(Edge, tenant_id, scope),
            Edge.type == new_edge.type,
            Edge.src_id == new_edge.src_id,
            Edge.id != new_edge.id,
            Edge.invalid_at.is_(None),
        )
        rows = (await session.execute(stmt)).scalars().all()
        n = 0
        now = datetime.now(UTC)
        nv, ni = _as_utc(new_edge.valid_at), _as_utc(new_edge.invalid_at)
        for old in rows:
            if old.dst_id == new_edge.dst_id:
                continue  # same fact reasserted, not a contradiction
            ov, oi = _as_utc(old.valid_at), _as_utc(old.invalid_at)
            if not windows_overlap(ov, oi, nv, ni):
                continue  # disjoint validity windows → adjacent, not a contradiction
            if nv >= ov:
                old.invalid_at = nv  # store UTC-normalized (never a naive datetime)
                old.expired_at = old.expired_at or now
            else:  # backfill: new is the older fact → bound it, keep the newer one open
                new_edge.invalid_at = ov
                new_edge.expired_at = new_edge.expired_at or now
            n += 1
        if n:
            await session.flush()
        return n

    async def invalidate_edge(self, session, *, tenant_id, scope, type_, src_id, dst_id, at):
        """Explicitly end an active (src, type, dst) relation at world-time ``at``.

        Closes the matching open edge(s) whose validity covers ``at`` (skips any that began
        *after* the end instant). Append-only: ``invalid_at`` = the world-time end (``at``);
        ``expired_at`` = belief-time, stamped ``now`` (when we processed the end) to match
        ``invalidate_superseded`` and the ontology convention. Never deletes. Returns the count.
        """
        stmt = select(Edge).where(
            _scope_clause(Edge, tenant_id, scope),
            Edge.type == type_,
            Edge.src_id == src_id,
            Edge.dst_id == dst_id,
            Edge.invalid_at.is_(None),
        )
        rows = (await session.execute(stmt)).scalars().all()
        n = 0
        now = datetime.now(UTC)
        at_utc = _as_utc(at)
        for old in rows:
            ov = _as_utc(old.valid_at)
            if ov is not None and ov > at_utc:
                continue  # this relation began after the end instant — not the one being ended
            old.invalid_at = at_utc  # store UTC-normalized (never a naive datetime)
            old.expired_at = old.expired_at or now
            n += 1
        if n:
            await session.flush()
        return n

    async def search_nodes(
        self,
        session,
        *,
        tenant_id,
        scopes,
        query_embedding=None,
        text=None,
        type_=None,
        sources=None,
        limit=10,
    ):
        if not scopes:
            return []  # no accessible scope -> no results (never a tenant-wide scan)
        filters = [or_(*[_scope_clause(Node, tenant_id, s) for s in scopes])]
        # v3 lifecycle: expired working-tier memories are dead even before the TTL sweep
        # physically removes them — correctness must not depend on sweep timing.
        filters.append(or_(Node.expires_at.is_(None), Node.expires_at > func.now()))
        if type_:
            filters.append(Node.type == type_)
        if sources:
            filters.append(Node.source.in_(sources))
        if text:
            filters.append(Node.name.ilike(f"%{text}%"))

        # Postgres + a query vector: ANN ordering via pgvector `<=>` (HNSW). The scope filters
        # are applied as WHERE *after* the ANN scan, so relax iterative scan to keep recall on
        # filtered queries (see migration 0007). SQLite falls through to the Python path.
        if query_embedding is not None and session.bind.dialect.name == "postgresql":
            # return_type=Float: the `<=>` distance is a float, not a Vector — without this the
            # column inherits the Embedding type and pgvector tries to decode the float as a vector.
            dist = Node.embedding.op("<=>", return_type=Float)(query_embedding)
            # Scope/temporal filters are applied as WHERE *after* the ANN scan, so without
            # iterative scan a filtered query can return < limit rows (silent recall loss). Relax
            # iterative scan to keep recall (pgvector 0.8.0+; older versions skip — see migration
            # 0007). This is the filter-after-ANN fix #18 validates.
            if await self._supports_iterative_scan(session):
                await self._enable_iterative_scan(session)
            # NULL embeddings have no distance (`<=>` yields NULL) — exclude them from the ANN
            # leg; the lexical leg still surfaces those rows.
            stmt = (
                select(Node, dist.label("dist"))
                .where(*filters, Node.embedding.isnot(None))
                .order_by(dist)
                .limit(limit)
            )
            rows = (await session.execute(stmt)).all()
            return [ScoredNode(node, 1.0 - dist_val) for node, dist_val in rows]

        candidates = (await session.execute(select(Node).where(*filters))).scalars().all()
        if query_embedding is not None:
            scored = [ScoredNode(n, _cosine(query_embedding, n.embedding)) for n in candidates]
            scored.sort(key=lambda s: s.score, reverse=True)
            return scored[:limit]
        return [ScoredNode(n, 0.0) for n in candidates[:limit]]

    async def lexical_search_nodes(
        self, session, *, tenant_id, scopes, text, type_=None, sources=None, limit=10
    ):
        """The hybrid lexical leg (BM25-style). Postgres: weighted FTS (name=A, summary=B) via
        ``ts_rank_cd`` UNIONed with pg_trgm similarity + key-prefix for identifier recall
        (service/repo/sk_* names FTS tokenization mangles). SQLite/offline: a portable
        token-overlap + identifier-bonus score. Returns ``ScoredNode`` ordered by relevance.

        We deliberately use tsvector+pg_trgm, not pg_search/vchord — those can't run on the prod
        RDS; this stays portable on managed Postgres. (Swap seam only if memory-graph leaves RDS.)
        """
        if not scopes or not text:
            return []
        base = [or_(*[_scope_clause(Node, tenant_id, s) for s in scopes])]
        # v3 lifecycle: expired working-tier rows are dead regardless of sweep timing.
        base.append(or_(Node.expires_at.is_(None), Node.expires_at > func.now()))
        if type_:
            base.append(Node.type == type_)
        if sources:
            base.append(Node.source.in_(sources))

        if session.bind.dialect.name == "postgresql":
            # IMMUTABLE tsvector, identical to migration 0008's ix_node_fts expression so the GIN
            # index is used (regconfig cast + ``||`` rather than the only-STABLE concat_ws).
            doc = func.coalesce(Node.name, "").concat(" ").concat(func.coalesce(Node.summary, ""))
            tsv = func.to_tsvector(literal_column("'english'::regconfig"), doc)
            tsq = func.plainto_tsquery("english", text)
            sim = func.similarity(Node.name, text)  # pg_trgm
            score = (func.ts_rank_cd(tsv, tsq) + sim).label("lex")
            matched = or_(
                tsv.op("@@")(tsq),  # full-text match
                Node.name.op("%")(text),  # trigram-similar (pg_trgm threshold)
                func.lower(Node.key).like(func.lower(text) + "%"),  # identifier prefix
            )
            stmt = select(Node, score).where(*base, matched).order_by(score.desc()).limit(limit)
            rows = (await session.execute(stmt)).all()
            return [ScoredNode(n, float(s)) for n, s in rows]

        # SQLite/offline: portable token-overlap scoring
        candidates = (await session.execute(select(Node).where(*base))).scalars().all()
        scored = [ScoredNode(n, sc) for n in candidates if (sc := _lexical_score(text, n)) > 0]
        scored.sort(key=lambda s: s.score, reverse=True)
        return scored[:limit]

    async def find_node_ids(self, session, *, tenant_id, scopes, type_, keys):
        """Resolve node ids for ``(type_, key)`` across the accessible scopes — used to find the
        caller's anchor nodes (their User node, activated Agent) for node-distance reranking."""
        keys = [k for k in keys if k]
        if not scopes or not keys:
            return []
        stmt = select(Node.id).where(
            or_(*[_scope_clause(Node, tenant_id, s) for s in scopes]),
            Node.type == type_,
            Node.key.in_(keys),
        )
        return [r for (r,) in (await session.execute(stmt)).all()]

    async def graph_distance(self, session, *, anchor_ids, candidate_ids, max_depth=3):
        """Min hop distance from any anchor to each candidate over *current* edges (undirected),
        via a depth-bounded recursive CTE (Postgres and SQLite both support WITH RECURSIVE; the
        depth bound also makes it cycle-safe). Candidates unreachable within ``max_depth`` are
        omitted. Returns ``{candidate_id: distance}``."""
        anchors = [a for a in dict.fromkeys(anchor_ids) if a]
        cands = [c for c in dict.fromkeys(candidate_ids) if c]
        if not anchors or not cands:
            return {}
        # non-recursive base: all anchors at distance 0 (a single SELECT — a recursive CTE's base
        # term can't itself be a UNION).
        base = select(Node.id.label("id"), literal(0).label("dist")).where(Node.id.in_(anchors))
        reach = base.cte("reach", recursive=True)
        e = Edge.__table__
        step = (
            select(
                case((e.c.src_id == reach.c.id, e.c.dst_id), else_=e.c.src_id).label("id"),
                (reach.c.dist + 1).label("dist"),
            )
            .select_from(
                reach.join(
                    e,
                    and_(
                        or_(e.c.src_id == reach.c.id, e.c.dst_id == reach.c.id),
                        e.c.invalid_at.is_(None),
                    ),
                )
            )
            .where(reach.c.dist < max_depth)
        )
        reach = reach.union_all(step)
        q = (
            select(reach.c.id, func.min(reach.c.dist))
            .where(reach.c.id.in_(cands))
            .group_by(reach.c.id)
        )
        return {nid: int(d) for nid, d in (await session.execute(q)).all()}

    async def search_fact_edges(
        self,
        session,
        *,
        tenant_id,
        scopes,
        query_embedding=None,
        as_of=None,
        as_of_mode="valid",
        include_invalid=False,
        limit=10,
    ):
        """Vector search over edge ``fact_embedding`` with scope + temporal (invalid_at/as_of)
        filters — temporally-aware fact retrieval. This is the most heavily-filtered vector query,
        so on Postgres it relies on HNSW iterative_scan (when available) to avoid filter-after-ANN
        recall loss (#18). Returns ``[(Edge, score)]``. SQLite falls back to Python cosine.
        """
        if not scopes:
            return []
        filters = [or_(*[_scope_clause(Edge, tenant_id, s) for s in scopes]), Edge.fact.isnot(None)]
        if as_of is not None:
            filters += _as_of_clause(as_of, as_of_mode)
        elif not include_invalid:
            filters.append(Edge.invalid_at.is_(None))  # current view

        if query_embedding is not None and session.bind.dialect.name == "postgresql":
            dist = Edge.fact_embedding.op("<=>", return_type=Float)(query_embedding)
            if await self._supports_iterative_scan(session):
                await self._enable_iterative_scan(session)
            stmt = (
                select(Edge, dist.label("dist"))
                .where(*filters, Edge.fact_embedding.isnot(None))
                .order_by(dist)
                .limit(limit)
            )
            return [(e, 1.0 - d) for e, d in (await session.execute(stmt)).all()]

        rows = (await session.execute(select(Edge).where(*filters))).scalars().all()
        if query_embedding is not None:
            scored = [
                (e, _cosine(query_embedding, e.fact_embedding)) for e in rows if e.fact_embedding
            ]
            scored.sort(key=lambda x: x[1], reverse=True)
            return scored[:limit]
        return [(e, 0.0) for e in rows[:limit]]

    async def nodes_with_valid_edges(self, session, node_ids, *, as_of, as_of_mode="valid"):
        """Of ``node_ids``, those with >=1 edge valid at ``as_of`` — node-follows-edge-validity.
        A node only surfaces at a time it actually participates in the graph (e.g. a Fact whose
        DECIDED edge was invalidated by T drops out of an as_of=T read)."""
        ids = list(dict.fromkeys(node_ids))
        if not ids:
            return set()
        stmt = select(Edge.src_id, Edge.dst_id).where(
            or_(Edge.src_id.in_(ids), Edge.dst_id.in_(ids)),
            *_as_of_clause(as_of, as_of_mode),
        )
        idset = set(ids)
        live: set[str] = set()
        for src, dst in (await session.execute(stmt)).all():
            if src in idset:
                live.add(src)
            if dst in idset:
                live.add(dst)
        return live

    async def neighbors(
        self,
        session,
        node_id,
        *,
        depth=1,
        include_invalid=False,
        as_of=None,
        as_of_mode="valid",
        tenant_id=None,
        scopes=None,
        max_edges=500,
    ):
        frontier: set[str] = {node_id}
        seen: set[str] = set()
        collected: dict[str, Edge] = {}
        for _ in range(max(1, depth)):
            if not frontier or len(collected) >= max_edges:
                break
            stmt = select(Edge).where(or_(Edge.src_id.in_(frontier), Edge.dst_id.in_(frontier)))
            if scopes is not None:
                # confine expansion to the caller's accessible scopes — an authorized start
                # node must not let BFS walk into scopes the caller cannot read.
                if not scopes:
                    break
                stmt = stmt.where(or_(*[_scope_clause(Edge, tenant_id, s) for s in scopes]))
            if as_of is not None:
                # time-travel: edges true (valid) / believed (system) at the instant `as_of`.
                stmt = stmt.where(*_as_of_clause(as_of, as_of_mode))
            elif not include_invalid:
                stmt = stmt.where(Edge.invalid_at.is_(None))  # current view
            stmt = stmt.limit(max_edges - len(collected))  # hub-node fan-out bound
            rows = (await session.execute(stmt)).scalars().all()
            nxt: set[str] = set()
            for e in rows:
                collected[e.id] = e
                for nid in (e.src_id, e.dst_id):
                    if nid not in seen and nid not in frontier:
                        nxt.add(nid)
            seen |= frontier
            frontier = nxt - seen
        return list(collected.values())

    async def purge_scope(self, session, *, tenant_id, scope):
        edges = await session.execute(delete(Edge).where(_scope_clause(Edge, tenant_id, scope)))
        nodes = await session.execute(delete(Node).where(_scope_clause(Node, tenant_id, scope)))
        await session.flush()
        return nodes.rowcount or 0, edges.rowcount or 0
