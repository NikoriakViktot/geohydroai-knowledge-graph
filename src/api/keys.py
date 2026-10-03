"""Manage API consumer keys (ops.api_key). Only the sha256 of a key is stored.

    python -m src.api.keys create --consumer floodstate-eo --scopes read,llm --note "Paper 3 repo"
    python -m src.api.keys list
    python -m src.api.keys revoke --key-id <uuid>

`create` prints the key once; put it in the consumer's .env as GHAI_API_KEY.
"""

from __future__ import annotations

import argparse
import secrets
from datetime import datetime, timezone

from sqlalchemy import select, update

from src.api.deps import hash_key
from src.db.engine import session_scope
from src.db.models import ApiKey

SCOPES = ("read", "llm", "write", "admin", "verify")


def create(consumer: str, scopes: list[str], note: str | None = None) -> tuple[str, str]:
    bad = set(scopes) - set(SCOPES)
    if bad:
        raise SystemExit(f"unknown scopes {sorted(bad)}; allowed {SCOPES}")
    raw = "ghai_" + secrets.token_urlsafe(32)
    with session_scope() as s:
        row = ApiKey(consumer=consumer, scopes=sorted(set(scopes)), key_hash=hash_key(raw), note=note)
        s.add(row)
        s.flush()
        key_id = str(row.key_id)
    return key_id, raw


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create")
    c.add_argument("--consumer", required=True)
    c.add_argument("--scopes", required=True, help="comma list of read,llm,write,admin,verify")
    c.add_argument("--note")
    sub.add_parser("list")
    r = sub.add_parser("revoke")
    r.add_argument("--key-id", required=True)
    g = sub.add_parser("grant", help="add a scope to an active key ('verify' is for people only)")
    g.add_argument("--key-id", required=True)
    g.add_argument("--scope", required=True, choices=SCOPES)
    args = ap.parse_args(argv)

    if args.cmd == "create":
        key_id, raw = create(args.consumer, [x.strip() for x in args.scopes.split(",") if x.strip()], args.note)
        print(f"key_id={key_id}\nGHAI_API_KEY={raw}\n(shown once; only its hash is stored)")
    elif args.cmd == "list":
        with session_scope() as s:
            for k in s.scalars(select(ApiKey).order_by(ApiKey.created_at)):
                state = "revoked" if k.revoked_at else "active"
                print(f"{k.key_id}  {k.consumer:20s} {','.join(k.scopes):24s} {state:8s} {k.created_at:%Y-%m-%d}  {k.note or ''}")
    elif args.cmd == "grant":
        with session_scope() as s:
            k = s.scalar(select(ApiKey).where(ApiKey.key_id == args.key_id, ApiKey.revoked_at.is_(None)))
            if k is None:
                print("no active key with that id")
                return 1
            k.scopes = sorted(set(k.scopes) | {args.scope})
            print(f"{k.consumer}: {','.join(k.scopes)}")
    else:
        with session_scope() as s:
            n = s.execute(update(ApiKey).where(ApiKey.key_id == args.key_id, ApiKey.revoked_at.is_(None))
                          .values(revoked_at=datetime.now(timezone.utc))).rowcount
        print("revoked" if n else "no active key with that id")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
