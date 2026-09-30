
import pytest

from dexio.parse import extract_links, scan


@pytest.fixture
def wiki(tmp_path):
    (tmp_path / "entities").mkdir()
    (tmp_path / "index.md").write_text(
        "# Index\n<!-- template: [[entities/page-name]] -->\n"
        "- [[entities/acme]]\n- [[concepts/pricing|how we price]]\n")
    (tmp_path / "entities" / "acme.md").write_text(
        "# Acme\nSee [[concepts/pricing]] and [[entities/acme]] (self).\n"
        "Also [a link](../concepts/pricing.md) and [ext](https://example.com).\n")
    (tmp_path / "concepts").mkdir()
    (tmp_path / "concepts" / "pricing.md").write_text(
        "# Pricing\nPoints at [[nowhere]].\n")
    (tmp_path / "orphan.md").write_text("# Orphan\nNothing links here.\n")
    return tmp_path


def files_of(root):
    """A push body's files for a folder of markdown."""
    from dexio.parse import read_pages
    return [{"path": p.path, "file": p.file, "text": p.text}
            for p in read_pages(root, keep_text=True).values()]


def test_extract_links_ignores_comments_and_urls():
    links = extract_links("<!-- [[skipme]] -->\n[[a/b]] [x](https://e.com) [y](c/d.md)")
    assert "skipme" not in links
    assert "a/b" in links and "c/d" in links


def test_links_inside_code_are_examples_not_links():
    text = (
        "Real [[a]], inline `[[inline]]`, double ``[[dbl `tick`]]`` and [x](`c/e.md`).\n"
        "```\n[[fenced]]\n[y](fenced/f.md)\n```\n"
        "~~~~python\n[[tilde]]\n~~~\n[[still-in-tilde]]\n~~~~\n"
        "Prose again [[b]] and [z](c/d.md). A lone ` backtick, then [[c]].\n"
        "<!-- [[commented]] -->\n"
        "    indented ```span``` is inline, so [[d]] counts\n"
    )
    assert extract_links(text) == ["a", "b", "c", "d", "c/d"]


def test_code_mask_edges():
    # The wiki's own SCHEMA line that used to show up as a broken link.
    assert extract_links("• Use `[[wikilinks]]` between related pages.") == []
    # An unclosed fence runs to the end of the page.
    assert extract_links("[[x]]\n```\n[[y]]\n") == ["x"]
    # A backtick fence whose info string holds a backtick is not a fence.
    assert extract_links("``` a`b\n[[z]]\n") == ["z"]
    # An inline span cannot close across a blank line.
    assert extract_links("`open\n\n[[p]] then `") == ["p"]
    # An escaped backtick does not open a span.
    assert extract_links(r"\`[[q]]`") == ["q"]


def test_graph_shape(wiki):
    g = scan(wiki)
    assert g.stats()["pages"] == 4
    assert ("index", "entities/acme") in g.edges
    assert ("entities/acme", "concepts/pricing") in g.edges
    assert not any(s == d for s, d in g.edges), "self links are dropped"
    assert g.dangling == [("concepts/pricing", "nowhere")]
    assert g.orphans() == ["orphan"]
    assert "index" in g.unreferenced()


def test_titles_and_aliases(wiki):
    g = scan(wiki)
    assert g.pages["entities/acme"].title == "Acme"
    assert ("index", "concepts/pricing") in g.edges  # [[path|alias]] resolves


def test_html_is_self_contained(wiki):
    from dexio.render import offline
    html = offline(scan(wiki).to_dict(include_text=True), title="t")
    assert "http://" not in html.split("<script>")[0]
    assert "dexio.load(" in html
    assert "__GRAPH_JS__" not in html


def test_server_round_trip(wiki, tmp_path, monkeypatch):
    fastapi = pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from dexio.server import db
    from dexio.server.app import get_app

    monkeypatch.setenv("DEXIO_ADMIN_EMAIL", "admin@example.com")
    monkeypatch.setenv("DEXIO_ADMIN_PASSWORD", "round-trip-password")
    app = get_app(str(tmp_path / "test.db"))
    client = TestClient(app)
    viewer = ("admin@example.com", "round-trip-password")

    assert client.get("/healthz").json()["ok"] is True

    # There is no operator endpoint: API keys come only from a signed-in person.
    for method, path in (("post", "/api/v1/tokens?name=nope"), ("get", "/api/v1/tokens"),
                         ("get", "/api/v1/users")):
        r = getattr(client, method)(path, headers={"Authorization": "Bearer admin-secret"})
        assert r.status_code in (404, 405), (path, r.status_code)

    from dexio.parse import read_pages
    conn = app.state.conn
    stats = db.commit_pages(conn, db.wiki_key(db.default_workspace(conn)),
                            dict(read_pages(wiki, keep_text=True)), source="macmini-1",
                            author="macmini-1", op="push", is_push=True)
    assert stats["pages"] == 4

    assert client.get("/api/v1/graph?project=fleet-a").status_code == 401

    # A workspace has one wiki (2026-09-28): an old `project` is ignored.
    g = client.get("/api/v1/graph?project=fleet-a", auth=viewer).json()
    assert len(g["nodes"]) == 4
    assert {"source": "index", "target": "entities/acme"} in g["links"]
    assert g["project"] == "main"

    note = client.get("/api/v1/note?path=entities/acme", auth=viewer).json()
    assert "Acme" in note["text"]

    assert client.get("/api/v1/projects", auth=viewer).status_code == 404   # nothing to list
    assert "dexio" in client.get("/", auth=viewer).text
    assert client.get("/api/v1/history", auth=viewer).json()["pushes"][0]["pages"] == 4


def test_server_with_no_accounts_is_closed(tmp_path, monkeypatch):
    """A fresh server must not serve pushed wikis before anyone has an account."""
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    from dexio.server.app import get_app

    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_VIEW_PASSWORD"):
        monkeypatch.delenv(var, raising=False)
    client = TestClient(get_app(str(tmp_path / "fresh.db")))
    assert client.get("/api/v1/graph").status_code == 401
    r = client.get("/", headers={"accept": "text/html"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/login")
    assert "Create a free account" in client.get("/login").text
