# Changelog

Versions follow the public package (`mlpal-memory`); the managed service deploys the same code.

## 0.2.0 — 2026-09-22

Measured against six open-source memory systems and hardened for production; the first release a
self-hosted instance can run outside dev mode.

### Self-hosting
- **Static API keys** (`MLPAL_API_KEYS_FILE`): a key file the operator owns replaces the platform
  auth service. Keys are stored as SHA-256 hashes, pinned to one tenant, hot-reloaded on edit,
  minted and revoked with `python -m mlpal_memory_graph.tools.api_keys`.
- **Production compose overlay** (`docker-compose.prod.yml`): dev auth off, required secrets,
  Postgres unpublished.
- **The image builds the UI itself**; `docker compose up --build` needs no Node on the host.
  A `.dockerignore` keeps datasets and results out of the build context.

### Read path
- Speaker-aware ranking for conversation transcripts: a passage from the user's own turn scores
  1.3× on question-shaped queries (`direct_user_turn_boost`, `direct_speaker_boost_mode`).
- The current view never serves a superseded keyed value; as-of reads still reconstruct history.
- Pinned facts: a reserved quarter of every projection for what the owner wants always in front of
  an agent, with an expiry (`pinned`, `valid_until`; `POST /memory/endorse` with `pin`).
- Unprompted (stop-hook) learnings reach only their author and HOP, rank last, three per session.

### Resilience
- Model-client retries with backoff and a circuit breaker; search degrades to its lexical legs
  when the embedder is down and says so (`degraded: ["vector"]`).
- Synchronous ingest is bounded per process (503 + Retry-After past eight in flight); an ingest that
  meets a model outage is stored and answered `queued`; the fold worker defers during an outage.
- Worker clocks start at −∞: the nightly trust join and retention purge run on the first tick.
- Every failure path is counted on `/metrics` and logged with a stable event name.

### HOP tuning loop (memory ↔ harness)
- Registry contract gate accepts every telemetry contract from d11.2 on (d11.8 adds provider labels).
- Distiller facts for memory bypass, silent runs and unused capabilities; proposals target
  `modules.<provider>.enabled` when the HOP declares them tunable.
- Owner's door: `POST /memory/tune/proposals`, `GET /memory/tune/pending`, `POST /memory/tune/decide`;
  a rejection's reason is folded as a build learning the next turn reads.
- Nightly deterministic curation of the workspace note: stale open threads closed against the
  graph, a bounded current-state block rebuilt from injected state topics.

### Memory explorer UI
- Two pages the API had outgrown: **Notes** (the workspace note's five sections, edit with
  optimistic concurrency, version history, citations open the node) and **HOPs** (the registry,
  tune proposals pending the owner's decision with reject-with-reason, the decided history).
- A memory's trust verbs on its detail panel: endorse or withdraw, pin or unpin, retract with a
  reason; trust tier and pinned badges.
- Search shows when the vector leg was unavailable and how long the read took. The Connect page
  lists the MCP's real tool surface (six reads, three governed writes) and the key-file setup.
- Tune proposals and decisions no longer surface as searchable passages (they are ledger rows;
  a rejection reaches memory as the build learning).
- The service reports its installed version (`/health`) instead of a stale constant.

### Evaluation
- Public benchmark kit (`evals/benchmarks/`): LongMemEval, LoCoMo and ConvoMem through the public
  API, plus adapters for mem0, Memobase, supermemory, LangMem, Graphiti and Cognee under one
  reader and judge. Reports in `docs/benchmarks/`.
- Load runner (`evals/load/load_run.py`).

## 0.1.0 — 2026-09-01

First public release: two-tier memory (direct passages and derived facts), bi-temporal store,
deterministic read path with zero model calls, read-only MCP, memory explorer UI, Claude Code and
yodex integration, preregistered usefulness study (x10) and the mem0 head-to-head (x11).
