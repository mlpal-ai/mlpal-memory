"""Request/response schemas for direct-memory (document) ingestion."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..core.scope import Scope


class DocumentIngestRequest(BaseModel):
    # optional client-supplied id → idempotent re-posts dedup server-side (collectors use
    # a content hash, so an unchanged file can never double-ingest even across state loss)
    event_id: str | None = None
    content: str = Field(..., min_length=1)  # verbatim text to store as direct memory
    title: str | None = None
    scope: Scope = Scope.ORG
    scope_id: str | None = None  # required for non-org scopes; defaults to the tenant for org
    source: str = "document"
    uri: str | None = None  # canonical locator (file path, URL) — stored on the Document
    # v3: workspace facet + bitemporal event-time (when the content was written/true —
    # a 2024 design doc ingested today gets valid_at=2024; staleness ranking depends on it).
    workspace: str | None = None
    valid_at: datetime | None = None


class DocumentIngestResponse(BaseModel):
    event_id: str
    scope: str
    scope_id: str | None
    status: str  # "processed" | "consent_blocked" | "policy_dropped"


class DocumentOut(BaseModel):
    id: str
    title: str | None
    uri: str | None
    source: str | None
    scope: str
    scope_id: str | None
    workspace: str | None
    classification: str
    valid_at: datetime | None
    ingested_at: datetime
    chunks: int = 0


class DocumentListResponse(BaseModel):
    documents: list[DocumentOut]
    total: int
    limit: int
    offset: int


class ChunkOut(BaseModel):
    id: str
    ordinal: int
    content: str
    embedding_model: str | None


class DocumentDetailResponse(DocumentOut):
    chunk_contents: list[ChunkOut] = []
