"""Run the suite against Postgres (the default, as in production) or SQLite.

    pytest                        # Postgres, started locally by pgserver
    DEXIO_TEST_PG_URL=postgresql://postgres@127.0.0.1:5432/postgres pytest
                                  # Postgres at that URL (CI, where pgserver has no build)
    DEXIO_TEST_DB=sqlite pytest   # SQLite files, as before

In Postgres mode every `db.connect(path)` a test makes gets its own fresh
database, and the same path maps back to the same database, so tests that reopen
a path (a restart) still see their data. Tests that build an old SQLite file by
hand to check an in-place upgrade are marked `sqlite_only` and skipped here.
"""
import itertools
import os
import tempfile

import pytest

from dexio.server import db, pg

MODE = os.environ.get("DEXIO_TEST_DB", "postgres").lower()
_open: list = []


def pytest_configure(config):
    config.addinivalue_line("markers", "sqlite_only: checks a SQLite-specific upgrade path")


def pytest_collection_modifyitems(config, items):
    if MODE != "postgres":
        return
    skip = pytest.mark.skip(reason="SQLite-only upgrade test (DEXIO_TEST_DB=sqlite runs it)")
    for item in items:
        if "sqlite_only" in item.keywords:
            item.add_marker(skip)


if MODE == "postgres":
    import urllib.parse

    import psycopg

    if os.environ.get("DEXIO_TEST_PG_URL"):
        # Start that server with max_connections of 400 or so: every test app
        # keeps a small pool open.
        _base = os.environ["DEXIO_TEST_PG_URL"]
    else:
        import pgserver

        _dir = tempfile.mkdtemp(prefix="dexio-pg-")
        # Every test app keeps a small pool open, so allow more than the default 100.
        _first = pgserver.get_server(_dir, cleanup_mode="stop")
        _first.psql("ALTER SYSTEM SET max_connections = 400;")
        _first.cleanup()
        _server = pgserver.get_server(_dir, cleanup_mode="delete")
        _base = _server.get_uri()
    _dbs: dict[str, str] = {}
    _n = itertools.count()
    _real_connect = db.connect

    def _fresh_url() -> str:
        name = f"t{os.getpid()}_{next(_n)}"
        with psycopg.connect(_base, autocommit=True) as c:
            c.execute(f"CREATE DATABASE {name}")
        u = urllib.parse.urlsplit(_base)
        return urllib.parse.urlunsplit(u._replace(path=f"/{name}"))

    def _connect(path=None):
        target = str(path or os.environ.get("DEXIO_DB", "dexio.db"))
        if pg.is_url(target):
            conn = _real_connect(target)
        else:
            if target == ":memory:" or target not in _dbs:
                _dbs[target] = _fresh_url()
            conn = _real_connect(_dbs[target])
        _open.append(conn)
        return conn

    db.connect = _connect

    @pytest.fixture(autouse=True)
    def _close_test_connections():
        """Close the connections a test opened when it ends. Every test app keeps a
        pool, and holding them all to the end of the session ran the CI server out
        at max_connections=400 once the suite passed ~320 tests ("too many clients
        already", 2026-09-27). Module-scoped fixtures are set up before this runs,
        so their connections are not in the slice and stay open for their module."""
        start = len(_open)
        yield
        for conn in _open[start:]:
            try:
                conn.close()
            except Exception:
                pass
        del _open[start:]

    def pytest_sessionfinish(session, exitstatus):
        for conn in _open:
            try:
                conn.close()
            except Exception:
                pass
