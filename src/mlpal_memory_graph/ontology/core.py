"""The small, versioned core ontology (see docs/ontology.md).

Enterprises extend by subclassing — they never edit this core. Node/edge classes are
anchored to public upper ontologies (schema.org / FOAF / ORG / SKOS / PROV) so the model
stays generic and interoperable.
"""

from __future__ import annotations

from dataclasses import dataclass

ONTOLOGY_VERSION = "core/0.2.0"  # 0.2.0: +insight classes for coding-agent memory (v3)


@dataclass(frozen=True)
class NodeClass:
    name: str
    anchor: str  # upper-ontology anchor (IRI prefix form)
    description: str


@dataclass(frozen=True)
class EdgeClass:
    name: str
    description: str
    functional: bool = False  # if True, a new edge of this (type, src) supersedes the old


NODE_CLASSES: dict[str, NodeClass] = {
    "Org": NodeClass("Org", "org:Organization", "Enterprise tenant; top-level partition."),
    "Team": NodeClass("Team", "org:OrganizationalUnit", "A group within an org."),
    "User": NodeClass("User", "schema:Person", "A human actor."),
    "Agent": NodeClass("Agent", "schema:SoftwareApplication", "An assistant built on MLPal."),
    "MCPServer": NodeClass("MCPServer", "schema:SoftwareApplication", "A deployed MCP server."),
    "Tool": NodeClass("Tool", "schema:Action", "A tool exposed by an MCP server."),
    "Skill": NodeClass("Skill", "schema:CreativeWork", "A published/installed skill."),
    "Chat": NodeClass("Chat", "schema:Conversation", "A conversation/session."),
    "Artifact": NodeClass("Artifact", "schema:CreativeWork", "A repo, file, template or doc."),
    "Action": NodeClass("Action", "prov:Activity", "A typed event occurrence."),
    "Status": NodeClass("Status", "schema:ActionStatusType", "A lifecycle/run status value."),
    "Fact": NodeClass("Fact", "skos:Concept", "Generic extracted assertion (catch-all)."),
    # --- v3 insight classes (ontology 0.2.0): what actually transfers across coding
    # sessions — conventions, decisions, gotchas, procedures, preferences. Typed so
    # retrieval can route by kind, dedup stays per-class, and ranking can weight them.
    "Workspace": NodeClass(
        "Workspace", "schema:Project", "A repo/project a user works in (personal-store facet)."
    ),
    "Convention": NodeClass(
        "Convention", "skos:Concept", "A standing rule/style the codebase or team follows."
    ),
    "Decision": NodeClass(
        "Decision", "prov:Entity", "A recorded choice with rationale (and its date)."
    ),
    "Gotcha": NodeClass(
        "Gotcha", "skos:Concept", "A landmine/failure-mode worth warning future sessions about."
    ),
    "HowTo": NodeClass(
        "HowTo", "schema:HowTo", "A working procedure (commands, order, preconditions)."
    ),
    "Preference": NodeClass(
        "Preference", "skos:Concept", "A person-level preference (style, tools, workflow)."
    ),
    "Insight": NodeClass(
        "Insight", "skos:Concept", "Generic derived learning (subtype via props.kind)."
    ),
}

EDGE_CLASSES: dict[str, EdgeClass] = {
    "MEMBER_OF": EdgeClass("MEMBER_OF", "User/Team is a member of a Team/Org."),
    "AUTHORED": EdgeClass("AUTHORED", "Actor created an Agent/MCPServer/Skill/Artifact."),
    "DEPLOYED": EdgeClass("DEPLOYED", "Actor deployed an Agent/MCPServer."),
    "INVOKED": EdgeClass("INVOKED", "Actor/Agent invoked a Tool/MCPServer."),
    "EXPOSES": EdgeClass("EXPOSES", "An MCPServer exposes a Tool."),
    "USED_SKILL": EdgeClass("USED_SKILL", "Actor/Agent used a Skill."),
    "DERIVED_FROM": EdgeClass("DERIVED_FROM", "An MCPServer derived from a template Artifact."),
    "PARTICIPATED_IN": EdgeClass("PARTICIPATED_IN", "Actor/Agent participated in a Chat."),
    "SHARED_WITH": EdgeClass("SHARED_WITH", "Actor shared a resource with a User/Team."),
    "DECIDED": EdgeClass("DECIDED", "Actor/Team recorded a decision (Fact)."),
    "MENTIONS": EdgeClass("MENTIONS", "An Action/Chat soft-references any node."),
    "RELATES_TO": EdgeClass("RELATES_TO", "Generic fallback relationship."),
    # functional/stative: an Agent/MCPServer has exactly one current status, so a new status
    # (running→stopped) supersedes the prior one — the deterministic invalidation fast-path (D1).
    "HAS_STATUS": EdgeClass(
        "HAS_STATUS", "An Agent/MCPServer has a current lifecycle status.", functional=True
    ),
    # --- v3 insight edges (ontology 0.2.0) ---
    "APPLIES_TO": EdgeClass("APPLIES_TO", "An insight applies to a Workspace/CodeArea/entity."),
    "LEARNED_IN": EdgeClass("LEARNED_IN", "An insight was learned in a Chat/session."),
    "SUPERSEDES": EdgeClass("SUPERSEDES", "A newer insight explicitly replaces an older one."),
    # NOT functional: contradictions from different writers COEXIST as a contention point,
    # ranked at read time — never silently resolved (design v3 §2.4).
    "CONTRADICTS": EdgeClass("CONTRADICTS", "Two live assertions disagree (contention point)."),
}

# Recognized action verbs (envelopes may carry others — the graph stays generic).
KNOWN_ACTION_TYPES: frozenset[str] = frozenset(
    {
        "agent.created",
        "agent.updated",
        "agent.deployed",
        "agent.stopped",
        "mcp.created",
        "mcp.deployed",
        "mcp.started",
        "mcp.stopped",
        "mcp.tool.invoked",
        "skill.created",
        "skill.published",
        "skill.installed",
        "skill.loaded",
        "chat.message",
        "chat.trace",
        "model.invoked",
        "build.session.started",
        "build.session.completed",
        "org.member_added",
        "org.member_removed",
        "org.role_changed",
        "fact.observed",
        # v3: coding-session capture + insight lifecycle
        "session.started",
        "session.turn",
        "session.completed",
        "session.transcript",
        "document.ingested",
        "insight.observed",
        "insight.committed",
        "memory.published",
    }
)


def is_valid_node_type(t: str) -> bool:
    return t in NODE_CLASSES


def is_valid_edge_type(t: str) -> bool:
    return t in EDGE_CLASSES
