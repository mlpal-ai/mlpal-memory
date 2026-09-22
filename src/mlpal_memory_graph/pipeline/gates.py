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
    reason = policy.drop_reason(source=episode.source, metadata=_episode_metadata(episode))
    if reason is not None:
        return reason
    # memory v7 WP4 (design §9): documents are admitted by salience and budget, never by size.
    # Claims and telemetry are never salience-gated: they are small and already governed.
    if episode.action_type == "document.ingested" and episode.content and (policy.min_salience is not None or policy.source_budget_per_day):
        from ..services.salience import admission_reason

        reason, rec = await admission_reason(session, tenant_id, source=episode.source, text=episode.content,
                                             valid_at=episode.occurred_at, min_salience=policy.min_salience,
                                             budget_per_day=policy.budget_for(episode.source))
        payload = dict(episode.payload or {})
        payload["salience"] = rec
        episode.payload = payload
        if reason is not None:
            return reason
    # memory v6 lift rule (DESIGN §5): a claim that names a person beyond a role never lands above the
    # person tier. Below the person tier it is theirs and stays. Deterministic patterns; a person
    # reviews anything the patterns cannot judge (names) at lift time.
    if episode.action_type == "memory.claim" and scope.scope.value != "user":
        from ..services.pii import classify_pii

        kinds = classify_pii(episode.content) or classify_pii(str((episode.payload or {}).get("value") or ""))
        if kinds:
            return "pii:" + "+".join(kinds)
    return None
