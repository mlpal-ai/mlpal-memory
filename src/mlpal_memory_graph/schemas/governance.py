"""Request/response schemas for consent (opt-out) and extraction-policy administration."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

from ..core.scope import Scope

ConsentAction = Literal["active", "off", "pause", "clear"]


class ConsentRequest(BaseModel):
    scope: Scope
    scope_id: str
    state: ConsentAction


class ConsentResponse(BaseModel):
    scope: str
    scope_id: str
    state: str  # the stored state (clear collapses to 'off' after purge)
    purged_nodes: int = 0
    purged_edges: int = 0
    purged_documents: int = 0
    purged_chunks: int = 0


class PolicyRequest(BaseModel):
    scope: Scope
    scope_id: str
    deny_sources: list[str] = []
    allow_sources: list[str] | None = None
    metadata_deny: dict[str, list[str]] = {}


class PolicyResponse(BaseModel):
    scope: str
    scope_id: str
    version: int
