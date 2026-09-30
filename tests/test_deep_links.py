"""Deep links (Forrest, 2026-09-28): every page has an address,
/w/<workspace>/<page path>#<section>, that opens it in the web app, and MCP results
carry it so agents can hand it to people. A workspace has one wiki (the same day),
so the address names none; the earlier /w/<workspace>/<wiki>/<page> still works."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dexio.parse import heading_anchors, heading_slug

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.render import APP_JS  # noqa: E402
from dexio.server import files  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import browser, mcp, signup, token_from_connect, ws_id  # noqa: E402

NODE = shutil.which("node")
ANCHORS = Path(__file__).parent / "anchors.mjs"
STATIC = Path(__file__).parent.parent / "src" / "dexio" / "static"

PAGE = """---
description: a page with every kind of heading
---
# Acme Corp

Intro.

## Deployment state

<!-- ## Not a heading -->

```
## Not one either
```

### Deployment state

## `code` and **bold** in a heading ##

## See [[entities/globex|Globex]] and [[entities/initech]]

## A [link](https://example.com/x) here

## Q&A: 2026-09-28, 50% off!

## Café Zürich 日本語

## Deployment state

#### Deep one

#Not a heading, no space
"""


def test_heading_slugs():
    assert heading_slug("Deployment state") == "deployment-state"
    assert heading_slug("Deployment state ##") == "deployment-state"
    assert heading_slug("`code` and **bold**") == "code-and-bold"
    assert heading_slug("See [[entities/globex|Globex]] and [[entities/initech]]") == \
        "see-globex-and-entities-initech"
    assert heading_slug("A [link](https://example.com/x) here") == "a-link-here"
    assert heading_slug("Q&A: 2026-09-28, 50% off!") == "q-a-2026-09-28-50-off"
    assert heading_slug("Café Zürich 日本語") == "café-zürich-日本語"
    assert heading_slug("snake_case_name") == "snake-case-name"
    assert heading_slug("!!!") == "section"


def test_heading_anchors_skip_the_title_code_and_comments_and_number_repeats():
    got = heading_anchors(PAGE, "Acme Corp")
    assert list(got.values()) == [
        "deployment-state", "deployment-state-2", "code-and-bold-in-a-heading",
        "see-globex-and-entities-initech", "a-link-here", "q-a-2026-09-28-50-off",
        "café-zürich-日本語", "deployment-state-3", "deep-one"]
    lines = PAGE.split("\n")
    assert all(lines[n - 1].startswith("#") for n in got)
    # the opening H1 is a heading like any other when it is not the title
    assert "acme-corp" in heading_anchors(PAGE, "Something else").values()
    assert "acme-corp" not in heading_anchors(PAGE, "Acme Corp").values()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_web_view_gives_headings_the_same_anchors():
    docs = [{"text": PAGE, "title": "Acme Corp"},
            {"text": PAGE, "title": "Other"},
            {"text": "Intro first.\n\n# Acme Corp\n\n## One\n\n## One\n", "title": "Acme Corp"},
            {"text": "<!-- note -->\n# T\n\n## T\n", "title": "T"}]
    p = subprocess.run([NODE, str(ANCHORS)], input=json.dumps(docs), capture_output=True,
                       text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    for doc, ids in zip(docs, json.loads(p.stdout)):
        assert ids == list(heading_anchors(doc["text"], doc["title"]).values()), doc


def test_the_app_keeps_page_addresses():
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    sel = js.split("async function select(n, opts) {", 1)[1].split("\n  }\n", 1)[0]
    assert "setAddress(n ? pageHref(n.id, opts.section) : wikiHref()" in sel
    # only the app: the export and the dexio.wiki demos have no server behind them
    assert "const ROUTED = !!(window.DEXIO_API" in js
    # links and tree rows carry the address, and Cmd-click is the browser's
    assert "if (ROUTED) a.href = pageHref(a.dataset.id, a.dataset.sec);" in js
    assert "if (!page || newTab(e)) return;" in js
    # the address names the workspace and the page, no wiki; since 2026-09-28
    # also the folder the graph is narrowed to, ?folder= (test_folder_focus)
    href = js.split("function wikiHref() {", 1)[1].split("\n  }\n", 1)[0]
    assert href.strip() == ('return "/w/" + encodeURIComponent(window.DEXIO_WORKSPACE)'
                            ' + focusQuery();')
    # Back and Forward, and the address on load, are the app script's
    assert 'window.addEventListener("popstate"' in APP_JS
    assert "else if (want.page) openAsked(want.page, want.sec);" in APP_JS
    assert 'parts[0] === "w" ? parts.slice(2)' in APP_JS


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    files.set_store(files.FolderStore(tmp_path / "store"))
    a = get_app(str(tmp_path / "d.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        yield a
    files.set_store(None)


def setup(app):
    ann = browser(app)
    signup(ann, "ann@example.com")
    ws = ann.get("/api/v1/workspaces").json()["current"]
    key = token_from_connect(ann)
    got = mcp(app, key, "write_page", path="entities/acme-corp", text=PAGE)
    assert got["ok"]
    return ann, ws, key, got


def test_an_address_opens_the_app_in_that_workspace(app):
    ann, ws, _key, _ = setup(app)
    r = ann.get(f"/w/{ws}/entities/acme-corp")
    assert r.status_code == 200
    assert f'window.DEXIO_WORKSPACE = "{ws}";' in r.text
    assert 'id="switcher-button"' not in r.text          # no wiki to pick
    # the workspace alone is its wiki, with no page open
    assert ann.get(f"/w/{ws}").status_code == 200


def test_addresses_from_before_one_wiki_go_to_the_page(app):
    """Until a workspace had one wiki the address named it after the workspace,
    and agents handed those out for a few hours on 2026-09-28."""
    ann, ws, key, _ = setup(app)
    r = ann.get(f"/w/{ws}/main/entities/acme-corp?x=1")
    assert r.status_code == 308
    assert r.headers["location"] == f"/w/{ws}/entities/acme-corp?x=1"
    r = ann.get(f"/w/{ws}/main")
    assert r.status_code == 308 and r.headers["location"] == f"/w/{ws}"
    # a folder that happens to be called main keeps its pages' addresses
    assert mcp(app, key, "write_page", path="main/notes", text="# Notes")["ok"]
    assert ann.get(f"/w/{ws}/main/notes").status_code == 200


def test_signed_out_the_address_survives_sign_in(app):
    _ann, ws, _key, _ = setup(app)
    anon = browser(app)
    r = anon.get(f"/w/{ws}/entities/acme-corp", headers={"Accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"] == f"/login?next=/w/{ws}/entities/acme-corp"
    page = anon.get(r.headers["location"]).text
    assert f'name="next" value="/w/{ws}/entities/acme-corp"' in page
    # the #section is put back on next by the page itself (the server never sees it)
    assert 'input[name="next"]' in page and "location.hash" in page


def test_other_workspaces_say_so(app):
    _ann, ws, _key, _ = setup(app)
    bob = browser(app)
    signup(bob, "bob@example.com")
    r = bob.get(f"/w/{ws}/entities/acme-corp")
    assert r.status_code == 404 and "not a member" in r.text


def test_mcp_results_carry_the_address(app):
    _ann, ws, key, wrote = setup(app)
    base = f"https://app.dexio.wiki/w/{ws}"
    assert wrote["url"] == f"{base}/entities/acme-corp"
    got = mcp(app, key, "read_page", path="entities/acme-corp")
    assert got["url"] == f"{base}/entities/acme-corp"
    got = mcp(app, key, "read_page", path="entities/acme-corp",
              section="Café Zürich 日本語")
    assert got["url"] == (f"{base}/entities/acme-corp#"
                          "caf%C3%A9-z%C3%BCrich-%E6%97%A5%E6%9C%AC%E8%AA%9E")
    # a section named by line: the third "Deployment state"
    line = [n for n, a in heading_anchors(PAGE, "Acme Corp").items()
            if a == "deployment-state-3"][0]
    got = mcp(app, key, "read_page", path="entities/acme-corp", section=str(line))
    assert got["url"] == f"{base}/entities/acme-corp#deployment-state-3"
    got = mcp(app, key, "append_page", path="log", text="x")
    assert got["url"] == f"{base}/log"
    got = mcp(app, key, "move_page", path="log", new_path="logs/log")
    assert got["url"] == f"{base}/logs/log"


def test_addresses_name_a_workspace_by_a_handle_that_says_nothing_about_its_age(app):
    """Ids count up from 1, so /w/<id> told a new customer how early they were
    (Forrest, 2026-09-28). Addresses, ?w= and the page's script use a random
    handle; the id never reaches the browser."""
    import re

    from dexio.server import db
    ann, ws, _key, wrote = setup(app)
    wid = ws_id(app, ws)
    assert re.fullmatch(r"[a-z][a-z0-9]{7}", ws) and not set(ws) & set("01ilo")
    assert f"/w/{ws}/" in wrote["url"]
    page = ann.get(f"/w/{ws}").text
    for leak in (f"/w/{wid}", f"w={wid}&", f"w={wid}\"", f"DEXIO_WORKSPACE = {wid};",
                 f'"id": {wid},', f'data-ws="{wid}"', f'data-key="ws:{wid}"'):
        assert leak not in page, leak
    settings = ann.get(f"/settings?w={ws}").text
    assert f"?w={wid}" not in settings and f"?w={ws}" in settings
    assert ann.cookies.get("dexio_ws") == ws
    # each workspace gets its own, unrelated to the order they were made in
    handles = {db.new_handle() for _ in range(1000)}
    assert len(handles) == 1000


def test_addresses_by_id_from_before_handles_go_to_the_handle(app):
    """Agents handed out /w/<id> links for a few hours on 2026-09-28."""
    ann, ws, _key, _ = setup(app)
    wid = ws_id(app, ws)
    r = ann.get(f"/w/{wid}/entities/acme-corp?x=1")
    assert r.status_code == 308 and r.headers["location"] == f"/w/{ws}/entities/acme-corp?x=1"
    r = ann.get(f"/w/{wid}/main/entities/acme-corp")         # older still: id and wiki
    assert r.status_code == 308 and r.headers["location"] == f"/w/{ws}/entities/acme-corp"
    assert ann.get(f"/w/{wid}").headers["location"] == f"/w/{ws}"
    assert ann.get(f"/settings?w={wid}").status_code == 200  # an old ?w= still picks it


def test_a_database_from_before_handles_gives_every_workspace_one(app):
    from dexio.server import db
    conn = app.state.conn
    ann, ws, _key, _ = setup(app)
    with conn:
        conn.execute("UPDATE workspaces SET handle=NULL")
    db.give_handles(conn)
    got = [r["handle"] for r in conn.execute("SELECT handle FROM workspaces")]
    assert got and all(got) and len(set(got)) == len(got)
    db.give_handles(conn)                                   # and keeps them after
    assert [r["handle"] for r in conn.execute("SELECT handle FROM workspaces")] == got
