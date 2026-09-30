"""Line diffs for the web app's page history: what one revision changed.

`diff(old, new)` returns hunks the way `git diff` shows them: changed lines with a
few lines of context, numbered on both sides. A changed line paired with the line
that replaced it also carries its words split into kept and changed runs, so the
viewer can mark the words that changed inside a long line (most wiki pages wrap a
paragraph as one line, where a line diff alone shows a whole paragraph as new).

Segments are sent as text rather than offsets: Python counts code points and
JavaScript UTF-16 units, so offsets would drift on any emoji.
"""
from __future__ import annotations

import difflib
import re

CONTEXT = 3
# Past this many lines on both sides together, lines are compared whole only.
WORD_DIFF_MAX_LINES = 20000
# A pair of lines is compared word by word only when both are this short.
WORD_DIFF_MAX_CHARS = 4000

_TOKEN = re.compile(r"\s+|\w+|[^\w\s]", re.UNICODE)


def _lines(text: str | None) -> list[str]:
    return (text or "").splitlines()


# Below this share of words in common, two lines are different lines, not one
# line edited, and marking the few words they share would only be noise.
PAIR_MIN_RATIO = 0.3


def _words(a: str, b: str) -> tuple[list, list] | None:
    """The two lines as runs of [changed, text], kept runs have changed=0; None
    when they have too little in common to read as one line edited."""
    ta, tb = _TOKEN.findall(a), _TOKEN.findall(b)
    sm = difflib.SequenceMatcher(None, ta, tb, autojunk=False)
    # share of words in common, spaces not counted: they match between any two lines
    solid = sum(1 for t in ta + tb if not t.isspace())
    kept = sum(1 for m in sm.get_matching_blocks() for t in ta[m.a:m.a + m.size]
               if not t.isspace())
    if not solid or 2 * kept / solid < PAIR_MIN_RATIO:
        return None
    out_a: list = []
    out_b: list = []

    def put(out, changed, text):
        if not text:
            return
        if out and out[-1][0] == changed:
            out[-1][1] += text
        else:
            out.append([changed, text])

    for op, i1, i2, j1, j2 in sm.get_opcodes():
        if op == "equal":
            put(out_a, 0, "".join(ta[i1:i2]))
            put(out_b, 0, "".join(tb[j1:j2]))
        else:
            put(out_a, 1, "".join(ta[i1:i2]))
            put(out_b, 1, "".join(tb[j1:j2]))
    return out_a, out_b


def diff(old: str | None, new: str | None, context: int = CONTEXT) -> dict:
    """Hunks from `old` to `new`, and the number of lines added and removed.

    Each hunk is {"old_start", "new_start", "lines"}; each line is
    {"t": " " | "-" | "+", "a": old line number or None, "b": new line number or
    None, "s": text} plus "w" (the word runs) on a changed line that has a partner.
    """
    a, b = _lines(old), _lines(new)
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    words = len(a) + len(b) <= WORD_DIFF_MAX_LINES
    added = removed = 0
    hunks = []
    for group in sm.get_grouped_opcodes(context):
        lines = []
        for op, i1, i2, j1, j2 in group:
            if op == "equal":
                for k in range(i2 - i1):
                    lines.append({"t": " ", "a": i1 + k + 1, "b": j1 + k + 1, "s": a[i1 + k]})
                continue
            dels = [{"t": "-", "a": i1 + k + 1, "b": None, "s": a[i1 + k]} for k in range(i2 - i1)]
            adds = [{"t": "+", "a": None, "b": j1 + k + 1, "s": b[j1 + k]} for k in range(j2 - j1)]
            removed += len(dels)
            added += len(adds)
            if op == "replace" and words:
                for d, n in zip(dels, adds):
                    if len(d["s"]) <= WORD_DIFF_MAX_CHARS and len(n["s"]) <= WORD_DIFF_MAX_CHARS:
                        runs = _words(d["s"], n["s"])
                        if runs:
                            d["w"], n["w"] = runs
            lines.extend(dels)
            lines.extend(adds)
        first = group[0]
        hunks.append({"old_start": first[1] + 1, "new_start": first[3] + 1, "lines": lines})
    return {"hunks": hunks, "added": added, "removed": removed}
