"""Google and GitHub sign-in started by the desktop app: the system browser does the
provider round trip, and the app's own window redeems the handoff (desktop.py)."""
from __future__ import annotations

import html
import re
import secrets
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import desktop, social  # noqa: E402
from test_social import BASE, app, browser, fake, me  # noqa: E402,F401  (fixtures)


def app_verifier() -> tuple[str, str]:
    v = secrets.token_urlsafe(32)
    return v, desktop.challenge_for(v)


def browser_half(c, fake, provider, code, email, challenge, next_url="/"):
    """The system browser's part: /auth/start with the app's challenge, the provider,
    the callback. Returns the callback response."""
    fake["people"][code] = social.Identity(provider, f"{provider}-{code}", email)
    r = c.get("/auth/start", params={"provider": provider, "next": next_url,
                                     "desktop": challenge})
    assert r.status_code == 303, r.text
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    return c.get(f"/auth/{provider}/callback", params={"code": code, "state": state})


def handoff_code(page: str) -> str:
    link = html.unescape(re.search(r'href="(dexio://auth\?code=[^"]+)"', page).group(1))
    return parse_qs(urlsplit(link).query)["code"][0]


def test_browser_hands_the_sign_in_to_the_app_window(app, fake):
    verifier, challenge = app_verifier()
    system_browser = browser(app)
    r = browser_half(system_browser, fake, "google", "d1", "new@example.com", challenge,
                     next_url="/settings")
    assert r.status_code == 200
    assert 'http-equiv="refresh"' in r.text and "Open Dexio" in r.text
    # The browser was only the go-between: it is not signed in.
    assert me(system_browser) is None
    window = browser(app)
    done = window.get("/auth/desktop", params={"code": handoff_code(r.text),
                                               "verifier": verifier})
    assert done.status_code == 303 and done.headers["location"] == "/settings"
    assert me(window) == "new@example.com"
    # A new account's workspace comes along to the window.
    assert "new's Workspace" in html.unescape(window.get("/settings").text)


def test_github_and_an_existing_account(app, fake):
    browser_half(browser(app), fake, "github", "h1", "dana@example.com", app_verifier()[1])
    verifier, challenge = app_verifier()
    r = browser_half(browser(app), fake, "github", "h2", "dana@example.com", challenge)
    window = browser(app)
    window.get("/auth/desktop", params={"code": handoff_code(r.text), "verifier": verifier})
    assert me(window) == "dana@example.com"


def test_a_stolen_link_is_worthless_without_the_apps_verifier(app, fake):
    verifier, challenge = app_verifier()
    r = browser_half(browser(app), fake, "google", "d2", "eve@example.com", challenge)
    code = handoff_code(r.text)
    thief = browser(app)
    near = verifier[:-1] + ("B" if verifier.endswith("A") else "A")
    for wrong in ("", secrets.token_urlsafe(32), near):
        got = thief.get("/auth/desktop", params={"code": code, "verifier": wrong})
        assert got.status_code == 400 and "expired" in got.text
    assert me(thief) is None
    tampered = code[:-2] + ("AA" if not code.endswith("AA") else "BB")
    assert thief.get("/auth/desktop", params={"code": tampered,
                                              "verifier": verifier}).status_code == 400


def test_a_malformed_challenge_is_refused_at_the_start(app):
    r = browser(app).get("/auth/start", params={"provider": "google", "desktop": "short"})
    assert r.status_code == 400 and "Start again from the app" in r.text


def test_without_the_app_the_browser_signs_in_as_before(app, fake):
    fake["people"]["p1"] = social.Identity("google", "g-p1", "plain@example.com")
    c = browser(app)
    r = c.get("/auth/start", params={"provider": "google", "next": "/"})
    state = parse_qs(urlsplit(r.headers["location"]).query)["state"][0]
    r = c.get("/auth/google/callback", params={"code": "p1", "state": state})
    assert r.status_code == 303 and me(c) == "plain@example.com"


def test_codes_expire_after_two_minutes():
    key = b"k" * 32
    verifier, challenge = app_verifier()
    code = desktop.issue(key, 7, challenge, "/x", 3, now=1000)
    assert desktop.redeem(key, code, verifier, now=1000 + desktop.TTL) == {
        "user_id": 7, "next": "/x", "workspace": 3}
    assert desktop.redeem(key, code, verifier, now=1001 + desktop.TTL) is None
    assert desktop.redeem(b"x" * 32, code, verifier, now=1000) is None
    assert desktop.redeem(key, "garbage", verifier, now=1000) is None
