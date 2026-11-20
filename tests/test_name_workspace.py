"""A new account names its workspace (Forrest, 2026-09-28). With one wiki per
workspace the workspace's name is the only name the wiki has, and sign-up gives
it "<first name>'s Workspace" without asking. The first graph screen asks an
owner once, with that name filled in, before anything else."""
from __future__ import annotations

import sqlite3
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import db  # noqa: E402

from test_workspaces import app, browser, invite_code, signup, ws_id  # noqa: E402,F401


def asks(page: str) -> bool:
    return "window.DEXIO_ASK_NAME = true;" in page


def current(c) -> str:
    return c.get("/api/v1/workspaces").json()["current"]


def test_a_new_account_is_asked_once_and_the_name_sticks(app):
    ann = browser(app)
    signup(ann, "ann@example.com", first="Ann")
    ws = current(ann)
    page = ann.get(f"/w/{ws}").text
    assert asks(page)
    assert '<div id="wrap" class="onboarding">' in page      # nothing else shows first
    assert "Ann&#x27;s Workspace" in page                     # the default is there to keep
    r = ann.post(f"/api/v1/workspace/name?w={ws}", json={"name": "  Acme Coffee  "})
    assert r.status_code == 200 and r.json() == {"id": ws, "name": "Acme Coffee"}
    assert db.workspace(app.state.conn, ws_id(app, ws))["name"] == "Acme Coffee"
    page = ann.get(f"/w/{ws}").text
    assert not asks(page) and "Acme Coffee" in page


def test_keeping_the_name_it_came_with_counts(app):
    ann = browser(app)
    signup(ann, "ann@example.com", first="Ann")
    ws = current(ann)
    assert ann.post(f"/api/v1/workspace/name?w={ws}",
                    json={"name": "Ann's Workspace"}).status_code == 200
    assert not asks(ann.get(f"/w/{ws}").text)


def test_a_blank_name_other_workspaces_and_other_sites_are_refused(app):
    ann = browser(app)
    signup(ann, "ann@example.com")
    ws = current(ann)
    assert ann.post(f"/api/v1/workspace/name?w={ws}", json={"name": "  "}).status_code == 400
    assert ann.post(f"/api/v1/workspace/name?w={ws}", json=["Acme"]).status_code == 400
    assert asks(ann.get(f"/w/{ws}").text)                     # still unnamed
    r = ann.post(f"/api/v1/workspace/name?w={ws}", json={"name": "Evil"},
                 headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert browser(app).post("/api/v1/workspace/name", json={"name": "x"}).status_code == 401
    # someone else's workspace id falls back to your own, which you may name
    bob = browser(app)
    signup(bob, "bob@example.com")
    r = bob.post(f"/api/v1/workspace/name?w={ws}", json={"name": "Bob's"})
    assert r.status_code == 200 and r.json()["id"] != ws
    assert db.workspace(app.state.conn, ws_id(app, ws))["name"] != "Bob's"


def test_members_are_not_asked_and_cannot_name_it(app, monkeypatch):
    ann = browser(app)
    signup(ann, "ann@example.com")
    ws = current(ann)
    app.state.conn.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws_id(app, ws),))
    app.state.conn.commit()
    code = invite_code(ann, monkeypatch, "guest@example.com", w=ws)
    guest = browser(app)
    signup(guest, "guest@example.com", next_=f"/invite/{code}")
    assert guest.get(f"/invite/{code}").status_code == 303
    assert not asks(guest.get(f"/w/{ws}").text)
    r = guest.post(f"/api/v1/workspace/name?w={ws}", json={"name": "Mine now"})
    assert r.status_code == 403


def test_a_name_typed_elsewhere_counts(app):
    ann = browser(app)
    signup(ann, "ann@example.com")
    ws = current(ann)
    # renamed in Settings before ever seeing the question
    assert ann.post(f"/settings/rename?w={ws}", data={"name": "Acme"}).status_code == 303
    assert not asks(ann.get(f"/w/{ws}").text)
    # a workspace made from the menu with a name typed in is named already
    r = ann.post("/settings/workspaces", data={"name": "Side project"})
    assert r.status_code == 303
    side = ann.get("/api/v1/workspaces").json()["workspaces"][-1]["id"]
    assert not asks(ann.get(f"/w/{side}").text)
    # one made with the name left blank is asked about
    ann.post("/settings/workspaces", data={"name": ""})
    blank = ann.get("/api/v1/workspaces").json()["workspaces"][-1]["id"]
    assert asks(ann.get(f"/w/{blank}").text)


@pytest.mark.sqlite_only
def test_workspaces_from_before_the_question_are_not_asked(tmp_path):
    path = tmp_path / "old.db"
    conn = db.connect(str(path))
    conn.close()
    old = sqlite3.connect(path)
    old.execute("ALTER TABLE workspaces DROP COLUMN named_at")
    old.execute("INSERT INTO workspaces (name, plan, created_at) VALUES ('Old', 'free', ?)",
                (time.time() - 3600,))
    old.commit()
    old.close()
    conn = db.connect(str(path))
    row = conn.execute("SELECT created_at, named_at FROM workspaces WHERE name='Old'").fetchone()
    assert row["named_at"] == row["created_at"]
    # and a workspace made after the upgrade is not
    ws = db.create_workspace(conn, "New", None)
    assert db.workspace(conn, ws_id(app, ws))["named_at"] is None


# ---- How did you hear about Dexio? (Forrest, 2026-11-19) -----------------------------

def asks_heard(page: str) -> bool:
    return "window.DEXIO_ASK_HEARD = true;" in page


def heard_of(app, email):
    c = app.state.conn
    uid = c.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]
    row = c.execute("SELECT heard, heard_other FROM signups WHERE user_id=?", (uid,)).fetchone()
    return (row["heard"], row["heard_other"])


def test_the_name_screen_asks_how_they_heard_and_keeps_the_answer(app):
    ann = browser(app)
    signup(ann, "ann@example.com", first="Ann")
    ws = current(ann)
    assert asks_heard(ann.get(f"/w/{ws}").text)
    r = ann.post(f"/api/v1/workspace/name?w={ws}", json={"name": "Acme", "heard": "ai"})
    assert r.status_code == 200
    assert heard_of(app, "ann@example.com") == ("ai", None)
    # asked once per account: a blank workspace from the menu asks for a name, not this
    ann.post("/settings/workspaces", data={"name": ""})
    blank = ann.get("/api/v1/workspaces").json()["workspaces"][-1]["id"]
    page = ann.get(f"/w/{blank}").text
    assert asks(page) and not asks_heard(page)
    # and a second answer does not replace the first
    ann.post(f"/api/v1/workspace/name?w={blank}", json={"name": "Side", "heard": "reddit"})
    assert heard_of(app, "ann@example.com") == ("ai", None)
    from dexio.server import signups
    assert signups.report(app.state.conn)["by_heard"] == {"ai": 1}


def test_other_needs_words_and_unknown_choices_are_refused(app):
    ann = browser(app)
    signup(ann, "ann@example.com")
    ws = current(ann)
    for body in ({"name": "Acme", "heard": "tv"}, {"name": "Acme", "heard": "other"},
                 {"name": "Acme", "heard": "other", "heard_other": "   "}):
        assert ann.post(f"/api/v1/workspace/name?w={ws}", json=body).status_code == 400
    assert asks(ann.get(f"/w/{ws}").text)                     # nothing half-applied
    r = ann.post(f"/api/v1/workspace/name?w={ws}",
                 json={"name": "Acme", "heard": "other", "heard_other": " 播客\x07 " + "x" * 200})
    assert r.status_code == 200
    heard, words = heard_of(app, "ann@example.com")
    assert heard == "other" and words.startswith("播客 x") and len(words) == 100
    # words sent with any other choice are not kept
    bob = browser(app)
    signup(bob, "bob@example.com")
    bob.post(f"/api/v1/workspace/name?w={current(bob)}",
             json={"name": "B", "heard": "friend", "heard_other": "Sam"})
    assert heard_of(app, "bob@example.com") == ("friend", None)


def test_a_page_from_before_the_question_still_names_the_workspace(app):
    ann = browser(app)
    signup(ann, "ann@example.com")
    ws = current(ann)
    assert ann.post(f"/api/v1/workspace/name?w={ws}", json={"name": "Acme"}).status_code == 200
    assert heard_of(app, "ann@example.com") == (None, None)
    assert not asks(ann.get(f"/w/{ws}").text)


def test_the_labels_on_the_screen_match_what_is_stored():
    import re
    from dexio import render
    from dexio.server import signups
    block = render.APP_JS[render.APP_JS.index("const HEARD = ["):]
    block = block[:block.index("]];") + 3]
    pairs = re.findall(r'\["(\w+)", "([^"]+)"\]', block)
    assert dict(pairs) == signups.HEARD and [k for k, _ in pairs] == list(signups.HEARD)


def test_a_signups_table_from_before_gets_the_columns(app):
    from dexio.server import signups
    c = app.state.conn
    c.execute("ALTER TABLE signups DROP COLUMN heard")
    c.execute("ALTER TABLE signups DROP COLUMN heard_other")
    c.commit()
    signups.init(c)
    have = {r["name"] for r in c.execute("PRAGMA table_info(signups)")}
    assert {"heard", "heard_other"} <= have
