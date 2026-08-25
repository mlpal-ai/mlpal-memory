"""Response schemas for memory retrieval."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class NodeOut(BaseModel):
    id: str
    type: str
    key: str
    name: str
    summary: str | None = None
    score: float = 0.0
    props: dict = {}
    scope: str = "org"
    scope_id: str | None = None
    # broader scopes that held the same fact and were shadowed by this (narrower) copy
    also_known_at: list[str] = []
    # this is DERIVED (inferred) memory; provenance links/score it back to direct memory
    origin: str = "derived"
    confidence: float | None = None
    # v3: lifecycle + facet + disagreement surfacing
    status: str = "committed"
    workspace: str | None = None
    contested: bool = False
    observed_count: int = 1
    derived_from: list[str] = []


class EdgeOut(BaseModel):
    id: str
    type: str
    src_id: str
    dst_id: str
    fact: str | None = None
    valid_at: datetime | None = None
    invalid_at: datetime | None = None
    scope: str = "org"
    scope_id: str | None = None


class PassageOut(BaseModel):
    """A DIRECT-memory passage — a verbatim, citeable chunk of stored content."""

    id: str
    document_id: str
    content: str
    score: float = 0.0
    ordinal: int = 0
    scope: str = "org"
    scope_id: str | None = None
    source: str | None = None
    origin: str = "direct"
    # v3: parent-document context so citations resolve without a second call
    workspace: str | None = None
    document_uri: str | None = None
    document_title: str | None = None
    valid_at: datetime | None = None


class SearchResponse(BaseModel):
    nodes: list[NodeOut]  # derived (inferred) facts
    edges: list[EdgeOut]
    passages: list[PassageOut] = []  # direct (verbatim) memory


class ProjectionResponse(BaseModel):
    """The always-on Markdown memory tier, rendered from the graph and budget-capped (M7)."""

    markdown: str
    estimated_tokens: int
    fact_count: int
    truncated: bool


class ExplainResponse(BaseModel):
    """The resolution trace for a query — which scopes were considered, what was deduped."""

    query: str | None = None
    accessible_scopes: list[str]
    requested_scope: str | None = None
    per_scope_hits: dict[str, int]
    candidates: int
    merged: int
    shadowed: list[dict]
    results: list[NodeOut]


class PublishRequest(BaseModel):
    """Promote personal (user-scope) memories into a shared scope (v3 lifecycle)."""

    node_ids: list[str]
    scope: str = "org"  # target: org | team
    scope_id: str | None = None  # defaults to the caller's org for org scope


class ContentionOut(BaseModel):
    published_id: str
    conflicts_with_id: str
    fact: str


class PublishResponse(BaseModel):
    published: int
    merged: int  # identical fact already shared → observed_count bump, no new node
    contentions: list[ContentionOut] = []


class AnswerResponse(BaseModel):
    """A memory packet: the system's designed answer format (markdown, llms.txt-style,
    citations to memory:// ids, explicit gaps) + a structured summary."""

    query: str
    markdown: str
    facts: int
    passages: int
    contested: int
    gaps: list[str] = []
    top_fact_id: str | None = None
    took_ms: int


class StoreStats(BaseModel):
    """Store composition for the UI: counts by scope/source/status/workspace."""

    documents: int
    chunks: int
    nodes: int
    edges: int
    episodes: int
    by_scope: dict[str, int] = {}
    by_source: dict[str, int] = {}
    by_status: dict[str, int] = {}
    top_workspaces: list[dict] = []
    contested: int = 0
    # active embedding space {name, quality, dim} — evals record it so every number
    # is attributable to the space that produced it (D2)
    embedder: dict = {}
