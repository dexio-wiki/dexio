"""Signup, connect and settings pages. Server-rendered HTML on the login page's
shell (login.PAGE): no build step, and no scripts beyond the theme picker in Settings."""
from __future__ import annotations

import html
import json
import time
from pathlib import Path
from urllib.parse import quote

from .. import ais, themes
from ..render import WS_MENU_JS, workspace_menu, ws_tile
from .login import PAGE

MCP_PATH = "/mcp"
# The instructions an agent follows to sign itself in and add Dexio to its own
# config (device login, then its MCP settings). Served by dexio-www.
AGENT_GUIDE = "https://dexio.wiki/agents.md"


def agent_prompt() -> str:
    """The message without a key: the agent signs in through the device login."""
    # Read raw: a summarizing fetch tool drops the commands the agent has to run.
    return (f"Connect yourself to my Dexio wiki. Read the steps with "
            f"curl -s {AGENT_GUIDE} and follow them.")


AGENT_PROMPT = agent_prompt()


def key_prompt(token: str) -> str:
    """The message with a key in it: the agent skips the sign-in and uses the key."""
    # The key goes last, so no punctuation touches it.
    return (f"Connect yourself to my Dexio wiki. Read the steps with "
            f"curl -s {AGENT_GUIDE} and follow them, using this API key: {token}")


# Every client the connect flow knows how to connect. "token" clients take a
# static bearer header and can run commands, so they set themselves up from
# AGENT_PROMPT; "oauth" clients sign in to Dexio from their own UI.
# Muse and Grok Bot added 2026-09-28 (Forrest), after the always-on agents Hermes
# and OpenClaw, from third-party setup guides (parallel.ai for Muse, poster.ly for
# Grok Bot); neither had been connected from inside the product. Grok Bot's first
# steps (a custom connector under Settings, Plugins) were wrong: xAI's docs say
# Plugins is not a settings section but the marketplace, and Cursor support
# (forum.cursor.com/t/grokbot-custom-connectors/169965, 2026-08-30) says a custom
# MCP server is added from chat. So Grok Bot sets itself up from the message like
# the other agents (Forrest, 2026-09-28).
CLIENTS = {
    "claude-code": ("Claude Code", "in a terminal", "token"),
    "openclaw": ("OpenClaw", "open source", "token"),
    "hermes": ("Hermes", "from Nous Research", "token"),
    "muse": ("Muse", "from Meta", "token"),
    "grok-bot": ("Grok Bot", "from xAI", "token"),
    "claude": ("Claude", "claude.ai and the app", "oauth"),
    "chatgpt": ("ChatGPT", "chatgpt.com", "oauth"),
    "other": ("Something else", "Codex, Cursor and others", "token"),
}
# How the page refers to the one picked, where the tile's name would read oddly.
CALL = {"other": "your agent"}


def e(value) -> str:
    return html.escape(str(value if value is not None else ""))


def when(ts) -> str:
    if not ts:
        return "never"
    return time.strftime("%Y-%m-%d", time.gmtime(float(ts)))


def _page(title: str, body: str, foot: str = "", width: int = 640) -> str:
    return PAGE.format(title=e(title), form=body, foot=foot, width=width)


# ---- Google Analytics (Forrest, 2026-09-28) ---------------------------------
# Only on the sign-in and sign-up pages and the one-time /joined hop after an account
# is made: the pages that carry no wiki content. The graph view, page panels, Settings,
# shared pages and every other page load nothing from Google, so a wiki page's address,
# title or text never reaches it. consent.js (a copy of dexio-www's) asks devices in
# EEA, UK and Swiss time zones first, keeps the answer in the dexio_consent cookie on
# dexio.wiki (shared with the site), and hands Google only the page's origin and path,
# never ?next= or a same-site referrer.
CONSENT_JS = (Path(__file__).resolve().parent.parent / "static" / "consent.js").read_text(
    encoding="utf-8")
CONSENT_COOKIE = "dexio_consent"
COOKIE_SETTINGS = ('<a href="https://dexio.wiki/privacy#cookies" data-consent-open>'
                   'Cookie settings</a>')
CONSENT_BANNER = """<style>
  .consent { position:fixed; z-index:40; left:16px; bottom:16px; max-width:400px;
    padding:16px 18px; background:var(--panel); border:1px solid var(--line);
    border-radius:10px; box-shadow:0 8px 28px rgba(0,0,0,.14); color:var(--muted);
    font-size:14px; text-align:left; }
  .consent[hidden] { display:none; }
  /* The sign-in card is centred in a page that does not scroll: while the banner shows,
     the page keeps room under the card so the banner never covers its last line. */
  body:has(.consent:not([hidden])) { padding-bottom:200px; }
  .consent p { margin:0 0 12px; line-height:1.5; }
  .consent a { color:var(--accent); }
  .consent-actions { display:flex; gap:8px; }
  .consent-actions button { flex:1; min-height:38px; border-radius:8px; font-size:14px;
    font-weight:600; background:var(--panel); color:var(--text);
    border:1px solid var(--btn-line); cursor:pointer; }
  .consent-actions button:hover { background:var(--btn-fill-hover);
    border-color:var(--btn-line-hover); }
  .consent-actions button:active { background:var(--btn-fill-press); }
  @media (max-width:480px) { .consent { left:8px; right:8px; bottom:8px; max-width:none; } }
</style>
<div id="consent" class="consent" role="region" aria-label="Cookies" hidden>
  <p>We use Google Analytics cookies to count visits and see which pages people read. None of
    it is used to target ads. <a href="https://dexio.wiki/privacy#cookies">Privacy Policy</a></p>
  <div class="consent-actions">
    <button type="button" data-consent="denied">Decline</button>
    <button type="button" data-consent="granted">Accept</button>
  </div>
</div>"""
ANALYTICS = f"<script>{CONSENT_JS}</script>"


def joined_page(next_url: str, method: str) -> str:
    """The hop between making an account and the page it was made for: records the
    sign-up with Analytics (when the person's consent allows it) and moves on at once.
    Never more than 1.5 seconds, including when a blocker stops Google's script."""
    go = json.dumps(next_url).replace("</", "<\\/")
    body = f"""<form>
      <p class="who">Your account is ready. Taking you to Dexio&hellip;</p>
      <p class="muted"><a href="{e(next_url)}" style="color:var(--accent)">Continue</a></p>
    </form>
    <noscript><meta http-equiv="refresh" content="0;url={e(next_url)}"></noscript>
    {ANALYTICS}
    <script>
    (function () {{
      var done = false;
      function go() {{ if (!done) {{ done = true; location.replace({go}); }} }}
      setTimeout(go, 1500);
      window.addEventListener("dexio-analytics-failed", go);
      if (window.dexioConsent && window.dexioConsent.active()) {{
        gtag("event", "sign_up", {{ method: {json.dumps(method)}, event_callback: go,
                                     event_timeout: 1200 }});
      }} else go();
    }})();
    </script>"""
    return _page("Welcome", body, width=360)


def _message(error: str = "", ok: str = "") -> str:
    if error:
        return f'<div class="error">{e(error)}</div>'
    if ok:
        return f'<div class="ok" role="status">{e(ok)}</div>'
    return ""


# ---- signup -------------------------------------------------------------
def auth_page(mode: str = "login", step: str = "email", next_url: str = "/", email: str = "",
              note: str = "", error: str = "", ok: str = "", social: str = "", first: str = "",
              last: str = "", providers: str = "",
              terms_url: str = "https://dexio.wiki/terms",
              privacy_url: str = "https://dexio.wiki/privacy") -> str:
    """Sign in and sign up, email first (as Composio's sign-in does). Steps:

    - email: Google and GitHub buttons, then an email box and Continue.
    - password: the address has an account with a password: ask for it.
    - create: the address is new: a first name (last name optional), a password, the terms.
    - social: the account only signs in with Google or GitHub: those buttons, or
      set a password through "Forgot your password?".

    `mode` (login or signup) only changes the title and the footer; either page
    sends an existing address to the password step and a new one to create."""
    signup = mode == "signup"
    nxt = e(quote(next_url or "/", safe="/"))
    lead = (_message(error, ok) or (f'<p class="who">{e(note)}</p>' if note else ""))
    change = (f'<a href="/{"signup" if signup else "login"}?next={nxt}" '
              f'style="color:var(--accent);text-decoration:none">Change</a>')
    # The address shows as text; the hidden username field is what lets a password
    # manager pair the password with it.
    fixed_email = f"""<p class="who">Continuing as <b>{e(email)}</b> &middot; {change}</p>
      <input type="hidden" name="email" value="{e(email)}">
      <input type="email" name="username" autocomplete="username" value="{e(email)}" hidden>"""
    if step == "password":
        body = f"""<form method="post" action="/login">
      {lead}
      <input type="hidden" name="next" value="{e(next_url)}">
      {fixed_email}
      <label for="password">Password</label>
      <input id="password" name="password" type="password" autocomplete="current-password"
             required autofocus>
      <button type="submit">Sign in</button>
    </form>"""
        foot = f'<a href="/forgot">Forgot your password?</a>'
        title = "Sign in"
    elif step == "create":
        body = f"""<form method="post" action="/signup">
      {lead}
      <input type="hidden" name="next" value="{e(next_url)}">
      {fixed_email}
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem">
        <div><label for="first_name">First name</label>
        <input id="first_name" name="first_name" autocomplete="given-name" maxlength="80"
               value="{e(first)}" required autofocus></div>
        <div><label for="last_name">Last name <span class="opt">(optional)</span></label>
        <input id="last_name" name="last_name" autocomplete="family-name" maxlength="80"
               value="{e(last)}"></div>
      </div>
      <label for="password">Password <span class="opt">(12 characters or more)</span></label>
      <input id="password" name="password" type="password" autocomplete="new-password"
             minlength="12" required>
      <label for="confirm">Confirm password</label>
      <input id="confirm" name="confirm_password" type="password" autocomplete="new-password"
             minlength="12" required>
      <label class="check"><input type="checkbox" name="agree" value="1" required>
        <span>I agree to the <a href="{e(terms_url)}">Terms</a> and the
        <a href="{e(privacy_url)}">Privacy Policy</a>.</span></label>
      <button type="submit">Create free account</button>
    </form>"""
        foot = "Free for one person."
        title = "Create your account"
    elif step == "social":
        body = f"""<form method="get" action="/forgot">
      {lead}
      {fixed_email}
      {providers}
      <p class="muted" style="text-align:center">This account signs in with the button above.
        To use a password as well, set one:</p>
      <button type="submit">Set a password</button>
    </form>"""
        foot = ""
        title = "Sign in"
    else:
        body = f"""<form method="post" action="/login/email">
      {lead}{social}
      <input type="hidden" name="next" value="{e(next_url)}">
      <input type="hidden" name="mode" value="{"signup" if signup else "login"}">
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="username" inputmode="email"
             required autofocus value="{e(email)}">
      <button type="submit">Continue with email</button>
    </form>"""
        if signup:
            foot = ('Free for one person. Already have an account? '
                    f'<a href="/login?next={nxt}">Sign in</a>')
        else:
            foot = ('<a href="/forgot">Forgot your password?</a><br>New to Dexio? '
                    f'<a href="/signup?next={nxt}">Create a free account</a>')
        title = "Create your account" if signup else "Sign in"
    foot = (foot + "<br>" if foot else "") + COOKIE_SETTINGS
    return _page(title, body + KEEP_SECTION + CONSENT_BANNER + ANALYTICS, foot, width=360)


# A link to a section of a page (/w/<workspace>/page#section) sent to someone signed
# out comes to sign-in with the #section still in the address, since browsers
# keep a fragment across a redirect, but the server never sees it, so `next`
# lacks it. This puts it back on every `next` the page carries, in the forms and
# the Google, GitHub and other sign-in links, so the section opens afterwards.
KEEP_SECTION = """<script>
(function () {
  var h = location.hash;
  if (h.length < 2) return;
  document.querySelectorAll('input[name="next"]').forEach(function (i) {
    if (i.value.indexOf("#") < 0) i.value += h;
  });
  document.querySelectorAll('a[href*="next="]').forEach(function (a) {
    var u = new URL(a.href, location.href), n = u.searchParams.get("next");
    if (n && n.indexOf("#") < 0) { u.searchParams.set("next", n + h); a.href = u.href; }
  });
})();
</script>"""


# ---- connect your AI (an empty wiki's graph view, and Settings > Agents) ----------------------------------------------------------
def _snippet(client: str, base: str, token: str) -> str:
    url = base + MCP_PATH
    tok = token or "YOUR_API_KEY"
    if client == "hermes":
        # `hermes mcp add` asks for the API key (masked), saves it to the profile's
        # .env as MCP_DEXIO_API_KEY and writes the server into config.yaml.
        return f"""<ol class="steps">
      <li>Run this in a terminal (add <code>-p &lt;profile&gt;</code> after <code>hermes</code>
        for a named profile):
        <pre><code>hermes mcp add dexio --url {e(url)} --auth header</code></pre>
        Answer yes, paste the API key when it asks, and enable all tools.</li>
      <li>Send <code>/reload-mcp</code> in chat, or start a new session.</li>
    </ol>"""
    if client == "other":
        return f"""<p>Add a remote MCP server (Streamable HTTP) in your agent's settings:</p>
      <pre><code>URL:    {e(url)}
Header: Authorization: Bearer {e(tok)}</code></pre>"""
    if client == "muse":
        # parallel.ai/articles/meta-muse-custom-integrations (2026-09-26): Muse has no
        # add-server setting; given a URL and a header it writes an MCP SDK client.
        return f"""<p>Send Muse this instead:</p>
      <pre><code>Build a custom integration to the Dexio MCP server at {e(url)}. It is a remote
MCP server over streamable HTTP. Send the header Authorization: Bearer {e(tok)}.
Test it by calling list_pages, then save it as a reusable skill so you can use Dexio
in future conversations.</code></pre>"""
    if client == "grok-bot":
        # Cursor support, 2026-08-30: Grok Bot adds a custom MCP server when told to in
        # chat, confirms it, and the tools work from the next message.
        return f"""<p>Send Grok Bot this instead:</p>
      <pre><code>Add this MCP server: {e(url)}. It is a remote MCP server over streamable
HTTP. Send the header Authorization: Bearer {e(tok)}</code></pre>
      <p>It confirms the details with you. The Dexio tools work from your next message.</p>"""
    if client == "claude-code":
        return f"""<ol class="steps">
      <li>Run this in a terminal:
        <pre><code>claude mcp add --transport http dexio {e(url)} \\
  --header "Authorization: Bearer {e(tok)}"</code></pre></li>
      <li>Start <code>claude</code> and run <code>/mcp</code> to check that Dexio is connected.</li>
    </ol>"""
    if client == "openclaw":
        # docs.openclaw.ai: gateway/configuration, cli/mcp/transports, config-secrets-env.
        # Without transport "streamable-http" OpenClaw uses SSE, which /mcp does not serve.
        return f"""<ol class="steps">
      <li>Add the API key to <code>~/.openclaw/.env</code>:
        <pre><code>DEXIO_API_KEY={e(tok)}</code></pre></li>
      <li>Add the server to <code>~/.openclaw/openclaw.json</code>:
        <pre><code>mcp: {{
  servers: {{
    dexio: {{
      url: "{e(url)}",
      transport: "streamable-http",
      headers: {{ Authorization: "Bearer ${{DEXIO_API_KEY}}" }},
    }},
  }},
}},</code></pre></li>
      <li>The gateway picks up the change without a restart. Check it with
        <code>openclaw mcp doctor dexio --probe</code>.</li>
    </ol>"""
    return ""


def _app_steps(client: str, base: str) -> tuple[str, str]:
    """Claude and ChatGPT sign in to Dexio from their own settings: how to add it,
    then how to save a first page, as the stepper's Connect and First page steps."""
    url = base + MCP_PATH
    if client == "claude":
        # support.claude.com/en/articles/11175166 (Aug 2026): Customize > Connectors; Free
        # allows one custom connector; Team and Enterprise need an Owner.
        # claude.com/docs/connectors/building/directory-vs-custom: the install link opens
        # the Add custom connector dialog with the name and URL filled in.
        install = ("https://claude.ai/customize/connectors?modal=add-custom-connector"
                   "&connectorName=Dexio&connectorUrl=" + quote(url, safe=""))
        return (f"""<p><a class="cta" href="{e(install)}" target="_blank" rel="noopener">Add Dexio
      to Claude</a></p>
    <ol class="steps">
      <li>Claude opens with Dexio filled in. Click <b>Add</b>, then <b>Connect</b>.</li>
      <li>Claude brings you back here. Click <b>Allow access</b>.</li>
    </ol>
    <p class="muted">Claude's free plan allows one connector like this. On a Team or Enterprise
      plan, an owner adds it for everyone under Organization settings, then Connectors.</p>
    <details>
      <summary>The button did not open it?</summary>
      <p>In Claude, go to <b>Customize</b>, then <b>Connectors</b>. Click <b>+</b>, then
        <b>Add custom connector</b>, name it Dexio and paste this address:</p>
      {_copy_block("mcp-url", url, "Copy address")}
    </details>""",
                f"""<p>In a new chat, click <b>+</b>, then <b>Connectors</b>, and turn on Dexio.
      Then send this to save your first page:</p>
    {_copy_block("try-it", TRY_IT, "Copy")}""")
    # developers.openai.com/api/docs/guides/developer-mode and
    # developers.openai.com/plugins/deploy/connect-chatgpt (checked 2026-09-25).
    return (f"""<p class="muted">ChatGPT connects to Dexio only in Developer mode, on
      chatgpt.com in a browser, with a paid plan (Plus, Pro, Business, Enterprise or
      Education).</p>
    <ol class="steps">
      <li>Copy this address:
        {_copy_block("mcp-url", url, "Copy address")}</li>
      <li>In <a href="https://chatgpt.com" target="_blank" rel="noopener">ChatGPT</a>, open
        <b>Settings</b>, then <b>Security and login</b>, and turn on <b>Developer mode</b>.</li>
      <li>Open <a href="https://chatgpt.com/plugins" target="_blank"
        rel="noopener">chatgpt.com/plugins</a>, click <b>+</b>, type <b>Dexio</b> as the name,
        paste the address, and click <b>Create</b>.</li>
      <li>ChatGPT brings you here; click <b>Allow access</b>.</li>
    </ol>""",
            f"""<p>In a new chat, open the <b>+</b> menu, choose <b>Developer mode</b>, then
      Dexio, and send this to save your first page:</p>
    {_copy_block("try-it", TRY_IT, "Copy")}""")


def connect_steps(client: str, base: str, token: str = "", view: str = "agents") -> str:
    """The steps for one AI, as an HTML fragment: the stepper's Connect and First
    page steps (ais.panes), in an empty wiki's graph view (view="graph") or Settings >
    Agents' connect panel. The page's own script wires the copy buttons, and "Get
    the message" posts to /api/v1/connect, which mints the key and returns this with
    it filled in."""
    name, _sub, kind = CLIENTS[client]
    if kind == "token":
        return ais.panes(*_agent_steps(client, CALL.get(client, name), base, token, view))
    return ais.panes(*_app_steps(client, base))


# The first message: the agent saves a real first page, and the graph appearing is
# how the person sees it worked (Forrest, 2026-09-27: "have them push up a first page
# as part of the onboarding"). Not a test note, which would sit in the wiki for good
# (the same morning's "Hello" page). An agent in a project writes about it; a chat app
# with nothing to go on asks first. One message for every agent.
TRY_IT = ("Save a page in my Dexio wiki about what we're working on. "
          "If you don't know enough yet, ask me first.")


def _copy_block(block_id: str, text: str, label: str = "Copy message") -> str:
    return (f'<pre><code id="{block_id}" class="agent-prompt">{e(text)}</code></pre>\n'
            f'      <button type="button" class="copy" data-copy="{block_id}">{e(label)}</button>')


# Copy buttons on server-rendered pages (the invite link in Settings). The connect
# steps' are wired by the graph view's and Settings > Agents' own scripts.
COPY_SCRIPT = """<script>
        document.querySelectorAll("[data-copy]").forEach(function (b) {
          var label = b.textContent;
          b.addEventListener("click", function () {
            var text = document.getElementById(b.getAttribute("data-copy")).textContent;
            navigator.clipboard.writeText(text).then(function () {
              b.textContent = "Copied";
              setTimeout(function () { b.textContent = label; }, 2000);
            });
          });
        });
      </script>"""


def _agent_steps(client: str, name: str, base: str, token: str,
                 view: str = "agents") -> tuple[str, str]:
    """An agent that runs commands sets itself up. The main path is one message
    holding a new key, so the person pastes once and the agent never needs a
    browser round trip; the key is minted by a POST to /api/v1/connect, never on
    a GET. Without a key, the agent signs in through the device login instead.
    Returns the stepper's Connect step and its First page step."""
    cap = name[:1].upper() + name[1:]
    # Where the key can be revoked: the list just below the panel in Settings, or
    # Settings from the graph view.
    revoke = ('You can revoke it any time in <a href="/settings/agents">Settings</a>, under '
              'Agents.' if view == "graph"
              else "It shows under Connected, below, where you can revoke it any time.")
    reload_note = ""
    if client == "grok-bot":
        reload_note = ("<p class=\"muted\">Grok Bot adds Dexio from chat and confirms it with you. "
                       "The Dexio tools work from your next message.</p>")
    if client == "hermes":
        reload_note = ("<p class=\"muted\">If Hermes runs in a chat app, it asks you to send "
                       "<code>/reload-mcp</code> at the end; that loads the Dexio tools.</p>")
    check = f"""<p>Once {e(name)} says it is connected, have it save your first page. Send:</p>
    {_copy_block("try-it", TRY_IT, "Copy")}
    <p class="muted">Connecting several agents? Send the same message to each one. They share
      the API key, each names itself on its changes, and turning it off disconnects all of
      them.</p>"""
    if not token:
        return f"""<p>{e(cap)} connects itself. Click below to get a message to paste into it.</p>
      <p><button type="button" class="cta" data-mint="{e(client)}">Get the message</button></p>
      <details>
        <summary>Rather not paste an API key into chat?</summary>
        <p>Send this instead. {e(cap)} replies with a link where you click <b>Allow</b>.</p>
        {_copy_block("device-prompt", AGENT_PROMPT)}
      </details>""", check
    return f"""<p>Copy this message and send it to {e(name)}. It does the rest.</p>
    {_copy_block("agent-prompt", key_prompt(token))}
    {reload_note}
    <p class="muted">The message holds an API key to your Dexio, so send it only to your own agent.
      {revoke}</p>
    <details>
      <summary>Set it up by hand instead</summary>
      <p>The API key on its own:</p>
      <pre><code>{e(token)}</code></pre>
      {_snippet(client, base, token)}
    </details>""", check


# ---- settings ------------------------------------------------------------
def appearance_panel(pref: str | None = None) -> str:
    """Mode and colour theme, Slack-style. Choosing applies at once and saves to
    the account in the background; without JavaScript the Save button posts."""
    theme, mode = themes.parse(pref)
    modes = "".join(
        f'<label><input type="radio" name="mode" value="{m}"'
        f'{" checked" if m == mode else ""}>{m.title()}</label>' for m in themes.MODES)
    cards = "".join(
        f'<label><input type="radio" name="theme" value="{tid}"'
        f'{" checked" if tid == theme else ""}>'
        f'<span class="sw" style="{themes.swatch_style(tid)}" aria-hidden="true">'
        f'<span class="bar"><i></i><b></b></span>'
        f'<span class="body"><i class="a"></i><i></i><i style="width:70%"></i></span></span>'
        f'<span class="name">{e(t["name"])}<span class="tick" aria-hidden="true">&#10003;</span>'
        f'</span></label>' for tid, t in themes.THEMES.items())
    return f"""<div class="panel" id="appearance">
      <form method="post" action="/settings/theme" class="plain" id="theme-form">
        <fieldset class="bare"><legend>Mode</legend><div class="modes">{modes}</div></fieldset>
        <fieldset class="bare"><legend>Theme</legend><div class="themes">{cards}</div></fieldset>
        <noscript><p><button type="submit">Save</button></p></noscript>
        <p class="saved" role="status" aria-live="polite"></p>
      </form>
      <script>
        (function () {{
          var f = document.getElementById("theme-form"), msg = f.querySelector(".saved");
          var T = window.dexioTheme;
          if (!T) return;
          // show what this browser is using, which is what is on screen
          var p = T.pref();
          [["theme", p[0]], ["mode", p[1]]].forEach(function (kv) {{
            var r = f.querySelector('input[name="' + kv[0] + '"][value="' + kv[1] + '"]');
            if (r) r.checked = true;
          }});
          f.addEventListener("change", function () {{
            var t = f.elements.theme.value, m = f.elements.mode.value;
            T.save(t, m);
            msg.textContent = "Saving\\u2026";
            fetch(f.action, {{
              method: "POST", credentials: "same-origin",
              headers: {{ "Content-Type": "application/x-www-form-urlencoded",
                          "Accept": "application/json" }},
              body: "theme=" + encodeURIComponent(t) + "&mode=" + encodeURIComponent(m)
            }}).then(function (r) {{
              msg.textContent = r.ok ? "Saved to your account."
                                     : "Not saved. It applies in this browser only.";
            }}, function () {{
              msg.textContent = "Not saved. It applies in this browser only.";
            }});
          }});
        }})();
      </script>
    </div>"""


def _size(n: int | None) -> str:
    if n is None:
        return "no limit"
    for unit, size in (("GB", 1024 ** 3), ("MB", 1024 ** 2), ("KB", 1024)):
        if n >= size:
            return f"{n / size:.1f} {unit}".replace(".0 ", " ")
    return f"{n} bytes"


# Files shown on one page of Settings > Files.
FILES_PER_PAGE = 50


def _pager(ws: dict, page: int, per: int, total: int) -> str:
    """Previous and Next for a paged list, with where this page sits in it."""
    if total <= per:
        return ""
    last = (total + per - 1) // per
    first_n, last_n = (page - 1) * per + 1, min(page * per, total)

    def to(n: int, label: str, rel: str) -> str:
        if n < 1 or n > last:
            return f'<span class="pg off" aria-disabled="true">{label}</span>'
        return (f'<a class="pg" rel="{rel}" href="/settings/files?w={ws["handle"]}&amp;page={n}">'
                f'{label}</a>')

    return (f'<nav class="pager" aria-label="Pages of files">'
            f'<span class="muted">{first_n:,}&ndash;{last_n:,} of {total:,}</span>'
            f'{to(page - 1, "&larr; Previous", "prev")}{to(page + 1, "Next &rarr;", "next")}'
            f'</nav>')


def files_panel(ws: dict, storage: dict | None) -> str:
    """Storage used against the plan's limit, one page of the workspace's files,
    and an upload form. Uploads PUT the file to /api/v1/files with the session
    cookie."""
    if storage is None:
        return ""
    used, limit = storage["used"], storage["limit"]
    pct = f" ({used * 100 // limit}%)" if limit else ""
    split = (f': {_size(storage["in_files"])} in files, {_size(storage["in_pages"])} in pages'
             f' and their history' if "in_files" in storage else "")
    total, page = storage.get("total", len(storage["files"])), storage.get("page", 1)
    per = storage.get("per_page", FILES_PER_PAGE)
    head = ["Path", "Size", "Updated", ""]
    hide = {2}
    rows = "".join(_row([
        f'<a class="path" href="/api/v1/files?path={quote(f["path"])}&amp;w={ws["handle"]}"'
        f' target="_blank" rel="noopener">{e(f["path"])}</a>',
        f'<span class="nw">{_size(f["size"])}</span>', _date(f["updated_at"]),
        f'<button type="button" class="quiet" data-del-path="{e(f["path"])}">Delete</button>'],
        hide) for f in storage["files"]) or _empty(len(head), "No files yet.")
    count = f'{total:,} file{"s" if total != 1 else ""}' if total else ""
    upload = """<form id="upload-form" class="row plain" onsubmit="return false">
        <div style="flex:1"><label for="up-folder">Folder <span class="opt">(optional)</span></label>
        <input id="up-folder" placeholder="raw"></div>
        <div><label for="up-file">File</label><input id="up-file" type="file" multiple></div>
        <button type="submit" id="up-go">Upload</button></form>"""
    return f"""<div class="panel" id="files">
      <h2>Storage</h2>
      <p class="who">Using <b>{_size(used)}</b> of {_size(limit)}{pct}{split}. Files up to
      {_size(storage["max_file"])} each: images, PDFs, decks, anything. Pages link to them by
      path, like <code>[deck](raw/deck.pdf)</code> or <code>![](images/arch.png)</code>.</p>
      {upload}
      <p class="saved" id="up-msg" role="status" aria-live="polite"></p>
    </div>
    <div class="panel">
      <div class="phead"><h2>Files</h2><span class="muted">{count}</span></div>
      {_table(head, rows, hide)}
      {_pager(ws, page, per, total)}
      <script>
        (function () {{
          var f = document.getElementById("upload-form"), msg = document.getElementById("up-msg");
          var ws = {json.dumps(ws["handle"])};
          function api(q) {{ return "/api/v1/files?w=" + ws + "&" + q; }}
          if (f) f.addEventListener("submit", async function () {{
            var folder = document.getElementById("up-folder").value.trim().replace(/^\/+|\/+$/g, "");
            var list = document.getElementById("up-file").files;
            if (!list.length) return;
            for (var i = 0; i < list.length; i++) {{
              var file = list[i], path = (folder ? folder + "/" : "") + file.name;
              msg.textContent = "Uploading " + path + "\u2026";
              var r = await fetch(api("path=" + encodeURIComponent(path)), {{ method: "PUT", body: file, credentials: "same-origin",
                headers: {{ "Content-Type": file.type || "application/octet-stream" }} }});
              if (!r.ok) {{
                var d = await r.json().catch(function () {{ return {{}}; }});
                msg.textContent = "Not uploaded: " + (d.error || r.status); return;
              }}
            }}
            location.reload();
          }});
          document.querySelectorAll("[data-del-path]").forEach(function (b) {{
            b.addEventListener("click", async function () {{
              var path = b.getAttribute("data-del-path");
              if (!confirm("Delete " + path + "? Deleted files cannot be restored.")) return;
              var r = await fetch(api("path=" + encodeURIComponent(path)), {{ method: "DELETE", credentials: "same-origin" }});
              if (r.ok) location.reload(); else msg.textContent = "Not deleted (" + r.status + ")";
            }});
          }});
        }})();
      </script>
    </div>"""


# Settings is one page per section, beside a sidebar of every section. The
# workspace's sections come first, since the gear sits in a workspace's graph;
# the person's own come after. Each section has its own URL, so an action lands
# back on the section it came from with its message in view.
# There was a Workspaces section under Account until 2026-09-27 (Forrest asked
# whether it was still needed): a list of the person's workspaces and a New
# workspace form, both of which the workspace menu now carries in the graph and
# in this sidebar. Its URL redirects to General.
# A Wikis section (list, rename, delete, new, download) went on 2026-09-28, when a
# workspace came to have one wiki; its download link is under General.
WORKSPACE_SECTIONS = (("general", "General"), ("members", "Members"), ("sharing", "Sharing"),
                      ("files", "Files"), ("plan", "Plan"))
# Agents is the person's since 2026-10-06 (Forrest: "why is the Agents screen still a
# part of the Workspace settings area?"): a key acts as its person in every workspace.
ACCOUNT_SECTIONS = (("profile", "Profile"), ("agents", "Agents"), ("appearance", "Appearance"),
                    ("help", "Help"))
SECTIONS = dict(WORKSPACE_SECTIONS + ACCOUNT_SECTIONS)
WORKSPACE_ONLY = {k for k, _ in WORKSPACE_SECTIONS}

# What a finished action says once it has redirected back to its section
# (?done=). Fixed strings, so a link cannot put words on the page.
DONE = {
    "renamed": "Workspace renamed.",
    "revoked": "API key revoked.",
    "disconnected": "App disconnected.",
    "name": "Name saved.",
    "publisher": "Publisher name saved.",
    "password": "Password changed. Any other signed-in browsers have been signed out.",
    "role": "Role changed.",
    "removed": "Removed from the workspace. Their API keys and app sign-ins there stopped working.",
    "left": "You left the workspace.",
    "deleted": "Workspace deleted.",
    "invited": "Invite sent. The link in it works once and expires in 7 days.",
    "resent": "Invite sent again. The earlier link no longer works.",
    "cancelled": "Invite cancelled. Its link no longer works.",
    "upgraded": "Upgraded. Stripe charged the difference for the rest of this billing month.",
    "switched": "Plan changed. The unused part of this month comes off your next invoice.",
    "to_free": "Done. The paid plan stays until the end of the month you paid for, then this"
               " workspace moves to Free. Nothing more is charged.",
    "to_free_now": "Done. This workspace is on Free now.",
    "kept": "The plan continues. Nothing changes.",
    "contact_sent": "Message sent. We will reply to you by email.",
    "unshared": "Stopped sharing. Their link no longer opens it.",
    "private": "Made private. Its public address now asks people to sign in.",
    "extra": "Removed. The wider share it sat under still gives the same access.",
    "unpublished": "Unpublished. It is off dexio.wiki, and anyone with the link can still open it.",
}
BILLING_DONE = ("Thanks. The plan changes as soon as Stripe confirms the payment, usually "
                "within a few seconds; reload if it has not yet.")

# Old links to a panel of the single long page still land on its section.
OLD_ANCHORS = {"#appearance": "/settings/appearance", "#files": "/settings/files"}


def section_url(section: str) -> str:
    return "/settings" if section == "general" else f"/settings/{section}"


HIDE = ' class="hide-sm"'


def _table(head: list[str], rows: str, hide: set[int] = frozenset()) -> str:
    """A table that scrolls inside its panel rather than widening the page; the
    columns in `hide` drop out on a phone."""
    ths = "".join(f'<th{HIDE if i in hide else ""}>{h}</th>'
                  for i, h in enumerate(head))
    return f'<div class="tbl"><table><tr>{ths}</tr>{rows}</table></div>'


def _td(value: str, i: int, hide: set[int]) -> str:
    return f'<td{HIDE if i in hide else ""}>{value}</td>'


def _row(cells: list[str], hide: set[int] = frozenset()) -> str:
    return "<tr>" + "".join(_td(c, i, hide) for i, c in enumerate(cells)) + "</tr>"


def _date(ts) -> str:
    return f'<span class="nw">{when(ts)}</span>'


def _empty(cols: int, text: str) -> str:
    return f'<tr><td colspan="{cols}" class="muted">{e(text)}</td></tr>'


def _settings_nav(section: str, ws: dict | None, workspaces: list[dict]) -> str:
    def link(key: str, label: str) -> str:
        here = ' aria-current="page"' if key == section else ""
        return f'<a class="sec" href="{section_url(key)}"{here}>{e(label)}</a>'

    parts = ['<a class="back" href="/">&larr; Back to the graph</a>']
    if ws:
        parts.append('<div class="sgroup">Workspace</div>')
        # The graph's workspace menu, here too: switching keeps you on the same
        # section, in the other workspace, and New workspace is always on offer.
        # Agents stays put: the workspace picked here is where a new agent starts.
        target = section_url(section if section in WORKSPACE_ONLY or section == "agents"
                             else "general")
        parts.append(workspace_menu(workspaces or [ws], ws["id"],
                                    lambda w: f"{target}?w={w['handle']}", menu_id="ws-menu"))
        parts += [link(k, label) for k, label in WORKSPACE_SECTIONS]
    parts.append('<div class="sgroup">Account</div>')
    parts += [link(k, label) for k, label in ACCOUNT_SECTIONS]
    return '<nav class="snav" aria-label="Settings">' + "".join(parts) + "</nav>"


SETTINGS_SCRIPT = """<script>
  (function () {
    var old = %s[location.hash];
    if (old && location.pathname === "/settings") location.replace(old);
    // A ⋯ menu closes when you click elsewhere or press Escape, and only one is open.
    document.addEventListener("click", function (ev) {
      document.querySelectorAll("details.menu[open]").forEach(function (d) {
        if (!d.contains(ev.target)) d.removeAttribute("open");
      });
    });
    document.addEventListener("keydown", function (ev) {
      if (ev.key !== "Escape") return;
      document.querySelectorAll("details.menu[open]").forEach(function (d) {
        d.removeAttribute("open");
        d.querySelector("summary").focus();
      });
    });
  })();
</script>"""


def settings_page(section: str, email: str, ws: dict | None, workspaces: list[dict], *,
                  error: str = "", ok: str = "", me: int | None = None, read_only: str = "",
                  **data) -> str:
    """One section of Settings. `data` holds what that section shows; see the
    _general, _members ... functions below for the keys each one reads.
    read_only is db.read_only_reason for the workspace, shown on every section."""
    owner = bool(ws) and ws.get("role") == "owner"
    body = {
        "general": lambda: _general(ws, owner, data.get("billing") or {},
                                     data.get("leave_blocker", ""), data.get("delete_blocker", ""),
                                     data.get("members") or [], data.get("wiki") or {}, me),
        "members": lambda: _members(ws, owner, data.get("members") or [], data.get("limit"),
                                     me, data.get("pending") or [], data.get("seat_price")),
        "plan": lambda: _plan(ws, owner, data.get("billing") or {}, data.get("owners") or [],
                              email),
        "agents": lambda: _agents(ws, data.get("tokens") or [], data.get("apps") or [],
                                  data.get("connect"), data.get("base", ""),
                                  data.get("api", "/api/v1"), len(workspaces) > 1),
        "files": lambda: files_panel(ws, data.get("storage")),
        "sharing": lambda: _sharing(ws, data.get("sharing") or {}, data.get("team", False)),
        "profile": lambda: _profile(email, data.get("first", ""), data.get("last", ""),
                                     data.get("has_password", True),
                                     data.get("providers") or [], data.get("erase")),
        "appearance": lambda: appearance_panel(data.get("theme")),
        "help": lambda: _help(ws, email),
    }[section]()
    title = SECTIONS[section]
    locked = ""
    if read_only:        # the reason after "read-only: ", without the link to this page
        why = read_only.split(": ", 1)[-1].split(", at https://", 1)[0]
        locked = (f'<div class="alert bad"><div><b>This workspace is read-only.</b>'
                  f' {e(why[:1].upper() + why[1:])}.</div></div>')
    page = f"""<div class="settings">
    {_settings_nav(section, ws, workspaces)}
    <main class="sbody">
      <h1>{e(title)}</h1>
      {locked}
      {_message(error, ok)}
      {body}
    </main>
  </div>
  {SETTINGS_SCRIPT % json.dumps(OLD_ANCHORS)}
  <script>{WS_MENU_JS}</script>
  {f"<script>{ais.STEPS_JS}{CONNECT_JS}</script>" if section == "agents" else ""}"""
    return _page(f"Settings: {title}", page, width=920)


def _in(ws: dict) -> str:
    """Pins a workspace form to the workspace on screen, whatever another tab
    has since switched to."""
    return f"?w={ws['handle']}"


def _confirm(text: str) -> str:
    return f' onsubmit="return confirm({e(json.dumps(text))})"'


def _general(ws: dict, owner: bool, billing: dict, leave_blocker: str, delete_blocker: str,
             members: list[dict], wiki: dict, me: int | None) -> str:
    if owner:
        name = f"""<form method="post" action="/settings/rename{_in(ws)}" class="row plain">
        <div style="flex:1"><label for="wsname">Workspace name</label>
        <input id="wsname" name="name" value="{e(ws["name"])}" maxlength="80" required></div>
        <button type="submit">Save</button></form>"""
    else:
        name = (f'<p class="who"><b>{e(ws["name"])}</b></p>'
                '<p class="muted">Only an owner can rename it.</p>')
    panels = [f'<div class="panel"><h2>Name</h2>{name}</div>']

    # Who this workspace publishes as on dexio.wiki (shares.set_publisher).
    pub = ws.get("publisher_name") or ""
    about = ("Everything this workspace publishes shows on dexio.wiki as by this name, and"
             " lists on its own page there. Letters, numbers and hyphens, no spaces; no other"
             " workspace can use it.")
    if pub:
        page = f"https://dexio.wiki/wikis/{pub.lower()}/"
        about += f" Its page: <a href='{e(page)}'>{e(page.replace('https://', ''))}</a>."
    if owner:
        publisher = f"""<form method="post" action="/settings/publisher{_in(ws)}" class="row plain">
        <div style="flex:1"><label for="publisher">Publisher name</label>
        <input id="publisher" name="publisher" value="{e(pub)}" minlength="2" maxlength="39"
          pattern="[A-Za-z0-9]+(-[A-Za-z0-9]+)*" placeholder="wrenfield-roasters"
          autocapitalize="off" spellcheck="false" required></div>
        <button type="submit">Save</button></form>
        <p class="muted">{about}</p>"""   # about is built from escaped parts
    else:
        publisher = ((f'<p class="who"><b>{e(pub)}</b></p>' if pub else
                      '<p class="who">None yet.</p>')
                     + f'<p class="muted">{about} Only an owner can change it.</p>')
    panels.append(f'<div class="panel" id="publisher-name"><h2>Publishing</h2>{publisher}</div>')

    # The wiki: its size, and a download of every page. It moved here from the
    # Wikis section, which went when a workspace came to have one wiki.
    n = int(wiki.get("pages") or 0)
    size = (f'{n:,} page{"s" if n != 1 else ""}, {int(wiki.get("words") or 0):,} words'
            if n else "No pages yet")
    panels.append(f"""<div class="panel" id="wiki"><h2>Wiki</h2>
      <p class="who">{size}.</p>
      <p><a class="button alt" download href="/api/v1/export?w={ws["handle"]}">Download as
        markdown</a></p>
      <p class="muted">Every page as a markdown file at its path, in one zip. Uploaded files
        download one at a time from Files.</p></div>""")

    # Leave: anyone, unless it would leave the workspace without an owner or empty.
    if leave_blocker:
        leave = f'<p class="muted">{e(leave_blocker)}</p>'
    else:
        leave = f"""<p class="muted">You lose access to its wiki, and the agents you connected to it
        stop working. Pages you wrote stay.</p>
      <form method="post" action="/settings/leave{_in(ws)}" class="inline"{_confirm(
            f"Leave {ws['name']}? You will need a new invite to come back.")}>
        <button type="submit" class="quiet">Leave workspace</button></form>"""
    if not (owner and leave_blocker.startswith("You are its only member")):
        panels.append(f'<div class="panel" id="leave"><h2>Leave this workspace</h2>{leave}</div>')

    # Delete: owners only, once any paid plan is cancelled.
    if owner:
        if delete_blocker:
            body = f'<p class="muted">{e(delete_blocker)}</p>'
        else:
            others = len([m for m in members if m["id"] != me])
            lose = (f" {others} other member{'s' if others != 1 else ''} lose"
                    f"{'s' if others == 1 else ''} access." if others else "")
            body = f"""<p class="who">This deletes \u201c{e(ws["name"])}\u201d and everything in it:
        its wiki, with page history and files. Agents
        connected to it stop working.{e(lose)} It cannot be undone.</p>
      <form method="post" action="/settings/delete{_in(ws)}" class="plain">
        <label for="confirm_name">Type <b>{e(ws["name"])}</b> to confirm</label>
        <input id="confirm_name" name="confirm_name" autocomplete="off" required>
        <button type="submit" class="danger">Delete workspace</button></form>"""
        panels.append(f'<div class="panel" id="delete-workspace"><h2>Delete this workspace</h2>'
                      f'{body}</div>')
    return "".join(panels)


BAD = ' class="bad"'


def _row_menu(who: str, items: list[tuple[str, str, str, str]]) -> str:
    """A ⋯ menu of POST actions for one row. Each item: (url, role field or "",
    label, confirm text or ""). Items whose label starts Remove or Cancel read
    as destructive."""
    forms = "".join(
        f'<form method="post" action="{url}"{_confirm(ask) if ask else ""}>'
        + (f'<input type="hidden" name="role" value="{role}">' if role else "")
        + f'<button type="submit" role="menuitem"'
          f'{BAD if label.startswith(("Remove", "Cancel")) else ""}>{e(label)}</button></form>'
        for url, role, label, ask in items)
    return (f'<div class="acts"><details class="menu"><summary aria-label="Actions for '
            f'{e(who)}" title="Actions">&#8943;</summary>'
            f'<div class="menu-list" role="menu">{forms}</div></details></div>')


def _member_actions(ws: dict, m: dict, me: int | None) -> str:
    """What an owner can do to someone else: change their role, or remove them.
    Nothing on your own row; you leave from General."""
    if m["id"] == me:
        return ""
    who = " ".join(p for p in (m.get("first_name"), m.get("last_name")) if p) or m["email"]
    base = f'/settings/members/{m["id"]}'
    if m["role"] != "owner":
        items = [(f"{base}/role{_in(ws)}", "owner", "Make owner",
                  f"Make {who} an owner of {ws['name']}? Owners can invite and remove people,"
                  " manage billing and delete the workspace.")]
    else:
        items = [(f"{base}/role{_in(ws)}", "member", "Make member", "")]
    items.append((f"{base}/remove{_in(ws)}", "", "Remove from workspace",
                  f"Remove {who} from {ws['name']}? Their API keys and app sign-ins there"
                  " stop working."))
    return _row_menu(who, items)


def _members(ws: dict, owner: bool, members: list[dict], limit: int | None,
             me: int | None = None, pending: list[dict] | None = None,
             seat_price: int | None = None) -> str:
    hide = {1, 2}          # on a phone the role sits under the name
    pending = pending or []

    def person(m: dict) -> str:
        name = " ".join(p for p in (m.get("first_name"), m.get("last_name")) if p) or m["email"]
        you = ' <span class="tag">you</span>' if m["id"] == me else ""
        role = f'<span class="sub show-sm">{e(str(m["role"]).title())}</span>'
        return f'{e(name)}{you}<span class="sub">{e(m["email"])}</span>{role}'

    def invited(i: dict) -> str:
        return (f'{e(i["email"])} <span class="tag">pending</span>'
                f'<span class="sub">Invited {when(i["created_at"])}, link expires '
                f'{when(i["expires_at"])}</span><span class="sub show-sm">Member</span>')

    def invite_actions(i: dict) -> str:
        base = f'/settings/invites/{i["id"]}'
        return _row_menu(i["email"], [
            (f"{base}/resend{_in(ws)}", "", "Resend invite", ""),
            (f"{base}/cancel{_in(ws)}", "", "Cancel invite", "")])

    people = "".join(_row(
        [person(m), e(str(m["role"]).title()), _date(m["created_at"])]
        + ([_member_actions(ws, m, me)] if owner else []), hide) for m in members)
    people += "".join(_row(
        [invited(i), "Member", '<span class="muted">Not yet</span>']
        + ([invite_actions(i)] if owner else []), hide) for i in pending)
    count = f'{len(members)} member{"s" if len(members) != 1 else ""}' + (
        f", {len(pending)} pending" if pending else "")
    if limit is not None and len(members) >= limit:
        invite = (f'<p class="muted">{e(ws["plan"].title())} workspaces have {limit} member'
                  f'{"s" if limit != 1 else ""}. Team adds as many people as you need, at'
                  f' ${PRICES["team"]} each a month.</p>'
                  + (f'<a class="button" href="/settings/plan{_in(ws)}">See plans</a>' if owner
                     else '<p class="muted">Ask an owner to move the workspace to Team.</p>'))
    elif owner:
        invite = f"""<form method="post" action="/settings/invite{_in(ws)}" class="row plain">
          <div style="flex:1"><label for="invite-email">Email</label>
          <input id="invite-email" name="email" type="email" required autocomplete="off"
          placeholder="name@company.com"></div>
          <button type="submit">Send invite</button></form>
          <p class="muted">We email them a link to join. It works once and expires in 7 days.
          They show as pending under People until they join.</p>""" + (
            f'<p class="muted">Each person who joins adds ${seat_price} a month to your bill,'
            f' prorated. See <a href="/settings/plan{_in(ws)}">Plan</a>.</p>' if seat_price else "")
    else:
        invite = '<p class="muted">Ask an owner of this workspace to invite people.</p>'
    roles = ('<p class="muted">Owners rename the workspace, invite and remove people, manage '
             'billing and can delete it. Members use its wiki and connect their own agents.</p>')
    head = ["Name", "Role", "Joined"] + ([""] if owner else [])
    return f"""<div class="panel"><div class="phead"><h2>People</h2>
      <span class="muted">{count}</span></div>
      {_table(head, people, hide)}{roles}</div>
    <div class="panel"><h2>Invite</h2>{invite}</div>"""


# ---- Sharing -----------------------------------------------------------------------
# Forrest, 2026-09-28: "in settings, can we make it easy to see what parts of the
# wiki have been shared?" The Share dialog shows one thing at a time; this lists
# everything shared outside the workspace: what is public on the web, then who
# each thing is shared with. Each row links to what it shares, and its ⋯ menu
# takes it away. Adding shares stays in the graph, where you pick what to share.
_KIND_SVG = {
    "wiki": '<circle cx="4" cy="4.6" r="1.9"></circle><circle cx="12" cy="4.6" r="1.9"></circle>'
            '<circle cx="8" cy="11.8" r="1.9"></circle><path d="M5.9 4.6h4.2M5 6.2l2 3.9'
            'M11 6.2l-2 3.9"></path>',
    "folder": '<path d="M1.8 4.2c0-.7.5-1.2 1.2-1.2h3.1l1.5 1.6h5.4c.7 0 1.2.5 1.2 1.2v6.2'
              'c0 .7-.5 1.2-1.2 1.2H3c-.7 0-1.2-.5-1.2-1.2z"></path>',
    "page": '<path d="M4 1.8h5.2l3 3v9.4H4zM9.2 1.8v3h3M6.2 8.2h3.6M6.2 10.8h3.6"></path>',
}


def _pages(n: int) -> str:
    return f'{n:,} page{"s" if n != 1 else ""}'


def _via(v: dict | None) -> str:
    if not v:
        return ""
    return "the whole wiki" if v["kind"] == "wiki" else f'the folder {v["path"]}'


def _target(ws: dict, it: dict, tag: str = "") -> str:
    """What one share reaches: an icon, its name linked to it in the graph, and a
    grey line on what it covers now; `tag`, a pill after the name."""
    kind, path, n = it["kind"], it["path"], it["pages"]
    name = "The whole wiki" if kind == "wiki" else it["title"]
    href = f'/w/{ws["handle"]}' + ("/" + quote(path, safe="/") if kind == "page"
                                   else "?folder=" + quote(path, safe="/") if kind == "folder"
                                   else "")
    shown = f'<a href="{e(href)}">{e(name)}</a>' if it["exists"] else e(name)
    if tag:
        shown += f' <span class="tag">{e(tag)}</span>'
    if kind == "wiki":
        sub = f"Every page, now and later ({_pages(n)} now)"
    elif kind == "folder":
        sub = (f"Folder, {_pages(n)}" if n
               else "Folder, empty now. It applies again if pages are added.")
    else:
        sub = path if it["exists"] else f"{path}, deleted. It applies again if the page returns."
    via = (f'<span class="sub">Already covered by {e(_via(it["via"]))}</span>'
           if it["via"] else "")
    return (f'<div class="target"><span class="ticon">'
            f'<svg viewBox="0 0 16 16" aria-hidden="true">{_KIND_SVG.get(kind, "")}</svg></span>'
            f'<span class="tname">{shown}<span class="sub">{e(sub)}</span>{via}</span></div>')


def _since(it: dict, team: bool) -> str:
    by = f'<span class="sub">by {e(it["by"])}</span>' if team and it.get("by") else ""
    return _date(it["created_at"]) + by


def _sharing(ws: dict, ov: dict, team: bool) -> str:
    public, people = ov.get("public") or [], ov.get("people") or []
    graph = f'/w/{ws["handle"]}'
    how = (f'To share something, open it in the <a href="{e(graph)}">graph</a> and use Share'
           ' at the top.')
    if not public and not people:
        return (f'<div class="panel"><h2>Nothing is shared</h2>'
                f'<p class="who">Only members of {e(ws["name"])} can open its wiki.</p>'
                f'<p class="muted">{how}</p></div>')
    base = "/settings/sharing"
    hide = {1}                                  # the date drops out on a phone

    def menu(it: dict, label: str, ask: str) -> str:
        return _row_menu(it["title"], [(f'{base}/{it["id"]}/stop{_in(ws)}', "", label, ask)])

    # Public things in two panels, as Visibility's levels in the Share dialog
    # (Forrest, 2026-10-01: "how-we-build-dexio is public on dexio.wiki, but it's
    # listed under anyone with the link"): Published on dexio.wiki, then Anyone
    # with the link. A published thing is open with its link too; it shows once.
    def count(n: int) -> str:
        total = int(ov.get("pages") or 0)
        return (f"All {_pages(total)}" if total and n == total
                else f"{n:,} of {_pages(total)}" if n else "")

    def pub_row(it: dict) -> str:
        name = "the whole wiki" if it["kind"] == "wiki" else it["title"]
        if it["via"]:
            act = menu(it, "Remove", f"Remove this share? {name} stays public through"
                                     f" {_via(it['via'])}.")
        else:
            act = menu(it, "Make private", f"Make {name} private? Its address will ask people"
                                           " to sign in.")
        return _row([_target(ws, it), _since(it, team), act], hide)

    def published_row(it: dict) -> str:
        name = it.get("listed_title") or it["title"]
        items = [(f'{base}/{it["id"]}/unpublish{_in(ws)}', "", "Unpublish",
                  f"Take {name} off dexio.wiki? Anyone with the link can still open it.")]
        if it["via"]:
            items.append((f'{base}/{it["id"]}/stop{_in(ws)}', "", "Remove",
                          f"Take {name} off dexio.wiki and remove this share? It stays open to"
                          f" anyone with the link through {_via(it['via'])}."))
        else:
            items.append((f'{base}/{it["id"]}/stop{_in(ws)}', "", "Make private",
                          f"Make {name} private? It leaves the public wikis on dexio.wiki, and"
                          " its address will ask people to sign in."))
        # the name it has on dexio.wiki, then what of the wiki it is
        what = _target(ws, {**it, "title": name})
        if it["kind"] == "wiki":
            what = what.replace(">The whole wiki<", f">{e(name)}<", 1).replace(
                '<span class="sub">Every page,', '<span class="sub">The whole wiki: every page,', 1)
        else:
            what = what.replace('<span class="sub">Folder, ',
                                f'<span class="sub">Folder {e(it["path"])}, ', 1)
        by = (f'<span class="sub">by {e(it["listed_by"])}</span>'
              if team and it.get("listed_by") else "")
        return _row([what, _date(it["listed_at"]) + by, _row_menu(name, items)], hide)

    published = [it for it in public if it.get("listed")]
    link = [it for it in public if not it.get("listed")]
    pub_name = ov.get("publisher") or ""
    where = (f' under <a href="https://dexio.wiki/wikis/{e(pub_name.lower())}/">{e(pub_name)}</a>'
             if pub_name and published else "")
    rows = "".join(published_row(it) for it in published) or _empty(3, "Nothing is published.")
    panels = [f"""<div class="panel" id="published"><div class="phead"><h2>Published on
      dexio.wiki</h2><span class="muted">{e(count(int(ov.get("published_pages") or 0)))}</span></div>
      <p class="muted">Listed with the <a href="https://dexio.wiki/wikis/">public wikis</a>{where}.
        Anyone can read these and make a copy.</p>
      {_table(["What", "Published", ""], rows, hide)}</div>"""]
    rows = "".join(pub_row(it) for it in link) or _empty(3, "Nothing else is public.")
    panels.append(f"""<div class="panel" id="public"><div class="phead"><h2>Anyone with the link</h2>
      <span class="muted">{e(count(int(ov.get("link_pages") or 0)))}</span></div>
      <p class="muted">Anyone can open these without signing in.</p>
      {_table(["What", "Made public", ""], rows, hide)}</div>""")

    # One heading row per person, then what they can view under it: a person
    # with three shares reads once, and a phone has room for the paths.
    def person(it: dict) -> str:
        if it.get("name") and it["name"] != it["email"]:
            return f'{e(it["name"])}<span class="sub">{e(it["email"])}</span>'
        return e(it["email"])

    def people_row(it: dict) -> str:
        name = "the whole wiki" if it["kind"] == "wiki" else it["title"]
        ask = f"Stop sharing {name} with {it['email']}?" + (
            f" They keep it through {_via(it['via'])}." if it["via"] else "")
        # each share has its own link, opened or not
        what = _target(ws, it, "link not opened" if it["pending"] else "")
        return (f'<tr class="item">{_td(what, 0, hide)}{_td(_since(it, team), 1, hide)}'
                f'{_td(menu(it, "Stop sharing", ask), 2, hide)}</tr>')

    rows, last = "", None
    for it in people:
        if it["email"] != last:
            last = it["email"]
            rows += f'<tr class="who-row"><td colspan="3">{person(it)}</td></tr>'
        rows += people_row(it)
    who = {it["email"] for it in people}
    waiting = sum(1 for it in people if it["pending"])
    count = f'{len(who)} {"person" if len(who) == 1 else "people"}' + (
        f', {waiting} link{"s" if waiting != 1 else ""} not opened' if waiting else "")
    rows = rows or _empty(3, "Nothing is shared with anyone.")
    panels.append(f"""<div class="panel" id="people"><div class="phead"><h2>Shared with
      people</h2><span class="muted">{e(count if people else "")}</span></div>
      <p class="muted">They can view it after signing in with the address it was sent to.
        Members of {e(ws["name"])} see everything and are under
        <a href="/settings/members{_in(ws)}">Members</a>.</p>
      {_table(["Person, and what they can view", "Shared", ""], rows, hide)}
      <p class="muted">{how}</p></div>""")
    return "".join(panels)


def _agents(ws: dict | None, tokens: list[dict], apps: list[dict], connect: str | None,
            base: str, api: str, many: bool = False) -> str:
    """Your agents: your API keys and app sign-ins in every workspace, in one list.
    An account section since 2026-10-06 (Forrest: "why is the Agents screen still a
    part of the Workspace settings area?"), once a key acted as its person. Each row
    says where it starts when you are in more than one workspace (many). Above it,
    the connect panel, always open (Forrest, 2026-09-27: it doesn't need to be
    collapsible); a new agent starts in the workspace Settings is on. ?connect=<AI>
    still picks an AI in it; the account menu and old links come in that way."""
    # No Access column since 2026-09-27 (Forrest asked what it did): how the AI
    # connected, and where it starts, is a grey line under its name instead.
    hide = {1}                                  # Added drops out on a phone

    def name(label: str, how: str, where: str) -> str:
        at = f", starts in {e(where)}" if many and where else ""
        return f'{e(label)}<span class="sub">{how}{at}</span>'

    rows = [((t["last_used"] or 0), _row([
        name(t["name"], "API key", t.get("workspace_name", "")),
        _date(t["created_at"]), _date(t["last_used"]),
        f'<form method="post" action="/settings/tokens/{t["id"]}/revoke" class="inline">'
        f'<button type="submit" class="quiet">Revoke</button></form>'], hide))
        for t in tokens]
    rows += [((a["last_used"] or 0), _row([
        name(a["name"], "Signed in", a.get("workspace_name", "")),
        _date(a["since"]), _date(a["last_used"]),
        f'<form method="post" action="/settings/apps/{e(a["client_id"])}/disconnect'
        f'?in={e(a.get("workspace_handle", ""))}" class="inline">'
        f'<button type="submit" class="quiet">Disconnect</button></form>'],
        hide)) for a in apps]
    rows.sort(key=lambda r: -r[0])
    body = "".join(r for _, r in rows) or _empty(4, "None yet. Connect one to get started.")
    connect_html = _connect_panel(ws, connect, base, api) if ws else ""
    return f"""{connect_html}
    <div class="panel" id="connected"><h2>Your agents</h2>
      <p class="muted">Every agent you have connected, most recently used first. Each one works
      as you: in every workspace you are in, and it can read what other workspaces share with
      you. Revoking or disconnecting one takes effect at once.</p>
      {_table(["Name", "Added", "Last used", ""], body, hide)}</div>"""


def _connect_panel(ws: dict, connect: str | None, base: str, api: str) -> str:
    """Connecting an AI, on the Agents page itself (Forrest, 2026-09-27: it should
    not change screen), and always open (same day: it doesn't need to be
    collapsible). Pick an AI and its steps load in place; "Get the message" mints
    a key and the list below picks it up. No "Waiting for your AI to connect" line
    (same day: Forrest connected Claude and it never resolved, and didn't think it
    necessary); the Connected list re-reads itself when the tab comes back into
    view, so an AI that signed in elsewhere shows there. connect is the ?connect
    value: an AI's id picks that AI, anything else picks none."""
    head = "<h2>Connect an agent</h2>"
    pick = connect if connect in CLIENTS else ""
    tiles = "".join(
        f'<button type="button" class="choice ai-tile" data-client="{e(k)}"'
        f' aria-pressed="{"true" if k == pick else "false"}">{ais.tile(k, n, s)}</button>'
        for k, (n, s, _kind) in CLIENTS.items())
    steps = connect_steps(pick, base) if pick else ""
    at = 2 if pick else 1
    picked = CLIENTS[pick][0] if pick else ""
    return f"""<div class="panel" id="connect" data-api="{e(api)}" data-ws="{e(ws['handle'])}"
      data-client="{e(pick)}">
      {head}
      <p class="muted">Which agent do you use? Once it is connected it works as you, starting in
      {e(ws["name"])}.</p>
      <div class="ai-flow" data-at="{at}"{f' data-client="{e(pick)}"' if pick else ""}>
        {ais.steps_head(at, picked)}
        <div class="ai-pane" data-pane="1">
          <div class="choices ai-grid" role="group" aria-label="Your agent">{tiles}</div></div>
        <div class="csteps" aria-live="polite">{steps}</div>
      </div>
    </div>"""


# The connect panel's script. The steps are server-rendered fragments
# (GET /api/v1/connect), so their copy and "Get the message" buttons are handled
# here by delegation. Nothing polls: the Connected list is re-read from this page
# after a key is made and whenever the tab comes back into view (as after
# allowing access in Claude or ChatGPT in another tab). The address keeps
# ?connect, so a reload comes back to the same AI.
CONNECT_JS = r"""
(function () {
  const box = document.getElementById("connect");
  if (!box || !box.dataset.api) return;
  const api = box.dataset.api || "/api/v1", wsq = "?w=" + encodeURIComponent(box.dataset.ws || "");
  const steps = box.querySelector(".csteps");
  let client = box.dataset.client || "";
  const flow = box.querySelector(".ai-flow");
  const enc = encodeURIComponent;
  // setup_events: the panel is always open, so every visit counts as one showing,
  // with the AI a ?connect link picked, if any.
  if (window.dexioSetup) window.dexioSetup("connect_shown", "agents" + (client ? ":" + client : ""));

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function remember() {
    const u = new URL(location.href);
    u.searchParams.delete("done");
    u.searchParams.set("connect", client);
    u.searchParams.delete("wiki");
    history.replaceState(null, "", u);
  }

  async function failure(r) {
    if (!r) return "Could not reach Dexio. Try again.";
    let detail = "something went wrong";
    try { detail = (await r.json()).error || detail; } catch (err) {}
    return detail[0].toUpperCase() + detail.slice(1) + (detail.endsWith(".") ? "" : ".");
  }

  async function pick(c) {
    client = c;
    for (const b of box.querySelectorAll(".choice")) {
      b.setAttribute("aria-pressed", String(b.dataset.client === c));
    }
    remember();
    const tile = [...box.querySelectorAll(".choice")].find(b => b.dataset.client === c);
    dexioSteps.picked(flow, c, tile ? tile.querySelector("b").textContent : c);
    const wait = el("div", "ai-pane");
    wait.dataset.pane = "2";
    wait.append(el("p", "muted", "Loading…"));
    steps.replaceChildren(wait);
    dexioSteps.go(flow, 2);
    let r;
    try {
      r = await fetch(api + "/connect" + wsq + "&client=" + enc(c));
    } catch (err) { r = null; }
    if (client !== c) return;               // another pick arrived first
    if (!r || !r.ok) return wait.replaceChildren(el("p", "cerr", await failure(r)));
    const html = (await r.json()).html;
    if (client !== c) return;
    steps.innerHTML = html;
  }

  box.addEventListener("click", async e => {
    const tile = e.target.closest(".choice");
    if (tile) return pick(tile.dataset.client);
    const copy = e.target.closest("[data-copy]");
    if (copy) {
      const src = document.getElementById(copy.dataset.copy);
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
    mint.disabled = true;
    let r;
    try {
      r = await fetch(api + "/connect" + wsq, {
        method: "POST", headers: {"Content-Type": "application/json"},
        body: JSON.stringify({client: mint.dataset.mint})});
    } catch (err) { r = null; }
    if (!r || !r.ok) {
      mint.disabled = false;
      const old = steps.querySelector(".cerr");
      if (old) old.remove();
      mint.insertAdjacentElement("afterend", el("span", "cerr", " " + await failure(r)));
      return;
    }
    steps.innerHTML = (await r.json()).html;
    const first = steps.querySelector("[data-copy]");
    if (first) first.focus();
    refreshList();                          // the new key shows under Connected
  });

  async function refreshList() {
    try {
      const u = new URL(location.href);
      u.searchParams.delete("connect");
      u.searchParams.delete("wiki");
      const r = await fetch(u);
      if (!r.ok) return;
      const doc = new DOMParser().parseFromString(await r.text(), "text/html");
      const fresh = doc.querySelector("#connected .tbl"), old = document.querySelector("#connected .tbl");
      if (fresh && old) old.replaceWith(document.adoptNode(fresh));
    } catch (err) {}
  }

  // Back from Claude or ChatGPT in another tab: show what signed in meanwhile.
  document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshList(); });
})();
"""


PROVIDER_NAMES = {"google": "Google", "github": "GitHub"}


def _profile(email: str, first: str, last: str, has_password: bool,
             providers: list[str], erase_plan: dict | None = None) -> str:
    ways = (["Password"] if has_password else []) + [PROVIDER_NAMES.get(p, p.title())
                                                      for p in providers]
    if has_password:
        password = f"""<form method="post" action="/account/password" class="plain">
        <input type="email" name="username" autocomplete="username" value="{e(email)}" hidden>
        <label for="current">Current password</label>
        <input id="current" name="current_password" type="password"
               autocomplete="current-password" required>
        <div class="pair">
          <div><label for="new">New password</label>
          <input id="new" name="new_password" type="password" autocomplete="new-password"
                 minlength="12" required></div>
          <div><label for="confirm">Confirm new password</label>
          <input id="confirm" name="confirm_password" type="password"
                 autocomplete="new-password" minlength="12" required></div>
        </div>
        <button type="submit">Change password</button>
        <p class="muted">At least 12 characters. Changing it signs out every other browser.</p>
      </form>"""
    else:
        password = ('<p class="muted">No password on this account. To add one, '
                    '<a href="/forgot">get a link by email</a>.</p>')
    return f"""<div class="panel"><h2>Name</h2>
      <form method="post" action="/account/name" class="plain">
        <div class="pair">
          <div><label for="first_name">First name</label>
          <input id="first_name" name="first_name" autocomplete="given-name" maxlength="80"
                 value="{e(first)}"></div>
          <div><label for="last_name">Last name</label>
          <input id="last_name" name="last_name" autocomplete="family-name" maxlength="80"
                 value="{e(last)}"></div>
        </div>
        <button type="submit">Save name</button>
      </form></div>
    <div class="panel"><h2>Sign-in</h2>
      <p class="who">Email <b>{e(email)}</b><br>Signs in with {e(", ".join(ways) or "a link")}</p>
      {password}</div>
    <div class="panel"><div class="phead"><h2>Sign out</h2>
      <form method="post" action="/logout" class="inline">
      <button type="submit" class="quiet">Sign out</button></form></div>
      <p class="muted">Signs this browser out.</p></div>
    {delete_account_panel(erase_plan) if erase_plan else ""}"""


PRICES = {"team": 10, "business": 20}      # per member a month; there is no annual billing
PRICING_URL = "https://dexio.wiki/pricing"
# What a message from the Plan page's contact form is about, by the key the
# button that opened it carries (a plan, or "billing").
CONTACT_ABOUT = {"team": "Moving to Team", "business": "Moving to Business",
                 "billing": "Billing", "help": "Help"}
CONTACT_PROMPT = {"team": "How many people, and when you would like to switch.",
                  "business": "How many people, and when you would like to switch.",
                  "billing": "What would you like to know about billing?",
                  "help": "What happened, or what you need. Name the page or agent if there"
                          " is one."}
CONTACT_LINE = {"team": "Moving {ws} to Team.", "business": "Moving {ws} to Business.",
                "billing": "Billing for {ws}.", "help": "Help with {ws}."}
# The address we publish for help (Forrest, 2026-09-29): an alias of his inbox, so it
# can move to someone else without changing a page. Messages from the forms still go to
# mail.contact_to().
SUPPORT_EMAIL = "support@dexio.wiki"
DOCS_URL = "https://dexio.wiki/docs"


def _help(ws: dict | None, email: str) -> str:
    """Help: write to us from inside the app, open to every member rather than
    owners only (it posts to /settings/contact as about=help), plus the address and
    the docs. Added 2026-09-29 after a customer had to ask his agent how to reach us
    and was told Dexio had no support address."""
    action = "/settings/contact" + (_in(ws) if ws else "")
    return f"""<div class="panel"><h2>Write to us</h2>
      <form method="post" action="{action}" class="plain hform">
        <input type="hidden" name="about" value="help">
        <label for="hmsg">Message</label>
        <textarea id="hmsg" name="message" rows="6" maxlength="5000" required
          placeholder="{e(CONTACT_PROMPT['help'])}"></textarea>
        <button type="submit">Send message</button>
        <p class="muted">We reply to <b>{e(email)}</b>.</p>
      </form></div>
    <div class="panel"><h2>Other ways</h2>
      <p>Email <a href="mailto:{SUPPORT_EMAIL}">{SUPPORT_EMAIL}</a>, or read the
        <a href="{DOCS_URL}">docs</a>.</p>
      <p class="muted">To have something deleted for good, including a page's earlier
        versions, tell us which pages. We delete them and confirm by email.</p></div>"""

# The plans side by side, in the pricing page's words (dexio-www src/pages/pricing.astro),
# shortened. Keep the two in step.
PLAN_CARDS = (
    ("free", ("Unlimited agents and pages", "One member", "Every change kept",
              "100 MB of storage")),
    ("team", ("Everything in Free", "Unlimited members", "1 GB of storage per member")),
    ("business", ("Everything in Team", "10 GB of storage per member")),
)


def _day(ts) -> str:
    t = time.gmtime(float(ts))
    return f'{time.strftime("%B", t)} {t.tm_mday}, {t.tm_year}'


def _people(n: int) -> str:
    return f'{n} member{"s" if n != 1 else ""}'


def _portal(ws: dict, label: str, alt: bool = False) -> str:
    """A button into Stripe's billing portal (card and invoices). Moving to Free
    is done in the app, where it can require the owner to be the only member."""
    cls = ' class="alt"' if alt else ""
    return (f'<form method="post" action="/billing/portal{_in(ws)}" class="inline">'
            f'<button type="submit"{cls}>{e(label)}</button></form>')


def _keep(ws: dict, name: str) -> str:
    """Undo a scheduled move to Free."""
    return (f'<form method="post" action="/billing/keep{_in(ws)}" class="inline">'
            f'<button type="submit">Keep {e(name)}</button></form>')


def _plan(ws: dict, owner: bool, b: dict, owners: list[str], email: str = "") -> str:
    """What the workspace is on and what it costs, then every plan side by side.
    `b` is billing_view: enabled, for_sale, customer, subscription, status,
    seats, period_end, ends_at. `email` is the signed-in person's, for the
    contact form (replies go there)."""
    if not b.get("enabled"):
        # No Stripe key: a self-hosted copy. There are no plans to show or limits to
        # meet (db.plans_apply), so the section says that and nothing else.
        return ('<div class="panel"><div class="phead"><h2>Plan</h2></div>'
                '<p class="muted">This server does not sell plans, so its workspaces have no'
                ' member or storage limits.</p></div>')
    plan = ws.get("plan") or "free"
    name = plan.title()
    seats = max(1, int(b.get("seats") or 1))
    billed = plan in PRICES and bool(b.get("subscription"))     # paying through Stripe
    failed = billed and b.get("status") in ("past_due", "unpaid")
    ends = b.get("ends_at") if billed else None
    manage = owner and billed and bool(b.get("customer"))
    out = []

    # Anything that needs doing comes first.
    if failed:
        out.append(f'<div class="alert bad"><div><b>The last payment for {e(name)} failed.</b>'
                   f' Stripe tries again over the next few days. Update the card to keep'
                   f' {e(name)}.</div>{_portal(ws, "Update card") if manage else ""}</div>')
    elif ends:
        out.append(f'<div class="alert"><div><b>{e(name)} ends on {_day(ends)}.</b> After that'
                   f' this workspace moves to Free.</div>'
                   f'{_keep(ws, name) if manage else ""}</div>')

    # The plan it is on now. Free has nothing to add to its card below.
    facts: list[tuple[str, str]] = []
    pill = price = detail = ""
    if billed:
        per = PRICES[plan]
        pill = ('<span class="pill bad">Payment failed</span>' if failed else
                '<span class="pill warn">Cancelled</span>' if ends else
                '<span class="pill ok">Active</span>')
        price = f'<div class="price-now"><b>${per * seats:,}</b> a month</div>'
        detail = f"{_people(seats)} at ${per} each."
        facts.append(("Members", str(seats)))
        if ends:
            facts.append(("Ends", _day(ends)))
        elif b.get("period_end") and not failed:
            facts.append(("Next payment", _day(b["period_end"])))
        facts.append(("Billed", "Monthly, through Stripe"))
    elif plan != "free":    # arranged by hand: Enterprise, or a paid plan with no subscription
        pill, price = '<span class="pill">Arranged with Dexio</span>', ""
        detail = "No card on file; billing for this workspace is arranged with Dexio."
        if owner and plan in PRICES and b.get("enabled"):
            detail += " To pay by card instead, choose a plan below."
        if owner:
            detail += (f' Questions? <a href="{_contact_href(ws, "billing")}" data-contact="billing"'
                       f'>Contact us</a>.')
        facts.append(("Members", str(seats)))
    rows = "".join(f"<div><dt>{e(k)}</dt><dd>{e(v)}</dd></div>" for k, v in facts)
    if manage:
        acts = (f'<div class="plan-acts">{_portal(ws, "Manage billing", alt=True)}'
                f'<span class="muted">Card and invoices, in Stripe. To move to Free, see Free'
                f' below.</span></div>')
    elif not owner:
        who = f": {e(', '.join(owners))}" if owners else ""
        acts = f'<p class="muted">Only owners change the plan{who}.</p>'
    else:
        acts = ""
    if plan != "free":
        out.append(f"""<div class="panel plan-now"><div class="phead"><h2>{e(name)}</h2>{pill}</div>
      {price}<p class="muted">{detail}</p>{f'<dl class="facts">{rows}</dl>' if rows else ""}
      {acts}</div>""")
    elif acts:
        out.append(acts)

    # Every plan side by side.
    cards = []
    for key, points in PLAN_CARDS:
        here = key == plan
        cost = "<b>$0</b>" if key == "free" else f"<b>${PRICES[key]}</b> per member a month"
        items = "".join(f"<li>{e(p)}</li>" for p in points)
        cards.append(f"""<div class="pcard{' here' if here else ''}">
        <div class="pc-top"><h3>{key.title()}</h3>{'<span class="pill">Current</span>' if here else ''}</div>
        <div class="pc-price">{cost}</div><ul>{items}</ul>
        <div class="pc-cta">{_plan_cta(ws, key, plan, owner, billed, b, seats)}</div></div>""")
    out.append(f"""<div class="panel"><div class="phead"><h2>Plans</h2>
      <a href="{PRICING_URL}" target="_blank" rel="noopener">Full comparison</a></div>
      <div class="pcards">{''.join(cards)}</div>
      <p class="muted">Billed monthly per member, in US dollars.</p>
      </div>""")
    if owner:
        out.append(_contact_dialog(ws, email, b.get("contact", "")))
    return "".join(out)


def _plan_cta(ws: dict, key: str, plan: str, owner: bool, billed: bool, b: dict,
              seats: int) -> str:
    """What a plan's card offers the owner: nothing on the current plan; from
    Free, Checkout; from a paid plan, a switch on the same subscription. A Team
    or Business plan set by hand (no subscription) is self-serve too (Forrest
    2026-09-27: "why is this not automated?"): Checkout for the other plan, and
    Free at once, since nothing is being paid. Only Enterprise, a contract, asks."""
    if key == plan or not owner or not b.get("enabled"):
        return ""
    arranged = plan in PRICES and not billed
    if key == "free":
        if arranged:
            why = b.get("free_blocker") or ""
            if why:
                return (f'<p class="muted">{e(why)}</p>'
                        f'<a class="button alt" href="/settings/members{_in(ws)}">Go to Members</a>')
            ask = (f"Move {ws['name']} to Free now? It is for one person, with 100 MB of storage.")
            return (f'<p class="muted">Nothing is being paid, so this takes effect now.</p>'
                    f'<form method="post" action="/billing/free{_in(ws)}" class="inline"'
                    f'{_confirm(ask)}><button type="submit" class="alt">Move to Free</button></form>')
        if not billed:
            return ""
        if b.get("ends_at"):
            return f'<p class="muted">Moving to Free on {_day(b["ends_at"])}.</p>'
        why = b.get("free_blocker") or ""
        if why:
            return (f'<p class="muted">{e(why)}</p>'
                    f'<a class="button alt" href="/settings/members{_in(ws)}">Go to Members</a>')
        until = f" on {_day(b['period_end'])}" if b.get("period_end") else ""
        ask = (f"Move {ws['name']} to Free? {plan.title()} stays until the end of the month you"
               f" paid for{until}, then stops. Nothing more is charged.")
        return (f'<p class="muted">{plan.title()} stays until the end of the month you paid'
                f' for.</p>'
                f'<form method="post" action="/billing/free{_in(ws)}" class="inline"'
                f'{_confirm(ask)}><button type="submit" class="alt">Move to Free</button></form>')
    per = PRICES[key]
    if (plan == "free" or arranged) and key in (b.get("for_sale") or ()):
        then = (", by card. The arrangement ends when Stripe confirms the payment."
                if arranged else f" now; each person who joins adds ${per}.")
        return (f'<p class="muted">${per * seats:,} a month for {_people(seats)}{then}</p>'
                f'<form method="post" action="/billing/checkout{_in(ws)}" class="inline">'
                f'<input type="hidden" name="plan" value="{key}">'
                f'<button type="submit">{"Choose" if arranged else "Upgrade to"} {key.title()}'
                f'</button></form>')
    if billed and plan in PRICES:
        if b.get("status") in ("past_due", "unpaid"):
            return '<p class="muted">Update the card first, under Manage billing.</p>'
        up = PRICES[key] > PRICES[plan]
        if up:
            ask = (f"Move {ws['name']} to {key.title()}? It becomes ${per * seats:,} a month, and"
                   f" Stripe charges the difference for the rest of this month now.")
            note = (f"${per * seats:,} a month for {_people(seats)}. The rest of this month"
                    f" is charged now, prorated.")
        else:
            ask = (f"Move {ws['name']} to {key.title()}? {plan.title()} features stop now, and"
                   f" the unused part of this month comes off your next invoice.")
            note = f"${per * seats:,} a month for {_people(seats)}."
        cls = "" if up else ' class="alt"'
        return (f'<p class="muted">{note}</p>'
                f'<form method="post" action="/billing/change{_in(ws)}" class="inline"'
                f'{_confirm(ask)}><input type="hidden" name="plan" value="{key}">'
                f'<button type="submit"{cls}>{"Upgrade" if up else "Switch"} to {key.title()}'
                f'</button></form>')
    return f'<a class="button alt" href="{_contact_href(ws, key)}" data-contact="{key}">Contact us</a>'


def _contact_href(ws: dict, about: str) -> str:
    """Without JavaScript the link reloads Plan with the form already open."""
    return f"/settings/plan?w={ws['handle']}&contact={about}#contact"


def _contact_dialog(ws: dict, email: str, about: str = "") -> str:
    """The Plan page's contact form, in a dialog the Contact us buttons open. It
    posts to /settings/contact, which emails us with Reply-To set to `email`.
    Replaced mailto links on 2026-09-27: they did nothing without a mail app."""
    about = about if about in CONTACT_ABOUT else ""
    key = about or "billing"
    lines = {k: CONTACT_LINE[k].format(ws=ws["name"]) for k in CONTACT_ABOUT}
    # Into a script: json.dumps, then no "</" can close the script element early.
    labels = json.dumps({k: [lines[k], CONTACT_PROMPT[k]] for k in CONTACT_ABOUT}).replace("</", "<\\/")
    return f"""<dialog id="contact" class="cdlg"{" open" if about else ""}
      aria-labelledby="contact-title">
    <form method="post" action="/settings/contact{_in(ws)}" class="cform">
      <div class="phead"><h2 id="contact-title">Contact Dexio</h2>
        <button type="button" class="cx" data-close aria-label="Close">&times;</button></div>
      <p class="muted"><b class="cabout">{e(lines[key])}</b> We reply to <b>{e(email)}</b>.</p>
      <input type="hidden" name="about" value="{e(key)}">
      <label for="cmsg">Message</label>
      <textarea id="cmsg" name="message" rows="6" maxlength="5000" required
        placeholder="{e(CONTACT_PROMPT[key])}"></textarea>
      <button type="submit">Send message</button>
    </form>
  </dialog>
  <script>
  (function () {{
    var d = document.getElementById("contact"), L = {labels};
    if (!d || typeof d.showModal !== "function") return;
    var f = d.querySelector("form"), about = f.elements.about, msg = f.elements.message;
    if (d.hasAttribute("open")) {{ d.removeAttribute("open"); d.showModal(); msg.focus(); }}
    document.addEventListener("click", function (ev) {{
      var a = ev.target.closest && ev.target.closest("[data-contact]");
      if (!a || ev.metaKey || ev.ctrlKey || ev.shiftKey) return;
      ev.preventDefault();
      var k = L[a.dataset.contact] ? a.dataset.contact : "billing";
      about.value = k;
      d.querySelector(".cabout").textContent = L[k][0];
      msg.placeholder = L[k][1];
      d.showModal();
      msg.focus();
    }});
    d.querySelectorAll("[data-close]").forEach(function (b) {{
      b.addEventListener("click", function () {{ d.close(); }});
    }});
    d.addEventListener("click", function (ev) {{
      var r = d.getBoundingClientRect();
      if (ev.target === d && (ev.clientX < r.left || ev.clientX > r.right ||
                              ev.clientY < r.top || ev.clientY > r.bottom)) d.close();
    }});
  }})();
  </script>"""


WORKSPACE_FIELD_CSS = """<style>
  .wsf { min-width:0; margin:0 0 1rem; padding:0; border:0; }
  .wsf legend { padding:0; margin-bottom:.35rem; color:var(--muted); font-size:.78rem;
                letter-spacing:.06em; text-transform:uppercase; }
  .wsf-list { display:flex; flex-direction:column; gap:6px; max-height:264px; overflow:auto; }
  .wsf-opt { position:relative; display:flex; align-items:center; gap:10px; min-height:44px;
    margin:0; padding:0 12px; border:1px solid var(--line); border-radius:8px;
    background:var(--field); color:var(--text); font-size:15px; letter-spacing:0;
    text-transform:none; cursor:pointer; transition:border-color .12s ease, background-color .12s ease; }
  .wsf-opt:hover { border-color:var(--accent); }
  .wsf-opt:has(input:checked) { border-color:var(--accent);
    background:color-mix(in srgb,var(--accent) 8%,var(--field)); }
  .wsf-opt:has(input:focus-visible) {
    box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent); }
  .wsf-opt input { position:absolute; width:1px; height:1px; min-height:0; margin:0; padding:0;
    opacity:0; pointer-events:none; }
  .wsf-tile { flex:none; display:inline-grid; place-items:center; width:22px; height:22px;
    border-radius:6px; background:hsl(var(--h) 45% 45%); color:var(--btn-text); font-size:12px;
    font-weight:700; line-height:1; }
  .wsf-name { flex:1 1 auto; min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .wsf-opt:has(input:checked) .wsf-name { font-weight:600; }
  .wsf-check { flex:none; width:16px; height:16px; fill:none; stroke:var(--accent); stroke-width:2;
    stroke-linecap:round; stroke-linejoin:round; visibility:hidden; }
  .wsf-opt:has(input:checked) .wsf-check { visibility:visible; }
</style>"""


def _workspace_field(workspaces: list[dict], current: int) -> str:
    """The workspace an approval is for, asked only when there is a choice: one row
    per workspace, the current one checked. Rows, not a native select (Forrest,
    2026-09-28: "as a standing rule we should not use native browser dropdowns"),
    and with a handful of workspaces nothing needs to open to show them all."""
    if len(workspaces) == 1:
        return f'<input type="hidden" name="workspace" value="{e(workspaces[0]["handle"])}">'
    if not any(w["id"] == current for w in workspaces):
        current = workspaces[0]["id"]
    check = ('<svg class="wsf-check" viewBox="0 0 16 16" aria-hidden="true">'
             '<path d="M3.5 8.4 6.6 11.4 12.5 4.8"></path></svg>')
    rows = "".join(
        f'<label class="wsf-opt"><input type="radio" name="workspace" value="{e(w["handle"])}"'
        f'{" checked" if w["id"] == current else ""}>{ws_tile(w, "wsf-tile")}'
        f'<span class="wsf-name">{e(w["name"])}</span>{check}</label>' for w in workspaces)
    return (f'{WORKSPACE_FIELD_CSS}<fieldset class="wsf"><legend>Which workspace?</legend>'
            f'<div class="wsf-list">{rows}</div></fieldset>')


def consent_page(client_name: str, redirect_host: str, email: str, workspaces: list[dict],
                 current: int, req: str) -> str:
    body = f"""<form method="post" action="/oauth/consent">
      <p class="who"><b>{e(client_name)}</b> wants to read and write your Dexio notes. After you
        allow it, you go back to <b>{e(redirect_host)}</b>.</p>
      <p class="who">Signed in as <b>{e(email)}</b></p>
      <input type="hidden" name="req" value="{e(req)}">
      {_workspace_field(workspaces, current)}
      <button type="submit" name="action" value="allow">Allow access</button>
      <p></p>
      <button type="submit" name="action" value="deny" class="quiet"
        style="width:100%;min-height:44px;font-size:15px">Deny</button>
    </form>"""
    foot = 'You can disconnect it any time in <a href="/settings/agents">Settings</a>.'
    return _page("Allow access", body, foot, width=400)


# ---- agent sign-in (device flow) -------------------------------------------
AGENT_NOTE = ("Your agent is waiting to connect to Dexio. Sign in, or create a free "
              "account, to continue.")
SHARE_NOTE = ("Someone shared something with you on Dexio. Sign in, or create a free "
              "account, with the address the email came to.")


# ---- sign in with Google or GitHub -------------------------------------------
GOOGLE_G = ('<svg viewBox="0 0 48 48" aria-hidden="true">'
            '<path fill="#EA4335" d="M24 9.5c3.54 0 6.71 1.22 9.21 3.6l6.85-6.85C35.9 2.38 30.47 0'
            ' 24 0 14.62 0 6.51 5.38 2.56 13.22l7.98 6.19C12.43 13.72 17.74 9.5 24 9.5z"/>'
            '<path fill="#4285F4" d="M46.98 24.55c0-1.57-.15-3.09-.38-4.55H24v9.02h12.94c-.58'
            ' 2.96-2.26 5.48-4.78 7.18l7.73 6c4.51-4.18 7.09-10.36 7.09-17.65z"/>'
            '<path fill="#FBBC05" d="M10.53 28.59c-.48-1.45-.76-2.99-.76-4.59s.27-3.14.76-4.59'
            'l-7.98-6.19C.92 16.46 0 20.12 0 24c0 3.88.92 7.54 2.56 10.78l7.97-6.19z"/>'
            '<path fill="#34A853" d="M24 48c6.48 0 11.93-2.13 15.89-5.81l-7.73-6c-2.15 1.45-4.92'
            ' 2.3-8.16 2.3-6.26 0-11.57-4.22-13.47-9.91l-7.98 6.19C6.51 42.62 14.62 48 24 48z"/>'
            '</svg>')
GITHUB_MARK = ('<svg viewBox="0 0 16 16" aria-hidden="true" fill="currentColor"><path d="M8 0C3.58'
               ' 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49'
               '-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01'
               ' 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89'
               '-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18'
               ' 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12'
               '.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01'
               ' 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z"/></svg>')


def social_block(providers: list[str], next_url: str = "/",
                 terms_url: str = "https://dexio.wiki/terms",
                 privacy_url: str = "https://dexio.wiki/privacy", terms: bool = True) -> str:
    """Continue with Google / GitHub buttons for the sign-in and signup forms, or
    nothing when neither provider is configured."""
    if not providers:
        return ""
    nxt = e(quote(next_url or "/", safe="/"))
    icons = {"google": GOOGLE_G, "github": GITHUB_MARK}
    labels = {"google": "Google", "github": "GitHub"}
    buttons = "".join(
        f'<a class="social" href="/auth/start?provider={p}&amp;next={nxt}">{icons[p]}'
        f'Continue with {labels[p]}</a>' for p in providers)
    if not terms:
        return buttons
    return (f'{buttons}<p class="terms">By continuing with {" or ".join(labels[p] for p in providers)}'
            f' you agree to the <a href="{e(terms_url)}">Terms</a> and the'
            f' <a href="{e(privacy_url)}">Privacy Policy</a>.</p><div class="or">or</div>')


def device_entry_page(error: str = "", code: str = "") -> str:
    body = f"""<form method="get" action="/device">
      {_message(error)}
      <p class="who">Enter the code your agent showed you.</p>
      <label for="code">Code</label>
      <input id="code" name="code" value="{e(code)}" autocomplete="off" autocapitalize="characters"
             spellcheck="false" placeholder="ABCD-EFGH" required autofocus>
      <button type="submit">Continue</button>
    </form>"""
    return _page("Connect your agent", body, '<a href="/">Back to Dexio</a>', width=360)


def device_page(client_name: str, code: str, email: str, workspaces: list[dict],
                current: int) -> str:
    body = f"""<form method="post" action="/device">
      <p class="who"><b>{e(client_name)}</b> wants to read and write your Dexio notes.</p>
      <p class="who">Your agent should show this same code:</p>
      <pre><code style="font-size:1.4rem;letter-spacing:.12em">{e(code)}</code></pre>
      <p class="muted">Only allow it if you just asked your agent to connect to Dexio.</p>
      <p class="who">Signed in as <b>{e(email)}</b></p>
      <input type="hidden" name="code" value="{e(code)}">
      {_workspace_field(workspaces, current)}
      <button type="submit" name="action" value="allow">Allow access</button>
      <p></p>
      <button type="submit" name="action" value="deny" class="quiet"
        style="width:100%;min-height:44px;font-size:15px">Deny</button>
    </form>"""
    foot = 'You can disconnect it any time in <a href="/settings/agents">Settings</a>.'
    return _page("Connect your agent", body, foot, width=400)


def delete_account_panel(plan: dict) -> str:
    """The Delete account panel in Settings > Profile: what goes, what stays, and
    why it cannot happen yet when a paid plan is in the way."""
    gone = "".join(f"<li>\u201c{e(w['name'])}\u201d and everything in it: its wiki, page "
                   "history and files. Agents connected to it stop working.</li>"
                   for w in plan["gone"])
    left = "".join(f"<li>You leave \u201c{e(w['name'])}\u201d. Its pages stay with its other "
                   "members.</li>" for w in plan["leave"])
    head = """<div class="panel" id="delete"><h2>Delete your account</h2>"""
    if plan["blockers"]:
        why = "".join(f"<li>{e(b)}</li>" for b in plan["blockers"])
        return f"""{head}
      <p class="who">Your account cannot be deleted yet:</p><ul>{why}</ul></div>"""
    return f"""{head}
      <p class="who">This deletes your account and signs you out. It cannot be undone.</p>
      <ul>{gone}{left}</ul>
      <form method="post" action="/account/delete" class="plain">
        <label for="confirm_email">Type your email to confirm</label>
        <input id="confirm_email" name="confirm_email" type="email" autocomplete="off" required>
        <button type="submit" class="danger">Delete my account</button>
      </form></div>"""


def forgot_page(message: str = "", error: str = "", email: str = "") -> str:
    body = f"""<form method="post" action="/forgot">
      {_message(error, message)}
      <p class="who">Enter your account's email and we will send a link to choose a new
        password.</p>
      <label for="email">Email</label>
      <input id="email" name="email" type="email" autocomplete="username" inputmode="email"
             required autofocus value="{e(email)}">
      <button type="submit">Send reset link</button>
    </form>"""
    return _page("Reset password", body, '<a href="/login">Back to sign in</a>', width=360)


def reset_page(token: str, email: str, error: str = "") -> str:
    body = f"""<form method="post" action="/reset">
      {_message(error)}
      <p class="who">New password for <b>{e(email)}</b></p>
      <input type="hidden" name="token" value="{e(token)}">
      <input type="email" name="username" autocomplete="username" value="{e(email)}" hidden>
      <label for="new">New password</label>
      <input id="new" name="new_password" type="password" autocomplete="new-password"
             minlength="12" required autofocus>
      <label for="confirm">Confirm new password</label>
      <input id="confirm" name="confirm_password" type="password" autocomplete="new-password"
             minlength="12" required>
      <button type="submit">Set password</button>
    </form>"""
    return _page("Choose a new password", body,
                 "At least 12 characters. Setting it signs out every other browser.", width=360)


def desktop_handoff_page(link: str) -> str:
    """The browser's last page of a sign-in the desktop app started: it opens the
    app through its dexio:// link, and the button does the same if the browser
    asked first or the person dismissed it. The meta refresh is honoured in the body."""
    body = f"""<meta http-equiv="refresh" content="0;url={e(link)}">
    <div class="panel"><div class="ok" role="status">You're signed in. Opening the Dexio app.</div>
      <p>If it doesn't open, use the button. You can close this tab afterwards.</p>
      <a class="button" href="{e(link)}">Open Dexio</a></div>"""
    return _page("Open Dexio", body, width=420)


def notice_page(title: str, message: str, link: str = "/", link_text: str = "Continue",
                status_error: bool = True) -> str:
    body = f"""<div class="panel">{_message(message if status_error else "",
                                            "" if status_error else message)}
      <a href="{e(link)}">{e(link_text)}</a></div>"""
    return _page(title, body, width=420)


# ---- Make a copy (copies.py) --------------------------------------------------
COPY_NOTE = ("Someone listed this on Dexio for anyone to copy. Sign in, or create a free "
             "account, and the copy goes into a workspace of yours.")
COPY_CSS = """<style>
  .cp-what { margin:0 0 1.1rem; padding:12px 14px; border:1px solid var(--line); border-radius:8px; }
  .cp-what b { display:block; font-size:16px; }
  .cp-what span { display:block; margin-top:2px; color:var(--muted); font-size:13.5px; }
  .cp-about { margin-top:6px !important; color:var(--text) !important; font-size:14px !important; }
  .wsf-opt.wsf-off { cursor:default; opacity:.55; }
  .wsf-opt.wsf-off:hover { border-color:var(--line); }
  .wsf-meta { flex:none; color:var(--muted); font-size:13px; }
  .wsf-tile.wsf-new { background:var(--sunken); color:var(--muted); font-size:15px;
    box-shadow:inset 0 0 0 1px var(--line); }
</style>"""


def copy_page(info: dict, email: str, dests: list[dict], from_id: int, *,
              choose: str = "", error: str = "") -> str:
    """Where to put a copy: one row per workspace the person is in (radio rows,
    not a native select, by the standing rule), then a new workspace. Picked
    first: the one they named, else an empty workspace of theirs, else a new one."""
    check = ('<svg class="wsf-check" viewBox="0 0 16 16" aria-hidden="true">'
             '<path d="M3.5 8.4 6.6 11.4 12.5 4.8"></path></svg>')
    usable = [w for w in dests if not w["blocked"]]
    if choose == "new" or choose in {w["handle"] for w in usable}:
        pick = choose
    else:
        pick = next((w["handle"] for w in usable if not w["pages"]), "new")
    rows = ""
    for w in dests:
        meta = "Read-only now" if w["blocked"] else (
            "Empty" if not w["pages"] else f'{w["pages"]:,} page{"s" if w["pages"] != 1 else ""}')
        off = " disabled" if w["blocked"] else ""
        rows += (f'<label class="wsf-opt{" wsf-off" if off else ""}"><input type="radio" name="to"'
                 f' value="{e(w["handle"])}"{" checked" if w["handle"] == pick else ""}{off}>'
                 f'{ws_tile(w, "wsf-tile")}<span class="wsf-name">{e(w["name"])}</span>'
                 f'<span class="wsf-meta">{e(meta)}</span>{check}</label>')
    rows += (f'<label class="wsf-opt"><input type="radio" name="to" value="new"'
             f'{" checked" if pick == "new" else ""}><span class="wsf-tile wsf-new">+</span>'
             f'<span class="wsf-name">A new workspace, {e(info["title"])}</span>{check}</label>')
    n = int(info.get("pages") or 0)
    size = (f'by {info["author"]} · ' if info.get("author") else "") + (
        f'{n:,} page{"s" if n != 1 else ""}')
    about = f'<span class="cp-about">{e(info["description"])}</span>' if info.get("description") else ""
    body = f"""{WORKSPACE_FIELD_CSS}{COPY_CSS}<form method="post" action="/copy">
      {_message(error)}
      <div class="cp-what"><b>{e(info["title"])}</b><span>{e(size)}</span>{about}</div>
      <input type="hidden" name="from" value="{int(from_id)}">
      <fieldset class="wsf"><legend>Copy into</legend><div class="wsf-list">{rows}</div></fieldset>
      <p class="who">Pages keep their paths. A page that is already there is left as it is.
        Signed in as <b>{e(email)}</b>.</p>
      <button type="submit">Make a copy</button>
    </form>"""
    foot = (f'<a href="{e(info["url"])}">Back to {e(info["title"])}</a>' if info.get("url") else "")
    return _page("Make a copy", body, foot, width=440)
