"""Fail-fast production guard: unsafe configs must refuse to start, and a missing
auth SDK must fail closed (503), never downgrade to header auth."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from mlpal_memory_graph.core.config import Settings, get_settings


def _prod_settings(**overrides) -> Settings:
    return Settings(
        environment="production",
        _env_file=None,  # ignore any local .env
        **overrides,
    )


def test_prod_defaults_are_rejected():
    errors = _prod_settings().production_config_errors(has_auth_sdk=False)
    joined = "\n".join(errors)
    assert "dev_auth" in joined
    assert "internal_service_api_key" in joined
    assert "mlpal_auth" in joined
    assert "debug" in joined
    assert len(errors) == 4


def test_prod_hardened_config_passes():
    s = _prod_settings(
        dev_auth=False,
        debug=False,
        internal_service_api_key="a-real-secret",
    )
    assert s.production_config_errors(has_auth_sdk=True) == []


def test_local_and_test_envs_are_exempt():
    for env in ("local", "test", "development"):
        s = Settings(environment=env, _env_file=None)
        assert s.production_config_errors(has_auth_sdk=False) == []


def test_create_app_raises_on_unsafe_prod(monkeypatch):
    # import first (module-level `app = create_app()` must run under test settings),
    # then flip the environment and build again.
    from mlpal_memory_graph.main import create_app

    monkeypatch.setattr(get_settings(), "environment", "production")
    try:
        with pytest.raises(RuntimeError, match="unsafe production configuration"):
            create_app()
    finally:
        monkeypatch.setattr(get_settings(), "environment", "test")


@pytest.mark.asyncio
async def test_missing_sdk_fails_closed_not_open(monkeypatch):
    """dev_auth off + no SDK => 503 on any authed route; X-Test headers must NOT work."""
    from mlpal_memory_graph.api import deps
    from mlpal_memory_graph.main import create_app

    monkeypatch.setattr(get_settings(), "dev_auth", False)
    monkeypatch.setattr(deps, "_HAS_MLPAL_AUTH", False)
    try:
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get(
                "/api/v1/memory/search",
                params={"q": "anything"},
                headers={"X-Test-Org-Id": "orgA"},
            )
        assert r.status_code == 503
        assert r.json()["detail"] == "auth backend unavailable"
    finally:
        monkeypatch.setattr(get_settings(), "dev_auth", True)
