"""Sign in with Google or GitHub from the desktop app (dexio-wiki/dexio-desktop).

The app is a window onto this site. Google refuses sign-in inside an app's embedded
browser (`disallowed_useragent`), and a session cookie set in the person's own
browser never reaches the app's window. So the app hands the sign-in to the system
browser and takes it back through its `dexio://` link, the way native apps do
(RFC 8252), with a PKCE-style binding so a stolen link is worthless:

1. The app catches the click on /auth/start, makes a random verifier, and opens
   /auth/start?provider=...&next=...&desktop=<challenge> in the system browser,
   where challenge = base64url(sha256(verifier)).
2. The challenge rides in the signed OAuth state. The provider round trip is the
   ordinary one, in the ordinary browser.
3. The callback does not sign the browser in. It issues a handoff code, signed by
   the server, naming the account, the challenge, where to go next and any new
   workspace, good for two minutes, and shows a page that opens dexio://auth?code=...
4. The app, which still holds the verifier, loads /auth/desktop?code=...&verifier=...
   in its own window. The server checks the code and that the verifier hashes to its
   challenge, and sets the session cookie there.

Another app that registers the dexio:// scheme and catches the link gets a code it
cannot redeem without the verifier, which never left the Dexio app. Nothing is
stored: the code is its own record, and it dies with its two minutes.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time

SCHEME = "dexio"
TTL = 120
_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")   # base64url of a SHA-256, unpadded
_VERIFIER = re.compile(r"^[A-Za-z0-9_-]{43,128}$")  # RFC 7636 section 4.1 length


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(key: bytes, body: str) -> str:
    return _b64(hmac.new(key, b"desktop-handoff." + body.encode(), hashlib.sha256).digest())


def valid_challenge(challenge: str) -> bool:
    return bool(_CHALLENGE.match(challenge or ""))


def challenge_for(verifier: str) -> str:
    return _b64(hashlib.sha256(verifier.encode()).digest())


def issue(key: bytes, user_id: int, challenge: str, next_url: str, workspace: int = 0,
          now: float | None = None) -> str:
    """The handoff code for the app's dexio:// link."""
    body = _b64(json.dumps({"u": int(user_id), "c": challenge, "n": next_url,
                            "w": int(workspace or 0), "t": int(now or time.time())},
                           separators=(",", ":")).encode())
    return f"{body}.{_sign(key, body)}"


def redeem(key: bytes, code: str, verifier: str, now: float | None = None) -> dict | None:
    """{user_id, next, workspace} for a code this server issued in the last two
    minutes whose challenge the verifier answers, else None."""
    if not _VERIFIER.match(verifier or ""):
        return None
    try:
        body, sig = (code or "").split(".", 1)
        if not hmac.compare_digest(sig, _sign(key, body)):
            return None
        data = json.loads(_unb64(body))
    except (ValueError, TypeError):
        return None
    if (now or time.time()) - float(data.get("t", 0)) > TTL:
        return None
    if not hmac.compare_digest(str(data.get("c", "")), challenge_for(verifier)):
        return None
    return {"user_id": int(data["u"]), "next": str(data.get("n") or "/"),
            "workspace": int(data.get("w") or 0)}


def link(code: str) -> str:
    return f"{SCHEME}://auth?code={code}"
