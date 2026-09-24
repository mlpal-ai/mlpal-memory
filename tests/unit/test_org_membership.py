"""memory v12 §3: a session token names a person; X-Org-Id names the tenant; the platform's
membership list decides. Positive answers are cached per token; refusals are plain 403s."""

from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from mlpal_memory_graph.api import deps


def _platform(request: httpx.Request) -> httpx.Response:
    if request.headers.get("authorization") != "Bearer tok-115":
        return httpx.Response(401, json={"detail": "bad token"})
    return httpx.Response(200, json={"organizations": [{"id": 6, "name": "ML Pal"}], "total": 1})


@pytest.fixture(autouse=True)
def _transport(monkeypatch):
    monkeypatch.setattr(deps, "_membership_transport", httpx.MockTransport(_platform))
    deps._membership_cache.clear()


async def test_member_passes_and_is_cached_non_member_is_refused():
    await deps.require_membership("tok-115", "6", "https://platform.test/api/v1")
    assert deps._membership_cache, "a positive answer is cached"
    with pytest.raises(HTTPException) as e:
        await deps.require_membership("tok-115", "7", "https://platform.test/api/v1")
    assert e.value.status_code == 403 and "not a member" in e.value.detail
    with pytest.raises(HTTPException) as e:
        await deps.require_membership("tok-other", "6", "https://platform.test/api/v1")
    assert e.value.status_code == 403


async def test_disabled_instance_refuses_org_choice():
    with pytest.raises(HTTPException) as e:
        await deps.require_membership("tok-115", "6", "")
    assert e.value.status_code == 403 and "not enabled" in e.value.detail
