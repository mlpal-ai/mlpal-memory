"""Unit: sources-YAML loading expands ${VAR} env references (secrets never live in the YAML)."""

from __future__ import annotations

from mlpal_memory_graph.services.scheduler import _expand_env


def test_expand_env_substitutes_set_vars(monkeypatch):
    monkeypatch.setenv("MEMORY_READER_DSN", "postgresql://r:pw@host:5432/db")
    assert _expand_env("${MEMORY_READER_DSN}") == "postgresql://r:pw@host:5432/db"


def test_expand_env_unset_var_resolves_to_none(monkeypatch):
    monkeypatch.delenv("MEMORY_READER_DSN", raising=False)
    # unset → None → the source becomes a configured no-op (missing-DSN behavior), not a
    # connection attempt against the literal "${MEMORY_READER_DSN}"
    assert _expand_env("${MEMORY_READER_DSN}") is None


def test_expand_env_leaves_plain_values_alone():
    assert _expand_env("mlpal_events") == "mlpal_events"
    assert _expand_env(True) is True
    assert _expand_env(None) is None
