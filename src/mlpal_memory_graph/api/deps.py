"""FastAPI dependencies: auth identity + permission gates + service singletons.

Auth uses the mlpal-auth SDK in production (JWT or mlpal_sk_ keys) and a dev/test fallback
otherwise — the X-Test-* header scheme mirrors the platform's test harness. An
``X-Internal-Service-Key`` always grants machine-to-machine ingest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.config import get_settings
from ..core.permissions import has_permission, team_ids_from_permissions
from ..db import get_session
from ..pipeline.updater import Updater
from ..services.retrieval import Retrieval

ADMIN_PERMISSION = "memory:admin"

try:  # the SDK is an optional extra; absent in local dev/test
    import mlpal_auth  # noqa: F401

    _HAS_MLPAL_AUTH = True
except Exception:  # noqa: BLE001
    _HAS_MLPAL_AUTH = False


@dataclass
class AuthIdentity:
    user_id: str | None
    org_id: str | None  # tenant boundary
    permissions: list[str] = field(default_factory=list)
    key_id: str | None = None
    team_ids: list[str] = field(default_factory=list)  # derived from team:<id> grants
    # True only for the internal service-to-service key — the one identity trusted to write
    # episodes across tenants (per-episode org_id). User/admin identities are pinned to their org.
    is_service: bool = False

    def has(self, perm: str) -> bool:
        """Hierarchical permission check — delegates to the platform's shared logic."""
        return has_permission(self.permissions, perm)

    def is_admin(self) -> bool:
        return self.has(ADMIN_PERMISSION)


async def get_identity(
    request: Request,
    authorization: str | None = Header(None),
    x_internal_service_key: str | None = Header(None, alias="X-Internal-Service-Key"),
    x_test_user_id: str | None = Header(None),
    x_test_org_id: str | None = Header(None),
    x_test_permissions: str | None = Header(None),
    x_test_api_key_id: str | None = Header(None),
) -> AuthIdentity:
    s = get_settings()
    # 1) internal service-to-service key — full access (machine-to-machine ingest/admin)
    if x_internal_service_key and x_internal_service_key == s.internal_service_api_key:
        return _identity(
            "internal", request.headers.get("x-org-id"), ["*"], "internal", is_service=True
        )
    # 2) dev/test fallback — ONLY when explicitly enabled. A missing SDK no longer
    # downgrades to header auth: outside local envs that state is a fatal startup error
    # (config.production_config_errors) and here it fails closed with a distinct status.
    if s.dev_auth:
        perms = x_test_permissions.split(",") if x_test_permissions else ["*"]
        return _identity(
            x_test_user_id or "dev-user", x_test_org_id or "dev-org", perms, x_test_api_key_id
        )
    if not _HAS_MLPAL_AUTH:
        raise HTTPException(status_code=503, detail="auth backend unavailable")
    # 3) production: validate via mlpal-auth (JWT or mlpal_sk_ key, 60s SDK cache)
    if not authorization:
        raise HTTPException(status_code=401, detail="missing credentials")
    token = authorization.removeprefix("Bearer ").strip()
    try:
        from mlpal_auth import AuthClient  # type: ignore

        result = await AuthClient(auth_service_url=s.auth_service_url).validate_token(token)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="invalid credentials") from None
    return _identity(
        _as_str(getattr(result, "user_id", None)),
        _as_str(getattr(result, "org_id", None)),
        list(getattr(result, "permissions", []) or []),
        _as_str(getattr(result, "key_id", None)),
    )


def _as_str(v: object | None) -> str | None:
    """Platform ids are ints; memory-graph stores them as strings (its column type)."""
    return None if v is None else str(v)


def _identity(
    user_id: str | None,
    org_id: str | None,
    perms: list[str],
    key_id: str | None,
    *,
    is_service: bool = False,
) -> AuthIdentity:
    return AuthIdentity(
        user_id=user_id,
        org_id=org_id,
        permissions=perms,
        key_id=key_id,
        team_ids=team_ids_from_permissions(perms),
        is_service=is_service,
    )


async def rls_guard(
    session: AsyncSession = Depends(get_session),
    identity: AuthIdentity = Depends(get_identity),
) -> None:
    """Defence-in-depth: when RLS is enabled, set the ``app.current_org`` GUC (transaction-scoped)
    so the migration-0009 policies restrict rows to the caller's tenant at the DB layer too. The
    app-layer scope_clause stays primary; this is a backstop. No-op off Postgres / when disabled."""
    s = get_settings()
    if s.rls_enabled and identity.org_id and session.bind.dialect.name == "postgresql":
        await session.execute(
            sa_text("SELECT set_config('app.current_org', :org, true)"),
            {"org": identity.org_id},
        )


# Scopes a non-privileged caller may write to directly: their own personal scope and the
# org-wide scope. Subject scopes (TEAM/SERVICE/REPO/AGENT) need per-subject write authority we
# do not model yet, so they are gated to the trusted service / org admins (task #21).
SELF_WRITABLE_SCOPES = frozenset({"user", "org"})


def authorize_write_scope(identity: AuthIdentity, scope: str, scope_id: str | None) -> None:
    """Confine non-trusted callers to scopes they own — the HARD GATE for user-attributed
    writes, shared by every write surface (episodes, documents, future ingest routes).

    The internal service key and org admins are trusted to backfill any scope in the tenant.
    Everyone else may write only their own personal (USER) scope or the org-wide (ORG) scope;
    a body-supplied subject scope_id (TEAM/SERVICE/REPO/AGENT) or another user's personal
    scope is refused until per-subject write authz exists (task #21).

    GLOBAL is the one cross-tenant surface (visible to every org): platform-curated,
    near-empty, and writable ONLY by the internal service identity — an org admin's
    authority ends at their tenant boundary.
    """
    if scope == "global" and not identity.is_service:
        raise HTTPException(
            status_code=403, detail="global scope is platform-curated (service-only)"
        )
    if identity.is_service or identity.is_admin():
        return
    if scope == "user" and scope_id != identity.user_id:
        raise HTTPException(status_code=403, detail="cannot write another user's personal memory")
    if scope not in SELF_WRITABLE_SCOPES:
        raise HTTPException(
            status_code=403, detail=f"writing {scope} scope requires elevated authorization"
        )


def require_permission(perm: str) -> Callable:
    async def _dep(identity: AuthIdentity = Depends(get_identity)) -> AuthIdentity:
        if not identity.has(perm):
            raise HTTPException(status_code=403, detail=f"requires permission {perm}")
        return identity

    return _dep


_retrieval: Retrieval | None = None
_updater: Updater | None = None


def get_retrieval() -> Retrieval:
    global _retrieval
    if _retrieval is None:
        _retrieval = Retrieval()
    return _retrieval


def get_updater() -> Updater:
    global _updater
    if _updater is None:
        _updater = Updater()
    return _updater
