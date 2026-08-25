"""Direct-memory ingestion — store a document/conversation/PDF verbatim and retrievable.

A document is ingested as a content-bearing episode so it flows through the SAME governed fold
as everything else (consent gate → policy → secret-scrub), then lands as Document + embedded
Chunks (direct tier) plus any inferred entities (derived tier). See design-proposal §14.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db import get_session
from ...db.models import Chunk, Document, Episode
from ...db.scoping import browse_clause
from ...ingest.envelope import EpisodeEnvelope
from ...repositories.episodes import insert_episode
from ...schemas.document import (
    ChunkOut,
    DocumentDetailResponse,
    DocumentIngestRequest,
    DocumentIngestResponse,
    DocumentListResponse,
    DocumentOut,
)
from ..deps import AuthIdentity, authorize_write_scope, get_updater, require_permission

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("", status_code=202, response_model=DocumentIngestResponse)
async def ingest_document(
    body: DocumentIngestRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory:write"))],
) -> DocumentIngestResponse:
    # Same hard gate as episodes: non-privileged callers write only their own USER scope
    # or ORG; subject scopes (team/repo/service/agent) require service key or org admin.
    authorize_write_scope(identity, body.scope.value, body.scope_id)

    payload: dict = {}
    if body.title:
        payload["title"] = body.title
    if body.uri:
        payload["uri"] = body.uri
    env = EpisodeEnvelope(
        org_id=identity.org_id,
        scope=body.scope.value,
        scope_id=body.scope_id,
        workspace=body.workspace,
        source=body.source,
        action_type="document.ingested",
        content=body.content,
        payload=payload,
    )
    if body.event_id:
        env.event_id = body.event_id
    if body.valid_at is not None:
        env.occurred_at = body.valid_at  # bitemporal: event-time from the caller
    # documents are content by definition — capture it regardless of the metadata-only default.
    kwargs = env.to_episode_kwargs(capture_content=True)
    inserted = await insert_episode(session, kwargs)
    if not inserted:
        # idempotent re-post of an already-ingested document — never double-fold
        return DocumentIngestResponse(
            event_id=env.event_id, scope=env.scope, scope_id=env.scope_id, status="duplicate"
        )
    episode = await session.get(Episode, env.event_id)
    result = await get_updater().process_episode(session, episode)

    status = "processed"
    if result.get("dropped"):
        status = (
            "policy_dropped" if not result["dropped"].startswith("consent") else "consent_blocked"
        )
    return DocumentIngestResponse(
        event_id=env.event_id, scope=episode.scope, scope_id=episode.scope_id, status=status
    )


def _doc_out(doc: Document, chunk_count: int = 0) -> DocumentOut:
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        uri=doc.uri,
        source=doc.source,
        scope=doc.scope,
        scope_id=doc.scope_id,
        workspace=doc.workspace,
        classification=doc.classification,
        valid_at=doc.valid_at,
        ingested_at=doc.ingested_at,
        chunks=chunk_count,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory:read"))],
    q: str | None = Query(None, description="title substring filter"),
    source: str | None = Query(None),
    workspace: str | None = Query(None),
    scope: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
) -> DocumentListResponse:
    """Browse the direct store (UI listing). Same visibility as retrieval: org-internal
    rows plus the caller's own personal scope; other users' personal docs never appear."""
    where = [
        browse_clause(
            Document,
            tenant_id=identity.org_id,
            user_id=identity.user_id,
            team_ids=tuple(identity.team_ids),
        )
    ]
    if q:
        from ...db.pgvector_support import escape_like

        where.append(Document.title.ilike(f"%{escape_like(q)}%", escape="\\"))
    if source:
        where.append(Document.source == source)
    if workspace:
        where.append(Document.workspace == workspace)
    if scope:
        where.append(Document.scope == scope)

    total = (
        await session.execute(select(func.count()).select_from(Document).where(*where))
    ).scalar_one()
    docs = (
        (
            await session.execute(
                select(Document)
                .where(*where)
                .order_by(Document.ingested_at.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )
    counts: dict[str, int] = {}
    if docs:
        rows = await session.execute(
            select(Chunk.document_id, func.count())
            .where(Chunk.document_id.in_([d.id for d in docs]))
            .group_by(Chunk.document_id)
        )
        counts = dict(rows.all())
    return DocumentListResponse(
        documents=[_doc_out(d, counts.get(d.id, 0)) for d in docs],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{document_id}", response_model=DocumentDetailResponse)
async def get_document(
    document_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory:read"))],
) -> DocumentDetailResponse:
    doc = (
        await session.execute(
            select(Document).where(
                Document.id == document_id,
                browse_clause(
                    Document,
                    tenant_id=identity.org_id,
                    user_id=identity.user_id,
                    team_ids=tuple(identity.team_ids),
                ),
            )
        )
    ).scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    chunks = (
        (
            await session.execute(
                select(Chunk)
                .where(and_(Chunk.document_id == doc.id))
                .order_by(Chunk.ordinal)
            )
        )
        .scalars()
        .all()
    )
    out = _doc_out(doc, len(chunks)).model_dump()
    return DocumentDetailResponse(
        **out,
        chunk_contents=[
            ChunkOut(
                id=c.id, ordinal=c.ordinal, content=c.content, embedding_model=c.embedding_model
            )
            for c in chunks
        ],
    )
