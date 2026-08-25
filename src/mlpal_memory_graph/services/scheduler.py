"""SourceScheduler — the pull loop v1 declared but never wired.

For each *incremental* source it tails ``list_items(since=watermark)``, maps items to
episode envelopes, inserts them idempotently, and advances the per-source watermark. Poll vs
webhook vs backfill is chosen from ``capabilities()`` — no per-source special-casing. Drains
into the same append-only ``episodes`` table the fold worker already processes. See §3.2.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..core.config import get_settings
from ..core.logging import get_logger
from ..db.models import MemoryWatermark
from ..ingest.sources import (
    SOURCE_REGISTRY,
    SourceConfig,
    SourcePlugin,
    source_item_to_envelope,
)
from ..repositories.episodes import insert_episode

log = get_logger(__name__)


def _expand_env(value):
    """Expand ``${VAR}`` references in string option values (e.g. ``read_dsn``) from the
    environment, so secrets live in env/secret-manager and never in the committed YAML. An UNSET
    referenced var resolves the whole value to None — for ``read_dsn`` that makes the source a
    configured no-op (the plugins' existing missing-DSN behavior), with a loud warning."""
    if not isinstance(value, str):
        return value
    import os
    import re

    out = value
    for var in re.findall(r"\$\{(\w+)\}", value):
        env_val = os.environ.get(var)
        if env_val is None:
            log.warning("sources.env_unset", var=var)
            return None
        out = out.replace(f"${{{var}}}", env_val)
    return out


def load_source_configs() -> list[SourceConfig]:
    """Read enabled sources from the YAML config (MLPAL_SOURCES_CONFIG_PATH); [] if unset."""
    path = get_settings().sources_config_path
    if not path:
        return []
    import yaml  # lazy: only needed when sources are configured

    with open(path) as f:
        raw = yaml.safe_load(f) or {}
    reserved = {"type", "enabled", "default_scope"}
    return [
        SourceConfig(
            type=s["type"],
            enabled=s.get("enabled", True),
            default_scope=s.get("default_scope", "org"),
            options={k: _expand_env(v) for k, v in s.items() if k not in reserved},
        )
        for s in (raw.get("sources") or [])
    ]


class SourceScheduler:
    def __init__(self, configs: list[SourceConfig], batch_size: int | None = None) -> None:
        s = get_settings()
        self.batch_size = batch_size or s.updater_batch_size
        self.capture_content = s.content_capture_default
        self.sources: list[tuple[SourcePlugin, SourceConfig]] = []
        for cfg in configs:
            if not cfg.enabled:
                continue
            if cfg.type not in SOURCE_REGISTRY:
                raise ValueError(
                    f"unknown source type {cfg.type!r}; known: {sorted(SOURCE_REGISTRY)}"
                )
            self.sources.append((SOURCE_REGISTRY[cfg.type](cfg), cfg))

    async def poll_once(self, session) -> dict[str, int]:
        """One scheduler tick over all incremental sources. Returns per-source ingest counts."""
        results: dict[str, int] = {}
        for plugin, cfg in self.sources:
            if not plugin.capabilities().incremental:
                continue  # push/webhook-only sources POST episodes directly
            results[cfg.type] = await self._drain_one(session, plugin, cfg)
        return results

    async def _drain_one(self, session, plugin: SourcePlugin, cfg: SourceConfig) -> int:
        stype = plugin.source_type()
        wm = await session.get(MemoryWatermark, stype)
        since = wm.last_processed_ref if wm else None

        items, cursor = await plugin.list_items(since, self.batch_size)
        ingested = 0
        for item in items:
            env = source_item_to_envelope(item, source_type=stype, default_scope=cfg.default_scope)
            kwargs = env.to_episode_kwargs(capture_content=self.capture_content)
            if await insert_episode(session, kwargs):
                ingested += 1

        if wm is None:
            wm = MemoryWatermark(source=stype)
            session.add(wm)
        wm.last_processed_ref = cursor
        wm.last_processed_at = datetime.now(UTC)
        await session.flush()

        if ingested:
            log.info("source.items_ingested", type=stype, count=ingested)
        return ingested
