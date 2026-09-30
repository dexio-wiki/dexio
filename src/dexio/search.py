"""Word search over a wiki's pages, shared by the MCP tool and the web app.

A query is matched word by word, not as one phrase: "how do I deploy the relay"
finds the page that says "Deploying the relay" even though that string appears
nowhere. Common words (how, do, the) are dropped unless the query is nothing
but common words. Each remaining word must appear somewhere in the page (its
path, title or text), as a substring, so "deploy" also finds "deployment".
"Quoted text" is one term matched exactly as written.

Results are ranked: pages with every word first; then the page named for the
query (its title or the last part of its path is the query: "Northwind" finds
the page titled Northwind before the long pages that merely mention it); then
by how many words hit the path or title; then by the most words found on a
single line (words near each other beat words scattered across the page);
then by BM25, which weighs how often the words appear against the page's
length and against how many pages use them, so a long page does not win on
sheer size. When no page has every word, the pages with some of them come
back flagged partial, so a long question never returns a false zero.

Regex search is separate (`regex_search`) and behaves as it always has.
"""
from __future__ import annotations

import re
from math import log
from dataclasses import dataclass

STOPWORDS = frozenset("""
a about am an and any are as at be been but by can could did do does doing for from
had has have how i if in into is it its just me my of on or our should so than that
the their them then there these they this those to was we were what when where which
who whom why will with would you your
""".split())

# Punctuation trimmed from the ends of a word; inside it stays, so "e.g."
# loses nothing that matters and "c++", "v1.2" and "api/v1" survive.
_EDGE = "\"'`.,;:!?()[]{}<>*"
_TERM = re.compile(r'"([^"]+)"|(\S+)')
_WORD = re.compile(r"[^\W_]+")
MAX_LINE = 240
# BM25's usual constants: how fast repeats stop counting, and how much a
# page's length discounts them.
K1, B = 1.2, 0.75


@dataclass
class Term:
    text: str       # as matched (lower-cased unless case-sensitive)
    phrase: bool    # came from "quotes"


def parse_query(q: str, case_sensitive: bool = False) -> list[Term]:
    """Split a query into terms: quoted phrases whole, other words one by one,
    common words dropped unless nothing else is left. Duplicates removed."""
    fold = (lambda s: s) if case_sensitive else str.lower
    phrases, words = [], []
    for m in _TERM.finditer(q or ""):
        if m.group(1) is not None:
            p = " ".join(m.group(1).split())
            if p:
                phrases.append(fold(p))
        else:
            w = m.group(2).strip(_EDGE)
            if w:
                words.append(fold(w))
    kept = [w for w in words if w.lower() not in STOPWORDS]
    if not kept and not phrases:
        kept = words
    out, seen = [], set()
    for text, phrase in [(p, True) for p in phrases] + [(w, False) for w in kept]:
        if text not in seen:
            seen.add(text)
            out.append(Term(text, phrase))
    return out


def _clip(line: str, at: int) -> str:
    line = line.strip()
    if len(line) <= MAX_LINE:
        return line
    c = max(0, at - 100)
    return ("..." if c else "") + line[c:c + MAX_LINE] + "..."


def _is_name(name: str, terms: list[Term]) -> bool:
    """True when `name` (already folded) is the query: every term is in it,
    and every word of it other than common ones holds a term. "Deploying the
    relay" is named for "deploy relay"; "Relay runbook" is not."""
    words = [w for w in _WORD.findall(name) if w.lower() not in STOPWORDS]
    if not words or not all(t.text in name for t in terms):
        return False
    parts = [set(_WORD.findall(t.text)) for t in terms]
    return all(any(t.text in w or w in p for t, p in zip(terms, parts)) for w in words)


def search_pages(pages, query: str, *, case_sensitive: bool = False, folder: str = "",
                 per_page: int = 5) -> dict:
    """Word search. `pages` is an iterable of objects with path, title and text.
    Returns {terms, matched_all, results}; each result has path, title,
    words_matched (which terms it has), match_count (lines holding any term),
    path_or_title_match and up to per_page matching lines, best lines first."""
    terms = parse_query(query, case_sensitive)
    if not terms:
        return {"terms": [], "matched_all": True, "results": []}
    fold = (lambda s: s) if case_sensitive else str.lower
    prefix = folder.strip("/")
    hits, scanned, total_len = [], 0, 0
    df = {t.text: 0 for t in terms}
    for page in pages:
        if prefix and not page.path.startswith(prefix + "/"):
            continue
        title = fold(page.title or "")
        head = fold(page.path) + "\n" + title
        text = page.text or ""
        body = fold(text)
        length = len(text.split())
        scanned += 1
        total_len += length
        found = [t for t in terms if t.text in head or t.text in body]
        if not found:
            continue
        tf = {t.text: body.count(t.text) for t in found}
        for t in found:
            if tf[t.text]:
                df[t.text] += 1
        in_head = sum(1 for t in found if t.text in head)
        named = _is_name(title, terms) or _is_name(fold(page.path.rsplit("/", 1)[-1]), terms)
        # Score each line by how many distinct terms it holds.
        scored, total = [], 0
        lines = text.split("\n")
        folded = body.split("\n")
        for i, (raw, low) in enumerate(zip(lines, folded), 1):
            on = [t for t in found if t.text in low]
            if not on:
                continue
            total += 1
            first = min(low.find(t.text) for t in on)
            scored.append((len(on), sum(low.count(t.text) for t in on), i, raw, first))
        best_line = max((s[0] for s in scored), default=0)
        scored.sort(key=lambda s: (-s[0], s[2]))
        hits.append({
            "path": page.path, "title": page.title, "words_matched": [t.text for t in found],
            "match_count": total, "path_or_title_match": in_head > 0,
            "matches": [{"line": s[2], "text": _clip(s[3], s[4])} for s in scored[:per_page]],
            "_rank": [-len(found), not named, -in_head, -best_line, 0.0, page.path],
            "_tf": tf, "_len": length,
        })
    # BM25 needs every page's length and every term's page count, so it is
    # filled in once the scan is done.
    avg = (total_len / scanned) if scanned else 1.0
    for h in hits:
        norm = K1 * (1 - B + B * h["_len"] / (avg or 1.0))
        score = 0.0
        for term, n in h["_tf"].items():
            if n:
                idf = log(1 + (scanned - df[term] + 0.5) / (df[term] + 0.5))
                score += idf * n * (K1 + 1) / (n + norm)
        h["_rank"][4] = -score
    hits.sort(key=lambda h: h["_rank"])
    for h in hits:
        del h["_rank"], h["_tf"], h["_len"]
    everything = len(terms)
    full = [h for h in hits if len(h["words_matched"]) == everything]
    if full:
        return {"terms": [t.text for t in terms], "matched_all": True, "results": full}
    return {"terms": [t.text for t in terms], "matched_all": False, "results": hits}


def regex_search(pages, pat: re.Pattern, *, folder: str = "", per_page: int = 5) -> list[dict]:
    """Every page whose path, title or text matches `pat`, each matching line
    once with its line number; path or title matches first, then match count."""
    prefix = folder.strip("/")
    hits = []
    for page in pages:
        if prefix and not page.path.startswith(prefix + "/"):
            continue
        text = page.text
        head = bool(pat.search(page.path) or pat.search(page.title))
        lines, total = [], 0
        last_start, pos, line_no = -1, 0, 1
        for m in pat.finditer(text):
            if m.start() == m.end():
                continue
            a = text.rfind("\n", 0, m.start()) + 1
            if a == last_start:
                continue                    # one entry per line, however many matches
            last_start = a
            line_no += text.count("\n", pos, a)
            pos = a
            total += 1
            if len(lines) < per_page:
                b = text.find("\n", m.end())
                b = len(text) if b < 0 else b
                lines.append({"line": line_no, "text": _clip(text[a:b], m.start() - a)})
        if head or total:
            hits.append({"path": page.path, "title": page.title, "match_count": total,
                         "path_or_title_match": head, "matches": lines})
    hits.sort(key=lambda h: (not h["path_or_title_match"], -h["match_count"], h["path"]))
    return hits
