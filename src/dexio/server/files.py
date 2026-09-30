"""Files: images, PDFs, decks and anything else, stored beside a wiki's pages.

A file lives at a path in its wiki, like a page, but with its extension
(raw/deck.pdf, images/arch.png). Pages link to it with an ordinary markdown link
or image. The bytes go to object storage (S3 in the hosted service, a folder
otherwise); the database keeps one row per file with its size, type, hash, who
put it there, and the text pulled out of it (PDFs, Word, PowerPoint, Excel and
plain-text formats), so word search finds what is inside a deck.

Storage is pooled per workspace and set by plan (PLAN_STORAGE): Free 100 MB, Team
1 GB and Business 10 GB per member, Enterprise by agreement
(workspaces.storage_limit overrides any plan). Cut from 1 GB, 10 GB and 50 GB
on 2026-09-28 (Forrest): the heaviest workspace then used about 16 MB a member.
One file may be up to MAX_FILE_BYTES. It counts files, and pages with every
earlier version of them: no plan limits history by age (Forrest, 2026-09-27), so
history is paid for by the space it takes instead.

Downloads are never served from the app's own origin: S3 hands out a presigned
URL on its own domain, valid for minutes, so a file's content (an SVG with a
script, an HTML page) can never run with a Dexio session. The folder store used
in development serves through a signed app route instead.

Agents cannot send bytes through MCP tool arguments, so an agent uploads by
asking upload_file for a one-time URL and PUTting the file to it with its shell.
People upload from Settings; scripts PUT with a workspace token.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import mimetypes
import os
import re
import secrets
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from . import db

log = logging.getLogger("dexio.files")

GB = 1024 ** 3
MB = 1024 ** 2
MAX_FILE_BYTES = 100 * MB
# Pooled per workspace: (fixed bytes, bytes per member). None means no limit set.
PLAN_STORAGE: dict[str, tuple[int, int] | None] = {
    "free": (100 * MB, 0),
    "team": (0, 1 * GB),
    "business": (0, 10 * GB),
    "enterprise": None,
}
MAX_TEXT = 2 * MB               # extracted text kept for search
UPLOAD_TTL = 15 * 60            # one-time upload URLs
URL_TTL = 5 * 60                # download URLs
MAX_PDF_PAGES = 1000

SCHEMA = """
CREATE TABLE IF NOT EXISTS files (
  id TEXT PRIMARY KEY, project TEXT NOT NULL, workspace_id INTEGER NOT NULL,
  path TEXT NOT NULL, size INTEGER NOT NULL, content_type TEXT NOT NULL,
  sha256 TEXT NOT NULL, text TEXT, created_at REAL NOT NULL, updated_at REAL NOT NULL,
  author TEXT, agent TEXT, user_id INTEGER, note TEXT,
  UNIQUE (project, path)
);
CREATE INDEX IF NOT EXISTS files_workspace ON files(workspace_id);
CREATE TABLE IF NOT EXISTS uploads (
  token_hash TEXT PRIMARY KEY, project TEXT NOT NULL, workspace_id INTEGER NOT NULL,
  path TEXT NOT NULL, author TEXT, agent TEXT, user_id INTEGER, note TEXT,
  created_at REAL NOT NULL, expires_at REAL NOT NULL, used_at REAL
);
"""
MIGRATIONS = [("workspaces", "storage_limit", "INTEGER")]   # bytes; overrides the plan

# Served inline (shown in the browser); everything else downloads.
INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "application/pdf"}
IMAGES = {"image/png", "image/jpeg", "image/gif", "image/webp"}
TEXT_EXTS = {
    "txt", "csv", "tsv", "json", "jsonl", "yaml", "yml", "toml", "xml", "html", "htm", "css",
    "js", "mjs", "ts", "tsx", "jsx", "py", "rb", "go", "rs", "java", "kt", "swift", "c", "h",
    "cpp", "hpp", "cs", "php", "sh", "bash", "zsh", "sql", "ini", "cfg", "conf", "log", "rst",
    "tex", "ipynb", "svg", "markdown", "org", "adoc", "graphql", "proto", "tf", "hcl"}
_PATH_EXT = re.compile(r"\.([A-Za-z0-9]{1,10})$")


class FileError(ValueError):
    """A refusal the caller can show as it is (bad path, over quota, too big)."""


def init(conn) -> None:
    conn.executescript(SCHEMA)
    for table, column, decl in MIGRATIONS:
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        if column not in have:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    conn.commit()


def norm_path(path: str) -> str:
    """A file's path: relative, no empty or dot segments, and an extension other
    than .md (a .md is a page)."""
    p = str(path or "").strip().replace("\\", "/").strip("/")
    if not p or any(part in ("", ".", "..") for part in p.split("/")) or len(p) > 512:
        raise FileError(f"bad file path: {path!r}")
    m = _PATH_EXT.search(p.rsplit("/", 1)[-1])
    if not m or m.group(1).lower() == "md":
        raise FileError(f"a file path needs its extension, other than .md (a page): {path!r}")
    return p


def looks_like_file(path: str) -> bool:
    try:
        norm_path(path)
        return True
    except FileError:
        return False


# Types the slim server image's mimetypes table lacks (it has no /etc/mime.types):
# without these a deck arrived as application/octet-stream.
KNOWN_TYPES = {
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "ppt": "application/vnd.ms-powerpoint", "doc": "application/msword",
    "xls": "application/vnd.ms-excel", "key": "application/vnd.apple.keynote",
    "numbers": "application/vnd.apple.numbers", "pages": "application/vnd.apple.pages",
    "pdf": "application/pdf", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml", "heic": "image/heic",
    "avif": "image/avif", "csv": "text/csv", "tsv": "text/tab-separated-values",
    "txt": "text/plain", "json": "application/json", "jsonl": "application/jsonl",
    "yaml": "application/yaml", "yml": "application/yaml", "toml": "application/toml",
    "html": "text/html", "htm": "text/html", "xml": "application/xml", "zip": "application/zip",
    "mp4": "video/mp4", "mov": "video/quicktime", "webm": "video/webm", "mp3": "audio/mpeg",
    "wav": "audio/wav", "ipynb": "application/x-ipynb+json", "py": "text/x-python",
    "js": "text/javascript", "ts": "text/plain", "sh": "text/x-shellscript", "sql": "text/plain",
}


def content_type(path: str, given: str = "") -> str:
    ext = path.rsplit(".", 1)[-1].lower() if "." in path else ""
    if ext in KNOWN_TYPES:
        return KNOWN_TYPES[ext]
    guess = mimetypes.guess_type(path)[0]
    given = (given or "").split(";", 1)[0].strip().lower()
    if guess:
        return guess
    return given if re.fullmatch(r"[a-z0-9.+-]+/[a-z0-9.+-]+", given or "") else \
        "application/octet-stream"


# ---- storage backends ---------------------------------------------------------
class FolderStore:
    """Files on local disk. Development and tests; downloads go through a signed
    app route (see signed_token)."""

    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _file(self, key: str) -> Path:
        return self.root / key

    def put(self, key: str, src, size: int, ctype: str) -> None:
        dest = self._file(key)
        dest.parent.mkdir(parents=True, exist_ok=True)
        src.seek(0)
        with open(dest, "wb") as out:
            while chunk := src.read(1024 * 1024):
                out.write(chunk)

    def open(self, key: str):
        return open(self._file(key), "rb")

    def delete(self, key: str) -> None:
        try:
            self._file(key).unlink()
        except FileNotFoundError:
            pass

    def url(self, key: str, name: str, ctype: str, inline: bool) -> str | None:
        return None                                # the app signs its own route


class S3Store:
    """Files in an S3 bucket; downloads are presigned URLs on S3's own domain."""

    def __init__(self, bucket: str, region: str):
        import boto3  # server extra
        from botocore.config import Config
        self.bucket = bucket
        self.s3 = boto3.client("s3", region_name=region,
                               config=Config(signature_version="s3v4"))

    def put(self, key: str, src, size: int, ctype: str) -> None:
        src.seek(0)
        self.s3.upload_fileobj(src, self.bucket, key, ExtraArgs={"ContentType": ctype})

    def open(self, key: str):
        return self.s3.get_object(Bucket=self.bucket, Key=key)["Body"]

    def delete(self, key: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=key)

    def url(self, key: str, name: str, ctype: str, inline: bool) -> str:
        disposition = ("inline" if inline else "attachment") + \
            f"; filename*=UTF-8''{_quote(name)}"
        return self.s3.generate_presigned_url(
            "get_object", ExpiresIn=URL_TTL,
            Params={"Bucket": self.bucket, "Key": key, "ResponseContentType": ctype,
                    "ResponseContentDisposition": disposition})


def _quote(name: str) -> str:
    from urllib.parse import quote
    return quote(name, safe="")


_store = None
_store_lock = threading.Lock()


def store():
    """The configured backend: DEXIO_FILES_BUCKET, or the bucket named in the SSM
    parameter DEXIO_FILES_SSM (the hosted deploy), else a folder
    (DEXIO_FILES_DIR, default ./files next to the database)."""
    global _store
    with _store_lock:
        if _store is None:
            region = os.environ.get("DEXIO_AWS_REGION", "us-east-1")
            bucket = os.environ.get("DEXIO_FILES_BUCKET", "").strip()
            param = os.environ.get("DEXIO_FILES_SSM", "").strip()
            if not bucket and param:
                import boto3
                bucket = boto3.client("ssm", region_name=region).get_parameter(
                    Name=param)["Parameter"]["Value"]
            if bucket:
                _store = S3Store(bucket, region)
            else:
                _store = FolderStore(os.environ.get("DEXIO_FILES_DIR") or "files")
        return _store


def set_store(s) -> None:
    """Tests point the module at a store of their own."""
    global _store
    with _store_lock:
        _store = s


# ---- quota ----------------------------------------------------------------
def storage_limit(conn, workspace_id: int) -> int | None:
    ws = conn.execute("SELECT plan, storage_limit FROM workspaces WHERE id=?",
                      (workspace_id,)).fetchone()
    if not ws:
        return 0
    if ws["storage_limit"] is not None:
        return int(ws["storage_limit"])
    if not db.plans_apply():        # no Stripe key: a self-hosted copy has no plan limits
        return None
    rule = PLAN_STORAGE.get(ws["plan"] or "free", PLAN_STORAGE["free"])
    if rule is None:
        return None
    fixed, per_member = rule
    members = len(db.members(conn, workspace_id)) or 1
    return fixed + per_member * members


def file_usage(conn, workspace_id: int) -> int:
    return int(conn.execute("SELECT COALESCE(SUM(size), 0) FROM files WHERE workspace_id=?",
                            (workspace_id,)).fetchone()[0])


def page_usage(conn, workspace_id: int) -> int:
    """Pages and their history: the current text of every page, plus every stored
    revision as it is kept (a whole text, or a delta from the one before),
    deleted pages' included. Counted in characters, which are bytes for ASCII."""
    like = f"{int(workspace_id)}:%"
    current = conn.execute("SELECT COALESCE(SUM(LENGTH(text)), 0) FROM pages"
                           " WHERE project LIKE ?", (like,)).fetchone()[0]
    history = conn.execute("SELECT COALESCE(SUM(COALESCE(LENGTH(text), 0)"
                           " + COALESCE(LENGTH(delta), 0)), 0) FROM revisions"
                           " WHERE project LIKE ?", (like,)).fetchone()[0]
    return int(current or 0) + int(history or 0)


def usage(conn, workspace_id: int) -> int:
    """Everything that counts toward the workspace's storage limit."""
    return file_usage(conn, workspace_id) + page_usage(conn, workspace_id)


def human(n: int | None) -> str:
    if n is None:
        return "no limit"
    for unit, size in (("GB", GB), ("MB", MB), ("KB", 1024)):
        if n >= size:
            v = n / size
            return f"{v:.1f} {unit}".replace(".0 ", " ")
    return f"{n} bytes"


# ---- text for search ----------------------------------------------------------
def _xml_text(xml: str, tag: str, block: str) -> str:
    xml = re.sub(rf"</{block}>", "\n", xml)
    parts = re.findall(rf"<{tag}(?:\s[^>]*)?>([^<]*)</{tag}>", xml)
    import html
    return html.unescape(" ".join(parts).replace(" \n", "\n"))


def extract_text(path: str, ctype: str, src) -> str | None:
    """Plain text from a file, for search and for read_page; None when there is
    nothing to read (images, archives, unknown binaries) or it cannot be parsed."""
    ext = path.rsplit(".", 1)[-1].lower()
    src.seek(0)
    try:
        if ext in TEXT_EXTS or ctype.startswith("text/"):
            return src.read(MAX_TEXT).decode("utf-8", "replace")
        if ext == "pdf":
            from pypdf import PdfReader
            reader = PdfReader(src)
            out, size = [], 0
            for i, page in enumerate(reader.pages):
                if i >= MAX_PDF_PAGES or size > MAX_TEXT:
                    break
                t = page.extract_text() or ""
                out.append(f"--- page {i + 1} ---\n{t.strip()}\n")
                size += len(out[-1])
            return "".join(out)[:MAX_TEXT] or None
        if ext in ("docx", "pptx", "xlsx"):
            with zipfile.ZipFile(src) as z:
                names = z.namelist()
                if ext == "docx":
                    xml = z.read("word/document.xml").decode("utf-8", "replace")
                    text = _xml_text(xml, "w:t", "w:p")
                elif ext == "pptx":
                    slides = sorted((n for n in names if re.fullmatch(r"ppt/slides/slide\d+\.xml", n)),
                                    key=lambda n: int(re.findall(r"\d+", n)[-1]))
                    text = "".join(f"--- slide {i} ---\n"
                                   + _xml_text(z.read(n).decode("utf-8", "replace"), "a:t", "a:p")
                                   + "\n" for i, n in enumerate(slides, 1))
                else:
                    shared = ("xl/sharedStrings.xml" in names and
                              _xml_text(z.read("xl/sharedStrings.xml").decode("utf-8", "replace"),
                                        "t", "si")) or ""
                    text = shared
                return text[:MAX_TEXT] or None
    except Exception:  # noqa: BLE001 - a file we cannot parse is still stored
        return None
    return None


# ---- the files themselves -------------------------------------------------------
def get(conn, project: str, path: str):
    row = conn.execute("SELECT * FROM files WHERE project=? AND path=?", (project, path)).fetchone()
    return dict(row) if row else None


def list_files(conn, project: str, folder: str = "") -> list[dict]:
    folder = folder.strip("/")
    rows = conn.execute("SELECT path, size, content_type, updated_at, agent FROM files"
                        " WHERE project=? ORDER BY path", (project,)).fetchall()
    return [dict(r) for r in rows if not folder or r["path"].startswith(folder + "/")]


def workspace_files(conn, workspace_id: int, limit: int | None = None,
                    offset: int = 0) -> list[dict]:
    """The files in the workspace's wiki, by path; limit and offset take one page."""
    sql = ("SELECT path, size, content_type, updated_at FROM files"
           " WHERE workspace_id=? AND project=? ORDER BY path")
    args: tuple = (workspace_id, db.wiki_key(workspace_id))
    if limit is not None:
        sql += " LIMIT ? OFFSET ?"
        args += (int(limit), max(0, int(offset)))
    return [dict(r) for r in conn.execute(sql, args)]


def count_files(conn, workspace_id: int) -> int:
    return int(conn.execute("SELECT COUNT(*) FROM files WHERE workspace_id=? AND project=?",
                            (workspace_id, db.wiki_key(workspace_id))).fetchone()[0])


class Spool:
    """A file arriving in chunks: written to a temporary file (memory up to 8 MB,
    then disk) and hashed as it comes, refused past `limit` without reading on."""

    def __init__(self, limit: int = MAX_FILE_BYTES):
        self.file = tempfile.SpooledTemporaryFile(max_size=8 * MB)
        self.hash = hashlib.sha256()
        self.size = 0
        self.limit = limit

    def add(self, chunk: bytes) -> None:
        self.size += len(chunk)
        if self.size > self.limit:
            self.file.close()
            raise FileError(f"a file can be at most {human(self.limit)}")
        self.hash.update(chunk)
        self.file.write(chunk)

    @property
    def sha256(self) -> str:
        return self.hash.hexdigest()


def spool(chunks, limit: int = MAX_FILE_BYTES):
    """(file, size, sha256) for an iterable of byte chunks; see Spool."""
    s = Spool(limit)
    for chunk in chunks:
        s.add(chunk)
    return s.file, s.size, s.sha256


def save(conn, project: str, workspace_id: int, path: str, src, size: int, sha: str,
         ctype: str = "", *, author: str = "", agent: str | None = None,
         user_id: int | None = None, note: str | None = None) -> dict:
    """Store a spooled file at `path`, replacing any file there. Checks the
    workspace's storage first; the bytes go to the store before the row changes,
    and a replaced file's old bytes are removed after."""
    path = norm_path(path)
    ctype = content_type(path, ctype)
    if size > MAX_FILE_BYTES:
        raise FileError(f"a file can be at most {human(MAX_FILE_BYTES)}")
    old = get(conn, project, path)
    limit = storage_limit(conn, workspace_id)
    used = usage(conn, workspace_id) - (old["size"] if old else 0)
    if limit is not None and used + size > limit:
        raise FileError(f"not enough storage: this workspace uses {human(used)} of"
                        f" {human(limit)}, and the file is {human(size)}. Delete files or"
                        " move to a larger plan.")
    text = extract_text(path, ctype, src)
    fid = secrets.token_hex(16)
    key = f"ws/{workspace_id}/{fid}"
    store().put(key, src, size, ctype)
    now = time.time()
    with db.LOCK, conn:
        conn.execute("DELETE FROM files WHERE project=? AND path=?", (project, path))
        conn.execute(
            "INSERT INTO files (id, project, workspace_id, path, size, content_type, sha256, text,"
            " created_at, updated_at, author, agent, user_id, note)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (fid, project, workspace_id, path, size, ctype, sha, text,
             old["created_at"] if old else now, now, author, agent, user_id, note))
    if old:
        store().delete(f"ws/{old['workspace_id']}/{old['id']}")
    return {"path": path, "size": size, "content_type": ctype, "sha256": sha,
            "replaced": bool(old), "searchable": text is not None}


def delete(conn, project: str, path: str) -> bool:
    row = get(conn, project, path)
    if not row:
        return False
    with db.LOCK, conn:
        conn.execute("DELETE FROM files WHERE id=?", (row["id"],))
    store().delete(f"ws/{row['workspace_id']}/{row['id']}")
    return True


def delete_wiki(conn, workspace_id: int, name: str) -> int:
    """Delete a wiki (db.delete_wiki), then its files' stored bytes. Returns how
    many stored files were removed. One that cannot be is logged: its row is
    already gone, so nothing reaches it."""
    removed = 0
    for k in db.delete_wiki(conn, workspace_id, name):
        try:
            store().delete(k)
            removed += 1
        except Exception:  # noqa: BLE001
            log.exception("could not delete stored file %s", k)
    return removed


def move(conn, project: str, old: str, new: str) -> None:
    new = norm_path(new)
    if get(conn, project, new):
        raise FileError(f"{new!r} already exists; delete or move it first")
    with db.LOCK, conn:
        conn.execute("UPDATE files SET path=?, updated_at=? WHERE project=? AND path=?",
                     (new, time.time(), project, old))


def open_bytes(row) -> bytes:
    f = store().open(f"ws/{row['workspace_id']}/{row['id']}")
    try:
        return f.read()
    finally:
        f.close()


# ---- download URLs and one-time uploads -------------------------------------------
def download_url(conn, row, secret: bytes, base: str, download: bool = False) -> str:
    """A short-lived URL for a file's bytes, on another origin in the hosted
    service (S3), or the app's signed route with the folder store."""
    inline = row["content_type"] in INLINE and not download
    name = row["path"].rsplit("/", 1)[-1]
    url = store().url(f"ws/{row['workspace_id']}/{row['id']}", name, row["content_type"], inline)
    if url:
        return url
    return f"{base}/api/v1/files/raw/{signed_token(secret, row['id'], inline)}"


def signed_token(secret: bytes, fid: str, inline: bool) -> str:
    exp = int(time.time()) + URL_TTL
    msg = f"{fid}.{int(inline)}.{exp}"
    sig = hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{msg}.{sig}"


def check_signed(secret: bytes, token: str) -> tuple[str, bool] | None:
    try:
        fid, inline, exp, sig = token.split(".")
    except ValueError:
        return None
    msg = f"{fid}.{inline}.{exp}"
    good = hmac.new(secret, msg.encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, good) or int(exp) < time.time():
        return None
    return fid, inline == "1"


def new_upload(conn, project: str, workspace_id: int, path: str, *, author: str = "",
               agent: str | None = None, user_id: int | None = None,
               note: str | None = None) -> str:
    """A one-time upload token for `path`, valid UPLOAD_TTL seconds."""
    path = norm_path(path)
    token = "dxu_" + secrets.token_urlsafe(24)
    now = time.time()
    with db.LOCK, conn:
        conn.execute("DELETE FROM uploads WHERE expires_at < ?", (now - 86400,))
        conn.execute(
            "INSERT INTO uploads (token_hash, project, workspace_id, path, author, agent, user_id,"
            " note, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (db.hash_token(token), project, workspace_id, path, author, agent, user_id, note,
             now, now + UPLOAD_TTL))
    return token


def take_upload(conn, token: str):
    """The upload a token grants, marked used so it works once; None if unknown,
    used or expired."""
    with db.LOCK, conn:
        row = conn.execute("SELECT * FROM uploads WHERE token_hash=?",
                           (db.hash_token(token),)).fetchone()
        if not row or row["used_at"] or row["expires_at"] < time.time():
            return None
        conn.execute("UPDATE uploads SET used_at=? WHERE token_hash=?",
                     (time.time(), row["token_hash"]))
    return dict(row)


def image_bytes_ok(row) -> bool:
    """Small enough to hand a model as an image (about 5 MB once base64-encoded)."""
    return row["content_type"] in IMAGES and row["size"] <= int(3.7 * MB)


def search_items(conn, project: str):
    """Files as search candidates: path and extracted text."""
    from types import SimpleNamespace
    return [SimpleNamespace(path=r["path"], title=r["path"].rsplit("/", 1)[-1],
                            text=r["text"] or "")
            for r in conn.execute("SELECT path, text FROM files WHERE project=?", (project,))]

