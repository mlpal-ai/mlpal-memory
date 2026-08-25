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
    session_id = path.stem
    workspace: str | None = None
    started_at: datetime | None = None
    lines: list[str] = []
    user_turns = 0

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
            if started_at is None and rec.get("timestamp"):
                try:
                    started_at = datetime.fromisoformat(rec["timestamp"].replace("Z", "+00:00"))
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
                    user_turns += 1
                    lines.append(f"USER: {text}")
            else:
                text = _text_of_content(content)
                if text:
                    lines.append(f"ASSISTANT: {text}")

    if user_turns < MIN_USER_TURNS:
        return None
    project_slug = path.parent.name.lower()
    if any(marker in project_slug for marker in NOISE_WORKSPACE_MARKERS):
        return None
    text = "\n\n".join(lines)
    if len(text) < MIN_DOC_CHARS:
        return None
    if len(text) > MAX_DOC_CHARS:
        # keep the head and tail — openings state intent, endings state outcomes
        half = MAX_DOC_CHARS // 2
        text = text[:half] + "\n\n[... session truncated ...]\n\n" + text[-half:]
    return SessionDoc(
        session_id=session_id,
        path=path,
        workspace=workspace,
        started_at=started_at,
        turns=user_turns,
        text=text,
    )


def iter_session_files(projects_dir: Path = DEFAULT_PROJECTS_DIR):
    """All session transcripts, newest first (most valuable memory lands first)."""
    files = sorted(
        projects_dir.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True
    )
    yield from files
