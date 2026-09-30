/* The graph must open already laid out, not fly apart on screen, and each
 * layout in the menu must place the pages the way it says.
 * Run: node tests/test_layout.mjs
 * graph.js is an IIFE that needs DOM globals, so we stub just enough of one.
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import assert from "node:assert/strict";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "../src/dexio/static/graph.js"), "utf8");

// --- minimal DOM ---------------------------------------------------------
const ctx2d = new Proxy({}, {
  get: (_, k) => (k === "measureText" ? () => ({ width: 40 }) : () => {}),
  set: () => true,
});
const el = () => ({
  getContext: () => ctx2d,
  addEventListener() {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  querySelector: () => null, querySelectorAll: () => [], setAttribute() {},
  style: {}, clientWidth: 1200, clientHeight: 800, dataset: {},
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
globalThis.cancelAnimationFrame = () => {};
// Reduced motion is on unless a test turns it off, so a switch lands at once.
let reduceMotion = true;
globalThis.matchMedia = (q) => ({ matches: q.includes("reduce") ? reduceMotion : false,
                                  addEventListener() {} });
const stored = new Map();
globalThis.localStorage = {
  getItem: (k) => (stored.has(k) ? stored.get(k) : null),
  setItem: (k, v) => stored.set(k, String(v)), removeItem: (k) => stored.delete(k),
};

const wrapped = src.replace(
  "window.dexio = { load, resize, select };",
  "window.dexio = { load, resize, select, state, step, setLayout, tick, finishGlide, layoutMenuHtml, seedForce, stepForce, refilter };");
new Function(wrapped)();
const { load, state, step, setLayout, tick, finishGlide, layoutMenuHtml, seedForce, stepForce, refilter } = globalThis.window.dexio;

// A wiki shaped like the one that exposed this: 189 pages, 913 links, one
// index page linking to most of them, a few secondary hubs, 28 loose pages.
function rng(seed) {
  return () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
function denseWiki(seed) {
  const r = rng(seed), ids = ["index"];
  for (let i = 0; i < 188; i++) ids.push(`f${i % 19}/p${i}`);
  const live = ids.slice(0, 161), edges = new Set(), deg = new Map(ids.map((id) => [id, 0]));
  const add = (a, b) => {
    const k = a + ">" + b;
    if (a === b || edges.has(k)) return;
    edges.add(k); deg.set(a, deg.get(a) + 1); deg.set(b, deg.get(b) + 1);
  };
  for (const id of live.slice(1)) if (r() < 0.93) add("index", id);
  for (const [h, n] of [[1, 62], [2, 60], [3, 39], [4, 35]]) {
    for (let i = 0; i < n; i++) add(live[h], live[1 + Math.floor(r() * 160)]);
  }
  while (edges.size < 913) {
    const a = live[5 + Math.floor(r() * 156)];
    let pick = r() * [...live.slice(5)].reduce((s, id) => s + deg.get(id) + 1, 0), b = live[5];
    for (const id of live.slice(5)) { pick -= deg.get(id) + 1; if (pick <= 0) { b = id; break; } }
    add(a, b);
  }
  const links = [...edges].map((k) => { const [source, target] = k.split(">"); return { source, target }; });
  const nodes = ids.map((id) => ({ id, title: id, folder: id.includes("/") ? id.split("/")[0] : "",
                                   degree: deg.get(id) }));
  return { nodes, links, stats: { pages: nodes.length, links: links.length }, dangling: [] };
}

let failures = 0;
function check(name, fn) {
  try { fn(); console.log(`  ok   ${name}`); }
  catch (e) { failures++; console.log(`  FAIL ${name}\n       ${e.message}`); }
}

console.log("graph layout");

// Before the fix each node moved about 30 layout px per frame over the first
// second on a wiki like this, and 2,000 px on screen before it came to rest.
// A browser that never picked a layout opens By folder (Forrest, 2026-09-27).
check("a first visit opens By folder", () => {
  assert.equal(state.layout, "folders");
});

check("a dense wiki opens laid out: the first second on screen barely moves", () => {
  for (const [layout, seed] of [["folders", 1], ["folders", 2], ["folders", 3], ["force", 1], ["force", 2], ["force", 3]]) {
    setLayout(layout);
    load(denseWiki(seed));
    let moved = 0;
    const n = state.data.nodes.length;
    for (let f = 0; f < 60; f++) {
      const before = state.data.nodes.map((nd) => ({ ...state.pos.get(nd.id) }));
      step();
      state.data.nodes.forEach((nd, i) => {
        const p = state.pos.get(nd.id);
        moved += Math.hypot(p.x - before[i].x, p.y - before[i].y);
      });
    }
    const perFrame = moved / (60 * n);
    assert.ok(perFrame < 1.5, `${layout}, seed ${seed}: ${perFrame.toFixed(2)} px per node per frame`);
  }
  setLayout("folders");
});

check("opening a wiki stays quick", () => {
  for (const layout of ["folders", "force"]) {
    setLayout(layout);
    const t = performance.now();
    load(denseWiki(4));
    assert.ok(performance.now() - t < 400, `${layout}: ${Math.round(performance.now() - t)} ms`);
  }
});

// ---- layouts -------------------------------------------------------------
const menu = [...layoutMenuHtml().matchAll(/data-layout="([a-z]+)"/g)].map((m) => m[1]);
const hyp = (p, q) => Math.hypot(p.x - q.x, p.y - q.y);
const allFinite = () => [...state.pos.values()].every((p) => Number.isFinite(p.x) && Number.isFinite(p.y));
function hops(from) {
  const adj = new Map(state.data.nodes.map((n) => [n.id, []]));
  for (const l of state.data.links) { adj.get(l.source).push(l.target); adj.get(l.target).push(l.source); }
  const d = new Map([[from, 0]]), q = [from];
  for (let i = 0; i < q.length; i++) for (const nb of adj.get(q[i])) if (!d.has(nb)) { d.set(nb, d.get(q[i]) + 1); q.push(nb); }
  return d;
}

check("the menu offers exactly the four layouts, By folder first, the current one ticked", () => {
  assert.deepEqual(menu, ["folders", "force", "radial", "circle"]);
  setLayout("radial");
  const html = layoutMenuHtml();
  assert.match(html, /data-layout="radial" aria-checked="true"/);
  assert.equal((html.match(/aria-checked="true"/g) || []).length, 1, "exactly one layout is ticked");
  for (const name of ["Force", "By folder", "Radial", "Circle"]) assert.ok(html.includes(`>${name}<`), name);
  assert.ok(!/<select/.test(html), "a native select draws its list in the browser's colours");
  setLayout("force");
});

check("every layout places every page somewhere real", () => {
  load(denseWiki(5));
  for (const name of menu) {
    setLayout(name);
    assert.equal(state.layout, name);
    assert.ok(allFinite(), `${name}: a page has no position`);
  }
  setLayout("force");
});

check("radial: the most linked page in the middle, rings further out by links away", () => {
  load(denseWiki(6));
  setLayout("radial");
  const hub = state.data.nodes.reduce((a, b) => (b.degree > a.degree ? b : a));
  assert.equal(state.radialCentre, hub.id);
  const c = state.pos.get(hub.id), d = hops(hub.id), ring = new Map();
  for (const [id, k] of d) {
    const r = hyp(state.pos.get(id), c);
    if (!ring.has(k)) ring.set(k, []);
    ring.get(k).push(r);
  }
  const ks = [...ring.keys()].sort((a, b) => a - b);
  for (let i = 1; i < ks.length; i++) {
    assert.ok(Math.min(...ring.get(ks[i])) > Math.max(...ring.get(ks[i - 1])) + 1,
              `ring ${ks[i]} is not outside ring ${ks[i - 1]}`);
  }
  setLayout("force");
});

check("radial: opening a page moves it to the middle", () => {
  load(denseWiki(6));
  setLayout("radial");
  const leaf = state.data.nodes.find((n) => n.degree === 3) || state.data.nodes[10];
  state.selected = leaf.id; state.radialCentre = null;
  setLayout("force"); setLayout("radial");
  assert.equal(state.radialCentre, leaf.id);
  for (const nb of hops(leaf.id).keys()) {
    if (nb !== leaf.id) assert.ok(hyp(state.pos.get(nb), state.pos.get(leaf.id)) > 50);
  }
  state.selected = null;
  setLayout("force");
});

check("circle: every page the same distance out, no two overlapping, folders together", () => {
  load(denseWiki(7));
  setLayout("circle");
  const c = { x: 600, y: 400 }, rs = [...state.pos.values()].map((p) => hyp(p, c));   // the canvas's centre
  assert.ok(Math.max(...rs) - Math.min(...rs) < 1, "pages are not on one circle");
  const ns = state.data.nodes, rad = (n) => 5 + Math.min(20, Math.sqrt(n.degree || 0) * 4.5);
  for (let i = 0; i < ns.length; i++) for (let j = i + 1; j < ns.length; j++) {
    assert.ok(hyp(state.pos.get(ns[i].id), state.pos.get(ns[j].id)) >= rad(ns[i]) + rad(ns[j]),
              `${ns[i].id} overlaps ${ns[j].id}`);
  }
  const around = ns.map((n) => ({ f: n.folder, a: Math.atan2(state.pos.get(n.id).y - c.y, state.pos.get(n.id).x - c.x) }))
    .sort((a, b) => a.a - b.a).map((x) => x.f);
  const runs = around.filter((f, i) => f !== around[(i + 1) % around.length]).length;
  assert.equal(runs, new Set(around).size, "a folder is split into more than one arc");
  setLayout("force");
});

check("by folder: nearly every page sits nearest its own folder's group", () => {
  for (const seed of [8, 9]) {
    load(denseWiki(seed));
    setLayout("folders");
    const groups = new Map();
    for (const n of state.data.nodes) {
      const g = groups.get(n.folder) || { x: 0, y: 0, n: 0 }, p = state.pos.get(n.id);
      g.x += p.x; g.y += p.y; g.n++; groups.set(n.folder, g);
    }
    let own = 0;
    for (const n of state.data.nodes) {
      const p = state.pos.get(n.id);
      let best = null, bd = Infinity;
      for (const [f, g] of groups) {
        const d = Math.hypot(p.x - g.x / g.n, p.y - g.y / g.n);
        if (d < bd) { bd = d; best = f; }
      }
      if (best === n.folder) own++;
    }
    const share = own / state.data.nodes.length;
    assert.ok(share >= 0.9, `seed ${seed}: ${(share * 100).toFixed(0)}% of pages nearest their folder`);
  }
  setLayout("force");
});

check("radial and circle hold still: a dropped page stays where it was dropped", () => {
  load(denseWiki(10));
  for (const name of ["radial", "circle"]) {
    setLayout(name);
    const id = state.data.nodes[3].id, p = state.pos.get(id);
    p.x += 180; p.y -= 90;
    const at = { x: p.x, y: p.y }, others = state.data.nodes[4].id, o = { ...state.pos.get(others) };
    for (let i = 0; i < 30; i++) tick();
    assert.deepEqual({ x: p.x, y: p.y }, at, `${name}: the dropped page moved`);
    assert.deepEqual({ ...state.pos.get(others) }, o, `${name}: another page moved`);
  }
  setLayout("force");
});

check("a switch glides from where the pages were to where they go", () => {
  load(denseWiki(11));
  reduceMotion = false;
  const before = new Map([...state.pos].map(([id, p]) => [id, { x: p.x, y: p.y }]));
  setLayout("circle");
  assert.ok(state.glide, "no glide started");
  const near = (p, q) => Math.abs(p.x - q.x) < 1e-6 && Math.abs(p.y - q.y) < 1e-6;
  for (const [id, p] of state.pos) assert.ok(near(p, before.get(id)), `${id} jumped instead of gliding`);
  const to = state.glide.to;
  tick(state.glide.t0 + 10000);            // past the end of the glide
  assert.equal(state.glide, null);
  for (const [id, p] of state.pos) assert.deepEqual({ x: p.x, y: p.y }, to.get(id));
  reduceMotion = true;
  setLayout("force");
});

check("the choice is remembered per browser, and restored on the next visit", () => {
  setLayout("radial", true);
  assert.equal(stored.get("dexio-layout"), "radial");
  new Function(wrapped)();
  assert.equal(globalThis.window.dexio.state.layout, "radial");
  stored.set("dexio-layout", "nonsense");
  new Function(wrapped)();
  assert.equal(globalThis.window.dexio.state.layout, "folders");
});

// Force is Obsidian's layout: d3-force, set up the way Obsidian sets it up.
// tests/d3_reference.json is a run of d3-force 3.0.0 itself (made by
// tests/d3_reference.mjs); from the same start, graph.js must land on the
// same positions.
check("force lands where d3-force itself does, set up as Obsidian sets it up", () => {
  const ref = JSON.parse(readFileSync(join(here, "d3_reference.json"), "utf8"));
  const nodes = ref.ids.map((id) => ({ id, title: id, folder: id.includes("/") ? id.split("/")[0] : "", degree: 0 }));
  setLayout("force");
  load({ nodes, links: ref.links.map(([source, target]) => ({ source, target })), stats: {}, dangling: [] });
  seedForce();
  ref.ids.forEach((id, i) => {
    const p = state.pos.get(id), v = state.vel.get(id);
    p.x = ref.start[i][0]; p.y = ref.start[i][1]; v.x = 0; v.y = 0;
  });
  state.alpha = 1;
  for (let i = 0; i < ref.steps; i++) stepForce();
  let worst = 0;
  ref.ids.forEach((id, i) => {
    const p = state.pos.get(id);
    worst = Math.max(worst, Math.hypot(p.x - ref.end[i][0], p.y - ref.end[i][1]));
  });
  assert.ok(worst < 1e-6, `${worst.toExponential(2)} px from d3's positions`);
  assert.ok(Math.abs(state.alpha - 0.001) < 1e-9, `cooled to ${state.alpha}, d3 to 0.001`);
});

check("force: a dragged page is pinned and the rest keeps moving", () => {
  load(denseWiki(12));
  const id = state.data.nodes[7].id, p = state.pos.get(id);
  state.drag = { id, moved: true };
  p.x += 300; p.y += 200;
  const at = { x: p.x, y: p.y }, other = state.pos.get(state.data.nodes[8].id), was = { ...other };
  for (let i = 0; i < 20; i++) step();
  assert.deepEqual({ x: p.x, y: p.y }, at);
  assert.ok(Math.hypot(other.x - was.x, other.y - was.y) > 0.01, "the layout did not react");
  assert.ok(state.alpha > 0.1, "dragging keeps the layout warm");
  state.drag = null;
});

// ---- search ----------------------------------------------------------------
// A search hides the pages it leaves out and lays out the rest afresh, as if
// they were the whole wiki (Forrest, 2026-09-28: it used to grey them out
// where they stood).
function search(q) { state.query = q; refilter(); }

check("a search hides the pages it leaves out, and the links to them", () => {
  load(denseWiki(13));
  const all = state.data.nodes.length;
  search("f3/");
  const ids = new Set(state.data.nodes.map((n) => n.id));
  assert.ok(ids.size > 3 && ids.size < 20, `${ids.size} pages shown`);
  for (const id of ids) assert.ok(id.startsWith("f3/"), `${id} is shown`);
  assert.ok(state.data.links.length > 0, "the links between the pages shown went too");
  for (const l of state.data.links) assert.ok(ids.has(l.source) && ids.has(l.target), "a link to a hidden page is shown");
  search("");
  assert.equal(state.data.nodes.length, all, "clearing the search did not bring every page back");
});

check("every layout lays out only what the search shows, afresh", () => {
  load(denseWiki(14));
  for (const name of menu) {
    setLayout(name);
    search("");
    const whole = new Map([...state.pos].map(([id, p]) => [id, { x: p.x, y: p.y }]));
    search("f5/");
    const shown = state.data.nodes;
    assert.ok(shown.length > 3, `${name}: ${shown.length} pages shown`);
    assert.ok(shown.every((n) => Number.isFinite(state.pos.get(n.id).x) && Number.isFinite(state.pos.get(n.id).y)),
              `${name}: a page shown has no position`);
    const moved = shown.filter((n) => hyp(state.pos.get(n.id), whole.get(n.id)) > 1).length;
    assert.ok(moved > shown.length / 2, `${name}: only ${moved} of ${shown.length} pages moved, so it was not laid out again`);
    // laid out on their own they sit closer together than across the whole wiki
    const spread = (pos) => Math.max(...shown.map((a) => Math.max(...shown.map((b) => hyp(pos(a), pos(b))))));
    assert.ok(spread((n) => state.pos.get(n.id)) < spread((n) => whole.get(n.id)),
              `${name}: the pages shown are no closer together than in the whole wiki`);
  }
  search("");
  setLayout("force");
});

check("radial centres on the most linked page the search shows", () => {
  load(denseWiki(15));
  setLayout("radial");
  search("f7/");
  // most linked among the pages shown, since 2026-09-28 (a page's size
  // follows what the search leaves, graph.js filterShown)
  const d = (n) => state.viewDeg.get(n.id);
  const hub = state.data.nodes.reduce((a, b) => (d(b) > d(a) ? b : a));
  assert.equal(state.radialCentre, hub.id);
  search("");
  setLayout("force");
});

check("by folder gives no spot to a folder the search hides", () => {
  load(denseWiki(16));
  setLayout("folders");
  search("f2/");
  assert.deepEqual([...state.anchors.keys()], ["f2"]);
  search("");
  setLayout("force");
});

check("a search that matches nothing shows nothing and settles", () => {
  load(denseWiki(17));
  for (const name of menu) {
    setLayout(name);
    search("nothing-matches-this");
    assert.equal(state.data.nodes.length, 0);
    let frames = 0;
    globalThis.requestAnimationFrame = () => ++frames;
    tick();
    globalThis.requestAnimationFrame = () => 0;
    assert.equal(frames, 0, `${name}: kept asking for frames with nothing to draw`);
    search("");
    assert.ok(allFinite(), `${name}: a page came back with no position`);
  }
  setLayout("force");
});

check("the same pages shown again leave the layout where it is", () => {
  load(denseWiki(18));
  setLayout("circle");
  search("f4/");
  const at = new Map(state.data.nodes.map((n) => [n.id, { ...state.pos.get(n.id) }]));
  search("f4/p");            // the same pages
  for (const n of state.data.nodes) assert.deepEqual({ ...state.pos.get(n.id) }, at.get(n.id));
  search("");
  setLayout("force");
});

console.log(failures ? `\n${failures} failing` : "\nall passing");
process.exit(failures ? 1 : 0);
