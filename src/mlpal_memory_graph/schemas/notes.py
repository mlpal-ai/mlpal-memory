"""Workspace notes API schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class NoteOut(BaseModel):
    id: str
    scope: str
    scope_id: str
    workspace: str
    title: str | None = None
    body: str
    sections: dict[str, str]
    version: int
    updated_by: str | None = None
    updated_at: datetime | None = None
    as_of: datetime | None = None
    stale_citations: list[dict] = Field(default_factory=list)


class NoteSummary(BaseModel):
    id: str
    scope: str
    scope_id: str
    workspace: str
    title: str | None = None
    version: int
    updated_by: str | None = None
    updated_at: datetime | None = None
    chars: int


class NoteListOut(BaseModel):
    notes: list[NoteSummary]


class NotePut(BaseModel):
    body: str = Field(..., description="Whole note: '## Now', '## Decisions', '## Open threads', '## Preferences', '## Pointers'")
    reason: str | None = Field(None, max_length=512)
    base_version: int | None = Field(None, description="optimistic concurrency: current version you read")
    title: str | None = Field(None, max_length=256)


class NotePatch(BaseModel):
    section: str
    op: str = Field("append", pattern="^(append|replace)$")
    text: str
    reason: str | None = Field(None, max_length=512)
    base_version: int | None = None


class NoteVersionOut(BaseModel):
    version: int
    updated_by: str | None = None
    reason: str | None = None
    created_at: datetime | None = None
    chars: int


class NoteHistoryOut(BaseModel):
    note_id: str
    versions: list[NoteVersionOut]


class NotesContextOut(BaseModel):
    markdown: str
    estimated_tokens: int
    notes: list[dict]
    truncated: bool
