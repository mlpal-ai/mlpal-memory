"""Swap-in driver stubs. Each documents how it would be implemented; instantiating one
raises until implemented, so misconfiguration fails loudly rather than silently.
"""

from __future__ import annotations

from ..driver import GraphDriver


class _UnimplementedDriver(GraphDriver):
    name = "unimplemented"
    _how = ""

    def __init__(self) -> None:
        raise NotImplementedError(
            f"GraphDriver '{self.name}' is not implemented in v1. "
            f"Use MLPAL_GRAPH_DRIVER=postgres (default). Planned: {self._how}"
        )

    async def find_node(self, *a, **k): ...  # pragma: no cover
    async def get_node(self, *a, **k): ...  # pragma: no cover
    async def upsert_node(self, *a, **k): ...  # pragma: no cover
    async def upsert_edge(self, *a, **k): ...  # pragma: no cover
    async def invalidate_superseded(self, *a, **k): ...  # pragma: no cover
    async def invalidate_edge(self, *a, **k): ...  # pragma: no cover
    async def search_nodes(self, *a, **k): ...  # pragma: no cover
    async def lexical_search_nodes(self, *a, **k): ...  # pragma: no cover
    async def find_node_ids(self, *a, **k): ...  # pragma: no cover
    async def nodes_with_valid_edges(self, *a, **k): ...  # pragma: no cover
    async def graph_distance(self, *a, **k): ...  # pragma: no cover
    async def search_fact_edges(self, *a, **k): ...  # pragma: no cover
    async def neighbors(self, *a, **k): ...  # pragma: no cover
    async def purge_scope(self, *a, **k): ...  # pragma: no cover


class AgeDriver(_UnimplementedDriver):
    name = "age"
    _how = "openCypher via Apache AGE in the same Postgres; nodes/edges as AGE vertices."


class FalkorDriver(_UnimplementedDriver):
    name = "falkordb"
    _how = "FalkorDB (RESP/Bolt), one graph per org_id, vector-in-Cypher hybrid search."


class Neo4jDriver(_UnimplementedDriver):
    name = "neo4j"
    _how = "Neo4j 5.26+ with native vector index; per-org database or label partition."


class GraphitiDriver(_UnimplementedDriver):
    name = "graphiti"
    _how = "delegate to the upstream Graphiti engine (Kuzu/FalkorDB/Neo4j backend)."
