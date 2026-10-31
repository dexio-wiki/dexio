"""Deleting a page for good (Forrest, 2026-10-31: "we need to allow permanent page
deletion through dexio mcp").

A plain delete keeps the page's history, so it can be restored. A permanent one
removes that history too: every revision at the page's path, and, when the page
got there by a move, the revisions under each path it was moved from up to the
move (a moved page leaves its earlier history under its old path; whatever was
written at that old path afterwards is another page and stays). Page shares on
the path go too, so a page written there later is not shared by accident.

One row is left: op `purge`, with no text, saying who deleted the page for good,
when, and their note, so the people in the workspace can see it happened. The
storage the history held is freed (files.usage counts revisions). Copies in
database backups go as those backups expire, as the privacy policy says.
"""
from __future__ import annotations

import time

from . import db, history

# How many paths a page's history is followed back through by moves.
MAX_PATHS = 50
PURGE = "purge"


def lineage(conn, k: str, path: str) -> dict[str, int | None]:
    """The paths holding the history of the page at `path`, each with the last
    revision id of it to delete (None: all of them)."""
    spans: dict[str, int | None] = {}
    todo: list[tuple[str, int | None]] = [(path, None)]
    while todo and len(spans) < MAX_PATHS:
        p, upto = todo.pop()
        if p in spans:
            had = spans[p]
            if had is None or (upto is not None and upto <= had):
                continue
        spans[p] = upto
        rows = [dict(r) for r in conn.execute(
            f"SELECT {history._COLS} FROM revisions WHERE project=? AND path=? AND id<=?"
            " ORDER BY id", (k, p, upto if upto is not None else (1 << 62)))]
        prev = None
        for r in rows:
            # each life at this path that began with a move came from another path
            if not r["deleted"] and (prev is None or prev["deleted"]):
                src = history.move_source(conn, k, p, r)
                if src:
                    todo.append((src["path"], src["id"]))
            prev = r
    return spans


def only_purged(conn, k: str, path: str) -> bool | None:
    """None when `path` has no history at all; True when all it has is purge rows
    (deleted for good already); else False."""
    ops = {r[0] for r in conn.execute("SELECT DISTINCT op FROM revisions WHERE project=?"
                                      " AND path=?", (k, path))}
    if not ops:
        return None
    return ops == {PURGE}


def purge_page(conn, k: str, path: str, *, author: str, note: str | None, agent: str | None,
               user_id: int | None) -> dict:
    """Delete the history of the page at `path`, which must not exist now (delete it
    first), and record one purge row. Returns how many revisions went and from
    which paths. All or nothing; takes db.LOCK."""
    with db.LOCK:
        if db.note(conn, k, path) is not None:
            raise RuntimeError(f"{path!r} still exists; delete it before purging its history")
        spans = lineage(conn, k, path)
        ws_id, _ = db.split_key(k)
        removed = 0
        with conn:
            for p, upto in spans.items():
                if upto is None:
                    cur = conn.execute("DELETE FROM revisions WHERE project=? AND path=?", (k, p))
                else:
                    cur = conn.execute("DELETE FROM revisions WHERE project=? AND path=? AND id<=?",
                                       (k, p, upto))
                removed += max(cur.rowcount, 0)
            conn.execute("DELETE FROM shares WHERE workspace_id=? AND kind='page' AND path=?",
                         (ws_id, path))
            db._insert_revisions(conn, k, [(path, time.time(), PURGE, author, None, None,
                                            (note or "").strip() or None,
                                            (agent or "").strip() or None, user_id)])
    return {"revisions_deleted": removed,
            "history_paths": sorted(spans, key=lambda p: (p != path, p))}
