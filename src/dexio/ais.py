"""The AIs Dexio connects, as people pick them: each one's mark, and the
picker's styles. The picker shows in two places, the empty wiki's steps on the
graph screen (render.SWITCHER_JS, connectFlow) and Settings > Agents
(server.pages._connect_panel); both draw it from here so it looks the same in
each (Forrest, 2026-09-27: improve it everywhere it appears).

The marks are the products' own: OpenAI's and Claude's from Simple Icons
(CC0; the trademarks stay their owners'), OpenClaw's lobster from openclaw.ai,
and Hermes's portrait from Nous Research's Hermes Agent docs, a 64px PNG because
the drawing has no vector form. Muse's scribble (its Google Play icon) and Grok's
mark (grok.com's apple-touch-icon, corners filled black) are 64px PNGs too
(2026-09-28). Claude Code gets a terminal prompt in Claude's
orange rather than a second copy of Claude's mark, so the two tiles differ at a
glance. Something else gets a plain ellipsis."""
from __future__ import annotations

import html

_OPENAI = "M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.504 4.504 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z"
_CLAUDE = "m4.7144 15.9555 4.7174-2.6471.079-.2307-.079-.1275h-.2307l-.7893-.0486-2.6956-.0729-2.3375-.0971-2.2646-.1214-.5707-.1215-.5343-.7042.0546-.3522.4797-.3218.686.0608 1.5179.1032 2.2767.1578 1.6514.0972 2.4468.255h.3886l.0546-.1579-.1336-.0971-.1032-.0972L6.973 9.8356l-2.55-1.6879-1.3356-.9714-.7225-.4918-.3643-.4614-.1578-1.0078.6557-.7225.8803.0607.2246.0607.8925.686 1.9064 1.4754 2.4893 1.8336.3643.3035.1457-.1032.0182-.0728-.164-.2733-1.3539-2.4467-1.445-2.4893-.6435-1.032-.17-.6194c-.0607-.255-.1032-.4674-.1032-.7285L6.287.1335 6.6997 0l.9957.1336.419.3642.6192 1.4147 1.0018 2.2282 1.5543 3.0296.4553.8985.2429.8318.091.255h.1579v-.1457l.1275-1.706.2368-2.0947.2307-2.6957.0789-.7589.3764-.9107.7468-.4918.5828.2793.4797.686-.0668.4433-.2853 1.8517-.5586 2.9021-.3643 1.9429h.2125l.2429-.2429.9835-1.3053 1.6514-2.0643.7286-.8196.85-.9046.5464-.4311h1.0321l.759 1.1293-.34 1.1657-1.0625 1.3478-.8804 1.1414-1.2628 1.7-.7893 1.36.0729.1093.1882-.0183 2.8535-.607 1.5421-.2794 1.8396-.3157.8318.3886.091.3946-.3278.8075-1.967.4857-2.3072.4614-3.4364.8136-.0425.0304.0486.0607 1.5482.1457.6618.0364h1.621l3.0175.2247.7892.522.4736.6376-.079.4857-1.2142.6193-1.6393-.3886-3.825-.9107-1.3113-.3279h-.1822v.1093l1.0929 1.0686 2.0035 1.8092 2.5075 2.3314.1275.5768-.3218.4554-.34-.0486-2.2039-1.6575-.85-.7468-1.9246-1.621h-.1275v.17l.4432.6496 2.3436 3.5214.1214 1.0807-.17.3521-.6071.2125-.6679-.1214-1.3721-1.9246L14.38 17.959l-1.1414-1.9428-.1397.079-.674 7.2552-.3156.3703-.7286.2793-.6071-.4614-.3218-.7468.3218-1.4753.3886-1.9246.3157-1.53.2853-1.9004.17-.6314-.0121-.0425-.1397.0182-1.4328 1.9672-2.1796 2.9446-1.7243 1.8456-.4128.164-.7164-.3704.0667-.6618.4008-.5889 2.386-3.0357 1.4389-1.882.929-1.0868-.0062-.1579h-.0546l-6.3385 4.1164-1.1293.1457-.4857-.4554.0608-.7467.2307-.2429 1.9064-1.3114Z"
_HERMES = "data:image/png;base64,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"

_MUSE = "data:image/png;base64,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"
_GROK = "data:image/png;base64,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"

# Each mark is a whole app-icon square, its background drawn in, so the colours
# stay the product's in every theme and none sit in the stylesheet (colours
# there are theme variables; tests/test_ui.py). A 24-unit glyph fills five
# eighths of its square; the lobster is drawn on 120 units.
_SQ = '<svg viewBox="-7.2 -7.2 38.4 38.4"><rect x="-7.2" y="-7.2" width="38.4" height="38.4" fill="{bg}"/>'

ICONS = {
    "chatgpt": _SQ.format(bg="#0d0d0d") + f'<path fill="#fff" d="{_OPENAI}"/></svg>',
    "claude": _SQ.format(bg="#f4f1ea") + f'<path fill="#d97757" d="{_CLAUDE}"/></svg>',
    "claude-code": (_SQ.format(bg="#262624") + '<path d="M5.5 7.5 10 12l-4.5 4.5M12.5 17h6" '
                    'fill="none" stroke="#d97757" stroke-width="2.4" stroke-linecap="round" '
                    'stroke-linejoin="round"/></svg>'),
    "hermes": f'<img src="{_HERMES}" alt="" width="36" height="36">',
    "muse": f'<img src="{_MUSE}" alt="" width="36" height="36">',
    "grok-bot": f'<img src="{_GROK}" alt="" width="36" height="36">',
    "openclaw": ('<svg viewBox="-27 -27 174 174"><rect x="-27" y="-27" width="174" height="174" '
                 'fill="#0b0f1a"/>'
                 '<path d="M60 10C30 10 15 35 15 55s15 40 30 45v10h10v-10s5 2 10 0v10h10v-10'
                 'c15-5 30-25 30-45S90 10 60 10Z" fill="#e5484d"/>'
                 '<path d="M20 45C5 40 0 50 5 60s15 5 20-5c3-7 0-10-5-10Zm80 0c15-5 20 5 15 15'
                 's-15 5-20-5c-3-7 0-10 5-10Z" fill="#e5484d"/>'
                 '<path d="M45 15Q35 5 30 8m45 7q10-10 15-7" stroke="#ff6b6b" stroke-width="4" '
                 'stroke-linecap="round" fill="none"/>'
                 '<circle cx="45" cy="35" r="7" fill="#050810"/><circle cx="75" cy="35" r="7" '
                 'fill="#050810"/><circle cx="46.5" cy="33.5" r="3" fill="#00e5cc"/>'
                 '<circle cx="76.5" cy="33.5" r="3" fill="#00e5cc"/></svg>'),
    # no product to show: the theme's own sunken grey and muted dots
    "other": ('<svg viewBox="-7.2 -7.2 38.4 38.4" fill="currentColor"><circle cx="6" cy="12" '
              'r="1.8"/><circle cx="12" cy="12" r="1.8"/><circle cx="18" cy="12" r="1.8"/></svg>'),
}


def icon(client: str) -> str:
    """A client's mark in its square, or an empty string for one without."""
    mark = ICONS.get(client)
    return f'<span class="ai-icon" data-ai="{client}" aria-hidden="true">{mark}</span>' if mark else ""


def tile(client: str, name: str, sub: str) -> str:
    """What goes inside an AI's tile, the same in both places it shows: its
    mark, its name, the grey line under it and the tick shown once it is picked
    (the arrow is the tile's ::after).
    The name is a <b>, which the pickers' scripts read back for the steps' heading."""
    return (f'{icon(client)}<span class="ai-text"><b class="ai-name">{html.escape(name)}</b>'
            f'<span class="ai-sub">{html.escape(sub)}</span></span>{TICK}')


# Rows, two across, one on a phone (Forrest, 2026-09-27, option B of three: A was
# cards three across, C these rows split into chat apps and agents). Each row: the
# mark, the name with its grey line, and an arrow that becomes a tick once the AI is
# picked, when the row is also tinted and ringed in the accent. Columns are at
# least 232px, which gives two in the graph screen's card and in Settings and one
# on a phone, and keeps every grey line on one line. Pixel sizes, so the graph
# screen (14px) and Settings (15px) match.
CSS = """
  .ai-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(100%,232px),1fr));
    gap:8px; margin:0; }
  .ai-tile { position:relative; display:flex; align-items:center; gap:12px; width:100%;
    min-height:0; margin:0; padding:10px 34px 10px 10px; text-align:left; font:inherit;
    font-size:14px; line-height:1.3; color:var(--text); background:var(--panel);
    border:1px solid var(--line-strong); border-radius:10px; cursor:pointer;
    transition:border-color .12s ease, box-shadow .12s ease, background-color .12s ease; }
  .ai-tile::after { content:""; position:absolute; right:15px; top:50%; width:6px; height:6px;
    margin-top:-3px; border-top:1.6px solid var(--muted); border-right:1.6px solid var(--muted);
    transform:rotate(45deg); opacity:.75; }
  .ai-tile:hover { border-color:color-mix(in srgb,var(--accent) 55%,var(--line-strong));
    box-shadow:0 1px 2px rgba(0,0,0,.05),0 4px 12px rgba(0,0,0,.06); }
  .ai-tile[aria-pressed="true"] { border-color:var(--accent);
    background:color-mix(in srgb,var(--accent) 7%,var(--panel));
    box-shadow:inset 0 0 0 1px var(--accent); }
  .ai-tile[aria-pressed="true"]::after { display:none; }
  .ai-tile:focus-visible { outline:none; border-color:var(--accent);
    box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 30%,transparent); }
  .ai-icon { display:block; flex:none; width:36px; height:36px; border-radius:9px;
    overflow:hidden; background:var(--sunken); color:var(--muted);
    box-shadow:0 0 0 1px color-mix(in srgb,var(--text) 10%,transparent); }
  .ai-icon > svg, .ai-icon > img { display:block; width:100%; height:100%; }
  .ai-text { display:block; min-width:0; }
  .ai-tile .ai-name { display:block; font-weight:600; font-size:14px; }
  .ai-tile .ai-sub { display:block; margin-top:2px; color:var(--muted); font-weight:400;
    font-size:12.5px; line-height:1.35; }
  .ai-tick { position:absolute; right:11px; top:50%; margin-top:-9px; display:none;
    place-items:center; width:18px; height:18px; border-radius:50%; background:var(--accent);
    color:var(--panel); }
  .ai-tick svg { display:block; width:11px; height:11px; }
  .ai-tile[aria-pressed="true"] .ai-tick { display:grid; }
  @media (prefers-reduced-motion:reduce) { .ai-tile { transition:none; } }
"""

TICK = ('<span class="ai-tick" aria-hidden="true"><svg viewBox="0 0 12 12" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
        '<path d="m2.5 6.2 2.3 2.3 4.7-5"/></svg></span>')


# ---- the stepper ------------------------------------------------------------
# Connecting runs as three steps, one on screen at a time (Forrest, 2026-09-27:
# "the content gets rather long as you get the message and expand things"):
# Choose your agent (the tiles), Connect (how to add Dexio to it) and First page
# (the message that has it save one; the graph appears when it lands). Over them,
# a progress bar of three equal segments filled up to the current step, each
# step's name under its segment (Forrest, 2026-09-28, option A of three: B was a
# segmented track like tabs, C dots on a line). Once an agent is picked, step 1
# shows its name with a tick, and every step reached so far can be clicked to go
# back. The flow's data-at says which step shows, so the server can render a
# picked agent's steps (Settings' ?connect=) with no script. Both pickers use it:
# render.connectFlow on the graph screen, pages._connect_panel in Settings.
STEP_NAMES = ("Choose your agent", "Connect", "First page")
# the tick before a done step's name, drawn as a mask so it takes the accent colour
_TICK_MASK = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 12 12'"
              "%3E%3Cpath d='m2.5 6.2 2.3 2.3 4.7-5' fill='none' stroke='black' stroke-width='2' "
              "stroke-linecap='round' stroke-linejoin='round'/%3E%3C/svg%3E\") center/contain no-repeat")


def steps_head(at: int = 1, picked: str = "") -> str:
    """The progress bar over the flow; picked is the chosen agent's name. Each
    step's number is its label's data-k, shown before the name until it is done."""
    items = []
    for n, label in enumerate(STEP_NAMES, 1):
        if n == 1 and picked:
            label = picked
        state = ' aria-current="step"' if n == at else (" data-done" if n < at else "")
        off = "" if n <= at or picked else " disabled"
        items.append(f'<li data-n="{n}"{state}><button type="button" class="ai-step" '
                     f'data-go="{n}"{off}><span class="ai-bar"></span>'
                     f'<span class="ai-lab" data-k="{n}"><span class="ai-txt">{html.escape(label)}</span>'
                     f'</span></button></li>')
    return f'<ol class="ai-steps" aria-label="Steps">{"".join(items)}</ol>'


def panes(connect: str, check: str) -> str:
    """An agent's Connect and First page steps, each with Back and, on Connect, Next."""
    back = '<button type="button" class="ai-back" data-go="{n}">Back</button>'
    return (f'<div class="ai-pane" data-pane="2">{connect}<div class="ai-nav">'
            f'{back.format(n=1)}<button type="button" class="ai-next" data-go="3">Next: first page'
            f'</button></div></div>'
            f'<div class="ai-pane" data-pane="3">{check}<div class="ai-nav">{back.format(n=2)}'
            f'</div></div>')


CSS += """
  .ai-steps { display:flex; align-items:flex-start; gap:6px; margin:0 0 18px; padding:0;
    list-style:none; }
  .ai-steps li { display:flex; flex:1 1 0; min-width:0; margin:0; }
  .ai-step { display:flex; flex-direction:column; align-items:stretch; gap:8px; width:100%;
    min-width:0; min-height:0; margin:0; padding:0; border:0; border-radius:4px;
    background:none; box-shadow:none; font:inherit; font-size:12.5px; font-weight:600;
    line-height:1.2; color:var(--muted); text-align:left; cursor:pointer; }
  .ai-step:disabled { cursor:default; }
  .ai-step:not(:disabled):hover .ai-lab { text-decoration:underline; text-underline-offset:3px; }
  .ai-step:focus-visible { outline:none;
    box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 30%,transparent); }
  .ai-bar { display:block; height:4px; border-radius:999px; background:var(--line);
    transition:background-color .2s ease; }
  .ai-steps li[data-done] .ai-bar, .ai-steps li[aria-current] .ai-bar { background:var(--accent); }
  .ai-lab { display:flex; align-items:center; gap:5px; min-width:0; }
  .ai-txt { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
  .ai-lab::before { content:attr(data-k); flex:none; color:var(--muted); font-weight:500;
    font-variant-numeric:tabular-nums; }
  .ai-steps li[data-done] .ai-lab::before { content:""; width:12px; height:12px;
    background:var(--accent); -webkit-mask:TICK; mask:TICK; }
  .ai-steps li[aria-current] .ai-step { color:var(--text); }
  .ai-steps li[data-done] .ai-step { color:var(--text); font-weight:500; }
  .ai-pane { display:none; }
  .ai-flow[data-at="1"] .ai-pane[data-pane="1"], .ai-flow[data-at="2"] .ai-pane[data-pane="2"],
  .ai-flow[data-at="3"] .ai-pane[data-pane="3"] { display:block; animation:ai-in .16s ease-out; }
  @keyframes ai-in { from { opacity:0; transform:translateY(3px); } }
  .ai-pane > :first-child { margin-top:0; }
  .ai-nav { display:flex; align-items:center; justify-content:space-between; gap:12px;
    margin:18px 0 0; }
  /* Back and Next are the app's 38px buttons (login.py has the system, Forrest 2026-09-28) */
  .ai-nav button { min-height:38px; margin:0; padding:0 16px; border-radius:8px; font:inherit;
    font-size:14px; font-weight:600; cursor:pointer;
    transition:background-color .12s ease,border-color .12s ease,color .12s ease; }
  .ai-nav button:focus-visible { outline:2px solid var(--accent); outline-offset:2px;
    box-shadow:none; }
  .ai-back { color:var(--text); background:var(--panel); border:1px solid var(--btn-line); }
  .ai-back:hover { background:var(--btn-fill-hover); border-color:var(--btn-line-hover); }
  .ai-back:active { background:var(--btn-fill-press); }
  .ai-next { margin-left:auto; color:var(--btn-text); background:var(--btn);
    border:1px solid transparent; }
  .ai-next:hover { background:var(--btn-hover); }
  .ai-next:active { background:var(--btn-press); }
  @media (prefers-reduced-motion:reduce) {
    .ai-pane { animation:none !important; } .ai-bar { transition:none; } }
""".replace("TICK", _TICK_MASK)

# Moves between steps for any .ai-flow on the page: a click on anything with
# data-go (the progress bar, Back, Next). The pickers call dexioSteps.picked when
# an agent is chosen and dexioSteps.go to show a step.
# It also reports what only the browser sees while someone connects an agent
# (server/setup_events.py): window.dexioSetup(event, detail) for the screens to
# call, plus a step moved, a copy button, and the tab hidden, shown or closed while
# a setup screen is open. sendBeacon, so a closing tab still gets its line in.
STEPS_JS = r"""
(function () {
  if (window.dexioSteps) return;
  let sent = 0;
  function note(event, detail) {
    if (window.DEXIO_GUEST || sent >= 60) return;
    sent += 1;
    const box = document.getElementById("connect");
    const w = window.DEXIO_WORKSPACE || (box && box.dataset.ws) || "";
    const url = (window.DEXIO_API || "/api/v1") + "/setup/event?w=" + encodeURIComponent(w);
    const body = JSON.stringify({event: event, detail: detail || ""});
    try {
      if (navigator.sendBeacon &&
          navigator.sendBeacon(url, new Blob([body], {type: "application/json"}))) return;
      fetch(url, {method: "POST", keepalive: true, body: body,
                  headers: {"Content-Type": "application/json"}}).catch(() => {});
    } catch (err) {}
  }
  const inSetup = () => !!document.querySelector("#onboard, #connect .ai-flow");
  document.addEventListener("visibilitychange", () => {
    if (inSetup()) note(document.hidden ? "hidden" : "visible");
  });
  window.addEventListener("pagehide", () => { if (inSetup()) note("left"); });
  document.addEventListener("click", e => {
    const c = e.target.closest("[data-copy]");
    if (c && c.closest("#onboard, #connect")) note("copied", c.dataset.copy);
  });
  function go(flow, n) {
    if (n > 1 && !flow.dataset.client) return;
    if (flow.dataset.at !== String(n)) note("step", String(n));
    flow.dataset.at = String(n);
    for (const li of flow.querySelectorAll(".ai-steps > li")) {
      const k = Number(li.dataset.n);
      if (k === n) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
      li.toggleAttribute("data-done", k < n);
      li.querySelector(".ai-step").disabled = k > n && !flow.dataset.client;
    }
    const top = flow.getBoundingClientRect().top;
    if (top < 0) flow.scrollIntoView({block: "start"});
  }
  function picked(flow, id, name) {
    flow.dataset.client = id;
    const lab = flow.querySelector('.ai-steps li[data-n="1"] .ai-txt');
    if (lab) lab.textContent = name;
  }
  document.addEventListener("click", e => {
    const b = e.target.closest("[data-go]");
    const flow = b && b.closest(".ai-flow");
    if (!flow || b.disabled) return;
    go(flow, Number(b.dataset.go));
  });
  window.dexioSteps = {go, picked};
  window.dexioSetup = note;
})();
"""
