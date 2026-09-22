from mlpal_memory_graph.core.topics import grant_from_headers, topic_matches


def test_glob_covers_whole_topic_path_and_me_is_the_caller():
    assert topic_matches("infra/*", "infra/state/cost-daily")
    assert not topic_matches("infra/*", "stock/state/aapl")
    assert topic_matches("person/{me}/pref/infra", "person/priya/pref/infra", "priya")
    assert not topic_matches("person/{me}/pref/infra", "person/marco/pref/infra", "priya")
    assert topic_matches("person/{me}/pref/infra", "person/anyone/pref/infra", None)


def test_grant_reads_are_writes_plus_reads_and_headers_build_it():
    g = grant_from_headers({"x-memory-writes": "infra/*, person/{me}/pref/infra", "x-memory-reads": "company/ownership"}, "priya")
    assert g is not None and g.may_write("infra/learning") and not g.may_write("company/ownership")
    assert g.may_read("company/ownership") and not g.may_read("stock/state/aapl")
    assert grant_from_headers({}, "priya") is None
