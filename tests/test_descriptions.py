"""The one-line description list_pages gives each page (parse.description_of)."""
from dexio.parse import AUTHORED_CHARS, DESCRIPTION_CHARS, DESCRIPTION_SOURCE, description_of


def test_frontmatter_description_wins_over_the_first_sentence():
    text = "---\ntitle: X\ndescription: What X is for.\n---\n# X\n\nSomething else.\n"
    assert description_of(text) == "What X is for."


def test_summary_key_quotes_and_block_scalars():
    assert description_of("---\nsummary: 'Quoted one.'\n---\n# T\n") == "Quoted one."
    text = "---\ndescription: >\n  Folded over\n  two lines.\ntags: [a]\n---\n# T\n\nBody.\n"
    assert description_of(text) == "Folded over two lines."


def test_first_sentence_skips_title_headings_code_comments_tables_and_rules():
    text = ("# Title\n\n<!-- a comment -->\n\n```\ncode here\n```\n\n| a | b |\n|---|---|\n\n---\n\n"
            "## Background\n\n![logo](logo.png)\n\n"
            "The relay forwards **webhooks** to [[services/queue|the queue]]. It runs on EC2.\n")
    assert description_of(text) == "The relay forwards webhooks to the queue."


def test_markdown_is_reduced_to_plain_text():
    text = "# T\n\nSee [the docs](https://x.y) and `list_pages` for *more* on <b>it</b>. Next.\n"
    assert description_of(text) == "See the docs and list_pages for more on it."


def test_a_paragraph_wrapped_over_lines_is_one_sentence():
    text = "# T\n\nThis sentence is\nwrapped across\nthree lines. Then another.\n"
    assert description_of(text) == "This sentence is wrapped across three lines."


def test_quotes_and_lists():
    assert description_of("# T\n\n> Quoted opening line.\n") == "Quoted opening line."
    assert description_of("# T\n\n- First item\n- Second item\n") == "First item"
    assert description_of("# T\n\nIntro line\n- item\n") == "Intro line"


def test_abbreviations_and_decimals_do_not_end_a_sentence():
    text = "# T\n\nCosts about 2.5 times more, e.g. for storage. More.\n"
    assert description_of(text) == "Costs about 2.5 times more, e.g. for storage."


def test_long_text_is_clipped_on_a_word():
    s = description_of("# T\n\n" + "word " * 100)
    assert len(s) <= DESCRIPTION_CHARS and s.endswith("…") and " wor…" not in s
    long_fm = "---\ndescription: " + "word " * 100 + "\n---\n"
    assert DESCRIPTION_CHARS < len(description_of(long_fm)) <= AUTHORED_CHARS


def test_no_prose_means_no_description():
    assert description_of("") == ""
    assert description_of("# Only a title\n") == ""
    assert description_of("# T\n\n## [2026-09-01] start\n") == ""
    assert description_of("---\ntitle: x\n---\n") == ""


def test_frontmatter_longer_than_the_part_read_gives_nothing_rather_than_yaml():
    head = ("---\n" + "sources: [" + "x, " * DESCRIPTION_SOURCE + "]\n")[:DESCRIPTION_SOURCE]
    assert description_of(head) == ""
