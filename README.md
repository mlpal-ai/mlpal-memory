# MLPal Memory

**Institutional memory for AI agents — bi-temporal, ontology-typed, governed, and
deterministic on the read path.**

MLPal Memory turns what your organization's agents and people already produce —
coding sessions, markdown knowledge, repositories, harness telemetry — into a
governed memory store that any agent can query in milliseconds:

- **Two tiers.** *Direct* memory stores verbatim, citeable passages; *derived*
  memory holds ontology-typed facts with provenance links back to the evidence
  they came from. Facts never float free of their sources.
- **Bi-temporal.** Every fact carries valid-time and system-time. Supersession
  invalidates instead of deleting; `as_of` reads answer "what did we believe at
  *t*?" in either timeline.
- **Governed writes.** Every ingest flows through one auditable gate: consent
  (per-scope opt-out with purge-on-clear), deterministic extraction policy, and
  secret redaction — before anything is stored.
- **Scope hierarchy.** global / org / team / service / repo / agent / user, with
  owner-only personal memory (enforced against administrators) and a workspace
  facet for "me, in repo X" focus.
- **Deterministic reads.** Hybrid retrieval (IDF-weighted full-text + semantic
  ANN, weighted reciprocal-rank fusion, recency decay, per-document diversity)
  issues **zero model calls**. The `/memory/answer` endpoint assembles an
  llms.txt-shaped markdown packet — TL;DR, facts, verbatim evidence with
  `memory://` citations, contested labels, gaps, freshness — in ~100 ms.
- **Agent-safe by contract.** The MCP surface is read-only (pinned by test),
  forwards the caller's own credential, and holds no secrets.

The design and its evaluation are described in the MLPal Memory paper
(launch post: link forthcoming).

## Quickstart

```bash
docker compose up --build          # Postgres (pgvector) + API/worker + read-only MCP
```

Open the UI at **http://localhost:8000/ui/** (build it once with
`cd ui-app && npm install && npm run build`).

Semantic embeddings run **in-process** by default (`bge-small` via ONNX — no API
key, no external calls; the model downloads on first use). Point
`MLPAL_EMBEDDINGS_PROVIDER=gateway` at an OpenAI-compatible `/v1/embeddings`
endpoint to use a hosted embedder instead.

### Ingest your own corpus

```bash
python scripts/collect_local.py --source all      # Claude Code sessions, md/skills, repos
```

Collectors are idempotent (content-hashed ids + server-side dedup) — re-run them
any time; unchanged inputs are recognized, changed files become new versions with
their own event time.

### Ask it questions

```bash
curl -s "http://localhost:8000/api/v1/memory/answer" \
  --get --data-urlencode "q=what build system does repo-x use" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"
```

### Plug into Claude Code

```bash
claude mcp add --transport http memory http://localhost:8011/mcp
```

The MCP serves `memory_search`, `memory_get`, and `memory_answer` — read-only.
See `docs/integrations/` for the full agent-integration guide.

## Evaluate on your corpus

```bash
python evals/run_eval.py         # retrieval quality vs a grep baseline (edit evals/goldset.yaml)
python evals/run_probes.py       # live contract probes: freshness, deletion certificate
python evals/run_probes.py --replay   # + rebuild-equivalence replay canary
```

The probe suite runs the store's maintenance contracts end-to-end against your
deployment and emits machine-readable verdicts — including a deletion
certificate proving a purged scope is gone from every read surface.

## Development

```bash
uv sync --extra pg --extra local-embeddings
pytest -q                        # offline suite (SQLite) + Postgres-marked tests
```

## License

Apache-2.0. Copyright 2026 MLPal.
