"""Fact-text normalization for embedding (D3).

The embedding leg works best on **self-contained, pronoun-free** fact sentences. Writing those
is ultimately the LLM extractor's job (P2.1); this helper just does the cheap, deterministic
hygiene — collapse whitespace — so the same fact always embeds to the same vector.
"""

from __future__ import annotations

import re

_WS = re.compile(r"\s+")


def normalize_fact(fact: str | None) -> str | None:
    """Collapse whitespace and strip. Returns None for empty/None (nothing to embed)."""
    if not fact:
        return None
    normalized = _WS.sub(" ", fact).strip()
    return normalized or None
