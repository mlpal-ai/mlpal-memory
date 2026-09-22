"""memory v9 C4: a running state per topic, folded at write time from conversation sessions.

The head-to-head (v8, LEARNINGS L14, L31, L36) left one question type where the extraction systems
beat passages: "how many / how much in total across sessions". A reader over passages over- or
under-counts, and a list of dated facts beside the passages is trusted even when incomplete. What
answers those questions is one complete, current line per topic — Memobase's profile slot — in our
claim-fold shape: a `Metric` anchor `state:conv/<topic>:<user>` whose current `MetricValue` is the
rendered tally, superseded on every session that touches the topic.

This module is the deterministic part: given the topic's previous state and the session's facts
(from `ConversationTopicExtractor`), the new state and its rendering. No model call here.

Fold rules (per topic, facts in date order):
- `add`: one more event, purchase or item — count += 1 (or the quantity when its unit is a count),
  and the quantity joins the total for its unit (USD, hours, km …).
- `set`: the fact states the current total or value ("I now have 25 titles on my list") — the
  tally of that unit is reset to it as of that date. An `add` dated on or before the last `set` is
  already inside the set value and is skipped (the "27 titles" over-count of v8 L31).
- `remove`: the inverse of `add`.
- `none`: recorded as an item, no tally change.
"""

from __future__ import annotations

import re
from typing import Any

MAX_ITEMS = 40          # items kept in the state props (evidence for the tally)
MAX_RENDER = 400        # the MetricValue text cap the state fold already enforces
COUNT_UNITS = {None, "", "items", "item", "count", "times", "events", "pieces", "units"}

_WS = re.compile(r"\s+")


def topic_slug(topic: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (topic or "").lower()).strip("-")[:80] or "topic"


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # NaN guard


def _fmt(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else f"{v:.2f}".rstrip("0").rstrip(".")


def fold_topic(state: dict | None, facts: list[dict], as_of: str) -> dict:
    """The topic's new state after this session's facts. `state` is the previous MetricValue props
    (or None); each fact is `{fact, date, op, qty, unit, source_uri?}`. Pure and idempotent for a
    replayed session when the caller dedups on (date, fact)."""
    st: dict[str, Any] = {
        "count": 0, "sums": {}, "items": [], "set": None, "as_of": as_of,
        **{k: v for k, v in (state or {}).items() if k in ("count", "sums", "items", "set")},
    }
    st["sums"] = dict(st.get("sums") or {})
    st["items"] = list(st.get("items") or [])
    seen = {(i.get("date"), _WS.sub(" ", (i.get("fact") or "").strip().lower())) for i in st["items"]}
    ordered = sorted(facts, key=lambda f: (f.get("date") or as_of))
    for f in ordered:
        date = f.get("date") or as_of
        key = (date, _WS.sub(" ", (f.get("fact") or "").strip().lower()))
        if key in seen:
            continue
        seen.add(key)
        op = (f.get("op") or "none").lower()
        qty = _num(f.get("qty"))
        unit = (f.get("unit") or "").strip().lower() or None
        countish = unit in COUNT_UNITS
        if op == "set":
            if qty is not None:
                if countish:
                    st["count"] = int(qty) if qty.is_integer() else qty
                else:
                    st["sums"][unit] = qty
                st["set"] = {"date": date, "value": qty, "unit": unit}
        elif op in ("add", "remove"):
            last_set = st.get("set")
            if last_set and date <= last_set["date"] and (countish or last_set.get("unit") == unit):
                op = "none"  # already inside the stated total
            else:
                sign = 1 if op == "add" else -1
                if countish:
                    st["count"] += sign * (int(qty) if qty is not None and qty.is_integer() else (qty if qty is not None else 1))
                else:
                    st["count"] += sign
                    if qty is not None:
                        st["sums"][unit] = st["sums"].get(unit, 0) + sign * qty
        item = {"date": date, "fact": (f.get("fact") or "").strip()[:160], "op": op}
        if qty is not None:
            item["qty"] = qty
        if unit:
            item["unit"] = unit
        if f.get("source_uri"):
            item["source_uri"] = f["source_uri"]
        st["items"].append(item)
    st["items"] = sorted(st["items"], key=lambda i: i.get("date") or "")[-MAX_ITEMS:]
    st["as_of"] = max([as_of] + [i.get("date") or "" for i in st["items"]])
    if st["count"] < 0:
        st["count"] = 0
    return st


def render_topic_state(topic: str, st: dict) -> str:
    """One line a reader can trust: the tally, its date, then the dated items that make it up,
    newest last, cut at the value cap."""
    head = f"{topic}: {_fmt(st.get('count', 0))} items as of {st.get('as_of')}"
    for unit, total in sorted((st.get("sums") or {}).items()):
        head += f"; total {_fmt(total)} {unit}"
    if st.get("set"):
        s = st["set"]
        head += f" (stated {_fmt(s['value'])}{' ' + s['unit'] if s.get('unit') else ''} on {s['date']})"
    parts = []
    for i in st.get("items") or []:
        if i.get("op") == "none":
            continue
        q = f" [{_fmt(i['qty'])}{' ' + i['unit'] if i.get('unit') else ''}]" if i.get("qty") is not None else ""
        parts.append(f"{i.get('date')} {i.get('fact')}{q}")
    text = head + (": " + "; ".join(parts) if parts else "")
    if len(text) > MAX_RENDER:
        text = text[: MAX_RENDER - 1].rsplit(";", 1)[0] + "…"
    return text


async def load_topic_states(session, *, tenant_id: str | None, user: str) -> dict[str, tuple[str, dict]]:
    """The user's current topic states in this tenant: slug → (label, MetricValue props), read
    through the live HAS_VALUE edge of each `state:conv/<slug>:<user>` anchor."""
    from sqlalchemy import select

    from ..db.models import Edge, Node

    prefix = "state:conv/"
    suffix = f":{user}"
    q = (
        select(Node.key, Node.props, Edge.dst_id)
        .join(Edge, (Edge.src_id == Node.id) & (Edge.type == "HAS_VALUE") & (Edge.invalid_at.is_(None)))
        .where(Node.type == "Metric", Node.key.like(f"{prefix}%{suffix}"))
    )
    if tenant_id is not None:
        q = q.where(Node.org_id == tenant_id)
    rows = (await session.execute(q)).all()
    if not rows:
        return {}
    values = {n.id: n for n in (await session.execute(select(Node).where(Node.id.in_([r[2] for r in rows])))).scalars()}
    out: dict[str, tuple[str, dict]] = {}
    for key, props, dst in rows:
        if not key.endswith(suffix):
            continue
        slug = key[len(prefix): -len(suffix)]
        v = values.get(dst)
        if v is None:
            continue
        out[slug] = ((props or {}).get("label") or slug.replace("-", " "), dict(v.props or {}))
    return out
