"""memory v7 WP4: salience is deterministic and model-free; recency decays by valid time, terms are
the document's own leading vocabulary."""

from datetime import UTC, datetime, timedelta

from mlpal_memory_graph.services.salience import recency, terms_of


def test_recency_decays_with_age_and_defaults_to_half_when_unknown():
    now = datetime(2026, 9, 17, tzinfo=UTC)
    assert recency(now, now) == 1.0
    assert 0.36 < recency(now - timedelta(days=365), now) < 0.37
    assert recency(None, now) == 0.5


def test_terms_prefer_the_documents_own_frequent_words_and_skip_stopwords():
    t = terms_of("Merchant settlement report: settlement batches settle daily; this report lists settlement totals.")
    assert t[0] == "settlement" and "this" not in t and "report" in t
