# Memory v9 — learnings (numbering continues from v8's L37)

## Round 1 (2026-09-21/22, box c7i.4xlarge, fresh ingest per configuration)

- **L38 — the baseline reproduces on a fresh box: oracle-100 0.927 exactly; S-18 0.778 against
  0.833 the day before on the same configuration.** One question on 18 is the reader's own
  variance (two runs of the same thing), so S-18 decides nothing under ±1 and only a 100-question S
  pass (round 4) can rank close configurations there.
- **L39 — turn-boundary chunking (C2) is not an improvement: oracle −3 (five lost, two won), S +1.**
  Packing whole turns changes which text shares a chunk, and retrieval shifts with it in both
  directions: two counting questions flipped ("4 citrus fruits" for 3), two preference questions
  lost their user aside to assistant list text, and the "Target" coupon passage stopped being
  retrieved at all; the two wins were an assistant-content question and a preference. No mechanism
  to fix, just a different sample of a noisy process — the same finding as v7's line mode. The
  re-prefixed continuation pieces were correct in the traces, so the speaker signal is not what was
  missing. `chunk_mode=turn` stays available and off. Runs: `expbox-v9/longmemeval-oracle-ours-direct-20260921T234149Z`,
  `…-s-…20260921T235218Z`; baseline `…-oracle-…20260921T232300Z`, `…-s-…20260921T233330Z`.
- **L40 — a read-path defect found by C4: superseded values were served beside the current one.**
  The fold did its job (older `HAS_VALUE` edges invalidated, older value nodes marked
  `superseded`), but the node search legs filtered only scope and expiry, so a plain search could
  return every historical version of a keyed value that matched the query — three "plants
  acquired" tallies (2, 6 and 7 items) in one context. The state-anchor expansion attached the
  live value correctly; the direct hits on the old value nodes were the leak. Production keyed
  state (cost-daily, watch targets) had the same exposure for any value whose text matched a query.
  Fixed (`postgres.py`): the current view excludes `superseded` and `ended` nodes; as-of reads keep
  them, since edge validity reconstructs history there.
- **L41 — re-mentions double-count: "the snake plant my sister gave me last month" is counted again
  in every later session that recalls it, with a new date each time.** Exact (date, fact) dedup
  cannot see that; the extractor must. Second cut: the prompt carries `current_state` (the events
  already counted per topic) and the rule that a fact re-mentioning one of them is `none`, plus
  quantities only for additive contributions (a $325,000 purchase price and a pre-approval were
  being summed into one "home purchase" total). First cut: oracle-100 0.896 vs 0.927 (multi-session
  11/16 vs 14/16 — the served history and the double counts did the damage; single-session-assistant
  16/16 vs 15/16). Run: `expbox-v9/longmemeval-oracle-ours-direct-20260922T000019Z`.
- **L42 — C4 second cut: oracle-100 0.865 (83/96; multi-session 11/16 vs 14/16, knowledge-update 14/16 vs 16/16), and the served
  state lines are now the cause, not the read path.** With history hidden and re-mentions handled,
  the tallies a Haiku-class extractor produces are wrong about one topic in three: "tanks owned: 2"
  for 3 (the main tank never became an item), "games completed: total 35 hours" for 140, "home
  purchase: 350,000 usd" summing a pre-approval despite the rule, "family trips: Hawaii" with Paris
  missing, a "Target savings" topic whose one item mis-stated where the coupon was redeemed. The
  reader took every one of those lines over the passages beside them; on the one topic the tally
  was right ("plants acquired: 3 items"), the reader still answered 2 from the passages. So: a
  derived tally is trusted when wrong and ignored when right, and a small model cannot make it
  complete. That is v8 L31 one level up, and it closes write-time tallies from free conversation
  for this reader. The baseline's passages-first answers were right on 14 of those 16, which says
  the residual multi-session misses are reader arithmetic, not retrieval. Round 1's lasting result
  is L40. Run: `expbox-v9/longmemeval-oracle-ours-direct-20260922T004259Z`; round-1 model spend
  ≈ $12, box ≈ 2.5 h.

## Round 2 (read-time cost, 2026-09-22, same box, cached orgs)

- **L45 — there is no cheap page: accuracy falls roughly in proportion to the tokens removed.**
  Oracle-100 / S-18, context tokens per question in parentheses: 20 passages 0.927 / 0.778–0.833
  (2.4k / 4.0k); top-12 0.865 / 0.778 (1.9k, −22 %); top-8 with 4 per session 0.833 / 0.778 (1.2k,
  −51 %). The answer packet as the reader's context is not smaller — 2.8k on oracle and 4.5k on S,
  because it carries facts, citations and the passages — and scores 0.885 on oracle (−4) and 0.889
  on S (16/18, +1 over the best passage run, inside the ±1 band). The reader needs the full page;
  what makes a question cheap is the reader's price, not the page. Our read-time cost stands at
  ≈ 2.4k input tokens per question against mem0's 0.9k, and the way to close that is a cheaper
  reader for the easy question types, which is a product decision, not a memory change.
  Runs: `expbox-v9/longmemeval-oracle-direct-20260922T012609Z` (packet), `…-ours-direct-20260922T014359Z`
  (top-12), `…-20260922T015510Z` (top-8) and their S counterparts. Round-2 spend ≈ $5.

## Round 3 (hardening, 2026-09-22)

- **L43 — the worker's nightly jobs did not run on a freshly booted host.** `_last_trust_join` and
  `_last_retention` started at 0.0 and were compared against `time.monotonic()`, which counts from
  boot: until the host's uptime exceeded the interval (a day for the consequence join), every tick
  skipped both. Found because the join's integration test failed on a laptop up for less than a
  day and passed on one up longer. The trust tiers in production have depended on the host's
  uptime since v7. Fixed (`-inf` start); the test no longer depends on uptime.
- **L44 — what a model outage used to do to the service, and what it does now.** Before: one
  attempt per call with a fixed timeout; a 503 from the gateway failed the document's ingest with a
  500 (the harness counted 0–3 of those per run), a query with the embedder down was a 500, and the
  worker spent one of an episode's five retries per tick during an outage and dead-lettered it
  within minutes. Now (`7bcb67c`): bounded retries with jittered backoff on timeout / 429 / 5xx, a
  per-client breaker (5 consecutive failures, 30 s window, one probe), a typed `ModelUnavailable`;
  search answers from its lexical legs and says `degraded: ["vector"]`; a synchronous ingest past
  the per-process bound (8) is refused at once with 503 + Retry-After and the document is kept for
  the worker; an ingest that hits the outage is stored and answered `queued`; the worker defers the
  batch without touching retry budgets. Every path has a counter at `/metrics`
  (`model_call_failures{client,reason}`, `model_retries`, `breaker_open`, `search_degraded{tier,leg}`,
  `ingest_rejected`, `ingest_queued`) and a structured log event. Six integration tests drive each
  path with a mock gateway; the suite is 419 passing.
- **L46 — under load the hardened service is embedder-bound and otherwise quiet: 9,548 documents
  (200 histories of ≈ 48 sessions) in 55 minutes on a c7i.4xlarge, 2.87 documents/s at ingest
  concurrency 6, zero errors, while four queriers ran 23,661 searches at p50 288 ms, p95 461 ms,
  p99 574 ms, max 842 ms.** Ingest per document p50 2.0 s, p95 3.2 s — the local bge-small embedder
  at eight cores; Postgres stayed at 520 MiB and the service at 1.4 GiB. No 503s (concurrency 6 is
  under the bound of 8), no degraded searches, no dead letters. The laptop incident (three Postgres
  crash recoveries) was the laptop's swap, not the service; on a box with headroom the same
  workload is uneventful. What the run says to size by: ≈ 3 documents/s per 8 embedder cores at
  16 KB per document; for a tenant ingesting long histories in bulk the gateway embedder
  (`MLPAL_EMBEDDINGS_PROVIDER=gateway`) moves that cost off the box. `evals/load/results/load-20260922T020758Z/`.

## Round 4 (evidence, 2026-09-22)

- **L47 — ours on the hard split at 100 stratified questions: 0.865 (83/96 scored), reader/judge
  $2.18, no write-time spend.** By type: knowledge-update 16/16, single-session-user 16/16,
  single-session-assistant 16/16, single-session-preference 12/16, temporal-reasoning 12/16,
  multi-session 11/16. Context 4.0k tokens per question. No empty answers (three reader retries
  fired). Against the record: v7 measured 0.768 on all 500 before the reader fix and C6; the
  disclosed published S numbers are 0.60 (full context), 0.71 (Zep) and 0.82 (supermemory, its own
  judge). The 18-question S sample sat at 0.778–0.833, so its noise was masking a higher true level.
  The same three types miss on both splits, and they are the reader's arithmetic and preference
  judgement over correct passages, not retrieval (session recall@15 0.982).
  Run: `expbox-v9/longmemeval-s-ours-direct-20260922T030452Z`.
- **L48 — mem0 and Memobase on the same hard split under $35 write-time caps: mem0 0.804 on the 51
  questions it reached (ours 42/51 vs its 41), Memobase 0.865 on its 52 (ours 43/52 vs its 45).**
  Both caps ran out after ≈ 50 haystacks (mem0 $35.70, Memobase $35.44, ≈ $700 per thousand
  48-session histories); the dataset order meant they never reached the single-session-assistant
  and knowledge-update questions, where ours scores 16/16 and extraction systems 0.375–0.625 on
  oracle. On what they did reach — user facts, counting, preferences, some temporal — the three
  systems are within the ±3 noise band of each other. So the hard-split verdict is: tied on
  accuracy where they can afford to play, ahead where they cannot, at $0 against $700 per thousand
  histories. Memobase's 5.8k-token context per question is 45 % above ours; mem0's 0.9k is a quarter.
  Runs: `expbox-v9/longmemeval-s-mem0-direct-20260922T030548Z`, `…-memobase-…20260922T030649Z`; table `results/S100.md`.
