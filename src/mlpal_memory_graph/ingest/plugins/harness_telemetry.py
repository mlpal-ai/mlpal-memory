"""Harness telemetry ingestion — the memory side of the D11.x contracts.

Validates and normalizes RunOutcomeEvents / TuningLedgerEntries emitted by the
harness's ``telemetryEmit`` seam. Events are content-free BY CONSTRUCTION:
``normalize()`` builds the episode payload from an explicit allowlist — there is
no code path that copies free text into the event, so fleet aggregation (HOP
ladder rung 4) has nothing to leak.

Contracts (version-discriminated by the event's ``contract`` field):
- D11.1 (frozen 2026-08-24; no/other discriminator): legacy shape — wall_s,
  loose self_check/anti_churn bools, top-level verifier{}.
- D11.2 (landed 2026-09-01, engine mlpal-harness@0df42fb, spec mlpal-ai/hop@ace4cfc
  §6.3): adds explicit ``tier`` (OMITTED ≠ empty), ``failure_class`` (null IFF
  run_result == success; otherwise failure_class_vocab@v1 — emitters that cannot
  classify say "other", never a guessed bucket), full per-check ``checks{}``
  shape, and ``wall_ms`` (int ms; wall_s is REJECTED under d11.2 — silent unit
  drift inside a version is the failure class the bump exists to kill). The
  agent verdict lives at ``checks.agent.verdict``; the legacy ``verifier{}``
  block remains optional for emitters that persist findings to memory://.

D11.1 rows lack the new fields; the distiller reads them as ABSENT, never zero.
"""

from __future__ import annotations

from ...core.logging import get_logger
from ..envelope import Actor, EpisodeEnvelope

log = get_logger(__name__)

RUN_RESULTS = frozenset({"success", "error", "max_turns", "cancelled"})
FEEDBACK_OUTCOMES = frozenset({"accepted", "retried", "escalated", "failed"})
VERIFIER_VERDICTS = frozenset({"PASS", "FAIL", "PARTIAL"})
ACTION_TYPES = frozenset({"run.completed", "verifier.failed", "eval.scored"})
LEDGER_ACTIONS = frozenset({"hop.version_published", "hop.eval_scored"})
# failure_class_vocab@v1 (spec §6.3): versioned so growth is auditable. The v1
# emitter honestly populates a subset ({step_budget_stall, user_cancelled,
# gateway_error, other} + null); the vocab is frozen, only coverage widens.
FAILURE_CLASSES = frozenset({
    "empty_patch", "step_budget_stall", "test_timeout", "tool_error",
    "gateway_error", "verifier_reject", "user_cancelled", "other",
})


class TelemetryContractError(ValueError):
    """The event violates the frozen D11.1 contract — rejected, never coerced."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise TelemetryContractError(msg)


def _validate_d112_checks(checks: dict) -> dict:
    """Full per-check shape (D11.2): waste-fact distillation needs {ran, caught}
    per mechanism. The agent verdict lives HERE (checks.agent.verdict); yodex
    leaves the legacy verifier{} block unset to avoid double-emitting it."""
    _require(isinstance(checks, dict) and checks != {}, "d11.2 requires checks{}")
    for key in ("self_check", "anti_churn"):
        blk = checks.get(key)
        _require(isinstance(blk, dict) and isinstance(blk.get("fired"), bool),
                 f"checks.{key}.fired must be a bool")
    obs = checks.get("observe")
    _require(isinstance(obs, dict) and isinstance(obs.get("ran"), bool)
             and isinstance(obs.get("passed"), bool),
             "checks.observe requires {ran, passed} bools")
    agent = checks.get("agent")
    _require(isinstance(agent, dict), "checks.agent required")
    verdict = agent.get("verdict")
    _require(verdict is None or verdict in VERIFIER_VERDICTS,
             f"checks.agent.verdict must be null or one of {sorted(VERIFIER_VERDICTS)}")
    return {
        "self_check": {"fired": checks["self_check"]["fired"]},
        "anti_churn": {"fired": checks["anti_churn"]["fired"]},
        "observe": {"ran": obs["ran"], "passed": obs["passed"]},
        "agent": {"verdict": verdict},
    }


def normalize_run_outcome(event: dict, *, user_id: str) -> EpisodeEnvelope:
    """RunOutcomeEvent → episode envelope. Allowlist projection; content-free."""
    action = event.get("action_type")
    _require(action in ACTION_TYPES, f"unknown action_type {action!r}")
    is_d112 = event.get("contract") == "d11.2"
    p = event.get("payload") or {}
    hop = p.get("hop") or {}
    _require(bool(hop.get("name")) and bool(hop.get("version")), "hop {name, version} required")
    _require(p.get("run_result") in RUN_RESULTS, f"run_result must be one of {sorted(RUN_RESULTS)}")
    fo = p.get("feedback_outcome")
    _require(fo is None or fo in FEEDBACK_OUTCOMES, f"bad feedback_outcome {fo!r}")
    verifier = p.get("verifier") or {}
    if verifier:
        _require(verifier.get("verdict") in VERIFIER_VERDICTS, "verifier.verdict invalid")
        fr = verifier.get("findings_ref")
        _require(
            fr is None or str(fr).startswith("memory://"),
            "verifier.findings_ref must be a memory:// pointer (content-free events)",
        )
    tokens = p.get("tokens") or {}
    repo = event.get("scope_id") or p.get("repo")
    _require(bool(repo), "repo (scope_id) required")

    payload = {  # explicit allowlist — nothing else crosses
        "hop": {"name": str(hop["name"]), "version": str(hop["version"])},
        "model": str(p.get("model", "")),
        "task_type": str(p.get("task_type", "")),
        "run_result": p["run_result"],
        **({"feedback_outcome": fo} if fo else {}),
        **(
            {
                "verifier": {
                    "verdict": verifier["verdict"],
                    **(
                        {"findings_ref": str(verifier["findings_ref"])}
                        if verifier.get("findings_ref")
                        else {}
                    ),
                }
            }
            if verifier
            else {}
        ),
        "tokens": {
            k: int(tokens.get(k, 0))
            for k in ("input", "output", "cache_read_input", "cache_creation_input")
        },
        "turns": int(p.get("turns", 0)),
    }

    if is_d112:
        payload["contract"] = "d11.2"
        # wall_ms replaces wall_s; a d11.2 event carrying wall_s is REJECTED —
        # accepting both units under one version is silent drift.
        _require("wall_s" not in p, "d11.2 uses wall_ms; wall_s present")
        _require(isinstance(p.get("wall_ms"), int), "d11.2 requires int wall_ms")
        payload["wall_ms"] = p["wall_ms"]
        # failure_class: PRESENT always; null IFF success (frozen invariant).
        _require("failure_class" in p, "d11.2 requires failure_class (null on success)")
        fc = p["failure_class"]
        if p["run_result"] == "success":
            _require(fc is None, "failure_class must be null on success")
        else:
            _require(fc in FAILURE_CLASSES,
                     f"failure_class must be from failure_class_vocab@v1, got {fc!r}")
        payload["failure_class"] = fc
        payload["checks"] = _validate_d112_checks(p.get("checks") or {})
        tier = p.get("tier")
        if tier is not None:  # OMITTED ≠ empty — absent means host couldn't resolve
            _require(isinstance(tier, str) and tier != "", "tier must be a non-empty string")
            payload["tier"] = tier
    else:
        payload["self_check_fired"] = bool(p.get("self_check_fired", False))
        payload["anti_churn_fired"] = bool(p.get("anti_churn_fired", False))
        payload["wall_s"] = int(p.get("wall_s", 0))
    env = EpisodeEnvelope(
        scope="repo",
        scope_id=str(repo),
        workspace=str(repo),
        actor=Actor(user_id=user_id),
        source="harness_telemetry",
        action_type=event["action_type"],
        payload=payload,
        content=None,  # content-free by construction
        source_ref=str(event.get("source_ref") or ""),
    )
    if event.get("event_id"):
        env.event_id = str(event["event_id"])
    if event.get("occurred_at"):
        env.occurred_at = event["occurred_at"]
    return env


def normalize_ledger_entry(event: dict, *, user_id: str) -> EpisodeEnvelope:
    """TuningLedgerEntry → episode envelope (bitemporal HOP version lineage)."""
    action = event.get("action_type")
    _require(action in LEDGER_ACTIONS, f"unknown ledger action {action!r}")
    p = event.get("payload") or {}
    hop = p.get("hop") or {}
    _require(bool(hop.get("name")), "hop.name required")
    _require(p.get("decision") in ("adopted", "rejected", None), "bad decision")
    ev = p.get("eval") or {}
    payload = {
        "hop": {"name": str(hop["name"])},
        **({"from_version": str(p["from_version"])} if p.get("from_version") else {}),
        **({"to_version": str(p["to_version"])} if p.get("to_version") else {}),
        "diff_paths": [str(x) for x in (p.get("diff_paths") or [])],
        **(
            {
                "eval": {
                    "suite_digest": str(ev.get("suite_digest", "")),
                    "score": float(ev.get("score", 0.0)),
                    "pass_bar": float(ev.get("pass_bar", 0.0)),
                    "runs": int(ev.get("runs", 0)),
                    "eval_run_id": str(ev.get("eval_run_id", "")),
                }
            }
            if ev
            else {}
        ),
        **({"decision": p["decision"]} if p.get("decision") else {}),
        **({"proposed_by": str(p["proposed_by"])} if p.get("proposed_by") else {}),
    }
    env = EpisodeEnvelope(
        scope="org",
        actor=Actor(user_id=user_id),
        source="harness_telemetry",
        action_type=event["action_type"],
        payload=payload,
        content=None,
    )
    if event.get("event_id"):
        env.event_id = str(event["event_id"])
    if event.get("occurred_at"):
        env.occurred_at = event["occurred_at"]
    return env
