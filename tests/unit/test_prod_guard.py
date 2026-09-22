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


def test_static_key_file_satisfies_the_guard_without_the_sdk():
    s = _prod_settings(dev_auth=False, debug=False, internal_service_api_key="a-real-secret",
                       api_keys_file="/etc/mlpal/api_keys.yaml")
    assert s.production_config_errors(has_auth_sdk=False) == []


def test_create_app_refuses_an_unreadable_key_file(monkeypatch, tmp_path):
    from mlpal_memory_graph.main import create_app

    monkeypatch.setattr(get_settings(), "api_keys_file", str(tmp_path / "missing.yaml"))
    try:
        with pytest.raises(RuntimeError, match="api_keys_file unusable"):
            create_app()
    finally:
        monkeypatch.setattr(get_settings(), "api_keys_file", "")


@pytest.mark.asyncio
async def test_static_keys_pin_a_caller_to_its_tenant(monkeypatch, tmp_path):
    """dev_auth off + key file: X-Test headers are refused, a listed key reads as its org, an
    unknown key is 401, and no SDK is needed."""
    from mlpal_memory_graph.api import deps
    from mlpal_memory_graph.core.api_keys import get_api_key_file, key_digest, mint_key
    from mlpal_memory_graph.main import create_app

    token = mint_key()
    f = tmp_path / "api_keys.yaml"
    f.write_text(
        f"keys:\n  - id: laptop\n    sha256: {key_digest(token)}\n    org_id: acme\n"
        "    user_id: sai\n    permissions: [memory.read]\n"
    )
    monkeypatch.setattr(get_settings(), "dev_auth", False)
    monkeypatch.setattr(get_settings(), "api_keys_file", str(f))
    monkeypatch.setattr(deps, "_HAS_MLPAL_AUTH", False)
    get_api_key_file.cache_clear()
    try:
        transport = ASGITransport(app=create_app())
        async with AsyncClient(transport=transport, base_url="http://testserver") as c:
            r = await c.get("/api/v1/memory/search", params={"q": "x"},
                            headers={"X-Test-Org-Id": "orgA"})
            assert r.status_code == 401 and r.json()["detail"] == "missing credentials"
            r = await c.get("/api/v1/memory/search", params={"q": "x"},
                            headers={"X-API-Key": "mem_wrong"})
            assert r.status_code == 401 and r.json()["detail"] == "invalid credentials"
            r = await c.get("/api/v1/memory/search", params={"q": "x"},
                            headers={"X-API-Key": token})
            assert r.status_code == 200, r.text
            r = await c.get("/api/v1/memory/search", params={"q": "x"},
                            headers={"Authorization": f"Bearer {token}"})
            assert r.status_code == 200, r.text
            r = await c.post("/api/v1/documents", headers={"X-API-Key": token},
                             json={"title": "t", "content": "c", "scope": "org"})
            assert r.status_code == 403, "a read-only key cannot write"
    finally:
        monkeypatch.setattr(get_settings(), "dev_auth", True)
        monkeypatch.setattr(get_settings(), "api_keys_file", "")
        get_api_key_file.cache_clear()
