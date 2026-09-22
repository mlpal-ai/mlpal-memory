"""Response schemas for memory retrieval."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


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


class FusedHit(BaseModel):
    """memory v7 WP11: one ranked list across both tiers (facts and verbatim passages), RRF-fused."""

    kind: str  # node | passage
    id: str
    score: float


class SearchResponse(BaseModel):
    nodes: list[NodeOut]  # derived (inferred) facts
    edges: list[EdgeOut]
    passages: list[PassageOut] = []  # direct (verbatim) memory
    fused: list[FusedHit] | None = None  # present when ?fusion=rrf
    took_ms: int | None = None
    timings_ms: dict[str, int] | None = None  # where the read spent its time (derived/direct tiers, embed, legs)
    degraded: list[str] | None = None  # memory v9: legs that fell away for this read ("vector" when the embedder was down)


class ProjectionResponse(BaseModel):
    """The always-on Markdown memory tier, rendered from the graph and budget-capped (M7)."""

    markdown: str
    estimated_tokens: int
    fact_count: int
    truncated: bool
    took_ms: int | None = None


class ProfileResponse(BaseModel):
    """memory v7 WP11: the person's profile in one call — stable facts (preferences, current state
    the HOP injects) and recent activity (what they wrote and read lately, content-free), plus the
    learnings they would see first. The projection is its Markdown rendering."""

    markdown: str
    preferences: list[dict]
    state: list[dict]
    learnings: list[dict]
    recent: list[dict]
    estimated_tokens: int
    took_ms: int


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
    # answer mode: packet (deterministic) | synthesized | hybrid | hop — see x5
    mode: str = "packet"
    synth_model: str | None = None
    synth_ms: int | None = None
    # hop mode: model calls spent + the queries the loop actually executed (audit trace)
    hops: int | None = None
    hop_trace: list[str] | None = None
    # server-enforced grounding: citations stripped because they were never retrieved
    invented_citations: int = 0
    # memory v7 WP4: cold source items admitted to answer this question (promote on demand)
    promoted: list[str] = []
    took_ms: int


class MetricValueOut(BaseModel):
    value: str
    display: str
    valid_at: datetime | None
    invalid_at: datetime | None
    current: bool
    evidence_span: str | None = None


class MetricHistoryOut(BaseModel):
    key: str
    label: str
    workspace: str | None
    values: list[MetricValueOut]


class MetricsResponse(BaseModel):
    """Watched-value histories (timeline UI): every value a metric has held, with
    validity windows — supersession made visible."""

    metrics: list[MetricHistoryOut]


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


class EndorseRequest(BaseModel):
    """memory v6 §6: a person's yes. Rare, decisive; the only signal a nightly join cannot compute."""

    node_ids: list[str]
    withdraw: bool = False  # remove the caller's own endorsement
    pin: bool = False  # memory v10: also pin the memory into every session's projection (withdraw + pin unpins)


class RetractRequest(BaseModel):
    """memory v7 WP3: a person takes a memory back. The fact is closed at now (bitemporal), never
    deleted: the as-of view keeps it, the current view and the packet drop it, a ledger row says who."""

    node_ids: list[str]
    reason: str = Field(..., min_length=3, max_length=500)


class RetractResponse(BaseModel):
    retracted: int
    unchanged: int


class EndorseResponse(BaseModel):
    endorsed: int
    withdrawn: int
    unchanged: int
    tiers: dict[str, str]  # node id -> tier after the change
