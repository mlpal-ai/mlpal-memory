"""Value-facts (P0.1): watched values get current-value semantics — the mechanism
x6/x6b/x6c all showed missing. Covers extraction, supersession through the fold,
re-observation, backfill, and packet behavior (current view vs as-of)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from mlpal_memory_graph.pipeline.value_facts import extract_value_specs

H = {
    "X-Test-Org-Id": "orgA",
    "X-Test-User-Id": "alice",
    "X-Test-Permissions": "memory.read,memory.write",
}


def test_extract_value_specs_patterns():
    ents, edges = extract_value_specs(
        "Cost check: the platform runs roughly $36 a day with the new nodegroups. Later "
        "the platform costs $32 per day after re-architecture. Cluster runs kubernetes "
        "1.33 on EKS."
    )
    keys = {e.key for e in ents}
    assert "metric:daily-cost" in keys
    # narrative-order rule: one observation per key per doc — the LAST match wins
    assert "metric:daily-cost=32" in keys and "metric:daily-cost=36" not in keys
    assert "setting:k8s-version=1.33" in keys
    assert all(e.functional for e in edges) and all(e.type == "HAS_VALUE" for e in edges)
    # same value twice in one doc collapses to one observation
    ents2, _ = extract_value_specs(
        "the platform costs $32 per day. the platform still costs $32 per day."
    )
    assert sum(1 for e in ents2 if e.key == "metric:daily-cost=32") == 1


async def _ingest(client, content: str, days_ago: int) -> None:
    r = await client.post(
        "/api/v1/documents",
        json={
            "content": content, "title": content[:30], "scope": "user",
            "scope_id": "alice", "source": "md_file", "workspace": "vf-lab",
            "valid_at": (datetime.now(UTC) - timedelta(days=days_ago)).isoformat(),
        },
        headers=H,
    )
    assert r.status_code == 202, r.text


@pytest.mark.asyncio
async def test_value_supersession_end_to_end(client):
    """Old value restated MANY times, new value once — the packet must serve the
    new value as fact and stop serving the old one (the x6c failure, fixed)."""
    for days in (40, 35, 30):
        await _ingest(client, f"note {days}: the platform costs $52.6 per day.", days)
    await _ingest(client, "steady state: the platform costs $32 per day now.", 2)

    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "platform daily cost", "workspace": "vf-lab"},
        headers=H,
    )
    md = r.json()["markdown"]
    assert "platform daily cost = 32" in md
    facts_section = md.split("## Evidence")[0]
    assert "= 52.6" not in facts_section, "superseded value served as a current fact"

    # as-of a month ago: the OLD value is the truth
    asof = (datetime.now(UTC) - timedelta(days=20)).isoformat()
    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "platform daily cost", "workspace": "vf-lab", "as_of": asof},
        headers=H,
    )
    md = r.json()["markdown"]
    assert "= 52.6" in md
    assert "= 32" not in md.split("## Evidence")[0]


@pytest.mark.asyncio
async def test_value_reobservation_reopens(client):
    """A value that comes BACK is current again, not stuck superseded."""
    await _ingest(client, "metrics: the platform costs $40 per day.", 30)
    await _ingest(client, "spike: the platform costs $311 per day!", 20)
    await _ingest(client, "resolved: the platform costs $40 per day again.", 10)

    r = await client.get(
        "/api/v1/memory/answer",
        params={"q": "platform daily cost", "workspace": "vf-lab"},
        headers=H,
    )
    facts_section = r.json()["markdown"].split("## Evidence")[0]
    assert "= 40" in facts_section
    assert "= 311" not in facts_section


def test_subject_guards_reject_other_entities():
    """x6c2 regression: the OLD ACCOUNT's teardown billing must not become the
    platform's daily cost; hypotheticals and projections are not observations."""
    ents, _ = extract_value_specs(
        "the old account is still billing ~$70/day to the UC Davis org until teardown."
    )
    assert not any(e.key.startswith("metric:daily-cost=") for e in ents)
    ents, _ = extract_value_specs("Projected old-account spend: ~$70/day.")
    assert not any(e.key.startswith("metric:daily-cost=") for e in ents)
    # the real observation still extracts
    ents, _ = extract_value_specs("steady state: the platform costs $32 per day.")
    assert any(e.key == "metric:daily-cost=32" for e in ents)


@pytest.mark.asyncio
async def test_metric_history_endpoint(client):
    """Timeline surface: the full value history with validity windows, owner-scoped."""
    await _ingest(client, "review: the platform costs $50 per day.", 30)
    await _ingest(client, "steady: the platform costs $28 per day now.", 1)
    r = await client.get(
        "/api/v1/memory/metrics", params={"workspace": "vf-lab"}, headers=H
    )
    assert r.status_code == 200
    metrics = {m["key"]: m for m in r.json()["metrics"]}
    hist = metrics["metric:daily-cost"]["values"]
    assert [v["value"] for v in hist][:2] and hist[-1]["current"] is True
    closed = [v for v in hist if not v["current"]]
    assert closed and all(v["invalid_at"] for v in closed)
    # another user cannot see alice's personal metric history
    r = await client.get(
        "/api/v1/memory/metrics",
        params={"workspace": "vf-lab"},
        headers={**H, "X-Test-User-Id": "mallory"},
    )
    assert all(m["key"] != "metric:daily-cost" for m in r.json()["metrics"])


def test_retrospective_multi_value_takes_last():
    """A retrospective stating both eras' values yields ONE observation — the last
    (narrative order ends on the current value); verbless forms extract too."""
    ents, _ = extract_value_specs(
        "Cost. Destination steady-state at full platform: $52.6/day. After "
        "re-architecture: **~$32/day (-38%)** with more redundancy."
    )
    vals = [e.key for e in ents if e.key.startswith("metric:daily-cost=")]
    assert vals == ["metric:daily-cost=32"]
    ents, _ = extract_value_specs("Cluster: EKS 1.33 upgraded to 1.36 during cutover.")
    vals = [e.key for e in ents if e.key.startswith("setting:eks-version=")]
    assert vals == ["setting:eks-version=1.36"]


@pytest.mark.asyncio
async def test_llm_value_tier_grounding(monkeypatch):
    """Precision tier: model output is only trusted when the quote is verbatim and
    the key is watched; failures degrade to the pattern tier."""
    from mlpal_memory_graph.pipeline import value_facts
    from mlpal_memory_graph.services import llm_client as lc

    class Stub:
        async def complete_json(self, **_):
            return {"values": [
                {"key": "metric:daily-cost", "value": "$32", "quote": "now ~$32/day after"},
                {"key": "metric:daily-cost", "value": "52.6", "quote": "was $52.6/day"},
                {"key": "metric:unwatched", "value": "9", "quote": "now ~$32/day after"},
                {"key": "setting:eks-version", "value": "1.36", "quote": "NOT IN TEXT"},
            ]}

    monkeypatch.setattr(lc, "get_llm_client", lambda: Stub())
    text = "Cost narrative: the platform was $52.6/day, now ~$32/day after re-architecture."
    ents, edges = await value_facts.llm_extract_value_specs(text)
    keys = [e.key for e in ents if "=" in e.key]
    assert keys == ["metric:daily-cost=32"]  # dup key dropped, unwatched dropped,
    assert len(edges) == 1                    # ungrounded quote dropped, $ stripped

    class Boom:
        async def complete_json(self, **_):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(lc, "get_llm_client", lambda: Boom())
    ents, _ = await value_facts.llm_extract_value_specs(
        "steady state: the platform costs $28 per day."
    )
    assert any(e.key == "metric:daily-cost=28" for e in ents)  # pattern fallback


@pytest.mark.asyncio
async def test_llm_state_tier_lifecycle_flips(monkeypatch):
    """x11: state flips announced as passing mentions ("status.mlpal.ai LIVE" inside
    a teardown worklog) produced ZERO derived facts and lost retrieval to topical
    stale docs. The state tier concentrates them: closed enum, verbatim quote,
    stopword-stable keys ("public status page" == "status page"), silent on failure."""
    from mlpal_memory_graph.pipeline import value_facts
    from mlpal_memory_graph.services import llm_client as lc

    text = (
        "Teardown day. docs host terminated. Also: status.mlpal.ai LIVE "
        "(CF + OAC S3, survives platform outages). fc services fully retired."
    )

    class Stub:
        async def complete_json(self, **_):
            return {"states": [
                {"subject": "public status page", "state": "live", "quote": "status.mlpal.ai LIVE"},
                {"subject": "the status page", "state": "live", "quote": "status.mlpal.ai LIVE"},
                {"subject": "fc services", "state": "retired", "quote": "fc services fully retired"},
                {"subject": "docs site", "state": "exploded", "quote": "docs host terminated"},
                {"subject": "signup", "state": "open", "quote": "NOT IN TEXT"},
            ]}

    monkeypatch.setattr(lc, "get_llm_client", lambda: Stub())
    ents, edges = await value_facts.llm_extract_state_specs(text)
    keys = sorted(e.key for e in ents if "=" in e.key)
    # dup subject merged by stable key; unknown state dropped; ungrounded quote dropped
    assert keys == ["state:fc-services=retired", "state:status-page=live"]
    assert len(edges) == 2 and all(e.functional for e in edges)

    # cost gate: no lifecycle marker -> no model call at all
    class Boom:
        async def complete_json(self, **_):
            raise AssertionError("should not be called")

    monkeypatch.setattr(lc, "get_llm_client", lambda: Boom())
    ents, _ = await value_facts.llm_extract_state_specs("We refactored the ranking code.")
    assert ents == []

    # model failure -> silence, never noise
    class Down:
        async def complete_json(self, **_):
            raise RuntimeError("gateway down")

    monkeypatch.setattr(lc, "get_llm_client", lambda: Down())
    ents, _ = await value_facts.llm_extract_state_specs("The docs host was retired today.")
    assert ents == []


def test_canonical_subject_snapping():
    """Deterministic code-side canonicalization (the traced prompt-side variant was
    erratic): subset either way merges, Jaccard >= 0.5 merges, components stay apart."""
    from mlpal_memory_graph.pipeline.value_facts import canonical_subject

    known = ["status page", "fc router", "auth"]
    assert canonical_subject("public status page", known) == "status page"
    assert canonical_subject("status page design", known) == "status page"
    assert canonical_subject("mlpal-auth", known) == "auth"
    assert canonical_subject("fc console", known) == "fc console"  # 1/3 shared: distinct
    assert canonical_subject("billing sync", known) == "billing sync"  # novel stays novel
