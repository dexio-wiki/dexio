"""The header's workspace menu (render.server_page) and the menu Settings shares
(render.workspace_menu). A workspace has one wiki (2026-09-28), so the header has
no wiki button beside it. Their behaviour in a browser (keyboard, switching,
creating, closing, phones) was checked in Chrome when they were built; these keep
the markup and the escaping from regressing."""
import json
import re

from dexio.render import STATIC, WS_MENU_CSS, server_page, workspace_menu

WS = [{"id": 1, "handle": "dana2345", "name": "Dana's workspace", "plan": "free",
       "role": "owner"},
      {"id": 7, "handle": "north789", "name": "Northwind", "plan": "team", "role": "member"}]


def header(html: str) -> str:
    return html.split("<header>")[1].split("</header>")[0]


def test_a_server_without_plans_shows_none_beside_the_names():
    h = header(server_page("/api/v1", workspaces=WS, current=7))
    assert ">Team</span>" not in h and ">Free</span>" not in h
    assert '<span class="wsm-label">Northwind</span>' in h


def test_the_header_has_the_workspace_menu_and_no_wiki_button():
    h = header(server_page("/api/v1", workspaces=WS, current=7))
    assert "<select" not in h
    btn = re.search(r'<button[^>]*id="ws-switch-button"[^>]*>', h).group(0)
    assert 'aria-haspopup="menu"' in btn and 'aria-expanded="false"' in btn
    assert 'aria-controls="ws-switch"' in btn
    assert re.search(r'<div[^>]*id="ws-switch" role="menu"[^>]*hidden>', h)
    assert '<span class="wsm-label">Northwind</span>' in h        # the current workspace
    for gone in ('id="switcher"', 'id="switcher-button"', 'class="sw-sep"', "sw-wiki",
                 "New wiki", "Manage wikis"):
        assert gone not in h, gone


def test_workspace_menu_lists_switches_creates_and_links_settings(plans):
    h = header(server_page("/api/v1", workspaces=WS, current=7))
    items = re.findall(r'<a class="wsm-item" role="menuitemradio"[^>]*>', h)
    hrefs = [re.search(r'href="([^"]+)"', i).group(1) for i in items]
    assert hrefs == ["/?w=dana2345", "/?w=north789"]
    assert ['aria-checked="true"' in i for i in items] == [False, True]
    assert ">Team</span>" in h and ">Free</span>" in h                  # plans beside names
    assert 'data-key="new-ws"' in h and "New workspace" in h
    form = re.search(r'<form class="wsm-form" method="post" action="/settings/workspaces" hidden>',
                     h)
    assert form and 'name="name"' in h and 'required' in h
    assert "A workspace has its own wiki, members, agents and plan." in h
    assert 'href="/settings?w=north789"' in h and "Settings for Northwind" in h
    # every workspace keeps one colour wherever it shows
    assert h.count('style="--h:') == 3                                   # button + two rows


def test_the_script_loads_the_wiki_at_once_and_has_no_create_step():
    from dexio.render import APP_JS
    init = APP_JS.rsplit("(async function () {", 1)[1]
    # the graph is asked for before anything waits, the naming step included
    assert init.index('fetch(window.DEXIO_API + "/graph') < init.index("await ")
    assert init.index("if (ASK_NAME) await nameFlow();") < init.index("const r = await graph;")
    for gone in ("createFlow", "/wikis", "/projects", "sw-new"):
        assert gone not in APP_JS, gone
    assert 'window.DEXIO_PROJECT = "main"' in APP_JS


def test_an_empty_wiki_starts_onboarding_and_keeps_it_until_the_graph_answers():
    from dexio.render import APP_JS
    wrap = lambda html: html.split('<div id="wrap"')[1].split(">")[0]
    assert wrap(server_page("/api/v1", empty=True)) == ' class="onboarding"'
    assert wrap(server_page("/api/v1")) == ""
    init = APP_JS.rsplit("(async function () {", 1)[1]
    before = init.split("/graph")[0]
    assert "closeFlow()" not in before, "clearing the flow first flashed the folders"
    assert "closeFlow()" in init.split("/graph")[1]


def test_one_workspace_still_offers_a_new_one():
    h = header(server_page("/api/v1", workspaces=WS[:1], current=1))
    assert '<span class="wsm-label">Dana&#x27;s workspace</span>' in h
    assert 'data-key="new-ws"' in h


def test_settings_menu_keeps_the_section_and_has_no_settings_link():
    m = workspace_menu(WS, 1, lambda w: f"/settings/members?w={w['handle']}", menu_id="ws-menu")
    assert 'href="/settings/members?w=north789"' in m and 'id="ws-menu-button"' in m
    assert "Settings for" not in m and 'id="ws-menu-new"' in m


def test_workspace_names_are_escaped_in_the_markup_and_the_bootstrap():
    evil = [{"id": 1, "handle": "evil2345",
             "name": '<img src=x onerror=alert(1)></script><script>alert(2)',
             "plan": "free"}]
    html = server_page("/api/v1", workspaces=evil, current=1)
    assert "<img src=x" not in header(html)
    assert '<span class="wsm-label">&lt;img src=x' in header(html)
    assert '<span class="wsm-tile" style="--h:137" aria-hidden="true">&lt;</span>' in header(html)
    # in the bootstrap it is a JSON string, with </ broken so the script tag holds
    boot = html.split("window.DEXIO_WORKSPACES = ")[1].split(";window.")[0]
    assert "</script>" not in boot
    assert json.loads(boot.replace("<\\/", "</"))[0]["name"].startswith("<img")


def test_bootstrap_carries_workspaces_but_not_roles():
    html = server_page("/api/v1", workspaces=WS, current=7)
    boot = json.loads(html.split("window.DEXIO_WORKSPACES = ")[1].split(";window.")[0])
    # the browser gets handles, never the ids, which count up and date a workspace
    assert boot == [{"id": "dana2345", "name": "Dana's workspace", "plan": "free"},
                    {"id": "north789", "name": "Northwind", "plan": "team"}]
    assert 'window.DEXIO_WORKSPACE = "north789";' in html
    assert 'window.DEXIO_PROJECT = "main";' in html
    assert "DEXIO_PROJECTS" not in html


def test_menus_use_the_page_colours_and_phones_keep_one_row():
    css = (STATIC / "shell.html").read_text()
    menu = css.split("#switcher-menu {")[1].split("}")[0]              # the exported file's
    assert "background: var(--panel)" in menu and "--accent: var(--page-accent)" in menu
    assert "background: var(--panel)" in WS_MENU_CSS.split(".wsm-menu {")[1].split("}")[0]
    assert "header .wsm-menu { --accent: var(--page-accent); }" in css
    phone = css.split("@media (max-width: 480px)")[1].split("}\n  }")[0]
    assert "header .wsm-label { display: none; }" in phone
    # the app's narrow-screen rule keys on the header's menu, not the old wiki button
    assert "header:has(#crumbs):not(.export)" in css and "header:has(#switcher)" not in css
    # the shared styles reach the graph page
    assert ".wsm-menu {" in server_page("/api/v1", workspaces=WS, current=1)
