"""Parse a directory of markdown into a link graph.

Zero dependencies on purpose: this module runs on any machine that has Python,
including the push client on a laptop or a Mac mini, without an install step.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

WIKILINK = re.compile(r"\[\[([^\]]+)\]\]")
MDLINK = re.compile(r"\[[^\]]*\]\(([^)\s]+)\)")
COMMENT_LINE = re.compile(r"<!--.*?-->", re.S)
H1 = re.compile(r"^#\s+(.+)$", re.M)


@dataclass
class Page:
    path: str            # wiki-relative, no extension, forward slashes
    file: str            # wiki-relative filename as written on disk
    title: str
    folder: str
    words: int
    text: str = ""


@dataclass
class Graph:
    pages: dict[str, Page] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)
    dangling: list[tuple[str, str]] = field(default_factory=list)

    # ---- derived -------------------------------------------------------
    def degree(self) -> dict[str, int]:
        d = {p: 0 for p in self.pages}
        for src, dst in self.edges:
            d[src] = d.get(src, 0) + 1
            d[dst] = d.get(dst, 0) + 1
        return d

    def inbound(self) -> dict[str, int]:
        d = {p: 0 for p in self.pages}
        for _src, dst in self.edges:
            d[dst] = d.get(dst, 0) + 1
        return d

    def orphans(self) -> list[str]:
        """Pages nothing links to and that link nowhere."""
        deg = self.degree()
        return sorted(p for p in self.pages if deg.get(p, 0) == 0)

    def unreferenced(self) -> list[str]:
        """Pages nothing links to, even if they link out."""
        inb = self.inbound()
        return sorted(p for p in self.pages if inb.get(p, 0) == 0)

    def hubs(self, n: int = 10) -> list[tuple[str, int]]:
        return sorted(self.degree().items(), key=lambda kv: (-kv[1], kv[0]))[:n]

    def stats(self) -> dict:
        return {
            "pages": len(self.pages),
            "links": len(self.edges),
            "orphans": len(self.orphans()),
            "unreferenced": len(self.unreferenced()),
            "dangling": len(self.dangling),
            "words": sum(p.words for p in self.pages.values()),
        }

    def to_dict(self, include_text: bool = False) -> dict:
        deg = self.degree()
        return {
            "stats": self.stats(),
            "nodes": [
                {
                    "id": p.path,
                    "title": p.title,
                    "folder": p.folder,
                    "words": p.words,
                    "degree": deg.get(p.path, 0),
                    **({"text": p.text} if include_text else {}),
                }
                for p in sorted(self.pages.values(), key=lambda p: p.path)
            ],
            "links": [{"source": s, "target": t} for s, t in self.edges],
            "dangling": [{"source": s, "target": t} for s, t in self.dangling],
        }


def _normalise(target: str) -> str:
    target = target.split("|", 1)[0]          # [[path|alias]]
    target = target.split("#", 1)[0]          # [[path#anchor]]
    target = target.strip().strip("/")
    if target.endswith(".md"):
        target = target[:-3]
    return target


def basename(path: str) -> str:
    return path.rsplit("/", 1)[-1]


class PageIndex:
    """What link resolution needs to know about a wiki: which paths exist, and
    which paths end in a given name. Built once, so resolving a link no longer
    scans every page."""

    def __init__(self, paths):
        self.paths = set(paths)
        self.bases: dict[str, list[str]] = {}
        for p in self.paths:
            self.bases.setdefault(basename(p), []).append(p)

    def __contains__(self, path: str) -> bool:
        return path in self.paths

    def with_base(self, base: str) -> list[str]:
        return self.bases.get(base, [])


def link_keys(target: str, source: str) -> tuple[str, str]:
    """The two things besides the target itself that decide where a link from
    `source` goes: the target joined to the source's folder ('' at the root),
    and the target's last segment. A page can change where a link resolves only
    if its path is the target or that joined path, or its name is that segment."""
    rel = ""
    parent = source.rsplit("/", 1)[0] if "/" in source else ""
    if parent:
        joined = str(Path(parent, target)).replace("\\", "/")
        # normalise ../ segments
        parts: list[str] = []
        for seg in joined.split("/"):
            if seg == "..":
                if parts:
                    parts.pop()
            elif seg not in ("", "."):
                parts.append(seg)
        rel = "/".join(parts)
    return rel, basename(target)


def resolve_keys(target: str, rel: str, base: str, index) -> str | None:
    """Exact path, then relative to the linking page, then a page name that only
    one page has. `index` answers `in` and `with_base` (PageIndex, or the
    database-backed one the server uses)."""
    if target in index:
        return target
    if rel and rel in index:
        return rel
    matches = index.with_base(base)
    if len(matches) == 1:
        return matches[0]
    return None


def _resolve(target: str, source: str, pages) -> str | None:
    """Resolve a link target against known pages: exact, then relative, then basename."""
    index = pages if hasattr(pages, "with_base") else PageIndex(pages)
    rel, base = link_keys(target, source)
    return resolve_keys(target, rel, base, index)


def page_links(source: str, text: str) -> list[tuple[int, str, str, str]]:
    """Every link on a page, in order, duplicates kept: (position, target, rel, base)."""
    return [(i, t, *link_keys(t, source)) for i, t in enumerate(extract_links(text))]


FENCE_OPEN = re.compile(r"(`{3,}|~{3,})(.*)$")
FENCE_CLOSE = re.compile(r"(`{3,}|~{3,})[ \t]*$")
BACKTICKS = re.compile(r"`+")
BLANK_LINE = re.compile(r"\n[ \t]*\n")


def mask_code(text: str) -> str:
    """Blank out HTML comments, fenced code blocks and inline code spans.

    Every offset and line break is kept, so a pattern matched on the result
    sits at the same position in the original. Links written as examples
    inside code are not links, which is also how the web view renders them.
    Follows CommonMark closely enough for wiki prose: a fence opens with three
    or more backticks or tildes indented at most three spaces, closes on a line
    of the same character at least as long, and runs to the end of the page if
    it never closes; an inline span closes on the next run of exactly as many
    backticks within the same paragraph, and an unmatched run is literal text.
    """
    out = list(text)

    def blank(a: int, b: int) -> None:
        for i in range(a, b):
            if out[i] != "\n":
                out[i] = " "

    for m in COMMENT_LINE.finditer(text):
        blank(m.start(), m.end())

    offset = 0
    fence: tuple[str, int, int] | None = None   # (char, length, start offset)
    for line in text.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        stripped = body.lstrip(" ")
        indent = len(body) - len(stripped)
        if indent <= 3:
            if fence is None:
                m = FENCE_OPEN.match(stripped)
                # A backtick fence's info string may not contain a backtick.
                if m and not (m.group(1)[0] == "`" and "`" in m.group(2)):
                    fence = (m.group(1)[0], len(m.group(1)), offset)
            else:
                m = FENCE_CLOSE.match(stripped)
                if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1]:
                    blank(fence[2], offset + len(line))
                    fence = None
        offset += len(line)
    if fence is not None:
        blank(fence[2], len(text))

    fenced = "".join(out)
    i = 0
    while True:
        m = BACKTICKS.search(fenced, i)
        if not m:
            break
        if m.start() > 0 and fenced[m.start() - 1] == "\\":
            i = m.start() + 1                   # escaped backtick is literal
            continue
        n = len(m.group(0))
        para = BLANK_LINE.search(fenced, m.end())
        limit = para.start() if para else len(fenced)
        close = None
        j = m.end()
        while True:
            c = BACKTICKS.search(fenced, j, limit)
            if not c:
                break
            if len(c.group(0)) == n:
                close = c
                break
            j = c.end()
        if close is None:
            i = m.end()                         # unmatched run is literal
            continue
        blank(m.start(), close.end())
        i = close.end()
    return "".join(out)


# Extensions that mark a link as pointing at a file (an image, a deck, a PDF,
# code), not a page. Such links are not page links: they never count as broken,
# and the wiki's files (server/files.py) are where they resolve.
FILE_EXTS = frozenset("""
png jpg jpeg gif webp svg bmp tif tiff ico heic avif pdf doc docx ppt pptx key xls xlsx
numbers pages odt ods odp rtf csv tsv txt json jsonl yaml yml toml xml html htm css js mjs
ts tsx jsx py rb go rs java kt swift c h cpp hpp cs php sh bash zsh sql ini cfg conf log
ipynb zip gz tgz tar 7z rar mp3 wav m4a ogg flac mp4 mov webm avi mkv drawio excalidraw
fig sketch psd ai eps ttf otf woff woff2 wasm bin exe dmg pkg apk msi
""".split())


def is_file_link(target: str) -> bool:
    """True for a link to a file rather than a page: raw/deck.pdf, img.png."""
    name = target.split("#", 1)[0].split("|", 1)[0].strip().rsplit("/", 1)[-1]
    return "." in name and name.rsplit(".", 1)[1].lower() in FILE_EXTS


def extract_links(text: str) -> list[str]:
    """Wikilinks and relative markdown links, ignoring code, HTML comments, URLs
    and links to files (is_file_link)."""
    body = mask_code(text)
    out = [_normalise(m) for m in WIKILINK.findall(body) if not is_file_link(m)]
    for m in MDLINK.findall(body):
        if "://" in m or m.startswith(("#", "mailto:")) or is_file_link(m):
            continue
        # Judge the part before any anchor, as move_page's rewrite does, so
        # [see](notes.md#part) is a link to notes like [see](notes.md).
        base = m.split("#", 1)[0]
        if base.endswith(".md") or "/" in base:
            out.append(_normalise(m))
    return [t for t in out if t]


ATX_HEADING = re.compile(r"^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$")
CLOSING_HASHES = re.compile(r"(?:^|[ \t]+)#+[ \t]*$")


def keep_lines(text: str) -> list[str]:
    """Lines with their endings, split on "\\n" only, so line numbers agree with
    search_pages (which counts newlines) whatever else the text contains."""
    parts = text.split("\n")
    out = [p + "\n" for p in parts[:-1]]
    if parts[-1]:
        out.append(parts[-1])
    return out


def _frontmatter_lines(lines: list[str]) -> int:
    """How many leading lines are YAML frontmatter (0 if none)."""
    if not lines or lines[0].rstrip("\r\n") != "---":
        return 0
    for i in range(1, len(lines)):
        if lines[i].rstrip("\r\n") in ("---", "..."):
            return i + 1
    return 0


def outline(text: str) -> list[dict]:
    """The page's ATX headings, in order: {line (1-based), level, heading}.
    Headings inside code, HTML comments or frontmatter are not headings."""
    lines = keep_lines(text)
    masked = keep_lines(mask_code(text))
    out = []
    for i in range(_frontmatter_lines(lines), len(lines)):
        m = ATX_HEADING.match(masked[i].rstrip("\r\n"))
        if not m:
            continue
        orig = ATX_HEADING.match(lines[i].rstrip("\r\n"))
        raw = (orig.group(2) if orig else "") or ""
        out.append({"line": i + 1, "level": len(m.group(1)),
                    "heading": CLOSING_HASHES.sub("", raw).strip()})
    return out


# A section's anchor in a page's web address (app.dexio.wiki/w/<workspace>/page#anchor).
# graph.js computes the same from the same source text (headingSlug), so an
# address built here opens the section the web view gives that id. Built from the
# markdown rather than the rendered heading, so a link's target or a page's title
# never changes it: links read as their label, code and emphasis as their text.
_WEB_HEADING = re.compile(r"^(#{1,6})\s+(.*)$")
_SLUG_IMAGE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_SLUG_WIKI = re.compile(r"!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]")
_SLUG_LINK = re.compile(r"\[([^\]]+)\]\([^)\s]+\)")
_SLUG_JUNK = re.compile(r"[\W_]+")


def heading_slug(raw: str) -> str:
    """The anchor for a heading written `raw` (the text after the #s)."""
    s = CLOSING_HASHES.sub("", raw)
    s = _SLUG_IMAGE.sub(r"\1", s)
    s = _SLUG_WIKI.sub(lambda m: m.group(2) or m.group(1), s)
    s = _SLUG_LINK.sub(r"\1", s)
    return _SLUG_JUNK.sub("-", s.lower()).strip("-") or "section"


def heading_anchors(text: str, title: str | None = None) -> dict[int, str]:
    """Line number (1-based) of each heading the web view shows -> its anchor,
    numbered -2, -3 on repeats as the web view numbers them. A first line that is
    `# <title>` is the page's title, which the view shows above the text instead."""
    lines = keep_lines(text)
    masked = keep_lines(mask_code(text))            # code and comments blanked
    no_comments = list(text)
    for c in COMMENT_LINE.finditer(text):
        for j in range(c.start(), c.end()):
            if no_comments[j] != "\n":
                no_comments[j] = " "
    shown = keep_lines("".join(no_comments))        # what the view renders
    start = _frontmatter_lines(lines)
    out: dict[int, str] = {}
    used: set[str] = set()
    first = True
    for i in range(start, len(lines)):
        if not shown[i].strip():
            continue                        # blank, or only a comment
        was_first, first = first, False
        m = _WEB_HEADING.match(lines[i].rstrip("\r\n"))
        if not m or not masked[i].lstrip().startswith("#"):
            continue                        # not a heading, or one inside code
        if was_first and title and len(m.group(1)) == 1 and \
                CLOSING_HASHES.sub("", m.group(2)).strip() == title.strip():
            continue
        base = heading_slug(m.group(2))
        slug, k = base, 2
        while slug in used:
            slug, k = f"{base}-{k}", k + 1
        used.add(slug)
        out[i + 1] = slug
    return out


def _fold(s: str) -> str:
    return " ".join(s.split()).casefold()


def find_section(text: str, section: str) -> tuple[int, int, dict]:
    """Lines (first, last; 1-based, inclusive) of the section headed `section`: its
    heading line through the line before the next heading of the same or a higher
    level. `section` is the heading's text (case and extra spaces ignored), may
    start with #s to pin the level, or is the heading's line number. Raises
    ValueError saying what exists when it matches no heading or several."""
    heads = outline(text)
    s = str(section or "").strip()
    if not s:
        raise ValueError("section is empty")
    if s.isdigit():
        found = [h for h in heads if h["line"] == int(s)]
        if not found:
            raise ValueError(f"line {s} is not a heading; headings are on lines "
                             + ", ".join(str(h["line"]) for h in heads[:40]))
    else:
        m = re.match(r"^(#{1,6})\s+(.*)$", s)
        level, name = (len(m.group(1)), m.group(2)) if m else (None, s)
        found = [h for h in heads if _fold(h["heading"]) == _fold(name)
                 and (level is None or h["level"] == level)]
        if not found:
            listed = "; ".join(f"{'#' * h['level']} {h['heading']}" for h in heads[:40])
            raise ValueError(f"no heading {s!r}" + (f"; the page's headings: {listed}"
                                                     if heads else "; the page has no headings"))
        if len(found) > 1:
            where = ", ".join(f"line {h['line']} ({'#' * h['level']})" for h in found)
            raise ValueError(f"{len(found)} headings match {s!r}: {where}; pass the line number"
                             " as section, or the heading with its #s")
    head = found[0]
    end = len(keep_lines(text))
    for h in heads:
        if h["line"] > head["line"] and h["level"] <= head["level"]:
            end = h["line"] - 1
            break
    return head["line"], end, head


def line_slice(text: str, first: int, last: int) -> str:
    """Lines first..last (1-based, inclusive), line endings kept."""
    return "".join(keep_lines(text)[max(first, 1) - 1:max(last, 0)])


def replace_lines(text: str, first: int, last: int, new: str) -> str:
    """Replace lines first..last (1-based, inclusive) with `new`, keeping one blank
    line between it and whatever follows. Empty `new` removes the lines."""
    lines = keep_lines(text)
    before, after = "".join(lines[:first - 1]), "".join(lines[last:])
    if before and not before.endswith("\n"):
        before += "\n"
    if not new.strip():
        return before + after
    body = new.rstrip("\n") + "\n"
    if after:
        body += "\n"
    return before + body + after


def title_of(text: str, fallback: str) -> str:
    m = H1.search(text)
    if m:
        return m.group(1).strip()
    return fallback.rsplit("/", 1)[-1].replace("-", " ").replace("_", " ")


DESCRIPTION_CHARS = 160         # a first sentence, trimmed
AUTHORED_CHARS = 300            # a frontmatter description someone wrote on purpose
# Only the top of a page is read for its description; list_pages fetches this much.
DESCRIPTION_SOURCE = 6000
_FM_KEY = re.compile(r"^(description|summary)[ \t]*:[ \t]*(.*?)[ \t]*$", re.I)
_NOT_PROSE = re.compile(r"^\s*(?:\||[-*_=](?:\s*[-*_=]){2,}\s*$|<[A-Za-z/!]|!\[[^\]]*\]\([^)]*\)\s*$|\[\^)")
_BULLET = re.compile(r"^\s*(?:>\s*)*(?:[-*+]|\d{1,3}[.)])\s+(?:\[[ xX]\]\s+)?")
_QUOTE = re.compile(r"^\s*(?:>\s*)+")
_WIKI_ALIAS = re.compile(r"\[\[([^\]|#]*)(?:#[^\]|]*)?(?:\|([^\]]*))?\]\]")
_MD_TEXT = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_STRONG = re.compile(r"(\*\*|__)(.+?)\1")
_EM = re.compile(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])")
_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_FN_REF = re.compile(r"\[\^[^\]\s]+\]")       # a footnote's citation, [^1]: not words
_SENTENCE_END = re.compile(r"[.!?](?=\s+[\"'“‘(\[`*_]?[A-Z0-9])")


def _plain(s: str) -> str:
    """Markdown inline syntax reduced to the words a reader sees."""
    s = _FN_REF.sub("", s)
    s = _WIKI_ALIAS.sub(lambda m: (m.group(2) or m.group(1)).strip(), s)
    s = _MD_TEXT.sub(lambda m: m.group(1), s)
    s = _STRONG.sub(lambda m: m.group(2), s)
    s = _EM.sub(lambda m: m.group(1), s)
    s = _TAG.sub("", s).replace("`", "")
    return " ".join(s.split())


def _clip(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    cut = s[:limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return (cut or s[:limit - 1]) + "…"


def description_of(text: str, limit: int = DESCRIPTION_CHARS) -> str:
    """One line saying what a page is, for list_pages: the frontmatter's
    `description` (or `summary`) when it has one, else the first sentence of the
    page's first paragraph of prose, skipping the title, other headings, code,
    comments, tables, rules and images. Plain text, at most `limit` characters
    (AUTHORED_CHARS for a frontmatter description); empty when the page has no prose."""
    lines = keep_lines(text)
    fm = _frontmatter_lines(lines)
    if not fm and lines and lines[0].rstrip("\r\n") == "---" and len(text) >= DESCRIPTION_SOURCE:
        return ""                  # frontmatter longer than the part list_pages reads
    for i in range(1, max(fm - 1, 1)):
        m = _FM_KEY.match(lines[i].rstrip("\r\n"))
        if not m:
            continue
        value = m.group(2)
        if value[:1] in (">", "|"):                    # block scalar: the indented lines after
            block = []
            for ln in lines[i + 1:fm - 1]:
                if ln.strip() and ln[:1] not in (" ", "\t"):
                    break
                block.append(ln.strip())
            value = " ".join(b for b in block if b)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        value = _plain(value)
        if value:
            return _clip(value, max(limit, AUTHORED_CHARS))
    masked = keep_lines(mask_code(text))
    para: list[str] = []
    for i in range(fm, len(lines)):
        raw = lines[i].rstrip("\r\n")
        code_or_comment = raw.strip() and not masked[i].strip()
        if not raw.strip() or code_or_comment or ATX_HEADING.match(masked[i].rstrip("\r\n")):
            if para:
                break
            continue
        if _NOT_PROSE.match(raw):
            if para:
                break
            continue
        bullet = _BULLET.match(raw)
        if bullet and para:                            # a list item ends the paragraph
            break
        para.append(_QUOTE.sub("", raw[bullet.end():] if bullet else raw))
        if bullet:                                     # a list's first item stands alone
            break
    s = _plain(" ".join(para))
    if not s:
        return ""
    m = _SENTENCE_END.search(s)
    if m:
        s = s[:m.end()]
    return _clip(s, limit)


def read_pages(root: str | Path, keep_text: bool = True) -> dict[str, Page]:
    root = Path(root)
    pages: dict[str, Page] = {}
    for f in sorted(root.rglob("*.md")):
        if any(part.startswith(".") for part in f.relative_to(root).parts):
            continue
        rel = f.relative_to(root).as_posix()
        key = rel[:-3]
        text = f.read_text(encoding="utf-8", errors="replace")
        pages[key] = Page(
            path=key,
            file=rel,
            title=title_of(text, key),
            folder=key.rsplit("/", 1)[0] if "/" in key else "",
            words=len(text.split()),
            text=text if keep_text else "",
        )
    return pages


def build(pages: dict[str, Page]) -> Graph:
    g = Graph(pages=pages)
    index = PageIndex(pages)
    seen: set[tuple[str, str]] = set()
    for key, page in pages.items():
        for _pos, target, rel, base in page_links(key, page.text):
            resolved = resolve_keys(target, rel, base, index)
            if resolved is None:
                g.dangling.append((key, target))
                continue
            if resolved == key or (key, resolved) in seen:
                continue
            seen.add((key, resolved))
            g.edges.append((key, resolved))
    return g


def scan(root: str | Path, keep_text: bool = True) -> Graph:
    return build(read_pages(root, keep_text=keep_text))
