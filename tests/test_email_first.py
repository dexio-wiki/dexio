"""Email-first sign-in and sign-up, as Composio's works: Google and GitHub buttons,
then only an email; what comes next depends on whether the address has an account."""
from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, social  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

BASE = "https://localhost"
PW = "a-good-long-password-1"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEXIO_PUBLIC_URL", BASE)
    for p in ("GOOGLE", "GITHUB"):
        monkeypatch.setenv(f"{p}_CLIENT_ID", "id")
        monkeypatch.setenv(f"{p}_CLIENT_SECRET", "secret")
    a = get_app(str(tmp_path / "e.db"))
    with TestClient(a, base_url=BASE, follow_redirects=False) as live:
        a.state.live = live
        yield a


def browser(app):
    return TestClient(app, base_url=BASE, follow_redirects=False)


def step(c, email, mode="login", next_url="/", **headers):
    return c.post("/login/email", data={"email": email, "mode": mode, "next": next_url},
                  headers=headers)


def test_first_step_is_only_the_email(app):
    for path, title in (("/login", "Sign in"), ("/signup", "Create your account")):
        page = app.state.live.get(path).text
        assert title in page and 'name="email"' in page and "Continue with email" in page
        assert 'name="password"' not in page and 'name="first_name"' not in page
        assert "Continue with Google" in page


def test_a_new_address_gets_names_password_and_terms(app):
    c = browser(app)
    r = step(c, "New@Example.com")
    assert r.status_code == 200 and "Create your account" in r.text
    for field in ('name="first_name"', 'name="last_name"', 'name="password"',
                  'name="confirm_password"', 'name="agree"', 'action="/signup"'):
        assert field in r.text, field
    assert "Continuing as <b>new@example.com</b>" in r.text and ">Change</a>" in r.text
    assert 'name="email" value="new@example.com"' in r.text
    assert 'autocomplete="username" value="new@example.com"' in r.text
    r = c.post("/signup", data={"email": "new@example.com", "first_name": "Nia",
                                "password": PW, "confirm_password": PW, "agree": "1"})
    assert r.status_code == 303 and r.headers["location"] == "/joined?next=%2F"  # then /


def test_an_existing_address_gets_the_password(app):
    auth.create_user(app.state.conn, "old@example.com", PW)
    c = browser(app)
    r = step(c, "old@example.com")
    assert 'name="password"' in r.text and 'action="/login"' in r.text
    assert 'name="first_name"' not in r.text and "Forgot your password?" in r.text
    # From the sign-up page too, with a word about it.
    r = step(c, "old@example.com", mode="signup")
    assert 'name="password"' in r.text and "You already have an account" in r.text
    r = c.post("/login", data={"email": "old@example.com", "password": "wrong-password-1"})
    assert r.status_code == 401 and "Incorrect email or password." in r.text
    assert 'value="old@example.com"' in r.text and 'name="password"' in r.text
    r = c.post("/login", data={"email": "old@example.com", "password": PW, "next": "/settings"})
    assert r.status_code == 303 and r.headers["location"] == "/settings"


def test_a_google_only_account_gets_its_button(app, monkeypatch):
    uid = auth.user_from_identity(app.state.conn, "google", "g-1", "g@example.com")[0]["id"]
    r = step(browser(app), "g@example.com")
    assert "Continue with Google" in r.text and "Continue with GitHub" not in r.text
    assert 'name="password"' not in r.text and "Set a password" in r.text
    assert 'action="/forgot"' in r.text
    assert 'value="g@example.com"' in app.state.live.get("/forgot?email=g@example.com").text
    assert not auth.has_password(app.state.conn, uid)


def test_next_survives_every_step(app):
    nxt = "/device?code=BCDF-GHJK"
    r = step(browser(app), "someone@example.com", next_url=nxt)
    assert f'name="next" value="{nxt}"' in r.text and "Your agent is waiting" in r.text
    assert "/login?next=/device%3Fcode%3DBCDF-GHJK" in r.text        # the Change link


def test_bad_address_cross_site_and_rate_limit(app):
    c = browser(app)
    r = step(c, "not-an-email")
    assert r.status_code == 400 and "Enter your email address." in r.text
    r = step(c, "x@example.com", Origin="https://evil.example")
    assert r.status_code == 403
    codes = [step(c, f"u{i}@example.com").status_code for i in range(31)]
    assert codes[-1] == 429 and 200 in codes


def test_signup_errors_keep_the_create_step_and_what_was_typed(app):
    c = browser(app)
    r = c.post("/signup", data={"email": "t@example.com", "first_name": "Tia", "last_name": "Ro",
                                "password": PW, "confirm_password": PW + "x", "agree": "1"})
    assert r.status_code == 400 and "The passwords do not match." in r.text
    assert 'value="Tia"' in r.text and 'value="Ro"' in r.text and 'value="t@example.com"' in r.text
