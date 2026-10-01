"""A public page's text and links in the HTML itself.

Forrest, 2026-10-01 ("will google crawl these guides?"): the app draws a page with
JavaScript, from /api/v1/note, after the HTML arrives. Google runs that script, but
later than it reads HTML and not always; Bing runs less, and the crawlers behind AI
assistants mostly none. So what anyone may read also carries its text in the HTML,
rendered here, with links to the other pages the reader may open: a page shows its
text and the pages beside it, a folder or wiki address lists its pages.

The app hides this the moment its script starts (`html.js`, set in the head), and
shows the same page in its panel, so a reader with JavaScript sees nothing new and a
reader or crawler without it gets a plain, readable page. Only pages anyone may read
get it (app.guest_page); a page shared by email does not.

Markdown is CommonMark with tables (markdown-it-py), raw HTML escaped, plus what the
app's renderer adds: [[wikilinks]] and "•" bullets. A link reaches another page only
if the reader may see it, and then by its public address; otherwise its words stay as
plain text, as the app shows them to a guest. Outside links get rel="ugc nofollow",
as everywhere a guest reads. Images show their alt text: files are served through
signed addresses the HTML cannot hold.
"""
from __future__ import annotations

import html
import re
from urllib.parse import quote, unquote

from markdown_it import MarkdownIt

from ..parse import PageIndex, _frontmatter_lines, _resolve, title_of

# Pages listed beside a page, and on a folder or wiki address.
NEAR_PAGES = 100
LIST_PAGES = 500

_FENCE = re.compile(r"^\s*(```|~~~)")
_BULLET = re.compile(r"^(\s*)•(\s+)")
_H1 = re.compile(r"^#\s+(.*?)\s*#*\s*$")
_WIKI = re.compile(r"\[\[([^\]<>]+)\]\]")
_CODE = re.compile(r"(<pre>.*?</pre>|<code>.*?</code>)", re.S)
_OURS = re.compile(r"^https?://(?:[a-z0-9-]+\.)*dexio\.wiki(?:[/?#:]|$)", re.I)

e = html.escape


def page_href(handle: str, path: str, anchor: str = "") -> str:
    return (f"/w/{quote(handle, safe='')}/{quote(path, safe='/')}"
            + (f"#s-{quote(anchor, safe='')}" if anchor else ""))


class _Links:
    """Where a link on `source` goes for this reader: the page's public address, or
    None (its words stay as text)."""

    def __init__(self, handle: str, source: str, paths, sees):
        self.handle, self.source, self.sees = handle, source, sees
        self.index = PageIndex(paths)

    def page(self, target: str) -> str | None:
        target = unquote(target or "").strip()
        anchor = ""
        if "#" in target:
            target, anchor = target.split("#", 1)
        target = target.strip().strip("/")
        if target.startswith("./"):
            target = target[2:]
        if target.endswith(".md"):
            target = target[:-3]
        if not target:
            return None
        path = _resolve(target, self.source, self.index)
        if not path or not self.sees(path):
            return None
        from .. import parse
        return page_href(self.handle, path, parse.heading_slug(anchor) if anchor else "")


def _md() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": False}).enable(["table", "strikethrough"])

    def link_open(self, tokens, idx, options, env):
        tok = tokens[idx]
        href = tok.attrGet("href") or ""
        stack = env.setdefault("_links", [])
        if re.match(r"^(https?:|mailto:)", href, re.I):
            if not _OURS.match(href) and not href.lower().startswith("mailto:"):
                tok.attrSet("rel", "ugc nofollow")
            stack.append(True)
            return self.renderToken(tokens, idx, options, env)
        to = env["links"].page(href)
        if to is None:
            stack.append(False)
            return "<span>"
        tok.attrSet("href", to)
        stack.append(True)
        return self.renderToken(tokens, idx, options, env)

    def link_close(self, tokens, idx, options, env):
        stack = env.setdefault("_links", [])
        linked = stack.pop() if stack else True
        return self.renderToken(tokens, idx, options, env) if linked else "</span>"

    def image(self, tokens, idx, options, env):
        alt = self.renderInlineAsText(tokens[idx].children or [], options, env)
        return f'<span class="ssr-img">{e(alt)}</span>' if alt else ""

    md.add_render_rule("link_open", link_open)
    md.add_render_rule("link_close", link_close)
    md.add_render_rule("image", image)
    return md


_MARKDOWN = _md()


def _prepare(text: str, title: str) -> str:
    """The page's markdown as the app shows it: no frontmatter, no first heading
    that repeats the title, and "•" lines as list items."""
    lines = (text or "").split("\n")
    lines = lines[_frontmatter_lines([ln + "\n" for ln in lines]):]
    for i, ln in enumerate(lines):
        if not ln.strip():
            continue
        m = _H1.match(ln)
        if m and m.group(1).strip() == (title or "").strip():
            del lines[i]
        break
    out, fence = [], False
    for ln in lines:
        if _FENCE.match(ln):
            fence = not fence
        out.append(ln if fence else _BULLET.sub(r"\1-\2", ln))
    return "\n".join(out)


def _wikilinks(body: str, links: _Links) -> str:
    """[[target]], [[target|words]] and [[target#section]] outside code."""
    def one(m: re.Match) -> str:
        raw = html.unescape(m.group(1))
        target, _, words = raw.partition("|")
        words = words.strip() or target.split("#", 1)[0].strip().rsplit("/", 1)[-1] or target
        to = links.page(target)
        return f'<a href="{e(to)}">{e(words)}</a>' if to else f"<span>{e(words)}</span>"
    parts = _CODE.split(body)
    return "".join(_WIKI.sub(one, p) if i % 2 == 0 else p for i, p in enumerate(parts))


def render_page(text: str, path: str, title: str, handle: str, paths, sees) -> str:
    """The page's text as HTML, links resolved for a reader who sees what `sees` allows."""
    links = _Links(handle, path, paths, sees)
    body = _MARKDOWN.render(_prepare(text, title), {"links": links})
    return _wikilinks(body, links)


def _page_list(items: list[dict], handle: str) -> str:
    rows = []
    for p in items:
        name = p.get("title") or title_of("", p["path"])
        about = p.get("description") or ""
        rows.append(f'<li><a href="{e(page_href(handle, p["path"]))}">{e(name)}</a>'
                    + (f" <span>{e(about)}</span>" if about else "") + "</li>")
    return "<ul>" + "".join(rows) + "</ul>" if rows else ""


def page_article(conn, key: str, handle: str, workspace: str, row: dict, sees) -> str:
    """The whole block for one page: title, text, then the pages beside it (its top
    folder's pages the reader may open, nearest first)."""
    from . import db
    path = row["path"]
    title = row.get("title") or title_of(row.get("text") or "", path)
    paths = db.page_paths(conn, key)
    body = render_page(row.get("text") or "", path, title, handle, paths, sees)
    top = path.split("/", 1)[0] if "/" in path else ""
    near = [p for p in db.page_list(conn, key, top, limit=LIST_PAGES)
            if p["path"] != path and sees(p["path"])]
    parent = path.rsplit("/", 1)[0] if "/" in path else ""
    near.sort(key=lambda p: (p["path"].rsplit("/", 1)[0] != parent, p["path"]))
    more = _page_list(near[:NEAR_PAGES], handle)
    crumbs = " / ".join(e(s) for s in path.split("/")[:-1])
    return (f'<main id="ssr" class="md">'
            f'<p class="ssr-crumbs"><a href="{e(page_href(handle, "").rstrip("/"))}">'
            f"{e(workspace)}</a>{' / ' + crumbs if crumbs else ''}</p>"
            f"<h1>{e(title)}</h1>\n{body}"
            + (f'<nav aria-label="More pages"><h2>More pages</h2>{more}</nav>' if more else "")
            + "</main>")


def list_article(conn, key: str, handle: str, workspace: str, folder: str, sees) -> str:
    """A wiki or folder address: its name and the pages in it the reader may open."""
    from . import db
    folder = (folder or "").strip("/")
    items = [p for p in db.page_list(conn, key, folder, limit=LIST_PAGES) if sees(p["path"])]
    if not items:
        return ""
    heading = f"{workspace} / {folder}" if folder else workspace
    return (f'<main id="ssr" class="md"><h1>{e(heading)}</h1>'
            f'<nav aria-label="Pages">{_page_list(items, handle)}</nav></main>')
