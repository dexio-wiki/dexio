/* Exercises the panel's markdown renderer in isolation.
 * Run: node tests/test_markdown.mjs
 * graph.js is an IIFE that needs DOM globals, so we stub just enough of one.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "../src/dexio/static/graph.js"), "utf8");

// --- minimal DOM ---------------------------------------------------------
const el = () => ({
  getContext: () => new Proxy({}, { get: () => () => {}, set: () => true }),
  addEventListener() {}, classList: { add() {}, remove() {} },
  querySelector: () => ({}), querySelectorAll: () => [],
  style: {}, clientWidth: 800, clientHeight: 600, dataset: {},
  getBoundingClientRect: () => ({ left: 0, top: 0 }),
  innerHTML: "", textContent: "",
});
globalThis.document = {
  getElementById: el, documentElement: { dataset: { theme: "light" } },
  querySelector: el,
};
globalThis.window = { addEventListener() {}, devicePixelRatio: 1 };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.requestAnimationFrame = () => 0;
globalThis.matchMedia = () => ({ matches: false, addEventListener() {} });

// graph.js assigns window.dexio; expose its internals for the test by
// evaluating in this scope and grabbing the functions it closes over.
const wrapped = src.replace(
  "window.dexio = { load, resize, select };",
  "window.dexio = { load, resize, select, renderMarkdown, resolveWikiLink, state, " +
  "buildTree, treeHtml, defaultExpanded, backlinks, rankedMatches, termPattern, " +
  "matchedFolders, findRows, sourcesBlock, reviewBit };");
new Function(wrapped)();
const { renderMarkdown, state, buildTree, treeHtml, defaultExpanded, backlinks, rankedMatches, termPattern,
        matchedFolders, findRows, sourcesBlock, reviewBit } = globalThis.window.dexio;

// pretend these pages exist
for (const id of ["entities/acme-corp", "index", "people/jane-doe"]) {
  state.byId.set(id, { id, title: id });
}

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (e) { failures++; console.log(`  FAIL ${name}\n       ${e.message}`); }
}

console.log("markdown renderer");

check("the wiki's images render and links to its files open them", () => {
  window.DEXIO_API = "/api/v1"; window.DEXIO_PROJECT = "main";
  state.files = new Set(["images/arch.png", "raw/deck.pdf"]);
  const html = renderMarkdown(
    "![Architecture](../images/arch.png) and [the deck](../raw/deck.pdf) and ![[arch.png]]" +
    " and [[raw/deck.pdf|deck]] and ![missing](nope.png)", "notes/p");
  assert.match(html, /<img class="md-img" loading="lazy" src="\/api\/v1\/files\?path=images%2Farch\.png" alt="Architecture">/);
  assert.match(html, /<a href="\/api\/v1\/files\?path=raw%2Fdeck\.pdf" target="_blank" rel="noopener">the deck<\/a>/);
  // A workspace has one wiki (2026-09-28): a file's URL names only its path.
  assert.ok(!html.includes("wiki="), "no wiki in a file URL");
  assert.equal((html.match(/<img /g) || []).length, 2, "the embed renders too");
  assert.match(html, /rel="noopener">deck<\/a>/);
  assert.ok(!html.includes("nope.png\""), "an unknown file is not fetched");
  state.files = new Set();
  delete window.DEXIO_API;
});

check("wikilink to an existing page becomes a clickable link", () => {
  const html = renderMarkdown("Founder behind [[entities/acme-corp]].", "x");
  assert.match(html, /<a href="#" class="wl" data-id="entities\/acme-corp">/);
  assert.ok(!html.includes("[["), "raw brackets should be gone");
});

check("a wikilink reads as the page's title, not its path", () => {
  state.byId.set("entities/globex", { id: "entities/globex", title: "Globex Corp" });
  const html = renderMarkdown("Founded by [[entities/globex]]'s team.", "x");
  assert.match(html, /data-id="entities\/globex">Globex Corp<\/a>&#39;s team/);
  assert.match(renderMarkdown("[[entities/globex|Globex]]", "x"), />Globex<\/a>/);
  assert.match(renderMarkdown("[[nope/missing-page]]", "x"), />nope\/missing-page</);
});

check("the page's opening H1 is dropped when the header already shows it", () => {
  const src = "---\ntitle: Jane\n---\n\n<!-- note -->\n# Jane Doe\n\nBody.\n\n## Overview";
  const html = renderMarkdown(src, "x", "Jane Doe");
  assert.ok(!html.includes("Jane Doe"), html);
  assert.match(html, /<p>Body.<\/p>/);
  assert.match(html, /<h3 id="s-overview">Overview<\/h3>/);
  // Kept when it is not the title, or not the first thing on the page.
  assert.match(renderMarkdown("# Other\n\nx", "x", "Jane Doe"), /<h2 id="s-other">Other<\/h2>/);
  assert.match(renderMarkdown("Intro.\n\n# Jane Doe", "x", "Jane Doe"), /<h2 id="s-jane-doe">Jane Doe<\/h2>/);
  assert.match(renderMarkdown("# Jane Doe", "x"), /<h2 id="s-jane-doe">Jane Doe<\/h2>/);
});

check("backlinks list pages by title, sorted, with the path on hover", () => {
  state.byId.set("notes/zeta", { id: "notes/zeta", title: "Alpha notes" });
  const html = backlinks(["entities/globex", "notes/zeta"]);
  assert.ok(html.indexOf("Alpha notes") < html.indexOf("Globex Corp"), html);
  assert.match(html, /data-id="notes\/zeta" title="notes\/zeta">Alpha notes<\/a>/);
  assert.match(html, /<b>Linked from<\/b>/);
  assert.equal(backlinks([]), "");
  state.byId.set("notes/x2", { id: "notes/x2", title: "<b>x</b>" });
  assert.ok(!backlinks(["notes/x2"]).includes("<b>x</b>"), "titles are escaped");
});

check("wikilink to a missing page is marked broken, not silently plain", () => {
  const html = renderMarkdown("see [[nope/does-not-exist]]", "x");
  assert.match(html, /class="wl-missing"/);
});

check("aliased wikilink shows the alias", () => {
  const html = renderMarkdown("[[index|the index]]", "x");
  assert.match(html, />the index</);
  assert.match(html, /data-id="index"/);
});

check("anchors are stripped when resolving", () => {
  assert.match(renderMarkdown("[[index#overview]]", "x"), /data-id="index"/);
});

check("bare filename resolves when unique", () => {
  assert.match(renderMarkdown("[[jane-doe]]", "index"),
               /data-id="people\/jane-doe"/);
});

check("headings render and are demoted below the page title", () => {
  const html = renderMarkdown("# Jane Doe\n\n## Overview", "x");
  assert.match(html, /<h2 id="s-jane-doe">Jane Doe<\/h2>/);
  assert.match(html, /<h3 id="s-overview">Overview<\/h3>/);
});

check("bold, italic and inline code", () => {
  const html = renderMarkdown("**CEO, Northwind.** and *maybe* and `code`", "x");
  assert.match(html, /<strong>CEO, Northwind\.<\/strong>/);
  assert.match(html, /<em>maybe<\/em>/);
  assert.match(html, /<code>code<\/code>/);
});

check("lists", () => {
  const html = renderMarkdown("- one\n- two\n\n1. first\n2. second", "x");
  assert.match(html, /<ul>\n<li>one<\/li>\n<li>two<\/li>\n<\/ul>/);
  assert.match(html, /<ol>/);
});

check("a wrapped list item keeps its continuation lines", () => {
  const src = "- **CEO, Northwind.** Coffee roaster. Wholesale, the cafe\n"
            + "  accounts, is its primary priority.\n"
            + "- **Harbor Holdings** is its parent. Northwind runs under it\n"
            + "until there is revenue.\n"
            + "- last\n\nAfter the list.";
  const html = renderMarkdown(src, "x");
  assert.match(html, /<li><strong>CEO, Northwind\.<\/strong> Coffee roaster\. Wholesale, the cafe accounts, is its primary priority\.<\/li>/);
  assert.match(html, /<li><strong>Harbor Holdings<\/strong> is its parent\. Northwind runs under it until there is revenue\.<\/li>/);
  assert.match(html, /<li>last<\/li>\n<\/ul>\n<p>After the list\.<\/p>/);
  assert.equal((html.match(/<p>/g) || []).length, 1, "no continuation line may fall out into a paragraph");
});

check("a list item stops at the next block", () => {
  const html = renderMarkdown("1. first\n   wrapped\n## Next\n- a\n---", "x");
  assert.match(html, /<li>first wrapped<\/li>\n<\/ol>\n<h3 id="s-next">Next<\/h3>/);
  assert.match(html, /<li>a<\/li>\n<\/ul>\n<hr>/);
});

check("numbered steps split by code blocks keep counting", () => {
  // The guides' how-to steps: a code block after step 1 started a new list, and
  // every step after it showed "1." (Forrest, 2026-10-01).
  const html = renderMarkdown(
    "1. Scaffold:\n\n```js\nx()\n```\n\n2. Define the content.\n\n3. Decide what is published:\n\n```ts\ny()\n```\n\n4. Put the head tags in one layout.", "x");
  assert.match(html, /<ol>\n<li>Scaffold:<\/li>\n<\/ol>\n<pre><code>x\(\)<\/code><\/pre>/);
  assert.match(html, /<ol start="2">\n<li>Define the content\.<\/li>\n<li>Decide what is published:<\/li>\n<\/ol>/,
               "2 and 3, split only by a blank line, are one list starting at 2");
  assert.match(html, /<ol start="4">\n<li>Put the head tags in one layout\.<\/li>\n<\/ol>$/);
});

check("a blank line between items does not end the list", () => {
  const ol = renderMarkdown("1. a\n\n1. b\n\n\n1. c\n\nAfter.", "x");
  assert.equal((ol.match(/<ol/g) || []).length, 1, "one list, so the browser counts 1, 2, 3");
  assert.match(ol, /<li>c<\/li>\n<\/ol>\n<p>After\.<\/p>/);
  const ul = renderMarkdown("- a\n\n- b", "x");
  assert.match(ul, /^<ul>\n<li>a<\/li>\n<li>b<\/li>\n<\/ul>$/);
  const mixed = renderMarkdown("- a\n\n1. b", "x");
  assert.match(mixed, /<\/ul>\n<ol>\n<li>b<\/li>/, "a different kind of list still starts its own");
});

check("blocks indented under an item belong to it", () => {
  // Forrest, 2026-10-01 ("fix it?"): the guides' code blocks and sub-bullets
  // should sit under their step, not between the steps.
  const html = renderMarkdown(
    "1. Build the image:\n\n   ```dockerfile\n   FROM base\n     RUN x\n   ```\n\n" +
    "   Pin the base image.\n\n2. Write the stack:\n   • a bucket\n   • a role\n     that wraps\n3. Deploy.", "x");
  assert.equal((html.match(/<ol/g) || []).length, 1, "one list for all three steps");
  assert.match(html, /^<ol>\n<li>Build the image:\n<pre><code>FROM base\n  RUN x<\/code><\/pre>\n<p>Pin the base image\.<\/p><\/li>/,
               "the code keeps its own indent past the item's, and the paragraph after it stays in step 1");
  assert.match(html, /<li>Write the stack:\n<ul>\n<li>a bucket<\/li>\n<li>a role that wraps<\/li>\n<\/ul><\/li>\n<li>Deploy\.<\/li>\n<\/ol>$/);
});

check("a nested list, and an unindented line continuing an item's text", () => {
  const html = renderMarkdown("- top\n  - inner\n    - deepest\n- next\nlazy line\n\nAfter.", "x");
  assert.match(html, /^<ul>\n<li>top\n<ul>\n<li>inner\n<ul>\n<li>deepest<\/li>\n<\/ul><\/li>\n<\/ul><\/li>\n<li>next lazy line<\/li>\n<\/ul>\n<p>After\.<\/p>$/);
});

check("an item ends at a line indented less than its text", () => {
  // Two spaces under "1. " (text at column 3) is not enough to nest, as in CommonMark.
  const html = renderMarkdown("1. step\n  - not nested\n\nOutside.", "x");
  assert.match(html, /<ol>\n<li>step<\/li>\n<\/ol>\n<ul>\n<li>not nested/);
  assert.match(html, /<p>Outside\.<\/p>$/);
});

check("fenced code is not treated as markup", () => {
  const html = renderMarkdown("```\n**not bold** [[not a link]]\n```", "x");
  assert.match(html, /<pre><code>/);
  assert.ok(!html.includes("<strong>"), "fence contents must stay literal");
  assert.ok(!html.includes('class="wl"'), "fence contents must stay literal");
});

check("YAML front matter is stripped", () => {
  const html = renderMarkdown("---\ntitle: x\n---\n\nBody text", "x");
  assert.ok(!html.includes("title: x"));
  assert.match(html, /Body text/);
});

check("html in page content cannot inject", () => {
  const html = renderMarkdown('<img src=x onerror="alert(1)"> <script>bad()</script>', "x");
  assert.ok(!html.includes("<img"), "raw tags must be escaped");
  assert.ok(!html.includes("<script"), "raw tags must be escaped");
  assert.match(html, /&lt;script&gt;/);
});

check("a javascript: url is not turned into a link", () => {
  const html = renderMarkdown("[click](javascript:alert(1))", "x");
  assert.ok(!/href="javascript:/i.test(html), "only http(s) links get an href");
});

check("external links open in a new tab safely", () => {
  const html = renderMarkdown("[docs](https://example.com/a)", "x");
  assert.match(html, /rel="noopener noreferrer"/);
  assert.match(html, /target="_blank"/);
});

check("• bullets render as a list, one item per bullet", () => {
  const html = renderMarkdown(
    "Intro line.\n• first item\n  wraps here\n• second item\n\nAfter.", "x");
  assert.match(html, /<p>Intro line\.<\/p>/);
  assert.match(html, /<ul>\n<li>first item wraps here<\/li>\n<li>second item<\/li>\n<\/ul>/);
  assert.match(html, /<p>After\.<\/p>/);
});

check("HTML comments are hidden, as markdown hides them", () => {
  const html = renderMarkdown(
    "## Entities\n<!-- Alphabetical. Format: • [[entities/page-name]]: summary. -->\n" +
    "• [[index]]: the index\n\nText <!-- inline\nover two lines --> after.", "x");
  assert.ok(!html.includes("&lt;!--") && !html.includes("Alphabetical"), html);
  assert.ok(!html.includes("page-name"), "a link inside a comment is not rendered");
  assert.match(html, /<h3 id="s-entities">Entities<\/h3>\n<ul>\n<li><a href="#" class="wl" data-id="index">/);
  assert.match(html, /<p>Text\s+after\.<\/p>/, "an inline comment leaves one paragraph");
});

check("comments inside code are left alone", () => {
  const html = renderMarkdown("Use `<!-- x -->` here.\n\n```\n<!-- kept -->\n```", "x");
  assert.match(html, /<code>&lt;!-- x --&gt;<\/code>/);
  assert.match(html, /<pre><code>&lt;!-- kept --&gt;<\/code><\/pre>/);
});

check("a pipe table renders as a table, not one run-on paragraph", () => {
  const html = renderMarkdown(
    "**Monthly project breakdown**\n\n" +
    "| Project | Maint $ | Dev $ | Total $ |\n" +
    "|---------|---------|-------|---------|\n" +
    "| Roastery site | $1,200 | $0 | $1,200 |\n" +
    "| **Total** | **$3,400** | **$900** | **$4,300** |\n\n" +
    "Dev hours are fuzzed.", "x");
  assert.ok(!html.includes("|"), "no raw pipes left: " + html);
  assert.match(html, /<p><strong>Monthly project breakdown<\/strong><\/p>/);
  assert.match(html, /<thead><tr><th>Project<\/th><th>Maint \$<\/th><th>Dev \$<\/th><th>Total \$<\/th><\/tr><\/thead>/);
  assert.equal((html.match(/<tr>/g) || []).length, 3);
  assert.match(html, /<td><strong>\$4,300<\/strong><\/td><\/tr><\/tbody><\/table><\/div>/);
  assert.match(html, /<p>Dev hours are fuzzed\.<\/p>/);
});

check("table alignment, missing edge pipes, short and long rows", () => {
  const html = renderMarkdown("a | b | c\n:-- | :-: | --:\n1 | 2\n4 | 5 | 6 | 7", "x");
  assert.match(html, /<th style="text-align:left">a<\/th><th style="text-align:center">b<\/th>/);
  assert.match(html, /<td style="text-align:right"><\/td><\/tr>/, "a short row is padded");
  assert.ok(!html.includes(">7<"), "cells beyond the header are dropped");
});

check("pipes inside code, wikilinks and escapes stay in their cell", () => {
  const html = renderMarkdown(
    "| Page | Note |\n| --- | --- |\n| [[index|Home]] | `a|b` and x \\| y |\n| [[index\\|Start]] | ok |", "x");
  assert.match(html, /<td><a href="#" class="wl" data-id="index">Home<\/a><\/td>/);
  assert.match(html, /<td><code>a\|b<\/code> and x \| y<\/td>/);
  assert.match(html, /<td><a href="#" class="wl" data-id="index">Start<\/a><\/td>/);
});

check("a table directly under a paragraph or list line still starts", () => {
  const p = renderMarkdown("Breakdown:\n| a | b |\n| - | - |\n| 1 | 2 |", "x");
  assert.match(p, /<p>Breakdown:<\/p>\n<div class="table-wrap">/);
  const l = renderMarkdown("- item\n| a | b |\n| - | - |", "x");
  assert.match(l, /<li>item<\/li>\n<\/ul>\n<div class="table-wrap">/);
});

check("pipes without a delimiter row stay prose, and cells cannot inject", () => {
  assert.equal(renderMarkdown("either a | b\nor c", "x"), "<p>either a | b or c</p>");
  assert.ok(!renderMarkdown("a | b\n| --- |", "x").includes("<table>"),
            "a delimiter row with a different cell count is not a table");
  const html = renderMarkdown("| <b>h</b> |\n| --- |\n| <script>x</script> |", "x");
  assert.ok(!html.includes("<script>") && !html.includes("<b>"), html);
});

console.log("\nfolder tree");

const NODES = [
  { id: "index", title: "Index", folder: "", degree: 3 },
  { id: "entities/acme", title: "Acme Corp", folder: "entities", degree: 2 },
  { id: "entities/people/jane", title: "Jane Doe", folder: "entities/people", degree: 1 },
  { id: "notes/lonely", title: "Lonely", folder: "notes", degree: 0 },
  { id: "notes/x", title: '<img src=x onerror="alert(1)">', folder: "notes", degree: 1 },
];
function tree(query = "", expanded = null) {
  state.folders = [...new Set(NODES.map((n) => n.folder))].sort();
  state.query = query;
  state.selected = null;
  const root = buildTree(NODES);
  state.expanded = expanded || defaultExpanded(root, NODES.length);
  return treeHtml(root);
}

check("folders nest by path, with page counts that include subfolders", () => {
  const html = tree();
  assert.match(html, /data-dir="entities"[^>]*>.*?<span class="name">entities<\/span><span class="count">2<\/span>/);
  assert.match(html, /data-dir="entities\/people"[^>]*>.*?<span class="count">1<\/span>/);
  assert.match(html, /data-id="entities\/people\/jane"/);
});

check("pages at the root are listed at the top level", () => {
  assert.match(tree(), /data-id="index"/);
});

check("page titles and paths are escaped", () => {
  const html = tree();
  assert.ok(!html.includes("<img"), "raw HTML from a title must not reach the tree");
  assert.match(html, /&lt;img src=x onerror=&quot;alert\(1\)&quot;&gt;/);
});

check("a collapsed folder hides what is inside it", () => {
  const html = tree("", new Set(["notes"]));
  assert.match(html, /data-dir="entities"[^>]*aria-expanded="false"/);
  assert.ok(!html.includes('data-id="entities/acme"'));
  assert.match(html, /data-id="notes\/lonely"/);
});

check("search filters the tree, opens folders with matches, drops empty ones", () => {
  const html = tree("jane", new Set());
  assert.match(html, /data-id="entities\/people\/jane"/);
  assert.match(html, /data-dir="entities"[^>]*aria-expanded="true"/);
  assert.ok(!html.includes('data-dir="notes"'), "a folder with no matches is hidden");
  assert.ok(!html.includes('data-id="index"'));
});

check("search matches words in any order, drops common words, quotes stay exact", () => {
  for (const q of ["doe jane", "who is Jane Doe?", "people jane"]) {
    assert.match(tree(q, new Set()), /data-id="entities\/people\/jane"/, q);
  }
  assert.ok(!tree('"doe jane"', new Set()).includes('data-id="entities/people/jane"'));
  assert.ok(!tree("jane acme", new Set()).includes('data-id="entities/people/jane"'));
});

console.log("\nsearch results list");

function ranked(query, nodes, serverOrder = null) {
  // the list ranks the whole wiki, not just what the graph shows
  state.all = state.data = { nodes, links: [] };
  state.query = query;
  state.textHits = serverOrder ? new Set(serverOrder) : null;
  state.textRank = serverOrder ? new Map(serverOrder.map((p, i) => [p, i])) : null;
  return rankedMatches().map((n) => n.id);
}
const PAGES = [
  { id: "index", title: "Index", folder: "", degree: 9, text: "see pricing and more" },
  { id: "entities/verdant", title: "Verdant", folder: "entities", degree: 5, text: "pricing at $339" },
  { id: "notes/deal", title: "Deal", folder: "notes", degree: 2, text: "Pricing, pricing, pricing." },
  { id: "notes/pricing-plan", title: "Pricing plan", folder: "notes", degree: 1, text: "" },
  { id: "notes/other", title: "Other", folder: "notes", degree: 4, text: "nothing here" },
];

check("a page with the words in its title or path comes before one with them in its text", () => {
  assert.equal(ranked("pricing", PAGES)[0], "notes/pricing-plan");
});

check("then the page where the words weigh most for its length, then the most linked", () => {
  // deal says pricing three times; verdant and index once each, verdant in fewer words.
  assert.deepEqual(ranked("pricing", PAGES).slice(1), ["notes/deal", "entities/verdant", "index"]);
  const same = PAGES.filter((p) => p.id !== "notes/other").map((p) => ({ ...p, text: p.text && "pricing" }));
  assert.deepEqual(ranked("pricing", same).slice(1), ["index", "entities/verdant", "notes/deal"]);
});

check("the page named for the query comes first, however long the others are", () => {
  const pages = [
    { id: "engineering/acme-deploy", title: "Acme Deploy", folder: "engineering", degree: 19,
      text: "Acme ".repeat(100) + "deploy notes" },
    { id: "entities/acme", title: "Acme", folder: "entities", degree: 4, text: "Acme is a consultancy." },
    { id: "notes/acme-billing", title: "Acme Billing", folder: "notes", degree: 1, text: "Acme bills monthly." },
  ];
  assert.equal(ranked("Acme", pages)[0], "entities/acme");
  assert.equal(ranked("acme billing", pages)[0], "notes/acme-billing");
});

check("once the server has ranked the text search, its order leads, local matches after", () => {
  assert.deepEqual(ranked("pricing", PAGES, ["entities/verdant", "notes/pricing-plan", "index"]),
                   ["entities/verdant", "notes/pricing-plan", "index", "notes/deal"]);
});

check("no query, no list; no match, an empty one", () => {
  assert.deepEqual(ranked("", PAGES), []);
  assert.deepEqual(ranked("zzz-nothing", PAGES), []);
});

console.log("\nfolders in the search list");

// Forrest, 2026-09-28: "we should be able to search for folders", and a folder
// should not sit above the page of the same name.
const pg = (id, title, degree = 1) =>
  ({ id, title, folder: id.includes("/") ? id.slice(0, id.lastIndexOf("/")) : "", degree, text: "" });
const WIKI = [
  pg("entities/northwind", "Northwind", 6), pg("northwind/clients", "Clients"),
  pg("northwind/team", "Team"), pg("northwind/ops/runbook", "Runbook"),
  pg("competitors/zep", "Zep", 3), pg("competitors/mem0", "Mem0", 3),
  pg("comparisons/pricing", "Pricing", 3), pg("notes/plan", "Plan"),
];
function rows(query, nodes = WIKI, focus = null, serverOrder = null) {
  state.all = state.data = { nodes, links: [] };
  state.query = query; state.focus = focus;
  state.textHits = serverOrder ? new Set(serverOrder) : null;
  state.textRank = serverOrder ? new Map(serverOrder.map((p, i) => [p, i])) : null;
  return findRows(rankedMatches(), matchedFolders()).map((x) => (x.dir != null ? x.dir + "/" : x.id));
}

check("the page named for the query comes first, its folder of the same name right after", () => {
  assert.deepEqual(rows("northwind").slice(0, 2), ["entities/northwind", "northwind/"]);
  assert.deepEqual(rows("northwind").slice(2).sort(),
                   ["northwind/clients", "northwind/ops/runbook", "northwind/team"]);
  // so it stays when the server's order puts another page first
  assert.deepEqual(rows("northwind", WIKI, null,
                        ["northwind/team", "entities/northwind", "northwind/clients"]).slice(0, 3),
                   ["northwind/team", "entities/northwind", "northwind/"]);
});

check("folders match on their own name, and come before pages not named for the query", () => {
  // "comp" names neither page; both folders lead, the fuller first
  assert.deepEqual(rows("comp").slice(0, 2), ["competitors/", "comparisons/"]);
  assert.deepEqual(rows("comp").slice(2).sort(),
                   ["comparisons/pricing", "competitors/mem0", "competitors/zep"]);
  const found = (q) => { rows(q); return matchedFolders().map((f) => [f.dir, f.pages]); };
  assert.deepEqual(found("northwind"), [["northwind", 3]]);          // counts pages in subfolders
  assert.deepEqual(found("ops"), [["northwind/ops", 1]]);            // a subfolder by its name
  assert.deepEqual(found("plan"), []);                                 // a page is not a folder
});

check("drilled into a folder, only folders inside it come up", () => {
  rows("ops", WIKI, "northwind");
  assert.deepEqual(matchedFolders().map((f) => f.dir), ["northwind/ops"]);
  rows("ops", WIKI, "competitors");
  assert.deepEqual(matchedFolders(), []);
  rows("northwind", WIKI, "northwind");                               // not the folder itself
  assert.deepEqual(matchedFolders(), []);
});

check("at most three folders, and the list stays eight rows", () => {
  const many = [...Array(10).keys()].map((i) => pg(`notes/foo-${i}`, `Foo ${i}`))
    .concat(["a", "b", "c", "d"].map((k) => pg(`foo-${k}/x`, "X")));
  const got = rows("foo", many);
  assert.equal(got.length, 8);
  assert.deepEqual(got.slice(0, 3).map((r) => r.endsWith("/")), [true, true, true]);
  assert.ok(got.slice(3).every((r) => !r.endsWith("/")));
});
state.all = state.data = { nodes: [], links: [] }; state.query = ""; state.textHits = null; state.textRank = null;
state.focus = null;

check("large wikis start with folders closed", () => {
  const root = buildTree(NODES);
  assert.equal(defaultExpanded(root, 151).size, 0);
  assert.deepEqual([...defaultExpanded(root, 5)].sort(),
                   ["entities", "entities/people", "notes"]);
});


console.log("\nthe search's words in the open page");

// what markTerms would wrap in <mark>, from the same pattern
const hits = (q, text) => { const re = termPattern(q); return re ? [...text.matchAll(re)].map((m) => m[0]) : []; };

check("the search's words are found in any case, anywhere in a word", () => {
  assert.deepEqual(hits("roast", "Roast day: the ROASTER roasts"), ["Roast", "ROAST", "roast"]);
  assert.deepEqual(hits("espresso blend", "Morning Blend is our espresso"), ["Blend", "espresso"]);
});

check("common words are not marked, unless nothing else is left", () => {
  assert.deepEqual(hits("the roast", "the roast of the day"), ["roast"]);
  assert.deepEqual(hits("the", "the roast"), ["the"]);
});

check("a quoted phrase is marked whole, across a line break", () => {
  assert.deepEqual(hits('"price sheet"', "the Price\n  sheet and the price list"), ["Price\n  sheet"]);
});

check("the longer of two overlapping words wins", () => {
  assert.deepEqual(hits("roast roaster", "our roaster"), ["roaster"]);
});

check("characters with a meaning in a pattern are matched as written", () => {
  // (brackets round a word are dropped, as the search drops them)
  assert.deepEqual(hits("c++ (beta)", "c++ and (beta) and cxx"), ["c++", "beta"]);
  assert.deepEqual(hits("$20/mo", "costs $20/mo"), ["$20/mo"]);
});

check("no search, nothing marked", () => {
  assert.equal(termPattern(""), null);
  assert.deepEqual(hits("", "anything"), []);
});

console.log("footnotes and bare addresses");

check("a footnote is a numbered citation, and its note is listed at the end", () => {
  const html = renderMarkdown(
    "Churn fell 4%.[^src] More text.\n\n[^src]: Acme 2026 report, [PDF](https://example.com/r.pdf)\n\nAfter.", "x");
  assert.match(html, /<p>Churn fell 4%\.<sup class="fn-ref" id="fnref-1"><a href="#fn-1" data-fn="fn-1" title="Acme 2026 report, PDF \(https:\/\/example\.com\/r\.pdf\)">1<\/a><\/sup> More text\.<\/p>/);
  assert.ok(!html.includes("[^"), "no raw footnote syntax is left");
  assert.match(html, /<p>After\.<\/p>\n<section class="footnotes" aria-label="Notes"><ol><li id="fn-1">Acme 2026 report, <a href="https:\/\/example\.com\/r\.pdf"/);
  assert.match(html, /<a href="#fnref-1" class="fn-back" data-fn="fnref-1" aria-label="Back to where note 1 is cited">↩<\/a><\/li><\/ol><\/section>$/);
});

check("notes are numbered in the order they are cited, and a note cited twice links back twice", () => {
  const html = renderMarkdown(
    "[^b]: Second written.\n[^a]: First written.\n\nOne[^a], two[^b], again[^A].", "x");
  assert.match(html, /One<sup class="fn-ref" id="fnref-1"><a href="#fn-1"[^>]*>1<\/a><\/sup>/);
  assert.match(html, /two<sup class="fn-ref" id="fnref-2"><a href="#fn-2"[^>]*>2<\/a><\/sup>/);
  assert.match(html, /again<sup class="fn-ref" id="fnref-1-2"><a href="#fn-1"[^>]*>1<\/a><\/sup>/,
    "labels match regardless of case");
  assert.match(html, /<li id="fn-1">First written\. <a href="#fnref-1"[^>]*>↩<\/a> <a href="#fnref-1-2"[^>]*>↩<sup>2<\/sup><\/a><\/li><li id="fn-2">Second written\./);
});

check("a citation of a note the page lacks stays as written; an uncited note is still listed", () => {
  const html = renderMarkdown("Claim[^1] and claim[^nope].\n\n[^1]: Cited.\n[^2]: Never cited.", "x");
  assert.ok(html.includes("claim[^nope]."), html);
  assert.match(html, /<li id="fn-1">Cited\. <a[^>]*fn-back[^>]*>↩<\/a><\/li><li id="fn-2">Never cited\.<\/li>/);
});

check("a note runs over its following lines and indented paragraphs", () => {
  const html = renderMarkdown(
    "Text[^n].\n\n[^n]: First line\ngoes on here.\n\n    A second paragraph.\n\n## Next\n\nBody.", "x");
  assert.match(html, /<li id="fn-1"><p>First line goes on here\.<\/p><p>A second paragraph\. <a href="#fnref-1"[^>]*>↩<\/a><\/p><\/li>/);
  assert.match(html, /<h3 id="s-next">Next<\/h3>\n<p>Body\.<\/p>/, "the page goes on after the note");
});

check("a note straight after a paragraph does not join the text either side", () => {
  const html = renderMarkdown("Before[^1].\n[^1]: The note.\n\nAfter.", "x");
  assert.match(html, /<p>Before<sup[^>]*>.*?<\/sup>\.<\/p>\n<p>After\.<\/p>/);
});

check("footnote syntax in code is left alone", () => {
  const html = renderMarkdown("Use `[^1]` like this.\n\n```\n[^1]: not a note\n```\n\n[^1]: Real.", "x");
  assert.ok(html.includes("<code>[^1]</code>"), html);
  assert.ok(html.includes("[^1]: not a note"), "a fenced block keeps its text");
  assert.match(html, /<li id="fn-1">Real\.<\/li>/, "the real note is listed, uncited");
});

check("markup in a note cannot break its citation's tooltip", () => {
  const html = renderMarkdown("A *word*[^1] and *more*.\n\n[^1]: 5*3 is \"15\" <b>", "x");
  assert.match(html, /title="5\*3 is &quot;15&quot; &lt;b&gt;">1<\/a><\/sup>/);
  assert.ok(!/title="[^"]*<em>/.test(html), html);
});

check("a footnote in a heading leaves the heading's anchor as the server makes it", () => {
  const html = renderMarkdown("## Results[^1]\n\n[^1]: Source.", "x");
  assert.match(html, /<h3 id="s-results-1">Results<sup class="fn-ref"/);
});

check("a bare web address is a link, without the sentence's punctuation", () => {
  const html = renderMarkdown(
    "See https://example.com/a?b=1&c=2. Or (https://en.wikipedia.org/wiki/Foo_(bar)), or <https://example.org/x>.", "x");
  assert.ok(html.includes('See <a href="https://example.com/a?b=1&amp;c=2" target="_blank" rel="noopener noreferrer">https://example.com/a?b=1&amp;c=2</a>. Or'), html);
  assert.ok(html.includes('(<a href="https://en.wikipedia.org/wiki/Foo_(bar)" target="_blank" rel="noopener noreferrer">https://en.wikipedia.org/wiki/Foo_(bar)</a>), or'), html);
  assert.ok(html.includes('or <a href="https://example.org/x" target="_blank" rel="noopener noreferrer">https://example.org/x</a>.'), html);
});

check("an address already in a link, or in code, is not linked again", () => {
  const html = renderMarkdown("[https://example.com](https://example.com) and `https://example.com/code`", "x");
  assert.equal((html.match(/<a /g) || []).length, 1, html);
  assert.ok(html.includes("<code>https://example.com/code</code>"));
});

console.log("what a page says about itself (okf.py)");

const SOURCES = [
  { id: "policy", resource: "https://example.com/policy", title: "Refund policy", author: "team:legal",
    last_modified: "2026-05-30T00:00:00Z" },
  { id: "unused", resource: "people/jane-doe", title: "Call with Jane" },
  { resource: "all queries in project X" },
  { text: "Interview, 2026-10-02, https://example.com/i" },
];

check("a footnote labelled with a source's id cites the source", () => {
  const cited = new Set();
  const html = renderMarkdown("---\nsources: []\n---\nRefunds take 14 days.[^policy] Also[^1].\n\n[^1]: A note.",
    "x", "", SOURCES, cited);
  assert.ok(!html.includes("sources:"), "the frontmatter is not shown");
  assert.match(html, /<sup class="fn-ref" id="fnref-1"><a href="#fn-1" data-fn="fn-1" title="Refund policy \(https:\/\/example\.com\/policy\) · team:legal · updated [^"]+">1<\/a><\/sup>/);
  assert.match(html, /<li id="fn-1"><a href="https:\/\/example\.com\/policy"[^>]*>Refund policy<\/a> · team:legal · updated /);
  assert.ok(html.includes("updated May 30, 2026"), "a date written without a time stays that date in any time zone");
  assert.ok(html.includes('<li id="fn-2">A note.'), html);
  assert.ok(!html.includes("Call with Jane"), "an uncited source is not a note");
  assert.deepEqual([...cited], ["policy"]);
});

check("the page's own note wins over a source with the same id", () => {
  const cited = new Set();
  const html = renderMarkdown("Claim[^policy].\n\n[^policy]: Our own words.", "x", "", SOURCES, cited);
  assert.ok(html.includes('<li id="fn-1">Our own words.'), html);
  assert.equal(cited.size, 0);
});

check("sources no footnote cites are listed under Sources", () => {
  const html = sourcesBlock(SOURCES, new Set(["policy"]), "x");
  assert.ok(html.startsWith('<section class="links backlinks page-sources" aria-label="Sources"><b>Sources</b>'), html);
  assert.ok(!html.includes("Refund policy"), "a cited source is in the notes instead");
  assert.match(html, /<div class="src"><a [^>]*>Call with Jane<\/a><\/div>/);
  assert.ok(html.includes('<div class="src">all queries in project X</div>'), "a scope is text, not a link");
  assert.match(html, /Interview, 2026-10-02, <a href="https:\/\/example\.com\/i"/);
  assert.equal(sourcesBlock(SOURCES.slice(0, 1), new Set(["policy"]), "x"), "");
  assert.equal(sourcesBlock(undefined, new Set(), "x"), "");
});

check("a source's title cannot break out of its link", () => {
  const html = sourcesBlock([{ resource: "https://e.com/a (b)", title: "x] <b>bold</b> [y" }], new Set(), "x");
  assert.ok(html.includes('href="https://e.com/a%20%28b%29"'), html);
  assert.ok(!html.includes("<b>bold"), html);
});

check("the review line says who, when, and whether the page changed since", () => {
  const now = Date.now() / 1000;
  const human = reviewBit({ tier: "human-reviewed", name: "Ann Lee", at: now - 7200, reviewers: ["Ann Lee", "Bo"],
                            edited_since: false });
  assert.match(human, /^<span class="rv rv-human" title="[^"]* · also reviewed by Bo">Reviewed [^<]+ by Ann Lee<\/span>$/);
  const old = reviewBit({ tier: "human-reviewed", name: "Ann Lee", at: now - 7200, reviewers: ["Ann Lee"],
                          edited_since: true });
  assert.ok(old.includes('class="rv rv-human rv-old"') && old.endsWith("by Ann Lee, changed since</span>"), old);
  const agent = reviewBit({ tier: "machine-confirmed", name: "niko", at: now - 60, reviewers: ["niko"] });
  assert.match(agent, />Checked [^<]+ by niko</);
  const guest = reviewBit({ tier: "human-reviewed", at: now - 7200, edited_since: false });
  assert.match(guest, />Reviewed [^<]+<\/span>$/);
  assert.ok(!guest.includes(" by "), guest);
  const evil = reviewBit({ tier: "human-reviewed", name: "<img src=x>", at: now, reviewers: [] });
  assert.ok(!evil.includes("<img"), evil);
});

console.log(failures ? `\n${failures} failing` : "\nall passing");
process.exit(failures ? 1 : 0);
