"""Billing: checkout, portal, signed webhooks moving a workspace between plans, and
seats following membership. Stripe is replaced by a fake that records calls."""
from __future__ import annotations

import json
import time

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, billing, db  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import invite_code, ws_id  # noqa: E402

PW = "a-good-long-password-1"
WHSEC = "whsec_test_secret"


class FakeStripe:
    def __init__(self):
        self.calls = []
        self.subs = {}
        self.decline = False

    def __call__(self, method, path, params=None):
        params = params or {}
        self.calls.append((method, path, dict(params)))
        if path == "/v1/prices":
            lookup = params["lookup_keys[]"]
            return {"data": [{"id": f"price_{lookup}", "lookup_key": lookup}]}
        if path == "/v1/customers":
            return {"id": "cus_1"}
        if path == "/v1/checkout/sessions":
            return {"id": "cs_1", "url": "https://checkout.stripe.com/c/pay/cs_1"}
        if path == "/v1/billing_portal/sessions":
            return {"url": "https://billing.stripe.com/p/session/1"}
        if path.startswith("/v1/subscriptions/") and method == "GET":
            return self.subs[path.rsplit("/", 1)[1]]
        if path.startswith("/v1/subscriptions/") and method == "POST":
            sub = self.subs[path.rsplit("/", 1)[1]]
            if self.decline:           # pending_if_incomplete: the update waits on a payment
                return {**sub, "pending_update": {"expires_at": 0}}
            if "items[0][price]" in params:
                lookup = params["items[0][price]"].removeprefix("price_")
                sub["items"]["data"][0]["price"] = {
                    "id": params["items[0][price]"], "lookup_key": lookup,
                    "recurring": {"interval": "month"}}
            if "items[0][quantity]" in params:
                sub["items"]["data"][0]["quantity"] = int(params["items[0][quantity]"])
            if "cancel_at_period_end" in params:
                sub["cancel_at_period_end"] = params["cancel_at_period_end"] == "true"
            if "cancel_at" in params:
                sub["cancel_at"] = int(params["cancel_at"]) if params["cancel_at"] else None
            return sub
        raise AssertionError(f"unexpected Stripe call {method} {path}")

    def sub(self, sub_id, ws, status="active", plan="team", interval="month", qty=1,
            period_end=None, cancel_at=None, cancel_at_period_end=False):
        lookup = f"{plan}_{'monthly' if interval == 'month' else 'yearly'}"
        self.subs[sub_id] = {
            "id": sub_id, "customer": "cus_1", "status": status,
            "metadata": {"workspace_id": str(ws)},
            "cancel_at": cancel_at, "cancel_at_period_end": cancel_at_period_end,
            "items": {"data": [{"id": "si_1", "quantity": qty, "price": {
                "id": f"price_{lookup}", "lookup_key": lookup,
                "recurring": {"interval": interval}},
                "current_period_end": period_end}]}}


@pytest.fixture()
def fake(monkeypatch):
    f = FakeStripe()
    monkeypatch.setattr(billing, "call", f)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WHSEC)
    return f


@pytest.fixture()
def app(tmp_path, monkeypatch, fake):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    return get_app(str(tmp_path / "b.db"))


def person(app, email):
    c = TestClient(app, base_url="https://testserver", follow_redirects=False)
    c.post("/signup", data={"email": email, "password": PW, "confirm_password": PW,
                            "agree": "1", "first_name": email.split("@")[0]})
    return c


def webhook(app, event, secret=WHSEC):
    body = json.dumps(event).encode()
    c = TestClient(app, base_url="https://testserver")
    return c.post("/stripe/webhook", content=body,
                  headers={"Stripe-Signature": billing.sign(body, secret),
                           "Content-Type": "application/json"})


def plan_of(app, ws):
    return db.workspace(app.state.conn, ws_id(app, ws))["plan"]


def test_checkout_sends_the_owner_to_stripe_with_the_member_count(app, fake):
    ann = person(app, "ann@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    page = ann.get("/settings/plan").text
    assert "Upgrade to Team" in page and "$10 a month for 1 member now" in page
    assert "Upgrade to Business" in page and "$20 a month for 1 member now" in page
    assert "Contact us" not in page
    assert "a year" not in page and "yearly" not in page
    assert "Upgrade to Team" not in ann.get("/settings").text      # General no longer sells
    # A stale form that still asks for a year gets the monthly price: there is no annual plan.
    r = ann.post("/billing/checkout", data={"plan": "team", "interval": "year"})
    assert r.status_code == 303 and r.headers["location"].startswith("https://checkout.stripe.com/")
    session = next(p for m, path, p in fake.calls if path == "/v1/checkout/sessions")
    assert session["line_items[0][price]"] == "price_team_monthly"
    assert not any("yearly" in str(p) for _m, _path, p in fake.calls)
    assert session["line_items[0][quantity]"] == "1"
    assert session["client_reference_id"] == str(ws_id(app, ws))
    assert session["subscription_data[metadata][workspace_id]"] == str(ws_id(app, ws))
    assert session["success_url"].endswith(f"/settings/plan?w={ws}&billing=done")
    assert session["cancel_url"].endswith(f"/settings/plan?w={ws}")
    # The customer is created once and remembered.
    ann.post("/billing/checkout", data={"plan": "team", "interval": "month"})
    assert sum(1 for _m, path, _p in fake.calls if path == "/v1/customers") == 1


def test_business_is_bought_in_the_app(app, fake):
    ann = person(app, "ann@example.com")
    r = ann.post("/billing/checkout", data={"plan": "business", "interval": "month"})
    assert r.status_code == 303
    session = next(p for m, path, p in fake.calls if path == "/v1/checkout/sessions")
    assert session["line_items[0][price]"] == "price_business_monthly"
    assert ann.post("/billing/checkout", data={"plan": "enterprise"}).status_code == 400


def test_owners_move_between_team_and_business(app, fake, monkeypatch):
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    fake.sub("sub_1", ws_id(app, ws), qty=1)
    webhook(app, {"type": "customer.subscription.created", "data": {"object": {"id": "sub_1"}}})
    page = ann.get("/settings/plan").text
    assert "Upgrade to Business" in page and "The rest of this month is charged now" in page
    assert "/billing/change" in page and "Contact us" not in page

    # Up: charged now for the rest of the period, and only if the payment goes through.
    r = ann.post(f"/billing/change?w={ws}", data={"plan": "business"})
    assert r.status_code == 303 and r.headers["location"] == f"/settings/plan?w={ws}&done=upgraded"
    change = [p for m, path, p in fake.calls if m == "POST" and path == "/v1/subscriptions/sub_1"][-1]
    assert change["items[0][price]"] == "price_business_monthly"
    assert change["items[0][id]"] == "si_1" and change["items[0][quantity]"] == "1"
    assert change["proration_behavior"] == "always_invoice"
    assert change["payment_behavior"] == "pending_if_incomplete"
    assert plan_of(app, ws) == "business"            # without waiting for the webhook
    page = ann.get(r.headers["location"]).text
    assert "Upgraded." in page and "<b>$20</b> a month" in page and "Switch to Team" in page

    # Down: credited on the next invoice, nothing charged now.
    r = ann.post(f"/billing/change?w={ws}", data={"plan": "team"})
    assert r.headers["location"] == f"/settings/plan?w={ws}&done=switched"
    change = [p for m, path, p in fake.calls if m == "POST" and path == "/v1/subscriptions/sub_1"][-1]
    assert change["proration_behavior"] == "create_prorations" and "payment_behavior" not in change
    assert plan_of(app, ws) == "team"

    # A declined upgrade changes nothing and says what to do.
    fake.decline = True
    r = ann.post(f"/billing/change?w={ws}", data={"plan": "business"})
    assert r.status_code == 400 and "could not take the payment" in r.text
    assert plan_of(app, ws) == "team"
    fake.decline = False

    # Not while a payment is failing, not to the plan it is on, and never by a member.
    fake.sub("sub_1", ws_id(app, ws), status="past_due")
    webhook(app, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})
    assert "Update the card first" in ann.get("/settings/plan").text
    r = ann.post(f"/billing/change?w={ws}", data={"plan": "business"})
    assert r.status_code == 400 and plan_of(app, ws) == "team"
    fake.sub("sub_1", ws_id(app, ws))
    assert ann.post(f"/billing/change?w={ws}", data={"plan": "team"}).status_code == 400
    code = invite_code(ann, monkeypatch, "bob@example.com")
    bob.get(f"/invite/{code}")
    assert bob.post(f"/billing/change?w={ws}", data={"plan": "business"}).status_code == 403
    assert plan_of(app, ws) == "team"


def test_signed_webhooks_move_the_workspace_between_plans(app, fake):
    ann = person(app, "ann@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    renews = 1_793_145_600                                   # 2026-10-28 00:00 UTC
    fake.sub("sub_1", ws_id(app, ws), period_end=renews)
    r = webhook(app, {"type": "checkout.session.completed", "data": {"object": {
        "mode": "subscription", "client_reference_id": str(ws_id(app, ws)), "customer": "cus_1",
        "subscription": "sub_1"}}})
    assert r.status_code == 200 and r.json()["result"] == "team"
    assert plan_of(app, ws) == "team"
    page = ann.get("/settings/plan").text
    assert "<b>$10</b> a month" in page and "1 member at $10 each" in page
    assert "Active" in page and "Next payment" in page and "October 28, 2026" in page
    assert "Manage billing" in page and "Upgrade to Team" not in page
    assert "adds $10 a month" in ann.get("/settings/members").text

    # Cancelled in the portal: still Team until the period ends, and it says so.
    fake.sub("sub_1", ws_id(app, ws), period_end=renews, cancel_at_period_end=True)
    webhook(app, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})
    page = ann.get("/settings/plan").text
    assert plan_of(app, ws) == "team"
    assert "Team ends on October 28, 2026" in page and "Keep Team" in page
    assert "Next payment" not in page
    # Newer API versions schedule it with cancel_at instead.
    fake.sub("sub_1", ws_id(app, ws), period_end=renews, cancel_at=renews)
    webhook(app, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})
    assert db.workspace(app.state.conn, ws_id(app, ws))["billing_ends_at"] == renews
    # Renewed in the portal: the warning goes away.
    fake.sub("sub_1", ws_id(app, ws), period_end=renews)
    webhook(app, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})
    assert "ends on" not in ann.get("/settings/plan").text

    fake.sub("sub_1", ws_id(app, ws), status="past_due")
    webhook(app, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})
    assert plan_of(app, ws) == "team"                 # grace while Stripe retries
    page = ann.get("/settings/plan").text
    assert "Payment failed" in page and "Update card" in page

    fake.sub("sub_1", ws_id(app, ws), status="canceled")
    webhook(app, {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_1"}}})
    assert plan_of(app, ws) == "free"
    assert db.workspace(app.state.conn, ws_id(app, ws))["stripe_subscription"] is None
    assert db.workspace(app.state.conn, ws_id(app, ws))["billing_ends_at"] is None
    assert "Upgrade to Team" in ann.get("/settings/plan").text


def test_moving_to_free_needs_the_owner_alone(app, fake, monkeypatch):
    """Free is for one person, so the owner removes everyone else and cancels pending
    invites before a paid workspace can move to Free, as in Confluence (Forrest,
    2026-09-27). The move waits for the end of the paid month and can be undone."""
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    renews = 1_793_145_600                                   # 2026-10-28 00:00 UTC
    fake.sub("sub_1", ws_id(app, ws), period_end=renews)
    webhook(app, {"type": "customer.subscription.created", "data": {"object": {"id": "sub_1"}}})
    code = invite_code(ann, monkeypatch, "bob@example.com")
    bob.get(f"/invite/{code}")
    cancels = lambda: [p for m, path, p in fake.calls                  # noqa: E731
                       if m == "POST" and path == "/v1/subscriptions/sub_1"
                       and ("cancel_at_period_end" in p or "cancel_at" in p)]

    # Someone else is still in: refused, and the page says what to do.
    page = ann.get(f"/settings/plan?w={ws}").text
    assert "Remove the 1 other member under Members first" in page and "Go to Members" in page
    assert "/billing/free" not in page and "cancelling, in Stripe" not in page
    r = ann.post(f"/billing/free?w={ws}")
    assert r.status_code == 400 and "Free is for one person. Remove the 1 other member" in r.text
    assert bob.post(f"/billing/free?w={ws}").status_code == 403
    assert not cancels() and plan_of(app, ws) == "team"

    # Bob removed; a pending invite still blocks it.
    bob_id = auth.user_by_email(app.state.conn, "bob@example.com")["id"]
    assert ann.post(f"/settings/members/{bob_id}/remove?w={ws}").status_code == 303
    invite_code(ann, monkeypatch, "cat@example.com", w=ws)
    assert "Cancel the 1 pending invite under Members first" in ann.get(
        f"/settings/plan?w={ws}").text
    assert ann.post(f"/billing/free?w={ws}").status_code == 400 and not cancels()
    pending = db.pending_invites(app.state.conn, ws_id(app, ws))[0]["id"]
    ann.post(f"/settings/invites/{pending}/cancel?w={ws}")

    # Alone: scheduled for the end of the paid month, nothing charged, still Team until then.
    page = ann.get(f"/settings/plan?w={ws}").text
    assert "Move to Free" in page and "/billing/free" in page
    r = ann.post(f"/billing/free?w={ws}")
    assert r.status_code == 303 and r.headers["location"] == f"/settings/plan?w={ws}&done=to_free"
    assert cancels()[-1] == {"cancel_at_period_end": "true"}
    assert plan_of(app, ws) == "team"
    assert db.workspace(app.state.conn, ws_id(app, ws))["billing_ends_at"] == renews
    page = ann.get(r.headers["location"]).text
    assert "then this workspace moves to Free" in page
    assert "Team ends on October 28, 2026" in page and "Keep Team" in page
    assert "Moving to Free on October 28, 2026" in page and "/billing/free" not in page
    # Nobody can be invited in the meantime.
    r = ann.post(f"/settings/invite?w={ws}", data={"email": "dan@example.com"})
    assert r.status_code == 400 and "moves to Free on October 28, 2026" in r.text

    # Keep Team undoes it.
    r = ann.post(f"/billing/keep?w={ws}")
    assert r.status_code == 303 and r.headers["location"] == f"/settings/plan?w={ws}&done=kept"
    assert cancels()[-1] == {"cancel_at_period_end": "false"}
    assert db.workspace(app.state.conn, ws_id(app, ws))["billing_ends_at"] is None
    # A cancellation Stripe holds as cancel_at is cleared as cancel_at.
    fake.sub("sub_1", ws_id(app, ws), period_end=renews, cancel_at=renews)
    ann.post(f"/billing/keep?w={ws}")
    assert cancels()[-1] == {"cancel_at": ""}

    # When Stripe ends it, the workspace is Free with one person in it.
    fake.sub("sub_1", ws_id(app, ws), status="canceled")
    webhook(app, {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_1"}}})
    assert plan_of(app, ws) == "free" and len(db.members(app.state.conn, ws_id(app, ws))) == 1


def test_owners_hear_when_a_lapsed_plan_leaves_the_workspace_read_only(app, fake, monkeypatch):
    """Stripe gives up on the card: the plan ends with everyone still in, the
    workspace goes read-only, and each owner gets one email saying what to do."""
    from dexio.server import mail
    sent = []
    monkeypatch.setattr(mail, "read_only", lambda *a: sent.append(a) or True)
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    fake.sub("sub_1", ws_id(app, ws))
    webhook(app, {"type": "customer.subscription.created", "data": {"object": {"id": "sub_1"}}})
    bob.get(f"/invite/{invite_code(ann, monkeypatch, 'bob@example.com')}")
    fake.sub("sub_1", ws_id(app, ws), status="unpaid")
    webhook(app, {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_1"}}})
    assert plan_of(app, ws) == "free" and db.read_only_reason(app.state.conn, ws_id(app, ws))
    assert len(sent) == 1
    to, name, plan, members, link = sent[0]
    assert to == "ann@example.com" and plan == "Team" and members == 2
    assert link.endswith(f"/settings/plan?w={ws}")
    # The same news again (Stripe sends deleted after unpaid) is not sent twice.
    fake.sub("sub_1", ws_id(app, ws), status="canceled")
    webhook(app, {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_1"}}})
    assert len(sent) == 1
    # An owner alone when the plan lapses is simply on Free: nothing to fix, no email.
    cat = person(app, "cat@example.com")
    ws2 = cat.get("/api/v1/workspaces").json()["current"]
    fake.sub("sub_2", ws_id(app, ws2))
    webhook(app, {"type": "customer.subscription.created", "data": {"object": {"id": "sub_2"}}})
    fake.sub("sub_2", ws_id(app, ws2), status="canceled")
    webhook(app, {"type": "customer.subscription.deleted", "data": {"object": {"id": "sub_2"}}})
    assert plan_of(app, ws2) == "free" and len(sent) == 1


def test_plan_page_for_members_and_plans_set_by_hand(app, fake, monkeypatch):
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    # Free has one member: Members points the owner at Plan.
    members = ann.get("/settings/members").text
    assert "See plans" in members and f"/settings/plan?w={ws}" in members
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='business' WHERE id=?", (ws_id(app, ws),))
    code = invite_code(ann, monkeypatch, "bob@example.com")
    bob.get(f"/invite/{code}")
    # A plan set by hand has no Stripe subscription: no price, no portal, and whom to ask.
    page = ann.get(f"/settings/plan?w={ws}").text
    assert "Arranged with Dexio" in page and "Manage billing" not in page
    assert "Contact us" in page and "/billing/change" not in page
    # ...and is self-serve all the same: Checkout for Team, Free once the owner is alone.
    assert "Choose Team" in page and "/billing/checkout" in page
    assert "Remove the 1 other member under Members first" in page
    assert "adds $" not in ann.get(f"/settings/members?w={ws}").text
    # Members see the plan but not the buttons, and are told who can change it.
    page = bob.get(f"/settings/plan?w={ws}").text
    assert "Only owners change the plan: ann" in page
    assert "Upgrade to" not in page and "Contact us" not in page


def test_old_checkout_return_lands_on_plan(app, fake):
    ann = person(app, "ann@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    r = ann.get(f"/settings?w={ws}&billing=done")
    assert r.status_code == 303 and r.headers["location"] == f"/settings/plan?w={ws}&billing=done"
    assert "The plan changes as soon as Stripe confirms" in ann.get(r.headers["location"]).text


def test_contact_us_on_plan_opens_a_form_that_emails_us(app, fake, monkeypatch):
    """A plan arranged by hand offers Contact us, which opens a form in the page (no
    mailto link: they did nothing without a mail app). Sending it emails us with the
    workspace attached and Reply-To set to the owner; members get no form."""
    from dexio.server import mail
    sent = []
    monkeypatch.setattr(mail, "contact", lambda *a: sent.append(a) or True)
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='business' WHERE id=?", (ws_id(app, ws),))
    bob.get(f"/invite/{invite_code(ann, monkeypatch, 'bob@example.com')}")
    page = ann.get(f"/settings/plan?w={ws}").text
    assert "mailto:" not in page
    assert 'data-contact="billing"' in page
    assert '<dialog id="contact"' in page and "We reply to <b>ann@example.com</b>" in page
    assert '<dialog id="contact" class="cdlg" open' not in page
    # Without JavaScript the button's link reloads Plan with the form open.
    assert '<dialog id="contact" class="cdlg" open' in ann.get(
        f"/settings/plan?w={ws}&contact=team").text
    assert 'id="contact"' not in bob.get(f"/settings/plan?w={ws}").text

    r = ann.post(f"/settings/contact?w={ws}", data={"about": "team",
                                                   "message": "Move us to Team, please.\r\nThanks"})
    assert r.status_code == 303 and r.headers["location"].endswith(f"w={ws}&done=contact_sent")
    assert "Message sent." in ann.get(r.headers["location"]).text
    (frm, name, wsname, wsid, plan, members, about, message, link), = sent
    assert (frm, name, wsid, plan, members, about) == (
        "ann@example.com", "ann", ws_id(app, ws), "Business", 2, "Moving to Team")
    assert message == "Move us to Team, please.\nThanks" and link.endswith(f"/settings/plan?w={ws}")

    assert ann.post(f"/settings/contact?w={ws}", data={"message": "  "}).status_code == 400
    assert bob.post(f"/settings/contact?w={ws}", data={"message": "hi"}).status_code == 403
    assert ann.post(f"/settings/contact?w={ws}",
                    data={"message": "x" * 5001}).status_code == 400
    # Five an hour per person.
    codes = [ann.post(f"/settings/contact?w={ws}", data={"message": f"m{i}"}).status_code
             for i in range(5)]
    assert codes == [303] * 4 + [429]
    assert len(sent) == 5


def test_contact_reports_a_send_that_failed(app, fake, monkeypatch):
    from dexio.server import mail
    monkeypatch.setattr(mail, "contact", lambda *a: False)
    ann = person(app, "ann@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    r = ann.post(f"/settings/contact?w={ws}", data={"message": "hello"})
    assert r.status_code == 502 and "did not go through" in r.text
    assert "support@dexio.wiki" in r.text


def test_help_lets_any_member_write_to_us(app, fake, monkeypatch):
    """Settings, Help (2026-09-29, after a customer's agent told him Dexio had no support
    address): the account menu leads there, it shows the support address, and its form
    reaches us from any member, not only an owner as Plan's does."""
    from dexio.server import mail
    from dexio.server.mcp_server import INSTRUCTIONS
    sent = []
    monkeypatch.setattr(mail, "contact", lambda *a: sent.append(a) or True)
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    with app.state.conn as c:        # inviting needs a paid plan
        c.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws_id(app, ws),))
    bob.get(f"/invite/{invite_code(ann, monkeypatch, 'bob@example.com')}")
    home = bob.get(f"/?w={ws}", headers={"accept": "text/html"}).text
    assert 'href="/settings/help">' in home and ">Help</span>" in home
    page = bob.get(f"/settings/help?w={ws}").text
    assert 'name="about" value="help"' in page and "We reply to <b>bob@example.com</b>" in page
    assert 'href="mailto:support@dexio.wiki"' in page and "forrest@" not in page

    r = bob.post(f"/settings/contact?w={ws}", data={"about": "help", "message": "Where is X?"})
    assert r.status_code == 303 and r.headers["location"].endswith(f"w={ws}&done=contact_sent")
    assert "/settings/help?" in r.headers["location"]
    assert "Message sent." in bob.get(r.headers["location"]).text
    (frm, _name, _wsname, wsid, _plan, members, about, message, _link), = sent
    assert (frm, wsid, members, about, message) == (
        "bob@example.com", ws_id(app, ws), 2, "Help", "Where is X?")
    r = bob.post(f"/settings/contact?w={ws}", data={"about": "help", "message": " "})
    assert r.status_code == 400 and "<h1>Help</h1>" in r.text
    # Plan's form stays owners-only.
    assert bob.post(f"/settings/contact?w={ws}", data={"message": "hi"}).status_code == 403
    # Agents are told where help is, and that permanent deletion goes through it.
    assert "support@dexio.wiki" in INSTRUCTIONS and "https://dexio.wiki/contact" in INSTRUCTIONS


def test_a_plan_set_by_hand_is_self_serve(app, fake):
    """Business set by hand (no subscription): the owner can buy Team through
    Checkout, which replaces the arrangement when Stripe confirms, or move to Free
    at once, since nothing is being paid. Enterprise still asks."""
    ann = person(app, "ann@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='business' WHERE id=?", (ws_id(app, ws),))
    page = ann.get(f"/settings/plan?w={ws}").text
    assert "Choose Team" in page and "The arrangement ends when Stripe confirms" in page
    assert "To pay by card instead, choose a plan below." in page
    r = ann.post(f"/billing/checkout?w={ws}", data={"plan": "team"})
    assert r.status_code == 303 and r.headers["location"].startswith("https://checkout.stripe.com/")
    session = [p for m, path, p in fake.calls if path == "/v1/checkout/sessions"][-1]
    assert session["line_items[0][quantity]"] == "1"
    # Stripe confirms: the webhook puts the workspace on the Team subscription.
    fake.sub("sub_1", ws_id(app, ws), plan="team")
    webhook(app, {"type": "customer.subscription.created", "data": {"object": {"id": "sub_1"}}})
    assert plan_of(app, ws) == "team"

    # Free at once, no Stripe call, for a hand-set plan with the owner alone.
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='business', stripe_subscription=NULL WHERE id=?",
                  (ws_id(app, ws),))
    assert "takes effect now" in ann.get(f"/settings/plan?w={ws}").text
    before = len(fake.calls)
    r = ann.post(f"/billing/free?w={ws}")
    assert r.status_code == 303 and r.headers["location"].endswith("done=to_free_now")
    assert plan_of(app, ws) == "free" and len(fake.calls) == before
    assert "This workspace is on Free now." in ann.get(r.headers["location"]).text

    # Enterprise is a contract: its owner writes to us instead.
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='enterprise' WHERE id=?", (ws_id(app, ws),))
    page = ann.get(f"/settings/plan?w={ws}").text
    assert 'data-contact="team"' in page and "Choose Team" not in page
    assert ann.post(f"/billing/free?w={ws}").status_code == 400


def test_contact_mail_goes_to_us_with_reply_to_the_sender(monkeypatch):
    from dexio.server import mail
    calls = []
    monkeypatch.setattr(mail, "send", lambda *a, **k: calls.append((a, k)) or True)
    assert mail.contact("ann@example.com", "Ann Lee", "Acme\nBcc: x", 7, "Business", 2,
                        "Moving to Team", "Please switch us.", "https://app/settings/plan?w=7")
    (to, subject, text), kw = calls[0]
    assert to == mail.contact_to() and kw["reply_to_addr"] == "ann@example.com"
    assert "\n" not in subject and subject.startswith("Dexio moving to team: Acme")
    assert "From: Ann Lee <ann@example.com>" in text and "Please switch us." in text


def test_webhook_rejects_bad_or_stale_signatures(app, fake):
    event = {"type": "customer.subscription.updated", "data": {"object": {"id": "sub_x"}}}
    assert webhook(app, event, secret="whsec_wrong").status_code == 400
    body = json.dumps(event).encode()
    old = billing.sign(body, WHSEC, ts=int(time.time()) - 3600)
    r = TestClient(app).post("/stripe/webhook", content=body, headers={"Stripe-Signature": old})
    assert r.status_code == 400
    assert webhook(app, {"type": "invoice.paid", "data": {"object": {}}}).json()["result"] \
        == "ignored"


def test_seats_follow_members(app, fake, monkeypatch):
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    fake.sub("sub_1", ws_id(app, ws), qty=1)
    webhook(app, {"type": "customer.subscription.created", "data": {"object": {"id": "sub_1"}}})
    assert plan_of(app, ws) == "team"
    code = invite_code(ann, monkeypatch, "bob@example.com")
    assert bob.get(f"/invite/{code}").status_code == 303
    update = [p for m, path, p in fake.calls if m == "POST" and path == "/v1/subscriptions/sub_1"]
    assert update and update[-1]["items[0][quantity]"] == "2"
    assert update[-1]["proration_behavior"] == "create_prorations"


def test_only_owners_touch_billing(app, fake, monkeypatch):
    ann, bob = person(app, "ann@example.com"), person(app, "bob@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    with app.state.conn as c:
        c.execute("UPDATE workspaces SET plan='team', stripe_customer='cus_1' WHERE id=?",
                  (ws_id(app, ws),))
    code = invite_code(ann, monkeypatch, "bob@example.com")
    bob.get(f"/invite/{code}")
    assert bob.post(f"/billing/portal?w={ws}").status_code == 403
    assert bob.post(f"/billing/checkout?w={ws}", data={"plan": "team"}).status_code == 403
    r = ann.post("/billing/portal")
    assert r.status_code == 303 and r.headers["location"].startswith("https://billing.stripe.com/")


def test_no_billing_without_a_key(app, fake, monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    ann = person(app, "ann@example.com")
    assert "Upgrade to" not in ann.get("/settings/plan").text
