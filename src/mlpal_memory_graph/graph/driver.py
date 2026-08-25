"""The ``GraphDriver`` interface — storage is a runtime choice, not a baked-in assumption.

This mirrors Graphiti's key design lesson (a storage-agnostic memory layer). The default
``PostgresDriver`` implements it on the platform's existing Postgres; ``AgeDriver`` /
``FalkorDriver`` / ``Neo4jDriver`` / ``GraphitiDriver`` are swap-ins behind the same API.

Every write is addressed by ``tenant_id`` (the hard multi-tenant boundary) **and** a
:class:`~mlpal_memory_graph.core.scope.ScopeRef` (the hierarchy layer it is owned by).
Every read takes the *set of accessible scopes* and applies it as a hard predicate, so
cross-tenant / cross-scope isolation is enforced in the query, not by the caller.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from ..core.scope import ScopeRef
from ..db.models import Edge, Node


@dataclass
class ScoredNode:
    node: Node
    score: float


class GraphDriver(ABC):
    name: str = "abstract"

    @abstractmethod
    async def find_node(
        self, session: AsyncSession, tenant_id: str | None, scope: ScopeRef, type_: str, key: str
    ) -> Node | None: ...

    @abstractmethod
    async def get_node(self, session: AsyncSession, node_id: str) -> Node | None: ...

    @abstractmethod
    async def upsert_node(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scope: ScopeRef,
        type_: str,
        key: str,
        name: str,
        summary: str | None = None,
        props: dict | None = None,
        embedding: list[float] | None = None,
        embedding_model: str | None = None,
        embedding_dim: int | None = None,
        source: str | None = None,
    ) -> Node: ...

    @abstractmethod
    async def upsert_edge(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scope: ScopeRef,
        type_: str,
        src_id: str,
        dst_id: str,
        fact: str | None = None,
        props: dict | None = None,
        valid_at: datetime | None = None,
        source: str | None = None,
        fact_embedding: list[float] | None = None,
        embedding_model: str | None = None,
        embedding_dim: int | None = None,
    ) -> Edge: ...

    @abstractmethod
    async def invalidate_superseded(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scope: ScopeRef,
        new_edge: Edge,
    ) -> int:
        """Bi-temporal contradiction handling for the just-written ``new_edge``: close prior
        active same-(src, type) edges with a different, validity-overlapping dst — or, if
        ``new_edge`` is a backfilled earlier-world fact, bound it instead. Append-only (sets
        ``invalid_at``/``expired_at``, never deletes). Returns count of edges bounded."""

    @abstractmethod
    async def invalidate_edge(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scope: ScopeRef,
        type_: str,
        src_id: str,
        dst_id: str,
        at: datetime,
    ) -> int:
        """Explicitly END an active relation at world-time ``at`` (a stative relation that was
        terminated, e.g. a member removed). Sets ``invalid_at`` on the matching open edge(s) whose
        validity covers ``at``; append-only, never deletes. Returns count of edges ended."""

    @abstractmethod
    async def search_nodes(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scopes: list[ScopeRef],
        query_embedding: list[float] | None = None,
        text: str | None = None,
        type_: str | None = None,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> list[ScoredNode]:
        """Search across the given accessible scopes only, optionally restricted to
        ``sources``. An empty ``scopes`` returns nothing — never a tenant-wide scan."""

    @abstractmethod
    async def lexical_search_nodes(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scopes: list[ScopeRef],
        text: str,
        type_: str | None = None,
        sources: list[str] | None = None,
        limit: int = 10,
    ) -> list[ScoredNode]:
        """The hybrid lexical leg: weighted FTS + pg_trgm (identifier recall) on Postgres, a
        portable token-overlap score elsewhere. Scope is a hard predicate, as in search_nodes."""

    @abstractmethod
    async def find_node_ids(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scopes: list[ScopeRef],
        type_: str,
        keys: list[str],
    ) -> list[str]:
        """Resolve node ids for ``(type_, key)`` across accessible scopes (caller anchor nodes)."""

    @abstractmethod
    async def nodes_with_valid_edges(
        self,
        session: AsyncSession,
        node_ids: list[str],
        *,
        as_of: datetime,
        as_of_mode: str = "valid",
    ) -> set[str]:
        """Of ``node_ids``, those with >=1 edge valid at ``as_of`` (node-follows-edge-validity):
        a node only surfaces at a time it actually participates in the graph."""

    @abstractmethod
    async def search_fact_edges(
        self,
        session: AsyncSession,
        *,
        tenant_id: str | None,
        scopes: list[ScopeRef],
        query_embedding: list[float] | None = None,
        as_of: datetime | None = None,
        as_of_mode: str = "valid",
        include_invalid: bool = False,
        limit: int = 10,
    ) -> list[tuple[Edge, float]]:
        """Vector search over edge fact_embedding with scope + temporal (invalid_at/as_of) filters.
        The most heavily-filtered vector query; uses HNSW iterative_scan to keep recall (#18)."""

    @abstractmethod
    async def graph_distance(
        self,
        session: AsyncSession,
        *,
        anchor_ids: list[str],
        candidate_ids: list[str],
        max_depth: int = 3,
    ) -> dict[str, int]:
        """Min hop distance from any anchor to each candidate over current edges (depth-bounded,
        cycle-safe). Unreachable-within-bound candidates are omitted. For node-distance rerank."""

    @abstractmethod
    async def neighbors(
        self,
        session: AsyncSession,
        node_id: str,
        *,
        depth: int = 1,
        include_invalid: bool = False,
        as_of: datetime | None = None,
        as_of_mode: str = "valid",  # "valid" (world-time) | "system" (belief-time)
        # Scope confinement + fan-out bound. ``scopes=None`` skips the scope predicate and is
        # reserved for already-scoped internal flows (e.g. tests over a single-tenant fixture);
        # API-facing callers MUST pass the caller's accessible scopes or expansion can walk
        # into scopes the caller may not read (another user's personal edges).
        tenant_id: str | None = None,
        scopes: list | None = None,
        max_edges: int = 500,
    ) -> list[Edge]: ...

    @abstractmethod
    async def purge_scope(
        self, session: AsyncSession, *, tenant_id: str | None, scope: ScopeRef
    ) -> tuple[int, int]:
        """Hard-delete all nodes and edges owned by ``scope`` (the CLEAR opt-out action).
        Returns (nodes_deleted, edges_deleted)."""
