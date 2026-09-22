"""Sources API shapes (memory v7 WP4)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class RegisterSourceRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    kind: str = Field(..., pattern="^(files|sql)$")
    # files: a directory under the sources root; sql: a read-only connection URL + table allow-list
    root: str | None = None
    url: str | None = None
    tables: list[str] = []
    row_limit: int = Field(200, ge=1, le=5000)
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
