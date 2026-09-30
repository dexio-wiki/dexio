"""No native browser dropdowns anywhere in Dexio (Forrest, 2026-09-28: "as a
standing rule we should not use native browser dropdowns"). A native select
draws its closed box and its open list in the browser's colours, not the
page's: unreadable in the dark theme and plainer than every menu around it. A
choice is a button that opens a menu drawn by the page (the Layout, Show and
workspace menus, the share dialog's pickers), or rows of choices on a form.

The check reads the source, so a select added in Python, a template or
JavaScript fails here before it ships."""
import re
from pathlib import Path

from dexio.server import pages

SRC = Path(__file__).resolve().parent.parent / "src" / "dexio"
NATIVE = re.compile(r"<select[\s>/]|createElement\(\s*[\"'`]select[\"'`]", re.I)


def test_no_native_select_in_the_source():
    found = []
    for f in sorted(SRC.rglob("*")):
        if f.suffix not in {".py", ".js", ".mjs", ".html", ".css", ".svg"} or "__pycache__" in f.parts:
            continue
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if NATIVE.search(line):
                found.append(f"{f.relative_to(SRC)}:{n}: {line.strip()[:100]}")
    assert not found, "native select (use a menu button or radio rows):\n" + "\n".join(found)


def test_choosing_a_workspace_is_rows_not_a_select():
    ws = [{"id": 1, "handle": "acme", "name": "Acme"}, {"id": 2, "handle": "wren", "name": "Wrenfield"}]
    for page in (pages.consent_page("Claude", "claude.ai", "a@example.com", ws, 2, "r"),
                 pages.device_page("Claude", "ABCD-EFGH", "a@example.com", ws, 2)):
        assert "<select" not in page and "<option" not in page
        assert page.count('type="radio" name="workspace"') == 2
        assert re.search(r'name="workspace" value="wren" checked', page)
        assert not re.search(r'value="acme" checked', page)
    # one workspace: nothing to choose
    one = pages.consent_page("Claude", "claude.ai", "a@example.com", ws[:1], 1, "r")
    assert 'type="hidden" name="workspace" value="acme"' in one and "Which workspace" not in one
