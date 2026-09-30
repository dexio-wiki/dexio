"""Agent sign-in end to end, the way an agent told "look at dexio.wiki and log me
in" does it: start a device login, show the person the link, poll while they sign
up and allow it, then call MCP with the token it collected."""
from __future__ import annotations

import html
import json
import re
from urllib.parse import parse_qs, quote, unquote, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import device  # noqa: E402
from dexio.server.app import get_app  # noqa: E402
from dexio.server.mcp_server import CHANGE_TOOLS  # noqa: E402

BASE = "https://localhost"
PW = "a-good-long-password-1"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEXIO_PUBLIC_URL", BASE)
    a = get_app(str(tmp_path / "d.db"))
    with TestClient(a, base_url=BASE, follow_redirects=False) as live:
        a.state.live = live
        yield a


def browser(app):
    return TestClient(app, base_url=BASE, follow_redirects=False)


def signup(app, email, next_url=None):
    c = browser(app)
    data = {"email": email, "password": PW, "confirm_password": PW, "agree": "1",
            "first_name": email.split("@")[0]}
    if next_url:
        data["next"] = next_url
    r = c.post("/signup", data=data)
    assert r.status_code == 303
    return c, r


def start(app, **body):
    r = app.state.live.post("/api/v1/device/code", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def poll(app, device_code):
    return app.state.live.post("/api/v1/device/token", json={"device_code": device_code})


def mcp(app, tok, tool, **args):
    r = app.state.live.post("/mcp", headers={"Authorization": f"Bearer {tok}",
                                             "Accept": "application/json, text/event-stream"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": (
                                      {"agent": "test-agent", **args}
                                      if tool in CHANGE_TOOLS else args)}})
    assert r.status_code == 200, r.text
    res = r.json()["result"]
    return json.loads(res["content"][0]["text"])


def forget_last_poll(app):
    # Tests poll back to back; the server would call that too fast.
    with app.state.conn:
        app.state.conn.execute("UPDATE device_logins SET last_poll=NULL")


def test_new_person_signs_up_and_the_agent_collects_a_working_token(app):
    got = start(app, client_name="Claude Code on test-mac")
    assert re.fullmatch(r"[BCDFGHJKLMNPQRSTVWXZ]{4}-[BCDFGHJKLMNPQRSTVWXZ]{4}", got["user_code"])
    assert got["verification_uri"] == BASE + "/device"
    link = got["verification_uri_complete"]
    assert link == f"{BASE}/device?code={got['user_code']}"
    assert got["interval"] == 5 and got["expires_in"] == 1800
    assert link in got["message"] and "Claude Code on test-mac" in got["message"]

    r = poll(app, got["device_code"])
    assert r.status_code == 400 and r.json()["error"] == "authorization_pending"
    r = poll(app, got["device_code"])
    assert r.json()["error"] == "slow_down"

    # The person opens the link with no session: sign-in page, told why, with a
    # signup link that comes back here.
    c = browser(app)
    r = c.get(urlsplit(link).path + "?" + urlsplit(link).query)
    assert r.status_code == 303
    login_url = r.headers["location"]
    assert login_url.startswith("/login?next=")
    next_url = unquote(parse_qs(urlsplit(login_url).query)["next"][0])
    assert next_url == f"/device?code={got['user_code']}"
    page = c.get(login_url)
    assert "Your agent is waiting to connect to Dexio" in page.text
    assert f'/signup?next=/device%3Fcode%3D{got["user_code"]}' in page.text
    assert "Your agent is waiting" in c.get(f"/signup?next={next_url}").text

    # New account: signup returns to the approval page.
    c, r = signup(app, "new@example.com", next_url)
    assert r.headers["location"] == "/joined?next=" + quote(next_url, safe="")  # then next_url
    page = c.get(next_url)
    assert page.status_code == 200
    assert "Claude Code on test-mac" in page.text and got["user_code"] in page.text
    ws = re.search(r'name="workspace" value="([a-z0-9]+)"', page.text)   # one workspace: no menu
    assert "<select" not in page.text
    r = c.post("/device", data={"code": got["user_code"], "workspace": ws.group(1),
                                "action": "allow"})
    assert r.status_code == 200 and "Agent connected" in r.text

    forget_last_poll(app)
    r = poll(app, got["device_code"])
    assert r.status_code == 200, r.text
    assert r.headers["cache-control"] == "no-store"
    body = r.json()
    tok = body["access_token"]
    assert tok.startswith("dxk_") and body["token_type"] == "Bearer"
    assert body["mcp_url"] == BASE + "/mcp"
    assert body["account"] == "new@example.com"
    assert body["workspace"] == {"id": ws.group(1), "name": "new's Workspace"}
    assert "wikis" not in body        # a workspace has one wiki, so there is none to list

    # Collected once.
    forget_last_poll(app)
    assert poll(app, got["device_code"]).json()["error"] == "invalid_grant"

    # The token works over MCP and shows up in Settings under the agent's name.
    # The new workspace came with its wiki, empty, so the agent can write at once.
    assert mcp(app, tok, "list_pages")["pages"] == []
    assert "error" not in mcp(app, tok, "write_page", path="hello", text="# Hello\n")
    assert [p["path"] for p in mcp(app, tok, "list_pages")["pages"]] == ["hello"]
    assert "Claude Code on test-mac" in c.get("/settings/agents").text


def test_signed_in_person_goes_straight_to_the_approval_page(app):
    c, _ = signup(app, "me@example.com")
    got = start(app, client_name="Hermes")
    r = c.get("/device", params={"code": got["user_code"].replace("-", "").lower()})
    assert r.status_code == 200 and got["user_code"] in r.text


def test_deny_tells_the_agent(app):
    c, _ = signup(app, "me@example.com")
    got = start(app)
    assert "An agent" in c.get("/device", params={"code": got["user_code"]}).text
    r = c.post("/device", data={"code": got["user_code"], "action": "deny"})
    assert r.status_code == 200 and "not given access" in r.text
    assert poll(app, got["device_code"]).json()["error"] == "access_denied"
    # And the code cannot be allowed afterwards.
    r = c.post("/device", data={"code": got["user_code"], "workspace": "1", "action": "allow"})
    assert r.status_code == 400


def test_expired_code(app):
    c, _ = signup(app, "me@example.com")
    got = start(app)
    with app.state.conn:
        app.state.conn.execute("UPDATE device_logins SET expires_at=0")
    assert poll(app, got["device_code"]).json()["error"] == "expired_token"
    r = c.get("/device", params={"code": got["user_code"]})
    assert r.status_code == 400 and "expired" in r.text


def test_cannot_hand_an_agent_someone_elses_workspace(app):
    signup(app, "owner@example.com")
    c, _ = signup(app, "other@example.com")
    got = start(app)
    mine = re.search(r'name="workspace" value="([a-z0-9]+)"',
                     c.get("/device", params={"code": got["user_code"]}).text).group(1)
    theirs = app.state.conn.execute("SELECT handle FROM workspaces WHERE handle<>?",
                                    (mine,)).fetchone()["handle"]
    r = c.post("/device", data={"code": got["user_code"], "workspace": str(theirs),
                                "action": "allow"})
    assert r.status_code == 403
    assert poll(app, got["device_code"]).json()["error"] == "authorization_pending"


def test_leaving_the_workspace_before_collection_denies(app):
    c, _ = signup(app, "me@example.com")
    got = start(app)
    ws = re.search(r'name="workspace" value="([a-z0-9]+)"',
                   c.get("/device", params={"code": got["user_code"]}).text).group(1)
    c.post("/device", data={"code": got["user_code"], "workspace": ws, "action": "allow"})
    with app.state.conn:
        app.state.conn.execute("DELETE FROM memberships")
    assert poll(app, got["device_code"]).json()["error"] == "access_denied"


def test_wrong_code_and_the_manual_entry_page(app):
    c, _ = signup(app, "me@example.com")
    r = c.get("/device")
    assert r.status_code == 200 and "Enter the code your agent showed you" in r.text
    r = c.get("/device", params={"code": "BCDF-GHJK"})
    assert r.status_code == 400 and "wrong, expired or already used" in r.text
    assert poll(app, "dxd_nope").json()["error"] == "invalid_grant"
    assert poll(app, "").json()["error"] == "invalid_grant"


def test_form_encoded_requests_work_too(app):
    r = app.state.live.post("/api/v1/device/code", data={"client_name": "curl"})
    assert r.status_code == 200 and "curl" in r.json()["message"]
    r = app.state.live.post("/api/v1/device/code")
    assert r.status_code == 200 and "An agent" in r.json()["message"]
    code = r.json()["device_code"]
    r = app.state.live.post("/api/v1/device/token", data={"device_code": code,
                            "grant_type": "urn:ietf:params:oauth:grant-type:device_code"})
    assert r.json()["error"] == "authorization_pending"


def test_cross_site_approval_is_refused(app):
    c, _ = signup(app, "me@example.com")
    got = start(app)
    r = c.post("/device", data={"code": got["user_code"], "workspace": "1", "action": "allow"},
               headers={"Origin": "https://evil.example"})
    assert r.status_code == 403


def test_client_name_is_cleaned():
    assert device.clean_name("  Claude\n\tCode\x00 ") == "Claude Code"
    assert device.clean_name("") == "An agent"
    assert len(device.clean_name("x" * 500)) == 60
    assert device.pretty("bcdf ghjk") == "BCDF-GHJK"
