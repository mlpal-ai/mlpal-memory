# How each system builds memory — from the logged model calls

Source: `model_calls.jsonl` in each run directory (SDK-metered systems) and the counting proxy's
logs (`results/.h2h-proxy/`, black-box servers); prompts quoted from the 2026-09-18 shake-outs.
mem0's prompts are read from its source (`mem0/configs/prompts.py`) because its logging landed after
its shake-out. This is the trace Sai asked for: what the write path actually sends to the model.

## The shapes at a glance

| system | calls per session (11-session shake-out) | write-time stages | what is stored |
|---|---|---|---|
| mem0 | 1 | one call: "Personal Information Organizer" extracts a JSON list of facts; then an update-decision call (ADD/UPDATE/DELETE/NONE) against the nearest existing memories | rewritten fact sentences (≈ 7 per session) |
| Graphiti (per-message) | ≈ 127 (1,402 / 11) | per message: entity extraction → fact-triple extraction with previous messages as context → temporal bounds per new edge → duplicate/contradiction check per edge → entity summaries; each a tool call with a Pydantic schema | entities with summaries, edges with `fact`, `valid_at`, `invalid_at`; episodes kept verbatim |
| supermemory | ≈ 5 (54 / 11) | one *agentic* extraction per document: the model is given the first 8 chunks and tools (`readChunks`, `searchMemories`, `searchDocumentChunks`, `getMemoryDetails`, `linkMemories`, `forgetMemory`, `CreateMemory`) and told "more memories is better than fewer"; plus a container-description call | atomic memory sentences (≈ 24 per session) linked to chunks; chunks kept and searchable |
| Cognee | ≈ 4 (27 / 11, ≈ 2 per chunk) | per chunk (session-sized): a "chunk summary for retrieval" (categories + facts) and a knowledge-graph extraction (nodes typed Person/Product/Concept/Date…, snake_case edges with descriptions) | chunk, chunk summary, typed graph |
| LangMem | 1 | one "memory subroutine" call per session with the full instruction set (extract, compare/update, synthesise with p(x) confidence); with existing memories it returns JSON patches (`PatchDoc`) plus new `Memory` items | dense memory paragraphs, revised in place |
| Memobase | ≈ 3 (34 / 11) | per blob: (1) "expert of logging personal info, schedule, events" → dated memo lines tagged info/event/schedule with `[mention YYYY-MM-DD, event YYYY-MM-DD]`; (2) "professional psychologist" → `TOPIC::SUB_TOPIC::MEMO` profile lines against a fixed topic tree; (3) "memo maintainer" → per-slot ADD/UPDATE/DISCARD; (4) high-level preference summary | profile slots (latest value per topic/sub-topic), dated events |
| ours (direct) | 0 | none | 1,000-char passages with session date |

## What each prompt is trying to do, and what it costs the answer

**mem0** — the extraction prompt is a *personal information* organiser: preferences, personal
details, plans, health, professional details. It is explicitly user-centric ("extract the relevant
facts and preferences *about the user*"); assistant-authored content is only kept when it is about
the user. That is why the rotation-sheet row (L3) vanished: the assistant produced it. The prompt
also injects `Today's date is {now}` — the source of L1 (relative dates resolved against ingest
day). The follow-up update prompt compares each new fact to retrieved neighbours and picks
ADD/UPDATE/DELETE/NONE, so contradictions are resolved at write time by a small model with no
provenance kept.

**Graphiti** — the most explicit pipeline. Entity extraction has a long NEVER list (pronouns,
abstract nouns, bare kinship terms → "Nisha's dad"), fact extraction only between two extracted
entities in the *current* message with previous messages as context, then a dedicated temporal call
per edge ("NEVER hallucinate dates", resolve relative expressions against REFERENCE_TIME), then
duplicate/contradiction detection with idx lists, then entity summaries under 1,000 chars. The
design is sound for temporal knowledge; the cost is that every step is a model call per message
(L9) and that facts which are not a relation between two named entities are never edges (L5). In
the per-message rerun, edges + node summaries alone answered 5/6.

**supermemory** — the only *agentic* writer: the model reads chunks with tools, searches existing
memories 3–5 times, and creates memories one tool call at a time, with an explicit instruction that
"more memories is better than fewer — split anything compound". It keeps the chunk store and returns
chunks beside memories at read time (hybrid), which is what saved the assistant-authored answer.
Cost is 3.4× mem0 and the duplication in L12 is the direct result of "more is better".

**Cognee** — two prompts per chunk: a retrieval summary in a fixed two-section format (categories,
then self-contained facts ordered by time) and a Wikipedia-style graph extraction with basic node
types and a hard rule to keep dates as `Date` nodes. Chunks are session-sized, so the graph is built
over whole sessions in one pass (cheap) and search returns whole sessions as chunk items (L10).

**LangMem** — one long instruction: extract, contextualise with confidence, compare and update,
"synthesise & reason … using deduction, induction and abduction", and record memories "exactly as
you'd want to recall them when predicting how to act". The traces show the model writing
meta-memories about *conversation quality* and *agent behaviour patterns* ("PATTERN: when user asked
for local recommendations…"), and in the next call issuing a JSON patch to remove one of its own
earlier memories as inaccurate. For question answering this is spend on self-reflection; its
user-fact memories are the same as mem0's.

**Memobase** — a chain: dated memo lines (with the explicit `[mention …, event …]` convention, the
cleanest temporal grounding in the set after Graphiti's), then profile slotting against a fixed
topic tree (basic_info, contact_info, education, work, interest, psychological, life_event…), then
per-slot merge decisions, then a preference summary. The profile is what answered the counting
question (L14): the slot for the relevant sub-topic accumulated three items across sessions. The
same mechanism drops episodic detail (assistant-authored rows) and keeps only the latest value.

## What this suggests for ours (candidates, to be measured)

1. **Keep passages; add a compact per-session fact list beside them** (the supermemory shape, the
   Cognee summary shape): one extraction call per session producing dated, self-contained fact
   sentences, ranked together with passages. Expected effect: fewer reader tokens on single-fact
   questions, no loss on assistant-authored ones. Cost bound: mem0/LangMem class (≈ $0.01 per
   session with Haiku).
2. **A per-topic current-state view** (the Memobase shape) for the multi-session type: aggregate
   facts by topic across sessions and return one item per topic. `memory.claim` already keeps
   state per `(topic, key)`; the extension is free-text topics.
3. **Temporal grounding at write time** the Memobase/Graphiti way: `[mention date, event date]`
   on every extracted fact, with the session date as the reference. Our passages already carry the
   session date; the reader does the arithmetic — and gets it wrong on the temporal type (v7). A
   resolved event date in the item text removes the arithmetic from the reader.
4. **Not worth copying:** per-message multi-stage extraction (Graphiti's cost), self-reflective
   meta-memories (LangMem), "more memories is better" duplication (supermemory).
