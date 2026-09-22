"""memory v7 WP11: the chat client speaks OpenAI's /v1/chat/completions; an OpenAI-compatible local
server (Ollama) is a provider switch, with plain bearer auth or none."""

from mlpal_memory_graph.services.llm_client import GatewayLLMClient


def test_openai_compatible_provider_sends_plain_bearer_or_nothing():
    c = GatewayLLMClient("http://localhost:11434/", "llama3.1", None, provider="openai-compatible")
    assert c.base_url == "http://localhost:11434" and "Authorization" not in c._headers()
    c = GatewayLLMClient("http://localhost:11434", "llama3.1", "ollama", provider="openai-compatible")
    assert c._headers()["Authorization"] == "Bearer ollama" and "X-Internal-Service-Key" not in c._headers()


def test_gateway_provider_keeps_its_two_auth_paths():
    assert GatewayLLMClient("https://g", "m", "mlpal_x")._headers()["Authorization"] == "Bearer mlpal_x"
    assert GatewayLLMClient("https://g", "m", "svc")._headers()["X-Internal-Service-Key"] == "svc"
