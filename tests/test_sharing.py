"""Sharing a wiki, a folder or a page by email or with anyone who has the link
(Forrest, 2026-09-28: "it should be like gdocs where you can share by email, or
you can make it publically viewable"). See server/shares.py."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import db, mail, shares  # noqa: E402

from test_workspaces import app, browser, mcp, signup, token_from_connect, ws_id  # noqa: E402,F401

PAGES = {
    "index": "# Index\n\nSee [[notes/plan]] and [[secret/deal]].\n",
    "notes/plan": "# Plan\n\nThe plan. ![chart](chart.png) and [[notes/deep/more]].\n",
    "notes/deep/more": "# More\n\nMore notes.\n",
    "secret/deal": "# Deal\n\nConfidential terms.\n",
}


def owner_with_wiki(app, email="owner@example.com"):
    """An account whose wiki has PAGES and two files: notes/chart.png (shown on
    notes/plan) and raw/private.pdf (shown nowhere shared)."""
    c = browser(app)
    assert signup(c, email).status_code == 303
    tok = token_from_connect(c)
    for path, text in PAGES.items():
        assert mcp(app, tok, "write_page", path=path, text=text).get("ok"), path
    for path in ("notes/chart.png", "raw/private.pdf"):
        r = c.put(f"/api/v1/files?path={path}", content=b"bytes of " + path.encode())
        assert r.status_code == 200, r.text
    handle = c.get("/api/v1/workspaces").json()["current"]
    return c, tok, handle


def share(c, handle, kind, path, email, role="viewer", status=200):
    r = c.post(f"/api/v1/share?w={handle}", json={"kind": kind, "path": path, "email": email,
                                                   "role": role})
    assert r.status_code == status, r.text
    return r.json()


def public(c, handle, kind, path, on=True):
    r = c.post(f"/api/v1/share/public?w={handle}", json={"kind": kind, "path": path, "on": on})
    assert r.status_code == 200, r.text
    return r.json()


@pytest.fixture()
def sent(monkeypatch):
    """Share emails, captured instead of sent: (to, link) pairs."""
    got: list[tuple[str, str]] = []

    def fake(to, sharer, what, title, workspace, link, sharer_email=""):
        got.append((to, link))
        return True
    monkeypatch.setattr(mail, "share", fake)
    return got


def code_of(link: str) -> str:
    return re.search(r"/s/(dxs_[A-Za-z0-9_\-]+)$", link).group(1)


def test_nothing_is_shared_until_someone_shares_it(app):
    c, _tok, handle = owner_with_wiki(app)
    anon = browser(app)
    r = anon.get(f"/w/{handle}/index", headers={"accept": "text/html"})
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert anon.get(f"/api/v1/graph?w={handle}").status_code == 401
    other = browser(app)
    signup(other, "stranger@example.com")
    assert other.get(f"/api/v1/graph?w={handle}").status_code == 404
    assert other.get(f"/api/v1/note?w={handle}&path=index").status_code == 404
    r = other.get(f"/w/{handle}", headers={"accept": "text/html"})
    assert r.status_code == 404 and "You need access" in r.text
    # a workspace is never found by its id, only by its handle
    wid = ws_id(app, handle)
    public(c, handle, "wiki", "")
    assert anon.get(f"/api/v1/graph?w={wid}").status_code == 401


def test_a_public_page_opens_for_anyone_and_shows_nothing_else(app):
    c, _tok, handle = owner_with_wiki(app)
    d = public(c, handle, "page", "notes/plan")
    assert d["public"]["on"] and d["public"]["own"] and d["link"].endswith(f"/w/{handle}/notes/plan")
    anon = browser(app)
    page = anon.get(f"/w/{handle}/notes/plan", headers={"accept": "text/html"})
    assert page.status_code == 200
    # public pages are for search engines too (Forrest: "public wiki should bring
    # in search traffic"): no noindex, a title, a description and a canonical address
    assert "x-robots-tag" not in page.headers
    assert "<title>Plan · owner&#x27;s Workspace</title>" in page.text
    assert '<meta name="description" content="The plan.' in page.text
    assert f'<link rel="canonical" href="https://app.dexio.wiki/w/{handle}/notes/plan">' in page.text
    assert '"role": "public"' in page.text and "View only" in page.text
    assert "Try Dexio free" in page.text and 'id="share-wiki"' not in page.text
    assert "window.dexioShare = open" not in page.text          # share.js is for members
    g = anon.get(f"/api/v1/graph?w={handle}").json()
    assert [n["id"] for n in g["nodes"]] == ["notes/plan"]
    assert g["links"] == [] and g["stats"]["pages"] == 1
    # the file the page shows comes along; the one it does not stays out
    assert [f["path"] for f in g["files"]] == ["notes/chart.png"]
    assert anon.get(f"/api/v1/files?w={handle}&path=notes/chart.png").status_code == 302
    assert anon.get(f"/api/v1/files?w={handle}&path=raw/private.pdf").status_code == 404
    n = anon.get(f"/api/v1/note?w={handle}&path=notes/plan").json()
    assert n["text"].startswith("# Plan")
    assert set(n["info"]) == {"exists", "updated_at", "created"}  # when, not who
    assert "who" not in str(n["info"]) and "owner@" not in str(n)
    for path in ("index", "secret/deal", "notes/deep/more"):
        assert anon.get(f"/api/v1/note?w={handle}&path={path}").status_code == 404
    assert anon.get(f"/api/v1/page-history?w={handle}&path=notes/plan").status_code == 403
    assert anon.get(f"/api/v1/history?w={handle}").status_code == 403
    hits = anon.get(f"/api/v1/search?w={handle}&q=plan").json()["results"]
    assert [h["path"] for h in hits] == ["notes/plan"]
    assert anon.get(f"/api/v1/search?w={handle}&q=confidential").json()["results"] == []
    # another page's address, signed out, asks to sign in (it might be a member)
    r = anon.get(f"/w/{handle}/secret/deal", headers={"accept": "text/html"})
    assert r.status_code == 303 and "/login" in r.headers["location"]
    # nothing to share or export from outside
    assert anon.get(f"/api/v1/share?w={handle}&kind=wiki").status_code == 401
    assert anon.get(f"/api/v1/export?w={handle}").status_code == 401


def test_a_public_folder_covers_its_subfolders_and_later_pages(app):
    c, tok, handle = owner_with_wiki(app)
    public(c, handle, "folder", "notes")
    anon = browser(app)
    ids = {n["id"] for n in anon.get(f"/api/v1/graph?w={handle}").json()["nodes"]}
    assert ids == {"notes/plan", "notes/deep/more"}
    mcp(app, tok, "write_page", path="notes/new", text="# New\n")
    ids = {n["id"] for n in anon.get(f"/api/v1/graph?w={handle}").json()["nodes"]}
    assert ids == {"notes/plan", "notes/deep/more", "notes/new"}
    g = anon.get(f"/api/v1/graph?w={handle}").json()
    assert {"source": "notes/plan", "target": "notes/deep/more"} in g["links"]
    # a page opened inside the folder shows the folder's public state as inherited
    d = c.get(f"/api/v1/share?w={handle}&kind=page&path=notes/plan").json()
    assert d["public"]["on"] and not d["public"]["own"] and d["public"]["via"]["path"] == "notes"
    # closing the folder closes everything under it
    public(c, handle, "folder", "notes", on=False)
    assert anon.get(f"/api/v1/graph?w={handle}").status_code == 401


def test_sharing_by_email_binds_to_the_account_that_opens_the_link(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    d = share(c, handle, "folder", "notes", "Bea@Example.com")
    assert [(p["email"], p["pending"]) for p in d["people"]] == [("bea@example.com", True)]
    (to, link), = sent
    assert to == "bea@example.com" and link.startswith("https://testserver/s/dxs_")
    code = code_of(link)
    # Bea has no account yet: the link sends her to sign in or up, then back
    bea = browser(app)
    r = bea.get(f"/s/{code}")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/s/" + code
    assert "Someone shared something with you" in bea.get(r.headers["location"]).text
    signup(bea, "bea@example.com")
    r = bea.get(f"/s/{code}")
    assert r.status_code == 303 and r.headers["location"] == f"/w/{handle}?folder=notes"
    ids = {n["id"] for n in bea.get(f"/api/v1/graph?w={handle}").json()["nodes"]}
    assert ids == {"notes/plan", "notes/deep/more"}
    page = bea.get(f"/w/{handle}/notes/plan", headers={"accept": "text/html"})
    assert page.status_code == 200 and '"role": "guest"' in page.text
    # shared by email only: kept out of search engines
    assert page.headers["x-robots-tag"] == "noindex, nofollow"
    assert 'rel="canonical"' not in page.text
    # her own wiki is still hers: the shared one shows under Shared with you
    home = bea.get("/", headers={"accept": "text/html"})
    assert "Shared with you" in home.text and f'href="/w/{handle}"' in home.text
    d = c.get(f"/api/v1/share?w={handle}&kind=folder&path=notes").json()
    assert d["people"][0]["pending"] is False
    # the link works once for Bea, and never for anyone else
    assert bea.get(f"/s/{code}").status_code == 303
    eve = browser(app)
    signup(eve, "eve@example.com")
    r = eve.get(f"/s/{code}")
    assert r.status_code == 403 and "sent to b•••@example.com" in r.text
    assert eve.get(f"/api/v1/graph?w={handle}").status_code == 404


def test_only_the_address_it_was_sent_to_can_claim_the_link(app, sent):
    """Forrest, 2026-09-28: "the link should not be claim-able by anyone other
    than the user it was intended for". He opened his own share email in the
    sharing account's browser first, and that account took the share."""
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "folder", "notes", "kayla@example.com")
    code = code_of(sent[-1][1])
    # the sharer opens it: turned away, told whose it is (masked), link unused
    r = c.get(f"/s/{code}")
    assert r.status_code == 403
    assert "sent to ka•••@example.com" in r.text and "kayla@" not in r.text
    assert "signed in as owner@example.com" in r.text
    assert f'href="/logout?next=%2Fs%2F{code}"' in r.text
    d = c.get(f"/api/v1/share?w={handle}&kind=folder&path=notes").json()
    assert d["people"][0]["pending"] is True
    # so does a stranger it was forwarded to, who gets nothing
    eve = browser(app)
    signup(eve, "eve@example.com")
    assert eve.get(f"/s/{code}").status_code == 403
    assert eve.get(f"/api/v1/graph?w={handle}").status_code == 404
    # switching accounts signs out and comes back to the link after sign-in
    r = eve.get(f"/logout?next=%2Fs%2F{code}")
    assert r.status_code == 303 and r.headers["location"] == f"/login?next=/s/{code}"
    assert eve.get("/logout").headers["location"] == "/login"
    assert eve.get("/logout?next=https://evil.example").headers["location"] == "/login"
    # the account for the address still can
    kayla = browser(app)
    signup(kayla, "Kayla@Example.com")
    r = kayla.get(f"/s/{code}")
    assert r.status_code == 303 and r.headers["location"] == f"/w/{handle}?folder=notes"
    assert kayla.get(f"/api/v1/graph?w={handle}").status_code == 200


def test_an_address_google_verified_can_claim_the_link(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "page", "index", "mo@gmail.example")
    mo = browser(app)
    signup(mo, "mo@work.example")
    uid = app.state.conn.execute("SELECT id FROM users WHERE email='mo@work.example'").fetchone()[0]
    with app.state.conn:
        app.state.conn.execute("INSERT INTO identities (provider, subject, user_id, email,"
                               " created_at) VALUES ('google', 'g-2', ?, 'mo@gmail.example', 0)",
                               (uid,))
    assert mo.get(f"/s/{code_of(sent[-1][1])}").status_code == 303
    row = app.state.conn.execute("SELECT user_id FROM shares WHERE email='mo@gmail.example'")
    assert row.fetchone()[0] == uid


def test_a_link_the_wrong_account_took_before_goes_to_the_right_one(app, sent):
    """Shares claimed before the rule above by an account the address is not
    theirs, as Forrest's was, move to the right account when it opens the link."""
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "folder", "notes", "lee@example.com")
    code = code_of(sent[-1][1])
    owner = app.state.conn.execute("SELECT id FROM users WHERE email='owner@example.com'")
    with app.state.conn:
        app.state.conn.execute("UPDATE shares SET user_id=?, accepted_at=0",
                               (owner.fetchone()[0],))
    lee = browser(app)
    signup(lee, "lee@example.com")
    assert lee.get(f"/s/{code}").status_code == 303
    assert {n["id"] for n in lee.get(f"/api/v1/graph?w={handle}").json()["nodes"]} == \
        {"notes/plan", "notes/deep/more"}
    # and once it has, nobody else with the address can take it back
    other = browser(app)
    signup(other, "lee2@example.com")
    uid = app.state.conn.execute("SELECT id FROM users WHERE email='lee2@example.com'")
    with app.state.conn:
        app.state.conn.execute("INSERT INTO identities (provider, subject, user_id, email,"
                               " created_at) VALUES ('github', 'gh-1', ?, 'lee@example.com', 0)",
                               (uid.fetchone()[0],))
    r = other.get(f"/s/{code}")
    assert r.status_code == 400 and "already used by another account" in r.text


def test_masked_addresses():
    assert shares.masked("fozzie.bear@gmail.com") == "fo•••@gmail.com"
    assert shares.masked("bea@example.com") == "b•••@example.com"
    assert shares.masked("a@b.c") == "a•••@b.c"


def test_a_password_account_with_the_address_still_needs_the_link(app, sent):
    """Sign-up never checks an address, so owning the inbox is what counts."""
    c, _tok, handle = owner_with_wiki(app)
    squat = browser(app)
    signup(squat, "cal@example.com")
    share(c, handle, "page", "index", "cal@example.com")
    assert squat.get(f"/api/v1/graph?w={handle}").status_code == 404


def test_an_address_google_verified_sees_its_shares_without_the_link(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "page", "index", "dee@example.com")
    dee = browser(app)
    signup(dee, "dee@example.com")
    uid = app.state.conn.execute("SELECT id FROM users WHERE email='dee@example.com'").fetchone()[0]
    with app.state.conn:
        app.state.conn.execute("INSERT INTO identities (provider, subject, user_id, email,"
                               " created_at) VALUES ('google', 'g-1', ?, 'dee@example.com', 0)",
                               (uid,))
    assert [n["id"] for n in dee.get(f"/api/v1/graph?w={handle}").json()["nodes"]] == ["index"]


def test_removing_a_share_takes_access_away(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    d = share(c, handle, "wiki", "", "fay@example.com")
    fay = browser(app)
    signup(fay, "fay@example.com")
    fay.get(f"/s/{code_of(sent[-1][1])}")
    assert len(fay.get(f"/api/v1/graph?w={handle}").json()["nodes"]) == len(PAGES)
    # the whole wiki shows every file too
    assert fay.get(f"/api/v1/files?w={handle}&path=raw/private.pdf").status_code == 302
    # a folder's dialog lists her, from the whole wiki
    f = c.get(f"/api/v1/share?w={handle}&kind=folder&path=notes").json()
    assert f["people"][0]["via"]["kind"] == "wiki"
    r = c.delete(f"/api/v1/share/{d['people'][0]['id']}?w={handle}&kind=wiki&path=")
    assert r.status_code == 200 and r.json()["people"] == []
    assert fay.get(f"/api/v1/graph?w={handle}").status_code == 404


def test_sharing_again_sends_a_fresh_link_and_retires_the_old_one(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "page", "index", "gus@example.com")
    share(c, handle, "page", "index", "gus@example.com")
    old, new = code_of(sent[0][1]), code_of(sent[1][1])
    assert old != new
    gus = browser(app)
    signup(gus, "gus@example.com")
    assert gus.get(f"/s/{old}").status_code == 400
    assert gus.get(f"/s/{new}").status_code == 303


def test_only_members_share_and_only_owners_add_editors(app, sent, monkeypatch, plans):
    c, _tok, handle = owner_with_wiki(app)
    # a guest cannot share onward
    share(c, handle, "page", "index", "hal@example.com")
    hal = browser(app)
    signup(hal, "hal@example.com")
    hal.get(f"/s/{code_of(sent[-1][1])}")
    r = hal.post(f"/api/v1/share?w={handle}", json={"kind": "page", "path": "index",
                                                     "email": "x@example.com"})
    assert r.status_code == 403
    assert hal.post(f"/api/v1/share/public?w={handle}", json={"kind": "page", "path": "index",
                                                               "on": True}).status_code == 403
    # a member is told they need not share with themselves
    me = share(c, handle, "page", "index", "owner@example.com", status=400)
    assert "already see" in me["error"]
    # editing is membership: the whole wiki only, and Free is for one person
    share(c, handle, "page", "index", "ivy@example.com", role="editor", status=403)
    d = c.get(f"/api/v1/share?w={handle}&kind=wiki").json()
    assert d["editors"] == {"allowed": True, "room": False, "plan": "free"}
    share(c, handle, "wiki", "", "ivy@example.com", role="editor", status=400)
    with app.state.conn:
        app.state.conn.execute("UPDATE workspaces SET plan='business' WHERE id=?",
                               (ws_id(app, handle),))
    invites: list[str] = []
    monkeypatch.setattr(mail, "invite", lambda to, *a, **k: invites.append(to) or True)
    d = share(c, handle, "wiki", "", "ivy@example.com", role="editor")
    assert invites == ["ivy@example.com"] and [i["email"] for i in d["invites"]] == ["ivy@example.com"]
    # things that do not exist cannot be shared
    share(c, handle, "page", "nope", "x@example.com", status=400)
    share(c, handle, "folder", "nope", "x@example.com", status=400)
    assert c.get(f"/api/v1/share?w={handle}&kind=page&path=nope").status_code == 404


def test_cross_site_share_requests_are_refused(app):
    c, _tok, handle = owner_with_wiki(app)
    r = c.post(f"/api/v1/share/public?w={handle}", json={"kind": "wiki", "on": True},
               headers={"origin": "https://evil.example"})
    assert r.status_code == 403
    assert db.workspace_by_handle(app.state.conn, handle)
    assert not app.state.conn.execute("SELECT 1 FROM shares").fetchone()


def test_a_moved_page_keeps_its_shares(app):
    c, tok, handle = owner_with_wiki(app)
    public(c, handle, "page", "index")
    assert mcp(app, tok, "move_page", path="index", new_path="home").get("ok")
    anon = browser(app)
    assert [n["id"] for n in anon.get(f"/api/v1/graph?w={handle}").json()["nodes"]] == ["home"]
    public(c, handle, "page", "notes/deep/more")
    r = mcp(app, tok, "change_pages", changes=[
        {"op": "move", "path": "notes/deep/more", "new_path": "notes/later"}])
    assert r.get("ok"), r
    ids = {n["id"] for n in anon.get(f"/api/v1/graph?w={handle}").json()["nodes"]}
    assert ids == {"home", "notes/later"}


def test_members_see_share_buttons_and_the_whole_wiki_as_before(app):
    c, _tok, handle = owner_with_wiki(app)
    page = c.get(f"/w/{handle}", headers={"accept": "text/html"})
    assert 'id="share-wiki"' in page.text and "window.dexioShare = open" in page.text
    assert "window.DEXIO_SHARE = true" in page.text and "window.DEXIO_GUEST = null" in page.text
    # every request names its workspace, and the old cookie-only form still works
    assert len(c.get(f"/api/v1/graph?w={handle}").json()["nodes"]) == len(PAGES)
    assert len(c.get("/api/v1/graph").json()["nodes"]) == len(PAGES)
    assert c.get(f"/api/v1/page-history?w={handle}&path=index").status_code == 200


def test_deleting_the_workspace_or_the_account_removes_its_shares(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "page", "index", "jo@example.com")
    jo = browser(app)
    signup(jo, "jo@example.com")
    jo.get(f"/s/{code_of(sent[-1][1])}")
    from dexio.server import erase
    uid = app.state.conn.execute("SELECT id FROM users WHERE email='jo@example.com'").fetchone()[0]
    erase.delete_account(app.state.conn, uid)
    assert not app.state.conn.execute("SELECT 1 FROM shares WHERE user_id=?", (uid,)).fetchone()
    public(c, handle, "wiki", "")
    wid = ws_id(app, handle)
    with app.state.conn:
        erase._drop_workspace(app.state.conn, wid)
    assert not app.state.conn.execute("SELECT 1 FROM shares WHERE workspace_id=?",
                                      (wid,)).fetchone()


def test_public_pages_are_in_the_sitemap_and_robots_points_to_it(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    anon = browser(app)
    robots = anon.get("/robots.txt")
    assert robots.status_code == 200
    assert "Sitemap: https://app.dexio.wiki/sitemap.xml" in robots.text
    assert "Disallow: /mcp" in robots.text and "Disallow: /s/" in robots.text
    assert "/api/v1/note" not in robots.text      # the page's text must stay reachable
    empty = anon.get("/sitemap.xml")
    assert empty.status_code == 200 and "<url>" not in empty.text
    public(c, handle, "folder", "notes")
    share(c, handle, "page", "secret/deal", "kim@example.com")   # by email: not listed
    locs = re.findall(r"<loc>([^<]+)</loc>", anon.get("/sitemap.xml").text)
    base = f"https://app.dexio.wiki/w/{handle}"
    assert locs == [base, f"{base}/notes/deep/more", f"{base}/notes/plan"]
    public(c, handle, "folder", "notes", on=False)
    assert "<url>" not in anon.get("/sitemap.xml").text
    # the workspace's own address is indexable while something in it is public
    public(c, handle, "page", "index")
    home = anon.get(f"/w/{handle}", headers={"accept": "text/html"})
    assert home.status_code == 200 and "x-robots-tag" not in home.headers
    assert f'<link rel="canonical" href="{base}">' in home.text


def test_access_rules():
    a = shares.Access(1, "public", folders=frozenset({"notes"}), pages=frozenset({"index"}))
    assert a.sees("index") and a.sees("notes/plan") and a.sees("notes/deep/more")
    assert not a.sees("notesx/plan") and not a.sees("notes") and not a.sees("secret/deal")
    assert shares.covers("folder", "notes", "folder", "notes/deep")
    assert shares.covers("folder", "notes", "page", "notes/plan")
    assert not shares.covers("folder", "notes", "wiki", "")
    assert not shares.covers("page", "index", "page", "index2")
    assert shares._file_targets("![a](chart.png) [[deck.pdf|Deck]] `[[x.png]]` [[page]]") == \
        ["deck.pdf", "chart.png"]
    paths = {"notes/chart.png", "raw/a.png", "other/a.png"}
    assert shares._resolve_file("chart.png", "notes/plan", paths) == "notes/chart.png"
    assert shares._resolve_file("../raw/a.png", "notes/plan", paths) == "raw/a.png"
    assert shares._resolve_file("a.png", "index", paths) is None          # two a.png


def test_a_folder_or_page_share_says_the_recipient_is_a_viewer():
    """Forrest, 2026-09-28: "when sharing a folder, it should be more apparent that the
    recipient of the share only has viewer permissions". Where Editor is not on offer the
    dialog shows a fixed Viewer in the picker's place and says what a viewer can do."""
    from pathlib import Path

    import dexio
    js = (Path(dexio.__file__).parent / "static" / "share.js").read_text(encoding="utf-8")
    assert 'id="sd-role-fixed"' in js
    assert "People you add here are viewers" in js and "can't change anything" in js
    assert "as a viewer." in js

