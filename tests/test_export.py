"""Export (back 2026-09-28, Forrest): GET /api/v1/export returns the workspace's
wiki as a zip of markdown files at their paths, named for the workspace, to an
API key, an OAuth token or the signed-in browser, and Settings, General links it."""
from __future__ import annotations

import io
import zipfile

import httpx
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import db  # noqa: E402
from dexio.server.app import get_app  # noqa: E402
from dexio.server.mcp_server import INSTRUCTIONS  # noqa: E402

from test_mcp_parity import PAGES, call, ok, server  # noqa: E402,F401
from test_workspaces import browser, signup, ws_id  # noqa: E402


def unzip(content: bytes) -> dict[str, str]:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        return {n: z.read(n).decode() for n in z.namelist()}


def test_export_is_every_page_at_its_path(server):
    r = httpx.get(f"{server['base']}/api/v1/export",
                  headers={"Authorization": f"Bearer {server['fleet']}"})
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    assert 'filename="Default-workspace.zip"' in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "no-store"
    assert unzip(r.content) == {f"{p}.md": t for p, t in PAGES.items()}


def test_export_zip_is_named_safely_for_the_workspace(server):
    """A header is Latin-1 and a workspace name can be anything, so the file name
    keeps ASCII letters, digits and ._- and turns each other run into one '-'."""
    conn, ws = server["conn"], db.default_workspace(server["conn"])
    for name, want in [("Acme & Co. / R&D ✓", "Acme-Co.-R-D"), ("✓✓", "dexio"),
                       ("x" * 80, "x" * 60)]:
        conn.execute("UPDATE workspaces SET name=? WHERE id=?", (name, ws))
        conn.commit()
        r = httpx.get(f"{server['base']}/api/v1/export",
                      headers={"Authorization": f"Bearer {server['fleet']}"})
        assert f'filename="{want}.zip"' in r.headers["content-disposition"], name


def test_export_follows_mcp_changes(server):
    ok(call(server, "write_page", {"path": "notes/new", "text": "# New\n\nAdded.\n"}))
    r = httpx.get(f"{server['base']}/api/v1/export",
                  headers={"Authorization": f"Bearer {server['fleet']}"})
    got = unzip(r.content)
    assert got["notes/new.md"] == "# New\n\nAdded.\n"
    assert len(got) == len(PAGES) + 1


def test_export_refuses_bad_keys_and_no_auth_and_ignores_a_wiki(server):
    """A workspace has one wiki (2026-09-28): an old ?wiki= still gets it."""
    base = server["base"]
    r = httpx.get(f"{base}/api/v1/export", headers={"Authorization": "Bearer dxk_nope"})
    assert r.status_code == 403
    assert httpx.get(f"{base}/api/v1/export").status_code == 401
    r = httpx.get(f"{base}/api/v1/export", params={"wiki": "nothing"},
                  headers={"Authorization": f"Bearer {server['fleet']}"})
    assert r.status_code == 200 and unzip(r.content) == {f"{p}.md": t for p, t in PAGES.items()}


def test_instructions_say_how_to_export():
    assert "GET https://app.dexio.wiki/api/v1/export" in INSTRUCTIONS
    assert "export?" not in INSTRUCTIONS


def test_signed_in_browser_exports_and_settings_links_it(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    app = get_app(str(tmp_path / "e.db"))
    with TestClient(app, base_url="https://testserver"):
        c = browser(app)
        signup(c, "ann@example.com")
        conn = app.state.conn
        ws = db.workspaces_for_user(conn, conn.execute(
            "SELECT id FROM users WHERE email='ann@example.com'").fetchone()["id"])[0]["id"]
        pages = db.PageSet(conn, db.wiki_key(ws_id(app, ws)))
        pages["hello"] = db.make_page("hello", "# Hello\n")
        db.apply_changes(conn, db.wiki_key(ws_id(app, ws)), pages.changes, source="mcp", op="write")
        settings = c.get(f"/settings?w={ws}").text
        assert f"/api/v1/export?w={db.handle_of(conn, ws)}" in settings
        assert "Download as markdown" in " ".join(settings.split())    # as a browser shows it
        r = c.get(f"/api/v1/export?w={ws}")
        assert r.status_code == 200 and unzip(r.content) == {"hello.md": "# Hello\n"}
