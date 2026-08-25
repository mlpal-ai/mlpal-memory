"""Request/response schemas for ingestion."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from ..ingest.envelope import EpisodeEnvelope


class IngestRequest(BaseModel):
    episodes: list[EpisodeEnvelope] = Field(..., min_length=1)


class IngestResponse(BaseModel):
    accepted: int
    duplicates: int
    processed: int


class EpisodeOut(BaseModel):
    event_id: str
    occurred_at: datetime
    ingested_at: datetime
    source: str
    action_type: str
    scope: str
    scope_id: str | None
    workspace: str | None
    lifecycle: str
    tier: str | None
    # derived status: pending | processed | dropped | dead
    status: str
    processed_at: datetime | None
    dropped_reason: str | None
    error_count: int = 0
    dead_at: datetime | None = None


class EpisodeDetailResponse(EpisodeOut):
    payload: dict = {}
    error: str | None = None
    has_content: bool = False


class EpisodeListResponse(BaseModel):
    episodes: list[EpisodeOut]
    total: int
    limit: int
    offset: int
