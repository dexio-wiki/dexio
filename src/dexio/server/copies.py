"""Make a copy: a wiki, folder or page listed in the directory on dexio.wiki,
copied into a workspace of one's own (Forrest, 2026-10-01: users opt in to list
what they share publicly, and Dexio's templates are listed the same way).

Only what its owner listed can be copied (shares.set_listed): making something
public lets people read it, and listing it is the separate yes to copying.

- Pages keep their paths, so a copied folder is the same folder in the copy and
  every link in it resolves as it did. A page the destination already has is
  left as it is and reported, never overwritten.
- Files come along when the copied pages show or link to them, or they sit in a
  copied folder (the files a guest could open, shares.visible_files), unless the
  destination already has a file at that path.
- Each copied page's first revision carries the person who made the copy and a
  note naming where it came from, so the copy's history starts with its source.
- Nothing is written unless all of it fits the destination's storage.
"""
from __future__ import annotations

from . import db, files, shares

MAX_PAGES = 5000


class CopyError(ValueError):
    """A reason a person can act on."""


def access_of(share: dict) -> shares.Access:
    """What a copy reaches: what anyone may read through this one share."""
    kind, path = share["kind"], share["path"]
    return shares.Access(share["workspace_id"], "public", whole=kind == "wiki",
                         folders=frozenset([path]) if kind == "folder" else frozenset(),
                         pages=frozenset([path]) if kind == "page" else frozenset())


def source_files(conn, share: dict) -> list[dict]:
    """The files that come with a copy: full rows, so their bytes can be read."""
    k = db.wiki_key(share["workspace_id"])
    seen = shares.visible_files(conn, k, access_of(share), files.list_files(conn, k))
    out = []
    for f in seen:
        row = files.get(conn, k, f["path"])
        if row:
            out.append(dict(row))
    return out


def destinations(conn, user_id: int) -> list[dict]:
    """The workspaces this person can copy into: every one they are a member of,
    with its page count and, if it cannot take changes now, why."""
    out = []
    for w in db.workspaces_for_user(conn, user_id):
        stats = db.wiki_stats(conn, db.ensure_wiki(conn, w["id"]))
        out.append({**w, "pages": int(stats.get("pages") or 0),
                    "blocked": db.read_only_reason(conn, w["id"])})
    return out


def copy(conn, share: dict, dest_ws: int, *, user: dict, source_url: str) -> dict:
    """Copy what `share` reaches into workspace `dest_ws`'s wiki. Returns
    {pages, skipped, files, files_skipped, first}: counts, and the first page
    copied (for where to land)."""
    if int(dest_ws) == int(share["workspace_id"]):
        raise CopyError("That is the workspace it is in already. Pick another.")
    why = db.read_only_reason(conn, dest_ws)
    if why:
        raise CopyError(why[0].upper() + why[1:] + ".")
    src = shares.reached(conn, share["workspace_id"], share["kind"], share["path"])
    if not src:
        raise CopyError("There is nothing in it to copy any more.")
    if len(src) > MAX_PAGES:
        raise CopyError(f"It has {len(src):,} pages, and a copy can take at most"
                        f" {MAX_PAGES:,}. Ask its owner to list a folder of it instead.")
    k = db.ensure_wiki(conn, dest_ws)
    have = set(db.page_paths(conn, k))
    pages = {r["path"]: r["text"] or "" for r in src if r["path"] not in have}
    offered = source_files(conn, share)
    attach = [f for f in offered if not files.get(conn, k, f["path"])]

    # Storage: each page is stored twice (the page, and its first revision).
    need = sum(2 * len(t) for t in pages.values()) + sum(int(f["size"] or 0) for f in attach)
    limit = files.storage_limit(conn, dest_ws)
    used = files.usage(conn, dest_ws)
    if limit is not None and used + need > limit:
        raise CopyError(f"It needs {files.human(need)} and that workspace has"
                        f" {files.human(max(0, limit - used))} of storage left. Pick another"
                        " workspace, or make room first.")

    info = shares.listing(conn, share) or {}
    title = info.get("title") or "a wiki on Dexio"
    note = f"Copied from {title}, {source_url}"[:500]
    if pages:
        db.apply_changes(conn, k, {p: db.make_page(p, t) for p, t in pages.items()},
                         source="copy", author=user["email"], op="write", note=note,
                         user_id=user["id"])
    copied_files = 0
    for f in attach:
        data = files.open_bytes(f)
        fh, size, sha = files.spool([data])
        try:
            files.save(conn, k, dest_ws, f["path"], fh, size, sha, f["content_type"] or "",
                       author=user["email"], user_id=user["id"], note=note)
            copied_files += 1
        finally:
            fh.close()
    return {"pages": len(pages), "skipped": len(src) - len(pages), "files": copied_files,
            "files_skipped": len(offered) - len(attach),
            "first": next(iter(sorted(pages)), None), "title": title}
