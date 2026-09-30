"""The Postgres adapter: the sqlite3 behaviours the server relies on."""
import sqlite3

import pytest

from conftest import MODE
from dexio.server import db, pg

pytestmark = pytest.mark.skipif(MODE != "postgres", reason="Postgres adapter only")


def test_suite_really_runs_on_postgres(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    assert isinstance(conn, pg.PgConnection)
    assert conn.execute("SELECT version()").fetchone()[0].startswith("PostgreSQL")


def test_rows_answer_to_position_name_dict_and_unpacking(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    row = conn.execute("SELECT 1 AS a, 'x' AS b").fetchone()
    assert row[0] == 1 and row["b"] == "x" and dict(row) == {"a": 1, "b": "x"}
    a, b = row
    assert (a, b) == (1, "x") and row.keys() == ["a", "b"]


def test_like_ignores_case_as_in_sqlite(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    conn.execute("INSERT INTO workspaces (name, plan, created_at) VALUES (?,?,?)", ("Mixed", "free", 1.0))
    assert conn.execute("SELECT name FROM workspaces WHERE name LIKE ?", ("mix%",)).fetchone()


def test_lastrowid_and_insert_or_ignore_and_replace(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    cur = conn.execute("INSERT INTO workspaces (name, plan, created_at) VALUES (?,?,?)", ("w", "free", 1.0))
    assert isinstance(cur.lastrowid, int) and cur.lastrowid > 0
    conn.execute("CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v TEXT)")
    conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", ("a", "1"))
    conn.execute("INSERT OR REPLACE INTO kv (k, v) VALUES (?,?)", ("a", "2"))
    conn.execute("INSERT OR IGNORE INTO kv (k, v) VALUES (?,?)", ("a", "3"))
    assert conn.execute("SELECT v FROM kv WHERE k=?", ("a",)).fetchone()[0] == "2"


def test_violation_inside_a_transaction_can_be_caught_and_the_block_goes_on(tmp_path):
    """SQLite lets a transaction continue after a caught constraint error; device
    login relies on it to redraw a colliding code."""
    conn = db.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE IF NOT EXISTS u (k TEXT PRIMARY KEY)")
    with conn:
        conn.execute("INSERT INTO u (k) VALUES (?)", ("a",))
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("INSERT INTO u (k) VALUES (?)", ("a",))
        conn.execute("INSERT INTO u (k) VALUES (?)", ("b",))
    assert [r[0] for r in conn.execute("SELECT k FROM u ORDER BY k")] == ["a", "b"]


def test_a_failed_block_rolls_back(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE IF NOT EXISTS u (k TEXT PRIMARY KEY)")
    with pytest.raises(RuntimeError):
        with conn:
            conn.execute("INSERT INTO u (k) VALUES (?)", ("a",))
            raise RuntimeError("boom")
    assert conn.execute("SELECT COUNT(*) FROM u").fetchone()[0] == 0


def test_text_sorts_by_bytes_like_sqlite(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    conn.execute("CREATE TABLE IF NOT EXISTS s (v TEXT)")
    conn.executemany("INSERT INTO s (v) VALUES (?)", [("b",), ("B",), ("a",), ("_x",)])
    assert [r[0] for r in conn.execute("SELECT v FROM s ORDER BY v")] == ["B", "_x", "a", "b"]


def test_sums_come_back_as_numbers_and_bools_go_in_as_ints(tmp_path):
    conn = db.connect(tmp_path / "x.db")
    conn.execute("INSERT INTO workspaces (name, plan, created_at) VALUES (?,?,?)", ("w", "free", 1.5))
    assert conn.execute("SELECT SUM(id) FROM workspaces").fetchone()[0] == 1
    conn.execute("CREATE TABLE IF NOT EXISTS f (b INTEGER)")
    conn.execute("INSERT INTO f (b) VALUES (?)", (True,))
    assert conn.execute("SELECT b FROM f").fetchone()[0] == 1


def test_emails_compare_without_case(tmp_path):
    from dexio.server import auth
    conn = db.connect(tmp_path / "x.db")
    auth.init(conn)
    auth.create_user(conn, "Mixed.Case@Example.com", "correct horse battery")
    assert auth.user_by_email(conn, "mixed.case@example.COM")
    assert auth.authenticate(conn, " MIXED.case@example.com ", "correct horse battery")
    with pytest.raises(sqlite3.IntegrityError):
        auth.create_user(conn, "mixed.case@example.com", "another password here")
