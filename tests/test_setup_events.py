"""Setup events (server/setup_events.py, Forrest 2026-10-08): what a person did while
connecting their first agent, by account, so a signup that never connects shows
where it stopped. The server records what passes through it; the setup screens
report the rest through POST /api/v1/setup/event."""
from __future__ import annotations

import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio import ais, render  # noqa: E402
from dexio.server import auth, erase, pages, setup_events  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_oauth import authorize, consent, pkce, register  # noqa: E402
from test_workspaces import browser, mcp, signup, token_from_connect  # noqa: E402

CHROME_MAC = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 18_6 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/18.6 Mobile/15E148 Safari/604.1")


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEXIO_PUBLIC_URL", "https://localhost")
    a = get_app(str(tmp_path / "s.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        yield a


def uid(app, email):
    return auth.user_by_email(app.state.conn, email)["id"]


def events(app, email):
    return [(e["event"], e["detail"]) for e in setup_events.trail(app.state.conn, uid(app, email))["events"]]


def handle(app, email):
    return app.state.conn.execute(
        "SELECT w.handle FROM workspaces w JOIN memberships m ON m.workspace_id = w.id"
        " WHERE m.user_id=?", (uid(app, email),)).fetchone()["handle"]


def test_the_server_records_naming_picking_and_a_key_in_order(app):
    c = browser(app)
    signup(c, "ann@example.com")
    w = handle(app, "ann@example.com")
    name = app.state.conn.execute("SELECT name FROM workspaces WHERE handle=?", (w,)).fetchone()["name"]
    assert c.post(f"/api/v1/workspace/name?w={w}", json={"name": name}).status_code == 200
    assert c.get("/api/v1/connect", params={"client": "chatgpt", "view": "graph", "w": w}).status_code == 200
    assert c.get("/api/v1/connect", params={"client": "claude-code", "w": w}).status_code == 200
    tok = token_from_connect(c, "claude-code")
    assert events(app, "ann@example.com") == [
        ("named", "kept"), ("agent_picked", "chatgpt graph"),
        ("agent_picked", "claude-code agents"), ("key_minted", "claude-code")]
    t = setup_events.trail(app.state.conn, uid(app, "ann@example.com"))
    assert not t["connected"] and t["keys"][0]["last_used"] is None and t["writes"] == 0

    # The key reaching Dexio and a first page are what "connected" means.
    mcp(app, tok, "write_page", wiki="main", path="hello", text="# Hello", agent="cc")
    t = setup_events.trail(app.state.conn, uid(app, "ann@example.com"))
    assert t["connected"] and t["keys"][0]["last_used"] and t["writes"] == 1 and t["pages"] == 1
    assert t["first_write"] is not None


def test_renaming_counts_as_changed(app):
    c = browser(app)
    signup(c, "bea@example.com")
    c.post("/api/v1/workspace/name", json={"name": "Acme"})
    assert events(app, "bea@example.com") == [("named", "changed")]


def test_the_beacon_takes_only_known_events_and_short_clean_details(app):
    c = browser(app)
    signup(c, "cy@example.com")
    w = handle(app, "cy@example.com")
    ok = c.post(f"/api/v1/setup/event?w={w}", json={"event": "connect_shown", "detail": "graph"},
                headers={"User-Agent": SAFARI_IPHONE})
    assert ok.status_code == 204
    c.post(f"/api/v1/setup/event?w={w}", json={"event": "copied", "detail": "agent-prompt"})
    c.post(f"/api/v1/setup/event?w={w}", json={"event": "hidden"})
    c.post(f"/api/v1/setup/event?w={w}",
           json={"event": "step", "detail": "<script>alert(1)</script>" + "x" * 80})
    assert c.post("/api/v1/setup/event", json={"event": "named"}).status_code == 400  # server-only
    assert c.post("/api/v1/setup/event", json={"event": "nope"}).status_code == 400
    assert c.post("/api/v1/setup/event", content=b"not json").status_code == 400
    got = events(app, "cy@example.com")
    assert got[:3] == [("connect_shown", "graph mobile Safari"), ("copied", "agent-prompt"),
                       ("hidden", None)]
    assert got[3][0] == "step" and "<" not in got[3][1] and len(got[3][1]) <= 30
    assert len(got) == 4
    row = app.state.conn.execute("SELECT DISTINCT workspace_id FROM setup_events WHERE user_id=?",
                                 (uid(app, "cy@example.com"),)).fetchall()
    assert len(row) == 1 and row[0]["workspace_id"] is not None


def test_the_beacon_needs_a_session_and_the_same_site(app):
    stranger = browser(app)
    assert stranger.post("/api/v1/setup/event", json={"event": "hidden"}).status_code == 401
    c = browser(app)
    signup(c, "dee@example.com")
    r = c.post("/api/v1/setup/event", json={"event": "hidden"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    assert events(app, "dee@example.com") == []


def test_the_beacon_is_rate_limited_per_person(app):
    c = browser(app)
    signup(c, "eve@example.com")
    for _ in range(130):
        assert c.post("/api/v1/setup/event", json={"event": "visible"}).status_code == 204
    assert len(events(app, "eve@example.com")) == 120


def test_consent_shown_approved_and_denied_name_the_app(app):
    ann = browser(app)
    signup(ann, "ann@example.com")
    cid = register(app, "Claude")
    _v, challenge = pkce()
    back = consent(ann, authorize(app, ann, cid, challenge))
    assert back.status_code == 303
    cid2 = register(app, "ChatGPT")
    consent(ann, authorize(app, ann, cid2, challenge), action="deny")
    assert events(app, "ann@example.com") == [
        ("consent_shown", "Claude"), ("consent_approved", "Claude"),
        ("consent_shown", "ChatGPT"), ("consent_denied", "ChatGPT")]


def test_events_go_with_the_account_and_age_out(app):
    c = browser(app)
    signup(c, "fay@example.com")
    c.post("/api/v1/setup/event", json={"event": "hidden"})
    conn = app.state.conn
    who = uid(app, "fay@example.com")
    setup_events.record(conn, who, "left")
    conn.execute("UPDATE setup_events SET at=? WHERE event='left'",
                 (time.time() - (setup_events.RETAIN_DAYS + 1) * 86400,))
    conn.commit()
    setup_events.init(conn)                   # runs at every start
    assert events(app, "fay@example.com") == [("hidden", None)]
    erase.delete_account(conn, who)
    assert conn.execute("SELECT COUNT(*) AS n FROM setup_events WHERE user_id=?",
                        (who,)).fetchone()["n"] == 0


def test_device_classes():
    assert setup_events.device_of(CHROME_MAC) == "desktop Chrome"
    assert setup_events.device_of(SAFARI_IPHONE) == "mobile Safari"
    assert setup_events.device_of("Mozilla/5.0 (iPad; CPU OS 18_0 like Mac OS X) "
                                  "AppleWebKit/605.1.15 Version/18.0 Safari/604.1") == "tablet Safari"
    assert setup_events.device_of("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                  "Chrome/141.0 Safari/537.36 Edg/141.0") == "desktop Edge"
    assert setup_events.device_of("Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 "
                                  "Firefox/143.0") == "desktop Firefox"
    assert setup_events.device_of(None) == "desktop other"


def test_the_screens_report_what_only_the_browser_sees():
    js = ais.STEPS_JS
    assert "window.dexioSetup = note" in js and "sendBeacon" in js and "/setup/event" in js
    for event in ("hidden", "visible", "left", "copied", "step"):
        assert f'"{event}"' in js
    assert 'dexioSetup("connect_shown", "graph")' in render.APP_JS
    assert 'dexioSetup("ready_shown", "graph")' in render.APP_JS
    assert 'dexioSetup("connect_shown", "agents"' in pages.CONNECT_JS
    # Every event a screen sends is one the server takes.
    for event in ("connect_shown", "ready_shown", "step", "copied", "hidden", "visible", "left"):
        assert event in setup_events.BROWSER
