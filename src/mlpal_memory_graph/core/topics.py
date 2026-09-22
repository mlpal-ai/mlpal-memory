"""Topic grants (hop-v1.1 §9.3, memory v6 WP11): a HOP writes only the topics in its `writes`
list and reads keyed topics in `writes ∪ reads`. The host stamps the contract on every memory call
(engine `_meta` → sidecar headers `X-Memory-Writes` / `X-Memory-Reads`); the service enforces it.
No headers means no contract (a legacy caller or a person): nothing is restricted, as before."""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass

WRITES_HEADER = "x-memory-writes"
READS_HEADER = "x-memory-reads"


def _expand(pattern: str, user_id: str | None) -> str:
    # `{me}` names the caller; an unknown caller matches any single segment there
    return pattern.replace("{me}", user_id if user_id else "*")


def topic_matches(pattern: str, topic: str, user_id: str | None = None) -> bool:
    """Glob over the whole topic id: `infra/*` covers `infra/state/cost-daily`; `{me}` is the caller."""
    return fnmatch.fnmatchcase(topic, _expand(pattern, user_id))


@dataclass(frozen=True)
class TopicGrant:
    writes: tuple[str, ...]
    reads: tuple[str, ...] = ()
    user_id: str | None = None

    def may_write(self, topic: str) -> bool:
        return any(topic_matches(p, topic, self.user_id) for p in self.writes)

    def may_read(self, topic: str) -> bool:
        return any(topic_matches(p, topic, self.user_id) for p in (*self.writes, *self.reads))

    def describe(self) -> str:
        return f"writes {list(self.writes)}, reads {list(self.reads)}"


def _split(value: str | None) -> tuple[str, ...]:
    return tuple(p.strip() for p in (value or "").split(",") if p.strip())


def grant_from_headers(headers, user_id: str | None) -> TopicGrant | None:
    """The contract the host stamped on this call, or None when the caller carries none."""
    get = headers.get
    writes, reads = _split(get(WRITES_HEADER)), _split(get(READS_HEADER))
    if not writes and not reads:
        return None
    return TopicGrant(writes=writes, reads=reads, user_id=user_id)
