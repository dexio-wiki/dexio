"""Who changed the wiki lately, for the stats card over the graph (db.activity;
graph.js, "stats"; Forrest, 2026-11-07). Members get it with the graph; someone
viewing what a workspace shares does not."""
from __future__ import annotations

import pytest

from dexio.server import db

DAY = 86400.0
NOW = 2_000_000_000.0


@pytest.fixture()
def conn(tmp_path):
    return db.connect(str(tmp_path / "a.db"))


def _write(conn, k, path, text, agent, at, op="edit"):
    pages = db.PageSet(conn, k)
    pages[path] = db.make_page(path, text)
    db.apply_changes(conn, k, pages.changes, source="mcp", author="t", op=op, agent=agent)
    rid = conn.execute("SELECT MAX(id) FROM revisions WHERE project=?", (k,)).fetchone()[0]
    conn.execute("UPDATE revisions SET at=? WHERE id=?", (at, rid))
    conn.commit()


def test_activity_counts_this_week_and_keeps_the_quiet(conn):
    k = db.wiki_key(db.default_workspace(conn))
    _write(conn, k, "notes/a", "# A\n", "iris", NOW - 2 * DAY)
    _write(conn, k, "notes/a", "# A\n\nmore\n", "Iris", NOW - 1 * DAY)    # one agent, any case
    _write(conn, k, "notes/b", "# B\n", "iris", NOW - 3600)
    _write(conn, k, "ops/c", "# C\n", "niko", NOW - 600)
    _write(conn, k, "ops/d", "# D\n", "enzo", NOW - 10 * DAY)              # quiet, not gone
    _write(conn, k, "ops/e", "# E\n", "mia", NOW - 40 * DAY)               # gone
    _write(conn, k, "ops/f", "# F\n", None, NOW - 300)                      # nobody named
    a = db.activity(conn, k, now=NOW)
    assert a["days"] == 7 and a["quiet_days"] == 30 and a["now"] == NOW
    per = {}
    for c in a["changes"]:
        per.setdefault(c["who"], {})[c["path"]] = c["n"]
    assert set(per) == {"iris", "niko", "someone"}
    assert per["iris"] == {"notes/a": 2, "notes/b": 1}
    assert per["niko"] == {"ops/c": 1}
    writers = {w["who"]: w for w in a["writers"]}
    assert set(writers) == {"iris", "niko", "enzo", "someone"}        # mia wrote 40 days ago
    assert writers["enzo"]["last"] == NOW - 10 * DAY and writers["enzo"]["agent"] is True
    assert writers["someone"]["agent"] is False
    assert a["latest"] == {"who": "someone", "path": "ops/f", "at": NOW - 300}
    # newest first
    assert a["changes"][0]["path"] == "ops/f"


def test_activity_leaves_out_baseline_rows(conn):
    k = db.wiki_key(db.default_workspace(conn))
    _write(conn, k, "old", "# Old\n", "import", NOW - DAY, op="baseline")
    a = db.activity(conn, k, now=NOW)
    assert a["changes"] == [] and a["writers"] == [] and a["latest"] is None


def test_account_menu_has_room_for_the_wikis_totals_under_the_workspace():
    """The totals (pages, links, words) sit under the workspace's name in the
    account menu (Forrest, 2026-11-07), filled from the graph for members only:
    the graph comes with activity for members alone (render.py, acctStats)."""
    from dexio import render
    menu = render.account_menu("Forrest Zhang", "f@example.com", "Forrest", "Zhang", "VantaSoft")
    head = menu.index('<div class="sw-head">VantaSoft</div>')
    assert menu.index('<div class="ac-stats" hidden></div>') > head
    assert menu.index('<div class="ac-stats"') < menu.index('href="/settings"')
    js = render.APP_JS
    assert "function acctStats(data)" in js
    assert "data && data.activity && data.stats" in js
    assert js.count("acctStats(data);") == 2            # first load, and the empty wiki's watch


pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from test_sharing import owner_with_wiki, public  # noqa: E402
from test_workspaces import app, browser, mcp  # noqa: E402,F401


def test_members_get_activity_with_the_graph_and_guests_do_not(app):
    c, tok, handle = owner_with_wiki(app)
    assert mcp(app, tok, "write_page", path="notes/plan", text="# Plan\n\nRevised.\n",
               agent="iris").get("ok")
    g = c.get(f"/api/v1/graph?w={handle}").json()
    act = g["activity"]
    assert act["days"] == 7
    iris = [ch for ch in act["changes"] if ch["who"] == "iris"]
    assert [ch["path"] for ch in iris] == ["notes/plan"] and iris[0]["n"] == 1
    assert act["latest"]["who"] == "iris" and act["latest"]["path"] == "notes/plan"
    public(c, handle, "page", "notes/plan")
    anon = browser(app)
    g = anon.get(f"/api/v1/graph?w={handle}").json()
    assert [n["id"] for n in g["nodes"]] == ["notes/plan"] and "activity" not in g
