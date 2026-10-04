"""The Open Knowledge Format's review, source and status fields (dexio/okf.py),
in the page panel, the Mark reviewed button and the MCP tools (Forrest,
2026-10-04: "Let's do 1-3" after comparing Dexio with Google's OKF v0.2)."""
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

T = okf.parse_time


def test_reads_the_spec_example():
    fm = okf.fields(SPEC)
    assert fm["parameters"] == [{"name": "year", "type": "integer", "required": "true"}]
    assert fm["executor"] == {"resource": "references/skills/run-on-bq.md",
                              "receipt": ["job_id", "executed_sql", "result"]}
    assert fm["usage_window"]["to"] == "2026-06-30T00:00:00Z"
    said = okf.summary(SPEC, T("2026-06-25T09:00:01Z"))
    assert said["status"] == "stable"
    assert said["review"] == {"tier": "human-reviewed", "by": "human:ahormati",
                              "at": T("2026-06-25T09:00:00Z"), "reviewers": ["human:ahormati"],
                              "edited_since": False}
    assert said["sources"][1] == {
        "resource": "https://developers.google.com/analytics/bigquery/export-schema",
        "id": "ga4-schema", "title": "GA4 BigQuery Export schema", "author": "team:ga4-docs",
        "last_modified": "2026-05-30T00:00:00Z", "usage_count": 5000}
    assert said["generated"] == {"by": "reference_agent/gemini-2.5-pro", "kind": "agent",
                                 "at": T("2026-06-20T22:53:05Z")}


def test_trust_tiers_follow_the_actors():
    def tier(v):
        return okf.tier(okf.reviews(okf.fields(f"---\nverified: {v}\n---\n")))
    assert tier("[]") == "unverified"
    assert tier("{ by: niko, at: 2026-10-01 }") == "machine-confirmed"
    assert tier("[{ by: process:nightly }, { by: niko }]") == "machine-confirmed"
    assert tier("[{ by: niko }, { by: human:ann }]") == "human-reviewed"
    assert okf.summary("# no frontmatter\n") == {}
    assert "review" not in okf.summary("---\ntitle: x\n---\n")


def test_a_page_changed_after_its_review_says_so():
    text = "---\nverified:\n  - { by: human:ann, at: 2026-10-01T00:00:00Z }\n---\n# A\n"
    at = T("2026-10-01T00:00:00Z")
    assert not okf.summary(text, at + 2)["review"]["edited_since"]       # the review's own write
    assert okf.summary(text, at + 3600)["review"]["edited_since"]
    # The latest human review counts; an agent's later check does not lift a human tier.
    text2 = ("---\nverified:\n  - { by: human:ann, at: 2026-10-01T00:00:00Z }\n"
             "  - { by: human:bo, at: 2026-10-03T00:00:00Z }\n  - { by: niko, at: 2026-10-04T00:00:00Z }\n---\n")
    rv = okf.summary(text2, T("2026-10-03T00:00:01Z"))["review"]
    assert rv["by"] == "human:bo" and rv["reviewers"] == ["human:ann", "human:bo"]
    assert not rv["edited_since"]


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


@pytest.mark.parametrize("text", [
    "---\nverified: [unclosed\n---\n",
    "---\n- a list, not a mapping\n---\n",
    "---\nverified:\n    - {by: x\n  junk: : :\n---\n",
    "---\nsources: {\n---\n",
    "---\n\tstatus:\tdraft\n---\n",
    "---\nstatus: |\n  multi\n  line\n---\n",
])
def test_odd_frontmatter_is_never_an_error(text):
    okf.summary(text, 1.0)
    okf.add_review(text, "human:ann", 1.0)


def test_add_review_writes_one_entry_per_reviewer():
    at1, at2 = T("2026-10-04T10:00:00Z"), T("2026-10-05T10:00:00Z")
    once = okf.add_review(SPEC, "human:forrest-zhang", at1)
    assert ("verified:\n  - { by: human:ahormati, at: 2026-06-25T09:00:00Z }\n"
            "  - { by: human:forrest-zhang, at: 2026-10-04T10:00:00Z }\nstale_after:") in once
    assert once.replace("verified:\n  - { by: human:ahormati, at: 2026-06-25T09:00:00Z }\n"
                        "  - { by: human:forrest-zhang, at: 2026-10-04T10:00:00Z }\n",
                        "verified: { by: human:ahormati, at: 2026-06-25T09:00:00Z }\n") == SPEC
    twice = okf.add_review(once, "human:forrest-zhang", at2)
    assert twice.count("human:forrest-zhang") == 1 and "2026-10-05T10:00:00Z" in twice
    assert okf.summary(twice, at2)["review"]["reviewers"] == ["human:ahormati", "human:forrest-zhang"]


def test_add_review_to_pages_without_verified_or_frontmatter():
    at = T("2026-10-04T10:00:00Z")
    assert okf.add_review("# Hello\n\nbody\n", "human:ann", at) == (
        "---\nverified:\n  - { by: human:ann, at: 2026-10-04T10:00:00Z }\n---\n# Hello\n\nbody\n")
    assert okf.add_review("---\ntitle: x\n\nstatus: draft\n---\n# Hello\n", "human:ann", at) == (
        "---\ntitle: x\n\nstatus: draft\nverified:\n  - { by: human:ann, at: 2026-10-04T10:00:00Z }\n"
        "---\n# Hello\n")
    # A block list at the key's own indent, then a blank line and another key.
    text = "---\nverified:\n- by: niko\n  at: 2026-10-01\n\ntags: [a]\n---\n"
    out = okf.add_review(text, "human:ann", at)
    assert out == ("---\nverified:\n  - { by: niko, at: 2026-10-01 }\n"
                   "  - { by: human:ann, at: 2026-10-04T10:00:00Z }\n\ntags: [a]\n---\n")
    assert okf.fields(out)["tags"] == ["a"]


def test_human_actor_from_a_name():
    assert okf.human_actor("Forrest Zhang", "member-1") == "human:forrest-zhang"
    assert okf.human_actor(" ", "member-7") == "human:member-7"
    assert okf.human_actor("Zoë O'Neil", "x") == "human:zoë-o-neil"


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
---
# Refunds

Refunds take 14 days.[^policy]
"""


def note(c, path, **q):
    r = c.get("/api/v1/note", params={"project": "main", "path": path, **q})
    assert r.status_code == 200, r.text
    return r.json()


def review(c, path, version, **q):
    return c.post("/api/v1/review" + (f"?w={q['w']}" if q.get("w") else ""),
                  json={"path": path, "version": version})


def owner(app, first="Ann", last="Lee"):
    c = browser(app)
    assert signup(c, "ann@example.com", first=first).status_code == 303
    app.state.conn.execute("UPDATE users SET last_name=? WHERE email=?", (last, "ann@example.com"))
    app.state.conn.commit()
    key = token_from_connect(c)
    return c, key


def test_note_says_status_sources_and_review(app):
    c, key = owner(app)
    assert mcp(app, key, "write_page", path="policies/refunds", text=PAGE).get("ok")
    got = note(c, "policies/refunds")
    said = got["info"]["okf"]
    assert said["status"] == "draft" and "review" not in said
    assert said["sources"] == [{"resource": "https://example.com/policy", "id": "policy",
                                "title": "Refund policy"}, {"text": "Call with Jane, 2026-10-02"}]
    assert got["version"]


def test_mark_reviewed_records_the_person(app):
    c, key = owner(app)
    assert mcp(app, key, "write_page", path="policies/refunds", text=PAGE).get("ok")
    got = note(c, "policies/refunds")
    r = review(c, "policies/refunds", got["version"])
    assert r.status_code == 200, r.text
    out = r.json()
    assert "  - { by: human:ann-lee, at: " in out["text"]
    rv = out["info"]["okf"]["review"]
    assert rv["tier"] == "human-reviewed" and rv["name"] == "Ann Lee" and rv["mine"]
    assert not rv["edited_since"] and out["info"]["revisions"] == 2
    # The review is a change of its own in the history, by the person.
    revs = c.get("/api/v1/page-history", params={"project": "main", "path": "policies/refunds"}).json()
    top = revs["revisions"][0]
    assert top["op"] == "review" and top["note"] == "Marked reviewed" and top["agent"] == "Ann Lee"
    # A later edit leaves the review standing but changed since.
    app.state.conn.execute("UPDATE pages SET updated_at=updated_at+60 WHERE path=?",
                           ("policies/refunds",))
    app.state.conn.commit()
    later = note(c, "policies/refunds")["info"]["okf"]["review"]
    assert later["edited_since"] and later["mine"]
    # The MCP tools say the same.
    head = mcp(app, key, "read_page", path="policies/refunds")
    assert head["status"] == "draft"
    assert head["review"]["tier"] == "human-reviewed" and head["review"]["by"] == "human:ann-lee"
    assert head["review"]["edited_since"] is True
    listed = {p["path"]: p for p in mcp(app, key, "list_pages")["pages"]}
    assert listed["policies/refunds"]["status"] == "draft"
    assert listed["policies/refunds"]["review"]["by"] == "human:ann-lee"
    health = mcp(app, key, "wiki_health")
    assert [d["path"] for d in health["changed_since_review"]] == ["policies/refunds"]


def test_a_page_changed_since_it_was_read_is_not_marked(app):
    c, key = owner(app)
    assert mcp(app, key, "write_page", path="a", text="# A\n\none\n").get("ok")
    v = note(c, "a")["version"]
    assert mcp(app, key, "write_page", path="a", text="# A\n\ntwo\n").get("ok")
    r = review(c, "a", v)
    assert r.status_code == 409 and "changed after you opened it" in r.json()["error"]
    assert "verified" not in note(c, "a")["text"]
    assert review(c, "nope", "").status_code == 404


def test_only_members_mark_reviewed_and_guests_see_no_names(app):
    c, key, handle = owner_with_wiki(app)
    assert mcp(app, key, "write_page", path="index", text=PAGE).get("ok")
    assert review(c, "index", note(c, "index")["version"]).status_code == 200
    public(c, handle, "wiki", "")
    anon = browser(app)
    got = note(anon, "index", w=handle)
    rv = got["info"]["okf"]["review"]
    assert set(rv) == {"tier", "at", "edited_since"} and rv["tier"] == "human-reviewed"
    assert review(anon, "index", got["version"], w=handle).status_code in (401, 403)
    other = browser(app)
    assert signup(other, "eve@example.com").status_code == 303
    assert review(other, "index", got["version"], w=handle).status_code in (403, 404)
