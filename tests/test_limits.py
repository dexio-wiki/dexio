"""Page size: a 5 MB abuse limit with a warning from 80%, and read_page returning
at most READ_MAX_BYTES per call with next_offset to continue."""
from __future__ import annotations

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402

from dexio.server.app import get_app  # noqa: E402
from dexio.server.mcp_server import MAX_PAGE_BYTES, READ_MAX_BYTES  # noqa: E402

from test_workspaces import browser, mcp, signup, token_from_connect  # noqa: E402


@pytest.fixture()
def tok(tmp_path, monkeypatch):
    for var in ("DEXIO_ADMIN_EMAIL", "DEXIO_ADMIN_PASSWORD", "DEXIO_PUSH_TOKEN"):
        monkeypatch.delenv(var, raising=False)
    a = get_app(str(tmp_path / "l.db"))
    with TestClient(a, base_url="https://testserver") as live:
        a.state.live = live
        c = browser(a)
        signup(c, "ann@example.com")
        yield a, token_from_connect(c)


def lines(n_bytes: int, tag: str = "x") -> str:
    row = f"- {tag} " + "y" * 90 + "\n"                 # 95 bytes a line
    return "".join(row for _ in range(n_bytes // len(row)))


def test_big_pages_write_and_warn_near_the_limit(tok):
    app, t = tok
    r = mcp(app, t, "write_page", wiki="main", path="big", text="# Big\n" + lines(300 * 1024))
    assert r["created"] and "warning" not in r            # over the old 256 KB cap
    near = "# Log\n" + lines(int(MAX_PAGE_BYTES * 0.85))
    r = mcp(app, t, "write_page", wiki="main", path="log", text=near)
    assert r["bytes"] == len(near) and "of the 5.0 MB limit" in r["warning"]
    r = mcp(app, t, "append_page", wiki="main", path="log", text="- one more")
    assert "warning" in r
    too_big = "# X\n" + lines(MAX_PAGE_BYTES + 1000)
    err = mcp(app, t, "write_page", wiki="main", path="x", text=too_big)["error"]
    assert "over the 5.0 MB limit" in err and "split it" in err


def test_read_page_returns_a_bounded_slice_and_continues(tok):
    app, t = tok
    text = "# Big\n\n## One\n" + lines(200 * 1024, "one") + "## Two\n" + lines(100 * 1024, "two")
    mcp(app, t, "write_page", wiki="main", path="big", text=text)
    got, offset, calls = [], None, 0
    while True:
        args = {"offset": offset} if offset else {}
        r = mcp(app, t, "read_page", wiki="main", path="big", **args)
        calls += 1
        assert len(r["text"].encode()) <= READ_MAX_BYTES
        got.append(r["text"])
        if not r.get("next_offset"):
            break
        assert r["truncated"] and r["outline"] and "offset=" in r["hint"]
        offset = r["next_offset"]
    assert "".join(got) == text and calls == 3
    # A section that fits comes back whole; a small page is untouched.
    two = mcp(app, t, "read_page", wiki="main", path="big", section="Two")
    assert "truncated" not in two and two["text"].startswith("## Two")
    mcp(app, t, "write_page", wiki="main", path="small", text="# Small\n\nhi\n")
    s = mcp(app, t, "read_page", wiki="main", path="small")
    assert s["text"] == "# Small\n\nhi\n" and "truncated" not in s
