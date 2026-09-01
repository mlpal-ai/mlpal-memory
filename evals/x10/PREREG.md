# x10 — the Claude Code usefulness study (launch ablation)

Preregistered 2026-08-31, before any run.

**Question**: for a real Claude Code user on a real org corpus, what does
adding MLPal Memory measurably change — in time, cost, and effort?

**Arms** (identical model, identical cwd = the org's code root, identical
tool permissions, one run per task per arm):
- BASELINE: stock Claude Code. It may Read/Grep/Glob the actual repos — the
  honest counterfactual (the org's knowledge IS on disk, findable the hard way).
- MEMORY: identical + the memory MCP attached + one sentence in the prompt
  saying org memory exists (mirrors real onboarding: `claude mcp add` + skill).

**Tasks** (10, each with a machine-checkable answer regex; drawn from the real
corpus; three deliberately include the STALENESS trap — the answer changed
over time and stale values outnumber current ones on disk):
recall-and-apply scope, stated: these measure recovering what the org already
knows — the product claim — not novel problem-solving.

**Metrics per run** (big-org legible):
- TIME: wall seconds to final answer.
- COST: total tokens (input+output+cache) × model list rates → $/task.
- EFFORT: number of agent turns + tool calls (proxy for compute/attention).
- CORRECTNESS: answer regex (primary); staleness-correct on trap tasks
  (current-world answer, not the superseded one).

**Analysis**: per-task table + medians; memory-vs-baseline deltas; the
staleness-trap subgroup reported separately (hypothesis: baseline gets these
WRONG confidently — grep finds the old answers; memory serves the current
world). N=1/task/arm: a mechanism-scale study, medians over 10 tasks, spreads
shown, no significance claims.
