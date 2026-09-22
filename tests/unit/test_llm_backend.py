"""memory v7 WP14: the model-tier stages pick the gateway whenever a key is configured; the offline
stubs are a deliberate mode, never a silent fallback under dev auth."""

from mlpal_memory_graph.core.config import get_settings
from mlpal_memory_graph.services.llm_client import llm_backend


def test_auto_picks_gateway_when_a_key_is_configured(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_mode", "auto"); monkeypatch.setattr(s, "llm_api_key", "mlpal_sk_test"); monkeypatch.setattr(s, "dev_auth", True)
    assert llm_backend() == "gateway"
    monkeypatch.setattr(s, "llm_api_key", "")
    assert llm_backend() == "dev"
    monkeypatch.setattr(s, "llm_mode", "dev"); monkeypatch.setattr(s, "llm_api_key", "mlpal_sk_test")
    assert llm_backend() == "dev"
