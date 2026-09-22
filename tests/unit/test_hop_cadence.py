from __future__ import annotations

from datetime import UTC, datetime, timedelta

from mlpal_memory_graph.pipeline.hop_cadence import CadencePolicy, decide_due, parse_max_age

NOW = datetime(2026, 9, 4, 18, 0, tzinfo=UTC)
POLICY = CadencePolicy(min_runs_since_last=30, max_age_days=28)


def test_parse_max_age_units():
    assert parse_max_age("4w") == 28
    assert parse_max_age("14d") == 14
    assert parse_max_age(7) == 7
    assert parse_max_age(None) == 28


def test_below_floor_and_young_is_not_due():
    v = decide_due(POLICY, runs_since_last=12, last_turn_at=NOW - timedelta(days=3), now=NOW)
    assert not v.due and v.reasons == [] and v.days_since_last == 3.0


def test_run_floor_makes_it_due():
    v = decide_due(POLICY, runs_since_last=30, last_turn_at=NOW - timedelta(days=1), now=NOW)
    assert v.due and "30 main runs" in v.reasons[0]


def test_max_age_makes_it_due_even_with_few_runs():
    v = decide_due(POLICY, runs_since_last=2, last_turn_at=NOW - timedelta(days=29), now=NOW)
    assert v.due and "maxAge" in v.reasons[0]


def test_never_tuned_hop_is_due_on_runs_only():
    """No previous turn: nothing to age from, so age never fires; the floor still can."""
    assert not decide_due(POLICY, runs_since_last=5, last_turn_at=None, now=NOW).due
    assert decide_due(POLICY, runs_since_last=31, last_turn_at=None, now=NOW).due


def test_declared_triggers_count_and_undeclared_do_not():
    v = decide_due(POLICY, runs_since_last=1, last_turn_at=NOW, now=NOW, events=("on-model-release",))
    assert v.due and v.reasons == ["trigger on-model-release"]
    v2 = decide_due(POLICY, runs_since_last=1, last_turn_at=NOW, now=NOW, events=("on-full-moon",))
    assert not v2.due
