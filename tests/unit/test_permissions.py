from __future__ import annotations

from mlpal_memory_graph.core.permissions import (
    has_permission,
    team_ids_from_permissions,
)


def test_wildcards_and_exact():
    # scope names use the platform registry's dot grammar (memory.read); the
    # ns:* prefix wildcard remains for colon-namespaced grants like team:<id>:*
    assert has_permission(["*"], "memory.read")
    assert has_permission(["memory.read"], "memory.read")
    assert not has_permission(["memory.read"], "memory.write")


def test_admin_is_not_an_implicit_read_wildcard():
    # admin is an explicit named permission, not a wildcard (platform semantics)
    assert not has_permission(["memory.admin"], "memory.read")


def test_team_ids_extracted_ignoring_bare_wildcard():
    perms = ["team:eng", "team:sales:*", "team:*", "memory.read"]
    assert team_ids_from_permissions(perms) == ["eng", "sales"]


def test_team_ids_empty_without_grants():
    assert team_ids_from_permissions(["*", "memory.read"]) == []
