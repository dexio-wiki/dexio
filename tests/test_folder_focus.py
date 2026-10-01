"""Narrowing the graph to one folder (Forrest, 2026-09-28: "we need an ability to
somehow filter down the nodes to just a folder", option A of four): a focus button
on every folder row in the tree, a chip beside the layout menu that names the
folder and clears it, the pages elsewhere that link to it shown faded (Linked
pages), and ?folder= in the address. Checked in Chromium against a local server
when built; these pin the contracts in place."""
from __future__ import annotations

from pathlib import Path

import pytest

from dexio.render import APP_JS, server_page

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from test_sharing import owner_with_wiki, public  # noqa: E402
from test_workspaces import app, browser  # noqa: E402,F401

STATIC = Path(__file__).parent.parent / "src" / "dexio" / "static"
JS = (STATIC / "graph.js").read_text(encoding="utf-8")
SHELL = (STATIC / "shell.html").read_text(encoding="utf-8")


def body(name: str) -> str:
    return JS.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


def test_the_focus_hides_the_rest_and_keeps_linked_pages_faded():
    f = body("filterShown")
    assert "if (!state.query && !state.focus) state.data = all;" in f
    assert "all.nodes.filter((n) => inFocus(n) && matchesQuery(n))" in f
    # pages elsewhere tied to the folder stay, faded, when Show asks for them
    assert "if (state.focus && state.linked)" in f
    assert "state.fringe.add(l.target)" in f and "state.fringe.add(l.source)" in f
    # but not the links among them
    assert "!(fr.has(l.source) && fr.has(l.target))" in f
    # faded: drawn like pages outside a hovered folder, unless opened
    assert "state.fringe.has(n.id) && n.id !== state.selected" in body("matches")
    # a search inside the folder lists only its pages
    assert "nodes.filter((n) => inFocus(n) && matchesQuery(n))" in body("rankedMatches")


def test_a_click_opens_a_folder_and_the_arrow_drills_in():
    """Forrest, 2026-09-29: "can we actually have clicking a folder in the left
    sidebar expand/collapse it? and can we introduce an icon at the right side of
    the row that drills into it?" (replacing 2026-09-28's Finder-style double-click)."""
    tree = body("treeHtml")
    assert '<span class="twisty"' in tree and "data-focus-dir" not in JS
    # the arrow is a sibling of the row (a button cannot hold a button), out of
    # the tab order since Enter on the row drills in
    assert '<div class="dir-line"><button type="button" class="row dir" ' in tree
    assert 'class="drill" data-drill="${path}" tabindex="-1" ' in tree
    # a click anywhere on the row opens or closes it; the arrow drills in
    assert 'if (drill) { setFocus(drill.dataset.drill); return; }' in JS
    assert 'if (dir) { toggleDir(dir.dataset.dir); return; }' in JS
    assert JS.index("[data-drill]\");\n      if (drill)") < JS.index("if (dir) { toggleDir(")
    # no double-click drilling and no picked-out rows any more
    assert 'treeEl.addEventListener("dblclick"' not in JS and '"picked"' not in JS
    assert "lastPointer" not in JS
    # Enter from the keyboard still drills in
    assert 'if (e.key === "Enter") { e.preventDefault(); setFocus(p); }' in JS
    # hovering the arrow still picks the folder out in the graph
    assert 'e.target.closest("[data-dir], [data-drill]")' in JS
    # the arrow shows on hover or focus over the count, always on touch screens
    assert "#tree .dir-line:hover > .drill, #tree .row.dir:focus-visible + .drill" in SHELL
    assert "@media (hover: none) {\n    #tree .drill { display: grid;" in SHELL
    assert ".picked" not in SHELL
    # drilled in, the tree lists only what is inside, under a row back up
    t = body("renderTree")
    assert 'class="row up" data-up=' in t and "root = d;" in t
    assert "#tree .row.dir .twisty" in SHELL and "#tree .row.up" in SHELL
    # no Share in the tree (Forrest, 2026-09-28): drill in, then the header's Share
    assert "data-share-dir" not in JS and "dir-acts" not in JS and "dir-acts" not in SHELL


def test_show_sits_left_of_the_folder_pill_and_the_pill_is_the_path():
    """"there should be a dropdown to the left of the folder pill that determines
    whether or not it shows linked pages" (Forrest, 2026-09-28)."""
    b = body("buildBar")
    assert b.index("pick.appendChild(show);") < b.index("pick.appendChild(chip);")
    assert 'data-show="${id}"' in b and "setLinked(item.dataset.show === \"linked\")" in b
    assert 'folder: { name: "Folder only"' in JS and 'linked: { name: "With linked pages"' in JS
    # the folder alone unless asked for, remembered per browser
    assert 'state.linked = localStorage.getItem(SHOW_KEY) === "linked";' in JS
    chip = body("renderChip")
    assert 'data-to="${escapeHtml(at)}"' in chip and 'class="fc-x"' in chip
    assert "x.onclick = () => setFocus(null);" in chip
    assert "#show-button {" in SHELL and "#focus-chip {" in SHELL
    # hidden when it would change nothing, as for someone one folder is shared with
    assert "bar.show.hidden = !on || !linksOut(state.focus);" in chip
    # the pill only while the tree is put away: the tree already shows the folder
    assert "#wrap.tree-open #focus-chip { display: none; }" in SHELL


def test_the_headers_share_shares_the_folder_drilled_into():
    """"the Share should be aware of which folder is currently active"."""
    share = (STATIC / "share.js").read_text(encoding="utf-8")
    assert "window.dexio.shareTarget()" in share and "open(t.kind, t.path);" in share
    # and offers nothing wider (Forrest, 2026-10-01: "can we get rid of this button")
    assert "Share the whole wiki instead" not in share and "sd-scope" not in share
    assert "window.dexio.focused = () => state.focus;" in JS
    assert "window.dexio.shareTarget = shareTarget;" in JS
    assert 'if (state.focus) return { kind: "folder", path: state.focus' in body("shareTarget")
    assert "`Share the folder ${state.focus}`" in body("shareHere")


def test_one_share_on_the_screen_and_it_shares_the_open_page():
    """"it's weird seeing the share button above another share button like this"
    (Forrest, 2026-09-28): the page head has no Share; the header's shares the
    page open, else the folder drilled into, else the wiki."""
    share = (STATIC / "share.js").read_text(encoding="utf-8")
    assert "share-page" not in JS and "share-page" not in SHELL
    assert "head-actions" not in JS and "head-actions" not in SHELL
    t = body("shareTarget")
    assert 'if (state.selected != null && panel.classList.contains("open"))' in t
    assert t.index('kind: "page"') < t.index('kind: "folder"') < t.index('kind: "wiki"')
    # the tooltip follows the panel as it opens and closes
    assert "`Share the page ${t.title}`" in body("shareHere")
    assert 'panel.classList.add("open");\n    shareHere();' in body("showPage")
    assert 'panel.classList.remove("open"); placeGrip(); shareHere(); return;' in body("select")
    # the dialog offers no wider scope (Forrest, 2026-10-01: "can we get rid of this
    # button in the share modal? 'Share the whole wiki instead'")
    assert "function scope(" not in share and "the whole wiki\", \" instead" not in share


def test_the_folder_rides_in_the_address_and_back_undoes_it():
    assert 'return state.focus ? "?folder="' in body("focusQuery")
    assert "focusQuery() +" in body("pageHref")
    s = body("setFocus")
    # an old link to a folder with no pages narrows to nothing
    assert "if (folder && !state.all.nodes.some((n) => inFolder(n, folder))) folder = null;" in s
    assert 'setAddress(open ? pageHref(open, hist.section) : wikiHref(), how || "push"' in s
    # the address on load and on Back and Forward
    assert 'new URLSearchParams(location.search).get("folder")' in APP_JS
    assert 'window.dexio.setFocus(want.folder, "none");' in APP_JS
    assert "{focus: want.folder}" in APP_JS
    assert "state.focus = want && data.nodes.some((n) => inFolder(n, want)) ? want : null;" in body("load")


def test_the_chip_sits_in_the_layout_row_of_every_graph_view():
    page = server_page("/api/v1")
    assert '<div id="layout-pick">' in page and "function renderChip()" in page


def test_a_folder_share_links_to_the_wiki_narrowed_to_it(app):
    c, _tok, handle = owner_with_wiki(app)
    d = public(c, handle, "folder", "notes/deep")
    assert d["link"] == f"https://app.dexio.wiki/w/{handle}?folder=notes/deep"
    # the address with ?folder= opens like the wiki's own, for anyone it is shared with
    r = browser(app).get(f"/w/{handle}?folder=notes/deep", headers={"accept": "text/html"})
    assert r.status_code == 200 and "function renderChip()" in r.text
    # and so it does for a member, through the old id form's redirect too
    assert c.get(f"/w/{handle}?folder=notes", headers={"accept": "text/html"}).status_code == 200
