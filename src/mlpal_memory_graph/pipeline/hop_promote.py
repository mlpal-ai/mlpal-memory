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

# Builtin tool capability manifest — dumped from the engine's defaultRegistry()
# by the harness session on 2026-09-01 (ground truth; changes only when a builtin
# is added). The authoritative apply-capable bit is readOnly == False, NOT
# edits||executes: Kill and WebFetch are readOnly:false with neither flag
# (process termination, network egress) and a HOP granting only those is still
# apply-capable. Custom/MCP/plugin tools register at session runtime and are
# invisible here -> unknown names resolve CONSERVATIVELY (apply-capable).
BUILTIN_READ_ONLY: dict[str, bool] = {
    "Bash": False, "BashOutput": True, "Edit": False, "ExitPlanMode": True,
    "Glob": True, "Grep": True, "Kill": False, "List": True, "Read": True,
    "WebFetch": False, "Write": False,
}
# memory's own MCP tools are read-only by contract (pinned by test in this repo)
BUILTIN_READ_ONLY.update({"memory_answer": True, "memory_search": True, "memory_get": True})

_TOOL_NAME = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_-]*)")


def _tool_name(rule: str) -> str:
    """'Bash(git*)' -> 'Bash'; 'Read' -> 'Read'."""
    m = _TOOL_NAME.match(rule)
    return m.group(1) if m else rule


def _is_read_only(name: str) -> bool | None:
    return BUILTIN_READ_ONLY.get(name)  # None = unknown (runtime-registered tool)


@dataclass
class PromotionDecision:
    allowed: bool
    mode: str            # auto | human | refused
    reason: str

    def as_dict(self) -> dict:
        return {"allowed": self.allowed, "mode": self.mode, "reason": self.reason}


def is_apply_capable(hop: dict) -> bool:
    """apply-capable <=> any referenced tool has readOnly == False, or any
    referenced name is unknown (runtime-registered), or tools.include is []
    (spec: [] = all tools, which includes Bash/Write). Read-only only when
    every referenced tool resolves to readOnly == True."""
    perms = hop.get("permissions") or {}
    include = [str(t) for t in ((hop.get("tools") or {}).get("include") or [])]
    allow = [str(r) for r in (perms.get("allow") or [])]
    if not include:
        return True  # [] = all tools
    names = {_tool_name(t) for t in include} | {_tool_name(r) for r in allow}
    if any(_tool_name(r) == "*" for r in allow):
        return True
    for n in names:
        ro = _is_read_only(n)
        if ro is None or ro is False:
            return True
    return False


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
