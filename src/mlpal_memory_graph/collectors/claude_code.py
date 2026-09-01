"""Claude Code session collector: parse ~/.claude/projects/**/*.jsonl transcripts.

A session becomes one direct-tier document: the user↔assistant narrative (text blocks
only — tool dumps and thinking are noise for retrieval; tool names are kept as trace
lines). ``valid_at`` = the session's own start time, so a March session ranks as March
knowledge. Sidechains (subagent transcripts) are skipped.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

DEFAULT_PROJECTS_DIR = Path.home() / ".claude" / "projects"
MAX_DOC_CHARS = 60_000  # cap per session document (keeps chunk counts sane)
MIN_USER_TURNS = 1  # one-shot sessions can carry real knowledge …
MIN_DOC_CHARS = 2_000  # … but only if there is substance
# scripted/ephemeral workspaces are noise, not personal memory (benchmark runs, tmp dirs)
NOISE_WORKSPACE_MARKERS = ("harness-bench-runs", "tmp", "var-folders")


@dataclass
class SessionDoc:
    session_id: str
    path: Path
    workspace: str | None  # repo/project name from cwd
    started_at: datetime | None
    turns: int
    text: str
    # set when this doc is one DAY of a multi-day resumed session (P0.4 — x6 recon:
    # a 5-week continuously-resumed session must not carry one valid_at)
    day: str | None = None


def _text_of_content(content) -> str:
    """User content is a string or a block list; assistant content is a block list.
    Keep text blocks; represent tool_use as a one-line trace; drop tool_result/thinking."""
    if isinstance(content, str):
        return content.strip()
    parts: list[str] = []
    for block in content or []:
        kind = block.get("type")
        if kind == "text":
            parts.append((block.get("text") or "").strip())
        elif kind == "tool_use":
            parts.append(f"[tool: {block.get('name', '?')}]")
    return "\n".join(p for p in parts if p)


def parse_session(path: Path) -> SessionDoc | None:
    """Single-document view of a session (first segment when multi-day)."""
    segs = parse_session_segments(path)
    return segs[0] if segs else None


def parse_session_segments(path: Path) -> list[SessionDoc]:
    session_id = path.stem
    workspace: str | None = None
    started_at: datetime | None = None
    # (day, line) pairs; day "" when the record carries no timestamp
    lines: list[tuple[str, str]] = []
    day_first_ts: dict[str, datetime] = {}
    user_turns_by_day: dict[str, int] = {}
    current_day = ""

    with path.open(encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("isSidechain"):
                continue
            rtype = rec.get("type")
            if rtype not in ("user", "assistant"):
                continue
            if workspace is None and rec.get("cwd"):
                workspace = Path(rec["cwd"]).name or None
            ts = rec.get("timestamp") or rec.get("ts")
            if ts:
                try:
                    parsed = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    started_at = started_at or parsed
                    current_day = str(ts)[:10]
                    day_first_ts.setdefault(current_day, parsed)
                except ValueError:
                    pass
            msg = rec.get("message") or {}
            content = msg.get("content")
            if rtype == "user":
                # tool_result-only user records are transport, not conversation
                if not isinstance(content, str) and not any(
                    b.get("type") == "text" for b in (content or []) if isinstance(b, dict)
                ):
                    continue
                text = _text_of_content(content)
                if text:
                    user_turns_by_day[current_day] = user_turns_by_day.get(current_day, 0) + 1
                    lines.append((current_day, f"USER: {text}"))
            else:
                text = _text_of_content(content)
                if text:
                    lines.append((current_day, f"ASSISTANT: {text}"))

    project_slug = path.parent.name.lower()
    if any(marker in project_slug for marker in NOISE_WORKSPACE_MARKERS):
        return []
    days = sorted({d for d, _ in lines})
    multi_day = len([d for d in days if d]) > 1

    def _finish(text: str, turns: int, day: str | None, start: datetime | None):
        if turns < MIN_USER_TURNS or len(text) < MIN_DOC_CHARS:
            return None
        if len(text) > MAX_DOC_CHARS:
            # keep the head and tail — openings state intent, endings state outcomes
            half = MAX_DOC_CHARS // 2
            text = text[:half] + "\n\n[... session truncated ...]\n\n" + text[-half:]
        return SessionDoc(
            session_id=session_id + (f"#{day}" if day else ""),
            path=path,
            workspace=workspace,
            started_at=start,
            turns=turns,
            text=text,
            day=day,
        )

    if not multi_day:
        doc = _finish(
            "\n\n".join(t for _, t in lines),
            sum(user_turns_by_day.values()),
            None,
            started_at,
        )
        return [doc] if doc else []
    # multi-day resumed session: one document per active day, each with ITS OWN
    # event time — five weeks of shifting truth must not share one valid_at (P0.4)
    out: list[SessionDoc] = []
    for day in days:
        if not day:
            continue
        doc = _finish(
            "\n\n".join(t for d, t in lines if d == day),
            user_turns_by_day.get(day, 0),
            day,
            day_first_ts.get(day),
        )
        if doc:
            out.append(doc)
    return out


def iter_session_files(projects_dir: Path = DEFAULT_PROJECTS_DIR):
    """All session transcripts, newest first (most valuable memory lands first)."""
    files = sorted(
        projects_dir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    yield from files
