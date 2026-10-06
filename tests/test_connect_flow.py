"""No onboarding page (Forrest, 2026-09-27): the graph view shows "Connect your AI"
where the graph goes when the wiki is empty, with the steps right there on the graph
screen (render.APP_JS). A workspace comes with its one wiki (2026-09-28), so there
is nothing to name first. Connecting from anywhere else runs in Settings > Agents,
on the page (pages._connect_panel and CONNECT_JS). These cover the server side the
flows stand on and keep the old /welcome and /?connect links working."""
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.render import server_page  # noqa: E402
from dexio.server import db  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import browser, mcp, signup, ws_id  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    a = get_app(str(tmp_path / "c.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        yield a


def test_every_entry_lands_on_the_graph_view(app):
    c = browser(app)
    assert signup(c, "ann@example.com").headers["location"] == "/joined?next=%2F"  # then /
    assert c.get("/signup").headers["location"] == "/"          # already signed in
    assert c.get("/login").headers["location"] == "/"
    # the old first-run page: plain links open the graph, named AIs open the flow
    assert c.get("/welcome").headers["location"] == "/"
    assert c.get("/welcome?client=chatgpt").headers["location"] == "/?connect=chatgpt"
    assert c.get("/welcome?client=nope").headers["location"] == "/"
    # /?connect (the old flow over the graph) lands on Settings > Agents' panel
    ws = c.get("/api/v1/workspaces").json()["current"]
    r = c.get("/?connect=chatgpt", headers={"accept": "text/html"})
    assert r.status_code == 303
    assert r.headers["location"] == f"/settings/agents?w={ws}&connect=chatgpt"
    # one wiki per workspace: an old link's ?project= is ignored, not carried along
    r = c.get("/?project=main&connect", headers={"accept": "text/html"})
    assert r.headers["location"] == f"/settings/agents?w={ws}&connect="
    # signed out, ?connect survives the trip through sign-in
    r = browser(app).get("/?connect=claude", headers={"accept": "text/html"})
    assert r.headers["location"] == "/login?next=/%3Fconnect%3Dclaude"
    r = browser(app).get("/settings/agents?connect=claude")
    assert r.headers["location"] == "/login?next=/settings/agents%3Fconnect%3Dclaude"


def test_sign_up_and_sign_in_pages_never_name_an_onboarding_page(app):
    for path in ("/signup", "/login"):
        assert "welcome" not in app.state.live.get(path).text


def test_an_empty_wiki_connects_its_ai_on_the_graph_screen(app):
    """Forrest, 2026-09-27: when an empty wiki prompts people to connect an AI,
    they stay on the graph rather than going into Settings."""
    c = browser(app)
    signup(c, "ann@example.com")
    page = c.get("/").text
    clients = json.loads(re.search(r"window\.DEXIO_CLIENTS = (\[.*?\]);", page).group(1))
    # Forrest, 2026-09-27: agents first by popularity, then Claude and ChatGPT, then
    # Something else; 2026-09-28: Muse and Grok Bot after Hermes
    assert [x["id"] for x in clients] == ["claude-code", "openclaw", "hermes", "muse",
                                          "grok-bot", "claude", "chatgpt", "other"]
    assert 'href="/welcome"' not in page
    for fn in ("function connectFlow",
               "function watchFor", "function pickClient",
               "[data-mint]", "[data-copy]", '"/connect?view=graph&client="'):
        assert fn in page
    # the workspace comes with its wiki, so there is no naming step before this
    assert "function createFlow" not in page
    connect = page[page.index("function connectFlow"):page.index("async function pickClient")]
    assert "agentsUrl" not in connect and "ob-choice" in connect
    # Forrest, 2026-09-27: no "Waiting for ..." line; the watch stays, for the first page
    assert "Waiting for your" not in page and "Waiting for the first" not in page
    assert "watchFor()" in connect
    # Forrest, 2026-09-27: the first page is the proof, so no "connected" state and
    # no check of the person's keys
    for gone in ("connectedFlow", "aiStatus", "/connect/status", "is connected"):
        assert gone not in page, gone
    # the tiles are for a workspace with no agent yet; one that has one skips them
    assert "Your agent is already connected?" not in page and "ob-already" not in page
    ready = page[page.index("function readyFlow"):page.index("async function pickClient")]
    assert 'flow("Your wiki is empty")' in ready and 'el("code", "agent-prompt", FIRST)' in ready
    assert "Connect another agent" in ready and "connectFlow()" in ready
    assert "(CONNECTED && FIRST ? readyFlow : connectFlow)()" in page
    # a wiki with pages gets no flow over it: that is Settings > Agents
    assert "closable" not in page


def test_an_empty_wiki_skips_connecting_when_the_workspace_has_an_agent(app):
    """Forrest, 2026-09-27: prompting to connect an agent that is already
    connected to the workspace is weird. Decided per workspace: a key made
    outside the connect flow records no creator."""
    def connected(client) -> bool:
        m = re.search(r"window\.DEXIO_CONNECTED = (true|false);", client.get("/").text)
        return m.group(1) == "true"

    c = browser(app)
    signup(c, "ann@example.com")
    assert connected(c) is False
    r = c.post("/api/v1/connect", json={"client": "hermes"})
    key = re.search(r"(dxk_[A-Za-z0-9_\-]+)</code>", r.json()["html"]).group(1)
    assert connected(c) is False                      # made, but no agent has used it
    mcp(app, key, "list_pages")
    assert connected(c) is True
    # revoking the only used key takes the workspace back to the tiles
    ws = c.get("/api/v1/workspaces").json()["current"]
    token = next(t for t in db.list_tokens(app.state.conn, ws_id(app, ws)) if t["last_used"])
    assert c.post(f"/settings/tokens/{token['id']}/revoke?w={ws}").status_code == 303
    assert connected(c) is False
    other = browser(app)
    signup(other, "bob@example.com")
    assert connected(other) is False                  # Ann's agent is not in Bob's workspace


def test_the_first_message_has_the_agent_save_a_real_first_page(app):
    """Forrest, 2026-09-27: have them push up a first page as part of the
    onboarding. Not a test note: the agent writes about the work, and asks
    first when it has nothing to go on. The graph appearing is the proof."""
    from dexio.server import pages

    assert pages.TRY_IT == ("Save a page in my Dexio wiki about what we're working on. "
                            "If you don't know enough yet, ask me first.")
    c = browser(app)
    signup(c, "ann@example.com")
    first = json.loads(re.search(r'window\.DEXIO_FIRST_PAGE = ("(?:[^"\\]|\\.)*");',
                                 c.get("/").text).group(1))
    assert first == pages.TRY_IT
    for client in ("claude", "chatgpt"):
        html = c.get(f"/api/v1/connect?client={client}").json()["html"]
        assert "send this to save your first page:" in html and "Save a page in my Dexio wiki" in html
    html = c.post("/api/v1/connect", json={"client": "hermes"}).json()["html"]
    assert "Once Hermes says it is connected, have it save your first page. Send:" in html
    assert "What&#x27;s in" not in html
    # the old connection check is gone
    assert c.get("/api/v1/connect/status").status_code in (404, 405)


def test_steps_on_the_graph_point_to_settings_to_revoke_the_key(app):
    c = browser(app)
    signup(c, "ann@example.com")
    graph = c.get("/api/v1/connect?client=hermes&view=graph").json()["html"]
    assert "dxk_" not in graph and 'data-mint="hermes"' in graph
    r = c.post("/api/v1/connect", json={"client": "hermes", "view": "graph"})
    html = r.json()["html"]
    assert "dxk_" in html
    assert 'revoke it any time in <a href="/settings/agents">Settings</a>, under Agents.' in html
    assert "under Connected, below" not in html
    # in Settings > Agents the list is just below
    html = c.post("/api/v1/connect", json={"client": "hermes"}).json()["html"]
    assert "It shows under Connected, below, where you can revoke it any time." in html


def test_server_page_renders_without_a_workspace():
    html = server_page("/api/v1")
    assert 'window.DEXIO_PROJECT = "main";' in html and "window.DEXIO_CLIENTS = [];" in html
    assert "DEXIO_PROJECTS" not in html


def panel(page: str) -> str:
    start = page.index('<div class="panel" id="connect"')
    return page[start:page.index('<div class="panel" id="connected">')]


def test_agents_page_connects_an_ai_in_place(app):
    """Forrest, 2026-09-27: connecting from the Agents tab should not change
    screen. The panel is on the page, and always open: the same day, it doesn't
    need to be collapsible, so there is no opener and no Close."""
    c = browser(app)
    signup(c, "ann@example.com")
    for url in ("/settings/agents", "/settings/agents?connect"):
        page = c.get(url).text
        box = panel(page)
        assert not re.search(r'<div class="panel" id="connect"[^>]*\shidden>', page), url
        for gone in ("connect-open", "connect-close", "cclose", "closePanel"):
            assert gone not in page, (url, gone)
        assert "<h2>Connect an agent</h2>" in box
        assert "<script>" in page and "function pick(c)" in page
        # Forrest, 2026-09-27: no "Waiting for your AI to connect", and so no polling
        for gone in ("Waiting for", "cwait", "cdone", "/connect/status", "setInterval"):
            assert gone not in page, (url, gone)
        assert 'addEventListener("visibilitychange"' in page     # the list re-reads instead
        # a tile for every AI, nothing picked
        for cid in ("chatgpt", "claude", "claude-code", "hermes", "openclaw", "other"):
            assert f'data-client="{cid}" aria-pressed="false"' in box, cid
        assert "connect-wiki" not in box                                    # one wiki
        assert 'data-client="">' in box
    # ?connect=hermes picks Hermes and renders its steps, with no key made
    box = panel(c.get("/settings/agents?connect=hermes").text)
    assert 'data-client="hermes" aria-pressed="true"' in box
    assert '<div class="ai-flow" data-at="2" data-client="hermes">' in box    # on Connect
    assert 'data-k="1"><span class="ai-txt">Hermes</span>' in box
    assert 'data-mint="hermes"' in box and "dxk_" not in box
    assert "Waiting for" not in box


def test_connecting_is_a_stepper_with_a_progress_bar(app):
    """Forrest, 2026-09-27: the steps got long once the message and the folded
    parts opened, so connecting runs as three steps, one at a time: Choose your
    agent, Connect, First page. 2026-09-28: the bar over them is option A of
    three, equal segments filled up to the current step, names under them."""
    from dexio import ais
    from dexio.server import login

    assert ais.STEP_NAMES == ("Choose your agent", "Connect", "First page")
    head = ais.steps_head()
    assert head.count('class="ai-bar"') == 3 and 'data-n="1" aria-current="step"' in head
    assert head.count(" disabled") == 2, "nothing to go forward to before an agent is picked"
    picked = ais.steps_head(3, "Claude Code")
    assert picked.count(" data-done") == 2 and '<span class="ai-txt">Claude Code</span>' in picked
    assert " disabled" not in picked
    c = browser(app)
    signup(c, "ann@example.com")
    # every agent's steps are the Connect and First page panes, each with Back
    for client in ("claude-code", "openclaw", "hermes", "claude", "chatgpt", "other"):
        html = c.get(f"/api/v1/connect?client={client}").json()["html"]
        connect = html[:html.index('<div class="ai-pane" data-pane="3">')]
        first = html[html.index('<div class="ai-pane" data-pane="3">'):]
        assert connect.startswith('<div class="ai-pane" data-pane="2">'), client
        assert 'class="ai-back" data-go="1">Back' in connect and 'data-go="3">Next: first page' in connect
        assert 'id="try-it"' in first and 'id="try-it"' not in connect, client
        assert 'class="ai-back" data-go="2">Back' in first
    # the message with the key stays on Connect
    minted = c.post("/api/v1/connect", json={"client": "hermes"}).json()["html"]
    assert minted.index('id="agent-prompt"') < minted.index('data-pane="3"')
    # the graph screen builds the same stepper; Settings draws it on the server
    page = c.get("/").text
    assert "window.DEXIO_AI_STEPS = " in page and "window.dexioSteps" in page
    connect = page[page.index("function connectFlow"):page.index("function readyFlow")]
    assert '"ai-flow"' in connect and "window.DEXIO_AI_STEPS" in connect
    settings = c.get("/settings/agents").text
    assert '<div class="ai-flow" data-at="1">' in settings and "window.dexioSteps" in settings
    # one stylesheet for both, the bar in it, no colours of its own
    assert ".ai-bar {" in ais.CSS and ais.CSS in page and ais.CSS in login.SETTINGS_CSS
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", ais.CSS)


def test_the_ai_picker_is_one_component_in_both_places(app):
    """Forrest, 2026-09-27: improve the picker everywhere it appears (option B of
    three: rows with each AI's mark). The graph screen and Settings > Agents draw
    the same tiles from ais.py, with one stylesheet and no colours of its own."""
    from dexio import ais
    from dexio.server import login, pages

    assert set(ais.ICONS) == set(pages.CLIENTS), "every AI has a mark"
    c = browser(app)
    signup(c, "ann@example.com")
    page = c.get("/").text
    clients = json.loads(re.search(r"window\.DEXIO_CLIENTS = (\[.*?\]);", page).group(1))
    box = panel(c.get("/settings/agents").text)
    for x in clients:
        name, sub, _kind = pages.CLIENTS[x["id"]]
        inner = ais.tile(x["id"], name, sub)
        assert x["tile"] == inner and x["name"] == name
        assert (f'<button type="button" class="choice ai-tile" data-client="{x["id"]}" '
                f'aria-pressed="false">{inner}</button>') in box
        assert f'data-ai="{x["id"]}"' in inner and "ai-tick" in inner
    assert '<div class="choices ai-grid" role="group"' in box
    connect = page[page.index("function connectFlow"):page.index("async function pickClient")]
    assert '"ob-choices ai-grid"' in connect and "b.innerHTML = c.tile" in connect
    # one stylesheet, in both pages, and none of the old per-page copies
    assert ais.CSS in page and ais.CSS in login.SETTINGS_CSS
    assert "#onboard .ob-choice" not in page and ".choices .choice" not in login.SETTINGS_CSS
    assert ".choices a" not in login.PAGE
    assert not re.findall(r"#[0-9a-fA-F]{3,6}\b", ais.CSS), "brand colours live in the marks"
    # rows, two across where there is room, one on a phone
    assert "minmax(min(100%,232px),1fr)" in ais.CSS
    assert "claude.ai and the app" in pages.CLIENTS["claude"][1]


def test_connected_list_has_no_access_column(app):
    """Forrest, 2026-09-27: Access only ever read "API key, all wikis" or "Signed
    in". How an AI connected is the grey line under its name, on every width."""
    c = browser(app)
    signup(c, "ann@example.com")
    assert c.post("/api/v1/connect", json={"client": "hermes"}).status_code == 200
    page = c.get("/settings/agents").text
    table = page[page.index('<div class="panel" id="connected">'):]
    assert "<th>Name</th><th class=\"hide-sm\">Added</th><th>Last used</th><th></th>" in table
    assert "Access" not in table and "all wikis" not in table
    assert '<td>Hermes<span class="sub">API key</span></td>' in table


def test_agents_panel_has_no_wiki_to_pick(app):
    """Forrest, 2026-09-27: no "Wiki it uses" picker; a picker under the line
    saying what the agent can reach read as a contradiction. Since 2026-09-28 a
    workspace has one wiki, so an old link's ?wiki= changes nothing."""
    c = browser(app)
    signup(c, "ann@example.com")
    box = panel(c.get("/settings/agents?connect").text)
    for gone in ("connect-wiki", "Wiki it uses", "<select", "data-wiki", "none yet"):
        assert gone not in box, gone
    assert "it works as you, starting in" in box
    picked = panel(c.get("/settings/agents?connect=claude-code").text)
    assert panel(c.get("/settings/agents?connect=claude-code&wiki=research").text) == picked
    assert "wiki called" not in picked


def test_steps_need_a_session_and_a_known_ai(app):
    anon = browser(app)
    assert anon.get("/api/v1/connect?client=claude").status_code == 401
    assert anon.post("/api/v1/connect", json={"client": "hermes"}).status_code == 401
    c = browser(app)
    signup(c, "ann@example.com")
    assert c.get("/api/v1/connect?client=nope").status_code == 400
    # a workspace has one wiki, so an old caller's wiki is ignored, not refused
    assert c.get("/api/v1/connect?client=claude&wiki=nope").status_code == 200
    assert c.post("/api/v1/connect", json={"client": "hermes", "wiki": "nope"}).status_code == 200
    # Claude and ChatGPT sign in as apps; no key is made for them
    assert c.post("/api/v1/connect", json={"client": "claude"}).status_code == 400
    # another site cannot make a key with the person's session
    r = c.post("/api/v1/connect", json={"client": "hermes"},
               headers={"origin": "https://evil.example"})
    assert r.status_code == 403


def test_the_message_carries_a_working_key_and_names_no_wiki(app):
    """The agent's message is the same for every workspace: it never names a
    wiki (one per workspace since 2026-09-28), and the key in it works."""
    from dexio.server import pages

    c = browser(app)
    signup(c, "ann@example.com")
    got = c.get("/api/v1/connect?client=hermes").json()
    assert pages.agent_prompt() in got["html"] and "dxk_" not in got["html"]
    r = c.post("/api/v1/connect", json={"client": "hermes"})
    assert r.status_code == 200
    html = r.json()["html"]
    m = re.search(r"Connect yourself to my Dexio wiki\. Read the steps with curl -s "
                  r"https://dexio\.wiki/agents\.md and follow them, using this API key: "
                  r"(dxk_[A-Za-z0-9_\-]+)</code>", html)
    assert m, html
    assert html.count("wiki called") == 0
    assert "Save a page in my Dexio wiki about what we&#x27;re working on." in html
    assert mcp(app, m.group(1), "list_pages")["pages"] == []         # the key works
    # chat apps get the same first message
    assert "Save a page in my Dexio wiki" in c.get("/api/v1/connect?client=claude").json()["html"]


def wikis(app) -> set[str]:
    return {r["name"] for r in app.state.conn.execute("SELECT name FROM projects")}


def test_a_new_account_comes_with_its_wiki(app):
    """2026-09-28: every workspace is made with its one wiki, so a new person
    names nothing: the graph view opens on the empty wiki's connect flow."""
    c = browser(app)
    signup(c, "ann@acme.com")
    ws = c.get("/api/v1/workspaces").json()["current"]
    assert db.wiki_key(ws_id(app, ws)) in wikis(app)
    page = c.get("/").text
    assert '<div id="wrap" class="onboarding">' in page               # empty: onboarding
    assert "function createFlow" not in page and "DEXIO_PROJECTS" not in page


def test_new_workspace_opens_on_its_graph_view(app):
    c = browser(app)
    signup(c, "ann@example.com")
    first = c.get("/api/v1/workspaces").json()["current"]
    r = c.post("/settings/workspaces", data={"name": "Side"})
    assert r.headers["location"] == "/"
    c.cookies.set("dexio_ws", r.cookies.get("dexio_ws"))
    side = c.get("/api/v1/workspaces").json()["current"]
    assert side != first and db.wiki_key(ws_id(app, side)) in wikis(app)       # made with its wiki
    assert '<div id="wrap" class="onboarding">' in c.get("/").text


def test_muse_and_grok_bot_connect(app):
    """Forrest, 2026-09-28: Muse and Grok Bot join the tiles. Both set themselves up
    from the usual message. Grok Bot adds a custom MCP server from chat (Cursor
    support); there is no settings form for one, so no Settings, Plugins steps."""
    c = browser(app)
    signup(c, "ann@example.com")
    muse = c.post("/api/v1/connect", json={"client": "muse"})
    assert muse.status_code == 200
    html = muse.json()["html"]
    assert "Copy this message and send it to Muse" in html
    assert "save it as a reusable skill" in html          # the by-hand route
    # Grok Bot: the same "Get the message" button, then the message with the key
    first = c.get("/api/v1/connect?client=grok-bot").json()["html"]
    assert 'data-mint="grok-bot"' in first and "dxk_" not in first
    assert "Get the message" in first
    r = c.post("/api/v1/connect", json={"client": "grok-bot"})
    assert r.status_code == 200
    html = r.json()["html"]
    assert "Copy this message and send it to Grok Bot" in html
    assert "Add this MCP server: https://testserver/mcp" in html   # the by-hand route
    assert "from your next message" in html
    assert "<b>Settings</b>, then <b>Plugins</b>" not in html
    m = re.search(r"Authorization: Bearer (dxk_[A-Za-z0-9_\-]+)", html)
    assert m, html
    assert mcp(app, m.group(1), "list_pages")["pages"] == []          # the key works
    names = [r["name"] for r in app.state.conn.execute("SELECT name FROM tokens")]
    assert "Muse" in names and "Grok Bot" in names
