"""The single generic event envelope every MLPal interaction is normalized into.

Field names deliberately mirror the platform's existing SkillLoadEvent / ToolCallMetrics
so the watermark-tail adapters map 1:1. Any service, CI step, git hook or MCP tool can POST
this shape — that is the "easy to plug in anywhere" surface.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(UTC)


class Actor(BaseModel):
    user_id: str | None = None
    key_id: str | None = None
    team_id: str | None = None


class Subject(BaseModel):
    project_id: str | None = None
    agent_id: str | None = None
    chat_id: str | None = None
    server_id: str | None = None
    skill_id: str | None = None
    artifact_ref: str | None = None
    tool_name: str | None = None
    target_user_id: str | None = None  # e.g. the member added/removed in an org audit event


class EpisodeEnvelope(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = Field(default_factory=_now)
    org_id: str | None = None  # tenant boundary
    # target hierarchy layer for this episode's extracted memory (default: org-wide).
    scope: str = "org"  # global|org|team|service|repo|agent|user (core.scope.Scope)
    scope_id: str | None = None  # the team_id/user_id; defaults to org_id for org scope
    # v3: workspace facet — which repo/project (inside the personal store) produced this.
    # Focuses retrieval ("me, in repo X"); never an authz surface.
    workspace: str | None = None
    # v3: lifecycle of memories derived from this episode. "committed" (default, durable) |
    # "working" (session-scoped, TTL'd — the watcher's per-turn stream uses this).
    lifecycle: str = "committed"
    actor: Actor = Field(default_factory=Actor)
    source: str = "external"  # backend|assistants|mcp|skills|cde|devops|external
    action_type: str = "fact.observed"
    subject: Subject = Field(default_factory=Subject)
    payload: dict = Field(default_factory=dict)
    content: str | None = None  # raw text; only persisted when content capture is enabled
    source_ref: str | None = None
    schema_version: int = 1

    def to_episode_kwargs(self, *, capture_content: bool) -> dict:
        """Map the envelope onto Episode ORM column kwargs.

        Defaults ``scope_id`` to ``org_id`` for org-scoped episodes so the common
        (flat, org-wide) case needs no extra fields from producers.
        """
        scope_id = self.scope_id
        if scope_id is None and self.scope == "org":
            scope_id = self.org_id
        # global memory is org-less by design; every other scope must name its subject, or the
        # episode would land under (scope, NULL) and be unreachable. Fail loud at this boundary.
        if scope_id is None and self.scope != "global":
            raise ValueError(
                f"episode {self.event_id!r}: scope {self.scope!r} requires a scope_id "
                "(only 'global' may omit it)"
            )
        return {
            "event_id": self.event_id,
            "occurred_at": self.occurred_at,
            "org_id": self.org_id,
            "scope": self.scope,
            "scope_id": scope_id,
            "workspace": self.workspace,
            "lifecycle": self.lifecycle,
            "actor": self.actor.model_dump(exclude_none=True),
            "source": self.source,
            "action_type": self.action_type,
            "subject": self.subject.model_dump(exclude_none=True),
            "payload": self.payload,
            "content": self.content if capture_content else None,
            "source_ref": self.source_ref,
            "schema_version": self.schema_version,
        }
