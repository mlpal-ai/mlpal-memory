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

- D11.3 (landed 2026-09-04, hop-v1.1 §10.1): D11.2 plus the PARKED outcome — ``run_result``
  gains ``needs_approval`` (failure_class MUST be ``approval_pending``) and the vocab becomes
  failure_class_vocab@v2 (+ policy_denied, approval_declined, preflight_failed). D11.2 stays
  frozen: a parked run under D11.2 is emitted as ``cancelled`` and the run-dir artifact
  (hop-run-result-v1) is the source of truth; the distiller never reads that as a park.

D11.1 rows lack the new fields; the distiller reads them as ABSENT, never zero.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ...core.logging import get_logger
from ..envelope import Actor, EpisodeEnvelope

log = get_logger(__name__)

RUN_RESULTS = frozenset({"success", "error", "max_turns", "cancelled"})
# D11.3 adds the PARKED terminal outcome: the run stopped at the safety envelope edge
# (hop-v1.1 §10.1 hop-run-result-v1 status needs_approval). Under D11.2 a parked run is
# emitted as `cancelled` (least-wrong, agreed 2026-09-03) and the run-dir artifact is truth.
RUN_RESULTS_D113 = RUN_RESULTS | {"needs_approval"}
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
# failure_class_vocab@v2 (D11.3): the safety-envelope classes. approval_pending is the ONLY
# class allowed with run_result == needs_approval (and required by it); the other three are
# real failures at the gate. v1 stays frozen: a d11.2 event carrying a v2 class is rejected.
FAILURE_CLASSES_V2 = FAILURE_CLASSES | {
    "approval_pending", "policy_denied", "approval_declined", "preflight_failed",
}
CONTRACTS_WITH_CHECKS = frozenset({"d11.2", "d11.3", "d11.4", "d11.5", "d11.6", "d11.7"})
# d11.6 (2026-09-15) = d11.5 + memory_projection {fact_count, estimated_tokens, truncated};
# d11.7 (2026-09-17) = d11.6 + tool_calls {tool name: count} — content-free, so "answered without a
# live read" can be computed (memory v7 WP1). Tool names are low-cardinality identifiers.
CONTRACTS_D115_PLUS = frozenset({"d11.5", "d11.6", "d11.7"})
CONTRACTS_D116_PLUS = frozenset({"d11.6", "d11.7"})
TOOL_CALLS_MAX_TOOLS = 64
LABELS_MAX = 16  # d11.8 provider labels
# D11.4 (harness, 2026-09-04): role main|subagent + run_id required, parent_run_id on sub-agent
# runs, task_type from YODEX_HOP_TELEMETRY_TASK. A D11.3 event may be either role, so role is
# NEVER defaulted for d11.3: absent means unknown (the shipper may derive it from the run file
# and say so with role_source). D11.2 is treated the same: the sink has lived inside buildSession
# since 2026-09-01, so a d11.2 child session WOULD have emitted; the x12 rows are main only by
# inspection (64 files, one event each) and are stamped role_source "verified-single-event-per-run".
ROLES = frozenset({"main", "subagent"})


class TelemetryContractError(ValueError):
    """The event violates the frozen D11.1 contract — rejected, never coerced."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise TelemetryContractError(msg)


def _occurred_at(value) -> datetime:
    """Emitters send ISO-8601 (yodex: '...Z'); the ORM needs an aware datetime.
    A malformed timestamp is a contract violation, not something to guess."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise TelemetryContractError(f"occurred_at is not ISO-8601: {value!r}") from exc
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


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
    contract = event.get("contract")
    # memory v10: the gates are "at least this version" — the contracts only add fields, and a
    # literal tuple stopping at d11.5 silently excluded every d11.6/d11.7 run from the tuning loop
    from ...pipeline.hop_names import contract_at_least

    is_d112 = contract_at_least(contract, "d11.2")  # d11.2 shape; d11.3 = d11.2 + parked outcome
    is_d113 = contract_at_least(contract, "d11.3")  # parked outcome + vocab@v2
    is_d114 = contract_at_least(contract, "d11.4")  # d11.5 (2026-09-15) = d11.4 + memories_injected
    run_results = RUN_RESULTS_D113 if is_d113 else RUN_RESULTS
    failure_classes = FAILURE_CLASSES_V2 if is_d113 else FAILURE_CLASSES
    p = event.get("payload") or {}
    hop = p.get("hop") or {}
    _require(bool(hop.get("name")) and bool(hop.get("version")), "hop {name, version} required")
    _require(p.get("run_result") in run_results, f"run_result must be one of {sorted(run_results)}")
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
    # role: main loop vs sub-agent run (verifier, delegated reads). Sub-agents emit their own
    # run.completed under the same hop; without this the distiller counts each HOP run 4×.
    role = p.get("role")
    if is_d114:
        _require(role in ROLES, "d11.4 requires role main|subagent")
        _require(bool(p.get("run_id")), "d11.4 requires run_id")
        payload["run_id"] = str(p["run_id"])
    elif role is not None:
        _require(role in ROLES, "role must be main or subagent")
    if role is not None:
        payload["role"] = role
        if p.get("role_source"):
            payload["role_source"] = str(p["role_source"])
    if p.get("parent_run_id"):
        payload["parent_run_id"] = str(p["parent_run_id"])
    if contract_at_least(contract, "d11.5") and "memories_injected" in p:
        mi = p["memories_injected"]
        _require(isinstance(mi, list) and all(isinstance(x, str) for x in mi), "d11.5 memories_injected must be a list of ids")
        payload["memories_injected"] = list(mi)   # the local topics rendered into the prompt (event ids)
    if contract_at_least(contract, "d11.6") and "memory_projection" in p:
        mp = p["memory_projection"]
        _require(isinstance(mp, dict) and isinstance(mp.get("fact_count"), int) and isinstance(mp.get("estimated_tokens"), int)
                 and isinstance(mp.get("truncated"), bool), "d11.6 memory_projection must be {fact_count int, estimated_tokens int, truncated bool}")
        payload["memory_projection"] = {"fact_count": mp["fact_count"], "estimated_tokens": mp["estimated_tokens"], "truncated": mp["truncated"]}
    if contract_at_least(contract, "d11.7") and "tool_calls" in p:
        tc = p["tool_calls"]
        _require(isinstance(tc, dict) and len(tc) <= TOOL_CALLS_MAX_TOOLS
                 and all(isinstance(k, str) and k and isinstance(v, int) and v >= 0 for k, v in tc.items()),
                 f"d11.7 tool_calls must map ≤{TOOL_CALLS_MAX_TOOLS} tool names to non-negative int counts")
        payload["tool_calls"] = {str(k)[:120]: int(v) for k, v in tc.items()}
    if contract_at_least(contract, "d11.8") and "labels" in p:
        # d11.8 (memory v10): provider CLI → count of Bash calls that started with it; a label, never a command
        lb = p["labels"]
        _require(isinstance(lb, dict) and len(lb) <= LABELS_MAX
                 and all(isinstance(k, str) and k and isinstance(v, int) and v >= 0 for k, v in lb.items()),
                 f"d11.8 labels must map ≤{LABELS_MAX} provider names to non-negative int counts")
        payload["labels"] = {str(k)[:40]: int(v) for k, v in lb.items()}

    if is_d112:
        payload["contract"] = contract
        # wall_ms replaces wall_s; a d11.2 event carrying wall_s is REJECTED —
        # accepting both units under one version is silent drift.
        _require("wall_s" not in p, f"{contract} uses wall_ms; wall_s present")
        _require(isinstance(p.get("wall_ms"), int), f"{contract} requires int wall_ms")
        payload["wall_ms"] = p["wall_ms"]
        # failure_class: PRESENT always; null IFF success (frozen invariant).
        _require("failure_class" in p, f"{contract} requires failure_class (null on success)")
        fc = p["failure_class"]
        vocab = "v2" if is_d113 else "v1"
        if p["run_result"] == "success":
            _require(fc is None, "failure_class must be null on success")
        elif p["run_result"] == "needs_approval":
            _require(fc == "approval_pending",
                     "run_result needs_approval requires failure_class approval_pending")
        else:
            _require(fc in failure_classes,
                     f"failure_class must be from failure_class_vocab@{vocab}, got {fc!r}")
            _require(fc != "approval_pending",
                     "approval_pending is only valid with run_result needs_approval")
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
        env.occurred_at = _occurred_at(event["occurred_at"])
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
        env.occurred_at = _occurred_at(event["occurred_at"])
    return env
