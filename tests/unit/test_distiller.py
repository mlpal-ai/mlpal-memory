"""Session distiller: typed insights with mandatory verbatim grounding."""

from __future__ import annotations

import pytest

from mlpal_memory_graph.pipeline.distiller import (
    DevDistiller,
    GatewayDistiller,
    _to_extraction,
)


class _Ep:
    def __init__(self, content: str, workspace: str | None = "repo-x") -> None:
        self.content = content
        self.workspace = workspace
        self.event_id = "e1"
        self.source_ref = "session-42"


TRANSCRIPT = (
    "USER: how do I run the tests here.\n\n"
    "ASSISTANT: Always run migrations before starting the api container. "
    "We decided to use trunk-based development for this repo. "
    "Beware that the sqlite suite hides pgvector issues. "
    "ok done."
)


@pytest.mark.asyncio
async def test_dev_distiller_extracts_typed_grounded_insights():
    ex = await DevDistiller().distill(_Ep(TRANSCRIPT))
    kinds = {e.type for e in ex.entities}
    assert {"Convention", "Decision", "Gotcha"} <= kinds
    assert "Workspace" in kinds and "Chat" in kinds
    # every insight edge carries its verbatim evidence span
    learned = [e for e in ex.edges if e.type == "LEARNED_IN"]
    assert learned
    for e in learned:
        assert e.props["evidence_span"] in TRANSCRIPT
    # workspace linkage
    assert any(e.type == "APPLIES_TO" for e in ex.edges)


def test_ungrounded_insights_are_dropped():
    ep = _Ep("short transcript with nothing quotable.")
    ex = _to_extraction(
        [
            {"kind": "gotcha", "name": "made up", "evidence_span": "NOT IN TRANSCRIPT"},
            {"kind": "banana", "name": "bad kind", "evidence_span": "short transcript"},
            {"kind": "howto", "name": "", "evidence_span": "short transcript"},
        ],
        ep,
    )
    insight_types = {"Convention", "Decision", "Gotcha", "HowTo", "Preference"}
    assert not [e for e in ex.entities if e.type in insight_types]


@pytest.mark.asyncio
async def test_gateway_distiller_uses_schema_and_grounds(monkeypatch):
    class _StubClient:
        name = "stub"

        async def complete_json(self, *, system, user, schema, max_tokens=None):
            assert "insights" in schema["properties"]
            return {
                "insights": [
                    {
                        "kind": "howto",
                        "name": "run migrations before the api",
                        "evidence_span": "Always run migrations before starting the api container.",
                    },
                    {
                        "kind": "gotcha",
                        "name": "hallucinated",
                        "evidence_span": "this span does not exist",
                    },
                ]
            }

    ex = await GatewayDistiller(client=_StubClient()).distill(_Ep(TRANSCRIPT))
    howtos = [e for e in ex.entities if e.type == "HowTo"]
    gotchas = [e for e in ex.entities if e.type == "Gotcha"]
    assert len(howtos) == 1  # grounded → kept
    assert len(gotchas) == 0  # hallucinated span → dropped
