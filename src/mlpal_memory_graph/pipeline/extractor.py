"""Ontology-driven extraction: episode -> (entities, edges).

v1 is rule-based and deterministic: it maps the actor/subject/org of a typed episode onto
core ontology nodes and edges. This is the cheap, robust ``deterministic`` extraction tier. An
LLM extractor (constrained to the ontology) slots in for the ``llm`` tier to mine free-text
content (see pipeline/router.py::extraction_tier); it must return the same (entities, edges) shape.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Protocol

from ..ontology.core import EDGE_CLASSES


def _functional(edge_type: str) -> bool:
    """Whether this relation is single-valued per (src, type) — the supersession fast-path.

    Sourced from the ontology so there's one place that declares functionality (e.g. HAS_STATUS).
    """
    ec = EDGE_CLASSES.get(edge_type)
    return bool(ec and ec.functional)


@dataclass
class EntitySpec:
    type: str
    key: str
    name: str
    props: dict = field(default_factory=dict)


@dataclass
class EdgeSpec:
    type: str
    src_type: str
    src_key: str
    dst_type: str
    dst_key: str
    fact: str | None = None
    functional: bool = False
    # LLM extraction stamps provenance here (rule extraction leaves it empty): evidence_span,
    # extraction_version, prompt_version, confidence. Persisted to edge.props.
    props: dict = field(default_factory=dict)


@dataclass
class Extraction:
    entities: list[EntitySpec] = field(default_factory=list)
    edges: list[EdgeSpec] = field(default_factory=list)
    # relations to *end* (a stative relation that was terminated, e.g. a member removed). The
    # updater closes the matching open edge at the episode time — invalidate, never delete.
    invalidations: list[EdgeSpec] = field(default_factory=list)


class EpisodeLike(Protocol):
    org_id: str | None
    actor: dict
    subject: dict
    action_type: str
    payload: dict
    content: str | None


def _slug(text: str, limit: int = 200) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:limit] or "fact"


def extract(ep: EpisodeLike) -> Extraction:
    out = Extraction()
    seen: set[tuple[str, str]] = set()

    def add_entity(type_: str, key: str | None, name: str | None = None, props: dict | None = None):
        if not key or (type_, key) in seen:
            return
        seen.add((type_, key))
        out.entities.append(EntitySpec(type_, str(key), name or str(key), props or {}))

    actor = ep.actor or {}
    subj = ep.subject or {}
    org = ep.org_id
    user = actor.get("user_id")

    # canonical entities (authoritative IDs ground resolution)
    add_entity("Org", org)
    add_entity("User", user)
    add_entity("Team", actor.get("team_id"))
    add_entity("Agent", subj.get("agent_id"))
    add_entity("MCPServer", subj.get("server_id"))
    add_entity("Skill", subj.get("skill_id"))
    add_entity("Chat", subj.get("chat_id"))
    add_entity("Tool", subj.get("tool_name"))
    add_entity("Artifact", subj.get("artifact_ref"))

    # baseline org membership
    if user and org:
        out.edges.append(EdgeSpec("MEMBER_OF", "User", user, "Org", org))

    at = ep.action_type or ""

    def link(etype, st, sk, dt, dk, fact=None):
        # functionality is an ontology property of the relation, not a per-call choice
        if sk and dk:
            out.edges.append(EdgeSpec(etype, st, str(sk), dt, str(dk), fact, _functional(etype)))

    def status(stype, skey, value):
        """Record the current lifecycle status of a subject (a functional HAS_STATUS edge).

        Distinct status values are distinct dst nodes, so a later status (running→stopped)
        supersedes the prior one via the deterministic invalidation fast-path (updater).
        """
        if skey:
            add_entity("Status", value, value)
            link("HAS_STATUS", stype, skey, "Status", value, f"{stype} {skey} is {value}")

    if at in ("agent.created", "agent.updated"):
        link("AUTHORED", "User", user, "Agent", subj.get("agent_id"), f"{user} authored agent")
    elif at == "agent.deployed":
        link("DEPLOYED", "User", user, "Agent", subj.get("agent_id"), f"{user} deployed agent")
        status("Agent", subj.get("agent_id"), "running")
    elif at == "agent.stopped":
        status("Agent", subj.get("agent_id"), "stopped")
    elif at == "mcp.created":
        link("AUTHORED", "User", user, "MCPServer", subj.get("server_id"), f"{user} authored MCP")
    elif at == "mcp.deployed":
        server_id = subj.get("server_id")
        link("DEPLOYED", "User", user, "MCPServer", server_id, f"{user} deployed MCP")
        status("MCPServer", server_id, "running")
        # a deployed server exposes its tools (from the tailed tools_metadata)
        for tool in (ep.payload or {}).get("tools", []):
            add_entity("Tool", tool)
            link("EXPOSES", "MCPServer", server_id, "Tool", tool)
    elif at in ("mcp.stopped", "mcp.started"):
        status("MCPServer", subj.get("server_id"), "stopped" if at == "mcp.stopped" else "running")
    elif at == "mcp.tool.invoked":
        link("INVOKED", "User", user, "Tool", subj.get("tool_name"), f"{user} invoked tool")
        link("EXPOSES", "MCPServer", subj.get("server_id"), "Tool", subj.get("tool_name"))
    elif at in ("skill.created", "skill.published"):
        link("AUTHORED", "User", user, "Skill", subj.get("skill_id"), f"{user} {at}")
    elif at in ("skill.installed", "skill.loaded", "skill.used"):
        link("USED_SKILL", "User", user, "Skill", subj.get("skill_id"))
    elif at in ("chat.message", "chat.trace"):
        link("PARTICIPATED_IN", "User", user, "Chat", subj.get("chat_id"))
    elif at in ("org.member_added", "org.role_changed"):
        # both assert membership; role_changed also re-affirms the target is a member
        target = subj.get("target_user_id") or user
        add_entity("User", target)  # the added/changed member may differ from the actor
        link("MEMBER_OF", "User", target, "Org", org)
    elif at == "org.member_removed":
        # membership is stative: removal ENDS the MEMBER_OF relation (close it at the event time)
        target = subj.get("target_user_id") or user
        if target and org:
            add_entity("User", target)
            out.invalidations.append(EdgeSpec("MEMBER_OF", "User", str(target), "Org", str(org)))
    elif at == "fact.observed":
        stmt = (ep.payload or {}).get("statement")
        if stmt:
            fkey = _slug(stmt)
            add_entity("Fact", fkey, stmt, {"statement": stmt})
            link("DECIDED", "User", user, "Fact", fkey, stmt)

    return out
