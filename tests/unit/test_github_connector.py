"""Unit: the GitHub connector's pure parts and its client on a mock transport."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from mlpal_memory_graph.services.connectors.github import ConnectorError, GitHubClient, is_text_path, path_matches
from mlpal_memory_graph.services.resilience import ModelUnavailable


def test_path_filters():
    assert path_matches("docs/a.md", []) and path_matches("docs/a.md", ["docs/"]) and not path_matches("src/a.md", ["docs/"])
    assert path_matches("README.md", ["*.md"]) and path_matches("x/y/z.md", ["**/*.md", "docs/"]) is True
    assert is_text_path("docs/a.md") and is_text_path("notes/b.txt") and not is_text_path("src/a.py")
    assert not is_text_path(".github/workflows/x.yml"), "dot directories are skipped"


def _handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p == "/repos/acme/handbook":
        return httpx.Response(200, json={"default_branch": "main"})
    if p == "/repos/acme/handbook/git/trees/main":
        return httpx.Response(200, json={"tree": [{"path": "docs/oncall.md", "type": "blob", "sha": "s1", "size": 90}, {"path": "src/x.py", "type": "blob", "sha": "s2", "size": 10}]})
    if p == "/repos/acme/handbook/contents/docs/oncall.md":
        return httpx.Response(200, json={"encoding": "base64", "content": base64.b64encode(b"# On-call\n\nRotation restarts Monday.").decode()})
    if p == "/repos/acme/private":
        return httpx.Response(404, json={"message": "Not Found"})
    if p == "/repos/acme/down":
        return httpx.Response(502, text="bad gateway")
    return httpx.Response(500, text="unexpected")


async def test_client_parses_repo_tree_and_contents_and_reports_refusals():
    c = GitHubClient("tok", transport=httpx.MockTransport(_handler))
    try:
        assert await c.default_branch("acme/handbook") == "main"
        tree = await c.tree("acme/handbook", "main")
        assert [b["path"] for b in tree] == ["docs/oncall.md", "src/x.py"]
        assert (await c.blob_text("acme/handbook", "docs/oncall.md", "main")).startswith("# On-call")
        with pytest.raises(ConnectorError, match="not found"):
            await c.default_branch("acme/private")
        with pytest.raises(ModelUnavailable):
            await c.default_branch("acme/down")
    finally:
        await c.aclose()


def test_token_header_only_when_given():
    assert "Authorization" not in GitHubClient(None)._client.headers
    assert GitHubClient("t")._client.headers["Authorization"] == "Bearer t"
    json.dumps({})  # keep json imported for the handler above
