"""A page's history as the web app shows it: when it was created and last changed
and by whom, its revisions with what each one did, and one revision with the
lines it changed.

Every change is already kept in `revisions` (see db.py); this reads it for
people. Two things it works out that the rows do not say outright:

- Where a moved page came from. A move writes two rows in one step, at the same
  moment: one that removes the old path and one that starts the new path's
  history. The page's created date follows the move back to the old path, so
  renaming a page does not make it look new.
- A page's current life. A path deleted and later written again starts over;
  "created" is the start of the life the page is in now.

A `baseline` row is the text a page had when history began, not a change, so a
page whose history starts with one was created on or before that date.
"""
from __future__ import annotations

from .. import linediff
from . import db

GONE = "(text IS NULL AND delta IS NULL)"
_COLS = (f"id, path, at, op, author, agent, note, user_id, words, chars, version,"
         f" CASE WHEN {GONE} THEN 1 ELSE 0 END AS deleted")
# How far a created date follows a page back through moves.
MAX_MOVES = 20
MAX_LIMIT = 200


def _ops(op: str | None) -> set[str]:
    return set((op or "").split("+"))


def _row(conn, k: str, path: str, sql: str, args: tuple):
    r = conn.execute(f"SELECT {_COLS} FROM revisions WHERE project=? AND path=? {sql}",
                     (k, path, *args)).fetchone()
    return dict(r) if r else None


def _before(conn, k: str, path: str, rev_id: int):
    return _row(conn, k, path, "AND id<? ORDER BY id DESC LIMIT 1", (rev_id,))


def _first_of_life(conn, k: str, path: str, rev: dict) -> bool:
    prev = _before(conn, k, path, rev["id"])
    return prev is None or bool(prev["deleted"])


def move_source(conn, k: str, path: str, rev: dict) -> dict | None:
    """For the revision that started `path`'s current life, when it was a move:
    the row that removed the page's old path in the same step, else None."""
    if "move" not in _ops(rev["op"]) or rev["deleted"]:
        return None
    cands = [dict(r) for r in conn.execute(
        f"SELECT {_COLS} FROM revisions WHERE project=? AND at=? AND path<>? AND op LIKE ?"
        f" AND {GONE} ORDER BY id", (k, rev["at"], path, "%move%"))]
    if len(cands) > 1:
        # several moves in one step: the one whose text before it is this text
        same = [c for c in cands
                if (_before(conn, k, c["path"], c["id"]) or {}).get("version") == rev["version"]]
        cands = same or cands
    return cands[0] if cands else None


def move_target(conn, k: str, path: str, rev: dict) -> str | None:
    """For a revision that moved `path` away: the path the page went to."""
    if "move" not in _ops(rev["op"]) or not rev["deleted"]:
        return None
    cands = [dict(r) for r in conn.execute(
        f"SELECT {_COLS} FROM revisions WHERE project=? AND at=? AND path<>? AND op LIKE ?"
        f" AND NOT {GONE} ORDER BY id", (k, rev["at"], path, "%move%"))]
    # pages whose links the move rewrote change in the same step; the moved
    # page is the one whose history starts there
    cands = [c for c in cands if _first_of_life(conn, k, c["path"], c)]
    if len(cands) > 1:
        was = (_before(conn, k, path, rev["id"]) or {}).get("version")
        cands = [c for c in cands if c["version"] == was] or cands
    return cands[0]["path"] if cands else None


def _life_start(conn, k: str, path: str, upto: int | None = None) -> dict | None:
    """The first revision of the page's life at `path` as of revision `upto`
    (the latest when None): the one after its last deletion. For a page that
    is deleted as of `upto`, the life that deletion ended."""
    bound = upto if upto is not None else (1 << 62)
    latest = _row(conn, k, path, "AND id<=? ORDER BY id DESC LIMIT 1", (bound,))
    if not latest:
        return None
    end = latest["id"] - 1 if latest["deleted"] else latest["id"]
    gone = conn.execute(f"SELECT MAX(id) FROM revisions WHERE project=? AND path=? AND id<=?"
                        f" AND {GONE}", (k, path, end)).fetchone()[0]
    return _row(conn, k, path, "AND id>? AND id<=? ORDER BY id LIMIT 1", (gone or 0, end))


def _who(rev: dict | None, names: dict[int, str]) -> dict:
    if not rev:
        return {"agent": "", "person": "", "author": ""}
    return {"agent": rev.get("agent") or "", "person": names.get(rev.get("user_id"), ""),
            "author": rev.get("author") or ""}


def page_info(conn, k: str, path: str, page: dict | None = None) -> dict:
    """When the page at `path` was created and last changed, by whom, how many
    revisions it has and who wrote them. Times are Unix seconds. `page` is the
    page's row from db.note when the caller has it already."""
    page = page if page is not None else db.note(conn, k, path)
    count = conn.execute("SELECT COUNT(*) FROM revisions WHERE project=? AND path=?",
                         (k, path)).fetchone()[0]
    latest = _row(conn, k, path, "ORDER BY id DESC LIMIT 1", ())
    start = _life_start(conn, k, path)
    moved_from = None
    cur_path, cur = path, start
    for _ in range(MAX_MOVES):
        src = move_source(conn, k, cur_path, cur) if cur else None
        if not src:
            break
        if moved_from is None:
            moved_from = {"path": src["path"], "at": src["at"]}
        cur_path, cur = src["path"], _life_start(conn, k, src["path"], src["id"] - 1)
    ids = [r["user_id"] for r in (latest, cur) if r]
    names = db.people(conn, ids)
    created = None
    if cur:
        created = {"at": cur["at"], "exact": cur["op"] != "baseline", "path": cur_path,
                   **_who(cur if cur["op"] != "baseline" else None, names)}
    contributors = [
        {"name": r["who"], "changes": r["n"]} for r in conn.execute(
            "SELECT COALESCE(NULLIF(agent, ''), author, '') AS who, COUNT(*) AS n FROM revisions"
            " WHERE project=? AND path=? AND op<>'baseline'"
            " GROUP BY COALESCE(NULLIF(agent, ''), author, '') ORDER BY n DESC, who",
            (k, path)) if r["who"]]
    updated_at = page["updated_at"] if page else (latest["at"] if latest else None)
    edited_by = latest if latest and latest["op"] != "baseline" else None
    return {
        "exists": page is not None,
        "created": created,
        "moved_from": moved_from,
        "updated_at": updated_at or None,
        "updated_by": _who(edited_by, names) if edited_by else None,
        "revisions": count,
        "contributors": contributors,
        "chars": len(page["text"]) if page else None,
    }


def _entry(conn, k: str, path: str, r: dict, prev: dict | None, names: dict) -> dict:
    first = prev is None or bool(prev["deleted"])
    before = None if first else (prev["words"] or 0)
    after = None if r["deleted"] else (r["words"] or 0)
    out = {"id": r["id"], "at": r["at"], "op": r["op"], "note": r["note"] or "",
           "deleted": bool(r["deleted"]), "first": first and not r["deleted"],
           "words": after, "words_before": before, **_who(r, names)}
    if out["first"]:
        src = move_source(conn, k, path, r)
        if src:
            out["moved_from"] = src["path"]
    elif r["deleted"]:
        dst = move_target(conn, k, path, r)
        if dst:
            out["moved_to"] = dst
    return out


def page_revisions(conn, k: str, path: str, limit: int = 50,
                   before: int | None = None) -> dict:
    """The page's revisions newest first, `limit` at a time, older than revision
    `before` when given. Each says what it did (op, first, deleted, moved_from,
    moved_to), who made it, the writer's note and the word count before and
    after. `next_before` continues the list; None at its end."""
    limit = max(1, min(int(limit), MAX_LIMIT))
    sql = f"SELECT {_COLS} FROM revisions WHERE project=? AND path=?"
    args: list = [k, path]
    if before is not None:
        sql += " AND id<?"
        args.append(int(before))
    rows = [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit + 1))]
    more = len(rows) > limit
    names = db.people(conn, [r["user_id"] for r in rows])
    out = [_entry(conn, k, path, r, rows[i + 1] if i + 1 < len(rows) else None, names)
           for i, r in enumerate(rows[:limit])]
    return {"path": path, "revisions": out, "next_before": out[-1]["id"] if more else None}


def revision_view(conn, k: str, path: str, rev_id: int) -> dict | None:
    """One revision of `path`: who made it and when, the page's text after it,
    the revisions either side of it, and the lines it changed from the one
    before. A revision that started the page has no diff (the text is all of
    it); one that removed the page carries the text it removed instead."""
    rev = _row(conn, k, path, "AND id=?", (int(rev_id),))
    if not rev:
        return None
    prev = _before(conn, k, path, rev["id"])
    nxt = _row(conn, k, path, "AND id>? ORDER BY id LIMIT 1", (rev["id"],))
    names = db.people(conn, [rev["user_id"]])
    entry = _entry(conn, k, path, rev, prev, names)
    text = None if rev["deleted"] else db.revision_text(conn, k, path, rev["id"])
    old = None if prev is None or prev["deleted"] else db.revision_text(conn, k, path, prev["id"])
    out = {**entry, "path": path, "text": text,
           "older": prev["id"] if prev else None, "newer": nxt["id"] if nxt else None,
           "latest": nxt is None, "diff": None}
    if text is not None and old is not None:
        out["diff"] = linediff.diff(old, text)
    elif text is None and old is not None:
        out["removed_text"] = old
    return out
