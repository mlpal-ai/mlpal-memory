# MLPal Memory

**Institutional memory for AI agents: bi-temporal, ontology-typed, governed,
and deterministic on the read path.**

MLPal Memory turns what your organization's agents and people already produce
(coding sessions, markdown knowledge, repositories, PDFs, harness telemetry)
into a governed memory store that any agent can query in milliseconds. In our
preregistered study, Claude Code with this memory answered 10/10 real org
questions against 6/10 without it, at the same median cost per task and the
same number of agent turns; on the three questions whose true answer had
changed over time, the baseline scored 0/3 and bounced the question back to
the human. The harness ships in this repo (`evals/x10/`), so you can run the
same ablation on your own org.

- **Two tiers.** *Direct* memory stores verbatim, citeable passages; *derived*
  memory holds ontology-typed facts with provenance links back to the evidence
  they came from. Facts never float free of their sources.
- **Bi-temporal.** Every fact carries valid-time and system-time. Supersession
  invalidates instead of deleting; `as_of` reads answer "what did we believe at
  *t*?" in either timeline. Watched values (costs, versions, endpoints) get
  stable-key histories: the current value wins, every prior value stays
  reconstructable.
- **An answer ladder, priced honestly.** The default read is a deterministic
  packet: hybrid retrieval (IDF-weighted full-text + semantic ANN, weighted
  reciprocal-rank fusion, recency decay, per-document diversity), **zero model
  calls**, ~150 ms. Above it, `mode=hop` runs a bounded retrieve-reformulate
  loop that in our n=40 goldset reached 72% grounded answers against the 48%
  one-shot ceiling. Citations are enforced server-side: anything the model
  cites that was not actually retrieved is stripped and counted. The hop
  streams its trace live over SSE, and the UI shows every step.
- **Forgetting is a feature.** Delete a document (audited), purge a workspace
  (owner-scoped, both tiers), or say it in natural language: the curator
  proposes exact deletions with usage evidence, and nothing is removed until a
  human confirms the exact ids. Usage counters record what memory actually
  serves, so you measure junk before deleting it.
- **Governed writes.** Every ingest flows through one auditable gate: consent
  (per-scope opt-out with purge-on-clear), deterministic extraction policy, and
  secret redaction, before anything is stored.
- **Scope hierarchy.** global / org / team / service / repo / agent / user,
  with owner-only personal memory (enforced against administrators) and a
  workspace facet for "me, in repo X" focus.
- **Agent-safe by contract.** The MCP surface is read-only (pinned by test),
  forwards the caller's own credential, and holds no secrets. Writing to
  memory happens through ingestion and human-confirmed curation, never through
  a tool an agent could be prompt-injected into calling.

Scale: retrieval quality and latency are flat from 5k to 1,000,000 chunks
(100% hit@5 on the probe set throughout; 142 ms p50 at 1M on one r6i.2xlarge).
The design and its evaluation are described in the launch paper,
[`docs/paper/mlpal-memory-paper.pdf`](docs/paper/mlpal-memory-paper.pdf)
(launch post: link forthcoming).

## Quickstart

```bash
docker compose up --build          # Postgres (pgvector) + API/worker + read-only MCP
```

Open the UI at **http://localhost:8000/ui/** (build it once with
`cd ui-app && npm install && npm run build`). The Connect page in the sidebar
gives you the copy-paste agent setup.

Semantic embeddings run **in-process** by default (`bge-small` via ONNX; no API
key, no external calls; the model downloads on first use). Point
`MLPAL_EMBEDDINGS_PROVIDER=gateway` at an OpenAI-compatible `/v1/embeddings`
endpoint to use a hosted embedder instead.

### Ingest your own corpus

```bash
python scripts/collect_local.py --source all      # Claude Code sessions, md/skills, repos, PDFs
```

Collectors are idempotent (content-hashed ids + server-side dedup); re-run them
any time. Unchanged inputs are recognized, changed files become new versions
with their own event time, and multi-day sessions are segmented per day so the
timeline stays honest.

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

### Plug into Claude Code

```bash
claude mcp add mlpal-memory --transport http http://localhost:8011/mcp
```

The MCP serves `memory_search`, `memory_get`, and `memory_answer`, read-only.
See `docs/integrations/` for the full agent-integration guide. yodex users:
memory is a first-class backend there (`memory.backend=graph`), plus the same
MCP path.

### Forget things

```bash
# Direct: delete one document (audited).
curl -X DELETE "http://localhost:8000/api/v1/documents/<id>" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"

# Natural language, two-phase: preview exactly what would go, then confirm.
# (Or use the Manage page in the UI.)
```

## Evaluate on your corpus

```bash
python evals/run_eval.py         # retrieval quality vs a grep baseline (edit evals/goldset.yaml)
python evals/run_probes.py       # live contract probes: freshness, deletion certificate
python evals/run_probes.py --replay   # + rebuild-equivalence replay canary
python evals/x10/run_x10.py      # the with/without-memory agent ablation (edit evals/x10/tasks.yaml)
```

The probe suite runs the store's maintenance contracts end-to-end against your
deployment and emits machine-readable verdicts, including a deletion
certificate proving a purged scope is gone from every read surface. The x10
harness is the preregistered usefulness study from the paper: author questions
about your own org, mark the ones whose answer changed over time, and measure
correctness, cost per task, and turns in both arms.

## Development

```bash
uv sync --extra pg --extra local-embeddings
pytest -q                        # offline suite (SQLite) + Postgres-marked tests
```

## License

Apache-2.0. Copyright 2026 MLPal.
