"""Who made a change: the agent (a required parameter), the person (the account
behind the token or the OAuth sign-in, recorded by the server), and the token
or app it came through. page_history filters by agent."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, db  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import browser, mcp, signup, token_from_connect  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    a = get_app(str(tmp_path / "a.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        yield a


def test_person_and_agent_on_every_change_and_history_filter(app):
    c = browser(app)
    signup(c, "ann@example.com")
    conn = app.state.conn
    uid = auth.user_by_email(conn, "ann@example.com")["id"]
    auth.set_names(conn, uid, "Ann", "Lee")
    tok = token_from_connect(c)                     # created by Ann: person is Ann
    mcp(app, tok, "write_page", wiki="main", path="a", text="# A", agent="owen")
    mcp(app, tok, "edit_page", wiki="main", path="a", old_text="# A", new_text="# A2",
        agent="niko")
    mcp(app, tok, "write_page", wiki="main", path="b", text="# B", agent="Owen")

    h = mcp(app, tok, "page_history", wiki="main", path="a")
    assert [(r["agent"], r["person"]) for r in h["revisions"]] == [
        ("niko", "Ann Lee"), ("owen", "Ann Lee")]
    assert h["revisions"][0]["author"] == "Hermes"

    # Filter by agent, case-insensitive, with and without a path.
    h = mcp(app, tok, "page_history", wiki="main", agent="OWEN")
    assert [(r["path"], r["agent"]) for r in h["changes"]] == [("b", "Owen"), ("a", "owen")]
    h = mcp(app, tok, "page_history", wiki="main", path="a", agent="niko")
    assert [r["agent"] for r in h["revisions"]] == ["niko"]
    h = mcp(app, tok, "page_history", wiki="main", path="a", agent="nobody")
    assert h["revisions"] == [] and "nobody" in h["note"]

    # The person is on a past version too.
    rev = mcp(app, tok, "page_history", wiki="main", path="a")["revisions"][-1]["revision"]
    assert mcp(app, tok, "read_page", wiki="main", path="a", revision=rev)["person"] == "Ann Lee"


def test_operator_token_is_recorded_against_the_workspace_owner(app):
    c = browser(app)
    signup(c, "bob@example.com")
    conn = app.state.conn
    uid = auth.user_by_email(conn, "bob@example.com")["id"]
    ws = db.workspaces_for_user(conn, uid)[0]["id"]
    tok = db.create_token(conn, "ops", workspace_id=ws)          # no creator on record
    mcp(app, tok, "write_page", wiki="main", path="x", text="# X", agent="ci")
    r = mcp(app, tok, "page_history", wiki="main", path="x")["revisions"][0]
    assert r["person"] == "bob" and r["author"] == "ops"


def test_person_of_prefers_the_signed_in_account():
    class Conn:  # person_of must not query when the row names its person
        def execute(self, *a):
            raise AssertionError("no query expected")
    assert db.person_of(Conn(), {"user_id": 7, "workspace_id": 1, "created_by": None}) == 7
    assert db.person_of(Conn(), {"created_by": 3, "workspace_id": 1}) == 3
