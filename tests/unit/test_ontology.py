from __future__ import annotations

from mlpal_memory_graph.ontology import (
    EDGE_CLASSES,
    NODE_CLASSES,
    ONTOLOGY_VERSION,
    is_valid_edge_type,
    is_valid_node_type,
)


def test_core_node_classes_present():
    for cls in ("Org", "User", "Agent", "MCPServer", "Skill", "Chat", "Action", "Fact"):
        assert cls in NODE_CLASSES
        assert NODE_CLASSES[cls].anchor  # anchored to an upper ontology


def test_core_edge_classes_present():
    for edge in ("AUTHORED", "DEPLOYED", "INVOKED", "MEMBER_OF", "RELATES_TO"):
        assert edge in EDGE_CLASSES


def test_validators():
    assert is_valid_node_type("Agent")
    assert not is_valid_node_type("Nonsense")
    assert is_valid_edge_type("DEPLOYED")
    assert not is_valid_edge_type("nope")


def test_version_shape():
    assert ONTOLOGY_VERSION.startswith("core/")
