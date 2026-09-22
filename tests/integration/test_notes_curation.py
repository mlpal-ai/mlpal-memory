"""memory v10: the nightly note curation — stale open threads close, run pointers older than the
ttl close, a person's own lines stay, and Now carries a current-state block rebuilt from the
HOP's injected state topics. Deterministic and idempotent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mlpal_memory_graph.db import get_session_factory
from mlpal_memory_graph.services.notes_curation import STATE_BEGIN, close_stale_threads, curate_workspace_notes, rebuild_now

H = {"X-Test-Org-Id": "orgN", "X-Test-User-Id": "svp", "X-Test-Permissions": "memory.read,memory.write"}


def test_close_stale_threads_rules():
    now = datetime(2026, 9, 22, tzinfo=UTC)
    section = "\n".join([
        "- a living thread [memory://node/aaaaaaaa-1111]",
        "- a dead thread [memory://edge/bbbbbbbb-2222]",
        "- r1 cost-30d: see memory://hop-run:cost-30d-r1 (result status ok) 2026-09-04",
        "- r9 cost-30d: see memory://hop-run:cost-30d-r9 (result status ok) 2026-09-21",
        "- a person's own line with no citation",
    ])
    run_dates = {"cost-30d-r1": datetime(2026, 9, 4, tzinfo=UTC), "cost-30d-r9": datetime(2026, 9, 21, tzinfo=UTC)}
    kept, closed = close_stale_threads(section, {"memory://edge/bbbbbbbb-2222"}, now=now, ttl_days=14, run_dates=run_dates)
    assert "living thread" in kept and "own line" in kept and "r9 cost-30d" in kept
    assert len(closed) == 2 and any("dead thread" in c for c in closed) and any("r1 cost-30d" in c for c in closed)
    # a pointer with no document behind it is dangling: closed whatever the ttl
    kept, closed = close_stale_threads("- r5: see memory://hop-run:cost-30d-r5", set(), now=now, ttl_days=14, run_dates={"cost-30d-r5": None})
    assert kept == "" and len(closed) == 1


def test_rebuild_now_keeps_the_persons_lines_and_replaces_the_block():
    first = rebuild_now("- Real-account rounds started 2026-09-04.", ["- infra/state/cost-daily/2026-09-22 = sent 07:40Z"])
    assert first.startswith("- Real-account rounds") and STATE_BEGIN in first and "cost-daily" in first
    second = rebuild_now(first, ["- infra/state/cost-daily/2026-09-23 = sent 07:41Z"])
    assert second.count(STATE_BEGIN) == 1 and "2026-09-23" in second and "2026-09-22" not in second
    assert rebuild_now(first, []) == "- Real-account rounds started 2026-09-04."


async def test_curation_end_to_end(client):
    # a registered HOP with an injected state topic, a state claim, and a note with stale and old threads
    r = await client.put("/api/v1/memory/hops/infra", json={"version": "0.3.1", "owner": "svp", "mode": "review", "workspace": "infra",
                                                            "topics": [{"id": "infra/state/cost-daily", "kind": "state", "key": "date", "halfLife": "1d", "read": "company", "inject": True, "phase": "deploy"}],
                                                            "reads": [], "writes": ["infra/*"]}, headers=H)
    assert r.status_code in (200, 201), r.text
    for key, value, at in (("2026-09-21", "sent 07:39Z; MTD $80", "2026-09-21T07:39:00Z"), ("2026-09-22", "sent 07:40Z; MTD $106", "2026-09-22T07:40:00Z")):
        claim = {"scope": "org", "workspace": "infra", "source": "harness_memory", "action_type": "memory.claim", "actor": {"user_id": "svp"}, "occurred_at": at,
                 "payload": {"kind": "state", "topic": "infra/state/cost-daily", "key": key, "value": value, "evidence_ids": ["c1"]}}
        r = await client.post("/api/v1/episodes", params={"process": "true"}, json={"episodes": [claim]}, headers=H)
        assert r.status_code == 202, r.text
    body = "\n".join(["## Now", "- Real-account rounds started 2026-09-04.", "## Decisions", "- keep Bedrock in us-east-2", "## Open threads",
                      "- r1 cost-30d: see memory://hop-run:cost-30d-r1 (result status ok) 2026-09-04",
                      "- dead: [memory://node/00000000-0000-0000-0000-000000000000]",
                      "- still open: check the NAT gateway bytes next week", "## Preferences", "- cost as a table", "## Pointers", ""])
    r = await client.put("/api/v1/notes/org/orgN", params={"workspace": "infra"}, json={"body": body, "reason": "seed"}, headers=H)
    assert r.status_code in (200, 201), r.text
    async with get_session_factory()() as s:
        results = await curate_workspace_notes(s, org_id="orgN", now=datetime(2026, 9, 22, tzinfo=UTC) + timedelta(days=1), thread_ttl_days=14)
        await s.commit()
    res = next(x for x in results if x.workspace == "infra")
    assert res.changed and len(res.closed_threads) == 2 and res.state_lines == 1, res
    note = (await client.get("/api/v1/notes/org/orgN", params={"workspace": "infra"}, headers=H)).json()
    md = note["body"]
    assert "still open" in md and "r1 cost-30d" not in md and "dead:" not in md
    assert "keep Bedrock" in md and "cost as a table" in md, "Decisions and Preferences are untouched"
    assert STATE_BEGIN in md and "infra/state/cost-daily/2026-09-22 = sent 07:40Z" in md
    assert "2026-09-21 = sent 07:39Z" not in md, "one line per topic: the newest value, not the history"
    async with get_session_factory()() as s:
        again = await curate_workspace_notes(s, org_id="orgN", now=datetime(2026, 9, 23, tzinfo=UTC), thread_ttl_days=14)
    assert not any(x.changed for x in again), "idempotent"
