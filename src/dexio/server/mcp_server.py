"""MCP endpoint: agents read, search, write and reorganize their workspace's
wiki over streamable HTTP.

Mounted at /mcp by the server. It authenticates with workspace API keys, each of
which reads and writes its workspace's wiki and, once the key's creator allows it
in Settings > Agents, reads what other workspaces share with that person (the
four read tools' `workspace` argument, list_workspaces). A workspace has one wiki (since
2026-09-28), so no tool takes a wiki name; a `wiki` argument from a client set up
before then is ignored. Clients that can send a
header use one as
`Authorization: Bearer <API key>` (Claude Code, Hermes, OpenClaw); Claude and
ChatGPT sign in with OAuth instead (see oauth.py) and get an access token for
one workspace.

The tool set is meant to cover everything an agent does with a wiki on local
disk, so the wiki can live here alone: list, read (whole, one section, a line
range, or just the headings), search (text or regex), create, replace, edit in
place (an exact string or a whole section), append, delete, move with link
rewriting, several of those at once as one all-or-nothing step with a dry run,
and page history with any past version readable. Every change is kept as a
revision with the writer's note on why and the agent that made it (a required
parameter on every change, so agents sharing one API key are still told apart), and
writes can carry the version they were based on so two agents cannot silently
overwrite each other.

read_page returns the page's markdown as its own text block after a JSON
header, so an agent sees the text exactly as stored, not escaped inside JSON,
and can quote it back to edit_page as it is.

Built on the public MCP Python SDK (mcp 2.x, `MCPServer`), stateless JSON
responses, so each call is one POST and nothing is held open between calls.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from typing import Annotated, Literal
from urllib.parse import quote

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp_types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, Field, WithJsonSchema

from .. import VERSION
from .. import health, okf
from .. import search as wordsearch
from ..parse import (MDLINK, WIKILINK, Page, _normalise, _resolve, basename, find_section,
                     keep_lines, line_slice, mask_code, page_links, replace_lines, resolve_keys)
from ..parse import heading_anchors
from ..parse import outline as page_outline
from . import auth, db, files, oauth, shares

MCP_PATH = "/mcp"
# An abuse limit, not a design one: history stores diffs, so a big page costs
# its size once, not once per change. Writes warn past SIZE_WARN of it.
MAX_PAGE_BYTES = 5 * 1024 * 1024
SIZE_WARN = 0.8
# The most one read_page returns. A bigger page, section or line range comes back
# cut at a line boundary with next_offset (about 32k tokens).
READ_MAX_BYTES = 128 * 1024
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_QUERY = 500
MAX_NOTE = 500
MAX_AGENT = 64
MAX_BATCH = 200
# Past this a page is flagged by wiki_health and read_page offers its outline. About
# 200 lines of wrapped prose; every reader pays for the whole page on each read.
LONG_PAGE_WORDS = 2000
# Unlinked mentions are looked for in at most this much of a page, so a write to a
# very large page costs no more than one to a page this size.
MENTION_SCAN_BYTES = 512 * 1024
# The most stale pages wiki_health lists.
MAX_STALE_LISTED = 50
# Pages one list_pages call returns unless it asks for more, and the most it may
# ask for. Each entry costs an agent about 80 tokens, so 200 is about 16K; past
# that the call says how many remain and which folders hold them.
LIST_PAGES_DEFAULT = 200
LIST_PAGES_MAX = 1000
# MCP methods Dexio does not serve: it has no prompts or resources, and the SDK
# advertises both unless their handlers are removed (see build_mcp).
UNSERVED_METHODS = ("prompts/list", "prompts/get", "resources/list",
                    "resources/templates/list", "resources/read",
                    "resources/subscribe", "resources/unsubscribe")

INSTRUCTIONS = """\
Dexio is this workspace's shared wiki: the markdown pages its people and agents keep about
their own work. It is the wiki's only copy, so every change goes through these tools. Page
paths have no .md extension, for example entities/acme-corp. A workspace has one wiki, so
no tool takes a wiki name; folders keep topics apart.

When to use it:
- Before answering a question about this workspace's own projects, customers, decisions,
  people, systems or past research, search the wiki. The answer may already be written down.
- When the conversation produces something the workspace will need again, record it: a
  decision and its reason, a research finding with its source, how something works, or a
  correction to a page. Search for the page that covers the topic and edit it; create a
  page only when none does. Then tell the user what you recorded, with the page's url.
- Cite a source with a footnote: [^1] after the claim, and a line "[^1]: the source, with
  its link" anywhere on the page. The page shows the citation as [1] and lists the notes
  at its end.
- Frontmatter can carry two of the Open Knowledge Format's fields: status (draft, stable
  or deprecated), and sources, a list of { id, resource, title }, where a footnote
  labelled with a source's id, [^id], cites it.
- Leave out small talk, one-off lookups, unfinished drafts, secrets, and anything the user
  asks you to keep out of the wiki.

Find things: list_pages (a one-line description of each page, 200 at a time;
optionally one folder), search_pages (every word of the query, best matches first, "quotes" for an exact
phrase, or a regex; matching lines with their line numbers), read_page (a JSON header with
version, links and status, then the markdown as plain text; outline, section, or offset
and limit read part of a long page). wiki_health finds links that point nowhere, pages that
may be out of date, pages named without a link, and pages long enough to split.

Change things: edit_page replaces an exact string or a whole section (the usual way to change
part of a page), append_page adds to the end (logs), write_page creates or replaces a whole
page, delete_page removes one, move_page renames one and rewrites the links pointing at it.
change_pages does several of these as one step that applies completely or not at all, with a
dry run; use it to reorganise. Pass the version from read_page as base_version to make the
change fail instead of overwriting someone else's edit. Every change names its agent: your own
agent name, or the person's name when a person is making the change directly. Give every
change a note too, one line on what changed and why; both are kept in the history. Changes
report any links on the page that do not resolve; fix those before moving on. They also list
pages the text names without linking to (unlinked_mentions); link them where it helps.

Link people to pages: every page has a web address, url in read_page and in change results
(a section read with section adds its #anchor): https://app.dexio.wiki/w/<workspace>/,
then its path. Give it whenever you point someone at a page.

Undo things: page_history lists a page's revisions with the agent and person who made them
and their notes (or, without a path, the wiki's latest changes; agent filters to one agent),
read_page with revision reads an old one, and write_page with that text restores it. Deleted
pages keep their history.

Files: upload_file uploads one (images, PDFs, decks), list_files lists them, read_page and
search_pages read and search them like pages, and delete_file removes one for good: files
keep no history.

Other workspaces: list_workspaces lists this workspace and those that share pages with you.
Pass one's handle, or a link into it, as workspace to list_pages, search_pages, read_page or
list_files to read what it shares; it is read-only. A key reads only its own workspace until
its owner allows more in Settings > Agents.

Who can see it: set_visibility reports or sets who can open a page, a folder or the whole
wiki: restricted (members), link (anyone with the url) or published (listed on dexio.wiki).
Open something to the public only when the person asks for it. Sharing with one person by
email is done in the app.

Take the whole wiki out: GET https://app.dexio.wiki/api/v1/export with the same bearer
returns every page as a zip of markdown files at their paths.

Help: the person writes to Dexio from Settings, Help in the app, at
https://dexio.wiki/contact, or to support@dexio.wiki. That is also how a workspace owner has
something deleted for good, such as a page's earlier versions, which no tool here can do.

Guide: https://dexio.wiki/agents.md"""

READ = dict(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)
CHANGE = dict(readOnlyHint=False, destructiveHint=False, idempotentHint=False,
              openWorldHint=False)
REMOVE = dict(readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=False)

# The six tools that change a wiki. Each takes `agent`, and the schema they list
# marks it required (see build_mcp).
CHANGE_TOOLS = ("write_page", "edit_page", "append_page", "delete_page", "move_page",
                "change_pages", "upload_file")
Agent = Annotated[str, Field(description=(
    "Your own agent name (each agent sharing an API key gives its own), or the person's name"
    " if a person is making the change."))]
Note = Annotated[str | None, Field(description="One line: what changed and why.")]
# Another workspace to read, on the four read tools (Forrest, 2026-10-06: keys act
# as their person, so an agent reads what other workspaces share with that person).
Workspace = Annotated[str, Field(description=(
    "Read another workspace instead: its handle, or a link into it (https://app.dexio.wiki/w/"
    "<handle>/...). Only what it shares with you, read-only; list_workspaces lists them."
    " Empty: this workspace."))]
_HANDLE_IN_URL = re.compile(r"/w/([A-Za-z0-9_-]+)")


def ann(kind: dict, title: str) -> ToolAnnotations:
    """Annotations for one tool. Anthropic's directory policy asks for readOnlyHint,
    destructiveHint and title on every tool."""
    return ToolAnnotations(title=title, **kind)


def _compact_schema(schema) -> None:
    """Shrink a JSON schema in place without changing what it accepts from a
    client that leaves optional parameters out: drop "title"; write an optional
    `X | None` (anyOf X or null, default null) as plain X, since leaving it out
    already means None. Property names are keys of "properties", never touched."""
    if isinstance(schema, dict):
        if isinstance(schema.get("title"), str):
            del schema["title"]
        opts = schema.get("anyOf")
        if (isinstance(opts, list) and len(opts) == 2 and {"type": "null"} in opts
                and "default" in schema and schema["default"] is None):
            other = next(o for o in opts if o != {"type": "null"})
            del schema["anyOf"], schema["default"]
            schema.update(other)
        for key, value in schema.items():
            if key == "properties" and isinstance(value, dict):
                for sub in value.values():
                    _compact_schema(sub)
            elif isinstance(value, (dict, list)):
                _compact_schema(value)
    elif isinstance(schema, list):
        for item in schema:
            _compact_schema(item)


def _bearer(headers) -> str:
    value = (headers or {}).get("authorization", "") if headers is not None else ""
    return value[7:].strip() if value.lower().startswith("bearer ") else ""


def _norm_path(path: str) -> str:
    p = str(path or "").strip().replace("\\", "/").strip("/")
    if p.endswith(".md"):
        p = p[:-3]
    if not p or any(part in ("", ".", "..") for part in p.split("/")):
        raise ToolError(f"bad page path: {path!r}")
    return p


def _iso(ts) -> str:
    if not ts:
        return ""
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mb(n: int) -> str:
    return f"{n / 1024 / 1024:.1f} MB"


def _subfolders(paths: list[str], prefix: str) -> list[dict]:
    """The folders one level under `prefix` (or the wiki's top-level folders) with
    how many pages each holds, counting pages nested deeper, in path order."""
    counts: dict[str, int] = {}
    cut = len(prefix) + 1 if prefix else 0
    for p in paths:
        rest = p[cut:]
        if "/" in rest:
            name = (prefix + "/" if prefix else "") + rest.split("/", 1)[0]
            counts[name] = counts.get(name, 0) + 1
    return [{"folder": f, "pages": n} for f, n in sorted(counts.items())]


def _check_size(text: str) -> dict:
    """Refuse a page over MAX_PAGE_BYTES; past SIZE_WARN of it, the write result
    carries the size and a warning, so a growing log is split before it fails."""
    n = len(text.encode("utf-8"))
    if n > MAX_PAGE_BYTES:
        raise ToolError(f"page would be {_mb(n)}, over the {_mb(MAX_PAGE_BYTES)} limit; split it"
                        " into several pages (change_pages does it in one step)")
    if n > MAX_PAGE_BYTES * SIZE_WARN:
        return {"bytes": n, "warning": f"page is {_mb(n)} of the {_mb(MAX_PAGE_BYTES)} limit;"
                                       " split it, or start a new page for new entries"}
    return {}


def _check_base(pages: dict[str, Page], path: str, base_version: str | None) -> None:
    if not base_version:
        return
    if path not in pages:
        raise ToolError(f"{path!r} does not exist, so it cannot be at version {base_version}")
    current = db.version_of(pages[path].text)
    if current != base_version:
        raise ToolError(f"{path!r} changed since you read it: you have version {base_version},"
                        f" it is now {current}. Read it again and reapply your change.")


def _check_note(note: str | None) -> str | None:
    n = " ".join(str(note or "").split())
    if len(n) > MAX_NOTE:
        raise ToolError(f"note is longer than {MAX_NOTE} characters; keep it to one line")
    return n or None


def _check_agent(agent: str | None) -> str:
    a = " ".join(str(agent or "").split())
    if not a:
        raise ToolError("agent is required: pass agent, your own agent name (each agent"
                        " sharing an API key gives its own), or the person's name if a person is"
                        " making it. If your list of Dexio tools shows no agent parameter,"
                        " reconnect to the Dexio MCP server to load the current tools.")
    if len(a) > MAX_AGENT:
        raise ToolError(f"agent is longer than {MAX_AGENT} characters; give just the name")
    return a


def _page_result(meta: dict, text: str | None) -> CallToolResult:
    """A JSON header, then the page's markdown as its own plain text block. Inside
    JSON the text would arrive with every newline and quote escaped, and an agent
    has to undo that exactly before it can quote the text back to edit_page."""
    if text is not None:
        meta = {**meta, "text_follows": "the page's markdown is the next block, unescaped"}
    blocks = [TextContent(type="text", text=json.dumps(meta, ensure_ascii=False, indent=2))]
    if text is not None:
        blocks.append(TextContent(type="text", text=text))
    return CallToolResult(content=blocks)


class Change(BaseModel):
    """One change in change_pages."""
    op: Literal["write", "edit", "append", "delete", "move"]
    path: str = Field(description="The page, e.g. entities/acme-corp")
    text: str | None = Field(None, description="write: the whole page; append: text to add")
    old_text: str | None = Field(None, description="edit: exact text to replace")
    new_text: str | None = Field(None, description="edit: replacement, empty to delete")
    section: str | None = Field(None, description="edit: a heading's text or line number; "
                                "alone, replaces that whole section")
    replace_all: bool = Field(False, description="edit: replace every match of old_text")
    new_path: str | None = Field(None, description="move: where the page goes")
    update_links: bool = Field(True, description="move: rewrite links to the old path")
    base_version: str | None = Field(None, description="write, edit, delete: fail if the "
                                     "page changed since this version")


# The schema clients see for change_pages' list, written out in place: some clients
# and model APIs do not follow a $ref into $defs, which is what pydantic emits.
ChangeList = Annotated[list[Change], WithJsonSchema({
    "type": "array", "minItems": 1, "maxItems": MAX_BATCH,
    "items": Change.model_json_schema()})]


def _split_target(inner: str) -> tuple[str, str]:
    """[[target|alias]] / [[target#anchor]] -> (target, '|alias' or '#anchor...')."""
    cut = min([i for i in (inner.find("|"), inner.find("#")) if i >= 0], default=len(inner))
    return inner[:cut], inner[cut:]


def rewrite_links(text: str, src: str, old: str, new: str, pages: dict[str, Page]) -> tuple[str, int]:
    """Point every link in `text` (on page `src`) that resolves to `old` at `new`.
    Resolution uses `pages` as it was before the move, the same rules the graph uses."""
    count = 0

    def wiki(m: re.Match) -> str:
        nonlocal count
        target, suffix = _split_target(m.group(1))
        t = _normalise(target)
        if t and _resolve(t, src, pages) == old:
            count += 1
            return f"[[{new}{suffix}]]"
        return m.group(0)

    def md(m: re.Match) -> str:
        nonlocal count
        full = m.group(0)
        target = m.group(1)
        if "://" in target or target.startswith(("#", "mailto:")):
            return full
        base, _, anchor = target.partition("#")
        if not (base.endswith(".md") or "/" in base):
            return full
        t = _normalise(base)
        if t and _resolve(t, src, pages) == old:
            count += 1
            repl = new + (".md" if base.endswith(".md") else "") + (f"#{anchor}" if anchor else "")
            return full[: m.start(1) - m.start(0)] + repl + full[m.end(1) - m.start(0):]
        return full

    text = _sub_outside_code(WIKILINK, text, wiki)
    text = _sub_outside_code(MDLINK, text, md)
    return text, count


def _sub_outside_code(pattern: re.Pattern, text: str, fn) -> str:
    """pattern.sub(fn, text), skipping matches inside code and HTML comments, which
    are examples rather than links (the graph ignores them the same way)."""
    masked = mask_code(text)
    out, last = [], 0
    for m in pattern.finditer(masked):
        orig = pattern.fullmatch(text, m.start(), m.end())
        if orig is None:
            continue
        out.append(text[last:m.start()])
        out.append(fn(orig))
        last = m.end()
    out.append(text[last:])
    return "".join(out)


def build_mcp(conn) -> MCPServer:
    server = MCPServer(name="dexio", title="Dexio", version=VERSION,
                       instructions=INSTRUCTIONS, website_url="https://dexio.wiki")
    # Dexio serves tools only. MCPServer registers prompt and resource handlers
    # whether or not any exist, and the SDK advertises a capability for every
    # registered handler, so clients that give each capability its own tools
    # (Hermes adds four) carried tools that always list nothing. Without the
    # handlers the capabilities are not advertised and those methods answer
    # "method not found", as the spec expects.
    for method in UNSERVED_METHODS:
        server._lowlevel_server._request_handlers.pop(method, None)

    def caller(ctx: Context):
        bearer = _bearer(ctx.headers)
        row = db.check_token(conn, bearer) or oauth.access_row(conn, bearer)
        if not row:
            # The ASGI gate rejects these before they reach a tool; this is the
            # second lock in case the endpoint is ever mounted without it.
            raise ToolError("unauthorized: send Authorization: Bearer <API key>")
        return row

    def writer(ctx: Context):
        """The caller, for a tool that changes the workspace: refused while the
        workspace is read-only (db.read_only_reason), with what an owner can do."""
        row = caller(ctx)
        why = db.read_only_reason(conn, row["workspace_id"])
        if why:
            raise ToolError(why)
        return row

    def the_wiki(row) -> str:
        """The key of the caller's workspace's wiki, made if it is somehow missing."""
        return db.ensure_wiki(conn, row["workspace_id"], source="mcp", author=row["name"])

    def reading(row, workspace: str | None) -> tuple[str, shares.Access | None, str]:
        """What a read tool reads: (wiki key, access, workspace handle). Access is
        None for the caller's own workspace (everything). For another workspace it
        is what that workspace shares with the key's person (shares.access), and
        only when that person let the key read their shares (db.shared_reach_person);
        a workspace that shares nothing with them reads as not there at all."""
        home = db.handle_of(conn, row["workspace_id"])
        want = str(workspace or "").strip()
        found = _HANDLE_IN_URL.search(want)
        want = found.group(1) if found else want.strip("/")
        if not want or want == home:
            return the_wiki(row), None, home
        person = db.shared_reach_person(row)
        if person is None:
            raise ToolError(
                f"this key reads only its own workspace ({home}). Its owner can let it read, not"
                " change, what other workspaces share with them: Settings > Agents, \"Let it read"
                f" what's shared with you\" ({public}/settings/agents)")
        ws = db.workspace_by_handle(conn, want)
        acc = shares.access(conn, ws["id"], person) if ws else None
        if ws is None or acc is None or not acc.any:
            raise ToolError(f"no workspace {want!r} shares anything with you;"
                            " list_workspaces lists those that do")
        if acc.member:
            # A key reaches what is shared with its person, not every workspace they
            # are in; one connected there reads and writes it.
            raise ToolError(f"you are a member of {ws['name']} ({want}); connect an agent there"
                            " to read and write it")
        return db.wiki_key(ws["id"]), acc, want

    def shown_files(k: str, acc: shares.Access | None, folder: str = "") -> list[dict]:
        """The wiki's files, cut down to those `acc` may open (shares.visible_files)."""
        stored = files.list_files(conn, k)
        if acc is not None:
            stored = shares.visible_files(conn, k, acc, stored)
        folder = str(folder or "").strip("/")
        return [f for f in stored if not folder or f["path"].startswith(folder + "/")]

    def require_page(pages, path: str) -> Page:
        if path not in pages:
            close = [r[0] for r in conn.execute(
                "SELECT path FROM pages WHERE project=? AND base=? ORDER BY path",
                (pages.project, path.rsplit("/", 1)[-1]))]
            hint = f"; did you mean {', '.join(close[:5])}?" if close else "; list_pages shows what exists"
            raise ToolError(f"no page {path!r}{hint}")
        return pages[path]

    def broken_from(wiki: str, path: str) -> list[str]:
        return [r["target"] for r in conn.execute(
            "SELECT target FROM dangling WHERE project=? AND src=? ORDER BY target", (wiki, path))]

    # One title index per wiki, rebuilt when the wiki has changed since (its
    # updated_at and page count), so reads between writes share it.
    mention_cache: dict[str, tuple[tuple, health.Mentions]] = {}

    def mention_index(k: str) -> health.Mentions:
        proj = conn.execute("SELECT updated_at, pages FROM projects WHERE name=?", (k,)).fetchone()
        stamp = (proj["updated_at"], proj["pages"]) if proj else (0, 0)
        hit = mention_cache.get(k)
        if hit and hit[0] == stamp:
            return hit[1]
        titles = {r["path"]: r["title"] for r in conn.execute(
            "SELECT path, title FROM pages WHERE project=?", (k,))}
        index = health.Mentions(titles)
        mention_cache[k] = (stamp, index)
        return index

    def unlinked(k: str, path: str, text: str, limit: int = 10) -> list[str]:
        """Pages `text` (on page `path`) names by title without a link to them."""
        linked = {r["dst"] for r in conn.execute(
            "SELECT dst FROM edges WHERE project=? AND src=?", (k, path))}
        return mention_index(k).find(text[:MENTION_SCAN_BYTES], path, linked, limit)

    def with_mentions(out: dict, k: str, path: str, text: str | None = None) -> dict:
        """A write result, plus unlinked_mentions when the page names any. `text`
        limits the search to that text (an append's new lines); else the page."""
        if text is None:
            page = db.note(conn, k, path)
            text = page["text"] if page else ""
        found = unlinked(k, path, text)
        if found:
            out["unlinked_mentions"] = found
        return out

    def upkeep(k: str, stale_days: int) -> dict:
        """wiki_health's information beyond links: stale pages, pages named
        without a link across the wiki, and the most wanted missing pages."""
        rows = conn.execute("SELECT path, updated_at, text FROM pages WHERE project=?",
                            (k,)).fetchall()
        edges = [(r["src"], r["dst"]) for r in conn.execute(
            "SELECT src, dst FROM edges WHERE project=?", (k,))]
        linked: dict[str, set[str]] = {}
        for src, dst in edges:
            linked.setdefault(src, set()).add(dst)
        index = mention_index(k)
        per_page = {}
        meta = []
        for r in rows:
            text = r["text"] or ""
            meta.append({"path": r["path"], "updated_at": r["updated_at"],
                         "stale_after": health.stale_after(text)})
            found = index.find(text[:MENTION_SCAN_BYTES], r["path"], linked.get(r["path"], set()),
                               limit=50)
            if found:
                per_page[r["path"]] = found
        stale = health.stale_pages(meta, edges, time.time(), stale_days)
        return {"stale_days": stale_days, "stale": stale[:MAX_STALE_LISTED],
                "unlinked_mentions": health.mention_summary(per_page)}

    def change(row, wiki: str, op, fn, note: str | None, agent: str) -> tuple[dict, dict]:
        """Apply fn(pages) -> result and write what it changed, under the write lock.
        `pages` reads pages only as fn asks for them, and only the pages fn sets or
        deletes are written (db.apply_changes), so a write costs the same in a wiki
        of thirty pages or thirty thousand. `wiki` is the wiki's key (the_wiki)."""
        with db.LOCK:
            pages = db.PageSet(conn, wiki)
            result = fn(pages)
            stats = db.apply_changes(conn, wiki, pages.changes, source="mcp",
                                     author=row["name"], op=op, note=note, agent=agent,
                                     user_id=db.person_of(conn, row))
        return result, stats

    def summary(stats: dict) -> dict:
        return {k: stats[k] for k in ("pages", "links", "dangling")}

    def restore_hint(k: str, path: str) -> dict:
        """For a page just deleted: the revision holding its last text and how to
        bring it back, so an agent (or the person it works for) can undo the delete."""
        rev = db.restore_point(conn, k, path)
        if rev is None:
            return {}
        return {"restore_revision": rev,
                "restore": f"read_page with revision={rev}, then write_page with that text"}

    public = os.environ.get("DEXIO_PUBLIC_URL", "https://app.dexio.wiki").rstrip("/")

    def page_url(row, path: str | None = None, anchor: str = "", handle: str = "") -> str:
        """Where a person opens the page in the web app (app.py wiki_address), for
        agents to hand on; the wiki's own address without a path. `anchor` is a
        section's (parse.heading_anchors), the same id the web view gives it. A
        workspace has one wiki, so the address names no wiki (2026-09-28). handle:
        another workspace's, for a page read there."""
        url = f"{public}/w/{handle or db.handle_of(conn, row['workspace_id'])}"
        if path:
            url += "/" + quote(path, safe="/")
        return url + ("#" + quote(anchor, safe="") if anchor else "")

    def file_at(k: str, path: str):
        """The file at path when there is no page there, else None."""
        if not files.looks_like_file(path) or db.note(conn, k, _norm_path(path)):
            return None
        return files.get(conn, k, files.norm_path(path))

    def file_result(f: dict, offset: int | None, limit: int | None) -> CallToolResult:
        """A file for read_page: a JSON header with a short-lived download_url, then
        the image itself, or the text pulled out of a document (sliced like a page)."""
        meta = {"path": f["path"], "file": True, "size": f["size"],
                "content_type": f["content_type"], "sha256": f["sha256"],
                "updated": _iso(f["updated_at"]), "agent": f["agent"] or "",
                "download_url": files.download_url(conn, f, auth.secret_key(conn), public),
                "download_url_expires_in": files.URL_TTL}
        if files.image_bytes_ok(f):
            import base64
            data = base64.b64encode(files.open_bytes(f)).decode()
            return CallToolResult(content=[
                TextContent(type="text", text=json.dumps(meta, ensure_ascii=False, indent=2)),
                ImageContent(type="image", data=data, mimeType=f["content_type"])])
        text = f["text"]
        if text is None:
            meta["hint"] = "no text to show for this type of file; download_url fetches it"
            return _page_result(meta, None)
        total = len(keep_lines(text))
        first = max(1, int(offset or 1))
        last = min(total, first + int(limit) - 1) if limit else total
        body = line_slice(text, first, last)
        if len(body.encode("utf-8")) > READ_MAX_BYTES:
            stop, used = first - 1, 0
            for line in keep_lines(body):
                used += len(line.encode("utf-8"))
                if used > READ_MAX_BYTES and stop >= first:
                    break
                stop += 1
            body = line_slice(text, first, stop)
            meta.update(truncated=True, next_offset=stop + 1 if stop < last else None)
            last = stop
        meta.update(text_lines=[first, last], total_lines=total)
        return _page_result(meta, body)

    def findings(wiki: str, long_page_words: int = LONG_PAGE_WORDS) -> dict:
        g = db.graph(conn, wiki)
        inbound: dict[str, int] = {}
        for link in g["links"]:
            inbound[link["target"]] = inbound.get(link["target"], 0) + 1
        orphans = sorted(n["id"] for n in g["nodes"] if n["degree"] == 0)
        unref = sorted(n["id"] for n in g["nodes"]
                       if inbound.get(n["id"], 0) == 0 and n["degree"] > 0)
        hubs = sorted(g["nodes"], key=lambda n: (-n["degree"], n["id"]))[:5]
        long_pages = sorted((n for n in g["nodes"] if n["words"] > long_page_words),
                            key=lambda n: (-n["words"], n["id"]))
        return {
            "stats": g["stats"],
            "problems": len(g["dangling"]),
            "dangling": g["dangling"],
            # Information, not problems: agents find pages by search and listing, so
            # a page nothing links to is fine (raw sources, archives, logs).
            "orphaned": orphans,
            "unreferenced": unref,
            "long_pages": [{"path": n["id"], "words": n["words"]} for n in long_pages],
            "most_connected": [{"path": n["id"], "links": n["degree"]} for n in hubs],
        }

    def section_range(text: str, section: str) -> tuple[int, int, dict]:
        try:
            return find_section(text, section)
        except ValueError as e:
            raise ToolError(str(e)) from None

    # ---- the changes themselves, shared by the one-page tools and change_pages --
    def do_write(pages, p: str, body: str, base_version: str | None) -> dict:
        size = _check_size(body)
        _check_base(pages, p, base_version)
        created = p not in pages
        pages[p] = db.make_page(p, body, None if created else pages[p].file)
        return {"created": created, "version": db.version_of(body), **size}

    def do_edit(pages, p: str, old_s: str, new_s: str, section: str | None,
                replace_all: bool, base_version: str | None) -> dict:
        page = require_page(pages, p)
        _check_base(pages, p, base_version)
        text = page.text
        out: dict = {}
        if section:
            first, last, head = section_range(text, section)
            out["section"] = head["heading"]
            if not old_s:
                body = replace_lines(text, first, last, new_s)
                out.update(lines_replaced=[first, last], replacements=1)
            else:
                lines = keep_lines(text)
                before, part, after = ("".join(lines[:first - 1]), "".join(lines[first - 1:last]),
                                       "".join(lines[last:]))
                n = part.count(old_s)
                if n == 0:
                    raise ToolError(f"old_text was not found in section {head['heading']!r} of"
                                    f" {p!r}; read_page with that section shows its text")
                if n > 1 and not replace_all:
                    raise ToolError(f"old_text appears {n} times in section {head['heading']!r};"
                                    " add surrounding text so it matches once, or set replace_all")
                part = part.replace(old_s, new_s) if replace_all else part.replace(old_s, new_s, 1)
                body = before + part + after
                out["replacements"] = n if replace_all else 1
        else:
            if not old_s:
                raise ToolError("give old_text (the exact text to replace), or section (a heading)"
                                " to replace that whole section")
            n = text.count(old_s)
            if n == 0:
                raise ToolError(f"old_text was not found in {p!r}; read_page shows its current"
                                " text (whitespace and line breaks must match exactly)")
            if n > 1 and not replace_all:
                raise ToolError(f"old_text appears {n} times in {p!r}; add surrounding text"
                                " so it matches once, pass section to search one section only,"
                                " or set replace_all")
            body = text.replace(old_s, new_s) if replace_all else text.replace(old_s, new_s, 1)
            out["replacements"] = n if replace_all else 1
        size = _check_size(body)
        pages[p] = db.make_page(p, body, page.file)
        out["version"] = db.version_of(body)
        return {**out, **size}

    def do_append(pages, p: str, add: str) -> dict:
        if not add:
            raise ToolError("text is required")
        created = p not in pages
        old = "" if created else pages[p].text
        body = old + ("\n" if old and not old.endswith("\n") else "") + add
        size = _check_size(body)
        pages[p] = db.make_page(p, body, None if created else pages[p].file)
        return {"created": created, "version": db.version_of(body), "words": len(body.split()),
                **size}

    def do_delete(pages, p: str, base_version: str | None) -> dict:
        if file_at(pages.project, p):
            raise ToolError(f"{p!r} is an uploaded file; delete_file removes it (permanently)")
        require_page(pages, p)
        _check_base(pages, p, base_version)
        del pages[p]
        return {"deleted": p}

    def do_move(pages, k: str, old: str, new: str, update_links: bool) -> dict:
        if old == new:
            raise ToolError("path and new_path are the same")
        if file_at(k, old):
            raise ToolError(f"{old!r} is an uploaded file, and files are not moved: upload it"
                            " at the new path with upload_file, then delete_file the old one")
        require_page(pages, old)
        if new in pages:
            raise ToolError(f"{new!r} already exists; delete or move it first")
        # Links are resolved against the wiki as it is before this move, earlier
        # changes in the same batch included. Only pages with a link whose target,
        # joined path or name could be `old` can need rewriting; the links table
        # knows which stored pages those are, and pending pages are checked too.
        before = db.PendingIndex(conn, k, dict(pages.changes))
        rewritten = {}
        if update_links:
            cands = {r[0] for r in conn.execute(
                "SELECT DISTINCT src FROM links WHERE project=? AND (dst=? OR target=? OR rel=?"
                " OR base=?)", (k, old, old, old, basename(old)))}
            cands |= {p for p, pg in pages.changes.items() if pg is not None}
            for src in sorted(cands):
                if src not in pages:
                    continue
                pg = pages[src]
                text, n = rewrite_links(pg.text, src, old, new, before)
                if n:
                    rewritten[src] = n
                    pages[src] = db.make_page(src, text, pg.file)
        moved_text = pages[old].text
        del pages[old]
        pages[new] = db.make_page(new, moved_text)
        return {"from": old, "to": new, "links_rewritten": sum(rewritten.values()),
                "pages_updated": sorted((new if s == old else s) for s in rewritten)}

    def predicted_breaks(pages, k: str) -> tuple[dict, list]:
        """With pages.changes applied but not written: the broken links on each
        changed page, and links on untouched pages that a removal would break."""
        index = db.PendingIndex(conn, k, pages.changes)
        changed, removed = pages.diff()
        broken = {}
        for p in changed:
            bad = sorted({t for _pos, t, rel, base in page_links(p, pages.changes[p].text)
                          if resolve_keys(t, rel, base, index) is None})
            if bad:
                broken[p] = bad
        elsewhere = []
        for chunk in [removed[i:i + 400] for i in range(0, len(removed), 400)]:
            marks = ",".join("?" * len(chunk))
            for r in conn.execute(f"SELECT src, target, rel, base FROM links WHERE project=?"
                                  f" AND dst IN ({marks}) ORDER BY src, pos", (k, *chunk)):
                if r["src"] in pages.changes:
                    continue
                if resolve_keys(r["target"], r["rel"], r["base"], index) is None:
                    elsewhere.append({"page": r["src"], "target": r["target"]})
        return broken, elsewhere

    # ---- read ------------------------------------------------------------
    @server.tool(annotations=ann(READ, "List pages"))
    def list_pages(ctx: Context, folder: str = "",
                   limit: int = LIST_PAGES_DEFAULT, offset: int = 0,
                   workspace: Workspace = "") -> dict:
        """List the wiki's pages, or one folder's, with title, a one-line description
        (frontmatter description: if set, else the first sentence), word count and last
        change, in path order, 200 at a time. When more remain it gives next_offset and
        the subfolders with their page counts. Uploaded files are in list_files."""
        row = caller(ctx)
        k, acc, handle = reading(row, workspace)
        prefix = str(folder or "").strip("/")
        limit = max(1, min(int(limit), LIST_PAGES_MAX))
        offset = max(0, int(offset))
        paths = db.page_paths(conn, k, prefix)
        if acc is None:
            rows = db.page_list(conn, k, prefix, limit=limit, offset=offset)
        else:
            paths = [p for p in paths if acc.sees(p)]
            rows = [r for r in db.page_list(conn, k, prefix)
                    if acc.sees(r["path"])][offset:offset + limit]
        pages = []
        for r in rows:
            p = {"path": r["path"], "title": r["title"]}
            if r["description"]:
                p["description"] = r["description"]
            if r.get("status") and r["status"] != "stable":
                p["status"] = r["status"]
            p.update(words=r["words"], updated=_iso(r["updated_at"]))
            pages.append(p)
        out = {"folder": prefix, "count": len(paths), "pages": pages}
        if acc is not None:
            out = {"workspace": handle, "read_only": True, **out}
        if offset == 0 and len(pages) >= len(paths):
            return out                       # everything, as it has always been
        end = offset + len(pages)
        if end < len(paths):
            out["next_offset"] = end
        out["folders"] = _subfolders(paths, prefix)
        shown = f"pages {offset + 1} to {end} of {len(paths)}" if pages else \
            f"no pages at offset {offset}; there are {len(paths)}"
        more = f"; offset={end} lists the next ones" if end < len(paths) else ""
        narrow = ", or list_pages with one of these folders" if out["folders"] else ""
        out["hint"] = f"{shown}{more}. To find something, search_pages{narrow}."
        return out

    @server.tool(annotations=ann(READ, "List files"))
    def list_files(ctx: Context, folder: str = "", workspace: Workspace = "") -> dict:
        """List the wiki's uploaded files, or one folder's, with size, type and last change."""
        row = caller(ctx)
        k, acc, handle = reading(row, workspace)
        stored = shown_files(k, acc, str(folder or ""))
        out = {"folder": str(folder or "").strip("/"), "count": len(stored),
               "files": [{"path": f["path"], "size": f["size"], "type": f["content_type"],
                          "updated": _iso(f["updated_at"])} for f in stored]}
        return out if acc is None else {"workspace": handle, "read_only": True, **out}

    @server.tool(annotations=ann(READ, "List workspaces"))
    def list_workspaces(ctx: Context) -> dict:
        """This workspace, and the other workspaces that share pages with you, with what
        each shares. Pass one's handle as workspace to list_pages, read_page,
        search_pages or list_files to read it; what is shared is read-only."""
        row = caller(ctx)
        ws = db.workspace(conn, row["workspace_id"]) or {}
        out: dict = {"workspace": {"handle": ws.get("handle"), "name": ws.get("name"),
                                   "access": "read and write",
                                   "url": f"{public}/w/{ws.get('handle')}"}}
        person = db.shared_reach_person(row)
        if person is None:
            out["shared"] = []
            out["hint"] = ("this key reads only its own workspace. Its owner can let it read, not"
                           " change, what other workspaces share with them: Settings > Agents,"
                           f" \"Let it read what's shared with you\" ({public}/settings/agents)")
            return out
        found = []
        for w in shares.shared_with(conn, person):
            acc = shares.access(conn, w["id"], person)
            if not acc.any or acc.member:
                continue
            what: dict = {"wiki": True} if acc.whole else {}
            if not acc.whole:
                what.update({"folders": sorted(acc.folders)} if acc.folders else {})
                what.update({"pages": sorted(acc.pages)} if acc.pages else {})
            found.append({"handle": w["handle"], "name": w["name"], "access": "read",
                          "url": f"{public}/w/{w['handle']}", "shared": what})
        out["shared"] = found
        if not found:
            out["hint"] = ("nothing is shared with you by email yet. A page open to anyone with"
                           " the link can be read too: pass its address as workspace")
        return out

    @server.tool(annotations=ann(READ, "Check wiki health"))
    def wiki_health(ctx: Context, long_page_words: int = LONG_PAGE_WORDS,
                    stale_days: int = health.STALE_DAYS) -> dict:
        """Check the wiki: broken links (the only problems), and as information: missing
        pages most linked to (wanted), pages that may be stale (past their stale_after
        date, or unchanged stale_days while pages they link to changed), pages named
        without a link (unlinked_mentions), pages over long_page_words, the most linked
        pages, and pages with no links."""
        row = caller(ctx)
        k = the_wiki(row)
        proj = conn.execute("SELECT updated_at FROM projects WHERE name=?", (k,)).fetchone()
        limit = max(100, int(long_page_words))
        found = findings(k, limit)
        return {"last_change": _iso(proj["updated_at"]),
                "long_page_words": limit, **found, "wanted": health.wanted(found["dangling"]),
                **upkeep(k, max(1, int(stale_days)))}

    @server.tool(annotations=ann(READ, "Read page"))
    def read_page(path: str, ctx: Context, revision: int | None = None,
                  section: str | None = None, offset: int | None = None,
                  limit: int | None = None, outline: bool = False,
                  workspace: Workspace = "") -> CallToolResult:
        """Read a page: a JSON header (version, to pass back as base_version; links in
        and out; broken links), then the markdown as plain text. Read part of a long page
        with outline=true (headings and line numbers), section (one heading's section), or
        offset and limit (lines). One read returns at most 128 KB; next_offset continues.
        revision reads a past version from page_history. On an uploaded file's path it
        returns the image, or a document's text."""
        row = caller(ctx)
        k, acc, handle = reading(row, workspace)
        if acc is not None and revision is not None:
            raise ToolError("history is for the workspace's members; read the page as it is now")
        f = file_at(k, path) if revision is None else None
        if f and acc is not None and f["path"] not in {s["path"] for s in shown_files(k, acc)}:
            raise ToolError(f"no page {_norm_path(path)!r}")
        if f:
            return file_result(f, offset, limit)
        p = _norm_path(path)
        if revision is not None:
            rev = db.revision(conn, k, int(revision))
            if not rev or rev["path"] != p:
                raise ToolError(f"no revision {revision} of {p!r}; page_history lists them")
            if rev["text"] is None:
                raise ToolError(f"revision {revision} is the {rev['op']} that removed {p!r};"
                                " read an earlier one")
            text = rev["text"]
            meta = {"path": p, "revision": rev["id"], "op": rev["op"],
                    "agent": rev["agent"] or "",
                    "person": db.people(conn, [rev["user_id"]]).get(rev["user_id"], ""),
                    "author": rev["author"], "at": _iso(rev["at"]),
                    "note": rev["note"] or "",
                    "version": db.version_of(text), "words": len(text.split())}
        else:
            page = db.note(conn, k, p) if acc is None or acc.sees(p) else None
            if not page:
                close = db.search(conn, k, p.rsplit("/", 1)[-1], limit=5)
                hint = ", ".join(r["path"] for r in close if acc is None or acc.sees(r["path"]))
                raise ToolError(f"no page {p!r}" + (f"; similar: {hint}" if hint else ""))
            text = page["text"]
            links_out = [r["dst"] for r in conn.execute(
                "SELECT DISTINCT dst FROM edges WHERE project=? AND src=? ORDER BY dst", (k, p))]
            links_in = [r["src"] for r in conn.execute(
                "SELECT DISTINCT src FROM edges WHERE project=? AND dst=? ORDER BY src", (k, p))]
            if acc is None:
                meta = {"path": p, "url": page_url(row, p), "title": page["title"],
                        "words": page["words"],
                        "version": db.version_of(text), "updated": _iso(page["updated_at"]),
                        "links_out": links_out, "links_in": links_in,
                        "broken_links": broken_from(k, p)}
            else:
                # Another workspace's page: links only to what it also shares, and no
                # broken links, which would say which unshared pages exist.
                meta = {"workspace": handle, "read_only": True, "path": p,
                        "url": page_url(row, p, handle=handle), "title": page["title"],
                        "words": page["words"], "updated": _iso(page["updated_at"]),
                        "links_out": [x for x in links_out if acc.sees(x)],
                        "links_in": [x for x in links_in if acc.sees(x)]}
            st = okf.status(okf.fields(text)) if text.startswith("---") else None
            if st:
                meta["status"] = st
        total = len(keep_lines(text))
        meta["total_lines"] = total
        heads = page_outline(text)
        if outline:
            meta["outline"] = heads
            return _page_result(meta, None)
        if section and (offset is not None or limit is not None):
            raise ToolError("pass section, or offset and limit, not both")
        first, last = 1, total
        if section:
            first, last, head = section_range(text, section)
            meta.update(section=head["heading"], lines=[first, last], outline=heads)
            anchor = heading_anchors(text, meta.get("title")).get(first)
            if anchor and revision is None:
                meta["url"] = page_url(row, p, anchor, handle=handle if acc is not None else "")
        elif offset is not None or limit is not None:
            first = max(1, int(offset or 1))
            if total and first > total:
                raise ToolError(f"offset {first} is past the page's last line ({total})")
            count = int(limit) if limit is not None else total - first + 1
            if count < 1:
                raise ToolError("limit must be at least 1 (outline=true lists headings only)")
            last = min(total, first + count - 1)
            meta.update(lines=[first, last], outline=heads)
        elif meta["words"] > LONG_PAGE_WORDS:
            meta["outline"] = heads
            meta["hint"] = ("long page: read_page with section (a heading) or offset and limit"
                            " reads one part")
        body = text if (first, last) == (1, total) else line_slice(text, first, last)
        if len(body.encode("utf-8")) > READ_MAX_BYTES:
            # Cut at the last whole line that fits; the rest is one call away.
            used, stop = 0, first - 1
            for line in keep_lines(body):
                used += len(line.encode("utf-8"))
                if used > READ_MAX_BYTES and stop >= first:
                    break
                stop += 1
            body = line_slice(text, first, stop)
            meta.update(lines=[first, stop], outline=heads, truncated=True,
                        next_offset=stop + 1 if stop < last else None,
                        hint=f"this read returns lines {first}-{stop} of {last}, up to"
                             f" {READ_MAX_BYTES // 1024} KB; read_page with offset={stop + 1}"
                             " continues, or read one section")
        if revision is None and acc is None:
            named = unlinked(k, p, body)
            if named:
                meta["unlinked_mentions"] = named
        return _page_result(meta, body)

    @server.tool(annotations=ann(READ, "Search pages"))
    def search_pages(query: str, ctx: Context, regex: bool = False,
                     case_sensitive: bool = False, folder: str = "", limit: int = 20,
                     matches_per_page: int = 5, workspace: Workspace = "") -> dict:
        """Search the wiki's paths, titles and text. Matches every word (common words
        dropped, substrings count), best first; "quoted text" matches exactly; regex=true
        takes a Python regular expression. Returns pages with matching lines and line
        numbers. Search before answering about the workspace and before creating a page."""
        row = caller(ctx)
        k, acc, handle = reading(row, workspace)
        q = str(query or "")
        if not q.strip():
            raise ToolError("query is required")
        if len(q) > MAX_QUERY:
            raise ToolError(f"query is longer than {MAX_QUERY} characters")
        limit = max(1, min(int(limit), 100))
        per = max(1, min(int(matches_per_page), 50))
        prefix = str(folder or "")
        stored = files.search_items(conn, k)
        loaded = list(db.load_pages(conn, k).values())
        out: dict = {"query": q, "regex": bool(regex)}
        if acc is not None:
            # Another workspace: only what it shares with this key's person.
            opened = {f["path"] for f in shown_files(k, acc)}
            stored = [s for s in stored if s.path in opened]
            loaded = [pg for pg in loaded if acc.sees(pg.path)]
            out = {"workspace": handle, "read_only": True, **out}
        pages = [*loaded, *stored]
        if regex:
            flags = 0 if case_sensitive else re.IGNORECASE
            try:
                pat = re.compile(q, flags | re.MULTILINE)
            except re.error as e:
                raise ToolError(f"bad regex: {e}") from None
            hits = wordsearch.regex_search(pages, pat, folder=prefix, per_page=per)
        else:
            found = wordsearch.search_pages(pages, q, case_sensitive=case_sensitive,
                                            folder=prefix, per_page=per)
            hits = found["results"]
            out["words"] = found["terms"]
            if hits and not found["matched_all"]:
                out["hint"] = ("no page has every word; these pages have some of them."
                               " Try fewer words, or other words for the same thing.")
        file_paths = {f.path for f in stored}
        for h in hits:
            if h["path"] in file_paths:
                h["file"] = True
        if not hits:
            out["hint"] = ("nothing matched. Try fewer words, or other words for the same"
                           " thing; list_pages shows what exists.")
        return {**out, "pages_matched": len(hits), "results": hits[:limit]}

    @server.tool(annotations=ann(READ, "Page history"))
    def page_history(ctx: Context, path: str | None = None, limit: int = 20,
                     agent: str | None = None) -> dict:
        """A page's revisions, newest first, or without path the wiki's latest
        changes: when, op, agent, person (the account behind it), author (API key or app),
        note, version. agent filters to one agent. Works for deleted pages."""
        row = caller(ctx)
        k = the_wiki(row)
        n = max(1, min(int(limit), 200))
        who = " ".join(str(agent or "").split()) or None
        if not str(path or "").strip():
            revs = db.recent_revisions(conn, k, limit=n, agent=who)
            names = db.people(conn, [r["user_id"] for r in revs])
            return {**({"agent": who} if who else {}), "changes": [
                {"revision": r["id"], "at": _iso(r["at"]), "path": r["path"], "op": r["op"],
                 "agent": r["agent"] or "", "person": names.get(r["user_id"], ""),
                 "author": r["author"], "note": r["note"] or "", "deleted": bool(r["deleted"])}
                for r in revs]}
        p = _norm_path(path)
        revs = db.revisions(conn, k, p, limit=n, agent=who)
        if not revs:
            exists = db.note(conn, k, p) is not None
            if who:
                msg = f"no changes to {p!r} by agent {who!r}"
            else:
                msg = ("no changes recorded since history began" if exists
                       else f"no page {p!r} and no history for it")
            return {"path": p, "revisions": [], "note": msg}
        names = db.people(conn, [r["user_id"] for r in revs])
        return {"path": p, **({"agent": who} if who else {}), "revisions": [
            {"revision": r["id"], "at": _iso(r["at"]), "op": r["op"], "agent": r["agent"] or "",
             "person": names.get(r["user_id"], ""), "author": r["author"],
             "note": r["note"] or "", "deleted": bool(r["deleted"]),
             "version": r["version"] or "", "words": r["words"] or 0}
            for r in revs]}

    # ---- write -----------------------------------------------------------
    @server.tool(annotations=ann(WRITE, "Write page"))
    def write_page(path: str, text: str, ctx: Context, agent: Agent = "",
                   base_version: str | None = None, note: Note = None) -> dict:
        """Create a page or replace its whole text. If a page on the topic exists, use
        edit_page. base_version makes the write fail if the page changed since you read it."""
        row = writer(ctx)
        who = _check_agent(agent)
        k = the_wiki(row)
        p = _norm_path(path)
        body = str(text or "")
        result, stats = change(row, k, "write", lambda pages: do_write(pages, p, body, base_version),
                               _check_note(note), who)
        return with_mentions({"ok": True, "path": p, "url": page_url(row, p), **result,
                              "broken_links": broken_from(k, p), "wiki_stats": summary(stats)},
                             k, p)

    @server.tool(annotations=ann(CHANGE, "Edit page"))
    def edit_page(path: str, new_text: str, ctx: Context, agent: Agent = "",
                  old_text: str = "", section: str | None = None, replace_all: bool = False,
                  base_version: str | None = None, note: Note = None) -> dict:
        """Change part of a page. old_text replaces an exact string, which must be unique
        unless replace_all. section alone replaces that whole section, heading included;
        with old_text, old_text need only be unique within it. Empty new_text deletes."""
        row = writer(ctx)
        who = _check_agent(agent)
        k = the_wiki(row)
        p = _norm_path(path)
        old_s, new_s = str(old_text or ""), str(new_text or "")
        result, stats = change(row, k, "edit", lambda pages: do_edit(
            pages, p, old_s, new_s, section, replace_all, base_version),
            _check_note(note), who)
        return with_mentions({"ok": True, "path": p, "url": page_url(row, p), **result,
                              "broken_links": broken_from(k, p), "wiki_stats": summary(stats)},
                             k, p)

    @server.tool(annotations=ann(CHANGE, "Append to page"))
    def append_page(path: str, text: str, ctx: Context, agent: Agent = "",
                    note: Note = None) -> dict:
        """Add text to the end of a page on a new line, creating the page if needed.
        For logs and running lists."""
        row = writer(ctx)
        who = _check_agent(agent)
        k = the_wiki(row)
        p = _norm_path(path)
        add = str(text or "")
        result, stats = change(row, k, "append", lambda pages: do_append(pages, p, add),
                               _check_note(note), who)
        return with_mentions({"ok": True, "path": p, "url": page_url(row, p), **result,
                              "broken_links": broken_from(k, p), "wiki_stats": summary(stats)},
                             k, p, add)

    @server.tool(annotations=ann(REMOVE, "Delete page"))
    def delete_page(path: str, ctx: Context, agent: Agent = "",
                    base_version: str | None = None, note: Note = None) -> dict:
        """Delete a page. Its history is kept, and the result says how to restore it.
        Returns the pages whose links to it are now broken."""
        row = writer(ctx)
        who = _check_agent(agent)
        k = the_wiki(row)
        p = _norm_path(path)

        def fn(pages):
            do_delete(pages, p, base_version)
            return [r["src"] for r in conn.execute(
                "SELECT DISTINCT src FROM edges WHERE project=? AND dst=? AND src!=?"
                " ORDER BY src", (k, p, p))]

        with db.LOCK:
            linked_from, stats = change(row, k, "delete", fn, _check_note(note), who)
            restore = restore_hint(k, p)
        return {"ok": True, "deleted": p, **restore, "now_broken_on": linked_from,
                "wiki_stats": summary(stats)}

    @server.tool(annotations=ann(CHANGE, "Move page"))
    def move_page(path: str, new_path: str, ctx: Context, agent: Agent = "",
                  update_links: bool = True, note: Note = None) -> dict:
        """Rename or move a page, rewriting the links to it (unless update_links is
        false). Fails if new_path exists."""
        row = writer(ctx)
        who = _check_agent(agent)
        k = the_wiki(row)
        old, new = _norm_path(path), _norm_path(new_path)
        result, stats = change(row, k, "move", lambda pages: do_move(
            pages, k, old, new, update_links), _check_note(note), who)
        shares.follow_move(conn, row["workspace_id"], old, new)
        return {"ok": True, **result, "url": page_url(row, new),
                "broken_links": broken_from(k, new), "wiki_stats": summary(stats)}

    @server.tool(annotations=ann(WRITE, "Upload file"))
    def upload_file(path: str, ctx: Context, agent: Agent = "",
                    note: Note = None) -> dict:
        """Get a one-time URL for uploading a file (image, PDF, deck, anything up to
        100 MB) to path, e.g. raw/deck.pdf, then PUT the file to it from a shell:
        curl -T FILE URL. It works once, for 15 minutes, and replaces a file at path.
        Pages link to files by path."""
        row = writer(ctx)
        who = _check_agent(agent)
        k = the_wiki(row)
        try:
            fp = files.norm_path(path)
        except files.FileError as e:
            raise ToolError(str(e)) from None
        limit = files.storage_limit(conn, row["workspace_id"])
        used = files.usage(conn, row["workspace_id"])
        if limit is not None and used >= limit:
            raise ToolError(f"this workspace's storage is full ({files.human(used)} of"
                            f" {files.human(limit)}); delete files or move to a larger plan")
        token = files.new_upload(conn, k, row["workspace_id"], fp, author=row["name"],
                                 agent=who, user_id=db.person_of(conn, row),
                                 note=_check_note(note))
        url = f"{public}/api/v1/upload/{token}"
        return {"ok": True, "path": fp, "upload_url": url, "method": "PUT",
                "expires_in": files.UPLOAD_TTL, "max_bytes": files.MAX_FILE_BYTES,
                "storage_left": None if limit is None else max(0, limit - used),
                "command": f"curl -sS --fail-with-body -T FILE '{url}'"}

    @server.tool(annotations=ann(REMOVE, "Delete file"))
    def delete_file(path: str, ctx: Context) -> dict:
        """Delete an uploaded file. Permanent: files keep no history."""
        row = writer(ctx)
        k = the_wiki(row)
        f = file_at(k, path)
        if not f:
            if db.note(conn, k, _norm_path(path)):
                raise ToolError(f"{path!r} is a page, not a file; delete_page removes it")
            try:
                fp = files.norm_path(path)
            except files.FileError as e:
                raise ToolError(str(e)) from None
            raise ToolError(f"no file {fp!r}; list_files shows what exists")
        files.delete(conn, k, f["path"])
        return {"ok": True, "deleted": f["path"]}

    @server.tool(annotations=ann(CHANGE, "Change several pages"))
    def change_pages(changes: ChangeList, ctx: Context, agent: Agent = "",
                     dry_run: bool = False, note: Note = None) -> dict:
        """Apply up to 200 changes as one step, all or none: write, edit, append,
        delete and move, each with its one-page tool's fields, run in order. dry_run
        reports what would change and which links would break, writing nothing."""
        row = writer(ctx)
        who = _check_agent(agent)
        k = the_wiki(row)
        note_s = _check_note(note)
        items = list(changes or [])
        if not items:
            raise ToolError("changes is empty")
        if len(items) > MAX_BATCH:
            raise ToolError(f"at most {MAX_BATCH} changes in one call")
        results: list[dict] = []
        ops: dict[str, list[str]] = {}

        def run(pages) -> None:
            for i, c in enumerate(items, 1):
                c = c if isinstance(c, Change) else Change.model_validate(c)
                p = _norm_path(c.path)
                before = dict(pages.changes)
                try:
                    if c.op == "write":
                        if c.text is None:
                            raise ToolError("write needs text")
                        res = do_write(pages, p, c.text, c.base_version)
                    elif c.op == "edit":
                        if c.new_text is None:
                            raise ToolError("edit needs new_text (empty to delete)")
                        res = do_edit(pages, p, c.old_text or "", c.new_text, c.section,
                                      c.replace_all, c.base_version)
                    elif c.op == "append":
                        res = do_append(pages, p, c.text or "")
                    elif c.op == "delete":
                        res = do_delete(pages, p, c.base_version)
                    else:
                        if not c.new_path:
                            raise ToolError("move needs new_path")
                        res = do_move(pages, k, p, _norm_path(c.new_path), c.update_links)
                except ToolError as e:
                    raise ToolError(f"change {i} ({c.op} {p}) failed, so nothing was written:"
                                    f" {e}") from None
                for q, pg in pages.changes.items():
                    if q not in before or before[q] is not pg:
                        seen = ops.setdefault(q, [])
                        if c.op not in seen:
                            seen.append(c.op)
                results.append({"change": i, "op": c.op, "path": p, **res})

        with db.LOCK:
            pages = db.PageSet(conn, k)
            run(pages)
            broken, elsewhere = predicted_breaks(pages, k)
            changed, removed = pages.diff()
            if dry_run:
                return {"ok": True, "dry_run": True, "applied": False,
                        "results": results, "pages_changed": changed, "pages_removed": removed,
                        "broken_links": broken, "now_broken_elsewhere": elsewhere}
            stats = db.apply_changes(conn, k, pages.changes, source="mcp", author=row["name"],
                                     op={q: "+".join(v) for q, v in ops.items()}, note=note_s,
                                     agent=who, user_id=db.person_of(conn, row))
            removed = set(stats["removed"])
            for r in results:
                if r["op"] == "delete" and r["path"] in removed:
                    r.update(restore_hint(k, r["path"]))
            # A moved page keeps its shares (shares.follow_move), in batch order.
            for c in items:
                c = c if isinstance(c, Change) else Change.model_validate(c)
                if c.op == "move" and c.new_path:
                    shares.follow_move(conn, row["workspace_id"], _norm_path(c.path),
                                       _norm_path(c.new_path))
        broken = {p: b for p in stats["changed"] if (b := broken_from(k, p))}
        return {"ok": True, "dry_run": False, "applied": True, "results": results,
                "pages_changed": stats["changed"], "pages_removed": stats["removed"],
                "broken_links": broken, "now_broken_elsewhere": elsewhere,
                "wiki_stats": summary(stats)}

    # ---- visibility ------------------------------------------------------
    # Forrest, 2026-10-01: "does the mcp support changing the visibility of a page or
    # a folder?", then "wire it up". The Share dialog's three levels (shares.py),
    # with the same rules and the same functions behind them. Sharing with a person
    # by email stays in the app: it writes to someone outside the workspace.
    def target_of(row, path: str, kind: str | None) -> tuple[str, str]:
        """(kind, path) for what set_visibility names: the whole wiki for an
        empty path, else the page or folder there; `kind` decides when a page and
        a folder share a path."""
        ws = row["workspace_id"]
        p = "" if not str(path or "").strip().strip("/") else _norm_path(path)
        k = db.wiki_key(ws)
        if kind is None:
            if not p:
                kind = "wiki"
            else:
                page, folder = bool(db.note(conn, k, p)), bool(db.page_paths(conn, k, p))
                if page and folder:
                    raise ToolError(f"{p!r} is both a page and a folder; pass kind \"page\" or"
                                    " \"folder\"")
                if not page and not folder:
                    raise ToolError(f"no page or folder {p!r}; list_pages shows what exists")
                kind = "page" if page else "folder"
        try:
            return shares.norm_target(conn, ws, kind, p)
        except shares.ShareError as e:
            raise ToolError(str(e)) from None

    def visibility_of(row, kind: str, path: str) -> dict:
        """Who can open the target now, counting wider shares (the broadest wins):
        restricted, link or published, and the share it comes from when that is
        a folder or the wiki above it."""
        ws = row["workspace_id"]
        rows = [r for r in conn.execute(
            "SELECT kind, path, email, user_id, listed_at FROM shares WHERE workspace_id=?"
            " ORDER BY id", (ws,)).fetchall() if shares.covers(r["kind"], r["path"], kind, path)]
        pub = [r for r in rows if r["email"] is None and r["user_id"] is None]
        own = next((r for r in pub if (r["kind"], r["path"]) == (kind, path)), None)
        wider = [r for r in pub if (r["kind"], r["path"]) != (kind, path)]
        if own is not None and own["listed_at"]:
            level, via = "published", None
        elif any(r["listed_at"] for r in wider):
            level, via = "published", next(r for r in wider if r["listed_at"])
        elif own is not None:
            level, via = "link", None
        elif wider:
            level, via = "link", wider[0]
        else:
            level, via = "restricted", None
        if kind == "folder":
            url = page_url(row) + "?folder=" + quote(path, safe="/")
        else:
            url = page_url(row, path or None)
        out = {"kind": kind, "path": path, "title": shares.title_of(conn, ws, kind, path),
               "visibility": level, "url": url,
               "shared_with_people": len({r["email"] for r in rows if r["email"] is not None})}
        if own is not None and own["listed_at"]:
            form = shares.listing_form(conn, ws, kind, path, 0)
            out["listing"] = {"title": form["title"], "description": form["description"],
                              "publisher": form["publisher"]}
        if via is not None:
            name = "the wiki" if via["kind"] == "wiki" else f"{via['kind']} {via['path']}"
            out["via"] = {"kind": via["kind"], "path": via["path"],
                          "title": shares.title_of(conn, ws, via["kind"], via["path"])}
            out["hint"] = f"it is {level} because {name} is; set_visibility on that changes it"
        return out

    @server.tool(annotations=ann(WRITE, "Set visibility"))
    def set_visibility(ctx: Context, path: str = "",
                       visibility: Literal["restricted", "link", "published"] | None = None,
                       kind: Literal["page", "folder", "wiki"] | None = None,
                       title: str | None = None, description: str | None = None) -> dict:
        """Who can open a page, a folder (everything under it) or the whole wiki (empty
        path), or change it. restricted: members, and people it was shared with by
        email. link: anyone with its url can read it; search engines are asked not to
        list it. published: public and listed on dexio.wiki, where anyone can find it
        and copy it, and search engines list it; a folder or the wiki only, with title
        and description for the listing. Without visibility it reports and changes
        nothing. A folder's or the wiki's wider setting still applies to what is in it.
        Opening something to the public is for when the person asks for it."""
        row = caller(ctx)
        ws = row["workspace_id"]
        kind_, p = target_of(row, path, kind)
        if visibility is None:
            return {"ok": True, "changed": False, **visibility_of(row, kind_, p)}
        before = visibility_of(row, kind_, p)
        by = db.person_of(conn, row)
        if by is None:
            raise ToolError("this key has no account behind it to make the change as")
        try:
            if visibility == "published":
                shares.publish(conn, ws, kind_, p, True, by, title=title,
                               description=description)
            else:
                own = shares._own_public(conn, ws, kind_, p)
                if visibility == "link" and own is not None and own["listed_at"]:
                    shares.publish(conn, ws, kind_, p, False, by)    # off dexio.wiki, still open
                else:
                    shares.set_public(conn, ws, kind_, p, visibility == "link", by)
        except shares.ShareError as e:
            raise ToolError(str(e)) from None
        after = visibility_of(row, kind_, p)
        return {"ok": True, "changed": after != before, **after}

    # agent defaults to "" in Python so a client that omits it gets _check_agent's
    # message (reconnect to load the current tools) rather than a bare validation
    # error; the schema clients see still lists it as required.
    for name in CHANGE_TOOLS:
        params = server._tool_manager.get_tool(name).parameters
        params["required"] = [*params.get("required", []), "agent"]
    # Pydantic titles every property ("Wiki", "Path"...) and spells each optional
    # as anyOf-with-null: bytes in every agent's context on every turn that say
    # nothing the property name and type do not.
    for tool in server._tool_manager.list_tools():
        _compact_schema(tool.parameters)

    return server


def mcp_asgi(server: MCPServer, conn, resource_metadata_url: str = ""):
    """The streamable-HTTP app for `server`, behind a bearer-token gate."""
    inner = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        stateless_http=True,
        json_response=True,
        # The SDK refuses bodies over 4 MB, under one 5 MB page once JSON escapes
        # it (a newline is two bytes, a non-ASCII character up to six). Requests
        # only get here with a valid token; 32 MB matches the push endpoint.
        max_request_body_size=MAX_REQUEST_BYTES,
        # Host/Origin checks defend unauthenticated local servers against DNS
        # rebinding. This endpoint is public and every request needs a bearer
        # token a browser will never attach, so they add nothing here.
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )

    async def app(scope, receive, send):
        if scope["type"] == "http" and scope["path"].rstrip("/") == MCP_PATH:
            headers = {k.decode("latin-1").lower(): v.decode("latin-1")
                       for k, v in scope.get("headers", [])}
            bearer = _bearer(headers)
            if not (db.check_token(conn, bearer) or oauth.access_row(conn, bearer)):
                body = json.dumps({"error": "unauthorized",
                                   "detail": "send Authorization: Bearer <Dexio API key>"}).encode()
                await send({"type": "http.response.start", "status": 401, "headers": [
                    (b"content-type", b"application/json"),
                    # RFC 9728: tells an OAuth client (Claude, ChatGPT) where to
                    # start; a static-token client just ignores it.
                    (b"www-authenticate", (f'Bearer realm="dexio", resource_metadata='
                                           f'"{resource_metadata_url}"' if resource_metadata_url
                                           else 'Bearer realm="dexio"').encode()),
                ]})
                await send({"type": "http.response.body", "body": body})
                return
            # Stateless mode has no sessions and never pushes server-initiated
            # messages, but the SDK still answers GET by opening an SSE stream
            # that stays silent forever. Clients that probe with GET, or open
            # the optional listening stream after initialize, hang on it and
            # each one holds a connection here. The spec allows 405 instead,
            # and DELETE (session teardown) has nothing to tear down.
            if scope["method"] not in ("POST", "OPTIONS"):
                await send({"type": "http.response.start", "status": 405, "headers": [
                    (b"content-type", b"application/json"),
                    (b"allow", b"POST"),
                ]})
                await send({"type": "http.response.body", "body": json.dumps(
                    {"error": "method_not_allowed",
                     "detail": "stateless server: send JSON-RPC as POST"}).encode()})
                return
        await inner(scope, receive, send)

    return app
