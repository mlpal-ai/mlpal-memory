#!/usr/bin/env python3
"""Live-stack verification probes — the maintenance contracts, run for real.

    python evals/run_probes.py                 # freshness + deletion certificate
    python evals/run_probes.py --replay        # + C1 rebuild-equivalence replay (slow)

Each probe runs against the deployed local stack under an isolated probe identity
(its own USER scope), verifies a contract end-to-end through the public API, cleans
up after itself, and emits a machine-readable verdict. Results are appended to
evals/results/<stamp>-probes.json — the same evidence trail as the retrieval evals.

Probes:
  freshness     C4a — two vintages of one fact must invert under recency ranking,
                and the packet must report source ages (Freshness section).
  deletion      D1 ladder — consent CLEAR purges a scope from every read surface
                (search, answer packet, store counts); emits a deletion certificate
                listing each surface checked with counts.
  replay (opt)  C1 canary — re-running the collectors over the identical corpus
                changes nothing: every document is recognized by content identity
                (status=duplicate) and store counts are bit-identical.
"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

EVALS_DIR = Path(__file__).resolve().parent
RESULTS_DIR = EVALS_DIR / "results"
REPO_ROOT = EVALS_DIR.parent


def client_for(base_url: str, org: str, user: str) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=120,
        headers={
            "X-Test-Org-Id": org,
            "X-Test-User-Id": user,
            "X-Test-Permissions": "memory:read,memory:write",
        },
    )


def check(checks: list[dict], name: str, ok: bool, detail: str = "") -> bool:
    checks.append({"check": name, "ok": bool(ok), "detail": detail})
    print(f"    {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))
    return bool(ok)


def ingest(c: httpx.Client, user: str, workspace: str, content: str, valid_at) -> None:
    r = c.post(
        "/api/v1/documents",
        json={
            "content": content,
            "title": content[:40],
            "scope": "user",
            "scope_id": user,
            "source": "md_file",
            "workspace": workspace,
            "valid_at": valid_at.isoformat(),
        },
    )
    r.raise_for_status()


def wait_searchable(c: httpx.Client, query: str, needle: str, timeout_s: int = 60) -> bool:
    """Poll until the fold worker has made `needle` retrievable (async pipeline)."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        r = c.get("/api/v1/memory/search", params={"q": query, "origin": "direct", "limit": 10})
        if r.status_code == 200 and any(
            needle in p["content"] for p in r.json().get("passages", [])
        ):
            return True
        time.sleep(2)
    return False


def reset_consent(c: httpx.Client, user: str) -> None:
    c.put(
        "/api/v1/memory/consent",
        json={"scope": "user", "scope_id": user, "state": "active"},
    ).raise_for_status()


# --------------------------------------------------------------- freshness (C4a)
def probe_freshness(base_url: str, org: str) -> dict:
    user = "probe-freshness"
    ws = "probe-fresh-repo"
    c = client_for(base_url, org, user)
    checks: list[dict] = []
    now = datetime.now(UTC)
    reset_consent(c, user)
    ingest(c, user, ws, "The build system for probe-fresh-repo is maven. Run mvn install.",
           now - timedelta(days=700))
    ingest(c, user, ws, "The build system for probe-fresh-repo is gradle. Run gradle build.",
           now - timedelta(days=2))
    check(checks, "fold completes", wait_searchable(c, "probe-fresh-repo build system", "gradle"))

    r = c.get(
        "/api/v1/memory/answer",
        params={"q": "what build system does probe-fresh-repo use", "workspace": ws},
    )
    md = r.json().get("markdown", "") if r.status_code == 200 else ""
    both = "gradle" in md and "maven" in md
    check(checks, "both vintages retrieved", both)
    check(
        checks, "recency inversion: 2-day-old fact outranks 700-day-old",
        both and md.index("gradle") < md.index("maven"),
    )
    check(checks, "packet reports source ages (Freshness)", "## Freshness" in md)

    # cleanup: purge the probe scope, then re-arm consent for the next run
    c.put("/api/v1/memory/consent",
          json={"scope": "user", "scope_id": user, "state": "clear"}).raise_for_status()
    reset_consent(c, user)
    return {"probe": "freshness", "pass": all(x["ok"] for x in checks), "checks": checks}


# --------------------------------------------------- deletion certificate (D1)
def probe_deletion(base_url: str, org: str) -> dict:
    user = "probe-deletion"
    ws = "probe-del-repo"
    marker = "zanzibar-quorum-cactus"  # unique token: retrievable iff the doc exists
    c = client_for(base_url, org, user)
    checks: list[dict] = []
    reset_consent(c, user)

    ingest(c, user, ws, f"The staging database password rotation runs via {marker} tooling "
           "every thursday at 03:00 UTC.", datetime.now(UTC))
    check(checks, "document ingested and searchable", wait_searchable(c, marker, marker))

    r = c.put("/api/v1/memory/consent",
              json={"scope": "user", "scope_id": user, "state": "clear"})
    r.raise_for_status()
    purged = r.json()
    check(
        checks, "purge acknowledged with counts",
        purged.get("purged_documents", 0) >= 1 and purged.get("purged_chunks", 0) >= 1,
        f"documents={purged.get('purged_documents')} chunks={purged.get('purged_chunks')} "
        f"nodes={purged.get('purged_nodes')} edges={purged.get('purged_edges')}",
    )

    reset_consent(c, user)  # re-arm so the read checks aren't consent-masked
    r = c.get("/api/v1/memory/search", params={"q": marker, "origin": "direct", "limit": 10})
    gone_search = not any(marker in p["content"] for p in r.json().get("passages", []))
    check(checks, "surface: /memory/search returns nothing", gone_search)

    r = c.get("/api/v1/memory/answer", params={"q": f"when does {marker} rotation run"})
    body = r.json()
    # the packet's H1 echoes the query (which contains the marker) — check the body below it
    md_body = "\n".join(body.get("markdown", "").splitlines()[1:])
    check(
        checks, "surface: /memory/answer abstains (Gaps)",
        marker not in md_body and body.get("passages") == 0 and body.get("facts") == 0,
    )

    cert = {
        "probe": "deletion_certificate",
        "pass": all(x["ok"] for x in checks),
        "checks": checks,
        "certificate": {
            "subject": f"user:{user}",
            "purged": {k: v for k, v in purged.items() if k.startswith("purged_")},
            "surfaces_verified": ["memory/search (direct)", "memory/answer (packet)"],
            "verified_at": datetime.now(UTC).isoformat(timespec="seconds"),
        },
    }
    return cert


# ------------------------------------------------------- rebuild replay (C1)
def probe_replay(base_url: str, org: str, user: str) -> dict:
    """Two back-to-back full collector replays with forgotten local state. Run 1 absorbs
    real drift (files changed since the last collection); run 2 must be a no-op: every
    document recognized by content identity, store counts bit-identical."""
    checks: list[dict] = []
    c = client_for(base_url, org, user)

    def collect(tag: str) -> dict:
        out = EVALS_DIR / "results" / f"replay-{tag}.json"
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "collect_local.py"),
             "--source", "all", "--reset-state", "--base-url", base_url,
             "--org", org, "--user", user, "--json-out", str(out)],
            capture_output=True, text=True, timeout=1800,
        )
        if r.returncode != 0:
            print(r.stderr[-2000:], file=sys.stderr)
            return {"errors": -1}
        return json.loads(out.read_text())

    print("    replay 1/2 (absorbing drift since last collection)…")
    s1 = collect("run1")
    check(checks, "replay 1 clean", s1.get("errors") == 0,
          f"docs={s1.get('documents')} new={len(s1.get('processed_uris', []))}")

    before = c.get("/api/v1/memory/stats").json()
    print("    replay 2/2 (equivalence run)…")
    s2 = collect("run2")
    after = c.get("/api/v1/memory/stats").json()

    check(checks, "replay 2 clean", s2.get("errors") == 0)
    check(
        checks, "every document recognized by content identity",
        s2.get("documents", 0) > 0 and not s2.get("processed_uris"),
        f"{s2.get('duplicates')}/{s2.get('documents')} duplicate"
        + (f"; NEW: {s2['processed_uris'][:3]}" if s2.get("processed_uris") else ""),
    )
    counts_equal = all(before.get(k) == after.get(k)
                       for k in ("documents", "chunks", "nodes", "edges"))
    check(
        checks, "store counts bit-identical across replay", counts_equal,
        f"before={{d:{before.get('documents')},c:{before.get('chunks')},"
        f"n:{before.get('nodes')},e:{before.get('edges')}}} "
        f"after={{d:{after.get('documents')},c:{after.get('chunks')},"
        f"n:{after.get('nodes')},e:{after.get('edges')}}}",
    )
    return {"probe": "rebuild_replay", "pass": all(x["ok"] for x in checks), "checks": checks}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", default="http://localhost:8000")
    ap.add_argument("--org", default="local")
    ap.add_argument("--user", default=getpass.getuser(), help="identity for the replay probe")
    ap.add_argument("--replay", action="store_true", help="include the slow C1 replay canary")
    args = ap.parse_args()

    results = []
    print("▸ freshness (C4a)")
    results.append(probe_freshness(args.base_url, args.org))
    print("▸ deletion certificate (D1)")
    results.append(probe_deletion(args.base_url, args.org))
    if args.replay:
        print("▸ rebuild replay (C1)")
        results.append(probe_replay(args.base_url, args.org, args.user))

    RESULTS_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    out = RESULTS_DIR / f"{stamp}-probes.json"
    out.write_text(json.dumps(
        {"at": datetime.now(UTC).isoformat(timespec="seconds"), "probes": results}, indent=2
    ))
    ok = all(p["pass"] for p in results)
    print(f"\n{'ALL PROBES PASS' if ok else 'PROBE FAILURES'} → {out.relative_to(REPO_ROOT)}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
