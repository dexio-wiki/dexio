"""Files beside pages: upload with a token, a signed-in browser, or a one-time URL
from upload_file; storage limits by plan; text pulled from documents for search
and read_page; images handed to the model; downloads from a signed, expiring URL
with a sandboxing policy; list_files and delete_file, and page tools that refuse
files; file links are not broken links."""
from __future__ import annotations

import base64
import io
import json
import zipfile

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server import auth, db, files  # noqa: E402
from dexio.server.app import get_app  # noqa: E402

from test_workspaces import browser, mcp, signup, token_from_connect  # noqa: E402

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg==")


def docx(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", f'<w:document><w:body><w:p><w:r><w:t>{text}</w:t>'
                   f'</w:r></w:p></w:body></w:document>')
    return buf.getvalue()


def pptx(*slides: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, s in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", f"<p:sld><a:p><a:r><a:t>{s}</a:t></a:r></a:p></p:sld>")
    return buf.getvalue()


def pdf(text: str) -> bytes:
    """A one-page PDF with a line of text, written by hand (no PDF library)."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> >>",
            b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    out, offsets = io.BytesIO(), []
    out.write(b"%PDF-1.4\n")
    for i, o in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % i + o + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
              % (len(objs) + 1, xref))
    return out.getvalue()


@pytest.fixture()
def env(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    files.set_store(files.FolderStore(tmp_path / "store"))
    app = get_app(str(tmp_path / "f.db"))
    with TestClient(app, base_url="https://testserver") as live:
        app.state.live = live
        c = browser(app)
        signup(c, "ann@example.com")
        tok = token_from_connect(c)
        conn = app.state.conn
        ws = db.workspaces_for_user(conn, auth.user_by_email(conn, "ann@example.com")["id"])[0]["id"]
        yield {"app": app, "live": live, "c": c, "tok": tok, "conn": conn, "ws": ws,
               "store": tmp_path / "store"}
    files.set_store(None)


def put(env, path, data, token=None, ctype="application/octet-stream", wiki=None):
    """A workspace has one wiki (2026-09-28): the file API takes a path only,
    and a `wiki` from an older client is ignored."""
    params = {"path": path} if wiki is None else {"wiki": wiki, "path": path}
    return env["live"].put("/api/v1/files", params=params, content=data,
                           headers={"Authorization": f"Bearer {token or env['tok']}",
                                    "Content-Type": ctype})


def test_upload_list_read_and_search_inside_documents(env):
    app, tok = env["app"], env["tok"]
    r = put(env, "raw/deck.pptx", pptx("Q3 revenue plan", "Hiring the relay team"))
    assert r.status_code == 200 and r.json()["searchable"], r.text
    assert put(env, "raw/memo.docx", docx("The relay deploys on Tuesdays")).status_code == 200
    assert put(env, "raw/spec.pdf", pdf("Relay latency budget 40ms")).status_code == 200
    assert put(env, "data/rows.csv", b"name,count\nrelay,3\n").status_code == 200

    listed = mcp(app, tok, "list_files", wiki="main")
    assert listed["count"] == 4
    assert [f["path"] for f in listed["files"]] == [
        "data/rows.csv", "raw/deck.pptx", "raw/memo.docx", "raw/spec.pdf"]
    assert mcp(app, tok, "list_files", wiki="main", folder="data")["files"][0]["type"] == "text/csv"
    assert mcp(app, tok, "list_files", wiki="main", folder="notes") == {
        "folder": "notes", "count": 0, "files": []}
    pages = mcp(app, tok, "list_pages", wiki="main")               # pages only
    assert "files" not in pages and "file_count" not in pages
    types = {f["path"]: f["type"] for f in listed["files"]}
    assert types["raw/deck.pptx"].endswith("presentationml.presentation")
    assert types["raw/memo.docx"].endswith("wordprocessingml.document")

    hits = mcp(app, tok, "search_pages", wiki="main", query="relay deploys")["results"]
    assert hits[0]["path"] == "raw/memo.docx" and hits[0]["file"] is True
    hits = mcp(app, tok, "search_pages", wiki="main", query="latency budget")["results"]
    assert [h["path"] for h in hits] == ["raw/spec.pdf"]

    deck = mcp(app, tok, "read_page", wiki="main", path="raw/deck.pptx")
    assert deck["file"] and "--- slide 2 ---" in deck["text"] and "Hiring the relay team" in deck["text"]
    assert deck["download_url"].startswith("https://testserver/api/v1/files/raw/") or \
        "/api/v1/files/raw/" in deck["download_url"]


def test_images_come_back_as_images(env):
    put(env, "images/dot.png", PNG, ctype="image/png")
    r = env["live"].post("/mcp", headers={"Authorization": f"Bearer {env['tok']}",
                                          "Accept": "application/json, text/event-stream"},
                         json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                             "name": "read_page",
                             "arguments": {"wiki": "main", "path": "images/dot.png"}}})
    content = r.json()["result"]["content"]
    assert json.loads(content[0]["text"])["content_type"] == "image/png"
    assert content[1]["type"] == "image" and base64.b64decode(content[1]["data"]) == PNG


def test_download_is_signed_expiring_and_sandboxed(env):
    put(env, "raw/page.html", b"<script>alert(1)</script>", ctype="text/html")
    r = env["live"].get("/api/v1/files", params={"path": "raw/page.html"},
                        headers={"Authorization": f"Bearer {env['tok']}"}, follow_redirects=False)
    assert r.status_code == 302
    raw = env["live"].get(r.headers["location"])
    assert raw.content == b"<script>alert(1)</script>"
    assert raw.headers["content-disposition"].startswith("attachment")   # html never inline
    assert "sandbox" in raw.headers["content-security-policy"]
    assert raw.headers["x-content-type-options"] == "nosniff"
    bad = r.headers["location"][:-4] + "0000"
    assert env["live"].get(bad).status_code == 403


def test_one_time_upload_url_from_upload_file(env):
    app, tok = env["app"], env["tok"]
    r = mcp(app, tok, "upload_file", wiki="main", path="raw/notes.txt", agent="owen")
    assert r["method"] == "PUT" and r["command"].startswith("curl") and r["expires_in"] == 900
    url = r["upload_url"].split("://", 1)[1].split("/", 1)[1]
    up = env["live"].put("/" + url, content=b"uploaded by an agent")
    assert up.status_code == 200 and up.json()["path"] == "raw/notes.txt"
    again = env["live"].put("/" + url, content=b"second try")
    assert again.status_code == 403 and "used or expired" in again.json()["error"]
    row = files.get(env["conn"], db.wiki_key(env["ws"]), "raw/notes.txt")
    assert row["agent"] == "owen" and row["user_id"]
    assert "agent is required" in mcp(app, tok, "upload_file", wiki="main", path="a.txt",
                                      agent="")["error"]
    assert "extension" in mcp(app, tok, "upload_file", wiki="main", path="notes",
                              agent="a")["error"]


def test_storage_limits_by_plan_and_file_size(env, monkeypatch, plans):
    conn, ws = env["conn"], env["ws"]
    assert files.storage_limit(conn, ws) == 100 * files.MB           # free: 100 MB pooled
    conn.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws,))
    conn.commit()
    assert files.storage_limit(conn, ws) == files.GB                 # one member
    conn.execute("UPDATE workspaces SET plan='business' WHERE id=?", (ws,))
    conn.commit()
    assert files.storage_limit(conn, ws) == 10 * files.GB            # one member
    conn.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws,))
    conn.commit()
    conn.execute("UPDATE workspaces SET storage_limit=? WHERE id=?", (1000, ws))
    conn.commit()
    assert put(env, "a.bin", b"x" * 600).status_code == 200
    r = put(env, "b.bin", b"x" * 600)
    assert r.status_code == 413 and "not enough storage" in r.json()["error"]
    assert put(env, "a.bin", b"y" * 900).status_code == 200          # replacing frees the old
    monkeypatch.setattr(files, "MAX_FILE_BYTES", 100)
    conn.execute("UPDATE workspaces SET storage_limit=NULL WHERE id=?", (ws,))
    conn.commit()
    r = put(env, "c.bin", b"x" * 101)
    assert r.status_code == 413 and "at most" in r.json()["error"]


def test_pages_and_their_history_count_toward_storage(env):
    """No plan limits history by age; it counts toward storage instead (Forrest,
    2026-09-27): the current text of every page, every stored revision, and the
    history of deleted pages."""
    app, tok, conn, ws = env["app"], env["tok"], env["conn"], env["ws"]
    before = files.page_usage(conn, ws)
    body = "# P\n\n" + "word " * 200 + "\n"
    mcp(app, tok, "write_page", wiki="main", path="notes/p", text=body)
    one = files.page_usage(conn, ws)
    assert one - before >= 2 * len(body)                    # the page, and its first revision
    mcp(app, tok, "edit_page", wiki="main", path="notes/p", old_text="# P", new_text="# Q")
    two = files.page_usage(conn, ws)
    assert two > one                                         # the edit's revision adds to it
    mcp(app, tok, "delete_page", wiki="main", path="notes/p")
    assert 0 < files.page_usage(conn, ws) - before < two - before    # history outlives the page
    assert files.usage(conn, ws) == files.file_usage(conn, ws) + files.page_usage(conn, ws)
    # History can fill the space an upload needs.
    conn.execute("UPDATE workspaces SET storage_limit=? WHERE id=?",
                 (files.page_usage(conn, ws) + 100, ws))
    conn.commit()
    assert put(env, "a.bin", b"x" * 90).status_code == 200
    r = put(env, "b.bin", b"x" * 20)
    assert r.status_code == 413 and "not enough storage" in r.json()["error"]
    page = env["c"].get("/settings/files").text
    assert "in files," in page and "in pages and their history" in page


def test_free_workspace_over_its_member_limit_is_read_only(env, monkeypatch, plans):
    """A plan that ends without the owner choosing Free (Stripe giving up on a card,
    or a plan changed by hand) can leave several people in a Free workspace. It is
    then read-only for everyone until an owner removes members or picks a plan
    (Forrest, 2026-09-27). Reading and Settings stay open."""
    from test_workspaces import invite_code
    app, tok, conn, ws, c = env["app"], env["tok"], env["conn"], env["ws"], env["c"]
    assert "error" not in mcp(app, tok, "write_page", wiki="main", path="notes/p", text="# P\n")
    assert put(env, "raw/a.txt", b"hi").status_code == 200
    conn.execute("UPDATE workspaces SET plan='team' WHERE id=?", (ws,))
    conn.commit()
    bob = browser(app)
    signup(bob, "bob@example.com")
    bob.get(f"/invite/{invite_code(c, monkeypatch, 'bob@example.com', w=ws)}")
    conn.execute("UPDATE workspaces SET plan='free' WHERE id=?", (ws,))
    conn.commit()

    w = mcp(app, tok, "write_page", wiki="main", path="notes/q", text="# Q\n")
    assert "read-only" in w["error"] and "2 members" in w["error"]
    assert "Settings, Members" in w["error"] and "Settings, Plan" in w["error"]
    for tool, args in [
            ("edit_page", {"path": "notes/p", "old_text": "# P", "new_text": "# R"}),
            ("append_page", {"path": "notes/p", "text": "more"}),
            ("move_page", {"path": "notes/p", "new_path": "notes/r"}),
            ("delete_page", {"path": "notes/p"}),
            ("change_pages", {"changes": [{"op": "delete", "path": "notes/p"}]}),
            ("upload_file", {"path": "raw/b.txt"}),
            ("delete_file", {"path": "raw/a.txt"})]:
        assert "read-only" in mcp(app, tok, tool, wiki="main", **args).get("error", ""), tool
    assert mcp(app, tok, "read_page", wiki="main", path="notes/p")["text"].startswith("# P")
    assert "error" not in mcp(app, tok, "search_pages", wiki="main", query="P")
    r = put(env, "raw/b.txt", b"x")
    assert r.status_code == 403 and "read-only" in r.json()["error"]
    assert env["live"].delete("/api/v1/files", params={"path": "raw/a.txt"},
                              headers={"Authorization": f"Bearer {tok}"}).status_code == 403
    page = c.get(f"/settings/members?w={ws}").text
    assert "This workspace is read-only." in page and "2 members" in page
    assert "https://app.dexio.wiki/settings/plan" not in page     # no link to where you are

    # Removing Bob brings it back.
    bob_id = auth.user_by_email(conn, "bob@example.com")["id"]
    assert c.post(f"/settings/members/{bob_id}/remove?w={ws}").status_code == 303
    assert "error" not in mcp(app, tok, "write_page", wiki="main", path="notes/q", text="# Q\n")
    assert "read-only" not in c.get(f"/settings/members?w={ws}").text


def test_files_have_their_own_delete_and_page_tools_refuse_them(env):
    app, tok, conn = env["app"], env["tok"], env["conn"]
    put(env, "raw/deck.pdf", pdf("hello"))
    w = mcp(app, tok, "write_page", wiki="main", path="notes/p",
            text="# P\n\nSee [the deck](../raw/deck.pdf) and ![](../images/x.png).\n")
    assert w["broken_links"] == []                                    # file links are not page links
    k = db.wiki_key(env["ws"])
    m = mcp(app, tok, "move_page", wiki="main", path="raw/deck.pdf", new_path="decks/q3.pdf")
    assert "is an uploaded file" in m["error"] and "upload_file" in m["error"]
    d = mcp(app, tok, "delete_page", wiki="main", path="raw/deck.pdf")
    assert "delete_file" in d["error"] and files.get(conn, k, "raw/deck.pdf")
    batch = mcp(app, tok, "change_pages", wiki="main",
                changes=[{"op": "delete", "path": "raw/deck.pdf"}])
    assert "delete_file" in batch["error"] and files.get(conn, k, "raw/deck.pdf")
    assert "is a page" in mcp(app, tok, "delete_file", wiki="main", path="notes/p")["error"]
    assert "no file" in mcp(app, tok, "delete_file", wiki="main", path="raw/none.pdf")["error"]
    gone = mcp(app, tok, "delete_file", wiki="main", path="raw/deck.pdf",
               agent="owen")                      # an agent that names itself anyway still works
    assert gone == {"ok": True, "deleted": "raw/deck.pdf"}
    assert not files.get(conn, k, "raw/deck.pdf")
    assert not any(env["store"].rglob("*")) or all(p.is_dir() for p in env["store"].rglob("*"))
    assert put(env, "x.txt", b"x", wiki="nope").status_code == 200    # ignored, not refused
    assert files.get(conn, k, "x.txt")


def test_browser_uploads_from_settings(env, plans):
    c = env["c"]
    r = c.put(f"/api/v1/files?path=raw/a.txt&w={env['ws']}", content=b"hi",
              headers={"Content-Type": "text/plain"})
    assert r.status_code == 200
    r = c.put(f"/api/v1/files?path=raw/b.txt&w={env['ws']}", content=b"hi",
              headers={"Origin": "https://evil.example"})
    assert r.status_code == 403
    page = c.get("/settings/files").text
    assert "Using <b>2 bytes</b> of 100 MB" in page and "raw/a.txt" in page
