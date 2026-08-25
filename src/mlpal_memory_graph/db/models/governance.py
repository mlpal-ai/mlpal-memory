"""Per-scope governance records: consent (collect/update opt-out) and extraction policy.

Both are keyed by (org_id, scope, scope_id) and resolved down the scope chain with
deny-precedence — a broader scope sets a floor a narrower scope can only tighten.
See design-proposal §2 and §5.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base


class ConsentState(Base):
    """Whether memory may be collected/updated at a scope.

    ``active`` — learn normally. ``off`` — stop learning new (existing still served).
    ``pause`` — same as off for writes (kept distinct for UX/audit intent).
    The irreversible CLEAR action purges existing memory then records ``off``.
    """

    __tablename__ = "consent_states"

    org_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="active", server_default="active")
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = ({"schema": SCHEMA},)


class ExtractionPolicy(Base):
    """What is allowed to be picked up vs excluded at a scope (data-driven, versioned).

    ``policy`` JSON holds: deny_sources [str], allow_sources [str] | null,
    metadata_deny {key: [values]}, metadata_allow {key: [values]} | null, min_salience float.
    """

    __tablename__ = "extraction_policies"

    org_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_by: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = ({"schema": SCHEMA},)
