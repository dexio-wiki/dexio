/* The stats at the graph's top right (graph.js, "stats"; Forrest, 2026-11-07):
 * the figures for the whole wiki or the folder drilled into, and lighting a
 * writer's pages. Run: node tests/test_stats.mjs
 * graph.js is an IIFE that needs DOM globals, so we stub just enough of one,
 * as test_focus.mjs does. The button and card need a real DOM and are checked
 * in a browser; here buildStats finds none and draws nothing.
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
  "window.dexio = { load, resize, select, state, setFocus, statsModel, lightWriter, lightChanged, " +
  "setLit, matches, statsAgo };");
new Function(wrapped)();
const { load, state, setFocus, statsModel, lightWriter, lightChanged, setLit, matches, statsAgo } =
  globalThis.window.dexio;

const NOW = 2_000_000_000;
const page = (id, words = 10) => ({ id, title: id.split("/").pop(), words, degree: 1,
  folder: id.includes("/") ? id.slice(0, id.lastIndexOf("/")) : "" });
const wiki = (activity) => ({
  nodes: [page("index", 5), page("notes/plan", 100), page("notes/deep/more"), page("ops/roast", 1000)],
  links: [
    { source: "index", target: "notes/plan" },
    { source: "notes/plan", target: "notes/deep/more" },
    { source: "notes/plan", target: "ops/roast" },
  ],
  stats: {}, dangling: [], activity,
});
const activity = () => ({
  now: NOW, days: 7, quiet_days: 30,
  changes: [
    { who: "iris", agent: true, path: "notes/plan", n: 3, last: NOW - 3000 },
    { who: "iris", agent: true, path: "notes/deep/more", n: 1, last: NOW - 9000 },
    { who: "niko", agent: true, path: "ops/roast", n: 5, last: NOW - 600 },
    { who: "niko", agent: true, path: "ops/gone", n: 1, last: NOW - 700 },    // deleted since
  ],
  writers: [
    { who: "niko", agent: true, last: NOW - 600 },
    { who: "iris", agent: true, last: NOW - 3000 },
    { who: "enzo", agent: true, last: NOW - 10 * 86400 },
  ],
  latest: { who: "niko", path: "ops/roast", at: NOW - 600 },
});
const ok = (name, fn) => {
  try { fn(); console.log("ok  ", name); }
  catch (e) { console.log("FAIL", name, "\n    ", e.message); process.exitCode = 1; }
};

ok("no activity (a guest, an export, the demo) means no stats", () => {
  load(wiki(undefined));
  assert.equal(statsModel(), null);
});

ok("the whole wiki: writers busiest first, the quiet one last", () => {
  load(wiki(activity()));
  const m = statsModel();
  // the wiki's totals moved to the account menu (render.py, acctStats)
  assert.equal(m.pages, undefined);
  assert.equal(m.total, 10);
  assert.equal(m.active, 2);
  assert.equal(m.agents, true);
  assert.deepEqual(m.rows.map((r) => [r.who, r.n]), [["niko", 6], ["iris", 4], ["enzo", 0]]);
  // a page deleted since counts as a change but lights nothing
  assert.deepEqual([...m.changed].sort(), ["notes/deep/more", "notes/plan", "ops/roast"]);
  assert.deepEqual(m.latest, { who: "niko", path: "ops/roast", at: NOW - 600 });
});

ok("a writer with no agent name makes them writers, not agents", () => {
  const a = activity();
  a.changes.push({ who: "Forrest Zhang", agent: false, path: "index", n: 1, last: NOW - 60 });
  load(wiki(a));
  assert.equal(statsModel().agents, false);
});

ok("drilled into a folder, everything counts that folder and the quiet go", () => {
  load(wiki(activity()));
  setFocus("notes");
  const m = statsModel();
  assert.deepEqual(m.rows.map((r) => [r.who, r.n]), [["iris", 4]]);
  assert.equal(m.latest.path, "notes/plan");
  setFocus(null);
});

ok("lighting a writer dims every other page, and again clears it", () => {
  load(wiki(activity()));
  lightWriter("iris");
  assert.deepEqual([...state.lit.ids].sort(), ["notes/deep/more", "notes/plan"]);
  const byId = (id) => state.all.nodes.find((n) => n.id === id);
  assert.equal(matches(byId("notes/plan")), true);
  assert.equal(matches(byId("ops/roast")), false);
  lightWriter("iris");
  assert.equal(state.lit, null);
  assert.equal(matches(byId("ops/roast")), true);
});

ok("Show lights every page changed; drilling in or another wiki clears it", () => {
  load(wiki(activity()));
  lightChanged();
  assert.equal(state.lit.ids.size, 3);
  setFocus("notes");
  assert.equal(state.lit, null);
  setFocus(null);
  lightWriter("niko");
  assert.ok(state.lit);
  setFocus(null);                    // the same folder again (Back): stays lit
  assert.ok(state.lit);
  load(wiki(activity()));
  assert.equal(state.lit, null);
});

ok("nothing to light is no light", () => {
  load(wiki(activity()));
  setLit({ key: "x", ids: new Set(), label: "" });
  assert.equal(state.lit, null);
});

ok("how long ago reads plainly", () => {
  load(wiki(activity()));
  assert.equal(statsAgo(NOW), "just now");
  assert.equal(statsAgo(NOW - 29 * 60), "29 min ago");
  assert.equal(statsAgo(NOW - 3600), "1 hour ago");
  assert.equal(statsAgo(NOW - 5 * 3600), "5 hours ago");
  assert.equal(statsAgo(NOW - 86400), "1 day ago");
  assert.equal(statsAgo(NOW - 10 * 86400), "10 days ago");
});
