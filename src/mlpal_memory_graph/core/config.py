"""Application configuration via pydantic-settings (MLPAL_ env prefix).

Mirrors the platform convention (see mlpal-storage-service/core/config.py): a single
``Settings`` object, ``@lru_cache`` accessor, and computed async/sync DB URLs.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="MLPAL_", env_file=".env", extra="ignore")

    # --- service ---
    environment: str = "local"
    debug: bool = True
    log_level: str = "INFO"
    host: str = "0.0.0.0"
    port: int = 8000

    # --- database (shared Postgres; own `memory` schema) ---
    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "mlpal"
    db_user: str = "postgres"
    db_password: str = "localdev"  # non-secret default; real value comes from MLPAL_DB_PASSWORD
    db_schema: str = "memory"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    # Full URL override (tests use sqlite+aiosqlite). Empty -> build from parts.
    database_url_override: str | None = None

    # --- auth ---
    auth_service_url: str = ""
    internal_service_api_key: str = "dev-internal-key"
    dev_auth: bool = True

    # --- embeddings ---
    # provider: auto (dev embedder in local/test, gateway otherwise) | dev | local | gateway.
    # "local" runs a real semantic model in-process via fastembed (the OSS default path —
    # no gateway key required); vectors are zero-padded to embeddings_dim, which preserves
    # cosine ranking and keeps one column dimension across spaces (D2 stamps the space name).
    embeddings_provider: str = "auto"
    embeddings_service_url: str = ""
    embeddings_model: str = "text-embedding-3-small"
    embeddings_local_model: str = "BAAI/bge-small-en-v1.5"
    # gateway auth for provider=gateway: an mlpal_ key -> Bearer (public gateway);
    # empty -> falls back to internal_service_api_key (in-cluster header)
    embeddings_api_key: str = ""
    embeddings_dim: int = 1536

    # --- graph storage driver: postgres | age | falkordb | neo4j | graphiti ---
    graph_driver: str = "postgres"

    # --- incremental updater (background worker) ---
    updater_enabled: bool = True
    updater_poll_interval: float = 5.0
    updater_batch_size: int = 100
    updater_advisory_lock_id: int = 920100
    # bounded retries: a fold that fails this many times is dead-lettered (kept for
    # audit/replay, excluded from the worker cursor) instead of retrying forever.
    updater_max_retries: int = 5

    # --- extraction: rule | llm ---
    # "rule" = deterministic only (cheap path). "llm" = also run the LLM extractor + contradiction
    # judge on content-bearing high-salience episodes (the cost-tiered 'full' tier).
    extractor: str = "rule"
    llm_model: str = "claude-haiku-4-5"  # cheap Claude (Haiku-class) for extraction via gateway
    llm_max_tokens: int = 1500
    # provenance stamped on every LLM-extracted fact so a write is auditable/replayable. Bump
    # prompt_version when the extraction prompt changes, extraction_version for code/schema changes.
    extraction_version: str = "llm/0.1.0"
    prompt_version: str = "extract/0.1.0"

    # --- ingestion sources + retrieval routing (YAML config paths; empty -> defaults/off) ---
    sources_config_path: str | None = None
    routes_config_path: str | None = None

    # --- privacy ---
    content_capture_default: bool = False

    # --- retention (DIRECT tier only — episodes/chunks; never derived facts) ---
    direct_retention_days: int = 0  # 0 = keep forever (off)
    retention_interval_seconds: int = 3600  # how often the worker runs the purge

    # --- v3 lifecycle: working-tier TTL (session-scoped memories; committed = durable) ---
    working_ttl_days: int = 14

    # --- v3 distillation: AI-assisted memory formation, cost-bounded by design ---
    # One small-model gateway call per SESSION document (never per turn), only for these
    # sources. Active when extractor=llm; offline uses the deterministic dev distiller.
    distill_sources: str = "claude_code,yodex,session"

    # --- RLS backstop (defence-in-depth; app-layer scope_clause stays primary) ---
    # When true the read path sets app.current_org so the (permissive-when-unset) RLS policies in
    # migration 0009 restrict rows at the DB layer too. Off by default — see docs.
    rls_enabled: bool = False

    # --- ontology ---
    ontology_version: str = "core/0.1.0"

    aws_region: str = "us-east-2"

    @property
    def is_local_env(self) -> bool:
        return self.environment in ("local", "test", "development", "dev-local")

    def production_config_errors(self, *, has_auth_sdk: bool) -> list[str]:
        """Fatal misconfigurations for a non-local environment.

        The service refuses to start rather than serve an internet-facing API with
        header-trust auth or well-known keys (fail-fast beats fail-open).
        """
        if self.is_local_env:
            return []
        errors: list[str] = []
        if self.dev_auth:
            errors.append("dev_auth=true is forbidden outside local/test (MLPAL_DEV_AUTH=false)")
        if self.internal_service_api_key == "dev-internal-key":
            errors.append("internal_service_api_key is the well-known default; set a real secret")
        if not has_auth_sdk:
            errors.append("mlpal_auth SDK is not importable; production auth would fail open")
        if self.debug:
            errors.append("debug=true is forbidden outside local/test (SQL echo, reload)")
        return errors

    @property
    def database_url(self) -> str:
        if self.database_url_override:
            return self.database_url_override
        return (
            f"postgresql+asyncpg://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url_sync(self) -> str:
        if self.database_url_override:
            return self.database_url_override.replace("+asyncpg", "").replace("+aiosqlite", "")
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def is_postgres(self) -> bool:
        return self.database_url.startswith("postgresql")


@lru_cache
def get_settings() -> Settings:
    return Settings()
