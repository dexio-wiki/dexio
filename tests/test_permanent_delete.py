"""delete_page with permanent (Forrest, 2026-10-31: "we need to allow permanent page
deletion through dexio mcp"): the page goes and so does its history, including the
history it left under the paths it was moved from, with one purge row left to say who
did it. Real uvicorn, real MCP client (the fixtures of test_mcp_parity)."""
from __future__ import annotations

import pytest

pytest.importorskip("mcp")
pytest.importorskip("fastapi")

from dexio.server import db, files  # noqa: E402

from test_mcp_parity import PAGES, call, err, ok, server  # noqa: E402,F401


def wiki(server) -> str:
    return db.wiki_key(db.default_workspace(server["conn"]))


def texts_at(server, path) -> list[str | None]:
    """Every revision of `path` rebuilt, oldest first."""
    conn, k = server["conn"], wiki(server)
    ids = [r["id"] for r in conn.execute(
        "SELECT id FROM revisions WHERE project=? AND path=? ORDER BY id", (k, path))]
    return [db.revision_text(conn, k, path, i) for i in ids]


def anywhere(server, needle) -> bool:
    """Whether any revision of any page in the wiki holds `needle`."""
    conn, k = server["conn"], wiki(server)
    paths = {r["path"] for r in conn.execute("SELECT DISTINCT path FROM revisions WHERE project=?",
                                             (k,))}
    return any(needle in (t or "") for p in paths for t in texts_at(server, p))


def test_a_page_and_its_history_go_for_good(server):
    k, ws = wiki(server), db.default_workspace(server["conn"])
    ok(call(server, "edit_page", {"path": "entities/beta", "old_text": "40", "new_text": "41"}))
    old = ok(call(server, "page_history", {"path": "entities/beta"}))["revisions"]
    before = files.usage(server["conn"], ws)

    r = ok(call(server, "delete_page", {"path": "entities/beta", "permanent": True,
                                        "agent": "cleaner", "note": "asked to remove it"}))
    assert r["permanent"] is True and r["deleted"] == "entities/beta"
    assert r["revisions_deleted"] == 3                      # push, edit, the delete itself
    assert r["now_broken_on"] == ["index"] and "restore_revision" not in r
    assert "moved_from" not in r

    assert "no page" in err(call(server, "read_page", {"path": "entities/beta"}))
    for rev in old:
        assert err(call(server, "read_page", {"path": "entities/beta",
                                              "revision": rev["revision"]}))
    h = ok(call(server, "page_history", {"path": "entities/beta"}))["revisions"]
    assert [(x["op"], x["deleted"], x["agent"], x["note"]) for x in h] == [
        ("purge", True, "cleaner", "asked to remove it")]
    feed = ok(call(server, "page_history", {}))["changes"]
    assert feed[0]["path"] == "entities/beta" and feed[0]["op"] == "purge"
    assert not anywhere(server, "Owes 4")
    assert files.usage(server["conn"], ws) < before          # the history's storage is freed
    assert ok(call(server, "read_page", {"path": "index"}))["broken_links"] == ["entities/beta"]
    assert db.restore_point(server["conn"], k, "entities/beta") is None

    # a page written there later starts fresh
    ok(call(server, "write_page", {"path": "entities/beta", "text": "# Beta\n\nNew.\n"}))
    h = ok(call(server, "page_history", {"path": "entities/beta"}))["revisions"]
    assert [x["op"] for x in h] == ["write", "purge"]


def test_a_page_deleted_before_is_deleted_for_good_after(server):
    ok(call(server, "delete_page", {"path": "concepts/drift"}))
    r = ok(call(server, "delete_page", {"path": "concepts/drift", "permanent": True}))
    assert r["revisions_deleted"] == 2 and r["now_broken_on"] == [] and "wiki_stats" not in r
    assert texts_at(server, "concepts/drift") == [None]
    assert "already deleted for good" in err(call(server, "delete_page", {
        "path": "concepts/drift", "permanent": True}))
    assert "no page" in err(call(server, "delete_page", {"path": "nowhere/at-all",
                                                         "permanent": True}))


def test_history_left_under_a_moved_from_path_goes_too(server):
    ok(call(server, "edit_page", {"path": "entities/acme", "old_text": "March", "new_text": "May"}))
    ok(call(server, "move_page", {"path": "entities/acme", "new_path": "customers/acme"}))
    ok(call(server, "edit_page", {"path": "customers/acme", "old_text": "loud", "new_text": "quiet"}))
    # another page later takes the old path; it is not the deleted page's history
    ok(call(server, "write_page", {"path": "entities/acme", "text": "# Acme Labs\n\nUnrelated.\n"}))

    r = ok(call(server, "delete_page", {"path": "customers/acme", "permanent": True}))
    assert r["moved_from"] == ["entities/acme"]
    assert r["revisions_deleted"] == 3 + 3                  # move, edit, delete; push, edit, move
    for needle in ("renews in", "ACME is"):
        assert not anywhere(server, needle)
    assert texts_at(server, "entities/acme") == ["# Acme Labs\n\nUnrelated.\n"]
    assert ok(call(server, "read_page", {"path": "entities/acme"}))["text"].startswith("# Acme Labs")
    # the pages whose links the move rewrote keep their own history
    assert len(texts_at(server, "index")) >= 2


def test_a_move_chain_is_followed_back(server):
    ok(call(server, "move_page", {"path": "log", "new_path": "notes/log"}))
    ok(call(server, "move_page", {"path": "notes/log", "new_path": "archive/log"}))
    r = ok(call(server, "delete_page", {"path": "archive/log", "permanent": True}))
    assert sorted(r["moved_from"]) == ["log", "notes/log"]
    assert not anywhere(server, "[2026-09-01] start")


def test_a_stale_base_version_deletes_nothing(server):
    v = ok(call(server, "read_page", {"path": "entities/beta"}))["version"]
    ok(call(server, "append_page", {"path": "entities/beta", "text": "More."}))
    assert "changed" in err(call(server, "delete_page", {
        "path": "entities/beta", "permanent": True, "base_version": v})).lower()
    assert len(texts_at(server, "entities/beta")) == 2
    assert ok(call(server, "read_page", {"path": "entities/beta"}))["text"].endswith("More.")


def test_change_pages_will_not_delete_for_good(server):
    out = call(server, "change_pages", {"changes": [
        {"op": "write", "path": "notes/x", "text": "# X\n"},
        {"op": "delete", "path": "entities/beta", "permanent": True}]})
    assert out.is_error and "delete_page" in out.content[0].text
    assert ok(call(server, "read_page", {"path": "entities/beta"}))
    assert "no page" in err(call(server, "read_page", {"path": "notes/x"}))


def test_shares_on_the_page_go_with_it(server):
    conn = server["conn"]
    ws = db.default_workspace(conn)
    with conn:
        conn.execute("INSERT INTO shares (workspace_id, kind, path, created_at)"
                     " VALUES (?, 'page', 'entities/beta', 0)", (ws,))
        conn.execute("INSERT INTO shares (workspace_id, kind, path, created_at)"
                     " VALUES (?, 'page', 'entities/acme', 0)", (ws,))
    ok(call(server, "delete_page", {"path": "entities/beta", "permanent": True}))
    left = [r["path"] for r in conn.execute("SELECT path FROM shares WHERE workspace_id=?", (ws,))]
    assert left == ["entities/acme"]

