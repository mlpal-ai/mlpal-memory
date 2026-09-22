"""Embeddings provider.

Production calls assistants-service ``/v1/embeddings`` (the platform's existing path).
Local/dev/test uses a deterministic hash embedder so the pipeline runs offline and tests
are reproducible — selected automatically when MLPAL_DEV_AUTH is true or environment=local.
"""

from __future__ import annotations

import hashlib
from functools import lru_cache

import httpx

from ..core.config import get_settings


class Embedder:
    dim: int
    name: str  # embedding-space id, stamped on every row so re-embeds never mix spaces (D2)

    async def embed(self, texts: list[str]) -> list[list[float]]:  # pragma: no cover
        raise NotImplementedError

    async def embed_one(self, text: str) -> list[float]:
        return (await self.embed([text]))[0]


class DevEmbedder(Embedder):
    """Deterministic offline embedder (hashed bag-of-tokens, L2-normalized)."""

    def __init__(self, dim: int) -> None:
        self.dim = dim
        self.name = "dev-hash"
        # self-reported signal quality: retrieval damps this leg in fusion — a hashed
        # bag-of-tokens has token-overlap signal but no semantics.
        self.quality = "dev"

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * self.dim
        for tok in (text or "").lower().split():
            h = int(hashlib.sha1(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0
        norm = sum(x * x for x in v) ** 0.5
        return [x / norm for x in v] if norm else v

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]


class AssistantsEmbedder(Embedder):
    def __init__(self, base_url: str, model: str, dim: int, api_key: str | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.name = model
        self.dim = dim
        self.quality = "semantic"
        self.api_key = api_key
        self._transport: httpx.AsyncBaseTransport | None = None  # tests inject a MockTransport

    async def embed(self, texts: list[str]) -> list[list[float]]:
        headers = {"Content-Type": "application/json"}
        if self.api_key and self.api_key.startswith("mlpal_"):
            # public gateway key (models.mlpal.ai) — Bearer auth
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.api_key:
            # in-cluster service-to-service path
            headers["X-Internal-Service-Key"] = self.api_key
        from ..core.config import get_settings
        from .resilience import breaker_for, with_retries

        s = get_settings()

        async def _call() -> list[list[float]]:
            async with httpx.AsyncClient(timeout=s.embeddings_timeout_s, transport=self._transport) as client:
                resp = await client.post(
                    f"{self.base_url}/v1/embeddings",
                    json={"model": self.model, "input": texts},
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
            return [item["embedding"] for item in data["data"]]

        # memory v9 round 3: bounded retries with backoff, a breaker that fails fast during an outage;
        # the caller gets a typed ModelUnavailable and decides how to degrade
        return await with_retries("embeddings", _call, attempts=s.model_retry_attempts,
                                  breaker=breaker_for("embeddings", s.model_breaker_failures, s.model_breaker_open_s))


class LocalEmbedder(Embedder):
    """Real semantic embeddings in-process (fastembed/ONNX) — the OSS default.

    Vectors are zero-padded to the column dimension; zero padding preserves dot
    products and norms, so cosine ranking is unaffected.
    """

    def __init__(self, model: str, dim: int) -> None:
        self.model = model
        self.name = model.rsplit("/", 1)[-1]
        self.dim = dim
        self.quality = "semantic"
        self._engine = None

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        if self._engine is None:
            import os

            from fastembed import TextEmbedding  # optional dep: mlpal-memory[local-embeddings]

            # intra-op ONNX threads (NOT fastembed's `parallel` multiprocessing, which
            # deadlocks under a thread executor on macOS). memory v7 WP14: measured in the
            # container — 4 threads and batches of 16 beat all-cores and large batches, and
            # concurrent callers must not each spin up a full thread pool.
            s = get_settings()
            self._engine = TextEmbedding(self.model, threads=max(1, int(s.embeddings_threads or os.cpu_count() or 1)))
            self._batch = max(1, int(s.embeddings_batch_size or 16))
        out: list[list[float]] = []
        for vec in self._engine.embed(texts, batch_size=self._batch):
            v = list(map(float, vec))
            if len(v) < self.dim:
                v = v + [0.0] * (self.dim - len(v))
            out.append(v[: self.dim])
        return out

    async def embed(self, texts: list[str]) -> list[list[float]]:
        import asyncio

        # one BATCH at a time: the ONNX session is CPU-bound and two ingests at once oversubscribe
        # the cores (measured 20 → 5 chunks/s). A single-text embed (a query) does not queue behind
        # the ingest batches: a read must not wait for a write (measured 8 s search p50 while a
        # 50-session haystack was being ingested).
        if len(texts) <= 1:
            return await asyncio.to_thread(self._embed_sync, texts)
        if not hasattr(self, "_gate"):
            self._gate = asyncio.Semaphore(max(1, int(get_settings().embeddings_concurrency or 1)))
        async with self._gate:
            return await asyncio.to_thread(self._embed_sync, texts)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    provider = s.embeddings_provider
    if provider == "auto":
        provider = "dev" if (s.dev_auth or s.environment in ("local", "test")) else "gateway"
    if provider == "dev":
        return DevEmbedder(s.embeddings_dim)
    if provider == "local":
        return LocalEmbedder(s.embeddings_local_model, s.embeddings_dim)
    if provider == "gateway":
        return AssistantsEmbedder(
            s.embeddings_service_url,
            s.embeddings_model,
            s.embeddings_dim,
            s.embeddings_api_key or s.internal_service_api_key,
        )
    raise ValueError(f"unknown embeddings_provider {s.embeddings_provider!r}")
