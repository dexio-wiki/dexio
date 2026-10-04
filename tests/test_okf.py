"""The Open Knowledge Format's source and status fields (dexio/okf.py), in the
page panel and the MCP tools (Forrest, 2026-10-04, after comparing Dexio with
Google's OKF v0.2). Reviews shipped the same day and were rolled back; a page's
`verified` lines are left in its text and not read."""
from __future__ import annotations

import pytest

from dexio import okf

# OKF v0.2 section 10.2's example, with section 5.1's source entry added.
SPEC = """---
type: Attested Computation
title: Revenue for fiscal year
description: Recognized revenue for a fiscal year, per Finance's definition.
status: stable
runtime: bigquery
parameters:
  - { name: year, type: integer, required: true }
executor:
  resource: references/skills/run-on-bq.md
  receipt: [job_id, executed_sql, result]
generated: { by: reference_agent/gemini-2.5-pro, at: 2026-06-20T22:53:05Z }
verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }
stale_after: 2026-09-23T00:00:00Z
sources:
  - id: rev-policy
    resource: https://wiki.acme/finance/revenue-recognition
    title: Revenue recognition policy
  - id: ga4-schema
    resource: https://developers.google.com/analytics/bigquery/export-schema
    title: GA4 BigQuery Export schema
    author: team:ga4-docs
    usage_count: 5000
    last_modified: 2026-05-30T00:00:00Z
usage_window: { from: 2026-06-01T00:00:00Z, to: 2026-06-30T00:00:00Z }
---

# Revenue

Recognized revenue.[^rev-policy]
"""


def test_reads_the_spec_example():
    fm = okf.fields(SPEC)
    assert fm["parameters"] == [{"name": "year", "type": "integer", "required": "true"}]
    assert fm["executor"] == {"resource": "references/skills/run-on-bq.md",
                              "receipt": ["job_id", "executed_sql", "result"]}
    assert fm["verified"] == {"by": "human:ahormati", "at": "2026-06-25T09:00:00Z"}
    assert fm["usage_window"]["to"] == "2026-06-30T00:00:00Z"
    said = okf.summary(SPEC)
    assert set(said) == {"status", "sources"}, "reviews are not read"
    assert said["status"] == "stable"
    assert said["sources"][1] == {
        "resource": "https://developers.google.com/analytics/bigquery/export-schema",
        "id": "ga4-schema", "title": "GA4 BigQuery Export schema", "author": "team:ga4-docs",
        "last_modified": "2026-05-30T00:00:00Z", "usage_count": 5000}
    assert okf.summary("# no frontmatter\n") == {}
    assert okf.summary("---\ntitle: x\n---\n") == {}


def test_status_is_one_of_three():
    assert okf.summary("---\nstatus: Draft\n---\n")["status"] == "draft"
    assert okf.summary("---\nstatus: deprecated # superseded by x\n---\n")["status"] == "deprecated"
    assert "status" not in okf.summary("---\nstatus: wip\n---\n")


def test_free_text_sources_stay_whole():
    """Many wikis (this fleet's included) write sources as a flow list of plain
    text, with commas inside parentheses. YAML would split those; a source with
    its section numbers in brackets is one source."""
    text = ('---\ntitle: X\nsources: [SPEC.md v0.2 read 2026-09-27 (sections 5, 6.1, 11); GitHub API'
            ' 2026-09-27, "Google blog, 2026-06-12", https://example.com/a]\n---\n')
    assert okf.summary(text)["sources"] == [
        {"text": "SPEC.md v0.2 read 2026-09-27 (sections 5, 6.1, 11); GitHub API 2026-09-27"},
        {"text": "Google blog, 2026-06-12"}, {"text": "https://example.com/a"}]
    one = okf.summary("---\nsources: https://example.com/only\n---\n")["sources"]
    assert one == [{"text": "https://example.com/only"}]
    # An OKF entry needs its resource; one without it is skipped, not an error.
    assert okf.summary("---\nsources:\n  - { id: a, title: no resource }\n---\n") == {}


def test_block_lists_at_the_keys_indent_and_comments():
    text = ("---\nsources:\n- id: a  # the first\n  resource: https://a.example\n\n"
            "- plain one\ntags: [x, 'y, z']\n---\n")
    fm = okf.fields(text)
    assert fm["sources"] == [{"id": "a", "resource": "https://a.example"}, "plain one"]
    assert fm["tags"] == ["x", "y, z"]


@pytest.mark.parametrize("text", [
    "---\nsources: [unclosed\n---\n",
    "---\n- a list, not a mapping\n---\n",
    "---\nsources:\n    - {resource: x\n  junk: : :\n---\n",
    "---\nsources: {\n---\n",
    "---\n\tstatus:\tdraft\n---\n",
    "---\nstatus: |\n  multi\n  line\n---\n",
])
def test_odd_frontmatter_is_never_an_error(text):
    okf.summary(text)


# ---- the app and the MCP tools ------------------------------------------------

pytest.importorskip("fastapi")
pytest.importorskip("mcp")

from test_sharing import owner_with_wiki, public  # noqa: E402
from test_workspaces import app, browser, mcp, signup, token_from_connect  # noqa: E402,F401

PAGE = """---
status: draft
sources:
  - id: policy
    resource: https://example.com/policy
    title: Refund policy
  - Call with Jane, 2026-10-02
verified: { by: human:ann-lee, at: 2026-10-04T16:00:00Z }
---
# Refunds

Refunds take 14 days.[^policy]
"""


def note(c, path, **q):
    r = c.get("/api/v1/note", params={"project": "main", "path": path, **q})
    assert r.status_code == 200, r.text
    return r.json()


def test_note_and_mcp_say_status_and_sources(app):
    c = browser(app)
    assert signup(c, "ann@example.com").status_code == 303
    key = token_from_connect(c)
    assert mcp(app, key, "write_page", path="policies/refunds", text=PAGE).get("ok")
    said = note(c, "policies/refunds")["info"]["okf"]
    assert said == {"status": "draft", "sources": [
        {"resource": "https://example.com/policy", "id": "policy", "title": "Refund policy"},
        {"text": "Call with Jane, 2026-10-02"}]}
    head = mcp(app, key, "read_page", path="policies/refunds")
    assert head["status"] == "draft" and "review" not in head
    listed = {p["path"]: p for p in mcp(app, key, "list_pages")["pages"]}
    assert listed["policies/refunds"]["status"] == "draft"
    assert "review" not in listed["policies/refunds"]
    assert "changed_since_review" not in mcp(app, key, "wiki_health")
    # Mark reviewed is gone.
    r = c.post("/api/v1/review", json={"path": "policies/refunds", "version": ""})
    assert r.status_code in (404, 405)


def test_guests_see_status_and_sources_too(app):
    c, key, handle = owner_with_wiki(app)
    assert mcp(app, key, "write_page", path="index", text=PAGE).get("ok")
    public(c, handle, "wiki", "")
    said = note(browser(app), "index", w=handle)["info"]["okf"]
    assert said["status"] == "draft" and len(said["sources"]) == 2
