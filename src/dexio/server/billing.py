"""Billing through Stripe: hosted Checkout to upgrade, the hosted billing portal
to manage, and a signed webhook that moves a workspace between plans.

A workspace's paid plan is a Stripe subscription whose quantity is the number of
members, billed monthly (annual billing was dropped on 2026-09-27). Prices are
found by lookup key (team_monthly, business_monthly), so no price IDs live in
code or config. The webhook is the only thing that changes `workspaces.plan`;
the redirect back from Checkout only shows a message.

Standard library only, like the rest of the server: a handful of form-encoded
calls do not justify the stripe package.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
import urllib.error
import urllib.parse
import urllib.request

from . import db, mail

logger = logging.getLogger(__name__)

API = "https://api.stripe.com"
PAID = ("team", "business")
LIVE_STATUSES = ("active", "trialing", "past_due")   # past_due keeps the plan while Stripe retries
DEAD_STATUSES = ("canceled", "unpaid", "incomplete_expired")


class BillingError(RuntimeError):
    pass


def enabled() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def call(method: str, path: str, params: dict | None = None) -> dict:
    """One Stripe API call. Raises BillingError with Stripe's message."""
    key = os.environ.get("STRIPE_SECRET_KEY", "")
    if not key:
        raise BillingError("billing is not configured on this server")
    body = urllib.parse.urlencode(params or {}, doseq=True).encode()
    url = API + path
    if method == "GET" and params:
        url, body = url + "?" + body.decode(), None
    req = urllib.request.Request(url, data=body if method != "GET" else None, method=method,
                                 headers={"Authorization": "Basic " + base64.b64encode(
                                     (key + ":").encode()).decode()})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            message = json.loads(e.read()).get("error", {}).get("message", "")
        except Exception:
            message = ""
        raise BillingError(message or f"Stripe returned {e.code}") from None
    except urllib.error.URLError as e:
        raise BillingError(f"could not reach Stripe: {e.reason}") from None


# ---- webhook signature (Stripe-Signature: t=...,v1=...) ----------------------
def verify(payload: bytes, header: str, secret: str, tolerance: int = 300,
           now: float | None = None) -> dict:
    """The event, if the signature is valid and fresh; ValueError otherwise."""
    if not secret:
        raise ValueError("no webhook secret configured")
    parts: dict[str, list[str]] = {}
    for item in (header or "").split(","):
        k, _, v = item.strip().partition("=")
        parts.setdefault(k, []).append(v)
    try:
        ts = int(parts["t"][0])
    except (KeyError, ValueError):
        raise ValueError("malformed signature header") from None
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    if not any(hmac.compare_digest(expected, sig) for sig in parts.get("v1", [])):
        raise ValueError("signature does not match")
    if abs((now or time.time()) - ts) > tolerance:
        raise ValueError("signature timestamp too old")
    return json.loads(payload)


def sign(payload: bytes, secret: str, ts: int | None = None) -> str:
    """Build a Stripe-Signature header (tests and local replays)."""
    ts = int(ts or time.time())
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload, hashlib.sha256).hexdigest()
    return f"t={ts},v1={mac}"


# ---- prices, checkout, portal -------------------------------------------------
_price_cache: dict[str, tuple[float, str]] = {}


def price_id(plan: str) -> str:
    lookup = f"{plan}_monthly"
    hit = _price_cache.get(lookup)
    if hit and time.time() - hit[0] < 3600:
        return hit[1]
    data = call("GET", "/v1/prices", {"lookup_keys[]": lookup, "active": "true"}).get("data", [])
    if not data:
        raise BillingError(f"no active Stripe price with lookup key {lookup}")
    _price_cache[lookup] = (time.time(), data[0]["id"])
    return data[0]["id"]


def plan_of(price: dict) -> str:
    lookup = price.get("lookup_key") or ""
    plan = (price.get("metadata") or {}).get("plan") or lookup.split("_", 1)[0]
    return plan if plan in PAID else "free"


def ensure_customer(conn, ws: dict, email: str) -> str:
    if ws.get("stripe_customer"):
        return ws["stripe_customer"]
    customer = call("POST", "/v1/customers", {
        "email": email, "name": ws["name"], "metadata[workspace_id]": str(ws["id"])})
    with db.LOCK, conn:
        conn.execute("UPDATE workspaces SET stripe_customer=? WHERE id=?",
                     (customer["id"], ws["id"]))
    return customer["id"]


def checkout_url(conn, ws: dict, email: str, plan: str, base: str) -> str:
    if plan not in PAID:
        raise BillingError("that plan is not for sale in the app")
    seats = max(1, len(db.members(conn, ws["id"])))
    session = call("POST", "/v1/checkout/sessions", {
        "mode": "subscription",
        "customer": ensure_customer(conn, ws, email),
        "client_reference_id": str(ws["id"]),
        "line_items[0][price]": price_id(plan),
        "line_items[0][quantity]": str(seats),
        "subscription_data[metadata][workspace_id]": str(ws["id"]),
        "allow_promotion_codes": "true",
        "success_url": f"{base}/settings/plan?w={ws['handle']}&billing=done",
        "cancel_url": f"{base}/settings/plan?w={ws['handle']}",
    })
    return session["url"]


def change_plan(conn, ws: dict, plan: str) -> str:
    """Move a paying workspace between Team and Business. Returns "upgraded" or
    "switched". An upgrade charges the rest of this period at the new price now
    and applies only once that payment succeeds; a move down credits the unused
    part of the period to the next invoice. `ws` is the full workspaces row."""
    current = ws.get("plan") or "free"
    if plan not in PAID or current not in PAID:
        raise BillingError("that plan is not for sale in the app")
    if plan == current:
        raise BillingError(f"this workspace is already on {plan.title()}")
    sub_id = ws.get("stripe_subscription")
    if not sub_id:
        raise BillingError("this workspace has no subscription to change. Email "
                           "support@dexio.wiki")
    sub = call("GET", f"/v1/subscriptions/{sub_id}")
    if sub.get("status") not in ("active", "trialing"):
        raise BillingError("update the card under Manage billing before changing plans")
    item = sub["items"]["data"][0]
    up = PAID.index(plan) > PAID.index(current)
    params = {"items[0][id]": item["id"], "items[0][price]": price_id(plan),
              "items[0][quantity]": str(max(1, len(db.members(conn, ws["id"]))))}
    if up:
        params.update({"proration_behavior": "always_invoice",
                       "payment_behavior": "pending_if_incomplete"})
    else:
        params["proration_behavior"] = "create_prorations"
    updated = call("POST", f"/v1/subscriptions/{sub_id}", params)
    if updated.get("pending_update"):
        # Stripe could not take the payment, so nothing changed.
        raise BillingError("Stripe could not take the payment for the upgrade, so nothing "
                           "changed. Update the card under Manage billing, then try again")
    apply_subscription(conn, updated)     # the webhook will say the same; this is for the page
    return "upgraded" if up else "switched"


def free_blocker(conn, ws: dict, user_id: int) -> str:
    """Why this workspace cannot move to Free yet, or "" if it can. Free is for one
    person, so, as in Confluence, the owner first removes everyone but themselves
    and cancels pending invites (Forrest, 2026-09-27). Nobody is ever left in a
    Free workspace above its limit by a downgrade."""
    others = [m for m in db.members(conn, ws["id"]) if m["id"] != user_id]
    if others:
        n = len(others)
        return (f"Free is for one person. Remove the {n} other member{'s' if n != 1 else ''}"
                " under Members first.")
    pending = db.pending_invites(conn, ws["id"])
    if pending:
        n = len(pending)
        return (f"Free is for one person. Cancel the {n} pending invite{'s' if n != 1 else ''}"
                " under Members first.")
    return ""


def move_to_free(conn, ws: dict, user_id: int) -> str:
    """Schedule the move to Free for the end of the period already paid for; the
    webhook moves the plan when Stripe ends the subscription. A Team or Business
    plan set by hand (no subscription, nothing being paid) moves at once. `ws` is
    the full workspaces row; `user_id` is the owner asking. Returns "to_free"
    (scheduled) or "to_free_now"."""
    sub_id = ws.get("stripe_subscription")
    if (ws.get("plan") or "free") not in PAID:
        raise BillingError("this workspace has no paid plan to end")
    why = free_blocker(conn, ws, user_id)
    if why:
        raise BillingError(why.rstrip("."))
    if not sub_id:
        with db.LOCK, conn:
            conn.execute("UPDATE workspaces SET plan='free', billing_status=NULL,"
                         " billing_interval=NULL, billing_period_end=NULL, billing_ends_at=NULL"
                         " WHERE id=?", (ws["id"],))
        return "to_free_now"
    apply_subscription(conn, call("POST", f"/v1/subscriptions/{sub_id}",
                                  {"cancel_at_period_end": "true"}))
    return "to_free"


def keep_plan(conn, ws: dict) -> None:
    """Undo a scheduled move to Free. A cancellation set as cancel_at (newer API
    versions, or the portal) is cleared as cancel_at."""
    sub_id = ws.get("stripe_subscription")
    if not sub_id:
        raise BillingError("this workspace has no subscription")
    sub = call("GET", f"/v1/subscriptions/{sub_id}")
    params = ({"cancel_at": ""} if sub.get("cancel_at") and not sub.get("cancel_at_period_end")
              else {"cancel_at_period_end": "false"})
    apply_subscription(conn, call("POST", f"/v1/subscriptions/{sub_id}", params))


def portal_url(ws: dict, base: str) -> str:
    if not ws.get("stripe_customer"):
        raise BillingError("this workspace has no billing account yet")
    params = {"customer": ws["stripe_customer"],
              "return_url": f"{base}/settings/plan?w={ws['handle']}"}
    config = os.environ.get("STRIPE_PORTAL_CONFIG", "")
    if config:
        params["configuration"] = config
    return call("POST", "/v1/billing_portal/sessions", params)["url"]


# ---- applying subscription state -----------------------------------------------
def _workspace_for(conn, sub: dict) -> int | None:
    wid = (sub.get("metadata") or {}).get("workspace_id")
    if wid and str(wid).isdigit():
        return int(wid)
    row = conn.execute("SELECT id FROM workspaces WHERE stripe_subscription=? OR"
                       " stripe_customer=?", (sub.get("id"), sub.get("customer"))).fetchone()
    return int(row["id"]) if row else None


def apply_subscription(conn, sub: dict) -> tuple[int | None, str]:
    """Make the workspace's plan match a subscription object. Idempotent, so a
    repeated or out-of-order event does no harm when called with fresh state."""
    ws = _workspace_for(conn, sub)
    if ws is None:
        return None, "unknown"
    status = sub.get("status", "")
    items = (sub.get("items") or {}).get("data") or []
    item = items[0] if items else {}
    price = item.get("price") or {}
    if status in LIVE_STATUSES:
        plan = plan_of(price)
    elif status in DEAD_STATUSES:
        plan = "free"
    else:                                   # incomplete: first payment still pending
        return ws, "pending"
    interval = ((price.get("recurring") or {}).get("interval")) or None
    # The period moved from the subscription to its items in Stripe's 2025 API
    # versions; read either. A cancellation shows as cancel_at, or on older
    # versions as cancel_at_period_end.
    period_end = item.get("current_period_end") or sub.get("current_period_end")
    ends_at = sub.get("cancel_at") or (period_end if sub.get("cancel_at_period_end") else None)
    if status in DEAD_STATUSES:
        period_end = ends_at = None
    with db.LOCK, conn:
        conn.execute(
            "UPDATE workspaces SET plan=?, billing_status=?, billing_interval=?,"
            " billing_period_end=?, billing_ends_at=?,"
            " stripe_subscription=?, stripe_customer=COALESCE(stripe_customer, ?) WHERE id=?",
            (plan, status, interval, period_end, ends_at,
             None if status in DEAD_STATUSES else sub.get("id"),
             sub.get("customer"), ws))
    return ws, plan


def handle_event(conn, event: dict) -> str:
    """Webhook entry point. Re-reads the subscription from Stripe rather than
    trusting the event body, so out-of-order delivery cannot roll a plan back."""
    kind = event.get("type", "")
    obj = (event.get("data") or {}).get("object") or {}
    if kind == "checkout.session.completed" and obj.get("mode") == "subscription":
        ws = obj.get("client_reference_id")
        if ws and str(ws).isdigit():
            with db.LOCK, conn:
                conn.execute("UPDATE workspaces SET stripe_customer=?, stripe_subscription=?"
                             " WHERE id=?", (obj.get("customer"), obj.get("subscription"),
                                             int(ws)))
        sub_id = obj.get("subscription")
    elif kind.startswith("customer.subscription."):
        sub_id = obj.get("id")
    else:
        return "ignored"
    if not sub_id:
        return "ignored"
    sub = call("GET", f"/v1/subscriptions/{sub_id}")
    ws_id = _workspace_for(conn, sub)
    before = (db.workspace(conn, ws_id) or {}).get("plan") if ws_id else None
    ws, plan = apply_subscription(conn, sub)
    logger.info("billing: %s -> workspace %s plan %s (%s)", kind, ws, plan, sub.get("status"))
    if ws and before in PAID and plan == "free":
        tell_owners_read_only(conn, ws, before)
    return plan


def tell_owners_read_only(conn, workspace_id: int, plan: str) -> None:
    """A paid plan just ended without anyone choosing Free. If that leaves the
    workspace over Free's one person, it is read-only (db.read_only_reason), and
    each owner is told once, here, when the plan changes."""
    if not db.read_only_reason(conn, workspace_id):
        return
    ws = db.workspace(conn, workspace_id) or {}
    people = db.members(conn, workspace_id)
    base = os.environ.get("DEXIO_PUBLIC_URL", "https://app.dexio.wiki").rstrip("/")
    link = f"{base}/settings/plan?w={ws.get('handle', '')}"
    for m in people:
        if m["role"] != "owner":
            continue
        try:
            mail.read_only(m["email"], ws.get("name", "Your workspace"), plan.title(),
                           len(people), link)
        except Exception:  # noqa: BLE001 - a lost email must not fail the webhook
            logger.exception("billing: could not email %s about read-only workspace %s",
                             m["email"], workspace_id)


def sync_seats(conn, workspace_id: int) -> None:
    """Keep the subscription's quantity equal to the member count. Stripe
    prorates the difference on the next invoice."""
    ws = db.workspace(conn, workspace_id) or {}
    sub_id = ws.get("stripe_subscription")
    if not sub_id or not enabled():
        return
    seats = max(1, len(db.members(conn, workspace_id)))
    try:
        sub = call("GET", f"/v1/subscriptions/{sub_id}")
        item = sub["items"]["data"][0]
        if item.get("quantity") != seats:
            call("POST", f"/v1/subscriptions/{sub_id}", {
                "items[0][id]": item["id"], "items[0][quantity]": str(seats),
                "proration_behavior": "create_prorations"})
    except (BillingError, KeyError, IndexError) as e:
        logger.warning("billing: could not sync seats for workspace %s: %s", workspace_id, e)
