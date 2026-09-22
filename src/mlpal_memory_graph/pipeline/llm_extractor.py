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

import json

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


_FACTS_SYSTEM = (
    "You turn one conversation session into a list of self-contained facts for a long-term memory. "
    "Each fact is ONE sentence a stranger could understand on its own: use names, never pronouns; "
    "keep exact numbers, amounts, durations, dates, titles and product or place names. Cover the whole "
    "session, in order: first everything the user states about themselves, their life, plans, "
    "possessions, preferences and past events; then the specific things the assistant provided that "
    "the user may later ask to be reminded of (a recommendation list, a schedule row, a number it "
    "computed, a name it suggested) — for those set speaker to \"assistant\". Resolve relative time "
    "expressions (\"yesterday\", \"last month\", \"two weeks ago\") against the session date and put "
    "the resolved calendar date in event_date (YYYY-MM-DD, or null when the text gives no date). Every "
    "fact MUST cite an evidence_span copied verbatim from the session. Do not infer beyond the text; "
    "do not summarise the assistant's general advice."
)
_FACTS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fact", "speaker", "event_date", "evidence_span"],
                "properties": {
                    "fact": {"type": "string"},
                    "speaker": {"type": "string", "enum": ["user", "assistant"]},
                    "event_date": {"type": ["string", "null"]},
                    "evidence_span": {"type": "string"},
                },
            },
        }
    },
}
_WS = re.compile(r"\s+")


class ConversationFactExtractor(LLMExtractor):
    """memory v8 candidate C3/C5 (``MLPAL_EXTRACTOR=facts``): one call per content episode (a
    conversation session) producing dated, self-contained fact sentences stored as Fact nodes
    *beside* the verbatim passages — the shape the head-to-head traces showed winning (supermemory:
    memories + chunks; Memobase: dated memo lines). The date rides in the node name so it is in the
    embedded text and in what a reader sees ("[2023-03-07] User volunteered at the Coastal Cleanup").
    Unlike the enterprise-ontology extractor above, facts are not typed triples and the assistant's
    specifics are kept (LEARNINGS L3, L21): the fold's supersession still applies to keyed values;
    plain facts accumulate, so a within-session update keeps both statements."""

    def __init__(self, client: LLMClient, max_tokens: int = 6000) -> None:
        self.client = client
        self.max_tokens = max_tokens

    @staticmethod
    def _grounded(span: str, content_norm: str) -> bool:
        s = _WS.sub(" ", (span or "").strip())
        return len(s) >= 8 and s in content_norm

    async def extract(self, episode, *, reference_time) -> Extraction:
        content = (episode.content or "").strip()
        out = Extraction()
        if not content:
            return out
        user = (episode.actor or {}).get("user_id")
        session_date = reference_time.date().isoformat() if hasattr(reference_time, "date") else str(reference_time)[:10]
        prompt = f"session_date: {session_date}\nsession:\n{content}"
        raw = await self.client.complete_json(system=_FACTS_SYSTEM, user=prompt, schema=_FACTS_SCHEMA, max_tokens=self.max_tokens)
        content_norm = _WS.sub(" ", content)
        facts = [f for f in (raw.get("facts") or []) if isinstance(f, dict) and f.get("fact") and self._grounded(f.get("evidence_span", ""), content_norm)]
        log.info("llm.extracted", event_id=getattr(episode, "event_id", None), extractor="facts", model=self.client.name,
                 facts=len(facts), dropped=len(raw.get("facts") or []) - len(facts))
        if user:
            out.entities.append(EntitySpec("User", str(user), str(user)))
        uri = (getattr(episode, "payload", None) or {}).get("uri")
        for f in facts:
            when = f.get("event_date") or session_date
            name = f"[{when}] {f['fact'].strip()}"
            key = _slug(name)
            props = {"statement": f["fact"].strip(), "speaker": f.get("speaker") or "user", "event_date": f.get("event_date"),
                     "mention_date": session_date, **({"source_uri": uri} if uri else {})}
            out.entities.append(EntitySpec("Fact", key, name, props))
            src = (str(user) if user else "unknown", "User")
            out.edges.append(EdgeSpec("DECIDED", src[1], src[0], "Fact", key, f["fact"].strip(),
                                      props={**_provenance(f.get("evidence_span", "")), "confidence": 0.8}))
        return out


_TOPICS_SYSTEM = (
    "You turn one conversation session into facts for a long-term memory, each attached to a topic. "
    "A fact is ONE sentence a stranger could understand on its own: names, never pronouns; keep exact "
    "numbers, amounts, durations, dates, titles, product and place names. Cover what the user states "
    "about their life, possessions, plans, purchases, activities and preferences, and the specific "
    "things the assistant provided that the user may ask to be reminded of (speaker \"assistant\"). "
    "topic: a short noun phrase for the running subject the fact belongs to in the user's life "
    "('plants acquired', 'bike expenses', 'movies to watch', 'charity runs', 'work projects'); when "
    "known_topics lists the same subject, reuse that exact name. current_state lists, per known "
    "topic, the events already counted: a fact that mentions one of those events again — the same "
    "purchase, acquisition, run or item, even in other words or with a different date — gets op "
    "'none' (never count an event twice). op: 'add' when the fact is one more NEW event, purchase, "
    "item or occurrence that adds to the topic's tally; 'remove' when something leaves it; 'set' only "
    "when the user states the topic's current total outright ('I now have 25 titles on my list'); "
    "'none' otherwise. quantity: only for an additive contribution to the topic's running total (the "
    "amount spent, hours played, items acquired) with its unit — 'items' for a count, or 'USD', "
    "'hours', 'km', 'days' … — and null for a price, a limit, a target, an approval amount or any "
    "number that is not accumulated. Resolve relative time "
    "('yesterday', 'last month', 'three weeks ago') against the session date into event_date "
    "(YYYY-MM-DD, or null when the text gives no date). Every fact MUST cite an evidence_span copied "
    "verbatim from the session. Do not infer beyond the text; do not summarise general advice."
)
_TOPICS_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["facts"],
    "properties": {
        "facts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["fact", "speaker", "topic", "op", "quantity", "event_date", "evidence_span"],
                "properties": {
                    "fact": {"type": "string"},
                    "speaker": {"type": "string", "enum": ["user", "assistant"]},
                    "topic": {"type": "string"},
                    "op": {"type": "string", "enum": ["add", "remove", "set", "none"]},
                    "quantity": {
                        "type": ["object", "null"],
                        "additionalProperties": False,
                        "required": ["value", "unit"],
                        "properties": {"value": {"type": ["number", "null"]}, "unit": {"type": ["string", "null"]}},
                    },
                    "event_date": {"type": ["string", "null"]},
                    "evidence_span": {"type": "string"},
                },
            },
        }
    },
}


class ConversationTopicExtractor(ConversationFactExtractor):
    """memory v9 C4 (``MLPAL_EXTRACTOR=topics``): one call per session, facts attached to topics,
    folded into one running state per topic (`pipeline/topic_state.py`) and stored as keyed state
    (`state:conv/<topic>:<user>`) so the claim fold's supersession keeps each topic's tally current.
    No Fact nodes: the reader sees one complete line per topic instead of a partial list (v8 L31)."""

    async def extract(self, episode, *, reference_time, known_topics: list[str] | None = None,
                      current_states: dict[str, tuple[str, dict]] | None = None) -> Extraction:
        from .claims import anchor_key
        from .topic_state import fold_topic, render_topic_state, topic_slug

        content = (episode.content or "").strip()
        out = Extraction()
        if not content:
            return out
        user = str((episode.actor or {}).get("user_id") or "unknown")
        session_date = reference_time.date().isoformat() if hasattr(reference_time, "date") else str(reference_time)[:10]
        known = sorted(known_topics or [])[:60]
        # the events already counted per topic, so a re-mention in a later session is not counted twice
        current = {label: [f"{i.get('date')} {i.get('fact')}" for i in (st.get("items") or []) if i.get("op") != "none"][-12:]
                   for label, st in (current_states or {}).values()}
        current = {k: v for k, v in current.items() if v}
        prompt = (f"session_date: {session_date}\nknown_topics: {json.dumps(known)}\ncurrent_state: {json.dumps(current)}\n"
                  f"session:\n{content}")
        raw = await self.client.complete_json(system=_TOPICS_SYSTEM, user=prompt, schema=_TOPICS_SCHEMA, max_tokens=self.max_tokens)
        content_norm = _WS.sub(" ", content)
        facts = [f for f in (raw.get("facts") or []) if isinstance(f, dict) and f.get("fact") and f.get("topic")
                 and self._grounded(f.get("evidence_span", ""), content_norm)]
        log.info("llm.extracted", event_id=getattr(episode, "event_id", None), extractor="topics", model=self.client.name,
                 facts=len(facts), dropped=len(raw.get("facts") or []) - len(facts))
        uri = (getattr(episode, "payload", None) or {}).get("uri")
        by_topic: dict[str, list[dict]] = {}
        names: dict[str, str] = {}
        for f in facts:
            slug = topic_slug(f["topic"])
            names.setdefault(slug, (current_states or {}).get(slug, (f["topic"].strip(),))[0])
            q = f.get("quantity") or {}
            by_topic.setdefault(slug, []).append({"fact": f["fact"].strip(), "date": f.get("event_date") or session_date, "op": f.get("op") or "none",
                                                  "qty": q.get("value") if isinstance(q, dict) else None,
                                                  "unit": q.get("unit") if isinstance(q, dict) else None, **({"source_uri": uri} if uri else {})})
        for slug, items in by_topic.items():
            prev = (current_states or {}).get(slug, (None, None))[1]
            st = fold_topic(prev, items, session_date)
            if not st["count"] and not st["sums"] and not st.get("set"):
                continue  # nothing to tally yet ("Premiere Pro learning: 0 items" would only mislead a reader)
            topic = f"conv/{slug}"
            a = anchor_key("state", topic, user)
            value = render_topic_state(names[slug], st)
            vk = f"{a}={value}"
            prov = {"grounded": True, "evidence_span": items[0]["fact"][:120], **({"source_uri": uri} if uri else {})}
            out.entities.append(EntitySpec("Metric", a, f"{topic}/{user}", {"topic": topic, "claim_key": user, "kind": "state", "label": names[slug]}))
            out.entities.append(EntitySpec("MetricValue", vk, value, {"value": value, "unit": "state", "topic": names[slug], **st, **prov}))
            out.edges.append(EdgeSpec("HAS_VALUE", "Metric", a, "MetricValue", vk, value, functional=True, props={"value": value, **prov}))
        return out


def get_llm_extractor() -> LLMExtractor:
    from ..services.llm_client import llm_backend

    if llm_backend() == "dev":
        return DevLLMExtractor()
    mode = get_settings().extractor
    if mode == "facts":
        return ConversationFactExtractor(get_llm_client())
    if mode == "topics":
        return ConversationTopicExtractor(get_llm_client())
    return GatewayLLMExtractor(get_llm_client())
