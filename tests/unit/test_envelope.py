from __future__ import annotations

import pytest

from mlpal_memory_graph.ingest.envelope import EpisodeEnvelope


def test_defaults_and_event_id():
    e = EpisodeEnvelope(action_type="fact.observed")
    assert e.event_id  # auto uuid
    assert e.source == "external"
    assert e.occurred_at is not None


def test_content_capture_gate():
    e = EpisodeEnvelope(action_type="chat.message", org_id="o1", content="secret prompt text")
    off = e.to_episode_kwargs(capture_content=False)
    on = e.to_episode_kwargs(capture_content=True)
    assert off["content"] is None  # metadata-only by default
    assert on["content"] == "secret prompt text"


def test_org_scope_defaults_scope_id_to_org_id():
    e = EpisodeEnvelope(action_type="fact.observed", scope="org", org_id="o1")
    assert e.to_episode_kwargs(capture_content=False)["scope_id"] == "o1"


def test_global_scope_may_omit_scope_id():
    e = EpisodeEnvelope(action_type="fact.observed", scope="global")
    assert e.to_episode_kwargs(capture_content=False)["scope_id"] is None


def test_non_global_scope_without_id_fails_loud():
    # a 'user'/'team' episode with no subject id would land under (scope, NULL) — reject it
    e = EpisodeEnvelope(action_type="fact.observed", scope="user")
    with pytest.raises(ValueError, match="requires a scope_id"):
        e.to_episode_kwargs(capture_content=False)


def test_actor_subject_roundtrip():
    e = EpisodeEnvelope(
        action_type="agent.deployed",
        org_id="org1",
        actor={"user_id": "u1"},
        subject={"agent_id": "a1"},
    )
    kw = e.to_episode_kwargs(capture_content=False)
    assert kw["org_id"] == "org1"
    assert kw["actor"]["user_id"] == "u1"
    assert kw["subject"]["agent_id"] == "a1"
