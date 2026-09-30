"""Agent sign-in: the device authorization grant (RFC 8628), the way a CLI logs in.

Someone tells their agent "look at dexio.wiki and log me in". The agent starts a
request here, shows the person a link and a short code, and polls. The person opens
the link, signs in or signs up, picks a workspace and allows it. The agent's next
poll gets a workspace token, the same kind the connect flow mints, named for the agent and
revocable in Settings. The agent then adds https://app.dexio.wiki/mcp to its own MCP
config with that token as a bearer header.

This works for any agent that can make two HTTP requests, including one running on
another machine or behind a chat app: the browser never has to reach the agent,
which is what an OAuth redirect to localhost needs.

Device codes are stored hashed and die after 30 minutes. The token is minted at the
poll that collects it, so its plaintext is never at rest.
"""
from __future__ import annotations

import hashlib
import re
import secrets
import sqlite3
import time

from . import db

EXPIRES_IN = 1800
INTERVAL = 5
# RFC 8628 section 6.1: consonants only, so a code cannot spell a word, and none
# that are easy to confuse when read aloud or typed from a phone.
ALPHABET = "BCDFGHJKLMNPQRSTVWXZ"
CODE_LEN = 8

SCHEMA = """
CREATE TABLE IF NOT EXISTS device_logins (
  device_hash  TEXT PRIMARY KEY,
  user_code    TEXT NOT NULL UNIQUE,
  client_name  TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  user_id      INTEGER,
  workspace_id INTEGER,
  created_at   REAL NOT NULL,
  expires_at   REAL NOT NULL,
  last_poll    REAL
);
"""


def _h(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def init(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def normalize(code: str) -> str:
    """What a person typed, as stored: upper case, separators dropped."""
    return re.sub(r"[^A-Z]", "", (code or "").upper())


def pretty(code: str) -> str:
    code = normalize(code)
    return f"{code[:4]}-{code[4:]}" if len(code) == CODE_LEN else code


def clean_name(name) -> str:
    """The agent's own description of itself, shown on the approval page and used
    as the token's name. Printable, one line, 60 characters."""
    name = re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", str(name or ""))).strip()
    return name[:60].strip() or "An agent"


def start(conn, client_name: str) -> dict:
    now = time.time()
    device_code = "dxd_" + secrets.token_urlsafe(32)
    name = clean_name(client_name)
    with db.LOCK, conn:
        conn.execute("DELETE FROM device_logins WHERE expires_at<?", (now - 86400,))
        for _ in range(10):
            user_code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
            try:
                conn.execute(
                    "INSERT INTO device_logins (device_hash, user_code, client_name,"
                    " created_at, expires_at) VALUES (?,?,?,?,?)",
                    (_h(device_code), user_code, name, now, now + EXPIRES_IN))
                break
            except sqlite3.IntegrityError:  # a user_code collision; draw again
                continue
        else:
            raise RuntimeError("could not allocate a user code")
    return {"device_code": device_code, "user_code": pretty(user_code),
            "client_name": name, "expires_in": EXPIRES_IN, "interval": INTERVAL}


def pending(conn, user_code: str) -> dict | None:
    """The live request a person is looking at, or None when the code is wrong,
    expired or already answered."""
    code = normalize(user_code)
    if len(code) != CODE_LEN:
        return None
    row = conn.execute("SELECT * FROM device_logins WHERE user_code=?", (code,)).fetchone()
    if not row or row["status"] != "pending" or row["expires_at"] < time.time():
        return None
    return dict(row)


def answer(conn, user_code: str, allow: bool, user_id: int | None = None,
           workspace_id: int | None = None) -> bool:
    """Record the person's decision. False when the request is no longer open."""
    code = normalize(user_code)
    with db.LOCK, conn:
        cur = conn.execute(
            "UPDATE device_logins SET status=?, user_id=?, workspace_id=?"
            " WHERE user_code=? AND status='pending' AND expires_at>=?",
            ("approved" if allow else "denied", user_id if allow else None,
             workspace_id if allow else None, code, time.time()))
    return cur.rowcount == 1


def poll(conn, device_code: str) -> tuple[str, dict | None]:
    """One poll from the agent. Returns (state, result): state is an RFC 8628
    error code (authorization_pending, slow_down, access_denied, expired_token,
    invalid_grant) or "ok", in which case result holds the new token."""
    now = time.time()
    with db.LOCK, conn:
        row = conn.execute("SELECT * FROM device_logins WHERE device_hash=?",
                           (_h(device_code),)).fetchone()
        if not row or row["status"] == "done":
            return "invalid_grant", None
        if row["expires_at"] < now:
            return "expired_token", None
        if row["status"] == "denied":
            return "access_denied", None
        last = row["last_poll"]
        conn.execute("UPDATE device_logins SET last_poll=? WHERE device_hash=?",
                     (now, row["device_hash"]))
        if row["status"] == "pending":
            # Polling a little faster than the interval is forgiven; hammering is not.
            if last and now - last < INTERVAL / 2:
                return "slow_down", None
            return "authorization_pending", None
        # Approved. Collect exactly once, and only while the person still belongs
        # to the workspace they picked.
        conn.execute("UPDATE device_logins SET status='done' WHERE device_hash=?",
                     (row["device_hash"],))
    if not db.role_in(conn, row["workspace_id"], row["user_id"]):
        return "access_denied", None
    token = db.create_token(conn, row["client_name"], workspace_id=row["workspace_id"],
                            created_by=row["user_id"])
    return "ok", {"token": token, "user_id": row["user_id"],
                  "workspace_id": row["workspace_id"], "client_name": row["client_name"]}
