#!/usr/bin/env bash
# The full Part B run, in order, one bench at a time (memory v7 WP12). Meant for the expbox:
#   MLPAL_LLM_API_KEY=… nohup evals/benchmarks/suite.sh > evals/benchmarks/results/suite.log 2>&1 &
# Every run writes its own directory under evals/benchmarks/results/; this script only sequences.
set -uo pipefail
cd "$(dirname "$0")/../.."
PY="${BENCH_PY:-python3}"
C="${BENCH_CONCURRENCY:-8}"
run() { echo "=== $(date -u +%FT%TZ) $*"; "$PY" evals/benchmarks/bench.py run "$@" --concurrency "$C" 2>&1 | tail -40; }

run --bench longmemeval --split oracle --limit 500          # full oracle, harness v2 (the 60-subset baseline was 0.75 with v1)
run --bench longmemeval --split s --limit 500               # the real haystack: 500 × ≈48 sessions
run --bench locomo                                          # all 10 conversations, 1,986 questions (F1 + judge on 1–4, abstention on 5)
run --bench convomem --category user_evidence_1            --context-sizes 1,10,100 --per-size 25
run --bench convomem --category assistant_facts_evidence_1 --context-sizes 1,4,10   --per-size 25
run --bench convomem --category changing_evidence_2        --context-sizes 2,5,10   --per-size 25
run --bench convomem --category abstention_evidence_1      --context-sizes 1,4,10   --per-size 25
run --bench convomem --category preference_evidence_1      --context-sizes 1,4,10   --per-size 25
run --bench convomem --category implicit_connection_evidence_1 --context-sizes 3,6,50 --per-size 25
echo "=== $(date -u +%FT%TZ) suite done"
