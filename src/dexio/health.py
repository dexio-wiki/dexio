"""Wiki health beyond broken links: pages that may be out of date, and pages that
name another page without linking to it. Both are information, not problems:
only a broken link is a defect (Forrest, 2026-09-26).

Stale. A page is stale when its frontmatter `stale_after` has passed (the Open
Knowledge Format's field: an ISO date or instant; the page is stale on or after
it), or when it has gone `stale_days` without a change while at least one page
it links to has changed since. The second is a hint, not a verdict: a page that
summarises others is the one most likely to fall behind them.

Unlinked mentions. Page A names page B (B's title, in A's prose) but has no link
to B. Titles are matched as whole words, longest title first, outside code,
comments, frontmatter and existing links. A one-word title must match its case
exactly, so a page called "Notion" is not found in "a notion of", and one-word
titles that are ordinary words ("Log", "Index") are not matched at all. A title
two pages share names neither, since the link could go to either. A title with
a trailing parenthetical, "Open Knowledge Format (OKF)", is also matched without
it, unless another page already has that shorter title.
"""
from __future__ import annotations

import datetime as _dt
import re

from .parse import MDLINK, WIKILINK, _frontmatter_lines, keep_lines, mask_code
from .search import STOPWORDS

STALE_DAYS = 90
# Titles too generic to mean one page when they appear in prose.
GENERIC = frozenset("""
readme index log logs schema notes note overview home todo todos changelog summary
introduction intro glossary about faq misc draft drafts inbox archive template
""".split())
MAX_TITLE_WORDS = 12
_WORD = re.compile(r"[^\W_]+")
_STALE_KEY = re.compile(r"^stale_after[ \t]*:[ \t]*(.*?)[ \t]*$", re.I)
_PAREN = re.compile(r"\s*\([^()]*\)\s*$")


# ---- stale ---------------------------------------------------------------------

def stale_after(text: str) -> float | None:
    """The page's frontmatter `stale_after` as a UTC timestamp, or None when it has
    none or it does not parse. A date alone means the start of that day, UTC."""
    lines = keep_lines(text[:20000])
    fm = _frontmatter_lines(lines)
    for i in range(1, max(fm - 1, 1)):
        m = _STALE_KEY.match(lines[i].rstrip("\r\n"))
        if not m:
            continue
        raw = m.group(1).split("#", 1)[0].strip().strip("'\"").strip()
        try:
            if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
                when = _dt.datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=_dt.timezone.utc)
            else:
                when = _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=_dt.timezone.utc)
        except ValueError:
            return None
        return when.timestamp()
    return None


def stale_pages(pages: list[dict], edges: list[tuple[str, str]], now: float,
                stale_days: int = STALE_DAYS) -> list[dict]:
    """pages: dicts with path, updated_at and stale_after (a timestamp or None).
    edges: (src, dst) for every link that resolves. Newest reason first per page;
    the list is ordered by how long ago the page last changed, oldest first."""
    updated = {p["path"]: float(p["updated_at"] or 0) for p in pages}
    links_to: dict[str, set[str]] = {}
    for src, dst in edges:
        if src != dst:
            links_to.setdefault(src, set()).add(dst)
    cutoff = now - stale_days * 86400
    out = []
    for p in pages:
        path, when = p["path"], updated[p["path"]]
        entry = None
        if p.get("stale_after") is not None and now >= p["stale_after"]:
            entry = {"path": path, "reason": "past its stale_after date",
                     "stale_after": _date(p["stale_after"])}
        elif when and when <= cutoff:
            newer = sorted((d for d in links_to.get(path, ()) if updated.get(d, 0) > when),
                           key=lambda d: (-updated[d], d))
            if newer:
                entry = {"path": path,
                         "reason": f"unchanged {int((now - when) // 86400)} days; pages it links"
                                   f" to changed since",
                         "changed_since": newer[:3]}
        if entry:
            entry["updated"] = _date(when)
            out.append(entry)
    return sorted(out, key=lambda e: (updated[e["path"]], e["path"]))


def _date(ts: float) -> str:
    return _dt.datetime.fromtimestamp(float(ts), _dt.timezone.utc).strftime("%Y-%m-%d") if ts else ""


# ---- unlinked mentions ------------------------------------------------------------

class Mentions:
    """Finds which pages a text names by title. Built once per wiki state from
    {path: title}; find() is linear in the text's length."""

    def __init__(self, titles: dict[str, str]):
        full: dict[tuple, set[str]] = {}
        short: dict[tuple, set[str]] = {}
        for path, title in titles.items():
            for bucket, t in ((full, title), (short, _PAREN.sub("", title or ""))):
                key = self._key(t)
                if key:
                    bucket.setdefault(key, set()).add(path)
        terms: dict[tuple, str] = {}
        for key, paths in full.items():
            if len(paths) == 1:
                terms[key] = next(iter(paths))
        taken = set(full)
        for key, paths in short.items():
            if key not in taken and len(paths) == 1:
                terms[key] = next(iter(paths))
        self.terms = terms
        # The term lengths that start with each first word, longest first, so find()
        # tries only what could match: one-word terms by their exact word, longer
        # ones by their lower-cased first word.
        starts: dict[tuple[int, str], set[int]] = {}
        for key in terms:
            first = (1, key[0]) if len(key) == 1 else (0, key[0])
            starts.setdefault(first, set()).add(len(key))
        self.exact_lengths = {w: sorted(n, reverse=True) for (kind, w), n in starts.items() if kind}
        self.lower_lengths = {w: sorted(n, reverse=True) for (kind, w), n in starts.items()
                              if not kind}

    @staticmethod
    def _key(title: str) -> tuple | None:
        """The words a title is matched by. One-word titles keep their case
        (matched exactly); longer ones are lower-cased (matched in any case)."""
        words = _WORD.findall(title or "")
        if not words or len(words) > MAX_TITLE_WORDS:
            return None
        if len(words) == 1:
            w = words[0]
            if len(w) < 3 or w.lower() in STOPWORDS or w.lower() in GENERIC or w.isdigit():
                return None
            return (w,)
        if all(w.lower() in STOPWORDS for w in words):
            return None
        return tuple(w.lower() for w in words)

    def find(self, text: str, src: str, linked: set[str], limit: int = 10) -> list[str]:
        """Pages `text` (the page at `src`) names without linking to, in the order
        they first appear."""
        if not self.terms:
            return []
        words = _WORD.findall(_prose(text))
        lower = [w.lower() for w in words]
        found: list[str] = []
        seen = set(linked) | {src}
        i, total = 0, len(words)
        while i < total and len(found) < limit:
            hit, size = None, 0
            for n in self.lower_lengths.get(lower[i], ()):
                if n <= total - i:
                    path = self.terms.get(tuple(lower[i:i + n]))
                    if path is not None:
                        hit, size = path, n
                        break
            if hit is None and words[i] in self.exact_lengths:
                hit, size = self.terms[(words[i],)], 1
            if hit is not None:
                if hit not in seen:
                    seen.add(hit)
                    found.append(hit)
                i += size
            else:
                i += 1
        return found


def _prose(text: str) -> str:
    """The text with code, comments, frontmatter and links blanked out, offsets
    kept, so only words a reader sees as plain prose remain."""
    out = mask_code(text)
    lines = keep_lines(out)
    fm = _frontmatter_lines(lines)
    if fm:
        cut = sum(len(line) for line in lines[:fm])
        out = re.sub(r"[^\n]", " ", out[:cut]) + out[cut:]
    for pattern in (WIKILINK, MDLINK):
        out = pattern.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), out)
    return out


def mention_summary(per_page: dict[str, list[str]], limit: int = 30) -> list[dict]:
    """Across a wiki: each page that is named without a link somewhere, with how
    many pages do it and the first few of them, most mentioned first."""
    by_target: dict[str, list[str]] = {}
    for src in sorted(per_page):
        for dst in per_page[src]:
            by_target.setdefault(dst, []).append(src)
    ranked = sorted(by_target.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    return [{"path": dst, "unlinked_on": len(srcs), "for_example": srcs[:5]} for dst, srcs in ranked]


def wanted(dangling: list[dict], limit: int = 20) -> list[dict]:
    """Broken-link targets ranked by how many pages link to them: the missing
    pages the wiki most wants written (or the links most in need of fixing)."""
    by_target: dict[str, list[str]] = {}
    for d in dangling:
        srcs = by_target.setdefault(d["target"], [])
        if d["source"] not in srcs:
            srcs.append(d["source"])
    ranked = sorted(by_target.items(), key=lambda kv: (-len(kv[1]), kv[0]))[:limit]
    return [{"target": t, "linked_from": len(srcs), "for_example": srcs[:3]} for t, srcs in ranked]
