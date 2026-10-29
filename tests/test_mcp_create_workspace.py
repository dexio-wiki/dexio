"""An agent creates a workspace (Forrest, 2026-10-29, on an agent that had to ask him to
make one in the app: "Can we fix this gap?"). create_workspace makes a Free workspace,
with its wiki, owned by the key's person; the key reaches it through `workspace` like
any other workspace that person is in. Asked again for a name they already have, it
returns that one instead of a second."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import db  # noqa: E402

from test_workspaces import app, browser, mcp, signup, token_from_connect  # noqa: E402,F401


def user_id(app, email) -> int:
    return app.state.conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]


def test_an_agent_creates_a_workspace_and_works_in_it(app):
    c = browser(app)
    signup(c, "ann@example.com")
    home = c.get("/api/v1/workspaces").json()["current"]
    tok = token_from_connect(c)
    conn = app.state.conn

    r = mcp(app, tok, "create_workspace", name="  Home   Lineage ")
    assert r["created"] is True
    ws = r["workspace"]
    assert ws["name"] == "Home Lineage" and ws["handle"] != home
    assert ws["url"].endswith(f"/w/{ws['handle']}") and ws["access"] == "read and write"
    row = db.workspace_by_handle(conn, ws["handle"])
    assert row["plan"] == "free" and row["named_at"]          # named: no name prompt in the app
    assert db.role_in(conn, row["id"], user_id(app, "ann@example.com")) == "owner"
    assert [m["email"] for m in db.members(conn, row["id"])] == ["ann@example.com"]

    # the key's own workspace is unchanged; the new one is listed and works by handle
    w = mcp(app, tok, "list_workspaces")
    assert w["workspace"]["handle"] == home
    assert [m["handle"] for m in w["member_of"]] == [ws["handle"]]
    put = mcp(app, tok, "write_page", path="family/tree", text="# Tree\n",
              workspace=ws["handle"])
    assert put.get("created") and put["url"].endswith(f"/w/{ws['handle']}/family/tree")
    assert db.note(conn, db.wiki_key(row["id"]), "family/tree")
    assert mcp(app, tok, "list_pages")["count"] == 0          # nothing landed at home

    # the browser sees it in the workspace menu too
    assert ws["handle"] in [x["id"] for x in c.get("/api/v1/workspaces").json()["workspaces"]]


def test_the_same_name_again_returns_the_one_already_there(app):
    c = browser(app)
    signup(c, "ann@example.com")
    tok = token_from_connect(c)
    first = mcp(app, tok, "create_workspace", name="Home Lineage")
    again = mcp(app, tok, "create_workspace", name="home lineage")
    assert again["created"] is False and again["workspace"]["handle"] == first["workspace"]["handle"]
    n = app.state.conn.execute("SELECT COUNT(*) FROM memberships WHERE user_id=?",
                               (user_id(app, "ann@example.com"),)).fetchone()[0]
    assert n == 2                                             # signup's, and this one


def test_a_name_is_required(app):
    c = browser(app)
    signup(c, "ann@example.com")
    tok = token_from_connect(c)
    assert "needs a name" in mcp(app, tok, "create_workspace", name="   ")["error"]


def test_an_agent_in_a_loop_is_stopped(app):
    c = browser(app)
    signup(c, "ann@example.com")
    tok = token_from_connect(c)
    made = [mcp(app, tok, "create_workspace", name=f"ws {i}") for i in range(20)]
    assert all(m["created"] for m in made)
    assert "too many" in mcp(app, tok, "create_workspace", name="ws 20")["error"]
    # a name it already has still answers
    assert mcp(app, tok, "create_workspace", name="ws 3")["created"] is False


def test_a_key_with_no_person_cannot_create_one(app):
    conn = app.state.conn
    orphan_ws = db.create_workspace(conn, "Nobody's", None)
    tok = db.create_token(conn, "operator-key", workspace_id=orphan_ws)
    assert "no person" in mcp(app, tok, "create_workspace", name="New")["error"]
