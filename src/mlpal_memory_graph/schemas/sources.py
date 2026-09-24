"""Sources API shapes (memory v7 WP4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterSourceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: str = Field(..., pattern="^(files|sql|github)$")
    # files: a directory under the sources root; sql: a read-only connection URL + table allow-list
    root: str | None = None
    url: str | None = None
    tables: list[str] = []
    row_limit: int = Field(200, ge=1, le=5000)
    # github (memory v12 §6): owner/name, optional branch, path prefixes or globs, the NAME of the
    # environment variable holding the token (never the token), eager or on-demand admission
    repo: str | None = Field(None, pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    branch: str | None = None
    paths: list[str] = []
    credential_ref: str | None = Field(None, pattern=r"^[A-Z][A-Z0-9_]*$")
    admit: str = Field("eager", pattern="^(eager|on-demand)$")
    interval_minutes: int = Field(60, ge=5, le=1440)
    scope: str = "org"
    scope_id: str | None = None
    workspace: str | None = None


class SourceOut(BaseModel):
    name: str
    kind: str
    status: str
    item_count: int
    scope: str
    scope_id: str | None
    workspace: str | None
    tables: list[str] = []
    schema_tables: dict[str, list[str]] | None = None
    indexed_at: str | None = None
    repo: str | None = None
    branch: str | None = None
    admit: str | None = None
    last_sync: dict | None = None
    last_error: str | None = None


class SourceListResponse(BaseModel):
    sources: list[SourceOut]


class PromoteRequest(BaseModel):
    query: str = Field(..., min_length=2)
    limit: int = Field(3, ge=1, le=10)


class PromoteResponse(BaseModel):
    promoted: list[dict]


class SourceQueryRequest(BaseModel):
    sql: str = Field(..., min_length=6, max_length=8000)


class SourceQueryResponse(BaseModel):
    query_id: str
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    tables: list[str]
    took_ms: int
