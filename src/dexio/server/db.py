"""Storage, SQLite or Postgres. One row per page, one row per page revision.

A wiki is created through `commit_pages`; a change made through MCP goes
through `apply_changes`, which writes only what it touches. Both hold `LOCK`.
The server shares a single connection across threads (MCP tools run in a worker pool,
HTTP routes on the event loop), so the lock is what keeps one writer's read-modify-write from dropping
another's page, and keeps a token's `last_used` commit from landing in the
middle of someone else's transaction.
"""
from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from collections.abc import MutableMapping

from .. import delta as deltas
from ..parse import Page, PageIndex, basename, build, page_links, resolve_keys, title_of
from . import pg

LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
  id         INTEGER PRIMARY KEY,
  name       TEXT NOT NULL,
  token_hash TEXT NOT NULL UNIQUE,
  project    TEXT,                 -- NULL means any project
  created_at REAL NOT NULL,
  last_used  REAL
);
CREATE TABLE IF NOT EXISTS projects (
  name       TEXT PRIMARY KEY,
  source     TEXT,
  updated_at REAL NOT NULL,
  pages      INTEGER NOT NULL DEFAULT 0,
  links      INTEGER NOT NULL DEFAULT 0,
  words      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS pages (
  project TEXT NOT NULL, path TEXT NOT NULL, file TEXT NOT NULL,
  title TEXT NOT NULL, folder TEXT NOT NULL, words INTEGER NOT NULL,
  degree INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL,
  PRIMARY KEY (project, path)
);
-- One row per link as written on a page, duplicates kept, in page order.
-- target is the link as normalised (no alias, anchor or .md); rel is it joined
-- to the source page's folder ('' at the root); base is its last segment; dst
-- is the page it resolves to, NULL when it resolves to nothing. `edges` and
-- `dangling` are views over this table (see _upgrade_links). A page created,
-- deleted or moved can only change links whose rel is its path or whose base
-- is its name, which is what lets one write touch only what it affects.
CREATE TABLE IF NOT EXISTS links (
  project TEXT NOT NULL, src TEXT NOT NULL, pos INTEGER NOT NULL,
  target TEXT NOT NULL, rel TEXT NOT NULL, base TEXT NOT NULL, dst TEXT,
  PRIMARY KEY (project, src, pos)
);
CREATE TABLE IF NOT EXISTS pushes (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, source TEXT,
  at REAL NOT NULL, pages INTEGER, links INTEGER, token_name TEXT
);
-- One row per change to a page. The page after the change is stored either
-- whole in text, or as delta (a migration): the edits from the page's previous
-- revision (see delta.py and _stored_form). A row with neither is a change that
-- removed the page. chars, words and version (migrations) describe the page
-- after the change, so a history listing never rebuilds a text. op is push, write, edit, append, delete, move, several
-- joined by + when one batch did more than one thing to the page, or baseline
-- (the text a page had before its first tracked change). note (a migration) is
-- the writer's own line on what changed and why. author is the token (or OAuth
-- client) that made the change; agent (a migration) is who the caller says is
-- making it, required on every MCP change so a shared token still shows which
-- agent wrote what; user_id (a migration) is the person: the account that
-- signed in through OAuth, or that created the token (see person_of).
CREATE TABLE IF NOT EXISTS revisions (
  id INTEGER PRIMARY KEY, project TEXT NOT NULL, path TEXT NOT NULL,
  at REAL NOT NULL, op TEXT NOT NULL, author TEXT, text TEXT
);
-- A workspace owns its wiki and tokens; users reach it through memberships and
-- may belong to several. Wiki rows in every table above are keyed by
-- "<workspace id>:<wiki name>" (see key()). Since 2026-09-28 every workspace has
-- exactly one wiki, named "main" (see WIKI and one_wiki_each).
CREATE TABLE IF NOT EXISTS workspaces (
  id INTEGER PRIMARY KEY, name TEXT NOT NULL, plan TEXT NOT NULL DEFAULT 'free',
  created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS memberships (
  workspace_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
  role TEXT NOT NULL DEFAULT 'member', created_at REAL NOT NULL,
  PRIMARY KEY (workspace_id, user_id)
);
CREATE TABLE IF NOT EXISTS invites (
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, code_hash TEXT NOT NULL UNIQUE,
  created_by INTEGER, created_at REAL NOT NULL, expires_at REAL NOT NULL,
  used_by INTEGER, used_at REAL
);
-- A renamed wiki's old name keeps reaching it (see resolve_wiki), the way a
-- renamed repository redirects, so agents set up with the old name keep working.
-- The old name is released when a wiki takes it again.
CREATE TABLE IF NOT EXISTS wiki_renames (
  workspace_id INTEGER NOT NULL, old_name TEXT NOT NULL, new_name TEXT NOT NULL,
  at REAL NOT NULL, author TEXT, user_id INTEGER,
  PRIMARY KEY (workspace_id, old_name)
);
-- Sharing (shares.py): the whole wiki (kind 'wiki', path ''), a folder and
-- everything under it, or one page, viewable by one person (email, bound to
-- user_id when they open the emailed link or sign in with a provider that
-- verified that address) or by anyone with the link (email and user_id NULL).
CREATE TABLE IF NOT EXISTS shares (
  id INTEGER PRIMARY KEY, workspace_id INTEGER NOT NULL, kind TEXT NOT NULL,
  path TEXT NOT NULL, email TEXT, user_id INTEGER, code_hash TEXT,
  created_by INTEGER, created_at REAL NOT NULL, accepted_at REAL
);
CREATE INDEX IF NOT EXISTS shares_ws ON shares(workspace_id);
CREATE INDEX IF NOT EXISTS shares_user ON shares(user_id);
CREATE INDEX IF NOT EXISTS memberships_user ON memberships(user_id);
CREATE INDEX IF NOT EXISTS links_base ON links(project, base);
CREATE INDEX IF NOT EXISTS links_rel ON links(project, rel);
CREATE INDEX IF NOT EXISTS links_dst ON links(project, dst);
CREATE INDEX IF NOT EXISTS pushes_project ON pushes(project, at DESC);
CREATE INDEX IF NOT EXISTS revisions_page ON revisions(project, path, id DESC);
CREATE INDEX IF NOT EXISTS revisions_project ON revisions(project, at);
"""

# Columns added after the first release. connect() adds any that are missing,
# so an existing database upgrades in place on the next start.
MIGRATIONS = [
    ("pages", "updated_at", "REAL NOT NULL DEFAULT 0"),
    ("projects", "last_push_at", "REAL"),
    ("projects", "last_edit_at", "REAL"),
    ("projects", "workspace_id", "INTEGER"),
    ("projects", "wiki", "TEXT"),
    ("tokens", "workspace_id", "INTEGER"),
    ("tokens", "created_by", "INTEGER"),
    ("workspaces", "stripe_customer", "TEXT"),
    ("workspaces", "stripe_subscription", "TEXT"),
    ("workspaces", "billing_status", "TEXT"),
    ("workspaces", "billing_interval", "TEXT"),
    ("workspaces", "billing_period_end", "REAL"),   # when the paid period renews
    ("workspaces", "billing_ends_at", "REAL"),      # set once a cancellation is scheduled
    ("pages", "base", "TEXT"),            # last path segment, for link resolution
    ("projects", "dangling", "INTEGER"),  # broken links, kept like pages/links/words
    ("revisions", "note", "TEXT"),        # the writer's own line on what changed and why
    ("revisions", "agent", "TEXT"),       # who the caller says made the change
    ("revisions", "user_id", "INTEGER"),  # the account behind the token or OAuth grant
    ("revisions", "delta", "TEXT"),       # edits from the previous revision, when text is NULL
    ("revisions", "chars", "INTEGER"),    # the page after the change: length,
    ("revisions", "words", "INTEGER"),    # word count,
    ("revisions", "version", "TEXT"),     # and version_of; '' when the change removed it
    ("invites", "email", "TEXT"),         # who it was sent to; NULL for pre-email link invites
    # When a person gave the workspace its name: the first graph screen asks an owner
    # to until then (2026-09-28). NULL for one named for its creator at sign-up.
    ("workspaces", "named_at", "REAL"),
    # What addresses call the workspace (/w/<handle>, ?w=<handle>): random, so an
    # address does not show how many workspaces came before it (see new_handle).
    ("workspaces", "handle", "TEXT"),
    # When a public share's owner listed it in the directory on dexio.wiki, where
    # anyone can find it and make a copy (shares.set_listed); NULL when not listed.
    ("shares", "listed_at", "REAL"),
    # What the directory shows for a listing, as its owner wrote it (Forrest, 2026-10-01:
    # "when they publish a wiki, we should allow them to give it a name and description",
    # and "show the author"); NULL falls back to the wiki's own (shares.listing).
    ("shares", "listed_title", "TEXT"),
    ("shares", "listed_description", "TEXT"),
    ("shares", "listed_author", "TEXT"),      # unused: the author is the account's name

    ("shares", "listed_by", "INTEGER"),
    # Who a workspace publishes as on dexio.wiki, unique across Dexio (Forrest,
    # 2026-10-01: "a publisher name field in the settings ... a workspace setting",
    # "It should also be unique"). publisher_key is the name folded for comparing
    # (shares.publisher_key); a unique index on it keeps two workspaces apart.
    ("workspaces", "publisher_name", "TEXT"),
    ("workspaces", "publisher_key", "TEXT"),
    # Unused since the evening of 2026-10-06: a key acts as its person everywhere,
    # with no switch (Forrest: "allow access to anything the user can see").
    ("tokens", "shared_reach", "INTEGER"),
]

# A page's history keeps a whole copy at least every this many revisions, so
# reading an old version applies at most this many deltas.
SNAPSHOT_EVERY = 50

# Members a workspace may have on each plan; None means no limit.
MEMBER_LIMITS = {"free": 1, "team": None, "business": None, "enterprise": None}
WIKI_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def version_of(text: str | None) -> str:
    """Short content hash. A page's version changes exactly when its text does."""
    if text is None:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    """Open the database: Postgres for a `postgresql://` URL (DEXIO_DATABASE_URL when
    no path is given), otherwise a SQLite file (DEXIO_DB). Postgres comes back
    wrapped to look like a sqlite3 connection; see pg.py."""
    path = str(path or os.environ.get("DEXIO_DATABASE_URL")
               or os.environ.get("DEXIO_DB", "dexio.db"))
    if pg.is_url(path):
        conn = pg.connect(path)
    else:
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(SCHEMA)
    added = set()
    for table, column, decl in MIGRATIONS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
            added.add((table, column))
    if ("workspaces", "named_at") in added:
        # Workspaces from before the question count as named: their people are past
        # the first screen, and asking them now would interrupt, not onboard.
        conn.execute("UPDATE workspaces SET named_at = created_at")
    # Pages that predate updated_at take their project's last change time.
    conn.execute("UPDATE pages SET updated_at = (SELECT updated_at FROM projects"
                 " WHERE projects.name = pages.project) WHERE updated_at = 0")
    conn.execute("UPDATE projects SET last_push_at = (SELECT MAX(at) FROM pushes"
                 " WHERE pushes.project = projects.name) WHERE last_push_at IS NULL")
    # Keys once could be limited to one wiki; every key now covers its workspace.
    conn.execute("UPDATE tokens SET project = NULL WHERE project IS NOT NULL")
    conn.commit()
    _upgrade_links(conn)
    _move_into_workspaces(conn)
    one_wiki_each(conn)
    give_handles(conn)
    keys_to_people(conn)
    with LOCK:
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS workspaces_publisher"
                     " ON workspaces(publisher_key)")
        conn.commit()
    upgrade_publishers(conn)
    compact_history(conn)
    return conn


# ---- workspaces --------------------------------------------------------
# One workspace, one wiki (Forrest, 2026-09-28). Members, keys, billing and
# storage were already workspace-wide, so a second wiki in a workspace kept
# nothing apart and only split links, search and the graph. The wiki's internal
# name is always WIKI; people and agents never see or pass it.
WIKI = "main"

# A workspace's handle: what every address and request from a browser or agent
# names it by. The id counts up from 1, so /w/<id> told a new customer how early
# they were (Forrest, 2026-09-28); the id stays internal. Lowercase letters and
# digits without the ones read as each other (0/o, 1/l/i), 8 of them (31^8, about
# 850 billion), starting with a letter so a handle is never mistaken for an id.
HANDLE_LETTERS = "abcdefghjkmnpqrstuvwxyz"
HANDLE_CHARS = HANDLE_LETTERS + "23456789"
HANDLE_LEN = 8


def new_handle() -> str:
    return secrets.choice(HANDLE_LETTERS) + "".join(
        secrets.choice(HANDLE_CHARS) for _ in range(HANDLE_LEN - 1))


def _unused_handle(conn) -> str:
    while True:
        h = new_handle()
        if not conn.execute("SELECT 1 FROM workspaces WHERE handle=?", (h,)).fetchone():
            return h


def give_handles(conn) -> None:
    """Every workspace gets a handle once, kept for good; the index keeps them unique."""
    with LOCK:
        for r in conn.execute("SELECT id FROM workspaces WHERE handle IS NULL").fetchall():
            conn.execute("UPDATE workspaces SET handle=? WHERE id=?",
                         (_unused_handle(conn), r["id"]))
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS workspaces_handle ON workspaces(handle)")
        conn.commit()


# A workspace's publisher name on dexio.wiki is a handle, as a GitHub name is
# (Forrest, 2026-10-01: "should we allow spaces in the publisher name?", then yes to
# no spaces): letters, numbers and single hyphens, 2 to 39 characters, starting and
# ending with a letter or number. It is shown as typed and names the publisher's
# page, dexio.wiki/wikis/<name in lowercase>. Two names are the same publisher when
# they match without case or hyphens (Wrenfield-Roasters and wrenfieldroasters), so
# a look-alike cannot be taken; publisher_key holds that form, unique by index.
PUBLISHER_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){1,38}$")


def publisher_key(name: str) -> str:
    return str(name or "").replace("-", "").lower()


def upgrade_publishers(conn) -> None:
    """Bring publisher names written under the first rule (spaces allowed, the
    same day) to the handle rule: spaces and punctuation become hyphens; a name
    that still does not fit, or now matches an earlier one, is cleared for its
    owner to choose again. Does nothing once every name fits."""
    with LOCK:
        rows = conn.execute("SELECT id, publisher_name, publisher_key FROM workspaces"
                            " WHERE publisher_name IS NOT NULL ORDER BY id").fetchall()
        if all(PUBLISHER_RE.match(r["publisher_name"] or "")
               and r["publisher_key"] == publisher_key(r["publisher_name"]) for r in rows):
            return
        with conn:
            conn.execute("UPDATE workspaces SET publisher_key=NULL WHERE publisher_key IS NOT NULL")
            seen: set[str] = set()
            for r in rows:
                name = re.sub(r"[^A-Za-z0-9]+", "-", r["publisher_name"] or "").strip("-")
                key = publisher_key(name)
                if not PUBLISHER_RE.match(name) or key in seen:
                    conn.execute("UPDATE workspaces SET publisher_name=NULL WHERE id=?", (r["id"],))
                    continue
                seen.add(key)
                conn.execute("UPDATE workspaces SET publisher_name=?, publisher_key=? WHERE id=?",
                             (name, key, r["id"]))


def handle_of(conn, workspace_id: int) -> str:
    row = conn.execute("SELECT handle FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return row["handle"] if row else ""


def find_workspace(workspaces: list[dict], wanted) -> dict | None:
    """The one `wanted` names among `workspaces` (a person's): by handle, or by id
    for an address or cookie from before handles."""
    wanted = str(wanted or "")
    return next((w for w in workspaces if wanted and wanted in (w.get("handle"), str(w["id"]))),
                None)


def key(workspace_id: int, wiki: str) -> str:
    """Internal key for a wiki: every content table stores this in `project`."""
    return f"{int(workspace_id)}:{wiki}"


def wiki_key(workspace_id: int) -> str:
    """The key of a workspace's wiki."""
    return key(workspace_id, WIKI)


def ensure_wiki(conn, workspace_id: int, *, source: str = "dexio", author: str = "",
                user_id: int | None = None) -> str:
    """A workspace's wiki, made empty if it does not exist yet. Returns its key.
    The check holds LOCK, as writes do: the connection is shared across threads."""
    k = wiki_key(workspace_id)
    with LOCK:
        if not project_exists(conn, k):
            commit_pages(conn, k, {}, source=source, author=author, op="create",
                         user_id=user_id)
    return k


def one_wiki_each(conn) -> None:
    """Give every workspace its one wiki, named WIKI. A workspace with none gets an
    empty one; one whose wikis all have other names (from before 2026-09-28) has the
    largest renamed. Idempotent. Nothing is deleted: any other wiki a workspace
    still holds stays in the database, out of reach, until removed by hand."""
    have_one = {r["workspace_id"] for r in conn.execute(
        "SELECT workspace_id FROM projects WHERE wiki=?", (WIKI,))}
    for r in conn.execute("SELECT id FROM workspaces ORDER BY id").fetchall():
        ws = int(r["id"])
        if ws in have_one or project_exists(conn, wiki_key(ws)):
            continue
        have = sorted(projects(conn, ws), key=lambda p: (-(p["pages"] or 0), p["name"]))
        if have:
            rename_wiki(conn, ws, have[0]["name"], WIKI, author="one wiki per workspace")
        else:
            ensure_wiki(conn, ws)


def split_key(k: str) -> tuple[int | None, str]:
    head, sep, rest = str(k).partition(":")
    if sep and head.isdigit():
        return int(head), rest
    return None, str(k)


def valid_wiki_name(name: str) -> bool:
    return bool(WIKI_NAME.match(name or ""))


WIKI_NAME_RULE = ("a wiki's name is up to 64 letters, digits, dots, dashes and underscores,"
                  " starting with a letter or digit")

def create_wiki(conn, workspace_id: int, name: str, *, source: str, author: str = "",
                user_id: int | None = None) -> str:
    """Make an empty wiki in a workspace: "created", or "exists" when that exact
    name is taken. ValueError for a bad name, or for one that differs from an
    existing wiki's only in case: a `Main` beside `main` is a typo in waiting.
    Since 2026-09-26 this is the only way a wiki comes to exist (bulk push was
    removed 2026-09-27); MCP changes to a missing wiki are refused rather than
    creating it."""
    name = (name or "").strip()
    if not valid_wiki_name(name):
        raise ValueError(WIKI_NAME_RULE)
    with LOCK:
        names = [p["name"] for p in projects(conn, workspace_id)]
        if name in names:
            return "exists"
        clash = next((n for n in names if n.lower() == name.lower()), None)
        if clash:
            raise ValueError(f"a wiki called {clash!r} already exists; names that differ only"
                             " in case are not allowed")
        commit_pages(conn, key(workspace_id, name), {}, source=source, author=author,
                     op="create", user_id=user_id)
        # A new wiki takes its name back from a renamed one, as a new repository
        # does from a renamed one's redirect.
        with conn:
            conn.execute("DELETE FROM wiki_renames WHERE workspace_id=? AND old_name=?",
                         (workspace_id, name))
    return "created"


# Every table that keys a wiki's rows by db.key(); a rename rewrites them all.
KEYED_TABLES = ("pages", "links", "pushes", "revisions", "files", "uploads")


def rename_wiki(conn, workspace_id: int, old: str, new: str, *, author: str = "",
                user_id: int | None = None) -> str:
    """Rename a wiki: "renamed", or "same" when new is its name already.
    ValueError when old does not exist, new is not a valid name, or another
    wiki has it (in any case). Pages, links, history and files move with it,
    and the old name keeps reaching it (see
    resolve_wiki) until a wiki takes that name again. Stored file bytes are keyed
    by file id, not by wiki, so nothing moves in the bucket."""
    old, new = (old or "").strip(), (new or "").strip()
    if not valid_wiki_name(new):
        raise ValueError(WIKI_NAME_RULE)
    with LOCK:
        names = [p["name"] for p in projects(conn, workspace_id)]
        if old not in names:
            raise ValueError(f"no wiki called {old!r}")
        if new == old:
            return "same"
        clash = next((n for n in names if n != old and n.lower() == new.lower()), None)
        if clash:
            raise ValueError(f"a wiki called {clash!r} already exists" + (
                "" if clash == new else "; names that differ only in case are not allowed"))
        ok, nk = key(workspace_id, old), key(workspace_id, new)
        # files and uploads belong to files.py, which a bare database may lack.
        tables = [t for t in KEYED_TABLES if _kind(conn, t) == "table"]
        with conn:
            for table in tables:
                conn.execute(f"UPDATE {table} SET project=? WHERE project=?", (nk, ok))
            conn.execute("UPDATE projects SET name=?, wiki=? WHERE name=?", (nk, new, ok))
            # Redirects: the new name is a wiki now, names that led to old lead to
            # new, and old leads to new.
            conn.execute("DELETE FROM wiki_renames WHERE workspace_id=? AND old_name=?",
                         (workspace_id, new))
            conn.execute("UPDATE wiki_renames SET new_name=? WHERE workspace_id=?"
                         " AND new_name=?", (new, workspace_id, old))
            conn.execute("INSERT OR REPLACE INTO wiki_renames (workspace_id, old_name,"
                         " new_name, at, author, user_id) VALUES (?,?,?,?,?,?)",
                         (workspace_id, old, new, time.time(), author or None, user_id))
    return "renamed"


def delete_wiki(conn, workspace_id: int, name: str) -> list[str]:
    """Delete a wiki and everything in it: pages, links, history, pushes, files
    and pending uploads, and the old names that led to it, so a former name no
    longer reaches anything. Unlike deleting a page, no history is kept; it
    cannot be undone. ValueError when the workspace has no wiki by that exact
    name. Returns the stored-file keys (files.py keys bytes by file id) for the
    caller to remove once this has committed."""
    name = (name or "").strip()
    with LOCK:
        k = key(workspace_id, name)
        if not name or not project_exists(conn, k):
            raise ValueError(f"no wiki called {name!r}")
        # files and uploads belong to files.py, which a bare database may lack.
        tables = [t for t in KEYED_TABLES if _kind(conn, t) == "table"]
        stored = ([f"ws/{int(workspace_id)}/{r['id']}" for r in
                   conn.execute("SELECT id FROM files WHERE project=?", (k,)).fetchall()]
                  if "files" in tables else [])
        with conn:
            for table in tables:
                conn.execute(f"DELETE FROM {table} WHERE project=?", (k,))
            conn.execute("DELETE FROM projects WHERE name=?", (k,))
            conn.execute("DELETE FROM wiki_renames WHERE workspace_id=? AND new_name=?",
                         (workspace_id, name))
    return stored


def resolve_wiki(conn, workspace_id: int, name: str) -> str:
    """The wiki a name reaches: itself when a wiki has it, else the wiki it was
    renamed to, else the name unchanged (callers then report it missing)."""
    name = (name or "").strip()
    if not name or project_exists(conn, key(workspace_id, name)):
        return name
    row = conn.execute("SELECT new_name FROM wiki_renames WHERE workspace_id=? AND"
                       " old_name=?", (workspace_id, name)).fetchone()
    if row and project_exists(conn, key(workspace_id, row["new_name"])):
        return row["new_name"]
    return name


def former_names(conn, workspace_id: int) -> dict[str, list[str]]:
    """Each wiki's old names that still reach it, oldest rename first."""
    out: dict[str, list[str]] = {}
    for r in conn.execute("SELECT old_name, new_name FROM wiki_renames WHERE workspace_id=?"
                          " ORDER BY at", (workspace_id,)):
        out.setdefault(r["new_name"], []).append(r["old_name"])
    return out


def default_workspace(conn) -> int:
    """The first workspace, created if none exists. Single-operator paths (the
    admin token, `dexio token`, `dexio user add`) put things here."""
    row = conn.execute("SELECT id FROM workspaces ORDER BY id LIMIT 1").fetchone()
    if row:
        return int(row["id"])
    with LOCK, conn:
        cur = conn.execute("INSERT INTO workspaces (name, plan, created_at, handle)"
                           " VALUES (?,?,?,?)",
                           ("Default workspace", "business", time.time(), _unused_handle(conn)))
    # No wiki made here: upgrading a database from before workspaces moves its
    # wikis in next, and one_wiki_each then names the largest main.
    return int(cur.lastrowid)


def _move_into_workspaces(conn) -> None:
    """Upgrade a database from before workspaces: every existing wiki and token
    moves into the default workspace. Idempotent."""
    loose = [r["name"] for r in conn.execute(
        "SELECT name FROM projects WHERE workspace_id IS NULL")]
    loose_tokens = conn.execute(
        "SELECT COUNT(*) FROM tokens WHERE workspace_id IS NULL").fetchone()[0]
    if not loose and not loose_tokens:
        return
    ws = default_workspace(conn)
    with LOCK, conn:
        for name in loose:
            k = key(ws, name)
            for table in ("pages", "links", "pushes", "revisions"):
                conn.execute(f"UPDATE {table} SET project=? WHERE project=?", (k, name))
            conn.execute("UPDATE projects SET name=?, wiki=?, workspace_id=? WHERE name=?",
                         (k, name, ws, name))
        conn.execute("UPDATE tokens SET workspace_id=? WHERE workspace_id IS NULL", (ws,))


def seed_memberships(conn) -> None:
    """Once, after the users table exists: give every account that predates
    workspaces a membership, as owner, in the default workspace."""
    done = conn.execute("SELECT value FROM settings WHERE key='memberships_seeded'").fetchone()
    if done:
        return
    users = [r["id"] for r in conn.execute("SELECT id FROM users ORDER BY id")]
    with LOCK, conn:
        if users:
            ws = default_workspace(conn)
            for uid in users:
                conn.execute("INSERT OR IGNORE INTO memberships (workspace_id, user_id, role,"
                             " created_at) VALUES (?,?,?,?)", (ws, uid, "owner", time.time()))
        conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES"
                     " ('memberships_seeded', '1')")


def create_workspace(conn, name: str, owner_id: int | None, plan: str = "free",
                     named: bool = False) -> int:
    """A workspace with its wiki. named: a person chose the name (the workspace
    menu's New workspace), so the first graph screen does not ask for one."""
    name = (name or "").strip()[:80] or "My workspace"
    with LOCK, conn:
        now = time.time()
        cur = conn.execute("INSERT INTO workspaces (name, plan, created_at, named_at, handle)"
                           " VALUES (?,?,?,?,?)",
                           (name, plan, now, now if named else None, _unused_handle(conn)))
        ws = int(cur.lastrowid)
        if owner_id is not None:
            conn.execute("INSERT INTO memberships (workspace_id, user_id, role, created_at)"
                         " VALUES (?,?,?,?)", (ws, owner_id, "owner", time.time()))
    ensure_wiki(conn, ws, user_id=owner_id)
    return ws


def rename_workspace(conn, workspace_id: int, name: str) -> None:
    """Rename a workspace, which also counts as naming it (named_at)."""
    name = (name or "").strip()[:80]
    if not name:
        raise ValueError("a workspace needs a name")
    with LOCK, conn:
        conn.execute("UPDATE workspaces SET name=?, named_at=? WHERE id=?",
                     (name, time.time(), workspace_id))


def workspace(conn, workspace_id: int):
    row = conn.execute("SELECT * FROM workspaces WHERE id=?", (workspace_id,)).fetchone()
    return dict(row) if row else None


def workspace_by_handle(conn, handle) -> dict | None:
    """A workspace by its handle only: what a share's address names. Never by id,
    so a stranger cannot walk the ids looking for something shared."""
    handle = str(handle or "").strip()
    if not handle or handle.isdigit():
        return None
    row = conn.execute("SELECT * FROM workspaces WHERE handle=?", (handle,)).fetchone()
    return dict(row) if row else None


def workspaces_for_user(conn, user_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT w.id, w.handle, w.name, w.plan, w.publisher_name, m.role FROM workspaces w"
        " JOIN memberships m"
        " ON m.workspace_id = w.id WHERE m.user_id=? ORDER BY w.id", (user_id,))]


def role_in(conn, workspace_id: int, user_id: int) -> str | None:
    row = conn.execute("SELECT role FROM memberships WHERE workspace_id=? AND user_id=?",
                       (workspace_id, user_id)).fetchone()
    return row["role"] if row else None


def members(conn, workspace_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT u.id, u.email, u.first_name, u.last_name, m.role, m.created_at"
        " FROM memberships m JOIN users u"
        " ON u.id = m.user_id WHERE m.workspace_id=? ORDER BY m.created_at", (workspace_id,))]


def add_member(conn, workspace_id: int, user_id: int, role: str = "member") -> None:
    with LOCK, conn:
        conn.execute("INSERT OR IGNORE INTO memberships (workspace_id, user_id, role,"
                     " created_at) VALUES (?,?,?,?)", (workspace_id, user_id, role, time.time()))


def plans_apply() -> bool:
    """Plans, and the member and storage limits that come with them, exist only
    where a plan can be bought: a server with a Stripe key. A server without one,
    a self-hosted copy, has no plan limits at all (Forrest, 2026-09-30), since its
    people would have no way to choose a plan. billing.enabled() reads the same key."""
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def member_limit(conn, workspace_id: int) -> int | None:
    if not plans_apply():
        return None
    ws = workspace(conn, workspace_id) or {}
    return MEMBER_LIMITS.get(ws.get("plan") or "free", 1)


def read_only_reason(conn, workspace_id: int) -> str:
    """Why nothing in this workspace can be changed right now, or "" if it can.
    A workspace with more members than its plan allows is read-only for everyone,
    people and agents, until an owner removes members or picks a plan (Forrest,
    2026-09-27, as Bitbucket does). A move to Free the owner chooses needs them
    alone first (billing.free_blocker), so this is for a plan that ended without
    anyone choosing: Stripe giving up on a card, or a plan changed by hand.
    Reading, and Settings (members, plan, keys), stay open."""
    limit = member_limit(conn, workspace_id)
    if limit is None:
        return ""
    n = len(members(conn, workspace_id))
    if n <= limit:
        return ""
    return (f"this workspace is read-only: it is on the Free plan, which is for one person,"
            f" and it has {n} members. An owner can remove members under Settings, Members,"
            f" or choose a plan under Settings, Plan, at https://app.dexio.wiki/settings/plan")


def create_invite(conn, workspace_id: int, created_by: int, days: int = 7,
                  email: str | None = None) -> str:
    code = "dxi_" + secrets.token_urlsafe(24)
    now = time.time()
    with LOCK, conn:
        conn.execute("INSERT INTO invites (workspace_id, code_hash, created_by, created_at,"
                     " expires_at, email) VALUES (?,?,?,?,?,?)",
                     (workspace_id, hash_token(code), created_by, now, now + days * 86400,
                      email))
    return code


def pending_invites(conn, workspace_id: int) -> list[dict]:
    """Invites sent by email that nobody has used yet and that have not expired."""
    return [dict(r) for r in conn.execute(
        "SELECT id, email, created_by, created_at, expires_at FROM invites"
        " WHERE workspace_id=? AND used_at IS NULL AND expires_at > ? AND email IS NOT NULL"
        " ORDER BY created_at", (workspace_id, time.time()))]


def pending_invite(conn, workspace_id: int, invite_id: int) -> dict | None:
    return next((i for i in pending_invites(conn, workspace_id) if i["id"] == invite_id), None)


def withdraw_invites(conn, workspace_id: int, email: str, keep: str | None = None) -> int:
    """Delete this address's unused invites to the workspace, except the one whose
    code is `keep`. Their links stop working."""
    with LOCK, conn:
        cur = conn.execute(
            "DELETE FROM invites WHERE workspace_id=? AND used_at IS NULL"
            " AND lower(email)=lower(?) AND code_hash<>?",
            (workspace_id, email, hash_token(keep) if keep else ""))
    return cur.rowcount


def cancel_invite(conn, workspace_id: int, invite_id: int) -> bool:
    with LOCK, conn:
        cur = conn.execute("DELETE FROM invites WHERE id=? AND workspace_id=? AND used_at IS NULL",
                           (invite_id, workspace_id))
    return cur.rowcount > 0


def delete_invite(conn, code: str) -> None:
    with LOCK, conn:
        conn.execute("DELETE FROM invites WHERE code_hash=?", (hash_token(code or ""),))


def accept_invite(conn, code: str, user_id: int) -> int:
    """Join the invite's workspace. Returns the workspace id; raises ValueError
    with a reason a person can act on."""
    with LOCK, conn:
        row = conn.execute("SELECT * FROM invites WHERE code_hash=?",
                           (hash_token(code or ""),)).fetchone()
        if not row or row["used_at"] or row["expires_at"] < time.time():
            raise ValueError("this invite link is invalid, used or expired")
        ws = int(row["workspace_id"])
        if role_in(conn, ws, user_id):
            return ws
        limit = member_limit(conn, ws)
        count = conn.execute("SELECT COUNT(*) FROM memberships WHERE workspace_id=?",
                             (ws,)).fetchone()[0]
        if limit is not None and count >= limit:
            raise ValueError("this workspace is at its member limit for its plan")
        conn.execute("INSERT INTO memberships (workspace_id, user_id, role, created_at)"
                     " VALUES (?,?,?,?)", (ws, user_id, "member", time.time()))
        conn.execute("UPDATE invites SET used_by=?, used_at=? WHERE id=?",
                     (user_id, time.time(), row["id"]))
    return ws


# ---- tokens ------------------------------------------------------------
def create_token(conn, name: str, workspace_id: int | None = None,
                 created_by: int | None = None) -> str:
    """Mint an API key for one workspace. It reaches every wiki there; there are no
    keys for a single wiki (Forrest 2026-09-27).
    Without a workspace it goes to the default one (operator paths)."""
    token = "dxk_" + secrets.token_urlsafe(32)
    ws = workspace_id if workspace_id is not None else default_workspace(conn)
    with LOCK, conn:
        conn.execute(
            "INSERT INTO tokens (name, token_hash, project, created_at, workspace_id, created_by)"
            " VALUES (?,?,?,?,?,?)",
            (name, hash_token(token), None, time.time(), ws, created_by))
    return token


def delete_token(conn, token_id: int, workspace_id: int) -> bool:
    with LOCK, conn:
        cur = conn.execute("DELETE FROM tokens WHERE id=? AND workspace_id=?",
                           (token_id, workspace_id))
    return cur.rowcount > 0


def keys_to_people(conn) -> None:
    """Every API key belongs to a person and acts as them (Forrest, 2026-10-06: "migrate
    all existing workspace keys to the user, and have it allow access to anything the
    user can see"). Keys minted before keys had a creator (the operator endpoint, gone
    since 2026-09-27) go to their workspace's first owner, the account person_of already
    recorded their changes against."""
    with LOCK, conn:
        conn.execute(
            "UPDATE tokens SET created_by = (SELECT m.user_id FROM memberships m"
            " WHERE m.workspace_id = tokens.workspace_id AND m.role = 'owner'"
            " ORDER BY m.created_at, m.user_id LIMIT 1)"
            " WHERE created_by IS NULL AND workspace_id IS NOT NULL")


def check_token(conn, token: str):
    # The read takes the write lock too. Every thread shares this connection, and a
    # commit from another thread can reset a statement mid-fetch, so under concurrent
    # writes an unlocked lookup sometimes came back empty and a valid token was refused.
    with LOCK:
        row = conn.execute("SELECT * FROM tokens WHERE token_hash=?",
                           (hash_token(token),)).fetchone()
        if row:
            with conn:
                conn.execute("UPDATE tokens SET last_used=? WHERE id=?", (time.time(), row["id"]))
    return row


def person_of(conn, row) -> int | None:
    """The account a change is recorded against: the person who signed in, for
    an OAuth token (Claude, ChatGPT); the account that created the token, for a
    workspace token; for a token minted by the operator, with no creator on
    record, the workspace's owner."""
    keys = set(row.keys())
    if "user_id" in keys and row["user_id"]:
        return int(row["user_id"])
    if "created_by" in keys and row["created_by"]:
        return int(row["created_by"])
    ws = row["workspace_id"] if "workspace_id" in keys else None
    if ws is None:
        return None
    owner = conn.execute("SELECT user_id FROM memberships WHERE workspace_id=? AND role='owner'"
                         " ORDER BY created_at, user_id LIMIT 1", (ws,)).fetchone()
    return int(owner["user_id"]) if owner else None


def people(conn, ids) -> dict[int, str]:
    """Display names (name, else email) for account ids."""
    ids = sorted({int(i) for i in ids if i})
    out: dict[int, str] = {}
    for chunk in _chunks(ids):
        for r in conn.execute(f"SELECT id, email, first_name, last_name FROM users"
                              f" WHERE id IN ({_marks(len(chunk))})", tuple(chunk)):
            name = " ".join(p for p in (r["first_name"] or "", r["last_name"] or "") if p)
            out[int(r["id"])] = name or r["email"]
    return out


def list_tokens(conn, workspace_id: int | None = None) -> list[dict]:
    cols = "SELECT id, name, project, workspace_id, created_at, last_used, created_by FROM tokens"
    if workspace_id is None:
        rows = conn.execute(cols + " ORDER BY id")
    else:
        rows = conn.execute(cols + " WHERE workspace_id=? ORDER BY id", (workspace_id,))
    return [dict(r) for r in rows]


# ---- content -----------------------------------------------------------
def make_page(path: str, text: str, file: str | None = None) -> Page:
    return Page(path=path, file=file or f"{path}.md", title=title_of(text, path),
                folder=path.rsplit("/", 1)[0] if "/" in path else "",
                words=len(text.split()), text=text)


def load_pages(conn, project: str) -> dict[str, Page]:
    return {r["path"]: Page(path=r["path"], file=r["file"], title=r["title"],
                            folder=r["folder"], words=r["words"], text=r["text"])
            for r in conn.execute("SELECT path, file, title, folder, words, text"
                                  " FROM pages WHERE project=?", (project,))}


# ---- history -----------------------------------------------------------
def _meta(text: str | None) -> tuple:
    if text is None:
        return None, None, ""
    return len(text), len(text.split()), version_of(text)


def _stored_form(conn, project: str, path: str, text: str | None,
                 prev: str | None) -> tuple[str | None, str | None]:
    """(text, delta) to store for a new revision of `path` whose page is now
    `text`, where `prev` is the page's text before the change (None if it did
    not exist). A delta only when the page's latest revision is exactly `prev`,
    the chain since the last whole copy is short, and the delta is smaller."""
    if text is None:
        return None, None
    if prev is None:
        return text, None
    last = conn.execute(
        "SELECT id, version, CASE WHEN text IS NULL AND delta IS NULL THEN 1 ELSE 0 END AS gone"
        " FROM revisions WHERE project=? AND path=? ORDER BY id DESC LIMIT 1",
        (project, path)).fetchone()
    if not last or last["gone"] or last["version"] != version_of(prev):
        return text, None
    chain = conn.execute(
        "SELECT COUNT(*) FROM revisions WHERE project=? AND path=? AND id > COALESCE((SELECT"
        " MAX(id) FROM revisions WHERE project=? AND path=? AND text IS NOT NULL), 0)",
        (project, path, project, path)).fetchone()[0]
    if chain >= SNAPSHOT_EVERY - 1:
        return text, None
    d = deltas.encode(deltas.make(prev, text))
    return (text, None) if len(d) >= len(text) else (None, d)


def _insert_revisions(conn, project: str, rows) -> None:
    """rows: (path, at, op, author, text, prev_text, note, agent, user_id). Each
    is stored whole or as a delta from the page's previous revision."""
    for path, at, op, author, text, prev, note, agent, user_id in rows:
        stored, d = _stored_form(conn, project, path, text, prev)
        conn.execute(
            "INSERT INTO revisions (project, path, at, op, author, text, delta, chars, words,"
            " version, note, agent, user_id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (project, path, at, op, author, stored, d, *_meta(text), note, agent, user_id))


def _baseline(conn, project: str, path: str, at: float, text: str) -> None:
    """The text a page had before its first tracked change, kept whole."""
    if not conn.execute("SELECT 1 FROM revisions WHERE project=? AND path=? LIMIT 1",
                        (project, path)).fetchone():
        _insert_revisions(conn, project, [(path, at, "baseline", "", text, None, None, None,
                                           None)])


def compact_history(conn) -> int:
    """Store history as deltas: rewrite every page that has a revision without a
    version (written before deltas, or by an older release after a rollback)
    into the current form, whole copies every SNAPSHOT_EVERY revisions and
    deltas between. Every revision's text is rebuilt first and checked after,
    so the rewrite cannot change what any version reads as. Returns the number
    of pages rewritten."""
    todo = conn.execute("SELECT DISTINCT project, path FROM revisions WHERE version IS NULL"
                        " ORDER BY project, path").fetchall()
    if not todo:
        return 0
    with LOCK, conn:
        for pr in todo:
            project, path = pr["project"], pr["path"]
            rows = conn.execute("SELECT id, text, delta FROM revisions WHERE project=? AND path=?"
                                " ORDER BY id", (project, path)).fetchall()
            texts, cur = [], None
            for r in rows:
                if r["text"] is not None:
                    cur = r["text"]
                elif r["delta"] is not None:
                    cur = deltas.apply(cur, deltas.decode(r["delta"]))
                else:
                    cur = None
                texts.append(cur)
            prev, chain = None, 0
            for r, text in zip(rows, texts):
                stored, d = text, None
                if text is not None and prev is not None and chain < SNAPSHOT_EVERY - 1:
                    enc = deltas.encode(deltas.make(prev, text))
                    if len(enc) < len(text):
                        stored, d = None, enc
                chain = chain + 1 if d is not None else 0
                conn.execute("UPDATE revisions SET text=?, delta=?, chars=?, words=?, version=?"
                             " WHERE id=?", (stored, d, *_meta(text), r["id"]))
                prev = text
            for r, text in zip(rows, texts):
                if revision_text(conn, project, path, r["id"]) != text:
                    raise RuntimeError(f"history rewrite of {project}:{path} would change"
                                       f" revision {r['id']}; nothing was written")
    return len(todo)


def revision_text(conn, project: str, path: str, rev_id: int) -> str | None:
    """The page as it was after revision `rev_id`: the latest whole copy at or
    before it, with the deltas since applied. None for a change that removed it."""
    rows = conn.execute(
        "SELECT id, text, delta FROM revisions WHERE project=? AND path=? AND id<=? AND id >="
        " COALESCE((SELECT MAX(id) FROM revisions WHERE project=? AND path=? AND id<=?"
        " AND text IS NOT NULL), 0) ORDER BY id",
        (project, path, rev_id, project, path, rev_id)).fetchall()
    if not rows or rows[-1]["id"] != rev_id:
        return None
    cur = None
    for r in rows:
        if r["text"] is not None:
            cur = r["text"]
        elif r["delta"] is not None:
            if cur is None:
                raise RuntimeError(f"revision {r['id']} of {project}:{path} has no text to"
                                   " apply its delta to")
            cur = deltas.apply(cur, deltas.decode(r["delta"]))
        else:
            cur = None
    return cur


def commit_pages(conn, project: str, pages: dict[str, Page], *, source: str,
                 author: str = "", op: str, is_push: bool = False,
                 user_id: int | None = None) -> dict:
    """Make `pages` the project's full contents, rebuild its links, and record a
    revision for every page added, changed or removed. Returns stats plus the
    changed and removed paths. Takes LOCK; callers that read before writing
    should hold it across both."""
    with LOCK:
        old = {r["path"]: (r["text"], r["updated_at"]) for r in conn.execute(
            "SELECT path, text, updated_at FROM pages WHERE project=?", (project,))}
        graph = build(pages)
        deg = graph.degree()
        now = time.time()
        changed = sorted(p for p, pg in pages.items() if p not in old or old[p][0] != pg.text)
        removed = sorted(p for p in old if p not in pages)
        stats = graph.stats()
        with conn:
            for path in changed + removed:
                if path in old:
                    _baseline(conn, project, path, old[path][1] or now, old[path][0])
            for table in ("pages", "links"):
                conn.execute(f"DELETE FROM {table} WHERE project=?", (project,))
            conn.executemany(
                "INSERT INTO pages (project, path, file, title, folder, words, degree, text,"
                " updated_at, base) VALUES (?,?,?,?,?,?,?,?,?,?)",
                [(project, p.path, p.file, p.title, p.folder, p.words, deg.get(p.path, 0),
                  p.text, old[p.path][1] if p.path in old and old[p.path][0] == p.text
                  and old[p.path][1] else now, basename(p.path))
                 for p in graph.pages.values()])
            _insert_links(conn, project, _link_rows(graph.pages, PageIndex(graph.pages)))
            _insert_revisions(conn, project, [
                (p, now, op, author, pages[p].text, old[p][0] if p in old else None, None, None,
                 user_id) for p in changed] + [
                (p, now, op, author, None, old[p][0], None, None, user_id) for p in removed])
            edited = bool(changed or removed) and not is_push
            ws_id, wiki_name = split_key(project)
            conn.execute(
                PROJECT_UPSERT,
                (project, source, now, stats["pages"], stats["links"], stats["words"],
                 stats["dangling"], now if is_push else None, now if edited else None,
                 ws_id, wiki_name))
            if is_push:
                conn.execute(
                    "INSERT INTO pushes (project, source, at, pages, links, token_name)"
                    " VALUES (?,?,?,?,?,?)",
                    (project, source, now, stats["pages"], stats["links"], author))
    return {**stats, "changed": changed, "removed": removed}


PROJECT_UPSERT = (
    "INSERT INTO projects (name, source, updated_at, pages, links, words, dangling,"
    " last_push_at, last_edit_at, workspace_id, wiki) VALUES (?,?,?,?,?,?,?,?,?,?,?)"
    " ON CONFLICT(name) DO UPDATE SET"
    " source=excluded.source, updated_at=excluded.updated_at,"
    " pages=excluded.pages, links=excluded.links, words=excluded.words,"
    " dangling=excluded.dangling,"
    " last_push_at=COALESCE(excluded.last_push_at, projects.last_push_at),"
    " last_edit_at=COALESCE(excluded.last_edit_at, projects.last_edit_at)")
CHUNK = 500


def _chunks(items):
    items = list(items)
    for i in range(0, len(items), CHUNK):
        yield items[i:i + CHUNK]


def _marks(n: int) -> str:
    return ",".join("?" * n)


def _link_rows(pages: dict[str, Page], index) -> list[tuple]:
    """(src, pos, target, rel, base, dst) for every link on `pages`."""
    return [(src, pos, target, rel, base, resolve_keys(target, rel, base, index))
            for src, page in pages.items()
            for pos, target, rel, base in page_links(src, page.text)]


def _insert_links(conn, project: str, rows: list[tuple]) -> None:
    if rows:
        conn.executemany("INSERT INTO links (project, src, pos, target, rel, base, dst)"
                         " VALUES (?,?,?,?,?,?,?)", [(project, *r) for r in rows])


def _kind(conn, name: str) -> str | None:
    """'table', 'view' or None, in either database."""
    if isinstance(conn, pg.PgConnection):
        row = conn.execute("SELECT table_type FROM information_schema.tables"
                           " WHERE table_schema = current_schema() AND table_name=?",
                           (name,)).fetchone()
        return None if not row else ("view" if row[0] == "VIEW" else "table")
    row = conn.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    return row[0] if row else None


def relink(conn, project: str) -> None:
    """Recompute a wiki's links from its stored pages, and its totals with them."""
    with LOCK, conn:
        pages = load_pages(conn, project)
        conn.execute("DELETE FROM links WHERE project=?", (project,))
        _insert_links(conn, project, _link_rows(pages, PageIndex(pages)))
        stats = build(pages).stats()
        conn.execute("UPDATE projects SET pages=?, links=?, words=?, dangling=? WHERE name=?",
                     (stats["pages"], stats["links"], stats["words"], stats["dangling"], project))


def _upgrade_links(conn) -> None:
    """Idempotent. Fill pages.base, and replace the old edges/dangling tables with
    views over `links`, computing links for every wiki the first time."""
    missing = conn.execute("SELECT project, path FROM pages WHERE base IS NULL").fetchall()
    if missing:
        with LOCK, conn:
            conn.executemany("UPDATE pages SET base=? WHERE project=? AND path=?",
                             [(basename(r["path"]), r["project"], r["path"]) for r in missing])
    conn.execute("CREATE INDEX IF NOT EXISTS pages_base ON pages(project, base)")
    if _kind(conn, "edges") == "view":
        return
    with LOCK, conn:
        for name in ("edges", "dangling"):
            if _kind(conn, name) == "table":
                conn.execute(f"DROP TABLE {name}")
        conn.execute("CREATE VIEW edges AS SELECT DISTINCT project, src, dst FROM links"
                     " WHERE dst IS NOT NULL AND dst <> src")
        conn.execute("CREATE VIEW dangling AS SELECT project, src, pos, target FROM links"
                     " WHERE dst IS NULL")
    for r in conn.execute("SELECT name FROM projects").fetchall():
        relink(conn, r["name"])


class DbIndex:
    """PageIndex answered from the pages table, for one wiki, with a cache.
    `preload` fetches many keys in two queries instead of one per link."""

    def __init__(self, conn, project: str):
        self.conn, self.project = conn, project
        self._has: dict[str, bool] = {}
        self._base: dict[str, list[str]] = {}

    def preload(self, paths=(), bases=()) -> None:
        need = sorted({p for p in paths if p and p not in self._has})
        for chunk in _chunks(need):
            found = {r[0] for r in self.conn.execute(
                f"SELECT path FROM pages WHERE project=? AND path IN ({_marks(len(chunk))})",
                (self.project, *chunk))}
            for p in chunk:
                self._has[p] = p in found
        need = sorted({b for b in bases if b not in self._base})
        for chunk in _chunks(need):
            for b in chunk:
                self._base[b] = []
            for r in self.conn.execute(
                    f"SELECT base, path FROM pages WHERE project=? AND base IN ({_marks(len(chunk))})",
                    (self.project, *chunk)):
                self._base[r[0]].append(r[1])

    def __contains__(self, path: str) -> bool:
        if not path:
            return False
        if path not in self._has:
            self.preload(paths=[path])
        return self._has[path]

    def with_base(self, base: str) -> list[str]:
        if base not in self._base:
            self.preload(bases=[base])
        return self._base[base]


class PendingIndex:
    """What link resolution sees with `changes` (path -> Page, or None for a
    removal) applied on top of the stored wiki, without writing anything. Lets a
    batch resolve links against pages created or removed earlier in the same
    batch, and lets a dry run report the links a change would leave broken."""

    def __init__(self, conn, project: str, changes: dict):
        self.db = DbIndex(conn, project)
        self.changes = changes

    def __contains__(self, path: str) -> bool:
        if path in self.changes:
            return self.changes[path] is not None
        return path in self.db

    def with_base(self, base: str) -> list[str]:
        out = [p for p in self.db.with_base(base)
               if p not in self.changes or self.changes[p] is not None]
        out += [p for p, pg in self.changes.items()
                if pg is not None and basename(p) == base and p not in out]
        return out


class PageSet(MutableMapping):
    """A wiki's pages as a dict, read from the database only as they are asked for.
    Assignments and deletions are recorded in `changes` (path -> Page, or None for
    a removal) for apply_changes; nothing is written here."""

    def __init__(self, conn, project: str):
        self.conn, self.project = conn, project
        self.changes: dict[str, Page | None] = {}
        self._cache: dict[str, Page | None] = {}

    def _stored(self, path: str) -> Page | None:
        if path not in self._cache:
            r = self.conn.execute("SELECT path, file, title, folder, words, text FROM pages"
                                  " WHERE project=? AND path=?", (self.project, path)).fetchone()
            self._cache[path] = Page(path=r["path"], file=r["file"], title=r["title"],
                                     folder=r["folder"], words=r["words"], text=r["text"]) \
                if r else None
        return self._cache[path]

    def __getitem__(self, path: str) -> Page:
        page = self.changes[path] if path in self.changes else self._stored(path)
        if page is None:
            raise KeyError(path)
        return page

    def __contains__(self, path) -> bool:
        if path in self.changes:
            return self.changes[path] is not None
        return self._stored(path) is not None

    def __setitem__(self, path: str, page: Page) -> None:
        self.changes[path] = page

    def __delitem__(self, path: str) -> None:
        if path not in self:
            raise KeyError(path)
        self.changes[path] = None

    def _paths(self) -> list[str]:
        stored = [r[0] for r in self.conn.execute(
            "SELECT path FROM pages WHERE project=? ORDER BY path", (self.project,))]
        out = [p for p in stored if self.changes.get(p, True) is not None]
        return out + sorted(p for p, v in self.changes.items() if v is not None and p not in stored)

    def __iter__(self):
        return iter(self._paths())

    def __len__(self) -> int:
        return len(self._paths())

    def diff(self) -> tuple[list[str], list[str]]:
        """(changed, removed) paths, as apply_changes would count them."""
        changed = sorted(p for p, pg in self.changes.items() if pg is not None
                         and (self._stored(p) is None or self._stored(p).text != pg.text))
        removed = sorted(p for p, pg in self.changes.items()
                         if pg is None and self._stored(p) is not None)
        return changed, removed


def _edge_and_dangling_counts(conn, project: str, sources) -> tuple[int, int]:
    """Distinct edges and broken links whose source is in `sources`."""
    # Counted here from the sources' own rows: with the conditions on dst in SQL,
    # SQLite's planner walked the whole wiki's links by dst instead.
    edges: set = set()
    dangling = 0
    for chunk in _chunks(sorted(sources)):
        for src, dst in conn.execute(
                f"SELECT src, dst FROM links WHERE project=? AND src IN ({_marks(len(chunk))})",
                (project, *chunk)):
            if dst is None:
                dangling += 1
            elif dst != src:
                edges.add((src, dst))
    return len(edges), dangling


def apply_changes(conn, project: str, changes: dict[str, Page | None], *, source: str,
                  author: str = "", op: str | dict[str, str], note: str | None = None,
                  agent: str | None = None, user_id: int | None = None) -> dict:
    """Write only what a change touches: the pages named in `changes` (a Page, or
    None to remove it), their links, and the links elsewhere that a created,
    removed or moved page can redirect. Totals move by the difference. The result
    is the same as commit_pages with the whole wiki (the tests check that against a
    full rebuild after thousands of random changes); the cost no longer grows with
    the wiki's size. Returns stats plus the changed and removed paths. `op` is
    one op for every page, or a path -> op map (a batch); `note` goes on every
    revision written, and so do `agent` and `user_id`."""
    def op_for(path: str) -> str:
        return op if isinstance(op, str) else op.get(path, "batch")

    note = (note or "").strip() or None
    agent = (agent or "").strip() or None
    with LOCK:
        now = time.time()
        old: dict[str, tuple] = {}
        for chunk in _chunks(changes):
            for r in conn.execute(
                    f"SELECT path, text, updated_at, words FROM pages WHERE project=?"
                    f" AND path IN ({_marks(len(chunk))})", (project, *chunk)):
                old[r["path"]] = (r["text"], r["updated_at"], r["words"])
        changed = sorted(p for p, pg in changes.items()
                         if pg is not None and (p not in old or old[p][0] != pg.text))
        removed = sorted(p for p, pg in changes.items() if pg is None and p in old)
        touched = set(changed) | set(removed)
        created = [p for p in changed if p not in old]
        ws_id, wiki_name = split_key(project)
        with conn:
            # Links elsewhere that a page appearing or disappearing can redirect.
            moved = set(created) | set(removed)
            redirect: list = []
            if moved:
                for chunk in _chunks(moved):
                    m = _marks(len(chunk))
                    bases = sorted({basename(p) for p in chunk})
                    redirect += conn.execute(
                        f"SELECT src, pos, target, rel, base, dst FROM links WHERE project=?"
                        f" AND (rel IN ({m}) OR base IN ({_marks(len(bases))}))",
                        (project, *chunk, *bases)).fetchall()
                seen: set = set()
                redirect = [r for r in redirect if r["src"] not in touched
                            and (r["src"], r["pos"]) not in seen
                            and not seen.add((r["src"], r["pos"]))]
            sources = touched | {r["src"] for r in redirect}
            edges_before, dangling_before = _edge_and_dangling_counts(conn, project, sources)

            for path in changed + removed:
                if path in old:
                    _baseline(conn, project, path, old[path][1] or now, old[path][0])
            _insert_revisions(conn, project, [
                (p, now, op_for(p), author, changes[p].text, old[p][0] if p in old else None,
                 note, agent, user_id) for p in changed] + [
                (p, now, op_for(p), author, None, old[p][0], note, agent, user_id)
                for p in removed])
            for chunk in _chunks(removed):
                m = _marks(len(chunk))
                conn.execute(f"DELETE FROM pages WHERE project=? AND path IN ({m})", (project, *chunk))
            for chunk in _chunks(touched):
                m = _marks(len(chunk))
                conn.execute(f"DELETE FROM links WHERE project=? AND src IN ({m})", (project, *chunk))
            conn.executemany(
                "INSERT INTO pages (project, path, file, title, folder, words, degree, text,"
                " updated_at, base) VALUES (?,?,?,?,?,?,0,?,?,?)"
                " ON CONFLICT(project, path) DO UPDATE SET file=excluded.file,"
                " title=excluded.title, folder=excluded.folder, words=excluded.words,"
                " text=excluded.text, updated_at=excluded.updated_at, base=excluded.base",
                [(project, p, changes[p].file, changes[p].title, changes[p].folder,
                  changes[p].words, changes[p].text, now, basename(p)) for p in changed])

            # Resolve against the wiki as it is now, in a handful of queries.
            new_links = [(p, *link) for p in changed for link in page_links(p, changes[p].text)]
            index = DbIndex(conn, project)
            index.preload(
                paths=[x for r in new_links for x in (r[2], r[3])]
                + [x for r in redirect for x in (r["target"], r["rel"])],
                bases=[r[4] for r in new_links] + [r["base"] for r in redirect])
            _insert_links(conn, project, [(*r, resolve_keys(r[2], r[3], r[4], index))
                                          for r in new_links])
            moves = []
            for r in redirect:
                dst = resolve_keys(r["target"], r["rel"], r["base"], index)
                if dst != r["dst"]:
                    moves.append((dst, project, r["src"], r["pos"]))
            if moves:
                conn.executemany("UPDATE links SET dst=? WHERE project=? AND src=? AND pos=?",
                                 moves)
            edges_after, dangling_after = _edge_and_dangling_counts(conn, project, sources)

            proj = conn.execute("SELECT pages, links, words, dangling FROM projects WHERE name=?",
                                (project,)).fetchone()
            if proj is None or any(proj[k] is None for k in ("pages", "links", "words", "dangling")):
                stats = build(load_pages(conn, project)).stats()
                totals = (stats["pages"], stats["links"], stats["words"], stats["dangling"])
            else:
                words = sum(changes[p].words for p in changed) \
                    - sum(old[p][2] for p in touched if p in old)
                totals = (proj["pages"] + len(created) - len(removed),
                          proj["links"] + edges_after - edges_before,
                          proj["words"] + words,
                          proj["dangling"] + dangling_after - dangling_before)
            conn.execute(PROJECT_UPSERT, (project, source, now, *totals, None,
                                          now if touched else None, ws_id, wiki_name))
    return {"pages": totals[0], "links": totals[1], "words": totals[2], "dangling": totals[3],
            "changed": changed, "removed": removed}


def project_exists(conn, k: str) -> bool:
    return conn.execute("SELECT 1 FROM projects WHERE name=?", (k,)).fetchone() is not None


def export_zip(conn, project: str) -> bytes:
    """Every page of a wiki as `<path>.md` in a zip (deflated), dated by each
    page's last change. Pages only: files download one at a time."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for r in conn.execute("SELECT path, text, updated_at FROM pages WHERE project=?"
                              " ORDER BY path", (project,)):
            info = zipfile.ZipInfo(r["path"] + ".md", time.gmtime(max(r["updated_at"] or 0,
                                                                       315532800))[:6])
            info.compress_type = zipfile.ZIP_DEFLATED
            z.writestr(info, r["text"])
    return buf.getvalue()


def wiki_stats(conn, k: str) -> dict:
    """A wiki's page, link and word counts and last change, zeros for none."""
    row = conn.execute("SELECT pages, links, words, updated_at FROM projects WHERE name=?",
                       (k,)).fetchone()
    return {c: (row[c] if row else 0) or 0 for c in ("pages", "links", "words", "updated_at")}


def projects(conn, workspace_id: int | None = None) -> list[dict]:
    """Wikis, by display name (`name`) with their internal `key`. With a
    workspace, only that workspace's wikis."""
    sql = ("SELECT COALESCE(wiki, name) AS name, name AS key, workspace_id, source, updated_at,"
           " pages, links, words, last_push_at, last_edit_at FROM projects")
    if workspace_id is None:
        rows = conn.execute(sql + " ORDER BY name")
    else:
        rows = conn.execute(sql + " WHERE workspace_id=? ORDER BY COALESCE(wiki, name)",
                            (workspace_id,))
    return [dict(r) for r in rows]


def graph(conn, project: str) -> dict:
    rows = conn.execute(
        "SELECT path, title, folder, words FROM pages WHERE project=?"
        " ORDER BY path", (project,)).fetchall()
    edges = conn.execute("SELECT src, dst FROM edges WHERE project=? ORDER BY src, dst",
                         (project,)).fetchall()
    dang = conn.execute("SELECT src, target FROM dangling WHERE project=? ORDER BY src, pos",
                        (project,)).fetchall()
    inbound: dict[str, int] = {}
    degree: dict[str, int] = {}
    for e in edges:
        inbound[e["dst"]] = inbound.get(e["dst"], 0) + 1
        degree[e["src"]] = degree.get(e["src"], 0) + 1
        degree[e["dst"]] = degree.get(e["dst"], 0) + 1
    # Degree is counted here rather than stored: a stored count would have to be
    # updated on every page a write's links touch.
    pages = [{**dict(r), "degree": degree.get(r["path"], 0)} for r in rows]
    return {
        "project": project,
        "stats": {
            "pages": len(pages),
            "links": len(edges),
            "orphans": sum(1 for p in pages if p["degree"] == 0),
            "unreferenced": sum(1 for p in pages if inbound.get(p["path"], 0) == 0),
            "dangling": len(dang),
            "words": sum(p["words"] for p in pages),
        },
        "nodes": [{"id": p["path"], "title": p["title"], "folder": p["folder"],
                   "words": p["words"], "degree": p["degree"]} for p in pages],
        "links": [{"source": e["src"], "target": e["dst"]} for e in edges],
        "dangling": [{"source": d["src"], "target": d["target"]} for d in dang],
    }


def note(conn, project: str, path: str):
    row = conn.execute("SELECT path, title, folder, words, text, updated_at FROM pages"
                       " WHERE project=? AND path=?", (project, path)).fetchone()
    return dict(row) if row else None


def _folder_where(folder: str) -> tuple[str, tuple]:
    """The WHERE clause, after project, that keeps one folder's pages (or all)."""
    folder = folder.strip("/")
    return (" AND (? = '' OR path LIKE ? ESCAPE '\\')",
            (folder, folder.replace("\\", "\\\\").replace("%", "\\%")
             .replace("_", "\\_") + "/%"))


def page_list(conn, project: str, folder: str = "", limit: int | None = None,
              offset: int = 0) -> list[dict]:
    """A wiki's pages (or one folder's) in path order, each with its one-line
    description (parse.description_of) and its frontmatter status (okf.status),
    read from the top of the page only.
    limit and offset take one slice of that order."""
    from .. import okf
    from ..parse import DESCRIPTION_SOURCE, description_of
    where, args = _folder_where(folder)
    sql = (f"SELECT path, title, words, updated_at, substr(text, 1, {int(DESCRIPTION_SOURCE)}) AS head"
           " FROM pages WHERE project=?" + where + " ORDER BY path")
    args = (project, *args)
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args = (*args, int(limit), int(offset))
    rows = conn.execute(sql, args).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        head = d.pop("head") or ""
        d["description"] = description_of(head)
        d["status"] = okf.status(okf.fields(head)) if head.startswith("---") else None
        out.append(d)
    return out


def page_paths(conn, project: str, folder: str = "") -> list[str]:
    """Every page path in a wiki (or one folder), in path order: the count and the
    folder breakdown list_pages gives when one call does not show everything."""
    where, args = _folder_where(folder)
    return [r["path"] for r in conn.execute(
        "SELECT path FROM pages WHERE project=?" + where + " ORDER BY path",
        (project, *args)).fetchall()]


def search(conn, project: str, q: str, limit: int = 40) -> list[dict]:
    """Pages for the web app's search box and read_page's "similar" hint: the
    same word search as the MCP tool (search.py), best first, pages with only
    some of the words included when none has them all."""
    from ..search import search_pages
    pages = load_pages(conn, project)
    found = search_pages(pages.values(), q, per_page=1)["results"][:limit]
    return [{"path": h["path"], "title": h["title"], "folder": pages[h["path"]].folder,
             "words": pages[h["path"]].words} for h in found]


def revisions(conn, project: str, path: str, limit: int = 20,
              agent: str | None = None) -> list[dict]:
    """A page's revisions, newest first, without their text (revision() reads
    one); with `agent`, only that agent's (case-insensitive)."""
    sql = ("SELECT id, at, op, author, agent, note, user_id, chars, words, version,"
           " CASE WHEN text IS NULL AND delta IS NULL THEN 1 ELSE 0 END AS deleted"
           " FROM revisions WHERE project=? AND path=?")
    args: list = [project, path]
    if agent:
        sql += " AND lower(agent)=lower(?)"
        args.append(agent)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]


def recent_revisions(conn, project: str, limit: int = 50, agent: str | None = None) -> list[dict]:
    """The wiki's latest changes across every page, newest first, without page text;
    with `agent`, only that agent's. Baseline rows are left out: they record text
    from before tracking, not a change."""
    sql = ("SELECT id, path, at, op, author, agent, note, user_id, chars, CASE WHEN text IS NULL"
           " AND delta IS NULL THEN 1 ELSE 0 END AS deleted FROM revisions"
           " WHERE project=? AND op <> 'baseline'")
    args: list = [project]
    if agent:
        sql += " AND lower(agent)=lower(?)"
        args.append(agent)
    return [dict(r) for r in conn.execute(sql + " ORDER BY id DESC LIMIT ?", (*args, limit))]


def restore_point(conn, project: str, path: str) -> int | None:
    """After `path` was deleted: its newest revision that still holds text, the one
    to restore it from. None when the page is not deleted or has no such revision."""
    last = conn.execute(
        "SELECT id, CASE WHEN text IS NULL AND delta IS NULL THEN 1 ELSE 0 END AS gone"
        " FROM revisions WHERE project=? AND path=? ORDER BY id DESC LIMIT 1",
        (project, path)).fetchone()
    if not last or not last["gone"]:
        return None
    row = conn.execute(
        "SELECT id FROM revisions WHERE project=? AND path=? AND id < ?"
        " AND (text IS NOT NULL OR delta IS NOT NULL) ORDER BY id DESC LIMIT 1",
        (project, path, last["id"])).fetchone()
    return row["id"] if row else None


def revision(conn, project: str, rev_id: int):
    """One revision with the page's text after it (None if it removed the page)."""
    row = conn.execute("SELECT id, path, at, op, author, agent, note, user_id, text, delta"
                       " FROM revisions WHERE project=? AND id=?", (project, rev_id)).fetchone()
    if not row:
        return None
    out = dict(row)
    if out["text"] is None and out.pop("delta") is not None:
        out["text"] = revision_text(conn, project, out["path"], rev_id)
    out.pop("delta", None)
    return out


def history(conn, project: str, limit: int = 50) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT at, source, pages, links, token_name FROM pushes WHERE project=?"
        " ORDER BY at DESC LIMIT ?", (project, limit))]
