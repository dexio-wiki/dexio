"""Page history in the web app: a page's created and edited dates and who made
them, its revisions, and what one revision changed (server/history.py)."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio import linediff  # noqa: E402
from dexio.server import db, files  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import browser, mcp, signup, token_from_connect, ws_id  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    files.set_store(files.FolderStore(tmp_path / "store"))
    a = get_app(str(tmp_path / "h.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        yield a
    files.set_store(None)


def setup(app):
    ann = browser(app)
    signup(ann, "ann@example.com")
    key = token_from_connect(ann)
    return ann, key


def write(app, key, path, text, agent="niko", note=None):
    args = {"wiki": "main", "path": path, "text": text, "agent": agent}
    if note:
        args["note"] = note
    r = mcp(app, key, "write_page", **args)
    assert r.get("ok"), r


def hist(c, path, **q):
    r = c.get("/api/v1/page-history", params={"project": "main", "path": path, **q})
    assert r.status_code == 200, r.text
    return r.json()


def test_note_carries_created_and_edited_by_whom(app):
    ann, key = setup(app)
    write(app, key, "a", "# A\n\nOne.\n", agent="niko", note="start the page")
    write(app, key, "a", "# A\n\nOne. Two.\n", agent="claude-code", note="add two")
    r = ann.get("/api/v1/note", params={"project": "main", "path": "a"})
    assert r.status_code == 200
    info = r.json()["info"]
    assert info["exists"] and info["revisions"] == 2
    assert info["created"]["agent"] == "niko" and info["created"]["exact"]
    assert info["updated_by"]["agent"] == "claude-code"
    assert info["updated_at"] >= info["created"]["at"]
    assert {c["name"] for c in info["contributors"]} == {"niko", "claude-code"}
    assert info["chars"] == len("# A\n\nOne. Two.\n")


def test_history_lists_revisions_newest_first_with_word_changes(app):
    ann, key = setup(app)
    write(app, key, "a", "# A\n\none two\n", note="first")
    write(app, key, "a", "# A\n\none two three four\n", note="more")
    out = hist(ann, "a")
    revs = out["revisions"]
    assert [r["note"] for r in revs] == ["more", "first"]
    assert revs[1]["first"] and revs[1]["words_before"] is None and revs[1]["words"] == 4
    assert not revs[0]["first"] and revs[0]["words_before"] == 4 and revs[0]["words"] == 6
    assert out["next_before"] is None and out["info"]["revisions"] == 2


def test_history_pages_through_older_revisions(app):
    ann, key = setup(app)
    for i in range(5):
        write(app, key, "a", f"# A\n\n{'w ' * (i + 1)}\n", note=f"n{i}")
    first = hist(ann, "a", limit=2)
    assert [r["note"] for r in first["revisions"]] == ["n4", "n3"]
    # the last one shown still knows its word count before
    assert first["revisions"][1]["words_before"] == 5
    second = hist(ann, "a", limit=2, before=first["next_before"])
    assert [r["note"] for r in second["revisions"]] == ["n2", "n1"]
    third = hist(ann, "a", limit=2, before=second["next_before"])
    assert [r["note"] for r in third["revisions"]] == ["n0"] and third["next_before"] is None
    assert third["revisions"][0]["first"]


def test_revision_shows_the_lines_and_words_it_changed(app):
    ann, key = setup(app)
    write(app, key, "a", "# A\n\nThe quick brown fox.\n\nKept line.\n")
    write(app, key, "a", "# A\n\nThe quick red fox.\n\nKept line.\n\nNew line.\n")
    revs = hist(ann, "a")["revisions"]
    r = ann.get("/api/v1/revision", params={"project": "main", "path": "a", "id": revs[0]["id"]})
    assert r.status_code == 200
    v = r.json()
    assert v["text"].endswith("New line.\n") and v["older"] == revs[1]["id"] and v["latest"]
    lines = [ln for h in v["diff"]["hunks"] for ln in h["lines"]]
    minus = [ln for ln in lines if ln["t"] == "-"]
    plus = [ln for ln in lines if ln["t"] == "+"]
    assert [ln["s"] for ln in minus] == ["The quick brown fox."]
    assert [ln["s"] for ln in plus] == ["The quick red fox.", "", "New line."] or \
        "New line." in [ln["s"] for ln in plus]
    changed = [run[1] for run in minus[0]["w"] if run[0]]
    assert changed == ["brown"]
    assert v["diff"]["removed"] == 1
    # the first revision has no diff: its text is all of it
    first = ann.get("/api/v1/revision", params={"project": "main", "path": "a",
                                                "id": revs[1]["id"]}).json()
    assert first["diff"] is None and first["first"] and first["newer"] == revs[0]["id"]


def test_a_moved_page_keeps_its_created_date_and_says_where_it_came_from(app):
    ann, key = setup(app)
    write(app, key, "old", "# Page\n\nBody.\n", agent="niko")
    created = hist(ann, "old")["info"]["created"]
    write(app, key, "linker", "# L\n\nSee [[old]].\n", agent="other")
    r = mcp(app, key, "move_page", wiki="main", path="old", new_path="notes/new")
    assert r.get("ok"), r
    out = hist(ann, "notes/new")
    info = out["info"]
    assert info["moved_from"]["path"] == "old"
    assert info["created"]["at"] == created["at"] and info["created"]["agent"] == "niko"
    assert info["created"]["path"] == "old"
    assert out["revisions"][0]["moved_from"] == "old"
    # the old path's history says where the page went, and still reads
    gone = hist(ann, "old")
    assert gone["info"]["exists"] is False
    assert gone["revisions"][0]["deleted"] and gone["revisions"][0]["moved_to"] == "notes/new"
    # the page whose link was rewritten is not mistaken for the moved page
    lk = hist(ann, "linker")["revisions"][0]
    assert "move" in lk["op"] and "moved_from" not in lk


def test_a_deleted_page_shows_what_was_removed_and_a_rewrite_starts_over(app):
    ann, key = setup(app)
    write(app, key, "a", "# A\n\nFirst life.\n", agent="niko")
    assert mcp(app, key, "delete_page", wiki="main", path="a").get("ok")
    out = hist(ann, "a")
    assert out["revisions"][0]["deleted"] and not out["info"]["exists"]
    v = ann.get("/api/v1/revision", params={"project": "main", "path": "a",
                                            "id": out["revisions"][0]["id"]}).json()
    assert v["text"] is None and v["removed_text"] == "# A\n\nFirst life.\n"
    write(app, key, "a", "# A\n\nSecond life.\n", agent="claude-code")
    info = hist(ann, "a")["info"]
    assert info["created"]["agent"] == "claude-code" and info["revisions"] == 3
    assert hist(ann, "a")["revisions"][0]["first"]


def test_a_page_from_before_history_began_is_created_on_or_before(app):
    ann, key = setup(app)
    ws = ann.get("/api/v1/workspaces").json()["current"]
    k = db.wiki_key(ws_id(app, ws))
    conn = app.state.conn
    write(app, key, "a", "# A\n\nOld.\n")
    # as if the page predated history: only a baseline row
    conn.execute("DELETE FROM revisions WHERE project=? AND path=?", (k, "a"))
    db._baseline(conn, k, "a", 1700000000.0, "# A\n\nOld.\n")
    write(app, key, "a", "# A\n\nOld. New.\n", agent="niko")
    info = hist(ann, "a")["info"]
    assert info["created"]["exact"] is False and info["created"]["at"] == 1700000000.0
    assert info["created"]["agent"] == ""
    assert [c["name"] for c in info["contributors"]] == ["niko"]


def test_history_is_private_to_the_workspace(app):
    ann, key = setup(app)
    write(app, key, "a", "# A\n")
    bob = browser(app)
    signup(bob, "bob@example.com")
    r = bob.get("/api/v1/page-history", params={"project": "main", "path": "a"})
    assert r.status_code == 404
    assert TestClient(app, base_url="https://testserver", follow_redirects=False).get(
        "/api/v1/page-history", params={"project": "main", "path": "a"}).status_code in (401, 303)


def test_line_diff_numbers_both_sides_and_keeps_context():
    old = "\n".join(f"line {i}" for i in range(1, 21))
    new = old.replace("line 10", "line ten")
    d = linediff.diff(old, new)
    assert d["added"] == 1 and d["removed"] == 1 and len(d["hunks"]) == 1
    h = d["hunks"][0]
    assert h["old_start"] == 7 and h["new_start"] == 7
    kinds = "".join(ln["t"] for ln in h["lines"])
    assert kinds == "   -+   "
    minus = h["lines"][3]
    assert minus["a"] == 10 and minus["b"] is None
    assert [r for r in minus["w"] if r[0]] == [[1, "10"]]


def test_line_diff_leaves_unrelated_lines_whole():
    d = linediff.diff("alpha beta gamma\n", "completely different words here\n")
    lines = d["hunks"][0]["lines"]
    assert all("w" not in ln for ln in lines)
