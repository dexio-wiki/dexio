"""Roles, removing people, leaving and deleting a workspace (membership.py)."""
from __future__ import annotations


import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, db, files  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import (browser, invite_code, mcp, signup,  # noqa: E402
                             token_from_connect, ws_id)


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    files.set_store(files.FolderStore(tmp_path / "store"))
    a = get_app(str(tmp_path / "m.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        a.state.store = tmp_path / "store"
        yield a
    files.set_store(None)


def refused(app, key: str) -> bool:
    """The key no longer opens the MCP endpoint."""
    return app.state.live.post("/mcp", headers={"Authorization": f"Bearer {key}"},
                               json={}).status_code == 401


def uid(app, email):
    return auth.user_by_email(app.state.conn, email)["id"]


def team(app, monkeypatch):
    """Ann's workspace from signup, moved to Team; Bob has joined it and connected
    an agent to it. Bob also has his own workspace from signup."""
    ann, bob = browser(app), browser(app)
    signup(ann, "ann@example.com")
    signup(bob, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    with app.state.conn as conn:
        conn.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws_id(app, ws),))
    code = invite_code(ann, monkeypatch, "bob@example.com")
    assert bob.get(f"/invite/{code}").status_code == 303
    bob_key = token_from_connect(bob)                 # the invite made Ann's workspace current
    assert "pages" in mcp(app, bob_key, "list_pages")
    return ann, bob, ws, bob_key


def roles(app, ws):
    return {m["email"]: m["role"] for m in db.members(app.state.conn, ws_id(app, ws))}


def test_owner_changes_roles_and_a_workspace_keeps_an_owner(app, monkeypatch):
    ann, bob, ws, _ = team(app, monkeypatch)
    bob_id, ann_id = uid(app, "bob@example.com"), uid(app, "ann@example.com")
    page = ann.get(f"/settings/members?w={ws}").text
    assert f'action="/settings/members/{bob_id}/role?w={ws}"' in page and "Make owner" in page
    assert page.count('<details class="menu">') == 1              # Bob's row only
    assert f"/settings/members/{ann_id}/" not in page             # nothing on your own row
    # A member cannot change roles or remove anyone, and sees no buttons.
    assert "Make owner" not in bob.get(f"/settings/members?w={ws}").text
    assert bob.post(f"/settings/members/{bob_id}/role?w={ws}",
                    data={"role": "owner"}).status_code == 403
    assert bob.post(f"/settings/members/{ann_id}/remove?w={ws}").status_code == 403

    r = ann.post(f"/settings/members/{bob_id}/role?w={ws}", data={"role": "owner"})
    assert r.status_code == 303 and r.headers["location"] == f"/settings/members?w={ws}&done=role"
    assert roles(app, ws) == {"ann@example.com": "owner", "bob@example.com": "owner"}
    # Nobody changes their own role, even with another owner there to take over.
    r = ann.post(f"/settings/members/{ann_id}/role?w={ws}", data={"role": "member"})
    assert r.status_code == 400 and "your own role" in r.text
    assert roles(app, ws)["ann@example.com"] == "owner"
    # Another owner can.
    assert bob.post(f"/settings/members/{ann_id}/role?w={ws}",
                    data={"role": "member"}).status_code == 303
    assert roles(app, ws) == {"ann@example.com": "member", "bob@example.com": "owner"}
    assert bob.post(f"/settings/members/{ann_id}/role?w={ws}",
                    data={"role": "admin"}).status_code == 400
    # The last owner is protected in the rules too, not only by the self rule.
    from dexio.server import membership
    with pytest.raises(ValueError, match="needs an owner"):
        membership.set_role(app.state.conn, ws_id(app, ws), bob_id, "member", by=ann_id)


def test_owner_removes_a_member_and_their_agents_lose_access(app, monkeypatch):
    ann, bob, ws, bob_key = team(app, monkeypatch)
    bob_id = uid(app, "bob@example.com")
    # Bob's unused invite goes with him.
    with app.state.conn as conn:
        conn.execute("UPDATE memberships SET role='owner' WHERE user_id=?", (bob_id,))
    invite_code(bob, monkeypatch, "cat@example.com", w=ws)
    with app.state.conn as conn:
        conn.execute("UPDATE memberships SET role='member' WHERE user_id=?", (bob_id,))
    assert ann.post(f"/settings/members/{uid(app, 'ann@example.com')}/remove?w={ws}") \
        .status_code == 400                                      # not yourself
    r = ann.post(f"/settings/members/{bob_id}/remove?w={ws}")
    assert r.status_code == 303 and "done=removed" in r.headers["location"]
    assert "Removed from the workspace" in ann.get(r.headers["location"]).text
    assert "bob@example.com" not in roles(app, ws)
    assert refused(app, bob_key)
    assert ws not in [w["id"] for w in bob.get("/api/v1/workspaces").json()["workspaces"]]
    assert app.state.conn.execute("SELECT COUNT(*) FROM invites WHERE created_by=? AND"
                                  " used_at IS NULL", (bob_id,)).fetchone()[0] == 0
    assert ann.post(f"/settings/members/{bob_id}/remove?w={ws}").status_code == 400
    # Cross-site posts are refused.
    assert ann.post(f"/settings/members/{bob_id}/role?w={ws}", data={"role": "owner"},
                    headers={"Origin": "https://evil.example"}).status_code == 403


def test_leaving(app, monkeypatch):
    ann, bob, ws, bob_key = team(app, monkeypatch)
    # The only owner, with others still there, has to hand over first.
    page = ann.get(f"/settings?w={ws}").text
    assert "only owner" in page and 'action="/settings/leave' not in page
    r = ann.post(f"/settings/leave?w={ws}")
    assert r.status_code == 400 and "only owner" in r.text
    # A member leaves; their agent key stops working.
    assert f'action="/settings/leave?w={ws}"' in bob.get(f"/settings?w={ws}").text
    r = bob.post(f"/settings/leave?w={ws}")
    # He lands on General in the workspace he still has, which the message names.
    own = db.workspaces_for_user(app.state.conn, uid(app, "bob@example.com"))
    assert [w["handle"] for w in own] != [ws] and len(own) == 1
    assert r.status_code == 303
    assert r.headers["location"] == f"/settings?w={own[0]['handle']}&done=left"
    page = bob.get(r.headers["location"]).text
    assert "You left the workspace. You&#x27;re now in " + own[0]["name"].replace("'", "&#x27;") \
        in page
    assert refused(app, bob_key)
    assert "bob@example.com" not in roles(app, ws)
    # Now alone, Ann cannot leave: she deletes instead, and the page says so.
    page = ann.get(f"/settings?w={ws}").text
    assert "Leave this workspace" not in page and "Delete this workspace" in page
    assert ann.post(f"/settings/leave?w={ws}").status_code == 400


def test_deleting_a_workspace(app, monkeypatch):
    ann, bob, ws, bob_key = team(app, monkeypatch)
    wid = ws_id(app, ws)
    ann_key = token_from_connect(ann)
    assert bob.post(f"/settings/delete?w={ws}", data={"confirm_name": "x"}).status_code == 403
    page = ann.get(f"/settings?w={ws}").text
    assert "1 other member loses access" in page
    # A live subscription blocks it until the plan is cancelled.
    with app.state.conn as conn:
        conn.execute("UPDATE workspaces SET stripe_subscription='sub_1' WHERE id=?",
                     (ws_id(app, ws),))
    assert "Cancel the plan first, under Plan" in ann.get(f"/settings?w={ws}").text
    name = db.workspace(app.state.conn, ws_id(app, ws))["name"]
    r = ann.post(f"/settings/delete?w={ws}", data={"confirm_name": name})
    assert r.status_code == 400 and db.workspace(app.state.conn, ws_id(app, ws))
    with app.state.conn as conn:
        conn.execute("UPDATE workspaces SET stripe_subscription=NULL, plan='free' WHERE id=?",
                     (ws_id(app, ws),))
    # The name has to be typed back exactly.
    r = ann.post(f"/settings/delete?w={ws}", data={"confirm_name": name.upper() + "x"})
    assert r.status_code == 400 and "exactly" in r.text
    assert db.workspace(app.state.conn, ws_id(app, ws))
    r = app.state.live.put("/api/v1/files", params={"path": "raw/a.txt"},
                           content=b"hi", headers={"Authorization": f"Bearer {ann_key}"})
    stored = list(app.state.store.rglob("*"))
    r = ann.post(f"/settings/delete?w={ws}", data={"confirm_name": f"  {name} "})
    # With no workspace left she lands on Profile, which needs none.
    assert r.status_code == 303 and r.headers["location"] == "/settings/profile?done=deleted"
    page = ann.get(r.headers["location"]).text
    assert "Workspace deleted." in page and "now in" not in page
    assert db.workspace(app.state.conn, wid) is None
    assert refused(app, ann_key)
    assert refused(app, bob_key)
    assert not [p for p in app.state.store.rglob("*") if p.is_file() and p in stored]
    # Bob still has his own workspace; Ann has none, and opening Dexio makes her one.
    assert len(bob.get("/api/v1/workspaces").json()["workspaces"]) == 1
    assert db.workspaces_for_user(app.state.conn, uid(app, "ann@example.com")) == []
    assert "/settings/start" in ann.get("/", headers={"accept": "text/html"}).headers["location"]


# ---- pending invites --------------------------------------------------------
def outbox(monkeypatch, ok: bool = True) -> list[tuple[str, str]]:
    """Capture (to, link) for every invite email the app sends."""
    from dexio.server import mail
    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(mail, "invite",
                        lambda to, inviter, ws, link, *_: sent.append((to, link)) or ok)
    return sent


def code_of(link: str) -> str:
    return link.rsplit("/invite/", 1)[1]


def test_pending_invites_show_in_people_until_they_join(app, monkeypatch):
    ann, bob, ws, _ = team(app, monkeypatch)
    sent = outbox(monkeypatch)
    r = ann.post(f"/settings/invite?w={ws}", data={"email": "cat@example.com"})
    assert r.status_code == 303 and r.headers["location"].endswith("done=invited")
    page = ann.get(f"/settings/members?w={ws}").text
    assert 'cat@example.com <span class="tag">pending</span>' in page
    assert "2 members, 1 pending" in page and "Resend invite" in page and "Cancel invite" in page
    # Members see who is pending, without the owner's menu.
    page = bob.get(f"/settings/members?w={ws}").text
    assert 'cat@example.com <span class="tag">pending</span>' in page and "Resend invite" not in page
    # Once they join, they are a member and no longer pending.
    cat = browser(app)
    signup(cat, "cat@example.com")
    assert cat.get(f"/invite/{code_of(sent[-1][1])}").status_code == 303
    page = ann.get(f"/settings/members?w={ws}").text
    assert '<span class="tag">pending</span>' not in page and "3 members" in page


def test_resending_replaces_the_link_and_cancelling_withdraws_it(app, monkeypatch):
    ann, bob, ws, _ = team(app, monkeypatch)
    sent = outbox(monkeypatch)
    ann.post(f"/settings/invite?w={ws}", data={"email": "cat@example.com"})
    first = code_of(sent[-1][1])
    # Inviting the same address again sends a fresh link; the old one stops working.
    r = ann.post(f"/settings/invite?w={ws}", data={"email": "Cat@Example.com"})
    assert r.status_code == 303 and r.headers["location"].endswith("done=resent")
    second = code_of(sent[-1][1])
    invites = db.pending_invites(app.state.conn, ws_id(app, ws))
    assert len(invites) == 1 and second != first
    # Resend from the menu: another fresh link.
    iid = invites[0]["id"]
    assert bob.post(f"/settings/invites/{iid}/resend?w={ws}").status_code == 403
    r = ann.post(f"/settings/invites/{iid}/resend?w={ws}")
    assert r.status_code == 303 and "Invite sent again." in ann.get(r.headers["location"]).text
    third = code_of(sent[-1][1])
    assert sent[-1][0].lower() == "cat@example.com"
    # A resend that cannot be sent leaves the last link working.
    iid = db.pending_invites(app.state.conn, ws_id(app, ws))[0]["id"]
    outbox(monkeypatch, ok=False)
    assert ann.post(f"/settings/invites/{iid}/resend?w={ws}").status_code == 502
    assert [i["id"] for i in db.pending_invites(app.state.conn, ws_id(app, ws))] == [iid]
    # Cancel withdraws it.
    assert bob.post(f"/settings/invites/{iid}/cancel?w={ws}").status_code == 403
    r = ann.post(f"/settings/invites/{iid}/cancel?w={ws}")
    assert r.status_code == 303 and "Invite cancelled." in ann.get(r.headers["location"]).text
    assert db.pending_invites(app.state.conn, ws_id(app, ws)) == []
    assert ann.post(f"/settings/invites/{iid}/cancel?w={ws}").status_code == 404
    cat = browser(app)
    signup(cat, "cat@example.com")
    for code in (first, second, third):
        assert cat.get(f"/invite/{code}").status_code == 400


# ---- account menu in the graph view ---------------------------------------
def test_account_menu_names_the_person_and_leads_to_settings(app, monkeypatch):
    ann, bob, ws, _ = team(app, monkeypatch)
    with app.state.conn as conn:
        conn.execute("UPDATE users SET first_name='Ann', last_name='Lee'"
                     " WHERE email='ann@example.com'")
        # sign-up asks for a first name since 11b13c6; Bob stands for an account without one
        conn.execute("UPDATE users SET first_name='', last_name=''"
                     " WHERE email='bob@example.com'")
    page = ann.get(f"/?w={ws}", headers={"accept": "text/html"}).text
    assert 'id="acct-button"' in page and '<span class="avatar" aria-hidden="true">AL</span>' in page
    assert "<b>Ann Lee</b><span>ann@example.com</span>" in page
    for href, label in (("/settings", "Settings"), ("/settings/members", "Invite people"),
                        ("/settings/agents?connect", "Connect an agent"),
                        ("/settings/profile", "Profile"),
                        ("/settings/appearance", "Appearance"),
                        ("/settings/help", "Help")):
        assert f'href="{href}">' in page and f">{label}</span>" in page, label
    assert '<form method="post" action="/logout">' in page
    # the workspace menu leads to the workspace's settings too
    assert f'data-key="settings" href="/settings?w={ws}">' in page and ">Settings for " in page
    # a member sees Members, not Invite people; no name set shows the email's initial
    page = bob.get(f"/?w={ws}", headers={"accept": "text/html"}).text
    assert ">Members</span>" in page and ">Invite people</span>" not in page
    assert '<span class="avatar" aria-hidden="true">B</span>' in page
    assert "<b>bob@example.com</b></span>" in page          # the email is not shown twice
