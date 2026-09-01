"""LLM chat client over the assistants-service gateway (``/v1/chat/completions``).

Mirrors ``embeddings_client``: a service-to-service call (``X-Internal-Service-Key``) to the shared
gateway, using structured (``json_schema``) output for ontology-constrained extraction. Only the
**write-side** LLM tier (extraction + contradiction judge) calls this — the read path stays
LLM-free. A cheap Claude (Haiku-class) model is used; ``temperature=0`` for replayability.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from functools import lru_cache

import httpx

from ..core.config import get_settings
from ..core.logging import get_logger

log = get_logger(__name__)


class LLMClient(ABC):
    name: str = "abstract"

    @abstractmethod
    async def complete_json(
        self, *, system: str, user: str, schema: dict, max_tokens: int | None = None
    ) -> dict:
        """Return the model's structured JSON output for ``schema`` ({} on parse failure)."""

    @abstractmethod
    async def complete_text(
        self, *, system: str, user: str, model: str | None = None, max_tokens: int | None = None
    ) -> tuple[str, dict]:
        """Return (text, usage) for a plain completion. ``model`` overrides the default
        (the x5 answer-synthesis experiment compares model tiers on the same packet)."""


class GatewayLLMClient(LLMClient):
    def __init__(
        self, base_url: str, model: str, api_key: str | None = None, max_tokens: int = 1500
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = model
        self.api_key = api_key
        self.max_tokens = max_tokens

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key.startswith("mlpal_"):
            headers["Authorization"] = f"Bearer {self.api_key}"  # public gateway key
        elif self.api_key:
            headers["X-Internal-Service-Key"] = self.api_key  # in-cluster path
        return headers

    async def complete_text(self, *, system, user, model=None, max_tokens=None) -> tuple[str, dict]:
        body = {
            "model": model or self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": 0,
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}/v1/chat/completions", json=body, headers=self._headers()
            )
            r.raise_for_status()
            data = r.json()
        return str(data.get("content") or ""), dict(data.get("usage") or {})

    async def complete_json(self, *, system, user, schema, max_tokens=None) -> dict:
        headers = self._headers()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": max_tokens or self.max_tokens,
            "temperature": 0,  # deterministic / replayable
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": schema, "strict": True},
            },
        }
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{self.base_url}/v1/chat/completions", json=body, headers=headers
            )
            r.raise_for_status()
            data = r.json()
        # the gateway returns parsed structured output in `data`; otherwise parse `content`.
        if isinstance(data.get("data"), dict):
            return data["data"]
        try:
            return json.loads(data.get("content") or "{}")
        except json.JSONDecodeError:
            log.warning("llm.bad_json", model=self.model, event="extraction")
            return {}


@lru_cache
def get_llm_client() -> LLMClient:
    s = get_settings()
    return GatewayLLMClient(
        s.embeddings_service_url,
        s.llm_model,
        s.llm_api_key or s.internal_service_api_key,
        s.llm_max_tokens,
    )
