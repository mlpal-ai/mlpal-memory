"""Optional MCP surface — expose memory as standard MLPal tools.

This server is **public/agent-facing** and runs every call AS THE CALLER. It serves the read tools
(``memory_search``, ``memory_get``, ``memory_answer``, ``memory_notes``) and two write verbs that are
the HOP's memory contract on the wire (memory v6): ``memory_write`` (a grounded claim into a topic
the HOP declared under ``writes``; refused here before the network when the topic is outside the
contract stamped in ``_meta``) and ``memory_endorse`` (a person's yes). No service key is attached to
any call, so a write can only land where the caller's own token may write. System/ingest writes
(``POST /api/v1/episodes`` with the internal service key) stay inside the cluster.

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
SERVED_TOOLS = ("memory_search", "memory_get", "memory_answer", "memory_notes", "memory_write", "memory_endorse", "memory_retract", "source_list", "source_query", "source_promote", "memory_document", "memory_brief")


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
    dev = os.getenv("MLPAL_DEV_AUTH", "").lower() in ("1", "true", "yes")
    if dev and incoming.get("x-test-org-id"):
        # Local-only: an eval harness that seeds a snapshot into one org must read it back as that
        # org (the yodex memory server passes `X-Test-Org-Id: ${HOP_MEMORY_ORG:-dev-org}`); without
        # this every ask-family case read the founder's real memory instead (2026-09-04, G7 3/12).
        headers = {"X-Test-Org-Id": incoming["x-test-org-id"],
                   "X-Test-User-Id": incoming.get("x-test-user-id") or os.getenv("MLPAL_DEMO_USER_ID", "demo")}
        if incoming.get("x-test-permissions"):
            headers["X-Test-Permissions"] = incoming["x-test-permissions"]
        return headers
    auth = incoming.get("authorization") or incoming.get("x-api-key")
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

    def _call_meta() -> dict:
        """The engine's per-call `_meta` (mlpal/run_id, mlpal/hop, mlpal/origin): who is writing, under
        which HOP, from which kind of run. Absent when a client sends none."""
        try:
            from fastmcp.server.dependencies import get_context

            ctx = get_context()
            rc = getattr(ctx, "request_context", None)
            meta = getattr(rc, "meta", None)
            if meta is None:
                return {}
            data = meta.model_dump() if hasattr(meta, "model_dump") else dict(meta)
            extra = data.get("model_extra") or {}
            return {**data, **extra}
        except Exception:
            return {}

    def _run_headers() -> dict[str, str]:
        """Forward the engine's `_meta` (run id, hop, origin) so the service can log which memories a
        run saw (trust by consequence, memory v6 WP6)."""
        meta = _call_meta()
        out = {}
        for k, h in (("mlpal/run_id", "X-Run-Id"), ("mlpal/hop", "X-Hop"), ("mlpal/origin", "X-Origin")):
            if meta.get(k):
                out[h] = str(meta[k])
        # WP11: the HOP's memory contract (E6) travels with every call so the service can enforce it
        for k, h in (("mlpal/memory_writes", "X-Memory-Writes"), ("mlpal/memory_reads", "X-Memory-Reads")):
            v = meta.get(k)
            if isinstance(v, (list, tuple)) and v:
                out[h] = ",".join(str(x) for x in v)
            elif isinstance(v, str) and v.strip():
                out[h] = v
        return out

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def memory_write(
        topic: str, kind: str, key: str, value: str, evidence_ids: list[str], workspace: str | None = None,
        pinned: bool = False, valid_until: str | None = None,
    ) -> dict:
        """Write one memory claim under the HOP's memory contract (hop-v1.1 §9.3).

        ``kind`` is state | learning | preference | record | deviation. ``key`` is the topic's key
        (a date, a target, a field, a run id, or "dedup" for a learning). ``value`` is the claim
        (a value for state/preference; the text for learning/deviation). ``evidence_ids`` are the
        command or message ids this claim rests on: REQUIRED and non-empty, a claim with no
        evidence is refused. A preference lands at your own scope; everything else at the org's.
        A state or preference claim for the same topic+key supersedes the previous value.
        ``pinned`` (memory v10) puts the value at the top of every session's projection until it is
        superseded, retracted or past ``valid_until`` (an ISO date or datetime) — for the facts the
        owner must always see: a credit and its expiry, a hard limit, a contract date.
        """
        if kind not in ("state", "learning", "preference", "record", "deviation"):
            return {"error": f"unknown kind {kind!r}; one of state, learning, preference, record, deviation"}
        ev = [e for e in (evidence_ids or []) if str(e).strip()]
        if not ev:
            return {"error": "a claim needs evidence_ids (the command or message ids it rests on); refused"}
        if not topic or not key or not str(value).strip():
            return {"error": "topic, key and value are required"}
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        meta = _call_meta()
        incoming = _incoming_headers()
        user = headers.get("X-Test-User-Id") or incoming.get("x-test-user-id")
        writes = meta.get("mlpal/memory_writes")
        if isinstance(writes, (list, tuple)) and writes:
            from .core.topics import TopicGrant

            if not TopicGrant(tuple(str(w) for w in writes), user_id=user).may_write(topic):
                return {"error": f"topic {topic!r} is outside this HOP's memory contract (writes {list(writes)}); "
                                 "write only your own topics, propose elsewhere through publish"}
        scope = "user" if kind == "preference" else "org"
        payload = {
            "kind": kind, "topic": topic, "key": key, "value": value, "evidence_ids": ev,
            **({"pinned": True} if pinned else {}),
            **({"valid_until": str(valid_until)} if valid_until else {}),
            **({"run_id": meta.get("mlpal/run_id")} if meta.get("mlpal/run_id") else {}),
            **({"hop": meta.get("mlpal/hop")} if meta.get("mlpal/hop") else {}),
            **({"origin": meta.get("mlpal/origin")} if meta.get("mlpal/origin") else {}),
        }
        envelope = {
            "scope": scope, **({"scope_id": user} if scope == "user" and user else {}),
            **({"workspace": workspace} if workspace else {}),
            "source": "harness_memory", "action_type": "memory.claim",
            "actor": {**({"user_id": user} if user else {})},
            "payload": payload, "content": str(value),
        }
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{_BASE}/api/v1/episodes", params={"process": "true"}, json={"episodes": [envelope]}, headers=headers)
            if r.status_code >= 400:
                return {"error": f"memory service refused the write: {r.status_code} {r.text[:200]}"}
            return {"written": r.json(), "topic": topic, "key": key, "kind": kind, "scope": scope}

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def memory_endorse(node_ids: list[str], withdraw: bool = False) -> dict:
        """A PERSON endorses memories (memory v6 §6): only when the person you are working for has
        said so, in this session, about these specific memories. Never endorse on your own judgement.
        ``withdraw`` removes that person's endorsement."""
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{_BASE}/api/v1/memory/endorse", json={"node_ids": node_ids, "withdraw": withdraw}, headers=headers)
            if r.status_code >= 400:
                return {"error": f"memory service refused: {r.status_code} {r.text[:200]}"}
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def memory_retract(node_ids: list[str], reason: str) -> dict:
        """A PERSON retracts memories (memory v7 §3): only when the person you are working for has
        said, in this session, that these specific memories are wrong or no longer hold. The fact is
        closed at now, not deleted; say why in ``reason``. Never retract on your own judgement."""
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.post(f"{_BASE}/api/v1/memory/retract", json={"node_ids": node_ids, "reason": reason}, headers=headers)
            if r.status_code >= 400:
                return {"error": f"memory service refused: {r.status_code} {r.text[:200]}"}
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def memory_document(title: str, content: str, workspace: str | None = None, valid_at: str | None = None) -> dict:
        """Keep a document the company should retain (memory v7 §1: run reports are documents, not
        memory): the daily cost mail, a watch report, a decision record. Stored verbatim in the direct
        tier under the caller's org, citeable, salience-governed like any document."""
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        body = {"title": title, "content": content, "source": "hop_reports", **({"workspace": workspace} if workspace else {}),
                **({"valid_at": valid_at} if valid_at else {})}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{_BASE}/api/v1/documents", json=body, headers=headers)
            if r.status_code >= 400:
                return {"error": f"memory service refused: {r.status_code} {r.text[:200]}"}
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def memory_brief(hop: str, window_days: int = 30) -> dict:
        """The builder's brief before step one (memory v7 §5): what earlier builds of this HOP
        learned, how each version scored, the deviations of the window, what the company already
        knows for its workspace, and fleet facts. Read it before proposing a change."""
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.get(f"{_BASE}/api/v1/memory/brief", params={"hop": hop, "window_days": window_days}, headers=headers)
            r.raise_for_status()
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def source_list() -> dict:
        """The company's registered sources (memory v7 §9): files catalogues (cold until a question
        needs an item) and read-only databases with their table allow-lists. Sources are the world;
        read them here, remember what you learn with memory_write."""
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_BASE}/api/v1/sources", headers=headers)
            r.raise_for_status()
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def source_query(source: str, sql: str) -> dict:
        """Read a registered sql source: one SELECT over its allow-listed tables, rows capped by the
        source's limit. The rows are NOT stored in memory; the returned ``query_id`` is the evidence id
        to cite when you write what you learned about the source (which table means what, which
        columns are trusted, which query answered which question) to the topic source/<name>/map."""
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=60) as c:
            r = await c.post(f"{_BASE}/api/v1/sources/{source}/query", json={"sql": sql}, headers=headers)
            if r.status_code >= 400:
                return {"error": f"memory service refused: {r.status_code} {r.text[:300]}"}
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": False, "openWorldHint": True})
    async def source_promote(source: str, query: str, limit: int = 3) -> dict:
        """Admit the cold items of a files source that match ``query`` into memory (salience floor and
        the source's daily budget still apply). memory_answer does this by itself when it finds nothing;
        call it when you know which corpus holds the answer."""
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=120) as c:
            r = await c.post(f"{_BASE}/api/v1/sources/{source}/promote", json={"query": query, "limit": limit}, headers=headers)
            if r.status_code >= 400:
                return {"error": f"memory service refused: {r.status_code} {r.text[:300]}"}
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def memory_search(
        query: str, type: str | None = None, limit: int = 10, as_of: str | None = None
    ) -> dict:
        """Search institutional memory; returns matching nodes and their neighborhood.

        ``type`` narrows to a memory kind (``state``, ``preference``, ``learning``) or an ontology
        node type; keyed state is found by its key, e.g. ``state:infra/state/cost-daily:2026-09-16``.
        ``as_of`` (ISO-8601) time-travels: return the facts that were true at that instant.
        """
        params: dict = {"q": query, "limit": limit}
        if type:
            params["type"] = type
        if as_of:
            params["as_of"] = as_of
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_BASE}/api/v1/memory/search", params=params, headers=headers)
            r.raise_for_status()
            return r.json()

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
    async def memory_answer(
        query: str, workspace: str | None = None, as_of: str | None = None,
        per_document: int = 1, max_passages: int = 5, full_passages: bool = False,
    ) -> str:
        """Answer a question from institutional memory as a MEMORY PACKET — a markdown
        document with facts, verbatim evidence (memory:// citations), contested points,
        explicit gaps, and freshness. USE THIS FIRST when starting work in an unfamiliar
        area: pass ``workspace`` (the repo/project name) to focus on knowledge learned
        there. If the packet reports gaps, say so rather than guessing. ``as_of``
        (ISO-8601) answers from what memory knew at that instant."""
        # agent_mode always: this surface serves agents by definition, and agent-mode
        # packets suppress failed-run narrative (x3 finding 5 — models mine memory for
        # prior-confirmation), rendering negative knowledge as one-line constraints.
        # memory v7 WP14: a conversation haystack wants several passages per session, more of them,
        # and whole chunks (the defaults suit a curated-docs corpus; measured on LongMemEval)
        params: dict = {"q": query, "agent_mode": "true", "per_document": per_document, "max_passages": max_passages,
                        "full_passages": str(bool(full_passages)).lower()}
        if workspace:
            params["workspace"] = workspace
        if as_of:
            params["as_of"] = as_of
        headers = {**_forward_read_headers(_incoming_headers()), **_run_headers()}
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_BASE}/api/v1/memory/answer", params=params, headers=headers)
            r.raise_for_status()
            return r.json()["markdown"]

    @mcp.tool(annotations={"readOnlyHint": True})
    async def memory_notes(workspace: str | None = None, token_budget: int = 3000) -> str:
        """The WORKING NOTES for this workspace — the curated, always-on tier: what we are
        doing now, decisions, open threads, preferences, pointers (org index → workspace note
        → your notes). READ THIS AT THE START OF A SESSION before searching; it is what a
        teammate would tell you first. Lines marked ⚠ superseded cite a fact memory has since
        replaced. Notes are updated over the REST API (PUT/PATCH /api/v1/notes)."""
        params: dict = {"token_budget": token_budget}
        if workspace:
            params["workspace"] = workspace
        headers = _forward_read_headers(_incoming_headers())
        async with httpx.AsyncClient(timeout=30) as c:
            r = await c.get(f"{_BASE}/api/v1/notes/context", params=params, headers=headers)
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
