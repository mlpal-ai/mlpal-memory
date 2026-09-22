"""Tuning cadence: is a tune turn due for a HOP? (hop-v1.1 `tuning` block, R3 rule)

    T_review = min(T_env, T_model_release, T_max_age)

expressed over what telemetry can see: main-loop runs since the last scored turn
(`minRunsSinceLast`), days since the last scored turn (`maxAge`), and event
triggers the caller observed (a new model at the gateway, an incident, an API
change). Pure function; the tool wraps it with the episode queries.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta


@dataclass(frozen=True)
class CadencePolicy:
    min_runs_since_last: int = 30
    max_age_days: int = 28
    triggers: tuple[str, ...] = ("on-incident", "on-model-release", "on-api-change")


@dataclass
class CadenceVerdict:
    due: bool
    reasons: list[str] = field(default_factory=list)
    runs_since_last: int = 0
    days_since_last: float | None = None
    last_turn_at: datetime | None = None


def parse_max_age(text: str | int | None, default_days: int = 28) -> int:
    """`4w`, `14d`, `2w`, or a bare integer (days)."""
    if text is None:
        return default_days
    if isinstance(text, int):
        return text
    s = str(text).strip().lower()
    if s.endswith("w"):
        return int(s[:-1]) * 7
    if s.endswith("d"):
        return int(s[:-1])
    return int(s)


def decide_due(
    policy: CadencePolicy,
    *,
    runs_since_last: int,
    last_turn_at: datetime | None,
    now: datetime,
    events: tuple[str, ...] = (),
) -> CadenceVerdict:
    """A turn is due when any one clause holds. `events` are trigger names the caller
    observed since the last turn (e.g. `on-model-release`); only those the policy declares
    count. A HOP that has never been tuned is due on the run floor alone: the age clause
    needs a previous turn to age from."""
    v = CadenceVerdict(due=False, runs_since_last=runs_since_last, last_turn_at=last_turn_at)
    if runs_since_last >= policy.min_runs_since_last:
        v.reasons.append(f"{runs_since_last} main runs since the last turn (floor {policy.min_runs_since_last})")
    if last_turn_at is not None:
        age = now - last_turn_at
        v.days_since_last = round(age.total_seconds() / 86400, 1)
        if age >= timedelta(days=policy.max_age_days):
            v.reasons.append(f"last turn {v.days_since_last} days ago (maxAge {policy.max_age_days}d)")
    for e in events:
        if e in policy.triggers:
            v.reasons.append(f"trigger {e}")
    v.due = bool(v.reasons)
    return v
