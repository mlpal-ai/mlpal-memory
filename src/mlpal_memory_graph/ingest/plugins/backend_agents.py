"""Phase-1 source: backend `agents` → deterministic AUTHORED / DEPLOYED backbone.

There is no agent event table, so we tail the `agents` snapshot by ``updated_at`` and synthesize
lifecycle episodes from status columns: every agent yields an ``agent.created`` (AUTHORED), and a
deployed one (``deployment_status='running'`` + ``deployed_at``) also yields ``agent.deployed``
(DEPLOYED). The author is the project owner, so we LEFT JOIN `projects` for ``user_id`` +
``organization_id``. Deterministic tier. Schema verified 2026-06 (recon)."""

from __future__ import annotations

from ..sources import SourceItem, register_source
from ._producer_tail import ProducerTailSource, scope_for


@register_source
class BackendAgentsSource(ProducerTailSource):
    TABLE = "agents"
    CURSOR = "a.updated_at"
    CURSOR_KEY = "updated_at"
    ID_EXPR = "a.id"
    COLUMNS = (
        "a.id AS id",
        "a.name AS name",
        "a.status AS status",
        "a.deployment_status AS deployment_status",
        "a.deployed_at AS deployed_at",
        "a.created_at AS created_at",
        "a.updated_at AS updated_at",
        "p.user_id AS author_user_id",
        "p.organization_id AS org_id",
    )
    REQUIRED = (
        "id",
        "name",
        "status",
        "deployment_status",
        "deployed_at",
        "created_at",
        "updated_at",
        "author_user_id",
        "org_id",
    )

    @classmethod
    def source_type(cls) -> str:
        return "backend_agents"

    def from_clause(self) -> str:
        s = f'"{self.schema}".' if self.schema else ""
        return f"{s}agents a LEFT JOIN {s}projects p ON a.project_id = p.id"

    def map_row(self, row: dict) -> list[SourceItem]:
        author = row.get("author_user_id")
        org_id = row.get("org_id")
        if author is None and org_id is None:
            return []  # pool agent with no owner — nothing to attribute
        scope, scope_id = scope_for(org_id, author)
        actor = {"user_id": str(author)} if author is not None else {}
        org = str(org_id) if org_id is not None else None
        agent_id = str(row["id"])

        def _item(suffix, action_type, occurred_at):
            return SourceItem(
                source_id=f"{agent_id}:{suffix}",
                occurred_at=occurred_at,
                metadata={
                    "org_id": org,
                    "scope": scope,
                    "scope_id": scope_id,
                    "actor": actor,
                    "subject": {"agent_id": agent_id},
                    "action_type": action_type,
                    "payload": {
                        "name": row.get("name"),
                        "status": row.get("status"),
                        "deployment_status": row.get("deployment_status"),
                    },
                },
            )

        items = [_item("created", "agent.created", row["created_at"])]
        if row.get("deployment_status") == "running" and row.get("deployed_at") is not None:
            items.append(_item("deployed", "agent.deployed", row["deployed_at"]))
        return items
