from datetime import UTC, datetime, timedelta

from mlpal_memory_graph.core.halflife import decay, half_life_days_for, parse_half_life


def test_parse_forms():
    assert parse_half_life("90d") == 90.0 and parse_half_life("1y") == 365.0 and parse_half_life("2w") == 14.0
    assert parse_half_life("none") is None and parse_half_life(None) is None
    assert parse_half_life("2x") == "2x"  # a cadence multiple, resolved by the report


def test_kind_defaults_and_override():
    assert half_life_days_for({"kind": "state"}) is None
    assert half_life_days_for({"kind": "learning"}) == 365.0
    assert half_life_days_for({"kind": "deviation"}) == 90.0
    assert half_life_days_for({"kind": "learning", "half_life": "30d"}) == 30.0
    assert half_life_days_for({}) == 180.0


def test_decay_orders_a_fresh_learning_above_an_old_one_and_never_touches_state():
    now = datetime.now(UTC)
    old, fresh = now - timedelta(days=400), now - timedelta(days=1)
    assert decay({"kind": "learning"}, fresh, now) > decay({"kind": "learning"}, old, now) >= 0.35
    assert decay({"kind": "state"}, old, now) == 1.0
    assert decay({"kind": "learning", "half_life": "30d"}, now - timedelta(days=60), now) < decay({"kind": "learning"}, now - timedelta(days=60), now)
