"""Focus on a page (Forrest, 2026-10-05: "can we get a focus mode for wiki
pages?"): the open page fills the window under the header, its text and
contents in the middle at the width they had in the panel. Checked in Chromium
against a local server when built; these pin the contracts in place."""
from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).parent.parent / "src" / "dexio" / "static"
JS = (STATIC / "graph.js").read_text(encoding="utf-8")
SHELL = (STATIC / "shell.html").read_text(encoding="utf-8")


def body(name: str) -> str:
    return JS.split(f"function {name}(", 1)[1].split("\n  }\n", 1)[0]


def focus_css() -> str:
    return SHELL.split("/* ---- focus ----", 1)[1].split("\n\n", 1)[0]


def test_the_button_sits_left_of_close_in_one_box():
    sp = body("showPage")
    assert '<div class="head-acts">${focusButton()}' in sp
    assert sp.index("focusButton()") < sp.index('id="close"')
    assert 'querySelector(".focus-btn").onclick = () => setPageFocus(!pageFocus)' in sp
    # applied as the panel opens, so a page opened in focus never shows beside the graph
    assert sp.index('panel.classList.add("open")') < sp.index("applyPageFocus();")
    assert sp.index("applyPageFocus();") < sp.index("revealBesidePanel(n)")
    b = body("focusButton")
    assert 'aria-pressed="false"' in b and 'class="focus-btn"' in b


def test_the_text_keeps_its_width_and_sits_in_the_middle():
    css = focus_css()
    panel = SHELL.split("  #panel {", 1)[1].split("}", 1)[0]
    assert "width: var(--panel-box);" in panel
    # the sides take what the panel's own width leaves, half each
    assert "--focus-side: max(0px, calc((100% - var(--panel-box)) / 2));" in css
    assert "padding-left: calc(var(--focus-side) + var(--panel-pad));" in css
    assert "padding-right: calc(var(--focus-side) + var(--panel-pad));" in css
    # the pinned contents take the text's left padding, as beside the graph
    assert "#wrap.page-focus #panel.toc-pinned { padding-left: var(--focus-side); }" in css
    # the 1px edge stays, unseen, so the text is not a pixel wider
    assert "border-left-color: transparent" in css
    # the graph, folders and their controls go; a toast still shows
    assert "#wrap.page-focus > :not(#panel, #toast) { visibility: hidden; }" in css


def test_wide_screens_only():
    css = focus_css()
    assert "#panel .focus-btn { display: none; }" in css
    assert "@media (min-width: 721px)" in css
    assert css.index("#panel .focus-btn { display: none; }") < css.index("@media (min-width: 721px)")
    apply = body("applyPageFocus")
    assert 'pageFocus && wide.matches && panel.classList.contains("open")' in apply


def test_esc_leaves_focus_before_it_closes_the_page():
    esc = JS.split("Esc closes the page, unless the key was meant for a form field.", 1)[1]
    esc = esc.split("});", 1)[0]
    assert esc.index("if (focusOn()) { setPageFocus(false); return; }") < esc.index("select(null);")


def test_f_toggles_it_outside_fields():
    keys = JS.split("// ---- focus ---", 1)[1].split("// ---- contents ---", 1)[0]
    handler = keys.split('window.addEventListener("keydown"', 1)[1].split("});", 1)[0]
    assert '(e.key || "").toLowerCase() !== "f"' in handler
    assert "e.metaKey || e.ctrlKey || e.altKey || e.shiftKey" in handler, "Cmd+F stays search"
    assert "INPUT|SELECT|TEXTAREA" in handler and "isContentEditable" in handler
    assert 'document.body.classList.contains("sd-open")' in handler


def test_closing_the_page_ends_it_and_the_graph_frames_for_the_panel():
    sel = body("select")
    assert 'if (!n) { setPageFocus(false); panel.classList.remove("open");' in sel
    # focus is kept for the tab, so a reload keeps it
    assert 'sessionStorage.getItem(FOCUS_KEY) === "1"' in JS
    assert 'const FOCUS_KEY = "dexio-focus";' in JS
    # no grips while the page has the whole width
    assert '&& !focusOn();' in body("placeGrip")
    # radial frames itself for the panel's width beside the graph, not the window's
    assert "focusOn() ? focusSideW : panel.offsetWidth" in body("coveredRight")
    assert "focusOn()) return;" in body("revealBesidePanel")
