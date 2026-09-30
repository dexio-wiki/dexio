"""Stale pages, unlinked mentions and wanted pages: the health module on its own,
then end to end through the MCP tools (wiki_health, read_page, write results)."""
from __future__ import annotations

import asyncio
import json
import socket
import threading
import time

import pytest

from dexio.health import Mentions, mention_summary, stale_after, stale_pages, wanted

DAY = 86400


# ---- the module ------------------------------------------------------------------

def test_stale_after_reads_the_okf_field():
    assert stale_after("---\nstale_after: 2026-09-23T00:00:00Z\n---\n# x\n") == 1790121600.0
    assert stale_after("---\nstale_after: '2026-01-02'  # review\n---\n") == 1767312000.0
    assert stale_after("---\nStale_After: 2026-01-02T08:00:00+08:00\n---\n") == 1767312000.0
    # Only in frontmatter, and only when it parses.
    assert stale_after("# x\n\nstale_after: 2020-01-01\n") is None
    assert stale_after("---\nstale_after: soon\n---\n") is None
    assert stale_after("---\ntitle: x\n---\n") is None


def test_stale_pages_past_date_or_behind_what_they_link_to():
    now = time.time()
    pages = [
        {"path": "hub", "updated_at": now - 200 * DAY, "stale_after": None},
        {"path": "leaf", "updated_at": now - 5 * DAY, "stale_after": None},
        {"path": "expired", "updated_at": now, "stale_after": now - 1},
        {"path": "future", "updated_at": now, "stale_after": now + DAY},
        {"path": "quiet", "updated_at": now - 400 * DAY, "stale_after": None},   # links nowhere
        {"path": "archive", "updated_at": now - 400 * DAY, "stale_after": None},  # links to older
    ]
    edges = [("hub", "leaf"), ("archive", "quiet"), ("hub", "hub")]
    out = {e["path"]: e for e in stale_pages(pages, edges, now)}
    assert set(out) == {"hub", "expired"}
    assert out["hub"]["changed_since"] == ["leaf"]
    assert out["hub"]["reason"].startswith("unchanged 200 days")
    assert out["expired"]["reason"] == "past its stale_after date"
    # A shorter window catches nothing new here: leaf links nowhere.
    assert {e["path"] for e in stale_pages(pages, edges, now, stale_days=1)} == {"hub", "expired"}


def test_mentions_whole_titles_outside_links_and_code():
    m = Mentions({"entities/dexio": "Dexio", "entities/dexio-company": "Dexio (company)",
                  "competitors/notion": "Notion", "concepts/okf": "Open Knowledge Format (OKF)",
                  "log": "Log", "competitors/tela": "tela", "a": "A", "x/readme": "README",
                  "y/readme": "Readme two", "z/readme": "Readme two"})
    text = ("---\ntitle: Notion\nsources: [Dexio]\n---\n# Page\n\n"
            "Dexio (company) is not Dexio. We had a notion, then Notion. The open knowledge\n"
            "format helps. See [[competitors/tela]] and [tela](competitors/tela.md) and tela.\n"
            "`Dexio` in code, <!-- Notion --> a comment. Log this. Stela is no tela match? It is.\n"
            "Readme two twice: readme two.\n")
    found = m.find(text, "p", set())
    # Longest title first (Dexio (company) before Dexio), each page once, in order;
    # a one-word title keeps its case; generic and shared titles are not matched.
    assert found == ["entities/dexio-company", "entities/dexio", "competitors/notion",
                     "concepts/okf", "competitors/tela"]
    assert m.find(text, "p", {"entities/dexio", "competitors/tela"}) == [
        "entities/dexio-company", "competitors/notion", "concepts/okf"]
    assert m.find(text, "entities/dexio", set(), limit=2) == ["entities/dexio-company",
                                                              "competitors/notion"]
    assert m.find("Nothing here.", "p", set()) == []
    assert Mentions({}).find(text, "p", set()) == []


def test_summaries_rank_most_mentioned_and_most_wanted():
    s = mention_summary({"a": ["x", "y"], "b": ["x"], "c": ["x", "z"]})
    assert s[0] == {"path": "x", "unlinked_on": 3, "for_example": ["a", "b", "c"]}
    assert [e["path"] for e in s] == ["x", "y", "z"]
    w = wanted([{"source": "a", "target": "t"}, {"source": "b", "target": "t"},
                {"source": "a", "target": "t"}, {"source": "c", "target": "u"}])
    assert w == [{"target": "t", "linked_from": 2, "for_example": ["a", "b"]},
                 {"target": "u", "linked_from": 1, "for_example": ["c"]}]


# ---- through MCP --------------------------------------------------------------------

pytest.importorskip("mcp")
pytest.importorskip("fastapi")

import httpx  # noqa: E402
import httpx2  # noqa: E402
import uvicorn  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402

from dexio.server import db  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

PAGES = {
    "index": "# Index\n\n- [[entities/acme]]\n- [[concepts/drift]]\n",
    "entities/acme": "# Acme\n\nA customer. See [[concepts/drift]] and [[entities/ghost]].\n",
    "concepts/drift": "# Calibration drift\n\nSensors drift about 2% a year. Back to [[index]].\n",
    "notes/old": "# Old summary\n\nWhat we knew about [[entities/acme]]. Also [[entities/ghost]].\n",
    "notes/review": "---\nstale_after: 2020-01-01\n---\n# Review me\n\nChecked once.\n",
}


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    app = get_app(str(tmp_path_factory.mktemp("health") / "t.db"))
    conn = app.state.conn
    token = db.create_token(conn, "fleet")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
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
    k = db.wiki_key(db.default_workspace(conn))
    db.commit_pages(conn, k, {p: db.make_page(p, t) for p, t in PAGES.items()},
                    source="test", author="fleet", op="push", is_push=True)
    # notes/old last changed 200 days ago; acme, which it links to, since.
    with conn:
        conn.execute("UPDATE pages SET updated_at=? WHERE project=? AND path=?",
                     (time.time() - 200 * DAY, k, "notes/old"))
    yield {"base": base, "token": token}
    srv.should_exit = True
    t.join(timeout=5)


def call(server, tool, args):
    async def go():
        headers = {"Authorization": f"Bearer {server['token']}"}
        async with httpx2.AsyncClient(headers=headers) as hc:
            async with streamable_http_client(f"{server['base']}/mcp", http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    return await s.call_tool(tool, {"wiki": "research", **args})
    res = asyncio.run(go())
    assert not res.is_error, res.content[0].text
    return res


def payload(res):
    return json.loads(res.content[0].text)


def test_health_lists_stale_wanted_and_unlinked(server):
    h = payload(call(server, "wiki_health", {}))
    assert h["problems"] == 2                                    # ghost, twice
    assert h["wanted"] == [{"target": "entities/ghost", "linked_from": 2,
                            "for_example": ["entities/acme", "notes/old"]}]
    stale = {e["path"]: e for e in h["stale"]}
    assert set(stale) == {"notes/old", "notes/review"}
    assert stale["notes/old"]["changed_since"] == ["entities/acme"]
    assert stale["notes/review"]["stale_after"] == "2020-01-01"
    assert h["stale_days"] == 90
    assert h["unlinked_mentions"] == []
    assert payload(call(server, "wiki_health", {"stale_days": 300}))["stale"] == [
        stale["notes/review"]]


def test_writes_and_reads_name_pages_left_unlinked(server):
    text = ("# Visit notes\n\nAcme asked about calibration drift again. [[index]] has the list."
            " Nothing on the Old summary yet.\n")
    w = payload(call(server, "write_page", {"path": "notes/visit", "text": text, "agent": "t"}))
    assert w["unlinked_mentions"] == ["entities/acme", "concepts/drift", "notes/old"]
    r = payload(call(server, "read_page", {"path": "notes/visit"}))
    assert r["unlinked_mentions"] == ["entities/acme", "concepts/drift", "notes/old"]
    # Linking one takes it off the list.
    e = payload(call(server, "edit_page", {"path": "notes/visit", "old_text": "Acme asked",
                                           "new_text": "[[entities/acme|Acme]] asked",
                                           "agent": "t"}))
    assert e["unlinked_mentions"] == ["concepts/drift", "notes/old"]
    # An append reports only what the appended text names.
    a = payload(call(server, "append_page", {"path": "notes/visit",
                                              "text": "Old summary needs a pass.", "agent": "t"}))
    assert a["unlinked_mentions"] == ["notes/old"]
    a = payload(call(server, "append_page", {"path": "notes/visit", "text": "Done.",
                                              "agent": "t"}))
    assert "unlinked_mentions" not in a
    h = payload(call(server, "wiki_health", {}))
    assert {"path": "concepts/drift", "unlinked_on": 1, "for_example": ["notes/visit"]} in \
        h["unlinked_mentions"]
    # A page whose text names nothing unlinked has no key at all.
    r = payload(call(server, "read_page", {"path": "concepts/drift"}))
    assert "unlinked_mentions" not in r
    # Nor does an old revision.
    rev = payload(call(server, "page_history", {"path": "notes/visit"}))["revisions"][-1]
    r = payload(call(server, "read_page", {"path": "notes/visit", "revision": rev["revision"]}))
    assert "unlinked_mentions" not in r
