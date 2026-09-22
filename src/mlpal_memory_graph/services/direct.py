"""Direct memory: store verbatim content as retrievable embedded chunks, and search them.

This is the DIRECT tier (conversation history, PDFs, docs kept as-is and citeable), as opposed
to the DERIVED graph (inferred nodes/edges). It reuses the one scope predicate (``db/scoping``),
the shared embedder, and the same governance columns — so consent/policy/secret-scrub and
tenant/scope isolation apply identically to both tiers. See design-proposal §14.
"""

from __future__ import annotations

import re

from dataclasses import dataclass

from sqlalchemy import delete, or_, select

from ..core.config import get_settings
from ..core.logging import get_logger
from ..core.scope import Scope, ScopeRef
from ..db.models import Chunk, Document
from ..db.scoping import classification_for, cosine, scope_clause
from .embeddings_client import get_embedder

DEFAULT_CHUNK_CHARS = 1000


log = get_logger(__name__)

_USER_TURN = re.compile(r"(?im)^\s*(user|human)\s*:")


def has_user_turn(content: str) -> bool:
    """True when the passage contains a user/human turn (a line starting `user:`); transcripts are
    `speaker: text` lines, so this is a line-anchored check, not a substring one."""
    return bool(_USER_TURN.search(content or ""))


# memory v8 C6 (LEARNINGS L30): a question about the assistant's own words — the answer is assistant text
_ASKS_ASSISTANT = re.compile(
    r"\b(you (told|said|mentioned|suggested|recommended|gave|shared|listed|explained|advised|described)"
    r"|what did you\b|you'd (suggested|recommended|mentioned|said)"
    r"|your (suggestions?|recommendations?|advice|tips?|explanation|answer)\b"
    r"|(our|the) (previous |last |earlier |recent )?(conversation|chat|discussion)s? (about|on|regarding|where)\b"
    r"|we (discussed|talked about|went over)\b)",
    re.I,
)


def has_assistant_turn(content: str) -> bool:
    """A chunk that carries no user turn: a `speaker: text` transcript is user and assistant turns
    only, and a long assistant turn is chunked into continuation chunks that carry no speaker line
    at all (memory v8 L32: that is where the assistant's answer usually sits), so the presence of an
    `assistant:` line is not the test — the absence of a user line is."""
    return not has_user_turn(content)


def speaker_for_question(query: str | None) -> str:
    """Which speaker's turns the question is about: "assistant" when it asks what the assistant said
    or recommended, "user" otherwise (the user's own facts, preferences and events)."""
    return "assistant" if query and _ASKS_ASSISTANT.search(query) else "user"


def apply_user_turn_boost(fused: dict[str, float], content_of, factor: float, speaker: str = "user") -> dict[str, float]:
    """memory v8 C1 (LEARNINGS L18): passages carrying the user's own words outrank assistant prose
    of the same fused score by `factor`. `factor` 1.0 returns the input unchanged. C6 (L30):
    `speaker="assistant"` boosts assistant turns instead, for a question about the assistant's words."""
    if factor == 1.0:
        return fused
    has = has_assistant_turn if speaker == "assistant" else has_user_turn
    return {cid: s * (factor if has(content_of(cid)) else 1.0) for cid, s in fused.items()}


_ROLE_SPEAKERS = {"user", "human", "assistant", "ai", "bot"}
_NAME_HEAD = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)?$")


def split_turns(paragraph: str) -> list[tuple[str | None, list[str]]]:
    """The turns of a `speaker: text` transcript as (speaker, lines). When the text has role prefixes
    (user/assistant/…) only those start a turn; otherwise a capitalised name that recurs does. Any
    other line — an assistant's `1. Online Reviews: …` list item, a `Note:` — continues the turn."""
    lines = [ln for ln in paragraph.split("\n") if ln.strip()]
    heads = [ln.split(":", 1)[0].strip() for ln in lines if ":" in ln]
    role_mode = any(h.lower() in _ROLE_SPEAKERS for h in heads)
    counts: dict[str, int] = {}
    for h in heads:
        if _NAME_HEAD.match(h):
            counts[h] = counts.get(h, 0) + 1
    names = {h for h, n in counts.items() if n >= 2}
    turns: list[tuple[str | None, list[str]]] = []
    for ln in lines:
        head, sep, _ = ln.partition(":")
        h = head.strip()
        starts = bool(sep) and ((h.lower() in _ROLE_SPEAKERS) if role_mode else h in names)
        if starts or not turns:
            turns.append((h if starts else None, [ln]))
        else:
            turns[-1][1].append(ln)
    return turns


def _pack_turns(paragraph: str, max_chars: int) -> list[str]:
    """memory v9 C2: whole turns packed up to the cap. A turn longer than the cap is cut into pieces
    of whole lines (a single oversize line at the cap), and every continuation piece opens with
    `speaker: (continued)` so it still says who is talking — the head-to-head misses sat in
    continuation chunks of long assistant turns that carried no speaker at all (v8 L32)."""
    chunks: list[str] = []
    buf: list[str] = []
    size = 0
    for speaker, lines in split_turns(paragraph):
        text = "\n".join(lines)
        if len(text) <= max_chars:
            if buf and size + len(text) + 1 > max_chars:
                chunks.append("\n".join(buf)); buf, size = [], 0
            buf.append(text); size += len(text) + 1
            continue
        if buf:
            chunks.append("\n".join(buf)); buf, size = [], 0
        prefix = f"{speaker}: (continued) " if speaker else ""
        room = max(1, max_chars - len(prefix))
        piece: list[str] = []
        psize = 0
        first = True
        for ln in lines:
            units = [ln] if len(ln) <= room else [ln[i : i + room] for i in range(0, len(ln), room)]
            for u in units:
                if piece and psize + len(u) + 1 > max_chars:
                    chunks.append("\n".join(piece)); piece, psize, first = [], 0, False
                if not piece and not first:
                    u = prefix + u
                piece.append(u); psize += len(u) + 1
        if piece:
            chunks.append("\n".join(piece))
    if buf:
        chunks.append("\n".join(buf))
    return chunks


def chunk_text(content: str, max_chars: int = DEFAULT_CHUNK_CHARS, overlap_lines: int | None = None) -> list[str]:
    """Split content into chunks capped at ``max_chars``: on paragraph boundaries first, then on line
    boundaries inside an oversize paragraph, hard-splitting only a single line longer than the cap.

    Deterministic, no model call. memory v7 WP14: a conversation transcript is one paragraph of
    ``speaker: text`` lines, and the old rule hard-split it every 1,000 characters mid-turn; the
    LongMemEval-S misses were mostly inside a retrieved session, at those cuts. Lines are the unit
    now, and ``overlap_lines`` (default from settings, 1) carries the last line(s) of a chunk into
    the next so a turn and its reply stay retrievable together.
    """
    content = (content or "").strip()
    if not content:
        return []
    settings = get_settings()
    if overlap_lines is None:
        overlap_lines = max(0, int(settings.chunk_overlap_lines))
    mode = settings.chunk_mode or "paragraph"
    line_aware = mode == "line"
    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(para) <= max_chars:
            if current and len(current) + len(para) + 2 > max_chars:
                chunks.append(current)
                current = para
            else:
                current = f"{current}\n\n{para}" if current else para
            continue
        if current:
            chunks.append(current)
            current = ""
        if mode == "turn":
            chunks.extend(_pack_turns(para, max_chars))
            continue
        if not line_aware:
            # paragraph mode (the measured default): fixed-size cuts, every chunk carries the full cap
            # of text; measured 0.850 vs 0.817 for line packing on LongMemEval-S 60 (two runs)
            for i in range(0, len(para), max_chars):
                chunks.append(para[i : i + max_chars])
            continue
        # line mode: pack whole lines, overlapping the tail of one chunk into the next
        lines = [ln for ln in para.split("\n") if ln.strip()]
        buf: list[str] = []
        size = 0
        for ln in lines:
            if len(ln) > max_chars:
                if buf:
                    chunks.append("\n".join(buf)); buf, size = [], 0
                for i in range(0, len(ln), max_chars):
                    chunks.append(ln[i : i + max_chars])
                continue
            if buf and size + len(ln) + 1 > max_chars:
                chunks.append("\n".join(buf))
                tail = buf[-overlap_lines:] if overlap_lines else []
                tail = [t for t in tail if len(t) + len(ln) + 1 <= max_chars]
                buf = list(tail); size = sum(len(t) + 1 for t in buf)
            buf.append(ln); size += len(ln) + 1
        if buf:
            chunks.append("\n".join(buf))
    if current:
        chunks.append(current)
    return chunks


@dataclass
class ChunkHit:
    chunk: Chunk
    score: float


class DirectMemory:
    # per-process df cache: (tenant, term) → (df, n_total, monotonic_stamp)
    _DF_TTL_SECONDS = 600.0

    def __init__(self) -> None:
        self.embedder = get_embedder()
        self._df_cache: dict[tuple[str | None, str], tuple[int, int, float]] = {}

    async def _idf_for(self, session, tenant_id, terms, base, tsv, term_tsqs) -> dict[str, float]:
        """IDF per term, from cached document frequencies (one filtered-aggregate scan
        for the misses). Corpus df drifts slowly; a 10-minute TTL is far fresher than
        the knowledge itself."""
        import math
        import time

        now = time.monotonic()
        idf: dict[str, float] = {}
        missing = []
        for t in terms:
            hit = self._df_cache.get((tenant_id, t))
            if hit and now - hit[2] < self._DF_TTL_SECONDS:
                df, n_total, _ = hit
                idf[t] = math.log(1.0 + (n_total - df + 0.5) / (df + 0.5))
            else:
                missing.append(t)
        if missing:
            from sqlalchemy import func, select

            cols = [func.count().label("n_total")] + [
                func.count().filter(tsv.op("@@")(term_tsqs[t])).label(f"df_{i}")
                for i, t in enumerate(missing)
            ]
            row = (await session.execute(select(*cols).select_from(Chunk).where(*base))).one()
            n_total = row[0] or 1
            for i, t in enumerate(missing):
                df = row[i + 1] or 0
                self._df_cache[(tenant_id, t)] = (df, n_total, now)
                idf[t] = math.log(1.0 + (n_total - df + 0.5) / (df + 0.5))
        return idf

    async def add_document(
        self,
        session,
        *,
        tenant_id: str | None,
        scope: ScopeRef,
        content: str,
        title: str | None = None,
        uri: str | None = None,
        source: str | None = None,
        source_ref: str | None = None,
        workspace: str | None = None,
        valid_at=None,  # bitemporal event-time (when the content was true/written)
    ) -> Document | None:
        """Store ``content`` verbatim as a Document + embedded Chunks. Returns None if empty."""
        import time as _time

        t0 = _time.monotonic()
        chunks = chunk_text(content)
        if not chunks:
            return None
        cls = classification_for(scope)
        owner = scope.scope_id if scope.scope is Scope.USER else None
        doc = Document(
            org_id=tenant_id,
            scope=scope.scope.value,
            scope_id=scope.scope_id,
            classification=cls,
            owner_user_id=owner,
            source=source,
            source_ref=source_ref,
            title=title,
            uri=uri,
            workspace=workspace,
            valid_at=valid_at,
        )
        session.add(doc)
        await session.flush()
        t1 = _time.monotonic()
        embeddings = await self.embedder.embed(chunks)
        t2 = _time.monotonic()
        for i, (text, emb) in enumerate(zip(chunks, embeddings, strict=False)):
            session.add(
                Chunk(
                    document_id=doc.id,
                    org_id=tenant_id,
                    scope=scope.scope.value,
                    scope_id=scope.scope_id,
                    classification=cls,
                    owner_user_id=owner,
                    source=source,
                    workspace=workspace,
                    ordinal=i,
                    content=text,
                    embedding=emb,
                    embedding_model=self.embedder.name,
                    embedding_dim=self.embedder.dim,
                )
            )
        await session.flush()
        # memory v7 WP14: where ingest spends its time (chunk, embed, insert incl. the HNSW updates)
        self.last_ingest_timings = {"chunks": len(chunks), "chunk_ms": int((t1 - t0) * 1000),
                                    "embed_ms": int((t2 - t1) * 1000), "insert_ms": int((_time.monotonic() - t2) * 1000)}
        return doc

    async def search(
        self,
        session,
        *,
        tenant_id: str | None,
        scopes: list[ScopeRef],
        query: str | None = None,
        sources: list[str] | None = None,
        limit: int = 10,
        workspace: str | None = None,
        legs: set[str] | None = None,
        as_of=None,
        as_of_mode: str = "valid",
        per_document: int = 1,
    ) -> list[ChunkHit]:
        """Retrieve passages across the accessible scopes only (hard scope predicate).

        Two legs fused by RRF, mirroring the derived-tier node path: a semantic leg
        (pgvector ANN on Postgres with the iterative-scan recall guard; Python cosine on
        SQLite) and a lexical leg (escaped ILIKE as a candidate *generator* — never a hard
        prefilter, so semantically relevant passages without the literal substring are
        reachable).
        """
        if not scopes:
            return []
        base = [or_(*[scope_clause(Chunk, tenant_id, s) for s in scopes])]
        if sources:
            base.append(Chunk.source.in_(sources))
        if as_of is not None:
            # Point-in-time reads must not see the future (x6 measured 9/12 as-of
            # answers leaking post-instant knowledge before this filter existed).
            # valid mode: the parent document's event time (falling back to ingest
            # time when the source is undated); system mode: what the STORE knew.
            from sqlalchemy import and_
            from sqlalchemy import select as _select

            if as_of_mode == "system":
                time_ok = Document.ingested_at <= as_of
            else:
                time_ok = or_(
                    Document.valid_at <= as_of,
                    and_(Document.valid_at.is_(None), Document.ingested_at <= as_of),
                )
            base.append(Chunk.document_id.in_(_select(Document.id).where(time_ok)))
        if not query:
            rows = (await session.execute(select(Chunk).where(*base).limit(limit))).scalars()
            return [ChunkHit(c, 0.0) for c in rows]

        # eval-facing ablation switch: run one leg alone to measure its solo quality
        # (vector-only == a naive-RAG baseline; lexical-only == FTS baseline)
        legs = legs or {"vector", "lexical"}
        overfetch = max(limit * 3, 30)
        import time as _time

        timings: dict[str, int] = {}
        degraded: list[str] = []
        _t0 = _time.monotonic()
        embedding = None
        if "vector" in legs:
            try:
                embedding = await self.embedder.embed_one(query)
            except Exception as exc:  # noqa: BLE001 — memory v9 round 3: the embedder is a dependency, not the answer
                # lexical-only is a worse page than a hybrid one but a page; the response says so
                # (`degraded: ["vector"]`), the log and the counter say how often
                from .metrics import REGISTRY as _registry

                legs = set(legs) - {"vector"}
                degraded.append("vector")
                _registry.inc("search_degraded", tier="direct", leg="vector")
                log.warning("search.degraded", tier="direct", leg="vector", error=str(exc)[:160])
        timings["direct_embed_ms"] = int((_time.monotonic() - _t0) * 1000)
        self.last_degraded = degraded
        by_id: dict[str, Chunk] = {}
        _t0 = _time.monotonic()

        # -- semantic leg --
        if "vector" not in legs:
            semantic = []
        elif session.bind.dialect.name == "postgresql":
            from sqlalchemy import Float

            from ..db.pgvector_support import enable_iterative_scan, supports_iterative_scan

            dist = Chunk.embedding.op("<=>", return_type=Float)(embedding)
            if await supports_iterative_scan(session):
                await enable_iterative_scan(session)
            stmt = (
                select(Chunk, dist.label("dist"))
                .where(*base, Chunk.embedding.isnot(None))
                .order_by(dist)
                .limit(overfetch)
            )
            semantic = [(c, 1.0 - d) for c, d in (await session.execute(stmt)).all()]
        else:
            candidates = (await session.execute(select(Chunk).where(*base))).scalars().all()
            semantic = sorted(
                ((c, cosine(embedding, c.embedding)) for c in candidates),
                key=lambda t: t[1],
                reverse=True,
            )[:overfetch]
        for c, _ in semantic:
            by_id[str(c.id)] = c
        timings["direct_vector_ms"] = int((_time.monotonic() - _t0) * 1000)
        _t0 = _time.monotonic()

        # -- lexical leg: term-based full-text ranking, NOT whole-query phrase matching.
        # (The eval caught this: an ILIKE on the full question matches nothing for
        # multi-word queries, which silenced the lexical leg entirely — see
        # evals/results/20260730-182214-memory.json, hit@5 0.20 vs grep 0.55.)
        # OR-semantics term ranking with IDF weighting. Two eval-diagnosed defects led
        # here (results 182214 → 183615 → 185126): a whole-question phrase ILIKE, then
        # AND-semantics plainto_tsquery, then equal-weight coverage — under which
        # corpus-ubiquitous terms ("memory", "graph") count as much as discriminating
        # ones ("tenant", "isolation"); Postgres FTS has no IDF. We compute per-term
        # document frequencies via the GIN index (≤8 fast counts) and rank by
        # IDF-weighted term coverage in SQL. (A true BM25 index — pg_search/vchord —
        # can't run on the prod RDS; this stays portable.)
        import math as _math
        import re as _re

        terms = [t.lower() for t in _re.findall(r"[a-zA-Z0-9_]{3,}", query)][:8]
        lex_overfetch = max(limit * 6, 60)
        if session.bind.dialect.name == "postgresql":
            from sqlalchemy import case, func, literal_column

            # migration 0013: STORED generated tsvector — computed once per write, not
            # 9× per row per query (evals 192510/192522: p95 2.8s cold / 1.1s warm).
            tsv = literal_column("chunks.content_tsv")
            if not terms:
                lexical = []
            else:
                # Binary IDF-weighted coverage — the measured-best scoring (run 190743:
                # hit@5 0.55). A tf-saturating variant (idf × ts_rank_cd) REGRESSED to
                # 0.45 at 3× the latency (run 192246): single-term cover density rewards
                # repetitive transcript text over curated docs. Reverted on measurement.
                # dfs come from ONE filtered-aggregate scan, cached per (tenant, term) —
                # document frequencies drift slowly; interactive agents repeat domain
                # terms constantly.
                term_tsqs = {tm: func.to_tsquery("english", tm) for tm in terms}
                idf = await self._idf_for(session, tenant_id, terms, base, tsv, term_tsqs)
                timings["direct_idf_ms"] = int((_time.monotonic() - _t0) * 1000)
                _t0 = _time.monotonic()
                score_expr = None
                for tm in terms:
                    term_score = case((tsv.op("@@")(term_tsqs[tm]), idf[tm]), else_=0.0)
                    score_expr = term_score if score_expr is None else score_expr + term_score
                any_tsq = func.to_tsquery("english", " | ".join(terms))
                lex_stmt = (
                    select(Chunk, score_expr.label("lex"))
                    .where(*base, tsv.op("@@")(any_tsq))
                    .order_by(literal_column("lex").desc())
                    .limit(lex_overfetch)
                )
                lexical = [c for c, _ in (await session.execute(lex_stmt)).all()]
        else:
            # portable idf × log-tf scoring (same model, in-process)
            candidates = (await session.execute(select(Chunk).where(*base))).scalars().all()
            tokenized = [
                (c, _re.findall(r"[a-z0-9_]{3,}", c.content.lower())) for c in candidates
            ]
            n_total = len(tokenized) or 1
            idf = {}
            for t in terms:
                df = sum(1 for _, toks in tokenized if t in toks)
                idf[t] = _math.log(1.0 + (n_total - df + 0.5) / (df + 0.5))
            scored_lex = []
            token_sets = [(c, set(toks)) for c, toks in tokenized]
            for c, toks in token_sets:
                s = sum(idf[t] for t in terms if t in toks)
                if s > 0:
                    scored_lex.append((c, s))
            scored_lex.sort(key=lambda t: t[1], reverse=True)
            lexical = [c for c, _ in scored_lex[:lex_overfetch]]
        for c in lexical:
            by_id[str(c.id)] = c
        timings["direct_lexical_ms"] = int((_time.monotonic() - _t0) * 1000)
        _t0 = _time.monotonic()

        # -- title leg: document-level signal the chunk legs structurally lack --
        # x5 round 4 measured ranking dilution as the corpus grew: chunks match on
        # their own text only, so a focused guide loses to bulk mentions in large
        # docs. Titles carry curated document-level vocabulary; the docs table is
        # small, so on-the-fly title FTS is cheap. Chunks of title-matched docs
        # join the fusion as a third ranked list.
        title_chunks: list[Chunk] = []
        if terms and "lexical" in legs:
            doc_where = [or_(*[scope_clause(Document, tenant_id, s) for s in scopes])]
            if session.bind.dialect.name == "postgresql":
                from sqlalchemy import func as _f

                any_tsq = _f.to_tsquery("english", " | ".join(terms))
                title_docs = (
                    (
                        await session.execute(
                            select(Document.id)
                            .where(*doc_where, Document.title.isnot(None))
                            .where(_f.to_tsvector("english", Document.title).op("@@")(any_tsq))
                            .limit(20)
                        )
                    )
                    .scalars()
                    .all()
                )
            else:
                docs = (
                    (await session.execute(select(Document).where(*doc_where)))
                    .scalars()
                    .all()
                )
                title_docs = [
                    d.id
                    for d in docs
                    if d.title and any(t in d.title.lower() for t in terms)
                ][:20]
            if title_docs:
                title_chunks = (
                    (
                        await session.execute(
                            select(Chunk)
                            .where(*base, Chunk.document_id.in_(title_docs))
                            .limit(lex_overfetch)
                        )
                    )
                    .scalars()
                    .all()
                )
                for c in title_chunks:
                    by_id[str(c.id)] = c

        timings["direct_title_ms"] = int((_time.monotonic() - _t0) * 1000)
        self.last_timings = timings

        # -- weighted RRF fusion --
        # A known-weak vector signal (dev-hash embedder) must not dilute the lexical leg
        # 1:1 (eval runs 182214→183737 measured exactly that dilution). Semantic
        # embedders fuse at full weight.
        from .hybrid import rrf_fuse

        vector_weight = 0.25 if getattr(self.embedder, "quality", "semantic") == "dev" else 1.0
        fused = rrf_fuse(
            [
                [str(c.id) for c, _ in semantic],
                [str(c.id) for c in lexical],
                [str(c.id) for c in title_chunks],
            ],
            weights=[vector_weight, 1.0, 0.7],
        )
        # workspace focus, direct tier (the ablation run 192535 measured ZERO effect —
        # the boost only existed on the node path; passages are what agents mostly read)
        if workspace:
            fused = {
                cid: s * (1.4 if by_id[cid].workspace == workspace else 1.0)
                for cid, s in fused.items()
            }
        settings = get_settings()
        speaker = speaker_for_question(query) if settings.direct_speaker_boost_mode == "question" else "user"
        fused = apply_user_turn_boost(fused, lambda cid: by_id[cid].content, float(settings.direct_user_turn_boost), speaker)
        # per-document diversity: at most `per_document` chunks per document in the final page.
        # One is right for a curated-docs corpus (five chunks of one runbook is one answer);
        # a conversation memory is different — a long session holds several relevant turns,
        # and the benchmark harness (memory v7 WP12) measured recall capped by this very
        # rule (three sessions → three passages). Callers choose; the default stays one.
        ranked = sorted(fused.items(), key=lambda kv: kv[1], reverse=True)
        out: list[ChunkHit] = []
        per_doc: dict[str, int] = {}
        cap = max(1, int(per_document))
        for cid, score in ranked:
            chunk = by_id[cid]
            if per_doc.get(chunk.document_id, 0) >= cap:
                continue
            per_doc[chunk.document_id] = per_doc.get(chunk.document_id, 0) + 1
            out.append(ChunkHit(chunk, score))
            if len(out) >= limit:
                break
        return out

    async def purge_scope(
        self, session, *, tenant_id: str | None, scope: ScopeRef
    ) -> tuple[int, int]:
        """Hard-delete documents + chunks owned by ``scope`` (the CLEAR opt-out, direct tier)."""
        chunks = await session.execute(delete(Chunk).where(scope_clause(Chunk, tenant_id, scope)))
        docs = await session.execute(
            delete(Document).where(scope_clause(Document, tenant_id, scope))
        )
        await session.flush()
        return docs.rowcount or 0, chunks.rowcount or 0
