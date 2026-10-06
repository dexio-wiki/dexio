"""Who is in a workspace, and how they stop being in it.

Owners change roles and remove people; anyone can leave; an owner can delete the
workspace. The rules, each refused with a reason a person can act on:

- Nobody changes their own role. An owner stays an owner until another owner
  makes them a member, so a workspace always has an owner; the last one also
  cannot leave while other people remain (they make someone else an owner first).
- Its only member cannot leave; they delete the workspace instead.
- Nobody removes themselves through Remove; that is Leave.
- A workspace with a live paid subscription cannot be deleted until the plan is
  cancelled, so nobody keeps paying for something that is gone.

Leaving or being removed takes away everything that let the person, or an AI
acting for them, into that workspace: the membership, the agent keys they made
there, their Claude or ChatGPT sign-ins to it, pending agent sign-ins, and
invites they created that nobody has used. Pages they wrote stay; they belong to
the workspace.
"""
from __future__ import annotations

import logging

from . import db, erase

log = logging.getLogger("dexio.membership")

ROLES = ("owner", "member")


def _owners(conn, ws_id: int) -> int:
    return conn.execute("SELECT COUNT(*) FROM memberships WHERE workspace_id=? AND role='owner'",
                        (ws_id,)).fetchone()[0]


def _revoke_access(conn, ws_id: int, user_id: int) -> None:
    conn.execute("DELETE FROM memberships WHERE workspace_id=? AND user_id=?", (ws_id, user_id))
    conn.execute("DELETE FROM tokens WHERE workspace_id=? AND created_by=?", (ws_id, user_id))
    for table in ("oauth_tokens", "oauth_codes", "device_logins"):
        conn.execute(f"DELETE FROM {table} WHERE workspace_id=? AND user_id=?", (ws_id, user_id))
    conn.execute("DELETE FROM invites WHERE workspace_id=? AND created_by=? AND used_at IS NULL",
                 (ws_id, user_id))


def set_role(conn, ws_id: int, user_id: int, role: str, by: int) -> None:
    """An owner (`by`) makes someone else an owner or a member."""
    if role not in ROLES:
        raise ValueError("Unknown role.")
    if user_id == by:
        raise ValueError("You can't change your own role. Another owner can.")
    with db.LOCK, conn:
        now = db.role_in(conn, ws_id, user_id)
        if not now:
            raise ValueError("They are not a member of this workspace.")
        if now == "owner" and role == "member" and _owners(conn, ws_id) <= 1:
            raise ValueError("A workspace needs an owner. Make someone else an owner first.")
        conn.execute("UPDATE memberships SET role=? WHERE workspace_id=? AND user_id=?",
                     (role, ws_id, user_id))


def remove_member(conn, ws_id: int, user_id: int, by: int) -> None:
    """An owner (`by`) removes someone else from the workspace."""
    if user_id == by:
        raise ValueError("To take yourself out, use Leave this workspace under General.")
    with db.LOCK, conn:
        if not db.role_in(conn, ws_id, user_id):
            raise ValueError("They are not a member of this workspace.")
        _revoke_access(conn, ws_id, user_id)


def leave_blocker(conn, ws_id: int, user_id: int) -> str:
    """Why this person cannot leave the workspace right now, or "" if they can."""
    people = db.members(conn, ws_id)
    me = next((m for m in people if m["id"] == user_id), None)
    if not me:
        return "You are not a member of this workspace."
    others = [m for m in people if m["id"] != user_id]
    if not others:
        return "You are its only member. To remove it, delete the workspace instead."
    if me["role"] == "owner" and not any(m["role"] == "owner" for m in others):
        return "You are its only owner. Make someone else an owner under Members first."
    return ""


def leave(conn, ws_id: int, user_id: int) -> None:
    with db.LOCK, conn:
        why = leave_blocker(conn, ws_id, user_id)
        if why:
            raise ValueError(why)
        _revoke_access(conn, ws_id, user_id)


def delete_blocker(ws: dict) -> str:
    """Why this workspace cannot be deleted right now, or "" if it can. `ws` is
    the full workspaces row (db.workspace)."""
    if erase._paid(ws):
        return (f"It is on the {ws['plan'].title()} plan. Cancel the plan first, under Plan,"
                " then delete it.")
    return ""


def delete_workspace(conn, ws_id: int, confirm_name: str, store=None) -> None:
    """Delete the workspace and everything in it, once its name is typed back."""
    ws = db.workspace(conn, ws_id)
    if not ws:
        raise ValueError("That workspace no longer exists.")
    why = delete_blocker(ws)
    if why:
        raise ValueError(why)
    if (confirm_name or "").strip() != ws["name"]:
        raise ValueError("Type the workspace's name exactly as shown to delete it.")
    with db.LOCK, conn:
        keys = erase._drop_workspace(conn, ws_id)
    for key in keys:
        try:
            if store is not None:
                store.delete(key)
        except Exception:  # noqa: BLE001 - the rows are gone; a stray object is logged
            log.exception("could not delete stored file %s", key)
