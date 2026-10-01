"""Design-pass regressions: theming and mobile behaviour that is easy to undo."""
import re

import pytest

from dexio import themes
from dexio.render import offline, server_page
from dexio.render import STATIC
from dexio.server import login, pages as server_pages

GRAPH = {"nodes": [{"id": "a", "title": "A", "folder": "", "degree": 1, "words": 3}],
         "links": [], "stats": {}, "dangling": []}


def header_of(html: str) -> str:
    """The header's inside. An export's opens as <header class="export">."""
    return re.split(r"<header[^>]*>", html, maxsplit=1)[1].split("</header>", 1)[0]


def pages():
    return {"offline": offline(GRAPH), "server": server_page("/api/v1"),
            "login": server_pages.auth_page("login", "password", email="a@b.co")}


def test_every_page_declares_a_responsive_viewport():
    for name, html in pages().items():
        assert 'name="viewport"' in html, name
        assert "width=device-width" in html, name
        assert "initial-scale=1" in html, name


def test_every_page_supports_both_colour_schemes():
    for name, html in pages().items():
        assert 'content="light dark"' in html, name
        assert 'html[data-mode="dark"]' in html, name


def test_theme_is_applied_before_first_paint():
    """A toggle that runs after render flashes the wrong theme on every load."""
    for name, html in pages().items():
        head = html.split("</head>")[0]
        assert "document.cookie" in head and themes.COOKIE in head, name
        assert "prefers-color-scheme: dark" in head, name
        # the old toggle's saved choice still counts until a theme is picked
        assert 'localStorage.getItem("dexio-theme")' in head, name


def test_theme_changes_crossfade_but_page_loads_do_not():
    """A change after load fades the whole page, canvas included; loads apply
    before first paint with no animation; reduced motion switches instantly."""
    for name, html in pages().items():
        head = html.split("</head>")[0]
        assert "document.startViewTransition(change)" in head, name
        assert "prefers-reduced-motion: reduce" in head, name
        assert "::view-transition-new(root)" in head, name
    script = themes.head_script()
    first_paint = script.split("function fade")[0]
    assert "startViewTransition" not in first_paint
    # a CSS colour transition on the page would replay inside the fade's new
    # snapshot and make it lag
    shell = (STATIC / "shell.html").read_text(encoding="utf-8")
    for rule in ("  body {", "  header {"):
        block = shell.split(rule, 1)[1].split("}", 1)[0]
        assert "transition" not in block, rule


def test_closed_panel_does_not_make_the_page_scroll():
    """The closed panel sits past the graph's right edge (below it on phones);
    unclipped, the page scrolled sideways by the panel's width (2026-09-26)."""
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    wrap = css.split("  #wrap {", 1)[1].split("}", 1)[0]
    assert "overflow: clip" in wrap and "overflow: hidden" in wrap
    closed = css.split("  #panel {", 1)[1].split("}", 1)[0]
    assert "visibility: hidden" in closed, "a closed panel must not take focus"
    opened = css.split("  #panel.open {", 1)[1].split("}", 1)[0]
    assert "visibility: visible" in opened


def test_graph_page_itself_never_scrolls():
    """Also embedded: the dexio.wiki demo scrolled sideways and down (2026-09-26)."""
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    assert "html, body { height: 100%; overflow: hidden; }" in css
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    assert "window.self !== window.top" in js, "an embedded graph starts with the tree closed"


def test_every_page_opens_at_its_top():
    """The panel is one element for every page. Only the tree and in-page links
    reset its scroll, so a page opened from the graph kept the last page's
    position (Forrest, 2026-09-26). showPage() resets it after rendering."""
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    sel = js.split("function showPage(", 1)[1].split("\n  }\n", 1)[0]
    rendered = sel.split("panel.innerHTML", 1)[1].split('panel.classList.add("open")', 1)[0]
    assert "panel.scrollTop = 0" in rendered


def test_a_page_opens_before_its_text_arrives():
    """Opening a page drew nothing until its text was fetched: the panel stayed
    shut, or showed the page before (Forrest, 2026-09-26). It opens at once
    with a placeholder the text replaces; a page that arrives after another was
    opened is dropped; the placeholder waits 150ms so a quick page never
    flashes it. Checked in Chrome against a slowed /note: panel open with the
    title at 60ms, placeholder shown at 500ms, a fast page never showed it."""
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    sel = js.split("async function select(", 1)[1].split("\n  }\n", 1)[0]
    assert sel.index("showPage(") < sel.index("await fetchNote(")
    after = sel.split("await fetchNote(", 1)[1]
    assert after.index("ticket !== opening") < after.index("fillPage(")
    fill = js.split("function fillPage(", 1)[1].split("\n  }\n", 1)[0]
    assert "page-retry" in fill and "select(n, " in fill, "a failed load offers another try"
    assert 'aria-busy="true"' in js and 'removeAttribute("aria-busy")' in fill
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    rule = css.split("#panel .page-loading {", 1)[1].split("}", 1)[0]
    assert "page-loading-in .2s ease .15s both" in rule
    reduced = css.split("@media (prefers-reduced-motion: reduce)", 1)[1].split("</style>", 1)[0]
    assert "#panel .page-loading .sk { animation: none; }" in reduced


def test_graph_view_has_a_folder_tree():
    """A file-explorer view of the wiki beside the graph, with a header button
    to show or hide it (a drawer on phones)."""
    for name in ("offline", "server"):
        html = pages()[name]
        assert '<nav id="tree" aria-label="Folders">' in html, name
        assert "#wrap.tree-open #tree" in html, name
    mobile = server_page("/api/v1").split("@media (max-width: 720px)")[1]
    drawer = mobile.split("#tree {", 1)[1].split("}", 1)[0]
    assert "z-index" in drawer, "on phones the drawer covers the page sheet too"


def test_folder_tree_overlays_the_graph_like_the_panel():
    """The tree took width from the graph while the page panel lay over it;
    both now overlay (Forrest, 2026-09-26)."""
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    tree = css.split("  #tree {", 1)[1].split("}", 1)[0]
    assert "position: absolute" in tree and "flex:" not in tree
    assert "visibility: hidden" in tree, "a closed tree must not take focus"
    opened = css.split("  #wrap.tree-open #tree {", 1)[1].split("}", 1)[0]
    assert "visibility: visible" in opened and "transform: none" in opened
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    # the fit leaves room for the tree, measured at load and on resize only,
    # so a toggle never moves the graph
    fit = js.split("function fitView(", 1)[1].split("\n  }\n", 1)[0]
    assert "state.fitLeft" in fit and "coveredLeft()" not in fit


def test_folder_tree_carries_its_own_toggle():
    """The toggle sat in the header, left of the logo. Now the tree hides
    itself from its own head row, as the page panel closes itself, and a
    button over the graph's corner reopens it (Forrest, 2026-09-26)."""
    for name in ("offline", "server"):
        html = pages()[name]
        header = header_of(html)
        assert "tree-" not in header, name
        tree = html.split('<nav id="tree"', 1)[1].split("</nav>", 1)[0]
        assert 'id="tree-close"' in tree and 'id="tree-list"' in tree, name
        opener = html.split('<button id="tree-open"', 1)[1].split("</button>", 1)[0]
        assert 'aria-controls="tree"' in opener and 'aria-expanded="false"' in opener, name
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    # the tree re-renders its list, never its head (which holds the button)
    assert "treeList.innerHTML" in js and "treeEl.innerHTML" not in js
    assert 'e.key !== "\\\\"' in js, "Cmd+\\ or Ctrl+\\ shows and hides it"


def test_search_sits_mid_header_with_a_results_list():
    """Search was a 200px box in the header's far right corner. It sits in the
    middle now, between the logo side and the counts, with a list of matching
    pages under it and a clear button level with its magnifier (Forrest,
    2026-09-26)."""
    for name in ("offline", "server"):
        html = pages()[name]
        header = header_of(html)
        start, rest = header.split('<div id="find"', 1)
        find, end = rest.split('<div class="h-end">', 1)
        assert 'class="brand"' in start, name
        # No counts in either header. The app's went 2026-09-27 ("Empty so far"
        # and "No wikis yet" repeated the guided flow, and the switcher gives
        # each wiki's page count); the export's, which only the homepage demo
        # showed, went the same day (Forrest).
        assert 'id="stats"' not in html, name
        assert 'id="search"' in find and 'role="combobox"' in find, name
        assert 'aria-controls="find-list"' in find and 'id="find-list" role="listbox"' in find, name
        assert 'id="find-clear"' in find and 'id="find-keys"' in find, name
    css = pages()["server"].split("<style>", 1)[1].split("</style>", 1)[0]
    # the browser's own clear button sat inside the room kept for the key hint
    assert "#search::-webkit-search-cancel-button" in css
    # each side keeps what its content needs; the box gets up to 480px of the rest
    assert ("grid-template-columns: minmax(max-content, 1fr) minmax(200px, 480px) "
            "minmax(max-content, 1fr)") in css
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    assert "function rankedMatches(" in js and "state.textRank" in js
    # Cmd+F or Ctrl+F reaches the box from anywhere, Cmd+K too; a second Cmd+F
    # in the box is left to the browser's own find (Forrest, 2026-09-26)
    assert 'key !== "f" && key !== "k"' in js
    assert 'key === "f" && document.activeElement === searchEl' in js
    for name in ("offline", "server"):
        assert '<kbd id="find-keys" aria-hidden="true">⌘F</kbd>' in pages()[name], name


def test_both_sidebars_resize_from_their_inner_edge():
    """The page panel resized from its left edge; the folder tree now resizes
    from its right edge the same way (Forrest, 2026-09-26)."""
    for name in ("offline", "server"):
        html = pages()[name]
        tree = html.split('<nav id="tree"', 1)[1].split("</nav>", 1)[0]
        assert 'id="tree-grip" role="separator"' in tree, name
        assert 'id="panel-grip" role="separator"' in html, name
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    assert "width: var(--tree-w, 264px)" in css
    # what sits beside the tree follows its width, not a fixed 264px
    assert "calc(264px" not in css
    mobile = css.split("@media (max-width: 720px)", 1)[1]
    assert "#tree-grip { display: none; }" in mobile, "a phone drawer keeps its width"
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    assert '"dexio-tree-w"' in js and '"dexio-panel-w"' in js, "both widths are remembered"
    assert 'treeGrip.addEventListener("dblclick"' in js, "double-click resets the tree"

def test_no_theme_toggle_outside_settings():
    """Appearance is an app setting (Forrest, 2026-09-26), not a header button."""
    for name, html in pages().items():
        assert 'id="theme"' not in html, name
        assert "Toggle dark mode" not in html, name
        assert 'class="moon"' not in html and 'class="sun"' not in html, name


def test_every_theme_has_both_modes_in_the_stylesheet():
    css = themes.css()
    for tid in themes.THEMES:
        if tid == themes.DEFAULT_THEME:
            continue
        for mode in ("light", "dark"):
            assert f'html[data-theme="{tid}"][data-mode="{mode}"]' in css, (tid, mode)
    keys = set(themes.THEMES[themes.DEFAULT_THEME]["light"])
    for tid, t in themes.THEMES.items():
        for mode in ("light", "dark"):
            assert set(t[mode]) == keys, (tid, mode, set(t[mode]) ^ keys)


def test_every_theme_is_readable():
    """WCAG AA (4.5:1) for text and links, 3:1 for the wordmark's node, in every
    theme and mode. Dexio's own azure on the grey sign-in page is 4.4:1, so links
    are checked against the surfaces pages put them on: panels and the page."""
    c = themes.contrast
    for tid, t in themes.THEMES.items():
        for mode in ("light", "dark"):
            v = {**themes.NEUTRALS[mode], **t[mode]}
            where = f"{tid}/{mode}"
            assert c(v["link"], v["panel"]) >= 4.5, where
            assert c(v["link"], v["bg"]) >= 4.5, where
            assert c(v["btn-text"], v["btn"]) >= 4.5, where
            assert c(v["btn-text"], v["btn-hover"]) >= 4.5, where
            assert c(v["btn-text"], v["btn-press"]) >= 4.5, where
            assert c(v["chrome-text"], v["chrome"]) >= 4.5, where
            assert c(v["chrome-muted"], v["chrome"]) >= 4.5, where
            assert c(v["chrome-text"], v["chrome-field"]) >= 4.5, where
            assert c(v["chrome-accent"], v["chrome"]) >= 3, where
            # the selected mode button is panel-coloured text on the accent
            assert c(v["panel"], v["accent"]) >= 4.5, where


def test_buttons_are_one_system():
    """One button system across the app (Forrest, 2026-09-28, option A of four). Before
    it, quiet buttons such as Sign out turned red on hover, nothing looked pressed or
    disabled, keyboard focus showed only the browser's outline, and buttons were drawn
    in the browser's default font instead of the page's."""
    import re
    from dexio.server import login
    css = login.PAGE
    quiet_hover = re.search(r"button\.quiet:hover[^{]*\{\{([^}]*)\}\}", css).group(1)
    assert "bad" not in quiet_hover, "quiet buttons are not red on hover"
    assert "button {{ font-family:inherit; }}" in css
    assert re.search(r"button:focus-visible[^{]*\{\{\s*outline:2px solid var\(--accent\)", css)
    assert "button[type=submit]:active {{ background:var(--btn-press); }}" in css
    assert re.search(r"button:disabled \{\{ opacity:\.5", css)
    assert "border-radius:6px" not in re.search(r"button\[type=submit\] \{\{([^}]*)\}\}", css).group(1)
    for mode, v in themes.NEUTRALS.items():
        assert themes.contrast(v["text"], v["btn-fill-press"]) >= 4.5, mode
        assert themes.contrast(v["muted"], v["panel"]) >= 4.5, mode
        assert themes.contrast(v["bad-solid-text"], v["bad-solid"]) >= 4.5, mode
        assert themes.contrast(v["bad-solid-text"], v["bad-press"]) >= 4.5, mode


def test_layout_menu_is_drawn_in_the_page_colours():
    """The layout menu was a native <select>. The browser drew its open list in
    its own colours: light theme text on a light list in the dark theme, and
    plainer than the wiki switcher (Forrest, 2026-09-26). It is now a button and
    a menu like the switcher's, in the page's colours, which are readable in
    every theme."""
    import re
    shell = (STATIC / "shell.html").read_text(encoding="utf-8")
    body = shell.split("<body>")[1]
    assert "<select" not in body.split('id="wrap"')[1], "no native select over the graph"
    assert 'id="layout-button"' in body and 'aria-haspopup="menu"' in body
    assert re.search(r'id="layout-menu" role="menu"', body)
    css = shell.split("<style>")[1].split("</style>")[0]
    menu = re.search(r"  #layout-menu \{([^}]*)\}", css).group(1)
    assert "background: var(--panel)" in menu and "color: var(--text)" in menu
    for tid, t in themes.THEMES.items():
        for mode in ("light", "dark"):
            v = {**themes.NEUTRALS[mode], **t[mode]}
            assert themes.contrast(v["text"], v["panel"]) >= 4.5, f"{tid}/{mode}"
            assert themes.contrast(v["muted"], v["panel"]) >= 4.5, f"{tid}/{mode} descriptions"


def test_default_theme_looks_as_it_did_before_themes():
    light = {**themes.NEUTRALS["light"], **themes.THEMES["dexio"]["light"]}
    dark = {**themes.NEUTRALS["dark"], **themes.THEMES["dexio"]["dark"]}
    assert light["chrome"] == light["bg"] == "#ffffff" and light["accent"] == "#0077c8"
    assert dark["chrome"] == dark["bg"] == "#0d1117" and dark["accent"] == "#38bdf8"


def test_theme_preference_parsing():
    assert themes.parse(None) == ("dexio", "system")
    assert themes.parse("aubergine.dark") == ("aubergine", "dark")
    assert themes.parse("nope.dark") == ("dexio", "dark")
    assert themes.parse("jade.sideways") == ("jade", "system")
    assert themes.valid("jade", "light") and not themes.valid("jade", "x")


def test_settings_offers_every_theme_and_mode():
    html = server_pages.appearance_panel("lagoon.dark")
    for tid, t in themes.THEMES.items():
        assert f'name="theme" value="{tid}"' in html, tid
        assert t["name"] in html
    for m in themes.MODES:
        assert f'name="mode" value="{m}"' in html, m
    assert 'value="lagoon" checked' in html and 'value="dark" checked' in html
    assert 'action="/settings/theme"' in html


def test_no_hardcoded_colours_left_in_the_app_chrome():
    """Colours belong in custom properties, or the canvas and DOM drift apart."""
    shell = (STATIC / "shell.html").read_text(encoding="utf-8")
    css = shell.split("<style>")[1].split("</style>")[0]
    assert "__THEME_CSS__" in css
    leftover = re.findall(r"#[0-9a-fA-F]{3,6}\b", css)
    assert not leftover, f"hardcoded colours outside the theme blocks: {leftover}"
    login_css = login.PAGE.split("<style>")[1].split("</style>")[0]
    login_css = login_css.replace(themes.css().replace("{", "{{").replace("}", "}}"), "")
    login_css = re.sub(r"style=\"[^\"]*\"", "", login_css)
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", login_css)


def test_mobile_breakpoint_turns_the_panel_into_a_sheet():
    css = server_page("/api/v1").split("</style>")[0]
    assert "@media (max-width: 720px)" in css
    mobile = css.split("@media (max-width: 720px)")[1]
    assert "translateY(100%)" in mobile, "panel should slide up from the bottom"
    assert "max-width: none" in mobile, "a 420px drawer does not fit a 390px screen"
    sheet = mobile.split("#panel {", 1)[1].split("}", 1)[0]
    # all the height below the header (Forrest, 2026-10-01), not 72dvh
    assert "top: 0;" in sheet and "height: 100%;" in sheet
    assert "dvh" not in sheet


def test_phone_sheet_drag_follows_the_finger_and_never_fights_the_scroll():
    """Pulling the sheet down on a phone trailed the finger through the .18s
    slide, and a drag at the top of a page could move the sheet and scroll the
    text at once (Forrest, 2026-09-26: "weird and jittery" in Chrome)."""
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    sheet = js.split("// ---- bottom sheet", 1)[1].split("// ---- panel width", 1)[0]
    assert 'panel.style.transition = "none"' in sheet, "no transition while the finger is down"
    assert "e.preventDefault()" in sheet and "{ passive: false }" in sheet, \
        "a sheet drag keeps the text still"
    assert "e.cancelable" in sheet, "a scroll the browser has begun stays a scroll"
    assert '.closest(".panel-head")' in sheet, "the head pulls the sheet at any scroll position"
    # ...on an iPhone too, where a pull on a scrolled page's handle went to the
    # text (Forrest, 2026-10-01): the browser never scrolls from the head, and
    # the script holds its first moves
    assert "pull.onHead && e.cancelable) e.preventDefault()" in sheet
    shell = (STATIC / "shell.html").read_text(encoding="utf-8")
    assert 'matchMedia("(max-width: 720px)")' in sheet and "@media (max-width: 720px)" in shell, \
        "only where the panel is a sheet"
    head = shell.split("@media (max-width: 720px)")[1].split("#panel .panel-head {", 1)[1].split("}", 1)[0]
    assert "touch-action: none" in head


def test_canvas_opts_out_of_browser_gestures():
    """Without touch-action:none the browser scrolls the page instead of panning."""
    assert "touch-action: none" in server_page("/api/v1")


def test_login_inputs_are_large_enough_not_to_trigger_ios_zoom():
    css = server_pages.auth_page("login", "password", email="a@b.co").split("</style>")[0]
    assert "font-size:16px" in css.replace(" ", "")
    assert "min-height:44px" in css.replace(" ", "")


def test_graph_reads_its_colours_from_css_and_repaints_on_change():
    js = server_page("/api/v1")
    assert "readTheme" in js
    assert 'dexio:theme' in js
    assert "PALETTES" in js and "dark:" in js


def test_graph_handles_touch():
    js = server_page("/api/v1")
    for handler in ("touchstart", "touchmove", "touchend", "touchcancel"):
        assert handler in js, handler
    assert "pinchDistance" in js, "pinch to zoom"
    assert "TAP_SLOP" in js, "tap must be distinguishable from a drag"


def test_right_click_on_the_graph_closes_the_page():
    js = server_page("/api/v1")
    assert 'canvas.addEventListener("contextmenu"' in js
    assert "e.button !== 0" in js, "only the left button may open a page"


def test_middle_button_always_pans():
    """A left drag that starts on a node moves the node; the middle button
    pans from anywhere, nodes included (Forrest, 2026-09-26)."""
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    down = js.split('canvas.addEventListener("mousedown"', 1)[1].split("\n  });\n", 1)[0]
    middle = down.split("if (e.button === 1) {", 1)[1].split("return;", 1)[0]
    assert "e.preventDefault()" in middle, "no autoscroll on Windows and Linux"
    assert "state.pan =" in middle and "nodeAt" not in middle
    assert down.index("e.button === 1") < down.index("nodeAt("), "pans before looking for a node"


def test_page_panel_has_a_contents_sidebar_like_wikipedia():
    """A page's sections beside its text, hidden into a button by the title
    and pinned back from that button's menu (Forrest, 2026-09-26)."""
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    for part in ('class="toc toc-side"', 'class="toc toc-pop"', 'class="toc-btn"',
                 '"hide"', '"move to sidebar"', "(Top)", '"dexio-toc"'):
        assert part in js, part
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    pinned = css.split("  #panel.toc-pinned {", 1)[1].split("}", 1)[0]
    assert "display: grid" in pinned and "--toc-room" in pinned
    # the column widens the panel rather than narrowing the text
    panel = css.split("  #panel {", 1)[1].split("}", 1)[0]
    assert "var(--toc-room, 0px)" in panel.split("width:", 1)[1].split(";", 1)[0]
    # the resize grip accounts for the column, so the edge follows the pointer
    grip = js.split("function setPanelWidth(", 1)[1].split("\n  }\n", 1)[0]
    assert "tocRoom()" in grip
    # Esc closes the menu before it would close the page
    assert 'e.key !== "Escape" || !tocMenuOpen()' in js


def test_hiding_the_contents_moves_only_the_panel_edge():
    """Hiding the column shrank the panel by its full 230px while the text's
    left padding came back, so the text narrowed 24px and the whole page
    rewrapped under the reader (Forrest, 2026-09-26). The column now adds its
    width less that padding, and a wide panel keeps room for it, so the text
    column never changes."""
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    assert "--toc-add: calc(var(--toc-w) + var(--toc-gap) - var(--panel-pad))" in css
    assert "padding: 0 var(--panel-pad) var(--panel-pad)" in css
    assert "@media (min-width: 1000px) { #panel { --toc-hold: var(--toc-add); } }" in css
    panel = css.split("  #panel {", 1)[1].split("}", 1)[0]
    assert "var(--panel-max) - var(--toc-hold, 0px)" in panel
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    assert 'matchMedia("(min-width: 1000px)")' in js, "column breakpoint matches the stylesheet"
    grip = js.split("function setPanelWidth(", 1)[1].split("\n  }\n", 1)[0]
    assert "tocHold()" in grip and "tocRoom()" in grip


def test_contents_column_resizes_from_its_boundary():
    """The boundary between the contents and the text drags, like the panel's
    own edge; the panel's edges stay put and the text gives up what the column
    gains (Forrest, 2026-09-26)."""
    for name in ("offline", "server"):
        html = pages()[name]
        grip = html.split('<div id="toc-grip"', 1)[1].split("</div>", 1)[0]
        assert 'role="separator"' in grip and "Resize contents" in grip, name
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    fn = js.split("function setTocWidth(", 1)[1].split("\n  }\n", 1)[0]
    # the column's width sets --toc-w, and --panel-w absorbs it so the total holds
    assert '"--toc-w"' in fn and '"--panel-w"' in fn and "total - (w + gap - pad)" in fn
    assert "PANEL_MIN" in fn and "TOC_W_MAX" in fn and "TOC_W_MIN" in fn
    assert '"dexio-toc-w"' in js
    # the saved column width is applied before the saved panel width, which
    # is clamped to leave room for it
    init = js.split("if (grip && panel) {", 1)[1]
    assert init.index("TOC_W_KEY") < init.index("PANEL_KEY")


def test_contents_boundary_sits_in_the_middle_of_the_gap_that_shows():
    """The grip sat at the middle of the grid's gap, but the column's own right
    padding shows as gap too, so its line read 3px right of the middle of the
    space between the list and the text (Forrest, 2026-09-27). It is placed from
    what shows, the drag uses the same offset so the line stays under the
    pointer, and it is placed again when the column's scrollbar can come or go."""
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    place = js.split("function placeGrip(", 1)[1].split("\n  }\n", 1)[0]
    assert "tocSeam()" in place and '"--toc-gap") / 2' not in place
    seam = js.split("function tocSeam(", 1)[1].split("\n  }\n", 1)[0]
    assert "paddingRight" in seam and "side.offsetWidth - side.clientWidth" in seam
    drag = js.split('tocGrip.addEventListener("pointerdown"', 1)[1].split("\n    });\n", 1)[0]
    assert "tocSeam()" in drag
    fill = js.split("function fillPage(", 1)[1].split("\n  }\n", 1)[0]
    assert "placeGrip()" in fill
    fold = js.split('t.closest(".toc-chev")', 1)[1].split("return;", 1)[0]
    assert "placeGrip()" in fold
    # the page head no longer reaches into the gap, so its rule stops at the text
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    assert "#panel.toc-pinned .panel-head { margin-left: 0; padding-left: 0; }" in css


def test_section_headings_are_prominent():
    """## sections were 15px bold, the size of the text. Now Wikipedia's:
    a serif with a rule under it, and ### a size up in bold."""
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    sections = css.split("  .md h2, .md h3 {", 1)[1].split("}", 1)[0]
    assert "var(--serif)" in sections and "border-bottom" in sections
    size = int(re.search(r"\.md h3 \{ font-size: (\d+)px", css).group(1))
    assert size >= 20
    title = css.split("#panel .panel-title h2 {", 1)[1].split("}", 1)[0]
    assert int(re.search(r"font-size: (\d+)px", title).group(1)) > size, \
        "the title stays a size above the sections"


def test_app_header_shows_the_wordmark():
    """The graph view's top left is the logo, not the name typed out."""
    for name in ("offline", "server"):
        header = header_of(pages()[name])
        assert 'class="brand"' in header, name
        assert 'aria-label="Dexio"' in header and "<path d=" in header, name
        assert "<h1>Dexio</h1>" not in header, name
    server = pages()["server"].split("<header>")[1]
    assert '<a class="brand" href="/"' in server


def test_export_keeps_the_wiki_name_beside_the_wordmark():
    html = offline(GRAPH, title="notes <b>")
    header = header_of(html)
    assert "<h1>notes &lt;b&gt;</h1>" in header
    assert "<title>notes &lt;b&gt;</title>" in html


def test_one_wordmark_everywhere():
    """Sign-in and the app header draw the same outlines."""
    from dexio.brand import PATH
    for name, html in pages().items():
        assert PATH in html, name


def test_wordmark_is_one_colour():
    """Since 2026-09-26 the serif wordmark has no accent dot: the mark carries the colour."""
    from dexio.brand import wordmark
    svg = wordmark(26)
    assert "<circle" not in svg and "--accent" not in svg


def test_graph_mark_leads_the_wordmark_everywhere():
    """The graph mark (2026-09-26) sits before the wordmark on sign-in and in the header."""
    from dexio.brand import MARK_SPOKES, PATH
    for name, html in pages().items():
        assert MARK_SPOKES in html, name
        assert html.index(MARK_SPOKES) < html.index(PATH), name


def test_favicon_is_the_graph_mark():
    import base64
    import re
    for name, html in pages().items():
        m = re.search(r'rel="icon" href="data:image/svg\+xml;base64,([^"]+)"', html)
        assert m, name
        svg = base64.b64decode(m.group(1)).decode()
        assert "hub and four nodes" in svg and 'fill="#38bdf8"' in svg, name
        assert "http://" not in html.split(m.group(1))[0][-40:], "data URI must stay base64"


def test_export_can_pin_a_mode():
    """The graph on the light-only marketing site must not follow the visitor's
    OS dark mode or a saved choice (Forrest, 2026-09-26)."""
    pinned = offline(GRAPH, mode="light")
    head = pinned.split("</head>")[0]
    pref = head.split("function pref() {", 1)[1].split("}", 1)[0]
    assert pref.strip().startswith('return ["dexio", "light"];')
    assert "__PIN__" not in pinned
    # without a mode the export follows the saved choice and the OS, as the app does
    free = offline(GRAPH)
    assert "__PIN__" not in free
    assert 'return ["dexio", "light"];' not in free
    assert themes.head_script() in free
    for bad in (("nope", "light"), ("dexio", "system"), ("dexio", "sepia")):
        with pytest.raises(ValueError):
            themes.head_script(bad)


def test_an_export_of_several_wikis_has_the_apps_wiki_picker():
    """The dexio.wiki demo holds its three sample wikis in one file and switches
    between them with the app's wiki picker (Forrest, 2026-09-27: "a proper wiki
    picker that matches the in-app design, with three options")."""
    import json
    from dexio.render import EXPORT_SWITCHER_JS, offline_wikis
    two = {"nodes": GRAPH["nodes"] + [{"id": "b", "title": "B", "folder": "", "degree": 1}],
           "links": [], "stats": {}, "dangling": []}
    html = offline_wikis([("research", GRAPH), ("ops", two), ("support", GRAPH)], current="ops")
    head = header_of(html)
    # the app's button, named for the wiki it opens on, where the name was
    assert 'id="switcher-button"' in head and '<span class="sw-wiki">ops</span>' in head
    assert 'id="switcher-menu" role="menu"' in head and "<h1>" not in head
    assert "<title>ops</title>" in html
    listed = json.loads(re.search(r"window\.DEXIO_WIKIS = (\[.*?\]);window\.DEXIO_WIKI =", html)
                        .group(1).replace("<\\/", "</"))
    assert [(w["name"], w["pages"]) for w in listed] == [("research", 1), ("ops", 2), ("support", 1)]
    assert 'window.DEXIO_WIKI = "ops";' in html
    # nothing behind it: no New wiki, no Manage wikis, no requests
    for gone in ("New wiki", "Manage wikis", "fetch(", "DEXIO_API"):
        assert gone not in EXPORT_SWITCHER_JS, gone
    # same classes as the app's menu, so the same look
    for cls in ('"sw-head"', '"sw-item"', '"sw-meta"', "sw-check", "menuitemradio"):
        assert cls in EXPORT_SWITCHER_JS, cls
    # a current name it does not hold opens the first
    assert '<span class="sw-wiki">research</span>' in header_of(
        offline_wikis([("research", GRAPH), ("ops", two)], current="nope"))


def test_an_export_puts_search_at_the_right():
    """In the app search is centred, with the account menu on the right. An
    export has nothing on the right, so search goes there (Forrest,
    2026-09-27)."""
    from dexio.render import offline_wikis
    for html in (offline(GRAPH), offline_wikis([("a", GRAPH), ("b", GRAPH)])):
        assert '<header class="export">' in html
    assert '<header class="export">' not in pages()["server"]
    css = pages()["server"].split("<style>", 1)[1].split("</style>", 1)[0]
    assert "header.export .h-end { display: none; }" in css
    assert "header.export #find { flex: 0 1 320px; max-width: 320px; min-width: 200px; margin-left: auto;" in css
    # the app's own narrow-screen rule leaves the export alone
    assert "header:has(#crumbs):not(.export) #find" in css
    assert "header:has(#crumbs) #find" not in css


def test_history_rail_runs_between_the_first_and_last_initials():
    """The rail ran from the top of the newest revision's row, so a few pixels
    of it showed above that initial (Forrest, 2026-09-28). It starts at the
    initial's centre, as it already ended at the oldest one's, and a single
    revision has no rail at all."""
    css = (STATIC / "shell.html").read_text(encoding="utf-8")
    first = "#panel .hist-list > .rev-day:first-child + .rev-item"
    assert f"{first}::before {{ top: 19px; }}" in css
    assert "#panel .hist-list > .rev-item:last-child::before { bottom: auto; height: 19px; }" in css
    assert f"{first}:last-child::before {{ display: none; }}" in css
    # 19px is the initial's centre: the row's 8px padding, the node's -1px
    # offset and half its 24px height
    rev = css.split("  #panel .hist-list .rev {", 1)[1].split("}", 1)[0]
    node = css.split("  #panel .rev-node {", 1)[1].split("}", 1)[0]
    assert "padding: 8px;" in rev and "height: 24px; margin-top: -1px;" in node
    # the list always opens on a day heading, which the selector relies on
    js = (STATIC / "graph.js").read_text(encoding="utf-8")
    shown = js.split("function showHistory(", 1)[1].split("\n  }\n", 1)[0]
    assert "hist.lastDay = null;" in shown
