"""Harness telemetry ingestion — the memory side of the frozen D11.1 contract.

Validates and normalizes RunOutcomeEvents / TuningLedgerEntries emitted by the
harness's ``telemetryEmit`` seam (pending engine-side; fixtures in
tests/unit/test_harness_telemetry.py exercise this today). Events are content-free
BY CONSTRUCTION: ``normalize()`` builds the episode payload from an explicit
allowlist — there is no code path that copies free text into the event, so fleet
aggregation (HOP ladder rung 4) has nothing to leak.

Contract: docs/redesign/v4-memory-lakehouse.md §D11.1 (frozen 2026-08-24,
validated against engine source by the harness team).
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


class TelemetryContractError(ValueError):
    """The event violates the frozen D11.1 contract — rejected, never coerced."""


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise TelemetryContractError(msg)


def normalize_run_outcome(event: dict, *, user_id: str) -> EpisodeEnvelope:
    """RunOutcomeEvent → episode envelope. Allowlist projection; content-free."""
    action = event.get("action_type")
    _require(action in ACTION_TYPES, f"unknown action_type {action!r}")
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
        "self_check_fired": bool(p.get("self_check_fired", False)),
        "anti_churn_fired": bool(p.get("anti_churn_fired", False)),
        "tokens": {
            k: int(tokens.get(k, 0))
            for k in ("input", "output", "cache_read_input", "cache_creation_input")
        },
        "wall_s": int(p.get("wall_s", 0)),
        "turns": int(p.get("turns", 0)),
    }
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
