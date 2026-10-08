"""What a person did while connecting their first agent, by account (Forrest,
2026-10-08: "are we missing telemetry during the setup?", then "go").

Until this, an account that signed up and never connected left no trace past
naming its workspace: which agent they picked, whether they copied the steps,
when they left the tab, whether they saw the sign-in screen for Claude or
ChatGPT. Each of those is now one row here, written by the server where the
browser already calls it (an agent picked, a key made, the consent and device
screens) and by a small beacon from the setup screens for what only the browser
sees (the screen shown, a step, a copy, the tab hidden, shown or left).

First-party and in our own database only: nothing goes to Google or anyone
else, and no page text, key or address is stored. Rows go with the account and
are dropped after RETAIN_DAYS. `trail` is the per-account timeline the operator
reads (the day-after line on the signup alert).
"""
from __future__ import annotations

import json
import re
import time

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_events (
  id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, workspace_id INTEGER,
  at REAL NOT NULL, event TEXT NOT NULL, detail TEXT
);
CREATE INDEX IF NOT EXISTS setup_events_user ON setup_events(user_id, at);
"""
RETAIN_DAYS = 180

# What the setup screens may report (POST /api/v1/setup/event). Everything else
# is recorded by the server itself.
BROWSER = frozenset({
    "connect_shown",   # Connect your agent on screen: detail graph|agents (+ device)
    "ready_shown",     # an empty wiki whose workspace already has an agent
    "step",            # the stepper moved to step 1, 2 or 3
    "copied",          # a copy button: detail is the copied element's id
    "hidden",          # the tab went to the background with a setup screen open
    "visible",         # and came back
    "left",            # the page was closed or navigated away from
})
SERVER = frozenset({
    "named", "agent_picked", "key_minted", "consent_shown", "consent_approved",
    "consent_denied", "device_shown", "device_approved", "device_denied",
})


def init(conn) -> None:
    conn.executescript(SCHEMA)
    with db.LOCK, conn:
        conn.execute("DELETE FROM setup_events WHERE at < ?",
                     (time.time() - RETAIN_DAYS * 86400,))


def clean(value, limit: int = 60) -> str:
    """A short label: letters, digits, space and . _ : -, nothing else."""
    return re.sub(r"[^A-Za-z0-9 ._:-]", "", str(value or "")).strip()[:limit]


def device_of(user_agent: str | None) -> str:
    """"desktop Chrome", "mobile Safari" and so on, from the User-Agent. Coarse on
    purpose: whether someone was on a phone is the question, not who they are."""
    ua = user_agent or ""
    if re.search(r"iPhone|iPod|Android.+Mobile|Mobi", ua):
        kind = "mobile"
    elif re.search(r"iPad|Android|Tablet", ua):
        kind = "tablet"
    else:
        kind = "desktop"
    for name, pat in (("Edge", r"Edg/|EdgiOS/|EdgA/"), ("Opera", r"OPR/"),
                      ("Firefox", r"Firefox/|FxiOS/"), ("Chrome", r"Chrome/|CriOS/"),
                      ("Safari", r"Safari/")):
        if re.search(pat, ua):
            return f"{kind} {name}"
    return f"{kind} other"


def record(conn, user_id: int, event: str, detail: str = "",
           workspace_id: int | None = None) -> None:
    if event not in BROWSER and event not in SERVER:
        raise ValueError(f"unknown setup event {event!r}")
    with db.LOCK, conn:
        conn.execute("INSERT INTO setup_events (user_id, workspace_id, at, event, detail)"
                     " VALUES (?,?,?,?,?)",
                     (user_id, workspace_id, time.time(), event, clean(detail) or None))


def trail(conn, user_id: int) -> dict:
    """One account's setup, in order, and whether an agent ever got through: keys
    it made and their last use, app sign-ins by client, approved agent sign-ins,
    and pages written in its workspaces."""
    events = [dict(r) for r in conn.execute(
        "SELECT at, event, detail, workspace_id FROM setup_events WHERE user_id=?"
        " ORDER BY at, id", (user_id,)).fetchall()]
    keys = [dict(r) for r in conn.execute(
        "SELECT name, created_at, last_used FROM tokens WHERE created_by=? ORDER BY created_at",
        (user_id,)).fetchall()]
    apps = []
    for r in conn.execute(
            "SELECT t.client_id, c.info, MIN(t.created_at) AS since, MAX(t.last_used) AS last_used"
            " FROM oauth_tokens t JOIN oauth_clients c USING (client_id) WHERE t.user_id=?"
            " GROUP BY t.client_id, c.info ORDER BY since", (user_id,)).fetchall():
        try:
            name = json.loads(r["info"]).get("client_name") or "OAuth app"
        except (TypeError, ValueError):
            name = "OAuth app"
        apps.append({"name": name, "since": r["since"], "last_used": r["last_used"]})
    devices = [dict(r) for r in conn.execute(
        "SELECT client_name, status, created_at FROM device_logins WHERE user_id=?"
        " ORDER BY created_at", (user_id,)).fetchall()]
    # Changes made as this person (revisions.user_id: the account behind the key or
    # sign-in), and the pages now in the workspaces they own.
    w = conn.execute("SELECT COUNT(*) AS n, MIN(at) AS first_at FROM revisions"
                     " WHERE user_id=? AND op <> 'baseline'", (user_id,)).fetchone()
    pages = 0
    for r in conn.execute("SELECT workspace_id FROM memberships WHERE user_id=? AND role='owner'",
                          (user_id,)).fetchall():
        pages += conn.execute("SELECT COUNT(*) AS n FROM pages WHERE project LIKE ?",
                              (f"{r['workspace_id']}:%",)).fetchone()["n"]
    connected = (any(k["last_used"] for k in keys) or any(a["last_used"] for a in apps)
                 or bool(w["n"]))
    return {"events": events, "keys": keys, "apps": apps, "devices": devices,
            "writes": w["n"], "first_write": w["first_at"], "pages": pages,
            "connected": bool(connected)}
