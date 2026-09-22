"""HOP telemetry distillation — stage 2 of the optimizer loop (design §1.2).

Turns D11.2 run.completed episodes into typed watched facts the proposer can
cite: waste, budget, routing, and regression facts. DETERMINISTIC aggregation
over structured payloads — no LLM anywhere (the payloads are already typed;
extraction fragility has no business here). Facts ride the Metric/MetricValue/
HAS_VALUE machinery, so same-key supersession keeps each aggregate current as
windows advance, packets can lead with them, and /memory/metrics shows their
history.

Thresholds are explicit constants, not tunables-by-vibes: a fact below its
floor is silence, not a weak claim (statistical-power rule from design §3).
Only D11.2 episodes participate — D11.1 rows lack failure_class/tier/checks
and are skipped as ABSENT, never counted as zero.
"""

from __future__ import annotations

import re
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .extractor import EdgeSpec, EntitySpec
from .hop_names import contract_at_least, hop_base

MIN_RUNS = 30          # power floor for waste/budget/routing facts
MIN_REGRESSION = 5     # occurrences before a failure-class fact exists
_MEMORY_READ = re.compile(r"memory_(search|answer|profile|projection)$")  # the memory read tools, MCP-prefixed or not


@dataclass
class RunStats:
    runs: int = 0
    successes: int = 0
    stalls: int = 0                       # failure_class == step_budget_stall
    parked: int = 0                       # run_result == needs_approval (D11.3 safety edge)
    observe_ran: int = 0
    observe_caught: int = 0               # observe ran and did NOT pass
    self_check_fired: int = 0
    anti_churn_fired: int = 0
    agent_ran: int = 0
    agent_caught: int = 0                 # verdict == FAIL
    out_tokens: list[int] = field(default_factory=list)
    # memory v10, from d11.6/d11.7 fields
    injected: int = 0                     # runs that started with memory in the projection
    reread: int = 0                       # …and still called a memory read tool (the prompt bypasses what it has)
    with_tool_calls: int = 0              # runs whose record carries tool_calls (d11.7+)
    silent: int = 0                       # …that completed with no tool call at all
    with_labels: int = 0                  # runs whose record carries labels (d11.8+)
    capability: dict[str, int] = field(default_factory=dict)  # provider label -> runs that used it


def _fact(key: str, label: str, value: str, evidence: str) -> tuple[list[EntitySpec], list[EdgeSpec]]:
    vkey = f"{key}={value}"
    display = f"{label} = {value}"
    return (
        [
            EntitySpec(type="Metric", key=key, name=label),
            EntitySpec(
                type="MetricValue", key=vkey, name=display,
                props={"value": value, "unit": "telemetry", "evidence_span": evidence[:300]},
            ),
        ],
        [
            EdgeSpec(
                type="HAS_VALUE", src_type="Metric", src_key=key,
                dst_type="MetricValue", dst_key=vkey,
                fact=display, functional=True, props={"value": value},
            )
        ],
    )


def distill_runs(episodes: list[dict]) -> tuple[list[EntitySpec], list[EdgeSpec]]:
    """``episodes`` = D11.2 run.completed payload dicts (as normalized by
    harness_telemetry). Returns watched-fact specs; empty when nothing clears
    its floor."""
    by_class: dict[tuple[str, str], RunStats] = defaultdict(RunStats)
    by_tier: dict[tuple[str, str, str], RunStats] = defaultdict(RunStats)
    by_model: dict[tuple[str, str, str, str], RunStats] = defaultdict(RunStats)
    by_version_failure: dict[tuple[str, str, str], int] = defaultdict(int)
    runs_by_version: dict[tuple[str, str], int] = defaultdict(int)

    for p in episodes:
        if not contract_at_least(p.get("contract")):
            continue  # D11.1: fields absent, never zero — excluded from aggregates
        if p.get("role") != "main":
            continue  # sub-agent, or a row whose role was never established: not a HOP run
        hop = hop_base(p["hop"]["name"])  # the read-only twin's runs count toward its parent
        task = p.get("task_type") or "unknown"
        s = by_class[(hop, task)]
        s.runs += 1
        if p["run_result"] == "success":
            s.successes += 1
        if p["run_result"] == "needs_approval":
            s.parked += 1  # D11.3 only; a D11.2 parked run reads `cancelled` and is NOT counted here
        if p.get("failure_class") == "step_budget_stall":
            s.stalls += 1
        checks = p.get("checks") or {}
        if (checks.get("self_check") or {}).get("fired"):
            s.self_check_fired += 1
        if (checks.get("anti_churn") or {}).get("fired"):
            s.anti_churn_fired += 1
        obs = checks.get("observe") or {}
        if obs.get("ran"):
            s.observe_ran += 1
            if not obs.get("passed"):
                s.observe_caught += 1
        verdict = (checks.get("agent") or {}).get("verdict")
        if verdict is not None:
            s.agent_ran += 1
            if verdict == "FAIL":
                s.agent_caught += 1
        s.out_tokens.append(int((p.get("tokens") or {}).get("output", 0)))
        # memory v10: was memory injected, and did the run read it again anyway? did it call nothing?
        mp = p.get("memory_projection") or {}
        injected = bool(p.get("memories_injected")) or int(mp.get("fact_count") or 0) > 0
        tools = p.get("tool_calls")
        if injected:
            s.injected += 1
            if isinstance(tools, dict) and any(_MEMORY_READ.search(k) for k in tools):
                s.reread += 1
        if isinstance(tools, dict):
            s.with_tool_calls += 1
            if sum(int(v or 0) for v in tools.values()) == 0 and p["run_result"] == "success":
                s.silent += 1
        labels = p.get("labels")
        if isinstance(labels, dict):
            s.with_labels += 1
            for name, n in labels.items():
                if int(n or 0) > 0:
                    s.capability[str(name)] = s.capability.get(str(name), 0) + 1

        tier = p.get("tier")
        if tier:
            t = by_tier[(hop, task, tier)]
            # a tier label can change model underneath (catalog max: claude-fable-5 → gpt-6-astra on
            # 2026-09-08); per-model stats keep a route comparison honest across such a change
            model = p.get("model")
            if model:
                m = by_model[(hop, task, tier, str(model))]
                m.runs += 1
                if p["run_result"] == "success":
                    m.successes += 1
                m.out_tokens.append(int((p.get("tokens") or {}).get("output", 0)))
            t.runs += 1
            if p["run_result"] == "success":
                t.successes += 1
            t.out_tokens.append(int((p.get("tokens") or {}).get("output", 0)))
            if (checks.get("self_check") or {}).get("fired"):
                t.self_check_fired += 1
            if (checks.get("anti_churn") or {}).get("fired"):
                t.anti_churn_fired += 1
            if verdict is not None:
                t.agent_ran += 1

        version = p["hop"]["version"]
        runs_by_version[(hop, version)] += 1
        fc = p.get("failure_class")
        if fc:
            by_version_failure[(hop, version, fc)] += 1

    entities: list[EntitySpec] = []
    edges: list[EdgeSpec] = []

    def emit(key: str, label: str, value: str, evidence: str) -> None:
        e, g = _fact(key, label, value, evidence)
        entities.extend(e)
        edges.extend(g)

    for (hop, task), s in sorted(by_class.items()):
        if s.runs < MIN_RUNS:
            continue
        # budget fact: how often this class exhausts the step budget
        emit(
            f"hop:{hop}|stall|{task}", f"{hop} stall rate ({task})",
            f"{s.stalls}/{s.runs}",
            f"{s.stalls} of {s.runs} {task} runs ended in step_budget_stall",
        )
        # edge fact (D11.3): how often this class parks at the safety envelope edge. High = the
        # envelope is tighter than the work (a blastRadius/approval knob signal for a HUMAN);
        # zero over many runs on an apply-capable HOP = the edge is never exercised (probe it).
        emit(
            f"hop:{hop}|edge|{task}", f"{hop} approval-edge rate ({task})",
            f"{s.parked}/{s.runs}",
            f"{s.parked} of {s.runs} {task} runs parked with needs_approval",
        )
        # waste facts: a check that ran often and NEVER caught anything
        if s.observe_ran >= MIN_RUNS and s.observe_caught == 0:
            emit(
                f"hop:{hop}|waste-observe|{task}", f"{hop} observe waste ({task})",
                f"0/{s.observe_ran}",
                f"observe ran {s.observe_ran} times on {task}, caught 0 failures",
            )
        if s.agent_ran >= MIN_RUNS and s.agent_caught == 0:
            emit(
                f"hop:{hop}|waste-agent|{task}", f"{hop} agent-check waste ({task})",
                f"0/{s.agent_ran}",
                f"agent check ran {s.agent_ran} times on {task}, FAILed 0",
            )
        # firing-rate facts for the intervention checks (content-free: we can see
        # THAT they fired, never why) — these map to the verification knobs most
        # HOPs actually declare tunable (selfCheck.minEdits, antiChurn.threshold)
        emit(
            f"hop:{hop}|fired-self-check|{task}", f"{hop} self-check firing ({task})",
            f"{s.self_check_fired}/{s.runs}",
            f"self_check fired in {s.self_check_fired} of {s.runs} {task} runs",
        )
        emit(
            f"hop:{hop}|fired-anti-churn|{task}", f"{hop} anti-churn firing ({task})",
            f"{s.anti_churn_fired}/{s.runs}",
            f"anti_churn fired in {s.anti_churn_fired} of {s.runs} {task} runs",
        )
        # memory v10 (d11.6/7): the prompt re-reads memory it was already given — a prompt fact, not a
        # knob; and runs that finish without a single tool call on a HOP whose work is reading
        if s.injected >= MIN_RUNS:
            emit(
                f"hop:{hop}|memory-bypass|{task}", f"{hop} memory bypass ({task})",
                f"{s.reread}/{s.injected}",
                f"{s.reread} of {s.injected} {task} runs that started with memory injected still called a memory read tool",
            )
        if s.with_tool_calls >= MIN_RUNS:
            emit(
                f"hop:{hop}|silent|{task}", f"{hop} silent runs ({task})",
                f"{s.silent}/{s.with_tool_calls}",
                f"{s.silent} of {s.with_tool_calls} {task} runs completed with no tool call",
            )
        # memory v10 (d11.8): which providers the runs actually touched — the capability mix a HOP
        # specialises on (a provider used by ~none of the runs is a module the owner can switch off)
        if s.with_labels >= MIN_RUNS:
            for name, used in sorted(s.capability.items()):
                emit(
                    f"hop:{hop}|capability|{task}|{name}", f"{hop} {name} usage ({task})",
                    f"{used}/{s.with_labels}",
                    f"{used} of {s.with_labels} {task} runs called {name}",
                )

    # routing facts: per-tier COMPLETION rate + median output tokens. run_result ==
    # success means the loop finished without error/stall — it is NOT graded
    # correctness (telemetry is content-free; correctness lives in the eval/bench
    # results and is paired with these facts at analysis time, never inferred
    # here). Comparison across tiers is the PROPOSER's job.
    for (hop, task, tier), t in sorted(by_tier.items()):
        if t.runs < MIN_RUNS:
            continue
        med = int(statistics.median(t.out_tokens)) if t.out_tokens else 0
        emit(
            f"hop:{hop}|route|{task}|{tier}", f"{hop} {tier} tier ({task})",
            f"{t.successes}/{t.runs} at {med} out-tokens",
            f"tier {tier}: {t.successes}/{t.runs} {task} runs completed without error/stall "
            f"(NOT graded correctness), median {med} output tokens; checks fired: "
            f"self_check {t.self_check_fired}/{t.runs}, anti_churn {t.anti_churn_fired}/{t.runs}, "
            f"agent ran {t.agent_ran}/{t.runs} (descriptive, x12 A8)",
        )

    # per-model facts under a tier: written only when a tier saw more than one model in the window,
    # so a tier-level rate that spans a catalog change is never read as one model's number
    models_per_tier: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for (hop, task, tier, model) in by_model:
        models_per_tier[(hop, task, tier)].add(model)
    for (hop, task, tier, model), t in sorted(by_model.items()):
        if len(models_per_tier[(hop, task, tier)]) < 2 or t.runs < MIN_RUNS:
            continue
        med = int(statistics.median(t.out_tokens)) if t.out_tokens else 0
        emit(
            f"hop:{hop}|route|{task}|{tier}|{model}", f"{hop} {tier} tier / {model} ({task})",
            f"{t.successes}/{t.runs} at {med} out-tokens",
            f"tier {tier} served by {model}: {t.successes}/{t.runs} {task} runs completed without "
            f"error/stall (NOT graded correctness), median {med} output tokens; the tier label "
            f"mixed models in this window, compare per model",
        )

    # regression facts: failure-class incidence per HOP version — the proposer
    # compares versions; a class appearing after vX is a rollback/golden candidate
    for (hop, version, fc), n in sorted(by_version_failure.items()):
        if n < MIN_REGRESSION:
            continue
        total = runs_by_version[(hop, version)]
        emit(
            f"hop:{hop}|failure|{version}|{fc}", f"{hop} v{version} {fc}",
            f"{n}/{total}",
            f"failure_class {fc}: {n} of {total} runs on {hop} v{version}",
        )

    return entities, edges


def distill_deviations(episodes: list[dict]) -> tuple[list[EntitySpec], list[EdgeSpec]]:
    """``episodes`` = deviation memory payloads (hop-v1.1 §9.2), shipped by the harness as
    ``source=harness_memory, action_type=deviation``: ``hop`` is the provenance stamp
    ``<name>@<version>``, ``kind`` one of the closed set, ``slug`` the memory id, ``run`` the
    telemetry run id when the agent knew it. One fact per (hop, kind), floor 1: a single
    deviation is already a case the builder can author; the count is the priority."""
    by_kind: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for p in episodes:
        hop = hop_base(str(p.get("hop") or "").split("@", 1)[0])
        kind = p.get("kind")
        if not hop or not kind:
            continue
        by_kind[(hop, str(kind))].append(p)
    entities: list[EntitySpec] = []
    edges: list[EdgeSpec] = []
    for (hop, kind), rows in sorted(by_kind.items()):
        slugs = sorted({str(r.get("slug") or "?") for r in rows})
        runs = sorted({str(r["run"]) for r in rows if r.get("run")})
        shown = ", ".join(slugs[:5]) + (f", +{len(slugs) - 5} more" if len(slugs) > 5 else "")
        e, g = _fact(
            f"hop:{hop}|deviation|{kind}", f"{hop} deviations ({kind})",
            f"{len(rows)} in window",
            f"{len(rows)} {kind} deviation(s) recorded by the agent: {shown}"
            + (f"; runs {', '.join(runs[:5])}" if runs else ""),
        )
        entities += e
        edges += g
    return entities, edges
