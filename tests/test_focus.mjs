/* Narrowing the graph to one folder (graph.js, "one folder"; Forrest,
 * 2026-09-28, option A of four). Run: node tests/test_focus.mjs
 * graph.js is an IIFE that needs DOM globals, so we stub just enough of one,
 * as test_layout.mjs does.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "../src/dexio/static/graph.js"), "utf8");

const ctx2d = new Proxy({}, {
  get: (_, k) => (k === "measureText" ? () => ({ width: 40 }) : () => {}),
  set: () => true,
});
const el = () => ({
  getContext: () => ctx2d,
  addEventListener() {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], setAttribute() {}, appendChild() {},
  style: {}, clientWidth: 1200, clientHeight: 800, dataset: {},
  getBoundingClientRect: () => ({ left: 0, top: 0 }),
  innerHTML: "", textContent: "", hidden: false, value: "", blur() {}, focus() {},
});
globalThis.document = {
  getElementById: el, documentElement: { dataset: { theme: "light" } },
  querySelector: el, createElement: el,
};
globalThis.window = { addEventListener() {}, devicePixelRatio: 1 };
globalThis.getComputedStyle = () => ({ getPropertyValue: () => "" });
globalThis.requestAnimationFrame = () => 0;
globalThis.cancelAnimationFrame = () => {};
globalThis.matchMedia = (q) => ({ matches: q.includes("reduce"), addEventListener() {} });
const stored = new Map();
globalThis.localStorage = {
  getItem: (k) => (stored.has(k) ? stored.get(k) : null),
  setItem: (k, v) => stored.set(k, String(v)), removeItem: (k) => stored.delete(k),
};

const wrapped = src.replace(
  "window.dexio = { load, resize, select };",
  "window.dexio = { load, resize, select, state, refilter, setFocus, rankedMatches, treeList, linksOut, " +
  "openFound, matchedFolders, nodeColor, folderColor };");
new Function(wrapped)();
const { load, state, refilter, setFocus, treeList, linksOut, nodeColor, folderColor } = globalThis.window.dexio;

const page = (id) => ({ id, title: id.split("/").pop(), folder: id.includes("/") ? id.slice(0, id.lastIndexOf("/")) : "",
                        words: 10, degree: 1, text: "about " + id });
const wiki = () => ({
  nodes: ["index", "notes/plan", "notes/deep/more", "notes/deep/other", "notesx/odd",
          "ops/roast", "ops/cups", "finance/margins"].map(page),
  links: [
    { source: "index", target: "notes/plan" },
    { source: "notes/plan", target: "notes/deep/more" },
    { source: "notes/plan", target: "ops/roast" },       // out of the folder
    { source: "ops/cups", target: "notes/deep/other" },  // into it
    { source: "ops/roast", target: "ops/cups" },         // between two outside pages
    { source: "finance/margins", target: "ops/roast" },  // nothing to do with it
  ],
  stats: {}, dangling: [],
});
const shown = () => state.data.nodes.map((n) => n.id).sort();
const ok = (name, fn) => {
  try { fn(); console.log("ok  ", name); }
  catch (e) { console.log("FAIL", name, "\n    ", e.message); process.exitCode = 1; }
};

ok("the whole wiki shows until a folder is picked", () => {
  load(wiki());
  assert.equal(state.focus, null);
  assert.equal(shown().length, 8);
});

ok("drilling in shows only the folder's pages and its subfolders', by default", () => {
  load(wiki());
  assert.equal(state.linked, false);
  setFocus("notes");
  assert.deepEqual(shown(), ["notes/deep/more", "notes/deep/other", "notes/plan"]);
  assert.equal(state.fringe.size, 0);
});

ok("With linked pages: the pages tied to it too, faded", () => {
  load(wiki());
  state.linked = true;
  setFocus("notes");
  assert.equal(state.focus, "notes");
  assert.deepEqual(shown(), ["index", "notes/deep/more", "notes/deep/other", "notes/plan",
                             "ops/cups", "ops/roast"]);
  assert.deepEqual([...state.fringe].sort(), ["index", "ops/cups", "ops/roast"]);
  // notesx/odd is not in notes, and finance/margins links to none of it
  assert.ok(!shown().includes("notesx/odd") && !shown().includes("finance/margins"));
  // the link between two faded pages is not drawn
  assert.ok(!state.data.links.some((l) => l.source === "ops/roast" && l.target === "ops/cups"));
  assert.ok(state.data.links.some((l) => l.source === "notes/plan" && l.target === "ops/roast"));
  state.linked = false;
});

ok("the tree lists only what is inside, under a row back up", () => {
  load(wiki());
  setFocus("notes/deep");
  const html = treeList.innerHTML;
  assert.ok(html.includes('data-up="notes"'), "a row up to the parent");
  assert.ok(html.includes('data-id="notes/deep/more"') && html.includes('data-id="notes/deep/other"'));
  assert.ok(!html.includes('data-id="notes/plan"') && !html.includes('data-dir="ops"'));
  setFocus("notes");
  assert.ok(treeList.innerHTML.includes('data-up=""'), "a row up to every folder");
  setFocus(null);
  assert.ok(!treeList.innerHTML.includes("data-up") && treeList.innerHTML.includes('data-dir="ops"'));
});

ok("a subfolder narrows further, and clearing brings everything back", () => {
  load(wiki());
  state.linked = true;
  setFocus("notes/deep");
  assert.deepEqual(shown(), ["notes/deep/more", "notes/deep/other", "notes/plan", "ops/cups"]);
  assert.deepEqual([...state.fringe].sort(), ["notes/plan", "ops/cups"]);
  setFocus(null);
  assert.equal(state.focus, null);
  assert.equal(shown().length, 8);
  assert.equal(state.fringe.size, 0);
  state.linked = false;
});

ok("a folder with no pages, from an old link, is no folder", () => {
  load(wiki());
  setFocus("gone");
  assert.equal(state.focus, null);
  assert.equal(shown().length, 8);
  load(wiki(), { focus: "gone" });
  assert.equal(state.focus, null);
});

ok("an address's folder is there from the first layout", () => {
  load(wiki(), { focus: "ops/" });
  assert.equal(state.focus, "ops");
  assert.ok(state.expanded.has("ops"));
  assert.deepEqual(shown().filter((id) => !state.fringe.has(id)), ["ops/cups", "ops/roast"]);
});

ok("a search inside the folder searches only it", () => {
  load(wiki());
  setFocus("ops");
  state.query = "about";
  refilter();
  const hits = window.dexio.rankedMatches().map((n) => n.id).sort();
  assert.deepEqual(hits, ["ops/cups", "ops/roast"]);
  state.query = "";
  refilter();
});

ok("a folder picked from the search list is drilled into, and the search cleared", () => {
  // Forrest, 2026-09-28: "we should be able to search for folders"
  load(wiki());
  state.query = "deep";
  refilter();
  const [dir] = window.dexio.matchedFolders();
  assert.equal(dir.dir, "notes/deep");
  window.dexio.openFound(dir);
  assert.equal(state.focus, "notes/deep");
  assert.equal(state.query, "");
  assert.deepEqual(shown(), ["notes/deep/more", "notes/deep/other"]);
  setFocus(null);
});

ok("Show is offered only when linked pages would add something", () => {
  load(wiki());
  assert.equal(linksOut("notes"), true);          // notes/plan -> ops/roast, ops/cups -> notes/deep/other
  assert.equal(linksOut("finance"), true);        // finance/margins -> ops/roast
  assert.equal(linksOut("notesx"), false);        // nothing links in or out
  // a guest a single folder is shared with has only that folder's pages
  const w = wiki();
  w.nodes = w.nodes.filter((n) => n.id.startsWith("notes/"));
  w.links = w.links.filter((l) => l.source.startsWith("notes/") && l.target.startsWith("notes/"));
  load(w);
  assert.equal(linksOut("notes"), false);
  assert.equal(linksOut("notes/deep"), true);     // notes/plan -> notes/deep/more is outside notes/deep
});

ok("a narrowed graph sizes pages by what it shows", () => {
  load(wiki());
  assert.equal(state.viewDeg, null);              // the whole wiki: the server's degrees
  state.linked = false;
  setFocus("notes");
  // notes/plan: links to notes/deep/more inside; its links to index and ops/roast are gone
  assert.equal(state.viewDeg.get("notes/plan"), 1);
  assert.equal(state.viewDeg.get("notes/deep/other"), 0);   // only ops/cups linked to it
  // With linked pages the links to the pages elsewhere count again
  state.linked = true;
  refilter();
  assert.equal(state.viewDeg.get("notes/plan"), 3);
  assert.equal(state.viewDeg.get("notes/deep/other"), 1);
  state.linked = false;
  setFocus(null);
  assert.equal(state.viewDeg, null);
});

// Forrest, 2026-09-29: "i don't like how the color of the nodes change when
// drilling into a folder".
ok("but never recolours them: drilling in or searching keeps every page's colour", () => {
  const w = wiki();
  w.nodes.push({ ...page("notes/lone"), degree: 0 });    // an orphan in the whole wiki
  load(w);
  const before = new Map(state.all.nodes.map((n) => [n.id, nodeColor(n)]));
  assert.equal(before.get("notes/lone"), nodeColor({ degree: 0, folder: "notes" }));
  assert.notEqual(before.get("notes/plan"), before.get("notes/lone"));
  const same = () => { for (const n of state.data.nodes) assert.equal(nodeColor(n), before.get(n.id), n.id); };
  state.linked = false;
  setFocus("notes");
  assert.equal(state.viewDeg.get("notes/deep/other"), 0);  // no links left, and still not grey
  same();
  setFocus("notes/deep");
  same();
  state.linked = true;
  setFocus("ops");
  same();
  state.linked = false;
  setFocus(null);
  state.query = "ops/";
  refilter();
  same();
  assert.equal(folderColor("ops"), before.get("ops/roast"));
  state.query = "";
  refilter();
  same();
});

ok("so does a search", () => {
  load(wiki());
  state.query = "ops/";
  refilter();
  assert.deepEqual(shown(), ["ops/cups", "ops/roast"]);
  assert.equal(state.viewDeg.get("ops/roast"), 1);         // only ops/roast -> ops/cups is left
  state.query = "";
  refilter();
  assert.equal(state.viewDeg, null);
});
