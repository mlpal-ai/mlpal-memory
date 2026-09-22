"""Units: the org's own hierarchy (memory v12 §2b) — a tree of any depth the org defines,
rolling up to the org. A unit is the storage scope ``team`` (scope_id = unit id); membership and
the tree decide who reads what, the unit's policy decides what rolls up and who oversees.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from .base import SCHEMA, Base, new_uuid


class Unit(Base):
    __tablename__ = "units"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    org_id: Mapped[str] = mapped_column(String(64), index=True)  # tenant
    parent_id: Mapped[str | None] = mapped_column(String(36), index=True)  # None = directly under the org
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(32), default="unit", server_default="unit")  # the org's label
    # read_descendants: bool · publish_up: members|admins · hops_may_write: bool · lift: {tier, to}
    policy: Mapped[dict] = mapped_column(JSON, default=dict)
    created_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("org_id", "parent_id", "name", name="uq_unit_sibling_name"),
        {"schema": SCHEMA},
    )


class UnitMember(Base):
    __tablename__ = "unit_members"

    org_id: Mapped[str] = mapped_column(String(64), index=True)
    unit_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="member", server_default="member")  # member | admin
    added_by: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = ({"schema": SCHEMA},)
