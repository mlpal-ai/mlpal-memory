# Getting started with MLPal Memory

From zero to an agent that remembers your organization, in about fifteen
minutes. Everything here runs on your machine; nothing leaves it unless you
point it somewhere.

## What you are setting up

MLPal Memory turns what your org already produces (coding sessions, markdown,
repositories, PDFs) into a governed memory store any agent can query:

- **Direct tier**: verbatim, citeable passages from your documents.
- **Derived tier**: typed facts extracted from them, each linked back to its
  evidence. Watched values and lifecycle states (a cost, a version, "the
  status page is live") get stable keys, so when the truth changes the old
  value is superseded, never deleted, and any past date stays reconstructable.
- **Reads**: a free deterministic answer packet (zero model calls, ~150 ms),
  and above it a bounded retrieval loop (the "hop") with server-enforced
  citations.

## 1. Prerequisites

- Docker (compose v2)
- Node 18+ (only to build the UI once)
- Python 3.12+ with [uv](https://docs.astral.sh/uv/) (only for the collectors
  and eval harnesses)

No API key is needed for the core loop: embeddings run in-process (bge-small
via ONNX, downloads on first use). An OpenAI-compatible LLM endpoint is
optional and only powers the hop, answer synthesis, natural-language curation,
and the LLM extraction tiers.

## 2. Start the stack

```bash
git clone https://github.com/mlpal-ai/mlpal-memory
cd mlpal-memory
cd ui-app && npm install && npm run build && cd ..
docker compose up --build
```

Three containers come up: Postgres (pgvector), the API + fold worker, and a
read-only MCP server on port 8011. Open **http://localhost:8000/ui/** — the
Overview page explains the two tiers; the sidebar holds your dev identity
(org/user) and workspace focus.

Optional, to enable the LLM-powered features:

```bash
MLPAL_LLM_API_KEY=<key> MLPAL_VALUE_EXTRACTOR=llm docker compose up --build
```

The key is sent to whatever OpenAI-compatible `/v1/chat/completions` endpoint
you configure; the default read path never uses it.

## 3. Ingest your corpus

```bash
uv sync --extra pg --extra local-embeddings
python scripts/collect_local.py --source all
```

Collectors cover Claude Code session history, markdown/skills files, git
repositories (READMEs, docs, deterministic repo cards), and PDFs. They are
idempotent: re-run any time; unchanged inputs are recognized, changed files
become new versions with their own event time, multi-day sessions are split
per day. Every document flows through the governed fold: consent check,
extraction policy, secret redaction, then the bi-temporal write.

Watch the Documents and Episodes pages fill as the worker folds. The Timeline
page shows knowledge forming over time once you have a few days of history.

## 4. Ask it things

The free deterministic packet (this is the default read — no model involved):

```bash
curl -s "http://localhost:8000/api/v1/memory/answer" \
  --get --data-urlencode "q=what build system does repo-x use" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"
```

You get a markdown packet: TL;DR, typed facts, verbatim evidence with
`memory://` citations, contested labels, gaps, freshness. If a watched value
or state answers the question, it leads the packet with its validity window
("current since ..."), and older evidence is labeled as predating it.

The hop (bounded retrieve-reformulate loop, streams its trace live):

```bash
curl -Ns "http://localhost:8000/api/v1/memory/answer/stream" \
  --get --data-urlencode "q=why did we migrate accounts and what changed" \
  --data-urlencode "max_hops=3" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"
```

Time travel — what did we believe on June 1st:

```bash
curl -s "http://localhost:8000/api/v1/memory/answer" \
  --get --data-urlencode "q=how much does the platform cost per day" \
  --data-urlencode "as_of=2026-06-01T00:00:00Z" \
  -H "X-Test-Org-Id: local" -H "X-Test-User-Id: $USER"
```

Or use the Ask page in the UI, which exposes all three routes with their
costs labeled, and renders the hop's live trace.

## 5. Connect your agent

```bash
claude mcp add mlpal-memory --transport http http://localhost:8011/mcp
```

That is the whole integration. The agent now has `memory_answer`,
`memory_search`, and `memory_get` — read-only by contract: writing happens
through ingestion and human-confirmed curation, never through a tool an agent
could be prompt-injected into calling. Ask Claude Code a question about your
org and it answers from memory with citations instead of re-deriving from
files. In yodex, memory is additionally a first-class backend
(`memory.backend=graph`, opt-in).

The Connect page in the UI shows this command pre-filled for wherever the
server is running.

## 6. Forget things (it's a feature)

- Delete one document: `DELETE /api/v1/documents/{id}` (audited).
- Purge a workspace you're done with: owner-scoped, both tiers, audited.
- Or say it in natural language on the Manage page: "the migration is done,
  forget the details, keep the key decisions." The curator proposes exact
  deletions with usage evidence (what memory actually served), and nothing is
  removed until you confirm the exact ids.

Superseded values are not deletion targets: they stay reconstructable under
as-of, which is the difference between forgetting and losing history.

## 7. Measure it on your own org

The eval harnesses ship in the repo — the same ones behind the paper's
numbers:

```bash
python evals/run_eval.py          # retrieval quality vs grep (edit evals/goldset.yaml)
python evals/run_probes.py        # live contract probes incl. deletion certificate
python evals/x10/run_x10.py       # the with/without-memory agent ablation
```

For x10, author ~10 questions about your org with machine-checkable answers
in `evals/x10/tasks.yaml`, mark the ones whose true answer changed over time,
and run both arms. If your numbers disagree with ours, open an issue.

## Where to go next

- `README.md` — the measured claims, with conditions.
- `docs/paper/mlpal-memory-paper.pdf` — design, comparison, full evaluation.
- `docs/integrations/` — agent integration details.
- `/docs` on the running server — the interactive API reference.
