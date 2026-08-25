"""Two orthogonal axes — don't conflate them.

* **extraction_tier** — does the *fold* need the LLM? Keyed on whether the episode carries raw
  **content** to mine. Lifecycle/structural/signal events (no content) are always
  ``deterministic`` (rule edges, no LLM) regardless of how important they are. Only
  content-bearing high-salience episodes are ``llm``. This is the cost control
  (Mem0: indiscriminate graph writes ≈ 3× cost for ~2% gain).
* **importance** — how much a memory should weigh in *retrieval scoring* (recency × relevance ×
  importance). A purely structural fact ("alice is a member of acme") can be high-importance for
  recall yet still ``deterministic`` to extract. Used by ranking, not by the fold.
"""

from __future__ import annotations

# Episodes whose raw `content` is worth mining with an LLM when present.
CONTENT_BEARING: frozenset[str] = frozenset(
    {
        "chat.message",
        "agent.decided",
        "build.session.completed",
        "document.ingested",
    }
)


def extraction_tier(action_type: str, has_content: bool) -> str:
    """``llm`` only for content-bearing episodes that actually carry content; else
    ``deterministic`` (rule edges, no LLM)."""
    return "llm" if (has_content and action_type in CONTENT_BEARING) else "deterministic"


# Retrieval-scoring importance by action_type (0..1). Orthogonal to extraction_tier:
# structural lifecycle facts can be high-importance yet deterministic to extract; tool-call /
# usage signals are low-importance and never full-extracted.
_IMPORTANCE: dict[str, float] = {
    "org.member_added": 0.8,
    "org.role_changed": 0.8,
    "agent.created": 0.8,
    "agent.deployed": 0.8,
    "mcp.deployed": 0.8,
    "skill.published": 0.8,
    "build.session.completed": 0.9,
    "agent.decided": 0.9,
    "fact.observed": 0.9,
    "chat.message": 0.6,
    "skill.used": 0.4,
    "mcp.tool.invoked": 0.2,
    "tool.invoked": 0.2,
    "usage.recorded": 0.1,
}
DEFAULT_IMPORTANCE = 0.5


def importance(action_type: str) -> float:
    """Retrieval-scoring weight for an action_type (not used by the fold)."""
    return _IMPORTANCE.get(action_type, DEFAULT_IMPORTANCE)
