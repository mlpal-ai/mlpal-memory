"""HOP registry, routing, brief, departure (memory v7)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class HopRegisterRequest(BaseModel):
    version: str | None = None
    owner: str | None = None
    mode: str | None = Field(None, pattern="^(auto|review|manual)$")
    sharing: str | None = Field(None, pattern="^(none|org|fleet)$")
    tenant: str | None = None
    workspace: str | None = None
    topics: list[dict] = []
    reads: list[str] = []
    writes: list[str] = []
    ontology: list[dict] = []


class HopOut(BaseModel):
    name: str
    version: str | None
    owner: str | None
    mode: str
    sharing: str
    tenant: str | None
    workspace: str | None
    topics: list[dict]
    reads: list[str]
    writes: list[str]
    ontology: list[dict]


class HopListResponse(BaseModel):
    hops: list[HopOut]
    ontology_extension: dict[str, dict]


class RouteResponse(BaseModel):
    question: str
    routes: list[dict]


class BriefResponse(BaseModel):
    hop: str
    registry: dict | None
    build_topics: list[str]
    build_state: list[dict]
    build_learnings: list[dict]
    scores: list[dict]
    deviations: dict
    runs: dict
    company: list[dict]
    fleet: list[dict]
    markdown: str


class DepartRequest(BaseModel):
    user_id: str = Field(..., min_length=1)
    reason: str | None = Field(None, max_length=300)


class FleetResponse(BaseModel):
    facts: list[dict]


# ---- memory v10: tune proposals — the owner's door on the tuning loop

class TuneProposalIn(BaseModel):
    """What a tune turn writes when it has a candidate (or an advisory set) for the owner: the
    record the engine lists as "pending your approval" and opens for review."""

    hop: str
    turn: str                              # the turn id (turn-YYYYMMDD…)
    from_version: str
    candidate_version: str | None = None   # None when the turn only has advisory proposals
    candidate_path: str | None = None      # where the candidate artifact lives on the owner's machine
    twins: list[str] = []                  # candidate artifacts of the HOP's variants (the read-only twin), same knob changes
    summary: str                           # one paragraph a person reads first
    proposals: list[dict] = []             # hop_proposer records (knob, change, rationale, applicability, evidence)
    verdict: dict | None = None            # the golden-suite verdict for the candidate, when it ran
    facts: list[dict] = []                 # the distilled facts the proposals cite (key, value)
    cost_usd: float | None = None


class TuneProposalOut(TuneProposalIn):
    id: str
    at: str
    actor: str | None = None
    status: str                            # pending | approved | rejected | edited
    decision: dict | None = None


class TunePendingResponse(BaseModel):
    pending: list[TuneProposalOut]


class TuneDecisionIn(BaseModel):
    turn: str
    hop: str
    decision: str                          # approve | reject | edit
    reason: str = ""                       # required for reject; becomes a build learning the next turn reads
    installed_version: str | None = None   # what the engine installed on approve


class TuneDecisionOut(BaseModel):
    turn: str
    hop: str
    status: str
    learning_written: bool
