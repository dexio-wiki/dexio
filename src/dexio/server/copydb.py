"""Copy a SQLite database into an empty Postgres one, then prove the copy.

    dexio copy-db /data/dexio.db --to postgresql://...

The destination gets the server's own schema first (the same init the server runs),
then every table's rows in one transaction, identity sequences moved past the
copied ids, and a row-by-row comparison of every table. Emails are lower-cased on
the way, which is how Postgres stores them (see auth.email_key). The source is
opened read-only.
"""
from __future__ import annotations

import sqlite3


def _norm(v):
    if v is None:
        return (0, "")
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return (1, float(v))
    return (2, str(v))


def _rows(conn, table: str, cols: list[str]) -> list[tuple]:
    got = conn.execute(f"SELECT {', '.join(cols)} FROM {table}").fetchall()
    return sorted(tuple(_norm(v) for v in r) for r in got)


def copy(src_path: str, dst_url: str, replace: bool = False, log=print) -> bool:
    from . import auth, db, device, oauth, pg

    if not pg.is_url(dst_url):
        raise ValueError("the destination must be a postgresql:// URL")
    src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = db.connect(dst_url)
    auth.init(dst)
    oauth.init(dst)
    device.init(dst)

    # Links are derived from the pages and recomputed on arrival, under whichever
    # layout the source used (old edges/dangling tables, or links).
    derived = {"edges", "dangling", "links"}
    tables = [r[0] for r in src.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        " ORDER BY name") if r[0] not in derived]
    have = {r[0] for r in dst.execute(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = current_schema()")}
    missing = [t for t in tables if t not in have]
    if missing:
        raise RuntimeError(f"the server's schema has no table for {missing}; not copying")
    busy = [t for t in tables if dst.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]]
    if busy and not replace:
        raise RuntimeError(f"destination tables already hold rows: {busy} (use --replace)")

    plan = {}
    for t in tables:
        s_cols = [r["name"] for r in src.execute(f"PRAGMA table_info({t})")]
        d_cols = {r["name"] for r in dst.execute(f"PRAGMA table_info({t})")}
        dropped = [c for c in s_cols if c not in d_cols]
        if dropped:
            raise RuntimeError(f"{t}: the server's schema lacks columns {dropped}; not copying")
        plan[t] = s_cols

    with dst:
        for t in busy:
            dst.execute(f"DELETE FROM {t}")
        for t, cols in plan.items():
            rows = [tuple(r) for r in src.execute(f"SELECT {', '.join(cols)} FROM {t}")]
            if t == "users" and "email" in cols:
                i = cols.index("email")
                rows = [r[:i] + (auth.email_key(r[i]),) + r[i + 1:] for r in rows]
            if rows:
                dst.executemany(f"INSERT INTO {t} ({', '.join(cols)}) VALUES"
                                f" ({', '.join('?' * len(cols))})", rows)
            if "id" in cols:
                dst.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'),"
                            f" (SELECT MAX(id) FROM {t}))")
            log(f"{t:18s} {len(rows):6d} rows")
    for (name,) in dst.execute("SELECT name FROM projects").fetchall():
        db.relink(dst, name)
    log(f"{'links':18s} recomputed")

    ok = True
    for t, cols in plan.items():
        want = _rows(src, t, cols)
        if t == "users" and "email" in cols:
            i = cols.index("email")
            want = sorted(r[:i] + ((2, auth.email_key(r[i][1])),) + r[i + 1:] for r in want)
        got = _rows(dst, t, cols)
        if want != got:
            ok = False
            log(f"MISMATCH {t}: {len(want)} rows in SQLite, {len(got)} in Postgres")
    log("verified: every row matches" if ok else "VERIFY FAILED")
    src.close()
    dst.close()
    return ok
