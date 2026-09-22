"""Workspace notes: the closed section grammar and the byte budget (pure rules, no DB)."""
from __future__ import annotations

import pytest

from mlpal_memory_graph.services.notes import (
    MAX_CHARS,
    SECTIONS,
    NoteError,
    changed_sections,
    normalise,
    parse_sections,
    render_body,
)

BODY = """## Now
Building the infra HOP eval kit.

## decisions
- Coding HOP parked until infra ships.
## Pointers
- mlpal-hops/infra/WORKLOG.md
"""


def test_parse_returns_every_canonical_section_in_order():
    s = parse_sections(BODY)
    assert list(s) == list(SECTIONS)
    assert s["Now"] == "Building the infra HOP eval kit."
    assert s["Decisions"] == "- Coding HOP parked until infra ships."   # heading case normalised
    assert s["Open threads"] == "" and s["Preferences"] == ""


def test_normalise_is_idempotent_and_canonical():
    once = normalise(BODY)
    assert once.startswith("## Now\n") and "## Decisions\n" in once and once.endswith("\n")
    assert normalise(once) == once
    assert once.index("## Open threads") < once.index("## Pointers")


def test_unknown_heading_is_error():
    with pytest.raises(NoteError, match="unknown section"):
        parse_sections("## Random\nstuff\n")


def test_text_before_first_heading_is_error():
    with pytest.raises(NoteError, match="before the first"):
        parse_sections("preamble\n## Now\nx\n")


def test_duplicate_section_is_error():
    with pytest.raises(NoteError, match="duplicate"):
        parse_sections("## Now\na\n## now\nb\n")


def test_budget_is_enforced_not_truncated():
    with pytest.raises(NoteError, match="exceeds"):
        parse_sections("## Now\n" + "x" * MAX_CHARS)


def test_empty_body_is_valid_and_renders_template():
    assert parse_sections("") == {s: "" for s in SECTIONS}
    assert render_body({}) == "## Now\n\n## Decisions\n\n## Open threads\n\n## Preferences\n\n## Pointers\n"


def test_changed_sections_names_only_the_diff():
    new = BODY.replace("Building the infra HOP eval kit.", "Writing golden G5.")
    assert changed_sections(BODY, new) == ["Now"]
    assert changed_sections("", BODY) == ["Now", "Decisions", "Pointers"]
