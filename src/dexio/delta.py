"""Page history as diffs: a revision stores what changed since the one before,
not the whole page.

A delta is a JSON list of [start, end, replacement] edits on the old text's
characters, in order and not overlapping: old[start:end] becomes replacement.
make() finds them in two steps. First it trims the text both versions share at
the start and at the end, which is all an append or a one-place edit needs and
costs one pass over the page. Only when what is left differs on both sides does
it run a line diff (difflib) over that middle part, so a page of megabytes that
gained a paragraph never goes through the quadratic path.

apply(old, make(old, new)) == new for any two strings; tests/test_history.py
checks that on random edits.
"""
from __future__ import annotations

import difflib
import json

# Past this many characters on both sides of the changed middle, the line diff
# is skipped and the middle is stored as one replacement: correct, just larger,
# and the caller stores a full copy when a delta is not smaller than the page.
MAX_DIFF_CHARS = 2_000_000


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    lo, hi = 0, n
    # Binary search on slices: C-speed comparisons instead of a Python loop.
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _common_suffix(a: str, b: str, limit: int) -> int:
    lo, hi = 0, limit
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if a[len(a) - mid:] == b[len(b) - mid:]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def make(old: str, new: str) -> list[list]:
    """The edits that turn old into new: [[start, end, replacement], ...]."""
    if old == new:
        return []
    p = _common_prefix(old, new)
    s = _common_suffix(old, new, min(len(old), len(new)) - p)
    a_mid, b_mid = old[p:len(old) - s], new[p:len(new) - s]
    if not a_mid or not b_mid or len(a_mid) + len(b_mid) > MAX_DIFF_CHARS:
        return [[p, len(old) - s, b_mid]]
    # Snap the middle out to whole lines so the line diff lines up with the
    # page's own lines rather than a cut through the first and last of them.
    start = old.rfind("\n", 0, p) + 1
    a_end = old.find("\n", len(old) - s)
    a_end = len(old) if a_end < 0 else a_end + 1
    b_end = new.find("\n", len(new) - s)
    b_end = len(new) if b_end < 0 else b_end + 1
    a_lines = old[start:a_end].splitlines(keepends=True)
    b_lines = new[start:b_end].splitlines(keepends=True)
    edits, a_pos = [], [start]
    for line in a_lines:
        a_pos.append(a_pos[-1] + len(line))
    matcher = difflib.SequenceMatcher(None, a_lines, b_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            edits.append([a_pos[i1], a_pos[i2], "".join(b_lines[j1:j2])])
    return edits


def apply(old: str, edits: list[list]) -> str:
    out, pos = [], 0
    for start, end, text in edits:
        out.append(old[pos:start])
        out.append(text)
        pos = end
    out.append(old[pos:])
    return "".join(out)


def encode(edits: list[list]) -> str:
    return json.dumps(edits, ensure_ascii=False, separators=(",", ":"))


def decode(delta: str) -> list[list]:
    return json.loads(delta)
