"""Workspaces: self-serve signup, isolation between accounts, memberships and
invites, per-workspace tokens, rate limits. TestClient against the real app,
MCP through plain JSON-RPC POSTs (the endpoint is stateless)."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, db  # noqa: E402
from dexio.server.app import get_app  # noqa: E402
from dexio.server.mcp_server import CHANGE_TOOLS  # noqa: E402

PW = "a-good-long-password-1"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    a = get_app(str(tmp_path / "w.db"))
    # One client runs the app's lifespan (the MCP session manager starts once).
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        yield a


def browser(app) -> TestClient:
    # https so the Secure session cookie is sent back.
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def signup(c: TestClient, email: str, pw: str = PW, agree: bool = True, next_: str = "",
           wiki: str | None = "main", first: str | None = None):
    """Sign up. The new workspace comes with its wiki (2026-09-28); `wiki` is
    kept for callers from when the graph view asked for a first wiki's name."""
    data = {"email": email, "password": pw, "confirm_password": pw,
            "first_name": email.split("@")[0] if first is None else first}
    if agree:
        data["agree"] = "1"
    if next_:
        data["next"] = next_
    return c.post("/signup", data=data)


def ws_id(app, ws) -> int:
    """A workspace's internal id, from the handle its addresses and the web API use
    (db.new_handle); an id passes through."""
    if isinstance(ws, int) or str(ws).isdigit():
        return int(ws)
    row = app.state.conn.execute("SELECT id FROM workspaces WHERE handle=?", (ws,)).fetchone()
    assert row, f"no workspace with handle {ws!r}"
    return int(row["id"])


def invite_code(c: TestClient, monkeypatch, email: str = "guest@example.com",
                w: int | None = None) -> str:
    """Invite by email and return the code from the message the app sends."""
    from dexio.server import mail
    links: list[str] = []
    monkeypatch.setattr(mail, "invite", lambda to, inviter, ws, link, *_: links.append(link) or True)
    r = c.post("/settings/invite" + (f"?w={w}" if w else ""), data={"email": email})
    assert r.status_code == 303, r.text
    return re.search(r"/invite/(dxi_[A-Za-z0-9_\-]+)", links[-1]).group(1)


def token_from_connect(c: TestClient, client: str = "hermes", wiki: str = "main") -> str:
    """Mint a key through the connect flow. It takes no wiki since 2026-09-28;
    `wiki` is kept for callers written before then and is not sent."""
    r = c.post("/api/v1/connect", json={"client": client})
    assert r.status_code == 200, r.text
    return re.search(r"dxk_[A-Za-z0-9_\-]+", r.json()["html"]).group(0)


def mcp(app, token: str, tool: str, **args):
    r = app.state.live.post("/mcp", headers={"Authorization": f"Bearer {token}",
                                             "Accept": "application/json, text/event-stream"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": (
                                      {"agent": "test-agent", **args}
                                      if tool in CHANGE_TOOLS else args)}})
    res = r.json()["result"]
    text = res["content"][0]["text"]
    if res.get("isError"):
        return {"error": text}
    data = json.loads(text)
    if len(res["content"]) > 1:           # read_page: header, then the page as plain text
        data["text"] = res["content"][1]["text"]
    return data


def test_signup_makes_an_account_a_workspace_and_a_main_wiki(app):
    c = browser(app)
    r = signup(c, "ann@example.com")
    assert r.status_code == 303 and r.headers["location"] == "/joined?next=%2F"  # then /
    assert r.cookies.get(auth.SESSION_COOKIE)
    page = c.get("/")                     # the graph view; its empty wiki links to connecting
    assert page.status_code == 200 and "function connectFlow" in page.text
    assert 'data-client="claude-code"' in c.get("/settings/agents?connect").text
    assert "ann&#x27;s Workspace" in c.get("/settings").text
    # The workspace comes with its one wiki, empty until an agent writes to it.
    ws = c.get("/api/v1/workspaces").json()["current"]
    assert [p["name"] for p in db.projects(app.state.conn, ws_id(app, ws))] == ["main"]
    assert c.get("/api/v1/graph").status_code == 404


def steps(c: TestClient, client: str) -> str:
    r = c.get(f"/api/v1/connect?client={client}")
    assert r.status_code == 200, r.text
    return r.json()["html"]


def test_connect_gives_agents_that_run_commands_a_message_to_set_themselves_up(app):
    c = browser(app)
    signup(c, "ann@example.com")
    for client in ("hermes", "openclaw", "claude-code"):
        page = steps(c, client)
        assert "dxk_" not in page          # a GET never mints a key
        assert f'data-mint="{client}"' in page and "Get the message" in page
        # without a key, the agent signs in through the device login
        assert "Connect yourself to my Dexio wiki. Read the steps with curl -s " \
               "https://dexio.wiki/agents.md and follow them.</code>" in page
    # One click mints a key, named for the agent, inside the message to paste.
    page = c.post("/api/v1/connect", json={"client": "hermes"}).json()["html"]
    m = re.search(r"follow them, using this API key: (dxk_[A-Za-z0-9_\-]+)</code>", page)
    assert m and page.count(m.group(1)) == 2          # the message, and on its own by hand
    assert "/reload-mcp" in page and "under Connected" in page
    assert "<td>Hermes<span" in c.get("/settings/agents").text
    listed = mcp(app, m.group(1), "list_pages")                        # the key works
    assert "error" not in listed and "pages" in listed
    # By hand, Hermes's own command stores the key and writes the config.
    assert "hermes mcp add dexio --url https://testserver/mcp --auth header" in page
    assert "config.yaml" not in page and "DEXIO_API_KEY=" not in page


def test_claude_gets_a_one_click_install_link_and_everyone_a_first_message(app):
    c = browser(app)
    signup(c, "ann@example.com")
    page = steps(c, "claude")
    # claude.com/docs/connectors/building/directory-vs-custom: opens Add custom connector
    # with the name and URL filled in
    assert ("https://claude.ai/customize/connectors?modal=add-custom-connector&amp;"
            "connectorName=Dexio&amp;connectorUrl=https%3A%2F%2Ftestserver%2Fmcp") in page
    for client in ("claude", "chatgpt"):
        # a real first page, not a "Hello" test note (Forrest, 2026-09-27)
        page = steps(c, client)
        assert "Save a page in my Dexio wiki about what we&#x27;re working on." in page
        assert "Hello" not in page


def test_signup_needs_a_first_name_because_the_workspace_is_named_for_it(app):
    c = browser(app)
    r = signup(c, "x@example.com", first="  ")
    assert r.status_code == 400 and "Enter your first name." in r.text
    signup(c, "x@example.com", first="Xavier", wiki=None)
    names = [w["name"] for w in c.get("/api/v1/workspaces").json()["workspaces"]]
    assert names == ["Xavier's Workspace"]


def test_signup_validation(app):
    c = browser(app)
    assert signup(c, "x@example.com", agree=False).status_code == 400
    r = c.post("/signup", data={"email": "x@example.com", "password": PW, "first_name": "X",
                                "confirm_password": PW + "x", "agree": "1"})
    assert r.status_code == 400 and "do not match" in r.text
    assert "at least 12" in signup(c, "x@example.com", pw="short").text
    assert signup(c, "x@example.com").status_code == 303
    dupe = signup(browser(app), "X@example.com")
    assert dupe.status_code == 400 and "already exists" in dupe.text
    assert browser(app).post("/signup", data={"email": "y@example.com"},
                             headers={"origin": "https://evil.example"}).status_code == 403


def test_accounts_cannot_see_each_others_pages(app):
    """An agent reaches only its own workspace's wiki, whatever wiki name it was
    set up with, and the browser API only shows your own workspace's pages."""
    a, b = browser(app), browser(app)
    signup(a, "ann@example.com")
    signup(b, "bob@example.com")
    ta, tb = token_from_connect(a), token_from_connect(b)
    assert mcp(app, ta, "write_page", path="secret", text="# Ann only")["ok"]
    # An old wiki name is ignored: the page lands in Ann's own wiki.
    assert mcp(app, ta, "write_page", wiki="notes", path="x", text="# X")["ok"]
    assert mcp(app, tb, "write_page", wiki="main", path="hello", text="# Bob")["ok"]

    assert sorted(p["path"] for p in mcp(app, ta, "list_pages")["pages"]) == ["secret", "x"]
    assert [p["path"] for p in mcp(app, tb, "list_pages")["pages"]] == ["hello"]
    assert "no page" in mcp(app, tb, "read_page", path="secret")["error"]
    assert "no page" in mcp(app, tb, "read_page", wiki="notes", path="x")["error"]
    assert mcp(app, tb, "search_pages", query="Ann")["results"] == []

    # And in the browser API.
    assert b.get("/api/v1/note?path=hello").status_code == 200
    assert b.get("/api/v1/note?path=secret").status_code == 404
    assert b.get("/api/v1/note?project=notes&path=x").status_code == 404
    assert "secret" not in b.get("/api/v1/graph").text
    # Asking for someone else's workspace id silently falls back to your own.
    ann_ws = a.get("/api/v1/workspaces").json()["current"]
    assert b.get(f"/api/v1/workspaces?w={ann_ws}").json()["current"] != ann_ws
    assert b.get(f"/api/v1/note?w={ann_ws}&path=secret").status_code == 404


def test_invites_join_a_workspace_and_respect_the_plan_limit(app, monkeypatch, plans):
    a, b = browser(app), browser(app)
    signup(a, "ann@example.com")
    signup(b, "bob@example.com")
    conn = app.state.conn
    ann_ws = a.get("/api/v1/workspaces").json()["current"]

    # Free workspaces hold one member.
    r = a.post("/settings/invite", data={"email": "bob@example.com"})
    assert r.status_code == 400 and "member limit" in r.text

    with conn:
        conn.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws_id(app, ann_ws),))
    code = invite_code(a, monkeypatch, "bob@example.com")
    joined = b.get(f"/invite/{code}")
    assert joined.status_code == 303
    mine = {w["id"] for w in b.get("/api/v1/workspaces").json()["workspaces"]}
    assert ann_ws in mine and len(mine) == 2
    assert b.get(f"/api/v1/workspaces?w={ann_ws}").json()["current"] == ann_ws
    # The link works once.
    again = browser(app)
    signup(again, "cat@example.com")
    assert again.get(f"/invite/{code}").status_code == 400
    # Members cannot invite; only owners.
    assert b.post(f"/settings/invite?w={ann_ws}", data={"email": "x@example.com"}) \
        .status_code == 403


def test_a_server_without_stripe_has_no_plan_limits(app, monkeypatch):
    """Forrest, 2026-09-30: a self-hosted copy (no Stripe key) has no member or
    storage limits, since nobody on it can buy a plan."""
    from dexio.server import files
    a, b, c = browser(app), browser(app), browser(app)
    signup(a, "ann@example.com")
    signup(b, "bob@example.com")
    signup(c, "cat@example.com")
    ann_ws = a.get("/api/v1/workspaces").json()["current"]
    wid = ws_id(app, ann_ws)
    conn = app.state.conn
    assert db.member_limit(conn, wid) is None and files.storage_limit(conn, wid) is None
    # A Free workspace takes a second and a third member, and stays writable.
    for guest, email in ((b, "bob@example.com"), (c, "cat@example.com")):
        code = invite_code(a, monkeypatch, email)
        assert guest.get(f"/invite/{code}").status_code == 303
    assert len(db.members(conn, wid)) == 3 and db.read_only_reason(conn, wid) == ""
    # Settings, Plan says there are no plans here, and offers none.
    page = a.get("/settings/plan").text
    assert "does not sell plans" in page and "no member or storage limits" in page
    assert "Upgrade" not in page and "$10" not in page
    # A limit an operator sets on a workspace still holds.
    with conn:
        conn.execute("UPDATE workspaces SET storage_limit=5000 WHERE id=?", (wid,))
    assert files.storage_limit(conn, wid) == 5000
    # With a Stripe key the Free plan's limits are back.
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    assert db.member_limit(conn, wid) == 1 and db.read_only_reason(conn, wid)


def test_invite_for_someone_without_an_account(app, monkeypatch):
    a = browser(app)
    signup(a, "ann@example.com")
    with app.state.conn as conn:
        conn.execute("UPDATE workspaces SET plan='business'")
    code = invite_code(a, monkeypatch, "dan@example.com")
    newcomer = browser(app)
    r = newcomer.get(f"/invite/{code}")
    assert r.status_code == 303 and r.headers["location"].startswith("/signup?next=")
    r = signup(newcomer, "dan@example.com", next_=f"/invite/{code}")
    assert r.headers["location"] == f"/invite/{code}"
    assert newcomer.get(f"/invite/{code}").status_code == 303
    # Joined Ann's workspace and did not get an empty one of their own.
    assert len(newcomer.get("/api/v1/workspaces").json()["workspaces"]) == 1


def test_tokens_are_listed_and_revoked_per_workspace(app):
    a = browser(app)
    signup(a, "ann@example.com")
    tok = token_from_connect(a, "claude-code")
    assert "pages" in mcp(app, tok, "list_pages")
    page = a.get("/settings/agents").text
    tid = re.search(r"/settings/tokens/(\d+)/revoke", page).group(1)
    assert "Claude Code" in page
    b = browser(app)
    signup(b, "bob@example.com")
    assert b.post(f"/settings/tokens/{tid}/revoke").status_code == 404   # not Bob's
    r = a.post(f"/settings/tokens/{tid}/revoke")
    assert r.status_code == 303 and "done=revoked" in r.headers["location"]
    assert "API key revoked." in a.get(r.headers["location"]).text
    r = app.state.live.post("/mcp", headers={"Authorization": f"Bearer {tok}"}, json={})
    assert r.status_code == 401


def test_new_workspace_and_rename(app):
    a = browser(app)
    signup(a, "ann@example.com")
    r = a.post("/settings/workspaces", data={"name": "Side project"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    new_ws = r.cookies.get("dexio_ws")
    names = {w["name"] for w in a.get("/api/v1/workspaces").json()["workspaces"]}
    assert names == {"ann's Workspace", "Side project"}
    r = a.post(f"/settings/rename?w={new_ws}", data={"name": "Renamed"})
    assert r.status_code == 303 and r.headers["location"] == f"/settings?w={new_ws}&done=renamed"
    assert "Workspace renamed." in a.get(r.headers["location"]).text
    assert db.workspace(app.state.conn, ws_id(app, new_ws))["name"] == "Renamed"
    home = a.get("/", headers={"accept": "text/html"})
    # the header switcher lists both workspaces, the current one by name
    assert '"name": "Renamed"' in home.text and '"name": "ann\'s Workspace"' in home.text
    assert '<span class="wsm-label">Renamed</span>' in home.text


def test_signup_and_login_are_rate_limited(app):
    c = browser(app)
    codes = [signup(c, f"u{i}@example.com").status_code for i in range(7)]
    assert codes[:5] == [303] * 5 and codes[5:] == [429, 429]
    signup(browser(app), "z@example.com")  # different client, same test IP: also limited
    fails = [browser(app).post("/login", data={"email": "nobody@example.com",
                                               "password": "wrong-password-0"}).status_code
             for _ in range(12)]
    assert fails[:10] == [401] * 10 and 429 in fails[10:]


@pytest.mark.sqlite_only
def test_operator_database_upgrade_keeps_existing_tokens_and_login(tmp_path, monkeypatch):
    """A pre-workspace database: its wiki, a scoped token and the operator login
    all land in the default workspace and keep working. The old wiki becomes the
    workspace's one wiki, so nobody's pages go missing in the upgrade."""
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
    CREATE TABLE tokens (id INTEGER PRIMARY KEY, name TEXT NOT NULL, token_hash TEXT NOT NULL
      UNIQUE, project TEXT, created_at REAL NOT NULL, last_used REAL);
    CREATE TABLE projects (name TEXT PRIMARY KEY, source TEXT, updated_at REAL NOT NULL,
      pages INTEGER NOT NULL DEFAULT 0, links INTEGER NOT NULL DEFAULT 0,
      words INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE pages (project TEXT NOT NULL, path TEXT NOT NULL, file TEXT NOT NULL,
      title TEXT NOT NULL, folder TEXT NOT NULL, words INTEGER NOT NULL,
      degree INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL, PRIMARY KEY (project, path));
    CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
      password_hash TEXT NOT NULL, created_at REAL NOT NULL, last_login REAL);
    INSERT INTO projects VALUES ('fleet', 'mac', 1000.0, 1, 0, 2);
    INSERT INTO pages VALUES ('fleet', 'index', 'index.md', 'Index', '', 1, 0, '# Index');
    """)
    old.execute("INSERT INTO tokens (name, token_hash, project, created_at) VALUES (?,?,?,?)",
                ("mac", db.hash_token("dxk_old_token"), "fleet", 1.0))
    old.execute("INSERT INTO users (email, password_hash, created_at) VALUES (?,?,?)",
                ("op@example.com", auth.hash_password(PW), 1.0))
    old.commit()
    old.close()
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    app = get_app(str(path))
    with TestClient(app, base_url="https://testserver") as live:
        app.state.live = live
        # The token reaches the old wiki with the name it was set up with, or none.
        assert mcp(app, "dxk_old_token", "read_page", wiki="fleet",
                   path="index")["text"] == "# Index"
        assert [p["path"] for p in mcp(app, "dxk_old_token", "list_pages")["pages"]] == ["index"]
    c = browser(app)
    c.post("/login", data={"email": "op@example.com", "password": PW})
    ws = c.get("/api/v1/workspaces").json()["current"]
    assert [p["name"] for p in db.projects(app.state.conn, ws_id(app, ws))] == ["main"]
    assert c.get("/api/v1/note?path=index").json()["text"] == "# Index"


def test_theme_is_an_account_setting_that_follows_sign_in(app):
    """Appearance lives in Settings, is saved on the account, and every sign-in
    and app load brings it to the browser as the cookie the <head> script reads."""
    from dexio import themes
    c = browser(app)
    signup(c, "tia@example.com")
    page = c.get("/settings/appearance")
    assert 'id="appearance"' in page.text and 'value="dexio" checked' in page.text
    assert themes.COOKIE not in page.cookies  # nothing chosen yet: nothing forced

    r = c.post("/settings/theme", data={"theme": "aubergine", "mode": "dark"},
               headers={"Accept": "application/json"})
    assert r.status_code == 200 and r.json() == {"theme": "aubergine", "mode": "dark"}
    assert r.cookies.get(themes.COOKIE) == "aubergine.dark"
    assert 'value="aubergine" checked' in c.get("/settings/appearance").text

    # without JavaScript the form posts and comes back to the panel
    r = c.post("/settings/theme", data={"theme": "jade", "mode": "system"})
    assert r.status_code == 303 and r.headers["location"] == "/settings/appearance"
    assert r.cookies.get(themes.COOKIE) == "jade.system"

    # another device: signing in brings the choice with it
    other = browser(app)
    r = other.post("/login", data={"email": "tia@example.com", "password": PW})
    assert r.status_code == 303 and r.cookies.get(themes.COOKIE) == "jade.system"

    # a device still holding an older choice is corrected on the next app load
    c.post("/settings/theme", data={"theme": "rose", "mode": "light"},
           headers={"Accept": "application/json"})
    r = other.get("/", headers={"Accept": "text/html"})
    assert r.status_code == 200 and r.cookies.get(themes.COOKIE) == "rose.light"
    r = other.get("/", headers={"Accept": "text/html"})
    assert themes.COOKIE not in r.cookies  # already current: no Set-Cookie

    # the graph page has no toggle; the head script is there to apply the cookie
    assert 'id="theme"' not in r.text and "dexioTheme" in r.text


def test_settings_is_one_page_per_section(app, monkeypatch):
    """A sidebar of sections, each at its own URL, the current one marked."""
    from dexio.server import pages
    a, b = browser(app), browser(app)
    signup(a, "ann@example.com")
    signup(b, "bob@example.com")
    for section, title in pages.SECTIONS.items():
        url = pages.section_url(section)
        r = a.get(url)
        assert r.status_code == 200, url
        assert f"<h1>{title}</h1>" in r.text
        assert f'<a class="sec" href="{url}" aria-current="page">' in r.text
        assert r.text.count('aria-current="page">') == 1
    # Old links to the single long page's panels still reach their section.
    assert '"#appearance": "/settings/appearance"' in a.get("/settings").text
    # A member sees the workspace's settings without the owner's controls.
    with app.state.conn as conn:
        conn.execute("UPDATE workspaces SET plan='team'")
    code = invite_code(a, monkeypatch, "bob@example.com")
    b.get(f"/invite/{code}")
    ann_ws = a.get("/api/v1/workspaces").json()["current"]
    page = b.get(f"/settings?w={ann_ws}").text
    assert "Only an owner can rename it." in page and 'action="/settings/rename"' not in page
    assert "Ask an owner" in b.get(f"/settings/members?w={ann_ws}").text
    # Two workspaces: the sidebar's menu switches between them, staying on the section.
    bob_ws = [w["id"] for w in b.get("/api/v1/workspaces").json()["workspaces"]
              if w["id"] != ann_ws][0]
    assert 'id="ws-menu-button"' in page and f'href="/settings?w={bob_ws}"' in page
    assert f'href="/settings/members?w={bob_ws}"' in b.get(f"/settings/members?w={ann_ws}").text


def test_account_sections_open_without_a_workspace(app):
    """Someone whose invite failed has an account and no workspace: their own
    sections open, and a workspace section offers them one."""
    auth.create_user(app.state.conn, "lone@example.com", PW)
    c = browser(app)
    c.post("/login", data={"email": "lone@example.com", "password": PW})
    for url in ("/settings/profile", "/settings/appearance"):
        r = c.get(url)
        assert r.status_code == 200 and "Workspace</div>" not in r.text, url
    r = c.get("/settings/members")
    assert r.status_code == 303 and r.headers["location"] == "/settings/start"


def test_no_workspaces_section(app):
    """The workspace menu lists and creates workspaces, so Settings has no
    Workspaces section (removed 2026-09-27). Its old URL lands on General."""
    c = browser(app)
    signup(c, "solo@example.com")
    page = c.get("/settings").text
    assert 'href="/settings/workspaces"' not in page and ">Workspaces</a>" not in page
    assert "Your workspaces" not in page
    assert 'data-key="new-ws"' in page                 # New workspace is in the sidebar menu
    for section in ("/settings", "/settings/profile", "/settings/appearance"):
        assert "/settings/workspaces\" class=\"row" not in c.get(section).text
    r = c.get("/settings/workspaces?done=left")
    assert r.status_code == 308 and r.headers["location"] == "/settings?done=left"
    # Creating still posts there.
    r = c.post("/settings/workspaces", data={"name": "Second"})
    assert r.status_code == 303 and r.headers["location"] == "/"


def test_theme_setting_is_validated(app):
    c = browser(app)
    signup(c, "val@example.com")
    for data in ({"theme": "neon", "mode": "dark"}, {"theme": "jade", "mode": "dim"},
                 {"theme": "jade"}, {}):
        assert c.post("/settings/theme", data=data).status_code == 400, data
    r = c.post("/settings/theme", data={"theme": "jade", "mode": "dark"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    anon = browser(app)
    r = anon.post("/settings/theme", data={"theme": "jade", "mode": "dark"})
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
