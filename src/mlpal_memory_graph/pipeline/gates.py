"""The single pre-extraction gate: consent first, then deterministic extraction policy.

Returning a non-None reason means "do not fold this episode" — the caller records the reason
and stops. Centralising this here gives one auditable choke point (design-proposal §5.2).
"""

from __future__ import annotations

from ..core.scope import ScopeRef
from ..services.policy import resolve_consent, resolve_extraction_policy


def _episode_metadata(episode) -> dict:
    """The metadata surface policy rules match on (never the source *type* — see §3)."""
    return {**(episode.subject or {}), **(episode.payload or {})}


async def fold_gate(session, tenant_id: str | None, scope: ScopeRef, episode) -> str | None:
    """Return the rule id blocking this fold, or None to proceed.

    Order is deterministic: consent (collect/update opt-out) before policy deny rules.
    """
    state = await resolve_consent(session, tenant_id, scope)
    if state is not None:
        return f"consent:{state}"

    policy = await resolve_extraction_policy(session, tenant_id, scope)
    return policy.drop_reason(source=episode.source, metadata=_episode_metadata(episode))
