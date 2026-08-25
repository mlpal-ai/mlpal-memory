# Plugging local agents into the memory system

> The local stack must be up (`docker compose up -d`) and the store populated
> (`python scripts/collect_local.py --source all`). Verify: `./scripts/smoke_local.sh`
> and open the UI at <http://localhost:8000/ui/>.

The agent surface is the **read-only MCP sidecar** at `http://localhost:8011/mcp`
(streamable HTTP). Three tools:

| Tool | What it does | When the agent should use it |
|---|---|---|
| `memory_answer` | One question → a **memory packet** (markdown: facts, verbatim evidence with citations, contested points, gaps, freshness) | First stop when starting work in an unfamiliar area; pass `workspace=<repo>` |
| `memory_search` | Raw hybrid search → nodes + neighborhood (+`as_of` time-travel) | Targeted lookups, graph exploration |
| `memory_get` | One node + scope-visible neighborhood by id | Following a citation from a packet |

Identity: the sidecar forwards the caller's bearer token; with none present it uses the
env-gated dev identity baked into docker-compose (`local` / your username). In prod
those env vars are absent and reads fail closed.

## Claude Code

```bash
claude mcp add --transport http memory http://localhost:8011/mcp
```

Then add one line to `~/.claude/CLAUDE.md` so sessions actually use it:

```markdown
- **Memory**: before exploring an unfamiliar repo/area, call `memory_answer`
  (workspace = the repo directory name). Trust its citations; if it reports gaps,
  say so and proceed from first principles.
```

That's the whole integration. Optional freshness loop: re-run the collector after
working sessions (or on a schedule) so new sessions become memory:

```bash
python scripts/collect_local.py --source claude-code   # incremental; unchanged files skip
```

## Yodex

Two independent hooks — enable either or both:

1. **Read path (MCP)** — add to yodex settings (`~/.yodex/settings.json` or project
   `.yodex/settings.json`):

```json
{
  "mcp": {
    "servers": [
      { "name": "memory", "transport": "http", "url": "http://localhost:8011/mcp" }
    ]
  }
}
```

   Add the same one-line usage note to `YODEX.md` / `AGENTS.md`.

2. **Write path (episode mirroring)** — yodex's `SyncingMemory` decorator already
   mirrors topic-file writes to `POST /api/v1/episodes`. Enable with:

```json
{
  "memory": {
    "backend": "graph",
    "endpoint": "http://localhost:8000"
  }
}
```

## The with/without benchmark (protocol A)

Same model, same tasks, fresh workspace per run — the only variable is memory:

- **Arm A (baseline)**: memory MCP absent from settings.
- **Arm B (memory)**: MCP configured as above, store pre-seeded by the collectors.

Reuse the yodex bench harness (`yodex/bench`): its `results.jsonl` already records
`pass, wall_s, tokens_in/out, turns, files_changed`. Add per-run `memory_calls`
(count of `mcp__memory__*` invocations in the transcript). Warm-up tasks seed the
store; eval tasks share context with warm-ups; an unrelated-task subset guards
against negative transfer (memory must not lose there). ≥3 runs per task per arm.

## Cost model (write path, per the design)

| Operation | Model calls | Notes |
|---|---|---|
| Per-turn capture (working tier) | 0 | deterministic fold |
| Session distillation (commit) | 1 small-model call | `MLPAL_EXTRACTOR=llm`; haiku-class via gateway ≈ $0.01–0.03/session |
| Document ingest | 0 (embed only) | + rule extraction |
| Any read | 0 | always |
