"""Value-fact extraction — the mechanism three timeline experiments demanded.

x6/x6b/x6c all converged on the same failure: quantitative drift (a cost, a
version) loses to repetition mass because passages carry no notion of "the
current value of X". This pass extracts WATCHED VALUES deterministically at
fold time into an anchor-plus-values shape the existing bitemporal machinery
already handles:

    (Metric anchor, stable key)  --HAS_VALUE (functional)-->  (MetricValue)

HAS_VALUE is functional: a new value's edge auto-supersedes the old value's
edge (deterministic, valid-time aware, backfill-safe — postgres driver
``invalidate_superseded``), and the superseded MetricValue node is marked
status="superseded" so current-view packets stop serving it as a fact while
as-of reads still reconstruct it through edge validity.

Patterns are a small default library (extendable per-org later via sources
config). Rule-tier and LLM-tier folds both run this pass — it is cheap,
deterministic, and replayable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .extractor import EdgeSpec, EntitySpec


def _normalize_value(raw: str) -> str:
    """Strip currency/unit clothing so the same value has ONE identity: "$32/day",
    "32/day" and "32" are one observation, not three (x6c5 histories)."""
    v = raw.strip().lstrip("$~").strip()
    for suffix in ("/day", " per day", " a day", "$"):
        if v.lower().endswith(suffix):
            v = v[: -len(suffix)].strip()
    return v.rstrip("$").strip()


@dataclass(frozen=True)
class ValuePattern:
    key: str          # stable metric key, e.g. "metric:daily-cost"
    label: str        # human name for the anchor node
    regex: re.Pattern
    unit: str = ""
    # subject guards, checked against the ±60-char window around a match: a watched
    # value is (subject + pattern), never pattern alone — x6c2 measured the failure
    # (the OLD ACCOUNT's teardown billing captured as the platform's daily cost).
    require: re.Pattern | None = None
    exclude: re.Pattern | None = None
    # value validation (x6c5: the LLM tier emitted "<UNKNOWN>", unit-suffixed and
    # patch-versioned values — a value field is only a value if it parses as one)
    value_re: re.Pattern = re.compile(r"^\d+(?:\.\d+)?$")


DEFAULT_PATTERNS: tuple[ValuePattern, ...] = (
    ValuePattern(
        "metric:daily-cost", "platform daily cost",
        re.compile(
            # verb-anchored ("costs $32 per day") OR verbless-stative ("steady-state:
            # ~$32/day" — retrospectives state values without verbs; x6c3 missed one)
            r"(?:costs?|paying|bill(?:ed)?[^.\n]{0,20}?|cost check[^$\n]{0,20}?)"
            r"\s*(?:about |roughly |~|still )?\$\s?(\d+(?:\.\d+)?)\s*(?:per day|/day|a day)"
            r"|(?:steady[- ]state|→|—|:)[^$\n]{0,40}?\*{0,2}~?\$\s?(\d+(?:\.\d+)?)/day",
            re.I,
        ),
        "$/day",
        require=re.compile(r"platform|total|steady[- ]state|we are|paying", re.I),
        exclude=re.compile(r"old account|old-account|would|could|if we|floor|projected", re.I),
    ),
    ValuePattern(
        "setting:k8s-version", "kubernetes version",
        re.compile(
            r"kubernetes(?:\s+version)?\s*(?:is now|is|upgraded[^\d]{0,12}to|runs?|=)?\s*"
            r"v?(1\.\d{2})\b"
            # continuation form: "… 1.33 upgraded to 1.36" / "1.33 → 1.36" — the guard
            # (require) keeps stray "to 1.36" lines out
            r"|(?:upgraded[^\d\n]{0,12}to|→)\s*v?(1\.\d{2})\b",
            re.I,
        ),
        "k8s",
        require=re.compile(r"kubernetes|k8s", re.I),
        value_re=re.compile(r"^1\.\d{2}$"),
    ),
    ValuePattern(
        "setting:eks-version", "eks cluster version",
        re.compile(
            r"\beks\b[^\d\n]{0,30}v?(1\.\d{2})\b"
            r"|(?:upgraded[^\d\n]{0,12}to|→)\s*v?(1\.\d{2})\b",
            re.I,
        ),
        "eks",
        require=re.compile(r"\beks\b|kubernetes", re.I),
        value_re=re.compile(r"^1\.\d{2}$"),
    ),
)


VALUE_LLM_SYSTEM = """You extract the CURRENT values of watched metrics from one document.
Watched metrics (report ONLY these keys):
{keys}
Rules:
- Report a key ONLY if the document states a value for it about THE PLATFORM ITSELF
  (never another entity like an old/other account, never hypotheticals, projections,
  floors, or per-item prices).
- If the document narrates a change ("was X, now Y"), report the FINAL/current value.
- quote MUST be copied verbatim from the document (it is checked; paraphrase = dropped).
- Omit keys the document does not clearly value. Output STRICT JSON:
  {{"values": [{{"key": "...", "value": "...", "quote": "..."}}]}}"""

VALUE_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "values": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                    "quote": {"type": "string"},
                },
                "required": ["key", "value", "quote"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["values"],
    "additionalProperties": False,
}


async def llm_extract_value_specs(
    text: str | None, patterns: tuple[ValuePattern, ...] = DEFAULT_PATTERNS
) -> tuple[list[EntitySpec], list[EdgeSpec]]:
    """Precision tier (x6c3/x6c4: three pattern-retune rounds each exposed a new
    failure class — subject leakage, multi-era retrospectives, window misses; the
    regex tier has a measured ceiling on narrative corpora). One haiku-class call
    per document that COARSELY matches a watched pattern (cost gate), closed
    schema, verbatim-quote validation, same (entities, edges) shape. Falls back
    to the pattern tier on any model failure."""
    if not text:
        return [], []
    # cost gate: only spend the call when a watched pattern coarsely fires
    if not any(p.regex.search(text) for p in patterns):
        return [], []
    from ..services.llm_client import get_llm_client

    by_key = {p.key: p for p in patterns}
    keys_desc = "\n".join(f"- {p.key}: {p.label} ({p.unit or 'value'})" for p in patterns)
    try:
        out = await get_llm_client().complete_json(
            system=VALUE_LLM_SYSTEM.format(keys=keys_desc),
            user=text[:20_000],
            schema=VALUE_LLM_SCHEMA,
            max_tokens=500,
        )
    except Exception:  # noqa: BLE001 — precision tier degrades to the pattern tier
        return extract_value_specs(text, patterns)
    entities: list[EntitySpec] = []
    edges: list[EdgeSpec] = []
    seen_keys: set[str] = set()
    for item in out.get("values", []):
        pat = by_key.get(str(item.get("key", "")))
        value = _normalize_value(str(item.get("value", "")))
        quote = str(item.get("quote", ""))
        if not pat or not value or pat.key in seen_keys or quote not in text:
            continue  # unknown key, duplicate, or ungrounded quote -> dropped
        if not pat.value_re.match(value):
            # truncate a patch version to the key's granularity before rejecting
            trimmed = ".".join(value.split(".")[:2])
            if pat.value_re.match(trimmed):
                value = trimmed
            else:
                continue  # "<UNKNOWN>" and friends: not a value
        seen_keys.add(pat.key)
        display = f"{pat.label} = {value}" + (f" {pat.unit}" if pat.unit else "")
        entities.append(EntitySpec(type="Metric", key=pat.key, name=pat.label))
        entities.append(
            EntitySpec(
                type="MetricValue", key=f"{pat.key}={value}", name=display,
                props={"value": value, "unit": pat.unit, "evidence_span": quote[:300]},
            )
        )
        edges.append(
            EdgeSpec(
                type="HAS_VALUE",
                src_type="Metric", src_key=pat.key,
                dst_type="MetricValue", dst_key=f"{pat.key}={value}",
                fact=display, functional=True, props={"value": value},
            )
        )
    return entities, edges


def extract_value_specs(
    text: str | None, patterns: tuple[ValuePattern, ...] = DEFAULT_PATTERNS
) -> tuple[list[EntitySpec], list[EdgeSpec]]:
    """Scan ``text`` for watched values. Multiple mentions of the SAME value in one
    document collapse to one observation; different values in one document each
    produce an observation (the fold's valid-time ordering settles them)."""
    if not text:
        return [], []
    entities: list[EntitySpec] = []
    edges: list[EdgeSpec] = []
    for pat in patterns:
        # ONE observation per key per document — the LAST surviving match. Rationale
        # (x6c3 metrics view): retrospectives state old AND new values with one
        # valid_at ("was $52.6/day … now ~$32/day", "1.33 → 1.36"); emitting both
        # ping-pongs supersession, and narrative order ends on the current value.
        last = None
        for m in pat.regex.finditer(text):
            value = next((g for g in m.groups() if g), None)
            if value is None:
                continue
            window = text[max(0, m.start() - 60): m.end() + 60]
            if pat.require and not pat.require.search(window):
                continue
            if pat.exclude and pat.exclude.search(window):
                continue
            last = (value, window.strip())
        if last is None:
            continue
        value, span = last
        value = _normalize_value(value)
        if not pat.value_re.match(value):
            continue
        anchor_key = pat.key
        value_key = f"{pat.key}={value}"
        display = f"{pat.label} = {value}" + (f" {pat.unit}" if pat.unit else "")
        entities.append(EntitySpec(type="Metric", key=anchor_key, name=pat.label))
        entities.append(
            EntitySpec(
                type="MetricValue", key=value_key, name=display,
                props={"value": value, "unit": pat.unit, "evidence_span": span[:300]},
            )
        )
        edges.append(
            EdgeSpec(
                type="HAS_VALUE",
                src_type="Metric", src_key=anchor_key,
                dst_type="MetricValue", dst_key=value_key,
                fact=display,
                functional=True,
                props={"value": value},
            )
        )
    return entities, edges
