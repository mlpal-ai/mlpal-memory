"""Unit: memory v10 — one naming rule and one contract gate for every tuning stage."""

from __future__ import annotations

from mlpal_memory_graph.pipeline.hop_names import contract_at_least, hop_base


def test_variants_count_toward_their_parent():
    assert hop_base("infra-ro") == "infra" and hop_base("infra") == "infra" and hop_base("coding-exp-max") == "coding"
    assert hop_base(None) == "" and hop_base("") == ""


def test_contract_gate_accepts_everything_from_d11_2_on():
    assert not contract_at_least("d11.1") and not contract_at_least(None) and not contract_at_least("garbage")
    for c in ("d11.2", "d11.5", "d11.6", "d11.7", "d11.12", "d12.0"):
        assert contract_at_least(c), c
