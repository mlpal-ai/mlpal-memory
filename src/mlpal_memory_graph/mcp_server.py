"""Optional MCP surface — expose memory as standard MLPal tools.

This server is **READ-ONLY and public/agent-facing**: it serves exactly ``memory_search`` and
``memory_get``. There is deliberately NO write tool here. System/ingest writes go through the REST
API (``POST /api/v1/episodes`` with the internal service key) from inside the cluster — they don't
belong on an agent-reachable connector, where a write tool would run with the service key and
could target any tenant (cross-tenant write escalation). If a system-only MCP write surface is
ever wanted, it is a SEPARATE, non-public server with its own auth — not this one.

Tools are thin clients over this service's REST API (``MLPAL_MEMORY_SERVICE_URL``), so the MCP can
run as its own pod/sidecar. Reads run AS THE CALLER: we forward the caller's bearer credential and
attach NO service key, so a read with no caller credential fails closed (REST → 401) rather than
silently escalating. The platform wrapper (``@mlpal_mcp_server``) adds auth + telemetry +
/health|/info|/metrics and is only needed to actually start the server; building ``mcp`` and its
tools needs only ``fastmcp``.

Run:  python -m mlpal_memory_graph.mcp_server   (requires extras: fastmcp + mlpal-mcp)
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import httpx

_BASE = os.getenv("MLPAL_MEMORY_SERVICE_URL", "http://localhost:8000").rstrip("/")

# The canonical served tool set — this server is read-only. Pinned by tests/unit/test_mcp_identity
# (both the live ``mcp.list_tools()`` and this constant) so a write tool can't silently reappear.
SERVED_TOOLS = ("memory_search", "memory_get", "memory_answer")


def _forward_read_headers(incoming: Mapping[str, str]) -> dict[str, str]:
    """Headers for a *user-facing* read — run AS THE CALLER, never as the service.

    We forward the caller's bearer credential so the REST API validates it and resolves the real
    (org, user, permissions); that is what makes a read scope-correct instead of seeing
    everything. We deliberately attach NO internal service key: a read with no caller credential
    must fail closed (REST → 401) rather than silently escalate to the omniscient internal
    identity. ``incoming`` keys are case-insensitive HTTP header names.

    Defense-in-depth (the REST API is the real boundary): the caller token is the *only* per-user
    identity bridge, so if the internal service key happens to be present in this process's env
    AND a mis-wired consumer passes it where a user token belongs, refuse the read loudly — it
    would otherwise run unscoped. On the read-only public sidecar that key is absent, so this is
    a no-op there; the check costs nothing and catches a co-located misconfiguration.
    """
    auth = incoming.get("authorization")
    if not auth:
        # Dev/demo fallback: when there's no caller token (e.g. a local harness MCP connection that
        # can't attach headers), read as a configured demo identity. Env-gated → no-op in prod
        # (the public sidecar has neither flag set and stays fail-closed).
        if os.getenv("MLPAL_DEV_AUTH", "").lower() in ("1", "true", "yes"):
            org = os.getenv("MLPAL_DEMO_ORG_ID")
            if org:
                headers = {"X-Test-Org-Id": org,
                           "X-Test-User-Id": os.getenv("MLPAL_DEMO_USER_ID", "demo")}
                perms = os.getenv("MLPAL_DEMO_PERMISSIONS")
                if perms:
                    headers["X-Test-Permissions"] = perms
                return headers
        return {}
    token = auth[7:].strip() if auth[:7].lower() == "bearer " else auth.strip()
    service_key = os.getenv("MLPAL_INTERNAL_SERVICE_API_KEY")
    if service_key and token == service_key:
        raise PermissionError(
            "memory read requires the caller's user token, not a service token (scope lost)"
        )
    return {"Authorization": auth}


def _incoming_headers() -> dict[str, str]:
    """The live MCP request's HTTP headers (lowercased keys), or {} off the HTTP transport.

    Mirrors how the platform auth middleware reads the caller token
    (mlpal_auth.mcp.middleware uses the same ``get_http_request`` accessor).
    """
    try:
        from fastmcp.server.dependencies import get_http_request

        request = get_http_request()
        return dict(request.headers) if request is not None else {}
    except Exception:  # no HTTP request in scope (e.g. stdio transport) — fail closed
        return {}


try:
    from fastmcp import FastMCP

    _FASTMCP_AVAILABLE = True
except ImportError:  # pragma: no cover
    _FASTMCP_AVAILABLE = False


if _FASTMCP_AVAILABLE:
    mcp = FastMCP("memory_graph_mcp")

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def memory_search(
        query: str, type: str | None = None, limit: int = 10, as_of: str | None = None
    ) -> dict:
        """Search institutional memory; returns matching nodes and their neighborhood.

        ``as_of`` (ISO-8601) time-travels: return the facts that were true at that instant.
        """
        params: dict = {"q": query, "limit": limit}
        if type:
            params["type"] = type
        if as_of:
            params["as_of"] = as_of
        headers = _forward_read_headers(_incoming_headers())
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_BASE}/api/v1/memory/search", params=params, headers=headers)
            r.raise_for_status()
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def memory_answer(
        query: str, workspace: str | None = None, as_of: str | None = None
    ) -> str:
        """Answer a question from institutional memory as a MEMORY PACKET — a markdown
        document with facts, verbatim evidence (memory:// citations), contested points,
        explicit gaps, and freshness. USE THIS FIRST when starting work in an unfamiliar
        area: pass ``workspace`` (the repo/project name) to focus on knowledge learned
        there. If the packet reports gaps, say so rather than guessing. ``as_of``
        (ISO-8601) answers from what memory knew at that instant."""
        params: dict = {"q": query}
        if workspace:
            params["workspace"] = workspace
        if as_of:
            params["as_of"] = as_of
        headers = _forward_read_headers(_incoming_headers())
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_BASE}/api/v1/memory/answer", params=params, headers=headers)
            r.raise_for_status()
            return r.json()["markdown"]

    @mcp.tool(annotations={"readOnlyHint": True})
    async def memory_get(node_id: str, depth: int = 1, as_of: str | None = None) -> dict:
        """Fetch a memory node and its neighborhood by id (``as_of`` ISO-8601 to time-travel)."""
        params: dict = {"depth": depth}
        if as_of:
            params["as_of"] = as_of
        headers = _forward_read_headers(_incoming_headers())
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(
                f"{_BASE}/api/v1/memory/nodes/{node_id}",
                params=params,
                headers=headers,
            )
            r.raise_for_status()
            return r.json()


def main() -> None:
    """Start the read-only memory MCP.

    Managed: behind the platform wrapper (auth/telemetry/health, ``mlpal-mcp``).
    Self-hosted/OSS: plain FastMCP HTTP server — same tools, same fail-closed
    header forwarding; reads still authenticate at the REST API boundary.
    """
    if not _FASTMCP_AVAILABLE:
        raise SystemExit("MCP surface requires the fastmcp extra: pip install fastmcp")
    try:
        from mlpal_mcp import mlpal_mcp_server
    except ImportError:
        from starlette.requests import Request
        from starlette.responses import JSONResponse

        @mcp.custom_route("/health", methods=["GET"])
        async def health(_: Request) -> JSONResponse:
            return JSONResponse({"status": "ok", "service": "mlpal-memory-mcp"})

        mcp.run(
            transport="http",
            host=os.getenv("MCP_HOST", "0.0.0.0"),
            port=int(os.getenv("MCP_PORT", "8011")),
        )
        return

    @mlpal_mcp_server(config_path="mcp_config.yaml")
    class MemoryGraphMCP:
        """Memory Graph MCP — READ-ONLY institutional memory (search + get)."""

        server = mcp

    MemoryGraphMCP.start()


if __name__ == "__main__":
    main()
