"""memory.claim — the write path for a HOP's memory contract (hop-v1.1 §9.3).

A claim is one addressed, typed assertion: ``{kind, topic, key, value, evidence_ids, hop, prompt_sha,
origin, supersedes}`` riding an episode with ``action_type = "memory.claim"``. The fold maps each
kind onto the store's existing primitives, so nothing new is stored and every existing guarantee
(bi-temporal supersession, dedup, scope predicate, provenance) applies:

    state       Metric(topic:key) --HAS_VALUE(functional)--> MetricValue   → one current value
    preference  same shape under a ``pref:`` anchor, at the writer's user scope (the envelope's)
    learning    Fact keyed by a slug of the text; the resolver dedups by meaning (cosine 0.86)
                and the actor DECIDED it (provenance: evidence ids, hop, prompt hash, origin)
    deviation   nothing to extract here: the distiller reads the episode directly (§9.2)
    record      nothing to extract: the episode IS the record

Grounding is stamped, never faked: ``grounded`` is true iff ``evidence_ids`` is non-empty. The MCP
write tool refuses an ungrounded claim; the engine's mirror of legacy Memorize topics (which carry
no evidence yet) is accepted and stamped ungrounded so trust can tell them apart.
"""

from __future__ import annotations

from datetime import UTC, datetime

from typing import Any

from .extractor import EdgeSpec, EntitySpec, Extraction, _slug

CLAIM_ACTION = "memory.claim"
KINDS = ("state", "record", "learning", "preference", "deviation")
_MAX_VALUE = 400


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        import json

        return json.dumps(value, sort_keys=True, separators=(",", ":"))
    return str(value)


def anchor_key(kind: str, topic: str, key: str) -> str:
    """The stable anchor a keyed claim supersedes under: ``state:<topic>:<key>`` / ``pref:<topic>:<key>``."""
    prefix = "pref" if kind == "preference" else "state"
    return f"{prefix}:{topic}:{key}"


def valid_until(payload: dict) -> datetime | None:
    """memory v7 WP3: a writer-set expiry ("I have an exam tomorrow" ends when the date passes).
    ISO-8601; a malformed value is ignored rather than guessed, and the claim lands without one."""
    raw = payload.get("valid_until")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def claim_provenance(payload: dict) -> dict:
    ev = [str(x) for x in (payload.get("evidence_ids") or []) if str(x).strip()]
    vu = valid_until(payload)
    return {
        "topic": payload.get("topic"),
        "claim_key": payload.get("key"),
        "kind": payload.get("kind"),
        "evidence_ids": ev,
        "grounded": bool(ev),
        **({"valid_until": vu.isoformat()} if vu else {}),
        **({"phase": "build"} if payload.get("phase") == "build" else {}),
        # memory v10: a fact the owner wants in every session (a credit, a hard limit) — rendered in a
        # reserved slice of the projection regardless of rank; leaves when its valid_until passes
        **({"pinned": True} if payload.get("pinned") else {}),
        **{k: payload[k] for k in ("hop", "prompt_sha", "origin", "supersedes", "run_id", "half_life") if payload.get(k)},
    }


def entity_hints(payload: dict) -> list[dict]:
    """memory v7 WP7: a claim may name the domain entities it is about (`entities: [{type, key,
    name?, aliases?}]`); the fold creates or resolves them (by key or any alias) and links the claim's
    fact or value to them with MENTIONS, so 'what depends on this volume' is a walk."""
    out = []
    for e in payload.get("entities") or []:
        if isinstance(e, dict) and e.get("type") and e.get("key"):
            out.append({"type": str(e["type"])[:40], "key": str(e["key"])[:200], "name": str(e.get("name") or e["key"])[:200],
                        "aliases": [str(a)[:200] for a in (e.get("aliases") or []) if a]})
    return out[:10]


def extract_claim(episode) -> Extraction:
    """Deterministic. Returns an empty extraction for kinds the fold does not model as facts."""
    out = Extraction()
    p = episode.payload or {}
    kind = p.get("kind")
    topic = p.get("topic")
    if episode.action_type != CLAIM_ACTION or kind not in KINDS or not topic:
        return out
    prov = claim_provenance(p)
    actor = (episode.actor or {}).get("user_id") or (episode.actor or {}).get("agent_id") or "agent"
    # hop-v1.1 §9.3: topic ids may carry `{me}`; the service knows the actor, so it resolves the
    # placeholder here rather than trusting the writer (engine or model) to have substituted it
    topic = topic.replace("{me}", str(actor))
    if kind in ("state", "preference"):
        key = str(p.get("key") or "")
        value = _text(p.get("value"))[:_MAX_VALUE]
        if not key or not value:
            return out
        a = anchor_key(kind, topic, key)
        vk = f"{a}={value}"
        out.entities.append(EntitySpec(type="Metric", key=a, name=f"{topic}/{key}", props={"topic": topic, "claim_key": key, "kind": kind,
                                                                                            **({"half_life": p["half_life"]} if p.get("half_life") else {})}))
        out.entities.append(EntitySpec(type="MetricValue", key=vk, name=f"{topic}/{key} = {value}", props={"value": value, "unit": kind, **prov}))
        out.edges.append(EdgeSpec(type="HAS_VALUE", src_type="Metric", src_key=a, dst_type="MetricValue", dst_key=vk,
                                  fact=f"{topic}/{key} = {value}", functional=True, props={"value": value, **prov}))
        _link_entities(out, p, "MetricValue", vk, f"{topic}/{key}")
        return out
    if kind == "learning":
        text = _text(p.get("value")).strip()
        if len(text.split()) < 3:
            return out
        fk = _slug(text)
        out.entities.append(EntitySpec(type="User", key=str(actor), name=str(actor)))
        out.entities.append(EntitySpec(type="Fact", key=fk, name=text, props={"statement": text, **prov}))
        out.edges.append(EdgeSpec(type="DECIDED", src_type="User", src_key=str(actor), dst_type="Fact", dst_key=fk,
                                  fact=text, props={"confidence": 0.7 if prov["grounded"] else 0.4, **prov}))
        _link_entities(out, p, "Fact", fk, text[:80])
        return out
    return out   # record, deviation: the episode itself is the memory


def _link_entities(out: Extraction, payload: dict, src_type: str, src_key: str, about: str) -> None:
    for ent in entity_hints(payload):
        out.entities.append(EntitySpec(type=ent["type"], key=ent["key"], name=ent["name"],
                                       props={"aliases": ent["aliases"], "extension": True} if ent["aliases"] else {"extension": True}))
        out.edges.append(EdgeSpec(type="MENTIONS", src_type=src_type, src_key=src_key, dst_type=ent["type"], dst_key=ent["key"],
                                  fact=f"{about} mentions {ent['type']} {ent['key']}", functional=False, props={}))


_ABSENT = ("absent", "ended", "deleted", "gone", "terminated", "removed")


def absence_target(episode) -> str | None:
    """The key of an entity a state claim declares gone, else None. A watch topic's key is the
    target (`infra/state/watch/{target}`); the value must open with an absence word or the payload
    must say `ended: true`. Only state claims: a learning that mentions 'deleted' is not an end."""
    p = episode.payload or {}
    if episode.action_type != CLAIM_ACTION or p.get("kind") != "state":
        return None
    key = str(p.get("key") or "").strip()
    if not key:
        return None
    value = _text(p.get("value")).strip().lower()
    if p.get("ended") is True or value.split(" ", 1)[0].rstrip(".,;:") in _ABSENT:
        return key
    return None


def presence_target(episode) -> str | None:
    """The key of an entity a state claim on a watch topic observes as present again, else None."""
    p = episode.payload or {}
    if episode.action_type != CLAIM_ACTION or p.get("kind") != "state":
        return None
    key = str(p.get("key") or "").strip()
    topic = str(p.get("topic") or "")
    if not key or "/watch/" not in topic:
        return None
    value = _text(p.get("value")).strip().lower()
    return key if value.split(" ", 1)[0].rstrip(".,;:") in ("present", "ready", "running", "available", "ok", "healthy") else None
