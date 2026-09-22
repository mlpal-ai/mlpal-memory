"""Half-lives per kind (memory v6 §2, §7; decision PLAN.md second pass 3): platform defaults per
kind, a per-topic override the host stamps on the claim (`half_life`), never the agent's choice.
Used for ranking decay (packets) and nowhere on the write path. State and preference do not decay by
clock: one current value per key, staleness is judged against the key's own cadence (report)."""

from __future__ import annotations

import math
import re
from datetime import datetime

# days; None = no clock decay
KIND_HALF_LIFE_DAYS: dict[str, float | None] = {
    "state": None,
    "preference": None,
    "learning": 365.0,
    "record": 365.0,
    "deviation": 90.0,
}
DEFAULT_HALF_LIFE_DAYS = 180.0  # untyped facts: the v3 packet default
DECAY_FLOOR = 0.35  # old-but-relevant knowledge never decays to invisibility

_UNITS = {"h": 1 / 24, "d": 1.0, "w": 7.0, "m": 30.0, "y": 365.0}


def parse_half_life(value) -> float | None | str:
    """'90d' -> 90.0; '1y' -> 365.0; 'none' -> None; '2x' -> '2x' (cadence multiple, resolved by the
    report against the key's observed cadence). Unknown text -> None (no decay, logged upstream)."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip().lower()
    if s in ("", "none", "never"):
        return None
    if re.fullmatch(r"\d+(\.\d+)?x", s):
        return s
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([hdwmy])", s)
    return float(m.group(1)) * _UNITS[m.group(2)] if m else None


def half_life_days_for(props: dict | None) -> float | None:
    """The clock half-life that applies to a node: its stamped override, else its kind's default,
    else the untyped default. Cadence multiples ('2x') are not clock decay: no decay in ranking."""
    p = props or {}
    if "half_life" in p:
        hl = parse_half_life(p.get("half_life"))
        return None if isinstance(hl, str) else hl
    kind = p.get("kind")
    if kind in KIND_HALF_LIFE_DAYS:
        return KIND_HALF_LIFE_DAYS[kind]
    return DEFAULT_HALF_LIFE_DAYS


def decay(props: dict | None, stamp: datetime | None, now: datetime) -> float:
    """exp2(-age / half_life), floored; 1.0 when the kind has no clock half-life or no timestamp."""
    hl = half_life_days_for(props)
    if hl is None or stamp is None:
        return 1.0
    age_days = max(0.0, (now - stamp).total_seconds() / 86400.0)
    return max(DECAY_FLOOR, math.pow(2.0, -age_days / hl))
