"""Shared scope/tenant predicate + helpers — the single isolation rule.

Both memory tiers use these: the **derived** graph (nodes/edges, via the GraphDriver) and the
**direct** store (documents/chunks, via services/direct.py). Keeping one ``scope_clause`` means
the security-critical "which tenant + which scope" predicate is defined exactly once, not
duplicated per store. The tenant boundary (``org_id``) is baked into every non-GLOBAL clause,
so a buggy caller cannot leak across tenants or scopes.
"""

from __future__ import annotations

import math

from sqlalchemy import and_

from ..core.scope import Scope, ScopeRef


def cosine(a: list[float] | None, b: list[float] | None) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def classification_for(scope: ScopeRef) -> str:
    """Default data classification implied by the owning scope (design-proposal §6)."""
    if scope.scope is Scope.GLOBAL:
        return "public"
    if scope.scope is Scope.USER:
        return "personal"
    return "internal"


def scope_clause(model, tenant_id: str | None, scope: ScopeRef):
    """A predicate selecting exactly the rows of ``model`` owned by ``scope`` within
    ``tenant_id``. ``model`` must expose ``org_id``, ``scope`` and ``scope_id`` columns."""
    if scope.scope is Scope.GLOBAL:
        return model.scope == Scope.GLOBAL.value  # cross-tenant, platform-curated
    return and_(
        model.org_id == tenant_id,
        model.scope == scope.scope.value,
        model.scope_id == scope.scope_id,
    )


def browse_clause(model, *, tenant_id: str | None, user_id: str | None, team_ids: tuple):
    """Tenant-wide browse predicate (UI listings): everything the caller may see without
    activating subject scopes one by one — org/repo/service/agent/global rows (org-internal
    readable), plus ONLY the caller's own USER rows and only teams they belong to. The same
    semantics as retrieval's accessible-scopes, collapsed into one listing predicate."""
    from sqlalchemy import false, or_

    return and_(
        or_(model.org_id == tenant_id, model.scope == Scope.GLOBAL.value),
        or_(model.scope != Scope.USER.value, model.scope_id == user_id),
        or_(
            model.scope != Scope.TEAM.value,
            model.scope_id.in_(team_ids) if team_ids else false(),
        ),
    )
