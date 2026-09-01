"""P0.4: multi-day resumed sessions split into per-day documents with their own
event times (x6 recon: one 5-week session must not share one valid_at)."""

from __future__ import annotations

import json

from mlpal_memory_graph.collectors.claude_code import parse_session, parse_session_segments


def _rec(rtype: str, text: str, ts: str) -> str:
    return json.dumps({
        "type": rtype, "timestamp": ts, "cwd": "/Users/x/code/my-repo",
        "message": {"content": [{"type": "text", "text": text}]},
    })


def _write(tmp_path, name, lines):
    d = tmp_path / "-Users-x-code-my-repo"
    d.mkdir(exist_ok=True)
    p = d / f"{name}.jsonl"
    p.write_text("\n".join(lines))
    return p


def test_multi_day_session_splits_per_day(tmp_path):
    filler = "x" * 1100  # clear the per-segment MIN_DOC_CHARS bar
    lines = []
    for day in ("2026-07-25", "2026-08-10", "2026-08-19"):
        lines.append(_rec("user", f"work on day {day} {filler}", f"{day}T09:00:00Z"))
        lines.append(_rec("assistant", f"did the {day} work {filler}", f"{day}T09:05:00Z"))
    p = _write(tmp_path, "long-session", lines)

    segs = parse_session_segments(p)
    assert [s.day for s in segs] == ["2026-07-25", "2026-08-10", "2026-08-19"]
    for s in segs:
        assert s.started_at is not None and s.started_at.date().isoformat() == s.day
        assert f"day {s.day}" in s.text
        assert s.session_id.endswith(f"#{s.day}")
    # a quiet day below the substance bar is dropped, not padded
    lines.append(_rec("user", "tiny", "2026-08-20T09:00:00Z"))
    p2 = _write(tmp_path, "long-session-2", lines)
    assert [s.day for s in parse_session_segments(p2)][-1] == "2026-08-19"


def test_single_day_session_unchanged(tmp_path):
    filler = "y" * 2100
    p = _write(tmp_path, "short", [
        _rec("user", f"one day of work {filler}", "2026-08-01T10:00:00Z"),
        _rec("assistant", f"done {filler}", "2026-08-01T10:30:00Z"),
    ])
    segs = parse_session_segments(p)
    assert len(segs) == 1 and segs[0].day is None
    assert segs[0].session_id == "short"
    assert parse_session(p) is not None  # back-compat wrapper
