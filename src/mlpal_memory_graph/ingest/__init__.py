"""Ingestion: the generic episode envelope and the Source → Memory plugin model."""

from .envelope import Actor, EpisodeEnvelope, Subject
from .sources import (
    SOURCE_REGISTRY,
    SourceCapabilities,
    SourceConfig,
    SourceItem,
    SourcePlugin,
    register_source,
    source_item_to_envelope,
)

__all__ = [
    "Actor",
    "EpisodeEnvelope",
    "Subject",
    "SOURCE_REGISTRY",
    "SourceCapabilities",
    "SourceConfig",
    "SourceItem",
    "SourcePlugin",
    "register_source",
    "source_item_to_envelope",
]
