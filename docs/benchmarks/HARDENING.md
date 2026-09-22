# Memory v9 — hardening: the one-page account (2026-09-22)

**Directive (Sai, 2026-09-21):** the HOP can wait; harden and fix the memory system and make it
stronger than every competitor first, cost-conscious, experiments on EC2 only.

**What was measured, what was changed, what was learned** — four rounds on one c7i.4xlarge box,
model spend ≈ $106 of the $120 cap (round 1 $12, round 2 $5, round 4 $89: ours $2, mem0 $36, Memobase $37 with reader/judge), EC2 ≈ $12 (one box, ≈ 16 hours).

## Rounds

| round | what | result | call |
|---|---|---|---|
| 1 | **C2** turn-boundary chunking | oracle −3, S +1 (L39) | not adopted |
| 1 | **C4** running state per topic from conversations, two cuts | oracle 0.896 → 0.865 vs 0.927 (L41, L42) | not adopted: a Haiku-class tally is incomplete on a third of topics and the reader trusts it over the evidence |
| 1 | read-path defect found by C4 | superseded keyed values were served in the current view (L40) | **fixed** (`1ba9712`); production state topics had the same exposure |
| 2 | answer packet, top-12, top-8 pages as the reader's context | accuracy falls with the tokens removed; the packet is not smaller (L45) | no change: 20 passages, 8 per session |
| 3 | model-client resilience, search degradation, bounded ingest, worker deferral | six failure paths under test, every one observable (L44) | **adopted** (`7bcb67c`) |
| 3 | worker interval clocks | nightly trust join and retention purge did not run until the host's uptime exceeded the interval (L43) | **fixed** (`69f57b3`) |
| 3 | load run: 200 long histories, concurrent queries | 9,548 documents, 0 errors, search p99 574 ms during bulk ingest, service 1.4 GiB (L46) | nothing to fix; `evals/load/load_run.py` is the repeatable check |
| 4 | evidence: ours on S-100; mem0 and Memobase on the same questions under $35 caps | ours 0.865 on 96; tied with both on the ≈ 50 questions they reached, ahead on the types they never reached (L47, L48) | the publishable hard-split claim: tied where they can afford to play, at $0 vs ≈ $700 per thousand histories |

## What the service does differently now

- A gateway blip is retried with backoff; an outage opens a breaker and fails fast; both are
  counted (`/metrics`) and logged.
- Search answers from its lexical legs when the embedder is down and says `degraded: ["vector"]`.
- A synchronous ingest past eight in flight per process is refused at once with 503 + Retry-After,
  the document kept for the worker; an ingest that meets a model outage is stored and answered
  `queued`. The worker defers a batch during an outage instead of spending episodes' retry budgets.
- The current view never serves a superseded keyed value; as-of reads still reconstruct history.
- The nightly consequence join and the retention purge run on the first eligible tick after boot.
- C6 (question-conditioned speaker boost) is the default since v8; the laptop production
  container runs all of the above.

## What did not work, and the lesson

Write-time tallies from free conversation (C4) fail for the same reason the v8 fact list did: a
derived line is trusted when wrong and ignored when right, and a small model cannot make it
complete. The competitors that win multi-session counting do so with a fixed profile schema that a
stronger model fills; in our design the equivalent is a structured writer under the topic
contract, not extraction from prose. That is a HOP-side item, deliberately left for after this
program.

## Numbers that stand (LongMemEval, Sonnet 5 reader and judge, same for every system)

| | oracle-100 | S-18 | S-100 |
|---|---|---|---|
| ours (C6, 20 passages) | 0.927 (reproduced on a fresh box) | 0.778–0.833 (±1) | **0.865** (83/96; L47) |
| Memobase | 0.875 | 12/15 | 0.865 on 52 reached ($35 cap); ours 43/52 on the same |
| mem0 | 0.854 | 0.722 | 0.804 on 51 reached ($35 cap); ours 42/51 on the same |

Learnings L38–L48 in `results/LEARNINGS.md`; decisions in `results/DECISIONS.md`; worklog in
`WORKLOG.md`; runs under `evals/benchmarks/results/expbox-v9/` and `evals/load/results/`.
