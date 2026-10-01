"""The directory on dexio.wiki and Make a copy (Forrest, 2026-10-01: "i'd rather
have users opt in to publish their wiki to the dexio website when they share
publically - and if we want to put out some official dexio templates, we can do
so by creating pages within the dexio wiki and then sharing them"). See
shares.set_listed, shares.directory and copies.py."""
from __future__ import annotations

from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import shares  # noqa: E402

from test_sharing import owner_with_wiki, public  # noqa: E402
from test_workspaces import app, browser, mcp, signup, token_from_connect, ws_id  # noqa: E402,F401

HTML = {"accept": "text/html"}


def owner(app, email="owner@example.com", publisher="owner-co"):
    """owner_with_wiki, with the workspace's publisher name set in Settings, General
    (publishing needs one; Forrest, 2026-10-01)."""
    c, tok, handle = owner_with_wiki(app, email)
    if publisher:
        r = c.post(f"/settings/publisher?w={handle}", data={"publisher": publisher})
        assert r.status_code == 303, r.text
    return c, tok, handle


def listed(c, handle, kind, path, on=True, status=200):
    r = c.post(f"/api/v1/share/listed?w={handle}", json={"kind": kind, "path": path, "on": on})
    assert r.status_code == status, r.text
    return r.json()


def entries(app):
    r = browser(app).get("/api/v1/directory")
    assert r.status_code == 200
    return r.json()["wikis"]


def listed_notes(app, c, handle, tok):
    """The owner's notes folder public and listed, with a front page."""
    mcp(app, tok, "write_page", path="notes/index",
        text="# Notes starter\n\nA starter for keeping research notes with agents.\n")
    public(c, handle, "folder", "notes")
    d = listed(c, handle, "folder", "notes")
    return d, entries(app)[0]


def test_only_something_public_on_its_own_can_be_listed(app):
    c, _tok, handle = owner(app)
    d = c.get(f"/api/v1/share?w={handle}&kind=folder&path=notes").json()
    assert d["public"]["listed"] is False
    r = c.post(f"/api/v1/share/listed?w={handle}", json={"kind": "folder", "path": "notes",
                                                         "on": True})
    assert r.status_code == 400 and "public on the web first" in r.json()["error"]
    # a page public only because its folder is: list the folder instead
    public(c, handle, "folder", "notes")
    r = c.post(f"/api/v1/share/listed?w={handle}", json={"kind": "page", "path": "notes/plan",
                                                         "on": True})
    assert r.status_code == 400
    d = listed(c, handle, "folder", "notes")
    assert d["public"]["on"] and d["public"]["listed"] is True
    # turning it off works, and so does asking to unlist what is not listed
    assert listed(c, handle, "folder", "notes", on=False)["public"]["listed"] is False
    assert listed(c, handle, "page", "index", on=False)["public"]["listed"] is False
    # only members of the workspace list anything
    other = browser(app)
    signup(other, "stranger@example.com")
    r = other.post(f"/api/v1/share/listed?w={handle}", json={"kind": "wiki", "path": "",
                                                             "on": True})
    assert r.status_code == 403


def test_the_directory_lists_what_owners_listed_and_nothing_else(app):
    c, tok, handle = owner(app)
    assert entries(app) == []
    public(c, handle, "page", "index")                         # public, not listed
    c.post(f"/api/v1/share?w={handle}", json={"kind": "folder", "path": "secret",
                                              "email": "x@example.com"})
    assert entries(app) == []
    _d, e = listed_notes(app, c, handle, tok)
    r = browser(app).get("/api/v1/directory")
    assert r.headers["access-control-allow-origin"] == "*"
    assert "max-age=300" in r.headers["cache-control"]
    assert len(r.json()["wikis"]) == 1
    assert e["title"] == "Notes starter" and e["kind"] == "folder" and e["path"] == "notes"
    assert e["description"] == "A starter for keeping research notes with agents."
    assert e["pages"] == 3 and e["workspace"] == "owner's Workspace" and e["handle"] == handle
    assert e["url"] == f"https://app.dexio.wiki/w/{handle}?folder=notes"
    assert e["copy_url"] == f"https://app.dexio.wiki/copy?from={e['id']}"
    assert "Confidential" not in r.text and "secret" not in r.text
    # opening it again keeps the listing; making it private takes it out
    public(c, handle, "folder", "notes")
    assert len(entries(app)) == 1
    public(c, handle, "folder", "notes", on=False)
    assert entries(app) == []
    public(c, handle, "folder", "notes")                     # public again, not listed again
    assert entries(app) == []


def test_a_listed_whole_wiki_is_named_for_its_workspace(app):
    c, _tok, handle = owner(app)
    public(c, handle, "wiki", "")
    listed(c, handle, "wiki", "")
    (e,) = entries(app)
    assert e["title"] == "owner's Workspace" and e["pages"] == 4
    assert e["description"] == "See notes/plan and secret/deal."    # from index
    assert e["url"] == f"https://app.dexio.wiki/w/{handle}"


def test_a_guest_sees_make_a_copy_only_where_something_is_listed(app):
    c, tok, handle = owner(app)
    public(c, handle, "folder", "notes")
    anon = browser(app)
    page = anon.get(f"/w/{handle}/notes/plan", headers=HTML).text
    assert 'href="/signup">Try Dexio free</a>' in page and ">Make a copy</a>" not in page
    _d, e = listed_notes(app, c, handle, tok)
    page = anon.get(f"/w/{handle}/notes/plan", headers=HTML).text
    assert f'href="/copy?from={e["id"]}">Make a copy</a>' in page
    assert 'href="/signup">Try Dexio free</a>' not in page
    # signed in, not a member: the button sits before the account menu
    other = browser(app)
    signup(other, "reader@example.com")
    assert ">Make a copy</a>" in other.get(f"/w/{handle}/notes/plan", headers=HTML).text


def test_signed_out_make_a_copy_signs_up_first_and_comes_back(app):
    c, tok, handle = owner(app)
    _d, e = listed_notes(app, c, handle, tok)
    anon = browser(app)
    r = anon.get(f"/copy?from={e['id']}")
    assert r.status_code == 303
    loc = urlsplit(r.headers["location"])
    assert loc.path == "/signup" and parse_qs(loc.query)["next"] == [f"/copy?from={e['id']}"]
    form = anon.get(r.headers["location"]).text
    assert "listed this on Dexio for anyone to copy" in form
    r = signup(anon, "newbie@example.com", next_=f"/copy?from={e['id']}")
    assert r.status_code == 303
    page = anon.get(f"/copy?from={e['id']}")
    assert page.status_code == 200 and page.headers["x-robots-tag"] == "noindex"
    assert "Notes starter" in page.text and "3 pages" in page.text
    # the new account's own workspace is empty, so it is the one picked
    mine = anon.get("/api/v1/workspaces").json()["current"]
    assert f'value="{mine}" checked' in page.text
    assert 'value="new"' in page.text and "<select" not in page.text


def test_only_listed_things_can_be_copied(app):
    c, _tok, handle = owner(app)
    public(c, handle, "folder", "notes")                # public, not listed
    sid = app.state.conn.execute("SELECT id FROM shares WHERE path='notes'").fetchone()["id"]
    other = browser(app)
    signup(other, "copier@example.com")
    for addr in (f"/copy?from={sid}", "/copy?from=nope", "/copy"):
        r = other.get(addr)
        assert r.status_code == 404 and "not listed for copying" in r.text, addr
    r = other.post("/copy", data={"from": str(sid), "to": "new"})
    assert r.status_code == 404
    assert len(other.get("/api/v1/workspaces").json()["workspaces"]) == 1


def test_a_copy_lands_in_your_workspace_with_its_paths_files_and_history(app):
    c, tok, handle = owner(app)
    _d, e = listed_notes(app, c, handle, tok)
    other = browser(app)
    signup(other, "copier@example.com")
    otok = token_from_connect(other)
    mcp(app, otok, "write_page", path="notes/plan", text="# My own plan\n\nKeep this.\n")
    mine = other.get("/api/v1/workspaces").json()["current"]
    r = other.post("/copy", data={"from": str(e["id"]), "to": mine})
    assert r.status_code == 303 and r.headers["location"] == f"/w/{mine}?folder=notes"
    conn, wid = app.state.conn, ws_id(app, mine)
    k = f"{wid}:main"
    got = {r["path"]: r["text"] for r in conn.execute(
        "SELECT path, text FROM pages WHERE project=?", (k,))}
    assert set(got) == {"notes/plan", "notes/index", "notes/deep/more"}
    assert got["notes/plan"].startswith("# My own plan")     # never overwritten
    assert got["notes/deep/more"].startswith("# More")
    g = other.get(f"/api/v1/graph?w={mine}").json()
    assert g["stats"]["dangling"] == 0
    # the file the copied page shows came along; the one outside did not
    files = {f["path"] for f in g["files"]}
    assert files == {"notes/chart.png"}
    # history says who copied it, and from where
    rev = conn.execute("SELECT note, user_id, op FROM revisions WHERE project=? AND"
                       " path='notes/index' ORDER BY id DESC", (k,)).fetchone()
    assert rev["note"].startswith("Copied from Notes starter, https://app.dexio.wiki/w/")
    assert rev["op"] == "write" and rev["user_id"]
    # the source is untouched
    src = {r["path"] for r in conn.execute("SELECT path FROM pages WHERE project=?",
                                           (f"{ws_id(app, handle)}:main",))}
    assert src == {"index", "notes/plan", "notes/deep/more", "secret/deal", "notes/index"}


def test_a_copy_into_a_new_workspace(app):
    c, tok, handle = owner(app)
    _d, e = listed_notes(app, c, handle, tok)
    other = browser(app)
    signup(other, "copier@example.com")
    r = other.post("/copy", data={"from": str(e["id"]), "to": "new"})
    assert r.status_code == 303
    new = urlsplit(r.headers["location"]).path.split("/")[2]
    ws = {w["id"]: w for w in other.get("/api/v1/workspaces").json()["workspaces"]}
    assert ws[new]["name"] == "Notes starter" and len(ws) == 2
    n = app.state.conn.execute("SELECT COUNT(*) AS n FROM pages WHERE project=?",
                               (f"{ws_id(app, new)}:main",)).fetchone()["n"]
    assert n == 3
    # the new workspace is opened next
    assert r.cookies.get("dexio_ws") == new or new in r.headers.get("set-cookie", "")


def test_a_copy_that_does_not_fit_writes_nothing(app):
    c, tok, handle = owner(app)
    _d, e = listed_notes(app, c, handle, tok)
    other = browser(app)
    signup(other, "copier@example.com")
    mine = other.get("/api/v1/workspaces").json()["current"]
    conn = app.state.conn
    conn.execute("UPDATE workspaces SET storage_limit=10 WHERE id=?", (ws_id(app, mine),))
    conn.commit()
    r = other.post("/copy", data={"from": str(e["id"]), "to": mine})
    assert r.status_code == 400 and "storage left" in r.text
    n = conn.execute("SELECT COUNT(*) AS n FROM pages WHERE project=?",
                     (f"{ws_id(app, mine)}:main",)).fetchone()["n"]
    assert n == 0
    # copying into its own workspace is refused too
    r = c.post("/copy", data={"from": str(e["id"]), "to": handle})
    assert r.status_code == 400 and "already" in r.text


def test_a_cross_site_copy_is_refused(app):
    c, tok, handle = owner(app)
    _d, e = listed_notes(app, c, handle, tok)
    other = browser(app)
    signup(other, "copier@example.com")
    r = other.post("/copy", data={"from": str(e["id"]), "to": "new"},
                   headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert len(other.get("/api/v1/workspaces").json()["workspaces"]) == 1


def test_settings_sharing_says_what_is_listed(app):
    """Forrest, 2026-10-01: "how-we-build-dexio is public on dexio.wiki, but it's
    listed under anyone with the link". Published things get their own panel, as
    Visibility's levels in the Share dialog, and Unpublish keeps the link open."""
    c, tok, handle = owner(app)
    listed_notes(app, c, handle, tok)
    public(c, handle, "page", "index")                 # open with the link, not published
    ov = shares.overview(app.state.conn, ws_id(app, handle))
    assert (ov["published_pages"], ov["link_pages"], ov["publisher"]) == (3, 1, "owner-co")
    page = c.get(f"/settings/sharing?w={handle}").text
    panel = lambda pid: page.split(f'id="{pid}"', 1)[1].split('<div class="panel"', 1)[0]  # noqa: E731
    assert page.index('id="published"') < page.index('id="public"') < page.index('id="people"')
    pub, link = panel("published"), panel("public")
    assert "Notes starter" in pub and "Folder notes, 3 pages" in pub      # its dexio.wiki name
    assert "notes" not in link and "index" in link
    assert 'href="https://dexio.wiki/wikis/owner-co/"' in pub and ">Unpublish<" in pub
    assert "It leaves the public wikis on dexio.wiki" in pub and ">Make private<" in pub
    assert ">Unpublish<" not in link and "dexio.wiki" not in link
    assert f"3 of {ov['pages']} pages" in pub and f"1 of {ov['pages']} pages" in link
    sid = next(it["id"] for it in ov["public"] if it["listed"])
    r = c.post(f"/settings/sharing/{sid}/unpublish?w={handle}",
               headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    r = c.post(f"/settings/sharing/{sid}/unpublish?w={handle}")
    assert r.status_code == 303 and r.headers["location"].endswith("done=unpublished")
    assert entries(app) == []
    assert browser(app).get(f"/api/v1/note?w={handle}&path=notes/plan").status_code == 200
    page = c.get(r.headers["location"]).text
    assert "Unpublished." in page and "Nothing is published." in page
    assert "notes" in panel("public")
    assert c.post(f"/settings/sharing/{sid}/unpublish?w={handle}").status_code == 404
    # another workspace cannot unpublish it
    public(c, handle, "folder", "notes")
    listed(c, handle, "folder", "notes")
    other = browser(app)
    signup(other, "other-pub@example.com")
    mine = other.get("/api/v1/workspaces").json()["current"]
    assert other.post(f"/settings/sharing/{sid}/unpublish?w={mine}").status_code == 404
    assert len(entries(app)) == 1


def test_robots_keep_crawlers_off_copy(app):
    assert "Disallow: /copy" in browser(app).get("/robots.txt").text


def test_listing_for_prefers_the_widest(app):
    c, tok, handle = owner(app)
    public(c, handle, "page", "index")
    listed(c, handle, "page", "index")
    public(c, handle, "folder", "notes")
    listed(c, handle, "folder", "notes")
    got = shares.listing_for(app.state.conn, ws_id(app, handle))
    assert (got["kind"], got["path"]) == ("folder", "notes")


# ---- name, description, author and a preview (Forrest, 2026-10-01: "when they
# publish a wiki, we should allow them to give it a name and description", "show a
# visual preview of the wiki somehow", "we should also show the author") ----------
def test_a_listing_has_the_name_description_and_author_its_owner_gives_it(app):
    c, tok, handle = owner(app)
    mcp(app, tok, "write_page", path="notes/index", text="# Notes starter\n\nOwn words.\n")
    d = public(c, handle, "folder", "notes")
    form = d["public"]["listing"]
    assert form["title"] == "Notes starter" and form["description"] == "Own words."
    assert form["publisher"] == "owner-co" and form["can_name_publisher"] is True
    assert form["limits"] == {"title": 80, "description": 300, "publisher": 39}
    assert form["publisher_slug"] == "owner-co"
    r = c.post(f"/api/v1/share/listed?w={handle}", json={
        "kind": "folder", "path": "notes", "on": True, "title": "  Research   notes ",
        "description": "How we keep\nresearch notes.", "author": "Wrenfield Roasters"})
    assert r.status_code == 200, r.text
    assert r.json()["public"]["listing"]["title"] == "Research notes"
    (e,) = entries(app)
    # an author sent along is ignored: it is the workspace's publisher name (Forrest,
    # 2026-10-01: not typed in, then a unique workspace setting)
    assert (e["title"], e["description"], e["author"]) == (
        "Research notes", "How we keep research notes.", "owner-co")
    # saving again changes only what was sent, and keeps the first listing date
    first = e["listed_at"]
    listed(c, handle, "folder", "notes")
    (e,) = entries(app)
    assert e["title"] == "Research notes" and e["listed_at"] == first
    c.post(f"/api/v1/share/listed?w={handle}", json={"kind": "folder", "path": "notes",
                                                     "on": True, "description": ""})
    (e,) = entries(app)
    assert e["description"] == "" and e["author"] == "owner-co"
    # renaming the publisher shows on what is published
    r = c.post(f"/settings/publisher?w={handle}", data={"publisher": "AnnLeeNotes"})
    assert r.status_code == 303
    (e,) = entries(app)
    assert (e["author"], e["publisher"], e["publisher_slug"]) == ("AnnLeeNotes", "AnnLeeNotes",
                                                                  "annleenotes")
    # the copy page says who it is by
    other = browser(app)
    signup(other, "copier@example.com")
    assert "by AnnLeeNotes" in other.get(f"/copy?from={e['id']}").text


def test_listing_fields_are_checked(app):
    c, _tok, handle = owner(app)
    public(c, handle, "wiki", "")
    for body, words in (({"title": "   "}, "Give it a name"), ({"title": "x" * 81}, "80"),
                        ({"description": "y" * 301}, "300")):
        r = c.post(f"/api/v1/share/listed?w={handle}",
                   json={"kind": "wiki", "path": "", "on": True, **body})
        assert r.status_code == 400 and words in r.json()["error"], body
    assert entries(app) == []
    # with no author given, it is the lister's name; never their address
    listed(c, handle, "wiki", "")
    (e,) = entries(app)
    assert e["author"] == "owner-co"


def test_a_listing_shows_a_picture_of_its_graph(app):
    c, tok, handle = owner(app)
    _d, e = listed_notes(app, c, handle, tok)
    assert e["preview_url"].startswith(f"https://app.dexio.wiki/api/v1/directory/{e['id']}/"
                                       "preview.svg?v=")
    r = browser(app).get(e["preview_url"].replace("https://app.dexio.wiki", ""))
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert "max-age=86400" in r.headers["cache-control"]
    svg = r.text
    assert svg.startswith("<svg") and svg.count("<circle") == 3      # the folder's pages only
    assert svg.count("<line") == 1                                   # plan -> deep/more
    # a change to the wiki gives the picture a new address
    mcp(app, tok, "write_page", path="notes/extra", text="# Extra\n\n[[notes/plan]]\n")
    (e2,) = entries(app)
    assert e2["preview_url"] != e["preview_url"]
    assert browser(app).get(e2["preview_url"].replace("https://app.dexio.wiki", "")
                            ).text.count("<circle") == 4
    # nothing unlisted has a picture
    listed(c, handle, "folder", "notes", on=False)
    assert browser(app).get(f"/api/v1/directory/{e['id']}/preview.svg").status_code == 404


# ---- Publish: its own button and dialog (Forrest, 2026-10-01: "should we have a
# second button for publish, rather than lump it into share?", "yes") -------------
def publish(c, handle, kind, path, status=200, **body):
    r = c.post(f"/api/v1/publish?w={handle}", json={"kind": kind, "path": path, **body})
    assert r.status_code == status, r.text
    return r.json()


def test_publish_makes_it_public_and_lists_it_in_one_step(app):
    c, tok, handle = owner(app)
    mcp(app, tok, "write_page", path="notes/index", text="# Notes starter\n\nOwn words.\n")
    s = c.get(f"/api/v1/publish?w={handle}&kind=folder&path=notes").json()
    assert s["published"] is False and s["public"] is False and s["pages"] == 3
    assert s["listing"]["title"] == "Notes starter" and s["listing"]["publisher"] == "owner-co"
    assert s["version"].startswith("3.")
    s = publish(c, handle, "folder", "notes", on=True, title="Research notes",
                description="How we keep notes.", author="Wrenfield")
    assert s["published"] and s["public"] and s["own_public"] and s["share_id"]
    (e,) = entries(app)
    assert (e["title"], e["author"], e["description"]) == ("Research notes", "owner-co",
                                                           "How we keep notes.")
    anon = browser(app)
    assert anon.get(f"/api/v1/note?w={handle}&path=notes/plan").status_code == 200
    # the Share dialog knows
    d = c.get(f"/api/v1/share?w={handle}&kind=folder&path=notes").json()
    assert d["public"]["on"] and d["public"]["listed"]
    # unpublish: off the directory, still public
    s = publish(c, handle, "folder", "notes", on=False)
    assert s["published"] is False and s["public"] is True
    assert entries(app) == []
    assert anon.get(f"/api/v1/note?w={handle}&path=notes/plan").status_code == 200


def test_publish_takes_a_wiki_or_a_folder_and_a_bad_name_changes_nothing(app):
    c, _tok, handle = owner(app)
    r = c.get(f"/api/v1/publish?w={handle}&kind=page&path=index")
    assert r.status_code == 400 and "whole wiki or a folder" in r.json()["error"]
    publish(c, handle, "page", "index", status=400, on=True, title="x", author="y")
    err = publish(c, handle, "wiki", "", status=400, on=True, title=" ", author="Me")
    assert "Give it a name" in err["error"]
    assert c.get(f"/api/v1/share?w={handle}&kind=wiki&path=").json()["public"]["on"] is False
    assert entries(app) == []
    # a public folder's wiki can be published as well: the whole wiki gets its own row
    public(c, handle, "folder", "notes")
    s = publish(c, handle, "wiki", "", on=True, title="All of it", author="Me")
    assert s["published"] and s["own_public"]
    assert [x["title"] for x in entries(app)] == ["All of it"]


def test_the_publish_picture_is_for_members(app):
    c, _tok, handle = owner(app)
    r = c.get(f"/api/v1/publish/preview.svg?w={handle}&kind=folder&path=notes&v=1")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")
    assert r.text.count("<circle") == 2 and "private" in r.headers["cache-control"]
    other = browser(app)
    signup(other, "stranger@example.com")
    assert other.get(f"/api/v1/publish/preview.svg?w={handle}&kind=wiki").status_code == 403
    assert browser(app).get(f"/api/v1/publish?w={handle}&kind=wiki").status_code == 401


def test_publishing_is_a_level_of_visibility(app):
    """Forrest, 2026-10-01: back into Share, as "Restricted, Anyone with the link,
    Published on dexio.wiki"; no Publish button of its own."""
    c, _tok, handle = owner(app)
    page = c.get(f"/w/{handle}", headers=HTML).text
    assert 'id="publish-wiki"' not in page and "dexioPublish" not in page
    assert "Anyone with the link" in page and "Published on dexio.wiki" in page
    # people only under Restricted (Forrest, 2026-10-01)
    assert 'sd-done' not in page and 'class="sd-whosec"' in page
    # Visibility applies as it is picked, Published waits for its own Publish, and
    # the dialog has a close button (Forrest, 2026-10-01: "the dropdown should
    # automatically apply the update. Except publication should require a second
    # button press. This modal is also missing a close button")
    assert "sd-update" not in page and 'class="sd-foot"' not in page
    assert '<button class="sd-primary sd-pub" type="button">Publish</button>' in page
    assert 'class="sd-close" aria-label="Close"' in page
    assert page.index('class="sd-close"') < page.index('>Visibility</h3>')
    # no "Members of ... can always edit it" line in its place (Forrest, 2026-10-01)
    assert "sd-members" not in page and "can always edit it" not in page
    # Visibility, not General access, and above the people (Forrest, 2026-10-01)
    assert "General access" not in page
    assert page.index('>Visibility</h3>') < page.index('class="sd-whosec"')
    # the link under Visibility at every level, Copy at its end (Forrest,
    # 2026-10-01: "B", then "we should show the link regardless of which
    # visibility is set, right?")
    assert page.index('>Visibility</h3>') < page.index('class="sd-link"') < page.index('class="sd-whosec"')
    assert ">Copy link</button>" not in page
    d = c.get(f"/api/v1/share?w={handle}&kind=folder&path=notes").json()
    assert d["public"]["publishable"] is True and d["public"]["listed"] is False
    assert c.get(f"/api/v1/share?w={handle}&kind=page&path=notes/plan"
                 ).json()["public"]["publishable"] is False
    publish(c, handle, "folder", "notes", on=True, title="Notes")
    d = c.get(f"/api/v1/share?w={handle}&kind=page&path=notes/plan").json()
    assert d["public"]["on"] and d["public"]["via"]["listed"] is True      # published via the folder


def test_publishing_needs_a_unique_publisher_name(app):
    c, _tok, handle = owner(app, publisher=None)
    s0 = c.get(f"/api/v1/publish?w={handle}&kind=wiki").json()
    assert s0["listing"]["publisher"] == "" and s0["listing"]["can_name_publisher"] is True
    err = publish(c, handle, "wiki", "", status=400, on=True, title="All of it")
    assert "publisher name" in err["error"]
    assert c.get(f"/api/v1/share?w={handle}&kind=wiki&path=").json()["public"]["on"] is False
    # the first time, the Publish dialog names it; a space is refused, not mended
    err = publish(c, handle, "wiki", "", status=400, on=True, title="All of it",
                  publisher="Ann Lee")
    assert "No spaces" in err["error"]
    s1 = publish(c, handle, "wiki", "", on=True, title="All of it", publisher="  Ann-Lee ")
    assert s1["published"] and s1["listing"]["publisher"] == "Ann-Lee"
    assert s1["listing"]["publisher_slug"] == "ann-lee"
    (e,) = entries(app)
    assert (e["author"], e["publisher_slug"]) == ("Ann-Lee", "ann-lee")
    page = c.get(f"/settings?w={handle}").text
    assert 'id="publisher-name"' in page and 'value="Ann-Lee"' in page
    assert "dexio.wiki/wikis/ann-lee/" in page
    # no other workspace can have it, in any case, with or without hyphens
    c2, _t2, h2 = owner(app, "second@example.com", publisher=None)
    for name in ("annlee", "ANN-LEE", "a-n-n-l-e-e"):
        r = c2.post(f"/settings/publisher?w={h2}", data={"publisher": name})
        assert r.status_code == 400 and "Another workspace publishes as" in r.text, name
    err = publish(c2, h2, "wiki", "", status=400, on=True, title="Mine", publisher="AnnLee")
    assert "Another workspace" in err["error"]
    assert c2.get(f"/api/v1/share?w={h2}&kind=wiki&path=").json()["public"]["on"] is False
    # names that pass for Dexio, or for a page of the site, are kept
    for name in ("Dexio", "dexio-team", "Support", "wikis", "DexioHQ"):
        r = c2.post(f"/settings/publisher?w={h2}", data={"publisher": name})
        assert r.status_code == 400 and "kept for Dexio" in r.text, name
    for name in ("x", "y" * 40, "-dash", "trailing-", "a--b", "a<b>", "under_score", "dot.name",
                 "two words"):
        r = c2.post(f"/settings/publisher?w={h2}", data={"publisher": name})
        assert r.status_code == 400, name
    assert c2.post(f"/settings/publisher?w={h2}", data={"publisher": "second-co"}).status_code == 303
    # renaming frees the old name
    c.post(f"/settings/publisher?w={handle}", data={"publisher": "ann-lee-2"})
    assert c2.post(f"/settings/publisher?w={h2}", data={"publisher": "AnnLee"}).status_code == 303


def test_names_from_the_first_rule_become_handles(app):
    from dexio.server import db
    c, _tok, handle = owner(app, publisher=None)
    c2, _t2, h2 = owner(app, "second@example.com", publisher=None)
    conn = app.state.conn
    conn.execute("UPDATE workspaces SET publisher_name='Old Name & Co.', publisher_key='old name & co.'"
                 " WHERE handle=?", (handle,))
    conn.execute("UPDATE workspaces SET publisher_name='old  name co', publisher_key='old  name co'"
                 " WHERE handle=?", (h2,))
    conn.commit()
    db.upgrade_publishers(conn)
    assert shares.publisher_of(conn, ws_id(app, handle)) == "Old-Name-Co"
    assert shares.publisher_of(conn, ws_id(app, h2)) == ""       # the same name: chosen again
    db.upgrade_publishers(conn)                                   # and again changes nothing
    assert shares.publisher_of(conn, ws_id(app, handle)) == "Old-Name-Co"


def test_dexio_may_use_its_own_name(app, monkeypatch):
    c, _tok, handle = owner(app, publisher=None)
    monkeypatch.setenv("DEXIO_OFFICIAL_WORKSPACES", handle)
    assert c.post(f"/settings/publisher?w={handle}", data={"publisher": "Dexio"}).status_code == 303
    assert shares.publisher_of(app.state.conn, ws_id(app, handle)) == "Dexio"
