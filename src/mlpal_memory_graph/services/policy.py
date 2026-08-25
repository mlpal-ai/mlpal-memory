"""Resolve consent + extraction policy for one episode, deny-precedence down the scope chain.

A broader scope sets a floor; a narrower scope can only tighten it. Consent OFF/PAUSE at any
ancestor blocks learning; any scope's deny rule drops an item; allow-lists narrow further.
This is the single read path the fold gate (``pipeline/gates.py``) uses. See §2, §5.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select, tuple_

from ..core.scope import Scope, ScopeRef
from ..db.models import ConsentState, ExtractionPolicy

BLOCKING_CONSENT_STATES = frozenset({"off", "pause"})


def consent_chain(tenant_id: str | None, target: ScopeRef) -> list[ScopeRef]:
    """Ancestor scopes whose consent/policy govern a write at ``target`` (broad → narrow)."""
    if target.scope is Scope.GLOBAL or tenant_id is None:
        return [target]
    chain: list[ScopeRef] = [ScopeRef(Scope.ORG, tenant_id)]
    if target.scope is not Scope.ORG:
        chain.append(target)
    return chain


@dataclass
class ResolvedPolicy:
    deny_sources: set[str] = field(default_factory=set)
    allow_sources: set[str] | None = None  # None = no allow-list constraint
    metadata_deny: dict[str, set[str]] = field(default_factory=dict)

    def drop_reason(self, *, source: str, metadata: dict | None) -> str | None:
        """Return the rule id that excludes this item, or None to keep it."""
        if source in self.deny_sources:
            return f"deny_source:{source}"
        if self.allow_sources is not None and source not in self.allow_sources:
            return f"not_allowed_source:{source}"
        for key, denied in self.metadata_deny.items():
            if (metadata or {}).get(key) in denied:
                return f"metadata_deny:{key}"
        return None


async def resolve_consent(session, tenant_id: str | None, target: ScopeRef) -> str | None:
    """Return the blocking consent state ('off'/'pause') if any ancestor opted out, else None."""
    chain = consent_chain(tenant_id, target)
    rows = await _load(session, ConsentState, tenant_id, chain)
    for r in rows:
        if r.state in BLOCKING_CONSENT_STATES:
            return r.state
    return None


async def resolve_extraction_policy(
    session, tenant_id: str | None, target: ScopeRef
) -> ResolvedPolicy:
    chain = consent_chain(tenant_id, target)
    rows = await _load(session, ExtractionPolicy, tenant_id, chain)
    resolved = ResolvedPolicy()
    for r in rows:
        p = r.policy or {}
        resolved.deny_sources |= set(p.get("deny_sources") or [])
        allow = p.get("allow_sources")
        if allow:  # each scope's allow-list narrows (intersection semantics)
            allow_set = set(allow)
            resolved.allow_sources = (
                allow_set if resolved.allow_sources is None
                else resolved.allow_sources & allow_set
            )
        for key, vals in (p.get("metadata_deny") or {}).items():
            resolved.metadata_deny.setdefault(key, set()).update(vals)
    return resolved


async def _load(session, model, tenant_id: str | None, chain: list[ScopeRef]) -> list:
    if tenant_id is None:
        return []
    keys = [(s.scope.value, s.scope_id) for s in chain if s.scope_id is not None]
    if not keys:
        return []
    stmt = select(model).where(
        model.org_id == tenant_id,
        tuple_(model.scope, model.scope_id).in_(keys),
    )
    return list((await session.execute(stmt)).scalars().all())
