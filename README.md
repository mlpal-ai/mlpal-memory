# MLPal Memory

**Institutional memory for AI agents: bi-temporal, governed, deterministic on the read path,
and measured against the field.**

MLPal Memory turns what your organization's agents and people already produce (coding sessions,
markdown knowledge, repositories, PDFs, harness telemetry) into a memory store any agent can
query in milliseconds. Nothing on the write path calls a model: passages are stored verbatim and
indexed lexically and semantically, and facts are extracted by rules that never guess. What is
served is what was said, with its date and its source, and a fact that was superseded is served
as history, never as the present.

## Measured

Same harness, same Sonnet 5 reader, the benchmark's own judge templates, every competitor run
from its open-source code under one protocol with its write-time models metered
([docs/benchmarks](docs/benchmarks/README.md)).

**LongMemEval, oracle split, 100 stratified questions (2026-09-20):**

| system | accuracy | write-time $ per 1k haystacks | all-in $ per 1k questions |
|---|---|---|---|
| **MLPal Memory** | **0.927** | **0** | 16 |
| Memobase | 0.875 | 27 | 37 |
| mem0 | 0.854 | 27 | 34 |
| supermemory | 0.792 | 108 | 120 |
| LangMem | 0.708 | 17 | 26 |
| Graphiti (47 questions reached) | 0.66 | 410 | |
| Cognee | 0.625 | 34 | 56 |

**LongMemEval, S split (48-session haystacks), 100 stratified questions (2026-09-22):** 0.865,
tied with mem0 and Memobase on the questions they reached under a $35 write-time cap each, at
zero write-time cost ([S100.md](docs/benchmarks/S100.md)).

What this does not say: these are our runs of their code, not their published numbers (different
judge, reader and sample), and no hosted product was measured. The gap to the second and third
systems on the oracle split is outside run-to-run noise; the gap between those two is not. Under
load the service ingests about three documents per second per eight embedder cores with search
p99 under 600 ms ([HARDENING.md](docs/benchmarks/HARDENING.md)).

## What it does

- **Two tiers.** *Direct* memory stores verbatim, citeable passages; *derived* memory holds
  typed facts with provenance back to the evidence. Facts never float free of their sources.
- **Bi-temporal.** Every fact carries valid time and system time. Supersession invalidates
  instead of deleting; `as_of` reads answer "what did we believe then?".
- **Deterministic read path.** Search, the answer packet and the projection an agent loads at
  session start make zero model calls. A model is optional, only for synthesis and only if you
  bring one.
- **Governed.** Scopes (org, team, service, repo, agent, user), per-scope consent and policy,
  owner-only personal memory, two-phase forgetting with a deletion certificate, secret redaction.
- **Trust by consequence.** A learning an agent writes is on probation until runs that saw it
  succeed; endorsed facts rank first, retracted ones leave. Owners can pin what must always be in
  front of an agent, with an expiry.
- **A door for the owner.** Harness telemetry is distilled into facts about how a harness
  profile behaves; proposals to tune it wait for a person's approval, and a rejection's reason is
  itself a memory the next proposal reads.
- **Built to degrade, not fail.** Model outages open a breaker; search falls back to its lexical
  legs and says so; ingest is bounded and queues instead of dropping.

## Quickstart

```bash
docker compose up --build          # Postgres (pgvector) + API/worker + MCP; builds the UI too
```

Open the UI at **http://localhost:8000/ui/**. Embeddings run in-process by default (`bge-small`
via ONNX; no API key; the model downloads on first use).

### Ingest your own corpus

```bash
python scripts/collect_local.py --source all      # Claude Code sessions, md/skills, repos, PDFs
```

Collectors are idempotent (content-hashed ids, server-side dedup). Changed files become new
versions with their own event time; multi-day sessions are segmented per day.

### Ask it questions

```bash
# The free deterministic packet (zero model calls):
curl -s "http://localhost:8000/api/v1/memory/answer" \
  --get --data-urlencode "q=what build system does repo-x use" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"

# The memory hop (bounded retrieval loop, citations enforced, SSE trace):
curl -Ns "http://localhost:8000/api/v1/memory/answer/stream" \
  --get --data-urlencode "q=why did we migrate accounts and what did it change" \
  --data-urlencode "max_hops=3" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"
```

### Plug into an agent

```bash
claude mcp add mlpal-memory --transport http http://localhost:8011/mcp
```

The MCP serves `memory_search`, `memory_get`, `memory_answer`, `memory_brief`, `memory_notes`,
`memory_document`, and the governed writes `memory_write`, `memory_endorse`, `memory_retract`.
See [docs/integrations](docs/integrations/) for the agent-integration guide; in yodex, memory is
a first-class backend (`memory.backend=graph`).

### Forget things

```bash
curl -X DELETE "http://localhost:8000/api/v1/documents/<id>" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"
```

Natural-language forgetting is two-phase: preview exactly what would go, then confirm (the Manage
page in the UI does the same).

## Run it for a team

The quickstart trusts identity headers and is for one machine. For an instance other people reach,
mint keys and start the production overlay: dev auth is off, every key is pinned to one tenant,
the MCP forwards the caller's key unchanged.

```bash
python -m mlpal_memory_graph.tools.api_keys new --file api_keys.yaml --id sai --org acme --user sai
POSTGRES_PASSWORD=... MLPAL_INTERNAL_SERVICE_API_KEY=... \
  docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
claude mcp add mlpal-memory --transport http http://<host>:8011/mcp --header "X-API-Key: mem_..."
```

Sizing, backups, upgrades, metrics and every setting: [docs/SELF_HOSTING.md](docs/SELF_HOSTING.md).
Images: `ghcr.io/mlpal-ai/mlpal-memory:<version>` (amd64, arm64).

## Evaluate it

```bash
bash evals/benchmarks/fetch_datasets.sh                  # LongMemEval, LoCoMo, ConvoMem
uv run python evals/benchmarks/bench.py run --bench longmemeval --split oracle --limit 100 --stratify
uv run python evals/benchmarks/bench.py run --bench longmemeval --split oracle --limit 100 --stratify --system mem0
python evals/run_eval.py                                 # retrieval quality on your own goldset
python evals/x10/run_x10.py                              # with/without-memory agent ablation on your org
```

The benchmark kit runs every system through the same loop with its model usage metered; the
adapters for mem0, Memobase, supermemory, LangMem, Graphiti and Cognee ship in
`evals/benchmarks/systems/`.

## Development

```bash
uv sync --extra pg --extra mcp --extra local-embeddings
uv run pytest -q                 # offline suite (SQLite); Postgres-marked tests need MLPAL_TEST_POSTGRES_DSN
```

The in-process embedder needs an `onnxruntime` wheel for your platform (Linux, macOS on Apple
silicon, Windows). On an Intel Mac run the service in Docker or use `MLPAL_EMBEDDINGS_PROVIDER=gateway`.

Changes per release: [CHANGELOG.md](CHANGELOG.md).

## License

Apache-2.0. Copyright 2026 MLPal.
