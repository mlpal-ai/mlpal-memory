"""Workspace notes service — the curated working-memory tier.

Pure rules live here so the API and the MCP share them: the closed section set, the byte
budget, optimistic concurrency, versioning with a content-free episode, point-in-time reads,
citation staleness against live edges, and the always-on bundle (org index → workspace note
→ user notes) capped to a token budget. No LLM anywhere: a note is what people and agents
wrote, verbatim.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select

from ..core.scope import Scope
from ..db.models import Edge, Node, Note, NoteVersion
from ..repositories.episodes import insert_episode
from .resolution import RetrievalContext

SECTIONS = ("Now", "Decisions", "Open threads", "Preferences", "Pointers")
_SECTION_BY_KEY = {s.lower(): s for s in SECTIONS}
MAX_CHARS = 8_000                      # ≈ 2k tokens; a note is ambient context, not a document
CHARS_PER_TOKEN = 4
_HEADING = re.compile(r"^##\s+(.+?)\s*$")
_CITATION = re.compile(r"memory://(node|edge)/([0-9a-fA-F-]{8,})")
SOURCE = "notes"
ACTION_UPDATED = "note.updated"


class NoteError(ValueError):
    """Schema violation in a note body (422 at the API)."""


class NoteConflict(RuntimeError):
    """Optimistic-concurrency failure (409 at the API)."""


def parse_sections(body: str) -> dict[str, str]:
    """Split a note body into its canonical sections.

    Rules: only `## <Section>` headings from SECTIONS (case-insensitive), no text before the
    first heading, no duplicate section. Returns every canonical section (missing ones empty),
    in canonical order, content stripped of surrounding blank lines.
    """
    if len(body) > MAX_CHARS:
        raise NoteError(f"note body exceeds {MAX_CHARS} characters ({len(body)})")
    found: dict[str, list[str]] = {}
    current: str | None = None
    for raw in body.splitlines():
        m = _HEADING.match(raw)
        if m:
            key = m.group(1).strip().lower()
            if key not in _SECTION_BY_KEY:
                raise NoteError(f"unknown section '{m.group(1).strip()}'; allowed: {', '.join(SECTIONS)}")
            name = _SECTION_BY_KEY[key]
            if name in found:
                raise NoteError(f"duplicate section '{name}'")
            current = name
            found[name] = []
            continue
        if current is None:
            if raw.strip():
                raise NoteError("text before the first '## Section' heading")
            continue
        found[current].append(raw)
    return {s: "\n".join(found.get(s, [])).strip("\n") for s in SECTIONS}


def render_body(sections: dict[str, str]) -> str:
    parts = []
    for s in SECTIONS:
        text = (sections.get(s) or "").strip("\n")
        parts.append(f"## {s}\n{text}\n" if text else f"## {s}\n")
    return "\n".join(parts).rstrip("\n") + "\n"


def normalise(body: str) -> str:
    """Canonical form: sections in order, headings cased canonically, trailing newline."""
    return render_body(parse_sections(body))


def changed_sections(old: str, new: str) -> list[str]:
    a, b = parse_sections(old) if old else {s: "" for s in SECTIONS}, parse_sections(new)
    return [s for s in SECTIONS if a.get(s, "") != b.get(s, "")]


@dataclass
class NoteRead:
    note: Note
    body: str
    version: int
    as_of: datetime | None = None
    stale_citations: list[dict] = field(default_factory=list)


async def find_note(session, org_id: str | None, scope: str, scope_id: str, workspace: str) -> Note | None:
    stmt = select(Note).where(
        Note.org_id == org_id, Note.scope == scope, Note.scope_id == scope_id,
        Note.workspace == (workspace or ""),
    )
    return (await session.execute(stmt)).scalar_one_or_none()


async def get_note(session, org_id: str | None, scope: str, scope_id: str, workspace: str,
                   *, as_of: datetime | None = None) -> NoteRead | None:
    note = await find_note(session, org_id, scope, scope_id, workspace)
    if note is None:
        return None
    if as_of is None:
        return NoteRead(note, note.body, note.version,
                        stale_citations=await stale_citations(session, org_id, note.body))
    stmt = (
        select(NoteVersion)
        .where(NoteVersion.note_id == note.id, NoteVersion.created_at <= as_of)
        .order_by(NoteVersion.version.desc())
        .limit(1)
    )
    ver = (await session.execute(stmt)).scalar_one_or_none()
    if ver is None:
        return None  # the note did not exist yet at that instant
    return NoteRead(note, ver.body, ver.version, as_of=as_of)


async def put_note(
    session, *, org_id: str | None, scope: str, scope_id: str, workspace: str, body: str,
    updated_by: str | None, reason: str | None, base_version: int | None = None,
    classification: str | None = None, title: str | None = None,
) -> NoteRead:
    """Replace the whole body (validated + normalised); bump version; keep the version row;
    emit one content-free episode. ``base_version`` enables optimistic concurrency."""
    new_body = normalise(body)
    note = await find_note(session, org_id, scope, scope_id, workspace)
    if note is None:
        if base_version not in (None, 0):
            raise NoteConflict("note does not exist yet; base_version must be 0 or omitted")
        note = Note(
            org_id=org_id, scope=scope, scope_id=scope_id, workspace=workspace or "",
            classification=classification or ("personal" if scope == Scope.USER.value else "internal"),
            owner_user_id=scope_id if scope == Scope.USER.value else updated_by,
            title=title, body="", version=0, updated_by=updated_by,
        )
        session.add(note)
        await session.flush()
    elif base_version is not None and base_version != note.version:
        raise NoteConflict(f"base_version {base_version} != current {note.version}")
    sections = changed_sections(note.body, new_body)
    note.version += 1
    note.body = new_body
    note.updated_by = updated_by
    if title is not None:
        note.title = title
    session.add(NoteVersion(note_id=note.id, org_id=org_id, version=note.version, body=new_body,
                            updated_by=updated_by, reason=reason))
    await insert_episode(session, {
        "event_id": f"note:{note.id}:v{note.version}",
        "occurred_at": datetime.now(tz=__import__("datetime").UTC),
        "org_id": org_id, "scope": scope, "scope_id": scope_id, "workspace": workspace or None,
        "lifecycle": "committed", "actor": {"user_id": updated_by}, "source": SOURCE,
        "action_type": ACTION_UPDATED, "subject": {},
        "payload": {"note_id": note.id, "version": note.version, "reason": reason,
                    "sections_changed": sections, "chars": len(new_body)},
        "content": None, "source_ref": f"note:{note.id}", "schema_version": 1,
        "processed": True,  # nothing to extract: the note IS the curated form
    })
    await session.flush()
    return NoteRead(note, new_body, note.version)


async def patch_section(
    session, *, org_id: str | None, scope: str, scope_id: str, workspace: str, section: str,
    op: str, text: str, updated_by: str | None, reason: str | None, base_version: int | None = None,
) -> NoteRead:
    key = section.strip().lower()
    if key not in _SECTION_BY_KEY:
        raise NoteError(f"unknown section '{section}'; allowed: {', '.join(SECTIONS)}")
    if op not in ("replace", "append"):
        raise NoteError("op must be 'replace' or 'append'")
    name = _SECTION_BY_KEY[key]
    note = await find_note(session, org_id, scope, scope_id, workspace)
    current = parse_sections(note.body) if note and note.body else {s: "" for s in SECTIONS}
    if base_version is not None and (note.version if note else 0) != base_version:
        raise NoteConflict(f"base_version {base_version} != current {note.version if note else 0}")
    new_text = text.strip("\n")
    current[name] = new_text if op == "replace" or not current[name] else f"{current[name]}\n{new_text}"
    return await put_note(session, org_id=org_id, scope=scope, scope_id=scope_id, workspace=workspace,
                          body=render_body(current), updated_by=updated_by, reason=reason,
                          base_version=base_version)


async def history(session, note: Note, limit: int = 50) -> list[NoteVersion]:
    stmt = (select(NoteVersion).where(NoteVersion.note_id == note.id)
            .order_by(NoteVersion.version.desc()).limit(limit))
    return list((await session.execute(stmt)).scalars().all())


async def stale_citations(session, org_id: str | None, body: str) -> list[dict]:
    """Citations to edges that were invalidated or nodes that no longer exist."""
    out: list[dict] = []
    for kind, ident in {(m.group(1), m.group(2)) for m in _CITATION.finditer(body or "")}:
        if kind == "edge":
            e = await session.get(Edge, ident)
            if e is None:
                out.append({"citation": f"memory://edge/{ident}", "state": "missing"})
            elif e.invalid_at is not None:
                out.append({"citation": f"memory://edge/{ident}", "state": "superseded",
                            "invalid_at": e.invalid_at.isoformat()})
        else:
            n = await session.get(Node, ident)
            if n is None:
                out.append({"citation": f"memory://node/{ident}", "state": "missing"})
    return out


@dataclass
class NotesContext:
    markdown: str
    estimated_tokens: int
    notes: list[dict]
    truncated: bool


def _annotate(body: str, stale: list[dict]) -> str:
    if not stale:
        return body
    flags = {s["citation"]: s["state"] for s in stale}
    lines = []
    for line in body.splitlines():
        hit = [c for c in flags if c in line]
        lines.append(f"{line}  ⚠ {flags[hit[0]]}" if hit else line)
    return "\n".join(lines) + ("\n" if body.endswith("\n") else "")


async def render_context(session, ctx: RetrievalContext, *, workspace: str | None,
                         token_budget: int = 3000) -> NotesContext:
    """The always-on bundle for a caller in a workspace, most general first:
    org index → org workspace note → user preferences → user workspace note."""
    candidates: list[tuple[str, str, str, str]] = []
    if ctx.tenant_id:
        candidates.append(("Org", Scope.ORG.value, ctx.tenant_id, ""))
        if workspace:
            candidates.append((f"Workspace {workspace}", Scope.ORG.value, ctx.tenant_id, workspace))
    if ctx.user_id:
        candidates.append(("You", Scope.USER.value, ctx.user_id, ""))
        if workspace:
            candidates.append((f"You in {workspace}", Scope.USER.value, ctx.user_id, workspace))
    char_budget = token_budget * CHARS_PER_TOKEN
    parts: list[str] = ["# Working notes\n"]
    used = len(parts[0])
    included: list[dict] = []
    truncated = False
    for label, scope, scope_id, ws in candidates:
        read = await get_note(session, ctx.tenant_id, scope, scope_id, ws)
        if read is None or not read.body.strip():
            continue
        block = f"\n# {label} (v{read.version})\n{_annotate(read.body, read.stale_citations)}"
        if used + len(block) > char_budget:
            truncated = True
            break
        parts.append(block)
        used += len(block)
        included.append({"label": label, "scope": scope, "scope_id": scope_id, "workspace": ws,
                         "version": read.version, "stale_citations": read.stale_citations})
    if truncated:
        parts.append("\n_(more notes omitted to fit the context budget)_\n")
    md = "".join(parts)
    return NotesContext(md, max(1, len(md) // CHARS_PER_TOKEN), included, truncated)
