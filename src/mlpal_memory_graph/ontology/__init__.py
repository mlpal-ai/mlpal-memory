"""Core ontology for the MLPal institutional memory graph."""

from .core import (
    EDGE_CLASSES,
    KNOWN_ACTION_TYPES,
    NODE_CLASSES,
    ONTOLOGY_VERSION,
    EdgeClass,
    NodeClass,
    is_valid_edge_type,
    is_valid_node_type,
)

__all__ = [
    "ONTOLOGY_VERSION",
    "NODE_CLASSES",
    "EDGE_CLASSES",
    "KNOWN_ACTION_TYPES",
    "NodeClass",
    "EdgeClass",
    "is_valid_node_type",
    "is_valid_edge_type",
]
