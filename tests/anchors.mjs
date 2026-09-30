/* The heading anchors graph.js gives each page, for test_deep_links.py to hold
 * against parse.heading_anchors: an address the server hands out must name the
 * id the web view puts on that heading.
 * Reads [{text, title}] as JSON on stdin; prints [[anchor, ...], ...].
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const src = readFileSync(join(here, "../src/dexio/static/graph.js"), "utf8");

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

const exported = "window.dexio = { load, resize, select };";
if (!src.includes(exported)) throw new Error("graph.js no longer exports as expected");
new Function(src.replace(exported, "window.dexio = { renderMarkdown, headingSlug };"))();
const { renderMarkdown } = globalThis.window.dexio;

const docs = JSON.parse(readFileSync(0, "utf8"));
const out = docs.map(({ text, title }) =>
  [...renderMarkdown(text, "page", title).matchAll(/<h[1-6] id="s-([^"]*)"/g)].map((m) => m[1]));
process.stdout.write(JSON.stringify(out));
