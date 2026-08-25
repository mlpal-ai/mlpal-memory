"""Permission checking — aligned with the platform's mlpal-auth model.

The real implementation lives in ``mlpal_auth.permissions.has_permission`` (the single
source of truth shared with auth-service). That SDK is an optional extra, absent in local
dev/test, so this module re-exports it when present and otherwise provides a byte-for-byte
local mirror. Keep the two in sync if the platform's wildcard semantics ever change.

Wildcards: ``*`` (all), ``ns:*`` (namespace prefix), exact match. Team membership is encoded
as ``team:<id>`` / ``team:<id>:*`` grants — the same convention every MLPal service uses.
"""

from __future__ import annotations

try:  # platform SDK is the source of truth when installed
    from mlpal_auth.permissions import has_permission as _has_permission

    _HAS_SDK = True
except Exception:  # noqa: BLE001  (SDK is an optional extra)
    _HAS_SDK = False

    def _has_permission(granted: list[str], required: str) -> bool:
        for perm in granted:
            if perm == "*":
                return True
            if perm == required:
                return True
            if perm.endswith(":*") and required.startswith(perm[:-1]):
                return True
        return False


def has_permission(granted: list[str], required: str) -> bool:
    return _has_permission(granted, required)


def team_ids_from_permissions(granted: list[str]) -> list[str]:
    """Extract concrete team ids from ``team:<id>`` / ``team:<id>:*`` grants.

    Wildcard-only grants (``team:*``) confer no *specific* team and are ignored — a caller
    can only address teams it is concretely a member of.
    """
    teams: list[str] = []
    for perm in granted:
        if not perm.startswith("team:"):
            continue
        tid = perm[len("team:") :].split(":", 1)[0]
        if tid and tid != "*" and tid not in teams:
            teams.append(tid)
    return teams
