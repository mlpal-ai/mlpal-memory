"""hop_evals_from_memory — deviation memories become reviewable scenario skeletons."""

from __future__ import annotations

import yaml

from mlpal_memory_graph.tools.hop_evals_from_memory import draft_candidates


def test_skeleton_carries_the_deviation_and_is_idempotent(tmp_path):
    payloads = [{
        "event_id": "e1", "hop": "infra@0.2.0", "origin": "routine:infra-watch", "kind": "unmodelled",
        "slug": "dev-unmodelled-describe-volumes", "expected": "a volume listing",
        "observed": "SnapshotUnmodelled", "cause": "coverage", "action": "serve the listing", "run": "r1",
    }]
    written = draft_candidates(payloads, tmp_path / "candidates")
    assert [w.parent.name for w in written] == ["dev-unmodelled-describe-volumes"]
    doc = yaml.safe_load(written[0].read_text())
    assert doc["id"] == "cand-dev-unmodelled-describe-volumes" and doc["task_class"] == "diagnose"
    assert doc["deviation"]["observed"] == "SnapshotUnmodelled"
    assert doc["deviation"]["memory"] == "memory://episode/e1"
    assert doc["safety"]["forbidden_call_classes"] == ["mutative", "unknown"]
    assert draft_candidates(payloads, tmp_path / "candidates") == []      # second run touches nothing


def test_refusal_and_correction_map_to_their_families(tmp_path):
    written = draft_candidates([
        {"kind": "refusal", "slug": "dev-refusal-scale"},
        {"kind": "correction", "slug": "dev-correction-owner"},
    ], tmp_path)
    classes = {w.parent.name: yaml.safe_load(w.read_text())["task_class"] for w in written}
    assert classes == {"dev-refusal-scale": "refuse", "dev-correction-owner": "ask"}
