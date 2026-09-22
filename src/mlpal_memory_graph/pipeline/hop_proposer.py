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
BYPASS_RATE_MIN = 0.50     # memory v10: >=50% of injected runs re-read memory -> the prompt bypasses its projection
SILENT_RATE_MIN = 0.20     # memory v10: >=20% of runs finish with no tool call -> the prompt answers from nothing
CAPABILITY_UNUSED_MAX = 0.02  # memory v10: a provider touched by <=2% of runs is a module to switch off (advisory)
FIRED_RATE_HIGH = 0.50     # an intervention check firing in >=50% of runs -> loosen its knob
FIRED_RATE_LOW = 0.05      # firing in <=5% of runs -> tighten (it is not doing work)
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
    kind: str                       # budget | waste | route | regression | verification | eval | memory | capability
    knob: str                       # the REAL hop.yaml dot-path (spec §3), or "" if none exists
    change: dict
    rationale: str
    predicted: str
    evidence: list[str] = field(default_factory=list)
    risk: str = ""
    # applicability against the artifact's declared surface (filled by classify()):
    # enactable | blocked_locked | not_tunable | no_declared_knob | advisory
    applicability: str = "unclassified"
    applicability_note: str = ""

    def as_dict(self) -> dict:
        return {
            "hop": self.hop, "kind": self.kind, "knob": self.knob, "change": self.change,
            "rationale": self.rationale, "predicted": self.predicted,
            "risk": self.risk, "evidence": list(self.evidence),
            "applicability": self.applicability, "applicability_note": self.applicability_note,
        }


def classify(proposals: list[Proposal], tunable: dict[str, tuple[float, float]] | None,
             locked: set[str] | None) -> list[Proposal]:
    """Mark each proposal against the artifact's declared surface. ``tunable``
    maps dot-path -> (min, max) (the loader's EFFECTIVE list incl. inheritance);
    ``locked`` is the dot-path set. The tuner never bends a lock and never
    invents a knob: "no applicable knob" is a first-class, publishable outcome.
    With no surface given, everything stays 'unclassified'."""
    if tunable is None and locked is None:
        return proposals
    tunable = tunable or {}
    locked = locked or set()
    for p in proposals:
        if p.kind == "regression":
            p.applicability, p.applicability_note = "advisory", "rollback/golden candidate; not a knob change"
            continue
        if p.kind == "eval":
            p.applicability, p.applicability_note = "advisory", "eval candidate from a deviation memory; the builder authors the case (feeds: evals)"
            continue
        if p.kind == "memory":
            p.applicability, p.applicability_note = "advisory", "a prompt change; the owner edits the prompt, no knob"
            continue
        if p.kind == "capability" and p.knob not in tunable:
            p.applicability, p.applicability_note = "advisory", f"{p.knob} is not declared tunable: the owner switches the module off by hand"
            continue
        if not p.knob:
            p.applicability, p.applicability_note = "no_declared_knob", "no hop.yaml field expresses this change"
            continue
        if p.knob in locked:
            p.applicability, p.applicability_note = "blocked_locked", f"{p.knob} is locked on this artifact"
            continue
        if p.knob not in tunable:
            p.applicability, p.applicability_note = "not_tunable", f"{p.knob} exists but is not declared tunable"
            continue
        rng = tunable[p.knob]
        if isinstance(rng, (set, frozenset)):  # enum-set range (hop-v1.1 §6): categorical knobs
            target = p.change.get("to")
            if target in rng:
                p.applicability, p.applicability_note = "enactable", f"member of declared set {sorted(rng)}"
            else:
                p.applicability, p.applicability_note = "not_tunable", f"target {target!r} not in declared set {sorted(rng)}"
            continue
        lo, hi = rng
        target = p.change.get("to")
        if isinstance(target, (int, float)) and not (lo <= target <= hi):
            p.applicability, p.applicability_note = "not_tunable", f"target {target} outside declared range [{lo}, {hi}]"
            continue
        p.applicability, p.applicability_note = "enactable", f"within declared range [{lo}, {hi}]"
    return proposals


def propose(facts: list[Fact], tier_order: tuple[str, ...] = DEFAULT_TIER_ORDER) -> list[Proposal]:
    out: list[Proposal] = []
    by_kind: dict[str, list[Fact]] = {"deviation": [], "stall": [], "waste-observe": [], "waste-agent": [],
                                      "route": [], "failure": [],
                                      "fired-self-check": [], "fired-anti-churn": [],
                                      "memory-bypass": [], "silent": [], "capability": []}
    for f in facts:
        p = f.parts
        if len(p) >= 2 and p[1] in by_kind:
            by_kind[p[1]].append(f)

    # memory v10: prompt facts from d11.6/7 — no hop.yaml knob expresses them, so they are advisory
    # proposals the owner reads (a prompt edit is a build decision, never an automatic one)
    for f in by_kind["memory-bypass"]:
        r = f.rate
        if r is None or r < BYPASS_RATE_MIN:
            continue
        task = f.parts[2]
        out.append(Proposal(
            hop=f.hop, kind="memory", knob="",
            change={"op": "prompt", "note": "the prompt re-reads state that the projection already injected: drop the read step, or inject the topic it reads (`inject: true`), or raise the projection budget"},
            rationale=f"{f.value} {task} runs that started with memory injected still called a memory read tool ({r:.0%})",
            predicted="fewer tool calls and tokens per run on this class; the same answers",
            risk="a read that checks freshness on purpose looks the same in telemetry; confirm on the prompt before removing it",
            evidence=[f.citation],
        ))
    for f in by_kind["silent"]:
        r = f.rate
        if r is None or r < SILENT_RATE_MIN:
            continue
        task = f.parts[2]
        out.append(Proposal(
            hop=f.hop, kind="memory", knob="",
            change={"op": "prompt", "note": "runs complete with no tool call: either memory answered (fine, then say so in the report) or the prompt let the model answer from nothing (add a required probe)"},
            rationale=f"{f.value} {task} runs completed with no tool call ({r:.0%})",
            predicted="answers grounded in a read or in an injected memory, never in the model alone",
            risk="a class that legitimately answers from injected state will look silent; pair with the memory-bypass fact",
            evidence=[f.citation],
        ))

    for f in by_kind["capability"]:
        r = f.rate
        if r is None or r > CAPABILITY_UNUSED_MAX or len(f.parts) < 4:
            continue
        task, provider = f.parts[2], f.parts[3]
        out.append(Proposal(
            # memory v10: a capability module is a knob when the artifact declares it
            # (`modules.<provider>.enabled`, range [on, off]); otherwise the proposal is advisory
            hop=f.hop, kind="capability", knob=f"modules.{provider}.enabled",
            change={"op": "set", "to": "off", "provider": provider},
            rationale=f"{f.value} {task} runs called {provider} ({r:.0%}); the {provider} sections of the prompt, its tools and routes are carried but unused",
            predicted="a shorter, cheaper prompt and tool list; nothing changes for the providers in use",
            risk=f"a {provider} question after the switch is a deviation that proposes turning the module back on",
            evidence=[f.citation],
        ))

    # budget: a class that stalls often needs a bigger budget or a bigger model
    for f in by_kind["stall"]:
        r = f.rate
        if r is None or r < STALL_RATE_MIN:
            continue
        task = f.parts[2]
        out.append(Proposal(
            hop=f.hop, kind="budget", knob="budgets.maxTurns",
            change={"op": "raise", "by": "50%", "scope_note": f"class {task}"},
            rationale=f"{f.value} {task} runs ended in step_budget_stall ({r:.0%})",
            predicted="fewer stalls on this class; cost per run rises for the runs that needed it",
            risk="masks a routing problem if the class is capability-limited (x3 boundary)",
            evidence=[f.citation],
        ))

    # waste: a check that ran often and caught nothing is a candidate to skip
    # observe has no tunable knob in the spec (builtin:coding|none, categorical) ->
    # knob="" so classify() reports no_declared_knob honestly; the agent verifier's
    # size gate IS a declared knob: raise it toward its range max to skip small diffs
    for kind, knob, change in (
        ("waste-observe", "", {"op": "skip_for", "task_type": None}),
        ("waste-agent", "verification.agent.riskGateMinChangedLines", {"op": "raise_to_max"}),
    ):
        for f in by_kind[kind]:
            fr = f.fraction
            if not fr or fr[0] != 0:
                continue
            task = f.parts[2]
            change = {**change, **({"task_type": task} if "task_type" in change else {})}
            out.append(Proposal(
                hop=f.hop, kind="waste", knob=knob,
                change=change,
                rationale=f"{kind.removeprefix('waste-')} check ran {fr[1]} times on {task}, caught 0 failures",
                predicted="lower tokens/wall per run on this class; no measured correctness loss",
                risk="the check may be catching nothing because the class is easy TODAY; "
                     "golden suite must include this class",
                evidence=[f.citation],
            ))

    # routing: per (hop, task), if a cheaper tier COMPLETES within the margin of the
    # best tier, propose routing the class down. Completion (run_result) is not
    # graded correctness — the proposal says so and the golden suite is what
    # decides. Requires both tiers in tier_order; else silence.
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
        # tie-break toward the MOST EXPENSIVE tier: when a cheaper tier matches the
        # best rate exactly, the proposal must surface (x12: both tiers 32/32 and
        # the cheap tier was picked as "best", so the route finding vanished
        # instead of landing in the applicability table as no_declared_knob)
        best = max(known, key=lambda t: (tiers[t].rate, rank[t]))
        for t in sorted(known, key=lambda t: rank[t]):
            if rank[t] >= rank[best]:
                break
            gap_pp = (tiers[best].rate - tiers[t].rate) * 100
            if gap_pp <= ROUTE_MARGIN_PP:
                out.append(Proposal(
                    hop=hop, kind="route", knob="model.main",   # hop-v1.1 §8: the main-loop tier is a knob
                    change={"from": best, "to": t, "scope_note": f"class {task}"},
                    rationale=(f"tier {t} completes {task} at {tiers[t].value} vs "
                               f"{best} at {tiers[best].value} ({gap_pp:.1f}pp gap); "
                               "completion is not graded correctness"),
                    predicted="same completion rate at the cheaper tier's price; correctness unknown until evaluated",
                    risk="completion parity is NOT correctness parity (content-free telemetry); "
                         "the golden suite decides, never this fact",
                    evidence=[tiers[t].citation, tiers[best].citation],
                ))
                break  # cheapest tier within margin wins; one proposal per class

    # verification firing rates -> the knobs most artifacts declare tunable. High
    # firing = the check intervenes constantly (loosen); near-zero = it is not
    # doing work (tighten). Either way the change is a MEASURABLE hypothesis for
    # gen1, never a certainty — the rationale says which direction and why.
    for kind, knob, loosen, tighten in (
        ("fired-self-check", "verification.selfCheck.minEdits", "raise", "lower"),
        ("fired-anti-churn", "verification.antiChurn.threshold", "raise", "lower"),
    ):
        for f in by_kind[kind]:
            r = f.rate
            if r is None:
                continue
            task = f.parts[2]
            if r >= FIRED_RATE_HIGH:
                op, why = loosen, f"fires in {r:.0%} of {task} runs (>= {FIRED_RATE_HIGH:.0%}): constant intervention"
            elif r <= FIRED_RATE_LOW:
                op, why = tighten, f"fires in {r:.0%} of {task} runs (<= {FIRED_RATE_LOW:.0%}): not doing work"
            else:
                continue
            out.append(Proposal(
                hop=f.hop, kind="verification", knob=knob,
                change={"op": op, "step": 1},
                rationale=f"{knob}: {why} ({f.value})",
                predicted="fewer/more interventions on this class; effect on correctness is the gen1 question",
                risk="firing is observable, benefit is not (content-free telemetry) — golden must gate",
                evidence=[f.citation],
            ))

    # deviation (hop-v1.1 §9.2): what the agent recorded as "not to plan" is the eval backlog —
    # advisory, no knob expresses "write a case"; the builder authors it and a person reviews it
    for f in by_kind["deviation"]:
        if len(f.parts) < 3:
            continue
        kind = f.parts[2]
        count = int(f.value.split(" ", 1)[0]) if f.value[:1].isdigit() else 0
        if count < 1:
            continue
        out.append(Proposal(
            hop=f.hop, kind="eval", knob="",
            change={"op": "author_case", "deviation": kind, "count": count},
            rationale=f"{count} {kind} deviation(s) recorded by the agent in the window",
            predicted="a golden case that reproduces the deviation makes the fix measurable and keeps it fixed",
            risk="a deviation can be environment coverage (the eval's), not the HOP's; triage the layer before authoring",
            evidence=[f.citation],
        ))

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
