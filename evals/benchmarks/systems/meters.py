"""Model-usage meters for competitor adapters (memory v8): wrap the Anthropic and OpenAI SDK calls a
system makes for itself, sync and async, and count calls and tokens. Nothing else changes; the
counts are the write-time and search-time cost columns in the comparison.

`log_to(path)` additionally records every call (request messages, response text, usage) as one
JSON line, up to `MAX_LOGGED_CALLS` full records per run and usage-only lines after that. This is
the trace of *how* a system builds its memory: the prompts it sends at write time (DESIGN.md §7)."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

from .base import Usage

_usage = Usage()
_lock = threading.Lock()
_installed = False
_log_path: Path | None = None
_logged = 0
MAX_LOGGED_CALLS = 300


def log_to(path: Path) -> None:
    global _log_path, _logged
    _log_path = path
    _logged = 0


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(_text_of(getattr(c, "text", None) or (c.get("text") if isinstance(c, dict) else "") or "") for c in content)
    return str(content or "")


def _log(kind: str, kw: dict, out, usage_fields: dict) -> None:
    global _logged
    if _log_path is None:
        return
    rec = {"ts": time.time(), "kind": kind, "model": kw.get("model"), **usage_fields}
    if _logged < MAX_LOGGED_CALLS:
        if kind == "anthropic.messages":
            rec["system"] = _text_of(kw.get("system"))
            rec["messages"] = [{"role": m.get("role"), "content": _text_of(m.get("content"))} for m in kw.get("messages") or []]
            rec["tools"] = [t.get("name") for t in kw.get("tools") or []] or None
            rec["response"] = _text_of(getattr(out, "content", None)) or None
            blocks = [b for b in (getattr(out, "content", None) or []) if getattr(b, "type", "") == "tool_use"]
            rec["tool_use"] = [getattr(b, "input", None) for b in blocks] or None
        elif kind == "openai.chat":
            rec["messages"] = [{"role": m.get("role"), "content": _text_of(m.get("content"))} for m in kw.get("messages") or []]
            ch = (getattr(out, "choices", None) or [None])[0]
            rec["response"] = getattr(getattr(ch, "message", None), "content", None)
        elif kind == "openai.embeddings":
            inp = kw.get("input")
            rec["inputs"] = len(inp) if isinstance(inp, list) else 1
    _logged += 1
    with _lock:
        with _log_path.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
stripped_sampling_kwargs = 0  # how often the SDK shim below had to drop a kwarg


def install() -> None:
    global _installed
    if _installed:
        return
    import anthropic
    import openai

    def _count_llm(out, kind, kw):
        # langchain-anthropic calls `messages.with_raw_response.create`, which hands back the HTTP
        # wrapper; the parsed Message (with usage) is behind .parse()
        parsed = out.parse() if not hasattr(out, "usage") and hasattr(out, "parse") else out
        u = getattr(parsed, "usage", None)
        record_llm(kind, kw, parsed, int(getattr(u, "input_tokens", 0) or getattr(u, "prompt_tokens", 0) or 0),
                   int(getattr(u, "output_tokens", 0) or getattr(u, "completion_tokens", 0) or 0))
        return out

    def _count_embed(out, kw):
        u = getattr(out, "usage", None)
        record_embed(kw, out, int(getattr(u, "total_tokens", 0) or 0))
        return out

    M = anthropic.resources.messages
    o_sync, o_async = M.Messages.create, M.AsyncMessages.create
    # anthropic >= 1.7 dropped temperature/top_p/top_k from Messages.create; mem0 and Graphiti still pass
    # them. Dropping the kwargs means the API default sampling applies to every competitor alike
    # (recorded in CONDITIONS as "sampling: API default"), instead of the adapter failing on write.
    import inspect
    accepted = set(inspect.signature(o_sync).parameters)
    unsupported = {k for k in ("temperature", "top_p", "top_k") if k not in accepted}

    def _strip(kw):
        for k in unsupported:
            if k in kw:
                kw.pop(k)
                global stripped_sampling_kwargs
                stripped_sampling_kwargs += 1
        return kw

    def sync_create(self, *a, **kw):
        return _count_llm(o_sync(self, *a, **_strip(kw)), "anthropic.messages", kw)

    async def async_create(self, *a, **kw):
        return _count_llm(await o_async(self, *a, **_strip(kw)), "anthropic.messages", kw)

    M.Messages.create, M.AsyncMessages.create = sync_create, async_create

    E = openai.resources.embeddings
    e_sync, e_async = E.Embeddings.create, E.AsyncEmbeddings.create

    def sync_embed(self, *a, **kw):
        return _count_embed(e_sync(self, *a, **kw), kw)

    async def async_embed(self, *a, **kw):
        return _count_embed(await e_async(self, *a, **kw), kw)

    E.Embeddings.create, E.AsyncEmbeddings.create = sync_embed, async_embed

    # systems that route their LLM through OpenAI's chat API (or an OpenAI-compatible shim)
    C = openai.resources.chat.completions
    c_sync, c_async = C.Completions.create, C.AsyncCompletions.create

    def sync_chat(self, *a, **kw):
        return _count_llm(c_sync(self, *a, **kw), "openai.chat", kw)

    async def async_chat(self, *a, **kw):
        return _count_llm(await c_async(self, *a, **kw), "openai.chat", kw)

    C.Completions.create, C.AsyncCompletions.create = sync_chat, async_chat
    _installed = True


def record_llm(kind: str, kw: dict, out, input_tokens: int, output_tokens: int) -> None:
    """Count one LLM call; also used by adapters whose system does not go through the SDKs above
    (Cognee reports through litellm callbacks)."""
    with _lock:
        _usage.llm_calls += 1
        _usage.llm_input_tokens += input_tokens
        _usage.llm_output_tokens += output_tokens
    _log(kind, kw, out, {"input_tokens": input_tokens, "output_tokens": output_tokens})


def record_embed(kw: dict, out, tokens: int) -> None:
    with _lock:
        _usage.embed_calls += 1
        _usage.embed_tokens += tokens
    _log("openai.embeddings", kw, out, {"tokens": tokens})


def snapshot() -> Usage:
    with _lock:
        return Usage(**_usage.to_dict())


def delta(before: Usage) -> Usage:
    now = snapshot()
    return Usage(llm_calls=now.llm_calls - before.llm_calls, llm_input_tokens=now.llm_input_tokens - before.llm_input_tokens,
                 llm_output_tokens=now.llm_output_tokens - before.llm_output_tokens, embed_calls=now.embed_calls - before.embed_calls,
                 embed_tokens=now.embed_tokens - before.embed_tokens)
