"""Concrete source plugins. Importing this package registers them in ``SOURCE_REGISTRY``."""

from . import (  # noqa: F401  (import-for-registration side effect)
    backend_agents,
    backend_audit,
    langgraph_store,
    mcp_servers,
    skills,
)

__all__ = ["backend_agents", "backend_audit", "langgraph_store", "mcp_servers", "skills"]
