"""Unit: memory v12 §2b — the tree's pure rules. Ancestors nearest first, descendants, readable
units (own → ancestors → administered subtrees), and the tenant rule for a person without an org."""

from __future__ import annotations

from types import SimpleNamespace as NS

from mlpal_memory_graph.api.deps import tenant_for
from mlpal_memory_graph.services.units import (
    UnitPolicy,
    administered_units,
    ancestors,
    depth,
    descendants,
    readable_units,
)


def _tree():
    # company > engineering > platform > memory-squad ; company > sales
    u = {
        "eng": NS(id="eng", parent_id=None, policy={}),
        "platform": NS(id="platform", parent_id="eng", policy={"read_descendants": True}),
        "squad": NS(id="squad", parent_id="platform", policy={}),
        "sales": NS(id="sales", parent_id=None, policy={}),
    }
    return u


def test_ancestors_and_descendants():
    t = _tree()
    assert ancestors(t, "squad") == ["platform", "eng"]
    assert ancestors(t, "eng") == [] and depth(t, "squad") == 2
    assert descendants(t, "eng") == {"platform", "squad"} and descendants(t, "sales") == set()


def test_readable_is_own_then_ancestors_nearest_first_never_siblings_or_children():
    t = _tree()
    alice = [NS(unit_id="squad", role="member")]
    assert readable_units(t, alice) == ("squad", "platform", "eng")
    bob = [NS(unit_id="platform", role="member")]
    assert readable_units(t, bob) == ("platform", "eng"), "a member does not see the squads below by default"
    assert "sales" not in readable_units(t, alice)


def test_admin_with_read_descendants_sees_the_subtree_and_administers_it():
    t = _tree()
    carol = [NS(unit_id="platform", role="admin")]
    assert readable_units(t, carol) == ("platform", "eng", "squad")
    assert administered_units(t, carol) == {"platform", "squad"}
    dave = [NS(unit_id="eng", role="admin")]  # eng's policy has no read_descendants
    assert readable_units(t, dave) == ("eng",)
    assert administered_units(t, dave) == {"eng", "platform", "squad"}, "administering is by role; reading below is by policy"


def test_a_cycle_in_data_does_not_hang_the_walk():
    t = {"a": NS(id="a", parent_id="b", policy={}), "b": NS(id="b", parent_id="a", policy={})}
    assert ancestors(t, "a") == ["b"]


def test_policy_refuses_unknown_keys():
    import pytest

    assert UnitPolicy(read_descendants=True).publish_up == "members"
    with pytest.raises(ValueError):
        UnitPolicy(read_descendant=True)  # a typo must not silently mean off


def test_tenant_rule():
    assert tenant_for("acme", "u1") == "acme"
    assert tenant_for(None, "u1") == "user:u1"
    assert tenant_for("", "u1") == "user:u1"
    assert tenant_for(None, None) is None
