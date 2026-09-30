"""Sign in with Google or GitHub, directly (no identity vendor in between).

Each is the standard OAuth authorization-code flow. /auth/start sends the person to
the provider with a signed state (where to go next) and, for Google, a PKCE
challenge; the nonce and PKCE verifier travel in a short-lived httponly cookie, so a
sign-in can only be finished in the browser that started it. The provider sends
them back to /auth/<provider>/callback with a code, which is exchanged server to
server (with the client secret) for who they are:

- Google: the ID token from the token endpoint. It arrives over TLS straight from
  Google in reply to an authenticated request, so its claims are checked (issuer,
  audience, expiry, verified email) rather than its signature, as OpenID Connect
  Core 3.1.3.7 allows for a token received this way.
- GitHub: /user for the account id and /user/emails for a verified email, since
  the profile email can be empty or unverified.

Only a verified email is ever accepted. The app then links the identity to a Dexio
account (see auth.user_from_identity) and sets its own session cookie. A provider
is offered only when its client id and secret are both set.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from .auth import split_name

PROVIDERS = ("google", "github")
LABELS = {"google": "Google", "github": "GitHub"}
COOKIE = "dexio_auth"
STATE_TTL = 900

GOOGLE_AUTH = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN = "https://oauth2.googleapis.com/token"
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
GITHUB_AUTH = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN = "https://github.com/login/oauth/access_token"
GITHUB_API = "https://api.github.com"


class SocialError(Exception):
    pass


@dataclass
class Identity:
    provider: str
    subject: str          # the provider's stable account id
    email: str            # verified by the provider
    first: str = ""       # given name (Google), or GitHub's single name split once
    last: str = ""
    handle: str = ""      # GitHub username: names the workspace when there is no name


def _creds(provider: str) -> tuple[str, str]:
    p = provider.upper()
    return (os.environ.get(f"{p}_CLIENT_ID", "").strip(),
            os.environ.get(f"{p}_CLIENT_SECRET", "").strip())


def configured(provider: str) -> bool:
    return provider in PROVIDERS and all(_creds(provider))


def enabled() -> list[str]:
    return [p for p in PROVIDERS if configured(p)]


# ---- state ---------------------------------------------------------------------
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, b"social-state." + body.encode(), hashlib.sha256).digest())


def make_state(key: bytes, provider: str, next_url: str,
               desktop: str = "") -> tuple[str, str, str]:
    """(state for the provider, value for COOKIE, PKCE verifier). `desktop` is the
    desktop app's challenge when the app started this sign-in (see desktop.py)."""
    nonce = secrets.token_urlsafe(16)
    verifier = secrets.token_urlsafe(48)
    fields = {"p": provider, "n": nonce, "next": next_url, "t": int(time.time())}
    if desktop:
        fields["d"] = desktop
    body = _b64(json.dumps(fields, separators=(",", ":")).encode())
    return f"{body}.{_sign(key, body)}", f"{nonce}.{verifier}", verifier


def read_state(key: bytes, provider: str, state: str, cookie: str) -> tuple[str, str] | None:
    """(next URL, PKCE verifier) for a state this server issued to this browser for
    this provider in the last 15 minutes, else None."""
    try:
        body, sig = (state or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(key, body)):
            return None
        data = json.loads(_unb64(body))
        nonce, verifier = (cookie or "").split(".", 1)
    except (ValueError, TypeError):
        return None
    if data.get("p") != provider or not hmac.compare_digest(str(data.get("n", "")), nonce):
        return None
    if time.time() - float(data.get("t", 0)) > STATE_TTL:
        return None
    return str(data.get("next") or "/"), verifier


def state_desktop(key: bytes, state: str) -> str:
    """The desktop app's challenge in a state this server signed, or ''."""
    try:
        body, sig = (state or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(key, body)):
            return ""
        return str(json.loads(_unb64(body)).get("d") or "")
    except (ValueError, TypeError, AttributeError):
        return ""


# ---- the provider calls ----------------------------------------------------------
def authorize_url(provider: str, redirect_uri: str, state: str, verifier: str) -> str:
    client_id, _ = _creds(provider)
    if provider == "google":
        challenge = _b64(hashlib.sha256(verifier.encode()).digest())
        return GOOGLE_AUTH + "?" + urlencode({
            "client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code",
            "scope": "openid email profile", "state": state, "code_challenge": challenge,
            "code_challenge_method": "S256", "prompt": "select_account"})
    return GITHUB_AUTH + "?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri, "scope": "read:user user:email",
        "state": state, "allow_signup": "true"})


def _request(method: str, url: str, form: dict | None = None,
             token: str = "") -> Any:
    headers = {"Accept": "application/json", "User-Agent": "dexio"}
    data = None
    if form is not None:
        data = urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read())
            detail = body.get("error_description") or body.get("error") or body.get("message")
        except Exception:  # noqa: BLE001 - any unreadable error body
            detail = e.reason
        raise SocialError(f"the sign-in was refused ({e.code}): {detail}") from None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        raise SocialError(f"could not reach the sign-in provider: {e}") from None


def _google(code: str, redirect_uri: str, verifier: str) -> Identity:
    client_id, secret = _creds("google")
    got = _request("POST", GOOGLE_TOKEN, form={
        "code": code, "client_id": client_id, "client_secret": secret,
        "redirect_uri": redirect_uri, "grant_type": "authorization_code",
        "code_verifier": verifier})
    if not isinstance(got, dict):
        raise SocialError("Google returned no ID token")
    try:
        claims = json.loads(_unb64(str(got.get("id_token", "")).split(".")[1]))
    except (IndexError, ValueError, AttributeError):
        raise SocialError("Google returned no ID token") from None
    if claims.get("iss") not in GOOGLE_ISSUERS or claims.get("aud") != client_id:
        raise SocialError("Google's ID token is not for Dexio")
    if float(claims.get("exp", 0)) < time.time():
        raise SocialError("Google's ID token has expired")
    if claims.get("email_verified") is not True or not claims.get("email"):
        raise SocialError("Google has not verified this account's email address")
    first, last = str(claims.get("given_name") or ""), str(claims.get("family_name") or "")
    if not (first or last):
        first, last = split_name(str(claims.get("name") or ""))
    return Identity("google", str(claims["sub"]), str(claims["email"]), first, last)


def _github(code: str, redirect_uri: str) -> Identity:
    client_id, secret = _creds("github")
    got = _request("POST", GITHUB_TOKEN, form={
        "client_id": client_id, "client_secret": secret, "code": code,
        "redirect_uri": redirect_uri})
    token = got.get("access_token") if isinstance(got, dict) else None
    if not token:
        raise SocialError("GitHub refused the sign-in: "
                          f"{(got or {}).get('error_description') or 'no access token'}")
    user = _request("GET", f"{GITHUB_API}/user", token=token)
    emails = _request("GET", f"{GITHUB_API}/user/emails", token=token)
    if not isinstance(user, dict) or not user.get("id") or not isinstance(emails, list):
        raise SocialError("GitHub did not return the account")
    verified = [e for e in emails if isinstance(e, dict) and e.get("verified") and e.get("email")]
    pick = next((e for e in verified if e.get("primary")), verified[0] if verified else None)
    if not pick:
        raise SocialError("this GitHub account has no verified email address; verify one on "
                          "GitHub and try again")
    first, last = split_name(str(user.get("name") or ""))
    return Identity("github", str(user["id"]), str(pick["email"]), first, last,
                    str(user.get("login") or ""))


def exchange(provider: str, code: str, redirect_uri: str, verifier: str) -> Identity:
    """Who the person is, from the callback code. Raises SocialError."""
    if provider == "google":
        return _google(code, redirect_uri, verifier)
    if provider == "github":
        return _github(code, redirect_uri)
    raise SocialError(f"unknown provider {provider}")
