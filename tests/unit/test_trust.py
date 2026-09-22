"""Trust by consequence: the pure join and the tier rule."""

from mlpal_memory_graph.pipeline.trust import Consequence, join_consequences, tier_for, trust_record


def test_join_counts_passes_and_fails_per_node_and_ignores_runs_without_a_verdict():
    served = {"r1": {"n1", "n2"}, "r2": {"n1"}, "r3": {"n1"}, "r9": {"n2"}}
    verdicts = {"r1": "success", "r2": "success", "r3": "error"}      # r9: no verdict yet
    out = join_consequences(served, verdicts)
    assert (out["n1"].passes, out["n1"].fails, len(out["n1"].runs)) == (2, 1, 3)
    assert (out["n2"].passes, out["n2"].fails, len(out["n2"].runs)) == (1, 0, 1)


def test_tiers():
    assert tier_for(grounded=True, observed_count=1, consequence=None, endorsed=False) == "probation"
    assert tier_for(grounded=True, observed_count=2, consequence=None, endorsed=False) == "corroborated"
    assert tier_for(grounded=True, observed_count=1, consequence=Consequence(passes=3), endorsed=False) == "corroborated"
    assert tier_for(grounded=True, observed_count=5, consequence=Consequence(passes=9, fails=1), endorsed=False) == "probation"   # a fail holds it back
    assert tier_for(grounded=False, observed_count=1, consequence=Consequence(passes=1), endorsed=True) == "endorsed"


def test_grounding_is_the_floor():
    # an explicitly ungrounded claim (legacy mirror, no evidence) cannot be corroborated into team use
    assert tier_for(grounded=False, observed_count=4, consequence=Consequence(passes=5), endorsed=False) == "probation"
    # a node that predates the stamp (extractor fact with a span) is not held back
    assert tier_for(grounded=None, observed_count=2, consequence=None, endorsed=False) == "corroborated"


def test_record_shape():
    r = trust_record(grounded=True, observed_count=2, consequence=Consequence(passes=1, runs={"r1"}), endorsed=False)
    assert r["tier"] == "corroborated" and r["passes"] == 1 and r["runs"] == 1 and r["computed_at"].endswith("+00:00")


def test_survival_is_recorded_against_the_half_life():
    from datetime import UTC, datetime, timedelta

    now = datetime(2026, 9, 17, tzinfo=UTC)
    r = trust_record(grounded=True, observed_count=1, consequence=None, endorsed=False, now=now,
                     last_seen=now - timedelta(days=400), half_life_days=365.0)
    assert r["survival"]["decayed"] is True and r["survival"]["half_lives"] == 1.1
    assert "survival" not in trust_record(grounded=True, observed_count=1, consequence=None, endorsed=False, now=now)
