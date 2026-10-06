"""Going back between pages (Forrest, 2026-09-28: "when clicking from page to
page in the wiki, we need a way to go back", option B of three): the page you
came from is the head's first row, "← <title>", driven by the browser's history
in the app and by a list of its own in the static viewer. Checked in Chrome and
WebKit against a local server when built; these pin the contracts in place."""
from __future__ import annotations

from pathlib import Path

from dexio.render import APP_JS

STATIC = Path(__file__).parent.parent / "src" / "dexio" / "static"
JS = (STATIC / "graph.js").read_text(encoding="utf-8")
SHELL = (STATIC / "shell.html").read_text(encoding="utf-8")


def body(name: str) -> str:
    return JS.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


def test_every_entry_the_app_pushes_carries_its_trail():
    s = body("setAddress")
    # a push records the page and the pages open before it; a replace keeps them
    assert "history.pushState({ dexio: 1," in s and "trail: trailFrom(cur)" in s
    assert "history.replaceState({ ...cur," in s
    # the entry being left keeps where it was read
    assert s.index("saveTop();") < s.index("history.pushState(")
    t = body("trailFrom")
    # the page open now goes first, one step back; a closed step adds none
    assert 't.unshift({ id: open, title: titleOf(open), steps: 1 })' in t
    assert "steps: b.steps + 1" in t


def test_the_line_goes_to_the_newest_other_page_and_uses_the_browser_history():
    b = body("backTo")
    assert "cur.page !== id" in b                       # only the entry's own page
    assert ".find((x) => x.id !== id)" in b             # not "back to itself"
    g = body("goBack")
    assert "history.go(-b.steps)" in g                  # agrees with browser Back
    # the static viewer has no addresses: its own list
    assert "trail.length = b.at;" in g and "select(state.byId.get(to.id), { back: true, top: to.top })" in g
    assert "trail.length = 0;" in body("load")          # another wiki in an export


def test_back_and_forward_put_the_reading_position_back():
    assert "openAsked(want.page, want.sec, e.state && e.state.dexio ? e.state.top : 0)" in APP_JS
    assert 'window.dexio.openPath(page, sec, "none", top || 0)' in APP_JS
    sel = body("select")
    assert "if (top && hist.text !== null) panel.scrollTop = top;" in sel
    # any other way of opening a page still starts at its top (2026-09-26)
    assert "panel.scrollTop = 0;" in body("showPage")
    # saved as the scroll settles, not on every scroll event: browsers cap
    # how often an entry may be rewritten
    assert "topTimer = setTimeout(saveTop, 250);" in JS


def test_the_line_is_the_heads_first_row_and_stays_across_tabs():
    sp = body("showPage")
    assert '${back ? " has-back" : ""}' in sp
    assert sp.index("back +") < sp.index('class="toc-btn"')
    assert "wireBack();" in sp
    # tabs swap the body only, so the head, and the line, stay as they are
    assert "panel.innerHTML" not in body("setBody")
    css = SHELL.split("---- going back", 1)[1].split("\n\n", 1)[0]
    for rule in ("grid-template-columns: auto minmax(0, 1fr) auto",
                 ".back-row { grid-row: 1; grid-column: 1 / 3;",
                 ".head-acts { grid-row: 1; grid-column: 3; align-self: center;",
                 ".toc-btn { grid-row: 2; grid-column: 1; }",
                 ".panel-title { grid-row: 2; grid-column: 2 / 4; }",
                 "#panel:not(.has-toc) .panel-head.has-back .panel-title"):
        assert rule in css, rule
    phone = SHELL.split("the back line's text is small; its tap area is not", 1)[1].split("\n", 2)[1]
    assert "padding: 10px 2px" in phone


def test_graph_js_declares_each_function_once():
    """graph.js is one scope: a second function of the same name silently
    replaces the first. The back line's first draft named a helper step(),
    which took the force layout's step() out (caught by test_layout.mjs)."""
    import collections
    import re
    names = re.findall(r"^  (?:async )?function (\w+)\(", JS, re.M)
    assert len(names) > 100
    assert [n for n, c in collections.Counter(names).items() if c > 1] == []
