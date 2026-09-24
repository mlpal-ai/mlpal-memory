"""memory v12 §6: a repository as a source. Registration catalogues and (eagerly) admits its
documents through the governed fold; a changed file becomes a new version; on-demand leaves items
cold; a missing credential is refused plainly; the token is never stored."""

from __future__ import annotations

import base64

import httpx
import pytest

from mlpal_memory_graph.services.connectors import github as gh

H = {"X-Test-Org-Id": "ghorg", "X-Test-User-Id": "svp", "X-Test-Permissions": "memory.read,memory.write"}
STATE = {"oncall": "# On-call\n\nThe rotation restarts on the first Monday; the pager handoff is at 09:00 UTC.", "sha": "aaa"}


def _handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p == "/repos/acme/handbook":
        return httpx.Response(200, json={"default_branch": "main"})
    if p == "/repos/acme/handbook/git/trees/main":
        return httpx.Response(200, json={"tree": [
            {"path": "docs/oncall.md", "type": "blob", "sha": STATE["sha"], "size": len(STATE["oncall"])},
            {"path": "docs/costs.md", "type": "blob", "sha": "ccc", "size": 80},
            {"path": "src/app.py", "type": "blob", "sha": "ddd", "size": 10},
            {"path": "docs/tiny.md", "type": "blob", "sha": "eee", "size": 3},
        ]})
    if p == "/repos/acme/handbook/contents/docs/oncall.md":
        return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(STATE["oncall"].encode()).decode()})
    if p == "/repos/acme/handbook/contents/docs/costs.md":
        return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"# Costs\n\nThe platform costs $42 per day after the migration on 2026-08-20.").decode()})
    if p == "/repos/acme/handbook/contents/docs/tiny.md":
        return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"hi").decode()})
    return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture(autouse=True)
def _mock_github(monkeypatch):
    monkeypatch.setattr(gh, "make_client", lambda cfg: gh.GitHubClient(gh.token_for(cfg), transport=httpx.MockTransport(_handler)))
    STATE["sha"] = "aaa"


async def _titles(client, q):
    r = await client.get("/api/v1/memory/search", params={"q": q, "limit": 20}, headers=H)
    return {p["document_title"] for p in r.json()["passages"]}


async def test_register_catalogues_admits_and_versions(client, monkeypatch):
    monkeypatch.setenv("GH_TOKEN_TEST", "secret-token")
    body = {"name": "handbook", "kind": "github", "repo": "acme/handbook", "paths": ["docs/"], "credential_ref": "GH_TOKEN_TEST", "workspace": "handbook"}
    r = await client.post("/api/v1/sources", json=body, headers=H)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["kind"] == "github" and out["repo"] == "acme/handbook" and out["branch"] == "main" and out["item_count"] == 3
    assert out["last_sync"]["admitted"] == 2 and out["last_sync"]["declined"] == 1, out["last_sync"]
    assert "secret-token" not in r.text, "the token is never stored or echoed"
    assert {"oncall", "costs"} <= await _titles(client, "pager handoff rotation costs per day")
    # the same registration again: nothing changes, nothing double-ingests
    r = await client.post("/api/v1/sources", json=body, headers=H)
    assert r.status_code == 201 and r.json()["last_sync"]["changed"] == 0 and r.json()["last_sync"]["admitted"] == 0
    # the file changes upstream: the next sync admits a new version, the old one is history
    STATE["oncall"] = "# On-call\n\nThe rotation restarts on the LAST Friday now; the pager handoff moved to 17:00 UTC."
    STATE["sha"] = "bbb"
    r = await client.post("/api/v1/sources", json=body, headers=H)
    assert r.status_code == 201 and r.json()["last_sync"]["changed"] == 1 and r.json()["last_sync"]["admitted"] == 1
    docs = (await client.get("/api/v1/documents", params={"source": "src:handbook", "limit": 50}, headers=H)).json()["documents"]
    assert sum(1 for d in docs if d["title"] == "oncall") == 2, "both versions are kept; the current one is the newest"


async def test_on_demand_leaves_items_cold_and_a_missing_credential_is_refused(client, monkeypatch):
    r = await client.post("/api/v1/sources", json={"name": "cold", "kind": "github", "repo": "acme/handbook", "admit": "on-demand"}, headers=H)
    assert r.status_code == 201, r.text
    assert r.json()["item_count"] == 3 and r.json()["last_sync"]["admitted"] == 0 and r.json()["status"] == "cold"
    assert await _titles(client, "pager handoff") == set()
    monkeypatch.delenv("GH_TOKEN_MISSING", raising=False)
    r = await client.post("/api/v1/sources", json={"name": "nope", "kind": "github", "repo": "acme/handbook", "credential_ref": "GH_TOKEN_MISSING"}, headers=H)
    assert r.status_code == 422 and "GH_TOKEN_MISSING" in r.json()["detail"]
    r = await client.post("/api/v1/sources", json={"name": "bad", "kind": "github", "repo": "not-a-repo"}, headers=H)
    assert r.status_code == 422
