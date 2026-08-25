"""Collector watermark state: content-hash per input path, so re-runs skip unchanged
inputs and re-ingest changed ones (which then version alongside the old bitemporally)."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

DEFAULT_STATE_PATH = Path.home() / ".mlpal-memory" / "collectors.json"


def content_sha(data: bytes | str) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()[:32]


class CollectorState:
    def __init__(self, path: Path = DEFAULT_STATE_PATH) -> None:
        self.path = path
        self._seen: dict[str, str] = {}
        if path.exists():
            try:
                self._seen = json.loads(path.read_text())
            except (OSError, json.JSONDecodeError):
                self._seen = {}

    def unchanged(self, key: str, sha: str) -> bool:
        return self._seen.get(key) == sha

    def mark(self, key: str, sha: str) -> None:
        self._seen[key] = sha

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._seen, indent=0, sort_keys=True))
