"""Answer synthesis — the optional LLM layer ABOVE the deterministic packet.

The packet stays the system's ground truth: retrieval, ranking, provenance,
contested labels, and gaps are all decided deterministically first. Synthesis
is one gateway call that composes a direct natural-language answer FROM the
packet, citing only the packet's memory:// ids. It never sees the store — only
the packet — so it cannot cite anything retrieval didn't surface, and an empty
packet short-circuits to the packet's own abstention (zero model cost).

This is the x5 experiment surface ("Perplexity for org memory"); whether it
becomes the default read mode is decided by measurement, not preference.
"""

from __future__ import annotations

from .llm_client import get_llm_client

SYNTH_SYSTEM = """You answer a question using ONLY the memory packet provided. Rules:
- FIRST scan the ENTIRE packet (facts AND every Evidence passage) for the specific
  value the question asks about — a number, port, path, model tag, name, date. The
  answer is often a detail inside a long evidence passage, not in the fact list.
- COPY specific values VERBATIM from the packet (exact numbers, exact identifiers,
  exact paths). Never paraphrase a value; never round; never generalize a specific.
- Answer directly in 1-3 sentences, the specific value first.
- EVERY factual claim must carry a citation in the form [memory://...] copied exactly
  from the packet. Never invent citations; never use outside knowledge.
- If the packet's facts conflict (Contested section), present both sides as contested.
- If the packet does not contain the answer, say exactly what is missing — do not guess.
- If the packet marks something as ruled out or from a failed attempt, do not present
  it as true.
Output plain markdown, no heading, no preamble."""


async def synthesize_answer(
    *, query: str, packet_markdown: str, model: str | None = None
) -> tuple[str, dict]:
    """Return (answer_markdown, usage). Caller guarantees the packet is non-empty."""
    text, usage = await get_llm_client().complete_text(
        system=SYNTH_SYSTEM,
        user=f"Question: {query}\n\nMemory packet:\n\n{packet_markdown[:24_000]}",
        model=model,
        max_tokens=500,
    )
    return text.strip(), usage
