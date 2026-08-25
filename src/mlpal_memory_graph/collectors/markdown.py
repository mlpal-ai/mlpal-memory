"""Markdown knowledge collector: CLAUDE.md / AGENTS.md / YODEX.md / MEMORY.md and
skill files across the code root + the user's global claude config.

These files are curated knowledge (conventions, commands, project context) — exactly
what a coding agent needs. ``valid_at`` = file mtime: a stale CLAUDE.md from January
is January knowledge, outranked by newer sources but preserved for as-of reads.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

MEMORY_FILE_NAMES = ("CLAUDE.md", "AGENTS.md", "YODEX.md", "MEMORY.md")
MAX_MD_CHARS = 40_000
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", "__pycache__", ".next"}


@dataclass
class MdDoc:
    path: Path
    kind: str  # memory-file | skill
    workspace: str | None
    mtime: datetime
    text: str


def _workspace_of(path: Path, code_root: Path) -> str | None:
    try:
        rel = path.relative_to(code_root)
    except ValueError:
        return None
    return rel.parts[0] if rel.parts else None


def _read(path: Path) -> str:
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    return text[:MAX_MD_CHARS]


def iter_md_docs(code_root: Path, claude_home: Path | None = None):
    """Memory files (repo-level, depth<=3) + skills (SKILL.md under any skills/ dir) +
    the user's global CLAUDE.md / memory directory."""
    seen: set[Path] = set()

    def _walk(root: Path, max_depth: int):
        stack = [(root, 0)]
        while stack:
            d, depth = stack.pop()
            if depth > max_depth or d.name in SKIP_DIRS:
                continue
            try:
                entries = list(d.iterdir())
            except OSError:
                continue
            for e in entries:
                if e.is_dir():
                    stack.append((e, depth + 1))
                elif e.name in MEMORY_FILE_NAMES or (
                    e.name == "SKILL.md" or (e.suffix == ".md" and "skills" in e.parts[-3:-1])
                ):
                    if e not in seen:
                        seen.add(e)
                        yield e

    roots = [code_root]
    if claude_home and claude_home.exists():
        roots.append(claude_home)
    for root in roots:
        for path in _walk(root, max_depth=4):
            try:
                text = _read(path)
            except OSError:
                continue
            if not text:
                continue
            kind = "skill" if ("skills" in path.parts or path.name == "SKILL.md") else "memory-file"
            yield MdDoc(
                path=path,
                kind=kind,
                workspace=_workspace_of(path, code_root),
                mtime=datetime.fromtimestamp(path.stat().st_mtime, tz=UTC),
                text=text,
            )
