"""OAuth 2.1 for the MCP endpoint, so Claude and ChatGPT can connect by signing in.

Those clients cannot send a static bearer header. They discover this server's
authorization server from /.well-known metadata, register themselves (RFC 7591
dynamic client registration), send the person here to sign in and pick a
workspace, and exchange the code (PKCE S256) for a short-lived access token
plus a refresh token. The MCP library supplies the protocol endpoints
(/authorize, /token, /register, /revoke and both metadata documents); this
module is the storage and the consent step behind them.

An access token stands for one person in one workspace, with no wiki
restriction, exactly like a workspace token made in the connect flow. Tokens are
stored hashed. Access tokens live an hour; refresh tokens 30 days and rotate
on every use.
"""
from __future__ import annotations

import hashlib
import json
import secrets
import time

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken

from . import db

ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 300
PENDING_TTL = 900

SCHEMA = """
CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id TEXT PRIMARY KEY, info TEXT NOT NULL, created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_pending (
  id_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, params TEXT NOT NULL,
  expires_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_codes (
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, user_id INTEGER NOT NULL,
  workspace_id INTEGER NOT NULL, params TEXT NOT NULL, expires_at REAL NOT NULL,
  used_at REAL
);
CREATE TABLE IF NOT EXISTS oauth_tokens (
  token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL, client_id TEXT NOT NULL,
  user_id INTEGER NOT NULL, workspace_id INTEGER NOT NULL, scopes TEXT NOT NULL,
  resource TEXT, grant_id TEXT NOT NULL, created_at REAL NOT NULL,
  expires_at REAL NOT NULL, revoked_at REAL, last_used REAL
);
CREATE INDEX IF NOT EXISTS oauth_tokens_grant ON oauth_tokens(grant_id);
CREATE INDEX IF NOT EXISTS oauth_tokens_ws ON oauth_tokens(workspace_id, client_id);
-- A per-sign-in switch for reading shares, 2026-10-06 afternoon to evening; a sign-in
-- now acts as its person everywhere.
DROP TABLE IF EXISTS oauth_reach;
"""


def _h(value: str) -> str:
    return hashlib.sha256((value or "").encode("utf-8")).hexdigest()


def init(conn) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


# ---- used by the MCP gate and tools ----------------------------------------
def access_row(conn, token: str) -> dict | None:
    """The token-shaped row for a live OAuth access token, or None. Same fields
    the MCP tools read from a workspace token: name, project, workspace_id."""
    if not token or not token.startswith("dxa_"):
        return None
    # Reads under the write lock, as in db.check_token: a commit from another thread on
    # the shared connection can reset a statement mid-fetch and refuse a valid token.
    with db.LOCK:
        row = conn.execute(
            "SELECT t.*, c.info FROM oauth_tokens t JOIN oauth_clients c USING (client_id)"
            " WHERE t.token_hash=? AND t.kind='access'", (_h(token),)).fetchone()
        now = time.time()
        if not row or row["revoked_at"] or row["expires_at"] < now:
            return None
        # Membership can be removed after consent; the token dies with it.
        if not db.role_in(conn, row["workspace_id"], row["user_id"]):
            return None
        with conn:
            conn.execute("UPDATE oauth_tokens SET last_used=? WHERE token_hash=?",
                         (now, row["token_hash"]))
    name = json.loads(row["info"]).get("client_name") or "OAuth app"
    return {"id": None, "name": f"{name} (OAuth)", "project": None,
            "workspace_id": row["workspace_id"], "user_id": row["user_id"]}


def connections(conn, workspace_id: int) -> list[dict]:
    """Apps signed in to a workspace: one row per client with a live grant."""
    now = time.time()
    rows = conn.execute(
        "SELECT t.client_id, c.info, MIN(t.created_at) AS since, MAX(t.last_used) AS last_used"
        " FROM oauth_tokens t JOIN oauth_clients c USING (client_id)"
        " WHERE t.workspace_id=? AND t.revoked_at IS NULL AND t.expires_at>?"
        # c.info is grouped too: Postgres rejects a selected column that is
        # neither grouped nor aggregated, which SQLite allows.
        " GROUP BY t.client_id, c.info ORDER BY since", (workspace_id, now)).fetchall()
    return [{"client_id": r["client_id"],
             "name": json.loads(r["info"]).get("client_name") or "OAuth app",
             "since": r["since"], "last_used": r["last_used"]} for r in rows]


def my_connections(conn, user_id: int) -> list[dict]:
    """A person's app sign-ins: one row per app and workspace it was signed in to
    (where it starts), for Settings > Agents."""
    now = time.time()
    rows = conn.execute(
        "SELECT t.client_id, c.info, t.workspace_id, w.name AS workspace_name,"
        " w.handle AS workspace_handle, MIN(t.created_at) AS since, MAX(t.last_used) AS last_used"
        " FROM oauth_tokens t JOIN oauth_clients c USING (client_id)"
        " JOIN workspaces w ON w.id = t.workspace_id"
        " WHERE t.user_id=? AND t.revoked_at IS NULL AND t.expires_at>?"
        " GROUP BY t.client_id, c.info, t.workspace_id, w.name, w.handle ORDER BY since",
        (user_id, now)).fetchall()
    return [{"client_id": r["client_id"],
             "name": json.loads(r["info"]).get("client_name") or "OAuth app",
             "since": r["since"], "last_used": r["last_used"], "workspace_id": r["workspace_id"],
             "workspace_name": r["workspace_name"], "workspace_handle": r["workspace_handle"]}
            for r in rows]


def disconnect_mine(conn, user_id: int, client_id: str, workspace_id: int) -> int:
    """Sign one person's app out of the workspace it was signed in to."""
    with db.LOCK, conn:
        cur = conn.execute("UPDATE oauth_tokens SET revoked_at=? WHERE user_id=? AND"
                           " client_id=? AND workspace_id=? AND revoked_at IS NULL",
                           (time.time(), user_id, client_id, workspace_id))
    return cur.rowcount


def disconnect(conn, workspace_id: int, client_id: str) -> int:
    with db.LOCK, conn:
        cur = conn.execute("UPDATE oauth_tokens SET revoked_at=? WHERE workspace_id=? AND"
                           " client_id=? AND revoked_at IS NULL",
                           (time.time(), workspace_id, client_id))
    return cur.rowcount


# ---- consent step ------------------------------------------------------------
def pending(conn, req: str) -> tuple[OAuthClientInformationFull, dict] | None:
    row = conn.execute("SELECT * FROM oauth_pending WHERE id_hash=?", (_h(req),)).fetchone()
    if not row or row["expires_at"] < time.time():
        return None
    client = conn.execute("SELECT info FROM oauth_clients WHERE client_id=?",
                          (row["client_id"],)).fetchone()
    if not client:
        return None
    return OAuthClientInformationFull.model_validate_json(client["info"]), json.loads(row["params"])


def approve(conn, req: str, user_id: int, workspace_id: int, issuer: str = "") -> str:
    """Turn a pending request into an authorization code for this person and
    workspace. Returns the client redirect URL carrying the code."""
    found = pending(conn, req)
    if not found:
        raise ValueError("this sign-in request expired; start again from your app")
    client, params = found
    code = "dxc_" + secrets.token_urlsafe(32)
    with db.LOCK, conn:
        conn.execute("DELETE FROM oauth_pending WHERE id_hash=?", (_h(req),))
        conn.execute("INSERT INTO oauth_codes (code_hash, client_id, user_id, workspace_id,"
                     " params, expires_at) VALUES (?,?,?,?,?,?)",
                     (_h(code), client.client_id, user_id, workspace_id, json.dumps(params),
                      time.time() + CODE_TTL))
    # RFC 9207: name the issuer in the response. ChatGPT uses its stable callback
    # URL only for servers that do this.
    return construct_redirect_uri(params["redirect_uri"], code=code, state=params.get("state"),
                                  iss=issuer or None)


def deny(conn, req: str, issuer: str = "") -> str | None:
    found = pending(conn, req)
    if not found:
        return None
    _client, params = found
    with db.LOCK, conn:
        conn.execute("DELETE FROM oauth_pending WHERE id_hash=?", (_h(req),))
    return construct_redirect_uri(params["redirect_uri"], error="access_denied",
                                  error_description="The person declined access.",
                                  state=params.get("state"), iss=issuer or None)


# ---- the provider the MCP library calls --------------------------------------
class Provider:
    def __init__(self, conn, issuer: str):
        self.conn = conn
        self.issuer = issuer.rstrip("/")

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        row = self.conn.execute("SELECT info FROM oauth_clients WHERE client_id=?",
                                (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row["info"]) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        with db.LOCK, self.conn:
            self.conn.execute("INSERT OR REPLACE INTO oauth_clients (client_id, info,"
                              " created_at) VALUES (?,?,?)",
                              (client_info.client_id, client_info.model_dump_json(),
                               time.time()))

    async def authorize(self, client: OAuthClientInformationFull,
                        params: AuthorizationParams) -> str:
        if params.resource and params.resource.rstrip("/") != self.issuer + "/mcp":
            raise AuthorizeError("invalid_target", "this server only protects /mcp")
        req = "dxp_" + secrets.token_urlsafe(24)
        now = time.time()
        with db.LOCK, self.conn:
            self.conn.execute("DELETE FROM oauth_pending WHERE expires_at<?", (now,))
            self.conn.execute(
                "INSERT INTO oauth_pending (id_hash, client_id, params, expires_at)"
                " VALUES (?,?,?,?)",
                (_h(req), client.client_id, params.model_dump_json(), now + PENDING_TTL))
        return f"{self.issuer}/oauth/consent?req={req}"

    async def load_authorization_code(self, client: OAuthClientInformationFull,
                                      authorization_code: str) -> AuthorizationCode | None:
        row = self.conn.execute("SELECT * FROM oauth_codes WHERE code_hash=? AND client_id=?",
                                (_h(authorization_code), client.client_id)).fetchone()
        if not row or row["used_at"] or row["expires_at"] < time.time():
            return None
        p = json.loads(row["params"])
        return AuthorizationCode(
            code=authorization_code, scopes=p.get("scopes") or [], expires_at=row["expires_at"],
            client_id=client.client_id, code_challenge=p["code_challenge"],
            redirect_uri=p["redirect_uri"],
            redirect_uri_provided_explicitly=p["redirect_uri_provided_explicitly"],
            resource=p.get("resource"),
            subject=f'{row["user_id"]}:{row["workspace_id"]}')

    def _issue(self, client_id: str, user_id: int, workspace_id: int, scopes: list[str],
               resource: str | None, grant_id: str) -> OAuthToken:
        access = "dxa_" + secrets.token_urlsafe(32)
        refresh = "dxr_" + secrets.token_urlsafe(32)
        now = time.time()
        rows = [(_h(access), "access", now + ACCESS_TTL), (_h(refresh), "refresh", now + REFRESH_TTL)]
        with db.LOCK, self.conn:
            for token_hash, kind, expires in rows:
                self.conn.execute(
                    "INSERT INTO oauth_tokens (token_hash, kind, client_id, user_id, workspace_id,"
                    " scopes, resource, grant_id, created_at, expires_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (token_hash, kind, client_id, user_id, workspace_id, json.dumps(scopes),
                     resource, grant_id, now, expires))
        return OAuthToken(access_token=access, token_type="Bearer", expires_in=ACCESS_TTL,
                          refresh_token=refresh, scope=" ".join(scopes) or None)

    async def exchange_authorization_code(self, client: OAuthClientInformationFull,
                                          authorization_code: AuthorizationCode) -> OAuthToken:
        with db.LOCK, self.conn:
            row = self.conn.execute("SELECT * FROM oauth_codes WHERE code_hash=?",
                                    (_h(authorization_code.code),)).fetchone()
            if not row or row["used_at"]:
                raise TokenError("invalid_grant", "authorization code already used")
            self.conn.execute("UPDATE oauth_codes SET used_at=? WHERE code_hash=?",
                              (time.time(), row["code_hash"]))
        return self._issue(client.client_id, row["user_id"], row["workspace_id"],
                           authorization_code.scopes, authorization_code.resource,
                           "g_" + secrets.token_hex(8))

    def _token_row(self, token: str, kind: str, client_id: str | None = None):
        row = self.conn.execute("SELECT * FROM oauth_tokens WHERE token_hash=? AND kind=?",
                                (_h(token), kind)).fetchone()
        if not row or row["revoked_at"] or row["expires_at"] < time.time():
            return None
        if client_id and row["client_id"] != client_id:
            return None
        return row

    async def load_refresh_token(self, client: OAuthClientInformationFull,
                                 refresh_token: str) -> RefreshToken | None:
        row = self._token_row(refresh_token, "refresh", client.client_id)
        if not row:
            return None
        return RefreshToken(token=refresh_token, client_id=client.client_id,
                            scopes=json.loads(row["scopes"]), expires_at=int(row["expires_at"]),
                            resource=row["resource"],
                            subject=f'{row["user_id"]}:{row["workspace_id"]}')

    async def exchange_refresh_token(self, client: OAuthClientInformationFull,
                                     refresh_token: RefreshToken,
                                     scopes: list[str]) -> OAuthToken:
        row = self._token_row(refresh_token.token, "refresh", client.client_id)
        if not row:
            raise TokenError("invalid_grant", "refresh token is not valid")
        if not db.role_in(self.conn, row["workspace_id"], row["user_id"]):
            raise TokenError("invalid_grant", "no longer a member of that workspace")
        with db.LOCK, self.conn:
            # Rotation: the used refresh token and its access tokens end here.
            self.conn.execute("UPDATE oauth_tokens SET revoked_at=? WHERE grant_id=? AND"
                              " revoked_at IS NULL", (time.time(), row["grant_id"]))
        return self._issue(client.client_id, row["user_id"], row["workspace_id"],
                           scopes or json.loads(row["scopes"]), row["resource"],
                           row["grant_id"])

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self._token_row(token, "access")
        if not row:
            return None
        return AccessToken(token=token, client_id=row["client_id"],
                           scopes=json.loads(row["scopes"]), expires_at=int(row["expires_at"]),
                           resource=row["resource"],
                           subject=f'{row["user_id"]}:{row["workspace_id"]}')

    async def revoke_token(self, token) -> None:
        row = self.conn.execute("SELECT grant_id FROM oauth_tokens WHERE token_hash=?",
                                (_h(token.token),)).fetchone()
        if row:
            with db.LOCK, self.conn:
                self.conn.execute("UPDATE oauth_tokens SET revoked_at=? WHERE grant_id=? AND"
                                  " revoked_at IS NULL", (time.time(), row["grant_id"]))
