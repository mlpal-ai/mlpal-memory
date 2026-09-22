"""PII patterns for the lift rule (memory v6 §5, DESIGN §5): nothing that names a person beyond a
role may sit above the person tier. Deterministic and conservative, like the secret redactor: an
email address, a phone number, a card number that passes Luhn, a national-id shape. Deliberately
NOT matched: cloud account ids, ARNs, hostnames, IPs (they identify the tenant's estate, which the
company tier exists to hold) and person names (no reliable pattern; the lift step asks a person).
"""

from __future__ import annotations

import re

_EMAIL = re.compile(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
# international or US shapes with separators; requires 10-15 digits to avoid matching ids and counts
_PHONE = re.compile(r"(?<![\w/:-])(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{3}\)?[\s.-]?)\d{3}[\s.-]\d{4}(?![\w-])")
_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2
            if d > 9:
                d -= 9
        total += d
        alt = not alt
    return total % 10 == 0


def classify_pii(text: str | None) -> list[str]:
    """Kinds of PII found in ``text``, in a stable order; empty when none."""
    if not text:
        return []
    kinds: list[str] = []
    if _EMAIL.search(text):
        kinds.append("email")
    if _PHONE.search(text):
        kinds.append("phone")
    if _SSN.search(text):
        kinds.append("national-id")
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and _luhn(digits):
            kinds.append("card")
            break
    return kinds


def pii_in_node(name: str | None, summary: str | None, props: dict | None) -> list[str]:
    """The lift rule's view of a node: its name, summary and string props."""
    parts = [name or "", summary or ""]
    for v in (props or {}).values():
        if isinstance(v, str):
            parts.append(v)
    return classify_pii("\n".join(parts))
