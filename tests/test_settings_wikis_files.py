"""Settings > Files (storage, upload, one page of files at a time) and the wiki
panel on General. Settings > Wikis went on 2026-09-28, when a workspace came to
have one wiki."""
from __future__ import annotations

import re
import time
import uuid

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import db, files, pages  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import browser, signup, ws_id  # noqa: E402


@pytest.fixture()
def app(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    files.set_store(files.FolderStore(tmp_path / "store"))
    a = get_app(str(tmp_path / "s.db"))
    with TestClient(a, base_url="https://testserver"):
        yield a
    files.set_store(None)


def person(app):
    c = browser(app)
    signup(c, f"{uuid.uuid4().hex[:8]}@example.com")
    return c, c.get("/api/v1/workspaces").json()["current"]


def add_files(app, ws, wiki, n):
    """n file rows straight into the table: listing is what is under test."""
    now, ws = time.time(), ws_id(app, ws)
    with db.LOCK, app.state.conn as conn:
        for i in range(n):
            conn.execute("INSERT INTO files (id, project, workspace_id, path, size, content_type,"
                         " sha256, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (uuid.uuid4().hex, db.key(ws, wiki), ws, f"raw/f{i:03d}.txt", 10,
                          "text/plain", "0" * 64, now, now))


def test_files_is_a_section_and_wikis_is_gone(app):
    """With one wiki per workspace there is nothing to list, name or create, so
    the Wikis section is gone; its old links, and the older combined section's,
    land on General with the query kept. Files keeps its own section."""
    c, ws = person(app)
    f = c.get("/settings/files").text
    assert "<h1>Files</h1>" in f and 'id="upload-form"' in f
    assert '<a class="sec" href="/settings/files" aria-current="page">' in f
    assert 'href="/settings/wikis"' not in f and ">Wikis</a>" not in f
    for old in ("wikis", "data"):
        r = c.get(f"/settings/{old}?w={ws}&done=x")
        assert r.status_code == 308, old
        assert r.headers["location"] == f"/settings?w={ws}&done=x"
    assert '"#files": "/settings/files"' in c.get("/settings").text


def test_general_shows_the_wiki_and_a_download(app):
    """What the Wikis section told you about a wiki is on General now: its size,
    and a download of every page, so a customer can always take their pages."""
    c, ws = person(app)
    page = c.get(f"/settings?w={ws}").text
    assert 'id="wiki"' in page and "No pages yet." in page
    assert f'href="/api/v1/export?w={ws}">Download as' in page
    assert "its wiki, with page history and files" in page


def test_files_page_through_fifty_at_a_time(app):
    c, ws = person(app)
    add_files(app, ws, "main", 120)
    per = pages.FILES_PER_PAGE

    def rows(html):
        return re.findall(r'data-del-path="(raw/f\d+\.txt)"', html)

    first = c.get("/settings/files").text
    assert rows(first) == [f"raw/f{i:03d}.txt" for i in range(per)]
    assert "1&ndash;50 of 120" in first and "120 files" in first
    assert f'href="/settings/files?w={ws}&amp;page=2">Next' in first
    assert 'aria-disabled="true">&larr; Previous' in first

    third = c.get("/settings/files?page=3").text
    assert rows(third) == [f"raw/f{i:03d}.txt" for i in range(100, 120)]
    assert "101&ndash;120 of 120" in third and 'aria-disabled="true">Next' in third
    # Past the end lands on the last page; nonsense lands on the first.
    assert rows(c.get("/settings/files?page=99").text) == rows(third)
    assert rows(c.get("/settings/files?page=-1").text) == rows(first)
    assert rows(c.get("/settings/files?page=x").text) == rows(first)


def test_files_list_only_the_workspace_wiki_with_no_wiki_column(app):
    """Files belong to the workspace's one wiki, so there is no Wiki column or
    picker. A wiki left over from before one wiki each is out of reach, and so
    are its files."""
    c, ws = person(app)
    add_files(app, ws, "main", 2)
    add_files(app, ws, "ops", 3)
    one = c.get("/settings/files").text
    assert "Wiki</th>" not in one and 'id="up-wiki"' not in one
    assert re.findall(r'data-del-path="(raw/f\d+\.txt)"', one) == ["raw/f000.txt", "raw/f001.txt"]
    assert "Pages of files" not in one                         # no pager for two files


def test_a_new_workspace_can_upload_files_at_once(app):
    """Every workspace has its wiki from the start, so Files never asks you to
    create one first."""
    c, _ws = person(app)
    page = c.get("/settings/files").text
    assert 'id="upload-form"' in page and "No files yet." in page
    assert 'href="/settings/wikis"' not in page
