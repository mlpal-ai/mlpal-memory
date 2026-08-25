"""Secret vault seam — memory must never hold plaintext credentials.

When the redactor (``services/redaction.py``) finds a secret, it hands the raw value here;
the vault stores it out-of-band and returns a stable reference. Only the reference
(``mlpal-secret://<ref>``) is ever written into memory. Production points this at the
platform ``mlpal-secrets`` service; local/dev/test uses a deterministic in-process vault so
the pipeline runs offline and redaction is testable. See design-proposal §2.3 (guardrails).
"""

from __future__ import annotations

import hashlib
from functools import lru_cache
from typing import Protocol

REFERENCE_SCHEME = "mlpal-secret"


def make_reference(ref: str) -> str:
    return f"{REFERENCE_SCHEME}://{ref}"


class SecretVault(Protocol):
    def store(self, secret: str) -> str:
        """Persist ``secret`` out-of-band; return a stable reference id (same in → same ref)."""
        ...


class DevSecretVault:
    """Deterministic in-process vault. The ref is a content hash, so the same secret dedupes
    to the same reference. Never logs or returns the plaintext."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def store(self, secret: str) -> str:
        ref = hashlib.sha256(secret.encode()).hexdigest()[:16]
        self._store[ref] = secret
        return ref


@lru_cache
def get_vault() -> SecretVault:
    # Production wiring (mlpal-secrets HTTP client) slots in here behind the same Protocol;
    # the dev vault keeps secrets out of memory for local/test without external deps.
    return DevSecretVault()
