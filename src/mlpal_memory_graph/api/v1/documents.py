"""Direct-memory ingestion — store a document/conversation/PDF verbatim and retrievable.

A document is ingested as a content-bearing episode so it flows through the SAME governed fold
as everything else (consent gate → policy → secret-scrub), then lands as Document + embedded
Chunks (direct tier) plus any inferred entities (derived tier). See design-proposal §14.
"""

from __future__ import annotations

import asyncio

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
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
from ...core.config import get_settings
from ...core.logging import get_logger
from ...services.metrics import REGISTRY
from ...services.resilience import ModelUnavailable
from ..deps import AuthIdentity, authorize_write_scope, get_updater, require_permission

log = get_logger(__name__)
router = APIRouter(prefix="/documents", tags=["documents"])


_ingest_slots: asyncio.Semaphore | None = None


def ingest_slots() -> asyncio.Semaphore:
    """memory v9 round 3: the synchronous fold runs inside the request, so the number in flight is
    bounded per process; past the bound the caller gets 503 + Retry-After at once rather than a
    queue that grows until Postgres gives up (three crash recoveries on the laptop, 2026-09-18)."""
    global _ingest_slots
    if _ingest_slots is None:
        _ingest_slots = asyncio.Semaphore(max(1, int(get_settings().ingest_concurrency)))
    return _ingest_slots


def reset_ingest_slots() -> None:  # tests
    global _ingest_slots
    _ingest_slots = None


@router.post("", status_code=202, response_model=DocumentIngestResponse)
async def ingest_document(
    request: Request,
    body: DocumentIngestRequest,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> DocumentIngestResponse:
    # Same hard gate as episodes: non-privileged callers write only their own USER scope
    # or ORG; subject scopes (team/repo/service/agent) require service key or org admin.
    authorize_write_scope(identity, body.scope.value, body.scope_id, hop=request.headers.get("x-hop"))

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
    slots = ingest_slots()
    if slots.locked():
        REGISTRY.inc("ingest_rejected", reason="saturated")
        # the episode is stored (processed=false): the worker folds it later; the caller may also retry
        await session.commit()
        raise HTTPException(status_code=503, detail="ingest saturated; the document is queued for the worker",
                            headers={"Retry-After": "2"})
    async with slots:
        try:
            result = await get_updater().process_episode(session, episode)
        except ModelUnavailable as exc:
            # memory v9 round 3: a model outage is not a bad document — keep the episode for the
            # worker (which defers while the breaker is open) and tell the caller it is queued
            await session.rollback()
            await insert_episode(session, kwargs)
            await session.commit()
            REGISTRY.inc("ingest_queued", reason=exc.reason)
            log.warning("ingest.queued", event_id=env.event_id, client=exc.client, reason=exc.reason)
            return DocumentIngestResponse(event_id=env.event_id, scope=env.scope, scope_id=env.scope_id, status="queued", reason=str(exc)[:160])

    status = "processed"
    reason = result.get("dropped")
    if reason:
        if reason.startswith("consent"):
            status = "consent_blocked"
        elif reason.startswith(("salience:", "budget:")):
            status = "declined"   # memory v7 WP4: below the salience floor or over the source's daily budget
        else:
            status = "policy_dropped"
    return DocumentIngestResponse(
        event_id=env.event_id, scope=episode.scope, scope_id=episode.scope_id, status=status, reason=reason,
        salience=(episode.payload or {}).get("salience") or None,
        timings_ms=result.get("timings_ms") or None,
    )


@router.delete("/{document_id}")
async def forget_document(
    document_id: str,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
) -> dict:
    """Forget one document: hard-delete it and its chunks (direct tier), audited.

    Scope-authorized like any write (own personal scope, or org; subject scopes
    need elevation). Derived facts are NOT auto-deleted (refcounted purge is a
    separate mechanism); what users experience as "memory pollution" is the
    direct tier, and this removes it — with an audit episode, never silently.
    """
    from sqlalchemy import delete as _delete
    from sqlalchemy import select as _select

    from ...ingest.envelope import Actor, EpisodeEnvelope
    from ...repositories.episodes import insert_episode

    doc = (
        await session.execute(
            _select(Document).where(
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
    authorize_write_scope(identity, doc.scope, doc.scope_id)

    purged = await session.execute(_delete(Chunk).where(Chunk.document_id == doc.id))
    await session.execute(_delete(Document).where(Document.id == doc.id))
    # audit: forgetting is a governance event with a durable, content-free record
    env = EpisodeEnvelope(
        org_id=identity.org_id,
        scope=doc.scope,
        scope_id=doc.scope_id,
        workspace=doc.workspace,
        actor=Actor(user_id=identity.user_id),
        source="governance",
        action_type="memory.forgotten",
        payload={"document_id": doc.id, "title": doc.title or "",
                 "purged_chunks": purged.rowcount or 0},
    )
    await insert_episode(session, env.to_episode_kwargs(capture_content=False))
    return {"id": doc.id, "purged_chunks": purged.rowcount or 0, "title": doc.title}


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
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
    q: str | None = Query(None, description="title substring filter"),
    source: str | None = Query(None),
    workspace: str | None = Query(None),
    scope: str | None = Query(None),
    limit: int = Query(25, ge=1, le=100),
    offset: int = Query(0, ge=0),
    order: str = Query(
        "ingested", pattern="^(ingested|valid)$",
        description="ingested (newest first, default) | valid (event-time ascending — "
        "the timeline view's order, so a page spans history instead of yesterday)",
    ),
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
                .order_by(
                    Document.valid_at.asc().nulls_last()
                    if order == "valid"
                    else Document.ingested_at.desc()
                )
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
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.read"))],
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


UPLOAD_MAX_BYTES = 20 * 1024 * 1024
_TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml", ".log")


def _text_of_upload(filename: str, data: bytes) -> str:
    """The verbatim text of an uploaded file: text-like files decoded, PDFs extracted page by
    page (pypdf, the `pdf` extra). Anything else is refused: memory stores text it can cite."""
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        try:
            from io import BytesIO

            from pypdf import PdfReader
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise HTTPException(status_code=415, detail="PDF uploads need the `pdf` extra (pypdf)") from exc
        try:
            pages = [(page.extract_text() or "").strip() for page in PdfReader(BytesIO(data)).pages]
        except Exception as exc:  # noqa: BLE001 - a corrupt file is the caller's problem, said plainly
            raise HTTPException(status_code=422, detail=f"could not read the PDF: {exc}") from exc
        text = "\n\n".join(p for p in pages if p)
        if not text.strip():
            raise HTTPException(status_code=422, detail="the PDF has no extractable text (scanned? run OCR first)")
        return text
    if name.endswith(_TEXT_SUFFIXES) or not name:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=415, detail="text uploads must be UTF-8") from exc
    raise HTTPException(status_code=415, detail=f"unsupported file type: {filename!r} (text, markdown or PDF)")


@router.post("/upload", status_code=202, response_model=DocumentIngestResponse)
async def upload_document(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_session)],
    identity: Annotated[AuthIdentity, Depends(require_permission("memory.write"))],
    file: UploadFile = File(...),
    scope: str = Form("org"),
    scope_id: str | None = Form(None),
    workspace: str | None = Form(None),
    title: str | None = Form(None),
    source: str = Form("upload"),
    valid_at: str | None = Form(None),
) -> DocumentIngestResponse:
    """memory v12 §6: the second door for sources when nobody runs a collector — a file dropped
    in the UI. The same ingest as `POST /documents`; the file's text is the document, its name
    the title and uri when none is given."""
    data = await file.read()
    if len(data) > UPLOAD_MAX_BYTES:
        raise HTTPException(status_code=413, detail=f"file larger than {UPLOAD_MAX_BYTES // (1024 * 1024)} MB")
    if not data:
        raise HTTPException(status_code=422, detail="empty file")
    from datetime import datetime

    body = DocumentIngestRequest(
        content=_text_of_upload(file.filename or "", data),
        title=title or file.filename,
        scope=scope,  # validated by the schema
        scope_id=scope_id,
        source=source,
        uri=file.filename,
        workspace=workspace,
        valid_at=datetime.fromisoformat(valid_at) if valid_at else None,
    )
    return await ingest_document(request, body, session, identity)
