"""First and last names: from Google and GitHub at sign-in (never over a name the
person set), optional at email sign-up, editable on the account page, and shown in
the member list, invite emails, the default workspace name and /api/v1/me."""
from __future__ import annotations

import base64
import html
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, mail, social  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

BASE = "https://localhost"
PW = "a-good-long-password-1"


@pytest.fixture()
def fake(monkeypatch):
    people: dict[str, social.Identity] = {}
    monkeypatch.setattr(social, "exchange",
                        lambda provider, code, redirect_uri, verifier: people[code])
    return people


@pytest.fixture()
def app(tmp_path, monkeypatch, fake):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEXIO_PUBLIC_URL", BASE)
    for p in ("GOOGLE", "GITHUB"):
        monkeypatch.setenv(f"{p}_CLIENT_ID", "id")
        monkeypatch.setenv(f"{p}_CLIENT_SECRET", "secret")
    a = get_app(str(tmp_path / "n.db"))
    with TestClient(a, base_url=BASE, follow_redirects=False) as live:
        a.state.live = live
        yield a


def browser(app):
    return TestClient(app, base_url=BASE, follow_redirects=False)


def me(c):
    return c.get("/api/v1/me", headers={"accept": "application/json"}).json()


def sign_in(c, fake, provider, code, email, first="", last=""):
    fake[code] = social.Identity(provider, f"{provider}-{code}", email, first, last)
    r = c.get("/auth/start", params={"provider": provider})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return c.get(f"/auth/{provider}/callback", params={"code": code, "state": state})


def test_google_names_fill_a_new_account_and_its_workspace(app, fake):
    c = browser(app)
    sign_in(c, fake, "google", "g1", "dana@example.com", "Dana", "Lee")
    assert me(c) == {"email": "dana@example.com", "first_name": "Dana", "last_name": "Lee"}
    assert "Dana's Workspace" in html.unescape(c.get("/settings").text)


def test_a_github_account_without_a_name_names_the_workspace_by_username(app, fake):
    fake["h9"] = social.Identity("github", "github-h9", "gh@example.com", "", "", "octo-dev")
    c = browser(app)
    r = c.get("/auth/start", params={"provider": "github"})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    c.get("/auth/github/callback", params={"code": "h9", "state": state})
    assert me(c)["first_name"] == ""                  # the username is not taken as a name
    assert "octo-dev's Workspace" in html.unescape(c.get("/settings").text)


def test_provider_names_never_overwrite_names_the_person_set(app, fake):
    uid = auth.create_user(app.state.conn, "sam@example.com", PW, "Sam", "")
    c = browser(app)
    sign_in(c, fake, "github", "h1", "sam@example.com", "samuel-gh", "")
    assert me(c)["first_name"] == "Sam"
    # An account with no name at all takes the provider's.
    auth.set_names(app.state.conn, uid, "", "")
    sign_in(browser(app), fake, "google", "g2", "sam@example.com", "Samuel", "Ortiz")
    assert auth.names(app.state.conn, uid) == ("Samuel", "Ortiz")


def test_email_signup_needs_a_first_name(app):
    page = app.state.live.post("/login/email", data={"email": "ann@example.com",
                                                     "mode": "signup"}).text
    assert 'name="first_name"' in page and 'name="last_name"' in page
    assert 'name="first_name" autocomplete="given-name" maxlength="80"' in page
    c = browser(app)
    r = c.post("/signup", data={"email": "ann@example.com", "password": PW,
                                "confirm_password": PW, "agree": "1",
                                "first_name": "  Ann\n", "last_name": "O'Neil"})
    assert r.status_code == 303
    assert me(c) == {"email": "ann@example.com", "first_name": "Ann", "last_name": "O'Neil"}
    assert "Ann's Workspace" in html.unescape(c.get("/settings").text)
    assert "First name <span" not in page and 'value="" required autofocus' in page
    r = browser(app).post("/signup", data={"email": "noname@example.com", "password": PW,
                                           "confirm_password": PW, "agree": "1",
                                           "last_name": "Only"})
    assert r.status_code == 400 and "Enter your first name." in r.text
    assert 'value="Only"' in r.text                   # what was typed is kept


def test_account_page_edits_the_name(app):
    c = browser(app)
    c.post("/signup", data={"email": "bo@example.com", "password": PW,
                            "confirm_password": PW, "agree": "1", "first_name": "B"})
    page = c.get("/settings/profile").text
    assert "Save name" in page and "Change password" in page
    r = c.post("/account/name", data={"first_name": "Bo", "last_name": "Kim"})
    assert r.status_code == 303 and r.headers["location"] == "/settings/profile?done=name"
    page = c.get(r.headers["location"]).text
    assert "Name saved." in page and 'value="Kim"' in page
    assert me(c)["last_name"] == "Kim"
    r = c.post("/account/name", data={"first_name": "x"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert browser(app).post("/account/name", data={"first_name": "x"}).status_code == 303


def test_members_and_invites_show_names(app, fake, monkeypatch):
    sent = []
    monkeypatch.setattr(mail, "invite", lambda to, inviter, ws, link, *_: sent.append(inviter) or True)
    owner = browser(app)
    sign_in(owner, fake, "google", "g3", "owner@example.com", "Olive", "Ng")
    with app.state.conn:
        app.state.conn.execute("UPDATE workspaces SET plan='team'")
    owner.post("/settings/invite", data={"email": "guest@example.com"})
    assert sent == ["Olive Ng"]
    page = html.unescape(owner.get("/settings/members").text)
    assert '<td>Olive Ng <span class="tag">you</span><span class="sub">owner@example.com</span>' in page


def test_name_cleaning_and_splitting():
    assert auth.clean_name("  Dana\t\n Lee\x00 ") == "Dana Lee"
    assert len(auth.clean_name("x" * 300)) == 80
    assert auth.split_name("Dana van der Berg") == ("Dana", "van der Berg")
    assert auth.split_name("Cher") == ("Cher", "")
    assert auth.split_name("") == ("", "")
    assert auth.display_name("", "", "a@b.co") == "a@b.co"
    assert auth.display_name("Dana", "", "a@b.co") == "Dana"


def test_google_prefers_given_and_family_names(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "gid")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "gs")
    claims = {"iss": "https://accounts.google.com", "aud": "gid", "sub": "1", "exp": time.time() + 60,
              "email": "a@gmail.com", "email_verified": True, "name": "Ana María López",
              "given_name": "Ana María", "family_name": "López"}

    def token(c):
        body = base64.urlsafe_b64encode(json.dumps(c).encode()).decode().rstrip("=")
        return {"id_token": f"h.{body}.s"}

    monkeypatch.setattr(social, "_request", lambda *a, **k: token(claims))
    who = social.exchange("google", "c", "https://x/cb", "v")
    assert (who.first, who.last) == ("Ana María", "López")
    only_name = {k: v for k, v in claims.items() if k not in ("given_name", "family_name")}
    monkeypatch.setattr(social, "_request", lambda *a, **k: token(only_name))
    assert (social.exchange("google", "c", "https://x/cb", "v").first) == "Ana"


def test_existing_database_gains_the_name_columns(app):
    cols = {r[1] for r in app.state.conn.execute("PRAGMA table_info(users)")}
    assert {"first_name", "last_name"} <= cols
    auth.init(app.state.conn)                  # idempotent
    assert re.search(r"first_name", str(cols))
