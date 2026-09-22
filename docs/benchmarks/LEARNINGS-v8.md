# Learnings from the traces

Numbered so DECISIONS.md can cite them. Each entry: what the trace shows, the question ids, what it
suggests for our system. Shake-out entries come from six oracle questions per system and are
observations to test on the learning pass, not conclusions.

## mem0

- **L1 — mem0 grounds relative dates at ingest time, not session time.** With no `timestamp`, a
  probe message "I moved to Lisbon last month" became the memory "User moved to Lisbon around
  mid-August 2026" (ingested 2026-09-18). LongMemEval sessions are from 2023; every "last week /
  yesterday" in a session resolves to the wrong year unless the date is in the text. mem0's `add`
  has a `timestamp` parameter; whether it reaches the extraction prompt is to be checked, and the
  adapter should pass it either way so mem0 gets the same date we give our documents. *Ours:* we
  keep the verbatim passage with the session date and let the reader do the arithmetic; the
  temporal-reasoning misses in v7 (89/116 of our misses) are the same arithmetic done by a bigger
  model at read time. Neither approach resolves dates at write time correctly today.
- **L2 — mem0 stores a small number of rewritten sentences per session (≈ 7 per session on these
  11 sessions: 78 memories, 129k Haiku input tokens, $0.16).** Read-time context is tiny (≈ 20
  short sentences) versus our 6k-token passage block, and it still answered 4/6, including the
  degree question from a single returned memory. *Ours:* a compact "what this session established"
  layer would cut reader tokens by an order of magnitude on questions whose answer is a single fact;
  the v7 llm arm measured lower recall precisely because it replaced passages instead of adding to
  them — the candidate is fact sentences *alongside* passages, ranked together.
- **L3 — rewriting loses assistant-authored specifics.** Question `sharegpt…` (single-session-
  assistant: Admon's Sunday shift from a rotation sheet the assistant produced): mem0 kept "the
  rotation has 4 shifts …" and "Admon is one of 7 agents" and dropped the row that says Admon →
  Sunday 8–4. Recall@15 = 1.0 (the right session), answer wrong. *Ours:* the verbatim passage tier
  is what answers these; keep it under any compaction we add.
- **L4 — mem0 returned 1 of 12 stored memories for a `limit=20` search** (question `280352e9`).
  To be checked on the learning pass whether mem0 applies a similarity floor in `search` or the
  Qdrant local store does; if so, its recall on multi-fact questions is bounded by that floor.

## Graphiti

- **L9 — episode granularity decides Graphiti's result; whole sessions as one episode is the
  wrong configuration.** The first shake-out gave Graphiti one episode per session (20–40k chars);
  extraction produced 1–44 edges per haystack and the 18-question run scored 5/18 with session
  recall 0.49 and $1.41 of Haiku for 18 haystacks (364 calls). Zep's own LongMemEval evaluator
  ingests one episode per *message* and searches nodes + edges + episodes with a reranker
  (perplexity check against the graphiti repo and the Zep paper, 2026-09-18). The adapter now
  follows that (per-message `add_episode_bulk`, `COMBINED_HYBRID_SEARCH_RRF`, episodes returned as
  verbatim items). Under §8 the 0.28 is our configuration's number, not Graphiti's; the rerun is the
  one to read. *Ours:* the same lesson as v7's `per_document` cap: the unit you extract or index over
  bounds recall before any ranking does.
  **Rerun (per-message episodes, combined RRF search): 5/6 on the six shake-out questions (was 2/6),
  answered from edges and node summaries alone (no episodes came back from the bm25-only episode
  leg). The price: 1,402 Haiku calls, 3.7M input tokens, $4.41 for 11 sessions — $0.40 per session,
  ≈ 28× mem0 and ≈ 20× Cognee.** On LongMemEval-S (≈ 40 sessions per question) that is ≈ $16 of
  ingest per question; the $20 per-system cap covers one S question. Graphiti is therefore measured on
  the oracle split only, and this cost is itself the finding: Zep's published quality comes from
  per-message extraction that costs two orders of magnitude more than a passage index.

- **L5 — entity/edge extraction on a long session dropped the user fact the question needs.** For
  `e47becba` ("What degree did I graduate with?") the 17.9k-char session was turned into 17
  entities and 16 RELATES_TO edges, all product/app recommendations from the assistant's reply; the
  user's "degree in Business Administration" (present in the episode text) produced no entity and
  no edge, so no search could recover it. Graphiti's extractor is entity-first: facts that are not
  relationships between named things are the ones it misses. *Ours:* the same argument as L3 — a
  verbatim tier under any extraction; and if we add extraction, it must be question-agnostic
  ("what did the user state about themselves") rather than entity-centric.
- **L6 — Graphiti's write path is the most expensive: 93 Haiku calls and 310k input tokens for 11
  sessions (≈ 8.5 calls, $0.036 per session) versus mem0's 1 call per session and our 0.** On a
  40-session LongMemEval-S haystack that is ≈ $1.40 per question of ingest alone; the $20 cap
  covers ≈ 14 S questions.
- **L7 — Graphiti returns few items (≤ 7 of 20 asked) and its answer on `edb0332…` was correct
  despite the reader saying "not available … but given your …".** Its edges carry `valid_at` from
  `reference_time`, which is the one write-time temporal grounding in this set that is correct by
  construction (we supply it). *Ours:* our passages carry the same date; nothing to change.

## supermemory (self-hosted "lite", 0.0.8)

- **L11 — supermemory keeps both tiers and returns both.** Its pipeline is chunk → embed → "memory
  agent" (LLM) per document, and `search_mode=hybrid` returns memory sentences *and* matching
  chunks. On the six shake-out questions it answered 5/6 — the same set as ours, including the
  Admon shift row that mem0's rewrite lost (L3) because the chunk came back beside the memories.
  Its memories are dated relative to `document_date` ("moved to Lisbon last month relative to the
  document date"), so unlike mem0 OSS it grounds relative time correctly. *Ours:* this is the
  architecture v7 pointed at (passages + a compact fact layer, ranked together); supermemory is the
  existence proof that it works on this benchmark. Cost side unknown: the server calls the model
  itself and exposes no usage; ingest wall was 16–63 s per haystack on the laptop.
- **L12 — its memory agent duplicates.** The probe produced "Ana adopted a beagle named Rufus" and
  "The beagle Ana adopted is named Rufus" as two memories; 307 memories for 11 sessions versus
  mem0's 78. More items per session means more read-time candidates to rank, not more facts.

## Cognee (1.5.4)

- **L10 — Cognee's `GRAPH_COMPLETION` context is one 12–47k-character text.** Nodes with
  descriptions, whole-chunk nodes (its chunks are session-sized), then edges. Handed to a 6k-token
  reader budget as one item it is truncated to its head and the reader abstains; the first
  shake-out scored 2/6 with session recall 0 for that reason. Cognee is built to hand this context
  to *its own* completion call (`GRAPH_COMPLETION` without `only_context`), i.e. it assumes a large
  reader window, which is the cost it does not show in an accuracy number. The adapter now splits
  the context into node/edge items after the chunk items. Cognee 1.x also runs a "session memory"
  LLM call on every search by default (`SessionTurnAnalysis`; off with `CACHING=false`) — a read-time
  model call the other systems do not make. Its write path was the cheapest of the LLM systems
  (≈ 4 Haiku calls per haystack, $0.19 for 11 sessions) because it extracts over session-sized chunks.
  **Rerun (chunk items first, graph nodes/edges split, session memory off): 5/6.** Its chunks are
  whole sessions, so a Cognee "hit" hands the reader the entire session (4.3k context tokens on
  average, the most of any system) — on the oracle split this is close to full-context reading, and
  its LongMemEval-S number will tell whether the graph layer ranks the right session. One judge
  false positive observed (reader answered "not available", judge said Yes on `gpt4_2655b836`) —
  the Sonnet judge is lenient on abstentions; to be checked across runs (L13).

## Memobase (0.0.42)

- **L14 — a profile solved the multi-session counting question that every other system missed.**
  `afa9873b` ("How many items of clothing do I need to pick up or return?") needs three facts from
  three sessions; passage and memory-sentence systems returned two of them and answered 2. Memobase's
  profile slots aggregate per topic across sessions ("new boots to pick up, original boots to return,
  navy blue blazer at the dry cleaner") and the reader answered 3. It missed the knowledge-update and
  the assistant-rotation questions (profile slots hold the *latest* value and drop episodic detail),
  so it is complementary, not better. *Ours:* a per-topic *current state* view (which `memory.claim`
  already models for metrics/preferences) extended to free-text topics and returned as one item per
  topic is the candidate; measured on the multi-session type first. Cost: 34 Haiku calls, $0.19 for
  11 sessions (same as Cognee, a third of supermemory).

## LangMem (0.0.30)

- **L15 — LangMem is mem0 with a longer prompt.** One manager call per session (11 calls, 67k input
  tokens, $0.12 for 11 sessions, the cheapest LLM writer here), memories written as dense sentences;
  4/6 with the same misses as mem0 (the counting question and the assistant-authored rotation row).
  Its default instructions ask the model to record confidence ("p(x)") and to *synthesise*
  generalisations — the traces show it doing so; none of that helped on these questions.

## supermemory, metered

- **L16 — supermemory's memory agent costs 3.4× mem0 per session** (54 calls, 373k input tokens,
  $0.54 for 11 sessions once routed through the proxy), 262 memories for 11 sessions. Its embeddings
  also go through `OPENAI_BASE_URL`, so both are metered now; the log holds its prompts.

## Letta

- **Letta's self-hosted memory server is end-of-life.** `letta/letta:latest` (2026-09) prints
  "The retired Python Letta server is end-of-life and this image now contains Letta Code" and
  refuses the REST-server layout; the current product is an agent runtime ("App Server") backed by
  Letta's cloud account. Benchmarking a retired image teaches nothing about a live competitor, so
  Letta is dropped from v8 (recorded in CONDITIONS as out of scope).

## Ours, seen against the others

- **L18 — our ranking hands the reader assistant filler before the user's own statement.** On the
  counting question (`0a995998`), all 20 of our passages came from the right three sessions
  (session recall 1.0), but ranks 1–8 were the assistant's closet-organising list items ("Create a
  To-Pickup list", "Utilize the back of the door"); the user's sentence that carries the fact ("by the
  way, I still need to …") sat at rank 9 and was cut by the 1,000-char chunk boundary. Passage
  similarity to the *question* favours the assistant's on-topic prose over the user's aside.
  Memobase answered from a profile slot that had aggregated the three items. Candidates: (a) prefer
  user turns for user-fact questions (a speaker-aware boost, cheap, measurable on the harness);
  (b) chunk on turn boundaries so a user aside is not split from its sentence; (c) the per-topic
  state view of L14. Note `gold_hit` is uninformative for numeric golds ("3" matched "3 piles").
- **L19 — Memobase's item dates were the ingest day.** Adapter bug on our side: `updated_at` and
  `created_at` on its profile slots and events are write times; the reader saw "2026-09-18" on every
  item. Fixed (no item date; Memobase's own `[mention …, event …]` text carries the dates). Pass-1
  Memobase rows carry the wrong dates and its temporal-reasoning number there is to be discounted.

## Pass 1 (oracle-100, 96 scored): ours 0.844 vs mem0 0.854

- **L20 — our multi-session gap (0.562 vs mem0 0.875) is a within-session ranking failure, not a
  retrieval one.** Session recall was 1.0 on every multi-session question we missed. On `28dc39ac`
  ("hours playing games in total", gold 140) all 20 passages came from the five right sessions, but
  they were the assistant's game-recommendation lists ("The Witcher 3 (50–100 hours)"); the user's
  lines with the actual numbers ("finished Assassin's Creed Odyssey after 70 hours") were not in the
  top 20, and the reader summed the assistant's ranges. mem0 returned exactly those user facts at
  ranks 1–6 in 780 tokens. Same shape on `46a3abf7` (tanks: 5-gallon betta, 20-gallon community,
  1-gallon for the friend's kid — all three in mem0's top 5; none of the user's sentences in our 20).
  The question text is about *the topic* (games, tanks), the assistant's prose is dense in that
  topic, and passage similarity rewards it. C1 (user-turn boost) and C3 (fact list beside passages)
  target exactly this; C1 is being measured now.
- **L21 — mem0's write-time UPDATE merges a within-session change into one wrong fact.** On
  `852ce960` the pre-approval was $350,000 then $400,000 in the same session; mem0 stored "received
  pre-approval for $350,000" only (the reader answered $350,000). We keep both passages and the
  reader picked the later. This is the knowledge-update gap (ours 0.938 vs 0.812) and the argument
  for provenance-keeping updates (our `memory.claim` fold keeps the value history; a fact layer we
  add must too). mem0's single-session-assistant 0.625 is L3 again: the extraction prompt is
  user-centric, so assistant-authored specifics (a rotation row, a book outline) are not stored.
- **L22 — temporal-reasoning (ours 0.812 vs mem0 0.938): the date-prefixed extraction resolved
  the dates at write time.** `f0853d11`: mem0 stored "Coastal Cleanup on March 7, 2023" and "Walk
  for Hunger on February 21, 2023" (from "last week"/"two weeks ago" in a 2023-03-14 session, using
  the session-date line the adapter prefixes — L1's fix); the reader subtracted. Our passages for
  the same question were the assistant's event lists; the user's two sentences with the relative
  dates were not returned (L20 again), so there was nothing to subtract. C5 (resolved dates in fact
  text) is the write-time half; C1/C3 the retrieval half.
- Cost on this sample: mem0 $2.61 write-time + $0.65 reader/judge for 96 questions; ours $0 +
  $1.44. Per correct answer: ours $0.018, mem0 $0.040. mem0's reader context is 530 tokens against
  our 2,444.

## Pass 1, the rest

- **L23 — Cognee's default chunk is the whole session, and that alone explains its 0.625.**
  `cognify` caps chunks at min(embedder max tokens, half the LLM context) ≈ 8k tokens; median
  returned chunk 14.6k chars; mean reader context 4.3k of the 6k budget from one or two chunks; 17 of
  96 answers were abstentions; multi-session 0.125, temporal 0.25 (both need several sessions in
  view), single-session-assistant 1.0 (one whole session is exactly right there). Under §8 this is
  a configuration question first: `chunk_size=256` (≈ our passage) is being measured on the
  six-question shake-out, including what it does to its extraction cost, before its pass-1 number is
  read as Cognee's.
- **L24 — Graphiti at 0.66 (47 questions, $19.6 write-time) with per-message episodes: multi-
  session 0.143 and knowledge-update 0.625, temporal and assistant 0.875.** Its returned items
  (edges + node summaries, 930 tokens) are facts about *entities*; counting questions need the user's
  several statements side by side, and edge extraction merges them into per-entity relations
  (three tanks become three entity nodes with summaries, not a list the reader sees together).
  Knowledge-update misses are the same merge as mem0's (L21) done at the edge level with
  `invalid_at`, which the reader never sees. Cost per correct answer on this sample: $0.65 —
  35× ours.
- **L25 — the pass-1 ranking on the oracle split (96 questions unless noted):** ours 0.896 with C1
  (0.844 without), mem0 0.854, Memobase 0.844, supermemory 0.792, LangMem 0.708, Graphiti 0.66 (47),
  Cognee 0.625 (session chunks; capped 256-chunk rerun in progress). Reader context: ours 2.4k tokens, the
  memory-sentence systems 0.5–0.9k. Cost per correct answer: ours $0.018, LangMem $0.036, mem0
  $0.040, Memobase ≈ $0.043, Cognee $0.09, Graphiti $0.65. The two systems within a point of ours
  do it with a fifth of the reader tokens and a write-time model; ours does it with ranking alone.

- **L26 — supermemory's "more memories is better" costs it precision, not recall.** 0.792 at
  $10.40 for 160 sessions ($0.065 per session, 4× mem0), 6,701 memories (42 per session, L12's
  duplication at scale), reader context 1.7k tokens (memories + chunks). Session recall 0.92, but
  single-session-preference 0.5 and multi-session 0.625: with 42 near-duplicate memories per session
  the 20 returned items are variants of the same two or three facts, and hybrid mode's chunks come
  from those same sessions. The one place it is perfect is assistant-authored questions (1.0), because
  the chunks are there. Cost per correct answer $0.15 — 9× ours, 4× mem0. *Ours:* C3's extractor
  prompt asks for coverage without "more is better"; dedupe by (date, statement) at write time.

## S pass (18 stratified LongMemEval-S questions, ≈ 48 sessions each)

- **L27 — on the S haystacks, mem0's write-time cost becomes the story.** mem0 0.667 vs ours 0.722
  and 0.778 (two runs, same configuration, one question apart). Session recall 0.92 vs 0.99. mem0
  wrote 5,730 memories for 847 sessions at $12.40 — $0.69 of ingest per question, 32× our all-in
  cost per question ($22 vs $698 per 1,000 questions); its reader context stays small (0.9k vs 4.0k
  tokens), which is its one advantage at this scale. By type both are weak on multi-session (1/3);
  mem0 loses temporal (2/3 vs 3/3) and assistant-authored (1/3 vs 2/3) questions. The oracle-split
  picture (mem0 within a point of us at 1/5 the reader tokens) does not carry to S: when the
  haystack is 48 sessions, extracting every session is paid for whether or not it is ever asked
  about, and the passage index answers more of the questions. Table: `results/PASS2-s18.md`.

- **L30 — the user-turn boost (C1) is a blunt instrument: +5 on oracle-100, −2 on S-18, same box,
  same cached orgs, fixed reader.** Boost off 0.833 (15/18) vs on 0.722 (13/18) on S; the two laptop
  boost-on runs were 0.722 and 0.778. The two flips say why: on the "how many projects" count the boost
  put a fourth user passage in the block and the reader counted 5 instead of 2 (more user asides,
  more things that look like projects); on a single-session-assistant question ("you told me the
  Plesiosaur had…") the boost demoted the assistant passage that held the answer and the reader
  abstained. The oracle gains were all user-aside questions with two-session haystacks, where
  demoting assistant text costs nothing; on 48-session haystacks it displaces answers. Candidate
  C6: condition the boost on the question — off (or inverted) when the question asks what the
  assistant said or recommended ("you told me", "you suggested", "our conversation about"), on
  otherwise. Runs: `expbox-h2h/longmemeval-s-ours-direct-20260920T013104Z` (off) and
  `…-20260920T023358Z` (on).

- **L32 — C6 first cut (assistant turns by their `assistant:` line): oracle 0.927 = C1, S 0.778,
  one of C1's two S losses back.** The other loss shows why the detector was wrong: the Plesiosaur
  answer sits in a continuation chunk of a long assistant turn, and continuation chunks carry no
  speaker line at all (chunking is by paragraph within a `speaker: text` transcript, so only the
  first chunk of a turn has the prefix). In these transcripts a chunk without a user line *is*
  assistant prose, so the assistant mode now boosts exactly the chunks the user mode does not
  (second cut, `292a50c`, lane g). On oracle the first cut swapped one question each way (a citrus
  count won, a video-editing preference lost). Runs: `expbox-h2h/…-oracle-…20260920T031007Z`, `…-s-…20260920T032000Z`.

- **L33 — C6 second cut: oracle 0.927 (= C1), S 0.833 (= no boost). Adopted as the default.** With
  the assistant mode boosting every chunk that carries no user line, the Plesiosaur question is back
  and S equals the boost-off run (the one remaining difference from boost-off is the "how many
  projects" count, lost either way to an over-count when more user asides are in view — a C4
  problem). On oracle it swaps two multi-session wins for one multi-session and one
  single-session-assistant loss: net zero against C1 and +5 against no boost. Over the 114 questions
  measured the question-conditioned boost is the only speaker rule that never lost a split. Defaults:
  `direct_user_turn_boost=1.3`, `direct_speaker_boost_mode=question` (`config.py`, compose).
  Runs: `expbox-h2h/…-oracle-…20260920T032423Z`, `…-s-…20260920T033226Z`.

## C3 on the box (2026-09-20, oracle-100, 96 scored; box c7i.4xlarge, local embedder as in pass 1)

- **L28 — plain RRF over two independently ranked tiers is a round-robin, and it halved the
  passage tier.** C3 as first measured (`MLPAL_EXTRACTOR=facts` + `OURS_FUSION=rrf`): 0.865 against
  the direct baseline's 0.896 on the same 96 questions, won 3 (two counting questions and one
  temporal-ordering one — the facts tier does what it was built for) and lost 6. The fused list
  alternates fact, passage, fact, passage: rank 1 of each list ties under RRF, rank 2 ties, and so
  on, so the top 20 is always 10 facts + 10 passages whatever their scores, and the passages the
  reader saw fell from 20 to 10 (context 2,433 → 2,017 tokens). The 6 losses are what that predicts:
  a $185 total whose three expense passages were no longer in the block, a "four festivals" count with
  only two festivals left in view, two preference questions answered from generic assistant text,
  a "most recent trip" where the fact list held Hawaii and Paris without state, an ordering question
  answered with nothing (L29). Traces: `expbox-h2h/longmemeval-oracle-ours-direct-20260920T013858Z`.
  Consequence: facts must not compete with passages for the same slots. C3b (lane e) reads the same
  c3 orgs through the adapter's default path — the 20 boosted passages as before, then up to 10
  facts appended — so the facts tier can only add. Measured next.

- **L31 — appended, the facts tier is a coin flip on oracle-100: +4 −5 (C3b 0.917 vs 0.927
  rescored direct), and the losses are a trust problem, not a slot problem.** Wins: two counting
  and two temporal questions where the dated fact list enumerated the events ("4 charity events",
  "14 days", "three citrus fruits", "140 hours"). Losses: counting questions where the list was
  incomplete or double-counted and the reader believed it over the passages beside it — "$65 ($25 +
  $40)" for a $185 total, "2 plants" of 3, "27 titles (25 plus two added that day)" where the two
  were already in the 25 — and two single-session questions whose answer passage lost budget to ten
  facts (context 2,433 → 2,723 tokens mean, items up to 30). A crisp derived list outranks verbatim
  evidence in the reader's eyes, so a fact list must be complete for its topic or not be shown. That
  is C4's territory (a per-topic state folded at write time: running counts and totals, the current
  value of a list), which is what Memobase's profile slot does and why it led multi-session in pass 1.
  C3 as a retrieval-time addition is closed; the S ingest (a3) was stopped at 3 of 18 haystacks
  (≈ $2 spent) because the oracle result does not justify $8 and four box-hours more.
  Runs: `expbox-h2h/longmemeval-oracle-ours-direct-20260920T023358Z`.

## Harness

- **L8 — recall@k on rewritten items measures the session, not the fact.** mem0 and Graphiti both
  reach recall 1.0 on questions they answer wrong (L3), because every memory from the right session
  carries that session's id. A fact-level recall (does any returned item contain the gold answer
  string) is needed for the comparison; the shake-out rows already keep the items, so it can be
  computed after the fact. Added in `systems/compare.py` as `gold_hit@k` (a loose proxy: gold answers are
  often paraphrases, so it undercounts for every system alike).
- **L17 — our adapters split assistant list items into separate messages.** Found by reading
  Memobase's logged prompt: lines like `1. Online Reviews: Check …` inside an assistant turn contain a
  colon, and the per-adapter `speaker: text` parsers (mem0, LangMem, Memobase, Graphiti) started a new
  message on them, alternating roles. For Graphiti that meant one bogus episode per list line (and the
  extraction cost that goes with it). Pass 1 was stopped for those four systems, a shared
  `split_messages` (role-prefixed lines only when the document has roles; recurring names otherwise)
  replaced the four parsers, stores were reset and the four relaunched. Rule for the record: read the
  prompts a system receives before reading its score — §8's "our bug first" applied to us.
- **L13 — the Sonnet judge accepts some abstentions, rarely.** Quantified on pass 1 (96 questions each):
  ours 4 abstention-like answers, 1 judged correct (`35a27287`, a preference question whose template
  scores response style, arguably legitimate); mem0 9 abstentions, 1 judged correct (`7e00a6cb`, gold
  "International Budget Hostel" — a plain judge error). Bound: ≤ 1 point per system, symmetric; the
  rankings stand. Abstention counts themselves are informative: mem0 abstains twice as often (its
  rewrite has dropped the fact), ours answers wrongly instead (the fact is present but outranked).
- **L29 — the reader returned an empty answer whenever its reasoning used the whole 400-token
  budget, and that hit the hard question types of every system.** Sonnet 5 through the gateway spends
  output tokens on reasoning before the answer (`reasoning_tokens` in the cost block; the gateway
  reports `finish_reason: length` and `content: null` when the budget runs out first — reproduced with
  a 40-token call). Counting and ordering questions reason longest, so the empties cluster in
  multi-session and temporal-reasoning: pass-1 ours 3 of 96, ours-without-boost 5, Memobase 3,
  LangMem 1, Cognee 1, Graphiti 2 of 47, mem0 1 of 18 on S, C3 5 of 96. Every empty answer was
  judged wrong. Fix in the harness (`f7337f7`): one retry at four times the budget when the content
  is empty and the finish reason is `length`; `bench.py rescore RUN_DIR` re-asks the reader and
  re-judges the empty rows of a finished run from the items it stored and rewrites its files with a
  `rescored` record, so the pass-1 tables are corrected in place rather than rerun. Numbers after the
  rescore are in `PASS1-oracle100.md` (regenerated). Rescored pass 1 (oracle-100, 96 scored): ours
  0.927 (was 0.896; all 3 empties were right once the reader could finish), Memobase 0.875 (0.844),
  mem0 0.854 (no empties), supermemory 0.792 (none), LangMem 0.708, Cognee 0.625, Graphiti 0.66 on
  47 (the rescored answers stayed wrong); ours without the boost 0.854 (0.844); mem0 S-18 0.722
  (0.667). The lead over mem0 on the oracle split is 7 questions on 96, outside the ±3 noise band
  for the first time; L25's ranking stands with these numbers.
- **L34 — LangMem on S-18: 0.389 (7/18) against 0.708 on oracle, at $7.7 write-time for 18
  haystacks; its memory manager compresses a 48-session haystack into ≈ 100 abstractions.** Every
  haystack produced 85–111 items, and the ones retrieved read like a profile of the user's
  conversational habits — "User domain expertise & professional context (2023-05-20): User is
  engaged in educational curricu…", "Agent capability observation update…", "User information
  compression & session arc pattern…" — not the degree, the store or the count the question asks
  for. Session recall@15 is 0.694 (the manager does date each item by the session), so the item
  from the right session is often there and does not contain the fact: multi-session 0/3,
  single-session-user 1/3. On two-session oracle haystacks the same manager keeps more of the
  specifics (fewer sessions to fold into one profile). Cost side: 5.0M input tokens for 847 calls
  because the manager re-reads its existing memories before each write (query step) and rewrites
  them; the cap ($10) was not hit. The S split is where write-time abstraction pays its price.
  Run: `expbox-h2h/longmemeval-s-langmem-direct-20260920T020738Z`.
- **L35 — Cognee's chunk size was its whole pass-1 story, and the fix costs twenty times as much.**
  With `COGNEE_CHUNK_TOKENS=256` (against its default of a whole session) it reached its $8 cap after
  21 of the 96 haystacks — $0.39 per two-session haystack, 138 model calls per haystack, because the
  entity/relationship extraction and chunk summaries run per chunk — and answered 17 of those 21
  (temporal-reasoning 15/16, multi-session 2/5), exactly ours' 17 on the same questions and against
  5 for its session-chunk run. Its context per question is 5.8k tokens of 64 items (the CHUNKS list
  plus the graph context split into nodes and edges), the largest of any system. So Cognee is
  competitive on accuracy when configured for conversations, at ≈ $390 of write-time spend per 1,000
  two-session haystacks (mem0 ≈ $27, ours $0). Not rerun further: the cost question is answered.
  Run: `expbox-h2h/longmemeval-oracle-cognee-direct-20260920T041711Z`.
- **L36 — Memobase on S: 12/15 = ours (C6) 12/15 = mem0 12/15 on the same fifteen questions, at
  $12.4 of write-time for 15 haystacks; the three it never reached are single-session-assistant, its
  weakest type on oracle (0.375).** It hit the $10 cap at 15 of 18 haystacks (5.7M input tokens,
  2,037 calls: profile extraction plus a summary pass per session). Its misses are the two counting
  questions every system misses on this S sample (`0a995998`, `6d550036`) and one preference
  question. Its context per question is 5.8k tokens of ≈ 84 items (the whole profile plus events),
  against ours ≈ 2.4k; the profile slot answers "current value" questions, and on the 48-session
  haystacks that shows as knowledge-update 3/3 and temporal 3/3. Where the three systems tie, the
  cost per 1,000 S haystacks separates them: Memobase ≈ $830, mem0 ≈ $690 (L27), ours $0.
  Run: `expbox-h2h/longmemeval-s-memobase-direct-20260920T020455Z`.
- **L37 — supermemory on S: $2.6 of write-time per 48-session haystack; the $10 cap stopped it at
  4 of 18 questions (2/4).** 780 model calls and 7.1M input tokens for four haystacks: its memory
  agent runs per chunk and re-reads what it has, and each haystack produced 1,400–1,650 memories
  (against ≈ 100 for LangMem and ≈ 320 for mem0 on the same haystacks). Ingest wall time ≈ 11
  minutes per haystack, most of it the server's asynchronous processing that the adapter waits for.
  Per 1,000 S haystacks that is ≈ $2,600 of write-time, ten times mem0 (L27) and three times
  Memobase (L36); the four answers say nothing about accuracy at this sample size. The pass-1
  finding stands (L26: more memories cost precision, not recall) and the S split adds the cost
  scaling. Run: `expbox-h2h/longmemeval-s-supermemory-direct-20260920T043732Z`.
