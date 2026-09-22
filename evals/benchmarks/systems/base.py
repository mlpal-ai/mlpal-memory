"""The system adapter contract for the head-to-head (memory v8, DESIGN.md §4).

An adapter wraps one memory system. The harness gives it the same sessions to ingest and the same
questions to search as every other system, and reads back items, timings and cost. The reader and
the judge live in the harness, never in the adapter, so the comparison isolates the memory system.

Every adapter records what it can about its own model usage (`llm_calls`, `llm_tokens`) — for the
OSS systems that means wrapping the provider client; where a system hides it, the field is None and
the conditions table says so. `trace()` returns the raw stored state for a haystack: that is what we
read to learn.
"""

from __future__ import annotations

import re

import os
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Item:
    id: str
    text: str
    when: str | None = None      # ISO date of the source, when the system knows it
    kind: str = "memory"         # passage | memory | fact | edge | profile
    score: float | None = None
    source_id: str | None = None  # the session/document it came from, when the system knows it

    def to_dict(self) -> dict:
        return {"id": self.id, "text": self.text, "when": self.when, "kind": self.kind, "score": self.score, "source_id": self.source_id}


@dataclass
class Usage:
    llm_calls: int = 0
    llm_input_tokens: int = 0
    llm_output_tokens: int = 0
    embed_calls: int = 0
    embed_tokens: int = 0

    def add(self, other: "Usage") -> None:
        for f in ("llm_calls", "llm_input_tokens", "llm_output_tokens", "embed_calls", "embed_tokens"):
            setattr(self, f, getattr(self, f) + getattr(other, f))

    def to_dict(self) -> dict:
        return {"llm_calls": self.llm_calls, "llm_input_tokens": self.llm_input_tokens, "llm_output_tokens": self.llm_output_tokens,
                "embed_calls": self.embed_calls, "embed_tokens": self.embed_tokens}


@dataclass
class IngestResult:
    ms: int
    items_created: int | None
    usage: Usage = field(default_factory=Usage)
    errors: int = 0
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"ms": self.ms, "items_created": self.items_created, "usage": self.usage.to_dict(), "errors": self.errors, "notes": self.notes}


@dataclass
class SearchResult:
    items: list[Item]
    ms: int
    usage: Usage = field(default_factory=Usage)
    raw: object = None  # the system's own response, kept in the row for the trace

    def to_dict(self) -> dict:
        return {"items": [i.to_dict() for i in self.items], "ms": self.ms, "usage": self.usage.to_dict()}


class System:
    """Subclass per system. Methods are synchronous; the harness runs them in a thread."""

    name: str = "base"
    #: what the system needs and what it cannot be configured to — printed into CONDITIONS.md
    conditions: dict[str, str] = {}

    def setup(self, run_dir: Path) -> None:
        self.run_dir = run_dir

    def ingest(self, haystack_id: str, docs: list[dict]) -> IngestResult:
        raise NotImplementedError

    def search(self, haystack_id: str, question: str, k: int) -> SearchResult:
        raise NotImplementedError

    def stats(self, haystack_id: str) -> dict:
        return {}

    def trace(self, haystack_id: str) -> dict:
        return {}

    def teardown(self) -> None:
        pass


def timed(fn):
    t0 = time.perf_counter()
    out = fn()
    return out, int((time.perf_counter() - t0) * 1000)


def load_keys() -> dict[str, str]:
    """The OpenAI and Anthropic keys from the environment, or from the `KEY=value` file named by
    MEMORY_KEYS_FILE when the environment lacks them. Never printed, never written."""
    out = {k: os.environ[k] for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY") if os.environ.get(k)}
    if len(out) == 2:
        return out
    keys_file = os.environ.get("MEMORY_KEYS_FILE", "")
    path = Path(keys_file) if keys_file else None
    if path is not None and path.exists():
        for line in path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("\"'")
                if k in ("OPENAI_KEY", "OPENAI_API_KEY") and v:
                    out.setdefault("OPENAI_API_KEY", v)
                if k in ("ANTHROPIC_KEY", "ANTHROPIC_API_KEY") and v:
                    out.setdefault("ANTHROPIC_API_KEY", v)
    for k, v in out.items():
        os.environ.setdefault(k, v)
    return out


REGISTRY: dict[str, type[System]] = {}


def register(cls: type[System]) -> type[System]:
    REGISTRY[cls.name] = cls
    return cls


ROLE_SPEAKERS = {"user": "user", "human": "user", "assistant": "assistant", "ai": "assistant", "bot": "assistant"}
_NAME = re.compile(r"^[A-Z][a-z]+(?: [A-Z][a-z]+)?$")


def split_messages(content: str) -> list[dict]:
    """A harness document back into chat messages: `{"role", "content", "speaker"}` per message.

    Documents are `speaker: text` lines. LongMemEval speakers are the roles themselves (user/assistant);
    LoCoMo and ConvoMem use names. Any other line — including an assistant's numbered list items such
    as `1. Online Reviews: Check …`, which contain a colon — continues the previous message. When the
    document has role-prefixed lines at all, only role prefixes start a message; otherwise a
    capitalised one- or two-word name does. Named speakers alternate user/assistant starting with
    user; `speaker` keeps the name for adapters that want it.
    """
    lines = content.split("\n")
    heads = [ln.split(":", 1)[0].strip() for ln in lines if ":" in ln]
    role_mode = any(h.lower() in ROLE_SPEAKERS for h in heads)
    # name mode: a speaker recurs; a one-off capitalised word before a colon ("Note: …") does not start a message
    counts: dict[str, int] = {}
    for h in heads:
        if _NAME.match(h):
            counts[h] = counts.get(h, 0) + 1
    top = sorted(counts, key=lambda h: -counts[h])[:2]  # a dialogue has two speakers; one may appear once in a short document
    names = {h for h, n in counts.items() if n >= 2} | set(top)
    msgs: list[dict] = []
    last_role = "assistant"
    for ln in lines:
        head, sep, rest = ln.partition(":")
        h = head.strip()
        starts = bool(sep) and ((h.lower() in ROLE_SPEAKERS) if role_mode else h in names)
        if starts:
            if h.lower() in ROLE_SPEAKERS:
                role, speaker = ROLE_SPEAKERS[h.lower()], None
            else:
                role, speaker = ("assistant" if last_role == "user" else "user"), h
            last_role = role
            msgs.append({"role": role, "content": rest.strip(), "speaker": speaker})
        elif msgs:
            if ln.strip():
                msgs[-1]["content"] += "\n" + ln
        elif ln.strip():
            msgs.append({"role": "user", "content": ln.strip(), "speaker": None})
    return msgs or [{"role": "user", "content": content, "speaker": None}]
