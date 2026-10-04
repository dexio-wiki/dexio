"""The Open Knowledge Format's review, source and status fields in a page's
frontmatter (Forrest, 2026-10-04: "Let's do 1-3", after comparing Dexio with
Google's OKF v0.2, github.com/GoogleCloudPlatform/open-knowledge-format):

- `verified`, a list of review events ({by, at}), and the trust tier OKF derives
  from it: unverified, machine-confirmed (only agents or processes), or
  human-reviewed (a `human:<id>` actor). Dexio adds what a file cannot say on its
  own: whether the page changed after its last review, from the page's own
  last-change time.
- `sources`, a list of {id, resource, title, author, last_modified, usage_count};
  a footnote whose label is a source's id cites it (OKF section 5.1). Plain
  strings are kept too, as the free-text source lists many wikis already write.
- `status`: draft, stable or deprecated. No status means stable.

`add_review` writes a person's review into `verified`, for the app's Mark
reviewed. Only the YAML these fields need is read: plain and quoted scalars,
flow lists and maps, and block lists and maps nested by indentation. Anything
else is skipped and never an error: OKF says a page is never rejected for its
frontmatter (section 11).
"""
from __future__ import annotations

import datetime as _dt
import json
import re

from .parse import _frontmatter_lines, keep_lines

STATUSES = ("draft", "stable", "deprecated")
HUMAN, PROCESS = "human:", "process:"
# A page that changes this soon after its review was changed by the review.
REVIEW_GRACE = 5.0
MAX_SOURCES = 100
MAX_REVIEWS = 50
# Frontmatter past this is not read (a page's head; health.stale_after reads 20000).
READ_CHARS = 20000

# A mapping key and what follows its colon: a quoted key, or a plain one that
# does not open a list item, a flow collection or a comment.
_KEY = re.compile(r"""^(?:"([^"]*)"|'([^']*)'|([^\s#'"\[\]{},:-][^:#]*?|-[^\s:#][^:#]*?))[ \t]*:(?:[ \t]+|$)(.*)$""")
_PLAIN_OK = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_:./@+\-]*$")


# ---- reading ---------------------------------------------------------------

def _strip_comment(s: str) -> str:
    """s without a trailing ` # comment` that sits outside quotes."""
    quote = None
    for i, ch in enumerate(s):
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'" and (i == 0 or s[i - 1] in " \t[{,:"):
            quote = ch
        elif ch == "#" and (i == 0 or s[i - 1] in " \t"):
            return s[:i].rstrip()
    return s.rstrip()


def _scalar(raw: str):
    s = raw.strip()
    if len(s) >= 2 and s[0] == s[-1] == '"':
        try:
            return json.loads(s)
        except ValueError:
            return s[1:-1]
    if len(s) >= 2 and s[0] == s[-1] == "'":
        return s[1:-1].replace("''", "'")
    if s in ("", "~", "null", "Null", "NULL"):
        return None
    if re.fullmatch(r"[+-]?\d{1,15}", s):
        return int(s)
    return s


class _Flow:
    """A flow collection or scalar, [a, {b: c}] or "x", read from one string."""

    def __init__(self, s: str):
        self.s, self.i = s, 0

    def ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t\r\n":
            self.i += 1

    def value(self, stops: str):
        self.ws()
        if self.i >= len(self.s):
            return None
        ch = self.s[self.i]
        if ch == "[":
            return self.seq()
        if ch == "{":
            return self.map()
        if ch in "\"'":
            return self.quoted(ch)
        # A plain scalar runs to the next stop. Not inside parentheses: YAML
        # would split "read 2026-09-27 (sections 5, 6.1)" at its commas, but a
        # source list written that way means one source, so it stays one.
        start = self.i
        depth = 0
        while self.i < len(self.s) and (depth > 0 or self.s[self.i] not in stops):
            if self.s[self.i] == "(":
                depth += 1
            elif self.s[self.i] == ")" and depth:
                depth -= 1
            elif self.s[self.i] in "]}" and depth:
                break                          # an unclosed "(" stops at the collection's end
            self.i += 1
        return _scalar(self.s[start:self.i])

    def quoted(self, q: str):
        start = self.i
        self.i += 1
        while self.i < len(self.s):
            if self.s[self.i] == "\\" and q == '"':
                self.i += 2
                continue
            if self.s[self.i] == q:
                if q == "'" and self.s[self.i + 1:self.i + 2] == "'":
                    self.i += 2
                    continue
                self.i += 1
                break
            self.i += 1
        return _scalar(self.s[start:self.i])

    def seq(self) -> list:
        self.i += 1
        out = []
        while True:
            self.ws()
            if self.i >= len(self.s):
                return out
            if self.s[self.i] == "]":
                self.i += 1
                return out
            if self.s[self.i] == ",":
                self.i += 1
                continue
            before = self.i
            out.append(self.value(",]"))
            if self.i == before:
                self.i += 1

    def map(self) -> dict:
        self.i += 1
        out: dict = {}
        while True:
            self.ws()
            if self.i >= len(self.s):
                return out
            ch = self.s[self.i]
            if ch == "}":
                self.i += 1
                return out
            if ch == ",":
                self.i += 1
                continue
            if ch in "\"'":
                key = self.quoted(ch)
            else:
                start = self.i
                while self.i < len(self.s) and self.s[self.i] not in ",}" and not (
                        self.s[self.i] == ":" and self.s[self.i + 1:self.i + 2] in ("", " ", "\t", ",", "}")):
                    self.i += 1
                key = self.s[start:self.i].strip()
            self.ws()
            value = None
            if self.s[self.i:self.i + 1] == ":":
                self.i += 1
                value = self.value(",}")
            if key not in (None, ""):
                out[str(key)] = value


def _inline(s: str):
    s = s.strip()
    if s and s[0] in "[{":
        return _Flow(s).value("")
    return _scalar(s)


def _balanced(s: str) -> bool:
    depth, quote = 0, None
    for ch in s:
        if quote:
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth <= 0


class _Block:
    """Block mappings and sequences nested by indentation."""

    def __init__(self, lines: list[str]):
        self.lines = [ln.rstrip("\r\n").replace("\t", "  ") for ln in lines]

    @staticmethod
    def indent(line: str) -> int:
        return len(line) - len(line.lstrip(" "))

    def skip(self, i: int) -> int:
        while i < len(self.lines) and (not self.lines[i].strip() or self.lines[i].lstrip().startswith("#")):
            i += 1
        return i

    def node(self, i: int, ind: int):
        i = self.skip(i)
        if i >= len(self.lines):
            return None, i
        if self.lines[i].lstrip().startswith("-") and self.lines[i].lstrip()[1:2] in ("", " "):
            return self.seq(i, self.indent(self.lines[i]))
        return self.map(i, self.indent(self.lines[i]))

    def rest_value(self, rest: str, i: int, ind: int):
        """The value written after `key:` on line i - 1, which may go on below."""
        rest = _strip_comment(rest)
        if rest and rest[0] in "|>":
            j, block = i, []
            while j < len(self.lines) and (not self.lines[j].strip() or self.indent(self.lines[j]) > ind):
                block.append(self.lines[j].strip())
                j += 1
            joiner = "\n" if rest[0] == "|" else " "
            return joiner.join(block).strip(), j
        if rest and rest[0] in "[{":
            j = i
            while not _balanced(rest) and j < len(self.lines) and self.indent(self.lines[j]) > ind:
                rest += " " + _strip_comment(self.lines[j].strip())
                j += 1
            return _inline(rest), j
        if rest:
            return _scalar(rest), i
        j = self.skip(i)
        if j < len(self.lines):
            nxt = self.lines[j]
            deeper = self.indent(nxt) > ind
            same_seq = self.indent(nxt) == ind and nxt.lstrip().startswith("- ")
            if deeper or same_seq:
                return self.node(j, ind)
        return None, i

    def map(self, i: int, ind: int):
        out: dict = {}
        while True:
            i = self.skip(i)
            if i >= len(self.lines):
                return out, i
            line = self.lines[i]
            here = self.indent(line)
            if here < ind:
                return out, i
            if here > ind:
                i += 1                         # stray deeper line: not ours to read
                continue
            if line.lstrip().startswith("- "):
                return out, i
            m = _KEY.match(line.strip())
            if not m:
                i += 1
                continue
            key = next(g for g in m.groups()[:3] if g is not None).strip()
            value, i = self.rest_value(m.group(4), i + 1, ind)
            out.setdefault(key, value)

    def seq(self, i: int, ind: int):
        out: list = []
        while True:
            i = self.skip(i)
            if i >= len(self.lines):
                return out, i
            line = self.lines[i]
            stripped = line.lstrip()
            if self.indent(line) != ind or not (stripped == "-" or stripped.startswith("- ")):
                return out, i
            content = stripped[1:].lstrip()
            col = len(line) - len(content)
            if not content:
                value, i = self.node(i + 1, ind)
                out.append(value)
                continue
            if content[:1] not in "[{\"'" and _KEY.match(content):
                self.lines[i] = " " * col + content     # the item's first key, then its others
                value, i = self.map(i, col)
                out.append(value)
                continue
            value, i = self.rest_value(content, i + 1, ind)
            out.append(value)


def fields(text: str) -> dict:
    """The page's frontmatter as a mapping, {} when it has none."""
    lines = keep_lines((text or "")[:READ_CHARS])
    fm = _frontmatter_lines(lines)
    if fm < 2:
        return {}
    try:
        value, _ = _Block(lines[1:fm - 1]).node(0, 0)
    except (RecursionError, IndexError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def parse_time(value) -> float | None:
    """An ISO 8601 date or datetime as Unix seconds; a date alone is that day's
    start in UTC, as is a time with no offset."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return None
    s = str(value or "").strip()
    if not s:
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            when = _dt.datetime.strptime(s, "%Y-%m-%d")
        else:
            when = _dt.datetime.fromisoformat(s.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=_dt.timezone.utc)
    return when.timestamp()


def iso(ts: float) -> str:
    return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def kind_of(actor: str) -> str:
    """OKF's actor convention: human:<id>, process:<id>, or an agent's name."""
    a = str(actor or "")
    return "human" if a.startswith(HUMAN) else "process" if a.startswith(PROCESS) else "agent"


def status(fm: dict) -> str | None:
    v = fm.get("status")
    s = str(v).strip().lower() if isinstance(v, str) else ""
    return s if s in STATUSES else None


def reviews(fm: dict) -> list[dict]:
    """`verified` as a list of {by, at (Unix seconds or None), kind}. A bare
    mapping is a one-element list (OKF section 5.2)."""
    v = fm.get("verified")
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:MAX_REVIEWS]:
        if isinstance(item, dict) and item.get("by") not in (None, ""):
            by = str(item["by"]).strip()
            out.append({"by": by, "at": parse_time(item.get("at")), "kind": kind_of(by)})
        elif isinstance(item, str) and item.strip():
            out.append({"by": item.strip(), "at": None, "kind": kind_of(item.strip())})
    return out


def tier(revs: list[dict]) -> str:
    if any(r["kind"] == "human" for r in revs):
        return "human-reviewed"
    return "machine-confirmed" if revs else "unverified"


def sources(fm: dict) -> list[dict]:
    """`sources` as a list: {id, resource, title, author, last_modified,
    usage_count} for OKF's entries (resource required), {text} for a plain one."""
    v = fm.get("sources")
    if isinstance(v, (str, dict)):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for item in v[:MAX_SOURCES]:
        if isinstance(item, dict):
            res = item.get("resource")
            if res in (None, ""):
                continue
            s = {"resource": str(res)}
            for k in ("id", "title", "author", "last_modified"):
                if item.get(k) not in (None, ""):
                    s[k] = str(item[k])
            if isinstance(item.get("usage_count"), int) and not isinstance(item["usage_count"], bool):
                s["usage_count"] = item["usage_count"]
            out.append(s)
        elif item not in (None, "") and not isinstance(item, list):
            out.append({"text": str(item)})
    return out


def generated(fm: dict) -> dict | None:
    g = fm.get("generated")
    if isinstance(g, dict) and g.get("by") not in (None, ""):
        out: dict = {"by": str(g["by"]), "kind": kind_of(str(g["by"]))}
        at = parse_time(g.get("at"))
        if at is not None:
            out["at"] = at
        return out
    return None


def review_state(revs: list[dict], updated_at: float | None) -> dict | None:
    """The page's review as the app and the MCP header show it: the tier, the
    latest review of that tier, everyone who reviewed at that tier, and whether
    the page changed after that review."""
    if not revs:
        return None
    t = tier(revs)
    best = [r for r in revs if (r["kind"] == "human") == (t == "human-reviewed")]
    last = max(best, key=lambda r: r["at"] or 0)
    out = {"tier": t, "by": last["by"], "at": last["at"],
           "reviewers": list(dict.fromkeys(r["by"] for r in best))}
    out["edited_since"] = bool(updated_at and last["at"] is not None
                               and float(updated_at) > last["at"] + REVIEW_GRACE)
    return out


def summary(text: str, updated_at: float | None = None) -> dict:
    """What the page says about itself, only the keys it has: status, review
    (review_state), sources and generated."""
    fm = fields(text)
    if not fm:
        return {}
    out: dict = {}
    st = status(fm)
    if st:
        out["status"] = st
    rv = review_state(reviews(fm), updated_at)
    if rv:
        out["review"] = rv
    src = sources(fm)
    if src:
        out["sources"] = src
    gen = generated(fm)
    if gen:
        out["generated"] = gen
    return out


# ---- writing ---------------------------------------------------------------

def human_actor(name: str, fallback: str) -> str:
    """human:<id> for a person, from their name: Forrest Zhang is human:forrest-zhang."""
    slug = re.sub(r"[^\w]+", "-", str(name or "").lower(), flags=re.UNICODE).strip("-_")
    return HUMAN + (slug or fallback)


def _plain_or_quoted(v) -> str:
    if isinstance(v, bool) or v is None:
        return json.dumps(v)
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    return s if _PLAIN_OK.match(s) else json.dumps(s, ensure_ascii=False)


def _flow_map(entry: dict) -> str:
    return "{ " + ", ".join(f"{k}: {_plain_or_quoted(v)}" for k, v in entry.items()
                            if not isinstance(v, (dict, list))) + " }"


def _raw_entries(fm: dict) -> list[dict]:
    v = fm.get("verified")
    if isinstance(v, dict):
        v = [v]
    if not isinstance(v, list):
        return []
    out = []
    for item in v:
        if isinstance(item, dict) and item.get("by") not in (None, ""):
            out.append(item)
        elif isinstance(item, str) and item.strip():
            out.append({"by": item.strip()})
    return out


def add_review(text: str, actor: str, at: float) -> str:
    """The text with `actor` reviewing it at `at`: one `verified` entry per
    reviewer, so the reviewer's earlier entry gives way to this one. `verified`
    is rewritten as a block list of { by, at } entries, the rest of the
    frontmatter left as written; a page with no frontmatter gets one."""
    entry = {"by": actor, "at": iso(at)}
    lines = keep_lines(text or "")
    fm = _frontmatter_lines(lines)
    if fm < 2:
        return "---\nverified:\n  - " + _flow_map(entry) + "\n---\n" + (text or "")
    kept = [e for e in _raw_entries(fields(text)) if str(e.get("by")).strip() != actor]
    block = "verified:\n" + "".join(f"  - {_flow_map(e)}\n" for e in kept + [entry])
    end = fm - 1                                    # the closing --- line
    start = None
    for i in range(1, end):
        if re.match(r"^verified[ \t]*:", lines[i]):
            start = i
            break
    if start is None:
        before = lines[:end]
        if before and not before[-1].endswith("\n"):
            before[-1] += "\n"
        return "".join(before) + block + "".join(lines[end:])
    stop = start + 1
    while stop < end and (not lines[stop].strip() or lines[stop][:1] in (" ", "\t")
                          or lines[stop].startswith("- ")):
        stop += 1
    while stop > start + 1 and not lines[stop - 1].strip():
        stop -= 1                                   # blank lines after it stay
    return "".join(lines[:start]) + block + "".join(lines[stop:])
