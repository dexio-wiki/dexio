"""Deleting an account, as the privacy policy promises: "When you delete your account
we delete its data from the live service within 30 days; copies in backups are
removed as those backups expire."

A workspace the person has to themselves goes with them: its wikis, history, files
(including the stored bytes), tokens, invites and app connections. A workspace
they share stays with the other members. They leave it, their agent tokens there
stop working, and if they were its only owner the longest-standing member becomes
owner. Pages they wrote stay, since they belong to the workspace, but no longer
point at their account.

Two cases are refused rather than guessed at, because money is involved: a paid
workspace they have to themselves (cancel the plan first), and a paid workspace
they own alone while others use it (it needs a new owner and card first).
"""
from __future__ import annotations

import logging
import time

from . import db

log = logging.getLogger("dexio.erase")

CONTACT = "support@dexio.wiki"


def _paid(ws: dict) -> bool:
    return (ws.get("plan") or "free") != "free" and bool(ws.get("stripe_subscription"))


def plan(conn, user_id: int) -> dict:
    """What deleting this account would do: workspaces that go, workspaces it
    leaves, and any reason it cannot happen yet."""
    gone, leave, blockers = [], [], []
    for w in db.workspaces_for_user(conn, user_id):
        ws = dict(conn.execute("SELECT * FROM workspaces WHERE id=?", (w["id"],)).fetchone())
        people = db.members(conn, ws["id"])
        others = [m for m in people if m["id"] != user_id]
        if not others:
            if _paid(ws):
                blockers.append(f"“{ws['name']}” is on the {ws['plan'].title()} plan. "
                                "Cancel it first, under Settings, Plan.")
            gone.append(ws)
            continue
        other_owners = [m for m in others if m.get("role") == "owner"]
        if w.get("role") == "owner" and not other_owners and _paid(ws):
            blockers.append(f"You pay for “{ws['name']}”, which other people use. "
                            f"Email {CONTACT} to hand it over first.")
        leave.append(ws)
    return {"gone": gone, "leave": leave, "blockers": blockers}


def _drop_workspace(conn, ws_id: int) -> list[str]:
    """Delete a workspace and everything in it. Returns the stored-file keys to
    remove once the transaction has committed."""
    keys = [f"ws/{ws_id}/{r['id']}" for r in
            conn.execute("SELECT id FROM files WHERE workspace_id=?", (ws_id,)).fetchall()]
    prefix = f"{ws_id}:%"
    for table in ("pages", "links", "pushes", "revisions"):
        conn.execute(f"DELETE FROM {table} WHERE project LIKE ?", (prefix,))
    conn.execute("DELETE FROM projects WHERE name LIKE ?", (prefix,))
    for table in ("files", "uploads", "tokens", "invites", "memberships", "oauth_tokens",
                  "oauth_codes", "oauth_reach", "device_logins", "wiki_renames", "shares"):
        conn.execute(f"DELETE FROM {table} WHERE workspace_id=?", (ws_id,))
    conn.execute("DELETE FROM workspaces WHERE id=?", (ws_id,))
    return keys


def _leave_workspace(conn, ws_id: int, user_id: int, role: str | None) -> None:
    conn.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (ws_id, user_id))
    conn.execute("DELETE FROM tokens WHERE workspace_id=? AND created_by=?", (ws_id, user_id))
    if role == "owner":
        owner = conn.execute("SELECT 1 FROM memberships WHERE workspace_id=? AND role='owner'",
                             (ws_id,)).fetchone()
        if not owner:
            nxt = conn.execute("SELECT user_id FROM memberships WHERE workspace_id=?"
                               " ORDER BY created_at LIMIT 1", (ws_id,)).fetchone()
            if nxt:
                conn.execute("UPDATE memberships SET role='owner' WHERE workspace_id=?"
                             " AND user_id=?", (ws_id, nxt["user_id"]))


def delete_account(conn, user_id: int, store=None, on_seats=None) -> dict:
    """Delete the account. Raises ValueError with the blockers if it cannot happen.
    `store` removes stored file bytes; `on_seats(workspace_id)` resyncs a paid
    workspace's seat count after someone leaves it."""
    p = plan(conn, user_id)
    if p["blockers"]:
        raise ValueError(" ".join(p["blockers"]))
    roles = {w["id"]: w.get("role") for w in db.workspaces_for_user(conn, user_id)}
    keys: list[str] = []
    with db.LOCK, conn:
        for ws in p["gone"]:
            keys += _drop_workspace(conn, ws["id"])
        for ws in p["leave"]:
            _leave_workspace(conn, ws["id"], user_id, roles.get(ws["id"]))
        # What stays in shared workspaces no longer points at the account.
        for table in ("revisions", "files", "uploads", "wiki_renames"):
            conn.execute(f"UPDATE {table} SET user_id=NULL WHERE user_id=?", (user_id,))
        conn.execute("UPDATE tokens SET created_by=NULL WHERE created_by=?", (user_id,))
        conn.execute("UPDATE invites SET created_by=NULL WHERE created_by=?", (user_id,))
        conn.execute("UPDATE invites SET used_by=NULL WHERE used_by=?", (user_id,))
        # What was shared with the account goes with it; what it shared stays.
        conn.execute("DELETE FROM shares WHERE user_id=?", (user_id,))
        conn.execute("UPDATE shares SET created_by=NULL WHERE created_by=?", (user_id,))
        conn.execute("UPDATE shares SET listed_by=NULL WHERE listed_by=?", (user_id,))
        for table in ("identities", "password_resets", "device_logins", "oauth_tokens",
                      "oauth_codes", "oauth_reach", "signups"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        conn.execute("DELETE FROM users WHERE id=?", (user_id,))
    removed = 0
    for key in keys:
        try:
            if store is not None:
                store.delete(key)
            removed += 1
        except Exception:  # noqa: BLE001 - the rows are gone; a stray object is logged
            log.exception("could not delete stored file %s", key)
    for ws in p["leave"]:
        if _paid(ws) and on_seats:
            try:
                on_seats(ws["id"])
            except Exception:  # noqa: BLE001
                log.exception("seat sync failed for workspace %s", ws["id"])
    return {"workspaces_deleted": len(p["gone"]), "workspaces_left": len(p["leave"]),
            "files_removed": removed, "at": time.time()}
