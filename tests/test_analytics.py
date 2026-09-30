"""Google Analytics in the app (Forrest, 2026-09-28): only on the sign-in and sign-up
pages and the one-time /joined hop that records a new account, behind the consent
check shared with dexio.wiki (static/consent.js). Every page that shows wiki content
loads nothing from Google."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import pages  # noqa: E402

from test_workspaces import app, browser, invite_code, signup  # noqa: E402,F401

GA = "googletagmanager.com/gtag/js"
ID = "G-N3CK5LV6BK"


def test_sign_in_and_sign_up_pages_carry_the_tag_the_banner_and_cookie_settings(app):
    c = browser(app)
    for url in ("/login", "/signup", "/login?next=/w/abc/secret-page", "/signup?next=/s/xyz"):
        page = c.get(url).text
        assert GA in page and ID in page, url
        assert 'id="consent"' in page and 'data-consent="denied"' in page, url
        assert "data-consent-open>Cookie settings</a>" in page, url
    # The email step (a POST response) is a sign-in page too.
    page = c.post("/login/email", data={"email": "new@example.com", "mode": "signup"}).text
    assert GA in page and 'id="consent"' in page


def test_google_sees_only_the_path_and_campaign_tags():
    js = pages.CONSENT_JS
    assert "page_location: location_()" in js and "page_referrer: referrer()" in js
    assert "location.origin + location.pathname" in js
    assert "utm_[a-z]+|gclid|gbraid|wbraid" in js
    # a same-site referrer (which could name a wiki page) is cut to the origin
    assert 'r.origin + "/"' in js


def test_pages_with_wiki_content_load_nothing_from_google(app):
    c = browser(app)
    signup(c, "ann@example.com")
    for url in ("/", "/settings", "/settings/members", "/settings/agents", "/forgot",
                "/device"):
        r = c.get(url)
        assert GA not in r.text and "dexio_consent" not in r.text, url
    r = c.get("/", follow_redirects=True)
    assert r.status_code == 200 and GA not in r.text


def test_a_new_account_goes_through_joined_once(app):
    c = browser(app)
    r = signup(c, "ann@example.com")
    assert r.headers["location"] == "/joined?next=%2F"
    page = c.get("/joined?next=%2F")
    assert page.status_code == 200
    assert GA in page.text and 'gtag("event", "sign_up", { method: "password"' in page.text
    assert 'location.replace("/")' in page.text
    assert 'id="consent"' not in page.text          # no banner on a page that moves on at once
    # Only once: reloading, or a bookmark, goes straight on.
    again = c.get("/joined?next=%2F")
    assert again.status_code == 303 and again.headers["location"] == "/"


def test_the_hop_keeps_the_destination_and_refuses_other_sites(app):
    c = browser(app)
    r = signup(c, "ann@example.com", next_="/device?code=ABCD-EFGH")
    assert r.headers["location"] == "/joined?next=%2Fdevice%3Fcode%3DABCD-EFGH"
    assert 'location.replace("/device?code=ABCD-EFGH")' in c.get(r.headers["location"]).text
    for i, bad in enumerate(("//evil.example", "/\\evil.example", "https://evil.example")):
        c2 = browser(app)
        signup(c2, f"x{i}@example.com")
        page = c2.get("/joined", params={"next": bad}).text
        assert 'location.replace("/")' in page and "evil" not in page, bad


def test_a_declined_visitor_skips_the_hop(app):
    c = browser(app)
    c.cookies.set("dexio_consent", "denied")
    r = signup(c, "ann@example.com")
    assert r.headers["location"] == "/"
    assert "dexio_joined" not in r.headers.get("set-cookie", "")


def test_joining_a_team_by_invite_is_not_a_sign_up_event(app, monkeypatch):
    owner = browser(app)
    signup(owner, "owner@example.com")
    with app.state.conn as conn:
        conn.execute("UPDATE workspaces SET plan='team'")
    code = invite_code(owner, monkeypatch, "guest@example.com")
    guest = browser(app)
    r = signup(guest, "guest@example.com", next_=f"/invite/{code}")
    assert r.headers["location"] == f"/invite/{code}"


def test_the_engine_copy_of_consent_js_matches_the_site_contract():
    js = pages.CONSENT_JS
    assert f'var ID = "{ID}"' in js and 'NAME = "dexio_consent"' in js
    assert "Domain=dexio.wiki" in js and "180 * 86400" in js
    assert re.search(r"Europe\\/", js)
