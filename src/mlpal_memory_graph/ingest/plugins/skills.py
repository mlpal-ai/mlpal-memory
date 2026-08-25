"""Phase-1 source: skills-service `skill_load_events` → deterministic USED_SKILL backbone.

Tails the load-event stream by ``timestamp``. Load/execute events (a user actually using a
skill) become ``skill.used`` → USED_SKILL(User→Skill); metadata-only surfacing and
consumer-less events are skipped in Phase 1. Authorship (AUTHORED on publish) comes from the
`skills` status table — deferred to a follow-up. Deterministic tier. Schema verified 2026-06."""

from __future__ import annotations

from ..sources import SourceItem, register_source
from ._producer_tail import ProducerTailSource, scope_for

_USE_EVENTS = frozenset({"body.loaded", "file.loaded", "script.executed", "outcome"})


@register_source
class SkillsSource(ProducerTailSource):
    TABLE = "skill_load_events"
    CURSOR = "timestamp"
    COLUMNS = (
        "id",
        "event_id",
        "event_type",
        "skill_id",
        "consumer_user_id",
        "consumer_org_id",
        "agent_id",
        "metadata_json",
        "timestamp",
    )
    REQUIRED = (
        "id",
        "event_id",
        "event_type",
        "skill_id",
        "consumer_user_id",
        "consumer_org_id",
        "agent_id",
        "metadata_json",
        "timestamp",
    )

    @classmethod
    def source_type(cls) -> str:
        return "skills"

    def map_row(self, row: dict) -> list[SourceItem]:
        if row["event_type"] not in _USE_EVENTS:
            return []  # metadata.surfaced etc. — not a usage in Phase 1
        consumer = row.get("consumer_user_id")
        org = row.get("consumer_org_id")
        if consumer is None and org is None:
            return []  # no actor to attribute the use to
        scope, scope_id = scope_for(org, consumer)
        subject: dict = {"skill_id": str(row["skill_id"])}
        if row.get("agent_id") is not None:
            subject["agent_id"] = str(row["agent_id"])
        return [
            SourceItem(
                source_id=str(row["event_id"]),
                occurred_at=row["timestamp"],
                metadata={
                    "org_id": str(org) if org is not None else None,
                    "scope": scope,
                    "scope_id": scope_id,
                    "actor": {"user_id": str(consumer)} if consumer is not None else {},
                    "subject": subject,
                    "action_type": "skill.used",
                    "payload": {
                        "event_type": row["event_type"],
                        "meta": row.get("metadata_json") or {},
                    },
                },
            )
        ]
