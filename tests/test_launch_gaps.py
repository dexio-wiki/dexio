"""Deleting an account (the privacy policy's promise) and recording where each
signup came from."""
from __future__ import annotations


import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, db, files, signups  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import (PW, browser, invite_code, mcp, signup,  # noqa: E402
                             token_from_connect, ws_id)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    files.set_store(files.FolderStore(tmp_path / "store"))
    a = get_app(str(tmp_path / "g.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        a.state.store = tmp_path / "store"
        yield a
    files.set_store(None)


def uid(app, email):
    return auth.user_by_email(app.state.conn, email)["id"]


def delete(c, email):
    return c.post("/account/delete", data={"confirm_email": email})


# ---- deleting an account -------------------------------------------------------
def test_delete_account_removes_the_workspace_its_pages_files_and_keys(app):
    c = browser(app)
    signup(c, "ann@example.com")
    conn = app.state.conn
    ws = db.workspaces_for_user(conn, uid(app, "ann@example.com"))[0]["id"]
    tok = token_from_connect(c)
    assert "error" not in mcp(app, tok, "write_page", wiki="main", path="notes", text="# Notes\n")
    r = app.state.live.put("/api/v1/files", params={"wiki": "main", "path": "raw/a.txt"},
                           content=b"hello", headers={"Authorization": f"Bearer {tok}",
                                                      "Content-Type": "text/plain"})
    assert r.status_code in (200, 201), r.text
    assert any(p.is_file() for p in app.state.store.rglob("*"))

    page = c.get("/settings/profile").text
    assert "Delete your account" in page and "and everything in it" in page

    # the email has to match
    assert delete(c, "someone@example.com").status_code == 400
    assert auth.user_by_email(conn, "ann@example.com")

    r = delete(c, "ANN@example.com")
    assert r.status_code == 200 and "Account deleted" in r.text
    assert auth.user_by_email(conn, "ann@example.com") is None
    for table, where in (("workspaces", "id=?"), ("memberships", "workspace_id=?"),
                         ("tokens", "workspace_id=?"), ("files", "workspace_id=?")):
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}",
                            (ws_id(app, ws),)).fetchone()[0] == 0, table
    for table in ("pages", "revisions", "projects"):
        col = "name" if table == "projects" else "project"
        assert conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {col} LIKE ?",
                            (f"{ws}:%",)).fetchone()[0] == 0, table
    assert not any(p.is_file() for p in app.state.store.rglob("*"))    # the bytes too
    # the agent's key stops working and the browser is signed out
    r = app.state.live.post("/mcp", headers={"Authorization": f"Bearer {tok}",
                                             "Accept": "application/json, text/event-stream"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert c.get("/settings").status_code == 303
    # the address is free to sign up again
    assert signup(browser(app), "ann@example.com").status_code == 303


def test_leaving_a_shared_workspace_keeps_it_for_the_others(app, monkeypatch):
    a, b = browser(app), browser(app)
    signup(a, "ann@example.com")
    signup(b, "bob@example.com")
    conn = app.state.conn
    ann_ws = a.get("/api/v1/workspaces").json()["current"]
    with conn:
        conn.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws_id(app, ann_ws),))
    code = invite_code(a, monkeypatch, "bob@example.com")
    assert b.get(f"/invite/{code}").status_code == 303
    tok = token_from_connect(a)
    assert "error" not in mcp(app, tok, "write_page", wiki="main", path="plan", text="# Plan\n")

    # Ann owns it alone but pays nothing (no subscription): she can go, Bob inherits it.
    assert "You leave" in a.get("/settings/profile").text
    assert delete(a, "ann@example.com").status_code == 200
    assert conn.execute("SELECT COUNT(*) FROM workspaces WHERE id=?",
                        (ws_id(app, ann_ws),)).fetchone()[0] == 1
    assert db.role_in(conn, ws_id(app, ann_ws), uid(app, "bob@example.com")) == "owner"
    assert conn.execute("SELECT COUNT(*) FROM pages WHERE project=?",
                        (db.wiki_key(ws_id(app, ann_ws)),)).fetchone()[0] == 1
    assert conn.execute("SELECT COUNT(*) FROM revisions WHERE user_id IS NOT NULL AND"
                        " project=? AND user_id NOT IN (SELECT id FROM users)",
                        (db.wiki_key(ws_id(app, ann_ws)),)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM tokens WHERE workspace_id=?",
                        (ws_id(app, ann_ws),)).fetchone()[0] == 0   # her agent's key is gone


def test_a_paid_plan_has_to_be_cancelled_first(app):
    c = browser(app)
    signup(c, "ann@example.com")
    conn = app.state.conn
    ws = c.get("/api/v1/workspaces").json()["current"]
    with conn:
        conn.execute("UPDATE workspaces SET plan='team', stripe_subscription='sub_1' WHERE id=?",
                     (ws_id(app, ws),))
    page = c.get("/settings/profile").text
    assert "cannot be deleted yet" in page and "Cancel it first, under Settings, Plan" in page
    assert "Delete my account" not in page
    r = delete(c, "ann@example.com")
    assert r.status_code == 409 and auth.user_by_email(conn, "ann@example.com")


def test_delete_refuses_cross_site_posts(app):
    c = browser(app)
    signup(c, "ann@example.com")
    r = c.post("/account/delete", data={"confirm_email": "ann@example.com"},
               headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and auth.user_by_email(app.state.conn, "ann@example.com")


# ---- where signups come from ---------------------------------------------------
def source(app, email):
    row = app.state.conn.execute("SELECT * FROM signups WHERE user_id=?",
                                 (uid(app, email),)).fetchone()
    return dict(row) if row else None


def test_the_sites_tags_are_kept_through_the_email_steps(app):
    c = browser(app)
    r = c.get("/signup", params={"ref": "news.ycombinator.com", "utm_source": "hn",
                                 "utm_campaign": "launch", "landing": "/pricing"})
    assert signups.COOKIE in r.cookies
    assert signup(c, "ann@example.com").status_code == 303
    s = source(app, "ann@example.com")
    assert (s["referrer"], s["utm_source"], s["utm_campaign"], s["landing"], s["method"],
            s["via"]) == ("news.ycombinator.com", "hn", "launch", "/pricing", "password", "site")
    # the operator report splits by campaign too, so two ad campaigns can be told apart
    assert signups.report(app.state.conn)["by_utm_campaign"] == {"launch": 1}


def test_first_touch_wins_and_an_outside_referer_counts(app):
    c = browser(app)
    c.get("/login", headers={"referer": "https://www.reddit.com/r/ClaudeAI/comments/x"})
    c.get("/signup", params={"utm_source": "later"})          # not overwritten
    signup(c, "ann@example.com")
    s = source(app, "ann@example.com")
    assert s["referrer"] == "www.reddit.com" and s["utm_source"] is None
    # our own pages are not a source
    d = browser(app)
    d.get("/signup", headers={"referer": "https://dexio.wiki/pricing"})
    signup(d, "bob@example.com")
    assert source(app, "bob@example.com")["referrer"] is None


def test_what_brought_them_is_recorded(app):
    c = browser(app)
    signup(c, "ann@example.com", next_="/device?code=BCDF-GHJK")
    assert source(app, "ann@example.com")["via"] == "agent sign-in"
    assert signups.report(app.state.conn)["by_via"] == {"agent sign-in": 1}


def test_touch_keeps_only_hosts_and_short_printable_tags():
    assert signups.touch({"ref": "https://news.ycombinator.com/item?id=1"}, None)["ref"] == \
        "news.ycombinator.com"
    assert signups.touch({}, "https://app.dexio.wiki/login") is None
    assert signups.touch({"utm_source": "x" * 500}, None)["utm_source"] == "x" * 100
    assert signups.decode("not base64!") == {}
