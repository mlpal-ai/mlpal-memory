"""The incremental fold: process one episode into the graph (Graphiti-style loop).

route -> extract -> embed (facts) -> resolve/upsert nodes -> upsert edges (bi-temporal,
invalidate-not-delete for functional relations) -> mark processed.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ..core.config import get_settings
from ..core.logging import get_logger
from ..core.scope import Scope, ScopeRef
from ..core.text import normalize_fact
from ..graph import get_driver
from ..services.direct import DirectMemory
from ..services.embeddings_client import get_embedder
from ..services.redaction import redact, redact_mapping
from ..services.secrets import get_vault
from .contradiction import get_judge, judge_and_invalidate
from .extractor import extract
from .gates import fold_gate
from .llm_extractor import get_llm_extractor
from .resolver import Resolver
from .router import extraction_tier

log = get_logger(__name__)


def _episode_scope(episode) -> ScopeRef:
    """The hierarchy layer this episode's extracted memory is owned by."""
    scope = Scope(episode.scope or "org")
    if scope is Scope.GLOBAL:
        return ScopeRef.global_()
    # org-scoped episodes default their scope_id to the tenant (org_id).
    return ScopeRef(scope, episode.scope_id or episode.org_id)


class Updater:
    def __init__(self, llm_enabled: bool | None = None) -> None:
        self.driver = get_driver()
        self.embedder = get_embedder()
        self.resolver = Resolver(self.driver, self.embedder)
        self.direct = DirectMemory()
        # the cost-tiered 'full' path: LLM extraction (content episodes) + contradiction judge
        # (non-functional edges). Off unless MLPAL_EXTRACTOR=llm; tests pass llm_enabled=True.
        self.llm_enabled = get_settings().extractor == "llm" if llm_enabled is None else llm_enabled
        self.llm_extractor = get_llm_extractor() if self.llm_enabled else None
        self.judge = get_judge() if self.llm_enabled else None

    async def process_episode(self, session, episode) -> dict:
        tier = extraction_tier(episode.action_type, bool(episode.content))
        episode.tier = tier
        tenant_id = episode.org_id
        scope = _episode_scope(episode)

        # single pre-extraction choke point: consent opt-out + deterministic policy denies.
        reason = await fold_gate(session, tenant_id, scope, episode)
        if reason is not None:
            episode.dropped_reason = reason
            episode.processed = True
            episode.processed_at = datetime.now(UTC)
            log.info(
                "extraction.item_dropped",
                event_id=episode.event_id,
                rule=reason,
                scope=str(scope),
                source=episode.source,
            )
            return {"dropped": reason, "nodes": 0, "edges": 0}

        # guardrail: vault any secrets BEFORE extraction/persistence — memory stores only
        # mlpal-secret:// references, never plaintext credentials (design-proposal §2.3).
        vault = get_vault()
        episode.content, content_refs = redact(episode.content, vault)
        episode.payload, payload_refs = redact_mapping(episode.payload, vault)
        secret_refs = content_refs + payload_refs
        if secret_refs:
            log.info("redaction.applied", event_id=episode.event_id, secrets=len(secret_refs))

        # DIRECT tier: when the episode carries content, store it verbatim as retrievable
        # chunks (post-scrub) — the citeable ground truth the derived facts are inferred from.
        direct_doc = 0
        if episode.content:
            doc = await self.direct.add_document(
                session,
                tenant_id=tenant_id,
                scope=scope,
                content=episode.content,
                title=(episode.payload or {}).get("title") or episode.source_ref,
                uri=(episode.payload or {}).get("uri"),
                source=episode.source,
                source_ref=episode.source_ref or episode.event_id,
                workspace=episode.workspace,
                valid_at=episode.occurred_at,  # bitemporal: content's event time
            )
            direct_doc = 1 if doc is not None else 0

        # DERIVED tier: rule extraction always runs (the cheap, deterministic backbone). For the
        # 'full' tier, the LLM extractor mines the (already-redacted) content and merges in — both
        # produce the same (entities, edges, invalidations) shape. The read path stays LLM-free.
        extraction = extract(episode)
        if self.llm_enabled and tier == "llm" and episode.content:
            llm_ex = await self.llm_extractor.extract(episode, reference_time=episode.occurred_at)
            extraction.entities.extend(llm_ex.entities)
            extraction.edges.extend(llm_ex.edges)
            extraction.invalidations.extend(llm_ex.invalidations)
        # watched-value pass (x6/x6b/x6c): runs on the redacted content; HAS_VALUE is
        # functional so the existing supersession machinery maintains "the current
        # value" bitemporally. Tiered: pattern (free, deterministic) | llm (precision
        # — the pattern tier's ceiling was measured across three x6c retune rounds).
        from ..core.config import get_settings as _gs
        from .value_facts import extract_value_specs, llm_extract_value_specs

        if _gs().value_extractor == "llm":
            v_entities, v_edges = await llm_extract_value_specs(episode.content)
        else:
            v_entities, v_edges = extract_value_specs(episode.content)
        extraction.entities.extend(v_entities)
        extraction.edges.extend(v_edges)
        # v3 distillation: session documents get ONE insight-distillation pass (typed
        # Convention/Decision/Gotcha/HowTo/Preference nodes with evidence spans). Cost is
        # bounded by design: per session, never per turn; only for distill_sources.
        if (
            self.llm_enabled
            and episode.action_type == "document.ingested"
            and episode.content
            and episode.source in get_settings().distill_sources.split(",")
        ):
            from .distiller import get_distiller

            distilled = await get_distiller().distill(episode)
            extraction.entities.extend(distilled.entities)
            extraction.edges.extend(distilled.edges)

        # v3 lifecycle: working-tier memories are session-scoped and TTL'd; committed are
        # durable. A committed re-observation PROMOTES a working memory (never the reverse —
        # a working session must not downgrade durable knowledge).
        is_working = (episode.lifecycle or "committed") == "working"
        working_expiry = (
            episode.occurred_at + timedelta(days=get_settings().working_ttl_days)
            if is_working
            else None
        )

        def _stamp(obj) -> None:
            refs = list(obj.derived_from or [])
            newly_created = not refs
            if episode.event_id not in refs:
                refs.append(episode.event_id)
            obj.derived_from = refs
            if obj.workspace is None:  # first-learned facet, like source provenance
                obj.workspace = episode.workspace
            if newly_created:
                if is_working:
                    obj.status = "working"
                    obj.expires_at = working_expiry
            else:
                # re-observation: bump the signal counter (nodes only) and promote
                # working → committed when a durable episode re-asserts it.
                if hasattr(obj, "observed_count"):
                    obj.observed_count = (obj.observed_count or 1) + 1
                if not is_working and obj.status == "working":
                    obj.status = "committed"
                    obj.expires_at = None

        node_map: dict[tuple[str, str], object] = {}
        for ent in extraction.entities:
            embedding = None
            if ent.type == "Fact":
                embedding = await self.embedder.embed_one(ent.name)
            node = await self.resolver.resolve_entity(
                session, tenant_id, scope, ent, embedding, source=episode.source
            )
            _stamp(node)
            node_map[(ent.type, ent.key)] = node

        edge_count = 0
        judged = 0
        for e in extraction.edges:
            src = node_map.get((e.src_type, e.src_key))
            dst = node_map.get((e.dst_type, e.dst_key))
            if src is None or dst is None:
                continue
            # embed the normalized fact sentence → semantic contradiction candidates (D3).
            normalized = normalize_fact(e.fact)
            fact_embedding = await self.embedder.embed_one(normalized) if normalized else None
            edge = await self.driver.upsert_edge(
                session,
                tenant_id=tenant_id,
                scope=scope,
                type_=e.type,
                src_id=src.id,
                dst_id=dst.id,
                fact=e.fact,
                props=e.props,  # LLM provenance (evidence_span, versions, confidence); {} for rule
                valid_at=episode.occurred_at,
                source=episode.source,
                fact_embedding=fact_embedding,
                embedding_model=self.embedder.name,
                embedding_dim=self.embedder.dim,
            )
            _stamp(edge)
            if e.functional:
                # functional + diff-dst + overlap → deterministic auto-invalidate (D1, no LLM)
                await self.driver.invalidate_superseded(
                    session, tenant_id=tenant_id, scope=scope, new_edge=edge
                )
                if e.type == "HAS_VALUE":
                    # current-view fact hygiene: a value node whose HAS_VALUE edge is
                    # closed is SUPERSEDED — packets must stop serving it as a fact
                    # while as-of reads still reconstruct it via edge validity.
                    # Set-based + idempotent; also covers the backfill case (the NEW
                    # edge got bounded → its own dst is marked).
                    from sqlalchemy import select as _sel
                    from sqlalchemy import update as _upd

                    from ..db.models import Edge as _E
                    from ..db.models import Node as _N

                    open_dsts = _sel(_E.dst_id).where(
                        _E.src_id == edge.src_id,
                        _E.type == "HAS_VALUE",
                        _E.invalid_at.is_(None),
                    )
                    closed_dsts = _sel(_E.dst_id).where(
                        _E.src_id == edge.src_id,
                        _E.type == "HAS_VALUE",
                        _E.invalid_at.isnot(None),
                    )
                    await session.execute(
                        _upd(_N)
                        .where(
                            _N.id.in_(closed_dsts),
                            _N.id.notin_(open_dsts),  # a re-observed value is NOT superseded
                            _N.status != "superseded",
                        )
                        .values(status="superseded")
                    )
                    await session.execute(  # re-observation reopens a formerly-superseded value
                        _upd(_N)
                        .where(_N.id.in_(open_dsts), _N.status == "superseded")
                        .values(status="committed")
                    )
            elif self.llm_enabled:
                # non-functional/ambiguous → LLM contradiction judge over similar candidates (D1)
                judged += await judge_and_invalidate(
                    session,
                    self.driver,
                    self.judge,
                    tenant_id=tenant_id,
                    scope=scope,
                    new_edge=edge,
                    fact_embedding=fact_embedding,
                )
            edge_count += 1

        # explicit ends: a stative relation that was terminated (e.g. a member removed) — close
        # the matching open edge at the episode time. Invalidate-not-delete; history is preserved.
        ended = 0
        for inv in extraction.invalidations:
            src = node_map.get((inv.src_type, inv.src_key))
            dst = node_map.get((inv.dst_type, inv.dst_key))
            if src is None or dst is None:
                continue
            ended += await self.driver.invalidate_edge(
                session,
                tenant_id=tenant_id,
                scope=scope,
                type_=inv.type,
                src_id=src.id,
                dst_id=dst.id,
                at=episode.occurred_at,
            )

        episode.processed = True
        episode.processed_at = datetime.now(UTC)
        episode.error = None
        # per-ingest observability: entities/facts written, edges invalidated, contradictions found
        log.info(
            "memory.fold",
            event_id=episode.event_id,
            tier=tier,
            source=episode.source,
            nodes=len(node_map),
            edges=edge_count,
            invalidated=ended,
            contradictions=judged,
            direct_docs=direct_doc,
        )
        return {
            "tier": tier,
            "nodes": len(node_map),
            "edges": edge_count,
            "ended": ended + judged,
            "contradictions": judged,
            "direct_docs": direct_doc,
        }
