"""Detect secrets in free text and replace them with vault references before extraction.

Runs in the fold pipeline ahead of extraction/persistence, so neither the extractor nor the
graph ever sees plaintext credentials. Matches two shapes: (1) ``key = value`` assignments for
sensitive keys (the value is vaulted, the key kept for context), and (2) well-known token
formats (AWS keys, ``mlpal_sk_``/``ghp_``/Slack tokens, PEM private keys). Conservative by
design — better to miss an exotic format than to corrupt prose with false positives.
See design-proposal §2.3.
"""

from __future__ import annotations

import re

from .secrets import SecretVault, make_reference

# key = "value" / key: value  for sensitive keys (value captured in group 'secret')
_ASSIGNMENT = re.compile(
    r"(?i)\b(?P<key>password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|"
    r"client[_-]?secret|private[_-]?key)\b(?P<op>\s*[:=]\s*)"
    r"(?P<q>[\"']?)(?P<secret>[^\s\"']{4,})(?P=q)"
)

# standalone token formats (whole match is the secret)
_TOKEN_PATTERNS: tuple[re.Pattern, ...] = (
    re.compile(r"(?P<secret>AKIA[0-9A-Z]{16})"),
    re.compile(r"(?P<secret>mlpal_sk_[A-Za-z0-9]{8,})"),
    re.compile(r"(?P<secret>ghp_[A-Za-z0-9]{20,})"),
    re.compile(r"(?P<secret>xox[baprs]-[A-Za-z0-9-]{10,})"),
    re.compile(
        r"(?P<secret>-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----)"
    ),
)


def redact(text: str | None, vault: SecretVault) -> tuple[str | None, list[str]]:
    """Return (redacted_text, refs). ``refs`` is the list of vault references substituted."""
    if not text:
        return text, []
    refs: list[str] = []

    def _vault(secret: str) -> str:
        ref = vault.store(secret)
        refs.append(ref)
        return make_reference(ref)

    def _assign(m: re.Match) -> str:
        return f"{m.group('key')}{m.group('op')}{_vault(m.group('secret'))}"

    text = _ASSIGNMENT.sub(_assign, text)
    for pat in _TOKEN_PATTERNS:
        text = pat.sub(lambda m: _vault(m.group("secret")), text)
    return text, refs


def redact_mapping(data: dict, vault: SecretVault) -> tuple[dict, list[str]]:
    """Redact every string value in a shallow mapping (e.g. an episode payload)."""
    if not data:
        return data, []
    out, refs = {}, []
    for key, value in data.items():
        if isinstance(value, str):
            redacted, found = redact(value, vault)
            out[key] = redacted
            refs.extend(found)
        else:
            out[key] = value
    return out, refs
