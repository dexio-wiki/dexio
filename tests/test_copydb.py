"""copy-db: a SQLite database with real content moves into Postgres intact."""
import pytest

import conftest
from dexio.server import auth, copydb, db, device, oauth, pg

pytestmark = pytest.mark.skipif(conftest.MODE != "postgres", reason="needs Postgres")


def _sqlite_with_content(path):
    conn = conftest._real_connect(str(path))
    assert not isinstance(conn, pg.PgConnection)
    auth.init(conn)
    oauth.init(conn)
    device.init(conn)
    uid = auth.create_user(conn, "Owner@Example.com", "correct horse battery")
    ws = db.create_workspace(conn, "Acme", uid, plan="team")
    k = db.wiki_key(ws)
    pages = {"index": db.make_page("index", "# Index\n\nSee [[notes/a]] and [[missing]].\n"),
             "notes/a": db.make_page("notes/a", "# A\n\nBack to [[index]].\n")}
    db.commit_pages(conn, k, pages, source="mac", op="push", is_push=True)
    pages["notes/a"] = db.make_page("notes/a", "# A\n\nBack to [[index]]. Edited.\n")
    db.commit_pages(conn, k, pages, source="mcp", author="agent", op="write")
    token = db.create_token(conn, "agent", workspace_id=ws)
    auth.secret_key(conn)
    conn.close()
    return uid, ws, k, token


def test_copy_moves_everything_and_the_app_keeps_working(tmp_path):
    uid, ws, k, token = _sqlite_with_content(tmp_path / "src.db")
    url = conftest._fresh_url()
    assert copydb.copy(str(tmp_path / "src.db"), url, log=lambda *_: None)

    conn = db.connect(url)
    assert isinstance(conn, pg.PgConnection)
    assert auth.authenticate(conn, "owner@example.com", "correct horse battery")["id"] == uid
    assert db.check_token(conn, token)["workspace_id"] == ws
    g = db.graph(conn, k)
    assert g["stats"]["pages"] == 2 and g["stats"]["links"] == 2 and g["stats"]["dangling"] == 1
    assert [r["op"] for r in db.revisions(conn, k, "notes/a")] == ["write", "push"]
    # New rows get fresh ids after the copied ones.
    assert db.create_workspace(conn, "Next", uid) > ws


def test_copy_refuses_a_destination_that_already_has_data(tmp_path):
    _sqlite_with_content(tmp_path / "src.db")
    url = conftest._fresh_url()
    assert copydb.copy(str(tmp_path / "src.db"), url, log=lambda *_: None)
    with pytest.raises(RuntimeError, match="already hold rows"):
        copydb.copy(str(tmp_path / "src.db"), url, log=lambda *_: None)
    assert copydb.copy(str(tmp_path / "src.db"), url, replace=True, log=lambda *_: None)
