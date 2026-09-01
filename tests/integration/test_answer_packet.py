"""The memory packet (/memory/answer): llms.txt-shaped markdown, citations, recency
ranking, contested labels, honest abstention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

H = {
    "X-Test-Org-Id": "orgA",
    "X-Test-User-Id": "alice",
    "X-Test-Permissions": "memory.read,memory.write",
}


async def _ingest_doc(client, content: str, valid_at: datetime, title: str) -> None:
    r = await client.post(
        "/api/v1/documents",
        json={
            "content": content,
            "title": title,
            "scope": "user",
            "scope_id": "alice",
            "source": "md_file",
            "workspace": "repo-x",
            "valid_at": valid_at.isoformat(),
        },
        headers=H,
    )
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_packet_shape_citations_and_recency(client):
    now = datetime.now(UTC)
    # same topic, two vintages: the OLD one says maven, the NEW one says gradle
    await _ingest_doc(
        client,
        "The build system for repo-x is maven. Run mvn install before tests.",
        now - timedelta(days=700),
        "old build notes",
    )
    await _ingest_doc(
        client,
        "The build system for repo-x is gradle. Run gradle build before tests.",
        now - timedelta(days=5),
        "current build notes",
    )

    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "what build system does repo-x use", "workspace": "repo-x"},
        headers=H,
    )
    assert r.status_code == 200
    body = r.json()
    md = body["markdown"]

    # llms.txt shape: H1 topic + blockquote TL;DR + H2 sections
    assert md.startswith("# what build system does repo-x use")
    assert "\n> " in md
    assert "## Evidence" in md
    assert "memory://chunk/" in md  # citations resolve to inspectable ids
    assert "## Freshness" in md

    # recency decay: the 5-day-old gradle doc must outrank the 700-day-old maven doc
    assert md.index("gradle") < md.index("maven")
    assert body["passages"] >= 2
    assert body["took_ms"] < 3000


@pytest.mark.asyncio
async def test_packet_abstains_honestly(client):
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "what is the quarterly revenue of atlantis"},
        headers=H,
    )
    body = r.json()
    assert body["facts"] == 0 and body["passages"] == 0
    assert "## Gaps" in body["markdown"]
    assert "no relevant knowledge" in body["markdown"].lower()


@pytest.mark.asyncio
async def test_packet_labels_contested_facts(client, session):
    from mlpal_memory_graph.core.scope import Scope, ScopeRef
    from mlpal_memory_graph.graph import get_driver

    drv = get_driver()
    a = await drv.upsert_node(
        session, tenant_id="orgA", scope=ScopeRef(Scope.USER, "alice"),
        type_="Convention", key="deploy-day", name="we deploy on fridays",
    )
    a.derived_from = ["e1"]
    await session.commit()
    await client.post(
        "/api/v1/memory/publish", json={"node_ids": [a.id]}, headers=H
    )
    b = await drv.upsert_node(
        session, tenant_id="orgA", scope=ScopeRef(Scope.USER, "bob"),
        type_="Convention", key="deploy-day", name="we never deploy on fridays",
    )
    b.derived_from = ["e2"]
    await session.commit()
    await client.post(
        "/api/v1/memory/publish",
        json={"node_ids": [b.id]},
        headers={**H, "X-Test-User-Id": "bob"},
    )

    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "when do we deploy fridays"},
        headers=H,
    )
    md = r.json()["markdown"]
    assert "## Contested" in md
    assert "do not present either side as settled" in md


@pytest.mark.asyncio
async def test_failed_run_hypotheses_never_present_as_facts(client, session):
    """x2 finding 3: insights from failed runs render as unverified leads, not Facts."""
    from mlpal_memory_graph.core.scope import Scope, ScopeRef
    from mlpal_memory_graph.graph import get_driver

    drv = get_driver()
    n = await drv.upsert_node(
        session, tenant_id="orgA", scope=ScopeRef(Scope.USER, "alice"),
        type_="Fact", key="wrong-theory",
        name="the bug is in Float hashing precision",
        props={"hypothesis_from_failed_attempt": True, "run_result": "max_turns"},
    )
    n.derived_from = ["x2-attempt"]
    n.workspace = "repo-x"
    await session.commit()

    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "float hashing precision bug", "workspace": "repo-x"},
        headers=H,
    )
    md = r.json()["markdown"]
    assert "## Prior attempts (unverified)" in md
    assert "DID NOT verify" in md
    assert "from a `max_turns` run" in md
    # and it must not lead the packet as the TL;DR fact
    assert not md.split("\n")[1].startswith("> the bug is in Float hashing")


@pytest.mark.asyncio
async def test_failed_attempt_evidence_is_labeled_and_downranked(client):
    """x3 finding 4: evidence from failed runs carries the warning inline and ranks
    below verified-run evidence of equal relevance."""
    from datetime import UTC, datetime

    now = datetime.now(UTC)
    for src, text in (
        ("yodex_failed", "Found it. The retry bug is in the cache layer keys."),
        ("yodex", "Verified: the retry bug fix belongs in the queue consumer ack path."),
    ):
        r = await client.post(
            "/api/v1/documents",
            json={
                "content": text, "title": f"attempt ({src})", "scope": "user",
                "scope_id": "alice", "source": src, "workspace": "repo-y",
                "valid_at": now.isoformat(),
            },
            headers=H,
        )
        assert r.status_code == 202
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "where is the retry bug", "workspace": "repo-y"},
        headers=H,
    )
    md = r.json()["markdown"]
    assert "⚠ from a FAILED attempt" in md
    assert md.index("queue consumer ack") < md.index("cache layer keys")


@pytest.mark.asyncio
async def test_agent_mode_suppresses_failed_narrative_renders_constraints(client, session):
    """x3 finding 5: agent-mode packets exclude failed-run content entirely and turn
    negative knowledge into one-line constraints (models cherry-pick narratives)."""
    from datetime import UTC, datetime

    from mlpal_memory_graph.core.scope import Scope, ScopeRef
    from mlpal_memory_graph.graph import get_driver

    drv = get_driver()
    n = await drv.upsert_node(
        session, tenant_id="orgA", scope=ScopeRef(Scope.USER, "alice"),
        type_="Fact", key="dead-end", name="the fix is in Float hashing",
        props={"hypothesis_from_failed_attempt": True, "run_result": "max_turns"},
    )
    n.derived_from = ["e"]
    n.workspace = "repo-z"
    await session.commit()
    await client.post(
        "/api/v1/documents",
        json={"content": "Found it! Float hashing precision is definitely the bug here.",
              "title": "failed attempt", "scope": "user", "scope_id": "alice",
              "source": "yodex_failed", "workspace": "repo-z",
              "valid_at": datetime.now(UTC).isoformat()},
        headers=H,
    )
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "float hashing fix", "workspace": "repo-z", "agent_mode": "true"},
        headers=H,
    )
    md = r.json()["markdown"]
    assert "## Ruled out (do not pursue)" in md
    assert "dead end" in md
    assert "Found it! Float hashing precision" not in md  # narrative fully suppressed


@pytest.mark.asyncio
async def test_synthesized_mode_wiring_and_empty_abstention(client, monkeypatch):
    """x5: mode=synthesized composes FROM the packet via the LLM seam; an empty
    packet short-circuits to the packet's abstention with ZERO model calls."""
    from mlpal_memory_graph.services import synthesis

    calls: list[str] = []

    async def fake_synth(*, query, packet_markdown, model=None):
        calls.append(query)
        assert "memory://" in packet_markdown  # composes from the real packet
        return "The build system is gradle [memory://chunk/x].", {"output_tokens": 12}

    monkeypatch.setattr(synthesis, "synthesize_answer", fake_synth)

    # empty store for this query -> abstention, no synthesis call
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "quarterly revenue of atlantis", "mode": "synthesized"},
        headers=H,
    )
    assert r.json()["mode"] == "synthesized" and r.json()["synth_ms"] is None
    assert not calls, "empty packet must not spend a model call"
    assert "no relevant knowledge" in r.json()["markdown"].lower()

    # non-empty store -> synthesized answer replaces the packet; hybrid appends it
    now = datetime.now(UTC)
    await _ingest_doc(client, "The build system for repo-s is gradle.", now, "notes")
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "build system for repo-s", "workspace": "repo-x",
                "mode": "synthesized"},
        headers=H,
    )
    body = r.json()
    assert calls and body["markdown"].startswith("The build system is gradle")
    assert body["synth_ms"] is not None
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "build system for repo-s", "workspace": "repo-x", "mode": "hybrid"},
        headers=H,
    )
    md = r.json()["markdown"]
    assert md.startswith("The build system is gradle") and "## Evidence" in md


@pytest.mark.asyncio
async def test_as_of_excludes_future_documents(client):
    """x6 gap fix: a point-in-time answer must not see documents from after the
    as-of instant (measured pre-fix: 9/12 as-of queries leaked future knowledge)."""
    now = datetime.now(UTC)
    await _ingest_doc(client, "The cache engine for repo-t is redis 7 without tls.",
                      now - timedelta(days=400), "old cache notes")
    await _ingest_doc(client, "The cache engine for repo-t is valkey 8.2, tls only.",
                      now - timedelta(days=3), "new cache notes")

    asof = (now - timedelta(days=100)).isoformat()
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "what cache engine does repo-t use", "workspace": "repo-x",
                "as_of": asof},
        headers=H,
    )
    md = r.json()["markdown"]
    assert "redis 7" in md
    assert "valkey" not in md, "future document leaked into a point-in-time answer"

    # current view still serves the new truth first
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "what cache engine does repo-t use", "workspace": "repo-x"},
        headers=H,
    )
    md = r.json()["markdown"]
    assert md.index("valkey") < md.index("redis 7")


@pytest.mark.asyncio
async def test_current_value_fact_leads_packet_and_labels_stale_evidence(client):
    """x11: the store served the current watched value while older verbatim passages
    shouted the superseded one — and an identical reader followed the louder stale
    evidence. A current MetricValue matching the query must LEAD the packet, and
    evidence older than the value must carry a predates label."""
    await _ingest_doc(
        client,
        "Cluster inventory: our eks cluster runs kubernetes 1.33 (eks.41), verified live.",
        datetime.now(UTC) - timedelta(days=30),
        "old inventory",
    )
    await _ingest_doc(
        client,
        "Post-upgrade check: the eks cluster now runs kubernetes 1.36 across all nodegroups.",
        datetime.now(UTC) - timedelta(days=2),
        "upgrade note",
    )
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "what kubernetes version does the eks cluster run", "workspace": "repo-x"},
        headers=H,
    )
    assert r.status_code == 200
    md = r.json()["markdown"]
    tldr = md.splitlines()[1]
    assert "1.36" in tldr and "current" in tldr, tldr
    assert "1.33" not in tldr
    # the 30-day-old passage predates the current value and is labeled
    assert "predates the current value" in md
