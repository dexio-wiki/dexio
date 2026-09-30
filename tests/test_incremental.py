"""Writes touch only what they affect, and still leave the wiki exactly as a full
rebuild would.

A change through MCP writes the pages it names, their links, and the links
elsewhere that a created, deleted or moved page can redirect (db.apply_changes).
Link resolution is global (a unique page name resolves from anywhere), so the
risk is a link elsewhere left pointing at the wrong page. These tests run
thousands of random changes, with folder and name collisions, relative links,
aliases, anchors, markdown links, self-links, code examples and batched moves,
and after every one compare the stored graph and totals with build() over the
same pages. DEXIO_RANDOM_STEPS raises the count.
"""
import os
import random
import time

import pytest

from dexio.parse import build
from dexio.server import db

FOLDERS = ["", "a", "b", "a/c"]
NAMES = ["x", "y", "z", "w", "v"]


def _path(rng) -> str:
    folder, name = rng.choice(FOLDERS), rng.choice(NAMES)
    return f"{folder}/{name}" if folder else name


def _target(rng, model) -> str:
    return rng.choice([
        lambda: rng.choice(sorted(model)) if model else _path(rng),  # an existing path
        lambda: rng.choice(NAMES),                                     # a bare name
        lambda: _path(rng),                                            # any path
        lambda: "../" + rng.choice(NAMES),                             # up a folder
        lambda: "c/" + rng.choice(NAMES),                              # down a folder
        lambda: "missing/" + rng.choice(NAMES),
        lambda: "..",
    ])()


def _text(rng, model, path) -> str:
    parts = [f"# {path} {rng.randint(0, 9999)}"]
    for _ in range(rng.randint(0, 6)):
        t, style = _target(rng, model), rng.random()
        if style < 0.45:
            parts.append(f"[[{t}]]")
        elif style < 0.6:
            parts.append(f"[[{t}|alias]]")
        elif style < 0.7:
            parts.append(f"[[{t}#part]]")
        elif style < 0.82:
            parts.append(f"[see]({t}.md)")
        elif style < 0.9:
            parts.append(f"[see]({t}.md#part)")
        else:
            parts.append(f"`[[{t}]]`")                                 # code: not a link
    if rng.random() < 0.2:
        parts.append(f"[[{path}]]")                                    # self-link
    return "\n\n".join(parts) + "\n"


def _check(conn, key, model) -> None:
    g = build(dict(model))
    s = g.stats()
    edges = {(r[0], r[1]) for r in conn.execute("SELECT src, dst FROM edges WHERE project=?", (key,))}
    assert edges == set(g.edges)
    dangling = sorted((r[0], r[1]) for r in conn.execute(
        "SELECT src, target FROM dangling WHERE project=?", (key,)))
    assert dangling == sorted(g.dangling)
    if model:
        row = conn.execute("SELECT pages, links, words, dangling FROM projects WHERE name=?",
                           (key,)).fetchone()
        assert tuple(row) == (s["pages"], s["links"], s["words"], s["dangling"])
    stored = {r[0]: r[1] for r in conn.execute("SELECT path, text FROM pages WHERE project=?", (key,))}
    assert stored == {p: pg.text for p, pg in model.items()}
    degree = {n["id"]: n["degree"] for n in db.graph(conn, key)["nodes"]}
    assert degree == g.degree()


@pytest.mark.parametrize("seed", [1, 2, 3, 4])
def test_random_changes_match_a_full_rebuild(tmp_path, seed):
    rng = random.Random(seed)
    conn = db.connect(tmp_path / "x.db")
    key = db.key(1, "w")
    model: dict = {}
    for _ in range(int(os.environ.get("DEXIO_RANDOM_STEPS", "500"))):
        op = rng.random()
        changes: dict = {}
        if op < 0.02:                                       # a push now and then
            db.commit_pages(conn, key, dict(model), source="test", op="push", is_push=True)
            _check(conn, key, model)
            continue
        if op < 0.35 or not model:                          # create or overwrite
            p = _path(rng)
            changes[p] = db.make_page(p, _text(rng, model, p))
        elif op < 0.6:                                      # edit
            p = rng.choice(sorted(model))
            changes[p] = db.make_page(p, _text(rng, model, p), model[p].file)
        elif op < 0.75:                                     # delete
            changes[rng.choice(sorted(model))] = None
        elif op < 0.9:                                      # move, with other pages rewritten
            p, q = rng.choice(sorted(model)), _path(rng)
            if q in model:
                continue
            changes[p] = None
            changes[q] = db.make_page(q, model[p].text)
            for other in rng.sample(sorted(model), min(2, len(model))):
                if other != p:
                    changes[other] = db.make_page(other, _text(rng, model, other), model[other].file)
        else:                                               # a batch
            for _ in range(rng.randint(2, 5)):
                p = _path(rng)
                changes[p] = None if p in model and rng.random() < 0.4 \
                    else db.make_page(p, _text(rng, model, p))
        db.apply_changes(conn, key, changes, source="test", op="write")
        for p, page in changes.items():
            if page is None:
                model.pop(p, None)
            else:
                model[p] = page
        _check(conn, key, model)


def test_a_write_does_not_read_or_rewrite_the_rest_of_the_wiki(tmp_path):
    """The point of it: in a 3,000-page wiki a one-page write stays fast, where a
    full rebuild (the old path) grows with the wiki."""
    conn = db.connect(tmp_path / "x.db")
    key = db.key(1, "big")
    pages = {f"f{i % 30}/p{i}": db.make_page(f"f{i % 30}/p{i}",
                                            f"# P{i}\n\n[[p{(i * 7) % 3000}]] [[p{(i + 1) % 3000}]]\n")
             for i in range(3000)}
    db.commit_pages(conn, key, pages, source="test", op="push", is_push=True)
    t0 = time.perf_counter()
    for i in range(20):
        p = f"f{i % 30}/p{i}"
        db.apply_changes(conn, key, {p: db.make_page(p, pages[p].text + f"\nedit {i} [[p{i + 5}]]\n")},
                         source="test", op="edit")
    per_write = (time.perf_counter() - t0) / 20
    t0 = time.perf_counter()
    whole = db.load_pages(conn, key)
    db.commit_pages(conn, key, whole, source="test", op="edit")
    full = time.perf_counter() - t0
    assert per_write < full / 5, (per_write, full)
    for i in range(20):
        pages[f"f{i % 30}/p{i}"] = whole[f"f{i % 30}/p{i}"]
    _check(conn, key, pages)


def test_move_rewrites_exactly_what_a_full_scan_would(server):
    """move_page now finds the pages to rewrite from the links table instead of
    reading every page. Every page must come out as the old full scan made it."""
    from test_mcp_parity import _seed, call, ok
    from dexio.server.mcp_server import rewrite_links

    old, new = "notes/plan", "archive/2026/plan"
    wiki = {
        "index": "# I\n\n[[notes/plan]] [[plan|the plan]] [x](notes/plan.md#part)\n",
        "notes/other": "# O\n\n[[plan#goals]] and [[./plan]] and `[[plan]]` in code\n",
        "notes/plan": "# Plan\n\nSelf: [[plan]]. Up: [[../index]].\n",
        "deep/a/b": "# B\n\n[[../../notes/plan]] [see](../../notes/plan.md)\n",
        "elsewhere/plan-b": "# Not it\n\n[[plan-b]] [[notes/other]]\n",
        "log": "# Log\n\nNothing about it.\n",
    }
    _seed(server["conn"], "mv", wiki)
    before = {p: db.make_page(p, t) for p, t in wiki.items()}
    want = {}
    for src, page in before.items():
        text, _n = rewrite_links(page.text, src, old, new, before)
        want[new if src == old else src] = text
    got = ok(call(server, "move_page", {"wiki": "mv", "path": old, "new_path": new}))
    assert got["links_rewritten"] >= 8
    conn = server["conn"]
    k = db.wiki_key(db.default_workspace(conn))
    stored = {r[0]: r[1] for r in conn.execute("SELECT path, text FROM pages WHERE project=?", (k,))}
    assert stored == want
    _check(conn, k, {p: db.make_page(p, t) for p, t in want.items()})


from test_mcp_parity import server  # noqa: E402,F401  (the fixture)
