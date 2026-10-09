"""Run Dexio as a stdio MCP server, for Glama's checks.

Glama starts a listed server over stdio (wrapped in mcp-proxy) and asks it for its tools.
Dexio serves MCP over HTTP, and every request needs a key. So this starts a throwaway
copy on loopback with its own SQLite file and one account, mints a key for that account,
and hands stdio to mcp-remote, which forwards each message to the copy with the key.
Everything it makes is deleted when it exits. Not for production: run the server itself
(deploy/README.md).

Glama build steps: `uv sync --locked --extra server` and `npm install -g mcp-remote@0.14.3`.
Command: `uv run --no-sync python deploy/glama_stdio.py`.
"""
from __future__ import annotations

import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

EMAIL = "glama-check@example.com"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_healthy(url: str, server: subprocess.Popen, seconds: float = 60) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if server.poll() is not None:
            sys.exit(f"dexio serve exited with {server.returncode}")
        try:
            if urllib.request.urlopen(url, timeout=2).status == 200:
                return
        except OSError:
            pass
        time.sleep(0.3)
    sys.exit(f"dexio serve did not answer {url} within {seconds:.0f}s")


def mint_key(db_path: str) -> str:
    from dexio.server import auth, db

    conn = db.connect(db_path)
    user = auth.user_by_email(conn, EMAIL)
    if not user:
        sys.exit("the seeded account is missing")
    return db.create_token(conn, "Glama check", None, user["id"])


def main() -> int:
    bridge = shutil.which("mcp-remote")
    if not bridge:
        sys.exit("mcp-remote is not installed: npm install -g mcp-remote")
    # A stop signal unwinds through the cleanup below instead of skipping it.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit(0))
    data = tempfile.mkdtemp(prefix="dexio-")
    db_path = os.path.join(data, "dexio.db")
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    env = dict(os.environ, DEXIO_DB=db_path, DEXIO_PUBLIC_URL=base,
               DEXIO_ADMIN_EMAIL=EMAIL, DEXIO_ADMIN_PASSWORD=secrets.token_urlsafe(24))
    # stdout carries the MCP messages, so the server's output goes to stderr.
    server = subprocess.Popen(
        [sys.executable, "-m", "dexio.cli", "serve", "--host", "127.0.0.1",
         "--port", str(port), "--log-level", "warning"],
        env=env, stdin=subprocess.DEVNULL, stdout=sys.stderr, stderr=sys.stderr)
    try:
        wait_healthy(f"{base}/healthz", server)
        key = mint_key(db_path)
        return subprocess.call(
            [bridge, f"{base}/mcp", "--transport", "http-only", "--allow-http",
             "--header", f"Authorization: Bearer {key}"],
            env=env)
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
