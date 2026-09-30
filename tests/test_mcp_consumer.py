"""What an agent needs to reorganise a wiki through MCP alone: read and edit by
section, the page as plain text, long-page checks, change notes, several changes
as one all-or-nothing step with a dry run, and one search result per line.
Written after an agent had to leave the tools for a script to split a long page
(2026-09-26). Real uvicorn, real MCP client."""
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

from dexio import parse  # noqa: E402
from dexio.server import db  # noqa: E402
from dexio.server.app import get_app  # noqa: E402
from dexio.server.mcp_server import CHANGE_TOOLS  # noqa: E402

PRODUCT = """---
title: Product
tags: [a]
# not a heading: YAML comment
---

# Product

Intro with a "quoted" word and a tab\there.

## Pricing

Team $10. Basic Memory is $15.

### Anchor

Basic Memory again.

## Competitors

- [[entities/hjarni]] is close.
- Basic Memory syncs files.

```
## not a heading, inside code
```

## Notes

Last section.
"""

PAGES = {
    "index": "# Index\n\n- [[product]]\n- [[entities/hjarni]]\n",
    "product": PRODUCT,
    "entities/hjarni": "# Hjarni\n\nHosted notes. Back to [[product]].\n",
    "log": "# Log\n",
}


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


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
    db.commit_pages(conn, db.wiki_key(db.default_workspace(conn)),
                    {p: db.make_page(p, t) for p, t in PAGES.items()},
                    source="test", author="fleet", op="push", is_push=True)
    yield {"base": base, "fleet": fleet, "conn": conn}
    srv.should_exit = True
    t.join(timeout=5)


def raw(server, tool, args=None):
    async def go():
        async with httpx2.AsyncClient(headers={"Authorization": f"Bearer {server['fleet']}"}) as hc:
            async with streamable_http_client(f"{server['base']}/mcp", http_client=hc) as streams:
                async with ClientSession(streams[0], streams[1]) as s:
                    await s.initialize()
                    args_ = {"wiki": "kb", **(args or {})}
                    if tool in CHANGE_TOOLS:
                        args_.setdefault("agent", "test-agent")
                    return await s.call_tool(tool, args_)
    return asyncio.run(go())


def ok(server, tool, args=None) -> dict:
    result = raw(server, tool, args)
    assert not result.is_error, result.content
    if result.structured_content is not None:
        return result.structured_content
    data = json.loads(result.content[0].text)
    if len(result.content) > 1:
        data["text"] = result.content[1].text
    return data


def err(server, tool, args=None) -> str:
    result = raw(server, tool, args)
    assert result.is_error, result.content
    return result.content[0].text


def text_of(server, path) -> str:
    return ok(server, "read_page", {"path": path})["text"]


# ---- markdown structure (parse.py) --------------------------------------
def test_outline_skips_frontmatter_code_and_keeps_closing_hash_rules():
    heads = parse.outline(PRODUCT)
    assert [(h["level"], h["heading"]) for h in heads] == [
        (1, "Product"), (2, "Pricing"), (3, "Anchor"), (2, "Competitors"), (2, "Notes")]
    assert heads[0]["line"] == 7
    text = "# C#\n\n## Closed ##\n\n#nospace\n"
    assert [h["heading"] for h in parse.outline(text)] == ["C#", "Closed"]


def test_find_section_bounds_levels_and_errors():
    first, last, head = parse.find_section(PRODUCT, "pricing")
    assert parse.line_slice(PRODUCT, first, last).startswith("## Pricing\n")
    assert "### Anchor" in parse.line_slice(PRODUCT, first, last)       # subsections included
    assert "## Competitors" not in parse.line_slice(PRODUCT, first, last)
    first, last, _ = parse.find_section(PRODUCT, "Notes")
    assert last == len(parse.keep_lines(PRODUCT))                        # runs to the end
    assert parse.find_section(PRODUCT, str(first))[0] == first           # by line number
    with pytest.raises(ValueError, match="the page's headings"):
        parse.find_section(PRODUCT, "Missing")
    dup = "# A\n\n## Notes\n\nx\n\n# B\n\n## Notes\n\ny\n"
    with pytest.raises(ValueError, match="2 headings match"):
        parse.find_section(dup, "Notes")
    assert parse.find_section(dup, "9")[0] == 9
    with pytest.raises(ValueError, match="not a heading"):
        parse.find_section(dup, "2")


def test_replace_lines_keeps_one_blank_line_and_can_remove():
    first, last, _ = parse.find_section(PRODUCT, "Competitors")
    out = parse.replace_lines(PRODUCT, first, last, "## Competitors\n\nSee [[comparisons/x]].")
    assert "## Competitors\n\nSee [[comparisons/x]].\n\n## Notes\n" in out
    gone = parse.replace_lines(PRODUCT, first, last, "")
    assert "Competitors" not in gone and "## Notes" in gone


# ---- read ----------------------------------------------------------------
def test_read_page_returns_markdown_as_its_own_unescaped_block(server):
    result = raw(server, "read_page", {"path": "product"})
    assert not result.is_error and len(result.content) == 2
    header = json.loads(result.content[0].text)
    assert "text" not in header and header["text_follows"]
    assert header["version"] == db.version_of(PRODUCT) and header["total_lines"] == len(parse.keep_lines(PRODUCT)) == 30
    assert result.content[1].text == PRODUCT         # exactly as stored: real newlines, quotes
    assert '\\"' not in result.content[1].text and "\\n" not in result.content[1].text
    # An exact string copied from that block edits the page.
    ok(server, "edit_page", {"path": "product",
                             "old_text": 'Intro with a "quoted" word and a tab\there.',
                             "new_text": "Intro."})
    assert "Intro.\n" in text_of(server, "product")


def test_read_page_outline_section_and_lines(server):
    r = ok(server, "read_page", {"path": "product", "outline": True})
    assert "text" not in r
    assert [h["heading"] for h in r["outline"]] == [
        "Product", "Pricing", "Anchor", "Competitors", "Notes"]
    r = ok(server, "read_page", {"path": "product", "section": "## Pricing"})
    assert r["text"].startswith("## Pricing\n") and "### Anchor" in r["text"]
    assert "Competitors" not in r["text"] and r["section"] == "Pricing"
    assert r["version"] == db.version_of(PRODUCT)     # the whole page's version
    first, last = r["lines"]
    r2 = ok(server, "read_page", {"path": "product", "offset": first, "limit": last - first + 1})
    assert r2["text"] == r["text"]
    # A search hit's line number lands on the same line.
    hit = ok(server, "search_pages", {"query": "Last section"})["results"][0]["matches"][0]
    line = ok(server, "read_page", {"path": "product", "offset": hit["line"], "limit": 1})
    assert line["text"] == "Last section.\n"
    assert "no heading" in err(server, "read_page", {"path": "product", "section": "Nope"})
    assert "not both" in err(server, "read_page", {"path": "product", "section": "Notes",
                                                   "offset": 3})
    assert "past the page's last line" in err(server, "read_page",
                                               {"path": "product", "offset": 999})


def test_long_pages_come_with_an_outline_and_show_in_health(server):
    body = "# Big\n\n" + "".join(f"## Part {i}\n\n" + "word " * 300 + "\n\n" for i in range(8))
    ok(server, "write_page", {"path": "big", "text": body})
    r = ok(server, "read_page", {"path": "big"})
    assert r["text"] == body and "hint" in r and len(r["outline"]) == 9
    assert "hint" not in ok(server, "read_page", {"path": "product"})
    h = ok(server, "wiki_health")
    assert h["long_pages"] == [{"path": "big", "words": len(body.split())}] and h["long_page_words"] == 2000
    assert ok(server, "wiki_health", {"long_page_words": 5000})["long_pages"] == []


def test_search_returns_each_line_once(server):
    r = ok(server, "search_pages", {"query": "Basic Memory|Team", "regex": True})
    hit = next(h for h in r["results"] if h["path"] == "product")
    lines = [m["line"] for m in hit["matches"]]
    # "Team $10. Basic Memory is $15." matches twice and is listed once.
    assert lines == sorted(set(lines)) and hit["match_count"] == len(lines) == 3


# ---- edit by section -------------------------------------------------------
def test_edit_page_replaces_or_deletes_a_whole_section(server):
    v = ok(server, "read_page", {"path": "product"})["version"]
    r = ok(server, "edit_page", {"path": "product", "section": "Competitors",
                                 "new_text": "## Competitors\n\nMoved to [[entities/hjarni]].",
                                 "base_version": v})
    assert r["section"] == "Competitors" and r["replacements"] == 1
    text = text_of(server, "product")
    assert "## Competitors\n\nMoved to [[entities/hjarni]].\n\n## Notes" in text
    assert "not a heading, inside code" not in text
    ok(server, "edit_page", {"path": "product", "section": "Anchor", "new_text": ""})
    text = text_of(server, "product")
    assert "Anchor" not in text and "Team $10" in text
    assert "no heading" in err(server, "edit_page", {"path": "product", "section": "Anchor",
                                                     "new_text": "x"})
    assert "give old_text" in err(server, "edit_page", {"path": "product", "new_text": "x"})


def test_old_text_only_has_to_be_unique_inside_the_section(server):
    assert "appears 3 times" in err(server, "edit_page", {
        "path": "product", "old_text": "Basic Memory", "new_text": "BM"})
    assert "appears 2 times in section" in err(server, "edit_page", {
        "path": "product", "section": "Pricing", "old_text": "Basic Memory", "new_text": "BM"})
    ok(server, "edit_page", {"path": "product", "section": "Competitors",
                             "old_text": "Basic Memory", "new_text": "BM"})
    text = text_of(server, "product")
    assert "- BM syncs files." in text and text.count("Basic Memory") == 2


# ---- notes and history -----------------------------------------------------
def test_notes_are_kept_and_the_wiki_has_a_recent_changes_feed(server):
    ok(server, "append_page", {"path": "log", "text": "entry", "note": "  logged   the split "})
    ok(server, "edit_page", {"path": "index", "old_text": "# Index", "new_text": "# Home",
                             "note": "renamed the index"})
    h = ok(server, "page_history", {"path": "log"})["revisions"]
    assert h[0]["op"] == "append" and h[0]["note"] == "logged the split"
    assert h[1]["op"] == "push" and h[1]["note"] == ""
    feed = ok(server, "page_history")["changes"]
    assert [(c["path"], c["op"], c["note"]) for c in feed[:2]] == [
        ("index", "edit", "renamed the index"), ("log", "append", "logged the split")]
    assert all(c["op"] != "baseline" for c in feed)
    old = ok(server, "read_page", {"path": "log", "revision": h[0]["revision"]})
    assert old["note"] == "logged the split" and old["text"] == "# Log\nentry"
    assert "one line" in err(server, "write_page", {"path": "x", "text": "x", "note": "n" * 501})


# ---- several changes as one step --------------------------------------------
SPLIT = [
    {"op": "write", "path": "entities/basic-memory",
     "text": "# Basic Memory\n\nClosest product. See [[comparisons/pricing]] and [[product]].\n"},
    {"op": "write", "path": "comparisons/pricing",
     "text": "# Pricing\n\n[[entities/basic-memory]] $15, [[entities/hjarni]] $12.\n"},
    {"op": "edit", "path": "product", "section": "Competitors",
     "new_text": "## Competitors\n\nSee [[entities/basic-memory]] and [[comparisons/pricing]]."},
    {"op": "append", "path": "log", "text": "- split competitors out of product"},
]


def test_change_pages_dry_run_predicts_the_real_run_and_writes_nothing(server):
    before = text_of(server, "product")
    dry = ok(server, "change_pages", {"changes": SPLIT, "dry_run": True, "note": "split"})
    assert dry["dry_run"] and not dry["applied"]
    assert dry["pages_changed"] == ["comparisons/pricing", "entities/basic-memory", "log",
                                    "product"]
    # Pages created in the same step link to each other without being reported broken.
    assert dry["broken_links"] == {} and dry["now_broken_elsewhere"] == []
    assert text_of(server, "product") == before
    assert "no page" in err(server, "read_page", {"path": "entities/basic-memory"})
    real = ok(server, "change_pages", {"changes": SPLIT, "note": "split competitors out"})
    assert real["applied"] and real["results"] == dry["results"]
    assert real["pages_changed"] == dry["pages_changed"] and real["broken_links"] == {}
    assert ok(server, "wiki_health")["dangling"] == []
    h = ok(server, "page_history", {"path": "product"})["revisions"][0]
    assert h["op"] == "edit" and h["note"] == "split competitors out"
    assert "See [[entities/basic-memory]]" in text_of(server, "product")


def test_change_pages_is_all_or_nothing(server):
    bad = SPLIT[:2] + [{"op": "edit", "path": "product", "old_text": "not there",
                        "new_text": "x"}]
    msg = err(server, "change_pages", {"changes": bad})
    assert "change 3 (edit product) failed, so nothing was written" in msg
    assert "no page" in err(server, "read_page", {"path": "entities/basic-memory"})
    assert [c for c in ok(server, "page_history")["changes"] if c["op"] != "push"] == []
    assert "needs new_path" in err(server, "change_pages", {"changes": [
        {"op": "move", "path": "log"}]})
    assert "changes is empty" in err(server, "change_pages", {"changes": []})


def test_change_pages_later_changes_see_earlier_ones(server):
    steps = [
        {"op": "write", "path": "drafts/a", "text": "# A\n\nLinks to [[drafts/b]].\n"},
        {"op": "write", "path": "drafts/b", "text": "# B\n"},
        {"op": "move", "path": "drafts/b", "new_path": "final/b"},
        {"op": "edit", "path": "final/b", "old_text": "# B", "new_text": "# B final"},
        {"op": "delete", "path": "entities/hjarni"},
    ]
    dry = ok(server, "change_pages", {"changes": steps, "dry_run": True})
    # The move rewrote the link written two steps earlier.
    assert dry["results"][2]["pages_updated"] == ["drafts/a"]
    assert dry["now_broken_elsewhere"] == [{"page": "index", "target": "entities/hjarni"},
                                           {"page": "product", "target": "entities/hjarni"}]
    assert dry["broken_links"] == {}
    real = ok(server, "change_pages", {"changes": steps})
    assert real["now_broken_elsewhere"] == dry["now_broken_elsewhere"]
    assert "[[final/b]]" in text_of(server, "drafts/a")
    assert text_of(server, "final/b") == "# B final\n"
    assert {d["target"] for d in ok(server, "wiki_health")["dangling"]} == {"entities/hjarni"}
    ops: dict = {}
    for c in ok(server, "page_history")["changes"]:     # newest first
        ops.setdefault(c["path"], c["op"])
    assert ops["final/b"] == "move+edit" and ops["entities/hjarni"] == "delete"
    assert "drafts/b" not in {p["path"] for p in ok(server, "list_pages")["pages"]}


def test_change_pages_respects_base_version(server):
    v = ok(server, "read_page", {"path": "log"})["version"]
    ok(server, "append_page", {"path": "log", "text": "someone else"})
    msg = err(server, "change_pages", {"changes": [
        {"op": "write", "path": "new", "text": "# New\n"},
        {"op": "edit", "path": "log", "old_text": "# Log", "new_text": "# L",
         "base_version": v}]})
    assert "changed since you read it" in msg
    assert "no page" in err(server, "read_page", {"path": "new"})
