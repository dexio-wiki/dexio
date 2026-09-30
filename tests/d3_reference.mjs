/* Regenerates tests/d3_reference.json: a run of d3-force itself, set up the
 * way Obsidian sets it up, for test_layout.mjs to hold graph.js's Force to.
 * Not run by the test suite (it needs d3-force from npm):
 *   npm install d3-force@3.0.0 && node tests/d3_reference.mjs
 */
import { writeFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { forceSimulation, forceX, forceY, forceLink, forceManyBody, forceCollide } from "d3-force";

const here = dirname(fileURLToPath(import.meta.url));
const STEPS = 300, CX = 600, CY = 400;

// 60 pages, 180 links, one index page linking to most, starting positions
// scattered over a disc (a fixed generator, so the file is reproducible)
let seed = 11;
const rand = () => ((seed = (1664525 * seed + 1013904223) % 4294967296) / 4294967296);
const ids = ["index"];
for (let i = 1; i < 60; i++) ids.push(`f${i % 6}/p${i}`);
const edges = new Set();
for (let i = 1; i < 50; i++) if (rand() < 0.8) edges.add(`index>${ids[i]}`);
while (edges.size < 180) {
  const a = ids[1 + Math.floor(rand() * 59)], b = ids[Math.floor(rand() * 50)];
  if (a !== b) edges.add(`${a}>${b}`);
}
const links = [...edges].map((k) => k.split(">"));
const start = ids.map(() => {
  const a = 2 * Math.PI * rand(), r = Math.sqrt(rand()) * 500;
  return [CX + r * Math.cos(a), CY + r * Math.sin(a)];
});

const nodes = ids.map((id, i) => ({ id, x: start[i][0], y: start[i][1], vx: 0, vy: 0 }));
const link = forceLink(links.map(([source, target]) => ({ source, target }))).id((d) => d.id).distance(250);
const sim = forceSimulation(nodes)
  .force("x", forceX(CX).strength(0.1)).force("y", forceY(CY).strength(0.1))
  .force("link", link)
  .force("charge", forceManyBody().strength(-1000).distanceMin(30))
  .force("collide", forceCollide(60).strength(0.5))
  .stop();
const byDefault = link.strength();
link.strength((l, i, ls) => 1 * byDefault(l, i, ls));
sim.tick(STEPS);

writeFileSync(join(here, "d3_reference.json"), JSON.stringify({
  generated: "d3-force 3.0.0, forces as Obsidian 1.13.8 sets them, " + STEPS + " steps",
  centre: [CX, CY], steps: STEPS, ids, links, start,
  end: nodes.map((d) => [d.x, d.y]),
}) + "\n");
console.log("wrote tests/d3_reference.json");
