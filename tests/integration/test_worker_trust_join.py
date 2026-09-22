"""memory v7 WP1: the worker joins consequence on a clock. The join itself is tested in test_trust_flow;
here: it runs for every org with a verdict in the window, and not more often than the interval."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from mlpal_memory_graph.db.models import Episode
from mlpal_memory_graph.services import worker as worker_mod


@pytest.mark.asyncio
async def test_trust_join_runs_per_org_and_respects_interval(session, monkeypatch):
    for org in ("orgJ1", "orgJ2"):
        session.add(Episode(event_id=f"run-{org}", occurred_at=datetime.now(UTC), org_id=org, scope="repo", scope_id="infra",
                            lifecycle="committed", actor={}, source="harness_telemetry", action_type="run.completed",
                            subject={}, payload={"run_id": f"r-{org}", "run_result": "success"}, processed=True, tier="deterministic"))
    await session.commit()
    calls: list[str] = []

    async def fake_compute(org: str, window_days: int) -> dict:
        calls.append(org)
        return {"runs_with_verdict": 1, "tiers": {}}

    from mlpal_memory_graph.tools import hop_trust
    monkeypatch.setattr(hop_trust, "compute", fake_compute)
    w = worker_mod.MemoryUpdateWorker.__new__(worker_mod.MemoryUpdateWorker)
    w.settings = worker_mod.get_settings()
    w._last_trust_join = float("-inf")  # a fresh worker: the first tick is eligible whatever the host's uptime
    await w._maybe_trust_join(session)
    assert sorted(set(calls) & {"orgJ1", "orgJ2"}) == ["orgJ1", "orgJ2"]
    n = len(calls)
    await w._maybe_trust_join(session)   # inside the interval: no second pass
    assert len(calls) == n
