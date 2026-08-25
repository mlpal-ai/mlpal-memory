"""Phase-1 source: mcp-service `mcp.mcp_servers` → deterministic DEPLOYED + EXPOSES backbone.

No status-change event exists, so we tail the server snapshot by ``updated_at`` and synthesize:
``status='deployed'`` → ``mcp.deployed`` (DEPLOYED + EXPOSES per tool in ``tools_metadata``);
``status='stopped'`` → ``mcp.stopped`` (recorded, no edge). Platform-owned servers land in the
GLOBAL scope; user-owned under the owner (mcp_servers has no org_id — a known tenancy gap).
Deterministic tier. Schema verified 2026-06 (recon)."""

from __future__ import annotations

from ..sources import SourceItem, register_source
from ._producer_tail import ProducerTailSource, scope_for


@register_source
class McpServersSource(ProducerTailSource):
    TABLE = "mcp_servers"
    CURSOR = "updated_at"
    COLUMNS = (
        "id",
        "owner_user_id",
        "owner_type",
        "name",
        "status",
        "tools_metadata",
        "updated_at",
    )
    REQUIRED = (
        "id",
        "owner_user_id",
        "owner_type",
        "name",
        "status",
        "tools_metadata",
        "updated_at",
    )

    @classmethod
    def source_type(cls) -> str:
        return "mcp_servers"

    def map_row(self, row: dict) -> list[SourceItem]:
        status = row["status"]
        if status not in ("deployed", "stopped"):
            return []  # draft/archived → no lifecycle episode in Phase 1
        owner = row.get("owner_user_id")
        if row.get("owner_type") == "platform":
            scope, scope_id, org = "global", None, None
        else:
            scope, scope_id = scope_for(None, owner)  # user-owned (no org_id available)
            org = None
        server_id = str(row["id"])
        tools = [t.get("name") for t in (row.get("tools_metadata") or []) if t.get("name")]
        action_type = "mcp.deployed" if status == "deployed" else "mcp.stopped"
        return [
            SourceItem(
                source_id=f"{server_id}:{status}",
                occurred_at=row["updated_at"],
                metadata={
                    "org_id": org,
                    "scope": scope,
                    "scope_id": scope_id,
                    "actor": {"user_id": str(owner)} if owner is not None else {},
                    "subject": {"server_id": server_id},
                    "action_type": action_type,
                    "payload": {"name": row.get("name"), "tools": tools},
                },
            )
        ]
