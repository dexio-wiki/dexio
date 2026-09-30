"""Users, password hashing and signed session cookies.

Standard library only, in keeping with the rest of the package. Passwords are
hashed with scrypt, which is in hashlib, so there is no bcrypt or argon2
dependency to carry. Sessions are HMAC-signed cookies rather than server-side
state, so a restart does not log everybody out and there is no session table to
prune.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time

from .db import LOCK

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id            INTEGER PRIMARY KEY,
  email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  created_at    REAL NOT NULL,
  last_login    REAL
);
CREATE TABLE IF NOT EXISTS password_resets (
  token_hash TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at REAL NOT NULL,
  expires_at REAL NOT NULL, used_at REAL
);
CREATE TABLE IF NOT EXISTS settings (
  key   TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS identities (
  provider   TEXT NOT NULL,
  subject    TEXT NOT NULL,
  user_id    INTEGER NOT NULL,
  email      TEXT NOT NULL,
  created_at REAL NOT NULL,
  last_login REAL,
  PRIMARY KEY (provider, subject)
);
"""

# scrypt parameters. n=2**15 keeps a single hash around 100ms on a t4g.small,
# which is the point: slow enough to make guessing expensive, fast enough that a
# login does not feel broken.
SCRYPT_N = 2 ** 15
SCRYPT_R = 8
SCRYPT_P = 1
# 128 * N * r is about 33.5MB here, which is over OpenSSL's 32MB default and
# raises "memory limit exceeded" unless maxmem is raised to match.
SCRYPT_MAXMEM = 128 * SCRYPT_N * SCRYPT_R * 2
SESSION_COOKIE = "dexio_session"
SESSION_MAX_AGE = 14 * 24 * 3600


# ---- passwords ---------------------------------------------------------
def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=SCRYPT_R,
                        p=SCRYPT_P, dklen=32, maxmem=SCRYPT_MAXMEM)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt_hex, want_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex),
                            n=int(n), r=int(r), p=int(p), dklen=len(want_hex) // 2,
                            maxmem=128 * int(n) * int(r) * 2)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(dk.hex(), want_hex)


# ---- users -------------------------------------------------------------
def init(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    # Added after the first release; an existing database upgrades in place.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "password_changed_at" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN password_changed_at REAL")
    # Names (2026-09-26): from Google or GitHub at sign-in, optional at email sign-up,
    # editable on the account page. Empty string when unknown.
    for col in ("first_name", "last_name"):
        if col not in cols:
            conn.execute(f"ALTER TABLE users ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")
    # Appearance, "<theme>.<mode>" (see dexio/themes.py); NULL until chosen.
    if "theme" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN theme TEXT")
    # Left by the WorkOS sign-in (engine 8ad400b), which never went live: always
    # NULL. Sign-in identities live in their own table now.
    if "workos_id" in cols:
        conn.execute("DROP INDEX IF EXISTS users_workos_id")
        conn.execute("ALTER TABLE users DROP COLUMN workos_id")
    conn.commit()


def email_key(email: str | None) -> str:
    """How an email is stored and compared: trimmed and lower-cased. SQLite's
    COLLATE NOCASE did this in the schema; Postgres has no equivalent built in, so
    every read and write of users.email goes through here instead."""
    return (email or "").strip().lower()


def clean_name(value) -> str:
    """One name part as stored: printable, one line, at most 80 characters."""
    text = "".join(ch if ch.isprintable() else " " for ch in str(value or ""))
    return " ".join(text.split())[:80].strip()


def split_name(full: str) -> tuple[str, str]:
    """(first, last) from a single full name, as GitHub gives it: split on the first
    space. A best guess; people can correct it on the account page."""
    parts = clean_name(full).split(" ", 1)
    return parts[0], (parts[1] if len(parts) > 1 else "")


def has_password(conn: sqlite3.Connection, user_id: int) -> bool:
    row = conn.execute("SELECT password_hash FROM users WHERE id=?", (user_id,)).fetchone()
    return bool(row) and not str(row["password_hash"] or "").startswith("!")


def providers(conn: sqlite3.Connection, user_id: int) -> list[str]:
    """The sign-in providers (google, github) linked to an account."""
    return [r["provider"] for r in conn.execute(
        "SELECT provider FROM identities WHERE user_id=? ORDER BY provider", (user_id,))]


def names(conn: sqlite3.Connection, user_id: int) -> tuple[str, str]:
    row = conn.execute("SELECT first_name, last_name FROM users WHERE id=?",
                       (user_id,)).fetchone()
    return (row["first_name"] or "", row["last_name"] or "") if row else ("", "")


def set_names(conn: sqlite3.Connection, user_id: int, first: str, last: str) -> None:
    with LOCK, conn:
        conn.execute("UPDATE users SET first_name=?, last_name=? WHERE id=?",
                     (clean_name(first), clean_name(last), user_id))


def display_name(first: str, last: str, email: str = "") -> str:
    """How a person is shown to others: their name, else their email."""
    return " ".join(p for p in (first, last) if p) or email


def create_user(conn: sqlite3.Connection, email: str, password: str, first: str = "",
                last: str = "") -> int:
    email = email_key(email)
    if not email or "@" not in email:
        raise ValueError("email must look like an email address")
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    with LOCK, conn:
        cur = conn.execute(
            "INSERT INTO users (email, password_hash, created_at, first_name, last_name)"
            " VALUES (?,?,?,?,?)",
            (email, hash_password(password), time.time(), clean_name(first), clean_name(last)))
    return int(cur.lastrowid)


def set_password(conn: sqlite3.Connection, email: str, password: str) -> bool:
    if len(password) < 12:
        raise ValueError("password must be at least 12 characters")
    with LOCK, conn:
        # Recording when the password changed is what signs out every session
        # issued before it (see session_is_current).
        cur = conn.execute(
            "UPDATE users SET password_hash=?, password_changed_at=? WHERE email=?",
            (hash_password(password), round(time.time(), 3), email_key(email)))
    return cur.rowcount > 0


def authenticate(conn: sqlite3.Connection, email: str, password: str) -> dict | None:
    row = conn.execute("SELECT * FROM users WHERE email=?", (email_key(email),)).fetchone()
    if row is None:
        # Hash anyway, so a missing user and a wrong password take the same
        # time and the response cannot be used to enumerate accounts.
        hash_password(password)
        return None
    if not verify_password(password, row["password_hash"]):
        return None
    with LOCK, conn:
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (time.time(), row["id"]))
    return {"id": row["id"], "email": row["email"]}


RESET_TTL = 3600


def create_reset(conn: sqlite3.Connection, email: str) -> str | None:
    """A one-use password reset token for an existing account, else None. Any
    older unused token for the account stops working."""
    user = user_by_email(conn, email)
    if not user:
        return None
    token = "dxpr_" + secrets.token_urlsafe(32)
    now = time.time()
    with LOCK, conn:
        conn.execute("UPDATE password_resets SET used_at=? WHERE user_id=? AND used_at IS NULL",
                     (now, user["id"]))
        conn.execute("INSERT INTO password_resets (token_hash, user_id, created_at, expires_at)"
                     " VALUES (?,?,?,?)", (hashlib.sha256(token.encode()).hexdigest(),
                                          user["id"], now, now + RESET_TTL))
    return token


def reset_email(conn: sqlite3.Connection, token: str) -> str | None:
    """The account a live reset token belongs to, without using it."""
    row = conn.execute(
        "SELECT r.*, u.email FROM password_resets r JOIN users u ON u.id = r.user_id"
        " WHERE r.token_hash=?", (hashlib.sha256((token or "").encode()).hexdigest(),)).fetchone()
    if not row or row["used_at"] or row["expires_at"] < time.time():
        return None
    return row["email"]


def use_reset(conn: sqlite3.Connection, token: str, new_password: str) -> str:
    """Set a new password with a reset token; returns the email. Raises
    ValueError for an unknown, used or expired token or a weak password."""
    email = reset_email(conn, token)
    if not email:
        raise ValueError("this reset link is invalid, used or expired")
    set_password(conn, email, new_password)          # also signs out every session
    with LOCK, conn:
        conn.execute("UPDATE password_resets SET used_at=? WHERE token_hash=?",
                     (time.time(), hashlib.sha256(token.encode()).hexdigest()))
    return email


def user_by_email(conn: sqlite3.Connection, email: str) -> dict | None:
    row = conn.execute("SELECT id, email FROM users WHERE email=?",
                       (email_key(email),)).fetchone()
    return {"id": row["id"], "email": row["email"]} if row else None


def get_setting(conn: sqlite3.Connection, name: str) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key=?", (name,)).fetchone()
    return row["value"] if row else None


def set_setting(conn: sqlite3.Connection, name: str, value: str) -> None:
    with LOCK, conn:
        conn.execute("INSERT INTO settings (key, value) VALUES (?,?) ON CONFLICT(key)"
                     " DO UPDATE SET value=excluded.value", (name, value))


def get_theme(conn: sqlite3.Connection, email: str) -> str | None:
    row = conn.execute("SELECT theme FROM users WHERE email=?",
                       (email_key(email),)).fetchone()
    return row["theme"] if row else None


def set_theme(conn: sqlite3.Connection, user_id: int, pref: str) -> None:
    with LOCK, conn:
        conn.execute("UPDATE users SET theme=? WHERE id=?", (pref, user_id))


# A password hash no password matches: the account signs in with Google or GitHub
# until its owner sets a password through "Forgot your password?".
NO_PASSWORD = "!social"


def user_from_identity(conn: sqlite3.Connection, provider: str, subject: str,
                       email: str, first: str = "", last: str = "") -> tuple[dict, bool]:
    """The Dexio account for a Google or GitHub account whose email the provider
    has verified: the one already linked to it, else the one with that email (now
    linked), else a new one without a password. Returns (user, created). The
    provider's name fills the account's only where it has none, so a name the
    person set on the account page is never overwritten."""
    email = email_key(email)
    if not provider or not subject or not email or "@" not in email:
        raise ValueError("the sign-in provider returned no usable account")
    now = time.time()
    with LOCK, conn:
        row = conn.execute(
            "SELECT u.id, u.email FROM identities i JOIN users u ON u.id = i.user_id"
            " WHERE i.provider=? AND i.subject=?", (provider, subject)).fetchone()
        linked = row is not None
        if row is None:
            row = conn.execute("SELECT id, email FROM users WHERE email=?", (email,)).fetchone()
        created = row is None
        if created:
            cur = conn.execute("INSERT INTO users (email, password_hash, created_at)"
                               " VALUES (?,?,?)", (email, NO_PASSWORD, now))
            row = {"id": int(cur.lastrowid), "email": email}
        if linked:
            conn.execute("UPDATE identities SET email=?, last_login=? WHERE provider=? AND"
                         " subject=?", (email, now, provider, subject))
        else:
            conn.execute("DELETE FROM identities WHERE provider=? AND subject=?",
                         (provider, subject))
            conn.execute("INSERT INTO identities (provider, subject, user_id, email, created_at,"
                         " last_login) VALUES (?,?,?,?,?,?)",
                         (provider, subject, row["id"], email, now, now))
        conn.execute("UPDATE users SET last_login=? WHERE id=?", (now, row["id"]))
        first, last = clean_name(first), clean_name(last)
        if first or last:
            conn.execute("UPDATE users SET first_name=?, last_name=? WHERE id=? AND"
                         " first_name='' AND last_name=''", (first, last, row["id"]))
    return {"id": row["id"], "email": row["email"]}, created


def list_users(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        "SELECT email, created_at, last_login FROM users ORDER BY email").fetchall()
    return [dict(r) for r in rows]


def count_users(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])


# ---- session secret ----------------------------------------------------
def secret_key(conn: sqlite3.Connection) -> bytes:
    """Prefer an explicit env var; otherwise persist a generated one.

    Persisting matters: a fresh key on every boot would silently invalidate
    every session each time the container restarts.
    """
    env = os.environ.get("DEXIO_SECRET_KEY", "")
    if env:
        return env.encode()
    row = conn.execute("SELECT value FROM settings WHERE key='secret_key'").fetchone()
    if row:
        return bytes.fromhex(row["value"])
    key = secrets.token_bytes(32)
    with LOCK, conn:
        conn.execute("INSERT INTO settings (key, value) VALUES ('secret_key', ?)",
                     (key.hex(),))
    return key


# ---- sessions ----------------------------------------------------------
def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_session(key: bytes, email: str, now: float | None = None) -> str:
    # Millisecond resolution, so a session issued right after a password change
    # is not mistaken for one issued before it.
    payload = json.dumps({"sub": email, "iat": round(now or time.time(), 3)},
                         separators=(",", ":")).encode()
    body = _b64e(payload)
    sig = hmac.new(key, body.encode(), hashlib.sha256).digest()
    return f"{body}.{_b64e(sig)}"


def read_session(key: bytes, cookie: str, now: float | None = None) -> str | None:
    claims = read_session_claims(key, cookie, now)
    return claims[0] if claims else None


def read_session_claims(key: bytes, cookie: str,
                        now: float | None = None) -> tuple[str, float] | None:
    """(email, issued-at) from a valid, unexpired session cookie, else None."""
    if not cookie or "." not in cookie:
        return None
    body, _, sig = cookie.partition(".")
    expected = hmac.new(key, body.encode(), hashlib.sha256).digest()
    try:
        if not hmac.compare_digest(_b64d(sig), expected):
            return None
        data = json.loads(_b64d(body))
    except (ValueError, TypeError):
        return None
    iat = float(data.get("iat", 0))
    if (now or time.time()) - iat > SESSION_MAX_AGE:
        return None
    sub = str(data.get("sub") or "")
    return (sub, iat) if sub else None


def session_is_current(conn: sqlite3.Connection, email: str, iat: float) -> bool:
    """A signed session still counts only if its account exists and it was issued
    after the account's last password change. Sessions are stateless cookies, so
    this check is how a password change signs out every other device."""
    row = conn.execute("SELECT password_changed_at FROM users WHERE email=?",
                       (email_key(email),)).fetchone()
    if row is None:
        return False
    changed = row[0]
    return changed is None or iat >= changed
