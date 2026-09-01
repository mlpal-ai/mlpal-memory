"""The memory hop — a bounded, audited retrieval loop above the deterministic core.

The user picks the route explicitly (cost is a product surface, not a hidden
implementation choice):

  mode=packet       L0: deterministic, ~150ms, $0 — the primitive and the default
  mode=synthesized  L1: one model call composes FROM one packet
  mode=hop          L2: this module — up to ``max_hops`` rounds of
                    (assess → reformulate with the corpus's own vocabulary →
                    retrieve again) before answering. Targets the x5-measured
                    failure class: vocabulary-gap retrieval misses (57%
                    derivable ceiling at ANY single-shot depth).

Audit properties, by construction:
- every hop retrieves through the same deterministic pipeline (scope, consent,
  as-of, agent-mode all apply) — the loop adds reasoning, never new provenance;
- the final answer's citations are server-verified against the UNION of
  everything actually retrieved (invented citations are counted and stripped);
- the full hop trace (queries tried per hop) returns with the answer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .llm_client import get_llm_client
from .synthesis import SYNTH_SYSTEM

CIT_RE = re.compile(r"memory://[a-z]+/[A-Za-z0-9-]+")

HOP_SYSTEM = """You operate a memory-retrieval loop. Given a question and the memory
packets retrieved so far, decide ONE of:
- action "answer": the packets contain enough to answer. Provide the final answer
  following the answer rules below.
- action "search": the packets do NOT contain the answer, but suggest the corpus uses
  different vocabulary. Provide 1-2 NEW search queries using terms you SAW in the
  packets (names, identifiers, synonyms) or plausible corpus vocabulary. Never repeat
  a previous query.

Answer rules (when action=answer):
""" + SYNTH_SYSTEM

HOP_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["answer", "search"]},
        "answer": {"type": "string"},
        "queries": {"type": "array", "items": {"type": "string"}, "maxItems": 2},
    },
    "required": ["action"],
    "additionalProperties": False,
}


@dataclass
class HopResult:
    answer: str
    hops: int  # model calls spent
    trace: list[str] = field(default_factory=list)  # queries executed, in order
    invented_citations: int = 0


def enforce_citations(answer: str, valid_ids: set[str]) -> tuple[str, int]:
    """Server-side grounding: strip citations that were never retrieved (x5: 1/14
    invented per naive arm). Verification is enforcement, not a prompt request."""
    invented = [c for c in set(CIT_RE.findall(answer)) if c not in valid_ids]
    for c in invented:
        answer = answer.replace(f"[{c}]", "[uncited]").replace(f"({c})", "(uncited)")
        answer = answer.replace(c, "uncited")
    return answer, len(invented)


async def run_memory_hop(
    *,
    query: str,
    fetch_packet,  # async (q: str) -> str  — the deterministic L0 read, policy-applied
    max_hops: int = 3,
    model: str | None = None,
    on_event=None,  # async (dict) -> None — live trace for the streaming UI
) -> HopResult:
    async def emit(ev: dict) -> None:
        if on_event is not None:
            await on_event(ev)

    packets: list[str] = [await fetch_packet(query)]
    executed = [query]
    await emit({"type": "retrieved", "hop": 0, "query": query,
                "citations": len(set(CIT_RE.findall(packets[0])))})
    client = get_llm_client()
    answer = ""
    hops_spent = 0

    for _ in range(max_hops):
        context = "\n\n===== packet =====\n\n".join(packets)[-40_000:]
        decision = await client.complete_json(
            system=HOP_SYSTEM,
            user=f"Question: {query}\n\nPackets retrieved so far:\n\n{context}",
            schema=HOP_SCHEMA,
            max_tokens=600,
        )
        hops_spent += 1
        if decision.get("action") == "answer" and (decision.get("answer") or "").strip():
            answer = decision["answer"].strip()
            await emit({"type": "deciding", "hop": hops_spent, "action": "answer"})
            break
        await emit({"type": "deciding", "hop": hops_spent, "action": "search",
                    "queries": decision.get("queries") or []})
        new_qs = [
            q.strip() for q in (decision.get("queries") or []) if q.strip() and q not in executed
        ][:2]
        if not new_qs:  # model stalled — stop looping, compose from what we have
            break
        # early-stop (P0.3): if this hop's retrievals surface ZERO new citations, more
        # reformulation cannot help — x5r3 traces showed unanswerable questions burning
        # the whole budget (5–7 queries) before abstaining.
        before = set()
        for p in packets:
            before |= set(CIT_RE.findall(p))
        for nq in new_qs:
            executed.append(nq)
            packets.append(await fetch_packet(nq))
            await emit({"type": "retrieved", "hop": hops_spent, "query": nq,
                        "citations": len(set(CIT_RE.findall(packets[-1])))})
        after = set()
        for p in packets:
            after |= set(CIT_RE.findall(p))
        if after == before:
            await emit({"type": "early_stop", "hop": hops_spent,
                        "reason": "no new citations"})
            break

    if not answer:
        # budget exhausted or stalled: one final grounded compose over everything
        await emit({"type": "composing", "hop": hops_spent + 1})
        context = "\n\n===== packet =====\n\n".join(packets)[-40_000:]
        text, _ = await client.complete_text(
            system=SYNTH_SYSTEM,
            user=f"Question: {query}\n\nMemory packet:\n\n{context}",
            model=model,
            max_tokens=500,
        )
        answer = text.strip()
        hops_spent += 1

    valid = set()
    for p in packets:
        valid |= set(CIT_RE.findall(p))
    answer, invented = enforce_citations(answer, valid)
    return HopResult(answer=answer, hops=hops_spent, trace=executed, invented_citations=invented)
