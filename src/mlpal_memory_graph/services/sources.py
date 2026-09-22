"""Sources (memory v7 WP4, design §9): connect, do not copy; admit by salience; promote on demand;
read databases through a tool and let memory learn the map.

Two kinds. A **files** source is a directory under the configured sources root: registration
catalogues its items (path, title, size, modified time) as cold entries and stores no content. An
item becomes a document when a question needs it (promote on demand: the answer path found nothing
and this item's title matched), or when someone promotes it; the document path then applies the
salience floor and the source's daily budget like any other document. A **sql** source is a
read-only connection with an allow-list of tables and a row limit: registration introspects the
allowed tables and writes their columns as keyed state claims (`source/<name>/schema`, key = table),
so the next session finds the map in memory before it queries; every query leaves a content-free
`source.queried` ledger row and returns a query id the agent cites as evidence for what it learns.
"""

from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select

from ..core.config import get_settings
from ..db.models import MemorySource, SourceItem
from ..ingest.envelope import Actor, EpisodeEnvelope
from ..repositories.episodes import insert_episode
from .salience import terms_of

FILE_KINDS = (".md", ".txt", ".pdf", ".rst", ".csv", ".json", ".yaml", ".yml")
MAX_FILE_CHARS = 200_000
MAX_ITEMS = 20_000
_SELECT = re.compile(r"^\s*(select|with)\b", re.I)
_TABLE_REF = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_\.]*)", re.I)
_LIMIT = re.compile(r"\blimit\s+(\d+)\b", re.I)


class SourceError(ValueError):
    pass


def _root_ok(root: Path) -> Path:
    base = Path(get_settings().sources_root).resolve()
    r = root.resolve()
    if not r.is_relative_to(base):
        raise SourceError(f"root {r} is outside the sources root {base}")
    if not r.is_dir():
        raise SourceError(f"root {r} is not a directory")
    return r


async def get_source(session, org_id: str | None, name: str) -> MemorySource | None:
    return (await session.execute(select(MemorySource).where(MemorySource.org_id == org_id, MemorySource.name == name))).scalars().first()


async def register_files(session, *, org_id: str | None, user_id: str | None, name: str, root: str,
                         scope: str = "org", scope_id: str | None = None, workspace: str | None = None) -> MemorySource:
    r = _root_ok(Path(root))
    src = await get_source(session, org_id, name)
    if src is None:
        src = MemorySource(org_id=org_id, name=name, kind="files", scope=scope, scope_id=scope_id, workspace=workspace,
                           config={"root": str(r)}, status="cold", created_by=user_id)
        session.add(src)
        await session.flush()
    known = {i.ref for i in (await session.execute(select(SourceItem).where(SourceItem.source_id == src.id))).scalars().all()}
    count = 0
    for p in sorted(r.rglob("*")):
        if count >= MAX_ITEMS:
            break
        if not p.is_file() or p.suffix.lower() not in FILE_KINDS or any(part.startswith(".") for part in p.relative_to(r).parts):
            continue
        ref = str(p.relative_to(r))
        count += 1
        if ref in known:
            continue
        st = p.stat()
        session.add(SourceItem(org_id=org_id, source_id=src.id, ref=ref, title=p.stem.replace("_", " ").replace("-", " "),
                               size=st.st_size, modified_at=datetime.fromtimestamp(st.st_mtime, tz=UTC)))
    src.item_count = count
    src.indexed_at = datetime.now(UTC)
    await session.flush()
    return src


def _read_item(root: Path, ref: str) -> str:
    p = (root / ref).resolve()
    if not p.is_relative_to(root):
        raise SourceError("item escapes the source root")
    if p.suffix.lower() == ".pdf":
        from ..collectors.pdfs import extract_text

        return extract_text(p, max_chars=MAX_FILE_CHARS)
    return p.read_text(errors="replace")[:MAX_FILE_CHARS]


async def candidates(session, src: MemorySource, query: str, limit: int) -> list[SourceItem]:
    """Cold items ranked by overlap between the question's terms and the item's title and path."""
    qt = set(terms_of(query)[:12]) | {t for t in re.findall(r"[a-z0-9]{3,}", query.lower())}
    if not qt:
        return []
    items = (await session.execute(select(SourceItem).where(SourceItem.source_id == src.id, SourceItem.admitted.is_(False),
                                                             SourceItem.declined_reason.is_(None)))).scalars().all()
    scored = []
    for it in items:
        words = set(re.findall(r"[a-z0-9]{3,}", f"{it.title or ''} {it.ref}".lower()))
        hit = len(qt & words)
        if hit:
            scored.append((hit, -(it.modified_at.timestamp() if it.modified_at else 0), it))
    scored.sort(key=lambda x: (-x[0], x[1]))
    return [s[2] for s in scored[:limit]]


async def admit(session, src: MemorySource, item: SourceItem, *, user_id: str | None, by: str) -> dict:
    """Turn one cold item into a document through the governed fold (salience and budget apply)."""
    from ..api.deps import get_updater
    from ..db.models import Episode

    root = Path(src.config["root"])
    text = _read_item(root, item.ref)
    if len(text.strip()) < 50:
        item.declined_reason = "empty"
        return {"ref": item.ref, "status": "declined", "reason": "empty"}
    # ids stay under the episodes table's 64-char event_id (Postgres enforces it; SQLite does not)
    event_id = f"src:{src.id[:12]}:{hashlib.sha256(item.ref.encode()).hexdigest()[:24]}"
    env = EpisodeEnvelope(event_id=event_id, org_id=src.org_id, scope=src.scope, scope_id=src.scope_id, workspace=src.workspace,
                          source=f"src:{src.name}"[:32], action_type="document.ingested", content=text,
                          payload={"title": item.title or item.ref, "uri": f"{src.name}://{item.ref}", "promoted_by": by},
                          actor=Actor(user_id=user_id) if user_id else Actor())
    if item.modified_at is not None:
        env.occurred_at = item.modified_at
    inserted = await insert_episode(session, env.to_episode_kwargs(capture_content=True))
    status, reason = "processed", None
    if inserted:
        episode = await session.get(Episode, env.event_id)
        result = await get_updater().process_episode(session, episode)
        reason = result.get("dropped")
        status = "declined" if reason else "processed"
    if reason:
        item.declined_reason = reason[:250]
    else:
        item.admitted = True
        item.admitted_at = datetime.now(UTC)
        item.admitted_by = by
        item.document_event_id = event_id
        if src.status == "cold":
            src.status = "warm"
    await session.flush()
    return {"ref": item.ref, "status": status, "reason": reason, "event_id": event_id}


async def promote_for_question(session, *, org_id: str | None, user_id: str | None, query: str, limit: int | None = None) -> list[dict]:
    """The answer path found nothing: admit the best few cold items that match the question, across
    the tenant's files sources. Bounded by promote_on_demand_max_items."""
    k = limit or get_settings().promote_on_demand_max_items
    out: list[dict] = []
    srcs = (await session.execute(select(MemorySource).where(MemorySource.org_id == org_id, MemorySource.kind == "files"))).scalars().all()
    for src in srcs:
        for item in await candidates(session, src, query, k - len(out)):
            out.append({"source": src.name, **(await admit(session, src, item, user_id=user_id, by="question"))})
            if len(out) >= k:
                return out
    return out


# ---------------------------------------------------------------- sql read-through

@dataclass
class QueryResult:
    query_id: str
    columns: list[str]
    rows: list[list]
    row_count: int
    truncated: bool
    tables: list[str]


def _check_sql(sql: str, allowed: set[str], row_limit: int) -> tuple[str, list[str]]:
    s = sql.strip().rstrip(";")
    if ";" in s or not _SELECT.match(s):
        raise SourceError("only a single SELECT (or WITH ... SELECT) statement is allowed")
    tables = [t.split(".")[-1].lower() for t in _TABLE_REF.findall(s)]
    bad = sorted({t for t in tables if t not in allowed})
    if bad:
        raise SourceError(f"table(s) not in this source's allow-list: {bad}; allowed: {sorted(allowed)}")
    # fetch one row beyond the cap so the caller learns the result was truncated
    m = _LIMIT.search(s)
    if m is None:
        s = f"{s} LIMIT {row_limit + 1}"
    elif int(m.group(1)) > row_limit:
        s = _LIMIT.sub(f"LIMIT {row_limit + 1}", s)
    return s, sorted(set(tables))


def _run_sync(url: str, sql: str, row_limit: int) -> tuple[list[str], list[list]]:
    from sqlalchemy import create_engine, text

    eng = create_engine(url, future=True)
    try:
        with eng.connect() as conn:
            if url.startswith("postgresql"):
                conn.execute(text("SET LOCAL statement_timeout = 5000"))
            res = conn.execute(text(sql))
            cols = list(res.keys())
            rows = [list(r) for r in res.fetchmany(row_limit + 1)]
        return cols, rows
    finally:
        eng.dispose()


def _introspect_sync(url: str, allowed: list[str]) -> dict[str, list[str]]:
    from sqlalchemy import create_engine, inspect

    eng = create_engine(url, future=True)
    try:
        insp = inspect(eng)
        have = set(insp.get_table_names())
        return {t: [c["name"] for c in insp.get_columns(t)] for t in allowed if t in have}
    finally:
        eng.dispose()


async def register_sql(session, *, org_id: str | None, user_id: str | None, name: str, url: str, tables: list[str],
                       row_limit: int = 200, scope: str = "org", scope_id: str | None = None, workspace: str | None = None) -> tuple[MemorySource, dict]:
    if not url.startswith(("sqlite://", "postgresql://", "postgresql+psycopg://")):
        raise SourceError("sql sources accept sqlite:// and postgresql:// URLs")
    allowed = [t.lower() for t in tables]
    if not allowed:
        raise SourceError("a sql source needs an explicit table allow-list")
    schema = await asyncio.to_thread(_introspect_sync, url, allowed)
    src = await get_source(session, org_id, name)
    if src is None:
        src = MemorySource(org_id=org_id, name=name, kind="sql", scope=scope, scope_id=scope_id, workspace=workspace,
                           config={"url": url, "tables": allowed, "row_limit": int(row_limit)}, status="warm", created_by=user_id)
        session.add(src)
    else:
        src.config = {"url": url, "tables": allowed, "row_limit": int(row_limit)}
    src.item_count = len(schema)
    src.indexed_at = datetime.now(UTC)
    await session.flush()
    # the map's machine half: one keyed state claim per table, evidence = this introspection
    from ..api.deps import get_updater
    from ..db.models import Episode

    ev = f"introspect:{src.id[:12]}:{int(datetime.now(UTC).timestamp())}"
    for table, cols in schema.items():
        env = EpisodeEnvelope(org_id=org_id, scope=scope, scope_id=scope_id, workspace=workspace, source="harness_memory",
                              action_type="memory.claim", actor=Actor(user_id=user_id) if user_id else Actor(),
                              content=f"{name} table {table}: columns {', '.join(cols)}",
                              payload={"kind": "state", "topic": f"source/{name}/schema", "key": table,
                                       "value": f"table {table} columns: {', '.join(cols)}", "evidence_ids": [ev], "origin": "introspect"})
        if await insert_episode(session, env.to_episode_kwargs(capture_content=False)):
            await get_updater().process_episode(session, await session.get(Episode, env.event_id))
    await session.flush()
    return src, schema


async def query(session, src: MemorySource, *, sql: str, user_id: str | None, run: dict | None = None) -> QueryResult:
    if src.kind != "sql":
        raise SourceError(f"source {src.name} is not a sql source")
    cfg = src.config or {}
    row_limit = int(cfg.get("row_limit") or 200)
    safe_sql, tables = _check_sql(sql, set(cfg.get("tables") or []), row_limit)
    cols, rows = await asyncio.to_thread(_run_sync, cfg["url"], safe_sql, row_limit)
    truncated = len(rows) > row_limit
    rows = rows[:row_limit]
    qid = f"query:{src.id[:12]}:{hashlib.sha256(safe_sql.encode()).hexdigest()[:16]}:{int(datetime.now(UTC).timestamp())}"
    env = EpisodeEnvelope(event_id=qid, org_id=src.org_id, scope="org", scope_id=src.org_id, workspace=src.workspace,
                          source="governance", action_type="source.queried", actor=Actor(user_id=user_id) if user_id else Actor(),
                          payload={"source": src.name, "tables": tables, "rows": len(rows), "truncated": truncated,
                                   "sql_sha": hashlib.sha256(safe_sql.encode()).hexdigest()[:16],
                                   **{k: v for k, v in (run or {}).items() if k in ("run_id", "hop", "origin") and v}})
    await insert_episode(session, env.to_episode_kwargs(capture_content=False))
    await session.flush()
    return QueryResult(query_id=qid, columns=cols, rows=[[_json_safe(v) for v in r] for r in rows], row_count=len(rows), truncated=truncated, tables=tables)


def _json_safe(v):
    if isinstance(v, (datetime,)):
        return v.isoformat()
    if isinstance(v, (bytes, bytearray)):
        return v.hex()
    if isinstance(v, (int, float, str, bool)) or v is None:
        return v
    return str(v)
