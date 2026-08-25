"""Phase-2 source (stub): the agent runtime's LangGraph event store — the richest source.

Conversation **content** lives only here (assistants-service is metadata-only). This tails the
LangGraph ``AsyncPostgresStore`` backing table ``mlpal_events.store`` directly — a single global
query across all agent/chat namespaces, no per-namespace enumeration:

    SELECT namespace, key, value, updated_at FROM mlpal_events.store
    WHERE updated_at > :since ORDER BY updated_at, key LIMIT :limit

PROD-VERIFIED (2026-06-10, read as ``memory_reader`` against the live RDS): the namespace column
is ``prefix`` — dot-joined TEXT like ``user.234.project.110.agent.81505.chat.22`` — NOT the
``namespace text[]`` an earlier recon claimed. Full live columns: ``prefix, key, value jsonb,
created_at, updated_at, expires_at, ttl_minutes`` (16k+ rows readable). ``value`` is the
``Event.to_json()`` payload — ``type`` ∈ {MESSAGE, TRACE}, ``data`` carrying ``content``/``role``
(MESSAGE) or ``trace_type`` (TRACE). Pin **langgraph 1.0.5** (mlpal's version) —
``AsyncPostgresStore``'s schema is an internal API, so the column-contract check
(prefix/key/value/updated_at) fails loud on drift. NOTE: prod HAS RLS on this table
(``agent_isolation_store``); ``memory_reader`` reads via its own SELECT policy
(``memory_reader_read``) — see docs/ops/dba-request-ingestion.md §3.

Governance: MESSAGE content is the **first place chat content is persisted+extracted**, so it
MUST flow through the fold's consent → policy → redaction → scope gate and respect the
content_capture opt-in — which it does automatically by going through the normal envelope→fold.

Until a ``read_dsn`` is supplied this is a configured no-op (not a silent failure).
"""

from __future__ import annotations

from datetime import UTC, datetime

from ..sources import SourceCapabilities, SourceConfig, SourceItem, register_source
from ._producer_tail import ProducerTailSource

_REQUIRED_COLUMNS = ("prefix", "key", "value", "updated_at")


def _ns_pairs(prefix) -> dict[str, str]:
    """'user.1.project.2...' -> {'user':'1','project':'2',...} (langgraph dot-joins the
    namespace tuple into the ``prefix`` column; labels can't contain dots)."""
    parts = [p for p in str(prefix or "").split(".") if p]
    return dict(zip(parts[::2], parts[1::2], strict=False))


def _item_from_store_row(row: dict) -> SourceItem:
    p = _ns_pairs(row.get("prefix"))
    value = row.get("value") or {}
    etype = str(value.get("type", "")).upper()
    data = value.get("data") or {}
    if etype == "MESSAGE":
        action_type, content = "chat.message", data.get("content")
    elif str(data.get("trace_type", "")).upper() == "LLM":
        action_type, content = "agent.tool_called", None
    else:
        action_type, content = "agent.decided", None
    # dedup id = namespace prefix + key. The store key is unique only WITHIN a namespace, so we
    # qualify it; the envelope event_id is "langgraph_store:<prefix>.<key>" (idempotent per row).
    ns_path = str(row.get("prefix") or "")
    return SourceItem(
        source_id=f"{ns_path}.{row.get('key')}" if ns_path else str(row.get("key")),
        occurred_at=row.get("updated_at") or datetime.now(UTC),
        content=content,  # only MESSAGE carries content → routed through redaction/consent gate
        metadata={
            "scope": "agent" if p.get("agent") else "user",  # agent memory is a subject scope
            "scope_id": p.get("agent") or p.get("user"),
            "actor": {"user_id": p.get("user")},
            "action_type": action_type,
            "subject": {
                "project_id": p.get("project"),
                "agent_id": p.get("agent"),
                "chat_id": p.get("chat"),
            },
            "payload": data.get("metadata") or {},
        },
    )


@register_source
class LangGraphStoreSource(ProducerTailSource):
    """Phase-2 (the payoff): tails the agent runtime's LangGraph store ``mlpal_events.store``
    globally on a composite ``(updated_at, key)`` watermark — conversation content lives only
    here. MESSAGE → chat.message (content, HIGH tier); TRACE → agent.tool_called / agent.decided.
    Content flows through the normal envelope→fold, i.e. consent → policy → redaction → scope.

    Pin **langgraph 1.0.5** (mlpal's version) — AsyncPostgresStore's schema is an internal API, so
    the column-contract check fails loud on drift. Inert until a ``read_dsn`` is supplied."""

    TABLE = "store"
    CURSOR = "updated_at"
    ID_EXPR = "key"
    ID_KEY = "key"
    ID_SQL_TYPE = "text"  # the store key is text (not an integer PK)
    COLUMNS = _REQUIRED_COLUMNS
    REQUIRED = _REQUIRED_COLUMNS

    def __init__(self, config: SourceConfig) -> None:
        super().__init__(config)
        self.schema = self.schema or "mlpal_events"  # langgraph store's default schema

    @classmethod
    def source_type(cls) -> str:
        return "langgraph_store"

    @classmethod
    def capabilities(cls) -> SourceCapabilities:
        return SourceCapabilities(incremental=True, rich_structure=True)

    def map_row(self, row: dict) -> list[SourceItem]:
        return [_item_from_store_row(row)]
