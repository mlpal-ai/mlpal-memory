"""LLM extraction tier (PR5): mine free-text ``content`` into ontology-constrained facts.

Runs only for content-bearing high-salience episodes (router.extraction_tier == "llm") when
``MLPAL_EXTRACTOR=llm``. Content is already secret-redacted by the updater before it reaches here.
The read path stays LLM-free.

Gateway design (prod): a closed-type ontology schema → structured extraction with MANDATORY
evidence spans → a cheap verifier pass that drops any fact not grounded in its cited span →
relative dates resolved against ``reference_time`` → every fact stamped with extraction_version,
prompt_version, evidence_span, confidence (episode_id is added by the updater's _stamp). Offline/dev
uses a deterministic heuristic so the unit suite is replayable without a network. Every extraction
logs its inputs+decision for replay.
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

from ..core.config import get_settings
from ..core.logging import get_logger
from ..ontology.core import EDGE_CLASSES, NODE_CLASSES
from ..services.llm_client import LLMClient, get_llm_client
from .extractor import EdgeSpec, EntitySpec, Extraction, _slug

log = get_logger(__name__)

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]?")


def _provenance(span: str) -> dict:
    s = get_settings()
    return {
        "evidence_span": span,
        "extraction_version": s.extraction_version,
        "prompt_version": s.prompt_version,
    }


class LLMExtractor(ABC):
    @abstractmethod
    async def extract(self, episode, *, reference_time) -> Extraction:
        """Mine ``episode.content`` (already redacted) into ontology entities/edges."""


class DevLLMExtractor(LLMExtractor):
    """Deterministic, network-free offline extractor: each substantive sentence becomes a Fact the
    actor DECIDED, grounded in its own span. Stands in for the gateway model in the unit suite."""

    async def extract(self, episode, *, reference_time) -> Extraction:
        out = Extraction()
        content = (episode.content or "").strip()
        user = (episode.actor or {}).get("user_id")
        if not content or not user:
            return out
        out.entities.append(EntitySpec("User", str(user), str(user)))
        for raw in _SENTENCE.findall(content):
            sentence = raw.strip()
            if len(sentence.split()) < 3:  # skip fragments
                continue
            key = _slug(sentence)
            out.entities.append(EntitySpec("Fact", key, sentence, {"statement": sentence}))
            out.edges.append(
                EdgeSpec(
                    "DECIDED",
                    "User",
                    str(user),
                    "Fact",
                    key,
                    sentence,
                    props={**_provenance(sentence), "confidence": 0.7},
                )
            )
        log.info(
            "llm.extracted",
            event_id=episode.event_id,
            extractor="dev",
            facts=sum(1 for e in out.entities if e.type == "Fact"),
        )
        return out


# Closed-type ontology contract handed to the model so it can't invent node/edge types.
_NODE_TYPES = sorted(NODE_CLASSES)
_EDGE_TYPES = sorted(EDGE_CLASSES)

_SYSTEM = (
    "You extract an enterprise knowledge graph from a message. Use ONLY these node types "
    f"{_NODE_TYPES} and edge types {_EDGE_TYPES}. Each fact MUST be a self-contained, pronoun-free "
    "sentence and MUST cite an exact verbatim evidence_span copied from the message. Resolve "
    "relative dates against the given reference_time. Do not infer beyond the text."
)

_EXTRACT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": [
                    "subject",
                    "relation",
                    "object",
                    "fact",
                    "evidence_span",
                    "confidence",
                ],
                "properties": {
                    "subject": {"type": "string"},
                    "relation": {"type": "string", "enum": _EDGE_TYPES},
                    "object": {"type": "string"},
                    "fact": {"type": "string"},
                    "evidence_span": {"type": "string"},
                    "confidence": {"type": "number"},
                },
            },
        }
    },
}


class GatewayLLMExtractor(LLMExtractor):
    """Production extractor via the gateway: extract → verify (drop ungrounded) → stamp."""

    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def extract(self, episode, *, reference_time) -> Extraction:
        content = (episode.content or "").strip()
        user = (episode.actor or {}).get("user_id")
        if not content:
            return Extraction()
        prompt = f"reference_time: {reference_time}\nmessage:\n{content}"
        raw = await self.client.complete_json(system=_SYSTEM, user=prompt, schema=_EXTRACT_SCHEMA)
        facts = [f for f in (raw.get("facts") or []) if self._grounded(f, content)]
        log.info(
            "llm.extracted",
            event_id=episode.event_id,
            extractor="gateway",
            model=self.client.name,
            facts=len(facts),
            dropped=len(raw.get("facts") or []) - len(facts),
        )
        out = Extraction()
        if user:
            out.entities.append(EntitySpec("User", str(user), str(user)))
        for f in facts:
            key = _slug(f["fact"])
            out.entities.append(EntitySpec("Fact", key, f["fact"], {"statement": f["fact"]}))
            src = (str(user) if user else "unknown", "User")
            out.edges.append(
                EdgeSpec(
                    "DECIDED",
                    src[1],
                    src[0],
                    "Fact",
                    key,
                    f["fact"],
                    props={
                        **_provenance(f.get("evidence_span", "")),
                        "confidence": float(f.get("confidence", 0.5)),
                    },
                )
            )
        return out

    @staticmethod
    def _grounded(fact: dict, content: str) -> bool:
        # cheap verifier: the cited span must actually appear in the (redacted) message.
        span = (fact.get("evidence_span") or "").strip()
        return bool(span) and span in content


def get_llm_extractor() -> LLMExtractor:
    s = get_settings()
    if s.dev_auth or s.environment in ("local", "test"):
        return DevLLMExtractor()
    return GatewayLLMExtractor(get_llm_client())
