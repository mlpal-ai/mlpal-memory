"""MCP surface contract: READ-ONLY served tools + reads run AS THE CALLER.

Two invariants, both load-bearing for tenant isolation:
  1. The public/agent-facing MCP serves ONLY memory_search + memory_get. memory_write is ABSENT —
     not merely labelled system-only — so an agent connection can't issue a service-key write that
     targets any org (cross-tenant write escalation). Asserted against the LIVE registry
     (mcp.list_tools()), not a policy constant, so a re-added @mcp.tool fails the test.
  2. Reads forward the caller's bearer and attach no service key; missing credential fails closed;
     a service token smuggled in as the caller token is refused.
"""

from __future__ import annotations

import pytest

from mlpal_memory_graph.mcp_server import SERVED_TOOLS, _forward_read_headers

SERVICE_KEY = "svc-secret-xyz"


async def _served_tool_names() -> set[str]:
    from mlpal_memory_graph.mcp_server import mcp

    return {t.name for t in await mcp.list_tools()}


async def test_served_tools_are_read_only_no_write():
    names = await _served_tool_names()
    # the live registry, not a label. memory_answer (v3) is a READ — the packet endpoint.
    assert names == {"memory_search", "memory_get", "memory_answer"}
    assert "memory_write" not in names
    assert set(SERVED_TOOLS) == names  # the documented constant tracks the registry


def test_read_forwards_caller_bearer_token():
    out = _forward_read_headers({"authorization": "Bearer mlpal_sk_abc", "x-other": "v"})
    assert out == {"Authorization": "Bearer mlpal_sk_abc"}


def test_read_never_attaches_internal_service_key():
    # even if a caller tries to smuggle one in, reads forward only the bearer credential
    out = _forward_read_headers({"authorization": "Bearer t", "x-internal-service-key": "anything"})
    assert "X-Internal-Service-Key" not in out
    assert out == {"Authorization": "Bearer t"}


def test_read_without_credential_fails_closed():
    assert _forward_read_headers({}) == {}
    assert _forward_read_headers({"x-internal-service-key": "anything"}) == {}


def test_read_rejects_a_service_token_passed_as_caller_token(monkeypatch):
    # defense-in-depth: if the service key is present in env AND passed as the caller bearer, the
    # read would run unscoped — fail fast (covers a mis-wired / co-located consumer)
    monkeypatch.setenv("MLPAL_INTERNAL_SERVICE_API_KEY", SERVICE_KEY)
    with pytest.raises(PermissionError):
        _forward_read_headers({"authorization": f"Bearer {SERVICE_KEY}"})
    with pytest.raises(PermissionError):
        _forward_read_headers({"authorization": SERVICE_KEY})  # no Bearer prefix either


def test_guard_is_noop_when_no_service_key_in_env(monkeypatch):
    # on the read-only sidecar the key is absent → the guard holds no secret and never fires
    monkeypatch.delenv("MLPAL_INTERNAL_SERVICE_API_KEY", raising=False)
    assert _forward_read_headers({"authorization": "Bearer whatever"}) == {
        "Authorization": "Bearer whatever"
    }
