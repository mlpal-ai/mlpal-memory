"""Entity resolution.

Authoritative entities (User, Agent, MCPServer, ...) ground on their canonical MLPal IDs —
exact (org, type, key) match — so they never fragment. Free-text Facts use hybrid semantic
dedup (cosine over same-type candidates above a threshold), the Graphiti/Cognee approach.
"""

from __future__ import annotations

from ..core.scope import ScopeRef
from .extractor import EntitySpec

AUTHORITATIVE: frozenset[str] = frozenset(
    {"Org", "Team", "User", "Agent", "MCPServer", "Tool", "Skill", "Chat", "Artifact"}
)


class Resolver:
    def __init__(self, driver, embedder, threshold: float = 0.86) -> None:
        self.driver = driver
        self.embedder = embedder
        self.threshold = threshold

    async def resolve_entity(
        self,
        session,
        tenant_id: str | None,
        scope: ScopeRef,
        spec: EntitySpec,
        embedding=None,
        source: str | None = None,
    ):
        if spec.type in AUTHORITATIVE or embedding is None:
            return await self.driver.upsert_node(
                session,
                tenant_id=tenant_id,
                scope=scope,
                type_=spec.type,
                key=spec.key,
                name=spec.name,
                props=spec.props,
                embedding=embedding,
                embedding_model=self.embedder.name,
                embedding_dim=self.embedder.dim,
                source=source,
            )

        # free-text (Fact): semantic dedup against same-type nodes *in the same scope*.
        results = await self.driver.search_nodes(
            session,
            tenant_id=tenant_id,
            scopes=[scope],
            query_embedding=embedding,
            type_=spec.type,
            limit=1,
        )
        if results and results[0].score >= self.threshold:
            match = results[0].node
            return await self.driver.upsert_node(
                session,
                tenant_id=tenant_id,
                scope=scope,
                type_=match.type,
                key=match.key,
                name=match.name,
                props=spec.props,
                embedding=match.embedding,
                embedding_model=self.embedder.name,
                embedding_dim=self.embedder.dim,
                source=source,
            )
        return await self.driver.upsert_node(
            session,
            tenant_id=tenant_id,
            scope=scope,
            type_=spec.type,
            key=spec.key,
            name=spec.name,
            props=spec.props,
            embedding=embedding,
            embedding_model=self.embedder.name,
            embedding_dim=self.embedder.dim,
            source=source,
        )
