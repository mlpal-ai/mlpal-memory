"""Bi-temporal interval helpers.

Validity windows are half-open ``[valid_at, invalid_at)`` with ``invalid_at = None`` meaning
"+infinity" (still true). Touching endpoints do **not** overlap (adjacent ≠ contradiction).
Used by the contradiction/supersession logic in the graph driver. See
``memory-ontology-primer/03-memory-operations.md`` §6/§8.
"""

from __future__ import annotations

from datetime import datetime


def windows_overlap(
    a_valid: datetime,
    a_invalid: datetime | None,
    b_valid: datetime,
    b_invalid: datetime | None,
) -> bool:
    """True if the half-open validity windows [a_valid, a_invalid) and [b_valid, b_invalid)
    overlap. ``None`` end = open-ended (+inf). Adjacent (touching) windows are disjoint."""
    # disjoint if one window ends at or before the other begins (None end = +inf, never ends)
    if a_invalid is not None and a_invalid <= b_valid:
        return False
    if b_invalid is not None and b_invalid <= a_valid:
        return False
    return True
