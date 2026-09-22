#!/usr/bin/env bash
# Fetch the public benchmark datasets into evals/benchmarks/datasets/ (git-ignored). Idempotent.
set -euo pipefail
D="$(cd "$(dirname "$0")" && pwd)/datasets"
mkdir -p "$D/locomo" "$D/longmemeval" "$D/convomem"
[ -s "$D/locomo/locomo10.json" ] || curl -sSL -o "$D/locomo/locomo10.json" https://raw.githubusercontent.com/snap-research/locomo/main/data/locomo10.json
LME=https://huggingface.co/datasets/xiaowu0162/longmemeval-cleaned/resolve/main
[ -s "$D/longmemeval/longmemeval_oracle.json" ] || curl -sSL -o "$D/longmemeval/longmemeval_oracle.json" "$LME/longmemeval_oracle.json"
[ -s "$D/longmemeval/longmemeval_s.json" ] || curl -sSL -o "$D/longmemeval/longmemeval_s.json" "$LME/longmemeval_s_cleaned.json"
CM=https://huggingface.co/datasets/Salesforce/ConvoMem/resolve/main/core_benchmark/pre_mixed_testcases
fetch_cm() { local src=$1 dst=$2; shift 2; mkdir -p "$D/convomem/$dst"; for f in "$@"; do [ -s "$D/convomem/$dst/batched_$f.json" ] || curl -sSL -o "$D/convomem/$dst/batched_$f.json" "$CM/$src/batched_$f.json"; done; }
# batch index → context size (measured 2026-09-18): user_evidence_1 000=1 025=2 030=3 035=4 040=5 045=6/10 049=50..300
fetch_cm user_evidence/1_evidence user_evidence_1 000 025 035 045 049
fetch_cm changing_evidence/2_evidence changing_evidence_2 000 035 045
fetch_cm abstention_evidence/1_evidence abstention_evidence_1 000 035 045
fetch_cm preference_evidence/1_evidence preference_evidence_1 000 035 045
fetch_cm assistant_facts_evidence/1_evidence assistant_facts_evidence_1 000 035 045
fetch_cm implicit_connection_evidence/1_evidence implicit_connection_evidence_1 000 035 045
du -sh "$D"/*
