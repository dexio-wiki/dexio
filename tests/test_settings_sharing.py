"""Settings > Sharing: everything a workspace shares outside itself, on one page
(Forrest, 2026-09-28: "in settings, can we make it easy to see what parts of the
wiki have been shared?"). See shares.overview and pages._sharing."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import shares  # noqa: E402

from test_sharing import code_of, owner_with_wiki, public, sent, share  # noqa: E402,F401
from test_workspaces import app, browser, mcp, signup, ws_id  # noqa: E402,F401


def rows(html: str, panel: str) -> list[str]:
    """The text of each row in one panel's table, tags stripped."""
    body = html.split(f'id="{panel}"', 1)[1].split('<div class="panel"', 1)[0]
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S)[1:]:
        text = re.sub(r"<form.*?</form>", " ", tr, flags=re.S)
        out.append(" ".join(re.sub(r"<[^>]+>", " ", text).split()))
    return out


def test_nothing_shared_says_so(app):
    c, _tok, handle = owner_with_wiki(app)
    r = c.get(f"/settings/sharing?w={handle}")
    assert r.status_code == 200
    assert "Nothing is shared" in r.text and f'href="/w/{handle}"' in r.text
    # it sits in the Workspace group of the sidebar, after Members
    nav = r.text.split('class="snav"', 1)[1].split("</nav>", 1)[0]
    assert nav.index(">Members<") < nav.index(">Sharing<") < nav.index(">Agents<")
    assert 'href="/settings/sharing" aria-current="page"' in nav


def test_lists_what_is_public_and_who_things_are_shared_with(app, sent):
    c, tok, handle = owner_with_wiki(app)
    public(c, handle, "folder", "notes")
    public(c, handle, "page", "notes/plan")          # inside a public folder: redundant
    share(c, handle, "page", "secret/deal", "ann@example.com")
    share(c, handle, "folder", "notes", "bob@example.com")
    bob = browser(app)
    signup(bob, "bob@example.com", first="Bob")
    assert bob.get(f"/s/{code_of(sent[-1][1])}").status_code == 303
    # a share on a page that is later deleted stays listed, and says so
    share(c, handle, "page", "index", "cy@example.com")
    assert mcp(app, tok, "delete_page", path="index").get("ok")

    ov = shares.overview(app.state.conn, ws_id(app, handle))
    assert ov["public_pages"] == 2 and ov["pages"] == 3
    assert [(p["kind"], p["path"], bool(p["via"])) for p in ov["public"]] == [
        ("folder", "notes", False), ("page", "notes/plan", True)]
    assert [(p["email"], p["pending"], p["exists"]) for p in ov["people"]] == [
        ("ann@example.com", True, True), ("bob@example.com", False, True),
        ("cy@example.com", True, False)]

    r = c.get(f"/settings/sharing?w={handle}")
    assert r.status_code == 200
    pub = rows(r.text, "public")
    assert len(pub) == 2
    assert pub[0].startswith("notes Folder, 2 pages ")
    assert "Plan notes/plan Already covered by the folder notes" in pub[1]
    assert "2 of 3 pages" in r.text
    # Forrest, 2026-09-28: "we don't need to callout the thing about search engines"
    assert "search engine" not in r.text.lower()
    # and, "yes", nor does the Share dialog (static/share.js, inlined in the graph page)
    graph = c.get(f"/w/{handle}", headers={"accept": "text/html"}).text
    assert "window.dexioShare = open" in graph and "search engines can list" not in graph
    # one row naming each person, then what they can view under it
    ppl = rows(r.text, "people")
    assert ppl[0] == "ann@example.com"
    assert ppl[1].startswith("Deal link not opened secret/deal")
    assert ppl[2] == "Bob bob@example.com"
    assert ppl[3].startswith("notes Folder, 2 pages ") and "not opened" not in ppl[3]
    assert ppl[4] == "cy@example.com"
    assert ppl[5].startswith("index link not opened index, deleted.")
    assert "3 people, 2 links not opened" in r.text
    # each thing links to itself in the graph; a deleted page has nothing to link to
    assert f'href="/w/{handle}?folder=notes"' in r.text
    assert f'href="/w/{handle}/secret/deal"' in r.text
    assert f'href="/w/{handle}/index"' not in r.text
    # the menus say what each will do
    assert ">Make private<" in r.text and ">Remove<" in r.text and ">Stop sharing<" in r.text


def test_the_menu_takes_a_share_away(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    public(c, handle, "page", "index")
    share(c, handle, "page", "secret/deal", "dee@example.com")
    dee = browser(app)
    signup(dee, "dee@example.com")
    dee.get(f"/s/{code_of(sent[-1][1])}")
    assert dee.get(f"/api/v1/note?w={handle}&path=secret/deal").status_code == 200
    anon = browser(app)
    assert anon.get(f"/api/v1/note?w={handle}&path=index").status_code == 200

    ov = shares.overview(app.state.conn, ws_id(app, handle))
    pub_id, dee_id = ov["public"][0]["id"], ov["people"][0]["id"]
    # another site cannot post it
    r = c.post(f"/settings/sharing/{dee_id}/stop?w={handle}",
               headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post(f"/settings/sharing/{dee_id}/stop?w={handle}")
    assert r.status_code == 303 and r.headers["location"].endswith("done=unshared")
    assert dee.get(f"/api/v1/note?w={handle}&path=secret/deal").status_code == 404
    r = c.post(f"/settings/sharing/{pub_id}/stop?w={handle}")
    assert r.status_code == 303 and r.headers["location"].endswith("done=private")
    assert anon.get(f"/api/v1/note?w={handle}&path=index").status_code != 200
    page = c.get(r.headers["location"])
    assert "Made private." in page.text and "Nothing is shared" in page.text
    # again: already gone
    assert c.post(f"/settings/sharing/{pub_id}/stop?w={handle}").status_code == 404


def test_one_workspace_cannot_take_away_anothers_share(app):
    c, _tok, handle = owner_with_wiki(app)
    public(c, handle, "page", "index")
    sid = shares.overview(app.state.conn, ws_id(app, handle))["public"][0]["id"]
    other = browser(app)
    signup(other, "other@example.com")
    mine = other.get("/api/v1/workspaces").json()["current"]
    assert other.post(f"/settings/sharing/{sid}/stop?w={mine}").status_code == 404
    assert other.post(f"/settings/sharing/{sid}/stop?w={handle}").status_code == 404
    assert shares.overview(app.state.conn, ws_id(app, handle))["public"]


def test_a_redundant_share_says_the_wider_one_still_applies(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "wiki", "", "eve@example.com")
    share(c, handle, "page", "index", "eve@example.com")
    ov = shares.overview(app.state.conn, ws_id(app, handle))
    page = next(p for p in ov["people"] if p["kind"] == "page")
    assert page["via"]["kind"] == "wiki"
    r = c.post(f"/settings/sharing/{page['id']}/stop?w={handle}")
    assert r.headers["location"].endswith("done=extra")
    text = c.get(r.headers["location"]).text
    assert "The wider share it sat under still gives the same access." in text
    assert "The whole wiki" in text
