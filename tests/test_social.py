"""Sign in with Google or GitHub: the buttons, the authorize requests, the callback
(the provider's answers stubbed), linking by verified email, and the flows that sit
on top of the session (agent device sign-in, invites)."""
from __future__ import annotations

import base64
import hashlib
import html
import json
import re
import time
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, social  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

BASE = "https://localhost"
PW = "a-good-long-password-1"


def configure(monkeypatch, *providers):
    for p in social.PROVIDERS:
        if p in providers:
            monkeypatch.setenv(f"{p.upper()}_CLIENT_ID", f"{p}-client")
            monkeypatch.setenv(f"{p.upper()}_CLIENT_SECRET", f"{p}-secret")
        else:
            monkeypatch.delenv(f"{p.upper()}_CLIENT_ID", raising=False)
            monkeypatch.delenv(f"{p.upper()}_CLIENT_SECRET", raising=False)


@pytest.fixture()
def fake(monkeypatch):
    """Who the provider says the person is, per code. Tests fill `people`."""
    people: dict[str, social.Identity] = {}
    calls: list[tuple] = []

    def exchange(provider, code, redirect_uri, verifier):
        calls.append((provider, code, redirect_uri, verifier))
        if code not in people or people[code].provider != provider:
            raise social.SocialError("the sign-in was refused (400): bad_verification_code")
        return people[code]

    monkeypatch.setattr(social, "exchange", exchange)
    return {"people": people, "calls": calls}


@pytest.fixture()
def app(tmp_path, monkeypatch, fake):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEXIO_PUBLIC_URL", BASE)
    configure(monkeypatch, "google", "github")
    a = get_app(str(tmp_path / "s.db"))
    with TestClient(a, base_url=BASE, follow_redirects=False) as live:
        a.state.live = live
        yield a


def browser(app):
    return TestClient(app, base_url=BASE, follow_redirects=False)


def me(c):
    r = c.get("/api/v1/me", headers={"accept": "application/json"})
    return r.json().get("email") if r.status_code == 200 else None


def start(c, provider, next_url="/"):
    r = c.get("/auth/start", params={"provider": provider, "next": next_url})
    assert r.status_code == 303, r.text
    to = urlsplit(r.headers["location"])
    return to, {k: v[0] for k, v in parse_qs(to.query).items()}


def sign_in(c, fake, provider, code, email, subject=None, next_url="/"):
    fake["people"][code] = social.Identity(provider, subject or f"{provider}-{code}", email)
    _, q = start(c, provider, next_url)
    return c.get(f"/auth/{provider}/callback", params={"code": code, "state": q["state"]})


# ---- pages and authorize requests --------------------------------------------
def test_buttons_on_sign_in_and_signup_above_the_email_step(app):
    page = app.state.live.get("/login?next=/device%3Fcode%3DBCDF-GHJK").text
    assert "Continue with Google" in page and "Continue with GitHub" in page
    assert "Continue with email" in page and "Your agent is waiting" in page
    assert "/auth/start?provider=google&amp;next=/device%3Fcode%3DBCDF-GHJK" in page
    signup = app.state.live.get("/signup").text
    assert "Continue with Google" in signup and "Continue with email" in signup
    assert "By continuing with Google or GitHub you agree" in signup


def test_only_configured_providers_show(tmp_path, monkeypatch):
    configure(monkeypatch, "github")
    monkeypatch.setenv("DEXIO_PUBLIC_URL", BASE)
    with TestClient(get_app(str(tmp_path / "g.db")), base_url=BASE,
                    follow_redirects=False) as c:
        page = c.get("/login").text
        assert "Continue with GitHub" in page and "Continue with Google" not in page
        assert c.get("/auth/start?provider=google").headers["location"] == "/login"
        configure(monkeypatch)
        page = c.get("/login").text
        assert "Continue with Google" not in page and "Continue with GitHub" not in page
        assert c.get("/auth/start?provider=github").headers["location"] == "/login"


def test_google_authorize_request_uses_pkce(app):
    c = browser(app)
    to, q = start(c, "google", "/settings")
    assert f"{to.scheme}://{to.netloc}{to.path}" == social.GOOGLE_AUTH
    assert q["client_id"] == "google-client" and q["response_type"] == "code"
    assert q["redirect_uri"] == BASE + "/auth/google/callback"
    assert q["scope"] == "openid email profile" and q["prompt"] == "select_account"
    verifier = c.cookies.get(social.COOKIE).strip('"').split(".", 1)[1]
    want = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    assert q["code_challenge"] == want and q["code_challenge_method"] == "S256"


def test_github_authorize_request(app):
    to, q = start(browser(app), "github")
    assert f"{to.scheme}://{to.netloc}{to.path}" == social.GITHUB_AUTH
    assert q["client_id"] == "github-client" and q["scope"] == "read:user user:email"
    assert q["redirect_uri"] == BASE + "/auth/github/callback"


def test_google_on_the_signup_page_takes_an_existing_account_to_its_graph(app, fake):
    """2026-09-27: Forrest pressed Continue with Google on the sign-up page, already
    having an account and wikis, and landed on the old first-run page, because the
    sign-up page handed /welcome as the destination. It hands / now."""
    assert "/auth/start?provider=google&amp;next=/\"" in app.state.live.get("/signup").text
    c = browser(app)
    assert sign_in(c, fake, "google", "g1", "old@example.com").headers["location"] == \
        "/joined?next=%2F"  # a new account, so through the sign-up hop to /
    again = browser(app)
    assert sign_in(again, fake, "google", "g2", "old@example.com",
                   subject="google-g1").headers["location"] == "/"
    assert len(again.get("/api/v1/workspaces").json()["workspaces"]) == 1


# ---- callbacks ----------------------------------------------------------------
def test_new_person_gets_an_account_workspace_and_session(app, fake):
    c = browser(app)
    r = sign_in(c, fake, "google", "g1", "New@Example.com")
    assert r.status_code == 303 and r.headers["location"] == "/joined?next=%2F"  # then /
    assert me(c) == "new@example.com"
    assert "new's Workspace" in html.unescape(c.get("/settings").text)
    provider, code, redirect_uri, verifier = fake["calls"][0]
    assert (provider, code, redirect_uri) == ("google", "g1", BASE + "/auth/google/callback")
    assert verifier
    # No password yet: the password form refuses it, as for anyone else.
    assert not auth.authenticate(app.state.conn, "new@example.com", "!social")


def test_existing_password_account_links_and_keeps_its_password(app, fake):
    uid = auth.create_user(app.state.conn, "forrest@example.com", PW)
    c = browser(app)
    r = sign_in(c, fake, "github", "h1", "forrest@example.com", subject="1234")
    assert r.headers["location"] == "/" and me(c) == "forrest@example.com"
    assert auth.count_users(app.state.conn) == 1
    row = app.state.conn.execute("SELECT user_id, email FROM identities WHERE provider=? AND"
                                 " subject=?", ("github", "1234")).fetchone()
    assert row["user_id"] == uid
    assert auth.authenticate(app.state.conn, "forrest@example.com", PW)
    # Later sign-ins follow the GitHub account id, even after its email changes there.
    c2 = browser(app)
    sign_in(c2, fake, "github", "h2", "forrest@elsewhere.com", subject="1234")
    assert me(c2) == "forrest@example.com"
    # Google with the same address lands on the same account too.
    c3 = browser(app)
    sign_in(c3, fake, "google", "g2", "forrest@example.com")
    assert me(c3) == "forrest@example.com" and auth.count_users(app.state.conn) == 1


def test_state_must_come_from_this_browser_and_provider(app, fake):
    fake["people"]["g3"] = social.Identity("google", "s3", "x@example.com")
    starter = browser(app)
    _, q = start(starter, "google")
    other = browser(app)
    r = other.get("/auth/google/callback", params={"code": "g3", "state": q["state"]})
    assert r.status_code == 400 and "another browser" in r.text
    r = starter.get("/auth/github/callback", params={"code": "g3", "state": q["state"]})
    assert r.status_code == 400
    r = starter.get("/auth/google/callback", params={"code": "g3", "state": q["state"] + "x"})
    assert r.status_code == 400
    assert fake["calls"] == []


def test_provider_errors_are_shown(app, fake):
    c = browser(app)
    r = c.get("/auth/google/callback", params={"error": "access_denied"})
    assert r.status_code == 400 and "access denied." in r.text
    _, q = start(c, "github")
    r = c.get("/auth/github/callback", params={"code": "nope", "state": q["state"]})
    assert r.status_code == 502 and "The sign-in was refused" in r.text


def test_agent_device_sign_in_with_google(app, fake):
    got = app.state.live.post("/api/v1/device/code", json={"client_name": "Claude Code"}).json()
    c = browser(app)
    login = c.get(f"/device?code={got['user_code']}").headers["location"]
    assert "Continue with Google" in c.get(login).text
    next_url = parse_qs(urlsplit(login).query)["next"][0]
    r = sign_in(c, fake, "google", "g4", "dana@example.com", next_url=next_url)
    assert r.headers["location"] == f"/joined?next=%2Fdevice%3Fcode%3D{got['user_code']}"
    page = c.get(f"/device?code={got['user_code']}").text
    m = re.search(r'type="hidden" name="workspace" value="(\w+)"|name="workspace" value="(\w+)" checked', page)
    ws = m.group(1) or m.group(2)
    assert c.post("/device", data={"code": got["user_code"], "workspace": ws,
                                   "action": "allow"}).status_code == 200
    tok = app.state.live.post("/api/v1/device/token", json={"device_code": got["device_code"]})
    assert tok.json()["account"] == "dana@example.com"


def test_invite_sign_up_joins_without_a_workspace_of_its_own(app, fake, monkeypatch):
    owner = browser(app)
    sign_in(owner, fake, "google", "g5", "owner@example.com")
    with app.state.conn:                       # Free allows one member; Team invites
        app.state.conn.execute("UPDATE workspaces SET plan='team'")
    from test_workspaces import invite_code
    link = "/invite/" + invite_code(owner, monkeypatch, "guest@example.com")
    guest = browser(app)
    assert guest.get(link).headers["location"].startswith("/signup?next=/invite/")
    r = sign_in(guest, fake, "github", "h3", "guest@example.com", next_url=link)
    assert r.headers["location"] == link
    guest.get(link)
    names = [w["name"] for w in guest.get(
        "/api/v1/workspaces", headers={"accept": "application/json"}).json()["workspaces"]]
    assert names == ["owner's Workspace"]


def test_the_unused_workos_column_is_dropped(app):
    conn = app.state.conn
    conn.execute("ALTER TABLE users ADD COLUMN workos_id TEXT")
    conn.execute("CREATE UNIQUE INDEX users_workos_id ON users(workos_id)")
    conn.commit()
    auth.init(conn)
    assert "workos_id" not in {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    auth.init(conn)                            # and again, with nothing to drop


# ---- what the providers return -----------------------------------------------
def id_token(claims):
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJSUzI1NiJ9.{body}.sig"


def test_google_id_token_checks(monkeypatch):
    configure(monkeypatch, "google")
    good = {"iss": "https://accounts.google.com", "aud": "google-client", "sub": "1071",
            "email": "a@gmail.com", "email_verified": True, "exp": time.time() + 300,
            "name": "A"}
    seen = {}

    def answer(claims):
        def request(method, url, form=None, token=""):
            seen.update(form or {})
            return {"id_token": id_token(claims), "access_token": "x"}
        return request

    monkeypatch.setattr(social, "_request", answer(good))
    who = social.exchange("google", "c", "https://app/cb", "ver")
    assert (who.subject, who.email) == ("1071", "a@gmail.com")
    assert seen["code_verifier"] == "ver" and seen["client_secret"] == "google-secret"
    for bad, why in (({"aud": "someone-else"}, "not for Dexio"),
                     ({"iss": "https://evil.example"}, "not for Dexio"),
                     ({"email_verified": False}, "not verified"),
                     ({"exp": time.time() - 5}, "expired")):
        monkeypatch.setattr(social, "_request", answer({**good, **bad}))
        with pytest.raises(social.SocialError, match=why):
            social.exchange("google", "c", "https://app/cb", "ver")


def test_github_picks_the_verified_primary_email(monkeypatch):
    configure(monkeypatch, "github")
    answers = {
        social.GITHUB_TOKEN: {"access_token": "gho_x"},
        f"{social.GITHUB_API}/user": {"id": 42, "login": "dana", "name": None,
                                      "email": "unverified@example.com"},
        f"{social.GITHUB_API}/user/emails": [
            {"email": "old@example.com", "verified": True, "primary": False},
            {"email": "dana@example.com", "verified": True, "primary": True},
            {"email": "typo@example.com", "verified": False, "primary": False}],
    }
    monkeypatch.setattr(social, "_request", lambda m, url, form=None, token="": answers[url])
    who = social.exchange("github", "c", "https://app/cb", "")
    assert (who.subject, who.email, who.first, who.last) == ("42", "dana@example.com", "", "")
    answers[f"{social.GITHUB_API}/user"]["name"] = "Dana van der Berg"
    who = social.exchange("github", "c", "https://app/cb", "")
    assert (who.first, who.last) == ("Dana", "van der Berg")
    answers[f"{social.GITHUB_API}/user/emails"] = [
        {"email": "typo@example.com", "verified": False, "primary": True}]
    with pytest.raises(social.SocialError, match="no verified email"):
        social.exchange("github", "c", "https://app/cb", "")
    answers[social.GITHUB_TOKEN] = {"error": "bad_verification_code",
                                    "error_description": "The code passed is incorrect."}
    with pytest.raises(social.SocialError, match="code passed is incorrect"):
        social.exchange("github", "c", "https://app/cb", "")


def test_state_round_trip_and_expiry():
    key = b"k" * 32
    state, cookie, verifier = social.make_state(key, "google", "/device?code=ABCD")
    assert social.read_state(key, "google", state, cookie) == ("/device?code=ABCD", verifier)
    assert social.read_state(key, "github", state, cookie) is None
    assert social.read_state(key, "google", state, "other.cookie") is None
    assert social.read_state(b"x" * 32, "google", state, cookie) is None
