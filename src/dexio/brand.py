"""The Dexio mark and wordmark, inlined so a page needs no extra request to show them.

Copied from dexio-www `brand/marks.json`, which `brand/build.py` generates. The wordmark
(since 2026-09-26) is "Dexio" in Libertinus Serif Semibold, the maintained fork of Linux
Libertine, outlined, in the text colour with no accent. The mark is a small graph: an accent
hub, four straight spokes to nodes of uneven size, the spokes tapering from the wordmark's
thick stroke to its hairline; spokes and nodes take the text colour, the hub `--accent`, so both follow the page's mode and colour theme.
"""
from __future__ import annotations

VIEWBOX = "-4 -676 2500 713"
PATH = "M24 0H338C487 0 681 -63 681 -309C681 -494 523 -646 320 -646H24L22 -644V-621C22 -616 26 -613 30 -613H44C84 -613 99 -601 99 -569V-77C99 -45 84 -33 44 -33H30C26 -33 22 -30 22 -24V-2ZM221 -75V-571C221 -603 247 -606 283 -606C480 -606 549 -435 549 -284C549 -88 436 -40 308 -40C230 -40 221 -46 221 -75ZM1120 -109C1083 -64 1046 -46 1008 -46C930 -46 872 -121 872 -219V-245H1115C1139 -245 1154 -252 1154 -266C1154 -351 1119 -444 972 -444C867 -444 757 -351 757 -198C757 -88 817 10 969 10C1038 10 1108 -13 1145 -88ZM877 -285C891 -387 950 -402 971 -402C998 -402 1039 -376 1039 -296C1039 -290 1039 -287 1037 -285ZM1360 -73 1402 -133C1424 -164 1425 -164 1441 -136L1483 -72C1503 -42 1495 -37 1460 -32C1458 -32 1452 -30 1452 -24V0L1456 2C1456 2 1530 0 1578 0C1617 0 1679 2 1679 2L1682 0V-25C1682 -29 1677 -31 1672 -32C1635 -38 1611 -51 1579 -99L1491 -230C1485 -239 1486 -241 1489 -245L1578 -353C1605 -387 1637 -399 1668 -402C1674 -403 1679 -406 1679 -411V-434L1677 -436C1677 -436 1636 -434 1609 -434C1563 -434 1492 -436 1492 -436L1489 -433V-409C1489 -406 1491 -402 1496 -402C1532 -399 1534 -388 1513 -359L1463 -287C1457 -280 1456 -279 1448 -290L1400 -360C1377 -393 1384 -398 1422 -402C1425 -402 1430 -403 1430 -410V-434L1427 -436C1427 -436 1351 -434 1302 -434C1263 -434 1201 -436 1201 -436L1198 -434V-409C1198 -404 1203 -402 1208 -402C1243 -402 1271 -375 1299 -335L1388 -208C1392 -203 1393 -202 1385 -193L1293 -79C1267 -48 1233 -36 1203 -32C1197 -31 1192 -27 1192 -22V0L1194 2C1194 2 1242 0 1272 0C1315 0 1381 2 1381 2L1385 0V-24C1385 -28 1382 -31 1377 -32C1359 -36 1337 -40 1360 -73ZM1909 -321C1909 -371 1912 -444 1912 -444C1912 -446 1907 -447 1905 -447C1874 -437 1804 -427 1734 -417L1736 -387L1777 -383C1791 -382 1799 -371 1799 -321V-77C1799 -49 1783 -39 1752 -34L1740 -32C1736 -31 1733 -29 1733 -23V0L1735 2C1735 2 1816 0 1852 0C1891 0 1972 2 1972 2L1974 0V-23C1974 -29 1970 -31 1966 -32L1956 -34C1926 -40 1909 -49 1909 -77ZM1793 -590C1793 -557 1820 -530 1853 -530C1886 -530 1913 -557 1913 -590C1913 -623 1886 -650 1853 -650C1820 -650 1793 -623 1793 -590ZM2018 -207C2018 -104 2096 10 2243 10C2398 10 2469 -115 2469 -216C2469 -321 2407 -444 2244 -444C2099 -444 2018 -344 2018 -207ZM2234 -403C2302 -403 2348 -327 2348 -184C2348 -57 2295 -33 2259 -33C2177 -33 2140 -155 2140 -229C2140 -314 2170 -403 2234 -403Z"

_W, _H = (float(v) for v in VIEWBOX.split()[2:])


def wordmark(height: float = 26, cls: str = "wordmark") -> str:
    """The wordmark as inline SVG, `height` px tall, width from its own proportions."""
    width = round(height * _W / _H, 1)
    return (f'<svg class="{cls}" viewBox="{VIEWBOX}" width="{width:g}" height="{height:g}" '
            f'role="img" aria-label="Dexio"><path d="{PATH}" fill="currentColor"/></svg>')


MARK_VIEWBOX = "-573 -585 1173 1034"  # `tight`: no tile margin beside the wordmark
MARK_SPOKES = "M79.9 0.0L20.4 -475.2L-20.4 -475.2L-79.9 0.0ZM29.1 74.4L498.1 -173.2L483.3 -211.2L-29.1 -74.4ZM-52.3 60.3L392.4 367.4L419.0 336.6L52.3 -60.3ZM-33.6 -72.5L-462.2 191.8L-445.0 228.8L33.6 72.5Z"
MARK_LEAVES = '<circle cx="0.0" cy="-475.2" r="87.6"/><circle cx="490.7" cy="-192.2" r="87.6"/><circle cx="405.7" cy="352.0" r="74.5"/><circle cx="-453.6" cy="210.3" r="96.4"/>'
MARK_HUB = (0.0, 0.0, 166.5)


def mark(height: int = 26, cls: str = "mark") -> str:
    """The graph mark as inline SVG, `height` px tall. Decorative: the wordmark names it."""
    cx, cy, r = MARK_HUB
    vw, vh = (float(v) for v in MARK_VIEWBOX.split()[2:])
    width = round(height * vw / vh, 1)
    return (f'<svg class="{cls}" viewBox="{MARK_VIEWBOX}" width="{width:g}" height="{height}" '
            f'aria-hidden="true"><path d="{MARK_SPOKES}" fill="currentColor"/>'
            f'<g fill="currentColor">{MARK_LEAVES}</g>'
            f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="var(--accent)"/></svg>')


def logo(height: int = 26) -> str:
    """Mark then wordmark, for a flex row. `height` sets the mark at 1.15 times it; the serif
    wordmark's box is two thirds of the mark, so the letters sit a little under its height."""
    m = round(height * 1.15)
    return mark(m) + wordmark(round(m * 0.67, 1))
