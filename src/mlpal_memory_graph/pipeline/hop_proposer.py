"""HOP proposal generation — stage 3 of the optimizer loop (design §1.3).

Reads the distilled watched facts (pipeline/hop_distiller output, as CURRENT
MetricValue nodes) and emits bounded HOP diffs: knob changes only, each with a
rationale, a predicted effect, and memory:// citations to the exact facts it
rests on. DETERMINISTIC rules with explicit thresholds — the proposer is the
one place a model could be tempted in, and it is exactly where a
plausible-but-wrong rationale would cost the most (x3 finding 2). Rules first;
a model may later RANK proposals, never author them.

Citation discipline (x5, applied to the optimizer itself): every proposal cites
the node ids it used; the CLI verifies each cited id resolves before emitting.
"Memory proposes, evals dispose": nothing here touches a served HOP.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STALL_RATE_MIN = 0.20      # >=20% step-budget stalls on a class -> budget proposal
ROUTE_MARGIN_PP = 5.0      # cheaper tier within 5pp of best tier -> routing proposal
REGRESSION_RATE_MIN = 0.10 # >=10% of a version's runs in one failure class -> flag
DEFAULT_TIER_ORDER = ("cheap", "balanced", "frontier")  # cheapest first

_FRACTION = re.compile(r"^(\d+)/(\d+)")


@dataclass
class Fact:
    node_id: str
    key: str          # e.g. hop:coding|route|bugfix|cheap
    value: str        # e.g. "28/30 at 100 out-tokens"

    @property
    def parts(self) -> list[str]:
        return self.key.split("|")

    @property
    def hop(self) -> str:
        return self.parts[0].removeprefix("hop:")

    @property
    def fraction(self) -> tuple[int, int] | None:
        m = _FRACTION.match(self.value)
        return (int(m.group(1)), int(m.group(2))) if m else None

    @property
    def rate(self) -> float | None:
        f = self.fraction
        return (f[0] / f[1]) if f and f[1] else None

    @property
    def citation(self) -> str:
        return f"memory://node/{self.node_id}"


@dataclass
class Proposal:
    hop: str
    kind: str                       # budget | waste | route | regression
    knob: str
    change: dict
    rationale: str
    predicted: str
    evidence: list[str] = field(default_factory=list)
    risk: str = ""

    def as_dict(self) -> dict:
        return {
            "hop": self.hop, "kind": self.kind, "knob": self.knob, "change": self.change,
            "rationale": self.rationale, "predicted": self.predicted,
            "risk": self.risk, "evidence": list(self.evidence),
        }


def propose(facts: list[Fact], tier_order: tuple[str, ...] = DEFAULT_TIER_ORDER) -> list[Proposal]:
    out: list[Proposal] = []
    by_kind: dict[str, list[Fact]] = {"stall": [], "waste-observe": [], "waste-agent": [],
                                      "route": [], "failure": []}
    for f in facts:
        p = f.parts
        if len(p) >= 2 and p[1] in by_kind:
            by_kind[p[1]].append(f)

    # budget: a class that stalls often needs a bigger budget or a bigger model
    for f in by_kind["stall"]:
        r = f.rate
        if r is None or r < STALL_RATE_MIN:
            continue
        task = f.parts[2]
        out.append(Proposal(
            hop=f.hop, kind="budget", knob=f"budgets.max_turns[{task}]",
            change={"op": "raise", "by": "50%"},
            rationale=f"{f.value} {task} runs ended in step_budget_stall ({r:.0%})",
            predicted="fewer stalls on this class; cost per run rises for the runs that needed it",
            risk="masks a routing problem if the class is capability-limited (x3 boundary)",
            evidence=[f.citation],
        ))

    # waste: a check that ran often and caught nothing is a candidate to skip
    for kind, knob in (("waste-observe", "verification.observe"), ("waste-agent", "verification.agent")):
        for f in by_kind[kind]:
            fr = f.fraction
            if not fr or fr[0] != 0:
                continue
            task = f.parts[2]
            out.append(Proposal(
                hop=f.hop, kind="waste", knob=knob,
                change={"op": "skip_for", "task_type": task},
                rationale=f"{knob} ran {fr[1]} times on {task}, caught 0 failures",
                predicted="lower tokens/wall per run on this class; no measured correctness loss",
                risk="the check may be catching nothing because the class is easy TODAY; "
                     "golden suite must include this class",
                evidence=[f.citation],
            ))

    # routing: per (hop, task), if a cheaper tier resolves within the margin of the
    # best tier, route the class down. Requires both tiers in tier_order; else silence.
    by_task: dict[tuple[str, str], dict[str, Fact]] = {}
    for f in by_kind["route"]:
        if len(f.parts) < 4:
            continue
        by_task.setdefault((f.hop, f.parts[2]), {})[f.parts[3]] = f
    rank = {t: i for i, t in enumerate(tier_order)}
    for (hop, task), tiers in by_task.items():
        known = [t for t in tiers if t in rank and tiers[t].rate is not None]
        if len(known) < 2:
            continue
        best = max(known, key=lambda t: tiers[t].rate)
        for t in sorted(known, key=lambda t: rank[t]):
            if rank[t] >= rank[best]:
                break
            gap_pp = (tiers[best].rate - tiers[t].rate) * 100
            if gap_pp <= ROUTE_MARGIN_PP:
                out.append(Proposal(
                    hop=hop, kind="route", knob=f"routing.tier[{task}]",
                    change={"from": best, "to": t},
                    rationale=(f"tier {t} resolves {task} at {tiers[t].value} vs "
                               f"{best} at {tiers[best].value} ({gap_pp:.1f}pp gap)"),
                    predicted="same resolve rate at the cheaper tier's price",
                    risk="resolve-rate parity measured on the observed mix only; "
                         "frontier golden must stay green",
                    evidence=[tiers[t].citation, tiers[best].citation],
                ))
                break  # cheapest tier within margin wins; one proposal per class

    # regression: a failure class concentrated on one version is a rollback /
    # golden candidate — advisory, never auto-applied
    for f in by_kind["failure"]:
        r = f.rate
        if r is None or r < REGRESSION_RATE_MIN or len(f.parts) < 4:
            continue
        version, fc = f.parts[2], f.parts[3]
        out.append(Proposal(
            hop=f.hop, kind="regression", knob="version",
            change={"op": "investigate_or_rollback", "version": version, "failure_class": fc},
            rationale=f"{fc} in {f.value} of runs on v{version} ({r:.0%})",
            predicted="rollback removes the failure class if it is version-caused",
            risk="may be environment-caused, not version-caused; compare prior version's rate",
            evidence=[f.citation],
        ))
    return out
