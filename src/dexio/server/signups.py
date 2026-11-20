"""Where each account came from, recorded once when it is created.

dexio.wiki passes the visit's first outside referrer (host only) and any utm_ tags
on its links to app.dexio.wiki, as ref, utm_source, utm_medium, utm_campaign and
landing. The app's sign-in and sign-up pages keep the first of those they see, or
an outside Referer host, in a first-party cookie for 30 days, and the account
creation writes it here with how the person signed up (password, google, github)
and what brought them (the site, an agent's sign-in link, Claude or ChatGPT, an
invite). No third-party analytics; the privacy policy discloses this.

The first graph screen also asks an owner how they heard about Dexio, beside
Name your workspace (Forrest, 2026-11-19: "Do we ask how they found us during
onboarding? If not, we should"), because most accounts carry no referrer or tag:
27 of 39 outside accounts that day. The answer is one of HEARD, plus a few words
for Other, and the first answer is kept (note_heard).
"""
from __future__ import annotations

import base64
import json
import re
import time
from urllib.parse import urlsplit

from . import db

SCHEMA = """
CREATE TABLE IF NOT EXISTS signups (
  user_id INTEGER PRIMARY KEY, at REAL NOT NULL, method TEXT NOT NULL, via TEXT NOT NULL,
  referrer TEXT, utm_source TEXT, utm_medium TEXT, utm_campaign TEXT, landing TEXT,
  heard TEXT, heard_other TEXT
);
"""
COOKIE = "dexio_src"
MAX_AGE = 30 * 86400
FIELDS = ("ref", "utm_source", "utm_medium", "utm_campaign", "landing")
_OWN = re.compile(r"(^|\.)dexio\.wiki$|^localhost$|^127\.0\.0\.1$|^testserver$")
# How did you hear about Dexio? The keys are what is stored; the screen's labels are
# render.py's HEARD, in the same order.
HEARD = {"search": "Google or another search engine", "ai": "ChatGPT, Claude or another AI",
         "github": "GitHub", "list": "A list or directory", "reddit": "Reddit",
         "social": "X or LinkedIn", "friend": "A friend or colleague", "other": "Other"}


def init(conn) -> None:
    conn.executescript(SCHEMA)
    have = {r["name"] for r in conn.execute("PRAGMA table_info(signups)")}
    for column in ("heard", "heard_other"):
        if column not in have:
            conn.execute(f"ALTER TABLE signups ADD COLUMN {column} TEXT")
    conn.commit()


def _clean(value, limit: int = 100) -> str:
    return re.sub(r"[^\x20-\x7e]", "", str(value or "")).strip()[:limit]


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def touch(query: dict, referer: str | None) -> dict | None:
    """The attribution a request carries: tags passed on by the site, else an outside
    Referer's host. None when there is nothing to keep."""
    src = {k: _clean(query.get(k)) for k in FIELDS if _clean(query.get(k))}
    if "ref" in src:
        ref = src["ref"]
        src["ref"] = _host(ref if "://" in ref else "//" + ref) or ref
    if not src.get("ref") and referer:
        host = _host(referer)
        if host and not _OWN.search(host):
            src["ref"] = host
    return src or None


def encode(src: dict) -> str:
    return base64.urlsafe_b64encode(json.dumps(src, separators=(",", ":")).encode()).decode()


def decode(value: str | None) -> dict:
    try:
        data = json.loads(base64.urlsafe_b64decode((value or "").encode()))
    except (ValueError, TypeError):
        return {}
    return {k: _clean(data.get(k)) for k in FIELDS if isinstance(data, dict) and data.get(k)}


def via_of(next_url: str) -> str:
    n = next_url or ""
    if n.startswith("/device"):
        return "agent sign-in"
    if n.startswith("/oauth"):
        return "claude or chatgpt"
    if n.startswith("/invite/"):
        return "invite"
    return "site"


def record(conn, user_id: int, method: str, next_url: str, cookie: str | None) -> None:
    src = decode(cookie)
    with db.LOCK, conn:
        conn.execute(
            "INSERT OR IGNORE INTO signups (user_id, at, method, via, referrer, utm_source,"
            " utm_medium, utm_campaign, landing) VALUES (?,?,?,?,?,?,?,?,?)",
            (user_id, time.time(), method, via_of(next_url), src.get("ref"),
             src.get("utm_source"), src.get("utm_medium"), src.get("utm_campaign"),
             src.get("landing")))


def report(conn, days: int = 30) -> dict:
    """Counts by source over the last `days` days, for the operator command."""
    since = time.time() - days * 86400
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM signups WHERE at >= ? ORDER BY at DESC", (since,)).fetchall()]

    def count(key):
        out: dict[str, int] = {}
        for r in rows:
            k = r.get(key) or "(none)"
            out[k] = out.get(k, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    return {"days": days, "signups": len(rows), "by_referrer": count("referrer"),
            "by_utm_source": count("utm_source"), "by_utm_campaign": count("utm_campaign"),
            "by_via": count("via"), "by_heard": count("heard"),
            "by_method": count("method")}


def check_heard(heard, other) -> tuple[str, str]:
    """The answer to How did you hear about Dexio?, cleaned: (key, words for Other).
    ("", "") when there is none. ValueError for a key not in HEARD, or Other with no
    words."""
    heard = str(heard or "").strip()
    if not heard:
        return "", ""
    if heard not in HEARD:
        raise ValueError("pick one of the choices")
    # Other's words may be in any language, so only control characters go
    words = re.sub(r"[\x00-\x1f\x7f]", "", str(other or "")).strip()[:100] if heard == "other" else ""
    if heard == "other" and not words:
        raise ValueError("say where you heard about Dexio")
    return heard, words


def needs_heard(conn, user_id: int) -> bool:
    """The account was recorded here and has not said how it heard about Dexio."""
    row = conn.execute("SELECT heard FROM signups WHERE user_id=?", (user_id,)).fetchone()
    return row is not None and not row["heard"]


def note_heard(conn, user_id: int, heard: str, other: str = "") -> bool:
    """Keep the account's answer, if it has none yet. False when nothing was written."""
    if not heard:
        return False
    with db.LOCK, conn:
        cur = conn.execute("UPDATE signups SET heard=?, heard_other=? WHERE user_id=?"
                           " AND heard IS NULL", (heard, other or None, user_id))
    return bool(cur.rowcount)
