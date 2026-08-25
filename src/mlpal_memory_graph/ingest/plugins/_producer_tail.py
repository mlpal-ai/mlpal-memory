"""Base for Phase-1 watermark-tail SourcePlugins over a **producer** Postgres table (read-only).

The platform has no event bus, so the cheap deterministic backbone is built by *pull-tailing*
existing producer tables (audit logs, agents, mcp_servers, skill_load_events) — "no producer
changes". A subclass declares the table, cursor, the columns it depends on, and a pure
``map_row`` → list of ``SourceItem``; the base runs the tail and **fails loud on schema drift**
(the column-contract check) instead of silently mis-mapping. Producer schema names are
env-specific (`mlpal_test`/`mcp`/`skills_events`/…) so they come from config, never hardcoded.

Read access only: a least-privilege ``memory_reader`` role (SELECT-only) via a per-source
``read_dsn`` in the sources YAML; we never write to producers. See
``docs/research/primer-gaps-and-fixes.md`` Workstream B and ``docs/ops/dba-request-ingestion.md``.
"""

from __future__ import annotations

from datetime import datetime

from ...core.logging import get_logger
from ..sources import Checkpoint, SourceCapabilities, SourceConfig, SourceItem

log = get_logger(__name__)


class SourceContractError(RuntimeError):
    """A producer table is missing a column the plugin depends on (schema drift) — fail loud."""


def scope_for(org_id, user_id) -> tuple[str, str]:
    """Best-available (scope, scope_id): org tenancy when present, else the user's personal scope.

    Tenant resolution for org-less, user-owned resources (some mcp/agent rows) requires a
    users→org join the producer query should add — tracked as a known gap. Until then we land
    such episodes under the owning user.
    """
    if org_id is not None:
        return "org", str(org_id)
    if user_id is not None:
        return "user", str(user_id)
    raise SourceContractError("row has neither org_id nor user_id — cannot scope")


# the tiebreaker id column may be integer (Phase-1 producer PKs) or text (the langgraph store
# key). decode_cursor returns it as a string, so coerce it back to the column's type before
# binding — asyncpg rejects a str bound to an int column in the keyset comparison.
_INT_SQL_TYPES = frozenset(
    {"bigint", "integer", "int", "int4", "int8", "smallint", "serial", "bigserial"}
)


def encode_cursor(ts: datetime, row_id) -> str:
    """Composite keyset cursor ``<iso>|<id>`` so same-timestamp rows aren't skipped."""
    return f"{ts.isoformat()}|{row_id}"


def decode_cursor(cursor: Checkpoint) -> tuple[datetime, str] | None:
    if not cursor:
        return None
    iso, _, row_id = cursor.partition("|")  # ISO timestamps never contain '|', so split on first
    return datetime.fromisoformat(iso), row_id


class ProducerTailSource:
    """Subclass contract: set ``TABLE``, ``CURSOR`` (SQL expr), ``CURSOR_KEY`` (row key, if the
    cursor is aliased), ``COLUMNS`` (selected), ``REQUIRED`` (fail-loud contract); implement
    ``source_type()`` and ``map_row(row) -> list[SourceItem]``."""

    TABLE: str = ""
    CURSOR: str = "created_at"
    CURSOR_KEY: str = ""  # row dict key for the cursor value; defaults to CURSOR
    ID_EXPR: str = "id"  # SQL expr for the tiebreaker id (override when aliased, e.g. "a.id")
    ID_KEY: str = "id"  # row dict key for the tiebreaker id (e.g. "key" for the langgraph store)
    ID_SQL_TYPE: str = "bigint"  # tiebreaker column type so the keyset param binds correctly
    COLUMNS: tuple[str, ...] = ()
    REQUIRED: tuple[str, ...] = ()

    def __init__(self, config: SourceConfig) -> None:
        self.config = config
        self.read_dsn: str | None = config.options.get("read_dsn")
        self.schema: str | None = config.options.get("schema")

    @classmethod
    def capabilities(cls) -> SourceCapabilities:
        return SourceCapabilities(incremental=True, rich_structure=False)

    # subclasses implement
    def map_row(self, row: dict) -> list[SourceItem]:  # pragma: no cover - abstract
        raise NotImplementedError

    @property
    def _cursor_key(self) -> str:
        return self.CURSOR_KEY or self.CURSOR

    def validate_row(self, row: dict) -> None:
        missing = [c for c in self.REQUIRED if c not in row]
        if missing:
            raise SourceContractError(
                f"{self.source_type()}: {self._qualified()} is missing required column(s) "
                f"{missing} — producer schema drift. Update the plugin's contract."
            )

    def _qualified(self) -> str:
        return f'"{self.schema}".{self.TABLE}' if self.schema else self.TABLE

    def from_clause(self) -> str:
        """The SQL FROM target. Override to JOIN sibling tables (e.g. agents→projects for the
        author). Must expose ``id``, the ``COLUMNS`` and the ``CURSOR``/``CURSOR_KEY``."""
        return self._qualified()

    async def list_items(
        self, since: Checkpoint, limit: int
    ) -> tuple[list[SourceItem], Checkpoint]:
        if not self.read_dsn:
            return [], since  # configured no-op until a read DSN is provided (local/dev/CI)

        import json

        import asyncpg  # lazy: producer access is a Postgres-only path

        conn = await asyncpg.connect(self.read_dsn)
        try:
            # decode JSON(B) columns (details, tools_metadata, metadata_json) to Python objects
            for jtype in ("json", "jsonb"):
                await conn.set_type_codec(
                    jtype, encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
                )
            cols = ", ".join(self.COLUMNS)
            tbl = self.from_clause()
            order = f"{self.CURSOR}, {self.ID_EXPR}"
            prev = decode_cursor(since)
            if prev is None:
                rows = await conn.fetch(
                    f"SELECT {cols} FROM {tbl} ORDER BY {order} LIMIT $1", limit
                )
            else:
                # composite keyset — never skip rows that share the cursor timestamp. Coerce the
                # decoded id back to the column type (str -> int for integer PKs).
                id_param = int(prev[1]) if self.ID_SQL_TYPE in _INT_SQL_TYPES else prev[1]
                rows = await conn.fetch(
                    f"SELECT {cols} FROM {tbl} "
                    f"WHERE ({self.CURSOR}, {self.ID_EXPR}) > ($1, $2) ORDER BY {order} LIMIT $3",
                    prev[0],
                    id_param,
                    limit,
                )
        finally:
            await conn.close()

        items: list[SourceItem] = []
        cursor = since
        for record in rows:
            row = dict(record)
            self.validate_row(row)
            items.extend(self.map_row(row))
            value = row.get(self._cursor_key)
            if isinstance(value, datetime):
                cursor = encode_cursor(value, row[self.ID_KEY])  # rows ascending → last is max
        if items:
            log.info("source.tailed", type=self.source_type(), count=len(items))
        return items, cursor
