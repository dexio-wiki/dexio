"""Word search (search.py): a question finds the page that answers it even when
its words are not one exact phrase on the page. The cases in the first test are
the ones a customer's agent reported on 2026-09-26, where phrase matching found 0,
2 and 8 pages for the same question."""
from __future__ import annotations

import re
from types import SimpleNamespace

from dexio.search import parse_query, regex_search, search_pages


def P(path, text, title=None):
    return SimpleNamespace(path=path, title=title or path.rsplit("/", 1)[-1], text=text)


PAGES = [
    P("runbooks/relay", "# Relay\n\nDeploying the relay: run `make deploy` on the host.\n"
      "The relay restarts itself.\n", "Relay runbook"),
    P("runbooks/api", "# API\n\nDeploy the API with the pipeline.\n", "API runbook"),
    P("entities/relay-host", "# Relay host\n\nWhere the relay runs. No deploy notes here.\n"),
    P("log", "# Log\n\n- relay upgraded\n- deploy froze for a day\n"),
    P("concepts/drift", "# Drift\n\nSensors drift about 2% a year.\n"),
]


def paths(result):
    return [h["path"] for h in result["results"]]


def test_a_question_matches_on_its_words():
    for q in ("how do I deploy the relay", "deploy relay", "relay deploy",
              "How do I deploy the relay?"):
        r = search_pages(PAGES, q)
        assert sorted(r["terms"]) == ["deploy", "relay"] and r["matched_all"], q
        # Same pages, same order, whatever the phrasing.
        assert paths(r) == ["runbooks/relay", "entities/relay-host", "log"], q


def test_ranking_title_then_words_together_then_frequency():
    r = search_pages(PAGES, "deploy relay")
    top = r["results"][0]
    # Title holds "relay", and one line holds both words: first, and its best line first.
    assert top["path_or_title_match"]
    assert top["matches"][0]["text"].startswith("Deploying the relay")
    # entities/relay-host has relay in its path; log has the words on separate lines.
    assert paths(r).index("entities/relay-host") < paths(r).index("log")


def test_substring_so_deploy_finds_deployment():
    r = search_pages([P("a", "Deployment steps for the relay.\n")], "deploy relay")
    assert paths(r) == ["a"]


def test_the_page_named_for_the_query_comes_first():
    # 2026-09-26: "Northwind" in a customer's wiki put a 13,000-word
    # deployment page titled "Northwind Hermes EC2 Deployment" first and the
    # page titled "Northwind" far down, because the tiebreak was raw count.
    pages = [
        P("engineering/hermes-ec2-deployment", "Northwind deploys.\n" * 105 + "filler " * 13000,
          "Northwind Hermes EC2 Deployment"),
        P("customer-success/nas", "Northwind Agent Service.\n" * 78, "Northwind Agent Service (NAS)"),
        P("entities/northwind", "# Northwind\n\nA roaster. Northwind sells coffee.\n", "Northwind"),
        P("log", "- Northwind note\n" * 40, "Wiki Log"),
    ]
    assert paths(search_pages(pages, "Northwind"))[0] == "entities/northwind"
    assert paths(search_pages(pages, "northwind"))[0] == "entities/northwind"
    # The last part of the path counts as a name too, and a question can name a page.
    idx = [P("index", "northwind northwind\n", "Wiki Index"),
           P("marketing/gsc-index-monitor", "index " * 50, "GSC index monitor")]
    assert paths(search_pages(idx, "index"))[0] == "index"
    howto = [P("runbooks/relay", "relay deploy relay deploy\n", "Relay runbook"),
             P("runbooks/deploying-the-relay", "Steps.\n", "Deploying the relay")]
    assert paths(search_pages(howto, "how do I deploy the relay"))[0] == "runbooks/deploying-the-relay"


def test_a_long_page_does_not_win_on_size():
    short = P("notes/short", "The relay moved hosts.\n")
    long_ = P("notes/long", "relay\n" + "unrelated words here\n" * 2000 + "relay again\n")
    assert paths(search_pages([long_, short], "relay")) == ["notes/short", "notes/long"]


def test_partial_results_when_no_page_has_every_word():
    r = search_pages(PAGES, "deploy relay kubernetes")
    assert not r["matched_all"]
    assert paths(r)[:3] == ["runbooks/relay", "entities/relay-host", "log"]
    assert all("kubernetes" not in h["words_matched"] for h in r["results"])


def test_quotes_match_a_phrase_exactly():
    assert paths(search_pages(PAGES, '"deploy the relay"')) == []
    assert paths(search_pages(PAGES, '"deploying the relay"')) == ["runbooks/relay"]
    assert parse_query('"make deploy" host') [0].phrase


def test_stopwords_only_when_something_else_is_left():
    assert [t.text for t in parse_query("the relay")] == ["relay"]
    assert [t.text for t in parse_query("the")] == ["the"]
    assert [t.text for t in parse_query("2% a year")] == ["2%", "year"]
    assert paths(search_pages(PAGES, "2% a year")) == ["concepts/drift"]


def test_case_sensitive_and_folder():
    r = search_pages(PAGES, "Relay", case_sensitive=True)
    assert "log" not in paths(r)                  # log only has lower-case relay
    assert paths(search_pages(PAGES, "relay", folder="runbooks")) == ["runbooks/relay"]


def test_nothing_and_punctuation_only():
    assert search_pages(PAGES, "((")["results"] == []
    assert search_pages(PAGES, "zzz")["results"] == []


def test_regex_search_unchanged():
    hits = regex_search(PAGES, re.compile(r"\d% a year", re.I))
    assert [h["path"] for h in hits] == ["concepts/drift"]
    assert hits[0]["matches"] == [{"line": 3, "text": "Sensors drift about 2% a year."}]
