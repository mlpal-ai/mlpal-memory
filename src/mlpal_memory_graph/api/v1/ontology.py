"""Expose the core ontology (for clients building/extending on it)."""

from __future__ import annotations

from fastapi import APIRouter

from ...ontology import EDGE_CLASSES, NODE_CLASSES, ONTOLOGY_VERSION
from ...ontology.core import KNOWN_ACTION_TYPES

router = APIRouter(prefix="/ontology", tags=["ontology"])


@router.get("")
async def get_ontology() -> dict:
    return {
        "version": ONTOLOGY_VERSION,
        "node_classes": [
            {"name": c.name, "anchor": c.anchor, "description": c.description}
            for c in NODE_CLASSES.values()
        ],
        "edge_classes": [
            {"name": c.name, "description": c.description, "functional": c.functional}
            for c in EDGE_CLASSES.values()
        ],
        "action_types": sorted(KNOWN_ACTION_TYPES),
    }
