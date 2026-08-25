"""Select a GraphDriver from settings (MLPAL_GRAPH_DRIVER)."""

from __future__ import annotations

from functools import lru_cache

from ..core.config import get_settings
from .driver import GraphDriver
from .drivers.postgres import PostgresDriver
from .drivers.stubs import AgeDriver, FalkorDriver, GraphitiDriver, Neo4jDriver

_REGISTRY: dict[str, type[GraphDriver]] = {
    "postgres": PostgresDriver,
    "age": AgeDriver,
    "falkordb": FalkorDriver,
    "neo4j": Neo4jDriver,
    "graphiti": GraphitiDriver,
}


@lru_cache
def get_driver(name: str | None = None) -> GraphDriver:
    name = name or get_settings().graph_driver
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise ValueError(
            f"Unknown MLPAL_GRAPH_DRIVER={name!r}. Options: {sorted(_REGISTRY)}"
        ) from None
