/* dexio graph view. No build step, no dependencies, no CDN. */
(function () {
  "use strict";

  // Two palettes, by mode: the light one is too dark to read against #0d1117,
  // and the dark one washes out on white. Picked for roughly equal luminance
  // within each so no folder silently dominates. Colour themes change the
  // accent and header, not the folder colours.
  const PALETTES = {
    light: ["#0077c8", "#c0392b", "#27ae60", "#8e44ad", "#d35400",
            "#16a085", "#7f8c8d", "#b7950b", "#2c3e50", "#a93226"],
    dark:  ["#38bdf8", "#ff7b72", "#3fb950", "#bc8cff", "#ffa657",
            "#39c5cf", "#8b949e", "#e3b341", "#79c0ff", "#f85149"],
  };

  // Everything else comes from the stylesheet, so the canvas and the DOM can
  // never drift apart.
  let THEME = { pal: PALETTES.light, edge: "#d3d8dd", edgeLit: "#3b78c3",
                dim: "#aab1b8", ring: "#111", text: "#1b1f23", muted: "#57606a", bg: "#fff" };

  function readTheme() {
    const cs = getComputedStyle(document.documentElement);
    const v = (name, fallback) => (cs.getPropertyValue(name).trim() || fallback);
    const dark = document.documentElement.dataset.mode === "dark";
    THEME = {
      pal: dark ? PALETTES.dark : PALETTES.light,
      edge: v("--edge", "#d3d8dd"),
      edgeLit: v("--edge-lit", "#3b78c3"),
      dim: v("--node-dim", "#aab1b8"),
      ring: v("--node-ring", "#111"),
      text: v("--text", "#1b1f23"),
      muted: v("--muted", "#57606a"),
      bg: v("--bg", "#ffffff"),
    };
  }
  const state = {
    // The whole wiki is `all`; `data` is the part on screen, which is all of
    // it until a search hides the pages it leaves out (see filterShown).
    all: { nodes: [], links: [], stats: {}, dangling: [] },
    data: { nodes: [], links: [], stats: {}, dangling: [] },
    shownIds: new Set(), shownKey: null,
    pos: new Map(), vel: new Map(), byId: new Map(),
    view: { x: 0, y: 0, k: 1 },
    selected: null, hover: null, query: "", alpha: 1,
    drag: null, pan: null, raf: null,
    // Until someone pans or zooms, the view keeps the whole graph in frame.
    userView: false,
    // Which layout places the pages (see "layouts" below), the switch in
    // progress, and the page Radial is centred on.
    layout: "folders", glide: null, radialCentre: null,
    adj: new Map(), anchors: new Map(),
    // The folder the graph is narrowed to (see "one folder" below), and the
    // pages outside it that its pages link to or from, drawn faded.
    focus: null, fringe: new Set(), linked: true,
  };

  const canvas = document.getElementById("graph");
  const ctx = canvas.getContext("2d");
  const panel = document.getElementById("panel");
  const searchEl = document.getElementById("search");
  const treeEl = document.getElementById("tree");
  const treeList = document.getElementById("tree-list");
  const treeOpenBtn = document.getElementById("tree-open");
  const treeCloseBtn = document.getElementById("tree-close");

  // A folder's colour comes from the whole wiki's folders, in order, and stays
  // put when the graph is narrowed by a folder or a search (Forrest,
  // 2026-09-29: "i don't like how the color of the nodes change when drilling
  // into a folder"; from 2026-09-28 until then the palette was dealt out
  // again over the folders shown).
  function folderColor(folder) {
    const folders = state.folders || [];
    const i = folders.indexOf(folder || "");
    return THEME.pal[(i < 0 ? 0 : i) % THEME.pal.length];
  }

  // A page's links among the pages shown (its whole-wiki degree when all are).
  function deg(n) {
    const d = state.viewDeg && state.viewDeg.get(n.id);
    return d == null ? (n.degree || 0) : d;
  }

  // An orphan (no links anywhere in the wiki) is grey; every other page takes
  // its folder's colour. Whole-wiki links, not the links shown, so a page
  // keeps its colour when drilling in or searching leaves it unlinked (it did
  // turn grey from 2026-09-28 to 2026-09-29). Its size does follow the links
  // shown (deg).
  function nodeColor(n) { return (n.degree || 0) === 0 ? THEME.dim : folderColor(n.folder); }

  function resize() {
    const dpr = window.devicePixelRatio || 1;
    canvas.width = canvas.clientWidth * dpr;
    canvas.height = canvas.clientHeight * dpr;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    // until someone has panned or zoomed, keep the whole graph in frame
    state.fitLeft = coveredLeft();
    fitView(true);
    draw();
  }

  // Each page's neighbours, either direction, sorted by folder then title so
  // that pages which share a folder sit together in Radial.
  function buildAdjacency(data) {
    const adj = new Map(data.nodes.map((n) => [n.id, new Set()]));
    for (const l of data.links) {
      if (l.source === l.target || !adj.has(l.source) || !adj.has(l.target)) continue;
      adj.get(l.source).add(l.target); adj.get(l.target).add(l.source);
    }
    const out = new Map();
    for (const [id, s] of adj) out.set(id, [...s].sort((a, b) => byFolderThenTitle(state.byId.get(a), state.byId.get(b))));
    return out;
  }

  // opts.focus: a folder to open narrowed to (an address's ?folder=), so the
  // first layout is already the folder's rather than gliding there from the
  // whole wiki.
  function load(data, opts) {
    trail.length = 0;               // the static viewer's back line: another wiki
    state.all = data;
    state.data = data;
    state.files = new Set((data.files || []).map((f) => f.path));
    if (state.query) textSearch();   // text hits were for the wiki shown before
    state.byId = new Map(data.nodes.map((n) => [n.id, n]));
    state.folders = [...new Set(data.nodes.map((n) => n.folder || ""))].sort();
    const w = canvas.clientWidth || 900, h = canvas.clientHeight || 600;
    state.pos = new Map();
    state.vel = new Map();
    data.nodes.forEach((n, i) => {
      const a = (i / Math.max(1, data.nodes.length)) * Math.PI * 2;
      const r = Math.min(w, h) * 0.3 * (0.4 + ((i * 37) % 100) / 160);
      state.pos.set(n.id, { x: w / 2 + Math.cos(a) * r, y: h / 2 + Math.sin(a) * r });
      state.vel.set(n.id, { x: 0, y: 0 });
    });
    state.alpha = 1;
    state.view = { x: 0, y: 0, k: 1 };
    state.userView = false;
    state.glide = null;
    clearTimeout(filterTimer);
    const want = opts && opts.focus ? String(opts.focus).replace(/^\/+|\/+$/g, "") : null;
    state.focus = want && data.nodes.some((n) => inFolder(n, want)) ? want : null;
    filterShown();
    state.fitLeft = coveredLeft();
    state.fitRight = coveredRight();
    state.tree = buildTree(data.nodes);
    state.expanded = defaultExpanded(state.tree, data.nodes.length);
    if (state.focus) {
      let p = "";
      for (const part of state.focus.split("/")) { p = p ? p + "/" + part : part; state.expanded.add(p); }
    }
    state.hoverFolder = null;
    renderTree();
    renderChip();
    arrange();
    tick();
  }

  // Searching hides the pages it leaves out and lays out the rest afresh, as
  // if they were the whole wiki, in whichever layout is picked (Forrest,
  // 2026-09-28: until then it greyed them out where they stood). Only links
  // between two pages still shown are kept. Every layout, the fit, the
  // drawing and finding the page under the pointer read state.data, so they
  // all see just these; the folder tree, the results list and the page panel
  // read the whole wiki. What is shown also decides a page's size (Forrest,
  // 2026-09-28: drilled into a folder, "the nodes should resize and recolor
  // depending on what links remain and whether links are displayed", then
  // "same for when there's a search query filter"): its links among the pages
  // shown (viewDeg). Its colour does not: the recolouring went on 2026-09-29
  // ("i don't like how the color of the nodes change when drilling into a
  // folder"), so colours stay with the whole wiki (folderColor, nodeColor).
  // Returns whether the pages shown changed.
  function filterShown() {
    const all = state.all;
    state.fringe = new Set();
    if (!state.query && !state.focus) state.data = all;
    else {
      const nodes = all.nodes.filter((n) => inFocus(n) && matchesQuery(n));
      const ids = new Set(nodes.map((n) => n.id));
      // One folder: the pages elsewhere that link to it or that it links to
      // stay, faded, so the folder's ties to the rest still show. Off with the
      // chip's Linked pages.
      if (state.focus && state.linked) {
        for (const l of all.links) {
          if (ids.has(l.source) && !ids.has(l.target)) state.fringe.add(l.target);
          if (ids.has(l.target) && !ids.has(l.source)) state.fringe.add(l.source);
        }
        for (const n of all.nodes) {
          if (!state.fringe.has(n.id)) continue;
          if (matchesQuery(n)) { nodes.push(n); ids.add(n.id); }
          else state.fringe.delete(n.id);
        }
      }
      // Links between two faded pages are the rest of the wiki's business.
      const fr = state.fringe;
      state.data = { ...all, nodes, links: all.links.filter((l) => ids.has(l.source) &&
        ids.has(l.target) && !(fr.has(l.source) && fr.has(l.target))) };
    }
    if (state.data === all) state.viewDeg = null;
    else {
      // Counted as the server counts a page's degree: each link, both ends.
      const d = new Map(state.data.nodes.map((n) => [n.id, 0]));
      for (const l of state.data.links) {
        if (l.source === l.target) continue;
        d.set(l.source, d.get(l.source) + 1); d.set(l.target, d.get(l.target) + 1);
      }
      state.viewDeg = d;
    }
    state.shownIds = new Set(state.data.nodes.map((n) => n.id));
    state.adj = buildAdjacency(state.data);
    const key = state.data.nodes.map((n) => n.id).join("\n");
    const changed = key !== state.shownKey;
    state.shownKey = key;
    return changed;
  }

  // Re-run the layout on what the search now shows, gliding from where the
  // pages were. Nothing moves if the same pages are still shown.
  let filterTimer = null;
  function refilter() {
    clearTimeout(filterTimer);
    if (!filterShown()) { draw(); return; }
    if (!state.data.nodes.length) { finishGlide(); state.userView = false; draw(); return; }
    relayout(arrange);
  }

  // The pages on screen change once the search settles, not on every key. In
  // the app that is when the server's text search answers, because a page can
  // match on its text alone and the app's graph carries no text; the timer is
  // there in case it never does. In an export, which has every page's text,
  // it is a moment after the last key. Clearing the box brings every page
  // back at once.
  function scheduleFilter() {
    clearTimeout(filterTimer);
    if (!state.query) { refilter(); return; }
    filterTimer = setTimeout(refilter, serverSearch() ? 1500 : 150);
  }

  function serverSearch() { return !!(window.DEXIO_API && window.DEXIO_PROJECT); }

  // Lay most of the graph out before the first frame is drawn. Animated from
  // the starting ring, a large wiki flew apart across the screen for seconds
  // while the auto-fit zoom chased it out and back in: at 189 pages and 913
  // links each node travelled about 2,000 px on screen. Only the last of the
  // settling is animated now. Time-boxed, so a very large wiki still opens
  // promptly and does the rest on screen.
  const WARM_ALPHA = 0.05, WARM_MS = 250;
  // Furthest out the view zooms, by fit or by hand. Was 0.2; at Obsidian's
  // spacing a wiki of a few hundred pages needs less to fit on screen.
  const MIN_ZOOM = 0.05;
  function warmUp() {
    const t0 = performance.now();
    while (state.alpha > WARM_ALPHA && performance.now() - t0 < WARM_MS) step();
  }

  // One step of the current layout's simulation. Force runs Obsidian's forces
  // (below); By folder runs Dexio's own, with each folder held to its spot.
  function step() {
    if (state.layout === "force") stepForce();
    else stepFolders();
  }

  function alphaMin() { return state.layout === "force" ? OBS.alphaMin : 0.005; }

  /* ---- By folder: Dexio's own forces (Force used them until 2026-09-26) ----
     Repulsion falling off with the square of distance, springs on links, a
     pull to the folder's spot. */
  function stepFolders() {
    const nodes = state.data.nodes, links = state.data.links;
    if (!nodes.length) return;
    const w = canvas.clientWidth || 900, h = canvas.clientHeight || 600;
    const k = 1 + Math.sqrt(nodes.length);
    // Densely linked wikis collapse into a ball at a fixed repulsion, and the
    // labels then have nowhere to go. Scale repulsion with links per page.
    const repel = repulsion(), spring = 0.012, damp = 0.86;
    const folders = state.layout === "folders";

    for (let i = 0; i < nodes.length; i++) {
      const a = nodes[i], pa = state.pos.get(a.id), va = state.vel.get(a.id);
      for (let j = i + 1; j < nodes.length; j++) {
        const b = nodes[j], pb = state.pos.get(b.id), vb = state.vel.get(b.id);
        let dx = pa.x - pb.x, dy = pa.y - pb.y;
        let d2 = dx * dx + dy * dy;
        if (d2 < 1) { d2 = 1; dx = (Math.random() - 0.5); dy = (Math.random() - 0.5); }
        const f = repel / d2;
        const d = Math.sqrt(d2);
        va.x += (dx / d) * f; va.y += (dy / d) * f;
        vb.x -= (dx / d) * f; vb.y -= (dy / d) * f;
      }
      // Pages with no links feel only repulsion, so with the weak centring
      // every other node gets they drift off and shrink the fitted view.
      // By folder pulls each page to its folder's spot instead of the centre.
      const c = folders ? state.anchors.get(a.folder || "") : null;
      const g = c ? FOLDER_PULL * (deg(a) ? 1 : 3) : 0.004 * k * 0.06 * (deg(a) ? 1 : 12);
      va.x += ((c ? c.x : w / 2) - pa.x) * g;
      va.y += ((c ? c.y : h / 2) - pa.y) * g;
    }
    for (const l of links) {
      const pa = state.pos.get(l.source), pb = state.pos.get(l.target);
      if (!pa || !pb) continue;
      const va = state.vel.get(l.source), vb = state.vel.get(l.target);
      const dx = pb.x - pa.x, dy = pb.y - pa.y;
      const d = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      // By folder: a link across folders pulls weakly, or the groups merge.
      const across = folders &&
        (state.byId.get(l.source) || {}).folder !== (state.byId.get(l.target) || {}).folder;
      const f = (d - 120) * spring * (across ? FOLDER_CROSS : 1);
      va.x += (dx / d) * f; va.y += (dy / d) * f;
      vb.x -= (dx / d) * f; vb.y -= (dy / d) * f;
    }
    for (const n of nodes) {
      if (state.drag && state.drag.id === n.id) continue;
      const p = state.pos.get(n.id), v = state.vel.get(n.id);
      v.x *= damp; v.y *= damp;
      p.x += Math.max(-30, Math.min(30, v.x * state.alpha));
      p.y += Math.max(-30, Math.min(30, v.y * state.alpha));
    }
    state.alpha *= 0.985;
  }

  // ---- Force: Obsidian's layout ----------------------------------------------
  // Force runs the layout Obsidian's graph view runs: d3-force, set up the way
  // Obsidian sets it up (read from Obsidian 1.13.8's own code, 2026-09-26; see
  // the wiki's queries/graph-layout-algorithms). In Obsidian's order: a pull to
  // the centre on each axis (0.1); links (distance 250, d3's default strength,
  // which weakens a link by the smaller link count at its two ends, the better
  // linked end moving less); repulsion (1,000, as if never closer than 30,
  // Barnes-Hut at 0.9); collision (radius 60, strength 0.5). Then velocity
  // decay 0.6, cooling over 300 steps, and while a page is dragged, pinned to
  // the pointer with the layout kept warm at 0.3. Starting positions follow
  // Obsidian too, seeded rather than random so a wiki draws the same way each
  // time. Obsidian's own numbers, unscaled: its page circles (8 to 30 across
  // those distances) are close to ours (5 to 25), so pages sit as far apart
  // for their size as in Obsidian. Halving the distances crowded small wikis.
  // Step for step this gives the same positions as d3-force 3.0.0 itself
  // (tests/test_layout.mjs checks against a d3 run).
  // The force and quadtree code is adapted from d3-force and d3-quadtree
  // (https://github.com/d3), Copyright 2010-2021 Mike Bostock, ISC licence.
  const OBS = {
    centre: 0.1, linkDistance: 250, linkStrength: 1,
    repel: -1000, distanceMin2: 30 * 30, theta2: 0.81,
    collide: 60, collideStrength: 0.5, velocityDecay: 0.6,
    alphaMin: 0.001, alphaDecay: 1 - Math.pow(0.001, 1 / 300), dragAlpha: 0.3,
    spread: 60,
  };

  // d3's random source for its jiggle, and a second seeded one for the
  // starting positions, where Obsidian uses Math.random.
  function lcg(seed) {
    let s = seed;
    return () => (s = (1664525 * s + 1013904223) % 4294967296) / 4294967296;
  }
  const jiggle = (random) => (random() - 0.5) * 1e-6;

  // Obsidian's starting positions, page by page: next to pages already placed,
  // at their average, moved up to half of 60·√n either way on each axis; with
  // none placed yet, at random in a disc of one 60 × 60 square per page.
  function seedForce() {
    const nodes = state.data.nodes, { x: cx, y: cy } = centre();
    const rand = lcg(7 + nodes.length);
    const area = OBS.spread * OBS.spread * nodes.length;
    const disc = Math.sqrt(area / Math.PI), jitter = Math.sqrt(area);
    const placed = new Set();
    for (const nd of nodes) {
      let sx = 0, sy = 0, k = 0;
      for (const nb of state.adj.get(nd.id) || []) {
        if (!placed.has(nb)) continue;
        const q = state.pos.get(nb);
        sx += q.x; sy += q.y; k++;
      }
      const p = state.pos.get(nd.id), v = state.vel.get(nd.id);
      if (k) {
        p.x = sx / k + (rand() - 0.5) * jitter;
        p.y = sy / k + (rand() - 0.5) * jitter;
      } else {
        const a = 2 * Math.PI * rand(), r = Math.sqrt(rand()) * disc;
        p.x = cx + r * Math.cos(a); p.y = cy + r * Math.sin(a);
      }
      v.x = 0; v.y = 0;
      placed.add(nd.id);
    }
    // the simulation's own view of the pages and links, as d3 initialises it
    const parts = nodes.map((nd, index) => ({ index, id: nd.id, p: state.pos.get(nd.id), v: state.vel.get(nd.id) }));
    const byId = new Map(parts.map((q) => [q.id, q])), count = new Array(parts.length).fill(0);
    const links = [];
    for (const l of state.data.links) {
      const s = byId.get(l.source), t = byId.get(l.target);
      if (!s || !t || s === t) continue;
      links.push({ s, t });
      count[s.index]++; count[t.index]++;
    }
    for (const l of links) {
      l.bias = count[l.s.index] / (count[l.s.index] + count[l.t.index]);
      l.strength = OBS.linkStrength / Math.min(count[l.s.index], count[l.t.index]);
    }
    state.sim = { parts, links, random: lcg(1) };
  }

  function stepForce() {
    const sim = state.sim;
    if (!sim || !sim.parts.length) return;
    const { parts, links, random } = sim;
    state.alpha += ((state.drag ? OBS.dragAlpha : 0) - state.alpha) * OBS.alphaDecay;
    const alpha = state.alpha, { x: cx, y: cy } = centre();

    // centre, x then y (d3 forceX, forceY)
    for (const q of parts) q.v.x += (cx - q.p.x) * OBS.centre * alpha;
    for (const q of parts) q.v.y += (cy - q.p.y) * OBS.centre * alpha;

    // links (d3 forceLink)
    for (const l of links) {
      const s = l.s, t = l.t;
      let x = t.p.x + t.v.x - s.p.x - s.v.x || jiggle(random);
      let y = t.p.y + t.v.y - s.p.y - s.v.y || jiggle(random);
      let d = Math.sqrt(x * x + y * y);
      d = (d - OBS.linkDistance) / d * alpha * l.strength;
      x *= d; y *= d;
      t.v.x -= x * l.bias; t.v.y -= y * l.bias;
      s.v.x += x * (1 - l.bias); s.v.y += y * (1 - l.bias);
    }

    // repulsion, Barnes-Hut (d3 forceManyBody)
    const tree = quadtree(parts, (q) => q.p.x, (q) => q.p.y);
    qtVisitAfter(tree, (quad) => {
      let strength = 0, weight = 0, x = 0, y = 0;
      if (quad.length) {
        for (let i = 0; i < 4; ++i) {
          const q = quad[i];
          let c;
          if (q && (c = Math.abs(q.value))) { strength += q.value; weight += c; x += c * q.x; y += c * q.y; }
        }
        quad.x = x / weight; quad.y = y / weight;
      } else {
        let q = quad;
        q.x = q.data.p.x; q.y = q.data.p.y;
        do strength += OBS.repel; while ((q = q.next));
      }
      quad.value = strength;
    });
    for (const node of parts) {
      qtVisit(tree, (quad, x1, _, x2) => {
        if (!quad.value) return true;
        let x = quad.x - node.p.x, y = quad.y - node.p.y, w = x2 - x1, l = x * x + y * y;
        if (w * w / OBS.theta2 < l) {
          if (x === 0) { x = jiggle(random); l += x * x; }
          if (y === 0) { y = jiggle(random); l += y * y; }
          if (l < OBS.distanceMin2) l = Math.sqrt(OBS.distanceMin2 * l);
          node.v.x += x * quad.value * alpha / l;
          node.v.y += y * quad.value * alpha / l;
          return true;
        } else if (quad.length) return false;
        if (quad.data !== node || quad.next) {
          if (x === 0) { x = jiggle(random); l += x * x; }
          if (y === 0) { y = jiggle(random); l += y * y; }
          if (l < OBS.distanceMin2) l = Math.sqrt(OBS.distanceMin2 * l);
        }
        do if (quad.data !== node) {
          w = OBS.repel * alpha / l;
          node.v.x += x * w; node.v.y += y * w;
        } while ((quad = quad.next));
        return false;
      });
    }

    // collision (d3 forceCollide; it does not cool with alpha)
    const R = OBS.collide, ctree = quadtree(parts, (q) => q.p.x + q.v.x, (q) => q.p.y + q.v.y);
    qtVisitAfter(ctree, (quad) => {
      if (quad.data) { quad.r = R; return; }
      quad.r = 0;
      for (let i = 0; i < 4; ++i) if (quad[i] && quad[i].r > quad.r) quad.r = quad[i].r;
    });
    for (const node of parts) {
      const ri = R, ri2 = R * R, xi = node.p.x + node.v.x, yi = node.p.y + node.v.y;
      qtVisit(ctree, (quad, x0, y0, x1, y1) => {
        const data = quad.data;
        let rj = quad.r, r = ri + rj;
        if (data) {
          if (data.index > node.index) {
            let x = xi - data.p.x - data.v.x, y = yi - data.p.y - data.v.y, l = x * x + y * y;
            if (l < r * r) {
              if (x === 0) { x = jiggle(random); l += x * x; }
              if (y === 0) { y = jiggle(random); l += y * y; }
              l = (r - (l = Math.sqrt(l))) / l * OBS.collideStrength;
              node.v.x += (x *= l) * (r = (rj *= rj) / (ri2 + rj));
              node.v.y += (y *= l) * r;
              data.v.x -= x * (r = 1 - r);
              data.v.y -= y * r;
            }
          }
          return false;
        }
        return x0 > xi + r || x1 < xi - r || y0 > yi + r || y1 < yi - r;
      });
    }

    // move (a dragged page is pinned to the pointer, as d3's fx/fy pin it)
    for (const q of parts) {
      if (state.drag && state.drag.id === q.id) { q.v.x = 0; q.v.y = 0; continue; }
      q.p.x += q.v.x *= OBS.velocityDecay;
      q.p.y += q.v.y *= OBS.velocityDecay;
    }
  }

  // A point quadtree, as d3-quadtree builds it: internal nodes are 4-arrays,
  // leaves {data, next} with coincident points chained on next.
  function quadtree(data, fx, fy) {
    const t = { fx, fy, x0: NaN, y0: NaN, x1: NaN, y1: NaN, root: undefined };
    const n = data.length, xz = new Array(n), yz = new Array(n);
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (let i = 0; i < n; ++i) {
      const x = +fx(data[i]), y = +fy(data[i]);
      if (isNaN(x) || isNaN(y)) continue;
      xz[i] = x; yz[i] = y;
      if (x < x0) x0 = x;
      if (x > x1) x1 = x;
      if (y < y0) y0 = y;
      if (y > y1) y1 = y;
    }
    if (x0 > x1 || y0 > y1) return t;
    qtCover(t, x0, y0); qtCover(t, x1, y1);
    for (let i = 0; i < n; ++i) qtAdd(t, xz[i], yz[i], data[i]);
    return t;
  }

  function qtCover(t, x, y) {
    let x0 = t.x0, y0 = t.y0, x1 = t.x1, y1 = t.y1;
    if (isNaN(x0)) {
      x1 = (x0 = Math.floor(x)) + 1;
      y1 = (y0 = Math.floor(y)) + 1;
    } else {
      let z = x1 - x0 || 1, node = t.root;
      while (x0 > x || x >= x1 || y0 > y || y >= y1) {
        const i = (y < y0) << 1 | (x < x0), parent = new Array(4);
        parent[i] = node; node = parent; z *= 2;
        switch (i) {
          case 0: x1 = x0 + z; y1 = y0 + z; break;
          case 1: x0 = x1 - z; y1 = y0 + z; break;
          case 2: x1 = x0 + z; y0 = y1 - z; break;
          case 3: x0 = x1 - z; y0 = y1 - z; break;
        }
      }
      if (t.root && t.root.length) t.root = node;
    }
    t.x0 = x0; t.y0 = y0; t.x1 = x1; t.y1 = y1;
  }

  function qtAdd(t, x, y, d) {
    if (isNaN(x) || isNaN(y)) return;
    let parent, node = t.root, x0 = t.x0, y0 = t.y0, x1 = t.x1, y1 = t.y1, xm, ym, right, bottom, i, j;
    const leaf = { data: d };
    if (!node) { t.root = leaf; return; }
    while (node.length) {
      if ((right = x >= (xm = (x0 + x1) / 2))) x0 = xm; else x1 = xm;
      if ((bottom = y >= (ym = (y0 + y1) / 2))) y0 = ym; else y1 = ym;
      parent = node;
      if (!(node = node[i = bottom << 1 | right])) { parent[i] = leaf; return; }
    }
    const xp = +t.fx(node.data), yp = +t.fy(node.data);
    if (x === xp && y === yp) {
      leaf.next = node;
      if (parent) parent[i] = leaf; else t.root = leaf;
      return;
    }
    do {
      parent = parent ? (parent[i] = new Array(4)) : (t.root = new Array(4));
      if ((right = x >= (xm = (x0 + x1) / 2))) x0 = xm; else x1 = xm;
      if ((bottom = y >= (ym = (y0 + y1) / 2))) y0 = ym; else y1 = ym;
    } while ((i = bottom << 1 | right) === (j = (yp >= ym) << 1 | (xp >= xm)));
    parent[j] = node; parent[i] = leaf;
  }

  // Parents first; a callback returning true skips that quad's children.
  function qtVisit(t, cb) {
    const quads = [];
    if (t.root) quads.push([t.root, t.x0, t.y0, t.x1, t.y1]);
    let q;
    while ((q = quads.pop())) {
      const [node, x0, y0, x1, y1] = q;
      if (!cb(node, x0, y0, x1, y1) && node.length) {
        const xm = (x0 + x1) / 2, ym = (y0 + y1) / 2;
        let c;
        if ((c = node[3])) quads.push([c, xm, ym, x1, y1]);
        if ((c = node[2])) quads.push([c, x0, ym, xm, y1]);
        if ((c = node[1])) quads.push([c, xm, y0, x1, ym]);
        if ((c = node[0])) quads.push([c, x0, y0, xm, ym]);
      }
    }
  }

  // Children first.
  function qtVisitAfter(t, cb) {
    const quads = [], next = [];
    if (t.root) quads.push([t.root, t.x0, t.y0, t.x1, t.y1]);
    let q;
    while ((q = quads.pop())) {
      const [node, x0, y0, x1, y1] = q;
      if (node.length) {
        const xm = (x0 + x1) / 2, ym = (y0 + y1) / 2;
        let c;
        if ((c = node[0])) quads.push([c, x0, y0, xm, ym]);
        if ((c = node[1])) quads.push([c, xm, y0, x1, ym]);
        if ((c = node[2])) quads.push([c, x0, ym, xm, y1]);
        if ((c = node[3])) quads.push([c, xm, ym, x1, y1]);
      }
      next.push(q);
    }
    while ((q = next.pop())) cb(q[0], q[1], q[2], q[3], q[4]);
  }

  function repulsion() {
    const nodes = state.data.nodes, links = state.data.links;
    const density = links.length / Math.max(1, nodes.length);
    return 5200 * Math.min(3, 1 + density / 3);
  }

  // ---- layouts ------------------------------------------------------------
  // Four ways to place the pages, picked from the menu over the graph's
  // top-left corner and remembered per browser (Forrest, 2026-09-26). By
  // folder is what a browser that never picked one sees (Forrest, 2026-09-27;
  // Force was until then).
  //   folders  the same forces, each folder held to its own part of the graph;
  //            the default, and first in the menu (Forrest, 2026-09-27)
  //   force    links pull, pages push apart
  //   radial   one page in the middle, the rest in rings by links away
  //   circle   every page round one circle, folder by folder
  // Force and By folder are simulations: they settle before they are shown,
  // and give way around a dragged page. Radial and Circle are worked out
  // directly and hold still, so a dragged page stays where it is dropped.
  // Switching glides each page from where it was to where it goes, so the eye
  // can follow one across rather than watching the graph re-form.
  const LAYOUT_KEY = "dexio-layout";
  const LAYOUTS = ["folders", "force", "radial", "circle"];
  const DEFAULT_LAYOUT = "folders";
  const GLIDE_MS = 520;
  const FOLDER_PULL = 0.06, FOLDER_CROSS = 0.05, FOLDER_SPACE = 70;
  const RING_GAP = 140, RING_PAD = 18, CIRCLE_PAD = 12, FOLDER_GAP = 26;
  const layoutBtn = document.getElementById("layout-button");
  const layoutMenu = document.getElementById("layout-menu");
  // What the menu shows for each layout: a name, one line on what it does, and
  // a small picture of it.
  const LAYOUT_INFO = {
    force: { name: "Force", desc: "Links pull, pages push apart",
      icon: '<path d="M5.4 5.3 10.4 4.4M4.9 6.7 7.1 10.6M11.1 6.1 8.9 10.6"></path>' +
            '<circle class="dot" cx="3.8" cy="5.6" r="1.9"></circle><circle class="dot" cx="12" cy="4.1" r="1.9"></circle>' +
            '<circle class="dot" cx="8" cy="12.3" r="1.9"></circle>' },
    folders: { name: "By folder", desc: "Each folder in its own area",
      icon: '<rect x="1.2" y="2.5" width="6.1" height="11" rx="2"></rect><rect x="8.7" y="2.5" width="6.1" height="11" rx="2"></rect>' +
            '<circle class="dot" cx="4.25" cy="6" r="1.2"></circle><circle class="dot" cx="4.25" cy="10" r="1.2"></circle>' +
            '<circle class="dot" cx="11.75" cy="8" r="1.2"></circle>' },
    radial: { name: "Radial", desc: "Rings out from the open page",
      icon: '<circle cx="8" cy="8" r="3.6"></circle><circle cx="8" cy="8" r="6.6"></circle>' +
            '<circle class="dot" cx="8" cy="8" r="1.5"></circle>' },
    circle: { name: "Circle", desc: "Every page round one circle",
      icon: [0, 1, 2, 3, 4, 5, 6, 7].map((i) => {
        const a = i * Math.PI / 4;
        return `<circle class="dot" cx="${(8 + Math.cos(a) * 5.6).toFixed(2)}" cy="${(8 + Math.sin(a) * 5.6).toFixed(2)}" r="1.25"></circle>`;
      }).join("") },
  };
  const layoutIcon = (id) => `<svg class="lo-icon" viewBox="0 0 16 16" aria-hidden="true">${LAYOUT_INFO[id].icon}</svg>`;
  const CHECK = '<svg class="sw-check" viewBox="0 0 16 16" aria-hidden="true">' +
                '<path d="M3.5 8.4 6.6 11.4 12.5 4.8"></path></svg>';

  function simulated() { return state.layout === "force" || state.layout === "folders"; }

  function savedLayout() {
    try {
      const v = localStorage.getItem(LAYOUT_KEY);
      if (LAYOUTS.includes(v)) return v;
    } catch (e) {}
    return DEFAULT_LAYOUT;
  }

  function byFolderThenTitle(a, b) {
    const fa = state.folders.indexOf((a && a.folder) || ""), fb = state.folders.indexOf((b && b.folder) || "");
    if (fa !== fb) return fa - fb;
    return String((a && (a.title || a.id)) || "").localeCompare(String((b && (b.title || b.id)) || ""),
      undefined, { sensitivity: "base", numeric: true });
  }

  function centre() {
    return { x: (canvas.clientWidth || 900) / 2, y: (canvas.clientHeight || 600) / 2 };
  }

  // Place every page for the current layout, starting from where they are.
  function arrange() {
    if (!state.data.nodes.length) return;
    if (state.layout === "radial") placeRadial(radialCentre());
    else if (state.layout === "circle") placeCircle();
    else {
      if (state.layout === "folders") placeFolderAnchors();
      else seedForce();
      for (const v of state.vel.values()) { v.x = 0; v.y = 0; }
      state.alpha = 1;
      warmUp();
    }
  }

  function setLayout(name, remember) {
    if (!LAYOUTS.includes(name)) name = DEFAULT_LAYOUT;
    showLayout(name);
    if (remember) { try { localStorage.setItem(LAYOUT_KEY, name); } catch (e) {} }
    if (name === state.layout) return;
    state.layout = name;
    relayout(arrange);
  }

  // ---- the layout menu ----
  // A button and a menu of four, like the wiki switcher's: arrow keys move,
  // Enter picks, Escape or a click elsewhere closes.
  function layoutMenuHtml() {
    return '<div class="sw-head">Layout</div>' + LAYOUTS.map((id) => {
      const on = id === state.layout, info = LAYOUT_INFO[id];
      return `<button type="button" class="sw-item" role="menuitemradio" tabindex="-1" ` +
        `data-layout="${id}" aria-checked="${on}">${layoutIcon(id)}` +
        `<span class="lo-text"><span class="sw-name">${info.name}</span>` +
        `<span class="lo-desc">${info.desc}</span></span>${CHECK}</button>`;
    }).join("");
  }

  function showLayout(id) {
    if (!layoutBtn || !layoutBtn.querySelector) return;
    const now = layoutBtn.querySelector(".lo-now");
    if (now) now.textContent = LAYOUT_INFO[id].name;
    if (layoutBtn.setAttribute) layoutBtn.setAttribute("aria-label", `Layout: ${LAYOUT_INFO[id].name}. Change layout`);
  }

  function layoutItems() { return [...layoutMenu.querySelectorAll(".sw-item")]; }

  function openLayoutMenu(last) {
    layoutMenu.innerHTML = layoutMenuHtml();
    layoutMenu.hidden = false;
    layoutBtn.setAttribute("aria-expanded", "true");
    const list = layoutItems();
    const target = last ? list[list.length - 1]
                        : list.find((i) => i.getAttribute("aria-checked") === "true") || list[0];
    if (target) target.focus();
  }

  function closeLayoutMenu(refocus) {
    if (layoutMenu.hidden) return;
    layoutMenu.hidden = true;
    layoutBtn.setAttribute("aria-expanded", "false");
    if (refocus) layoutBtn.focus();
  }

  if (layoutBtn && layoutMenu && layoutMenu.querySelectorAll) {
    layoutBtn.addEventListener("click", () => (layoutMenu.hidden ? openLayoutMenu(false) : closeLayoutMenu(true)));
    layoutBtn.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      openLayoutMenu(e.key === "ArrowUp");
    });
    layoutMenu.addEventListener("click", (e) => {
      const item = e.target.closest("[data-layout]");
      if (!item) return;
      closeLayoutMenu(true);
      setLayout(item.dataset.layout, true);
    });
    layoutMenu.addEventListener("keydown", (e) => {
      const list = layoutItems(), i = list.indexOf(document.activeElement);
      const go = (j) => { e.preventDefault(); list[(j + list.length) % list.length].focus(); };
      if (e.key === "ArrowDown") go(i + 1);
      else if (e.key === "ArrowUp") go(i - 1);
      else if (e.key === "Home") go(0);
      else if (e.key === "End") go(list.length - 1);
      // stopped here so the window's Escape does not also close the open page
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); closeLayoutMenu(true); }
      else if (e.key === "Tab") closeLayoutMenu(false);
    });
    window.addEventListener("pointerdown", (e) => {
      // (the row it sits in also holds Show and the folder pill)
      if (!layoutBtn.contains(e.target) && !layoutMenu.contains(e.target)) closeLayoutMenu(false);
    });
  }

  // Work out the new positions, then glide there from the current ones, and
  // frame the result in whatever the sidebars leave showing.
  function relayout(place) {
    if (!state.data.nodes.length) return;
    finishGlide();
    const from = new Map();
    for (const [id, p] of state.pos) from.set(id, { x: p.x, y: p.y });
    state.userView = false;
    state.fitLeft = coveredLeft();
    state.fitRight = coveredRight();
    place();
    const to = new Map();
    for (const [id, p] of state.pos) to.set(id, { x: p.x, y: p.y });
    if (!matchMedia("(prefers-reduced-motion: reduce)").matches) {
      for (const [id, p] of state.pos) { const f = from.get(id); p.x = f.x; p.y = f.y; }
      state.glide = { from, to, t0: performance.now() };
    }
    tick();
  }

  function glideStep(now) {
    const g = state.glide;
    const t = Math.max(0, ((now || performance.now()) - g.t0) / GLIDE_MS);
    if (t >= 1) { finishGlide(); return true; }
    const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(2 - 2 * t, 3) / 2;   // ease in and out
    for (const [id, p] of state.pos) {
      const a = g.from.get(id), b = g.to.get(id);
      if (!a || !b) continue;
      p.x = a.x + (b.x - a.x) * e; p.y = a.y + (b.y - a.y) * e;
    }
    return false;
  }

  // A drag, a new wiki or another switch cuts a glide short: jump to its end.
  function finishGlide() {
    if (!state.glide) return;
    for (const [id, p] of state.pos) {
      const b = state.glide.to.get(id);
      if (b) { p.x = b.x; p.y = b.y; }
    }
    state.glide = null;
  }

  // ---- by folder ----
  // Each folder gets a spot, found by a small layout of the folders
  // themselves: folders that link to each other sit near, none overlap. The
  // spot's size is about where a folder's own pages settle, their pushing
  // apart against the pull back to the spot.
  function placeFolderAnchors() {
    const { x: cx, y: cy } = centre();
    const repel = repulsion();
    // only folders with a page on screen get a spot, so a search leaves no
    // empty spaces where the folders it hid would have sat
    const count = new Map();
    for (const n of state.data.nodes) count.set(n.folder || "", (count.get(n.folder || "") || 0) + 1);
    const fs = state.folders.filter((f) => count.has(f)).map((f, i) => ({
      f, i, r: Math.cbrt(count.get(f) * repel / FOLDER_PULL) * 0.9 + 24, x: 0, y: 0,
    }));
    const pair = new Map();
    for (const l of state.data.links) {
      const a = (state.byId.get(l.source) || {}).folder || "", b = (state.byId.get(l.target) || {}).folder || "";
      if (a === b) continue;
      const key = a < b ? a + "\n" + b : b + "\n" + a;
      pair.set(key, (pair.get(key) || 0) + 1);
    }
    const idx = new Map(fs.map((g) => [g.f, g]));
    const ring = fs.reduce((s, g) => s + g.r, 0) / Math.PI;
    fs.forEach((g, i) => {
      const a = (i / fs.length) * Math.PI * 2 - Math.PI / 2;
      g.x = cx + Math.cos(a) * ring; g.y = cy + Math.sin(a) * ring;
    });
    if (fs.length === 1) { fs[0].x = cx; fs[0].y = cy; }
    for (let round = 0; round < 300; round++) {
      // linked folders drift together, all of them toward the middle
      for (const [key, w] of pair) {
        const [fa, fb] = key.split("\n"), a = idx.get(fa), b = idx.get(fb);
        const dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy) || 1;
        const pull = Math.max(0, d - a.r - b.r) * 0.02 * Math.min(1, w / 4);
        a.x += (dx / d) * pull; a.y += (dy / d) * pull;
        b.x -= (dx / d) * pull; b.y -= (dy / d) * pull;
      }
      for (const g of fs) { g.x += (cx - g.x) * 0.01; g.y += (cy - g.y) * 0.01; }
      // and never overlap
      for (let i = 0; i < fs.length; i++) {
        for (let j = i + 1; j < fs.length; j++) {
          const a = fs[i], b = fs[j];
          let dx = b.x - a.x, dy = b.y - a.y, d = Math.hypot(dx, dy);
          if (d < 1e-6) { dx = 1; dy = 0; d = 1; }
          const min = a.r + b.r + FOLDER_SPACE;
          if (d >= min) continue;
          const push = (min - d) / 2;
          a.x -= (dx / d) * push; a.y -= (dy / d) * push;
          b.x += (dx / d) * push; b.y += (dy / d) * push;
        }
      }
    }
    state.anchors = new Map(fs.map((g) => [g.f, { x: g.x, y: g.y }]));
    // start each page near its folder's spot, so the groups form directly
    // rather than having to untangle
    const seen = new Map();
    for (const n of state.data.nodes) {
      const g = idx.get(n.folder || ""), i = seen.get(g.f) || 0;
      seen.set(g.f, i + 1);
      const a = i * 2.39996, r = 14 * Math.sqrt(i + 0.5);   // a sunflower spiral
      const p = state.pos.get(n.id);
      p.x = g.x + Math.cos(a) * r; p.y = g.y + Math.sin(a) * r;
    }
  }

  // ---- radial ----
  // The open page, or the most linked page when none is open (or a search
  // hides the open one).
  function radialCentre() {
    if (state.selected && state.shownIds.has(state.selected)) return state.selected;
    let best = null;
    for (const n of state.data.nodes) if (!best || deg(n) > deg(best)) best = n;
    return best ? best.id : null;
  }

  function placeRadial(centreId) {
    const { x: cx, y: cy } = centre();
    state.radialCentre = centreId;
    if (!centreId) return;
    // breadth first from the centre: ring k holds the pages k links away, and
    // each page hangs under the first page that reached it
    const depth = new Map([[centreId, 0]]), kids = new Map(), order = [centreId];
    for (let i = 0; i < order.length; i++) {
      const id = order[i];
      kids.set(id, []);
      for (const nb of state.adj.get(id) || []) {
        if (depth.has(nb)) continue;
        depth.set(nb, depth.get(id) + 1); kids.get(id).push(nb); order.push(nb);
      }
    }
    // each page gets a slice of the circle in proportion to what hangs under
    // it, so a branch stays in one direction
    const weight = new Map();
    for (let i = order.length - 1; i >= 0; i--) {
      const id = order[i];
      weight.set(id, 1 + kids.get(id).reduce((s, c) => s + weight.get(c), 0));
    }
    const angle = new Map(), slice = new Map([[centreId, [-Math.PI / 2, Math.PI * 1.5]]]);
    for (const id of order) {
      let [a0] = slice.get(id);
      const a1 = slice.get(id)[1], cs = kids.get(id);
      const total = cs.reduce((s, c) => s + weight.get(c), 0);
      for (const c of cs) {
        const a = a0 + (a1 - a0) * weight.get(c) / total;
        slice.set(c, [a0, a]); angle.set(c, (a0 + a) / 2); a0 = a;
      }
    }
    const rings = [];
    for (const id of order) (rings[depth.get(id)] = rings[depth.get(id)] || []).push(id);
    // pages no path of links reaches go round the outside, by folder
    const loose = state.data.nodes.filter((n) => !depth.has(n.id)).sort(byFolderThenTitle).map((n) => n.id);
    loose.forEach((id, i) => angle.set(id, -Math.PI / 2 + ((i + 0.5) / loose.length) * Math.PI * 2));
    if (loose.length) rings.push(loose);
    state.pos.get(centreId).x = cx; state.pos.get(centreId).y = cy;
    // Each ring far enough out to read and long enough round for its pages.
    // Then spaced evenly out to the last: around a page with few links next
    // to a hub, ring 2 can hold most of the wiki, and the rings inside it
    // would otherwise shrink to a dot in the middle.
    const radii = [0];
    for (let d = 1; d < rings.length; d++) {
      const need = rings[d].reduce((s, id) => s + 2 * radius(state.byId.get(id)) + RING_PAD, 0);
      radii[d] = Math.max(radii[d - 1] + RING_GAP, need / (Math.PI * 2));
    }
    const outer = radii[rings.length - 1] || 0;
    for (let d = 1; d < rings.length; d++) {
      radii[d] = Math.max(radii[d], outer * d / (rings.length - 1));
      const ring = rings[d], r = radii[d];
      spreadRound(ring, angle, r);
      for (const id of ring) {
        const p = state.pos.get(id);
        p.x = cx + Math.cos(angle.get(id)) * r; p.y = cy + Math.sin(angle.get(id)) * r;
      }
    }
  }

  // Nudge the pages on one ring apart until none overlap, keeping their order.
  function spreadRound(ids, angle, r) {
    const items = ids.map((id) => ({
      id, a: angle.get(id), s: (radius(state.byId.get(id)) + RING_PAD / 2) / r,
    })).sort((p, q) => p.a - q.a);
    const n = items.length;
    if (n < 2) return;
    const total = items.reduce((s, it) => s + 2 * it.s, 0);
    if (total > Math.PI * 2 * 0.85) {
      // nearly full: evenly, in order, scaled to close the circle
      const k = (Math.PI * 2) / total;
      let a = items[0].a - items[0].s * k;
      for (const it of items) { it.a = a + it.s * k; a += 2 * it.s * k; }
    } else {
      for (let pass = 0; pass < 200; pass++) {
        let moved = false;
        for (let i = 0; i < n; i++) {
          const p = items[i], q = items[(i + 1) % n];
          const gap = q.a - p.a + (i === n - 1 ? Math.PI * 2 : 0), need = p.s + q.s;
          if (gap < need - 1e-9) {
            const d = (need - gap) / 2;
            p.a -= d; q.a += d; moved = true;
          }
        }
        if (!moved) break;
      }
    }
    for (const it of items) angle.set(it.id, it.a);
  }

  // ---- circle ----
  // Folder by folder round one circle, a small gap between folders, sized so
  // that no two pages overlap.
  function placeCircle() {
    const { x: cx, y: cy } = centre();
    const nodes = [...state.data.nodes].sort(byFolderThenTitle);
    let prev = null, total = 0;
    const slots = nodes.map((n) => {
      const f = n.folder || "", gap = prev !== null && f !== prev ? FOLDER_GAP : 0;
      prev = f;
      const s = 2 * radius(n) + CIRCLE_PAD;
      total += s + gap;
      return { n, s, gap };
    });
    if (new Set(nodes.map((n) => n.folder || "")).size > 1) total += FOLDER_GAP;
    const r = Math.max(120, total / (Math.PI * 2));
    let a = -Math.PI / 2;
    for (const { n, s, gap } of slots) {
      a += (gap + s / 2) / r;
      const p = state.pos.get(n.id);
      p.x = cx + Math.cos(a) * r; p.y = cy + Math.sin(a) * r;
      a += s / 2 / r;
    }
  }

  function radius(n) { return 5 + Math.min(20, Math.sqrt(deg(n)) * 4.5); }

  // Search matches words, not one phrase, the same way the server's search does
  // (search.py): common words are dropped unless nothing else is left, every
  // other word has to appear in the page's path, title or text, and "quoted
  // text" is matched as written. Titles and paths filter as you type; the
  // server's text search adds pages whose text matches (state.textHits).
  const STOPWORDS = new Set(("a about am an and any are as at be been but by can could did " +
    "do does doing for from had has have how i if in into is it its just me my of on or " +
    "our should so than that the their them then there these they this those to was we " +
    "were what when where which who whom why will with would you your").split(" "));

  function queryTerms(q) {
    const phrases = [], words = [];
    for (const m of q.matchAll(/"([^"]+)"|(\S+)/g)) {
      if (m[1] !== undefined) { const p = m[1].trim().replace(/\s+/g, " "); if (p) phrases.push(p.toLowerCase()); }
      else { const w = m[2].replace(/^["'`.,;:!?()[\]{}<>*]+|["'`.,;:!?()[\]{}<>*]+$/g, ""); if (w) words.push(w.toLowerCase()); }
    }
    let kept = words.filter((w) => !STOPWORDS.has(w));
    if (!kept.length && !phrases.length) kept = words;
    return [...new Set([...phrases, ...kept])];
  }

  function matchesQuery(n) {
    if (!state.query) return true;
    if (state.textHits && state.textHits.has(n.id)) return true;
    if (state.termsFor !== state.query) {        // parsed once per query, not per node
      state.queryTerms = queryTerms(state.query); state.termsFor = state.query;
    }
    const terms = state.queryTerms;
    if (!terms.length) return false;
    const hay = (n.id + "\n" + (n.title || "") + "\n" + (n.text || "")).toLowerCase();
    return terms.every((t) => hay.includes(t));
  }

  let searchSeq = 0, searchTimer = null;
  function textSearch() {
    const q = state.query, seq = ++searchSeq;
    state.textHits = null; state.textRank = null;
    clearTimeout(searchTimer);
    if (!q || !window.DEXIO_API || !window.DEXIO_PROJECT) return;
    searchTimer = setTimeout(async () => {
      try {
        const r = await fetch(withW(`${window.DEXIO_API}/search?project=${encodeURIComponent(
          window.DEXIO_PROJECT)}&q=${encodeURIComponent(q)}`));
        if (seq !== searchSeq) return;
        if (!r.ok) { refilter(); return; }       // titles still filter
        const found = ((await r.json()).results || []).map((x) => x.path);
        if (seq !== searchSeq) return;
        state.textHits = new Set(found);
        state.textRank = new Map(found.map((p, i) => [p, i]));   // best first
        refilter(); renderTree(); renderFind();
      } catch (e) { if (seq === searchSeq) refilter(); }   // titles still filter
    }, 200);
  }

  // Whether `name` is what the query names: it holds every term, and every
  // word in it is one of them (so "acme" names Acme, not Acme Billing).
  function namedBy(name, terms) {
    name = (name || "").toLowerCase();
    const words = (name.match(/[\p{L}\p{N}]+/gu) || []).filter((w) => !STOPWORDS.has(w));
    if (!words.length || !terms.every((t) => name.includes(t))) return false;
    const parts = terms.map((t) => new Set(t.match(/[\p{L}\p{N}]+/gu) || []));
    return words.every((w) => terms.some((t, i) => w.includes(t) || parts[i].has(w)));
  }

  function pageNamedBy(n, terms) {
    return namedBy(n.title, terms) || namedBy(n.id.split("/").pop(), terms);
  }

  // The pages the search matches, best first. Once the server's text search
  // is back its order leads (search.py ranks by words found, then words in the
  // page named for the query, then words in the path or title, then the best
  // line, then BM25). Until then, and in an exported file, the page named for
  // the query comes first (its title or the last part of its path is the
  // query), then pages with more of the words in their path or title, then the
  // pages where the words weigh most for their length (BM25 without the
  // rarity part), then the most linked.
  function rankedMatches() {
    if (!state.query) return [];
    const terms = queryTerms(state.query), rank = state.textRank;
    const inHead = (n) => {
      const head = (n.id + "\n" + (n.title || "")).toLowerCase();
      return terms.filter((t) => head.includes(t)).length;
    };
    const named = (n) => pageNamedBy(n, terms);
    const len = (n) => {                          // counted once per text, not per keystroke
      if (n._lenOf !== n.text) {
        n._lenOf = n.text; n._len = n.text ? n.text.split(/\s+/).filter(Boolean).length : 0;
      }
      return n._len;
    };
    const nodes = state.all.nodes, avg = nodes.reduce((s, n) => s + len(n), 0) / (nodes.length || 1);
    const weight = (n) => {
      const text = (n.text || "").toLowerCase();
      if (!text) return 0;
      const norm = 1.2 * (0.25 + 0.75 * len(n) / (avg || 1));
      return terms.reduce((sum, t) => {
        const tf = text.split(t).length - 1;
        return sum + (tf ? tf * 2.2 / (tf + norm) : 0);
      }, 0);
    };
    return nodes.filter((n) => inFocus(n) && matchesQuery(n))
      .map((n) => ({ n, r: rank && rank.has(n.id) ? rank.get(n.id) : Infinity,
                     e: named(n) ? 1 : 0, h: inHead(n), u: weight(n) }))
      .sort((a, b) => (a.r - b.r) || (b.e - a.e) || (b.h - a.h) || (b.u - a.u) ||
                      ((b.n.degree || 0) - (a.n.degree || 0)) || byName(label(a.n), label(b.n)))
      .map((x) => x.n);
  }

  // Folders the search finds (Forrest, 2026-09-28: "we should be able to
  // search for folders"): those whose own name holds every word of the query,
  // below the folder the graph is narrowed to, each with the pages under it.
  // A guest's graph holds only what is shared, so only those folders come up.
  // The folder named for the query first, then the fullest.
  function matchedFolders() {
    if (!state.query) return [];
    const terms = queryTerms(state.query);
    if (!terms.length) return [];
    const pages = new Map();
    for (const n of state.all.nodes) {
      let p = "";
      for (const part of (n.folder || "").split("/").filter(Boolean)) {
        p = p ? p + "/" + part : part;
        pages.set(p, (pages.get(p) || 0) + 1);
      }
    }
    const out = [];
    for (const [dir, count] of pages) {
      if (state.focus != null && !dir.startsWith(state.focus + "/")) continue;
      const name = dir.split("/").pop(), low = name.toLowerCase();
      if (!terms.every((t) => low.includes(t))) continue;
      out.push({ dir, name, pages: count, e: namedBy(name, terms) ? 1 : 0 });
    }
    return out.sort((a, b) => (b.e - a.e) || (b.pages - a.pages) || byName(a.dir, b.dir));
  }

  // The rows under the search box, pages and folders in one list: the pages
  // named for the query, then the folders, then the other pages (Forrest,
  // 2026-09-28: a folder should not sit above the page of the same
  // name). The folders go below the last page named for the query
  // among the rows shown, and never off the end of the list.
  const FIND_FOLDERS = 3;
  function findRows(pages, folders) {
    const dirs = folders.slice(0, FIND_FOLDERS);
    const room = FIND_MAX - dirs.length, terms = queryTerms(state.query);
    let at = 0;
    pages.slice(0, room).forEach((n, i) => { if (pageNamedBy(n, terms)) at = i + 1; });
    return [...pages.slice(0, at), ...dirs, ...pages.slice(at)].slice(0, FIND_MAX);
  }

  function inFolder(n, folder) {
    const f = n.folder || "";
    return f === folder || f.startsWith(folder + "/");
  }

  // Inside the folder the graph is narrowed to, or no folder is (see "one folder").
  function inFocus(n) {
    return state.focus == null || inFolder(n, state.focus);
  }

  // Dimmed in the graph while a folder in the tree is hovered: the pages
  // outside it; and, with the graph narrowed to one folder, the linked pages
  // outside it. Pages the search leaves out are not dimmed but hidden
  // (filterShown).
  function matches(n) {
    if (state.fringe.has(n.id) && n.id !== state.selected) return false;
    return state.hoverFolder == null || inFolder(n, state.hoverFolder);
  }

  function toScreen(p) {
    return { x: p.x * state.view.k + state.view.x, y: p.y * state.view.k + state.view.y };
  }

  // Width of the graph's left edge hidden under the folder tree. The tree
  // lies over the canvas rather than beside it, so the fit leaves it room:
  // measured when a wiki loads and when the canvas resizes, and kept in
  // state.fitLeft, so opening or closing the tree never moves the graph, even
  // while the layout is still settling. Like the page panel, it slides over a
  // graph that stays put.
  function coveredLeft() {
    return wide.matches && treeOpen() && treeEl ? treeEl.offsetWidth : 0;
  }

  // The same on the right, for the page panel. Only a new layout frames
  // itself around the panel (Radial re-centres on the page being opened);
  // otherwise the panel, like the tree, slides over a graph that stays put.
  function coveredRight(opening) {
    const open = opening || (panel && panel.classList.contains("open"));
    return wide.matches && open && panel ? panel.offsetWidth : 0;
  }

  // Zoom and centre so the laid-out graph fills the canvas, less any part the
  // folder tree covers. Eased rather than snapped, because it runs every frame
  // while the layout is still settling; `snap` jumps straight there (the
  // canvas was resized).
  function fitView(snap) {
    const nodes = state.data.nodes;
    if (!nodes.length || state.userView) return;
    const left = state.fitLeft || 0;
    const w = canvas.clientWidth - left - (state.fitRight || 0), h = canvas.clientHeight;
    if (w <= 0 || !h) return;
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const n of nodes) {
      const p = state.pos.get(n.id); if (!p) continue;
      const r = radius(n);
      x0 = Math.min(x0, p.x - r); x1 = Math.max(x1, p.x + r);
      y0 = Math.min(y0, p.y - r); y1 = Math.max(y1, p.y + r + 16);
    }
    // room for the folder names drawn outside the circle, or above the groups
    const px = 36 + (state.layout === "circle" ? 110 : 0);
    const pt = 36 + (state.layout === "circle" ? 30 : state.layout === "folders" ? 22 : 0);
    const pb = 36 + (state.layout === "circle" ? 30 : 0);
    const k = Math.max(MIN_ZOOM, Math.min(1.6,
      Math.min((w - px * 2) / Math.max(1, x1 - x0), (h - pt - pb) / Math.max(1, y1 - y0))));
    const tx = left + w / 2 - ((x0 + x1) / 2) * k, ty = pt + (h - pt - pb) / 2 - ((y0 + y1) / 2) * k;
    const e = snap || (state.view.k === 1 && state.view.x === 0 && state.view.y === 0) ? 1 : 0.18;
    state.view.k += (k - state.view.k) * e;
    state.view.x += (tx - state.view.x) * e;
    state.view.y += (ty - state.view.y) * e;
  }

  function draw() {
    const w = canvas.clientWidth, h = canvas.clientHeight;
    ctx.clearRect(0, 0, w, h);
    const neighbours = new Set();
    if (state.selected) {
      for (const l of state.data.links) {
        if (l.source === state.selected) neighbours.add(l.target);
        if (l.target === state.selected) neighbours.add(l.source);
      }
    }
    ctx.lineWidth = 1;
    for (const l of state.data.links) {
      const a = state.pos.get(l.source), b = state.pos.get(l.target);
      if (!a || !b) continue;
      const sa = toScreen(a), sb = toScreen(b);
      const lit = state.selected && (l.source === state.selected || l.target === state.selected);
      ctx.strokeStyle = lit ? THEME.edgeLit : THEME.edge;
      ctx.lineWidth = lit ? 1.8 : 1;
      ctx.globalAlpha = !lit && (state.fringe.has(l.source) || state.fringe.has(l.target)) ? 0.5 : 1;
      ctx.beginPath(); ctx.moveTo(sa.x, sa.y); ctx.lineTo(sb.x, sb.y); ctx.stroke();
    }
    ctx.globalAlpha = 1;
    for (const n of state.data.nodes) {
      const p = state.pos.get(n.id); if (!p) continue;
      // never smaller than a dot, however far out the view is zoomed
      const s = toScreen(p), r = Math.max(1.5, radius(n) * state.view.k);
      const dim = !matches(n) || (state.selected && n.id !== state.selected && !neighbours.has(n.id));
      ctx.globalAlpha = dim ? 0.22 : 1;
      ctx.fillStyle = nodeColor(n);
      ctx.beginPath(); ctx.arc(s.x, s.y, r, 0, Math.PI * 2); ctx.fill();
      if (n.id === state.selected) {
        ctx.strokeStyle = THEME.ring; ctx.lineWidth = 2; ctx.stroke();
      }
      ctx.globalAlpha = 1;
    }
    // a search that matches nothing leaves an empty canvas: say so
    if ((state.query || state.focus) && !state.data.nodes.length && state.all.nodes.length) {
      ctx.font = LABEL_FONT; ctx.textAlign = "center"; ctx.fillStyle = THEME.muted;
      ctx.fillText("No pages match", (state.fitLeft || 0) + (w - (state.fitLeft || 0) - (state.fitRight || 0)) / 2, h / 2);
    }
    const names = folderNames();
    drawLabels(neighbours, names.map((f) => f.box));
    drawFolderNames(names);
  }

  // By folder and Circle name each folder's group: the palette has ten
  // colours, so past ten folders colour alone cannot tell groups apart.
  // Pages at the top level get no name. Worked out before the page titles
  // are placed, so a title that would print over a folder name is skipped.
  function folderNames() {
    if (state.layout !== "folders" && state.layout !== "circle") return [];
    const groups = new Map();
    let cx = 0, cy = 0, count = 0;
    for (const n of state.data.nodes) {
      const p = state.pos.get(n.id); if (!p) continue;
      cx += p.x; cy += p.y; count++;
      const f = n.folder || "";
      let g = groups.get(f);
      if (!g) groups.set(f, (g = { x: 0, y: 0, n: 0, top: Infinity }));
      g.x += p.x; g.y += p.y; g.n++; g.top = Math.min(g.top, p.y - radius(n));
    }
    if (!count) return [];
    cx /= count; cy /= count;
    let ring = 0;
    for (const n of state.data.nodes) {
      const p = state.pos.get(n.id);
      if (p) ring = Math.max(ring, Math.hypot(p.x - cx, p.y - cy));
    }
    const c = toScreen({ x: cx, y: cy });
    ctx.font = FOLDER_FONT;
    const out = [];
    for (const [f, g] of groups) {
      if (!f) continue;
      const tw = ctx.measureText(f).width;
      let x, y, align = "center";
      if (state.layout === "folders") {
        const s = toScreen({ x: g.x / g.n, y: g.top });
        x = s.x; y = s.y - 8;
      } else {
        // outside the circle, level with the middle of the folder's arc
        const s = toScreen({ x: g.x / g.n, y: g.y / g.n });
        const dx = s.x - c.x, dy = s.y - c.y, d = Math.hypot(dx, dy) || 1;
        const r = ring * state.view.k + 30;
        x = c.x + (dx / d) * r; y = c.y + (dy / d) * r + 4;
        align = dx / d > 0.25 ? "left" : dx / d < -0.25 ? "right" : "center";
      }
      const x0 = align === "left" ? x : align === "right" ? x - tw : x - tw / 2;
      out.push({ f, x, y, align, box: { x0: x0 - 3, x1: x0 + tw + 3, y0: y - 15, y1: y + 5 } });
    }
    return out;
  }

  // Page titles match the folder tree's 13px; folder names stay a step
  // larger and bold. Were 11px and 12px until Forrest found them too small
  // (2026-09-28).
  const FOLDER_FONT = "600 14px -apple-system, Segoe UI, Roboto, sans-serif";
  const LABEL_FONT = "13px -apple-system, Segoe UI, Roboto, sans-serif";

  function drawFolderNames(names) {
    if (!names.length) return;
    ctx.font = FOLDER_FONT;
    ctx.lineJoin = "round"; ctx.lineWidth = 4; ctx.strokeStyle = THEME.bg; ctx.fillStyle = THEME.muted;
    for (const { f, x, y, align } of names) {
      const dim = (state.hoverFolder != null && !inFolder({ folder: f }, state.hoverFolder)) ||
                  (state.focus != null && !inFolder({ folder: f }, state.focus));
      ctx.globalAlpha = dim ? 0.3 : 0.9;
      ctx.textAlign = align;
      ctx.strokeText(f, x, y); ctx.fillText(f, x, y);
    }
    ctx.globalAlpha = 1; ctx.textAlign = "center";
  }

  // Labels are placed greedily in priority order and skipped where they would
  // overlap one already drawn. Without this a dense wiki renders as a smear of
  // overprinted titles. Selected page first, then its neighbours, the hovered
  // page, then everything else by degree, so hubs keep their names.
  function drawLabels(neighbours, reserved) {
    const nodes = state.data.nodes;
    const prio = (n) => (n.id === state.selected ? 3e6 : 0) + (neighbours.has(n.id) ? 2e6 : 0)
                      + (n.id === state.hover ? 1e6 : 0) + deg(n);
    const order = nodes.filter((n) => state.view.k > 0.55 || deg(n) > 3 ||
                                      n.id === state.selected || n.id === state.hover ||
                                      neighbours.has(n.id))
                       .sort((a, b) => prio(b) - prio(a));
    ctx.font = LABEL_FONT;
    ctx.textAlign = "center";
    const placed = [...(reserved || [])];
    for (const n of order) {
      const p = state.pos.get(n.id); if (!p) continue;
      const s = toScreen(p), r = radius(n) * state.view.k;
      const text = n.title || n.id;
      const tw = ctx.measureText(text).width;
      const box = { x0: s.x - tw / 2 - 2, x1: s.x + tw / 2 + 2, y0: s.y + r + 1, y1: s.y + r + 19 };
      const must = n.id === state.selected || n.id === state.hover;
      if (!must && placed.some((b) => box.x0 < b.x1 && box.x1 > b.x0 && box.y0 < b.y1 && box.y1 > b.y0)) {
        continue;
      }
      placed.push(box);
      const dim = !matches(n) || (state.selected && n.id !== state.selected && !neighbours.has(n.id));
      ctx.globalAlpha = dim ? 0.25 : 0.95;
      ctx.fillStyle = THEME.text;
      ctx.fillText(text, s.x, s.y + r + 14);
    }
    ctx.globalAlpha = 1;
  }

  function tick(now) {
    let landed = false;
    if (state.glide) landed = glideStep(now);
    else if (simulated()) step();
    fitView(landed); draw();
    cancelAnimationFrame(state.raf);
    // (a search that shows nothing leaves nothing to settle)
    if (state.glide || state.drag || (simulated() && state.data.nodes.length && state.alpha > alphaMin())) {
      state.raf = requestAnimationFrame(tick);
    }
  }

  function nodeAt(mx, my) {
    for (let i = state.data.nodes.length - 1; i >= 0; i--) {
      const n = state.data.nodes[i], p = state.pos.get(n.id);
      if (!p) continue;
      const s = toScreen(p), r = radius(n) * state.view.k + 3;
      if ((mx - s.x) ** 2 + (my - s.y) ** 2 <= r * r) return n;
    }
    return null;
  }

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function unescapeHtml(s) {
    return String(s).replace(/&(amp|lt|gt|quot|#39);/g, (_m, c) => (
      { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" }[c]));
  }


  // ---- markdown ----------------------------------------------------------
  // The panel used to dump raw source into a <pre>, so [[wikilinks]], headings
  // and bold all showed as literal text. This is a deliberately small renderer:
  // escape first, then apply markup, so page content can never inject HTML.

  function stripFrontMatter(src) {
    return src.replace(/^---\r?\n[\s\S]*?\r?\n---\r?\n?/, "");
  }

  // A heading's anchor, from its markdown (the text after the #s). The same
  // rule as parse.heading_slug on the server, which hands agents addresses
  // like /w/k7mq2xfp/some/page#a-section, so both sides must agree: links read
  // as their label, and anything that is not a letter or digit becomes a dash.
  function headingSlug(raw) {
    const s = String(raw).replace(/(?:^|[ \t]+)#+[ \t]*$/, "")
      .replace(/!\[([^\]]*)\]\([^)]*\)/g, "$1")
      .replace(/!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, t, a) => a || t)
      .replace(/\[([^\]]+)\]\([^)\s]+\)/g, "$1");
    return s.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-").replace(/^-+|-+$/g, "") || "section";
  }

  // Mirrors the resolution order in parse.py: exact path, then relative to the
  // linking page, then a unique filename match.
  function resolveWikiLink(target, from) {
    const t = String(target).split("#")[0].trim().replace(/\.md$/, "");
    if (!t) return null;
    if (state.byId.has(t)) return t;
    if (from && from.includes("/")) {
      const rel = from.slice(0, from.lastIndexOf("/") + 1) + t;
      if (state.byId.has(rel)) return rel;
    }
    const base = t.split("/").pop();
    const hits = [];
    for (const id of state.byId.keys()) {
      if (id.split("/").pop() === base) hits.push(id);
    }
    return hits.length === 1 ? hits[0] : null;
  }

  // A link or image pointing at one of the wiki's files (raw/deck.pdf,
  // images/arch.png): resolved like a page link, against the file paths the
  // graph API lists, and served through the app, which redirects to a
  // short-lived URL on another origin.
  function resolveFile(target, from) {
    if (!state.files || !state.files.size) return null;
    let t = String(target).split("#")[0].replace(/^\.\//, "").replace(/^\/+/, "");
    try { t = decodeURIComponent(t); } catch (e) { /* keep as written */ }
    if (state.files.has(t)) return t;
    if (from && from.includes("/")) {
      const parts = from.split("/").slice(0, -1);
      for (const seg of t.split("/")) {
        if (seg === "..") parts.pop(); else if (seg !== ".") parts.push(seg);
      }
      const rel = parts.join("/");
      if (state.files.has(rel)) return rel;
    }
    const base = t.split("/").pop();
    const hits = [...state.files].filter((p) => p.split("/").pop() === base);
    return hits.length === 1 ? hits[0] : null;
  }

  function fileUrl(path) {
    return withW(`${window.DEXIO_API}/files?path=${encodeURIComponent(path)}`);
  }

  // Every request names the workspace it is about (?w=<handle>). The server
  // used to fall back on a cookie holding the last workspace opened, which a
  // second tab could change under the first; and someone viewing what another
  // workspace shares with them (shares.py) has no such cookie for it at all.
  function withW(url) {
    const w = window.DEXIO_WORKSPACE;
    if (!w || !window.DEXIO_API) return url;
    return url + (url.includes("?") ? "&" : "?") + "w=" + encodeURIComponent(w);
  }

  // Someone viewing what a workspace shares (shares.py), not a member.
  const GUEST = !!window.DEXIO_GUEST;

  function imageTag(path, alt) {
    return '<img class="md-img" loading="lazy" src="' + escapeHtml(fileUrl(path)) +
      '" alt="' + alt + '">';
  }

  // A link's page and section: [[page#Heading]], [text](page.md#heading), and
  // [[#Heading]] or [text](#heading) for a section of the page itself (`from`).
  // The section is the heading's anchor, however the link wrote it.
  function splitAnchor(target, from) {
    const t = String(target), at = t.indexOf("#");
    if (at < 0) return [null, ""];
    const page = t.slice(0, at).trim();
    let sec = t.slice(at + 1).trim();
    try { sec = decodeURIComponent(sec); } catch (e) { /* keep as written */ }
    return [page || from, sec ? headingSlug(sec) : ""];
  }

  function secAttr(sec) { return sec ? ' data-sec="' + escapeHtml(sec) + '"' : ""; }

  const CODE_TOKEN = "CODE";

  function titleOf(id) {
    const n = state.byId.get(id);
    return (n && n.title) || id;
  }

  // ---- footnotes ----
  // Citations (Forrest, 2026-10-01). "[^label]" after a claim and a line
  // "[^label]: the source" anywhere in the page, the syntax GitHub, Obsidian
  // and Pandoc share, so it is what agents already write. The reference reads
  // as a bracketed number, [1], and the notes are listed at the end of the
  // page in the order they are first cited, each with a link back to where it
  // was cited. A note no text cites is still listed, after the rest, rather
  // than dropped; a reference to a note the page does not have stays as
  // written. Labels match regardless of case, as GitHub's do.
  const FN_DEF = /^ {0,3}\[\^([^\]\s]+)\]:[ \t]?(.*)$/;
  const FN_REF = /\[\^([^\]\s]+)\]/g;
  const FN_BLOCK = /^\s*(#{1,6}\s|>|[-*+•]\s|\d+[.)]\s|```)|^\s*(-{3,}|\*{3,}|_{3,})\s*$/;

  // The notes of the page being rendered: renderMarkdown sets it, inline
  // numbers the references as it meets them.
  let notes = null;

  // Take the "[^label]: note" lines out of the page, outside fenced code. A
  // note goes on over the lines after it, as a paragraph does, and over
  // lines indented four spaces (or a tab) after a blank line: its further
  // paragraphs. Each note leaves a blank line behind, so the text either side
  // of it stays two paragraphs.
  function takeNotes(lines) {
    const body = [], defs = [];
    let fenced = false;
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i];
      if (!fenced && /^\s*```(\w*)\s*$/.test(line)) { fenced = true; body.push(line); continue; }
      if (fenced) { body.push(line); if (/^\s*```\s*$/.test(line)) fenced = false; continue; }
      const m = line.match(FN_DEF);
      if (!m) { body.push(line); continue; }
      const paras = [[m[2]]];
      let j = i + 1;
      while (j < lines.length) {
        const l = lines[j];
        if (/^\s*$/.test(l)) {
          let k = j;
          while (k < lines.length && /^\s*$/.test(lines[k])) k++;
          if (k < lines.length && /^(?: {4}|\t)/.test(lines[k])) {
            paras.push([lines[k].trim()]);
            j = k + 1;
            continue;
          }
          break;
        }
        if (FN_DEF.test(l) || (!/^(?: {4}|\t)/.test(l) && FN_BLOCK.test(l))) break;
        paras[paras.length - 1].push(l.trim());
        j++;
      }
      defs.push({ label: m[1].toLowerCase(), paras: paras.map((p) => p.join(" ").trim()) });
      body.push("");
      i = j - 1;
    }
    return { body, defs };
  }

  // A note as plain text, for the tooltip on its reference.
  function notePlain(paras) {
    const s = paras.join(" ")
      .replace(/!?\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, t, a) => a || t)
      .replace(/!?\[([^\]]*)\]\(([^)\s]+)\)/g, (_m, t, u) => (t && t !== u ? t + " (" + u + ")" : u))
      .replace(FN_REF, "").replace(/\*\*|__|`/g, "").replace(/\s+/g, " ").trim();
    return s.length > 300 ? s.slice(0, 299).trimEnd() + "…" : s;
  }

  // "[^label]" in a line's (escaped) text, as the note's number. The markup
  // is held aside, as code spans are, and put in last: the note's text in its
  // tooltip must not be read as bold, a link or an address.
  const NOTE_TOKEN = "NOTE";
  function noteRefs(s, held) {
    if (!notes) return s;
    return s.replace(FN_REF, (m, raw) => {
      const label = unescapeHtml(raw).toLowerCase();
      const def = notes.byLabel.get(label);
      if (!def) return m;
      if (!def.n) { def.n = notes.order.length + 1; notes.order.push(def); }
      def.refs++;
      const ref = "fnref-" + def.n + (def.refs > 1 ? "-" + def.refs : "");
      held.push('<sup class="fn-ref" id="' + ref + '"><a href="#fn-' + def.n + '" data-fn="fn-' +
        def.n + '" title="' + escapeHtml(notePlain(def.paras)) + '">' + def.n + "</a></sup>");
      return NOTE_TOKEN + (held.length - 1) + "";
    });
  }

  // The list at the end of the page. Rendering a note can cite another
  // (a note that says "see [^2]"), which joins the list as it is met.
  function renderNotes(from) {
    const items = [];
    let k = 0;
    const flush = () => { while (k < notes.order.length) items.push(noteItem(notes.order[k++], from)); };
    flush();
    for (const def of notes.defs) {
      if (def.n || def.source) continue;   // an uncited source is listed under Sources
      def.n = notes.order.length + 1;
      notes.order.push(def);
      flush();
    }
    return items.length ? '<section class="footnotes" aria-label="Notes"><ol>' +
      items.join("") + "</ol></section>" : "";
  }

  function noteItem(def, from) {
    const backs = [];
    for (let r = 1; r <= def.refs; r++) {
      const ref = "fnref-" + def.n + (r > 1 ? "-" + r : "");
      backs.push('<a href="#' + ref + '" class="fn-back" data-fn="' + ref + '" aria-label="Back to ' +
        "where note " + def.n + " is cited" + (def.refs > 1 ? " (" + r + ")" : "") + '">↩' +
        (r > 1 ? "<sup>" + r + "</sup>" : "") + "</a>");
    }
    const back = backs.length ? " " + backs.join(" ") : "";
    const paras = def.paras.map((p) => inline(p, from));
    const body = paras.length === 1 ? paras[0] + back
      : paras.map((p, k) => "<p>" + p + (k === paras.length - 1 ? back : "") + "</p>").join("");
    return '<li id="fn-' + def.n + '">' + body + "</li>";
  }

  // Outside links get no search ranking from a shared page (see inline).
  function outRel() {
    return GUEST ? "ugc nofollow noopener noreferrer" : "noopener noreferrer";
  }

  // A bare https:// address in the text is a link, as GitHub makes it: a
  // note is often nothing but its source's address. Only text outside the
  // links the markup made is looked at; "<https://...>" loses its brackets.
  // Punctuation that ends the sentence, and a closing bracket the address
  // did not open, stay outside the link.
  function autolink(s) {
    let inLink = 0;
    return s.split(/(<[^>]+>)/).map((part) => {
      if (part[0] === "<") {
        if (/^<a[\s>]/i.test(part)) inLink++;
        else if (/^<\/a>/i.test(part)) inLink = Math.max(0, inLink - 1);
        return part;
      }
      if (inLink || !/https?:\/\//i.test(part)) return part;
      return part.replace(/&lt;(https?:\/\/[^\s<]+?)&gt;|https?:\/\/(?:(?!&lt;|&gt;|&quot;)[^\s<])+/gi,
        (m, angled) => {
          let url = angled || m, tail = "";
          if (!angled) {
            for (;;) {
              const p = /(?:&#39;|[.,:;!?*_~'])$/.exec(url);
              if (p) { tail = p[0] + tail; url = url.slice(0, -p[0].length); continue; }
              if (url.endsWith(")") &&
                  (url.match(/\(/g) || []).length < (url.match(/\)/g) || []).length) {
                tail = ")" + tail; url = url.slice(0, -1); continue;
              }
              break;
            }
          }
          if (/^https?:\/\/$/i.test(url)) return m;
          return '<a href="' + url + '" target="_blank" rel="' + outRel() + '">' + url + "</a>" + tail;
        });
    }).join("");
  }

  function inline(text, from) {
    // Code spans are pulled out first so their contents are never treated as
    // markup, then restored at the end.
    const spans = [];
    let s = String(text).replace(/`([^`]+)`/g, (_m, code) => {
      spans.push(code);
      return CODE_TOKEN + (spans.length - 1) + "";
    });

    s = escapeHtml(s);

    const held = [];
    s = noteRefs(s, held);

    // ![[image.png]] (the Obsidian embed) and ![alt](images/a.png): the wiki's
    // own images. Anything else stays as written.
    s = s.replace(/!\[\[([^\]|]+)(?:\|[^\]]*)?\]\]/g, (m, target) => {
      const f = resolveFile(target, from);
      return f ? imageTag(f, escapeHtml(target)) : m;
    });
    s = s.replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (m, alt, src) => {
      const f = resolveFile(src.replace(/&amp;/g, "&"), from);
      return f ? imageTag(f, alt) : m;
    });

    // [[wikilink]] and [[wikilink|alias]]. Without an alias the link reads as
    // the page's title, not its path: "Forrest Zhang", not "entities/forrest-zhang".
    s = s.replace(/\[\[([^\]|]+)(?:\|([^\]]+))?\]\]/g, (_m, target, alias) => {
      const file = resolveFile(target, from);
      if (file) {
        return '<a href="' + escapeHtml(fileUrl(file)) + '" target="_blank" rel="noopener">' +
          escapeHtml(alias || target) + '</a>';
      }
      // `target` and `alias` are escaped already (s was escaped above)
      const raw = unescapeHtml(target);
      const [page, sec] = splitAnchor(raw, from);
      const id = page === from && sec ? from : resolveWikiLink(target, from);
      // [[page#Heading]] reads "Page title › Heading"; [[#Heading]] reads "Heading"
      const heading = sec ? raw.slice(raw.indexOf("#") + 1).trim() : "";
      const label = alias ? alias : !id ? escapeHtml(target) : !sec ? escapeHtml(titleOf(id))
        : escapeHtml(raw.trim().startsWith("#") ? heading : titleOf(id) + " › " + heading);
      if (id) {
        return '<a href="#" class="wl" data-id="' + escapeHtml(id) + '"' + secAttr(sec) + '>' +
          label + '</a>';
      }
      // A guest sees only what was shared, so a link to anything else is
      // plain text: not marked broken, and not naming what it points at.
      // Without an alias it reads as the page's name, "Roast schedule" for
      // ops/roast-schedule, as a member's link reads as the page's title.
      if (GUEST) {
        const name = unescapeHtml(page || raw).split("/").pop().replace(/\.md$/, "")
          .replace(/[-_]+/g, " ").trim();
        return '<span class="wl-hidden">' + (alias ? alias : escapeHtml(
          name ? name[0].toUpperCase() + name.slice(1) : raw)) + '</span>';
      }
      return '<span class="wl-missing" title="no page called ' + escapeHtml(target)
           + '">' + label + '</span>';
    });

    // [text](url): external opens in a tab, internal resolves like a wikilink
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_m, label, href) => {
      if (/^https?:\/\//i.test(href)) {
        // On a page a workspace shares, an outside link passes no search
        // ranking (ugc nofollow): public pages are indexed, and a spam page must
        // not borrow our domain's standing.
        return '<a href="' + escapeHtml(href) + '" target="_blank" rel="' +
          (GUEST ? "ugc nofollow noopener noreferrer" : "noopener noreferrer") + '">' + label + '</a>';
      }
      const file = resolveFile(href.replace(/&amp;/g, "&"), from);
      if (file) {
        return '<a href="' + escapeHtml(fileUrl(file)) + '" target="_blank" rel="noopener">' +
          label + '</a>';
      }
      const plain = unescapeHtml(href).replace(/^\.\//, "");
      const [page, sec] = splitAnchor(plain, from);
      const id = page === from && sec ? from : resolveWikiLink(plain, from);
      return id ? '<a href="#" class="wl" data-id="' + escapeHtml(id) + '"' + secAttr(sec) + '>' +
                  label + '</a>'
                : '<span class="' + (GUEST ? "wl-hidden" : "wl-missing") + '">' + label + '</span>';
    });

    s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/~~([^~]+)~~/g, "<del>$1</del>");

    s = autolink(s).replace(/NOTE(\d+)/g, (_m, i) => held[+i]);

    return s.replace(/CODE(\d+)/g,
                     (_m, i) => "<code>" + escapeHtml(spans[+i]) + "</code>");
  }

  // Lines that end a list item's text: blank, a heading, quote, fence, rule,
  // or the next item. "•" counts as a bullet beside - * +: agent-written
  // wikis use it (the Hermes fleet wiki does throughout), and without it each
  // bulleted section ran together into one paragraph.
  const ITEM_END = /^\s*$|^\s*(#{1,6}\s|>|[-*+•]\s|\d+[.)]\s|```)|^\s*(-{3,}|\*{3,}|_{3,})\s*$/;

  // List items hold blocks, as in CommonMark (Forrest, 2026-10-01: a guide's code
  // blocks and sub-bullets sat between its numbered steps instead of under them).
  // An item's content starts where its text does, after the marker and its spaces;
  // a following line indented that far or more belongs to the item (paragraphs,
  // code, a nested list, a quote, a table), and so does a blank line followed by
  // one. An unindented line straight after the item's text continues that text
  // (CommonMark's lazy continuation) unless it starts a block of its own.
  const LIST_ITEM = /^([ \t]*)([-*+•]|\d{1,9}[.)])([ \t]+)(.*)$/;

  // Width of a line's leading whitespace, a tab reaching the next multiple of 4.
  function leadWidth(s) {
    let w = 0;
    for (const ch of s) {
      if (ch === " ") w++;
      else if (ch === "\t") w += 4 - (w % 4);
      else break;
    }
    return w;
  }

  // The line with up to `n` columns of leading whitespace taken off.
  function dedent(s, n) {
    let w = 0, k = 0;
    while (k < s.length && w < n && (s[k] === " " || s[k] === "\t")) {
      w += s[k] === "\t" ? 4 - (w % 4) : 1;
      k++;
    }
    return (w > n ? " ".repeat(w - n) : "") + s.slice(k);
  }

  function listItemAt(line) {
    const m = line.match(LIST_ITEM);
    if (!m) return null;
    const ind = leadWidth(m[1]);
    // The spaces after the marker, counted from the column they start at.
    let gap = 0;
    for (const ch of m[3]) {
      const at = ind + m[2].length + gap;
      gap += ch === "\t" ? 4 - (at % 4) : 1;
    }
    const ordered = /^\d/.test(m[2]);
    return { ordered, start: ordered ? parseInt(m[2], 10) : 1, text: m[4],
             // Five or more spaces after the marker would make indented code in
             // CommonMark; the content then starts one space after the marker.
             col: ind + m[2].length + (gap <= 4 ? gap : 1) };
  }

  // The lines of the item starting at lines[i], dedented to its content column,
  // and the index of the first line after it.
  function listItemBody(lines, i, item) {
    const body = [item.text];
    let j = i + 1, inFence = /^\s*```(\w*)\s*$/.test(item.text), last = "text";
    while (j < lines.length) {
      const line = lines[j];
      if (inFence) {
        body.push(dedent(line, item.col));
        if (/^\s*```\s*$/.test(line)) { inFence = false; last = "fence"; }
        j++; continue;
      }
      if (/^\s*$/.test(line)) {
        let k = j + 1;
        while (k < lines.length && /^\s*$/.test(lines[k])) k++;
        if (k >= lines.length || leadWidth(lines[k]) < item.col) break;
        while (j < k) { body.push(""); j++; }
        last = "blank";
        continue;
      }
      if (leadWidth(line) >= item.col) {
        const d = dedent(line, item.col);
        body.push(d);
        if (/^\s*```(\w*)\s*$/.test(d)) inFence = true;
        last = "text";
        j++; continue;
      }
      if (last === "text" && !ITEM_END.test(line) && !tableAt(lines, j)) {
        body.push(line.trim());
        j++; continue;
      }
      break;
    }
    return { body, next: j };
  }

  // GFM tables. There was no table rule at all, so every row fell through to
  // the paragraph branch and a table read as one long line of pipes. A table
  // is a header row, a delimiter row (| --- | :-: |) with the same number of
  // cells, then body rows up to a blank line or a line with no pipe.
  //
  // Cells split on unescaped pipes. A pipe inside a code span or inside
  // [[wikilink|alias]] brackets stays in its cell, and "\|" is a literal pipe,
  // so [[page\|alias]] (the Obsidian form) works as well.
  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith("|")) s = s.slice(1);
    if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
    const cells = [];
    let cur = "", code = false, link = false;
    for (let k = 0; k < s.length; k++) {
      const c = s[k];
      if (c === "\\" && s[k + 1] === "|") { cur += "|"; k++; continue; }
      if (c === "`" && (code || s.indexOf("`", k + 1) !== -1)) code = !code;
      else if (!code && !link && s.startsWith("[[", k) && s.indexOf("]]", k + 2) !== -1) {
        link = true; cur += "[["; k++; continue;
      } else if (link && s.startsWith("]]", k)) { link = false; cur += "]]"; k++; continue; }
      else if (c === "|" && !code && !link) { cells.push(cur.trim()); cur = ""; continue; }
      cur += c;
    }
    cells.push(cur.trim());
    return cells;
  }

  const DELIM_CELL = /^:?-+:?$/;

  // The table starting at line i, or null when there is none.
  function tableAt(lines, i) {
    const head = lines[i], delim = lines[i + 1];
    if (delim === undefined || !head.includes("|") || !delim.includes("|")) return null;
    const d = splitRow(delim);
    if (!d.every((c) => DELIM_CELL.test(c))) return null;
    const h = splitRow(head);
    if (h.length !== d.length) return null;
    const align = d.map((c) => c.endsWith(":") ? (c.startsWith(":") ? "center" : "right")
                                               : (c.startsWith(":") ? "left" : ""));
    return { head: h, align };
  }

  function renderTable(t, rows, from) {
    const row = (cells, tag) => "<tr>" + t.align.map((a, k) =>
      "<" + tag + (a ? ' style="text-align:' + a + '"' : "") + ">" +
      inline(cells[k] || "", from) + "</" + tag + ">").join("") + "</tr>";
    return '<div class="table-wrap"><table><thead>' + row(t.head, "th") + "</thead>" +
      (rows.length ? "<tbody>" + rows.map((r) => row(r, "td")).join("") + "</tbody>" : "") +
      "</table></div>";
  }

  // HTML comments are invisible in markdown, but this renderer escapes all
  // HTML, so they showed as literal "<!-- ... -->" text (the wiki index keeps
  // its format notes in them). Drop them outside fenced blocks and code
  // spans, the same way parse.py ignores them when it collects links. A
  // comment's newlines are kept, so a comment on its own line still ends the
  // paragraph or list before it.
  function stripComments(src) {
    const out = [];
    let chunk = [], fenced = false;
    const flush = () => {
      if (!chunk.length) return;
      out.push(chunk.join("\n").replace(/`[^`\n]*`|<!--[\s\S]*?-->/g,
        (m) => (m[0] === "`" ? m : m.replace(/[^\n]/g, ""))));
      chunk = [];
    };
    for (const line of src.split(/\r?\n/)) {
      if (!fenced && /^\s*```(\w*)\s*$/.test(line)) { flush(); fenced = true; out.push(line); }
      else if (fenced) { out.push(line); if (/^\s*```\s*$/.test(line)) fenced = false; }
      else chunk.push(line);
    }
    flush();
    return out.join("\n");
  }

  // The panel's header already shows the page's title, which is its first H1.
  // When the page opens with that H1, drop it so the title is not shown twice.
  function dropTitle(lines, title) {
    if (!title) return lines;
    const i = lines.findIndex((l) => !/^\s*$/.test(l));
    const h = i >= 0 && lines[i].match(/^#\s+(.*?)\s*#*\s*$/);
    return h && h[1].trim() === String(title).trim() ? lines.slice(i + 1) : lines;
  }

  // sources: the page's frontmatter sources (okf.py), when the server sent
  // them. A footnote labelled with a source's id cites it, as the Open
  // Knowledge Format has it (Forrest, 2026-10-04): with no "[^id]: ..." line
  // of the page's own, the note is the source. `cited` collects the ids cited.
  function renderMarkdown(src, from, title, sources, cited) {
    const taken = takeNotes(dropTitle(
      stripComments(stripFrontMatter(String(src || ""))).split(/\r?\n/), title));
    const byLabel = new Map();
    for (const def of taken.defs) {
      def.n = 0; def.refs = 0;
      if (!byLabel.has(def.label)) byLabel.set(def.label, def);   // the first wins
    }
    for (const s of sources || []) {
      const label = s.id ? String(s.id).toLowerCase() : "";
      if (!label || byLabel.has(label)) continue;               // the page's own note wins
      const def = { label, paras: [sourceMd(s)], n: 0, refs: 0, source: s };
      byLabel.set(label, def);
      taken.defs.push(def);
    }
    const outer = notes;
    notes = { defs: taken.defs, byLabel, order: [] };
    try {
      const body = renderBlocks(taken.body, from), list = renderNotes(from);
      if (cited) for (const def of taken.defs) if (def.source && def.refs) cited.add(def.label);
      return list ? body + "\n" + list : body;
    } finally {
      notes = outer;
    }
  }

  // ---- what a page says about itself (okf.py) ----
  // The Open Knowledge Format's status, sources and review in the page's
  // frontmatter, which the server reads (Forrest, 2026-10-04: "Let's do 1-3").
  // A source as one line of markdown: its title linked to it when it is an
  // address or a path, then who wrote it and when it last changed.
  function sourceMd(s) {
    if (s.text) return String(s.text);
    const res = String(s.resource || "");
    const label = String(s.title || res).replace(/[[\]]/g, "");
    const linkable = /^https?:\/\//i.test(res) || (!/\s/.test(res) && /[/.]/.test(res));
    const href = res.replace(/[()\s]/g, (c) => ({ "(": "%28", ")": "%29" })[c] || "%20");
    let md = linkable ? `[${label}](${href})` : label;
    if (s.author) md += ` · ${s.author}`;
    const t = s.last_modified ? Date.parse(s.last_modified) : NaN;
    if (!isNaN(t)) md += ` · updated ${dayOf(t / 1000)}`;
    return md;
  }

  // The sources no footnote cites, after the text, as "Linked from" is.
  function sourcesBlock(sources, cited, from) {
    const rest = (sources || []).filter((s) => !(s.id && cited.has(String(s.id).toLowerCase())));
    if (!rest.length) return "";
    return `<section class="links backlinks page-sources" aria-label="Sources"><b>Sources</b>` +
      rest.map((s) => `<div class="src">${inline(sourceMd(s), from)}</div>`).join("") + `</section>`;
  }

  // The open page's okf fields, once its text has come (null before, and in
  // the static viewer, which has no server to read them).
  function pageMeta(n) {
    return (n && hist.n && hist.n.id === n.id && hist.info && hist.info.okf) || null;
  }

  // used: heading anchors so far, shared with the blocks inside list items;
  // repeats get -2, -3.
  function renderBlocks(lines, from, used = new Set()) {
    const out = [];
    let i = 0, list = null;

    const closeList = () => { if (list) { out.push("</" + list + ">"); list = null; } };

    while (i < lines.length) {
      const line = lines[i];

      const fence = line.match(/^\s*```(\w*)\s*$/);
      if (fence) {
        closeList();
        const buf = [];
        i++;
        while (i < lines.length && !/^\s*```\s*$/.test(lines[i])) buf.push(lines[i++]);
        i++;
        out.push("<pre><code>" + escapeHtml(buf.join("\n")) + "</code></pre>");
        continue;
      }

      if (/^\s*$/.test(line)) {
        // A blank line between two items of the same list does not end the list,
        // as in CommonMark: "1. a", a blank line, then "1. b" counts 1, 2, and
        // steps written with blank lines between them stay one list (2026-10-01).
        let j = i + 1;
        while (j < lines.length && /^\s*$/.test(lines[j])) j++;
        const next = j < lines.length ? lines[j] : "";
        const same = list === "ol" ? /^\s*\d+[.)]\s+/.test(next)
                   : list === "ul" ? /^\s*[-*+•]\s+/.test(next) : false;
        if (!same) closeList();
        i = j;
        continue;
      }

      if (/^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
        closeList(); out.push("<hr>"); i++; continue;
      }

      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) {
        closeList();
        const lvl = Math.min(6, h[1].length + 1);   // the page title is already an h2
        const base = headingSlug(h[2]);
        let slug = base, k = 2;
        while (used.has(slug)) slug = base + "-" + k++;
        used.add(slug);
        // "s-": page text must never take an id the app's own elements use
        out.push("<h" + lvl + ' id="s-' + escapeHtml(slug) + '">' + inline(h[2], from) +
                 "</h" + lvl + ">");
        i++; continue;
      }

      const quote = line.match(/^\s*>\s?(.*)$/);
      if (quote) {
        closeList();
        const buf = [quote[1]];
        i++;
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          buf.push(lines[i].replace(/^\s*>\s?/, "")); i++;
        }
        out.push("<blockquote>" + inline(buf.join(" "), from) + "</blockquote>");
        continue;
      }

      const item = listItemAt(line);
      if (item) {
        const want = item.ordered ? "ol" : "ul";
        if (list !== want) {
          closeList();
          // A numbered list counts from its first item's number, as in CommonMark.
          // Steps split by a code block or a paragraph start a new list each
          // time, and every one of them showed "1." (Forrest, 2026-10-01).
          out.push(item.ordered && item.start !== 1 ? '<ol start="' + item.start + '">'
                                                    : "<" + want + ">");
          list = want;
        }
        // The item's text, wrapped lines included, and every block under it.
        // Taking only the marker line once dropped the rest into a stray <p>.
        const { body, next } = listItemBody(lines, i, item);
        i = next;
        let inner = renderBlocks(body, from, used);
        // The item's first paragraph sits on the marker's line, unwrapped, so a
        // one-line item renders as it always has: <li>text</li>.
        if (inner.startsWith("<p>")) {
          const end = inner.indexOf("</p>");
          inner = inner.slice(3, end) + inner.slice(end + 4);
        }
        out.push("<li>" + inner + "</li>");
        continue;
      }

      closeList();
      const table = tableAt(lines, i);
      if (table) {
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].includes("|") && !/^\s*$/.test(lines[i])) {
          rows.push(splitRow(lines[i++]));
        }
        out.push(renderTable(table, rows, from));
        continue;
      }

      const buf = [line];
      i++;
      while (i < lines.length && !/^\s*$/.test(lines[i])
             && !/^\s*(#{1,6}\s|>|[-*+•]\s|\d+[.)]\s|```)/.test(lines[i])
             && !tableAt(lines, i)) {
        buf.push(lines[i]); i++;
      }
      out.push("<p>" + inline(buf.join(" "), from) + "</p>");
    }
    closeList();
    return out.join("\n");
  }

  // Pages that link here, under the page as a wiki's backlinks are, listed by
  // title. The pages this one links to are already links in its text.
  function backlinks(ins) {
    if (!ins.length) return "";
    const items = ins.map((id) => ({ id, title: titleOf(id) }))
      .sort((a, b) => a.title.localeCompare(b.title));
    return `<nav class="links backlinks" aria-label="Linked from"><b>Linked from</b>` +
      items.map((p) => `<a href="#" data-id="${escapeHtml(p.id)}" title="${escapeHtml(p.id)}">` +
        `${escapeHtml(p.title)}</a>`).join("") + `</nav>`;
  }

  // Opening a page used to draw nothing until its text had arrived: the panel
  // stayed shut, or went on showing the page before, for as long as the
  // fetch took (Forrest, 2026-09-26). It now opens at once with the title,
  // the meta line and a placeholder where the text goes, and the text takes
  // the placeholder's place when it lands. Only the page asked for last is
  // filled in: a slow one that arrives after another was opened is dropped.
  let opening = 0;
  // The open page, what the panel shows of it (view: page, history or
  // revision), and the path whose history is shown (a page's former path,
  // after a move, is not in the graph).
  // section: the anchor the page was opened at, if any.
  const hist = { n: null, ins: [], out: [], text: null, info: null, view: "page", path: null,
                  exists: true, people: false, lastDay: null, rev: null, section: "" };

  // ---- addresses ---------------------------------------------------------
  // In the app every page has an address of its own (Forrest, 2026-09-28:
  // "we need to support deep links"): /w/<workspace>/<page path>, with
  // #<anchor> for a section (headingSlug). A workspace has one wiki, so the
  // address names none (the same day; before, the wiki's name came after the
  // workspace, and the server still sends those addresses here). Opening a page
  // puts its address in the address bar and the browser's history, so Back
  // returns to the page before, a reload reopens it, and the address can be
  // pasted anywhere; the MCP tools hand agents the same addresses. Links,
  // backlinks and tree rows carry them too, so Cmd-click opens a page in a new
  // tab. The static viewer (an export, the demos on dexio.wiki) has no server to
  // answer them. render.py's APP_JS reads an address on load and on Back and
  // Forward.
  const ROUTED = !!(window.DEXIO_API && window.DEXIO_WORKSPACE != null &&
                    window.history && window.history.pushState);

  function wikiHref() {
    return "/w/" + encodeURIComponent(window.DEXIO_WORKSPACE) + focusQuery();
  }

  // "#" in the static viewer, where links only open pages in place.
  function pageHref(id, sec) {
    if (!ROUTED) return "#";
    return "/w/" + encodeURIComponent(window.DEXIO_WORKSPACE) + "/" +
      String(id).split("/").map(encodeURIComponent).join("/") + focusQuery() +
      (sec ? "#" + encodeURIComponent(sec) : "");
  }

  // The folder the graph is narrowed to rides along in every address,
  // ?folder=notes/deep, so Back, a reload and a pasted link keep it.
  function focusQuery() {
    return state.focus ? "?folder=" + state.focus.split("/").map(encodeURIComponent).join("/") : "";
  }

  // how: "push" (a new history entry), "replace", or "none" (already there).
  // page: the page the entry shows (null for none); left out, it stays as it
  // was. Each entry carries what the back line needs (see "going back").
  function setAddress(url, how, page) {
    if (!ROUTED || how === "none") return;
    if (url === location.pathname + location.search + location.hash) return;
    const cur = entryNow();
    if (how === "replace") {
      history.replaceState({ ...cur, page: page === undefined ? cur.page : page }, "", url);
      return;
    }
    saveTop();                      // the entry being left keeps where it was read
    history.pushState({ dexio: 1, page: page === undefined ? null : page,
                        trail: trailFrom(cur), top: 0 }, "", url);
  }

  // ---- going back ----------------------------------------------------------
  // Forrest, 2026-09-28: "when clicking from page to page in the wiki, we need
  // a way to go back" (option B of three). The page you came from is the
  // head's first row, "← Morning blend", in link colour: it says where Back
  // goes without a hover, keeps the title on the text's left edge, and reads
  // the same with the contents shown, folded into the button or on a phone.
  // It is only there when this tab reached the page from another page.
  //
  // In the app it is the browser's own history, so it always agrees with the
  // browser's Back, Cmd+[ in the desktop app, and a swipe. Every entry the app
  // makes carries in history.state the page it shows and its trail: the pages
  // open before it, newest first, with how many entries back each is. A step
  // where the page was closed is not on the trail, so open A, close it, open B
  // from the graph, and the line goes to A. The trail survives a reload; a
  // page opened from a pasted address, a bookmark or a new tab has none, since
  // Back there would leave the app. Back and Forward return to where you were
  // reading: the panel's scroll is kept in the entry as you read and put back
  // when you come back to it. A page opened any other way opens at its top.
  //
  // The static viewer (an export, the dexio.wiki demos) has no addresses, so
  // it keeps the pages you came through in a list of its own, `trail`.
  const TRAIL_MAX = 30;
  const ARROW_BACK = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 12H5M11 6l-6 6 6 6"></path></svg>';
  const trail = [];                 // static viewer: {id, top}, oldest first

  // The entry the browser is on, as the app writes them.
  function entryNow() {
    const s = ROUTED && history.state;
    return s && s.dexio ? s : { dexio: 1, page: null, trail: [], top: 0 };
  }

  // The trail of an entry pushed from `cur`: the page open now, then cur's
  // own trail, each a step further back.
  function trailFrom(cur) {
    const open = panel && panel.classList.contains("open") && state.selected;
    const t = (cur.trail || []).map((b) => ({ ...b, steps: b.steps + 1 }));
    if (open) t.unshift({ id: open, title: titleOf(open), steps: 1 });
    return t.slice(0, TRAIL_MAX);
  }

  // Keep the reading position in the entry, while its page is what shows.
  let topTimer = 0;
  function saveTop() {
    clearTimeout(topTimer);
    if (!ROUTED || !panel || !panel.classList.contains("open") || !state.selected) return;
    if (hist.view !== "page" || !history.state || !history.state.dexio) return;
    const cur = history.state, top = Math.round(panel.scrollTop);
    if (cur.page !== state.selected || cur.top === top) return;
    try { history.replaceState({ ...cur, top }, ""); } catch (e) { /* rate-limited: next time */ }
  }

  // Where the back line goes from page `id`: the newest page on the trail
  // that is not this one (open A, close it, open A again: not "← A").
  function backTo(id) {
    if (!ROUTED) {
      for (let i = trail.length - 1; i >= 0; i--) {
        if (trail[i].id !== id && state.byId.has(trail[i].id)) {
          return { id: trail[i].id, title: titleOf(trail[i].id), at: i };
        }
      }
      return null;
    }
    const cur = history.state;
    if (!cur || !cur.dexio || cur.page !== id) return null;
    const b = (cur.trail || []).find((x) => x.id !== id);
    if (!b) return null;
    return { id: b.id, title: state.byId.has(b.id) ? titleOf(b.id) : b.title, steps: b.steps };
  }

  function backRow(id) {
    const b = backTo(id);
    if (!b) return "";
    return `<div class="back-row"><a class="back-line" href="${escapeHtml(pageHref(b.id))}" ` +
      `title="Back to ${escapeHtml(b.title)}" aria-label="Back to ${escapeHtml(b.title)}">` +
      `${ARROW_BACK}<span>${escapeHtml(b.title)}</span></a></div>`;
  }

  // Put the back line in the open page's head, or take it out.
  function showBack(n) {
    const head = panel && panel.querySelector(".panel-head");
    if (!head || !n) return;
    const old = head.querySelector(".back-row");
    if (old) old.remove();
    const html = backRow(n.id);
    head.classList.toggle("has-back", !!html);
    if (html) head.insertAdjacentHTML("afterbegin", html);
    wireBack();
  }

  function wireBack() {
    const a = panel.querySelector(".back-line");
    if (!a) return;
    a.onclick = (e) => {
      if (newTab(e)) return;
      e.preventDefault();
      goBack();
    };
  }

  function goBack() {
    const b = hist.n && backTo(hist.n.id);
    if (!b) return;
    if (ROUTED) { saveTop(); history.go(-b.steps); return; }
    const to = trail[b.at];
    trail.length = b.at;
    select(state.byId.get(to.id), { back: true, top: to.top });
  }

  // Static viewer: note the page being left, as the app's history would.
  function leaving(n) {
    if (ROUTED || !panel || !panel.classList.contains("open") || !state.selected) return;
    if (n && n.id === state.selected) return;
    trail.push({ id: state.selected, top: Math.round(panel.scrollTop) });
    if (trail.length > TRAIL_MAX) trail.shift();
  }

  // A click that should open a new tab or window instead of the page in place.
  function newTab(e) {
    return ROUTED && !!(e.metaKey || e.ctrlKey || e.shiftKey || e.altKey || e.button);
  }

  // Scroll to a section of the open page by its anchor; false if it has none.
  function goSection(sec) {
    const el = sec && panel.querySelector("#" + CSS.escape("s-" + sec));
    if (!el) return false;
    jumpTo(el.id);
    return true;
  }

  // Open the page at `path`, at section `sec`, following the page to where it
  // was moved if it is no longer there (an address pasted before a move_page).
  // False when there is no such page. With no path, closes the open page.
  // top: the reading position to put back (Back and Forward, render.py).
  async function openPath(path, sec, how, top) {
    if (!path) {
      if (state.selected) select(null, { history: how });
      else setAddress(wikiHref(), how, null);
      return true;
    }
    let id = path;
    for (let hop = 0; hop < 5 && window.DEXIO_API && !state.byId.has(id); hop++) {
      const h = await api("page-history", { path: id, limit: 1 });
      const r = h && h.revisions && h.revisions[0];
      if (!r || !r.deleted || !r.moved_to) break;
      id = r.moved_to;
    }
    const n = state.byId.get(id);
    if (!n) return false;
    const moved = id !== path && how === "none" ? "replace" : how;
    if (state.selected === n.id && panel.classList.contains("open") && hist.view === "page") {
      setAddress(pageHref(n.id, sec), moved === "push" ? "replace" : moved, n.id);
      hist.section = sec || "";
      showBack(n);
      if (top) panel.scrollTop = top;
      else if (sec) goSection(sec);
      return true;
    }
    select(n, { history: moved, section: sec, top });
    return true;
  }

  // opts: history (how the address changes, "push" by default), section (the
  // anchor to open at), top (the reading position to put back, which wins
  // over the section), back (the static viewer's back line opened it).
  async function select(n, opts) {
    opts = opts || {};
    const ticket = ++opening;
    if (!opts.back) leaving(n);
    if (ROUTED && opts.history !== "none") {
      setAddress(n ? pageHref(n.id, opts.section) : wikiHref(), opts.history || "push", n ? n.id : null);
    }
    if (ROUTED) {
      // A shared page is titled with its wiki's name, as the server titles it.
      const site = GUEST ? window.DEXIO_GUEST.name : "Dexio";
      document.title = n ? `${n.title || n.id} · ${site}` : GUEST ? `${site} · Dexio` : "Dexio";
    }
    state.selected = n ? n.id : null;
    // Radial is centred on the open page: opening another glides it to the
    // middle, framed beside the panel it opens into. Closing leaves it put.
    // (a page a search hides, opened from a link, leaves the graph as it is)
    const recentre = n && state.layout === "radial" && n.id !== state.radialCentre && state.shownIds.has(n.id);
    if (recentre) {
      relayout(() => { state.fitRight = coveredRight(true); placeRadial(n.id); });
    }
    draw();
    showInTree(n);
    if (!n) { panel.classList.remove("open"); placeGrip(); shareHere(); return; }
    const out = [], ins = [];
    for (const l of state.all.links) {
      if (l.source === n.id) out.push(l.target);
      if (l.target === n.id) ins.push(l.source);
    }
    // The static viewer carries every page's text; the app fetches it.
    const fetched = !n.text && !!window.DEXIO_API;
    Object.assign(hist, { n, ins, out, text: fetched ? null : n.text || "", info: null,
                          view: "page", path: n.id, section: opts.section || "" });
    showPage(n, ins, out, fetched ? null : n.text || "");
    const top = opts.top || 0;
    if (!fetched) {
      if (top) panel.scrollTop = top;
      else if (hist.section) goSection(hist.section);
      return;
    }
    const note = await fetchNote(n);
    if (ticket !== opening) return;      // another page was opened, or it closed
    hist.text = note ? note.text : null;
    hist.info = note ? note.info : null;
    hist.version = note ? note.version : "";
    showWhen(hist.info);
    if (hist.view !== "page") return;
    fillPage(n, ins, hist.text);
    // Back and Forward put the reading position back; an address with a
    // section opens there. Both once the text is in.
    if (top && hist.text !== null) panel.scrollTop = top;
    else if (hist.section && panel.scrollTop === 0) goSection(hist.section);
  }

  // The page's text ("" for an empty page) and its dates, or null when it
  // could not be had.
  async function fetchNote(n) {
    try {
      const r = await fetch(withW(`${window.DEXIO_API}/note?project=${encodeURIComponent(
        window.DEXIO_PROJECT || "")}&path=${encodeURIComponent(n.id)}`));
      if (!r.ok) return null;
      const j = await r.json();
      return { text: j.text || "", info: j.info || null, version: j.version || "" };
    } catch (e) { return null; }
  }

  // Grey lines in the shape of a few paragraphs. shell.html fades them in
  // after a moment, so a page that arrives quickly never flashes them.
  const LOADING = `<div class="page-loading" role="status">` +
    `<span class="sk-label">Loading page</span>` +
    [96, 100, 91, 58, 0, 100, 94, 72].map((w) =>
      w ? `<span class="sk" style="width:${w}%"></span>` : `<span class="sk-gap"></span>`).join("") +
    `</div>`;

  function pageBody(n, body, ins) {
    const meta = pageMeta(n), cited = new Set();
    const sources = meta && meta.sources;
    const text = body ? renderMarkdown(body, n.id, n.title, sources, cited) : "";
    // Kept for links and history, no longer current: OKF's deprecated.
    const old = meta && meta.status === "deprecated"
      ? `<p class="page-notice" role="note">This page is deprecated. It is kept for its links and ` +
        `history and is no longer current.</p>` : "";
    return old + (text ? `<article class="md">${text}</article>` : "") +
      sourcesBlock(sources, cited, n.id) + backlinks(ins);
  }

  // body null: the text is on its way, so the placeholder stands in for it.
  function showPage(n, ins, out, body) {
    const loading = body === null;
    const back = backRow(n.id);
    // Page history is for members; a guest's page has no tabs (shares.py).
    const tabs = !!window.DEXIO_API && !GUEST;
    panel.innerHTML =
      `<nav class="toc toc-side" aria-label="Contents"></nav>` +
      `<div class="panel-main"${loading ? ' aria-busy="true"' : ""}>` +
      `<div class="panel-head${tabs ? " has-tabs" : ""}${back ? " has-back" : ""}">` +
      back +
      `<button class="toc-btn" type="button" aria-label="Contents" title="Contents" ` +
      `aria-haspopup="true" aria-expanded="false">${TOC_ICON}</button>` +
      `<div class="panel-title">` +
      `<h2>${escapeHtml(n.title || n.id)}</h2>` +
      `<div class="meta">${escapeHtml(n.id)} · ${n.words || 0} words · ` +
      `${ins.length} in · ${out.length} out</div>` +
      (window.DEXIO_API ? `<div class="meta meta-when"></div>` : "") + `</div>` +
      // No Share here: the header's Share shares the open page (Forrest,
      // 2026-09-28: "it's weird seeing the share button above another share
      // button like this"), so there is one Share on the screen.
      `<button id="close" type="button" aria-label="Close page" title="Close (Esc)">` +
      `<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M6.5 6.5l11 11M17.5 6.5l-11 11"></path></svg>` +
      `</button>${tabs ? TABS : ""}` +
      `<nav class="toc toc-pop" aria-label="Contents" hidden></nav></div>` +
      `<div class="panel-body"${tabs ? ' id="panel-view" role="tabpanel" aria-labelledby="tab-page"' : ""}>` +
      `${loading ? LOADING : pageBody(n, body, ins)}</div></div>`;
    buildContents();
    // One panel shows every page, so its scroll position carried over from
    // page to page when a page was opened from the graph (Forrest,
    // 2026-09-26). Every page opens at its top, however it was opened.
    // Before spyContents, which reads the position to mark the current section.
    panel.scrollTop = 0;
    panel.classList.add("open");
    shareHere();
    markStuck();
    spyContents();
    placeGrip();
    if (state.layout !== "radial") revealBesidePanel(n);
    panel.querySelector("#close").onclick = () => select(null);
    wireBack();
    wireTabs();
    wirePageLinks();
    markTerms();
  }

  // The text replaces the placeholder; the head, and the reader's focus in
  // it, stay as they are. A failed fetch says so and offers another try.
  function fillPage(n, ins, body) {
    const slot = panel.querySelector(".page-loading");
    if (!slot) return;
    if (body === null) {
      slot.outerHTML = `<p class="page-error" role="alert">This page could not be loaded. ` +
        `<button type="button" class="page-retry">Try again</button></p>`;
      panel.querySelector(".page-retry").onclick = () =>
        select(n, { history: "replace", section: hist.section });
    } else {
      slot.outerHTML = pageBody(n, body, ins);
      buildContents();
      spyContents();
      wirePageLinks();
      placeGrip();              // a longer contents list can bring its scrollbar
      markTerms();
    }
    const main = panel.querySelector(".panel-main");
    if (main) main.removeAttribute("aria-busy");
    markStuck();
  }

  function wirePageLinks() {
    panel.querySelectorAll("a[data-id]").forEach((a) => {
      if (ROUTED) a.href = pageHref(a.dataset.id, a.dataset.sec);
      a.onclick = (e) => {
        if (newTab(e)) return;
        e.preventDefault();
        openPath(a.dataset.id, a.dataset.sec || "", "push");
      };
    });
  }

  // ---- the search's words in the open page ----
  // While a search is active its words are marked wherever they appear in
  // the open page's title and text, and in a past revision's text (Forrest,
  // 2026-09-28). Matched as the search matches them: any case, anywhere in a
  // word, common words dropped, a "quoted phrase" as written. Marked in the
  // rendered page rather than the markdown, so a word inside a link, a
  // heading or code is marked without breaking the markup; a phrase split by
  // formatting (half of it bold) is not. The marks follow the query as it is
  // typed and go when it is cleared. The page does not scroll to the first
  // one: every page opens at its top (Forrest, 2026-09-26).
  function termPattern(q) {
    if (!q) return null;
    const terms = queryTerms(q);
    if (!terms.length) return null;
    const esc = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&").replace(/ /g, "\\s+");
    return new RegExp([...terms].sort((a, b) => b.length - a.length).map(esc).join("|"), "giu");
  }

  function markTerms() {
    if (!panel || !panel.querySelectorAll || typeof document.createTreeWalker !== "function") return;
    for (const m of panel.querySelectorAll("mark.hit")) {
      const parent = m.parentNode;
      m.replaceWith(document.createTextNode(m.textContent));
      parent.normalize();
    }
    const re = termPattern(state.query);
    if (!re) return;
    for (const root of panel.querySelectorAll(".panel-title h2, article.md")) {
      const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT), found = [];
      while (walker.nextNode()) found.push(walker.currentNode);
      for (const node of found) {
        const text = node.nodeValue;
        let last = 0, m, frag = null;
        re.lastIndex = 0;
        while ((m = re.exec(text))) {
          if (!m[0]) { re.lastIndex++; continue; }
          frag = frag || document.createDocumentFragment();
          if (m.index > last) frag.append(text.slice(last, m.index));
          const mark = document.createElement("mark");
          mark.className = "hit";
          mark.textContent = m[0];
          frag.append(mark);
          last = m.index + m[0].length;
        }
        if (!frag) continue;
        if (last < text.length) frag.append(text.slice(last));
        node.replaceWith(frag);
      }
    }
  }

  // ---- history -------------------------------------------------------------
  // What a wiki's page history shows (Forrest, 2026-09-27): under the title,
  // when the page was last edited and created and by whom; a History tab, as
  // Wikipedia's "View history" is, with the page's details and every revision
  // newest first (the agent, the person behind it, the writer's note, the
  // words it added or took out); and any one revision, as the lines it
  // changed or the whole page as it was then. The app only: the exported
  // file carries no history.
  //
  // Redesigned the same day (Forrest asked for a better design):
  // - The Page / History tabs are the bottom row of the pinned head, so
  //   History is one click from anywhere in a long page. They sat under the
  //   head and scrolled away with the text. History carries its revision count.
  // - History opens on a summary (created, last edited, revisions, size) and
  //   the agents who wrote the page, in place of a seven-row table that
  //   repeated the meta line.
  // - Revisions read as a timeline: the note, which says why, is the line you
  //   read; who, what and when sit under it; each agent has an initial of its
  //   own colour on the rail. The person behind a change is named on the row
  //   only when more than one person appears, since one account on every row
  //   is noise; it is always in the tooltip and on the revision itself.
  // - The contents column lists the history's days, a revision's changed
  //   blocks, or the headings of the page as it was, instead of "(Top)" alone.
  const ICON_PAGE = `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4 1.75h5.2L12.5 5v9.25H4z"></path>` +
    `<path d="M9 1.9V5.2h3.3M6.2 8.2h4M6.2 10.7h4"></path></svg>`;
  const ICON_CLOCK = `<svg viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="6.1"></circle>` +
    `<path d="M8 4.7V8l2.3 1.5"></path></svg>`;
  const ICON_CHEV = `<svg class="rev-chev" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5"></path></svg>`;
  const TABS = `<div class="panel-tabs" role="tablist" aria-label="Page views">` +
    `<button type="button" role="tab" id="tab-page" data-view="page" aria-selected="true" ` +
    `aria-controls="panel-view">${ICON_PAGE}<span>Page</span></button>` +
    `<button type="button" role="tab" id="tab-history" data-view="history" aria-selected="false" ` +
    `tabindex="-1" aria-controls="panel-view">${ICON_CLOCK}<span>History</span>` +
    `<span class="tab-count" hidden></span></button></div>`;
  const WHEN = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" });
  const DAY = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });
  const DAY_HEAD = new Intl.DateTimeFormat(undefined,
    { weekday: "long", year: "numeric", month: "long", day: "numeric" });
  const DAY_HEAD_THIS_YEAR = new Intl.DateTimeFormat(undefined,
    { weekday: "long", month: "long", day: "numeric" });
  const CLOCK = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });
  const REL = window.Intl && Intl.RelativeTimeFormat
    ? new Intl.RelativeTimeFormat(undefined, { numeric: "auto" }) : null;
  const HIST_PAGE = 50;

  const at = (s) => new Date(s * 1000);
  // A day as the reader's calendar has it; a time of exactly midnight UTC is
  // a date someone wrote without a time (2026-09-12 in a page's frontmatter),
  // so it stays that date rather than the evening before west of Greenwich.
  const DAY_UTC = new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "UTC" });
  const dayOf = (s) => (s % 86400 === 0 ? DAY_UTC : DAY).format(at(s));
  const plural = (n, one, many) => `${n.toLocaleString()} ${n === 1 ? one : many || one + "s"}`;
  const cap = (s) => (s ? s[0].toUpperCase() + s.slice(1) : s);

  // "just now", "3 hours ago", "yesterday"; the date once it is a month old
  function ago(s) {
    const d = Date.now() / 1000 - s;
    if (!REL || d > 30 * 86400) return `on ${dayOf(s)}`;
    if (d < 60) return "just now";
    for (const [unit, secs] of [["day", 86400], ["hour", 3600], ["minute", 60]]) {
      if (d >= secs) return REL.format(-Math.floor(d / secs), unit);
    }
    return "just now";
  }

  // "Today", "Yesterday", else the weekday and date, with the year only when
  // it is not this one.
  function dayLabel(s) {
    const d = at(s), now = new Date();
    const start = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime();
    const days = Math.round((start(now) - start(d)) / 86400000);
    if (days === 0) return "Today";
    if (days === 1) return "Yesterday";
    return (d.getFullYear() === now.getFullYear() ? DAY_HEAD_THIS_YEAR : DAY_HEAD).format(d);
  }

  // An agent names itself on every change; the person is the account whose
  // key it used. Older changes have no agent, only the key's name.
  function whoName(r) { return (r && (r.agent || r.author)) || ""; }
  function whoFull(r) {
    const name = whoName(r);
    if (!name) return r && r.person ? r.person : "unknown";
    return r.person && r.person !== name ? `${name} (${r.person})` : name;
  }
  function whoTitle(r) {
    if (!r) return "";
    const bits = [];
    if (r.agent) bits.push(`agent ${r.agent}`);
    if (r.person) bits.push(`account ${r.person}`);
    if (r.author) bits.push(`API key ${r.author}`);
    return bits.join(", ");
  }

  // An agent's initial on a tint of its own: the same name gets the same
  // colour everywhere, so one agent's changes can be picked out down the list.
  function hueOf(name) {
    let h = 0;
    for (const ch of String(name)) h = (h * 31 + ch.codePointAt(0)) >>> 0;
    return h % 360;
  }
  // One letter, or two for a name in parts: claude-code is CC, codex is C.
  function initials(name) {
    const parts = String(name).split(/[^\p{L}\p{N}]+/u).filter(Boolean);
    const first = (w) => [...w][0].toUpperCase();
    if (!parts.length) return [...String(name)][0];
    return parts.length > 1 ? first(parts[0]) + first(parts[1]) : first(parts[0]);
  }
  function avatar(name) {
    if (!name) return `<span class="av av-none" aria-hidden="true"></span>`;
    const ch = initials(name);
    return `<span class="av${ch.length > 1 ? " av-2" : ""}" style="--av-h:${hueOf(name)}" ` +
      `aria-hidden="true">${escapeHtml(ch)}</span>`;
  }

  function api(route, params) {
    const q = new URLSearchParams({ project: window.DEXIO_PROJECT || "", ...params });
    return fetch(withW(`${window.DEXIO_API}/${route}?${q}`))
      .then((r) => (r.ok ? r.json() : null)).catch(() => null);
  }

  // The line under the meta line: last edited, then created. Also the
  // History tab's count.
  function showWhen(info) {
    const el = panel.querySelector(".meta-when");
    if (!el || !info) return;
    const bits = [];
    const said = info.okf || {};
    // A draft or a deprecated page says so first (OKF's status; stable,
    // the default, says nothing).
    if (said.status === "draft" || said.status === "deprecated") {
      bits.push(`<span class="pg-status pg-${said.status}">${cap(said.status)}</span>`);
    }
    if (info.updated_at) {
      const by = info.updated_by && whoName(info.updated_by);
      bits.push(`<span title="${escapeHtml(WHEN.format(at(info.updated_at)) +
        (info.updated_by ? " · " + whoTitle(info.updated_by) : ""))}">Edited ${escapeHtml(
        ago(info.updated_at))}${by ? " by " + escapeHtml(by) : ""}</span>`);
    }
    const c = info.created;
    if (c) {
      const tip = c.exact ? WHEN.format(at(c.at)) + (whoTitle(c) ? " · " + whoTitle(c) : "")
        : "History began then; the page is at least that old";
      bits.push(`<span title="${escapeHtml(tip)}">Created ${c.exact ? "" : "on or before "}` +
        `${escapeHtml(DAY.format(at(c.at)))}${c.exact && whoName(c) ? " by " +
        escapeHtml(whoName(c)) : ""}</span>`);
    }
    const rv = said.review;
    if (rv) bits.push(reviewBit(rv));
    if (canReview() && !(rv && rv.mine && rv.tier === "human-reviewed" && !rv.edited_since)) {
      bits.push(`<button type="button" class="mark-reviewed" title="Record that you have read this ` +
        `page and it is right">Mark reviewed</button>`);
    }
    el.innerHTML = bits.join(" · ");
    const btn = el.querySelector(".mark-reviewed");
    if (btn) btn.onclick = () => markReviewed(btn);
    showCount(info.revisions);
  }

  // The page's review, in the words of the line around it: "Reviewed 2 days
  // ago by Forrest Zhang", or "Checked" when only agents have checked it; a
  // page changed after that review says so. A guest sees when, not who.
  function reviewBit(rv) {
    const human = rv.tier === "human-reviewed";
    const who = rv.name ? ` by ${escapeHtml(rv.name)}` : "";
    const when = rv.at ? ` ${escapeHtml(ago(rv.at))}` : "";
    const others = (rv.reviewers || []).filter((r) => r !== rv.name);
    const tip = (rv.at ? WHEN.format(at(rv.at)) : "") +
      (others.length ? ` · also reviewed by ${others.join(", ")}` : "") +
      (rv.edited_since ? " · the page has changed since" : "");
    return `<span class="rv ${human ? "rv-human" : "rv-agent"}${rv.edited_since ? " rv-old" : ""}"` +
      ` title="${escapeHtml(tip)}">${human ? "Reviewed" : "Checked"}${when}${who}` +
      `${rv.edited_since ? ", changed since" : ""}</span>`;
  }

  // A member can mark the page reviewed; a guest or the static viewer cannot.
  function canReview() {
    return !!window.DEXIO_API && !GUEST && !!hist.n && hist.text !== null;
  }

  // Mark reviewed (okf.py, app.py review): the server adds the person to the
  // page's `verified` frontmatter as a change of its own. It sends the
  // version read, so a page that changed meanwhile is not marked unread.
  async function markReviewed(btn) {
    const n = hist.n;
    btn.disabled = true;
    btn.textContent = "Marking…";
    let r = null, j = null;
    try {
      const q = new URLSearchParams({ project: window.DEXIO_PROJECT || "" });
      r = await fetch(withW(`${window.DEXIO_API}/review?${q}`), {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: n.id, version: hist.version || "" }) });
      j = await r.json().catch(() => null);
    } catch (e) { r = null; }
    if (hist.n !== n) return;                     // another page was opened meanwhile
    if (!r || !r.ok || !j) {
      btn.disabled = false;
      btn.textContent = "Mark reviewed";
      const msg = (j && j.error) || "The review was not saved. Try again.";
      const note = document.createElement("span");
      note.className = "rv-error";
      note.setAttribute("role", "alert");
      note.textContent = " " + msg;
      btn.after(note);
      return;
    }
    hist.text = j.text || "";
    hist.info = j.info || null;
    hist.version = j.version || "";
    showWhen(hist.info);
  }

  function showCount(n) {
    const badge = panel.querySelector(".tab-count");
    if (!badge || !n) return;
    badge.textContent = n > 999 ? "999+" : n.toLocaleString();
    badge.hidden = false;
    panel.querySelector("#tab-history").setAttribute("aria-label", `History, ${plural(n, "revision")}`);
  }

  // Tabs as the ARIA pattern has them: one tab stop, arrows move between
  // them and show the one they land on.
  function wireTabs() {
    const tabs = [...panel.querySelectorAll(".panel-tabs [role=tab]")];
    const go = (b) => (b.dataset.view === "page" ? showPageView() : showHistory(hist.n.id));
    tabs.forEach((b, i) => {
      b.onclick = () => go(b);
      b.onkeydown = (e) => {
        const j = { ArrowRight: i + 1, ArrowLeft: i - 1, Home: 0, End: tabs.length - 1 }[e.key];
        if (j === undefined) return;
        e.preventDefault();
        const t = tabs[(j + tabs.length) % tabs.length];
        t.focus();
        go(t);
      };
    });
  }

  function markTab(view) {
    const want = view === "page" ? "page" : "history";
    panel.querySelectorAll(".panel-tabs [role=tab]").forEach((b) => {
      const on = b.dataset.view === want;
      b.setAttribute("aria-selected", String(on));
      b.tabIndex = on ? 0 : -1;
    });
    const body = panel.querySelector(".panel-body[role=tabpanel]");
    if (body) body.setAttribute("aria-labelledby", `tab-${want}`);
  }

  // Swap what is under the head; the head, and the contents column's width,
  // stay as they are.
  function setBody(html, view) {
    const body = panel.querySelector(".panel-body");
    if (!body) return null;
    hist.view = view;
    markTab(view);
    body.innerHTML = html;
    const main = panel.querySelector(".panel-main");
    if (main) main.removeAttribute("aria-busy");
    panel.scrollTop = 0;
    buildContents();
    spyContents();
    markStuck();
    placeGrip();
    wirePageLinks();
    markTerms();
    return body;
  }

  function showPageView() {
    const n = hist.n;
    if (!n) return;
    ++opening;
    if (hist.text === null) {                          // it never loaded: try again
      select(n, { history: "replace", section: hist.section });
      return;
    }
    setBody(pageBody(n, hist.text, hist.ins), "page");
  }

  // The words a revision added or took out; a page's first revision added
  // all of its words. A move takes the words along, so it changes none.
  function wordChange(r) {
    if (r.moved_to || r.moved_from) return null;
    if (r.deleted) return r.words_before ? { cls: "minus", text: `−${plural(r.words_before, "word")}` } : null;
    if (r.words_before === null || r.words_before === undefined) {
      if (r.op === "baseline") return { cls: "muted", text: plural(r.words || 0, "word") };
      return r.words ? { cls: "plus", text: `+${plural(r.words, "word")}` } : null;
    }
    const d = (r.words || 0) - r.words_before;
    if (!d) return null;
    return d > 0 ? { cls: "plus", text: `+${plural(d, "word")}` }
                 : { cls: "minus", text: `−${plural(-d, "word")}` };
  }

  const OPS = { write: "Rewritten", edit: "Edited", append: "Appended", push: "Pushed",
                move: "Links updated for a move", delete: "Deleted", review: "Marked reviewed" };

  // What a revision did, in words.
  function revWhat(r) {
    if (r.deleted) return r.moved_to ? "Moved to" : "Deleted";
    if (r.op === "baseline") return "Earliest recorded version";
    if (r.moved_from) return "Moved from";
    if (r.first) return "Created";
    const ops = String(r.op || "").split("+").filter((o) => o !== "baseline");
    return ops.map((o) => OPS[o] || o).join(", ") || "Changed";
  }

  // Green for a page's start, red for its end, plain for the rest.
  function revTone(r) {
    if (r.deleted && !r.moved_to) return " bad";
    if (r.first && r.op !== "baseline" && !r.moved_from) return " good";
    return "";
  }

  function revWhere(r) {
    const p = r.moved_to || r.moved_from;
    return p ? ` <a href="#" class="hist-path" data-hist="${escapeHtml(p)}">${escapeHtml(p)}</a>` : "";
  }

  const SEP = `<span class="sep" aria-hidden="true">·</span>`;

  function revRow(r, opts) {
    const w = wordChange(r);
    const base = r.op === "baseline";
    const who = whoName(r);
    const what = revWhat(r) + (r.moved_to || r.moved_from ? " " + (r.moved_to || r.moved_from) : "");
    const tone = revTone(r);
    const meta = [];
    if (!base) {
      meta.push(`<span class="rev-who" title="${escapeHtml(whoTitle(r))}">${escapeHtml(who || r.person ||
        "unknown")}</span>` + (opts.people && r.person && r.person !== who
        ? ` <span class="rev-person">${escapeHtml(r.person)}</span>` : ""));
    }
    if (r.note) meta.push(`<span class="rev-op${tone}">${escapeHtml(what)}</span>`);
    meta.push(`<time datetime="${at(r.at).toISOString()}" title="${escapeHtml(WHEN.format(at(r.at)))}">` +
      `${escapeHtml(CLOCK.format(at(r.at)))}</time>`);
    return `<li class="rev-item"><button type="button" class="rev" data-rev="${r.id}">` +
      `<span class="rev-node">${base ? avatar("") : avatar(who || r.person)}</span>` +
      `<span class="rev-main"><span class="rev-text${r.note ? "" : " rev-op" + tone}">` +
      `${escapeHtml(r.note || what)}</span>` +
      `<span class="rev-meta">${meta.join(SEP)}</span></span>` +
      `<span class="rev-side">` + (opts.current ? `<span class="rev-tag">Current</span>` : "") +
      (w ? `<span class="rev-words ${w.cls}">${escapeHtml(w.text)}</span>` : "") + ICON_CHEV +
      `</span></button></li>`;
  }

  // Revisions under a heading for each day, on one rail, as a commit list is.
  function revList(revs, first) {
    let out = "";
    revs.forEach((r, i) => {
      const day = dayLabel(r.at);
      if (day !== hist.lastDay) {
        out += `<li class="rev-day">${escapeHtml(day)}</li>`;
        hist.lastDay = day;
      }
      out += revRow(r, { people: hist.people, current: first && i === 0 && hist.exists && !r.deleted });
    });
    return out;
  }

  // Created, last edited, revisions and size, then who wrote the page.
  function summary(info, path) {
    const cells = [];
    const cell = (label, value, sub, tip) => cells.push(
      `<div class="hs-cell"${tip ? ` title="${escapeHtml(tip)}"` : ""}>` +
      `<div class="hs-label">${label}</div><div class="hs-value">${value}</div>` +
      (sub ? `<div class="hs-sub">${sub}</div>` : "") + `</div>`);
    const c = info.created;
    if (c) {
      const by = c.exact && whoName(c) ? `by ${escapeHtml(whoName(c))}` : "";
      const as = c.exact && c.path && c.path !== path ? ` as <code>${escapeHtml(c.path)}</code>` : "";
      cell("Created", escapeHtml(DAY.format(at(c.at))),
        c.exact ? by + as : "or earlier, when history began",
        c.exact ? WHEN.format(at(c.at)) + (whoTitle(c) ? " · " + whoTitle(c) : "")
          : "History began then; the page is at least that old");
    }
    if (info.updated_at) {
      const u = info.updated_by;
      cell(info.exists ? "Last edited" : "Last changed", escapeHtml(cap(ago(info.updated_at).replace(/^on /, ""))),
        u && whoName(u) ? `by ${escapeHtml(whoName(u))}` : "",
        WHEN.format(at(info.updated_at)) + (u ? " · " + whoTitle(u) : ""));
    }
    const who = info.contributors || [];
    cell("Revisions", escapeHtml((info.revisions || 0).toLocaleString()),
      who.length ? `by ${escapeHtml(plural(who.length, "agent"))}` : "");
    const n = state.byId.get(path);
    if (info.exists && n) {
      cell("Size", escapeHtml(plural(n.words || 0, "word")),
        info.chars !== null && info.chars !== undefined ? escapeHtml(plural(info.chars, "character")) : "");
    }
    let out = `<div class="hist-sum-box"><div class="hist-sum" data-n="${cells.length}">` +
      `${cells.join("")}</div></div>`;
    if (who.length) {
      out += `<div class="hist-who"><span class="hw-label">Edited by</span>` +
        who.slice(0, 8).map((p) => `<span class="who-chip" title="${escapeHtml(plural(p.changes, "change"))}">` +
          `${avatar(p.name)}<span>${escapeHtml(p.name)}</span><span class="n">${p.changes.toLocaleString()}` +
          `</span></span>`).join("") +
        (who.length > 8 ? `<span class="muted">and ${(who.length - 8).toLocaleString()} more</span>` : "") +
        `</div>`;
    }
    if (info.moved_from) {
      out += `<p class="hist-moved">Moved here from <a href="#" class="hist-path" data-hist="${escapeHtml(
        info.moved_from.path)}">${escapeHtml(info.moved_from.path)}</a> on ${escapeHtml(
        DAY.format(at(info.moved_from.at)))}. Its history there is kept under that path.</p>`;
    }
    return out;
  }

  // The history of `path`: the open page's, or a path it had before a move.
  async function showHistory(path) {
    const ticket = ++opening;
    hist.path = path;
    hist.lastDay = null;
    const own = hist.n && path === hist.n.id;
    const crumb = own ? "" : `<p class="hist-crumb">${ICON_CLOCK}<span>History of ` +
      `<code>${escapeHtml(path)}</code>, a path this page had before. <a href="#" class="hist-path" ` +
      `data-hist="${escapeHtml(hist.n.id)}">Back to this page's history</a></span></p>`;
    setBody(crumb + LOADING, "history");
    const data = await api("page-history", { path, limit: HIST_PAGE });
    if (ticket !== opening) return;
    if (!data) {
      setBody(crumb + `<p class="page-error" role="alert">The history could not be loaded. ` +
        `<button type="button" class="page-retry">Try again</button></p>`, "history");
      panel.querySelector(".page-retry").onclick = () => showHistory(path);
      return;
    }
    const revs = data.revisions || [];
    const info = data.info || {};
    hist.exists = !!info.exists;
    hist.people = new Set(revs.map((r) => r.person).filter(Boolean)).size > 1;
    if (own) showCount(info.revisions);
    const body = setBody(crumb + summary(info, path) +
      (revs.length ? `<ol class="hist-list" aria-label="Revisions, newest first">${revList(revs, true)}</ol>`
        : `<p class="muted">No changes recorded since its history began.</p>`) +
      (data.next_before ? `<button type="button" class="hist-more">Show older revisions</button>` : ""),
      "history");
    if (body) wireHistory(body, path, data.next_before);
  }

  function wireHistory(body, path, next) {
    body.querySelectorAll("a.hist-path").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); showHistory(a.dataset.hist); };
    });
    body.querySelectorAll("button.rev").forEach((b) => {
      b.onclick = () => showRevision(path, +b.dataset.rev);
    });
    const more = body.querySelector(".hist-more");
    if (more && next) {
      more.onclick = async () => {
        more.disabled = true;
        more.textContent = "Loading…";
        const ticket = opening;
        const data = await api("page-history", { path, limit: HIST_PAGE, before: next });
        if (ticket !== opening) return;
        if (!data) { more.disabled = false; more.textContent = "Try again"; return; }
        const older = data.revisions || [];
        if (!hist.people && new Set(older.map((r) => r.person).filter(Boolean)).size > 1) hist.people = true;
        body.querySelector(".hist-list").insertAdjacentHTML("beforeend", revList(older, false));
        if (data.next_before) {
          more.disabled = false;
          more.textContent = "Show older revisions";
        } else more.remove();
        buildContents();
        spyContents(true);
        placeGrip();
        wireHistory(body, path, data.next_before);
      };
    }
  }

  function diffHtml(d) {
    if (!d.hunks.length) return `<p class="muted">The text is the same as the revision before.</p>`;
    const line = (ln) => {
      const kind = ln.t === "+" ? "add" : ln.t === "-" ? "del" : "ctx";
      const text = ln.w
        ? ln.w.map(([changed, s]) => (changed ? `<mark>${escapeHtml(s)}</mark>` : escapeHtml(s))).join("")
        : escapeHtml(ln.s);
      return `<div class="dl ${kind}"><span class="dn">${ln.t === "-" ? ln.a : ln.b}</span>` +
        `<span class="ds" aria-label="${kind === "add" ? "added" : kind === "del" ? "removed" : ""}">` +
        `${ln.t === "+" ? "+" : ln.t === "-" ? "−" : ""}</span><span class="dt">${text || " "}</span></div>`;
    };
    // "Lines 12–18": the span of the new text the block covers
    const span = (h) => {
      const last = h.new_start + h.lines.filter((ln) => ln.t !== "-").length - 1;
      return last > h.new_start ? `Lines ${h.new_start}–${last}` : `Line ${h.new_start}`;
    };
    return `<div class="diff">` + d.hunks.map((h) =>
      `<div class="dh">${span(h)}</div>` + h.lines.map(line).join("")).join("") + `</div>`;
  }

  // One revision: who made it and when, then what it changed (the lines, or
  // for the first revision the whole text) or the page as it was after it.
  async function showRevision(path, id, show) {
    const ticket = ++opening;
    // switching between Changes and the page as it was needs no new fetch
    const kept = hist.rev && hist.rev.path === path && hist.rev.id === id ? hist.rev : null;
    if (!kept) setBody(LOADING, "revision");
    const r = kept || await api("revision", { path, id });
    if (ticket !== opening) return;
    hist.rev = r;
    if (!r) {
      setBody(`<p class="page-error" role="alert">This revision could not be loaded. ` +
        `<button type="button" class="page-retry">Try again</button></p>`, "revision");
      panel.querySelector(".page-retry").onclick = () => showRevision(path, id, show);
      return;
    }
    const canDiff = !!r.diff;
    const mode = show || (canDiff ? "diff" : "text");
    const w = wordChange(r);
    const base = r.op === "baseline";
    const title = (hist.n && path === hist.n.id && hist.n.title) || path.split("/").pop();
    let content;
    if (r.text === null || r.text === undefined) {
      content = r.removed_text !== undefined && r.removed_text !== null
        ? `<p class="muted">This change ${r.moved_to ? "moved the page away" : "deleted the page"}. ` +
          `It read:</p><article class="md md-past">${renderMarkdown(r.removed_text, path, title)}</article>`
        : `<p class="muted">This change removed the page.</p>`;
    } else if (mode === "diff" && canDiff) {
      content = diffHtml(r.diff);
    } else {
      content = `<article class="md md-past">${renderMarkdown(r.text, path, title)}</article>`;
    }
    const stats = [
      w ? `<span class="${w.cls}">${escapeHtml(w.text)}</span>` : "",
      canDiff && r.diff.added ? `<span class="plus">+${plural(r.diff.added, "line")}</span>` : "",
      canDiff && r.diff.removed ? `<span class="minus">−${plural(r.diff.removed, "line")}</span>` : ""]
      .filter(Boolean).join(" ");
    const who = whoName(r);
    const kicker = (base ? "" : `${avatar(who || r.person)}<span class="rev-who" title="${escapeHtml(
      whoTitle(r))}">${escapeHtml(who || r.person || "unknown")}</span>` +
      (r.person && r.person !== who ? `<span class="rev-person">${escapeHtml(r.person)}</span>` : "") + SEP) +
      `<time datetime="${at(r.at).toISOString()}">${escapeHtml(WHEN.format(at(r.at)))}</time>` +
      (r.latest && !r.deleted ? `<span class="rev-tag">Current</span>` : "");
    const what = `<span class="rev-op${revTone(r)}">${escapeHtml(revWhat(r))}</span>${revWhere(r)}`;
    const kicked = r.note ? kicker.replace(`${SEP}<time`, `${SEP}<span>${what}</span>${SEP}<time`) : kicker;
    const body = setBody(
      `<div class="rev-bar"><button type="button" class="rev-back">` +
      `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5"></path></svg>History</button>` +
      `<span class="rev-nav" role="group" aria-label="Step through revisions">` +
      `<button type="button" data-go="${r.older || ""}"${r.older ? "" : " disabled"} title="The revision before this one">` +
      `<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5"></path></svg>Older</button>` +
      `<button type="button" data-go="${r.newer || ""}"${r.newer ? "" : " disabled"} title="The revision after this one">` +
      `Newer<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5 10.5 8 6 12.5"></path></svg></button>` +
      `</span></div>` +
      `<div class="rev-head"><div class="rev-kicker">${kicked}</div>` +
      `<h3 class="rev-title">${r.note ? escapeHtml(r.note) : what}</h3></div>` +
      `<div class="rev-tools">` +
      (r.text !== null && r.text !== undefined && canDiff
        ? `<div class="rev-show" role="group" aria-label="Show">` +
          `<button type="button" data-show="diff" aria-pressed="${mode === "diff"}">Changes</button>` +
          `<button type="button" data-show="text" aria-pressed="${mode === "text"}">Page as it was</button></div>`
        : "") +
      (stats ? `<span class="rev-stats">${stats}</span>` : "") + `</div>` +
      `<div class="rev-content">${content}</div>`, "revision");
    if (!body) return;
    body.querySelector(".rev-back").onclick = () => showHistory(path);
    body.querySelectorAll(".rev-nav [data-go]").forEach((b) => {
      if (b.dataset.go) b.onclick = () => showRevision(path, +b.dataset.go, show);
    });
    body.querySelectorAll(".rev-show [data-show]").forEach((b) => {
      b.onclick = () => showRevision(path, id, b.dataset.show);
    });
    body.querySelectorAll("a.hist-path").forEach((a) => {
      a.onclick = (e) => { e.preventDefault(); showHistory(a.dataset.hist); };
    });
  }

  // Right-click on the graph closes the open page. Only the left button drags,
  // pans and opens pages: a right-click on a node used to open that node on
  // release. Ctrl-click is a right-click on a Mac.
  canvas.addEventListener("contextmenu", (e) => {
    if (!panel.classList.contains("open")) return;
    e.preventDefault();
    select(null);
  });

  canvas.addEventListener("mousedown", (e) => {
    // The middle button always pans, wherever it lands, so moving the graph
    // never picks up a page by accident, as a left drag that starts on a
    // node does (Forrest, 2026-09-26). preventDefault keeps Windows and Linux
    // browsers from starting their autoscroll.
    if (e.button === 1) {
      e.preventDefault();
      state.userView = true;
      state.drag = null;
      state.pan = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y };
      canvas.style.cursor = "grabbing";
      return;
    }
    if (e.button !== 0 || e.ctrlKey) return;
    state.userView = true;
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const n = nodeAt(mx, my);
    if (n) {
      finishGlide();
      state.drag = { id: n.id, moved: false };
    } else {
      state.pan = { x: e.clientX, y: e.clientY, vx: state.view.x, vy: state.view.y };
      canvas.style.cursor = "grabbing";
    }
  });
  window.addEventListener("mousemove", (e) => {
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    if (state.drag) {
      const p = state.pos.get(state.drag.id);
      p.x = (mx - state.view.x) / state.view.k;
      p.y = (my - state.view.y) / state.view.k;
      state.drag.moved = true;
      if (simulated()) state.alpha = Math.max(state.alpha, state.layout === "force" ? OBS.dragAlpha : 0.25);
      tick();
    } else if (state.pan) {
      state.view.x = state.pan.vx + (e.clientX - state.pan.x);
      state.view.y = state.pan.vy + (e.clientY - state.pan.y);
      draw();
    } else {
      canvas.style.cursor = nodeAt(mx, my) ? "pointer" : "grab";
    }
  });
  window.addEventListener("mouseup", (e) => {
    if (state.drag && !state.drag.moved) select(state.byId.get(state.drag.id));
    if (state.pan) canvas.style.cursor = "";
    state.drag = null; state.pan = null;
  });
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault();
    state.userView = true;
    const r = canvas.getBoundingClientRect();
    const mx = e.clientX - r.left, my = e.clientY - r.top;
    const f = e.deltaY < 0 ? 1.12 : 1 / 1.12;
    const k2 = Math.max(MIN_ZOOM, Math.min(4, state.view.k * f));
    state.view.x = mx - (mx - state.view.x) * (k2 / state.view.k);
    state.view.y = my - (my - state.view.y) * (k2 / state.view.k);
    state.view.k = k2;
    draw();
  }, { passive: false });

  // ---- touch -------------------------------------------------------------
  // The canvas had mouse handlers only, so on a phone the graph was a static
  // picture: no pan, no zoom, no way to open a page. Pointer events would have
  // unified this, but touch gives cleaner multi-finger pinch state.
  const TAP_SLOP = 10;      // px of movement still counted as a tap
  const TAP_MS = 350;

  function touchLocal(touch) {
    const r = canvas.getBoundingClientRect();
    return { x: touch.clientX - r.left, y: touch.clientY - r.top };
  }
  function pinchDistance(t) {
    const dx = t[0].clientX - t[1].clientX, dy = t[0].clientY - t[1].clientY;
    return Math.hypot(dx, dy);
  }
  function pinchCentre(t) {
    const r = canvas.getBoundingClientRect();
    return { x: (t[0].clientX + t[1].clientX) / 2 - r.left,
             y: (t[0].clientY + t[1].clientY) / 2 - r.top };
  }

  canvas.addEventListener("touchstart", (e) => {
    state.userView = true;
    finishGlide();
    if (e.touches.length === 1) {
      const p = touchLocal(e.touches[0]);
      const n = nodeAt(p.x, p.y);
      state.touch = {
        mode: n ? "node" : "pan", id: n ? n.id : null,
        sx: e.touches[0].clientX, sy: e.touches[0].clientY,
        vx: state.view.x, vy: state.view.y,
        t0: Date.now(), moved: 0,
      };
    } else if (e.touches.length === 2) {
      state.touch = {
        mode: "pinch",
        d0: pinchDistance(e.touches), k0: state.view.k,
        c: pinchCentre(e.touches),
      };
    }
    e.preventDefault();
  }, { passive: false });

  canvas.addEventListener("touchmove", (e) => {
    const s = state.touch;
    if (!s) return;
    if (s.mode === "pinch" && e.touches.length === 2) {
      const k2 = Math.max(MIN_ZOOM, Math.min(4, s.k0 * (pinchDistance(e.touches) / s.d0)));
      // keep the point between the fingers fixed while scaling
      state.view.x = s.c.x - (s.c.x - state.view.x) * (k2 / state.view.k);
      state.view.y = s.c.y - (s.c.y - state.view.y) * (k2 / state.view.k);
      state.view.k = k2;
      draw();
    } else if (e.touches.length === 1) {
      const dx = e.touches[0].clientX - s.sx, dy = e.touches[0].clientY - s.sy;
      s.moved = Math.max(s.moved, Math.hypot(dx, dy));
      if (s.mode === "node" && s.moved > TAP_SLOP) {
        const p = touchLocal(e.touches[0]);
        const pos = state.pos.get(s.id);
        if (pos) {
          pos.x = (p.x - state.view.x) / state.view.k;
          pos.y = (p.y - state.view.y) / state.view.k;
          if (simulated()) state.alpha = Math.max(state.alpha, state.layout === "force" ? OBS.dragAlpha : 0.25);
          tick();
        }
      } else if (s.mode === "pan") {
        state.view.x = s.vx + dx;
        state.view.y = s.vy + dy;
        draw();
      }
    }
    e.preventDefault();
  }, { passive: false });

  canvas.addEventListener("touchend", (e) => {
    const s = state.touch;
    if (s && s.mode === "node" && s.moved <= TAP_SLOP && Date.now() - s.t0 < TAP_MS) {
      select(state.byId.get(s.id));
    }
    if (e.touches.length === 0) state.touch = null;
  });
  canvas.addEventListener("touchcancel", () => { state.touch = null; });

  // ---- bottom sheet ------------------------------------------------------
  // On a phone the page panel is a sheet, and pulling it down closes it:
  // from its head (the grab handle and the title) at any time, from the text
  // only while the page is at its top. A gesture either drags the sheet or
  // scrolls the text, never both: its first few pixels decide which, and the
  // choice holds until the finger lifts. It used to be both at once. A drag
  // that dipped down even a pixel at the top of a page shifted the sheet,
  // which then stayed shifted while the text scrolled, and the sheet trailed
  // the finger through the .18s slide (Forrest, 2026-09-26: "weird and
  // jittery" in Chrome on a phone). Now it follows the finger with no
  // transition and slides only once the finger lifts.
  // The move listener is not passive so a drag can keep the text still.
  // Once the browser has begun a scroll its moves are no longer cancelable,
  // and the gesture stays a scroll.
  if (panel) {
    const sheetScreen = matchMedia("(max-width: 720px)");
    const DECIDE = 4;     // px a finger moves before the gesture is decided
    const CLOSE = 90;     // px pulled down that closes the sheet
    const FLICK = 0.5;    // px/ms: a quick pull closes it from FLICK_MIN
    const FLICK_MIN = 24;
    let pull = null;
    const settle = (close) => {
      panel.style.transition = "";
      panel.style.transform = "";
      if (close) select(null);
    };
    panel.addEventListener("touchstart", (e) => {
      if (pull && pull.drag) settle(false);   // a second finger ends the drag
      pull = null;
      if (!sheetScreen.matches || e.touches.length !== 1 || !panel.classList.contains("open")) return;
      // the contents menu scrolls on its own
      if (e.target.closest && e.target.closest(".toc-pop")) return;
      const onHead = !!(e.target.closest && e.target.closest(".panel-head"));
      if (!onHead && panel.scrollTop > 0) return;
      const t = e.touches[0];
      pull = { x: t.clientX, y: t.clientY, drag: null, from: 0, top: 0, dy: 0, v: 0, at: e.timeStamp, onHead };
    }, { passive: true });
    panel.addEventListener("touchmove", (e) => {
      if (!pull || e.touches.length !== 1) return;
      const t = e.touches[0], dx = t.clientX - pull.x, dy = t.clientY - pull.y;
      if (pull.drag === null) {
        // On the head the text never scrolls (its touch-action is none in
        // the stylesheet). Its first moves are held too, before the gesture
        // is decided, so the browser cannot begin a scroll in them. On an
        // iPhone with the page scrolled, the grab handle did nothing: the
        // pull went to the text as a scroll instead (Forrest, 2026-10-01).
        if (pull.onHead && e.cancelable) e.preventDefault();
        if (Math.max(Math.abs(dx), Math.abs(dy)) < DECIDE) return;
        // down, more than sideways, and before the browser started a scroll
        if (!(e.cancelable && dy > Math.abs(dx))) { pull = null; return; }
        pull.drag = true;
        pull.from = dy;
        pull.top = panel.scrollTop;
        panel.style.transition = "none";
      }
      if (e.cancelable) e.preventDefault();
      const y = Math.max(0, dy - pull.from), dt = e.timeStamp - pull.at;
      if (dt > 0) pull.v = 0.8 * ((y - pull.dy) / dt) + 0.2 * pull.v;
      pull.dy = y;
      pull.at = e.timeStamp;
      panel.style.transform = y ? `translateY(${y}px)` : "";
      // pushed back up past where it started, the finger scrolls the text,
      // as the browser would have
      panel.scrollTop = pull.top + Math.max(0, pull.from - dy);
    }, { passive: false });
    panel.addEventListener("touchend", (e) => {
      const p = pull;
      if (!p || !p.drag || e.touches.length) return;
      pull = null;
      // a finger that stopped before lifting is not a flick
      const v = e.timeStamp - p.at > 80 ? 0 : p.v;
      settle(p.dy > CLOSE || (v > FLICK && p.dy > FLICK_MIN));
    });
    panel.addEventListener("touchcancel", () => {
      const p = pull;
      pull = null;
      if (p && p.drag) settle(false);
    });
  }

  // ---- panel width -------------------------------------------------------
  // The side panel's left edge can be dragged (or moved with the arrow keys)
  // to resize it; the width is kept per browser, and a double-click on the
  // edge goes back to the default. Wide screens only: below 721px the panel
  // is a full-width bottom sheet.
  const grip = document.getElementById("panel-grip");
  const tocGrip = document.getElementById("toc-grip");
  const wrap = canvas.parentElement;
  const wide = matchMedia("(min-width: 721px)");
  const PANEL_KEY = "dexio-panel-w";
  const PANEL_MIN = 360, GRAPH_MIN = 160;
  const TOC_W_KEY = "dexio-toc-w";
  const TOC_W_MIN = 140, TOC_W_MAX = 420;

  // A length the stylesheet sets on the panel (--toc-w, --toc-gap, --panel-pad).
  function panelVar(name) {
    return parseFloat(getComputedStyle(panel).getPropertyValue(name)) || 0;
  }

  function placeGrip() {
    if (!grip || !panel) return;
    const show = wide.matches && panel.classList.contains("open");
    grip.style.display = show ? "block" : "none";
    if (show) grip.style.left = `${wrap.clientWidth - panel.offsetWidth}px`;
    // the contents boundary: the middle of the gap between column and text
    if (!tocGrip) return;
    const toc = show && panel.classList.contains("toc-pinned");
    tocGrip.style.display = toc ? "block" : "none";
    if (toc) {
      tocGrip.style.left = `${wrap.clientWidth - panel.offsetWidth + panel.clientLeft +
                              panelVar("--toc-w") + tocSeam()}px`;
    }
  }

  // How far past the contents column's box the boundary sits: the middle of
  // the gap that shows between the column and the text. --toc-gap is only the
  // grid's gap; the column's own right padding shows as gap too, unless the
  // column's scrollbar (always drawn with a mouse on a Mac, and on Windows)
  // fills it. The boundary was the grid gap's middle until 2026-09-27, which
  // put it 3px right of the middle of what shows (Forrest).
  function tocSeam() {
    const gap = panelVar("--toc-gap");
    const side = panel.querySelector && panel.querySelector(".toc-side");
    if (!side) return gap / 2;
    const bar = side.offsetWidth - side.clientWidth;
    const pad = bar > 0 ? 0 : parseFloat(getComputedStyle(side).paddingRight) || 0;
    return (gap - pad) / 2;
  }

  function setPanelWidth(px) {
    if (!panel) return;
    if (px) {
      // px is the whole panel; --panel-w is its width without the pinned
      // contents column, and never so wide that the column would not fit
      const most = wrap.clientWidth - GRAPH_MIN - tocHold();
      px = Math.round(Math.max(PANEL_MIN, Math.min(px - tocRoom(), most)));
      panel.style.setProperty("--panel-w", `${px}px`);
    } else {
      panel.style.removeProperty("--panel-w");
    }
    placeGrip();
  }

  function savePanelWidth() {
    try {
      const px = parseInt(panel.style.getPropertyValue("--panel-w"), 10);
      if (px) localStorage.setItem(PANEL_KEY, String(px));
      else localStorage.removeItem(PANEL_KEY);
    } catch (e) {}
  }

  // Move the boundary between the contents column and the text. The panel's
  // edges stay put: what the column gains the text gives up, and the other
  // way (Forrest, 2026-09-26). `total` is the panel's width to keep.
  function setTocWidth(w, total) {
    if (!panel) return;
    const pad = panelVar("--panel-pad"), gap = panelVar("--toc-gap");
    // the text never drops below PANEL_MIN, its width without the column
    const most = Math.min(TOC_W_MAX, total - PANEL_MIN - gap + pad);
    w = Math.round(Math.max(TOC_W_MIN, Math.min(w, most)));
    panel.style.setProperty("--toc-w", `${w}px`);
    panel.style.setProperty("--panel-w", `${total - (w + gap - pad)}px`);
    placeGrip();
  }

  function saveTocWidth() {
    try {
      const px = parseInt(panel.style.getPropertyValue("--toc-w"), 10);
      if (px) localStorage.setItem(TOC_W_KEY, String(px));
      else localStorage.removeItem(TOC_W_KEY);
    } catch (e) {}
    savePanelWidth();
  }

  // The sidebars lie over the graph's edges, the folder tree on the left and
  // the page panel on the right, so a page opened from a link or from the
  // tree can land underneath one. Slide the graph sideways until the open
  // page sits in the part still showing.
  function revealBesidePanel(n) {
    if (!wide.matches || !panel || !state.shownIds.has(n.id)) return;
    const p = state.pos.get(n.id);
    if (!p) return;
    const left = coveredLeft(), right = canvas.clientWidth - panel.offsetWidth;
    const x = toScreen(p).x;
    if (right - left < 200 || (x > left + 40 && x < right - 40)) return;
    state.userView = true;             // stop the auto-fit from pulling it back
    const x0 = state.view.x, dx = (left + right) / 2 - x;
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) {
      state.view.x = x0 + dx; draw(); return;
    }
    const t0 = performance.now();
    (function frame(now) {
      const t = Math.min(1, (now - t0) / 260);
      state.view.x = x0 + dx * (1 - Math.pow(1 - t, 3));
      draw();
      if (t < 1) requestAnimationFrame(frame);
    })(t0);
  }

  // The page head is sticky; draw a rule under it only once content is
  // scrolling beneath, so an unscrolled page reads as one piece.
  function markStuck() {
    const head = panel && panel.querySelector(".panel-head");
    if (head && head.classList) head.classList.toggle("stuck", panel.scrollTop > 2);
  }

  if (panel) {
    panel.addEventListener("scroll", markStuck, { passive: true });
    // the reading position goes into the history entry once the scroll
    // settles: browsers limit how often an entry may be rewritten
    if (ROUTED) {
      panel.addEventListener("scroll", () => {
        clearTimeout(topTimer);
        topTimer = setTimeout(saveTop, 250);
      }, { passive: true });
      window.addEventListener("pagehide", saveTop);
    }
    // Esc closes the page, unless the key was meant for a form field.
    window.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !panel.classList.contains("open")) return;
      if (e.target && /^(INPUT|SELECT|TEXTAREA)$/.test(e.target.tagName)) return;
      select(null);
    });
  }

  if (grip && panel) {
    try {
      // the column's width first: the panel's saved width is clamped to
      // leave room for it
      const cw = parseInt(localStorage.getItem(TOC_W_KEY), 10);
      if (cw) panel.style.setProperty("--toc-w", `${Math.max(TOC_W_MIN, Math.min(cw, TOC_W_MAX))}px`);
      const saved = parseInt(localStorage.getItem(PANEL_KEY), 10);
      if (saved) setPanelWidth(saved);
    } catch (e) {}
    grip.tabIndex = 0;
    grip.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      grip.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing");
      const move = (ev) => setPanelWidth(wrap.getBoundingClientRect().right - ev.clientX);
      const up = () => {
        document.body.classList.remove("resizing");
        grip.removeEventListener("pointermove", move);
        grip.removeEventListener("pointerup", up);
        grip.removeEventListener("pointercancel", up);
        savePanelWidth();
      };
      grip.addEventListener("pointermove", move);
      grip.addEventListener("pointerup", up);
      grip.addEventListener("pointercancel", up);
    });
    grip.addEventListener("dblclick", () => { setPanelWidth(null); savePanelWidth(); });
    grip.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowLeft" ? 40 : e.key === "ArrowRight" ? -40 : 0;
      if (!step) return;
      e.preventDefault();
      setPanelWidth(panel.offsetWidth + step);
      savePanelWidth();
    });
    window.addEventListener("resize", placeGrip);
    if (wide.addEventListener) wide.addEventListener("change", placeGrip);
  }

  if (tocGrip && panel) {
    tocGrip.tabIndex = 0;
    tocGrip.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      tocGrip.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing-toc");
      const total = panel.offsetWidth;
      // where the column starts on screen; the boundary sits tocSeam() past its end
      const start = wrap.getBoundingClientRect().left + wrap.clientWidth - total + panel.clientLeft;
      const seam = tocSeam();
      const move = (ev) => setTocWidth(ev.clientX - start - seam, total);
      const up = () => {
        document.body.classList.remove("resizing-toc");
        tocGrip.removeEventListener("pointermove", move);
        tocGrip.removeEventListener("pointerup", up);
        tocGrip.removeEventListener("pointercancel", up);
        saveTocWidth();
      };
      tocGrip.addEventListener("pointermove", move);
      tocGrip.addEventListener("pointerup", up);
      tocGrip.addEventListener("pointercancel", up);
    });
    // double-click: the column's default width, the panel's edges still put
    tocGrip.addEventListener("dblclick", () => {
      const total = panel.offsetWidth;
      panel.style.removeProperty("--toc-w");
      setTocWidth(panelVar("--toc-w"), total);
      panel.style.removeProperty("--toc-w");
      saveTocWidth();
    });
    tocGrip.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowLeft" ? -20 : e.key === "ArrowRight" ? 20 : 0;
      if (!step) return;
      e.preventDefault();
      setTocWidth(panelVar("--toc-w") + step, panel.offsetWidth);
      saveTocWidth();
    });
  }

  // ---- contents ----------------------------------------------------------
  // A page's sections, the way Wikipedia lists them: a column beside the
  // text on a wide screen, bold on the section being read. "hide" folds the
  // column into a button left of the title that opens the same list as a
  // menu, and "move to sidebar" pins it back; the choice is kept per browser.
  // Narrower than tocWide there is no room for the column, so it is the menu.
  // The column is there on every page while pinned, (Top) alone on a page
  // with no headings, so the panel does not change width from page to page.
  const TOC_KEY = "dexio-toc";
  const tocWide = matchMedia("(min-width: 1000px)");
  const TOC_ICON = '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M9.5 7h10M9.5 12h10M9.5 17h10"></path>' +
    '<path d="M4.8 7h.4M4.8 12h.4M4.8 17h.4" stroke-width="2.8"></path></svg>';
  const TOC_CHEV = '<svg viewBox="0 0 10 10" aria-hidden="true"><path d="M2 3.5l3 3 3-3"></path></svg>';
  // Beside each heading on hover, as GitHub's anchors are: its section's address.
  const LINK_ICON = '<svg viewBox="0 0 16 16" aria-hidden="true">' +
    '<path d="M6.9 9.1a2.6 2.6 0 0 0 3.7 0l2.2-2.2a2.6 2.6 0 0 0-3.7-3.7l-.9.9"></path>' +
    '<path d="M9.1 6.9a2.6 2.6 0 0 0-3.7 0L3.2 9.1a2.6 2.6 0 0 0 3.7 3.7l.9-.9"></path></svg>';
  const toc = { heads: [], active: null, jump: null };

  function tocWanted() {
    try { return localStorage.getItem(TOC_KEY) !== "0"; } catch (e) { return true; }
  }

  // What the pinned column adds to the panel: its width and gap, less the
  // text's left padding it takes the place of (--toc-add in shell.html).
  function tocAdd() {
    if (!panel) return 0;
    const css = getComputedStyle(panel), px = (v) => parseFloat(css.getPropertyValue(v)) || 0;
    return px("--toc-w") + px("--toc-gap") - px("--panel-pad");
  }

  // How much wider the pinned column makes the panel now.
  function tocRoom() {
    return panel && panel.classList.contains("toc-pinned") ? tocAdd() : 0;
  }

  // Held back from the widest panel wherever the column can be pinned,
  // pinned or not (--toc-hold). Same 1000px as tocWide and the stylesheet;
  // a function, not tocWide, because the saved width is applied before
  // tocWide exists.
  function tocHold() {
    return matchMedia("(min-width: 1000px)").matches ? tocAdd() : 0;
  }

  function applyToc() {
    if (!panel || !panel.classList.toggle) return;
    panel.classList.toggle("toc-pinned", tocWide.matches && tocWanted());
    setTocMenu(false);
    placeGrip();
  }

  function slugOf(text) {
    return String(text).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "-")
      .replace(/^-+|-+$/g, "") || "section";
  }

  // Collect the open page's headings, give each an id, and write both lists:
  // the column and the menu. The page's top heading level is a section and
  // the next one down a subsection, whichever markdown levels those are.
  // In History the list is the days; for one revision, the blocks it changed,
  // or the headings of the page as it was (2026-09-27: it showed "(Top)"
  // alone there).
  function buildContents() {
    toc.heads = []; toc.active = null; toc.jump = null;
    const side = panel.querySelector(".toc-side"), menu = panel.querySelector(".toc-pop");
    // While a view loads, the contents keep what they had: the button left
    // of the title stays, and the lists stay until the new view's are
    // ready. Built from the placeholder, they had nothing in them, so the
    // button went on the way into History and came back with the
    // revisions, moving the title each time (Forrest, 2026-09-28). A page
    // opened afresh has empty lists, so it gets (Top) meanwhile.
    const loading = !!panel.querySelector(".panel-body > .page-loading");
    if (loading && side && side.childElementCount) { toc.active = ""; return; }
    const article = panel.querySelector(".panel-body > article.md, .panel-body .rev-content > article.md");
    const found = article ? [...article.querySelectorAll("h2, h3, h4, h5, h6")]
      : [...panel.querySelectorAll(".panel-body .hist-list > .rev-day, .panel-body .diff > .dh")];
    const top = article ? Math.min(...found.map((h) => +h.tagName[1])) : 0;
    // A page's headings arrive with their anchors (renderMarkdown), which
    // addresses name; the history's days and a revision's blocks get ids here.
    const used = new Set(found.map((h) => h.id).filter(Boolean));
    const linkable = ROUTED && article && hist.view === "page" && hist.n;
    for (const h of found) {
      const lvl = article ? +h.tagName[1] - top : 0;
      if (linkable && h.id && !h.querySelector(".h-link")) {
        h.insertAdjacentHTML("beforeend", `<a class="h-link" href="${escapeHtml(
          pageHref(hist.n.id, h.id.slice(2)))}" data-anchor="${escapeHtml(h.id)}" ` +
          `aria-label="Link to this section" title="Link to this section">${LINK_ICON}</a>`);
      }
      if (lvl > 1) continue;
      // a footnote's number in a heading is not part of its name
      let text = h.textContent;
      if (h.querySelector(".fn-ref")) {
        const c = h.cloneNode(true);
        c.querySelectorAll(".fn-ref").forEach((x) => x.remove());
        text = c.textContent;
      }
      text = text.trim();
      if (!text) continue;
      let id = h.id;
      if (!id) {
        id = "s-" + slugOf(text);
        for (let k = 2; used.has(id); k++) id = "s-" + slugOf(text) + "-" + k;
        used.add(id);
        h.id = id;
      }
      toc.heads.push({ el: h, id, lvl, text });
    }
    if (!loading) panel.classList.toggle("has-toc", toc.heads.length > 0);
    // The lists under the text: the sources no note cites, then backlinks.
    for (const [sel, id, text] of [[".panel-body > .page-sources", "s--sources", "Sources"],
                                   [".panel-body > nav.backlinks", "s--linked-from", "Linked from"]]) {
      const el = panel.querySelector(sel);
      if (!el) continue;
      el.id = id;
      toc.heads.push({ el, id, lvl: 0, text });
    }
    // subsections sit under their section; one that comes before any
    // section is listed on its own
    const items = [];
    for (const h of toc.heads) {
      const last = items[items.length - 1];
      if (h.lvl === 1 && last && last.lvl === 0) last.kids.push(h);
      else items.push({ ...h, kids: [] });
    }
    const href = (id) => escapeHtml(linkable ? pageHref(hist.n.id, id.slice(2)) : "#" + id);
    const link = (h) => `<a href="${href(h.id)}" data-toc="${h.id}">${escapeHtml(h.text)}</a>`;
    const list = `<ul><li><a href="${linkable ? escapeHtml(pageHref(hist.n.id)) : "#"}" ` +
      `data-toc="" class="on">(Top)</a></li>` +
      items.map((h) => !h.kids.length ? `<li>${link(h)}</li>` :
        `<li data-sec="${h.id}"><button type="button" class="toc-chev" aria-expanded="true" ` +
        `aria-label="Subsections of ${escapeHtml(h.text)}">${TOC_CHEV}</button>${link(h)}` +
        `<ul>${h.kids.map((c) => `<li>${link(c)}</li>`).join("")}</ul></li>`).join("") +
      `</ul>`;
    const head = (label) => `<div class="toc-top"><b>Contents</b>` +
      `<button type="button" class="toc-pin">${label}</button></div>`;
    if (side) side.innerHTML = head("hide") + list;
    if (menu) menu.innerHTML = head("move to sidebar") + list;
    toc.active = "";
  }

  function tocMenuOpen() {
    const menu = panel && panel.querySelector(".toc-pop");
    return !!(menu && !menu.hidden);
  }

  function setTocMenu(open) {
    const menu = panel && panel.querySelector(".toc-pop");
    const btn = panel && panel.querySelector(".toc-btn");
    if (!menu || !btn) return;
    menu.hidden = !open;
    btn.setAttribute("aria-expanded", String(open));
    if (open) keepInView(menu, menu.querySelector("a.on"));
  }

  // Scroll a list box, never the page, so the given entry shows.
  function keepInView(box, a) {
    if (!box || !a || !box.clientHeight) return;
    const r = a.getBoundingClientRect(), b = box.getBoundingClientRect();
    if (r.top < b.top + 44) box.scrollTop -= b.top + 44 - r.top;
    else if (r.bottom > b.bottom - 8) box.scrollTop += r.bottom - b.bottom + 8;
  }

  function headHeight() {
    const head = panel.querySelector(".panel-head");
    return head ? head.offsetHeight : 0;
  }

  // Scroll the page so a section's heading sits just under the pinned head.
  // A jump, as Wikipedia's contents make: smooth scrolling took Chrome two
  // seconds to cross a long page.
  // mark: false for a place that is not a section (a footnote), which then
  // leaves the contents marking whatever section the scroll lands in.
  function jumpTo(id, mark) {
    let top = 0;
    if (id) {
      const el = panel.querySelector("#" + CSS.escape(id));
      if (!el) return;
      top = el.getBoundingClientRect().top - panel.getBoundingClientRect().top +
            panel.scrollTop - headHeight() - 10;
    }
    top = Math.round(Math.max(0, Math.min(top, panel.scrollHeight - panel.clientHeight)));
    // A section near the end can't reach the top; remember which one was
    // asked for, so it is the one marked once the scroll stops short.
    toc.jump = mark === false ? null : { id, top };
    panel.scrollTop = top;
    spyContents();
  }

  // Mark the section being read: the last heading above the head's edge,
  // and (Top) until the page has scrolled at all.
  // `again` re-marks even when it has not changed, after the lists reflow.
  function spyContents(again) {
    if (!panel || toc.active === null) return;
    const line = panel.getBoundingClientRect().top + headHeight() + 24;
    let cur = "";
    for (const h of panel.scrollTop >= 1 ? toc.heads : []) {
      if (h.el.getBoundingClientRect().top <= line) cur = h.id; else break;
    }
    if (toc.jump && Math.abs(panel.scrollTop - toc.jump.top) <= 2) cur = toc.jump.id;
    if (cur === toc.active && !again) return;
    toc.active = cur;
    panel.querySelectorAll("[data-toc]").forEach((a) => {
      a.classList.toggle("on", a.dataset.toc === cur);
    });
    if (panel.classList.contains("toc-pinned")) {
      const side = panel.querySelector(".toc-side");
      keepInView(side, side && side.querySelector("a.on"));
    }
  }

  if (panel) {
    let spyQueued = false;
    panel.addEventListener("scroll", () => {
      if (spyQueued) return;
      spyQueued = true;
      requestAnimationFrame(() => { spyQueued = false; spyContents(); });
    }, { passive: true });

    panel.addEventListener("click", (e) => {
      const t = e.target;
      const chev = t.closest(".toc-chev");
      if (chev) {
        const id = chev.parentElement.dataset.sec;
        const shut = !chev.parentElement.classList.contains("shut");
        panel.querySelectorAll(`li[data-sec="${CSS.escape(id)}"]`).forEach((li) => {
          li.classList.toggle("shut", shut);
          li.querySelector(".toc-chev").setAttribute("aria-expanded", String(!shut));
        });
        placeGrip();            // folding can take the column's scrollbar away
        return;
      }
      // a footnote's number jumps to its note, the note's arrow back to where
      // it is cited, and what it lands on is lit until the next jump, as
      // Wikipedia lights a reference. The address stays the page's.
      const fn = t.closest("a[data-fn]");
      if (fn) {
        e.preventDefault();
        const id = fn.dataset.fn;
        jumpTo(id, false);
        panel.querySelectorAll(".fn-lit").forEach((x) => x.classList.remove("fn-lit"));
        const to = panel.querySelector("#" + CSS.escape(id));
        if (to) to.classList.add("fn-lit");
        return;
      }
      // an entry in the contents, or the link icon beside a heading
      const entry = t.closest("[data-toc], [data-anchor]");
      if (entry) {
        if (newTab(e)) return;
        e.preventDefault();
        const fromMenu = !!t.closest(".toc-pop");
        const id = entry.dataset.toc != null ? entry.dataset.toc : entry.dataset.anchor;
        jumpTo(id);
        // the address names the section jumped to, ready to copy; a jump is
        // not a new place in the history, so Back still leaves the page
        if (hist.view === "page" && hist.n) {
          hist.section = id.slice(2);
          setAddress(pageHref(hist.n.id, hist.section), "replace");
        }
        if (fromMenu) { setTocMenu(false); panel.querySelector(".toc-btn").focus({ preventScroll: true }); }
        return;
      }
      if (t.closest(".toc-pin")) {
        const pin = !!t.closest(".toc-pop");
        try { localStorage.setItem(TOC_KEY, pin ? "1" : "0"); } catch (err) {}
        applyToc();
        // focus stays on a control that is still showing, as with the tree
        const next = panel.querySelector(pin ? ".toc-side .toc-pin" : ".toc-btn");
        if (next) next.focus();
        spyContents(true);
        const n = state.selected && state.byId.get(state.selected);
        if (n && state.layout !== "radial") revealBesidePanel(n);
        return;
      }
      if (t.closest(".toc-btn")) setTocMenu(!tocMenuOpen());
    });

    // A click anywhere else closes the menu; so does Esc, before it would
    // close the page.
    window.addEventListener("pointerdown", (e) => {
      if (tocMenuOpen() && !e.target.closest(".toc-pop, .toc-btn")) setTocMenu(false);
    }, true);
    window.addEventListener("keydown", (e) => {
      if (e.key !== "Escape" || !tocMenuOpen()) return;
      e.stopPropagation();
      setTocMenu(false);
      const btn = panel.querySelector(".toc-btn");
      if (btn) btn.focus();
    }, true);

    if (tocWide.addEventListener) tocWide.addEventListener("change", applyToc);
    applyToc();
  }

  // ---- one folder --------------------------------------------------------
  // Forrest, 2026-09-28: "we need an ability to somehow filter down the nodes
  // to just a folder" (option A of four), then, the same evening, "like macos
  // where if they click the > it expands, but if they double click they drill
  // into the folder", with "a dropdown to the left of the folder pill that
  // determines whether or not it shows linked pages", and "the Share should be
  // aware of which folder is currently active".
  //
  // Then, 2026-09-29: "can we actually have clicking a folder in the left
  // sidebar expand/collapse it? and can we introduce an icon at the right side
  // of the row that drills into it?"
  //
  // Drilling into a folder (the arrow at the right of its row in the tree, or
  // Enter on the row) narrows everything to it: the graph shows its pages and its
  // subfolders', laid out afresh as a search's are (filterShown), the tree lists
  // only what is inside it with a row back up, and a search searches only it.
  // Over the graph, beside Layout: Show (Folder only, or With linked pages: the
  // pages elsewhere that link to or from it, faded; remembered per browser),
  // then the folder pill, its path with each parent a step back up, and × for
  // the whole wiki. The header's Share shares the folder drilled into. The page
  // panel and page links still reach the whole wiki. The address carries
  // ?folder=, so Back undoes a drill, a reload keeps it and a link carries it.
  const SHOW_KEY = "dexio-folder-show";
  try { state.linked = localStorage.getItem(SHOW_KEY) === "linked"; } catch (e) { /* private mode */ }
  const SHOW_INFO = {
    folder: { name: "Folder only", desc: "The pages in this folder and the folders inside it" },
    linked: { name: "With linked pages",
              desc: "Also the pages elsewhere that link to or from it, faded" },
  };
  const FOLDER_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M1.8 4.2c0-.7.5-1.2 1.2-1.2h3.1' +
    'l1.5 1.6h5.4c.7 0 1.2.5 1.2 1.2v6.2c0 .7-.5 1.2-1.2 1.2H3c-.7 0-1.2-.5-1.2-1.2z"></path></svg>';
  const X_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M4.5 4.5l7 7M11.5 4.5l-7 7"></path></svg>';
  const UP_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M10 3.5 5.5 8l4.5 4.5"></path></svg>';
  const DRILL_SVG = '<svg viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8h9.5M8.5 4 12.5 8l-4 4"></path></svg>';
  const MENU_CHEV = '<svg class="sw-chev" viewBox="0 0 16 16" aria-hidden="true">' +
    '<path d="M5 6.2 8 3.2l3 3M5 9.8l3 3 3-3"></path></svg>';
  let bar = null;                   // {show, button, menu, chip}

  function buildBar(pick) {
    const show = document.createElement("div");
    show.id = "show-pick";
    show.innerHTML = `<button id="show-button" type="button" aria-haspopup="menu" ` +
      `aria-expanded="false" aria-controls="show-menu" title="Which pages the folder shows">` +
      `<span class="lo-cap">Show</span><span class="lo-now"></span>${MENU_CHEV}</button>` +
      `<div id="show-menu" role="menu" aria-label="Show" hidden></div>`;
    const chip = document.createElement("div");
    chip.id = "focus-chip";
    chip.setAttribute("role", "group");
    pick.appendChild(show);
    pick.appendChild(chip);
    const button = show.querySelector ? show.querySelector("#show-button") : null;
    const menu = show.querySelector ? show.querySelector("#show-menu") : null;
    bar = { show, chip, button, menu };
    if (!button || !menu) return;
    const items = () => [...menu.querySelectorAll(".sw-item")];
    const open = (last) => {
      menu.innerHTML = '<div class="sw-head">Show</div>' + ["folder", "linked"].map((id) => {
        const on = (id === "linked") === state.linked;
        return `<button type="button" class="sw-item" role="menuitemradio" tabindex="-1" ` +
          `data-show="${id}" aria-checked="${on}"><span class="lo-text"><span class="sw-name">` +
          `${SHOW_INFO[id].name}</span><span class="lo-desc">${SHOW_INFO[id].desc}</span></span>${CHECK}</button>`;
      }).join("");
      menu.hidden = false;
      button.setAttribute("aria-expanded", "true");
      const list = items();
      const t = last ? list[list.length - 1] : list.find((i) => i.getAttribute("aria-checked") === "true");
      if (t) t.focus();
    };
    const close = (refocus) => {
      if (menu.hidden) return;
      menu.hidden = true;
      button.setAttribute("aria-expanded", "false");
      if (refocus) button.focus();
    };
    button.addEventListener("click", () => (menu.hidden ? open(false) : close(true)));
    button.addEventListener("keydown", (e) => {
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      e.preventDefault();
      open(e.key === "ArrowUp");
    });
    menu.addEventListener("click", (e) => {
      const item = e.target.closest("[data-show]");
      if (!item) return;
      close(true);
      setLinked(item.dataset.show === "linked");
    });
    menu.addEventListener("keydown", (e) => {
      const list = items(), i = list.indexOf(document.activeElement);
      const go = (j) => { e.preventDefault(); list[(j + list.length) % list.length].focus(); };
      if (e.key === "ArrowDown") go(i + 1);
      else if (e.key === "ArrowUp") go(i - 1);
      else if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); close(true); }
      else if (e.key === "Tab") close(false);
    });
    window.addEventListener("pointerdown", (e) => { if (!show.contains(e.target)) close(false); });
  }

  function setLinked(on) {
    if (on === state.linked) return;
    state.linked = on;
    try { localStorage.setItem(SHOW_KEY, on ? "linked" : "folder"); } catch (e) { /* ok */ }
    state.userView = false;
    refilter();
    renderChip();
  }

  // Whether any page in `folder` links to or from a page outside it.
  function linksOut(folder) {
    const inside = (id) => { const n = state.byId.get(id); return !!n && inFolder(n, folder); };
    return state.all.links.some((l) => inside(l.source) !== inside(l.target) &&
                                       state.byId.has(l.source) && state.byId.has(l.target));
  }

  // The Show menu and the folder pill, or neither with no folder drilled into.
  function renderChip() {
    const pick = document.getElementById("layout-pick");
    if (!pick || (state.focus == null && !bar)) { shareHere(); return; }
    if (!bar) buildBar(pick);
    const on = state.focus != null;
    // Show only when With linked pages would add something: a link between the
    // folder and a page outside it among the pages this reader has. Someone a
    // single folder or page is shared with has nothing outside it, so the menu
    // would do nothing (Forrest, 2026-09-28); nor does a folder no page links.
    bar.show.hidden = !on || !linksOut(state.focus);
    bar.chip.hidden = !on;
    shareHere();
    if (!on) { bar.chip.innerHTML = ""; return; }
    if (bar.button && bar.button.querySelector) {
      const now = bar.button.querySelector(".lo-now");
      if (now) now.textContent = SHOW_INFO[state.linked ? "linked" : "folder"].name;
      bar.button.setAttribute("aria-label",
        `Show: ${SHOW_INFO[state.linked ? "linked" : "folder"].name}. Change`);
    }
    // The path, each parent a step back up, as a Finder window's path bar.
    let at = "";
    const parts = state.focus.split("/").map((seg, i, all) => {
      at = at ? at + "/" + seg : seg;
      return i === all.length - 1
        ? `<span class="fc-seg fc-here" aria-current="location">${escapeHtml(seg)}</span>`
        : `<button type="button" class="fc-seg" data-to="${escapeHtml(at)}" ` +
          `title="Go up to ${escapeHtml(at)}">${escapeHtml(seg)}</button><span class="fc-sep">/</span>`;
    }).join("");
    bar.chip.setAttribute("aria-label", "Folder " + state.focus);
    bar.chip.innerHTML = FOLDER_SVG + `<span class="fc-path">${parts}</span>` +
      `<button type="button" class="fc-x" aria-label="Back to the whole wiki" ` +
      `title="Back to the whole wiki">${X_SVG}</button>`;
    const x = bar.chip.querySelector && bar.chip.querySelector(".fc-x");
    if (x) x.onclick = () => setFocus(null);
    if (bar.chip.querySelectorAll) {
      bar.chip.querySelectorAll("[data-to]").forEach((b) => { b.onclick = () => setFocus(b.dataset.to); });
    }
  }

  // What the header's Share shares: the page open in the panel, else the
  // folder drilled into, else the whole wiki. It is the only Share on the
  // screen; the page head had its own until 2026-09-28 (Forrest: "it's weird
  // seeing the share button above another share button like this").
  // static/share.js reads window.dexio.shareTarget.
  function shareTarget() {
    if (state.selected != null && panel.classList.contains("open")) {
      const n = state.byId.get(state.selected);
      return { kind: "page", path: state.selected, title: (n && n.title) || state.selected };
    }
    if (state.focus) return { kind: "folder", path: state.focus, title: state.focus };
    return { kind: "wiki", path: "", title: "" };
  }

  // The header's Share names what it will share in its tooltip.
  function shareHere() {
    const b = document.getElementById("share-wiki");
    if (!b || !b.setAttribute) return;
    const t = shareTarget();
    const what = t.kind === "page" ? `Share the page ${t.title}`
      : t.kind === "folder" ? `Share the folder ${state.focus}` : "Share the wiki";
    b.setAttribute("title", what);
    b.setAttribute("aria-label", what);
  }

  // Drill into `folder` (null for the whole wiki). how: how the address
  // changes, "push" by default, "none" when it already says so (a reload,
  // Back). A folder with no pages, from an old link, is no folder.
  function setFocus(folder, how) {
    folder = folder ? String(folder).replace(/^\/+|\/+$/g, "") : null;
    if (folder && !state.all.nodes.some((n) => inFolder(n, folder))) folder = null;
    if (folder === state.focus) { renderChip(); return; }
    state.focus = folder;
    if (folder) {                   // what is inside shows open, as far as it did
      let p = "";
      for (const part of folder.split("/")) { p = p ? p + "/" + part : part; state.expanded.add(p); }
    }
    state.userView = false;         // frame what is shown now
    refilter();
    renderTree();
    renderChip();
    if (ROUTED && how !== "none") {
      const open = panel && panel.classList.contains("open") && state.selected;
      setAddress(open ? pageHref(open, hist.section) : wikiHref(), how || "push",
                 open || null);
    }
  }

  // ---- folder tree -------------------------------------------------------
  // Folders and pages as a file explorer beside the graph. Clicking a page
  // opens it; hovering a folder picks out its pages in the graph; the search
  // box filters both. Open by default on wide screens (remembered per
  // browser); a drawer on phones.
  const TREE_KEY = "dexio-tree";
  const CHEV = '<svg class="chev" viewBox="0 0 10 10" aria-hidden="true">' +
               '<path d="M2 3.5l3 3 3-3"></path></svg>';

  function buildTree(nodes) {
    const root = { name: "", path: "", dirs: new Map(), pages: [] };
    for (const n of nodes) {
      let d = root;
      for (const part of (n.folder ? n.folder.split("/") : [])) {
        if (!d.dirs.has(part)) {
          d.dirs.set(part, { name: part, path: d.path ? d.path + "/" + part : part,
                             dirs: new Map(), pages: [] });
        }
        d = d.dirs.get(part);
      }
      d.pages.push(n);
    }
    return root;
  }

  // Small wikis open fully; large ones start with their top-level folders closed.
  function defaultExpanded(root, size) {
    const open = new Set();
    if (size > 150) return open;
    (function walk(d) {
      for (const c of d.dirs.values()) { open.add(c.path); walk(c); }
    })(root);
    return open;
  }

  const label = (n) => n.title || n.id.split("/").pop();
  const byName = (a, b) => a.localeCompare(b, undefined, { sensitivity: "base", numeric: true });

  function countIn(d) {
    let c = d.pages.filter(matchesQuery).length;
    for (const s of d.dirs.values()) c += countIn(s);
    return c;
  }

  function treeHtml(d) {
    let out = "";
    for (const c of [...d.dirs.values()].sort((a, b) => byName(a.name, b.name))) {
      const count = countIn(c);
      if (!count) continue;
      const open = !!state.query || state.expanded.has(c.path);
      // A click on the row opens and closes it; the arrow at its right drills
      // into it ("one folder"). The arrow is a sibling, since a button cannot
      // hold another; it is out of the tab order because Enter on the row
      // drills in too.
      const path = escapeHtml(c.path);
      out += `<li><div class="dir-line"><button type="button" class="row dir" ` +
             `data-dir="${path}" aria-expanded="${open}" title="${path}">` +
             `<span class="twisty">${CHEV}</span>` +
             `<span class="name">${escapeHtml(c.name)}</span><span class="count">${count}</span></button>` +
             `<button type="button" class="drill" data-drill="${path}" tabindex="-1" ` +
             `aria-label="Show only ${path}" title="Show only ${path}">${DRILL_SVG}</button></div>` +
             `${open ? treeHtml(c) : ""}</li>`;
    }
    const pages = d.pages.filter(matchesQuery).sort((a, b) => byName(label(a), label(b)));
    for (const p of pages) {
      const colour = nodeColor(p);
      out += `<li><a href="${escapeHtml(pageHref(p.id))}" ` +
             `class="row page${p.id === state.selected ? " on" : ""}" ` +
             `data-id="${escapeHtml(p.id)}" title="${escapeHtml(p.id)}">` +
             `<span class="dot" style="background:${escapeHtml(colour)}"></span>` +
             `<span class="name">${escapeHtml(label(p))}</span></a></li>`;
    }
    return out ? `<ul>${out}</ul>` : "";
  }

  function renderTree() {
    if (!treeList || !state.tree) return;
    // Drilled into a folder, the tree lists only what is inside it, under a
    // row back up to its parent (or to every folder).
    let root = state.tree, up = "";
    if (state.focus) {
      let d = state.tree;
      for (const part of state.focus.split("/")) d = d && d.dirs.get(part);
      if (d) {
        root = d;
        const parent = state.focus.includes("/") ? state.focus.slice(0, state.focus.lastIndexOf("/")) : "";
        up = `<button type="button" class="row up" data-up="${escapeHtml(parent)}" ` +
             `title="Up to ${parent ? escapeHtml(parent) : "every folder"}">${UP_SVG}` +
             `<span class="name">${parent ? escapeHtml(parent.split("/").pop()) : "All folders"}</span></button>` +
             `<div class="tree-here">${FOLDER_SVG}<span>${escapeHtml(d.name)}</span></div>`;
      }
    }
    treeList.innerHTML = up + (treeHtml(root) ||
      `<p class="empty">${state.query ? "No pages match." : "No pages yet."}</p>`);
  }

  // Open the folders above a page and bring its row into view.
  function showInTree(n) {
    if (!treeEl || !state.tree) return;
    if (n && n.folder) {
      let p = "";
      for (const part of n.folder.split("/")) {
        p = p ? p + "/" + part : part;
        state.expanded.add(p);
      }
    }
    renderTree();
    if (!n || !treeEl.querySelector) return;
    const row = treeEl.querySelector(`.row.on`);
    if (row && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
  }

  function treeOpen() {
    return !!(wrap && wrap.classList.contains && wrap.classList.contains("tree-open"));
  }

  function setTree(open, remember) {
    if (!treeEl || !wrap || !wrap.classList.toggle) return;
    wrap.classList.toggle("tree-open", open);
    if (treeOpenBtn && treeOpenBtn.setAttribute) treeOpenBtn.setAttribute("aria-expanded", String(open));
    // Keep keyboard focus on a control that is still showing: closing from
    // inside the tree lands on the button that reopens it, and opening with
    // that button lands on the one that hides it again.
    const active = document.activeElement;
    if (active && !open && treeOpenBtn && treeEl.contains && treeEl.contains(active)) treeOpenBtn.focus();
    if (active && open && active === treeOpenBtn && treeCloseBtn) treeCloseBtn.focus();
    if (!open && (state.hoverFolder != null || state.hover)) {
      state.hoverFolder = null; state.hover = null; draw();
    }
    if (remember) { try { localStorage.setItem(TREE_KEY, open ? "1" : "0"); } catch (e) {} }
    placeGrip();
  }

  function treeWanted() {
    if (!wide.matches) return false;      // phones: a drawer, closed until asked for
    let saved = null;
    try { saved = localStorage.getItem(TREE_KEY); } catch (e) {}
    if (saved) return saved === "1";
    // Embedded in another page (the demo on dexio.wiki), the graph is the
    // point and the frame is small: start with the tree closed.
    let framed = false;
    try { framed = window.self !== window.top; } catch (e) { framed = true; }
    return !framed;
  }

  if (treeEl && treeOpenBtn) {
    treeOpenBtn.addEventListener("click", () => setTree(true, wide.matches));
    if (treeCloseBtn) treeCloseBtn.addEventListener("click", () => setTree(false, wide.matches));
    // Cmd+\ (Ctrl+\ elsewhere) shows or hides it from anywhere, in a field too.
    const mac = /Mac|iPhone|iPad/.test((typeof navigator !== "undefined" &&
                                        (navigator.platform || navigator.userAgent)) || "");
    const keys = mac ? "⌘\\" : "Ctrl+\\";
    treeOpenBtn.title = `Show folders (${keys})`;
    if (treeCloseBtn) treeCloseBtn.title = `Hide folders (${keys})`;
    window.addEventListener("keydown", (e) => {
      if (e.key !== "\\" || !(e.metaKey || e.ctrlKey) || e.altKey || e.shiftKey) return;
      e.preventDefault();
      setTree(!treeOpen(), wide.matches);
    });
    if (wide.addEventListener) wide.addEventListener("change", () => setTree(treeWanted()));
    // Folders (Forrest, 2026-09-29, replacing the Finder-style double-click of
    // 2026-09-28): a click anywhere on the row opens or closes it, and the
    // arrow at the row's right drills into it (shown on hover or keyboard
    // focus, always on a touch screen). From the keyboard: Enter drills in,
    // Space or the arrow keys open and close.
    const toggleDir = (p, want) => {
      const open = state.expanded.has(p);
      if (want === open) return;
      if (open) state.expanded.delete(p); else state.expanded.add(p);
      renderTree();
      const again = treeEl.querySelector(`[data-dir="${CSS.escape(p)}"]`);
      if (again) again.focus();
    };
    treeEl.addEventListener("click", (e) => {
      const up = e.target.closest("[data-up]");
      if (up) { setFocus(up.dataset.up || null); return; }
      const drill = e.target.closest("[data-drill]");
      if (drill) { setFocus(drill.dataset.drill); return; }
      const dir = e.target.closest("[data-dir]");
      if (dir) { toggleDir(dir.dataset.dir); return; }
      const page = e.target.closest("[data-id]");
      if (!page || newTab(e)) return;
      e.preventDefault();
      const n = state.byId.get(page.dataset.id);
      if (!n) return;
      if (!wide.matches) setTree(false);  // the drawer gets out of the way
      select(n);
    });
    treeEl.addEventListener("keydown", (e) => {
      const dir = e.target.closest && e.target.closest("[data-dir]");
      if (!dir || e.altKey || e.metaKey || e.ctrlKey) return;
      const p = dir.dataset.dir;
      if (e.key === "Enter") { e.preventDefault(); setFocus(p); }
      else if (e.key === "ArrowRight") { e.preventDefault(); toggleDir(p, true); }
      else if (e.key === "ArrowLeft") { e.preventDefault(); toggleDir(p, false); }
    });
    // Hover a folder to pick out its pages; hover a page to name it in the
    // graph. Mouse only: a tap on a phone would leave the graph dimmed.
    treeEl.addEventListener("pointerover", (e) => {
      if (e.pointerType !== "mouse") return;
      const dir = e.target.closest("[data-dir], [data-drill]");
      const page = e.target.closest("[data-id]");
      const folder = dir ? (dir.dataset.dir || dir.dataset.drill) : null;
      const hover = page ? page.dataset.id : null;
      if (folder !== state.hoverFolder || hover !== state.hover) {
        state.hoverFolder = folder; state.hover = hover; draw();
      }
    });
    treeEl.addEventListener("pointerleave", () => {
      if (state.hoverFolder == null && !state.hover) return;
      state.hoverFolder = null; state.hover = null; draw();
    });
    // On a phone, touching the graph closes the drawer.
    canvas.addEventListener("pointerdown", () => { if (!wide.matches && treeOpen()) setTree(false); });
    setTree(treeWanted());
  }

  // ---- folder tree width -------------------------------------------------
  // The tree's right edge drags (or moves with the arrow keys) to resize it,
  // as the page panel's left edge does. The width is kept per browser, and a
  // double-click on the edge goes back to the default. It lives in --tree-w
  // on #wrap, which the layout menu and the footer hint read to stay beside
  // the tree. Wide screens only: on a phone the tree is a fixed-width drawer.
  const treeGrip = document.getElementById("tree-grip");
  const TREE_W_KEY = "dexio-tree-w";
  const TREE_MIN = 180, TREE_MAX = 640;

  function setTreeWidth(px) {
    if (!wrap || !wrap.style || !wrap.style.setProperty) return;
    if (px) {
      px = Math.round(Math.max(TREE_MIN, Math.min(px, TREE_MAX)));
      wrap.style.setProperty("--tree-w", `${px}px`);
    } else {
      wrap.style.removeProperty("--tree-w");
    }
    if (treeGrip && treeEl) treeGrip.setAttribute("aria-valuenow", String(treeEl.offsetWidth));
  }

  function saveTreeWidth() {
    try {
      const px = parseInt(wrap.style.getPropertyValue("--tree-w"), 10);
      if (px) localStorage.setItem(TREE_W_KEY, String(px));
      else localStorage.removeItem(TREE_W_KEY);
    } catch (e) {}
  }

  if (treeGrip && treeGrip.setAttribute && treeEl) {
    treeGrip.tabIndex = 0;
    treeGrip.setAttribute("aria-valuemin", String(TREE_MIN));
    treeGrip.setAttribute("aria-valuemax", String(TREE_MAX));
    let saved = null;
    try { saved = parseInt(localStorage.getItem(TREE_W_KEY), 10); } catch (e) {}
    setTreeWidth(saved || null);
    treeGrip.addEventListener("pointerdown", (e) => {
      if (e.button !== 0) return;
      e.preventDefault();
      treeGrip.setPointerCapture(e.pointerId);
      document.body.classList.add("resizing-tree");
      // never so wide that less than GRAPH_MIN of the graph shows beside it
      const move = (ev) => setTreeWidth(Math.min(ev.clientX - wrap.getBoundingClientRect().left,
                                                 wrap.clientWidth - GRAPH_MIN));
      const up = () => {
        document.body.classList.remove("resizing-tree");
        treeGrip.removeEventListener("pointermove", move);
        treeGrip.removeEventListener("pointerup", up);
        treeGrip.removeEventListener("pointercancel", up);
        saveTreeWidth();
      };
      treeGrip.addEventListener("pointermove", move);
      treeGrip.addEventListener("pointerup", up);
      treeGrip.addEventListener("pointercancel", up);
    });
    treeGrip.addEventListener("dblclick", () => { setTreeWidth(null); saveTreeWidth(); });
    treeGrip.addEventListener("keydown", (e) => {
      const step = e.key === "ArrowRight" ? 40 : e.key === "ArrowLeft" ? -40 : 0;
      if (!step) return;
      e.preventDefault();
      setTreeWidth(Math.min(treeEl.offsetWidth + step, wrap.clientWidth - GRAPH_MIN));
      saveTreeWidth();
    });
  }

  // ---- search box --------------------------------------------------------
  // Centred in the header. Typing dims the pages that do not match and
  // filters the tree, as it always has, and lists the best matches under the
  // box: Enter or a click opens the highlighted one, the arrow keys move, Esc
  // clears (Forrest, 2026-09-26, option D of four mockups). Cmd+F (Ctrl+F
  // elsewhere) gets there from anywhere (Forrest, 2026-09-26, replacing Cmd+K
  // as the advertised key; Cmd+K still works). Pressed again while the box has
  // focus, it is left to the browser, so its own find-in-page is one more
  // press away.
  const findEl = document.getElementById("find");
  const findList = document.getElementById("find-list");
  const findClear = document.getElementById("find-clear");
  const findKeys = document.getElementById("find-keys");
  const FIND_MAX = 8;
  const find = { items: [], active: 0 };
  const MAC = /Mac|iPhone|iPad/.test((typeof navigator !== "undefined" &&
                                      (navigator.platform || navigator.userAgent)) || "");

  function findShown() { return !!findList && findList.hidden === false; }

  function closeFind() {
    find.items = [];
    if (!findList || findList.hidden) return;
    findList.hidden = true;
    findList.innerHTML = "";
    searchEl.setAttribute("aria-expanded", "false");
    searchEl.setAttribute("aria-activedescendant", "");
  }

  function renderFind() {
    if (!findList || !searchEl) return;
    if (!state.query || document.activeElement !== searchEl) { closeFind(); return; }
    const all = rankedMatches();
    const dirs = matchedFolders();
    find.items = findRows(all, dirs);
    if (find.active >= find.items.length) find.active = 0;
    const plural = (k, one) => (k === 1 ? `1 ${one}` : `${k} ${one}s`);
    const head = [dirs.length ? plural(dirs.length, "folder") : "",
                  all.length ? plural(all.length, "page") : ""].filter(Boolean).join(" · ");
    let html = `<div class="find-head">${head || "Nothing matches"}</div>`;
    find.items.forEach((x, i) => {
      const row = `<div class="find-item${x.dir != null ? " find-dir" : ""}${i === find.active ? " on" : ""}" ` +
                  `id="find-${i}" role="option" aria-selected="${i === find.active}" data-i="${i}" `;
      const enter = `<span class="find-enter" aria-hidden="true">↵</span></div>`;
      if (x.dir != null) {          // a folder: its icon, its name, where it is and how full
        const up = x.dir.includes("/") ? x.dir.slice(0, x.dir.lastIndexOf("/")) : "";
        html += row + `title="${escapeHtml(x.dir)}">` +
                `<span class="find-folder" style="color:${escapeHtml(folderColor(x.dir))}">${FOLDER_SVG}</span>` +
                `<span class="find-title">${escapeHtml(x.name)}</span>` +
                `<span class="find-path">${escapeHtml((up ? up + " · " : "") + plural(x.pages, "page"))}</span>` +
                enter;
        return;
      }
      html += row + `title="${escapeHtml(x.id)}">` +
              `<span class="dot" style="background:${escapeHtml(nodeColor(x))}"></span>` +
              `<span class="find-title">${escapeHtml(label(x))}</span>` +
              `<span class="find-path">${escapeHtml(x.folder || "")}</span>` + enter;
    });
    const shown = find.items.filter((x) => x.dir == null).length;
    if (all.length > shown) {
      html += `<div class="find-more">${all.length - shown} more in the graph</div>`;
    }
    if (find.items.length) {
      html += `<div class="find-foot">↑↓ to move · Enter to open · Esc to clear</div>`;
    }
    findList.innerHTML = html;
    findList.hidden = false;
    searchEl.setAttribute("aria-expanded", "true");
    searchEl.setAttribute("aria-activedescendant", find.items.length ? `find-${find.active}` : "");
  }

  // Move the highlight without redrawing the list under the pointer.
  function setActive(i) {
    if (!find.items.length) return;
    find.active = (i + find.items.length) % find.items.length;
    findList.querySelectorAll(".find-item").forEach((row) => {
      const on = +row.dataset.i === find.active;
      row.classList.toggle("on", on);
      row.setAttribute("aria-selected", String(on));
      if (on && row.scrollIntoView) row.scrollIntoView({ block: "nearest" });
    });
    searchEl.setAttribute("aria-activedescendant", `find-${find.active}`);
  }

  // The query stays, so the graph and the tree stay filtered; the list and
  // the focus go, so Esc now closes the page. A folder is drilled into, as a
  // the arrow on its row in the tree does, and the query goes so all of it shows.
  function openFound(n) {
    if (!n) return;
    closeFind();
    searchEl.blur();
    if (n.dir != null) {
      searchEl.value = "";
      runSearch();
      if (!wide.matches && treeOpen()) setTree(false);
      setFocus(n.dir);
      return;
    }
    if (panel) panel.scrollTop = 0;
    if (!wide.matches && treeOpen()) setTree(false);
    select(n);
  }

  function runSearch() {
    state.query = searchEl.value.trim();
    find.active = 0;
    if (findEl && findEl.classList) findEl.classList.toggle("has-text", !!searchEl.value);
    if (findClear) findClear.hidden = !searchEl.value;
    textSearch(); draw(); renderTree(); renderFind(); scheduleFilter(); markTerms();
  }

  function clearSearch() {
    searchEl.value = "";
    runSearch();
  }

  if (searchEl && searchEl.setAttribute) {
    const keys = MAC ? "⌘F" : "Ctrl F";
    if (findKeys) findKeys.textContent = keys;
    searchEl.setAttribute("aria-keyshortcuts", MAC ? "Meta+F Meta+K" : "Control+F Control+K");
    searchEl.title = `Search pages and folders (${MAC ? "⌘F" : "Ctrl+F"})`;
    searchEl.addEventListener("input", runSearch);
    searchEl.addEventListener("focus", renderFind);
    searchEl.addEventListener("blur", closeFind);
    searchEl.addEventListener("keydown", (e) => {
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        if (!findShown()) renderFind();
        e.preventDefault();
        setActive(find.active + (e.key === "ArrowDown" ? 1 : -1));
      } else if (e.key === "Enter") {
        e.preventDefault();
        if (!findShown()) renderFind();
        openFound(find.items[find.active]);
      } else if (e.key === "Escape") {
        e.preventDefault();
        if (searchEl.value) clearSearch(); else searchEl.blur();
      }
    });
    window.addEventListener("keydown", (e) => {
      const key = (e.key || "").toLowerCase();
      if ((key !== "f" && key !== "k") || !(MAC ? e.metaKey : e.ctrlKey) ||
          e.altKey || e.shiftKey) return;
      // a second Cmd+F in the box is the browser's find-in-page
      if (key === "f" && document.activeElement === searchEl) return;
      e.preventDefault();
      searchEl.focus();
      searchEl.select();
    });
    if (findClear) {
      // pressing it must not take focus from the field, or the list closes
      findClear.addEventListener("mousedown", (e) => e.preventDefault());
      findClear.addEventListener("click", () => { clearSearch(); searchEl.focus(); });
    }
    if (findList) {
      findList.addEventListener("mousedown", (e) => e.preventDefault());
      findList.addEventListener("mousemove", (e) => {
        const row = e.target.closest(".find-item");
        if (row && +row.dataset.i !== find.active) setActive(+row.dataset.i);
      });
      findList.addEventListener("click", (e) => {
        const row = e.target.closest(".find-item");
        if (row) openFound(find.items[+row.dataset.i]);
      });
    }
  }
  state.layout = savedLayout();
  showLayout(state.layout);
  // The canvas fills whatever the header leaves, and the header's height can
  // change without the window resizing (it wraps once the counts load).
  if (window.ResizeObserver) new ResizeObserver(() => resize()).observe(canvas);
  else window.addEventListener("resize", resize);
  window.addEventListener("dexio:theme", () => { readTheme(); draw(); renderTree(); });

  window.dexio = { load, resize, select };
  window.dexio.openPath = openPath;          // the switcher opens addresses with it
  window.dexio.setFocus = setFocus;          // ?folder= in an address (render.py APP_JS)
  window.dexio.focused = () => state.focus;  // what the header's Share shares (share.js)
  window.dexio.shareTarget = shareTarget;    // the page open, else the folder (share.js)
  readTheme();
  resize();
})();
