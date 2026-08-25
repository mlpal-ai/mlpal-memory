"""Hybrid fusion math (portable, offline): RRF + node-distance boost."""

from __future__ import annotations

from mlpal_memory_graph.services.hybrid import distance_boost, rrf_fuse


def test_rrf_rewards_agreement_across_legs():
    # 'b' is mid-rank in both legs; 'a' tops one leg only. Agreement should lift 'b' near 'a'.
    vec = ["a", "b", "c"]
    lex = ["d", "b", "a"]
    s = rrf_fuse([vec, lex])
    # b appears in both (ranks 2 and 2); a appears in both (ranks 1 and 3); c and d once each
    assert s["b"] == 1 / 62 + 1 / 62
    assert s["a"] == 1 / 61 + 1 / 63
    assert s["b"] > s["c"] and s["b"] > s["d"]


def test_rrf_k_dampens_top_rank_weight():
    assert rrf_fuse([["x"]], k=60)["x"] == 1 / 61
    assert rrf_fuse([["x"]], k=10)["x"] == 1 / 11  # smaller k → more weight on top ranks


def test_rrf_missing_from_a_leg_contributes_nothing():
    s = rrf_fuse([["a"], ["b"]])
    assert s["a"] == 1 / 61 and s["b"] == 1 / 61  # each in exactly one leg


def test_distance_boost_lifts_closer_nodes_monotonically():
    scores = {"near": 1.0, "far": 1.0, "unreachable": 1.0}
    dist = {"near": 0, "far": 3}  # 'unreachable' absent
    out = distance_boost(scores, dist, beta=0.5)
    assert out["near"] > out["far"] > out["unreachable"]
    assert out["near"] == 1.0 * (1 + 0.5 / 1)  # distance 0 → max boost
    assert out["unreachable"] == 1.0  # no distance → unchanged


def test_distance_boost_is_bounded():
    out = distance_boost({"n": 2.0}, {"n": 0}, beta=0.5)
    assert out["n"] == 2.0 * 1.5  # factor never exceeds 1 + beta
