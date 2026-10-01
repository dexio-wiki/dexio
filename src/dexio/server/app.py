"""Dexio server: MCP for agents, the graph and settings in a browser.

Multi-tenant since 2026-09-25: every wiki, token and view belongs to a workspace,
people reach workspaces through memberships, and anyone can sign up for a free
account. Since 2026-09-28 a workspace has exactly one wiki (db.WIKI, keyed
"<workspace id>:main" by db.wiki_key); nothing people or agents see names it.
"""
from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from .. import VERSION, ais, themes
from ..render import server_page
from . import (auth, billing, copies, db, device, erase, files, mail, membership, oauth,
               pages, preview, shares, signups, social)
from . import desktop as desktop_signin
from . import history as page_history
from .ratelimit import Limiter, client_ip

API = "/api/v1"
WS_COOKIE = "dexio_ws"
# Set on the redirect after a new account, read once by /joined (note_signup).
JOINED_COOKIE = "dexio_joined"


def get_app(db_path: str | None = None) -> FastAPI:
    conn = db.connect(db_path)

    # The MCP endpoint's session manager needs a running task group, and a
    # mounted sub-app's own lifespan never runs, so it is started here.
    from .mcp_server import build_mcp, mcp_asgi
    mcp = build_mcp(conn)

    @asynccontextmanager
    async def lifespan(_app):
        async with mcp.session_manager.run():
            yield

    # No generated API docs or schema: the HTTP routes serve the web app, agent sign-in
    # and file transfers, not a public API, and a public listing of every route
    # (billing, Settings forms, the Stripe webhook) helps nobody (Forrest, 2026-09-27).
    app = FastAPI(title="Dexio", version=VERSION, docs_url=None, redoc_url=None,
                  openapi_url=None, lifespan=lifespan)
    app.state.conn = conn

    auth.init(conn)
    oauth.init(conn)
    device.init(conn)
    signups.init(conn)
    files.init(conn)
    # Links to files (raw/deck.pdf) stopped counting as page links when files
    # arrived; recompute stored links once so they stop showing as broken.
    if auth.get_setting(conn, "link_rules") != "files":
        for proj in db.projects(conn):
            db.relink(conn, proj["key"])
        auth.set_setting(conn, "link_rules", "files")
    db.seed_memberships(conn)
    key = auth.secret_key(conn)
    # The public origin: OAuth metadata, the consent redirect and token audience
    # are all absolute URLs, so they cannot come from a request's Host header.
    issuer = os.environ.get("DEXIO_PUBLIC_URL", "https://app.dexio.wiki").rstrip("/")
    terms_url = os.environ.get("DEXIO_TERMS_URL", "https://dexio.wiki/terms")
    privacy_url = os.environ.get("DEXIO_PRIVACY_URL", "https://dexio.wiki/privacy")

    # Seed the operator's account from the environment so a fresh deploy comes
    # up with a usable login. It joins the default workspace as owner.
    seed_email = os.environ.get("DEXIO_ADMIN_EMAIL", "").strip()
    seed_pw = os.environ.get("DEXIO_ADMIN_PASSWORD", "")
    if seed_email and seed_pw and auth.count_users(conn) == 0:
        try:
            uid = auth.create_user(conn, seed_email, seed_pw)
            db.add_member(conn, db.default_workspace(conn), uid, "owner")
        except (ValueError, Exception):  # noqa: B014 - never block startup
            pass
    login_ip = Limiter(30, 600)        # sign-in attempts per address per 10 minutes
    login_email = Limiter(10, 600)     # per account per 10 minutes
    signup_ip = Limiter(5, 3600)       # new accounts per address per hour
    reset_ip = Limiter(5, 3600)        # reset emails per address per hour
    reset_email = Limiter(3, 3600)     # per account per hour
    form_user = Limiter(20, 600)       # password changes, tokens, invites per user
    lookup_ip = Limiter(30, 600)       # email-first lookups per address per 10 minutes
    contact_user = Limiter(5, 3600)    # contact-form messages per person per hour
    device_ip = Limiter(20, 600)       # agent sign-ins started per address per 10 minutes
    device_user = Limiter(30, 600)     # agent codes looked up or answered per user
    copy_user = Limiter(10, 600)       # copies made per person per 10 minutes

    # ---- identity ------------------------------------------------------
    def session_user(request: Request) -> str | None:
        """The account behind a valid session cookie. A cookie issued before the
        account's last password change no longer counts."""
        claims = auth.read_session_claims(key, request.cookies.get(auth.SESSION_COOKIE, ""))
        if claims and auth.session_is_current(conn, *claims):
            return claims[0]
        return None

    def same_origin(request: Request) -> bool:
        """Refuse a state-changing form post that another site submitted. The
        session cookie is SameSite=Lax, which already keeps it off cross-site
        POSTs; this is the second check. Browsers send Origin on every form
        POST; Referer is the fallback, and a request with neither (curl) passes."""
        source = request.headers.get("origin") or request.headers.get("referer") or ""
        if not source:
            return True
        return urlsplit(source).netloc == request.headers.get("host", "")

    def current_user(request: Request) -> dict | None:
        """{id, email} from the session cookie, or from HTTP Basic against a real
        account (scripts and curl need a way in that is not a form)."""
        who = session_user(request)
        if who:
            return auth.user_by_email(conn, who)
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            import base64
            try:
                email, _, pw = base64.b64decode(header[6:]).decode().partition(":")
            except Exception:
                return None
            user = auth.authenticate(conn, email, pw)
            if user:
                return {"id": user["id"], "email": user["email"]}
        return None

    def require_view(request: Request) -> dict:
        """Gate over everything a browser reads. Closed from the first request."""
        user = current_user(request)
        if user:
            return user
        # A browser gets sent to the form; an API client gets JSON it can parse.
        if "text/html" in request.headers.get("accept", ""):
            raise HTTPException(303, "sign in required", headers={
                "Location": f"/login?next={quote(request.url.path)}"})
        raise HTTPException(401, "authentication required",
                            headers={"WWW-Authenticate": 'Basic realm="dexio"'})

    def current_workspace(request: Request, user: dict) -> dict:
        """The workspace this request is about: ?w= (its handle, or its id from an
        older link), else the last one opened (cookie), else the user's first. Only
        workspaces the user belongs to."""
        mine = db.workspaces_for_user(conn, user["id"])
        if not mine:
            raise HTTPException(403, "you do not belong to any workspace")
        for wanted in (request.query_params.get("w"), request.cookies.get(WS_COOKIE)):
            found = db.find_workspace(mine, wanted)
            if found:
                return found
        return mine[0]

    def base_url(request: Request) -> str:
        host = request.headers.get("host", "app.dexio.wiki")
        # Behind Caddy the app itself sees plain http, so anything public is https;
        # a local run keeps whatever scheme it was reached on.
        local = host.startswith(("localhost", "127.0.0.1", "testserver"))
        return (request.url.scheme if local else "https") + "://" + host

    def agent_connected(workspace_id: int) -> bool:
        """The workspace already has an agent: an API key that has reached Dexio, or
        a live Claude or ChatGPT sign-in. Per workspace, not per person: every key
        and sign-in reaches the workspace's wiki, and keys made outside the connect flow
        (an admin's, a fleet's) record no creator (Forrest, 2026-09-27)."""
        return (any(t["last_used"] for t in db.list_tokens(conn, workspace_id))
                or bool(oauth.connections(conn, workspace_id)))

    def _set_session(response: Response, email: str) -> None:
        response.set_cookie(
            auth.SESSION_COOKIE, auth.issue_session(key, email),
            max_age=auth.SESSION_MAX_AGE, httponly=True, samesite="lax",
            # Caddy terminates TLS and everything is redirected to HTTPS, so
            # the cookie should never travel in the clear.
            secure=True, path="/")
        # Every sign-in brings the account's theme to this browser.
        pref = auth.get_theme(conn, email)
        if pref:
            _set_theme(response, pref)

    def _set_theme(response: Response, pref: str) -> None:
        # Readable by script on purpose: the <head> script applies it before
        # first paint. It holds only a theme name.
        response.set_cookie(themes.COOKIE, pref, max_age=365 * 86400, httponly=False,
                            samesite="lax", secure=True, path="/")

    def _sync_theme(request: Request, response: Response, email: str) -> None:
        """Bring a choice made on another device to this one. Set-Cookie lands
        before the page's script reads document.cookie, so it applies on this load."""
        pref = auth.get_theme(conn, email)
        if pref and request.cookies.get(themes.COOKIE) != pref:
            _set_theme(response, pref)

    def _set_ws(response: Response, ws_id: int) -> None:
        # The handle, like every address: a browser never holds the id (db.new_handle).
        response.set_cookie(WS_COOKIE, db.handle_of(conn, ws_id), max_age=365 * 86400,
                            httponly=True,
                            samesite="lax", secure=True, path="/")

    async def form_fields(request: Request) -> dict[str, str]:
        # Parsed by hand rather than with fastapi's Form(), which would drag in
        # python-multipart. HTML forms post urlencoded; urllib handles that.
        fields = parse_qs((await request.body())[:16384].decode("utf-8", "replace"))
        return {k: v[0] for k, v in fields.items() if v}

    def local_next(target: str, default: str = "/") -> str:
        # "/\evil.example" too: browsers read a backslash there as a second slash.
        return target if (target.startswith("/") and not target.startswith("//")
                          and not target.startswith("/\\")) else default

    def create_account_workspace(user: dict, fallback: str = "") -> int:
        """"{first name}'s Workspace" (Forrest, 2026-09-27). Sign-up asks for a first
        name and Google gives one; a GitHub account with no display name uses its
        username (`fallback`), and anything else the email's part before the @."""
        first, _last = auth.names(conn, user["id"])
        who = first[:40] or fallback[:40] or user["email"].split("@", 1)[0][:40] or "My"
        # The workspace comes with its wiki (db.create_workspace).
        return db.create_workspace(conn, f"{who}'s Workspace", user["id"], plan="free")

    # ---- files -----------------------------------------------------------
    def file_caller(request: Request, write: bool = False) -> dict:
        """Who is using a file route and in which workspace: an API key or OAuth
        token for the workspace, else the signed-in browser,
        which must come from this site to change anything."""
        header = request.headers.get("authorization", "")
        if header.lower().startswith("bearer "):
            value = header[7:].strip()
            row = db.check_token(conn, value) or oauth.access_row(conn, value)
            if not row:
                raise HTTPException(403, "unknown API key")
            return {"workspace_id": row["workspace_id"], "author": row["name"],
                    "user_id": db.person_of(conn, row)}
        user = require_view(request)
        if write and not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        return {"workspace_id": current_workspace(request, user)["id"], "author": user["email"],
                "user_id": user["id"]}

    def wiki_for_files(who: dict) -> str:
        """The caller's workspace's wiki. A `wiki` query parameter, from before a
        workspace had one wiki (2026-09-28), is accepted and ignored."""
        return db.ensure_wiki(conn, who["workspace_id"])

    def refuse_if_read_only(ws_id: int) -> None:
        """Stop a change to a read-only workspace (db.read_only_reason)."""
        why = db.read_only_reason(conn, ws_id)
        if why:
            raise HTTPException(403, why)

    async def receive_file(request: Request, k: str, ws_id: int, path: str, *, author: str,
                           agent: str | None, user_id: int | None, note: str | None) -> dict:
        refuse_if_read_only(ws_id)
        declared = request.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) > files.MAX_FILE_BYTES:
            raise HTTPException(413, f"a file can be at most {files.human(files.MAX_FILE_BYTES)}")
        try:
            path = files.norm_path(path)
            sp = files.Spool()
            async for chunk in request.stream():
                sp.add(chunk)
            src, size, sha = sp.file, sp.size, sp.sha256
            if size == 0:
                raise files.FileError("the upload was empty")
            from starlette.concurrency import run_in_threadpool
            try:
                return await run_in_threadpool(
                    files.save, conn, k, ws_id, path, src, size, sha,
                    request.headers.get("content-type", ""), author=author, agent=agent,
                    user_id=user_id, note=note)
            finally:
                src.close()
        except files.FileError as e:
            status = 413 if "at most" in str(e) or "storage" in str(e) else 400
            raise HTTPException(status, str(e)) from None

    @app.put(f"{API}/files")
    async def put_file(request: Request, path: str = Query(...),
                       agent: str = Query(default=""), note: str = Query(default="")):
        """Upload a file to the wiki (the body is the file), replacing any at the path."""
        who = file_caller(request, write=True)
        k = wiki_for_files(who)
        saved = await receive_file(request, k, who["workspace_id"], path, author=who["author"],
                                   agent=agent.strip()[:64] or None, user_id=who["user_id"],
                                   note=note.strip()[:500] or None)
        return {"ok": True, **saved}

    @app.put(API + "/upload/{token}")
    async def put_upload(token: str, request: Request):
        """A one-time upload URL from the upload_file tool: the token is the only
        credential, works once and expires in minutes."""
        grant = files.take_upload(conn, token)
        if not grant:
            raise HTTPException(403, "this upload link is unknown, used or expired; ask"
                                     " upload_file for a new one")
        if not db.project_exists(conn, grant["project"]):
            raise HTTPException(404, "the wiki no longer exists")
        saved = await receive_file(request, grant["project"], grant["workspace_id"],
                                   grant["path"], author=grant["author"] or "",
                                   agent=grant["agent"], user_id=grant["user_id"],
                                   note=grant["note"])
        return {"ok": True, **saved}

    @app.get(f"{API}/files")
    def get_file(request: Request, path: str = Query(...), download: bool = Query(default=False)):
        """Redirect to a short-lived URL for the file's bytes, on another origin.
        A browser that is not a member gets a file only when something shared
        with it shows or holds that file (shares.visible_files)."""
        path = path.strip().strip("/")
        if request.headers.get("authorization", "").lower().startswith("bearer "):
            k = wiki_for_files(file_caller(request))
            row = files.get(conn, k, path)
        else:
            _user, acc, k = reader(request)
            row = files.get(conn, k, path)
            if row and not acc.member and row["path"] not in {
                    f["path"] for f in shares.visible_files(conn, k, acc,
                                                            files.list_files(conn, k))}:
                row = None
        if not row:
            raise HTTPException(404, f"no file {path}")
        return RedirectResponse(files.download_url(conn, row, key, base_url(request), download),
                                status_code=302, headers={"Cache-Control": "no-store"})

    @app.delete(f"{API}/files")
    def delete_file(request: Request, path: str = Query(...)):
        who = file_caller(request, write=True)
        refuse_if_read_only(who["workspace_id"])
        k = wiki_for_files(who)
        if not files.delete(conn, k, path.strip().strip("/")):
            raise HTTPException(404, f"no file {path}")
        return {"ok": True, "deleted": path}

    # ---- export ----------------------------------------------------------
    # Export only: bulk import stays removed (Forrest 2026-09-27). Export came
    # back 2026-09-28 (Forrest) so a customer can always take their pages with them.
    @app.get(f"{API}/export")
    def export(request: Request):
        """The whole wiki as a zip of markdown files at their paths, named for the
        workspace."""
        who = file_caller(request)
        k = wiki_for_files(who)
        data = db.export_zip(conn, k)
        name = (db.workspace(conn, who["workspace_id"]) or {}).get("name") or "dexio"
        # ASCII only: a header is Latin-1, and a workspace name can be anything.
        safe = "".join(c if (c.isascii() and c.isalnum()) or c in "._-" else "-"
                       for c in name).strip("-._")
        safe = "-".join(part for part in safe.split("-") if part)[:60] or "dexio"
        return Response(data, media_type="application/zip", headers={
            "Content-Disposition": f'attachment; filename="{safe}.zip"',
            "Cache-Control": "no-store"})

    @app.get(API + "/files/raw/{token}")
    def raw_file(token: str):
        """The folder store's download route (development): a signed, expiring
        token names the file. The hosted service uses S3 URLs instead."""
        got = files.check_signed(key, token)
        if not got:
            raise HTTPException(403, "this link is invalid or expired")
        row = conn.execute("SELECT * FROM files WHERE id=?", (got[0],)).fetchone()
        if not row:
            raise HTTPException(404, "not found")
        name = row["path"].rsplit("/", 1)[-1]
        return Response(files.open_bytes(row), media_type=row["content_type"], headers={
            "Content-Disposition": ("inline" if got[1] else "attachment")
            + f"; filename*=UTF-8''{quote(name, safe='')}",
            "Content-Security-Policy": "sandbox; default-src 'none'; img-src 'self'",
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=300"})

    # ---- read ----------------------------------------------------------
    @app.get(f"{API}/workspaces")
    def workspaces_(request: Request, user=Depends(require_view)):
        # Handles, not ids, as everywhere a browser sees a workspace (db.new_handle).
        return {"current": current_workspace(request, user)["handle"],
                "workspaces": [{"id": w["handle"], "name": w["name"], "plan": w["plan"],
                                "role": w["role"]}
                               for w in db.workspaces_for_user(conn, user["id"])]}

    def reader(request: Request) -> tuple[dict | None, shares.Access, str]:
        """Who is reading which workspace's wiki, and how much of it they may see.
        A member sees all of it, as before; anyone else (signed in or not) sees
        what is shared with them (shares.py), named by ?w=<handle>. Nothing
        shared, and not signed in: the sign-in gate, as before. Signed in with
        nothing shared: 404, the same as a workspace that does not exist."""
        user = current_user(request)
        wanted = request.query_params.get("w")
        if user:
            mine = db.workspaces_for_user(conn, user["id"])
            found = db.find_workspace(mine, wanted) if wanted else None
            if found or (mine and not wanted):
                ws = found or current_workspace(request, user)
                return user, shares.Access(ws["id"], ws["role"], whole=True), \
                    db.ensure_wiki(conn, ws["id"])
        target = db.workspace_by_handle(conn, wanted) if wanted else None
        if target:
            acc = shares.access(conn, target["id"], user["id"] if user else None)
            if acc.any:
                return user, acc, db.wiki_key(target["id"])
        if not user:
            require_view(request)             # raises: the sign-in gate
        if not wanted:
            current_workspace(request, user)  # raises: no workspace at all
        raise HTTPException(404, "not found")

    def members_only(acc: shares.Access, what: str) -> None:
        if not acc.member:
            raise HTTPException(403, f"{what} is for members of the workspace")

    @app.get(f"{API}/graph")
    def graph_(request: Request, response: Response, project: str = Query(default="")):
        _user, acc, k = reader(request)
        data = shares.restrict_graph(db.graph(conn, k), acc)
        data["files"] = [{"path": f["path"], "size": f["size"], "type": f["content_type"]}
                         for f in shares.visible_files(conn, k, acc, files.list_files(conn, k))]
        if not data["nodes"] and not data["files"]:
            raise HTTPException(404, "no pages yet")
        data["project"] = db.WIKI
        if not acc.member:
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        return data

    @app.get(f"{API}/note")
    def note(request: Request, response: Response, project: str = Query(default=""),
             path: str = Query(...)):
        _user, acc, k = reader(request)
        row = db.note(conn, k, path) if acc.sees(path) else None
        if not row:
            raise HTTPException(404, "not found")
        # created, last changed and by whom, for the page panel's meta line
        info = page_history.page_info(conn, k, path, row)
        if not acc.member:
            # A guest sees when, not who: the names and addresses of the people
            # and agents behind the wiki stay inside the workspace.
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
            c = info.get("created")
            info = {"exists": info["exists"], "updated_at": info["updated_at"],
                    "created": {"at": c["at"], "exact": c["exact"]} if c else None}
        return {**row, "info": info}

    @app.get(f"{API}/page-history")
    def page_history_(request: Request, project: str = Query(default=""), path: str = Query(...),
                      before: int | None = Query(default=None), limit: int = Query(default=50)):
        """A page's revisions, newest first, and its created and edited dates.
        Works for a deleted page too."""
        _user, acc, k = reader(request)
        members_only(acc, "page history")
        out = page_history.page_revisions(conn, k, path, limit=limit, before=before)
        if before is None:
            out["info"] = page_history.page_info(conn, k, path)
        if not out["revisions"] and before is None and not out["info"]["exists"]:
            raise HTTPException(404, f"no page {path} and no history for it")
        return out

    @app.get(f"{API}/revision")
    def revision_(request: Request, project: str = Query(default=""), path: str = Query(...),
                  id: int = Query(...)):
        """One revision of a page: its text, and the lines it changed."""
        _user, acc, k = reader(request)
        members_only(acc, "page history")
        out = page_history.revision_view(conn, k, path, id)
        if not out:
            raise HTTPException(404, f"no revision {id} of {path}")
        return out

    @app.get(f"{API}/search")
    def search(request: Request, project: str = Query(default=""), q: str = Query(...)):
        _user, acc, k = reader(request)
        found = db.search(conn, k, q, limit=40 if acc.member else 400)
        return {"results": [h for h in found if acc.sees(h["path"])][:40]}

    @app.get(f"{API}/history")
    def history(request: Request, project: str = Query(default="")):
        _user, acc, k = reader(request)
        members_only(acc, "the wiki's history")
        return {"pushes": db.history(conn, k)}

    # ---- sharing (shares.py) ------------------------------------------------
    def sharer(request: Request) -> tuple[dict, dict]:
        """The signed-in member sharing something from the workspace ?w= names."""
        user = require_view(request)
        mine = db.workspaces_for_user(conn, user["id"])
        wanted = request.query_params.get("w")
        ws = db.find_workspace(mine, wanted) if wanted else current_workspace(request, user)
        if not ws:
            raise HTTPException(403, "only members of the workspace can share from it")
        return user, ws

    def share_dialog(ws: dict, user: dict, kind: str, path: str) -> dict:
        try:
            out = shares.dialog(conn, ws["id"], kind, path, user["id"])
        except shares.ShareError as e:
            raise HTTPException(404, str(e)) from None
        # A page's address, or the wiki's; a folder's is the wiki narrowed to it
        # (graph.js, "one folder").
        t = out["target"]
        out["link"] = f"{issuer}/w/{ws['handle']}" + (
            "/" + quote(t["path"], safe="/") if t["kind"] == "page"
            else "?folder=" + quote(t["path"], safe="/") if t["kind"] == "folder" else "")
        return out

    @app.get(f"{API}/share")
    def share_get(request: Request, kind: str = Query(...), path: str = Query(default="")):
        user, ws = sharer(request)
        return share_dialog(ws, user, kind, path)

    async def share_body(request: Request) -> dict:
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        f = await json_or_form(request)
        return f if isinstance(f, dict) else {}

    @app.post(f"{API}/share")
    async def share_add(request: Request):
        """Share with a person by email: to view (anything), or to edit (the whole
        wiki, an owner only), which is a member invite."""
        user, ws = sharer(request)
        f = await share_body(request)
        kind, path = str(f.get("kind") or ""), str(f.get("path") or "")
        role = str(f.get("role") or "viewer")
        to = auth.email_key(str(f.get("email") or ""))
        if "@" not in to or len(to) > 254 or any(ch.isspace() for ch in to):
            raise HTTPException(400, "Enter an email address.")
        if not form_user.allow(f"share:{user['id']}"):
            raise HTTPException(429, "Too many shares at once. Wait a few minutes.")
        if any(auth.email_key(m["email"]) == to for m in db.members(conn, ws["id"])):
            raise HTTPException(400, f"{to} is a member of {ws['name']} and can already see"
                                     " everything in it.")
        if role == "editor":
            if kind != "wiki" or ws["role"] != "owner":
                raise HTTPException(403, "Only an owner can add editors, and only to the whole"
                                         " wiki. Editors are members of the workspace.")
            limit = db.member_limit(conn, ws["id"])
            if limit is not None and len(db.members(conn, ws["id"])) >= limit:
                raise HTTPException(400, "Editors are members, and this workspace's plan has"
                                         " room for no more. Choose a plan under Settings, Plan.")
            if (db.workspace(conn, ws["id"]) or {}).get("billing_ends_at"):
                raise HTTPException(400, "This workspace moves to Free soon, and Free is for one"
                                         " person. To add editors, keep the plan under Plan.")
            if not await send_invite(request, user, ws, to):
                raise HTTPException(502, f"We could not send the invite to {to}. Try again in"
                                         " a few minutes.")
            return share_dialog(ws, user, kind, path)
        try:
            made = shares.share_with(conn, ws["id"], kind, path, to, user["id"])
        except shares.ShareError as e:
            raise HTTPException(400, str(e)) from None
        if made["code"]:
            from starlette.concurrency import run_in_threadpool
            first, last = auth.names(conn, user["id"])
            name = auth.display_name(first, last, user["email"])
            k_, p_ = shares.norm_target(conn, ws["id"], kind, path)
            sent = await run_in_threadpool(
                mail.share, to, name, k_, shares.title_of(conn, ws["id"], k_, p_), ws["name"],
                f"{base_url(request)}/s/{made['code']}", user["email"])
            if not sent and mail.enabled():
                if made["new"]:
                    shares.remove(conn, ws["id"], made["id"])
                raise HTTPException(502, f"We could not send the email to {to}. Try again in a"
                                         " few minutes.")
        return share_dialog(ws, user, kind, path)

    @app.post(f"{API}/share/public")
    async def share_public(request: Request):
        """Open a wiki, folder or page to anyone with the link, or close it."""
        user, ws = sharer(request)
        f = await share_body(request)
        kind, path = str(f.get("kind") or ""), str(f.get("path") or "")
        on = f.get("on") in (True, "1", "true", "on", 1)
        try:
            shares.set_public(conn, ws["id"], kind, path, on, user["id"])
        except shares.ShareError as e:
            raise HTTPException(400, str(e)) from None
        return share_dialog(ws, user, kind, path)

    @app.post(f"{API}/share/listed")
    async def share_listed(request: Request):
        """List something public in the directory on dexio.wiki, where anyone can
        find it and make a copy, or take it out (shares.set_listed)."""
        user, ws = sharer(request)
        f = await share_body(request)
        kind, path = str(f.get("kind") or ""), str(f.get("path") or "")
        on = f.get("on") in (True, "1", "true", "on", 1)
        try:
            shares.set_listed(conn, ws["id"], kind, path, on, by=user["id"],
                              title=f.get("title"), description=f.get("description"))
        except shares.ShareError as e:
            raise HTTPException(400, str(e)) from None
        return share_dialog(ws, user, kind, path)

    @app.get(f"{API}/publish")
    def publish_get(request: Request, kind: str = Query(...), path: str = Query(default="")):
        """What the Publish dialog shows for a wiki or folder (shares.publish_state)."""
        user, ws = sharer(request)
        try:
            return shares.publish_state(conn, ws["id"], kind, path, user["id"])
        except shares.ShareError as e:
            raise HTTPException(400, str(e)) from None

    @app.post(f"{API}/publish")
    async def publish_post(request: Request):
        """Publish a wiki or folder on dexio.wiki (public, listed, with its name,
        description and author), or unpublish it."""
        user, ws = sharer(request)
        f = await share_body(request)
        kind, path = str(f.get("kind") or ""), str(f.get("path") or "")
        on = f.get("on") in (True, "1", "true", "on", 1)
        try:
            # No author: it is the name on the account (shares.author_of).
            shares.publish(conn, ws["id"], kind, path, on, user["id"], title=f.get("title"),
                           description=f.get("description"), publisher=f.get("publisher"))
            return shares.publish_state(conn, ws["id"], kind, path, user["id"])
        except shares.ShareError as e:
            raise HTTPException(400, str(e)) from None

    @app.get(f"{API}/publish/preview.svg")
    def publish_preview(request: Request, kind: str = Query(...), path: str = Query(default="")):
        """The picture the directory would show, for the Publish dialog: members only,
        since nothing may be public yet."""
        _user, ws = sharer(request)
        try:
            kind, path = shares.norm_target(conn, ws["id"], kind, path)
        except shares.ShareError as e:
            raise HTTPException(404, str(e)) from None
        v = shares.counts(conn, ws["id"], kind, path)["version"]
        return Response(preview.picture(conn, ws["id"], kind, path, v),
                        media_type="image/svg+xml",
                        headers={"Cache-Control": "private, max-age=60",
                                 "X-Content-Type-Options": "nosniff"})

    # ---- the directory on dexio.wiki, and making a copy (copies.py) ----------
    @app.get(f"{API}/directory")
    def directory():
        """Everything listed, for dexio.wiki/wikis: read by the site's build and,
        for what was listed since, by the page itself, so any origin may read it.
        Only what owners chose to list, all of it already public."""
        items = shares.directory(conn)
        for it in items:
            for key in ("url", "copy_url", "preview_url"):
                it[key] = issuer + it[key]
        return JSONResponse({"wikis": items}, headers={
            "Access-Control-Allow-Origin": "*", "Cache-Control": "public, max-age=300"})

    @app.get(API + "/directory/{share_id}/preview.svg")
    def directory_preview(share_id: str):
        """A picture of a listed wiki's graph for its card (preview.py). The
        address carries the listing's version, so it can be cached for long."""
        share = shares.listed(conn, share_id)
        info = shares.listing(conn, share) if share else None
        if not share or not info:
            raise HTTPException(404, "not listed")
        return Response(preview.svg(conn, share, info["version"]), media_type="image/svg+xml",
                        headers={"Cache-Control": "public, max-age=86400",
                                 "Access-Control-Allow-Origin": "*",
                                 "X-Content-Type-Options": "nosniff"})

    def copy_source(from_id) -> tuple[dict, dict]:
        share = shares.listed(conn, from_id)
        info = shares.listing(conn, share) if share else None
        if not share or not info:
            raise HTTPException(404, "That is not listed for copying any more. Its owner may"
                                     " have made it private or taken it out of the directory.")
        return share, {**info, "url": issuer + info["url"]}

    def copy_gone(e: HTTPException) -> HTMLResponse:
        return HTMLResponse(pages.notice_page("Make a copy", str(e.detail),
                                              "https://dexio.wiki/wikis/", "Public wikis"),
                            status_code=e.status_code)

    @app.get("/copy", response_class=HTMLResponse)
    def copy_form(request: Request, from_: str = Query(default="", alias="from"),
                  to: str = Query(default="")):
        """Where to put a copy of something listed. Signed out: sign in or sign
        up first, then back here (the new account's own workspace is empty, so
        it is picked)."""
        try:
            share, info = copy_source(from_)
        except HTTPException as e:
            return copy_gone(e)
        user = current_user(request)
        if not user:
            here = f"/copy?from={share['id']}"
            return RedirectResponse(f"/signup?next={quote(here, safe='')}", status_code=303)
        dests = copies.destinations(conn, user["id"])
        response = HTMLResponse(pages.copy_page(info, user["email"], dests, share["id"],
                                                choose=to))
        response.headers["X-Robots-Tag"] = "noindex"
        return response

    @app.post("/copy")
    async def copy_submit(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        user = require_view(request)
        f = await form_fields(request)
        try:
            share, info = copy_source(f.get("from"))
        except HTTPException as e:
            return copy_gone(e)
        dests = copies.destinations(conn, user["id"])
        to = (f.get("to") or "").strip()

        def again(error: str, status: int = 400) -> HTMLResponse:
            return HTMLResponse(pages.copy_page(info, user["email"], dests, share["id"],
                                                choose=to, error=error), status_code=status)

        if not copy_user.allow(f"copy:{user['id']}"):
            return again("Too many copies at once. Wait a few minutes and try again.", 429)
        made_new = None
        if to == "new":
            ws_id = db.create_workspace(conn, info["title"], user["id"], plan="free", named=True)
            made_new = ws_id
        else:
            ws = db.find_workspace(dests, to)
            if not ws:
                return again("Pick a workspace to copy it into.")
            ws_id = ws["id"]
        from starlette.concurrency import run_in_threadpool
        try:
            done = await run_in_threadpool(copies.copy, conn, share, ws_id, user=user,
                                           source_url=info["url"])
        except copies.CopyError as e:
            # Refused before anything was written: a workspace made for it goes again.
            if made_new:
                with db.LOCK, conn:
                    erase._drop_workspace(conn, made_new)
            return again(str(e))
        except files.FileError as e:
            return again(f"The pages were copied, but a file could not be: {e}")
        handle = db.handle_of(conn, ws_id)
        there = f"/w/{handle}"
        if share["kind"] == "folder":
            there += "?folder=" + quote(share["path"], safe="/")
        elif share["kind"] == "page" and done["pages"]:
            there += "/" + quote(share["path"], safe="/")
        response = RedirectResponse(there, status_code=303)
        _set_ws(response, ws_id)
        return response

    @app.delete(f"{API}/share/{{share_id}}")
    def share_remove(request: Request, share_id: int, kind: str = Query(...),
                     path: str = Query(default="")):
        """Stop sharing with one person (a share on this target or one above it)."""
        user, ws = sharer(request)
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        if not shares.remove(conn, ws["id"], share_id):
            raise HTTPException(404, "that share was already removed")
        return share_dialog(ws, user, kind, path)

    @app.get("/s/{code}")
    def share_link(request: Request, code: str):
        """The link in a share email: binds the share to the account that opens
        it (signing in or up first) if the address is that account's, then
        opens what was shared. Another account is told whose address it was
        sent to, masked, and offered a way to sign in as someone else."""
        who = session_user(request)
        user = auth.user_by_email(conn, who) if who else None
        if not user:
            return RedirectResponse(f"/login?next={quote('/s/' + code)}", status_code=303)
        try:
            got = shares.accept(conn, code, user["id"])
        except shares.WrongAccount as e:
            return HTMLResponse(pages.notice_page(
                "Shared link", f"This link was sent to {e.sent_to}, and you are signed in as"
                f" {user['email']}. Open it where you use that address, or sign in with it"
                " here.", "/logout?next=" + quote("/s/" + code, safe=""),
                "Sign in with a different account"), status_code=403)
        except shares.ShareError as e:
            return HTMLResponse(pages.notice_page("Shared link", str(e), "/", "Open Dexio"),
                                status_code=400)
        there = f"/w/{db.handle_of(conn, got['workspace_id'])}"
        if got["kind"] == "page":
            there += "/" + quote(got["path"], safe="/")
        elif got["kind"] == "folder":            # the graph narrowed to it
            there += "?folder=" + quote(got["path"], safe="/")
        return RedirectResponse(there, status_code=303)

    # ---- sign in, sign up ----------------------------------------------
    def keep_source(request: Request, response):
        """First touch: remember the referrer or campaign tags that brought this
        browser, until an account is created (see signups.py)."""
        if signups.COOKIE not in request.cookies:
            src = signups.touch(dict(request.query_params), request.headers.get("referer"))
            if src:
                response.set_cookie(signups.COOKIE, signups.encode(src), max_age=signups.MAX_AGE,
                                    httponly=True, samesite="lax", secure=True, path="/")
        return response

    def note_signup(request: Request, response, user_id: int, method: str, next_url: str):
        signups.record(conn, user_id, method, next_url, request.cookies.get(signups.COOKIE))
        response.delete_cookie(signups.COOKIE, path="/")
        # Analytics counts the new account on /joined, a one-time hop to where the
        # person was going (pages.joined_page). Not for someone joining a team through
        # an invite (a teammate, not someone we acquired), not for the desktop app's
        # hand-off page, and not when they have declined analytics.
        if (isinstance(response, RedirectResponse) and not next_url.startswith("/invite/")
                and request.cookies.get(pages.CONSENT_COOKIE) != "denied"):
            response.headers["location"] = "/joined?" + urlencode({"next": next_url})
            response.set_cookie(JOINED_COOKIE, method, max_age=600, httponly=True,
                                samesite="lax", secure=True, path="/joined")

    @app.get("/joined", response_class=HTMLResponse)
    def joined(request: Request, next: str = Query(default="/")):
        next_ = local_next(next)
        method = request.cookies.get(JOINED_COOKIE, "")
        if method in ("password", "google", "github") and \
                request.cookies.get(pages.CONSENT_COOKIE) != "denied":
            response = HTMLResponse(pages.joined_page(next_, method))
        else:
            # Opened again, bookmarked or declined: nothing to record.
            response = RedirectResponse(next_, status_code=303)
        response.delete_cookie(JOINED_COOKIE, path="/joined")
        return response

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = Query(default="/")):
        if session_user(request):
            return RedirectResponse(local_next(next), status_code=303)
        return keep_source(request, HTMLResponse(pages.auth_page(
            "login", "email", local_next(next), note=agent_note(next),
            social=social_buttons(next))))

    def agent_note(next_url: str) -> str:
        if (next_url or "").startswith("/s/"):
            return pages.SHARE_NOTE
        if (next_url or "").startswith("/copy"):
            return pages.COPY_NOTE
        return pages.AGENT_NOTE if (next_url or "").startswith("/device") else ""

    @app.post("/login/email")
    async def email_step(request: Request):
        """Step two of sign-in or sign-up: what the address needs next. This tells
        anyone whether an address has an account, as sign-up always has; the rate
        limit keeps it from being a cheap way to check many addresses."""
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        f = await form_fields(request)
        mode = "signup" if f.get("mode") == "signup" else "login"
        next_ = local_next(f.get("next", "/"))
        email = auth.email_key(f.get("email", ""))
        note = agent_note(next_)

        def step(name: str, status: int = 200, **kw):
            return HTMLResponse(pages.auth_page(mode, name, next_, email=email, note=note,
                                                terms_url=terms_url, privacy_url=privacy_url,
                                                **kw), status_code=status)

        if not lookup_ip.allow(client_ip(request)):
            return step("email", 429, error="Too many tries from this address. Wait a few "
                        "minutes and try again.", social=social_buttons(next_))
        if "@" not in email or "." not in email.rsplit("@", 1)[-1]:
            return step("email", 400, error="Enter your email address.",
                        social=social_buttons(next_))
        user = auth.user_by_email(conn, email)
        if not user:
            return step("create")
        if auth.has_password(conn, user["id"]):
            return step("password", ok="You already have an account. Sign in to continue."
                        if mode == "signup" else "")
        linked = [p for p in auth.providers(conn, user["id"]) if social.configured(p)]
        return step("social", providers=pages.social_block(
            linked, next_, terms_url=terms_url, privacy_url=privacy_url, terms=False))

    @app.post("/login")
    async def login_submit(request: Request):
        fields = await form_fields(request)
        email = fields.get("email", "")
        password = fields.get("password", "")
        next_ = fields.get("next", "/")
        if not login_ip.allow(client_ip(request)) or \
                not login_email.allow(email.strip().lower()):
            return HTMLResponse(pages.auth_page(
                "login", "password", local_next(next_), email=auth.email_key(email),
                note=agent_note(next_), error="Too many sign-in attempts. Wait a few minutes "
                "and try again."), status_code=429)
        user = auth.authenticate(conn, email, password)
        if not user:
            # Deliberately one message for both cases: a different response for
            # "no such user" would let anyone enumerate accounts.
            return HTMLResponse(pages.auth_page(
                "login", "password", local_next(next_), email=auth.email_key(email),
                note=agent_note(next_), error="Incorrect email or password."), status_code=401)
        response = RedirectResponse(local_next(next_), status_code=303)
        _set_session(response, user["email"])
        return response

    @app.get("/signup", response_class=HTMLResponse)
    def signup_form(request: Request, next: str = Query(default="/")):
        if session_user(request):
            return RedirectResponse(local_next(next), status_code=303)
        next_ = local_next(next)
        return keep_source(request, HTMLResponse(pages.auth_page(
            "signup", "email", next_, note=agent_note(next_), social=social_buttons(next_),
            terms_url=terms_url, privacy_url=privacy_url)))

    @app.post("/signup")
    async def signup_submit(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        f = await form_fields(request)
        email = f.get("email", "").strip()
        next_ = local_next(f.get("next", "/"))

        def fail(message: str, status: int = 400):
            return HTMLResponse(pages.auth_page(
                "signup", "create", next_, email=auth.email_key(email), note=agent_note(next_),
                error=message, first=f.get("first_name", ""), last=f.get("last_name", ""),
                terms_url=terms_url, privacy_url=privacy_url), status_code=status)

        if not signup_ip.allow(client_ip(request)):
            return fail("Too many new accounts from this address. Try again in an hour.", 429)
        if not auth.clean_name(f.get("first_name", "")):
            return fail("Enter your first name.")
        if f.get("agree") != "1":
            return fail("Please agree to the Terms and Privacy Policy to continue.")
        if f.get("password", "") != f.get("confirm_password", ""):
            return fail("The passwords do not match.")
        if auth.user_by_email(conn, email):
            return fail("An account with this email already exists. Sign in instead.")
        try:
            uid = auth.create_user(conn, email, f.get("password", ""),
                                   f.get("first_name", ""), f.get("last_name", ""))
        except ValueError as e:
            return fail(str(e).capitalize() + ".")
        except Exception:
            return fail("An account with this email already exists. Sign in instead.")
        user = {"id": uid, "email": email}
        ws = None
        if not next_.startswith("/invite/"):
            ws = create_account_workspace(user)
        response = RedirectResponse(next_, status_code=303)
        _set_session(response, email)
        if ws:
            _set_ws(response, ws)
        note_signup(request, response, uid, "password", next_)
        return response

    # ---- sign in with Google or GitHub (see social.py) -----------------------
    def social_buttons(next_url: str) -> str:
        return pages.social_block(social.enabled(), local_next(next_url),
                                  terms_url=terms_url, privacy_url=privacy_url)

    @app.get("/auth/start")
    def auth_start(provider: str = Query(default=""), next: str = Query(default="/"),
                   desktop: str = Query(default="")):
        if not social.configured(provider):
            return RedirectResponse("/login", status_code=303)
        # The desktop app opens this in the system browser with its challenge
        # (desktop.py); anything else in that slot is refused, not ignored, so a
        # broken app build fails loudly instead of signing the browser in.
        if desktop and not desktop_signin.valid_challenge(desktop):
            return auth_failed("This sign-in link from the Dexio app is not valid. "
                               "Start again from the app.")
        state, cookie, verifier = social.make_state(key, provider, local_next(next), desktop)
        response = RedirectResponse(social.authorize_url(
            provider, f"{issuer}/auth/{provider}/callback", state, verifier), status_code=303)
        response.set_cookie(social.COOKIE, cookie, max_age=social.STATE_TTL, httponly=True,
                            samesite="lax", secure=True, path="/auth")
        return response

    def auth_failed(message: str, status: int = 400) -> HTMLResponse:
        return HTMLResponse(pages.notice_page("Sign-in failed", message, "/login",
                                              "Try again"), status_code=status)

    @app.get("/auth/{provider}/callback")
    async def auth_callback(request: Request, provider: str, code: str = Query(default=""),
                            state: str = Query(default=""), error: str = Query(default=""),
                            error_description: str = Query(default="")):
        if not social.configured(provider):
            return RedirectResponse("/login", status_code=303)
        if error:
            # Cancelling at Google or GitHub lands here; say so plainly.
            return auth_failed((error_description or error.replace("_", " "))[:300] + ".")
        got = social.read_state(key, provider, state, request.cookies.get(social.COOKIE, ""))
        if got is None or not code:
            return auth_failed("This sign-in expired or was started in another browser. "
                               "Start again.")
        next_, verifier = got
        if not login_ip.allow(client_ip(request)):
            return auth_failed("Too many sign-in attempts. Wait a few minutes and try again.", 429)
        from starlette.concurrency import run_in_threadpool
        try:
            who = await run_in_threadpool(social.exchange, provider, code,
                                          f"{issuer}/auth/{provider}/callback", verifier)
            user, created = auth.user_from_identity(conn, who.provider, who.subject, who.email,
                                                    who.first, who.last)
        except (social.SocialError, ValueError) as e:
            return auth_failed(str(e)[:1].upper() + str(e)[1:] + ".", 502)
        next_ = local_next(next_)
        ws = None
        if created and not next_.startswith("/invite/"):
            ws = create_account_workspace(user, fallback=who.handle)
        challenge = social.state_desktop(key, state)
        if challenge:
            # Started by the desktop app: this browser is only the go-between. The
            # app redeems the code in its own window (GET /auth/desktop).
            code = desktop_signin.issue(key, user["id"], challenge, next_, ws or 0)
            response = HTMLResponse(pages.desktop_handoff_page(desktop_signin.link(code)))
            response.delete_cookie(social.COOKIE, path="/auth")
            if created:
                note_signup(request, response, user["id"], provider, next_)
            return response
        response = RedirectResponse(next_, status_code=303)
        _set_session(response, user["email"])
        if ws:
            _set_ws(response, ws)
        response.delete_cookie(social.COOKIE, path="/auth")
        if created:
            note_signup(request, response, user["id"], provider, next_)
        return response

    @app.get("/auth/desktop")
    def auth_desktop(code: str = Query(default=""), verifier: str = Query(default="")):
        """The desktop app's window finishing a sign-in its browser started."""
        got = desktop_signin.redeem(key, code, verifier)
        row = None
        if got:
            row = conn.execute("SELECT email FROM users WHERE id=?",
                               (got["user_id"],)).fetchone()
        if not got or not row:
            return auth_failed("This sign-in expired. Start again from the app.")
        response = RedirectResponse(local_next(got["next"]), status_code=303)
        _set_session(response, row["email"])
        if got["workspace"]:
            _set_ws(response, got["workspace"])
        return response

    # ---- password reset --------------------------------------------------
    RESET_SENT = ("If an account exists for that email, we sent a link to reset its password. "
                  "It works for an hour.")

    @app.get("/forgot", response_class=HTMLResponse)
    def forgot_form(email: str = Query(default="")):
        return HTMLResponse(pages.forgot_page(email=email.strip()[:254]))

    @app.post("/forgot", response_class=HTMLResponse)
    async def forgot_submit(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        email = (await form_fields(request)).get("email", "").strip()
        if not reset_ip.allow(client_ip(request)) or not reset_email.allow(email.lower()):
            return HTMLResponse(pages.forgot_page(
                error="Too many reset requests. Wait an hour and try again.", email=email),
                status_code=429)
        token = auth.create_reset(conn, email)
        if token:
            user = auth.user_by_email(conn, email)
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(mail.password_reset, user["email"],
                                    f"{base_url(request)}/reset?token={token}")
        # The same answer either way, so the form cannot be used to find accounts.
        return HTMLResponse(pages.forgot_page(message=RESET_SENT))

    @app.get("/reset", response_class=HTMLResponse)
    def reset_form(token: str = Query(default="")):
        email = auth.reset_email(conn, token)
        if not email:
            return HTMLResponse(pages.notice_page(
                "Reset link", "This reset link is invalid, used or expired.", "/forgot",
                "Send a new link"), status_code=400)
        return HTMLResponse(pages.reset_page(token, email))

    @app.post("/reset")
    async def reset_submit(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        f = await form_fields(request)
        token = f.get("token", "")
        email = auth.reset_email(conn, token)
        if not email:
            return HTMLResponse(pages.notice_page(
                "Reset link", "This reset link is invalid, used or expired.", "/forgot",
                "Send a new link"), status_code=400)
        if f.get("new_password", "") != f.get("confirm_password", ""):
            return HTMLResponse(pages.reset_page(token, email, "The passwords do not match."),
                                status_code=400)
        try:
            email = auth.use_reset(conn, token, f.get("new_password", ""))
        except ValueError as e:
            return HTMLResponse(pages.reset_page(token, email, str(e).capitalize() + "."),
                                status_code=400)
        response = RedirectResponse("/", status_code=303)
        _set_session(response, email)
        return response

    @app.post("/logout")
    @app.get("/logout")
    def logout(next: str = Query(default="")):
        """Sign out, then to sign-in: with `next`, sign-in goes on there (a share
        link opened by the wrong account uses this to switch)."""
        there = local_next(next, "")
        response = RedirectResponse(f"/login?next={quote(there)}" if there else "/login",
                                    status_code=303)
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        return response

    # ---- account -------------------------------------------------------
    # Name and password live in Settings, under Profile. /account forwards
    # there, and the two forms still post to /account/...
    def account_user(request: Request) -> dict | None:
        who = session_user(request)
        return auth.user_by_email(conn, who) if who else None

    @app.post("/account/name")
    async def change_name(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        user = account_user(request)
        if not user:
            return RedirectResponse("/login?next=/settings/profile", status_code=303)
        f = await form_fields(request)
        auth.set_names(conn, user["id"], f.get("first_name", ""), f.get("last_name", ""))
        return settings_done("profile", "name")

    @app.get("/account")
    def account(request: Request):
        return RedirectResponse("/settings/profile", status_code=303)

    @app.post("/account/delete")
    async def delete_account(request: Request):
        """Delete the signed-in account (see erase.py). The person types their email
        to confirm; paid workspaces have to be cancelled or handed over first."""
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        user = account_user(request)
        if not user:
            return RedirectResponse("/login?next=/settings/profile", status_code=303)
        f = await form_fields(request)

        def fail(message: str, status: int):
            return render_settings(request, user, settings_workspace(request, user), "profile",
                                   error=message, status=status)

        if not form_user.allow(f"del:{user['id']}"):
            return fail("Too many attempts. Wait a few minutes.", 429)
        if auth.email_key(f.get("confirm_email", "")) != auth.email_key(user["email"]):
            return fail("To delete your account, type your email exactly as shown.", 400)
        try:
            erase.delete_account(conn, user["id"], store=files.store(),
                                 on_seats=lambda w: billing.sync_seats(conn, w))
        except ValueError as e:
            return fail(str(e), 409)
        response = HTMLResponse(pages.notice_page(
            "Account deleted", "Your account and its data are deleted. Copies in backups are "
            "removed as those backups expire.", "https://dexio.wiki", "Go to dexio.wiki",
            status_error=False))
        response.delete_cookie(auth.SESSION_COOKIE, path="/")
        response.delete_cookie(WS_COOKIE, path="/")
        return response

    @app.post("/account/password")
    async def change_password(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        user = account_user(request)
        if not user:
            return RedirectResponse("/login?next=/settings/profile", status_code=303)
        who = user["email"]
        f = await form_fields(request)
        current, new, confirm = (f.get("current_password", ""), f.get("new_password", ""),
                                 f.get("confirm_password", ""))

        def fail(message: str, status: int = 400):
            return render_settings(request, user, settings_workspace(request, user), "profile",
                                   error=message, status=status)

        if not form_user.allow(f"pw:{who.lower()}"):
            return fail("Too many attempts. Wait a few minutes and try again.", 429)
        if not auth.authenticate(conn, who, current):
            return fail("Current password is incorrect.", 401)
        if new != confirm:
            return fail("The new passwords do not match.")
        if new == current:
            return fail("Choose a password different from the current one.")
        try:
            auth.set_password(conn, who, new)
        except ValueError as e:
            return fail(str(e).capitalize() + ".")
        response = settings_done("profile", "password")
        _set_session(response, who)
        return response

    # ---- first run -----------------------------------------------------
    def signed_in_or_redirect(request: Request, here: str):
        who = session_user(request)
        user = auth.user_by_email(conn, who) if who else None
        if not user:
            return None, RedirectResponse(f"/login?next={quote(here)}", status_code=303)
        return user, None

    # ---- connect your AI ------------------------------------------------
    # There is no onboarding page. The graph view shows what to do where the
    # graph would be: while the wiki is empty, the steps to connect an AI, on the
    # graph screen (Forrest, 2026-09-27). Connecting from anywhere else is
    # Settings > Agents (?connect opens its panel).
    @app.get("/welcome")
    def welcome(client: str = Query(default="")):
        """The old first-run page. Links to it (agents.md, bookmarks) open the
        graph view, or Settings > Agents with that AI picked when they named one
        (by way of /?connect, which survives a sign-in)."""
        target = f"/?connect={quote(client)}" if client in pages.CLIENTS else "/"
        return RedirectResponse(target, status_code=303)

    def browser_user(request: Request) -> dict:
        """A signed-in person, by session cookie only: a key is never handed to
        a script signing in with Basic auth."""
        who = session_user(request)
        user = auth.user_by_email(conn, who) if who else None
        if not user:
            raise HTTPException(401, "sign in required")
        return user

    @app.get(f"{API}/connect")
    def connect_steps(request: Request, client: str = Query(...),
                      view: str = Query(default="agents")):
        """The steps for one AI, as HTML for the connect flow. A GET never mints a key.
        view is where they show: "graph" (an empty wiki) or Settings > Agents."""
        browser_user(request)
        if client not in pages.CLIENTS:
            raise HTTPException(400, "unknown client")
        return {"client": client, "html": pages.connect_steps(client, base_url(request), view=view)}

    @app.post(f"{API}/connect")
    async def connect_mint(request: Request):
        """Mint a workspace key for an agent that sets itself up, and return its
        steps with the key in the message."""
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        user = browser_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        body = body if isinstance(body, dict) else {}
        client = str(body.get("client", ""))
        if pages.CLIENTS.get(client, ("", "", ""))[2] != "token":
            raise HTTPException(400, "that agent connects without an API key")
        ws = current_workspace(request, user)
        if not form_user.allow(f"tok:{user['id']}"):
            raise HTTPException(429, "too many API keys created. Wait a few minutes")
        # "Something else" names the tile, not the agent; its key reads "AI agent".
        name = "AI agent" if client == "other" else pages.CLIENTS[client][0]
        token = db.create_token(conn, name, workspace_id=ws["id"], created_by=user["id"])
        return {"client": client,
                "html": pages.connect_steps(client, base_url(request), token=token,
                                            view=str(body.get("view", "")))}

    @app.post(f"{API}/workspace/name")
    async def name_workspace(request: Request):
        """The first graph screen's Name your workspace (Forrest, 2026-09-28): an owner
        names the current workspace, or keeps the name it came with. Either way it
        counts as named and is not asked again (db.rename_workspace)."""
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        user = browser_user(request)
        try:
            body = await request.json()
        except Exception:
            body = {}
        name = str(body.get("name", "") if isinstance(body, dict) else "").strip()
        ws = current_workspace(request, user)
        if ws.get("role") != "owner":
            raise HTTPException(403, "only an owner can name the workspace")
        if not form_user.allow(f"wsname:{user['id']}"):
            raise HTTPException(429, "too many tries. Wait a few minutes")
        try:
            db.rename_workspace(conn, ws["id"], name)
        except ValueError:
            raise HTTPException(400, "give the workspace a name") from None
        return {"id": ws["handle"], "name": name[:80]}

    # ---- settings ------------------------------------------------------
    def billing_view(request: Request, ws: dict) -> dict:
        full = db.workspace(conn, ws["id"]) or {}
        return {"enabled": billing.enabled(), "for_sale": billing.PAID,
                "customer": bool(full.get("stripe_customer")),
                "subscription": bool(full.get("stripe_subscription")),
                "status": full.get("billing_status"), "interval": full.get("billing_interval"),
                "period_end": full.get("billing_period_end"),
                "ends_at": full.get("billing_ends_at"),
                "seats": len(db.members(conn, ws["id"])),
                "just_paid": request.query_params.get("billing") == "done"}

    def settings_workspace(request: Request, user: dict) -> dict | None:
        """The workspace Settings is about, or None for an account in none yet
        (its own sections still open)."""
        return current_workspace(request, user) if db.workspaces_for_user(conn, user["id"]) \
            else None

    def render_settings(request: Request, user: dict, ws: dict | None,
                        section: str = "general", *, error: str = "",
                        ok: str = "", status: int = 200) -> Response:
        """One section of Settings. Only that section's data is read."""
        if ws is None and section in pages.WORKSPACE_ONLY:
            return RedirectResponse("/settings/start", status_code=303)
        if section == "general" and request.query_params.get("billing") == "done":
            # Checkout sessions opened before the Plan section existed come back here.
            return RedirectResponse(f"/settings/plan?w={ws['handle']}&billing=done",
                                    status_code=303)
        if not ok and not error:
            ok = pages.DONE.get(request.query_params.get("done", ""), "")
            if section == "plan" and request.query_params.get("billing") == "done":
                ok = pages.BILLING_DONE
            if ok and ws and request.query_params.get("done") in ("left", "deleted"):
                ok += f" You're now in {ws['name']}."      # escaped with the rest
        data: dict = {}
        if section == "general":
            full = db.workspace(conn, ws["id"]) or {}
            wiki = db.wiki_stats(conn, db.ensure_wiki(conn, ws["id"]))
            data.update(members=db.members(conn, ws["id"]), wiki=wiki,
                        leave_blocker=membership.leave_blocker(conn, ws["id"], user["id"]),
                        delete_blocker=membership.delete_blocker(full))
        elif section == "plan":
            owners = [" ".join(p for p in (m.get("first_name"), m.get("last_name")) if p)
                      or m["email"] for m in db.members(conn, ws["id"]) if m["role"] == "owner"]
            b = billing_view(request, ws)
            b["free_blocker"] = billing.free_blocker(conn, ws, user["id"])
            b["contact"] = request.query_params.get("contact", "")   # opens the contact form
            data.update(billing=b, owners=owners)
        elif section == "members":
            full = db.workspace(conn, ws["id"]) or {}
            billed = bool(full.get("stripe_subscription"))
            data.update(members=db.members(conn, ws["id"]), limit=db.member_limit(conn, ws["id"]),
                        pending=db.pending_invites(conn, ws["id"]),
                        seat_price=pages.PRICES.get(full.get("plan") or "") if billed else None)
        elif section == "agents":
            q = request.query_params
            data.update(tokens=db.list_tokens(conn, ws["id"]),
                        apps=oauth.connections(conn, ws["id"]),
                        base=base_url(request), api=API,
                        connect=q.get("connect") if "connect" in q else None)
        elif section == "sharing":
            data.update(sharing=shares.overview(conn, ws["id"]),
                        team=len(db.members(conn, ws["id"])) > 1)
        elif section == "files":
            per = pages.FILES_PER_PAGE
            total = files.count_files(conn, ws["id"])
            last = max(1, (total + per - 1) // per)
            raw = request.query_params.get("page", "1")
            page = min(max(1, int(raw)), last) if raw.isdigit() else 1
            in_files, in_pages = files.file_usage(conn, ws["id"]), files.page_usage(conn, ws["id"])
            data.update(storage={
                "used": in_files + in_pages, "in_files": in_files, "in_pages": in_pages,
                "limit": files.storage_limit(conn, ws["id"]),
                "files": files.workspace_files(conn, ws["id"], limit=per,
                                               offset=(page - 1) * per),
                "total": total, "page": page, "per_page": per,
                "max_file": files.MAX_FILE_BYTES})
        elif section == "profile":
            first, last = auth.names(conn, user["id"])
            data.update(first=first, last=last, has_password=auth.has_password(conn, user["id"]),
                        providers=auth.providers(conn, user["id"]),
                        erase=erase.plan(conn, user["id"]))
        elif section == "appearance":
            data["theme"] = auth.get_theme(conn, user["email"])
        response = HTMLResponse(pages.settings_page(
            section, user["email"], ws, db.workspaces_for_user(conn, user["id"]),
            error=error, ok=ok, me=user["id"],
            read_only=db.read_only_reason(conn, ws["id"]) if ws else "", **data),
            status_code=status)
        if ws:
            _set_ws(response, ws["id"])
        _sync_theme(request, response, user["email"])
        return response

    def settings_done(section: str, done: str, ws: dict | None = None) -> RedirectResponse:
        """After an action: back to its section, which shows the message. A
        reload then reloads the page rather than repeating the action."""
        query = (f"w={ws['handle']}&" if ws else "") + f"done={done}"
        return RedirectResponse(f"{pages.section_url(section)}?{query}", status_code=303)

    def settings_gone(user: dict, done: str) -> RedirectResponse:
        """After leaving or deleting a workspace: General in the next one, or,
        with none left, Profile (an account section needs no workspace, and
        opening Dexio then makes one)."""
        rest = db.workspaces_for_user(conn, user["id"])
        response = (settings_done("general", done, rest[0]) if rest
                    else settings_done("profile", done))
        response.delete_cookie(WS_COOKIE, path="/")
        return response

    def settings_view(section: str):
        def view(request: Request):
            here = pages.section_url(section) + (f"?{request.url.query}" if request.url.query
                                                 else "")
            user, bounce = signed_in_or_redirect(request, here)
            if bounce:
                return bounce
            return render_settings(request, user, settings_workspace(request, user), section)
        return view

    for _section in pages.SECTIONS:
        app.add_api_route(pages.section_url(_section), settings_view(_section), methods=["GET"],
                          response_class=HTMLResponse)

    async def settings_post(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        user, bounce = signed_in_or_redirect(request, "/settings")
        if bounce:
            return None, None, None, bounce
        return user, settings_workspace(request, user), await form_fields(request), None

    @app.post("/settings/theme")
    async def settings_theme(request: Request):
        """Save the appearance choice to the account. The settings page posts it
        in the background (Accept: application/json); without JavaScript it is a
        plain form post that comes back to the page."""
        user, _ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        theme, mode = f.get("theme", ""), f.get("mode", "")
        if not themes.valid(theme, mode):
            raise HTTPException(400, "unknown theme or mode")
        pref = f"{theme}.{mode}"
        auth.set_theme(conn, user["id"], pref)
        if "application/json" in request.headers.get("accept", ""):
            response = JSONResponse({"theme": theme, "mode": mode})
        else:
            response = RedirectResponse("/settings/appearance", status_code=303)
        _set_theme(response, pref)
        return response

    @app.post("/settings/rename", response_class=HTMLResponse)
    async def settings_rename(request: Request):
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, error="Only an owner can rename it.",
                                   status=403)
        try:
            db.rename_workspace(conn, ws["id"], f.get("name", ""))
        except ValueError as e:
            return render_settings(request, user, ws, error=str(e).capitalize() + ".",
                                   status=400)
        return settings_done("general", "renamed", ws)

    @app.post("/settings/publisher", response_class=HTMLResponse)
    async def settings_publisher(request: Request):
        """The workspace's publisher name on dexio.wiki (shares.set_publisher)."""
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, error="Only an owner can set the"
                                   " publisher name.", status=403)
        try:
            shares.set_publisher(conn, ws["id"], f.get("publisher", ""))
        except shares.ShareError as e:
            return render_settings(request, user, ws, error=str(e), status=400)
        return settings_done("general", "publisher", ws)

    @app.get("/settings/data")
    @app.get("/settings/wikis")
    def settings_wikis_moved(request: Request):
        """Settings had a Wikis section (and before 2026-09-27 a Wikis and files one)
        until a workspace came to have one wiki (2026-09-28). Its download link is
        under General now, so old links land there."""
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/settings{query}", status_code=308)

    @app.get("/settings/workspaces")
    def settings_workspaces_moved(request: Request):
        """Settings had a Workspaces section until 2026-09-27; the workspace menu
        lists and creates workspaces now. Old links land on General."""
        query = f"?{request.url.query}" if request.url.query else ""
        return RedirectResponse(f"/settings{query}", status_code=308)

    @app.post("/settings/workspaces")
    async def settings_new_workspace(request: Request):
        """New workspace, from the workspace menu (graph and Settings)."""
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not form_user.allow(f"ws:{user['id']}"):
            return render_settings(request, user, ws, "general" if ws else "profile", status=429,
                                   error="Too many workspaces created. Wait a few minutes.")
        typed = f.get("name", "").strip()
        new = db.create_workspace(conn, typed, user["id"], plan="free", named=bool(typed))
        response = RedirectResponse("/", status_code=303)
        _set_ws(response, new)
        return response

    @app.post("/settings/tokens/{token_id}/revoke", response_class=HTMLResponse)
    async def settings_revoke(request: Request, token_id: int):
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if ws and db.delete_token(conn, token_id, ws["id"]):
            return settings_done("agents", "revoked", ws)
        return render_settings(request, user, ws, "agents", status=404,
                               error="No such API key in this workspace.")

    @app.post("/settings/sharing/{share_id}/stop", response_class=HTMLResponse)
    async def settings_unshare(request: Request, share_id: int):
        """Settings > Sharing: stop sharing with a person, or make something
        private. Any member may, as any member may in the Share dialog."""
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws:
            return render_settings(request, user, ws, "sharing")
        ov = shares.overview(conn, ws["id"])
        was = next((it for it in ov["public"] + ov["people"] if it["id"] == share_id), None)
        if not was or not shares.stop(conn, ws["id"], share_id):
            return render_settings(request, user, ws, "sharing", status=404,
                                   error="That was already unshared.")
        done = "extra" if was["via"] else "unshared" if "email" in was else "private"
        return settings_done("sharing", done, ws)

    async def send_invite(request: Request, user: dict, ws: dict, to: str) -> str:
        """Email a new invite to `to`. The link goes only in the email: if it cannot
        be sent, the new invite is withdrawn and "" comes back. On success, any
        earlier invite to the same address is withdrawn (its link stops working)
        and the ?done= key comes back: "invited", or "resent" if one was pending."""
        code = db.create_invite(conn, ws["id"], user["id"], email=to)
        from starlette.concurrency import run_in_threadpool
        sender = auth.display_name(*auth.names(conn, user["id"]), user["email"])
        sent = await run_in_threadpool(mail.invite, to, sender, ws["name"],
                                       f"{base_url(request)}/invite/{code}", user["email"])
        if not sent:
            db.delete_invite(conn, code)
            return ""
        return "resent" if db.withdraw_invites(conn, ws["id"], to, keep=code) else "invited"

    @app.post("/settings/invite", response_class=HTMLResponse)
    async def settings_invite(request: Request):
        """Invite someone by email. Inviting an address that already has a pending
        invite sends it a fresh one."""
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce

        def fail(message: str, status: int):
            return render_settings(request, user, ws, "members", error=message, status=status)

        if not ws or ws["role"] != "owner":
            return fail("Only an owner can invite.", 403)
        to = (f.get("email") or "").strip()
        if "@" not in to or len(to) > 254 or any(ch.isspace() for ch in to):
            return fail("Enter the email address of the person to invite.", 400)
        people = db.members(conn, ws["id"])
        if any(auth.email_key(m["email"]) == auth.email_key(to) for m in people):
            return fail(f"{to} is already a member.", 400)
        limit = db.member_limit(conn, ws["id"])
        if limit is not None and len(people) >= limit:
            return fail("This workspace is at its member limit for its plan.", 400)
        ends = (db.workspace(conn, ws["id"]) or {}).get("billing_ends_at")
        if ends:
            return fail(f"This workspace moves to Free on {pages._day(ends)}, and Free is for one"
                        " person. To invite people, keep the plan under Plan.", 400)
        if not form_user.allow(f"inv:{user['id']}"):
            return fail("Too many invites. Wait a few minutes.", 429)
        done = await send_invite(request, user, ws, to)
        if not done:
            return fail(f"We could not send the invite to {to}. Try again in a few minutes.", 502)
        return settings_done("members", done, ws)

    @app.post("/settings/invites/{invite_id}/resend", response_class=HTMLResponse)
    async def settings_resend_invite(request: Request, invite_id: int):
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, "members", status=403,
                                   error="Only an owner can resend an invite.")
        pending = db.pending_invite(conn, ws["id"], invite_id)
        if not pending:
            return render_settings(request, user, ws, "members", status=404,
                                   error="That invite was used, cancelled or has expired.")
        if not form_user.allow(f"inv:{user['id']}"):
            return render_settings(request, user, ws, "members", status=429,
                                   error="Too many invites. Wait a few minutes.")
        if not await send_invite(request, user, ws, pending["email"]):
            return render_settings(request, user, ws, "members", status=502,
                                   error=f"We could not send the invite to {pending['email']}."
                                         " Try again in a few minutes.")
        return settings_done("members", "resent", ws)

    @app.post("/settings/invites/{invite_id}/cancel", response_class=HTMLResponse)
    async def settings_cancel_invite(request: Request, invite_id: int):
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, "members", status=403,
                                   error="Only an owner can cancel an invite.")
        if not db.cancel_invite(conn, ws["id"], invite_id):
            return render_settings(request, user, ws, "members", status=404,
                                   error="That invite was used, cancelled or has expired.")
        return settings_done("members", "cancelled", ws)

    # ---- members: roles, removal, leaving, deleting (see membership.py) ----
    async def sync_seats(ws_id: int) -> None:
        from starlette.concurrency import run_in_threadpool
        await run_in_threadpool(billing.sync_seats, conn, ws_id)

    @app.post("/settings/members/{member_id}/role", response_class=HTMLResponse)
    async def settings_role(request: Request, member_id: int):
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, "members", status=403,
                                   error="Only an owner can change roles.")
        try:
            membership.set_role(conn, ws["id"], member_id, f.get("role", ""), user["id"])
        except ValueError as e:
            return render_settings(request, user, ws, "members", error=str(e), status=400)
        return settings_done("members", "role", ws)

    @app.post("/settings/members/{member_id}/remove", response_class=HTMLResponse)
    async def settings_remove_member(request: Request, member_id: int):
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, "members", status=403,
                                   error="Only an owner can remove people.")
        try:
            membership.remove_member(conn, ws["id"], member_id, user["id"])
        except ValueError as e:
            return render_settings(request, user, ws, "members", error=str(e), status=400)
        await sync_seats(ws["id"])
        return settings_done("members", "removed", ws)

    @app.post("/settings/leave", response_class=HTMLResponse)
    async def settings_leave(request: Request):
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws:
            return RedirectResponse("/settings/start", status_code=303)
        try:
            membership.leave(conn, ws["id"], user["id"])
        except ValueError as e:
            return render_settings(request, user, ws, error=str(e), status=400)
        await sync_seats(ws["id"])
        return settings_gone(user, "left")

    @app.post("/settings/delete", response_class=HTMLResponse)
    async def settings_delete_workspace(request: Request):
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, status=403,
                                   error="Only an owner can delete the workspace.")
        if not form_user.allow(f"wsdel:{user['id']}"):
            return render_settings(request, user, ws, status=429,
                                   error="Too many attempts. Wait a few minutes.")
        try:
            membership.delete_workspace(conn, ws["id"], f.get("confirm_name", ""),
                                        store=files.store())
        except ValueError as e:
            return render_settings(request, user, ws, error=str(e), status=400)
        return settings_gone(user, "deleted")

    # ---- billing ---------------------------------------------------------
    @app.post("/billing/checkout")
    async def billing_checkout(request: Request):
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, error="Only an owner can change the plan.",
                                   status=403)
        full = {**ws, **(db.workspace(conn, ws["id"]) or {})}
        try:
            from starlette.concurrency import run_in_threadpool
            url = await run_in_threadpool(billing.checkout_url, conn, full, user["email"],
                                          f.get("plan", ""), base_url(request))
        except billing.BillingError as e:
            return render_settings(request, user, ws, error=str(e).capitalize() + ".",
                                   status=400)
        return RedirectResponse(url, status_code=303)

    @app.post("/billing/change")
    async def billing_change(request: Request):
        """Team to Business or back, on the subscription the workspace already has."""
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, "plan",
                                   error="Only an owner can change the plan.", status=403)
        full = {**ws, **(db.workspace(conn, ws["id"]) or {})}
        try:
            from starlette.concurrency import run_in_threadpool
            done = await run_in_threadpool(billing.change_plan, conn, full, f.get("plan", ""))
        except billing.BillingError as e:
            return render_settings(request, user, ws, "plan", error=str(e).capitalize() + ".",
                                   status=400)
        return settings_done("plan", done, ws)

    def _billing_error(e: Exception) -> str:
        msg = str(e)
        return msg[:1].upper() + msg[1:] + ("" if msg.endswith(".") else ".")

    @app.post("/billing/free")
    async def billing_free(request: Request):
        """Move to Free at the end of the paid period, or at once for a plan set by
        hand, once the owner is the only member (see billing.free_blocker)."""
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, "plan",
                                   error="Only an owner can change the plan.", status=403)
        full = {**ws, **(db.workspace(conn, ws["id"]) or {})}
        try:
            from starlette.concurrency import run_in_threadpool
            done = await run_in_threadpool(billing.move_to_free, conn, full, user["id"])
        except billing.BillingError as e:
            return render_settings(request, user, ws, "plan", error=_billing_error(e), status=400)
        return settings_done("plan", done, ws)

    @app.post("/billing/keep")
    async def billing_keep(request: Request):
        """Undo a scheduled move to Free."""
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, "plan",
                                   error="Only an owner can change the plan.", status=403)
        full = {**ws, **(db.workspace(conn, ws["id"]) or {})}
        try:
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(billing.keep_plan, conn, full)
        except billing.BillingError as e:
            return render_settings(request, user, ws, "plan", error=_billing_error(e), status=400)
        return settings_done("plan", "kept", ws)

    @app.post("/settings/contact")
    async def settings_contact(request: Request):
        """The contact forms in Settings: emails us (mail.contact) with Reply-To set
        to the person who wrote, and the workspace, plan and member count attached.
        Plan's form (billing and plan moves) is for owners; Help's (about=help, added
        2026-09-29) is for anyone signed in, with or without a workspace. Replaced the
        Plan page's mailto links on 2026-09-27 (Forrest: they did nothing)."""
        user, ws, f, bounce = await settings_post(request)
        if bounce:
            return bounce
        is_help = f.get("about", "") == "help"
        section = "help" if is_help else "plan"
        if not is_help and (not ws or ws["role"] != "owner"):
            return render_settings(request, user, ws, "plan",
                                   error="Only an owner can write to us from here.", status=403)
        message = (f.get("message") or "").replace("\r\n", "\n").strip()
        if not message:
            return render_settings(request, user, ws, section, error="Write a message.",
                                   status=400)
        if len(message) > 5000:
            return render_settings(request, user, ws, section,
                                   error="That message is over 5,000 characters.", status=400)
        if not contact_user.allow(str(user["id"])):
            return render_settings(request, user, ws, section,
                                   error="Too many messages. Try again in an hour.", status=429)
        about = pages.CONTACT_ABOUT.get(f.get("about", ""), "Billing")
        name = " ".join(p for p in auth.names(conn, user["id"]) if p)
        if ws:
            full = {**ws, **(db.workspace(conn, ws["id"]) or {})}
            where = (full["name"], ws["id"], (full.get("plan") or "free").title(),
                     len(db.members(conn, ws["id"])))
            link = f"{base_url(request)}/settings/plan?w={ws['handle']}"
        else:
            where = ("no workspace", 0, "None", 0)
            link = f"{base_url(request)}/settings/help"
        from starlette.concurrency import run_in_threadpool
        sent = await run_in_threadpool(mail.contact, user["email"], name, *where, about,
                                       message, link)
        if not sent:
            return render_settings(request, user, ws, section, status=502,
                                   error="That did not go through. Try again, or write to"
                                         f" {pages.SUPPORT_EMAIL}.")
        return settings_done(section, "contact_sent", ws)

    @app.post("/billing/portal")
    async def billing_portal(request: Request):
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if not ws or ws["role"] != "owner":
            return render_settings(request, user, ws, error="Only an owner can manage billing.",
                                   status=403)
        full = {**ws, **(db.workspace(conn, ws["id"]) or {})}
        try:
            from starlette.concurrency import run_in_threadpool
            url = await run_in_threadpool(billing.portal_url, full, base_url(request))
        except billing.BillingError as e:
            return render_settings(request, user, ws, error=str(e).capitalize() + ".",
                                   status=400)
        return RedirectResponse(url, status_code=303)

    @app.post("/stripe/webhook")
    async def stripe_webhook(request: Request):
        payload = await request.body()
        try:
            event = billing.verify(payload, request.headers.get("stripe-signature", ""),
                                   os.environ.get("STRIPE_WEBHOOK_SECRET", ""))
        except ValueError as e:
            raise HTTPException(400, f"invalid signature: {e}") from None
        try:
            from starlette.concurrency import run_in_threadpool
            result = await run_in_threadpool(billing.handle_event, conn, event)
        except billing.BillingError as e:
            # A 5xx makes Stripe retry, which is what a transient failure needs.
            raise HTTPException(502, f"could not apply event: {e}") from None
        return {"ok": True, "result": result}

    @app.get("/invite/{code}")
    def invite(request: Request, code: str):
        who = session_user(request)
        user = auth.user_by_email(conn, who) if who else None
        if not user:
            return RedirectResponse(f"/signup?next={quote('/invite/' + code)}", status_code=303)
        try:
            ws = db.accept_invite(conn, code, user["id"])
            billing.sync_seats(conn, ws)
        except ValueError as e:
            has_home = bool(db.workspaces_for_user(conn, user["id"]))
            return HTMLResponse(pages.notice_page(
                "Invite", str(e).capitalize() + ".",
                "/" if has_home else "/settings/start",
                "Open Dexio" if has_home else "Create your own workspace"), status_code=400)
        response = RedirectResponse("/", status_code=303)
        _set_ws(response, ws)
        return response

    @app.get("/settings/start")
    def start_own_workspace(request: Request):
        """For someone who signed up through an invite that then failed: give them
        their own free workspace rather than an account that can open nothing."""
        user, bounce = signed_in_or_redirect(request, "/settings/start")
        if bounce:
            return bounce
        if not db.workspaces_for_user(conn, user["id"]):
            create_account_workspace(user)
        return RedirectResponse("/", status_code=303)

    # ---- agent sign-in (device flow, RFC 8628) ----------------------------
    # An agent asked to "log me in" starts here, shows the person a link and a
    # code, and polls /device/token until they allow it. See device.py.
    async def json_or_form(request: Request) -> dict:
        raw = (await request.body())[:16384]
        if not raw.strip():
            return {}
        if "json" in request.headers.get("content-type", "") or raw.lstrip()[:1] == b"{":
            try:
                data = json.loads(raw)
            except ValueError:
                return {}
            return data if isinstance(data, dict) else {}
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace")).items() if v}

    DEVICE_ERRORS = {
        "authorization_pending": "The person has not allowed it yet. Poll again after "
                                 "`interval` seconds.",
        "slow_down": "Polling too fast. Wait `interval` seconds between polls.",
        "access_denied": "The person denied access, or no longer belongs to that workspace.",
        "expired_token": "This sign-in expired. Start again with POST /api/v1/device/code.",
        "invalid_grant": "Unknown or already used device_code. Start again with "
                         "POST /api/v1/device/code.",
    }

    @app.post(f"{API}/device/code")
    async def device_code(request: Request):
        if not device_ip.allow(client_ip(request)):
            return JSONResponse({"error": "slow_down", "error_description":
                                 "Too many sign-ins started from this address. Wait a few "
                                 "minutes."}, status_code=429)
        f = await json_or_form(request)
        started = device.start(conn, f.get("client_name") or "")
        code = started["user_code"]
        link = f"{issuer}/device?code={code}"
        return JSONResponse({
            "device_code": started["device_code"], "user_code": code,
            "verification_uri": f"{issuer}/device", "verification_uri_complete": link,
            "expires_in": started["expires_in"], "interval": started["interval"],
            "message": (f"Open {link} to sign in to Dexio (or create a free account) and "
                        f"allow {started['client_name']}. The page shows the code {code}."),
        }, headers={"Cache-Control": "no-store"})

    @app.post(f"{API}/device/token")
    async def device_token(request: Request):
        f = await json_or_form(request)
        state, got = device.poll(conn, str(f.get("device_code") or ""))
        if state != "ok":
            return JSONResponse({"error": state, "error_description": DEVICE_ERRORS[state]},
                                status_code=400, headers={"Cache-Control": "no-store"})
        ws = db.workspace(conn, got["workspace_id"]) or {}
        who = conn.execute("SELECT email FROM users WHERE id=?", (got["user_id"],)).fetchone()
        return JSONResponse({
            "access_token": got["token"], "token_type": "Bearer",
            "mcp_url": f"{issuer}/mcp",
            "account": who["email"] if who else None,
            "workspace": {"id": ws.get("handle"), "name": ws.get("name")},
            "token_name": got["client_name"],
            "next": "Add mcp_url to your MCP config with the header 'Authorization: Bearer "
                    "<access_token>'. Steps per agent: https://dexio.wiki/agents.md",
        }, headers={"Cache-Control": "no-store"})

    def device_user_or_bounce(request: Request, code: str):
        here = "/device" + (f"?code={code}" if code else "")
        who = session_user(request)
        user = auth.user_by_email(conn, who) if who else None
        if not user:
            return None, RedirectResponse(f"/login?next={quote(here, safe='/')}",
                                          status_code=303)
        return user, None

    @app.get("/device", response_class=HTMLResponse)
    def device_form(request: Request, code: str = Query(default="")):
        code = device.pretty(code)
        user, bounce = device_user_or_bounce(request, code)
        if bounce:
            return bounce
        if not code:
            return HTMLResponse(pages.device_entry_page())
        if not device_user.allow(f"dev:{user['id']}"):
            return HTMLResponse(pages.device_entry_page(
                error="Too many codes tried. Wait a few minutes.", code=code), status_code=429)
        found = device.pending(conn, code)
        if not found:
            return HTMLResponse(pages.device_entry_page(
                error="That code is wrong, expired or already used. Ask your agent to start "
                      "again.", code=code), status_code=400)
        if not db.workspaces_for_user(conn, user["id"]):
            create_account_workspace(user)
        mine = db.workspaces_for_user(conn, user["id"])
        return HTMLResponse(pages.device_page(found["client_name"], code, user["email"], mine,
                                              current_workspace(request, user)["id"]))

    @app.post("/device")
    async def device_submit(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        f = await form_fields(request)
        code = device.pretty(f.get("code", ""))
        user, bounce = device_user_or_bounce(request, code)
        if bounce:
            return bounce
        if not device_user.allow(f"dev:{user['id']}"):
            return HTMLResponse(pages.device_entry_page(
                error="Too many codes tried. Wait a few minutes.", code=code), status_code=429)
        found = device.pending(conn, code)
        if not found:
            return HTMLResponse(pages.device_entry_page(
                error="That code expired or was already used. Ask your agent to start again.",
                code=code), status_code=400)
        if f.get("action") != "allow":
            device.answer(conn, code, False)
            return HTMLResponse(pages.notice_page(
                "Agent not connected", f"Denied. {found['client_name']} was not given access.",
                "/", "Open Dexio", status_error=False))
        picked = db.find_workspace(db.workspaces_for_user(conn, user["id"]), f.get("workspace"))
        if not picked:
            raise HTTPException(403, "not a member of that workspace")
        ws = picked["id"]
        if not device.answer(conn, code, True, user["id"], ws):
            return HTMLResponse(pages.device_entry_page(
                error="That code expired or was already used. Ask your agent to start again.",
                code=code), status_code=400)
        response = HTMLResponse(pages.notice_page(
            "Agent connected", f"Done. Go back to {found['client_name']}; it finishes the "
            "setup on its own.", "/", "Open Dexio", status_error=False))
        _set_ws(response, ws)
        return response

    # ---- misc ----------------------------------------------------------
    @app.get(f"{API}/me")
    def me(user=Depends(require_view)):
        first, last = auth.names(conn, user["id"])
        return {"email": user["email"], "first_name": first, "last_name": last}

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    # ---- search engines: public wikis bring in search traffic (Forrest, 2026-09-28)
    # Only what a workspace opens to anyone (shares.py) can be read without
    # signing in, so that is all a crawler reaches; the graph view fetches a page's
    # text from /api/v1/note, which crawlers must be allowed to load. The rest of
    # the app is behind sign-in, and its sign-in pages say noindex themselves.
    @app.get("/robots.txt")
    def robots_txt():
        body = ("User-agent: *\n"
                "Disallow: /mcp\nDisallow: /s/\nDisallow: /invite/\nDisallow: /oauth/\n"
                "Disallow: /device\nDisallow: /settings\nDisallow: /billing/\n"
                "Disallow: /api/v1/share\nDisallow: /api/v1/export\nDisallow: /copy\n"
                f"Sitemap: {issuer}/sitemap.xml\n")
        return Response(body, media_type="text/plain",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/sitemap.xml")
    def sitemap_xml():
        """Every page open to anyone, in every workspace (shares.public_pages)."""
        import html as _html
        import time as _time
        rows = []
        for handle, path, at in shares.public_pages(conn):
            loc = f"{issuer}/w/{handle}" + (f"/{quote(path, safe='/')}" if path else "")
            day = _time.strftime("%Y-%m-%d", _time.gmtime(at)) if at else ""
            rows.append(f"<url><loc>{_html.escape(loc)}</loc>"
                        + (f"<lastmod>{day}</lastmod>" if day else "") + "</url>")
        body = ('<?xml version="1.0" encoding="UTF-8"?>\n'
                '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
                + "\n".join(rows) + "\n</urlset>\n")
        return Response(body, media_type="application/xml",
                        headers={"Cache-Control": "public, max-age=3600"})

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request):
        user = current_user(request)
        if not user:
            if "text/html" in request.headers.get("accept", ""):
                here = "/" + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/login?next={quote(here)}", status_code=303)
            raise HTTPException(401, "authentication required",
                                headers={"WWW-Authenticate": 'Basic realm="dexio"'})
        mine = db.workspaces_for_user(conn, user["id"])
        if not mine:
            return RedirectResponse("/settings/start", status_code=303)
        ws = current_workspace(request, user)
        if "connect" in request.query_params:
            # Connecting an AI moved to Settings > Agents (2026-09-27). Old links,
            # /welcome?client= and a sign-in's ?next= land on its panel, with the
            # AI they named picked.
            there = {"w": ws["handle"], "connect": request.query_params.get("connect", "")}
            return RedirectResponse("/settings/agents?" + urlencode(there), status_code=303)
        # The script puts the address in the bar (/w/<workspace>, wiki_address). A
        # ?project= from an older link names nothing now: a workspace has one wiki.
        return app_page(request, user, mine, ws)

    # Every page has an address of its own (Forrest, 2026-09-28: "we need to
    # support deep links to dexio wiki pages"): /w/<workspace handle>, then /<page
    # path>, then #<section anchor> (parse.heading_anchors). The page and section
    # are the script's to open; the fragment never reaches the server. MCP results
    # carry these addresses (mcp_server.page_url). Until a workspace had one wiki
    # (the same day) the wiki's name came after the workspace; those addresses
    # still work (see old_address), and so do ones naming the workspace by its id,
    # from before handles (the same day): both redirect.
    @app.get("/w/{ws_id}", response_class=HTMLResponse)
    @app.get("/w/{ws_id}/{rest:path}", response_class=HTMLResponse)
    def wiki_address(request: Request, ws_id: str, rest: str = ""):
        user = current_user(request)

        def sign_in():
            if "text/html" in request.headers.get("accept", ""):
                here = request.url.path + (f"?{request.url.query}" if request.url.query else "")
                return RedirectResponse(f"/login?next={quote(here)}", status_code=303)
            raise HTTPException(401, "authentication required",
                                headers={"WWW-Authenticate": 'Basic realm="dexio"'})

        mine = db.workspaces_for_user(conn, user["id"]) if user else []
        ws = db.find_workspace(mine, ws_id)
        if ws is None:
            # Not a member: what is shared with this person, or with anyone who
            # has the link, opens read-only (shares.py). The address is the same
            # one members use, as a Google Doc's is.
            target = db.workspace_by_handle(conn, ws_id)
            acc = shares.access(conn, target["id"], user["id"] if user else None) \
                if target else None
            page = rest.strip("/")
            if acc and acc.any and (user or not page or acc.sees(page)
                                    or not db.note(conn, db.wiki_key(target["id"]), page)):
                return guest_page(request, user, mine, target, acc, page)
            if not user:
                return sign_in()
            return HTMLResponse(pages.notice_page(
                "You need access",
                f"This link is to a wiki you are not a member of, and nothing in it is shared"
                f" with {user['email']}. Ask someone in it to share it with you, or sign in"
                " with the account they shared it with.",
                link="/", link_text="Go to your wiki"), status_code=404)
        there = old_address(ws["id"], rest.strip("/"))
        if there is not None or ws_id != ws["handle"]:
            there = rest.strip("/") if there is None else there
            target = f"/w/{ws['handle']}" + (f"/{quote(there)}" if there else "")
            if request.url.query:
                target += f"?{request.url.query}"
            return RedirectResponse(target, status_code=308)
        return app_page(request, user, mine, ws)

    def old_address(ws_id: int, rest: str) -> str | None:
        """For an address from before a workspace had one wiki, /w/<ws>/<wiki>/<page>:
        the page path to go to instead, "" for the wiki itself, or None when `rest`
        is already a page path. The wiki is main or a name it had before; a page
        whose path starts that way (a folder called main) keeps its address."""
        first, _, page = rest.partition("/")
        if not first:
            return None
        names = {db.WIKI, *db.former_names(conn, ws_id).get(db.WIKI, [])}
        if first not in names:
            return None
        if db.note(conn, db.wiki_key(ws_id), rest):
            return None
        return page

    def app_page(request: Request, user: dict, mine: list[dict], ws: dict) -> HTMLResponse:
        """The graph view of workspace `ws`'s wiki."""
        pages_in = db.wiki_stats(conn, db.ensure_wiki(conn, ws["id"]))["pages"]
        clients = [{"id": k, "name": n, "tile": ais.tile(k, n, s)}
                   for k, (n, s, _kind) in pages.CLIENTS.items()]
        first, last = auth.names(conn, user["id"])
        account = {"name": auth.display_name(first, last, user["email"]), "email": user["email"],
                   "first": first, "last": last}
        # An owner is asked to name a workspace no one has named yet: the one sign-up
        # made as "<first name>'s Workspace" (Forrest, 2026-09-28).
        full = db.workspace(conn, ws["id"]) or {}
        ask_name = ws.get("role") == "owner" and not full.get("named_at")
        response = HTMLResponse(server_page(API, title="Dexio", workspaces=mine,
                                            current=ws["id"], clients=clients, account=account,
                                            first_page=pages.TRY_IT,
                                            connected=agent_connected(ws["id"]),
                                            empty=not pages_in, ask_name=ask_name,
                                            shared=shares.shared_with(conn, user["id"]),
                                            can_share=True))
        _set_ws(response, ws["id"])
        _sync_theme(request, response, user["email"])
        return response

    def guest_page(request: Request, user: dict | None, mine: list[dict], ws: dict,
                   acc: shares.Access, page: str = "") -> HTMLResponse:
        """The graph view of what `ws` shares with this person (or with anyone who
        has the link): read-only, no history, no Settings, no onboarding. The
        workspace cookie is left alone, so "/" still opens their own wiki.
        What anyone may read is for search engines too (Forrest, 2026-09-28:
        "public wiki should bring in search traffic"): its title, description and
        canonical address are in the page. What is shared only by email is not."""
        account = None
        if user:
            first, last = auth.names(conn, user["id"])
            account = {"name": auth.display_name(first, last, user["email"]),
                       "email": user["email"], "first": first, "last": last}
        guest = {"role": acc.role, "signed_in": bool(user), "home": bool(mine),
                 "name": ws["name"], "handle": ws["handle"], "next": request.url.path}
        listed = shares.listing_for(conn, ws["id"])
        if listed:
            guest["copy"] = f"/copy?from={listed['id']}"
        anyone = shares.access(conn, ws["id"], None)
        row = db.note(conn, db.wiki_key(ws["id"]), page) if page else None
        public = anyone.any and (anyone.sees(page) if row else not page)
        title, seo = f"{ws['name']} · Dexio", None
        if public:
            from ..parse import description_of
            url = f"{issuer}/w/{ws['handle']}" + (f"/{quote(page, safe='/')}" if row else "")
            if row:
                title = f"{row['title'] or page} · {ws['name']}"
                about = description_of(row["text"] or "")
            else:
                about = f"{ws['name']}, a wiki on Dexio."
            seo = {"url": url, "title": title, "description": about, "site": ws["name"]}
        response = HTMLResponse(server_page(
            API, title=title, workspaces=mine,
            current=mine[0]["id"] if mine else None, account=account, connected=True,
            guest=guest, shared=shares.shared_with(conn, user["id"]) if user else [],
            seo=seo))
        if not public:
            # Shared with chosen people only: not for search engines.
            response.headers["X-Robots-Tag"] = "noindex, nofollow"
        response.headers["Referrer-Policy"] = "same-origin"
        if user:
            _sync_theme(request, response, user["email"])
        return response

    @app.exception_handler(HTTPException)
    def _http_error(request: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code,
                            headers=getattr(exc, "headers", None))

    # ---- OAuth for Claude and ChatGPT -------------------------------------
    from mcp.server.auth.handlers.metadata import ProtectedResourceMetadataHandler
    from mcp.server.auth.routes import (create_auth_routes, create_protected_resource_routes,
                                        cors_middleware)
    from mcp.server.auth.settings import (AuthSettings, ClientRegistrationOptions,
                                          RevocationOptions)
    from mcp.shared.auth import ProtectedResourceMetadata
    from pydantic import AnyHttpUrl
    from starlette.routing import Route

    provider = oauth.Provider(conn, issuer)
    resource = AnyHttpUrl(issuer + "/mcp")
    # AnyHttpUrl("https://host") would become "https://host/". RFC 8414 compares
    # the issuer as an exact string, so keep it path-less the way AuthSettings does.
    issuer_url = AuthSettings(issuer_url=issuer, resource_server_url=issuer + "/mcp",
                              validate_token_resource=False).issuer_url
    oauth_routes = create_auth_routes(
        provider, issuer_url,
        service_documentation_url=AnyHttpUrl("https://dexio.wiki"),
        client_registration_options=ClientRegistrationOptions(enabled=True),
        revocation_options=RevocationOptions(enabled=True))
    oauth_routes += create_protected_resource_routes(
        resource_url=resource, authorization_servers=[issuer_url], resource_name="Dexio")
    # Some clients ask at the root rather than at the /mcp-suffixed path.
    root_meta = ProtectedResourceMetadataHandler(ProtectedResourceMetadata(
        resource=resource, authorization_servers=[issuer_url], resource_name="Dexio"))
    oauth_routes.append(Route("/.well-known/oauth-protected-resource",
                              endpoint=cors_middleware(root_meta.handle, ["GET", "OPTIONS"]),
                              methods=["GET", "OPTIONS"]))
    # RFC 9207 (issuer in authorization responses). Advertise it, and add `iss` to
    # the error redirects the library's /authorize builds itself; our consent step
    # adds it to its own. ChatGPT only uses its stable callback URL for servers
    # that do this.
    from mcp.server.auth.handlers.metadata import MetadataHandler
    from mcp.server.auth.routes import build_metadata
    from urllib.parse import parse_qsl, urlencode, urlunsplit

    as_meta = build_metadata(issuer_url, AnyHttpUrl("https://dexio.wiki"),
                             ClientRegistrationOptions(enabled=True),
                             RevocationOptions(enabled=True))
    as_meta.authorization_response_iss_parameter_supported = True

    def _with_iss(location: str) -> str:
        parts = urlsplit(location)
        query = parse_qsl(parts.query, keep_blank_values=True)
        if any(k == "iss" for k, _ in query):
            return location
        return urlunsplit(parts._replace(query=urlencode(query + [("iss", issuer)])))

    class _AuthorizeWithIss:
        """ASGI wrapper (a class, so Starlette treats it as an app, not a handler)."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            async def send_(message):
                if message["type"] == "http.response.start" and message["status"] in (302, 303, 307):
                    headers = []
                    for k, v in message.get("headers", []):
                        if k.lower() == b"location":
                            loc = v.decode("latin-1")
                            # Only redirects back to the client carry `iss`; the hop to
                            # our own consent page does not need it.
                            if not loc.startswith(issuer + "/"):
                                v = _with_iss(loc).encode("latin-1")
                        headers.append((k, v))
                    message = {**message, "headers": headers}
                await send(message)
            await self.inner(scope, receive, send_)

    class _ClientIdFromBasic:
        """RFC 6749 section 2.3.1 lets a client_secret_basic client send its id only in
        the Authorization header. The MCP library's ClientAuthenticator reads client_id
        from the form body alone and answers 401 "Missing client_id" without it, so
        Smithery's scan could never finish signing in (found 2026-09-28; Claude and
        Glama use client_secret_post and were unaffected). Copy the id from a Basic
        header into the body when the body has none; the library still checks the
        secret and that the two ids match."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            headers = dict(scope.get("headers") or [])
            auth_header = headers.get(b"authorization", b"").decode("latin-1")
            if scope.get("type") != "http" or scope.get("method") != "POST" \
                    or auth_header[:6].lower() != "basic ":
                return await self.inner(scope, receive, send)
            body, more = b"", True
            while more:
                message = await receive()
                body += message.get("body", b"")
                more = message.get("more_body", False)
            fields = parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True)
            if not any(k == "client_id" for k, _ in fields):
                try:
                    import base64
                    from urllib.parse import unquote
                    raw = base64.b64decode(auth_header[6:].strip()).decode("utf-8")
                    client_id = unquote(raw.split(":", 1)[0])
                except (ValueError, UnicodeDecodeError):
                    client_id = ""
                if client_id:
                    body += (b"&" if body else b"") + urlencode(
                        [("client_id", client_id)]).encode()
            scope = {**scope, "headers": [(k, v) for k, v in scope["headers"]
                                          if k.lower() != b"content-length"]
                     + [(b"content-length", str(len(body)).encode())]}
            replayed = False

            async def receive_():
                nonlocal replayed
                if not replayed:
                    replayed = True
                    return {"type": "http.request", "body": body, "more_body": False}
                return await receive()
            await self.inner(scope, receive_, send)

    for i, r in enumerate(oauth_routes):
        if getattr(r, "path", "") == "/.well-known/oauth-authorization-server":
            oauth_routes[i] = Route(r.path, endpoint=cors_middleware(
                MetadataHandler(as_meta).handle, ["GET", "OPTIONS"]), methods=["GET", "OPTIONS"])
        elif getattr(r, "path", "") == "/authorize":
            oauth_routes[i] = Route(r.path, endpoint=_AuthorizeWithIss(r.app),
                                    methods=["GET", "POST"])
        elif getattr(r, "path", "") in ("/token", "/revoke"):
            oauth_routes[i] = Route(r.path, endpoint=_ClientIdFromBasic(r.app),
                                    methods=sorted(r.methods or ["POST"]))
    app.router.routes.extend(oauth_routes)

    @app.get("/oauth/consent", response_class=HTMLResponse)
    def oauth_consent(request: Request, req: str = Query(default="")):
        found = oauth.pending(conn, req)
        if not found:
            return HTMLResponse(pages.notice_page(
                "Sign-in expired", "This sign-in request expired or was already used. "
                "Start again from your app."), status_code=400)
        here = f"/oauth/consent?req={quote(req)}"
        who = session_user(request)
        user = auth.user_by_email(conn, who) if who else None
        if not user:
            return RedirectResponse(f"/login?next={quote(here)}", status_code=303)
        mine = db.workspaces_for_user(conn, user["id"])
        if not mine:
            create_account_workspace(user)
            mine = db.workspaces_for_user(conn, user["id"])
        client, params = found
        current = current_workspace(request, user)["id"]
        return HTMLResponse(pages.consent_page(
            client.client_name or "An app", urlsplit(params["redirect_uri"]).netloc,
            user["email"], mine, current, req))

    @app.post("/oauth/consent")
    async def oauth_consent_submit(request: Request):
        if not same_origin(request):
            raise HTTPException(403, "cross-site request refused")
        f = await form_fields(request)
        req = f.get("req", "")
        who = session_user(request)
        user = auth.user_by_email(conn, who) if who else None
        if not user:
            return RedirectResponse(f"/login?next={quote('/oauth/consent?req=' + req)}",
                                    status_code=303)
        if f.get("action") != "allow":
            target = oauth.deny(conn, req, issuer)
            return RedirectResponse(target or "/", status_code=303)
        picked = db.find_workspace(db.workspaces_for_user(conn, user["id"]), f.get("workspace"))
        if not picked:
            raise HTTPException(403, "not a member of that workspace")
        ws = picked["id"]
        try:
            target = oauth.approve(conn, req, user["id"], ws, issuer)
        except ValueError as e:
            return HTMLResponse(pages.notice_page("Sign-in expired", str(e).capitalize() + "."),
                                status_code=400)
        return RedirectResponse(target, status_code=303)

    @app.post("/settings/apps/{client_id}/disconnect", response_class=HTMLResponse)
    async def settings_disconnect(request: Request, client_id: str):
        user, ws, _f, bounce = await settings_post(request)
        if bounce:
            return bounce
        if ws and oauth.disconnect(conn, ws["id"], client_id):
            return settings_done("agents", "disconnected", ws)
        return render_settings(request, user, ws, "agents", status=404,
                               error="That app is not connected here.")

    # Last, so every route above wins; the MCP app only answers /mcp.
    app.mount("/", mcp_asgi(mcp, conn,
                            f"{issuer}/.well-known/oauth-protected-resource/mcp"))
    return app


app = get_app()
