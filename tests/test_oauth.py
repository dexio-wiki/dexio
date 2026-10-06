"""OAuth end to end, the way Claude or ChatGPT does it: discovery, dynamic client
registration, sign-in and consent (picking a workspace), code exchange with
PKCE, MCP calls with the access token, refresh rotation and disconnecting."""
from __future__ import annotations

import base64
import hashlib
import json
import re
import secrets
from urllib.parse import parse_qs, quote, urlsplit

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, db, oauth  # noqa: E402
from dexio.server.app import get_app  # noqa: E402
from dexio.server.mcp_server import CHANGE_TOOLS  # noqa: E402

BASE = "https://localhost"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
PW = "a-good-long-password-1"


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DEXIO_PUBLIC_URL", BASE)
    a = get_app(str(tmp_path / "o.db"))
    with TestClient(a, base_url=BASE, follow_redirects=False) as live:
        a.state.live = live
        yield a


def person(app, email):
    c = TestClient(app, base_url=BASE, follow_redirects=False)
    r = c.post("/signup", data={"email": email, "password": PW, "confirm_password": PW,
                                "agree": "1", "first_name": email.split("@")[0]})
    assert r.status_code == 303
    return c


def register(app, name="Claude"):
    r = app.state.live.post("/register", json={
        "redirect_uris": [REDIRECT], "client_name": name, "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert r.status_code == 201, r.text
    return r.json()["client_id"]


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()) \
        .decode().rstrip("=")
    return verifier, challenge


def authorize(app, browser, client_id, challenge, state="xyz"):
    r = browser.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": challenge, "code_challenge_method": "S256", "state": state,
        "resource": BASE + "/mcp"})
    assert r.status_code in (302, 303, 307), r.text
    consent = r.headers["location"]
    assert consent.startswith(BASE + "/oauth/consent?req=")
    return urlsplit(consent).path + "?" + urlsplit(consent).query


def consent(browser, consent_path, action="allow", workspace=None):
    page = browser.get(consent_path)
    assert page.status_code == 200 and "wants to read and write" in page.text
    req = parse_qs(urlsplit(consent_path).query)["req"][0]
    m = re.search(r'type="hidden" name="workspace" value="(\w+)"|name="workspace" value="(\w+)" checked',
                  page.text)
    ws = workspace or m.group(1) or m.group(2)
    return browser.post("/oauth/consent", data={"req": req, "workspace": ws, "action": action})


def token(app, **form):
    return app.state.live.post("/token", data=form)


def mcp(app, tok, tool, **args):
    r = app.state.live.post("/mcp", headers={"Authorization": f"Bearer {tok}",
                                             "Accept": "application/json, text/event-stream"},
                            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": tool, "arguments": (
                                      {"agent": "test-agent", **args}
                                      if tool in CHANGE_TOOLS else args)}})
    if r.status_code != 200:
        return {"status": r.status_code}
    res = r.json()["result"]
    text = res["content"][0]["text"]
    return {"error": text} if res.get("isError") else json.loads(text)


def full_flow(app, browser, name="Claude"):
    client_id = register(app, name)
    verifier, challenge = pkce()
    back = consent(browser, authorize(app, browser, client_id, challenge))
    assert back.status_code == 303
    q = parse_qs(urlsplit(back.headers["location"]).query)
    assert back.headers["location"].startswith(REDIRECT) and q["state"] == ["xyz"]
    assert q["iss"] == [BASE]
    r = token(app, grant_type="authorization_code", code=q["code"][0], redirect_uri=REDIRECT,
              client_id=client_id, code_verifier=verifier)
    assert r.status_code == 200, r.text
    return client_id, r.json(), q["code"][0], verifier


def test_discovery_documents_and_the_401_hint(app):
    live = app.state.live
    pr = live.get("/.well-known/oauth-protected-resource/mcp").json()
    # Exact strings: RFC 8414 clients compare the issuer byte for byte.
    assert pr["resource"] == BASE + "/mcp" and pr["authorization_servers"] == [BASE]
    assert live.get("/.well-known/oauth-protected-resource").json()["resource"] == BASE + "/mcp"
    meta = live.get("/.well-known/oauth-authorization-server").json()
    assert meta["issuer"] == BASE
    assert meta["authorization_endpoint"] == BASE + "/authorize"
    assert meta["token_endpoint"] == BASE + "/token"
    assert meta["registration_endpoint"] == BASE + "/register"
    assert "S256" in meta["code_challenge_methods_supported"]
    assert meta["authorization_response_iss_parameter_supported"] is True
    r = live.post("/mcp", json={})
    assert r.status_code == 401
    assert 'resource_metadata="https://localhost/.well-known/oauth-protected-resource/mcp"' \
        in r.headers["www-authenticate"]


def test_full_sign_in_then_tools_work_in_the_chosen_workspace(app):
    ann = person(app, "ann@example.com")
    client_id, tok, code, verifier = full_flow(app, ann)
    assert tok["access_token"].startswith("dxa_") and tok["refresh_token"].startswith("dxr_")
    assert tok["token_type"].lower() == "bearer" and tok["expires_in"] == 3600
    # the workspace came with its wiki, empty
    assert mcp(app, tok["access_token"], "list_pages")["pages"] == []
    assert mcp(app, tok["access_token"], "write_page", path="hello",
               text="# Hello from Claude")["created"]
    assert [p["path"] for p in mcp(app, tok["access_token"], "list_pages")["pages"]] == ["hello"]
    # The code works once.
    again = token(app, grant_type="authorization_code", code=code, redirect_uri=REDIRECT,
                  client_id=client_id, code_verifier=verifier)
    assert again.status_code == 400


def test_wrong_pkce_verifier_is_refused(app):
    ann = person(app, "ann@example.com")
    client_id = register(app)
    _verifier, challenge = pkce()
    back = consent(ann, authorize(app, ann, client_id, challenge))
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    r = token(app, grant_type="authorization_code", code=code, redirect_uri=REDIRECT,
              client_id=client_id, code_verifier=secrets.token_urlsafe(48))
    assert r.status_code == 400


def test_consent_needs_sign_in_and_deny_returns_access_denied(app):
    client_id = register(app)
    _v, challenge = pkce()
    stranger = TestClient(app, base_url=BASE, follow_redirects=False)
    path = authorize(app, stranger, client_id, challenge)
    r = stranger.get(path)
    assert r.status_code == 303 and r.headers["location"].startswith("/login?next=")
    ann = person(app, "ann@example.com")
    back = consent(ann, path, action="deny")
    q = parse_qs(urlsplit(back.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["xyz"] and q["iss"] == [BASE]


def test_library_error_redirects_carry_iss_too(app):
    client_id = register(app)
    r = app.state.live.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT,
        "code_challenge": "x" * 43, "code_challenge_method": "S256", "state": "s1",
        "resource": "https://elsewhere.example/mcp"})
    assert r.status_code in (302, 303, 307)
    q = parse_qs(urlsplit(r.headers["location"]).query)
    assert r.headers["location"].startswith(REDIRECT)
    assert q["error"] and q["state"] == ["s1"] and q["iss"] == [BASE]


def test_a_live_sign_in_counts_as_the_workspace_having_an_agent(app):
    """An empty wiki skips the connect tiles once Claude or ChatGPT is signed in
    to the workspace, and shows them again once it is disconnected."""
    def connected() -> bool:
        return "window.DEXIO_CONNECTED = true;" in ann.get("/").text

    ann = person(app, "ann@example.com")
    assert not connected()
    client_id, _tok, _code, _verifier = full_flow(app, ann)
    assert connected()
    uid = auth.user_by_email(app.state.conn, "ann@example.com")["id"]
    oauth.disconnect(app.state.conn, db.workspaces_for_user(app.state.conn, uid)[0]["id"],
                     client_id)
    assert not connected()


def test_refresh_rotates_and_old_tokens_stop_working(app):
    ann = person(app, "ann@example.com")
    client_id, tok, _c, _v = full_flow(app, ann)
    r = token(app, grant_type="refresh_token", refresh_token=tok["refresh_token"],
              client_id=client_id)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["access_token"] != tok["access_token"]
    assert "pages" in mcp(app, new["access_token"], "list_pages")
    assert mcp(app, tok["access_token"], "list_pages") == {"status": 401}
    reused = token(app, grant_type="refresh_token", refresh_token=tok["refresh_token"],
                   client_id=client_id)
    assert reused.status_code == 400


def test_tokens_stay_inside_their_workspace_and_can_be_disconnected(app):
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    _cid, ann_tok, _c, _v = full_flow(app, ann)
    _cid2, bob_tok, _c2, _v2 = full_flow(app, bob, name="ChatGPT")
    assert mcp(app, ann_tok["access_token"], "write_page", path="secret", text="# Ann")["created"]
    assert "no page" in mcp(app, bob_tok["access_token"], "read_page", path="secret")["error"]
    # A person cannot grant a workspace they are not in.
    client_id = register(app)
    _v3, challenge = pkce()
    path = authorize(app, bob, client_id, challenge)
    ann_ws = ann.get("/api/v1/workspaces").json()["current"]
    assert consent(bob, path, workspace=str(ann_ws)).status_code == 403
    # Disconnect from settings.
    page = ann.get("/settings/agents").text
    assert "Claude" in page and "Signed in" in page
    cid = re.search(r"/settings/apps/([^/]+)/disconnect", page).group(1)
    r = ann.post(f"/settings/apps/{cid}/disconnect")
    assert r.status_code == 303 and "done=disconnected" in r.headers["location"]
    assert "App disconnected." in ann.get(r.headers["location"]).text
    assert ann.post(f"/settings/apps/{cid}/disconnect").status_code == 404
    assert mcp(app, ann_tok["access_token"], "list_pages") == {"status": 401}
    assert "pages" in mcp(app, bob_tok["access_token"], "list_pages")


def test_client_secret_basic_with_the_id_only_in_the_header(app):
    """RFC 6749 section 2.3.1: a client_secret_basic client may send its id only in the
    Authorization header. Smithery does (found 2026-09-28); the MCP library reads
    client_id from the form body alone, so /token answered 401 until the app copied
    the id over."""
    ann = person(app, "ann@example.com")
    r = app.state.live.post("/register", json={
        "redirect_uris": [REDIRECT], "client_name": "Smithery",
        "token_endpoint_auth_method": "client_secret_basic",
        "grant_types": ["authorization_code", "refresh_token"], "response_types": ["code"]})
    assert r.status_code == 201, r.text
    cid, secret = r.json()["client_id"], r.json()["client_secret"]
    verifier, challenge = pkce()
    back = consent(ann, authorize(app, ann, cid, challenge))
    code = parse_qs(urlsplit(back.headers["location"]).query)["code"][0]
    basic = "Basic " + base64.b64encode(f"{quote(cid)}:{quote(secret)}".encode()).decode()
    r = app.state.live.post("/token", headers={"Authorization": basic}, data={
        "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT,
        "code_verifier": verifier})
    assert r.status_code == 200, r.text
    tok = r.json()
    assert mcp(app, tok["access_token"], "list_pages")["pages"] == []
    r = app.state.live.post("/token", headers={"Authorization": basic}, data={
        "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]})
    assert r.status_code == 200, r.text
    wrong = "Basic " + base64.b64encode(f"{cid}:not-the-secret".encode()).decode()
    r = app.state.live.post("/token", headers={"Authorization": wrong}, data={
        "grant_type": "refresh_token", "refresh_token": r.json()["refresh_token"]})
    assert r.status_code == 401


def test_a_sign_in_reads_what_is_shared_with_its_person_once_allowed(app, monkeypatch):
    """Keys act as their person (Forrest, 2026-10-06), app sign-ins too: Bob's
    Claude reads the folder Ann shared with him once he allows it, and only that."""
    from dexio.server import mail
    links: list[str] = []
    monkeypatch.setattr(mail, "share",
                        lambda *a, **k: links.append(" ".join(map(str, [*a, *k.values()]))) or True)
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    _cid, ann_tok, _c, _v = full_flow(app, ann)
    for path in ("notes/plan", "secret"):
        assert mcp(app, ann_tok["access_token"], "write_page", path=path, text=f"# {path}\n")
    ann_ws = ann.get("/api/v1/workspaces").json()["current"]
    r = ann.post(f"/api/v1/share?w={ann_ws}", json={"kind": "folder", "path": "notes",
                                                    "email": "bob@example.com", "role": "viewer"})
    assert r.status_code == 200, r.text
    code = re.search(r"/s/(dxs_[A-Za-z0-9_\-]+)", links[-1]).group(1)
    assert bob.get(f"/s/{code}").status_code == 303
    _cid2, bob_tok, _c2, _v2 = full_flow(app, bob)
    tok = bob_tok["access_token"]
    assert "reads only its own workspace" in mcp(app, tok, "list_pages", workspace=ann_ws)["error"]
    page = bob.get("/settings/agents").text
    cid = re.search(r"/settings/apps/([^/?]+)/shared/on", page).group(1)
    # Ann has no sign-in of Bob's app in his workspace to turn on
    assert ann.post(f"/settings/apps/{cid}/shared/on").status_code in (403, 404)
    r = bob.post(f"/settings/apps/{cid}/shared/on")
    assert r.status_code == 303 and "done=shared_on" in r.headers["location"]
    got = mcp(app, tok, "list_pages", workspace=ann_ws)
    assert [p["path"] for p in got["pages"]] == ["notes/plan"] and got["read_only"] is True
    assert "no page" in mcp(app, tok, "read_page", path="secret", workspace=ann_ws)["error"]
    # disconnecting clears it, so a fresh sign-in starts at its own workspace again
    assert bob.post(f"/settings/apps/{cid}/disconnect").status_code == 303
    _cid3, again, _c3, _v3 = full_flow(app, bob)
    assert "reads only" in mcp(app, again["access_token"], "list_pages", workspace=ann_ws)["error"]
