"""HOP promotion gate — stage 5 of the optimizer loop (design §1.5, spec §6.2).

The engine's loader already enforces reference integrity and "promote:auto
needs a gating golden". What it cannot enforce — and what this module owns —
is the blast-radius decision: an APPLY-CAPABLE HOP may never self-promote,
whatever its tuning block says. v0 capability heuristic is deliberately
conservative and explicit: a HOP is apply-capable unless its permissions
restrict it to a read-only tool allowlist. Conservative means the failure
mode is "a human had to click", never "a mutating policy promoted itself".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# tools/rules that can change the world; matching any of these = apply-capable
_MUTATING = re.compile(
    r"^(Bash|Write|Edit|MultiEdit|NotebookEdit|Apply|kubectl|terraform|aws|gh|git|npm|pip|"
    r"docker|rm|mv|cp|chmod|curl|wget|psql)(\(|$)",
    re.I,
)
_READ_ONLY = re.compile(r"^(Read|Grep|Glob|LS|Search|WebFetch|WebSearch|memory_[a-z]+)(\(|$)", re.I)


@dataclass
class PromotionDecision:
    allowed: bool
    mode: str            # auto | human | refused
    reason: str

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "mode": self.mode, "reason": self.reason}


def is_apply_capable(hop: dict) -> bool:
    perms = hop.get("permissions") or {}
    allow = [str(r) for r in (perms.get("allow") or [])]
    include = [str(t) for t in ((hop.get("tools") or {}).get("include") or [])]
    if any(_MUTATING.match(r) for r in allow):
        return True
    if any(_MUTATING.match(t) for t in include):
        return True
    # read-only only when the allowlists are non-empty and every entry is read-only
    if allow and all(_READ_ONLY.match(r) for r in allow):
        return False
    if include and all(_READ_ONLY.match(t) for t in include):
        return False
    # empty allowlists = all tools (spec: [] = all) -> assume apply-capable
    return True


def decide_promotion(hop: dict, verdict: dict) -> PromotionDecision:
    if verdict.get("disposition") != "accepted":
        return PromotionDecision(False, "refused",
                                 f"verdict is {verdict.get('disposition')!r}: {verdict.get('reason')}")
    tuning = hop.get("tuning") or {}
    mode = str(tuning.get("promote", "human"))
    golden_name = tuning.get("goldenSuite")
    golden = next((s for s in (hop.get("evals") or []) if s.get("name") == golden_name), None)
    golden_gates = bool((golden or {}).get("gates", True)) if golden else False
    if mode == "auto":
        if not golden_gates:
            return PromotionDecision(True, "human",
                                     "promote:auto refused — golden suite does not gate; human promote")
        if is_apply_capable(hop):
            return PromotionDecision(
                True, "human",
                "promote:auto refused by the tuner — HOP is apply-capable (mutating tools "
                "reachable); apply-capable HOPs may not self-promote",
            )
        return PromotionDecision(True, "auto", "gates green, margin met, read-only HOP: auto-promote")
    return PromotionDecision(True, "human", "promote:human — a person merges the promotion")
