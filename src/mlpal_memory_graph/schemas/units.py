"""Units API schemas (memory v12 §2b)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class UnitIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    parent_id: str | None = None  # None = directly under the org
    kind: str = Field("unit", max_length=32)  # the org's own label: division, department, squad …
    policy: dict = {}


class UnitPatch(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=128)
    kind: str | None = Field(None, max_length=32)
    parent_id: str | None = None
    move: bool = False  # True: apply parent_id (None = move to the org's top level)
    policy: dict | None = None


class UnitOut(BaseModel):
    id: str
    parent_id: str | None
    name: str
    kind: str
    policy: dict
    depth: int
    members: int
    created_by: str | None
    created_at: str | None


class UnitListOut(BaseModel):
    units: list[UnitOut]
    mine: list[str]  # units the caller reads (nearest first)
    administered: list[str]
    admin: bool = False  # a memory admin of the tenant: may create top-level units and govern every unit


class MemberIn(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=64)
    role: str = Field("member", pattern="^(member|admin)$")


class MemberOut(BaseModel):
    unit_id: str
    user_id: str
    role: str
    added_by: str | None
    created_at: str | None


class MembersOut(BaseModel):
    members: list[MemberOut]
