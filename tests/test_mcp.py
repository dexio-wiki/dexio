"""End-to-end MCP tests: a real uvicorn server, a real MCP client over streamable HTTP."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

pytest.importorskip("mcp")
pytest.importorskip("fastapi")

import httpx  # noqa: E402
import httpx2  # noqa: E402
import uvicorn  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from dexio.server import db  # noqa: E402
from dexio.server.mcp_server import CHANGE_TOOLS  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

PAGES = {
    "index": "# Index\n\n- [[entities/acme]]\n- [[concepts/drift]]\n",
    "entities/acme": "# Acme\n\nA customer. See [[concepts/drift]] and [[entities/ghost]].\n",
    "concepts/drift": "# Calibration drift\n\nSensors drift about 2% a year. Back to [[index]].\n",
    "notes/loose": "# Loose note\n\nNothing links here and it links nowhere.\n",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    path = tmp_path_factory.mktemp("mcp") / "t.db"
    app = get_app(str(path))
    conn = app.state.conn
    fleet = db.create_token(conn, "fleet")                     # sees every wiki
    port = _free_port()
    srv = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            if httpx.get(f"{base}/healthz").status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.05)
    db.commit_pages(conn, db.wiki_key(db.default_workspace(conn)),
                    {p: db.make_page(p, t) for p, t in PAGES.items()},
                    source="test", author="fleet", op="push", is_push=True)
    yield {"base": base, "fleet": fleet}
    srv.should_exit = True
    t.join(timeout=5)


def call(server, token, tool=None, args=None):
    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}) as hc:
            async with streamable_http_client(f"{server['base']}/mcp", http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    if tool is None:
                        return await s.list_tools()
                    return await s.call_tool(tool, with_agent(tool, args))
    return asyncio.run(go())


def with_agent(tool, args):
    """Every change tool requires agent; tests that are not about it pass one."""
    args = dict(args or {})
    if tool in CHANGE_TOOLS:
        args.setdefault("agent", "test-agent")
    return args


def payload(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content
    data = json.loads(result.content[0].text)
    if len(result.content) > 1:           # read_page: header, then the page as plain text
        data["text"] = result.content[1].text
    return data


def test_rejects_missing_or_bad_token(server):
    assert httpx.post(f"{server['base']}/mcp", json={}).status_code == 401
    r = httpx.post(f"{server['base']}/mcp", json={}, headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    assert "bearer" in r.headers["www-authenticate"].lower()


def test_get_and_delete_answer_405_instead_of_hanging(server):
    auth = {"Authorization": f"Bearer {server['fleet']}", "Accept": "text/event-stream"}
    for method in ("GET", "DELETE"):
        r = httpx.request(method, f"{server['base']}/mcp", headers=auth, timeout=5)
        assert r.status_code == 405
        assert r.headers["allow"] == "POST"
    # Auth still comes first.
    assert httpx.get(f"{server['base']}/mcp", timeout=5).status_code == 401


def test_lists_tools(server):
    names = {t.name for t in call(server, server["fleet"]).tools}
    # A workspace has one wiki (2026-09-28): no tool lists, creates or renames wikis.
    assert names == {"list_pages", "wiki_health", "read_page", "search_pages",
                     "page_history", "write_page", "edit_page", "append_page", "delete_page",
                     "move_page", "change_pages", "upload_file", "list_files", "delete_file",
                     "set_visibility"}


def test_list_and_health(server):
    assert payload(call(server, server["fleet"], "list_pages"))["count"] == 4
    # Only the broken link is a problem; notes/loose (no links) is information.
    h = payload(call(server, server["fleet"], "wiki_health"))
    assert h["problems"] == 1
    assert h["orphaned"] == ["notes/loose"]
    assert h["dangling"] == [{"source": "entities/acme", "target": "entities/ghost"}]


def test_read_and_search(server):
    p = payload(call(server, server["fleet"], "read_page",
                     {"path": "entities/acme.md"}))
    assert p["links_out"] == ["concepts/drift"] and p["links_in"] == ["index"]
    assert p["broken_links"] == ["entities/ghost"]
    s = payload(call(server, server["fleet"], "search_pages",
                     {"query": "2% a year"}))
    assert s["results"][0]["path"] == "concepts/drift"
    assert s["results"][0]["matches"][0] == {"line": 3, "text": "Sensors drift about 2% a year. Back to [[index]]."}


def test_write_page_reports_broken_links_and_rebuilds(server):
    w = payload(call(server, server["fleet"], "write_page", {
        "path": "entities/ghost",
        "text": "# Ghost\n\nNow exists. Linked to [[entities/acme]] and [[entities/phantom]]."}))
    assert w["created"] is True and w["broken_links"] == ["entities/phantom"]
    h = payload(call(server, server["fleet"], "wiki_health"))
    targets = [d["target"] for d in h["dangling"]]
    assert "entities/ghost" not in targets and "entities/phantom" in targets


def test_path_traversal_refused(server):
    r = call(server, server["fleet"], "write_page",
             {"path": "../etc/passwd", "text": "x"})
    assert r.is_error and "bad page path" in r.content[0].text


def test_rest_routes_still_win(server):
    assert httpx.get(f"{server['base']}/healthz").json()["ok"] is True
