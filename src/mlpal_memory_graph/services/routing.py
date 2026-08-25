"""Use-case retrieval routing.

A ``use_case`` selects which sources memory is read from (and, later, which retrievers run).
The route's ``allowed_sources`` is applied as a filter — but the caller's resolved scope set
is ALWAYS appended as a hard, non-bypassable predicate in the driver, so routing can never
widen access. Routing is rule-based config (deterministic, debuggable); an LLM tiebreak is a
future opt-in per use_case. See design-proposal §4.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

from ..core.config import get_settings
from ..core.logging import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class RouteConfig:
    retrievers: tuple[str, ...] = ()
    allowed_sources: tuple[str, ...] | None = None  # None = no source restriction


# Sensible defaults; override per deployment via MLPAL_ROUTES_CONFIG_PATH (YAML).
DEFAULT_ROUTES: dict[str, RouteConfig] = {
    "coding_assistant": RouteConfig(
        retrievers=("code_memory", "project_docs"),
        allowed_sources=("github_pr", "repo_files", "langgraph_store"),
    ),
    "onboarding": RouteConfig(
        retrievers=("org_facts", "team_facts"),
        allowed_sources=("docs", "decisions"),
    ),
    "blast_radius": RouteConfig(
        retrievers=("graph_neighbors",),
        allowed_sources=("deploy", "mcp_tool_calls"),
    ),
}


class UseCaseRouter:
    def __init__(self, routes: dict[str, RouteConfig]) -> None:
        self.routes = routes

    def route(self, use_case: str | None) -> RouteConfig | None:
        if not use_case:
            return None
        return self.routes.get(use_case)


def _load_routes() -> dict[str, RouteConfig]:
    path = getattr(get_settings(), "routes_config_path", None)
    if not path:
        return DEFAULT_ROUTES
    try:
        import yaml  # lazy: only needed when a YAML route config is configured

        with open(path) as f:
            raw = yaml.safe_load(f) or {}
        routes = {
            name: RouteConfig(
                retrievers=tuple(cfg.get("retrievers", [])),
                allowed_sources=(
                    tuple(cfg["allowed_sources"]) if cfg.get("allowed_sources") else None
                ),
            )
            for name, cfg in (raw.get("use_cases") or {}).items()
        }
        return routes or DEFAULT_ROUTES
    except Exception as exc:  # noqa: BLE001  bad config must fail loud, not silently
        log.error("routing.config_load_failed", path=path, error=str(exc))
        raise


@lru_cache
def get_router() -> UseCaseRouter:
    return UseCaseRouter(_load_routes())
