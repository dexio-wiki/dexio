"""Sharing a wiki, a folder or a page, the way Google Docs shares a document
(Forrest, 2026-09-28: "i want the ability to share entire wikis, folders, and
articles. it should be like gdocs where you can share by email, or you can make
it publically viewable").

What a share gives is viewing, in the web app. Editing in Dexio is what members
and their agents do, so "can edit" on the whole wiki is a member invite (and a
seat on a paid plan); below the whole wiki there is only viewing.

- A share names a target: the whole wiki (kind "wiki", path ""), a folder and
  everything under it at any depth, now or later ("folder"), or one page
  ("page"), which follows the page when it is moved (follow_move).
- By email: the person gets a message with a link (/s/<code>). Only the account
  the address belongs to can take the share with it (Forrest, 2026-09-28: "the
  link should not be claim-able by anyone other than the user it was intended
  for"): the account signed up with that address, for which the link arriving
  in its inbox is the proof a password sign-up never gave, or one Google or
  GitHub verified the address for. Any other account, the sharer's included,
  is turned away and the link keeps working for the right one. An account
  signed in with Google or GitHub sees shares to that address without the link.
- Anyone with the link: the target's own address opens without signing in.
  Such pages are open to search engines too (Forrest, 2026-09-28: "public wiki
  should bring in search traffic"): the app gives them titles, descriptions and
  canonical addresses, lists them in /sitemap.xml, and marks outside links on
  them ugc nofollow so a spam page gets no link value from our domain. Pages
  shared only by email stay noindex.
- Grants add up and the broadest wins, as in Drive: a page inside a public
  folder is public, whatever its own row says.

A guest sees the shared pages, the links among them and the files they show or
that sit in a shared folder; links to anything else read as plain text. Page
history, the changes feed, export and everything under Settings stay with
members. Members (any role) share; only owners invite editors, as in Settings.
"""
from __future__ import annotations

import posixpath
import re
import secrets
import time
from dataclasses import dataclass, field
from urllib.parse import unquote

from . import db

KINDS = ("wiki", "folder", "page")
CODE_PREFIX = "dxs_"


class ShareError(ValueError):
    """A reason a person can act on."""


class WrongAccount(ShareError):
    """A share link opened by an account the address does not belong to."""

    def __init__(self, sent_to: str):
        self.sent_to = masked(sent_to)
        super().__init__(f"This link was sent to {self.sent_to}. Sign in with that address"
                         " to open it.")


def masked(email: str) -> str:
    """An address as shown to someone it may not belong to: enough for its owner
    to recognise (fo•••@gmail.com), not enough to write to."""
    local, _, domain = (email or "").partition("@")
    return f"{local[:2 if len(local) > 4 else 1]}•••@{domain}"


@dataclass
class Access:
    """What one caller may see of one workspace's wiki. role: owner or member
    (everything, and they can edit and share), guest (signed in, shared with
    them), public (anyone with the link), or None (nothing)."""
    workspace_id: int
    role: str | None
    whole: bool = False
    folders: frozenset = field(default_factory=frozenset)
    pages: frozenset = field(default_factory=frozenset)

    @property
    def member(self) -> bool:
        return self.role in ("owner", "member")

    @property
    def any(self) -> bool:
        return self.member or self.whole or bool(self.folders) or bool(self.pages)

    def sees(self, path: str) -> bool:
        """A page, or a file by its folder (sees_file also counts what pages show)."""
        if self.member or self.whole:
            return True
        return path in self.pages or any(path.startswith(f + "/") for f in self.folders)


# ---- targets --------------------------------------------------------------
def norm_target(conn, ws_id: int, kind: str, path: str) -> tuple[str, str]:
    """(kind, path) as stored, for something that exists: the wiki, a folder
    with at least one page under it, or a page."""
    kind = (kind or "").strip().lower()
    if kind not in KINDS:
        raise ShareError("Share the wiki, a folder or a page.")
    if kind == "wiki":
        return kind, ""
    p = str(path or "").strip().replace("\\", "/").strip("/")
    if p.endswith(".md") and kind == "page":
        p = p[:-3]
    if not p or any(part in ("", ".", "..") for part in p.split("/")):
        raise ShareError(f"No {kind} called {path!r}.")
    k = db.wiki_key(ws_id)
    if kind == "page" and not db.note(conn, k, p):
        raise ShareError(f"There is no page {p}.")
    if kind == "folder" and not db.page_paths(conn, k, p):
        raise ShareError(f"There is no folder {p}.")
    return kind, p


def covers(kind: str, path: str, target_kind: str, target_path: str) -> bool:
    """Whether a share on (kind, path) reaches (target_kind, target_path)."""
    if kind == "wiki":
        return True
    if kind == "page":
        return target_kind == "page" and target_path == path
    # a folder reaches itself, its subfolders and every page under it
    return target_kind != "wiki" and (target_path == path or target_path.startswith(path + "/"))


def title_of(conn, ws_id: int, kind: str, path: str) -> str:
    """What a person calls the target: the workspace's name, the folder's
    path, the page's title."""
    if kind == "wiki":
        return (db.workspace(conn, ws_id) or {}).get("name") or "the wiki"
    if kind == "folder":
        return path
    row = db.note(conn, db.wiki_key(ws_id), path)
    return (row or {}).get("title") or path


# ---- who sees what -----------------------------------------------------------
def verified_emails(conn, user_id: int | None) -> set[str]:
    """Addresses a sign-in provider (Google, GitHub) has verified for the account."""
    if not user_id:
        return set()
    return {(r["email"] or "").strip().lower() for r in conn.execute(
        "SELECT email FROM identities WHERE user_id=?", (user_id,))}


def addresses(conn, user_id: int | None) -> set[str]:
    """The addresses a share link may be claimed with: the account's own
    sign-in address, and those Google or GitHub verified for it."""
    if not user_id:
        return set()
    row = conn.execute("SELECT email FROM users WHERE id=?", (user_id,)).fetchone()
    own = {(row["email"] or "").strip().lower()} if row else set()
    return (own | verified_emails(conn, user_id)) - {""}


def _rows_for(conn, ws_id: int, user_id: int | None) -> list:
    rows = conn.execute("SELECT * FROM shares WHERE workspace_id=?", (ws_id,)).fetchall()
    mine = verified_emails(conn, user_id)
    out = []
    for r in rows:
        if r["email"] is None and r["user_id"] is None:
            out.append(r)                              # anyone with the link
        elif user_id and (r["user_id"] == user_id
                          or (r["user_id"] is None and r["email"] in mine)):
            out.append(r)
    return out


def access(conn, ws_id: int, user_id: int | None) -> Access:
    """What `user_id` (None: not signed in) may see of the workspace's wiki."""
    if user_id:
        role = db.role_in(conn, ws_id, user_id)
        if role:
            return Access(ws_id, role, whole=True)
    rows = _rows_for(conn, ws_id, user_id)
    if not rows:
        return Access(ws_id, None)
    personal = any(r["email"] is not None for r in rows)
    return Access(ws_id, "guest" if personal else "public",
                  whole=any(r["kind"] == "wiki" for r in rows),
                  folders=frozenset(r["path"] for r in rows if r["kind"] == "folder"),
                  pages=frozenset(r["path"] for r in rows if r["kind"] == "page"))


def shared_with(conn, user_id: int) -> list[dict]:
    """Workspaces that share something with this account and that it is not a
    member of: [{id, handle, name}], for the workspace menu's Shared with you."""
    mine = verified_emails(conn, user_id)
    ids: set[int] = set()
    for r in conn.execute("SELECT workspace_id, email, user_id FROM shares"
                          " WHERE email IS NOT NULL").fetchall():
        if r["user_id"] == user_id or (r["user_id"] is None and r["email"] in mine):
            ids.add(int(r["workspace_id"]))
    member_of = {w["id"] for w in db.workspaces_for_user(conn, user_id)}
    out = []
    for ws in sorted(ids - member_of):
        w = db.workspace(conn, ws)
        if w:
            out.append({"id": ws, "handle": w["handle"], "name": w["name"]})
    return out


SITEMAP_MAX = 50000   # the sitemap protocol's limit for one file


def public_pages(conn, limit: int = SITEMAP_MAX) -> list[tuple[str, str, float]]:
    """Every published page, across all workspaces: (workspace handle, page path,
    last change), for /sitemap.xml. Each workspace with something published also
    gets its own address, path "". Only what is published (listed on dexio.wiki)
    is for search engines; what is open to anyone with the link is not (Forrest,
    2026-10-01: "Anyone with the link (stop google from indexing shared wikis)")."""
    out: list[tuple[str, str, float]] = []
    seen: set[tuple[str, str]] = set()
    rows = conn.execute("SELECT s.workspace_id, s.kind, s.path, w.handle FROM shares s"
                        " JOIN workspaces w ON w.id = s.workspace_id"
                        " WHERE s.email IS NULL AND s.user_id IS NULL AND s.listed_at IS NOT NULL"
                        " ORDER BY s.workspace_id, s.kind, s.path").fetchall()
    for r in rows:
        k = db.wiki_key(r["workspace_id"])
        if r["kind"] == "wiki":
            found = conn.execute("SELECT path, updated_at FROM pages WHERE project=?"
                                 " ORDER BY path", (k,)).fetchall()
        elif r["kind"] == "folder":
            found = conn.execute("SELECT path, updated_at FROM pages WHERE project=? AND path"
                                 " LIKE ? ESCAPE '\\' ORDER BY path",
                                 (k, _like_prefix(r["path"]))).fetchall()
        else:
            found = conn.execute("SELECT path, updated_at FROM pages WHERE project=? AND"
                                 " path=?", (k, r["path"])).fetchall()
        if not found:
            continue
        home = (r["handle"], "")
        if home not in seen:
            seen.add(home)
            out.append((r["handle"], "", max(p["updated_at"] or 0 for p in found)))
        for p in found:
            if (r["handle"], p["path"]) not in seen:
                seen.add((r["handle"], p["path"]))
                out.append((r["handle"], p["path"], p["updated_at"] or 0))
        if len(out) >= limit:
            break
    return out[:limit]


# ---- files a guest may open ------------------------------------------------------
_WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
_MDLINK = re.compile(r"\]\(([^)\s]+)\)")


def _file_targets(text: str) -> list[str]:
    from ..parse import is_file_link, mask_code
    body = mask_code(text or "")
    out = [m.split("|", 1)[0] for m in _WIKILINK.findall(body)]
    out += [m for m in _MDLINK.findall(body) if "://" not in m and not m.startswith("mailto:")]
    return [t.split("#", 1)[0].strip() for t in out if is_file_link(t)]


def _resolve_file(target: str, source: str, paths: set[str]) -> str | None:
    """The same order the web view resolves a file link in (graph.js resolveFile):
    exact path, then relative to the linking page, then a unique file name."""
    t = target.replace("&amp;", "&")
    t = t[2:] if t.startswith("./") else t
    t = unquote(t.lstrip("/"))
    if t in paths:
        return t
    if "/" in source:
        rel = posixpath.normpath(posixpath.join(posixpath.dirname(source), t))
        if rel in paths:
            return rel
    base = t.rsplit("/", 1)[-1]
    hits = [p for p in paths if p.rsplit("/", 1)[-1] == base]
    return hits[0] if len(hits) == 1 else None


def visible_files(conn, k: str, acc: Access, all_files: list[dict]) -> list[dict]:
    """The wiki's files this caller may open: those in a shared folder, and
    those a page they can see links to or shows."""
    if acc.member or acc.whole:
        return all_files
    paths = {f["path"] for f in all_files}
    keep = {p for p in paths if acc.sees(p)}
    if paths - keep:
        for r in _visible_page_texts(conn, k, acc):
            for t in _file_targets(r["text"]):
                hit = _resolve_file(t, r["path"], paths)
                if hit:
                    keep.add(hit)
    return [f for f in all_files if f["path"] in keep]


def _visible_page_texts(conn, k: str, acc: Access) -> list:
    rows = []
    for f in sorted(acc.folders):
        rows += conn.execute("SELECT path, text FROM pages WHERE project=? AND path LIKE ?"
                             " ESCAPE '\\'", (k, _like_prefix(f))).fetchall()
    for p in sorted(acc.pages):
        rows += conn.execute("SELECT path, text FROM pages WHERE project=? AND path=?",
                             (k, p)).fetchall()
    return rows


def _like_prefix(folder: str) -> str:
    return folder.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "/%"


def restrict_graph(data: dict, acc: Access) -> dict:
    """The graph API's answer cut down to what a guest may see. Counts are
    recomputed from what is left, so nothing about the rest shows."""
    if acc.member or acc.whole:
        return data
    nodes = [n for n in data["nodes"] if acc.sees(n["id"])]
    ids = {n["id"] for n in nodes}
    links = [e for e in data["links"] if e["source"] in ids and e["target"] in ids]
    degree: dict[str, int] = {}
    inbound: dict[str, int] = {}
    for e in links:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
        inbound[e["target"]] = inbound.get(e["target"], 0) + 1
    nodes = [{**n, "degree": degree.get(n["id"], 0)} for n in nodes]
    dangling = [d for d in data["dangling"] if d["source"] in ids]
    return {**data, "nodes": nodes, "links": links, "dangling": dangling, "stats": {
        "pages": len(nodes), "links": len(links),
        "orphans": sum(1 for n in nodes if not n["degree"]),
        "unreferenced": sum(1 for n in nodes if not inbound.get(n["id"])),
        "dangling": len(dangling), "words": sum(n["words"] for n in nodes)}}


# ---- changing shares -----------------------------------------------------------
def _hash(code: str) -> str:
    return db.hash_token(code or "")


def share_with(conn, ws_id: int, kind: str, path: str, email: str, by: int) -> dict:
    """Share with one person by email. Returns {id, code, new}: code is the link's
    secret to email (None when they already opened an earlier link, which keeps
    working). Sharing the same thing with the same address again makes a fresh
    link and retires the old one."""
    email = (email or "").strip().lower()
    if "@" not in email or len(email) > 254 or any(c.isspace() for c in email):
        raise ShareError("Enter an email address.")
    kind, path = norm_target(conn, ws_id, kind, path)
    code = CODE_PREFIX + secrets.token_urlsafe(24)
    with db.LOCK, conn:
        row = conn.execute("SELECT * FROM shares WHERE workspace_id=? AND kind=? AND path=?"
                           " AND email=?", (ws_id, kind, path, email)).fetchone()
        if row and row["user_id"]:
            return {"id": row["id"], "code": None, "new": False}
        if row:
            conn.execute("UPDATE shares SET code_hash=? WHERE id=?", (_hash(code), row["id"]))
            return {"id": row["id"], "code": code, "new": False}
        cur = conn.execute("INSERT INTO shares (workspace_id, kind, path, email, code_hash,"
                           " created_by, created_at) VALUES (?,?,?,?,?,?,?)",
                           (ws_id, kind, path, email, _hash(code), by, time.time()))
        return {"id": int(cur.lastrowid), "code": code, "new": True}


def set_public(conn, ws_id: int, kind: str, path: str, on: bool, by: int) -> bool:
    """Open the target to anyone with the link, or close it again. Returns
    whether it is open now (on its own; a parent can still open it). Closing it
    also takes it out of the directory; opening what is already open changes
    nothing, so a listing survives it."""
    kind, path = norm_target(conn, ws_id, kind, path)
    with db.LOCK, conn:
        have = conn.execute("SELECT id FROM shares WHERE workspace_id=? AND kind=? AND path=?"
                            " AND email IS NULL AND user_id IS NULL",
                            (ws_id, kind, path)).fetchall()
        if on and len(have) == 1:
            return on
        conn.execute("DELETE FROM shares WHERE workspace_id=? AND kind=? AND path=?"
                     " AND email IS NULL AND user_id IS NULL", (ws_id, kind, path))
        if on:
            conn.execute("INSERT INTO shares (workspace_id, kind, path, created_by, created_at)"
                         " VALUES (?,?,?,?,?)", (ws_id, kind, path, by, time.time()))
    return on


# ---- the directory on dexio.wiki --------------------------------------------
# Forrest, 2026-10-01: "i'd rather have users opt in to publish their wiki to the
# dexio website when they share publically". Something public can be listed in
# the directory at dexio.wiki/wikis, where anyone can find it and make a copy of
# it into a workspace of their own (copies.py). Listing is a second, separate
# choice in the Share dialog, off until its owner turns it on: making something
# public lets people with the link read it; listing invites strangers to copy it.
# Dexio's own templates are listed the same way, from Dexio's own workspaces.
def _own_public(conn, ws_id: int, kind: str, path: str):
    return conn.execute("SELECT * FROM shares WHERE workspace_id=? AND kind=? AND path=?"
                        " AND email IS NULL AND user_id IS NULL ORDER BY id",
                        (ws_id, kind, path)).fetchone()


TITLE_MAX, DESCRIPTION_MAX = 80, 300


def _field(value, limit: int, what: str, required: bool) -> str | None:
    """A listing field as stored: None keeps what is there; text is trimmed to one
    line and checked."""
    if value is None:
        return None
    text = " ".join(str(value).split())
    if required and not text:
        raise ShareError(f"Give it {what}.")
    if len(text) > limit:
        raise ShareError(f"Keep {what} to {limit} characters.")
    return text


# ---- the publisher name --------------------------------------------------------
# Forrest, 2026-10-01: "Can we actually have a publisher name field in the settings?
# I'd imagine that it would be a workspace setting", then "It should also be
# unique". What a workspace publishes shows on dexio.wiki as by its publisher name
# (it replaced the lister's own name the same day), which is a handle with a page of
# its own there (db.PUBLISHER_RE: no spaces, the same day). Owners set it under
# Settings, General, or in the Publish dialog the first time; no two workspaces can
# have the same one (db.publisher_key). Names that would pass for Dexio itself, or
# for a page of the site, are kept for Dexio's own workspaces.
PUBLISHER_MIN, PUBLISHER_MAX = 2, 39
RESERVED_PUBLISHERS = {"dexio", "dexiowiki", "dexioteam", "dexioofficial", "dexiosupport",
                       "dexiohq", "admin", "administrator", "support", "staff", "moderator",
                       "official", "help", "api", "wikis", "templates", "new", "about",
                       "settings", "search", "explore"}
NO_PUBLISHER = ("This workspace needs a publisher name before it can publish. An owner sets"
                " it in Settings, General.")


def official_workspaces() -> set[str]:
    """Handles of Dexio's own workspaces, which may use the reserved names:
    DEXIO_OFFICIAL_WORKSPACES (comma-separated), else Dexio's on app.dexio.wiki."""
    import os
    raw = os.environ.get("DEXIO_OFFICIAL_WORKSPACES", "ccmrwyaz")
    return {h.strip() for h in raw.split(",") if h.strip()}


def publisher_key(name: str) -> str:
    return db.publisher_key(name)


def publisher_slug(name: str) -> str:
    """The publisher's page on dexio.wiki: /wikis/<this>/."""
    return str(name or "").lower()


def publisher_of(conn, ws_id: int) -> str:
    row = conn.execute("SELECT publisher_name FROM workspaces WHERE id=?", (ws_id,)).fetchone()
    return (row["publisher_name"] or "") if row else ""


def set_publisher(conn, ws_id: int, name) -> str:
    """Give a workspace its publisher name. ShareError says why one cannot be had:
    its length or characters, reserved, or another workspace has it."""
    text = str(name or "").strip()
    if any(ch.isspace() for ch in text):
        raise ShareError("No spaces in a publisher name: letters, numbers and hyphens, like"
                         " wrenfield-roasters.")
    if len(text) < PUBLISHER_MIN or len(text) > PUBLISHER_MAX:
        raise ShareError(f"A publisher name is {PUBLISHER_MIN} to {PUBLISHER_MAX} characters.")
    if not db.PUBLISHER_RE.match(text):
        raise ShareError("A publisher name has letters, numbers and single hyphens, and starts"
                         " and ends with a letter or number.")
    key = publisher_key(text)
    ws = db.workspace(conn, ws_id) or {}
    if key in RESERVED_PUBLISHERS and ws.get("handle") not in official_workspaces():
        raise ShareError(f"\u201c{text}\u201d is kept for Dexio itself. Pick another.")
    with db.LOCK, conn:
        taken = conn.execute("SELECT id FROM workspaces WHERE publisher_key=? AND id<>?",
                             (key, ws_id)).fetchone()
        if taken:
            raise ShareError(f"Another workspace publishes as \u201c{text}\u201d. Pick another.")
        try:
            conn.execute("UPDATE workspaces SET publisher_name=?, publisher_key=? WHERE id=?",
                         (text, key, ws_id))
        except Exception as e:                   # the unique index, in a race
            if "unique" in str(e).lower():
                raise ShareError(f"Another workspace publishes as \u201c{text}\u201d."
                                 " Pick another.") from None
            raise
    return text


def set_listed(conn, ws_id: int, kind: str, path: str, on: bool, *, by: int | None = None,
               title=None, description=None, author=None) -> bool:
    """List a public target in the directory, or take it out. Only something
    public on its own can be listed (not a page that is public because its
    folder is: list the folder). Listing takes the name and description the
    directory shows (Forrest, 2026-10-01); one left out keeps what it was, or
    falls back to the wiki's own (listing). Who it is by is never written here:
    it is the workspace's publisher name (Forrest, 2026-10-01: not freely typed,
    then a unique workspace setting), so `author` is ignored, and a workspace
    without one cannot list. Listed again later, it keeps its first listing date."""
    kind, path = norm_target(conn, ws_id, kind, path)
    t = _field(title, TITLE_MAX, "a name", True)
    d = _field(description, DESCRIPTION_MAX, "a description", False)
    if on and not publisher_of(conn, ws_id):
        raise ShareError(NO_PUBLISHER)
    with db.LOCK, conn:
        row = _own_public(conn, ws_id, kind, path)
        if not row:
            if not on:
                return False
            raise ShareError("Make it public on the web first. Only something public can be"
                             " listed.")
        if not on:
            conn.execute("UPDATE shares SET listed_at=NULL WHERE id=?", (row["id"],))
            return False
        conn.execute(
            "UPDATE shares SET listed_at=COALESCE(listed_at, ?), listed_by=COALESCE(?, listed_by),"
            " listed_title=COALESCE(?, listed_title),"
            " listed_description=COALESCE(?, listed_description),"
            " listed_author=NULL WHERE id=?",
            (time.time(), by, t, d, row["id"]))
    return on


def published_access(conn, ws_id: int) -> Access:
    """What of a workspace is published, as an Access: the part search engines may
    list. The rest of what is public (anyone with the link) tells them not to."""
    rows = conn.execute("SELECT kind, path FROM shares WHERE workspace_id=? AND email IS NULL"
                        " AND user_id IS NULL AND listed_at IS NOT NULL", (ws_id,)).fetchall()
    return Access(ws_id, "public" if rows else None,
                  whole=any(r["kind"] == "wiki" for r in rows),
                  folders=frozenset(r["path"] for r in rows if r["kind"] == "folder"),
                  pages=frozenset(r["path"] for r in rows if r["kind"] == "page"))


def listed(conn, share_id) -> dict | None:
    """A share that is public and listed, by id, or None: what /copy accepts."""
    try:
        sid = int(share_id)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT * FROM shares WHERE id=? AND email IS NULL AND user_id IS NULL"
                       " AND listed_at IS NOT NULL", (sid,)).fetchone()
    return dict(row) if row else None


def listing_for(conn, ws_id: int) -> dict | None:
    """The widest listed share in a workspace (the wiki before a folder before a
    page), for the Make a copy button a guest sees, or None."""
    rows = conn.execute("SELECT * FROM shares WHERE workspace_id=? AND email IS NULL AND"
                        " user_id IS NULL AND listed_at IS NOT NULL", (ws_id,)).fetchall()
    rows = sorted((dict(r) for r in rows), key=lambda r: (_ORDER.get(r["kind"], 3),
                                                          len(r["path"]), r["path"]))
    return rows[0] if rows else None


def reached(conn, ws_id: int, kind: str, path: str) -> list:
    """The pages a share on (kind, path) reaches now: rows of path, title, text,
    words and updated_at, in path order."""
    k = db.wiki_key(ws_id)
    cols = "SELECT path, title, text, words, updated_at FROM pages WHERE project=?"
    if kind == "wiki":
        return conn.execute(cols + " ORDER BY path", (k,)).fetchall()
    if kind == "folder":
        return conn.execute(cols + " AND path LIKE ? ESCAPE '\\' ORDER BY path",
                            (k, _like_prefix(path))).fetchall()
    return conn.execute(cols + " AND path=?", (k, path)).fetchall()


# A listed wiki or folder introduces itself with its front page, if it has one.
FRONT_PAGES = ("index", "README", "readme", "Readme", "home", "Home", "overview", "Overview")


def _scope(kind: str, path: str) -> tuple[str, tuple]:
    """The WHERE clause after project=? that picks a share's pages, and its values."""
    if kind == "wiki":
        return "", ()
    if kind == "folder":
        return " AND path LIKE ? ESCAPE '\\'", (_like_prefix(path),)
    return " AND path=?", (path,)


def _own_words(conn, ws: dict, kind: str, path: str) -> tuple[str, str]:
    """The wiki's own name and description for a target: the workspace's name for
    the whole wiki, else its front page's title, else its name; the front page's
    first sentence. What a listing shows when its owner wrote none."""
    from ..parse import description_of
    k = db.wiki_key(ws["id"])
    if kind == "page":
        wanted = [path]
    else:
        prefix = path + "/" if kind == "folder" else ""
        wanted = [prefix + name for name in FRONT_PAGES]
    found = {r["path"]: r for r in conn.execute(
        f"SELECT path, title, text FROM pages WHERE project=? AND path IN"
        f" ({','.join('?' * len(wanted))})", (k, *wanted)).fetchall()}
    front = next((found[p] for p in wanted if p in found), None)
    if kind == "wiki":
        title = ws.get("name") or "A wiki"
    elif kind == "folder":
        title = (front["title"] if front else "") or path.rsplit("/", 1)[-1]
    else:
        title = (front["title"] if front else "") or path
    return title, (description_of(front["text"] or "") if front else "")


def author_of(conn, ws: dict) -> str:
    """Who a listing is by: its workspace's publisher name as it is now; for one
    listed before publisher names existed, the workspace's name."""
    return publisher_of(conn, ws["id"]) or ws.get("name") or ""


def listing_form(conn, ws_id: int, kind: str, path: str, me: int) -> dict:
    """What the Share dialog's listing fields start with: what the owner wrote,
    else the wiki's own name and description, and their own name as author."""
    kind, path = norm_target(conn, ws_id, kind, path)
    ws = db.workspace(conn, ws_id) or {"id": ws_id}
    row = _own_public(conn, ws_id, kind, path)
    row = dict(row) if row else {}
    title, about = _own_words(conn, ws, kind, path)
    return {"title": row.get("listed_title") or title,
            "description": (row.get("listed_description")
                            if row.get("listed_description") is not None else about),
            # Who it is by: the workspace's publisher name (set_publisher), which an
            # owner can give it here if it has none yet.
            "publisher": publisher_of(conn, ws_id),
            "publisher_slug": publisher_slug(publisher_of(conn, ws_id)),
            "can_name_publisher": db.role_in(conn, ws_id, me) == "owner",
            "limits": {"title": TITLE_MAX, "description": DESCRIPTION_MAX,
                       "publisher": PUBLISHER_MAX}}


def counts(conn, ws_id: int, kind: str, path: str) -> dict:
    """Pages and words a target reaches now, and its version (page count and last
    change), which names a picture of it (preview.py)."""
    where, args = _scope(kind, path)
    n = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(words), 0) AS words,"
                     " COALESCE(MAX(updated_at), 0) AS at FROM pages WHERE project=?" + where,
                     (db.wiki_key(ws_id), *args)).fetchone()
    pages = int(n["n"] or 0) if n else 0
    at = int(n["at"] or 0) if n else 0
    return {"pages": pages, "words": int(n["words"] or 0) if n else 0, "version": f"{pages}.{at}"}


# Publish (Forrest, 2026-10-01: "should we have a second button for publish, rather
# than lump it into share?", then "yes"): its own button and dialog. Publishing a
# wiki or folder makes it public on its own and lists it, in one step, with the
# name, description and author the directory shows. Unpublishing takes it off the
# directory and leaves it public; Share is where it becomes private again.
PUBLISHABLE = ("wiki", "folder")


def publish_state(conn, ws_id: int, kind: str, path: str, me: int) -> dict:
    """Everything the Publish dialog shows for a wiki or folder."""
    kind, path = norm_target(conn, ws_id, kind, path)
    if kind not in PUBLISHABLE:
        raise ShareError("Publish the whole wiki or a folder.")
    row = _own_public(conn, ws_id, kind, path)
    pub_rows = [r for r in conn.execute(
        "SELECT kind, path FROM shares WHERE workspace_id=? AND email IS NULL AND"
        " user_id IS NULL", (ws_id,)).fetchall() if covers(r["kind"], r["path"], kind, path)]
    return {"target": {"kind": kind, "path": path, "title": title_of(conn, ws_id, kind, path)},
            **counts(conn, ws_id, kind, path),
            "published": bool(row and row["listed_at"]),
            "public": bool(pub_rows), "own_public": bool(row),
            "listing": listing_form(conn, ws_id, kind, path, me),
            "share_id": row["id"] if row and row["listed_at"] else None}


def publish(conn, ws_id: int, kind: str, path: str, on: bool, by: int, *, title=None,
            description=None, author=None, publisher=None) -> None:
    """Publish a wiki or folder (public on its own, and listed), or unpublish it
    (off the directory; still public). `publisher` names the workspace's publisher
    the first time, for an owner, when it has none."""
    kind, path = norm_target(conn, ws_id, kind, path)
    if kind not in PUBLISHABLE:
        raise ShareError("Publish the whole wiki or a folder.")
    if not on:
        set_listed(conn, ws_id, kind, path, False)
        return
    # Check everything before anything changes, so a refusal leaves it as it was.
    _field(title, TITLE_MAX, "a name", True)
    _field(description, DESCRIPTION_MAX, "a description", False)
    if not publisher_of(conn, ws_id):
        if publisher is None or not str(publisher).strip():
            raise ShareError(NO_PUBLISHER)
        if db.role_in(conn, ws_id, by) != "owner":
            raise ShareError("Only an owner can give the workspace its publisher name.")
        set_publisher(conn, ws_id, publisher)
    set_public(conn, ws_id, kind, path, True, by)
    set_listed(conn, ws_id, kind, path, True, by=by, title=title, description=description)


def listing(conn, row: dict) -> dict | None:
    """What the directory says about one listed share: its name, description and
    author (as its owner wrote them, else the wiki's own), its size and its
    addresses (relative to the app), and a picture of its graph (preview.py).
    None when it reaches no pages now (a folder emptied, a page deleted). Counts
    come from the database; only the front page's text is read."""
    ws = db.workspace(conn, row["workspace_id"]) or {"id": row["workspace_id"]}
    k = db.wiki_key(row["workspace_id"])
    where, args = _scope(row["kind"], row["path"])
    n = conn.execute("SELECT COUNT(*) AS n, COALESCE(SUM(words), 0) AS words,"
                     " COALESCE(MAX(updated_at), 0) AS at FROM pages WHERE project=?" + where,
                     (k, *args)).fetchone()
    if not n or not n["n"]:
        return None
    title, about = _own_words(conn, ws, row["kind"], row["path"])
    title = row.get("listed_title") or title
    if row.get("listed_description") is not None:
        about = row["listed_description"]
    author = author_of(conn, ws)
    version = f"{int(n['n'])}.{int(n['at'] or 0)}"
    handle = ws.get("handle") or ""
    url = f"/w/{handle}" + ("/" + _quote(row["path"]) if row["kind"] == "page"
                            else "?folder=" + _quote(row["path"]) if row["kind"] == "folder"
                            else "")
    publisher = publisher_of(conn, ws["id"])
    return {"id": row["id"], "kind": row["kind"], "path": row["path"], "title": title,
            "description": about, "author": author,
            # The publisher's handle and its page's slug; empty for a listing from
            # before publisher names, which shows the workspace's name instead.
            "publisher": publisher, "publisher_slug": publisher_slug(publisher),
            "workspace": ws.get("name") or "",
            "handle": handle, "pages": int(n["n"]), "words": int(n["words"] or 0),
            "updated_at": float(n["at"] or 0), "listed_at": row["listed_at"],
            "url": url, "copy_url": f"/copy?from={row['id']}",
            "preview_url": f"/api/v1/directory/{row['id']}/preview.svg?v={version}",
            "version": version}


def directory(conn, limit: int = 500) -> list[dict]:
    """Everything listed, newest listing first, for the directory on dexio.wiki."""
    rows = conn.execute("SELECT * FROM shares WHERE email IS NULL AND user_id IS NULL AND"
                        " listed_at IS NOT NULL ORDER BY listed_at DESC, id DESC"
                        " LIMIT ?", (int(limit),)).fetchall()
    out = []
    for r in rows:
        item = listing(conn, dict(r))
        if item:
            out.append(item)
    return out


def _quote(path: str) -> str:
    from urllib.parse import quote
    return quote(path, safe="/")


def remove(conn, ws_id: int, share_id: int) -> bool:
    with db.LOCK, conn:
        cur = conn.execute("DELETE FROM shares WHERE id=? AND workspace_id=?"
                           " AND email IS NOT NULL", (share_id, ws_id))
    return cur.rowcount > 0


def accept(conn, code: str, user_id: int) -> dict:
    """Open an emailed share link as `user_id`: the share is theirs from now on,
    if the address it was sent to is theirs. Returns the share. WrongAccount
    when the address is not theirs, which leaves the link as it was; ShareError
    when the link is unknown, or another account with the address has it.

    A share some other account took before only the address's owner could
    (the sharer opening their own email, say) goes to the owner when they open
    the link."""
    with db.LOCK, conn:
        row = conn.execute("SELECT * FROM shares WHERE code_hash=?",
                           (_hash(code),)).fetchone() if code else None
        if not row:
            raise ShareError("This link is not valid any more. It may have been replaced by a"
                             " newer one, or the share was removed.")
        if row["user_id"] == user_id:
            return dict(row)
        if row["email"] not in addresses(conn, user_id):
            raise WrongAccount(row["email"])
        if row["user_id"] and row["email"] in addresses(conn, row["user_id"]):
            raise ShareError("This link was already used by another account. Ask the person"
                             " who shared it to share it with the address you use here.")
        conn.execute("UPDATE shares SET user_id=?, accepted_at=? WHERE id=?",
                     (user_id, time.time(), row["id"]))
    return dict(row)


def follow_move(conn, ws_id: int, old: str, new: str) -> None:
    """A moved page keeps the shares on it, as a moved Drive file does."""
    if not old or not new or old == new:
        return
    with db.LOCK, conn:
        conn.execute("UPDATE shares SET path=? WHERE workspace_id=? AND kind='page' AND path=?",
                     (new, ws_id, old))


# ---- Settings > Sharing -------------------------------------------------------------
# Forrest, 2026-09-28: "in settings, can we make it easy to see what parts of
# the wiki have been shared?" The dialog answers that for one target at a time;
# this answers it for the whole workspace: everything public on the web, and
# everyone something is shared with, each with what it reaches now.
_ORDER = {"wiki": 0, "folder": 1, "page": 2}


def overview(conn, ws_id: int) -> dict:
    """Every share in the workspace, as Settings > Sharing lists it:
    {"public": [...], "people": [...], "public_pages": n, "pages": n}. Each item
    has its share's id, kind, path and title, `pages` (how many pages it reaches
    now), `exists` (False for a deleted page or a folder with no pages left:
    the share stays and applies again if one comes back), when and by whom it
    was made, and `via`: a broader share that already gives the same access,
    so taking this one away changes nothing. People also carry email, name
    (once they opened the link) and pending."""
    k = db.wiki_key(ws_id)
    titles = {r["path"]: r["title"] for r in conn.execute(
        "SELECT path, title FROM pages WHERE project=?", (k,)).fetchall()}
    ws_name = (db.workspace(conn, ws_id) or {}).get("name") or "the wiki"
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM shares WHERE workspace_id=? ORDER BY created_at, id", (ws_id,)).fetchall()]
    names = db.people(conn, [r["created_by"] for r in rows] + [r["user_id"] for r in rows])

    def reach(kind: str, path: str) -> list[str]:
        return [p for p in titles if covers(kind, path, "page", p)]

    def title(kind: str, path: str) -> str:
        if kind == "wiki":
            return ws_name
        return titles.get(path) or path if kind == "page" else path

    def item(r: dict, same: list[dict]) -> dict:
        n = len(reach(r["kind"], r["path"]))
        wider = next((o for o in same if o["id"] != r["id"] and _wider(o, r)), None)
        return {"id": r["id"], "kind": r["kind"], "path": r["path"],
                "title": title(r["kind"], r["path"]), "pages": n,
                "exists": r["kind"] == "wiki" or n > 0,
                "created_at": r["created_at"], "by": names.get(r["created_by"] or 0),
                "listed": bool(r.get("listed_at")),
                "via": None if not wider else {"kind": wider["kind"], "path": wider["path"],
                                               "title": title(wider["kind"], wider["path"])}}

    order = lambda r: (_ORDER.get(r["kind"], 3), r["path"])  # noqa: E731
    pub = sorted((r for r in rows if r["email"] is None and r["user_id"] is None), key=order)
    public = [item(r, pub) for r in pub]
    people = []
    for r in sorted((r for r in rows if r["email"] is not None),
                    key=lambda r: (r["email"], *order(r))):
        same = [o for o in rows if o["email"] == r["email"]]
        people.append({**item(r, same), "email": r["email"],
                       "name": names.get(r["user_id"]) if r["user_id"] else None,
                       "pending": not r["user_id"], "opened_at": r["accepted_at"]})
    open_pages = {p for r in pub for p in reach(r["kind"], r["path"])}
    return {"public": public, "people": people, "public_pages": len(open_pages),
            "pages": len(titles)}


def _wider(a: dict, b: dict) -> bool:
    """Whether share a reaches everything share b does, and more (or the same
    target, made earlier)."""
    if (a["kind"], a["path"]) == (b["kind"], b["path"]):
        return a["id"] < b["id"]
    return covers(a["kind"], a["path"], b["kind"], b["path"])


def stop(conn, ws_id: int, share_id: int) -> dict | None:
    """Take one share away, of either kind: a person's, or a public one (which
    set_public cannot reach once its page is gone). Returns the row, or None."""
    with db.LOCK, conn:
        row = conn.execute("SELECT * FROM shares WHERE id=? AND workspace_id=?",
                           (share_id, ws_id)).fetchone()
        if not row:
            return None
        conn.execute("DELETE FROM shares WHERE id=?", (share_id,))
    return dict(row)


# ---- the share dialog -------------------------------------------------------------
def dialog(conn, ws_id: int, kind: str, path: str, me: int) -> dict:
    """Everything the share dialog shows for one target: who can open it and how
    (members, and pending member invites, for the wiki; people it or a folder
    above it is shared with), and whether anyone with the link can."""
    kind, path = norm_target(conn, ws_id, kind, path)
    ws = db.workspace(conn, ws_id) or {}
    rows = conn.execute("SELECT * FROM shares WHERE workspace_id=? ORDER BY created_at, id",
                        (ws_id,)).fetchall()
    here = [r for r in rows if covers(r["kind"], r["path"], kind, path)]
    names = db.people(conn, [r["user_id"] for r in here if r["user_id"]])
    people = []
    for r in here:
        if r["email"] is None:
            continue
        own = r["kind"] == kind and r["path"] == path
        people.append({
            "id": r["id"], "email": r["email"],
            "name": names.get(r["user_id"]) if r["user_id"] else None,
            "pending": not r["user_id"], "role": "viewer",
            "via": None if own else {"kind": r["kind"], "path": r["path"],
                                     "title": title_of(conn, ws_id, r["kind"], r["path"])}})
    pub_rows = [r for r in here if r["email"] is None and r["user_id"] is None]
    own_pub = any(r["kind"] == kind and r["path"] == path for r in pub_rows)
    wider = [r for r in pub_rows if not (r["kind"] == kind and r["path"] == path)]
    via = next((r for r in wider if r["listed_at"]), None) or next(iter(wider), None)
    members = [{"id": m["id"], "email": m["email"],
                "name": " ".join(p for p in (m["first_name"] or "", m["last_name"] or "") if p)
                or None, "role": m["role"], "you": m["id"] == me}
               for m in db.members(conn, ws_id)]
    invites = [{"id": i["id"], "email": i["email"]} for i in db.pending_invites(conn, ws_id)]
    my_role = db.role_in(conn, ws_id, me)
    limit = db.member_limit(conn, ws_id)
    return {
        "target": {"kind": kind, "path": path, "title": title_of(conn, ws_id, kind, path)},
        "workspace": {"id": ws.get("handle"), "name": ws.get("name"), "plan": ws.get("plan")},
        "members": members, "invites": invites, "people": people,
        # listed: in the directory on dexio.wiki; only what is public on its own can be.
        "public": {"on": own_pub or via is not None, "own": own_pub,
                   "listed": any(r["kind"] == kind and r["path"] == path and r["listed_at"]
                                 for r in pub_rows),
                   "listing": listing_form(conn, ws_id, kind, path, me) if own_pub else None,
                   # The three levels of General access (Forrest, 2026-10-01): Restricted,
                   # Anyone with the link, Published on dexio.wiki (wiki or folder only).
                   "publishable": kind in PUBLISHABLE,
                   "via": None if not via else {
                       "kind": via["kind"], "path": via["path"],
                       "listed": bool(via["listed_at"]),
                       "title": title_of(conn, ws_id, via["kind"], via["path"])}},
        # Editors are members: only the whole wiki has them, and only owners
        # add them, within the plan's member limit.
        "editors": {"allowed": kind == "wiki" and my_role == "owner",
                    "room": limit is None or len(members) < limit,
                    "plan": ws.get("plan")},
    }
