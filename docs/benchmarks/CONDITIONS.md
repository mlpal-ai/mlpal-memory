# Conditions actually applied, per system

Shared: LongMemEval questions and haystacks in the harness order; one tenant per haystack; reader
Claude Sonnet 5 through the MLPal gateway, prompt `v2-dated-preferences`, 20 items, 6k-token
context budget; judge = the benchmark's own template through the same model; index everything,
then query.

| system | version | write-time model | embedder | store | item | deviations and adapter decisions |
|---|---|---|---|---|---|---|
| ours | this repo, direct arm | none | the service's local embedder (bge-small ONNX) on the laptop and on the box alike, so pass 1, the S pass and the C3 runs differ only in the lever under test; a gateway-embedder run (`text-embedding-3-small`, the competitors' embedder) is a separate lever, not measured in v8 | Postgres + graph | passage (1,000-char chunk) with session date and id, up to 8 per session | none |
| mem0 OSS | mem0ai 2.1.0 | claude-haiku-4-5-20251001 (Anthropic key) | text-embedding-3-small (OpenAI key) | embedded Qdrant on disk under the run dir | mem0 memory sentence; date and session id from the metadata we attach | one `add` per session with the session as chat messages; `infer=True` (default); sampling at API default (SDK shim); BM25 keyword search disabled (`fastembed` not installed, mem0's default install); no `timestamp` passed (see LEARNINGS L1) |
| Graphiti | graphiti-core 0.30.2, FalkorDB container | claude-haiku-4-5-20251001 for model and small_model | text-embedding-3-small | FalkorDB, one graph per haystack (`group_id`) | episode (verbatim message), entity-edge `fact` with `valid_at`, node summary; session id from the episode name | one episode per message (`EpisodeType.message`, `add_episode_bulk` per session, `reference_time` = session date + index), as in Zep's LongMemEval evaluator; `search_` COMBINED_HYBRID_SEARCH_RRF limit=k — no reranker model (Zep used the cross-encoder variant: a deviation, read path kept model-free like the others); sampling at API default (SDK shim). First shake-out used one episode per session (superseded, kept in the worklog) |
| supermemory | self-hosted lite 0.0.8 (binary) | its memory agent via its OpenAI provider → counting proxy (`scripts/h2h/llm_proxy.py`, port 8787) → claude-haiku-4-5-20251001 through the MLPal gateway; metered, prompts logged | text-embedding-3-small via `SUPERMEMORY_EMBEDDING_API_KEY`, also through the proxy (metered) | encrypted local storage (pglite) under results/.system-supermemory-data | memory sentence or chunk from `search_mode=hybrid`; session id from metadata; date from our record of that session | one `add` per session with `document_date`; ingest waits for async processing (wall includes it); `limit=k`, `threshold=0`, rerank off |
| Cognee | 1.5.4 (litellm) | anthropic/claude-haiku-4-5-20251001 (cognify) | text-embedding-3-small | cognee defaults under the run dir (sqlite, LanceDB, embedded graph) | chunk (session-sized), then graph nodes/edges from the GRAPH_COMPLETION context (`only_context=True`, split per node/edge) | one `add` per session (date line prefixed), one `cognify` per haystack; `CACHING=false` (session-memory LLM call per search off); litellm `temperature=0.0` as shipped |
| LangMem | 0.0.30 (langchain-anthropic, langchain-openai) | claude-haiku-4-5-20251001 (memory-store manager: extract + update; deletes off = default) | text-embedding-3-small as the LangGraph store index | InMemoryStore per process, one namespace per haystack — the ingest cache must be cleared before each run | manager memory content; session = the invocation that created it | one manager invocation per session (date line prefixed); `store.search(limit=k)` |
| Memobase | server 0.0.42 (prebuilt image) + pgvector + redis; client 0.0.27 | claude-haiku-4-5-20251001 via counting proxy (port 8788) → gateway; metered | text-embedding-3-small via the proxy (event embeddings) | Memobase's Postgres | profile slot (topic/sub-topic: value) and event gist; events dated by the blob's created_at | one ChatBlob per session (created_at = session date, message timestamps as strings), insert + flush(sync); `profile()` + `search_event(topk=k, similarity_threshold=0, time_range_in_days=36500)` |

Ours on the laptop runs its production embedder (`MLPAL_EMBEDDINGS_PROVIDER=local`, fastembed), a
smaller model than the others' text-embedding-3-small — a handicap on ours, recorded rather than
"fixed" for the laptop passes; the box pass uses the gateway embedder as designed.

SDK shim: `anthropic` 1.7.0 dropped `temperature`/`top_p`/`top_k` from `Messages.create`; mem0 and
Graphiti still send them. `systems/meters.py` removes the unsupported kwargs before the call, for
every adapter alike, so all competitors sample at the API default.

Out of scope: **Letta** — the self-hosted memory server is end-of-life (`letta/letta:latest` refuses the REST layout and ships the cloud-backed "Letta Code" runtime instead), see LEARNINGS.
