"""memory_report — the owner's weekly summary for one HOP, from the service's /memory/report.

    uv run python -m mlpal_memory_graph.tools.memory_report --org hop-infra-real --hop infra --hop-alias infra-ro=infra [--days 7] [--out FILE]

Prices tokens with the engine's cached catalog (~/.yodex/catalog-*.json, compute units per 1M) when
present; otherwise reports tokens only. Dev headers are used against a local service.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import urllib.parse
import urllib.request


def catalog_prices() -> dict[str, tuple[float, float]]:
    out = {}
    for p in sorted(pathlib.Path(os.path.expanduser("~/.yodex")).glob("catalog-*.json")):
        try:
            d = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        for k, m in ((d.get("catalog") or d).get("models") or {}).items():
            c = (m or {}).get("cost") or {}
            if "input_cu_per_1m" in c:
                out.setdefault(k, (float(c["input_cu_per_1m"]), float(c["output_cu_per_1m"])))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--org", required=True); ap.add_argument("--hop", default=None); ap.add_argument("--hop-alias", default=None)
    ap.add_argument("--days", type=int, default=7); ap.add_argument("--base", default=os.environ.get("MLPAL_MEMORY_SERVICE_URL", "http://localhost:8000"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    q = {"window_days": a.days, **({"hop": a.hop} if a.hop else {}), **({"hop_alias": a.hop_alias} if a.hop_alias else {})}
    req = urllib.request.Request(f"{a.base}/api/v1/memory/report?" + urllib.parse.urlencode(q),
                                 headers={"X-Test-Org-Id": a.org, "X-Test-User-Id": "owner", "X-Test-Permissions": "memory.read"})
    with urllib.request.urlopen(req, timeout=120) as r:
        rep = json.loads(r.read().decode())
    from ..services.report import render_markdown

    md = render_markdown(rep)
    prices = catalog_prices(); cu = 0.0; priced = 0
    for model, t in rep["cost"]["tokens_by_model"].items():
        if model in prices:
            pin, pout = prices[model]; priced += 1
            cu += (t["input"] * pin + t["cache_read"] * pin * 0.10 + t["cache_write"] * pin * 1.25 + t["output"] * pout) / 1_000_000
    if priced:
        md += f"\nCost: {cu:.2f} CU over the window ({priced} priced model(s); cache read at 10 %, cache write at 125 % of input)\n"
    print(md)
    if a.out:
        pathlib.Path(a.out).write_text(md + "\n```json\n" + json.dumps(rep, indent=2) + "\n```\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
