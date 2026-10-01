"""A picture of a listed wiki's graph, for its card in the directory on dexio.wiki
(Forrest, 2026-10-01: "in the public wikis page, we should show a visual preview of
the wiki somehow").

An SVG drawn on the server from what the listing reaches (the pages anyone may read
through that one share, and the links among them), in the graph view's own look:
a dot per page sized by its links, coloured by folder from the same palette, grey
for a page with no links, and a thin line per link. Laid out by a force simulation
(Fruchterman-Reingold, with a pull toward each folder's centre so folders gather as
they do in the app), seeded from the page paths so the same wiki always draws the
same picture. No labels: at card size they would not be readable.

Drawn once per version of the listing (its page count and last change) and kept in
memory; the address carries the version, so a cached copy is never stale.
"""
from __future__ import annotations

import hashlib
import math
from collections import OrderedDict

from . import db, shares

# graph.js PALETTES.light and THEME.dim / THEME.edge: the app's own colours.
PALETTE = ["#0077c8", "#c0392b", "#27ae60", "#8e44ad", "#d35400",
           "#16a085", "#7f8c8d", "#b7950b", "#2c3e50", "#a93226"]
DIM, EDGE, BG = "#aab1b8", "#c9d1d9", "#ffffff"
# A margin round the graph (Forrest, 2026-10-01: "i want to see more padding in the image
# preview"), wider at the sides, where the card's text does not frame it.
W, H, PAD_X, PAD_Y = 640, 360, 84, 54
MAX_NODES = 220            # the best-linked pages of a bigger wiki
ROUNDS = 90

_cache: OrderedDict[tuple, str] = OrderedDict()
CACHE_SIZE = 256


def _seed(text: str) -> float:
    return int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF


def _graph(conn, ws_id: int, kind: str, path: str) -> dict:
    from .copies import access_of
    return shares.restrict_graph(db.graph(conn, db.wiki_key(ws_id)), access_of(
        {"workspace_id": ws_id, "kind": kind, "path": path}))


def layout(nodes: list[dict], links: list[dict]) -> dict[str, tuple[float, float]]:
    """Positions in a unit square, by node id. Each folder has a spot on a circle
    (the top level in the middle) that its pages are pulled toward, so folders
    gather as they do in the app's By folder layout; everything is pulled to the
    middle, so pages without links ring the rest instead of flying off."""
    ids = [n["id"] for n in nodes]
    if not ids:
        return {}
    folder = {n["id"]: n.get("folder") or "" for n in nodes}
    sizes: dict[str, int] = {}
    for f in folder.values():
        sizes[f] = sizes.get(f, 0) + 1
    ring = sorted((f for f in sizes if f), key=lambda f: (-sizes[f], f))
    spot = {"": (0.5, 0.5)}
    for i, f in enumerate(ring):
        a = 2 * math.pi * i / max(1, len(ring)) - math.pi / 2
        spot[f] = (0.5 + 0.36 * math.cos(a), 0.5 + 0.36 * math.sin(a))
    pos = {}
    for i in ids:
        a, r = 2 * math.pi * _seed(i), 0.08 * _seed(i + "#r")
        fx, fy = spot[folder[i]]
        pos[i] = [fx + r * math.cos(a), fy + r * math.sin(a)]
    n = len(ids)
    k = 0.6 * math.sqrt(1.0 / n)
    edges = [(e["source"], e["target"]) for e in links
             if e["source"] in pos and e["target"] in pos and e["source"] != e["target"]]
    pull = 2.4 if len(ring) > 1 else 0.0
    temp = 0.12
    for _ in range(ROUNDS):
        disp = {i: [0.0, 0.0] for i in ids}
        for x in range(n):
            a = ids[x]
            ax, ay = pos[a]
            da = disp[a]
            for y in range(x + 1, n):
                b = ids[y]
                bx, by = pos[b]
                dx, dy = ax - bx, ay - by
                d2 = dx * dx + dy * dy or 1e-6
                f = k * k / d2                       # repulsion: k^2 / d along the unit vector
                da[0] += dx * f; da[1] += dy * f
                disp[b][0] -= dx * f; disp[b][1] -= dy * f
        for a, b in edges:
            dx, dy = pos[a][0] - pos[b][0], pos[a][1] - pos[b][1]
            d = math.sqrt(dx * dx + dy * dy) or 1e-6
            f = d / k                                # attraction: d^2 / k
            disp[a][0] -= dx * f; disp[a][1] -= dy * f
            disp[b][0] += dx * f; disp[b][1] += dy * f
        for i in ids:
            sx, sy = spot[folder[i]]
            disp[i][0] += (sx - pos[i][0]) * pull + (0.5 - pos[i][0]) * 1.2
            disp[i][1] += (sy - pos[i][1]) * pull + (0.5 - pos[i][1]) * 1.2
            dx, dy = disp[i]
            d = math.sqrt(dx * dx + dy * dy) or 1e-6
            step = min(d, temp)
            pos[i][0] = min(1.0, max(0.0, pos[i][0] + dx / d * step))
            pos[i][1] = min(1.0, max(0.0, pos[i][1] + dy / d * step))
        temp = max(0.004, temp * 0.95)
    return {i: (p[0], p[1]) for i, p in pos.items()}


def render(nodes: list[dict], links: list[dict]) -> str:
    """The SVG for a graph: nodes [{id, folder, degree}], links [{source, target}]."""
    if len(nodes) > MAX_NODES:
        keep = sorted(nodes, key=lambda n: (-(n.get("degree") or 0), n["id"]))[:MAX_NODES]
        ids = {n["id"] for n in keep}
        nodes = [n for n in nodes if n["id"] in ids]
        links = [e for e in links if e["source"] in ids and e["target"] in ids]
    linked = {e["source"] for e in links} | {e["target"] for e in links}
    pos = layout([n for n in nodes if n["id"] in linked], links)
    alone = [n["id"] for n in nodes if n["id"] not in linked]
    folders = sorted({n.get("folder") or "" for n in nodes})
    if pos:
        xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    else:
        x0, x1, y0, y1 = 0.3, 0.7, 0.3, 0.7
    # Pages with no links sit on an oval just outside the rest, as greys round the
    # edge, rather than wherever the simulation flung them.
    mx, my = (x0 + x1) / 2, (y0 + y1) / 2
    rx, ry = max(0.12, (x1 - x0) / 2 * 1.12), max(0.12, (y1 - y0) / 2 * 1.12)
    for j, i in enumerate(sorted(alone)):
        a = 2 * math.pi * j / max(1, len(alone)) + 0.3
        pos[i] = (mx + rx * math.cos(a), my + ry * math.sin(a))
    xs, ys = [p[0] for p in pos.values()], [p[1] for p in pos.values()]
    x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
    # Fill the card: each axis scaled to the frame, the stretch kept under 1.9x.
    sx = (W - 2 * PAD_X) / max(x1 - x0, 1e-6)
    sy = (H - 2 * PAD_Y) / max(y1 - y0, 1e-6)
    sx, sy = min(sx, sy * 1.9), min(sy, sx * 1.9)
    # A handful of pages sits in the middle rather than spread to the corners.
    if len(nodes) < 12:
        shrink = 0.45 + 0.55 * len(nodes) / 12
        sx, sy = sx * shrink, sy * shrink
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2

    def at(i: str) -> tuple[float, float]:
        x, y = pos[i]
        return round(W / 2 + (x - cx) * sx, 1), round(H / 2 + (y - cy) * sy, 1)

    small = len(nodes) > 80
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}"'
           f' height="{H}" role="img" aria-label="Graph of {len(nodes)} pages">',
           f'<rect width="{W}" height="{H}" fill="{BG}"/>',
           f'<g stroke="{EDGE}" stroke-width="{0.7 if small else 1}" stroke-opacity=".9">']
    for e in links:
        if e["source"] in pos and e["target"] in pos:
            (x1, y1), (x2, y2) = at(e["source"]), at(e["target"])
            out.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>')
    out.append('</g><g stroke="#ffffff" stroke-width="1">')
    for n in sorted(nodes, key=lambda n: n.get("degree") or 0):
        x, y = at(n["id"])
        deg = n.get("degree") or 0
        r = (2.2 if small else 3.2) + min(9.0, math.sqrt(deg) * (1.5 if small else 2.2))
        colour = DIM if not deg else PALETTE[folders.index(n.get("folder") or "")
                                             % len(PALETTE)]
        out.append(f'<circle cx="{x}" cy="{y}" r="{round(r, 1)}" fill="{colour}"/>')
    out.append("</g></svg>")
    return "".join(out)


def svg(conn, share: dict, version: str) -> str:
    """The picture for a listed share, drawn once per version."""
    return picture(conn, share["workspace_id"], share["kind"], share["path"], version)


def picture(conn, ws_id: int, kind: str, path: str, version: str) -> str:
    """The picture of what a wiki, folder or page reaches, drawn once per version:
    the directory's cards, and the Publish dialog before anything is published."""
    key = (int(ws_id), kind, path, version)
    if key in _cache:
        _cache.move_to_end(key)
        return _cache[key]
    g = _graph(conn, ws_id, kind, path)
    out = render(g["nodes"], g["links"])
    _cache[key] = out
    while len(_cache) > CACHE_SIZE:
        _cache.popitem(last=False)
    return out
