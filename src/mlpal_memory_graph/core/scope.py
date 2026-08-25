"""Memory scope — the axis every memory item is owned by.

The tenant boundary (``org_id``) and the *scope* are orthogonal:

* ``org_id`` is the hard multi-tenant boundary — never crossed (except GLOBAL).
* ``scope`` says which **subject** a fact belongs to. Memory is not strictly hierarchical
  by person: it compounds around any entity that accumulates knowledge over time — a user,
  a team, but equally a **repo**, a **service**, or an **agent**. ``GLOBAL`` and ``ORG`` are
  the shared backdrop; the rest are *subject* scopes a retrieval context activates and
  unions across ("I'm alice, in repo mlpal-backend, using agent X, in org Z").

A memory item is owned by exactly one :class:`ScopeRef` = (scope, scope_id).
Read-time *resolution* (union across active subjects, narrowest-wins on conflict,
deny-precedence on policy) lives in ``services/resolution.py``.

See ``docs/redesign/design-proposal.md`` §1. SESSION scope is deferred (§11).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Scope(StrEnum):
    """Memory subject kinds. GLOBAL/ORG are the backdrop; the rest are subject scopes."""

    GLOBAL = "global"  # platform-curated, cross-tenant (rare, admin-write-locked)
    ORG = "org"  # tenant-wide institutional memory
    TEAM = "team"  # one team within an org
    SERVICE = "service"  # one service/component (ownership, conventions, decisions)
    REPO = "repo"  # one code repository (its accumulated working knowledge)
    AGENT = "agent"  # one agent's accumulated operating memory
    USER = "user"  # one user's personal memory (private by default)

    @property
    def rank(self) -> int:
        """Narrowness rank (higher = narrower). Tie-breaks same-fact conflicts on read."""
        return _RANK[self]


# Single source of truth for ordering. GLOBAL/ORG are the broad backdrop; subject scopes are
# narrower (a subject fact shadows an org default); USER is narrowest (most specific).
_RANK: dict[Scope, int] = {
    Scope.GLOBAL: 0,
    Scope.ORG: 1,
    Scope.TEAM: 2,
    Scope.SERVICE: 3,
    Scope.REPO: 4,
    Scope.AGENT: 5,
    Scope.USER: 6,
}

# Subject scopes are org-internal and shareable; only USER is private-by-default.
SUBJECT_SCOPES: frozenset[Scope] = frozenset(
    {Scope.TEAM, Scope.SERVICE, Scope.REPO, Scope.AGENT, Scope.USER}
)


@dataclass(frozen=True)
class ScopeRef:
    """A concrete (scope, scope_id) address. ``scope_id`` is None only for GLOBAL.

    Examples: ``ScopeRef(Scope.ORG, "org_42")``, ``ScopeRef(Scope.USER, "user_7")``,
    ``ScopeRef(Scope.GLOBAL, None)``.
    """

    scope: Scope
    scope_id: str | None

    def __post_init__(self) -> None:
        if self.scope is Scope.GLOBAL:
            if self.scope_id is not None:
                raise ValueError("GLOBAL scope must have scope_id=None")
        elif not self.scope_id:
            raise ValueError(f"{self.scope.value} scope requires a non-empty scope_id")

    @classmethod
    def global_(cls) -> ScopeRef:
        return cls(Scope.GLOBAL, None)

    def __str__(self) -> str:
        return self.scope.value if self.scope_id is None else f"{self.scope.value}:{self.scope_id}"
