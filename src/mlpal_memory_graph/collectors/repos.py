"""Repo collector: a deterministic "repo card" + README/docs for every git repo in the
code root. The card (name, languages, layout) becomes derived facts; README/docs become
direct documents with ``valid_at`` = the file's last git commit date — the honest
staleness signal (a README untouched for a year is a year old, whatever its mtime says).
"""

from __future__ import annotations

import subprocess
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

MAX_DOC_CHARS = 40_000
MAX_DOCS_PER_REPO = 12  # README + recent docs/**.md — 5 starved doc-heavy repos
# (x10 smoke: the integration guide lost its slot to newer design docs; the
# answer class went unservable). Still bounded; recency-first admission.
LANG_EXT = {
    ".py": "Python", ".ts": "TypeScript", ".tsx": "TypeScript", ".js": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".tf": "Terraform",
    ".yaml": "YAML", ".yml": "YAML", ".sh": "Shell", ".sql": "SQL", ".md": "Markdown",
}
SKIP_DIRS = {"node_modules", ".git", ".venv", "venv", "dist", "build", "__pycache__", ".next"}


@dataclass
class RepoCard:
    name: str
    path: Path
    languages: list[str]
    top_dirs: list[str]
    docs: list[tuple[Path, datetime, str]] = field(default_factory=list)  # (path, valid_at, text)


def _git_file_date(repo: Path, file: Path) -> datetime | None:
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", str(file.relative_to(repo))],
            cwd=repo, capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        return datetime.fromisoformat(out) if out else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def _languages(repo: Path) -> list[str]:
    counts: Counter[str] = Counter()
    stack = [(repo, 0)]
    while stack:
        d, depth = stack.pop()
        if depth > 3 or d.name in SKIP_DIRS:
            continue
        try:
            for e in d.iterdir():
                if e.is_dir():
                    stack.append((e, depth + 1))
                elif (lang := LANG_EXT.get(e.suffix)) is not None:
                    counts[lang] += 1
        except OSError:
            continue
    return [lang for lang, _ in counts.most_common(3) if lang != "Markdown"][:3]


def scan_repo(repo: Path) -> RepoCard | None:
    if not (repo / ".git").exists():
        return None
    card = RepoCard(
        name=repo.name,
        path=repo,
        languages=_languages(repo),
        top_dirs=sorted(
            d.name for d in repo.iterdir() if d.is_dir() and d.name not in SKIP_DIRS
        )[:12],
    )
    candidates = [p for p in (repo / "README.md",) if p.exists()]
    docs_dir = repo / "docs"
    if docs_dir.is_dir():
        # recursive: integration guides live in docs/<area>/ (x5 measured the miss —
        # the authoritative answer sat in docs/integrations/, invisible to a flat glob).
        # Admission is RECENCY-first (git last-commit date), not size-first: x5 round 4
        # measured size-first admitting design tomes while missing the one focused,
        # recently-updated guide that held the answer. Freshly-touched docs are the
        # org's live knowledge; a stale tome can wait for a budget increase.
        pool = [
            p for p in docs_dir.rglob("*.md")
            if not any(part.startswith(".") for part in p.relative_to(docs_dir).parts)
        ]
        def _doc_age(p: Path) -> datetime:
            # uncommitted docs are the NEWEST work, not undated — fall back to
            # mtime or recency-first silently buries exactly the freshest files
            # (x10: the untracked integration guide sorted to the bottom)
            return _git_file_date(repo, p) or datetime.fromtimestamp(
                p.stat().st_mtime, tz=UTC
            )

        extra = sorted(pool, key=_doc_age, reverse=True)
        candidates.extend(extra[: MAX_DOCS_PER_REPO - len(candidates)])
    for p in candidates[:MAX_DOCS_PER_REPO]:
        try:
            text = p.read_text(encoding="utf-8", errors="replace").strip()[:MAX_DOC_CHARS]
        except OSError:
            continue
        if not text:
            continue
        valid_at = _git_file_date(repo, p) or datetime.fromtimestamp(
            p.stat().st_mtime, tz=UTC
        )
        card.docs.append((p, valid_at, text))
    return card


def iter_repos(code_root: Path):
    for entry in sorted(code_root.iterdir()):
        if entry.is_dir() and (entry / ".git").exists():
            card = scan_repo(entry)
            if card is not None:
                yield card
