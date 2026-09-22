"""Manage the static API key file of a self-hosted instance (core/api_keys.py).

    python -m mlpal_memory_graph.tools.api_keys new --file api_keys.yaml --id sai-laptop \
        --org acme --user sai --permissions memory.read,memory.write
    python -m mlpal_memory_graph.tools.api_keys list --file api_keys.yaml
    python -m mlpal_memory_graph.tools.api_keys revoke --file api_keys.yaml --id sai-laptop

`new` prints the key once; only its hash is written. The file is created on first use with
owner-only permissions.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import yaml

from ..core.api_keys import key_digest, mint_key, parse_entries

DEFAULT_PERMISSIONS = "memory.read,memory.write"


def _read(path: Path) -> dict:
    if not path.exists():
        return {"keys": []}
    data = yaml.safe_load(path.read_text()) or {"keys": []}
    parse_entries(data, source=str(path))
    return data


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False)
    os.replace(tmp, path)


def cmd_new(args: argparse.Namespace) -> int:
    path = Path(args.file)
    data = _read(path)
    if any(str(k.get("id")) == args.id for k in data["keys"]):
        print(f"error: a key with id {args.id!r} already exists in {path}", file=sys.stderr)
        return 2
    token = mint_key()
    entry = {"id": args.id, "sha256": key_digest(token), "org_id": args.org,
             "permissions": [p for p in args.permissions.split(",") if p]}
    if args.user:
        entry["user_id"] = args.user
    data["keys"].append(entry)
    _write(path, data)
    print(token)
    print(f"# key {args.id!r} for org {args.org!r} written to {path}; "
          "the key above is shown once", file=sys.stderr)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for _, e in parse_entries(_read(Path(args.file)), source=args.file):
        state = "disabled" if e.disabled else "active"
        perms = ",".join(e.permissions) or "-"
        print(f"{e.key_id}\t{e.org_id}\t{e.user_id or '-'}\t{perms}\t{state}")
    return 0


def cmd_revoke(args: argparse.Namespace) -> int:
    path = Path(args.file)
    data = _read(path)
    rows = [k for k in data["keys"] if str(k.get("id")) == args.id]
    if not rows:
        print(f"error: no key with id {args.id!r} in {path}", file=sys.stderr)
        return 2
    rows[0]["disabled"] = True
    _write(path, data)
    print(f"revoked {args.id!r}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    new = sub.add_parser("new", help="mint a key and append its hash")
    new.add_argument("--file", required=True)
    new.add_argument("--id", required=True, help="a name for the key (who or what holds it)")
    new.add_argument("--org", required=True, help="the tenant the key is pinned to")
    new.add_argument("--user", default=None)
    new.add_argument("--permissions", default=DEFAULT_PERMISSIONS)
    new.set_defaults(fn=cmd_new)
    ls = sub.add_parser("list")
    ls.add_argument("--file", required=True)
    ls.set_defaults(fn=cmd_list)
    rv = sub.add_parser("revoke", help="disable a key without deleting its row")
    rv.add_argument("--file", required=True)
    rv.add_argument("--id", required=True)
    rv.set_defaults(fn=cmd_revoke)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
