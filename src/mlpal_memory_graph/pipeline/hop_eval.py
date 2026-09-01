"""HOP eval ladder — stage 4 of the optimizer loop (design §2, spec §6.1).

Runs a candidate HOP's declared eval suites in ladder order — probes, then
goldens, then frontier — against the spec's role/gates semantics:

- gating suites (golden, probe; gates defaults True by role) are pass/fail:
  each run's scorer must exit 0; the suite passes iff pass_rate >= passBar.
  The FIRST gating failure rejects the candidate and stops the ladder (no
  money spent on frontier for a broken candidate).
- the frontier suite (gates False) is SCORED: the scorer prints the tuned
  number; the last numeric token of stdout is the score, averaged over runs.
  With a baseline verdict, the promotionMargin ("-5% at p<.05") is checked by
  its sign: "-5%" means candidate <= 0.95 x baseline (lower-is-better);
  "+5%" means >= 1.05 x baseline. Significance is NOT tested at runs=3 — the
  verdict says so instead of pretending.
- an LLM-judged suite must carry gates:false; it is scored and reported, never
  gates (the deterministic-gates-only rule, honored not re-derived).

Pure functions here; subprocess execution is injected so tests are hermetic.
"""

from __future__ import annotations

import hashlib
import re
import statistics
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import yaml

GATES_BY_ROLE = {"golden": True, "probe": True, "frontier": False}
_NUM = re.compile(r"-?\d+(?:\.\d+)?")
_MARGIN = re.compile(r"^\s*([+-])\s*(\d+(?:\.\d+)?)\s*%")


@dataclass
class SuiteResult:
    name: str
    role: str | None
    gates: bool
    runs: int
    pass_bar: float
    passes: int = 0
    pass_rate: float = 0.0
    scores: list[float] = field(default_factory=list)
    score: float | None = None
    passed: bool | None = None       # gating suites only
    skipped: bool = False

    def as_dict(self) -> dict:
        return {
            "name": self.name, "role": self.role, "gates": self.gates, "runs": self.runs,
            "pass_bar": self.pass_bar, "passes": self.passes, "pass_rate": self.pass_rate,
            "score": self.score, "passed": self.passed, "skipped": self.skipped,
        }


@dataclass
class Verdict:
    hop: str
    version: str
    suite_digest: str
    suites: list[SuiteResult]
    disposition: str                 # accepted | rejected | scored
    reason: str
    frontier: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "hop": self.hop, "version": self.version, "suite_digest": self.suite_digest,
            "disposition": self.disposition, "reason": self.reason,
            "frontier": self.frontier, "suites": [s.as_dict() for s in self.suites],
        }


Runner = Callable[[str, Path], tuple[int, str]]  # (scorer cmd, cwd) -> (returncode, stdout)


def shell_runner(cmd: str, cwd: Path) -> tuple[int, str]:
    r = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True, text=True, timeout=3600)
    return r.returncode, r.stdout


def load_hop(path: Path) -> tuple[dict, str]:
    raw = path.read_bytes()
    doc = yaml.safe_load(raw) or {}
    if doc.get("spec") != "mlpal/hop-v1":
        raise ValueError(f"{path}: spec must be mlpal/hop-v1 (unversioned artifacts are refused)")
    return doc, hashlib.sha256(raw).hexdigest()


def _suites_in_ladder_order(evals: list[dict]) -> list[dict]:
    order = {"probe": 0, "golden": 1, "frontier": 2, None: 3}
    return sorted(evals, key=lambda s: order.get(s.get("role"), 3))


def parse_margin(margin: str) -> tuple[str, float] | None:
    m = _MARGIN.match(margin or "")
    return (m.group(1), float(m.group(2))) if m else None


def meets_margin(candidate: float, baseline: float, margin: str) -> bool | None:
    pm = parse_margin(margin)
    if pm is None or baseline == 0:
        return None
    sign, pct = pm
    if sign == "-":
        return candidate <= baseline * (1 - pct / 100)
    return candidate >= baseline * (1 + pct / 100)


def run_ladder(
    hop_path: Path,
    *,
    runner: Runner = shell_runner,
    baseline_frontier: float | None = None,
) -> Verdict:
    doc, digest = load_hop(hop_path)
    hop_dir = hop_path.parent
    evals = doc.get("evals") or []
    tuning = doc.get("tuning") or {}
    results: list[SuiteResult] = []
    rejected_reason: str | None = None

    for suite in _suites_in_ladder_order(evals):
        role = suite.get("role")
        gates = bool(suite.get("gates", GATES_BY_ROLE.get(role, False)))
        res = SuiteResult(
            name=str(suite["name"]), role=role, gates=gates,
            runs=int(suite.get("runs", 1)), pass_bar=float(suite.get("passBar", 1.0)),
        )
        if rejected_reason is not None:
            res.skipped = True          # ladder stopped at an earlier gate
            results.append(res)
            continue
        cwd = hop_dir / str(suite.get("tasks", "."))
        for _ in range(res.runs):
            rc, out = runner(str(suite["scorer"]), cwd)
            if rc == 0:
                res.passes += 1
            nums = _NUM.findall(out or "")
            if nums:
                res.scores.append(float(nums[-1]))
        res.pass_rate = res.passes / res.runs if res.runs else 0.0
        if res.scores:
            res.score = statistics.fmean(res.scores)
        if gates:
            res.passed = res.pass_rate >= res.pass_bar
            if not res.passed:
                rejected_reason = (
                    f"{role or 'gating'} suite '{res.name}' failed: "
                    f"pass_rate {res.pass_rate:.2f} < passBar {res.pass_bar:.2f}"
                )
        results.append(res)

    frontier: dict = {}
    fm_name = tuning.get("frontierMetric")
    fm = next((r for r in results if r.name == fm_name), None)
    if fm is not None and not fm.skipped:
        frontier = {"suite": fm.name, "score": fm.score, "runs": fm.runs,
                    "significance": "not tested (runs too small for p<.05)"}
        if baseline_frontier is not None and fm.score is not None:
            frontier["baseline"] = baseline_frontier
            frontier["margin"] = tuning.get("promotionMargin")
            frontier["meets_margin"] = meets_margin(
                fm.score, baseline_frontier, str(tuning.get("promotionMargin", "")))

    if rejected_reason is not None:
        disposition, reason = "rejected", rejected_reason
    elif frontier.get("meets_margin") is True:
        disposition, reason = "accepted", "all gates green; frontier meets the preregistered margin"
    elif frontier.get("meets_margin") is False:
        disposition, reason = "rejected", "all gates green but frontier misses the preregistered margin"
    else:
        disposition = "scored"
        reason = "all gates green; no baseline given, so no promotion decision"
    return Verdict(
        hop=str(doc.get("name")), version=str(doc.get("version")), suite_digest=digest,
        suites=results, disposition=disposition, reason=reason, frontier=frontier,
    )
