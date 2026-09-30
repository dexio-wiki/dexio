import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from dexio.server import auth
from dexio.server.app import get_app


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    auth.init(c)
    return c


# ---- passwords ---------------------------------------------------------
def test_hash_is_salted_and_verifies(conn):
    a = auth.hash_password("correct horse battery")
    b = auth.hash_password("correct horse battery")
    assert a != b, "same password must not produce the same hash"
    assert auth.verify_password("correct horse battery", a)
    assert not auth.verify_password("Correct horse battery", a)


def test_password_never_stored_in_clear(conn):
    auth.create_user(conn, "a@example.com", "hunter2hunter2")
    stored = conn.execute("SELECT password_hash FROM users").fetchone()[0]
    assert "hunter2hunter2" not in stored
    assert stored.startswith("scrypt$")


def test_garbage_hash_does_not_raise(conn):
    for junk in ("", "nonsense", "scrypt$x$y$z$q$w", "md5$aa$bb"):
        assert auth.verify_password("whatever", junk) is False


def test_short_password_rejected(conn):
    with pytest.raises(ValueError):
        auth.create_user(conn, "a@example.com", "short")


def test_authenticate(conn):
    auth.create_user(conn, "a@example.com", "hunter2hunter2")
    assert auth.authenticate(conn, "a@example.com", "hunter2hunter2")
    assert auth.authenticate(conn, "A@EXAMPLE.COM", "hunter2hunter2"), "email is case-insensitive"
    assert auth.authenticate(conn, "a@example.com", "wrong") is None
    assert auth.authenticate(conn, "nobody@example.com", "hunter2hunter2") is None


# ---- sessions ----------------------------------------------------------
def test_session_round_trip():
    key = b"k" * 32
    cookie = auth.issue_session(key, "a@example.com")
    assert auth.read_session(key, cookie) == "a@example.com"


def test_session_rejects_tampering_and_wrong_key():
    key = b"k" * 32
    cookie = auth.issue_session(key, "a@example.com")
    body, _, sig = cookie.partition(".")
    forged = auth._b64e(b'{"sub":"admin@example.com","iat":0}') + "." + sig
    assert auth.read_session(key, forged) is None
    assert auth.read_session(b"j" * 32, cookie) is None
    assert auth.read_session(key, "garbage") is None


def test_session_expires():
    key = b"k" * 32
    old = auth.issue_session(key, "a@example.com", now=time.time() - auth.SESSION_MAX_AGE - 10)
    assert auth.read_session(key, old) is None


# ---- http --------------------------------------------------------------
@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("DEXIO_VIEW_PASSWORD", raising=False)
    monkeypatch.setenv("DEXIO_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("DEXIO_ADMIN_PASSWORD", "seeded-password-123")
    return TestClient(get_app(str(tmp_path / "t.db")), follow_redirects=False)


def test_seeded_account_exists_and_server_is_closed(client):
    r = client.get("/", headers={"accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"].startswith("/login")


def test_login_page_renders(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert "Sign in" in r.text and "Continue with email" in r.text
    assert 'name="password"' not in r.text          # email first; the password comes next
    r = client.post("/login/email", data={"email": "admin@example.com"})
    assert r.status_code == 200 and 'name="password"' in r.text


def test_login_rejects_bad_password_without_leaking_which_field(client):
    r = client.post("/login", data={"email": "admin@example.com", "password": "nope"})
    assert r.status_code == 401
    r2 = client.post("/login", data={"email": "ghost@example.com", "password": "nope"})
    assert r2.status_code == 401
    assert "Incorrect email or password." in r.text
    assert r.text.replace("admin@example.com", "") == r2.text.replace("ghost@example.com", "")


def test_login_sets_cookie_and_opens_the_app(client):
    r = client.post("/login", data={"email": "admin@example.com",
                                    "password": "seeded-password-123"})
    assert r.status_code == 303
    cookie = r.cookies.get(auth.SESSION_COOKIE)
    assert cookie
    set_cookie = r.headers["set-cookie"]
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie and "SameSite=lax" in set_cookie

    ok = client.get("/", headers={"accept": "text/html"}, cookies={auth.SESSION_COOKIE: cookie})
    assert ok.status_code == 200


def test_open_redirect_is_refused(client):
    r = client.post("/login", data={"email": "admin@example.com",
                                    "password": "seeded-password-123",
                                    "next": "//evil.example.com/"})
    assert r.headers["location"] == "/"


def test_api_client_gets_401_json_not_a_redirect(client):
    r = client.get("/api/v1/workspaces")
    assert r.status_code == 401
    assert r.json()["error"]


def test_basic_auth_works_against_a_real_account(client):
    r = client.get("/api/v1/workspaces", auth=("admin@example.com", "seeded-password-123"))
    assert r.status_code == 200


def test_logout_clears_the_cookie(client):
    r = client.post("/login", data={"email": "admin@example.com",
                                    "password": "seeded-password-123"})
    out = client.get("/logout", cookies={auth.SESSION_COOKIE: r.cookies.get(auth.SESSION_COOKIE)})
    assert out.status_code == 303
    assert 'dexio_session=""' in out.headers["set-cookie"] or \
           "Max-Age=0" in out.headers["set-cookie"]


def test_healthz_stays_public(client):
    assert client.get("/healthz").status_code == 200


# ---- change password ---------------------------------------------------
ADMIN, PW = "admin@example.com", "seeded-password-123"
NEW_PW = "a-much-better-password-456"


def _session(client, email=ADMIN, password=PW):
    r = client.post("/login", data={"email": email, "password": password})
    return r.cookies.get(auth.SESSION_COOKIE)


def _change(client, cookie, current, new, confirm=None, headers=None):
    return client.post("/account/password", cookies={auth.SESSION_COOKIE: cookie},
                       headers=headers or {},
                       data={"current_password": current, "new_password": new,
                             "confirm_password": new if confirm is None else confirm})


def test_account_page_needs_a_session(client):
    # /account is now Settings > Profile
    r = client.get("/account")
    assert r.status_code == 303 and r.headers["location"] == "/settings/profile"
    r = client.get("/settings/profile")
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/settings/profile"
    r = client.post("/account/password", data={"current_password": PW})
    assert r.status_code == 303
    page = client.get("/settings/profile", cookies={auth.SESSION_COOKIE: _session(client)})
    assert page.status_code == 200
    assert "Change password" in page.text and ADMIN in page.text


def test_app_header_links_to_account(client):
    r = client.get("/", headers={"accept": "text/html"},
                   cookies={auth.SESSION_COOKIE: _session(client)})
    assert 'href="/settings"' in r.text and "__ACCOUNT__" not in r.text


def test_change_password_rejects_bad_input_and_keeps_the_old_one(client):
    cookie = _session(client)
    r = _change(client, cookie, "wrong-password-000", NEW_PW)
    assert r.status_code == 401 and "Current password is incorrect." in r.text
    r = _change(client, cookie, PW, NEW_PW, confirm=NEW_PW + "x")
    assert r.status_code == 400 and "do not match" in r.text
    r = _change(client, cookie, PW, "short")
    assert r.status_code == 400 and "at least 12 characters" in r.text
    r = _change(client, cookie, PW, PW)
    assert r.status_code == 400 and "different" in r.text
    assert _session(client, ADMIN, PW)          # nothing changed


def test_change_password_works_and_signs_out_other_sessions(client):
    key = auth.secret_key(client.app.state.conn)
    other = auth.issue_session(key, ADMIN, now=time.time() - 60)   # another browser
    mine = auth.issue_session(key, ADMIN, now=time.time() - 30)
    html_ = {"accept": "text/html"}
    assert client.get("/", headers=html_, cookies={auth.SESSION_COOKIE: other}).status_code == 200

    r = _change(client, mine, PW, NEW_PW)
    assert r.status_code == 303 and r.headers["location"].endswith("done=password")
    fresh = r.cookies.get(auth.SESSION_COOKIE)
    assert fresh and fresh != mine
    back = client.get(r.headers["location"], cookies={auth.SESSION_COOKIE: fresh})
    assert "Password changed." in back.text

    assert client.get("/", headers=html_, cookies={auth.SESSION_COOKIE: other}).status_code == 303
    assert client.get("/", headers=html_, cookies={auth.SESSION_COOKIE: mine}).status_code == 303
    assert client.get("/", headers=html_, cookies={auth.SESSION_COOKIE: fresh}).status_code == 200
    assert not _session(client, ADMIN, PW)
    assert _session(client, ADMIN, NEW_PW)
    assert client.get("/api/v1/workspaces", auth=(ADMIN, NEW_PW)).status_code == 200


def test_change_password_refuses_cross_site_posts(client):
    cookie = _session(client)
    r = _change(client, cookie, PW, NEW_PW, headers={"origin": "https://evil.example.com"})
    assert r.status_code == 403
    r = _change(client, cookie, PW, NEW_PW, headers={"referer": "https://evil.example.com/x"})
    assert r.status_code == 403
    assert _session(client, ADMIN, PW)          # still the old password
    r = _change(client, cookie, PW, NEW_PW, headers={"origin": "http://testserver"})
    assert r.status_code == 303


def test_cli_password_reset_also_ends_sessions(conn):
    auth.create_user(conn, "a@example.com", "first-password-111")
    key = b"k" * 32
    claims = auth.read_session_claims(key, auth.issue_session(key, "a@example.com",
                                                             now=time.time() - 5))
    assert auth.session_is_current(conn, *claims)
    auth.set_password(conn, "a@example.com", "second-password-222")
    assert not auth.session_is_current(conn, *claims)
    assert not auth.session_is_current(conn, "ghost@example.com", time.time())


def test_existing_users_table_gains_the_column(tmp_path):
    import sqlite3 as _sq
    path = tmp_path / "old.db"
    c = _sq.connect(path)
    c.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT NOT NULL UNIQUE"
                    " COLLATE NOCASE, password_hash TEXT NOT NULL, created_at REAL NOT NULL,"
                    " last_login REAL);")
    c.commit()
    auth.init(c)
    cols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    assert "password_changed_at" in cols
