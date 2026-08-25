"""Unit (offline): the LangGraph store row → SourceItem mapper + the fail-loud column contract."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mlpal_memory_graph.ingest import SourceConfig
from mlpal_memory_graph.ingest.plugins._producer_tail import SourceContractError
from mlpal_memory_graph.ingest.plugins.langgraph_store import (
    LangGraphStoreSource,
    _item_from_store_row,
    _ns_pairs,
)

T = datetime(2026, 1, 1, tzinfo=UTC)
# prod-verified shape (2026-06-10): `prefix` is dot-joined TEXT — live sample
# user.234.project.110.agent.81505.chat.22
NS = "user.7.project.2.agent.3.chat.9"


def test_ns_pairs():
    assert _ns_pairs(NS) == {"user": "7", "project": "2", "agent": "3", "chat": "9"}
    assert _ns_pairs(None) == {}
    assert _ns_pairs("") == {}


def test_message_maps_to_chat_message_with_content():
    item = _item_from_store_row(
        {
            "prefix": NS,
            "key": "k1",
            "updated_at": T,
            "value": {"type": "MESSAGE", "data": {"content": "hi there", "role": "user"}},
        }
    )
    md = item.metadata
    assert md["action_type"] == "chat.message" and item.content == "hi there"
    assert md["scope"] == "agent" and md["scope_id"] == "3"  # agent is a subject scope
    assert md["subject"]["agent_id"] == "3" and md["subject"]["chat_id"] == "9"
    assert md["actor"]["user_id"] == "7"
    assert item.source_id == "user.7.project.2.agent.3.chat.9.k1"  # namespace-qualified dedup id


def test_trace_llm_is_tool_called_no_content():
    item = _item_from_store_row(
        {
            "prefix": NS,
            "key": "k2",
            "updated_at": T,
            "value": {"type": "TRACE", "data": {"trace_type": "LLM"}},
        }
    )
    assert item.metadata["action_type"] == "agent.tool_called" and item.content is None


def test_trace_other_is_decided():
    item = _item_from_store_row(
        {
            "prefix": NS,
            "key": "k3",
            "updated_at": T,
            "value": {"type": "TRACE", "data": {"trace_type": "DECISION"}},
        }
    )
    assert item.metadata["action_type"] == "agent.decided" and item.content is None


def test_no_agent_scopes_to_user():
    item = _item_from_store_row(
        {
            "prefix": "user.7.project.2.chat.9",
            "key": "k4",
            "updated_at": T,
            "value": {"type": "MESSAGE", "data": {"content": "x"}},
        }
    )
    assert item.metadata["scope"] == "user" and item.metadata["scope_id"] == "7"


def test_column_contract_fails_loud_on_drift():
    src = LangGraphStoreSource(SourceConfig(type="langgraph_store"))
    with pytest.raises(SourceContractError):
        src.validate_row({"key": "k1", "value": {}, "updated_at": T})  # missing 'prefix'


def test_registered_and_incremental():
    src = LangGraphStoreSource(SourceConfig(type="langgraph_store"))
    assert src.source_type() == "langgraph_store"
    assert src.capabilities().incremental and src.capabilities().rich_structure
    assert src.schema == "mlpal_events"  # default langgraph store schema
