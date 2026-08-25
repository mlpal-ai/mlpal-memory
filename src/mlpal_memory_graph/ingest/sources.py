"""Source → Memory plugin model.

A **Source** is anything we save raw signal from (a Claude Code conversation, a GitHub PR,
a doc, a deploy event). A :class:`SourcePlugin` normalizes it into thin :class:`SourceItem`s;
a single mapper turns those into the existing :class:`EpisodeEnvelope`, so the whole fold
pipeline downstream is reused unchanged. **Everything downstream branches on metadata, never
on source type** — that decoupling is what makes adding a new source trivial:

    @register_source
    class MySource:
        @classmethod
        def source_type(cls) -> str: return "my_source"
        @classmethod
        def capabilities(cls) -> SourceCapabilities: return SourceCapabilities(incremental=True)
        def __init__(self, config: SourceConfig) -> None: ...
        async def list_items(self, since, limit): ...   # tail since the watermark

See design-proposal §3.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol, runtime_checkable

from .envelope import Actor, EpisodeEnvelope, Subject

Checkpoint = str | None  # opaque per-source cursor (a timestamp, id, or token)


@dataclass(frozen=True)
class SourceItem:
    """One raw artifact from a source. ``content`` is None for metadata-only items
    (the privacy default — content capture is opt-in)."""

    source_id: str  # stable per-artifact id; the dedup/idempotency key
    occurred_at: datetime
    content: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SourceCapabilities:
    incremental: bool = False  # supports `since` checkpoint tailing (pull loop)
    streaming: bool = False
    webhooks: bool = False
    rich_structure: bool = False  # preserves threads/comments


@dataclass(frozen=True)
class SourceConfig:
    type: str
    enabled: bool = True
    default_scope: str = "org"  # where items land unless metadata says otherwise
    options: dict = field(default_factory=dict)


@runtime_checkable
class SourcePlugin(Protocol):
    @classmethod
    def source_type(cls) -> str: ...

    @classmethod
    def capabilities(cls) -> SourceCapabilities: ...

    def __init__(self, config: SourceConfig) -> None: ...

    async def list_items(
        self, since: Checkpoint, limit: int
    ) -> tuple[list[SourceItem], Checkpoint]:
        """Return (items after ``since``, advanced checkpoint). Pull sources implement this;
        push/webhook-only sources declare ``incremental=False`` and POST episodes directly."""
        ...


SOURCE_REGISTRY: dict[str, type[SourcePlugin]] = {}


def register_source(cls: type[SourcePlugin]) -> type[SourcePlugin]:
    """Class decorator — register a plugin under its ``source_type()``."""
    SOURCE_REGISTRY[cls.source_type()] = cls
    return cls


def source_item_to_envelope(
    item: SourceItem, *, source_type: str, default_scope: str, tenant_id: str | None = None
) -> EpisodeEnvelope:
    """Map a normalized item onto the generic episode envelope. Scope/actor/subject/action
    come from item metadata (never from the source *type*), with sensible fallbacks."""
    md = item.metadata or {}
    payload = md.get("payload") or ({"statement": item.content} if item.content else {})
    return EpisodeEnvelope(
        event_id=f"{source_type}:{item.source_id}",
        occurred_at=item.occurred_at,
        org_id=md.get("org_id") or tenant_id,
        scope=md.get("scope") or default_scope,
        scope_id=md.get("scope_id"),
        actor=Actor(**(md.get("actor") or {})),
        source=source_type,
        action_type=md.get("action_type") or "fact.observed",
        subject=Subject(**(md.get("subject") or {})),
        payload=payload,
        content=item.content,
        source_ref=f"{source_type}:{item.source_id}",
    )
