"""Readiness: /health says `warming` (503) until the read path is warm, `ok` after, `degraded` (200)
when warm-up failed. A sidecar that starts on "healthy" must never pay the cold embedder inside its own
tool timeout (WP9 finding)."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
@pytest.mark.parametrize("state,code", [("warming", 503), ("ok", 200), ("degraded", 200)])
async def test_health_reflects_readiness(state, code):
    from mlpal_memory_graph.main import create_app

    app = create_app()
    app.state.readiness = state
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == code and r.json()["status"] == state


@pytest.mark.asyncio
async def test_health_is_warming_before_any_lifespan():
    from mlpal_memory_graph.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://t") as c:
        r = await c.get("/health")
    assert r.status_code == 503 and r.json()["status"] == "warming"
