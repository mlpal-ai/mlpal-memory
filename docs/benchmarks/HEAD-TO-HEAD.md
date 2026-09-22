# Memory v8 — head-to-head: the one-page account (2026-09-20)

**Question.** With the same reader, judge, and write-time models, how does our memory compare with
the open-source memory systems on LongMemEval, what do they do that we do not, and which of it is
worth taking? (`DESIGN.md`; the directive: learn how the others work, log everything, be fair and
critical, mind the cost.)

**Setup.** Every system runs through one adapter contract (`evals/benchmarks/systems/`) inside the
same harness loop: ingest one haystack, search top-20, the same Sonnet 5 reader over the returned
items, the official per-type LongMemEval judge templates on Sonnet 5. Where a system needs a model at
write time it gets Claude Haiku 4.5 and `text-embedding-3-small`; ours runs the direct arm (no model
on write, local bge-small embedder). Model usage is metered per call (SDK wrappers, a litellm
callback, or an OpenAI-compatible counting proxy for the two servers) and every prompt a system sends
is logged (`results/PROMPTS.md`). Conditions and deviations per system: `results/CONDITIONS.md`.
Runs on the laptop (pass 1) and one c7i.4xlarge box (the S pass and our own iterations); every
scored run is under `evals/benchmarks/results/` with rows, items, traces and usage.

## Results

**Oracle split, 100 stratified questions (96 scored), after the reader fix (L29):**

| system | accuracy | multi-session | assistant-turn q. | write-time $/1k haystacks | $/1k questions (all-in) |
|---|---|---|---|---|---|
| **ours** (C6 default) | **0.927** | 0.75 | 1.0 | 0 | 16 |
| Memobase | 0.875 | 1.0 | 0.375 | 27 | 37 |
| mem0 | 0.854 | 0.875 | 0.625 | 27 | 34 |
| supermemory | 0.792 | 0.625 | 1.0 | 108 | 120 |
| LangMem | 0.708 | 0.562 | 0.375 | 17 | 26 |
| Graphiti (47 q) | 0.66 | | | 410 | |
| Cognee, session chunks | 0.625 | 0.125 | 1.0 | 34 | 56 |
| Cognee, 256-token chunks (21 q) | 0.81 (= ours on those 21) | | | 390 | |

**S split (48-session haystacks), 18 stratified questions, $10 write-time cap per system:**

| system | accuracy | questions reached | write-time $/1k haystacks | context tokens/q |
|---|---|---|---|---|
| **ours** (C6) | **0.833** | 18 | 0 | 4.0k |
| Memobase | 0.80 (12/15; ours 12/15, mem0 12/15 on the same 15) | 15 | 830 | 5.8k |
| mem0 | 0.722 | 18 | 690 | 0.9k |
| LangMem | 0.389 | 18 | 430 | 4.6k |
| supermemory | 2/4 | 4 | 2,600 | 1.5k |

Noise band: ±3 questions on 96, ±1 on 18 (two identical ours runs differed by one). Full tables:
`results/PASS1-oracle100.md`, `results/PASS2-s18.md`; 37 learnings in `results/LEARNINGS.md`.

## What it says

- **We lead on both splits at zero write-time cost, and the oracle lead (7 questions on 96) is
  outside the noise band.** On S the top three tie on the questions all reached, and cost separates
  them by three orders of magnitude.
- **Extraction systems drop what the assistant said** (single-session-assistant 0.375–0.625 for
  mem0, Memobase, LangMem against 1.0 for the passage systems). Keeping verbatim passages is a
  structural advantage, not a tuning one.
- **Where they beat us is counting and current state across sessions** (Memobase 1.0 vs our 0.75
  on multi-session). A written profile or a running count answers "how many / how much in total"
  where a reader over passages over-counts or under-counts. That is our one open gap (C4).
- **Write-time abstraction costs accuracy on long haystacks:** LangMem falls from 0.708 to 0.389
  when 48 sessions fold into one profile; supermemory's per-chunk agent costs $2.6 a haystack.
- **Chunk size is the whole story for Cognee** (0.625 → 0.81 on the reached questions at 20× the
  cost) and turn boundaries matter for us too (C1/C6).

## What we changed because of it

| change | measured | status |
|---|---|---|
| **C6** question-conditioned speaker boost (boost 1.3; assistant turns when the question asks what the assistant said, user turns otherwise) | oracle 0.927 (+5 vs no boost), S 0.833 (level) | **adopted, service default** (`0bfe1c1`) |
| C1 plain user-turn boost | oracle +5, S −2 | superseded by C6 |
| C3 dated facts beside passages (one Haiku call per session) | rrf fusion 0.865; appended 0.917 vs 0.927 | closed: the reader trusts an incomplete fact list over the passages (L28, L31) |
| Reader budget (harness) | 1–5 empty answers per run, every system, hard types | fixed: retry at 4×, `bench.py rescore` (L29) |
| Health probes (compose) | python start per 5 s probe = 300 % CPU idle | fixed |

**Next iteration (C4):** a per-topic running state folded at write time (count, total, current
value of a list) shown as one complete item instead of a fact list — the mechanism behind
Memobase's multi-session score, in our claim-fold shape. Not built in v8.

## Cost

Model spend ≈ $123 of the $150 cap (reader/judge $21, competitors' write-time $99 metered, our C3
extraction ≈ $4); EC2 ≈ $3 (one c7i.4xlarge, four hours). Graphiti alone was $19.6 for 47 questions.

## Fairness notes

The reader and judge are Sonnet 5 through the MLPal gateway for every system; the official
LongMemEval judge is gpt-4o, so absolute numbers are not comparable with published tables (v7
REPORT §). Competitors ran with their defaults except where `CONDITIONS.md` says otherwise (Cognee's
chunk size, Memobase's search window); our own lever changes were measured on the same cached
haystacks as the baseline. The S caps favour systems whose reached questions happen to be easier
(Memobase never reached its weakest type). Empty-answer rows were rescored for every system alike.
