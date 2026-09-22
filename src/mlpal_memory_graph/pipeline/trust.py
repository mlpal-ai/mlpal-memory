"""Trust by consequence (memory v6 DESIGN §6). A memory is a hypothesis on probation; it earns a tier
from five cheap signals, none on the read path:

    grounding       evidence ids on the claim (props.grounded, stamped at fold)
    corroboration   observed_count on the node (re-observation bumps it at fold)
    consequence     runs that served or injected the memory × the run's verdict (this module)
    endorsement     a person marked it (props.endorsed_by)
    survival        the fold closes or contradicts it; a closed value is never "current" again

Tiers: probation → corroborated → endorsed. Computed nightly (or on demand) by ``hop_trust``; written
to ``node.props["trust"]``; the packet shows it as a label. Pure functions here, so the rule is
testable without a database.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime

PASS_RESULTS = {"success"}
FAIL_RESULTS = {"error", "max_turns", "cancelled"}     # the D11 vocabulary; needs_approval is an edge stop, neutral
CORROBORATED_OBSERVATIONS = 2      # seen twice, by any writer
CORROBORATED_PASSES = 3            # or: three runs used it and passed, none failed


@dataclass
class Consequence:
    passes: int = 0
    fails: int = 0
    runs: set[str] = field(default_factory=set)


def join_consequences(served: dict[str, set[str]], verdicts: dict[str, str]) -> dict[str, Consequence]:
    """``served``: run_id → node ids that run saw (served by a read, or injected at session start).
    ``verdicts``: run_id → run_result. Runs without a verdict yet are ignored, never counted."""
    out: dict[str, Consequence] = defaultdict(Consequence)
    for run_id, node_ids in served.items():
        v = verdicts.get(run_id)
        if v is None:
            continue
        for nid in node_ids:
            c = out[nid]
            c.runs.add(run_id)
            if v in PASS_RESULTS:
                c.passes += 1
            elif v in FAIL_RESULTS:
                c.fails += 1
    return out


def tier_for(*, grounded: bool | None, observed_count: int, consequence: Consequence | None, endorsed: bool) -> str:
    """``grounded`` is None when the node predates the stamp (extractor facts carry their span instead);
    an explicitly ungrounded claim (a legacy Memorize mirror with no evidence) never leaves probation on
    its own: grounding is the floor of the ladder, and only a person's endorsement steps over it."""
    if endorsed:
        return "endorsed"
    if grounded is False:
        return "probation"
    c = consequence or Consequence()
    if observed_count >= CORROBORATED_OBSERVATIONS and c.fails == 0:
        return "corroborated"
    if c.passes >= CORROBORATED_PASSES and c.fails == 0:
        return "corroborated"
    return "probation"


def survival(last_seen: datetime | None, half_life_days: float | None, now: datetime) -> dict | None:
    """memory v7 WP3, the fifth signal made explicit: time since the memory was last observed against
    its kind's half-life. Ranking already decays by it (packets); here it is recorded so the report
    and a reader can see that a corroborated learning has not been seen for two half-lives."""
    if last_seen is None:
        return None
    ls = last_seen if last_seen.tzinfo else last_seen.replace(tzinfo=UTC)
    age = max(0.0, (now - ls).total_seconds() / 86400)
    out = {"age_days": round(age, 2), "half_life_days": half_life_days}
    if half_life_days:
        out["half_lives"] = round(age / half_life_days, 2)
        out["decayed"] = age > half_life_days
    return out


def trust_record(*, grounded: bool | None, observed_count: int, consequence: Consequence | None, endorsed: bool,
                 now: datetime | None = None, last_seen: datetime | None = None, half_life_days: float | None = None) -> dict:
    c = consequence or Consequence()
    at = now or datetime.now(UTC)
    surv = survival(last_seen, half_life_days, at)
    return {
        "tier": tier_for(grounded=grounded, observed_count=observed_count, consequence=c, endorsed=endorsed),
        "grounded": grounded,
        "observed": int(observed_count or 1),
        "passes": c.passes,
        "fails": c.fails,
        "runs": len(c.runs),
        **({"survival": surv} if surv else {}),
        "computed_at": at.isoformat(),
    }
