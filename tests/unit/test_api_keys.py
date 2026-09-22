"""Static API keys (0.2.0): the self-hosted auth backend. The key file holds hashes, a presented
key resolves to one tenant, a revoked key stops working on the next request, a broken edit keeps
the last good set, and the management tool round-trips with the loader."""

from __future__ import annotations

import os
import time

import pytest
import yaml

from mlpal_memory_graph.core.api_keys import ApiKeyFile, key_digest, mint_key, parse_entries
from mlpal_memory_graph.tools import api_keys as tool


def _write(path, entries):
    path.write_text(yaml.safe_dump({"keys": entries}))
    os.utime(path, (time.time() + 2, time.time() + 2))  # a distinct mtime for every write


def test_minted_keys_are_prefixed_and_unique():
    a, b = mint_key(), mint_key()
    assert a.startswith("mem_") and b.startswith("mem_") and a != b and len(a) > 40


def test_parse_names_the_defective_entry():
    with pytest.raises(ValueError, match=r"keys\[1\]: `sha256` must be 64 hex"):
        parse_entries({"keys": [{"id": "a", "sha256": "0" * 64, "org_id": "acme"},
                                {"id": "b", "sha256": "nope", "org_id": "acme"}]}, source="f")
    with pytest.raises(ValueError, match="duplicate id"):
        parse_entries({"keys": [{"id": "a", "sha256": "0" * 64, "org_id": "acme"}] * 2}, source="f")
    with pytest.raises(ValueError, match="expected a mapping with a `keys` list"):
        parse_entries(["not", "a", "mapping"], source="f")
    with pytest.raises(ValueError, match="`permissions` must be a list of strings"):
        parse_entries({"keys": [{"id": "a", "sha256": "0" * 64, "org_id": "acme",
                                 "permissions": "memory.read"}]}, source="f")


def test_lookup_hit_miss_and_disabled(tmp_path):
    token = mint_key()
    f = tmp_path / "keys.yaml"
    _write(f, [{"id": "k1", "sha256": key_digest(token), "org_id": "acme", "user_id": "sai",
                "permissions": ["memory.read"]}])
    kf = ApiKeyFile(str(f))
    assert kf.load() == 1
    hit = kf.lookup(token)
    assert hit is not None and hit.org_id == "acme" and hit.user_id == "sai"
    assert hit.permissions == ("memory.read",)
    assert kf.lookup(mint_key()) is None and kf.lookup("") is None
    _write(f, [{"id": "k1", "sha256": key_digest(token), "org_id": "acme", "disabled": True}])
    assert kf.lookup(token) is None, "a revoked key stops working without a restart"


def test_a_broken_edit_keeps_the_last_good_set(tmp_path):
    token = mint_key()
    f = tmp_path / "keys.yaml"
    _write(f, [{"id": "k1", "sha256": key_digest(token), "org_id": "acme"}])
    kf = ApiKeyFile(str(f))
    kf.load()
    f.write_text("keys: [ {id: k1, sha256: broken")
    os.utime(f, (time.time() + 4, time.time() + 4))
    assert kf.lookup(token) is not None, "a malformed file neither opens nor closes the door"
    f.write_text("keys:\n  - id: k1\n    sha256: nope\n    org_id: acme\n")
    os.utime(f, (time.time() + 6, time.time() + 6))
    assert kf.lookup(token) is not None


def test_tool_new_list_revoke_round_trip(tmp_path, capsys):
    f = tmp_path / "api_keys.yaml"
    assert tool.main(["new", "--file", str(f), "--id", "laptop", "--org", "acme",
                      "--user", "sai"]) == 0
    token = capsys.readouterr().out.strip()
    assert token.startswith("mem_")
    assert oct(f.stat().st_mode & 0o777) == "0o600"
    assert token not in f.read_text(), "only the hash is written"
    kf = ApiKeyFile(str(f))
    kf.load()
    assert kf.lookup(token).permissions == ("memory.read", "memory.write")
    assert tool.main(["new", "--file", str(f), "--id", "laptop", "--org", "acme"]) == 2, \
        "ids are unique"
    assert tool.main(["list", "--file", str(f)]) == 0
    assert "laptop\tacme\tsai\tmemory.read,memory.write\tactive" in capsys.readouterr().out
    assert tool.main(["revoke", "--file", str(f), "--id", "laptop"]) == 0
    assert tool.main(["revoke", "--file", str(f), "--id", "ghost"]) == 2
    os.utime(f, (time.time() + 2, time.time() + 2))
    assert kf.lookup(token) is None
