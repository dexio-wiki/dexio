"""The MCP tools that let the wiki live on the server alone: list, regex search,
in-place edits, appends, delete, move with link rewriting, page history, version
checks, and concurrent writers. Real uvicorn, real MCP client."""
from __future__ import annotations

import asyncio
import json
import socket
import sqlite3
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
from dexio.server.app import get_app  # noqa: E402
from dexio.server.mcp_server import CHANGE_TOOLS  # noqa: E402

PAGES = {
    "index": "# Index\n\n- [[entities/acme|Acme Corp]]\n- [[concepts/drift#rate]]\n- [Beta](entities/beta.md)\n",
    "entities/acme": "# Acme\n\nA customer. See [[concepts/drift]].\nAcme renews in March.\nACME is loud.\n",
    "entities/beta": "# Beta\n\nPartner of [[acme]]. Owes 40 units.\n",
    "concepts/drift": "# Calibration drift\n\nSensors drift about 2% a year.\n",
    "log": "# Log\n\n## [2026-09-01] start\n",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed(conn, project, pages, workspace_id=None):
    """Load `pages` into a wiki straight into the database, the state our own and
    a customer's wikis arrived in (by the bulk push removed 2026-09-27)."""
    ws = db.default_workspace(conn) if workspace_id is None else workspace_id
    # A workspace has one wiki (2026-09-28): `project` names nothing now.
    return db.commit_pages(conn, db.wiki_key(ws),
                           {p: db.make_page(p, t) for p, t in pages.items()},
                           source="test", author="fleet", op="push", is_push=True)


@pytest.fixture()
def server(tmp_path):
    app = get_app(str(tmp_path / "t.db"))
    conn = app.state.conn
    fleet = db.create_token(conn, "fleet")
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
    _seed(conn, "kb", PAGES)
    yield {"base": base, "fleet": fleet, "conn": conn}
    srv.should_exit = True
    t.join(timeout=5)


def call(server, tool, args=None, token=None):
    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {token or server['fleet']}"}) as hc:
            async with streamable_http_client(f"{server['base']}/mcp", http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    return await s.call_tool(tool, with_agent(tool, {"wiki": "kb", **(args or {})}))
    return asyncio.run(go())


OMIT = object()


def with_agent(tool, args):
    """Every change tool requires agent; tests that are not about it pass one.
    agent=OMIT sends no agent at all, as a client with an old tool list would;
    wiki=OMIT likewise sends no wiki."""
    args = dict(args)
    if tool in CHANGE_TOOLS:
        args.setdefault("agent", "test-agent")
    return {k: v for k, v in args.items() if v is not OMIT}


def ok(result):
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content
    data = json.loads(result.content[0].text)
    if len(result.content) > 1:           # read_page: header, then the page as plain text
        data["text"] = result.content[1].text
    return data


def err(result) -> str:
    assert result.is_error
    return result.content[0].text


def text_of(server, path):
    return ok(call(server, "read_page", {"path": path}))["text"]


def test_list_pages_all_and_by_folder(server):
    r = ok(call(server, "list_pages"))
    assert r["count"] == 5
    assert [p["path"] for p in r["pages"]] == sorted(PAGES)
    assert r["pages"][0]["updated"].endswith("Z")
    r = ok(call(server, "list_pages", {"folder": "entities/"}))
    assert [p["path"] for p in r["pages"]] == ["entities/acme", "entities/beta"]


def test_list_pages_whole_listing_has_no_paging_keys(server):
    r = ok(call(server, "list_pages"))
    assert set(r) == {"folder", "count", "pages"}


def test_any_wiki_argument_reaches_the_one_wiki(server):
    """A workspace has one wiki (2026-09-28), so no tool takes `wiki`. Agents set
    up before then still send one; it is ignored, so a call naming any wiki reads
    and writes the same pages as a call naming none."""
    ok(call(server, "write_page", {"wiki": "anything-else", "path": "notes/w", "text": "# W\n"}))
    got = ok(call(server, "read_page", {"wiki": OMIT, "path": "notes/w"}))
    assert got["text"] == "# W\n" and "wiki" not in got
    ok(call(server, "append_page", {"wiki": OMIT, "path": "notes/w", "text": "more"}))
    assert text_of(server, "notes/w") == "# W\nmore"

    def paths(wiki):
        return [p["path"] for p in ok(call(server, "list_pages", {"wiki": wiki}))["pages"]]
    assert paths("anything-else") == paths(OMIT) == sorted([*PAGES, "notes/w"])


def test_list_pages_pages_through_a_large_wiki(server):
    for i in range(4):
        ok(call(server, "write_page", {"path": f"notes/deep/n{i}", "text": f"# N{i}\n"}))
    everything = [p["path"] for p in ok(call(server, "list_pages"))["pages"]]
    assert len(everything) == 9

    first = ok(call(server, "list_pages", {"limit": 4}))
    assert first["count"] == 9 and first["next_offset"] == 4
    assert [p["path"] for p in first["pages"]] == everything[:4]
    # The folders one level down, with pages nested deeper counted in.
    assert first["folders"] == [{"folder": "concepts", "pages": 1},
                                {"folder": "entities", "pages": 2},
                                {"folder": "notes", "pages": 4}]
    assert "pages 1 to 4 of 9" in first["hint"] and "offset=4" in first["hint"]

    second = ok(call(server, "list_pages", {"limit": 4, "offset": 4}))
    assert [p["path"] for p in second["pages"]] == everything[4:8]
    last = ok(call(server, "list_pages", {"limit": 4, "offset": 8}))
    assert [p["path"] for p in last["pages"]] == everything[8:]
    assert "next_offset" not in last and "pages 9 to 9 of 9." in last["hint"]

    past = ok(call(server, "list_pages", {"offset": 50}))
    assert past["pages"] == [] and past["count"] == 9 and "there are 9" in past["hint"]


def test_list_pages_pages_one_folder(server):
    for i in range(3):
        ok(call(server, "write_page", {"path": f"notes/deep/n{i}", "text": f"# N{i}\n"}))
    ok(call(server, "write_page", {"path": "notes/top", "text": "# Top\n"}))
    r = ok(call(server, "list_pages", {"folder": "notes", "limit": 2}))
    assert r["count"] == 4 and r["next_offset"] == 2
    assert r["folders"] == [{"folder": "notes/deep", "pages": 3}]
    flat = ok(call(server, "list_pages", {"folder": "notes/deep", "limit": 1}))
    assert flat["folders"] == [] and "one of these folders" not in flat["hint"]


def test_list_pages_limit_is_clamped(server):
    assert len(ok(call(server, "list_pages", {"limit": 0}))["pages"]) == 1
    assert len(ok(call(server, "list_pages", {"limit": 10**6}))["pages"]) == 5
    assert ok(call(server, "list_pages", {"offset": -3}))["pages"][0]["path"] == \
        sorted(PAGES)[0]


def test_list_pages_describes_each_page(server):
    ok(call(server, "write_page", {"path": "notes/fm", "text":
        "---\ntitle: FM\ndescription: \"Where the **relay** runs, and how to restart it.\"\n---\n"
        "# FM\n\nIgnored first sentence.\n"}))
    ok(call(server, "write_page", {"path": "notes/bare", "text": "# Bare\n\n```\ncode\n```\n"}))
    pages = {p["path"]: p for p in ok(call(server, "list_pages"))["pages"]}
    assert pages["entities/acme"]["description"] == "A customer."
    assert pages["entities/beta"]["description"] == "Partner of acme."
    assert pages["concepts/drift"]["description"] == "Sensors drift about 2% a year."
    assert pages["index"]["description"] == "Acme Corp"          # a list's first item
    assert pages["notes/fm"]["description"] == "Where the relay runs, and how to restart it."
    assert "description" not in pages["notes/bare"]              # no prose, no key
    assert "description" not in pages["log"]


def test_server_offers_no_prompts_or_resources(server):
    """Empty prompt and resource capabilities cost clients tools (Hermes adds four),
    so the server advertises only tools and answers those methods "not found"."""
    from mcp.shared.exceptions import MCPError

    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {server['fleet']}"}) as hc:
            async with streamable_http_client(f"{server['base']}/mcp", http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    init = await s.initialize()
                    errors = []
                    for fn in (s.list_prompts, s.list_resources):
                        try:
                            await fn()
                        except MCPError as e:
                            errors.append(e.error.code)
                    return init.capabilities, errors
    caps, errors = asyncio.run(go())
    assert caps.tools is not None
    assert caps.prompts is None and caps.resources is None
    assert errors == [-32601, -32601]


def test_search_every_line_regex_folder_and_ranking(server):
    r = ok(call(server, "search_pages", {"query": "acme"}))
    first = r["results"][0]
    assert first["path"] == "entities/acme" and first["path_or_title_match"]
    assert first["match_count"] == 3
    assert [m["line"] for m in first["matches"]] == [1, 4, 5]
    r = ok(call(server, "search_pages", {"query": "acme", "case_sensitive": True}))
    # entities/acme is listed for its path even though its text only has Acme and ACME.
    assert {h["path"]: h["match_count"] for h in r["results"]} == {
        "entities/acme": 0, "index": 1, "entities/beta": 1}
    r = ok(call(server, "search_pages", {"query": r"\d+ units|\d% a year", "regex": True}))
    assert sorted(h["path"] for h in r["results"]) == ["concepts/drift", "entities/beta"]
    r = ok(call(server, "search_pages", {"query": "a", "folder": "concepts"}))
    assert [h["path"] for h in r["results"]] == ["concepts/drift"]
    assert "bad regex" in err(call(server, "search_pages", {"query": "(", "regex": True}))
    r = ok(call(server, "search_pages", {"query": "((", "regex": False}))
    assert r["results"] == []


def test_edit_page_exact_unique_replace_all_and_errors(server):
    r = ok(call(server, "edit_page", {"path": "entities/acme", "old_text": "renews in March",
                                      "new_text": "renews in April"}))
    assert r["replacements"] == 1
    assert "renews in April" in text_of(server, "entities/acme")
    assert "not found" in err(call(server, "edit_page", {
        "path": "entities/acme", "old_text": "renews in May", "new_text": "x"}))
    call(server, "write_page", {"path": "notes/dup", "text": "# Dup\n\nfoo foo foo\n"})
    assert "appears 3 times" in err(call(server, "edit_page", {
        "path": "notes/dup", "old_text": "foo", "new_text": "bar"}))
    r = ok(call(server, "edit_page", {"path": "notes/dup", "old_text": "foo", "new_text": "bar",
                                      "replace_all": True}))
    assert r["replacements"] == 3 and text_of(server, "notes/dup") == "# Dup\n\nbar bar bar\n"
    assert "no page" in err(call(server, "edit_page", {
        "path": "nope", "old_text": "a", "new_text": "b"}))


def test_base_version_stops_lost_updates(server):
    v = ok(call(server, "read_page", {"path": "entities/beta"}))["version"]
    ok(call(server, "edit_page", {"path": "entities/beta", "old_text": "40", "new_text": "41",
                                  "base_version": v}))
    msg = err(call(server, "write_page", {"path": "entities/beta", "text": "# Beta\n\nstale\n",
                                          "base_version": v}))
    assert "changed since you read it" in msg
    assert "41 units" in text_of(server, "entities/beta")


def test_append_page_adds_a_line_and_creates(server):
    r = ok(call(server, "append_page", {"path": "log", "text": "## [2026-09-02] next\n"}))
    assert r["created"] is False
    assert text_of(server, "log") == "# Log\n\n## [2026-09-01] start\n## [2026-09-02] next\n"
    r = ok(call(server, "append_page", {"path": "notes/new", "text": "# New"}))
    assert r["created"] is True
    ok(call(server, "append_page", {"path": "notes/new", "text": "second"}))
    assert text_of(server, "notes/new") == "# New\nsecond"


def list_tools(server):
    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {server['fleet']}"}) as hc:
            async with streamable_http_client(f"{server['base']}/mcp", http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    return (await s.list_tools()).tools
    return asyncio.run(go())


def test_every_change_tool_requires_agent(server):
    tools = {t.name: t.input_schema for t in list_tools(server)}
    for name in CHANGE_TOOLS:
        schema = tools[name]
        assert "agent" in schema["required"], name
        assert "agent name" in schema["properties"]["agent"]["description"].lower()
    for name in ("read_page", "search_pages", "list_pages"):
        assert "agent" not in tools[name].get("properties", {}), name
    # page_history takes agent too, as an optional filter.
    assert "agent" not in tools["page_history"].get("required", [])
    # A client with an old tool list sends none: told what to do, nothing written.
    before = text_of(server, "log")
    for agent in (OMIT, "", "   "):
        msg = err(call(server, "append_page", {"path": "log", "text": "x", "agent": agent}))
        assert "agent is required" in msg and "reconnect" in msg
    assert "longer than 64" in err(call(server, "write_page", {
        "path": "notes/x", "text": "# X", "agent": "a" * 65}))
    assert text_of(server, "log") == before
    assert "no page" in err(call(server, "read_page", {"path": "notes/x"}))


def test_history_shows_the_agent_and_the_token(server):
    ok(call(server, "append_page", {"path": "log", "text": "one", "agent": "niko",
                                    "note": "first"}))
    ok(call(server, "edit_page", {"path": "log", "old_text": "one", "new_text": "two",
                                  "agent": "  Claude   Code "}))
    ok(call(server, "change_pages", {"agent": "scout", "changes": [
        {"op": "append", "path": "log", "text": "three"},
        {"op": "write", "path": "notes/b", "text": "# B"}]}))
    h = ok(call(server, "page_history", {"path": "log"}))["revisions"]
    assert [(x["op"], x["agent"]) for x in h] == [
        ("append", "scout"), ("edit", "Claude Code"), ("append", "niko"), ("push", "")]
    assert h[0]["author"] == h[2]["author"] == "fleet"
    assert h[2]["note"] == "first"
    feed = ok(call(server, "page_history", {}))["changes"]
    assert {(c["path"], c["agent"]) for c in feed[:2]} == {("log", "scout"), ("notes/b", "scout")}
    old = ok(call(server, "read_page", {"path": "log", "revision": h[2]["revision"]}))
    assert old["agent"] == "niko" and old["author"] == "fleet"


def test_server_reports_its_version_and_healthz_none(server):
    from dexio import VERSION

    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {server['fleet']}"}) as hc:
            async with streamable_http_client(f"{server['base']}/mcp", http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    return await s.initialize()
    info = asyncio.run(go()).server_info
    assert info.version == VERSION
    health = httpx.get(f"{server['base']}/healthz")
    assert health.json() == {"ok": True}
    # No generated API docs or schema are served.
    for path in ("/openapi.json", "/api/v1/docs", "/docs", "/redoc"):
        assert httpx.get(f"{server['base']}{path}").status_code == 404, path


def test_version_is_fixed_whatever_the_environment_says():
    """Forrest, 2026-09-27: the served version is a fixed string, never bumped."""
    import subprocess
    import sys
    from pathlib import Path

    import dexio
    assert dexio.VERSION == "1.0.0"
    here = str(Path(dexio.__file__).resolve().parent.parent)     # this checkout's dexio
    out = subprocess.run([sys.executable, "-c", "import dexio; print(dexio.VERSION)"],
                         env={"DEXIO_VERSION": "1.0.7", "PATH": "", "PYTHONPATH": here},
                         capture_output=True, text=True, check=True).stdout.strip()
    assert out == "1.0.0"


def test_delete_page_reports_broken_links_and_keeps_history(server):
    r = ok(call(server, "delete_page", {"path": "entities/beta"}))
    assert r["now_broken_on"] == ["index"]
    assert "no page" in err(call(server, "read_page", {"path": "entities/beta"}))
    h = ok(call(server, "page_history", {"path": "entities/beta"}))["revisions"]
    assert [x["op"] for x in h] == ["delete", "push"] and h[0]["deleted"]
    # The delete says which revision to restore from and how.
    assert r["restore_revision"] == h[1]["revision"]
    assert r["restore"] == (f"read_page with revision={h[1]['revision']},"
                            " then write_page with that text")
    old = ok(call(server, "read_page", {"path": "entities/beta", "revision": r["restore_revision"]}))
    assert old["text"] == PAGES["entities/beta"]
    # Restore by writing the old text back.
    ok(call(server, "write_page", {"path": "entities/beta", "text": old["text"]}))
    assert ok(call(server, "read_page", {"path": "index"}))["broken_links"] == []


def test_restore_point_is_the_last_text_before_the_latest_delete(server):
    ok(call(server, "edit_page", {"path": "concepts/drift", "old_text": "2%", "new_text": "3%"}))
    first = ok(call(server, "delete_page", {"path": "concepts/drift"}))
    assert "3%" in ok(call(server, "read_page", {"path": "concepts/drift",
                                                  "revision": first["restore_revision"]}))["text"]
    ok(call(server, "write_page", {"path": "concepts/drift", "text": "# Drift\n\nRewritten.\n"}))
    second = ok(call(server, "delete_page", {"path": "concepts/drift"}))
    assert second["restore_revision"] > first["restore_revision"]
    back = ok(call(server, "read_page", {"path": "concepts/drift",
                                         "revision": second["restore_revision"]}))
    assert back["text"] == "# Drift\n\nRewritten.\n"


def test_change_pages_deletes_say_how_to_restore(server):
    r = ok(call(server, "change_pages", {"changes": [
        {"op": "delete", "path": "entities/beta"},
        {"op": "write", "path": "notes/new", "text": "# New\n"},
        {"op": "delete", "path": "notes/new"},
        {"op": "delete", "path": "log"},
        {"op": "write", "path": "log", "text": "# Log\n\nStarted over.\n"}]}))
    res = r["results"]
    old = ok(call(server, "read_page", {"path": "entities/beta",
                                        "revision": res[0]["restore_revision"]}))
    assert old["text"] == PAGES["entities/beta"] and "then write_page" in res[0]["restore"]
    assert "restore_revision" not in res[2]      # never existed before this step
    assert "restore_revision" not in res[3]      # written again in the same step
    dry = ok(call(server, "change_pages", {"dry_run": True, "changes": [
        {"op": "delete", "path": "entities/acme"}]}))
    assert "restore_revision" not in dry["results"][0]


def test_move_page_rewrites_links_with_alias_anchor_and_md(server):
    r = ok(call(server, "move_page", {"path": "entities/acme", "new_path": "_archive/acme"}))
    assert r["pages_updated"] == ["entities/beta", "index"] and r["links_rewritten"] == 2
    idx = text_of(server, "index")
    assert "[[_archive/acme|Acme Corp]]" in idx
    assert "[[_archive/acme]]" in text_of(server, "entities/beta")
    r = ok(call(server, "move_page", {"path": "concepts/drift", "new_path": "concepts/sensor-drift"}))
    idx = text_of(server, "index")
    assert "[[concepts/sensor-drift#rate]]" in idx
    ok(call(server, "move_page", {"path": "entities/beta", "new_path": "partners/beta"}))
    assert "[Beta](partners/beta.md)" in text_of(server, "index")
    h = ok(call(server, "wiki_health"))
    assert h["dangling"] == []
    assert "already exists" in err(call(server, "move_page", {"path": "index", "new_path": "log"}))
    assert "no page" in err(call(server, "move_page", {"path": "entities/acme", "new_path": "x"}))


def test_move_leaves_link_examples_in_code_alone(server):
    ok(call(server, "write_page", {"path": "notes/howto", "text": (
        "# How to link\n\nWrite `[[entities/beta]]` to link, like this: [[entities/beta]].\n"
        "```\n[Beta](entities/beta.md)\n```\n")}))
    r = ok(call(server, "move_page", {"path": "entities/beta", "new_path": "partners/beta"}))
    assert "notes/howto" in r["pages_updated"]
    assert text_of(server, "notes/howto") == (
        "# How to link\n\nWrite `[[entities/beta]]` to link, like this: [[partners/beta]].\n"
        "```\n[Beta](entities/beta.md)\n```\n")
    assert ok(call(server, "read_page", {"path": "notes/howto"}))["broken_links"] == []


def test_move_without_link_updates_leaves_them_broken(server):
    r = ok(call(server, "move_page", {"path": "concepts/drift", "new_path": "c/d",
                                      "update_links": False}))
    assert r["links_rewritten"] == 0
    dangling = {d["target"] for d in ok(call(server, "wiki_health"))["dangling"]}
    assert "concepts/drift" in dangling


def test_concurrent_writers_do_not_drop_each_others_pages(server):
    errors = []

    def one(i):
        try:
            if i % 2:
                ok(call(server, "write_page", {"path": f"c/p{i}", "text": f"# P{i}\n"}))
            else:
                ok(call(server, "append_page", {"path": "log", "text": f"line {i}"}))
        except Exception as e:  # pragma: no cover - reported below
            errors.append(repr(e))

    threads = [threading.Thread(target=one, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not errors
    paths = {p["path"] for p in ok(call(server, "list_pages", {"folder": "c"}))["pages"]}
    assert paths == {f"c/p{i}" for i in range(1, 16, 2)}
    log = text_of(server, "log")
    assert all(f"line {i}" in log for i in range(0, 16, 2))


@pytest.mark.sqlite_only
def test_old_database_upgrades_in_place(tmp_path):
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript("""
    CREATE TABLE projects (name TEXT PRIMARY KEY, source TEXT, updated_at REAL NOT NULL,
      pages INTEGER NOT NULL DEFAULT 0, links INTEGER NOT NULL DEFAULT 0,
      words INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE pages (project TEXT NOT NULL, path TEXT NOT NULL, file TEXT NOT NULL,
      title TEXT NOT NULL, folder TEXT NOT NULL, words INTEGER NOT NULL,
      degree INTEGER NOT NULL DEFAULT 0, text TEXT NOT NULL, PRIMARY KEY (project, path));
    CREATE TABLE pushes (id INTEGER PRIMARY KEY, project TEXT NOT NULL, source TEXT,
      at REAL NOT NULL, pages INTEGER, links INTEGER, token_name TEXT);
    INSERT INTO projects VALUES ('kb', 'mac', 1000.0, 1, 0, 2);
    INSERT INTO pages VALUES ('kb', 'a', 'a.md', 'A', '', 2, 0, '# A');
    INSERT INTO pushes VALUES (1, 'kb', 'mac', 1000.0, 1, 0, 'host');
    """)
    old.commit()
    old.close()
    conn = db.connect(path)
    # The wiki moved into the default workspace as its one wiki, "<workspace>:main".
    k = db.wiki_key(db.default_workspace(conn))
    assert db.note(conn, k, "a")["updated_at"] == 1000.0
    proj = db.projects(conn)[0]
    assert proj["name"] == "main" and proj["key"] == k and proj["last_push_at"] == 1000.0
    pages = db.load_pages(conn, k)
    pages["a"] = db.make_page("a", "# A\n\nedited")
    db.commit_pages(conn, k, pages, source="mcp", author="t", op="edit")
    revs = db.revisions(conn, k, "a")
    assert [r["op"] for r in revs] == ["edit", "baseline"]
    assert db.revision(conn, k, revs[1]["id"])["text"] == "# A"
    assert db.revision(conn, k, revs[0]["id"])["text"] == "# A\n\nedited"
    assert conn.execute("SELECT COUNT(*) FROM pushes WHERE project=?", (k,)).fetchone()[0] == 1
