"""set_visibility: an agent reports or sets who can open a page, a folder or the
whole wiki, at the Share dialog's three levels (Forrest, 2026-10-01: "does the mcp
support changing the visibility of a page or a folder?", then "wire it up")."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import shares  # noqa: E402

from test_workspaces import app, browser, mcp, ws_id  # noqa: E402,F401
from test_sharing import owner_with_wiki  # noqa: E402


def opens(app, handle, path) -> bool:
    """Whether someone who is not signed in can read the page."""
    return browser(app).get(f"/api/v1/note?w={handle}&path={path}").status_code == 200


def test_reports_without_changing(app):
    _c, tok, handle = owner_with_wiki(app)
    r = mcp(app, tok, "set_visibility", path="notes/plan")
    assert r["ok"] and r["changed"] is False
    assert (r["kind"], r["path"], r["visibility"]) == ("page", "notes/plan", "restricted")
    assert r["url"].endswith(f"/w/{handle}/notes/plan")
    # an empty path is the whole wiki, a path with pages under it a folder
    assert mcp(app, tok, "set_visibility")["kind"] == "wiki"
    f = mcp(app, tok, "set_visibility", path="notes")
    assert f["kind"] == "folder" and f["url"].endswith(f"/w/{handle}?folder=notes")
    assert "no page or folder" in mcp(app, tok, "set_visibility", path="nope")["error"]


def test_a_page_opens_to_anyone_with_the_link_and_closes_again(app):
    _c, tok, handle = owner_with_wiki(app)
    assert not opens(app, handle, "notes/plan")
    r = mcp(app, tok, "set_visibility", path="notes/plan", visibility="link")
    assert r["changed"] and r["visibility"] == "link"
    assert opens(app, handle, "notes/plan") and not opens(app, handle, "secret/deal")
    again = mcp(app, tok, "set_visibility", path="notes/plan", visibility="link")
    assert again["changed"] is False
    r = mcp(app, tok, "set_visibility", path="notes/plan", visibility="restricted")
    assert r["changed"] and r["visibility"] == "restricted"
    assert not opens(app, handle, "notes/plan")


def test_a_folder_opens_everything_under_it_and_wins_over_a_page(app):
    _c, tok, handle = owner_with_wiki(app)
    mcp(app, tok, "set_visibility", path="notes", visibility="link")
    assert opens(app, handle, "notes/plan") and opens(app, handle, "notes/deep/more")
    assert not opens(app, handle, "index")
    # restricting a page inside a public folder changes nothing, and says why
    r = mcp(app, tok, "set_visibility", path="notes/deep/more", visibility="restricted")
    assert r["changed"] is False and r["visibility"] == "link"
    assert r["via"]["path"] == "notes" and "folder notes" in r["hint"]
    assert opens(app, handle, "notes/deep/more")


def test_publish_lists_on_dexio_wiki_and_link_takes_it_off(app):
    c, tok, handle = owner_with_wiki(app)
    wid = ws_id(app, handle)
    # a page cannot be published, and a workspace needs a publisher name first
    assert "wiki or a folder" in mcp(app, tok, "set_visibility", path="notes/plan",
                                     visibility="published")["error"]
    assert "publisher name" in mcp(app, tok, "set_visibility", path="notes",
                                   visibility="published")["error"]
    assert not opens(app, handle, "notes/plan")           # a refusal changes nothing
    shares.set_publisher(app.state.conn, wid, "acme-notes")
    r = mcp(app, tok, "set_visibility", path="notes", visibility="published",
            title="Acme notes", description="How we plan.")
    assert r["changed"] and r["visibility"] == "published"
    assert r["listing"] == {"title": "Acme notes", "description": "How we plan.",
                            "publisher": "acme-notes"}
    assert [d["title"] for d in shares.directory(app.state.conn)] == ["Acme notes"]
    assert opens(app, handle, "notes/plan")
    # a page under it reads as published, through the folder
    p = mcp(app, tok, "set_visibility", path="notes/plan")
    assert p["visibility"] == "published" and p["via"]["path"] == "notes"
    # link: off dexio.wiki, still open
    r = mcp(app, tok, "set_visibility", path="notes", visibility="link")
    assert r["visibility"] == "link" and "listing" not in r
    assert shares.directory(app.state.conn) == [] and opens(app, handle, "notes/plan")
    # restricted from published closes it and takes the listing with it
    mcp(app, tok, "set_visibility", path="notes", visibility="published")
    r = mcp(app, tok, "set_visibility", path="notes", visibility="restricted")
    assert r["visibility"] == "restricted" and shares.directory(app.state.conn) == []
    assert not opens(app, handle, "notes/plan")


def test_one_workspace_cannot_reach_anothers(app):
    _c, tok, handle = owner_with_wiki(app)
    _c2, tok2, handle2 = owner_with_wiki(app, "other@example.com")
    mcp(app, tok2, "set_visibility", path="notes", visibility="link")
    assert opens(app, handle2, "notes/plan") and not opens(app, handle, "notes/plan")
