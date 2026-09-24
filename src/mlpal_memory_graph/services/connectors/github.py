"""GitHub connector (memory v12 §6, the third door): a repository's documents reach memory with
nobody running a collector. Registration catalogues the repository's text files as cold source
items (path, blob sha, size); a sync admits them as documents (eagerly by default, up to a cap
per sync; or on demand like a files source) through the same governed fold as every document.
Idempotent: an item's document id is derived from its path and blob sha, so an unchanged file
never double-ingests and a changed one becomes a new version with its own event time.

Credentials are never stored: the source carries a `credential_ref`, the name of a variable in
the service's environment (managed: the secrets service resolves the same name). A public
repository needs none.
"""

from __future__ import annotations

import base64
import fnmatch
import hashlib
import os
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
from sqlalchemy import select

from ...core.logging import get_logger
from ...db.models import MemorySource, SourceItem
from ..resilience import ModelUnavailable

log = get_logger(__name__)

API_BASE = "https://api.github.com"
TEXT_SUFFIXES = (".md", ".markdown", ".txt", ".rst", ".adoc", ".csv", ".json", ".yaml", ".yml")
MAX_BLOB_BYTES = 400_000  # the contents API serves up to 1 MB; memory stores text it can cite
MAX_ITEMS = 5_000
EAGER_ADMIT_PER_SYNC = 300


class ConnectorError(ValueError):
    """A registration or sync the connector cannot perform (bad repo, missing credential, API refusal)."""


@dataclass
class GitHubConfig:
    repo: str  # owner/name
    branch: str | None
    paths: list[str]  # prefixes or globs; empty = the whole repository
    credential_ref: str | None
    admit: str  # eager | on-demand
    interval_minutes: int

    @classmethod
    def from_source(cls, src: MemorySource) -> GitHubConfig:
        c = src.config or {}
        return cls(repo=str(c.get("repo") or ""), branch=c.get("branch"), paths=list(c.get("paths") or []),
                   credential_ref=c.get("credential_ref"), admit=str(c.get("admit") or "eager"),
                   interval_minutes=int(c.get("interval_minutes") or 60))


def path_matches(path: str, patterns: list[str]) -> bool:
    if not patterns:
        return True
    for pat in patterns:
        if pat.endswith("/"):
            if path.startswith(pat):
                return True
        elif fnmatch.fnmatch(path, pat) or path == pat:
            return True
    return False


def is_text_path(path: str) -> bool:
    lower = path.lower()
    return lower.endswith(TEXT_SUFFIXES) and not any(part.startswith(".") for part in lower.split("/")[:-1])


def token_for(cfg: GitHubConfig) -> str | None:
    if not cfg.credential_ref:
        return None
    tok = os.environ.get(cfg.credential_ref)
    if not tok:
        raise ConnectorError(f"credential {cfg.credential_ref!r} is not set in the service environment")
    return tok


class GitHubClient:
    """The few GitHub REST calls the connector needs. `transport` is the seam for tests."""

    def __init__(self, token: str | None, *, base_url: str = API_BASE, transport: httpx.AsyncBaseTransport | None = None) -> None:
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "mlpal-memory"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30, transport=transport)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get(self, path: str, **params) -> dict | list:
        try:
            r = await self._client.get(path, params=params or None)
        except httpx.HTTPError as exc:
            raise ModelUnavailable("github", str(exc)) from exc
        if r.status_code == 404:
            raise ConnectorError(f"GitHub: not found ({path}); check the repository name, branch and the token's access")
        if r.status_code in (401, 403):
            raise ConnectorError(f"GitHub refused ({r.status_code}): {r.json().get('message', '') if r.headers.get('content-type', '').startswith('application/json') else r.text[:120]}")
        if r.status_code >= 500:
            raise ModelUnavailable("github", f"HTTP {r.status_code}")
        r.raise_for_status()
        return r.json()

    async def default_branch(self, repo: str) -> str:
        info = await self._get(f"/repos/{repo}")
        return str(info["default_branch"])

    async def tree(self, repo: str, branch: str) -> list[dict]:
        data = await self._get(f"/repos/{repo}/git/trees/{branch}", recursive="1")
        return [e for e in data.get("tree", []) if e.get("type") == "blob"]

    async def blob_text(self, repo: str, path: str, ref: str) -> str:
        data = await self._get(f"/repos/{repo}/contents/{path}", ref=ref)
        if isinstance(data, dict) and data.get("encoding") == "base64":
            return base64.b64decode(data.get("content") or "").decode("utf-8", errors="replace")
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            return data["content"]
        raise ConnectorError(f"GitHub returned no content for {path}")


def make_client(cfg: GitHubConfig) -> GitHubClient:
    """The connector's client; tests replace this with one on a mock transport."""
    return GitHubClient(token_for(cfg))


def item_event_id(src: MemorySource, ref: str, sha: str) -> str:
    return f"gh:{src.id[:10]}:{hashlib.sha256(f'{ref}@{sha}'.encode()).hexdigest()[:24]}"


async def read_item(src: MemorySource, item: SourceItem) -> str:
    """The item's text, fetched now (used when a cold item is admitted on demand)."""
    cfg = GitHubConfig.from_source(src)
    client = make_client(cfg)
    try:
        return (await client.blob_text(cfg.repo, item.ref, cfg.branch or "HEAD"))[:MAX_BLOB_BYTES]
    finally:
        await client.aclose()


@dataclass
class SyncResult:
    catalogued: int
    changed: int
    admitted: int
    declined: int
    skipped: int


async def sync_source(session, src: MemorySource, *, user_id: str | None) -> SyncResult:
    """Catalogue the repository (new and changed blobs), admit eagerly when the source says so.
    A changed blob (same path, new sha) resets the item so it is admitted again as a new document
    version; the old document stays as history."""
    from ..sources import admit  # local import: sources imports this module

    cfg = GitHubConfig.from_source(src)
    if not cfg.repo or "/" not in cfg.repo:
        raise ConnectorError("a GitHub source needs `repo` as owner/name")
    client = make_client(cfg)
    try:
        branch = cfg.branch or await client.default_branch(cfg.repo)
        if not cfg.branch:
            src.config = {**(src.config or {}), "branch": branch}
        blobs = [b for b in await client.tree(cfg.repo, branch)
                 if is_text_path(str(b.get("path", ""))) and path_matches(str(b.get("path", "")), cfg.paths)
                 and int(b.get("size") or 0) <= MAX_BLOB_BYTES]
        blobs = blobs[:MAX_ITEMS]
        existing = {i.ref: i for i in (await session.execute(select(SourceItem).where(SourceItem.source_id == src.id))).scalars().all()}
        changed = 0
        now = datetime.now(UTC)
        sha_by_ref: dict[str, str] = {}
        for b in blobs:
            ref, sha, size = str(b["path"]), str(b.get("sha") or ""), int(b.get("size") or 0)
            sha_by_ref[ref] = sha
            it = existing.get(ref)
            if it is None:
                it = SourceItem(org_id=src.org_id, source_id=src.id, ref=ref, title=ref.rsplit("/", 1)[-1].rsplit(".", 1)[0].replace("_", " ").replace("-", " "),
                                size=size, modified_at=now, declined_reason=None)
                session.add(it)
                existing[ref] = it
                changed += 1
            elif it.admitted and it.document_event_id != item_event_id(src, ref, sha):
                # a new blob for a known path: the item is cold again, its next admission is a new version
                it.admitted, it.admitted_at, it.admitted_by, it.declined_reason, it.size, it.modified_at = False, None, None, None, size, now
                changed += 1
        src.item_count = len(blobs)
        src.indexed_at = now
        await session.flush()
        admitted = declined = skipped = 0
        if cfg.admit == "eager":
            cold = [existing[str(b["path"])] for b in blobs if not existing[str(b["path"])].admitted and not existing[str(b["path"])].declined_reason]
            for it in cold[:EAGER_ADMIT_PER_SYNC]:
                text = (await client.blob_text(cfg.repo, it.ref, branch))[:MAX_BLOB_BYTES]
                res = await admit(session, src, it, user_id=user_id, by="connector", text=text, event_id=item_event_id(src, it.ref, sha_by_ref[it.ref]))
                if res["status"] == "processed":
                    admitted += 1
                else:
                    declined += 1
            skipped = max(0, len(cold) - EAGER_ADMIT_PER_SYNC)
        src.config = {**(src.config or {}), "last_error": None, "last_sync": {"at": now.isoformat(), "catalogued": len(blobs), "changed": changed, "admitted": admitted, "declined": declined}}
        await session.flush()
        return SyncResult(catalogued=len(blobs), changed=changed, admitted=admitted, declined=declined, skipped=skipped)
    finally:
        await client.aclose()
