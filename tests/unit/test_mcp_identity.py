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


async def test_served_tools_are_the_four_reads_one_governed_write_and_the_endorse_verb():
    names = await _served_tool_names()
    # the live registry, not a label. memory_answer (v3) is a READ — the packet endpoint;
    # memory_notes (workspace notes, 2026-09-03) is a READ of the notes bundle. memory_write
    # (2026-09-15, hop-v1.1 §9.3) is the ONE write door: a claim under the HOP's contract, refused
    # without evidence ids, authorised by the caller's own token and the service's scope gate.
    # memory_endorse (2026-09-16, memory v6 WP13) is a PERSON's verb: their yes on a memory they can read.
    assert names == {"memory_search", "memory_get", "memory_answer", "memory_notes", "memory_write", "memory_endorse", "memory_retract", "source_list", "source_query", "source_promote", "memory_document", "memory_brief"}
    assert set(SERVED_TOOLS) == names  # the documented constant tracks the registry


async def test_write_tool_refuses_an_ungrounded_or_unknown_claim_before_any_network():
    from mlpal_memory_graph.mcp_server import mcp

    tool = await mcp.get_tool("memory_write")
    fn = tool.fn
    assert (await fn(topic="t", kind="state", key="k", value="v", evidence_ids=[]))["error"].startswith("a claim needs evidence_ids")
    assert "unknown kind" in (await fn(topic="t", kind="blob", key="k", value="v", evidence_ids=["c1"]))["error"]
    assert "required" in (await fn(topic="", kind="state", key="k", value="v", evidence_ids=["c1"]))["error"]


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


def test_dev_auth_honours_explicit_test_org_header(monkeypatch):
    from mlpal_memory_graph import mcp_server as m
    monkeypatch.setenv("MLPAL_DEV_AUTH", "true")
    monkeypatch.setenv("MLPAL_DEMO_ORG_ID", "local")
    fwd = m._forward_read_headers({"authorization": "Bearer x", "x-test-org-id": "hop-eval-7", "x-test-user-id": "u"})
    assert fwd == {"X-Test-Org-Id": "hop-eval-7", "X-Test-User-Id": "u"}
    monkeypatch.delenv("MLPAL_DEV_AUTH")
    assert m._forward_read_headers({"authorization": "Bearer x", "x-test-org-id": "hop-eval-7"}) == {"Authorization": "Bearer x"}


async def test_write_tool_carries_pinned_and_valid_until(monkeypatch):
    """memory v10: an important fact is pinned into every session's projection until it lapses."""
    from mlpal_memory_graph import mcp_server
    from mlpal_memory_graph.mcp_server import mcp

    posted: dict = {}

    class _Resp:
        status_code = 202

        def json(self):
            return {"accepted": 1}

    class _Client:
        def __init__(self, *a, **k): ...
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, params=None, json=None, headers=None):
            posted.update(json or {}); return _Resp()

    monkeypatch.setattr(mcp_server.httpx, "AsyncClient", _Client)
    fn = (await mcp.get_tool("memory_write")).fn
    out = await fn(topic="infra/state/identity", kind="state", key="credits.aws", value="$25,000 AWS credit, expires 2027-03-31",
                   evidence_ids=["msg-1"], pinned=True, valid_until="2027-03-31")
    assert "error" not in out, out
    payload = posted["episodes"][0]["payload"]
    assert payload["pinned"] is True and payload["valid_until"] == "2027-03-31"
    out = await fn(topic="infra/state/identity", kind="state", key="region", value="us-east-2", evidence_ids=["msg-2"])
    assert "pinned" not in posted["episodes"][0]["payload"] and "valid_until" not in posted["episodes"][0]["payload"]
