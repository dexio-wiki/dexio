"""Run Dexio as a stdio MCP server, for Glama's checks.

Glama starts a listed server over stdio (wrapped in mcp-proxy) and asks it for its tools.
Dexio serves MCP over HTTP, and every request needs a key. So this starts a copy of the
server on loopback, built from this checkout, with its own SQLite file and one account,
mints a key for that account, and relays each stdio message to the copy with the key.
Nothing leaves the machine, and everything it makes is deleted when it exits. Not for
production: run the server itself (deploy/README.md).

Glama build step: `uv sync --locked --extra server`.
Command: `uv run --no-sync python deploy/glama_stdio.py`.
"""
from __future__ import annotations

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
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


def replies(body: bytes, content_type: str) -> list[str]:
    """The JSON-RPC messages in one HTTP answer: a JSON body, or an event stream."""
    text = body.decode("utf-8").strip()
    if not text:
        return []
    if content_type.startswith("text/event-stream"):
        return [line[5:].strip() for line in text.splitlines() if line.startswith("data:")]
    return [json.dumps(json.loads(text))]


def relay(url: str, key: str) -> None:
    """One JSON-RPC message per stdin line, POSTed in order; answers go to stdout."""
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = urllib.request.Request(url, data=line.encode("utf-8"), headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                out = replies(r.read(), r.headers.get("Content-Type", ""))
        except urllib.error.HTTPError as e:
            out = replies(e.read(), e.headers.get("Content-Type", ""))
            if not out:
                msg = json.loads(line)
                if isinstance(msg, dict) and "id" in msg:
                    out = [json.dumps({"jsonrpc": "2.0", "id": msg["id"], "error": {
                        "code": -32603, "message": f"HTTP {e.code} from the server"}})]
        for reply in out:
            sys.stdout.write(reply + "\n")
        sys.stdout.flush()


def main() -> int:
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
        relay(f"{base}/mcp", mint_key(db_path))
        return 0
    finally:
        server.terminate()
        try:
            server.wait(timeout=10)
        except subprocess.TimeoutExpired:
            server.kill()
        shutil.rmtree(data, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
