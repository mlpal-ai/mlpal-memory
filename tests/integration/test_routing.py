"""M6 source routing: a ``use_case`` steers which sources memory is read from, while the
caller's scope stays a hard predicate. The ``coding_assistant`` route excludes ``slack``."""

from __future__ import annotations

ORG = {"X-Test-Org-Id": "orgA", "X-Test-User-Id": "alice"}


async def _post(client, source, statement):
    body = {"episodes": [
        {"action_type": "fact.observed", "source": source, "payload": {"statement": statement}}
    ]}
    r = await client.post("/api/v1/episodes?process=true", json=body, headers=ORG)
    assert r.status_code == 202


async def _names(client, **params):
    r = await client.get(
        "/api/v1/memory/search", params={"q": "pipeline deploy via", **params}, headers=ORG
    )
    assert r.status_code == 200
    return {n["name"] for n in r.json()["nodes"]}


async def test_use_case_routing_filters_by_source(client):
    await _post(client, "github_pr", "pipeline deploy via github")
    await _post(client, "slack", "pipeline deploy via slack")

    # no routing → both sources surface
    unrouted = await _names(client)
    assert "pipeline deploy via github" in unrouted
    assert "pipeline deploy via slack" in unrouted

    # coding_assistant allows github_pr but not slack
    routed = await _names(client, use_case="coding_assistant")
    assert "pipeline deploy via github" in routed
    assert "pipeline deploy via slack" not in routed
