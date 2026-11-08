"""A public page's text and links in the HTML (Forrest, 2026-10-01: "will google crawl
these guides?"). See server/ssr.py."""
from __future__ import annotations

import re

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from dexio.server import ssr  # noqa: E402

from test_sharing import owner_with_wiki, public, share  # noqa: E402
from test_workspaces import app, browser, mcp  # noqa: E402,F401

HTML = {"accept": "text/html"}


def article(html: str) -> str:
    m = re.search(r'<main id="ssr" class="md">(.*?)</main>', html, re.S)
    return m.group(1) if m else ""


def test_a_public_page_carries_its_text_and_links_in_the_html(app):
    c, tok, handle = owner_with_wiki(app)
    mcp(app, tok, "write_page", path="notes/guide", text=(
        "---\ndescription: A guide.\n---\n# Guide\n\nRead [[notes/plan]], [[secret/deal|the deal]],"
        " [the plan](plan.md) and [docs](https://example.com/docs).\n\n"
        "• one\n• two\n\n```\n[[notes/plan]] stays code\n```\n\n"
        "<script>alert(1)</script> [x](javascript:alert(1)) ![a chart](chart.png)\n"))
    public(c, handle, "folder", "notes")
    html = browser(app).get(f"/w/{handle}/notes/guide", headers=HTML).text
    a = article(html)
    assert "<h1>Guide</h1>" in a and a.count("Guide</h1>") == 1, "the title once, no repeat"
    assert "description: A guide." not in a, "frontmatter is not text"
    plan = f'href="/w/{handle}/notes/plan"'
    assert a.count(plan) >= 2, "wikilinks and page links reach public pages"
    assert "<span>the deal</span>" in a and "secret/deal" not in a, "a private page stays text"
    assert 'href="https://example.com/docs" rel="ugc nofollow"' in a
    assert "<li>one</li>" in a and "<li>two</li>" in a, "• bullets are a list"
    assert "[[notes/plan]] stays code" in a, "code is left alone"
    assert "<script>alert(1)</script>" not in html.split('<main id="ssr"')[1].split("</main>")[0]
    assert "&lt;script&gt;" in a and 'href="javascript:' not in a
    assert '<span class="ssr-img">a chart</span>' in a
    # the pages beside it, only what anyone may open
    more = a.split('aria-label="More pages"')[1]
    assert f'href="/w/{handle}/notes/deep/more"' in more and "secret" not in more
    # the app hides it as soon as its script starts
    assert html.index('classList.add("js")') < html.index('<main id="ssr"')


def test_a_folder_address_lists_its_public_pages(app):
    c, _tok, handle = owner_with_wiki(app)
    public(c, handle, "folder", "notes")
    a = article(browser(app).get(f"/w/{handle}?folder=notes", headers=HTML).text)
    assert f'href="/w/{handle}/notes/plan"' in a and f'href="/w/{handle}/notes/deep/more"' in a
    assert "secret" not in a and 'href="/w/' + handle + '/index"' not in a


def test_nothing_is_rendered_for_what_is_not_public(app, sent):
    c, _tok, handle = owner_with_wiki(app)
    share(c, handle, "page", "notes/plan", "friend@example.com")
    r = browser(app).get(f"/w/{handle}/notes/plan", headers=HTML, follow_redirects=False)
    assert '<main id="ssr"' not in r.text
    # members see the app alone
    assert '<main id="ssr"' not in c.get(f"/w/{handle}/notes/plan", headers=HTML).text


def test_page_text_cannot_fill_the_shells_placeholders(app):
    c, tok, handle = owner_with_wiki(app)
    mcp(app, tok, "write_page", path="notes/odd",
        text="# __INIT__\n\nUses `__GRAPH_JS__` and `__BRAND__`.\n")
    public(c, handle, "folder", "notes")
    html = browser(app).get(f"/w/{handle}/notes/odd", headers=HTML).text
    assert "<title>__INIT__ · " in html
    a = article(html)
    assert "<h1>__INIT__</h1>" in a
    assert "<code>__GRAPH_JS__</code> and <code>__BRAND__</code>" in a


def test_the_directory_names_every_published_page(app):
    c, _tok, handle = owner_with_wiki(app)
    public(c, handle, "folder", "notes")
    r = c.post(f"/settings/publisher?w={handle}", data={"publisher": "owner-co"})
    assert r.status_code == 303
    r = c.post(f"/api/v1/share/listed?w={handle}", json={"kind": "folder", "path": "notes",
                                                         "on": True})
    assert r.status_code == 200, r.text
    (entry,) = browser(app).get("/api/v1/directory").json()["wikis"]
    assert [p["path"] for p in entry["page_links"]] == ["notes/deep/more", "notes/plan"]
    plan = entry["page_links"][1]
    assert plan["title"] == "Plan" and plan["url"].endswith(f"/w/{handle}/notes/plan")
    assert plan["url"].startswith("https://"), "absolute, for the site's links"


def test_render_page_alone():
    out = ssr.render_page("# T\n\nSee [[a/b#Some part]] and [[gone]].\n", "a/x", "T", "h",
                          ["a/b", "a/x"], lambda p: True)
    assert '<a href="/w/h/a/b#s-some-part">b</a>' in out
    assert "<span>gone</span>" in out


from test_sharing import sent  # noqa: E402,F401


def test_a_pages_picture_shows_and_is_its_link_preview(app):
    """Frontmatter image: (Forrest, 2026-11-07): the picture in the HTML for readers
    without JavaScript, the link preview's picture, and a file anyone who may read
    the page may open, though it sits outside the shared folder."""
    c, tok, handle = owner_with_wiki(app)
    assert c.put("/api/v1/files?path=raw/images/iris.jpg", content=b"jpeg").status_code == 200
    assert mcp(app, tok, "write_page", path="notes/iris", text=(
        "---\nimage: raw/images/iris.jpg\nimage_caption: Iris <Lane>\n---\n# Iris\n\nHello.\n"
    )).get("ok")
    public(c, handle, "folder", "notes")
    anon = browser(app)
    html = anon.get(f"/w/{handle}/notes/iris", headers=HTML).text
    href = f"/api/v1/files?w={handle}&amp;path=raw/images/iris.jpg"
    assert f'<meta property="og:image" content="https://app.dexio.wiki{href}">' in html
    assert '<meta property="og:image:alt" content="Iris &lt;Lane&gt;">' in html
    assert '<meta name="twitter:card" content="summary_large_image">' in html
    a = article(html)
    assert a.index('<figure class="ssr-image">') < a.index("Hello."), "above the text"
    assert f'<img src="{href}" alt="Iris &lt;Lane&gt;"><figcaption>Iris &lt;Lane&gt;</figcaption>' in a
    assert anon.get(f"/api/v1/files?w={handle}&path=raw/images/iris.jpg").status_code == 302
    # a public page without one keeps the small card and names no picture
    plain = anon.get(f"/w/{handle}/notes/plan", headers=HTML).text
    assert '<meta name="twitter:card" content="summary">' in plain and "og:image" not in plain
    # agents see it in read_page and list_pages
    head = mcp(app, tok, "read_page", path="notes/iris")
    assert head["image"] == "raw/images/iris.jpg"
    listed = {p["path"]: p for p in mcp(app, tok, "list_pages", folder="notes")["pages"]}
    assert listed["notes/iris"]["image"] == "raw/images/iris.jpg"
    assert "image" not in listed["notes/plan"]


def test_a_picture_that_names_no_file_shows_nothing(app):
    c, tok, handle = owner_with_wiki(app)
    assert mcp(app, tok, "write_page", path="notes/gone", text=(
        "---\nimage: raw/images/gone.jpg\n---\n# Gone\n\nText.\n")).get("ok")
    public(c, handle, "folder", "notes")
    html = browser(app).get(f"/w/{handle}/notes/gone", headers=HTML).text
    assert "og:image" not in html and "ssr-image" not in article(html)
