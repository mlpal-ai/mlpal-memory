#!/usr/bin/env python3
"""Collect local sources into the memory system (v3 task #4).

    python scripts/collect_local.py --source all                # everything
    python scripts/collect_local.py --source claude-code --limit 50
    python scripts/collect_local.py --source md --dry-run
    python scripts/collect_local.py --source repos

Sources: claude-code (~/.claude/projects session transcripts), md (CLAUDE.md/AGENTS.md/
MEMORY.md/skills), repos (repo cards + READMEs/docs for every git repo in the code root).

Idempotent: content-hashed ids dedup at the API; a local state file
(~/.mlpal-memory/collectors.json) skips unchanged inputs. Everything flows through the
governed fold (consent → policy → redaction) server-side.
"""

from __future__ import annotations

import argparse
import getpass
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from mlpal_memory_graph.collectors.claude_code import (  # noqa: E402
    iter_session_files,
    parse_session_segments,
)
from mlpal_memory_graph.collectors.markdown import iter_md_docs  # noqa: E402
from mlpal_memory_graph.collectors.pdfs import iter_pdfs  # noqa: E402
from mlpal_memory_graph.collectors.repos import iter_repos  # noqa: E402
from mlpal_memory_graph.collectors.state import CollectorState, content_sha  # noqa: E402

DEFAULT_CODE_ROOT = Path.home() / "Downloads" / "Coding" / "mlpal" / "code"


class Ingestor:
    def __init__(self, base: str, org: str, user: str, dry_run: bool) -> None:
        self.dry = dry_run
        self.client = httpx.Client(
            base_url=base,
            timeout=120,
            headers={
                "X-Test-Org-Id": org,
                "X-Test-User-Id": user,
                "Content-Type": "application/json",
            },
        )
        self.user = user
        self.stats = {"documents": 0, "duplicates": 0, "episodes": 0, "dropped": 0, "errors": 0}
        # URIs the server treated as NEW this run — the C1 replay canary asserts this is
        # empty on a second back-to-back replay (every input already known by content id)
        self.processed_uris: list[str] = []

    def document(self, **body) -> bool:
        # deterministic id from locator+content → server-side dedup survives state loss
        body["event_id"] = "doc-" + content_sha(f"{body.get('uri')}|{body['content']}")
        if self.dry:
            self.stats["documents"] += 1
            return True
        try:
            r = self.client.post("/api/v1/documents", json=body)
            r.raise_for_status()
            status = r.json().get("status")
            if status in ("processed", "duplicate"):
                self.stats["documents"] += 1
                if status == "duplicate":
                    self.stats["duplicates"] += 1
                else:
                    self.processed_uris.append(str(body.get("uri")))
                return True
            self.stats["dropped"] += 1
        except httpx.HTTPError as exc:
            self.stats["errors"] += 1
            print(f"    ! document failed: {exc}", file=sys.stderr)
        return False

    def episodes(self, envelopes: list[dict]) -> bool:
        if self.dry:
            self.stats["episodes"] += len(envelopes)
            return True
        try:
            r = self.client.post("/api/v1/episodes?process=true", json={"episodes": envelopes})
            r.raise_for_status()
            self.stats["episodes"] += r.json().get("accepted", 0)
            return True
        except httpx.HTTPError as exc:
            self.stats["errors"] += 1
            print(f"    ! episodes failed: {exc}", file=sys.stderr)
            return False


def collect_claude_code(ing: Ingestor, state: CollectorState, limit: int | None) -> None:
    print("▸ claude-code sessions")
    done = skipped = 0
    for path in iter_session_files():
        if limit and done >= limit:
            break
        key = f"cc:{path}"
        sha = content_sha(path.read_bytes())
        if state.unchanged(key, sha):
            skipped += 1
            continue
        docs = parse_session_segments(path)
        if not docs:  # trivial session
            state.mark(key, sha)
            continue
        all_ok = True
        for doc in docs:
            day_note = f" · {doc.day}" if doc.day else (
                f" · {doc.started_at.date()}" if doc.started_at else " · undated"
            )
            ok = ing.document(
                content=doc.text,
                title=f"Claude Code session · {doc.workspace or 'home'}{day_note}",
                scope="user",
                scope_id=ing.user,
                source="claude_code",
                uri=str(doc.path) + (f"#{doc.day}" if doc.day else ""),
                workspace=doc.workspace,
                valid_at=doc.started_at.isoformat() if doc.started_at else None,
            )
            all_ok = all_ok and ok
        if all_ok:
            state.mark(key, sha)
            done += 1
            if done % 25 == 0:
                print(f"  … {done} sessions ingested")
    print(f"  sessions: {done} ingested, {skipped} unchanged")


def collect_md(ing: Ingestor, state: CollectorState, code_root: Path, limit: int | None) -> None:
    print("▸ markdown memory files + skills")
    done = skipped = 0
    for md in iter_md_docs(code_root, claude_home=Path.home() / ".claude"):
        if limit and done >= limit:
            break
        key = f"md:{md.path}"
        sha = content_sha(md.text)
        if state.unchanged(key, sha):
            skipped += 1
            continue
        ok = ing.document(
            content=md.text,
            title=f"{md.kind} · {md.path.name} · {md.workspace or 'global'}",
            scope="user",
            scope_id=ing.user,
            source=md.kind.replace("-", "_"),
            uri=str(md.path),
            workspace=md.workspace,
            valid_at=md.mtime.isoformat(),
        )
        if ok:
            state.mark(key, sha)
            done += 1
    print(f"  md/skills: {done} ingested, {skipped} unchanged")


def collect_pdfs(ing: Ingestor, state: CollectorState, root: Path, limit: int | None) -> None:
    print("▸ pdfs")
    n = 0
    for item in iter_pdfs(root):
        if limit is not None and n >= limit:
            break
        if "skipped" in item:
            print(f"    - skip {item['path'].name}: {item['skipped']}")
            continue
        key = f"pdf:{item['path']}"
        sha = content_sha(item["content"])
        if state.unchanged(key, sha):
            continue
        ok = ing.document(
            content=item["content"],
            title=f"pdf: {item['title']}",
            scope="user",
            scope_id=ing.user,
            source="pdf_file",
            uri=str(item["path"]),
            valid_at=item["valid_at"].isoformat(),
        )
        if ok:
            state.mark(key, sha)
            n += 1
    print(f"    {n} pdfs ingested")


def collect_repos(ing: Ingestor, state: CollectorState, code_root: Path, limit: int | None) -> None:
    print("▸ repos (cards + README/docs)")
    done = skipped = 0
    for card in iter_repos(code_root):
        if limit and done >= limit:
            break
        # deterministic repo-card facts → derived tier (cheap rule extraction)
        langs = ", ".join(card.languages) or "unknown"
        stmt = (
            f"the {card.name} repository is written in {langs}; "
            f"top-level layout: {', '.join(card.top_dirs[:8])}"
        )
        card_sha = content_sha(stmt)
        key = f"repo-card:{card.name}"
        if not state.unchanged(key, card_sha):
            ing.episodes(
                [
                    {
                        "event_id": f"repo-card-{card.name}-{card_sha[:12]}",
                        "org_id": None,  # pinned server-side to the caller's org
                        "actor": {"user_id": ing.user},
                        "source": "repo_scan",
                        "action_type": "fact.observed",
                        "scope": "repo",
                        "scope_id": card.name,
                        "workspace": card.name,
                        "payload": {"statement": stmt},
                    }
                ]
            )
            state.mark(key, card_sha)
        for path, valid_at, text in card.docs:
            dkey = f"repo-doc:{path}"
            dsha = content_sha(text)
            if state.unchanged(dkey, dsha):
                skipped += 1
                continue
            ok = ing.document(
                content=text,
                title=f"{card.name} · {path.name}",
                scope="repo",
                scope_id=card.name,
                source="repo_doc",
                uri=str(path),
                workspace=card.name,
                valid_at=valid_at.isoformat(),
            )
            if ok:
                state.mark(dkey, dsha)
        done += 1
    print(f"  repos: {done} scanned, {skipped} docs unchanged")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--source", choices=["all", "claude-code", "md", "repos", "pdfs"], default="all"
    )
    ap.add_argument("--pdf-root", type=Path, default=None, help="root dir for --source pdfs")
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--org", default="local")
    ap.add_argument("--user", default=getpass.getuser())
    ap.add_argument("--code-root", type=Path, default=DEFAULT_CODE_ROOT)
    ap.add_argument("--limit", type=int, default=None, help="max items per source")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--reset-state", action="store_true", help="forget watermarks first")
    ap.add_argument("--json-out", type=Path, default=None, help="write run stats as JSON")
    args = ap.parse_args()

    state = CollectorState()
    if args.reset_state:
        state._seen = {}

    # NOTE (repo scope): the collector's repo docs/cards land in REPO subject scope,
    # which requires elevated write authz — the local single-user org uses admin perms.
    ing = Ingestor(args.base_url, args.org, args.user, args.dry_run)
    ing.client.headers["X-Test-Permissions"] = "memory:read,memory:write,memory:admin"

    t0 = time.monotonic()
    if args.source in ("all", "claude-code"):
        collect_claude_code(ing, state, args.limit)
        state.save()
    if args.source in ("all", "md"):
        collect_md(ing, state, args.code_root, args.limit)
        state.save()
    if args.source in ("all", "repos"):
        collect_repos(ing, state, args.code_root, args.limit)
        state.save()
    if args.source == "pdfs":  # explicit opt-in: PDF sweeps can be large
        collect_pdfs(ing, state, args.pdf_root or args.code_root, args.limit)
        state.save()

    dt = time.monotonic() - t0
    s = ing.stats
    print(
        f"\nDONE in {dt:.0f}s — documents={s['documents']} ({s['duplicates']} duplicate) "
        f"episodes={s['episodes']} dropped={s['dropped']} errors={s['errors']}"
        + (" (dry-run)" if args.dry_run else "")
    )
    if args.json_out:
        import json as _json

        args.json_out.write_text(
            _json.dumps({**s, "processed_uris": ing.processed_uris, "seconds": round(dt)})
        )
    return 1 if s["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
