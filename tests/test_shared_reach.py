"""An agent reads what other workspaces share with its person (Forrest, 2026-10-06:
"that means the api keys should be at the person level, yes?", then "go"). A key
acts as its creator: its own workspace as before, and, once the creator lets it in
Settings > Agents, what other workspaces share with them, read-only, through the
read tools' `workspace` argument and list_workspaces."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import db  # noqa: E402

from test_sharing import code_of, owner_with_wiki, public, sent, share  # noqa: E402,F401
from test_workspaces import app, browser, mcp, signup, token_from_connect  # noqa: E402,F401


def guest_with_key(app, sent, handle, owner, email="bea@example.com"):
    """An account the owner's `notes` folder is shared with, its own workspace's
    key, and that workspace's handle. The key does not read shares yet."""
    share(owner, handle, "folder", "notes", email)
    bea = browser(app)
    signup(bea, email)
    assert bea.get(f"/s/{code_of(sent[-1][1])}").status_code == 303
    own = bea.get("/api/v1/workspaces").json()["current"]
    tok = token_from_connect(bea)
    mcp(app, tok, "write_page", path="mine", text="# Mine\n\nBea's own page.\n")
    return bea, tok, own


def key_id(app, own) -> int:
    ws = db.workspace_by_handle(app.state.conn, own)
    return db.list_tokens(app.state.conn, ws["id"])[0]["id"]


def allow(bea, app, own, on=True):
    r = bea.post(f"/settings/tokens/{key_id(app, own)}/shared/{'on' if on else 'off'}?w={own}")
    assert r.status_code == 303 and f"done=shared_{'on' if on else 'off'}" in r.headers["location"]


def test_a_key_reads_only_its_own_workspace_until_its_creator_allows_more(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    r = mcp(app, tok, "list_pages", workspace=handle)
    assert "reads only its own workspace" in r["error"] and "/settings/agents" in r["error"]
    w = mcp(app, tok, "list_workspaces")
    assert w["workspace"]["handle"] == own and w["shared"] == [] and "Settings > Agents" in w["hint"]
    # its own workspace reads as always, with or without naming it
    assert [p["path"] for p in mcp(app, tok, "list_pages")["pages"]] == ["mine"]
    assert [p["path"] for p in mcp(app, tok, "list_pages", workspace=own)["pages"]] == ["mine"]


def test_once_allowed_it_reads_what_is_shared_and_nothing_else(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    allow(bea, app, own)
    w = mcp(app, tok, "list_workspaces")
    (s,) = w["shared"]
    assert s["handle"] == handle and s["access"] == "read" and s["shared"] == {"folders": ["notes"]}
    # list: only the shared folder, and it says whose and that it is read-only
    r = mcp(app, tok, "list_pages", workspace=handle)
    assert r["workspace"] == handle and r["read_only"] is True
    assert [p["path"] for p in r["pages"]] == ["notes/deep/more", "notes/plan"] and r["count"] == 2
    # a link into the workspace works as well as its handle
    link = f"https://app.dexio.wiki/w/{handle}/notes/plan"
    assert mcp(app, tok, "list_pages", workspace=link)["count"] == 2
    # read: links only to what is shared, no broken links, its address in that workspace
    p = mcp(app, tok, "read_page", path="notes/plan", workspace=handle)
    assert p["text"].startswith("# Plan") and p["url"].endswith(f"/w/{handle}/notes/plan")
    assert p["links_out"] == ["notes/deep/more"] and "broken_links" not in p
    assert "version" not in p
    # a page outside it reads as missing, the same as one that does not exist
    for path in ("secret/deal", "index", "nope"):
        e = mcp(app, tok, "read_page", path=path, workspace=handle)["error"]
        assert e.endswith(f"no page '{path}'"), e      # no similar pages offered from outside
    # no history from outside
    assert "members" in mcp(app, tok, "read_page", path="notes/plan", workspace=handle,
                            revision=1)["error"]
    # search: only shared pages
    hits = mcp(app, tok, "search_pages", query="confidential", workspace=handle)
    assert hits["pages_matched"] == 0
    hits = mcp(app, tok, "search_pages", query="plan", workspace=handle)
    assert {h["path"] for h in hits["results"]} <= {"notes/plan", "notes/deep/more"}
    assert hits["pages_matched"] >= 1
    # files: the one a shared page shows, not the other
    f = mcp(app, tok, "list_files", workspace=handle)
    assert [x["path"] for x in f["files"]] == ["notes/chart.png"]
    assert "no page" in mcp(app, tok, "read_page", path="raw/private.pdf", workspace=handle)["error"]
    # writing still goes to its own workspace only: no change tool takes a workspace
    tools = {t["name"]: t for t in app.state.live.post(
        "/mcp", headers={"Authorization": f"Bearer {tok}",
                         "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]}
    for name in ("list_pages", "read_page", "search_pages", "list_files"):
        assert "workspace" in tools[name]["inputSchema"]["properties"], name
    for name in ("write_page", "edit_page", "append_page", "delete_page", "move_page",
                 "change_pages", "upload_file", "delete_file", "set_visibility", "wiki_health",
                 "page_history"):
        assert "workspace" not in tools[name]["inputSchema"]["properties"], name


def test_stopping_the_share_or_the_setting_cuts_it_off_at_once(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    allow(bea, app, own)
    assert mcp(app, tok, "list_pages", workspace=handle)["count"] == 2
    allow(bea, app, own, on=False)
    assert "reads only its own workspace" in mcp(app, tok, "list_pages", workspace=handle)["error"]
    allow(bea, app, own)
    sid = app.state.conn.execute("SELECT id FROM shares WHERE path='notes'").fetchone()["id"]
    wid = db.workspace_by_handle(app.state.conn, handle)["id"]
    from dexio.server import shares
    assert shares.stop(app.state.conn, wid, sid)
    e = mcp(app, tok, "list_pages", workspace=handle)["error"]
    assert "shares anything with you" in e
    assert mcp(app, tok, "list_workspaces")["shared"] == []


def test_a_workspace_that_shares_nothing_reads_as_not_there(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    allow(bea, app, own)
    third = browser(app)
    signup(third, "cat@example.com")
    theirs = third.get("/api/v1/workspaces").json()["current"]
    a = mcp(app, tok, "list_pages", workspace=theirs)["error"]
    b = mcp(app, tok, "list_pages", workspace="no-such-handle")["error"]
    assert a.replace(theirs, "X") == b.replace("no-such-handle", "X")
    # an id is never a handle
    wid = db.workspace_by_handle(app.state.conn, handle)["id"]
    assert "shares anything" in mcp(app, tok, "list_pages", workspace=str(wid))["error"]


def test_a_public_page_reads_too_once_allowed(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    public(owner, handle, "page", "secret/deal")
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    allow(bea, app, own)
    p = mcp(app, tok, "read_page", path="secret/deal", workspace=handle)
    assert p["text"].startswith("# Deal")


def test_only_the_keys_creator_can_let_it_read_their_shares(app, sent, monkeypatch):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    # a key with no creator on record never reads shares
    ws = db.workspace_by_handle(app.state.conn, own)
    bare = db.create_token(app.state.conn, "old-key", workspace_id=ws["id"])
    row = app.state.conn.execute("SELECT id FROM tokens WHERE created_by IS NULL").fetchone()
    r = bea.post(f"/settings/tokens/{row['id']}/shared/on?w={own}")
    assert r.status_code == 404
    assert "reads only its own" in mcp(app, bare, "list_pages", workspace=handle)["error"]
    # Settings > Agents: the menu item on her own key; the state shows under its name
    page = bea.get(f"/settings/agents?w={own}").text
    assert "Let it read what&#x27;s shared with you" in page or \
        "Let it read what's shared with you" in page
    allow(bea, app, own)
    page = bea.get(f"/settings/agents?w={own}").text
    assert "also reads what&#x27;s shared with you" in page or "also reads what's shared" in page
    assert "Stop it reading what" in page
    assert "turn that on from its" in page


def test_a_workspace_you_are_a_member_of_is_for_an_agent_connected_there(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    allow(bea, app, own)
    conn = app.state.conn
    uid = conn.execute("SELECT id FROM users WHERE email='bea@example.com'").fetchone()["id"]
    db.add_member(conn, db.workspace_by_handle(conn, handle)["id"], uid)
    assert "you are a member" in mcp(app, tok, "list_pages", workspace=handle)["error"]
    assert mcp(app, tok, "list_workspaces")["shared"] == []
