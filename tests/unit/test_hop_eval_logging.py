
from mlpal_memory_graph.pipeline.hop_eval import logging_runner


def test_logging_runner_keeps_stdout_and_stderr(tmp_path):
    run = logging_runner(tmp_path / "turn")
    rc, out = run("echo score 0.5; echo boom >&2; exit 3", tmp_path)
    assert rc == 3 and "score 0.5" in out
    log = (tmp_path / "turn" / "scorer-1.log").read_text()
    assert "exit 3" in log and "boom" in log and "score 0.5" in log


def test_scorers_see_the_candidate_dir_as_hop_dir(tmp_path):
    """Regression: tune turns 1 and 1b graded pass_rate 0.00 because $HOP_DIR was empty and the
    scorer's `yodex:$HOP_DIR` adapter ran without a HOP at all."""
    import yaml
    from mlpal_memory_graph.pipeline import hop_eval as he
    hop = tmp_path / "cand"; hop.mkdir()
    (hop / "hop.yaml").write_text(yaml.safe_dump({
        "spec": "mlpal/hop-v1", "name": "t", "version": "0.0.1",
        "evals": [{"name": "g", "role": "golden", "tasks": ".", "runs": 1, "passBar": 1.0,
                   "scorer": "test -n \"$HOP_DIR\" && test -d \"$HOP_DIR\" && echo 1.0"}],
        "tuning": {"goldenSuite": "g", "promote": "human"},
    }))
    v = he.run_ladder(hop / "hop.yaml", runner=he.shell_runner)
    assert v.suites[0].passed, v.as_dict()


def test_missing_tasks_dir_skips_informational_suite_and_rejects_gating(tmp_path):
    import yaml
    from mlpal_memory_graph.pipeline import hop_eval as he
    hop = tmp_path / "c"; hop.mkdir(); (hop / "golden").mkdir()
    (hop / "hop.yaml").write_text(yaml.safe_dump({
        "spec": "mlpal/hop-v1", "name": "t", "version": "0.0.1",
        "evals": [{"name": "g", "role": "golden", "tasks": "golden", "runs": 1, "passBar": 1.0, "scorer": "echo 1.0"},
                  {"name": "f", "role": "frontier", "tasks": "generated", "runs": 1, "passBar": 0, "gates": False, "scorer": "echo 0.5"}],
        "tuning": {"goldenSuite": "g", "frontierMetric": "f", "promote": "human"},
    }))
    v = he.run_ladder(hop / "hop.yaml", runner=he.shell_runner).as_dict()
    names = {s["name"]: s for s in v["suites"]}
    assert names["g"]["passed"] and names["f"]["skipped"] and v["disposition"] != "rejected"
