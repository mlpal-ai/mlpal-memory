"""Session distiller (v3): one small-model call per session → typed insights.

This is the "AI helps form the memory" tier with the cost model made explicit:
distillation runs ONCE per session document at commit time (never per turn), through
the assistants gateway on a haiku-class model, and only for session sources
(``distill_sources``). A 15k-token session distilled by a small model costs on the
order of a cent; per-turn folding stays deterministic and free.

Same trust rules as the LLM extractor: closed insight vocabulary, MANDATORY verbatim
evidence spans (ungrounded insights are dropped), provenance stamps. Offline/dev uses
a deterministic heuristic so the suite replays without a network.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from ..core.config import get_settings
from ..core.logging import get_logger
from ..services.llm_client import LLMClient, get_llm_client
from .extractor import EdgeSpec, EntitySpec, Extraction, _slug

log = get_logger(__name__)

INSIGHT_KINDS = {
    "convention": "Convention",
    "decision": "Decision",
    "gotcha": "Gotcha",
    "howto": "HowTo",
    "preference": "Preference",
}
MAX_INSIGHTS = 10

_SCHEMA = {
    "type": "object",
    "properties": {
        "insights": {
            "type": "array",
            "maxItems": MAX_INSIGHTS,
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": sorted(INSIGHT_KINDS)},
                    "name": {"type": "string", "description": "one-line insight, imperative"},
                    "detail": {"type": "string"},
                    "evidence_span": {
                        "type": "string",
                        "description": "verbatim quote from the transcript grounding this",
                    },
                },
                "required": ["kind", "name", "evidence_span"],
            },
        }
    },
    "required": ["insights"],
}

_SYSTEM = f"""You distill a coding session transcript into durable insights a future
session in the same workspace would genuinely benefit from knowing. Extract ONLY:
- convention: standing rules/styles this codebase or person follows
- decision: choices made, with their rationale
- gotcha: landmines/failure modes discovered the hard way
- howto: working procedures (commands, order, preconditions)
- preference: the person's durable preferences
Rules: max {MAX_INSIGHTS}; skip anything session-specific or trivially re-derivable from
the code; every insight MUST carry a verbatim evidence_span copied exactly from the
transcript; emit strict JSON for the given schema and nothing else."""


def _to_extraction(insights: list[dict], episode) -> Extraction:
    """Grounded insights → typed nodes + LEARNED_IN (session) / APPLIES_TO (workspace)."""
    out = Extraction()
    content = episode.content or ""
    session_key = episode.source_ref or episode.event_id
    out.entities.append(EntitySpec("Chat", _slug(session_key), f"session {session_key}"))
    if episode.workspace:
        out.entities.append(EntitySpec("Workspace", episode.workspace, episode.workspace))
    s = get_settings()
    kept = 0
    for ins in insights[:MAX_INSIGHTS]:
        kind = INSIGHT_KINDS.get(str(ins.get("kind", "")).lower())
        name = (ins.get("name") or "").strip()
        span = (ins.get("evidence_span") or "").strip()
        if not kind or not name or not span or span not in content:
            continue  # ungrounded or malformed → dropped, never stored
        key = _slug(name)
        props = {
            "evidence_span": span,
            "extraction_version": s.extraction_version,
            "prompt_version": s.prompt_version,
            "kind": kind.lower(),
        }
        out.entities.append(
            EntitySpec(kind, key, name, {"detail": (ins.get("detail") or "").strip()})
        )
        out.edges.append(
            EdgeSpec(
                "LEARNED_IN", kind, key, "Chat", _slug(session_key),
                f"learned: {name}", props=props,
            )
        )
        if episode.workspace:
            out.edges.append(
                EdgeSpec(
                    "APPLIES_TO", kind, key, "Workspace", episode.workspace,
                    f"{name} applies to {episode.workspace}", props={"kind": kind.lower()},
                )
            )
        kept += 1
    log.info("distill.done", event_id=episode.event_id, kept=kept, offered=len(insights))
    return out


class Distiller(ABC):
    @abstractmethod
    async def distill(self, episode) -> Extraction: ...


class DevDistiller(Distiller):
    """Deterministic offline distiller: imperative-marker sentences become insights.
    Stands in for the gateway model in the unit suite (replayable, network-free)."""

    _MARKERS = {
        "always": "convention",
        "never": "convention",
        "must": "convention",
        "decided": "decision",
        "gotcha": "gotcha",
        "beware": "gotcha",
        "run": "howto",
        "prefer": "preference",
    }
    _SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")

    async def distill(self, episode) -> Extraction:
        insights = []
        for raw in self._SENTENCE.findall(episode.content or ""):
            sentence = raw.strip()
            words = sentence.lower().split()
            if len(words) < 4:
                continue
            for marker, kind in self._MARKERS.items():
                if marker in words:
                    insights.append(
                        {"kind": kind, "name": sentence[:120], "evidence_span": sentence}
                    )
                    break
        return _to_extraction(insights, episode)


class GatewayDistiller(Distiller):
    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or get_llm_client()

    async def distill(self, episode) -> Extraction:
        result = await self.client.complete_json(
            system=_SYSTEM,
            user=(episode.content or "")[:60_000],
            schema=_SCHEMA,
        )
        return _to_extraction(list(result.get("insights") or []), episode)


def get_distiller() -> Distiller:
    s = get_settings()
    if s.environment in ("test",) or s.extractor != "llm":
        return DevDistiller()
    return GatewayDistiller()
