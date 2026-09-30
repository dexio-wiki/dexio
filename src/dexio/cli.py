"""Operator commands for the Dexio server: run it, manage accounts in its
database, and copy a database. Customers never use this: agents work through
MCP and people through the web app. The customer-facing commands (scan, lint,
html, push, pull) were removed 2026-09-26 (Forrest): nothing used them and the
package was never published. Bulk import over HTTPS (POST /api/v1/push) was
removed 2026-09-27 (Forrest); export (GET /api/v1/export) was removed with it and
came back 2026-09-28 (Forrest)."""
from __future__ import annotations

import argparse
import os
import sys
import time

from . import __version__


def cmd_serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print("the server needs the extra: pip install 'dexio[server]'", file=sys.stderr)
        return 2
    from .server.app import get_app
    app = get_app(args.db)
    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)
    return 0


def cmd_user(args) -> int:
    import getpass

    from .server import auth, db

    conn = db.connect(args.db)
    auth.init(conn)
    db.seed_memberships(conn)

    if args.action == "list":
        users = auth.list_users(conn)
        if not users:
            print("no accounts")
        for u in users:
            last = time.strftime("%Y-%m-%d", time.localtime(u["last_login"])) \
                if u["last_login"] else "never"
            print(f"{u['email']:<40} last login {last}")
        return 0

    if not args.email:
        print(f"user {args.action} needs an email address", file=sys.stderr)
        return 2

    password = args.password or getpass.getpass("password: ")
    try:
        if args.action == "add":
            uid = auth.create_user(conn, args.email, password)
            # Operator-made accounts join the default workspace as owners;
            # self-serve signups get a workspace of their own instead.
            db.add_member(conn, db.default_workspace(conn), uid, "owner")
            print(f"created {args.email}")
        else:
            if not auth.set_password(conn, args.email, password):
                print(f"no such user: {args.email}", file=sys.stderr)
                return 1
            print(f"password updated for {args.email}")
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    except Exception as e:  # sqlite UNIQUE, mostly
        print(f"could not save user: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_signups(args) -> int:
    """Where accounts came from over the last N days (see server/signups.py)."""
    import json
    from .server import db, signups
    conn = db.connect(args.db)
    signups.init(conn)
    print(json.dumps(signups.report(conn, args.days), indent=1))
    return 0


def cmd_copy_db(args) -> int:
    from .server import copydb
    if not args.to:
        print("give --to or set DEXIO_DATABASE_URL", file=sys.stderr)
        return 2
    try:
        return 0 if copydb.copy(args.source, args.to, replace=args.replace) else 1
    except (RuntimeError, ValueError) as e:
        print(f"not copied: {e}", file=sys.stderr)
        return 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="dexio", description="Run and operate a Dexio server.")
    p.add_argument("--version", action="version", version=f"dexio {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve", help="run the server")
    s.add_argument("--host", default="0.0.0.0")
    s.add_argument("--port", type=int, default=8080)
    s.add_argument("--db", default=os.environ.get("DEXIO_DATABASE_URL")
                   or os.environ.get("DEXIO_DB", "dexio.db"))
    s.add_argument("--log-level", default="info")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("user", help="add a login account or reset its password")
    s.add_argument("action", choices=["add", "passwd", "list"])
    s.add_argument("email", nargs="?")
    s.add_argument("--password", help="omit to be prompted, which keeps it out of shell history")
    s.add_argument("--db", default=None)
    s.set_defaults(func=cmd_user)

    s = sub.add_parser("signups", help="count new accounts by where they came from")
    s.add_argument("--days", type=int, default=30)
    s.add_argument("--db", default=os.environ.get("DEXIO_DATABASE_URL")
                   or os.environ.get("DEXIO_DB", "dexio.db"))
    s.set_defaults(func=cmd_signups)

    s = sub.add_parser("copy-db", help="copy a SQLite database into an empty Postgres one")
    s.add_argument("source", help="the SQLite file")
    s.add_argument("--to", default=os.environ.get("DEXIO_DATABASE_URL"),
                   help="postgresql:// URL; defaults to $DEXIO_DATABASE_URL")
    s.add_argument("--replace", action="store_true",
                   help="empty destination tables that already hold rows")
    s.set_defaults(func=cmd_copy_db)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
