"""Password reset and emailed invites, with SES replaced by a list of messages."""
from __future__ import annotations

import re
import sys
import time
import types

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, mail  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

PW = "a-good-long-password-1"
NEW = "a-brand-new-password-2"


@pytest.fixture()
def outbox(monkeypatch):
    sent = []
    monkeypatch.setenv("DEXIO_MAIL", "ses")
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html_body=None: sent.append(
        {"to": to, "subject": subject, "text": text, "html": html_body}) or True)
    return sent


@pytest.fixture()
def app(tmp_path, monkeypatch, outbox):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return get_app(str(tmp_path / "e.db"))


def client(app):
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def person(app, email):
    c = client(app)
    c.post("/signup", data={"email": email, "password": PW, "confirm_password": PW, "agree": "1",
                            "first_name": email.split("@")[0]})
    return c


def reset_link(message):
    return re.search(r"https://testserver(/reset\?token=\S+)", message["text"]).group(1)


def test_reset_flow_sets_a_new_password_and_signs_everyone_out(app, outbox):
    other_browser = person(app, "ann@example.com")
    assert other_browser.get("/api/v1/workspaces").status_code == 200
    stranger = client(app)
    assert "Forgot your password?" in stranger.get("/login").text
    r = stranger.post("/forgot", data={"email": "ann@example.com"})
    assert r.status_code == 200 and "If an account exists" in r.text
    assert len(outbox) == 1 and outbox[0]["to"] == "ann@example.com"
    assert outbox[0]["subject"] == "Reset your Dexio password"
    path = reset_link(outbox[0])
    assert f'href="https://testserver{path}"' in outbox[0]["html"]     # the button, same link
    page = stranger.get(path)
    assert page.status_code == 200 and "ann@example.com" in page.text
    token = path.split("token=")[1]
    r = stranger.post("/reset", data={"token": token, "new_password": NEW,
                                      "confirm_password": NEW})
    assert r.status_code == 303 and r.cookies.get(auth.SESSION_COOKIE)
    # New password works, old one does not, the other browser is signed out.
    assert client(app).post("/login", data={"email": "ann@example.com", "password": NEW}) \
        .status_code == 303
    assert client(app).post("/login", data={"email": "ann@example.com", "password": PW}) \
        .status_code == 401
    assert other_browser.get("/api/v1/workspaces").status_code == 401
    # The link works once.
    assert stranger.get(path).status_code == 400


def test_unknown_email_gets_the_same_answer_and_no_mail(app, outbox):
    person(app, "ann@example.com")
    known = client(app).post("/forgot", data={"email": "ann@example.com"}).text
    unknown = client(app).post("/forgot", data={"email": "nobody@example.com"}).text
    assert known.replace("ann@example.com", "") == unknown.replace("nobody@example.com", "")
    assert [m["to"] for m in outbox] == ["ann@example.com"]


def test_reset_validation_expiry_and_supersession(app, outbox, monkeypatch):
    person(app, "ann@example.com")
    c = client(app)
    c.post("/forgot", data={"email": "ann@example.com"})
    first = reset_link(outbox[-1]).split("token=")[1]
    c.post("/forgot", data={"email": "ann@example.com"})
    second = reset_link(outbox[-1]).split("token=")[1]
    assert c.get(f"/reset?token={first}").status_code == 400      # replaced by the newer one
    r = c.post("/reset", data={"token": second, "new_password": NEW, "confirm_password": "x" + NEW})
    assert r.status_code == 400 and "do not match" in r.text
    r = c.post("/reset", data={"token": second, "new_password": "short", "confirm_password": "short"})
    assert r.status_code == 400 and "at least 12" in r.text
    monkeypatch.setattr(time, "time", lambda: 10 ** 10)             # far future: expired
    assert c.get(f"/reset?token={second}").status_code == 400


def test_reset_requests_are_rate_limited(app, outbox):
    person(app, "ann@example.com")
    codes = [client(app).post("/forgot", data={"email": "ann@example.com"}).status_code
             for _ in range(5)]
    assert codes[:3] == [200, 200, 200] and 429 in codes[3:]
    assert len(outbox) == 3


def test_invites_can_be_emailed(app, outbox):
    ann = person(app, "ann@example.com")
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='team'")
    r = ann.post("/settings/invite", data={"email": "bob@example.com"})
    assert r.status_code == 303 and r.headers["location"].endswith("done=invited")
    page = ann.get(r.headers["location"]).text
    assert "Invite sent." in page and "/invite/dxi_" not in page     # the link is only in the email
    msg = outbox[-1]
    assert msg["to"] == "bob@example.com"
    assert msg["subject"] == "ann invited you to ann's Workspace on Dexio"
    assert "ann (ann@example.com) invited you" in msg["text"]
    assert "<b>ann</b> (ann@example.com) invited you" in msg["html"]
    assert "ann&#x27;s Workspace" in msg["html"]
    code = re.search(r"/invite/(dxi_\S+)", msg["text"]).group(1)
    assert f"/invite/{code}\"" in msg["html"]
    bob = person(app, "bob@example.com")
    assert bob.get(f"/invite/{code}").status_code == 303
    # An email is required; nothing is sent and no link is shown without one.
    n = len(outbox)
    for data in ({}, {"email": "not an address"}):
        r = ann.post("/settings/invite", data=data)
        assert r.status_code == 400 and "Enter the email address" in r.text
        assert "/invite/dxi_" not in r.text and len(outbox) == n
    # Someone already in the workspace is not invited again.
    r = ann.post("/settings/invite", data={"email": "ANN@example.com"})
    assert r.status_code == 400 and "already a member" in r.text and len(outbox) == n


def test_an_invite_that_cannot_be_sent_is_withdrawn(app, monkeypatch):
    ann = person(app, "ann@example.com")
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='team'")
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html_body=None: False)
    r = ann.post("/settings/invite", data={"email": "bob@example.com"})
    assert r.status_code == 502 and "could not send the invite to bob@example.com" in r.text
    assert "/invite/dxi_" not in r.text
    assert app.state.conn.execute("SELECT COUNT(*) FROM invites").fetchone()[0] == 0


def test_mail_off_means_logged_not_sent(monkeypatch):
    monkeypatch.delenv("DEXIO_MAIL", raising=False)
    assert mail.send("x@example.com", "s", "t") is False


def test_names_are_escaped_in_the_html(monkeypatch):
    got = {}
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html_body=None: got.update(
        subject=subject, text=text, html=html_body) or True)
    mail.invite("bob@example.com", 'Eve <img src=x onerror="alert(1)">', "Acme & <i>Co</i>",
                "https://app.dexio.wiki/invite/dxi_abc", "eve@example.com")
    page = got["html"]
    assert "<img src=x" not in page and "<i>Co</i>" not in page
    assert "Eve &lt;img src=x onerror=&quot;alert(1)&quot;&gt;" in page
    assert "Acme &amp; &lt;i&gt;Co&lt;/i&gt;" in page
    assert got["text"].startswith('Eve <img src=x onerror="alert(1)"> (eve@example.com) invited')


def test_an_inviter_without_a_name_is_not_shown_twice(monkeypatch):
    got = {}
    monkeypatch.setattr(mail, "send", lambda to, subject, text, html_body=None: got.update(
        text=text, html=html_body) or True)
    mail.invite("bob@example.com", "ann@example.com", "Acme", "https://x/invite/dxi_a",
                "Ann@Example.com")
    assert got["text"].startswith('ann@example.com invited you')
    assert "<b>ann@example.com</b> invited you" in got["html"]


def test_ses_gets_text_and_html(monkeypatch):
    calls = []

    class Client:
        def send_email(self, **kw):
            calls.append(kw)

    monkeypatch.setitem(sys.modules, "boto3", types.SimpleNamespace(
        client=lambda *a, **kw: Client()))
    monkeypatch.setenv("DEXIO_MAIL", "ses")
    assert mail.password_reset("ann@example.com", "https://app.dexio.wiki/reset?token=t&x=1")
    body = calls[0]["Content"]["Simple"]["Body"]
    assert "https://app.dexio.wiki/reset?token=t&x=1" in body["Text"]["Data"]
    page = body["Html"]["Data"]
    assert 'href="https://app.dexio.wiki/reset?token=t&amp;x=1"' in page
    assert mail.ICON in page and "Reset your password" in page
    assert calls[0]["ReplyToAddresses"] == [mail.reply_to()]
