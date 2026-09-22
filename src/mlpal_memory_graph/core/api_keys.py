"""Static API keys: the auth backend for a self-hosted instance (memory 0.2.0).

A self-hosted instance has no platform auth service. Outside dev mode it authenticates callers
against a key file the operator owns: one entry per key, the key stored as its SHA-256 hash, the
identity the key grants beside it. ``python -m mlpal_memory_graph.tools.api_keys new`` mints a key,
prints it once and appends its hash. The file is re-read when its mtime changes, so keys are added
or revoked without a restart; a file that stops parsing keeps the last good set and logs an error,
so a bad edit never opens or closes the door silently.

    keys:
      - id: sai-laptop
        sha256: 3b0c…              # sha256 of the key, never the key itself
        org_id: acme               # the tenant every read and write is pinned to
        user_id: sai
        permissions: [memory.read, memory.write]
        # disabled: true           # revoke without deleting the row
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from .logging import get_logger

log = get_logger(__name__)

KEY_PREFIX = "mem_"
_HEX64 = 64


@dataclass(frozen=True)
class StaticKey:
    key_id: str
    org_id: str
    user_id: str | None
    permissions: tuple[str, ...]
    disabled: bool = False


def mint_key() -> str:
    return KEY_PREFIX + secrets.token_urlsafe(32)


def key_digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def parse_entries(data: object, *, source: str) -> list[tuple[str, StaticKey]]:
    """Validate the file's shape at the boundary; every defect names its entry. Returns
    (sha256 digest, key) pairs."""
    if not isinstance(data, dict) or not isinstance(data.get("keys"), list):
        raise ValueError(f"{source}: expected a mapping with a `keys` list")
    out: list[tuple[str, StaticKey]] = []
    seen_ids: set[str] = set()
    for i, raw in enumerate(data["keys"]):
        where = f"{source}: keys[{i}]"
        if not isinstance(raw, dict):
            raise ValueError(f"{where}: expected a mapping")
        key_id = str(raw.get("id") or "")
        digest = str(raw.get("sha256") or "").lower()
        org_id = str(raw.get("org_id") or "")
        if not key_id or not org_id:
            raise ValueError(f"{where}: `id` and `org_id` are required")
        if key_id in seen_ids:
            raise ValueError(f"{where}: duplicate id {key_id!r}")
        if len(digest) != _HEX64 or any(c not in "0123456789abcdef" for c in digest):
            raise ValueError(f"{where}: `sha256` must be 64 hex characters")
        perms = raw.get("permissions") or []
        if not isinstance(perms, list) or not all(isinstance(p, str) and p for p in perms):
            raise ValueError(f"{where}: `permissions` must be a list of strings")
        seen_ids.add(key_id)
        out.append((digest, StaticKey(
            key_id=key_id, org_id=org_id,
            user_id=str(raw["user_id"]) if raw.get("user_id") is not None else None,
            permissions=tuple(perms), disabled=bool(raw.get("disabled", False)),
        )))
    return out


class ApiKeyFile:
    """The operator's key file, reloaded on mtime change. ``load`` raises on a malformed file so a
    bad path or shape is a startup error; a later bad edit is logged and the last good set kept."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)
        self._mtime: float | None = None
        self._digests: list[tuple[str, StaticKey]] = []

    def load(self) -> int:
        with self.path.open() as fh:
            data = yaml.safe_load(fh) or {}
        self._digests = parse_entries(data, source=str(self.path))
        self._mtime = self.path.stat().st_mtime
        return len(self._digests)

    def _maybe_reload(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
        except OSError as exc:
            log.error("api_keys.unreadable", path=str(self.path), error=str(exc))
            return
        if mtime == self._mtime:
            return
        try:
            n = self.load()
            log.info("api_keys.reloaded", path=str(self.path), keys=n)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            log.error("api_keys.reload_failed", path=str(self.path), error=str(exc))
            self._mtime = mtime  # do not retry the same broken file on every request

    def lookup(self, token: str) -> StaticKey | None:
        """The key a presented token grants, or None (unknown or disabled). Constant-time compare
        over every entry so a miss and a hit take the same time."""
        self._maybe_reload()
        digest = key_digest(token)
        found: StaticKey | None = None
        for stored, entry in self._digests:
            if hmac.compare_digest(stored, digest):
                found = entry
        return None if found is None or found.disabled else found


@lru_cache(maxsize=4)
def get_api_key_file(path: str) -> ApiKeyFile:
    return ApiKeyFile(path)
