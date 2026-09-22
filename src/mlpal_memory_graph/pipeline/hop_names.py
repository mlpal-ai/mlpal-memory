"""memory v10: the two naming rules every tuning stage shares.

Until now the due counter matched the HOP name exactly (`infra`), the distiller took an explicit
alias list (`infra-ro=infra`), and the brief split on the first dash — three rules for one fact,
and the routines run as the read-only twin `infra-ro`. One rule here, used by all of them.

The telemetry contract gate had the same shape of drift: a literal tuple of accepted versions that
stopped at d11.5 while the engine emits d11.7, so every recent run was silently excluded.
"""

from __future__ import annotations

import re

_CONTRACT = re.compile(r"^d(\d+)\.(\d+)$")


def hop_base(name: str | None) -> str:
    """A variant's runs count toward its parent HOP: `infra-ro` → `infra`, `coding` → `coding`.
    Variants are `<hop>-<variant>` by convention (hop-v1.1 §4)."""
    return (name or "").split("-", 1)[0]


def contract_at_least(contract: str | None, floor: str = "d11.2") -> bool:
    """True when `contract` is a `dMAJOR.MINOR` at or above `floor`. d11.1 rows lack the fields the
    aggregates need (absent, never zero) and stay excluded; anything newer is accepted, since the
    contracts only add fields."""
    m, f = _CONTRACT.match(str(contract or "")), _CONTRACT.match(floor)
    if not m or not f:
        return False
    return (int(m.group(1)), int(m.group(2))) >= (int(f.group(1)), int(f.group(2)))
