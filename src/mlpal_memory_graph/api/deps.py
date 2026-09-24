"""FastAPI dependencies: auth identity + permission gates + service singletons.

Auth uses the mlpal-auth SDK in production (JWT or mlpal_sk_ keys), a static key file on a
self-hosted instance (MLPAL_API_KEYS_FILE) and a dev/test fallback otherwise — the X-Test-*
header scheme mirrors the platform's test harness. An
``X-Internal-Service-Key`` always grants machine-to-machine ingest.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy import text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.api_keys import get_api_key_file
from ..core.config import get_settings
from ..core.permissions import has_permission, team_ids_from_permissions
from ..db import get_session
from ..pipeline.updater import Updater
from ..services.retrieval import Retrieval

ADMIN_PERMISSION = "memory.admin"

try:  # the SDK is an optional extra; absent in local dev/test
    import mlpal_auth  # noqa: F401

    _HAS_MLPAL_AUTH = True
except Exception:  # noqa: BLE001
    _HAS_MLPAL_AUTH = False


PERSONAL_TENANT_PREFIX = "user:"


def tenant_for(org_id: str | None, user_id: str | None) -> str | None:
    """memory v12 §2: the tenant is the org when the credential carries one; a person without an
    org (a personal platform key) gets their own tenant, `user:<id>`, so an individual and a
    company run on the same rows and rules. Joining an org later does not merge the two: the
    personal tenant stays theirs, publishing into the org is the explicit act."""
    if org_id:
        return org_id
    return f"{PERSONAL_TENANT_PREFIX}{user_id}" if user_id else None


@dataclass
class AuthIdentity:
    user_id: str | None
    org_id: str | None  # the tenant boundary: the org, or user:<id> for a person without one
    permissions: list[str] = field(default_factory=list)
    key_id: str | None = None
    team_ids: list[str] = field(default_factory=list)  # units the person reads (own + ancestors + grants), nearest first
    writable_team_ids: list[str] = field(default_factory=list)  # units the person may write into (own + ancestors by policy + grants)
    administered_units: list[str] = field(default_factory=list)  # units (and subtrees) the person administers
    hop_closed_units: list[str] = field(default_factory=list)  # writable units whose policy refuses HOP writes
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
    x_api_key: str | None = Header(None, alias="X-API-Key"),
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
        return await _with_units(_identity(
            x_test_user_id or "dev-user", x_test_org_id or "dev-org", perms, x_test_api_key_id
        ))
    if not s.api_keys_file and not _HAS_MLPAL_AUTH:
        raise HTTPException(status_code=503, detail="auth backend unavailable")
    # X-API-Key is the platform-UI convention for API keys; Bearer carries either.
    if not authorization and not x_api_key:
        raise HTTPException(status_code=401, detail="missing credentials")
    token = x_api_key or (authorization or "").removeprefix("Bearer ").strip()
    # 3a) self-hosted: the operator's static key file pins every key to one tenant.
    if s.api_keys_file:
        key = get_api_key_file(s.api_keys_file).lookup(token)
        if key is None:
            raise HTTPException(status_code=401, detail="invalid credentials")
        return await _with_units(_identity(key.user_id, key.org_id, list(key.permissions), key.key_id))
    # 3b) platform: validate via mlpal-auth (JWT or mlpal_sk_ key, 60s SDK cache).
    try:
        from mlpal_auth import AuthClient  # type: ignore

        result = await AuthClient(auth_service_url=s.auth_service_url).validate_token(token)
    except HTTPException:
        raise
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="invalid credentials") from None
    org_id = _as_str(getattr(result, "org_id", None))
    chosen = request.headers.get("x-org-id")
    if chosen and chosen != org_id:
        # a person in an organization: the token names them, the header names the tenant, the
        # platform's membership list says whether that pairing is theirs
        await require_membership(token, chosen, s.org_membership_url)
        org_id = chosen
    return await _with_units(_identity(
        _as_str(getattr(result, "user_id", None)),
        org_id,
        list(getattr(result, "permissions", []) or []),
        _as_str(getattr(result, "key_id", None)),
    ))


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
        org_id=org_id if is_service else tenant_for(org_id, user_id),
        permissions=perms,
        key_id=key_id,
        team_ids=team_ids_from_permissions(perms),
        writable_team_ids=team_ids_from_permissions(perms),
        is_service=is_service,
    )


MEMBERSHIP_TTL_S = 60.0
_membership_cache: dict[tuple[str, str], float] = {}  # (token digest, org) -> expiry
_membership_transport = None  # tests inject an httpx transport here


async def require_membership(token: str, org_id: str, base_url: str) -> None:
    """403 unless the platform lists `org_id` among the organizations this token's person belongs
    to. The platform is asked with the person's own bearer, so memory never holds a service
    credential for it; a positive answer is cached briefly per token."""
    import hashlib
    import time

    import httpx

    if not base_url:
        raise HTTPException(status_code=403, detail="choosing an organization is not enabled on this instance")
    key = (hashlib.sha256(token.encode()).hexdigest(), org_id)
    now = time.monotonic()
    if _membership_cache.get(key, 0.0) > now:
        return
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10, transport=_membership_transport) as client:
            r = await client.get("/organizations", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="organization membership could not be checked") from exc
    if r.status_code != 200:
        raise HTTPException(status_code=403, detail="organization membership could not be confirmed")
    body = r.json()
    orgs = body.get("organizations", body) if isinstance(body, dict) else body
    ids = {str(o.get("id")) for o in orgs if isinstance(o, dict)}
    if org_id not in ids:
        raise HTTPException(status_code=403, detail="not a member of that organization")
    _membership_cache[key] = now + MEMBERSHIP_TTL_S


async def _with_units(identity: AuthIdentity) -> AuthIdentity:
    """memory v12 §2b: the units a person reads (own, ancestors, administered subtrees) join the
    key's explicit team grants, nearest first. Resolved per request from the tenant's tree, cached
    briefly by the units service. A service identity has no person and reads by scope."""
    if identity.is_service or not identity.user_id or identity.org_id is None:
        return identity
    from ..db import get_session_factory
    from ..services import units as units_svc

    async with get_session_factory()() as session:
        access = await units_svc.resolve(session, identity.org_id, identity.user_id)
    if access.readable:
        identity.team_ids = list(access.readable) + [t for t in identity.team_ids if t not in access.readable]
    if access.writable:
        identity.writable_team_ids = list(access.writable) + [t for t in identity.writable_team_ids if t not in access.writable]
    identity.administered_units = sorted(access.administered)
    identity.hop_closed_units = sorted(access.hop_closed)
    return identity


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


def authorize_write_scope(identity: AuthIdentity, scope: str, scope_id: str | None, *, hop: str | None = None) -> None:
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
    if scope == "team":
        # memory v6 §5 / v12 §2b: a unit's memory is written by its members and, one level at a
        # time, by those below it (publishing upward, as the unit's policy allows); a unit whose
        # policy says hops_may_write: false takes no write that arrives under a HOP
        if scope_id in identity.writable_team_ids:
            if hop and scope_id in identity.hop_closed_units:
                raise HTTPException(status_code=403, detail=f"this unit's policy does not accept writes from HOPs (hop {hop})")
            return
        raise HTTPException(status_code=403, detail="cannot write a unit's memory without membership in it or below it, or the unit's policy does not let you publish up")
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
