"""Phase-1 source: backend `organization_audit_logs` → deterministic MEMBER_OF backbone.

Pull-tails the org audit log by ``created_at``. Every row already carries actor + org, so the
fold's baseline org-membership rule records MEMBER_OF; member-add events also link the target
user. Deterministic tier (no content). Schema verified 2026-06 (recon)."""

from __future__ import annotations

from ..sources import SourceItem, register_source
from ._producer_tail import ProducerTailSource, scope_for

# producer audit `action` → our envelope action_type. member_added/role_changed assert
# "User MEMBER_OF Org"; member_removed ENDS that membership (stative invalidation in the fold).
# invitation/settings events stay unmapped (no membership semantics).
_ACTION = {
    "member_added": "org.member_added",
    "member_role_changed": "org.role_changed",
    "member_removed": "org.member_removed",
}


@register_source
class BackendAuditSource(ProducerTailSource):
    TABLE = "organization_audit_logs"
    CURSOR = "created_at"
    COLUMNS = (
        "id",
        "organization_id",
        "actor_user_id",
        "action",
        "target_user_id",
        "details",
        "created_at",
    )
    REQUIRED = (
        "id",
        "organization_id",
        "actor_user_id",
        "action",
        "target_user_id",
        "details",
        "created_at",
    )

    @classmethod
    def source_type(cls) -> str:
        return "backend_audit"

    def map_row(self, row: dict) -> list[SourceItem]:
        action_type = _ACTION.get(row["action"])
        if action_type is None:
            return []  # not part of the Phase-1 additive-membership backbone — skip, don't guess
        org_id = row["organization_id"]
        scope, scope_id = scope_for(org_id, row.get("actor_user_id"))
        subject: dict = {}
        if row.get("target_user_id") is not None:
            subject["target_user_id"] = str(row["target_user_id"])
        return [
            SourceItem(
                source_id=str(row["id"]),
                occurred_at=row["created_at"],
                metadata={
                    "org_id": str(org_id),
                    "scope": scope,
                    "scope_id": scope_id,
                    "actor": {"user_id": str(row["actor_user_id"])},
                    "subject": subject,
                    "action_type": action_type,
                    "payload": {"audit_action": row["action"], "details": row.get("details") or {}},
                },
            )
        ]
