# Benchmarks

Everything here was measured with the kit in `evals/benchmarks/`: one harness, one adapter
contract per system, the same reader and judge for every system, model usage metered per call,
every prompt a competitor sent logged. Re-run it and open an issue if a number does not hold.

| document | what it is |
|---|---|
| [HEAD-TO-HEAD.md](HEAD-TO-HEAD.md) | The one-page account of the head-to-head against mem0, Memobase, supermemory, LangMem, Graphiti and Cognee on LongMemEval (2026-09-20). |
| [HARDENING.md](HARDENING.md) | The hardening round that followed: what was tried, what was adopted, what was fixed, and the S-100 evidence (2026-09-22). |
| [S100.md](S100.md) | LongMemEval S split, 100 stratified questions: ours, mem0 and Memobase under the same caps. |
| [CONDITIONS.md](CONDITIONS.md) | How each system was configured and every deviation from its defaults. |
| [PROMPTS.md](PROMPTS.md) | Every write-time and read-time prompt each system sent, verbatim. |
| [LEARNINGS-v8.md](LEARNINGS-v8.md), [LEARNINGS-v9.md](LEARNINGS-v9.md) | The numbered learnings (L1–L48), including the ones that went against us. |

The documents were written as internal engineering records and keep their internal names
(`DESIGN.md`, `WORKLOG.md`, box lanes); those files describe the same runs from the inside and
are not part of this repository. Scored runs live under `evals/benchmarks/results/`: the summary
of every scored run in the two lanes, and the per-question rows of every run the reports cite.

## What the numbers say, and what they do not

- On LongMemEval's oracle split (100 stratified questions, same Sonnet 5 reader and official
  judge templates) this system scored 0.927 against 0.875 for the next system, with no model on
  the write path. The gap to the second and third systems is outside run-to-run noise; the gap
  between those two is not.
- On the S split (48-session haystacks, 100 questions) it scored 0.865, tied with mem0 and
  Memobase on the questions both of them reached under their write-time caps, at 0 write-time
  cost against theirs.
- These are our runs of their open-source code under one protocol. They are not their published
  numbers (different judge, different reader, different question sample), and no hosted product
  was measured. A system we did not run is a system we make no claim about.

## Reproduce

```bash
bash evals/benchmarks/fetch_datasets.sh                    # LongMemEval, LoCoMo, ConvoMem (git-ignored)
docker compose up -d --build                               # the service under test
OPENAI_API_KEY=... ANTHROPIC_API_KEY=... \
  uv run python evals/benchmarks/bench.py run --bench longmemeval --split oracle --limit 100 --stratify
uv run python evals/benchmarks/bench.py run --bench longmemeval --split oracle --limit 100 --stratify --system mem0
uv run python evals/benchmarks/systems/compare.py evals/benchmarks/results/<run-a> evals/benchmarks/results/<run-b>
```

Each competitor adapter installs its system into `evals/benchmarks/systems/venvs/<name>` on first
use. Budget: the oracle-100 pass costs about $16 per 1k questions for this system (reader and
judge only) and $26–$120 for the others (their write-time models included); the S pass is where
write-time spend dominates (see HEAD-TO-HEAD.md).
