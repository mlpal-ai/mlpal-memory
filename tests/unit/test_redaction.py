from __future__ import annotations

from mlpal_memory_graph.services.redaction import redact, redact_mapping
from mlpal_memory_graph.services.secrets import DevSecretVault


def test_assignment_value_is_vaulted():
    text, refs = redact('the prod db password = hunter2 stays secret', DevSecretVault())
    assert "hunter2" not in text
    assert "mlpal-secret://" in text
    assert len(refs) == 1


def test_known_token_formats_are_vaulted():
    vault = DevSecretVault()
    text, refs = redact("key AKIAIOSFODNN7EXAMPLE and mlpal_sk_abcdef123456", vault)
    assert "AKIAIOSFODNN7EXAMPLE" not in text
    assert "mlpal_sk_abcdef123456" not in text
    assert len(refs) == 2


def test_same_secret_dedupes_to_same_reference():
    vault = DevSecretVault()
    assert vault.store("hunter2") == vault.store("hunter2")


def test_plain_prose_untouched():
    text, refs = redact("the team standardizes on postgres for storage", DevSecretVault())
    assert refs == []
    assert "postgres" in text


def test_redact_mapping_only_touches_strings():
    payload = {"statement": "token = supersecretvalue", "count": 5}
    out, refs = redact_mapping(payload, DevSecretVault())
    assert out["count"] == 5
    assert "supersecretvalue" not in out["statement"]
    assert len(refs) == 1
