"""Colour themes and light/dark mode, shared by the graph view and every server page.

Two independent choices, the way Slack does it: a mode (System, Light or Dark) and a
theme. The mode sets the neutrals (page, panels, text, lines). The theme sets the
accent (links, buttons, focus rings, the node in the wordmark, lit edges) and the
colour of the app's header bar. The default theme, Dexio, leaves the header plain,
so it looks exactly as the app did before themes existed.

The choice is an account setting (`users.theme`, set in Settings) carried to the
browser in a readable cookie, `dexio_theme=<theme>.<mode>`, so the script in
<head> can apply it before first paint without a round trip.
"""
from __future__ import annotations

import json

COOKIE = "dexio_theme"
MODES = ("system", "light", "dark")
DEFAULT_THEME = "dexio"
DEFAULT = f"{DEFAULT_THEME}.system"

NEUTRALS = {
    "light": {
        "bg": "#ffffff", "panel": "#ffffff", "sunken": "#f6f8fa", "field": "#ffffff",
        "line": "#e3e7ea", "line-strong": "#d3d8dd",
        "text": "#1b1f23", "muted": "#5b646c", "faint": "#98a1a8",
        "edge": "#d3d8dd", "node-dim": "#aab1b8", "node-ring": "#111111",
        "shadow": "0 8px 28px rgba(16,22,26,.16)",
        "bad-bg": "rgba(192,57,43,.08)", "bad-line": "rgba(192,57,43,.35)",
        "bad-text": "#a5342a",
        "good-bg": "rgba(26,127,55,.08)", "good-line": "rgba(26,127,55,.35)",
        "good-text": "#1a7f37",
        "warn-bg": "rgba(212,167,44,.12)", "warn-line": "rgba(191,135,0,.45)",
        "warn-text": "#8a6100",
        "hit": "#fde68a",   # the search's words in an open page, find-in-page yellow
    },
    "dark": {
        "bg": "#0d1117", "panel": "#161b22", "sunken": "#0d1117", "field": "#0d1117",
        "line": "#30363d", "line-strong": "#3d444d",
        "text": "#e6edf3", "muted": "#9198a1", "faint": "#6e7681",
        "edge": "#343b44", "node-dim": "#4d555e", "node-ring": "#e6edf3",
        "shadow": "0 8px 28px rgba(0,0,0,.6)",
        "bad-bg": "rgba(248,81,73,.1)", "bad-line": "rgba(248,81,73,.4)",
        "bad-text": "#ffa198",
        "good-bg": "rgba(63,185,80,.12)", "good-line": "rgba(63,185,80,.4)",
        "good-text": "#56d364",
        "warn-bg": "rgba(210,153,34,.14)", "warn-line": "rgba(210,153,34,.45)",
        "warn-text": "#e3b341",
        "hit": "#6b5a14",
    },
}


# ---- colour arithmetic ---------------------------------------------------
def _rgb(hex_: str) -> tuple[int, int, int]:
    h = hex_.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _hex(rgb) -> str:
    return "#" + "".join(f"{max(0, min(255, round(c))):02x}" for c in rgb)


def mix(a: str, b: str, t: float) -> str:
    """`t` of the way from colour a to colour b."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(x + (y - x) * t for x, y in zip(ra, rb))


def luminance(hex_: str) -> float:
    def ch(c):
        c /= 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (ch(c) for c in _rgb(hex_))
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(a: str, b: str) -> float:
    """WCAG 2 contrast ratio."""
    la, lb = sorted((luminance(a), luminance(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


# Outline buttons (secondary and quiet): the border at rest and on hover, and the faint
# fill on hover and press, as steps from the panel towards the text colour, so they read
# the same in both modes and every theme. Danger buttons turn solid red on hover.
# Forrest, 2026-09-28, option A of four.
for _v in NEUTRALS.values():
    _v.update({"btn-line": mix(_v["panel"], _v["text"], .20),
               "btn-line-hover": mix(_v["panel"], _v["text"], .34),
               "btn-fill-hover": mix(_v["panel"], _v["text"], .05),
               "btn-fill-press": mix(_v["panel"], _v["text"], .10),
               "bad-solid": "#cf222e", "bad-press": mix("#cf222e", "#000000", .2),
               "bad-solid-text": "#ffffff"})


# ---- themes --------------------------------------------------------------
WHITE, INK = "#ffffff", "#1b1f23"


def _colored(chrome: str, accent: str, node: str, btn: str | None = None,
             ink: bool = False) -> dict:
    """A theme's values for one mode, derived from a few seed colours: the header
    (`chrome`), the accent, and the wordmark's node as drawn on the header."""
    on = INK if ink else WHITE
    btn = btn or accent
    return {
        "accent": accent, "link": accent, "edge-lit": accent,
        "btn": btn, "btn-hover": mix(btn, "#000000", .15), "btn-press": mix(btn, "#000000", .28),
        "btn-text": WHITE,
        "chrome": chrome, "chrome-text": on, "chrome-muted": mix(chrome, on, .78),
        "chrome-accent": node, "chrome-line": mix(chrome, "#000000", .12),
        "chrome-field": mix(chrome, on, .12), "chrome-field-line": mix(chrome, on, .32),
    }


THEMES: dict[str, dict] = {
    "dexio": {
        "name": "Dexio",
        "light": {
            "accent": "#0077c8", "link": "#0077c8", "edge-lit": "#3b78c3",
            "btn": "#0077c8", "btn-hover": "#005f9e", "btn-press": "#005690", "btn-text": WHITE,
            "chrome": "#ffffff", "chrome-text": INK, "chrome-muted": "#5b646c",
            "chrome-accent": "#0077c8", "chrome-line": "#e3e7ea",
            "chrome-field": "#ffffff", "chrome-field-line": "#d3d8dd",
        },
        "dark": {
            "accent": "#38bdf8", "link": "#38bdf8", "edge-lit": "#38bdf8",
            # hover darkens rather than lightens: white on #1389d6 was 3.8:1
            "btn": "#0077c8", "btn-hover": "#005f9e", "btn-press": "#005690", "btn-text": WHITE,
            "chrome": "#0d1117", "chrome-text": "#e6edf3", "chrome-muted": "#9198a1",
            "chrome-accent": "#38bdf8", "chrome-line": "#30363d",
            "chrome-field": "#161b22", "chrome-field-line": "#3d444d",
        },
    },
    "aubergine": {
        "name": "Aubergine",
        "light": _colored("#3f0e40", "#7c3085", "#f0a8f2"),
        "dark": _colored("#2e0b30", "#e0a3e6", "#f0a8f2", btn="#7c3085"),
    },
    "jade": {
        "name": "Jade",
        "light": _colored("#0b4d3b", "#0a7a5a", "#6ee7b7"),
        "dark": _colored("#0a3529", "#4fd6a7", "#6ee7b7", btn="#0a7a5a"),
    },
    "lagoon": {
        "name": "Lagoon",
        "light": _colored("#0c5460", "#0a7285", "#7ee3f0"),
        "dark": _colored("#0a3a43", "#5cd4e6", "#7ee3f0", btn="#0a7285"),
    },
    "indigo": {
        "name": "Indigo",
        "light": _colored("#312e81", "#4f46e5", "#a5b4fc"),
        "dark": _colored("#1e1b4b", "#a5b4fc", "#a5b4fc", btn="#4f46e5"),
    },
    "rose": {
        "name": "Rose",
        "light": _colored("#9f1239", "#be123c", "#fecdd3"),
        "dark": _colored("#650c26", "#fb7a93", "#fecdd3", btn="#be123c"),
    },
    "clementine": {
        "name": "Clementine",
        # white on a clementine bright enough to read as orange is 5.2:1 and its
        # muted text 3.7:1, so the light header takes dark text instead
        "light": _colored("#fb8b3c", "#b53d0b", "#7a2a0a", ink=True),
        "dark": _colored("#7a2a0a", "#fb9a57", "#ffc08f", btn="#b53d0b"),
    },
    "sunflower": {
        "name": "Sunflower",
        "light": _colored("#f4c542", "#8a5700", "#8a5700", ink=True),
        "dark": _colored("#d9a92a", "#f4c542", "#6b4400", btn="#8a5700", ink=True),
    },
    "graphite": {
        "name": "Graphite",
        "light": _colored("#2d333b", "#4a5561", "#c5ced8"),
        "dark": _colored("#1c2128", "#c0cad4", "#c5ced8", btn="#4a5561"),
    },
}


def parse(pref: str | None) -> tuple[str, str]:
    """(theme, mode) from a stored or cookie value; anything unknown falls back."""
    theme, _, mode = (pref or "").partition(".")
    if theme not in THEMES:
        theme = DEFAULT_THEME
    if mode not in MODES:
        mode = "system"
    return theme, mode


def valid(theme: str, mode: str) -> bool:
    return theme in THEMES and mode in MODES


# ---- CSS and the pre-paint script ------------------------------------------
def _block(selector: str, values: dict) -> str:
    body = " ".join(f"--{k}:{v};" for k, v in values.items())
    return f"{selector} {{ {body} }}"


def css() -> str:
    """Custom properties for every mode and theme. Pages style themselves only
    through these, so a new theme needs nothing but an entry in THEMES."""
    out = [_block(":root", {**NEUTRALS["light"], **THEMES[DEFAULT_THEME]["light"]}),
           ":root { color-scheme: light; }",
           _block('html[data-mode="dark"]', {**NEUTRALS["dark"],
                                             **THEMES[DEFAULT_THEME]["dark"]}),
           'html[data-mode="dark"] { color-scheme: dark; }']
    for tid, t in THEMES.items():
        if tid == DEFAULT_THEME:
            continue
        for mode in ("light", "dark"):
            out.append(_block(f'html[data-theme="{tid}"][data-mode="{mode}"]', t[mode]))
    # A theme change crossfades the whole page (see head_script); this sets its pace.
    out.append("::view-transition-old(root), ::view-transition-new(root) "
               "{ animation-duration: .3s; animation-timing-function: ease; }")
    return "\n  ".join(out)


def head_script(pin: tuple[str, str] | None = None) -> str:
    """Applied before first paint: without it the default theme flashes for a
    frame on every load. Reads the cookie; before any choice was saved, falls back
    to the old light/dark toggle's localStorage key, then to System.

    pin, a (theme, mode) pair, fixes the theme for a page that must not follow
    the visitor's choice or the operating system: the graph embedded on the
    light-only marketing site is exported with ("dexio", "light").

    A change after load (picking a theme, or the OS switching under System)
    crossfades through the View Transition API: the browser snapshots the page,
    applies the theme, and fades between the two. The snapshot includes the
    graph canvas, which a CSS colour transition could not animate. Browsers
    without the API, and anyone who asks for reduced motion, switch instantly."""
    chrome = {tid: [t["light"]["chrome"], t["dark"]["chrome"]] for tid, t in THEMES.items()}
    if pin is not None and (pin[0] not in THEMES or pin[1] not in ("light", "dark")):
        raise ValueError(f"unknown theme or mode: {pin!r}")
    script = """<script>
  (function () {
    var d = document.documentElement, C = %s, dark = null, still = null;
    try { dark = matchMedia("(prefers-color-scheme: dark)"); } catch (e) {}
    try { still = matchMedia("(prefers-reduced-motion: reduce)"); } catch (e) {}
    function pref() {
      __PIN__
      var m = /(?:^|;\\s*)%s=([a-z]+)\\.(system|light|dark)/.exec(document.cookie);
      if (m && C[m[1]]) return [m[1], m[2]];
      try {
        var old = localStorage.getItem("dexio-theme");
        if (old === "light" || old === "dark") return ["%s", old];
      } catch (e) {}
      return ["%s", "system"];
    }
    function apply() {
      var p = pref(), mode = p[1];
      if (mode === "system") mode = dark && dark.matches ? "dark" : "light";
      d.dataset.theme = p[0];
      d.dataset.mode = mode;
      var meta = document.querySelector('meta[name="theme-color"]');
      if (meta) meta.setAttribute("content", C[p[0]][mode === "dark" ? 1 : 0]);
      return p;
    }
    apply();
    function fade(change) {
      if (!document.startViewTransition || (still && still.matches) ||
          document.visibilityState !== "visible") return change();
      document.startViewTransition(change);
    }
    window.dexioTheme = {
      pref: pref,
      // the graph draws on a canvas, so it is told to repaint
      apply: function () {
        fade(function () {
          var p = apply();
          window.dispatchEvent(new CustomEvent("dexio:theme", { detail: p }));
        });
        return pref();
      },
      save: function (theme, mode) {
        document.cookie = "%s=" + theme + "." + mode +
          "; path=/; max-age=31536000; samesite=lax" +
          (location.protocol === "https:" ? "; secure" : "");
        return this.apply();
      }
    };
    if (dark && dark.addEventListener) dark.addEventListener("change", function () {
      if (pref()[1] === "system") window.dexioTheme.apply();
    });
    document.addEventListener("DOMContentLoaded", apply);
  })();
</script>""" % (json.dumps(chrome), COOKIE, DEFAULT_THEME, DEFAULT_THEME, COOKIE)
    return script.replace("__PIN__", f"return {json.dumps(list(pin))};" if pin else "")


def swatch_style(tid: str) -> str:
    """Inline custom properties for a theme's preview card in Settings, for both
    modes; the stylesheet picks the pair that matches the current mode."""
    t = THEMES[tid]
    return (f"--sw-l:{t['light']['chrome']};--sw-d:{t['dark']['chrome']};"
            f"--sa-l:{t['light']['accent']};--sa-d:{t['dark']['accent']};"
            f"--sn-l:{t['light']['chrome-accent']};--sn-d:{t['dark']['chrome-accent']};"
            f"--st-l:{t['light']['chrome-text']};--st-d:{t['dark']['chrome-text']}")
