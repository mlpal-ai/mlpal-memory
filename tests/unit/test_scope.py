from __future__ import annotations

import pytest

from mlpal_memory_graph.core.scope import Scope, ScopeRef
from mlpal_memory_graph.services.resolution import (
    RetrievalContext,
    accessible_scopes,
    narrow_to,
)


def test_scope_ordering_broad_to_narrow():
    assert Scope.GLOBAL.rank < Scope.ORG.rank < Scope.TEAM.rank < Scope.USER.rank


def test_scoperef_requires_id_for_non_global():
    with pytest.raises(ValueError):
        ScopeRef(Scope.USER, None)
    with pytest.raises(ValueError):
        ScopeRef(Scope.ORG, "")


def test_global_must_have_no_id():
    assert ScopeRef.global_().scope_id is None
    with pytest.raises(ValueError):
        ScopeRef(Scope.GLOBAL, "x")


def test_accessible_scopes_narrowest_first():
    ctx = RetrievalContext(tenant_id="orgA", user_id="alice", team_ids=("eng",))
    scopes = accessible_scopes(ctx)
    assert [s.scope for s in scopes] == [Scope.USER, Scope.TEAM, Scope.ORG, Scope.GLOBAL]
    assert ScopeRef(Scope.USER, "alice") == scopes[0]


def test_accessible_scopes_anonymous_tenant_only():
    scopes = accessible_scopes(RetrievalContext(tenant_id="orgA", user_id=None))
    assert ScopeRef(Scope.USER, "alice") not in scopes
    assert ScopeRef(Scope.ORG, "orgA") in scopes


def test_narrow_to_single_layer():
    ctx = RetrievalContext(tenant_id="orgA", user_id="alice")
    only_user = narrow_to(accessible_scopes(ctx), Scope.USER)
    assert only_user == [ScopeRef(Scope.USER, "alice")]
