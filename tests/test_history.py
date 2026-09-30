"""History as diffs: each revision stores the edits from the one before, with a
whole copy every SNAPSHOT_EVERY revisions, and every past version still reads
back exactly. Existing history is rewritten on start (compact_history)."""
from __future__ import annotations

import random

import pytest

from dexio import delta
from dexio.server import db


def test_delta_round_trip_on_random_edits():
    rng = random.Random(7)
    alphabet = "ab \né中"
    for _ in range(3000):
        old = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 60)))
        new = list(old)
        for _ in range(rng.randint(0, 4)):
            i = rng.randint(0, len(new))
            if rng.random() < 0.5 and new:
                del new[i:i + rng.randint(1, 5)]
            else:
                new[i:i] = rng.choice(alphabet) * rng.randint(1, 5)
        new = "".join(new)
        assert delta.apply(old, delta.make(old, new)) == new, (old, new)


def test_append_and_one_place_edit_are_one_small_edit():
    page = "# Log\n\n" + "".join(f"- entry {i}\n" for i in range(20000))   # ~300 KB
    added = page + "- one more\n"
    assert delta.make(page, added) == [[len(page), len(page), "- one more\n"]]
    edited = page.replace("- entry 9999\n", "- entry 9999 (fixed)\n")
    d = delta.make(page, edited)
    assert len(d) == 1 and len(delta.encode(d)) < 60
    assert delta.apply(page, d) == edited


@pytest.fixture()
def conn(tmp_path):
    return db.connect(str(tmp_path / "h.db"))


def _write(conn, k, path, text, op="edit"):
    pages = db.PageSet(conn, k)
    pages[path] = db.make_page(path, text)
    db.apply_changes(conn, k, pages.changes, source="mcp", author="t", op=op, agent="a")


def test_revisions_store_diffs_and_read_back(conn):
    k = db.wiki_key(db.default_workspace(conn))
    texts = []
    text = "# Log\n"
    for i in range(120):
        text += f"- entry {i}: " + "x" * 200 + "\n"
        _write(conn, k, "log", text, op="append")
        texts.append(text)
    rows = conn.execute("SELECT id, text, delta, chars, words, version FROM revisions"
                        " WHERE project=? AND path='log' ORDER BY id", (k,)).fetchall()
    whole = [i for i, r in enumerate(rows) if r["text"] is not None]
    assert whole == [0, 50, 100]                  # a whole copy every 50 revisions
    stored = sum(len(r["text"] or r["delta"]) for r in rows)
    assert stored < sum(len(t) for t in texts) / 10
    for r, t in zip(rows, texts):
        assert db.revision(conn, k, r["id"])["text"] == t
        assert (r["chars"], r["words"], r["version"]) == (len(t), len(t.split()),
                                                          db.version_of(t))
    listed = db.revisions(conn, k, "log", limit=3)
    assert [x["version"] for x in listed] == [db.version_of(t) for t in texts[:-4:-1]]


def test_delete_then_recreate_starts_a_whole_copy(conn):
    k = db.wiki_key(db.default_workspace(conn))
    _write(conn, k, "a", "# A\n\none\n", op="write")
    _write(conn, k, "a", "# A\n\none\ntwo\n")
    pages = db.PageSet(conn, k)
    del pages["a"]
    db.apply_changes(conn, k, pages.changes, source="mcp", author="t", op="delete", agent="a")
    _write(conn, k, "a", "# A\n\none\ntwo\nthree\n", op="write")
    revs = db.revisions(conn, k, "a")
    assert [r["op"] for r in revs] == ["write", "delete", "edit", "write"]
    assert [r["deleted"] for r in revs] == [0, 1, 0, 0]
    assert db.revision(conn, k, revs[1]["id"])["text"] is None
    assert db.revision(conn, k, revs[0]["id"])["text"] == "# A\n\none\ntwo\nthree\n"
    raw = conn.execute("SELECT text FROM revisions WHERE id=?", (revs[0]["id"],)).fetchone()
    assert raw["text"] is not None                # after a delete, whole again


def test_existing_history_is_rewritten_once_and_reads_the_same(conn):
    """Rows written before diffs (whole text, no version), and one written by an
    older release after a rollback, become deltas; every version reads the same."""
    k = db.wiki_key(db.default_workspace(conn))
    texts = ["# P\n" + "".join(f"line {j}\n" for j in range(i * 10)) for i in range(1, 8)]
    for i, t in enumerate(texts):
        conn.execute("INSERT INTO revisions (project, path, at, op, author, text)"
                     " VALUES (?,?,?,?,?,?)", (k, "p", 1000.0 + i, "push", "old", t))
    conn.execute("INSERT INTO revisions (project, path, at, op, author, text)"
                 " VALUES (?,?,?,?,?,?)", (k, "gone", 1000.0, "push", "old", "# Gone\n"))
    conn.execute("INSERT INTO revisions (project, path, at, op, author, text)"
                 " VALUES (?,?,?,?,?,?)", (k, "gone", 1001.0, "delete", "old", None))
    conn.commit()
    assert db.compact_history(conn) == 2
    assert db.compact_history(conn) == 0
    rows = conn.execute("SELECT id, text, delta FROM revisions WHERE project=? AND path='p'"
                        " ORDER BY id", (k,)).fetchall()
    assert rows[0]["text"] is not None and all(r["delta"] for r in rows[1:])
    for r, t in zip(rows, texts):
        assert db.revision(conn, k, r["id"])["text"] == t
    gone = db.revisions(conn, k, "gone")
    assert [r["deleted"] for r in gone] == [1, 0]
    # An older release writes one more whole row with no version; the next start
    # folds it in.
    t8 = texts[-1] + "line new\n"
    conn.execute("INSERT INTO revisions (project, path, at, op, author, text)"
                 " VALUES (?,?,?,?,?,?)", (k, "p", 2000.0, "push", "old", t8))
    conn.commit()
    assert db.compact_history(conn) == 1
    last = db.revisions(conn, k, "p", limit=1)[0]
    assert db.revision(conn, k, last["id"])["text"] == t8
    assert conn.execute("SELECT delta FROM revisions WHERE id=?",
                        (last["id"],)).fetchone()["delta"]
