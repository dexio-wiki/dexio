"""A key acts as its person (Forrest, 2026-10-06: "can you migrate all existing
workspace keys to the user, and have it allow access to anything the user can
see?"). Every tool works in the key's own workspace unless it names another with
`workspace`: one the person is a member of, read and write, or one that shares
pages with them, read-only. list_workspaces lists them."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import db, shares  # noqa: E402

from test_sharing import code_of, owner_with_wiki, public, sent, share  # noqa: E402,F401
from test_workspaces import app, browser, mcp, signup, token_from_connect  # noqa: E402,F401


def guest_with_key(app, sent, handle, owner, email="bea@example.com"):
    """An account the owner's `notes` folder is shared with, its own workspace's
    key, and that workspace's handle."""
    share(owner, handle, "folder", "notes", email)
    bea = browser(app)
    signup(bea, email)
    assert bea.get(f"/s/{code_of(sent[-1][1])}").status_code == 303
    own = bea.get("/api/v1/workspaces").json()["current"]
    tok = token_from_connect(bea)
    mcp(app, tok, "write_page", path="mine", text="# Mine\n\nBea's own page.\n")
    return bea, tok, own


def user_id(app, email) -> int:
    return app.state.conn.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()["id"]


def ws_of(app, handle) -> int:
    return db.workspace_by_handle(app.state.conn, handle)["id"]


def test_a_key_reads_what_is_shared_with_its_person_and_nothing_else(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    w = mcp(app, tok, "list_workspaces")
    assert w["workspace"]["handle"] == own and w["member_of"] == []
    (s,) = w["shared"]
    assert s["handle"] == handle and s["access"] == "read" and s["shared"] == {"folders": ["notes"]}
    # its own workspace reads as always, with or without naming it
    assert [p["path"] for p in mcp(app, tok, "list_pages")["pages"]] == ["mine"]
    assert [p["path"] for p in mcp(app, tok, "list_pages", workspace=own)["pages"]] == ["mine"]
    # list: only the shared folder, and it says whose and that it is read-only
    r = mcp(app, tok, "list_pages", workspace=handle)
    assert r["workspace"] == handle and r["read_only"] is True
    assert [p["path"] for p in r["pages"]] == ["notes/deep/more", "notes/plan"] and r["count"] == 2
    # a link into the workspace works as well as its handle
    assert mcp(app, tok, "list_pages", workspace=f"https://app.dexio.wiki/w/{handle}/notes/plan")[
        "count"] == 2
    # read: links only to what is shared, no broken links or version, its address there
    p = mcp(app, tok, "read_page", path="notes/plan", workspace=handle)
    assert p["text"].startswith("# Plan") and p["url"].endswith(f"/w/{handle}/notes/plan")
    assert p["links_out"] == ["notes/deep/more"] and "broken_links" not in p and "version" not in p
    # a page outside it reads as missing, the same as one that does not exist
    for path in ("secret/deal", "index", "nope"):
        e = mcp(app, tok, "read_page", path=path, workspace=handle)["error"]
        assert e.endswith(f"no page '{path}'"), e      # no similar pages offered from outside
    # search: only shared pages; files: the one a shared page shows
    assert mcp(app, tok, "search_pages", query="confidential", workspace=handle)["pages_matched"] == 0
    hits = mcp(app, tok, "search_pages", query="plan", workspace=handle)
    assert hits["pages_matched"] >= 1
    assert {h["path"] for h in hits["results"]} <= {"notes/plan", "notes/deep/more"}
    assert [x["path"] for x in mcp(app, tok, "list_files", workspace=handle)["files"]] == \
        ["notes/chart.png"]
    assert "no page" in mcp(app, tok, "read_page", path="raw/private.pdf", workspace=handle)["error"]
    # read-only: no changes, history or health from outside
    for tool, args in (("write_page", {"path": "notes/x", "text": "# X\n"}),
                       ("edit_page", {"path": "notes/plan", "old_text": "plan", "new_text": "x"}),
                       ("delete_page", {"path": "notes/plan"}),
                       ("page_history", {}), ("wiki_health", {}),
                       ("set_visibility", {"path": "notes"})):
        e = mcp(app, tok, tool, workspace=handle, **args)["error"]
        assert "only its members" in e, (tool, e)
    assert "members" in mcp(app, tok, "read_page", path="notes/plan", workspace=handle,
                            revision=1)["error"]
    assert db.note(app.state.conn, db.wiki_key(ws_of(app, handle)), "notes/x") is None


def test_every_tool_takes_a_workspace(app):
    c = browser(app)
    signup(c, "ann@example.com")
    tok = token_from_connect(c)
    tools = app.state.live.post(
        "/mcp", headers={"Authorization": f"Bearer {tok}",
                         "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}).json()["result"]["tools"]
    for t in tools:
        assert ("workspace" in t["inputSchema"]["properties"]) == (t["name"] != "list_workspaces"), \
            t["name"]


def test_a_key_works_in_every_workspace_its_person_is_a_member_of(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    conn = app.state.conn
    db.add_member(conn, ws_of(app, handle), user_id(app, "bea@example.com"))
    w = mcp(app, tok, "list_workspaces")
    assert [m["handle"] for m in w["member_of"]] == [handle] and w["shared"] == []
    assert w["member_of"][0]["access"] == "read and write"
    # everything reads, unfiltered, with history and health
    assert mcp(app, tok, "list_pages", workspace=handle)["count"] == 4
    p = mcp(app, tok, "read_page", path="secret/deal", workspace=handle)
    assert p["text"].startswith("# Deal") and "version" in p and "read_only" not in p
    assert p["url"].endswith(f"/w/{handle}/secret/deal")
    assert "changes" in mcp(app, tok, "page_history", workspace=handle)
    assert "error" not in mcp(app, tok, "wiki_health", workspace=handle)
    # and it writes there, recorded against her, not in her own workspace
    r = mcp(app, tok, "write_page", path="notes/from-bea", text="# From Bea\n", workspace=handle)
    assert r.get("created") and r["url"].endswith(f"/w/{handle}/notes/from-bea")
    assert db.note(conn, db.wiki_key(ws_of(app, handle)), "notes/from-bea")
    assert db.note(conn, db.wiki_key(ws_of(app, own)), "notes/from-bea") is None
    hist = mcp(app, tok, "page_history", path="notes/from-bea", workspace=handle)
    assert hist["revisions"][0]["person"] in ("bea", "bea@example.com")
    # removed from it, the key is back to what is shared with her, read-only; her own
    # workspace still works
    from dexio.server import membership
    membership.remove_member(conn, ws_of(app, handle), user_id(app, "bea@example.com"),
                             user_id(app, "owner@example.com"))
    r = mcp(app, tok, "list_pages", workspace=handle)
    assert r["read_only"] is True and r["count"] == 3          # notes/, with her page in it
    assert "only its members" in mcp(app, tok, "write_page", path="notes/again", text="# A\n",
                                     workspace=handle)["error"]
    assert mcp(app, tok, "list_pages")["count"] == 1


def test_stopping_the_share_cuts_it_off_at_once(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    assert mcp(app, tok, "list_pages", workspace=handle)["count"] == 2
    sid = app.state.conn.execute("SELECT id FROM shares WHERE path='notes'").fetchone()["id"]
    assert shares.stop(app.state.conn, ws_of(app, handle), sid)
    assert "no workspace" in mcp(app, tok, "list_pages", workspace=handle)["error"]
    assert mcp(app, tok, "list_workspaces")["shared"] == []


def test_a_workspace_that_shares_nothing_reads_as_not_there(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    third = browser(app)
    signup(third, "cat@example.com")
    theirs = third.get("/api/v1/workspaces").json()["current"]
    a = mcp(app, tok, "list_pages", workspace=theirs)["error"]
    b = mcp(app, tok, "list_pages", workspace="no-such-handle")["error"]
    assert a.replace(theirs, "X") == b.replace("no-such-handle", "X")
    assert "no workspace" in mcp(app, tok, "list_pages", workspace=str(ws_of(app, handle)))["error"]


def test_a_public_page_reads_too(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    public(owner, handle, "page", "secret/deal")
    bea, tok, own = guest_with_key(app, sent, handle, owner)
    assert mcp(app, tok, "read_page", path="secret/deal", workspace=handle)["text"].startswith("# Deal")


def test_keys_from_before_creators_go_to_the_workspace_owner(app):
    c = browser(app)
    signup(c, "ann@example.com")
    handle = c.get("/api/v1/workspaces").json()["current"]
    conn = app.state.conn
    old = db.create_token(conn, "operator-key", workspace_id=ws_of(app, handle))
    row = db.check_token(conn, old)
    assert row["created_by"] is None
    db.keys_to_people(conn)
    assert db.check_token(conn, old)["created_by"] == user_id(app, "ann@example.com")
    assert mcp(app, old, "list_workspaces")["workspace"]["handle"] == handle


def test_deleting_an_account_deletes_its_keys(app, sent):
    owner, _tok, handle = owner_with_wiki(app)
    conn = app.state.conn
    bea = browser(app)
    signup(bea, "bea@example.com")
    db.add_member(conn, ws_of(app, handle), user_id(app, "bea@example.com"))
    r = bea.post(f"/api/v1/connect?w={handle}", json={"client": "hermes"})
    assert r.status_code == 200
    before = len(db.list_tokens(conn, ws_of(app, handle)))
    from dexio.server import erase
    erase.delete_account(conn, user_id(app, "bea@example.com"))
    assert len(db.list_tokens(conn, ws_of(app, handle))) == before - 1


def test_agents_is_an_account_section_listing_your_own_agents(app, sent):
    """Forrest, 2026-10-06: "why is the Agents screen still a part of the Workspace
    settings area?" Agents sits under Account and lists your keys and sign-ins in
    every workspace, with where each starts; you revoke only your own."""
    import re
    owner, _tok, handle = owner_with_wiki(app)
    conn = app.state.conn
    bea = browser(app)
    signup(bea, "bea@example.com")
    own = bea.get("/api/v1/workspaces").json()["current"]
    db.add_member(conn, ws_of(app, handle), user_id(app, "bea@example.com"))
    assert bea.post(f"/api/v1/connect?w={own}", json={"client": "hermes"}).status_code == 200
    assert bea.post(f"/api/v1/connect?w={handle}", json={"client": "claude-code"}).status_code == 200
    page = bea.get(f"/settings/agents?w={own}").text
    nav = page.split('class="snav"', 1)[1].split("</nav>", 1)[0]
    account = nav.split('<div class="sgroup">Account</div>', 1)[1]
    workspace = nav.split('<div class="sgroup">Account</div>', 1)[0]
    assert 'href="/settings/agents"' in account and 'href="/settings/agents"' not in workspace
    assert "Your agents" in page
    assert "starts in owner&#x27;s Workspace" in page and "starts in bea&#x27;s Workspace" in page
    # the owner's own key is not in Bea's list, and she cannot revoke it
    assert page.count("/revoke") == 2
    owner_key = conn.execute("SELECT id FROM tokens WHERE created_by=?",
                             (user_id(app, "owner@example.com"),)).fetchone()["id"]
    assert bea.post(f"/settings/tokens/{owner_key}/revoke").status_code == 404
    # nor does the owner see Bea's key made in the owner's workspace
    assert page.count("Claude Code") >= 1
    assert "Claude Code" not in owner.get(f"/settings/agents?w={handle}").text.split(
        'id="connected"', 1)[1]
    # switching workspace in the sidebar stays on Agents
    assert f'href="/settings/agents?w={handle}"' in nav
    tid = re.findall(r"/settings/tokens/(\d+)/revoke", page)[0]
    assert bea.post(f"/settings/tokens/{tid}/revoke").status_code == 303
