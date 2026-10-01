"""Render a graph to a single self-contained HTML file (no CDN, no build step)."""
from __future__ import annotations

import html
import json
import os
import re
from pathlib import Path
from urllib.parse import quote

from . import ais, themes
from .brand import logo

STATIC = Path(__file__).parent / "static"

# The Share button's icon: a person and a plus, as sharing reads in Google Docs.
SHARE_ICON = ('<svg class="share-icon" viewBox="0 0 16 16" aria-hidden="true">'
              '<circle cx="6.2" cy="5.2" r="2.6"></circle>'
              '<path d="M1.6 13.6c.5-2.5 2.3-3.9 4.6-3.9s4.1 1.4 4.6 3.9M12.6 4.6v4.4M10.4 6.8h4.4">'
              '</path></svg>')

# Header wordmark height in px: sits inside the 34px controls row.
BRAND_HEIGHT = 20


def _assets(pin: tuple[str, str] | None = None) -> tuple[str, str]:
    shell = ((STATIC / "shell.html").read_text(encoding="utf-8")
             .replace("__THEME_HEAD__", themes.head_script(pin))
             .replace("__THEME_CSS__", themes.css()))
    return shell, (STATIC / "graph.js").read_text(encoding="utf-8")


def _safe_json(obj) -> str:
    # </script> inside note text must not close the tag early.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


def offline(graph_dict: dict, title: str = "Dexio", mode: str | None = None) -> str:
    """mode "light" or "dark" fixes the export to that mode in the default
    theme, ignoring the viewer's saved choice and the operating system; None
    follows them, as the app does."""
    # One wiki and no picker, so the wiki's own name follows the wordmark.
    name = f"<h1>{html.escape(title)}</h1>" if title != "Dexio" else ""
    return _export(title, mode, name, f"dexio.load({_safe_json(graph_dict)});")


def offline_wikis(wikis: list[tuple[str, dict]], current: str | None = None,
                  mode: str | None = None) -> str:
    """One file holding several wikis, (name, graph_dict) pairs in menu order,
    with the app's wiki picker in the header to switch between them; it opens
    on `current`, or the first. The dexio.wiki demo (Forrest, 2026-09-27: "a
    proper wiki picker that matches the in-app design, with three options")."""
    names = [n for n, _ in wikis]
    if not names:
        raise ValueError("offline_wikis needs at least one wiki")
    first = current if current in names else names[0]
    picker = (
        '<div id="crumbs"><div id="switcher">'
        '<button id="switcher-button" type="button" aria-haspopup="menu" aria-expanded="false"'
        ' aria-controls="switcher-menu" title="Switch wiki">'
        f'<span class="sw-wiki">{html.escape(first)}</span>{CHEVRON}</button>'
        '<div id="switcher-menu" role="menu" aria-label="Wikis" hidden></div>'
        '</div></div>')
    listed = [{"name": n, "pages": len(g.get("nodes") or []), "graph": g} for n, g in wikis]
    init = (f"window.DEXIO_WIKIS = {_safe_json(listed)};"
            f"window.DEXIO_WIKI = {_safe_json(first)};" + EXPORT_SWITCHER_JS)
    return _export(first, mode, picker, init)


def _export(title: str, mode: str | None, after_brand: str, init: str) -> str:
    shell, js = _assets((themes.DEFAULT_THEME, mode) if mode else None)
    brand = f'<span class="brand">{logo(BRAND_HEIGHT)}</span>'
    # class="export": search sits at the header's right, not centred as in the
    # app (shell.html, "the exported file").
    return (shell
            .replace("<header>", '<header class="export">', 1)
            .replace("__TITLE__", html.escape(title))
            .replace("__BRAND__", brand + after_brand)
            .replace("__PROJECT_PICKER__", "")
            .replace("__ACCOUNT__", "")
            .replace("__BOOTSTRAP__", "")
            .replace("__GRAPH_JS__", js)
            .replace("__INIT__", init))


# The account menu, top right: the person's initials, opening a menu of where
# they go next. Server only: an offline HTML export has no account. Replaced a
# person-outline icon in a box on 2026-09-27 (Forrest: "isn't doing it for
# me"): it looked like a profile button but went straight to the workspace's
# General settings, with no name on it, no menu and no way to sign out.
def _icon(paths: str) -> str:
    return f'<svg class="ac-icon" viewBox="0 0 16 16" aria-hidden="true">{paths}</svg>'


AC_ICONS = {
    "settings": _icon('<circle cx="8" cy="8" r="2.2"></circle><path d="M8 1.8v1.6M8 12.6v1.6'
                      'M1.8 8h1.6M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M3.6 12.4l1.1-1.1'
                      'M11.3 4.7l1.1-1.1"></path>'),
    "invite": _icon('<circle cx="6.2" cy="5.4" r="2.4"></circle><path d="M1.8 13.2c.7-2.4 2.4-3.6'
                    ' 4.4-3.6s3.7 1.2 4.4 3.6M12.4 5v4M10.4 7h4"></path>'),
    "members": _icon('<circle cx="6" cy="5.4" r="2.4"></circle><path d="M1.8 13.2c.7-2.4 2.3-3.6'
                     ' 4.2-3.6s3.5 1.2 4.2 3.6M10.6 3.4a2.3 2.3 0 0 1 0 4.2M12.4 9.8c.9.6 1.5'
                     ' 1.8 1.8 3.4"></path>'),
    "connect": _icon('<path d="M6 2.5v3M10 2.5v3M4.5 5.5h7v2.2a3.5 3.5 0 0 1-7 0zM8 11.2v2.6">'
                     '</path>'),
    "profile": _icon('<circle cx="8" cy="5.6" r="2.6"></circle><path d="M3 13.5c.8-2.6 2.7-3.9'
                     ' 5-3.9s4.2 1.3 5 3.9"></path>'),
    "appearance": _icon('<circle cx="8" cy="8" r="5.6"></circle><path d="M8 2.4v11.2a5.6 5.6'
                        ' 0 0 0 0-11.2z" class="fill"></path>'),
    "help": _icon('<circle cx="8" cy="8" r="5.6"></circle><path d="M6.4 6.3a1.7 1.7 0 1 1 2.4'
                  ' 1.6c-.5.3-.8.6-.8 1.2v.3"></path><circle cx="8" cy="11.1" r=".75"'
                  ' class="fill"></circle>'),
    "signout": _icon('<path d="M6.5 2.8H4a1.2 1.2 0 0 0-1.2 1.2v8A1.2 1.2 0 0 0 4 13.2h2.5'
                     'M10 5l3 3-3 3M13 8H6.2"></path>'),
}


def initials(first: str, last: str, email: str) -> str:
    """One or two capital letters for the avatar: first and last name, else the
    first name, else the email."""
    if first and last:
        return (first[0] + last[0]).upper()
    return (first or last or email or "?")[0].upper()


def account_menu(name: str, email: str, first: str = "", last: str = "",
                 workspace: str = "", owner: bool = False) -> str:
    esc = html.escape

    def item(key: str, label: str, href: str) -> str:
        return (f'<a class="sw-item" role="menuitem" tabindex="-1" href="{href}">'
                f'{AC_ICONS[key]}<span class="sw-name">{esc(label)}</span></a>')

    team = (item("invite", "Invite people", "/settings/members") if owner
            else item("members", "Members", "/settings/members"))
    return (
        '<div id="acct">'
        '<button id="acct-button" type="button" aria-haspopup="menu" aria-expanded="false"'
        f' aria-controls="acct-menu" title="{esc(name)}: account and settings"'
        f' aria-label="Account and settings for {esc(name)}">'
        f'<span class="avatar" aria-hidden="true">{esc(initials(first, last, email))}</span>'
        '</button>'
        '<div id="acct-menu" role="menu" aria-label="Account and settings" hidden>'
        f'<div class="ac-who"><span class="avatar" aria-hidden="true">'
        f'{esc(initials(first, last, email))}</span><span class="ac-id">'
        f'<b>{esc(name)}</b>' + (f'<span>{esc(email)}</span>' if email != name else "")
        + '</span></div>'
        '<div class="sw-rule"></div>'
        f'<div class="sw-head">{esc(workspace)}</div>'
        + item("settings", "Settings", "/settings") + team
        + item("connect", "Connect an agent", "/settings/agents?connect")
        + '<div class="sw-rule"></div>'
        + item("profile", "Profile", "/settings/profile")
        + item("appearance", "Appearance", "/settings/appearance")
        + item("help", "Help", "/settings/help")
        + '<div class="sw-rule"></div>'
        '<form method="post" action="/logout"><button class="sw-item" type="submit"'
        f' role="menuitem" tabindex="-1">{AC_ICONS["signout"]}'
        '<span class="sw-name">Sign out</span></button></form>'
        '</div></div>')


ACCOUNT_JS = r"""
(function () {
  const root = document.getElementById("acct");
  if (!root) return;
  const btn = document.getElementById("acct-button");
  const menu = document.getElementById("acct-menu");
  const items = () => [...menu.querySelectorAll(".sw-item")];
  // Connect an AI opens Settings > Agents for this workspace.
  const connect = menu.querySelector('a[href="/settings/agents?connect"]');
  if (connect) connect.addEventListener("click", e => {
    const u = new URL(connect.href);
    if (window.DEXIO_WORKSPACE) u.searchParams.set("w", window.DEXIO_WORKSPACE);
    e.preventDefault();
    location.href = u.pathname + u.search;
  });
  function open(last) {
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    const list = items();
    if (list.length) list[last ? list.length - 1 : 0].focus();
  }
  function close(refocus) {
    if (menu.hidden) return;
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    if (refocus) btn.focus();
  }
  btn.addEventListener("click", () => (menu.hidden ? open(false) : close(true)));
  btn.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(e.key === "ArrowUp"); }
  });
  menu.addEventListener("keydown", e => {
    const list = items(), i = list.indexOf(document.activeElement);
    const go = j => { e.preventDefault(); list[(j + list.length) % list.length].focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === "Tab") close(false);
  });
  document.addEventListener("pointerdown", e => { if (!root.contains(e.target)) close(false); });
})();
"""


# ---- workspace menu ---------------------------------------------------------
# The workspace switcher, in the graph header and at the top of Settings' sidebar:
# a button naming the workspace (a coloured initial, then the name) that opens the
# workspaces, a check on the current one, New workspace, and in the graph a link
# to its settings. Rendered by the server, so Settings needs no data fetch. It
# replaced (2026-09-27, Forrest asked for a better design) a plain native select in
# Settings and a Workspaces group under the wikis in the graph's one combined
# menu, where a workspace (the container) was listed below its own wikis and
# nothing offered to create one.
PLUS_SVG = ('<svg class="wsm-icon" viewBox="0 0 16 16" aria-hidden="true">'
            '<path d="M8 3.5v9M3.5 8h9"></path></svg>')
GEAR_SVG = ('<svg class="wsm-icon" viewBox="0 0 16 16" aria-hidden="true">'
            '<circle cx="8" cy="8" r="2.2"></circle><path d="M8 1.8v1.6M8 12.6v1.6M1.8 8h1.6'
            'M12.6 8h1.6M3.6 3.6l1.1 1.1M11.3 11.3l1.1 1.1M3.6 12.4l1.1-1.1M11.3 4.7l1.1-1.1">'
            '</path></svg>')
CHECK_SVG = ('<svg class="wsm-check" viewBox="0 0 16 16" aria-hidden="true">'
             '<path d="M3.5 8.4 6.6 11.4 12.5 4.8"></path></svg>')


def ws_tile(ws: dict, cls: str = "wsm-tile") -> str:
    """A workspace's initial on a colour of its own, the same everywhere it
    appears. Hues step by the golden angle so neighbouring ids differ."""
    name = str(ws.get("name") or "").strip()
    hue = (int(ws.get("id") or 0) * 137) % 360
    return (f'<span class="{cls}" style="--h:{hue}" aria-hidden="true">'
            f'{html.escape((name[:1] or "?").upper())}</span>')


def workspace_menu(workspaces: list[dict], current: int | None, href, *, menu_id: str,
                   settings: bool = False, shared: list[dict] | None = None) -> str:
    """href(ws) is where picking a workspace goes. settings adds a link to the
    current workspace's settings (the graph; Settings itself does not need one).
    shared: workspaces that share something with this person without them being
    a member (shares.shared_with), listed under Shared with you."""
    esc = html.escape
    ws = next((w for w in workspaces if w["id"] == current), workspaces[0] if workspaces else None)
    if not ws:
        return ""
    # A server with no Stripe key sells no plans (server.db.plans_apply), so it
    # shows none beside the names.
    sells = bool(os.environ.get("STRIPE_SECRET_KEY"))
    plan = lambda w: str(w.get("plan") or "").title() if sells else ""  # noqa: E731
    rows = "".join(
        f'<a class="wsm-item" role="menuitemradio" tabindex="-1" data-key="ws:{esc(w["handle"])}"'
        f' aria-checked="{"true" if w["id"] == ws["id"] else "false"}" href="{esc(href(w))}">'
        f'{ws_tile(w)}<span class="wsm-name">{esc(w["name"])}</span>'
        f'<span class="wsm-meta">{esc(plan(w))}</span>{CHECK_SVG}</a>' for w in workspaces)
    if shared:
        rows += ('<div class="wsm-rule"></div><div class="wsm-head">Shared with you</div>' + "".join(
            f'<a class="wsm-item" role="menuitem" tabindex="-1" data-key="shared:{esc(s["handle"])}"'
            f' href="/w/{esc(s["handle"])}">{ws_tile(s)}<span class="wsm-name">{esc(s["name"])}'
            f'</span><span class="wsm-meta">View only</span></a>' for s in shared))
    link = (f'<a class="wsm-item wsm-quiet" role="menuitem" tabindex="-1" data-key="settings"'
            f' href="/settings?w={esc(ws["handle"])}">{GEAR_SVG}'
            f'<span class="wsm-name">Settings for {esc(ws["name"])}</span></a>'
            if settings else "")
    return (
        f'<div class="wsm" id="{menu_id}-root">'
        f'<button class="wsm-button" id="{menu_id}-button" type="button" aria-haspopup="menu"'
        f' aria-expanded="false" aria-controls="{menu_id}" title="Switch workspace"'
        f' aria-label="Workspace {esc(ws["name"])}. Switch or create a workspace">'
        f'{ws_tile(ws)}<span class="wsm-label">{esc(ws["name"])}</span>{CHEVRON}</button>'
        f'<div class="wsm-menu" id="{menu_id}" role="menu" aria-label="Workspaces" hidden>'
        f'<div class="wsm-head">Workspaces</div>{rows}'
        '<div class="wsm-rule"></div>'
        f'<button class="wsm-item wsm-quiet" type="button" role="menuitem" tabindex="-1"'
        f' data-key="new-ws">{PLUS_SVG}<span class="wsm-name">New workspace</span></button>'
        f'<form class="wsm-form" method="post" action="/settings/workspaces" hidden>'
        f'<label class="wsm-head" for="{menu_id}-new">New workspace</label>'
        f'<div class="wsm-form-row"><input id="{menu_id}-new" name="name" maxlength="80"'
        f' required autocomplete="off" placeholder="Acme Inc.">'
        f'<button class="wsm-create" type="submit">Create</button></div>'
        f'<p class="wsm-hint">A workspace has its own wiki, members, agents and plan.</p>'
        f'</form>{link}</div></div>')


WS_MENU_JS = r"""
(function () {
  document.querySelectorAll(".wsm").forEach(root => {
    const btn = root.querySelector(".wsm-button"), menu = root.querySelector(".wsm-menu");
    const form = menu.querySelector(".wsm-form"), add = menu.querySelector('[data-key="new-ws"]');
    const items = () => [...menu.querySelectorAll(".wsm-item")].filter(i => !i.hidden);
    function place() {
      menu.style.left = "0px";
      const r = menu.getBoundingClientRect(), over = r.right - (window.innerWidth - 12);
      if (over > 0) menu.style.left = -Math.min(over, r.left - 12) + "px";
    }
    function adding(on) {
      form.hidden = !on; add.hidden = on;
      if (on) form.querySelector("input").focus();
    }
    function open(last) {
      adding(false);
      menu.hidden = false;
      btn.setAttribute("aria-expanded", "true");
      place();
      const list = items();
      const target = last ? list[list.length - 1]
                          : list.find(i => i.getAttribute("aria-checked") === "true") || list[0];
      if (target) target.focus();
    }
    function close(refocus) {
      if (menu.hidden) return;
      menu.hidden = true;
      btn.setAttribute("aria-expanded", "false");
      if (refocus) btn.focus();
    }
    btn.addEventListener("click", () => (menu.hidden ? open(false) : close(true)));
    btn.addEventListener("keydown", e => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(e.key === "ArrowUp"); }
    });
    add.addEventListener("click", () => adding(true));
    menu.addEventListener("keydown", e => {
      if (e.target.closest(".wsm-form")) {           // typing; Escape leaves the form
        if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); adding(false); add.focus(); }
        return;
      }
      const list = items(), i = list.indexOf(document.activeElement);
      const go = j => { e.preventDefault(); if (list.length) list[(j + list.length) % list.length].focus(); };
      if (e.key === "ArrowDown") go(i + 1);
      else if (e.key === "ArrowUp") go(i - 1);
      else if (e.key === "Home") go(0);
      else if (e.key === "End") go(list.length - 1);
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === "Tab") close(false);
    });
    document.addEventListener("pointerdown", e => { if (!root.contains(e.target)) close(false); });
  });
})();
"""

# Shared by the graph (shell.html) and Settings (login.py SETTINGS_CSS). The menu
# is drawn in the page's colours; the button takes its surroundings' (the chrome
# in the graph header, the page in Settings).
WS_MENU_CSS = """
  .wsm { position: relative; min-width: 0; }
  .wsm-button { display: flex; align-items: center; gap: 8px; min-width: 0; max-width: 100%;
    height: 34px; margin: 0; padding: 0 8px 0 6px; border: 1px solid transparent;
    border-radius: 7px; background: transparent; color: inherit; font: inherit;
    font-size: 14px; white-space: nowrap; cursor: pointer; }
  .wsm-label { overflow: hidden; text-overflow: ellipsis; font-weight: 600; }
  .wsm-tile { flex: none; display: inline-grid; place-items: center; width: 22px; height: 22px;
    border-radius: 6px; background: hsl(var(--h) 45% 45%); color: var(--btn-text); font-size: 12px;
    font-weight: 700; line-height: 1; letter-spacing: 0; text-transform: none; }
  .wsm-button .chev, .wsm-button svg { flex: none; }
  .wsm-button .sw-chev { width: 14px; height: 14px; fill: none; stroke: currentColor;
    opacity: .55; stroke-width: 1.6; stroke-linecap: round; stroke-linejoin: round; }
  .wsm-menu { position: absolute; top: calc(100% + 6px); left: 0; z-index: 30;
    min-width: 260px; max-width: min(340px, calc(100vw - 24px));
    max-height: min(70dvh, 520px); overflow: auto; overscroll-behavior: contain;
    padding: 6px; border: 1px solid var(--line); border-radius: 10px; background: var(--panel);
    color: var(--text); box-shadow: var(--shadow); text-align: left; font-size: 14px; }
  .wsm-menu[hidden], .wsm-menu [hidden] { display: none; }
  .wsm-head { display: block; margin: 0; padding: 8px 10px 4px; color: var(--muted);
    font-size: 11.5px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; }
  .wsm-item { display: flex; align-items: center; gap: 10px; width: 100%; min-height: 36px;
    margin: 0; padding: 6px 10px 6px 8px; border: 0; border-radius: 6px; background: none;
    color: var(--text); font: inherit; font-size: 14px; font-weight: 400; text-align: left;
    text-decoration: none; cursor: pointer; }
  .wsm-item:hover, .wsm-item:focus-visible { background: var(--sunken); outline: none;
    box-shadow: none; text-decoration: none; }
  html[data-mode="dark"] .wsm-item:hover,
  html[data-mode="dark"] .wsm-item:focus-visible { background: var(--line); }
  .wsm-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .wsm-item[aria-checked="true"] .wsm-name { font-weight: 600; }
  .wsm-meta { margin-left: auto; padding-left: 12px; color: var(--muted); font-size: 12.5px;
    white-space: nowrap; }
  .wsm-check { flex: none; width: 15px; height: 15px; fill: none; stroke: var(--accent);
    stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; visibility: hidden; }
  .wsm-item[aria-checked="true"] .wsm-check { visibility: visible; }
  .wsm-quiet { color: var(--muted); }
  .wsm-quiet:hover, .wsm-quiet:focus-visible { color: var(--text); }
  .wsm-icon { flex: none; width: 22px; height: 15px; fill: none; stroke: currentColor;
    stroke-width: 1.7; stroke-linecap: round; stroke-linejoin: round; }
  .wsm-rule { height: 1px; margin: 6px 4px; background: var(--line); }
  .wsm-form { margin: 0; padding: 0 0 4px; border: 0; background: none; }
  .wsm-form-row { display: flex; gap: 6px; padding: 2px 6px 0; }
  .wsm-form input { flex: 1 1 auto; min-width: 0; height: 34px; min-height: 0; margin: 0;
    padding: 0 9px; border: 1px solid var(--line-strong); border-radius: 6px;
    background: var(--field); color: var(--text); font: inherit; font-size: 14px; }
  .wsm-form input::placeholder { color: var(--muted); }
  .wsm-form .wsm-create { flex: none; width: auto; height: 34px; min-height: 0; margin: 0; padding: 0 12px;
    border: 1px solid var(--btn); border-radius: 6px; background: var(--btn);
    color: var(--btn-text); font: inherit; font-size: 14px; font-weight: 600; cursor: pointer; }
  .wsm-form .wsm-create:hover { background: var(--btn-hover); border-color: var(--btn-hover); }
  .wsm-hint { margin: 6px 10px 2px; color: var(--muted); font-size: 12.5px; line-height: 1.4; }
  @media (max-width: 640px) {
    .wsm-item { min-height: var(--tap, 40px); }
    .wsm-form input { font-size: 16px; }
  }
"""


# The chevron on the workspace button and the exported file's wiki picker.
CHEVRON = ('<svg class="sw-chev" viewBox="0 0 16 16" aria-hidden="true">'
           '<path d="M5 6.2 8 3.2l3 3M5 9.8l3 3 3-3"></path></svg>')
APP_JS = r"""
(function () {
  // A workspace has one wiki (since 2026-09-28), so there is no wiki menu and no
  // "Create a wiki" step: the header names the workspace, and this loads its wiki.
  // Until then a second button beside the workspace menu ("workspace / wiki")
  // listed the workspace's wikis with New wiki and Manage wikis.
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  // ---- guided flows, shown where the graph would be ----------------------
  // There is no onboarding page. An empty wiki gets "Connect your agent", whose
  // steps end with the agent saving a first page, and gives way to the graph when
  // that page lands (Forrest, 2026-09-27: the first page is the onboarding's
  // proof, so there is no "connected" state and no check of the person's keys).
  // In a workspace that already has an agent (DEXIO_CONNECTED, decided by the
  // server at page load), an empty wiki skips the tiles and asks for the first
  // page (Forrest, 2026-09-27: prompting to connect an agent that is already
  // connected is weird). The steps run here, on the graph screen (Forrest,
  // 2026-09-27: people stay on the graph rather than going to Settings). The
  // steps for each agent are rendered by the server (GET /connect); "Get the
  // message" POSTs there to mint a key. Connecting from anywhere else (the
  // account menu, ?connect links) is Settings > Agents' panel.
  const CLIENTS = window.DEXIO_CLIENTS || [];
  const FIRST = window.DEXIO_FIRST_PAGE || "";
  const CONNECTED = !!window.DEXIO_CONNECTED;
  const ASK_NAME = !!window.DEXIO_ASK_NAME;
  const workspaces = window.DEXIO_WORKSPACES || [];
  const ws = workspaces.find(w => w.id === window.DEXIO_WORKSPACE) || workspaces[0] || {name: ""};
  const wrap = document.getElementById("wrap");
  let watch = null;                    // polls an empty wiki for its first page
  window.DEXIO_PROJECT = "main";      // what the graph's own requests send

  function stopWatch() { if (watch) { clearInterval(watch); watch = null; } }
  function closeFlow() {
    stopWatch();
    const old = document.getElementById("onboard");
    if (old) old.remove();
    wrap.classList.remove("onboarding");
  }
  function flow(title) {
    closeFlow();
    const box = el("section", "ob");
    box.id = "onboard";
    box.setAttribute("aria-labelledby", "ob-title");
    wrap.classList.add("onboarding");
    const h = el("h2", null, title);
    h.id = "ob-title";
    box.append(h);
    wrap.appendChild(box);
    return box;
  }

  // An owner names a workspace no one has named yet, before anything else
  // (Forrest, 2026-09-28). With one wiki per workspace its name is the only one
  // the wiki has: the header, the menu, invites and the download show it. The
  // field starts with the name sign-up gave it, so keeping that is one click;
  // either way the server counts it named and never asks again. Resolves once named.
  function nameFlow() {
    return new Promise(done => {
      const box = flow("Name your workspace");
      box.append(el("p", "ob-lead", "Use your team's, company's or project's name. The " +
        "workspace holds your wiki, the people you invite and your agents. You can change " +
        "it later in Settings."));
      const f = el("form", "ob-form");
      f.noValidate = true;
      const lab = el("label", "ob-label", "Workspace name");
      lab.htmlFor = "ob-name";
      const row = el("div", "ob-row");
      const input = el("input");
      input.id = "ob-name"; input.name = "name"; input.maxLength = 80;
      input.autocomplete = "organization"; input.value = ws.name || "";
      const go = el("button", "cta", "Continue");
      go.type = "submit";
      row.append(input, go);
      const msg = el("p", "ob-hint ob-error");
      msg.id = "ob-name-msg"; msg.hidden = true;
      msg.setAttribute("role", "alert");
      input.setAttribute("aria-describedby", msg.id);
      f.append(lab, row, msg);
      box.append(f);
      const fail = text => {
        msg.textContent = text; msg.hidden = false;
        input.setAttribute("aria-invalid", "true"); input.focus();
        go.disabled = false;
      };
      f.onsubmit = async e => {
        e.preventDefault();
        const name = input.value.trim();
        if (!name) return fail("Give the workspace a name.");
        go.disabled = true;
        let r;
        try {
          r = await fetch(window.DEXIO_API + "/workspace/name?w=" + encodeURIComponent(ws.id), {
            method: "POST", headers: {"Content-Type": "application/json"},
            body: JSON.stringify({name})});
        } catch (err) { r = null; }
        if (!r || !r.ok) return fail(await failure(r));
        renamed((await r.json()).name);
        closeFlow();
        done();
      };
      input.focus();
      input.select();
    });
  }

  // The new name everywhere the page already shows the old one: the header's
  // button and its tile, the menu's row and settings link, the account menu.
  function renamed(name) {
    const old = ws.name;
    ws.name = name;
    const initial = (name.trim()[0] || "?").toUpperCase();
    const btn = document.getElementById("ws-switch-button");
    if (btn) {
      const label = btn.querySelector(".wsm-label"), tile = btn.querySelector(".wsm-tile");
      if (label) label.textContent = name;
      if (tile) tile.textContent = initial;
      btn.setAttribute("aria-label", "Workspace " + name + ". Switch or create a workspace");
    }
    const row = document.querySelector('#ws-switch [data-key="ws:' + ws.id + '"]');
    if (row) {
      row.querySelector(".wsm-name").textContent = name;
      row.querySelector(".wsm-tile").textContent = initial;
    }
    const set = document.querySelector('#ws-switch [data-key="settings"] .wsm-name');
    if (set) set.textContent = "Settings for " + name;
    document.querySelectorAll("#acct-menu .sw-head").forEach(h => {
      if (h.textContent === old) h.textContent = name;
    });
  }

  // An empty wiki in a workspace with no agent yet: pick your agent and its
  // steps open in place, ending with the message that has it save the first
  // page. The watch brings up the graph at the first page, silently: no pulsing
  // line (Forrest, 2026-09-27, as on Agents).
  function connectFlow() {
    const box = flow("Connect your agent");
    box.append(el("p", "ob-lead", "Your wiki is empty. Which agent do you use? Once it is " +
      "connected, it saves notes here and reads them back later."));
    // the stepper (ais.py): Choose your agent, Connect, First page, one at a time
    const steps = el("div", "ai-flow");
    steps.dataset.at = "1";
    steps.innerHTML = window.DEXIO_AI_STEPS || "";
    const choose = el("div", "ai-pane");
    choose.dataset.pane = "1";
    const tiles = el("div", "ob-choices ai-grid");
    tiles.setAttribute("role", "group");
    tiles.setAttribute("aria-label", "Your agent");
    const panes = el("div", "ob-steps");
    panes.setAttribute("aria-live", "polite");
    for (const c of CLIENTS) {
      const b = el("button", "ob-choice ai-tile");
      b.type = "button"; b.dataset.client = c.id;
      b.setAttribute("aria-pressed", "false");
      b.innerHTML = c.tile;              // mark, name and grey line, from ais.tile
      b.onclick = () => pickClient(c, tiles, steps, panes);
      tiles.append(b);
    }
    choose.append(tiles);
    steps.append(choose, panes);
    box.append(steps);
    watchFor();
  }

  // An empty wiki in a workspace that already has an agent: nothing to connect,
  // so ask for the first page. "Connect another agent" opens the tiles in place,
  // for a teammate whose own agent is not connected yet.
  function readyFlow() {
    const box = flow("Your wiki is empty");
    box.append(el("p", "ob-lead", "Ask your agent to save its first page. The graph " +
      "appears here when it lands."));
    const pre = el("pre"), code = el("code", "agent-prompt", FIRST);
    code.id = "first-page";
    pre.append(code);
    const copy = el("button", "copy", "Copy");
    copy.type = "button"; copy.dataset.copy = "first-page";
    const more = el("p", "muted ob-more");
    const another = el("a", null, "Connect another agent");
    another.href = "#";
    another.onclick = e => { e.preventDefault(); connectFlow(); };
    more.append(another);
    box.append(pre, copy, more);
    watchFor();
  }

  async function pickClient(c, tiles, steps, panes) {
    for (const b of tiles.children) b.setAttribute("aria-pressed", String(b.dataset.client === c.id));
    window.dexioSteps.picked(steps, c.id, c.name);
    const wait = el("div", "ai-pane");
    wait.dataset.pane = "2";
    wait.append(el("p", "ob-hint", "Loading…"));
    panes.replaceChildren(wait);
    window.dexioSteps.go(steps, 2);
    let r;
    try {
      r = await fetch(window.DEXIO_API + "/connect?view=graph&client=" + encodeURIComponent(c.id) +
                      "&w=" + encodeURIComponent(ws.id));
    } catch (err) { r = null; }
    if (!wait.isConnected) return;       // another pick arrived first
    if (!r || !r.ok) return wait.replaceChildren(el("p", "ob-error", await failure(r)));
    panes.innerHTML = (await r.json()).html;
  }

  async function failure(r, fallback) {
    if (!r) return "Could not reach Dexio. Try again.";
    let detail = fallback || "something went wrong";
    try { detail = (await r.json()).error || detail; } catch (err) {}
    return detail[0].toUpperCase() + detail.slice(1) + (detail.endsWith(".") ? "" : ".");
  }

  // Copy buttons and "Get the message" live in server-rendered steps, so they
  // are handled here rather than by scripts inside them.
  wrap.addEventListener("click", async e => {
    const box = document.getElementById("onboard");
    if (!box || !box.contains(e.target)) return;
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      const src = box.querySelector("#" + CSS.escape(copy.dataset.copy));
      if (!src || !navigator.clipboard) return;
      const was = copy.textContent;
      navigator.clipboard.writeText(src.textContent).then(() => {
        copy.textContent = "Copied";
        setTimeout(() => { copy.textContent = was; }, 2000);
      });
      return;
    }
    const mint = e.target.closest("[data-mint]");
    if (!mint) return;
    const body = mint.closest(".ob-steps");
    mint.disabled = true;
    let r;
    try {
      r = await fetch(window.DEXIO_API + "/connect?w=" + encodeURIComponent(ws.id), {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({client: mint.dataset.mint, view: "graph"})});
    } catch (err) { r = null; }
    if (!r || !r.ok) {
      mint.disabled = false;
      const old = body.querySelector(".ob-error");
      if (old) old.remove();
      mint.insertAdjacentElement("afterend", el("span", "ob-error", " " + await failure(r)));
      return;
    }
    body.innerHTML = (await r.json()).html;
    const first = body.querySelector("[data-copy]");
    if (first) first.focus();
  });

  // An empty wiki is watched while its flow shows: the first page the agent
  // saves replaces the flow with the graph. Visible tabs only; an hour at most.
  function watchFor() {
    stopWatch();
    const until = Date.now() + 3600e3;
    watch = setInterval(async () => {
      if (Date.now() > until) return stopWatch();
      if (document.hidden) return;
      let r;
      try { r = await fetch(window.DEXIO_API + "/graph?w=" + encodeURIComponent(ws.id)); }
      catch (err) { return; }
      // /graph answers 404 for a wiki with no pages yet: still empty, go on.
      if ((!r.ok && r.status !== 404) || !watch) return;
      const data = r.ok ? await r.json() : {};
      if (data.nodes && data.nodes.length) {
        closeFlow();
        window.dexio.load(data);
      }
    }, 4000);
  }

  // ---- addresses ---------------------------------------------------------
  // Every page has an address (Forrest, 2026-09-28: deep links): /w/<workspace>,
  // then /<page path>, then #<section>. graph.js puts a page's address in the
  // bar and the history as it opens; this reads one on load and on Back and
  // Forward. Addresses from before a workspace had one wiki (the same day),
  // /w/<workspace>/<wiki>/<page>, and /?project=<wiki> reach the same pages:
  // the server rewrites the first, and the second just opens the wiki.
  function wikiAddress() {
    return "/w/" + encodeURIComponent(window.DEXIO_WORKSPACE);
  }
  // What the address asks for: {page, sec, folder}, any of them empty. folder
  // is the one the graph is narrowed to (graph.js, "one folder"), ?folder=.
  function asked() {
    const decode = s => { try { return decodeURIComponent(s); } catch (err) { return s; } };
    const parts = location.pathname.split("/").filter(Boolean);
    const page = parts[0] === "w" ? parts.slice(2).map(decode).join("/") : "";
    const folder = new URLSearchParams(location.search).get("folder") || "";
    return {page, sec: decode(location.hash.slice(1)), folder};
  }
  let toastTimer = null;
  function toast(text) {
    let t = document.getElementById("toast");
    if (!t) {
      t = el("div");
      t.id = "toast"; t.setAttribute("role", "status");
      wrap.appendChild(t);
    }
    t.textContent = text;
    t.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { t.hidden = true; }, 6000);
  }
  // Open the page an address names, or say it is gone. top: where it was
  // being read, put back on Back and Forward (graph.js, "going back").
  async function openAsked(page, sec, top) {
    const ok = await window.dexio.openPath(page, sec, "none", top || 0);
    if (ok) return;
    history.replaceState(null, "", wikiAddress() + location.search);
    toast("There is no page " + page + ". It may have been deleted.");
  }
  window.addEventListener("popstate", (e) => {
    const want = asked();
    window.dexio.setFocus(want.folder, "none");
    openAsked(want.page, want.sec, e.state && e.state.dexio ? e.state.top : 0);
  });

  // The graph is fetched at once. An empty wiki arrives from the server already
  // in its onboarding state, and the flow and the folders stay as they are until
  // the graph answers: clearing that first showed the folders, the layout picker
  // and the footer for two round trips on every reload (Forrest 2026-09-27).
  (async function () {
    const want = asked();
    if (location.pathname.split("/")[1] !== "w") {
      history.replaceState(null, "", wikiAddress() + location.hash);   // "/" and /?project=
    }
    // the graph is asked for at once, while a new workspace is being named
    const graph = fetch(window.DEXIO_API + "/graph?w=" + encodeURIComponent(ws.id));
    if (ASK_NAME) await nameFlow();
    const r = await graph;
    const data = r.ok ? await r.json() : null;
    const empty = !(data && data.nodes && data.nodes.length);
    if (empty) wrap.classList.add("onboarding");
    else closeFlow();
    window.dexio.load(data || {nodes: [], links: [], stats: {}}, {focus: want.folder});
    // Someone viewing what a workspace shares (shares.py) is never onboarded:
    // with nothing to show, the share was removed or its pages went.
    if (empty && window.DEXIO_GUEST) {
      const box = flow("Nothing to show");
      box.append(el("p", "ob-lead", "What was shared here has been removed, or its pages " +
        "have been deleted. Ask the person who shared it."));
    } else if (empty) (CONNECTED && FIRST ? readyFlow : connectFlow)();
    else if (want.page) openAsked(want.page, want.sec);
  })();
})();
"""


# The exported file's wiki picker (offline_wikis): the app's switcher button and
# menu, same ids and classes so the same styles, listing the wikis the file
# holds with their page counts. No New wiki or Manage wikis: there is no server
# behind it. Picking a wiki closes the open page and loads its graph in place.
EXPORT_SWITCHER_JS = r"""
(function () {
  const wikis = window.DEXIO_WIKIS || [];
  const root = document.getElementById("switcher");
  const btn = document.getElementById("switcher-button");
  const menu = document.getElementById("switcher-menu");
  const wikiLabel = btn.querySelector(".sw-wiki");
  const CHECK = '<svg class="sw-check" viewBox="0 0 16 16" aria-hidden="true">' +
                '<path d="M3.5 8.4 6.6 11.4 12.5 4.8"></path></svg>';
  let current = null;

  const plural = (n, word) => n.toLocaleString() + " " + word + (n === 1 ? "" : "s");
  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }
  function render() {
    const g = el("div", "sw-group");
    g.setAttribute("role", "group");
    const h = el("div", "sw-head", "Wikis");
    h.id = "sw-head-wikis";
    g.setAttribute("aria-labelledby", h.id);
    g.append(h);
    for (const w of wikis) {
      const b = el("button", "sw-item");
      b.type = "button"; b.tabIndex = -1;
      b.setAttribute("role", "menuitemradio");
      b.setAttribute("aria-checked", w.name === current ? "true" : "false");
      b.append(el("span", "sw-name", w.name), el("span", "sw-meta", plural(w.pages, "page")));
      b.insertAdjacentHTML("beforeend", CHECK);
      b.onclick = () => { close(true); show(w.name); };
      g.append(b);
    }
    menu.replaceChildren(g);
  }
  const items = () => [...menu.querySelectorAll(".sw-item")];
  // Opens under the button, shifted left if it would run past the right edge.
  function place() {
    menu.style.left = "0px";
    const r = menu.getBoundingClientRect(), over = r.right - (window.innerWidth - 12);
    if (over > 0) menu.style.left = -Math.min(over, r.left - 12) + "px";
  }
  function open(last) {
    render();
    menu.hidden = false;
    btn.setAttribute("aria-expanded", "true");
    place();
    const list = items();
    const target = last ? list[list.length - 1]
                        : list.find(i => i.getAttribute("aria-checked") === "true") || list[0];
    if (target) target.focus();
  }
  function close(refocus) {
    if (menu.hidden) return;
    menu.hidden = true;
    btn.setAttribute("aria-expanded", "false");
    if (refocus) btn.focus();
  }
  function show(name) {
    const w = wikis.find(x => x.name === name);
    if (!w || name === current) return;
    if (current !== null) window.dexio.select(null);   // the open page was the other wiki's
    current = name;
    wikiLabel.textContent = name;
    btn.setAttribute("aria-label", "Wiki " + name + ". Switch wiki");
    document.title = name;
    window.dexio.load(w.graph);
  }
  btn.addEventListener("click", () => (menu.hidden ? open(false) : close(true)));
  btn.addEventListener("keydown", e => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") { e.preventDefault(); open(e.key === "ArrowUp"); }
  });
  menu.addEventListener("keydown", e => {
    const list = items(), i = list.indexOf(document.activeElement);
    const go = j => { e.preventDefault(); if (list.length) list[(j + list.length) % list.length].focus(); };
    if (e.key === "ArrowDown") go(i + 1);
    else if (e.key === "ArrowUp") go(i - 1);
    else if (e.key === "Home") go(0);
    else if (e.key === "End") go(list.length - 1);
    else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
    else if (e.key === "Tab") close(false);
  });
  document.addEventListener("pointerdown", e => { if (!root.contains(e.target)) close(false); });
  show(window.DEXIO_WIKI || (wikis[0] && wikis[0].name));
})();
"""


def server_page(api_base: str, title: str = "Dexio",
                workspaces: list[dict] | None = None, current: int | None = None,
                clients: list[dict] | None = None, account: dict | None = None,
                empty: bool = False, first_page: str = "", connected: bool = False,
                ask_name: bool = False, guest: dict | None = None,
                shared: list[dict] | None = None, can_share: bool = False,
                seo: dict | None = None, ssr: str = "") -> str:
    """The graph view of the current workspace's wiki.
    guest: set when the reader is not a member and sees what the workspace shares
    (shares.py): {role, signed_in, home, name, handle, next}. The header then
    names that workspace with View only, and the page has no history, no share
    buttons and no onboarding. `workspaces` and `current` are still the reader's
    own, for the account menu.
    shared: what other workspaces share with this person, for the workspace menu.
    can_share: the reader is a member, so the Share buttons show.
    seo: for a page anyone may read, {url, title, description, site}: its canonical
    address, description and link previews, for search engines and chat apps.
    ssr: that page's text and links as HTML (server/ssr.py), shown only to readers
    without JavaScript; the app hides it at once.
    clients: the AIs an empty wiki's connect flow offers, [{id, name, tile}], tile
    being the inside of its button (ais.tile).
    first_page: the message that has an agent save the wiki's first page (pages.TRY_IT).
    connected: the workspace already has an agent (a used key or a live sign-in),
    so an empty wiki asks for its first page instead of offering to connect one.
    account: the signed-in person, {name, email, first, last}, for the account menu.
    empty: the wiki has no pages. The page then starts in its onboarding state
    (no folders, layout picker or footer), so they do not show until the script
    puts up the flow.
    ask_name: the person is an owner of a workspace no one has named yet, so the
    page opens on Name your workspace (in the onboarding state too)."""
    shell, js = _assets()
    workspaces = workspaces or []
    ws = next((w for w in workspaces if w["id"] == current), workspaces[0] if workspaces else None)
    ws_name = ws["name"] if ws else ""
    brand = f'<a class="brand" href="/" title="Dexio">{logo(BRAND_HEIGHT)}</a>'
    acct = account or {}
    esc = html.escape
    if guest and not account:
        # Someone with a public link and no account: the logo goes to the site.
        brand = f'<a class="brand" href="https://dexio.wiki" title="Dexio">{logo(BRAND_HEIGHT)}</a>'
    if guest:
        # What the reader opened: the sharing workspace, by name, marked View only.
        tile = ws_tile({"id": sum(map(ord, guest["handle"])), "name": guest["name"]})
        picker = (f'<div id="crumbs"><span class="guest-ws" title="{esc(guest["name"])}">{tile}'
                  f'<span class="wsm-label">{esc(guest["name"])}</span></span>'
                  f'<span class="guest-tag">View only</span></div>')
        listed = [{"id": guest["handle"], "name": guest["name"], "plan": ""}]
        current_handle = guest["handle"]
        if account:
            menu_html = account_menu(acct.get("name") or acct.get("email", ""),
                                     acct.get("email", ""), acct.get("first", ""),
                                     acct.get("last", ""), ws_name,
                                     bool(ws and ws.get("role") == "owner"))
        else:
            nxt = quote(guest.get("next") or "/", safe="/")
            # Something here is listed for copying (shares.set_listed): Make a copy
            # takes the place of Try Dexio free, since it signs people up too.
            menu_html = (f'<a class="hbtn" href="/login?next={esc(nxt)}">Sign in</a>'
                         + (f'<a class="hbtn hbtn-primary" href="{esc(guest["copy"])}">'
                            f'Make a copy</a>' if guest.get("copy") else
                            '<a class="hbtn hbtn-primary" href="/signup">Try Dexio free</a>'))
        if account and guest.get("copy"):
            menu_html = (f'<a class="hbtn hbtn-primary" href="{esc(guest["copy"])}">'
                         f'Make a copy</a>' + menu_html)
    else:
        wsm = workspace_menu(workspaces, ws["id"] if ws else None, lambda w: f"/?w={w['handle']}",
                             menu_id="ws-switch", settings=True, shared=shared)
        picker = f'<div id="crumbs">{wsm}</div>'
        # The browser knows a workspace by its handle only (db.new_handle): the script
        # puts it in addresses and every request's ?w=.
        listed = [{"id": w["handle"], "name": w["name"], "plan": w.get("plan", "")}
                  for w in workspaces]
        current_handle = ws["handle"] if ws else None
        menu_html = account_menu(acct.get("name") or acct.get("email", ""), acct.get("email", ""),
                                 acct.get("first", ""), acct.get("last", ""), ws_name,
                                 bool(ws and ws.get("role") == "owner"))
        if can_share:
            # Publishing is a level of Share's Visibility again (Forrest,
            # 2026-10-01), so the header has Share alone.
            menu_html = (f'<button id="share-wiki" class="hbtn" type="button"'
                         f' title="Share the wiki">{SHARE_ICON}<span>Share</span></button>'
                         + menu_html)
    bootstrap = (f'window.DEXIO_API = {json.dumps(api_base)};'
                 f'window.DEXIO_PROJECT = "main";'
                 f'window.DEXIO_WORKSPACES = {_safe_json(listed)};'
                 f'window.DEXIO_WORKSPACE = {json.dumps(current_handle)};'
                 f'window.DEXIO_CLIENTS = {_safe_json([] if guest else clients or [])};'
                 f'window.DEXIO_FIRST_PAGE = {_safe_json("" if guest else first_page or "")};'
                 f'window.DEXIO_CONNECTED = {"true" if connected or guest else "false"};'
                 f'window.DEXIO_ASK_NAME = {"true" if ask_name and not guest else "false"};'
                 f'window.DEXIO_GUEST = {_safe_json(guest or None)};'
                 f'window.DEXIO_SHARE = {"true" if can_share and not guest else "false"};'
                 f'window.DEXIO_AI_STEPS = {_safe_json(ais.steps_head())};')
    init = ais.STEPS_JS + APP_JS + WS_MENU_JS + ACCOUNT_JS
    if can_share and not guest:
        init += (STATIC / "share.js").read_text(encoding="utf-8")
    shell = shell.replace("</style>", WS_MENU_CSS + ais.CSS + "</style>", 1)
    if (empty or ask_name) and not guest:
        shell = shell.replace('<div id="wrap">', '<div id="wrap" class="onboarding">', 1)
    if guest:
        shell = shell.replace("<body>", '<body class="guest">', 1)
    # One pass, so nothing a page supplies (its title, description or text) is
    # itself scanned for a placeholder: a page titled "__INIT__" once would have
    # pulled the app's script into the <title>.
    fills = {"TITLE": html.escape(title), "BRAND": brand, "PROJECT_PICKER": picker,
             "ACCOUNT": menu_html, "BOOTSTRAP": bootstrap, "GRAPH_JS": js, "INIT": init}
    out = re.sub(r"__(TITLE|BRAND|PROJECT_PICKER|ACCOUNT|BOOTSTRAP|GRAPH_JS|INIT)__",
                 lambda m: fills[m.group(1)], shell)
    if seo:
        e = lambda s: esc(str(s or ""), quote=True)  # noqa: E731
        meta = (f'\n<meta name="description" content="{e(seo["description"])}">'
                f'\n<link rel="canonical" href="{e(seo["url"])}">'
                f'\n<meta property="og:type" content="article">'
                f'\n<meta property="og:title" content="{e(seo["title"])}">'
                f'\n<meta property="og:description" content="{e(seo["description"])}">'
                f'\n<meta property="og:url" content="{e(seo["url"])}">'
                f'\n<meta property="og:site_name" content="{e(seo.get("site"))}">'
                f'\n<meta name="twitter:card" content="summary">')
        out = out.replace("</title>", "</title>" + meta, 1)
    if ssr:
        # Between the header and the app, so a reader without JavaScript gets the
        # header's Sign in and Make a copy above the text.
        out = out.replace("</header>\n", "</header>\n" + ssr + "\n", 1)
    return out
