# Self-hosting MLPal Memory

This is the operator's guide for an instance other people reach. The quickstart in the README is
for one machine; it trusts identity headers and must never be exposed.

## 1. Topology

Three containers: **Postgres** with pgvector (the only state), the **API** (which also runs the
fold worker: retention purge, nightly trust join, nightly note curation, under a Postgres
advisory lock so several API replicas never fold twice), and the **MCP** server, a thin client
over the API that forwards each caller's credentials. The API speaks plain HTTP on 8000 and the
MCP on 8011: put both behind a TLS-terminating reverse proxy.

## 2. Auth

Outside `MLPAL_ENVIRONMENT=local|test` the service refuses to start with dev auth on, with the
default internal key, or with no auth backend. A self-hosted instance authenticates against a
key file you own:

```bash
python -m mlpal_memory_graph.tools.api_keys new --file api_keys.yaml \
  --id sai-laptop --org acme --user sai --permissions memory.read,memory.write
python -m mlpal_memory_graph.tools.api_keys list   --file api_keys.yaml
python -m mlpal_memory_graph.tools.api_keys revoke --file api_keys.yaml --id sai-laptop
```

- `new` prints the key once (`mem_…`) and writes only its SHA-256 hash. The file is created with
  owner-only permissions.
- A key is pinned to one `org_id` (the tenant boundary); `user_id` attributes writes and unlocks
  personal memory; `permissions` are the platform grammar (`memory.read`, `memory.write`,
  `memory.admin`, `team:<id>` for team scopes).
- The file is re-read when it changes; a revoked key fails on the next request. A file that stops
  parsing keeps the last good set and logs `api_keys.reload_failed`.
- Callers send `X-API-Key: mem_…` or `Authorization: Bearer mem_…`; the MCP passes either through.
- `MLPAL_INTERNAL_SERVICE_API_KEY` is the machine-to-machine key (collectors, harness telemetry)
  and may write across tenants; treat it like a database password.

## 3. Start

```bash
export POSTGRES_PASSWORD=$(openssl rand -hex 24)
export MLPAL_INTERNAL_SERVICE_API_KEY=$(openssl rand -hex 24)
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
curl -s http://localhost:8000/health        # {"status":"ok",...} once the embedder is warm
```

The overlay sets `MLPAL_ENVIRONMENT=production`, turns dev auth and debug off, mounts
`./api_keys.yaml` (override with `MLPAL_API_KEYS_FILE`), and stops publishing Postgres. To run a
released image instead of building, set `image: ghcr.io/mlpal-ai/mlpal-memory:<version>` on the
two services in a further override file.

## 4. Settings

Every setting is an environment variable `MLPAL_<NAME>` (upper-cased). The ones an operator sets:

| setting | default | notes |
|---|---|---|
| `ENVIRONMENT` | `local` | `production` enables the fail-fast guard |
| `DEV_AUTH` | `true` | must be `false` outside local |
| `API_KEYS_FILE` | empty | the key file (§2) |
| `INTERNAL_SERVICE_API_KEY` | well-known default | must be set outside local |
| `DB_HOST/PORT/NAME/USER/PASSWORD/SCHEMA` | compose values, schema `memory` | or `DATABASE_URL_OVERRIDE` |
| `EMBEDDINGS_PROVIDER` | `local` in compose | `local` = bge-small in-process; `gateway` = any OpenAI-compatible `/v1/embeddings` at `EMBEDDINGS_SERVICE_URL` with `EMBEDDINGS_API_KEY` |
| `EMBEDDINGS_THREADS` / `EMBEDDINGS_CONCURRENCY` | 4 / 1 | the local embedder's cores and parallel documents |
| `INGEST_CONCURRENCY` | 8 | synchronous folds in flight per process; beyond it 503 + Retry-After, the document kept for the worker |
| `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL` | empty | optional; only synthesis and the `llm` extractors use a model |
| `EXTRACTOR` / `VALUE_EXTRACTOR` | `rule` / `pattern` | the measured defaults; the model-backed variants were net negative on the benchmarks |
| `DIRECT_USER_TURN_BOOST` / `DIRECT_SPEAKER_BOOST_MODE` | `1.3` / `question` | speaker-aware ranking for transcripts |
| `DIRECT_RETENTION_DAYS`, `RETENTION_INTERVAL_SECONDS` | 0 (keep forever), 3600 | the purge the worker runs |
| `TRUST_JOIN_INTERVAL_SECONDS`, `NOTES_CURATION_INTERVAL_SECONDS` | 86400 | the nightly jobs |
| `RLS_ENABLED` | `false` | Postgres row-level security as a second tenant fence (migration 0009) |
| `SOURCES_ROOT` | `/sources` | read-through file sources mounted into the container |

The full list with comments is `src/mlpal_memory_graph/core/config.py`.

## 5. Sizing

Measured on one 16-vCPU box with the local embedder at eight cores: 200 long histories (9,548
documents, 16 KB each) ingested at about 3 documents/s with zero errors while four clients ran
23,661 searches at p50 288 ms, p95 461 ms, p99 574 ms; the service held 1.4 GiB and Postgres
520 MiB. Ingest is embedder-bound: for bulk ingest of long histories, `EMBEDDINGS_PROVIDER=gateway`
moves that cost off the box. Give Postgres its own volume and at least 2 GiB.

## 6. Observability

- `GET /health`: `warming` (503) until the read path is warm, then `ok`, or `degraded` if warm-up
  failed. Compose, the MCP and your load balancer should wait on it.
- `GET /metrics`: Prometheus text. The failure paths are counters: `model_retries`,
  `model_call_failures`, `breaker_open`, `search_degraded`, `ingest_rejected`, `ingest_queued`.
  Alert on `breaker_open` and `search_degraded` rising; page on `ingest_rejected` sustained.
- Logs are structured JSON with stable event names (`config.fatal`, `auth.static_keys`,
  `api_keys.reloaded`, `notes.curated`, …). Secrets and key material are never logged.
- `GET /api/v1/memory/metrics` and `/api/v1/memory/report` are per-tenant memory health (what is
  served, endorsed, on probation, stale).

## 7. Backups and upgrades

- State is the Postgres database (schema `memory`) plus the embedder cache volume (rebuildable).
  `pg_dump --schema=memory` on a schedule; test a restore.
- Migrations run at API start (`alembic upgrade head`, all 18 forward-only). Pin the image
  version, back up before moving a minor version, read `CHANGELOG.md`. Roll back by restoring the
  backup and starting the previous image; migrations are not reversed in place.
- Embedding space changes (`EMBEDDINGS_MODEL`, `EMBEDDINGS_LOCAL_MODEL`) need a re-embed:
  `python -m mlpal_memory_graph.tools.reembed`.

## 8. Security notes

- Never publish 5432 or run with `DEV_AUTH=true` on a reachable host; the guard refuses the
  second, the overlay handles the first.
- One key per person or agent; revoke rather than share. Keys never appear in logs or exports.
- The service redacts secret-shaped strings on ingest (`tests/unit/test_redaction.py` lists the
  shapes) but treat the store as sensitive: it holds what your people said.
- Report vulnerabilities through GitHub's private vulnerability reporting on this repository,
  not in a public issue.
