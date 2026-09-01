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

import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from .extractor import EdgeSpec, EntitySpec

MIN_RUNS = 30          # power floor for waste/budget/routing facts
MIN_REGRESSION = 5     # occurrences before a failure-class fact exists


@dataclass
class RunStats:
    runs: int = 0
    successes: int = 0
    stalls: int = 0                       # failure_class == step_budget_stall
    observe_ran: int = 0
    observe_caught: int = 0               # observe ran and did NOT pass
    agent_ran: int = 0
    agent_caught: int = 0                 # verdict == FAIL
    out_tokens: list[int] = field(default_factory=list)


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
    by_version_failure: dict[tuple[str, str, str], int] = defaultdict(int)
    runs_by_version: dict[tuple[str, str], int] = defaultdict(int)

    for p in episodes:
        if p.get("contract") != "d11.2":
            continue  # D11.1: fields absent, never zero — excluded from aggregates
        hop = p["hop"]["name"]
        task = p.get("task_type") or "unknown"
        s = by_class[(hop, task)]
        s.runs += 1
        if p["run_result"] == "success":
            s.successes += 1
        if p.get("failure_class") == "step_budget_stall":
            s.stalls += 1
        checks = p.get("checks") or {}
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

        tier = p.get("tier")
        if tier:
            t = by_tier[(hop, task, tier)]
            t.runs += 1
            if p["run_result"] == "success":
                t.successes += 1
            t.out_tokens.append(int((p.get("tokens") or {}).get("output", 0)))

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

    # routing facts: per-tier resolve rate + median output tokens (comparison
    # across tiers is the PROPOSER's job; the distiller states each tier plainly)
    for (hop, task, tier), t in sorted(by_tier.items()):
        if t.runs < MIN_RUNS:
            continue
        med = int(statistics.median(t.out_tokens)) if t.out_tokens else 0
        emit(
            f"hop:{hop}|route|{task}|{tier}", f"{hop} {tier} tier ({task})",
            f"{t.successes}/{t.runs} at {med} out-tokens",
            f"tier {tier}: {t.successes}/{t.runs} {task} runs resolved, median {med} output tokens",
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
